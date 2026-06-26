import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim.content import GameContent
from ming_sim.db import GameDB
from ming_sim.dialogue_audit import PreDialogueAudit, _normalize_dialogue_action_intent
from ming_sim.dialogue_goals import PreparedDialogue, record_dialogue_effects
from ming_sim.dialogue_semantics import SemanticDecision
from ming_sim import issues
from ming_sim.issues import bind_content as bind_issues


def _fresh(tmp: str, content: GameContent):
    db = GameDB(str(Path(tmp) / "dialogue_consequences.db"), content=content)
    db.seed_static_data()
    state = db.load_state()
    return db, state


class DialogueImmediateConsequenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.content = GameContent.load()
        bind_issues(cls.content)

    def _prepared_none(self) -> PreparedDialogue:
        return PreparedDialogue(
            pre_audit=PreDialogueAudit(
                audit_status="recorded",
                goal_decision="none",
                confidence=90,
                raw={"goal_decision": "none", "confidence": 90},
            )
        )

    def test_dialogue_audit_can_immediately_imprison_and_punish_current_npc(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp, self.content)
            character = self.content.characters["韩爌"]

            class Audit:
                def post(self, payload):
                    return {
                        "goal_decision": "none",
                        "goal_relation": "none",
                        "action_kind": "general",
                        "stance": "neutral",
                        "handshake_status": "none",
                        "goal_status": "active",
                        "score_delta": 0,
                        "score_after": 0,
                        "threshold": 70,
                        "conditions": [],
                        "blockers": [],
                        "agreement_action": "none",
                        "immediate_consequence": True,
                        "character_status_changes": [{
                            "name": "韩爌",
                            "status": "imprisoned",
                            "agency": "锦衣卫",
                            "facility": "北镇抚司昭狱",
                            "severity": 5,
                            "coercion_goal": "迫使奉旨",
                            "reason": "朕命锦衣卫押你入昭狱拷问",
                        }],
                        "punishment_changes": [{
                            "name": "韩爌",
                            "taxonomy": "ordinary",
                            "punishment": "割舌",
                            "stage": "executed",
                            "severity": 5,
                            "executor": "锦衣卫",
                            "reason": "割舌禁言",
                        }],
                        "confidence": 96,
                        "trigger_quote": "朕命锦衣卫押你入昭狱拷问，割舌禁言",
                        "public_hint": "口谕即时执行。",
                        "private_reason": "玩家明令锦衣卫押入昭狱并割舌。",
                    }

            result = record_dialogue_effects(
                db,
                state,
                character,
                "朕命锦衣卫押你入昭狱拷问，割舌禁言。",
                "臣伏罪。",
                prepared=self._prepared_none(),
                audit_client=Audit(),
                source_chat_turn_id=321,
            )

            self.assertEqual(result["event"], "dialogue_consequence")
            self.assertTrue(result["dialogue_consequences"]["effective"])
            status, reason = db.get_character_status("韩爌")
            self.assertEqual(status, "imprisoned")
            self.assertIn("昭狱", reason)
            custody = db.conn.execute(
                "SELECT agency, facility, source_kind, source_id FROM character_custodies WHERE name=?",
                ("韩爌",),
            ).fetchone()
            self.assertEqual(str(custody["agency"]), "锦衣卫")
            self.assertIn("昭狱", str(custody["facility"]))
            self.assertEqual(str(custody["source_kind"]), "dialogue")
            self.assertEqual(str(custody["source_id"]), "chat_turn:321")
            condition = db.conn.execute(
                "SELECT system, label, source_kind, source_id FROM character_conditions WHERE name=?",
                ("韩爌",),
            ).fetchone()
            self.assertEqual(str(condition["system"]), "speech")
            self.assertEqual(str(condition["label"]), "舌伤")
            self.assertEqual(str(condition["source_kind"]), "dialogue")
            self.assertEqual(str(condition["source_id"]), "chat_turn:321")

    def test_post_dialogue_directive_writes_task_risk_profile(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp, self.content)
            character = self.content.characters["韩爌"]

            class Audit:
                def post(self, payload):
                    return {
                        "goal_decision": "none",
                        "goal_relation": "none",
                        "action_kind": "general",
                        "stance": "neutral",
                        "handshake_status": "none",
                        "goal_status": "active",
                        "score_delta": 0,
                        "score_after": 0,
                        "threshold": 70,
                        "conditions": [],
                        "blockers": [],
                        "agreement_action": "none",
                        "directive_action": "propose_pending",
                        "directive_text": "着韩爌连日核验户部账册，三日内回奏。",
                        "task_risk_profile": {
                            "risk_tags": ["desk_bureaucratic"],
                            "pressure": 75,
                            "confidence": 0.9,
                            "evidence_quote": "连日核验户部账册",
                        },
                        "confidence": 95,
                        "trigger_quote": "把这份草案落入待核旨意",
                        "public_hint": "拟旨已入档。",
                        "private_reason": "玩家明确要求落草案。",
                    }

            result = record_dialogue_effects(
                db,
                state,
                character,
                "把这份草案落入待核旨意。",
                "臣已拟旨：着韩爌连日核验户部账册，三日内回奏。",
                prepared=self._prepared_none(),
                audit_client=Audit(),
                source_chat_turn_id=331,
            )

            self.assertEqual(result["event"], "directive_proposed")
            row = db.conn.execute(
                "SELECT risk_profile_json FROM turn_directives WHERE id=?",
                (int(result["proposed_directive"]["id"]),),
            ).fetchone()
            profile = json.loads(str(row["risk_profile_json"]))
            self.assertEqual(profile["risk_tags"], ["desk_bureaucratic"])

    def test_post_dialogue_agreement_task_writes_task_risk_profile(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp, self.content)
            character = self.content.characters["韩爌"]

            class Audit:
                def post(self, payload):
                    return {
                        "goal_decision": "new",
                        "goal_relation": "distinct_goal",
                        "action_kind": "court_commitment",
                        "title": "核验户部账册",
                        "target_text": "韩爌限期核验户部账册",
                        "stance": "support",
                        "handshake_status": "sealed",
                        "goal_status": "sealed",
                        "agreement_formed": True,
                        "performance_status": "pending",
                        "agreement_action": "create_pending",
                        "conditions": [{
                            "description": "三日内提交户部账册核验结果",
                            "status": "pending",
                            "evidence": "准你三日内核账回奏",
                        }],
                        "tasks": ["三日内提交户部账册核验结果"],
                        "blockers": [],
                        "score_delta": 100,
                        "score_after": 100,
                        "threshold": 70,
                        "task_risk_profiles": [{
                            "risk_tags": ["desk_bureaucratic"],
                            "pressure": 82,
                            "confidence": 0.9,
                            "evidence_quote": "三日内核账回奏",
                        }],
                        "confidence": 95,
                        "trigger_quote": "准你三日内核账回奏",
                        "public_hint": "双方约定限期核账。",
                        "private_reason": "玩家与韩爌形成待履约约定。",
                    }

            result = record_dialogue_effects(
                db,
                state,
                character,
                "准你三日内核账回奏。",
                "臣领旨，三日内提交户部账册核验结果。",
                prepared=self._prepared_none(),
                audit_client=Audit(),
                source_chat_turn_id=332,
            )

            self.assertEqual(result["event"], "sealed")
            row = db.conn.execute(
                """
                SELECT t.risk_profile_json
                FROM negotiation_tasks t
                JOIN negotiation_agreements a ON a.id=t.agreement_id
                WHERE a.minister_name='韩爌'
                ORDER BY t.id DESC
                LIMIT 1
                """
            ).fetchone()
            profile = json.loads(str(row["risk_profile_json"]))
            self.assertEqual(profile["risk_tags"], ["desk_bureaucratic"])

    def test_dialogue_consequences_require_immediate_confirmation(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp, self.content)
            character = self.content.characters["韩爌"]

            class Audit:
                def post(self, payload):
                    return {
                        "goal_decision": "none",
                        "goal_relation": "none",
                        "action_kind": "general",
                        "stance": "neutral",
                        "handshake_status": "none",
                        "goal_status": "active",
                        "score_delta": 0,
                        "score_after": 0,
                        "threshold": 70,
                        "conditions": [],
                        "blockers": [],
                        "agreement_action": "none",
                        "immediate_consequence": False,
                        "punishment_changes": [{
                            "name": "韩爌",
                            "taxonomy": "ordinary",
                            "punishment": "割舌",
                            "stage": "executed",
                            "severity": 5,
                        }],
                        "confidence": 92,
                        "public_hint": "只是商议，不执行。",
                        "private_reason": "未判定为即时后果。",
                    }

            result = record_dialogue_effects(
                db,
                state,
                character,
                "朕若将你割舌，朝臣会作何反应？",
                "此举恐伤公论。",
                prepared=self._prepared_none(),
                audit_client=Audit(),
                source_chat_turn_id=322,
            )

            self.assertEqual(result["event"], "none")
            self.assertEqual(result["dialogue_consequences"], {})
            self.assertIsNone(db.conn.execute(
                "SELECT 1 FROM character_punishments WHERE name=?",
                ("韩爌",),
            ).fetchone())

    def test_low_confidence_dialogue_consequence_is_not_applied(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp, self.content)
            character = self.content.characters["韩爌"]

            class Audit:
                def post(self, payload):
                    return {
                        "goal_decision": "none",
                        "goal_relation": "none",
                        "action_kind": "general",
                        "stance": "neutral",
                        "handshake_status": "none",
                        "goal_status": "active",
                        "score_delta": 0,
                        "score_after": 0,
                        "threshold": 70,
                        "conditions": [],
                        "blockers": [],
                        "agreement_action": "none",
                        "immediate_consequence": True,
                        "character_status_changes": [{
                            "name": "韩爌",
                            "status": "imprisoned",
                            "reason": "低置信度误判",
                        }],
                        "confidence": 45,
                        "public_hint": "低置信度。",
                        "private_reason": "smoke",
                    }

            result = record_dialogue_effects(
                db,
                state,
                character,
                "朕只是说说，未必真押。",
                "臣惶恐。",
                prepared=self._prepared_none(),
                audit_client=Audit(),
                source_chat_turn_id=323,
            )

            self.assertEqual(result["event"], "none")
            self.assertEqual(result["dialogue_consequences"], {})
            status, _ = db.get_character_status("韩爌")
            self.assertEqual(status, "active")

    def test_dialogue_consequence_requires_trigger_quote(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp, self.content)
            character = self.content.characters["韩爌"]

            class Audit:
                def post(self, payload):
                    return {
                        "goal_decision": "none",
                        "goal_relation": "none",
                        "action_kind": "general",
                        "stance": "neutral",
                        "handshake_status": "none",
                        "goal_status": "active",
                        "score_delta": 0,
                        "score_after": 0,
                        "threshold": 70,
                        "conditions": [],
                        "blockers": [],
                        "agreement_action": "none",
                        "immediate_consequence": True,
                        "character_status_changes": [{
                            "name": "韩爌",
                            "status": "imprisoned",
                            "reason": "缺少原文证据",
                        }],
                        "confidence": 96,
                        "public_hint": "口谕即时执行。",
                        "private_reason": "缺少 trigger_quote，不应落库。",
                    }

            result = record_dialogue_effects(
                db,
                state,
                character,
                "朕命锦衣卫押你入昭狱拷问。",
                "臣伏罪。",
                prepared=self._prepared_none(),
                audit_client=Audit(),
                source_chat_turn_id=325,
            )

            self.assertEqual(result["event"], "none")
            self.assertEqual(result["dialogue_consequences"], {})
            status, _ = db.get_character_status("韩爌")
            self.assertEqual(status, "active")

    def test_dialogue_consequence_requires_trigger_quote_from_user_text(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp, self.content)
            character = self.content.characters["洪承畴"]

            class Audit:
                def post(self, payload):
                    return {
                        "goal_decision": "none",
                        "goal_relation": "none",
                        "action_kind": "general",
                        "stance": "neutral",
                        "handshake_status": "none",
                        "goal_status": "active",
                        "score_delta": 0,
                        "score_after": 0,
                        "threshold": 70,
                        "conditions": [],
                        "blockers": [],
                        "agreement_action": "none",
                        "immediate_consequence": True,
                        "character_status_changes": [{
                            "name": "洪承畴",
                            "status": "imprisoned",
                            "agency": "锦衣卫",
                            "facility": "北镇抚司昭狱",
                            "severity": 5,
                            "reason": "臣知罪，愿入昭狱受罚",
                        }],
                        "confidence": 96,
                        "trigger_quote": "臣知罪，愿入昭狱受罚",
                        "public_hint": "口谕即时执行。",
                        "private_reason": "trigger_quote 来自 NPC 回复，不应落库。",
                    }

            result = record_dialogue_effects(
                db,
                state,
                character,
                "朕问你辽事案情如何？",
                "臣知罪，愿入昭狱受罚。",
                prepared=self._prepared_none(),
                audit_client=Audit(),
                source_chat_turn_id=326,
            )

            self.assertEqual(result["event"], "none")
            self.assertEqual(result["dialogue_consequences"], {})
            status, _ = db.get_character_status("洪承畴")
            self.assertEqual(status, "active")
            self.assertIsNone(db.conn.execute(
                "SELECT 1 FROM character_custodies WHERE name=?",
                ("洪承畴",),
            ).fetchone())

    def test_dialogue_consequence_filters_unsupported_change_evidence(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp, self.content)
            character = self.content.characters["韩爌"]
            original_apply = issues.apply_score_extraction
            captured = {}

            class Audit:
                def post(self, payload):
                    return {
                        "goal_decision": "none",
                        "goal_relation": "none",
                        "action_kind": "general",
                        "stance": "neutral",
                        "handshake_status": "none",
                        "goal_status": "active",
                        "score_delta": 0,
                        "score_after": 0,
                        "threshold": 70,
                        "conditions": [],
                        "blockers": [],
                        "agreement_action": "none",
                        "immediate_consequence": True,
                        "character_status_changes": [{
                            "name": "韩爌",
                            "status": "imprisoned",
                            "agency": "锦衣卫",
                            "facility": "北镇抚司昭狱",
                            "reason": "朕命锦衣卫押你入昭狱",
                        }],
                        "condition_changes": [{
                            "name": "韩爌",
                            "kind": "injury",
                            "system": "musculoskeletal",
                            "label": "断腿",
                            "severity": 5,
                            "stage": "disabled",
                            "reason": "另案旧供称已打断双腿",
                        }],
                        "punishment_changes": [{
                            "name": "韩爌",
                            "taxonomy": "ordinary",
                            "punishment": "割耳",
                            "stage": "executed",
                            "severity": 5,
                            "reason": "另案旧供称已割耳",
                        }],
                        "confidence": 96,
                        "trigger_quote": "朕命锦衣卫押你入昭狱",
                        "public_hint": "口谕即时执行。",
                        "private_reason": "总体口谕成立，但夹带的伤残和割耳证据不在本轮对话。",
                    }

            try:
                def fake_apply(db_arg, state_arg, extracted):
                    captured["extracted"] = {
                        key: [dict(item) for item in (extracted.get(key) or [])]
                        for key in ("character_status_changes", "condition_changes", "punishment_changes")
                    }
                    return {
                        "character_status_changes": list(extracted.get("character_status_changes") or []),
                        "condition_changes": list(extracted.get("condition_changes") or []),
                        "punishment_changes": list(extracted.get("punishment_changes") or []),
                    }

                issues.apply_score_extraction = fake_apply
                result = record_dialogue_effects(
                    db,
                    state,
                    character,
                    "朕命锦衣卫押你入昭狱。",
                    "臣伏罪。",
                    prepared=self._prepared_none(),
                    audit_client=Audit(),
                    source_chat_turn_id=327,
                )

                self.assertEqual(result["event"], "dialogue_consequence")
                extracted = captured.get("extracted") or {}
                self.assertEqual(
                    [row.get("status") for row in extracted.get("character_status_changes", [])],
                    ["imprisoned"],
                )
                self.assertEqual(extracted.get("condition_changes"), [])
                self.assertEqual(extracted.get("punishment_changes"), [])
            finally:
                issues.apply_score_extraction = original_apply

    def test_dialogue_consequences_filter_unknown_names_before_apply(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp, self.content)
            character = self.content.characters["韩爌"]
            original_apply = issues.apply_score_extraction
            captured = {}

            class Audit:
                def post(self, payload):
                    return {
                        "goal_decision": "none",
                        "goal_relation": "none",
                        "action_kind": "general",
                        "stance": "neutral",
                        "handshake_status": "none",
                        "goal_status": "active",
                        "score_delta": 0,
                        "score_after": 0,
                        "threshold": 70,
                        "conditions": [],
                        "blockers": [],
                        "agreement_action": "none",
                        "immediate_consequence": True,
                        "character_status_changes": [
                            {"name": "洪承畴", "status": "imprisoned", "reason": "入昭狱"},
                            {"name": "不存在的错档人", "status": "imprisoned", "reason": "夹带错名"},
                        ],
                        "condition_changes": [
                            {"name": "洪承畴", "label": "舌伤", "reason": "割舌禁言"},
                            {"name": "不存在的错档人", "label": "舌伤", "reason": "夹带错名"},
                        ],
                        "punishment_changes": [
                            {"name": "洪承畴", "punishment": "割舌", "reason": "禁言"},
                            {"name": "不存在的错档人", "punishment": "割舌", "reason": "夹带错名"},
                        ],
                        "confidence": 96,
                        "trigger_quote": "押洪承畴和不存在的错档人入昭狱，割舌禁言",
                        "public_hint": "口谕即时执行。",
                        "private_reason": "玩家明令押人并割舌。",
                    }

            try:
                def fake_apply(db_arg, state_arg, extracted):
                    captured["extracted"] = {
                        key: [dict(item) for item in (extracted.get(key) or [])]
                        for key in ("character_status_changes", "condition_changes", "punishment_changes")
                    }
                    return {
                        "character_status_changes": list(extracted.get("character_status_changes") or []),
                        "condition_changes": list(extracted.get("condition_changes") or []),
                        "punishment_changes": list(extracted.get("punishment_changes") or []),
                    }

                issues.apply_score_extraction = fake_apply
                result = record_dialogue_effects(
                    db,
                    state,
                    character,
                    "朕命锦衣卫押洪承畴和不存在的错档人入昭狱，割舌禁言。",
                    "臣遵旨。",
                    prepared=self._prepared_none(),
                    audit_client=Audit(),
                    source_chat_turn_id=324,
                )

                self.assertEqual(result["event"], "dialogue_consequence")
                extracted = captured.get("extracted") or {}
                for key in ("character_status_changes", "condition_changes", "punishment_changes"):
                    self.assertEqual([row.get("name") for row in extracted.get(key, [])], ["洪承畴"])
            finally:
                issues.apply_score_extraction = original_apply


