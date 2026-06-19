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
    "ack": 0,  # 「已阅」结果通知（复命/捷报）：免精力，无后果
}

KV_LAST_ATTENTION_DAY = "upgrade.attention_day"

# 奏疏淹没：弹章/告变/密揭按「急」算（越压不得）。单一真源——
# 日 tick 淹没判定与御案倒计时倒数同此公式，禁止各自重算。
EXPIRE_FORCED_URGENT_KINDS = ("弹章", "告变", "密揭")

# 「结果通知」类奏报（诏书到期复命、捷报）：是既成结果而非待裁请求——
# 读阅免精力、到期静默归档、不计淹没问责，不像请求那样堵塞御案、压迫任事意愿。
INFORMATIONAL_KINDS = ("复命", "捷报")

# ── 淹没/积压对信念的月度伤害封顶 ─────────────────────────────────────────────
# 病根（实玩实证）：奏疏积压无界（数十封/月淹没），每封固定 +1 RA / 弹章淹没 -3 势，
# 几百次累加碾压恒稳态固定的 ±8 → RA 棘轮到 100、势触底到 0（恰是恒稳态明文要防的吸收态）。
# 修法：把「来自淹没/积压」的信念反向伤害按月封顶，使其不超过恒稳态量级——
# 奏而不答仍有代价（势/任事在低位均衡受压），但代价不再无限累积成吸收态。
# 上限 < 恒稳态月回拉(~8)，故重压之下变量稳定在「痛苦但可被拉回」的低位，而非单边崩到 0/100。
KV_MONTH_DROWN_RA = "upgrade.month_drown_ra"
KV_MONTH_DROWN_SHI = "upgrade.month_drown_shi"
DROWN_RA_CAP = 5     # 每月「淹没/积压」最多把 RA 推高 +5（恒稳态月回拉约 -8，故净回落）
DROWN_SHI_CAP = 5    # 每月「弹章淹没/留中」最多把 势 压低 -5（恒稳态月回升约 +8，故净回升）


def reset_drown_belief_caps(db: GameDB) -> None:
    """朔日重置月度淹没/积压伤害预算（由 timeflow.rollover 调用）。"""
    kv_set_int(db, KV_MONTH_DROWN_RA, 0)
    kv_set_int(db, KV_MONTH_DROWN_SHI, 0)


