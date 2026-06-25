import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim.content import GameContent
from ming_sim.db import GameDB
from ming_sim.dialogue_goals import prepare_dialogue_context, record_dialogue_effects
from ming_sim.dialogue_semantics import DialogueActionExecutor, DialogueSemanticEngine, PendingDialogueAction, SemanticDecision
from ming_sim.eunuch_lore import get_lore, record_castration
from ming_sim.models import LLMConfig


def _fresh(tmp: str):
    content = GameContent.load()
    db = GameDB(str(Path(tmp) / "dialogue_semantics.db"), content=content)
    db.seed_static_data()
    return content, db, db.load_state()


class DialogueSemanticEngineTests(unittest.TestCase):
    def test_action_probe_normalizes_allowed_review(self):
        with TemporaryDirectory() as tmp:
            content, db, state = _fresh(tmp)
            character = content.characters["王承恩"]

            def audit(phase, payload):
                if phase == "dialogue_action_intent":
                    return {
                        "allow": True,
                        "phase": "propose",
                        "action_type": "recruitment",
                        "kind": "eunuch",
                        "trigger_quote": "宫里可有新的小内侍可用",
                        "private_reason": "明确要求找新人。",
                        "confidence": 96,
                    }
                return None

            decision = DialogueSemanticEngine(db, state, audit_client=audit).evaluate_action_probe(
                character,
                "宫里可有新的小内侍可用？",
            )

            self.assertTrue(decision.allow)
            self.assertEqual(decision.decision_type, "action")
            self.assertEqual(decision.action_type, "recruitment")
            self.assertEqual(decision.kind, "eunuch")

    def test_low_confidence_action_is_denied(self):
        with TemporaryDirectory() as tmp:
            content, db, state = _fresh(tmp)
            character = content.characters["王承恩"]

            def audit(phase, payload):
                if phase == "dialogue_action_intent":
                    return {
                        "allow": True,
                        "phase": "propose",
                        "action_type": "castration",
                        "target": "韩爌",
                        "trigger_quote": "把韩爌净身",
                        "confidence": 42,
                    }
                return None

            decision = DialogueSemanticEngine(db, state, audit_client=audit).evaluate_action_probe(
                character,
                "把韩爌净身。",
            )

            self.assertFalse(decision.allow)
            self.assertEqual(decision.decision_type, "none")

    def test_missing_trigger_quote_is_denied(self):
        with TemporaryDirectory() as tmp:
            content, db, state = _fresh(tmp)
            character = content.characters["王承恩"]

            def audit(phase, payload):
                if phase == "dialogue_action_intent":
                    return {
                        "allow": True,
                        "phase": "propose",
                        "action_type": "eunuch_care",
                        "target": "王承恩",
                        "mode": "urinary",
                        "confidence": 96,
                    }
                return None

            decision = DialogueSemanticEngine(db, state, audit_client=audit).evaluate_action_probe(
                character,
                "给王承恩调养。",
            )

            self.assertFalse(decision.allow)
            self.assertEqual(decision.decision_type, "none")

    def test_pending_recovery_requires_recent_proposal_evidence(self):
        with TemporaryDirectory() as tmp:
            content, db, state = _fresh(tmp)
            character = content.characters["王承恩"]

            def audit(phase, payload):
                if phase == "dialogue_pending_recovery":
                    return {
                        "allow": True,
                        "phase": "confirm",
                        "action_type": "recruitment",
                        "kind": "eunuch",
                        "trigger_quote": "准，照办",
                        "proposal_evidence": "旧档里说过可以挑人",
                        "private_reason": "误把外部旧档当成最近提案。",
                        "confidence": 96,
                    }
                return None

            decision = DialogueSemanticEngine(db, state, audit_client=audit).evaluate_pending_recovery(
                character,
                "准，照办。",
                ["陛下若准，奴婢便去挑一个忠谨可用的小火者来。"],
            )

            self.assertFalse(decision.allow)
            self.assertEqual(decision.decision_type, "none")

    def test_pending_recovery_requires_trigger_quote_from_current_user_text(self):
        with TemporaryDirectory() as tmp:
            content, db, state = _fresh(tmp)
            character = content.characters["王承恩"]

            def audit(phase, payload):
                if phase == "dialogue_pending_recovery":
                    return {
                        "allow": True,
                        "phase": "confirm",
                        "action_type": "recruitment",
                        "kind": "eunuch",
                        "trigger_quote": "准，照办",
                        "proposal_evidence": "陛下若准，奴婢便去挑一个忠谨可用的小火者来",
                        "private_reason": "误把旧确认句当成本轮原话。",
                        "confidence": 96,
                    }
                return None

            decision = DialogueSemanticEngine(db, state, audit_client=audit).evaluate_pending_recovery(
                character,
                "先说风险，不要招。",
                ["陛下若准，奴婢便去挑一个忠谨可用的小火者来。"],
            )

            self.assertFalse(decision.allow)
            self.assertEqual(decision.decision_type, "none")

    def test_no_llm_or_audit_client_does_not_mutate_semantically(self):
        with TemporaryDirectory() as tmp:
            content, db, state = _fresh(tmp)
            character = content.characters["王承恩"]
            config = LLMConfig(api_key="", base_url="", model="")

            decision = DialogueSemanticEngine(db, state, llm_config=config).evaluate_action_probe(
                character,
                "传韩爌入殿。",
            )

            self.assertFalse(decision.allow)
            self.assertEqual(decision.decision_type, "none")

    def test_injected_audit_client_runs_when_env_disables_real_llm_audit(self):
        old = os.environ.get("MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT")
        os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "1"
        try:
            with TemporaryDirectory() as tmp:
                content, db, state = _fresh(tmp)
                character = content.characters["王承恩"]

                def audit(phase, payload):
                    if phase == "dialogue_action_intent":
                        return {
                            "allow": True,
                            "phase": "propose",
                            "action_type": "recruitment",
                            "kind": "eunuch",
                            "trigger_quote": "宫里可有新的小内侍可用",
                            "confidence": 96,
                        }
                    return None

                decision = DialogueSemanticEngine(db, state, audit_client=audit).evaluate_action_probe(
                    character,
                    "宫里可有新的小内侍可用？",
                )

                self.assertTrue(decision.allow)
                self.assertEqual(decision.action_type, "recruitment")
        finally:
            if old is None:
                os.environ.pop("MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT", None)
            else:
                os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = old

    def test_audit_exception_is_denied(self):
        with TemporaryDirectory() as tmp:
            content, db, state = _fresh(tmp)
            character = content.characters["王承恩"]

            def audit(phase, payload):
                raise RuntimeError("audit exploded")

            decision = DialogueSemanticEngine(db, state, audit_client=audit).evaluate_action_probe(
                character,
                "押入昭狱，割舌禁言。",
            )

            self.assertFalse(decision.allow)
            self.assertEqual(decision.decision_type, "none")

    def test_injected_audit_none_does_not_fallback_to_real_llm(self):
        with TemporaryDirectory() as tmp:
            content, db, state = _fresh(tmp)
            character = content.characters["王承恩"]
            calls = []

            def audit(phase, payload):
                calls.append(phase)
                return None

            decision = DialogueSemanticEngine(
                db,
                state,
                llm_config=LLMConfig(api_key="test-key", base_url="https://example.test/v1", model="test-model"),
                audit_client=audit,
            ).evaluate_action_probe(
                character,
                "押入昭狱，割舌禁言。",
            )

            self.assertEqual(calls, ["dialogue_action_intent"])
            self.assertFalse(decision.allow)
            self.assertEqual(decision.decision_type, "none")

    def test_route_requires_semantic_allow(self):
        with TemporaryDirectory() as tmp:
            content, db, state = _fresh(tmp)
            character = content.characters["王承恩"]

            def audit(phase, payload):
                if phase == "dialogue_route_intent":
                    return {
                        "allow": True,
                        "intent": "summon",
                        "target_name": "韩爌",
                        "trigger_quote": "传韩爌入殿",
                        "private_reason": "玩家明确切换召见对象。",
                        "confidence": 95,
                    }
                return None

            decision = DialogueSemanticEngine(db, state, audit_client=audit).evaluate_route(
                character,
                "传韩爌入殿奏对。",
                route_context={"handler": "王承恩", "can_route_summon": True},
            )

            self.assertTrue(decision.allow)
            self.assertEqual(decision.to_route_review()["intent"], "summon")
            self.assertEqual(decision.target, "韩爌")

    def test_user_message_respects_route_disabled_context_and_continues_action_probe(self):
        with TemporaryDirectory() as tmp:
            content, db, state = _fresh(tmp)
            character = content.characters["王承恩"]
            calls = []

            def audit(phase, payload):
                calls.append(phase)
                if phase == "dialogue_route_intent":
                    return {
                        "allow": True,
                        "intent": "summon",
                        "target_name": "韩爌",
                        "trigger_quote": "传韩爌入殿",
                        "confidence": 96,
                    }
                if phase == "dialogue_action_intent":
                    return {
                        "allow": True,
                        "phase": "propose",
                        "action_type": "recruitment",
                        "kind": "eunuch",
                        "trigger_quote": "宫里可有新的小内侍可用",
                        "confidence": 96,
                    }
                return None

            decision = DialogueSemanticEngine(db, state, audit_client=audit).evaluate_user_message(
                character,
                "宫里可有新的小内侍可用？",
                route_context={"handler": "王承恩", "semantic_route_enabled": False},
            )

            self.assertEqual(calls, ["dialogue_action_intent"])
            self.assertTrue(decision.allow)
            self.assertEqual(decision.decision_type, "action")
            self.assertEqual(decision.action_type, "recruitment")

    def test_pending_action_schema_round_trips_legacy_payload(self):
        pending = PendingDialogueAction.from_mapping(
            {
                "type": "eunuch_care",
                "target": "王承恩",
                "mode": "urinary",
                "trigger_quote": "请太医调养",
                "turn": 7,
            },
            current_turn=7,
        )

        self.assertEqual(pending.type, "eunuch_care")
        self.assertEqual(pending.source_quote, "请太医调养")
        self.assertEqual(pending.to_mapping()["trigger_quote"], "请太医调养")

    def test_action_executor_requires_allowed_semantic_decision(self):
        calls = []

        def execute(action, chat_turn_id=0):
            calls.append((dict(action), int(chat_turn_id or 0)))
            return {"ok": True, "chat_turn_id": chat_turn_id}

        executor = DialogueActionExecutor(execute)
        denied = SemanticDecision(
            decision_type="tool",
            action_type="recruitment",
            phase="confirm",
            kind="eunuch",
            payload={"type": "recruitment", "kind": "eunuch"},
            confidence=96,
            trigger_quote="",
        )
        self.assertEqual(executor.execute(denied, chat_turn_id=7), {})
        self.assertEqual(calls, [])

        allowed = SemanticDecision(
            decision_type="tool",
            action_type="recruitment",
            phase="confirm",
            kind="eunuch",
            payload={"type": "recruitment", "kind": "eunuch"},
            confidence=96,
            trigger_quote="准，招一个小内侍",
        )
        self.assertEqual(executor.execute(allowed, chat_turn_id=9), {"ok": True, "chat_turn_id": 9})
        self.assertEqual(calls[0][0]["type"], "recruitment")
        self.assertEqual(calls[0][0]["kind"], "eunuch")
        self.assertEqual(calls[0][0]["trigger_quote"], "准，招一个小内侍")
        self.assertEqual(calls[0][1], 9)

    def test_lore_intake_requires_trigger_quote(self):
        with TemporaryDirectory() as tmp:
            content, db, state = _fresh(tmp)
            character = content.characters["王承恩"]

            def audit(phase, payload):
                if phase == "dialogue_eunuch_lore_intake":
                    return {
                        "allow": True,
                        "target_names": ["王承恩"],
                        "private_reason": "少了证据短句，不得入档。",
                        "confidence": 96,
                    }
                return None

            targets = DialogueSemanticEngine(db, state, audit_client=audit).evaluate_lore_intake(
                character,
                "请把王承恩的旧档补进去。",
                candidate_names=["王承恩"],
                source_role="user",
            )

            self.assertEqual(targets, [])

    def test_lore_intake_accepts_only_candidate_targets(self):
        with TemporaryDirectory() as tmp:
            content, db, state = _fresh(tmp)
            character = content.characters["王承恩"]

            def audit(phase, payload):
                if phase == "dialogue_eunuch_lore_intake":
                    return {
                        "allow": True,
                        "target_names": ["王承恩", "虚构人"],
                        "trigger_quote": "请把王承恩的旧档补进去",
                        "private_reason": "只可写入候选中的目标。",
                        "confidence": 96,
                    }
                return None

            targets = DialogueSemanticEngine(db, state, audit_client=audit).evaluate_lore_intake(
                character,
                "请把王承恩的旧档补进去。",
                candidate_names=["王承恩"],
                source_role="user",
            )

            self.assertEqual(targets, ["王承恩"])

    def test_unknown_mention_requires_trigger_quote(self):
        with TemporaryDirectory() as tmp:
            content, db, state = _fresh(tmp)
            character = content.characters["王承恩"]

            def audit(phase, payload):
                if phase == "dialogue_unknown_mention_intake":
                    return {
                        "allow": True,
                        "accepted_names": ["小禄子"],
                        "private_reason": "少了证据短句，不得入候选池。",
                        "confidence": 96,
                    }
                return None

            names = DialogueSemanticEngine(db, state, audit_client=audit).evaluate_unknown_mentions(
                character,
                "内书堂有个小禄子可带来问话。",
                candidate_names=["小禄子"],
                purpose="cache_candidate",
            )

            self.assertEqual(names, [])

    def test_unknown_mention_accepts_only_candidate_names(self):
        with TemporaryDirectory() as tmp:
            content, db, state = _fresh(tmp)
            character = content.characters["王承恩"]

            def audit(phase, payload):
                if phase == "dialogue_unknown_mention_intake":
                    return {
                        "allow": True,
                        "accepted_names": ["小禄子", "小顺子"],
                        "trigger_quote": "小禄子可带来问话",
                        "private_reason": "只可接受候选名单中的人物。",
                        "confidence": 96,
                    }
                return None

            names = DialogueSemanticEngine(db, state, audit_client=audit).evaluate_unknown_mentions(
                character,
                "内书堂有个小禄子可带来问话。",
                candidate_names=["小禄子"],
                purpose="cache_candidate",
            )

            self.assertEqual(names, ["小禄子"])

    def test_post_chat_directive_pressure_is_coordinated_by_semantic_engine(self):
        with TemporaryDirectory() as tmp:
            content, db, state = _fresh(tmp)
            character = content.characters["韩爌"]
            calls = []

            def audit(phase, payload):
                calls.append(phase)
                if phase == "dialogue_directive_pressure":
                    self.assertEqual((payload.get("directive_context") or {}).get("id"), 42)
                    return {
                        "allow": True,
                        "kind": "pressed",
                        "forceful": True,
                        "trigger_quote": "把这件差使压实",
                        "answer_evidence": "臣即日具奏",
                        "private_reason": "明确压实旨意。",
                        "confidence": 96,
                    }
                return None

            decision = DialogueSemanticEngine(db, state, audit_client=audit).evaluate_post_chat(
                character,
                "把这件差使压实。",
                "臣即日具奏。",
                kind="directive_pressure",
                context={"id": 42},
            )

            self.assertEqual(calls, ["dialogue_directive_pressure"])
            self.assertTrue(decision.allow)
            self.assertEqual(decision.action_type, "directive_pressure")
            self.assertEqual(decision.raw["kind"], "pressed")

    def test_post_chat_directive_pressure_requires_answer_evidence(self):
        with TemporaryDirectory() as tmp:
            content, db, state = _fresh(tmp)
            character = content.characters["韩爌"]

            def audit(phase, payload):
                if phase == "dialogue_directive_pressure":
                    return {
                        "allow": True,
                        "kind": "pressed",
                        "trigger_quote": "把这件差使压实",
                        "confidence": 96,
                    }
                return None

            decision = DialogueSemanticEngine(db, state, audit_client=audit).evaluate_post_chat(
                character,
                "把这件差使压实。",
                "臣惶恐。",
                kind="directive_pressure",
                context={"id": 42},
            )

            self.assertFalse(decision.allow)
            self.assertEqual(decision.decision_type, "none")

    def test_post_chat_decision_testimony_is_coordinated_by_semantic_engine(self):
        with TemporaryDirectory() as tmp:
            content, db, state = _fresh(tmp)
            character = content.characters["韩爌"]
            calls = []

            def audit(phase, payload):
                calls.append(phase)
                if phase == "dialogue_decision_testimony":
                    self.assertEqual((payload.get("decision_context") or {}).get("ref_id"), "rival_feud")
                    return {
                        "allow": True,
                        "kind": "evidence",
                        "trigger_quote": "弹劾魏忠贤有何证据",
                        "answer_evidence": "臣有账册与人证",
                        "private_reason": "皇帝正在为待裁案取证。",
                        "confidence": 96,
                    }
                return None

            decision = DialogueSemanticEngine(db, state, audit_client=audit).evaluate_post_chat(
                character,
                "弹劾魏忠贤有何证据？",
                "臣有账册与人证。",
                kind="decision_testimony",
                context={"kind": "decision", "ref_id": "rival_feud"},
            )

            self.assertEqual(calls, ["dialogue_decision_testimony"])
            self.assertTrue(decision.allow)
            self.assertEqual(decision.action_type, "decision_testimony")
            self.assertEqual(decision.kind, "evidence")

    def test_post_chat_decision_testimony_requires_answer_evidence(self):
        with TemporaryDirectory() as tmp:
            content, db, state = _fresh(tmp)
            character = content.characters["韩爌"]

            def audit(phase, payload):
                if phase == "dialogue_decision_testimony":
                    return {
                        "allow": True,
                        "kind": "evidence",
                        "trigger_quote": "弹劾魏忠贤有何证据",
                        "confidence": 96,
                    }
                return None

            decision = DialogueSemanticEngine(db, state, audit_client=audit).evaluate_post_chat(
                character,
                "弹劾魏忠贤有何证据？",
                "臣惶恐。",
                kind="decision_testimony",
                context={"kind": "decision", "ref_id": "rival_feud"},
            )

            self.assertFalse(decision.allow)
            self.assertEqual(decision.decision_type, "none")

    def test_post_chat_bargain_attitude_is_coordinated_by_semantic_engine(self):
        with TemporaryDirectory() as tmp:
            content, db, state = _fresh(tmp)
            character = content.characters["韩爌"]

            def audit(phase, payload):
                if phase == "dialogue_bargain_attitude":
                    self.assertEqual((payload.get("bargain_context") or {}).get("kind"), "petition")
                    return {
                        "allow": True,
                        "attitude": "press",
                        "trigger_quote": "先交账册",
                        "private_reason": "明确索证。",
                        "confidence": 95,
                    }
                return None

            decision = DialogueSemanticEngine(db, state, audit_client=audit).evaluate_post_chat(
                character,
                "先交账册，再议。",
                "臣遵旨。",
                kind="bargain_attitude",
                context={"kind": "petition"},
            )

            self.assertTrue(decision.allow)
            self.assertEqual(decision.action_type, "bargain_attitude")
            self.assertEqual(decision.raw["attitude"], "press")

    def test_goal_agreement_audits_are_coordinated_by_semantic_engine(self):
        with TemporaryDirectory() as tmp:
            content, db, state = _fresh(tmp)
            character = content.characters["韩爌"]
            calls = []

            def audit(phase, payload):
                calls.append(phase)
                if phase == "pre":
                    return {
                        "goal_decision": "new",
                        "goal_relation": "distinct_goal",
                        "action_kind": "court_commitment",
                        "title": "共办查账",
                        "target_text": "韩爌须三日内查明内库旧账。",
                        "confidence": 95,
                        "public_hint": "韩爌愿先查内库旧账。",
                        "npc_guidance": "可谨慎应承，但要留证。",
                    }
                if phase == "post":
                    return {
                        "goal_decision": "new",
                        "goal_relation": "distinct_goal",
                        "action_kind": "court_commitment",
                        "title": "共办查账",
                        "target_text": "韩爌须三日内查明内库旧账。",
                        "stance": "support",
                        "handshake_status": "sealed",
                        "goal_status": "sealed",
                        "score_after": 100,
                        "threshold": 70,
                        "agreement_action": "create_pending",
                        "tasks": ["三日内查明内库旧账并回奏证据"],
                        "trigger_quote": "替朕查明内库旧账",
                        "public_hint": "韩爌已领查账之约。",
                        "confidence": 96,
                    }
                return None

            prepared = prepare_dialogue_context(
                db,
                state,
                character,
                "替朕查明内库旧账。",
                llm_config=LLMConfig(api_key="", base_url="", model=""),
                audit_client=audit,
            )
            self.assertEqual(prepared.detection.action_kind, "court_commitment")

            result = record_dialogue_effects(
                db,
                state,
                character,
                "替朕查明内库旧账。",
                "臣愿领旨，三日内回奏。",
                prepared,
                llm_config=LLMConfig(api_key="", base_url="", model=""),
                audit_client=audit,
            )

            self.assertEqual(calls, ["pre", "post"])
            self.assertEqual(result["event"], "sealed")
            self.assertGreater(int(result.get("agreement_id") or 0), 0)
            agreements = db.list_negotiation_agreements(minister_name="韩爌", action_kind="court_commitment")
            self.assertEqual(len(agreements), 1)
            self.assertEqual(agreements[0]["tasks"][0]["status"], "pending")

    def test_castration_lore_and_eunuch_care_agreement_joint_simulation(self):
        with TemporaryDirectory() as tmp:
            content, db, state = _fresh(tmp)
            character = content.characters["韩爌"]
            record_castration(db, "韩爌", forced=True, day=1, detail_text="联合仿真：强旨改入内廷")
            self.assertIsNotNone(get_lore(db, "韩爌"))

            def audit(phase, payload):
                if phase == "pre":
                    return {
                        "goal_decision": "new",
                        "goal_relation": "distinct_goal",
                        "action_kind": "court_commitment",
                        "title": "尿路调养",
                        "target_text": "韩爌请太医调养净身旧患。",
                        "confidence": 94,
                        "public_hint": "韩爌净身旧患需要调养。",
                    }
                if phase == "post":
                    return {
                        "goal_decision": "new",
                        "goal_relation": "distinct_goal",
                        "action_kind": "court_commitment",
                        "title": "尿路调养",
                        "target_text": "韩爌请太医调养净身旧患。",
                        "stance": "support",
                        "handshake_status": "sealed",
                        "goal_status": "sealed",
                        "score_after": 100,
                        "threshold": 70,
                        "agreement_action": "create_pending",
                        "tasks": ["请太医调理韩爌尿闭漏尿旧患", "回奏调养后差遣风险"],
                        "trigger_quote": "朕准你请太医调养旧患",
                        "public_hint": "韩爌调养旧患已入履约账。",
                        "confidence": 95,
                    }
                return None

            prepared = prepare_dialogue_context(
                db,
                state,
                character,
                "朕准你请太医调养旧患。",
                llm_config=LLMConfig(api_key="", base_url="", model=""),
                audit_client=audit,
            )
            result = record_dialogue_effects(
                db,
                state,
                character,
                "朕准你请太医调养旧患。",
                "奴婢叩谢天恩，愿照方调养，回头照常当差。",
                prepared,
                llm_config=LLMConfig(api_key="", base_url="", model=""),
                audit_client=audit,
            )

            self.assertEqual(result["event"], "sealed")
            agreements = db.list_negotiation_agreements(minister_name="韩爌", action_kind="court_commitment")
            self.assertEqual(len(agreements), 1)
            self.assertIn("尿路调养", str(agreements[0]["topic"]))
            self.assertEqual(len(agreements[0]["tasks"]), 2)


if __name__ == "__main__":
    unittest.main()
