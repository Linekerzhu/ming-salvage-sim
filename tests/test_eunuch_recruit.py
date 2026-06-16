"""民间募新宦官 E2d + 采选民女黑暗面 E2e 测试。零 LLM。"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim import eunuch_lore as el, timeflow
from ming_sim.db import GameDB


def _fresh(tmp: str):
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    state = db.load_state()
    day = timeflow.ensure_active(db, state)
    return db, state, day


class RecruitTests(unittest.TestCase):
    def test_no_recruit_when_calm(self):
        with TemporaryDirectory() as tmp:
            db, state, _ = _fresh(tmp)
            db.conn.execute("UPDATE regions SET unrest=20")  # 太平 → 无自宫求进
            db.conn.commit()
            self.assertEqual(el.recruit_tick(db, state, 0), [])

    def test_famine_drives_self_castration_recruit(self):
        with TemporaryDirectory() as tmp:
            db, state, _ = _fresh(tmp)
            db.conn.execute("UPDATE regions SET unrest=70")  # 大灾民困
            db.conn.commit()
            before = db.conn.execute("SELECT COUNT(*) c FROM characters").fetchone()["c"]
            evs = el.recruit_tick(db, state, 0)  # day0：闸门命中
            self.assertTrue(any(e["kind"] == "eunuch_recruit" for e in evs))
            after = db.conn.execute("SELECT COUNT(*) c FROM characters").fetchone()["c"]
            self.assertEqual(after, before + 1)  # 新内侍入册
            new_name = str(evs[0]["ref_id"])
            lore = el.get_lore(db, new_name)
            self.assertIsNotNone(lore)
            self.assertIn("自宫求进", lore["note"])
            self.assertGreaterEqual(lore["servility"], 60)  # 卑微奴性偏重

    def test_recruit_capped(self):
        with TemporaryDirectory() as tmp:
            db, state, _ = _fresh(tmp)
            db.conn.execute("UPDATE regions SET unrest=80")
            db.conn.commit()
            for m in range(0, el._RECRUIT_CAP * 4 + 8):
                el.recruit_tick(db, state, m * 60)  # 每60日闸门命中
            self.assertLessEqual(el._auto_recruited_count(db), el._RECRUIT_CAP)


class CaixuanCostTests(unittest.TestCase):
    def test_caixuan_disturbs_people(self):
        from ming_sim.registry import _apply_caixuan_cost

        class _Ctx:
            def __init__(self, db, state):
                self.db, self.state = db, state

        with TemporaryDirectory() as tmp:
            db, state, _ = _fresh(tmp)
            state.metrics["民心"] = 60
            unrest0 = db.conn.execute(
                "SELECT unrest FROM regions WHERE kind!='外藩' ORDER BY population DESC LIMIT 1").fetchone()["unrest"]
            _apply_caixuan_cost(_Ctx(db, state), 3)
            self.assertLess(state.metrics["民心"], 60)  # 采选扰民损民心
            unrest1 = db.conn.execute(
                "SELECT unrest FROM regions WHERE kind!='外藩' ORDER BY population DESC LIMIT 1").fetchone()["unrest"]
            self.assertGreater(unrest1, unrest0)        # 采选之地动荡上升

    def test_zero_is_noop(self):
        from ming_sim.registry import _apply_caixuan_cost

        class _Ctx:
            def __init__(self, db, state):
                self.db, self.state = db, state

        with TemporaryDirectory() as tmp:
            db, state, _ = _fresh(tmp)
            state.metrics["民心"] = 60
            _apply_caixuan_cost(_Ctx(db, state), 0)
            self.assertEqual(state.metrics["民心"], 60)


if __name__ == "__main__":
    unittest.main()
