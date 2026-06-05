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


def convert_character_to_eunuch(
    db: GameDB,
    state: GameState,
    content: GameContent,
    name: str,
    *,
    force: bool,
    source: str,
    new_office: str = "司礼监随堂太监",
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
