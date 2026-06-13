"""御案系统（S4）+ 崇祯陷阱（S5）。L7。

支柱 P3「注意力稀缺」：奏疏随时间流陆续送达御案，每日注意力有限，
留中积压有后果；票拟是有立场的过滤器。
支柱 P2「问责越狠，担责越少」：risk_aversion（RA）是官僚集体的任事风险先验——
- 惩罚办事失败者 → RA↑ → 请旨奏疏暴增、主动奏报萎缩、执行推诿
- 为失败的忠臣买单 → RA↓（短期掉势：真实取舍）
两个数值同屏展示（/api/beliefs + /api/desk 积压数）让玩家亲眼看见循环。

全规则层零 LLM；奏疏正文与票拟由 scheduler LLM 任务润色（模板兜底）。
"""

from __future__ import annotations

import json
import random
from typing import Dict, List, Optional

from ming_sim.db import GameDB
from ming_sim.models import GameState
from ming_sim.timeflow import LEVEL_BLUE, LEVEL_RED, LEVEL_YELLOW
from ming_sim.upgrade_schema import (
    ATTENTION_PER_DAY,
    KV_ATTENTION,
    KV_RISK_AVERSION,
    KV_SHI,
    RISK_AVERSION_DEFAULT,
    SHI_DEFAULT,
    adjust_belief,
    kv_int,
    kv_set_int,
)

# 批红动作注意力消耗（每日 ATTENTION_PER_DAY=12 点；留中免费——这正是它的诱惑）
ATTENTION_COSTS = {
    "approve": 1, "deny": 1, "refer": 1, "read": 2, "shelve": 0,
}

KV_LAST_ATTENTION_DAY = "upgrade.attention_day"

# 奏疏淹没：弹章/告变/密揭按「急」算（越压不得）。单一真源——
# 日 tick 淹没判定与御案倒计时倒数同此公式，禁止各自重算。
EXPIRE_FORCED_URGENT_KINDS = ("弹章", "告变", "密揭")


def expire_deadline_days(kind: str, urgency: int) -> int:
    """奏疏自到案起多少日无人处置即「淹没」出队（urgency 越高越快）：u1→45 u2→40 u3→35。"""
    u = 3 if kind in EXPIRE_FORCED_URGENT_KINDS else max(1, min(3, int(urgency or 2)))
    return 50 - u * 5


# ── 注意力 ───────────────────────────────────────────────────────────────────

def reset_attention_for_day(db: GameDB, day: int) -> None:
    if kv_int(db, KV_LAST_ATTENTION_DAY, 0) != day:
        kv_set_int(db, KV_ATTENTION, ATTENTION_PER_DAY)
        kv_set_int(db, KV_LAST_ATTENTION_DAY, day)


def attention_left(db: GameDB) -> int:
    return kv_int(db, KV_ATTENTION, ATTENTION_PER_DAY)


def consume_attention(db: GameDB, cost: int) -> bool:
    left = attention_left(db)
    if left < cost:
        return False
    kv_set_int(db, KV_ATTENTION, left - cost)
    return True


# ── 奏疏生成（规则层）────────────────────────────────────────────────────────

_KIND_TEMPLATES = {
    "请旨": "臣愚昧，事关重大，不敢擅专，伏乞圣裁。",
    "请款": "库藏匮乏，需用孔亟，恳乞拨给，以济燃眉。",
    "陈情": "谨将地方情形据实奏闻，伏乞圣鉴。",
    "告变": "事机紧急，恭报上闻，伏乞速赐睿断。",
    "荐人": "臣谨保举贤员，堪当重任，伏候钦定。",
    "弹章": "臣职司风宪，不敢缄默，谨据实纠劾，伏乞圣明立断。",
}

_PIAONI_TEMPLATES = {
    "请旨": "拟：知道了。该衙门酌量奏行。",
    "请款": "拟：着户部覆议具奏。",
    "陈情": "拟：览。该地方官加意抚绥。",
    "告变": "拟：着该部速议剿抚事宜具奏。",
    "荐人": "拟：吏部知道。",
    "弹章": "拟：该员着回奏。",
}


