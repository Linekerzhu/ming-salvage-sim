#!/usr/bin/env python3
"""Generate runtime NPC personality and relationship assets from the master DB.

The game runtime deliberately keeps using content/*.json as its only static
source.  This script is a one-way compiler from the external design database
to those JSON assets; it never changes the game save schema.

The legacy npc_tiangang.json asset is intentionally not overwritten anymore:
conversation and directive behavior now use personality, relationships, memory,
and the handshake/agreement ledger.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MASTER = Path("/Users/zhujianzheng/Documents/大明王朝1628/master_data.sqlite")
CHARACTERS_JSON = ROOT / "content" / "characters.json"
NPC_NETWORK_JSON = ROOT / "content" / "npc_network.json"
NPC_TIANGANG_JSON = ROOT / "content" / "npc_tiangang.json"

POWER_ID = {
    "大明": "ming",
    "后金": "houjin",
    "蒙古": "mongol",
    "朝鲜": "korea",
    "流寇": "bandits",
}

EXTERNAL_FACTION = {
    "后金": "后金",
    "蒙古": "蒙古",
    "朝鲜": "朝鲜",
    "流寇": "流寇",
}

VALID_FACTIONS = {"阉党", "皇党", "军队", "东林", "宗室", "中立", "西学"}
OFFICE_TYPES = {"内阁", "吏部", "户部", "礼部", "兵部", "刑部", "工部", "司礼监", "东厂", "锦衣卫", "都察院", "翰林院", "地方", "边镇", "后宫", "待铨", "外臣"}
MINISTRIES = ("吏部", "户部", "礼部", "兵部", "刑部", "工部")
YANDANG_NAMES = {"魏忠贤", "崔呈秀", "王绍徽", "田尔耕", "许显纯", "王体乾", "黄立极", "施凤来", "来宗道", "张瑞图", "客印月"}
DONGLIN_NAMES = {"韩爌", "钱谦益", "钱龙锡", "李标", "徐光启", "刘宗周", "文震孟", "孙承宗", "倪元璐", "刘鸿训"}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def clamp(value: float, low: int = 0, high: int = 100) -> int:
    return max(low, min(high, int(round(value))))


def band_1_30(value: Any) -> int:
    try:
        raw = float(value or 1)
    except (TypeError, ValueError):
        raw = 1
    return max(1, min(5, int(round((raw - 1) / 29 * 4 + 1))))


def score_1_30(value: Any, floor: int = 25, ceiling: int = 92) -> int:
    try:
        raw = float(value or 1)
    except (TypeError, ValueError):
        raw = 1
    return clamp(floor + (raw - 1) / 29 * (ceiling - floor))


def short_text(text: str, limit: int) -> str:
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 1)].rstrip("，；。") + "…"


def first_sentence(text: str, limit: int = 120) -> str:
    clean = short_text(text, limit)
    parts = re.split(r"[。！？]", clean, maxsplit=1)
    return parts[0].strip(" ，；") or clean


def json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [value.strip()]
        return json_list(parsed)
    return []


def query_dicts(conn: sqlite3.Connection, sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, args).fetchall()]


def infer_office_type(office: str, power_label: str, sex_label: str) -> str:
    text = str(office or "")
    if power_label != "大明":
        return "外臣"
    if "无官职" in text or not text.strip():
        return "待铨"
    if re.search(r"内阁|大学士|首辅|次辅", text):
        return "内阁"
    for ministry in MINISTRIES:
        if ministry in text:
            return ministry
    if re.search(r"司礼监|御马监|御用监|内官监|尚膳监|太监|阉人", text) or sex_label == "阉人":
        return "司礼监"
    if "东厂" in text:
        return "东厂"
    if re.search(r"锦衣卫|镇抚司", text):
        return "锦衣卫"
    if re.search(r"都察院|御史|巡按|都御史", text):
        return "都察院"
    if re.search(r"翰林|詹事|庶吉士", text):
        return "翰林院"
    if re.search(r"总兵|副总兵|参将|游击|守备|监军|都指挥使|山海关|宁远|宣府|大同|延绥|甘肃|宁夏|京畿卫|御林军|左军|右军|中军|上军|下军", text):
        return "边镇"
    if re.search(r"布政使|按察使|参政|参议|知府|知县|地方", text):
        return "地方"
    return "待铨"


def status_from_profile(row: dict[str, Any]) -> str:
    existence = str(row.get("existence_label") or "")
    death_year = int(row.get("death_year") or 0)
    if existence == "死亡" or (death_year and death_year <= 1628):
        return "dead"
    if existence == "未登场":
        return "offstage"
    return "active"


def office_from_profile(row: dict[str, Any]) -> str:
    slot = str(row.get("current_slot_name") or "").strip()
    service = str(row.get("service_status_label") or "").strip()
    power = str(row.get("power_label") or "").strip()
    honor = str(row.get("honor_label") or "").strip()
    bio = str(row.get("biography") or "")
    if slot and slot != "无官职":
        return slot
    if power != "大明":
        return f"{power}人物"
    if honor:
        return f"{honor}，{service or '赋闲'}待召"
    former_titles = (
        "内阁首辅", "内阁次辅", "内阁大学士",
        "吏部尚书", "户部尚书", "礼部尚书", "兵部尚书", "刑部尚书", "工部尚书",
        "吏部左侍郎", "户部左侍郎", "礼部左侍郎", "兵部左侍郎", "刑部左侍郎", "工部左侍郎",
        "吏部右侍郎", "户部右侍郎", "礼部右侍郎", "兵部右侍郎", "刑部右侍郎", "工部右侍郎",
        "吏部侍郎", "户部侍郎", "礼部侍郎", "兵部侍郎", "刑部侍郎", "工部侍郎",
        "辽东巡抚", "蓟辽督师", "蓟辽总督", "总兵", "副总兵",
    )
    for title in former_titles:
        if title in bio:
            return f"前{title}，{service or '赋闲'}待召"
    ministry_post = re.search(r"(吏部|户部|礼部|兵部|刑部|工部).{0,8}(郎中|员外郎|主事|给事中)", bio)
    if ministry_post:
        return f"前{ministry_post.group(1)}{ministry_post.group(2)}，{service or '赋闲'}待召"
    if service in {"赋闲", "罢官", "归隐", "在野"}:
        return f"{service}待召"
    return "待铨"


def infer_faction(
    row: dict[str, Any],
    old_factions: dict[str, str],
    traits: list[dict[str, Any]],
    rels: list[dict[str, Any]],
) -> str:
    name = str(row["npc_name"])
    power = str(row.get("power_label") or "")
    office = str(row.get("current_slot_name") or "")
    bio = str(row.get("biography") or "")
    sex = str(row.get("sex_label") or "")
    trait_text = " ".join(str(t.get("trait_name") or "") for t in traits)
    if power != "大明":
        return EXTERNAL_FACTION.get(power, "中立")
    if name in old_factions and old_factions[name] in VALID_FACTIONS:
        return old_factions[name]
    if sex == "阉人" or re.search(r"司礼监|东厂|御马监|御用监|内官监|尚膳监", office) or "内廷通达" in trait_text:
        return "阉党"
    if re.search(r"总兵|副总兵|都指挥使|监军|边|辽|军", office):
        return "军队"
    if re.search(r"宗室|藩王|郡王|亲王", bio):
        return "宗室"
    if re.search(r"西学|传教|耶稣|历法|火器|格物", bio + trait_text):
        return "西学"
    if name in DONGLIN_NAMES or "东林清望" in trait_text or re.search(r"东林|清流|士林", bio):
        return "东林"
    if name in YANDANG_NAMES or re.search(r"魏阉|阉党|厂卫", bio):
        return "阉党"
    positive_party = [r for r in rels if r.get("type_code") == "DF" and int(r.get("energy") or 0) > 0]
    if any(str(r.get("other_npc_name")) in DONGLIN_NAMES for r in positive_party):
        return "东林"
    if any(str(r.get("other_npc_name")) in YANDANG_NAMES for r in positive_party):
        return "阉党"
    if re.search(r"御前|皇帝|奉旨|钦差", office + bio):
        return "皇党"
    return "中立"


def build_style(
    row: dict[str, Any],
    faction: str,
    traits: list[dict[str, Any]],
    rels: list[dict[str, Any]],
    prompt: dict[str, Any],
    old_style: str = "",
) -> str:
    name = str(row["npc_name"])
    persona = str(prompt.get("persona_fingerprint") or "").strip()
    base = ""
    if old_style and not old_style.startswith(("心术〔", "气性〔")):
        candidate = old_style.split("。", 1)[0].split("，", 1)[0].strip()
        if 2 <= len(candidate) <= 18 and "韬=" not in candidate:
            base = candidate
    if not base:
        base = {
            "阉党": "近权狠辣",
            "东林": "清议持重",
            "军队": "边事刚烈",
            "西学": "格物通达",
            "宗室": "贵胄自持",
            "皇党": "奉旨审势",
        }.get(faction, "审势自守")
    base = short_text(base, 18)
    strong = [t for t in traits if str(t.get("kind") or "") in {"擅", "绝艺"}][:3]
    flaws = [t for t in traits if str(t.get("kind") or "") == "痼"][:2]
    strong_line = "、".join(str(t.get("trait_name") or "") for t in strong) or "按职分办事"
    flaw_line = "、".join(str(t.get("trait_name") or "") for t in flaws) or "不肯轻易越界"
    ability_line = str(prompt.get("ability_prompt_line") or "")
    axis_labels = {"韬": "治军执行", "治": "经世行政", "识": "文章识见", "略": "谋略判断", "望": "声望动员"}
    strong_axes = []
    for axis, label in axis_labels.items():
        m = re.search(rf"{axis}=(\d+)", ability_line)
        if m and int(m.group(1)) >= 17:
            strong_axes.append(label)
    ability = f"能力底色偏{ '、'.join(strong_axes[:3]) }。" if strong_axes else ""
    party_line = {
        "阉党": "说话先探上意，办事喜抓把柄与密线；遇清流掣肘，会把小争执推成大案。",
        "东林": "说话重名分与公论，办事要章程可辩；遇阉党伸手，会先防清算再谈奉旨。",
        "军队": "说话看饷械、军心与战机，办事重兵权边报；空旨无粮时多半顶住不接。",
        "皇党": "说话围着皇权直控转，办事愿担急务；但要名分、人手和可复命的抓手。",
        "宗室": "说话顾祖制与宗藩体面，办事先护宗禄和田产边界。",
        "西学": "说话多从历算、火器、水利和实测入手，办事重证据胜过清议。",
    }.get(faction, "说话留余地，办事看风向、名分和代价。")
    ranked_rels = sorted(rels, key=lambda r: (abs(int(r.get("energy") or 0)), int(r.get("energy") or 0)), reverse=True)
    rel_bits = []
    for rel in ranked_rels[:3]:
        other = str(rel.get("other_npc_name") or "")
        rel_name = str(rel.get("name") or rel.get("type_code") or "关系")
        energy = int(rel.get("energy") or 0)
        if other:
            rel_bits.append(f"{'亲近' if energy > 0 else '提防'}{other}（{rel_name}）")
    rel_line = "；".join(rel_bits) if rel_bits else "人脉未显，更多凭本人职分行动"
    style = f"{base}。{party_line}擅长{strong_line}，短处是{flaw_line}。{ability}关键牵引：{rel_line}。"
    return short_text(style, 220)


def ability_skills(prompt: dict[str, Any], traits: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for kind in ("绝艺", "擅", "痼"):
        for trait in traits:
            if str(trait.get("kind") or "") == kind:
                name = str(trait.get("trait_name") or "").strip()
                if name and name not in names:
                    names.append(name)
    ability_line = str(prompt.get("ability_prompt_line") or "")
    for axis in ("韬", "治", "识", "略", "望"):
        m = re.search(rf"{axis}=(\d+)", ability_line)
        if m and int(m.group(1)) >= 18:
            label = {"韬": "治军执行", "治": "经世行政", "识": "文章识见", "略": "战略谋划", "望": "声望动员"}[axis]
            if label not in names:
                names.append(label)
    return (names + ["按职分办事"])[:5]


def relation_to_network(rel: dict[str, Any]) -> dict[str, str]:
    energy = int(rel.get("energy") or 0)
    confidence = "high" if abs(energy) >= 2 else "low"
    rel_name = str(rel.get("name") or rel.get("type_code") or "关系")
    note = str(rel.get("consequence") or rel.get("prompt_line") or "").strip()
    if not note:
        note = "此关系会影响信任、举荐、求情、阻挠和履约态度。"
    prefix = "党争敌对" if energy < 0 and abs(energy) >= 2 else rel_name
    return {
        "target": f"[[{rel.get('other_npc_name')}]]",
        "type": prefix,
        "note": short_text(note, 150),
        "confidence": confidence,
    }


def tiangang_values(row: dict[str, Any], faction: str, traits: list[dict[str, Any]], persona: dict[str, Any], ability: dict[str, Any]) -> dict[str, int]:
    trait_names = {str(t.get("trait_name") or "") for t in traits}
    sex = str(row.get("sex_label") or "")
    office = str(row.get("current_slot_name") or "")
    power = str(row.get("power_label") or "")
    js_tao = ability.get("js_tao", 10)
    js_zhi = ability.get("js_zhi", 10)
    js_shi = ability.get("js_shi", 10)
    js_lue = ability.get("js_lue", 10)
    js_wang = ability.get("js_wang", 10)
    is_inner = sex == "阉人" or faction == "阉党" or re.search(r"司礼监|东厂|监", office)
    is_military = faction == "军队" or re.search(r"总兵|副总兵|军|辽|边", office)
    values = {f"d{i:02d}": 3 for i in range(1, 37)}
    values.update(
        {
            "d01": clamp(3 + int(persona.get("xinshu_ba", 0)), 1, 5),
            "d02": 4 if faction in {"阉党", "皇党"} else 2 if faction == "东林" else 3,
            "d03": 5 if is_inner else 2 if faction == "东林" else 3,
            "d04": 5 if "天子耳目" in trait_names or is_inner else 2 if faction == "东林" else 3,
            "d05": clamp(3 - int(persona.get("xinshu_xu", 0)), 1, 5),
            "d06": 5 if "阳奉阴违" in trait_names else 4 if faction == "阉党" else 2 if faction in {"东林", "军队"} else 3,
            "d07": 5 if faction in {"阉党", "东林"} or "结党营私" in trait_names else 2 if faction == "中立" else 3,
            "d08": 4 if faction == "阉党" else 2 if faction == "东林" else 3,
            "d09": 4 if is_military else 2,
            "d10": 4 if is_inner else 2 if faction == "东林" else 3,
            "d11": clamp(3 - int(persona.get("xinshu_yi", 0)), 1, 5),
            "d12": 5 if any(x in trait_names for x in {"阳奉阴违", "善观风色", "神机妙算"}) else band_1_30(js_lue),
            "d13": 1 if "直言不讳" in trait_names else 4 if "阳奉阴违" in trait_names else 3,
            "d14": 4 if any(x in trait_names for x in {"暴戾恣睢", "审讯逼供"}) or is_inner else 2,
            "d15": 2 if faction in {"东林", "宗室"} else 3,
            "d16": 4 if power in {"后金", "蒙古"} else 2 if faction == "西学" else 3,
            "d17": 5 if is_military or power in {"后金", "蒙古"} else band_1_30(js_lue),
            "d18": 4 if power != "大明" else 3,
            "d19": 4 if faction == "西学" else 2 if faction == "东林" else 3,
            "d20": 2 if faction == "东林" else 3,
            "d21": band_1_30(js_zhi),
            "d22": 5 if "理财" in "".join(trait_names) or "户部" in office else band_1_30(js_zhi),
            "d23": band_1_30((float(js_zhi or 1) + float(js_shi or 1)) / 2),
            "d24": band_1_30(js_shi),
            "d25": band_1_30(js_lue),
            "d26": band_1_30(js_tao),
            "d27": max(2, band_1_30(js_tao)) if is_military else max(1, band_1_30(float(js_tao or 1) - 7)),
            "d28": band_1_30((float(js_tao or 1) + float(js_zhi or 1)) / 2),
            "d29": 5 if is_inner or "耳目遍布" in trait_names else band_1_30(js_lue),
            "d30": 5 if any(x in trait_names for x in {"阳奉阴违", "善观风色", "神机妙算"}) else band_1_30(js_lue),
            "d31": 5 if is_inner and "暴戾恣睢" in trait_names else 2,
            "d32": 5 if is_inner else 1,
            "d33": band_1_30(js_wang),
            "d34": max(band_1_30(js_lue), band_1_30(js_wang)),
            "d35": band_1_30(js_wang),
            "d36": 5 if faction == "西学" else band_1_30(js_shi),
        }
    )
    return {key: clamp(value, 1, 5) for key, value in values.items()}


def summary_from_values(dimensions: list[dict[str, Any]], values: dict[str, int], wanted: set[str]) -> str:
    parts = []
    for dim in dimensions:
        dim_id = str(dim.get("id") or "")
        if dim_id not in wanted:
            continue
        labels = dim.get("labels") if isinstance(dim.get("labels"), dict) else {}
        value = int(values.get(dim_id, 3))
        label = labels.get(str(value), f"{value}级")
        parts.append(f"{dim.get('symbol', '')}{dim.get('name')}偏“{label}”")
    return "，".join(parts)


def build_assets(master_db: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if not master_db.exists():
        raise SystemExit(f"master_data.sqlite 不存在：{master_db}")
    existing_characters = load_json(CHARACTERS_JSON)
    existing_network = load_json(NPC_NETWORK_JSON)
    existing_tiangang = load_json(NPC_TIANGANG_JSON)
    old_by_name = {str(item["name"]): item for item in existing_characters.get("characters", [])}
    old_factions = {name: str(item.get("faction") or "") for name, item in old_by_name.items()}

    conn = sqlite3.connect(master_db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    issues = query_dicts(conn, "SELECT * FROM v_data_quality_issues")
    if issues:
        preview = json.dumps(issues[:8], ensure_ascii=False)
        raise SystemExit(f"master_data.sqlite 数据质量检查未通过：{preview}")

    profiles = query_dicts(conn, "SELECT * FROM v_npc_runtime_profile ORDER BY npc_id")
    prompts = {r["npc_id"]: r for r in query_dicts(conn, "SELECT * FROM v_npc_prompt_context")}
    abilities = {r["npc_id"]: r for r in query_dicts(conn, "SELECT * FROM v_npc_ability_runtime")}
    personas = {r["npc_id"]: r for r in query_dicts(conn, "SELECT * FROM v_npc_persona_runtime")}
    traits_by_npc: dict[int, list[dict[str, Any]]] = {}
    for row in query_dicts(conn, "SELECT * FROM v_npc_trait_runtime ORDER BY npc_id, CASE kind WHEN '绝艺' THEN 1 WHEN '擅' THEN 2 WHEN '痼' THEN 3 ELSE 4 END, trait_code"):
        traits_by_npc.setdefault(int(row["npc_id"]), []).append(row)
    rels_by_npc: dict[int, list[dict[str, Any]]] = {}
    for row in query_dicts(conn, "SELECT * FROM v_npc_relation_perspective ORDER BY focal_npc_id, abs(energy) DESC, energy DESC, type_code, other_npc_id"):
        rels_by_npc.setdefault(int(row["focal_npc_id"]), []).append(row)

    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    characters: list[dict[str, Any]] = []
    network_npcs: dict[str, Any] = {}
    tiangang_npcs: dict[str, Any] = {}
    dimensions = existing_tiangang["meta"]["dimensions"]

    for row in profiles:
        npc_id = int(row["npc_id"])
        name = str(row["npc_name"])
        prompt = prompts.get(npc_id, {})
        ability = abilities.get(npc_id, {})
        persona = personas.get(npc_id, {})
        traits = traits_by_npc.get(npc_id, [])
        rels = rels_by_npc.get(npc_id, [])
        old = old_by_name.get(name, {})
        office = office_from_profile(row)
        power_label = str(row.get("power_label") or "大明")
        power_id = POWER_ID.get(power_label, "ming")
        sex_label = str(row.get("sex_label") or "")
        office_type = infer_office_type(office, power_label, sex_label)
        faction = infer_faction(row, old_factions, traits, rels)
        style = build_style(row, faction, traits, rels, prompt, str(old.get("style") or ""))
        skills = ability_skills(prompt, traits)
        birth_year = int(row.get("birth_year") or 0)
        death_year = int(row.get("death_year") or 0)
        js_tao = ability.get("js_tao", 10)
        js_zhi = ability.get("js_zhi", 10)
        js_shi = ability.get("js_shi", 10)
        js_lue = ability.get("js_lue", 10)
        js_wang = ability.get("js_wang", 10)
        force = score_1_30(js_tao, 25, 92)
        wisdom = score_1_30((float(js_zhi or 1) + float(js_shi or 1) + float(js_lue or 1)) / 3, 35, 94)
        charm = score_1_30(js_wang, 30, 90)
        core_ability = score_1_30((float(js_tao or 1) + float(js_zhi or 1) + float(js_lue or 1)) / 3, 35, 92)
        integrity = clamp(62 - int(persona.get("xinshu_ba", 0)) * 8 - (18 if any(t.get("trait_name") == "贪墨成性" for t in traits) else 0) + (8 if faction == "东林" else 0), 20, 95)
        courage = clamp(50 + int(persona.get("qixing_hao", 0)) * 9 + int(persona.get("qixing_zhi", 0)) * 6 + (10 if office_type == "边镇" else 0), 20, 95)
        loyalty = clamp(58 - int(persona.get("xinshu_ba", 0)) * 5 + (8 if faction in {"皇党", "军队"} else 0) - (8 if "阳奉阴违" in {t.get("trait_name") for t in traits} else 0), 20, 95)
        summary = short_text(str(row.get("biography") or prompt.get("biography") or f"{name}，{power_label}人物。"), 260)
        aliases = json_list(old.get("aliases")) if old else []
        if name not in aliases:
            aliases.insert(0, name)
        character = {
            "name": name,
            "office": office,
            "office_type": office_type if office_type in OFFICE_TYPES else "待铨",
            "faction": faction,
            "aliases": aliases[:8],
            "personal_skills": skills,
            "loyalty": loyalty,
            "ability": core_ability,
            "integrity": integrity,
            "courage": courage,
            "style": style,
            "birth_year": birth_year,
            "historical_death_year": death_year,
            "historical_death_month": int(old.get("historical_death_month") or 0) if old else 0,
            "debut_year": int(old.get("debut_year") or 0) if old else 0,
            "debut_month": int(old.get("debut_month") or 0) if old else 0,
            "status": status_from_profile(row),
            "summary": summary,
            "power_id": power_id,
            "location": str(row.get("residence_name") or row.get("native_place_name") or ""),
            "force": force,
            "wisdom": wisdom,
            "charm": charm,
            "luck": clamp(45 + int(persona.get("qixing_ji", 0)) * 7 + int(row.get("is_hero") or 0) * 8, 15, 95),
            "cultivation": int(old.get("cultivation") or 0) if old else 0,
            "hp": clamp(90 + force * 0.25 + courage * 0.15, 60, 140),
            "max_hp": clamp(90 + force * 0.25 + courage * 0.15, 60, 140),
            "exp": int(old.get("exp") or 0) if old else 0,
            "level": max(1, min(9, int(round((core_ability + wisdom + charm) / 55)))),
        }
        characters.append(character)

        relation_entries = [relation_to_network(rel) for rel in rels if rel.get("other_npc_name")]
        relationship_links = []
        for rel in relation_entries:
            target = rel["target"]
            if target not in relationship_links:
                relationship_links.append(target)
        positive = [t for t in traits if str(t.get("kind") or "") in {"擅", "绝艺"}][:4]
        negative = [t for t in traits if str(t.get("kind") or "") == "痼"][:3]
        positive_names = "、".join(str(t.get("trait_name")) for t in positive) or "本职经验"
        negative_names = "、".join(str(t.get("trait_name")) for t in negative) or "名分、资源或风险不足"
        enemy_count = sum(1 for rel in rels if int(rel.get("energy") or 0) < 0)
        network_npcs[name] = {
            "name": name,
            "obsidian_title": f"[[{name}]]",
            "tags": [f"#NPC/{name}", f"#派系/{faction}", f"#官署/{character['office_type']}", f"#势力/{power_id}"],
            "biography": summary,
            "identity": {
                "office": office,
                "office_type": character["office_type"],
                "faction": faction,
                "power_id": power_id,
                "aliases": aliases[:8],
            },
            "relationship_links": relationship_links[:16],
            "relations": relation_entries[:24],
            "ability_logic": short_text(
                f"能力轴：韬{js_tao}、治{js_zhi}、识{js_shi}、略{js_lue}、望{js_wang}。"
                f"强项来自{positive_names}；痼疾或风险在{negative_names}。"
                f"对话和办事要把这些转成口气、条件、拖延方式、承办边界和可动员人脉，不要裸报数值。",
                420,
            ),
            "growth_arc": {
                "seed": first_sentence(summary, 120),
                "rise": short_text(f"皇帝若顺着其{positive_names}给名分、资源和可复命目标，此人更可能积极背书或办成差事。", 160),
                "risk": short_text(f"若触犯其{negative_names}，或牵动{enemy_count}条敌对/党争关系，就可能拖延、护短、反咬或把小事推成党争。", 180),
            },
            "ai_hooks": [
                f"召对时先按“{style}”演绎，不要只给通用稳妥答案。",
                "举荐、求情、阻挠和履约时先看 relations 的亲疏与敌对能量。",
                "trait 是戏剧化行为抓手：强项会帮他办成事，痼疾会让旨意变形或反噬。",
            ],
        }

        values = tiangang_values(row, faction, traits, persona, ability)
        political = summary_from_values(dimensions, values, {f"d{i:02d}" for i in range(1, 21) if i in {1, 2, 3, 4, 6, 7, 9, 12, 13, 14}})
        professional = summary_from_values(dimensions, values, {f"d{i:02d}" for i in range(21, 37) if values.get(f"d{i:02d}", 3) >= 4})
        tiangang_npcs[name] = {
            "name": name,
            "hidden": True,
            "archetype": f"{faction}/{character['office_type']}",
            "values": values,
            "political_summary": political or "政治立场随名分、资源与关系网摆动。",
            "professional_summary": professional or "无特别突出的单项，但可按本职经验处理常务。",
            "behavior_rule": "遇事先按人格、trait、派系与关系网判断，再看皇威、名分、资源、承诺和风险是否足够。",
            "ai_use": "天罡值默认玩家不可见；AI只可把它转化为语气、立场、风险偏好、办事方式和冲突逻辑，不得向玩家逐项报数。",
        }

    characters_asset = {
        "factions": existing_characters["factions"],
        "characters": characters,
    }
    network_asset = {
        "meta": {
            "version": 2,
            "generated_at": generated_at,
            "source": str(master_db),
            "npc_count": len(network_npcs),
            "style": "Generated from normalized master_data.sqlite relation and trait views; links use [[人物名]].",
            "rules": [
                "relations 的 energy 已折算为 high/low confidence；亲疏会影响信任、举荐、求情、阻挠和履约，不是免费资源。",
                "trait 是戏剧化行为抓手：强项影响成事路径，痼疾影响拖延、变形、反噬和党争清算。",
                "growth_arc 只作叙事潜力与风险提示，当前版本不驱动天罡数值成长。",
            ],
            "previous_meta": existing_network.get("meta", {}),
        },
        "npcs": network_npcs,
    }
    tiangang_asset = {
        "meta": {
            **existing_tiangang["meta"],
            "version": 2,
            "source": f"master_data.sqlite compiled at {generated_at}",
            "npc_count": len(tiangang_npcs),
            "hidden_by_default": True,
            "growth_enabled": False,
        },
        "npcs": tiangang_npcs,
    }
    return characters_asset, network_asset, tiangang_asset


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--master-db", type=Path, default=DEFAULT_MASTER)
    parser.add_argument("--dry-run", action="store_true", help="build and validate in memory, but do not write files")
    args = parser.parse_args()
    characters, network, legacy_tiangang = build_assets(args.master_db)
    if args.dry_run:
        print(json.dumps({
            "characters": len(characters["characters"]),
            "network": len(network["npcs"]),
            "legacy_tiangang_loaded": len(legacy_tiangang["npcs"]),
        }, ensure_ascii=False))
        return
    write_json(CHARACTERS_JSON, characters)
    write_json(NPC_NETWORK_JSON, network)
    print(
        f"generated {len(characters['characters'])} NPCs from {args.master_db}; "
        f"preserved legacy {NPC_TIANGANG_JSON.relative_to(ROOT)}"
    )


if __name__ == "__main__":
    main()
