"""Probe the LLM-audited conversation goal layer without real LLM calls."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Dict

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ming_sim.content import GameContent
from ming_sim.context import bind_content as bind_context
from ming_sim.bureaucracy import directive_execution_assessments
from ming_sim.db import GameDB
from ming_sim.dialogue_goals import (
    GOAL_BLOCKED,
    GOAL_SEALED,
    GOAL_WAITING,
    prepare_dialogue_context,
    record_dialogue_effects,
    review_conversation_goals,
)
from ming_sim.issues import _castration_consent_recorded


def assert_true(value: object, message: str) -> None:
    if not value:
        raise AssertionError(message)


def latest_goal(db: GameDB, name: str) -> dict:
    goals = db.list_conversation_goals(minister_name=name, limit=5)
    assert_true(goals, f"{name} should have at least one conversation goal")
    return goals[0]


class FakeDialogueAudit:
    def pre(self, payload: Dict[str, object]) -> Dict[str, object]:
        text = str(payload.get("user_text") or "")
        active = payload.get("active_goal") if isinstance(payload.get("active_goal"), dict) else {}
        if "低置信" in text:
            return {"goal_decision": "new", "action_kind": "policy", "title": "低置信事务", "confidence": 35}
        if "作罢" in text or "不谈" in text:
            return {
                "goal_decision": "abandon",
                "action_kind": active.get("action_kind") or "general",
                "title": active.get("title") or "放弃当前目的",
                "target_text": active.get("target_text") or "",
                "confidence": 95,
                "public_hint": "当前奏对目的已作罢。",
            }
        if "净身" in text:
            return {
                "goal_decision": "new",
                "action_kind": "castration",
                "title": "劝其自愿净身入内廷",
                "target_text": "本人明确自愿净身入内廷",
                "confidence": 96,
                "npc_guidance": "必须确认是否自愿，不得把畏惧当同意。",
                "public_hint": "识别为身份转换目的。",
            }
        if "吏部" in text and "做官" in text:
            return {
                "goal_decision": "new",
                "action_kind": "personnel",
                "title": "劝其接受吏部任职",
                "target_text": "本人接受吏部任职安排",
                "confidence": 95,
                "npc_guidance": "围绕任职边界、明旨和名分回应；司礼监只作背景。",
                "public_hint": "识别为任职目的。",
            }
        if "密查" in text or "暗查" in text:
            return {
                "goal_decision": "new" if not active else "continue",
                "action_kind": "secret_order",
                "title": "劝其密查旧案",
                "target_text": "本人同意密查并只向御前回报",
                "confidence": 92,
                "npc_guidance": "若有条件，列明授权、人手和保密边界。",
                "public_hint": "识别为密办目的。",
            }
        if "清丈" in text:
            return {
                "goal_decision": "new",
                "action_kind": "policy",
                "title": "劝其承办清丈新政",
                "target_text": "本人同意承办清丈新政",
                "confidence": 90,
                "npc_guidance": "重点谈章程、明旨、会同名分。",
                "public_hint": "识别为政策协办目的。",
            }
        return {"goal_decision": "none", "action_kind": "general", "confidence": 92}

    def post(self, payload: Dict[str, object]) -> Dict[str, object]:
        text = str(payload.get("user_text") or "")
        answer = str(payload.get("npc_answer") or "")
        active = payload.get("active_goal") if isinstance(payload.get("active_goal"), dict) else {}
        pre = payload.get("pre_audit") if isinstance(payload.get("pre_audit"), dict) else {}
        action = str(pre.get("action_kind") or active.get("action_kind") or "general")
        title = str(pre.get("title") or active.get("title") or "本次奏对目的")
        target = str(pre.get("target_text") or active.get("target_text") or title)
        if action == "general" and not active:
            return {
                "goal_decision": "none",
                "action_kind": "general",
                "stance": "neutral",
                "handshake_status": "none",
                "goal_status": "active",
                "score_after": 0,
                "threshold": 70,
                "agreement_action": "none",
                "confidence": 92,
            }
        if "低置信" in text:
            return {"goal_decision": "new", "action_kind": "policy", "title": title, "confidence": 30}
        if "作罢" in text or "不谈" in text:
            return {
                "goal_decision": "abandon",
                "action_kind": action,
                "title": title,
                "target_text": target,
                "stance": "neutral",
                "handshake_status": "none",
                "goal_status": "abandoned",
                "score_after": int(active.get("score") or 0),
                "threshold": int(active.get("threshold") or 70),
                "agreement_action": "none",
                "confidence": 95,
                "public_hint": "已放弃当前奏对目的。",
            }
        if action == "castration":
            explicit = "愿入内廷" in answer or "愿为陛下内臣" in answer
            return {
                "goal_decision": "new" if not active else "continue",
                "action_kind": "castration",
                "title": title,
                "target_text": target,
                "stance": "support" if explicit else "caution",
                "handshake_status": "sealed",
                "goal_status": "sealed",
                "score_delta": 100,
                "score_after": 100,
                "threshold": 90,
                "conditions": [],
                "blockers": [] if explicit else ["只有遵旨畏惧，未见自愿"],
                "explicit_consent": explicit,
                "agreement_action": "create_achieved",
                "confidence": 94,
                "public_hint": "身份转换须以明确自愿为准。",
                "private_reason": answer,
            }
        if "但须" in answer:
            return {
                "goal_decision": "new" if not active else "continue",
                "action_kind": action,
                "title": title,
                "target_text": target,
                "stance": "caution",
                "handshake_status": "conditional",
                "goal_status": "waiting_conditions",
                "score_delta": 55,
                "score_after": 72,
                "threshold": 82,
                "conditions": [
                    {"description": "明旨授权", "status": "pending", "evidence": "NPC提出须明旨授权"},
                    {"description": "添派可靠人手", "status": "pending", "evidence": "NPC提出须添派人手"},
                ],
                "agreement_action": "none",
                "confidence": 92,
                "public_hint": "对方愿谈，但条件尚待证。",
            }
        return {
            "goal_decision": "new" if not active else "continue",
            "action_kind": action,
            "title": title,
            "target_text": target,
            "stance": "support",
            "handshake_status": "sealed",
            "goal_status": "sealed",
            "score_delta": 100,
            "score_after": 100,
            "threshold": 75 if action == "personnel" else 82,
            "conditions": [],
            "blockers": [],
            "explicit_consent": action not in {"castration", "emancipation"},
            "agreement_action": "create_achieved" if action == "personnel" else "create_pending",
            "confidence": 93,
            "public_hint": "奏对目的已握手。",
        }

    def condition(self, payload: Dict[str, object]) -> Dict[str, object]:
        context = str(payload.get("evidence_context") or "")
        goal = payload.get("goal") if isinstance(payload.get("goal"), dict) else {}
        conditions = []
        for item in goal.get("conditions") or []:
            if not isinstance(item, dict):
                continue
            desc = str(item.get("description") or "")
            done = ("明旨" in desc and "明旨授权" in context) or ("人手" in desc and "添派可靠人手" in context)
            conditions.append({
                "description": desc,
                "status": "done" if done else "pending",
                "evidence": "诏书满足该条件" if done else "",
            })
        sealed = bool(conditions) and all(item["status"] == "done" for item in conditions)
        return {
            "confidence": 94,
            "goal_status": "sealed" if sealed else "waiting_conditions",
            "conditions": conditions,
            "score_after": 100 if sealed else int(goal.get("score") or 0),
            "public_hint": "条件已由诏书满足。" if sealed else "条件仍未完全满足。",
            "private_reason": context[:180],
        }


def run_probe() -> None:
    content = GameContent.load()
    bind_context(content)
    fake = FakeDialogueAudit()
    with tempfile.TemporaryDirectory(prefix="ming-dialogue-goal-") as tmp:
        db = GameDB(str(Path(tmp) / "probe.db"), content=content)
        db.seed_static_data()
        state = db.load_state()

        yang = content.characters["杨嗣昌"]
        wang = content.characters["王承恩"]
        sun = content.characters["孙承宗"]
        huang = content.characters["黄道周"]
        liu = content.characters["刘鸿训"]
        xu = content.characters["徐光启"]
        qian = content.characters["钱龙锡"]
        wang_zaijin = content.characters["王在晋"]

        class TargetOnlyAudit(FakeDialogueAudit):
            def pre(self, payload: Dict[str, object]) -> Dict[str, object]:
                return {
                    "goal_decision": "new",
                    "action_kind": "policy",
                    "target_text": "劝其考虑清丈新政",
                    "confidence": 91,
                    "public_hint": "识别为政策目的。",
                }

        target_only_prep = prepare_dialogue_context(
            db,
            state,
            yang,
            "朕要你先看看清丈之议。",
            persistent=True,
            audit_client=TargetOnlyAudit(),
        )
        assert_true(target_only_prep.detection.has_goal, "target_text-only pre audit should still count as a goal")
        assert_true("劝其考虑清丈新政" in target_only_prep.prefix, "target_text-only goal should guide the NPC")

        db.append_chat_message(yang.name, state.turn, "user", "前情：朕问过你清丈边界。")
        db.append_chat_message(yang.name, state.turn, "minister", "前情：臣说须明旨和会同名分。")

        class RecentDialogueAudit(FakeDialogueAudit):
            seen_recent = False

            def pre(self, payload: Dict[str, object]) -> Dict[str, object]:
                recent = payload.get("recent_dialogue") if isinstance(payload.get("recent_dialogue"), list) else []
                self.seen_recent = any("清丈边界" in str(item.get("content") or "") for item in recent if isinstance(item, dict))
                return {"goal_decision": "none", "action_kind": "general", "confidence": 92}

        recent_audit = RecentDialogueAudit()
        prepare_dialogue_context(
            db,
            state,
            yang,
            "照方才说的边界办。",
            persistent=True,
            audit_client=recent_audit,
        )
        assert_true(recent_audit.seen_recent, "dialogue audit payload should include recent dialogue")

        class ActivePersonnelAudit(FakeDialogueAudit):
            def pre(self, payload: Dict[str, object]) -> Dict[str, object]:
                return {
                    "goal_decision": "new",
                    "goal_relation": "distinct_goal",
                    "action_kind": "personnel",
                    "title": "劝其接受兵部任职",
                    "target_text": "本人接受兵部任职安排",
                    "confidence": 94,
                    "public_hint": "识别为兵部任职目的。",
                }

            def post(self, payload: Dict[str, object]) -> Dict[str, object]:
                return {
                    "goal_decision": "new",
                    "goal_relation": "distinct_goal",
                    "action_kind": "personnel",
                    "title": "劝其接受兵部任职",
                    "target_text": "本人接受兵部任职安排",
                    "stance": "neutral",
                    "handshake_status": "none",
                    "goal_status": "active",
                    "score_delta": 18,
                    "score_after": 18,
                    "threshold": 78,
                    "agreement_action": "none",
                    "confidence": 94,
                    "public_hint": "对方仍在听取任职安排。",
                }

        prep = prepare_dialogue_context(
            db,
            state,
            huang,
            "朕有意让卿入兵部任事。",
            persistent=True,
            audit_client=ActivePersonnelAudit(),
        )
        record_dialogue_effects(
            db,
            state,
            huang,
            "朕有意让卿入兵部任事。",
            "臣尚须知道职掌名分，方敢议此。",
            prep,
            audit_client=ActivePersonnelAudit(),
        )
        original_personnel_goal = latest_goal(db, huang.name)

        class MistakenNewPersonnelRefineAudit(FakeDialogueAudit):
            def pre(self, payload: Dict[str, object]) -> Dict[str, object]:
                return {
                    "goal_decision": "new",
                    "action_kind": "personnel",
                    "title": "劝其接受兵部尚书",
                    "target_text": "本人接受兵部尚书任命并要求明旨名分",
                    "confidence": 93,
                    "public_hint": "同一任职目的细化为兵部尚书。",
                }

            def post(self, payload: Dict[str, object]) -> Dict[str, object]:
                return {
                    "goal_decision": "new",
                    "action_kind": "personnel",
                    "title": "劝其接受兵部尚书",
                    "target_text": "本人接受兵部尚书任命并要求明旨名分",
                    "stance": "caution",
                    "handshake_status": "conditional",
                    "goal_status": "waiting_conditions",
                    "score_delta": 40,
                    "score_after": 58,
                    "threshold": 78,
                    "conditions": [{"description": "明旨授兵部尚书名分", "status": "pending", "evidence": "NPC要求名分"}],
                    "agreement_action": "none",
                    "confidence": 93,
                    "public_hint": "任职目的已细化，尚待名分条件。",
                }

        refine_prep = prepare_dialogue_context(
            db,
            state,
            huang,
            "那就明旨授你兵部尚书，卿如何才肯接？",
            persistent=True,
            audit_client=MistakenNewPersonnelRefineAudit(),
        )
        assert_true("同一奏对目的" in refine_prep.prefix, "same-kind mistaken new intent should be presented as a refinement")
        record_dialogue_effects(
            db,
            state,
            huang,
            "那就明旨授你兵部尚书，卿如何才肯接？",
            "若有明旨授臣兵部尚书名分，臣方敢勉力任事。",
            refine_prep,
            audit_client=MistakenNewPersonnelRefineAudit(),
        )
        refined_personnel_goal = latest_goal(db, huang.name)
        huang_goals = db.list_conversation_goals(minister_name=huang.name, limit=10)
        assert_true(refined_personnel_goal["id"] == original_personnel_goal["id"], "same personnel intent should revise the active goal, not create another")
        assert_true(len(huang_goals) == 1, "same personnel intent should not duplicate goals")
        assert_true("兵部尚书" in str(refined_personnel_goal.get("title") or ""), "refined goal title should be written back")
        assert_true(refined_personnel_goal["status"] == GOAL_WAITING, "refined personnel goal should keep negotiated condition state")

        class PolicySeedAudit(FakeDialogueAudit):
            def pre(self, payload: Dict[str, object]) -> Dict[str, object]:
                return {
                    "goal_decision": "new",
                    "goal_relation": "distinct_goal",
                    "action_kind": "policy",
                    "title": "劝其承办清丈",
                    "target_text": "本人同意承办清丈",
                    "confidence": 92,
                }

            def post(self, payload: Dict[str, object]) -> Dict[str, object]:
                return {
                    "goal_decision": "new",
                    "goal_relation": "distinct_goal",
                    "action_kind": "policy",
                    "title": "劝其承办清丈",
                    "target_text": "本人同意承办清丈",
                    "stance": "neutral",
                    "handshake_status": "none",
                    "goal_status": "active",
                    "score_delta": 12,
                    "score_after": 12,
                    "threshold": 82,
                    "agreement_action": "none",
                    "confidence": 92,
                }

        policy_seed_prep = prepare_dialogue_context(db, state, liu, "卿先替朕看清丈章程。", persistent=True, audit_client=PolicySeedAudit())
        record_dialogue_effects(db, state, liu, "卿先替朕看清丈章程。", "臣先看章程。", policy_seed_prep, audit_client=PolicySeedAudit())

        class DistinctPolicyAudit(PolicySeedAudit):
            def pre(self, payload: Dict[str, object]) -> Dict[str, object]:
                return {
                    "goal_decision": "new",
                    "goal_relation": "distinct_goal",
                    "action_kind": "policy",
                    "title": "劝其承办盐法整饬",
                    "target_text": "本人同意承办盐法整饬",
                    "confidence": 92,
                }

            def post(self, payload: Dict[str, object]) -> Dict[str, object]:
                return {
                    "goal_decision": "new",
                    "goal_relation": "distinct_goal",
                    "action_kind": "policy",
                    "title": "劝其承办盐法整饬",
                    "target_text": "本人同意承办盐法整饬",
                    "stance": "neutral",
                    "handshake_status": "none",
                    "goal_status": "active",
                    "score_delta": 8,
                    "score_after": 8,
                    "threshold": 82,
                    "agreement_action": "none",
                    "confidence": 92,
                }

        distinct_prep = prepare_dialogue_context(db, state, liu, "另有一事，盐法整饬也要你承办。", persistent=True, audit_client=DistinctPolicyAudit())
        record_dialogue_effects(db, state, liu, "另有一事，盐法整饬也要你承办。", "臣还要另议盐法。", distinct_prep, audit_client=DistinctPolicyAudit())
        liu_goals = db.list_conversation_goals(minister_name=liu.name, limit=10)
        assert_true(len(liu_goals) == 2, "explicit distinct same-kind goal should create a second goal")
        assert_true(liu_goals[0]["status"] == "active" and "盐法" in str(liu_goals[0].get("title") or ""), "latest distinct goal should be active")
        assert_true(liu_goals[1]["status"] == "abandoned", "old active goal should be abandoned when distinct new goal starts")

        policy_seed_prep = prepare_dialogue_context(db, state, xu, "卿先替朕看清丈章程。", persistent=True, audit_client=PolicySeedAudit())
        record_dialogue_effects(db, state, xu, "卿先替朕看清丈章程。", "臣先看章程。", policy_seed_prep, audit_client=PolicySeedAudit())

        class ImplicitDistinctPolicyAudit(PolicySeedAudit):
            def pre(self, payload: Dict[str, object]) -> Dict[str, object]:
                return {
                    "goal_decision": "new",
                    "action_kind": "policy",
                    "title": "劝其承办盐法整饬",
                    "target_text": "本人同意承办盐法整饬",
                    "confidence": 92,
                }

            def post(self, payload: Dict[str, object]) -> Dict[str, object]:
                return {
                    "goal_decision": "new",
                    "action_kind": "policy",
                    "title": "劝其承办盐法整饬",
                    "target_text": "本人同意承办盐法整饬",
                    "stance": "neutral",
                    "handshake_status": "none",
                    "goal_status": "active",
                    "score_delta": 8,
                    "score_after": 8,
                    "threshold": 82,
                    "agreement_action": "none",
                    "confidence": 92,
                }

        implicit_distinct_prep = prepare_dialogue_context(db, state, xu, "另有盐法整饬，也要你承办。", persistent=True, audit_client=ImplicitDistinctPolicyAudit())
        record_dialogue_effects(db, state, xu, "另有盐法整饬，也要你承办。", "臣还要另议盐法。", implicit_distinct_prep, audit_client=ImplicitDistinctPolicyAudit())
        xu_goals = db.list_conversation_goals(minister_name=xu.name, limit=10)
        assert_true(len(xu_goals) == 2, "same-kind new goal without explicit relation should not merge when target text is unrelated")
        assert_true(xu_goals[0]["status"] == "active" and "盐法" in str(xu_goals[0].get("title") or ""), "implicit distinct policy should become the active goal")
        assert_true(xu_goals[1]["status"] == "abandoned" and "清丈" in str(xu_goals[1].get("title") or ""), "old unrelated policy should be abandoned")

        class ActivePersonnelForCrossAudit(FakeDialogueAudit):
            def pre(self, payload: Dict[str, object]) -> Dict[str, object]:
                return {
                    "goal_decision": "new",
                    "action_kind": "personnel",
                    "title": "劝其接受礼部任职",
                    "target_text": "本人接受礼部任职安排",
                    "confidence": 94,
                }

            def post(self, payload: Dict[str, object]) -> Dict[str, object]:
                return {
                    "goal_decision": "new",
                    "action_kind": "personnel",
                    "title": "劝其接受礼部任职",
                    "target_text": "本人接受礼部任职安排",
                    "stance": "neutral",
                    "handshake_status": "none",
                    "goal_status": "active",
                    "score_delta": 15,
                    "score_after": 15,
                    "threshold": 76,
                    "agreement_action": "none",
                    "confidence": 94,
                }

        cross_seed_prep = prepare_dialogue_context(db, state, qian, "朕有意令卿入礼部任事。", persistent=True, audit_client=ActivePersonnelForCrossAudit())
        record_dialogue_effects(db, state, qian, "朕有意令卿入礼部任事。", "臣且听圣裁。", cross_seed_prep, audit_client=ActivePersonnelForCrossAudit())

        class CrossActionRefineAudit(FakeDialogueAudit):
            def pre(self, payload: Dict[str, object]) -> Dict[str, object]:
                return {
                    "goal_decision": "continue",
                    "goal_relation": "refine_goal",
                    "action_kind": "castration",
                    "title": "劝其自愿净身入内廷",
                    "target_text": "本人明确自愿净身入内廷",
                    "confidence": 95,
                }

            def post(self, payload: Dict[str, object]) -> Dict[str, object]:
                return {
                    "goal_decision": "continue",
                    "goal_relation": "refine_goal",
                    "action_kind": "castration",
                    "title": "劝其自愿净身入内廷",
                    "target_text": "本人明确自愿净身入内廷",
                    "stance": "support",
                    "handshake_status": "sealed",
                    "goal_status": "sealed",
                    "score_delta": 100,
                    "score_after": 100,
                    "threshold": 90,
                    "conditions": [],
                    "explicit_consent": True,
                    "agreement_action": "create_achieved",
                    "confidence": 95,
                    "public_hint": "身份转换为另一目的，不能并入原任职目的。",
                    "private_reason": "NPC原文明确自愿入内廷。",
                }

        cross_prep = prepare_dialogue_context(db, state, qian, "方才任职作罢，若令卿净身入内廷，卿可愿？", persistent=True, audit_client=CrossActionRefineAudit())
        record_dialogue_effects(db, state, qian, "方才任职作罢，若令卿净身入内廷，卿可愿？", "臣愿自净入内廷，为陛下驱策。", cross_prep, audit_client=CrossActionRefineAudit())
        qian_goals = db.list_conversation_goals(minister_name=qian.name, limit=10)
        assert_true(len(qian_goals) == 2, "cross-action refine audit should split into a new goal")
        assert_true(qian_goals[0]["action_kind"] == "castration" and qian_goals[0]["status"] == GOAL_SEALED, "new identity goal should not inherit personnel action_kind")
        assert_true(qian_goals[1]["action_kind"] == "personnel" and qian_goals[1]["status"] == "abandoned", "old personnel goal should be abandoned on cross-action switch")

        class PendingSealAudit(FakeDialogueAudit):
            def pre(self, payload: Dict[str, object]) -> Dict[str, object]:
                return {
                    "goal_decision": "new",
                    "action_kind": "court_commitment",
                    "title": "劝其为辽饷作保",
                    "target_text": "本人同意为辽饷拨银作保",
                    "confidence": 94,
                }

            def post(self, payload: Dict[str, object]) -> Dict[str, object]:
                return {
                    "goal_decision": "new",
                    "action_kind": "court_commitment",
                    "title": "劝其为辽饷作保",
                    "target_text": "本人同意为辽饷拨银作保",
                    "stance": "caution",
                    "handshake_status": "sealed",
                    "goal_status": "active",
                    "score_delta": 80,
                    "score_after": 90,
                    "threshold": 80,
                    "conditions": [{"description": "先见户部拨银明旨", "status": "pending", "evidence": "NPC要求先见明旨"}],
                    "agreement_action": "create_pending",
                    "confidence": 94,
                    "public_hint": "LLM误报 sealed，但条件仍待证。",
                }

        pending_seal_prep = prepare_dialogue_context(db, state, wang_zaijin, "卿可愿替辽饷作保？", persistent=True, audit_client=PendingSealAudit())
        pending_result = record_dialogue_effects(
            db,
            state,
            wang_zaijin,
            "卿可愿替辽饷作保？",
            "臣愿为陛下分忧，但须先见户部拨银明旨。",
            pending_seal_prep,
            audit_client=PendingSealAudit(),
        )
        pending_goal = latest_goal(db, wang_zaijin.name)
        assert_true(pending_result["event"] == "waiting_conditions", "sealed audit with pending conditions must be downgraded to waiting")
        assert_true(pending_result["handshake_status"] == "conditional", "pending conditions must keep handshake conditional")
        assert_true(pending_goal["status"] == GOAL_WAITING, "pending-condition goal must not be sealed")
        assert_true(int(pending_goal["agreement_id"] or 0) == 0 and not pending_result.get("agreement_id"), "waiting goal must not create agreement")

        text = "你是否愿意去吏部做官？司礼监那边也会照会，不会掣肘。"
        prep = prepare_dialogue_context(db, state, yang, text, persistent=True, audit_client=fake)
        assert_true(prep.detection.action_kind == "personnel", f"吏部做官被误判为 {prep.detection.action_kind}")
        result = record_dialogue_effects(
            db,
            state,
            yang,
            text,
            "臣愿领旨，若陛下明授吏部职掌，臣当奉行。",
            prep,
            audit_client=fake,
        )
        goal = latest_goal(db, yang.name)
        assert_true(goal["action_kind"] == "personnel", "personnel goal should stay personnel")
        assert_true(goal["status"] == GOAL_SEALED, f"personnel goal should seal, got {goal['status']}")
        assert_true(int(goal["agreement_id"] or 0) > 0, "sealed personnel goal should bind agreement")
        agreements = db.list_negotiation_agreements(minister_name=yang.name, action_kind="personnel")
        assert_true(agreements and agreements[0]["target_status"] == "achieved", "personnel agreement should be achieved")
        assert_true(result["handshake_status"] == "sealed", "record result should report sealed")

        class GoalSplitAudit(FakeDialogueAudit):
            open_has_sealed = False
            completed_has_sealed = False

            def pre(self, payload: Dict[str, object]) -> Dict[str, object]:
                recent = payload.get("recent_goals") if isinstance(payload.get("recent_goals"), list) else []
                completed = payload.get("recent_completed_goals") if isinstance(payload.get("recent_completed_goals"), list) else []
                self.open_has_sealed = any(str(item.get("status") or "") == "sealed" for item in recent if isinstance(item, dict))
                self.completed_has_sealed = any(str(item.get("status") or "") == "sealed" for item in completed if isinstance(item, dict))
                return {"goal_decision": "none", "action_kind": "general", "confidence": 92}

        split_audit = GoalSplitAudit()
        split_prep = prepare_dialogue_context(
            db,
            state,
            yang,
            "朕只是问问先前任职一事的后续。",
            persistent=True,
            audit_client=split_audit,
        )
        assert_true(not split_prep.prefix, "sealed goals should not inject a negotiation prefix")
        assert_true(not split_audit.open_has_sealed, "sealed goals should not occupy recent open-goal context")
        assert_true(split_audit.completed_has_sealed, "sealed goals should remain as small completed-goal background")

        before = db.capture_chat_rollback_snapshot()
        casual = prepare_dialogue_context(db, state, yang, "卿今日身体如何？", persistent=True, audit_client=fake)
        casual_result = record_dialogue_effects(db, state, yang, "卿今日身体如何？", "臣尚可。", casual, audit_client=fake)
        after = db.capture_chat_rollback_snapshot()
        assert_true(casual_result.get("event") == "none", "ordinary question should not create a goal")
        assert_true(after["conversation_goals"] == before["conversation_goals"], "ordinary question should not mutate goals")

        low_text = "低置信：朕随口说说一件含混事务。"
        before = db.capture_chat_rollback_snapshot()
        prep = prepare_dialogue_context(db, state, yang, low_text, persistent=True, audit_client=fake)
        low = record_dialogue_effects(db, state, yang, low_text, "臣未明圣意。", prep, audit_client=fake)
        after = db.capture_chat_rollback_snapshot()
        assert_true(low.get("audit_status") == "not_recorded", "low confidence audit should not record")
        assert_true(after["conversation_goals"] == before["conversation_goals"], "low confidence should not mutate goals")
        assert_true(after["minister_stances"] == before["minister_stances"], "low confidence should not mutate stances")

        secret_text = "朕要你暗查厂卫旧案，取证后只许密奏。"
        prep = prepare_dialogue_context(db, state, yang, secret_text, persistent=True, audit_client=fake)
        record_dialogue_effects(
            db,
            state,
            yang,
            secret_text,
            "臣可密办，但须明旨授权锦衣卫，并添派可靠人手，否则风声一泄便会反噬。",
            prep,
            audit_client=fake,
        )
        goal = latest_goal(db, yang.name)
        assert_true(goal["action_kind"] == "secret_order", "secret goal should be secret_order")
        assert_true(goal["status"] == GOAL_WAITING, f"conditional secret goal should wait, got {goal['status']}")
        assert_true(int(goal["agreement_id"] or 0) == 0, "waiting goal must not create agreement early")

        reviewed = review_conversation_goals(
            db,
            state,
            decree_text="准明旨授权锦衣卫密查此案，添派可靠人手，所得证据只许密奏御前。",
            phase="preresolve",
            audit_client=fake,
        )
        goal = latest_goal(db, yang.name)
        assert_true(reviewed, "condition review should update the waiting goal")
        assert_true(goal["status"] == GOAL_SEALED, f"reviewed goal should seal, got {goal['status']}")
        assert_true(int(goal["agreement_id"] or 0) > 0, "reviewed sealed goal should create agreement")

        policy_text = "朕命卿承办清丈新政，先拿出章程。"
        prep = prepare_dialogue_context(db, state, yang, policy_text, persistent=True, audit_client=fake)
        record_dialogue_effects(
            db,
            state,
            yang,
            policy_text,
            "臣愿领此事，先试行章程。",
            prep,
            audit_client=fake,
        )
        policy_agreements = db.list_negotiation_agreements(minister_name=yang.name, action_kind="policy")
        assert_true(policy_agreements, "policy sealed should create a pending agreement")
        assert_true(policy_agreements[0]["target_status"] == "pending_conditions", "policy agreement should not be achieved before fulfillment")
        assessments = directive_execution_assessments(
            state,
            db,
            [{"id": 999, "actor": yang.name, "text": policy_text}],
        )
        assert_true(assessments, "directive execution assessment should run")
        assert_true(
            int(assessments[0].get("stance_score") or 0) < 60,
            f"pending policy agreement must not become execution endorsement: {assessments[0]}",
        )

        db.record_minister_stance(
            state,
            yang.name,
            topic="司礼监照会吏部任职",
            stance="support",
            summary="司礼监会照会，但本意是吏部做官，并非净身入宫。",
            handshake_status="sealed",
            psychological_score=100,
            psychological={"action_kind": "personnel", "handshake_status": "sealed"},
        )
        assert_true(
            not _castration_consent_recorded(db, state, yang.name),
            "structured personnel stance mentioning 司礼监 must not count as castration consent",
        )

        class NoCommitmentAudit(FakeDialogueAudit):
            def pre(self, payload: Dict[str, object]) -> Dict[str, object]:
                return {
                    "goal_decision": "new",
                    "action_kind": "policy",
                    "title": "威逼承办新政",
                    "target_text": "劝其承办新政",
                    "confidence": 94,
                }

            def post(self, payload: Dict[str, object]) -> Dict[str, object]:
                return {
                    "goal_decision": "new",
                    "action_kind": "policy",
                    "title": "威逼承办新政",
                    "target_text": "劝其承办新政",
                    "stance": "caution",
                    "handshake_status": "blocked",
                    "goal_status": "blocked",
                    "score_after": 20,
                    "threshold": 82,
                    "conditions": [],
                    "agreement_action": "none",
                    "confidence": 94,
                    "public_hint": "对方未给出明确承诺。",
                    "private_reason": "NPC只是惧怕圣怒，没有正向承诺证据。",
                }

        no_commitment_prep = prepare_dialogue_context(
            db,
            state,
            sun,
            "若再推诿，朕必重处。此事你办不办？",
            persistent=True,
            audit_client=NoCommitmentAudit(),
        )
        no_commitment_result = record_dialogue_effects(
            db,
            state,
            sun,
            "若再推诿，朕必重处。此事你办不办？",
            "臣不敢抗旨，只是此事实难遽行。",
            no_commitment_prep,
            audit_client=NoCommitmentAudit(),
        )
        no_commitment_goal = latest_goal(db, sun.name)
        assert_true(no_commitment_result.get("event") == "blocked", "no-commitment audit should be blocked")
        assert_true(no_commitment_goal["status"] == GOAL_BLOCKED, "coerced hesitation must not become handshake success")
        assert_true(int(no_commitment_goal["agreement_id"] or 0) == 0, "blocked goal must not create agreement")

        castration_text = "若令卿净身入宫，转入司礼监为内臣，卿是否自愿？"
        prep = prepare_dialogue_context(db, state, wang, castration_text, persistent=True, audit_client=fake)
        record_dialogue_effects(db, state, wang, castration_text, "奴才遵旨，不敢不从。", prep, audit_client=fake)
        goal = latest_goal(db, wang.name)
        assert_true(goal["status"] == GOAL_BLOCKED, "fearful compliance must not seal castration")
        assert_true(int(goal["agreement_id"] or 0) == 0, "blocked castration must not create agreement")

        prep = prepare_dialogue_context(db, state, wang, castration_text, persistent=True, audit_client=fake)
        record_dialogue_effects(
            db,
            state,
            wang,
            castration_text,
            "奴才愿入内廷，愿为陛下内臣，谨受此身。",
            prep,
            audit_client=fake,
        )
        goal = latest_goal(db, wang.name)
        assert_true(goal["action_kind"] == "castration", "explicit castration goal should be castration")
        assert_true(goal["status"] == GOAL_SEALED, "explicit castration should seal")

        class IdentityConditionAudit(FakeDialogueAudit):
            def condition(self, payload: Dict[str, object]) -> Dict[str, object]:
                goal = payload.get("goal") if isinstance(payload.get("goal"), dict) else {}
                return {
                    "confidence": 95,
                    "goal_status": "sealed",
                    "conditions": [
                        {
                            "description": str((goal.get("conditions") or [{}])[0].get("description") or "安置家小"),
                            "status": "done",
                            "evidence": "诏书安置家小",
                        }
                    ],
                    "score_after": 100,
                    "public_hint": "身份转换条件已满足。",
                    "private_reason": "诏书已兑现附带条件；自愿证据来自既有 post audit。",
                }

        existing_castration_agreements = len(db.list_negotiation_agreements(minister_name=wang.name, action_kind="castration"))
        waiting_identity_id = db.create_conversation_goal(
            state,
            minister_name=wang.name,
            action_kind="castration",
            title="自愿净身入内廷并安置家小",
            target_text="本人明确自愿净身入内廷",
            threshold=90,
            score=82,
            status=GOAL_WAITING,
            condition_status="pending",
            conditions=[{"description": "安置家小", "status": "pending", "evidence": "NPC原文：臣愿自净入内廷，但求安置家小"}],
            expires_turn=int(state.turn) + 3,
            last_delta={
                "explicit_consent": True,
                "audit": {
                    "explicit_consent": True,
                    "private_reason": "NPC原文：臣愿自净入内廷，但求安置家小。",
                },
            },
        )
        reviewed_identity = review_conversation_goals(
            db,
            state,
            decree_text="着有司安置王承恩家小。",
            phase="preresolve",
            audit_client=IdentityConditionAudit(),
        )
        waiting_identity = db.get_conversation_goal(waiting_identity_id)
        assert_true(reviewed_identity, "identity condition audit should review waiting identity goal")
        assert_true(waiting_identity and waiting_identity["status"] == GOAL_SEALED, "prior explicit consent should carry into condition audit")
        assert_true(
            len(db.list_negotiation_agreements(minister_name=wang.name, action_kind="castration")) > existing_castration_agreements,
            "sealed identity condition goal should create an agreement",
        )

        abandon_seed_text = "朕还要你密查一桩钱粮旧案，先不要走漏风声。"
        prep = prepare_dialogue_context(db, state, yang, abandon_seed_text, persistent=True, audit_client=fake)
        record_dialogue_effects(
            db,
            state,
            yang,
            abandon_seed_text,
            "臣可密查，但须明旨授权，另添两名可靠书吏。",
            prep,
            audit_client=fake,
        )
        goal = latest_goal(db, yang.name)
        assert_true(goal["status"] == GOAL_WAITING, "abandon setup should leave a waiting goal")

        abandon_text = "此事先作罢，不谈密查了。"
        prep = prepare_dialogue_context(db, state, yang, abandon_text, persistent=True, audit_client=fake)
        abandoned = record_dialogue_effects(db, state, yang, abandon_text, "臣谨记圣意，暂候后命。", prep, audit_client=fake)
        assert_true(abandoned.get("event") == "abandoned", "abandon phrase should abandon active/waiting goal")

        rollback_actor = content.characters["温体仁"]
        rollback_before = db.capture_chat_rollback_snapshot()
        chat_turn_id = db.create_chat_turn(state, rollback_actor.name, "probe-rollback", 0)
        rollback_text = "朕命你承办清丈新政，先替朕压住部院掣肘。"
        prep = prepare_dialogue_context(db, state, rollback_actor, rollback_text, persistent=True, audit_client=fake)
        record_dialogue_effects(
            db,
            state,
            rollback_actor,
            rollback_text,
            "臣愿承办，但须先有明旨定章程，并给部院会同名分。",
            prep,
            source_chat_turn_id=chat_turn_id,
            audit_client=fake,
        )
        rollback_after = db.capture_chat_rollback_snapshot()
        db.record_chat_turn_rollback_diffs(chat_turn_id, rollback_before, rollback_after)
        assert_true(
            len(rollback_after["conversation_goals"]) > len(rollback_before["conversation_goals"]),
            "rollback setup should create a goal",
        )
        assert_true(
            len(rollback_after["conversation_goal_events"]) > len(rollback_before["conversation_goal_events"]),
            "rollback setup should create goal events",
        )
        db.undo_chat_turn(chat_turn_id)
        rollback_restored = db.capture_chat_rollback_snapshot()
        for table in (
            "conversation_goals",
            "conversation_goal_events",
            "minister_stances",
            "negotiation_agreements",
            "negotiation_tasks",
        ):
            assert_true(rollback_restored[table] == rollback_before[table], f"undo should restore {table}")

        class ActiveNoneAudit(FakeDialogueAudit):
            def pre(self, payload: Dict[str, object]) -> Dict[str, object]:
                return {
                    "goal_decision": "new",
                    "action_kind": "policy",
                    "title": "试探清丈新政",
                    "target_text": "劝其考虑清丈新政",
                    "confidence": 92,
                }

            def post(self, payload: Dict[str, object]) -> Dict[str, object]:
                return {
                    "goal_decision": "new",
                    "action_kind": "policy",
                    "title": "试探清丈新政",
                    "target_text": "劝其考虑清丈新政",
                    "stance": "neutral",
                    "handshake_status": "none",
                    "goal_status": "active",
                    "score_delta": 10,
                    "score_after": 10,
                    "threshold": 82,
                    "conditions": [],
                    "blockers": [],
                    "agreement_action": "none",
                    "confidence": 92,
                    "public_hint": "对方只是听取，并未承诺。",
                }

        active_none_actor = content.characters["温体仁"]
        active_none_prep = prepare_dialogue_context(
            db,
            state,
            active_none_actor,
            "卿先听朕说清丈之议。",
            persistent=True,
            audit_client=ActiveNoneAudit(),
        )
        active_none = record_dialogue_effects(
            db,
            state,
            active_none_actor,
            "卿先听朕说清丈之议。",
            "臣谨听圣裁，尚需斟酌。",
            active_none_prep,
            audit_client=ActiveNoneAudit(),
        )
        assert_true(active_none.get("event") == "progress", "active/none handshake should record progress, not crash")
        assert_true(not active_none.get("agreement_id"), "active/none handshake must not create agreement")
        quiet_with_active = prepare_dialogue_context(
            db,
            state,
            active_none_actor,
            "卿今日身体如何？",
            persistent=True,
            audit_client=fake,
        )
        assert_true(not quiet_with_active.prefix, "active goal should not occupy NPC prompt when pre audit says none")

        print("[dialogue_goal_probe] ok")


if __name__ == "__main__":
    run_probe()
