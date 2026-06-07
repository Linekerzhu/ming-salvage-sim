#!/usr/bin/env python3
"""Audit clean runtime NPC seed data for the iOS prototype."""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
NPC_DIR = ROOT / "ChongzhenSimulator" / "Resources" / "NPCDatabase"

EXPECTED_LITERACY_LABELS = {
    1: "目不识丁",
    2: "白话俗语",
    3: "开蒙执笔",
    4: "经史成学",
    5: "博古通今",
}

EXPECTED_HOLDING_STATES = {
    "active_in_office": {
        "office_capacity_holder",
        "foreign_title_holder",
        "title_order_holder",
        "rebel_title_holder",
        "active_identity",
    },
    "active_unassigned": {"contact_only"},
    "candidate": {"candidate_pool"},
    "idle_home": {"inactive_reference"},
    "dismissed": {"inactive_reference"},
    "suspended": {"inactive_reference"},
    "retired": {"inactive_reference"},
    "imprisoned": {"unavailable_reference"},
    "exiled": {"unavailable_reference"},
    "offstage": {"unavailable_reference"},
    "dead": {"dead_reference"},
}

NON_ACTIVE_STATUSES = set(EXPECTED_HOLDING_STATES) - {"active_in_office"}

MINGPI_FORM_LABELS = {
    "wuyan_jueju": "五言绝句",
    "qiyan_jueju": "七言绝句",
    "duilian": "对联",
    "songci": "宋词",
    "xiaoqu": "小曲",
}

MINGPI_CIPAI_ALLOWLIST = {
    "临江仙",
    "菩萨蛮",
    "浣溪沙",
    "鹧鸪天",
    "蝶恋花",
    "虞美人",
    "浪淘沙令",
}

MINGPI_QUPAI_ALLOWLIST = {
    "山坡羊",
    "天净沙",
    "沉醉东风",
    "清江引",
    "水仙子",
}

MINGPI_GLOBAL_BANNED_TERMS = {
    "天罡",
    "命数",
    "心盘",
    "NPC",
    "游戏",
    "开局",
    "AI",
    "LLM",
    "prompt",
    "Prompt",
    "明廷",
    "大明",
    "崇祯",
    "天启",
    "万历",
    "南明",
    "清廷",
    "后金",
    "大清",
    "大顺",
    "东林",
    "阉党",
    "厂卫",
    "司礼监",
    "锦衣卫",
    "内阁",
    "东厂",
    "边镇",
    "流寇",
    "建州",
    "辽东",
    "辽西",
    "宁远",
    "皮岛",
    "山海关",
    "甲申",
}


def load_records(filename: str) -> list[dict[str, Any]]:
    path = NPC_DIR / filename
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload["records"]


def strip_ws(text: str) -> str:
    return "".join(str(text or "").split())


def line_len(line: str) -> int:
    return len(strip_ws(line))


def collect_mingpi_forbidden_terms(core: list[dict[str, Any]], start_by_id: dict[str, dict[str, Any]], npc_id: str) -> set[str]:
    terms = set(MINGPI_GLOBAL_BANNED_TERMS)
    for record in core:
        terms.add(record.get("canonical_name") or "")
        terms.update(record.get("aliases") or [])

    core_by_id = {record["npc_id"]: record for record in core}
    current = core_by_id[npc_id]
    native = current.get("native_place") or {}
    start = start_by_id.get(npc_id, {})
    for value in [
        native.get("province"),
        native.get("prefecture"),
        native.get("county"),
        start.get("start_office_title"),
        start.get("environment_office_canonical_title"),
    ]:
        if value:
            terms.add(str(value))
    return {strip_ws(term) for term in terms if len(strip_ws(term)) >= 2}


def npc_id_set(records: list[dict[str, Any]]) -> set[str]:
    return {record["npc_id"] for record in records}


def add_coverage_errors(
    errors: list[str],
    module_name: str,
    core_ids: set[str],
    records: list[dict[str, Any]],
) -> None:
    ids = npc_id_set(records)
    missing = sorted(core_ids - ids)
    extra = sorted(ids - core_ids)
    if missing:
        errors.append(f"{module_name}: missing {len(missing)} NPC records, e.g. {missing[:8]}")
    if extra:
        errors.append(f"{module_name}: has {len(extra)} unknown NPC records, e.g. {extra[:8]}")
    if len(ids) != len(records):
        duplicates = [npc_id for npc_id, count in Counter(record["npc_id"] for record in records).items() if count > 1]
        errors.append(f"{module_name}: duplicate npc_id values, e.g. {duplicates[:8]}")


