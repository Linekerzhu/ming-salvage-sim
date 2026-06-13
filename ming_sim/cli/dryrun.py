"""无头试跑仪表（M6「调参与全案验证」）：零 LLM，把 11 个系统当一盘棋跑数十月。

用途三合一：
  1) 咬合验证——引擎全程不崩，断言时间/势/RA/账目/生命周期等不变式。
  2) 调参仪表——打印势/RA/国库/民心轨迹、异常分布、办结率，供平衡性调参。
  3) 缺口探针——专测三个整合缺口（见 GapProbe）：
       缺口1 因果伏笔：全程有无人埋种（plant_causal_seed 是否被生产代码调用）。
       缺口2 势消费：势被谁读、被谁忽略（派系 heat / 税收效率是否吃势）。
       缺口3 生命周期效果：截留(integrity_actual<85)办结后，游戏数值有无相应折损。

跑法：  python -m ming_sim.cli.dryrun --months 36 --seed 42 [--directives 3] [--quiet]

确定性：自动皇帝按 seed 从语料库抽诏，旬检定本就是 did*100003+day 确定性种子，
        全程不调 LLM（月度过渡走 decree.advance_without_edict 的零-LLM 路径）。
"""

from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional
import random as _random

from ming_sim import lifecycle, timeflow
from ming_sim.db import GameDB
from ming_sim.issues import apply_issue_inertia_and_ongoing, clear_gated_legacies
from ming_sim.upgrade_schema import (
    KV_CURRENT_DAY,
    KV_RISK_AVERSION,
    KV_SHI,
    RISK_AVERSION_DEFAULT,
    SHI_DEFAULT,
    DAYS_PER_MONTH,
    kv_int,
)

# 自动皇帝的诏书语料：覆盖全部 directive 类别，含会触发阻力/截留/封驳的硬骨头。
DIRECTIVE_CORPUS = [
    "着户部即拨辽东军饷三十万两，毋得稽延",          # fiscal_allocation
    "清丈江南田亩，清查隐田，整顿盐政商税",            # tax_reform（士绅阻力高）
    "着即拨陕西赈济银二十万两，平粜安置流民",          # relief
    "调宣府兵马入卫京师，整饬边备",                    # military_ops
    "清查京营空饷，核饷核兵，有侵冒者论罪",            # audit_purge（官僚阻力）
    "起复孙承宗，量才录用，简拔贤能",                  # personnel
    "修缮蓟镇边墙，增筑墩台",                          # construction
    "遣使谕朝鲜，申明藩属之义",                        # diplomacy
    "祭告太庙，旌表忠烈",                              # ritual_signal（短工期低风险）
    "密遣厂卫廉访山西边镇粮饷虚实",                    # secret_investigation
    "裁撤天下驿递冗员，岁省驿银",                      # 经典「裁驿」——本应埋因果伏笔(缺口1)
]


@dataclass
class GapProbe:
    """三个整合缺口的运行期证据。"""
    # 缺口1
    seeds_planted: int = 0           # 全程 causal_seeds 新增行数
    seeds_sprouted: int = 0
    cut_post_directives: int = 0     # 颁了「裁驿」类诏的次数（本应埋伏笔）
    # 缺口2
    shi_change_reasons: Dict[str, int] = field(default_factory=dict)  # 势变动理由计数
    faction_moves: int = 0           # 全程派系出招次数（势驱动是否让世界活起来）
    heat_min: int = 999              # 全程派系 heat 最低（修前会一路衰到 0 死掉）
    heat_max: int = 0
    # 缺口3
    skimmed_done: int = 0            # 截留(actual<85)却办结的旨意数
    skim_total_lost: int = 0         # 累计被截留的执行率点数
    clean_done: int = 0
    report_ledger_rows: int = 0      # 账实分叉条数（截留留痕）
    unrest_start: int = 0            # 全国 unrest 合计（首月末）
    unrest_end: int = 0              # 全国 unrest 合计（末月末）


