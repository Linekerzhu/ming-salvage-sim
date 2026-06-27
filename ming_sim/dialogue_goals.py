"""Shared dialogue-goal state machine for summons.

Conversation goals are the psychological handshake layer. They sit between raw
chat and the agreement ledger: a goal may be active or waiting on conditions;
only sealed goals become negotiation agreements.
"""

from __future__ import annotations

import json
import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ming_sim.dialogue_audit import (
    PreDialogueAudit,
    review_goal_conditions_audit,
)
from ming_sim.dialogue_semantics import DialogueSemanticEngine
from ming_sim.effect_catalog import task_risk_profiles_from_payload
from ming_sim.context import npc_dialogue_behavior_profile
from ming_sim.models import Character, GameState, LLMConfig
from ming_sim.negotiation import (
    HANDSHAKE_BLOCKED,
    HANDSHAKE_CONDITIONAL,
    HANDSHAKE_NONE,
    HANDSHAKE_SEALED,
    commitment_required,
    handshake_label,
)


GOAL_ACTIVE = "active"
GOAL_WAITING = "waiting_conditions"
GOAL_SEALED = "sealed"
GOAL_BLOCKED = "blocked"
GOAL_ABANDONED = "abandoned"
GOAL_EXPIRED = "expired"

INSTANT_AGREEMENT_ACTIONS = {"castration", "emancipation", "personnel"}


@dataclass
class DialogueAgreementDecision:
    intent: str = "none"
    target: str = ""
    agreement_formed: bool = False
    performance_status: str = "none"
    conditions: List[Dict[str, str]] = field(default_factory=list)
    tasks: List[str] = field(default_factory=list)
    due_turn: int = 0
    evidence: str = ""
    confidence: int = 0
    source_card_key: str = ""
    blocked_reason: str = ""
    agreement_resolution: str = "none"
    agreement_id: int = 0
    political_cost_level: str = "none"

@dataclass
class GoalDetection:
    action_kind: str = ""
    title: str = ""
    target_text: str = ""
    confidence: int = 0
    source: str = "none"
    abandon: bool = False
    switches_goal: bool = False
    reason: str = ""

    @property
    def has_goal(self) -> bool:
        return bool(self.action_kind and (self.title or self.target_text) and not self.abandon)


@dataclass
class PreparedDialogue:
    prefix: str = ""
    behavior_context: str = ""
    behavior_brief: str = ""
    detection: GoalDetection = field(default_factory=GoalDetection)
    active_goal: Optional[Dict[str, object]] = None
    preview_goal: Optional[Dict[str, object]] = None
    pre_audit: Optional[PreDialogueAudit] = None


def _compact(text: str, limit: int = 120) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "").strip())
    return cleaned[:limit]


def _unique_strings(items: List[object], *, limit: int = 10) -> List[str]:
    out: List[str] = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


CONSEQUENCE_FIELDS = (
    "character_status_changes",
    "condition_changes",
    "punishment_changes",
)
CONSEQUENCE_CONFIDENCE_FLOOR = 70


def _dialogue_source_id(state: GameState, character: Character, source_chat_turn_id: int) -> str:
    if source_chat_turn_id:
        return f"chat_turn:{int(source_chat_turn_id)}"
    return f"{state.year}-{state.period}-turn-{state.turn}:{character.name}"


def _brief_card_key_from_context(source_context: Optional[Dict[str, object]]) -> str:
    if not isinstance(source_context, dict):
        return ""
    existing = str(source_context.get("card_key") or "").strip()
    if existing:
        return existing
    kind = str(source_context.get("kind") or "").strip()
    if not kind:
        return ""
    raw = "|".join(
        str(source_context.get(key) or "").strip()
        for key in ("kind", "ref_kind", "ref_id", "actor", "target", "title")
    )
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:14]
    safe_kind = "".join(ch for ch in kind if ch.isalnum() or ch in {"_", "-"})
    return f"{safe_kind or 'brief'}:{digest}"


def _source_context_summary(source_context: Optional[Dict[str, object]]) -> Dict[str, str]:
    if not isinstance(source_context, dict):
        return {}
    card_key = _brief_card_key_from_context(source_context)
    if not card_key:
        return {}
    return {
        "card_key": card_key,
        "kind": _compact(str(source_context.get("kind") or ""), 40),
        "title": _compact(str(source_context.get("title") or ""), 160),
        "actor": _compact(str(source_context.get("actor") or ""), 80),
        "target": _compact(str(source_context.get("target") or ""), 80),
        "source_type": _compact(
            str(source_context.get("source_type") or source_context.get("ref_kind") or ""),
            80,
        ),
        "source_id": _compact(
            str(source_context.get("source_id") or source_context.get("ref_id") or ""),
            120,
        ),
    }


def _post_agreement_formed(post: Any) -> bool:
    raw = getattr(post, "raw", {}) if isinstance(getattr(post, "raw", {}), dict) else {}
    if isinstance(raw.get("agreement_formed"), bool):
        return bool(raw.get("agreement_formed"))
    return str(getattr(post, "handshake_status", "") or "") == HANDSHAKE_SEALED and str(getattr(post, "agreement_action", "") or "") in {
        "create_achieved",
        "create_pending",
        "bind_existing",
    }


def _post_performance_status(post: Any, *, agreement_id: int = 0) -> str:
    raw = getattr(post, "raw", {}) if isinstance(getattr(post, "raw", {}), dict) else {}
    status = str(raw.get("performance_status") or "").strip()
    if status in {"fulfilled", "pending", "blocked", "rejected", "waived", "superseded", "none"}:
        return status
    if str(getattr(post, "handshake_status", "") or "") == HANDSHAKE_BLOCKED:
        return "blocked"
    if agreement_id:
        return "pending" if str(getattr(post, "agreement_action", "") or "") == "create_pending" else "fulfilled"
    return "none"


def _post_agreement_resolution(post: Any) -> str:
    raw = getattr(post, "raw", {}) if isinstance(getattr(post, "raw", {}), dict) else {}
    resolution = str(getattr(post, "agreement_resolution", "") or raw.get("agreement_resolution") or "").strip()
    if resolution in {"fulfill", "fail", "block", "waive", "supersede", "amend", "append_task"}:
        return resolution
    performance = str(raw.get("performance_status") or "").strip()
    return {
        "fulfilled": "fulfill",
        "blocked": "block",
        "rejected": "fail",
        "waived": "waive",
        "superseded": "supersede",
    }.get(performance, "none")


def _post_resolution_agreement_id(post: Any, active_goal: Optional[Dict[str, object]], db: Any, minister_name: str) -> int:
    raw = getattr(post, "raw", {}) if isinstance(getattr(post, "raw", {}), dict) else {}
    for value in (
        getattr(post, "agreement_id", 0),
        raw.get("agreement_id") if isinstance(raw, dict) else 0,
        raw.get("supersedes_agreement_id") if isinstance(raw, dict) else 0,
        getattr(post, "supersedes_agreement_id", 0),
        (active_goal or {}).get("agreement_id") if active_goal else 0,
    ):
        try:
            agreement_id = int(value or 0)
        except (TypeError, ValueError):
            agreement_id = 0
        if agreement_id > 0:
            return agreement_id
    try:
        rows = [
            row for row in db.list_negotiation_agreements(minister_name=minister_name, limit=8)
            if str(row.get("status") or "") in {"pending", "sealed"}
        ]
    except Exception:
        rows = []
    return int(rows[0].get("id") or 0) if len(rows) == 1 else 0


def _post_resolution_new_tasks(post: Any) -> List[str]:
    tasks = list(getattr(post, "new_tasks", []) or [])
    raw = getattr(post, "raw", {}) if isinstance(getattr(post, "raw", {}), dict) else {}
    if not tasks and isinstance(raw.get("new_tasks"), list):
        tasks = [str(item or "").strip() for item in raw.get("new_tasks") or [] if str(item or "").strip()]
    return [_compact(task, 180) for task in tasks if _compact(task, 180)][:8]


def _post_resolution_detail(post: Any, key: str, limit: int = 240) -> str:
    raw = getattr(post, "raw", {}) if isinstance(getattr(post, "raw", {}), dict) else {}
    return _compact(str(getattr(post, key, "") or raw.get(key) or ""), limit)


def _post_trigger_quote(post: Any) -> str:
    raw = getattr(post, "raw", {}) if isinstance(getattr(post, "raw", {}), dict) else {}
    if not isinstance(raw, dict):
        return ""
    return _compact(
        str(
            raw.get("trigger_quote")
            or raw.get("agreement_trigger_quote")
            or raw.get("goal_trigger_quote")
            or raw.get("user_trigger_quote")
            or ""
        ),
        180,
    )