def audit_literacy(core_ids: set[str], errors: list[str]) -> None:
    catalog = load_records("cultural_literacy_catalog_seed.json")
    records = load_records("npc_cultural_literacy_seed.json")
    add_coverage_errors(errors, "npc_cultural_literacy_seed", core_ids, records)

    catalog_by_level = {record["level"]: record for record in catalog}
    if set(catalog_by_level) != set(EXPECTED_LITERACY_LABELS):
        errors.append(
            "cultural_literacy_catalog_seed: levels must be exactly "
            f"{sorted(EXPECTED_LITERACY_LABELS)}"
        )

    for level, expected_label in EXPECTED_LITERACY_LABELS.items():
        actual = catalog_by_level.get(level, {}).get("label")
        if actual != expected_label:
            errors.append(
                "cultural_literacy_catalog_seed: "
                f"level {level} label is {actual!r}, expected {expected_label!r}"
            )

    for record in records:
        level = record.get("level")
        label = record.get("label")
        expected_label = EXPECTED_LITERACY_LABELS.get(level)
        if expected_label is None:
            errors.append(f"npc_cultural_literacy_seed: {record['npc_id']} has invalid level {level!r}")
        elif label != expected_label:
            errors.append(
                "npc_cultural_literacy_seed: "
                f"{record['npc_id']} level {level} label is {label!r}, expected {expected_label!r}"
            )


def audit_start_positions(core_ids: set[str], errors: list[str]) -> dict[str, dict[str, Any]]:
    records = load_records("npc_start_1628_positions_seed.json")
    add_coverage_errors(errors, "npc_start_1628_positions_seed", core_ids, records)

    start_by_id = {record["npc_id"]: record for record in records}
    reference_titles = 0
    holding_counter: Counter[tuple[str, str]] = Counter()
    for record in records:
        npc_id = record["npc_id"]
        status = record.get("start_status")
        holding_state = record.get("office_holding_state")
        holding_counter[(status, holding_state)] += 1

        expected_states = EXPECTED_HOLDING_STATES.get(status)
        if expected_states is None:
            errors.append(f"npc_start_1628_positions_seed: {npc_id} has unknown status {status!r}")
            continue
        if holding_state not in expected_states:
            errors.append(
                "npc_start_1628_positions_seed: "
                f"{npc_id} status {status!r} cannot use office_holding_state {holding_state!r}"
            )

        if status in NON_ACTIVE_STATUSES:
            if holding_state == "office_capacity_holder":
                errors.append(
                    "npc_start_1628_positions_seed: "
                    f"{npc_id} is {status!r} but still marked as current office capacity holder"
                )
            if record.get("start_office_title"):
                reference_titles += 1

    print("Start status / holding states:")
    for (status, holding_state), count in sorted(holding_counter.items()):
        print(f"  {status:18s} {holding_state:24s} {count}")
    print(f"Non-active reference titles retained: {reference_titles}")
    return start_by_id


def audit_appointments(start_by_id: dict[str, dict[str, Any]], errors: list[str]) -> None:
    records = load_records("npc_appointments_seed.json")
    active_capacity_by_npc: defaultdict[str, int] = defaultdict(int)

    for record in records:
        npc_id = record["npc_id"]
        active = bool(record.get("active"))
        occupies_capacity = bool(record.get("occupies_office_capacity"))
        if active and occupies_capacity:
            active_capacity_by_npc[npc_id] += 1
            start = start_by_id.get(npc_id)
            status = start.get("start_status") if start else None
            if status != "active_in_office":
                errors.append(
                    "npc_appointments_seed: "
                    f"{npc_id} has active capacity appointment {record['appointment_id']} "
                    f"but start_status is {status!r}"
                )

    for npc_id, start in start_by_id.items():
        if start.get("start_status") in NON_ACTIVE_STATUSES and active_capacity_by_npc.get(npc_id, 0):
            errors.append(
                "npc_appointments_seed: "
                f"{npc_id} is {start['start_status']!r} but has "
                f"{active_capacity_by_npc[npc_id]} active capacity appointment(s)"
            )


