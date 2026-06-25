"""Shared character identity helpers.

This module is deliberately small so DB, personnel, and web layers can agree on
eunuch-only roles without creating import cycles.
"""

from __future__ import annotations

import re
from typing import Mapping

VALID_SEXES = {"male", "female", "eunuch", "unknown"}
SEX_LABELS = {
    "male": "男",
    "female": "女",
    "eunuch": "阉人",
    "unknown": "不详",
}

_COMMONER_MARKERS = ("民籍", "布衣", "百姓", "脱籍", "还民", "出宫为民", "归为百姓", "转出")
_EUNUCH_ROLE_RE = re.compile(
    r"司礼监|东厂|太监|宦官|内廷|内官监|内官|御马监|御用监|尚膳监|小火者|内侍|监军"
)


def normalize_sex(value: object) -> str:
    raw = str(value or "").strip().lower()
    aliases = {
        "m": "male",
        "man": "male",
        "男": "male",
        "男人": "male",
        "男性": "male",
        "f": "female",
        "woman": "female",
        "女": "female",
        "女人": "female",
        "女性": "female",
        "eunuch": "eunuch",
        "阉人": "eunuch",
        "宦官": "eunuch",
        "太监": "eunuch",
        "内侍": "eunuch",
        "unknown": "unknown",
        "": "unknown",
        "不详": "unknown",
        "未知": "unknown",
    }
    raw = aliases.get(raw, raw)
    return raw if raw in VALID_SEXES else "unknown"


def sex_label(value: object) -> str:
    return SEX_LABELS.get(normalize_sex(value), SEX_LABELS["unknown"])


def requires_eunuch_identity(office: object = "", office_type: object = "", faction: object = "") -> bool:
    text = f"{office or ''} {office_type or ''} {faction or ''}"
    if any(marker in text for marker in _COMMONER_MARKERS):
        return False
    return bool(_EUNUCH_ROLE_RE.search(text))


def infer_character_sex(
    office: object = "",
    office_type: object = "",
    faction: object = "",
    current: object = "",
) -> str:
    current_norm = normalize_sex(current)
    if requires_eunuch_identity(office, office_type, faction):
        return "eunuch"
    if str(office_type or "").strip() == "后宫" or "后宫" in str(faction or ""):
        return "female"
    if current_norm != "unknown":
        return current_norm
    return "male"


def row_sex(row: Mapping[str, object] | None) -> str:
    if row is None:
        return "unknown"
    try:
        return normalize_sex(row.get("sex"))  # type: ignore[attr-defined]
    except AttributeError:
        try:
            return normalize_sex(row["sex"])  # type: ignore[index]
        except Exception:
            return "unknown"


def character_is_eunuch(
    row: Mapping[str, object] | None = None,
    *,
    sex: object = "",
    office: object = "",
    office_type: object = "",
    faction: object = "",
    allow_legacy_text_fallback: bool = True,
) -> bool:
    explicit = normalize_sex(sex) if sex not in (None, "") else row_sex(row)
    if explicit == "eunuch":
        return True
    if explicit in {"male", "female"}:
        return False
    return bool(allow_legacy_text_fallback and requires_eunuch_identity(office, office_type, faction))
