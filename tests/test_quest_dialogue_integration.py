"""Quest system integration tests with dialogue system.

Tests that sealed handshakes create quests automatically.
"""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim.content import GameContent
from ming_sim.db import GameDB
from ming_sim.dialogue_goals import prepare_dialogue_context, record_dialogue_effects
from ming_sim.dialogue_semantics import SemanticDecision
from ming_sim.models import LLMConfig
from ming_sim.quest_manager import get_quest_manager
from ming_sim.quest_db import apply_quest_schema


def _fresh(tmp: str):
    content = GameContent.load()
    db = GameDB(str(Path(tmp) / "quest_integration.db"), content=content)
    db.seed_static_data()
    # Apply quest schema
    apply_quest_schema(db.conn)
    return content, db, db.load_state()


class QuestDialogueIntegrationTests(unittest.TestCase):
    """Test that sealed dialogue handshakes create quests."""

    def test_sealed_handshake_creates_quest(self):
        """A sealed handshake should automatically create a quest."""
        with TemporaryDirectory() as tmp:
            content, db, state = _fresh(tmp)
            character = content.characters["韩爌"]

            # Mock audit that returns a sealed handshake
            def audit(phase, payload):
                if phase == "post":  # phase is "post", not "dialogue_post"
                    # Return a dict (will be normalized by _normalize_post)
                    return {
                        "valid": True,
                        "stance": "support",
                        "action_kind": "policy",
                        "target_text": "查账核实国库亏空",
                        "title": "查账国库",
                        "core_topic": "查账国库亏空",
                        "conditions": [],
                        "tasks": ["完成查账"],
                        "blockers": [],
                        "handshake_status": "sealed",
                        "goal_status": "sealed",
                        "goal_decision": "new",
                        "score": 100,
                        "threshold": 70,
                        "confidence": 88,
                        "public_hint": "韩爌已表示愿意承办此事。",
                        "private_reason": "大臣已达成约定。",
                        "agreement_action": "create_achieved",
                        "explicit_consent": True,
                        "agreement_formed": True,
                        "performance_status": "committed",
                        "trigger_quote": "户部，朕命你彻查国库亏空之事。",  # 必须有玩家原话
                    }
                return None

            result = record_dialogue_effects(
                db,
                state,
                character,
                "户部，朕命你彻查国库亏空之事。",
                "微臣遵旨。必当查清账目，三月内复奏。",
                audit_client=audit,
                llm_config=LLMConfig(model="test", api_key="test_key", base_url="http://test"),
                agno_db=None,
            )

            # Check that an assignment was created (audience_commission)
            # 旧 RPG 任务路径（NPC→玩家）已废弃；sealed 握手现落「召对交办」差使。
            self.assertIn("quest_created", result)
            quest_result = result["quest_created"]
            self.assertIsNotNone(quest_result)
            self.assertEqual(quest_result.get("assignment_kind"), "audience_commission")
            self.assertEqual(quest_result.get("entry_label"), "召对交办")
            assign_id = quest_result.get("assignment_id")
            self.assertIsNotNone(assign_id)

            # Verify a turn_directive was created with audience_commission kind
            row = db.conn.execute(
                "SELECT assignment_kind, text, assignee FROM turn_directives WHERE id=?",
                (assign_id,),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(str(row["assignment_kind"]), "audience_commission")
            self.assertIn("查账", str(row["text"] or ""))
            self.assertEqual(str(row["assignee"] or ""), "韩爌")

    def test_non_sealed_handshake_no_quest(self):
        """A non-sealed handshake should not create a quest."""
        with TemporaryDirectory() as tmp:
            content, db, state = _fresh(tmp)
            character = content.characters["王承恩"]

            # Mock audit that returns a non-sealed handshake
            def audit(phase, payload):
                if phase == "post":  # phase is "post", not "dialogue_post"
                    return {
                        "valid": True,
                        "stance": "neutral",
                        "action_kind": "general",
                        "target_text": "",
                        "title": "",
                        "handshake_status": "none",
                        "goal_status": "active",
                        "goal_decision": "none",
                        "score": 45,
                        "threshold": 70,
                        "confidence": 72,
                    }
                return None

            result = record_dialogue_effects(
                db,
                state,
                character,
                "最近宫里有什么新鲜事？",
                "回陛下，宫中一切安好。",
                audit_client=audit,
                llm_config=LLMConfig(model="test", api_key="test_key", base_url="http://test"),
                agno_db=None,
            )

            # Should not create a quest
            self.assertNotIn("quest_created", result)

            # Verify no quests in database
            manager = get_quest_manager(db)
            active_quests = manager.get_active_player_quests()
            self.assertEqual(len(active_quests), 0)

    def test_quest_progress_updates(self):
        """Quest progress can be updated through dialogue."""
        with TemporaryDirectory() as tmp:
            content, db, state = _fresh(tmp)
            character = content.characters["袁崇焕"]

            manager = get_quest_manager(db)

            # Create a test quest
            quest = manager.create_quest(
                quest_key="test_liaodong_defense",
                title="辽东防务",
                description="整顿辽东防务",
                category="campaign",
                tier=2,
                objective_type="dialogue_agreement",
                objective_config={"target_count": 2},
                source_type="test",
            )

            # Accept the quest
            player_quest = manager.accept_quest("test_liaodong_defense", state=state)
            self.assertIsNotNone(player_quest)
            self.assertEqual(player_quest.progress_current, 0)

            # Update progress
            updated = manager.update_quest_progress(
                player_quest.id,
                progress_delta=1,
                turn=state.turn,
            )
            self.assertEqual(updated.progress_current, 1)

            # Complete the quest
            completed = manager.update_quest_progress(
                player_quest.id,
                progress_delta=1,
                turn=state.turn + 1,
            )
            self.assertEqual(completed.status, "completed")


if __name__ == "__main__":
    unittest.main()
