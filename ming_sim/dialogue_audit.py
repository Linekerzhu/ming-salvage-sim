"""LLM-backed dialogue audit for goals, stance, and agreements.

This module is the semantic authority for summons after the dialogue-goal
refactor. Regex helpers may still exist elsewhere for legacy display, but new
goal/stance/agreement state should be derived from these audited JSON objects or
not recorded at all.
"""

from __future__ import annotations

import json
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
        out.append({
            "turn": int(row["turn"] or 0),
            "role": str(row["role"] or ""),
            "content": _compact(row["content"], 700),
        })
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
    blockers = _list_strings(data.get("blockers"), limit=8, item_limit=120)
    explicit_consent = bool(data.get("explicit_consent"))
    agreement_action = _enum(data.get("agreement_action"), AGREEMENT_ACTIONS, "none")
    directive_action = _enum(data.get("directive_action"), DIRECTIVE_ACTIONS, "none")
    directive_text = _compact(data.get("directive_text"), 1800)
    public_hint = _compact(data.get("public_hint"), 180)
    private_reason = _compact(data.get("private_reason") or data.get("reason"), 400)

    if decision in {"new", "continue", "switch"} and action_kind == "general":
        return _post_failure("目的类 action_kind 不可为 general", raw=data)

    pending_conditions = [item for item in conditions if item.get("status") == "pending"]
    failed_conditions = [item for item in conditions if item.get("status") == "failed"]

    def guard_conditioned_seal() -> None:
        nonlocal goal_status, handshake, agreement_action, score_after
        if goal_status == "sealed" and failed_conditions:
            goal_status = "blocked"
            handshake = "blocked"
            agreement_action = "none"
            score_after = min(score_after, threshold - 1)
            if "条件审计判定有条件失败，不能握手达成" not in blockers:
                blockers.append("条件审计判定有条件失败，不能握手达成")
        elif goal_status == "sealed" and pending_conditions:
            goal_status = "waiting_conditions"
            handshake = "conditional"
            agreement_action = "none"
            score_after = min(score_after, threshold - 1)
            if "仍有条件待证，不能提前握手达成" not in blockers:
                blockers.append("仍有条件待证，不能提前握手达成")

    guard_conditioned_seal()

    if goal_status == "waiting_conditions":
        handshake = "conditional"
        agreement_action = "none"
        if not conditions:
            blockers.append("等待条件但审计未给出条件")
    elif goal_status == "sealed":
        handshake = "sealed"
        score_after = 100
        if action_kind in INSTANT_AGREEMENT_ACTIONS:
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

    guard_conditioned_seal()

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
                blockers.append("身份转换缺少明确自愿净身证据")
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
    return {
        "turn": {"year": state.year, "period": state.period, "turn": state.turn},
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
    return "\n".join(part for part in parts if part.strip())


def _attach_behavior_context(payload: Dict[str, object], character: Character, *, text: str = "") -> None:
    source_text = _behavior_source_text(payload, text)
    try:
        profile = npc_dialogue_behavior_profile(character.name, text=source_text)
    except Exception:
        profile = {}
    try:
        brief = npc_dialogue_behavior_brief(character.name, text=source_text)
    except Exception:
        brief = ""
    payload["behavior_profile"] = profile if isinstance(profile, dict) else {}
    payload["behavior_brief"] = str(brief or "")[:1400]
    payload["behavior_source_excerpt"] = source_text[:1200]


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
- waiting_conditions 不得创建 agreement。
- 只有 sealed 后才可 create_achieved/create_pending。
- policy、secret_order、court_commitment sealed 后默认 create_pending；personnel、castration、emancipation 若即时完成才 create_achieved。
- castration/emancipation 必须 explicit_consent=true 且 private_reason/public_hint 说明 NPC 原文明确自愿，否则不能 sealed。
- recent_dialogue 是近期原文上下文，可用于续接指代；但 post audit 必须优先引用本轮 NPC 原文回复。
- 已 sealed/achieved 的目的只作为背景和账本证据；不要把它重新当作 active goal 推进，也不要让 NPC 每轮复述。
- pre_audit 为 none 且本轮文本没有明确谈判标的时，通常 goal_decision=none；不要因为历史 goal 存在而补判。
- “臣谨听”“容臣斟酌”“不敢不从”不是 sealed；分别更接近 active/conditional/blocked，须结合原文证据。
- 若 pre_audit.npc_guidance 或隐藏档案提示半真半假、阳奉阴违、护短、政敌牵动，NPC 的客套答应、泛泛称是、转移矛头不得直接判 sealed；必须有清楚承诺、可审计条件或实际工具落库。
- behavior_profile / behavior_brief 是本轮人格-关系-记忆行为档案；若 truth_mode、risk_tags、network_pressure 显示话术、护短、政敌或旧事压力，必须写入 private_reason/blockers/conditions 的判断依据。
- NPC 告状、构陷、甩锅、误导玩家时，stance 可为 support/caution/oppose，但 private_reason 必须写明这是“话术/风险”，不要把所有话都当事实。
- conditional 只能用于 NPC 提出可验证条件、边界或交换；conditions 要写成未来可审计条目。
- sealed 需要 NPC 对 target_text 有明确承诺、清楚接受，或 waiting 条件已被证据满足。
- 若本轮只是把同一 active_goal 从粗目标细化为具体官职/授权/名分/条件，输出 goal_relation=refine_goal，并把 title/target_text 改成修订后的版本；不要创建多个 goal。
- 若确属另一个目标，输出 goal_relation=distinct_goal；若旧目标应让位，goal_decision=switch。
- 若 active_goal 正在 waiting_conditions，而玩家/NPC 原文表明要求的明旨、授权、人手、钱粮、名分、保全、期限等已经给足，conditions 对应项应标 done；NPC 随即接受标的时可 sealed。
- 如果 NPC 原文已经给出一段可直接进入旨意库的完整草案、条陈式诏令或“臣已拟旨如下”，但没有工具调用痕迹，输出 directive_action=propose_pending，并把 directive_text 填为可入库草案。只有建议、原则、口头意见、零散条款时仍为 none。

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
  "blockers": ["阻碍"],
  "explicit_consent": false,
  "agreement_action": "none|create_achieved|create_pending|bind_existing",
  "directive_action": "none|propose_pending",
  "directive_text": "NPC 已拟成、可入库的旨意草案；无则空字符串",
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


def _agent(llm_config: LLMConfig, agno_db: object, *, phase: str, prompt: str, max_tokens: int = 2200) -> Agent:
    del agno_db
    cfg = llm_for_role(llm_config, "dialogue_audit")
    pipeline_id = {
        "pre": "llm.dialogue_pre_audit",
        "post": "llm.dialogue_post_audit",
        "condition": "llm.dialogue_condition_audit",
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


def post_dialogue_audit(
    db: Any,
    state: GameState,
    character: Character,
    user_text: str,
    answer: str,
    *,
    active_goal: Optional[Dict[str, object]] = None,
    pre_audit: Optional[PreDialogueAudit] = None,
    llm_config: Optional[LLMConfig] = None,
    agno_db: object = None,
    audit_client: object = None,
) -> PostDialogueAudit:
    payload = _context_payload(db, state, character, active_goal=active_goal)
    payload["user_text"] = user_text
    payload["npc_answer"] = answer
    payload["pre_audit"] = pre_audit.raw if isinstance(pre_audit, PreDialogueAudit) else {}
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