def create_memorial(db: GameDB, state: Optional[GameState], *, day: int, author_name: str, org: str,
                    kind: str, urgency: int, summary: str, full_text: str = "",
                    ref_kind: str = "", ref_id: str = "") -> int:
    """state 仅为调用方便保留，可传 None（worker 线程场景）。"""
    piaoni_author = _duty_grand_secretary(db)
    cur = db.conn.execute(
        """INSERT INTO memorials (author_name, org, kind, urgency, summary, full_text,
           piaoni, piaoni_author, arrived_day, status, ref_kind, ref_id)
           VALUES (?,?,?,?,?,?,?,?,?,'pending',?,?)""",
        (author_name, org, kind, max(1, min(3, int(urgency))), summary[:120],
         full_text or _KIND_TEMPLATES.get(kind, ""),
         _PIAONI_TEMPLATES.get(kind, "拟：知道了。"), piaoni_author,
         int(day), ref_kind, ref_id),
    )
    db.conn.commit()
    mid = int(cur.lastrowid)
    # 票拟与正文润色入 LLM 队列（模板已兜底，UI 不空窗）
    try:
        from ming_sim.scheduler import enqueue_job
        if int(urgency) >= 2:
            enqueue_job(db, "piaoni", {"memorial_id": mid})
    except Exception:
        pass
    return mid


def _duty_grand_secretary(db: GameDB) -> str:
    row = db.conn.execute(
        "SELECT name FROM characters WHERE status='active' AND "
        "(office LIKE '%大学士%' OR office LIKE '%内阁%') ORDER BY ability DESC LIMIT 1"
    ).fetchone()
    return str(row["name"]) if row else ""


def _random_official(db: GameDB, rng: random.Random, *, min_courage: int = 0):
    rows = db.conn.execute(
        "SELECT name, office, faction, courage FROM characters "
        "WHERE status='active' AND office_type NOT IN ('后宫') AND courage>=? "
        "ORDER BY name", (int(min_courage),),
    ).fetchall()
    return rng.choice(rows) if rows else None


