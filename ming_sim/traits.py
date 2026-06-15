"""性格特质系统（CK3 化 P4）：把"被检测的文字标签"做成"驱动机制的真特质"。

原先 9 个特质（context.py 从 ability_logic 检出）只染对话口气，无落库、无数值后果。
本模块：① 落库 character_traits（含正/负特质 + 由品阶补正面特质）；② trait_bias 把特质
折成行为偏置（弹劾倾向/植党倾向/贪墨/口是心非），喂活的宫廷自主出招与召对；③ 皇帝（崇祯）
自身特质（猜忌多疑/刚愎/励精/薄恩）影响抉择后果——让"每个官员是真人"落到机制，并反哺前三支柱。
"""

from __future__ import annotations

from typing import Dict, List

from ming_sim.db import GameDB

# ── 特质目录：key → (valence, 一句话) ─────────────────────────────────────────
# valence: +正面 / -负面 / 0 中性派系性
TRAIT_DEFS: Dict[str, Dict[str, object]] = {
    "直言不讳": {"valence": 1, "desc": "遇不平当面挑破，敢谏敢弹"},
    "清廉自守": {"valence": 1, "desc": "操守严明，钱粮过手不染"},
    "干练": {"valence": 1, "desc": "才力优长，办事爽利"},
    "知兵": {"valence": 1, "desc": "晓畅军务，临阵能持"},
    "忠谨": {"valence": 1, "desc": "忠悃谨饬，托付可靠"},
    "门户之见": {"valence": 0, "desc": "人事清议先看门墙派系"},
    "结党营私": {"valence": -1, "desc": "植党自固，要害安插私人"},
    "猜忌多疑": {"valence": -1, "desc": "疑心深重，难容异己"},
    "贪墨成性": {"valence": -1, "desc": "钱粮人事手过留痕"},
    "沽名钓誉": {"valence": -1, "desc": "好博清名，做有声光的事"},
    "暴戾恣睢": {"valence": -1, "desc": "用重手、易过火"},
    "阳奉阴违": {"valence": -1, "desc": "口头答应，背后拖延变形"},
    "善观风色": {"valence": -1, "desc": "看风向行事，无定守"},
}

# ability_logic 文本里出现即检出的负/中性特质（沿用旧 9 项）
_LOGIC_MARKERS = ("阳奉阴违", "善观风色", "门户之见", "猜忌多疑", "结党营私",
                  "贪墨成性", "沽名钓誉", "暴戾恣睢", "直言不讳",
                  "铁面无私", "破釜沉舟", "知兵", "百战余生")
# ability_logic 里的近义词归一到目录 key
_ALIAS = {"铁面无私": "清廉自守", "破釜沉舟": "干练", "百战余生": "知兵"}

# 崇祯本人（玩家皇帝）的性格——史评定调：勤政而多疑刚愎、薄情寡恩。
EMPEROR_TRAITS: List[str] = ["励精图治", "猜忌多疑", "刚愎自用", "薄情寡恩"]


def derive_traits(ability_logic: str, integrity: int, ability: int,
                  loyalty: int) -> List[str]:
    """从 ability_logic 文本 + 品阶推导特质 key 列表（去重、最多 5）。"""
    out: List[str] = []
    for m in _LOGIC_MARKERS:
        if m in (ability_logic or ""):
            key = _ALIAS.get(m, m)
            if key in TRAIT_DEFS and key not in out:
                out.append(key)
    # 品阶补正面特质（数据稀疏时仍让人物"有性格"）
    if integrity >= 78 and "清廉自守" not in out:
        out.append("清廉自守")
    if ability >= 80 and "干练" not in out:
        out.append("干练")
    if loyalty >= 82 and "忠谨" not in out:
        out.append("忠谨")
    return out[:5]


def seed_traits(db: GameDB, *, force: bool = False) -> int:
    """从 npc_network.ability_logic + 品阶播种 character_traits。幂等。"""
    if not force and db.conn.execute("SELECT 1 FROM character_traits LIMIT 1").fetchone():
        return 0
    network = getattr(db.content, "npc_network", {}) or {}
    n = 0
    for r in db.conn.execute(
        "SELECT name, integrity, ability, loyalty FROM characters WHERE power_id='ming'"
    ).fetchall():
        name = str(r["name"])
        entry = network.get(name) if isinstance(network, dict) else None
        logic = str(entry.get("ability_logic") or "") if isinstance(entry, dict) else ""
        for t in derive_traits(logic, int(r["integrity"] or 50),
                               int(r["ability"] or 50), int(r["loyalty"] or 50)):
            db.conn.execute(
                "INSERT OR IGNORE INTO character_traits (name, trait, valence) VALUES (?,?,?)",
                (name, t, int(TRAIT_DEFS[t]["valence"])))
            n += 1
    db.conn.commit()
    return n


def traits_of(db: GameDB, name: str) -> List[Dict[str, object]]:
    rows = db.conn.execute(
        "SELECT trait, valence FROM character_traits WHERE name=?", (name,)
    ).fetchall()
    return [{"key": str(r["trait"]), "valence": int(r["valence"]),
             "desc": str(TRAIT_DEFS.get(str(r["trait"]), {}).get("desc", ""))} for r in rows]


def trait_bias(db: GameDB, name: str) -> Dict[str, float]:
    """把特质折成行为偏置（喂活的宫廷自主出招、召对）。
    impeach=弹劾倾向, patronage=植党荐人倾向, corruption=侵吞倾向, deceptive=口是心非倾向。"""
    keys = {str(r["trait"]) for r in
            db.conn.execute("SELECT trait FROM character_traits WHERE name=?", (name,)).fetchall()}
    b = {"impeach": 0.0, "patronage": 0.0, "corruption": 0.0, "deceptive": 0.0}
    if "直言不讳" in keys: b["impeach"] += 0.35
    if "暴戾恣睢" in keys: b["impeach"] += 0.25
    if "猜忌多疑" in keys: b["impeach"] += 0.20
    if "沽名钓誉" in keys: b["impeach"] += 0.15
    if "结党营私" in keys: b["patronage"] += 0.40
    if "门户之见" in keys: b["patronage"] += 0.25
    if "贪墨成性" in keys: b["corruption"] += 0.50
    if "阳奉阴违" in keys: b["deceptive"] += 0.40
    if "善观风色" in keys: b["deceptive"] += 0.30
    return b


def emperor_renshi_amplifier(db: GameDB) -> float:
    """崇祯猜忌多疑 → 任事意愿的负向波动被放大（崇祯陷阱加深）。返回乘子。"""
    return 1.35 if "猜忌多疑" in EMPEROR_TRAITS else 1.0
