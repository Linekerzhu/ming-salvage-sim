"""硬阈值系统（S10）：是否发生由规则决定，如何发生由 LLM 叙述。L6。

数据源 content/threshold_rules.json。timeflow 日 tick 调 scan_thresholds()；
前端预警仪表走 threshold_dashboard()。触发即经 issues.event_to_issue 硬立项
（与 auto_trigger seed 同路径），冷却由 threshold_firings 表管。
"""

from __future__ import annotations

import json
import re
from typing import Dict, List, Optional, Tuple

from ming_sim.db import GameDB
from ming_sim.issues import event_to_issue
from ming_sim.models import Event, GameState
from ming_sim.paths import bundled_path
from ming_sim.upgrade_schema import KV_DEFICIT_STREAK, kv_int

_RULES_CACHE: Optional[Dict[str, object]] = None


def load_threshold_rules() -> Dict[str, object]:
    global _RULES_CACHE
    if _RULES_CACHE is None:
        path = bundled_path("content", "threshold_rules.json")
        with open(path, encoding="utf-8") as fh:
            _RULES_CACHE = json.load(fh)
    return _RULES_CACHE


_COND_RE = re.compile(r"^(>=|<=|>|<|==)\s*(-?\d+(?:\.\d+)?)$")


def _cond_ok(value: float, cond: str) -> bool:
    m = _COND_RE.match(str(cond).strip())
    if not m:
        return False
    op, num = m.group(1), float(m.group(2))
    return {
        ">=": value >= num, "<=": value <= num,
        ">": value > num, "<": value < num, "==": value == num,
    }[op]


def _derived_context(db: GameDB, state: GameState) -> Dict[str, float]:
    """global 规则可用的派生量。"""
    return {
        "derived.treasury_deficit_streak": float(kv_int(db, KV_DEFICIT_STREAK, 0)),
    }


def _row_field(row_dict: Dict[str, object], field: str) -> Optional[float]:
    """对象行字段求值，支持派生字段 arrears_months。"""
    if field == "arrears_months":
        maint = float(row_dict.get("maintenance_per_turn") or 0)
        if maint <= 0:
            return None
        return round(float(row_dict.get("arrears") or 0) / maint, 2)
    val = row_dict.get(field)
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _global_field(db: GameDB, state: GameState, field: str) -> Optional[float]:
    if field.startswith("derived."):
        return _derived_context(db, state).get(field)
    if field in state.metrics:
        return float(state.metrics[field])
    # 走 issues 的 gate key 语法（region.x.f / army.x.f / …）
    from ming_sim.issues import _eval_gate_key
    val = _eval_gate_key(field, state.metrics, db)
    return float(val) if val is not None else None


def _expand_scope(db: GameDB, scope: str) -> List[Tuple[str, Dict[str, object], Dict[str, str]]]:
    """返回 [(object_id, 字段行 dict, 占位符映射)]。global 返回单个空对象。"""
    if scope == "ming_armies":
        rows = db.conn.execute(
            "SELECT * FROM armies WHERE owner_power='ming' AND maintenance_per_turn>0"
        ).fetchall()
        return [
            (str(r["id"]), dict(r),
             {"army_id": str(r["id"]), "army_name": str(r["name"])})
            for r in rows
        ]
    if scope == "ming_regions":
        rows = db.conn.execute(
            "SELECT * FROM regions WHERE controlled_by='ming'"
        ).fetchall()
        return [
            (str(r["id"]), dict(r),
             {"region_id": str(r["id"]), "region_name": str(r["name"])})
            for r in rows
        ]
    return [("", {}, {})]


def _fill(template: str, mapping: Dict[str, str]) -> str:
    out = str(template)
    for key, val in mapping.items():
        out = out.replace("{" + key + "}", val)
    return out


def _fill_deep(obj: object, mapping: Dict[str, str]) -> object:
    if isinstance(obj, str):
        return _fill(obj, mapping)
    if isinstance(obj, dict):
        return {k: _fill_deep(v, mapping) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_fill_deep(v, mapping) for v in obj]
    return obj


def _in_cooldown(db: GameDB, rule_id: str, object_id: str, day: int, cooldown_days: int) -> bool:
    row = db.conn.execute(
        "SELECT MAX(fired_day) FROM threshold_firings WHERE rule_id=? AND object_id=?",
        (rule_id, object_id),
    ).fetchone()
    last = int(row[0]) if row and row[0] is not None else None
    return last is not None and day - last < max(1, int(cooldown_days))


