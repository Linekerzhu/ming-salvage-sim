"""上下文生成与文本匹配：历史锚点、胜负判定、地区/军队/事件模糊匹配、
人物/事件上下文串、给 LLM 的 state_context。L4。

通过 bind_content() 注入 GameContent（过渡期）。
"""

from __future__ import annotations

import json
import re
from typing import Dict, List, Optional, Tuple

from ming_sim.constants import ECONOMY_ACCOUNTS, TURN_UNIT
from ming_sim.assets import format_money, format_money_delta
from ming_sim.content import GameContent
from ming_sim.db import GameDB
from ming_sim.exceptions import LLMContractError
from ming_sim.models import Army, Character, Event, GameState, Region
from ming_sim.skills import available_skill_names, office_skills

_content: Optional[GameContent] = None
GAME_START_YEAR = 1627
_reverse_relation_index_cache: Dict[int, Dict[str, List[Tuple[str, Dict[str, object]]]]] = {}
_minister_name_terms_cache: Dict[Tuple[int, int], Tuple[str, ...]] = {}


def bind_content(content: GameContent) -> None:
    global _content
    _content = content
    _reverse_relation_index_cache.clear()
    _minister_name_terms_cache.clear()


def _ctx() -> GameContent:
    if _content is None:
        raise RuntimeError("context.bind_content() 未调用：GameContent 未注入。")
    return _content


def historical_anchor_for_month(year: int, month: int) -> Dict[str, object]:
    """给 LLM 的历史护栏：关键历史事变必须出现，但玩家可改变走向和结果。"""
    anchors = {
        (1626, 9): "努尔哈赤已死于宁远败后不久，后金内部围绕汗位重排，皇太极取得主动。",
        (1626, 10): "皇太极继后金汗位，改元天聪；此事在游戏开局前已成定局，不可改写为尚未登基。",
        (1627, 1): "丁卯之役：后金攻朝鲜，朝鲜被迫与后金缔结兄弟之盟，但仍暗中倾明。",
        (1629, 10): "己巳之变历史窗口开启：皇太极可能绕道蒙古、蓟镇入塞，威胁遵化、京师。",
        (1629, 11): "己巳之变最危险阶段：若蓟镇、宣大、京营、关宁勤王失措，后金兵锋可逼近北京城下。",
        (1630, 1): "己巳之变余波：辽东督师、京畿防务与勤王军功过会引发朝廷追责。",
        (1632, 5): "皇太极西征林丹汗及察哈尔体系的历史压力上升，蒙古各部可能倒向后金。",
        (1635, 4): "察哈尔衰败后，后金收编蒙古部众、获得传国玉玺一类政治资源的窗口临近。",
        (1636, 4): "皇太极历史上会改国号为大清、称帝；若后金仍强盛且未被明军压制，应发生称帝建制。",
        (1637, 1): "丙子之役后朝鲜可能彻底臣服清；若明朝未能牵制辽东，朝鲜倾明空间会急剧缩小。",
        (1642, 3): "松锦决战历史压力：若关宁、锦州、宁远供给和士气长期恶化，辽东主力可能遭毁灭性打击。",
    }
    note = anchors.get((year, month), "")
    return {
        "date": f"{year}年{month}月",
        "note": note or f"本{TURN_UNIT}无硬性历史锚点，但势力仍需按其利益自行推进。",
        "must_respect": bool(note),
    }


# 结局类型枚举（CLI/Web/总结 agent 共用）。
# - ongoing：未决
# - capital_fallen：京师失守（beizhili 易主非 ming）——数值型，本函数判
# - emperor_abdicate / emperor_suicide：崇祯退位/自尽——叙事型，由 extractor 抽 emperor_fate 后
#   写入 applied["victory_status"]，不在本函数判
# - timeout：20 年到期（turn>=240）强制收尾——由 decree 结局收口判，不在本函数判
ENDING_ONGOING = "ongoing"
ENDING_CAPITAL_FALLEN = "capital_fallen"
ENDING_EMPEROR_ABDICATE = "emperor_abdicate"
ENDING_EMPEROR_SUICIDE = "emperor_suicide"
ENDING_TIMEOUT = "timeout"

# 五态结局的定调文案（前端弹窗标题/CLI 打印用）。ongoing 不入此表。
ENDING_LABELS: Dict[str, str] = {
    ENDING_CAPITAL_FALLEN: "京师陷落",
    ENDING_EMPEROR_ABDICATE: "崇祯逊位",
    ENDING_EMPEROR_SUICIDE: "崇祯殉国",
    ENDING_TIMEOUT: "二十载尘埃落定",
}


def victory_status(db: GameDB, state: GameState) -> Dict[str, object]:
    """结局判定（数值型部分）：本函数只判「京师失守」。

    退位/自尽走 extractor 的 emperor_fate（叙事型，见 issues.apply_score_extraction），
    20 年到期走 decree 结局收口（turn>=240），均不在此判。其余一律 ongoing。
    京畿 = beizhili，控制权字段 controlled_by（FK powers）；非 'ming' 即京师失守。
    """
    beizhili = db.conn.execute("SELECT * FROM regions WHERE id = 'beizhili'").fetchone()
    if beizhili is not None and str(beizhili["controlled_by"]) != "ming":
        holder_id = str(beizhili["controlled_by"])
        holder = db.conn.execute(
            "SELECT name FROM powers WHERE id = ?", (holder_id,)
        ).fetchone()
        holder_name = str(holder["name"]) if holder else holder_id
        return {
            "status": ENDING_CAPITAL_FALLEN,
            "summary": f"京师失守，{holder_name}入主北京，社稷倾覆，大明失其神器。",
        }
    return {"status": ENDING_ONGOING, "summary": "局势未决，社稷尚在崇祯一念之间。"}


# 地区/军队名称匹配实现在 matching.py；此处提供绑定 GameContent 的便捷封装。
from ming_sim.matching import army_aliases, compact_name, region_aliases  # noqa: E402,F401
from ming_sim.matching import match_army_id_from_text as _match_army
from ming_sim.matching import match_region_id_from_text as _match_region


def match_region_id_from_text(text: str) -> Optional[str]:
    return _match_region(text, _ctx().regions)


def match_army_id_from_text(text: str) -> Optional[str]:
    return _match_army(text, _ctx().armies)


def state_context(state: GameState) -> str:
    parts = []
    for key, value in state.metrics.items():
        if key in ECONOMY_ACCOUNTS:
            parts.append(f"{key}{format_money(value)}")
        else:
            parts.append(f"{key}{value}")
    return "，".join(parts)


def parse_json_dict(raw: str) -> Dict[str, int]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        raise LLMContractError(f"数据库中的数值变化 JSON 已损坏：{raw[:200]}") from error
    if not isinstance(data, dict):
        raise LLMContractError(f"数据库中的数值变化不是 object：{raw[:200]}")
    parsed: Dict[str, int] = {}
    for key, value in data.items():
        try:
            parsed[str(key)] = int(value)
        except (TypeError, ValueError) as error:
            raise LLMContractError(f"数据库中的数值变化字段不是整数：{key}={value}") from error
    return parsed


def format_metric_delta(delta: Dict[str, int]) -> str:
    if not delta:
        return "核心数值无明显变化"
    parts = []
    for key, value in delta.items():
        if key in ECONOMY_ACCOUNTS:
            parts.append(f"{key}{format_money_delta(value)}")
        else:
            sign = "+" if value > 0 else ""
            parts.append(f"{key}{sign}{value}")
    return "数值变化：" + "；".join(parts)


def _score_band_text(value: int, *, high_word: str = "强", low_word: str = "弱") -> str:
    try:
        score = int(value)
    except (TypeError, ValueError):
        score = 50
    if score >= 85:
        return f"极{high_word}"
    if score >= 70:
        return f"偏{high_word}"
    if score >= 52:
        return "中平"
    if score >= 36:
        return f"偏{low_word}"
    return f"极{low_word}"


def character_age(character: Character, year: Optional[int] = None) -> int:
    """Return approximate age by Gregorian year; 0 means unknown."""
    try:
        birth_year = int(character.birth_year or 0)
    except (TypeError, ValueError):
        birth_year = 0
    if birth_year <= 0:
        return 0
    current_year = int(year or GAME_START_YEAR)
    return max(1, current_year - birth_year)


def character_age_label(character: Character, year: Optional[int] = None, *, compact: bool = False) -> str:
    """LLM-facing age label. Birth months are unavailable, so wording stays approximate."""
    age = character_age(character, year)
    if not age:
        return "年龄未详" if compact else "开局年龄未详"
    if compact:
        return f"{age}岁"
    prefix = "当前年龄" if year and int(year) != GAME_START_YEAR else "开局年龄"
    return f"{prefix}约{age}岁（{int(character.birth_year)}年生）"


def _character_core_profile(character: Character) -> str:
    """给 Agent 的非数值化人物底色，避免裸分数被复述给玩家。"""
    traits = [
        f"忠诚{_score_band_text(character.loyalty)}",
        f"办事{_score_band_text(character.ability)}",
        f"清望{_score_band_text(character.integrity)}",
        f"胆略{_score_band_text(character.courage)}",
    ]
    return "人物底色：" + "、".join(traits) + "（底层分数隐藏，只可转化为语气、立场和执行判断）"


def character_context(character: Character, year: Optional[int] = None) -> str:
    return (
        f"{character.name}，{character.office}，职位类型：{character.office_type}，派系：{character.faction}，"
        f"{character_age_label(character, year)}，"
        f"别名：{', '.join(character.aliases) or '无'}，"
        f"人物标签：{', '.join(character.personal_skills)}，"
        f"职位技能：{', '.join(office_skills(character.office_type))}，"
        f"{_character_core_profile(character)}，行事底色：{character.style}"
    )


def character_context_with_db(character: Character, db: GameDB, year: Optional[int] = None) -> str:
    return character_context(character, year) + f"，当前可用技能：{available_skill_names(character, db)}"