def memorials_daily_tick(db: GameDB, state: GameState, day: int) -> List[Dict[str, object]]:
    """每日：注意力刷新、奏疏生成（量随 RA 放大——崇祯陷阱的传导介质）、留中计数。"""
    events: List[Dict[str, object]] = []
    reset_attention_for_day(db, day)
    rng = random.Random(day * 7919 + state.turn)
    ra = kv_int(db, KV_RISK_AVERSION, RISK_AVERSION_DEFAULT)

    # 1) 请旨/陈情流：基准 0.45 封/日 × (1 + RA/100)。RA=100 时翻倍——御案被淹。
    arrival_rate = 0.45 * (1.0 + ra / 100.0)
    if rng.random() < arrival_rate:
        author = _random_official(db, rng)
        if author is not None:
            issue_row = db.conn.execute(
                "SELECT id, title FROM issues WHERE status='active' ORDER BY RANDOM() LIMIT 1"
            ).fetchone()
            if issue_row is not None and rng.random() < 0.6:
                kind = "请旨" if rng.random() < 0.55 + ra / 300.0 else "陈情"
                summary = f"为「{issue_row['title']}」事{kind}"
                ref_kind, ref_id = "issue", str(issue_row["id"])
            else:
                kind = rng.choice(["请款", "陈情", "荐人"])
                summary = f"{author['office']}{author['name']}{kind}"
                ref_kind, ref_id = "", ""
            mid = create_memorial(db, state, day=day, author_name=str(author["name"]),
                                  org=str(author["office"]), kind=kind, urgency=rng.choice([1, 1, 2]),
                                  summary=summary, ref_kind=ref_kind, ref_id=ref_id)
            events.append({"level": LEVEL_BLUE, "kind": "memorial", "title": f"奏疏到案：{summary}",
                           "detail": "", "ref_kind": "memorial", "ref_id": str(mid), "day": day})

    # 2) 主动奏报（告变/献策）：勇直之臣才上，概率 ×(1-RA/150)——寒心则无人言事。
    proactive_rate = 0.18 * max(0.0, 1.0 - ra / 150.0)
    if rng.random() < proactive_rate:
        author = _random_official(db, rng, min_courage=60)
        if author is not None:
            mid = create_memorial(db, state, day=day, author_name=str(author["name"]),
                                  org=str(author["office"]), kind="告变", urgency=2,
                                  summary=f"{author['name']}据实奏闻地方利病")
            events.append({"level": LEVEL_YELLOW, "kind": "memorial",
                           "title": f"直臣上书：{author['name']}据实奏闻",
                           "detail": "勇于任事之臣主动言事——任事意愿尚存的迹象。",
                           "ref_kind": "memorial", "ref_id": str(mid), "day": day})

    # 3) 留中积压：每 10 日结一次怨账；弹章留中折势。逾期则淹没出队（一次性结算），
    #    使待奏队列与「弹章留中折势」均有界——奏而不答是有代价的，但代价不无限累积。
    rows = db.conn.execute("SELECT * FROM memorials WHERE status='pending'").fetchall()
    for row in rows:
        shelved = int(day) - int(row["arrived_day"])
        deadline = expire_deadline_days(str(row["kind"]), int(row["urgency"] or 2))
        if shelved >= deadline:
            # 淹没：出队 + 一次性后果。弹章淹没＝言路寒心（势/RA 一次性折损 + 同党记恨）。
            db.conn.execute("UPDATE memorials SET status='expired', shelved_days=?, decided_day=? WHERE id=?",
                            (shelved, int(day), int(row["id"])))
            author = str(row["author_name"] or "")
            if author:
                db.conn.execute(
                    "UPDATE characters SET grievance=MIN(100, grievance+6), "
                    "emp_trust=MAX(0, emp_trust-4) WHERE name=?", (author,))
            if str(row["kind"]) == "弹章":
                adjust_belief(db, KV_SHI, -3, f"弹章淹没不报（#{row['id']}）", day=day)
                try:
                    from ming_sim.theater import adjust_faction_heat, faction_of
                    adjust_faction_heat(db, faction_of(db, author), +4, "弹章石沉大海")
                except Exception:
                    pass
            adjust_belief(db, KV_RISK_AVERSION, +1,
                          f"奏疏淹没（{author}{row['kind']}）", day=day)
            events.append({"level": LEVEL_YELLOW, "kind": "memorial_expired",
                           "title": f"奏疏淹没：{str(row['summary'])[:28]}",
                           "detail": "久奏不答，其人灰心，事亦不了了之——然臣心已寒。",
                           "ref_kind": "memorial", "ref_id": str(row["id"]), "day": day})
            continue
        db.conn.execute("UPDATE memorials SET shelved_days=? WHERE id=?",
                        (shelved, int(row["id"])))
        if shelved > 0 and shelved % 10 == 0:
            author = str(row["author_name"] or "")
            if author:
                db.conn.execute(
                    "UPDATE characters SET grievance=MIN(100, grievance+3) WHERE name=?",
                    (author,))
            if str(row["kind"]) == "弹章":
                adjust_belief(db, KV_SHI, -2, f"弹章留中不发（#{row['id']}）", day=day)
            if shelved == 30:
                adjust_belief(db, KV_RISK_AVERSION, +1,
                              f"奏疏积压逾月（{row['author_name']}{row['kind']}）", day=day)
                events.append({"level": LEVEL_YELLOW, "kind": "memorial_overdue",
                               "title": f"奏疏积压逾月：{str(row['summary'])[:30]}",
                               "detail": "奏而不答，臣下渐生观望。",
                               "ref_kind": "memorial", "ref_id": str(row["id"]), "day": day})
    db.conn.commit()
    return events


# ── 批红 ─────────────────────────────────────────────────────────────────────

