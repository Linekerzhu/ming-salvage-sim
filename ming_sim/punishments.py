"""Punishment ledger and side effects.

This module records punishments as their own durable facts instead of leaving
them scattered across status, custody and body-condition rows.  It supports
ordinary court punishments, Ming-code five punishments, and the older corporal
five punishments used by the fiction layer.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from ming_sim.db import GameDB
from ming_sim.models import GameState


VALID_TAXONOMIES = {"ordinary", "ming_five", "ancient_five"}
VALID_STAGES = {"sentenced", "executed", "stayed", "remitted"}

_CN_TAXONOMY = {
    "普通刑罚": "ordinary",
    "普通": "ordinary",
    "明律五刑": "ming_five",
    "五刑": "ming_five",
    "今五刑": "ming_five",
    "古五刑": "ancient_five",
    "肉刑五刑": "ancient_five",
}

_KEY_ALIASES = {
    "笞": "chi",
    "笞刑": "chi",
    "杖": "zhang",
    "杖刑": "zhang",
    "廷杖": "zhang",
    "徒": "tu",
    "徒刑": "tu",
    "流": "liu",
    "流刑": "liu",
    "充军": "liu",
    "死": "si",
    "死刑": "si",
    "赐死": "si",
    "弃市": "si",
    "大辟": "dabi",
    "墨": "mo",
    "黥": "mo",
    "黥面": "mo",
    "劓": "yi",
    "刖": "yue",
    "剕": "yue",
    "宫": "gong",
    "宫刑": "gong",
    "腐刑": "gong",
    "割舌": "tongue_cut",
    "截舌": "tongue_cut",
    "割耳": "ear_cut",
    "截耳": "ear_cut",
    "断腿": "leg_break",
    "折腿": "leg_break",
    "拷掠": "torture",
    "刑讯": "torture",
    "夹棍": "torture",
}

_KEY_LABELS = {
    "chi": "笞刑",
    "zhang": "杖刑",
    "tu": "徒刑",
    "liu": "流刑",
    "si": "死刑",
    "mo": "墨刑",
    "yi": "劓刑",
    "yue": "刖刑",
    "gong": "宫刑",
    "dabi": "大辟",
    "tongue_cut": "割舌",
    "ear_cut": "割耳",
    "leg_break": "断腿",
    "torture": "刑讯拷掠",
}

_DEFAULT_TAXONOMY = {
    "chi": "ming_five",
    "zhang": "ming_five",
    "tu": "ming_five",
    "liu": "ming_five",
    "si": "ming_five",
    "mo": "ancient_five",
    "yi": "ancient_five",
    "yue": "ancient_five",
    "gong": "ancient_five",
    "dabi": "ancient_five",
    "tongue_cut": "ordinary",
    "ear_cut": "ordinary",
    "leg_break": "ordinary",
    "torture": "ordinary",
}

_DEFAULT_SEVERITY = {
    "chi": 2,
    "zhang": 3,
    "tu": 3,
    "liu": 3,
    "si": 5,
    "mo": 3,
    "yi": 4,
    "yue": 5,
    "gong": 5,
    "dabi": 5,
    "tongue_cut": 5,
    "ear_cut": 4,
    "leg_break": 5,
    "torture": 4,
}


def _clean_text(value: object, limit: int = 200) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())[:limit]


def _clamp(value: object, default: int = 2, low: int = 1, high: int = 5) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(low, min(high, parsed))


def _bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    raw = _clean_text(value, 20).lower()
    if raw in {"1", "true", "yes", "y", "是", "真", "有"}:
        return True
    if raw in {"0", "false", "no", "n", "否", "假", "无", ""}:
        return False
    return bool(value)


def _current_day(db: GameDB) -> int:
    try:
        from ming_sim.upgrade_schema import KV_CURRENT_DAY, kv_int

        return kv_int(db, KV_CURRENT_DAY, 0)
    except Exception:
        return 0


def _pick(raw: Dict[str, object], *keys: str, default: object = "") -> object:
    for key in keys:
        if key in raw and raw.get(key) not in (None, ""):
            return raw.get(key)
    return default


def _norm_key(value: object, label: object = "") -> str:
    raw = _clean_text(value, 60).lower()
    if raw in _KEY_LABELS:
        return raw
    cn = _KEY_ALIASES.get(_clean_text(value, 60)) or _KEY_ALIASES.get(_clean_text(label, 60))
    if cn:
        return cn
    label_text = _clean_text(label or value, 80)
    for token, key in _KEY_ALIASES.items():
        if token and token in label_text:
            return key
    return raw or "punishment"


def _norm_taxonomy(value: object, key: str) -> str:
    raw = _clean_text(value, 40).lower()
    raw = _CN_TAXONOMY.get(_clean_text(value, 40), raw)
    if raw in VALID_TAXONOMIES:
        return raw
    return _DEFAULT_TAXONOMY.get(key, "ordinary")


def _norm_stage(value: object) -> str:
    raw = _clean_text(value, 40).lower()
    aliases = {
        "判": "sentenced",
        "判决": "sentenced",
        "已判": "sentenced",
        "执行": "executed",
        "已执行": "executed",
        "行刑": "executed",
        "缓刑": "stayed",
        "暂缓": "stayed",
        "赦免": "remitted",
        "减免": "remitted",
    }
    raw = aliases.get(raw, raw)
    return raw if raw in VALID_STAGES else "executed"


def _side_from_text(*parts: object) -> str:
    text = " ".join(str(part or "") for part in parts)
    if "左" in text:
        return "左"
    if "右" in text:
        return "右"
    return ""


def _condition(
    key: str,
    *,
    kind: str = "punishment",
    system: str = "general",
    label: str,
    severity: int,
    stage: str = "disabled",
    group: str = "other",
    organ: str = "",
    side: str = "",
    state: str = "",
    function: str = "",
    impact: str = "",
    speech: str = "",
    chronic: bool = True,
) -> Dict[str, object]:
    effects: Dict[str, object] = {"record_group": group, "privacy": "medical"}
    if organ:
        effects["organ"] = organ
    if side:
        effects["side"] = side
    if state:
        effects["state"] = state
    if function:
        effects["function"] = function
    if impact:
        effects["impact"] = impact
        effects["ability_delta"] = impact
    if speech:
        effects["speech"] = speech
    return {
        "condition_key": key,
        "kind": kind,
        "system": system,
        "label": label,
        "severity": severity,
        "stage": stage,
        "effects": effects,
        "chronic": chronic,
    }


def _row_payload(row: Any) -> Dict[str, object]:
    return {
        "id": int(row["id"]),
        "name": str(row["name"] or ""),
        "taxonomy": str(row["taxonomy"] or "ordinary"),
        "punishment_key": str(row["punishment_key"] or ""),
        "label": str(row["label"] or ""),
        "severity": int(row["severity"] or 1),
        "stage": str(row["stage"] or "executed"),
        "executor": str(row["executor"] or ""),
        "source_kind": str(row["source_kind"] or ""),
        "source_id": str(row["source_id"] or ""),
        "note": str(row["note"] or ""),
        "turn": int(row["turn"] or 0),
        "day": int(row["day"] or 0),
    }


def punishment_side_effect(item: Dict[str, object]) -> Dict[str, object]:
    key = str(item.get("punishment_key") or "")
    label = str(item.get("label") or _KEY_LABELS.get(key) or "刑罚")
    severity = int(item.get("severity") or _DEFAULT_SEVERITY.get(key, 2))
    side = _side_from_text(label, item.get("note"), item.get("executor"))
    if key in {"si", "dabi"}:
        return {"status": "dead", "reason": label, "fatal": True}
    if key == "liu":
        return {"status": "exiled", "reason": label}
    if key == "tu":
        return {"custody": True, "agency": "刑部", "facility": "徒刑羁押", "severity": severity}
    if key == "gong":
        return {
            "castration_medical": True,
            "forced": True,
            "reason": label,
        }
    if key == "tongue_cut":
        return {
            "conditions": [
                _condition(
                    "tongue:organ",
                    system="speech",
                    label="舌伤",
                    severity=5,
                    group="organic",
                    organ="舌",
                    state="缺损",
                    impact="发声含混，长篇奏对须改短句或书写",
                    speech="口齿含混，不能正常奏对",
                ),
                _condition(
                    "tongue:speech",
                    kind="disability",
                    system="speech",
                    label="言语受损",
                    severity=5,
                    group="pathological",
                    function="言语",
                    impact="口齿含混，不能正常奏对",
                    speech="口齿含混，不能正常奏对",
                ),
            ]
        }
    if key == "yi":
        return {
            "conditions": [
                _condition(
                    "nose:organ",
                    system="respiratory",
                    label="劓刑鼻伤",
                    severity=4,
                    group="organic",
                    organ="鼻",
                    state="缺损",
                    impact="面鼻重创，气息不稳",
                ),
                _condition(
                    "nose:breathing",
                    kind="disability",
                    system="respiratory",
                    label="呼吸受损",
                    severity=3,
                    stage="chronic",
                    group="pathological",
                    function="呼吸",
                    impact="鼻道伤残，气息不稳",
                ),
            ]
        }
    if key == "yue":
        return {
            "conditions": [
                _condition(
                    "leg:organ",
                    system="musculoskeletal",
                    label="刖刑足伤",
                    severity=5,
                    group="organic",
                    organ="足",
                    side=side,
                    state="缺失",
                    impact="行走久立困难",
                ),
                _condition(
                    "leg:walking",
                    kind="disability",
                    system="musculoskeletal",
                    label="行走受损",
                    severity=5,
                    group="pathological",
                    function="行走",
                    impact="不能正常行走，久立难支",
                ),
            ]
        }
    if key == "ear_cut":
        return {
            "condition": _condition(
                "ear:organ",
                system="nervous",
                label="耳伤",
                severity=max(4, severity),
                group="organic",
                organ="耳",
                side=side,
                state="缺失",
                impact="听辨受损，仪容伤残",
            )
        }
    if key == "leg_break":
        return {
            "conditions": [
                _condition(
                    "leg:fracture",
                    kind="injury",
                    system="musculoskeletal",
                    label="腿骨折伤",
                    severity=max(4, severity),
                    stage="serious",
                    group="organic",
                    organ="腿",
                    side=side,
                    state="骨折",
                    impact="行走久立困难",
                    chronic=False,
                ),
                _condition(
                    "leg:walking",
                    kind="disability",
                    system="musculoskeletal",
                    label="行走受损",
                    severity=max(4, severity),
                    stage="serious",
                    group="pathological",
                    function="行走",
                    impact="行走久立困难，差遣执行受限",
                    chronic=False,
                ),
            ]
        }
    if key == "mo":
        return {
            "condition": {
                "kind": "punishment",
                "system": "skin",
                "label": "墨刑黥面",
                "severity": 3,
                "stage": "active",
                "effects": {"ability_delta": "面刺成辱，出入受限"},
            }
        }
    if key in {"chi", "zhang", "torture"}:
        return {
            "condition": {
                "kind": "punishment",
                "system": "musculoskeletal",
                "label": label if key == "torture" else f"{label}伤",
                "severity": max(2, severity),
                "stage": "serious" if severity >= 4 else "active",
                "condition_key": "body:beating",
                "effects": {
                    "record_group": "other",
                    "impact": "久立行动受限" if severity >= 4 else "受创疼痛",
                    "ability_delta": "久立行动受限" if severity >= 4 else "受创疼痛",
                },
            }
        }
    return {}


def record_punishment(
    db: GameDB,
    state: GameState,
    item: Dict[str, object],
    *,
    source_kind: str = "",
    source_id: str = "",
) -> Dict[str, object]:
    name = _clean_text(_pick(item, "name", "姓名"), 80)
    if not name:
        raise ValueError("punishment name 为空")
    if db.conn.execute("SELECT 1 FROM characters WHERE name=?", (name,)).fetchone() is None:
        raise ValueError(f"人物不存在：{name}")
    label_raw = _pick(item, "label", "刑名", "刑罚", "punishment", default="")
    key = _norm_key(_pick(item, "punishment_key", "key", "刑罚键", default=""), label_raw)
    taxonomy = _norm_taxonomy(_pick(item, "taxonomy", "体系", "类别", default=""), key)
    label = _clean_text(label_raw, 80) or _KEY_LABELS.get(key, "刑罚")
    severity = _clamp(_pick(item, "severity", "严重度", default=_DEFAULT_SEVERITY.get(key, 2)), default=_DEFAULT_SEVERITY.get(key, 2))
    stage = _norm_stage(_pick(item, "stage", "阶段", default="executed"))
    executor = _clean_text(_pick(item, "executor", "执行机关", "机关", default=""), 80)
    note = _clean_text(_pick(item, "note", "reason", "原因", default=""), 240)
    day = _current_day(db)
    with db.conn:
        db.conn.execute(
            """
            INSERT INTO character_punishments
                (name, taxonomy, punishment_key, label, severity, stage,
                 executor, source_kind, source_id, note, turn, day)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name, punishment_key, source_kind, source_id) DO UPDATE SET
                taxonomy=excluded.taxonomy,
                label=excluded.label,
                severity=excluded.severity,
                stage=excluded.stage,
                executor=excluded.executor,
                note=excluded.note,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                name,
                taxonomy,
                key,
                label,
                severity,
                stage,
                executor,
                _clean_text(source_kind, 40),
                _clean_text(source_id, 80),
                note,
                int(state.turn),
                day,
            ),
        )
    row = db.conn.execute(
        "SELECT * FROM character_punishments WHERE name=? AND punishment_key=? AND source_kind=? AND source_id=?",
        (name, key, _clean_text(source_kind, 40), _clean_text(source_id, 80)),
    ).fetchone()
    payload = _row_payload(row) if row is not None else {}
    if stage == "executed":
        payload["side_effect"] = apply_punishment_side_effect(db, state, payload)
    return payload