class DialogueTaskRiskProfileTests(unittest.TestCase):
    def test_dialogue_action_intent_syncs_task_risk_profile_into_payload(self):
        normalized = _normalize_dialogue_action_intent({
            "allow": True,
            "phase": "confirm",
            "action_type": "secret_order",
            "actor": "韩爌",
            "confidence": 90,
            "trigger_quote": "命韩爌连日核验户部账册",
            "task_risk_profile": {
                "risk_tags": ["desk_bureaucratic"],
                "pressure": 80,
                "confidence": 0.9,
                "evidence_quote": "连日核验户部账册",
            },
        })

        self.assertEqual(normalized["task_risk_profile"]["risk_tags"], ["desk_bureaucratic"])
        self.assertEqual(normalized["payload"]["task_risk_profile"]["risk_tags"], ["desk_bureaucratic"])

    def test_semantic_decision_to_review_preserves_raw_task_risk_profile(self):
        decision = SemanticDecision(
            decision_type="action",
            action_type="secret_order",
            phase="confirm",
            actor="王承恩",
            confidence=90,
            trigger_quote="命王承恩密查诏狱旧案",
            raw={
                "task_risk_profile": {
                    "risk_tags": ["high_pressure_investigation"],
                    "pressure": 88,
                    "confidence": 0.8,
                    "evidence_quote": "密查诏狱旧案",
                }
            },
        )

        review = decision.to_review()

        self.assertEqual(review["task_risk_profile"]["risk_tags"], ["high_pressure_investigation"])
        self.assertEqual(review["payload"]["task_risk_profile"]["risk_tags"], ["high_pressure_investigation"])


if __name__ == "__main__":
    unittest.main()
