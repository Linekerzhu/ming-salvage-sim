"""P1.4 多阶段里程碑测试。

- 里程碑按类别+工期生成（短2段/中3段/长4段）
- 进度越过阈值标记完成 + 阶段性复命事件
- 最终阶段（复命/100%）不重复发事件（由 directive_done 发）

零 LLM。
"""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim import assignment, lifecycle, timeflow
from ming_sim.db import GameDB
from ming_sim.upgrade_schema import KV_CURRENT_DAY, kv_int


def _fresh(tmp: str):
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    state = db.load_state()
    timeflow.ensure_active(db, state)
    assignment.ensure_assignment_schema(db)
    return db, state


def _chain_milestones(db, did):
    row = db.conn.execute("SELECT chain FROM turn_directives WHERE id=?", (did,)).fetchone()
    return json.loads(row["chain"]).get("milestones") or []


def _force_executing(db, did, *, progress):
    """强制进入 executing 并设定进度（测试用，避开 in_transit 等待）。"""
    row = db.conn.execute(
        "SELECT start_day, lead_days, exec_days, eta_day FROM turn_directives WHERE id=?", (did,)
    ).fetchone()
    start = int(row["start_day"]); lead = int(row["lead_days"]); execd = int(row["exec_days"])
    day = kv_int(db, KV_CURRENT_DAY, 1)
    db.conn.execute(
        "UPDATE turn_directives SET lifecycle_status='executing', progress=?, "
        "start_day=?, eta_day=? WHERE id=?",
        (int(progress), day - lead, day + execd, did))
    db.conn.commit()


class MilestoneGenerationTests(unittest.TestCase):
    def test_long_directive_has_more_stages(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = kv_int(db, KV_CURRENT_DAY, 1)
            # 长差使（清丈，exec_days 通常 >40）
            r = assignment.issue_assignment(db, state, kind="edict",
                                            text="着户部清丈全国田亩", actor="毕自严", day=day)
            ms = _chain_milestones(db, r["id"])
            self.assertGreaterEqual(len(ms), 3)
            # 末段必为复命、阈值 100
            self.assertEqual(ms[-1]["label"], "复命")
            self.assertEqual(int(ms[-1]["threshold"]), 100)

    def test_category_flavored_labels(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = kv_int(db, KV_CURRENT_DAY, 1)
            r = assignment.issue_assignment(db, state, kind="edict",
                                            text="着锦衣卫密查陕西贪墨实情", actor="卓铭", day=day)
            ms = _chain_milestones(db, r["id"])
            labels = {m["label"] for m in ms}
            # secret_investigation 模板含「布线/取证/回奏」之一
            self.assertTrue(labels & {"布线", "取证", "回奏"})


class MilestoneProgressTests(unittest.TestCase):
    def test_progress_crosses_thresholds_marks_done(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = kv_int(db, KV_CURRENT_DAY, 1)
            r = assignment.issue_assignment(db, state, kind="edict",
                                            text="着户部清丈全国田亩", actor="毕自严", day=day)
            _force_executing(db, r["id"], progress=50)
            events = lifecycle.tick_directives(db, state, day=day + 1)
            ms = _chain_milestones(db, r["id"])
            done = [m["label"] for m in ms if m["status"] == "done"]
            self.assertIn("清丈", done)   # 阈值 25
            self.assertIn("造册", done)   # 阈值 50
            # 阶段性复命事件应发出（非最终段）
            ms_events = [e for e in events if e.get("kind") == "directive_milestone"]
            self.assertTrue(ms_events)

    def test_final_milestone_no_duplicate_event(self):
        """最终段（100%）由 directive_done 发，不重复发 milestone 事件。"""
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = kv_int(db, KV_CURRENT_DAY, 1)
            r = assignment.issue_assignment(db, state, kind="edict",
                                            text="着户部清丈全国田亩", actor="毕自严", day=day)
            _force_executing(db, r["id"], progress=99)
            # 推到 100% → 完成
            row = db.conn.execute(
                "SELECT start_day, lead_days, exec_days FROM turn_directives WHERE id=?",
                (r["id"],)).fetchone()
            db.conn.execute(
                "UPDATE turn_directives SET eta_day=? WHERE id=?",
                (kv_int(db, KV_CURRENT_DAY, 1) + 1, r["id"]))
            db.conn.commit()
            events = lifecycle.tick_directives(db, state, day=kv_int(db, KV_CURRENT_DAY, 1) + 1)
            ms = _chain_milestones(db, r["id"])
            # 复命段应已 done
            self.assertEqual(ms[-1]["status"], "done")
            # 但不应有「复命」段的 milestone 事件（最终段被抑制，由 directive_done 发）
            # 非最终段事件标题形如「X阶段复命」（如"清丈阶段复命"），最终段会是"复命阶段复命"
            ms_ev = [e for e in events if e.get("kind") == "directive_milestone"
                     and "复命阶段复命" in str(e.get("title") or "")]
            self.assertEqual(len(ms_ev), 0)
            # directive_done 事件应存在
            self.assertTrue(any(e.get("kind") == "directive_done" for e in events))

    def test_milestones_exposed_in_payload(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = kv_int(db, KV_CURRENT_DAY, 1)
            assignment.issue_assignment(db, state, kind="edict",
                                        text="着户部清丈全国田亩", actor="毕自严", day=day)
            payload = lifecycle.lifecycle_payload(db, include_done=True, limit=10)
            self.assertTrue(payload)
            self.assertTrue(payload[0].get("milestones"))


if __name__ == "__main__":
    unittest.main()
