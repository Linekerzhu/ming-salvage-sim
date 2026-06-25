"""人物老化·病·死·继承（CK3 化 P3「王朝长河」）：让朝局自然新陈代谢。

每个人物自带 historical_death_year/month（史料卒年），原先从不触发——名册万年不变。
本模块每月初按卒年 + 高龄病故概率令在朝官员自然凋零：
  - 病逝 → status=dead（自动削职）、讣告入朝报、好感网哀荣涟漪（党羽悲恸、政敌伺机）、派系势力消长。
  - 若殁者居要职 → 入"继任"队列，由 court_events 的 succession 抉择让陛下点替（与活的宫廷/抉择联动）。
"""

from __future__ import annotations

import json
import random
from typing import Dict, List

from ming_sim.db import GameDB
from ming_sim.models import GameState

KV_VACANCIES = "upgrade.succession_vacancies"
MAX_DEATHS_PER_MONTH = 2  # 防一月齐殁，节奏可控
MAX_ILLNESSES_PER_MONTH = 3  # 老病也要有节奏，不能一月满朝抱病
MAX_DISEASE_PROGRESSIONS_PER_MONTH = 3
MAX_DISEASE_DEATHS_PER_MONTH = 1

# 要职关键词：殁则触发继任抉择
_KEY_OFFICE = ("大学士", "尚书", "侍郎", "督师", "总督", "巡抚", "总兵", "都御史", "阁")

_NATURAL_DISEASES = (
    {
        "key": "wind_cold",
        "label": "风寒",
        "system": "respiratory",
        "effect": "恶寒发热，咳嗽鼻塞，奏对时气短声哑",
        "speech": "风寒咳嗽，句子宜短",
        "course_kind": "acute",
        "duration_days": 5,
        "possible_outcomes": ["恢复", "加重"],
        "recovery_chance": 0.62,
    },
    {
        "key": "phthisis",
        "label": "肺痨",
        "system": "respiratory",
        "effect": "久咳虚热，气力渐耗，长篇奏对易中断",
        "speech": "久咳虚喘，发声断续",
        "course_kind": "chronic",
        "possible_outcomes": ["缓解", "加重"],
        "chronic": True,
    },
    {
        "key": "hemorrhoids",
        "label": "痔疮",
        "system": "digestive",
        "effect": "坐立疼痛，久坐奏事与长途差遣皆受扰",
        "course_kind": "chronic",
        "possible_outcomes": ["缓解", "加重"],
        "chronic": True,
    },
    {
        "key": "cough_asthma",
        "label": "咳喘",
        "system": "respiratory",
        "effect": "气短咳逆，长篇奏对易中断",
    },
    {
        "key": "heart_palpitations",
        "label": "心悸",
        "system": "circulatory",
        "effect": "心气不稳，久立久议皆损气力",
    },
    {
        "key": "wind_dizziness",
        "label": "风眩",
        "system": "nervous",
        "effect": "眩晕迟疑，临事判断易失稳",
    },
    {
        "key": "fright_palpitations",
        "label": "惊悸",
        "system": "mental",
        "effect": "惊惶失眠，奏对多疑失序",
    },
    {
        "key": "stomach_weakness",
        "label": "胃弱",
        "system": "digestive",
        "effect": "饮食难进，精神与耐力俱减",
    },
    {
        "key": "urinary_drip",
        "label": "遗溺",
        "system": "urinary",
        "effect": "入值久坐不便，仪态受损",
    },
    {
        "key": "yin_shan",
        "label": "阴疝",
        "system": "reproductive",
        "effect": "下部痛楚，久立入值皆难支撑",
    },
    {
        "key": "joint_pain",
        "label": "痹痛",
        "system": "musculoskeletal",
        "effect": "筋骨疼痛，行走久立困难",
    },
    {
        "key": "skin_ulcers",
        "label": "疮疡",
        "system": "skin",
        "effect": "肌肤溃痛，衣冠仪态与睡眠皆受扰",
    },
    {
        "key": "throat_blockage",
        "label": "喉痹",
        "system": "speech",
        "effect": "咽喉肿痛，发声短促含混",
        "speech": "咽喉疼痛，不能久谈，句子须短",
    },
    {
        "key": "consumption",
        "label": "虚劳",
        "system": "general",
        "effect": "气血亏虚，办事耐力明显下降",
    },
)
_RARE_STRANGE_DISEASES = (
    {
        "key": "huo_huo",
        "label": "狐惑",
        "system": "general",
        "effect": "口咽与阴部溃痛，神思烦乱，奏对难以持久",
        "speech": "口咽溃痛，发声艰涩",
        "weird": True,
    },
    {
        "key": "corpse_syncope",
        "label": "尸厥",
        "system": "nervous",
        "effect": "忽然昏厥如死，醒后神识迟滞",
        "weird": True,
    },
    {
        "key": "blood_sweat",
        "label": "血汗",
        "system": "skin",
        "effect": "汗中带血，惊动内外，气血大亏",
        "weird": True,
    },
)
_VITAL_DISEASE_SYSTEMS = {"general", "respiratory", "circulatory", "nervous", "digestive", "urinary"}


