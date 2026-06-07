#!/usr/bin/env python3
"""Audit clean runtime Environment seed data for the iOS prototype."""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ENV_DIR = ROOT / "ChongzhenSimulator" / "Resources" / "EnvironmentDatabase"


EXPECTED_TOP_LEVEL_REGIONS = {
    "北直隶",
    "南直隶",
    "山东",
    "山西",
    "河南",
    "陕西",
    "四川",
    "江西",
    "湖广",
    "浙江",
    "福建",
    "广东",
    "广西",
    "云南",
    "贵州",
}


def load_records(filename: str) -> list[dict[str, Any]]:
    path = ENV_DIR / filename
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload["records"]


def audit_formal_administrative_divisions(errors: list[str]) -> None:
    records = load_records("formal_administrative_divisions_1628_seed.json")
    ids = [record["region_id"] for record in records]
    id_counts = Counter(ids)
    duplicates = [region_id for region_id, count in id_counts.items() if count > 1]
    if duplicates:
        errors.append(f"formal_administrative_divisions_1628_seed: duplicate ids {duplicates[:8]}")

    by_id = {record["region_id"]: record for record in records}
    missing_parents = [
        record["region_id"]
        for record in records
        if record.get("parent_region_id") and record["parent_region_id"] not in by_id
    ]
    if missing_parents:
        errors.append(
            "formal_administrative_divisions_1628_seed: "
            f"missing parents for {missing_parents[:8]}"
        )

    levels = Counter(record["level"] for record in records)
    if levels[1] != 1:
        errors.append(f"formal_administrative_divisions_1628_seed: expected 1 empire root, got {levels[1]}")
    if levels[2] != 15:
        errors.append(f"formal_administrative_divisions_1628_seed: expected 15 top-level regions, got {levels[2]}")
    if levels[3] < 140:
        errors.append(f"formal_administrative_divisions_1628_seed: expected at least 140 prefecture-level units, got {levels[3]}")

    top_level_names = {record["name"] for record in records if record["level"] == 2}
    missing_top = sorted(EXPECTED_TOP_LEVEL_REGIONS - top_level_names)
    extra_top = sorted(top_level_names - EXPECTED_TOP_LEVEL_REGIONS)
    if missing_top:
        errors.append(f"formal_administrative_divisions_1628_seed: missing top-level regions {missing_top}")
    if extra_top:
        errors.append(f"formal_administrative_divisions_1628_seed: unexpected top-level regions {extra_top}")

    bad_level3 = [
        record["region_id"]
        for record in records
        if record["level"] == 3
        and by_id.get(record.get("parent_region_id"), {}).get("level") != 2
    ]
    if bad_level3:
        errors.append(
            "formal_administrative_divisions_1628_seed: "
            f"level-3 records not parented to level-2 regions {bad_level3[:8]}"
        )

    missing_place_text = [
        record["region_id"]
        for record in records
        if record["level"] == 3 and not record.get("subordinate_places_text")
    ]
    if missing_place_text:
        errors.append(
            "formal_administrative_divisions_1628_seed: "
            f"level-3 records missing subordinate place text {missing_place_text[:8]}"
        )

    print("Formal administrative divisions 1628:")
    print(f"  records: {len(records)}")
    for level, count in sorted(levels.items()):
        print(f"  level {level}: {count}")
    for region_type, count in sorted(Counter(record["region_type"] for record in records).items()):
        print(f"  {region_type}: {count}")


def main() -> int:
    errors: list[str] = []
    audit_formal_administrative_divisions(errors)

    if errors:
        print("\nFAIL environment database audit", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print("\nPASS environment database audit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
