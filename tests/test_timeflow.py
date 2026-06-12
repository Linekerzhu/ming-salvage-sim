"""M1 时间地基测试：日推进、旬税赋、月窗口、硬阈值、因果伏笔、调度表。零 LLM。"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim import timeflow
from ming_sim.db import GameDB
from ming_sim.thresholds import scan_thresholds, threshold_dashboard
from ming_sim.upgrade_schema import (
    DAYS_PER_MONTH,
    KV_CURRENT_DAY,
    KV_DEFICIT_STREAK,
    KV_RISK_AVERSION,
    KV_SHI,
    adjust_belief,
    kv_int,
    kv_set_int,
)


def _fresh(tmp: str):
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    state = db.load_state()
    return db, state


class TimeflowBasics(unittest.TestCase):
    def test_activation_and_month_window(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = timeflow.ensure_active(db, state)
            self.assertEqual(day, (state.turn - 1) * DAYS_PER_MONTH + 1)
            status = timeflow.time_status(db, state)
            self.assertEqual(status["day_in_month"], 1)
            self.assertFalse(status["await_decree"])

    def test_advance_stops_at_month_end(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            timeflow.ensure_active(db, state)
            result = timeflow.advance_days(db, state, 999, stop_on_yellow=False)
            self.assertLessEqual(result["advanced"], DAYS_PER_MONTH - 1)
            status = timeflow.time_status(db, state)
            if result["stopped_by"] == "month_end":
                self.assertTrue(status["await_decree"])

    def test_xun_fiscal_shares_sum_to_month(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            timeflow.ensure_active(db, state)
            before = int(state.metrics["国库"]) + int(state.metrics["内库"])
            # 推到月末（忽略红黄停顿，硬推）
            month_end = state.turn * DAYS_PER_MONTH
            while kv_int(db, KV_CURRENT_DAY, 0) < month_end:
                r = timeflow.advance_days(db, state, 999, stop_on_yellow=False)
                if r["advanced"] == 0 and r["stopped_by"] != "month_end":
                    break
                if r["stopped_by"] == "month_end":
                    break
            # 三旬全部落账
            self.assertEqual(kv_int(db, "upgrade.month_xun_applied", 0), 3)
            # 再调 month_fixed_flows 不应重复落账（幂等）
            after_advance = int(state.metrics["国库"]) + int(state.metrics["内库"])
            flows = timeflow.month_fixed_flows(db, state)
            after_resolve = int(state.metrics["国库"]) + int(state.metrics["内库"])
            self.assertEqual(after_advance, after_resolve)
            self.assertTrue(isinstance(flows, list) and flows)
            # 至少应有军饷与税赋两类流水
            cats = {str(f.get("category")) for f in flows}
            self.assertIn("各军军饷", cats)

    def test_early_resolve_fast_forwards_fiscal(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            timeflow.ensure_active(db, state)
            timeflow.advance_days(db, state, 5, stop_on_yellow=False)  # 月中第6天
            timeflow.month_fixed_flows(db, state)  # 提前颁诏
            self.assertEqual(kv_int(db, "upgrade.month_xun_applied", 0), 3)

    def test_on_month_resolved_jumps_to_new_month(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            timeflow.ensure_active(db, state)
            timeflow.month_fixed_flows(db, state)
            state.next_period()
            db.save_state(state)
            timeflow.on_month_resolved(db, state)
            day = kv_int(db, KV_CURRENT_DAY, 0)
            self.assertEqual(day, (state.turn - 1) * DAYS_PER_MONTH + 1)
            # 新月已开月（军饷已结）
            self.assertEqual(kv_int(db, "upgrade.month_opened_turn", 0), state.turn)


class ThresholdTests(unittest.TestCase):
    def test_opening_board_no_mutiny(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = timeflow.ensure_active(db, state)
            fired = scan_thresholds(db, state, day)
            titles = [f["title"] for f in fired]
            self.assertFalse(any("哗变" in t for t in titles), f"开局不应哗变：{titles}")

    def test_mutiny_fires_on_conditions(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = timeflow.ensure_active(db, state)
            db.conn.execute(
                "UPDATE armies SET arrears=200, morale=30 WHERE id='xuan_da'")
            db.conn.commit()
            fired = scan_thresholds(db, state, day)
            self.assertTrue(any("宣大" in f["title"] and "哗变" in f["title"] for f in fired), fired)
            # 已立 issue
            hit = [f for f in fired if "哗变" in f["title"]][0]
            self.assertTrue(hit["issue_id"])
            # 冷却：再扫不重复触发
            fired2 = scan_thresholds(db, state, day + 1)
            self.assertFalse(any("宣大" in f["title"] for f in fired2))

    def test_dashboard_shows_warnings(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = timeflow.ensure_active(db, state)
            board = threshold_dashboard(db, state, day)
            self.assertTrue(board)
            # 陕西镇开局欠饷 5 个月 → 该仪表应非 safe
            shaanxi = [b for b in board if b["object_id"] == "shaanxi_army"]
            self.assertTrue(shaanxi)
            self.assertIn(shaanxi[0]["status"], ("warn", "danger"))

    def test_deficit_streak_rule(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = timeflow.ensure_active(db, state)
            kv_set_int(db, KV_DEFICIT_STREAK, 3)
            fired = scan_thresholds(db, state, day)
            self.assertTrue(any("太仓" in f["title"] for f in fired), fired)


class SchedulerAndSeeds(unittest.TestCase):
    def test_scheduled_resolution_fires_and_pauses(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = timeflow.ensure_active(db, state)
            timeflow.schedule(db, "agreement_due", day + 2,
                              {"title": "东厂密报", "detail": "test"},
                              auto_pause=2, created_day=day)
            result = timeflow.advance_days(db, state, 10, stop_on_yellow=False)
            self.assertEqual(result["stopped_by"], "red")
            self.assertEqual(result["advanced"], 2)
            events = [e for r in result["reports"] for e in r["events"]]
            self.assertTrue(any(e["title"] == "东厂密报" for e in events))

    def test_causal_seed_sprouts(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = timeflow.ensure_active(db, state)
            timeflow.plant_causal_seed(
                db, created_day=day, fuse_days=3,
                event_spec={"id": "seed_test_post", "title": "裁驿之祸",
                            "kind": "流寇", "summary": "被裁驿卒聚啸山林。"},
                note="裁撤驿站的延迟代价",
            )
            result = timeflow.advance_days(db, state, 10, stop_on_yellow=False)
            self.assertEqual(result["stopped_by"], "red")
            events = [e for r in result["reports"] for e in r["events"]]
            self.assertTrue(any("裁驿之祸" in str(e["title"]) for e in events))


class BeliefTests(unittest.TestCase):
    def test_adjust_belief_clamps_and_logs(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = timeflow.ensure_active(db, state)
            before = kv_int(db, "upgrade.shi", 55)  # 开月诏题奖励可能已动过势，取相对值
            new = adjust_belief(db, KV_SHI, +10, "公开惩处抗命", day=day)
            self.assertEqual(new, min(100, before + 10))
            new = adjust_belief(db, KV_RISK_AVERSION, +200, "株连大狱", day=day)
            self.assertEqual(new, 100)
            rows = db.conn.execute(
                "SELECT * FROM belief_logs WHERE reason IN ('公开惩处抗命','株连大狱')").fetchall()
            self.assertEqual(len(rows), 2)


if __name__ == "__main__":
    unittest.main()