def apply_punishment_side_effect(db: GameDB, state: GameState, punishment: Dict[str, object]) -> Dict[str, object]:
    effect = punishment_side_effect(punishment)
    name = str(punishment.get("name") or "")
    out: Dict[str, object] = {}
    if not name:
        return out
    if effect.get("status"):
        status = str(effect["status"])
        try:
            cur_status, _ = db.get_character_status(name)
            if cur_status in {"active", "imprisoned"}:
                db.set_character_status(state, name, status, str(effect.get("reason") or punishment.get("label") or "刑罚"))
                if status == "dead":
                    with db.conn:
                        db.conn.execute("UPDATE characters SET hp=0 WHERE name=?", (name,))
                out["status"] = status
            else:
                out["status_skipped"] = cur_status
        except Exception as exc:
            out["status_rejected"] = str(exc)
    raw_conditions: List[Dict[str, object]] = []
    condition = effect.get("condition")
    if isinstance(condition, dict):
        raw_conditions.append(condition)
    conditions = effect.get("conditions")
    if isinstance(conditions, list):
        raw_conditions.extend(item for item in conditions if isinstance(item, dict))
    applied_conditions: List[Dict[str, object]] = []
    for idx, condition in enumerate(raw_conditions):
        try:
            from ming_sim.conditions import add_condition

            cond = add_condition(
                db,
                state,
                name,
                kind=str(condition.get("kind") or "punishment"),
                system=str(condition.get("system") or "general"),
                condition_key=f"punishment:{punishment.get('punishment_key')}:{condition.get('condition_key') or idx}",
                label=str(condition.get("label") or punishment.get("label") or "刑伤"),
                severity=int(condition.get("severity") or punishment.get("severity") or 2),
                stage=str(condition.get("stage") or "active"),
                note=str(punishment.get("note") or punishment.get("label") or ""),
                effects=condition.get("effects") if isinstance(condition.get("effects"), dict) else {},
                chronic=_bool(condition.get("chronic", True)),
                source_kind=str(punishment.get("source_kind") or ""),
                source_id=str(punishment.get("source_id") or ""),
            )
            applied_conditions.append(cond)
        except Exception as exc:
            out["condition_rejected"] = str(exc)
    if applied_conditions:
        out["condition"] = applied_conditions[0]
        out["conditions"] = applied_conditions
    if effect.get("castration_medical"):
        try:
            from ming_sim.conditions import sync_castration_medical_record

            conditions = sync_castration_medical_record(
                db,
                state,
                name,
                forced=_bool(effect.get("forced", True)),
                note=str(punishment.get("note") or effect.get("reason") or punishment.get("label") or ""),
                source_kind=str(punishment.get("source_kind") or "punishment"),
                source_id=str(punishment.get("source_id") or punishment.get("punishment_key") or "gong"),
            )
            out["conditions"] = conditions
            first = next((item for item in conditions if isinstance(item, dict) and not item.get("rejected")), None)
            if first is not None:
                out["condition"] = first
        except Exception as exc:
            out["conditions_rejected"] = str(exc)
    if effect.get("custody"):
        try:
            from ming_sim.custody import record_custody

            out["custody"] = record_custody(
                db,
                state,
                name,
                agency=str(effect.get("agency") or "刑部"),
                facility=str(effect.get("facility") or "徒刑羁押"),
                severity=int(effect.get("severity") or punishment.get("severity") or 2),
                note=str(punishment.get("note") or punishment.get("label") or ""),
                source_kind=str(punishment.get("source_kind") or ""),
                source_id=str(punishment.get("source_id") or ""),
            )
        except Exception as exc:
            out["custody_rejected"] = str(exc)
    return out


