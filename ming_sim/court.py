"""活的宫廷（CK3 化 P1）：把"等皇帝调度的脚本演员"变成"各怀私心、彼此有恩怨的活人"。

三件事：
1. **双向好感网络**（relationships 表）：从 npc_network.json 的 relations（同门/同乡/姻亲/旧怨/政敌…）
   numericize 成 -100..100 的好感分，叠加同党/政敌的派系基线。原本只是给 LLM 看的背景文字，
   现在落库、可查询、随事件升降。
2. **个人私心**（npc_agendas 表）：每个在朝官员一条暗中野心（进取拜相/护党/复仇/自肥/自保/拥兵自重），
   由职掌+派系+特质规则化推导，驱动其自主行为。
3. **自主出招**（court_moves_tick）：每旬，怨念最深者弹劾政敌、野心最炽者保举党羽——
   点名真实的政敌/党羽与恩怨由来，并真实改变好感网络。世界因此自己运转，皇帝在其中周旋。
"""

from __future__ import annotations

import json
import random
from typing import Dict, List, Optional, Tuple

from ming_sim.db import GameDB
from ming_sim.models import GameState

# ── 关系类型 → 好感基线 ───────────────────────────────────────────────────────
# npc_network.json relations.type 的常见取值映射到初始好感。正=亲，负=仇。
RELATION_VALENCE: Dict[str, int] = {
    "姻亲": 38, "师生": 34, "同门": 30, "门生": 32, "恩主": 34, "故旧": 26,
    "旧交": 26, "同年": 24, "同乡": 22, "盟友": 36, "举主": 30, "知己": 40,
    "政见相合": 24, "提携": 28,
    "政敌": -42, "夙仇": -50, "宿敌": -50, "旧怨": -26, "党争": -32,
    "政见相左": -24, "倾轧": -38, "嫌隙": -22, "竞争": -16,
}
# 派系基线：同党相亲、阉党↔东林清流势不两立。
_OPPOSED_FACTIONS = {("阉党", "东林"), ("阉党", "清流"), ("阉党", "东林党")}

_AMBIGUOUS_KEYWORDS = {  # type 文案里含这些词时归类
    "亲": 18, "睦": 16, "善": 16, "厚": 18,
    "怨": -22, "仇": -34, "敌": -34, "隙": -20, "倾": -28,
}


def _strip(name: str) -> str:
    """去掉 obsidian 的 [[ ]] 包裹与空白。"""
    return str(name or "").strip().strip("[]").strip()


def _valence(rel_type: str, note: str = "") -> int:
    t = str(rel_type or "").strip()
    if t in RELATION_VALENCE:
        return RELATION_VALENCE[t]
    blob = t + str(note or "")
    score = 0
    for kw, v in _AMBIGUOUS_KEYWORDS.items():
        if kw in blob:
            score = v
            break
    return score


def _faction_baseline(fa: str, fb: str) -> int:
    fa, fb = str(fa or ""), str(fb or "")
    if not fa or not fb or "无" in (fa, fb) or "中立" in (fa, fb):
        return 0
    if fa == fb:
        return 14
    pair = (fa, fb)
    rpair = (fb, fa)
    for opp in _OPPOSED_FACTIONS:
        if pair == opp or rpair == opp:
            return -22
    return -6  # 不同党，天然略有戒心


def _clamp(v: int) -> int:
    return max(-100, min(100, int(v)))


# ── 好感网络读写 ──────────────────────────────────────────────────────────────

def get_opinion(db: GameDB, a: str, b: str) -> int:
    row = db.conn.execute(
        "SELECT opinion FROM relationships WHERE a_name=? AND b_name=?", (a, b)
    ).fetchone()
    return int(row["opinion"]) if row else 0


