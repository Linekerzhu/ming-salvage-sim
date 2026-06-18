"""v0.5.0 发布前审计修复的回归测试。零 LLM。"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim import timeflow
from ming_sim.db import GameDB
from ming_sim.scheduler import ensure_worker, stop_worker, _WORKERS
from ming_sim.upgrade_schema import KV_CURRENT_DAY, kv_int, kv_set_int


def _fresh(tmp: str):
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    state = db.load_state()
    day = timeflow.ensure_active(db, state)
    return db, state, day


class WorkerLifecycleTests(unittest.TestCase):
    def test_stop_worker_releases_and_removes(self):
        with TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "w.db")
            GameDB(path)  # 建库
            ensure_worker(path, None)
            self.assertIn(path, _WORKERS)
            worker = _WORKERS[path]
            stop_worker(path)
            self.assertNotIn(path, _WORKERS)
            self.assertFalse(worker._thread.is_alive())
            # 幂等
            stop_worker(path)
            # 可重挂
            ensure_worker(path, None)
            self.assertIn(path, _WORKERS)
            stop_worker(path)


class RollbackGateTests(unittest.TestCase):
    def test_undo_blocked_after_time_advances(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            turn_id = db.create_chat_turn(state, "韩爌", "session-x", 0)
            db.conn.execute(
                "UPDATE chat_turns SET user_message_id=1, minister_message_id=2 WHERE id=?",
                (turn_id,))
            db.conn.commit()
            # 同日可撤
            self.assertTrue(db.can_undo_last_chat_turn("韩爌", state.turn))
            # 推进一天后禁撤
            kv_set_int(db, KV_CURRENT_DAY, kv_int(db, KV_CURRENT_DAY, 0) + 1)
            self.assertFalse(db.can_undo_last_chat_turn("韩爌", state.turn))

    def test_chat_rollback_restores_kv_store(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            db.kv_set("test.rollback.kv", "before")
            turn_id = db.create_chat_turn(state, "韩爌", "session-x", 0)
            before = db.capture_chat_rollback_snapshot()
            db.kv_set("test.rollback.kv", "after")
            db.record_chat_turn_rollback_diffs(
                turn_id,
                before,
                db.capture_chat_rollback_snapshot(),
            )

            db.undo_chat_turn(turn_id)

            self.assertEqual(db.kv_get("test.rollback.kv"), "before")


class MonthOpenRoutineTests(unittest.TestCase):
    def test_faction_heat_decays_at_month_open(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            db.conn.execute("UPDATE factions SET heat=50")
            db.conn.commit()
            # 走完本月 → 月结 → 新月开月应衰减 heat
            timeflow.month_fixed_flows(db, state)
            state.next_period()
            db.save_state(state)
            timeflow.on_month_resolved(db, state)
            heats = [int(r["heat"]) for r in db.conn.execute("SELECT heat FROM factions")]
            self.assertTrue(all(h == 48 for h in heats), heats)

    def test_attention_fresh_at_month_open(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            from ming_sim.memorials import attention_left, consume_attention, reset_attention_for_day
            reset_attention_for_day(db, day)
            consume_attention(db, 10)
            timeflow.month_fixed_flows(db, state)
            state.next_period()
            db.save_state(state)
            timeflow.on_month_resolved(db, state)
            from ming_sim.upgrade_schema import ATTENTION_PER_DAY
            self.assertEqual(attention_left(db), ATTENTION_PER_DAY)


class ProgressSelfCorrectionTests(unittest.TestCase):
    def test_long_directive_completes_at_eta_not_day100(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            from ming_sim import lifecycle
            cur = db.conn.execute(
                "INSERT INTO turn_directives (turn, year, period, text, source, status) "
                "VALUES (?,?,?,?,?,?)",
                (state.turn, state.year, state.period, "工期校验占位旨", "test", "confirmed"))
            db.conn.commit()
            did = int(cur.lastrowid)
            rows = db.conn.execute("SELECT * FROM turn_directives WHERE id=?", (did,)).fetchall()
            lifecycle.init_directive_lifecycles(db, state, rows, day)
            # 人为设 150 天工期：第 75 天进度应≈50%，绝不该 100%
            db.conn.execute(
                "UPDATE turn_directives SET lifecycle_status='executing', lead_days=0, "
                "exec_days=150, eta_day=?, progress=0 WHERE id=?", (day + 150, did))
            db.conn.commit()
            for d in range(day + 1, day + 76):
                lifecycle.tick_directives(db, state, d)
                # 旬检定可能随机封驳/拖延——本测试只验进度算法，出现即复位
                db.conn.execute(
                    "UPDATE turn_directives SET lifecycle_status='executing', anomaly='', "
                    "exec_days=150, eta_day=? WHERE id=?", (day + 150, did))
                db.conn.commit()
            row = db.conn.execute(
                "SELECT progress, lifecycle_status FROM turn_directives WHERE id=?", (did,)).fetchone()
            self.assertEqual(str(row["lifecycle_status"]), "executing")
            self.assertLess(int(row["progress"]), 80)
            self.assertGreater(int(row["progress"]), 30)


if __name__ == "__main__":
    unittest.main()
