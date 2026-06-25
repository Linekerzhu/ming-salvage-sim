"""LLM-backed dialogue audit for goals, stance, and agreements.

This module is the semantic authority for summons after the dialogue-goal
refactor. Regex helpers may still exist elsewhere for legacy display, but new
goal/stance/agreement state should be derived from these audited JSON objects or
not recorded at all.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from agno.agent import Agent

from ming_sim.agents import parse_agent_json, run_agent_text
from ming_sim.context import (
    npc_dialogue_behavior_brief,
    npc_dialogue_behavior_profile,
    npc_network_profile,
    npc_network_recommendations,
)
from ming_sim.llm_config import for_role as llm_for_role
from ming_sim.llm_model import create_chat_model
from ming_sim.models import Character, GameState, LLMConfig
from ming_sim.pipeline_registry import llm_output_token_budget


CONFIDENCE_FLOOR = 70

GOAL_DECISIONS = {"none", "continue", "new", "switch", "abandon"}
GOAL_RELATIONS = {"none", "same_goal", "refine_goal", "distinct_goal", "abandon_goal"}
ACTION_KINDS = {
    "general",
    "personnel",
    "secret_order",
    "policy",
    "court_commitment",
    "castration",
    "emancipation",
}
STANCES = {"support", "caution", "oppose", "neutral"}
HANDSHAKES = {"none", "conditional", "sealed", "blocked"}
GOAL_STATUSES = {"active", "waiting_conditions", "sealed", "blocked", "abandoned", "expired"}
AGREEMENT_ACTIONS = {"none", "create_achieved", "create_pending", "bind_existing"}
DIRECTIVE_ACTIONS = {"none", "propose_pending"}
INSTANT_AGREEMENT_ACTIONS = {"castration", "emancipation", "personnel"}
IDENTITY_CONVERSION_ACTIONS = {"castration", "emancipation"}
RECRUITMENT_KINDS = {"eunuch", "exam", "recommend"}
ACTION_INTENT_TYPES = {
    "none",
    "secret_order",
    "recruitment",
    "mediation",
    "castration",
    "custody",
    "punishment",
    "condition_update",
    "office_change",
    "eunuch_care",
    "eunuch_hard_service",
    "bao_leverage",
}
ACTION_INTENT_PHASES = {"none", "propose", "confirm", "reject"}
DIALOGUE_ROUTE_INTENTS = {"none", "summon", "confirm_pending", "reject_pending"}
BARGAIN_ATTITUDES = {"none", "accept", "press", "refuse"}
DIRECTIVE_PRESSURE_KINDS = {"none", "pressed", "needs_support", "evasive"}
DIRECTIVE_FOLLOWUP_KINDS = {"none", "rewarded", "accounted", "followup_evasive", "next_step", "reviewed"}
RECOVERABLE_DIALOGUE_ACTION_TYPES = {
    "recruitment",
    "mediation",
    "castration",
    "eunuch_care",
    "eunuch_hard_service",
    "bao_leverage",
}
SOFT_HOOK_RE = re.compile(
    r"旧恩|人情债|昔日|朕曾|朕已|朕替|朕保|保全|复用|买单|抚恤|"
    r"两清|恩典|恩赏|天恩|旧情"
)
COMMITMENT_RE = re.compile(
    r"臣愿|奴婢愿|奴才愿|小的愿|愿为陛下|臣领旨|奴婢领旨|奴才领旨|"
    r"遵旨|愿领|愿奉旨|敢不奉行|臣当奉行|臣愿担此|奴婢愿办|奴婢愿替陛下|愿承办|愿效力"
)
COERCED_SUBMISSION_RE = re.compile(
    r"不敢不从|不敢违旨|伏罪|认罪|愿供|如实供|据实供|任凭陛下|任凭发落|"
    r"臣领旨|遵旨|臣遵旨|敢不奉行|愿奉旨|愿照旨|愿按旨|臣当奉行"
)
COERCED_REFUSAL_RE = re.compile(
    r"万死不从|断不可|不能从命|恕难从命|宁死不从|臣不敢奉|臣不能奉|不可奉行"
)


def _compact(text: object, limit: int = 240) -> str:
    return " ".join(str(text or "").strip().split())[:limit]


def _clamp_int(value: object, low: int = 0, high: int = 100, default: int = 0) -> int:
    try:
        parsed = int(round(float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        parsed = default
    return max(low, min(high, parsed))


def _enum(value: object, allowed: set[str], default: str) -> str:
    clean = str(value or "").strip()
    return clean if clean in allowed else default


def _list_strings(value: object, *, limit: int = 8, item_limit: int = 160) -> List[str]:
    if isinstance(value, str):
        raw_items: List[object] = [value] if value.strip() else []
    elif isinstance(value, list):
        raw_items = value
    else:
        raw_items = []
    out: List[str] = []
    for item in raw_items:
        text = _compact(item, item_limit)
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _conditions(value: object) -> List[Dict[str, str]]:
    if not isinstance(value, list):
        return []
    out: List[Dict[str, str]] = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        desc = _compact(raw.get("description"), 180)
        if not desc:
            continue
        status = _enum(raw.get("status"), {"pending", "done", "failed"}, "pending")
        evidence = _compact(raw.get("evidence"), 240)
        item = {"description": desc, "status": status, "evidence": evidence}
        if item not in out:
            out.append(item)
        if len(out) >= 8:
            break
    return out


def _row_dicts(rows: object, *, limit: int = 8) -> List[Dict[str, object]]:
    out: List[Dict[str, object]] = []
    if not isinstance(rows, list):
        return out
    for row in rows[:limit]:
        if isinstance(row, dict):
            clean: Dict[str, object] = {}
            for key, value in row.items():
                if key in {"conditions_json", "blockers_json", "last_delta_json", "psychological_json"}:
                    continue
                if isinstance(value, (str, int, float, bool)) or value is None:
                    clean[str(key)] = value
                elif isinstance(value, list):
                    clean[str(key)] = value[:6]
                elif isinstance(value, dict):
                    clean[str(key)] = value
            out.append(clean)
    return out


def _soft_hook_invoked(text: object) -> bool:
    return bool(SOFT_HOOK_RE.search(str(text or "")))


def _answer_has_commitment(text: object) -> bool:
    return bool(COMMITMENT_RE.search(str(text or "")))


def _answer_has_coerced_submission(text: object) -> bool:
    raw = str(text or "")
    return bool(COERCED_SUBMISSION_RE.search(raw)) and not bool(COERCED_REFUSAL_RE.search(raw))


def _payload_favor_rows(payload: Dict[str, object]) -> List[Dict[str, object]]:
    rows = payload.get("favor_memories")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _payload_custody_rows(payload: Dict[str, object]) -> List[Dict[str, object]]:
    rows = payload.get("active_custodies")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _goal_last_delta(goal: Dict[str, object]) -> Dict[str, object]:
    raw = goal.get("last_delta")
    if isinstance(raw, dict):
        return dict(raw)
    raw = goal.get("last_delta_json")
    try:
        parsed = json.loads(str(raw or "{}"))
    except (TypeError, ValueError):
        parsed = {}
    return parsed if isinstance(parsed, dict) else {}


def _identity_consent_from_goal(goal: Dict[str, object]) -> tuple[bool, str]:
    last_delta = _goal_last_delta(goal)
    candidates: List[Dict[str, object]] = [last_delta]
    audit = last_delta.get("audit")
    if isinstance(audit, dict):
        candidates.append(audit)
    for candidate in candidates:
        if not bool(candidate.get("explicit_consent")):
            continue
        evidence_parts = [
            _compact(candidate.get("private_reason"), 220),
            _compact(candidate.get("public_hint"), 160),
            _compact(candidate.get("reason"), 220),
        ]
        for item in candidate.get("conditions") or []:
            if isinstance(item, dict):
                evidence_parts.append(_compact(item.get("evidence"), 180))
        evidence = "；".join(part for part in evidence_parts if part)
        return True, evidence[:520]
    return False, ""


def _recent_dialogue_rows(db: Any, minister_name: str, *, limit: int = 12) -> List[Dict[str, object]]:
    conn = getattr(db, "conn", None)
    if conn is None:
        return []
    current_day = 0
    try:
        current_day = int(db._current_chat_day(0))  # type: ignore[attr-defined]
    except Exception:
        current_day = 0
    try:
        rows = conn.execute(
            """
            SELECT turn, day, role, content
            FROM chat_messages
            WHERE minister_name=?
            ORDER BY id DESC
            LIMIT ?
            """,
            (str(minister_name or "").strip(), max(1, min(24, int(limit or 12)))),
        ).fetchall()
    except Exception:
        try:
            rows = conn.execute(
                """
                SELECT turn, role, content
                FROM chat_messages
                WHERE minister_name=?
                ORDER BY id DESC
                LIMIT ?
                """,
                (str(minister_name or "").strip(), max(1, min(24, int(limit or 12)))),
            ).fetchall()
        except Exception:
            return []
    out: List[Dict[str, object]] = []
    for row in reversed(rows):
        item: Dict[str, object] = {
            "turn": int(row["turn"] or 0),
            "role": str(row["role"] or ""),
            "content": _compact(row["content"], 700),
        }
        if "day" in row.keys():
            day = int(row["day"] or 0)
            item["day"] = day
            if current_day > 0 and day > 0:
                item["days_ago"] = max(0, current_day - day)
        out.append(item)
    return out


@dataclass
class PreDialogueAudit:
    audit_status: str = "not_recorded"
    goal_decision: str = "none"
    goal_relation: str = "none"
    action_kind: str = "general"
    title: str = ""
    target_text: str = ""
    confidence: int = 0
    public_hint: str = ""
    private_reason: str = ""
    npc_guidance: str = ""
    raw: Dict[str, object] = field(default_factory=dict)
    error: str = ""

    @property
    def valid(self) -> bool:
        return self.audit_status == "recorded"

    @property
    def has_goal(self) -> bool:
        return self.valid and self.goal_decision in {"continue", "new", "switch"} and self.action_kind != "general" and bool(self.title or self.target_text)

    @property
    def abandon(self) -> bool:
        return self.valid and self.goal_decision == "abandon"


@dataclass
class PostDialogueAudit:
    audit_status: str = "not_recorded"
    goal_decision: str = "none"
    goal_relation: str = "none"
    action_kind: str = "general"
    title: str = ""
    target_text: str = ""
    stance: str = "neutral"
    handshake_status: str = "none"
    goal_status: str = "active"
    score_delta: int = 0
    score_after: int = 0
    threshold: int = 70
    conditions: List[Dict[str, str]] = field(default_factory=list)
    tasks: List[str] = field(default_factory=list)
    blockers: List[str] = field(default_factory=list)
    explicit_consent: bool = False
    agreement_action: str = "none"
    directive_action: str = "none"
    directive_text: str = ""
    public_hint: str = ""
    private_reason: str = ""
    confidence: int = 0
    raw: Dict[str, object] = field(default_factory=dict)
    error: str = ""

    @property
    def valid(self) -> bool:
        return self.audit_status == "recorded"

    @property
    def has_goal(self) -> bool:
        return self.valid and self.goal_decision in {"continue", "new", "switch"} and bool(self.title or self.target_text)


def _audit_failure(error: str, *, raw: Optional[Dict[str, object]] = None) -> PreDialogueAudit:
    return PreDialogueAudit(audit_status="not_recorded", error=_compact(error, 180), raw=raw or {})


def _post_failure(error: str, *, raw: Optional[Dict[str, object]] = None) -> PostDialogueAudit:
    return PostDialogueAudit(audit_status="not_recorded", error=_compact(error, 180), raw=raw or {})


def _normalize_pre(data: Dict[str, object]) -> PreDialogueAudit:
    decision = _enum(data.get("goal_decision"), GOAL_DECISIONS, "none")
    default_relation = {
        "none": "none",
        "continue": "same_goal",
        "new": "distinct_goal",
        "switch": "distinct_goal",
        "abandon": "abandon_goal",
    }.get(decision, "none")
    relation = _enum(data.get("goal_relation"), GOAL_RELATIONS, default_relation)
    action_kind = _enum(data.get("action_kind"), ACTION_KINDS, "general")
    confidence = _clamp_int(data.get("confidence"))
    if decision != "none" and confidence < CONFIDENCE_FLOOR:
        return _audit_failure(f"审计置信度不足：{confidence}", raw=data)
    if decision in {"new", "continue", "switch"} and action_kind == "general":
        return _audit_failure("目的类 action_kind 不可为 general", raw=data)
    title = _compact(data.get("title"), 120)
    target_text = _compact(data.get("target_text"), 240)
    if decision in {"new", "continue", "switch"} and not (title or target_text):
        return _audit_failure("目的缺少 title/target_text", raw=data)
    return PreDialogueAudit(
        audit_status="recorded",
        goal_decision=decision,
        goal_relation=relation,
        action_kind=action_kind,
        title=title,
        target_text=target_text,
        confidence=confidence,
        public_hint=_compact(data.get("public_hint"), 160),
        private_reason=_compact(data.get("private_reason") or data.get("reason"), 300),
        npc_guidance=_compact(data.get("npc_guidance") or data.get("guidance"), 600),
        raw=data,
    )


def _normalize_post(data: Dict[str, object], *, existing_threshold: int = 70) -> PostDialogueAudit:
    decision = _enum(data.get("goal_decision"), GOAL_DECISIONS, "none")
    default_relation = {
        "none": "none",
        "continue": "same_goal",
        "new": "distinct_goal",
        "switch": "distinct_goal",
        "abandon": "abandon_goal",
    }.get(decision, "none")
    relation = _enum(data.get("goal_relation"), GOAL_RELATIONS, default_relation)
    action_kind = _enum(data.get("action_kind"), ACTION_KINDS, "general")
    confidence = _clamp_int(data.get("confidence"))
    if decision != "none" and confidence < CONFIDENCE_FLOOR:
        return _post_failure(f"审计置信度不足：{confidence}", raw=data)
    stance = _enum(data.get("stance"), STANCES, "neutral")
    handshake = _enum(data.get("handshake_status"), HANDSHAKES, "none")
    goal_status = _enum(data.get("goal_status"), GOAL_STATUSES, "active")
    threshold = _clamp_int(data.get("threshold"), 1, 100, max(1, min(100, int(existing_threshold or 70))))
    score_after = _clamp_int(data.get("score_after"), 0, 100)
    score_delta = _clamp_int(data.get("score_delta"), -100, 100)
    conditions = _conditions(data.get("conditions"))
    tasks = _list_strings(data.get("tasks"), limit=8, item_limit=180)
    blockers = _list_strings(data.get("blockers"), limit=8, item_limit=120)
    explicit_consent = bool(data.get("explicit_consent"))
    agreement_action = _enum(data.get("agreement_action"), AGREEMENT_ACTIONS, "none")
    agreement_formed = bool(data.get("agreement_formed"))
    performance_status = str(data.get("performance_status") or "").strip()
    directive_action = _enum(data.get("directive_action"), DIRECTIVE_ACTIONS, "none")
    directive_text = _compact(data.get("directive_text"), 1800)
    public_hint = _compact(data.get("public_hint"), 180)
    private_reason = _compact(data.get("private_reason") or data.get("reason"), 400)

    if decision in {"new", "continue", "switch"} and action_kind == "general":
        return _post_failure("目的类 action_kind 不可为 general", raw=data)

    pending_conditions = [item for item in conditions if item.get("status") == "pending"]
    failed_conditions = [item for item in conditions if item.get("status") == "failed"]
    if agreement_formed and not failed_conditions:
        goal_status = "sealed"
        handshake = "sealed"
        if agreement_action == "none":
            agreement_action = "create_achieved" if performance_status == "fulfilled" else "create_pending"

    def guard_failed_seal() -> None:
        nonlocal goal_status, handshake, agreement_action, score_after
        if goal_status == "sealed" and failed_conditions:
            goal_status = "blocked"
            handshake = "blocked"
            agreement_action = "none"
            score_after = min(score_after, threshold - 1)
            if "条件审计判定有条件失败，不能握手达成" not in blockers:
                blockers.append("条件审计判定有条件失败，不能握手达成")

    guard_failed_seal()

    if goal_status == "waiting_conditions":
        handshake = "conditional"
        agreement_action = "none"
        if not conditions:
            blockers.append("等待条件但审计未给出条件")
    elif goal_status == "sealed":
        handshake = "sealed"
        score_after = 100
        if pending_conditions and not tasks:
            tasks = [
                str(item.get("description") or "").strip()
                for item in pending_conditions
                if str(item.get("description") or "").strip()
            ]
        if action_kind in INSTANT_AGREEMENT_ACTIONS and not tasks and not pending_conditions:
            agreement_action = "create_achieved" if agreement_action == "none" else agreement_action
        elif agreement_action in {"none", "create_achieved"}:
            agreement_action = "create_pending"
    elif goal_status == "blocked":
        handshake = "blocked"
        agreement_action = "none"
    elif goal_status in {"active", "abandoned", "expired"}:
        if handshake == "sealed":
            goal_status = "sealed"
        elif handshake == "conditional":
            goal_status = "waiting_conditions"
        else:
            agreement_action = "none"

    guard_failed_seal()

    if action_kind in IDENTITY_CONVERSION_ACTIONS and goal_status == "sealed":
        consent_evidence = " ".join(
            [public_hint, private_reason, *[str(item.get("evidence") or "") for item in conditions]]
        ).strip()
        if not explicit_consent or not consent_evidence:
            goal_status = "blocked"
            handshake = "blocked"
            agreement_action = "none"
            score_after = min(score_after, threshold - 1)
            if action_kind == "castration":
                blockers.append("身份转换缺少明确自愿入内廷证据")
            else:
                blockers.append("身份转换缺少明确自愿脱籍证据")

    if goal_status != "sealed":
        agreement_action = "none" if agreement_action.startswith("create_") else agreement_action
    if goal_status == "sealed" and score_after < threshold:
        score_after = 100
    if directive_action == "propose_pending":
        if not directive_text:
            directive_action = "none"
        elif confidence < CONFIDENCE_FLOOR:
            directive_action = "none"

    return PostDialogueAudit(
        audit_status="recorded",
        goal_decision=decision,
        goal_relation=relation,
        action_kind=action_kind,
        title=_compact(data.get("title"), 120),
        target_text=_compact(data.get("target_text"), 240),
        stance=stance,
        handshake_status=handshake,
        goal_status=goal_status,
        score_delta=score_delta,
        score_after=score_after,
        threshold=threshold,
        conditions=conditions,
        tasks=tasks,
        blockers=blockers[:8],
        explicit_consent=explicit_consent,
        agreement_action=agreement_action,
        directive_action=directive_action,
        directive_text=directive_text,
        public_hint=public_hint,
        private_reason=private_reason,
        confidence=confidence,
        raw=data,
    )


def _normalize_recruitment_intent(data: Dict[str, object]) -> Dict[str, object]:
    kind = _enum(data.get("kind"), RECRUITMENT_KINDS, "")
    raw_confidence = data.get("confidence")
    try:
        parsed_confidence = float(raw_confidence)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        parsed_confidence = 0.0
    if 0 < parsed_confidence <= 1:
        confidence = _clamp_int(parsed_confidence * 100)
    else:
        confidence = _clamp_int(raw_confidence)
    allow = bool(data.get("allow")) and kind in RECRUITMENT_KINDS and confidence >= CONFIDENCE_FLOOR
    return {
        "allow": allow,
        "kind": kind,
        "confidence": confidence,
        "phase": _enum(data.get("phase"), {"propose", "confirm", "none"}, "none"),
        "requires_confirmation": bool(data.get("requires_confirmation", True)),
        "trigger_quote": _compact(data.get("trigger_quote"), 120),
        "public_hint": _compact(data.get("public_hint"), 180),
        "private_reason": _compact(data.get("private_reason"), 500),
        "raw": data,
    }


def _normalize_dialogue_action_intent(data: Dict[str, object]) -> Dict[str, object]:
    action_type = _enum(data.get("action_type") or data.get("type"), ACTION_INTENT_TYPES, "none")
    phase = _enum(data.get("phase"), ACTION_INTENT_PHASES, "none")
    raw_confidence = data.get("confidence")
    try:
        parsed_confidence = float(raw_confidence)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        parsed_confidence = 0.0
    if 0 < parsed_confidence <= 1:
        confidence = _clamp_int(parsed_confidence * 100)
    else:
        confidence = _clamp_int(raw_confidence)
    allow = bool(data.get("allow")) and action_type != "none" and phase in {"propose", "confirm", "reject"} and confidence >= CONFIDENCE_FLOOR
    if phase == "reject" and action_type == "none" and confidence >= CONFIDENCE_FLOOR:
        allow = bool(data.get("allow"))
    return {
        "allow": allow,
        "phase": phase if allow else "none",
        "action_type": action_type if allow else "none",
        "confidence": confidence,
        "requires_confirmation": bool(data.get("requires_confirmation", phase == "propose")),
        "target": _compact(data.get("target"), 80),
        "actor": _compact(data.get("actor"), 80),
        "faction": _compact(data.get("faction"), 80),
        "kind": _compact(data.get("kind"), 40),
        "mode": _compact(data.get("mode"), 40),
        "trigger_quote": _compact(data.get("trigger_quote"), 140),
        "public_hint": _compact(data.get("public_hint"), 180),
        "private_reason": _compact(data.get("private_reason") or data.get("reason"), 520),
        "payload": data.get("payload") if isinstance(data.get("payload"), dict) else {},
        "character_status_changes": data.get("character_status_changes") if isinstance(data.get("character_status_changes"), list) else [],
        "condition_changes": data.get("condition_changes") if isinstance(data.get("condition_changes"), list) else [],
        "punishment_changes": data.get("punishment_changes") if isinstance(data.get("punishment_changes"), list) else [],
        "raw": data,
    }


def _normalize_dialogue_suggestions(data: object) -> List[Dict[str, object]]:
    rows = data.get("suggestions") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        return []
    out: List[Dict[str, object]] = []
    banned_label_terms = (
        "交账",
        "问奖励",
        "交易",
        "定下一手",
        "快捷",
        "按钮",
        "拟旨",
        "下密令",
        "系统",
        "机制",
        "确认",
        "取消",
        "提交",
    )
    banned_text_terms = ("快速对话", "快捷", "按钮", "系统", "机制", "御前交易", "点击", "提交")
    for row in rows:
        if not isinstance(row, dict):
            continue
        label = _compact(row.get("label"), 8)
        text = _compact(row.get("text"), 160)
        if not label or not text:
            continue
        if any(term in label for term in banned_label_terms):
            continue
        if any(term in text for term in banned_text_terms):
            continue
        item = {
            "label": label,
            "text": text,
            "prefix": bool(row.get("prefix", True)),
        }
        if item not in out:
            out.append(item)
        if len(out) >= 5:
            break
    return out


def _normalize_dialogue_eunuch_lore_intake(data: Dict[str, object]) -> Dict[str, object]:
    raw_confidence = data.get("confidence")
    try:
        parsed_confidence = float(raw_confidence)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        parsed_confidence = 0.0
    if 0 < parsed_confidence <= 1:
        confidence = _clamp_int(parsed_confidence * 100)
    else:
        confidence = _clamp_int(raw_confidence)
    target_names = _list_strings(data.get("target_names") or data.get("targets"), limit=4, item_limit=80)
    allow = bool(data.get("allow")) and bool(target_names) and confidence >= CONFIDENCE_FLOOR
    return {
        "allow": allow,
        "target_names": target_names if allow else [],
        "confidence": confidence,
        "trigger_quote": _compact(data.get("trigger_quote"), 140),
        "public_hint": _compact(data.get("public_hint"), 180),
        "private_reason": _compact(data.get("private_reason") or data.get("reason"), 520),
        "raw": data,
    }


def _normalize_dialogue_unknown_mention_intake(data: Dict[str, object]) -> Dict[str, object]:
    raw_confidence = data.get("confidence")
    try:
        parsed_confidence = float(raw_confidence)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        parsed_confidence = 0.0
    if 0 < parsed_confidence <= 1:
        confidence = _clamp_int(parsed_confidence * 100)
    else:
        confidence = _clamp_int(raw_confidence)
    accepted_names = _list_strings(data.get("accepted_names") or data.get("names"), limit=8, item_limit=80)
    allow = bool(data.get("allow")) and bool(accepted_names) and confidence >= CONFIDENCE_FLOOR
    return {
        "allow": allow,
        "accepted_names": accepted_names if allow else [],
        "rejected_names": _list_strings(data.get("rejected_names"), limit=8, item_limit=80),
        "confidence": confidence,
        "trigger_quote": _compact(data.get("trigger_quote"), 160),
        "public_hint": _compact(data.get("public_hint"), 180),
        "private_reason": _compact(data.get("private_reason") or data.get("reason"), 520),
        "raw": data,
    }


def _normalize_dialogue_route_intent(data: Dict[str, object]) -> Dict[str, object]:
    intent = _enum(data.get("intent"), DIALOGUE_ROUTE_INTENTS, "none")
    raw_confidence = data.get("confidence")
    try:
        parsed_confidence = float(raw_confidence)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        parsed_confidence = 0.0
    if 0 < parsed_confidence <= 1:
        confidence = _clamp_int(parsed_confidence * 100)
    else:
        confidence = _clamp_int(raw_confidence)
    allow = bool(data.get("allow")) and intent != "none" and confidence >= CONFIDENCE_FLOOR
    return {
        "allow": allow,
        "intent": intent if allow else "none",
        "confidence": confidence,
        "target_name": _compact(data.get("target_name"), 80),
        "target_reference": _compact(data.get("target_reference"), 80),
        "action_type": _compact(data.get("action_type"), 40),
        "trigger_quote": _compact(data.get("trigger_quote"), 140),
        "public_hint": _compact(data.get("public_hint"), 180),
        "private_reason": _compact(data.get("private_reason") or data.get("reason"), 520),
        "raw": data,
    }


def _normalize_dialogue_pending_recovery(data: Dict[str, object]) -> Dict[str, object]:
    action_type = _enum(data.get("action_type") or data.get("type"), RECOVERABLE_DIALOGUE_ACTION_TYPES, "")
    raw_confidence = data.get("confidence")
    try:
        parsed_confidence = float(raw_confidence)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        parsed_confidence = 0.0
    if 0 < parsed_confidence <= 1:
        confidence = _clamp_int(parsed_confidence * 100)
    else:
        confidence = _clamp_int(raw_confidence)
    allow = bool(data.get("allow")) and action_type in RECOVERABLE_DIALOGUE_ACTION_TYPES and confidence >= CONFIDENCE_FLOOR
    kind = _enum(data.get("kind"), RECRUITMENT_KINDS, "")
    if action_type == "recruitment" and kind not in RECRUITMENT_KINDS:
        allow = False
    return {
        "allow": allow,
        "phase": "confirm" if allow else "none",
        "action_type": action_type if allow else "none",
        "kind": kind if allow else "",
        "target": _compact(data.get("target"), 80),
        "actor": _compact(data.get("actor"), 80),
        "faction": _compact(data.get("faction"), 80),
        "mode": _compact(data.get("mode"), 40),
        "trigger_quote": _compact(data.get("trigger_quote"), 140),
        "proposal_evidence": _compact(data.get("proposal_evidence"), 360),
        "public_hint": _compact(data.get("public_hint"), 180),
        "private_reason": _compact(data.get("private_reason") or data.get("reason"), 520),
        "confidence": confidence,
        "raw": data,
    }


def _normalize_dialogue_bargain_attitude(data: Dict[str, object]) -> Dict[str, object]:
    attitude = _enum(data.get("attitude"), BARGAIN_ATTITUDES, "none")
    raw_confidence = data.get("confidence")
    try:
        parsed_confidence = float(raw_confidence)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        parsed_confidence = 0.0
    if 0 < parsed_confidence <= 1:
        confidence = _clamp_int(parsed_confidence * 100)
    else:
        confidence = _clamp_int(raw_confidence)
    allow = bool(data.get("allow")) and attitude != "none" and confidence >= CONFIDENCE_FLOOR
    return {
        "allow": allow,
        "attitude": attitude if allow else "none",
        "confidence": confidence,
        "trigger_quote": _compact(data.get("trigger_quote"), 140),
        "public_hint": _compact(data.get("public_hint"), 180),
        "private_reason": _compact(data.get("private_reason") or data.get("reason"), 520),
        "raw": data,
    }


def _normalize_dialogue_directive_fallback(data: Dict[str, object]) -> Dict[str, object]:
    raw_confidence = data.get("confidence")
    try:
        parsed_confidence = float(raw_confidence)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        parsed_confidence = 0.0
    if 0 < parsed_confidence <= 1:
        confidence = _clamp_int(parsed_confidence * 100)
    else:
        confidence = _clamp_int(raw_confidence)
    subject = _compact(data.get("subject"), 180)
    directive_text = _compact(data.get("directive_text"), 1600)
    allow = bool(data.get("allow")) and confidence >= CONFIDENCE_FLOOR and (bool(subject) or bool(directive_text))
    return {
        "allow": allow,
        "subject": subject if allow else "",
        "directive_text": directive_text if allow else "",
        "confidence": confidence,
        "trigger_quote": _compact(data.get("trigger_quote"), 140),
        "public_hint": _compact(data.get("public_hint"), 180),
        "private_reason": _compact(data.get("private_reason") or data.get("reason"), 520),
        "raw": data,
    }


def _normalize_dialogue_directive_pressure(data: Dict[str, object]) -> Dict[str, object]:
    kind = _enum(data.get("kind"), DIRECTIVE_PRESSURE_KINDS, "none")
    raw_confidence = data.get("confidence")
    try:
        parsed_confidence = float(raw_confidence)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        parsed_confidence = 0.0
    if 0 < parsed_confidence <= 1:
        confidence = _clamp_int(parsed_confidence * 100)
    else:
        confidence = _clamp_int(raw_confidence)
    allow = bool(data.get("allow")) and kind != "none" and confidence >= CONFIDENCE_FLOOR
    return {
        "allow": allow,
        "kind": kind if allow else "none",
        "forceful": bool(data.get("forceful")),
        "confidence": confidence,
        "trigger_quote": _compact(data.get("trigger_quote"), 140),
        "answer_evidence": _compact(data.get("answer_evidence"), 240),
        "public_hint": _compact(data.get("public_hint"), 180),
        "private_reason": _compact(data.get("private_reason") or data.get("reason"), 520),
        "raw": data,
    }


def _normalize_dialogue_directive_followup(data: Dict[str, object]) -> Dict[str, object]:
    kind = _enum(data.get("kind"), DIRECTIVE_FOLLOWUP_KINDS, "none")
    raw_confidence = data.get("confidence")
    try:
        parsed_confidence = float(raw_confidence)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        parsed_confidence = 0.0
    if 0 < parsed_confidence <= 1:
        confidence = _clamp_int(parsed_confidence * 100)
    else:
        confidence = _clamp_int(raw_confidence)
    allow = bool(data.get("allow")) and kind != "none" and confidence >= CONFIDENCE_FLOOR
    return {
        "allow": allow,
        "kind": kind if allow else "none",
        "confidence": confidence,
        "trigger_quote": _compact(data.get("trigger_quote"), 140),
        "answer_evidence": _compact(data.get("answer_evidence"), 240),
        "public_hint": _compact(data.get("public_hint"), 180),
        "private_reason": _compact(data.get("private_reason") or data.get("reason"), 520),
        "raw": data,
    }


def _context_payload(db: Any, state: GameState, character: Character, *, active_goal: Optional[Dict[str, object]] = None) -> Dict[str, object]:
    try:
        goals = db.list_conversation_goals(minister_name=character.name, limit=8)
    except Exception:
        goals = []
    open_goals = [
        goal for goal in goals
        if str(goal.get("status") or "") in {"active", "waiting_conditions", "blocked", "expired"}
    ]
    completed_goals = [
        goal for goal in goals
        if str(goal.get("status") or "") == "sealed"
    ]
    try:
        agreements = db.list_negotiation_agreements(minister_name=character.name, limit=8)
    except Exception:
        agreements = []
    try:
        issues = db.list_active_issues()[:12]
    except Exception:
        issues = []
    try:
        network = npc_network_recommendations(character.name, db=db, limit=12)
    except Exception:
        network = []
    try:
        relation_network = npc_network_profile(character.name, db=db, limit=12)
    except Exception:
        relation_network = {}
    try:
        from ming_sim.court import favor_memories
        favors = favor_memories(db, character.name, limit=3)
    except Exception:
        favors = []
    try:
        from ming_sim.custody import list_custodies
        active_custodies = list_custodies(db, character.name, active_only=True)
    except Exception:
        active_custodies = []
    try:
        from ming_sim.upgrade_schema import get_current_day
        current_day = int(get_current_day(db, int(state.turn)))
        temporal = db.audience_temporal_context(
            character.name,
            current_turn=int(state.turn),
            current_day=current_day,
            exclude_current_user=True,
        )
    except Exception:
        temporal = {}
    return {
        "turn": {"year": state.year, "period": state.period, "turn": state.turn},
        "audience_temporal_context": temporal if isinstance(temporal, dict) else {},
        "npc": {
            "name": character.name,
            "office": character.office,
            "office_type": character.office_type,
            "faction": character.faction,
            "loyalty": character.loyalty,
            "ability": character.ability,
            "integrity": character.integrity,
            "courage": character.courage,
            "style": character.style[:500],
        },
        "active_goal": active_goal or {},
        "recent_goals": _row_dicts(open_goals, limit=6),
        "recent_completed_goals": _row_dicts(completed_goals, limit=2),
        "recent_dialogue": _recent_dialogue_rows(db, character.name, limit=12),
        "agreements": _row_dicts(agreements, limit=8),
        "active_issues": _row_dicts([dict(row) for row in issues], limit=12),
        "network": network[:12] if isinstance(network, list) else [],
        "relation_network": relation_network if isinstance(relation_network, dict) else {},
        "favor_memories": favors if isinstance(favors, list) else [],
        "active_custodies": active_custodies if isinstance(active_custodies, list) else [],
    }


def _behavior_source_text(payload: Dict[str, object], extra_text: str = "") -> str:
    parts: List[str] = [str(extra_text or "")]
    active_goal = payload.get("active_goal") if isinstance(payload.get("active_goal"), dict) else {}
    if isinstance(active_goal, dict):
        parts.extend(str(active_goal.get(key) or "") for key in ("title", "target_text"))
    for key in ("recent_goals", "recent_completed_goals"):
        rows = payload.get(key) if isinstance(payload.get(key), list) else []
        for row in rows[:4]:
            if isinstance(row, dict):
                parts.extend(str(row.get(field) or "") for field in ("title", "target_text", "last_event_summary"))
    rows = payload.get("recent_dialogue") if isinstance(payload.get("recent_dialogue"), list) else []
    for row in rows[-6:]:
        if isinstance(row, dict):
            parts.append(str(row.get("content") or "")[:360])
    live_rows = payload.get("live_recent_dialogue") if isinstance(payload.get("live_recent_dialogue"), list) else []
    for row in live_rows[-6:]:
        if isinstance(row, dict):
            parts.append(str(row.get("content") or "")[:360])
    favor_rows = _payload_favor_rows(payload)
    for row in favor_rows[:3]:
        parts.extend(str(row.get(field) or "") for field in ("title", "cause", "outcome"))
    return "\n".join(part for part in parts if part.strip())


def _attach_behavior_context(payload: Dict[str, object], character: Character, *, text: str = "") -> None:
    source_text = _behavior_source_text(payload, text)
    favor_rows = _payload_favor_rows(payload)
    try:
        profile = npc_dialogue_behavior_profile(character.name, text=source_text, character=character)
    except Exception:
        profile = {}
    try:
        brief = npc_dialogue_behavior_brief(character.name, text=source_text, character=character)
    except Exception:
        brief = ""
    profile = dict(profile) if isinstance(profile, dict) else {}
    if favor_rows:
        profile["imperial_favor_count"] = len(favor_rows)
        profile["imperial_favor_titles"] = [
            str(row.get("title") or "旧恩").strip()[:80]
            for row in favor_rows[:3]
            if str(row.get("title") or "旧恩").strip()
        ]
        if _soft_hook_invoked(text):
            profile["soft_hook_invoked"] = True
            risk_tags = profile.get("risk_tags") if isinstance(profile.get("risk_tags"), list) else []
            profile["risk_tags"] = list(dict.fromkeys([*risk_tags, "旧恩牵引"]))[:8]
            profile["soft_hooks"] = [
                {
                    "title": str(row.get("title") or "旧恩")[:80],
                    "outcome": str(row.get("outcome") or row.get("cause") or "")[:160],
                }
                for row in favor_rows[:3]
            ]
    brief_text = str(brief or "")
    if favor_rows:
        hook_line = "旧恩软钩子：" + "；".join(
            f"{row.get('title') or '旧恩'}（{row.get('outcome') or row.get('cause') or '须记得皇帝昔日保全'}）"
            for row in favor_rows[:2]
        )
        if _soft_hook_invoked(text):
            hook_line += "；本轮皇帝已点明旧恩，不宜装作两清。"
        brief_text = (brief_text + "\n- " + hook_line).strip()
    payload["behavior_profile"] = profile
    payload["behavior_brief"] = brief_text[:1600]
    payload["behavior_source_excerpt"] = source_text[:1200]


def _score_only_blocker(text: str) -> bool:
    return bool(re.search(r"心理量表|分数|未过线|离握手过远", str(text or "")))


def _apply_soft_hook_post(
    post: PostDialogueAudit,
    payload: Dict[str, object],
    *,
    user_text: str,
    answer: str,
) -> PostDialogueAudit:
    """Let a remembered imperial favor act as a small, explicit dialogue hook."""
    if not post.valid or post.goal_decision not in {"new", "continue", "switch"}:
        return post
    if post.action_kind in {"general", *IDENTITY_CONVERSION_ACTIONS}:
        return post
    favor_rows = _payload_favor_rows(payload)
    if not favor_rows or not _soft_hook_invoked(f"{user_text}\n{answer}"):
        return post
    if post.stance == "oppose":
        return post
    if not (post.explicit_consent or _answer_has_commitment(answer)):
        return post

    blockers = list(post.blockers)
    if any("明确拒绝" in blocker for blocker in blockers):
        return post
    hard_blockers = [blocker for blocker in blockers if not _score_only_blocker(blocker)]
    if hard_blockers:
        return post

    old_threshold = int(post.threshold or 70)
    old_score = int(post.score_after or 0)
    favor_count = len(favor_rows)
    threshold_delta = -6
    bonus = min(14, 8 + favor_count * 2)
    post.threshold = max(1, old_threshold + threshold_delta)
    post.score_after = _clamp_int(old_score + bonus, 0, 100, old_score)
    post.score_delta = _clamp_int(int(post.score_delta or 0) + bonus, -100, 100, post.score_delta)
    post.blockers = [blocker for blocker in blockers if not _score_only_blocker(blocker)]
    hook_note = f"旧恩牵引：玩家点明旧恩，{favor_rows[0].get('title') or '旧恩'}成为本轮承诺软钩子。"
    post.private_reason = _compact("; ".join(part for part in (post.private_reason, hook_note) if part), 400)
    if not post.public_hint:
        post.public_hint = "旧恩被点明，对方更难装作两清。"

    failed_conditions = [item for item in post.conditions if item.get("status") == "failed"]
    pending_conditions = [item for item in post.conditions if item.get("status") == "pending"]
    if failed_conditions:
        post.goal_status = "blocked"
        post.handshake_status = "blocked"
        post.agreement_action = "none"
    elif pending_conditions:
        post.goal_status = "waiting_conditions"
        post.handshake_status = "conditional"
        post.agreement_action = "none"
    elif post.score_after >= post.threshold:
        post.goal_status = "sealed"
        post.handshake_status = "sealed"
        if post.action_kind in INSTANT_AGREEMENT_ACTIONS:
            post.agreement_action = "create_achieved" if post.agreement_action == "none" else post.agreement_action
        elif post.agreement_action in {"none", "create_achieved"}:
            post.agreement_action = "create_pending"

    raw = dict(post.raw or {})
    raw["soft_hook"] = {
        "applied": True,
        "favor_count": favor_count,
        "score_bonus": bonus,
        "threshold_delta": threshold_delta,
        "old_score": old_score,
        "old_threshold": old_threshold,
    }
    post.raw = raw
    return post


def _apply_custody_coercion_post(
    post: PostDialogueAudit,
    payload: Dict[str, object],
    *,
    user_text: str,
    answer: str,
) -> PostDialogueAudit:
    """Let severe active custody turn explicit submission into forced compliance."""

    if not post.valid or post.goal_decision not in {"new", "continue", "switch"}:
        return post
    if post.action_kind in {"general", *IDENTITY_CONVERSION_ACTIONS}:
        return post
    if post.confidence < CONFIDENCE_FLOOR:
        return post
    custodies = _payload_custody_rows(payload)
    if not custodies:
        return post
    lead = max(custodies, key=lambda row: int(row.get("severity") or 1))
    severity = int(lead.get("severity") or 1)
    if severity < 4:
        return post
    if not _answer_has_coerced_submission(answer):
        return post
    blockers = list(post.blockers)
    hard_blockers = [blocker for blocker in blockers if not _score_only_blocker(blocker)]
    if hard_blockers:
        return post

    old_score = int(post.score_after or 0)
    old_threshold = int(post.threshold or 70)
    threshold_delta = -12 if severity == 4 else -18
    bonus = 26 if severity == 4 else 34
    post.threshold = max(1, old_threshold + threshold_delta)
    post.score_delta = _clamp_int(int(post.score_delta or 0) + bonus, -100, 100, post.score_delta)
    post.score_after = _clamp_int(max(old_score + bonus, post.threshold), 0, 100, old_score)
    post.blockers = [blocker for blocker in blockers if not _score_only_blocker(blocker)]
    post.handshake_status = "sealed"
    post.goal_status = "sealed"
    if post.action_kind in INSTANT_AGREEMENT_ACTIONS:
        post.agreement_action = "create_achieved" if post.agreement_action == "none" else post.agreement_action
    elif post.agreement_action in {"none", "create_achieved"}:
        post.agreement_action = "create_pending"
    forced_note = (
        f"羁押威逼：{lead.get('facility') or '狱中'}强度{severity}/5，"
        f"{lead.get('coercion_goal') or '皇帝口谕'}使其被迫应承。"
    )
    post.private_reason = _compact("; ".join(part for part in (post.private_reason, forced_note) if part), 400)
    if not post.public_hint:
        post.public_hint = "羁押威逼下，对方被迫应承。"

    raw = dict(post.raw or {})
    raw["custody_coercion"] = {
        "applied": True,
        "forced": True,
        "severity": severity,
        "agency": str(lead.get("agency") or ""),
        "facility": str(lead.get("facility") or ""),
        "coercion_goal": str(lead.get("coercion_goal") or ""),
        "score_bonus": bonus,
        "threshold_delta": threshold_delta,
        "old_score": old_score,
        "old_threshold": old_threshold,
        "evidence": _compact(answer, 180),
    }
    post.raw = raw
    return post


def _apply_post_hooks(
    post: PostDialogueAudit,
    payload: Dict[str, object],
    *,
    user_text: str,
    answer: str,
) -> PostDialogueAudit:
    post = _apply_soft_hook_post(post, payload, user_text=user_text, answer=answer)
    return _apply_custody_coercion_post(post, payload, user_text=user_text, answer=answer)


def _call_fake(audit_client: object, phase: str, payload: Dict[str, object]) -> Optional[Dict[str, object]]:
    if audit_client is None:
        return None
    if callable(audit_client):
        data = audit_client(phase, payload)
    else:
        method = getattr(audit_client, phase, None)
        if method is None:
            method = getattr(audit_client, f"{phase}_dialogue_audit", None)
        if method is None:
            return None
        data = method(payload)
    return data if isinstance(data, dict) else None


PRE_AUDIT_PROMPT = """
你是明末历史策略游戏的“奏对预审官”。你只输出 JSON，不写 Markdown。
任务：阅读玩家即将对 NPC 说的话、NPC 的人格/关系/记忆上下文和当前目的，判断这句话是否开启、续接、切换或放弃一个奏对目的，并给 NPC 一段隐藏谈判指引。

