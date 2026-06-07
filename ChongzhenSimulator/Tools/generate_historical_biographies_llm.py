#!/usr/bin/env python3
"""Generate isolated LLM-written pseudo-Ming historical biographies.

Each NPC is sent as a separate model request with only that NPC's fact packet.
No previous response id, prior biography draft, or neighboring NPC context is
sent, so a failed or florid entry cannot bleed into the next character.
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
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
NPC_DIR = ROOT / "ChongzhenSimulator" / "Resources" / "NPCDatabase"
OUTPUT_FILE = NPC_DIR / "npc_historical_biographies_seed.json"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEEPSEEK_CHAT_COMPLETIONS_URL = "https://api.deepseek.com/chat/completions"

BIOGRAPHY_STYLE = "ming_shiji_simulated"
BANNED_OUTPUT_TERMS = (
    "曾任或见载",
    "首版按",
    "史实细节仍需",
    "旧库",
    "开局",
    "游戏",
    "后续补录",
    "补录",
    "后台",
    "规则生成",
    "AI",
    "LLM",
    "prompt",
    "可用",
    "适合",
    "来源",
    "置信",
    "审校",
    "维基",
    "Wiki",
    "wikipedia",
    "http",
    "《明史",
    "明史/",
    "卷23",
    "卷24",
    "source",
    "review",
    "fact_sources",
    "anecdote_sources",
    "架空",
)

PROCESS_HINT_TERMS = (
    "曾任或见载",
    "首版按",
    "史实细节仍需",
    "旧库",
    "后续补录",
    "补录",
    "游戏开局补网",
    "不作史实断言",
    "架空",
    "据/",
    "据《",
    "》",
    "http",
    "Wiki",
    "维基",
)

STATUS_TEXT = {
    "active_in_office": "在任",
    "active_unassigned": "待命听用",
    "candidate": "候补待铨",
    "idle_home": "赋闲在籍",
    "dismissed": "罢黜中",
    "suspended": "停职听勘",
    "retired": "致仕归籍",
    "imprisoned": "下狱待讯",
    "exiled": "流放在外",
    "offstage": "未入局",
    "dead": "已故",
}

POWER_TEXT = {
    "ming": "明廷",
    "houjin": "后金",
    "bandits": "流寇群雄",
    "mongol": "蒙古诸部",
    "korea": "朝鲜",
}

IDENTITY_TEXT = {
    "outer_court_actor": "外朝臣工",
    "military_actor": "边镇武臣",
    "inner_eunuch": "内廷宦官",
    "harem_actor": "后妃女官",
    "nobility_actor": "宗室勋贵",
    "foreign_actor": "外部势力",
    "rebel_actor": "流寇首领",
    "unassigned_specialist": "方技异士",
}

SEX_TEXT = {
    "male": "男",
    "female": "女",
    "eunuch": "阉人",
}

TRAINING_TEXT = {
    "classical_bureaucracy": "经史与外朝案牍",
    "grand_secretariat_service": "内阁机务",
    "inner_court_training": "内廷承传",
    "palace_service": "宫禁服役",
    "military_service": "军旅行伍",
    "border_command": "边镇军务",
    "field_command": "战阵统兵",
    "local_administration": "地方治理",
    "financial_administration": "钱谷财政",
    "censorial_service": "言路科道",
    "literary_circle": "士林文章",
    "religious_network": "僧道方术",
    "craft_service": "工巧营造",
    "maritime_trade": "海贸船政",
    "rebel_network": "草泽亡命",
    "foreign_court_service": "外廷部帐",
    "harem_service": "宫闱内则",
}

SYSTEM_PROMPT = f"""你是一个为明末历史模拟游戏撰写人物列传的中文写作者。

每次任务只处理一个人物；不得延续、模仿或引用其他人物的输出。
写作口吻取法《史记》《明史》列传，又要有小说家笔力：像真实传记，不像资料卡。
你要让读者一眼看出此人的出身、欲望、胆气、局限、兴败之势。

