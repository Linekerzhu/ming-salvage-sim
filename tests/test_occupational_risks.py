import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ming_sim import lifecycle, occupational_risks, playstyle, timeflow
from ming_sim.conditions import public_condition_payload
from ming_sim.db import GameDB
from ming_sim.upgrade_schema import KV_CURRENT_DAY, kv_int


class ZeroRng:
    def random(self):
        return 0.0

    def uniform(self, low, high):
        return low

    def randint(self, low, high):
        return low

    def choice(self, seq):
        return seq[0]


def _fresh(tmp: str):
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    state = db.load_state()
    day = timeflow.ensure_active(db, state)
    return db, state, day


def _row_candidate(db: GameDB, name: str, **overrides):
    row = db.conn.execute(
        """
        SELECT name, office, office_type, faction, sex, birth_year, hp,
               ability, integrity, courage, force, wisdom, luck
        FROM characters
        WHERE name=?
        """,
        (name,),
    ).fetchone()
    candidate = {key: row[key] for key in row.keys()}
    candidate.update(
        {
            "source_kind": "directive",
            "source_id": "999",
            "task_text": "骑马赶赴辽东巡边，查验军务。",
            "category": "military",
            "risk_score": 95,
            "condition_count": 0,
            "condition_max": 0,
            "duty_count": 1,
        }
    )
    candidate.update(overrides)
    return candidate


def _risk_profile(*tags: str, pressure: int = 95):
    return {
        "actor": "",
        "risk_tags": list(tags),
        "pressure": pressure,
        "privacy": "public",
        "evidence_quote": "结构化风险画像",
        "confidence": 1.0,
        "decision_source": "llm",
    }


def _issue(db: GameDB, state, text: str, actor: str = "", risk_profile=None) -> int:
    cur = db.conn.execute(
        "INSERT INTO turn_directives (turn, year, period, text, source, status, actor, risk_profile_json)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (
            state.turn,
            state.year,
            state.period,
            text,
            "test",
            "confirmed",
            actor or None,
            json.dumps(risk_profile or {}, ensure_ascii=False),
        ),
    )
    db.conn.commit()
    did = int(cur.lastrowid)
    rows = db.conn.execute("SELECT * FROM turn_directives WHERE id=?", (did,)).fetchall()
    lifecycle.init_directive_lifecycles(db, state, rows, kv_int(db, KV_CURRENT_DAY, 1))
    return did