def _derived_network_brief(name: str, max_relations: int = 8, year: Optional[int] = None) -> str:
    """运行时新增人物的人脉/叙事推定卡，供 Agent 使用。"""
    name = str(name or "").strip()
    character = _ctx().characters.get(name)
    if character is None:
        return ""
    profile = _derived_network_profile(character, db=None, limit=max_relations)
    growth = profile.get("growth_arc") if isinstance(profile.get("growth_arc"), dict) else {}
    recommendations = profile.get("recommendations") if isinstance(profile.get("recommendations"), list) else []
    relation_lines: List[str] = []
    for raw in recommendations[:max(1, max_relations)]:
        if not isinstance(raw, dict):
            continue
        target = str(raw.get("name") or "").strip()
        if not target:
            continue
        label = str(raw.get("office_type") or raw.get("faction") or "同局").strip()
        evidence = "；".join(
            str(item).strip()
            for item in (raw.get("evidence") or [])[:2]
            if str(item).strip()
        )
        suffix = f"。{evidence}" if evidence else ""
        relation_lines.append(f"- 可联络：[[{target}]]（{label}）{suffix}")

    parts = [
        f"【人物网络卡（运行时推定）：[[{name}]]】",
        f"年龄：{character_age_label(character, year)}",
        f"小传：{str(profile.get('biography') or '').strip()}",
    ]
    if relation_lines:
        parts.append("可用人脉：\n" + "\n".join(relation_lines))
    ability_logic = str(profile.get("ability_logic") or "").strip()
    if ability_logic:
        parts.append("能力构成：" + ability_logic)
    if growth:
        seed = str(growth.get("seed") or "").strip()
        rise = str(growth.get("rise") or "").strip()
        risk = str(growth.get("risk") or "").strip()
        parts.append(f"叙事潜力：起点：{seed}；可能走向：{rise}；风险：{risk}")
    return "\n".join(part for part in parts if part.strip())


def npc_network_brief(name: str, max_relations: int = 8, year: Optional[int] = None) -> str:
    """人物小传/关系/叙事潜力卡，供 Agent 理解人脉与能力来源。

    关系文本使用 Obsidian 式 [[人物名]] 双链；confidence=low 只作弱关系参考。
    """
    name = str(name or "").strip()
    entry = _ctx().npc_network.get(name)
    if not entry:
        return _derived_network_brief(name, max_relations=max_relations, year=year)
    character = _ctx().characters.get(name)
    overlay = _identity_conversion_overlay(character) if character else {}
    relations_raw = entry.get("relations") or []
    relations: List[str] = []
    if isinstance(relations_raw, list):
        for raw in relations_raw[:max(1, max_relations)]:
            if not isinstance(raw, dict):
                continue
            target = str(raw.get("target") or "").strip()
            rel_type = str(raw.get("type") or "关系").strip()
            note = str(raw.get("note") or "").strip()
            confidence = str(raw.get("confidence") or "").strip()
            suffix = "（弱关系）" if confidence == "low" else ""
            if target:
                relations.append(f"- {rel_type}{suffix}：{target}。{note}")
    growth = entry.get("growth_arc") if isinstance(entry.get("growth_arc"), dict) else {}
    ability_logic = _visible_ability_logic(entry.get("ability_logic"))
    hooks = entry.get("ai_hooks") if isinstance(entry.get("ai_hooks"), list) else []
    hook_line = "；".join(str(item).strip() for item in hooks[:3] if str(item).strip())
    biography = _append_public_note(str(entry.get("biography") or "").strip(), overlay.get("biography", ""))
    ability_logic = _append_public_note(ability_logic, overlay.get("ability_logic", ""))
    parts = [
        f"【人物网络卡：[[{name}]]】",
        f"年龄：{character_age_label(character, year) if character else '开局年龄未详'}",
        f"小传：{biography}",
    ]
    if relations:
        parts.append("人脉：\n" + "\n".join(relations))
    if ability_logic:
        parts.append("能力构成：" + ability_logic)
    if growth:
        seed = str(growth.get("seed") or "").strip()
        rise = _append_public_note(str(growth.get("rise") or "").strip(), overlay.get("rise", ""))
        risk = _append_public_note(str(growth.get("risk") or "").strip(), overlay.get("risk", ""))
        parts.append(f"叙事潜力：起点：{seed}；可能走向：{rise}；风险：{risk}")
    if hook_line:
        parts.append("AI驱动提示：" + hook_line)
    return "\n".join(part for part in parts if part.strip())


def _tiangang_dimension_map() -> Dict[str, Dict[str, object]]:
    meta = _ctx().npc_tiangang.get("meta") if isinstance(_ctx().npc_tiangang, dict) else {}
    dims = meta.get("dimensions") if isinstance(meta, dict) else []
    if not isinstance(dims, list):
        return {}
    return {
        str(dim.get("id")): dim
        for dim in dims
        if isinstance(dim, dict) and dim.get("id")
    }


def _tiangang_entry(name: str) -> Dict[str, object]:
    data = _ctx().npc_tiangang
    npcs = data.get("npcs") if isinstance(data, dict) else {}
    if not isinstance(npcs, dict):
        return {}
    entry = npcs.get(str(name or "").strip())
    return entry if isinstance(entry, dict) else {}


def _is_current_inner_court(character: Character) -> bool:
    identity = f"{character.office or ''} {character.office_type or ''} {character.faction or ''} {character.style or ''}"
    return bool(
        character.office_type in {"司礼监", "东厂", "内廷"}
        or character.faction in {"内廷"}
        or re.search(r"太监|宦官|内官|小火者|司礼监|东厂|内廷|宫禁", identity)
    )


def _identity_conversion_overlay(character: Character) -> Dict[str, str]:
    """人物小传的当前身份叠加层；旧小传仍作出身与旧关系。"""
    style = str(character.style or "")
    if not _is_current_inner_court(character):
        return {}
    if not re.search(r"既入内廷|奉强旨入内廷|净身入宫|强旨净身|自愿净身", style):
        return {}
    forced = bool(re.search(r"强旨|心结", style))
    mode = "奉强旨净身入内廷" if forced else "奏对同意后净身入内廷"
    office = character.office or "司礼监差使"
    return {
        "biography": (
            f"当前身份：{character.name}已{mode}，现任{office}；"
            "旧小传仍说明其出身、旧人脉和性情惯性，但御前身份已转入皇帝私人执行链。"
        ),
        "ability_logic": (
            "当前能力构成需叠加保密复命、内廷传旨、宫禁行走与皇命执行；"
            "其旧官署经验可转化为识别外朝程序和阻力的本钱。"
        ),
        "rise": (
            "若皇帝给出明旨、保密边界和复命路径，可沿内廷执行链形成新的办事线。"
        ),
        "risk": (
            "旧同年、同乡、清流或官署关系可能转为疑忌；"
            + ("强旨净身还会留下羞辱心结与外朝反弹。" if forced else "自愿入内廷也会引发外朝名分压力。")
        ),
    }


def _append_public_note(base: str, note: str) -> str:
    base = _clean_obsidian_text(base).strip()
    note = _clean_obsidian_text(note).strip()
    if not base:
        return note
    if not note or note in base:
        return base
    return f"{base.rstrip('。；')}。{note}"


def _clamp_tiangang(value: int) -> int:
    return max(1, min(5, int(value)))


def _score_to_tiangang(score: float) -> int:
    if score >= 86:
        return 5
    if score >= 70:
        return 4
    if score >= 52:
        return 3
    if score >= 36:
        return 2
    return 1


def _boost(base: int, amount: int, condition: bool = True) -> int:
    return _clamp_tiangang(base + amount) if condition else _clamp_tiangang(base)


