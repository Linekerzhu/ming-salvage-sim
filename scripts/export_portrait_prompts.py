#!/usr/bin/env python3
"""Export NPC portrait prompts for batch image generation."""

from __future__ import annotations

import json
import argparse
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ming_sim.models import Character, GameState  # noqa: E402
from ming_sim.portraits import REFERENCE_ROOT, build_portrait_spec  # noqa: E402
from ming_sim.ranks import official_rank_for, rank_prompt_fragment  # noqa: E402

PORTRAIT_DIR = ROOT / "web" / "public" / "portraits"
DNA_DIR = PORTRAIT_DIR / "_dna"
MANIFEST_PATH = ROOT / "content" / "portrait_generation_manifest.json"
PROMPTS_PATH = ROOT / "docs" / "portrait_batch_prompts.md"
INITIAL_STATE = GameState(year=1627, period=10, turn=1)


def portable_reference(path: str | Path) -> str:
    raw = str(path)
    if not raw:
        return ""
    if raw.startswith(("data:image", "http://", "https://", "reference://")):
        return raw
    item = Path(raw)
    try:
        return item.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        pass
    try:
        return f"reference://{item.resolve().relative_to(REFERENCE_ROOT.resolve()).as_posix()}"
    except ValueError:
        return item.name


def portable_references(paths: Iterable[str | Path]) -> List[str]:
    return [ref for ref in (portable_reference(path) for path in paths) if ref]


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def visual_rank(character: Dict[str, Any]) -> str:
    office = str(character.get("office") or "")
    office_type = str(character.get("office_type") or "")
    faction = str(character.get("faction") or "")
    power_id = str(character.get("power_id") or "ming")
    rank = official_rank_for(office, office_type, power_id=power_id, faction=faction)
    if rank.category not in {"unranked", "unranked-office"}:
        return f"{rank.label} / {rank.color_rule} / {rank.buzi_rule}"
    if power_id == "houjin":
        return "后金八旗/外部君臣"
    if power_id == "mongol":
        return "蒙古汗廷/外部势力"
    if power_id == "korea":
        return "朝鲜王廷/外藩人物"
    if power_id == "bandits":
        return "流寇首领/乱世武装"
    if "皇后" in office:
        return "皇后级后宫主位"
    if "贵妃" in office:
        return "贵妃级后宫主位"
    if "伯" in office:
        return "勋爵武臣/伯爵级"
    if "首辅" in office or "大学士" in office or office_type == "内阁":
        return "阁臣级"
    if "尚书" in office:
        return "部堂正卿级"
    if "侍郎" in office:
        return "部院佐贰级"
    if "给事中" in office or "御史" in office or office_type == "都察院":
        return "科道言官级"
    if "主事" in office:
        return "六部司官级"
    if "太监" in office or office_type in {"司礼监", "东厂"}:
        return "内廷近侍/监军级"
    if office_type == "锦衣卫":
        return "厂卫武官级"
    if office_type == "边镇" or any(word in office for word in ("总兵", "副将", "将军", "督师")):
        return "边镇武臣级"
    if office_type == "地方":
        return "地方督抚/藩臬级"
    if office_type in {"待铨", "未仕"} and faction in {"中立", "西学"}:
        return "江湖外缘/待诏人物"
    return office_type or "身份待定"


def compact(text: str, limit: int = 90) -> str:
    clean = " ".join(str(text or "").split())
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


