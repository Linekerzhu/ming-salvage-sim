"""P1.5 常驻差使（posting）+ P1.6 NPC 自动上奏测试。

- posting：送达转 executing 但不推进/不结案；月度 tick 产报+效果；撤差
- 自动上奏：地方民变信号 → 生成赈济奏请；去重

零 LLM。
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim import assignment, lifecycle, timeflow
from ming_sim.db import GameDB
from ming_sim.quest_db import apply_quest_schema
from ming_sim.upgrade_schema import KV_CURRENT_DAY, kv_int


def _fresh(tmp: str):
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    state = db.load_state()
    timeflow.ensure_active(db, state)
    assignment.ensure_assignment_schema(db)
    assignment.ensure_merit_schema(db)
    apply_quest_schema(db.conn)
    return db, state


class PostingTests(unittest.TestCase):
    def test_create_posting_marks_is_posting_and_large_exec(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = kv_int(db, KV_CURRENT_DAY, 1)
            import json
            r = assignment.create_posting(db, state, minister="卓铭", duty_type="mine_tax", day=day)
            row = db.conn.execute(
                "SELECT exec_days, assignment_kind, chain FROM turn_directives WHERE id=?",
                (r["id"],)).fetchone()
            self.assertEqual(str(row["assignment_kind"]), "posting")
            self.assertEqual(int(row["exec_days"]), 9999)
            meta = json.loads(row["chain"])
            self.assertTrue(meta.get("is_posting"))
            self.assertEqual(meta["posting"]["duty_type"], "mine_tax")

    def test_posting_does_not_auto_complete_or_progress(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = kv_int(db, KV_CURRENT_DAY, 1)
            r = assignment.create_posting(db, state, minister="卓铭", duty_type="frontier_commander", day=day)
            lead = int(db.conn.execute(
                "SELECT lead_days FROM turn_directives WHERE id=?", (r["id"],)).fetchone()["lead_days"])
            # 推过送达期 → executing
            lifecycle.tick_directives(db, state, day=day + lead + 1)
            st = db.conn.execute(
                "SELECT lifecycle_status FROM turn_directives WHERE id=?", (r["id"],)).fetchone()
            self.assertEqual(str(st["lifecycle_status"]), "executing")
            # 再推 60 日 → 不结案、progress 仍 0
            lifecycle.tick_directives(db, state, day=day + lead + 61)
            st2 = db.conn.execute(
                "SELECT lifecycle_status, progress FROM turn_directives WHERE id=?", (r["id"],)).fetchone()
            self.assertEqual(str(st2["lifecycle_status"]), "executing")
            self.assertEqual(int(st2["progress"]), 0)

    def test_posting_monthly_tick_applies_effects_and_emits(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = kv_int(db, KV_CURRENT_DAY, 1)
            assignment.create_posting(db, state, minister="卓铭", duty_type="mine_tax", day=day)
            mx0 = db.load_state().metrics.get("民心", 50)
            events = assignment.posting_monthly_tick(db, state, day + 30)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["kind"], "posting_monthly_report")
            # 矿税：民心 -2
            state2 = db.load_state()
            self.assertEqual(state2.metrics.get("民心", 50), mx0 - 2)

    def test_revoke_posting(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = kv_int(db, KV_CURRENT_DAY, 1)
            r = assignment.create_posting(db, state, minister="卓铭", duty_type="general_duty", day=day)
            res = assignment.revoke_posting(db, state, r["id"], day=day + 1)
            self.assertTrue(res["ok"])
            st = db.conn.execute(
                "SELECT lifecycle_status FROM turn_directives WHERE id=?", (r["id"],)).fetchone()
            self.assertEqual(str(st["lifecycle_status"]), "aborted")

    def test_revoke_rejects_non_posting(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = kv_int(db, KV_CURRENT_DAY, 1)
            r = assignment.issue_assignment(db, state, kind="edict", text="着办某事", actor="卓铭", day=day)
            res = assignment.revoke_posting(db, state, r["id"], day=day + 1)
            self.assertFalse(res["ok"])


class AutoPetitionTests(unittest.TestCase):
    def test_regional_distress_generates_relief_petition(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = kv_int(db, KV_CURRENT_DAY, 1)
            # 制造高民变地区
            db.conn.execute(
                "UPDATE regions SET unrest=78 WHERE id=(SELECT id FROM regions LIMIT 1)")
            db.conn.commit()
            events = assignment.petition_auto_tick(db, state, day)
            self.assertTrue(any(e["kind"] == "petition_auto" for e in events))
            avail = assignment.list_petitions(db, status="available")
            self.assertTrue(any("赈" in p["title"] for p in avail))

    def test_dedup_no_flood(self):
        """同类信号同月不重复生成（去重）。"""
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = kv_int(db, KV_CURRENT_DAY, 1)
            db.conn.execute(
                "UPDATE regions SET unrest=80 WHERE id=(SELECT id FROM regions LIMIT 1)")
            db.conn.commit()
            assignment.petition_auto_tick(db, state, day)
            n1 = len(assignment.list_petitions(db, status="available"))
            # 同 turn 再跑一次，不应新增同类
            assignment.petition_auto_tick(db, state, day)
            n2 = len(assignment.list_petitions(db, status="available"))
            self.assertEqual(n2, n1)

    def test_no_signal_no_petition(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = kv_int(db, KV_CURRENT_DAY, 1)
            # 无高民变、无高野心
            db.conn.execute("UPDATE regions SET unrest=10")
            db.conn.commit()
            events = assignment.petition_auto_tick(db, state, day)
            self.assertEqual(len(events), 0)


if __name__ == "__main__":
    unittest.main()