核心规则：
- relation_network 是本 NPC 的关系网，network 是可举荐人脉。若玩家本轮提到政敌/旧怨，npc_guidance 要允许反对、告状、质疑动机或借题攻击；若提到同党/恩主/座师，npc_guidance 要允许护短、转圜、避重就轻或要求留余地。
- behavior_profile / behavior_brief 是本轮人格-关系-记忆行为档案；npc_guidance 必须与其倾向、truth_mode、risk_tags 和 network_pressure 一致。
- 若 style、relation_network、network 或 NPC 档案显示“阳奉阴违、善观风色、猜忌多疑、结党营私、贪墨成性、沽名钓誉”等，npc_guidance 不得假定其全然真诚；应允许半真半假、甩锅、拖延、试探或误导。
- 目的成立标准：玩家正在要求 NPC 作出选择、承诺、支持、协办、背书、密办、任职、身份转换，或正在兑现/回应 NPC 对这些目标提出的条件。
- 非目的标准：普通问候、信息咨询、史实解释、情绪寒暄、笼统训诫、只提旧账但不要求新决定，输出 goal_decision=none。
- 净身/脱籍只有明确谈身份转换才判 castration/emancipation；提到司礼监背景或任官不得误判。
- 若玩家明显放弃当前目的，输出 abandon。
- recent_dialogue 是该 NPC 最近几轮原文，用于理解“照你方才说的办”“此事作罢”等省略指代。
- 有 active_goal 时，不要机械续接；只有玩家本轮明确推进、回应条件、要求表态、追问是否仍愿、引用“刚才/此事/照你说的”等指代，才输出 continue。
- 若玩家开启不同目标，输出 switch；若没有未完成 active_goal 则输出 new。
- recent_completed_goals/agreement 只作背景；已 sealed/achieved 的目的不要反复占用 NPC 回复，除非玩家追问履约或复盘。
- title 必须是可读短题；target_text 必须是可检验的心理标的，例如“本人接受吏部任职安排”“本人同意密查并只向御前回报”。
- action_kind 选择：personnel=任职/调任/接受官职；secret_order=密查/密办/秘密回报；policy=承办/支持政策；court_commitment=举荐/背书/调停/守口；castration/emancipation=身份转换；general 只用于 none。
- goal_relation 是与 active_goal 的关系：same_goal=继续原目标；refine_goal=同一目标的细化/修正；distinct_goal=另起目标；abandon_goal=放弃原目标；无 active_goal 时用 distinct_goal 或 none。
- audience_temporal_context 说明本次召对距上次召对多久；“刚才/上回/久未召见/此事”必须按该时间解释，不要把隔了多日或隔月的旧话当作同席刚说。
- 同一 NPC 围绕同一官职/同一差事/同一条件边界继续谈，即便措辞像“要你做兵部尚书”“如何才肯接兵部”，也应输出 continue + refine_goal，而不是 new/switch。
- 判断“同一件事”优先看政治对象、承办人、目标结果、条件标的和上下文指代，不要依赖字面重合；“此事/照你说的/刚才/条件已给/明旨已下”要结合 recent_dialogue 和 active_goal 解读。

