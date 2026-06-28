"""平衡护栏回归测试（commit b0d05a2 引入的三条护栏，此前仅契约解析被测）。

护栏目标（防止退化）：
1. 皇威 drift-to-baseline：不再单向棘轮钉死 100，每月向威权基线（随势）缓慢回归。
2. 封驳搁置 势 bleed 封顶 -3：一次封驳的累计折势不超过收回成命(-3)。
3. 搁置逾 30 天自动作罢：清出超期积压，止血（headless 下原本永久拖拽势→棘轮到吸收态）。

零 LLM。
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim import lifecycle, timeflow
from ming_sim.db import GameDB
from ming_sim.timeflow import _huangwei_drift_to_baseline
from ming_sim.upgrade_schema import (
    KV_CURRENT_DAY,
    KV_SHI,
    SHI_DEFAULT,
    kv_int,
    kv_set_int,
)


def _fresh(tmp: str):
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    state = db.load_state()
    timeflow.ensure_active(db, state)
    db.save_state(state)
    return db, state


def _day(db: GameDB) -> int:
    return kv_int(db, KV_CURRENT_DAY, 1)


def _force_stalled_directive(db, state, *, stalled_age_days: int) -> int:
    """构造一条已搁置 stalled_age_days 天的 stalled 指令，返回其 id。

    走 init_directive_lifecycles 建 lifecycle 字段（保证 schema + 字段齐全），
    再手动改写为 stalled 状态并回拨 start_day 到 stalled_age_days 天前。
    """
    from ming_sim.upgrade_schema import KV_CURRENT_DAY
    day = _day(db)
    did = int(db.conn.execute(
        "INSERT INTO turn_directives(turn, year, period, text, source, status, actor) "
        "VALUES(?,?,?,?,?,?,?)",
        (state.turn, state.year, state.period, "某事知道了", "test", "confirmed", None),
    ).lastrowid)
    rows = db.conn.execute("SELECT * FROM turn_directives WHERE id=?", (did,)).fetchall()
    lifecycle.init_directive_lifecycles(db, state, rows, day)

    # 读取 init 落的 lead/exec，反算 start_day 使 stall_age 达到目标
    row = db.conn.execute(
        "SELECT lead_days, exec_days FROM turn_directives WHERE id=?", (did,)
    ).fetchone()
    lead = int(row["lead_days"] or 1)
    exec_days = int(row["exec_days"] or 5)
    start_day = day - stalled_age_days - lead - exec_days
    db.conn.execute(
        "UPDATE turn_directives SET lifecycle_status='stalled', status='stalled', "
        "start_day=?, anomaly='{}' WHERE id=?",
        (start_day, did),
    )
    db.conn.commit()
    return did


class HuangweiDriftToBaselineTests(unittest.TestCase):
    """皇威 drift-to-baseline：皇威不钉死 100，每月向威权基线回归。
    直接测 _huangwei_drift_to_baseline（_ensure_month_open 月开时调一次）。"""

    def test_huangwei_above_baseline_drifts_down(self):
        """皇威远高于基线时，drift 后必须回落（不棘轮钉死 100）。"""
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            # 势设为默认，基线 ≈ 42；皇威拔到 95（廷杖/献俘后典型高位）
            kv_set_int(db, KV_SHI, SHI_DEFAULT)
            state.metrics["皇威"] = 95
            db.save_state(state)
            self.assertEqual(int(state.metrics["皇威"]), 95)

            _huangwei_drift_to_baseline(db, state, day=_day(db))
            state = db.load_state()
            wei = int(state.metrics["皇威"])
            # 必须从 95 回落（gap=53, step≈round(53*0.12)=6 → 89），不钉死 100
            self.assertLess(wei, 95,
                            f"皇威应从 95 回落，实际 {wei}（drift 失效→棘轮风险）")
            self.assertGreaterEqual(wei, 80, "皇威回落不应一次性崩盘")

    def test_huangwei_below_baseline_drifts_up(self):
        """皇威远低于基线时，drift 后必须回升（势盛则威自加）。"""
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            # 势拔高 → 基线上浮；皇威压到 20
            kv_set_int(db, KV_SHI, 85)  # baseline = 42 + (85-55)*0.35 ≈ 52.5
            state.metrics["皇威"] = 20
            db.save_state(state)

            _huangwei_drift_to_baseline(db, state, day=_day(db))
            state = db.load_state()
            wei = int(state.metrics["皇威"])
            self.assertGreater(wei, 20,
                               f"皇威应从 20 回升（势盛威自加），实际 {wei}")

    def test_huangwei_does_not_ratchet_to_100_repeatedly(self):
        """连续多次 drift（模拟数月无新武功），皇威不得单向爬到 100 并钉死。"""
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            kv_set_int(db, KV_SHI, SHI_DEFAULT)
            state.metrics["皇威"] = 90
            db.save_state(state)
            weis = [90]
            for _ in range(4):
                _huangwei_drift_to_baseline(db, state, day=_day(db))
                state = db.load_state()
                weis.append(int(state.metrics["皇威"]))
            # 4 次 drift 后皇威必须明显回落，不得 ≥ 95（钉死信号）
            self.assertLess(weis[-1], 90,
                            f"连续 4 次 drift 皇威应回落，实际轨迹 {weis}（棘轮钉死风险）")


class StalledDirectiveShiBleedCapTests(unittest.TestCase):
    """封驳搁置 势 bleed 封顶 -3：一次封驳累计折势不超过收回成命(-3)。"""

    def test_stalled_bleed_capped_at_three(self):
        """同一条 stalled 指令，每旬折势 -1，但累计不超过 3。"""
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            shi0 = kv_int(db, KV_SHI, SHI_DEFAULT)
            # 构造一条搁置龄足够的 stalled 指令（让多次旬检都命中）
            did = _force_stalled_directive(db, state, stalled_age_days=40)

            # 跑 5 次 tick_directives（每次 day 选在 %10==0 的旬检日）
            base_day = _day(db)
            shi_after = shi0
            tick_days = [base_day, base_day + 10, base_day + 20, base_day + 30, base_day + 40]
            for d in tick_days:
                kv_set_int(db, KV_CURRENT_DAY, d)
                lifecycle.tick_directives(db, state, day=d)
                shi_after = kv_int(db, KV_SHI, SHI_DEFAULT)

            # 累计折势不超过 3（bleed cap）—— 这是核心护栏
            total_bled = shi0 - shi_after
            self.assertLessEqual(total_bled, 3,
                                 f"单条 stalled 指令累计折势不超过 3；实际折 {total_bled}（bleed cap 失效）")

            # anomaly 里 shi_bled 应已封顶在 3
            anomaly = json.loads(db.conn.execute(
                "SELECT anomaly FROM turn_directives WHERE id=?", (did,)
            ).fetchone()["anomaly"] or "{}")
            self.assertLessEqual(int(anomaly.get("shi_bled", 0)), 3,
                                 f"anomaly.shi_bled 应封顶 3；实际 {anomaly}")


class StalledDirectiveAutoAbortTests(unittest.TestCase):
    """搁置逾 30 天自动作罢：清出超期积压，止血。"""

    def test_chronic_stall_auto_aborted_after_30_days(self):
        """搁置龄 > 30 天的指令，tick 后必须自动 aborted。"""
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            did = _force_stalled_directive(db, state, stalled_age_days=45)
            # 确认初始是 stalled
            status_before = db.conn.execute(
                "SELECT lifecycle_status FROM turn_directives WHERE id=?", (did,)
            ).fetchone()["lifecycle_status"]
            self.assertEqual(status_before, "stalled")

            events = lifecycle.tick_directives(db, state, day=_day(db))

            status_after = db.conn.execute(
                "SELECT lifecycle_status FROM turn_directives WHERE id=?", (did,)
            ).fetchone()["lifecycle_status"]
            self.assertEqual(status_after, "aborted",
                             f"搁置 45 天（>30）应自动作罢；实际 {status_after}")
            # 应产生 directive_aborted 事件
            abort_events = [e for e in events if e.get("kind") == "directive_aborted"]
            self.assertGreaterEqual(len(abort_events), 1,
                                    "久搁自罢应产生 directive_aborted 事件")

    def test_fresh_stall_not_aborted_prematurely(self):
        """搁置龄 ≤ 30 天的指令不应被过早作罢（仍可催办/换人/收回）。"""
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            did = _force_stalled_directive(db, state, stalled_age_days=15)

            lifecycle.tick_directives(db, state, day=_day(db))

            status_after = db.conn.execute(
                "SELECT lifecycle_status FROM turn_directives WHERE id=?", (did,)
            ).fetchone()["lifecycle_status"]
            self.assertEqual(status_after, "stalled",
                             f"搁置仅 15 天（≤30）不应作罢；实际 {status_after}")


if __name__ == "__main__":
    unittest.main()
