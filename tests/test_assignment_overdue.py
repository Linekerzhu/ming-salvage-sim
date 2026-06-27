"""P1.2b/c 逾期追责完整闭环测试。

- P1.2b：钦定期逾期的自动旬追责（怨气/信任/势，每旬一次，力度递增）
- P1.2c：玩家主动追责 reprimand_overdue（申饬/罚俸/降黜三档）

零 LLM。
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim import assignment, lifecycle, timeflow
from ming_sim.db import GameDB
from ming_sim.upgrade_schema import KV_CURRENT_DAY, KV_SHI, kv_int


def _fresh(tmp: str):
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    state = db.load_state()
    timeflow.ensure_active(db, state)
    assignment.ensure_assignment_schema(db)
    return db, state


def _day(db: GameDB) -> int:
    return kv_int(db, KV_CURRENT_DAY, 1)


def _issue_overdue(db, state, *, assignee, deadline=10, push_eta_to=40):
    """下达带钦定期的旨意，并把运行时 eta 顶远（模拟异常致延期），使其保持 executing 不结案。"""
    start = _day(db)
    r = assignment.issue_assignment(db, state, kind="edict", text="着十日内清账",
                                    actor=assignee, day=start, deadline_days=deadline)
    did = r["id"]
    db.conn.execute(
        "UPDATE turn_directives SET exec_days=?, eta_day=?, progress=20 WHERE id=?",
        (int(push_eta_to), start + int(push_eta_to), did),
    )
    db.conn.commit()
    return did, start + deadline  # 返回 did 与钦定期绝对日


def _stats(db, name):
    row = db.conn.execute(
        "SELECT grievance, emp_trust FROM characters WHERE name=?", (name,)
    ).fetchone()
    return int(row["grievance"]), int(row["emp_trust"])


class OverdueAutoConsequenceTests(unittest.TestCase):
    def test_overdue_triggers_stat_damage_and_event(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            did, deadline_day = _issue_overdue(db, state, assignee="毕自严", deadline=10)
            g0, t0 = _stats(db, "毕自严")
            shi0 = kv_int(db, KV_SHI, 50)
            # tick 到钦定期之后第 1 日
            events = lifecycle.tick_directives(db, state, day=deadline_day + 1)
            g1, t1 = _stats(db, "毕自严")
            self.assertEqual(g1, g0 + 2)         # 怨气 +2
            self.assertEqual(t1, t0 - 2)         # 信任 -2
            self.assertEqual(kv_int(db, KV_SHI, 50), shi0 - 1)  # 势 -1
            self.assertTrue(any(e.get("kind") == "directive_overdue" for e in events))

    def test_once_per_deca(self):
        """同一旬内多次 tick 不重复追责。"""
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            did, deadline_day = _issue_overdue(db, state, assignee="毕自严", deadline=10)
            lifecycle.tick_directives(db, state, day=deadline_day + 1)
            g1, _ = _stats(db, "毕自严")
            # 同旬 +5 日再 tick
            lifecycle.tick_directives(db, state, day=deadline_day + 6)
            g1b, _ = _stats(db, "毕自严")
            self.assertEqual(g1b, g1)  # 不变

    def test_escalates_next_deca(self):
        """下一旬追责力度递增（第2旬怨气+3）。"""
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            did, deadline_day = _issue_overdue(db, state, assignee="毕自严", deadline=10)
            lifecycle.tick_directives(db, state, day=deadline_day + 1)   # 第1旬 +2
            g1, _ = _stats(db, "毕自严")
            lifecycle.tick_directives(db, state, day=deadline_day + 11)  # 第2旬 +3
            g2, _ = _stats(db, "毕自严")
            self.assertEqual(g2 - g1, 3)

    def test_no_deadline_no_consequence(self):
        """无钦定期的旨意，即便运行时被顶远、tick 过原 eta，也不触发逾期追责。"""
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            start = _day(db)
            r = assignment.issue_assignment(db, state, kind="edict",
                                            text="着户部清查账目", actor="毕自严", day=start)
            did = r["id"]
            db.conn.execute(
                "UPDATE turn_directives SET exec_days=40, eta_day=?, progress=20 WHERE id=?",
                (start + 40, did),
            )
            db.conn.commit()
            g0, _ = _stats(db, "毕自严")
            lifecycle.tick_directives(db, state, day=start + 25)  # 远超原 eta
            g1, _ = _stats(db, "毕自严")
            self.assertEqual(g1, g0)  # 无钦定期 → 不追责


class ReprimandOverdueTests(unittest.TestCase):
    def test_three_severities_apply(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            for sev, expect_g in (("reprimand", 8), ("fine", 10), ("demote", 15)):
                did, deadline_day = _issue_overdue(db, state, assignee="毕自严", deadline=10)
                g0, t0 = _stats(db, "毕自严")
                res = lifecycle.intervene(db, state, did, "reprimand_overdue",
                                          day=deadline_day + 1, severity=sev)
                self.assertTrue(res["ok"], f"{sev} 应成功：{res.get('message')}")
                g1, t1 = _stats(db, "毕自严")
                self.assertEqual(g1 - g0, expect_g, f"{sev} 怨气增量")
                self.assertLess(t1, t0)  # 信任必降

    def test_rejected_when_not_overdue(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            start = _day(db)
            # 钦定期 30 日，当前日未到
            r = assignment.issue_assignment(db, state, kind="edict", text="着三十日内办",
                                            actor="毕自严", day=start, deadline_days=30)
            res = lifecycle.intervene(db, state, r["id"], "reprimand_overdue",
                                      day=start + 5, severity="reprimand")
            self.assertFalse(res["ok"])
            self.assertIn("尚未逾期", res["message"])

    def test_rejected_without_deadline(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            start = _day(db)
            r = assignment.issue_assignment(db, state, kind="edict",
                                            text="着清查账目", actor="毕自严", day=start)
            res = lifecycle.intervene(db, state, r["id"], "reprimand_overdue",
                                      day=start + 50, severity="demote")
            self.assertFalse(res["ok"])
            self.assertIn("无钦定期", res["message"])

    def test_demote_flags_pending_demote(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            import json
            did, deadline_day = _issue_overdue(db, state, assignee="毕自严", deadline=10)
            lifecycle.intervene(db, state, did, "reprimand_overdue",
                                day=deadline_day + 1, severity="demote")
            row = db.conn.execute("SELECT chain FROM turn_directives WHERE id=?", (did,)).fetchone()
            meta = json.loads(row["chain"])
            self.assertEqual(meta["last_reprimand"]["severity"], "demote")
            self.assertIn("pending_demote", meta)


if __name__ == "__main__":
    unittest.main()
