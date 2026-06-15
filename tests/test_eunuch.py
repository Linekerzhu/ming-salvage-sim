"""随侍太监枢纽回归（零 LLM）：默认挑选/换人/候选/降级/角色简报。"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim import eunuch
from ming_sim.db import GameDB


def _fresh(tmp: str):
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    return db


class EunuchHubTests(unittest.TestCase):
    def test_default_pick_is_wangchengen(self):
        with TemporaryDirectory() as tmp:
            db = _fresh(tmp)
            self.assertEqual(eunuch.get_attending_eunuch(db), "王承恩")
            # 落库幂等
            self.assertEqual(db.kv_get(eunuch.KV_ATTENDING_EUNUCH), "王承恩")

    def test_candidates_eunuchs_first(self):
        with TemporaryDirectory() as tmp:
            db = _fresh(tmp)
            cands = eunuch.list_candidates(db)
            self.assertTrue(cands)
            names = [c["name"] for c in cands]
            self.assertIn("王承恩", names)
            # 宦官置顶：首个候选应为宦官
            self.assertTrue(cands[0]["is_eunuch"])
            wce = next(c for c in cands if c["name"] == "王承恩")
            self.assertTrue(wce["is_eunuch"])

    def test_replace_to_another_eunuch(self):
        with TemporaryDirectory() as tmp:
            db = _fresh(tmp)
            r = eunuch.set_attending_eunuch(db, "曹化淳")
            self.assertTrue(r["ok"], r)
            self.assertEqual(eunuch.get_attending_eunuch(db), "曹化淳")

    def test_replace_rejects_unknown(self):
        with TemporaryDirectory() as tmp:
            db = _fresh(tmp)
            r = eunuch.set_attending_eunuch(db, "查无此人")
            self.assertFalse(r["ok"])
            # 在任者不变（仍为默认）
            self.assertEqual(eunuch.get_attending_eunuch(db), "王承恩")

    def test_fallback_when_preferred_inactive(self):
        with TemporaryDirectory() as tmp:
            db = _fresh(tmp)
            db.conn.execute("UPDATE characters SET status='dead' WHERE name='王承恩'")
            db.conn.commit()
            pick = eunuch.get_attending_eunuch(db)
            self.assertIsNotNone(pick)
            self.assertNotEqual(pick, "王承恩")
            row = db.conn.execute(
                "SELECT office, office_type FROM characters WHERE name=?", (pick,)).fetchone()
            self.assertTrue(eunuch.is_eunuch_like(str(row["office"]), str(row["office_type"])))

    def test_role_brief_mentions_name(self):
        brief = eunuch.eunuch_role_brief("王承恩", "内官监御前")
        self.assertIn("王承恩", brief)
        self.assertIn("随侍", brief)


if __name__ == "__main__":
    unittest.main()