@dataclass
class MonthSnapshot:
    turn: int
    year: int
    period: int
    day: int
    shi: int
    ra: int
    国库: int
    内库: int
    民心: int
    皇威: int
    live_directives: int
    done_cum: int
    aborted_cum: int
    faction_heat_avg: float
    memorials_pending: int


class DryRun:
    def __init__(self, *, months: int, seed: int, directives_per_month: int, quiet: bool):
        self.months = months
        self.seed = seed
        self.dpm = directives_per_month
        self.quiet = quiet
        self.rng = _random.Random(seed)
        self.tmp = tempfile.mkdtemp(prefix="ming_dryrun_")
        self.db = GameDB(str(Path(self.tmp) / "dryrun.db"))
        self.db.seed_static_data()
        self.state = self.db.load_state()
        timeflow.ensure_active(self.db, self.state)
        self.snaps: List[MonthSnapshot] = []
        self.probe = GapProbe()
        self.invariant_failures: List[str] = []
        self.red_events = 0
        self.yellow_events = 0
        self._seeds_seen = 0  # causal_seeds 上次观测到的最大 id

    # ── 自动皇帝 ─────────────────────────────────────────────────────────────
    def _issue_directives(self) -> None:
        day = kv_int(self.db, KV_CURRENT_DAY, 1)
        picks = self.rng.sample(DIRECTIVE_CORPUS, min(self.dpm, len(DIRECTIVE_CORPUS)))
        ids: List[int] = []
        for text in picks:
            cur = self.db.conn.execute(
                "INSERT INTO turn_directives (turn, year, period, text, source, status, actor)"
                " VALUES (?,?,?,?,?,?,?)",
                (self.state.turn, self.state.year, self.state.period, text, "dryrun", "confirmed", None),
            )
            ids.append(int(cur.lastrowid))
            if "裁撤" in text and "驿" in text:
                self.probe.cut_post_directives += 1
        self.db.conn.commit()
        rows = self.db.conn.execute(
            "SELECT * FROM turn_directives WHERE id IN (%s)" % ",".join("?" * len(ids)), ids
        ).fetchall()
        lifecycle.init_directive_lifecycles(self.db, self.state, rows, day)

    # ── 推进一整月（逐日：勤政皇帝每日处置奏疏，无视红黄停顿）────────────────
    def _advance_full_month(self) -> None:
        month_end = self.state.turn * DAYS_PER_MONTH
        guard = 0
        while kv_int(self.db, KV_CURRENT_DAY, 0) < month_end and guard < 40:
            guard += 1
            result = timeflow.advance_days(self.db, self.state, 1, stop_on_yellow=False)
            for rep in result["reports"]:
                for ev in rep["events"]:
                    lvl = ev.get("level")
                    if lvl == "red":
                        self.red_events += 1
                    elif lvl == "yellow":
                        self.yellow_events += 1
                    if ev.get("kind") == "faction_move":
                        self.probe.faction_moves += 1
            if result["advanced"] == 0:
                break
            self._auto_process_memorials()
            self._auto_signal_acts()

    def _auto_process_memorials(self) -> None:
        """勤政皇帝：当日注意力清御案（告变→准/纳谏，弹章→发部议中性，常规→准）。
        留出 2 点注意力余地。注意力 12/日，到达量 ~1/日，engaged 下绰绰有余。"""
        from ming_sim.memorials import attention_left, decide_memorial
        day = kv_int(self.db, KV_CURRENT_DAY, 0)
        rows = self.db.conn.execute(
            "SELECT id, kind FROM memorials WHERE status='pending' "
            "ORDER BY CASE kind WHEN '弹章' THEN 0 WHEN '告变' THEN 0 ELSE 1 END, arrived_day"
        ).fetchall()
        for row in rows:
            if attention_left(self.db) <= 2:
                break
            kind = str(row["kind"])
            action = "refer" if kind == "弹章" else "approve"
            decide_memorial(self.db, self.state, int(row["id"]), action, day=day)

    def _auto_signal_acts(self) -> None:
        """君威不振时偶行信号之举重塑势（献俘/廷杖）；任事意愿过低时下罪己诏安人心。
        逢旬一次，模拟玩家用朝堂剧场对冲螺旋。"""
        from ming_sim.theater import signal_action
        from ming_sim.upgrade_schema import KV_RISK_AVERSION, RISK_AVERSION_DEFAULT
        day = kv_int(self.db, KV_CURRENT_DAY, 0)
        if day % 10 != 0:
            return
        shi = kv_int(self.db, KV_SHI, SHI_DEFAULT)
        ra = kv_int(self.db, KV_RISK_AVERSION, RISK_AVERSION_DEFAULT)
        if ra >= 85:
            signal_action(self.db, self.state, "zuiji", day=day)   # 罪己诏：RA-8 民心+3 势-5
        elif shi < 40:
            signal_action(self.db, self.state, "xianfu", day=day)  # 献俘：势+5 民心+2

    # ── 缺口探针 ─────────────────────────────────────────────────────────────
    def _scan_gaps_this_month(self) -> None:
        # 缺口1：causal_seeds 新增
        max_id = self.db.conn.execute(
            "SELECT COALESCE(MAX(id),0) m FROM causal_seeds").fetchone()["m"]
        if max_id > self._seeds_seen:
            self.probe.seeds_planted += max_id - self._seeds_seen
            self._seeds_seen = max_id
        self.probe.seeds_sprouted = self.db.conn.execute(
            "SELECT COUNT(*) c FROM causal_seeds WHERE status='sprouted'").fetchone()["c"]
        # 缺口2 证据：派系 heat 是否随势复活（修前一路衰到 0）
        hr = self.db.conn.execute("SELECT MIN(heat) lo, MAX(heat) hi FROM factions").fetchone()
        self.probe.heat_min = min(self.probe.heat_min, int(hr["lo"] or 0))
        self.probe.heat_max = max(self.probe.heat_max, int(hr["hi"] or 0))
        # 缺口3 证据：全国 unrest（截留→民变压力的落点之一）
        unrest = self.db.conn.execute("SELECT COALESCE(SUM(unrest),0) s FROM regions").fetchone()["s"]
        if self.probe.unrest_start == 0:
            self.probe.unrest_start = int(unrest)
        self.probe.unrest_end = int(unrest)

    def _scan_gaps_final(self) -> None:
        # 缺口2：势变动理由分布
        for row in self.db.conn.execute("SELECT reason FROM belief_logs WHERE key='shi'").fetchall():
            r = str(row["reason"])
            tag = r.split("#")[0].split("（")[0][:12]
            self.probe.shi_change_reasons[tag] = self.probe.shi_change_reasons.get(tag, 0) + 1
        # 缺口3：截留办结却无机械后果
        for row in self.db.conn.execute(
            "SELECT integrity_actual, integrity_reported FROM turn_directives "
            "WHERE lifecycle_status='done'").fetchall():
            actual = int(row["integrity_actual"])
            if actual < 85:
                self.probe.skimmed_done += 1
                self.probe.skim_total_lost += (100 - actual)
            else:
                self.probe.clean_done += 1
        self.probe.report_ledger_rows = self.db.conn.execute(
            "SELECT COUNT(*) c FROM report_ledger WHERE entity_kind='directive'").fetchone()["c"]

    # ── 不变式 ───────────────────────────────────────────────────────────────
    def _check_invariants(self) -> None:
        day = kv_int(self.db, KV_CURRENT_DAY, 0)
        turn = self.state.turn
        lo, hi = (turn - 1) * DAYS_PER_MONTH + 1, turn * DAYS_PER_MONTH
        if not (lo <= day <= hi):
            self.invariant_failures.append(f"current_day={day} 越出 turn{turn} 窗口[{lo},{hi}]")
        shi = kv_int(self.db, KV_SHI, SHI_DEFAULT)
        ra = kv_int(self.db, KV_RISK_AVERSION, RISK_AVERSION_DEFAULT)
        if not (0 <= shi <= 100):
            self.invariant_failures.append(f"势={shi} 越界")
        if not (0 <= ra <= 100):
            self.invariant_failures.append(f"RA={ra} 越界")
        for k, v in self.state.metrics.items():
            if v < 0:
                self.invariant_failures.append(f"指标{k}={v} 为负")

    def _stuck_directives(self) -> int:
        """长期卡在 in_transit/executing/stalled 且早该完成的旨意数（咬合健康度）。"""
        day = kv_int(self.db, KV_CURRENT_DAY, 0)
        return self.db.conn.execute(
            "SELECT COUNT(*) c FROM turn_directives WHERE lifecycle_status IN "
            "('in_transit','executing','stalled') AND eta_day>0 AND eta_day < ?-60",
            (day,),
        ).fetchone()["c"]

    # ── 快照 ─────────────────────────────────────────────────────────────────
    def _snapshot(self) -> None:
        heat_row = self.db.conn.execute("SELECT AVG(heat) a FROM factions").fetchone()
        mem = self.db.conn.execute(
            "SELECT COUNT(*) c FROM memorials WHERE status='pending'").fetchone()["c"]
        done = self.db.conn.execute(
            "SELECT COUNT(*) c FROM turn_directives WHERE lifecycle_status='done'").fetchone()["c"]
        aborted = self.db.conn.execute(
            "SELECT COUNT(*) c FROM turn_directives WHERE lifecycle_status='aborted'").fetchone()["c"]
        live = self.db.conn.execute(
            "SELECT COUNT(*) c FROM turn_directives WHERE lifecycle_status IN "
            "('in_transit','executing','stalled')").fetchone()["c"]
        self.snaps.append(MonthSnapshot(
            turn=self.state.turn, year=self.state.year, period=self.state.period,
            day=kv_int(self.db, KV_CURRENT_DAY, 0),
            shi=kv_int(self.db, KV_SHI, SHI_DEFAULT),
            ra=kv_int(self.db, KV_RISK_AVERSION, RISK_AVERSION_DEFAULT),
            国库=int(self.state.metrics.get("国库", 0)),
            内库=int(self.state.metrics.get("内库", 0)),
            民心=int(self.state.metrics.get("民心", 0)),
            皇威=int(self.state.metrics.get("皇威", 0)),
            live_directives=live, done_cum=done, aborted_cum=aborted,
            faction_heat_avg=round(float(heat_row["a"] or 0), 1),
            memorials_pending=mem,
        ))

    # ── 主循环 ───────────────────────────────────────────────────────────────
    def run(self) -> None:
        for _ in range(self.months):
            self._issue_directives()
            self._advance_full_month()
            self._check_invariants()
            self._scan_gaps_this_month()
            self._snapshot()
            self._rollover_month()
        self._scan_gaps_final()

    def _rollover_month(self) -> None:
        """忠于「真实已结算回合」的零-LLM 月度过渡：
        快进旬份额 + 施加 issue 持续效果（民心/皇威 等危机消耗）+ 清除已解除的帝国修正
        + 跨月。镜像 decree.resolve_directives 的结算尾段，仅去掉 LLM 叙事/提取。
        注意：apply_score_extraction（旨意效果提取）本就只在 LLM 路径——无头不调，
        正是为了让缺口3「生命周期无机械后果」如实暴露。"""
        timeflow.month_fixed_flows(self.db, self.state)
        try:
            apply_issue_inertia_and_ongoing(self.db, self.state, touched_ids=set())
            clear_gated_legacies(self.db, self.state)
        except Exception as exc:
            self.invariant_failures.append(f"持续效果结算异常：{exc}")
        self.state.next_period()
        self.db.save_state(self.state)
        timeflow.on_month_resolved(self.db, self.state)

    # ── 报表 ─────────────────────────────────────────────────────────────────
    def report(self) -> None:
        s0, sN = self.snaps[0], self.snaps[-1]
        print("=" * 72)
        print(f"无头试跑  seed={self.seed}  月数={self.months}  每月颁诏={self.dpm}")
        print("=" * 72)

        if not self.quiet:
            print("\n── 轨迹（每月末快照）" + "─" * 50)
            print(f"{'年月':>9} {'势':>4} {'RA':>4} {'国库':>6} {'内库':>6} "
                  f"{'民心':>4} {'皇威':>4} {'在办':>4} {'累办':>4} {'撤':>3} {'派系热':>5} {'待奏':>4}")
            step = max(1, self.months // 24)
            for i, m in enumerate(self.snaps):
                if i % step and i != len(self.snaps) - 1:
                    continue
                print(f"{m.year}/{m.period:<2d}{'':>2} {m.shi:>4} {m.ra:>4} {m.国库:>6} {m.内库:>6} "
                      f"{m.民心:>4} {m.皇威:>4} {m.live_directives:>4} {m.done_cum:>4} "
                      f"{m.aborted_cum:>3} {m.faction_heat_avg:>5} {m.memorials_pending:>4}")

        print("\n── 咬合健康 " + "─" * 58)
        print(f"  红事件总数：{self.red_events}   黄事件总数：{self.yellow_events}")
        print(f"  滞留旨意（超 eta 60+ 日未结）：{self._stuck_directives()}")
        if self.invariant_failures:
            print(f"  ✗ 不变式违例 {len(self.invariant_failures)} 处：")
            for f in self.invariant_failures[:10]:
                print(f"      - {f}")
        else:
            print("  ✓ 全部不变式通过（时间窗口/势RA边界/指标非负）")

        print("\n── 缺口探针 " + "─" * 58)
        # 缺口1
        flag1 = "✗ 死基础设施" if self.probe.seeds_planted == 0 else "✓"
        print(f"  [缺口1 因果伏笔] 全程埋种 {self.probe.seeds_planted} 个 / 萌发 "
              f"{self.probe.seeds_sprouted} 个   {flag1}")
        print(f"      （其中颁「裁驿」类诏 {self.probe.cut_post_directives} 次——本应各埋一颗流寇伏笔）")
        # 缺口2：势现在驱动派系气焰 + 税收到账率
        flag2 = "✓" if self.probe.faction_moves > 0 and self.probe.heat_max > 0 else "✗ 派系仍死寂"
        print(f"  [缺口2 势消费] 派系出招 {self.probe.faction_moves} 次   "
              f"heat 区间 [{self.probe.heat_min},{self.probe.heat_max}]   {flag2}")
        print(f"      消费方(读势): 执行检定/独断/史笔/御案 ＋ 派系heat·出招(势<45涨) ＋ 税收到账率(±15%)")
        if not self.quiet and self.probe.shi_change_reasons:
            print("      势变动理由：" + "  ".join(
                f"{t}×{n}" for t, n in sorted(self.probe.shi_change_reasons.items(), key=lambda x: -x[1])[:5]))
        # 缺口3：截留现在有机械后果（民怨/民变压力）
        flag3 = "✓" if (self.probe.skimmed_done == 0 or self.probe.unrest_end > self.probe.unrest_start) else "✗ 无后果"
        print(f"  [缺口3 生命周期效果] 截留办结 {self.probe.skimmed_done} 道 / 洁净 "
              f"{self.probe.clean_done} 道 / 账实分叉留痕 {self.probe.report_ledger_rows} 条")
        print(f"      截留共偷 {self.probe.skim_total_lost} 执行率点 → 全国民变压力 "
              f"{self.probe.unrest_start}→{self.probe.unrest_end}（截留即刻化民怨/unrest）   {flag3}")

        print("\n── 关键变化（首→末月）" + "─" * 46)
        print(f"  势 {s0.shi}→{sN.shi}   RA {s0.ra}→{sN.ra}   "
              f"国库 {s0.国库}→{sN.国库}   民心 {s0.民心}→{sN.民心}")
        print("=" * 72)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="大明1628 无头试跑仪表（零 LLM）")
    ap.add_argument("--months", type=int, default=36)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--directives", type=int, default=3, help="自动皇帝每月颁诏数")
    ap.add_argument("--quiet", action="store_true", help="只打印汇总，不打印逐月轨迹")
    args = ap.parse_args(argv)

    dr = DryRun(months=args.months, seed=args.seed,
                directives_per_month=args.directives, quiet=args.quiet)
    dr.run()
    dr.report()
    return 1 if dr.invariant_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
