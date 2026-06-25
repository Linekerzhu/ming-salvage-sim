"""Character body conditions: disease, injury, punishment scars and speech limits.

This is the shared "body fact" layer for the punishment / prison / disease
roadmap.  It deliberately lives beside, not inside, ``characters.status``:
status says whether someone is on stage; conditions say what their body and
voice are currently capable of.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from ming_sim.db import GameDB
from ming_sim.models import GameState


VALID_KINDS = {"disease", "injury", "punishment", "disability", "prison_effect", "terminal", "other"}
VALID_SYSTEMS = {
    "general",
    "speech",
    "nervous",
    "mental",
    "respiratory",
    "circulatory",
    "digestive",
    "urinary",
    "reproductive",
    "musculoskeletal",
    "skin",
}
VALID_STAGES = {"active", "mild", "serious", "critical", "disabled", "chronic", "recovering", "resolved", "dead"}

_CN_KIND = {
    "疾病": "disease",
    "病": "disease",
    "伤": "injury",
    "刑伤": "punishment",
    "刑罚": "punishment",
    "残疾": "disability",
    "狱中": "prison_effect",
    "狱伤": "prison_effect",
    "濒死": "terminal",
}
_CN_SYSTEM = {
    "全身": "general",
    "通用": "general",
    "口舌": "speech",
    "语言": "speech",
    "咽喉": "speech",
    "神经": "nervous",
    "心神": "mental",
    "精神": "mental",
    "呼吸": "respiratory",
    "肺": "respiratory",
    "循环": "circulatory",
    "心血": "circulatory",
    "消化": "digestive",
    "肠胃": "digestive",
    "泌尿": "urinary",
    "生殖": "reproductive",
    "筋骨": "musculoskeletal",
    "肢体": "musculoskeletal",
    "皮肤": "skin",
}
_CN_STAGE = {
    "轻": "mild",
    "轻症": "mild",
    "重": "serious",
    "重症": "serious",
    "危重": "critical",
    "濒危": "critical",
    "废用": "disabled",
    "功能丧失": "disabled",
    "慢性": "chronic",
    "恢复": "recovering",
    "已愈": "resolved",
    "解除": "resolved",
    "死亡": "dead",
    "已死": "dead",
}

_SEVERITY_DAMAGE = {1: 4, 2: 10, 3: 22, 4: 45, 5: 70}
_DEATH_RE = re.compile(r"病逝|死亡|已死|暴毙|毙命|身死|殒命|气绝|咽气|不治而亡")

_MEDICAL_GROUP_LABELS = {
    "organic": "器质性",
    "pathological": "病理性",
    "psychological": "心理/照护",
    "other": "其他疾病/外伤",
}

_PUBLIC_CASTRATION_DETAIL_RE = re.compile(
    r"宝匣|旧匣|封匣|钥匙|验匣|宝案|净身房夜割|铜柄|银柄|檀柄|宫刀|小净刀|"
    r"无麻|油炸|封蜡|石灰|盐灰|香料|楠木|杉木|黄杨|锡胆|灰瓮|铁皮|锁匣|"
    r"佛龛|封签|宝约|宝用|匣藏|官库|供奉|自藏|还阳|临睡|旧愿"
)


def _is_castration_public_fact(item: Dict[str, object]) -> bool:
    effects = item.get("effects") if isinstance(item.get("effects"), dict) else {}
    key = str(item.get("condition_key") or "")
    source = str(item.get("source_kind") or "")
    return (
        key.startswith("castration:")
        or source == "castration_lore"
        or bool(effects.get("organ") in {"左侧睾丸", "右侧睾丸", "阴茎"})
    )


def _public_medical_text(value: object, *, castration: bool = False) -> str:
    text = _clean_text(value, 180)
    if not text:
        return ""
    text = (
        text.replace("宝匣", "宝贝")
        .replace("旧匣", "宝贝")
        .replace("封匣", "安置")
        .replace("验匣", "查验")
    )
    if not castration:
        return text
    if "钥匙" in text:
        return ""
    if _PUBLIC_CASTRATION_DETAIL_RE.search(text):
        if re.search(r"宝贝|全尸|执念|心结|惦念|还阳", text):
            return "惦念宝贝与全尸旧愿"
        return ""
    return text


def _clamp(value: object, default: int = 1, low: int = 1, high: int = 5) -> int:
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


def _nonnegative_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


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


def _source_id(value: object, fallback: str = "") -> str:
    cleaned = _clean_text(value, 80)
    return cleaned or _clean_text(fallback, 80)


def _truthy_effect(effects: Dict[str, object], *keys: str) -> bool:
    for key in keys:
        if key in effects and _bool(effects.get(key)):
            return True
    return False


def _clean_text(value: object, limit: int = 200) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())[:limit]


def _norm_kind(value: object) -> str:
    raw = _clean_text(value, 40).lower()
    raw = _CN_KIND.get(raw, raw)
    return raw if raw in VALID_KINDS else "other"


def _norm_system(value: object) -> str:
    raw = _clean_text(value, 40).lower()
    raw = _CN_SYSTEM.get(raw, raw)
    return raw if raw in VALID_SYSTEMS else "general"


def _norm_stage(value: object, severity: int) -> str:
    raw = _clean_text(value, 40).lower()
    raw = _CN_STAGE.get(raw, raw)
    if raw in VALID_STAGES:
        return raw
    if severity >= 5:
        return "critical"
    if severity >= 4:
        return "serious"
    if severity <= 2:
        return "mild"
    return "active"


def _condition_key(label: str, kind: str, system: str) -> str:
    base = re.sub(r"\s+", "", label or "")
    return (base or f"{kind}:{system}")[:80]


def _current_day(db: GameDB) -> int:
    try:
        from ming_sim.upgrade_schema import KV_CURRENT_DAY, kv_int

        return kv_int(db, KV_CURRENT_DAY, 0)
    except Exception:
        return 0


def add_condition(
    db: GameDB,
    state: GameState,
    name: str,
    *,
    kind: str = "other",
    system: str = "general",
    condition_key: str = "",
    label: str = "",
    severity: int = 1,
    stage: str = "",
    note: str = "",
    effects: Optional[Dict[str, object]] = None,
    hidden: bool = False,
    chronic: bool = False,
    duration_days: int = 0,
    source_kind: str = "",
    source_id: str = "",
) -> Dict[str, object]:
    """Insert or refresh one body-condition fact for a character."""

    clean_name = _clean_text(name, 80)
    if not clean_name:
        raise ValueError("condition name 为空")
    if db.conn.execute("SELECT 1 FROM characters WHERE name=?", (clean_name,)).fetchone() is None:
        raise ValueError(f"人物不存在：{clean_name}")
    sev = _clamp(severity)
    k = _norm_kind(kind)
    sys = _norm_system(system)
    lbl = _clean_text(label, 80) or _clean_text(condition_key, 80) or "身体异状"
    key = _clean_text(condition_key, 80) or _condition_key(lbl, k, sys)
    stg = _norm_stage(stage, sev)
    eff = dict(effects or {})
    day = _current_day(db)
    with db.conn:
        cur = db.conn.execute(
            """
            INSERT INTO character_conditions
                (name, kind, system, condition_key, label, severity, stage,
                 onset_turn, onset_day, duration_days, chronic, hidden,
                 source_kind, source_id, note, effects_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name, condition_key, source_kind, source_id) DO UPDATE SET
                kind=excluded.kind,
                system=excluded.system,
                label=excluded.label,
                severity=excluded.severity,
                stage=excluded.stage,
                duration_days=excluded.duration_days,
                chronic=excluded.chronic,
                hidden=excluded.hidden,
                note=excluded.note,
                effects_json=excluded.effects_json,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                clean_name,
                k,
                sys,
                key,
                lbl,
                sev,
                stg,
                int(state.turn),
                day,
                max(0, int(duration_days or 0)),
                1 if chronic else 0,
                1 if hidden else 0,
                _clean_text(source_kind, 40),
                _clean_text(source_id, 80),
                _clean_text(note, 240),
                json.dumps(eff, ensure_ascii=False, sort_keys=True),
            ),
        )
        condition_id = int(cur.lastrowid or 0)
    row = db.conn.execute(
        "SELECT * FROM character_conditions WHERE name=? AND condition_key=? AND source_kind=? AND source_id=?",
        (clean_name, key, _clean_text(source_kind, 40), _clean_text(source_id, 80)),
    ).fetchone()
    payload = _row_payload(row) if row is not None else {}
    if condition_id and not payload.get("id"):
        payload["id"] = condition_id
    health = sync_character_health(db, state, clean_name)
    if health:
        payload["health"] = health
    return payload


