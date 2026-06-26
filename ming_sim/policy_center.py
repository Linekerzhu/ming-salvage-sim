"""Policy-center payloads anchored on doctrine routes.

Doctrines remain defined in ``content/policy_doctrines.json``. Runtime state
continues to live in existing issues, legacies, directives, memorials, and
agreements; this module only gathers those signals into one strategic surface.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ming_sim.db import GameDB
from ming_sim.models import GameState
from ming_sim.policies import doctrine_alignment_summary, doctrine_route_state_cache


def _turn_payload(state: GameState) -> Dict[str, int]:
    return {"year": int(state.year), "period": int(state.period), "turn": int(state.turn)}


def _route_sort_key(route: Dict[str, object]) -> tuple:
    status_rank = {"orthodox": 0, "contested": 1, "latent": 2}
    return (
        status_rank.get(str(route.get("status") or "latent"), 9),
        -int(route.get("bar_value") or 0),
        str(route.get("axis") or ""),
        str(route.get("id") or ""),
    )


def _territory_snapshot(db: GameDB) -> Dict[str, object]:
    rows = db.conn.execute(
        "SELECT id, name, public_support, unrest, gentry_resistance, military_pressure, controlled_by "
        "FROM regions ORDER BY unrest DESC, military_pressure DESC, id"
    ).fetchall()
    ming_rows = [row for row in rows if str(row["controlled_by"] or "ming") == "ming"]
    source = ming_rows or rows
    n = max(1, len(source))
    return {
        "regions_total": len(rows),
        "ming_regions": len(ming_rows),
        "restless_regions": sum(1 for row in source if int(row["unrest"] or 0) >= 35),
        "danger_regions": sum(1 for row in source if int(row["unrest"] or 0) >= 60),
        "avg_unrest": round(sum(int(row["unrest"] or 0) for row in source) / n, 1),
        "avg_support": round(sum(int(row["public_support"] or 0) for row in source) / n, 1),
        "top_risks": [
            {
                "id": str(row["id"]),
                "name": str(row["name"]),
                "unrest": int(row["unrest"] or 0),
                "public_support": int(row["public_support"] or 0),
                "gentry_resistance": int(row["gentry_resistance"] or 0),
                "military_pressure": int(row["military_pressure"] or 0),
                "controlled_by": str(row["controlled_by"] or "ming"),
            }
            for row in rows[:5]
        ],
    }


def _army_snapshot(db: GameDB) -> Dict[str, object]:
    rows = db.conn.execute(
        "SELECT id, name, station, theater, maintenance_per_turn, morale, arrears, autonomy, owner_power "
        "FROM armies WHERE owner_power='ming' ORDER BY arrears DESC, morale ASC, id"
    ).fetchall()
    n = max(1, len(rows))
    arrears_total = sum(int(row["arrears"] or 0) for row in rows)
    return {
        "armies_total": len(rows),
        "monthly_pay": sum(int(row["maintenance_per_turn"] or 0) for row in rows),
        "arrears_total": arrears_total,
        "owing_armies": sum(1 for row in rows if int(row["arrears"] or 0) > 0),
        "avg_morale": round(sum(int(row["morale"] or 0) for row in rows) / n, 1),
        "top_arrears": [
            {
                "id": str(row["id"]),
                "name": str(row["name"]),
                "station": str(row["station"] or ""),
                "theater": str(row["theater"] or ""),
                "monthly_pay": int(row["maintenance_per_turn"] or 0),
                "arrears": int(row["arrears"] or 0),
                "morale": int(row["morale"] or 0),
                "autonomy": int(row["autonomy"] or 0),
            }
            for row in rows
            if int(row["arrears"] or 0) > 0
        ][:5],
    }


def _fiscal_snapshot(fiscal: Optional[Dict[str, Any]]) -> Dict[str, object]:
    if not fiscal:
        return {}
    net = fiscal.get("net_by_account") or {}
    totals = fiscal.get("totals") or {}
    out: Dict[str, object] = {
        "unit": fiscal.get("unit") or "万两/月",
        "accounts": net,
        "province_dynamic_tax": int(totals.get("province_dynamic_tax") or 0),
        "army_arrears": int(totals.get("army_arrears") or 0),
        "operating_gap": int(totals.get("operating_gap") or 0),
        "cash_gap_next_month": int(totals.get("cash_gap_next_month") or 0),
        "explainers": (fiscal.get("explainers") or [])[:5],
    }
    return out


def _route_memorials(db: GameDB, *, limit: int = 40) -> List[Dict[str, object]]:
    rows = db.conn.execute(
        "SELECT m.id, m.author_name, m.org, m.kind, m.urgency, m.summary, m.status, "
        "m.arrived_day, m.ref_id, i.origin_ref AS doctrine_id, i.title AS issue_title "
        "FROM memorials m JOIN issues i ON m.ref_kind='issue' AND CAST(i.id AS TEXT)=m.ref_id "
        "WHERE i.origin_kind='doctrine' "
        "ORDER BY CASE WHEN m.status='pending' THEN 0 ELSE 1 END, m.urgency DESC, m.id DESC "
        "LIMIT ?",
        (max(1, min(100, int(limit))),),
    ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "author": str(row["author_name"] or ""),
            "org": str(row["org"] or ""),
            "kind": str(row["kind"] or ""),
            "urgency": int(row["urgency"] or 0),
            "summary": str(row["summary"] or ""),
            "status": str(row["status"] or ""),
            "arrived_day": int(row["arrived_day"] or 0),
            "issue_id": int(row["ref_id"] or 0),
            "doctrine_id": str(row["doctrine_id"] or ""),
            "issue_title": str(row["issue_title"] or ""),
        }
        for row in rows
    ]


def _route_directives(db: GameDB, *, limit: int = 40) -> List[Dict[str, object]]:
    try:
        from ming_sim.lifecycle import lifecycle_payload
    except Exception:
        return []
    out: List[Dict[str, object]] = []
    for item in lifecycle_payload(db, include_done=True, limit=max(1, min(100, int(limit)))):
        doctrine = item.get("policy_doctrine") if isinstance(item, dict) else {}
        primary = doctrine.get("primary") if isinstance(doctrine, dict) else {}
        doctrine_id = str((primary or {}).get("id") or "")
        if not doctrine_id:
            continue
        out.append({
            "id": int(item.get("id") or 0),
            "text": str(item.get("text") or ""),
            "status": str(item.get("status") or ""),
            "progress": int(item.get("progress") or 0),
            "assignee": str(item.get("assignee") or ""),
            "doctrine_id": doctrine_id,
            "doctrine_name": str((primary or {}).get("name") or doctrine_id),
            "summary": str(doctrine.get("summary") or ""),
            "risk_tags": list(doctrine.get("risk_tags") or [])[:5],
            "establishment_blocked": bool(doctrine.get("establishment_blocked")),
        })
    return out


def _route_agreements(db: GameDB, *, limit: int = 40) -> List[Dict[str, object]]:
    rows = db.list_negotiation_agreements(limit=max(1, min(100, int(limit))))
    policy_kinds = {"policy", "court_commitment", "secret_order", "personnel", "castration", "general"}
    out: List[Dict[str, object]] = []
    status_labels = {
        "pending": "待履约",
        "sealed": "已立约",
        "fulfilled": "已履约",
        "failed": "已负约",
        "blocked": "受阻",
        "waived": "已免除",
        "superseded": "已改约",
    }
    for row in rows:
        status = str(row.get("status") or "")
        if status not in {"pending", "sealed", "fulfilled", "failed", "blocked", "waived", "superseded"}:
            continue
        action_kind = str(row.get("action_kind") or "general")
        if action_kind not in policy_kinds:
            continue
        out.append({
            "id": int(row.get("id") or 0),
            "minister_name": str(row.get("minister_name") or ""),
            "topic": str(row.get("topic") or ""),
            "core_topic": str(row.get("core_topic") or ""),
            "action_kind": action_kind,
            "status": status,
            "status_label": status_labels.get(status, status),
            "condition_status": str(row.get("condition_status") or ""),
            "target_status": str(row.get("target_status") or ""),
            "handshake_status": str(row.get("handshake_status") or ""),
            "due_turn": int(row.get("due_turn") or 0),
            "fulfillment_score": int(row.get("fulfillment_score") or 0),
            "evidence": str(row.get("fulfillment_evidence") or row.get("target_evidence") or ""),
        })
    return out[:limit]


def _work_counts(
    routes: List[Dict[str, object]],
    memorials: List[Dict[str, object]],
    directives: List[Dict[str, object]],
) -> None:
    counts: Dict[str, Dict[str, int]] = {}
    for item in memorials:
        did = str(item.get("doctrine_id") or "")
        counts.setdefault(did, {"memorials": 0, "directives": 0})
        counts[did]["memorials"] += 1
    for item in directives:
        did = str(item.get("doctrine_id") or "")
        counts.setdefault(did, {"memorials": 0, "directives": 0})
        counts[did]["directives"] += 1
    for route in routes:
        route["work_counts"] = counts.get(str(route.get("id") or ""), {"memorials": 0, "directives": 0})


def policy_center_payload(
    db: GameDB,
    state: GameState,
    *,
    fiscal: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    route_states = doctrine_route_state_cache(db)
    routes: List[Dict[str, object]] = []
    for doctrine_id, route in route_states.items():
        item = dict(route)
        item.setdefault("id", doctrine_id)
        item.update(doctrine_alignment_summary(db, doctrine_id, max_factions=4, max_figures=4))
        routes.append(item)
    routes.sort(key=_route_sort_key)

    memorials = _route_memorials(db)
    directives = _route_directives(db)
    agreements = _route_agreements(db)
    _work_counts(routes, memorials, directives)

    orthodox = [row for row in routes if str(row.get("status") or "") == "orthodox"]
    contested = [row for row in routes if str(row.get("status") or "") == "contested"]
    latent = [row for row in routes if str(row.get("status") or "") == "latent"]
    focus = str(db.kv_get("policy.active_focus") or "")
    if not focus:
        focus = str((contested or orthodox or routes or [{}])[0].get("id") or "")

    inner_court_route = next((row for row in routes if str(row.get("id")) == "inner_court_balance"), {})
    return {
        "turn": _turn_payload(state),
        "active_focus_id": focus,
        "route_summary": {
            "orthodox": len(orthodox),
            "contested": len(contested),
            "latent": len(latent),
        },
        "routes": routes,
        "orthodox": orthodox,
        "contested": contested,
        "latent": latent,
        "strategic_snapshot": {
            "fiscal": _fiscal_snapshot(fiscal),
            "territory": _territory_snapshot(db),
            "army": _army_snapshot(db),
        },
        "workstreams": {
            "memorials": memorials,
            "directives": directives,
            "agreements": agreements,
        },
        "inner_court_tools": {
            "route_id": "inner_court_balance",
            "route_status": str(inner_court_route.get("status") or "latent"),
            "required_gate": "奏对承诺、身份转换证据或内廷制衡国策触发后，净身/宦官线才可作为高风险工具链。",
        },
    }