def _post_quote_supported_by_player_text(post: Any, user_text: str) -> bool:
    return _quote_supported_by_player_text(_post_trigger_quote(post), user_text)


def _dialogue_agreement_decision(
    post: Any,
    *,
    source_context: Optional[Dict[str, object]] = None,
    agreement_id: int = 0,
) -> DialogueAgreementDecision:
    context = _source_context_summary(source_context)
    raw = getattr(post, "raw", {}) if isinstance(getattr(post, "raw", {}), dict) else {}
    tasks = list(getattr(post, "tasks", []) or [])
    if not tasks:
        for item in getattr(post, "conditions", []) or []:
            if isinstance(item, dict) and str(item.get("status") or "") != "done":
                text = _compact(str(item.get("description") or ""), 180)
                if text and text not in tasks:
                    tasks.append(text)
    resolved_agreement_id = int(agreement_id or 0)
    if resolved_agreement_id <= 0:
        for value in (
            getattr(post, "agreement_id", 0),
            raw.get("agreement_id"),
            raw.get("supersedes_agreement_id"),
            getattr(post, "supersedes_agreement_id", 0),
        ):
            try:
                resolved_agreement_id = int(value or 0)
            except (TypeError, ValueError):
                resolved_agreement_id = 0
            if resolved_agreement_id > 0:
                break
    return DialogueAgreementDecision(
        intent=str(raw.get("intent") or getattr(post, "action_kind", "") or "none"),
        target=str(getattr(post, "target_text", "") or getattr(post, "title", "") or ""),
        agreement_formed=_post_agreement_formed(post) or bool(resolved_agreement_id),
        performance_status=_post_performance_status(post, agreement_id=resolved_agreement_id),
        conditions=list(getattr(post, "conditions", []) or []),
        tasks=tasks[:8],
        due_turn=int(raw.get("due_turn") or raw.get("due_turns") or 0) if str(raw.get("due_turn") or raw.get("due_turns") or "").isdigit() else 0,
        evidence=_compact(str(getattr(post, "private_reason", "") or getattr(post, "public_hint", "")), 500),
        confidence=int(getattr(post, "confidence", 0) or 0),
        source_card_key=context.get("card_key", ""),
        blocked_reason=_compact(str(raw.get("blocked_reason") or "；".join(getattr(post, "blockers", []) or [])), 240),
        agreement_resolution=_post_agreement_resolution(post),
        agreement_id=resolved_agreement_id,
        political_cost_level=str(getattr(post, "political_cost_level", "") or raw.get("political_cost_level") or "none"),
    )


def _briefing_resolution_for_decision(post: Any, decision: DialogueAgreementDecision, *, agreement_id: int = 0) -> str:
    raw = getattr(post, "raw", {}) if isinstance(getattr(post, "raw", {}), dict) else {}
    explicit = str(raw.get("card_resolution") or "").strip()
    if explicit in {"handled", "pending", "fulfilled", "blocked", "rejected", "waived", "superseded"}:
        return explicit
    if decision.performance_status in {"fulfilled", "pending", "blocked", "rejected", "waived", "superseded"}:
        return decision.performance_status
    if agreement_id or decision.agreement_formed:
        return "pending" if decision.tasks else "fulfilled"
    if str(getattr(post, "handshake_status", "") or "") == HANDSHAKE_BLOCKED:
        return "blocked"
    return ""


def _record_source_briefing_resolution(
    db: Any,
    state: GameState,
    post: Any,
    *,
    user_text: str = "",
    source_context: Optional[Dict[str, object]],
    agreement_id: int = 0,
    source_chat_turn_id: int = 0,
) -> DialogueAgreementDecision:
    decision = _dialogue_agreement_decision(post, source_context=source_context, agreement_id=agreement_id)
    context = _source_context_summary(source_context)
    if not context or decision.confidence < 70:
        return decision
    resolution = _briefing_resolution_for_decision(post, decision, agreement_id=agreement_id)
    if not resolution:
        return decision
    if not _post_quote_supported_by_player_text(post, user_text):
        return decision
    try:
        db.record_briefing_card_resolution(
            state,
            card_key=context["card_key"],
            source_type=context.get("source_type", ""),
            source_id=context.get("source_id", ""),
            kind=context.get("kind", ""),
            title=context.get("title", ""),
            actor=context.get("actor", ""),
            target=context.get("target", ""),
            resolution=resolution,
            decision={
                "source": "llm_dialogue_audit",
                "agreement_id": int(agreement_id or 0),
                "agreement_formed": decision.agreement_formed,
                "performance_status": decision.performance_status,
                "tasks": decision.tasks,
                "conditions": decision.conditions,
                "evidence": decision.evidence,
                "blocked_reason": decision.blocked_reason,
                "audit": getattr(post, "raw", {}) if isinstance(getattr(post, "raw", {}), dict) else {},
            },
            chat_turn_id=source_chat_turn_id,
        )
    except Exception:
        pass
    return decision


def _agreement_resolution_required_response(
    post: Any,
    *,
    dialogue_consequences: Dict[str, object],
    proposed_directive: Dict[str, object],
    reason: str = "",
) -> Dict[str, object]:
    hint = reason or "已入账的奏对事项不能直接丢弃；请在奏对中说明免除、改约、追加条件或追责，由语义账本裁断。"
    return {
        "audit_status": "recorded",
        "event": "agreement_resolution_required",
        "proposed_directive": proposed_directive,
        "dialogue_consequences": dialogue_consequences,
        "agreement_id": 0,
        "agreement_resolution": _post_agreement_resolution(post),
        "public_hint": hint,
        "audit_confidence": int(getattr(post, "confidence", 0) or 0),
    }


def _resolve_existing_agreement_from_post(
    db: Any,
    state: GameState,
    character: Character,
    post: Any,
    *,
    user_text: str,
    active_goal: Optional[Dict[str, object]],
    source_chat_turn_id: int,
    dialogue_consequences: Dict[str, object],
    proposed_directive: Dict[str, object],
    source_context: Optional[Dict[str, object]],
) -> Optional[Dict[str, object]]:
    resolution = _post_agreement_resolution(post)
    if resolution == "none":
        return None
    if not _post_quote_supported_by_player_text(post, user_text):
        return _agreement_resolution_required_response(
            post,
            dialogue_consequences=dialogue_consequences,
            proposed_directive=proposed_directive,
            reason="未改动履约账本：缺少玩家本轮原话中的免除、改约、确认履约或追责证据。",
        )
    agreement_id = _post_resolution_agreement_id(post, active_goal, db, character.name)
    if agreement_id <= 0:
        return _agreement_resolution_required_response(
            post,
            dialogue_consequences=dialogue_consequences,
            proposed_directive=proposed_directive,
            reason="未改动履约账本：本轮语义没有对应到明确的旧账本条目。",
        )
    evidence = (
        _post_resolution_detail(post, "resolution_reason", 400)
        or str(getattr(post, "private_reason", "") or "")
        or str(getattr(post, "public_hint", "") or "")
    )
    result = db.resolve_negotiation_agreement_semantically(
        state,
        agreement_id,
        resolution=resolution,
        evidence=evidence,
        political_cost_level=_post_resolution_detail(post, "political_cost_level", 20) or "none",
        revised_target_text=_post_resolution_detail(post, "revised_target_text", 240),
        new_tasks=_post_resolution_new_tasks(post),
        llm_review=getattr(post, "raw", {}) if isinstance(getattr(post, "raw", {}), dict) else {},
        source_chat_turn_id=source_chat_turn_id,
    )
    agreement_decision = _record_source_briefing_resolution(
        db,
        state,
        post,
        user_text=user_text,
        source_context=source_context,
        agreement_id=agreement_id,
        source_chat_turn_id=source_chat_turn_id,
    )
    goal = result.get("goal") if isinstance(result, dict) else None
    return {
        "audit_status": "recorded",
        "goal": goal,
        "agreement_id": agreement_id,
        "agreement_resolution": resolution,
        "agreement_result": result,
        "proposed_directive": proposed_directive,
        "dialogue_consequences": dialogue_consequences,
        "agreement_decision": agreement_decision.__dict__,
        "event": f"agreement_{resolution}",
        "handshake_status": str(getattr(post, "handshake_status", "") or "none"),
        "score": int(getattr(post, "score_after", 0) or 0),
        "public_hint": str(getattr(post, "public_hint", "") or result.get("summary") or ""),
        "audit_confidence": int(getattr(post, "confidence", 0) or 0),
    }


