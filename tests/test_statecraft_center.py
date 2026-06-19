import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim import timeflow
from ming_sim.bureaucracy import organization_diagnostics
from ming_sim.db import GameDB
from ming_sim.fiscal_center import fiscal_center_payload
from ming_sim.lifecycle import init_directive_lifecycles, lifecycle_payload
from ming_sim.statecraft_center import directive_statecraft_execution_effect, statecraft_center_payload


def _fresh(tmp: str):
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    state = db.load_state()
    timeflow.ensure_active(db, state)
    return db, state


class StatecraftCenterTests(unittest.TestCase):
    def test_statecraft_center_reuses_fiscal_and_bureaucracy_sources(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            fiscal = fiscal_center_payload(db, state)
            organization = organization_diagnostics(db)
            payload = statecraft_center_payload(db, state, fiscal=fiscal, organization=organization)

            topbar = {row["key"]: row for row in payload["topbar"]}
            self.assertEqual(topbar["treasury"]["value"], int(state.metrics["国库"]))
            self.assertEqual(topbar["privy"]["value"], int(state.metrics["内库"]))
            self.assertEqual(
                topbar["treasury_net"]["value"],
                fiscal["net_by_account"]["国库"]["net"],
            )
            self.assertEqual(
                topbar["court_readiness"]["value"],
                organization["court_readiness"],
            )

            lanes = {row["id"]: row for row in payload["economy_lanes"]}
            self.assertEqual(
                lanes["state_revenue"]["value"],
                fiscal["totals"]["province_dynamic_tax"],
            )
            self.assertIn("FiscalCenter", payload["model"]["do_not_duplicate"])

    def test_statecraft_capacity_rows_expose_bureaucratic_bottlenecks(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            payload = statecraft_center_payload(db, state)

            domains = {row["domain"] for row in payload["capacity_rows"]}
            for expected in ("fiscal", "military", "construction", "local", "procedure"):
                self.assertIn(expected, domains)
            self.assertTrue(payload["bureaucracy_rows"])
            self.assertTrue(payload["building_capacity_rows"])
            self.assertTrue(all("title" in row and "detail" in row for row in payload["bottlenecks"]))

    def test_statecraft_maps_active_directives_to_bureaucracy_lanes(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            cur = db.conn.execute(
                """
                INSERT INTO turn_directives
                (turn, year, period, actor, text, source, status)
                VALUES (?, ?, ?, ?, ?, 'unit-test', 'issued')
                """,
                (
                    state.turn,
                    state.year,
                    state.period,
                    "毕自严",
                    "命户部清查辽饷积欠并拨银补发边军军饷",
                ),
            )
            did = int(cur.lastrowid)
            row = db.conn.execute("SELECT * FROM turn_directives WHERE id=?", (did,)).fetchone()
            init_directive_lifecycles(db, state, [row], day=1)

            payload = statecraft_center_payload(db, state)
            queue = {int(row["id"]): row for row in payload["directive_queue_rows"]}
            self.assertIn(did, queue)
            self.assertIn("fiscal", queue[did]["domains"])
            self.assertIn("military", queue[did]["domains"])
            self.assertGreater(queue[did]["capacity_score"], 0)

            lanes = {row["domain"]: row for row in payload["bureaucracy_lanes"]}
            self.assertGreater(lanes["fiscal"]["active_count"], 0)
            self.assertGreater(lanes["military"]["active_count"], 0)
            self.assertTrue(any(item["id"] == did for item in lanes["fiscal"]["active_directives"]))
            self.assertTrue(any(item["id"] == did for item in lanes["military"]["active_directives"]))

    def test_lifecycle_payload_includes_directive_statecraft_preflight(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            db.conn.execute(
                """
                INSERT INTO turn_directives
                (turn, year, period, actor, text, source, status)
                VALUES (?, ?, ?, ?, ?, 'unit-test', 'issued')
                """,
                (
                    state.turn,
                    state.year,
                    state.period,
                    "毕自严",
                    "命户部清查辽饷积欠并拨银补发边军军饷",
                ),
            )
            row = db.conn.execute("SELECT * FROM turn_directives ORDER BY id DESC LIMIT 1").fetchone()
            init_directive_lifecycles(db, state, [row], day=1)

            payload = lifecycle_payload(db, include_done=False, limit=5)
            self.assertEqual(len(payload), 1)
            preflight = payload[0]["statecraft_preflight"]
            self.assertIn("fiscal", preflight["domains"])
            self.assertIn("military", preflight["domains"])
            self.assertGreater(preflight["score"], 0)
            self.assertTrue(preflight["capacity_rows"])

    def test_statecraft_execution_effect_turns_capacity_into_lifecycle_modifiers(self):
        statecraft = {
            "topbar": [{"key": "court_readiness", "value": 38}],
            "capacity_rows": [
                {"domain": "fiscal", "label": "财政署理", "score": 35, "status": "断裂", "tone": "danger"},
                {"domain": "military", "label": "军政后勤", "score": 40, "status": "断裂", "tone": "danger"},
            ],
            "bottlenecks": [
                {"kind": "cash_gap", "title": "下月现金缺口 100 万两", "tone": "danger"},
                {"kind": "army_arrears", "title": "军饷欠发 50 万两", "tone": "danger"},
            ],
        }

        effect = directive_statecraft_execution_effect("拨银补发边军欠饷", statecraft)

        self.assertGreater(effect["exec_factor"], 1.2)
        self.assertLess(effect["score_bonus"], 0)
        self.assertGreater(effect["resistance_delta"], 0)
        self.assertGreaterEqual(effect["check_risk_delta"]["delay"], 20)
        self.assertGreaterEqual(effect["check_risk_delta"]["skim"], 8)
        self.assertGreaterEqual(effect["check_risk_delta"]["surprise"], 8)
        self.assertTrue(any("国家机器" in note for note in effect["notes"]))


if __name__ == "__main__":
    unittest.main()