def _row_payload(row: Any) -> Dict[str, object]:
    effects = _json_dict(row["effects_json"] if "effects_json" in row.keys() else "{}")
    return {
        "id": int(row["id"]),
        "name": str(row["name"] or ""),
        "kind": str(row["kind"] or "other"),
        "system": str(row["system"] or "general"),
        "condition_key": str(row["condition_key"] or ""),
        "label": str(row["label"] or row["condition_key"] or "身体异状"),
        "severity": int(row["severity"] or 1),
        "stage": str(row["stage"] or "active"),
        "onset_turn": int(row["onset_turn"] or 0),
        "onset_day": int(row["onset_day"] or 0),
        "duration_days": int(row["duration_days"] or 0),
        "chronic": bool(int(row["chronic"] or 0)),
        "hidden": bool(int(row["hidden"] or 0)),
        "source_kind": str(row["source_kind"] or ""),
        "source_id": str(row["source_id"] or ""),
        "note": str(row["note"] or ""),
        "effects": effects,
    }


def _medical_group_for_condition(item: Dict[str, object]) -> str:
    effects = item.get("effects") if isinstance(item.get("effects"), dict) else {}
    group = _clean_text(effects.get("record_group"), 40)
    if group in _MEDICAL_GROUP_LABELS:
        return group
    kind = str(item.get("kind") or "")
    system = str(item.get("system") or "")
    if effects.get("organ") or effects.get("state"):
        return "organic"
    if effects.get("function") or system in {"urinary", "reproductive"}:
        return "pathological"
    if system == "mental":
        return "psychological"
    if kind in {"disease", "injury", "punishment", "prison_effect", "terminal", "disability"}:
        return "other"
    return "other"