def character_from_record(record: Dict[str, Any]) -> Character:
    def as_int(key: str, fallback: int = 0) -> int:
        try:
            return int(record.get(key, fallback))
        except (TypeError, ValueError):
            return fallback

    def as_list(key: str) -> List[str]:
        value = record.get(key) or []
        if isinstance(value, list):
            return [str(item) for item in value if str(item).strip()]
        return [str(value)] if str(value).strip() else []

    return Character(
        name=str(record.get("name") or ""),
        office=str(record.get("office") or ""),
        office_type=str(record.get("office_type") or ""),
        faction=str(record.get("faction") or ""),
        aliases=as_list("aliases"),
        personal_skills=as_list("personal_skills"),
        loyalty=as_int("loyalty", 50),
        ability=as_int("ability", 50),
        integrity=as_int("integrity", 50),
        courage=as_int("courage", 50),
        style=str(record.get("style") or ""),
        power_id=str(record.get("power_id") or "ming"),
        location=str(record.get("location") or ""),
        birth_year=as_int("birth_year", 0),
        historical_death_year=as_int("historical_death_year", 0),
        historical_death_month=as_int("historical_death_month", 0),
        debut_year=as_int("debut_year", 0),
        debut_month=as_int("debut_month", 0),
        status=str(record.get("status") or "active"),
        summary=str(record.get("summary") or ""),
        portrait_id=str(record.get("portrait_id") or ""),
        force=as_int("force", 50),
        wisdom=as_int("wisdom", 50),
        charm=as_int("charm", 50),
        luck=as_int("luck", 50),
        cultivation=as_int("cultivation", 0),
        hp=as_int("hp", 100),
        max_hp=as_int("max_hp", 100),
        exp=as_int("exp", 0),
        level=as_int("level", 1),
        rank=str(record.get("rank") or ""),
        rank_grade=as_int("rank_grade", 0),
        rank_label=str(record.get("rank_label") or ""),
        rank_category=str(record.get("rank_category") or ""),
    )


def prompt_for(character: Dict[str, Any], biography: str, rank: str, filename: str) -> str:
    name = str(character.get("name") or "")
    office = str(character.get("office") or "")
    faction = str(character.get("faction") or "")
    power_id = str(character.get("power_id") or "ming")
    style = str(character.get("style") or "")
    skills = "、".join(character.get("personal_skills") or [])
    rank_text = rank_prompt_fragment(office, str(character.get("office_type") or ""), power_id, faction)
    return (
        "Late Ming dynasty political strategy game portrait, strict 2:3 vertical full-body standing cutout, "
        "aged paper background, cinnabar and muted gold accents, historically grounded Chinese clothing, "
        "painterly realism, clean face, readable silhouette, entire figure visible head-to-toe, no cropped head, hands, robe hem or boots, no text, no watermark. "
        f"Character: {name}. Rank/identity: {rank}. Office/identity: {office}. "
        f"{rank_text}. "
        f"Power: {power_id}. Faction: {faction}. "
        f"Temperament: {style}. Traits: {skills}. Biography: {compact(biography, 120)}. "
        f"Canvas 1024x1536, keep full standing figure centered with safe margins; export as PNG named {filename}."
    )


