"""指令生命周期（S2）：让「迟滞的官僚体系」可见、可博弈。L7。

每道颁出的旨意成为有状态的过程：
    issued → in_transit(送达) → executing(旬检定) → done / stalled / aborted
- 工期 = 基础工期(directive_categories.json) × 官员能力修正 × 距离修正 × 阻力修正
- 每旬执行检定（确定性种子随机，可复盘）：顺利/拖延/截留/封驳/意外
- 截留走账实分离（S3）：integrity_actual 降而 integrity_reported 照报，
  完成时落 report_ledger，等密查/盘库揭穿
- 玩家中途干预：催办/换人/加拨/收回成命（各有代价，更新 势/RA/怨气）

全规则层零 LLM；异常的叙事文本由 scheduler 的 LLM 任务补写（失败有模板兜底）。
"""

from __future__ import annotations

import json
import random
import re
from typing import Dict, List, Optional

from ming_sim.db import GameDB
from ming_sim.models import GameState
from ming_sim.paths import bundled_path
from ming_sim.timeflow import LEVEL_BLUE, LEVEL_RED, LEVEL_YELLOW
from ming_sim.upgrade_schema import (
    KV_CURRENT_DAY,
    KV_RISK_AVERSION,
    KV_SHI,
    RISK_AVERSION_DEFAULT,
    SHI_DEFAULT,
    adjust_belief,
    kv_int,
)

_CATS_CACHE: Optional[Dict[str, object]] = None
_CONSEQ_CACHE: Optional[Dict[str, object]] = None

LIVE_STATUSES = ("in_transit", "executing", "stalled")


def load_categories() -> Dict[str, object]:
    global _CATS_CACHE
    if _CATS_CACHE is None:
        with open(bundled_path("content", "directive_categories.json"), encoding="utf-8") as fh:
            _CATS_CACHE = json.load(fh)
    return _CATS_CACHE


def load_consequences() -> Dict[str, object]:
    global _CONSEQ_CACHE
    if _CONSEQ_CACHE is None:
        try:
            with open(bundled_path("content", "causal_consequences.json"), encoding="utf-8") as fh:
                _CONSEQ_CACHE = json.load(fh)
        except (OSError, ValueError):
            _CONSEQ_CACHE = {"consequences": []}
    return _CONSEQ_CACHE


def classify_directive(text: str) -> Dict[str, object]:
    """关键词归类。多类命中取命中数最多者；并列取表序靠前。"""
    cfg = load_categories()
    best, best_hits = None, 0
    for cat in cfg.get("categories") or []:
        hits = sum(1 for kw in (cat.get("keywords") or []) if kw and kw in text)
        if hits > best_hits:
            best, best_hits = cat, hits
    if best is None:
        default_id = str(cfg.get("default_category") or "misc")
        best = next((c for c in cfg["categories"] if c["id"] == default_id), cfg["categories"][-1])
    return best


# 类别 → 主办衙门关键词（assignee 兜底查询用）
_CATEGORY_OFFICE = {
    "fiscal_allocation": "户部", "tax_reform": "户部", "relief": "户部",
    "personnel": "吏部", "military_ops": "兵部", "audit_purge": "都察院",
    "construction": "工部", "diplomacy": "礼部", "ritual_signal": "礼部",
    "secret_investigation": "锦衣卫", "misc": "内阁",
}

_CN_NUM = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
           "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNIT = {"十": 10, "百": 100}


def _cn_number_to_int(raw: str) -> int:
    text = str(raw or "").strip()
    if not text:
        return 0
    if text.isdigit():
        return int(text)
    if text == "廿":
        return 20
    if text == "卅":
        return 30
    total = 0
    number = 0
    for ch in text:
        if ch.isdigit():
            number = number * 10 + int(ch)
        elif ch in _CN_NUM:
            number = _CN_NUM[ch]
        elif ch in _CN_UNIT:
            total += (number or 1) * _CN_UNIT[ch]
            number = 0
    return total + number


def explicit_deadline_days(text: str) -> int:
    """Parse clear decree deadlines such as "三日内" or "限五日内".

    Returns 0 when no explicit day deadline is present. The lifecycle treats this
    as a maximum duration from issue day, not as a reason to slow down naturally
    faster directives.
    """
    decree = str(text or "")
    patterns = [
        rf"(?:限|限期|须于|務於|务于|于|於|在)?\s*([零〇一二两三四五六七八九十百廿卅\d]{{1,6}})\s*(?:日|天)\s*(?:内|以内|之内|为限)",
        rf"(?:限|限期)\s*([零〇一二两三四五六七八九十百廿卅\d]{{1,6}})\s*(?:日|天)",
    ]
    found: List[int] = []
    for pattern in patterns:
        for match in re.finditer(pattern, decree):
            days = _cn_number_to_int(match.group(1))
            if 0 < days <= 120:
                found.append(days)
    return min(found) if found else 0


def _category_by_id(cfg: Dict[str, object], category_id: str, default: Dict[str, object]) -> Dict[str, object]:
    for cat in cfg.get("categories") or []:
        if str(cat.get("id") or "") == category_id:
            return cat
    return default


def _court_immediate_profile(text: str, region_id: str) -> Dict[str, object]:
    """Timing override for capital/inner-court orders that should not wait on courier flow."""

    decree = str(text or "")
    if region_id != "beizhili":
        return {}
    profiles = [
        (
            r"净身|宫刑|净军房|入内廷|入宫为宦|发入内廷",
            "personnel",
            "宫禁身份处置：成命即转内廷执行，传旨为 0 日，承办以 1 日复命。",
        ),
        (
            r"廷杖|杖责|收监|下狱|逮问|拿问",
            "personnel",
            "京师人身处置：不走跨省公文送达，成命后直接交承办链复命。",
        ),
        (
            r"赐死|处死|处斩|斩立决|弃市",
            "personnel",
            "京师刑罚处置：执行很快，后续风险体现在复命、追问和政治反弹。",
        ),
    ]
    for pattern, category_id, note in profiles:
        if re.search(pattern, decree):
            return {
                "timing_profile": "court_immediate",
                "category": category_id,
                "lead_days": 0,
                "exec_days": 1,
                "distance": 1.0,
                "resistance_cap": 35,
                "check_risk_delta": {"delay": -15, "skim": -20, "block": -8},
                "note": note,
            }
    return {}


def _detect_region(db: GameDB, text: str) -> str:
    rows = db.conn.execute("SELECT id, name FROM regions").fetchall()
    for row in rows:
        if str(row["name"]) and str(row["name"]) in text:
            return str(row["id"])
    return "beizhili"


def _pick_assignee(db: GameDB, text: str, actor: str, category_id: str) -> str:
    """主办官员：旨意 actor 优先；否则按类别找对口衙门主官；再兜底内阁。"""
    if actor:
        row = db.conn.execute(
            "SELECT name FROM characters WHERE name=? AND status='active'", (actor,)
        ).fetchone()
        if row:
            return str(row["name"])
    # 旨意正文里点名的在朝官员
    rows = db.conn.execute(
        "SELECT name FROM characters WHERE status='active' AND office_type!='后宫'"
    ).fetchall()
    for row in rows:
        if str(row["name"]) in text:
            return str(row["name"])
    office_kw = _CATEGORY_OFFICE.get(category_id, "内阁")
    row = db.conn.execute(
        "SELECT name FROM characters WHERE status='active' AND office LIKE ? "
        "ORDER BY ability DESC LIMIT 1",
        (f"%{office_kw}%",),
    ).fetchone()
    if row:
        return str(row["name"])
    row = db.conn.execute(
        "SELECT name FROM characters WHERE status='active' AND office LIKE '%阁%' "
        "ORDER BY ability DESC LIMIT 1"
    ).fetchone()
    return str(row["name"]) if row else ""


def _resistance(db: GameDB, category: Dict[str, object], region_id: str) -> int:
    """经手阻力 0-100：类别伤害的阶级在目标地区/全国的抵抗。"""
    harms = (category.get("harms") or {}).get("classes") or []
    res = 0
    row = db.conn.execute(
        "SELECT gentry_resistance, unrest, json_extract(fiscal,'$.corruption') AS corruption "
        "FROM regions WHERE id=?", (region_id,),
    ).fetchone()
    gentry = int(row["gentry_resistance"]) if row else 40
    corruption = int(row["corruption"] or 50) if row else 50
    unrest = int(row["unrest"]) if row else 30
    if "士绅" in harms:
        res += round(gentry * 0.45)
    if "官僚" in harms:
        res += round(corruption * 0.35)
    if "商人" in harms:
        res += 10
    if "军户" in harms or "匠户" in harms:
        res += round(unrest * 0.15)
    return max(0, min(100, res))


def _char_row(db: GameDB, name: str):
    if not name:
        return None
    return db.conn.execute(
        "SELECT name, office, faction, loyalty, ability, integrity, grievance FROM characters WHERE name=?",
        (name,),
    ).fetchone()