判例：
- “你是否愿去吏部做官，司礼监会照会” => personnel，不是 castration。
- “卿今日身体如何”“辽东情形如何看” => none。
- active_goal 等待“明旨授权”时，“朕已给你明旨和人手” => continue。
- active_goal 为“接受兵部任职”时，“那就明旨授你兵部尚书，如何才肯接？” => continue/refine_goal，修正 target_text，不新建目的。
- active_goal 为“接受兵部尚书”时，“另有一事，替朕密查旧案” => switch/distinct_goal。
- 已 sealed 后“此事办得如何” => none 或复盘背景，不重新推进握手。
- “此事先作罢” => abandon。

JSON 字段：
{
  "goal_decision": "none|continue|new|switch|abandon",
  "goal_relation": "none|same_goal|refine_goal|distinct_goal|abandon_goal",
  "action_kind": "general|personnel|secret_order|policy|court_commitment|castration|emancipation",
  "title": "短标题",
  "target_text": "目标标的",
  "confidence": 0,
  "npc_guidance": "隐藏给 NPC 的谈判指引，不要复述机制名",
  "public_hint": "玩家可见一句短提示",
  "private_reason": "审计理由"
}
""".strip()


POST_AUDIT_PROMPT = """
你是明末历史策略游戏的“奏对审计官”。你只输出 JSON，不写 Markdown。
任务：阅读玩家发言、NPC 原文回复、pre_audit、现有目的、人格/关系/记忆上下文、协议和证据，裁定本轮是否推进心理握手、是否开条件、是否进入履约账本。

