#!/usr/bin/env python3
"""Generate isolated NPC fate verses (命批) with DeepSeek V4 Pro."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
NPC_DIR = ROOT / "TiangangLab" / "Resources" / "NPCDatabase"
OUTPUT_FILE = NPC_DIR / "npc_mingpi_seed.json"
DEEPSEEK_CHAT_COMPLETIONS_URL = "https://api.deepseek.com/chat/completions"

FORM_LABELS = {
    "wuyan_jueju": "五言绝句",
    "qiyan_jueju": "七言绝句",
    "duilian": "对联",
    "songci": "宋词",
    "xiaoqu": "小曲",
}

CIPAI_ALLOWLIST = {
    "临江仙",
    "菩萨蛮",
    "浣溪沙",
    "鹧鸪天",
    "蝶恋花",
    "虞美人",
    "浪淘沙令",
}

XIAOQU_ALLOWLIST = {
    "山坡羊",
    "天净沙",
    "沉醉东风",
    "清江引",
    "水仙子",
}

GLOBAL_BANNED_TERMS = {
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

SYSTEM_PROMPT = """你是为明末历史模拟游戏写“命批”的中文诗人。

每次只处理一个人物；不得延续、模仿或引用其他人物的输出。
命批不是简介，而是警幻仙境判词一类的宿命谶语：再强、再智、再忠、再狠的人，终究抵不过世道洪流。
这是历史悲剧，不是私人伤春。可以哀艳，但不得把所有人写成卿卿我我的悲情小诗。

必须遵守：
1. 只输出 JSON 对象。
2. 你要从五言绝句、七言绝句、对联、宋词、小曲中为此人选择最合适的一体。
3. 正文必须高度象征、含蓄、悲凉，有红楼梦判词般的幻灭、因果、反讽与命运感。
4. 意象必须多元，随人物气质取象；可用草木、鸟兽、器物、宫苑、市井、兵火、舟车、海潮、山川、星历、织绣、药炉、锁钥、残盏、断弦、尘网、寒灰等，不要固定套用灯、雪、碑、梦、花、月。
5. 悲剧形态必须因人而异：飞蛾扑火、英勇献身、智尽身危、权焰反噬、清名成累、守成无功、红颜薄命、枭雄败亡、隐者零落、庸人随波、奸雄自缚、孤忠不售，各有不同声口。
6. 风格可以自由：可峭拔、哀艳、冷隽、慷慨、诡谲、荒寒、俚艳、庄严，但必须像判命，不像资料卡。
7. 必须有历史重量：制度、权势、兵火、民乱、党争、宫禁、边患、财赋、家国倾覆等大势可化作象征；不要只写个人幽怨、相思、春愁。
8. 不得写人物简介，不得复述生平，不得出现正式人名、别名、官名、地名、朝代、年号、组织名、具体事件名。
9. 五言、七言绝句必须是近体绝句感：四句，逐句五/七字，偶句押韵，起承转合。
10. 宋词必须填写词牌名，只能用：临江仙、菩萨蛮、浣溪沙、鹧鸪天、蝶恋花、虞美人、浪淘沙令。
11. 小曲必须填写曲牌名，只能用：山坡羊、天净沙、沉醉东风、清江引、水仙子。
12. 不得出现底层词：天罡、命数、心盘、NPC、游戏、开局、AI、LLM、prompt。

输出 JSON 形状：
{
  "form_id": "wuyan_jueju|qiyan_jueju|duilian|songci|xiaoqu",
  "form_label": "...",
  "cipai": "仅宋词填写，否则空字符串",
  "qupai": "仅小曲填写，否则空字符串",
  "title": "二至五字题名，不含实指信息",
  "lines": ["..."],
  "prosody_check": {
    "passed": true,
    "notes": "一句话说明格律与避实均已自检"
  }
}
"""

REVIEW_PROMPT = """你是命批审校官。请审查候选命批是否符合：
1. 像红楼梦警幻判词，有宿命、幻灭、时代洪流感。
2. 未出现任何人名、别名、官名、地名、朝代、年号、组织名、具体事件名。
3. 未出现天罡、命数、心盘、NPC、游戏、开局、AI、LLM、prompt 等底层词。
4. 五言/七言绝句行数和字数正确，偶句有押韵感；对联二行对仗；宋词有词牌名；小曲有曲牌名。
5. 通用古典意象、成语、典故、象征物不是实指词；如城、庭、碑、垒、海岳、铜驼、断楫、潮、灯、剑、舟、炉、弦、锁、盏等，只要未出现禁用清单中的专名或明确专有名词，不应判为违规。
6. 悲剧形态是否贴合此人，而非千人一面的“冷香残梦”。
7. 是否有历史悲剧的大势感，而非单纯个人伤春、相思、闺怨。
8. 不得用“可能暗示”“容易联想”“过于直白”作为失败理由；只有确切出现禁用词、硬专名、行数字数错误、体式错误、严重私情化时，才判失败。