def _medical_item_payload(item: Dict[str, object]) -> Dict[str, object]:
    effects = item.get("effects") if isinstance(item.get("effects"), dict) else {}
    is_castration = _is_castration_public_fact(item)
    group = _medical_group_for_condition(item)
    organ = _public_medical_text(effects.get("organ"), castration=is_castration) or _clean_text(effects.get("organ"), 80)
    side = _clean_text(effects.get("side"), 20)
    state = _public_medical_text(effects.get("state"), castration=is_castration) or _clean_text(effects.get("state"), 80)
    function = _public_medical_text(effects.get("function"), castration=is_castration) or _clean_text(effects.get("function"), 80)
    impact = _public_medical_text(effects.get("impact") or effects.get("ability_delta"), castration=is_castration)
    label = _public_medical_text(item.get("label"), castration=is_castration) or "身体异状"
    if is_castration:
        if label in {"宝贝/全尸执念", "旧匣/全尸执念", "身体异状"} and (
            "宝贝" in function or "全尸" in function or "宝贝" in str(item.get("label") or "")
        ):
            label = "宝贝/全尸执念"
        if function == "旧匣/全尸执念":
            function = "宝贝/全尸执念"
    display_organ = f"{side}{organ}" if side and organ and not organ.startswith(side) else organ
    if organ and state:
        title = f"{display_organ}：{state}"
    elif is_castration and (group == "psychological" or label in {"绝育", "性功能丧失"}) and label:
        title = label
    elif function and impact:
        title = f"{function}：{impact}"
    else:
        title = label
    title = _public_medical_text(title, castration=is_castration) or label
    next_check_day = _nonnegative_int(effects.get("next_check_day") or 0)
    if not next_check_day and int(item.get("duration_days") or 0) > 0:
        next_check_day = int(item.get("onset_day") or 0) + int(item.get("duration_days") or 0)
    outcomes_raw = effects.get("possible_outcomes") or []
    if isinstance(outcomes_raw, list):
        possible_outcomes = [_clean_text(part, 40) for part in outcomes_raw if _clean_text(part, 40)]
    else:
        possible_outcomes = [
            _clean_text(part, 40)
            for part in re.split(r"[,，/、|或]", str(outcomes_raw or ""))
            if _clean_text(part, 40)
        ]
    return {
        "id": item.get("id"),
        "title": title,
        "label": label,
        "severity": int(item.get("severity") or 1),
        "stage": str(item.get("stage") or ""),
        "system": str(item.get("system") or ""),
        "kind": str(item.get("kind") or ""),
        "organ": organ,
        "side": side,
        "state": state,
        "function": function,
        "impact": impact,
        "privacy": _clean_text(effects.get("privacy"), 40),
        "course_kind": _clean_text(effects.get("course_kind"), 40),
        "next_check_day": next_check_day,
        "possible_outcomes": possible_outcomes,
        "note": _public_medical_text(item.get("note"), castration=is_castration),
    }


def _course_label(item: Dict[str, object], today: int) -> str:
    next_check_day = int(item.get("next_check_day") or 0)
    if not next_check_day:
        return ""
    days = max(0, next_check_day - int(today or 0))
    outcomes = item.get("possible_outcomes") if isinstance(item.get("possible_outcomes"), list) else []
    if outcomes:
        return f"{days}天后复诊/判定，可能{'或'.join(str(outcome) for outcome in outcomes if str(outcome))}"
    return f"{days}天后复诊/判定"


def _redundant_organic_source_note(raw: Dict[str, object], payload: Dict[str, object]) -> bool:
    """Organic loss rows already say the body fact; avoid repeating "净身/宫刑" on each organ."""

    effects = raw.get("effects") if isinstance(raw.get("effects"), dict) else {}
    group = _clean_text(effects.get("record_group"), 40)
    key = str(raw.get("condition_key") or "")
    source = str(raw.get("source_kind") or "")
    state = str(payload.get("state") or "")
    if group != "organic" or not payload.get("organ") or state not in {"缺失", "缺损", "缺如"}:
        return False
    return key.startswith("castration:organ:") or source == "castration_lore"


def _compact_medical_note(
    raw: Dict[str, object],
    payload: Dict[str, object],
    *,
    seen_notes: set[str],
) -> None:
    note = _clean_text(payload.get("note"), 180)
    if not note:
        payload.pop("note", None)
        return
    if _is_castration_public_fact(raw) or _redundant_organic_source_note(raw, payload) or note in seen_notes:
        payload.pop("note", None)
        return
    payload["note"] = note
    seen_notes.add(note)


