import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim.content import GameContent
from ming_sim.db import GameDB
from ming_sim.dialogue_audit import PreDialogueAudit
from ming_sim.dialogue_goals import PreparedDialogue, record_dialogue_effects
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
                            "reason": "禁其妄言",
                        }],
                        "confidence": 96,
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
                            {"name": "洪承畴", "status": "imprisoned", "reason": "押入昭狱"},
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


if __name__ == "__main__":
    unittest.main()