def _capped_drown_belief(db: GameDB, key: str, delta: int, reason: str, *,
                         day: int, cap_key: str, cap_limit: int) -> None:
    """对来自淹没/积压的信念伤害按月封顶；超出本月预算的部分不再施加。"""
    used = kv_int(db, cap_key, 0)
    room = cap_limit - used
    if room <= 0:
        return
    mag = min(abs(delta), room)
    applied = mag if delta > 0 else -mag
    adjust_belief(db, key, applied, reason, day=day)
    kv_set_int(db, cap_key, used + mag)


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
    body = full_text or compose_memorial_full_text(
        db,
        state,
        author_name=author_name,
        org=org,
        kind=kind,
        summary=summary,
        ref_kind=ref_kind,
        ref_id=ref_id,
    )
    cur = db.conn.execute(
        """INSERT INTO memorials (author_name, org, kind, urgency, summary, full_text,
           piaoni, piaoni_author, arrived_day, status, ref_kind, ref_id)
           VALUES (?,?,?,?,?,?,?,?,?,'pending',?,?)""",
        (author_name, org, kind, max(1, min(3, int(urgency))), summary[:120],
         body,
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
        "WHERE status='active' AND power_id='ming' AND office_type NOT IN ('后宫') AND courage>=? "
        "ORDER BY name", (int(min_courage),),
    ).fetchall()
    return rng.choice(rows) if rows else None


def _sentence(text: str) -> str:
    clean = str(text or "").strip()
    if not clean:
        return ""
    return clean if clean[-1] in "。！？；" else f"{clean}。"


def _issue_memorial_context(db: GameDB, ref_kind: str, ref_id: str) -> Dict[str, object]:
    if str(ref_kind or "") != "issue" or not str(ref_id or "").strip():
        return {}
    row = db.conn.execute(
        """
        SELECT title, kind, origin_kind, origin_ref, bar_value, severity, stage_text,
               bar_good_meaning, bar_bad_meaning, region_hint
        FROM issues WHERE id=?
        """,
        (str(ref_id),),
    ).fetchone()
    if row is None:
        return {}
    region_name = ""
    region_id = str(row["region_hint"] or "")
    if region_id:
        rr = db.conn.execute("SELECT name FROM regions WHERE id=?", (region_id,)).fetchone()
        if rr is not None:
            region_name = str(rr["name"] or "")
    result = {
        "title": str(row["title"] or ""),
        "kind": str(row["kind"] or ""),
        "origin_kind": str(row["origin_kind"] or ""),
        "origin_ref": str(row["origin_ref"] or ""),
        "bar": int(row["bar_value"] or 0),
        "severity": int(row["severity"] or 0),
        "stage": str(row["stage_text"] or ""),
        "good": str(row["bar_good_meaning"] or ""),
        "bad": str(row["bar_bad_meaning"] or ""),
        "region": region_name,
    }
    if str(row["origin_kind"] or "") == "doctrine" and str(row["origin_ref"] or ""):
        try:
            from ming_sim import policies
            doctrine = policies.doctrine_by_id(str(row["origin_ref"] or "")) or {}
            if doctrine:
                result.update({
                    "route_name": str(doctrine.get("name") or row["origin_ref"]),
                    "route_axis": str(doctrine.get("axis") or ""),
                    "route_summary": str(doctrine.get("summary") or ""),
                })
        except Exception:
            pass
    return result


def compose_memorial_full_text(
    db: GameDB,
    state: Optional[GameState],
    *,
    author_name: str,
    org: str,
    kind: str,
    summary: str,
    ref_kind: str = "",
    ref_id: str = "",
) -> str:
    """Deterministic fallback memorial body with actual case facts.

    LLM piaoni may arrive later, but the player's first read of a memorial should
    already contain more than a title and stock formula. Keep this rules-only so
    daily arrivals never block on model availability.
    """
    author = str(author_name or "").strip() or "臣"
    office = str(org or "").strip()
    issue = _issue_memorial_context(db, ref_kind, ref_id)
    title = str(issue.get("title") or summary or "所奏之事").strip()
    stage = str(issue.get("stage") or "").strip()
    region = str(issue.get("region") or "").strip()
    severity = int(issue.get("severity") or 0)
    progress = int(issue.get("bar") or 0)
    locus = f"{region}一带" if region else "地方"
    identity = f"{office}{author}" if office and author not in office else author
    if issue:
        if str(issue.get("origin_kind") or "") == "doctrine":
            route = str(issue.get("route_name") or title).strip()
            axis = str(issue.get("route_axis") or "").strip()
            route_summary = str(issue.get("route_summary") or stage or "").strip()
            axis_text = f"，所涉为{axis}" if axis else ""
            if kind == "弹章":
                return (
                    f"臣{identity}谨奏：近来朝议有欲以「{route}」为定策者{axis_text}。"
                    f"臣窃以为，{route_summary or '此事利害未明，牵动甚广'}。"
                    f"今议论已至{progress}/100，若不及早辨明名分、财用与人心所系，"
                    "恐一时趋利之说压倒祖制旧章，遂令朝政失其准绳。伏乞圣明留中详察，勿遽定为国是。"
                )
            if kind == "陈情":
                return (
                    f"臣{identity}谨陈「{route}」之议{axis_text}。"
                    f"臣所见，{route_summary or '此路有可行处，亦有可惧处'}。"
                    f"目下廷议约至{progress}/100，支持者欲成定策，反对者恐其失范。"
                    "臣不敢以空言争胜，谨将利害陈明，伏乞陛下择其可行者试之。"
                )
            return (
                f"臣{identity}谨奏：为「{route}」路线事。"
                f"{route_summary or '此议关乎国家长策，非一事一地之便宜'}。"
                f"今其成说约至{progress}/100，若得明旨为准，则部院承行不再各执一端；"
                "若仍悬而不决，则党论相持，政令难有归宿。伏乞圣明裁定，使国是有所宗。"
            )
        current = stage or f"{title}尚在变化，进度约{progress}/100，险情约{severity}/100"
        if kind == "请旨":
            return (
                f"臣{identity}谨奏：臣等查得，{title}一事牵动{locus}，"
                f"目下{current}。其间利害相杂，若专行一端，恐激成后患；若坐视不决，又恐事势滋蔓。"
                "臣职分有限，不敢擅专，伏乞圣明裁定方略，俾各衙门有所遵循。"
            )
        if kind == "陈情":
            return (
                f"臣{identity}谨将{title}情形奏闻：{current}。"
                f"臣所见，{locus}人心已受其扰，催科、兵饷、讼狱诸务皆有牵连。"
                "臣不敢饰词报喜，惟愿陛下洞察其实，早定缓急。"
            )
        if kind == "告变":
            return (
                f"臣{identity}谨急奏：{title}事机骤紧，{current}。"
                f"{locus}风声已动，若旬日之内不见朝廷处分，恐地方官吏各自观望。"
                "伏乞速赐睿断，毋使小患酿成大变。"
            )
        if kind == "请款":
            return (
                f"臣{identity}谨奏：为办理{title}，所需钱粮已非本衙门常费所能支应。"
                f"目下{current}。若无明拨款项，承办诸员必以经费无着为辞。"
                "伏乞敕下户部核拨，俾事有实济。"
            )
    if kind == "荐人":
        return (
            f"臣{identity}谨奏：臣闻任事以得人为先。今因{summary}，"
            "谨保举一员堪供驱策。其人虽未必无瑕，然才具尚可试用。"
            "伏乞圣明裁择，若蒙采纳，臣愿具结保奏。"
        )
    if kind == "弹章":
        return (
            f"臣{identity}谨奏：臣职司风闻，不敢以私怨乱公论。今据所闻所见，"
            f"{summary}，其中关节已有可核之迹。若任其迁延，恐上下相蒙。"
            "伏乞敕下该衙门查明，毋使奸蠹幸免，亦毋使无辜受累。"
        )
    if kind == "请款":
        return f"臣{identity}谨奏：{summary}。库藏虽艰，需用实急，伏乞酌拨钱粮，以济目前。"
    if kind == "告变":
        return f"臣{identity}谨急奏：{summary}。事机紧迫，臣不敢迟延，伏乞速赐处分。"
    if kind == "陈情":
        return f"臣{identity}谨奏：{summary}。臣所陈皆据目前情形，不敢粉饰，伏乞圣鉴。"
    return _KIND_TEMPLATES.get(kind, "臣谨据实奏闻，伏乞圣鉴。")


def memorials_daily_tick(db: GameDB, state: GameState, day: int) -> List[Dict[str, object]]:
    """每日：注意力刷新、奏疏生成（量随 RA 放大——崇祯陷阱的传导介质）、留中计数。"""
    events: List[Dict[str, object]] = []
    reset_attention_for_day(db, day)
    rng = random.Random(day * 7919 + state.turn)
    ra = kv_int(db, KV_RISK_AVERSION, RISK_AVERSION_DEFAULT)

    # 1) 请旨/陈情流：基准 0.45 封/日 × (1 + min(RA,70)/100)。RA 放大流入是「崇祯陷阱
    #    传导介质」（越问责越多人请旨观望），但封顶在 70 以掐断「流入→淹没→RA↑→流入」的
    #    正反馈失控环——配合月度伤害封顶，避免御案流入随 RA 棘轮无限膨胀。
    arrival_rate = 0.45 * (1.0 + min(ra, 70) / 100.0)
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

    events.extend(_doctrine_memorial_pulse(db, state, day, rng))

    # 2.5) 司礼监代批红（宦官恶趣味 E1）：若启用，内廷掌印先代廓清积压——解御案壅塞，
    #     但权阉日涨、阉党自固（劾阉之疏留中销折）。先于淹没结算，故被代批者不再走淹没扣势。
    try:
        from ming_sim.eunuch_power import daipihong_process
        events.extend(daipihong_process(db, state, day))
    except Exception:
        pass

    # 3) 留中积压：每 10 日结一次怨账；弹章留中折势。逾期则淹没出队（一次性结算），
    #    使待奏队列与「弹章留中折势」均有界——奏而不答是有代价的，但代价不无限累积。
    rows = db.conn.execute("SELECT * FROM memorials WHERE status='pending'").fetchall()
    for row in rows:
        shelved = int(day) - int(row["arrived_day"])
        deadline = expire_deadline_days(str(row["kind"]), int(row["urgency"] or 2))
        # 结果通知：到期静默归档，无问责/怨气/淹没代价（不是奏而不答，是看过即可）。
        if str(row["kind"]) in INFORMATIONAL_KINDS:
            if shelved >= deadline:
                db.conn.execute(
                    "UPDATE memorials SET status='expired', shelved_days=?, decided_day=? WHERE id=?",
                    (shelved, int(day), int(row["id"])))
            else:
                db.conn.execute("UPDATE memorials SET shelved_days=? WHERE id=?",
                                (shelved, int(row["id"])))
            continue
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
                _capped_drown_belief(db, KV_SHI, -3, f"弹章淹没不报（#{row['id']}）",
                                     day=day, cap_key=KV_MONTH_DROWN_SHI, cap_limit=DROWN_SHI_CAP)
                try:
                    from ming_sim.theater import adjust_faction_heat, faction_of
                    adjust_faction_heat(db, faction_of(db, author), +4, "弹章石沉大海")
                except Exception:
                    pass
            _capped_drown_belief(db, KV_RISK_AVERSION, +1, f"奏疏淹没（{author}{row['kind']}）",
                                 day=day, cap_key=KV_MONTH_DROWN_RA, cap_limit=DROWN_RA_CAP)
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
                _capped_drown_belief(db, KV_SHI, -2, f"弹章留中不发（#{row['id']}）",
                                     day=day, cap_key=KV_MONTH_DROWN_SHI, cap_limit=DROWN_SHI_CAP)
            if shelved == 30:
                _capped_drown_belief(db, KV_RISK_AVERSION, +1,
                                     f"奏疏积压逾月（{row['author_name']}{row['kind']}）",
                                     day=day, cap_key=KV_MONTH_DROWN_RA, cap_limit=DROWN_RA_CAP)
                events.append({"level": LEVEL_YELLOW, "kind": "memorial_overdue",
                               "title": f"奏疏积压逾月：{str(row['summary'])[:30]}",
                               "detail": "奏而不答，臣下渐生观望。",
                               "ref_kind": "memorial", "ref_id": str(row["id"]), "day": day})
    db.conn.commit()
    return events


def _weighted_pick(rows: List[Dict[str, object]], rng: random.Random) -> Optional[Dict[str, object]]:
    if not rows:
        return None
    total = sum(max(1, int(item.get("weight") or 1)) for item in rows)
    mark = rng.random() * total
    seen = 0.0
    for item in rows:
        seen += max(1, int(item.get("weight") or 1))
        if seen >= mark:
            return item
    return rows[-1]


def _doctrine_author_candidates(
    db: GameDB,
    doctrine_id: str,
    direction: str,
) -> List[Dict[str, object]]:
    try:
        from ming_sim import policies
    except Exception:
        return []
    want_support = str(direction) != "oppose"
    rows = db.conn.execute(
        "SELECT name, office, faction, courage, ability FROM characters "
        "WHERE status='active' AND power_id='ming' AND office_type!='后宫' "
        "ORDER BY ability DESC LIMIT 80"
    ).fetchall()
    picked: List[Dict[str, object]] = []
    for row in rows:
        name = str(row["name"] or "")
        if not name:
            continue
        stance = policies.character_doctrine_stance(db, name, doctrine_id)
        score = float(stance.get("score") or 0.0)
        if want_support and score < 0.25:
            continue
        if not want_support and score > -0.25:
            continue
        courage = int(row["courage"] or 50)
        ability = int(row["ability"] or 50)
        picked.append({
            "name": name,
            "office": str(row["office"] or ""),
            "faction": str(row["faction"] or ""),
            "stance": stance,
            "weight": max(1, int(abs(score) * 100) + courage // 4 + ability // 10),
        })
    return picked


def _doctrine_memorial_pulse(
    db: GameDB,
    state: GameState,
    day: int,
    rng: random.Random,
    *,
    force: bool = False,
) -> List[Dict[str, object]]:
    """Occasionally turn active doctrine disputes into ordinary memorials.

    This is the route-politics bridge: ministers argue for their governing
    ideals through the existing desk flow, not through a new proposal table.
    """

    try:
        from ming_sim import policies
    except Exception:
        return []
    issues = db.conn.execute(
        "SELECT id, title, origin_ref, bar_value, severity FROM issues "
        "WHERE status='active' AND origin_kind='doctrine' "
        "ORDER BY severity DESC, updated_at DESC, id DESC LIMIT 6"
    ).fetchall()
    eligible = []
    for issue in issues:
        recent = db.conn.execute(
            "SELECT 1 FROM memorials WHERE ref_kind='issue' AND ref_id=? "
            "AND status IN ('pending','shelved') LIMIT 1",
            (str(issue["id"]),),
        ).fetchone()
        if recent is not None:
            continue
        cooldown = db.conn.execute(
            "SELECT 1 FROM memorials WHERE ref_kind='issue' AND ref_id=? "
            "AND arrived_day>=? LIMIT 1",
            (str(issue["id"]), int(day) - 18),
        ).fetchone()
        if cooldown is not None:
            continue
        doctrine_id = str(issue["origin_ref"] or "")
        doctrine = policies.doctrine_by_id(doctrine_id)
        if doctrine:
            eligible.append((issue, doctrine))
    if not eligible:
        return []

    chance = min(0.30, 0.12 + 0.04 * len(eligible))
    if not force and rng.random() >= chance:
        return []

    issue, doctrine = rng.choice(eligible)
    bar = int(issue["bar_value"] or 0)
    support_pressure = max(0.25, (70 - bar) / 100.0)
    oppose_pressure = max(0.20, (bar - 35) / 100.0)
    direction = "oppose" if rng.random() < oppose_pressure / (support_pressure + oppose_pressure) else "support"
    candidates = _doctrine_author_candidates(db, str(doctrine["id"]), direction)
    if not candidates:
        direction = "support" if direction == "oppose" else "oppose"
        candidates = _doctrine_author_candidates(db, str(doctrine["id"]), direction)
    author = _weighted_pick(candidates, rng)
    if not author:
        return []

    doctrine_name = str(doctrine.get("name") or issue["title"] or doctrine["id"])
    axis = str(doctrine.get("axis") or "")
    if direction == "oppose":
        kind = "弹章"
        urgency = 2 if bar < 75 else 3
        summary = f"弹驳「{doctrine_name}」路线未可遽定"
        detail = f"{author['name']}上弹章阻滞「{doctrine_name}」，路线斗争浮上御案。"
    else:
        kind = "请旨" if rng.random() < 0.65 else "陈情"
        urgency = 2
        summary = f"请定「{doctrine_name}」路线"
        detail = f"{author['name']}上疏推动「{doctrine_name}」，国策争议有了具体奏请。"
    if axis:
        summary = f"{summary}（{axis}）"
    mid = create_memorial(
        db,
        state,
        day=day,
        author_name=str(author["name"]),
        org=str(author["office"]),
        kind=kind,
        urgency=urgency,
        summary=summary,
        ref_kind="issue",
        ref_id=str(issue["id"]),
    )
    return [{
        "level": LEVEL_YELLOW if direction == "oppose" else LEVEL_BLUE,
        "kind": "memorial",
        "title": f"路线奏疏：{summary}",
        "detail": detail,
        "ref_kind": "memorial",
        "ref_id": str(mid),
        "day": int(day),
    }]


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
    if action == "ack":
        # 已阅结果通知：免精力、无后果，仅归档。
        db.conn.execute(
            "UPDATE memorials SET status='approved', decided_day=? WHERE id=?", (int(day), mid))
        db.conn.commit()
        return {"ok": True, "message": "已阅。", "attention_left": attention_left(db)}
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
        # 发部议 → 自动生成旨意草案，走正常拟诏/生命周期流程。
        # 皇帝批语（note）作为上谕写入旨意，令内阁/司礼监照圣意落实。
        batch = (note or "").strip()
        body = f"上谕：{_sentence(batch)}" if batch else _sentence(str(row["full_text"] or "")[:60])
        draft = f"下部议：{str(row['summary'])}。{body}着该衙门遵旨议奏施行。"
        db.conn.execute(
            "INSERT INTO turn_directives (turn, year, period, text, source, status, notes)"
            " VALUES (?,?,?,?,?,?,?)",
            (state.turn, state.year, state.period, draft, "memorial_refer", "draft",
             f"发部议自奏疏#{mid}"))
        message = "发该部议奏。已生成旨意草案，颁诏时一并下达。"
    doctrine_effect: Dict[str, object] = {}
    if action in ("approve", "deny"):
        try:
            from ming_sim import policies
            doctrine_effect = policies.apply_memorial_doctrine_effect(db, state, row, action)
        except Exception:
            doctrine_effect = {}
    db.conn.commit()
    payload = {"ok": True, "message": message, "attention_left": attention_left(db)}
    if doctrine_effect:
        payload["doctrine_effect"] = doctrine_effect
    return payload


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
                 "faction_sat": +4, "faction_heat": -6, "rival_sat": -2, "rival_heat": +3,
                 "note": "上谕引咎：『此朕之过，非该臣之罪。』"},
    "comfort":  {"ra": -5, "shi": 0, "label": "抚恤褒奖", "trust": +10,
                 "faction_sat": +2, "faction_heat": -3, "rival_sat": -1, "rival_heat": +1,
                 "note": "赐金抚恤，荫其子弟。"},
    "reuse":    {"ra": -10, "shi": -2, "label": "败后复用", "trust": +20,
                 "faction_sat": +5, "faction_heat": -5, "rival_sat": -3, "rival_heat": +4,
                 "note": "败军之将弃而复用，朝野侧目，然任事者知上不弃人。"},
}


