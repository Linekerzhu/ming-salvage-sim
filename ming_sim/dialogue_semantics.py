"""Unified semantic gate for NPC dialogue world actions.

This module is the single public semantic surface for dialogue-driven state
changes.  It deliberately wraps the older dialogue_audit entry points so callers
do not need to know which specialized audit prompt currently backs a decision.
Regex extraction may still provide candidates and display cleanup elsewhere,
but it must not be treated as permission to mutate game state.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ming_sim.dialogue_audit import CONFIDENCE_FLOOR
from ming_sim.models import Character, GameState, LLMConfig


ACTION_TYPES = {
    "none",
    "bargain_attitude",
    "directive_fallback",
    "directive_followup",
    "directive_pressure",
    "lore_intake",
    "unknown_mention",
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
ROUTE_INTENTS = {"none", "summon", "confirm_pending", "reject_pending"}
PHASES = {"none", "propose", "confirm", "reject"}
DECISION_TYPES = {
    "none",
    "route",
    "action",
    "pending",
    "recovery",
    "tool",
    "post",
    "mention",
    "lore",
}
RECRUITMENT_KINDS = {"eunuch", "exam", "recommend"}
RECOVERABLE_ACTIONS = {
    "recruitment",
    "mediation",
    "castration",
    "eunuch_care",
    "eunuch_hard_service",
    "bao_leverage",
}


def _compact(value: object, limit: int = 240) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _confidence(value: object) -> int:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        parsed = 0.0
    if 0 < parsed <= 1:
        parsed *= 100
    return max(0, min(100, int(round(parsed))))


def _enabled_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def _disabled_env(name: str) -> bool:
    return _enabled_env(name)


def _accepted_profiles(review: Dict[str, Any], accepted_names: List[str]) -> Dict[str, Dict[str, Any]]:
    raw_profiles = review.get("accepted_profiles") or review.get("profiles") or {}
    profiles: Dict[str, Dict[str, Any]] = {}

    def add_profile(name: str, profile: object) -> None:
        clean_name = _compact(name, 80)
        if clean_name not in accepted_names or not isinstance(profile, dict):
            return
        item: Dict[str, Any] = {}
        for key in ("office", "office_type", "faction", "summary", "source"):
            value = _compact(profile.get(key), 360 if key == "summary" else 80)
            if value:
                item[key] = value
        aliases_raw = profile.get("aliases")
        if isinstance(aliases_raw, list):
            aliases = [_compact(alias, 80) for alias in aliases_raw]
            aliases = [alias for alias in aliases if alias and alias != clean_name]
            if aliases:
                item["aliases"] = aliases[:8]
        if item:
            profiles[clean_name] = item

    if isinstance(raw_profiles, dict):
        for name, profile in raw_profiles.items():
            add_profile(str(name or ""), profile)
    elif isinstance(raw_profiles, list):
        for profile in raw_profiles:
            if not isinstance(profile, dict):
                continue
            add_profile(str(profile.get("name") or profile.get("target") or ""), profile)
    return profiles


@dataclass
class PendingDialogueAction:
    type: str = "none"
    target: str = ""
    actor: str = ""
    kind: str = ""
    mode: str = ""
    note: str = ""
    source_quote: str = ""
    proposal_evidence: str = ""
    created_turn: int = 0
    expires_turn: int = 0
    payload: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: Optional[Dict[str, Any]], *, current_turn: int = 0) -> "PendingDialogueAction":
        if not isinstance(data, dict) or not data:
            return cls()
        created = int(data.get("created_turn") or data.get("turn") or current_turn or 0)
        expires = int(data.get("expires_turn") or data.get("turn") or created or current_turn or 0)
        return cls(
            type=_compact(data.get("type"), 40) or "none",
            target=_compact(data.get("target"), 80),
            actor=_compact(data.get("actor"), 80),
            kind=_compact(data.get("kind"), 40),
            mode=_compact(data.get("mode"), 40),
            note=_compact(data.get("note") or data.get("need") or data.get("condition") or data.get("scheme_text"), 600),
            source_quote=_compact(data.get("source_quote") or data.get("trigger_quote"), 180),
            proposal_evidence=_compact(data.get("proposal_evidence"), 360),
            created_turn=created,
            expires_turn=expires,
            payload=dict(data),
        )

    def to_mapping(self) -> Dict[str, Any]:
        out = dict(self.payload)
        out["type"] = self.type
        for key in ("target", "actor", "kind", "mode", "note", "source_quote", "proposal_evidence"):
            value = getattr(self, key)
            if value:
                out[key] = value
        if self.source_quote and not out.get("trigger_quote"):
            out["trigger_quote"] = self.source_quote
        if self.created_turn:
            out["created_turn"] = self.created_turn
        if self.expires_turn:
            out["expires_turn"] = self.expires_turn
        return out


@dataclass
class SemanticDecision:
    decision_type: str = "none"
    action_type: str = "none"
    phase: str = "none"
    target: str = ""
    actor: str = ""
    kind: str = ""
    mode: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    confidence: int = 0
    trigger_quote: str = ""
    private_reason: str = ""
    public_hint: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def allow(self) -> bool:
        return self.decision_type != "none" and self.confidence >= CONFIDENCE_FLOOR and bool(self.trigger_quote)

    @classmethod
    def none(cls, reason: str = "", *, raw: Optional[Dict[str, Any]] = None) -> "SemanticDecision":
        return cls(private_reason=_compact(reason, 520), raw=dict(raw or {}))

    @classmethod
    def from_action_review(
        cls,
        review: Optional[Dict[str, Any]],
        *,
        decision_type: str = "action",
        default_action_type: str = "",
    ) -> "SemanticDecision":
        if not isinstance(review, dict) or not review.get("allow"):
            return cls.none(str((review or {}).get("private_reason") or ""), raw=review if isinstance(review, dict) else {})
        action_type = _compact(review.get("action_type") or review.get("type") or default_action_type, 40)
        phase = _compact(review.get("phase"), 40) or "none"
        confidence = _confidence(review.get("confidence"))
        trigger_quote = _compact(review.get("trigger_quote"), 180)
        if action_type not in ACTION_TYPES or phase not in PHASES or confidence < CONFIDENCE_FLOOR or not trigger_quote:
            return cls.none(str(review.get("private_reason") or "语义审计证据不足。"), raw=review)
        payload = dict(review.get("payload") or {}) if isinstance(review.get("payload"), dict) else {}
        for key in ("faction",):
            value = _compact(review.get(key), 80)
            if value:
                payload[key] = value
        for key in ("character_status_changes", "condition_changes", "punishment_changes"):
            value = review.get(key)
            if isinstance(value, list):
                payload[key] = value
        return cls(
            decision_type=decision_type if decision_type in DECISION_TYPES else "action",
            action_type=action_type,
            phase=phase,
            target=_compact(review.get("target"), 80),
            actor=_compact(review.get("actor"), 80),
            kind=_compact(review.get("kind"), 40),
            mode=_compact(review.get("mode"), 40),
            payload=payload,
            confidence=confidence,
            trigger_quote=trigger_quote,
            private_reason=_compact(review.get("private_reason") or review.get("reason"), 520),
            public_hint=_compact(review.get("public_hint"), 180),
            raw=dict(review),
        )

    @classmethod
    def from_recruitment_review(
        cls,
        review: Optional[Dict[str, Any]],
        *,
        decision_type: str = "tool",
    ) -> "SemanticDecision":
        if not isinstance(review, dict) or not review.get("allow"):
            return cls.none(str((review or {}).get("private_reason") or ""), raw=review if isinstance(review, dict) else {})
        kind = _compact(review.get("kind"), 40)
        phase = _compact(review.get("phase"), 40) or "none"
        confidence = _confidence(review.get("confidence"))
        trigger_quote = _compact(review.get("trigger_quote"), 180)
        if kind not in RECRUITMENT_KINDS or phase not in PHASES or confidence < CONFIDENCE_FLOOR or not trigger_quote:
            return cls.none(str(review.get("private_reason") or "用人语义审计证据不足。"), raw=review)
        return cls(
            decision_type=decision_type,
            action_type="recruitment",
            phase=phase,
            kind=kind,
            payload={},
            confidence=confidence,
            trigger_quote=trigger_quote,
            private_reason=_compact(review.get("private_reason") or review.get("reason"), 520),
            public_hint=_compact(review.get("public_hint"), 180),
            raw=dict(review),
        )

    @classmethod
    def from_route_review(cls, review: Optional[Dict[str, Any]]) -> "SemanticDecision":
        if not isinstance(review, dict) or not review.get("allow"):
            return cls.none(str((review or {}).get("private_reason") or ""), raw=review if isinstance(review, dict) else {})
        intent = _compact(review.get("intent"), 40)
        confidence = _confidence(review.get("confidence"))
        trigger_quote = _compact(review.get("trigger_quote"), 180)
        if intent not in ROUTE_INTENTS or intent == "none" or confidence < CONFIDENCE_FLOOR or not trigger_quote:
            return cls.none(str(review.get("private_reason") or "对白路由证据不足。"), raw=review)
        phase = "reject" if intent == "reject_pending" else "confirm"
        return cls(
            decision_type="route",
            action_type=intent,
            phase=phase,
            target=_compact(review.get("target_name"), 80),
            payload={
                "target_reference": _compact(review.get("target_reference"), 80),
                "pending_action_type": _compact(review.get("action_type"), 40),
            },
            confidence=confidence,
            trigger_quote=trigger_quote,
            private_reason=_compact(review.get("private_reason") or review.get("reason"), 520),
            public_hint=_compact(review.get("public_hint"), 180),
            raw=dict(review),
        )

    @classmethod
    def from_recovery_review(cls, review: Optional[Dict[str, Any]]) -> "SemanticDecision":
        if not isinstance(review, dict) or not review.get("allow"):
            return cls.none(str((review or {}).get("private_reason") or ""), raw=review if isinstance(review, dict) else {})
        action_type = _compact(review.get("action_type") or review.get("type"), 40)
        confidence = _confidence(review.get("confidence"))
        trigger_quote = _compact(review.get("trigger_quote"), 180)
        if action_type not in RECOVERABLE_ACTIONS or confidence < CONFIDENCE_FLOOR or not trigger_quote:
            return cls.none(str(review.get("private_reason") or "待办恢复证据不足。"), raw=review)
        proposal = _compact(review.get("proposal_evidence"), 360)
        payload = {"proposal_evidence": proposal} if proposal else {}
        return cls(
            decision_type="recovery",
            action_type=action_type,
            phase="confirm",
            target=_compact(review.get("target"), 80),
            actor=_compact(review.get("actor"), 80),
            kind=_compact(review.get("kind"), 40),
            mode=_compact(review.get("mode"), 40),
            payload=payload,
            confidence=confidence,
            trigger_quote=trigger_quote,
            private_reason=_compact(review.get("private_reason") or review.get("reason"), 520),
            public_hint=_compact(review.get("public_hint"), 180),
            raw=dict(review),
        )

    @classmethod
    def from_post_review(
        cls,
        review: Optional[Dict[str, Any]],
        *,
        action_type: str,
        required_any: Optional[List[str]] = None,
        required_all: Optional[List[str]] = None,
    ) -> "SemanticDecision":
        if not isinstance(review, dict) or not review.get("allow"):
            return cls.none(str((review or {}).get("private_reason") or ""), raw=review if isinstance(review, dict) else {})
        confidence = _confidence(review.get("confidence"))
        trigger_quote = _compact(review.get("trigger_quote"), 180)
        if confidence < CONFIDENCE_FLOOR or not trigger_quote:
            return cls.none(str(review.get("private_reason") or "对话后审计证据不足。"), raw=review)
        if required_any and not any(_compact(review.get(key), 240) for key in required_any):
            return cls.none(str(review.get("private_reason") or "对话后审计缺少必要字段。"), raw=review)
        if required_all and not all(_compact(review.get(key), 240) for key in required_all):
            return cls.none(str(review.get("private_reason") or "对话后审计缺少必要证据。"), raw=review)
        return cls(
            decision_type="post",
            action_type=_compact(action_type, 60),
            phase="confirm",
            kind=_compact(review.get("kind") or review.get("attitude"), 60),
            payload=dict(review),
            confidence=confidence,
            trigger_quote=trigger_quote,
            private_reason=_compact(review.get("private_reason") or review.get("reason"), 520),
            public_hint=_compact(review.get("public_hint"), 180),
            raw=dict(review),
        )

    @classmethod
    def from_lore_review(cls, review: Optional[Dict[str, Any]]) -> "SemanticDecision":
        if not isinstance(review, dict) or not review.get("allow"):
            return cls.none(str((review or {}).get("private_reason") or ""), raw=review if isinstance(review, dict) else {})
        target_names = [_compact(name, 80) for name in (review.get("target_names") or review.get("targets") or [])]
        target_names = [name for name in target_names if name]
        confidence = _confidence(review.get("confidence"))
        trigger_quote = _compact(review.get("trigger_quote"), 180)
        if not target_names or confidence < CONFIDENCE_FLOOR or not trigger_quote:
            return cls.none(str(review.get("private_reason") or "净身旧档入档审计证据不足。"), raw=review)
        return cls(
            decision_type="lore",
            action_type="lore_intake",
            phase="confirm",
            target=target_names[0],
            payload={"target_names": target_names},
            confidence=confidence,
            trigger_quote=trigger_quote,
            private_reason=_compact(review.get("private_reason") or review.get("reason"), 520),
            public_hint=_compact(review.get("public_hint"), 180),
            raw=dict(review),
        )

    @classmethod
    def from_unknown_mention_review(cls, review: Optional[Dict[str, Any]]) -> "SemanticDecision":
        if not isinstance(review, dict) or not review.get("allow"):
            return cls.none(str((review or {}).get("private_reason") or ""), raw=review if isinstance(review, dict) else {})
        accepted_names = [_compact(name, 80) for name in (review.get("accepted_names") or review.get("names") or [])]
        accepted_names = [name for name in accepted_names if name]
        confidence = _confidence(review.get("confidence"))
        trigger_quote = _compact(review.get("trigger_quote"), 180)
        if not accepted_names or confidence < CONFIDENCE_FLOOR or not trigger_quote:
            return cls.none(str(review.get("private_reason") or "未知人物线索入池审计证据不足。"), raw=review)
        payload: Dict[str, Any] = {"accepted_names": accepted_names}
        profiles = _accepted_profiles(review, accepted_names)
        if profiles:
            payload["accepted_profiles"] = profiles
        return cls(
            decision_type="mention",
            action_type="unknown_mention",
            phase="confirm",
            target=accepted_names[0],
            payload=payload,
            confidence=confidence,
            trigger_quote=trigger_quote,
            private_reason=_compact(review.get("private_reason") or review.get("reason"), 520),
            public_hint=_compact(review.get("public_hint"), 180),
            raw=dict(review),
        )

    def to_action(self) -> Dict[str, Any]:
        if not self.allow:
            return {}
        out = dict(self.payload)
        if self.action_type in {"custody", "punishment", "condition_update"}:
            out["type"] = "dialogue_consequence"
            out["action_type"] = self.action_type
        else:
            out["type"] = self.action_type
        for key in ("target", "actor", "kind", "mode"):
            value = getattr(self, key)
            if value:
                out[key] = value
        if self.trigger_quote:
            out["trigger_quote"] = self.trigger_quote
        if self.public_hint and not out.get("need"):
            out["need"] = self.public_hint
        if self.private_reason:
            out["semantic_reason"] = self.private_reason
        return out

    def to_route_review(self) -> Dict[str, Any]:
        if not self.allow or self.decision_type != "route":
            return {}
        return {
            "allow": True,
            "intent": self.action_type,
            "target_name": self.target,
            "target_reference": self.payload.get("target_reference", ""),
            "action_type": self.payload.get("pending_action_type", ""),
            "trigger_quote": self.trigger_quote,
            "public_hint": self.public_hint,
            "private_reason": self.private_reason,
            "confidence": self.confidence,
            "raw": self.raw,
        }

    def to_review(self) -> Dict[str, Any]:
        if not self.allow:
            return {}
        data = {
            "allow": True,
            "phase": self.phase,
            "action_type": self.action_type,
            "target": self.target,
            "actor": self.actor,
            "kind": self.kind,
            "mode": self.mode,
            "trigger_quote": self.trigger_quote,
            "public_hint": self.public_hint,
            "private_reason": self.private_reason,
            "confidence": self.confidence,
            "payload": self.payload,
            "raw": self.raw,
        }
        for key in ("character_status_changes", "condition_changes", "punishment_changes"):
            if key in self.payload:
                data[key] = self.payload[key]
        for key in ("proposal_evidence", "faction"):
            if key in self.payload:
                data[key] = self.payload[key]
        return data


class DialogueSemanticEngine:
    """LLM-first semantic authority for dialogue actions."""

    def __init__(
        self,
        db: Any,
        state: GameState,
        *,
        llm_config: Optional[LLMConfig] = None,
        agno_db: object = None,
        audit_client: object = None,
    ) -> None:
        self.db = db
        self.state = state
        self.llm_config = llm_config
        self.agno_db = agno_db
        self.audit_client = audit_client

    def _has_llm_or_fake(self) -> bool:
        if self.audit_client is not None:
            return True
        try:
            return bool(str(getattr(self.llm_config, "api_key", "") or "").strip())
        except Exception:
            return False

    def _call_injected_audit(self, phase: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if self.audit_client is None:
            return None
        if callable(self.audit_client):
            data = self.audit_client(phase, payload)
        else:
            method = getattr(self.audit_client, phase, None)
            if method is None:
                method = getattr(self.audit_client, f"{phase}_dialogue_audit", None)
            if method is None:
                return None
            data = method(payload)
        return dict(data) if isinstance(data, dict) else None

    def _route_available(self) -> bool:
        if self.audit_client is not None:
            return True
        return self._has_llm_or_fake() and not _disabled_env("MING_SIM_DISABLE_DIALOGUE_ROUTE_LLM_AUDIT")

    def _action_available(self) -> bool:
        if self.audit_client is not None:
            return True
        return self._has_llm_or_fake() and not _disabled_env("MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT")

    def _mention_available(self) -> bool:
        if self.audit_client is not None:
            return True
        return self._has_llm_or_fake() and not _disabled_env("MING_SIM_DISABLE_DIALOGUE_MENTION_LLM_AUDIT")

    def _lore_available(self) -> bool:
        if self.audit_client is not None:
            return True
        return self._has_llm_or_fake() and not _disabled_env("MING_SIM_DISABLE_DIALOGUE_LORE_LLM_AUDIT")

    def evaluate_user_message(
        self,
        character: Character,
        user_text: str,
        *,
        pending_action: Optional[Dict[str, Any]] = None,
        route_context: Optional[Dict[str, Any]] = None,
        recent_answers: Optional[List[str]] = None,
    ) -> SemanticDecision:
        route_context = route_context if isinstance(route_context, dict) else {}
        if bool(route_context.get("semantic_route_enabled", True)):
            route = self.evaluate_route(character, user_text, pending_action=pending_action, route_context=route_context)
            if route.allow:
                return route
        pending = PendingDialogueAction.from_mapping(pending_action, current_turn=int(getattr(self.state, "turn", 0) or 0))
        if pending.type != "none":
            decision = self.gate_tool_action(character, user_text, pending.to_mapping(), phase="confirm", pending_action=pending.to_mapping())
            if decision.allow:
                decision.decision_type = "pending"
                return decision
            return SemanticDecision.none("待确认动作未获语义确认。", raw=decision.raw)
        recovery = self.evaluate_pending_recovery(character, user_text, recent_answers or [])
        if recovery.allow:
            return recovery
        return self.evaluate_action_probe(character, user_text)

    def evaluate_pre_dialogue(
        self,
        character: Character,
        user_text: str,
        *,
        active_goal: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Coordinate the goal/stance pre-audit through the unified engine."""

        from ming_sim.dialogue_audit import (
            _attach_behavior_context,
            _audit_failure,
            _context_payload,
            _normalize_pre,
            pre_dialogue_audit,
        )

        if not self._has_llm_or_fake():
            return _audit_failure("未配置 LLM，奏对预审不落档。")
        if self.audit_client is not None:
            payload = _context_payload(self.db, self.state, character, active_goal=active_goal)
            payload["user_text"] = user_text
            _attach_behavior_context(payload, character, text=user_text)
            try:
                review = self._call_injected_audit("pre", payload)
            except Exception as exc:
                return _audit_failure(str(exc))
            if not isinstance(review, dict):
                return _audit_failure("注入奏对预审未返回结构化结果。")
            return _normalize_pre(review)
        try:
            return pre_dialogue_audit(
                self.db,
                self.state,
                character,
                user_text,
                active_goal=active_goal,
                llm_config=self.llm_config,
                agno_db=self.agno_db,
                audit_client=None,
            )
        except Exception as exc:
            return _audit_failure(str(exc))

    def evaluate_post_dialogue(
        self,
        character: Character,
        user_text: str,
        answer: str,
        *,
        active_goal: Optional[Dict[str, Any]] = None,
        pre_audit: object = None,
        source_context: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Coordinate goal progress, stance, agreement, and post-chat effects."""

        from ming_sim.dialogue_audit import (
            PreDialogueAudit,
            _attach_behavior_context,
            _context_payload,
            _normalize_post,
            _post_failure,
            post_dialogue_audit,
        )

        if not self._has_llm_or_fake():
            return _post_failure("未配置 LLM，奏对后审不落档。")
        if self.audit_client is not None:
            payload = _context_payload(self.db, self.state, character, active_goal=active_goal)
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
                review = self._call_injected_audit("post", payload)
            except Exception as exc:
                return _post_failure(str(exc))
            if not isinstance(review, dict):
                return _post_failure("注入奏对后审未返回结构化结果。")
            return _normalize_post(review, existing_threshold=existing_threshold)
        try:
            return post_dialogue_audit(
                self.db,
                self.state,
                character,
                user_text,
                answer,
                active_goal=active_goal,
                pre_audit=pre_audit,
                source_context=source_context,
                llm_config=self.llm_config,
                agno_db=self.agno_db,
                audit_client=None,
            )
        except Exception as exc:
            return _post_failure(str(exc))

    def evaluate_route(
        self,
        character: Character,
        user_text: str,
        *,
        pending_action: Optional[Dict[str, Any]] = None,
        route_context: Optional[Dict[str, Any]] = None,
    ) -> SemanticDecision:
        if not self._route_available():
            return SemanticDecision.none("对白路由语义审计不可用。")
        if not pending_action and not bool((route_context or {}).get("can_route_summon")) and not (route_context or {}).get("tool_requested_summon_target"):
            return SemanticDecision.none("无可路由对象。")
        if self.audit_client is not None:
            try:
                review = self._call_injected_audit(
                    "dialogue_route_intent",
                    {
                        "user_text": user_text,
                        "pending_action": pending_action if isinstance(pending_action, dict) else {},
                        "route_context": route_context if isinstance(route_context, dict) else {},
                    },
                )
            except Exception as exc:
                return SemanticDecision.none(str(exc))
            return SemanticDecision.from_route_review(review)
        try:
            from ming_sim.dialogue_audit import dialogue_route_intent_audit

            review = dialogue_route_intent_audit(
                self.db,
                self.state,
                character,
                user_text,
                pending_action=pending_action if isinstance(pending_action, dict) else None,
                route_context=route_context if isinstance(route_context, dict) else {},
                llm_config=self.llm_config,
                agno_db=self.agno_db,
                audit_client=self.audit_client,
            )
        except Exception as exc:
            return SemanticDecision.none(str(exc))
        return SemanticDecision.from_route_review(review)

    def evaluate_action_probe(self, character: Character, user_text: str) -> SemanticDecision:
        if not self._action_available():
            return SemanticDecision.none("对白动作语义审计不可用。")
        probe = {
            "type": "semantic_probe",
            "phase": "propose",
            "trigger_quote": _compact(user_text, 140),
        }
        if self.audit_client is not None:
            try:
                review = self._call_injected_audit(
                    "dialogue_action_intent",
                    {
                        "user_text": user_text,
                        "tool_action": probe,
                        "pending_action": {},
                    },
                )
            except Exception as exc:
                return SemanticDecision.none(str(exc))
            return SemanticDecision.from_action_review(review, decision_type="action")
        try:
            from ming_sim.dialogue_audit import dialogue_action_intent_audit

            review = dialogue_action_intent_audit(
                self.db,
                self.state,
                character,
                user_text,
                probe,
                pending_action=None,
                llm_config=self.llm_config,
                agno_db=self.agno_db,
                audit_client=self.audit_client,
            )
        except Exception as exc:
            return SemanticDecision.none(str(exc))
        return SemanticDecision.from_action_review(review, decision_type="action")

    def evaluate_pending_recovery(
        self,
        character: Character,
        user_text: str,
        recent_answers: List[str],
    ) -> SemanticDecision:
        if not self._action_available() or not recent_answers:
            return SemanticDecision.none("待办恢复审计不可用。")
        if self.audit_client is not None:
            try:
                review = self._call_injected_audit(
                    "dialogue_pending_recovery",
                    {
                        "user_text": user_text,
                        "recent_answers": list(recent_answers or [])[:4],
                        "recent_proposals": list(recent_answers or [])[:4],
                    },
                )
            except Exception as exc:
                return SemanticDecision.none(str(exc))
            return SemanticDecision.from_recovery_review(review)
        try:
            from ming_sim.dialogue_audit import dialogue_pending_recovery_audit

            review = dialogue_pending_recovery_audit(
                self.db,
                self.state,
                character,
                user_text,
                recent_answers,
                llm_config=self.llm_config,
                agno_db=self.agno_db,
                audit_client=self.audit_client,
            )
        except Exception as exc:
            return SemanticDecision.none(str(exc))
        return SemanticDecision.from_recovery_review(review)

    def gate_tool_action(
        self,
        character: Character,
        user_text: str,
        action: Dict[str, Any],
        *,
        phase: str = "",
        pending_action: Optional[Dict[str, Any]] = None,
    ) -> SemanticDecision:
        if not self._action_available():
            return SemanticDecision.none("对白动作语义审计不可用。")
        normalized = dict(action or {})
        if phase:
            normalized["phase"] = phase
        action_type = _compact(normalized.get("type"), 40)
        if action_type == "recruitment" and _compact(normalized.get("type"), 40) != "semantic_probe":
            if self.audit_client is not None:
                try:
                    review = self._call_injected_audit(
                        "recruitment_intent",
                        {
                            "user_text": user_text,
                            "tool_action": normalized,
                            "pending_action": pending_action if isinstance(pending_action, dict) else {},
                        },
                    )
                except Exception as exc:
                    return SemanticDecision.none(str(exc))
                return SemanticDecision.from_recruitment_review(review, decision_type="tool")
            try:
                from ming_sim.dialogue_audit import recruitment_intent_audit

                review = recruitment_intent_audit(
                    self.db,
                    self.state,
                    character,
                    user_text,
                    normalized,
                    pending_action=pending_action if isinstance(pending_action, dict) else None,
                    llm_config=self.llm_config,
                    agno_db=self.agno_db,
                    audit_client=self.audit_client,
                )
            except Exception as exc:
                return SemanticDecision.none(str(exc))
            return SemanticDecision.from_recruitment_review(review, decision_type="tool")
        if self.audit_client is not None:
            try:
                review = self._call_injected_audit(
                    "dialogue_action_intent",
                    {
                        "user_text": user_text,
                        "tool_action": normalized,
                        "pending_action": pending_action if isinstance(pending_action, dict) else {},
                    },
                )
            except Exception as exc:
                return SemanticDecision.none(str(exc))
            return SemanticDecision.from_action_review(review, decision_type="tool", default_action_type=action_type)
        try:
            from ming_sim.dialogue_audit import dialogue_action_intent_audit

            review = dialogue_action_intent_audit(
                self.db,
                self.state,
                character,
                user_text,
                normalized,
                pending_action=pending_action if isinstance(pending_action, dict) else None,
                llm_config=self.llm_config,
                agno_db=self.agno_db,
                audit_client=self.audit_client,
            )
        except Exception as exc:
            return SemanticDecision.none(str(exc))
        return SemanticDecision.from_action_review(review, decision_type="tool", default_action_type=action_type)

    def evaluate_unknown_mentions(
        self,
        character: Character,
        text: str,
        *,
        candidate_names: List[str],
        purpose: str = "cache_candidate",
    ) -> List[str]:
        decision = self.evaluate_unknown_mentions_decision(
            character,
            text,
            candidate_names=candidate_names,
            purpose=purpose,
        )
        if not decision.allow:
            return []
        candidates = [_compact(name, 80) for name in (candidate_names or []) if _compact(name, 80)]
        return [name for name in decision.payload.get("accepted_names", []) if name in candidates]

    def evaluate_unknown_mentions_decision(
        self,
        character: Character,
        text: str,
        *,
        candidate_names: List[str],
        purpose: str = "cache_candidate",
    ) -> SemanticDecision:
        if not self._mention_available() or not candidate_names:
            return SemanticDecision.none("未知人物审计不可用。")
        candidates = [_compact(name, 80) for name in (candidate_names or []) if _compact(name, 80)]
        if not candidates:
            return SemanticDecision.none("无未知人物候选。")
        if self.audit_client is not None:
            try:
                review = self._call_injected_audit(
                    "dialogue_unknown_mention_intake",
                    {
                        "user_text": text,
                        "text": text,
                        "candidate_names": candidates,
                        "purpose": purpose,
                    },
                )
            except Exception:
                return SemanticDecision.none("未知人物注入审计异常。")
            return SemanticDecision.from_unknown_mention_review(review)
        try:
            from ming_sim.dialogue_audit import dialogue_unknown_mention_intake_audit

            review = dialogue_unknown_mention_intake_audit(
                self.db,
                self.state,
                character,
                text,
                candidate_names=candidates,
                purpose=purpose,
                llm_config=self.llm_config,
                agno_db=self.agno_db,
                audit_client=self.audit_client,
            )
        except Exception:
            return SemanticDecision.none("未知人物审计异常。")
        return SemanticDecision.from_unknown_mention_review(review)

    def evaluate_lore_intake(
        self,
        character: Character,
        text: str,
        *,
        candidate_names: List[str],
        pending_target: str = "",
        source_role: str = "",
    ) -> List[str]:
        if not self._lore_available() or not candidate_names:
            return []
        candidates = [_compact(name, 80) for name in (candidate_names or []) if _compact(name, 80)]
        if not candidates:
            return []
        if self.audit_client is not None:
            try:
                review = self._call_injected_audit(
                    "dialogue_eunuch_lore_intake",
                    {
                        "user_text": text,
                        "text": text,
                        "candidate_names": candidates,
                        "pending_target": pending_target,
                        "source_role": source_role,
                    },
                )
            except Exception:
                return []
            decision = SemanticDecision.from_lore_review(review)
            if not decision.allow:
                return []
            return [name for name in decision.payload.get("target_names", []) if name in candidates]
        try:
            from ming_sim.dialogue_audit import dialogue_eunuch_lore_intake_audit

            review = dialogue_eunuch_lore_intake_audit(
                self.db,
                self.state,
                character,
                text,
                candidate_names=candidates,
                pending_target=pending_target,
                source_role=source_role,
                llm_config=self.llm_config,
                agno_db=self.agno_db,
                audit_client=self.audit_client,
            )
        except Exception:
            return []
        decision = SemanticDecision.from_lore_review(review)
        if not decision.allow:
            return []
        return [name for name in decision.payload.get("target_names", []) if name in candidates]

    def evaluate_post_chat(
        self,
        character: Character,
        user_text: str,
        answer: str,
        *,
        kind: str = "directive_fallback",
        context: Optional[Dict[str, Any]] = None,
    ) -> SemanticDecision:
        if not self._action_available():
            return SemanticDecision.none("对话后语义审计不可用。")
        phase_by_kind = {
            "bargain_attitude": "dialogue_bargain_attitude",
            "directive_fallback": "dialogue_directive_fallback",
            "directive_followup": "dialogue_directive_followup",
            "directive_pressure": "dialogue_directive_pressure",
        }
        phase = phase_by_kind.get(kind)
        if not phase:
            return SemanticDecision.none("未知对话后语义审计。")
        if self.audit_client is not None:
            payload: Dict[str, Any] = {
                "user_text": user_text,
                "answer": answer,
                "npc_answer": answer,
            }
            if kind == "bargain_attitude":
                payload["bargain_context"] = context if isinstance(context, dict) else {}
            if kind in {"directive_pressure", "directive_followup"}:
                payload["directive_context"] = context if isinstance(context, dict) else {}
            try:
                review = self._call_injected_audit(phase, payload)
            except Exception as exc:
                return SemanticDecision.none(str(exc))
            return self._post_decision_from_review(kind, review)
        try:
            if kind == "bargain_attitude":
                from ming_sim.dialogue_audit import dialogue_bargain_attitude_audit

                review = dialogue_bargain_attitude_audit(
                    self.db,
                    self.state,
                    character,
                    user_text,
                    answer,
                    context if isinstance(context, dict) else {},
                    llm_config=self.llm_config,
                    agno_db=self.agno_db,
                    audit_client=None,
                )
            elif kind == "directive_pressure":
                from ming_sim.dialogue_audit import dialogue_directive_pressure_audit

                review = dialogue_directive_pressure_audit(
                    self.db,
                    self.state,
                    character,
                    user_text,
                    answer,
                    context if isinstance(context, dict) else {},
                    llm_config=self.llm_config,
                    agno_db=self.agno_db,
                    audit_client=None,
                )
            elif kind == "directive_followup":
                from ming_sim.dialogue_audit import dialogue_directive_followup_audit

                review = dialogue_directive_followup_audit(
                    self.db,
                    self.state,
                    character,
                    user_text,
                    answer,
                    context if isinstance(context, dict) else {},
                    llm_config=self.llm_config,
                    agno_db=self.agno_db,
                    audit_client=None,
                )
            else:
                from ming_sim.dialogue_audit import dialogue_directive_fallback_audit

                review = dialogue_directive_fallback_audit(
                    self.db,
                    self.state,
                    character,
                    user_text,
                    answer,
                    llm_config=self.llm_config,
                    agno_db=self.agno_db,
                    audit_client=None,
                )
        except Exception as exc:
            return SemanticDecision.none(str(exc))
        return self._post_decision_from_review(kind, review)

    def _post_decision_from_review(self, kind: str, review: Optional[Dict[str, Any]]) -> SemanticDecision:
        if kind == "directive_fallback":
            return SemanticDecision.from_post_review(
                review,
                action_type="directive_fallback",
                required_any=["subject", "directive_text"],
            )
        if kind == "bargain_attitude":
            decision = SemanticDecision.from_post_review(review, action_type="bargain_attitude", required_all=["attitude"])
            if decision.allow and decision.kind not in {"accept", "press", "refuse"}:
                return SemanticDecision.none("御前交易态度无效。", raw=decision.raw)
            return decision
        if kind == "directive_pressure":
            decision = SemanticDecision.from_post_review(
                review,
                action_type="directive_pressure",
                required_all=["kind", "answer_evidence"],
            )
            if decision.allow and decision.kind not in {"pressed", "needs_support", "evasive"}:
                return SemanticDecision.none("旨意压力类型无效。", raw=decision.raw)
            return decision
        if kind == "directive_followup":
            decision = SemanticDecision.from_post_review(
                review,
                action_type="directive_followup",
                required_all=["kind", "answer_evidence"],
            )
            if decision.allow and decision.kind not in {"rewarded", "accounted", "followup_evasive", "next_step", "reviewed"}:
                return SemanticDecision.none("复命处置类型无效。", raw=decision.raw)
            return decision
        return SemanticDecision.none("未知对话后语义审计。", raw=review if isinstance(review, dict) else {})


class DialogueActionExecutor:
    """Small adapter that gives the semantic layer a uniform execute hook."""

    def __init__(self, execute_action: Callable[[Dict[str, Any], int], Dict[str, Any]]) -> None:
        self._execute_action = execute_action

    def execute(self, decision: SemanticDecision, chat_turn_id: int = 0) -> Dict[str, Any]:
        action = decision.to_action()
        if not action:
            return {}
        return self._execute_action(action, int(chat_turn_id or 0))