def _build_event(rule: Dict[str, object], mapping: Dict[str, str]) -> Event:
    spec = _fill_deep(rule.get("event") or {}, mapping)
    assert isinstance(spec, dict)
    return Event(
        id=str(spec.get("id") or f"th_{rule.get('id')}"),
        title=str(spec.get("title") or rule.get("title") or "阈值危机"),
        kind=str(spec.get("kind") or "危机"),
        summary=str(spec.get("summary") or ""),
        urgency=int(spec.get("urgency") or 5),
        severity=int(spec.get("severity") or 4),
        credibility=int(spec.get("credibility") or 5),
        interests=list(spec.get("interests") or []),
        audiences=list(spec.get("audiences") or []),
        resolve_condition=str(spec.get("resolve_condition") or ""),
        fail_condition=str(spec.get("fail_condition") or ""),
        # 标非空 gate → event_to_issue 走「仅查 active 同源」去重，结案后可再触发
        trigger_gate={"threshold": "hard"},
        event_type=str(spec.get("event_type") or "situation"),
        bar_value=int(spec.get("bar_value") or 0),
        bar_good_meaning=str(spec.get("bar_good_meaning") or ""),
        bar_bad_meaning=str(spec.get("bar_bad_meaning") or ""),
        issue_inertia=int(spec.get("issue_inertia") or 0),
        region_hint=str(spec.get("region_hint") or ""),
        issue_tags=list(spec.get("issue_tags") or []),
        ongoing_effects=dict(spec.get("ongoing_effects") or {}),
        effect_on_resolve=dict(spec.get("effect_on_resolve") or {}),
        effect_on_fail=dict(spec.get("effect_on_fail") or {}),
    )


def scan_thresholds(db: GameDB, state: GameState, day: int) -> List[Dict[str, object]]:
    """日 tick 扫描：conditions 全满足且不在冷却 → 硬立项。返回本次触发清单。"""
    fired: List[Dict[str, object]] = []
    rules = load_threshold_rules().get("rules") or []
    for rule in rules:  # type: ignore[assignment]
        rule_id = str(rule.get("id") or "")
        scope = str(rule.get("scope") or "global")
        conditions: Dict[str, str] = dict(rule.get("conditions") or {})
        cooldown = int(rule.get("cooldown_days") or 180)
        if not rule_id or not conditions:
            continue
        for object_id, row, mapping in _expand_scope(db, scope):
            if _in_cooldown(db, rule_id, object_id, day, cooldown):
                continue
            ok = True
            for field, cond in conditions.items():
                value = (
                    _global_field(db, state, field) if scope == "global"
                    else _row_field(row, field)
                )
                if value is None or not _cond_ok(value, cond):
                    ok = False
                    break
            if not ok:
                continue
            ev = _build_event(rule, mapping)
            issue_id = event_to_issue(db, state, ev)
            db.conn.execute(
                "INSERT OR IGNORE INTO threshold_firings (rule_id, object_id, fired_day) VALUES (?,?,?)",
                (rule_id, object_id, int(day)),
            )
            db.conn.commit()
            if issue_id is not None:
                db.record_log(state, f"【硬阈值触发】{ev.title}（规则 {rule_id}）")
                fired.append({
                    "rule_id": rule_id, "object_id": object_id,
                    "issue_id": issue_id, "title": ev.title,
                    "summary": ev.summary,
                })
    return fired


def threshold_dashboard(db: GameDB, state: GameState, day: int) -> List[Dict[str, object]]:
    """预警仪表：每条 watch 项的当前值/触发线/状态（safe|warn|danger）。

    注意：仪表读的是各衙门呈报口径（账面值）——账被做假时仪表同样会骗你（S3 设计统一）。
    """
    out: List[Dict[str, object]] = []
    rules = load_threshold_rules().get("rules") or []
    for rule in rules:  # type: ignore[assignment]
        rule_id = str(rule.get("id") or "")
        scope = str(rule.get("scope") or "global")
        watches = list(rule.get("watch") or [])
        if not watches:
            continue
        for object_id, row, mapping in _expand_scope(db, scope):
            gauges: List[Dict[str, object]] = []
            worst = "safe"
            for w in watches:
                field = str(w.get("field") or "")
                value = (
                    _global_field(db, state, field) if scope == "global"
                    else _row_field(row, field)
                )
                if value is None:
                    continue
                danger_at = float(w.get("danger_at") or 0)
                warn_ratio = float(w.get("warn_ratio") or 0.7)
                direction = str(w.get("direction") or "above")
                if direction == "above":
                    status = ("danger" if value >= danger_at
                              else "warn" if danger_at > 0 and value >= danger_at * warn_ratio
                              else "safe")
                else:
                    status = ("danger" if value <= danger_at
                              else "warn" if value <= danger_at * warn_ratio
                              else "safe")
                gauges.append({
                    "field": field, "label": str(w.get("label") or field),
                    "value": round(value, 2), "danger_at": danger_at,
                    "direction": direction, "status": status,
                })
                if status == "danger" or (status == "warn" and worst == "safe"):
                    worst = status
            if not gauges:
                continue
            # 全部 gauge 同时 danger 才意味着必然触发；单项 danger 仍按最差色显示
            all_danger = all(g["status"] == "danger" for g in gauges)
            out.append({
                "rule_id": rule_id,
                "object_id": object_id,
                "title": _fill(str(rule.get("title") or rule_id), mapping),
                "status": "danger" if all_danger else worst,
                "in_cooldown": _in_cooldown(db, rule_id, object_id, day,
                                            int(rule.get("cooldown_days") or 180)),
                "gauges": gauges,
            })
    # danger 在前，warn 次之
    rank = {"danger": 0, "warn": 1, "safe": 2}
    out.sort(key=lambda item: rank.get(str(item["status"]), 3))
    return out