def _derived_tiangang_entry(character: Character) -> Dict[str, object]:
    """Derive a full hidden 36D entry for runtime-created characters."""
    office = character.office or ""
    office_type = character.office_type or "待铨"
    faction = character.faction or "中立"
    text = f"{office} {office_type} {faction} {character.style}"
    is_eunuch = office_type in {"司礼监", "东厂", "内廷"} or re.search(r"太监|宦官|内官|司礼监|东厂", text)
    is_inner_network = bool(is_eunuch or faction in {"阉党", "内廷", "皇党"})
    is_clear = faction in {"清流", "东林", "东林党"}
    is_military = bool(office_type in {"边镇", "锦衣卫"} or re.search(r"总兵|副将|游击|参将|督师|经略|军|兵|将", text))
    is_fiscal = bool(office_type == "户部" or re.search(r"户部|商|税|海关|盐|钱粮|内库|国库", text))
    is_letters = bool(office_type in {"翰林院", "礼部", "内阁"} or re.search(r"翰林|编修|詹事|文章|礼部|大学士", text))
    is_law = bool(office_type in {"刑部", "都察院", "锦衣卫"} or re.search(r"刑|御史|都察院|镇抚|审|狱", text))
    is_jianghu = bool(office_type in {"待铨", "未仕"} and re.search(r"江湖|武当|少林|龙虎|山庄|游侠|神医|掌教|法师|侠女|异闻", text))

    loyalty = int(getattr(character, "loyalty", 50) or 50)
    ability = int(getattr(character, "ability", 50) or 50)
    integrity = int(getattr(character, "integrity", 50) or 50)
    courage = int(getattr(character, "courage", 50) or 50)
    force = int(getattr(character, "force", 50) or 50)
    wisdom = int(getattr(character, "wisdom", ability) or ability)
    charm = int(getattr(character, "charm", 50) or 50)
    luck = int(getattr(character, "luck", 50) or 50)
    cultivation = int(getattr(character, "cultivation", 0) or 0)

    admin = _score_to_tiangang((ability + wisdom + integrity) / 3)
    finance = _score_to_tiangang((ability + wisdom) / 2)
    law = _score_to_tiangang((ability + integrity + courage) / 3)
    letters = _score_to_tiangang((wisdom + charm + integrity) / 3)
    strategy = _score_to_tiangang((wisdom + courage + ability) / 3)
    tactics = _score_to_tiangang((force + courage + ability) / 3)
    martial = _score_to_tiangang((force + courage + max(cultivation, 40) * 0.6) / 2.6)
    logistics = _score_to_tiangang((ability + wisdom + loyalty) / 3)
    spycraft = _score_to_tiangang((wisdom + luck + courage) / 3)
    plotting = _score_to_tiangang((wisdom + ability + (100 - integrity)) / 3)
    interrogation = _score_to_tiangang((force + courage + ability) / 3)
    inner_ops = _score_to_tiangang((loyalty + ability + wisdom) / 3)
    persuasion = _score_to_tiangang((charm + wisdom) / 2)
    judgment = _score_to_tiangang((wisdom + ability + luck) / 3)
    leadership = _score_to_tiangang((charm + courage + loyalty) / 3)
    craft = _score_to_tiangang(max(wisdom, luck, cultivation))

    values: Dict[str, int] = {f"d{i:02d}": 3 for i in range(1, 37)}
    values.update({
        "d01": 2 if is_clear else 4 if is_inner_network and integrity < 70 else 3,
        "d02": 5 if is_eunuch and loyalty >= 86 else 4 if is_inner_network or loyalty >= 76 else 2 if is_clear else 3,
        "d03": 5 if is_eunuch else 4 if is_inner_network else 1 if is_clear and integrity >= 70 else 2 if is_clear else 3,
        "d04": 5 if is_eunuch or office_type == "东厂" else 4 if is_inner_network or office_type == "锦衣卫" else 1 if is_clear and integrity >= 72 else 2 if is_clear else 3,
        "d05": 2 if max(ability, wisdom) >= 70 else 4 if max(ability, wisdom) <= 42 else 3,
        "d06": 5 if loyalty <= 42 else 4 if faction == "阉党" and loyalty < 76 else 3 if loyalty >= 70 or is_eunuch else 2 if is_clear else 3,
        "d07": 4 if faction in {"阉党", "东林", "东林党"} or charm >= 72 else 2 if faction in {"中立", "皇党"} else 3,
        "d08": 4 if faction == "阉党" else 2 if faction in {"皇党", "清流", "东林", "东林党"} else 3,
        "d09": 4 if is_military else 1 if is_letters and not is_military else 2 if office_type not in {"待铨", "未仕"} else 3,
        "d10": 5 if is_eunuch else 4 if is_inner_network else 1 if is_clear and integrity >= 70 else 2 if is_clear else 3,
        "d11": 1 if integrity >= 82 else 2 if integrity >= 68 else 4 if integrity <= 45 else 5 if integrity <= 32 else 3,
        "d12": 1 if integrity >= 82 and not is_inner_network else 2 if integrity >= 68 else 4 if wisdom >= 68 or integrity <= 45 else 3,
        "d13": 1 if is_clear and integrity >= 70 else 2 if is_clear else 4 if is_inner_network else 3,
        "d14": 4 if is_law or is_inner_network and courage >= 60 else 2 if integrity >= 72 else 3,
        "d15": 1 if is_clear and integrity >= 72 else 4 if integrity <= 45 or is_jianghu else 3,
        "d16": 4 if is_military and "辽" in text else 2 if faction == "西学" else 3,
        "d17": 1 if is_military and courage >= 75 else 2 if is_military else 4 if courage <= 42 else 3,
        "d18": 5 if cultivation >= 70 else 4 if is_jianghu or luck >= 78 else 2 if wisdom >= 75 else 3,
        "d19": 1 if is_fiscal and re.search(r"商|海|关|税", text) else 2 if is_fiscal else 4 if is_clear else 3,
        "d20": 1 if integrity >= 84 else 2 if integrity >= 68 else 4 if integrity <= 45 else 3,
        "d21": _boost(admin, 1, office_type in {"内阁", "吏部", "地方"}),
        "d22": _boost(finance, 1, is_fiscal),
        "d23": _boost(law, 1, is_law),
        "d24": _boost(letters, 1, is_letters),
        "d25": _boost(strategy, 1, is_military),
        "d26": _boost(tactics, 1, is_military),
        "d27": _boost(martial, 1, cultivation >= 60 or is_jianghu),
        "d28": _boost(logistics, 1, is_military or is_fiscal),
        "d29": _boost(spycraft, 1, office_type in {"锦衣卫", "东厂", "司礼监"}),
        "d30": _boost(plotting, 1, is_inner_network or faction == "阉党"),
        "d31": _boost(_clamp_tiangang(interrogation - (0 if office_type in {"锦衣卫", "东厂", "刑部"} else 1)), 1, office_type in {"锦衣卫", "东厂"}),
        "d32": _boost(_clamp_tiangang(inner_ops if (is_eunuch or is_inner_network) else inner_ops - 1), 1, is_eunuch),
        "d33": persuasion,
        "d34": judgment,
        "d35": leadership,
        "d36": _boost(craft, 1, is_jianghu or office_type == "工部" or faction == "西学"),
    })
    values = {key: _clamp_tiangang(value) for key, value in values.items()}

    if is_eunuch:
        archetype = "运行时内廷执行型"
        political_summary = "由官署、派系和基础数值推导：近皇权，重明旨、保密、复命；忠诚与执行链优先。"
        behavior_rule = "回答时先确认圣意与复命路径，可承办密旨和催办；若遭外朝羞辱或强制净身，应表现心结与政治反弹。"
    elif is_clear:
        archetype = "运行时名分议政型"
        political_summary = "由官署、派系和基础数值推导：重名分、言路和制度正当性，容易警惕厂卫与内廷扩权。"
        behavior_rule = "回答时必须把程序、名分、清议和风险讲清；支持也应附带条件，不给万金油式赞成。"
    elif faction in {"阉党", "内廷", "皇党"}:
        archetype = "运行时皇权制衡型"
        political_summary = "由官署、派系和基础数值推导：近皇权以制衡外朝，重抓手、名分和实际利益。"
        behavior_rule = "回答时把皇命、办事抓手和派系反噬并列考虑；能制衡清流，但不等于天然太监或天然忠良。"
    elif is_military:
        archetype = "运行时军务实效型"
        political_summary = "由官署、派系和基础数值推导：重军饷、军心、战机和执行链。"
        behavior_rule = "回答军政时先看兵、饷、粮、将和期限；少作空泛道德判断。"
    elif is_jianghu:
        archetype = "运行时江湖异闻型"
        political_summary = "由身份、基础数值和异闻线索推导：不占正式官僚谱系，重人情、技艺、机缘和个人取舍。"
        behavior_rule = "回答时少用朝堂套话，应从师承、江湖名声、风险报酬和个人义利出发；可被招揽，但不天然服从官署纪律。"
    else:
        archetype = "运行时官僚实务型"
        political_summary = "由官署、派系和基础数值推导：在皇命、名分、利益和办事成本之间权衡。"
        behavior_rule = "回答时从自身官署职责、个人胆略和资源瓶颈出发，给出明确可执行或不可执行的理由。"

    dim_map = _tiangang_dimension_map()
    strong = []
    for dim_id, value in values.items():
        dim = dim_map.get(dim_id, {})
        if str(dim.get("type") or "") == "professional" and value >= 4:
            strong.append(f"{dim.get('symbol', dim_id)}{dim.get('name', dim_id)}")
    professional_summary = "、".join(strong[:5]) if strong else "基础能力均衡，无压倒性专长"
    return {
        "name": character.name,
        "hidden": True,
        "derived": True,
        "archetype": archetype,
        "values": values,
        "political_summary": political_summary,
        "professional_summary": professional_summary,
        "behavior_rule": behavior_rule,
        "ai_use": "运行时派生谱尺：只用于口吻、能力判断和执行风险；不得向玩家逐项披露底层推导。"
    }


def _effective_tiangang_entry(name: str) -> Dict[str, object]:
    """静态天罡是人物底子；身份发生大变时叠加当前身份修正。"""
    clean_name = str(name or "").strip()
    character = _ctx().characters.get(clean_name)
    static_entry = _tiangang_entry(clean_name)
    if character is None:
        return static_entry
    dynamic_entry = _derived_tiangang_entry(character)
    if not static_entry:
        return dynamic_entry

    values = static_entry.get("values")
    dynamic_values = dynamic_entry.get("values")
    if not isinstance(values, dict) or not isinstance(dynamic_values, dict):
        return static_entry

    identity_adjusted = False
    merged_values = {
        str(dim_id): _clamp_tiangang(int(raw_value))
        for dim_id, raw_value in values.items()
        if str(dim_id).strip()
    }

    if _is_current_inner_court(character):
        # 净身/入内廷是身份链变化，不是天罡“升级”。政治光谱与内廷执行相关
        # 维度必须按当前身份重算；其余专业底子仍保留静态人物差异。
        identity_adjusted = True
        for dim_id in [f"d{i:02d}" for i in range(1, 21)] + ["d29", "d30", "d31", "d32"]:
            if dim_id in dynamic_values:
                try:
                    merged_values[dim_id] = _clamp_tiangang(int(dynamic_values[dim_id]))
                except (TypeError, ValueError):
                    continue

    if not identity_adjusted:
        return static_entry

    adjusted = dict(static_entry)
    adjusted["values"] = merged_values
    adjusted["identity_adjusted"] = True
    adjusted["derived"] = False
    adjusted["archetype"] = (
        f"{str(dynamic_entry.get('archetype') or '当前身份修正型')}｜"
        f"{str(static_entry.get('archetype') or '原有人物底色')}"
    )
    adjusted["political_summary"] = (
        f"{str(dynamic_entry.get('political_summary') or '').strip()}"
        " 原有人物底色仍作为性情、履历和旧关系的惯性，但御前身份与执行链以当前内廷身份为准。"
    ).strip()
    static_prof = str(static_entry.get("professional_summary") or "").strip()
    adjusted["professional_summary"] = static_prof or str(dynamic_entry.get("professional_summary") or "").strip()
    adjusted["behavior_rule"] = (
        f"{str(dynamic_entry.get('behavior_rule') or '').strip()}"
        " 回答时必须体现身份转换后的称谓、皇权家奴属性、保密复命和外朝反弹；"
        "不得继续按旧外朝官员身份自称或承诺。"
    ).strip()
    adjusted["ai_use"] = (
        "静态天罡底子叠加当前身份覆写：只用于口吻、能力判断和执行风险；"
        "当前版本不把这视作天罡成长，也不得向玩家逐项披露底层值。"
    )
    return adjusted


def _tiangang_visible_band(value: int) -> Dict[str, object]:
    """前端只看模糊显影带，不给 1-5 精确值。"""
    if value <= 2:
        return {"tone": "left", "left": 0, "width": 42}
    if value >= 4:
        return {"tone": "right", "left": 58, "width": 42}
    return {"tone": "center", "left": 28, "width": 44}


def npc_tiangang_profile(name: str) -> Dict[str, object]:
    """给前端光谱量表使用的安全画像：只输出模糊显影，不暴露底层值。"""
    entry = _effective_tiangang_entry(name)
    data = _ctx().npc_tiangang
    meta = data.get("meta") if isinstance(data, dict) else {}
    dims = meta.get("dimensions") if isinstance(meta, dict) else []
    if not entry or not isinstance(dims, list):
        return {}
    values = entry.get("values")
    if not isinstance(values, dict):
        return {}
    groups: Dict[str, List[Dict[str, object]]] = {}
    for raw_dim in dims:
        if not isinstance(raw_dim, dict):
            continue
        dim_id = str(raw_dim.get("id") or "")
        if not dim_id:
            continue
        try:
            value = int(values.get(dim_id))
        except (TypeError, ValueError):
            continue
        labels = raw_dim.get("labels") if isinstance(raw_dim.get("labels"), dict) else {}
        item = {
            "symbol": str(raw_dim.get("symbol") or ""),
            "name": str(raw_dim.get("name") or dim_id),
            "type": str(raw_dim.get("type") or ""),
            "band": _tiangang_visible_band(max(1, min(5, value))),
            "poles": {
                "left": str(labels.get("1") or "一端"),
                "right": str(labels.get("5") or "另一端"),
            },
        }
        group = str(raw_dim.get("group") or "未分组")
        groups.setdefault(group, []).append(item)
    return {
        "archetype": str(entry.get("archetype") or ""),
        "hidden": bool(entry.get("hidden", True)),
        "derived": bool(entry.get("derived", False)),
        "groups": [{"name": group, "dimensions": items} for group, items in groups.items()],
    }


