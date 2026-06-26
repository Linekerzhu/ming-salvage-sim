"""Occupational disease, injury, death, and private-scandal risk events.

This module is intentionally rules-only.  It turns already-established duties
and offices into sparse, deterministic risk events, then projects the physical
consequences into ``character_conditions`` and private-morality consequences
into ``secrets``.  It does not ask the LLM to invent random illness or death.
"""

from __future__ import annotations

import json
import random
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ming_sim.db import GameDB
from ming_sim.effect_catalog import normalize_task_risk_profile, task_risk_domains, task_risk_events
from ming_sim.models import GameState


MAX_EVENTS_PER_MONTH = 4
MAX_MAJOR_EVENTS_PER_MONTH = 1
KV_MONTH_STATE = "occupational_risks.month_state"

_EVENT_TITLES = {
    "riding_fall": "差遣伤病：坠马伤",
    "desk_strain": "差遣伤病：案牍劳形",
    "urinary_strain": "差遣伤病：癃闭",
    "stress_breakdown": "差遣伤病：心神失常",
    "debauchery_stroke": "暴疾：中风猝发",
    "private_morality_secret": "私德风闻：私情违礼",
}


def _clean(value: object, limit: int = 160) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())[:limit]


def _age(row: Dict[str, object], state: GameState) -> int:
    try:
        birth = int(row.get("birth_year") or 0)
    except (TypeError, ValueError):
        birth = 0
    return int(state.year) - birth if birth else 0


def _int(row: Dict[str, object], key: str, default: int = 0) -> int:
    try:
        return int(row.get(key) if row.get(key) is not None else default)
    except (TypeError, ValueError):
        return default


def _json_dict(raw: object) -> Dict[str, object]:
    if isinstance(raw, dict):
        return dict(raw)
    if not raw:
        return {}
    try:
        data = json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _json_list(raw: object) -> List[object]:
    if isinstance(raw, list):
        return list(raw)
    if not raw:
        return []
    try:
        data = json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _month_state(db: GameDB, state: GameState) -> Dict[str, object]:
    data = _json_dict(db.kv_get(KV_MONTH_STATE))
    if int(data.get("turn") or 0) != int(state.turn):
        data = {"turn": int(state.turn), "events": 0, "major": 0, "names": []}
        db.kv_set(KV_MONTH_STATE, json.dumps(data, ensure_ascii=False, sort_keys=True))
    if not isinstance(data.get("names"), list):
        data["names"] = []
    return data


def _save_month_state(db: GameDB, data: Dict[str, object]) -> None:
    db.kv_set(KV_MONTH_STATE, json.dumps(data, ensure_ascii=False, sort_keys=True))


def _month_budget_allows(db: GameDB, state: GameState, name: str, *, major: bool) -> bool:
    data = _month_state(db, state)
    if int(data.get("events") or 0) >= MAX_EVENTS_PER_MONTH:
        return False
    if major and int(data.get("major") or 0) >= MAX_MAJOR_EVENTS_PER_MONTH:
        return False
    names = {str(item) for item in (data.get("names") or [])}
    return str(name or "") not in names


def _mark_month_event(db: GameDB, state: GameState, name: str, *, major: bool) -> None:
    data = _month_state(db, state)
    names = [str(item) for item in (data.get("names") or []) if str(item)]
    if name and name not in names:
        names.append(name)
    data["names"] = names[-80:]
    data["events"] = int(data.get("events") or 0) + 1
    if major:
        data["major"] = int(data.get("major") or 0) + 1
    _save_month_state(db, data)


def _condition_burden(db: GameDB, name: str) -> Tuple[int, int]:
    try:
        row = db.conn.execute(
            """
            SELECT COUNT(*) AS n, COALESCE(MAX(severity), 0) AS max_sev
            FROM character_conditions
            WHERE name=? AND stage NOT IN ('resolved', 'recovering', 'dead')
            """,
            (name,),
        ).fetchone()
    except Exception:
        return 0, 0
    if row is None:
        return 0, 0
    return int(row["n"] or 0), int(row["max_sev"] or 0)