def _set_opinion(db: GameDB, a: str, b: str, opinion: int, basis: str, day: int) -> None:
    db.conn.execute(
        "INSERT INTO relationships (a_name, b_name, opinion, basis, updated_day) VALUES (?,?,?,?,?) "
        "ON CONFLICT(a_name, b_name) DO UPDATE SET opinion=excluded.opinion, "
        "basis=CASE WHEN excluded.basis!='' THEN excluded.basis ELSE relationships.basis END, "
        "updated_day=excluded.updated_day",
        (a, b, _clamp(opinion), basis, int(day)),
    )


def adjust_opinion(db: GameDB, a: str, b: str, delta: int, basis: str = "", *, day: int = 0,
                   reciprocal: bool = True) -> int:
    """a 对 b 的好感增减 delta；reciprocal=True 时对向以七折同步（恩怨多半相互）。"""
    cur = get_opinion(db, a, b)
    new = _clamp(cur + int(delta))
    _set_opinion(db, a, b, new, basis, day)
    if reciprocal:
        back = get_opinion(db, b, a)
        _set_opinion(db, b, a, _clamp(back + int(delta * 0.7)), basis, day)
    db.conn.commit()
    return new


def _relation_strength_label(opinion: int) -> str:
    value = abs(int(opinion or 0))
    if int(opinion or 0) >= 0:
        if value >= 80:
            return "死党"
        if value >= 60:
            return "深援"
        if value >= 40:
            return "可借力"
        return "浅交"
    if value >= 80:
        return "死敌"
    if value >= 60:
        return "深怨"
    if value >= 40:
        return "敌意显"
    return "浅怨"


def _relation_play_hint(opinion: int) -> str:
    value = int(opinion or 0)
    if value >= 60:
        return "可请其担保/荐人，但会坐实党援。"
    if value >= 40:
        return "可借力试差，先要连坐担保。"
    if value >= 0:
        return "可先探口风，不宜径托大事。"
    hurt = abs(value)
    if hurt >= 60:
        return "可召双方共办/调停，偏袒会结怨。"
    if hurt >= 40:
        return "可借其制衡，也可设小差调停消怨。"
    return "可先问旧因，留调停余地。"


def _relation_payload(row) -> Dict[str, object]:
    opinion = int(row["opinion"])
    return {
        "name": str(row["name"]),
        "opinion": opinion,
        "basis": str(row["basis"]),
        "strength_label": _relation_strength_label(opinion),
        "play_hint": _relation_play_hint(opinion),
    }


def allies_of(db: GameDB, name: str, limit: int = 4, threshold: int = 18) -> List[Dict[str, object]]:
    rows = db.conn.execute(
        "SELECT r.b_name AS name, r.opinion, r.basis FROM relationships r "
        "JOIN characters c ON c.name=r.b_name "
        "WHERE r.a_name=? AND r.opinion>=? AND c.status='active' "
        "ORDER BY r.opinion DESC LIMIT ?",
        (name, int(threshold), int(limit)),
    ).fetchall()
    return [_relation_payload(r) for r in rows]


def rivals_of(db: GameDB, name: str, limit: int = 4, threshold: int = -18) -> List[Dict[str, object]]:
    rows = db.conn.execute(
        "SELECT r.b_name AS name, r.opinion, r.basis FROM relationships r "
        "JOIN characters c ON c.name=r.b_name "
        "WHERE r.a_name=? AND r.opinion<=? AND c.status='active' "
        "ORDER BY r.opinion ASC LIMIT ?",
        (name, int(threshold), int(limit)),
    ).fetchall()
    return [_relation_payload(r) for r in rows]


# ── 个人私心推导 ──────────────────────────────────────────────────────────────

