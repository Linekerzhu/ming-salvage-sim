"""P1.3 办差功过册 + 赏罚兑现测试。

- 功过册聚合：成/半/败/截留/逾期/功过分
- 赏罚兑现三档：奖（记功/加俸/超擢）、罚（申饬/罚俸/降黜）
- 赏罚历史落库

零 LLM。
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim import assignment, timeflow
from ming_sim.db import GameDB
from ming_sim.upgrade_schema import KV_CURRENT_DAY, KV_SHI, kv_int


def _fresh(tmp: str):
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    state = db.load_state()
    timeflow.ensure_active(db, state)
    assignment.ensure_assignment_schema(db)
    assignment.ensure_merit_schema(db)
    return db, state


def _settle(db, did, *, status, actual):
    """手动把一道差使结案（测试用）。"""
    db.conn.execute(
        "UPDATE turn_directives SET lifecycle_status=?, integrity_actual=? WHERE id=?",
        (status, int(actual), int(did)),
    )
    db.conn.commit()


class MeritLedgerTests(unittest.TestCase):
    def test_ledger_aggregates_grades_and_skim(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = kv_int(db, KV_CURRENT_DAY, 1)
            d1 = assignment.issue_assignment(db, state, kind="edict", text="甲", actor="毕自严", day=day)["id"]
            d2 = assignment.issue_assignment(db, state, kind="edict", text="乙", actor="毕自严", day=day)["id"]
            d3 = assignment.issue_assignment(db, state, kind="edict", text="丙", actor="毕自严", day=day)["id"]
            _settle(db, d1, status="done", actual=95)     # 成
            _settle(db, d2, status="done", actual=70)     # 半 + 截留(actual<85)
            _settle(db, d3, status="aborted", actual=40)  # 败（不计截留）
            led = assignment.minister_merit_ledger(db, "毕自严")
            self.assertEqual(led["totals"]["completed"], 3)
            self.assertEqual(led["totals"]["succeeded"], 1)
            self.assertEqual(led["totals"]["partial"], 1)
            self.assertEqual(led["totals"]["failed"], 1)
            self.assertEqual(led["totals"]["skim"], 1)   # 仅 d2
            # 功过分 = 1*2 + 1 - 1*3 - 1*2 = -2
            self.assertEqual(led["merit_score"], -2)
            self.assertEqual(len(led["recent"]), 3)

    def test_report_ledger_marks_skim(self):
        """report_ledger 有记录的 done 差使即使 actual≥85 也计截留（账实不符）。"""
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = kv_int(db, KV_CURRENT_DAY, 1)
            did = assignment.issue_assignment(db, state, kind="edict", text="x", actor="毕自严", day=day)["id"]
            _settle(db, did, status="done", actual=90)
            db.conn.execute(
                "INSERT INTO report_ledger (entity_kind, entity_id, field, reported_value, "
                "actual_value, author_character, reported_day) VALUES ('directive',?,'execution_rate',100,90,'毕自严',?)",
                (str(did), day),
            )
            db.conn.commit()
            led = assignment.minister_merit_ledger(db, "毕自严")
            self.assertEqual(led["totals"]["skim"], 1)

    def test_overview_ranks_by_score(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = kv_int(db, KV_CURRENT_DAY, 1)
            # 毕自严：1 成；卓铭：1 败
            a = assignment.issue_assignment(db, state, kind="edict", text="x", actor="毕自严", day=day)["id"]
            _settle(db, a, status="done", actual=95)
            b = assignment.issue_assignment(db, state, kind="edict", text="y", actor="卓铭", day=day)["id"]
            _settle(db, b, status="aborted", actual=30)
            ov = assignment.merit_overview(db)
            self.assertEqual(ov[0]["assignee"], "毕自严")  # 成 > 败
            self.assertGreater(ov[0]["merit_score"], ov[1]["merit_score"])

    def test_empty_ledger_for_unknown(self):
        with TemporaryDirectory() as tmp:
            db, _ = _fresh(tmp)
            led = assignment.minister_merit_ledger(db, "不存在的人")
            self.assertEqual(led["totals"]["completed"], 0)
            self.assertEqual(led["merit_score"], 0)


class RewardPunishTests(unittest.TestCase):
    def test_reward_three_tiers(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = kv_int(db, KV_CURRENT_DAY, 1)
            for tier, expect_trust in (("merit_mark", 3), ("raise", 5), ("promote", 8)):
                row = db.conn.execute(
                    "SELECT emp_trust FROM characters WHERE name='毕自严'").fetchone()
                before = int(row["emp_trust"])
                r = assignment.grant_reward(db, state, "毕自严", tier=tier, reason="t", day=day)
                self.assertTrue(r["ok"])
                after = int(db.conn.execute(
                    "SELECT emp_trust FROM characters WHERE name='毕自严'").fetchone()["emp_trust"])
                self.assertEqual(after - before, expect_trust, f"{tier} 信任增量")

    def test_punish_three_tiers(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = kv_int(db, KV_CURRENT_DAY, 1)
            # reprimand/fine/demote 怨气分别 +5/+8/+12
            for tier, expect_g in (("reprimand", 5), ("fine", 8), ("demote", 12)):
                before = int(db.conn.execute(
                    "SELECT grievance FROM characters WHERE name='毕自严'").fetchone()["grievance"])
                p = assignment.apply_punishment(db, state, "毕自严", tier=tier, reason="t", day=day)
                self.assertTrue(p["ok"])
                after = int(db.conn.execute(
                    "SELECT grievance FROM characters WHERE name='毕自严'").fetchone()["grievance"])
                self.assertEqual(after - before, expect_g, f"{tier} 怨气增量")

    def test_demote_raises_imperial_power(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = kv_int(db, KV_CURRENT_DAY, 1)
            shi0 = kv_int(db, KV_SHI, 50)
            assignment.apply_punishment(db, state, "毕自严", tier="demote", reason="t", day=day)
            self.assertEqual(kv_int(db, KV_SHI, 50), shi0 + 2)

    def test_actions_logged_and_listable(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = kv_int(db, KV_CURRENT_DAY, 1)
            assignment.grant_reward(db, state, "毕自严", tier="merit_mark", reason="功", day=day)
            assignment.apply_punishment(db, state, "毕自严", tier="fine", reason="过", day=day)
            actions = assignment.list_merit_actions(db, "毕自严")
            self.assertEqual(len(actions), 2)
            kinds = {a["kind"] for a in actions}
            self.assertEqual(kinds, {"reward", "punish"})

    def test_invalid_tier_rejected(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            with self.assertRaises(ValueError):
                assignment.grant_reward(db, state, "毕自严", tier="bogus")
            with self.assertRaises(ValueError):
                assignment.apply_punishment(db, state, "毕自严", tier="bogus")


if __name__ == "__main__":
    unittest.main()
