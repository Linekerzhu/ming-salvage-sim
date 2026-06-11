"""HTTP payload contracts shared by the web shell and API tests.

This module owns transport-level shapes only. Gameplay rules and runtime state
stay in ``GameSession``/``GameDB``; route handlers can import these helpers
without inheriting frontend-specific serialization details.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence


CHARACTER_CARD_FIELDS = (
    "name",
    "office",
    "office_type",
    "faction",
    "status",
    "portrait_available",
    "portrait_status",
    "birth_year",
    "status_reason",
    "portrait_id",
    "favorite",
)

CHARACTER_CARD_DEFAULTS = {
    "status_reason": "",
    "portrait_id": "",
    "favorite": False,
}

CHARACTER_INDEX_FIELDS = (
    "name",
    "office",
    "office_type",
    "faction",
    "status",
    "portrait_available",
    "status_reason",
    "can_summon",
    "power_id",
)

CHARACTER_INDEX_DEFAULTS = {
    "status_reason": "",
    "can_summon": True,
    "power_id": "ming",
}

REGION_FIELDS = (
    "id",
    "name",
    "kind",
    "population",
    "public_support",
    "unrest",
    "natural_disaster",
    "human_disaster",
    "registered_land",
    "hidden_land",
    "tax_per_turn",
    "grain_security",
    "gentry_resistance",
    "military_pressure",
    "status",
    "controlled_by",
)

ARMY_FIELDS = (
    "id",
    "name",
    "station",
    "theater",
    "commander",
    "controller",
    "troop_type",
    "manpower",
    "maintenance_per_turn",
    "supply",
    "morale",
    "training",
    "equipment",
    "arrears",
    "mobility",
    "loyalty",
    "status",
    "owner_power",
)

POWER_FIELDS = (
    "id",
    "name",
    "kind",
    "leader",
    "stance",
    "leverage",
    "satisfaction",
    "military_strength",
    "cohesion",
    "supply",
    "agenda",
    "status",
    "last_action",
    "aliases",
)

BUILDING_FIELDS = (
    "id",
    "region_id",
    "name",
    "category",
    "level",
    "condition",
    "maintenance",
    "risk",
    "status",
    "output_metric",
    "output_amount",
    "origin",
)

BUILDING_DEFAULTS = {
    "output_metric": "",
    "output_amount": 0,
    "origin": "preset",
}

MAP_NODE_FIELDS = (
    "id",
    "kind",
    "x",
    "y",
    "risk",
    "region",
    "armies",
    "buildings",
    "label",
)

MAP_NODE_DEFAULTS = {
    "label": "",
}

ORG_PERSON_FIELDS = (
    "name",
    "office",
    "office_type",
    "faction",
    "status",
    "status_reason",
    "power_id",
)

ORG_PERSON_DEFAULTS = {
    "status_reason": "",
    "power_id": "ming",
}

ORG_SLOT_FIELDS = (
    "title",
    "office_type",
    "count",
    "holders",
    "filled_count",
    "vacancies",
    "overflow_count",
    "open_pool",
    "match_hint",
)

ORG_SLOT_DEFAULTS = {
    "overflow_count": 0,
    "open_pool": False,
    "match_hint": "",
}

ORG_INSTITUTION_FIELDS = (
    "id",
    "name",
    "category",
    "mandate",
    "custom",
    "readiness",
    "coverage",
    "holder_quality",
    "execution_summary",
    "execution_risks",
    "slots",
    "vacancy_count",
    "holder_count",
)

ORG_INSTITUTION_DEFAULTS = {
    "custom": False,
    "execution_summary": "",
    "execution_risks": [],
}

MONTHLY_FOLLOWUP_FIELDS = (
    "minister_name",
    "priority",
    "reason_types",
    "memory_hooks",
    "risk_tags",
    "personality_cue",
    "truth_mode",
    "title",
    "summary",
    "suggested_opening",
    "preferred_stance",
)

MONTHLY_FOLLOWUP_SHARED_DEFAULT_FIELDS = (
    "title",
    "summary",
    "suggested_opening",
    "preferred_stance",
)

ISSUE_FIELDS = (
    "id",
    "title",
    "bar_value",
    "phase",
    "stage_text",
    "severity",
    "tags",
    "bar_good_meaning",
    "bar_bad_meaning",
    "resolve_condition",
    "fail_condition",
    "ongoing_text",
    "effect_on_resolve",
    "effect_on_fail",
    "kind",
    "inertia",
)

ISSUE_DEFAULTS = {
    "kind": "situation",
    "inertia": 0,
}

LEGACY_FIELDS = (
    "id",
    "name",
    "narrative_hint",
    "modifiers",
    "effect_text",
    "clear_condition",
    "remaining_months",
)

LEGACY_DEFAULTS = {
    "remaining_months": -1,
}


def compact_rows(
    rows: Sequence[Mapping[str, Any]],
    fields: Sequence[str],
    defaults: Mapping[str, Any] | None = None,
) -> List[List[Any]]:
    """Encode object rows as field-aligned arrays for hot HTTP payloads."""
    encoded: List[List[Any]] = []
    for row in rows:
        values = [row.get(field) for field in fields]
        if defaults:
            while values:
                field = fields[len(values) - 1]
                if field not in defaults or values[-1] != defaults[field]:
                    break
                values.pop()
        encoded.append(values)
    return encoded


def compact_character_cards(cards: Sequence[Mapping[str, Any]]) -> List[List[Any]]:
    """Compact repeated roster keys out of hot state payloads."""
    return compact_rows(cards, CHARACTER_CARD_FIELDS, defaults=CHARACTER_CARD_DEFAULTS)


def compact_character_index(rows: Sequence[Mapping[str, Any]]) -> List[List[Any]]:
    """Compact full character archive rows while leaving detail payloads untouched."""
    return compact_rows(rows, CHARACTER_INDEX_FIELDS, defaults=CHARACTER_INDEX_DEFAULTS)


def compact_regions(regions: Sequence[Mapping[str, Any]]) -> List[List[Any]]:
    return compact_rows(regions, REGION_FIELDS)


def compact_armies(armies: Sequence[Mapping[str, Any]]) -> List[List[Any]]:
    return compact_rows(armies, ARMY_FIELDS)


def compact_powers(powers: Sequence[Mapping[str, Any]]) -> List[List[Any]]:
    return compact_rows(powers, POWER_FIELDS)


def compact_buildings(buildings: Sequence[Mapping[str, Any]]) -> List[List[Any]]:
    return compact_rows(buildings, BUILDING_FIELDS, defaults=BUILDING_DEFAULTS)


def compact_map_nodes(nodes: Sequence[Mapping[str, Any]]) -> List[List[Any]]:
    """Compact detailed map nodes, including nested region/army/building rows."""
    compacted: List[Dict[str, Any]] = []
    for node in nodes:
        region = node.get("region")
        compacted.append({
            "id": node.get("id"),
            "kind": node.get("kind"),
            "x": node.get("x"),
            "y": node.get("y"),
            "risk": node.get("risk"),
            "region": compact_rows([region], REGION_FIELDS)[0] if isinstance(region, Mapping) else None,
            "armies": compact_armies(node.get("armies") or []),
            "buildings": compact_buildings(node.get("buildings") or []),
            "label": node.get("label") or "",
        })
    return compact_rows(compacted, MAP_NODE_FIELDS, defaults=MAP_NODE_DEFAULTS)


def compact_org_people(people: Sequence[Mapping[str, Any]]) -> List[List[Any]]:
    return compact_rows(people, ORG_PERSON_FIELDS, defaults=ORG_PERSON_DEFAULTS)


def compact_org_slots(slots: Sequence[Mapping[str, Any]]) -> List[List[Any]]:
    compacted: List[Dict[str, Any]] = []
    for slot in slots:
        compacted.append({
            "title": slot.get("title"),
            "office_type": slot.get("office_type"),
            "count": slot.get("count"),
            "holders": compact_org_people(slot.get("holders") or []),
            "filled_count": slot.get("filled_count"),
            "vacancies": slot.get("vacancies"),
            "overflow_count": slot.get("overflow_count") or 0,
            "open_pool": bool(slot.get("open_pool")),
            "match_hint": slot.get("match_hint") or "",
        })
    return compact_rows(compacted, ORG_SLOT_FIELDS, defaults=ORG_SLOT_DEFAULTS)


def compact_org_institutions(institutions: Sequence[Mapping[str, Any]]) -> List[List[Any]]:
    compacted: List[Dict[str, Any]] = []
    for institution in institutions:
        compacted.append({
            "id": institution.get("id"),
            "name": institution.get("name"),
            "category": institution.get("category"),
            "mandate": institution.get("mandate"),
            "custom": bool(institution.get("custom")),
            "readiness": institution.get("readiness"),
            "coverage": institution.get("coverage"),
            "holder_quality": institution.get("holder_quality"),
            "execution_summary": institution.get("execution_summary") or "",
            "execution_risks": institution.get("execution_risks") or [],
            "slots": compact_org_slots(institution.get("slots") or []),
            "vacancy_count": institution.get("vacancy_count"),
            "holder_count": institution.get("holder_count"),
        })
    return compact_rows(compacted, ORG_INSTITUTION_FIELDS, defaults=ORG_INSTITUTION_DEFAULTS)


def compact_organization_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Compact nested organization charts without changing gameplay data."""
    return {
        **{
            key: value
            for key, value in payload.items()
            if key not in {"institutions", "unassigned"}
        },
        "org_person_fields": list(ORG_PERSON_FIELDS),
        "org_slot_fields": list(ORG_SLOT_FIELDS),
        "org_institution_fields": list(ORG_INSTITUTION_FIELDS),
        "institutions": compact_org_institutions(payload.get("institutions") or []),
        "unassigned": compact_org_people(payload.get("unassigned") or []),
    }


