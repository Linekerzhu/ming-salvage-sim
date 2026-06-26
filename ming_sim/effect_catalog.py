"""Fixed outcome catalog for semantic disease, punishment, and task-risk events.

LLM layers should identify *what happened* and provide evidence.  This module
owns *what that means mechanically* so body facts, punishments, task delays,
identity transforms, and privacy boundaries stay deterministic.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple


PUNISHMENT_ALIASES = {
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
    "劓刑": "yi",
    "刖": "yue",
    "刖刑": "yue",
    "剕": "yue",
    "宫": "gong",
    "宫刑": "gong",
    "腐刑": "gong",
    "去势": "gong",
    "强制净身": "gong",
    "割舌": "tongue_cut",
    "截舌": "tongue_cut",
    "割耳": "ear_cut",
    "截耳": "ear_cut",
    "断腿": "leg_break",
    "折腿": "leg_break",
    "拷掠": "torture",
    "刑讯": "torture",
    "刑讯拷掠": "torture",
    "夹棍": "torture",
}

PUNISHMENT_LABELS = {
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

PUNISHMENT_TAXONOMY = {
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

PUNISHMENT_SEVERITY = {
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


def clean_text(value: object, limit: int = 200) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())[:limit]


def json_dict(raw: object) -> Dict[str, object]:
    if isinstance(raw, dict):
        return dict(raw)
    if not raw:
        return {}
    try:
        data = json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def json_list(raw: object) -> List[object]:
    if isinstance(raw, list):
        return list(raw)
    if not raw:
        return []
    try:
        data = json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def normalize_punishment_key(value: object = "", label: object = "") -> str:
    raw = clean_text(value, 60).lower()
    if raw in PUNISHMENT_LABELS:
        return raw
    label_text = clean_text(label or value, 80)
    direct = PUNISHMENT_ALIASES.get(clean_text(value, 60)) or PUNISHMENT_ALIASES.get(label_text)
    if direct:
        return direct
    for token, key in PUNISHMENT_ALIASES.items():
        if token and token in label_text:
            return key
    return raw or "punishment"


def punishment_catalog_entry(key: str) -> Dict[str, object]:
    clean = str(key or "").strip()
    return {
        "key": clean,
        "label": PUNISHMENT_LABELS.get(clean, "刑罚"),
        "taxonomy": PUNISHMENT_TAXONOMY.get(clean, "ordinary"),
        "severity": PUNISHMENT_SEVERITY.get(clean, 2),
        "identity_transform": clean == "gong",
        "corporal": clean in {"mo", "yi", "yue", "gong", "tongue_cut", "ear_cut", "leg_break", "torture"},
    }


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
    effects: Dict[str, object] = {"record_group": group, "privacy": "medical", "catalog_fixed": True}
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


def apply_punishment_catalog_effect(punishment: Dict[str, object]) -> Dict[str, object]:
    key = str(punishment.get("punishment_key") or "")
    label = str(punishment.get("label") or PUNISHMENT_LABELS.get(key) or "刑罚")
    severity = int(punishment.get("severity") or PUNISHMENT_SEVERITY.get(key, 2))
    side = _side_from_text(label, punishment.get("note"), punishment.get("executor"))
    if key in {"si", "dabi"}:
        return {"status": "dead", "reason": label, "fatal": True, "catalog_fixed": True}
    if key == "liu":
        return {"status": "exiled", "reason": label, "catalog_fixed": True}
    if key == "tu":
        return {"custody": True, "agency": "刑部", "facility": "徒刑羁押", "severity": severity, "catalog_fixed": True}
    if key == "gong":
        return {"castration_medical": True, "forced": True, "reason": label, "identity_transform": True, "catalog_fixed": True}
    if key == "tongue_cut":
        return {
            "catalog_fixed": True,
            "conditions": [
                _condition("tongue:organ", system="speech", label="舌伤", severity=5, group="organic", organ="舌", state="缺损", impact="发声含混，长篇奏对须改短句或书写", speech="口齿含混，不能正常奏对"),
                _condition("tongue:speech", kind="disability", system="speech", label="言语受损", severity=5, group="pathological", function="言语", impact="口齿含混，不能正常奏对", speech="口齿含混，不能正常奏对"),
            ],
        }
    if key == "yi":
        return {
            "catalog_fixed": True,
            "conditions": [
                _condition("nose:organ", system="respiratory", label="劓刑鼻伤", severity=4, group="organic", organ="鼻", state="缺损", impact="面鼻重创，气息不稳"),
                _condition("nose:breathing", kind="disability", system="respiratory", label="呼吸受损", severity=3, stage="chronic", group="pathological", function="呼吸", impact="鼻道伤残，气息不稳"),
            ],
        }
    if key == "yue":
        return {
            "catalog_fixed": True,
            "conditions": [
                _condition("leg:organ", system="musculoskeletal", label="刖刑足伤", severity=5, group="organic", organ="足", side=side, state="缺失", impact="行走久立困难"),
                _condition("leg:walking", kind="disability", system="musculoskeletal", label="行走受损", severity=5, group="pathological", function="行走", impact="不能正常行走，久立难支"),
            ],
        }
    if key == "ear_cut":
        return {"catalog_fixed": True, "condition": _condition("ear:organ", system="nervous", label="耳伤", severity=max(4, severity), group="organic", organ="耳", side=side, state="缺失", impact="听辨受损，仪容伤残")}
    if key == "leg_break":
        return {
            "catalog_fixed": True,
            "conditions": [
                _condition("leg:fracture", kind="injury", system="musculoskeletal", label="腿骨折伤", severity=max(4, severity), stage="serious", group="organic", organ="腿", side=side, state="骨折", impact="行走久立困难", chronic=False),
                _condition("leg:walking", kind="disability", system="musculoskeletal", label="行走受损", severity=max(4, severity), stage="serious", group="pathological", function="行走", impact="行走久立困难，差遣执行受限", chronic=False),
            ],
        }
    if key == "mo":
        return {"catalog_fixed": True, "condition": _condition("face:tattoo", system="skin", label="墨刑黥面", severity=3, stage="active", group="organic", organ="面", state="黥刺", impact="面刺成辱，出入受限")}
    if key in {"chi", "zhang", "torture"}:
        return {
            "catalog_fixed": True,
            "condition": _condition(
                "body:beating",
                system="musculoskeletal",
                label=label if key == "torture" else f"{label}伤",
                severity=max(2, severity),
                stage="serious" if severity >= 4 else "active",
                group="other",
                impact="久立行动受限" if severity >= 4 else "受创疼痛",
            ),
        }
    return {}


CONDITION_ALIASES = {
    "wind_cold": "wind_cold",
    "风寒": "wind_cold",
    "伤风": "wind_cold",
    "肺痨": "phthisis",
    "痨病": "phthisis",
    "phthisis": "phthisis",
    "痔疮": "hemorrhoids",
    "痔疾": "hemorrhoids",
    "癃闭": "urinary_retention",
    "小便不利": "urinary_retention",
    "虚劳": "consumption",
    "惊悸失眠": "palpitation_insomnia",
    "惊悸": "palpitation_insomnia",
    "失眠": "palpitation_insomnia",
    "心神失常": "mental_derangement",
    "精神失常": "mental_derangement",
}

CONDITION_CATALOG = {
    "wind_cold": {
        "kind": "disease", "system": "respiratory", "condition_key": "natural:wind_cold", "label": "风寒",
        "severity": 2, "stage": "active", "duration_days": 7, "chronic": False,
        "effects": {"record_group": "other", "function": "呼吸", "impact": "恶寒咳嗽，奏对气短", "course_kind": "acute", "possible_outcomes": ["恢复", "加重"]},
    },
    "phthisis": {
        "kind": "disease", "system": "respiratory", "condition_key": "natural:phthisis", "label": "肺痨",
        "severity": 4, "stage": "chronic", "chronic": True,
        "effects": {"record_group": "pathological", "function": "呼吸", "impact": "久咳虚热，体力与长奏皆受损", "course_kind": "chronic", "possible_outcomes": ["稳定", "加重"]},
    },
    "hemorrhoids": {
        "kind": "disease", "system": "digestive", "condition_key": "natural:hemorrhoids", "label": "痔疾",
        "severity": 2, "stage": "chronic", "chronic": True,
        "effects": {"record_group": "pathological", "function": "久坐", "impact": "久坐疼痛，案牍耐力下降", "course_kind": "chronic", "possible_outcomes": ["缓解", "加重"]},
    },
    "urinary_retention": {
        "kind": "disease", "system": "urinary", "condition_key": "natural:urinary_retention", "label": "癃闭",
        "severity": 3, "stage": "chronic", "chronic": True,
        "effects": {"record_group": "pathological", "function": "排尿", "impact": "小便不利，久候入值和长时奏事受扰", "course_kind": "chronic", "possible_outcomes": ["缓解", "加重"]},
    },
    "consumption": {
        "kind": "disease", "system": "general", "condition_key": "natural:consumption", "label": "虚劳",
        "severity": 3, "stage": "chronic", "chronic": True,
        "effects": {"record_group": "pathological", "function": "体力", "impact": "气血虚损，承办耐力下降", "course_kind": "chronic", "possible_outcomes": ["调养", "加重"]},
    },
    "palpitation_insomnia": {
        "kind": "disease", "system": "mental", "condition_key": "natural:palpitation_insomnia", "label": "惊悸失眠",
        "severity": 3, "stage": "active", "chronic": True,
        "effects": {"record_group": "psychological", "function": "心神", "impact": "惊惧失眠，奏对易慌乱", "course_kind": "chronic", "possible_outcomes": ["稳定", "加重"]},
    },
    "mental_derangement": {
        "kind": "disease", "system": "mental", "condition_key": "natural:mental_derangement", "label": "心神失常",
        "severity": 5, "stage": "disabled", "chronic": True,
        "effects": {"record_group": "psychological", "function": "心神", "impact": "奏对失序，承办判断不稳", "course_kind": "chronic", "possible_outcomes": ["稳定", "加重"]},
    },
}


def normalize_condition_key(item: Dict[str, object]) -> str:
    raw = clean_text(item.get("condition_key") or item.get("key") or "", 80)
    if raw.startswith("catalog:"):
        raw = raw.split(":", 1)[1]
    label = clean_text(item.get("label") or item.get("condition") or item.get("病名") or item.get("伤名") or "", 80)
    return CONDITION_ALIASES.get(raw) or CONDITION_ALIASES.get(label) or ""


def condition_catalog_payload(item: Dict[str, object], *, day: int = 0) -> Optional[Dict[str, object]]:
    key = normalize_condition_key(item)
    if not key or key not in CONDITION_CATALOG:
        return None
    payload = dict(CONDITION_CATALOG[key])
    payload["effects"] = dict(payload.get("effects") or {})
    payload["effects"]["catalog_key"] = key
    payload["effects"]["catalog_fixed"] = True
    if payload["effects"].get("course_kind") == "acute":
        duration = int(payload.get("duration_days") or 0)
        if duration and day:
            payload["effects"]["next_check_day"] = int(day) + duration
    for meta_key in ("decision_source", "confidence", "evidence_quote"):
        if item.get(meta_key) not in (None, ""):
            payload["effects"][meta_key] = item.get(meta_key)
    return payload


TASK_RISK_PROFILES = {
    "mounted_military": {"domains": ["mounted"], "events": [("riding_fall", 40)], "pressure": 72, "privacy": "public"},
    "desk_bureaucratic": {"domains": ["desk"], "events": [("desk_strain", 26), ("urinary_strain", 14)], "pressure": 64, "privacy": "public"},
    "high_pressure_investigation": {"domains": ["stress"], "events": [("stress_breakdown", 30)], "pressure": 78, "privacy": "public"},
    "corrupt_debauchery": {"domains": ["debauchery"], "events": [("debauchery_stroke", 5)], "pressure": 82, "privacy": "private"},
    "private_morality": {"domains": ["private_morality"], "events": [("private_morality_secret", 4)], "pressure": 60, "privacy": "private"},
}

TASK_RISK_ALIASES = {
    "mounted": "mounted_military",
    "military": "mounted_military",
    "mounted_military": "mounted_military",
    "desk": "desk_bureaucratic",
    "bureaucratic": "desk_bureaucratic",
    "desk_bureaucratic": "desk_bureaucratic",
    "stress": "high_pressure_investigation",
    "investigation": "high_pressure_investigation",
    "high_pressure": "high_pressure_investigation",
    "high_pressure_investigation": "high_pressure_investigation",
    "debauchery": "corrupt_debauchery",
    "corrupt_debauchery": "corrupt_debauchery",
    "private_morality": "private_morality",
}


def normalize_task_risk_profile(raw: object, *, actor: str = "") -> Dict[str, object]:
    data = json_dict(raw)
    if not data and isinstance(raw, str) and raw.strip() in TASK_RISK_ALIASES:
        data = {"risk_tags": [raw.strip()]}
    tags: List[str] = []
    for value in data.get("risk_tags") or data.get("tags") or data.get("profiles") or []:
        key = TASK_RISK_ALIASES.get(clean_text(value, 80))
        if key and key not in tags:
            tags.append(key)
    direct = TASK_RISK_ALIASES.get(clean_text(data.get("task_type") or data.get("profile") or data.get("risk_type"), 80))
    if direct and direct not in tags:
        tags.insert(0, direct)
    if not tags:
        return {}
    try:
        pressure = max(0, min(100, int(data.get("pressure") if data.get("pressure") is not None else max(int(TASK_RISK_PROFILES[t]["pressure"]) for t in tags))))
    except (TypeError, ValueError):
        pressure = max(int(TASK_RISK_PROFILES[t]["pressure"]) for t in tags)
    try:
        confidence = float(data.get("confidence") if data.get("confidence") is not None else 1.0)
    except (TypeError, ValueError):
        confidence = 1.0
    return {
        "actor": clean_text(data.get("actor") or data.get("assignee") or actor, 80),
        "risk_tags": tags,
        "pressure": pressure,
        "privacy": clean_text(data.get("privacy") or ("private" if any(TASK_RISK_PROFILES[t].get("privacy") == "private" for t in tags) else "public"), 20),
        "evidence_quote": clean_text(data.get("evidence_quote") or data.get("evidence") or "", 160),
        "confidence": max(0.0, min(1.0, confidence)),
        "decision_source": clean_text(data.get("decision_source") or "llm", 20),
    }


def accepted_task_risk_profile(raw: object, *, actor: str = "", min_confidence: float = 0.5) -> Dict[str, object]:
    profile = normalize_task_risk_profile(raw, actor=actor)
    if not profile:
        return {}
    try:
        confidence = float(profile.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < min_confidence or not profile.get("risk_tags"):
        return {}
    return profile


def task_risk_profiles_from_payload(
    raw: object,
    *,
    actor: str = "",
    min_confidence: float = 0.5,
    limit: int = 8,
) -> List[Dict[str, object]]:
    profiles: List[Dict[str, object]] = []

    def add(value: object) -> None:
        if len(profiles) >= max(1, int(limit or 8)):
            return
        profile = accepted_task_risk_profile(value, actor=actor, min_confidence=min_confidence)
        if profile:
            profiles.append(profile)

    if isinstance(raw, list):
        for item in raw:
            add(item)
        return profiles
    data = json_dict(raw)
    if not data:
        add(raw)
        return profiles
    rows = data.get("task_risk_profiles")
    if isinstance(rows, list):
        for item in rows:
            add(item)
    payload = data.get("payload")
    if isinstance(payload, dict):
        payload_rows = payload.get("task_risk_profiles")
        if isinstance(payload_rows, list):
            for item in payload_rows:
                add(item)
        add(payload.get("task_risk_profile"))
    add(data.get("task_risk_profile"))
    if not profiles:
        add(data)
    return profiles


def task_risk_domains(profile: Dict[str, object]) -> List[str]:
    domains: List[str] = []
    for tag in profile.get("risk_tags") or []:
        for domain in TASK_RISK_PROFILES.get(str(tag), {}).get("domains", []):
            if domain not in domains:
                domains.append(str(domain))
    return domains


def task_risk_events(profile: Dict[str, object]) -> List[Tuple[str, int]]:
    weighted: Dict[str, int] = {}
    for tag in profile.get("risk_tags") or []:
        for event_key, weight in TASK_RISK_PROFILES.get(str(tag), {}).get("events", []):
            weighted[event_key] = max(weighted.get(event_key, 0), int(weight))
    return list(weighted.items())
