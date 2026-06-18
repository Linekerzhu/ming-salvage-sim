"""抉择事件（CK3 化 P2）测试：触发→待决→落子→后果落库→冷却。零 LLM。"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim import court, court_events, timeflow
from ming_sim.db import GameDB
from ming_sim.upgrade_schema import KV_SHI, kv_int


def _fresh(tmp: str):
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    state = db.load_state()
    day = timeflow.ensure_active(db, state)
    return db, state, day


def _two_ming(db):
    return [r["name"] for r in db.conn.execute(
        "SELECT name FROM characters WHERE status='active' AND power_id='ming' "
        "AND office_type!='后宫' LIMIT 2")]


def _erupt(db, a, b, opinion=-75):
    """制造一对深仇 + 一封 a 劾 b 的弹章（宿敌互讦的触发前提：事已上台面）。"""
    court._set_opinion(db, a, b, opinion, "夙仇", 0)
    court._set_opinion(db, b, a, opinion, "夙仇", 0)
    from ming_sim.memorials import create_memorial
    create_memorial(db, None, day=1, author_name=a, org="都察院", kind="弹章", urgency=3,
                    summary=f"{a}劾{b}", full_text="劾其植党。", ref_kind="character", ref_id=b)
    db.conn.commit()


class TriggerTests(unittest.TestCase):
    def test_deep_rivalry_triggers_feud_decision(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            a, b = _two_ming(db)
            _erupt(db, a, b)
            payload = court_events.evaluate_decisions(db, state, day)
            self.assertIsNotNone(payload, "深仇当触发宿敌互讦抉择")
            self.assertEqual(payload["id"], "rival_feud")
            self.assertGreaterEqual(len(payload["choices"]), 3)

    def test_payload_includes_structured_choice_effects(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            a, b = _two_ming(db)
            _erupt(db, a, b)
            payload = court_events.evaluate_decisions(db, state, day)
            both = next(c for c in payload["choices"] if c["key"] == "both")
            labels = [e["label"] for e in both["effects"]]
            tones = {e["label"]: e["tone"] for e in both["effects"]}
            self.assertIn("君威 +3", labels)
            self.assertIn("任事 -5", labels)
            self.assertEqual(tones["君威 +3"], "good")
            self.assertEqual(tones["任事 -5"], "bad")

    def test_one_pending_at_a_time(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            a, b = _two_ming(db)
            _erupt(db, a, b)
            first = court_events.evaluate_decisions(db, state, day)
            self.assertIsNotNone(first)
            second = court_events.evaluate_decisions(db, state, day)  # 已有待决
            self.assertIsNone(second, "一次至多一道待决")

    def test_high_grievance_petition_triggers_dilemma(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            petitioner, rival = _two_ming(db)
            db.conn.execute(
                "UPDATE characters SET emp_trust=24, grievance=84, faction='东林' WHERE name=?",
                (petitioner,),
            )
            db.conn.execute(
                "UPDATE characters SET faction='阉党' WHERE name=?",
                (rival,),
            )
            court._set_opinion(db, petitioner, rival, -78, "夺功旧怨", day)
            court._set_opinion(db, rival, petitioner, -72, "夺功旧怨", day)
            db.conn.commit()

            payload = court_events.evaluate_decisions(db, state, day)

            self.assertIsNotNone(payload)
            self.assertEqual(payload["id"], "imperial_petition")
            self.assertIn(petitioner, str(payload["title"]))
            keys = {str(ch["key"]) for ch in payload["choices"]}
            self.assertEqual(keys, {"protect", "demand_service", "co_work", "shelve"})
            protect = next(ch for ch in payload["choices"] if ch["key"] == "protect")
            labels = [str(e["label"]) for e in protect["effects"]]
            self.assertIn(f"{petitioner}信任 +10", labels)
            self.assertIn("东林满意 +4", labels)
            self.assertIn("阉党热度 +5", labels)


class ResolveTests(unittest.TestCase):
    def test_resolve_applies_effect_and_clears(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            a, b = _two_ming(db)
            _erupt(db, a, b)
            court_events.evaluate_decisions(db, state, day)
            shi_before = kv_int(db, KV_SHI, 55)
            res = court_events.resolve_decision(db, state, "both", day=day)
            self.assertTrue(res["ok"])
            self.assertIn("君威", res["effect"])  # both 选项含 shi+3
            labels = [e["label"] for e in res["effects"]]
            self.assertIn("君威 +3", labels)
            self.assertIn("任事 -5", labels)
            self.assertGreater(kv_int(db, KV_SHI, 55), shi_before, "各打五十大板立威，君威应升")
            self.assertIsNone(court_events.get_pending(db), "落子后待决应清空")

    def test_cooldown_blocks_immediate_refire(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            a, b = _two_ming(db)
            _erupt(db, a, b)
            court_events.evaluate_decisions(db, state, day)
            court_events.resolve_decision(db, state, "ignore", day=day)
            # 同一触发仍在（opinion 仍深），但冷却应拦住即刻重弹
            again = court_events.evaluate_decisions(db, state, day + 1)
            self.assertIsNone(again, "同类抉择 60 日内不应重复弹出")

    def test_resolve_without_pending_is_graceful(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            res = court_events.resolve_decision(db, state, "both", day=day)
            self.assertFalse(res["ok"])

    def test_petition_protection_changes_people_and_faction_heat(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            petitioner, rival = _two_ming(db)
            db.conn.execute(
                "UPDATE characters SET emp_trust=24, grievance=84, faction='东林' WHERE name=?",
                (petitioner,),
            )
            db.conn.execute(
                "UPDATE characters SET emp_trust=55, grievance=20, faction='阉党' WHERE name=?",
                (rival,),
            )
            court._set_opinion(db, petitioner, rival, -78, "夺功旧怨", day)
            court._set_opinion(db, rival, petitioner, -72, "夺功旧怨", day)
            db.conn.commit()
            heat_before = int(db.conn.execute(
                "SELECT heat FROM factions WHERE name='阉党'"
            ).fetchone()["heat"])

            court_events.evaluate_decisions(db, state, day)
            res = court_events.resolve_decision(db, state, "protect", day=day)

            self.assertTrue(res["ok"], res)
            prow = db.conn.execute(
                "SELECT emp_trust, grievance FROM characters WHERE name=?", (petitioner,)
            ).fetchone()
            rrow = db.conn.execute(
                "SELECT emp_trust, grievance FROM characters WHERE name=?", (rival,)
            ).fetchone()
            self.assertEqual(int(prow["emp_trust"]), 34)
            self.assertEqual(int(prow["grievance"]), 72)
            self.assertEqual(int(rrow["emp_trust"]), 52)
            self.assertEqual(int(rrow["grievance"]), 25)
            heat_after = int(db.conn.execute(
                "SELECT heat FROM factions WHERE name='阉党'"
            ).fetchone()["heat"])
            self.assertEqual(heat_after, heat_before + 5)
            labels = [str(e["label"]) for e in res["effects"]]
            self.assertIn("阉党热度 +5", labels)
            self.assertIsNone(court_events.get_pending(db))


class IntegrationTests(unittest.TestCase):
    def test_decision_red_event_halts_advance(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            a, b = _two_ming(db)
            _erupt(db, a, b, opinion=-80)
            result = timeflow.advance_days(db, state, 12, stop_on_yellow=False)
            evs = [e for r in result["reports"] for e in r["events"]]
            self.assertTrue(any(e["kind"] == "decision" for e in evs), "抉择应作为红事件出现")
            self.assertEqual(result["stopped_by"], "red", "待决抉择应令推进停下待裁断")
            self.assertIsNotNone(court_events.pending_payload(db))


if __name__ == "__main__":
    unittest.main()
