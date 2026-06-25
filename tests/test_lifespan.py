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

    def test_extreme_old_age_can_generate_natural_disease(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            row = _an_active(db, key_office=False)
            name = row["name"]
            db.conn.execute(
                "UPDATE characters SET birth_year=?, historical_death_year=0, hp=100 WHERE name=?",
                (state.year - 100, name),
            )
            db.conn.commit()
            evs = lifespan.morbidity_tick(db, state, day=state.turn * 30 + 1)
            self.assertTrue(any(e["kind"] == "morbidity" and name in e["title"] for e in evs))
            condition = db.conn.execute(
                """
                SELECT kind, severity, source_kind, source_id
                FROM character_conditions
                WHERE name=? AND kind='disease'
                """,
                (name,),
            ).fetchone()
            self.assertIsNotNone(condition)
            self.assertEqual(condition["source_kind"], "lifespan")
            self.assertEqual(condition["source_id"], "natural-aging")
            self.assertGreaterEqual(int(condition["severity"]), 4)
            hp = db.conn.execute("SELECT hp FROM characters WHERE name=?", (name,)).fetchone()["hp"]
            self.assertLess(int(hp), 100)

    def test_young_healthy_has_no_natural_disease_tick(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            db.conn.execute(
                """
                UPDATE characters
                SET birth_year=?, historical_death_year=0, hp=100
                WHERE power_id='ming' AND status='active'
                """,
                (state.year - 30,),
            )
            db.conn.commit()
            evs = lifespan.morbidity_tick(db, state, day=state.turn * 30 + 1)
            self.assertEqual(evs, [])
            count = db.conn.execute(
                "SELECT COUNT(*) AS n FROM character_conditions WHERE kind='disease'"
            ).fetchone()["n"]
            self.assertEqual(int(count), 0)

    def test_natural_disease_catalog_covers_body_systems_and_strange_diseases(self):
        systems = set(lifespan.disease_catalog_systems())
        for system in {
            "general",
            "speech",
            "nervous",
            "mental",
            "respiratory",
            "circulatory",
            "digestive",
            "urinary",
            "reproductive",
            "musculoskeletal",
            "skin",
        }:
            self.assertIn(system, systems)
        self.assertTrue(any(item.get("weird") for item in lifespan._RARE_STRANGE_DISEASES))

    def test_strange_disease_branch_can_be_selected(self):
        class WeirdRng:
            def random(self):
                return 0.0

            def choice(self, seq):
                return seq[0]

        disease = lifespan._pick_natural_disease(WeirdRng(), age=80, hp=20)
        self.assertTrue(disease["weird"])
        self.assertIn(disease["system"], lifespan.disease_catalog_systems())

    def test_extreme_old_age_strange_disease_persists_note_and_speech_effect(self):
        original = lifespan._pick_natural_disease
        try:
            lifespan._pick_natural_disease = lambda rng, *, age, hp: {
                "key": "huo_huo",
                "label": "狐惑",
                "system": "general",
                "effect": "口咽与阴部溃痛，神思烦乱，奏对难以持久",
                "speech": "口咽溃痛，发声艰涩",
                "weird": True,
            }
            with TemporaryDirectory() as tmp:
                db, state = _fresh(tmp)
                row = _an_active(db, key_office=False)
                name = row["name"]
                db.conn.execute(
                    "UPDATE characters SET birth_year=?, historical_death_year=0, hp=100 WHERE name=?",
                    (state.year - 100, name),
                )
                db.conn.commit()

                evs = lifespan.morbidity_tick(db, state, day=state.turn * 30 + 1)

                self.assertTrue(any(e["kind"] == "morbidity" and "奇疾" in e["title"] for e in evs))
                row = db.conn.execute(
                    "SELECT label, note, effects_json FROM character_conditions WHERE name=? AND condition_key='natural:huo_huo'",
                    (name,),
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(str(row["label"]), "狐惑")
                self.assertIn("奇疾", str(row["note"]))
                self.assertIn("发声艰涩", str(row["effects_json"]))
        finally:
            lifespan._pick_natural_disease = original

    def test_acute_wind_cold_records_next_check_in_medical_record(self):
        from ming_sim.conditions import public_condition_payload

        original = lifespan._pick_natural_disease
        try:
            lifespan._pick_natural_disease = lambda rng, *, age, hp: {
                "key": "wind_cold",
                "label": "风寒",
                "system": "respiratory",
                "effect": "恶寒发热，咳嗽鼻塞，奏对时气短声哑",
                "speech": "风寒咳嗽，句子宜短",
                "course_kind": "acute",
                "duration_days": 5,
                "possible_outcomes": ["恢复", "加重"],
                "recovery_chance": 0.62,
            }
            with TemporaryDirectory() as tmp:
                db, state = _fresh(tmp)
                row = _an_active(db, key_office=False)
                name = row["name"]
                db.conn.execute(
                    "UPDATE characters SET birth_year=?, historical_death_year=0, hp=100 WHERE name=?",
                    (state.year - 100, name),
                )
                db.conn.commit()
                day = state.turn * 30 + 1

                lifespan.morbidity_tick(db, state, day=day)

                condition = db.conn.execute(
                    "SELECT duration_days, effects_json FROM character_conditions WHERE name=? AND condition_key='natural:wind_cold'",
                    (name,),
                ).fetchone()
                self.assertIsNotNone(condition)
                self.assertEqual(int(condition["duration_days"]), 5)
                self.assertIn('"next_check_day"', str(condition["effects_json"]))
                payload = public_condition_payload(db, name)
                labels = " ".join(
                    str(item.get("course_label") or "")
                    for group in payload["groups"]
                    for item in group.get("items", [])
                )
                self.assertIn("5天后复诊/判定", labels)
                self.assertIn("可能恢复或加重", labels)
        finally:
            lifespan._pick_natural_disease = original

    def test_acute_disease_due_check_can_recover(self):
        from ming_sim.conditions import add_condition

        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            row = _an_active(db, key_office=False)
            name = row["name"]
            day = state.turn * 30 + 1
            db.conn.execute(
                "UPDATE characters SET birth_year=?, historical_death_year=0, hp=90 WHERE name=?",
                (state.year - 40, name),
            )
            db.conn.commit()
            add_condition(
                db,
                state,
                name,
                kind="disease",
                system="respiratory",
                condition_key="natural:wind_cold",
                label="风寒",
                severity=2,
                stage="active",
                note="偶感风寒",
                effects={
                    "course_kind": "acute",
                    "next_check_day": day,
                    "possible_outcomes": ["恢复", "加重"],
                    "recovery_chance": 1.0,
                    "ability_delta": "恶寒咳嗽",
                },
                duration_days=5,
                source_kind="lifespan",
                source_id="natural-aging",
            )

            evs = lifespan.disease_progression_tick(db, state, day=day)

            self.assertTrue(any(e["kind"] == "disease_recovery" and name in e["title"] for e in evs))
            condition = db.conn.execute(
                "SELECT severity, stage, effects_json FROM character_conditions WHERE name=? AND condition_key='natural:wind_cold'",
                (name,),
            ).fetchone()
            self.assertEqual(str(condition["stage"]), "recovering")
            self.assertLessEqual(int(condition["severity"]), 1)
            self.assertNotIn("next_check_day", str(condition["effects_json"]))

    def test_existing_disease_progresses_to_terminal(self):
        from ming_sim.conditions import add_condition

        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            row = _an_active(db, key_office=False)
            name = row["name"]
            db.conn.execute(
                "UPDATE characters SET birth_year=?, historical_death_year=0, hp=30 WHERE name=?",
                (state.year - 96, name),
            )
            db.conn.commit()
            add_condition(
                db,
                state,
                name,
                kind="disease",
                system="circulatory",
                condition_key="natural:heart_palpitations",
                label="心悸",
                severity=4,
                stage="serious",
                note="年高旧疾",
                source_kind="lifespan",
                source_id="natural-aging",
                chronic=True,
            )

            evs = lifespan.disease_progression_tick(db, state, day=state.turn * 30 + 1)

            self.assertTrue(any(e["kind"] == "disease_progression" and name in e["title"] for e in evs))
            condition = db.conn.execute(
                "SELECT kind, severity, stage FROM character_conditions WHERE name=? AND condition_key='natural:heart_palpitations'",
                (name,),
            ).fetchone()
            self.assertEqual(str(condition["kind"]), "terminal")
            self.assertEqual(int(condition["severity"]), 5)
            self.assertEqual(str(condition["stage"]), "critical")
            hp = int(db.conn.execute("SELECT hp FROM characters WHERE name=?", (name,)).fetchone()["hp"])
            self.assertEqual(hp, 1)

    def test_terminal_disease_can_kill_and_queue_vacancy(self):
        from ming_sim.conditions import add_condition

        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            row = _an_active(db, key_office=True)
            name = row["name"]
            db.conn.execute(
                "UPDATE characters SET birth_year=?, historical_death_year=0, hp=1 WHERE name=?",
                (state.year - 96, name),
            )
            db.conn.commit()
            add_condition(
                db,
                state,
                name,
                kind="terminal",
                system="respiratory",
                condition_key="natural:cough_asthma",
                label="咳喘",
                severity=5,
                stage="critical",
                note="病入危笃",
                source_kind="lifespan",
                source_id="natural-aging",
                chronic=True,
            )

            evs = lifespan.disease_progression_tick(db, state, day=state.turn * 30 + 1)

            self.assertTrue(any(e["kind"] == "disease_death" and name in e["title"] for e in evs))
            status = db.conn.execute("SELECT status, office FROM characters WHERE name=?", (name,)).fetchone()
            self.assertEqual(str(status["status"]), "dead")
            self.assertEqual(str(status["office"]), "")
            self.assertTrue(any(v["deceased"] == name for v in lifespan.vacancies(db)))


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
