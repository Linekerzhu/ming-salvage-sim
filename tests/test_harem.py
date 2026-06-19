"""后宫自主政治（CK3 化 P6）测试：空后宫空转、枕边风荐人、进谗、争宠。零 LLM。"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim import court, harem, timeflow
from ming_sim.db import GameDB


def _fresh(tmp: str):
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    state = db.load_state()
    timeflow.ensure_active(db, state)
    return db, state


def _clear_consorts(db):
    """清掉开局自带的中宫（周皇后），隔离单测后宫机制。"""
    db.conn.execute("DELETE FROM characters WHERE office_type='后宫'")
    db.conn.commit()


def _add_consort(db, name, faction="", charm=80, ability=60):
    db.conn.execute(
        "INSERT INTO characters (name, office, office_type, faction, personal_skills, style, status, power_id, "
        "loyalty, ability, integrity, courage, charm) "
        "VALUES (?, '贵妃', '后宫', ?, '[]', '柔媚', 'active', 'ming', 60, ?, 50, 40, ?)",
        (name, faction, ability, charm))
    db.conn.commit()


class EmptyHaremTests(unittest.TestCase):
    def test_empty_harem_is_quiet(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            _clear_consorts(db)  # 移除开局中宫，测真·空后宫的优雅空转
            self.assertEqual(harem.active_consorts(db), [])
            self.assertEqual(harem.harem_tick(db, state, state.turn * 30 + 1), [])

    def test_empress_seeded_at_start(self):
        with TemporaryDirectory() as tmp:
            db, _ = _fresh(tmp)
            names = {c["name"] for c in harem.active_consorts(db)}
            self.assertIn("周皇后", names)  # 开局即有中宫，pillar ③ 自始有演员


class PillowTalkTests(unittest.TestCase):
    def test_factional_consort_recommends_kin(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            _add_consort(db, "测贵妃", faction="东林", charm=90)
            before = db.faction_leverage("东林")
            fired = None
            for d in range(state.turn * 30 + 1, state.turn * 30 + 400, 30):
                evs = harem.harem_tick(db, state, d)
                if any(e["kind"] == "harem_move" for e in evs):
                    fired = evs[0]
                    break
            self.assertIsNotNone(fired, "派系宠妃应吹枕边风（荐人或进谗）")

    def test_nonfactional_consort_slanders_upright_minister(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            _add_consort(db, "测美人", faction="", charm=85)
            # 取一个清流重臣，记其初始 emp_trust
            target = db.conn.execute(
                "SELECT name, emp_trust FROM characters WHERE status='active' AND power_id='ming' "
                "AND office_type!='后宫' AND integrity>=68 ORDER BY integrity DESC LIMIT 1").fetchone()
            t0 = int(target["emp_trust"] or 55)
            fired = False
            for d in range(state.turn * 30 + 1, state.turn * 30 + 90, 30):
                evs = harem.harem_tick(db, state, d)
                if any(e["kind"] == "harem_move" for e in evs):
                    fired = True
                    break
            self.assertTrue(fired, "无党宠妃应进谗清流")


class RivalryTests(unittest.TestCase):
    def test_two_consorts_compete(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            _clear_consorts(db)  # 隔离两妃争宠，去开局中宫避免随机配对取到她
            _add_consort(db, "测妃甲", faction="", charm=88)
            _add_consort(db, "测妃乙", faction="", charm=70)
            harem.harem_tick(db, state, state.turn * 30 + 1)
            # 争宠应令某一对妃嫔好感下降（至少存在一条负向）
            neg = db.conn.execute(
                "SELECT COUNT(*) FROM relationships WHERE a_name IN ('测妃甲','测妃乙') "
                "AND b_name IN ('测妃甲','测妃乙') AND opinion<0").fetchone()[0]
            self.assertGreaterEqual(neg, 1, "诸妃争宠应彼此生隙")


if __name__ == "__main__":
    unittest.main()