def _medical_groups(conditions: List[Dict[str, object]], *, today: int = 0) -> List[Dict[str, object]]:
    grouped: Dict[str, List[Dict[str, object]]] = {key: [] for key in _MEDICAL_GROUP_LABELS}
    seen: set[str] = set()
    seen_notes: set[str] = set()
    for item in conditions:
        dedupe_key = str(item.get("condition_key") or item.get("label") or item.get("id") or "")
        if dedupe_key and dedupe_key in seen:
            continue
        if dedupe_key:
            seen.add(dedupe_key)
        group = _medical_group_for_condition(item)
        payload = _medical_item_payload(item)
        _compact_medical_note(item, payload, seen_notes=seen_notes)
        label = _course_label(payload, today)
        if label:
            payload["course_label"] = label
        grouped.setdefault(group, []).append(payload)
    return [
        {"key": key, "label": label, "items": grouped.get(key, [])}
        for key, label in _MEDICAL_GROUP_LABELS.items()
        if grouped.get(key)
    ]


def _public_condition_record(item: Dict[str, object]) -> Dict[str, object]:
    """Sanitize the compatibility `conditions` list for public API payloads."""

    out = dict(item)
    is_castration = _is_castration_public_fact(item)
    if is_castration:
        out["label"] = _public_medical_text(out.get("label"), castration=True) or str(out.get("label") or "")
        out.pop("note", None)
        effects = dict(out.get("effects") or {}) if isinstance(out.get("effects"), dict) else {}
        cleaned_effects: Dict[str, object] = {}
        for key, value in effects.items():
            if isinstance(value, str):
                cleaned = _public_medical_text(value, castration=True)
                if cleaned:
                    cleaned_effects[key] = cleaned
            else:
                cleaned_effects[key] = value
        out["effects"] = cleaned_effects
    return out


def _is_castration_medical_fact(item: Dict[str, object]) -> bool:
    return _is_castration_public_fact(item)


def _display_damage(item: Dict[str, object]) -> int:
    if not _is_castration_medical_fact(item):
        return _clamp(item.get("severity"), default=1) * 8
    group = _medical_group_for_condition(item)
    system = str(item.get("system") or "")
    if group == "organic":
        return 3
    if system == "urinary":
        return 8
    if group == "pathological":
        return 5
    return 4


def _summary_label(item: Dict[str, object]) -> str:
    payload = _medical_item_payload(item)
    return _clean_text(payload.get("title") or item.get("label"), 80)


def _summary_conditions(conditions: List[Dict[str, object]]) -> List[Dict[str, object]]:
    """Prefer actionable diseases/injuries in the one-line summary.

    Permanent eunuch/castration facts are already shown in structured groups and
    tags; repeating "性功能丧失(5/5)" in the intro line makes the medical record
    look noisy without adding information.
    """

    non_castration = [item for item in conditions if not _is_castration_medical_fact(item)]
    return non_castration[:3]


def list_conditions(
    db: GameDB,
    name: str,
    *,
    include_hidden: bool = True,
    include_resolved: bool = False,
) -> List[Dict[str, object]]:
    clauses = ["name=?"]
    params: List[object] = [_clean_text(name, 80)]
    if not include_hidden:
        clauses.append("hidden=0")
    if not include_resolved:
        clauses.append("stage!='resolved'")
    rows = db.conn.execute(
        f"""
        SELECT * FROM character_conditions
        WHERE {' AND '.join(clauses)}
        ORDER BY severity DESC, id DESC
        """,
        params,
    ).fetchall()
    return [_row_payload(row) for row in rows]


def condition_summary(db: GameDB, name: str) -> Dict[str, object]:
    conditions = list_conditions(db, name, include_hidden=False)
    today = _current_day(db)
    if not conditions:
        hp_payload = character_health_payload(db, name)
        hp = int(hp_payload.get("hp") or 0)
        max_hp = max(1, int(hp_payload.get("max_hp") or 100))
        risk = str(hp_payload.get("mortality_risk") or "stable")
        health_score = max(0, min(100, round((hp / max_hp) * 100)))
        if risk == "dead":
            tags = ["已殁"]
            summary = "无显性病伤记录；人物已殁。"
        elif risk == "terminal":
            tags = ["濒危"]
            summary = "暂无显性病伤记录，但生命垂危。"
        elif risk == "critical":
            tags = ["气息危重"]
            summary = "暂无显性病伤记录，但体力危重。"
        elif risk == "serious":
            tags = ["体弱"]
            summary = "暂无显性病伤记录，但体力明显衰弱。"
        else:
            tags = ["体况平稳"]
            summary = "暂无显性病历记录；生命值与体况平稳。"
        return {
            "title": "病历",
            "health_score": health_score,
            "tags": tags,
            "summary": summary,
            "conditions": [],
            "groups": [],
            **hp_payload,
        }
    health_score = max(0, 100 - sum(_display_damage(item) for item in conditions))
    tags: List[str] = []
    for item in conditions:
        sev = int(item.get("severity") or 1)
        system = str(item.get("system") or "")
        kind = str(item.get("kind") or "")
        label = str(item.get("label") or "")
        group = _medical_group_for_condition(item)
        if system == "speech" or "舌" in label or "失声" in label:
            tags.append("言语受损")
        elif _is_castration_medical_fact(item) and system == "urinary":
            tags.append("排尿受损")
        elif _is_castration_medical_fact(item) and group == "psychological":
            tags.append("旧创心结")
        elif _is_castration_medical_fact(item):
            tags.append("生殖伤残")
        elif kind == "disease" and sev >= 4:
            tags.append("重病")
        elif kind in {"injury", "punishment", "prison_effect"} and sev >= 4:
            tags.append("重伤")
        elif kind == "terminal" or sev >= 5:
            tags.append("濒危")
        else:
            tags.append(label)
    deduped = list(dict.fromkeys(tag for tag in tags if tag))[:4]
    lead_labels = list(dict.fromkeys(_summary_label(item) for item in _summary_conditions(conditions) if _summary_label(item)))
    lead = "；".join(lead_labels[:3])
    summary_conditions = _summary_conditions(conditions)
    return {
        "title": "病历",
        "health_score": health_score,
        "tags": deduped,
        "summary": lead,
        # Compatibility list for older callers.  The structured `groups` field
        # is the canonical public medical record; keep this list concise so
        # permanent eunuch facts do not reappear as noisy "label(5/5)" chips.
        "conditions": [_public_condition_record(item) for item in summary_conditions],
        "groups": _medical_groups(conditions, today=today),
        **character_health_payload(db, name),
    }