def _immediate_consequence_enabled(raw: Dict[str, object]) -> bool:
    value = raw.get("immediate_consequence")
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"true", "yes", "y", "1", "apply", "executed"}


def _immediate_consequence_trigger_quote(raw: Dict[str, object]) -> str:
    return _compact(
        str(raw.get("trigger_quote") or raw.get("immediate_consequence_quote") or ""),
        180,
    )


def _quote_supported_by_dialogue_text(quote: str, combined_text: str) -> bool:
    clean_quote = re.sub(r"\s+", "", str(quote or ""))
    if not clean_quote:
        return False
    clean_text = re.sub(r"\s+", "", str(combined_text or ""))
    if clean_quote in clean_text:
        return True
    normalized_quote = re.sub(r"[\W_]+", "", str(quote or ""), flags=re.UNICODE)
    if not normalized_quote:
        return False
    normalized_text = re.sub(r"[\W_]+", "", str(combined_text or ""), flags=re.UNICODE)
    return bool(normalized_text and normalized_quote in normalized_text)


def _quote_supported_by_player_text(quote: str, user_text: str) -> bool:
    if _quote_supported_by_dialogue_text(quote, user_text):
        return True
    clean_quote = re.sub(r"[\W_]+", "", str(quote or ""), flags=re.UNICODE)
    if not clean_quote:
        return False
    clean_user = re.sub(r"[\W_]+", "", str(user_text or ""), flags=re.UNICODE)
    return clean_quote in clean_user


def _known_dialogue_consequence_name(db: Any, name: str) -> bool:
    content = getattr(db, "content", None)
    characters = getattr(content, "characters", None)
    if not isinstance(characters, dict):
        return True
    return str(name or "").strip() in characters


def _consequence_item_evidence_quote(item: Dict[str, object]) -> str:
    for key in ("evidence_quote", "source_quote", "quote", "reason"):
        value = _compact(str(item.get(key) or ""), 180)
        if value:
            return value
    return ""


def _consequence_item_allowed(item: Dict[str, object], *, db: Any, character: Character, combined_text: str) -> bool:
    name = str(item.get("name") or "").strip()
    if not name:
        return False
    if not _known_dialogue_consequence_name(db, name):
        return False
    evidence_quote = _consequence_item_evidence_quote(item)
    if not _quote_supported_by_dialogue_text(evidence_quote, combined_text):
        return False
    if name == character.name:
        return True
    return name in combined_text


