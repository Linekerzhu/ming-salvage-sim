"""M5 朝堂剧场测试：派系出招、信号指令、乾纲独断、弹章批红连锁、双向信任。零 LLM。"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim import lifecycle, memorials, theater, timeflow
from ming_sim.db import GameDB
from ming_sim.upgrade_schema import (
    KV_RISK_AVERSION,
    KV_SHI,
    RISK_AVERSION_DEFAULT,
    SHI_DEFAULT,
    kv_int,
    kv_set_int,
)


def _fresh(tmp: str):
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    state = db.load_state()
    day = timeflow.ensure_active(db, state)
    return db, state, day


def _a_faction(db) -> str:
    row = db.conn.execute(
        "SELECT f.name FROM factions f JOIN characters c ON c.faction=f.name "
        "WHERE c.status='active' GROUP BY f.name HAVING COUNT(*)>=2 LIMIT 1"
    ).fetchone()
    return str(row["name"])


class FactionMoveTests(unittest.TestCase):
    def test_hot_faction_moves_within_month(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            db.conn.execute("UPDATE factions SET heat=100")
            db.conn.commit()
            moved = False
            for _ in range(40):
                result = timeflow.advance_days(db, state, 30, stop_on_yellow=False)
                events = [e for r in result["reports"] for e in r["events"]]
                if any(e["kind"] == "faction_move" for e in events):
                    moved = True
                    break
                if result["stopped_by"] == "month_end" or result["advanced"] == 0:
                    break
            self.assertTrue(moved, "heat=100 的派系一个月内应至少出招一次")

    def test_cold_faction_stays_quiet(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            db.conn.execute("UPDATE factions SET heat=0")
            db.conn.commit()
            events = theater.faction_moves_tick(db, state, day + 4)  # 第5日
            self.assertEqual(events, [])

    def test_punish_bumps_faction_heat(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            faction = _a_faction(db)
            member = db.conn.execute(
                "SELECT name FROM characters WHERE faction=? AND status='active' LIMIT 1",
                (faction,)).fetchone()["name"]
            h0 = int(db.conn.execute(
                "SELECT heat FROM factions WHERE name=?", (faction,)).fetchone()["heat"])
            memorials.punish_official(db, state, member, "heavy", day=day)
            h1 = int(db.conn.execute(
                "SELECT heat FROM factions WHERE name=?", (faction,)).fetchone()["heat"])
            self.assertGreater(h1, h0)


class SignalTests(unittest.TestCase):
    def test_zuiji_trades_shi_for_renshi(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            shi0 = kv_int(db, KV_SHI, SHI_DEFAULT)
            ra0 = kv_int(db, KV_RISK_AVERSION, RISK_AVERSION_DEFAULT)
            r = theater.signal_action(db, state, "zuiji", day=day)
            self.assertTrue(r["ok"])
            self.assertLess(kv_int(db, KV_SHI, 0), shi0)
            self.assertLess(kv_int(db, KV_RISK_AVERSION, 0), ra0)

    def test_tingzhang_needs_target_and_chills(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            bad = theater.signal_action(db, state, "tingzhang", day=day)
            self.assertFalse(bad["ok"])
            ra0 = kv_int(db, KV_RISK_AVERSION, RISK_AVERSION_DEFAULT)
            r = theater.signal_action(db, state, "tingzhang", day=day, target="韩爌")
            self.assertTrue(r["ok"])
            self.assertGreater(kv_int(db, KV_RISK_AVERSION, 0), ra0)


class DucaiTests(unittest.TestCase):
    def test_ducai_locked_below_threshold(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            cur = db.conn.execute(
                "INSERT INTO turn_directives (turn, year, period, text, source, status) "
                "VALUES (?,?,?,?,?,?)",
                (state.turn, state.year, state.period, "清查京营空饷", "test", "confirmed"))
            db.conn.commit()
            did = int(cur.lastrowid)
            rows = db.conn.execute("SELECT * FROM turn_directives WHERE id=?", (did,)).fetchall()
            lifecycle.init_directive_lifecycles(db, state, rows, day)
            r = lifecycle.intervene(db, state, did, "ducai", day=day)
            self.assertFalse(r["ok"])  # 势=55 < 70
            kv_set_int(db, KV_SHI, 80)
            r = lifecycle.intervene(db, state, did, "ducai", day=day)
            self.assertTrue(r["ok"])


class ImpeachmentChainTests(unittest.TestCase):
    def test_approve_impeachment_hits_target_and_factions(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            memorials.reset_attention_for_day(db, day)
            faction = _a_faction(db)
            target = db.conn.execute(
                "SELECT name FROM characters WHERE faction=? AND status='active' LIMIT 1",
                (faction,)).fetchone()["name"]
            mid = memorials.create_memorial(
                db, state, day=day, author_name="史可法", org="都察院",
                kind="弹章", urgency=3, summary=f"劾{target}",
                ref_kind="character", ref_id=target)
            g0 = int(db.conn.execute(
                "SELECT grievance FROM characters WHERE name=?", (target,)).fetchone()["grievance"])
            h0 = int(db.conn.execute(
                "SELECT heat FROM factions WHERE name=?", (faction,)).fetchone()["heat"])
            r = memorials.decide_memorial(db, state, mid, "approve", day=day)
            self.assertTrue(r["ok"])
            g1 = int(db.conn.execute(
                "SELECT grievance FROM characters WHERE name=?", (target,)).fetchone()["grievance"])
            h1 = int(db.conn.execute(
                "SELECT heat FROM factions WHERE name=?", (faction,)).fetchone()["heat"])
            self.assertGreater(g1, g0)
            self.assertGreater(h1, h0)


class LeverageTests(unittest.TestCase):
    def test_failed_agreement_becomes_leverage(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            db.conn.execute(
                "INSERT INTO negotiation_agreements "
                "(turn_created, year_created, period_created, minister_name, topic, target_text, status) "
                "VALUES (?,?,?,'王洽','整饬京营','三月内整饬京营','failed')",
                (state.turn, state.year, state.period))
            db.conn.commit()
            items = theater.leverage_payload(db, state, "王洽")
            self.assertEqual(len(items), 1)
            self.assertIn("整饬京营", items[0]["promise"])


if __name__ == "__main__":
    unittest.main()
