"""M6 节奏层测试：中兴指数、阶段诏题、结局光谱。零 LLM。"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim import timeflow, zhongxing
from ming_sim.db import GameDB
from ming_sim.upgrade_schema import KV_RISK_AVERSION, kv_set_int


def _fresh(tmp: str):
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    state = db.load_state()
    day = timeflow.ensure_active(db, state)
    return db, state, day


class IndexTests(unittest.TestCase):
    def test_compute_in_range_with_parts(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            zx = zhongxing.compute_zhongxing(db, state)
            self.assertTrue(0 <= zx["total"] <= 100)
            self.assertEqual(set(zx["parts"].keys()), {"财政", "边备", "流寇压制", "吏治", "民心"})

    def test_history_recorded_at_month_open(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            # ensure_active 已开月 → 已记一条
            history = zhongxing.zhongxing_history(db)
            self.assertGreaterEqual(len(history), 1)
            self.assertEqual(history[-1]["turn"], state.turn)

    def test_governance_falls_with_risk_aversion(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            g0 = zhongxing.compute_zhongxing(db, state)["parts"]["吏治"]
            kv_set_int(db, KV_RISK_AVERSION, 100)
            g1 = zhongxing.compute_zhongxing(db, state)["parts"]["吏治"]
            self.assertLess(g1, g0)


class StageGoalTests(unittest.TestCase):
    def test_current_stage_is_foothold_at_start(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            stage = zhongxing.current_stage(state)
            self.assertEqual(stage["id"], "stage_foothold")

    def test_goal_completion_rewards_shi(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            # 把国库抬过线再评估 → g_treasury_300 达成并给势奖励
            state.metrics["国库"] = 400
            completed = zhongxing.evaluate_stage_goals(db, state, day)
            ids = [c["id"] for c in completed]
            if "g_treasury_300" not in ids:
                # 开月时已达成过（开月评估早于军饷扣款的存档情形）
                payload = zhongxing.stage_payload(db, state)
                goal = next(g for g in payload["goals"] if g["id"] == "g_treasury_300")
                self.assertTrue(goal["done"])
            rows = db.conn.execute(
                "SELECT * FROM belief_logs WHERE reason LIKE '%诏题达成%'").fetchall()
            self.assertTrue(rows)
            # 幂等：再评估不重复奖励
            again = zhongxing.evaluate_stage_goals(db, state, day)
            self.assertNotIn("g_treasury_300", [c["id"] for c in again])

    def test_stage_payload_shape(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            payload = zhongxing.stage_payload(db, state)
            self.assertTrue(payload["stage"]["title"])
            self.assertTrue(payload["goals"])


class SpectrumTests(unittest.TestCase):
    def test_spectrum_labels(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            survived = zhongxing.spectrum_label(db, state, "timeout")
            self.assertIn(survived["label"], ("中兴在望", "划江守成", "苟延残喘"))
            kv_set_int(db, KV_RISK_AVERSION, 85)
            fell = zhongxing.spectrum_label(db, state, "capital_fallen")
            self.assertIn(fell["label"], ("乾纲独断的代价", "回天乏术", "功败垂成"))


if __name__ == "__main__":
    unittest.main()