def public_condition_payload(db: GameDB, name: str) -> Dict[str, object]:
    """Payload for character detail pages."""

    return condition_summary(db, name)


def _castration_medical_items(
    *,
    forced: bool = False,
    lore: Optional[Dict[str, object]] = None,
    note: str = "",
) -> List[Dict[str, object]]:
    lore = dict(lore or {})
    urinary = _clean_text(lore.get("urinary_aftereffect"), 160)
    aftereffect = _clean_text(lore.get("aftereffect"), 160)
    voice_body = _clean_text(lore.get("voice_body_change"), 160)
    trauma = _clean_text(lore.get("trauma_response"), 160)
    fixation = _clean_text(lore.get("private_fixation"), 160)
    psychosexual = _clean_text(lore.get("psychosexual_state"), 160)
    bao = _clean_text(lore.get("bao_status"), 40)
    ritual = _clean_text(lore.get("bao_ritual"), 160)
    source_note = _clean_text(note or lore.get("note") or ("强制宫刑" if forced else "净身入内廷"), 220)

    def item(
        key: str,
        *,
        kind: str,
        system: str,
        label: str,
        severity: int,
        group: str,
        stage: str = "disabled",
        organ: str = "",
        state: str = "",
        function: str = "",
        impact: str = "",
        privacy: str = "medical",
    ) -> Dict[str, object]:
        effects = {
            "record_group": group,
            "privacy": privacy,
        }
        if organ:
            effects["organ"] = organ
        if state:
            effects["state"] = state
        if function:
            effects["function"] = function
        if impact:
            effects["impact"] = impact
            effects["ability_delta"] = impact
        return {
            "condition_key": f"castration:{key}",
            "kind": kind,
            "system": system,
            "label": label,
            "severity": severity,
            "stage": stage,
            "note": source_note,
            "effects": effects,
            "chronic": True,
        }

    rows = [
        item("organ:left_testicle", kind="disability", system="reproductive", label="左侧睾丸缺失",
             severity=5, group="organic", organ="左侧睾丸", state="缺失"),
        item("organ:right_testicle", kind="disability", system="reproductive", label="右侧睾丸缺失",
             severity=5, group="organic", organ="右侧睾丸", state="缺失"),
        item("organ:penis", kind="disability", system="reproductive", label="阴茎缺失",
             severity=5, group="organic", organ="阴茎", state="缺失"),
        item("pathology:sterility", kind="disability", system="reproductive", label="绝育",
             severity=5, group="pathological", function="绝育", impact="无法繁衍后代"),
        item("pathology:sexual_dysfunction", kind="disability", system="reproductive", label="性功能丧失",
             severity=5, group="pathological", function="性功能丧失", impact="无法完成性行为或获得性快感"),
        item("pathology:urethral", kind="disability", system="urinary", label="尿道狭窄",
             severity=4 if urinary else 3, group="pathological", function="尿道狭窄",
             impact=urinary or "排尿不畅，可能尿频、漏尿或尿闭", stage="chronic"),
        item("pathology:pain", kind="disability", system="reproductive", label="慢性创痛",
             severity=4 if aftereffect else 3, group="pathological", function="慢性创痛",
             impact=aftereffect or "创口旧痛，久立久坐易受影响", stage="chronic"),
    ]
    if voice_body:
        rows.append(item("pathology:body_voice", kind="disability", system="general", label="体声改变",
                         severity=3, group="pathological", function="体声改变", impact=voice_body, stage="chronic"))
    if trauma:
        rows.append(item("psychological:trauma", kind="disability", system="mental", label="惊创反应",
                         severity=4 if forced else 3, group="psychological", function="惊创反应",
                         impact=trauma, stage="chronic", privacy="private"))
    if fixation or ritual or bao:
        rows.append(item("psychological:bao_fixation", kind="disability", system="mental", label="宝贝/全尸执念",
                         severity=3 if forced else 2, group="psychological", function="宝贝/全尸执念",
                         impact="惦念宝贝与全尸旧愿",
                         stage="chronic", privacy="private"))
    if psychosexual:
        rows.append(item("psychological:sexual_identity", kind="disability", system="mental", label="性心理改变",
                         severity=3, group="psychological", function="性心理改变",
                         impact=psychosexual, stage="chronic", privacy="private"))
    return rows