def _age(birth_year: int, year: int) -> int:
    return (year - int(birth_year)) if birth_year else 0


def _is_key_office(office: str) -> bool:
    return any(k in str(office or "") for k in _KEY_OFFICE)


def vacancies(db: GameDB) -> List[Dict[str, object]]:
    try:
        d = json.loads(db.kv_get(KV_VACANCIES) or "[]")
        return d if isinstance(d, list) else []
    except ValueError:
        return []


def _push_vacancy(db: GameDB, vac: Dict[str, object]) -> None:
    vs = vacancies(db)
    vs.append(vac)
    db.kv_set(KV_VACANCIES, json.dumps(vs[-8:], ensure_ascii=False))


def pop_vacancy(db: GameDB) -> Dict[str, object] | None:
    vs = vacancies(db)
    if not vs:
        return None
    head = vs.pop(0)
    db.kv_set(KV_VACANCIES, json.dumps(vs, ensure_ascii=False))
    return head


def _will_die(rng: random.Random, year: int, month: int, dy: int, dm: int, age: int) -> str:
    """返回死因（空串=不死）。史料卒年到点必殁；高龄另有递增病故概率。"""
    if dy and (year > dy or (year == dy and month >= (dm or 12))):
        return "寿数"
    if age >= 70:
        # 每月病故概率随龄递增：70→~1.2%，80→~5.2%，90→~9.2%。
        p = max(0.0, (age - 66) * 0.004)
        if rng.random() < p:
            return "高龄染疾"
    return ""


def _disease_progression_risk(age: int, hp: int, severity: int, chronic: bool) -> float:
    risk = max(0.0, (int(age) - 55) * 0.012)
    risk += max(0, int(severity) - 2) * 0.16
    if chronic:
        risk += 0.08
    if hp <= 50:
        risk += 0.10
    if hp <= 25:
        risk += 0.18
    if hp <= 5:
        risk += 0.30
    if age >= 95:
        return 1.0
    return min(0.78, risk)


def _terminal_death_due(age: int, hp: int, severity: int, stage: str, kind: str) -> bool:
    if int(severity) < 5:
        return False
    if str(kind or "") == "terminal" or str(stage or "") == "critical":
        return int(hp) <= 5 or int(age) >= 96
    return False


