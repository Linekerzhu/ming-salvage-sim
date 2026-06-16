"""半即时时间引擎（S1）：日为最小单位，规则层日 tick 零 LLM。L7。

与旧月结算的契约：
- 一月 = 30 日；turn N 占据绝对日 [(N-1)*30+1, N*30]。
- 月初（开月）：军饷/建筑整月结算（保持 arrears/morale 旧语义）+ 记月初国库
  + 缓存当月定额预算；税赋等通用定额按旬（10/20/30 日）三等分落账。
- 月末（第 30 日）时间停驻，等玩家颁诏/无诏推进 → decree.resolve_directives
  仍是 LLM 大结算唯一入口，但其中 apply_fixed_period_flows 由本模块的
  month_fixed_flows 替代（已逐旬落账，不再整月重复落）。
- 推进中每日扫硬阈值（thresholds.scan_thresholds）、检因果伏笔、弹调度表节点。

激活语义：kv 里写过 current_day 即激活（ensure_active）。旧档迁移：
current_day = (turn-1)*30 + 1，当月按新流程开月。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ming_sim.constants import TURN_UNIT
from ming_sim.db import GameDB
from ming_sim.models import Event, GameState
from ming_sim.upgrade_schema import (
    DAYS_PER_MONTH,
    KV_CURRENT_DAY,
    KV_DEFICIT_STREAK,
    KV_MONTH_START_BALANCE,
    KV_TIME_SPEED,
    ensure_upgrade_schema,
    kv_int,
    kv_set_int,
)

KV_MONTH_OPENED_TURN = "upgrade.month_opened_turn"
KV_XUN_APPLIED = "upgrade.month_xun_applied"
KV_MONTH_FLOWS = "upgrade.month_flows"        # 当月累计 flows JSON（喂 simulator）
KV_BUDGET_LINES = "upgrade.month_budget_lines"  # 当月通用定额行 JSON
KV_MONTH_EVENTS = "upgrade.month_events"      # 当月日 tick 事件账（史官约束：邸报不得与之矛盾）

# 暂停级别：红必停 / 黄默认停 / 蓝仅角标
LEVEL_RED = "red"
LEVEL_YELLOW = "yellow"
LEVEL_BLUE = "blue"


def _short(text: object, limit: int = 120) -> str:
    raw = re.sub(r"\s+", " ", str(text or "").strip())
    if len(raw) <= limit:
        return raw
    return raw[: max(1, limit - 1)] + "…"


@dataclass
class DayReport:
    day: int
    events: List[Dict[str, object]] = field(default_factory=list)

    def add(self, level: str, kind: str, title: str, detail: str = "",
            ref_kind: str = "", ref_id: str = "") -> None:
        self.events.append({
            "level": level, "kind": kind, "title": title, "detail": detail,
            "ref_kind": ref_kind, "ref_id": ref_id, "day": self.day,
        })


# ── 激活与状态 ────────────────────────────────────────────────────────────────

def ensure_active(db: GameDB, state: GameState) -> int:
    """确保时间引擎激活并对齐当前 turn。返回 current_day。"""
    ensure_upgrade_schema(db)
    raw = db.kv_get(KV_CURRENT_DAY)
    month_start = (state.turn - 1) * DAYS_PER_MONTH + 1
    month_end = state.turn * DAYS_PER_MONTH
    if raw:
        try:
            day = int(raw)
        except ValueError:
            day = month_start
        # 存档回退/越月失配时拉回本月窗口
        if day < month_start or day > month_end:
            day = month_start
            kv_set_int(db, KV_CURRENT_DAY, day)
    else:
        day = month_start
        kv_set_int(db, KV_CURRENT_DAY, day)
    _ensure_month_open(db, state, day)
    return day


def is_active(db: GameDB) -> bool:
    try:
        return bool(db.kv_get(KV_CURRENT_DAY))
    except Exception:
        return False


def day_in_month(day: int) -> int:
    return (int(day) - 1) % DAYS_PER_MONTH + 1


def time_status(db: GameDB, state: GameState) -> Dict[str, object]:
    day = ensure_active(db, state)
    dim = day_in_month(day)
    return {
        "current_day": day,
        "day_in_month": dim,
        "days_in_month": DAYS_PER_MONTH,
        "year": state.year,
        "month": state.period,
        "turn": state.turn,
        "time_speed": kv_int(db, KV_TIME_SPEED, 1),
        "at_month_end": dim >= DAYS_PER_MONTH,
        # 连续时间：月末不再硬停等颁诏（诏书逐条即时复命）。保留 at_month_end 仅供 UI 提示。
        "await_decree": False,
    }


def set_speed(db: GameDB, speed: int) -> int:
    speed = max(0, min(3, int(speed)))
    kv_set_int(db, KV_TIME_SPEED, speed)
    return speed


# ── 月初开月 ──────────────────────────────────────────────────────────────────

def _flows_append(db: GameDB, entries: List[Dict[str, object]]) -> None:
    if not entries:
        return
    try:
        existing = json.loads(db.kv_get(KV_MONTH_FLOWS) or "[]")
    except ValueError:
        existing = []
    existing.extend(entries)
    db.kv_set(KV_MONTH_FLOWS, json.dumps(existing, ensure_ascii=False))


def _ensure_month_open(db: GameDB, state: GameState, day: int) -> List[Dict[str, object]]:
    """开月：军饷/建筑整月结算 + 月初余额 + 当月通用定额缓存。幂等（按 turn 去重）。"""
    if kv_int(db, KV_MONTH_OPENED_TURN, 0) == state.turn:
        return []
    from ming_sim.flows import compute_budget_lines

    # 赤字 streak：跟上月初余额比，低了记一月赤字
    prev_balance = db.kv_get(KV_MONTH_START_BALANCE)
    if prev_balance is not None and prev_balance != "":
        try:
            prev = int(prev_balance)
            cur = int(state.metrics.get("国库", 0))
            streak = kv_int(db, KV_DEFICIT_STREAK, 0)
            kv_set_int(db, KV_DEFICIT_STREAK, streak + 1 if cur < prev else 0)
        except ValueError:
            pass
    kv_set_int(db, KV_MONTH_START_BALANCE, int(state.metrics.get("国库", 0)))

    flows = _settle_army_and_buildings(db, state)

    # 通用定额行（除军饷/建筑外）缓存，逐旬落账
    budget = compute_budget_lines(db, state)
    skip = {"各军军饷", "建筑产出", "建筑维护"}
    lines: List[Dict[str, object]] = []
    for account in ("国库", "内库"):
        for direction in ("income", "expense"):
            for it in budget[account][direction]:
                if it["name"] in skip or int(it["amount"]) <= 0:
                    continue
                lines.append({
                    "account": account, "direction": direction,
                    "name": str(it["name"]), "amount": int(it["amount"]),
                })
    db.kv_set(KV_BUDGET_LINES, json.dumps(lines, ensure_ascii=False))
    kv_set_int(db, KV_XUN_APPLIED, 0)
    db.kv_set(KV_MONTH_FLOWS, "[]")
    db.kv_set(KV_MONTH_EVENTS, "[]")
    _flows_append(db, flows)
    kv_set_int(db, KV_MONTH_OPENED_TURN, state.turn)
    db.save_state(state)
    # 朔日例行（审计修复 P1-2：advance_days 的 tick 永远从第 2 日起，
    # dim==1 的逻辑必须挂在开月而非日 tick 上）：
    # 1) 派系 heat 自然衰减；2) 注意力按朔日刷新
    try:
        db.conn.execute("UPDATE factions SET heat=MAX(0, heat-2)")
        db.conn.commit()
    except Exception:
        pass
    # 恒稳态（P1 皇权是均衡）：势/任事意愿每月向基线缓慢回归，
    # 使其成为可被玩家拉扯的张力变量，而非一路棘轮到 0/100 的吸收态。
    # 君威无大失则自复（势→55）；积重无新刺激则渐缓（RA→40）。回归步长＝当前缺口的 ~14%。
    try:
        from ming_sim.upgrade_schema import (
            KV_RISK_AVERSION, KV_SHI, RISK_AVERSION_DEFAULT, SHI_DEFAULT, adjust_belief,
        )
        for key, default in ((KV_SHI, SHI_DEFAULT), (KV_RISK_AVERSION, RISK_AVERSION_DEFAULT)):
            cur = kv_int(db, key, default)
            gap = default - cur
            if gap:
                step = max(1, round(abs(gap) * 0.14)) * (1 if gap > 0 else -1)
                adjust_belief(db, key, step, "恒稳态回归（君威自复/积重渐缓）", day=day)
    except Exception:
        pass
    try:
        from ming_sim.memorials import reset_attention_for_day
        reset_attention_for_day(db, day)
    except ImportError:
        pass

    # 兵额账实分离播种（S3，幂等）：兵部账面 vs 实在兵
    try:
        from ming_sim.veil import seed_army_divergences
        seed_army_divergences(db, state, day)
    except ImportError:
        pass
    # 朔日节奏层（S12）：中兴指数刷新 + 阶段诏题评估
    try:
        from ming_sim.zhongxing import monthly_update
        monthly_update(db, state, day)
    except ImportError:
        pass
    return flows


def _settle_army_and_buildings(db: GameDB, state: GameState) -> List[Dict[str, object]]:
    """军饷（按优先级、欠饷/士气联动）与建筑（产出/维护）整月结算。
    逻辑与 flows.apply_fixed_period_flows 的对应段一致，只是挪到月初执行。"""
    from ming_sim.flows import ARMY_SALARY_PRIORITY
    flows: List[Dict[str, object]] = []

    army_rows = db.conn.execute(
        "SELECT id, name, maintenance_per_turn, arrears, morale FROM armies"
    ).fetchall()
    army_map = {str(r["id"]): r for r in army_rows}
    ordered = [army_map[k] for k in ARMY_SALARY_PRIORITY if k in army_map]
    ordered += [r for r in army_rows if str(r["id"]) not in ARMY_SALARY_PRIORITY]
    for row in ordered:
        army_id, name = str(row["id"]), str(row["name"])
        needed = int(row["maintenance_per_turn"])
        if needed <= 0:
            continue
        available = max(0, int(state.metrics["国库"]))
        pay_current = min(needed, available)
        shortfall = needed - pay_current
        old_arrears, old_morale = int(row["arrears"]), int(row["morale"])
        if pay_current > 0:
            db.record_issue_economy_move(state, "国库", -pay_current, "各军军饷", f"{name}{TURN_UNIT}军饷")
        new_arrears = max(0, old_arrears + shortfall)
        if shortfall > 0:
            morale_delta = -max(1, round(8 * shortfall / needed))
        elif old_arrears == 0:
            morale_delta = +2
        else:
            morale_delta = 0
        new_morale = max(0, min(100, old_morale + morale_delta))
        db.conn.execute("UPDATE armies SET arrears=?, morale=? WHERE id=?",
                        (new_arrears, new_morale, army_id))
        reason_tag = (f"{TURN_UNIT}军饷欠发{shortfall}万两" if shortfall > 0 else f"{TURN_UNIT}军饷足额")
        db.conn.executemany(
            """INSERT INTO army_logs
               (turn, year, period, army_id, field, old_value, new_value, delta, reason, event_id, edict_id, actor)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, '户部')""",
            [
                (state.turn, state.year, state.period, army_id, "arrears",
                 str(old_arrears), str(new_arrears), new_arrears - old_arrears, reason_tag),
                (state.turn, state.year, state.period, army_id, "morale",
                 str(old_morale), str(new_morale), new_morale - old_morale, reason_tag),
            ],
        )
        db.conn.commit()
        flows.append({"dir": "expense", "account": "国库", "category": "各军军饷",
                      "army": name, "needed": needed, "paid": pay_current,
                      "shortfall": shortfall, "arrears_delta": new_arrears - old_arrears,
                      "morale_delta": new_morale - old_morale})

    for row in db.conn.execute(
        "SELECT id, name, category, condition, maintenance, output_metric, output_amount FROM buildings"
    ).fetchall():
        name = str(row["name"])
        category = str(row["category"])
        condition = max(0, min(100, int(row["condition"])))
        maintenance = max(0, int(row["maintenance"]))
        metric = str(row["output_metric"])
        out_base = max(0, int(row["output_amount"]))
        produced = round(out_base * condition / 100) if metric and out_base else 0
        if metric in ("国库", "内库") and produced > 0:
            db.record_issue_economy_move(state, metric, produced, "建筑产出", f"{name}{TURN_UNIT}产出")
            flows.append({"dir": "income", "account": metric, "category": "建筑产出",
                          "building": name, "amount": produced})
        elif metric in ("民心", "皇威") and produced > 0:
            before = int(state.metrics.get(metric, 0))
            state.metrics[metric] = max(0, min(100, before + produced))
            flows.append({"dir": "score", "metric": metric, "category": "建筑产出",
                          "building": name, "amount": state.metrics[metric] - before})
        if maintenance > 0:
            maint_account = "内库" if category == "内廷" else "国库"
            paid = db.record_issue_economy_move(state, maint_account, -maintenance, "建筑维护",
                                                f"{name}{TURN_UNIT}维护费")
            flows.append({"dir": "expense", "account": maint_account, "category": "建筑维护",
                          "building": name, "needed": maintenance, "paid": abs(paid),
                          "shortfall": maintenance - abs(paid)})
    return flows


# ── 旬税赋 ───────────────────────────────────────────────────────────────────

def _apply_xun_shares(db: GameDB, state: GameState, upto_xun: int) -> List[Dict[str, object]]:
    """把通用定额行落到第 upto_xun 旬（含）。份额 = floor(总额*k/3)-floor(总额*(k-1)/3)。"""
    applied = kv_int(db, KV_XUN_APPLIED, 0)
    upto = max(0, min(3, int(upto_xun)))
    if upto <= applied:
        return []
    try:
        lines = json.loads(db.kv_get(KV_BUDGET_LINES) or "[]")
    except ValueError:
        lines = []
    flows: List[Dict[str, object]] = []
    for k in range(applied + 1, upto + 1):
        for line in lines:
            total = int(line["amount"])
            share = (total * k) // 3 - (total * (k - 1)) // 3
            if share <= 0:
                continue
            account = str(line["account"])
            name = str(line["name"])
            if line["direction"] == "income":
                actual = db.record_issue_economy_move(
                    state, account, share, name, f"{name}（{k}旬解）")
                flows.append({"dir": "income", "account": account, "amount": actual,
                              "category": name, "reason": f"{name}（{k}旬解）"})
            else:
                actual = db.record_issue_economy_move(
                    state, account, -share, name, f"{name}（{k}旬支）")
                flows.append({"dir": "expense", "account": account, "amount": abs(actual),
                              "category": name, "reason": f"{name}（{k}旬支）"})
    kv_set_int(db, KV_XUN_APPLIED, upto)
    _flows_append(db, flows)
    db.save_state(state)
    return flows


# ── 调度表与因果伏笔 ─────────────────────────────────────────────────────────

def schedule(db: GameDB, kind: str, due_day: int, payload: Dict[str, object], *,
             requires_llm: bool = False, auto_pause: int = 0, created_day: int = 0) -> int:
    cur = db.conn.execute(
        "INSERT INTO scheduled_resolutions (kind, due_day, payload, requires_llm, auto_pause, created_day)"
        " VALUES (?,?,?,?,?,?)",
        (kind, int(due_day), json.dumps(payload, ensure_ascii=False),
         1 if requires_llm else 0, int(auto_pause), int(created_day)),
    )
    db.conn.commit()
    return int(cur.lastrowid)


def _pop_due_resolutions(db: GameDB, state: GameState, day: int, report: DayReport) -> List[Dict[str, object]]:
    rows = db.conn.execute(
        "SELECT * FROM scheduled_resolutions WHERE status='pending' AND due_day<=? ORDER BY due_day, id",
        (int(day),),
    ).fetchall()
    fired: List[Dict[str, object]] = []
    for row in rows:
        try:
            payload = json.loads(row["payload"] or "{}")
        except ValueError:
            payload = {}
        node = {"id": int(row["id"]), "kind": str(row["kind"]),
                "payload": payload, "requires_llm": bool(row["requires_llm"]),
                "auto_pause": int(row["auto_pause"])}
        db.conn.execute(
            "UPDATE scheduled_resolutions SET status='fired', fired_day=? WHERE id=?",
            (int(day), int(row["id"])),
        )
        fired.append(node)
        detail = str(payload.get("detail") or "")
        title = str(payload.get("title") or row["kind"])
        # kind 专属结算（节点不只是通知，还有规则后果）
        if str(row["kind"]) == "intel_arrival":
            try:
                from ming_sim.veil import deliver_intel
                result = deliver_intel(db, state, payload, day)
                title = f"密揭到案：{result.get('summary') or '密查回报'}"
                detail = "密揭已入御案，候上披览。"
            except Exception as exc:
                # 政治代价已付（厂卫 heat），回报不可静默丢失——留痕并以挫败叙事兜底
                from ming_sim.token_stats import tlog
                tlog(f"[veil] 密查回报结算失败（节点#{row['id']}）：{exc}")
                title = "密查无功而返"
                detail = "所遣之人迁延无获，或为人所觉。此行靡费已不可追。"
        level = {2: LEVEL_RED, 1: LEVEL_YELLOW}.get(int(row["auto_pause"]), LEVEL_BLUE)
        report.add(level, str(row["kind"]), title, detail,
                   ref_kind=str(payload.get("ref_kind") or ""),
                   ref_id=str(payload.get("ref_id") or ""))
    db.conn.commit()
    return fired


def plant_causal_seed(db: GameDB, *, created_day: int, fuse_days: int,
                      trigger_gate: Optional[Dict[str, str]] = None,
                      event_spec: Optional[Dict[str, object]] = None,
                      note: str = "") -> int:
    """埋因果伏笔（S10）：fuse_day 后开始逐日查 gate（空 gate=到期即萌发）。"""
    payload = {"event": event_spec or {}}
    cur = db.conn.execute(
        "INSERT INTO causal_seeds (created_day, trigger_gate, fuse_day, payload, note)"
        " VALUES (?,?,?,?,?)",
        (int(created_day), json.dumps(trigger_gate or {}, ensure_ascii=False),
         int(created_day) + max(0, int(fuse_days)),
         json.dumps(payload, ensure_ascii=False), note[:200]),
    )
    db.conn.commit()
    return int(cur.lastrowid)


def _check_causal_seeds(db: GameDB, state: GameState, day: int, report: DayReport) -> None:
    from ming_sim.issues import _gate_passed, event_to_issue
    rows = db.conn.execute(
        "SELECT * FROM causal_seeds WHERE status='armed' AND fuse_day<=?", (int(day),)
    ).fetchall()
    for row in rows:
        try:
            gate = json.loads(row["trigger_gate"] or "{}")
            payload = json.loads(row["payload"] or "{}")
        except ValueError:
            continue
        if gate and not _gate_passed(gate, state.metrics, db):
            continue
        spec = payload.get("event") or {}
        if spec:
            ev = Event(
                id=str(spec.get("id") or f"seed_{row['id']}"),
                title=str(spec.get("title") or "旧策余波"),
                kind=str(spec.get("kind") or "人祸"),
                summary=str(spec.get("summary") or str(row["note"] or "")),
                urgency=int(spec.get("urgency") or 4),
                severity=int(spec.get("severity") or 3),
                credibility=int(spec.get("credibility") or 4),
                interests=list(spec.get("interests") or []),
                audiences=list(spec.get("audiences") or []),
                trigger_gate={"causal_seed": "armed"},
                event_type=str(spec.get("event_type") or "situation"),
                bar_value=int(spec.get("bar_value") or 0),
                region_hint=str(spec.get("region_hint") or ""),
                ongoing_effects=dict(spec.get("ongoing_effects") or {}),
                effect_on_resolve=dict(spec.get("effect_on_resolve") or {}),
                effect_on_fail=dict(spec.get("effect_on_fail") or {}),
            )
            issue_id = event_to_issue(db, state, ev)
            if issue_id is not None:
                report.add(LEVEL_RED, "causal_seed", ev.title, ev.summary,
                           ref_kind="issue", ref_id=str(issue_id))
                db.record_log(state, f"【因果伏笔萌发】{ev.title}")
        db.conn.execute("UPDATE causal_seeds SET status='sprouted' WHERE id=?", (int(row["id"]),))
    db.conn.commit()


# ── 日 tick 与推进 ───────────────────────────────────────────────────────────

def _tick_day(db: GameDB, state: GameState, day: int) -> DayReport:
    """单日规则层 tick。调用方保证 day 在本月窗口内。"""
    from ming_sim.thresholds import scan_thresholds
    report = DayReport(day=day)
    dim = day_in_month(day)

    # 旬税赋（10/20/30 日落第 1/2/3 旬份额）
    if dim in (10, 20, 30):
        flows = _apply_xun_shares(db, state, dim // 10)
        if flows:
            total_in = sum(int(f.get("amount") or 0) for f in flows if f["dir"] == "income")
            total_out = sum(int(f.get("amount") or 0) for f in flows if f["dir"] == "expense")
            report.add(LEVEL_BLUE, "fiscal", f"{dim // 10}旬钱粮入账",
                       f"收{total_in}万两，支{total_out}万两")

    # 指令生命周期推进（M2 起接管；未启用时为空操作）
    try:
        from ming_sim.lifecycle import tick_directives
        for ev in tick_directives(db, state, day):
            report.events.append(ev)
    except ImportError:
        pass

    # 御案奏疏流与注意力（S4/S5）
    try:
        from ming_sim.memorials import memorials_daily_tick
        for ev in memorials_daily_tick(db, state, day):
            report.events.append(ev)
    except ImportError:
        pass

    # 派系主动出招（S7，逢 5/15/25 日）
    try:
        from ming_sim.theater import faction_moves_tick
        for ev in faction_moves_tick(db, state, day):
            report.events.append(ev)
    except ImportError:
        pass

    # 活的宫廷个人级出招（CK3 化 P1，逢 8/18/28 日）：私怨成弹章、朋党相荐。
    try:
        from ming_sim.court import court_moves_tick
        for ev in court_moves_tick(db, state, day):
            report.events.append(ev)
    except ImportError:
        pass

    # 抉择事件（CK3 化 P2）：朝局张力到点弹一道"请陛下裁断"，必停（红）待玩家落子。
    try:
        from ming_sim.court_events import evaluate_decisions
        decision = evaluate_decisions(db, state, day)
        if decision:
            report.add(LEVEL_RED, "decision", f"请陛下裁断：{decision['title']}",
                       str(decision.get("narrative") or "")[:80],
                       ref_kind="decision", ref_id=str(decision.get("id") or ""))
    except ImportError:
        pass

    # 调度表节点
    _pop_due_resolutions(db, state, day, report)

    # 因果伏笔
    _check_causal_seeds(db, state, day, report)

    # 硬阈值（必停红事件）
    for hit in scan_thresholds(db, state, day):
        report.add(LEVEL_RED, "threshold", str(hit["title"]), str(hit.get("summary") or ""),
                   ref_kind="issue", ref_id=str(hit.get("issue_id") or ""))
    return report


def advance_days(db: GameDB, state: GameState, days: int, *,
                 stop_on_yellow: bool = True) -> Dict[str, object]:
    """推进至多 days 天。连续时间：到月末第30日自动零-LLM 跨月（不再硬停等颁诏——
    诏书逐条到期即时复命，月末不再集中结算）。红事件必停；黄事件默认停；跨月按黄停。
    返回 {advanced, reports, stopped_by, status}。"""
    ensure_active(db, state)
    reports: List[DayReport] = []
    stopped_by = ""
    advanced = 0
    for _ in range(max(0, int(days))):
        day = kv_int(db, KV_CURRENT_DAY, 0)
        month_end = state.turn * DAYS_PER_MONTH
        if day >= month_end:
            # 跨月：零-LLM 月度过渡（财政快进/issue 漂移/清帝国修正/next_period/开新月）。
            rollover_month(db, state)
            new_day = kv_int(db, KV_CURRENT_DAY, 0)
            report = DayReport(day=new_day)
            report.add(LEVEL_YELLOW, "month_rollover",
                       f"入{state.year}年{state.period}月", "上月诸事按已发生了结，新月开始。")
            # 王朝长河（CK3 化 P3）：月初按卒年/高龄令在朝官员自然凋零。
            try:
                from ming_sim.lifespan import mortality_tick
                for ev in mortality_tick(db, state, new_day):
                    report.events.append(ev)
            except ImportError:
                pass
            # 派系流动（CK3 化 P5）：改换门庭 + 党内倾轧内耗。
            try:
                from ming_sim.faction_dynamics import defection_tick, strife_tick
                for ev in defection_tick(db, state, new_day):
                    report.events.append(ev)
                strife_tick(db, state, new_day)
            except ImportError:
                pass
            # 后宫自主（CK3 化 P6）：宠妃枕边风（荐人/进谗）+ 诸妃争宠。无妃则空转。
            try:
                from ming_sim.harem import harem_tick
                for ev in harem_tick(db, state, new_day):
                    report.events.append(ev)
            except ImportError:
                pass
            # 地方割据（CK3 化 P7）：边镇离心（欠饷/势弱→阳奉阴违/截留/尾大不掉）。
            try:
                from ming_sim.frontier import autonomy_tick
                for ev in autonomy_tick(db, state, new_day):
                    report.events.append(ev)
            except ImportError:
                pass
            # NPC 持续私人目标（CK3 化 P8）：私心逐月累进，满则成局兑现。
            try:
                from ming_sim.ambition import pursue_tick
                for ev in pursue_tick(db, state, new_day):
                    report.events.append(ev)
            except ImportError:
                pass
            reports.append(report)
            advanced += 1
            _month_events_append(db, report.events)
            if stop_on_yellow:
                stopped_by = "month_rollover"
                break
            continue
        day += 1
        kv_set_int(db, KV_CURRENT_DAY, day)
        report = _tick_day(db, state, day)
        reports.append(report)
        advanced += 1
        _month_events_append(db, report.events)
        levels = {str(e["level"]) for e in report.events}
        if LEVEL_RED in levels:
            stopped_by = "red"
            break
        if stop_on_yellow and LEVEL_YELLOW in levels:
            stopped_by = "yellow"
            break
    return {
        "advanced": advanced,
        "stopped_by": stopped_by,
        "reports": [{"day": r.day, "events": r.events} for r in reports],
        "status": time_status(db, state),
    }


def rollover_month(db: GameDB, state: GameState) -> None:
    """零-LLM 月度过渡（连续时间下到月末自动跑，替代旧的月末 LLM 大结算）：
    快进未落旬份额 + 施加 issue 持续效果（危机消耗）+ 清除已解除的帝国修正 + 跨月开新月。
    诏书效果不在此结算——它们各自到期由 edict_outcome/drain 即时落库。"""
    from ming_sim.issues import apply_issue_inertia_and_ongoing, clear_gated_legacies
    month_events = month_event_log(db)
    month_fixed_flows(db, state)
    try:
        apply_issue_inertia_and_ongoing(db, state, touched_ids=set())
        for name in clear_gated_legacies(db, state):
            db.record_log(state, f"帝国修正消除：{name}")
    except Exception as exc:
        from ming_sim.token_stats import tlog
        tlog(f"[rollover] 持续效果结算异常，跳过：{exc}")
    try:
        record_rollover_chronicle(db, state, month_events=month_events)
    except Exception as exc:
        from ming_sim.token_stats import tlog
        tlog(f"[rollover] 月录留痕异常，跳过：{exc}")
    state.next_period()
    db.save_state(state)
    on_month_resolved(db, state)


def _directive_status_label(row) -> str:
    status = str(row["lifecycle_status"] or "")
    progress = int(row["progress"] or 0)
    outcome = str(row["outcome_status"] or "")
    if status == "done":
        return "已复命" + ("，结果已落库" if outcome == "applied" else "")
    if status == "executing":
        return f"承办中{progress}%"
    if status == "in_transit":
        return f"送达中{progress}%"
    if status == "stalled":
        return f"停摆/封驳{progress}%"
    return status or "已颁"


def _chronicle_tags(directives: List[object], issues: List[object], events: List[Dict[str, object]]) -> List[str]:
    tags: List[str] = ["月录", "连续时间"]
    for row in directives:
        for value in (row["assignee"], row["actor"]):
            clean = str(value or "").strip()
            if clean and clean not in tags:
                tags.append(clean[:40])
        for token in re.findall(r"[\u4e00-\u9fff]{2,4}", str(row["text"] or "")):
            if token in {"内书堂", "锦衣卫", "司礼监", "户部亏空", "辽东", "陕西", "流寇", "海关"} and token not in tags:
                tags.append(token)
    for row in issues:
        title = str(row["title"] or "").strip()
        if title and title not in tags:
            tags.append(title[:40])
        issue_id = f"#{int(row['id'])}"
        if issue_id not in tags:
            tags.append(issue_id)
    for event in events:
        title = str(event.get("title") or "").strip()
        if title and title not in tags:
            tags.append(title[:40])
    return tags[:16]


def record_rollover_chronicle(
    db: GameDB,
    state: GameState,
    *,
    month_events: Optional[List[Dict[str, object]]] = None,
) -> str:
    """Persist a zero-LLM monthly chronicle for continuous-time rollover.

    The old monthly LLM settlement wrote turn_reports, turn_extractions, and a
    chapter memory. Continuous-time rollover is intentionally cheap, but without
    this ledger NPCs and the history UI lose the shared facts of the month.
    """
    month_events = list(month_events or [])
    directives = db.conn.execute(
        """
        SELECT id, actor, assignee, text, lifecycle_status, progress,
               outcome_status, settle_note
        FROM turn_directives
        WHERE turn=? AND status='issued'
        ORDER BY
          CASE lifecycle_status
            WHEN 'stalled' THEN 0
            WHEN 'executing' THEN 1
            WHEN 'in_transit' THEN 2
            WHEN 'done' THEN 3
            ELSE 4
          END,
          id DESC
        LIMIT 24
        """,
        (int(state.turn),),
    ).fetchall()
    issues = db.conn.execute(
        """
        SELECT id, title, status, bar_value, phase, stage_text, last_advance_turn
        FROM issues
        WHERE status='active' OR last_advance_turn=?
        ORDER BY
          CASE status WHEN 'active' THEN 0 ELSE 1 END,
          id
        LIMIT 12
        """,
        (int(state.turn),),
    ).fetchall()
    logs = db.conn.execute(
        """
        SELECT message
        FROM turn_logs
        WHERE turn=?
        ORDER BY id DESC
        LIMIT 8
        """,
        (int(state.turn),),
    ).fetchall()
    memorial_counts = db.conn.execute(
        """
        SELECT status, COUNT(*) AS n
        FROM memorials
        WHERE arrived_day BETWEEN ? AND ?
        GROUP BY status
        """,
        ((int(state.turn) - 1) * DAYS_PER_MONTH + 1, int(state.turn) * DAYS_PER_MONTH),
    ).fetchall()
    memorial_summary = "、".join(f"{row['status']}{int(row['n'])}" for row in memorial_counts) or "无新奏疏留档"

    done = sum(1 for row in directives if str(row["lifecycle_status"] or "") == "done")
    executing = sum(1 for row in directives if str(row["lifecycle_status"] or "") in {"executing", "in_transit"})
    stalled = sum(1 for row in directives if str(row["lifecycle_status"] or "") == "stalled")
    metrics = "、".join(f"{key}{int(state.metrics.get(key, 0))}" for key in ("国库", "内库", "民心", "皇威"))

    lines = [
        f"崇祯{state.year}年{state.period}月月录",
        "",
        f"本月御案追踪圣旨{len(directives)}道：已复命{done}道，仍在承办{executing}道，停摆{stalled}道。奏疏流转：{memorial_summary}。盘面：{metrics}。",
    ]
    if directives:
        lines.append("")
        lines.append("一、圣旨履约")
        for row in directives[:10]:
            assignee = str(row["assignee"] or row["actor"] or "未署主办")
            line = (
                f"- #{int(row['id'])} {assignee}：{_directive_status_label(row)}；"
                f"{_short(row['text'], 92)}"
            )
            settle = _short(row["settle_note"], 88)
            if settle:
                line += f"；复命：{settle}"
            lines.append(line)
    if issues:
        lines.append("")
        lines.append("二、局势余波")
        for row in issues[:8]:
            stage = _short(row["stage_text"], 80)
            lines.append(
                f"- {row['title']}：{row['status']}，{row['phase']}，进度{int(row['bar_value'] or 0)}。{stage}"
            )
    notable_events = [e for e in month_events if e.get("level") in (LEVEL_RED, LEVEL_YELLOW)]
    if notable_events or logs:
        lines.append("")
        lines.append("三、朝局留痕")
        for event in notable_events[:8]:
            detail = _short(event.get("detail"), 70)
            suffix = f"：{detail}" if detail else ""
            lines.append(f"- {event.get('title') or event.get('kind')}{suffix}")
        for row in logs[:6]:
            msg = _short(row["message"], 90)
            if msg and all(msg not in line for line in lines):
                lines.append(f"- {msg}")

    report = "\n".join(lines).strip()
    db.save_turn_report(state, report)
    db.save_turn_extraction(
        state,
        decree_text="\n".join(_short(row["text"], 120) for row in directives[:12]),
        narrative=report,
        extractor_input="zero_llm_continuous_rollover",
        extractor_output=json.dumps({
            "rollover_chronicle": True,
            "metrics": {key: int(state.metrics.get(key, 0)) for key in ("国库", "内库", "民心", "皇威")},
            "directives": [
                {
                    "id": int(row["id"]),
                    "assignee": str(row["assignee"] or row["actor"] or ""),
                    "status": str(row["lifecycle_status"] or ""),
                    "progress": int(row["progress"] or 0),
                    "outcome_status": str(row["outcome_status"] or ""),
                }
                for row in directives
            ],
            "events": month_events[:20],
        }, ensure_ascii=False),
        causal_notes=[],
    )
    tags = _chronicle_tags(directives, issues, month_events)
    body = " ".join(line.lstrip("- ") for line in lines if line and not line.startswith("崇祯"))[:420]
    db.save_chapter_memory(
        state,
        title=f"崇祯{state.year}年{state.period}月月录",
        body=body or report[:420],
        tags=tags,
    )
    db.record_log(state, f"【月录】崇祯{state.year}年{state.period}月已归档，圣旨{len(directives)}道、奏疏流转{memorial_summary}。")
    return report


def _month_events_append(db: GameDB, events: List[Dict[str, object]]) -> None:
    """当月事实账（史官约束的数据源）。蓝色日常流水不记，只记黄/红节点。"""
    keep = [e for e in events if e.get("level") in (LEVEL_RED, LEVEL_YELLOW)]
    if not keep:
        return
    try:
        existing = json.loads(db.kv_get(KV_MONTH_EVENTS) or "[]")
    except ValueError:
        existing = []
    existing.extend(keep)
    db.kv_set(KV_MONTH_EVENTS, json.dumps(existing[-60:], ensure_ascii=False))


def month_event_log(db: GameDB) -> List[Dict[str, object]]:
    """本月已发生的日 tick 事实（哗变立项/旨意异常/办结/伏笔萌发…）。
    喂 simulator：邸报必须把这些当既成事实叙述，不得矛盾、不得重复结算。"""
    try:
        data = json.loads(db.kv_get(KV_MONTH_EVENTS) or "[]")
        return data if isinstance(data, list) else []
    except ValueError:
        return []


# ── 与月结算（decree.resolve_directives）的接驳 ──────────────────────────────

def month_fixed_flows(db: GameDB, state: GameState) -> List[Dict[str, object]]:
    """resolve 时替代 flows.apply_fixed_period_flows：
    - 时间引擎未激活 → 走旧整月落账（完全兼容）。
    - 已激活 → 快进未落的旬份额（提前颁诏不丢钱），返回本月累计 flows 供 simulator。"""
    if not is_active(db):
        from ming_sim.flows import apply_fixed_period_flows
        return apply_fixed_period_flows(db, state)
    ensure_active(db, state)
    _apply_xun_shares(db, state, 3)
    try:
        return json.loads(db.kv_get(KV_MONTH_FLOWS) or "[]")
    except ValueError:
        return []


def on_month_resolved(db: GameDB, state: GameState) -> None:
    """月结算完成（state.next_period 已执行）后调用：跳到新月第 1 日并开月。"""
    if not is_active(db):
        return
    new_day = (state.turn - 1) * DAYS_PER_MONTH + 1
    kv_set_int(db, KV_CURRENT_DAY, new_day)
    _ensure_month_open(db, state, new_day)
