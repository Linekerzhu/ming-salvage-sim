#!/usr/bin/env python3
"""Generate NPC native-place region links with DeepSeek.

This script creates the new foundation table npc_region_links_seed.json.
It treats older NPC seed files as legacy sources and keeps one isolated LLM
request per NPC when inference is needed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RESOURCE_DIR = ROOT / "ChongzhenSimulator" / "Resources"
NPC_DIR = RESOURCE_DIR / "NPCDatabase"
ENV_DIR = RESOURCE_DIR / "EnvironmentDatabase"
OUTPUT_FILE = NPC_DIR / "npc_region_links_seed.json"
FOUNDATION_FILE = NPC_DIR / "npc_foundation_base_info_seed.json"
MANIFEST_FILE = NPC_DIR / "npc_database_manifest.json"
DEEPSEEK_CHAT_COMPLETIONS_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-v4-flash"

EXTERNAL_POWER_DEFAULTS = {
    "houjin": ("后金", "赫图阿拉"),
    "mongol": ("蒙古", "察哈尔"),
    "korea": ("朝鲜", "汉城府"),
}

EXTERNAL_NAME_DEFAULTS = {
    "西洋": ("西洋", "利玛窦故里"),
    "南洋": ("南洋", "吕宋"),
    "倭寇": ("日本", "九州"),
}

SOURCE_KINDS = {
    "legacy_text",
    "biography_explicit",
    "llm_inferred",
    "external_inferred",
    "user_override",
}
CONFIDENCE_VALUES = {"high", "medium", "low"}
REVIEW_VALUES = {"accepted", "needs_review"}


SYSTEM_PROMPT = """你是《崇祯模拟器》的 NPC 籍贯设定官。

你的任务是为单个 NPC 设定“省 州府”级籍贯。这个项目追求可信架空，不追求逐条史实考据。

必须遵守：
1. 只输出 JSON 对象，不要输出解释文字。
2. 对大明人物，province 必须从 allowed_regions 的省级名称中选择，prefecture 必须从该省的州府列表中选择。
3. 不要输出县、乡、村、现代市名。
4. 如果列传或已有籍贯有明确线索，优先继承。
5. 如果列传写“不知何许人”“籍贯未知”或没有线索，请结合姓名、职业、身份、派系、人物气质和明末地域逻辑可信架空。
6. 流寇阵营不是外族，必须落到明制州府。
7. evidence_text 必须是短句，不能超过 40 个汉字。
8. rationale 必须是一句话，说明选择逻辑。

