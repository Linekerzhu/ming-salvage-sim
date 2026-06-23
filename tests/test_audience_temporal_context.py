import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim.db import GameDB
from ming_sim.models import CourtContext
from ming_sim.registry import build_personal_chat_memory_brief
from ming_sim.upgrade_schema import KV_CURRENT_DAY, kv_set_int


def _fresh(tmp: str):
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    return db, db.load_state()


class AudienceTemporalContextTests(unittest.TestCase):
    def test_audience_context_reports_days_since_last_summon(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            db.append_chat_message("韩爌", state.turn, "minister", "臣遵旨，三日内具本回奏。", day=5)
            kv_set_int(db, KV_CURRENT_DAY, 12)

            context = db.audience_temporal_context("韩爌", current_turn=state.turn, current_day=12)

            self.assertTrue(context["has_prior_audience"])
            self.assertEqual(context["days_since_last_audience"], 7)
            self.assertEqual(context["continuity_tone"], "warm")
            self.assertIn("三日内具本", context["last_excerpt"])

    def test_personal_chat_memory_brief_includes_day_gap(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            db.append_chat_message("韩爌", state.turn, "user", "卿上回所许，今日可有回报？", day=5)
            db.append_chat_message("韩爌", state.turn, "minister", "臣已查到户部旧账线索。", day=5)
            kv_set_int(db, KV_CURRENT_DAY, 12)
            character = db.content.characters["韩爌"]

            brief = build_personal_chat_memory_brief(character, CourtContext(state=state, db=db))

            self.assertIn("第1回合第5日", brief)
            self.assertIn("距今7日", brief)
            self.assertIn("户部旧账线索", brief)

    def test_recent_chat_history_exposes_game_day_for_ui_separators(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            db.append_chat_message("韩爌", state.turn, "user", "今日先议此事。", day=4)
            db.append_chat_message("韩爌", state.turn, "minister", "臣谨记。", day=4)
            db.append_chat_message("韩爌", state.turn, "user", "隔日再问，卿办得如何？", day=7)

            history = db.load_recent_chat_history(limit_per_minister=10)["韩爌"]

            self.assertEqual([row.get("day") for row in history], [4, 4, 7])


if __name__ == "__main__":
    unittest.main()
