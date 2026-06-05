"""战斗计算引擎。为传奇文字冒险提供个人对决、小规模冲突、奇袭暗算等战斗的数值框架。

核心设计：
- 个人战：基于 RPG 属性的回合制对决
- 军队战：在原 army 系统基础上叠加将领武力加成
- 暗算/刺杀：基于修为和智谋的隐蔽检定
- 所有计算可验算（依赖 dice.DiceRoller）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ming_sim.dice import DiceRoller


@dataclass
class Combatant:
    """战斗参与者（个人）。"""
    name: str
    force: int = 50           # 武力：直接攻击力
    wisdom: int = 50          # 智谋：战术、识破陷阱
    charm: int = 50           # 魅力：士气、援军号召
    luck: int = 50            # 气运：暴击、闪避
    cultivation: int = 0      # 修为：0-100，传奇武学/修仙修为
    hp: int = 100             # 生命值
    max_hp: int = 100
    equipment_bonus: Dict[str, int] = field(default_factory=dict)  # 装备加成

    def effective_force(self) -> int:
        bonus = self.equipment_bonus.get("force", 0)
        cult_mult = 1.0 + self.cultivation / 200.0
        return round((self.force + bonus) * cult_mult)

    def effective_defense(self) -> int:
        """防御力 = 武力*0.3 + 智谋*0.2 + 修为*0.1"""
        f_bonus = self.equipment_bonus.get("force", 0)
        w_bonus = self.equipment_bonus.get("wisdom", 0)
        return round((self.force + f_bonus) * 0.3 + (self.wisdom + w_bonus) * 0.2 + self.cultivation * 0.1)

    def crit_rate(self) -> float:
        """暴击率 = 气运/200 + 修为/400，上限 25%"""
        base = self.luck / 200.0 + self.cultivation / 400.0
        return min(0.25, base)

    def dodge_rate(self) -> float:
        """闪避率 = 修为/300 + 气运/400，上限 20%"""
        base = self.cultivation / 300.0 + self.luck / 400.0
        return min(0.20, base)

    def is_alive(self) -> bool:
        return self.hp > 0


@dataclass
class CombatRound:
    """单回合战斗记录。"""
    round_num: int
    attacker: str
    defender: str
    attack_roll: int
    defense_roll: int
    base_damage: int
    is_crit: bool
    is_dodge: bool
    final_damage: int
    defender_hp_after: int
    narrative: str


@dataclass
class CombatResult:
    """一场战斗的完整结果。"""
    winner: Optional[str]
    loser: Optional[str]
    rounds: List[CombatRound]
    total_rounds: int
    a_start_hp: int
    b_start_hp: int
    a_end_hp: int
    b_end_hp: int
    summary: str


def _compute_damage(attacker: Combatant, defender: Combatant, roller: DiceRoller, description: str) -> Tuple[int, bool, bool, int, int]:
    """计算一次攻击的伤害。返回 (最终伤害, 是否暴击, 是否闪避, 攻击roll, 防御roll)"""
    # 攻击骰：d20 + 武力修正
    atk_mod = round((attacker.effective_force() - 50) / 5)
    atk_roll = roller.d20(modifier=atk_mod, description=f"{description}-攻击")
    # 防御骰：d20 + 防御修正
    def_mod = round((defender.effective_defense() - 30) / 5)
    def_roll = roller.d20(modifier=def_mod, description=f"{description}-防御")

    # 闪避判定
    is_dodge = roller.chance(defender.dodge_rate(), description=f"{description}-闪避判定")
    if is_dodge:
        return 0, False, True, atk_roll.total, def_roll.total

    # 基础伤害 = 攻击力 - 防御力，至少1
    raw_damage = max(1, attacker.effective_force() - defender.effective_defense() // 2)
    # 攻击骰影响：atk_roll - 10 作为额外浮动
    float_damage = max(-5, min(5, atk_roll.total - 10))
    damage = raw_damage + float_damage

    # 暴击判定
    is_crit = roller.chance(attacker.crit_rate(), description=f"{description}-暴击判定")
    if is_crit:
        damage = round(damage * 1.5)

    return max(1, damage), is_crit, False, atk_roll.total, def_roll.total


def duel(
    a: Combatant,
    b: Combatant,
    roller: Optional[DiceRoller] = None,
    max_rounds: int = 20,
    description: str = "",
) -> CombatResult:
    """两人对决，回合制，直到一方倒下或达到最大回合数。

    每回合双方各攻击一次（同时出手），然后结算伤害。
    """
    if roller is None:
        roller = DiceRoller()

    a_hp, b_hp = a.hp, b.hp
    rounds: List[CombatRound] = []

    for rnum in range(1, max_rounds + 1):
        if not a.is_alive() or not b.is_alive():
            break

        # A 攻击 B
        dmg_a, crit_a, dodge_b, atk_a, def_b = _compute_damage(a, b, roller, f"{description}R{rnum}-A攻B")
        b_hp = max(0, b_hp - dmg_a)

        # B 攻击 A
        dmg_b, crit_b, dodge_a, atk_b, def_a = _compute_damage(b, a, roller, f"{description}R{rnum}-B攻A")
        a_hp = max(0, a_hp - dmg_b)

        # 生成叙事
        parts = []
        if dodge_b:
            parts.append(f"{a.name}攻向{b.name}，{b.name}身形一闪，堪堪避开")
        elif crit_a:
            parts.append(f"{a.name}暴起发难，重创{b.name}（{dmg_a}点）")
        else:
            parts.append(f"{a.name}击中{b.name}（{dmg_a}点）")

        if dodge_a:
            parts.append(f"{b.name}反击{a.name}，{a.name}轻松化解")
        elif crit_b:
            parts.append(f"{b.name}回手一击，{a.name}受创不轻（{dmg_b}点）")
        else:
            parts.append(f"{b.name}还击{a.name}（{dmg_b}点）")

        rounds.append(CombatRound(
            round_num=rnum,
            attacker=a.name,
            defender=b.name,
            attack_roll=atk_a,
            defense_roll=def_b,
            base_damage=dmg_a,
            is_crit=crit_a,
            is_dodge=dodge_b,
            final_damage=dmg_a,
            defender_hp_after=b_hp,
            narrative="；".join(parts),
        ))

    # 判定胜负
    if a_hp > 0 and b_hp <= 0:
        winner, loser = a.name, b.name
    elif b_hp > 0 and a_hp <= 0:
        winner, loser = b.name, a.name
    elif a_hp > b_hp:
        winner, loser = a.name, b.name
    elif b_hp > a_hp:
        winner, loser = b.name, a.name
    else:
        winner = loser = None

    if winner:
        summary = f"【决斗结果】{a.name}（剩{a_hp}血） vs {b.name}（剩{b_hp}血），共{rnum}回合，{winner}胜。"
    else:
        summary = f"【决斗结果】{a.name}（剩{a_hp}血） vs {b.name}（剩{b_hp}血），共{rnum}回合，不分胜负。"

    return CombatResult(
        winner=winner,
        loser=loser,
        rounds=rounds,
        total_rounds=len(rounds),
        a_start_hp=a.max_hp,
        b_start_hp=b.max_hp,
        a_end_hp=a_hp,
        b_end_hp=b_hp,
        summary=summary,
    )


def assassinate(
    assassin: Combatant,
    target: Combatant,
    roller: Optional[DiceRoller] = None,
    base_dc: int = 15,
) -> Tuple[bool, str, List[str]]:
    """刺杀/暗算。返回 (是否成功, 结果摘要, 详细过程列表)。

    流程：
    1. 隐蔽检定（刺客智谋 vs DC）
    2. 若隐蔽成功 → 致命一击（伤害×2）
    3. 若隐蔽失败 → 正面交锋（伤害×0.5，目标有警觉加成）
    """
    if roller is None:
        roller = DiceRoller()

    logs: List[str] = []

    # 隐蔽检定
    mod = round((assassin.wisdom - 50) / 5)
    stealth = roller.d20(modifier=mod, description="刺杀-隐蔽检定")
    hidden = stealth.total >= base_dc
    logs.append(f"隐蔽检定：d20+{mod} = {stealth.total} {'≥' if hidden else '<'} DC{base_dc} → {'成功' if hidden else '失败'}")

    if hidden:
        # 致命一击
        dmg, crit, dodge, atk_r, def_r = _compute_damage(assassin, target, roller, "刺杀-致命一击")
        dmg = round(dmg * 2)
        logs.append(f"致命一击：伤害×2 = {dmg}点")
        if dodge:
            logs.append(f"但{target.name}直觉惊人，于千钧一发之际避开要害！")
            dmg = dmg // 4
        target.hp = max(0, target.hp - dmg)
        success = target.hp <= 0
        if success:
            logs.append(f"{target.name}当场毙命！刺杀成功。")
        else:
            logs.append(f"{target.name}负伤（剩{target.hp}血），但一息尚存。")
        return success, "刺杀成功" if success else "刺杀未遂", logs
    else:
        # 暴露，正面交锋
        dmg, crit, dodge, atk_r, def_r = _compute_damage(assassin, target, roller, "刺杀-正面交锋")
        dmg = round(dmg * 0.5)
        logs.append(f"暴露！正面交锋，伤害×0.5 = {dmg}点")
        if dodge:
            logs.append(f"{target.name}早有防备，完全闪开！")
            dmg = 0
        target.hp = max(0, target.hp - dmg)
        # 目标反击
        counter, c_crit, c_dodge, c_atk, c_def = _compute_damage(target, assassin, roller, "刺杀-目标反击")
        logs.append(f"{target.name}反击！")
        assassin.hp = max(0, assassin.hp - counter)
        logs.append(f"刺客{assassin.name}受创（剩{assassin.hp}血）。")
        success = target.hp <= 0
        return success, "刺杀成功" if success else "刺杀失败，刺客暴露", logs


def army_battle_modifier(
    commander_force: int,
    commander_wisdom: int,
    commander_cultivation: int,
    base_morale: int,
) -> Dict[str, int]:
    """根据将领属性计算军队战斗加成。

    返回 {morale_bonus, attack_bonus, defense_bonus}，供军队推演时叠加。
    """
    # 武力影响攻击力加成（0-20）
    attack_bonus = round((commander_force - 50) / 2.5)
    # 智谋影响防御力加成（0-15）
    defense_bonus = round((commander_wisdom - 50) / 3.3)
    # 修为影响士气加成（0-10）
    morale_bonus = round(commander_cultivation / 10)

    return {
        "morale_bonus": max(-15, min(20, morale_bonus)),
        "attack_bonus": max(-10, min(20, attack_bonus)),
        "defense_bonus": max(-10, min(15, defense_bonus)),
    }
