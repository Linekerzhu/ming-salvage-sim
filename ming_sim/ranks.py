"""Official rank and costume inference for portrait prompts.

The game needs a stable visual rank even when offices are dynamic or slightly
fictional. Historical Ming rank details are therefore approximated into a
machine-readable costume tier: enough to keep color, buzi patch, belt, and
robe logic coherent without blocking custom titles.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class CostumeRank:
    grade: int
    label: str
    category: str
    color_rule: str
    buzi_rule: str
    belt_rule: str
    note: str = ""

    @property
    def has_grade(self) -> bool:
        return self.grade > 0


NO_RANK = CostumeRank(
    grade=0,
    label="无品级",
    category="unranked",
    color_rule="不穿正式明代品官补服，按身份设计民服、道袍、僧衣、商旅服或外部势力服装",
    buzi_rule="无官员补子",
    belt_rule="布带、皮带或身份相符的简素束带",
)


_CIVIL_BUZI = {
    1: "文官一品补子：仙鹤",
    2: "文官二品补子：锦鸡",
    3: "文官三品补子：孔雀",
    4: "文官四品补子：云雁",
    5: "文官五品补子：白鹇",
    6: "文官六品补子：鹭鸶",
    7: "文官七品补子：鸂鶒或低阶禽鸟",
    8: "文官八品补子：黄鹂或鹌鹑",
    9: "文官九品补子：鹌鹑",
}

_MILITARY_BUZI = {
    1: "武官一品补子：狮子",
    2: "武官二品补子：狮子",
    3: "武官三品补子：虎豹",
    4: "武官四品补子：虎豹",
    5: "武官五品补子：熊罴",
    6: "武官六品补子：彪",
    7: "武官七品补子：彪",
    8: "武官八品补子：犀牛",
    9: "武官九品补子：海马",
}


def _robe_color(grade: int) -> str:
    if grade <= 0:
        return NO_RANK.color_rule
    if grade <= 4:
        return "一至四品绯红/赤红圆领官袍，乌纱帽，深色朝靴"
    if grade <= 7:
        return "五至七品青蓝圆领官袍，乌纱帽，深色朝靴"
    return "八九品绿青圆领官袍，乌纱帽，布靴或素靴"


def _belt_rule(grade: int) -> str:
    if grade <= 0:
        return NO_RANK.belt_rule
    if grade <= 2:
        return "玉带或高阶金玉束带"
    if grade <= 4:
        return "金银束带或素金革带"
    if grade <= 7:
        return "素金/铜质束带，装饰克制"
    return "简素布带或低阶革带"


def _rank(grade: int, label: str, category: str, buzi: str, note: str = "") -> CostumeRank:
    grade = max(0, min(9, int(grade)))
    return CostumeRank(
        grade=grade,
        label=label,
        category=category,
        color_rule=_robe_color(grade),
        buzi_rule=buzi,
        belt_rule=_belt_rule(grade),
        note=note,
    )


def official_rank_for(office: str, office_type: str = "", power_id: str = "ming", faction: str = "") -> CostumeRank:
    """Infer a visual rank/costume tier from current office text.

    ``grade`` is 1-9 for Ming official ranks, 0 for no Ming-rank costume. The
    result is designed for portraits and UI sorting, not legal-history proof.
    """
    office = str(office or "").strip()
    office_type = str(office_type or "").strip()
    power_id = str(power_id or "ming").strip()
    faction = str(faction or "").strip()
    text = f"{office} {office_type} {faction}"

    if power_id != "ming" or office_type == "外臣":
        return CostumeRank(
            grade=0,
            label="外部势力无明制品级",
            category=f"foreign:{power_id or 'unknown'}",
            color_rule="不穿明制品官补服，按后金、蒙古、朝鲜、流寇等势力服制设计",
            buzi_rule="无明制补子",
            belt_rule="按其势力身份设计束带、甲胄或王公服饰",
        )
    if office_type == "后宫":
        return CostumeRank(
            grade=0,
            label=str(office or "后宫位分"),
            category="harem",
            color_rule="不穿外朝官服，按皇后/贵妃/妃嫔位分设计凤冠霞帔或宫装",
            buzi_rule="后宫纹样用凤、花卉、云纹，不用外朝官员补子",
            belt_rule="宫装织带、珠玉佩饰",
        )
    if re.search(r"司礼监|东厂|太监|宦官|内官|内廷|小火者|监军", text):
        if re.search(r"掌印|提督东厂|秉笔", text):
            label = "内廷高阶，无外朝品级"
            color = "高级太监服：赭红、白金或深蓝蟒纹内廷袍，黑纱内廷冠，可有披风"
        elif re.search(r"随堂|监军|信邸内官|太监", text):
            label = "内廷中阶，无外朝品级"
            color = "中级太监服：深蓝或青绿圆领内廷袍，小冠，绣云纹或暗纹"
        else:
            label = "内廷低阶，无外朝品级"
            color = "低级太监服：灰青、暗绿或旧蓝内廷袍，布带，朴素小帽"
        return CostumeRank(
            grade=0,
            label=label,
            category="eunuch",
            color_rule=color,
            buzi_rule="内廷纹样用云纹、蟒纹或素暗纹，不使用外朝文武补子",
            belt_rule="内廷束带，随层级用玉、金属或布带",
        )
    if re.search(r"待铨|未仕|诸生|江湖|掌教|武师|游侠|道长|法师|神医|商人|琴师|隐士|传教士|华商|蛊师|僧|刀客", text):
        return NO_RANK

    military = bool(
        office_type in {"边镇", "锦衣卫"}
        or re.search(r"总兵|副总兵|副将|游击|参将|守备|都指挥使|千户|百户|将军|伯|督师|经略|军|锦衣卫", text)
    )
    if military:
        if re.search(r"伯|督师|经略|总督|都督|都指挥使", text):
            grade = 2
        elif re.search(r"总兵|副总兵|副将|游击|参将|平贼将军", text):
            grade = 3
        elif re.search(r"千户", text):
            grade = 5
        elif re.search(r"百户|守备", text):
            grade = 6
        else:
            grade = 4
        return _rank(grade, f"武官视觉{grade}品", "military", _MILITARY_BUZI.get(grade, "武官补子按品级走兽"), "边镇/厂卫战斗角色可在官服外叠甲胄、披风或佩刀。")

    if re.search(r"首辅|次辅|大学士|尚书|左都御史|右都御史|总督|督师|经略", text):
        grade = 2
    elif re.search(r"侍郎|巡抚|参政|知府|少卿|少詹事", text):
        grade = 3 if re.search(r"侍郎|巡抚|参政", text) else 4
    elif re.search(r"郎中", text):
        grade = 5
    elif re.search(r"员外郎|主事", text):
        grade = 6
    elif re.search(r"给事中|御史|编修|检讨|知县|推官", text):
        grade = 7
    elif re.search(r"官场旧人|罢居|罢闲|前", text):
        grade = 5
    else:
        return CostumeRank(
            grade=0,
            label="无明确品级",
            category="unranked-office",
            color_rule="官职未明或原创差遣，不硬套明制品官服；按职责、地域和身份设计晚明服饰",
            buzi_rule="无明确补子；如需官样，可用素纹或低阶抽象禽鸟纹",
            belt_rule="简素束带",
            note="原创或未说明官位由画像系统自动生成衣服。",
        )
    return _rank(grade, f"文官视觉{grade}品", "civil", _CIVIL_BUZI.get(grade, "文官补子按品级禽鸟"))


def rank_prompt_fragment(office: str, office_type: str = "", power_id: str = "ming", faction: str = "") -> str:
    rank = official_rank_for(office, office_type, power_id=power_id, faction=faction)
    note = f" Note: {rank.note}" if rank.note else ""
    return (
        f"Rank/costume tier: {rank.label}. "
        f"Robe color and cut: {rank.color_rule}. "
        f"Chest/back patch: {rank.buzi_rule}. "
        f"Belt: {rank.belt_rule}.{note}"
    )