def _back_faction_effects(db: GameDB, name: str, spec: Dict[str, object]) -> List[Dict[str, str]]:
    try:
        from ming_sim.theater import adjust_faction_heat, faction_of
        from ming_sim.political_reactions import rival_faction
        faction = faction_of(db, name)
    except Exception:
        return []
    if not faction or faction in ("无", "中立"):
        return []

    effects: List[Dict[str, str]] = []
    sat_delta = int(spec.get("faction_sat") or 0)
    heat_delta = int(spec.get("faction_heat") or 0)
    if sat_delta:
        db.adjust_factions({faction: {"satisfaction": sat_delta}})
        effects.append({"kind": "faction", "label": f"{faction}满意 {sat_delta:+d}", "tone": "good"})
    if heat_delta:
        adjust_faction_heat(db, faction, heat_delta, f"{spec['label']}{name}")
        effects.append({"kind": "faction", "label": f"{faction}热度 {heat_delta:+d}", "tone": "good"})
    rival = rival_faction(db, faction)
    if rival:
        rival_sat = int(spec.get("rival_sat") or 0)
        rival_heat = int(spec.get("rival_heat") or 0)
        if rival_sat:
            db.adjust_factions({rival: {"satisfaction": rival_sat}})
            effects.append({"kind": "faction", "label": f"{rival}满意 {rival_sat:+d}", "tone": "bad"})
        if rival_heat:
            adjust_faction_heat(db, rival, rival_heat, f"{spec['label']}{name}（敌派受激）")
            effects.append({"kind": "faction", "label": f"{rival}热度 {rival_heat:+d}", "tone": "bad"})
    return effects


