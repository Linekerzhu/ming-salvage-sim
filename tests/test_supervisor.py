"""监军掣肘 E4 测试：遣/撤监军、监军抑割据掣肘军务、warlord抉择遣监军。零 LLM。"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim import frontier, timeflow
from ming_sim.db import GameDB
from ming_sim.eunuch_power import get_eunuch_power


def _fresh(tmp: str):
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    state = db.load_state()
    day = timeflow.ensure_active(db, state)
    return db, state, day


def _an_army(db):
    return str(db.conn.execute(
        "SELECT id FROM armies WHERE owner_power='ming' AND maintenance_per_turn>0 LIMIT 1").fetchone()["id"])


class DispatchTests(unittest.TestCase):
    def test_dispatch_requires_eunuch(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            aid = _an_army(db)
            self.assertFalse(frontier.dispatch_supervisor(db, state, aid, "韩爌", day)["ok"])  # 非宦官

    def test_dispatch_rejects_eunuch_office_without_eunuch_sex(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            aid = _an_army(db)
            db.conn.execute(
                "UPDATE characters SET office='司礼监随堂太监', office_type='司礼监', "
                "faction='内廷', sex='male' WHERE name='韩爌'"
            )
            db.conn.commit()
            r = frontier.dispatch_supervisor(db, state, aid, "韩爌", day)
            self.assertFalse(r["ok"])
            self.assertEqual(str(db.conn.execute(
                "SELECT supervisor FROM armies WHERE id=?", (aid,)).fetchone()["supervisor"]), "")

    def test_dispatch_sets_supervisor_and_raises_power(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            aid = _an_army(db)
            p0 = get_eunuch_power(db)
            r = frontier.dispatch_supervisor(db, state, aid, "王体乾", day)
            self.assertTrue(r["ok"])
            sup = str(db.conn.execute("SELECT supervisor FROM armies WHERE id=?", (aid,)).fetchone()["supervisor"])
            self.assertEqual(sup, "王体乾")
            self.assertGreater(get_eunuch_power(db), p0)  # 内廷伸入军 → 权阉涨

    def test_recall_clears_supervisor(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            aid = _an_army(db)
            frontier.dispatch_supervisor(db, state, aid, "王体乾", day)
            r = frontier.recall_supervisor(db, state, aid, day)
            self.assertTrue(r["ok"])
            self.assertEqual(str(db.conn.execute(
                "SELECT supervisor FROM armies WHERE id=?", (aid,)).fetchone()["supervisor"]), "")


class TickTests(unittest.TestCase):
    def test_supervisor_curbs_autonomy_but_hurts_morale(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            aid = _an_army(db)
            # 造一个高离心、欠饷、低士气的镇
            db.conn.execute(
                "UPDATE armies SET autonomy=60, arrears=maintenance_per_turn*5, morale=70, loyalty=40 WHERE id=?", (aid,))
            db.conn.commit()
            frontier.dispatch_supervisor(db, state, aid, "王体乾", day)
            morale0 = int(db.conn.execute("SELECT morale FROM armies WHERE id=?", (aid,)).fetchone()["morale"])
            auton0 = int(db.conn.execute("SELECT autonomy FROM armies WHERE id=?", (aid,)).fetchone()["autonomy"])
            frontier.autonomy_tick(db, state, day + 30)
            row = db.conn.execute("SELECT autonomy, morale FROM armies WHERE id=?", (aid,)).fetchone()
            self.assertLess(int(row["autonomy"]), auton0)  # 监军抑割据
            self.assertLess(int(row["morale"]), morale0)   # 然掣肘士气

    def test_dead_supervisor_auto_cleared(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            aid = _an_army(db)
            frontier.dispatch_supervisor(db, state, aid, "王体乾", day)
            db.set_character_status(state, "王体乾", "dead", "test")
            frontier.autonomy_tick(db, state, day + 30)
            self.assertEqual(str(db.conn.execute(
                "SELECT supervisor FROM armies WHERE id=?", (aid,)).fetchone()["supervisor"]), "")


class WarlordChoiceTests(unittest.TestCase):
    def test_warlord_supervise_choice_dispatches(self):
        from ming_sim import court_events
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            db.kv_set(court_events.KV_PENDING, ""); db.kv_set(court_events.KV_COOLDOWN, "{}")
            aid = _an_army(db)
            db.conn.execute("UPDATE armies SET autonomy=80 WHERE id=?", (aid,))
            db.conn.commit()
            frontier._push_warlord(db, {"army_id": aid, "army": "测试镇", "commander": "", "autonomy": 80})
            payload = court_events.evaluate_decisions(db, state, day)
            self.assertEqual(payload["id"], "warlord")
            keys = [ch["key"] for ch in payload["choices"]]
            self.assertIn("supervise", keys)  # 遣监军选项（有宦官候选）
            r = court_events.resolve_decision(db, state, "supervise", day)
            self.assertTrue(r["ok"])
            self.assertTrue(str(db.conn.execute(
                "SELECT supervisor FROM armies WHERE id=?", (aid,)).fetchone()["supervisor"]))  # 监军已遣


if __name__ == "__main__":
    unittest.main()