只输出 JSON：
{"passed": true|false, "violations": ["..."], "notes": "..."}
"""


def load_records(filename: str) -> list[dict[str, Any]]:
    with (NPC_DIR / filename).open(encoding="utf-8") as handle:
        return json.load(handle)["records"]


def load_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": 1, "generated_at": "2026-06-06T22:01:01+00:00", "records": []}
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def write_payload(path: Path, payload: dict[str, Any]) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    tmp_path.replace(path)


def by_id(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {record["npc_id"]: record for record in records}


def strip_ws(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def line_len(line: str) -> int:
    return len(strip_ws(line))


def last_char(line: str) -> str:
    text = strip_ws(line).rstrip("。！？；，、")
    return text[-1] if text else ""


def call_deepseek(
    *,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    timeout: int,
    max_tokens: int,
) -> dict[str, Any]:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "thinking": {"type": "disabled"},
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    request = urllib.request.Request(
        DEEPSEEK_CHAT_COMPLETIONS_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        parsed = json.loads(response.read().decode("utf-8"))
    content = parsed["choices"][0]["message"]["content"]
    return json.loads(content)


def build_machine_review_notes(candidate: dict[str, Any]) -> str:
    form_id = candidate.get("form_id")
    form_label = FORM_LABELS.get(form_id, "命批")
    if form_id in {"wuyan_jueju", "qiyan_jueju"}:
        return f"{form_label}行数与字数已通过机器校验；避实词已通过硬校验。"
    if form_id == "duilian":
        return "对联行数与等长已通过机器校验；避实词已通过硬校验。"
    if form_id == "songci":
        return f"宋词词牌《{candidate.get('cipai')}》与行数已通过机器校验；避实词已通过硬校验。"
    if form_id == "xiaoqu":
        return f"小曲曲牌《{candidate.get('qupai')}》与行数已通过机器校验；避实词已通过硬校验。"
    return "体式与避实词已通过机器校验。"


def clean_title(value: Any) -> str:
    title = strip_ws(str(value or ""))
    title = title.strip("《》“”\"'")
    return title


def split_compact_verse_lines(lines: list[str], form_id: str) -> list[str]:
    """Split compact classical verse returned as one punctuated line."""
    if form_id == "duilian" and len(lines) == 1:
        pieces = [strip_ws(piece).strip("，。；、") for piece in re.split(r"[；。]+", lines[0])]
        pieces = [piece for piece in pieces if piece]
        if len(pieces) == 2:
            return pieces
        comma_pieces = [strip_ws(piece).strip("，。；、") for piece in re.split(r"[，,]+", lines[0])]
        comma_pieces = [piece for piece in comma_pieces if piece]
        if len(comma_pieces) == 2:
            return comma_pieces

    if form_id not in {"songci", "xiaoqu"}:
        return lines

    max_lines = 12 if form_id == "songci" else 10
    should_split = len(lines) < 3 or any(re.search(r"[，。；！？]", line) and line_len(line) > 14 for line in lines)
    if not should_split:
        return lines

    pieces: list[str] = []
    for line in lines:
        for piece in re.split(r"[，。；！？]+", line):
            cleaned = strip_ws(piece).strip("，。；、")
            cleaned = re.sub(r"^(上片|下片|首句|尾声|其一|其二)[:：]", "", cleaned)
            if cleaned:
                pieces.append(cleaned)
    if 3 <= len(pieces) <= max_lines:
        return pieces
    return lines


def normalize_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    form_id = str(candidate.get("form_id") or "").strip()
    form_label = FORM_LABELS.get(form_id, str(candidate.get("form_label") or "").strip())
    lines = candidate.get("lines") or []
    if isinstance(lines, str):
        lines = [line for line in re.split(r"[\\n/]+", lines) if line.strip()]
    lines = [strip_ws(str(line)).strip("，。；、") for line in lines if strip_ws(str(line))]
    lines = split_compact_verse_lines(lines, form_id)

    cipai = strip_ws(str(candidate.get("cipai") or ""))
    qupai = strip_ws(str(candidate.get("qupai") or ""))
    if form_id != "songci":
        cipai = ""
    if form_id != "xiaoqu":
        qupai = ""

    return {
        "form_id": form_id,
        "form_label": form_label,
        "cipai": cipai,
        "qupai": qupai,
        "title": clean_title(candidate.get("title")),
        "lines": lines,
        "prosody_check": {
            "passed": True,
            "notes": "待机器校验。",
        },
    }


def collect_forbidden_terms(indexes: dict[str, Any], npc_id: str, *, include_all_names: bool = True) -> set[str]:
    terms = set(GLOBAL_BANNED_TERMS)
    current = indexes["core"][npc_id]
    terms.add(current.get("canonical_name") or "")
    terms.update(current.get("aliases") or [])
    if include_all_names:
        for record in indexes["core"].values():
            terms.add(record.get("canonical_name") or "")
            terms.update(record.get("aliases") or [])
    start = indexes["start"].get(npc_id, {})
    rank = indexes["rank"].get(npc_id, {})
    native = current.get("native_place") or {}
    for value in [
        start.get("start_office_title"),
        start.get("environment_office_canonical_title"),
        rank.get("title_name"),
        native.get("province"),
        native.get("prefecture"),
        native.get("county"),
    ]:
        if value:
            terms.add(str(value))

    for item in indexes["relationships"].get(npc_id, []):
        if item.get("name"):
            terms.add(item["name"])
    return {strip_ws(term) for term in terms if len(strip_ws(term)) >= 2}


def validate_candidate(candidate: dict[str, Any], forbidden_terms: set[str]) -> list[str]:
    errors: list[str] = []
    form_id = candidate.get("form_id")
    lines = candidate.get("lines") or []
    joined = "".join(lines) + candidate.get("title", "")

    if form_id not in FORM_LABELS:
        errors.append(f"form_id invalid: {form_id!r}")
    if candidate.get("form_label") != FORM_LABELS.get(form_id):
        errors.append("form_label does not match form_id")
    if not candidate.get("title"):
        errors.append("title is empty")
    if not lines:
        errors.append("lines are empty")

    found = sorted(term for term in forbidden_terms if term and term in joined)
    if found:
        errors.append("contains forbidden direct terms: " + "、".join(found[:12]))

    if form_id in {"wuyan_jueju", "qiyan_jueju"}:
        expected = 5 if form_id == "wuyan_jueju" else 7
        if len(lines) != 4:
            errors.append(f"{FORM_LABELS[form_id]} must have 4 lines")
        for index, line in enumerate(lines, 1):
            if line_len(line) != expected:
                errors.append(f"line {index} must have {expected} characters")
        if len(lines) >= 4 and (not last_char(lines[1]) or not last_char(lines[3])):
            errors.append("even line rhyme endings are missing")
    elif form_id == "duilian":
        if len(lines) != 2:
            errors.append("duilian must have 2 lines")
        elif line_len(lines[0]) != line_len(lines[1]):
            errors.append("duilian lines must have equal character count")
    elif form_id == "songci":
        if candidate.get("cipai") not in CIPAI_ALLOWLIST:
            errors.append("songci must use allowed cipai")
        if not (3 <= len(lines) <= 12):
            errors.append("songci must have 3-12 lines")
    elif form_id == "xiaoqu":
        if candidate.get("qupai") not in XIAOQU_ALLOWLIST:
            errors.append("xiaoqu must use allowed qupai")
        if not (3 <= len(lines) <= 10):
            errors.append("xiaoqu must have 3-10 lines")

    check = candidate.get("prosody_check") or {}
    if check.get("passed") is not True:
        errors.append("prosody_check.passed must be true")
    if not check.get("notes"):
        errors.append("prosody_check.notes is empty")
    return errors


def top_tiangang(values: list[dict[str, Any]], limit: int = 6) -> list[str]:
    ranked = sorted(
        values,
        key=lambda item: (abs(int(item.get("value", 3)) - 3), item.get("dimension_id", "")),
        reverse=True,
    )
    return [
        f"{item.get('dimension_name')}:{item.get('label')}"
        for item in ranked[:limit]
        if item.get("dimension_name") and item.get("label")
    ]


def top_mingshu(profile: dict[str, Any], reverse: bool = False, limit: int = 4) -> list[str]:
    axes = profile.get("axes") or []
    ranked = sorted(axes, key=lambda item: item.get("value", 0), reverse=not reverse)
    return [
        f"{item.get('axis_name')}:{item.get('label')}"
        for item in ranked[:limit]
        if item.get("axis_name") and item.get("label")
    ]


def build_relationship_lookup(social_by_id: dict[str, dict[str, Any]], core_by_id: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, str]]]:
    result: dict[str, list[dict[str, str]]] = {}
    for npc_id, social in social_by_id.items():
        rows: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in social.get("top_relationships") or []:
            other_id = item.get("other_npc_id")
            other = core_by_id.get(other_id or "")
            if not other or other_id in seen:
                continue
            seen.add(other_id)
            rows.append({"name": other["canonical_name"], "relation": item.get("raw_type") or item.get("relationship_kind") or ""})
            if len(rows) >= 4:
                break
        result[npc_id] = rows
    return result


def build_fact_packet(indexes: dict[str, Any], npc_id: str) -> dict[str, Any]:
    core = indexes["core"][npc_id]
    start = indexes["start"].get(npc_id, {})
    rank = indexes["rank"].get(npc_id, {})
    tiangang = indexes["tiangang"].get(npc_id, {})
    mingshu = indexes["mingshu"].get(npc_id, {})
    xinpan = indexes["xinpan"].get(npc_id, {})
    capability = indexes["capability"].get(npc_id, {})
    social = indexes["social"].get(npc_id, {})
    biography = indexes["biography"].get(npc_id, {})
    return {
        "人物事实只供抽象，不得在命批中直写": {
            "姓名": core.get("canonical_name"),
            "别名": core.get("aliases") or [],
            "身份": core.get("identity_type"),
            "处境": start.get("start_status"),
            "官职称号": start.get("start_office_title") or rank.get("title_name") or "",
        },
        "列传": biography.get("biography_text") or "",
        "能力事实": [item.get("label") for item in capability.get("capabilities") or [] if item.get("label")][:6],
        "天罡显著特征": top_tiangang(tiangang.get("values") or []),
        "命数气质": {
            "类型": mingshu.get("mingshu_archetype") or "",
            "高值": top_mingshu(mingshu, reverse=False),
            "低值": top_mingshu(mingshu, reverse=True),
        },
        "心盘": xinpan.get("initial_state") or {},
        "势网": {
            "角色": social.get("network_role") or "",
            "关系": indexes["relationships"].get(npc_id, []),
        },
        "创作要求": "请把上述事实高度抽象为宿命命批；正文中不得出现任何事实包中的正式名词。",
    }


def build_user_prompt(fact_packet: dict[str, Any], forbidden_terms: set[str], previous_errors: list[str] | None = None, previous_candidate: dict[str, Any] | None = None) -> str:
    lines = [
        "请为这个人物生成一则“命批”。",
        "它要像警幻仙境中的判词：以多元意象暗寓人生兴败，而不是套用同一组花月灯雪。",
        "请按人物气质取象：文臣可取简册、砚尘、庭树；武人可取铁衣、寒角、折戟；宫闱可取帘影、香篆、织纹；商海可取潮汐、算盘、沉舟；江湖可取断弦、药炉、旧伞；外部势力可取苍鹰、冻河、毳帐等。",
        "请先在心中判断此人的悲剧形态，再落笔：飞蛾扑火、英勇献身、智尽身危、权焰反噬、清名成累、守成无功、红颜薄命、枭雄败亡、隐者零落、庸人随波、奸雄自缚、孤忠不售，都应有不同声口。",
        "风格可以自由：慷慨者可沉雄，诡谲者可冷峭，宫闱者可哀艳，武人可苍凉，奸雄可反讽，隐者可空寂。",
        "这是历史悲剧，不是私人恋怨；要让制度倾轧、兵火倾覆、权势反噬、财尽民困、宫禁幽深、边尘动荡这些大势以象征方式压在诗中。",
        "关键气质：再强大也抵不过世道洪流；忠者未必得救，智者未必自全，权势终为尘土，英雄亦入劫灰。",
        "正文不能出现禁用实指词；只能用象征意象暗示。",
        "禁用实指词如下，输出正文和题名都不得出现：",
        "、".join(sorted(forbidden_terms)),
    ]
    if previous_errors:
        lines.append("上一版未通过，必须修正这些问题：")
        lines.extend(f"- {error}" for error in previous_errors)
        lines.append("若上一版因体式行数或字数不合规，请改用更能保证合规的体式；合规高于炫技。")
        if previous_candidate:
            lines.append("同一人物上一版候选，仅供修正，不得照抄：")
            lines.append(json.dumps(previous_candidate, ensure_ascii=False))
    lines.append("人物事实包：")
    lines.append(json.dumps(fact_packet, ensure_ascii=False, indent=2))
    return "\n".join(lines)


def build_review_prompt(fact_packet: dict[str, Any], candidate: dict[str, Any], forbidden_terms: set[str]) -> str:
    return "\n".join(
        [
            "请审校下列命批。必须严格避实，不得因为诗意而放过实指词。",
            "也不得因普通古典意象“可能联想到”人物经历而误判；命批本来就要隐喻人物命运。",
            "失败项必须引用确切违规词或确切格律错误。",
            "禁用实指词：",
            "、".join(sorted(forbidden_terms)),
            "人物事实包：",
            json.dumps(fact_packet, ensure_ascii=False, indent=2),
            "候选命批：",
            json.dumps(candidate, ensure_ascii=False, indent=2),
        ]
    )


def generate_one(
    *,
    api_key: str,
    model: str,
    npc_id: str,
    indexes: dict[str, Any],
    retries: int,
    timeout: int,
    temperature: float,
) -> dict[str, Any]:
    fact_packet = build_fact_packet(indexes, npc_id)
    prompt_forbidden_terms = collect_forbidden_terms(indexes, npc_id, include_all_names=False)
    validation_forbidden_terms = collect_forbidden_terms(indexes, npc_id, include_all_names=True)
    previous_errors: list[str] | None = None
    previous_candidate: dict[str, Any] | None = None
    for attempt in range(1, retries + 2):
        try:
            candidate = call_deepseek(
                api_key=api_key,
                model=model,
                system_prompt=SYSTEM_PROMPT,
                user_prompt=build_user_prompt(fact_packet, prompt_forbidden_terms, previous_errors, previous_candidate),
                temperature=temperature,
                timeout=timeout,
                max_tokens=900,
            )
        except Exception as exc:  # noqa: BLE001 - malformed API responses are retriable per NPC.
            previous_errors = [f"API candidate JSON failed: {exc}"]
            if attempt <= retries:
                time.sleep(0.8)
                continue
            break
        normalized = normalize_candidate(candidate)
        machine_errors = validate_candidate(normalized, validation_forbidden_terms)
        if not machine_errors:
            normalized["prosody_check"] = {
                "passed": True,
                "notes": build_machine_review_notes(normalized),
            }
            return {
                "npc_id": npc_id,
                "profile_version": 1,
                "display_name": "命批",
                **normalized,
            }
        previous_errors = machine_errors
        previous_candidate = normalized
        if attempt <= retries:
            time.sleep(0.5)
    raise RuntimeError(f"{npc_id} failed mingpi generation: {previous_errors}")


def build_indexes() -> dict[str, Any]:
    core_records = load_records("npc_core_seed.json")
    core_by_id = by_id(core_records)
    social_by_id = by_id(load_records("npc_social_capital_seed.json"))
    return {
        "ordered_ids": [record["npc_id"] for record in core_records],
        "core": core_by_id,
        "start": by_id(load_records("npc_start_1628_positions_seed.json")),
        "rank": by_id(load_records("npc_rank_titles_seed.json")),
        "biography": by_id(load_records("npc_historical_biographies_seed.json")),
        "tiangang": by_id(load_records("npc_tiangang_profiles_seed.json")),
        "mingshu": by_id(load_records("npc_mingshu_profiles_seed.json")),
        "xinpan": by_id(load_records("npc_xinpan_seed.json")),
        "capability": by_id(load_records("npc_capability_facts_seed.json")),
        "social": social_by_id,
        "relationships": build_relationship_lookup(social_by_id, core_by_id),
    }


def existing_valid_records(path: Path, indexes: dict[str, Any]) -> dict[str, dict[str, Any]]:
    payload = load_payload(path)
    result: dict[str, dict[str, Any]] = {}
    for record in payload.get("records") or []:
        npc_id = record.get("npc_id")
        if not npc_id or npc_id not in indexes["core"]:
            continue
        forbidden = collect_forbidden_terms(indexes, npc_id)
        if not validate_candidate(record, forbidden):
            result[npc_id] = record
    return result


def validate_output_file(path: Path, indexes: dict[str, Any]) -> int:
    payload = load_payload(path)
    records = payload.get("records") or []
    by_npc_id = {record.get("npc_id"): record for record in records}
    ordered_ids = indexes["ordered_ids"]
    errors: list[str] = []
    missing = [npc_id for npc_id in ordered_ids if npc_id not in by_npc_id]
    extra = [npc_id for npc_id in by_npc_id if npc_id not in set(ordered_ids)]
    if missing:
        errors.append(f"missing {len(missing)} records, e.g. {missing[:8]}")
    if extra:
        errors.append(f"unknown {len(extra)} records, e.g. {extra[:8]}")
    if len(by_npc_id) != len(records):
        errors.append("duplicate npc_id values")

    form_counts: dict[str, int] = {}
    for npc_id in ordered_ids:
        record = by_npc_id.get(npc_id)
        if not record:
            continue
        form_counts[record.get("form_id", "")] = form_counts.get(record.get("form_id", ""), 0) + 1
        for error in validate_candidate(record, collect_forbidden_terms(indexes, npc_id)):
            errors.append(f"{npc_id}: {error}")
    print("Mingpi form counts:")
    for form_id, count in sorted(form_counts.items()):
        print(f"  {form_id:14s} {count}")
    if errors:
        print("\nFAIL mingpi validation")
        for index, error in enumerate(errors, 1):
            print(f"{index}. {error}")
        return 1
    print("\nPASS mingpi validation")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro"))
    parser.add_argument("--npc-id", action="append")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--temperature", type=float, default=0.95)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output", type=Path, default=OUTPUT_FILE)
    args = parser.parse_args()

    indexes = build_indexes()
    ids = indexes["ordered_ids"]
    if args.npc_id:
        missing = sorted(set(args.npc_id) - set(ids))
        if missing:
            print(f"unknown npc_id(s): {missing}", file=sys.stderr)
            return 2
        ids = args.npc_id
    if args.limit is not None:
        ids = ids[: args.limit]

    if args.validate_only:
        return validate_output_file(args.output, indexes)
    if args.dry_run:
        for npc_id in ids:
            forbidden = collect_forbidden_terms(indexes, npc_id, include_all_names=False)
            packet = build_fact_packet(indexes, npc_id)
            print("=" * 80)
            print(build_user_prompt(packet, forbidden))
        return 0

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print("DEEPSEEK_API_KEY is required.", file=sys.stderr)
        return 2

    payload = load_payload(args.output)
    generated = {record["npc_id"]: record for record in payload.get("records") or [] if record.get("npc_id")}
    existing_valid = existing_valid_records(args.output, indexes) if args.resume else {}

    def flush_generated() -> None:
        payload["records"] = [generated[ordered_id] for ordered_id in indexes["ordered_ids"] if ordered_id in generated]
        write_payload(args.output, payload)

    if args.concurrency <= 1:
        for index, npc_id in enumerate(ids, 1):
            if npc_id in existing_valid:
                print(f"[{index}/{len(ids)}] skip valid {npc_id}", flush=True)
                continue
            record = generate_one(
                api_key=api_key,
                model=args.model,
                npc_id=npc_id,
                indexes=indexes,
                retries=args.retries,
                timeout=args.timeout,
                temperature=args.temperature,
            )
            generated[npc_id] = record
            print(f"[{index}/{len(ids)}] wrote {npc_id} {record['form_label']} {len(''.join(record['lines']))} chars", flush=True)
            flush_generated()
        flush_generated()
        return 0

    work_items: list[tuple[int, str]] = []
    for index, npc_id in enumerate(ids, 1):
        if npc_id in existing_valid:
            print(f"[{index}/{len(ids)}] skip valid {npc_id}", flush=True)
            continue
        work_items.append((index, npc_id))

    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as executor:
        futures = {
            executor.submit(
                generate_one,
                api_key=api_key,
                model=args.model,
                npc_id=npc_id,
                indexes=indexes,
                retries=args.retries,
                timeout=args.timeout,
                temperature=args.temperature,
            ): (index, npc_id)
            for index, npc_id in work_items
        }
        for future in as_completed(futures):
            index, npc_id = futures[future]
            try:
                record = future.result()
            except Exception as exc:  # noqa: BLE001 - batch generation should report every failed NPC.
                message = f"[{index}/{len(ids)}] failed {npc_id}: {exc}"
                print(message, flush=True)
                errors.append(message)
                continue
            generated[npc_id] = record
            print(f"[{index}/{len(ids)}] wrote {npc_id} {record['form_label']} {len(''.join(record['lines']))} chars", flush=True)
            flush_generated()

    flush_generated()
    if errors:
        print("\nGeneration completed with failures:")
        for error in errors:
            print(error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