def build_chain(db: GameDB, state: GameState, text: str, actor: str) -> Dict[str, object]:
    """归类 + 主办 + 工期 + 阻力。颁诏界面同口径展示（玩家可见的承诺）。"""
    cfg = load_categories()
    category = classify_directive(text)
    region_id = _detect_region(db, text)
    timing_profile = _court_immediate_profile(text, region_id)
    if timing_profile.get("category"):
        category = _category_by_id(cfg, str(timing_profile.get("category") or ""), category)
    assignee = _pick_assignee(db, text, actor, str(category["id"]))
    arow = _char_row(db, assignee)
    ability = int(arow["ability"]) if arow else 50
    # NPC 数据基座深接：才总折百替代游戏 ability；擅/痼特质给检定修正与工期因子
    foundation_mods = {"score": 0, "exec_factor": 1.0, "anomaly_bias": {}, "resistance": 0, "notes": []}
    try:
        from ming_sim import foundation
        fab = foundation.ability100(assignee)
        if fab is not None:
            ability = fab
        foundation_mods = foundation.directive_modifiers(assignee, str(category["id"]))
    except Exception:
        pass
    distance = float((cfg.get("distance_factors") or {}).get(
        region_id, (cfg.get("distance_factors") or {}).get("default", 1.4)))
    if timing_profile:
        distance = float(timing_profile.get("distance") or 1.0)
    resistance = _resistance(db, category, region_id) + int(foundation_mods.get("resistance") or 0)
    resistance = max(0, min(100, resistance))
    chain = [
        {"role": "主办", "name": assignee, "office": str(arow["office"]) if arow else "",
         "faction": str(arow["faction"]) if arow else ""},
        {"role": "承行", "name": "", "office": _CATEGORY_OFFICE.get(str(category["id"]), "内阁")},
        {"role": "地方", "name": "", "office": region_id},
    ]
    # 异常权重合并基座特质偏置（贪墨→截留+、优柔→拖延+、刚愎→封驳+…），下限 0
    check_risk = dict(category.get("check_risk") or {})
    for kind, delta in (foundation_mods.get("anomaly_bias") or {}).items():
        if kind in ("delay", "skim", "block", "surprise"):
            check_risk[kind] = max(0, int(check_risk.get(kind) or 0) + int(delta))
    policy_review: Dict[str, object] = {}
    try:
        from ming_sim import policies
        policy_review = policies.directive_doctrine_review(
            db, state, text, category_id=str(category["id"]), actor=assignee or actor
        )
    except Exception:
        policy_review = {}
    trait_notes = list(foundation_mods.get("notes") or [])
    policy_gate = policy_review.get("execution_gate") if isinstance(policy_review.get("execution_gate"), dict) else {}
    resistance_delta = int(policy_gate.get("resistance_delta") or 0)
    if resistance_delta:
        resistance = max(0, min(100, resistance + resistance_delta))
    for kind, delta in (policy_gate.get("check_risk_delta") or {}).items():
        if kind in ("delay", "skim", "block", "surprise"):
            check_risk[kind] = max(0, int(check_risk.get(kind) or 0) + int(delta))
    for note in policy_gate.get("notes") or []:
        trait_notes.append(f"国策：{note}")
    statecraft_effect: Dict[str, object] = {}
    statecraft_exec_factor = 1.0
    statecraft_score_bonus = 0
    if not timing_profile:
        try:
            from ming_sim.bureaucracy import organization_diagnostics
            from ming_sim.fiscal_center import fiscal_center_payload
            from ming_sim.statecraft_center import (
                directive_statecraft_execution_effect,
                statecraft_center_payload,
            )
            fiscal = fiscal_center_payload(db, state)
            organization = organization_diagnostics(db)
            statecraft = statecraft_center_payload(db, state, fiscal=fiscal, organization=organization)
            statecraft_effect = directive_statecraft_execution_effect(text, statecraft)
            statecraft_exec_factor = max(0.75, min(1.60, float(statecraft_effect.get("exec_factor") or 1.0)))
            statecraft_score_bonus = int(statecraft_effect.get("score_bonus") or 0)
            resistance = max(0, min(100, resistance + int(statecraft_effect.get("resistance_delta") or 0)))
            for kind, delta in (statecraft_effect.get("check_risk_delta") or {}).items():
                if kind in ("delay", "skim", "block", "surprise"):
                    check_risk[kind] = max(0, int(check_risk.get(kind) or 0) + int(delta))
            for note in statecraft_effect.get("notes") or []:
                trait_notes.append(str(note))
        except Exception:
            statecraft_effect = {}
    if timing_profile:
        resistance = min(resistance, int(timing_profile.get("resistance_cap") or resistance))
        for kind, delta in (timing_profile.get("check_risk_delta") or {}).items():
            if kind in ("delay", "skim", "block", "surprise"):
                check_risk[kind] = max(0, int(check_risk.get(kind) or 0) + int(delta))
        note = str(timing_profile.get("note") or "")
        if note:
            trait_notes.append(f"时序：{note}")
    ability_factor = 1.35 - ability / 200.0          # ability 100 → 0.85；50 → 1.10
    resistance_factor = 1.0 + resistance / 150.0     # 阻力 100 → ×1.67
    exec_days = max(2, round(int(category["base_days"]) * ability_factor * distance
                             * resistance_factor * float(foundation_mods.get("exec_factor") or 1.0)
                             * statecraft_exec_factor))
    lead_days = max(1, round(int(category["lead_days"]) * distance))
    if timing_profile:
        lead_days = max(0, int(timing_profile.get("lead_days") or 0))
        exec_days = max(1, int(timing_profile.get("exec_days") or 1))
    deadline_days = explicit_deadline_days(text)
    if deadline_days and lead_days + exec_days > deadline_days:
        lead_days = min(lead_days, max(0, deadline_days - 1))
        exec_days = max(1, deadline_days - lead_days)
    return {
        "category": str(category["id"]),
        "category_name": str(category["name"]),
        "region_id": region_id,
        "assignee": assignee,
        "lead_days": lead_days,
        "exec_days": exec_days,
        "explicit_deadline_days": deadline_days,
        "resistance": resistance,
        "chain": chain,
        "check_risk": check_risk,
        "trait_score": int(foundation_mods.get("score") or 0),
        "trait_notes": trait_notes,
        "score_bonus": statecraft_score_bonus,
        "statecraft_preflight": statecraft_effect.get("preflight") if isinstance(statecraft_effect, dict) else {},
        "statecraft_effect": statecraft_effect,
        "timing_profile": str(timing_profile.get("timing_profile") or "administrative"),
        "timing_note": str(timing_profile.get("note") or ""),
        "policy_doctrine": policy_review,
    }


def init_directive_lifecycles(db: GameDB, state: GameState, directives, day: int) -> List[Dict[str, object]]:
    """颁诏时初始化各旨意生命周期。幂等（已有 lifecycle_status 的跳过）。"""
    initialized = []
    for row in directives or []:
        did = int(row["id"])
        cur = db.conn.execute(
            "SELECT lifecycle_status FROM turn_directives WHERE id=?", (did,)
        ).fetchone()
        if cur is None or str(cur["lifecycle_status"] or ""):
            continue
        text = str(row["text"] or "")
        actor = str(row["actor"] or "") if "actor" in row.keys() else ""
        plan = build_chain(db, state, text, actor)
        policy_doctrine = plan.get("policy_doctrine") if isinstance(plan.get("policy_doctrine"), dict) else {}
        try:
            from ming_sim import policies
            policy_doctrine = policies.apply_directive_doctrine_effects(
                db,
                state,
                directive_id=did,
                text=text,
                category_id=str(plan["category"]),
                actor=str(plan.get("assignee") or actor or ""),
            )
        except Exception as exc:
            tlog(f"[policy] 国策标注失败，跳过：#{did} {exc}")
        eta = int(day) + int(plan["lead_days"]) + int(plan["exec_days"])
        initial_status = "executing" if int(plan["lead_days"]) <= 0 else "in_transit"
        db.conn.execute(
            """UPDATE turn_directives SET lifecycle_status=?, category=?,
               progress=0, lead_days=?, exec_days=?, start_day=?, eta_day=?,
               assignee=?, chain=?, integrity_actual=100, integrity_reported=100
               WHERE id=?""",
            (initial_status, plan["category"], int(plan["lead_days"]), int(plan["exec_days"]),
             int(day), eta, plan["assignee"],
             json.dumps({"chain": plan["chain"], "region_id": plan["region_id"],
                         "resistance": plan["resistance"],
                         "explicit_deadline_days": int(plan.get("explicit_deadline_days") or 0),
                         "check_risk": plan["check_risk"],
                         "trait_score": int(plan.get("trait_score") or 0),
                         "trait_notes": plan.get("trait_notes") or [],
                         "statecraft_preflight": plan.get("statecraft_preflight") or {},
                         "statecraft_effect": plan.get("statecraft_effect") or {},
                         "timing_profile": str(plan.get("timing_profile") or "administrative"),
                         "timing_note": str(plan.get("timing_note") or ""),
                         "policy_doctrine": policy_doctrine,
                         "score_bonus": int(plan.get("score_bonus") or 0)}, ensure_ascii=False),
             did),
        )
        # 崇祯陷阱被动信号（S5）：严谴问罪之旨，百官自动更新「任事有多危险」的先验
        punitive = ("下狱", "处死", "弃市", "处斩", "斩立决", "传首", "逮问", "革职查办", "廷杖")
        if any(kw in text for kw in punitive):
            adjust_belief(db, KV_RISK_AVERSION, +3, f"严谴之旨（#{did}）", day=day)
        initialized.append({"id": did, **plan, "policy_doctrine": policy_doctrine, "eta_day": eta})
    db.conn.commit()
    return initialized