def sync_castration_medical_record(
    db: GameDB,
    state: GameState,
    name: str,
    *,
    forced: bool = False,
    lore: Optional[Dict[str, object]] = None,
    note: str = "",
    source_kind: str = "castration_lore",
    source_id: str = "",
) -> List[Dict[str, object]]:
    """Project castration/eunuch-lore facts into the shared medical record."""

    clean_name = _clean_text(name, 80)
    if not clean_name:
        return []
    try:
        with db.conn:
            db.conn.execute("UPDATE characters SET sex='eunuch' WHERE name=?", (clean_name,))
    except Exception:
        pass
    sid = _source_id(source_id, clean_name)
    applied: List[Dict[str, object]] = []
    for item in _castration_medical_items(forced=forced, lore=lore, note=note):
        try:
            applied.append(add_condition(
                db,
                state,
                clean_name,
                kind=str(item.get("kind") or "disability"),
                system=str(item.get("system") or "reproductive"),
                condition_key=str(item.get("condition_key") or ""),
                label=str(item.get("label") or ""),
                severity=int(item.get("severity") or 5),
                stage=str(item.get("stage") or "disabled"),
                note=str(item.get("note") or note or ""),
                effects=item.get("effects") if isinstance(item.get("effects"), dict) else {},
                chronic=bool(item.get("chronic", True)),
                source_kind=source_kind,
                source_id=sid,
            ))
        except Exception as exc:
            applied.append({"name": clean_name, "rejected": True, "reason": str(exc), "condition_key": item.get("condition_key")})
    return applied


def sync_all_castration_lore_medical_records(db: GameDB, state: GameState) -> int:
    """Backfill medical records from the legacy eunuch_lore table."""

    try:
        rows = db.conn.execute("SELECT * FROM eunuch_lore").fetchall()
    except Exception:
        return 0
    count = 0
    for row in rows:
        lore = {key: row[key] for key in row.keys()}
        name = str(lore.get("name") or "")
        if not name:
            continue
        sync_castration_medical_record(
            db,
            state,
            name,
            forced=bool(int(lore.get("forced") or 0)),
            lore=lore,
            note=str(lore.get("note") or ""),
            source_kind="castration_lore",
            source_id=name,
        )
        count += 1
    return count


def sync_eunuch_identity_medical_records(db: GameDB, state: GameState) -> int:
    """Backfill standard castration medical facts for sex=eunuch identities."""

    try:
        rows = db.conn.execute(
            """
            SELECT name
            FROM characters
            WHERE sex='eunuch'
            ORDER BY name
            """
        ).fetchall()
    except Exception:
        return 0
    count = 0
    for row in rows:
        name = str(row["name"] or "")
        if not name:
            continue
        exists = db.conn.execute(
            """
            SELECT 1
            FROM character_conditions
            WHERE name=?
              AND (condition_key LIKE 'castration:%' OR source_kind='castration_lore')
            LIMIT 1
            """,
            (name,),
        ).fetchone()
        if exists is not None:
            continue
        lore: Dict[str, object] = {}
        try:
            from ming_sim.eunuch_lore import get_lore

            lore = get_lore(db, name) or {}
        except Exception:
            lore = {}
        sync_castration_medical_record(
            db,
            state,
            name,
            forced=bool(lore.get("forced")) if lore else False,
            lore=lore,
            note=str(lore.get("note") or "旧档阉人身份回填病历"),
            source_kind="castration_lore",
            source_id=name,
        )
        count += 1
    return count


