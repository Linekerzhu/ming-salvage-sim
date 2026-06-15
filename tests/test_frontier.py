"""地方割据制衡（CK3 化 P7）测试：欠饷/势弱→离心，自专→截留，尾大不掉→抉择。零 LLM。"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim import court_events, frontier, timeflow
from ming_sim.db import GameDB
from ming_sim.upgrade_schema import KV_SHI, kv_set_int


def _fresh(tmp: str):
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    state = db.load_state()
    timeflow.ensure_active(db, state)
    return db, state


def _an_army(db):
    return db.conn.execute(
        "SELECT id, maintenance_per_turn FROM armies WHERE owner_power='ming' AND maintenance_per_turn>0 LIMIT 1"
    ).fetchone()


class AutonomyTests(unittest.TestCase):
    def test_arrears_and_weak_throne_raise_autonomy(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            kv_set_int(db, KV_SHI, 25)  # 君威不振
            a = _an_army(db)
            db.conn.execute("UPDATE armies SET arrears=?, loyalty=30, autonomy=0 WHERE id=?",
                            (a["maintenance_per_turn"] * 8, a["id"]))  # 欠饷八月
            db.conn.commit()
            frontier.autonomy_tick(db, state, state.turn * 30 + 1)
            new = db.conn.execute("SELECT autonomy FROM armies WHERE id=?", (a["id"],)).fetchone()["autonomy"]
            self.assertGreater(new, 0, "欠饷久 + 君威弱应令离心上升")

    def test_paid_and_strong_throne_lowers_autonomy(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            kv_set_int(db, KV_SHI, 75)
            a = _an_army(db)
            db.conn.execute("UPDATE armies SET arrears=0, loyalty=70, autonomy=40 WHERE id=?", (a["id"],))
            db.conn.commit()
            frontier.autonomy_tick(db, state, state.turn * 30 + 1)
            new = db.conn.execute("SELECT autonomy FROM armies WHERE id=?", (a["id"],)).fetchone()["autonomy"]
            self.assertLess(new, 40, "足饷 + 君威隆盛应令离心回落")

    def test_extreme_autonomy_queues_warlord(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            kv_set_int(db, KV_SHI, 20)
            a = _an_army(db)
            db.conn.execute("UPDATE armies SET arrears=?, loyalty=20, autonomy=70 WHERE id=?",
                            (a["maintenance_per_turn"] * 10, a["id"]))
            db.conn.commit()
            frontier.autonomy_tick(db, state, state.turn * 30 + 1)
            self.assertTrue(any(v["army_id"] == a["id"] for v in frontier.warlord_queue(db)),
                            "离心≥72 应入封疆跋扈队列")


class WarlordDecisionTests(unittest.TestCase):
    def _seed_warlord(self, db, state):
        kv_set_int(db, KV_SHI, 20)
        a = _an_army(db)
        # 起于自专档(68)，本月推进跨入跋扈档(≥72) → 入队
        db.conn.execute("UPDATE armies SET arrears=?, loyalty=20, autonomy=68 WHERE id=?",
                        (a["maintenance_per_turn"] * 10, a["id"]))
        db.conn.commit()
        frontier.autonomy_tick(db, state, state.turn * 30 + 1)
        return a["id"]

    def test_warlord_decision_triggers_and_appease_drops_autonomy(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            aid = self._seed_warlord(db, state)
            day = state.turn * 30 + 2
            payload = court_events.evaluate_decisions(db, state, day)
            self.assertIsNotNone(payload)
            self.assertEqual(payload["id"], "warlord")
            before = db.conn.execute("SELECT autonomy FROM armies WHERE id=?", (aid,)).fetchone()["autonomy"]
            res = court_events.resolve_decision(db, state, "appease", day=day)
            self.assertTrue(res["ok"])
            after = db.conn.execute("SELECT autonomy FROM armies WHERE id=?", (aid,)).fetchone()["autonomy"]
            self.assertLess(after, before, "补饷羁縻应令离心骤降")


if __name__ == "__main__":
    unittest.main()