def _json_dict(raw: object) -> Dict[str, object]:
    if isinstance(raw, dict):
        return dict(raw)
    try:
        data = json.loads(str(raw or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        data = {}
    return data if isinstance(data, dict) else {}


def _effect_int(effects: Dict[str, object], key: str, default: int = 0) -> int:
    try:
        return int(effects.get(key) or default)
    except (TypeError, ValueError):
        return default


def _disease_effects(disease: Dict[str, object], *, day: int) -> Dict[str, object]:
    effects: Dict[str, object] = {
        "record_group": "other",
        "impact": str(disease.get("effect") or ""),
        "ability_delta": str(disease.get("effect") or ""),
        "privacy": "medical",
    }
    if disease.get("speech"):
        effects["speech"] = str(disease.get("speech") or "")
    course_kind = str(disease.get("course_kind") or "").strip()
    if course_kind:
        effects["course_kind"] = course_kind
    possible = disease.get("possible_outcomes")
    if isinstance(possible, list):
        effects["possible_outcomes"] = [str(item) for item in possible if str(item)]
    duration = int(disease.get("duration_days") or 0)
    if duration > 0:
        effects["next_check_day"] = int(day) + duration
    if disease.get("recovery_chance") is not None:
        effects["recovery_chance"] = float(disease.get("recovery_chance") or 0)
    return effects


def _progression_stage(system: str, severity: int) -> tuple[str, str]:
    if int(severity) >= 5:
        if str(system or "") in _VITAL_DISEASE_SYSTEMS:
            return "terminal", "critical"
        return "disease", "disabled"
    if int(severity) >= 4:
        return "disease", "serious"
    return "disease", "active"


def _record_disease_death(
    db: GameDB,
    state: GameState,
    row,
    *,
    cause: str,
    day: int,
) -> Dict[str, object]:
    from ming_sim.timeflow import LEVEL_RED

    name = str(row["name"])
    office = str(row["office"] or "")
    office_type = str(row["office_type"] or "")
    faction = str(row["faction"] or "")
    age = _age(int(row["birth_year"] or 0), int(state.year))
    age_txt = f"享年{age}" if age else "卒"
    try:
        from ming_sim import court

        for ally in court.allies_of(db, name, limit=5):
            court._adjust_char(db, ally["name"], grievance=+3)
        if faction and faction not in ("无", "中立"):
            db.adjust_factions({faction: {"leverage": -4, "satisfaction": -2}})
    except Exception:
        pass
    db.set_character_status(state, name, "dead", f"{cause}，{age_txt}")
    db.conn.commit()
    detail = (
        f"{office}{name}{age_txt}，以{cause}不治而亡。"
        + ("要缺出，亟须简替。" if _is_key_office(office) else "其缺由部院循例铨补。")
    )
    try:
        from ming_sim.eunuch_lore import burial_lament_on_death
        lament = burial_lament_on_death(db, state, name, day)
        if lament:
            detail += lament
    except Exception:
        pass
    if _is_key_office(office):
        _push_vacancy(db, {"office": office, "office_type": office_type, "faction": faction, "deceased": name, "day": int(day)})
    db.record_log(state, f"【病逝】{office}{name}{age_txt}，{cause}。")
    return {
        "level": LEVEL_RED,
        "kind": "disease_death",
        "title": f"病逝：{name}{age_txt}",
        "detail": detail,
        "ref_kind": "character",
        "ref_id": name,
        "day": int(day),
    }


def _morbidity_risk(age: int, hp: int, existing_count: int) -> float:
    """Monthly natural-disease chance. Age dominates; frailty adds pressure."""

    if age < 50 and hp > 50:
        return 0.0
    risk = max(0.0, (age - 45) * 0.006)
    if age >= 70:
        risk += 0.08
    if age >= 80:
        risk += 0.10
    if hp <= 50:
        risk += 0.08
    if hp <= 25:
        risk += 0.10
    # Already sick courtiers are more fragile, but cap so it remains sparse.
    risk += min(0.12, max(0, existing_count) * 0.03)
    if age >= 95:
        return 1.0
    return min(0.42, risk)


def _morbidity_severity(age: int, hp: int, existing_severity: int = 0) -> int:
    sev = 1
    if age >= 55:
        sev = 2
    if age >= 70:
        sev = 3
    if age >= 85:
        sev = 4
    if hp <= 25:
        sev = max(sev, 4)
    if existing_severity:
        sev = max(sev, min(4, existing_severity + (1 if age >= 75 else 0)))
    return max(1, min(4, sev))


def disease_catalog_systems() -> List[str]:
    """Public-ish test/diagnostic helper for the natural disease catalog."""

    systems = [
        str(item.get("system") or "")
        for item in (*_NATURAL_DISEASES, *_RARE_STRANGE_DISEASES)
        if str(item.get("system") or "")
    ]
    return list(dict.fromkeys(systems))


def _pick_natural_disease(rng: random.Random, *, age: int, hp: int) -> Dict[str, object]:
    if (int(age) >= 65 or int(hp) <= 35) and rng.random() < 0.10:
        return dict(rng.choice(_RARE_STRANGE_DISEASES))
    return dict(rng.choice(_NATURAL_DISEASES))


def disease_progression_tick(db: GameDB, state: GameState, day: int) -> List[Dict[str, object]]:
    """月初调用：已有疾病按年龄、HP、等级恶化，危笃者可能不治。"""

    from ming_sim.timeflow import LEVEL_BLUE, LEVEL_RED, LEVEL_YELLOW
    from ming_sim.conditions import add_condition

    rng = random.Random(f"disease-progression:{int(day)}:{int(state.year)}:{int(state.period)}")
    rows = db.conn.execute(
        """
        SELECT
            cc.*,
            c.office AS office,
            c.office_type AS office_type,
            c.faction AS faction,
            c.birth_year AS birth_year,
            c.hp AS hp
        FROM character_conditions cc
        JOIN characters c ON c.name=cc.name
        WHERE c.status='active'
          AND c.power_id='ming'
          AND c.office_type!='后宫'
          AND cc.kind IN ('disease', 'terminal')
          AND cc.stage NOT IN ('resolved', 'recovering', 'dead')
        ORDER BY cc.severity DESC, c.birth_year, cc.id
        """
    ).fetchall()
    events: List[Dict[str, object]] = []
    progressions = 0
    deaths = 0
    seen: set[str] = set()
    for row in rows:
        name = str(row["name"] or "")
        if not name or name in seen:
            continue
        age = _age(int(row["birth_year"] or 0), int(state.year))
        hp = int(row["hp"] or 100)
        severity = int(row["severity"] or 1)
        stage = str(row["stage"] or "")
        kind = str(row["kind"] or "")
        label = str(row["label"] or row["condition_key"] or "旧疾")
        effects = _json_dict(row["effects_json"] if "effects_json" in row.keys() else "{}")
        next_check_day = _effect_int(effects, "next_check_day", 0)
        course_kind = str(effects.get("course_kind") or "")
        if course_kind == "acute" and next_check_day and int(day) >= next_check_day:
            if progressions >= MAX_DISEASE_PROGRESSIONS_PER_MONTH:
                break
            recovery_chance = float(effects.get("recovery_chance") or 0.55)
            recovery_chance += 0.12 if hp >= 70 else 0.0
            recovery_chance -= 0.14 if age >= 70 else 0.0
            recovery_chance -= 0.10 if severity >= 4 else 0.0
            if rng.random() < max(0.08, min(0.86, recovery_chance)):
                effects.pop("next_check_day", None)
                effects["possible_outcomes"] = ["复原"]
                effect_text = f"{label}已过急期，尚需调养"
                payload = add_condition(
                    db,
                    state,
                    name,
                    kind="disease",
                    system=str(row["system"] or "general"),
                    condition_key=str(row["condition_key"] or ""),
                    label=label,
                    severity=max(1, severity - 1),
                    stage="recovering",
                    note=f"{str(row['note'] or label)[:120]}；病势转缓",
                    effects={**effects, "impact": effect_text, "ability_delta": effect_text},
                    hidden=bool(int(row["hidden"] or 0)),
                    chronic=False,
                    duration_days=0,
                    source_kind=str(row["source_kind"] or "lifespan"),
                    source_id=str(row["source_id"] or "natural-aging"),
                )
                health = payload.get("health") if isinstance(payload, dict) else {}
                hp_after = int(health.get("hp_after") or hp) if isinstance(health, dict) else hp
                events.append({
                    "level": LEVEL_BLUE,
                    "kind": "disease_recovery",
                    "title": f"病势转缓：{name}{label}",
                    "detail": f"{row['office'] or ''}{name}{label}转入恢复，体力约{hp_after}/100。",
                    "ref_kind": "character",
                    "ref_id": name,
                    "day": int(day),
                })
                db.record_log(state, f"【病势】{name}{label}转缓，入恢复。")
            else:
                duration = int(row["duration_days"] or 0) or 5
                effects["next_check_day"] = int(day) + duration
                effects["possible_outcomes"] = ["恢复", "加重"]
                new_severity = min(5, severity + 1)
                new_kind, new_stage = _progression_stage(str(row["system"] or "general"), new_severity)
                effect_text = "急病加重，发热咳逆更甚，奏对与任事耐力下降"
                payload = add_condition(
                    db,
                    state,
                    name,
                    kind=new_kind,
                    system=str(row["system"] or "general"),
                    condition_key=str(row["condition_key"] or ""),
                    label=label,
                    severity=new_severity,
                    stage=new_stage,
                    note=f"{str(row['note'] or label)[:120]}；到期判定加重",
                    effects={**effects, "impact": effect_text, "ability_delta": effect_text},
                    hidden=bool(int(row["hidden"] or 0)),
                    chronic=False,
                    duration_days=duration,
                    source_kind=str(row["source_kind"] or "lifespan"),
                    source_id=str(row["source_id"] or "natural-aging"),
                )
                health = payload.get("health") if isinstance(payload, dict) else {}
                hp_after = int(health.get("hp_after") or hp) if isinstance(health, dict) else hp
                events.append({
                    "level": LEVEL_YELLOW if new_stage != "critical" else LEVEL_RED,
                    "kind": "disease_progression",
                    "title": f"病势加重：{name}{label}",
                    "detail": f"{row['office'] or ''}{name}{label}加重至{new_severity}/5，体力降至{hp_after}/100。",
                    "ref_kind": "character",
                    "ref_id": name,
                    "day": int(day),
                })
                db.record_log(state, f"【病势】{name}{label}到期加重，严重度{new_severity}/5。")
            progressions += 1
            seen.add(name)
            continue
        if deaths < MAX_DISEASE_DEATHS_PER_MONTH and _terminal_death_due(age, hp, severity, stage, kind):
            events.append(_record_disease_death(db, state, row, cause=label, day=int(day)))
            deaths += 1
            seen.add(name)
            continue
        if progressions >= MAX_DISEASE_PROGRESSIONS_PER_MONTH:
            break
        chronic = bool(int(row["chronic"] or 0))
        risk = _disease_progression_risk(age, hp, severity, chronic)
        if rng.random() >= risk:
            continue
        new_severity = min(5, severity + 1)
        new_kind, new_stage = _progression_stage(str(row["system"] or "general"), new_severity)
        effect = "旧疾加重，奏对与任事耐力继续下降"
        if new_stage == "critical":
            effect = "病入危笃，气力几尽，难以承办差事"
        elif new_stage == "disabled":
            effect = "病势至功能丧失，相关身体系统难以正常任事"
        payload = add_condition(
            db,
            state,
            name,
            kind=new_kind,
            system=str(row["system"] or "general"),
            condition_key=str(row["condition_key"] or ""),
            label=label,
            severity=new_severity,
            stage=new_stage,
            note=f"{str(row['note'] or label)[:120]}；旧疾加重",
            effects={**effects, "impact": effect, "ability_delta": effect},
            hidden=bool(int(row["hidden"] or 0)),
            chronic=True,
            duration_days=int(row["duration_days"] or 0),
            source_kind=str(row["source_kind"] or "lifespan"),
            source_id=str(row["source_id"] or "natural-aging"),
        )
        health = payload.get("health") if isinstance(payload, dict) else {}
        hp_after = int(health.get("hp_after") or hp) if isinstance(health, dict) else hp
        level = LEVEL_RED if new_stage == "critical" else LEVEL_YELLOW
        events.append({
            "level": level,
            "kind": "disease_progression",
            "title": f"病势加重：{name}{label}",
            "detail": f"{row['office'] or ''}{name}{label}加重至{new_severity}/5，体力降至{hp_after}/100。",
            "ref_kind": "character",
            "ref_id": name,
            "day": int(day),
        })
        db.record_log(state, f"【病势】{name}{label}加重，严重度{new_severity}/5。")
        progressions += 1
        seen.add(name)
    return events


def morbidity_tick(db: GameDB, state: GameState, day: int) -> List[Dict[str, object]]:
    """月初调用：高龄与体弱者低频自然染疾，落入通用身体条件层。"""

    from ming_sim.timeflow import LEVEL_BLUE, LEVEL_YELLOW
    from ming_sim.conditions import add_condition

    year = int(state.year)
    rng = random.Random(f"morbidity:{int(day)}:{year}:{int(state.period)}")
    rows = db.conn.execute(
        """
        SELECT name, office, birth_year, hp
        FROM characters
        WHERE status='active' AND power_id='ming' AND office_type!='后宫'
        ORDER BY birth_year, name
        """
    ).fetchall()
    events: List[Dict[str, object]] = []
    illnesses = 0
    for r in rows:
        if illnesses >= MAX_ILLNESSES_PER_MONTH:
            break
        name = str(r["name"])
        age = _age(int(r["birth_year"] or 0), year)
        hp = int(r["hp"] or 100)
        if age <= 0:
            continue
        existing_count = int(db.conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM character_conditions
            WHERE name=? AND kind='disease' AND stage!='resolved'
            """,
            (name,),
        ).fetchone()["n"] or 0)
        if rng.random() >= _morbidity_risk(age, hp, existing_count):
            continue
        disease = _pick_natural_disease(rng, age=age, hp=hp)
        condition_key = f"natural:{disease['key']}"
        existing = db.conn.execute(
            """
            SELECT severity
            FROM character_conditions
            WHERE name=? AND condition_key=? AND source_kind='lifespan' AND source_id='natural-aging'
            """,
            (name, condition_key),
        ).fetchone()
        old_severity = int(existing["severity"] or 0) if existing is not None else 0
        severity = _morbidity_severity(age, hp, old_severity)
        if old_severity and severity <= old_severity:
            continue
        stage = "serious" if severity >= 4 else ("active" if severity >= 3 else "mild")
        effects = _disease_effects(disease, day=int(day))
        note = f"年高体弱，{'奇疾' if disease.get('weird') else '自然染疾'}；享年约{age}"
        payload = add_condition(
            db,
            state,
            name,
            kind="disease",
            system=str(disease["system"]),
            condition_key=condition_key,
            label=str(disease["label"]),
            severity=severity,
            stage=stage,
            note=note,
            effects=effects,
            chronic=bool(disease.get("chronic")) or age >= 70,
            duration_days=int(disease.get("duration_days") or 0),
            source_kind="lifespan",
            source_id="natural-aging",
        )
        health = payload.get("health") if isinstance(payload, dict) else {}
        hp_after = int(health.get("hp_after") or hp) if isinstance(health, dict) else hp
        office = str(r["office"] or "")
        title = f"{'奇疾' if disease.get('weird') else '病讯'}：{name}{disease['label']}"
        detail = (
            f"{office}{name}年约{age}，{disease['label']}"
            f"{'加重' if old_severity else '初作'}，体力降至{hp_after}/100。"
        )
        level = LEVEL_YELLOW if severity >= 4 else LEVEL_BLUE
        events.append({
            "level": level, "kind": "morbidity", "title": title,
            "detail": detail, "ref_kind": "character", "ref_id": name, "day": int(day),
        })
        db.record_log(state, f"【病讯】{name}{disease['label']}，严重度{severity}/5。")
        illnesses += 1
    return events


def mortality_tick(db: GameDB, state: GameState, day: int) -> List[Dict[str, object]]:
    """月初调用：在朝明廷官员按卒年/高龄自然凋零。返回讣告事件列表。"""
    from ming_sim.timeflow import LEVEL_YELLOW
    from ming_sim import court

    year, month = int(state.year), int(state.period)
    rng = random.Random((int(day) * 0x85EBCA6B) % (2 ** 31))
    rows = db.conn.execute(
        "SELECT name, office, office_type, faction, birth_year, "
        "historical_death_year AS dy, historical_death_month AS dm "
        "FROM characters WHERE status='active' AND power_id='ming' AND office_type!='后宫'"
    ).fetchall()
    events: List[Dict[str, object]] = []
    deaths = 0
    for r in rows:
        if deaths >= MAX_DEATHS_PER_MONTH:
            break
        name = str(r["name"])
        age = _age(int(r["birth_year"] or 0), year)
        cause = _will_die(rng, year, month, int(r["dy"] or 0), int(r["dm"] or 0), age)
        if not cause:
            continue
        office = str(r["office"] or "")
        office_type = str(r["office_type"] or "")
        faction = str(r["faction"] or "")
        # 好感网哀荣涟漪：党羽悲恸（怨气微增——痛失奥援），政敌伺机（无明面动作，仅记录）。
        allies = court.allies_of(db, name, limit=5)
        for a in allies:
            court._adjust_char(db, a["name"], grievance=+3)
        # 派系势力消长：本派失一人则势力降，宿党相对得隙。
        if faction and faction not in ("无", "中立"):
            db.adjust_factions({faction: {"leverage": -4, "satisfaction": -2}})
            rival = court.rivals_of(db, name, limit=1)
            if rival:
                rf = db.conn.execute("SELECT faction FROM characters WHERE name=?", (rival[0]["name"],)).fetchone()
                if rf and str(rf["faction"]) not in ("", "无", "中立", faction):
                    db.adjust_factions({str(rf["faction"]): {"leverage": +2}})
        age_txt = f"享年{age}" if age else "卒"
        db.set_character_status(state, name, "dead", f"{cause}，{age_txt}")
        db.conn.commit()
        deaths += 1
        detail = (f"{office}{name}{age_txt}，以{cause}卒于任上。"
                  + ("要缺出，亟须简替。" if _is_key_office(office) else "其缺由部院循例铨补。"))
        # 净身者全尸执念（E2a）：无宝者不得全尸，党羽哀恸更深；有宝者凑全尸稍慰。
        try:
            from ming_sim.eunuch_lore import burial_lament_on_death
            lament = burial_lament_on_death(db, state, name, day)
            if lament:
                detail += lament
        except Exception:
            pass
        events.append({
            "level": LEVEL_YELLOW, "kind": "obituary",
            "title": f"讣告：{name}{age_txt}",
            "detail": detail, "ref_kind": "character", "ref_id": name, "day": day,
        })
        db.record_log(state, f"【讣告】{office}{name}{age_txt}，{cause}。")
        if _is_key_office(office):
            _push_vacancy(db, {"office": office, "office_type": office_type,
                               "faction": faction, "deceased": name, "day": int(day)})
    return events