def _active_duty_counts(db: GameDB) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    try:
        rows = db.conn.execute(
            """
            SELECT assignee AS name, COUNT(*) AS n
            FROM turn_directives
            WHERE lifecycle_status IN ('in_transit', 'executing', 'stalled')
              AND COALESCE(assignee, '')!=''
            GROUP BY assignee
            """
        ).fetchall()
        for row in rows:
            counts[str(row["name"])] = counts.get(str(row["name"]), 0) + int(row["n"] or 0)
    except Exception:
        pass
    try:
        rows = db.conn.execute(
            """
            SELECT minister_name AS name, COUNT(*) AS n
            FROM secret_orders
            WHERE status IN ('active', 'pending_review')
            GROUP BY minister_name
            """
        ).fetchall()
        for row in rows:
            counts[str(row["name"])] = counts.get(str(row["name"]), 0) + int(row["n"] or 0)
    except Exception:
        pass
    try:
        rows = db.conn.execute(
            """
            SELECT a.minister_name AS name, COUNT(*) AS n
            FROM negotiation_agreements a
            JOIN negotiation_tasks t ON t.agreement_id=a.id
            WHERE a.status IN ('pending', 'sealed')
              AND a.target_status='pending_conditions'
              AND t.status='pending'
            GROUP BY a.minister_name
            """
        ).fetchall()
        for row in rows:
            counts[str(row["name"])] = counts.get(str(row["name"]), 0) + int(row["n"] or 0)
    except Exception:
        pass
    return counts


def _domains_for(candidate: Dict[str, object]) -> set[str]:
    profile = normalize_task_risk_profile(
        candidate.get("task_risk_profile") or candidate.get("risk_profile_json"),
        actor=str(candidate.get("name") or ""),
    )
    if not profile or float(profile.get("confidence") or 0) < 0.5:
        candidate["task_risk_profile"] = {}
        return set()
    candidate["task_risk_profile"] = profile
    return set(task_risk_domains(profile))