def _back_faction_preview(db: GameDB, name: str, spec: Dict[str, object]) -> List[Dict[str, str]]:
    try:
        from ming_sim.theater import faction_of
        from ming_sim.political_reactions import rival_faction
        faction = faction_of(db, name)
    except Exception:
        return []
    if not faction or faction in ("无", "中立"):
        return []

    effects: List[Dict[str, str]] = []
    sat_delta = int(spec.get("faction_sat") or 0)
    heat_delta = int(spec.get("faction_heat") or 0)
    if sat_delta:
        effects.append({"kind": "faction", "label": f"{faction}满意 {sat_delta:+d}", "tone": "good"})
    if heat_delta:
        effects.append({"kind": "faction", "label": f"{faction}热度 {heat_delta:+d}", "tone": "good"})
    rival = rival_faction(db, faction)
    if rival:
        rival_sat = int(spec.get("rival_sat") or 0)
        rival_heat = int(spec.get("rival_heat") or 0)
        if rival_sat:
            effects.append({"kind": "faction", "label": f"{rival}满意 {rival_sat:+d}", "tone": "bad"})
        if rival_heat:
            effects.append({"kind": "faction", "label": f"{rival}热度 {rival_heat:+d}", "tone": "bad"})
    return effects


def _back_network_preview(db: GameDB, name: str) -> List[Dict[str, str]]:
    try:
        from ming_sim import court
        allies = court.allies_of(db, name, limit=5)
        rivals = court.rivals_of(db, name, limit=5)
    except Exception:
        return []
    effects: List[Dict[str, str]] = []
    if allies:
        effects.append({"kind": "network", "label": f"党羽受慰 {len(allies)}人", "tone": "good"})
    if rivals:
        effects.append({"kind": "network", "label": f"政敌侧目 {len(rivals)}人", "tone": "bad"})
    return effects