输出 JSON 形状：
{
  "province": "...",
  "prefecture": "...",
  "source_kind": "biography_explicit|llm_inferred",
  "confidence": "high|medium|low",
  "evidence_text": "...",
  "rationale": "..."
}
"""


def load_records(directory: Path, filename: str) -> list[dict[str, Any]]:
    with (directory / filename).open(encoding="utf-8") as handle:
        return json.load(handle)["records"]


def load_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": 1, "records": []}
    with path.open(encoding="utf-8") as handle:
        return json.load(handle, object_pairs_hook=OrderedDict)


def write_payload(path: Path, payload: dict[str, Any]) -> None:
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temp_path.replace(path)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def by_id(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {record["npc_id"]: record for record in records}


def normalize_text(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def build_region_indexes() -> tuple[dict[str, str], dict[str, tuple[str, str]], dict[str, list[str]]]:
    records = load_records(ENV_DIR, "formal_administrative_divisions_1628_seed.json")
    by_id = {record["region_id"]: record for record in records}
    province_ids: dict[str, str] = {}
    prefecture_lookup: dict[str, tuple[str, str]] = {}
    province_to_prefectures: dict[str, list[str]] = {}

    for record in records:
        if record["level"] == 2:
            province_ids[record["name"]] = record["region_id"]

    for record in records:
        if record["level"] != 3:
            continue
        parent = by_id[record["parent_region_id"]]
        province_name = parent["name"]
        province_to_prefectures.setdefault(province_name, []).append(record["name"])
        prefecture_lookup[f"{province_name}|{record['name']}"] = (parent["region_id"], record["region_id"])

    for names in province_to_prefectures.values():
        names.sort()
    return province_ids, prefecture_lookup, province_to_prefectures


def parse_model_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise


def call_deepseek(api_key: str, model: str, messages: list[dict[str, str]], retries: int = 2) -> str:
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 1000,
        "response_format": {"type": "json_object"},
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        request = urllib.request.Request(
            DEEPSEEK_CHAT_COMPLETIONS_URL,
            data=data,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                body = json.loads(response.read().decode("utf-8"))
            content = body["choices"][0]["message"]["content"]
            if not content or not str(content).strip():
                raise RuntimeError("empty DeepSeek response content")
            return content
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, KeyError, json.JSONDecodeError) as error:
            last_error = error
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"DeepSeek request failed: {last_error}")


def external_native_place(profile: dict[str, Any]) -> tuple[str, str] | None:
    power_id = profile.get("initial_power", {}).get("power_id") or ""
    if power_id in EXTERNAL_POWER_DEFAULTS:
        return EXTERNAL_POWER_DEFAULTS[power_id]
    name = profile["canonical_name"]
    for marker, place in EXTERNAL_NAME_DEFAULTS.items():
        if marker in name:
            return place
    return None


def legacy_native_place(
    profile: dict[str, Any],
    prefecture_lookup: dict[str, tuple[str, str]],
) -> dict[str, Any] | None:
    native = profile.get("native_place") or {}
    province = native.get("province") or ""
    prefecture = native.get("prefecture") or ""
    if not (province and prefecture):
        return None
    ids = prefecture_lookup.get(f"{province}|{prefecture}")
    if not ids:
        return None
    province_region_id, prefecture_region_id = ids
    return make_record(
        profile=profile,
        province=province,
        prefecture=prefecture,
        province_region_id=province_region_id,
        prefecture_region_id=prefecture_region_id,
        is_external=False,
        source_kind="legacy_text",
        confidence="high",
        evidence_text=f"旧基础籍贯：{province}{prefecture}",
        rationale="旧基础表已有省府文本，且能映射到正式行政区。",
        review_status="accepted",
    )


def make_record(
    *,
    profile: dict[str, Any],
    province: str,
    prefecture: str,
    province_region_id: str | None,
    prefecture_region_id: str | None,
    is_external: bool,
    source_kind: str,
    confidence: str,
    evidence_text: str,
    rationale: str,
    review_status: str,
) -> OrderedDict[str, Any]:
    return OrderedDict(
        [
            ("npc_id", profile["npc_id"]),
            (
                "native_place",
                OrderedDict(
                    [
                        ("province", province),
                        ("prefecture", prefecture),
                        ("province_region_id", province_region_id),
                        ("prefecture_region_id", prefecture_region_id),
                        ("is_external", is_external),
                    ]
                ),
            ),
            ("source_kind", source_kind),
            ("confidence", confidence),
            ("evidence_text", evidence_text[:80]),
            ("rationale", rationale[:160]),
            ("review_status", review_status),
        ]
    )


def build_llm_messages(profile: dict[str, Any], province_to_prefectures: dict[str, list[str]]) -> list[dict[str, str]]:
    native = profile.get("native_place") or {}
    mingpi = profile.get("mingpi") or {}
    fact_packet = OrderedDict(
        [
            ("npc_id", profile["npc_id"]),
            ("姓名", profile["canonical_name"]),
            ("别名", profile.get("aliases", [])),
            ("性别", (profile.get("sex") or {}).get("label", "")),
            ("身份", (profile.get("initial_identity") or {}).get("category", "")),
            ("阵营", (profile.get("initial_power") or {}).get("label", "")),
            ("存在", (profile.get("existence") or {}).get("category", "")),
            ("现有籍贯", f"{native.get('province','')} {native.get('prefecture','')}".strip() or "无"),
            ("列传", normalize_text((profile.get("biography") or {}).get("text", ""))[:900]),
            ("命批", " / ".join([mingpi.get("title", "")] + (mingpi.get("lines") or []))[:220]),
            ("allowed_regions", province_to_prefectures),
        ]
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(fact_packet, ensure_ascii=False)},
    ]


def validate_record(
    record: dict[str, Any],
    core_ids: set[str],
    prefecture_lookup: dict[str, tuple[str, str]],
) -> list[str]:
    errors: list[str] = []
    npc_id = record.get("npc_id")
    if npc_id not in core_ids:
        errors.append(f"unknown npc_id {npc_id!r}")
    native = record.get("native_place") or {}
    province = native.get("province") or ""
    prefecture = native.get("prefecture") or ""
    if not province or not prefecture:
        errors.append(f"{npc_id}: province/prefecture required")
    if any(key in native for key in ["county", "county_region_id"]):
        errors.append(f"{npc_id}: county fields are not allowed")
    is_external = native.get("is_external")
    if not isinstance(is_external, bool):
        errors.append(f"{npc_id}: is_external must be boolean")
    if is_external:
        if native.get("province_region_id") is not None or native.get("prefecture_region_id") is not None:
            errors.append(f"{npc_id}: external native place must not use formal region ids")
    else:
        expected = prefecture_lookup.get(f"{province}|{prefecture}")
        if not expected:
            errors.append(f"{npc_id}: {province} {prefecture} is not a formal province/prefecture pair")
        else:
            if native.get("province_region_id") != expected[0] or native.get("prefecture_region_id") != expected[1]:
                errors.append(f"{npc_id}: region ids do not match {province} {prefecture}")
    if record.get("source_kind") not in SOURCE_KINDS:
        errors.append(f"{npc_id}: invalid source_kind {record.get('source_kind')!r}")
    if record.get("confidence") not in CONFIDENCE_VALUES:
        errors.append(f"{npc_id}: invalid confidence {record.get('confidence')!r}")
    if record.get("review_status") not in REVIEW_VALUES:
        errors.append(f"{npc_id}: invalid review_status {record.get('review_status')!r}")
    return errors


def generate_one(
    profile: dict[str, Any],
    api_key: str,
    model: str,
    prefecture_lookup: dict[str, tuple[str, str]],
    province_to_prefectures: dict[str, list[str]],
) -> OrderedDict[str, Any]:
    external = external_native_place(profile)
    if external:
        province, prefecture = external
        return make_record(
            profile=profile,
            province=province,
            prefecture=prefecture,
            province_region_id=None,
            prefecture_region_id=None,
            is_external=True,
            source_kind="external_inferred",
            confidence="medium",
            evidence_text=f"{(profile.get('initial_power') or {}).get('label','')}外部人物",
            rationale="外族或外来人物保留外部籍贯，不强行纳入明制州府。",
            review_status="accepted",
        )

    legacy = legacy_native_place(profile, prefecture_lookup)
    if legacy:
        return legacy

    last_error: Exception | None = None
    parsed: dict[str, Any] | None = None
    province = ""
    prefecture = ""
    ids: tuple[str, str] | None = None
    for attempt in range(3):
        try:
            messages = build_llm_messages(profile, province_to_prefectures)
            if attempt:
                messages.append(
                    {
                        "role": "user",
                        "content": "上一次输出无法解析或州府不在列表中。请只输出合法 JSON，province 和 prefecture 必须来自 allowed_regions。",
                    }
                )
            raw_text = call_deepseek(api_key, model, messages)
            parsed = parse_model_json(raw_text)
            province = str(parsed.get("province") or "").strip()
            prefecture = str(parsed.get("prefecture") or "").strip()
            ids = prefecture_lookup.get(f"{province}|{prefecture}")
            if ids:
                break
            raise RuntimeError(f"model returned invalid place {province} {prefecture}")
        except Exception as error:
            last_error = error
            time.sleep(1.0 * (attempt + 1))
    if not parsed or not ids:
        raise RuntimeError(f"{profile['npc_id']}: model output failed after retries: {last_error}")
    source_kind = parsed.get("source_kind")
    if source_kind not in {"biography_explicit", "llm_inferred"}:
        source_kind = "llm_inferred"
    confidence = parsed.get("confidence")
    if confidence not in CONFIDENCE_VALUES:
        confidence = "medium"
    review_status = "accepted" if confidence == "high" and source_kind == "biography_explicit" else "needs_review"
    return make_record(
        profile=profile,
        province=province,
        prefecture=prefecture,
        province_region_id=ids[0],
        prefecture_region_id=ids[1],
        is_external=False,
        source_kind=source_kind,
        confidence=confidence,
        evidence_text=normalize_text(str(parsed.get("evidence_text") or ""))[:80],
        rationale=normalize_text(str(parsed.get("rationale") or ""))[:160],
        review_status=review_status,
    )


def sync_foundation_base(region_records: list[dict[str, Any]]) -> None:
    payload = load_payload(FOUNDATION_FILE)
    by_region = {record["npc_id"]: record for record in region_records}
    for profile in payload["records"]:
        region = by_region[profile["npc_id"]]
        native = region["native_place"]
        if native["is_external"]:
            status = "external_native_place"
        elif region["source_kind"] in {"legacy_text", "biography_explicit", "user_override"}:
            status = "linked_to_formal_region"
        else:
            status = "llm_inferred_pending_review"
        profile["native_place"] = OrderedDict(
            [
                ("province", native["province"]),
                ("prefecture", native["prefecture"]),
                ("region_link_status", status),
                ("source_field", "npc_region_links_seed.native_place"),
            ]
        )
    write_payload(FOUNDATION_FILE, payload)


def update_manifest(record_count: int) -> None:
    manifest = load_payload(MANIFEST_FILE)
    modules = manifest.setdefault("modules", [])
    existing = next((item for item in modules if item.get("filename") == "npc_region_links_seed.json"), None)
    if existing:
        existing["record_count"] = record_count
        existing["asset_status"] = "foundation_current"
        existing["foundation_layer"] = "1.2 籍贯桥接"
    else:
        modules.append(
            OrderedDict(
                [
                    ("module", "npc_region_links_seed"),
                    ("filename", "npc_region_links_seed.json"),
                    ("record_count", record_count),
                    ("asset_status", "foundation_current"),
                    ("foundation_layer", "1.2 籍贯桥接"),
                ]
            )
        )
    write_payload(MANIFEST_FILE, manifest)


def validate_output() -> int:
    core_ids = {record["npc_id"] for record in load_records(NPC_DIR, "npc_foundation_base_info_seed.json")}
    _, prefecture_lookup, _ = build_region_indexes()
    payload = load_payload(OUTPUT_FILE)
    records = payload.get("records") or []
    errors: list[str] = []
    ids = [record.get("npc_id") for record in records]
    missing = sorted(core_ids - set(ids))
    extra = sorted(set(ids) - core_ids)
    duplicates = [npc_id for npc_id in set(ids) if ids.count(npc_id) > 1]
    if missing:
        errors.append(f"missing NPC records: {missing[:8]}")
    if extra:
        errors.append(f"unknown NPC records: {extra[:8]}")
    if duplicates:
        errors.append(f"duplicate NPC records: {duplicates[:8]}")
    for record in records:
        errors.extend(validate_record(record, core_ids, prefecture_lookup))
    if errors:
        print("FAIL npc_region_links_seed validation")
        for index, error in enumerate(errors, 1):
            print(f"{index}. {error}")
        return 1
    print(f"PASS npc_region_links_seed validation ({len(records)} records)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--npc-id", action="append", help="Generate only this NPC id. Repeatable.")
    parser.add_argument("--limit", type=int, help="Generate only the first N selected NPCs.")
    parser.add_argument("--dry-run", action="store_true", help="Print selected NPC ids and do not write files.")
    parser.add_argument("--validate-only", action="store_true", help="Validate existing npc_region_links_seed.json.")
    parser.add_argument("--model", default=os.environ.get("DEEPSEEK_MODEL") or os.environ.get("CHONGZHEN_TEXT_API_MODEL") or DEFAULT_MODEL)
    parser.add_argument("--max-workers", type=int, default=4)
    args = parser.parse_args()

    if args.validate_only:
        return validate_output()

    _, prefecture_lookup, province_to_prefectures = build_region_indexes()
    profiles = load_records(NPC_DIR, "npc_foundation_base_info_seed.json")
    if args.npc_id:
        wanted = set(args.npc_id)
        profiles = [profile for profile in profiles if profile["npc_id"] in wanted]
    if args.limit is not None:
        profiles = profiles[: args.limit]
    selected_ids = [profile["npc_id"] for profile in profiles]
    if args.dry_run:
        print(json.dumps(selected_ids, ensure_ascii=False, indent=2))
        return 0

    api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("CHONGZHEN_TEXT_API_KEY")
    if not api_key:
        print("Set DEEPSEEK_API_KEY or CHONGZHEN_TEXT_API_KEY before generation.", file=sys.stderr)
        return 2

    output_payload = load_payload(OUTPUT_FILE)
    existing_records = output_payload.get("records") or []
    generated_by_id = {record["npc_id"]: record for record in existing_records}
    pending = [profile for profile in profiles if profile["npc_id"] not in generated_by_id]
    print(f"selected={len(profiles)} existing={len(generated_by_id)} pending={len(pending)} model={args.model}")

    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=max(1, args.max_workers)) as executor:
        futures = {
            executor.submit(
                generate_one,
                profile,
                api_key,
                args.model,
                prefecture_lookup,
                province_to_prefectures,
            ): profile
            for profile in pending
        }
        for future in as_completed(futures):
            profile = futures[future]
            try:
                record = future.result()
                generated_by_id[profile["npc_id"]] = record
                print(f"OK {profile['canonical_name']} -> {record['native_place']['province']} {record['native_place']['prefecture']}")
            except Exception as error:
                failures.append(f"{profile['npc_id']} {profile['canonical_name']}: {error}")
                print(f"FAIL {profile['canonical_name']}: {error}", file=sys.stderr)

    all_profiles = load_records(NPC_DIR, "npc_foundation_base_info_seed.json")
    ordered_records = [generated_by_id[profile["npc_id"]] for profile in all_profiles if profile["npc_id"] in generated_by_id]
    payload = OrderedDict(
        [
            ("schema_version", 1),
            ("generated_at", now_iso()),
            ("asset_status", "foundation_current"),
            ("module", "npc_region_links_seed"),
            ("display_name", "NPC 籍贯桥接"),
            ("scope", "Province/prefecture-level native-place links for NPC foundation data."),
            ("source_policy", "Legacy text is inherited when valid; otherwise DeepSeek infers credible fictional native places. External actors keep external native places."),
            ("records", ordered_records),
        ]
    )
    write_payload(OUTPUT_FILE, payload)
    if failures:
        print(
            f"wrote partial npc_region_links_seed.json ({len(ordered_records)}/{len(all_profiles)} records) "
            "before reporting failures",
            file=sys.stderr,
        )
        print("\nGeneration failed for some NPCs:", file=sys.stderr)
        for failure in failures[:30]:
            print(f"- {failure}", file=sys.stderr)
        return 1
    if len(ordered_records) != len(all_profiles):
        print(
            f"wrote partial npc_region_links_seed.json ({len(ordered_records)}/{len(all_profiles)} records); "
            "foundation base sync waits for full coverage"
        )
        return 0
    sync_foundation_base(ordered_records)
    update_manifest(len(ordered_records))
    return validate_output()


if __name__ == "__main__":
    raise SystemExit(main())