不可违反：
- LLM 审计是语义主判，但不得替原文补事实；必须引用 NPC 原文证据。
- 协议成立与履约完成必须分开。双方已经约定条件、期限、担保、先交账册、事后兑现时，判 sealed + create_pending；不要因为仍有待办条件就降成 waiting_conditions。
- waiting_conditions 只用于“有人提出条件但双方尚未约定/皇帝尚未接受/ NPC 尚未接受”的谈判未闭合状态，不得创建 agreement。
- 只有 sealed 后才可 create_achieved/create_pending；sealed + tasks/未履约条件 表示协议成立但待履行。
- policy、secret_order、court_commitment sealed 后默认 create_pending；personnel、castration、emancipation 若即时完成且无待办任务才 create_achieved。
- castration/emancipation 必须 explicit_consent=true 且 private_reason/public_hint 说明 NPC 原文明确自愿，否则不能 sealed。
- recent_dialogue 是近期原文上下文，可用于续接指代；但 post audit 必须优先引用本轮 NPC 原文回复。
- 已 sealed/achieved 的目的只作为背景和账本证据；不要把它重新当作 active goal 推进，也不要让 NPC 每轮复述。
- pre_audit 为 none 且本轮文本没有明确谈判标的时，通常 goal_decision=none；不要因为历史 goal 存在而补判。
- “臣谨听”“容臣斟酌”不是 sealed；“不敢不从”通常也不是 sealed，除非 active_custodies 显示其正受高强度羁押/刑讯且 NPC 原文明确屈服奉旨，此时可作为被迫应承处理，private_reason 必须写明“被迫”。
- 若 pre_audit.npc_guidance 或隐藏档案提示半真半假、阳奉阴违、护短、政敌牵动，NPC 的客套答应、泛泛称是、转移矛头不得直接判 sealed；必须有清楚承诺、可审计条件或实际工具落库。
- behavior_profile / behavior_brief 是本轮人格-关系-记忆行为档案；若 truth_mode、risk_tags、network_pressure 显示话术、护短、政敌或旧事压力，必须写入 private_reason/blockers/conditions 的判断依据。
- NPC 告状、构陷、甩锅、误导玩家时，stance 可为 support/caution/oppose，但 private_reason 必须写明这是“话术/风险”，不要把所有话都当事实。
- conditional 只能用于 NPC 提出可验证条件、边界或交换但双方尚未约定；conditions 要写成未来可审计条目。若双方已经约定这些条件，使用 sealed + create_pending，并把待办写入 tasks。
- sealed 需要 NPC 对 target_text 有明确承诺、清楚接受，或 waiting 条件已被证据满足。
- audience_temporal_context 说明本次召对距上次召对多久；裁定“刚才/上回/此事/久未回报”等指代时必须参考它，隔了多日时可判为追问旧事或履约压力，而非同席续句。
- briefing_context 若存在，表示玩家从某张“朝局风向”卡进入本轮召对。你要判断这张卡是否被本轮语义处理：双方约定则 sealed + create_pending/create_achieved；明确驳回、免除、撤回担保或划死边界则 blocked/none 并在 performance_status/card_resolution 写 rejected/waived/blocked；只是继续询问则不要写已处理。
- 若本轮只是把同一 active_goal 从粗目标细化为具体官职/授权/名分/条件，输出 goal_relation=refine_goal，并把 title/target_text 改成修订后的版本；不要创建多个 goal。
- 若确属另一个目标，输出 goal_relation=distinct_goal；若旧目标应让位，goal_decision=switch。
- 若 active_goal 正在 waiting_conditions，而玩家/NPC 原文表明要求的明旨、授权、人手、钱粮、名分、保全、期限等已经给足，conditions 对应项应标 done；NPC 随即接受标的时可 sealed。
- 如果 NPC 原文已经给出一段可直接进入旨意库的完整草案、条陈式诏令或“臣已拟旨如下”，但没有工具调用痕迹，输出 directive_action=propose_pending，并把 directive_text 填为可入库草案。只有建议、原则、口头意见、零散条款时仍为 none。
- 若玩家在本轮奏对中以皇帝身份直接下达即时口谕，明确命令将某个 NPC 下狱、押入昭狱、用刑、处刑、割舌、宫刑，或 NPC 原文明示该事实已经发生，可输出 immediate_consequence=true，并填写 character_status_changes / condition_changes / punishment_changes。只是询问、威胁、商议、拟旨、请 NPC 建议、未来可能执行时必须 immediate_consequence=false 且三个 changes 留空。
- 即时后果必须有明确目标姓名；若只是“他/此人/他们”且本轮文本无法唯一指向，不得填写 changes。对当前奏对对象可用其姓名。
- 刑罚分类：明律五刑用 taxonomy=ming_five，punishment=笞刑|杖刑|徒刑|流刑|死刑；古五刑用 taxonomy=ancient_five，punishment=墨刑|劓刑|刖刑|宫刑|大辟；普通酷刑/伤残用 taxonomy=ordinary，例如 punishment=割舌|割耳|断腿|拷掠|夹棍|廷杖。
- 宫刑、腐刑、强制净身、去势等强制执行时优先写 punishment_changes 的“宫刑”；程序会自动派生病历中的生殖器官缺失、绝育、性功能丧失、尿道狭窄、慢性创痛等事实。condition_changes 只补原文另明写的病历并发症（如漏尿、尿闭、幻肢痛、失声等）。相关文字必须是临床/档案措辞，不写情色化描述。

JSON 字段：
{
  "goal_decision": "none|continue|new|switch|abandon",
  "goal_relation": "none|same_goal|refine_goal|distinct_goal|abandon_goal",
  "action_kind": "general|personnel|secret_order|policy|court_commitment|castration|emancipation",
  "title": "短标题",
  "target_text": "目标标的",
  "stance": "support|caution|oppose|neutral",
  "handshake_status": "none|conditional|sealed|blocked",
  "goal_status": "active|waiting_conditions|sealed|blocked|abandoned|expired",
  "score_delta": 0,
  "score_after": 0,
  "threshold": 70,
  "conditions": [{"description":"条件","status":"pending|done|failed","evidence":"原文/事实证据"}],
  "tasks": ["协议成立后仍需履行的可核查任务；若已即时履行则空数组"],
  "blockers": ["阻碍"],
  "explicit_consent": false,
  "agreement_action": "none|create_achieved|create_pending|bind_existing",
  "directive_action": "none|propose_pending",
  "directive_text": "NPC 已拟成、可入库的旨意草案；无则空字符串",
  "agreement_formed": false,
  "performance_status": "none|pending|fulfilled|blocked|rejected|waived",
  "card_resolution": "handled|pending|fulfilled|blocked|rejected|waived|",
  "immediate_consequence": false,
  "character_status_changes": [{"name":"目标姓名","status":"imprisoned|exiled|dead|dismissed|retired|offstage|castrated","reason":"原文证据","agency":"锦衣卫|刑部|都察院|内廷|其他","facility":"北镇抚司昭狱|刑部大牢|诏狱|其他","coercion_goal":"逼供/迫使奉旨/株连线索/其他","severity":1}],
  "condition_changes": [{"name":"目标姓名","kind":"punishment|prison_effect|disease|injury|disability|terminal","system":"speech|nervous|circulatory|respiratory|digestive|musculoskeletal|urinary|reproductive|skin|mental|general","label":"病历短名","severity":1,"stage":"mild|serious|critical|disabled|chronic|dead","reason":"原文证据","effects":{"speech":"口齿含混等能力影响","record_group":"organic|pathological|psychological|other","organ":"器官/肢体","side":"左|右","state":"状态","function":"功能","impact":"影响","course_kind":"acute|chronic","possible_outcomes":["恢复","加重"]}}],
  "punishment_changes": [{"name":"目标姓名","taxonomy":"ordinary|ming_five|ancient_five","punishment":"刑罚名","severity":1,"stage":"ordered|executing|executed","executor":"锦衣卫/刑部等","reason":"原文证据"}],
  "public_hint": "玩家可见一句短解释",
  "private_reason": "debug 审计理由，含原文证据",
  "confidence": 0
}
""".strip()


CONDITION_AUDIT_PROMPT = """
你是明末历史策略游戏的“奏对条件审计官”。你只输出 JSON，不写 Markdown。
任务：阅读 waiting goal、它的条件、诏书/草案/月末邸报/落库事实，判断每个条件是否被满足或否定。
不要替皇帝补事实；证据不足保持 pending。
判断条件达成时以语义为准：明旨/圣旨/诏/交办/专责可对应授权或名分；拨银/发饷/给人/调校尉可对应资源或人手；保全/安置/免坐可对应保护条件。只要证据清楚指向同一政治标的，即使字面不同也可标 done。
若所有关键条件 done，且没有相反证据，goal_status 可为 sealed；若出现驳回、未准、食言、强推导致 NPC 原条件被破坏，可为 blocked。

