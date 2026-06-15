"""活的宫廷（CK3 化 P1）测试：好感网络播种、私心推导、双向调整、自主出招。零 LLM。"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim import court, timeflow
from ming_sim.db import GameDB


def _fresh(tmp: str):
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    state = db.load_state()
    day = timeflow.ensure_active(db, state)
    return db, state, day


class SeedTests(unittest.TestCase):
    def test_seed_populates_relationships_and_agendas(self):
        with TemporaryDirectory() as tmp:
            db, _, _ = _fresh(tmp)
            rel_n = db.conn.execute("SELECT COUNT(*) FROM relationships").fetchone()[0]
            ag_n = db.conn.execute("SELECT COUNT(*) FROM npc_agendas").fetchone()[0]
            self.assertGreater(rel_n, 100, "应从 npc_network 关系播种出可观的好感网络")
            self.assertGreater(ag_n, 50, "应为在朝官员播种个人私心")

    def test_seed_is_idempotent(self):
        with TemporaryDirectory() as tmp:
            db, _, _ = _fresh(tmp)
            before = db.conn.execute("SELECT COUNT(*) FROM relationships").fetchone()[0]
            res = court.seed_court(db)  # 再播一次
            self.assertEqual(res["relationships"], 0)
            self.assertEqual(res["agendas"], 0)
            after = db.conn.execute("SELECT COUNT(*) FROM relationships").fetchone()[0]
            self.assertEqual(before, after)

    def test_only_ming_court_gets_agendas(self):
        with TemporaryDirectory() as tmp:
            db, _, _ = _fresh(tmp)
            # 后金/蒙古等敌国不应混入"宫廷"网络
            bad = db.conn.execute(
                "SELECT COUNT(*) FROM npc_agendas a JOIN characters c ON c.name=a.name "
                "WHERE c.power_id!='ming'"
            ).fetchone()[0]
            self.assertEqual(bad, 0, "敌国势力不应有朝廷私心")


class ValenceTests(unittest.TestCase):
    def test_relation_valence_sign(self):
        self.assertGreater(court._valence("同门"), 0)
        self.assertGreater(court._valence("姻亲"), court._valence("同乡"))
        self.assertLess(court._valence("政敌"), 0)
        self.assertLess(court._valence("夙仇"), court._valence("旧怨"))

    def test_faction_baseline(self):
        self.assertGreater(court._faction_baseline("东林", "东林"), 0)
        self.assertLess(court._faction_baseline("阉党", "东林"), 0)
        self.assertEqual(court._faction_baseline("中立", "东林"), 0)


class OpinionTests(unittest.TestCase):
    def test_adjust_clamps_and_reciprocates(self):
        with TemporaryDirectory() as tmp:
            db, _, _ = _fresh(tmp)
            a, b = "甲测", "乙测"
            court._set_opinion(db, a, b, 0, "", 0)
            court.adjust_opinion(db, a, b, 200, "测试", day=5)  # 超界
            self.assertEqual(court.get_opinion(db, a, b), 100)
            # 对向以七折同步（0 + 200*0.7 → clamp 100）
            self.assertEqual(court.get_opinion(db, b, a), 100)
            court.adjust_opinion(db, a, b, -250, "翻脸", day=6)
            self.assertEqual(court.get_opinion(db, a, b), -100)

    def test_allies_and_rivals_ordering(self):
        with TemporaryDirectory() as tmp:
            db, _, _ = _fresh(tmp)
            # 用真实在朝官员，保证 JOIN characters status='active' 命中
            names = [r["name"] for r in db.conn.execute(
                "SELECT name FROM characters WHERE status='active' AND power_id='ming' LIMIT 4")]
            hero = names[0]
            db.conn.execute("DELETE FROM relationships WHERE a_name=?", (hero,))  # 清开局已播种的，便于确定性断言
            court._set_opinion(db, hero, names[1], 60, "盟友", 0)
            court._set_opinion(db, hero, names[2], 30, "同乡", 0)
            court._set_opinion(db, hero, names[3], -55, "政敌", 0)
            db.conn.commit()
            allies = court.allies_of(db, hero, limit=5)
            rivals = court.rivals_of(db, hero, limit=5)
            self.assertEqual([a["name"] for a in allies][:2], [names[1], names[2]])
            self.assertEqual(rivals[0]["name"], names[3])


class AgendaTests(unittest.TestCase):
    def test_archetypes(self):
        # 边镇 → 自重
        k, *_ = court._derive_agenda("X", "蓟辽总兵", "军事", "中立", 60, 50, 50, 20, "")
        self.assertEqual(k, "entrench")
        # 阉党残余 → 自保
        k, *_ = court._derive_agenda("Y", "侍郎", "京官", "阉党", 60, 50, 50, 20, "")
        self.assertEqual(k, "survive")
        # 清流/高节 → 复仇（廓清阉党）
        k, *_ = court._derive_agenda("Z", "御史", "京官", "东林", 60, 80, 50, 20, "直言不讳")
        self.assertEqual(k, "revenge")


class MoveTests(unittest.TestCase):
    def test_deep_rivalry_yields_impeachment(self):
        with TemporaryDirectory() as tmp:
            db, state, _ = _fresh(tmp)
            names = [r["name"] for r in db.conn.execute(
                "SELECT name FROM characters WHERE status='active' AND power_id='ming' "
                "AND office_type!='后宫' ORDER BY courage DESC LIMIT 2")]
            attacker, target = names[0], names[1]
            # 制造深仇 + 确保有私心 + 高胆
            court._set_opinion(db, attacker, target, -80, "夙仇", 0)
            db.conn.execute("UPDATE characters SET courage=90 WHERE name=?", (attacker,))
            db.conn.execute("INSERT OR REPLACE INTO npc_agendas(name,kind,title,target_name,intensity,status) "
                            "VALUES (?, 'revenge','报仇','',99,'active')", (attacker,))
            db.conn.commit()
            before = db.conn.execute("SELECT COUNT(*) FROM memorials WHERE kind='弹章'").fetchone()[0]
            fired = False
            for d in (8, 18, 28, 38, 48, 58):
                evs = court.court_moves_tick(db, state, d)
                if any(e["kind"] == "court_move" for e in evs):
                    fired = True
                    break
            self.assertTrue(fired, "存在深仇且有胆有私心者，旬日内应自主出弹章")
            after = db.conn.execute("SELECT COUNT(*) FROM memorials WHERE kind='弹章'").fetchone()[0]
            self.assertGreater(after, before, "自主弹劾应落一封弹章进御案")

class RippleTests(unittest.TestCase):
    def test_oust_angers_allies_pleases_rivals(self):
        with TemporaryDirectory() as tmp:
            db, _, _ = _fresh(tmp)
            names = [r["name"] for r in db.conn.execute(
                "SELECT name FROM characters WHERE status='active' AND power_id='ming' LIMIT 4")]
            victim, ally, rival = names[0], names[1], names[2]
            db.conn.execute("DELETE FROM relationships WHERE a_name=?", (victim,))
            court._set_opinion(db, victim, ally, 60, "盟友", 0)
            court._set_opinion(db, victim, rival, -60, "政敌", 0)
            db.conn.execute("UPDATE characters SET emp_trust=55, grievance=20 WHERE name IN (?,?)", (ally, rival))
            db.conn.commit()
            court.ripple_personnel(db, victim, "oust")
            arow = db.conn.execute("SELECT emp_trust, grievance FROM characters WHERE name=?", (ally,)).fetchone()
            rrow = db.conn.execute("SELECT emp_trust, grievance FROM characters WHERE name=?", (rival,)).fetchone()
            self.assertLess(int(arow["emp_trust"]), 55, "党羽对帝信任应下降")
            self.assertGreater(int(arow["grievance"]), 20, "党羽怨气应上升")
            self.assertGreater(int(rrow["emp_trust"]), 55, "政敌对帝信任应上升（称快）")

    def test_favor_gratifies_subject(self):
        with TemporaryDirectory() as tmp:
            db, _, _ = _fresh(tmp)
            n = db.conn.execute(
                "SELECT name FROM characters WHERE status='active' AND power_id='ming' LIMIT 1").fetchone()["name"]
            db.conn.execute("UPDATE characters SET emp_trust=50, grievance=30 WHERE name=?", (n,))
            db.conn.commit()
            court.ripple_personnel(db, n, "favor")
            row = db.conn.execute("SELECT emp_trust, grievance FROM characters WHERE name=?", (n,)).fetchone()
            self.assertGreater(int(row["emp_trust"]), 50, "受恩者对帝信任应上升")
            self.assertLess(int(row["grievance"]), 30, "受恩者怨气应下降")


class QuietTests(unittest.TestCase):
    def test_quiet_when_no_grudges(self):
        with TemporaryDirectory() as tmp:
            db, state, _ = _fresh(tmp)
            # 清空所有负向关系 → 不应有人弹劾
            db.conn.execute("DELETE FROM relationships WHERE opinion<0")
            db.conn.commit()
            # 非旬日不出招
            self.assertEqual(court.court_moves_tick(db, state, 7), [])


if __name__ == "__main__":
    unittest.main()