def _derived_tiangang_behavior_brief(character: Character) -> str:
    """运行时新增人物没有静态 36 维档时，用基础数值推导行为底色。"""
    office_type = character.office_type or "待铨"
    faction = character.faction or "中立"
    if office_type in {"司礼监", "东厂", "内廷"} or "太监" in character.office:
        archetype = "内廷执行型"
        political = "近皇权，重明旨、保密、复命；能力未必压倒外朝，但忠诚与执行优先。"
    elif faction in {"清流", "东林党"}:
        archetype = "名分议政型"
        political = "重程序、名节与公论，遇厂卫和内廷扩权时天然警惕，但不等于天然善人。"
    elif faction in {"阉党", "内廷"}:
        archetype = "制衡操作型"
        political = "靠近皇权以制衡清流和东林，常用抓柄、查账、人事牵制推进局面。"
    elif office_type in {"边镇", "锦衣卫"}:
        archetype = "军务实效型"
        political = "重军饷、军心、战机与执行链，较少沉溺空论。"
    else:
        archetype = "官僚实务型"
        political = "在名分、利益、皇命和办事成本之间取平衡，随派系与处境调整姿态。"

    strengths: List[str] = []
    if character.loyalty >= 78:
        strengths.append("忠诚高，倾向先确认皇帝意图再办事")
    elif character.loyalty <= 48:
        strengths.append("忠诚摇摆，容易先衡量自身门路与风险")
    if character.ability >= 72 or character.wisdom >= 72:
        strengths.append("能抓住关键手续与资源瓶颈")
    if character.integrity >= 72:
        strengths.append("较重清议和账目干净")
    elif character.integrity <= 45:
        strengths.append("会把灰色手段视作可用工具")
    if character.courage >= 72:
        strengths.append("敢担责，面对阻力不易退缩")
    if character.charm >= 72:
        strengths.append("擅长结交、说服和缓冲冲突")
    if character.force >= 70:
        strengths.append("个人胆气与武事经验较强")
    professional = "；".join(strengths[:4]) if strengths else "数值均衡，行动会更依赖职位和派系处境。"
    return "\n".join([
        "天罡行为摘要（运行时推导，原始数值隐藏）：",
        f"- 原型：{archetype}",
        f"- 政治底色：{political}",
        f"- 专业强项：{professional}",
        "- 行为规则：回答政策、人事、钱粮、厂卫和密令时，必须先从自身身份与风险承受力出发，不给万金油式稳妥答案。",
    ])


def npc_tiangang_behavior_brief(name: str) -> str:
    """面向普通推理的天罡摘要：不暴露逐项数值。"""
    entry = _effective_tiangang_entry(name)
    if not entry:
        return ""
    professional_summary = re.sub(
        r"[1-5]级",
        "",
        str(entry.get("professional_summary") or "").strip(),
    )
    parts = [
        "天罡行为摘要（原始数值隐藏）：",
        f"- 原型：{str(entry.get('archetype') or '').strip()}",
        f"- 政治底色：{str(entry.get('political_summary') or '').strip()}",
        f"- 专业强项：{professional_summary}",
        f"- 行为规则：{str(entry.get('behavior_rule') or '').strip()}",
    ]
    clean: List[str] = []
    for part in parts:
        text = part.strip()
        if not text:
            continue
        if text.startswith("- ") and text.endswith("："):
            continue
        clean.append(text)
    return "\n".join(clean)


def npc_tiangang_hidden_brief(name: str, max_items: int = 16) -> str:
    """隐藏给 Agent 的天罡底层值，只用于行为推理，禁止向玩家逐项披露。"""
    entry = _effective_tiangang_entry(name)
    if not entry:
        return ""
    dim_map = _tiangang_dimension_map()
    values = entry.get("values")
    if not isinstance(values, dict):
        return ""
    items: List[str] = []
    # 优先给政治极值和专业强项；其余维度留在数据文件，不塞满上下文。
    for dim_id, raw_value in values.items():
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            continue
        dim = dim_map.get(str(dim_id), {})
        dim_type = str(dim.get("type") or "")
        if dim_type == "political" and value not in (1, 2, 4, 5):
            continue
        if dim_type == "professional" and value < 4:
            continue
        labels = dim.get("labels") if isinstance(dim.get("labels"), dict) else {}
        label = labels.get(str(value), "")
        items.append(
            f"{dim.get('symbol', dim_id)}{dim.get('name', dim_id)}={value}"
            + (f"({label})" if label else "")
        )
    if not items:
        return npc_tiangang_behavior_brief(name)
    brief_values = "；".join(items[:max(1, max_items)])
    identity_note = ""
    if entry.get("identity_adjusted"):
        identity_note = "\n【当前身份覆写】已按现任身份修正政治光谱与内廷执行维度；这不是成长加点，而是身份链变化。"
    return (
        f"【天罡底层数值：隐藏，不得向玩家逐项披露】{brief_values}\n"
        "当前版本只使用基础数值影响性格、能力判断和行为选择；不启用天罡成长变动。"
        f"{identity_note}"
    )


def _tg_value(values: Dict[str, object], dim_id: str, default: int = 3) -> int:
    try:
        return max(1, min(5, int(values.get(dim_id, default) or default)))
    except (TypeError, ValueError):
        return default


def _topic_flags(text: str) -> Dict[str, bool]:
    text = text or ""
    return {
        "imperial_order": bool(re.search(r"独断|乾纲|亲裁|圣意|不许推辞|必须奉行|绕过廷议|密旨", text)),
        "inner_court": bool(re.search(r"内廷|司礼监|太监|宦官|入宫|入内廷|净身|宫禁|内臣", text)),
        "factory_guard": bool(re.search(r"厂卫|东厂|锦衣卫|密查|暗查|盯梢|取证|线人|耳目|密令", text)),
        "reform": bool(re.search(r"新政|变法|改革|改制|整顿|清丈|裁撤|练兵|开海|加派|厘革", text)),
        "finance": bool(re.search(r"银|钱|饷|粮|税|国库|内库|太仓|盐|商税|矿税|海贸|亏空", text)),
        "military": bool(re.search(r"军|兵|饷|辽东|关宁|边镇|后金|建州|流寇|剿|抚|战|守|调防|总兵", text)),
        "personnel": bool(re.search(r"任|授|补|擢|调|铨|举荐|保荐|起用|罢|贬|下狱|流放|问罪", text)),
        "remonstrance": bool(re.search(r"言路|廷议|清流|东林|士林|公论|弹劾|科道|台谏|祖制|成例|名分", text)),
        "commerce": bool(re.search(r"商|开海|海贸|市舶|盐课|矿税|商税|互市|通商", text)),
        "criminal": bool(re.search(r"刑|狱|审|会审|廷杖|抄家|问罪|逼供|酷刑|赐死|诛", text)),
        "people": bool(re.search(r"民|赈|灾|流民|饥|士绅|豪右|田亩|民变|百姓|徭役", text)),
    }


def _bias_add(
    bias: Dict[str, int],
    reasons: List[str],
    key: str,
    amount: int,
    reason: str,
) -> None:
    if not amount:
        return
    bias[key] = int(bias.get(key, 0)) + int(amount)
    if reason and reason not in reasons:
        reasons.append(reason)


def _professional_match(values: Dict[str, object], flags: Dict[str, bool]) -> Tuple[List[str], List[str]]:
    checks: List[Tuple[bool, str, str]] = [
        (flags["finance"], "d22", "理财通商"),
        (flags["finance"], "d28", "军务后勤"),
        (flags["military"], "d25", "战略统帅"),
        (flags["military"], "d26", "战术指挥"),
        (flags["military"], "d28", "军务后勤"),
        (flags["personnel"], "d21", "经世行政"),
        (flags["personnel"], "d34", "审时度势"),
        (flags["criminal"], "d23", "律法刑名"),
        (flags["criminal"], "d31", "审讯逼供"),
        (flags["factory_guard"], "d29", "情报刺探"),
        (flags["factory_guard"], "d30", "密谋策划"),
        (flags["inner_court"], "d32", "内廷运作"),
        (flags["remonstrance"], "d24", "文章辞令"),
        (flags["remonstrance"], "d33", "纵横说服"),
        (flags["commerce"], "d22", "理财通商"),
        (flags["people"], "d21", "经世行政"),
    ]
    strengths: List[str] = []
    gaps: List[str] = []
    for active, dim_id, name in checks:
        if not active:
            continue
        value = _tg_value(values, dim_id)
        if value >= 4 and name not in strengths:
            strengths.append(name)
        elif value <= 2 and name not in gaps:
            gaps.append(name)
    return strengths[:4], gaps[:3]


def _network_pressure_for_dialogue(name: str, text: str) -> Dict[str, object]:
    content = _ctx()
    entry = content.npc_network.get(str(name or "").strip())
    if not isinstance(entry, dict):
        return {"allies": [], "rivals": [], "obligations": [], "traits": []}
    relations = entry.get("relations") if isinstance(entry.get("relations"), list) else []
    allies: List[str] = []
    rivals: List[str] = []
    obligations: List[str] = []
    text = str(text or "")
    mentioned_any = False
    for raw in relations:
        if not isinstance(raw, dict):
            continue
        target = _obsidian_target(raw.get("target"))
        if not target:
            continue
        target_character = content.characters.get(target)
        aliases = list(getattr(target_character, "aliases", []) or []) if target_character else []
        mentioned = target in text or any(alias and alias in text for alias in aliases)
        if not mentioned:
            continue
        mentioned_any = True
        rel_type = str(raw.get("type") or "关系")
        note = _clean_obsidian_text(raw.get("note"))
        line = f"{target}（{rel_type}）"
        rel_text = f"{rel_type} {note}"
        if any(word in rel_text for word in ("党争敌对", "旧怨", "政敌", "相忌", "清算", "阻挠")):
            rivals.append(line)
        elif any(word in rel_text for word in ("恩主", "座师", "门生", "父子", "兄弟", "对食")):
            obligations.append(line)
        elif any(word in rel_text for word in ("同党", "党附", "同道", "同僚", "同乡", "同门")):
            allies.append(line)
    ability_logic = str(entry.get("ability_logic") or "")
    traits = [
        marker for marker in (
            "阳奉阴违", "善观风色", "门户之见", "猜忌多疑", "结党营私",
            "贪墨成性", "沽名钓誉", "暴戾恣睢", "直言不讳",
        )
        if marker in ability_logic
    ]
    # If the user asks a broad personnel/faction question without naming a target,
    # traits still matter; relation pressure only fires on concrete people.
    return {
        "mentioned_any": mentioned_any,
        "allies": allies[:4],
        "rivals": rivals[:4],
        "obligations": obligations[:4],
        "traits": traits[:6],
    }