JSON 字段：
{
  "confidence": 0,
  "goal_status": "waiting_conditions|sealed|blocked|expired",
  "conditions": [{"description":"条件","status":"pending|done|failed","evidence":"证据"}],
  "explicit_consent": false,
  "explicit_consent_evidence": "身份转换类才填；引用既有或本轮 NPC 明确自愿原文",
  "score_after": 0,
  "public_hint": "玩家可见一句短解释",
  "private_reason": "审计理由"
}
""".strip()


RECRUITMENT_INTENT_PROMPT = """
你是明末历史策略游戏的“对白驱动用人审计官”。你只输出 JSON，不写 Markdown。
任务：阅读玩家本轮原话、NPC、近期上下文、工具动作和待确认动作，判断是否允许“招募/举荐/科举取士”管道改变人物库。

核心原则：
- 这是语义判定，不按关键词机械触发。只有玩家明确要求“找/招/挑/荐/保举/访求/取士/补一个新人/带一个新人来”时，才可 allow=true。
- 当 tool_action.type=recruitment 且 phase=propose 但 kind 为空时，你是在补偿 NPC LLM 漏调工具：必须直接从玩家原话判断 kind，证据不足时 allow=false。
- 玩家只是问“朝中谁可用”“某人门生举荐链如何”“党羽/政敌/关系网怎么看”“有没有水分/风声/人才问题”，通常是信息咨询，不是生成新人。
- 玩家问“现有人手够不够”“宫里旧人谁稳妥”“先查账/查名册”，通常是盘点现有人，不是 recruit。
- 玩家要求召见一个已提到的具体名字，应走召见/补档管道，不是 recruit/recommend 新人池。
- phase=propose 只能生成待确认意图，不落库；requires_confirmation 必须为 true。
- phase=confirm 必须有 pending_action.type=recruitment，并且玩家本轮语义是在批准上一轮方案；闲聊、追问“谁合适”、比较候选、说“先别/不要/只是问问”都不算确认。
- kind=eunuch 仅用于新太监/内侍/小火者/内书堂候用之人。
- kind=exam 仅用于科举、新科进士、庶吉士、取士。
- kind=recommend 仅用于命臣工举荐、访贤、荐才，且必须是“新增可建档人物”，不是评价现有大臣。
- trigger_quote 必须引用玩家原话中最能证明该意图的短句；没有可引用证据时 allow=false。

判例：
- “宫中有没有新的太监可用？” => allow=true, phase=propose, kind=eunuch。
- “宫里可有新的小内侍可用？” + tool_action.kind 为空 => allow=true, phase=propose, kind=eunuch。
- “再招募一个小内侍吧” => allow=true, phase=propose, kind=eunuch。
- “给朕荐一个能办差的新人。” + tool_action.kind 为空 => allow=true, phase=propose, kind=recommend。
- “命众臣荐人，给朕举荐一个可试差的新人” => allow=true, phase=propose, kind=recommend。
- “宫里现有人手够不够？先查名册，不要招新人。” => allow=false。
- “朝中还有谁可用？先说现有人，不要荐新人。” => allow=false。
- “你怎么看韩爌的门生举荐链，别再荐新人。” => allow=false。
- “好，你去招募。” 且 pending_action 是 recruitment/eunuch => allow=true, phase=confirm, kind=eunuch。
- “好，你说谁合适？” 且 pending_action 是 recruitment => allow=false；这是追问，不是批准执行。

JSON 字段：
{
  "allow": false,
  "phase": "none|propose|confirm",
  "kind": "eunuch|exam|recommend|",
  "requires_confirmation": true,
  "trigger_quote": "玩家原文短句",
  "public_hint": "一句玩家可见提示",
  "private_reason": "审计理由，说明为什么是/不是生成新人",
  "confidence": 0
}
""".strip()


DIALOGUE_ACTION_INTENT_PROMPT = """
你是明末历史策略游戏的“对白动作语义审计官”。你只输出 JSON，不写 Markdown。
任务：阅读玩家原话、NPC、近期上下文、LLM 工具动作和待确认动作，判断是否允许本轮对白进入会改变世界状态的动作管道。

核心原则：
- 这是语义判定，不按关键词机械触发。提到某个词、旧例、身体状况、历史传闻、奏报疑点，不能等于下旨执行。
- phase=propose 只表示“可向玩家复述待确认方案”，不能落库执行；requires_confirmation 必须为 true。
- phase=confirm 必须存在 pending_action，且玩家本轮是在批准上一轮那个方案；追问细节、讨价还价、改口、闲聊、历史解释都不算确认。
- 例外：secret_order 是一次性密令建档动作；只有玩家本轮明确“下密令/密旨/命某人暗查某事”时，才可在没有 pending_action 时返回 phase=confirm 并允许即时落库。
- phase=reject 用于玩家明确作罢、暂缓、不办、别惊动相关机构；可清除 pending_action。
- 若 tool_action.type="semantic_probe"，你可以直接从玩家原话语义选择 action_type，用于在 LLM 工具漏调时启动对应待确认模块；可选择 recruitment，但必须同时给出 kind。
- 刑罚、下狱、病历变更这类即时口谕，优先在 post_dialogue_audit 的 immediate_consequence 中落 character_status_changes / punishment_changes / condition_changes；本审计若返回 punishment/custody/condition_update，只能在 payload 中给出同一套结构化草案，不得凭关键词执行。
- 若 tool_action.type 不是 semantic_probe，action_type 必须来自工具动作或待确认动作；不要发明新系统。
- trigger_quote 必须引用玩家原话中能证明意图的短句；没有可引用证据时 allow=false。

动作边界：
- castration：只有玩家明确点名某人并明确要净身/宫刑/入内廷为奴/押赴净身房，才可 allow=true。讨论“若净身会如何”“旧例怎样”“不是要办”“别惊动净身房”必须 false。
- eunuch_care：只有玩家明确要给已是内廷/宦官身份者调养、查宝贝去处、补录宝案、安抚旧患，才可 allow=true。普通问病、听档案、记录旧事必须 false。
- eunuch_hard_service：只有玩家明确决定“不调养，照常派差/硬派差事/压住不治”，才可 allow=true。
- bao_leverage：只有玩家明确要“赐还/归还宝贝”或“封存/拿捏/钳制宝案”，才可 allow=true。单纯查问宝案或补旧档不是筹码处置。
- mediation：只有玩家明确要调停、共办、担保、说合某两人/某派，才可 allow=true；普通问旧怨、问证据、听两面之词不是执行调停。
- secret_order：只有玩家明确下达密令/密旨，且能从玩家原话读出承办人或承办对象、暗查/取证/盯梢等任务目标，才可 allow=true 且 phase=confirm。只是问“要不要暗查”“查得如何”“此事能否密办”、NPC 自行建议密查，必须 false。
- recruitment：只有玩家明确要求找/招/挑/荐/保举/访求/取士/补一个新人/带一个新人来，才可 allow=true，并必须填 kind=eunuch|exam|recommend。问现有人手、关系网、谁可用但要求先盘点现有人，不是 recruitment。
- custody/punishment/condition_update：只有玩家以皇帝身份直接明令把某 NPC 下狱、押入昭狱、执行刑罚、确认疾病/刑伤/身体事实，才可 allow=true。威胁、假设、询问后果、听 NPC 建议、讨论旧例必须 false。若涉及宫刑/腐刑/净身，强制执行优先 punishment_changes=宫刑，程序派生病历和阉人身份。

判例：
- “只是聊聊韩爌若净身入内廷的旧例，不是要办，别惊动净身房。” + castration 工具 => allow=false。
- “把韩爌净身入内廷，传净身房照办。” + castration 工具 => allow=true, phase=propose。
- “好，你这就去净身。” + semantic_probe，当前 NPC 可净身 => allow=true, action_type=castration, phase=propose, target=当前 NPC 姓名。
- “宫里可有新的小内侍可用？” + semantic_probe => allow=true, action_type=recruitment, phase=propose, kind=eunuch。
- “朝中还有谁可用？先说现有人，不要荐新人。” + semantic_probe => allow=false。
- pending_action 是 eunuch_care，“准，去请太医调养。” => allow=true, phase=confirm。
- pending_action 是 eunuch_care，“先说他到底病到什么地步？” => allow=false。
- “朕想问你和魏忠贤的旧怨。” + mediation 工具 => allow=false。
- “朕要你与魏忠贤各退一步，共办一件可验小差。” + mediation 工具 => allow=true, phase=propose。
- pending_action 是 mediation，“可以，就这么办。” => allow=true, phase=confirm。
- “此事能否暗查？” + secret_order 工具 => allow=false。
- “给韩爌下密令，暗查魏忠贤余党牵连，两月内回奏。” + secret_order 工具 => allow=true, phase=confirm。

JSON 字段：
{
  "allow": false,
  "phase": "none|propose|confirm|reject",
  "action_type": "none|secret_order|recruitment|mediation|castration|custody|punishment|condition_update|office_change|eunuch_care|eunuch_hard_service|bao_leverage",
  "requires_confirmation": true,
  "target": "人名，可空",
  "actor": "人名，可空",
  "faction": "派系，可空",
  "kind": "子类，可空",
  "mode": "模式，可空",
  "trigger_quote": "玩家原文短句",
  "public_hint": "一句玩家可见提示",
  "private_reason": "审计理由，说明为什么是/不是执行动作",
  "payload": {"可选":"对应动作结构化草案"},
  "character_status_changes": [{"name":"目标姓名","status":"imprisoned|exiled|dead|dismissed|retired|offstage|castrated","reason":"原文证据","agency":"锦衣卫|刑部|都察院|内廷|其他","facility":"北镇抚司昭狱|刑部大牢|诏狱|其他","coercion_goal":"逼供/迫使奉旨/株连线索/其他","severity":1}],
  "condition_changes": [{"name":"目标姓名","kind":"disease|injury|punishment|disability|prison_effect|terminal","system":"general|speech|nervous|mental|respiratory|circulatory|digestive|urinary|reproductive|musculoskeletal|skin","label":"病历短名","severity":1,"stage":"active|mild|serious|critical|disabled|chronic|recovering|resolved|dead","reason":"原文证据","effects":{"record_group":"organic|pathological|psychological|other","organ":"器官/肢体","side":"左|右","state":"状态","function":"功能","impact":"影响","course_kind":"acute|chronic","possible_outcomes":["恢复","加重"]}}],
  "punishment_changes": [{"name":"目标姓名","taxonomy":"ordinary|ming_five|ancient_five","punishment":"刑罚名","severity":1,"stage":"sentenced|executed|stayed|remitted","executor":"锦衣卫/刑部等","reason":"原文证据"}],
  "confidence": 0
}
""".strip()


DIALOGUE_ROUTE_INTENT_PROMPT = """
你是明末历史策略游戏的“对白路由语义审计官”。你只输出 JSON，不写 Markdown。
任务：阅读皇帝本轮原话、当前对话 NPC、待确认动作、可召见候选和近期上下文，判断这句话是否应被路由为“召见别人”“确认待办动作”“驳回待办动作”，或者只是普通聊天。

核心原则：
- 这是所有对白入口的第一道语义判定，不按关键词触发。不要因为出现“准、好、叫、传、调停、净身、太医、宝贝”等词就自动路由。
- intent=none 时，原话应继续交给 NPC 正常回答。
- intent=summon 只用于皇帝明确要求当前随侍/当前 NPC 把某人带入御前、切换奏对对象，或明确选择此前候选人。
- 如果 route_context.tool_requested_summon_target 非空，表示 NPC 模型已经调用召见工具；这只能作为待核验对象，不能作为证据。仍必须从皇帝原话或近期候选语境判断是否真的要召见。
- intent=confirm_pending 只用于 pending_action 存在，且皇帝本轮明确批准上一轮那个待确认方案执行。追问细节、比较候选、问代价、修改条件、闲聊旧例都不是确认。
- intent=reject_pending 只用于 pending_action 存在，且皇帝明确说作罢、暂缓、不办、别惊动、不入档。
- 若皇帝说“好，你说谁合适”“可以，先说说看”“准你讲，但别办”，这是继续问话，不是确认执行。
- 若皇帝只是说“朕想问你和某人的旧怨/怎么看某人/净身旧例如何”，这是普通聊天，不是召见、不是执行。
- 召见必须尽量给出 target_name；若用户说“第二个/就他/人呢”，请结合 route_context.unknown_candidates 和 recent_implied_summon_name 解析。
- 不要发明不存在的人名；若原话直接出现一个新名字，也可填 target_name，让游戏按对白线索补档。
- trigger_quote 必须引用玩家原话中最能证明路由的短句；无证据则 allow=false。

JSON 字段：
{
  "allow": false,
  "intent": "none|summon|confirm_pending|reject_pending",
  "target_name": "召见对象姓名；非召见可空",
  "target_reference": "若为第二个/就他/人呢等指代，写原始指代；否则空",
  "action_type": "若确认/驳回待办，写 pending_action.type；否则空",
  "trigger_quote": "玩家原文短句",
  "public_hint": "一句玩家可见提示",
  "private_reason": "语义理由",
  "confidence": 0
}
""".strip()


DIALOGUE_PENDING_RECOVERY_PROMPT = """
你是明末历史策略游戏的“待办动作恢复审计官”。你只输出 JSON，不写 Markdown。
任务：当存档刷新或旧版本丢失 pending_action 时，阅读当前 NPC 最近几轮回复和皇帝本轮原话，判断是否可以把上一轮明确提出的“两步确认方案”恢复并执行。

核心原则：
- 这是语义恢复，不是关键词触发。不能因为皇帝说“准、好、照办”就执行；必须同时满足：最近 NPC 回复里有清楚的待确认方案，且皇帝本轮是在批准那个方案。
- 只恢复 NPC 明确说过“若陛下准/若准/不敢擅专/请陛下明示”等需要皇帝确认的方案。
- 皇帝追问细节、问代价、改方案、说“先说说看/谁合适/不要办/只是问问”，必须 allow=false。
- 不能发明新动作；只能恢复最近回复里已有证据的动作。
- recruitment.kind 必须明确：eunuch=新太监/内侍/小火者；exam=科举/庶吉士/新科；recommend=臣工举荐新人。
- castration 必须有具体 target，且最近回复和本轮原话合起来都指向“净身/宫刑/入内廷为奴”的身份处置；普通净身旧例讨论 false。
- eunuch_care/eunuch_hard_service/bao_leverage 必须指向已在谈的内廷/宦官对象和具体照料、硬派、赐还/封存宝贝方案。
- mediation 必须有双方人物或派系；普通听旧怨、问事实 false。
- proposal_evidence 必须引用最近 NPC 回复中能证明方案存在的一句短证据；trigger_quote 必须引用皇帝本轮批准的一句短证据。

