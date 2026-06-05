"""天命异闻引擎。为月末暗线事件提供触发、处置和验算。

设计原则：
- 每月结算时低频触发异闻判定
- 异闻基于地点、人物校量、气运、当前局势综合触发
- 每个异闻有多分支处置，结果由骰子+属性检定决定
- 异闻结果影响人物校量、国势或后续回合反馈
- 所有触发可验算（依赖 dice.DiceRoller）
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ming_sim.dice import DiceRoller


@dataclass
class AdventureChoice:
    """奇遇的一个选项。"""
    text: str                          # 选项文案
    required_ability: Optional[str] = None   # 需要的校量（force/wisdom/charm/luck/cultivation）
    required_min: int = 0              # 属性最低要求
    dc: int = 10                       # 检定DC
    success_effects: Dict[str, int] = field(default_factory=dict)  # 成功效果 {metric: delta}
    fail_effects: Dict[str, int] = field(default_factory=dict)     # 失败效果
    success_narrative: str = ""        # 成功叙事
    fail_narrative: str = ""           # 失败叙事
    item_reward: Optional[str] = None  # 成功获得物品ID


@dataclass
class AdventureEvent:
    """一个奇遇事件。"""
    id: str
    title: str
    kind: str                          # 江湖/秘境/朝堂/边塞/天灾/异象
    summary: str                       # 事件简介
    region_hint: str = ""              # 偏好地区ID
    trigger_probability: float = 0.05  # 基础触发概率（5%）
    required_turn_min: int = 1
    required_turn_max: int = 9999
    required_metrics: Dict[str, str] = field(default_factory=dict)  # {metric: 比较式}
    choices: List[AdventureChoice] = field(default_factory=list)
    narrative_prefix: str = ""         # 前置叙事
    narrative_suffix: str = ""         # 后置叙事
    repeatable: bool = False           # 是否可重复触发


@dataclass
class AdventureResult:
    """一次奇遇的完整结果。"""
    adventure_id: str
    title: str
    chosen_index: int
    choice_text: str
    success: bool
    roll_result: Optional[Tuple[int, int, int]]  # (d20, modifier, total)
    effects: Dict[str, int]
    narrative: str
    item_reward: Optional[str] = None


class AdventureEngine:
    """奇遇引擎。"""

    def __init__(self, adventures: List[AdventureEvent]):
        self.adventures = {a.id: a for a in adventures}
        self._triggered: set[str] = set()  # 已触发过的非重复奇遇

    def remember_triggered(self, adventure_ids: List[str]) -> None:
        """恢复已入档事件，避免新建引擎后重复触发非 repeatable 异闻。"""
        self._triggered.update(str(item) for item in adventure_ids if str(item).strip())

    @classmethod
    def from_records(cls, records: List[Dict[str, object]]) -> "AdventureEngine":
        """从 GameContent 已加载的 JSON 记录构造引擎，避免运行时再绕过内容层。"""
        adventures: List[AdventureEvent] = []
        for item in records:
            if not isinstance(item, dict):
                continue
            choices = []
            raw_choices = item.get("choices", [])
            if not isinstance(raw_choices, list):
                raw_choices = []
            for c in raw_choices:
                if not isinstance(c, dict):
                    continue
                choices.append(AdventureChoice(
                    text=c["text"],
                    required_ability=c.get("required_ability"),
                    required_min=c.get("required_min", 0),
                    dc=c.get("dc", 10),
                    success_effects=c.get("success_effects", {}),
                    fail_effects=c.get("fail_effects", {}),
                    success_narrative=c.get("success_narrative", ""),
                    fail_narrative=c.get("fail_narrative", ""),
                    item_reward=c.get("item_reward"),
                ))
            adventures.append(AdventureEvent(
                id=item["id"],
                title=item["title"],
                kind=item["kind"],
                summary=item["summary"],
                region_hint=item.get("region_hint", ""),
                trigger_probability=item.get("trigger_probability", 0.05),
                required_turn_min=item.get("required_turn_min", 1),
                required_turn_max=item.get("required_turn_max", 9999),
                required_metrics=item.get("required_metrics", {}),
                choices=choices,
                narrative_prefix=item.get("narrative_prefix", ""),
                narrative_suffix=item.get("narrative_suffix", ""),
                repeatable=item.get("repeatable", False),
            ))
        return cls(adventures)

    @classmethod
    def from_json(cls, path: str) -> "AdventureEngine":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        records = data.get("adventures", []) if isinstance(data, dict) else []
        return cls.from_records(records if isinstance(records, list) else [])

    def check_trigger(
        self,
        adventure_id: str,
        turn: int,
        metrics: Dict[str, int],
        region_id: str = "",
        character_luck: int = 50,
        roller: Optional[DiceRoller] = None,
    ) -> bool:
        """判定某奇遇是否触发。"""
        adv = self.adventures.get(adventure_id)
        if not adv:
            return False
        if not adv.repeatable and adventure_id in self._triggered:
            return False
        if turn < adv.required_turn_min or turn > adv.required_turn_max:
            return False
        # 地区匹配（如果有指定）
        if adv.region_hint and region_id and adv.region_hint != region_id:
            return False
        # metric 条件检查
        for mk, mcond in adv.required_metrics.items():
            val = metrics.get(mk, 0)
            if not self._eval_cond(val, mcond):
                return False
        # 概率判定（气运加成：运气越高，effective_prob 越高）
        luck_bonus = (character_luck - 50) / 500.0  # -0.1 ~ +0.1
        effective_prob = min(0.5, max(0.0, adv.trigger_probability + luck_bonus))
        if roller is None:
            roller = DiceRoller()
        return roller.chance(effective_prob, description=f"奇遇触发-{adv.id}")

    def _eval_cond(self, value: int, cond: str) -> bool:
        """评估比较式，如 '>=50', '<30', '==100'。"""
        cond = cond.strip()
        for op in (">=", "<=", "==", "!=", ">", "<"):
            if cond.startswith(op):
                try:
                    target = int(cond[len(op):].strip())
                except ValueError:
                    return False
                if op == ">=":
                    return value >= target
                if op == "<=":
                    return value <= target
                if op == "==":
                    return value == target
                if op == "!=":
                    return value != target
                if op == ">":
                    return value > target
                if op == "<":
                    return value < target
        return False

    def resolve_choice(
        self,
        adventure_id: str,
        choice_index: int,
        character_abilities: Dict[str, int],
        roller: Optional[DiceRoller] = None,
    ) -> AdventureResult:
        """执行奇遇的某个选项，返回结果。"""
        adv = self.adventures[adventure_id]
        choice = adv.choices[choice_index]
        if roller is None:
            roller = DiceRoller()

        # 属性检定
        ability_key = choice.required_ability or "luck"
        ability_score = character_abilities.get(ability_key, 50)
        modifier = round((ability_score - 50) / 5)
        roll = roller.d20(modifier=modifier, description=f"奇遇-{adv.id}-选择{choice_index}")
        success = roll.total >= choice.dc

        effects = dict(choice.success_effects if success else choice.fail_effects)
        narrative = choice.success_narrative if success else choice.fail_narrative
        item_reward = choice.item_reward if success else None

        self._triggered.add(adventure_id)

        return AdventureResult(
            adventure_id=adventure_id,
            title=adv.title,
            chosen_index=choice_index,
            choice_text=choice.text,
            success=success,
            roll_result=(roll.rolls[0], modifier, roll.total),
            effects=effects,
            narrative=narrative,
            item_reward=item_reward,
        )

    def choose_choice_index(
        self,
        adventure_id: str,
        character_abilities: Dict[str, int],
        metrics: Optional[Dict[str, int]] = None,
    ) -> int:
        """按当前盘面和角色能力选一个较稳妥的处置项。

        前端暂未提供玩家即时选择时，不能永远默认第一项；否则事件设计的分支形同虚设。
        这里用可解释的启发式：通过率、成败效果、当前国势短板共同决定自动处置。
        """
        adv = self.adventures[adventure_id]
        if not adv.choices:
            return 0
        metrics = metrics or {}

        def effect_score(effects: Dict[str, int]) -> float:
            score = 0.0
            for key, raw in (effects or {}).items():
                value = int(raw)
                if key in ("民心", "皇威"):
                    current = int(metrics.get(key, 50))
                    urgency = 1.7 if current <= 35 else 1.2 if current <= 55 else 1.0
                    score += value * urgency
                elif key in ("国库", "内库"):
                    current = int(metrics.get(key, 0))
                    urgency = 1.5 if current <= 120 else 1.0
                    score += (value / 12.0) * urgency
                elif key in ("hp", "max_hp"):
                    score += value / 8.0
                elif key in ("force", "wisdom", "charm", "luck", "cultivation", "exp"):
                    score += value / 5.0
            return score

        best_index = 0
        best_score = float("-inf")
        for index, choice in enumerate(adv.choices):
            ability_key = choice.required_ability or "luck"
            ability_score = int(character_abilities.get(ability_key, 50))
            if choice.required_min and ability_score < choice.required_min:
                gate_penalty = -25.0
            else:
                gate_penalty = 0.0
            modifier = round((ability_score - 50) / 5)
            needed_roll = int(choice.dc) - modifier
            success_chance = max(0.05, min(0.95, (21 - needed_roll) / 20.0))
            success_score = effect_score(choice.success_effects)
            fail_score = effect_score(choice.fail_effects)
            score = gate_penalty + success_chance * success_score + (1 - success_chance) * fail_score
            # 同分时保留更靠前的选项，保证稳定可复现。
            if score > best_score:
                best_index = index
                best_score = score
        return best_index

    def get_available_adventures(
        self,
        turn: int,
        metrics: Dict[str, int],
        region_id: str = "",
        character_luck: int = 50,
        roller: Optional[DiceRoller] = None,
        limit: int = 3,
    ) -> List[AdventureEvent]:
        """获取当前可能触发的奇遇列表（已做概率判定）。"""
        candidates = []
        for adv in self.adventures.values():
            if self.check_trigger(adv.id, turn, metrics, region_id, character_luck, roller):
                candidates.append(adv)
        return candidates[:limit]


def format_adventure_narrative(result: AdventureResult, character_name: str) -> str:
    """把奇遇结果格式化为叙事文本，供 LLM 使用。"""
    parts = [f"【奇遇·{result.title}】"]
    parts.append(f"{character_name}选择了：{result.choice_text}")
    if result.roll_result:
        d, m, t = result.roll_result
        sign = "+" if m >= 0 else ""
        parts.append(f"检定：d20={d}，修正{sign}{m}，合计{t}，{'处置得力' if result.success else '处置失措'}")
    if result.narrative:
        parts.append(result.narrative)
    if result.effects:
        eff_str = "、".join(f"{k}{'+' if v > 0 else ''}{v}" for k, v in result.effects.items())
        parts.append(f"效果：{eff_str}")
    if result.item_reward:
        parts.append(f"获得物品：{result.item_reward}")
    return "\n".join(parts)