def dialogue_condition_brief(db: GameDB, name: str) -> str:
    """Hidden prompt fragment that makes bodily facts affect dialogue."""

    conditions = list_conditions(db, name, include_hidden=False)
    if not conditions:
        return ""
    prompt_conditions = _summary_conditions(conditions)
    if not prompt_conditions:
        return ""
    lines = ["【病历/身体事实（隐藏；会改变奏对能力，不得向玩家复述标题）】"]
    for item in prompt_conditions[:5]:
        effects = item.get("effects") if isinstance(item.get("effects"), dict) else {}
        bits = [
            str(item.get("label") or "身体异状"),
            f"严重度{int(item.get('severity') or 1)}/5",
            str(item.get("note") or ""),
        ]
        if effects:
            speech = effects.get("speech") or effects.get("speech_rule") or effects.get("language")
            if speech:
                bits.append(f"说话限制：{speech}")
            ability = effects.get("ability_delta")
            if ability:
                bits.append(f"办事折损：{ability}")
        lines.append("- " + "；".join(bit for bit in bits if bit))
    if any(str(c.get("system")) == "speech" or "舌" in str(c.get("label") or "") for c in prompt_conditions):
        lines.append(
            "口舌/咽喉受损者不得流利长篇奏对：句子要短，音节含混或以手势、书写补足；"
            "必要时用旁白说明其艰难表达。"
        )
    if any(int(c.get("severity") or 1) >= 4 for c in prompt_conditions):
        lines.append(
            "重病重伤者气力不支，回答会中断、迟疑或避重就轻；承诺执行时要显出身体限制。"
        )
    return "\n".join(lines)


def speech_impairment_level(db: GameDB, name: str) -> int:
    """Return the strongest active speech impairment severity for dialogue."""

    level = 0
    for item in list_conditions(db, name, include_hidden=False):
        if str(item.get("stage") or "") in {"resolved", "recovering"}:
            continue
        label = str(item.get("label") or "")
        system = str(item.get("system") or "")
        severity = int(item.get("severity") or 1)
        if system == "speech" or "舌" in label or "失声" in label or "喉" in label:
            if severity >= 4 or str(item.get("stage") or "") == "disabled":
                level = max(level, severity)
    return min(5, level)


def dialogue_answer_impairment_active(db: GameDB, name: str) -> bool:
    return speech_impairment_level(db, name) >= 4


def apply_dialogue_answer_impairment(db: GameDB, name: str, answer: str) -> str:
    """Deterministically make visible dialogue obey severe speech damage.

    The LLM still receives the hidden condition brief, but this small display
    pass keeps severe tongue/throat injuries from being rendered as perfectly
    fluent speech when the model misses the instruction.
    """

    text = str(answer or "").strip()
    if not text:
        return text
    level = speech_impairment_level(db, name)
    if level < 4:
        return text
    if "口舌受损" in text or "发声艰难" in text:
        return text
    if text.count("……") >= 2 or text.count("…") >= 4:
        return text
    stage = f"{name}口舌受损，发声艰难，只能断续示意"
    spoken = re.sub(r"\s+", "", text)
    spoken = re.sub(r"^[（(][^）)]{2,80}[）)]", "", spoken).strip()
    if len(spoken) > 180:
        spoken = spoken[:180] + "……"
    chunk_size = 8 if level >= 5 else 12
    chunks = [spoken[i:i + chunk_size] for i in range(0, len(spoken), chunk_size)]
    broken = "……".join(part for part in chunks if part)
    if not broken:
        broken = "唔……"
    prefix = "呃……" if level >= 5 else ""
    return f"（{stage}。）{prefix}{broken}"


def character_health_payload(db: GameDB, name: str) -> Dict[str, object]:
    row = db.conn.execute(
        "SELECT hp, max_hp, status FROM characters WHERE name=?",
        (_clean_text(name, 80),),
    ).fetchone()
    if row is None:
        return {"hp": 0, "max_hp": 0, "mortality_risk": "unknown"}
    hp = int(row["hp"] or 0)
    max_hp = max(1, int(row["max_hp"] or 100))
    ratio = hp / max_hp
    if str(row["status"] or "") == "dead" or hp <= 0:
        risk = "dead"
    elif hp <= 5 or ratio <= 0.08:
        risk = "terminal"
    elif hp <= 25 or ratio <= 0.25:
        risk = "critical"
    elif hp <= 50 or ratio <= 0.5:
        risk = "serious"
    else:
        risk = "stable"
    return {"hp": hp, "max_hp": max_hp, "mortality_risk": risk}


def _condition_is_fatal(item: Dict[str, object]) -> bool:
    effects = item.get("effects") if isinstance(item.get("effects"), dict) else {}
    label_note = f"{item.get('label') or ''} {item.get('note') or ''}"
    return (
        str(item.get("stage") or "") == "dead"
        or _truthy_effect(effects, "fatal", "dead", "death")
        or bool(_DEATH_RE.search(label_note))
    )


def _health_damage_from_condition(item: Dict[str, object]) -> int:
    if _is_castration_medical_fact(item):
        group = _medical_group_for_condition(item)
        system = str(item.get("system") or "")
        if group == "organic":
            return 1
        if system == "urinary":
            return 5
        if group == "pathological":
            return 3
        return 2
    sev = _clamp(item.get("severity"), default=1)
    damage = _SEVERITY_DAMAGE.get(sev, 4)
    if str(item.get("kind") or "") == "terminal":
        damage += 25
    if str(item.get("stage") or "") == "critical":
        damage += 10
    if str(item.get("stage") or "") == "disabled":
        damage += 5
    return damage