def monthly_followup_defaults(followups: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    defaults: Dict[str, Any] = {}
    for field in MONTHLY_FOLLOWUP_SHARED_DEFAULT_FIELDS:
        values = [item.get(field) for item in followups]
        if values and values[0] not in (None, "", []) and all(value == values[0] for value in values):
            defaults[field] = values[0]
    return defaults


def compact_monthly_followups(
    followups: Sequence[Mapping[str, Any]],
    defaults: Mapping[str, Any] | None = None,
) -> List[List[Any]]:
    return compact_rows(followups, MONTHLY_FOLLOWUP_FIELDS, defaults=defaults)


def compact_issues(issues: Sequence[Mapping[str, Any]]) -> List[List[Any]]:
    return compact_rows(issues, ISSUE_FIELDS, defaults=ISSUE_DEFAULTS)


def compact_legacies(legacies: Sequence[Mapping[str, Any]]) -> List[List[Any]]:
    return compact_rows(legacies, LEGACY_FIELDS, defaults=LEGACY_DEFAULTS)


def monthly_followups_payload(turn: int, followups: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Build the on-demand monthly follow-up panel payload."""
    defaults = monthly_followup_defaults(followups)
    return {
        "turn": int(turn),
        "followup_fields": list(MONTHLY_FOLLOWUP_FIELDS),
        "followup_defaults": defaults,
        "followups": compact_monthly_followups(followups, defaults=defaults),
    }