def decide_memorial(db: GameDB, state: GameState, memorial_id: int, action: str,
                    *, day: int, note: str = "") -> Dict[str, object]:
    """approve 照准 / deny 驳 / shelve 留中 / refer 发部议。消耗注意力。"""
    row = db.conn.execute("SELECT * FROM memorials WHERE id=?", (int(memorial_id),)).fetchone()
    if row is None:
        return {"ok": False, "message": "无此奏疏。"}
    if str(row["status"]) not in ("pending", "shelved"):
        return {"ok": False, "message": "该疏已批。"}
    cost = ATTENTION_COSTS.get(action)
    if cost is None:
        return {"ok": False, "message": f"未知批红动作：{action}"}
    if not consume_attention(db, cost):
        return {"ok": False, "message": "今日精力已竭（注意力不足），明日再批，或将此疏留中。"}

    mid = int(row["id"])
    author = str(row["author_name"] or "")
    kind = str(row["kind"])
    message = ""
    if action == "approve":
        db.conn.execute(
            "UPDATE memorials SET status='approved', decided_day=?, decision_note=? WHERE id=?",
            (int(day), note[:200], mid))
        if author:
            db.conn.execute(
                "UPDATE characters SET grievance=MAX(0, grievance-4), "
                "emp_trust=MIN(100, emp_trust+2) WHERE name=?", (author,))
        if kind in ("告变", "陈情"):
            adjust_belief(db, KV_RISK_AVERSION, -1, f"采纳{author}直言", day=day)
        message = "照准。" + (f"{author}感奋。" if author else "")
        # 起复荐疏照准（NPC 基座人才池闭环）：路由层据 followup 完成入朝注册
        if kind == "荐人" and str(row["ref_kind"]) == "foundation_npc" and str(row["ref_id"]):
            db.conn.commit()
            return {"ok": True, "message": f"准奏，征{row['ref_id']}入朝听用。",
                    "attention_left": attention_left(db),
                    "followup": {"kind": "recruit_foundation", "name": str(row["ref_id"])}}
        # 弹章照准（S7）：被劾者获谴，劾人一方扬眉，被劾一方衔恨
        if kind == "弹章" and str(row["ref_kind"]) == "character" and str(row["ref_id"]):
            target = str(row["ref_id"])
            db.conn.execute(
                "UPDATE characters SET grievance=MIN(100, grievance+25), "
                "emp_trust=MAX(0, emp_trust-12) WHERE name=?", (target,))
            adjust_belief(db, KV_RISK_AVERSION, +2, f"准弹章查办{target}", day=day)
            try:
                from ming_sim.theater import adjust_faction_heat, faction_of
                adjust_faction_heat(db, faction_of(db, author), -5, "弹章得准")
                adjust_faction_heat(db, faction_of(db, target), +10, "同党被劾获谴")
            except Exception:
                pass
            message = f"准奏，{target}着回奏待勘。两派俱看在眼里。"
    elif action == "deny":
        db.conn.execute(
            "UPDATE memorials SET status='denied', decided_day=?, decision_note=? WHERE id=?",
            (int(day), note[:200], mid))
        if author:
            db.conn.execute(
                "UPDATE characters SET grievance=MIN(100, grievance+5) WHERE name=?", (author,))
        if kind == "告变":
            adjust_belief(db, KV_RISK_AVERSION, +2, f"直言获谴（驳{author}告变疏）", day=day)
            message = "掷还。直言获谴，言路渐塞。"
        elif kind == "弹章":
            try:
                from ming_sim.theater import adjust_faction_heat, faction_of
                adjust_faction_heat(db, faction_of(db, author), +5, "弹章被驳")
            except Exception:
                pass
            message = "掷还。劾者之党衔之。"
        else:
            message = "掷还。"
    elif action == "shelve":
        db.conn.execute("UPDATE memorials SET status='pending' WHERE id=?", (mid,))
        message = "留中不发。（积压有后果：上疏人怨望、弹章折势、逾月增观望）"
    elif action == "refer":
        db.conn.execute(
            "UPDATE memorials SET status='referred', decided_day=?, decision_note=? WHERE id=?",
            (int(day), note[:200], mid))
        # 发部议 → 自动生成旨意草案，走正常拟诏/生命周期流程
        draft = f"下部议：{str(row['summary'])}。{str(row['full_text'] or '')[:80]}着该衙门议奏施行。"
        db.conn.execute(
            "INSERT INTO turn_directives (turn, year, period, text, source, status, notes)"
            " VALUES (?,?,?,?,?,?,?)",
            (state.turn, state.year, state.period, draft, "memorial_refer", "draft",
             f"发部议自奏疏#{mid}"))
        message = "发该部议奏。已生成旨意草案，颁诏时一并下达。"
    db.conn.commit()
    return {"ok": True, "message": message, "attention_left": attention_left(db)}


# ── 票拟 LLM handler（过滤器是有立场的人）────────────────────────────────────

PIAONI_PROMPT = """你是明末内阁当值大学士，正为一份奏疏拟票（票拟：替皇帝拟好的批答建议，贴在奏疏上）。
你会收到：奏疏概要与正文、上疏人及其派系、你自己的姓名与派系。
要求：
1. 30-60 字票拟，明代公文体（"拟：……"开头）。
2. 你有立场：同派系的奏请倾向玉成，政敌的奏请倾向驳议或拖（"下部覆议"），但措辞必须冠冕堂皇——立场藏在程序建议里，不许露骨。
3. 直接输出票拟文本。"""