def _risk_score(candidate: Dict[str, object], domains: Iterable[str], state: GameState) -> int:
    age = _age(candidate, state)
    hp = _int(candidate, "hp", 100)
    ability = _int(candidate, "ability", 50)
    courage = _int(candidate, "courage", 50)
    force = _int(candidate, "force", 50)
    wisdom = _int(candidate, "wisdom", 50)
    luck = _int(candidate, "luck", 50)
    integrity = _int(candidate, "integrity", 50)
    condition_count = _int(candidate, "condition_count", 0)
    condition_max = _int(candidate, "condition_max", 0)
    duty_count = _int(candidate, "duty_count", 1)
    source_kind = str(candidate.get("source_kind") or "")
    domain_set = set(domains)
    profile = candidate.get("task_risk_profile") if isinstance(candidate.get("task_risk_profile"), dict) else {}
    pressure = _int(profile, "pressure", 50)
    score = 22
    score += max(0, pressure - 50) // 3
    if source_kind == "directive":
        score += 14
    elif source_kind == "secret_order":
        score += 16
    elif source_kind == "agreement_task":
        score += 10
    elif source_kind == "office":
        score += 5
    if "mounted" in domain_set:
        score += max(0, 55 - force) // 4 + max(0, 54 - luck) // 5 + 8
    if "desk" in domain_set:
        score += max(0, age - 38) // 4 + max(0, 62 - hp) // 5 + 7
    if "stress" in domain_set:
        score += max(0, 55 - courage) // 4 + max(0, 55 - wisdom) // 5 + duty_count * 3 + 7
    if "debauchery" in domain_set:
        score += max(0, 45 - integrity) // 2 + 5
    if "private_morality" in domain_set and source_kind != "office":
        score += 2
    if "high_office" in domain_set:
        score += 4
    if age >= 55:
        score += min(18, (age - 50) // 2)
    if hp <= 75:
        score += (75 - hp) // 4
    score += min(15, condition_count * 3 + condition_max * 2)
    score += min(10, max(0, duty_count - 1) * 4)
    return max(0, min(100, score))


def _eligible_events(candidate: Dict[str, object], domains: Iterable[str]) -> List[Tuple[str, int]]:
    profile = candidate.get("task_risk_profile")
    if isinstance(profile, dict) and profile:
        events = task_risk_events(profile)
        if events:
            return events
    domain_set = set(domains)
    events: List[Tuple[str, int]] = []
    if "mounted" in domain_set:
        events.append(("riding_fall", 40))
    if "desk" in domain_set:
        events.append(("desk_strain", 26))
        events.append(("urinary_strain", 14))
    if "stress" in domain_set:
        events.append(("stress_breakdown", 30))
    if "debauchery" in domain_set:
        events.append(("debauchery_stroke", 5))
    if "private_morality" in domain_set:
        events.append(("private_morality_secret", 4))
    return events


def _choose_weighted(rng: random.Random, events: Sequence[Tuple[str, int]]) -> str:
    total = sum(max(0, int(weight)) for _, weight in events)
    if total <= 0:
        return events[0][0] if events else ""
    needle = rng.uniform(0, total)
    seen = 0.0
    for key, weight in events:
        seen += max(0, int(weight))
        if needle <= seen:
            return key
    return events[-1][0]


def _event_probability(candidate: Dict[str, object]) -> float:
    score = int(candidate.get("risk_score") or 0)
    if score < 48:
        return 0.0
    prob = min(0.24, (score - 44) / 260.0)
    if str(candidate.get("source_kind") or "") == "office":
        prob *= 0.35
    return max(0.0, min(0.24, prob))


def _character_payload(row: Any) -> Dict[str, object]:
    return {key: row[key] for key in row.keys()}


def collect_occupational_risk_candidates(db: GameDB, state: GameState, day: int) -> List[Dict[str, object]]:
    """Collect duty-bearing candidates for the daily occupational risk tick."""

    duty_counts = _active_duty_counts(db)
    candidates: List[Dict[str, object]] = []

    def add_candidate(base: Dict[str, object]) -> None:
        name = _clean(base.get("name"), 80)
        if not name:
            return
        condition_count, condition_max = _condition_burden(db, name)
        base["name"] = name
        base["duty_count"] = max(1, duty_counts.get(name, 0))
        base["condition_count"] = condition_count
        base["condition_max"] = condition_max
        domains = _domains_for(base)
        if not domains:
            return
        events = _eligible_events(base, domains)
        if not events:
            return
        base["domains"] = sorted(domains)
        base["risk_score"] = _risk_score(base, domains, state)
        if base.get("risk_score_bonus"):
            base["risk_score"] = min(100, int(base.get("risk_score") or 0) + int(base.get("risk_score_bonus") or 0))
        if _event_probability(base) <= 0:
            return
        candidates.append(base)

    try:
        rows = db.conn.execute(
            """
            SELECT
                d.id AS source_id, d.text AS task_text, d.category AS category,
                d.lifecycle_status AS lifecycle_status, d.progress AS progress,
                d.assignee AS name, d.exec_days AS exec_days, d.eta_day AS eta_day,
                d.start_day AS start_day, d.lead_days AS lead_days,
                d.risk_profile_json AS risk_profile_json,
                c.office AS office, c.office_type AS office_type, c.faction AS faction,
                c.sex AS sex, c.birth_year AS birth_year, c.hp AS hp,
                c.ability AS ability, c.integrity AS integrity, c.courage AS courage,
                c.force AS force, c.wisdom AS wisdom, c.luck AS luck
            FROM turn_directives d
            JOIN characters c ON c.name=d.assignee
            WHERE d.lifecycle_status IN ('in_transit', 'executing', 'stalled')
              AND c.status='active'
              AND c.power_id='ming'
              AND c.office_type!='后宫'
              AND COALESCE(d.assignee, '')!=''
            ORDER BY d.id DESC
            LIMIT 40
            """
        ).fetchall()
        for row in rows:
            item = _character_payload(row)
            item["source_kind"] = "directive"
            item["source_id"] = str(item.get("source_id") or "")
            add_candidate(item)
    except Exception:
        pass

    try:
        rows = db.conn.execute(
            """
            SELECT
                o.id AS source_id, o.title AS title, o.content AS task_text,
                o.tags AS tags, o.status AS order_status, o.due_turn AS due_turn,
                o.risk_profile_json AS risk_profile_json,
                o.minister_name AS name,
                c.office AS office, c.office_type AS office_type, c.faction AS faction,
                c.sex AS sex, c.birth_year AS birth_year, c.hp AS hp,
                c.ability AS ability, c.integrity AS integrity, c.courage AS courage,
                c.force AS force, c.wisdom AS wisdom, c.luck AS luck
            FROM secret_orders o
            JOIN characters c ON c.name=o.minister_name
            WHERE o.status IN ('active', 'pending_review')
              AND c.status='active'
              AND c.power_id='ming'
              AND c.office_type!='后宫'
            ORDER BY o.id DESC
            LIMIT 40
            """
        ).fetchall()
        for row in rows:
            item = _character_payload(row)
            tags = " ".join(str(part) for part in _json_list(item.get("tags")) if str(part))
            item["task_text"] = f"{item.get('title') or ''} {item.get('task_text') or ''} {tags}"
            item["source_kind"] = "secret_order"
            item["source_id"] = str(item.get("source_id") or "")
            if int(item.get("due_turn") or 0) and int(item.get("due_turn") or 0) <= int(state.turn):
                item["risk_score_bonus"] = 8
            add_candidate(item)
    except Exception:
        pass

    try:
        rows = db.conn.execute(
            """
            SELECT
                t.id AS task_id, a.id AS agreement_id, t.description AS task_text,
                t.risk_profile_json AS risk_profile_json,
                a.action_kind AS category, a.due_turn AS due_turn, a.minister_name AS name,
                c.office AS office, c.office_type AS office_type, c.faction AS faction,
                c.sex AS sex, c.birth_year AS birth_year, c.hp AS hp,
                c.ability AS ability, c.integrity AS integrity, c.courage AS courage,
                c.force AS force, c.wisdom AS wisdom, c.luck AS luck
            FROM negotiation_agreements a
            JOIN negotiation_tasks t ON t.agreement_id=a.id
            JOIN characters c ON c.name=a.minister_name
            WHERE a.status IN ('pending', 'sealed')
              AND a.target_status='pending_conditions'
              AND t.status='pending'
              AND c.status='active'
              AND c.power_id='ming'
              AND c.office_type!='后宫'
            ORDER BY t.id DESC
            LIMIT 40
            """
        ).fetchall()
        for row in rows:
            item = _character_payload(row)
            item["source_kind"] = "agreement_task"
            item["source_id"] = str(item.get("task_id") or "")
            item["agreement_id"] = int(item.get("agreement_id") or 0)
            add_candidate(item)
    except Exception:
        pass

    return sorted(candidates, key=lambda cand: (-int(cand.get("risk_score") or 0), str(cand.get("name") or "")))


def _source_id(candidate: Dict[str, object], event_key: str) -> str:
    return f"{candidate.get('source_kind') or 'unknown'}:{candidate.get('source_id') or candidate.get('name') or ''}:{event_key}"


def _source_label(candidate: Dict[str, object]) -> str:
    labels = {
        "directive": "旨意",
        "secret_order": "密令",
        "agreement_task": "履约事项",
        "office": "职责",
    }
    return labels.get(str(candidate.get("source_kind") or ""), "差遣")


def _event_severity(candidate: Dict[str, object], event_key: str, rng: random.Random) -> int:
    risk = int(candidate.get("risk_score") or 0)
    if event_key in {"debauchery_stroke"}:
        return 5
    if event_key in {"private_morality_secret"}:
        return 0
    if event_key == "riding_fall":
        return 5 if risk >= 86 or rng.random() < 0.18 else 4
    if event_key == "stress_breakdown":
        return 5 if risk >= 90 else 4 if risk >= 72 or rng.random() < 0.20 else 3
    if event_key == "urinary_strain":
        return 3 if risk >= 70 else 2
    return 3 if risk >= 68 else 2


def _condition_payload(candidate: Dict[str, object], event_key: str, severity: int, day: int) -> Optional[Dict[str, object]]:
    name = str(candidate.get("name") or "")
    source = _source_label(candidate)
    task = _clean(candidate.get("task_text"), 120)
    if event_key == "riding_fall":
        if severity >= 5:
            return {
                "name": name,
                "kind": "disability",
                "system": "nervous",
                "condition_key": "occupational:mounted:paralysis",
                "label": "下肢瘫痪",
                "severity": 5,
                "stage": "disabled",
                "note": f"承办{source}途中坠马伤及腰脊",
                "effects": {
                    "record_group": "pathological",
                    "organ": "腰脊",
                    "state": "损伤",
                    "function": "行走",
                    "impact": "下肢瘫痪，难以行走、久立或亲赴差遣",
                    "ability_delta": "外出、军务与长途承办能力大幅下降",
                    "course_kind": "chronic",
                    "possible_outcomes": ["长期伤残", "缓慢恢复"],
                },
                "chronic": True,
            }
        return {
            "name": name,
            "kind": "injury",
            "system": "musculoskeletal",
            "condition_key": "occupational:mounted:fall",
            "label": "坠马伤",
            "severity": severity,
            "stage": "serious",
            "note": f"承办{source}途中坠马；{task}",
            "effects": {
                "record_group": "other",
                "organ": "腰腿",
                "state": "挫伤",
                "function": "行走",
                "impact": "腰腿疼痛，外出和久立差遣受限",
                "ability_delta": "外出承办与军务机动下降",
                "course_kind": "acute",
                "next_check_day": int(day) + 10,
                "possible_outcomes": ["恢复", "加重"],
            },
            "duration_days": 10,
        }
    if event_key == "desk_strain":
        return {
            "name": name,
            "kind": "disease",
            "system": "musculoskeletal",
            "condition_key": "occupational:desk:lumbar",
            "label": "腰脊劳损",
            "severity": severity,
            "stage": "chronic",
            "note": f"案牍劳形，久坐久立损及腰脊；{task}",
            "effects": {
                "record_group": "pathological",
                "function": "久坐久立",
                "impact": "腰背疼痛，久坐票拟与长途差遣皆受扰",
                "ability_delta": "案牍耐力下降",
                "course_kind": "chronic",
                "possible_outcomes": ["缓解", "加重"],
            },
            "chronic": True,
        }
    if event_key == "urinary_strain":
        return {
            "name": name,
            "kind": "disease",
            "system": "urinary",
            "condition_key": "occupational:desk:urinary_retention",
            "label": "癃闭",
            "severity": severity,
            "stage": "chronic",
            "note": f"久坐久忍，小便不利；{task}",
            "effects": {
                "record_group": "pathological",
                "function": "排尿",
                "impact": "小便不利，久候入值和长时奏事受扰",
                "ability_delta": "长时值守与密查盯梢风险升高",
                "course_kind": "chronic",
                "possible_outcomes": ["缓解", "加重"],
            },
            "chronic": True,
        }
    if event_key == "stress_breakdown":
        label = "心神失常" if severity >= 5 else "惊悸失眠"
        return {
            "name": name,
            "kind": "disease",
            "system": "mental",
            "condition_key": "occupational:stress:breakdown",
            "label": label,
            "severity": severity,
            "stage": "disabled" if severity >= 5 else "serious" if severity >= 4 else "active",
            "note": f"重任压迫，惊惧失眠；{task}",
            "effects": {
                "record_group": "psychological",
                "function": "心神",
                "impact": "惊惧失眠，奏对易失序，承办判断不稳",
                "ability_delta": "重大职责承压能力下降",
                "course_kind": "chronic",
                "possible_outcomes": ["稳定", "加重"],
            },
            "chronic": True,
        }
    if event_key == "debauchery_stroke":
        return {
            "name": name,
            "kind": "terminal",
            "system": "circulatory",
            "condition_key": "occupational:private:stroke",
            "label": "中风暴疾",
            "severity": 5,
            "stage": "critical",
            "note": "私第夜宴后暴疾猝发，外间只称中风",
            "effects": {
                "record_group": "other",
                "function": "心脑血脉",
                "impact": "中风暴疾，顷刻危殆",
                "ability_delta": "不能任事",
                "fatal": True,
                "privacy": "public",
            },
        }
    return None


def _ensure_secret(db: GameDB, name: str, kind: str, detail: str, severity: int, day: int) -> bool:
    try:
        from ming_sim.intrigue import ensure_schema

        ensure_schema(db)
    except Exception:
        pass
    existing = db.conn.execute(
        "SELECT id FROM secrets WHERE holder=? AND kind=? AND detail=? LIMIT 1",
        (name, kind, detail),
    ).fetchone()
    if existing is not None:
        return False
    db.conn.execute(
        """
        INSERT INTO secrets(holder, kind, detail, severity, known_to_crown, discovered_day, used)
        VALUES (?, ?, ?, ?, 1, ?, 0)
        """,
        (name, kind, detail[:220], max(1, min(100, int(severity or 50))), int(day)),
    )
    db.conn.commit()
    return True


def _push_vacancy_if_needed(db: GameDB, state: GameState, candidate: Dict[str, object], day: int) -> None:
    office = str(candidate.get("office") or "")
    try:
        from ming_sim.lifespan import _is_key_office, _push_vacancy

        if _is_key_office(office):
            _push_vacancy(
                db,
                {
                    "office": office,
                    "office_type": str(candidate.get("office_type") or ""),
                    "faction": str(candidate.get("faction") or ""),
                    "deceased": str(candidate.get("name") or ""),
                    "day": int(day),
                },
            )
    except Exception:
        pass


def _apply_assignment_consequence(
    db: GameDB,
    state: GameState,
    candidate: Dict[str, object],
    event_key: str,
    severity: int,
    day: int,
) -> str:
    source_kind = str(candidate.get("source_kind") or "")
    source_id = str(candidate.get("source_id") or "")
    name = str(candidate.get("name") or "")
    label = _EVENT_TITLES.get(event_key, event_key)
    if source_kind == "directive" and source_id:
        if event_key == "debauchery_stroke" or severity >= 5:
            anomaly = json.dumps(
                {"kind": "occupational_risk", "event": event_key, "day": int(day), "assignee": name},
                ensure_ascii=False,
                sort_keys=True,
            )
            db.conn.execute(
                "UPDATE turn_directives SET lifecycle_status='stalled', anomaly=? WHERE id=?",
                (anomaly, int(source_id)),
            )
            db.conn.commit()
            return "主办伤病危重，旨意停滞，须另择主办或召对处置。"
        delay = 5 if severity >= 4 else 3
        anomaly = json.dumps(
            {"kind": "occupational_risk", "event": event_key, "day": int(day), "assignee": name, "delay": delay},
            ensure_ascii=False,
            sort_keys=True,
        )
        db.conn.execute(
            "UPDATE turn_directives SET exec_days=exec_days+?, eta_day=eta_day+?, anomaly=? WHERE id=?",
            (delay, delay, anomaly, int(source_id)),
        )
        db.conn.commit()
        return f"主办抱病，旨意展期{delay}日。"
    if source_kind == "secret_order" and source_id:
        delay_turns = 1
        db.conn.execute(
            """
            UPDATE secret_orders
            SET due_turn=MAX(COALESCE(due_turn, 0), ?)+?,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (int(state.turn), delay_turns, int(source_id)),
        )
        try:
            db.update_secret_order_sim_note(
                int(source_id),
                f"[病中承办] {name}{label}，密令展限{delay_turns}月。",
                int(state.year),
                int(state.period),
            )
        except Exception:
            pass
        db.conn.commit()
        return f"密令因病伤展限{delay_turns}月。"
    if source_kind == "agreement_task" and source_id:
        evidence = f"病中承办风险：{name}{label}，须召对改期、调养或换人。"
        db.conn.execute(
            """
            UPDATE negotiation_tasks
            SET evidence=?, last_checked_turn=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (evidence[:240], int(state.turn), int(source_id)),
        )
        db.conn.commit()
        return "待履约事项已标记病中承办风险，须召对闭环。"
    return "职责风险已入档。"


def _event_level(event_key: str, severity: int) -> str:
    if event_key == "debauchery_stroke" or severity >= 5:
        return "red"
    if severity >= 4 or event_key in {"private_morality_secret"}:
        return "yellow"
    return "blue"


def _record_memory(
    db: GameDB,
    state: GameState,
    candidate: Dict[str, object],
    event_key: str,
    title: str,
    detail: str,
    outcome: str,
    severity: int,
) -> None:
    name = str(candidate.get("name") or "")
    source_id = _source_id(candidate, event_key)
    try:
        db.upsert_event_memory(
            state,
            "character",
            name,
            "occupational_risk",
            title[:40],
            cause=_clean(candidate.get("task_text"), 80),
            process=detail[:80],
            outcome=outcome[:80],
            sentiment="negative" if severity >= 4 else "mixed",
            importance=4 if severity >= 4 else 3,
            tags=["差遣风险", "病历" if event_key != "private_morality_secret" else "把柄", event_key],
            source_kind="occupational_risk",
            source_id=source_id,
        )
    except Exception:
        pass


def apply_occupational_risk_event(
    db: GameDB,
    state: GameState,
    candidate: Dict[str, object],
    event_key: str,
    day: int,
    *,
    rng: Optional[random.Random] = None,
    severity_override: Optional[int] = None,
) -> Dict[str, object]:
    """Apply one selected occupational risk event and return a day-report event."""

    rng = rng or random.Random(f"task-risk-apply:{int(day)}:{candidate.get('name')}:{event_key}")
    name = str(candidate.get("name") or "")
    if not name:
        return {}
    severity = int(severity_override) if severity_override is not None else _event_severity(candidate, event_key, rng)
    title = f"{_EVENT_TITLES.get(event_key, '差遣风险')}：{name}"
    source_note = _clean(candidate.get("task_text"), 90)
    assignment_outcome = "职责风险已入档。"
    private_outcome = ""
    condition_payload: Optional[Dict[str, object]] = None

    if event_key == "private_morality_secret":
        detail = "私情违礼风闻递入御前；此为私德把柄，不作疾病病历。"
        inserted = _ensure_secret(db, name, "私情", "私情违礼，恐被言官借题弹劾", 48, day)
        private_outcome = "私德把柄已入档。" if inserted else "私德把柄已存在。"
    else:
        from ming_sim.conditions import add_condition

        condition_payload = _condition_payload(candidate, event_key, severity, day)
        if condition_payload:
            add_condition(
                db,
                state,
                name,
                kind=str(condition_payload.get("kind") or "other"),
                system=str(condition_payload.get("system") or "general"),
                condition_key=str(condition_payload.get("condition_key") or ""),
                label=str(condition_payload.get("label") or ""),
                severity=int(condition_payload.get("severity") or severity or 1),
                stage=str(condition_payload.get("stage") or ""),
                note=str(condition_payload.get("note") or ""),
                effects=condition_payload.get("effects") if isinstance(condition_payload.get("effects"), dict) else {},
                hidden=bool(condition_payload.get("hidden") or False),
                chronic=bool(condition_payload.get("chronic") or False),
                duration_days=int(condition_payload.get("duration_days") or 0),
                source_kind="occupational_risk",
                source_id=_source_id(candidate, event_key),
            )
        if event_key == "debauchery_stroke":
            _ensure_secret(db, name, "私德", "狎游失检，私第夜宴后暴疾", 58, day)
            _push_vacancy_if_needed(db, state, candidate, day)
        assignment_outcome = _apply_assignment_consequence(db, state, candidate, event_key, severity, day)
        label = str((condition_payload or {}).get("label") or _EVENT_TITLES.get(event_key, "伤病"))
        detail = f"{name}因{source_note or '承办重任'}诱发{label}。{assignment_outcome}"

    if event_key == "debauchery_stroke":
        detail = f"{name}私第夜宴后中风暴疾，外间只称暴病。{assignment_outcome}"
    elif event_key == "private_morality_secret":
        assignment_outcome = private_outcome

    _record_memory(db, state, candidate, event_key, title, detail, assignment_outcome, severity)
    db.record_log(state, f"【差遣风险】{title}。{detail}")
    return {
        "level": _event_level(event_key, severity),
        "kind": "occupational_risk",
        "title": title,
        "detail": detail,
        "ref_kind": "character",
        "ref_id": name,
        "day": int(day),
        "event_key": event_key,
        "severity": severity,
        "source_kind": str(candidate.get("source_kind") or ""),
        "source_id": str(candidate.get("source_id") or ""),
    }


def apply_task_risk_catalog_effect(
    db: GameDB,
    state: GameState,
    candidate: Dict[str, object],
    event_key: str,
    day: int,
    *,
    rng: Optional[random.Random] = None,
    severity_override: Optional[int] = None,
) -> Dict[str, object]:
    """Apply one fixed task-risk catalog outcome.

    The caller selects a catalog event from a structured LLM risk profile; this
    function owns the deterministic medical/secret/task consequences.
    """

    return apply_occupational_risk_event(
        db,
        state,
        candidate,
        event_key,
        day,
        rng=rng,
        severity_override=severity_override,
    )


def occupational_risk_tick(
    db: GameDB,
    state: GameState,
    day: int,
    *,
    rng: Optional[random.Random] = None,
) -> List[Dict[str, object]]:
    """Daily sparse occupational risk tick."""

    rng = rng or random.Random(f"task-risk:{int(day)}:{int(state.turn)}")
    events: List[Dict[str, object]] = []
    candidates = collect_occupational_risk_candidates(db, state, day)
    if not candidates:
        return events
    for candidate in candidates:
        name = str(candidate.get("name") or "")
        domains = set(candidate.get("domains") or [])
        choices = _eligible_events(candidate, domains)
        if not choices:
            continue
        event_key = _choose_weighted(rng, choices)
        if not event_key:
            continue
        severity = _event_severity(candidate, event_key, rng)
        major = event_key == "debauchery_stroke" or severity >= 5
        if not _month_budget_allows(db, state, name, major=major):
            continue
        if rng.random() >= _event_probability(candidate):
            continue
        event = apply_task_risk_catalog_effect(
            db,
            state,
            candidate,
            event_key,
            day,
            rng=rng,
            severity_override=severity,
        )
        if event:
            _mark_month_event(db, state, name, major=str(event.get("level")) == "red" or int(event.get("severity") or 0) >= 5)
            events.append(event)
        break
    return events