def _back_network_effects(db: GameDB, name: str, day: int) -> List[Dict[str, str]]:
    try:
        from ming_sim import court
        touched = court.ripple_personnel(db, name, "shield", day=day)
    except Exception:
        return []
    effects: List[Dict[str, str]] = []
    allies = list(touched.get("allies") or []) if isinstance(touched, dict) else []
    rivals = list(touched.get("rivals") or []) if isinstance(touched, dict) else []
    if allies:
        effects.append({"kind": "network", "label": f"党羽受慰 {len(allies)}人", "tone": "good"})
    if rivals:
        effects.append({"kind": "network", "label": f"政敌侧目 {len(rivals)}人", "tone": "bad"})
    return effects


def _record_back_favor_memory(
    db: GameDB,
    state: GameState,
    name: str,
    kind: str,
    spec: Dict[str, object],
    *,
    day: int,
) -> int:
    row = db.conn.execute(
        "SELECT office, faction, status FROM characters WHERE name=?",
        (name,),
    ).fetchone()
    office = str(row["office"] or "") if row else ""
    faction = str(row["faction"] or "") if row else ""
    label = str(spec.get("label") or kind)
    note = str(spec.get("note") or "")
    title = f"旧恩未报：{name}"
    cause = f"陛下以「{label}」为{name}买单，{office}{name}由此受恩。"
    process = note or "皇帝没有只按失败问罪，而是替其留任事余地。"
    outcome = "此后召对须记得旧恩；若陛下托以差事，不宜装作两清。"
    tags = [name, "旧恩", "人情债", "任事", label]
    if faction:
        tags.append(faction)
    source_id = f"back:{int(state.turn)}:{int(day)}:{name}:{kind}"
    memory_id = db.upsert_event_memory(
        state,
        subject_type="character",
        subject_id=name,
        event_type="imperial_favor",
        title=title,
        cause=cause,
        process=process,
        outcome=outcome,
        sentiment="positive",
        importance=4,
        tags=tags,
        source_kind="court_back",
        source_id=source_id,
        expires_turn=int(state.turn) + 36,
    )
    if memory_id:
        db.add_event_memory_source(
            memory_id,
            "court_back",
            source_id,
            excerpt=f"{label}{name}：{outcome}",
            locator={"day": int(day), "turn": int(state.turn), "kind": kind},
        )
    return memory_id


