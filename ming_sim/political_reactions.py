"""Deterministic court reactions for major personnel moves.

These helpers add a small, auditable political echo to hard actions that are
already committed elsewhere: appointments, removals, displacement, and
castration into the inner court. They intentionally stay bounded so the monthly
LLM simulation still owns broad narrative consequence.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from ming_sim.db import GameDB, normalize_office
from ming_sim.models import GameState


Reaction = Dict[str, object]

STATUS_LABELS = {
    "offstage": "退居幕外",
    "dismissed": "罢黜",
    "imprisoned": "下狱",
    "exiled": "流放",
    "retired": "致仕",
    "dead": "身故",
}

OUSTED_STATUS = {"dismissed", "imprisoned", "exiled", "retired", "dead"}
HARSH_STATUS = {"imprisoned", "exiled", "dead"}


def character_political_row(db: GameDB, name: str) -> Dict[str, str]:
    row = db.conn.execute(
        "SELECT name, office, office_type, faction, status FROM characters WHERE name=?",
        (name,),
    ).fetchone()
    if row is None:
        return {"name": name, "office": "", "office_type": "", "faction": "", "status": ""}
    return {key: str(row[key] or "") for key in row.keys()}


def valid_factions(db: GameDB) -> set[str]:
    return {str(row["name"]) for row in db.conn.execute("SELECT name FROM factions").fetchall()}


def canonical_faction(db: GameDB, faction: str, office_type: str = "", office: str = "") -> str:
    """Map narrative labels to factions that actually exist in the save."""
    valid = valid_factions(db)
    raw = (faction or "").strip()
    if raw in valid:
        return raw
    text = f"{raw} {office_type or ''} {office or ''}"
    aliases = {
        "内廷": "皇党",
        "司礼监": "皇党",
        "太监": "皇党",
        "宦官": "皇党",
        "皇权": "皇党",
        "清流": "东林",
        "东林党": "东林",
        "士林": "东林",
        "实务派": "中立",
    }
    for needle, target in aliases.items():
        if needle in text and target in valid:
            return target
    return "中立" if "中立" in valid else (next(iter(valid)) if valid else "")


def rival_faction(db: GameDB, faction: str) -> str:
    valid = valid_factions(db)
    rivals = {"东林": "阉党", "阉党": "东林", "皇党": "东林"}
    rival = rivals.get(faction, "")
    return rival if rival in valid else ""


def office_weight(office: str, office_type: str = "") -> int:
    """Return a bounded political weight for the current office text."""
    text = normalize_office(office)
    kind = (office_type or "").strip()
    if not text:
        return 0

    weights: List[int] = []
    for part in [p.strip() for p in text.split(",") if p.strip()]:
        weight = 0
        if re.search(r"首辅", part):
            weight = 9
        elif re.search(r"次辅", part):
            weight = 6
        elif re.search(r"大学士|入阁", part):
            weight = 4
        elif re.search(r"(吏部|户部|兵部)尚书", part):
            weight = 7
        elif re.search(r"尚书", part):
            weight = 5
        elif re.search(r"侍郎", part):
            weight = 3
        elif re.search(r"司礼监.*掌印|掌印太监", part):
            weight = 9
        elif re.search(r"司礼监.*秉笔|秉笔太监", part):
            weight = 6
        elif re.search(r"司礼监|随堂太监", part):
            weight = 3
        elif re.search(r"提督东厂|东厂提督", part):
            weight = 8
        elif re.search(r"东厂", part):
            weight = 5
        elif re.search(r"锦衣卫.*指挥使|都指挥使", part):
            weight = 7
        elif re.search(r"北镇抚司|镇抚", part):
            weight = 5
        elif re.search(r"千户", part):
            weight = 2
        elif re.search(r"督师|经略", part):
            weight = 8
        elif re.search(r"总督", part):
            weight = 6
        elif re.search(r"巡抚", part):
            weight = 5
        elif re.search(r"副总兵", part):
            weight = 3
        elif re.search(r"总兵", part):
            weight = 5
        elif re.search(r"游击|参将", part):
            weight = 2
        elif kind and kind not in {"待铨", "后宫"}:
            weight = 2
        elif part:
            weight = 1
        weights.append(weight)
    return max(0, min(10, sum(weights)))


def _bounded_delta(value: int, cap: int = 8) -> int:
    return max(-cap, min(cap, int(value)))


def _merge_delta(target: Dict[str, Dict[str, int]], faction: str, satisfaction: int = 0, leverage: int = 0) -> None:
    if not faction or (satisfaction == 0 and leverage == 0):
        return
    bucket = target.setdefault(faction, {"satisfaction": 0, "leverage": 0})
    bucket["satisfaction"] = _bounded_delta(bucket["satisfaction"] + int(satisfaction), 6)
    bucket["leverage"] = _bounded_delta(bucket["leverage"] + int(leverage), 8)


def _clean_delta(delta: Dict[str, Dict[str, int]]) -> Dict[str, Dict[str, int]]:
    return {
        faction: {k: v for k, v in values.items() if int(v) != 0}
        for faction, values in delta.items()
        if any(int(v) != 0 for v in values.values())
    }


def _apply(db: GameDB, note: Reaction) -> List[Reaction]:
    delta = note.get("faction_delta")
    if isinstance(delta, dict) and delta:
        db.adjust_factions(delta)
        return [note]
    return []


def _signed(value: int) -> str:
    return f"+{value}" if value > 0 else str(value)


def _delta_summary(delta: Dict[str, Dict[str, int]]) -> str:
    parts: List[str] = []
    for faction, values in delta.items():
        inner = []
        sat = int(values.get("satisfaction") or 0)
        lev = int(values.get("leverage") or 0)
        if sat:
            inner.append(f"满意{_signed(sat)}")
        if lev:
            inner.append(f"权势{_signed(lev)}")
        if inner:
            parts.append(f"{faction}{'、'.join(inner)}")
    return "；".join(parts)


def apply_office_change_reaction(
    db: GameDB,
    state: GameState,
    name: str,
    old_office: str,
    old_office_type: str,
    old_faction: str,
    new_office: str,
    new_office_type: str,
    new_faction: str,
    source: str = "",
) -> List[Reaction]:
    old_weight = office_weight(old_office, old_office_type)
    new_weight = office_weight(new_office, new_office_type)
    old_side = canonical_faction(db, old_faction, old_office_type, old_office)
    new_side = canonical_faction(db, new_faction or old_faction, new_office_type, new_office)
    delta: Dict[str, Dict[str, int]] = {}

    if old_side == new_side:
        diff = _bounded_delta(new_weight - old_weight)
        if diff:
            _merge_delta(delta, new_side, satisfaction=1 if diff > 0 else -1, leverage=diff)
    else:
        if old_weight:
            _merge_delta(delta, old_side, satisfaction=-1, leverage=-min(8, old_weight))
        if new_weight:
            _merge_delta(delta, new_side, satisfaction=1, leverage=min(8, new_weight))

    cleaned = _clean_delta(delta)
    if not cleaned:
        return []
    title = f"任免牵动：{name}"
    verb = "升授" if new_weight > old_weight else "降调" if new_weight < old_weight else "改隶"
    summary = f"{name}{verb}为{new_office or '无实职'}，{_delta_summary(cleaned)}。"
    return _apply(db, {
        "kind": "political_reaction",
        "tone": "warn" if new_weight < old_weight else "neutral",
        "title": title,
        "summary": summary,
        "subject": name,
        "action": "office_change",
        "old_office": old_office,
        "new_office": new_office,
        "drivers": [
            f"原缺权重{old_weight}",
            f"新缺权重{new_weight}",
            f"原派系{old_side or '无'}",
            f"新派系{new_side or '无'}",
            *( [f"事由：{source[:80]}"] if source else [] ),
        ],
        "faction_delta": cleaned,
        "turn": state.turn,
    })


def apply_office_loss_reaction(
    db: GameDB,
    state: GameState,
    name: str,
    lost_office: str,
    old_office_type: str,
    old_faction: str,
    source: str = "",
) -> List[Reaction]:
    weight = office_weight(lost_office, old_office_type)
    if weight <= 0:
        return []
    side = canonical_faction(db, old_faction, old_office_type, lost_office)
    delta: Dict[str, Dict[str, int]] = {}
    _merge_delta(delta, side, satisfaction=-1, leverage=-min(8, weight))
    cleaned = _clean_delta(delta)
    if not cleaned:
        return []
    return _apply(db, {
        "kind": "political_reaction",
        "tone": "warn",
        "title": f"腾缺余波：{name}",
        "summary": f"{name}失去{lost_office}，{_delta_summary(cleaned)}。",
        "subject": name,
        "action": "office_loss",
        "old_office": lost_office,
        "drivers": [
            f"被腾缺权重{weight}",
            f"受损派系{side or '无'}",
            *( [f"事由：{source[:80]}"] if source else [] ),
        ],
        "faction_delta": cleaned,
        "turn": state.turn,
    })


def apply_status_change_reaction(
    db: GameDB,
    state: GameState,
    name: str,
    old_office: str,
    old_office_type: str,
    old_faction: str,
    status: str,
    reason: str = "",
) -> List[Reaction]:
    status = (status or "").strip().lower()
    if status == "active":
        return []
    weight = office_weight(old_office, old_office_type)
    if status == "offstage":
        loss = max(1, min(2, weight or 1))
        sat_loss = -1
    elif status in OUSTED_STATUS:
        loss = max(1, min(8, weight or 2))
        sat_loss = -1 if status in {"dismissed", "retired"} else -3
    else:
        return []

    side = canonical_faction(db, old_faction, old_office_type, old_office)
    delta: Dict[str, Dict[str, int]] = {}
    _merge_delta(delta, side, satisfaction=sat_loss, leverage=-loss)
    rival = rival_faction(db, side)
    if rival and status in HARSH_STATUS and loss >= 4:
        _merge_delta(delta, rival, satisfaction=1, leverage=1)
    cleaned = _clean_delta(delta)
    if not cleaned:
        return []
    label = STATUS_LABELS.get(status, status)
    return _apply(db, {
        "kind": "political_reaction",
        "tone": "bad" if status in HARSH_STATUS else "warn",
        "title": f"去职余波：{name}",
        "summary": f"{name}{label}，{_delta_summary(cleaned)}。",
        "subject": name,
        "action": "status_change",
        "status": status,
        "old_office": old_office,
        "drivers": [
            f"原缺权重{weight}",
            f"受损派系{side or '无'}",
            *( [f"事由：{reason[:80]}"] if reason else [] ),
        ],
        "faction_delta": cleaned,
        "turn": state.turn,
    })


def apply_castration_reaction(
    db: GameDB,
    state: GameState,
    name: str,
    old_office: str,
    old_office_type: str,
    old_faction: str,
    force: bool = False,
) -> List[Reaction]:
    old_weight = office_weight(old_office, old_office_type)
    old_side = canonical_faction(db, old_faction, old_office_type, old_office)
    imperial_side = canonical_faction(db, "内廷", "司礼监", "司礼监随堂太监")
    delta: Dict[str, Dict[str, int]] = {}

    if old_side != imperial_side and old_weight:
        _merge_delta(delta, old_side, satisfaction=-3 if force else -1, leverage=-min(6, max(1, old_weight // 2)))
    _merge_delta(delta, imperial_side, satisfaction=0 if force else 1, leverage=3 if force else 2)
    if force:
        clear_side = canonical_faction(db, "清流")
        if clear_side and clear_side != imperial_side:
            _merge_delta(delta, clear_side, satisfaction=-2, leverage=-1)
    cleaned = _clean_delta(delta)
    if not cleaned:
        return []
    mode = "强旨净身" if force else "自愿净身"
    return _apply(db, {
        "kind": "political_reaction",
        "tone": "bad" if force else "warn",
        "title": f"内廷转身：{name}",
        "summary": f"{name}{mode}入司礼监，{_delta_summary(cleaned)}。",
        "subject": name,
        "action": "castration",
        "old_office": old_office,
        "new_office": "司礼监随堂太监",
        "drivers": [
            f"原缺权重{old_weight}",
            f"原派系{old_side or '无'}",
            f"内廷归入{imperial_side or '皇权'}",
            "强旨将被外朝视为奇辱与威压" if force else "奏对同意后转入皇帝私人执行链",
        ],
        "faction_delta": cleaned,
        "turn": state.turn,
    })