def _filter_dialogue_consequence_items(
    items: object,
    *,
    db: Any,
    character: Character,
    combined_text: str,
) -> List[Dict[str, object]]:
    if not isinstance(items, list):
        return []
    out: List[Dict[str, object]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        clean = dict(item)
        if _consequence_item_allowed(clean, db=db, character=character, combined_text=combined_text):
            out.append(clean)
    return out


def _extract_dialogue_immediate_consequences(
    post_raw: Dict[str, object],
    *,
    db: Any,
    character: Character,
    combined_text: str,
) -> Dict[str, object]:
    if not _immediate_consequence_enabled(post_raw):
        return {}
    extracted: Dict[str, object] = {}
    for field in CONSEQUENCE_FIELDS:
        filtered = _filter_dialogue_consequence_items(
            post_raw.get(field),
            db=db,
            character=character,
            combined_text=combined_text,
        )
        if filtered:
            extracted[field] = filtered
    return extracted


def _has_effective_dialogue_consequence(applied: Dict[str, object]) -> bool:
    for field in CONSEQUENCE_FIELDS:
        rows = applied.get(field) or []
        if any(isinstance(row, dict) and not row.get("rejected") for row in rows):
            return True
    return False


def _apply_dialogue_immediate_consequences(
    db: Any,
    state: GameState,
    character: Character,
    post_raw: Dict[str, object],
    *,
    user_text: str,
    combined_text: str,
    source_chat_turn_id: int,
    confidence: int,
) -> Dict[str, object]:
    if int(confidence or 0) < CONSEQUENCE_CONFIDENCE_FLOOR:
        return {}
    trigger_quote = _immediate_consequence_trigger_quote(post_raw)
    if not _quote_supported_by_player_text(trigger_quote, user_text):
        return {}
    extracted = _extract_dialogue_immediate_consequences(
        post_raw,
        db=db,
        character=character,
        combined_text=combined_text,
    )
    if not extracted:
        return {}
    from ming_sim.issues import apply_score_extraction

    extracted["_source_kind"] = "dialogue"
    extracted["_source_id"] = _dialogue_source_id(state, character, source_chat_turn_id)
    applied = apply_score_extraction(db, state, extracted)
    return {
        "source_kind": "dialogue",
        "source_id": extracted["_source_id"],
        "applied": {field: applied.get(field) or [] for field in CONSEQUENCE_FIELDS},
        "effective": _has_effective_dialogue_consequence(applied),
    }


def _dialogue_speech_profile(
    db: Any,
    state: GameState,
    character: Character,
    user_text: str,
    answer: str,
    *,
    context_text: str = "",
) -> Dict[str, object]:
    _ = (db, state)
    profile_text = "\n".join(
        part for part in (str(user_text or ""), str(context_text or "")[:3000]) if part.strip()
    )
    try:
        behavior = npc_dialogue_behavior_profile(character.name, text=profile_text, character=character)
    except Exception:
        behavior = {}
    network = behavior.get("network_pressure") if isinstance(behavior.get("network_pressure"), dict) else {}
    combined = f"{user_text}\n{answer}"
    rival_hit = bool(network.get("rivals"))
    ally_hit = bool(network.get("allies") or network.get("obligations"))
    accusation_markers = ("弹劾", "参劾", "告发", "奏劾", "清算", "查办", "构陷", "旧案", "余党", "奸党")
    shielding_markers = ("留余地", "从缓", "会审", "证据", "不可株连", "保全", "转圜", "求情", "护")
    misdirection_markers = ("另议", "容臣", "再查", "未可遽定", "风闻", "臣不敢尽知", "待查", "或有")

    speech_acts: List[str] = []
    if rival_hit and any(marker in combined for marker in accusation_markers):
        speech_acts.append("accusation")
    if ally_hit and any(marker in combined for marker in shielding_markers):
        speech_acts.append("shielding")
    truth_mode = str(behavior.get("truth_mode") or "直陈为主")
    if truth_mode == "半真半假" or any(marker in combined for marker in misdirection_markers):
        speech_acts.append("misdirection")
    elif truth_mode == "选择性真话":
        speech_acts.append("selective_truth")
    if not speech_acts:
        speech_acts.append("plain_advice")

    risk_tags = [str(tag) for tag in behavior.get("risk_tags") or []]
    if "accusation" in speech_acts and "政敌告状" not in risk_tags:
        risk_tags.append("政敌告状")
    if "shielding" in speech_acts and "人情护短" not in risk_tags:
        risk_tags.append("人情护短")
    if "misdirection" in speech_acts and "话术不实" not in risk_tags:
        risk_tags.append("话术不实")

    return {
        "preferred_stance": behavior.get("preferred_stance") or "neutral",
        "tone": behavior.get("tone") or "",
        "truth_mode": truth_mode,
        "speech_acts": _unique_strings(speech_acts, limit=4),
        "network_pressure": {
            "rivals": _unique_strings(list(network.get("rivals") or []), limit=6),
            "allies": _unique_strings(list(network.get("allies") or []), limit=6),
            "obligations": _unique_strings(list(network.get("obligations") or []), limit=6),
            "traits": _unique_strings(list(network.get("traits") or []), limit=8),
        },
        "risk_tags": _unique_strings(risk_tags, limit=10),
    }


def _speech_profile_summary(profile: Dict[str, object]) -> str:
    acts = {
        "accusation": "借政敌旧怨告状/奏劾",
        "shielding": "因同党人情护短转圜",
        "misdirection": "半真半假或以话术留暗门",
        "selective_truth": "选择性说真话",
        "plain_advice": "常规奏对",
    }
    labels = [acts.get(str(item), str(item)) for item in profile.get("speech_acts") or []]
    truth = str(profile.get("truth_mode") or "")
    pressure = profile.get("network_pressure") if isinstance(profile.get("network_pressure"), dict) else {}
    rivals = "、".join(str(item) for item in pressure.get("rivals") or [])
    allies = "、".join(str(item) for item in pressure.get("allies") or [])
    parts = [part for part in ["；".join(labels), f"真话策略：{truth}" if truth else "", f"牵涉政敌：{rivals}" if rivals else "", f"牵涉同党：{allies}" if allies else ""] if part]
    return "；".join(parts)[:180]


_GOAL_COMMON_PHRASES = (
    "劝其", "本人", "同意", "接受", "支持", "协办", "承办", "任命", "安排", "目的",
    "愿意", "是否", "可否", "明旨", "授权", "名分", "条件", "本次", "奏对",
)


def _goal_relation_explicit(audit: Any) -> bool:
    raw = getattr(audit, "raw", {}) if isinstance(getattr(audit, "raw", {}), dict) else {}
    return "goal_relation" in raw


def _goal_action_kind(active_goal: Optional[Dict[str, object]], audit: Any) -> tuple[str, str]:
    active_kind = str((active_goal or {}).get("action_kind") or "")
    action_kind = str(getattr(audit, "action_kind", "") or "")
    return active_kind, action_kind


def _distinctive_goal_ngrams(text: str) -> set[str]:
    cleaned = str(text or "")
    for phrase in _GOAL_COMMON_PHRASES:
        cleaned = cleaned.replace(phrase, "")
    chunks = re.findall(r"[\u4e00-\u9fffA-Za-z0-9]+", cleaned)
    grams: set[str] = set()
    for chunk in chunks:
        if len(chunk) == 2:
            grams.add(chunk)
        elif len(chunk) > 2:
            grams.update(chunk[index:index + 2] for index in range(len(chunk) - 1))
    return {gram for gram in grams if len(gram) >= 2}


def _goal_text_overlaps(active_goal: Dict[str, object], audit: Any) -> bool:
    old_text = f"{active_goal.get('title') or ''}\n{active_goal.get('target_text') or ''}"
    new_text = f"{getattr(audit, 'title', '') or ''}\n{getattr(audit, 'target_text', '') or ''}"
    old_grams = _distinctive_goal_ngrams(old_text)
    new_grams = _distinctive_goal_ngrams(new_text)
    return bool(old_grams and new_grams and old_grams.intersection(new_grams))


def _goal_audit_distinct_from_active(active_goal: Optional[Dict[str, object]], audit: Any) -> bool:
    if not active_goal or audit is None or not getattr(audit, "valid", False) or not getattr(audit, "has_goal", False):
        return False
    relation = str(getattr(audit, "goal_relation", "") or "")
    active_kind, action_kind = _goal_action_kind(active_goal, audit)
    if active_kind and action_kind and active_kind != action_kind:
        return True
    return relation == "distinct_goal" and _goal_relation_explicit(audit)


def _goal_audit_refines_active(active_goal: Optional[Dict[str, object]], audit: Any) -> bool:
    if not active_goal or audit is None or not getattr(audit, "valid", False) or not getattr(audit, "has_goal", False):
        return False
    relation = str(getattr(audit, "goal_relation", "") or "")
    active_kind, action_kind = _goal_action_kind(active_goal, audit)
    if active_kind and action_kind and active_kind != action_kind:
        return False
    if relation in {"same_goal", "refine_goal"}:
        return True
    if relation == "distinct_goal" and _goal_relation_explicit(audit):
        return False
    decision = str(getattr(audit, "goal_decision", "") or "")
    if action_kind and active_kind and action_kind == active_kind and decision == "continue":
        return True
    return bool(action_kind and active_kind and action_kind == active_kind and decision in {"new", "switch"} and _goal_text_overlaps(active_goal, audit))


def _revised_goal_text(goal: Dict[str, object], audit: Any) -> Dict[str, str]:
    return {
        "title": _compact(str(getattr(audit, "title", "") or goal.get("title") or ""), 120),
        "target_text": _compact(str(getattr(audit, "target_text", "") or goal.get("target_text") or ""), 240),
    }


def detect_conversation_goal(
    db: Any,
    state: GameState,
    character: Character,
    user_text: str,
    *,
    active_goal: Optional[Dict[str, object]] = None,
    llm_config: Optional[LLMConfig] = None,
    agno_db: object = None,
    audit_client: object = None,
) -> GoalDetection:
    audit = DialogueSemanticEngine(
        db,
        state,
        llm_config=llm_config,
        agno_db=agno_db,
        audit_client=audit_client,
    ).evaluate_pre_dialogue(
        character,
        user_text,
        active_goal=active_goal,
    )
    if not audit.valid:
        return GoalDetection(source="llm_audit_failed", reason=audit.error)
    detection = GoalDetection(
        action_kind="" if audit.action_kind == "general" else audit.action_kind,
        title=audit.title,
        target_text=audit.target_text,
        confidence=audit.confidence,
        source="llm_audit",
        abandon=audit.abandon,
        switches_goal=audit.goal_decision == "switch",
        reason=audit.private_reason or audit.public_hint,
    )
    return detection


def prepare_dialogue_context(
    db: Any,
    state: GameState,
    character: Character,
    user_text: str,
    *,
    llm_config: Optional[LLMConfig] = None,
    agno_db: object = None,
    audit_client: object = None,
    persistent: bool = True,
) -> PreparedDialogue:
    if not persistent:
        return PreparedDialogue()
    active = db.get_active_conversation_goal(character.name)
    audit = DialogueSemanticEngine(
        db,
        state,
        llm_config=llm_config,
        agno_db=agno_db,
        audit_client=audit_client,
    ).evaluate_pre_dialogue(
        character,
        user_text,
        active_goal=active,
    )
    detection = GoalDetection()
    if audit.valid:
        detection = GoalDetection(
            action_kind="" if audit.action_kind == "general" else audit.action_kind,
            title=audit.title,
            target_text=audit.target_text,
            confidence=audit.confidence,
            source="llm_audit",
            abandon=audit.abandon,
            reason=audit.private_reason or audit.public_hint,
        )
        if active and detection.has_goal:
            detection.switches_goal = audit.goal_decision == "switch"

    preview: Optional[Dict[str, object]] = active
    mode = "续接"
    refines_active = _goal_audit_refines_active(active, audit)
    if not audit.valid:
        return PreparedDialogue(detection=detection, active_goal=active, preview_goal=None, pre_audit=audit)
    if audit.goal_decision == "none":
        return PreparedDialogue(detection=detection, active_goal=active, preview_goal=None, pre_audit=audit)
    if detection.abandon and active:
        mode = "放弃"
    elif detection.has_goal:
        if refines_active and active:
            mode = "修正"
            revised = _revised_goal_text(active, audit)
            preview = {
                **active,
                "title": revised["title"],
                "target_text": revised["target_text"],
            }
        else:
            mode = "改立" if active else "拟立"
            preview = {
                "action_kind": detection.action_kind,
                "title": detection.title,
                "target_text": detection.target_text,
                "status": GOAL_ACTIVE,
                "score": 0,
                "threshold": commitment_required(detection.action_kind),
                "condition_status": "none",
                "conditions": [],
            }

    if not preview and not detection.abandon:
        return PreparedDialogue(detection=detection, active_goal=active, preview_goal=None, pre_audit=audit)

    lines = ["【召对目的提示（隐藏机制；可转化为局内拟旨感，不要复述机制名）】"]
    if detection.abandon and active:
        lines.append(f"- 玩家可能要放弃当前目的：{active.get('title') or active.get('target_text')}")
        lines.append("- 若玩家确是放弃，作出符合身份的反应；不要继续假装已经成约。")
    elif preview:
        score = int(preview.get("score") or 0)
        threshold = int(preview.get("threshold") or commitment_required(str(preview.get("action_kind") or "general")))
        conditions = preview.get("conditions") if isinstance(preview.get("conditions"), list) else []
        lines.append(f"- 本轮{mode}目的：{preview.get('title') or preview.get('target_text')}。")
        lines.append(f"- 心理进度：{score}% / 目标阈值 {threshold}。")
        if conditions:
            pending = "；".join(str(item.get("description") or "") for item in conditions if isinstance(item, dict) and item.get("status") != "done")
            if pending:
                lines.append(f"- 已开条件待证：{pending}。若玩家已明确满足，可承认条件闭环。")
        if refines_active:
            lines.append("- 这是同一奏对目的的修正或细化，不要当成另一项新任务。")
        lines.append("- 围绕该目的回应：真愿意就明说承诺；需要条件就列 1-3 条可履约条件；不能接受就明确拒绝或保留。")
    if audit.npc_guidance:
        lines.append(f"- 审计指引：{audit.npc_guidance}")
    return PreparedDialogue(prefix="\n".join(lines), detection=detection, active_goal=active, preview_goal=preview, pre_audit=audit)


def related_issue_for_chat(db: Any, text: str) -> int:
    combined = text or ""
    try:
        rows = db.list_active_issues()
    except Exception:
        rows = []
    for row in rows:
        title = str(row["title"] or "")
        try:
            tags = json.loads(str(row["tags"] or "[]"))
        except Exception:
            tags = []
        needles = [title, *[str(tag) for tag in tags]]
        if any(needle and needle[:12] in combined for needle in needles):
            return int(row["id"])
    return 0


def _agreement_tasks_for_goal(goal: Dict[str, object]) -> List[str]:
    action_kind = str(goal.get("action_kind") or "general")
    target = str(goal.get("target_text") or goal.get("title") or "本次奏对标的")
    if action_kind in INSTANT_AGREEMENT_ACTIONS:
        return []
    if action_kind == "secret_order":
        return [f"完成密办/取证并回报：{target}"[:180]]
    if action_kind == "policy":
        return [f"在后续执行中实际协办或背书：{target}"[:180]]
    if action_kind == "court_commitment":
        return [f"实际履行奏对约定：{target}"[:180]]
    return []


def _agreement_tasks_from_audit(
    audit: Optional[Dict[str, object]],
    conditions: List[Dict[str, str]],
    action_kind: str = "",
) -> List[str]:
    tasks: List[str] = []
    if isinstance(audit, dict):
        raw_tasks = audit.get("tasks")
        if isinstance(raw_tasks, list):
            for item in raw_tasks:
                text = _compact(str(item or ""), 180)
                if _is_material_agreement_task(text, action_kind) and text not in tasks:
                    tasks.append(text)
        elif isinstance(raw_tasks, str):
            text = _compact(raw_tasks, 180)
            if _is_material_agreement_task(text, action_kind):
                tasks.append(text)
    if not tasks:
        for item in conditions:
            if not isinstance(item, dict):
                continue
            if str(item.get("status") or "pending") == "done":
                continue
            text = _compact(str(item.get("description") or ""), 180)
            if _is_material_agreement_task(text, action_kind) and text not in tasks:
                tasks.append(text)
    return tasks[:3]


def _is_material_agreement_task(text: str, action_kind: str = "") -> bool:
    """Keep only concrete, auditable tasks out of an LLM audit payload."""

    clean = _compact(text, 180)
    if len(clean) < 4:
        return False
    if re.search(r"^(实际履行|继续推进|完成本次|落实本次|本次奏对|奏对目的|口头表态)", clean):
        return False
    if action_kind in {"castration", "emancipation"}:
        return bool(re.search(r"净身|入内廷|脱籍|民籍|家眷|安置|保全|明旨|限期|回奏|证据", clean))
    return bool(
        re.search(
            r"银|钱|饷|粮|人手|属官|书吏|兵|官|任|授|补|擢|调|职|名分|"
            r"明旨|圣旨|廷议|会审|期限|限期|日|月|旬|证|账|册|回奏|复命|"
            r"密|保密|线索|取证|家眷|安置|保全|担保|画押|试差|共办|查|"
            r"交|拨|筹|验|提交|入档|承办|协办|办成",
            clean,
        )
    )


def _audit_text_field(audit: Optional[Dict[str, object]], key: str, limit: int = 160) -> str:
    if not isinstance(audit, dict):
        return ""
    value = audit.get(key)
    if isinstance(value, list):
        value = "；".join(str(item or "") for item in value if str(item or "").strip())
    return _compact(str(value or ""), limit)


def _audit_due_turn(state: GameState, audit: Optional[Dict[str, object]], tasks: List[str]) -> int:
    if not tasks:
        return 0
    raw = None
    if isinstance(audit, dict):
        raw = audit.get("due_turn", audit.get("due_turns"))
    try:
        due = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        due = 0
    if due <= 0:
        due = int(state.turn) + 1
    elif due <= int(state.turn):
        due = int(state.turn) + max(1, due)
    return due


def _create_agreement_for_goal(
    db: Any,
    state: GameState,
    goal: Dict[str, object],
    *,
    stance_id: int = 0,
    summary: str = "",
    conditions: str = "",
    audit: Optional[Dict[str, object]] = None,
    agreement_action: str = "",
) -> int:
    existing = int(goal.get("agreement_id") or 0)
    if existing:
        return existing
    action_kind = str(goal.get("action_kind") or "general")
    condition_items: List[Dict[str, str]] = []
    raw_conditions = goal.get("conditions")
    if isinstance(raw_conditions, list):
        condition_items = [item for item in raw_conditions if isinstance(item, dict)]
    tasks = _agreement_tasks_from_audit(audit, condition_items, action_kind)
    if agreement_action == "create_pending" and not tasks and action_kind not in INSTANT_AGREEMENT_ACTIONS:
        return 0
    status = "sealed" if not tasks or agreement_action == "create_achieved" else "pending"
    score = int(goal.get("score") or 100)
    threshold = int(goal.get("threshold") or commitment_required(action_kind))
    topic = str(goal.get("title") or goal.get("target_text") or "本次奏对目的")
    promise_type = _audit_text_field(audit, "promise_type", 80) or action_kind
    stakes = _audit_text_field(audit, "stakes", 160) or _audit_text_field(audit, "blocked_reason", 160)
    due_turn = _audit_due_turn(state, audit, tasks)
    task_risk_profiles = task_risk_profiles_from_payload(
        audit or {},
        actor=str(goal.get("minister_name") or ""),
        limit=max(1, len(tasks) or 1),
    )
    agreement_id = db.create_negotiation_agreement(
        state,
        minister_name=str(goal.get("minister_name") or ""),
        topic=topic,
        action_kind=action_kind,
        status=status,
        stance_id=stance_id,
        goal_id=int(goal.get("id") or 0),
        handshake_status=HANDSHAKE_SEALED,
        psychological_score=score,
        threshold=threshold,
        verbal_only=not tasks,
        core_topic=topic,
        target_text=str(goal.get("target_text") or topic),
        promise_type=promise_type,
        stakes=stakes,
        due_turn=due_turn,
        conditions=conditions,
        summary=summary or f"奏对目的已握手：{topic}",
        tasks=tasks,
        task_risk_profiles=task_risk_profiles,
    )
    if audit:
        try:
            db.conn.execute(
                "UPDATE negotiation_agreements SET llm_review_json=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (json.dumps(audit, ensure_ascii=False), int(agreement_id)),
            )
            db.conn.commit()
        except Exception:
            pass
    db.bind_conversation_goal_agreement(int(goal.get("id") or 0), agreement_id)
    return agreement_id


def _create_directive_from_audit(
    db: Any,
    state: GameState,
    character: Character,
    post: Any,
    *,
    user_text: str,
    source_chat_turn_id: int = 0,
    already_recorded: bool = False,
) -> Dict[str, object]:
    if already_recorded:
        return {}
    if str(getattr(post, "directive_action", "") or "") != "propose_pending":
        return {}
    text = str(getattr(post, "directive_text", "") or "").strip()
    if not text:
        return {}
    raw = getattr(post, "raw", {})
    trigger_quote = ""
    if isinstance(raw, dict):
        trigger_quote = _compact(str(raw.get("trigger_quote") or raw.get("directive_trigger_quote") or ""), 180)
    if not _quote_supported_by_player_text(trigger_quote, user_text):
        return {}
    actor = str(getattr(character, "name", "") or "").strip()
    profiles = task_risk_profiles_from_payload(raw if isinstance(raw, dict) else {}, actor=actor, limit=1)
    task_risk_profile = profiles[0] if profiles else {}
    try:
        existing = db.conn.execute(
            """
            SELECT id, text, status, source, notes, actor
            FROM turn_directives
            WHERE turn=? AND actor=? AND text=? AND status IN ('pending','draft')
            ORDER BY id DESC
            LIMIT 1
            """,
            (int(state.turn), actor, text),
        ).fetchone()
    except Exception:
        existing = None
    if existing is not None:
        if task_risk_profile:
            try:
                db.set_task_risk_profile("turn_directives", int(existing["id"]), task_risk_profile)
            except Exception:
                pass
        return {
            "id": int(existing["id"]),
            "text": str(existing["text"] or text),
            "status": str(existing["status"] or "pending"),
            "source": str(existing["source"] or "大臣拟旨"),
            "notes": str(existing["notes"] or ""),
            "actor": str(existing["actor"] or actor),
            "already_recorded": True,
        }
    notes = f"由{actor}拟旨入档（语义审计）"
    if source_chat_turn_id:
        notes += f"；chat_turn={int(source_chat_turn_id)}"
    directive_id = db.add_directive(
        state,
        None,
        text,
        "大臣拟旨",
        actor=actor,
        notes=notes,
        status="pending",
        task_risk_profile=task_risk_profile,
    )
    return {
        "id": int(directive_id),
        "text": text,
        "status": "pending",
        "source": "大臣拟旨",
        "notes": notes,
        "actor": actor,
        "already_recorded": False,
        "trigger_quote": trigger_quote,
        "audit_confidence": int(getattr(post, "confidence", 0) or 0),
    }


def record_dialogue_effects(
    db: Any,
    state: GameState,
    character: Character,
    user_text: str,
    answer: str,
    prepared: Optional[PreparedDialogue] = None,
    *,
    source_chat_turn_id: int = 0,
    llm_config: Optional[LLMConfig] = None,
    agno_db: object = None,
    audit_client: object = None,
    persistent: bool = True,
    directive_already_recorded: bool = False,
    source_context: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    if not persistent:
        return {}
    prepared = prepared or prepare_dialogue_context(
        db,
        state,
        character,
        user_text,
        llm_config=llm_config,
        agno_db=agno_db,
        audit_client=audit_client,
        persistent=True,
    )
    active_goal = prepared.active_goal or db.get_active_conversation_goal(character.name)
    combined = f"{user_text}\n{answer}"
    post = DialogueSemanticEngine(
        db,
        state,
        llm_config=llm_config,
        agno_db=agno_db,
        audit_client=audit_client,
    ).evaluate_post_dialogue(
        character,
        user_text,
        answer,
        active_goal=active_goal,
        pre_audit=prepared.pre_audit,
        source_context=source_context,
    )
    if not post.valid:
        return {
            "audit_status": "not_recorded",
            "event": "audit_failed",
            "error": post.error,
            "public_hint": "本轮奏对审计未落档。",
        }
    proposed_directive = _create_directive_from_audit(
        db,
        state,
        character,
        post,
        user_text=user_text,
        source_chat_turn_id=source_chat_turn_id,
        already_recorded=directive_already_recorded,
    )
    dialogue_consequences = _apply_dialogue_immediate_consequences(
        db,
        state,
        character,
        post.raw,
        user_text=user_text,
        combined_text=combined,
        source_chat_turn_id=source_chat_turn_id,
        confidence=post.confidence,
    )
    agreement_resolution_result = _resolve_existing_agreement_from_post(
        db,
        state,
        character,
        post,
        user_text=user_text,
        active_goal=active_goal,
        source_chat_turn_id=source_chat_turn_id,
        dialogue_consequences=dialogue_consequences,
        proposed_directive=proposed_directive,
        source_context=source_context,
    )
    if agreement_resolution_result is not None:
        return agreement_resolution_result
    if post.goal_decision == "none":
        agreement_decision = _record_source_briefing_resolution(
            db,
            state,
            post,
            user_text=user_text,
            source_context=source_context,
            source_chat_turn_id=source_chat_turn_id,
        )
        return {
            "audit_status": "recorded",
            "event": (
                "dialogue_consequence"
                if dialogue_consequences.get("effective")
                else ("directive_proposed" if proposed_directive else "none")
            ),
            "proposed_directive": proposed_directive,
            "dialogue_consequences": dialogue_consequences,
            "agreement_decision": agreement_decision.__dict__,
            "public_hint": post.public_hint,
            "audit_confidence": post.confidence,
        }

    refines_active = _goal_audit_refines_active(active_goal, post) or _goal_audit_refines_active(active_goal, prepared.pre_audit)
    distinct_active = _goal_audit_distinct_from_active(active_goal, post) or _goal_audit_distinct_from_active(active_goal, prepared.pre_audit)
    if refines_active and active_goal:
        post.goal_decision = "continue"
        post.goal_relation = "refine_goal"
    elif distinct_active and active_goal:
        post.goal_decision = "switch"
        post.goal_relation = "distinct_goal"

    active_agreement_id = int((active_goal or {}).get("agreement_id") or 0)
    if active_agreement_id and post.goal_decision in {"abandon", "new", "switch"} and not refines_active:
        return _agreement_resolution_required_response(
            post,
            dialogue_consequences=dialogue_consequences,
            proposed_directive=proposed_directive,
        )

    if post.goal_decision == "abandon" and active_goal:
        updated = db.abandon_conversation_goal(
            state,
            int(active_goal["id"]),
            reason=post.public_hint or post.private_reason or "玩家主动放弃当前奏对目的。",
            source_chat_turn_id=source_chat_turn_id,
        )
        return {
            "audit_status": "recorded",
            "goal": updated,
            "event": "abandoned",
            "dialogue_consequences": dialogue_consequences,
            "public_hint": post.public_hint,
            "audit_confidence": post.confidence,
        }

    if active_goal and post.goal_decision in {"new", "switch"} and not refines_active:
        db.update_conversation_goal(
            int(active_goal["id"]),
            state=state,
            event_kind="switched",
            event_summary=f"玩家转入新目的：{post.title or post.target_text}",
            source_chat_turn_id=source_chat_turn_id,
            status=GOAL_ABANDONED,
            abandoned_reason=f"转入新目的：{post.title or post.target_text}"[:180],
            last_delta_json={"audit": post.raw, "public_hint": post.public_hint},
        )
        active_goal = None

    related_issue_id = related_issue_for_chat(db, combined)
    goal: Optional[Dict[str, object]] = active_goal
    if post.has_goal and goal is None:
        goal_id = db.create_conversation_goal(
            state,
            minister_name=character.name,
            action_kind=post.action_kind,
            title=post.title or post.target_text or "本次奏对目的",
            target_text=post.target_text or post.title,
            threshold=post.threshold,
            score=0,
            status=GOAL_ACTIVE,
            condition_status="none",
            related_issue_id=related_issue_id,
            source_chat_turn_id=source_chat_turn_id,
            expires_turn=int(state.turn) + 1,
            last_delta={"audit_source": "llm", "confidence": post.confidence, "pre_audit": (prepared.pre_audit.raw if prepared.pre_audit else {})},
        )
        goal = db.get_conversation_goal(goal_id)
    if goal is None:
        agreement_decision = _record_source_briefing_resolution(
            db,
            state,
            post,
            user_text=user_text,
            source_context=source_context,
            source_chat_turn_id=source_chat_turn_id,
        )
        return {
            "audit_status": "recorded",
            "event": "dialogue_consequence" if dialogue_consequences.get("effective") else "none",
            "dialogue_consequences": dialogue_consequences,
            "agreement_decision": agreement_decision.__dict__,
            "public_hint": post.public_hint,
            "audit_confidence": post.confidence,
        }

    old_score = int(goal.get("score") or 0)
    condition_items = post.conditions
    blockers = list(post.blockers)
    next_status = post.goal_status
    if next_status == GOAL_ABANDONED:
        next_status = GOAL_ACTIVE
    next_score = int(post.score_after)
    threshold = int(post.threshold)
    handshake_status = post.handshake_status
    if next_status == GOAL_WAITING:
        condition_status = "pending"
        event = "waiting_conditions"
    elif next_status == GOAL_SEALED:
        condition_status = (
            "pending"
            if any(str(item.get("status") or "") == "pending" for item in condition_items)
            else "satisfied"
        )
        event = "sealed"
        next_score = 100
        handshake_status = HANDSHAKE_SEALED
    elif next_status == GOAL_BLOCKED:
        condition_status = "failed" if condition_items else "none"
        event = "blocked"
        handshake_status = HANDSHAKE_BLOCKED
    elif next_status == GOAL_EXPIRED:
        condition_status = str(goal.get("condition_status") or "none")
        event = "expired"
    else:
        condition_status = "pending" if any(str(item.get("status") or "") == "pending" for item in condition_items) else "none"
        event = "progress"

    sealed_requires_player_quote = (
        next_status == GOAL_SEALED
        or handshake_status == HANDSHAKE_SEALED
        or post.agreement_action in {"create_achieved", "create_pending", "bind_existing"}
        or _post_agreement_formed(post)
    )
    if sealed_requires_player_quote and not _post_quote_supported_by_player_text(post, user_text):
        if "缺少玩家本轮握手原话证据" not in blockers:
            blockers.append("缺少玩家本轮握手原话证据")
        next_status = GOAL_WAITING if condition_items else GOAL_ACTIVE
        condition_status = "pending" if condition_items else "none"
        event = "waiting_conditions" if condition_items else "progress"
        handshake_status = HANDSHAKE_CONDITIONAL if condition_items else HANDSHAKE_NONE
        next_score = min(next_score, max(0, threshold - 1))
        post.agreement_action = "none"
        try:
            post.handshake_status = handshake_status
            post.goal_status = next_status
            if isinstance(post.raw, dict):
                post.raw["agreement_formed"] = False
                post.raw["agreement_action"] = "none"
                post.raw["performance_status"] = "none"
                post.raw["card_resolution"] = ""
        except Exception:
            pass

    if goal:
        revised = _revised_goal_text(goal, post) if refines_active else {"title": str(goal.get("title") or ""), "target_text": str(goal.get("target_text") or "")}
        title_changed = bool(refines_active and revised["title"] and revised["title"] != str(goal.get("title") or ""))
        target_changed = bool(refines_active and revised["target_text"] and revised["target_text"] != str(goal.get("target_text") or ""))
        delta_payload = {
            "event": event,
            "audit_source": "llm",
            "audit_status": post.audit_status,
            "audit_confidence": post.confidence,
            "goal_relation": post.goal_relation,
            "goal_revision": {
                "refines_active": bool(refines_active),
                "previous_title": str(goal.get("title") or ""),
                "previous_target_text": str(goal.get("target_text") or ""),
                "title": revised["title"],
                "target_text": revised["target_text"],
            },
            "stance": post.stance,
            "score_before": old_score,
            "score_after": next_score,
            "threshold": threshold,
            "handshake_status": handshake_status,
            "conditions": condition_items,
            "blockers": blockers,
            "explicit_consent": post.explicit_consent,
            "agreement_action": post.agreement_action,
            "trigger_quote": _post_trigger_quote(post),
            "public_hint": post.public_hint,
            "private_reason": post.private_reason,
            "audit": post.raw,
            "pre_audit": prepared.pre_audit.raw if prepared.pre_audit else {},
        }
        db.update_conversation_goal(
            int(goal["id"]),
            state=state,
            event_kind=event,
            event_summary=post.public_hint or f"{handshake_label(handshake_status)}：{goal.get('title') or post.title or post.target_text}",
            source_chat_turn_id=source_chat_turn_id,
            status=next_status,
            score=next_score,
            threshold=threshold,
            title=revised["title"] if title_changed else str(goal.get("title") or ""),
            target_text=revised["target_text"] if target_changed else str(goal.get("target_text") or ""),
            condition_status=condition_status,
            conditions_json=condition_items,
            blockers_json=blockers,
            related_issue_id=related_issue_id or int(goal.get("related_issue_id") or 0),
            last_delta_json=delta_payload,
            expires_turn=int(state.turn) + (3 if next_status == GOAL_WAITING else 1),
        )
        goal = db.get_conversation_goal(int(goal["id"])) or goal

    topic = str((goal or {}).get("title") or post.title or _compact(user_text, 80) or "本次奏对事项")
    conditions_text = "；".join(
        str(item.get("description") or "") + (f"（{item.get('evidence')}）" if item.get("evidence") else "")
        for item in condition_items
        if isinstance(item, dict) and item.get("description")
    )
    summary = post.public_hint or {
        "support": f"{character.name}已表示愿意支持/承办此事。",
        "oppose": f"{character.name}明确反对或不愿奉行此事。",
        "caution": f"{character.name}附条件赞成或有重大保留。",
        "neutral": f"{character.name}未给出明确承诺，只作一般分析。",
    }[post.stance]
    summary += f" 奏对目的：{handshake_label(handshake_status)}，{next_score}/{threshold}。"
    speech_context = ""
    if prepared is not None:
        speech_context = str(getattr(prepared, "behavior_context", "") or getattr(prepared, "prefix", "") or "")
    speech_profile = _dialogue_speech_profile(db, state, character, user_text, answer, context_text=speech_context)
    speech_summary = _speech_profile_summary(speech_profile)
    evidence = {
        "source": "llm_dialogue_audit",
        "audit_confidence": post.confidence,
        "public_hint": post.public_hint,
        "private_reason": post.private_reason,
        "speech_profile": speech_profile,
        "speech_profile_summary": speech_summary,
        "drivers": [
            {"kind": "审计", "text": (post.public_hint or post.private_reason or "LLM 奏对审计已落档")[:96]},
            {"kind": "人格关系", "text": "人物性格、关系网、记忆和履约状态已纳入奏对审计。"},
        ],
    }
    if speech_summary:
        evidence["drivers"].append({"kind": "人际话术", "text": speech_summary[:96]})
    risk_tags = _unique_strings([*blockers, *(speech_profile.get("risk_tags") or [])], limit=8)
    execution_hint = "目的仍在试探或推进中；未握手前不得当作执行背书。"
    if handshake_status == HANDSHAKE_SEALED:
        execution_hint = "奏对目的已握手；是否成为执行背书以履约账本 target_status 为准。"
    elif handshake_status == HANDSHAKE_CONDITIONAL:
        execution_hint = "目的附条件待证；条件未闭环前不得当作自愿配合。"
    elif handshake_status == HANDSHAKE_BLOCKED:
        execution_hint = "目的未握手；若强推，应按强旨/政治高压处理，而不是自愿配合。"
    speech_acts = set(str(item) for item in speech_profile.get("speech_acts") or [])
    if speech_acts.intersection({"misdirection", "selective_truth"}):
        execution_hint += " 本轮话术有保留，不可把口头顺从等同真实履约。"
    if "accusation" in speech_acts:
        execution_hint += " 涉政敌告状，后续推演应检查是否借旨清算。"
    if "shielding" in speech_acts:
        execution_hint += " 涉同党护短，后续推演应检查是否拖延转圜。"
    if "旧事牵引" in risk_tags:
        execution_hint += " 本轮承接旧事、密令或待证条件，后续应核查是否复命与履约闭环。"

    psychological = {
        "goal_id": int((goal or {}).get("id") or 0),
        "audit_source": "llm",
        "audit_status": post.audit_status,
        "audit_confidence": post.confidence,
        "action_kind": post.action_kind,
        "handshake_status": handshake_status,
        "score": next_score,
        "threshold": threshold,
        "verbal_only": handshake_status == HANDSHAKE_SEALED and not condition_items,
        "explicit_consent": post.explicit_consent,
        "core_topic": topic,
        "target_text": str((goal or {}).get("target_text") or post.target_text),
        "promise_type": _audit_text_field(post.raw, "promise_type", 80) or post.action_kind,
        "stakes": _audit_text_field(post.raw, "stakes", 160) or ";".join(blockers[:3]),
        "due_turns": int(post.raw.get("due_turns") or post.raw.get("due_turn") or (1 if post.agreement_action == "create_pending" else 0))
        if isinstance(post.raw, dict) else 0,
        "tasks": list(post.tasks or []) or [str(item.get("description") or "") for item in condition_items if isinstance(item, dict)],
        "blockers": blockers,
        "public_hint": post.public_hint,
        "private_reason": post.private_reason,
        "speech_profile": speech_profile,
        "speech_profile_summary": speech_summary,
        "audit": post.raw,
    }
    stance_id = db.record_minister_stance(
        state,
        character.name,
        topic=topic,
        stance=post.stance,
        confidence=max(1, min(5, round(post.confidence / 20) or 1)),
        summary=summary,
        conditions=conditions_text,
        related_issue_id=related_issue_id,
        source_chat_turn_id=source_chat_turn_id,
        user_message=user_text,
        minister_answer=answer,
        evidence=evidence,
        risk_tags=risk_tags,
        execution_hint=execution_hint,
        handshake_status=handshake_status,
        psychological_score=next_score,
        psychological=psychological,
        goal_id=int((goal or {}).get("id") or 0),
    )

    agreement_id = 0
    if goal and next_status == GOAL_SEALED and post.agreement_action in {"create_achieved", "create_pending", "bind_existing"}:
        if post.agreement_action == "bind_existing":
            agreement_id = int(goal.get("agreement_id") or 0)
        else:
            agreement_id = _create_agreement_for_goal(
                db,
                state,
                goal,
                stance_id=stance_id,
                summary=summary,
                conditions=conditions_text,
                audit=post.raw,
                agreement_action=post.agreement_action,
            )
        if not agreement_id:
            post.agreement_action = "none"
            try:
                post.handshake_status = HANDSHAKE_CONDITIONAL if condition_items else HANDSHAKE_NONE
                post.goal_status = GOAL_WAITING if condition_items else GOAL_ACTIVE
                handshake_status = post.handshake_status
                next_status = post.goal_status
                condition_status = "pending" if condition_items else "none"
                event = "waiting_conditions" if condition_items else "progress"
                next_score = min(next_score, max(0, threshold - 1))
                if goal:
                    db.update_conversation_goal(
                        int(goal["id"]),
                        state=state,
                        event_kind=event,
                        event_summary="未形成可核查履约账本：缺少明确待办条件。",
                        source_chat_turn_id=source_chat_turn_id,
                        status=post.goal_status,
                        score=next_score,
                        threshold=threshold,
                        condition_status=condition_status,
                        conditions_json=condition_items,
                        blockers_json=[*blockers, "缺少明确可核查待办条件"][:8],
                        expires_turn=int(state.turn) + (3 if condition_items else 1),
                    )
                    goal = db.get_conversation_goal(int(goal["id"])) or goal
                if isinstance(post.raw, dict):
                    post.raw["agreement_formed"] = False
                    post.raw["agreement_action"] = "none"
                    post.raw["performance_status"] = "none"
                    post.raw["card_resolution"] = ""
            except Exception:
                pass
    if agreement_id:
        db.update_minister_stance_agreement(stance_id, agreement_id)
        db.add_conversation_goal_event(
            state,
            int(goal["id"]),
            "agreement_created",
            status=GOAL_SEALED,
            score_delta=0,
            score_after=100,
            summary=f"已进入履约账本 #{agreement_id}",
            payload={"agreement_id": agreement_id, "audit": post.raw},
            source_chat_turn_id=source_chat_turn_id,
        )
    agreement_decision = _record_source_briefing_resolution(
        db,
        state,
        post,
        user_text=user_text,
        source_context=source_context,
        agreement_id=agreement_id,
        source_chat_turn_id=source_chat_turn_id,
    )

    # 任务系统整合：当对话握手 sealed 时创建任务
    quest_result = None
    if handshake_status == HANDSHAKE_SEALED and goal:
        try:
            # 御前与该大臣达成承办约定 → 落一道「召对交办」差使（统一入口 issue_assignment）。
            # 旧路径 create_quest_from_dialogue（NPC→玩家 RPG 任务）方向反了，已废弃。
            from ming_sim.assignment import issue_assignment
            from ming_sim.upgrade_schema import KV_CURRENT_DAY, kv_int

            topic = str(goal.get("target_text") or goal.get("title") or post.target_text or "奏对承办约定")
            tasks = [str(t) for t in (post.tasks or []) if str(t).strip()]
            assign_text = topic if not tasks else f"{topic}（承办：{'；'.join(tasks[:3])}）"
            assign_result = issue_assignment(
                db, state,
                kind="audience_commission",
                text=assign_text[:200],
                actor=str(character.name),
                day=kv_int(db, KV_CURRENT_DAY, 1),
                source_context={
                    "minister": str(character.name),
                    "agreement_id": agreement_id,
                    "action_kind": str(goal.get("action_kind") or post.action_kind),
                    "core_topic": str(goal.get("title") or post.title),
                },
                source_tag="audience_handshake",
            )
            quest_result = {
                "assignment_id": assign_result.get("id"),
                "assignment_kind": "audience_commission",
                "entry_label": assign_result.get("entry_label"),
                "assignee": assign_result.get("assignee"),
                "eta_day": assign_result.get("eta_day"),
            }
        except Exception as e:
            # 差使创建失败不应阻塞对话记录
            import logging
            logging.warning(f"Failed to create assignment from dialogue: {e}")

    result = {
        "audit_status": "recorded",
        "goal": goal,
        "stance_id": stance_id,
        "agreement_id": agreement_id,
        "proposed_directive": proposed_directive,
        "dialogue_consequences": dialogue_consequences,
        "agreement_decision": agreement_decision.__dict__,
        "event": event,
        "handshake_status": handshake_status,
        "score": next_score,
        "public_hint": post.public_hint,
        "audit_confidence": post.confidence,
    }

    # 将任务结果附加到返回值
    if quest_result:
        result["quest_created"] = quest_result

    return result


def review_conversation_goals(
    db: Any,
    state: GameState,
    *,
    decree_text: str = "",
    narrative: str = "",
    directives: Optional[List[Any]] = None,
    applied: Optional[Dict[str, object]] = None,
    phase: str = "preresolve",
    limit: int = 80,
    llm_config: Optional[LLMConfig] = None,
    agno_db: object = None,
    audit_client: object = None,
) -> List[Dict[str, object]]:
    context = db._agreement_review_context(
        decree_text=decree_text,
        narrative=narrative,
        directives=directives,
        applied=applied,
    )
    goals = db.list_conversation_goals(statuses=[GOAL_ACTIVE, GOAL_WAITING], limit=limit)
    reviewed: List[Dict[str, object]] = []
    for goal in goals:
        status = str(goal.get("status") or "")
        conditions = [dict(item) for item in (goal.get("conditions") or []) if isinstance(item, dict)]
        expires_turn = int(goal.get("expires_turn") or 0)
        if status == GOAL_ACTIVE and not conditions and phase == "postresolve":
            db.update_conversation_goal(
                int(goal["id"]),
                state=state,
                event_kind="expired",
                event_summary="本回合未继续推进，奏对目的过期。",
                status=GOAL_EXPIRED,
                last_delta_json={"phase": phase},
            )
            reviewed.append(db.get_conversation_goal(int(goal["id"])) or goal)
            continue
        if status == GOAL_WAITING and expires_turn and int(state.turn) > expires_turn:
            db.update_conversation_goal(
                int(goal["id"]),
                state=state,
                event_kind="expired",
                event_summary="附条件目的逾期未见闭环，已过期。",
                status=GOAL_EXPIRED,
                last_delta_json={"phase": phase},
            )
            reviewed.append(db.get_conversation_goal(int(goal["id"])) or goal)
            continue
        if not conditions or not context:
            continue
        audit = review_goal_conditions_audit(
            db,
            state,
            goal,
            context,
            phase=phase,
            llm_config=llm_config,
            agno_db=agno_db,
            audit_client=audit_client,
        )
        if not audit.valid:
            continue
        old_conditions = json.dumps(conditions, ensure_ascii=False, sort_keys=True)
        conditions = audit.conditions or conditions
        changed = json.dumps(conditions, ensure_ascii=False, sort_keys=True) != old_conditions
        statuses = [str(item.get("status") or "pending") for item in conditions]
        if audit.goal_status == GOAL_BLOCKED or any(item == "failed" for item in statuses):
            db.update_conversation_goal(
                int(goal["id"]),
                state=state,
                event_kind="condition_failed",
                event_summary=audit.public_hint or "目的条件被官方文本否定。",
                status=GOAL_BLOCKED,
                condition_status="failed",
                conditions_json=conditions,
                last_delta_json={"conditions": conditions, "phase": phase, "audit": audit.raw, "public_hint": audit.public_hint},
            )
            reviewed.append(db.get_conversation_goal(int(goal["id"])) or goal)
        elif audit.goal_status == GOAL_SEALED or (statuses and all(item == "done" for item in statuses)):
            db.update_conversation_goal(
                int(goal["id"]),
                state=state,
                event_kind="conditions_satisfied",
                event_summary=audit.public_hint or "目的条件已由诏书/草案/邸报证实，握手达成。",
                status=GOAL_SEALED,
                score=100,
                condition_status="satisfied",
                conditions_json=conditions,
                last_delta_json={"conditions": conditions, "phase": phase, "audit": audit.raw, "public_hint": audit.public_hint},
            )
            sealed = db.get_conversation_goal(int(goal["id"])) or goal
            stances = db.list_minister_stances(turn=state.turn, minister_name=str(goal.get("minister_name") or ""), limit=8)
            stance_id = 0
            for stance in stances:
                if int(stance.get("goal_id") or 0) == int(goal["id"]):
                    stance_id = int(stance.get("id") or 0)
                    break
            agreement_id = _create_agreement_for_goal(
                db,
                state,
                sealed,
                stance_id=stance_id,
                summary=audit.public_hint or f"条件审计闭环：{sealed.get('title') or sealed.get('target_text')}",
                conditions="；".join(str(item.get("evidence") or item.get("description") or "") for item in conditions),
                audit=audit.raw,
                agreement_action=audit.agreement_action,
            )
            if stance_id:
                db.update_minister_stance_agreement(stance_id, agreement_id)
            reviewed.append(db.get_conversation_goal(int(goal["id"])) or sealed)
        elif changed:
            db.update_conversation_goal(
                int(goal["id"]),
                state=state,
                event_kind="condition_reviewed",
                event_summary=audit.public_hint or "目的条件审计更新，尚未全部闭环。",
                conditions_json=conditions,
                last_delta_json={"conditions": conditions, "phase": phase, "audit": audit.raw, "public_hint": audit.public_hint},
            )
            reviewed.append(db.get_conversation_goal(int(goal["id"])) or goal)
    return reviewed
