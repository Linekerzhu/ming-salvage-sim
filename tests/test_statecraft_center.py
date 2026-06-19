import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim import timeflow
from ming_sim.bureaucracy import organization_diagnostics
from ming_sim.db import GameDB
from ming_sim.fiscal_center import fiscal_center_payload
from ming_sim.statecraft_center import statecraft_center_payload


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


if __name__ == "__main__":
    unittest.main()