def _handle_piaoni(db, llm_config, payload: Dict[str, object]) -> str:
    mid = int(payload.get("memorial_id") or 0)
    row = db.conn.execute("SELECT * FROM memorials WHERE id=?", (mid,)).fetchone()
    if row is None:
        return ""
    author_faction = ""
    arow = db.conn.execute(
        "SELECT faction FROM characters WHERE name=?", (str(row["author_name"]),)).fetchone()
    if arow:
        author_faction = str(arow["faction"])
    piaoni_author = str(row["piaoni_author"] or "")
    pfaction = ""
    prow = db.conn.execute(
        "SELECT faction FROM characters WHERE name=?", (piaoni_author,)).fetchone()
    if prow:
        pfaction = str(prow["faction"])
    text = ""
    try:
        from ming_sim.scheduler import _run_text
        text = _run_text(llm_config, agent_id="piaoni-writer", prompt=PIAONI_PROMPT,
                         payload={"memorial": {"kind": str(row["kind"]),
                                               "summary": str(row["summary"]),
                                               "full_text": str(row["full_text"])[:300],
                                               "author": str(row["author_name"]),
                                               "author_faction": author_faction},
                                  "you": {"name": piaoni_author, "faction": pfaction}},
                         minimum=200)
    except Exception:
        text = ""
    if text:
        db.conn.execute("UPDATE memorials SET piaoni=? WHERE id=?", (text[:200], mid))
        db.conn.commit()
    return text


try:
    from ming_sim.scheduler import register_handler as _register_handler
    _register_handler("piaoni", _handle_piaoni)
except Exception:
    pass


# ── 崇祯陷阱双杠杆（S5）──────────────────────────────────────────────────────

_PUNISH_SEVERITY = {
    "light":   {"ra": +4, "shi": +2, "grievance": +15, "label": "申斥夺俸"},
    "heavy":   {"ra": +8, "shi": +4, "grievance": +35, "label": "革职下狱"},
    "execute": {"ra": +15, "shi": +6, "grievance": 0, "label": "弃市传首"},
}


def punish_official(db: GameDB, state: GameState, name: str, severity: str,
                    *, day: int, public: bool = True, reason: str = "") -> Dict[str, object]:
    """问罪办事失败的官员：短期立威（势+），长期寒心（RA+）。私下处置减半但有泄露风险。"""
    spec = _PUNISH_SEVERITY.get(severity)
    if spec is None:
        return {"ok": False, "message": f"未知问责档：{severity}"}
    row = db.conn.execute(
        "SELECT name, office FROM characters WHERE name=? AND status='active'", (name,)
    ).fetchone()
    if row is None:
        return {"ok": False, "message": "无此在朝官员。"}
    scale = 1.0 if public else 0.5
    rng = random.Random(day * 31337 + len(name))
    leaked = (not public) and rng.random() < 0.35
    if leaked:
        scale = 1.0
    ra_delta = round(spec["ra"] * scale)
    shi_delta = round(spec["shi"] * scale)
    adjust_belief(db, KV_RISK_AVERSION, ra_delta,
                  f"{spec['label']}{name}" + ("（密旨外泄）" if leaked else ""), day=day)
    adjust_belief(db, KV_SHI, shi_delta, f"{spec['label']}{name}立威", day=day)
    if severity == "execute":
        db.conn.execute(
            "UPDATE characters SET status='dead', status_reason=?, status_changed_turn=? WHERE name=?",
            (f"坐罪诛：{reason[:60]}" if reason else "坐罪诛", state.turn, name))
    else:
        db.conn.execute(
            "UPDATE characters SET grievance=MIN(100, grievance+?), "
            "emp_trust=MAX(0, emp_trust-20) WHERE name=?",
            (int(spec["grievance"] * scale), name))
        if severity == "heavy":
            db.conn.execute(
                "UPDATE characters SET status='imprisoned', status_reason=?, status_changed_turn=? WHERE name=?",
                (f"坐罪下狱：{reason[:60]}" if reason else "坐罪下狱", state.turn, name))
    db.record_log(state, f"【问责】{spec['label']}{str(row['office'])}{name}。" + (reason or ""))
    # 派系连坐感知（S7）：同党遭谴，派系敌意累积
    try:
        from ming_sim.theater import adjust_faction_heat, faction_of
        adjust_faction_heat(db, faction_of(db, name), +8 if severity != "execute" else +15, "同党遭问责")
    except Exception:
        pass
    db.conn.commit()
    db.save_state(state)
    return {"ok": True,
            "message": f"{spec['label']}{name}。势+{shi_delta}，而百官任事之心-{ra_delta}"
                       + ("。密旨竟外泄，朝野尽知。" if leaked else "。"),
            "leaked": leaked}