def preview_back_official_effects(db: GameDB, name: str, kind: str, *, cost: int = 0) -> List[Dict[str, str]]:
    """Read-only player-facing preview for backing an official."""
    spec = _BACK_KINDS.get(kind)
    if spec is None:
        return []
    effects = [
        {"kind": "belief", "label": f"任事 +{abs(int(spec['ra']))}", "tone": "good"},
    ]
    if spec["shi"]:
        effects.append({"kind": "belief", "label": f"势 {int(spec['shi']):+d}", "tone": "bad"})
    if cost > 0:
        effects.append({"kind": "treasury", "label": f"内库 -{int(cost)}", "tone": "bad"})
    effects.append({"kind": "court", "label": f"{name}信任 +{int(spec['trust'])}", "tone": "good"})
    effects.extend(_back_faction_preview(db, name, spec))
    effects.extend(_back_network_preview(db, name))
    if kind == "reuse":
        effects.append({"kind": "court", "label": "复归在朝", "tone": "good"})
    return effects


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
    effects = [
        {"kind": "belief", "label": f"任事 +{abs(int(spec['ra']))}", "tone": "good"},
    ]
    adjust_belief(db, KV_RISK_AVERSION, spec["ra"], f"{spec['label']}{name}", day=day)
    if spec["shi"]:
        adjust_belief(db, KV_SHI, spec["shi"], f"{spec['label']}{name}（示弱）", day=day)
        effects.append({"kind": "belief", "label": f"势 {int(spec['shi']):+d}", "tone": "bad"})
    if cost > 0:
        effects.append({"kind": "treasury", "label": f"内库 -{int(cost)}", "tone": "bad"})
    db.conn.execute(
        "UPDATE characters SET grievance=MAX(0, grievance-15), emp_trust=MIN(100, emp_trust+?) WHERE name=?",
        (int(spec["trust"]), name))
    effects.append({"kind": "court", "label": f"{name}信任 +{int(spec['trust'])}", "tone": "good"})
    effects.extend(_back_faction_effects(db, name, spec))
    effects.extend(_back_network_effects(db, name, day))
    if kind == "reuse" and str(row["status"]) in ("imprisoned", "dismissed"):
        db.conn.execute(
            "UPDATE characters SET status='active', status_reason='败后复用', status_changed_turn=? WHERE name=?",
            (state.turn, name))
        effects.append({"kind": "court", "label": "复归在朝", "tone": "good"})
    if _record_back_favor_memory(db, state, name, kind, spec, day=day):
        effects.append({"kind": "memory", "label": "旧恩入账", "tone": "good"})
    db.record_log(state, f"【买单】{spec['label']}{name}。{spec['note']}")
    db.conn.commit()
    db.save_state(state)
    return {
        "ok": True,
        "message": f"{spec['label']}{name}。{spec['note']}（任事意愿回暖{abs(spec['ra'])}）",
        "effects": effects,
    }