必须遵守：
1. 只输出 JSON：{{"biography_text":"..."}}
2. biography_text 篇幅随人物轻重而定，名臣可稍详，小人物可简约，但必须像一段完整史传。
3. 要写成“传”：有来处、有行事、有性情、有兴败或史评；可以带一两笔传闻气、命运感和褒贬锋芒。
4. 不要逐字段翻译，不要按字段顺序堆信息。事实包只是骨架，你要化骨为人。
5. 可以合理润色、合并、戏说，但不得标注“杜撰”“考据”“来源”。
6. 不写现代视角，不写玩法说明，不写“开局、游戏、可用、适合、旧库、AI、LLM、prompt”等后台词。
7. 不要罗列字段，不要写小标题，不要写引号外的解释；不要写成“某某，某地人，某身份，某性格”的流水账。
8. 若事实薄弱，应写得含蓄可信，而不是说“未详”“待补”。
"""


def load_records(filename: str) -> list[dict[str, Any]]:
    with (NPC_DIR / filename).open(encoding="utf-8") as handle:
        return json.load(handle)["records"]


def load_payload(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def write_payload(path: Path, payload: dict[str, Any]) -> None:
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temp_path.replace(path)


def by_id(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {record["npc_id"]: record for record in records}


def clean_hint(text: str | None) -> str:
    if not text:
        return ""
    cleaned = text.replace("\n", " ").strip()
    for term in PROCESS_HINT_TERMS:
        cleaned = cleaned.replace(term, "")
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned.replace("，。", "。").replace("。。", "。")
    return cleaned.strip(" ，。；;")


def native_place_text(core: dict[str, Any]) -> str:
    native = core.get("native_place") or {}
    parts = [
        native.get("province") or "",
        native.get("prefecture") or "",
        native.get("county") or "",
    ]
    text = "".join(part for part in parts if part)
    return text or "不详"


def top_axis_labels(profile: dict[str, Any], reverse: bool = False) -> list[str]:
    axes = profile.get("axes") or []
    sorted_axes = sorted(
        axes,
        key=lambda axis: axis.get("value", 0),
        reverse=not reverse,
    )
    return [
        f"{axis.get('axis_name', '')}:{axis.get('label', '')}"
        for axis in sorted_axes[:3]
        if axis.get("axis_name") and axis.get("label")
    ]


def build_relationship_lookup(
    social_by_id: dict[str, dict[str, Any]],
    core_by_id: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, str]]]:
    lookup: dict[str, list[dict[str, str]]] = {}
    for npc_id, social in social_by_id.items():
        relationships: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for item in social.get("top_relationships") or []:
            other_id = item.get("other_npc_id")
            raw_type = item.get("raw_type") or item.get("relationship_kind") or ""
            if not other_id or (other_id, raw_type) in seen:
                continue
            seen.add((other_id, raw_type))
            other_name = core_by_id.get(other_id, {}).get("canonical_name")
            if not other_name:
                continue
            relationships.append(
                {
                    "name": other_name,
                    "relation": clean_hint(raw_type) or "关系",
                }
            )
            if len(relationships) >= 5:
                break
        lookup[npc_id] = relationships
    return lookup


def build_fact_packet(indexes: dict[str, Any], npc_id: str) -> dict[str, Any]:
    core = indexes["core"][npc_id]
    start = indexes["start"].get(npc_id, {})
    rank = indexes["rank"].get(npc_id, {})
    education = indexes["education"].get(npc_id, {})
    arc = indexes["arc"].get(npc_id, {})
    capability = indexes["capability"].get(npc_id, {})
    mingshu = indexes["mingshu"].get(npc_id, {})
    social = indexes["social"].get(npc_id, {})

    return {
        "npc_id": npc_id,
        "姓名": core.get("canonical_name"),
        "别名": core.get("aliases") or [],
        "性别": SEX_TEXT.get(core.get("sex_category"), core.get("sex_category")),
        "籍贯": native_place_text(core),
        "势力": POWER_TEXT.get(core.get("power_id"), core.get("power_id")),
        "身份": IDENTITY_TEXT.get(core.get("identity_type"), core.get("identity_type")),
        "崇祯元年处境": {
            "状态": STATUS_TEXT.get(start.get("start_status"), start.get("start_status")),
            "官职或称号": start.get("start_office_title") or rank.get("title_name") or "",
            "品秩": start.get("official_rank_code") or rank.get("official_rank_code") or "",
            "机构": start.get("environment_office_canonical_title") or "",
        },
        "出身教育": {
            "科名": education.get("exam_degree") or "",
            "科年": education.get("exam_year"),
            "训练路径": [
                TRAINING_TEXT.get(path, path)
                for path in education.get("training_paths") or []
            ],
            "事实标签": [
                fact.get("label")
                for fact in education.get("facts") or []
                if fact.get("label")
            ],
        },
        "能力事实": [
            item.get("label")
            for item in capability.get("capabilities") or []
            if item.get("label")
        ][:6],
        "命数": {
            "气质": mingshu.get("mingshu_archetype") or "",
            "显著高值": top_axis_labels(mingshu, reverse=False),
            "显著低值": top_axis_labels(mingshu, reverse=True),
        },
        "势网": {
            "角色": social.get("network_role") or "",
            "要人关系": indexes["relationships"].get(npc_id, []),
        }
    }


def build_user_prompt(
    fact_packet: dict[str, Any],
    previous_errors: list[str] | None = None,
    previous_text: str | None = None,
) -> str:
    parts = [
        "请只依据下面这个人物事实包，写一段拟明史列传。",
        "要写得像真传记：有文学性，有人物气味，有褒贬，有命运感。",
        "不要逐字段翻译；要把事实包化成史书正文。",
        "篇幅随人物轻重而定，宁可生动，不要为了短而干瘪。",
        "对资料薄弱处可合理补足经历气味，但不可写成现代说明。",
    ]
    if previous_errors:
        parts.append("上一次输出未通过校验，错误如下；请重写，不要引用失败文本：")
        parts.extend(f"- {error}" for error in previous_errors)
        if previous_text:
            parts.append("同一人物上一版草稿如下，仅用于判断增删方向；不得原样照抄，不得变短：")
            parts.append(previous_text)
        parts.append(
            "请只修正上述问题：若有后台词则改写避开，若句尾不完整则补成完整句。"
            "不要为了凑字数重复。"
        )
    parts.append("人物事实包：")
    parts.append(json.dumps(fact_packet, ensure_ascii=False, indent=2))
    return "\n".join(parts)


def extract_output_text(response: dict[str, Any]) -> str:
    if isinstance(response.get("output_text"), str):
        return response["output_text"].strip()

    chunks: list[str] = []
    for item in response.get("output") or []:
        for content in item.get("content") or []:
            if content.get("type") in {"output_text", "text"} and isinstance(content.get("text"), str):
                chunks.append(content["text"])
    return "".join(chunks).strip()


def parse_model_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    return json.loads(stripped)


def validate_biography_text(text: str) -> list[str]:
    errors: list[str] = []
    if not text:
        errors.append("biography_text 为空")

    found = [term for term in BANNED_OUTPUT_TERMS if term in text]
    if found:
        errors.append("含禁词：" + "、".join(found))
    if "\n" in text:
        errors.append("包含换行")
    if not text.endswith(("。", "也。", "焉。")):
        errors.append("结尾须为完整句")
    return errors


def normalize_biography_text(text: str) -> str:
    normalized = text.strip()
    normalized = re.sub(r"\s+", "", normalized)
    if normalized and not normalized.endswith(("。", "也。", "焉。")):
        normalized = normalized.rstrip("，；、：:;,.!！?？")
        if normalized:
            normalized += "。"
    return normalized


def call_openai_responses(
    *,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    timeout: int,
) -> str:
    payload = {
        "model": model,
        "input": [
            {
                "role": "system",
                "content": [{"type": "input_text", "text": system_prompt}],
            },
            {
                "role": "user",
                "content": [{"type": "input_text", "text": user_prompt}],
            },
        ],
        "temperature": temperature,
    }
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        OPENAI_RESPONSES_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        parsed = json.loads(response.read().decode("utf-8"))
    return extract_output_text(parsed)


def extract_chat_completion_text(response: dict[str, Any]) -> str:
    choices = response.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    return content.strip() if isinstance(content, str) else ""


def call_deepseek_chat_completions(
    *,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    timeout: int,
) -> str:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "thinking": {"type": "disabled"},
        "temperature": temperature,
        "max_tokens": 900,
        "response_format": {"type": "json_object"},
    }
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        DEEPSEEK_CHAT_COMPLETIONS_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        parsed = json.loads(response.read().decode("utf-8"))
    return extract_chat_completion_text(parsed)


def generate_one(
    *,
    provider: str,
    api_key: str,
    model: str,
    fact_packet: dict[str, Any],
    retries: int,
    temperature: float,
    timeout: int,
) -> str:
    previous_errors: list[str] | None = None
    previous_text: str | None = None
    for attempt in range(1, retries + 2):
        prompt = build_user_prompt(fact_packet, previous_errors, previous_text)
        try:
            if provider == "deepseek":
                raw_text = call_deepseek_chat_completions(
                    api_key=api_key,
                    model=model,
                    system_prompt=SYSTEM_PROMPT,
                    user_prompt=prompt,
                    temperature=temperature,
                    timeout=timeout,
                )
            else:
                raw_text = call_openai_responses(
                    api_key=api_key,
                    model=model,
                    system_prompt=SYSTEM_PROMPT,
                    user_prompt=prompt,
                    temperature=temperature,
                    timeout=timeout,
                )
            parsed = parse_model_json(raw_text)
            biography_text = normalize_biography_text(str(parsed.get("biography_text", "")))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as error:
            if attempt > retries:
                raise RuntimeError(f"model request failed: {error}") from error
            time.sleep(min(2 ** attempt, 20))
            previous_errors = [f"接口错误：{error}"]
            continue
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            biography_text = ""
            previous_errors = [f"输出不是合法 JSON：{error}"]

        errors = validate_biography_text(biography_text)
        if not errors:
            return biography_text
        previous_errors = errors
        if biography_text:
            previous_text = biography_text
        if attempt <= retries:
            time.sleep(0.5)

    npc_id = fact_packet.get("npc_id", "<unknown>")
    raise RuntimeError(f"{npc_id} failed validation after {retries + 1} attempt(s): {previous_errors}")


def build_indexes() -> dict[str, Any]:
    core_records = load_records("npc_core_seed.json")
    core_by_id = by_id(core_records)
    social_by_id = by_id(load_records("npc_social_capital_seed.json"))
    return {
        "ordered_ids": [record["npc_id"] for record in core_records],
        "core": core_by_id,
        "start": by_id(load_records("npc_start_1628_positions_seed.json")),
        "rank": by_id(load_records("npc_rank_titles_seed.json")),
        "education": by_id(load_records("npc_education_origin_seed.json")),
        "arc": by_id(load_records("npc_biography_arcs_seed.json")),
        "capability": by_id(load_records("npc_capability_facts_seed.json")),
        "mingshu": by_id(load_records("npc_mingshu_profiles_seed.json")),
        "social": social_by_id,
        "relationships": build_relationship_lookup(social_by_id, core_by_id),
    }


def choose_ids(indexes: dict[str, Any], args: argparse.Namespace) -> list[str]:
    ids = indexes["ordered_ids"]
    if args.npc_id:
        missing = sorted(set(args.npc_id) - set(ids))
        if missing:
            raise SystemExit(f"unknown npc_id: {', '.join(missing)}")
        ids = args.npc_id
    if args.limit is not None:
        ids = ids[: args.limit]
    return ids


def existing_valid_records(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    payload = load_payload(path)
    result: dict[str, dict[str, Any]] = {}
    for record in payload.get("records") or []:
        text = str(record.get("biography_text", "")).strip()
        if record.get("biography_style") == BIOGRAPHY_STYLE and not validate_biography_text(text):
            result[record["npc_id"]] = record
    return result


def validate_output_file(path: Path, ordered_ids: list[str]) -> int:
    payload = load_payload(path)
    records = payload.get("records") or []
    by_npc_id = {record.get("npc_id"): record for record in records}
    errors: list[str] = []

    missing = [npc_id for npc_id in ordered_ids if npc_id not in by_npc_id]
    extra = [npc_id for npc_id in by_npc_id if npc_id not in set(ordered_ids)]
    if missing:
        errors.append(f"missing records: {missing[:8]} ({len(missing)} total)")
    if extra:
        errors.append(f"unknown records: {extra[:8]} ({len(extra)} total)")
    if len(by_npc_id) != len(records):
        errors.append("duplicate npc_id values found")

    lengths: list[int] = []
    for npc_id in ordered_ids:
        record = by_npc_id.get(npc_id)
        if not record:
            continue
        forbidden_keys = {"fact_sources", "anecdote_sources", "confidence", "review_status"} & set(record)
        if forbidden_keys:
            errors.append(f"{npc_id}: forbidden runtime keys {sorted(forbidden_keys)}")
        if record.get("biography_style") != BIOGRAPHY_STYLE:
            errors.append(f"{npc_id}: biography_style must be {BIOGRAPHY_STYLE}")
        text = str(record.get("biography_text", "")).strip()
        text_errors = validate_biography_text(text)
        if text_errors:
            errors.extend(f"{npc_id}: {error}" for error in text_errors)
        length = len(text)
        if text:
            lengths.append(len(text))

    if lengths:
        print(
            "Biography lengths: "
            f"min={min(lengths)} max={max(lengths)} avg={sum(lengths) / len(lengths):.1f}"
        )
    if errors:
        print("\nFAIL biography validation")
        for index, error in enumerate(errors, 1):
            print(f"{index}. {error}")
        return 1

    print("\nPASS biography validation")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--provider",
        choices=("deepseek", "openai-responses"),
        default=os.environ.get("BIOGRAPHY_LLM_PROVIDER", "deepseek"),
    )
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL"))
    parser.add_argument("--npc-id", action="append", help="Generate only this NPC id. Repeatable.")
    parser.add_argument("--limit", type=int, help="Generate only the first N NPCs in npc_core order.")
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--temperature", type=float, default=0.82)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--dry-run", action="store_true", help="Print isolated prompt(s), do not call LLM.")
    parser.add_argument("--resume", action="store_true", help="Skip already valid generated records.")
    parser.add_argument("--validate-only", action="store_true", help="Validate output file and exit.")
    parser.add_argument("--output", type=Path, default=OUTPUT_FILE)
    args = parser.parse_args()

    indexes = build_indexes()
    ids = choose_ids(indexes, args)

    if args.validate_only:
        if args.npc_id or args.limit is not None:
            print("--validate-only always checks the full ordered NPC set.", file=sys.stderr)
        return validate_output_file(args.output, indexes["ordered_ids"])

    if args.dry_run:
        for npc_id in ids:
            packet = build_fact_packet(indexes, npc_id)
            print("=" * 80)
            print(build_user_prompt(packet))
        return 0

    if args.provider == "deepseek" and not args.model:
        args.model = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")

    api_key = os.environ.get("DEEPSEEK_API_KEY") if args.provider == "deepseek" else os.environ.get("OPENAI_API_KEY")
    if not api_key:
        env_name = "DEEPSEEK_API_KEY" if args.provider == "deepseek" else "OPENAI_API_KEY"
        print(f"{env_name} is required to generate biographies.", file=sys.stderr)
        return 2
    if not args.model:
        print("Set --model or OPENAI_MODEL/DEEPSEEK_MODEL for the LLM generation run.", file=sys.stderr)
        return 2

    payload = load_payload(args.output)
    existing_valid = existing_valid_records(args.output) if args.resume else {}
    generated: dict[str, dict[str, Any]] = {
        record["npc_id"]: record for record in payload.get("records") or []
    }

    for index, npc_id in enumerate(ids, 1):
        if npc_id in existing_valid:
            print(f"[{index}/{len(ids)}] skip valid {npc_id}")
            if index % 10 == 0:
                payload["records"] = [generated[npc_id] for npc_id in indexes["ordered_ids"]]
                write_payload(args.output, payload)
            continue
        packet = build_fact_packet(indexes, npc_id)
        biography_text = generate_one(
            provider=args.provider,
            api_key=api_key,
            model=args.model,
            fact_packet=packet,
            retries=args.retries,
            temperature=args.temperature,
            timeout=args.timeout,
        )
        generated[npc_id] = {
            "npc_id": npc_id,
            "biography_style": BIOGRAPHY_STYLE,
            "biography_text": biography_text,
        }
        print(f"[{index}/{len(ids)}] wrote {npc_id} {len(biography_text)} chars")
        if index % 10 == 0:
            payload["records"] = [generated[npc_id] for npc_id in indexes["ordered_ids"]]
            write_payload(args.output, payload)

    payload["records"] = [generated[npc_id] for npc_id in indexes["ordered_ids"]]
    write_payload(args.output, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