def apply_punishment_changes(
    db: GameDB,
    state: GameState,
    items: object,
    *,
    source_kind: str = "",
    source_id: str = "",
) -> List[Dict[str, object]]:
    applied: List[Dict[str, object]] = []
    if not isinstance(items, list):
        return applied
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            applied.append(record_punishment(db, state, item, source_kind=source_kind, source_id=source_id))
        except Exception as exc:
            applied.append({"name": str(item.get("name") or item.get("姓名") or ""), "rejected": True, "reason": str(exc)})
    return applied


def list_punishments(db: GameDB, name: str, *, limit: int = 8) -> List[Dict[str, object]]:
    rows = db.conn.execute(
        """
        SELECT * FROM character_punishments
        WHERE name=?
        ORDER BY id DESC
        LIMIT ?
        """,
        (_clean_text(name, 80), max(1, int(limit))),
    ).fetchall()
    return [_row_payload(row) for row in rows]


def public_punishment_payload(db: GameDB, name: str) -> Dict[str, object]:
    records = list_punishments(db, name, limit=6)
    if not records:
        return {}
    lead = records[0]
    tags = [str(lead.get("label") or "刑罚")]
    if str(lead.get("taxonomy")) == "ming_five":
        tags.append("明律五刑")
    elif str(lead.get("taxonomy")) == "ancient_five":
        tags.append("古五刑")
    if int(lead.get("severity") or 1) >= 5:
        tags.append("重刑")
    summary = f"{lead.get('label') or '刑罚'}；{lead.get('stage') or 'executed'}"
    if lead.get("executor"):
        summary += f"；{lead['executor']}"
    return {"summary": summary, "tags": list(dict.fromkeys(tags))[:4], "records": records}


def dialogue_punishment_brief(db: GameDB, name: str) -> str:
    records = list_punishments(db, name, limit=4)
    if not records:
        return ""
    lines = ["【刑罚记录（隐藏；影响恐惧、羞辱与身体后果，不得复述机制名）】"]
    for item in records:
        lines.append(
            f"- {item.get('label') or '刑罚'}；体系={item.get('taxonomy')}; "
            f"严重度{int(item.get('severity') or 1)}/5；{item.get('note') or ''}"
        )
    if any(int(item.get("severity") or 1) >= 5 for item in records):
        lines.append("重刑经历会令其奏对畏惧、羞辱、求生或怨毒；若有肉刑后遗，必须服从身体限制。")
    else:
        lines.append("受刑经历会让其言语更谨慎，回答中带畏罪、屈辱或求免之意。")
    return "\n".join(lines)