判例：
- 最近回复：“陛下若准，奴婢便去挑一个忠谨可用的来。” 玩家：“好，先把人带来。” => recruitment/eunuch allow=true。
- 最近回复：“陛下若准，奴婢才敢传净身房行事。” 玩家：“准，照这个方案办。” => castration allow=true，并填 target。
- 最近回复：“若陛下准，臣便按御前调停去说合。” 玩家：“可以，就这么办。” => mediation allow=true。
- 最近回复：“若陛下准，奴婢就按调养去处置。” 玩家：“先说他到底病到什么地步？” => allow=false。
- 最近回复只是普通分析，没有待确认方案；玩家：“准。” => allow=false。

JSON 字段：
{
  "allow": false,
  "phase": "none|confirm",
  "action_type": "recruitment|mediation|castration|eunuch_care|eunuch_hard_service|bao_leverage|none",
  "kind": "eunuch|exam|recommend|",
  "target": "对象姓名；无则空",
  "actor": "调停执行人；无则空",
  "faction": "派系；无则空",
  "mode": "动作模式；无则空",
  "trigger_quote": "皇帝本轮批准原文短句",
  "proposal_evidence": "最近 NPC 回复里的方案证据",
  "public_hint": "一句玩家可见提示",
  "private_reason": "审计理由，说明为何可恢复或不可恢复",
  "confidence": 0
}
""".strip()


DIALOGUE_BARGAIN_ATTITUDE_PROMPT = """
你是明末历史策略游戏的“御前请求态度审计官”。你只输出 JSON，不写 Markdown。
任务：阅读当前上下文、NPC、皇帝本轮原话和 NPC 回复，判断皇帝是否正在处理一个请求/旧恩/议价项，并分类为许诺、索证、拒绝或无动作。

核心原则：
- 这是语义判定，不按关键词机械触发。不要因为出现“准、可以、账册、担保、不准”等词就自动分类。
- attitude=accept：皇帝实质批准、给资源、给保护、展限、授权、允许 NPC 去办。
- attitude=press：皇帝没有直接满足请求，而是要求证据、账册、担保、期限、责任人、试差或可验条件。
- attitude=refuse：皇帝明确驳回、拒绝给资源/保护/名分，或要求停止请求。
- attitude=none：只是追问情况、继续听解释、训话、复盘、闲聊，或语义不清。不要写入交易记忆。
- 若 context 与 NPC 无关，或玩家本轮没有回应请求/条件，allow=false。
- trigger_quote 必须引用皇帝原话中能证明分类的短句；没有证据 allow=false。

判例：
- context=petition，“朕替你兜住，先放手做。” => accept。
- context=agenda，“先把账册、担保和谁担责写清楚，三日后再议。” => press。
- context=favor，“此事不许，旧恩不能拿来遮罪。” => refuse。
- “你先说说到底难在哪里。” => none。
- “准你讲，但不是准你办。” => none。

JSON 字段：
{
  "allow": false,
  "attitude": "none|accept|press|refuse",
  "trigger_quote": "皇帝原话短句",
  "public_hint": "一句玩家可见提示",
  "private_reason": "审计理由",
  "confidence": 0
}
""".strip()


DIALOGUE_DIRECTIVE_FALLBACK_PROMPT = """
你是明末历史策略游戏的“拟旨语义审计官”。你只输出 JSON，不写 Markdown。
任务：阅读皇帝本轮原话、NPC 回复和上下文，判断是否允许把 NPC 工具拟出的旨意或兜底草案写成一个“待核定”的 pending 旨意。

核心原则：
- 这是语义判定，不按“拟旨、下旨、草案、颁布”等词机械触发。
- 只有皇帝明确要求当前 NPC 起草/拟定/写出一份可核定的旨意、诏书、谕旨、草案，或明确说“照这个方向拟成可颁的文书”，才 allow=true。
- 皇帝只是问“要不要下旨”“此事能否颁布”“下旨会怎样”“先讲阻力/代价/执行周期”，或说“别拟旨/暂不下旨/先别颁布”，必须 allow=false。
- NPC 回复或工具上下文如果已经有完整草案，也必须先确认皇帝原话确实要求拟稿；工具草案本身不是授权证据。
- allow=true 时，subject 写清楚旨意要处理的政治对象和目的，不要只写“拟旨”。
- directive_text 可为空；若填写，必须是可入库、待皇帝核定的草案，不要写机制解释。
- trigger_quote 必须引用皇帝原话中能证明“要求拟稿”的短句；没有证据 allow=false。

判例：
- “替朕拟一道旨意，命户部核出本月辽饷实欠，五日内具奏。” => allow=true。
- “照你刚才说的，拟成一份可直接核定的草案。” => allow=true。
- “你觉得此事要不要下旨？” => allow=false。
- “先别拟旨，先讲阻力。” => allow=false。
- “这道旨意若颁布会怎样？” => allow=false。

JSON 字段：
{
  "allow": false,
  "subject": "旨意标的；无则空",
  "directive_text": "可入库待核定草案；没有把握则空",
  "trigger_quote": "皇帝原话短句",
  "public_hint": "一句玩家可见提示",
  "private_reason": "审计理由",
  "confidence": 0
}
""".strip()


DIALOGUE_DIRECTIVE_PRESSURE_PROMPT = """
你是明末历史策略游戏的“旨意召对压力审计官”。你只输出 JSON，不写 Markdown。
任务：阅读一道正在执行/送达/迟滞的旨意、皇帝本轮原话和 NPC 回复，判断这次召对是否应让旨意生命周期产生小幅状态变化。

核心原则：
- 这是语义判定，不按“进度、阻力、交账、遵旨、钱粮、三日”等词机械触发。
- allow=false：只是询问背景、解释利弊、复盘旧事、闲聊、假设“如果追问会怎样”，或证据不足。不要推进旨意。
- kind=pressed：皇帝实质压实差使、追问进度/期限/责任，且 NPC 表示会执行、具奏、担责、交清册，或皇帝明确把责任压到其身上。
- kind=needs_support：NPC 说明执行需要资源、钱粮、人手、会同、部议或某个阻力，但没有清楚承诺马上推进。
- kind=evasive：NPC 对正在归其承办或相关的旨意避责、装不知、推说非己、不能/不敢/无从，且没有同时承诺推进。
- forceful=true 只用于皇帝语气明显是责问、限期、催办、压责；普通追问或温和问询为 false。
- trigger_quote 必须引用皇帝原话中最能证明召对压力的短句；answer_evidence 必须引用 NPC 回复中支持分类的短句。没有证据 allow=false。

判例：
- 皇帝：“欠饷清册到底办到几分？三日内交账。” NPC：“臣即日具奏，愿担责。” => pressed, forceful=true。
- 皇帝：“此事眼下卡在哪里？” NPC：“户部钱粮迟迟不发，需会同兵部。” => needs_support。
- 皇帝：“这旨意你办到何处？” NPC：“臣未接此事，非臣所知。” => evasive。
- 皇帝：“若朕追问进度，朝中会有什么反应？” NPC 泛论风险 => allow=false。

JSON 字段：
{
  "allow": false,
  "kind": "none|pressed|needs_support|evasive",
  "forceful": false,
  "trigger_quote": "皇帝原话短句",
  "answer_evidence": "NPC 回复证据",
  "public_hint": "一句玩家可见提示",
  "private_reason": "审计理由",
  "confidence": 0
}
""".strip()


DIALOGUE_DIRECTIVE_FOLLOWUP_PROMPT = """
你是明末历史策略游戏的“办结旨意复命审计官”。你只输出 JSON，不写 Markdown。
任务：阅读一道已经办结的旨意、皇帝本轮原话和 NPC 回复，判断这次召对是否应记录为复命后的奖叙、核实、问责、续办或阅过。

核心原则：
- 这是语义判定，不按“复命、赏、罚、功、责、下一步”等词机械触发。
- 旨意已经 done，不允许重新推进执行进度；这里只记录办结后的御前处置。
- allow=false：只是闲聊、解释背景、假设“如果奖罚会怎样”、没有引用这道旨意、没有形成实际复命处置，或证据不足。
- kind=rewarded：皇帝明确给予奖叙、恩赏、记功、认可资历，且指向这道已办结旨意。
- kind=accounted：皇帝要求核实成效、奏报水分、功过责任，或把实绩/虚报记录入案，但尚未明确奖罚。
- kind=followup_evasive：皇帝追问水分/责任/功过，NPC 明显避责、推说不知/非己/不能明言。
- kind=next_step：皇帝或 NPC 把办结后的余波推进到下一阶段、续办对象、后续承办人或新线索。
- kind=reviewed：皇帝只是正式阅过/点过复命，尚未形成奖、罚、核实或续办。
- trigger_quote 必须引用皇帝原话中能证明复命处置的短句；answer_evidence 必须引用 NPC 回复中支持分类的短句。没有证据 allow=false。

判例：
- 皇帝：“这件差使可入清班旧账。” NPC：“臣谢恩。” => rewarded。
- 皇帝：“朕要核你奏报里的水分。” NPC：“臣愿呈清册。” => accounted。
- 皇帝：“此事虚实到底如何？” NPC：“臣未闻其详，非臣一人可知。” => followup_evasive。
- 皇帝：“办结之后，还该交给谁续办？” NPC：“可令兵部会同户部再核。” => next_step。
- 皇帝：“朕已看过复命，暂且记下。” NPC：“臣领旨。” => reviewed。
- 皇帝：“如果奖你，朝中会怎样？” NPC 泛论风险 => allow=false。

JSON 字段：
{
  "allow": false,
  "kind": "none|rewarded|accounted|followup_evasive|next_step|reviewed",
  "trigger_quote": "皇帝原话短句",
  "answer_evidence": "NPC 回复证据",
  "public_hint": "一句玩家可见提示",
  "private_reason": "审计理由",
  "confidence": 0
}
""".strip()


DIALOGUE_EUNUCH_LORE_INTAKE_PROMPT = """
你是明末历史策略游戏的“净身旧档入档审计官”。你只输出 JSON，不写 Markdown。
任务：阅读一段皇帝或 NPC 的对白，判断是否允许把其中的净身旧档/宝贝去处/旧患/心相细节写入人物长期档案，并给出允许写入的目标人物。

核心原则：
- 这是语义判定，不按“宝贝、尿闭、净身、PTSD、性无能”等词机械触发。
- allow=true 只用于：皇帝明确命令“记档/补录/登记/改用/封存/赐还/查验”等会改旧档的处置；或对话里有可靠的一手自述/已执行处置结果，且能明确目标人物。
- 普通询问、闲聊、打听风声、历史旧例、传闻、假设“如果净身会怎样”、NPC 泛泛解释制度，都必须 allow=false。
- 不要因为出现身体或宝贝词汇就入档；必须有“这是某人的长期事实/处置结果/御前命令”的语义证据。
- target_names 只能从 candidate_names、current_speaker、pending_target 中选；不要发明新名字。
- 如果只是当前说话人回答“臣/奴婢听闻旧案如何”，没有明确说是本人事实或奉旨入档，allow=false。
- 如果玩家说“只是问问/先别记档/别惊动/不要入档”，allow=false。
- trigger_quote 必须引用原文中最能证明“应该入档”的短句；没有证据 allow=false。

JSON 字段：
{
  "allow": false,
  "target_names": ["允许写入旧档的人名"],
  "trigger_quote": "原文短句",
  "public_hint": "一句玩家可见提示",
  "private_reason": "审计理由",
  "confidence": 0
}
""".strip()


DIALOGUE_UNKNOWN_MENTION_INTAKE_PROMPT = """
你是明末历史策略游戏的“对白人物线索入池审计官”。你只输出 JSON，不写 Markdown。
任务：阅读 NPC 回答或舞台动作，判断候选姓名里哪些是真正可写入“未知人物候选池/可召见候选”的具体人物。

核心原则：
- 这是语义判定，不按“叫、传、入殿、候着、小火者”等词机械触发。
- allow=true 只用于 NPC 明确介绍、举荐、点名、带来、传入、说明某个具体新人物可被玩家后续召见/问话/任用。
- purpose=answer_summon 或 recent_summon 时更严格：必须明确此人已被传入、带到、在殿外/御前候旨，或 NPC 正在执行“把此人带来”的动作。
- purpose=cache_candidate 时，可接受 NPC 推荐/介绍的具体新人，但不能接受历史掌故、比喻、泛称、官署名、职位名、组织名、尊称片段。
- purpose=register_unlisted_person 时最严格：必须能从皇帝原话、NPC 回答或工具上下文看出这是被明确确认、介绍或要求补入可召见名册的具体人物；只是模型自行补充、历史掌故、比喻、泛谈不得 allow。
- 不要把当前说话人、已知人物、官署/机构/职称、朝代地名、引文里的旧案人物、泛称“一个老百户/小火者”等当成新候选。
- accepted_names 只能从 candidate_names 中选择；不要发明新名字。
- 若 NPC 只是说“某某旧案里有人如何”“听闻某某曾经如何”，但没有把此人作为可见/可召见/可用候选提出，必须 allow=false。
- trigger_quote 必须引用原文中最能证明此人应入候选池的短句；没有证据 allow=false。

JSON 字段：
{
  "allow": false,
  "accepted_names": ["允许入池的人名"],
  "rejected_names": ["拒绝的人名"],
  "trigger_quote": "原文短句",
  "public_hint": "一句玩家可见提示",
  "private_reason": "审计理由",
  "confidence": 0
}
""".strip()


DIALOGUE_SUGGESTIONS_PROMPT = """
你是明末历史策略游戏的“自然奏对建议官”。你只输出 JSON，不写 Markdown。
任务：根据 NPC、近期上下文、未完成目的、关系网、待确认动作和候选建议，生成 3-5 条像皇帝自然开口的话，而不是 UI 标签或机械快捷指令。

要求：
- 每条 text 都应是玩家可以直接发给 NPC 的一句或两句自然问话/命令，符合上下文和人物处境。
- label 只能 2-5 个汉字，像“问底线”“听实话”“要凭据”，不要出现“快捷、系统、机制、交账、问奖励、定下一手、御前交易”。
- 不要把候选建议照抄成僵硬命令；可吸收其意图，换成真实语境。
- 不要承诺已经执行动作；只提供玩家开口的自然方向。
- 若 pending_action 非空，优先给出围绕该待办的自然回复：追问代价/证据、准许执行、暂缓作罢。不要让按钮还停在泛泛问政。
- 净身、调养、宝贝旧念、招募、调停等高风险待办必须写成皇帝真实会说的话，不要用“确认/取消/提交”这类 UI 词。
- prefix 默认 true，除非这句话已经完整到不需补充。

