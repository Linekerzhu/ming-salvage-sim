"""王朝长河（CK3 化 P3）测试：卒年触发病逝、好感哀荣涟漪、要缺继任抉择。零 LLM。"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim import court, court_events, lifespan, timeflow
from ming_sim.db import GameDB


def _fresh(tmp: str):
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    state = db.load_state()
    timeflow.ensure_active(db, state)
    return db, state


def _an_active(db, key_office=True):
    like = "AND office LIKE '%大学士%'" if key_office else ""
    row = db.conn.execute(
        f"SELECT name, office, birth_year FROM characters "
        f"WHERE status='active' AND power_id='ming' AND office_type!='后宫' {like} LIMIT 1").fetchone()
    return row


class MortalityTests(unittest.TestCase):
    def test_historical_death_year_triggers_death(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            row = _an_active(db, key_office=False)
            name = row["name"]
            # 把卒年设到当前年月之前 → 本月初必殁
            db.conn.execute("UPDATE characters SET historical_death_year=?, historical_death_month=1 WHERE name=?",
                            (state.year, name))
            db.conn.commit()
            evs = lifespan.mortality_tick(db, state, day=state.turn * 30 + 1)
            self.assertTrue(any(e["kind"] == "obituary" and name in e["title"] for e in evs))
            st = db.conn.execute("SELECT status, office FROM characters WHERE name=?", (name,)).fetchone()
            self.assertEqual(st["status"], "dead")
            self.assertEqual(st["office"], "", "殁则削职")

    def test_young_healthy_survives(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            # 把所有人设为年轻且无卒年 → 本月不应有人病逝
            db.conn.execute("UPDATE characters SET birth_year=?, historical_death_year=0 WHERE power_id='ming'",
                            (state.year - 30,))
            db.conn.commit()
            evs = lifespan.mortality_tick(db, state, day=state.turn * 30 + 1)
            self.assertEqual(evs, [])

    def test_key_office_death_queues_vacancy(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            row = _an_active(db, key_office=True)
            self.assertIsNotNone(row, "应有大学士在朝")
            name = row["name"]
            db.conn.execute("UPDATE characters SET historical_death_year=?, historical_death_month=1 WHERE name=?",
                            (state.year, name))
            db.conn.commit()
            lifespan.mortality_tick(db, state, day=state.turn * 30 + 1)
            vs = lifespan.vacancies(db)
            self.assertTrue(any(v["deceased"] == name for v in vs), "要职病逝应入继任队列")


class SuccessionTests(unittest.TestCase):
    def test_vacancy_triggers_succession_decision(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            row = _an_active(db, key_office=True)
            name, office = row["name"], row["office"]
            db.conn.execute("UPDATE characters SET historical_death_year=?, historical_death_month=1 WHERE name=?",
                            (state.year, name))
            db.conn.commit()
            day = state.turn * 30 + 1
            lifespan.mortality_tick(db, state, day)
            payload = court_events.evaluate_decisions(db, state, day)
            self.assertIsNotNone(payload, "出缺应触发继任抉择")
            self.assertEqual(payload["id"], "succession")
            self.assertGreaterEqual(len(payload["choices"]), 2)

    def test_resolving_succession_appoints_candidate(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            row = _an_active(db, key_office=True)
            name, office = row["name"], row["office"]
            db.conn.execute("UPDATE characters SET historical_death_year=?, historical_death_month=1 WHERE name=?",
                            (state.year, name))
            db.conn.commit()
            day = state.turn * 30 + 1
            lifespan.mortality_tick(db, state, day)
            payload = court_events.evaluate_decisions(db, state, day)
            choice = payload["choices"][0]
            appointee = choice["key"].split("pick:", 1)[1]
            res = court_events.resolve_decision(db, state, choice["key"], day=day)
            self.assertTrue(res["ok"])
            got = db.conn.execute("SELECT office FROM characters WHERE name=?", (appointee,)).fetchone()
            self.assertEqual(got["office"], office, "受简者应补得遗缺")


if __name__ == "__main__":
    unittest.main()