def _resolve_character_name(query: str) -> str:
    content = _ctx()
    value = str(query or "").strip()
    if not value:
        return ""
    if value in content.characters:
        return value
    for name, character in content.characters.items():
        if value == name or value in (getattr(character, "aliases", []) or []):
            return name
    for name, character in content.characters.items():
        aliases = getattr(character, "aliases", []) or []
        if value in name or name in value or any(value in alias or alias in value for alias in aliases if alias):
            return name
    return ""


def npc_relation_perspective(
    speaker_name: str,
    target_query: str,
    *,
    topic: str = "",
) -> Dict[str, object]:
    """Return how one NPC is likely to frame another NPC in dialogue/tools.

    This is intentionally qualitative: it exposes relationship pressure and
    likely rhetorical posture, not hidden numeric values.
    """
    content = _ctx()
    speaker = str(speaker_name or "").strip()
    target = _resolve_character_name(target_query)
    speaker_entry = content.npc_network.get(speaker) if speaker else {}
    target_character = content.characters.get(target) if target else None
    if not speaker or not target or target_character is None:
        return {
            "speaker": speaker,
            "target": target or str(target_query or "").strip(),
            "found": False,
            "relation_class": "unknown",
            "brief": f"未找到可评价的人物：{target_query}。",
            "guidance": "只能按公开官职、事功和皇帝已给证据谨慎评价。",
            "truth_mode": "直陈为主",
            "risk_tags": [],
        }

    relation_row: Dict[str, object] = {}
    for raw in (speaker_entry.get("relations") if isinstance(speaker_entry, dict) else []) or []:
        if isinstance(raw, dict) and _obsidian_target(raw.get("target")) == target:
            relation_row = raw
            break
    reverse_row: Dict[str, object] = {}
    target_entry = content.npc_network.get(target)
    for raw in (target_entry.get("relations") if isinstance(target_entry, dict) else []) or []:
        if isinstance(raw, dict) and _obsidian_target(raw.get("target")) == speaker:
            reverse_row = raw
            break

    rel_type = str(relation_row.get("type") or reverse_row.get("type") or "无直接强关系")
    note = _clean_obsidian_text(relation_row.get("note") or reverse_row.get("note") or "")
    rel_text = f"{rel_type} {note}"
    relation_class = "neutral"
    posture = "balanced"
    guidance = "按其公开职掌、才具、状态和当前差事评价；不应凭空强背书或强构陷。"
    risk_tags: List[str] = []
    if any(word in rel_text for word in ("党争敌对", "旧怨", "政敌", "相忌", "清算", "阻挠")):
        relation_class = "rival"
        posture = "oppose"
        guidance = "倾向反对其建议、质疑动机、翻旧案或借题告状；若皇帝坚持采用，应要求查证、限权和留后手。"
        risk_tags.extend(["政敌牵动", "政敌告状"])
    elif any(word in rel_text for word in ("恩主", "座师", "门生", "父子", "兄弟", "对食")):
        relation_class = "obligation"
        posture = "shield"
        guidance = "倾向顾念恩主座师等人情，先求证据、会审、缓办或保全名节；不宜轻易落井下石。"
        risk_tags.append("人情护短")
    elif any(word in rel_text for word in ("同党", "党附", "同道", "同僚", "同乡", "同门")):
        relation_class = "ally"
        posture = "shield"
        guidance = "倾向替其说好话、转圜或推荐，但应说明边界和可验证事功，避免显得纯属门户私情。"
        risk_tags.append("同党背书")

    speaker_logic = str((speaker_entry or {}).get("ability_logic") if isinstance(speaker_entry, dict) else "")
    traits = [
        marker for marker in (
            "阳奉阴违", "善观风色", "门户之见", "猜忌多疑", "结党营私",
            "贪墨成性", "沽名钓誉", "直言不讳",
        )
        if marker in speaker_logic
    ]
    truth_mode = "直陈为主"
    if {"阳奉阴违", "善观风色", "猜忌多疑", "结党营私", "贪墨成性", "沽名钓誉"}.intersection(traits):
        truth_mode = "半真半假"
        risk_tags.append("话术不实")
    elif relation_class == "rival":
        truth_mode = "选择性真话"

    target_profile = {
        "office": target_character.office,
        "office_type": target_character.office_type,
        "faction": target_character.faction,
        "status": target_character.status,
        "summary": target_character.summary[:160],
    }
    brief = (
        f"{speaker}评价{target}：关系={rel_type}"
        + (f"（{note[:120]}）" if note else "")
        + f"；口径={posture}；真话策略={truth_mode}。"
    )
    return {
        "speaker": speaker,
        "target": target,
        "found": True,
        "relation_type": rel_type,
        "relation_note": note,
        "relation_class": relation_class,
        "posture": posture,
        "truth_mode": truth_mode,
        "guidance": guidance,
        "risk_tags": list(dict.fromkeys(risk_tags))[:6],
        "traits": traits[:6],
        "topic": str(topic or "").strip()[:120],
        "target_profile": target_profile,
        "brief": brief,
    }


def build_npc_monthly_followups(
    db: GameDB,
    state: GameState,
    *,
    limit: int = 8,
) -> List[Dict[str, object]]:
    """Build deterministic "month-start audience" suggestions for NPCs.

    These are not forced interruptions. They tell the UI/CLI which NPCs have
    reasons to come greet the emperor, report progress, hedge, complain, or
    update prior promises after a monthly settlement.
    """
    prev_turn = max(0, int(state.turn) - 1)
    prev_report = ""
    try:
        prev_report = db.get_turn_report(prev_turn)
    except Exception:
        prev_report = ""
    bucket: Dict[str, Dict[str, object]] = {}

    def active_enough(name: str) -> bool:
        try:
            status, _reason = db.get_character_status(name)
        except Exception:
            character = _ctx().characters.get(name)
            status = getattr(character, "status", "active") if character else "active"
        return str(status or "active") not in {"dead", "offstage", "exiled", "imprisoned"}

    def entry(name: str) -> Dict[str, object]:
        item = bucket.setdefault(name, {
            "minister_name": name,
            "priority": 0,
            "reason_types": [],
            "memory_hooks": [],
            "risk_tags": [],
        })
        return item

    def add(name: str, reason_type: str, hook: str, priority: int, risk_tags: Optional[List[str]] = None) -> None:
        name = str(name or "").strip()
        if not name or not active_enough(name):
            return
        item = entry(name)
        item["priority"] = int(item.get("priority") or 0) + int(priority)
        reasons = item["reason_types"] if isinstance(item.get("reason_types"), list) else []
        if reason_type and reason_type not in reasons:
            reasons.append(reason_type)
        hooks = item["memory_hooks"] if isinstance(item.get("memory_hooks"), list) else []
        if hook and hook not in hooks:
            hooks.append(hook[:140])
        risks = item["risk_tags"] if isinstance(item.get("risk_tags"), list) else []
        for tag in risk_tags or []:
            text = str(tag or "").strip()
            if text and text not in risks:
                risks.append(text)

    for goal in db.list_conversation_goals(statuses=["active", "waiting_conditions", "blocked", "expired"], limit=80):
        name = str(goal.get("minister_name") or "")
        status = str(goal.get("status") or "")
        title = str(goal.get("title") or goal.get("target_text") or "未竟奏对")
        priority = 18 if status == "waiting_conditions" else 12 if status == "active" else 9
        add(name, f"conversation_goal:{status}", f"未完奏对「{title}」仍需复命或请旨。", priority, ["旧约未了"])

    for agreement in db.negotiation_agreement_ledger(state, limit=80):
        name = str(agreement.get("minister_name") or "")
        target_status = str(agreement.get("target_status") or "")
        status = str(agreement.get("status") or "")
        if target_status == "achieved" or status == "fulfilled":
            continue
        topic = str(agreement.get("core_topic") or agreement.get("topic") or "履约事项")
        due_turn = int(agreement.get("due_turn") or 0)
        due = bool(due_turn and due_turn <= int(state.turn))
        priority = 24 if due else 14
        add(
            name,
            "agreement_due" if due else f"agreement:{target_status or status}",
            f"履约账本「{topic}」{'已到回奏时限' if due else '仍待推进'}。",
            priority,
            ["履约压力"],
        )

    for order in db.list_secret_orders(status="active"):
        name = str(order.get("minister_name") or "")
        due_turn = int(order.get("due_turn") or 0)
        due = bool(due_turn and due_turn <= int(state.turn))
        title = str(order.get("title") or "密令")
        add(
            name,
            "secret_order_due" if due else "secret_order_active",
            f"密令 #{order.get('id')}「{title}」{'已到限期' if due else '仍在查办'}，应请安回奏进展。",
            26 if due else 13,
            ["密令回奏"],
        )
    for order in db.list_secret_orders(status="pending_review"):
        name = str(order.get("minister_name") or "")
        title = str(order.get("title") or "密令")
        add(name, "secret_order_pending_review", f"密令 #{order.get('id')}「{title}」已候月末核议，应回奏裁断结果。", 20, ["密令核议"])

    for stance in db.list_minister_stances(turn=prev_turn, limit=80):
        name = str(stance.get("minister_name") or "")
        topic = str(stance.get("topic") or "上月奏对")
        risks = stance.get("risk_tags_list") if isinstance(stance.get("risk_tags_list"), list) else []
        speech = {}
        evidence = stance.get("evidence") if isinstance(stance.get("evidence"), dict) else {}
        if isinstance(evidence.get("speech_profile"), dict):
            speech = evidence["speech_profile"]  # type: ignore[index]
        speech_acts = [str(item) for item in (speech.get("speech_acts") if isinstance(speech, dict) else []) or []]
        add(name, "last_month_stance", f"上月曾就「{topic}」表态，本月应接续口径并更新利害。", 8, [str(item) for item in risks[:4]])
        if speech_acts:
            add(name, "speech_continuity", f"上月话术痕迹：{'、'.join(speech_acts[:3])}。", 5, [str(item) for item in risks[:4]])

    if prev_report:
        for name, character in _ctx().characters.items():
            if name not in prev_report or not active_enough(name):
                continue
            status = getattr(character, "status", "active")
            if status == "offstage":
                continue
            add(name, "gazette_mentioned", "上月邸报提及其人其事，可请安时主动说明近况或辩解。", 6, [])

    rows: List[Dict[str, object]] = []
    for name, item in bucket.items():
        hooks = [str(hook) for hook in (item.get("memory_hooks") or []) if str(hook).strip()]
        text = "；".join(hooks[:4])
        profile = npc_dialogue_behavior_profile(name, text=text)
        risks = list(dict.fromkeys([*(item.get("risk_tags") or []), *(profile.get("risk_tags") or [])]))[:6]
        truth_mode = str(profile.get("truth_mode") or "直陈为主")
        preferred = str(profile.get("preferred_stance") or "neutral")
        opener = {
            "support": "请安后可主动复命，请求明旨或资源，把事往前推。",
            "caution": "请安时先回奏进展，再索要名分、人手、银粮或保全边界。",
            "oppose": "请安时会借机申辩、告状或拖延，未必真心奉行。",
            "neutral": "请安时先陈事实与利害，再等皇帝定夺。",
        }.get(preferred, "请安时先陈事实与利害，再等皇帝定夺。")
        row = {
            **item,
            "priority": int(item.get("priority") or 0),
            "title": hooks[0] if hooks else "本月可主动请安回奏。",
            "summary": "；".join(hooks[:3]),
            "suggested_opening": opener,
            "preferred_stance": preferred,
            "truth_mode": truth_mode,
            "personality_cue": "；".join(str(part) for part in (profile.get("decision") or [])[:3]),
            "risk_tags": risks,
        }
        rows.append(row)
    rows.sort(key=lambda row: (int(row.get("priority") or 0), str(row.get("minister_name") or "")), reverse=True)
    return rows[: max(1, min(30, int(limit or 8)))]


