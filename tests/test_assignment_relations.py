"""P2 任务关联测试：依赖冻结/解冻、冲突预警、调查转弹劾。零 LLM。"""

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
    assignment.ensure_merit_schema(db)
    return db, state


def _force_executing(db, did, *, day):
    db.conn.execute(
        "UPDATE turn_directives SET lifecycle_status='executing', start_day=?, eta_day=? WHERE id=?",
        (day - 5, day + 40, did))
    db.conn.commit()


class DependencyTests(unittest.TestCase):
    def test_depends_on_stored(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = kv_int(db, KV_CURRENT_DAY, 1)
            a = assignment.issue_assignment(db, state, kind="edict", text="查", actor="毕自严", day=day)["id"]
            b = assignment.issue_assignment(db, state, kind="edict", text="惩", actor="崔呈秀", day=day, depends_on=[a])["id"]
            meta = json.loads(db.conn.execute("SELECT chain FROM turn_directives WHERE id=?", (b,)).fetchone()["chain"])
            self.assertEqual(meta.get("depends_on"), [a])

    def test_freezes_until_dependency_done(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = kv_int(db, KV_CURRENT_DAY, 1)
            a = assignment.issue_assignment(db, state, kind="edict", text="查", actor="毕自严", day=day)["id"]
            b = assignment.issue_assignment(db, state, kind="edict", text="惩", actor="崔呈秀", day=day, depends_on=[a])["id"]
            _force_executing(db, b, day=day)
            # A 未 done → B 冻结
            lifecycle.tick_directives(db, state, day=day + 1)
            prog = db.conn.execute("SELECT progress FROM turn_directives WHERE id=?", (b,)).fetchone()["progress"]
            meta = json.loads(db.conn.execute("SELECT chain FROM turn_directives WHERE id=?", (b,)).fetchone()["chain"])
            self.assertEqual(prog, 0)
            self.assertTrue(meta.get("deps_blocked"))

    def test_unblocks_when_dependency_done(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = kv_int(db, KV_CURRENT_DAY, 1)
            a = assignment.issue_assignment(db, state, kind="edict", text="查", actor="毕自严", day=day)["id"]
            b = assignment.issue_assignment(db, state, kind="edict", text="惩", actor="崔呈秀", day=day, depends_on=[a])["id"]
            _force_executing(db, b, day=day)
            lifecycle.tick_directives(db, state, day=day + 1)  # 冻结
            # A done
            db.conn.execute("UPDATE turn_directives SET lifecycle_status='done', progress=100 WHERE id=?", (a,))
            db.conn.commit()
            ev = lifecycle.tick_directives(db, state, day=day + 2)
            self.assertTrue(any(e.get("kind") == "directive_unblocked" for e in ev))
            meta = json.loads(db.conn.execute("SELECT chain FROM turn_directives WHERE id=?", (b,)).fetchone()["chain"])
            self.assertFalse(meta.get("deps_blocked"))

    def test_no_dependency_advances_normally(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = kv_int(db, KV_CURRENT_DAY, 1)
            r = assignment.issue_assignment(db, state, kind="edict", text="着办某事", actor="毕自严", day=day)
            _force_executing(db, r["id"], day=day)
            lifecycle.tick_directives(db, state, day=day + 1)
            prog = db.conn.execute("SELECT progress FROM turn_directives WHERE id=?", (r["id"],)).fetchone()["progress"]
            self.assertGreater(prog, 0)


class OverloadWarningTests(unittest.TestCase):
    def test_warning_when_third_assignment(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = kv_int(db, KV_CURRENT_DAY, 1)
            # 第一、二件无预警
            r1 = assignment.issue_assignment(db, state, kind="edict", text="一", actor="毕自严", day=day)
            r2 = assignment.issue_assignment(db, state, kind="edict", text="二", actor="毕自严", day=day)
            self.assertFalse(r1["overload_warning"])
            self.assertFalse(r2["overload_warning"])
            # 第三件（含本件≥3）预警
            r3 = assignment.issue_assignment(db, state, kind="edict", text="三", actor="毕自严", day=day)
            self.assertTrue(r3["overload_warning"])
            self.assertIn("毕自严", r3["overload_warning"])


class TransformInvestigationTests(unittest.TestCase):
    def test_transform_creates_new_assignment_with_target(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = kv_int(db, KV_CURRENT_DAY, 1)
            inv = assignment.issue_assignment(db, state, kind="edict", text="着密查贪墨", actor="卓铭", day=day)["id"]
            db.conn.execute("UPDATE turn_directives SET lifecycle_status='done', category='audit_purge' WHERE id=?", (inv,))
            db.conn.execute(
                "INSERT INTO report_ledger (entity_kind,entity_id,field,reported_value,actual_value,author_character,reported_day) "
                "VALUES ('directive',?,'execution_rate',100,60,'毕自严',?)", (str(inv), day))
            db.conn.commit()
            res = assignment.transform_investigation(db, state, inv, day=day)
            self.assertTrue(res["ok"])
            self.assertEqual(res["target"], "毕自严")
            self.assertEqual(res["category"], "audit_purge")
            # 原差使标 transformed_to
            meta = json.loads(db.conn.execute("SELECT chain FROM turn_directives WHERE id=?", (inv,)).fetchone()["chain"])
            self.assertEqual(meta.get("transformed_to"), res["new_assignment_id"])

    def test_rejects_not_done(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = kv_int(db, KV_CURRENT_DAY, 1)
            inv = assignment.issue_assignment(db, state, kind="edict", text="查", actor="卓铭", day=day)
            res = assignment.transform_investigation(db, state, inv["id"], day=day)
            self.assertFalse(res["ok"])

    def test_rejects_no_target_found(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = kv_int(db, KV_CURRENT_DAY, 1)
            inv = assignment.issue_assignment(db, state, kind="edict", text="查", actor="卓铭", day=day)["id"]
            db.conn.execute("UPDATE turn_directives SET lifecycle_status='done', category='audit_purge' WHERE id=?", (inv,))
            db.conn.commit()
            res = assignment.transform_investigation(db, state, inv, day=day)
            self.assertFalse(res["ok"])  # 无截留/无线索 → 拒

    def test_rejects_double_transform(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = kv_int(db, KV_CURRENT_DAY, 1)
            inv = assignment.issue_assignment(db, state, kind="edict", text="查", actor="卓铭", day=day)["id"]
            db.conn.execute("UPDATE turn_directives SET lifecycle_status='done', category='audit_purge' WHERE id=?", (inv,))
            db.conn.execute(
                "INSERT INTO report_ledger (entity_kind,entity_id,field,reported_value,actual_value,author_character,reported_day) "
                "VALUES ('directive',?,'execution_rate',100,60,'毕自严',?)", (str(inv), day))
            db.conn.commit()
            assignment.transform_investigation(db, state, inv, day=day)
            res2 = assignment.transform_investigation(db, state, inv, day=day)
            self.assertFalse(res2["ok"])


if __name__ == "__main__":
    unittest.main()
