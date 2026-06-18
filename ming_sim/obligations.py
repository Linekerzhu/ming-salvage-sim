"""Monthly pressure for unresolved audience obligations.

Court promises should not sit in the ledger as inert reminders.  This module
turns stale conversation goals into small deterministic consequences so the
player feels the court remembering, waiting, and resenting delay.
"""

from __future__ import annotations

import json
from typing import Dict, List, Tuple

from ming_sim.db import GameDB
from ming_sim.models import GameState


_ACTIVE_STATUSES = ["active", "waiting_conditions", "blocked", "expired"]


def _short(text: object, limit: int = 80) -> str:
    raw = " ".join(str(text or "").strip().split())
    if len(raw) <= limit:
        return raw
    return raw[: max(1, limit - 1)] + "…"


def _goal_label(goal: Dict[str, object]) -> str:
    from ming_sim.context import _goal_followup_flavor

    flavor = _goal_followup_flavor(goal)
    reason = str(flavor.get("reason_type") or "")
    if "patronage" in reason:
        return "举主担保"
    if "co_work" in reason:
        return "共办回奏"
    if "policy_audit" in reason:
        return "旧政清查"
    if "resource_support" in reason:
        return "资源复办"
    if "secret_evidence" in reason:
        return "补证密令"
    if "favor_service" in reason:
        return "偿恩差使"
    if "petition_service" in reason:
        return "难差自证"
    return "奏对旧约"


def _already_pressured_this_turn(db: GameDB, goal_id: int, turn: int) -> bool:
    row = db.conn.execute(
        """
        SELECT 1
        FROM conversation_goal_events
        WHERE goal_id=? AND turn=? AND event_kind='monthly_pressure'
        LIMIT 1
        """,
        (int(goal_id), int(turn)),
    ).fetchone()
    return row is not None


def _active_character_exists(db: GameDB, name: str) -> bool:
    row = db.conn.execute(
        "SELECT 1 FROM characters WHERE name=? AND status='active' LIMIT 1",
        (str(name or "").strip(),),
    ).fetchone()
    return row is not None


def _pressure_kind(goal: Dict[str, object], state: GameState) -> Tuple[str, int]:
    created = int(goal.get("created_turn") or 0)
    expires = int(goal.get("expires_turn") or 0)
    age = max(0, int(state.turn) - created)
    if expires and int(state.turn) >= expires:
        return "overdue", age
    if age >= 3:
        return "stale", age
    return "", age


def _mark_pending_conditions_failed(goal: Dict[str, object], note: str) -> List[Dict[str, object]]:
    out: List[Dict[str, object]] = []
    for raw in goal.get("conditions") or []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        if str(item.get("status") or "") == "pending":
            item["status"] = "failed"
            item["evidence"] = note
        out.append(item)
    return out


def _blocked_json(goal: Dict[str, object], blocker: str) -> str:
    blockers = [str(item).strip() for item in (goal.get("blockers") or []) if str(item).strip()]
    if blocker not in blockers:
        blockers.append(blocker)
    return json.dumps(blockers[-8:], ensure_ascii=False)


def _clamp_opinion(value: int) -> int:
    return max(-100, min(100, int(value)))


def _shift_existing_opinion(db: GameDB, a: str, b: str, delta: int, day: int) -> bool:
    row = db.conn.execute(
        "SELECT opinion FROM relationships WHERE a_name=? AND b_name=?",
        (str(a or "").strip(), str(b or "").strip()),
    ).fetchone()
    if row is None:
        return False
    db.conn.execute(
        "UPDATE relationships SET opinion=?, updated_day=? WHERE a_name=? AND b_name=?",
        (_clamp_opinion(int(row["opinion"] or 0) + int(delta)), int(day), a, b),
    )
    return True


def _relationship_ripple(db: GameDB, name: str, *, overdue: bool, day: int) -> Dict[str, List[str]]:
    """Let a failed promise tug at the existing NPC relationship web."""

    try:
        from ming_sim import court
    except ImportError:
        return {"allies": [], "rivals": []}
    touched: Dict[str, List[str]] = {"allies": [], "rivals": []}
    ally_delta = 2 if overdue else 1
    rival_delta = -2 if overdue else -1
    for ally in court.allies_of(db, name, limit=3):
        other = str(ally.get("name") or "").strip()
        if not other:
            continue
        changed = _shift_existing_opinion(db, other, name, ally_delta, day)
        changed = _shift_existing_opinion(db, name, other, max(1, ally_delta - 1), day) or changed
        if changed:
            touched["allies"].append(other)
    for rival in court.rivals_of(db, name, limit=3):
        other = str(rival.get("name") or "").strip()
        if not other:
            continue
        changed = _shift_existing_opinion(db, other, name, rival_delta, day)
        changed = _shift_existing_opinion(db, name, other, min(-1, rival_delta + 1), day) or changed
        if changed:
            touched["rivals"].append(other)
    if touched["allies"] or touched["rivals"]:
        db.conn.commit()
    return touched