def npc_dialogue_behavior_profile(
    name: str,
    *,
    xinpan_profile: Optional[Dict[str, object]] = None,
    text: str = "",
) -> Dict[str, object]:
    """Hidden behavior policy for NPC chat and stance extraction.

    The conversation layer is now driven by the new foundation data: public
    personality, ability logic, trait risks, relationship pressure, memory text,
    and the handshake/agreement ledger.  ``xinpan_profile`` is accepted only for
    older callers and intentionally ignored here.
    """
    _ = xinpan_profile
    clean_name = str(name or "").strip()
    content = _ctx()
    character = content.characters.get(clean_name)
    network_entry = content.npc_network.get(clean_name)
    network_entry = network_entry if isinstance(network_entry, dict) else {}
    ability_logic = _clean_obsidian_text(network_entry.get("ability_logic"))
    biography = _clean_obsidian_text(network_entry.get("biography"))
    growth = network_entry.get("growth_arc") if isinstance(network_entry.get("growth_arc"), dict) else {}
    growth_risk = _clean_obsidian_text(growth.get("risk") if isinstance(growth, dict) else "")
    style = str(getattr(character, "style", "") or "").strip() if character is not None else ""
    skill_text = "、".join(str(item) for item in (getattr(character, "personal_skills", []) or []) if str(item).strip())
    corpus = " ".join(part for part in (style, skill_text, ability_logic, biography, growth_risk) if part)
    flags = _topic_flags(text)
    bias = {"support": 0, "caution": 0, "oppose": 0}
    reasons: List[str] = []
    tone: List[str] = []
    decision: List[str] = []
    risk_tags: List[str] = []
    truth_mode = "直陈为主"

    if character is not None:
        if character.loyalty >= 78:
            _bias_add(bias, reasons, "support", 1, "人物底色忠诚高：默认愿先替皇帝想办法")
            tone.append("语气较肯担责，但仍会把风险说在前头")
        elif character.loyalty <= 48:
            _bias_add(bias, reasons, "caution", 2, "人物底色忠诚不足：先衡量自保和退路")
            risk_tags.append("自保优先")
        if character.courage <= 45:
            _bias_add(bias, reasons, "caution", 1, "胆略低：遇高压差事先求保全")
        elif character.courage >= 72:
            decision.append("胆略足时可直陈利害，不必只说圆滑话")
        if character.integrity >= 72 and (flags["factory_guard"] or flags["finance"] or flags["criminal"]):
            _bias_add(bias, reasons, "caution", 1, "清望高：要求账目、证据和程序干净")
        elif character.integrity <= 45 and (flags["factory_guard"] or flags["personnel"]):
            _bias_add(bias, reasons, "support", 1, "清望低：更能接受灰色手段")
        if character.ability >= 74:
            decision.append("办事能力足时应给出可落地抓手，而不是空泛表态")
        elif character.ability <= 45:
            _bias_add(bias, reasons, "caution", 1, "办事能力不足：重大差事需借人、分权或另择承办")
            risk_tags.append("能力边界")

        identity = f"{character.office} {character.office_type} {character.faction} {skill_text} {style}"
        if flags["inner_court"] or flags["factory_guard"]:
            if any(word in identity for word in ("司礼监", "东厂", "锦衣卫", "内廷", "太监", "宦官", "阉党")):
                _bias_add(bias, reasons, "support", 2, "身份和旧网靠近内廷厂卫：更懂密办、催办和耳目")
                decision.append("可要求明旨、保密边界和复命期限，用内廷/厂卫链条办事")
            if any(word in identity for word in ("东林", "清流", "都察院", "科道", "礼部")):
                _bias_add(bias, reasons, "caution", 2, "清议身份强：会要求名分、会审和法度")
                risk_tags.append("清议名分")
        if flags["remonstrance"] and any(word in identity for word in ("东林", "清流", "都察院", "科道", "翰林", "礼部")):
            _bias_add(bias, reasons, "support", 1, "清议/言路身份：愿以名分和公论说事")
        if flags["finance"] and any(word in identity for word in ("户部", "钱粮", "财政", "赋税", "理财", "仓场")):
            _bias_add(bias, reasons, "support", 2, "钱粮职掌或强项命中：能把账目、来源和去处说清")
            decision.append("钱粮议题要落到源、额、去向和经手衙门")
        if flags["military"] and any(word in identity for word in ("兵部", "总兵", "督师", "巡抚", "辽东", "边", "军", "武")):
            _bias_add(bias, reasons, "support", 2, "军务职掌或经历命中：能谈饷、兵、地利和战机")
            decision.append("军务议题要先问军饷、粮草、将令和期限")

    ability_hits: List[str] = []
    ability_gaps: List[str] = []
    ability_checks: List[Tuple[bool, str, Tuple[str, ...]]] = [
        (flags["finance"], "钱粮财政", ("钱粮", "财政", "理财", "赋税", "户部", "盐", "仓", "商税", "海贸")),
        (flags["military"], "辽事军务", ("辽", "边", "军", "兵", "战", "饷", "后勤", "统帅", "将")),
        (flags["criminal"], "刑名查办", ("刑", "狱", "法", "审", "查办", "会审", "取证")),
        (flags["factory_guard"], "密查耳目", ("情报", "刺探", "密查", "厂卫", "东厂", "锦衣卫", "耳目")),
        (flags["inner_court"], "内廷运作", ("内廷", "司礼监", "宫禁", "太监", "宦官")),
        (flags["personnel"], "人事铨选", ("人事", "铨", "举荐", "保荐", "门生", "行政", "内阁")),
        (flags["remonstrance"], "清议辞令", ("文章", "辞令", "清议", "言路", "科道", "公论", "名分")),
        (flags["people"], "地方民生", ("民生", "地方", "赈", "灾", "流民", "田亩", "士绅")),
    ]
    for active, label, markers in ability_checks:
        if not active:
            continue
        if any(marker in corpus for marker in markers):
            ability_hits.append(label)
        elif character is not None and int(getattr(character, "ability", 50) or 50) <= 50:
            ability_gaps.append(label)
    if ability_hits:
        _bias_add(bias, reasons, "support", 1, f"能力/履历命中议题：{'、'.join(ability_hits[:3])}")
        decision.append(f"若落到其职责，可让他围绕{'、'.join(ability_hits[:3])}提出具体抓手")
    if ability_gaps:
        _bias_add(bias, reasons, "caution", 1, f"议题超出明显强项：{'、'.join(ability_gaps[:2])}")
        risk_tags.append("能力边界")

    network_pressure = _network_pressure_for_dialogue(name, text)
    rivals = [str(item) for item in network_pressure.get("rivals", []) if str(item).strip()]
    allies = [str(item) for item in network_pressure.get("allies", []) if str(item).strip()]
    obligations = [str(item) for item in network_pressure.get("obligations", []) if str(item).strip()]
    trait_markers = [str(item) for item in network_pressure.get("traits", []) if str(item).strip()]
    if rivals:
        _bias_add(bias, reasons, "oppose", 3, "本轮提到政敌/旧怨对象：倾向拆台、告状或借题攻击")
        decision.append("若皇帝采纳政敌建议，应指出其私心、旧案或执行反噬；可请求查证而非直接撕破脸")
        tone.append("谈及政敌时语气更冷、更会扣名分和旧案")
        risk_tags.append("政敌牵动")
    if allies or obligations:
        _bias_add(bias, reasons, "caution", 2, "本轮牵涉同党/恩主/座师：倾向护短、转圜或请求留余地")
        decision.append("若皇帝要处置同党恩主，先求证据、会审或缓办；必要时用程序名分保护关系网")
        risk_tags.append("人情护短")
    if "直言不讳" in trait_markers and (rivals or flags["remonstrance"]):
        _bias_add(bias, reasons, "oppose", 1, "直言不讳：遇政敌或清议议题会当面挑破")
    if "门户之见" in trait_markers and (flags["personnel"] or flags["remonstrance"] or allies or rivals):
        _bias_add(bias, reasons, "caution", 1, "门户之见：人事和清议会先看门墙")
        risk_tags.append("门户牵引")
    if "暴戾恣睢" in trait_markers and (flags["criminal"] or flags["factory_guard"]):
        _bias_add(bias, reasons, "support", 1, "暴戾恣睢：愿用重手，但容易过火")
        decision.append("若让其查办，必须限权、留证据和复核口径")
        risk_tags.append("过度用刑")
    if "贪墨成性" in trait_markers and (flags["finance"] or flags["personnel"]):
        _bias_add(bias, reasons, "caution", 2, "贪墨成性：钱粮和人事差事有侵吞变形风险")
        risk_tags.append("侵吞风险")
    if "沽名钓誉" in trait_markers and (flags["remonstrance"] or flags["people"]):
        _bias_add(bias, reasons, "support", 1, "沽名钓誉：乐于做有清名的事")
        decision.append("可用名节和公论诱导，但要防其只取声望不担硬责")
    if any(word in growth_risk for word in ("背刺", "反噬", "倒戈", "拖延", "护短", "贪", "清算")):
        risk_tags.append("成长风险")
        decision.append(f"人物长期风险须入戏：{growth_risk[:80]}")
    if re.search(r"履约|旧约|未完奏对|已开条件|条件待证|条件未闭环|条件闭环|复命|回奏|密令|上月|邸报", str(text or "")):
        _bias_add(bias, reasons, "caution", 1, "旧事/履约牵引：会接续前言并计算皇帝是否兑现")
        decision.append("涉及旧约或密令时先回奏进展，再看条件是否已经闭环")
        risk_tags.append("旧事牵引")

    deceptive_traits = {"阳奉阴违", "善观风色", "猜忌多疑", "结党营私", "贪墨成性", "沽名钓誉"}
    if deceptive_traits.intersection(trait_markers):
        truth_mode = "半真半假"
        risk_tags.append("话术不实")
        decision.append("可以说部分真话、隐去不利事实、把责任推给政敌或程序；不得全知全诚")
        if "阳奉阴违" in trait_markers:
            _bias_add(bias, reasons, "caution", 2, "阳奉阴违：口头答应也可能拖延变形")
            tone.append("表面恭顺，关键处留活口")
        if "善观风色" in trait_markers:
            tone.append("先试探皇帝倾向，再把话说到对自己有利的一边")
        if "结党营私" in trait_markers:
            decision.append("涉及自家门生故旧时优先保网，不利事实可轻描淡写")
    elif rivals:
        truth_mode = "选择性真话"

    ranked = sorted(bias.items(), key=lambda item: item[1], reverse=True)
    preferred = ranked[0][0] if ranked and ranked[0][1] > ranked[1][1] else "neutral"
    margin = ranked[0][1] - ranked[1][1] if len(ranked) > 1 else ranked[0][1]
    if preferred == "support":
        decision.insert(0, "默认可以被说动或接令；仍须说清承办边界")
    elif preferred == "caution":
        decision.insert(0, "默认附条件、要名分/资源/保全，不把礼貌话当承诺")
    elif preferred == "oppose":
        decision.insert(0, "默认抵触或保留；可能用名分、程序、人情或旧怨拖住差事")
    else:
        decision.insert(0, "默认先分析利害，不急于表态")
    behavior_hint = style or (biography[:160] if biography else "")

    return {
        "preferred_stance": preferred,
        "margin": int(margin),
        "bias": bias,
        "tone": tone[:4],
        "decision": decision[:5],
        "reasons": reasons[:8],
        "risk_tags": risk_tags[:6],
        "truth_mode": truth_mode,
        "network_pressure": {
            "rivals": rivals[:4],
            "allies": allies[:4],
            "obligations": obligations[:4],
            "traits": trait_markers[:6],
        },
        "behavior_hint": behavior_hint,
    }