# ── 旬检定 ───────────────────────────────────────────────────────────────────

def _chain_meta(row) -> Dict[str, object]:
    try:
        data = json.loads(row["chain"] or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _save_chain_meta(db: GameDB, did: int, meta: Dict[str, object]) -> None:
    db.conn.execute("UPDATE turn_directives SET chain=? WHERE id=?",
                    (json.dumps(meta, ensure_ascii=False), did))


def _json_dict(raw: object) -> Dict[str, object]:
    try:
        data = json.loads(str(raw or "{}"))
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _followup_action_brief(raw: object) -> str:
    data = raw if isinstance(raw, dict) else {}
    kind = str(data.get("kind") or "")
    minister = str(data.get("minister") or "").strip()
    day = int(data.get("day") or 0)
    label = {
        "rewarded": "已奖叙记功，差使可作为资历，不得再装作未受恩赏",
        "accounted": "已核过功过与奏报水分，后续说法须承认可据此赏罚",
        "followup_evasive": "追问时避责未清，疑点仍在，不得粉饰成全无争议",
        "next_step": "已问出续办方向，后续应承接下一手而不是重说已结之事",
        "reviewed": "已被御前点过，后续应承认此事已经复盘",
    }.get(kind, "")
    if not label:
        return ""
    who = f"{minister}：" if minister else ""
    when = f"第{day}日，" if day > 0 else ""
    return f"{when}{who}{label}"


def _anomaly_label(raw: object) -> str:
    kind = str(_json_dict(raw).get("kind") or "")
    return {"block": "封驳抗命", "delay": "迟滞拖延", "surprise": "实情有变"}.get(kind, "")


def directive_chat_context_brief(db: GameDB, minister_name: str, directive_id: object) -> str:
    """Build a trusted server-side brief for an audience about one directive."""
    try:
        did = int(str(directive_id or "0"))
    except (TypeError, ValueError):
        return ""
    if did <= 0:
        return ""
    item = next(
        (row for row in lifecycle_payload(db, include_done=True, limit=120)
         if int(row.get("id") or 0) == did),
        None,
    )
    if not item:
        return ""
    assignee = str(item.get("assignee") or "").strip()
    if assignee and assignee != minister_name:
        return ""
    status = str(item.get("status") or "")
    status_cn = {
        "in_transit": "送达中",
        "executing": "承办中",
        "stalled": "封驳停摆",
        "done": "已复命",
        "aborted": "已收回",
    }.get(status, status or "未知")
    anomaly = _anomaly_label(item.get("anomaly") or "")
    progress = int(item.get("progress") or 0)
    reported = int(item.get("reported_rate") or 0)
    resistance = int(item.get("resistance") or 0)
    text = str(item.get("text") or "").strip()
    eta = int(item.get("eta_day") or 0)
    is_done = status == "done"
    lines = [
        "【本次召对事项：复命后追问】" if is_done else "【本次召对事项：追问在办旨意】",
        f"旨意#{did}：{text}",
        f"主办官：{assignee or minister_name}；当前状态：{status_cn}；账面进度：{progress}%；奏报执行率：{reported}%；阻力估计：{resistance}。",
    ]
    if eta > 0:
        lines.append(f"预计见分晓日：第{eta}日。")
    if anomaly:
        lines.append(f"当前异常：{anomaly}。")
    settle_note = str(item.get("settle_note") or "").strip()
    if settle_note:
        lines.append(f"最近复命/处置摘录：{settle_note[:180]}")
    followup_brief = _followup_action_brief(item.get("followup_action"))
    if followup_brief:
        lines.append(f"最近御前追问：{followup_brief}。")
    if is_done:
        lines.append(
            "回答规则：你必须承认这道旨意已经复命；不得继续说成未办、未接办或全无进展。"
            "这是复命后的御前追问：按你的身份与私心说明成效、奏报口径是否有水分、谁有功谁有过、"
            "余波风险和下一步可续办之事；可请赏、请罪、推责或请求另下一道旨意，但不要把已复命之事重置成在办。"
            "若上方已有最近御前追问结论，必须承接该结论，不得推翻成未奖、未核或未问。"
        )
    else:
        lines.append(
            "回答规则：你必须承认这道旨意与自己有关；不得说成不知道、未接办或全无进展。"
            "按你的身份、能力、派系与私心交代真实阻力，可请款、请换人、推责、认责或给出可验期限。"
        )
    return "\n".join(lines)


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _extract_blocker_clue(db: GameDB, answer: str, assignee: str) -> Dict[str, object]:
    text = str(answer or "")
    if not text:
        return {}
    try:
        rows = db.conn.execute(
            """
            SELECT name, office, faction
            FROM characters
            WHERE status='active' AND name != ?
            ORDER BY length(name) DESC
            """,
            (assignee,),
        ).fetchall()
        for row in rows:
            name = str(row["name"] or "").strip()
            if name and name in text:
                detail = " · ".join(part for part in (str(row["office"] or ""), str(row["faction"] or "")) if part)
                return {"kind": "person", "name": name, "label": name, "detail": detail}
    except Exception:
        pass
    try:
        factions = [
            str(row["faction"] or "").strip()
            for row in db.conn.execute(
                "SELECT DISTINCT faction FROM characters WHERE status='active' AND faction!='' ORDER BY length(faction) DESC"
            ).fetchall()
        ]
        for faction in factions:
            if faction and faction in text:
                return {"kind": "faction", "label": faction, "detail": "派系掣肘"}
    except Exception:
        pass
    for org in ("户部", "兵部", "吏部", "礼部", "工部", "都察院", "内阁", "司礼监", "东厂", "锦衣卫", "关宁军", "京营"):
        if org in text:
            return {"kind": "org", "label": org, "detail": "衙门阻力"}
    if _has_any(text, ("钱粮", "军饷", "粮饷", "拨银", "加拨")):
        return {"kind": "resource", "label": "钱粮", "detail": "资源阻力"}
    return {}


def _remember_blocker_clue(meta: Dict[str, object], clue: Dict[str, object], *, minister_name: str, day: int) -> Dict[str, object]:
    if not clue:
        return {}
    saved = dict(clue)
    saved["source_minister"] = minister_name
    saved["day"] = int(day)
    meta["blocker_clue"] = saved
    return saved


def _blocker_clue(meta: Dict[str, object]) -> Dict[str, object]:
    clue = meta.get("blocker_clue")
    return clue if isinstance(clue, dict) else {}


def _blocker_label(clue: Dict[str, object]) -> str:
    return str(clue.get("name") or clue.get("label") or "").strip()


def _blocker_faction(db: GameDB, clue: Dict[str, object]) -> str:
    kind = str(clue.get("kind") or "")
    label = _blocker_label(clue)
    if not label:
        return ""
    if kind == "person":
        row = db.conn.execute("SELECT faction FROM characters WHERE name=?", (label,)).fetchone()
        return str(row["faction"] or "").strip() if row else ""
    if kind == "faction":
        row = db.conn.execute("SELECT name FROM factions WHERE name=?", (label,)).fetchone()
        return str(row["name"] or "").strip() if row else ""
    if label in ("司礼监", "东厂", "锦衣卫"):
        return "阉党"
    return ""


def _remember_blocker_action(
    meta: Dict[str, object],
    *,
    action: str,
    label: str,
    day: int,
    progress_delta: int,
    resistance_delta: int,
) -> None:
    meta["last_blocker_action"] = {
        "action": action,
        "label": label,
        "day": int(day),
        "progress_delta": int(progress_delta),
        "resistance_delta": int(resistance_delta),
    }


def _effect(label: str, tone: str = "neutral", kind: str = "intervention") -> Dict[str, object]:
    return {"kind": kind, "label": label, "tone": tone}


def _delta_label(name: str, value: int, *, good_positive: bool = True) -> Dict[str, object]:
    signed = f"{'+' if value > 0 else ''}{int(value)}"
    tone = "neutral"
    if value:
        tone = "good" if (value > 0) == good_positive else "bad"
    return _effect(f"{name} {signed}", tone)


def _followup_history(meta: Dict[str, object]) -> List[Dict[str, object]]:
    raw = meta.get("followup_history")
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    last = meta.get("last_followup_action")
    return [last] if isinstance(last, dict) else []


def _record_followup_action(meta: Dict[str, object], *, kind: str, minister_name: str, day: int) -> None:
    item = {"kind": kind, "minister": minister_name, "day": int(day)}
    history = _followup_history(meta)
    if not any(str(x.get("kind") or "") == kind for x in history):
        history.append(item)
    meta["followup_history"] = history[-8:]
    meta["last_followup_action"] = item


def _apply_done_directive_followup(
    db: GameDB,
    row,
    did: int,
    minister_name: str,
    user_text: str,
    answer: str,
) -> Dict[str, object]:
    """Record post-completion accountability without reopening a done directive."""

    prompt = str(user_text or "")
    reply = str(answer or "")
    combined = f"{prompt}\n{reply}"
    if not _has_any(
        combined,
        (
            "复命", "成效", "实效", "办得", "办成", "水分", "虚报", "不实", "查账",
            "核实", "功过", "赏", "奖", "嘉", "记功", "责", "罪", "罚", "申饬",
            "下一步", "续办", "后续", "余波", "续旨", "交给谁",
        ),
    ):
        return {}

    day = kv_int(db, KV_CURRENT_DAY, 1)
    meta = _chain_meta(row)
    assignee = str(row["assignee"] or "").strip()
    evade = _has_any(reply, ("不知", "未闻", "非臣", "不归臣", "无从", "不能", "难以", "未接"))
    accountability = _has_any(
        prompt,
        ("问责", "责", "罪", "水分", "虚报", "不实", "欺", "罚", "申饬", "查账", "核实", "实效", "功过"),
    )
    praise = _has_any(prompt, ("赏", "奖", "嘉", "记功", "褒", "慰", "辛苦", "有功", "办得好", "论功"))
    next_step = _has_any(prompt, ("下一步", "续办", "再下一道", "接着", "后续", "余波", "还缺", "交给谁", "限期", "续旨"))

    if evade and accountability:
        kind = "followup_evasive"
    elif accountability:
        kind = "accounted"
    elif praise:
        kind = "rewarded"
    elif next_step:
        kind = "next_step"
    else:
        kind = "reviewed"

    if any(str(item.get("kind") or "") == kind for item in _followup_history(meta)):
        _record_followup_action(meta, kind=kind, minister_name=minister_name, day=day)
        _save_chain_meta(db, did, meta)
        db.conn.commit()
        return {
            "directive_id": did,
            "kind": "already_followed_up",
            "title": "已入案",
            "message": f"这道旨意的{minister_name}复命追问已经入案，再问可作口供，不再重复增减利害。",
            "effects": [_effect("不重复结算", "neutral", "followup")],
        }

    effects: List[Dict[str, object]] = []
    if kind == "rewarded":
        if assignee:
            db.conn.execute(
                "UPDATE characters SET emp_trust=MIN(100, emp_trust+4), "
                "grievance=MAX(0, grievance-3) WHERE name=?",
                (assignee,),
            )
        adjust_belief(db, KV_RISK_AVERSION, -1, f"复命奖叙旨意#{did}", day=day)
        title = "功过已明"
        message = f"御前明示奖叙，{minister_name}这件差使结成资历（信任+4，怨气-3，任事观望-1）。"
        effects = [
            _delta_label("信任", +4),
            _delta_label("怨气", -3, good_positive=False),
            _delta_label("任事观望", -1, good_positive=False),
        ]
    elif kind == "followup_evasive":
        if assignee:
            db.conn.execute("UPDATE characters SET grievance=MIN(100, grievance+3) WHERE name=?", (assignee,))
        adjust_belief(db, KV_RISK_AVERSION, +1, f"复命追问旨意#{did}避重就轻", day=day)
        title = "复命未清"
        message = f"{minister_name}对水分与功过仍避重就轻，案虽结而疑未散（怨气+3，任事观望+1）。"
        effects = [
            _delta_label("怨气", +3, good_positive=False),
            _delta_label("任事观望", +1, good_positive=False),
        ]
    elif kind == "accounted":
        if assignee:
            db.conn.execute(
                "UPDATE characters SET emp_trust=MIN(100, emp_trust+2), "
                "grievance=MIN(100, grievance+1) WHERE name=?",
                (assignee,),
            )
        adjust_belief(db, KV_RISK_AVERSION, -1, f"复命核实旨意#{did}", day=day)
        title = "水分入账"
        message = f"御前把{minister_name}的功过和奏报水分记入案牍，日后可据此赏罚（信任+2，怨气+1，任事观望-1）。"
        effects = [
            _delta_label("信任", +2),
            _delta_label("怨气", +1, good_positive=False),
            _delta_label("任事观望", -1, good_positive=False),
        ]
    elif kind == "next_step":
        title = "余波成案"
        message = f"{minister_name}已把复命余波说到下一手，后续可另下旨接续追办。"
        effects = [_effect("续办线索入案", "good", "followup")]
    else:
        title = "复命已阅"
        message = f"这道旨意已被御前点过，若要形成利害，还需明示赏、罚、核实或续办。"
        effects = [_effect("复命追问入案", "neutral", "followup")]

    _record_followup_action(meta, kind=kind, minister_name=minister_name, day=day)
    _save_chain_meta(db, did, meta)
    db.conn.commit()
    return {
        "directive_id": did,
        "kind": kind,
        "title": title,
        "message": message,
        "effects": effects,
    }


def apply_directive_audience_pressure(
    db: GameDB,
    state: GameState,
    minister_name: str,
    directive_id: object,
    user_text: str,
    answer: str,
) -> Dict[str, object]:
    """Small deterministic lifecycle nudge from questioning an assignee in audience.

    This is intentionally weaker than explicit intervention actions. It turns
    roleplay into visible state movement without letting one conversation skip
    the execution lifecycle.
    """
    try:
        did = int(str(directive_id or "0"))
    except (TypeError, ValueError):
        return {}
    row = db.conn.execute("SELECT * FROM turn_directives WHERE id=?", (did,)).fetchone()
    if row is None:
        return {}
    assignee = str(row["assignee"] or "").strip()
    if assignee and assignee != minister_name:
        return {}
    status = str(row["lifecycle_status"] or "")
    if status == "done":
        return _apply_done_directive_followup(db, row, did, minister_name, user_text, answer)
    if status not in LIVE_STATUSES:
        return {}

    combined = f"{user_text}\n{answer}"
    if not _has_any(combined, ("进度", "实数", "几分", "阻力", "停滞", "掣肘", "期限", "催", "责", "交账", "复命")):
        return {}

    evade = _has_any(answer, ("不知", "未闻", "非臣", "不归臣", "无从", "不能", "不敢", "难以", "未接"))
    support = _has_any(answer, ("请拨", "加拨", "拨银", "钱粮", "军饷", "粮饷", "人手", "会同", "监军", "部议"))
    commit = _has_any(answer, ("遵旨", "谨遵", "臣当", "臣即", "即日", "立刻", "具奏", "清册", "交账", "担责", "限日", "三日", "五日", "十日"))
    forceful = _has_any(user_text, ("进度", "实数", "几分", "阻力", "停滞", "责", "期限"))
    day = kv_int(db, KV_CURRENT_DAY, 1)
    meta = _chain_meta(row)
    anomaly = _anomaly_label(row["anomaly"] or "")
    blocker_clue = _extract_blocker_clue(db, answer, assignee or minister_name)

    if evade and not commit:
        if assignee:
            db.conn.execute("UPDATE characters SET grievance=MIN(100, grievance+3) WHERE name=?", (assignee,))
        adjust_belief(db, KV_RISK_AVERSION, +1, f"召对追问旨意#{did}避重就轻", day=day)
        db.conn.commit()
        return {
            "directive_id": did,
            "kind": "evasive",
            "title": "旨意未动",
            "message": f"{minister_name}避重就轻，旨意进展未见松动（主办怨气+，任事观望+）。",
            "progress_delta": 0,
            "resistance_delta": 0,
        }

    if support and not commit:
        db.conn.execute(
            "UPDATE turn_directives SET progress=MIN(99, progress+2) WHERE id=?",
            (did,),
        )
        meta["last_audience_pressure"] = {"kind": "needs_support", "minister": minister_name, "day": day}
        saved_clue = _remember_blocker_clue(meta, blocker_clue, minister_name=minister_name, day=day)
        _save_chain_meta(db, did, meta)
        db.conn.commit()
        clue_text = f"，线索：{saved_clue.get('label')}" if saved_clue else ""
        return {
            "directive_id": did,
            "kind": "needs_support",
            "title": "阻力露底",
            "message": f"{minister_name}把阻力摊到御前，旨意稍有眉目（进度+2，仍待加拨或换人处置{clue_text}）。",
            "progress_delta": 2,
            "resistance_delta": 0,
            "suggested_action": "fund",
            "blocker_clue": saved_clue,
        }

    progress_delta = 6 if forceful else 4
    if status == "stalled":
        progress_delta = max(4, progress_delta - 1)
    resistance_delta = -5 if forceful else -3
    exec_delta = -2 if forceful else -1
    current_progress = int(row["progress"] or 0)
    new_progress = min(99, max(current_progress, current_progress + progress_delta))
    meta["resistance"] = max(0, int(meta.get("resistance") or 0) + resistance_delta)
    meta["last_audience_pressure"] = {
        "kind": "pressed",
        "minister": minister_name,
        "day": day,
        "progress_delta": new_progress - current_progress,
        "resistance_delta": resistance_delta,
    }
    saved_clue = _remember_blocker_clue(meta, blocker_clue, minister_name=minister_name, day=day)
    _save_chain_meta(db, did, meta)
    db.conn.execute(
        "UPDATE turn_directives SET lifecycle_status='executing', progress=?, "
        "exec_days=MAX(2, exec_days+?), eta_day=MAX(start_day+lead_days+2, eta_day+?), "
        "anomaly='' WHERE id=?",
        (new_progress, exec_delta, exec_delta, did),
    )
    if assignee:
        db.conn.execute("UPDATE characters SET grievance=MIN(100, grievance+2) WHERE name=?", (assignee,))
    if _has_any(user_text, ("责", "停滞", "问罪")):
        adjust_belief(db, KV_RISK_AVERSION, +1, f"御前责问旨意#{did}", day=day)
    db.conn.commit()
    moved = new_progress - current_progress
    cleared = f"，{anomaly}已压下" if anomaly else ""
    clue_text = f"，线索：{saved_clue.get('label')}" if saved_clue else ""
    return {
        "directive_id": did,
        "kind": "pressed",
        "title": "旨意有动",
        "message": f"御前追问压实了{minister_name}的差使（进度+{moved}，阻力{resistance_delta}{cleared}{clue_text}）。",
        "progress_delta": moved,
        "resistance_delta": resistance_delta,
        "cleared_anomaly": bool(anomaly),
        "blocker_clue": saved_clue,
    }


def _signed(value: object, suffix: str = "") -> str:
    try:
        n = int(value or 0)
    except (TypeError, ValueError):
        n = 0
    if n == 0:
        return ""
    return f"{'+' if n > 0 else ''}{n}{suffix}"


def _outcome_tone(kind: str, value: object) -> str:
    try:
        n = int(value or 0)
    except (TypeError, ValueError):
        n = 0
    if kind in {"unrest"}:
        return "good" if n < 0 else "bad"
    if kind in {"issue"}:
        return "good" if n > 0 else "bad"
    return "good" if n > 0 else "bad"


def _outcome_summary(db: GameDB, delta: Dict[str, object], *, limit: int = 8) -> List[Dict[str, object]]:
    """Small player-facing chips for a directive outcome.

    This exposes visible world changes, not the hidden actual/report integrity split.
    """
    chips: List[Dict[str, object]] = []
    metric_delta = delta.get("metric_delta")
    if isinstance(metric_delta, dict):
        for key, value in metric_delta.items():
            label = _signed(value)
            if label:
                chips.append({"kind": "metric", "label": f"{key} {label}", "tone": _outcome_tone("metric", value)})

    for move in delta.get("economy_moves") or []:
        if not isinstance(move, dict):
            continue
        account = str(move.get("account") or "").strip()
        label = _signed(move.get("delta"), "万")
        if account and label:
            chips.append({"kind": "economy", "label": f"{account} {label}", "tone": _outcome_tone("economy", move.get("delta"))})

    region_delta = delta.get("region_delta")
    if isinstance(region_delta, dict):
        for region_id, values in region_delta.items():
            if not isinstance(values, dict):
                continue
            unrest = _signed(values.get("unrest"))
            if not unrest:
                continue
            row = db.conn.execute("SELECT name FROM regions WHERE id=?", (str(region_id),)).fetchone()
            name = str(row["name"] or region_id) if row else str(region_id)
            chips.append({"kind": "region", "label": f"{name}动乱 {unrest}", "tone": _outcome_tone("unrest", values.get("unrest"))})

    for item in delta.get("issue_advances") or []:
        if not isinstance(item, dict):
            continue
        value = item.get("delta_bar", item.get("delta"))
        label = _signed(value)
        if not label:
            continue
        issue_id = int(item.get("issue_id") or 0)
        row = db.conn.execute("SELECT title FROM issues WHERE id=?", (issue_id,)).fetchone() if issue_id else None
        title = str(row["title"] or f"局势#{issue_id}") if row else f"局势#{issue_id}"
        chips.append({"kind": "issue", "label": f"{title} {label}", "tone": _outcome_tone("issue", value)})

    for item in delta.get("office_changes") or []:
        if isinstance(item, dict) and item.get("name") and item.get("new_office"):
            chips.append({"kind": "office", "label": f"{item.get('name')}授{item.get('new_office')}", "tone": "good"})

    for item in delta.get("character_status_changes") or []:
        if isinstance(item, dict) and item.get("name") and item.get("status"):
            chips.append({"kind": "person", "label": f"{item.get('name')} {item.get('status')}", "tone": "bad"})

    return chips[:limit]


def _execution_score(db: GameDB, row, meta: Dict[str, object]) -> int:
    arow = _char_row(db, str(row["assignee"] or ""))
    ability = int(arow["ability"]) if arow else 50
    loyalty = int(arow["loyalty"]) if arow else 50
    grievance = int(arow["grievance"]) if arow and "grievance" in arow.keys() else 20
    shi = kv_int(db, KV_SHI, SHI_DEFAULT)
    ra = kv_int(db, KV_RISK_AVERSION, RISK_AVERSION_DEFAULT)
    resistance = int(meta.get("resistance") or 0)
    bonus = int(meta.get("score_bonus") or 0) + int(meta.get("trait_score") or 0)
    # 基座五维能力（才总折百）优先于游戏 ability
    try:
        from ming_sim.foundation import ability100
        fab = ability100(str(row["assignee"] or ""))
        if fab is not None:
            ability = fab
    except Exception:
        pass
    score = (50
             + round((ability - 50) * 0.40)
             + round((loyalty - 50) * 0.15)
             + round((shi - 50) * 0.30)
             - round(resistance * 0.40)
             - round(max(0, ra - 40) * 0.20)
             - round(grievance * 0.20)
             + bonus)
    return max(5, min(95, score))


def _pick_anomaly(rng: random.Random, check_risk: Dict[str, object]) -> str:
    pool: List[str] = []
    for kind in ("delay", "skim", "block", "surprise"):
        pool.extend([kind] * max(0, int(check_risk.get(kind) or 0)))
    return rng.choice(pool) if pool else "delay"


def _enqueue(db: GameDB, kind: str, payload: Dict[str, object]) -> None:
    try:
        from ming_sim.scheduler import enqueue_job
        enqueue_job(db, kind, payload)
    except Exception:
        pass


_ANOMALY_TEXT = {
    "delay": ("主办陈情迁延", "称钱粮未齐、吏胥推诿，乞展限办理。可催办、换人或加拨钱粮。"),
    "block": ("科道封驳抗辩", "给事中以事体未协封还，旨意冻结待圣裁。可催办（强令施行）、换人或收回成命。"),
    "surprise": ("地方实情有变", "原议与地方情形不符，须更张办法，工期顺延。"),
}


def _plant_consequences(db: GameDB, state: GameState, did: int, text: str, day: int) -> List[str]:
    """缺口1 因果伏笔：旨意办结时按关键词命中埋 causal_seed（裁驿→流寇等）。
    当下省钱省力的政策，fuse_days 后（gate 达标）萌发为情势——延迟的代价。返回已埋说明。"""
    from ming_sim.timeflow import plant_causal_seed
    planted: List[str] = []
    for rule in load_consequences().get("consequences") or []:
        kws = rule.get("keywords") or []
        if not kws or not all(kw in text for kw in kws):
            continue
        spec = dict(rule.get("event") or {})
        region = spec.get("region_hint") or ""
        if region:  # 爆发地随旨意正文里点到的地区走（无则用配置默认）
            det = _detect_region(db, text)
            if det and det != "beizhili":
                spec["region_hint"] = det
        plant_causal_seed(
            db, created_day=int(day), fuse_days=int(rule.get("fuse_days") or 60),
            trigger_gate=dict(rule.get("gate") or {}), event_spec=spec,
            note=f"#{did} {str(rule.get('note') or '')}",
        )
        planted.append(str(rule.get("id") or ""))
        db.record_log(state, f"【因果伏笔】{text[:18]}…埋下祸根（{rule.get('note') or ''}）")
    return planted


def _apply_execution_consequence(db: GameDB, state: GameState, meta: Dict[str, object],
                                 actual: int, day: int) -> None:
    """缺口3 截留黑箱落地：integrity_actual<85 的办结，截留的银钱化为实在恶果——
    经手地方腐败/民怨抬头（unrest↑）、天下民心折损（民心↓），与 report_ledger 并存：
    数值后果即刻发生（规则层），账实矛盾留待密查/盘库揭穿（P4 一切数据皆证言）。"""
    shortfall = max(0, 100 - int(actual))   # 被截留的执行率点
    if shortfall <= 15:
        return
    region_id = str(meta.get("region_id") or "")
    if region_id:
        # 截留之政未达民，反激民怨：经手省份民变压力上升
        db.conn.execute(
            "UPDATE regions SET unrest=MIN(100, unrest + ?) WHERE id=?",
            (max(1, round(shortfall / 12)), region_id))
    # 天下民心折损（办差走样、银钱不知所终）
    before = int(state.metrics.get("民心", 50))
    state.metrics["民心"] = max(0, before - max(1, round(shortfall / 20)))


def tick_directives(db: GameDB, state: GameState, day: int) -> List[Dict[str, object]]:
    """日推进：送达→执行；执行中按日涨进度；逢旬（每10日）做执行检定。
    返回 timeflow 事件 dict 列表。"""
    events: List[Dict[str, object]] = []
    rows = db.conn.execute(
        "SELECT * FROM turn_directives WHERE lifecycle_status IN ('in_transit','executing','stalled')"
    ).fetchall()
    for row in rows:
        did = int(row["id"])
        status = str(row["lifecycle_status"])
        start_day = int(row["start_day"])
        lead = int(row["lead_days"])
        meta = _chain_meta(row)

        if status == "in_transit":
            if day >= start_day + lead:
                db.conn.execute(
                    "UPDATE turn_directives SET lifecycle_status='executing' WHERE id=?", (did,))
            continue

        if status == "stalled":
            # 封驳搁置：诏令不行损君威，但伤害前置且封顶——一次封驳的累计折势不
            # 超过 收回成命(−3)；君威损伤应是「一次冲击」而非「永久滴血」。
            # 且搁置逾月仍无人接办（催办/换人/收回均未至）则自动作罢，
            # 既止血又清出超期积压（headless 下原本永久拖拽势→棘轮到吸收态）。
            try:
                stall_meta = json.loads(row["anomaly"] or "{}")
            except (ValueError, TypeError):
                stall_meta = {}
            exec_days_s = max(1, int(row["exec_days"]))
            stall_age = day - start_day - lead - exec_days_s
            if stall_age > 30:
                db.conn.execute(
                    "UPDATE turn_directives SET lifecycle_status='aborted', anomaly='' WHERE id=?",
                    (did,))
                title_text = str(row["text"] or "")[:24]
                events.append({"level": LEVEL_YELLOW, "kind": "directive_aborted",
                               "title": f"〔{title_text}〕久搁自罢",
                               "detail": "封驳搁置经月无人接办，诏命形同具文，已自行作罢。",
                               "ref_kind": "directive", "ref_id": str(did), "day": day})
                continue
            if (day - start_day) % 10 == 0:
                bled = int(stall_meta.get("shi_bled", 0))
                if bled < 3:
                    adjust_belief(db, KV_SHI, -1, f"旨意#{did}遭封驳搁置", day=day)
                    stall_meta["shi_bled"] = bled + 1
                    db.conn.execute("UPDATE turn_directives SET anomaly=? WHERE id=?",
                                    (json.dumps(stall_meta, ensure_ascii=False), did))
            continue

        # executing：按「剩余进度/剩余天数」自校正推进——
        # 工期中途延长（delay/surprise）自动摊薄、干预加成自动提前、
        # 提前颁诏跳日后自动追平，完成日与 eta_day 严格对齐（审计修复 P2-1）。
        exec_days = max(1, int(row["exec_days"]))
        eta = start_day + lead + exec_days
        remaining_days = eta - day + 1
        old_progress = int(row["progress"])
        if remaining_days <= 1:
            progress = 100
        else:
            progress = min(100, old_progress + max(1, round((100 - old_progress) / remaining_days)))
        db.conn.execute("UPDATE turn_directives SET progress=? WHERE id=?", (progress, did))

        # 旬检定（执行期内每满10天一次；完成日不再检）
        days_in_exec = day - start_day - lead
        if progress < 100 and days_in_exec > 0 and days_in_exec % 10 == 0:
            rng = random.Random(did * 100003 + day)
            score = _execution_score(db, row, meta)
            anomaly_prob = max(0.05, min(0.60, (70 - score) / 100.0))
            if rng.random() < anomaly_prob:
                kind = _pick_anomaly(rng, dict(meta.get("check_risk") or {}))
                title_text = str(row["text"] or "")[:24]
                if kind == "skim":
                    # 截留打折：账实分叉，玩家此刻看不到（这正是黑箱）
                    cut = rng.randint(15, 30)
                    new_actual = max(20, int(row["integrity_actual"]) - cut)
                    db.conn.execute(
                        "UPDATE turn_directives SET integrity_actual=? WHERE id=?",
                        (new_actual, did))
                elif kind == "delay":
                    extra = rng.randint(5, 10)
                    db.conn.execute(
                        "UPDATE turn_directives SET exec_days=exec_days+?, eta_day=eta_day+?, anomaly=? WHERE id=?",
                        (extra, extra,
                         json.dumps({"kind": "delay", "day": day}, ensure_ascii=False), did))
                    t, d = _ANOMALY_TEXT["delay"]
                    events.append({"level": LEVEL_YELLOW, "kind": "directive_anomaly",
                                   "title": f"〔{title_text}〕{t}",
                                   "detail": d, "ref_kind": "directive", "ref_id": str(did), "day": day})
                    _enqueue(db, "anomaly_text", {"directive_id": did, "anomaly_kind": "delay"})
                elif kind == "block":
                    db.conn.execute(
                        "UPDATE turn_directives SET lifecycle_status='stalled', anomaly=? WHERE id=?",
                        (json.dumps({"kind": "block", "day": day}, ensure_ascii=False), did))
                    t, d = _ANOMALY_TEXT["block"]
                    events.append({"level": LEVEL_RED, "kind": "directive_anomaly",
                                   "title": f"〔{title_text}〕{t}",
                                   "detail": d, "ref_kind": "directive", "ref_id": str(did), "day": day})
                    _enqueue(db, "anomaly_text", {"directive_id": did, "anomaly_kind": "block"})
                else:  # surprise
                    extra = rng.randint(6, 14)
                    db.conn.execute(
                        "UPDATE turn_directives SET exec_days=exec_days+?, eta_day=eta_day+?, anomaly=? WHERE id=?",
                        (extra, extra,
                         json.dumps({"kind": "surprise", "day": day}, ensure_ascii=False), did))
                    t, d = _ANOMALY_TEXT["surprise"]
                    events.append({"level": LEVEL_YELLOW, "kind": "directive_anomaly",
                                   "title": f"〔{title_text}〕{t}",
                                   "detail": d, "ref_kind": "directive", "ref_id": str(did), "day": day})
                    _enqueue(db, "anomaly_text", {"directive_id": did, "anomaly_kind": "surprise"})

        # 完成
        row2 = db.conn.execute("SELECT progress, integrity_actual, integrity_reported, assignee, text "
                               "FROM turn_directives WHERE id=?", (did,)).fetchone()
        if int(row2["progress"]) >= 100:
            actual = int(row2["integrity_actual"])
            reported = int(row2["integrity_reported"])
            db.conn.execute(
                "UPDATE turn_directives SET lifecycle_status='done', progress=100, anomaly='' WHERE id=?",
                (did,))
            adjust_belief(db, KV_SHI, +1, f"旨意#{did}如期办结", day=day)
            if actual < 85:
                # 奏报粉饰：账实分离落 report_ledger（S3），待密查/盘库揭穿
                arow = _char_row(db, str(row2["assignee"] or ""))
                db.conn.execute(
                    """INSERT INTO report_ledger
                       (entity_kind, entity_id, field, reported_value, actual_value,
                        author_org, author_character, reported_day, note)
                       VALUES ('directive', ?, 'execution_rate', ?, ?, ?, ?, ?, ?)""",
                    (str(did), float(reported), float(actual),
                     str(arow["office"]) if arow else "",
                     str(row2["assignee"] or ""), int(day),
                     f"旨意办结奏报：{str(row2['text'] or '')[:40]}"),
                )
                # 缺口3：截留即刻化为实在恶果（民怨/地方民变压力），不再是无后果的黑箱
                _apply_execution_consequence(db, state, meta, actual, day)
            # 缺口1：政策落地即埋因果伏笔（裁驿→流寇等延迟代价）
            _plant_consequences(db, state, did, str(row2["text"] or ""), day)
            events.append({"level": LEVEL_YELLOW, "kind": "directive_done",
                           "title": f"〔{str(row2['text'] or '')[:24]}〕办结奏闻",
                           "detail": f"主办{row2['assignee']}奏称已遵旨办竣。",
                           "ref_kind": "directive", "ref_id": str(did), "day": day})
            # 即时复命：办结即由 worker 产复命奏报+暂存数值 delta（替代旧 settle_note 纯文采），
            # 主线程 session.drain_pending_outcomes 落库——变集中反馈为即时反馈。
            _enqueue(db, "edict_outcome", {"directive_id": did})
    db.conn.commit()
    db.save_state(state)  # 缺口3 的民心折损改的是 GameState.metrics，须落库
    return events


# ── 玩家中途干预 ─────────────────────────────────────────────────────────────

def intervene(db: GameDB, state: GameState, directive_id: int, action: str,
              *, day: int, new_assignee: str = "", fund: int = 0) -> Dict[str, object]:
    """执行中旨意干预。

    常规：催办 cuiban / 换人 reassign / 加拨 fund / 独断 ducai / 收回 abort。
    阻力线索：协调 bargain_blocker / 申饬 pressure_blocker。
    """
    row = db.conn.execute("SELECT * FROM turn_directives WHERE id=?", (int(directive_id),)).fetchone()
    if row is None or str(row["lifecycle_status"]) not in LIVE_STATUSES:
        return {"ok": False, "message": "该旨意不在执行中。"}
    did = int(row["id"])
    meta = _chain_meta(row)
    assignee = str(row["assignee"] or "")
    before_progress = int(row["progress"] or 0)
    before_resistance = int(meta.get("resistance") or 0)
    effects: List[Dict[str, object]] = []

    if action == "cuiban":
        progress_delta = min(100, before_progress + 8) - before_progress
        db.conn.execute(
            "UPDATE turn_directives SET lifecycle_status='executing', "
            "progress=MIN(100, progress+8), exec_days=MAX(2, exec_days-4), anomaly='' WHERE id=?",
            (did,))
        if assignee:
            db.conn.execute(
                "UPDATE characters SET grievance=MIN(100, grievance+5) WHERE name=?", (assignee,))
        adjust_belief(db, KV_RISK_AVERSION, +1, f"严旨催办#{did}", day=day)
        effects = [
            _delta_label("进度", progress_delta),
            _effect("工期 -4", "good"),
            _effect("主办怨气 +5", "bad"),
            _effect("任事观望 +1", "bad"),
        ]
        msg = f"严旨切责，{assignee}惶恐加紧办理（进度+，主办怨气+，百官观望+）。"
    elif action == "reassign":
        if not new_assignee or _char_row(db, new_assignee) is None:
            return {"ok": False, "message": "须指定在朝官员接办。"}
        progress_delta = max(0, before_progress - 15) - before_progress
        if assignee:
            db.conn.execute(
                "UPDATE characters SET grievance=MIN(100, grievance+10) WHERE name=?", (assignee,))
        # 换人重算基座特质修正（新主办的擅/痼接管检定与异常偏置）
        try:
            from ming_sim import foundation
            cat_id = str(row["category"] or "misc")
            mods = foundation.directive_modifiers(new_assignee, cat_id)
            meta["trait_score"] = int(mods.get("score") or 0)
            meta["trait_notes"] = list(mods.get("notes") or [])
            base_risk = dict((load_categories().get("categories") and next(
                (c.get("check_risk") or {} for c in load_categories()["categories"]
                 if c["id"] == cat_id), {})) or {})
            for kind, delta in (mods.get("anomaly_bias") or {}).items():
                base_risk[kind] = max(0, int(base_risk.get(kind) or 0) + int(delta))
            if base_risk:
                meta["check_risk"] = base_risk
            _save_chain_meta(db, did, meta)
        except Exception:
            pass
        db.conn.execute(
            "UPDATE turn_directives SET assignee=?, lifecycle_status='executing', "
            "progress=MAX(0, progress-15), anomaly='' WHERE id=?",
            (new_assignee, did))
        effects = [
            _effect(f"主办改派 {new_assignee}", "neutral"),
            _delta_label("进度", progress_delta),
            _effect(f"{assignee or '原主办'}怨气 +10", "bad"),
        ]
        msg = f"改命{new_assignee}接办（交接折损进度，原主办{assignee}怨望）。"
    elif action == "fund":
        amount = max(1, min(200, int(fund or 10)))
        actual = db.record_issue_economy_move(
            state, "国库", -amount, "加拨办差", f"加拨旨意#{did}经费{amount}万两")
        if not actual:
            return {"ok": False, "message": "国库不敷，拨不出银子。"}
        progress_delta = min(100, before_progress + 5) - before_progress
        meta["score_bonus"] = int(meta.get("score_bonus") or 0) + 12
        meta["resistance"] = max(0, int(meta.get("resistance") or 0) - 10)
        _save_chain_meta(db, did, meta)
        db.conn.execute(
            "UPDATE turn_directives SET lifecycle_status='executing', "
            "progress=MIN(100, progress+5), anomaly='' WHERE id=?", (did,))
        effects = [
            _delta_label("进度", progress_delta),
            _delta_label("阻力", int(meta.get("resistance") or 0) - before_resistance, good_positive=False),
            _effect("检定 +12", "good"),
            _effect(f"国库 {actual}万", "bad"),
        ]
        msg = f"加拨{abs(actual)}万两疏通办差（阻力-，检定+）。"
    elif action == "ducai":
        # 乾纲独断（S6）：势>70 解锁——绕过部议强推，但独断与寒心连动（RA+3）
        from ming_sim.theater import can_ducai
        if not can_ducai(db):
            return {"ok": False, "message": "君威未隆（势不足七十），独断之旨出不了午门。"}
        progress_delta = min(100, before_progress + 25) - before_progress
        meta["score_bonus"] = int(meta.get("score_bonus") or 0) + 20
        meta["resistance"] = max(0, int(meta.get("resistance") or 0) - 20)
        _save_chain_meta(db, did, meta)
        db.conn.execute(
            "UPDATE turn_directives SET lifecycle_status='executing', "
            "progress=MIN(100, progress+25), anomaly='' WHERE id=?", (did,))
        adjust_belief(db, KV_RISK_AVERSION, +3, f"乾纲独断强推#{did}", day=day)
        effects = [
            _delta_label("进度", progress_delta),
            _delta_label("阻力", int(meta.get("resistance") or 0) - before_resistance, good_positive=False),
            _effect("检定 +20", "good"),
            _effect("任事观望 +3", "bad"),
        ]
        msg = "中旨直下，绕开部议封驳，所司不敢复言（进度+25，阻力-20）。然独断日久，任事之心日灰（百官观望+3）。"
    elif action in ("bargain_blocker", "pressure_blocker"):
        clue = _blocker_clue(meta)
        label = _blocker_label(clue)
        if not label:
            return {"ok": False, "message": "尚无可处置的阻力线索。"}
        kind = str(clue.get("kind") or "")
        faction = _blocker_faction(db, clue)
        if action == "bargain_blocker":
            progress_delta, resistance_delta, score_delta = 6, -12, 6
            actual_progress_delta = min(99, before_progress + progress_delta) - before_progress
            meta["resistance"] = max(0, int(meta.get("resistance") or 0) + resistance_delta)
            meta["score_bonus"] = int(meta.get("score_bonus") or 0) + score_delta
            _remember_blocker_action(
                meta, action=action, label=label, day=day,
                progress_delta=progress_delta, resistance_delta=resistance_delta,
            )
            _save_chain_meta(db, did, meta)
            db.conn.execute(
                "UPDATE turn_directives SET lifecycle_status='executing', "
                "progress=MIN(99, progress+?), anomaly='' WHERE id=?",
                (progress_delta, did),
            )
            if kind == "person":
                try:
                    from ming_sim import court
                    court._adjust_char(db, label, emp_trust=+3, grievance=-5)
                    if assignee and assignee != label:
                        court.adjust_opinion(db, label, assignee, +8, "御前协调办差", day=day, reciprocal=False)
                        court.adjust_opinion(db, assignee, label, +4, "御前协调办差", day=day, reciprocal=False)
                except Exception:
                    pass
            if faction and faction not in ("无", "中立"):
                db.adjust_factions({faction: {"satisfaction": 2, "leverage": 2}})
            effects = [
                _delta_label("进度", actual_progress_delta),
                _delta_label("阻力", int(meta.get("resistance") or 0) - before_resistance, good_positive=False),
                _effect("检定 +6", "good"),
                _effect(f"{label}怨气 -5", "good") if kind == "person" else _effect(f"{label}配合", "good"),
            ]
            if faction and faction not in ("无", "中立"):
                effects.append(_effect(f"{faction}势力 +2", "bad"))
            msg = f"御前协调{label}配合办差（进度+{progress_delta}，阻力{resistance_delta}）。对方得了名分与转圜，相关势力满意与势力小涨。"
        else:
            progress_delta, resistance_delta, score_delta = 10, -15, 4
            actual_progress_delta = min(99, before_progress + progress_delta) - before_progress
            meta["resistance"] = max(0, int(meta.get("resistance") or 0) + resistance_delta)
            meta["score_bonus"] = int(meta.get("score_bonus") or 0) + score_delta
            _remember_blocker_action(
                meta, action=action, label=label, day=day,
                progress_delta=progress_delta, resistance_delta=resistance_delta,
            )
            _save_chain_meta(db, did, meta)
            db.conn.execute(
                "UPDATE turn_directives SET lifecycle_status='executing', "
                "progress=MIN(99, progress+?), anomaly='' WHERE id=?",
                (progress_delta, did),
            )
            if kind == "person":
                try:
                    from ming_sim import court
                    court._adjust_char(db, label, emp_trust=-4, grievance=+9)
                    if assignee and assignee != label:
                        court.adjust_opinion(db, label, assignee, -12, "御前申饬逼令配合", day=day, reciprocal=False)
                        court._adjust_char(db, assignee, emp_trust=+2, grievance=-2)
                except Exception:
                    pass
            if faction and faction not in ("无", "中立"):
                db.adjust_factions({faction: {"satisfaction": -3, "leverage": -2}})
            adjust_belief(db, KV_SHI, +1, f"申饬旨意#{did}阻力：{label}", day=day)
            adjust_belief(db, KV_RISK_AVERSION, +2, f"御前申饬旨意#{did}阻力：{label}", day=day)
            effects = [
                _delta_label("进度", actual_progress_delta),
                _delta_label("阻力", int(meta.get("resistance") or 0) - before_resistance, good_positive=False),
                _effect("势 +1", "good"),
                _effect("任事观望 +2", "bad"),
                _effect(f"{label}怨气 +9", "bad") if kind == "person" else _effect(f"{label}受压", "bad"),
            ]
            if faction and faction not in ("无", "中立"):
                effects.append(_effect(f"{faction}满意 -3", "bad"))
            msg = f"当廷申饬{label}，逼其不得再阻（进度+{progress_delta}，阻力{resistance_delta}，势+）。但被压者怨气与百官观望上升。"
    elif action == "abort":
        db.conn.execute(
            "UPDATE turn_directives SET lifecycle_status='aborted', anomaly='' WHERE id=?", (did,))
        adjust_belief(db, KV_SHI, -3, f"收回成命#{did}（朝令夕改）", day=day)
        adjust_belief(db, KV_RISK_AVERSION, +2, f"旨意#{did}半途而废", day=day)
        effects = [
            _effect("旨意收回", "neutral"),
            _effect("势 -3", "bad"),
            _effect("任事观望 +2", "bad"),
        ]
        msg = "收回成命。诏令反复，势有所损，百官益发观望。"
    else:
        return {"ok": False, "message": f"未知处置：{action}"}
    db.conn.commit()
    db.save_state(state)
    return {"ok": True, "message": msg, "effects": effects}


# ── 查询与推演注入 ───────────────────────────────────────────────────────────

def _intervention_option(
    action: str,
    label: str,
    effects: List[Dict[str, object]],
    *,
    tone: str = "neutral",
    disabled_reason: str = "",
) -> Dict[str, object]:
    item: Dict[str, object] = {
        "action": action,
        "label": label,
        "tone": tone,
        "effects": effects,
    }
    if disabled_reason:
        item["disabled"] = True
        item["disabled_reason"] = disabled_reason
    return item


def _intervention_options(db: GameDB, row, meta: Dict[str, object]) -> List[Dict[str, object]]:
    if str(row["lifecycle_status"] or "") not in LIVE_STATUSES:
        return []
    progress = int(row["progress"] or 0)
    resistance = int(meta.get("resistance") or 0)
    assignee = str(row["assignee"] or "")
    options = [
        _intervention_option(
            "cuiban", "催办",
            [
                _delta_label("进度", min(100, progress + 8) - progress),
                _effect("工期 -4", "good"),
                _effect("主办怨气 +5", "bad"),
                _effect("任事观望 +1", "bad"),
            ],
            tone="warn",
        ),
        _intervention_option(
            "fund", "加拨",
            [
                _delta_label("进度", min(100, progress + 5) - progress),
                _delta_label("阻力", max(0, resistance - 10) - resistance, good_positive=False),
                _effect("检定 +12", "good"),
                _effect("国库 -10万", "bad"),
            ],
            tone="info",
        ),
        _intervention_option(
            "ducai", "独断",
            [
                _delta_label("进度", min(100, progress + 25) - progress),
                _delta_label("阻力", max(0, resistance - 20) - resistance, good_positive=False),
                _effect("检定 +20", "good"),
                _effect("任事观望 +3", "bad"),
            ],
            tone="danger",
            disabled_reason="" if _can_ducai_for_preview(db) else "势不足七十",
        ),
        _intervention_option(
            "abort", "收回",
            [_effect("旨意收回", "neutral"), _effect("势 -3", "bad"), _effect("任事观望 +2", "bad")],
            tone="danger",
        ),
    ]

    clue = _blocker_clue(meta)
    label = _blocker_label(clue)
    if label:
        kind = str(clue.get("kind") or "")
        faction = _blocker_faction(db, clue)
        bargain_effects = [
            _delta_label("进度", min(99, progress + 6) - progress),
            _delta_label("阻力", max(0, resistance - 12) - resistance, good_positive=False),
            _effect("检定 +6", "good"),
            _effect(f"{label}怨气 -5", "good") if kind == "person" else _effect(f"{label}配合", "good"),
        ]
        if faction and faction not in ("无", "中立"):
            bargain_effects.append(_effect(f"{faction}势力 +2", "bad"))
        options.append(_intervention_option("bargain_blocker", "协调阻力", bargain_effects, tone="warn"))

        pressure_effects = [
            _delta_label("进度", min(99, progress + 10) - progress),
            _delta_label("阻力", max(0, resistance - 15) - resistance, good_positive=False),
            _effect("势 +1", "good"),
            _effect("任事观望 +2", "bad"),
            _effect(f"{label}怨气 +9", "bad") if kind == "person" else _effect(f"{label}受压", "bad"),
        ]
        if faction and faction not in ("无", "中立"):
            pressure_effects.append(_effect(f"{faction}满意 -3", "bad"))
        options.append(_intervention_option("pressure_blocker", "申饬阻力", pressure_effects, tone="danger"))

    if assignee:
        options.append(
            _intervention_option(
                "reassign", "换人",
                [_effect("须择新主办", "neutral"), _delta_label("进度", max(0, progress - 15) - progress), _effect(f"{assignee}怨气 +10", "bad")],
                tone="warn",
            )
        )
    return options


def _can_ducai_for_preview(db: GameDB) -> bool:
    try:
        from ming_sim.theater import can_ducai
        return bool(can_ducai(db))
    except Exception:
        return False


def lifecycle_payload(db: GameDB, *, include_done: bool = True, limit: int = 40) -> List[Dict[str, object]]:
    """前端指令进度面板。注意：integrity 只暴露 reported（账面），actual 不出 API（S3）。"""
    sql = ("SELECT id, text, lifecycle_status, category, progress, lead_days, exec_days, "
           "start_day, eta_day, assignee, chain, integrity_reported, anomaly, settle_note, "
           "outcome_delta, outcome_status "
           "FROM turn_directives WHERE lifecycle_status!=''")
    if not include_done:
        sql += " AND lifecycle_status IN ('in_transit','executing','stalled')"
    sql += " ORDER BY id DESC LIMIT ?"
    statecraft: Dict[str, object] = {}
    try:
        from ming_sim.bureaucracy import organization_diagnostics
        from ming_sim.fiscal_center import fiscal_center_payload
        from ming_sim.statecraft_center import statecraft_center_payload
        state = db.load_state()
        fiscal = fiscal_center_payload(db, state)
        organization = organization_diagnostics(db)
        statecraft = statecraft_center_payload(db, state, fiscal=fiscal, organization=organization)
    except Exception:
        statecraft = {}
    out = []
    for row in db.conn.execute(sql, (int(limit),)).fetchall():
        meta = _chain_meta(row)
        statecraft_preflight: Dict[str, object] = {}
        if statecraft:
            try:
                from ming_sim.statecraft_center import directive_statecraft_preflight
                statecraft_preflight = directive_statecraft_preflight(str(row["text"] or ""), statecraft)
            except Exception:
                statecraft_preflight = {}
        out.append({
            "id": int(row["id"]),
            "text": str(row["text"] or ""),
            "status": str(row["lifecycle_status"]),
            "category": str(row["category"] or ""),
            "progress": int(row["progress"]),
            "assignee": str(row["assignee"] or ""),
            "start_day": int(row["start_day"]),
            "eta_day": int(row["eta_day"]),
            "resistance": int(meta.get("resistance") or 0),
            "chain": meta.get("chain") or [],
            "blocker_clue": meta.get("blocker_clue") if isinstance(meta.get("blocker_clue"), dict) else {},
            "blocker_action": meta.get("last_blocker_action") if isinstance(meta.get("last_blocker_action"), dict) else {},
            "followup_action": meta.get("last_followup_action") if isinstance(meta.get("last_followup_action"), dict) else {},
            "followup_history": _followup_history(meta),
            "policy_doctrine": meta.get("policy_doctrine") if isinstance(meta.get("policy_doctrine"), dict) else {},
            "statecraft_preflight": statecraft_preflight,
            "reported_rate": int(row["integrity_reported"]),
            "anomaly": str(row["anomaly"] or ""),
            "settle_note": str(row["settle_note"] or ""),
            "outcome_status": str(row["outcome_status"] or ""),
            "outcome_summary": _outcome_summary(db, _json_dict(row["outcome_delta"])),
            "intervention_options": _intervention_options(db, row, meta),
        })
    return out


def executing_directives_brief(db: GameDB) -> List[Dict[str, object]]:
    """喂 simulator：执行中旨意清单（效果未落地，邸报不得按已完成叙述）。"""
    return [
        {"id": item["id"], "text": item["text"][:80], "status": item["status"],
         "progress": item["progress"], "assignee": item["assignee"]}
        for item in lifecycle_payload(db, include_done=False, limit=20)
    ]