def _derive_agenda(name: str, office: str, office_type: str, faction: str,
                   ability: int, integrity: int, loyalty: int, grievance: int,
                   traits: str) -> Tuple[str, str, str, int]:
    """规则化推导一条暗中私心：(kind, title, target_hint, intensity)。零 LLM。"""
    o, ot, fa = str(office or ""), str(office_type or ""), str(faction or "")
    tr = str(traits or "")
    # 边镇总兵/督师：拥兵自重、催饷固本
    if "总兵" in o or "督师" in o or "镇" in o or ot == "军事":
        return ("entrench", "固本镇、足兵饷，不愿轻动以保实力", "", 55 + min(25, grievance // 3))
    # 地方督抚：保境、要资源、避株连
    if ot == "地方" or "巡抚" in o or "总督" in o or "按察" in o or "布政" in o:
        return ("entrench", "保一方安靖、多要钱粮，少担朝廷株连之险", "", 48)
    # 阉党残余：自保求宽宥
    if "阉" in fa:
        return ("survive", "魏阉既除，惟求自保、洗白旧迹、免遭清算", "", 60)
    # 贪墨成性：自肥
    if "贪墨" in tr or integrity < 35:
        return ("enrich", "趁掌钱粮人事之便，敛财自固、植党自重", "", 50 + min(20, (40 - integrity)))
    # 清流/东林、刚直：廓清异己、争清议
    if ("东林" in fa or "清流" in fa) or "直言" in tr or integrity >= 70:
        return ("revenge", "廓清阉党余孽、伸张清议，不与浊流同朝", "", 52 + min(20, integrity // 4))
    # 高能力低位 or 内阁边缘：进取拜相
    if ability >= 70 and ("大学士" not in o):
        return ("climb", "积资望、结奥援，志在入阁拜相", "", 50 + min(25, ability // 4))
    # 结党营私倾向：护党
    if "结党" in tr or "门户" in tr:
        return ("protect", "护持本党同气、要害位置安插自己人", "", 50)
    # 默认：稳进、避祸
    return ("climb", "谨守班位、徐图迁转，不愿出头担险", "", 42)


def agenda_of(db: GameDB, name: str) -> Optional[Dict[str, object]]:
    row = db.conn.execute(
        "SELECT kind, title, target_name, intensity, status FROM npc_agendas WHERE name=?", (name,)
    ).fetchone()
    if row is None:
        return None
    kind = str(row["kind"])
    target = str(row["target_name"])
    try:
        from ming_sim.playstyle import _agenda_bargain_profile
        bargain = dict(_agenda_bargain_profile(kind, target))
    except Exception:
        bargain = {}
    return {"kind": kind, "title": str(row["title"]),
            "target": target, "intensity": int(row["intensity"]),
            "status": str(row["status"]), "bargain": bargain}


def favor_memories(db: GameDB, name: str, limit: int = 3, *, current_turn: Optional[int] = None) -> List[Dict[str, object]]:
    """Recent soft hooks: imperial favors that should color future summons.

    current_turn: 显式传入可省一次 db.load_state() 全态读（热路径 _context_payload 已持有 state）。
    """
    if current_turn is None:
        try:
            state = db.load_state()
            current_turn = int(state.turn)
        except Exception:
            current_turn = 0
    rows = db.conn.execute(
        """
        SELECT turn, year, period, title, cause, process, outcome, sentiment, importance, tags
        FROM event_memories
        WHERE subject_type='character'
          AND subject_id=?
          AND event_type='imperial_favor'
          AND (expires_turn IS NULL OR expires_turn>=?)
        ORDER BY importance DESC, turn DESC, id DESC
        LIMIT ?
        """,
        (name, int(current_turn), max(1, min(8, int(limit or 3)))),
    ).fetchall()
    out: List[Dict[str, object]] = []
    for row in rows:
        try:
            tags = json.loads(str(row["tags"] or "[]"))
        except Exception:
            tags = []
        out.append({
            "turn": int(row["turn"]),
            "year": int(row["year"]),
            "period": int(row["period"]),
            "title": str(row["title"] or ""),
            "cause": str(row["cause"] or ""),
            "process": str(row["process"] or ""),
            "outcome": str(row["outcome"] or ""),
            "sentiment": str(row["sentiment"] or "positive"),
            "importance": int(row["importance"] or 0),
            "tags": tags if isinstance(tags, list) else [],
        })
    return out


# ── 开局播种 ──────────────────────────────────────────────────────────────────

def seed_court(db: GameDB, *, force: bool = False) -> Dict[str, int]:
    """从 npc_network.relations + 派系，播种好感网络与个人私心。幂等（已有数据则跳过）。"""
    has_rel = db.conn.execute("SELECT 1 FROM relationships LIMIT 1").fetchone() is not None
    has_agenda = db.conn.execute("SELECT 1 FROM npc_agendas LIMIT 1").fetchone() is not None
    if has_rel and has_agenda and not force:
        return {"relationships": 0, "agendas": 0}

    # 只播明廷在朝官员；后金/蒙古/流寇等敌国势力不入"宫廷"网络。
    chars = {str(r["name"]): r for r in db.conn.execute(
        "SELECT name, office, office_type, faction, ability, integrity, loyalty, grievance "
        "FROM characters WHERE power_id='ming'"
    ).fetchall()}
    network = getattr(db.content, "npc_network", {}) or {}

    rel_n = 0
    if not has_rel or force:
        for name, entry in network.items():
            name = _strip(name)
            if name not in chars:
                continue
            fa = str(chars[name]["faction"] or "")
            relations = entry.get("relations") if isinstance(entry, dict) else None
            if not isinstance(relations, list):
                continue
            for rel in relations:
                if not isinstance(rel, dict):
                    continue
                target = _strip(rel.get("target"))
                if not target or target not in chars or target == name:
                    continue
                rtype = str(rel.get("type") or "")
                note = str(rel.get("note") or "")
                base = _valence(rtype, note)
                fb = str(chars[target]["faction"] or "")
                opinion = _clamp(base + _faction_baseline(fa, fb))
                if opinion == 0:
                    continue
                _set_opinion(db, name, target, opinion, rtype or "旧识", 0)
                # 关系数据多半单向记载，补一条七折的对向（若对向未显式给出）
                if get_opinion(db, target, name) == 0:
                    _set_opinion(db, target, name, _clamp(int(opinion * 0.7)), rtype or "旧识", 0)
                rel_n += 1
        db.conn.commit()

    agenda_n = 0
    if not has_agenda or force:
        for name, r in chars.items():
            ot = str(r["office_type"] or "")
            if ot == "后宫":
                continue
            traits = _traits_text(db, name)
            kind, title, target, intensity = _derive_agenda(
                name, str(r["office"] or ""), ot, str(r["faction"] or ""),
                int(r["ability"] or 50), int(r["integrity"] or 50),
                int(r["loyalty"] or 50), int(r["grievance"] or 20), traits)
            db.conn.execute(
                "INSERT OR REPLACE INTO npc_agendas (name, kind, title, target_name, intensity, status) "
                "VALUES (?,?,?,?,?, 'active')",
                (name, kind, title, target, int(intensity)))
            agenda_n += 1
        db.conn.commit()

    return {"relationships": rel_n, "agendas": agenda_n}


def _traits_text(db: GameDB, name: str) -> str:
    """从 npc_network ability_logic 取特质文字（用于私心推导，无则空）。"""
    network = getattr(db.content, "npc_network", {}) or {}
    entry = network.get(name)
    if isinstance(entry, dict):
        return str(entry.get("ability_logic") or "")
    return ""


# ── 召对/人物卡用：宫廷处境简报 ───────────────────────────────────────────────

def court_payload(db: GameDB, name: str) -> Dict[str, object]:
    """前端人物卡：性格特质 + 私心 + 党羽 + 政敌。"""
    try:
        from ming_sim.traits import traits_of
        traits = traits_of(db, name, include_medical=False)
    except ImportError:
        traits = []
    duishi = ""
    try:
        from ming_sim.harem import duishi_partner
        duishi = duishi_partner(db, name)
    except Exception:
        duishi = ""
    secret = None
    intrigue_previews = {}
    try:
        from ming_sim.intrigue import preview_intrigue_effects, secrets_for
        secret = secrets_for(db, name)
        ally_rows = allies_of(db, name, limit=1)
        ally = str(ally_rows[0]["name"]) if ally_rows else ""
        intrigue_previews = preview_intrigue_effects(db, name, ally=ally)
    except Exception:
        secret = None
        intrigue_previews = {}
    back_previews = {}
    try:
        row = db.conn.execute(
            "SELECT status, power_id, office_type FROM characters WHERE name=?", (name,)
        ).fetchone()
        can_back = (
            row is not None
            and str(row["power_id"] or "") == "ming"
            and str(row["office_type"] or "") != "君主"
            and str(row["status"] or "") in {"active", "imprisoned", "dismissed"}
            and name != "崇祯"
        )
        if can_back:
            from ming_sim.memorials import preview_back_official_effects
            back_previews = {
                "shoulder": preview_back_official_effects(db, name, "shoulder"),
                "comfort": preview_back_official_effects(db, name, "comfort"),
            }
            if str(row["status"] or "") in {"imprisoned", "dismissed"}:
                back_previews["reuse"] = preview_back_official_effects(db, name, "reuse")
    except Exception:
        back_previews = {}
    return {
        "traits": traits,
        "agenda": agenda_of(db, name),
        "favor_memories": favor_memories(db, name, limit=3),
        "allies": allies_of(db, name, limit=4),
        "rivals": rivals_of(db, name, limit=4),
        "duishi": duishi,
        "secret": secret,
        "back_previews": back_previews,
        "intrigue_previews": intrigue_previews,
    }


_KIND_CN = {"climb": "进取", "protect": "护党", "revenge": "夙怨",
            "enrich": "自肥", "survive": "自保", "entrench": "自重"}


def court_brief(db: GameDB, name: str) -> str:
    """注入召对 system prompt：让 LLM 据其私心与恩怨演绎，而非给通用稳妥答案。"""
    ag = agenda_of(db, name)
    favors = favor_memories(db, name, limit=2)
    allies = allies_of(db, name, limit=3)
    rivals = rivals_of(db, name, limit=3)
    lines: List[str] = []
    if ag and ag.get("title"):
        lines.append(f"【私心】{_KIND_CN.get(str(ag['kind']), '')}：{ag['title']}（不可明言，化作口风与条件）")
    if favors:
        text = "；".join(
            f"{item.get('title') or '旧恩'}（{item.get('outcome') or item.get('cause') or '须记得皇帝昔日保全'}）"
            for item in favors
        )
        lines.append(f"【旧恩】{text}：说话时要记得皇帝曾保全/复用自己，若陛下托事不可装作两清。")
    if allies:
        lines.append("【党羽】" + "、".join(f"{a['name']}（{a['basis']}）" for a in allies)
                     + "：会回护、引荐、通声气。")
    if rivals:
        lines.append("【政敌】" + "、".join(f"{r['name']}（{r['basis']}）" for r in rivals)
                     + "：积怨已深，遇之则倾轧、相攻、互不信任。")
    if not lines:
        return ""
    return "宫廷处境（据此演绎，勿裸报）：\n" + "\n".join(lines)


# ── 人事处置牵动整张网（CK3「你无法取悦所有人」）─────────────────────────────

def _adjust_char(db: GameDB, name: str, *, emp_trust: int = 0, grievance: int = 0) -> None:
    """对在朝官员的对帝信任/怨气小幅增减（clamp 0..100）。"""
    row = db.conn.execute(
        "SELECT emp_trust, grievance FROM characters WHERE name=? AND status='active'", (name,)
    ).fetchone()
    if row is None:
        return
    et = max(0, min(100, int(row["emp_trust"] or 55) + int(emp_trust)))
    gr = max(0, min(100, int(row["grievance"] or 20) + int(grievance)))
    db.conn.execute("UPDATE characters SET emp_trust=?, grievance=? WHERE name=?", (et, gr, name))


def ripple_personnel(db: GameDB, name: str, kind: str, *, day: int = 0) -> Dict[str, object]:
    """皇帝对某人的处置在关系网中荡开涟漪：
      kind='oust'  （罢/狱/流/死）：其党羽怨怒（信任↓怨气↑）、政敌称快（信任↑）。
      kind='favor' （起复/擢升/厚赏）：其人感念、党羽欣慰、政敌侧目戒备。
      kind='shield'（背书/买单）：其党羽感到皇帝肯保人、政敌不悦；当事人收益由调用方处理。
    返回被波及者摘要，供事件呈现。零 LLM。
    """
    allies = allies_of(db, name, limit=5)
    rivals = rivals_of(db, name, limit=5)
    touched = {"allies": [], "rivals": [], "kind": kind, "subject": name}
    if kind == "oust":
        for a in allies:
            _adjust_char(db, a["name"], emp_trust=-6, grievance=+5)
            touched["allies"].append(a["name"])
        for r in rivals:
            _adjust_char(db, r["name"], emp_trust=+4, grievance=-3)
            touched["rivals"].append(r["name"])
    elif kind == "favor":
        _adjust_char(db, name, emp_trust=+6, grievance=-4)
        for a in allies:
            _adjust_char(db, a["name"], emp_trust=+3)
            touched["allies"].append(a["name"])
        for r in rivals:
            _adjust_char(db, r["name"], grievance=+3)
            touched["rivals"].append(r["name"])
    elif kind == "shield":
        for a in allies:
            _adjust_char(db, a["name"], emp_trust=+3, grievance=-2)
            touched["allies"].append(a["name"])
        for r in rivals:
            _adjust_char(db, r["name"], emp_trust=-2, grievance=+3)
            touched["rivals"].append(r["name"])
    db.conn.commit()
    return touched


# ── 自主出招（每旬）──────────────────────────────────────────────────────────

def court_moves_tick(db: GameDB, state: GameState, day: int) -> List[Dict[str, object]]:
    """逢旬错峰（每月第 8/18/28 日）执行至多 1 招个人级行动：
    怨念最深者弹劾政敌、野心最炽者保举党羽——点名真实恩怨，并真实改写好感网络。
    """
    from ming_sim.upgrade_schema import DAYS_PER_MONTH
    from ming_sim.timeflow import LEVEL_RED, LEVEL_YELLOW
    from ming_sim.memorials import create_memorial

    events: List[Dict[str, object]] = []
    dim = (int(day) - 1) % DAYS_PER_MONTH + 1
    if dim not in (8, 18, 28):
        return events
    rng = random.Random((int(day) * 0x9E3779B1) % (2 ** 31))

    # 候选：在朝、有私心、courage 足以出头的官员。
    actors = db.conn.execute(
        "SELECT c.name, c.office, c.courage, a.kind, a.intensity "
        "FROM characters c JOIN npc_agendas a ON a.name=c.name "
        "WHERE c.status='active' AND c.power_id='ming' AND c.office_type!='后宫' AND a.status='active' "
        "ORDER BY a.intensity DESC LIMIT 24"
    ).fetchall()
    if not actors:
        return events

    # 性格特质偏置（CK3 化 P4）：弹劾/植党倾向随特质浮动，让自主行为有人格。
    try:
        from ming_sim.traits import trait_bias
    except ImportError:
        trait_bias = None

    # 先试"弹劾政敌"（戏剧性最强）：找一个有深仇且有胆的人。
    for row in actors:
        name, office, courage = str(row["name"]), str(row["office"]), int(row["courage"] or 50)
        if courage < 50:
            continue
        rv = rivals_of(db, name, limit=1, threshold=-40)
        if not rv:
            continue
        impeach_p = 0.45 + (trait_bias(db, name)["impeach"] if trait_bias else 0.0)
        if rng.random() >= impeach_p:
            continue
        target = rv[0]
        # 去重（实玩实证：御案常见多封逐字雷同的弹章劾同一目标）——
        # 同一被劾者已有在案弹章（pending）时不再叠生，避免刷屏并减轻奏疏洪流。
        dup = db.conn.execute(
            "SELECT 1 FROM memorials WHERE kind='弹章' AND status='pending' "
            "AND ref_kind='character' AND ref_id=? LIMIT 1", (str(target["name"]),)).fetchone()
        if dup is not None:
            continue
        trow = db.conn.execute("SELECT office FROM characters WHERE name=?", (target["name"],)).fetchone()
        toffice = str(trow["office"]) if trow else ""
        basis = target["basis"] or "夙怨"
        # 把柄喂弹章 credibility（宫斗阴谋）：被劾者已有御前掌握的把柄 → 弹章师出有名、更重更难驳。
        hook = None
        try:
            from ming_sim.intrigue import secrets_for
            hook = secrets_for(db, str(target["name"]))
        except Exception:
            hook = None
        if hook:
            mid = create_memorial(
                db, None, day=day, author_name=name, org=office, kind="弹章", urgency=4,
                summary=f"{name}劾{toffice}{target['name']}（{hook['label']}·有实)",
                full_text=(f"臣劾{target['name']}{hook['label']}：{hook['detail']}，赃证粲然、众目共睹。"
                           f"此非风闻，乃确有其事，乞陛下亟正典刑，以肃朝纲。"),
                ref_kind="character", ref_id=str(target["name"]))
        else:
            mid = create_memorial(
                db, None, day=day, author_name=name, org=office, kind="弹章", urgency=3,
                summary=f"{name}劾{toffice}{target['name']}（{basis}）",
                full_text=(f"臣与{target['name']}夙有{basis}。今风闻其居官有玷——"
                           f"或植党、或欺罔、或贪墨，乞陛下亟赐究问，以肃朝纲、正国法。"),
                ref_kind="character", ref_id=str(target["name"]))
        adjust_opinion(db, name, target["name"], -10, basis, day=day)
        events.append({
            "level": LEVEL_RED, "kind": "court_move",
            "title": f"私怨成弹章：{name}劾{target['name']}",
            "detail": f"两人因「{basis}」积怨已深。准其所奏、留中、还是申斥言官？满朝都看着陛下落子。",
            "ref_kind": "memorial", "ref_id": str(mid), "day": day,
        })
        return events

    # 否则试"保举党羽"：野心炽且有在朝盟友者，引荐其党。
    for row in actors:
        name, office = str(row["name"]), str(row["office"])
        if int(row["intensity"] or 0) < 50:
            continue
        al = allies_of(db, name, limit=1, threshold=28)
        if not al:
            continue
        patronage_p = 0.45 + (trait_bias(db, name)["patronage"] if trait_bias else 0.0)
        if rng.random() >= patronage_p:
            continue
        ally = al[0]
        arow = db.conn.execute("SELECT office FROM characters WHERE name=?", (ally["name"],)).fetchone()
        aoffice = str(arow["office"]) if arow else ""
        basis = ally["basis"] or "旧交"
        mid = create_memorial(
            db, None, day=day, author_name=name, org=office, kind="荐人", urgency=2,
            summary=f"{name}保举{aoffice}{ally['name']}（{basis}）",
            full_text=(f"臣谨保举{aoffice}{ally['name']}，与臣有{basis}之谊，才识忠勤、堪当大任，"
                       f"乞陛下量才擢用，以收得人之效。"),
            ref_kind="character", ref_id=str(ally["name"]))
        adjust_opinion(db, ally["name"], name, 6, basis, day=day)  # 受荐者更感念
        events.append({
            "level": LEVEL_YELLOW, "kind": "court_move",
            "title": f"朋党相荐：{name}举{ally['name']}",
            "detail": f"借「{basis}」之谊植党要津。擢之则其党坐大，抑之则寒了能臣之心。",
            "ref_kind": "memorial", "ref_id": str(mid), "day": day,
        })
        return events

    return events