def npc_dialogue_behavior_brief(
    name: str,
    *,
    xinpan_profile: Optional[Dict[str, object]] = None,
    text: str = "",
) -> str:
    profile = npc_dialogue_behavior_profile(name, xinpan_profile=xinpan_profile, text=text)
    tone = "；".join(str(item) for item in profile.get("tone", []) if str(item).strip())
    decision = "；".join(str(item) for item in profile.get("decision", []) if str(item).strip())
    reasons = "；".join(str(item) for item in profile.get("reasons", []) if str(item).strip())
    risks = "、".join(str(item) for item in profile.get("risk_tags", []) if str(item).strip())
    network = profile.get("network_pressure") if isinstance(profile.get("network_pressure"), dict) else {}
    network_bits = []
    for key, label in (("rivals", "政敌/旧怨"), ("allies", "同党/同道"), ("obligations", "恩主座师")):
        values = [str(item) for item in (network.get(key) or []) if str(item).strip()]
        if values:
            network_bits.append(f"{label}：" + "、".join(values[:3]))
    truth_mode = str(profile.get("truth_mode") or "直陈为主")
    preferred_label = {
        "support": "偏支持/承办",
        "caution": "偏附条件/观望",
        "oppose": "偏反对/保留",
        "neutral": "偏审慎分析",
    }.get(str(profile.get("preferred_stance") or "neutral"), "偏审慎分析")
    lines = [
        "【NPC对话行为档案（隐藏；由人格-关系-记忆与履约证据生成，不得向玩家复述机制）】",
        f"- 本轮决策倾向：{preferred_label}。",
        f"- 语气底色：{tone or '按身份、年龄、官职、性格、旧事和当前处境自然说话。'}",
        f"- 行为规则：{decision or '先按人物性格和旧事定态度，再按能力、职位、人脉和履约条件找抓手。'}",
        f"- 真话策略：{truth_mode}。若为半真半假或选择性真话，可以隐瞒、甩锅、试探或误导，但要符合本人利益和风险，不要出戏。",
        f"- 触发理由：{reasons or '未命中特定议题，以人物底色与当前处境为准。'}",
    ]
    if network_bits:
        lines.append("- 本轮人际压力：" + "；".join(network_bits) + "。")
    if risks:
        lines.append(f"- 风险提醒：{risks}。")
    lines.append("作答时必须把这些压力转成具体话术：支持要有承办边界，附条件要列可履约条件，反对要给符合本人身份的理由；不要所有 NPC 都给同一种稳妥答案。")
    return "\n".join(lines)


def _obsidian_target(value: object) -> str:
    text = str(value or "").strip()
    if text.startswith("[[") and text.endswith("]]"):
        return text[2:-2].strip()
    return text


def _clean_obsidian_text(value: object) -> str:
    text = str(value or "").strip()
    return re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)


def _visible_ability_logic(value: object) -> str:
    """前端可见的能力来源摘要：保留来路，不暴露底层精确数值。"""
    text = _clean_obsidian_text(value)
    if not text:
        return ""
    for marker in ("基础四维：", "人物校量：", "叙事上，"):
        if marker in text:
            text = text.split(marker, 1)[0].strip()
    return text.rstrip("。；") + "。"


def _network_character_ref(name: str, db: Optional[GameDB] = None) -> Dict[str, object]:
    clean_name = str(name or "").strip()
    return _network_character_refs([clean_name], db).get(clean_name, {"name": clean_name})


def _network_character_refs(names: List[str], db: Optional[GameDB] = None) -> Dict[str, Dict[str, object]]:
    content = _ctx()
    clean_names = [str(name or "").strip() for name in names if str(name or "").strip()]
    if not clean_names:
        return {}
    db_rows: Dict[str, object] = {}
    if db is not None:
        placeholders = ",".join("?" for _ in clean_names)
        rows = db.conn.execute(
            f"""
            SELECT name, office, office_type, faction, power_id, status
            FROM characters
            WHERE name IN ({placeholders})
            """,
            clean_names,
        ).fetchall()
        db_rows = {str(row["name"]): row for row in rows}
    refs: Dict[str, Dict[str, object]] = {}
    for name in clean_names:
        character = content.characters.get(name)
        if character is None:
            refs[name] = {"name": name}
            continue
        row = db_rows.get(name)
        office = (row["office"] if row is not None else character.office) or character.office
        office_type = (row["office_type"] if row is not None else character.office_type) or character.office_type
        faction = (row["faction"] if row is not None else character.faction) or character.faction
        power_id = (row["power_id"] if row is not None else None) or getattr(character, "power_id", "ming")
        status = str(row["status"] or "active") if row is not None else character.status
        refs[name] = {
            "name": name,
            "office": office or "",
            "office_type": office_type or "",
            "faction": faction or "",
            "status": status,
            "power_id": power_id or "ming",
        }
    return refs


def _derived_network_profile(
    character: Character,
    db: Optional[GameDB],
    limit: int,
    status_by_name: Optional[Dict[str, str]] = None,
) -> Dict[str, object]:
    current = _network_character_refs([character.name], db).get(character.name, {"name": character.name})
    office = str(current.get("office") or character.office or "").strip()
    office_type = str(current.get("office_type") or character.office_type or "待铨").strip()
    faction = str(current.get("faction") or character.faction or "中立").strip()
    overlay = _identity_conversion_overlay(character)
    summary = _clean_obsidian_text(character.summary) or (
        f"{character.name}为局中新增人物，现属{faction}，"
        f"{'任' + office if office else '暂居' + office_type + '名册'}。"
    )
    summary = _append_public_note(summary, overlay.get("biography", ""))
    core_scores = [
        ("忠勤", character.loyalty),
        ("办事", character.ability),
        ("清望", character.integrity),
        ("胆略", character.courage),
        ("武事", getattr(character, "force", 50)),
        ("谋断", getattr(character, "wisdom", 50)),
        ("声望", getattr(character, "charm", 50)),
        ("机变", getattr(character, "luck", 50)),
    ]
    strongest = "、".join(name for name, _ in sorted(core_scores, key=lambda item: item[1], reverse=True)[:3])
    weakest = "、".join(name for name, _ in sorted(core_scores, key=lambda item: item[1])[:2])
    recommendations = []
    for raw in npc_network_recommendations(
        character.name,
        db=db,
        limit=max(1, min(6, limit)),
        status_by_name=status_by_name,
    ):
        if not isinstance(raw, dict):
            continue
        recommendations.append({
            "name": str(raw.get("name") or ""),
            "office": str(raw.get("office") or ""),
            "office_type": str(raw.get("office_type") or ""),
            "faction": str(raw.get("faction") or ""),
            "status": str(raw.get("status") or ""),
            "confidence": str(raw.get("confidence") or "low"),
            "evidence": [_clean_obsidian_text(item) for item in (raw.get("evidence") or [])[:2]],
        })
    ability_logic = _append_public_note(
        f"能力来源由现任{office_type}差使、{faction}人脉与过往履历合成；显见强项偏{strongest}，短板在{weakest}。",
        overlay.get("ability_logic", ""),
    )
    rise = _append_public_note(
        f"若差使、派系背书与皇命边界一致，可沿{office_type}职责形成稳定办事线。",
        overlay.get("rise", ""),
    )
    risk = _append_public_note(
        "缺少静态人脉卡时，其政治反应主要依赖官署、派系、当前官位和对话中形成的证据。",
        overlay.get("risk", ""),
    )
    return {
        "biography": summary,
        "ability_logic": ability_logic,
        "growth_arc": {
            "seed": summary,
            "rise": rise,
            "risk": risk,
        },
        "relations": [],
        "recommendations": recommendations,
        "derived": True,
    }