def audit_mingpi(core: list[dict[str, Any]], core_ids: set[str], start_by_id: dict[str, dict[str, Any]], errors: list[str]) -> None:
    try:
        records = load_records("npc_mingpi_seed.json")
    except FileNotFoundError:
        errors.append("npc_mingpi_seed: missing seed file")
        return

    add_coverage_errors(errors, "npc_mingpi_seed", core_ids, records)
    form_counter: Counter[str] = Counter()
    for record in records:
        npc_id = record["npc_id"]
        form_id = record.get("form_id")
        form_counter[form_id] += 1
        lines = record.get("lines") or []
        joined = strip_ws(record.get("title", "")) + "".join(strip_ws(line) for line in lines)

        if record.get("display_name") != "命批":
            errors.append(f"npc_mingpi_seed: {npc_id} display_name must be 命批")
        if record.get("profile_version") != 1:
            errors.append(f"npc_mingpi_seed: {npc_id} profile_version must be 1")
        if form_id not in MINGPI_FORM_LABELS:
            errors.append(f"npc_mingpi_seed: {npc_id} invalid form_id {form_id!r}")
            continue
        if record.get("form_label") != MINGPI_FORM_LABELS[form_id]:
            errors.append(f"npc_mingpi_seed: {npc_id} form_label mismatch")
        if not strip_ws(record.get("title", "")):
            errors.append(f"npc_mingpi_seed: {npc_id} title is empty")
        if not lines:
            errors.append(f"npc_mingpi_seed: {npc_id} lines are empty")

        found_terms = sorted(term for term in collect_mingpi_forbidden_terms(core, start_by_id, npc_id) if term in joined)
        if found_terms:
            errors.append(f"npc_mingpi_seed: {npc_id} contains forbidden direct terms {found_terms[:12]}")

        if form_id in {"wuyan_jueju", "qiyan_jueju"}:
            expected = 5 if form_id == "wuyan_jueju" else 7
            if len(lines) != 4:
                errors.append(f"npc_mingpi_seed: {npc_id} {MINGPI_FORM_LABELS[form_id]} must have 4 lines")
            for index, line in enumerate(lines, 1):
                if line_len(line) != expected:
                    errors.append(f"npc_mingpi_seed: {npc_id} line {index} must have {expected} chars")
        elif form_id == "duilian":
            if len(lines) != 2:
                errors.append(f"npc_mingpi_seed: {npc_id} duilian must have 2 lines")
            elif line_len(lines[0]) != line_len(lines[1]):
                errors.append(f"npc_mingpi_seed: {npc_id} duilian lines must have equal length")
        elif form_id == "songci":
            if record.get("cipai") not in MINGPI_CIPAI_ALLOWLIST:
                errors.append(f"npc_mingpi_seed: {npc_id} invalid cipai {record.get('cipai')!r}")
            if not (3 <= len(lines) <= 12):
                errors.append(f"npc_mingpi_seed: {npc_id} songci must have 3-12 lines")
        elif form_id == "xiaoqu":
            if record.get("qupai") not in MINGPI_QUPAI_ALLOWLIST:
                errors.append(f"npc_mingpi_seed: {npc_id} invalid qupai {record.get('qupai')!r}")
            if not (3 <= len(lines) <= 10):
                errors.append(f"npc_mingpi_seed: {npc_id} xiaoqu must have 3-10 lines")

        prosody = record.get("prosody_check") or {}
        if prosody.get("passed") is not True:
            errors.append(f"npc_mingpi_seed: {npc_id} prosody_check.passed must be true")
        if not strip_ws(prosody.get("notes", "")):
            errors.append(f"npc_mingpi_seed: {npc_id} prosody_check.notes is empty")

    print("Mingpi form counts:")
    for form_id, count in sorted(form_counter.items()):
        print(f"  {form_id:14s} {count}")


def main() -> int:
    if not NPC_DIR.exists():
        print(f"NPC database directory not found: {NPC_DIR}", file=sys.stderr)
        return 2

    errors: list[str] = []
    core = load_records("npc_core_seed.json")
    core_ids = npc_id_set(core)

    print(f"Core NPC records: {len(core)}")
    audit_literacy(core_ids, errors)
    start_by_id = audit_start_positions(core_ids, errors)
    audit_appointments(start_by_id, errors)
    audit_mingpi(core, core_ids, start_by_id, errors)

    if errors:
        print("\nFAIL npc database audit")
        for index, error in enumerate(errors, 1):
            print(f"{index}. {error}")
        return 1

    print("\nPASS npc database audit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
