"""骰子与随机数验算引擎。为传奇文字冒险提供检定、战斗随机、奇遇触发等核心随机能力。

设计原则：
- 所有随机调用必须可验算（seed + 调用序列可复现）
- 支持 d20 检定系统（属性检定、技能检定、事件检定）
- 支持多面骰组合（d4/d6/d8/d10/d12/d20/d100）
- 所有结果附带详细的计算过程，供 LLM 叙事使用
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple


@dataclass
class DiceResult:
    """一次骰子投掷的完整结果，含可验算过程。"""
    rolls: List[int]           # 每次骰子的原始点数
    modifier: int              # 修正值
    total: int                 # 最终总值
    dice_spec: str             # 骰子规格，如 "2d6+3"
    description: str           # 用途描述
    seed_hash: str = ""        # 用于验算的 seed 摘要
    critical: bool = False     # 是否大成功（d20=20）
    fumble: bool = False       # 是否大失败（d20=1）

    def __str__(self) -> str:
        detail = "+".join(str(r) for r in self.rolls)
        if self.modifier:
            detail += f"{'+' if self.modifier >= 0 else ''}{self.modifier}"
        flags = []
        if self.critical:
            flags.append("大成功")
        if self.fumble:
            flags.append("大失败")
        flag_str = f" [{' '.join(flags)}]" if flags else ""
        return f"【{self.description}】{self.dice_spec} = {detail} = {self.total}{flag_str}"


class DiceRoller:
    """可复现的骰子引擎。通过 seed + 调用计数保证同 seed 同序列结果一致。"""

    def __init__(self, seed: Optional[int] = None):
        self._master_seed = seed if seed is not None else random.randint(0, 2**32 - 1)
        self._call_count = 0
        self._history: List[DiceResult] = []

    def _rng(self, context: str = "") -> random.Random:
        """为每次调用生成独立的 RNG，基于 master_seed + call_count + context。
        这样即使调用顺序有微调，也能通过 seed_hash 事后验算。"""
        self._call_count += 1
        raw = f"{self._master_seed}|{self._call_count}|{context}"
        hash_val = int(hashlib.sha256(raw.encode()).hexdigest(), 16)
        rng = random.Random(hash_val)
        return rng

    def roll(self, dice_spec: str, modifier: int = 0, description: str = "") -> DiceResult:
        """解析并投掷骰子规格，如 "2d6", "1d20+5", "3d8-2"。

        规格格式：<数量>d<面数>[+/-修正]
        """
        spec = dice_spec.strip().lower()
        # 解析数量和面数
        parts = spec.split("d")
        if len(parts) != 2:
            raise ValueError(f"非法骰子规格：{dice_spec}（应为如 '2d6'）")
        num_dice = int(parts[0]) if parts[0] else 1
        rest = parts[1]
        # 解析修正
        extra_mod = 0
        if "+" in rest:
            faces_str, mod_str = rest.split("+", 1)
            extra_mod = int(mod_str)
        elif "-" in rest:
            faces_str, mod_str = rest.split("-", 1)
            extra_mod = -int(mod_str)
        else:
            faces_str = rest
        faces = int(faces_str)
        if num_dice < 1 or faces < 2:
            raise ValueError(f"非法骰子参数：{num_dice}d{faces}")

        rng = self._rng(description)
        rolls = [rng.randint(1, faces) for _ in range(num_dice)]
        total = sum(rolls) + modifier + extra_mod

        result = DiceResult(
            rolls=rolls,
            modifier=modifier + extra_mod,
            total=total,
            dice_spec=dice_spec,
            description=description or f"第{self._call_count}次投掷",
            seed_hash=f"{self._master_seed}:{self._call_count}",
            critical=(faces == 20 and any(r == 20 for r in rolls) and num_dice == 1),
            fumble=(faces == 20 and any(r == 1 for r in rolls) and num_dice == 1),
        )
        self._history.append(result)
        return result

    def d20(self, modifier: int = 0, description: str = "") -> DiceResult:
        return self.roll("1d20", modifier=modifier, description=description)

    def d100(self, description: str = "") -> DiceResult:
        return self.roll("1d100", description=description)

    def ability_check(self, ability_score: int, dc: int, description: str = "") -> Tuple[bool, DiceResult]:
        """属性检定：d20 + 属性修正 >= DC。

        属性修正 = (属性值 - 50) / 5，约 -10 到 +10 范围。
        """
        modifier = round((ability_score - 50) / 5)
        result = self.d20(modifier=modifier, description=description)
        success = result.total >= dc
        return success, result

    def contested_roll(self, score_a: int, score_b: int, description: str = "") -> Tuple[str, DiceResult, DiceResult]:
        """对抗检定：双方各投 d20 + 属性修正，高者胜。"""
        mod_a = round((score_a - 50) / 5)
        mod_b = round((score_b - 50) / 5)
        r_a = self.d20(modifier=mod_a, description=f"{description}-甲方")
        r_b = self.d20(modifier=mod_b, description=f"{description}-乙方")
        if r_a.total > r_b.total:
            return "甲方胜", r_a, r_b
        if r_b.total > r_a.total:
            return "乙方胜", r_a, r_b
        return "平手", r_a, r_b

    def luck_check(self, luck_score: int, description: str = "") -> Tuple[bool, DiceResult]:
        """气运检定：d100 <= 气运值 为成功。气运越高越易成功。"""
        result = self.d100(description=description)
        success = result.total <= luck_score
        return success, result

    def chance(self, probability: float, description: str = "") -> bool:
        """概率判定：probability 为 0.0-1.0 的概率值。"""
        rng = self._rng(description)
        return rng.random() < probability

    def pick(self, items: List[str], weights: Optional[List[float]] = None, description: str = "") -> str:
        """加权随机抽取一项。"""
        if not items:
            return ""
        rng = self._rng(description)
        if weights is None:
            return rng.choice(items)
        return rng.choices(items, weights=weights, k=1)[0]

    def history(self) -> List[DiceResult]:
        return list(self._history)

    def verify_seed(self, seed: int, expected_count: int) -> bool:
        """验算：检查当前状态是否与给定 seed 和调用次数匹配。"""
        return self._master_seed == seed and self._call_count == expected_count


# ── 全局默认骰子引擎 ──
_default_roller: Optional[DiceRoller] = None


def get_roller(seed: Optional[int] = None) -> DiceRoller:
    global _default_roller
    if _default_roller is None or seed is not None:
        _default_roller = DiceRoller(seed=seed)
    return _default_roller


def reset_roller(seed: Optional[int] = None) -> DiceRoller:
    global _default_roller
    _default_roller = DiceRoller(seed=seed)
    return _default_roller
