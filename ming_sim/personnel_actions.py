"""Shared personnel mutations that are larger than a simple office write."""

from __future__ import annotations

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
    return bool(re.search(r"司礼监|东厂|太监|宦官|内廷", text))


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
        "SELECT emp_trust, grievance, ability, wisdom, charm, luck FROM characters WHERE name=?",
        (name,),
    ).fetchone()
    if row is None:
        return {}

    text = " ".join(str(lore.get(key) or "") for key in (
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
        "psychosexual_state",
    ))
    trust_delta = -8 if forced else 5
    grievance_delta = 18 if forced else -4
    ability_delta = 0
    wisdom_delta = 0
    charm_delta = -1 if forced else 0
    luck_delta = -1 if forced else 0
    traits = ["内廷奴籍"]

    if re.search(r"无麻|冷汗硬熬|蒙眼塞布|痛醒|番役|刑房薄刃|旧军刀", text):
        trust_delta -= 5
        grievance_delta += 12
        ability_delta -= 1
        traits.append("惊创未平")
    if re.search(r"麻沸散|温酒|香汤|老匠|熟匠|细净", text):
        trust_delta += 2
        grievance_delta -= 4
        traits.append("服从依恋")
    if re.search(r"漏尿|尿闭|石淋|小解|夜尿|尿频", text):
        ability_delta -= 1
        charm_delta -= 1
        traits.append("尿路旧患")
    if re.search(r"嗓音|体态|肩背|步子|腰腹|久跪", text):
        charm_delta -= 1
        traits.append("体声异变")
    if re.search(r"幻肢|PTSD|噩梦|梦回|磨刀|按肩|净房", text):
        trust_delta -= 2
        grievance_delta += 5
        wisdom_delta -= 1
        traits.append("惊创未平")
    if re.search(r"洁净|衣褶|香|压惊", text):
        traits.append("洁净癖")
    if re.search(r"宝匣|钥匙|封匣|供奉|还阳|全尸|佛龛", text):
        wisdom_delta += 1
        traits.append("宝匣执念")
    if re.search(r"贤者模式|性无能|不能人道|畸恋|羞辱|束缚|受罚|禁欲|冷淡|情欲", text):
        charm_delta -= 1
        traits.append("情欲异化")
    if re.search(r"受罚|规训|传旨声|服从", text):
        traits.append("服从依恋")

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
            outcome=(
                f"信任 {before['emp_trust']}->{after['emp_trust']}，"
                f"怨望 {before['grievance']}->{after['grievance']}；"
                f"入库物件：{item_id}"
            ),
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
            f"信任 {before['emp_trust']}->{after['emp_trust']}，怨望 {before['grievance']}->{after['grievance']}。"
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