def obligation_pressure_tick(
    db: GameDB,
    state: GameState,
    day: int,
    *,
    limit: int = 6,
) -> List[Dict[str, object]]:
    """Apply month-start pressure to unresolved conversation goals.

    Returns timeflow-compatible event dicts.  The function is idempotent per
    goal per turn through conversation_goal_events.
    """

    candidates: List[Tuple[int, str, int, Dict[str, object]]] = []
    for goal in db.list_conversation_goals(statuses=_ACTIVE_STATUSES, limit=120):
        goal_id = int(goal.get("id") or 0)
        name = str(goal.get("minister_name") or "").strip()
        if goal_id <= 0 or not name or not _active_character_exists(db, name):
            continue
        if _already_pressured_this_turn(db, goal_id, int(state.turn)):
            continue
        kind, age = _pressure_kind(goal, state)
        if not kind:
            continue
        expires = int(goal.get("expires_turn") or 0)
        priority = (40 if kind == "overdue" else 12) + age + (8 if expires else 0)
        candidates.append((priority, kind, age, goal))

    events: List[Dict[str, object]] = []
    for _, kind, age, goal in sorted(candidates, key=lambda item: item[0], reverse=True)[: max(1, int(limit))]:
        goal_id = int(goal.get("id") or 0)
        name = str(goal.get("minister_name") or "").strip()
        title = str(goal.get("title") or goal.get("target_text") or "未竟奏对").strip()
        label = _goal_label(goal)
        overdue = kind == "overdue"
        blocker = (
            f"{label}已逾期未复命，须召对追问责任与证据。"
            if overdue
            else f"{label}拖延已久，廷臣开始观望皇帝是否追问。"
        )
        summary = (
            f"{name}的{label}「{_short(title, 46)}」"
            + ("到期未交账，怨望渐生。" if overdue else "久无下文，心中疑惧。")
        )
        last_delta = dict(goal.get("last_delta") or {})
        last_delta["monthly_pressure"] = {
            "kind": kind,
            "label": label,
            "turn": int(state.turn),
            "age": int(age),
            "trust_delta": -2 if overdue else -1,
            "grievance_delta": 5 if overdue else 2,
        }
        ripple = _relationship_ripple(db, name, overdue=overdue, day=day)
        if ripple["allies"] or ripple["rivals"]:
            last_delta["monthly_pressure"]["network_touch"] = ripple
        fields: Dict[str, object] = {
            "last_delta_json": last_delta,
            "blockers_json": _blocked_json(goal, blocker),
        }
        if overdue:
            fields.update({
                "status": "blocked",
                "condition_status": "blocked",
                "conditions_json": _mark_pending_conditions_failed(goal, blocker),
            })
        db.conn.execute(
            """
            UPDATE characters
            SET emp_trust=MAX(0, emp_trust+?),
                grievance=MIN(100, grievance+?)
            WHERE name=? AND status='active'
            """,
            (-2 if overdue else -1, 5 if overdue else 2, name),
        )
        db.update_conversation_goal(
            goal_id,
            state=state,
            event_kind="monthly_pressure",
            event_summary=summary,
            **fields,
        )
        db.record_log(state, f"奏对承诺发酵：{summary}")
        events.append({
            "level": "yellow" if overdue else "blue",
            "kind": "conversation_goal_pressure",
            "title": f"旧约发酵：{name}",
            "detail": summary,
            "ref_kind": "conversation_goal",
            "ref_id": str(goal_id),
            "day": int(day),
            "actor": name,
            "effects": [
                {"kind": "trust", "label": "信任 -2" if overdue else "信任 -1", "tone": "bad"},
                {"kind": "grievance", "label": "怨望 +5" if overdue else "怨望 +2", "tone": "bad"},
                {"kind": "obligation", "label": label, "tone": "warn"},
                *(
                    [{"kind": "relationship", "label": "关系网震荡", "tone": "warn"}]
                    if ripple["allies"] or ripple["rivals"] else []
                ),
            ],
        })

    return events