def _health_cap_from_conditions(conditions: List[Dict[str, object]]) -> int:
    damage = 0
    for item in conditions:
        if str(item.get("stage") or "") in {"resolved", "recovering"}:
            continue
        damage += _health_damage_from_condition(item)
    if any(str(c.get("kind") or "") == "terminal" and int(c.get("severity") or 1) >= 5 for c in conditions):
        return 1
    return max(1, 100 - damage)


def _only_castration_health_facts(conditions: List[Dict[str, object]]) -> bool:
    active = [item for item in conditions if str(item.get("stage") or "") not in {"resolved", "recovering"}]
    return bool(active) and all(_is_castration_medical_fact(item) for item in active)


def sync_character_health(db: GameDB, state: GameState, name: str) -> Dict[str, object]:
    clean_name = _clean_text(name, 80)
    row = db.conn.execute(
        "SELECT hp, max_hp, status FROM characters WHERE name=?",
        (clean_name,),
    ).fetchone()
    if row is None:
        return {}
    conditions = list_conditions(db, clean_name, include_hidden=True)
    if not conditions:
        return {}
    old_hp = int(row["hp"] or 100)
    max_hp = max(1, int(row["max_hp"] or 100))
    fatal = any(_condition_is_fatal(item) for item in conditions)
    target_hp = 0 if fatal else min(max_hp, _health_cap_from_conditions(conditions))
    if not fatal and old_hp < target_hp and _only_castration_health_facts(conditions):
        new_hp = target_hp
    else:
        new_hp = min(old_hp, target_hp)
    if new_hp == old_hp and not fatal:
        return {"hp_before": old_hp, "hp_after": old_hp, "max_hp": max_hp}
    with db.conn:
        db.conn.execute("UPDATE characters SET hp=? WHERE name=?", (new_hp, clean_name))
    status_payload: Dict[str, object] = {}
    if fatal and str(row["status"] or "") != "dead":
        cause = next((str(c.get("label") or c.get("note") or "伤病致死") for c in conditions if _condition_is_fatal(c)), "伤病致死")
        try:
            db.set_character_status(state, clean_name, "dead", cause[:200])
            status_payload = {"status": "dead", "reason": cause[:200]}
        except Exception as exc:
            status_payload = {"status_rejected": str(exc)}
    return {"hp_before": old_hp, "hp_after": new_hp, "max_hp": max_hp, **status_payload}


def _pick(raw: Dict[str, object], *keys: str, default: object = "") -> object:
    for key in keys:
        if key in raw and raw.get(key) not in (None, ""):
            return raw.get(key)
    return default


def normalize_condition_item(item: Dict[str, object]) -> Dict[str, object]:
    name = _clean_text(_pick(item, "name", "姓名"), 80)
    label = _clean_text(_pick(item, "label", "display", "病名", "伤名", "状况", "condition"), 80)
    key = _clean_text(_pick(item, "condition_key", "key", "键", "编号", "id"), 80)
    severity = _clamp(_pick(item, "severity", "严重度", default=1))
    effects = _pick(item, "effects", "effects_json", "影响", "impact", default={})
    if not isinstance(effects, dict):
        effects = _json_dict(effects)
    fatal = _pick(item, "fatal", "dead", "death", "致死", "死亡", default=None)
    if fatal is not None:
        effects["fatal"] = _bool(fatal)
    return {
        "name": name,
        "kind": _norm_kind(_pick(item, "kind", "类型", default="other")),
        "system": _norm_system(_pick(item, "system", "系统", default="general")),
        "condition_key": key,
        "label": label,
        "severity": severity,
        "stage": _norm_stage(_pick(item, "stage", "stage_text", "阶段", "病程", "伤势", default=""), severity),
        "note": _clean_text(_pick(item, "note", "备注", "reason", "原因"), 240),
        "effects": effects,
        "hidden": _bool(_pick(item, "hidden", "隐藏", default=False)),
        "chronic": _bool(_pick(item, "chronic", "慢性", default=False)),
        "duration_days": _nonnegative_int(_pick(item, "duration_days", "持续天数", default=0)),
    }


def apply_condition_changes(
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
            norm = normalize_condition_item(item)
        except Exception as exc:
            applied.append({"rejected": True, "reason": f"字段规范化失败：{exc}"})
            continue
        if not norm["name"] or not (norm["label"] or norm["condition_key"]):
            applied.append({"name": norm["name"], "rejected": True, "reason": "姓名或状况为空"})
            continue
        try:
            row = add_condition(
                db,
                state,
                str(norm["name"]),
                kind=str(norm["kind"]),
                system=str(norm["system"]),
                condition_key=str(norm["condition_key"]),
                label=str(norm["label"]),
                severity=int(norm["severity"]),
                stage=str(norm["stage"]),
                note=str(norm["note"]),
                effects=norm["effects"] if isinstance(norm["effects"], dict) else {},
                hidden=bool(norm["hidden"]),
                chronic=bool(norm["chronic"]),
                duration_days=int(norm["duration_days"]),
                source_kind=source_kind,
                source_id=source_id,
            )
            applied.append(row)
        except Exception as exc:
            applied.append({"name": norm["name"], "rejected": True, "reason": str(exc)})
    return applied
