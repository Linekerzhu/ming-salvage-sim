#!/usr/bin/env python3
"""Audit whether NPC and environment seeds are ready to form a gameplay graph.

This is stricter than the table-level audits. Passing NPCDatabase and
EnvironmentDatabase audits only proves that seed files are internally decodable.
This audit checks whether the two foundations are connected enough for runtime
simulation.
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RESOURCE_DIR = ROOT / "ChongzhenSimulator" / "Resources"
NPC_DIR = RESOURCE_DIR / "NPCDatabase"
ENV_DIR = RESOURCE_DIR / "EnvironmentDatabase"

EXPECTED_P0_NPC_FILES = {
    "npc_foundation_base_info_seed.json": "new NPC layer 1.1 base information",
    "npc_region_links_seed.json": "NPC native/current-place links to formal region_id values",
    "npc_governance_profiles_seed.json": "NPC capability/value mappings to governance domains",
}

EXPECTED_P0_ENV_FILES = {
    "regional_risk_baseline_1628_seed.json": "province and prefecture risk baselines",
    "institution_governance_domains_seed.json": "institution to governance-domain mappings",
    "office_capacity_slots_seed.json": "office slot capacity and vacancy graph",
    "administrative_governance_links_1628_seed.json": "region to standing-official jurisdiction graph",
}

LOCAL_OR_PROVINCIAL_SCOPES = {
    "local_prefecture",
    "local_county",
    "provincial",
    "provincial_or_regional",
    "frontier_or_provincial_military",
    "frontier_native_chieftain_administration",
}

BASE_INFO_IDENTITY_CATEGORIES = {"民籍", "奴籍", "官籍", "军籍", "贵族", "外族"}
BASE_INFO_SEX_CATEGORIES = {"male", "female", "eunuch"}
BASE_INFO_EXISTENCE_CATEGORIES = {"在世", "死亡", "未登场"}
REGION_LINK_STATUSES = {
    "linked_to_formal_region",
    "external_native_place",
    "llm_inferred_pending_review",
}
REGION_LINK_SOURCE_KINDS = {
    "legacy_text",
    "biography_explicit",
    "llm_inferred",
    "external_inferred",
    "user_override",
}
REGION_LINK_CONFIDENCE_VALUES = {"high", "medium", "low"}
REGION_LINK_REVIEW_VALUES = {"accepted", "needs_review"}


def load_records(directory: Path, filename: str) -> list[dict[str, Any]]:
    with (directory / filename).open(encoding="utf-8") as handle:
        return json.load(handle)["records"]


def record_ids(records: list[dict[str, Any]], key: str) -> set[str]:
    return {record[key] for record in records if record.get(key)}


def add_missing_file_errors(
    errors: list[str],
    directory: Path,
    expected: dict[str, str],
    label: str,
) -> None:
    for filename, reason in expected.items():
        if not (directory / filename).exists():
            errors.append(f"{label}: missing {filename} ({reason})")


def audit_existing_crosslinks(errors: list[str], warnings: list[str]) -> None:
    core = load_records(NPC_DIR, "npc_core_seed.json")
    start_positions = load_records(NPC_DIR, "npc_start_1628_positions_seed.json")
    appointments = load_records(NPC_DIR, "npc_appointments_seed.json")
    relationships = load_records(NPC_DIR, "npc_relationship_edges_seed.json")
    affiliations = load_records(NPC_DIR, "npc_affiliations_seed.json")

    institutions = load_records(ENV_DIR, "ming_institutions_seed.json")
    offices = load_records(ENV_DIR, "ming_office_posts_seed.json")
    formal_regions = load_records(ENV_DIR, "formal_administrative_divisions_1628_seed.json")

    npc_ids = record_ids(core, "npc_id")
    institution_ids = record_ids(institutions, "institution_id")
    office_ids = record_ids(offices, "office_post_id")
    formal_region_ids = record_ids(formal_regions, "region_id")

    missing_relationship_endpoints = [
        edge["edge_id"]
        for edge in relationships
        if edge.get("from_npc_id") not in npc_ids or edge.get("to_npc_id") not in npc_ids
    ]
    if missing_relationship_endpoints:
        errors.append(
            "npc_relationship_edges_seed: unknown endpoint ids "
            f"{missing_relationship_endpoints[:8]}"
        )

    missing_start_institutions = [
        record["npc_id"]
        for record in start_positions
        if record.get("institution_id") not in institution_ids
    ]
    if missing_start_institutions:
        errors.append(
            "npc_start_1628_positions_seed: missing environment institution ids "
            f"{missing_start_institutions[:8]}"
        )

    missing_start_offices = [
        record["npc_id"]
        for record in start_positions
        if record.get("environment_office_post_id")
        and record.get("environment_office_post_id") not in office_ids
    ]
    if missing_start_offices:
        errors.append(
            "npc_start_1628_positions_seed: missing environment office ids "
            f"{missing_start_offices[:8]}"
        )

    missing_appointment_offices = [
        record["appointment_id"]
        for record in appointments
        if record.get("environment_office_post_id")
        and record.get("environment_office_post_id") not in office_ids
    ]
    if missing_appointment_offices:
        errors.append(
            "npc_appointments_seed: missing environment office ids "
            f"{missing_appointment_offices[:8]}"
        )

    native_place_region_ids = [
        record.get("native_place", {}).get("region_id")
        or record.get("native_place", {}).get("prefecture_region_id")
        or record.get("native_place", {}).get("province_region_id")
        for record in core
    ]
    linked_native_places = [region_id for region_id in native_place_region_ids if region_id]
    bad_native_region_ids = sorted(set(linked_native_places) - formal_region_ids)
    if bad_native_region_ids:
        errors.append(f"npc_core_seed: native_place uses unknown region ids {bad_native_region_ids[:8]}")

    office_scope_counts = Counter(record.get("jurisdiction_scope") for record in offices)
    unknown_office_scopes = office_scope_counts.get("unknown", 0)
    if unknown_office_scopes:
        errors.append(f"ming_office_posts_seed: {unknown_office_scopes} office posts still have unknown jurisdiction_scope")

    local_scope_offices = [
        record
        for record in offices
        if record.get("jurisdiction_scope") in LOCAL_OR_PROVINCIAL_SCOPES
    ]
    offices_with_region_scope = [
        record
        for record in local_scope_offices
        if record.get("region_scope_ids") or record.get("default_region_id")
    ]
    if local_scope_offices and len(offices_with_region_scope) != len(local_scope_offices):
        errors.append(
            "ming_office_posts_seed: local/provincial offices lack explicit region scope ids "
            f"({len(offices_with_region_scope)}/{len(local_scope_offices)} linked)"
        )

    office_sphere_affiliations = 0
    office_sphere_id_like_environment_id = 0
    for record in affiliations:
        for item in record.get("affiliations", []):
            if item.get("kind") != "office_sphere":
                continue
            office_sphere_affiliations += 1
            if item.get("id") in institution_ids:
                office_sphere_id_like_environment_id += 1
    if office_sphere_affiliations and office_sphere_id_like_environment_id != office_sphere_affiliations:
        warnings.append(
            "npc_affiliations_seed: office_sphere currently uses display labels, "
            f"not stable environment institution ids ({office_sphere_id_like_environment_id}/"
            f"{office_sphere_affiliations} id-linked)"
        )

    active_capacity_by_office: dict[str, list[str]] = defaultdict(list)
    office_policy_by_id = {record["office_post_id"]: record.get("capacity_policy") for record in offices}
    for record in appointments:
        office_id = record.get("environment_office_post_id")
        if not (record.get("active") and record.get("occupies_office_capacity") and office_id):
            continue
        active_capacity_by_office[office_id].append(record["npc_id"])

    singleton_overfills = [
        (office_id, holders)
        for office_id, holders in active_capacity_by_office.items()
        if office_policy_by_id.get(office_id) == "singleton" and len(holders) > 1
    ]
    if singleton_overfills:
        errors.append(
            "npc_appointments_seed: singleton office overfilled "
            f"{[(office_id, len(holders)) for office_id, holders in singleton_overfills[:8]]}"
        )

    print("Foundation graph source summary:")
    print(f"  NPC records: {len(core)}")
    print(f"  appointment records: {len(appointments)}")
    print(f"  relationship edges: {len(relationships)}")
    print(f"  formal regions: {len(formal_regions)}")
    print(f"  institutions: {len(institutions)}")
    print(f"  office posts: {len(offices)}")
    print(f"  active occupied office slots: {sum(len(v) for v in active_capacity_by_office.values())}")
    print(f"  office jurisdiction scopes: {dict(sorted(office_scope_counts.items()))}")


def audit_npc_foundation_base_info(errors: list[str], warnings: list[str]) -> None:
    path = NPC_DIR / "npc_foundation_base_info_seed.json"
    if not path.exists():
        return

    core = load_records(NPC_DIR, "npc_core_seed.json")
    core_ids = record_ids(core, "npc_id")
    records = load_records(NPC_DIR, "npc_foundation_base_info_seed.json")
    ids = [record.get("npc_id") for record in records]
    id_counts = Counter(ids)
    duplicates = [npc_id for npc_id, count in id_counts.items() if npc_id and count > 1]
    if duplicates:
        errors.append(f"npc_foundation_base_info_seed: duplicate npc_id values {duplicates[:8]}")

    record_ids_set = {npc_id for npc_id in ids if npc_id}
    missing = sorted(core_ids - record_ids_set)
    extra = sorted(record_ids_set - core_ids)
    if missing:
        errors.append(f"npc_foundation_base_info_seed: missing core NPC ids {missing[:8]}")
    if extra:
        errors.append(f"npc_foundation_base_info_seed: unknown NPC ids {extra[:8]}")

    bad_sex = [
        record.get("npc_id")
        for record in records
        if (record.get("sex") or {}).get("category") not in BASE_INFO_SEX_CATEGORIES
    ]
    if bad_sex:
        errors.append(f"npc_foundation_base_info_seed: invalid sex categories {bad_sex[:8]}")

    bad_identity = [
        record.get("npc_id")
        for record in records
        if (record.get("initial_identity") or {}).get("category") not in BASE_INFO_IDENTITY_CATEGORIES
    ]
    if bad_identity:
        errors.append(f"npc_foundation_base_info_seed: invalid initial_identity categories {bad_identity[:8]}")

    missing_power = [
        record.get("npc_id")
        for record in records
        if not (record.get("initial_power") or {}).get("power_id")
    ]
    if missing_power:
        errors.append(f"npc_foundation_base_info_seed: missing initial_power {missing_power[:8]}")

    missing_life_span = [
        record.get("npc_id")
        for record in records
        if "life_span" not in record
    ]
    if missing_life_span:
        errors.append(f"npc_foundation_base_info_seed: missing life_span {missing_life_span[:8]}")

    bad_existence = [
        record.get("npc_id")
        for record in records
        if (record.get("existence") or {}).get("category") not in BASE_INFO_EXISTENCE_CATEGORIES
    ]
    if bad_existence:
        errors.append(f"npc_foundation_base_info_seed: invalid existence categories {bad_existence[:8]}")

    underage_onstage = [
        record.get("npc_id")
        for record in records
        if (record.get("initial_age") or {}).get("value") is not None
        and (record.get("initial_age") or {}).get("value") < 16
        and (record.get("existence") or {}).get("category") != "未登场"
    ]
    if underage_onstage:
        errors.append(
            "npc_foundation_base_info_seed: NPCs under age 16 at 1628 must be 未登场 "
            f"{underage_onstage[:8]}"
        )

    wei = next((record for record in records if record.get("canonical_name") == "魏忠贤"), None)
    if not wei:
        errors.append("npc_foundation_base_info_seed: missing 魏忠贤")
    else:
        wei_age = (wei.get("initial_age") or {}).get("value")
        wei_existence = (wei.get("existence") or {}).get("category")
        wei_birth_year = (wei.get("life_span") or {}).get("birth_year")
        if wei_age != 52 or wei_birth_year != 1576 or wei_existence != "在世":
            errors.append(
                "npc_foundation_base_info_seed: 魏忠贤 must be revived as 在世, "
                "age 52 at 1628, birth_year 1576"
            )

    missing_biographies = [
        record.get("npc_id")
        for record in records
        if not (record.get("biography") or {}).get("text")
    ]
    if missing_biographies:
        errors.append(f"npc_foundation_base_info_seed: missing biography text {missing_biographies[:8]}")

    missing_mingpi = [
        record.get("npc_id")
        for record in records
        if not (record.get("mingpi") or {}).get("lines")
    ]
    if missing_mingpi:
        errors.append(f"npc_foundation_base_info_seed: missing mingpi lines {missing_mingpi[:8]}")

    missing_age = [
        record.get("npc_id")
        for record in records
        if (record.get("initial_age") or {}).get("value") is None
    ]
    if missing_age:
        warnings.append(
            "npc_foundation_base_info_seed: initial age empty for "
            f"{len(missing_age)} NPC(s), e.g. {missing_age[:8]}"
        )

    empty_native_place = [
        record.get("npc_id")
        for record in records
        if (record.get("native_place") or {}).get("region_link_status") == "empty_pending_import"
    ]
    if empty_native_place:
        warnings.append(
            "npc_foundation_base_info_seed: native place empty for "
            f"{len(empty_native_place)} NPC(s); this is expected until region-link pass"
        )

    identity_review = [
        record.get("npc_id")
        for record in records
        if (record.get("initial_identity") or {}).get("review_status") == "needs_review"
    ]
    if identity_review:
        warnings.append(
            "npc_foundation_base_info_seed: initial identity needs review for "
            f"{len(identity_review)} NPC(s), mostly external/rebel/specialist categories"
        )

    print("NPC foundation base info:")
    print(f"  records: {len(records)}")
    print(f"  missing initial age: {len(missing_age)}")
    print(f"  empty native place: {len(empty_native_place)}")
    print(f"  identity needs review: {len(identity_review)}")
    print(f"  existence categories: {dict(Counter((record.get('existence') or {}).get('category') for record in records))}")


def audit_npc_region_links(errors: list[str], warnings: list[str]) -> None:
    path = NPC_DIR / "npc_region_links_seed.json"
    if not path.exists():
        return

    foundation = load_records(NPC_DIR, "npc_foundation_base_info_seed.json")
    region_links = load_records(NPC_DIR, "npc_region_links_seed.json")
    formal_regions = load_records(ENV_DIR, "formal_administrative_divisions_1628_seed.json")
    foundation_ids = record_ids(foundation, "npc_id")
    region_ids = [record.get("npc_id") for record in region_links]
    region_id_set = {npc_id for npc_id in region_ids if npc_id}

    duplicates = [npc_id for npc_id, count in Counter(region_ids).items() if npc_id and count > 1]
    if duplicates:
        errors.append(f"npc_region_links_seed: duplicate npc_id values {duplicates[:8]}")

    missing = sorted(foundation_ids - region_id_set)
    extra = sorted(region_id_set - foundation_ids)
    if missing:
        errors.append(f"npc_region_links_seed: missing NPC ids {missing[:8]}")
    if extra:
        errors.append(f"npc_region_links_seed: unknown NPC ids {extra[:8]}")

    regions_by_id = {record["region_id"]: record for record in formal_regions}
    province_ids = {record["name"]: record["region_id"] for record in formal_regions if record["level"] == 2}
    prefecture_pairs: dict[tuple[str, str], tuple[str, str]] = {}
    for record in formal_regions:
        if record["level"] != 3:
            continue
        parent = regions_by_id[record["parent_region_id"]]
        prefecture_pairs[(parent["name"], record["name"])] = (parent["region_id"], record["region_id"])

    links_by_id = {record["npc_id"]: record for record in region_links}
    foundation_by_id = {record["npc_id"]: record for record in foundation}
    external_count = 0
    inferred_count = 0
    for record in region_links:
        npc_id = record.get("npc_id")
        native = record.get("native_place") or {}
        province = native.get("province") or ""
        prefecture = native.get("prefecture") or ""
        is_external = native.get("is_external")
        if not province or not prefecture:
            errors.append(f"npc_region_links_seed: {npc_id} missing province/prefecture")
        if "county" in native or "county_region_id" in native:
            errors.append(f"npc_region_links_seed: {npc_id} must not include county fields")
        if record.get("source_kind") not in REGION_LINK_SOURCE_KINDS:
            errors.append(f"npc_region_links_seed: {npc_id} invalid source_kind {record.get('source_kind')!r}")
        if record.get("confidence") not in REGION_LINK_CONFIDENCE_VALUES:
            errors.append(f"npc_region_links_seed: {npc_id} invalid confidence {record.get('confidence')!r}")
        if record.get("review_status") not in REGION_LINK_REVIEW_VALUES:
            errors.append(f"npc_region_links_seed: {npc_id} invalid review_status {record.get('review_status')!r}")

        if is_external is True:
            external_count += 1
            if native.get("province_region_id") is not None or native.get("prefecture_region_id") is not None:
                errors.append(f"npc_region_links_seed: {npc_id} external record must not use formal region ids")
        elif is_external is False:
            expected = prefecture_pairs.get((province, prefecture))
            if expected is None:
                errors.append(f"npc_region_links_seed: {npc_id} invalid formal native place {province} {prefecture}")
            else:
                if native.get("province_region_id") != expected[0] or native.get("prefecture_region_id") != expected[1]:
                    errors.append(f"npc_region_links_seed: {npc_id} region ids do not match {province} {prefecture}")
                if province not in province_ids:
                    errors.append(f"npc_region_links_seed: {npc_id} invalid province {province}")
        else:
            errors.append(f"npc_region_links_seed: {npc_id} is_external must be boolean")

        if record.get("source_kind") == "llm_inferred":
            inferred_count += 1

    for npc_id, foundation_record in foundation_by_id.items():
        link = links_by_id.get(npc_id)
        if not link:
            continue
        native = link.get("native_place") or {}
        displayed = foundation_record.get("native_place") or {}
        if displayed.get("province") != native.get("province") or displayed.get("prefecture") != native.get("prefecture"):
            errors.append(f"npc_foundation_base_info_seed: native_place not synced for {npc_id}")
        if displayed.get("region_link_status") not in REGION_LINK_STATUSES:
            errors.append(f"npc_foundation_base_info_seed: invalid region_link_status for {npc_id}")

    print("NPC region links:")
    print(f"  records: {len(region_links)}")
    print(f"  external native places: {external_count}")
    print(f"  llm inferred native places: {inferred_count}")


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []

    add_missing_file_errors(errors, NPC_DIR, EXPECTED_P0_NPC_FILES, "NPC P0")
    add_missing_file_errors(errors, ENV_DIR, EXPECTED_P0_ENV_FILES, "Environment P0")
    audit_npc_foundation_base_info(errors, warnings)
    audit_npc_region_links(errors, warnings)
    audit_existing_crosslinks(errors, warnings)

    if warnings:
        print("\nWARN foundation graph audit")
        for index, warning in enumerate(warnings, 1):
            print(f"{index}. {warning}")

    if errors:
        print("\nFAIL foundation graph audit")
        for index, error in enumerate(errors, 1):
            print(f"{index}. {error}")
        return 1

    print("\nPASS foundation graph audit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