_BACK_KINDS = {
    "shoulder": {"ra": -8, "shi": -4, "label": "公开担责", "trust": +15,
                 "note": "上谕引咎：『此朕之过，非该臣之罪。』"},
    "comfort":  {"ra": -5, "shi": 0, "label": "抚恤褒奖", "trust": +10,
                 "note": "赐金抚恤，荫其子弟。"},
    "reuse":    {"ra": -10, "shi": -2, "label": "败后复用", "trust": +20,
                 "note": "败军之将弃而复用，朝野侧目，然任事者知上不弃人。"},
}


def back_official(db: GameDB, state: GameState, name: str, kind: str,
                  *, day: int, cost: int = 0) -> Dict[str, object]:
    """为失败的忠臣买单——破崇祯陷阱的反直觉之举：短期掉势/花钱，长期挽回任事意愿。"""
    spec = _BACK_KINDS.get(kind)
    if spec is None:
        return {"ok": False, "message": f"未知买单方式：{kind}"}
    row = db.conn.execute("SELECT name, office, status FROM characters WHERE name=?", (name,)).fetchone()
    if row is None:
        return {"ok": False, "message": "查无此人。"}
    if cost > 0:
        actual = db.record_issue_economy_move(
            state, "内库", -int(cost), "抚恤买单", f"{spec['label']}{name}")
        if not actual:
            return {"ok": False, "message": "内库不敷。"}
    adjust_belief(db, KV_RISK_AVERSION, spec["ra"], f"{spec['label']}{name}", day=day)
    if spec["shi"]:
        adjust_belief(db, KV_SHI, spec["shi"], f"{spec['label']}{name}（示弱）", day=day)
    db.conn.execute(
        "UPDATE characters SET grievance=MAX(0, grievance-15), emp_trust=MIN(100, emp_trust+?) WHERE name=?",
        (int(spec["trust"]), name))
    if kind == "reuse" and str(row["status"]) in ("imprisoned", "dismissed"):
        db.conn.execute(
            "UPDATE characters SET status='active', status_reason='败后复用', status_changed_turn=? WHERE name=?",
            (state.turn, name))
    db.record_log(state, f"【买单】{spec['label']}{name}。{spec['note']}")
    db.conn.commit()
    db.save_state(state)
    return {"ok": True, "message": f"{spec['label']}{name}。{spec['note']}（任事意愿回暖{abs(spec['ra'])}）"}


# ── 查询 ─────────────────────────────────────────────────────────────────────

def desk_payload(db: GameDB, state: GameState, day: int) -> Dict[str, object]:
    rows = db.conn.execute(
        "SELECT * FROM memorials WHERE status='pending' ORDER BY urgency DESC, arrived_day ASC LIMIT 60"
    ).fetchall()
    decided = db.conn.execute(
        "SELECT * FROM memorials WHERE status!='pending' ORDER BY decided_day DESC, id DESC LIMIT 20"
    ).fetchall()

    def _row(r) -> Dict[str, object]:
        kind = str(r["kind"])
        # 淹没倒计时：以「在案天数」(day-到案日，pending 比 shelved_days 更准) 折算
        shelved_now = max(int(r["shelved_days"]), int(day) - int(r["arrived_day"]))
        deadline = expire_deadline_days(kind, int(r["urgency"]))
        days_to_expire = max(0, deadline - shelved_now) if str(r["status"]) == "pending" else 0
        return {
            "id": int(r["id"]), "author": str(r["author_name"]), "org": str(r["org"]),
            "kind": kind, "urgency": int(r["urgency"]),
            "summary": str(r["summary"]), "full_text": str(r["full_text"]),
            "piaoni": str(r["piaoni"]), "piaoni_author": str(r["piaoni_author"]),
            "arrived_day": int(r["arrived_day"]), "shelved_days": int(r["shelved_days"]),
            "status": str(r["status"]), "ref_kind": str(r["ref_kind"]), "ref_id": str(r["ref_id"]),
            "days_to_expire": days_to_expire,
        }

    ra = kv_int(db, KV_RISK_AVERSION, RISK_AVERSION_DEFAULT)
    return {
        "pending": [_row(r) for r in rows],
        "recent_decided": [_row(r) for r in decided],
        "backlog": len(rows),
        "attention_left": attention_left(db),
        "attention_per_day": ATTENTION_PER_DAY,
        "shi": kv_int(db, KV_SHI, SHI_DEFAULT),
        "renshi_willingness": 100 - ra,
        "trap_hint": ("百官观望，事事请旨，御案将溢——惩罚失败者愈狠，担责者愈少。"
                      if ra >= 60 else ""),
    }
