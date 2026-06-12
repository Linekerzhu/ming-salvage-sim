"""文书化黑箱与密查体系（S3/S9）+ 信息集隔离（S8）。L7。

支柱 P4「一切数据皆证言」：
- 官员属性不出真值，只出**印象评语 + 置信度**（perceive）：噪声随召对次数
  与密查收敛——「识人」由此成为玩法而非读面板。
- 军队兵额账实分离：armies.manpower 是真值（驱动推演/战斗），兵部呈报的
  **账面兵额**（含空饷虚冒）走 report_ledger；玩家界面看账面，盘库/密查才见真。
- 密查两线：厂卫（快而准，累积「厂卫横行」政治成本，且可能被反侦喂假）、
  科道（慢，带派系滤镜）。产出密揭入御案。

支柱 P6 信息集隔离：build_info_scope_brief 给每个大臣 agent 划定其身份应知
边界——本衙门账面数字、本派系内情、与己相关的承诺账；衙外之事只知邸报与风闻。
"""

from __future__ import annotations

import json
import random
from typing import Dict, List, Optional, Tuple

from ming_sim.db import GameDB
from ming_sim.models import Character, GameState
from ming_sim.timeflow import LEVEL_RED, LEVEL_YELLOW, schedule
from ming_sim.upgrade_schema import KV_CURRENT_DAY, kv_int, kv_set_int

KV_CHANGWEI_HEAT = "upgrade.changwei_heat"      # 厂卫动用累积
KV_PERCEPTION_BOOST = "upgrade.perception_boost"  # {name: 2} 密查后该人观感锐度

# ── 印象评语（属性真值 → 模糊证言）──────────────────────────────────────────

_ATTR_LABELS = {
    "loyalty": ["疑有贰心", "心迹难测", "恭顺常臣", "似可信重", "忠悃可托"],
    "ability": ["恐难胜任", "才具平平", "中材可用", "能事之臣", "干才卓异"],
    "integrity": ["贪声在外", "颇通馈遗", "未闻劣迹", "操守可观", "清慎著闻"],
    "courage": ["遇事推诿", "畏葸自全", "循例任事", "遇难有担当", "敢任天下事"],
}
_CONF_LABELS = ["仅凭风闻", "略有所闻", "颇有把握", "知之甚深"]


def _familiarity(db: GameDB, name: str) -> int:
    """召对熟悉度：与该臣的对话条数（封顶 40）。"""
    row = db.conn.execute(
        "SELECT COUNT(*) FROM chat_messages WHERE minister_name=?", (name,)
    ).fetchone()
    return min(40, int(row[0] or 0))


def _intel_boost(db: GameDB, name: str) -> int:
    try:
        data = json.loads(db.kv_get(KV_PERCEPTION_BOOST) or "{}")
        return int(data.get(name) or 0)
    except ValueError:
        return 0


def _grant_intel_boost(db: GameDB, name: str, level: int = 2) -> None:
    try:
        data = json.loads(db.kv_get(KV_PERCEPTION_BOOST) or "{}")
    except ValueError:
        data = {}
    data[name] = max(int(data.get(name) or 0), level)
    db.kv_set(KV_PERCEPTION_BOOST, json.dumps(data, ensure_ascii=False))