def iter_targets(mode: str = "missing") -> Iterable[Dict[str, Any]]:
    data = load_json(ROOT / "content" / "characters.json")
    network = load_json(ROOT / "content" / "npc_network.json").get("npcs", {})
    existing = {p.name for p in PORTRAIT_DIR.glob("*.png")}
    existing_dna = {p.name for p in DNA_DIR.glob("*.png")} if DNA_DIR.exists() else set()
    for character in data.get("characters", []):
        prefix = "consort_" if character.get("office_type") == "后宫" else "minister_"
        filename = f"{prefix}{character['name']}.png"
        dna_filename = f"dna_{character['name']}.png"
        portrait_id = str(character.get("portrait_id") or "")
        has_available_portrait = filename in existing or (bool(portrait_id) and f"{portrait_id}.png" in existing)
        has_available_dna = dna_filename in existing_dna
        if mode == "missing" and has_available_portrait and has_available_dna:
            continue
        biography = (
            network.get(character["name"], {}).get("biography")
            or character.get("summary")
            or f"{character.get('faction', '')}，{character.get('office_type') or character.get('office') or '身份待定'}人物。"
        )
        character_obj = character_from_record({**character, "summary": biography})
        spec = build_portrait_spec(character_obj, INITIAL_STATE, "release")
        inferred_rank = official_rank_for(
            str(character.get("office") or ""),
            str(character.get("office_type") or ""),
            power_id=str(character.get("power_id") or "ming"),
            faction=str(character.get("faction") or ""),
        )
        rank = visual_rank(character)
        yield {
            "name": character["name"],
            "filename": filename,
            "dna_filename": dna_filename,
            "dna_seed": spec.dna_seed,
            "dna_asset_id": spec.dna_asset_id,
            "asset_id": spec.asset_id,
            "wardrobe_key": spec.wardrobe_key,
            "wardrobe_label": spec.wardrobe_label,
            "rank_grade": inferred_rank.grade,
            "rank_category": inferred_rank.category,
            "rank_label": inferred_rank.label,
            "rank_summary": rank,
            "rank_costume_rule": rank_prompt_fragment(
                str(character.get("office") or ""),
                str(character.get("office_type") or ""),
                power_id=str(character.get("power_id") or "ming"),
                faction=str(character.get("faction") or ""),
            ),
            "reference_images": portable_references([DNA_DIR / dna_filename, *list(spec.reference_images)]),
            "clothing_reference_images": portable_references(spec.reference_images),
            "dna_reference_images": portable_references(spec.dna_reference_images),
            "office": character.get("office", ""),
            "office_type": character.get("office_type", ""),
            "faction": character.get("faction", ""),
            "style": character.get("style", ""),
            "biography": biography,
            "dna_prompt": spec.dna_prompt,
            "prompt": spec.prompt,
            "legacy_prompt": prompt_for(character, biography, rank, filename),
        }


def write_outputs(records: List[Dict[str, Any]]) -> None:
    MANIFEST_PATH.write_text(
        json.dumps({
            "image_spec": {
                "portrait": "strict 2:3 vertical full-body standing PNG cutout, minimum width 512, transparent background, no cropping",
                "dna_sheet": "3:4 PNG, 2x2 head-angle reference board",
            },
            "count": len(records),
            "portraits": records,
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# NPC 画像批量生成清单",
        "",
        "标准：立绘为固定 2:3 竖版 PNG，全身站像 head-to-toe，最小宽度 512，透明/抠图优先，严禁裁头、裁手、裁袍角、裁靴；DNA sheet 为 3:4 PNG，2x2 四视图头模参考。生成后按 filename 放入 `web/public/portraits/`；DNA 图按 dna_filename 归档到 `web/public/portraits/_dna/`。",
        "",
    ]
    for idx, rec in enumerate(records, 1):
        lines.extend([
            f"## {idx}. {rec['name']} -> `{rec['filename']}`",
            "",
            f"- DNA 种子：`{rec['dna_seed']}`",
            f"- DNA 文件：`{rec['dna_filename']}`",
            f"- 衣装 key：`{rec['wardrobe_key']}`（{rec['wardrobe_label']}）",
            f"- 品级/身份层级：{rec['rank_summary']}（grade={rec['rank_grade']}，category={rec['rank_category']}）",
            f"- 服制规则：{rec['rank_costume_rule']}",
            f"- 服装样板：{', '.join(rec.get('clothing_reference_images') or []) or '无'}",
            "- DNA角度参考：不传图片；按 DNA prompt 的四宫格文字规范生成",
            f"- 官职：{rec['office'] or rec['office_type']}",
            f"- 派系：{rec['faction']}",
            f"- 小传：{rec['biography']}",
            "",
            "### DNA 四视图 prompt",
            "",
            "```text",
            rec["dna_prompt"],
            "```",
            "",
            "### 当前立绘 prompt",
            "",
            "```text",
            rec["prompt"],
            "```",
            "",
        ])
    PROMPTS_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=["missing", "all"],
        default="missing",
        help="missing=只导出缺少专属立绘或 DNA 的人物；all=导出全体人物用于替换旧图",
    )
    args = parser.parse_args()
    records = list(iter_targets(args.mode))
    write_outputs(records)
    print(f"exported {len(records)} portrait prompts ({args.mode})")
    print(MANIFEST_PATH)
    print(PROMPTS_PATH)


if __name__ == "__main__":
    main()
