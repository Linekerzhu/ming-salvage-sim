"""派系流动（CK3 化 P5）测试：改换门庭、党内倾轧内耗。零 LLM。"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim import court, faction_dynamics, timeflow
from ming_sim.db import GameDB


def _fresh(tmp: str):
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    state = db.load_state()
    timeflow.ensure_active(db, state)
    return db, state


class DefectionTests(unittest.TestCase):
    def test_strong_cross_ally_pulls_defector(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            # 取一个中立官员 + 一个东林要人，结为厚交 → 中立者应被拉入东林
            neutral = db.conn.execute(
                "SELECT name FROM characters WHERE status='active' AND power_id='ming' AND faction='中立' "
                "AND office_type!='后宫' LIMIT 1").fetchone()["name"]
            donglin = db.conn.execute(
                "SELECT name FROM characters WHERE status='active' AND power_id='ming' AND faction='东林' LIMIT 1").fetchone()["name"]
            court._set_opinion(db, neutral, donglin, 70, "奥援", 0)
            db.conn.commit()
            day = state.turn * 30 + 1
            # 多月内应发生改换门庭
            defected = False
            for d in range(day, day + 300, 30):
                evs = faction_dynamics.defection_tick(db, state, d)
                if any(e["kind"] == "defection" and neutral in e["title"] for e in evs):
                    defected = True
                    break
            self.assertTrue(defected, "与东林要人交厚的中立官员应改投东林")
            fac = db.conn.execute("SELECT faction FROM characters WHERE name=?", (neutral,)).fetchone()["faction"]
            self.assertEqual(fac, "东林")

    def test_no_defection_without_cross_ally(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            # 清空所有正向跨派关系 → 无人被拉拢
            db.conn.execute("DELETE FROM relationships WHERE opinion>0")
            db.conn.commit()
            evs = faction_dynamics.defection_tick(db, state, state.turn * 30 + 1)
            self.assertEqual(evs, [])

    def test_defection_shifts_faction_leverage(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            neutral = db.conn.execute(
                "SELECT name FROM characters WHERE status='active' AND power_id='ming' AND faction='东林' "
                "AND office_type!='后宫' LIMIT 1").fetchone()["name"]
            yandang = db.conn.execute(
                "SELECT name FROM characters WHERE status='active' AND power_id='ming' AND faction='阉党' LIMIT 1").fetchone()["name"]
            court._set_opinion(db, neutral, yandang, 80, "密交", 0)
            db.conn.commit()
            before = db.faction_leverage("阉党")
            for d in range(state.turn * 30 + 1, state.turn * 30 + 400, 30):
                if any(e["kind"] == "defection" for e in faction_dynamics.defection_tick(db, state, d)):
                    break
            self.assertGreaterEqual(db.faction_leverage("阉党"), before, "受人之党势力应不降反升")


class StrifeTests(unittest.TestCase):
    def test_internal_rivalry_erodes_faction(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            two = [r["name"] for r in db.conn.execute(
                "SELECT name FROM characters WHERE status='active' AND power_id='ming' AND faction='阉党' LIMIT 2")]
            court._set_opinion(db, two[0], two[1], -70, "内讧", 0)
            court._set_opinion(db, two[1], two[0], -70, "内讧", 0)
            db.conn.commit()
            before = db.faction_leverage("阉党")
            faction_dynamics.strife_tick(db, state, state.turn * 30 + 1)
            self.assertLess(db.faction_leverage("阉党"), before, "内讧之党势力应被内耗")


if __name__ == "__main__":
    unittest.main()
