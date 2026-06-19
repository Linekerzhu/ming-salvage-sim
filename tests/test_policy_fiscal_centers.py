import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim import policies, timeflow
from ming_sim.db import GameDB
from ming_sim.fiscal_center import fiscal_center_payload
from ming_sim.flows import compute_budget_lines
from ming_sim.memorials import create_memorial
from ming_sim.policy_center import policy_center_payload


def _fresh(tmp: str):
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    state = db.load_state()
    day = timeflow.ensure_active(db, state)
    return db, state, day


class FiscalCenterPayloadTests(unittest.TestCase):
    def test_fiscal_center_reuses_budget_source_of_truth(self):
        with TemporaryDirectory() as tmp:
            db, state, _day = _fresh(tmp)
            budget = compute_budget_lines(db, state)
            payload = fiscal_center_payload(db, state)

            for account in ("国库", "内库"):
                expected_income = sum(int(line["amount"]) for line in budget[account]["income"])
                expected_expense = sum(int(line["amount"]) for line in budget[account]["expense"])
                self.assertEqual(payload["net_by_account"][account]["income_total"], expected_income)
                self.assertEqual(payload["net_by_account"][account]["expense_total"], expected_expense)
                self.assertEqual(
                    payload["net_by_account"][account]["net"],
                    expected_income - expected_expense,
                )

            dynamic_tax = next(
                line["amount"]
                for line in budget["国库"]["income"]
                if line["name"] == "田赋辽饷盐商"
            )
            self.assertEqual(
                sum(int(row["province_total"]) for row in payload["province_tax_rows"]),
                int(dynamic_tax),
            )
            province_tax_sources = [
                row for row in payload["revenue_family_rows"]
                if row["family"] == "province_tax"
            ]
            self.assertEqual(
                {row["name"] for row in province_tax_sources},
                {"田赋", "辽饷", "盐税", "商税"},
            )
            self.assertEqual(
                sum(int(row["amount"]) for row in province_tax_sources),
                int(dynamic_tax),
            )
            self.assertTrue(all("base_amount" in row for row in province_tax_sources))
            self.assertTrue(
                any(row["name"] == "各军军饷" and row["family"] == "army_pay"
                    for row in payload["expense_family_rows"])
            )
            self.assertEqual(
                {row["id"] for row in payload["money_questions"]},
                {"make_money", "spend_money", "balance_change"},
            )
            self.assertIn("monthly_budget", payload["player_model"])
            self.assertIn("ledger_summary", payload)
            sample = payload["province_tax_rows"][0]
            for key in ("tax_base", "田赋基数", "辽饷基数", "盐税基数", "商税基数", "efficiency", "corruption"):
                self.assertIn(key, sample)

    def test_dynamic_tax_adjustments_change_region_fiscal_and_payload(self):
        with TemporaryDirectory() as tmp:
            db, state, _day = _fresh(tmp)
            before = fiscal_center_payload(db, state)
            before_liao_base = sum(int(row["辽饷基数"]) for row in before["province_tax_rows"])
            before_liao_income = sum(int(row["辽饷"]) for row in before["province_tax_rows"])

            touched = db.apply_dynamic_fiscal_scale("辽饷", 0.5)
            self.assertGreater(touched, 0)
            after = fiscal_center_payload(db, state)
            after_liao_base = sum(int(row["辽饷基数"]) for row in after["province_tax_rows"])
            after_liao_income = sum(int(row["辽饷"]) for row in after["province_tax_rows"])
            self.assertLess(after_liao_base, before_liao_base)
            self.assertLess(after_liao_income, before_liao_income)

    def test_tian_fu_scale_changes_tax_per_turn_residual(self):
        with TemporaryDirectory() as tmp:
            db, state, _day = _fresh(tmp)
            before = fiscal_center_payload(db, state)
            before_base = sum(int(row["田赋基数"]) for row in before["province_tax_rows"])

            touched = db.scale_tian_fu(0.5)
            self.assertGreater(touched, 0)
            after = fiscal_center_payload(db, state)
            after_base = sum(int(row["田赋基数"]) for row in after["province_tax_rows"])
            self.assertLess(after_base, before_base)

    def test_ledger_summary_explains_actual_balance_changes(self):
        with TemporaryDirectory() as tmp:
            db, state, _day = _fresh(tmp)
            opening = fiscal_center_payload(db, state)
            summarized = (
                opening["ledger_summary"]["largest_incomes"]
                + opening["ledger_summary"]["largest_expenses"]
            )
            self.assertFalse(any(row["category"] == "期初" for row in summarized))

            moved = db.record_issue_economy_move(
                state,
                "国库",
                -7,
                "测试支出",
                "测试修渠支出",
                apply_legacy=False,
            )
            self.assertEqual(moved, -7)
            payload = fiscal_center_payload(db, state)
            self.assertGreaterEqual(payload["ledger_summary"]["expense_total"], 7)
            self.assertTrue(
                any(row["reason"] == "测试修渠支出" for row in payload["ledger_summary"]["largest_expenses"])
            )


class PolicyCenterPayloadTests(unittest.TestCase):
    def test_policy_center_classifies_routes_and_collects_workstreams(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            policies.ensure_doctrine_legacy(db, state, "western_learning_pragmatism")
            created = policies.ensure_doctrine_issue(db, state, "open_sea_trade", delta_bar=8)
            issue_id = int(created["issue_id"])
            create_memorial(
                db,
                state,
                day=day,
                author_name="测试御史",
                org="都察院",
                kind="弹章",
                urgency=3,
                summary="请廷议开海裕国一事",
                ref_kind="issue",
                ref_id=str(issue_id),
            )

            fiscal = fiscal_center_payload(db, state)
            payload = policy_center_payload(db, state, fiscal=fiscal)

            self.assertTrue(any(row["id"] == "western_learning_pragmatism" for row in payload["orthodox"]))
            self.assertTrue(any(row["id"] == "open_sea_trade" for row in payload["contested"]))
            self.assertGreater(len(payload["latent"]), 0)
            self.assertEqual(payload["strategic_snapshot"]["fiscal"]["province_dynamic_tax"], fiscal["totals"]["province_dynamic_tax"])
            self.assertIn("territory", payload["strategic_snapshot"])
            self.assertIn("army", payload["strategic_snapshot"])
            self.assertTrue(
                any(row["doctrine_id"] == "open_sea_trade" for row in payload["workstreams"]["memorials"])
            )


if __name__ == "__main__":
    unittest.main()
