"""Shared personnel mutations that are larger than a simple office write."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Dict, List, Tuple

from ming_sim.content import GameContent
from ming_sim.db import GameDB, normalize_office
from ming_sim.models import Character, GameState
from ming_sim.political_reactions import (
    Reaction,
    apply_castration_reaction,
    character_political_row,
)


def is_eunuch_office(office: str, office_type: str = "") -> bool:
    text = f"{office or ''} {office_type or ''}"
    return bool(re.search(r"司礼监|东厂|太监|宦官|内廷|内官监|内官|御马监|御用监|尚膳监|小火者", text))


def _json_list(raw: object) -> List[str]:
    if isinstance(raw, list):
        return [str(item) for item in raw if str(item).strip()]
    try:
        value = json.loads(str(raw or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        value = []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _clamp_0_100(value: int) -> int:
    return max(0, min(100, int(value)))


def _add_character_traits(db: GameDB, name: str, traits: List[str]) -> None:
    seen: set[str] = set()
    for trait in traits:
        clean = str(trait or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        valence = -1
        if clean in {"内廷奴籍", "服从依恋"}:
            valence = 0
        db.conn.execute(
            "INSERT OR IGNORE INTO character_traits (name, trait, valence) VALUES (?,?,?)",
            (name, clean, int(valence)),
        )


def _is_adult_in_year(year: int, birth_year: int) -> bool:
    if int(birth_year or 0) <= 0 or int(year or 0) <= 0:
        return True
    return int(year) - int(birth_year) >= 18


def _grant_player_item_once(db: GameDB, item_id: str, state: GameState) -> bool:
    clean = str(item_id or "").strip()
    if not clean:
        return False
    exists = db.conn.execute(
        "SELECT 1 FROM player_inventory WHERE item_id=?",
        (clean,),
    ).fetchone()
    if exists is not None:
        return False
    db.grant_player_item(clean, state)
    return True


def _castration_lore_text(lore: Dict[str, object], *, include_psychosexual: bool) -> str:
    keys = [
        "castration_method",
        "knife_tool",
        "anesthesia",
        "procedure_note",
        "bao_preservation",
        "bao_container",
        "bao_ritual",
        "aftereffect",
        "urinary_aftereffect",
        "voice_body_change",
        "trauma_response",
        "private_fixation",
    ]
    if include_psychosexual:
        keys.append("psychosexual_state")
    return " ".join(str(lore.get(key) or "") for key in keys)


def _castration_traits_from_text(text: str, *, adult: bool) -> List[str]:
    traits = ["内廷奴籍"]
    if re.search(r"无麻|冷汗硬熬|蒙眼塞布|痛醒|番役|刑房薄刃|旧军刀|幻肢|PTSD|噩梦|梦回|磨刀|按肩|净房", text):
        traits.append("惊创未平")
    if re.search(r"麻沸散|温酒|香汤|老匠|熟匠|细净|受罚|规训|传旨声|服从", text):
        traits.append("服从依恋")
    if re.search(r"漏尿|尿闭|石淋|小解|夜尿|尿频", text):
        traits.append("尿路旧患")
    if re.search(r"嗓音|体态|肩背|步子|腰腹|久跪", text):
        traits.append("体声异变")
    if re.search(r"洁净|衣褶|香|压惊", text):
        traits.append("洁净癖")
    if re.search(r"宝匣|钥匙|封匣|供奉|还阳|全尸|佛龛", text):
        traits.append("宝匣执念")
    if adult and re.search(r"贤者模式|性无能|不能人道|畸恋|羞辱|束缚|受罚|禁欲|冷淡|情欲", text):
        traits.append("情欲异化")
    return list(dict.fromkeys(traits))


def _scheme_review_source_id(lore: Dict[str, object], scheme_profile: Dict[str, object]) -> str:
    parts = [
        str(scheme_profile.get("tier") or ""),
        str(scheme_profile.get("risk_score") or ""),
        str(scheme_profile.get("brutality") or ""),
        str(scheme_profile.get("trauma_risk") or ""),
        str(scheme_profile.get("surgery_risk") or ""),
        str(scheme_profile.get("bao_security") or ""),
    ]
    parts.extend(
        str(lore.get(key) or "")
        for key in (
            "castration_method",
            "knife_tool",
            "anesthesia",
            "procedure_note",
            "bao_size",
            "bao_shape",
            "bao_texture",
            "bao_weight",
            "bao_preservation",
            "bao_container",
            "bao_ritual",
        )
    )
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"scheme_review:{digest}"


def _scheme_review_delta(scheme_profile: Dict[str, object]) -> Dict[str, int]:
    if not bool(scheme_profile.get("explicit")):
        return {}
    risk = int(scheme_profile.get("risk_score") or 0)
    brutality = int(scheme_profile.get("brutality") or 0)
    trauma = int(scheme_profile.get("trauma_risk") or 0)
    surgery = int(scheme_profile.get("surgery_risk") or 0)
    bao_security = int(scheme_profile.get("bao_security") or 0)
    delta = {"emp_trust": 0, "grievance": 0, "ability": 0, "wisdom": 0, "charm": 0, "luck": 0}
    if risk >= 72:
        delta["emp_trust"] -= 1 + (1 if brutality >= 80 else 0)
        delta["grievance"] += 2 + (1 if trauma >= 78 else 0)
        if surgery >= 72:
            delta["ability"] -= 1
        if trauma >= 80:
            delta["luck"] -= 1
    elif risk >= 55:
        delta["grievance"] += 1
        if surgery >= 68:
            delta["charm"] -= 1
    elif risk <= 34:
        delta["emp_trust"] += 1
        delta["grievance"] -= 1
        if bao_security >= 70:
            delta["luck"] += 1
    return {key: value for key, value in delta.items() if value}


def _apply_scheme_review_gameplay(
    db: GameDB,
    state: GameState,
    name: str,
    lore: Dict[str, object],
    scheme_profile: Dict[str, object],
) -> Dict[str, object]:
    """Apply one-time gameplay consequences when a maintained lore scheme becomes concrete."""

    if not bool(scheme_profile.get("explicit")):
        return {}
    delta = _scheme_review_delta(scheme_profile)
    if not delta:
        return {}
    source_id = _scheme_review_source_id(lore, scheme_profile)
    existed = db.conn.execute(
        """
        SELECT 1 FROM event_memories
        WHERE subject_type='character' AND subject_id=? AND event_type='eunuch_scheme_review'
          AND source_kind='dialogue_lore_update' AND source_id=?
        """,
        (name, source_id),
    ).fetchone()
    if existed is not None:
        return {}
    row = db.conn.execute(
        "SELECT emp_trust, grievance, ability, wisdom, charm, luck FROM characters WHERE name=?",
        (name,),
    ).fetchone()
    if row is None:
        return {}
    before = {
        "emp_trust": int(row["emp_trust"] or 55),
        "grievance": int(row["grievance"] or 20),
        "ability": int(row["ability"] or 50),
        "wisdom": int(row["wisdom"] or 50),
        "charm": int(row["charm"] or 50),
        "luck": int(row["luck"] or 50),
    }
    after = dict(before)
    for key, value in delta.items():
        if key in after:
            after[key] = _clamp_0_100(after[key] + int(value or 0))
    db.conn.execute(
        """
        UPDATE characters
        SET emp_trust=?, grievance=?, ability=?, wisdom=?, charm=?, luck=?
        WHERE name=?
        """,
        (
            after["emp_trust"],
            after["grievance"],
            after["ability"],
            after["wisdom"],
            after["charm"],
            after["luck"],
            name,
        ),
    )
    tier = str(scheme_profile.get("tier") or "方案未判")
    risk = int(scheme_profile.get("risk_score") or 0)
    outcome_bits = [
        f"{label}{after[key] - before[key]:+d}"
        for key, label in (
            ("emp_trust", "信任"),
            ("grievance", "怨望"),
            ("ability", "才干"),
            ("wisdom", "机敏"),
            ("charm", "仪表"),
            ("luck", "运势"),
        )
        if after[key] != before[key]
    ]
    db.upsert_event_memory(
        state,
        "character",
        name,
        "eunuch_scheme_review",
        f"净身方案复盘：{name}",
        cause="奏对补录净身方案",
        process=(
            f"{tier} 风险{risk}；酷烈{scheme_profile.get('brutality')}、"
            f"惊创{scheme_profile.get('trauma_risk')}、伤身{scheme_profile.get('surgery_risk')}、"
            f"宝案{scheme_profile.get('bao_security')}"
        ),
        outcome="，".join(outcome_bits),
        sentiment="negative" if risk >= 55 else "positive",
        importance=4 if risk >= 72 else 3,
        tags=["净身", "方案复盘", tier],
        source_kind="dialogue_lore_update",
        source_id=source_id,
    )
    db.record_log(
        state,
        f"【净身方案复盘】{name}：{tier}（风险{risk}），{','.join(outcome_bits)}。",
    )
    return {
        "tier": tier,
        "risk_score": risk,
        "delta": {key: after[key] - before[key] for key in before if after[key] != before[key]},
        "effects": [str(item) for item in (scheme_profile.get("effects") or [])[:4] if str(item).strip()],
        "source_id": source_id,
    }


def sync_castration_lore_gameplay(
    db: GameDB,
    state: GameState,
    name: str,
    lore: Dict[str, object],
    *,
    source: str = "dialogue_lore_update",
    changed_keys: List[str] | None = None,
    review_hint: str = "",
) -> Dict[str, object]:
    """Idempotently sync later lore maintenance into traits, inventory, and memory."""
    clean_name = str(name or "").strip()
    if not clean_name or not isinstance(lore, dict):
        return {}
    row = db.conn.execute(
        "SELECT emp_trust, grievance, ability, wisdom, charm, luck, birth_year FROM characters WHERE name=?",
        (clean_name,),
    ).fetchone()
    if row is None:
        return {}
    adult = _is_adult_in_year(int(getattr(state, "year", 0) or 0), int(row["birth_year"] or 0))
    text = _castration_lore_text(lore, include_psychosexual=adult)
    wanted_traits = _castration_traits_from_text(text, adult=adult)
    existing_traits = {
        str(r["trait"])
        for r in db.conn.execute("SELECT trait FROM character_traits WHERE name=?", (clean_name,)).fetchall()
    }
    new_traits = [trait for trait in wanted_traits if trait not in existing_traits]
    stat_delta = {"ability": 0, "wisdom": 0, "charm": 0, "luck": 0, "emp_trust": 0, "grievance": 0}
    for trait in new_traits:
        if trait == "尿路旧患":
            stat_delta["ability"] -= 1
            stat_delta["charm"] -= 1
        elif trait == "体声异变":
            stat_delta["charm"] -= 1
        elif trait == "惊创未平":
            stat_delta["emp_trust"] -= 1
            stat_delta["grievance"] += 3
            stat_delta["wisdom"] -= 1
        elif trait == "宝匣执念":
            stat_delta["wisdom"] += 1
        elif trait == "情欲异化":
            stat_delta["charm"] -= 1
        elif trait == "服从依恋":
            stat_delta["emp_trust"] += 1
            stat_delta["grievance"] -= 1
    if new_traits:
        _add_character_traits(db, clean_name, new_traits)
        before = {key: int(row[key] or (55 if key == "emp_trust" else 20 if key == "grievance" else 50)) for key in stat_delta}
        after = {key: _clamp_0_100(before[key] + stat_delta[key]) for key in stat_delta}
        db.conn.execute(
            """
            UPDATE characters
            SET emp_trust=?, grievance=?, ability=?, wisdom=?, charm=?, luck=?
            WHERE name=?
            """,
            (after["emp_trust"], after["grievance"], after["ability"], after["wisdom"], after["charm"], after["luck"], clean_name),
        )

    bao_status = str(lore.get("bao_status") or "")
    item_ids = [f"净身旧档：{clean_name}"]
    if bao_status == "forfeit":
        item_ids.append(f"官没宝匣：{clean_name}")
    elif bao_status == "kept":
        item_ids.append(f"宝匣线索：{clean_name}")
    elif bao_status:
        item_ids.append(f"遗失宝案：{clean_name}")
    granted = [item_id for item_id in item_ids if _grant_player_item_once(db, item_id, state)]

    scheme_review: Dict[str, object] = {}
    scheme_detail_keys = {
        "castration_method",
        "knife_tool",
        "anesthesia",
        "procedure_note",
        "bao_size",
        "bao_shape",
        "bao_texture",
        "bao_weight",
        "bao_preservation",
        "bao_container",
    }
    changed_set = {str(key) for key in (changed_keys or []) if str(key).strip()}
    prospective = bool(re.search(r"若准|若仍准|待.{0,8}准|陛下.{0,8}准|准了再|请旨|可去|奴婢可|臣可", str(review_hint or "")))
    if changed_set.intersection(scheme_detail_keys) and not prospective:
        try:
            from ming_sim.eunuch_lore import castration_scheme_profile
            scheme_profile = castration_scheme_profile(lore)
            if isinstance(scheme_profile, dict):
                scheme_review = _apply_scheme_review_gameplay(db, state, clean_name, lore, scheme_profile)
        except Exception:
            scheme_review = {}

    if new_traits or granted or scheme_review:
        db.upsert_event_memory(
            state,
            "character",
            clean_name,
            "eunuch_lore_maintenance",
            f"净身后患入档：{clean_name}",
            cause="奏对维护净身档案",
            process="；".join(str(lore.get(key) or "") for key in (
                "urinary_aftereffect",
                "voice_body_change",
                "trauma_response",
                "private_fixation",
                "psychosexual_state" if adult else "",
            ) if key and str(lore.get(key) or "")),
            outcome="；".join(
                part for part in (
                    f"新增特质：{'、'.join(new_traits)}" if new_traits else "",
                    f"入库物件：{'、'.join(granted)}" if granted else "",
                    (
                        f"方案复盘：{scheme_review.get('tier')} 风险{scheme_review.get('risk_score')}"
                        if scheme_review else ""
                    ),
                ) if part
            ),
            sentiment="negative" if any(t in new_traits for t in ("惊创未平", "尿路旧患", "情欲异化")) else "mixed",
            importance=4 if new_traits else 3,
            tags=["净身", "后患", "宝匣", *new_traits[:3]],
            source_kind=source,
            source_id=f"{state.turn}:{clean_name}:{source}",
        )
        db.record_log(
            state,
            f"【净身档案维护】{clean_name}新增机制后果："
            + ("、".join(new_traits) if new_traits else "无新特质")
            + (f"；入库{'、'.join(granted)}" if granted else ""),
        )
    db.conn.commit()
    return {"traits_added": new_traits, "items_added": granted, "adult": adult, "scheme_review": scheme_review}


def _apply_castration_gameplay_consequences(
    db: GameDB,
    state: GameState,
    name: str,
    *,
    forced: bool,
    lore: Dict[str, object],
) -> Dict[str, object]:
    """Turn castration-detail choices into persistent gameplay consequences."""
    row = db.conn.execute(
        "SELECT emp_trust, grievance, ability, wisdom, charm, luck, birth_year FROM characters WHERE name=?",
        (name,),
    ).fetchone()
    if row is None:
        return {}

    adult = _is_adult_in_year(int(getattr(state, "year", 0) or 0), int(row["birth_year"] or 0))
    text = _castration_lore_text(lore, include_psychosexual=adult)
    trust_delta = -8 if forced else 5
    grievance_delta = 18 if forced else -4
    ability_delta = 0
    wisdom_delta = 0
    charm_delta = -1 if forced else 0
    luck_delta = -1 if forced else 0
    traits = _castration_traits_from_text(text, adult=adult)
    if re.search(r"无麻|冷汗硬熬|蒙眼塞布|痛醒|番役|刑房薄刃|旧军刀", text):
        trust_delta -= 5
        grievance_delta += 12
        ability_delta -= 1
    if re.search(r"麻沸散|温酒|香汤|老匠|熟匠|细净", text):
        trust_delta += 2
        grievance_delta -= 4
    if re.search(r"漏尿|尿闭|石淋|小解|夜尿|尿频", text):
        ability_delta -= 1
        charm_delta -= 1
    if re.search(r"嗓音|体态|肩背|步子|腰腹|久跪", text):
        charm_delta -= 1
    if re.search(r"幻肢|PTSD|噩梦|梦回|磨刀|按肩|净房", text):
        trust_delta -= 2
        grievance_delta += 5
        wisdom_delta -= 1
    if re.search(r"宝匣|钥匙|封匣|供奉|还阳|全尸|佛龛", text):
        wisdom_delta += 1
    if adult and re.search(r"贤者模式|性无能|不能人道|畸恋|羞辱|束缚|受罚|禁欲|冷淡|情欲", text):
        charm_delta -= 1
    scheme_profile: Dict[str, object] = {}
    try:
        from ming_sim.eunuch_lore import castration_scheme_profile
        scheme_profile = castration_scheme_profile(lore)
    except Exception:
        scheme_profile = {}
    scheme_delta = scheme_profile.get("stat_delta") if isinstance(scheme_profile, dict) else {}
    if isinstance(scheme_delta, dict):
        trust_delta += int(scheme_delta.get("emp_trust") or 0)
        grievance_delta += int(scheme_delta.get("grievance") or 0)
        ability_delta += int(scheme_delta.get("ability") or 0)
        wisdom_delta += int(scheme_delta.get("wisdom") or 0)
        charm_delta += int(scheme_delta.get("charm") or 0)
        luck_delta += int(scheme_delta.get("luck") or 0)

    before = {
        "emp_trust": int(row["emp_trust"] or 55),
        "grievance": int(row["grievance"] or 20),
        "ability": int(row["ability"] or 50),
        "wisdom": int(row["wisdom"] or 50),
        "charm": int(row["charm"] or 50),
        "luck": int(row["luck"] or 50),
    }
    after = {
        "emp_trust": _clamp_0_100(before["emp_trust"] + trust_delta),
        "grievance": _clamp_0_100(before["grievance"] + grievance_delta),
        "ability": _clamp_0_100(before["ability"] + ability_delta),
        "wisdom": _clamp_0_100(before["wisdom"] + wisdom_delta),
        "charm": _clamp_0_100(before["charm"] + charm_delta),
        "luck": _clamp_0_100(before["luck"] + luck_delta),
    }
    db.conn.execute(
        """
        UPDATE characters
        SET emp_trust=?, grievance=?, ability=?, wisdom=?, charm=?, luck=?
        WHERE name=?
        """,
        (
            after["emp_trust"],
            after["grievance"],
            after["ability"],
            after["wisdom"],
            after["charm"],
            after["luck"],
            name,
        ),
    )
    _add_character_traits(db, name, traits)

    bao_status = str(lore.get("bao_status") or "")
    if bao_status == "forfeit":
        item_id = f"官没宝匣：{name}"
    elif bao_status == "kept":
        item_id = f"宝匣线索：{name}"
    else:
        item_id = f"遗失宝案：{name}"
    db.grant_player_item(f"净身旧档：{name}", state)
    db.grant_player_item(item_id, state)

    tags = ["净身", "宝匣", "内廷奴籍", *traits[:4]]
    try:
        db.upsert_event_memory(
            state,
            "character",
            name,
            "eunuch_castration",
            f"净身旧档：{name}",
            cause="强旨净身" if forced else "自愿净身入宫",
            process="；".join(part for part in (
                str(lore.get("castration_method") or ""),
                str(lore.get("knife_tool") or ""),
                str(lore.get("anesthesia") or ""),
                str(lore.get("bao_preservation") or ""),
                str(lore.get("bao_container") or ""),
            ) if part),
            outcome="；".join(part for part in (
                f"信任 {before['emp_trust']}->{after['emp_trust']}，怨望 {before['grievance']}->{after['grievance']}",
                f"方案画像：{scheme_profile.get('tier')} 风险{scheme_profile.get('risk_score')}" if scheme_profile else "",
                f"入库物件：{item_id}",
            ) if part),
            sentiment="negative" if forced else "mixed",
            importance=5,
            tags=tags,
            source_kind="castration",
            source_id=f"{state.turn}:{name}",
        )
    except Exception:
        pass
    db.record_log(
        state,
        (
            f"【净身旧档】{name}：{str(lore.get('castration_method') or '净身')}、"
            f"{str(lore.get('bao_preservation') or '宝况未详')}、{str(lore.get('bao_container') or '宝匣未详')}。"
            + (f"方案：{scheme_profile.get('tier')}（风险{scheme_profile.get('risk_score')}）。" if scheme_profile else "")
            + f"信任 {before['emp_trust']}->{after['emp_trust']}，怨望 {before['grievance']}->{after['grievance']}。"
        ),
    )
    db.conn.commit()
    return {
        "trust_delta": after["emp_trust"] - before["emp_trust"],
        "grievance_delta": after["grievance"] - before["grievance"],
        "stat_delta": {
            key: after[key] - before[key]
            for key in ("ability", "wisdom", "charm", "luck")
            if after[key] != before[key]
        },
        "traits": list(dict.fromkeys(traits)),
        "items": [f"净身旧档：{name}", item_id],
        "scheme_profile": scheme_profile,
    }


def convert_character_to_eunuch(
    db: GameDB,
    state: GameState,
    content: GameContent,
    name: str,
    *,
    force: bool,
    source: str,
    new_office: str = "司礼监随堂太监",
    lore_text: str = "",
) -> Tuple[Character, List[Reaction]]:
    """Convert an existing Ming character into the inner-court eunuch chain."""
    clean_name = (name or "").strip()
    if clean_name not in content.characters:
        raise ValueError(f"未找到可入内廷人物：{clean_name}")
    character = content.characters[clean_name]
    old_row = character_political_row(db, clean_name)
    office = normalize_office(new_office) or "司礼监随堂太监"
    db.set_character_office(clean_name, office, "司礼监", source=source or "净身入宫")

    status_reason = (
        "奉强旨净身入宫，转入皇帝私人执行链；外朝将视为重罚与奇辱"
        if force
        else "奏对同意后净身入宫，转入皇帝私人执行链"
    )
    row = db.conn.execute(
        "SELECT personal_skills, loyalty, courage, style FROM characters WHERE name=?",
        (clean_name,),
    ).fetchone()
    skills = _json_list(row["personal_skills"] if row else character.personal_skills)
    for skill in ("保密复命", "内廷传旨"):
        if skill not in skills:
            skills.append(skill)
    base_loyalty = int(row["loyalty"] if row else character.loyalty)
    loyalty = (
        max(18, min(72, base_loyalty - 10))
        if force
        else min(100, max(base_loyalty, 82))
    )
    courage = min(100, int(row["courage"] if row else character.courage) + (3 if force else 6))
    style = str(row["style"] if row else character.style)
    suffix = (
        "奉强旨入内廷，行事更重明旨与复命，但心结未解"
        if force
        else "既入内廷，凡事更重明旨、密奏与复命"
    )
    if suffix not in style:
        style = f"{style}；{suffix}" if style else suffix

    db.conn.execute(
        """UPDATE characters
           SET faction=?, personal_skills=?, loyalty=?, courage=?, style=?, status_reason=?
           WHERE name=?""",
        (
            "内廷",
            json.dumps(skills, ensure_ascii=False),
            int(loyalty),
            int(courage),
            style,
            status_reason,
            clean_name,
        ),
    )
    db.conn.commit()

    character.office = office
    character.office_type = "司礼监"
    character.faction = "内廷"
    character.personal_skills = skills
    character.loyalty = int(loyalty)
    character.courage = int(courage)
    character.style = style
    reactions = apply_castration_reaction(
        db,
        state,
        clean_name,
        old_row.get("office", ""),
        old_row.get("office_type", ""),
        old_row.get("faction", ""),
        force=force,
    )
    # 净身「宝」之处置与奴性登记（E2a）：强阉＝宝官没·奴性扭曲；自愿＝宝自藏·奴性恭谨。
    try:
        from ming_sim.eunuch_lore import record_castration
        from ming_sim.upgrade_schema import KV_CURRENT_DAY, kv_int
        lore = record_castration(
            db,
            clean_name,
            forced=force,
            day=kv_int(db, KV_CURRENT_DAY, 0),
            detail_text=" ".join(part for part in (source, lore_text) if str(part or "").strip()),
        )
        _apply_castration_gameplay_consequences(
            db,
            state,
            clean_name,
            forced=force,
            lore=lore,
        )
    except Exception:
        pass
    return character, reactions


def convert_eunuch_to_commoner(
    db: GameDB,
    state: GameState,
    content: GameContent,
    name: str,
    *,
    force: bool,
    source: str,
    new_office: str = "民籍百姓（内廷转出）",
) -> Tuple[Character, List[Reaction]]:
    """Release an inner-court eunuch from slave registry into Ming commoner status."""
    clean_name = (name or "").strip()
    if clean_name not in content.characters:
        raise ValueError(f"未找到可转民籍人物：{clean_name}")
    character = content.characters[clean_name]
    old_row = character_political_row(db, clean_name)
    office = normalize_office(new_office) or "民籍百姓（内廷转出）"
    db.set_character_office(clean_name, office, "民籍", source=source or "奴籍转民籍")

    status_reason = (
        "奉强旨脱离内廷奴籍，转为民籍百姓；内廷旧人会视为越例开恩"
        if force
        else "奏对同意后脱离内廷奴籍，转为民籍百姓"
    )
    row = db.conn.execute(
        "SELECT personal_skills, loyalty, courage, style FROM characters WHERE name=?",
        (clean_name,),
    ).fetchone()
    skills = _json_list(row["personal_skills"] if row else character.personal_skills)
    skills = [skill for skill in skills if skill not in {"内廷传旨", "宫禁熟习"}]
    for skill in ("布衣自立", "民间营生"):
        if skill not in skills:
            skills.append(skill)
    loyalty = int(row["loyalty"] if row else character.loyalty)
    courage = min(100, int(row["courage"] if row else character.courage) + (2 if force else 4))
    style = str(row["style"] if row else character.style)
    suffix = (
        "奉强旨脱籍为民，离宫后谨慎避祸，仍记得宫禁旧闻"
        if force
        else "脱籍还民，行事更重自保与民间生计，仍可为皇帝陈述宫禁旧闻"
    )
    if suffix not in style:
        style = f"{style}；{suffix}" if style else suffix

    db.conn.execute(
        """UPDATE characters
           SET faction=?, personal_skills=?, loyalty=?, courage=?, style=?, status_reason=?
           WHERE name=?""",
        (
            "民籍",
            json.dumps(skills, ensure_ascii=False),
            int(loyalty),
            int(courage),
            style,
            status_reason,
            clean_name,
        ),
    )
    db.conn.commit()

    character.office = office
    character.office_type = "民籍"
    character.faction = "民籍"
    character.personal_skills = skills
    character.loyalty = int(loyalty)
    character.courage = int(courage)
    character.style = style
    return character, []