def perceive(db: GameDB, name: str, attr: str, true_value: int,
             *, _fam: Optional[int] = None, _boost: Optional[int] = None) -> Tuple[int, str, str]:
    """(感知值, 评语, 置信度)。噪声由 (name,attr) 定种 → 同一人稳定地被看错，
    直到熟悉度/密查把噪声压下来——这正是「初见走眼、久识方真」。
    _fam/_boost 供批量调用方预取（省查询）。"""
    fam = _familiarity(db, name) if _fam is None else _fam
    boost = _intel_boost(db, name) if _boost is None else _boost
    # 噪声半径：生人 ±18 → 熟识 ±6 → 密查后 ±2
    radius = max(2, 18 - fam // 4 - boost * 6)
    rng = random.Random(f"{name}:{attr}")
    offset = rng.randint(-radius, radius)
    perceived = max(1, min(99, int(true_value) + offset))
    labels = _ATTR_LABELS.get(attr) or _ATTR_LABELS["ability"]
    label = labels[min(4, perceived // 20)]
    conf_idx = 0 if radius >= 14 else 1 if radius >= 9 else 2 if radius >= 4 else 3
    return perceived, label, _CONF_LABELS[conf_idx]


def character_evaluations(db: GameDB, character: Character) -> List[Dict[str, object]]:
    """人物详情页的印象档案（替代属性真值的玩家视图）。"""
    out = []
    fam = _familiarity(db, character.name)
    boost = _intel_boost(db, character.name)
    for attr, true_value, basis in (
        ("loyalty", character.loyalty, "召对观感与众臣议论"),
        ("ability", character.ability, "吏部考语与办差实绩"),
        ("integrity", character.integrity, "科道访单与风闻"),
        ("courage", character.courage, "临事进退之迹"),
    ):
        perceived, label, conf = perceive(db, character.name, attr, int(true_value),
                                          _fam=fam, _boost=boost)
        out.append({"attr": attr, "perceived": perceived, "label": label,
                    "confidence": conf, "basis": basis})
    # NPC 数据基座风闻：擅=朝野共知即刻可见；痼/绝艺须熟识（召对≥20）或密查后方显——
    # 「初见知其长，久识方知其病」。
    try:
        from ming_sim.foundation import evaluation_rumors
        for rumor in evaluation_rumors(character.name, familiarity=fam, intel_boost=boost):
            out.append({"attr": "trait", "perceived": 0, "label": rumor["text"],
                        "confidence": rumor["kind"], "basis": "国朝人事档案风闻"})
    except Exception:
        pass
    return out


# ── 军队兵额账实分离（空饷虚冒）─────────────────────────────────────────────

def seed_army_divergences(db: GameDB, state: GameState, day: int) -> int:
    """开局/激活时为明军生成「兵部账面兵额」（虚冒），幂等。
    armies.manpower 为实在兵（驱动推演）；账面=实在×虚冒系数（京营最甚）。"""
    existing = db.conn.execute(
        "SELECT COUNT(*) FROM report_ledger WHERE entity_kind='army' AND field='manpower'"
    ).fetchone()
    if int(existing[0] or 0) > 0:
        return 0
    inflations = {"jingying": 1.65, "guanning": 1.12, "xuan_da": 1.30, "jizhen": 1.25}
    n = 0
    for row in db.conn.execute(
        "SELECT id, name, manpower, commander FROM armies WHERE owner_power='ming'"
    ).fetchall():
        rng = random.Random(f"divergence:{row['id']}")
        factor = inflations.get(str(row["id"]), 1.05 + rng.random() * 0.20)
        actual = int(row["manpower"])
        reported = round(actual * factor)
        if reported <= actual:
            continue
        db.conn.execute(
            """INSERT INTO report_ledger
               (entity_kind, entity_id, field, reported_value, actual_value,
                author_org, author_character, reported_day, note)
               VALUES ('army', ?, 'manpower', ?, ?, '兵部', ?, ?, ?)""",
            (str(row["id"]), float(reported), float(actual),
             str(row["commander"] or ""), int(day),
             f"{row['name']}兵额（兵部武选司造册）"),
        )
        n += 1
    db.conn.commit()
    return n


def reported_value(db: GameDB, entity_kind: str, entity_id: str, field: str,
                   fallback: float) -> Tuple[float, bool]:
    """(账面值, 是否已被揭穿)。无账面记录返回 (fallback, False)。"""
    row = db.conn.execute(
        "SELECT reported_value, actual_value, exposed FROM report_ledger "
        "WHERE entity_kind=? AND entity_id=? AND field=? ORDER BY id DESC LIMIT 1",
        (entity_kind, entity_id, field),
    ).fetchone()
    if row is None:
        return fallback, False
    if int(row["exposed"]):
        return float(row["actual_value"]), True
    return float(row["reported_value"]), False


def army_reported_overlay(db: GameDB, armies: List[Dict[str, object]]) -> List[Dict[str, object]]:
    """玩家口径：明军 manpower 换成兵部账面值（已揭穿的显真值并标记）。原地修改并返回。"""
    for item in armies:
        aid = str(item.get("id") or "")
        if not aid or str(item.get("owner_power") or "") != "ming":
            continue
        shown, exposed = reported_value(db, "army", aid, "manpower",
                                        float(item.get("manpower") or 0))
        item["manpower"] = int(shown)
        item["manpower_source"] = "盘验核实" if exposed else "兵部造册"
    return armies


def ledger_contradictions(db: GameDB) -> List[Dict[str, object]]:
    """文书互证面板：未揭穿的账实分叉条目（只给「有疑点」线索，不给真值）。"""
    rows = db.conn.execute(
        "SELECT * FROM report_ledger WHERE exposed=0 ORDER BY reported_day DESC LIMIT 40"
    ).fetchall()
    out = []
    for row in rows:
        out.append({
            "id": int(row["id"]),
            "entity_kind": str(row["entity_kind"]), "entity_id": str(row["entity_id"]),
            "field": str(row["field"]),
            "reported_value": float(row["reported_value"]),
            "author_org": str(row["author_org"]), "author": str(row["author_character"]),
            "reported_day": int(row["reported_day"]),
            "note": str(row["note"]),
        })
    return out


# ── 密查两线（S9）────────────────────────────────────────────────────────────

_LINES = {
    "changwei": {"label": "厂卫密查", "days": (6, 12), "accuracy": 0.85, "cost_note": "累积「厂卫横行」民怨物议"},
    "kedao": {"label": "科道巡按", "days": (18, 32), "accuracy": 0.62, "cost_note": "带派系滤镜"},
}


def start_investigation(db: GameDB, state: GameState, *, line: str,
                        target_kind: str, target_id: str, day: int) -> Dict[str, object]:
    """target_kind: army(盘验兵额)|character(访查官员)|directive(核查办差实情)。"""
    spec = _LINES.get(line)
    if spec is None:
        return {"ok": False, "message": f"未知密查线：{line}"}
    rng = random.Random(day * 524287 + hash(target_id) % 9973)
    due = day + rng.randint(*spec["days"])
    if line == "changwei":
        heat = kv_int(db, KV_CHANGWEI_HEAT, 0) + 1
        kv_set_int(db, KV_CHANGWEI_HEAT, heat)
        if heat >= 3:
            _spawn_changwei_issue(db, state)
            kv_set_int(db, KV_CHANGWEI_HEAT, 0)
    schedule(db, "intel_arrival", due,
             {"title": f"{spec['label']}回报将至", "line": line,
              "target_kind": target_kind, "target_id": target_id,
              "ref_kind": "intel", "ref_id": target_id},
             requires_llm=False, auto_pause=1, created_day=day)
    db.record_log(state, f"【密查】遣{spec['label']}查{target_kind}:{target_id}（预计{due - day}日后回报）")
    return {"ok": True, "message": f"{spec['label']}已出，约{due - day}日后密揭回报。（{spec['cost_note']}）",
            "due_day": due}


def _spawn_changwei_issue(db: GameDB, state: GameState) -> None:
    from ming_sim.issues import event_to_issue
    from ming_sim.models import Event
    ev = Event(
        id="veil_changwei_overreach", title="厂卫横行，物议沸腾", kind="党争",
        summary="缇骑频出，访缉无度，士民侧目，言官交章论劾东厂锦衣卫扰民害政。",
        urgency=4, severity=3, credibility=5, interests=["党争"], audiences=["都察院"],
        trigger_gate={"veil": "heat"}, event_type="situation",
        bar_value=35, bar_good_meaning="厂卫收敛，朝野安帖", bar_bad_meaning="特务政治失控",
        issue_inertia=-3,
        ongoing_effects={"metrics": {"民心": -1}},
        effect_on_fail={"metric_delta": {"民心": -6, "皇威": -4}},
    )
    event_to_issue(db, state, ev)


def deliver_intel(db: GameDB, state: GameState, payload: Dict[str, object], day: int) -> Dict[str, object]:
    """intel_arrival 节点结算：揭账/识人/喂假。产出密揭入御案。"""
    from ming_sim.memorials import create_memorial
    line = str(payload.get("line") or "changwei")
    spec = _LINES.get(line) or _LINES["changwei"]
    target_kind = str(payload.get("target_kind") or "")
    target_id = str(payload.get("target_id") or "")
    rng = random.Random(day * 715827883 + hash(f"{line}:{target_id}") % 65537)
    accurate = rng.random() < float(spec["accuracy"])
    author = "东厂" if line == "changwei" else "巡按御史"
    text, summary = "", ""

    if target_kind == "army":
        row = db.conn.execute(
            "SELECT id, reported_value, actual_value FROM report_ledger "
            "WHERE entity_kind='army' AND entity_id=? AND field='manpower' AND exposed=0 "
            "ORDER BY id DESC LIMIT 1", (target_id,)).fetchone()
        aname_row = db.conn.execute("SELECT name FROM armies WHERE id=?", (target_id,)).fetchone()
        aname = str(aname_row["name"]) if aname_row else target_id
        if row is None:
            summary = f"盘验{aname}兵额"
            text = f"奉旨盘验{aname}，按册点视，兵额与造册相符，无虚冒情弊。"
        elif accurate:
            db.conn.execute(
                "UPDATE report_ledger SET exposed=1, exposed_day=? WHERE id=?",
                (day, int(row["id"])))
            gap = int(row["reported_value"]) - int(row["actual_value"])
            summary = f"盘验{aname}：查出空额{gap}"
            text = (f"奉旨密验{aname}。册载兵{int(row['reported_value'])}，臣按伍清点，"
                    f"实在兵仅{int(row['actual_value'])}，空冒{gap}之饷岁入将弁私囊。册籍俱在，伏候圣裁。")
        else:
            summary = f"盘验{aname}：未见虚冒"
            text = (f"奉旨盘验{aname}，所至营伍俱齐——惟臣抵营之先，行迹似已为将弁所侦知，"
                    f"点视当日人数齐整，前后殊不类，臣不敢必其无弊。")
    elif target_kind == "character":
        crow = db.conn.execute(
            "SELECT name, office, loyalty, ability, integrity FROM characters WHERE name=?",
            (target_id,)).fetchone()
        if crow is None:
            summary, text = "访查无果", "所访之人查无实据。"
        elif accurate:
            _grant_intel_boost(db, target_id, level=2)
            loy, ab, integ = int(crow["loyalty"]), int(crow["ability"]), int(crow["integrity"])
            summary = f"访查{target_id}有得"
            text = (f"密访{str(crow['office'])}{target_id}：其人居官之迹、交游之类、馈遗之谓，臣俱察得。"
                    f"忠悃{'可恃' if loy >= 60 else '难恃' if loy <= 40 else '中平'}，"
                    f"才具{'堪大用' if ab >= 70 else '平平' if ab >= 45 else '不堪重寄'}，"
                    f"操守{'清慎' if integ >= 65 else '颇有可议' if integ <= 45 else '未闻显劣'}。")
            if line == "kedao":
                text += "（科道访单，所言或带门户之见，上宜参酌。）"
        else:
            # 反侦/滤镜：喂一份反向印象（不降反升噪声方向的危害靠玩家自鉴）
            summary = f"访查{target_id}"
            text = (f"访查{target_id}，所闻皆称其贤——访迹似为其同党所觉，所遇之人言辞如出一辙，"
                    f"臣谨录所闻，不敢遽信。")
    else:  # directive
        row = db.conn.execute(
            "SELECT id, reported_value, actual_value, note FROM report_ledger "
            "WHERE entity_kind='directive' AND entity_id=? AND exposed=0 ORDER BY id DESC LIMIT 1",
            (target_id,)).fetchone()
        if row is None:
            summary, text = "核查办差", "所核旨意，办理情形与奏报相符。"
        elif accurate:
            db.conn.execute("UPDATE report_ledger SET exposed=1, exposed_day=? WHERE id=?",
                            (day, int(row["id"])))
            summary = "核查办差：奏报与实情不符"
            text = (f"密核前旨办理实情：奏报称十成办竣，实地查勘仅及{int(row['actual_value'])}成，"
                    f"余皆文饰。{str(row['note'])}。经手人等，伏候究治。")
        else:
            summary, text = "核查办差", "所核旨意，文卷俱合，未见情弊。"

    mid = create_memorial(db, None, day=day, author_name=author, org=author,
                          kind="密揭", urgency=2, summary=f"【{spec['label']}】{summary}",
                          full_text=text, ref_kind="intel", ref_id=target_id)
    return {"memorial_id": mid, "accurate": accurate, "summary": summary}


# ── 信息集隔离（S8 架构铁律）────────────────────────────────────────────────

_OFFICE_ENTITLEMENTS = {
    "户部": "国库出入、各省解额与欠额、月度收支细目（皆户部账面口径）",
    "兵部": "各军册载兵额、欠饷月数、将弁履历（兵部武选司口径，含造册水分自己心知）",
    "吏部": "百官履历考语、缺额与候补名单",
    "都察院": "科道访单、纠劾案卷、地方官风评",
    "工部": "工程钱粮、物料账目",
    "礼部": "典礼仪注、藩属往来文书",
    "内阁": "各部题奏要目、票拟底簿",
    "司礼监": "批红底簿、内库出入、宫闱动静",
    "锦衣卫": "缉事访单、诏狱案卷",
}


def build_info_scope_brief(db: GameDB, character: Character) -> str:
    """每个大臣 agent 的信息边界声明：身份应知 + 明确不知。欺骗与信息差由此涌现。"""
    office = character.office or ""
    entitled = []
    for kw, desc in _OFFICE_ENTITLEMENTS.items():
        if kw in office or kw in (character.office_type or ""):
            entitled.append(f"职掌所及：{desc}。")
    faction = (character.faction or "").strip()
    if faction and faction not in ("无", ""):
        entitled.append(f"你是{faction}中人，知道本派系的私下风向与同党处境。")
    lines = [
        "【信息边界（必须遵守的扮演铁律）】",
        f"你只知道你身份应知之事：{''.join(entitled) if entitled else '本职文书所及之事。'}",
        "此外你只知道公开邸报所载与道听途说的风闻。以下事项你一概不知，不得在言谈中表现出知情：",
        "其他衙门的内部账目细数；皇帝与他人的私下召对内容；不经你手的密令；",
        "任何账册数字的「真实」水分（除非那本就是你衙门造的册——自家册里的虚冒你心里有数，但绝不会主动对皇帝吐实）。",
        "皇帝若问及你不应知之事，按你的人格选择：据实称不知、推诿、或凭风闻揣测——但不得开天眼。",
    ]
    return "\n".join(lines)