class OccupationalRiskTests(unittest.TestCase):
    def test_riding_fall_generates_medical_record_and_stalls_directive(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            did = _issue(db, state, "命郑芝龙骑马赶赴辽东巡边，查验军务。", "郑芝龙")
            db.conn.execute("UPDATE characters SET hp=35, force=10, luck=10 WHERE name='郑芝龙'")
            db.conn.commit()
            candidate = _row_candidate(db, "郑芝龙", source_id=str(did), hp=35, force=10, luck=10)

            event = occupational_risks.apply_occupational_risk_event(
                db, state, candidate, "riding_fall", day + 1, rng=ZeroRng()
            )

            self.assertEqual(event["event_key"], "riding_fall")
            payload = public_condition_payload(db, "郑芝龙")
            flat = [item for group in payload["groups"] for item in group["items"]]
            self.assertTrue(any("下肢瘫痪" in str(item.get("label")) for item in flat))
            directive = db.conn.execute(
                "SELECT lifecycle_status, anomaly FROM turn_directives WHERE id=?",
                (did,),
            ).fetchone()
            self.assertEqual(str(directive["lifecycle_status"]), "stalled")
            self.assertIn("occupational_risk", str(directive["anomaly"]))

    def test_desk_and_urinary_strain_are_chronic_medical_records(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            candidate = _row_candidate(
                db,
                "韩爌",
                task_text="连日票拟、核账册、清查户部钱粮。",
                source_kind="agreement_task",
                source_id="123",
                risk_score=78,
            )

            occupational_risks.apply_occupational_risk_event(db, state, candidate, "desk_strain", day, rng=ZeroRng())
            occupational_risks.apply_occupational_risk_event(db, state, candidate, "urinary_strain", day, rng=ZeroRng())

            rows = db.conn.execute(
                """
                SELECT label, system, chronic, effects_json
                FROM character_conditions
                WHERE name='韩爌' AND source_kind='occupational_risk'
                ORDER BY label
                """
            ).fetchall()
            labels = {str(row["label"]) for row in rows}
            self.assertIn("腰脊劳损", labels)
            self.assertIn("癃闭", labels)
            self.assertTrue(all(int(row["chronic"]) == 1 for row in rows))
            self.assertTrue(any(str(row["system"]) == "urinary" for row in rows))

    def test_stress_breakdown_generates_mental_condition(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            candidate = _row_candidate(
                db,
                "王承恩",
                task_text="限期查清诏狱旧案，若误事严加追责。",
                source_kind="secret_order",
                source_id="7",
                risk_score=92,
            )

            occupational_risks.apply_occupational_risk_event(db, state, candidate, "stress_breakdown", day, rng=ZeroRng())

            row = db.conn.execute(
                """
                SELECT label, system, stage, effects_json
                FROM character_conditions
                WHERE name='王承恩' AND condition_key='occupational:stress:breakdown'
                """
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(str(row["system"]), "mental")
            self.assertIn(str(row["stage"]), {"serious", "disabled"})
            self.assertIn("psychological", str(row["effects_json"]))

    def test_secret_order_risk_extends_due_turn_and_sim_note(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            order_id = db.create_secret_order(
                state,
                minister_name="王承恩",
                title="密查诏狱",
                content="限期查清诏狱旧案，若误事严加追责。",
                tags=["诏狱", "追责"],
                importance=5,
                deadline_months=1,
            )
            before = db.conn.execute(
                "SELECT due_turn FROM secret_orders WHERE id=?",
                (order_id,),
            ).fetchone()
            candidate = _row_candidate(
                db,
                "王承恩",
                task_text="限期查清诏狱旧案，若误事严加追责。",
                source_kind="secret_order",
                source_id=str(order_id),
                risk_score=92,
            )

            occupational_risks.apply_occupational_risk_event(db, state, candidate, "stress_breakdown", day, rng=ZeroRng())

            after = db.conn.execute(
                "SELECT due_turn, sim_note FROM secret_orders WHERE id=?",
                (order_id,),
            ).fetchone()
            self.assertEqual(int(after["due_turn"]), max(int(before["due_turn"]), int(state.turn)) + 1)
            self.assertIn("病中承办", str(after["sim_note"]))

    def test_apply_score_extraction_persists_task_risk_profiles(self):
        from ming_sim.content import GameContent
        from ming_sim.issues import apply_score_extraction, bind_content

        with TemporaryDirectory() as tmp:
            bind_content(GameContent.load())
            db, state, _ = _fresh(tmp)
            directive_id = db.add_directive(state, None, "命郑芝龙巡边辽东。", "test", actor="郑芝龙")
            order_id = db.create_secret_order(
                state,
                minister_name="王承恩",
                title="密查诏狱",
                content="限期查清诏狱旧案。",
                tags=["诏狱"],
            )
            agreement_id = db.create_negotiation_agreement(
                state,
                minister_name="韩爌",
                topic="核账册",
                action_kind="court_commitment",
                status="pending",
                stance_id=0,
                handshake_status="sealed",
                psychological_score=100,
                threshold=70,
                verbal_only=False,
                tasks=["连日核验户部账册"],
            )
            task_id = int(db.conn.execute(
                "SELECT id FROM negotiation_tasks WHERE agreement_id=?",
                (agreement_id,),
            ).fetchone()["id"])

            result = apply_score_extraction(db, state, {
                "task_risk_profiles": [
                    {"table": "turn_directives", "source_id": directive_id, "risk_tags": ["mounted_military"], "confidence": 1.0},
                    {"table": "secret_orders", "source_id": order_id, "risk_tags": ["high_pressure_investigation"], "confidence": 0.8},
                    {"table": "negotiation_tasks", "task_id": task_id, "risk_tags": ["desk_bureaucratic"], "confidence": 0.9},
                ],
            })

            self.assertEqual(len(result["task_risk_profiles"]), 3)
            directive_profile = json.loads(db.conn.execute(
                "SELECT risk_profile_json FROM turn_directives WHERE id=?",
                (directive_id,),
            ).fetchone()["risk_profile_json"])
            order_profile = json.loads(db.conn.execute(
                "SELECT risk_profile_json FROM secret_orders WHERE id=?",
                (order_id,),
            ).fetchone()["risk_profile_json"])
            task_profile = json.loads(db.conn.execute(
                "SELECT risk_profile_json FROM negotiation_tasks WHERE id=?",
                (task_id,),
            ).fetchone()["risk_profile_json"])
            self.assertEqual(directive_profile["risk_tags"], ["mounted_military"])
            self.assertEqual(order_profile["risk_tags"], ["high_pressure_investigation"])
            self.assertEqual(task_profile["risk_tags"], ["desk_bureaucratic"])

    def test_apply_score_extraction_defaults_task_risk_profile_to_current_directive(self):
        from ming_sim.content import GameContent
        from ming_sim.issues import apply_score_extraction, bind_content

        with TemporaryDirectory() as tmp:
            bind_content(GameContent.load())
            db, state, _ = _fresh(tmp)
            directive_id = db.add_directive(state, None, "命韩爌连日核验户部账册。", "test", actor="韩爌")

            result = apply_score_extraction(db, state, {
                "_directive_id": directive_id,
                "task_risk_profiles": [
                    {"risk_tags": ["desk_bureaucratic"], "confidence": 0.7, "evidence_quote": "连日核验户部账册"}
                ],
            })

            self.assertEqual(len(result["task_risk_profiles"]), 1)
            profile = json.loads(db.conn.execute(
                "SELECT risk_profile_json FROM turn_directives WHERE id=?",
                (directive_id,),
            ).fetchone()["risk_profile_json"])
            self.assertEqual(profile["risk_tags"], ["desk_bureaucratic"])
            self.assertEqual(profile["evidence_quote"], "连日核验户部账册")

    def test_debauchery_stroke_kills_and_creates_private_secret(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            db.conn.execute("UPDATE characters SET integrity=20, hp=60 WHERE name='魏忠贤'")
            db.conn.commit()
            candidate = _row_candidate(
                db,
                "魏忠贤",
                source_kind="office",
                source_id="魏忠贤",
                task_text="钱粮请托、私第夜宴风闻甚密。",
                integrity=20,
                hp=60,
                risk_score=99,
            )

            event = occupational_risks.apply_occupational_risk_event(
                db, state, candidate, "debauchery_stroke", day, rng=ZeroRng()
            )

            status = db.get_character_status("魏忠贤")
            self.assertEqual(status[0], "dead")
            self.assertEqual(event["level"], "red")
            self.assertNotIn("嫖", event["detail"])
            secret = db.conn.execute(
                "SELECT kind, detail, known_to_crown FROM secrets WHERE holder='魏忠贤' AND kind='私德' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            self.assertIsNotNone(secret)
            self.assertEqual(int(secret["known_to_crown"]), 1)

    def test_private_morality_secret_does_not_create_condition_or_hp_loss(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            hp_before = int(db.conn.execute("SELECT hp FROM characters WHERE name='韩爌'").fetchone()["hp"])
            candidate = _row_candidate(
                db,
                "韩爌",
                source_kind="office",
                source_id="韩爌",
                task_text="私情违礼风闻。",
                risk_score=75,
            )

            event = occupational_risks.apply_occupational_risk_event(
                db, state, candidate, "private_morality_secret", day, rng=ZeroRng()
            )

            self.assertEqual(event["event_key"], "private_morality_secret")
            hp_after = int(db.conn.execute("SELECT hp FROM characters WHERE name='韩爌'").fetchone()["hp"])
            self.assertEqual(hp_after, hp_before)
            count = db.conn.execute(
                "SELECT COUNT(*) AS n FROM character_conditions WHERE name='韩爌' AND source_kind='occupational_risk'"
            ).fetchone()["n"]
            self.assertEqual(int(count), 0)
            secret = db.conn.execute(
                "SELECT kind, detail FROM secrets WHERE holder='韩爌' AND kind='私情' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            self.assertIsNotNone(secret)

    def test_tick_budget_prevents_same_npc_repeat_in_month(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            _issue(
                db,
                state,
                "命郑芝龙骑马赶赴辽东巡边，查验军务。",
                "郑芝龙",
                _risk_profile("mounted_military"),
            )
            db.conn.execute("UPDATE characters SET hp=35, force=10, luck=10 WHERE name='郑芝龙'")
            db.conn.commit()

            first = occupational_risks.occupational_risk_tick(db, state, day + 1, rng=ZeroRng())
            second = occupational_risks.occupational_risk_tick(db, state, day + 2, rng=ZeroRng())

            self.assertEqual(len(first), 1)
            self.assertEqual(second, [])

    def test_tick_ignores_text_without_llm_risk_profile(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            _issue(db, state, "命郑芝龙骑马赶赴辽东巡边，查验军务。", "郑芝龙")
            db.conn.execute("UPDATE characters SET hp=35, force=10, luck=10 WHERE name='郑芝龙'")
            db.conn.commit()

            events = occupational_risks.occupational_risk_tick(db, state, day + 1, rng=ZeroRng())

            self.assertEqual(events, [])

    def test_advance_days_returns_event_and_month_log_records_it(self):
        with TemporaryDirectory() as tmp:
            db, state, _ = _fresh(tmp)
            _issue(
                db,
                state,
                "命郑芝龙骑马赶赴辽东巡边，查验军务。",
                "郑芝龙",
                _risk_profile("mounted_military"),
            )
            db.conn.execute("UPDATE characters SET hp=35, force=10, luck=10 WHERE name='郑芝龙'")
            db.conn.commit()

            with patch("ming_sim.occupational_risks.random.Random", return_value=ZeroRng()):
                result = timeflow.advance_days(db, state, 1, stop_on_yellow=False)

            events = [ev for report in result["reports"] for ev in report["events"]]
            self.assertTrue(any(ev["kind"] == "occupational_risk" for ev in events))
            month_events = timeflow.month_event_log(db)
            self.assertTrue(any(ev.get("kind") == "occupational_risk" for ev in month_events))

    def test_playstyle_brief_contains_recent_health_risk_card(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            candidate = _row_candidate(db, "韩爌", risk_score=80)
            occupational_risks.apply_occupational_risk_event(db, state, candidate, "desk_strain", day, rng=ZeroRng())

            brief = playstyle.briefing_payload(db, state, limit=8, kind="health_risk")

            cards = brief.get("cards") or []
            self.assertTrue(any(card.get("kind") == "health_risk" and card.get("actor") == "韩爌" for card in cards))


if __name__ == "__main__":
    unittest.main()