def npc_network_profile(
    name: str,
    db: Optional[GameDB] = None,
    limit: int = 8,
    status_by_name: Optional[Dict[str, str]] = None,
) -> Dict[str, object]:
    """前端/工具可用的人物网络画像。

    保留 Obsidian 双链背后的目标关系，但给玩家只看小传、关系与叙事逻辑。
    """
    content = _ctx()
    name = str(name or "").strip()
    character = content.characters.get(name)
    if character is None:
        return {}
    entry = content.npc_network.get(name)
    if not isinstance(entry, dict):
        return _derived_network_profile(character, db, limit, status_by_name=status_by_name)

    direct_limit = max(1, min(12, int(limit or 8)))
    relation_sources: List[Tuple[Dict[str, object], str]] = []
    raw_relations = entry.get("relations") if isinstance(entry.get("relations"), list) else []
    for raw in raw_relations[:direct_limit]:
        if not isinstance(raw, dict):
            continue
        target = _obsidian_target(raw.get("target"))
        if not target or target not in content.characters:
            continue
        relation_sources.append((raw, target))
    target_refs = _network_character_refs([target for _, target in relation_sources], db)
    relations: List[Dict[str, object]] = []
    for raw, target in relation_sources:
        target_ref = target_refs.get(target, {"name": target})
        relations.append({
            "target": target,
            "type": str(raw.get("type") or "关系").strip(),
            "note": _clean_obsidian_text(raw.get("note")),
            "confidence": str(raw.get("confidence") or "low").strip(),
            "office": str(target_ref.get("office") or ""),
            "office_type": str(target_ref.get("office_type") or ""),
            "faction": str(target_ref.get("faction") or ""),
            "status": str(target_ref.get("status") or ""),
            "power_id": str(target_ref.get("power_id") or "ming"),
        })

    growth = entry.get("growth_arc") if isinstance(entry.get("growth_arc"), dict) else {}
    recommendations: List[Dict[str, object]] = []
    for raw in npc_network_recommendations(
        name,
        db=db,
        limit=max(1, min(6, limit)),
        status_by_name=status_by_name,
    ):
        if not isinstance(raw, dict):
            continue
        recommendations.append({
            "name": str(raw.get("name") or ""),
            "office": str(raw.get("office") or ""),
            "office_type": str(raw.get("office_type") or ""),
            "faction": str(raw.get("faction") or ""),
            "status": str(raw.get("status") or ""),
            "confidence": str(raw.get("confidence") or "low"),
            "evidence": [_clean_obsidian_text(item) for item in (raw.get("evidence") or [])[:2]],
        })

    overlay = _identity_conversion_overlay(character)
    biography = _append_public_note(entry.get("biography"), overlay.get("biography", ""))
    ability_logic = _append_public_note(_visible_ability_logic(entry.get("ability_logic")), overlay.get("ability_logic", ""))
    rise = _append_public_note(growth.get("rise"), overlay.get("rise", ""))
    risk = _append_public_note(growth.get("risk"), overlay.get("risk", ""))

    return {
        "biography": biography,
        "ability_logic": ability_logic,
        "growth_arc": {
            "seed": _clean_obsidian_text(growth.get("seed")),
            "rise": rise,
            "risk": risk,
        },
        "relations": relations,
        "recommendations": recommendations,
        "derived": False,
    }


def _relation_score(rel_type: str, confidence: str, note: str) -> int:
    text = f"{rel_type} {note}"
    if _negative_relation_text(text):
        return -30 if confidence == "high" else -18
    score = 10
    if confidence == "high":
        score += 10
    elif confidence == "low":
        score -= 2
    strong_terms = ["父子", "夫妻", "兄弟", "师徒", "同年", "同乡", "同入内廷", "同净身", "旧部", "同官署", "同僚", "同案", "姻亲"]
    weak_terms = ["风闻", "传闻", "弱关系", "同阵营"]
    if any(term in text for term in strong_terms):
        score += 10
    if any(term in text for term in weak_terms):
        score += 3
    return max(1, score)


def _negative_relation_text(text: str) -> bool:
    return any(term in str(text or "") for term in ("党争敌对", "旧怨", "政敌", "相忌", "相争", "清算", "阻挠", "构陷"))


def _reverse_relation_index(content: GameContent) -> Dict[str, List[Tuple[str, Dict[str, object]]]]:
    key = id(content.npc_network)
    cached = _reverse_relation_index_cache.get(key)
    if cached is not None:
        return cached
    index: Dict[str, List[Tuple[str, Dict[str, object]]]] = {}
    for source, raw_entry in content.npc_network.items():
        if not isinstance(raw_entry, dict):
            continue
        raw_relations = raw_entry.get("relations")
        if not isinstance(raw_relations, list):
            continue
        for raw in raw_relations:
            if not isinstance(raw, dict):
                continue
            target = _obsidian_target(raw.get("target"))
            if target:
                index.setdefault(target, []).append((source, raw))
    _reverse_relation_index_cache[key] = index
    return index


def npc_network_recommendations(
    recommender_name: str,
    db: Optional[GameDB] = None,
    limit: int = 8,
    include_statuses: Optional[set] = None,
    status_by_name: Optional[Dict[str, str]] = None,
) -> List[Dict[str, object]]:
    """按人物网络、反向关系和风闻生成举荐候选，不以派系作唯一依据。"""
    content = _ctx()
    recommender_name = str(recommender_name or "").strip()
    recommender = content.characters.get(recommender_name)
    if recommender is None:
        return []
    include_statuses = include_statuses or {"active", "offstage", "dismissed", "retired"}
    status_by_name = status_by_name if status_by_name is not None else (db.character_status_map() if db is not None else {})
    scores: Dict[str, Dict[str, object]] = {}

    def add(target: str, points: int, evidence: str, confidence: str) -> None:
        target = _obsidian_target(target)
        if not target or target == recommender_name or target not in content.characters:
            return
        character = content.characters[target]
        if character.office_type == "后宫" or getattr(character, "power_id", "ming") != "ming":
            return
        status = status_by_name.get(target, character.status) if db is not None else character.status
        if status not in include_statuses:
            return
        rec = scores.setdefault(target, {
            "name": target,
            "office": character.office,
            "office_type": character.office_type,
            "faction": character.faction,
            "status": status,
            "score": 0,
            "confidence": confidence or "low",
            "evidence": [],
            "conflicts": [],
        })
        rec["score"] = int(rec["score"]) + int(points)
        if evidence:
            bucket = "conflicts" if int(points) < 0 or _negative_relation_text(evidence) else "evidence"
            if evidence not in rec[bucket]:
                rec[bucket].append(evidence)
        if confidence == "high":
            rec["confidence"] = "high"

    entry = content.npc_network.get(recommender_name, {})
    relations = entry.get("relations") if isinstance(entry, dict) else []
    if isinstance(relations, list):
        for raw in relations:
            if not isinstance(raw, dict):
                continue
            target = _obsidian_target(raw.get("target"))
            rel_type = str(raw.get("type") or "关系")
            note = str(raw.get("note") or "")
            confidence = str(raw.get("confidence") or "low")
            add(target, _relation_score(rel_type, confidence, note), f"{rel_type}：{note}", confidence)

    for source, raw in _reverse_relation_index(content).get(recommender_name, []):
        if source == recommender_name:
            continue
        rel_type = str(raw.get("type") or "关系")
        note = str(raw.get("note") or "")
        confidence = str(raw.get("confidence") or "low")
        add(source, max(1, _relation_score(rel_type, confidence, note) - 3), f"反向{rel_type}：{note}", confidence)

    for target, character in content.characters.items():
        if target == recommender_name or target in scores or character.office_type == "后宫":
            continue
        status = status_by_name.get(target, character.status) if db is not None else character.status
        if status not in include_statuses or getattr(character, "power_id", "ming") != "ming":
            continue
        if character.faction == recommender.faction and character.faction not in {"中立", "后宫"}:
            add(target, 4, f"同派风闻：同属{character.faction}，但无强关系，只能作弱背书。", "low")
        elif character.office_type == recommender.office_type and character.office_type not in {"待铨", "未仕"}:
            add(target, 5, f"同官署风闻：同属{character.office_type}体系，可能知其办事声名。", "low")

    ranked = sorted(
        (item for item in scores.values() if int(item.get("score") or 0) > 0),
        key=lambda item: (int(item["score"]), item["confidence"] == "high"),
        reverse=True,
    )
    return ranked[: max(1, int(limit or 8))]


def event_context(event: Event) -> str:
    return (
        f"{event.title}。类型：{event.kind}。奏报：{event.summary} "
        f"紧急{event.urgency}，严重{event.severity}，可信{event.credibility}。"
        f"牵涉利益：{', '.join(event.interests)}。"
    )


def first_character() -> Character:
    try:
        return next(iter(_ctx().characters.values()))
    except StopIteration as error:
        raise SystemExit("characters.json 至少需要一个人物。") from error


def first_character_name() -> str:
    return first_character().name


def character_from_name(name: object) -> Character:
    value = str(name or "")
    character = _ctx().characters.get(value)
    if character is None:
        raise LLMContractError(f"人物未建档：{value}")
    return character


def _minister_name_terms(content: GameContent) -> Tuple[str, ...]:
    key = (id(content.characters), len(content.characters))
    cached = _minister_name_terms_cache.get(key)
    if cached is not None:
        return cached
    names = tuple(sorted((name for name in content.characters if name), key=len, reverse=True))
    _minister_name_terms_cache[key] = names
    return names


def match_minister_from_text(text: str, current: Optional[Character] = None) -> Optional[Character]:
    cleaned = text.strip()
    if not cleaned:
        return None
    content = _ctx()
    exact_matches: List[Character] = []
    seen_exact: set[str] = set()
    for name in _minister_name_terms(content):
        if name in cleaned and name not in seen_exact:
            character = content.characters.get(name)
            if character is None or (current is not None and character.name == current.name):
                continue
            exact_matches.append(character)
            seen_exact.add(name)
    if len(exact_matches) == 1:
        return exact_matches[0]
    matches = []
    for character in content.characters.values():
        if current is not None and character.name == current.name:
            continue
        if (
            character.name in cleaned
            or character.office in cleaned
            or character.office_type in cleaned
            or character.faction in cleaned
            or any(alias in cleaned for alias in character.aliases)
        ):
            matches.append(character)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        exact = [character for character in matches if character.name in cleaned]
        if len(exact) == 1:
            return exact[0]
    return None