# ── 查询 ─────────────────────────────────────────────────────────────────────

def _preview_effect(label: str, tone: str = "neutral", kind: str = "belief") -> Dict[str, str]:
    return {"kind": kind, "label": label, "tone": tone}


def _memorial_action_effects(
    row,
    days_to_expire: int,
    policy_doctrine: Optional[Dict[str, object]] = None,
) -> Dict[str, List[Dict[str, str]]]:
    """Predict player-facing consequences for desk actions.

    The actual state changes still live in decide_memorial; this only makes the
    known rule-layer tradeoffs visible before the player clicks.
    """

    kind = str(row["kind"])
    if kind in INFORMATIONAL_KINDS:
        return {"ack": [_preview_effect("已阅归档"), _preview_effect("精力 0", "good", "attention")]}

    approve = [_preview_effect("精力 -1", "bad", "attention")]
    refer = [_preview_effect("精力 -1", "bad", "attention"), _preview_effect("生成旨意草案", "good", "directive")]
    deny = [_preview_effect("精力 -1", "bad", "attention")]
    shelve = [_preview_effect("精力 0", "good", "attention")]

    if kind in ("告变", "陈情"):
        approve.append(_preview_effect("任事 +1", "good"))
    if kind == "告变":
        deny.append(_preview_effect("任事 -2", "bad"))
    elif kind == "弹章":
        if str(row["ref_kind"]) == "character" and str(row["ref_id"]):
            approve.append(_preview_effect("任事 -2", "bad"))
            approve.append(_preview_effect("牵动党争", "bad", "faction"))
        deny.append(_preview_effect("劾党生怨", "bad", "faction"))
        shelve.append(_preview_effect("久压折势", "bad"))
    elif kind == "荐人" and str(row["ref_kind"]) == "foundation_npc" and str(row["ref_id"]):
        approve.append(_preview_effect("征入朝", "good", "court"))
    elif kind == "请款":
        refer.append(_preview_effect("交部核拨", "good", "directive"))

    if policy_doctrine:
        route_delta = 12
        try:
            from ming_sim import policies
            route_delta = int((policies.load_policy_doctrines() or {}).get("memorial_issue_delta") or 12)
        except Exception:
            route_delta = 12
        reverse_delta = max(4, route_delta // 2)
        direction = str(policy_doctrine.get("direction") or "")
        if direction == "oppose":
            approve.append(_preview_effect(f"路线 -{route_delta}", "bad", "doctrine"))
            deny.append(_preview_effect(f"路线 +{reverse_delta}", "good", "doctrine"))
        else:
            approve.append(_preview_effect(f"路线 +{route_delta}", "good", "doctrine"))
            deny.append(_preview_effect(f"路线 -{reverse_delta}", "bad", "doctrine"))
        refer.append(_preview_effect("可转成旨意路线", "good", "doctrine"))

    if kind not in ("告变", "弹章"):
        deny.append(_preview_effect("上疏人怨", "bad", "court"))
    if days_to_expire > 0 and days_to_expire <= 7:
        shelve.append(_preview_effect("临期淹没", "bad"))
    else:
        shelve.append(_preview_effect("久压增观望", "bad"))

    return {"approve": approve, "refer": refer, "deny": deny, "shelve": shelve}


def _memorial_policy_doctrine(db: GameDB, row) -> Dict[str, object]:
    if str(row["ref_kind"] or "") != "issue" or not str(row["ref_id"] or "").strip():
        return {}
    issue = db.conn.execute(
        "SELECT id, title, origin_kind, origin_ref, bar_value, status FROM issues WHERE id=?",
        (str(row["ref_id"] or ""),),
    ).fetchone()
    if issue is None or str(issue["origin_kind"] or "") != "doctrine":
        return {}
    try:
        from ming_sim import policies
        doctrine_id = str(issue["origin_ref"] or "")
        doctrine = policies.doctrine_by_id(doctrine_id) or {}
        author = str(row["author_name"] or "")
        stance = policies.character_doctrine_stance(db, author, doctrine_id) if author else {}
    except Exception:
        return {}
    kind = str(row["kind"] or "")
    direction = "oppose" if kind == "弹章" else "support"
    return {
        "id": str(doctrine.get("id") or doctrine_id),
        "name": str(doctrine.get("name") or issue["title"] or doctrine_id),
        "axis": str(doctrine.get("axis") or ""),
        "issue_id": int(issue["id"]),
        "bar_value": int(issue["bar_value"] or 0),
        "direction": direction,
        "direction_label": "反对此路线" if direction == "oppose" else "推动此路线",
        "author_stance": stance,
    }


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
        policy_doctrine = _memorial_policy_doctrine(db, r)
        return {
            "id": int(r["id"]), "author": str(r["author_name"]), "org": str(r["org"]),
            "kind": kind, "urgency": int(r["urgency"]),
            "summary": str(r["summary"]), "full_text": str(r["full_text"]),
            "piaoni": str(r["piaoni"]), "piaoni_author": str(r["piaoni_author"]),
            "arrived_day": int(r["arrived_day"]), "shelved_days": int(r["shelved_days"]),
            "status": str(r["status"]), "ref_kind": str(r["ref_kind"]), "ref_id": str(r["ref_id"]),
            "days_to_expire": days_to_expire,
            "action_effects": _memorial_action_effects(r, days_to_expire, policy_doctrine),
            "policy_doctrine": policy_doctrine,
        }

    ra = kv_int(db, KV_RISK_AVERSION, RISK_AVERSION_DEFAULT)
    # backlog 只计「待裁请求」压力；结果通知（复命/捷报）另计 info_count，不压迫任事意愿。
    request_backlog = sum(1 for r in rows if str(r["kind"]) not in INFORMATIONAL_KINDS)
    info_count = sum(1 for r in rows if str(r["kind"]) in INFORMATIONAL_KINDS)
    return {
        "pending": [_row(r) for r in rows],
        "recent_decided": [_row(r) for r in decided],
        "backlog": request_backlog,
        "info_count": info_count,
        "attention_left": attention_left(db),
        "attention_per_day": ATTENTION_PER_DAY,
        "shi": kv_int(db, KV_SHI, SHI_DEFAULT),
        "renshi_willingness": 100 - ra,
        "eunuch_power": _eunuch_power_safe(db),
        "daipihong": _daipihong_safe(db),
        **_daipihong_keeper_safe(db),
        "trap_hint": ("百官观望，事事请旨，御案将溢——惩罚失败者愈狠，担责者愈少。"
                      if ra >= 60 else ""),
    }


def _eunuch_power_safe(db: GameDB) -> int:
    try:
        from ming_sim.eunuch_power import get_eunuch_power
        return get_eunuch_power(db)
    except Exception:
        return 0


def _daipihong_safe(db: GameDB) -> bool:
    try:
        from ming_sim.eunuch_power import is_daipihong_on
        return is_daipihong_on(db)
    except Exception:
        return False


def _daipihong_keeper_safe(db: GameDB) -> dict:
    """代批红委任者名 + 是否忠谨（前端 DaipihongBar 显委任者与忠谨/需警惕标）。"""
    try:
        from ming_sim.eunuch_power import daipihong_keeper, keeper_disposition
        keeper = daipihong_keeper(db)
        return {"daipihong_keeper": keeper,
                "daipihong_keeper_upright": keeper_disposition(db, keeper) == "upright"}
    except Exception:
        return {"daipihong_keeper": None, "daipihong_keeper_upright": False}