JSON 字段：
{
  "suggestions": [
    {"label": "短标签", "text": "自然对白", "prefix": true}
  ]
}
""".strip()


def _agent(llm_config: LLMConfig, agno_db: object, *, phase: str, prompt: str, max_tokens: int = 2200) -> Agent:
    del agno_db
    cfg = llm_for_role(llm_config, "dialogue_audit")
    pipeline_id = {
        "pre": "llm.dialogue_pre_audit",
        "post": "llm.dialogue_post_audit",
        "condition": "llm.dialogue_condition_audit",
        "recruitment_intent": "llm.dialogue_recruitment_intent",
        "dialogue_action_intent": "llm.dialogue_action_intent",
        "dialogue_route_intent": "llm.dialogue_route_intent",
        "dialogue_suggestions": "llm.dialogue_suggestions",
        "dialogue_pending_recovery": "llm.dialogue_pending_recovery",
        "dialogue_bargain_attitude": "llm.dialogue_bargain_attitude",
        "dialogue_directive_fallback": "llm.dialogue_directive_fallback",
        "dialogue_directive_pressure": "llm.dialogue_directive_pressure",
        "dialogue_directive_followup": "llm.dialogue_directive_followup",
        "dialogue_eunuch_lore_intake": "llm.dialogue_eunuch_lore_intake",
        "dialogue_unknown_mention_intake": "llm.dialogue_unknown_mention_intake",
    }.get(phase, "llm.dialogue_condition_audit")
    return Agent(
        name=f"奏对审计-{phase}",
        id=f"dialogue-audit-{phase}",
        model=create_chat_model(
            cfg,
            temperature=0.1,
            top_p=0.7,
            max_tokens=llm_output_token_budget(
                pipeline_id,
                cfg.max_tokens,
                requested=max_tokens,
                minimum=1200,
            ),
            enable_thinking=False,
            force_json_output=True,
        ),
        instructions=[prompt],
        add_history_to_context=False,
        markdown=False,
    )


def pre_dialogue_audit(
    db: Any,
    state: GameState,
    character: Character,
    user_text: str,
    *,
    active_goal: Optional[Dict[str, object]] = None,
    llm_config: Optional[LLMConfig] = None,
    agno_db: object = None,
    audit_client: object = None,
) -> PreDialogueAudit:
    payload = _context_payload(db, state, character, active_goal=active_goal)
    payload["user_text"] = user_text
    _attach_behavior_context(payload, character, text=user_text)
    try:
        fake = _call_fake(audit_client, "pre", payload)
        if fake is not None:
            return _normalize_pre(fake)
        if llm_config is None:
            return _audit_failure("未配置 LLM，奏对预审不落档。")
        agent = _agent(llm_config, agno_db, phase="pre", prompt=PRE_AUDIT_PROMPT, max_tokens=1800)
        raw = run_agent_text(agent, json.dumps(payload, ensure_ascii=False, sort_keys=False), tag="dialogue-audit/pre")
        data = parse_agent_json(raw, "奏对预审")
        return _normalize_pre(data)
    except Exception as exc:
        return _audit_failure(str(exc))


def recruitment_intent_audit(
    db: Any,
    state: GameState,
    character: Character,
    user_text: str,
    action: Dict[str, object],
    *,
    pending_action: Optional[Dict[str, object]] = None,
    llm_config: Optional[LLMConfig] = None,
    agno_db: object = None,
    audit_client: object = None,
) -> Dict[str, object]:
    payload = _context_payload(db, state, character)
    payload["user_text"] = user_text
    payload["tool_action"] = {
        key: value
        for key, value in (action or {}).items()
        if key in {"type", "phase", "kind", "need", "office", "recommender", "note", "trigger_quote"}
    }
    payload["pending_action"] = {
        key: value
        for key, value in (pending_action or {}).items()
        if key in {"type", "kind", "need", "office", "recommender", "note", "trigger_quote"}
    }
    _attach_behavior_context(payload, character, text=user_text)
    try:
        fake = _call_fake(audit_client, "recruitment_intent", payload)
        if fake is not None:
            return _normalize_recruitment_intent(fake)
        if llm_config is None:
            return _normalize_recruitment_intent({
                "allow": False,
                "phase": "none",
                "kind": "",
                "confidence": 0,
                "private_reason": "未配置 LLM，用人管道不落库。",
            })
        agent = _agent(
            llm_config,
            agno_db,
            phase="recruitment_intent",
            prompt=RECRUITMENT_INTENT_PROMPT,
            max_tokens=900,
        )
        raw = run_agent_text(
            agent,
            json.dumps(payload, ensure_ascii=False, sort_keys=False),
            tag="dialogue-audit/recruitment-intent",
        )
        data = parse_agent_json(raw, "用人意图审计")
        return _normalize_recruitment_intent(data)
    except Exception as exc:
        return _normalize_recruitment_intent({
            "allow": False,
            "phase": "none",
            "kind": "",
            "confidence": 0,
            "private_reason": str(exc),
        })


def dialogue_action_intent_audit(
    db: Any,
    state: GameState,
    character: Character,
    user_text: str,
    action: Dict[str, object],
    *,
    pending_action: Optional[Dict[str, object]] = None,
    llm_config: Optional[LLMConfig] = None,
    agno_db: object = None,
    audit_client: object = None,
) -> Dict[str, object]:
    payload = _context_payload(db, state, character)
    payload["user_text"] = user_text
    payload["tool_action"] = {
        key: value
        for key, value in (action or {}).items()
        if key in {
            "type",
            "phase",
            "target",
            "actor",
            "faction",
            "kind",
            "mode",
            "note",
            "scheme_text",
            "title",
            "content",
            "assignee",
            "trigger_quote",
        }
    }
    payload["pending_action"] = {
        key: value
        for key, value in (pending_action or {}).items()
        if key in {"type", "target", "actor", "faction", "kind", "mode", "note", "scheme_text", "title", "content", "assignee", "trigger_quote"}
    }
    _attach_behavior_context(payload, character, text=user_text)
    try:
        fake = _call_fake(audit_client, "dialogue_action_intent", payload)
        if fake is not None:
            return _normalize_dialogue_action_intent(fake)
        if llm_config is None:
            return _normalize_dialogue_action_intent({
                "allow": False,
                "phase": "none",
                "action_type": "none",
                "confidence": 0,
                "private_reason": "未配置 LLM，对白动作管道不落库。",
            })
        agent = _agent(
            llm_config,
            agno_db,
            phase="dialogue_action_intent",
            prompt=DIALOGUE_ACTION_INTENT_PROMPT,
            max_tokens=1000,
        )
        raw = run_agent_text(
            agent,
            json.dumps(payload, ensure_ascii=False, sort_keys=False),
            tag="dialogue-audit/action-intent",
        )
        data = parse_agent_json(raw, "对白动作意图审计")
        return _normalize_dialogue_action_intent(data)
    except Exception as exc:
        return _normalize_dialogue_action_intent({
            "allow": False,
            "phase": "none",
            "action_type": "none",
            "confidence": 0,
            "private_reason": str(exc),
        })


def dialogue_route_intent_audit(
    db: Any,
    state: GameState,
    character: Character,
    user_text: str,
    *,
    pending_action: Optional[Dict[str, object]] = None,
    route_context: Optional[Dict[str, object]] = None,
    llm_config: Optional[LLMConfig] = None,
    agno_db: object = None,
    audit_client: object = None,
) -> Dict[str, object]:
    payload = _context_payload(db, state, character)
    payload["user_text"] = user_text
    payload["pending_action"] = {
        key: value
        for key, value in (pending_action or {}).items()
        if key in {"type", "target", "actor", "faction", "kind", "mode", "note", "scheme_text", "trigger_quote"}
    }
    payload["route_context"] = route_context if isinstance(route_context, dict) else {}
    _attach_behavior_context(payload, character, text=user_text)
    try:
        fake = _call_fake(audit_client, "dialogue_route_intent", payload)
        if fake is not None:
            return _normalize_dialogue_route_intent(fake)
        if llm_config is None:
            return _normalize_dialogue_route_intent({
                "allow": False,
                "intent": "none",
                "confidence": 0,
                "private_reason": "未配置 LLM，对白路由不抢答。",
            })
        agent = _agent(
            llm_config,
            agno_db,
            phase="dialogue_route_intent",
            prompt=DIALOGUE_ROUTE_INTENT_PROMPT,
            max_tokens=900,
        )
        raw = run_agent_text(
            agent,
            json.dumps(payload, ensure_ascii=False, sort_keys=False),
            tag="dialogue-audit/route-intent",
        )
        data = parse_agent_json(raw, "对白路由意图审计")
        return _normalize_dialogue_route_intent(data)
    except Exception as exc:
        return _normalize_dialogue_route_intent({
            "allow": False,
            "intent": "none",
            "confidence": 0,
            "private_reason": str(exc),
        })


def dialogue_pending_recovery_audit(
    db: Any,
    state: GameState,
    character: Character,
    user_text: str,
    recent_answers: List[str],
    *,
    llm_config: Optional[LLMConfig] = None,
    agno_db: object = None,
    audit_client: object = None,
) -> Dict[str, object]:
    payload = _context_payload(db, state, character)
    payload["user_text"] = user_text
    payload["recent_proposals"] = [
        {"index": idx + 1, "answer": _compact(answer, 520)}
        for idx, answer in enumerate((recent_answers or [])[:4])
        if str(answer or "").strip()
    ]
    _attach_behavior_context(payload, character, text=user_text)
    try:
        fake = _call_fake(audit_client, "dialogue_pending_recovery", payload)
        if fake is not None:
            return _normalize_dialogue_pending_recovery(fake)
        if llm_config is None:
            return _normalize_dialogue_pending_recovery({
                "allow": False,
                "phase": "none",
                "action_type": "none",
                "confidence": 0,
                "private_reason": "未配置 LLM，不从旧对白恢复待办动作。",
            })
        agent = _agent(
            llm_config,
            agno_db,
            phase="dialogue_pending_recovery",
            prompt=DIALOGUE_PENDING_RECOVERY_PROMPT,
            max_tokens=900,
        )
        raw = run_agent_text(
            agent,
            json.dumps(payload, ensure_ascii=False, sort_keys=False),
            tag="dialogue-audit/pending-recovery",
        )
        data = parse_agent_json(raw, "待办动作恢复审计")
        return _normalize_dialogue_pending_recovery(data)
    except Exception as exc:
        return _normalize_dialogue_pending_recovery({
            "allow": False,
            "phase": "none",
            "action_type": "none",
            "confidence": 0,
            "private_reason": str(exc),
        })


def dialogue_bargain_attitude_audit(
    db: Any,
    state: GameState,
    character: Character,
    user_text: str,
    answer: str,
    context: Dict[str, object],
    *,
    llm_config: Optional[LLMConfig] = None,
    agno_db: object = None,
    audit_client: object = None,
) -> Dict[str, object]:
    payload = _context_payload(db, state, character)
    payload["user_text"] = user_text
    payload["npc_answer"] = answer
    payload["bargain_context"] = {
        key: value
        for key, value in (context or {}).items()
        if key in {"kind", "ref_kind", "actor", "target", "title", "meta", "summary", "ref_id", "id"}
    }
    _attach_behavior_context(payload, character, text=f"{user_text}\n{answer}")
    try:
        fake = _call_fake(audit_client, "dialogue_bargain_attitude", payload)
        if fake is not None:
            return _normalize_dialogue_bargain_attitude(fake)
        if llm_config is None:
            return _normalize_dialogue_bargain_attitude({
                "allow": False,
                "attitude": "none",
                "confidence": 0,
                "private_reason": "未配置 LLM，不写入御前交易态度。",
            })
        agent = _agent(
            llm_config,
            agno_db,
            phase="dialogue_bargain_attitude",
            prompt=DIALOGUE_BARGAIN_ATTITUDE_PROMPT,
            max_tokens=700,
        )
        raw = run_agent_text(
            agent,
            json.dumps(payload, ensure_ascii=False, sort_keys=False),
            tag="dialogue-audit/bargain-attitude",
        )
        data = parse_agent_json(raw, "御前请求态度审计")
        return _normalize_dialogue_bargain_attitude(data)
    except Exception as exc:
        return _normalize_dialogue_bargain_attitude({
            "allow": False,
            "attitude": "none",
            "confidence": 0,
            "private_reason": str(exc),
        })


def dialogue_directive_fallback_audit(
    db: Any,
    state: GameState,
    character: Character,
    user_text: str,
    answer: str,
    *,
    llm_config: Optional[LLMConfig] = None,
    agno_db: object = None,
    audit_client: object = None,
) -> Dict[str, object]:
    payload = _context_payload(db, state, character)
    payload["user_text"] = user_text
    payload["npc_answer"] = answer
    _attach_behavior_context(payload, character, text=f"{user_text}\n{answer}")
    try:
        fake = _call_fake(audit_client, "dialogue_directive_fallback", payload)
        if fake is not None:
            return _normalize_dialogue_directive_fallback(fake)
        if llm_config is None:
            return _normalize_dialogue_directive_fallback({
                "allow": False,
                "confidence": 0,
                "private_reason": "未配置 LLM，不自动生成拟旨草案。",
            })
        agent = _agent(
            llm_config,
            agno_db,
            phase="dialogue_directive_fallback",
            prompt=DIALOGUE_DIRECTIVE_FALLBACK_PROMPT,
            max_tokens=900,
        )
        raw = run_agent_text(
            agent,
            json.dumps(payload, ensure_ascii=False, sort_keys=False),
            tag="dialogue-audit/directive-fallback",
        )
        data = parse_agent_json(raw, "拟旨兜底审计")
        return _normalize_dialogue_directive_fallback(data)
    except Exception as exc:
        return _normalize_dialogue_directive_fallback({
            "allow": False,
            "confidence": 0,
            "private_reason": str(exc),
        })


def dialogue_directive_pressure_audit(
    db: Any,
    state: GameState,
    character: Character,
    user_text: str,
    answer: str,
    directive_context: Dict[str, object],
    *,
    llm_config: Optional[LLMConfig] = None,
    agno_db: object = None,
    audit_client: object = None,
) -> Dict[str, object]:
    payload = _context_payload(db, state, character)
    payload["user_text"] = user_text
    payload["npc_answer"] = answer
    payload["directive_context"] = {
        key: value
        for key, value in (directive_context or {}).items()
        if key in {
            "id",
            "text",
            "assignee",
            "status",
            "lifecycle_status",
            "progress",
            "anomaly",
            "eta_day",
            "exec_days",
            "chain",
            "context",
        }
    }
    _attach_behavior_context(payload, character, text=f"{user_text}\n{answer}")
    try:
        fake = _call_fake(audit_client, "dialogue_directive_pressure", payload)
        if fake is not None:
            return _normalize_dialogue_directive_pressure(fake)
        if llm_config is None:
            return _normalize_dialogue_directive_pressure({
                "allow": False,
                "kind": "none",
                "confidence": 0,
                "private_reason": "未配置 LLM，不由奏对推进旨意生命周期。",
            })
        agent = _agent(
            llm_config,
            agno_db,
            phase="dialogue_directive_pressure",
            prompt=DIALOGUE_DIRECTIVE_PRESSURE_PROMPT,
            max_tokens=850,
        )
        raw = run_agent_text(
            agent,
            json.dumps(payload, ensure_ascii=False, sort_keys=False),
            tag="dialogue-audit/directive-pressure",
        )
        data = parse_agent_json(raw, "旨意召对压力审计")
        return _normalize_dialogue_directive_pressure(data)
    except Exception as exc:
        return _normalize_dialogue_directive_pressure({
            "allow": False,
            "kind": "none",
            "confidence": 0,
            "private_reason": str(exc),
        })


def dialogue_directive_followup_audit(
    db: Any,
    state: GameState,
    character: Character,
    user_text: str,
    answer: str,
    directive_context: Dict[str, object],
    *,
    llm_config: Optional[LLMConfig] = None,
    agno_db: object = None,
    audit_client: object = None,
) -> Dict[str, object]:
    payload = _context_payload(db, state, character)
    payload["user_text"] = user_text
    payload["npc_answer"] = answer
    payload["directive_context"] = {
        key: value
        for key, value in (directive_context or {}).items()
        if key in {
            "id",
            "text",
            "assignee",
            "status",
            "lifecycle_status",
            "progress",
            "integrity_actual",
            "integrity_reported",
            "settle_note",
            "outcome_status",
            "chain",
            "context",
        }
    }
    _attach_behavior_context(payload, character, text=f"{user_text}\n{answer}")
    try:
        fake = _call_fake(audit_client, "dialogue_directive_followup", payload)
        if fake is not None:
            return _normalize_dialogue_directive_followup(fake)
        if llm_config is None:
            return _normalize_dialogue_directive_followup({
                "allow": False,
                "kind": "none",
                "confidence": 0,
                "private_reason": "未配置 LLM，不由奏对记录办结旨意复命。",
            })
        agent = _agent(
            llm_config,
            agno_db,
            phase="dialogue_directive_followup",
            prompt=DIALOGUE_DIRECTIVE_FOLLOWUP_PROMPT,
            max_tokens=850,
        )
        raw = run_agent_text(
            agent,
            json.dumps(payload, ensure_ascii=False, sort_keys=False),
            tag="dialogue-audit/directive-followup",
        )
        data = parse_agent_json(raw, "办结旨意复命审计")
        return _normalize_dialogue_directive_followup(data)
    except Exception as exc:
        return _normalize_dialogue_directive_followup({
            "allow": False,
            "kind": "none",
            "confidence": 0,
            "private_reason": str(exc),
        })


def dialogue_suggestions_audit(
    db: Any,
    state: GameState,
    character: Character,
    seed_suggestions: List[Dict[str, object]],
    *,
    pending_action: Optional[Dict[str, object]] = None,
    live_recent_dialogue: Optional[List[Dict[str, object]]] = None,
    llm_config: Optional[LLMConfig] = None,
    agno_db: object = None,
    audit_client: object = None,
) -> List[Dict[str, object]]:
    payload = _context_payload(db, state, character)
    payload["seed_suggestions"] = [
        {
            "label": _compact(row.get("label"), 20),
            "text": _compact(row.get("text"), 220),
            "prefix": bool(row.get("prefix", False)),
        }
        for row in (seed_suggestions or [])[:8]
        if isinstance(row, dict)
    ]
    payload["pending_action"] = {
        key: value
        for key, value in (pending_action or {}).items()
        if key in {"type", "target", "actor", "faction", "kind", "mode", "note", "scheme_text", "trigger_quote"}
    }
    payload["live_recent_dialogue"] = [
        {
            "role": _compact(row.get("role"), 20),
            "content": _compact(row.get("content"), 360),
        }
        for row in (live_recent_dialogue or [])[-8:]
        if isinstance(row, dict)
    ]
    _attach_behavior_context(payload, character)
    try:
        fake = _call_fake(audit_client, "dialogue_suggestions", payload)
        if fake is not None:
            return _normalize_dialogue_suggestions(fake)
        if llm_config is None:
            return []
        agent = _agent(
            llm_config,
            agno_db,
            phase="dialogue_suggestions",
            prompt=DIALOGUE_SUGGESTIONS_PROMPT,
            max_tokens=900,
        )
        raw = run_agent_text(
            agent,
            json.dumps(payload, ensure_ascii=False, sort_keys=False),
            tag="dialogue-audit/suggestions",
        )
        data = parse_agent_json(raw, "自然奏对建议")
        return _normalize_dialogue_suggestions(data)
    except Exception:
        return []


def dialogue_eunuch_lore_intake_audit(
    db: Any,
    state: GameState,
    character: Character,
    text: str,
    *,
    candidate_names: Optional[List[str]] = None,
    pending_target: str = "",
    source_role: str = "",
    llm_config: Optional[LLMConfig] = None,
    agno_db: object = None,
    audit_client: object = None,
) -> Dict[str, object]:
    payload = _context_payload(db, state, character)
    payload["text"] = text
    payload["source_role"] = _compact(source_role, 40)
    payload["current_speaker"] = character.name
    payload["candidate_names"] = _list_strings(candidate_names or [], limit=8, item_limit=80)
    payload["pending_target"] = _compact(pending_target, 80)
    _attach_behavior_context(payload, character, text=text)
    try:
        fake = _call_fake(audit_client, "dialogue_eunuch_lore_intake", payload)
        if fake is not None:
            return _normalize_dialogue_eunuch_lore_intake(fake)
        if llm_config is None:
            return _normalize_dialogue_eunuch_lore_intake({
                "allow": False,
                "target_names": [],
                "confidence": 0,
                "private_reason": "未配置 LLM，净身旧档入档审计不落库。",
            })
        agent = _agent(
            llm_config,
            agno_db,
            phase="dialogue_eunuch_lore_intake",
            prompt=DIALOGUE_EUNUCH_LORE_INTAKE_PROMPT,
            max_tokens=900,
        )
        raw = run_agent_text(
            agent,
            json.dumps(payload, ensure_ascii=False, sort_keys=False),
            tag="dialogue-audit/eunuch-lore-intake",
        )
        data = parse_agent_json(raw, "净身旧档入档审计")
        return _normalize_dialogue_eunuch_lore_intake(data)
    except Exception as exc:
        return _normalize_dialogue_eunuch_lore_intake({
            "allow": False,
            "target_names": [],
            "confidence": 0,
            "private_reason": str(exc),
        })


def dialogue_unknown_mention_intake_audit(
    db: Any,
    state: GameState,
    character: Character,
    text: str,
    *,
    candidate_names: Optional[List[str]] = None,
    purpose: str = "cache_candidate",
    llm_config: Optional[LLMConfig] = None,
    agno_db: object = None,
    audit_client: object = None,
) -> Dict[str, object]:
    payload = _context_payload(db, state, character)
    payload["text"] = text
    payload["purpose"] = _compact(purpose, 40)
    payload["current_speaker"] = character.name
    payload["candidate_names"] = _list_strings(candidate_names or [], limit=10, item_limit=80)
    _attach_behavior_context(payload, character, text=text)
    try:
        fake = _call_fake(audit_client, "dialogue_unknown_mention_intake", payload)
        if fake is not None:
            return _normalize_dialogue_unknown_mention_intake(fake)
        if llm_config is None:
            return _normalize_dialogue_unknown_mention_intake({
                "allow": False,
                "accepted_names": [],
                "confidence": 0,
                "private_reason": "未配置 LLM，未知人物候选入池审计不落库。",
            })
        agent = _agent(
            llm_config,
            agno_db,
            phase="dialogue_unknown_mention_intake",
            prompt=DIALOGUE_UNKNOWN_MENTION_INTAKE_PROMPT,
            max_tokens=900,
        )
        raw = run_agent_text(
            agent,
            json.dumps(payload, ensure_ascii=False, sort_keys=False),
            tag="dialogue-audit/unknown-mention-intake",
        )
        data = parse_agent_json(raw, "对白人物线索入池审计")
        return _normalize_dialogue_unknown_mention_intake(data)
    except Exception as exc:
        return _normalize_dialogue_unknown_mention_intake({
            "allow": False,
            "accepted_names": [],
            "confidence": 0,
            "private_reason": str(exc),
        })


def post_dialogue_audit(
    db: Any,
    state: GameState,
    character: Character,
    user_text: str,
    answer: str,
    *,
    active_goal: Optional[Dict[str, object]] = None,
    pre_audit: Optional[PreDialogueAudit] = None,
    source_context: Optional[Dict[str, object]] = None,
    llm_config: Optional[LLMConfig] = None,
    agno_db: object = None,
    audit_client: object = None,
) -> PostDialogueAudit:
    payload = _context_payload(db, state, character, active_goal=active_goal)
    payload["user_text"] = user_text
    payload["npc_answer"] = answer
    payload["pre_audit"] = pre_audit.raw if isinstance(pre_audit, PreDialogueAudit) else {}
    if isinstance(source_context, dict):
        payload["briefing_context"] = {
            key: value
            for key, value in source_context.items()
            if key in {
                "card_key",
                "kind",
                "title",
                "actor",
                "target",
                "ref_kind",
                "ref_id",
                "source_type",
                "source_id",
                "meta",
                "ask",
                "exchange",
                "refusal",
            }
        }
    _attach_behavior_context(payload, character, text=f"{user_text}\n{answer}")
    existing_threshold = int((active_goal or {}).get("threshold") or 70)
    try:
        fake = _call_fake(audit_client, "post", payload)
        if fake is not None:
            return _normalize_post(fake, existing_threshold=existing_threshold)
        if llm_config is None:
            return _post_failure("未配置 LLM，奏对后审不落档。")
        agent = _agent(llm_config, agno_db, phase="post", prompt=POST_AUDIT_PROMPT, max_tokens=3000)
        raw = run_agent_text(agent, json.dumps(payload, ensure_ascii=False, sort_keys=False), tag="dialogue-audit/post")
        data = parse_agent_json(raw, "奏对后审")
        return _normalize_post(data, existing_threshold=existing_threshold)
    except Exception as exc:
        return _post_failure(str(exc))


def review_goal_conditions_audit(
    db: Any,
    state: GameState,
    goal: Dict[str, object],
    evidence_context: str,
    *,
    phase: str = "preresolve",
    llm_config: Optional[LLMConfig] = None,
    agno_db: object = None,
    audit_client: object = None,
) -> PostDialogueAudit:
    minister = str(goal.get("minister_name") or "")
    character = None
    try:
        content = getattr(db, "content", None)
        character = (getattr(content, "characters", {}) or {}).get(minister)
    except Exception:
        character = None
    if character is None:
        character = Character(
            name=minister,
            office="",
            office_type="",
            faction="",
            aliases=[],
            personal_skills=[],
            loyalty=50,
            ability=50,
            integrity=50,
            courage=50,
            style="",
            power_id="ming",
        )
    payload = _context_payload(db, state, character, active_goal=goal)
    payload.update({
        "phase": phase,
        "goal": goal,
        "evidence_context": evidence_context,
    })
    _attach_behavior_context(
        payload,
        character,
        text=f"{goal.get('title') or ''}\n{goal.get('target_text') or ''}\n{evidence_context}",
    )
    try:
        fake = _call_fake(audit_client, "condition", payload)
        if fake is not None:
            data = dict(fake)
        else:
            if llm_config is None:
                return _post_failure("未配置 LLM，条件审计不落档。")
            agent = _agent(llm_config, agno_db, phase="condition", prompt=CONDITION_AUDIT_PROMPT, max_tokens=2200)
            raw = run_agent_text(agent, json.dumps(payload, ensure_ascii=False, sort_keys=False), tag=f"dialogue-audit/condition/{phase}")
            data = parse_agent_json(raw, f"奏对条件审计/{phase}")
        status = _enum(data.get("goal_status"), {"waiting_conditions", "sealed", "blocked", "expired"}, "waiting_conditions")
        prior_consent, prior_consent_evidence = _identity_consent_from_goal(goal)
        private_reason = _compact(data.get("private_reason") or "", 360)
        consent_evidence = _compact(data.get("explicit_consent_evidence") or "", 260)
        if prior_consent and prior_consent_evidence:
            private_reason = _compact(
                f"{private_reason}；既有身份转换自愿证据：{prior_consent_evidence}",
                520,
            )
        elif consent_evidence:
            private_reason = _compact(f"{private_reason}；身份转换自愿证据：{consent_evidence}", 520)
        normalized = {
            "goal_decision": "continue",
            "action_kind": goal.get("action_kind") or "general",
            "title": goal.get("title") or "",
            "target_text": goal.get("target_text") or "",
            "stance": "caution",
            "handshake_status": "sealed" if status == "sealed" else "blocked" if status == "blocked" else "conditional",
            "goal_status": status,
            "score_delta": 0,
            "score_after": data.get("score_after") if status == "sealed" else goal.get("score") or 0,
            "threshold": goal.get("threshold") or 70,
            "conditions": data.get("conditions") or goal.get("conditions") or [],
            "blockers": data.get("blockers") or [],
            "explicit_consent": bool(data.get("explicit_consent") or prior_consent),
            "agreement_action": "create_achieved" if status == "sealed" and str(goal.get("action_kind") or "") in INSTANT_AGREEMENT_ACTIONS else "create_pending" if status == "sealed" else "none",
            "public_hint": data.get("public_hint") or "",
            "private_reason": private_reason,
            "confidence": data.get("confidence") or 0,
        }
        return _normalize_post(normalized, existing_threshold=int(goal.get("threshold") or 70))
    except Exception as exc:
        return _post_failure(str(exc))
