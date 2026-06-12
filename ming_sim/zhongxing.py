"""节奏层（S12）：中兴指数、阶段诏题、结局光谱。L7。

中兴指数不是胜利条件，而是趋势仪表：财政/边备/流寇/吏治/民心五分项加权，
朔日刷新、留历史折线。阶段诏题把 240 个月切成章节并兼作软教程。
结局光谱按指数+结局类型归档（替代二值结局），喂给史笔定基调。
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional

from ming_sim.db import GameDB
from ming_sim.models import GameState
from ming_sim.paths import bundled_path
from ming_sim.upgrade_schema import (
    KV_DEFICIT_STREAK,
    KV_RISK_AVERSION,
    KV_SHI,
    RISK_AVERSION_DEFAULT,
    adjust_belief,
    kv_int,
)

KV_ZX_HISTORY = "upgrade.zhongxing_history"
KV_GOALS_DONE = "upgrade.stage_goals_done"

_STAGES_CACHE: Optional[Dict[str, object]] = None


def _stages() -> List[Dict[str, object]]:
    global _STAGES_CACHE
    if _STAGES_CACHE is None:
        with open(bundled_path("content", "stage_goals.json"), encoding="utf-8") as fh:
            _STAGES_CACHE = json.load(fh)
    return list(_STAGES_CACHE.get("stages") or [])


# ── 中兴指数 ─────────────────────────────────────────────────────────────────

def _clamp(v: float) -> int:
    return max(0, min(100, round(v)))


def compute_zhongxing(db: GameDB, state: GameState) -> Dict[str, object]:
    # 财政健康：存银（300万两=及格）+ 赤字连月惩罚
    treasury = int(state.metrics.get("国库", 0))
    streak = kv_int(db, KV_DEFICIT_STREAK, 0)
    fiscal = _clamp(treasury / 6.0 - streak * 12)

    # 九边稳定：明军士气/补给均值 - 欠饷月数均值惩罚
    row = db.conn.execute(
        "SELECT AVG(morale) AS m, AVG(supply) AS s, "
        "AVG(CAST(arrears AS REAL)/MAX(1, maintenance_per_turn)) AS am "
        "FROM armies WHERE owner_power='ming' AND maintenance_per_turn>0"
    ).fetchone()
    border = _clamp((float(row["m"] or 50) + float(row["s"] or 50)) / 2 - float(row["am"] or 0) * 8)

    # 流寇压制：三省动乱 + 贼势
    row = db.conn.execute(
        "SELECT AVG(unrest) AS u FROM regions WHERE id IN ('shaanxi','shanxi','henan')"
    ).fetchone()
    bandit_row = db.conn.execute(
        "SELECT AVG(military_strength) AS ms FROM powers WHERE kind IN ('流寇','贼')"
        " OR id LIKE '%bandit%'"
    ).fetchone()
    bandit_strength = float(bandit_row["ms"] or 30)
    suppress = _clamp(100 - float(row["u"] or 40) * 0.7 - bandit_strength * 0.3)

    # 吏治：地方腐败 + 任事意愿
    row = db.conn.execute(
        "SELECT AVG(CAST(json_extract(fiscal,'$.corruption') AS REAL)) AS c "
        "FROM regions WHERE controlled_by='ming'"
    ).fetchone()
    ra = kv_int(db, KV_RISK_AVERSION, RISK_AVERSION_DEFAULT)
    governance = _clamp((100 - float(row["c"] or 50)) * 0.7 + (100 - ra) * 0.3)

    minxin = _clamp(int(state.metrics.get("民心", 50)))

    total = _clamp(fiscal * 0.25 + border * 0.25 + suppress * 0.20
                   + governance * 0.15 + minxin * 0.15)
    return {"total": total, "parts": {"财政": fiscal, "边备": border, "流寇压制": suppress,
                                      "吏治": governance, "民心": minxin}}


def zhongxing_history(db: GameDB) -> List[Dict[str, object]]:
    try:
        data = json.loads(db.kv_get(KV_ZX_HISTORY) or "[]")
        return data if isinstance(data, list) else []
    except ValueError:
        return []


# ── 阶段诏题 ─────────────────────────────────────────────────────────────────

def _gate_value(db: GameDB, state: GameState, key: str) -> Optional[float]:
    if key == "derived.renshi":
        return float(100 - kv_int(db, KV_RISK_AVERSION, RISK_AVERSION_DEFAULT))
    if key == "derived.zhongxing":
        return float(compute_zhongxing(db, state)["total"])
    from ming_sim.thresholds import _global_field
    return _global_field(db, state, key)


def _goal_met(db: GameDB, state: GameState, gate: Dict[str, str]) -> bool:
    from ming_sim.thresholds import _cond_ok
    for key, cond in (gate or {}).items():
        val = _gate_value(db, state, key)
        if val is None or not _cond_ok(val, cond):
            return False
    return True


def _ym_lte(a_year: int, a_month: int, b_year: int, b_month: int) -> bool:
    return (a_year, a_month) <= (b_year, b_month)


def current_stage(state: GameState) -> Optional[Dict[str, object]]:
    for stage in _stages():
        f, t = stage["from"], stage["to"]
        if (_ym_lte(int(f["year"]), int(f["month"]), state.year, state.period)
                and _ym_lte(state.year, state.period, int(t["year"]), int(t["month"]))):
            return stage
    return None


def _goals_done(db: GameDB) -> Dict[str, int]:
    try:
        data = json.loads(db.kv_get(KV_GOALS_DONE) or "{}")
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def evaluate_stage_goals(db: GameDB, state: GameState, day: int) -> List[Dict[str, object]]:
    """朔日评估：当前阶段未完成的诏题达标即完成（势奖励 + 起居注）。"""
    stage = current_stage(state)
    if stage is None:
        return []
    done = _goals_done(db)
    completed: List[Dict[str, object]] = []
    for goal in stage.get("goals") or []:
        gid = str(goal.get("id") or "")
        if not gid or gid in done:
            continue
        if _goal_met(db, state, dict(goal.get("gate") or {})):
            done[gid] = state.turn
            reward = int(goal.get("shi_reward") or 3)
            adjust_belief(db, KV_SHI, reward, f"诏题达成：{goal.get('title')}", day=day)
            db.record_log(state, f"【诏题达成】{stage.get('title')}·{goal.get('title')}（势+{reward}）")
            completed.append({"id": gid, "title": str(goal.get("title")), "shi_reward": reward})
    if completed:
        db.kv_set(KV_GOALS_DONE, json.dumps(done, ensure_ascii=False))
    return completed


def stage_payload(db: GameDB, state: GameState) -> Dict[str, object]:
    stage = current_stage(state)
    done = _goals_done(db)
    if stage is None:
        return {"stage": None, "goals": []}
    goals = []
    for goal in stage.get("goals") or []:
        gid = str(goal.get("id") or "")
        goals.append({
            "id": gid, "title": str(goal.get("title")), "hint": str(goal.get("hint") or ""),
            "done": gid in done, "done_turn": done.get(gid),
        })
    return {
        "stage": {"id": str(stage.get("id")), "title": str(stage.get("title")),
                  "brief": str(stage.get("brief") or "")},
        "goals": goals,
    }


# ── 朔日更新（timeflow 开月时调用）──────────────────────────────────────────

def monthly_update(db: GameDB, state: GameState, day: int) -> Dict[str, object]:
    zx = compute_zhongxing(db, state)
    history = zhongxing_history(db)
    history.append({"turn": state.turn, "year": state.year, "period": state.period,
                    "total": zx["total"], "parts": zx["parts"]})
    db.kv_set(KV_ZX_HISTORY, json.dumps(history[-240:], ensure_ascii=False))
    completed = evaluate_stage_goals(db, state, day)
    return {"zhongxing": zx, "completed_goals": completed}


# ── 结局光谱 ─────────────────────────────────────────────────────────────────

def spectrum_label(db: GameDB, state: GameState, ending_status: str) -> Dict[str, str]:
    """按结局类型 + 中兴指数 + 任事意愿归档结局基调，喂史笔定笔调。"""
    zx = int(compute_zhongxing(db, state)["total"])
    ra = kv_int(db, KV_RISK_AVERSION, RISK_AVERSION_DEFAULT)
    survived = "timeout" in (ending_status or "") or "ongoing" in (ending_status or "")
    if survived:
        if zx >= 65:
            label, tone = "中兴在望", "雄主之姿，明祚复振，史笔当与孝宗弘治并称"
        elif zx >= 45:
            label, tone = "划江守成", "勉力支撑二十年，得失参半，功过待史家折中"
        else:
            label, tone = "苟延残喘", "国脉仅存，回天乏术，唯在位之久可书"
    else:
        if zx >= 50:
            label, tone = "功败垂成", "中道而崩，时也命也，史笔多有惋惜"
        elif ra >= 70:
            label, tone = "乾纲独断的代价", "上多疑好杀，柄臣救过不暇，亡国之因半在用人"
        else:
            label, tone = "回天乏术", "承累朝之敝，势穷力竭，君非亡国之君之论可采"
    return {"label": label, "tone": tone, "zhongxing": str(zx)}
