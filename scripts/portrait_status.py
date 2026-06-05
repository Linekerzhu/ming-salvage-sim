#!/usr/bin/env python3
"""扫 content/characters.json + web/public/portraits/，生成立绘进度表 docs/portrait-status.md。

可反复跑：生图进度变了重跑刷新表。
状态判定：
  已生成      —— 存在 clean 文件名 minister_<姓名>.png / consort_<姓名>.png
  待生成      —— 两者皆无
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent

import sys

sys.path.insert(0, str(ROOT))

from ming_sim.ranks import official_rank_for  # noqa: E402


OUT = ROOT / "web" / "public" / "portraits"
DNA_OUT = OUT / "_dna"
DOC = ROOT / "docs" / "portrait-status.md"
CHARACTERS = ROOT / "content" / "characters.json"

MING_POWER_ID = "ming"
CONSORT_RANKS = {"皇后", "贵人", "贵妃", "妃", "嫔"}
POOL_N = 20


def image_ratio(path: Path) -> str:
    try:
        from PIL import Image
    except Exception:
        return "未校验"
    if not path.exists():
        return "—"
    try:
        with Image.open(path) as image:
            w, h = image.size
        if h <= 0:
            return "异常"
        ratio = w / h
        if abs(ratio - 2 / 3) < 0.018:
            return f"2:3 OK ({w}x{h})"
        if abs(ratio - 3 / 4) < 0.018:
            return f"3:4 OK ({w}x{h})"
        return f"比例异常 ({w}x{h})"
    except Exception:
        return "无法读取"


def rank_cells(c: dict) -> tuple[str, str, int]:
    rank = official_rank_for(
        c.get("office", ""),
        c.get("office_type", ""),
        power_id=c.get("power_id", "ming"),
        faction=c.get("faction", ""),
    )
    label = str(c.get("rank_label") or c.get("rank") or rank.label)
    category = str(c.get("rank_category") or rank.category)
    try:
        grade = int(c.get("rank_grade") if c.get("rank_grade") is not None else rank.grade)
    except (TypeError, ValueError):
        grade = rank.grade
    return label, category, grade


def main() -> None:
    characters = json.loads(CHARACTERS.read_text("utf-8"))["characters"]

    clean = {p.name for p in OUT.glob("*.png")} if OUT.exists() else set()
    dna_clean = {p.name for p in DNA_OUT.glob("*.png")} if DNA_OUT.exists() else set()

    ministers = [
        (c, f"minister_{c['name']}.png", f"dna_{c['name']}.png")
        for c in characters
        if "rank" not in c
    ]
    consorts = [
        (c, f"consort_{c['name']}.png", f"dna_{c['name']}.png")
        for c in characters
        if c.get("power_id") == MING_POWER_ID and c.get("rank") in CONSORT_RANKS
    ]

    m_rows = ["| 人物 | 品级/类别 | 势力/派系 | 职位 | 立绘 | DNA | 比例 | 状态 |", "|---|---|---|---|---|---|---|---|"]
    m_done = 0
    m_dna_done = 0
    m_ratio_ok = 0
    for c, fn, dna_fn in ministers:
        cn = c["name"]
        office = c.get("office", "")
        faction = c.get("faction", "")
        rank_label, rank_category, grade = rank_cells(c)
        st = "已生成" if fn in clean else "待生成"
        if st == "已生成":
            m_done += 1
        dna_st = "已生成" if dna_fn in dna_clean else "待生成"
        if dna_st == "已生成":
            m_dna_done += 1
        ratio = image_ratio(OUT / fn)
        if ratio.startswith("2:3 OK"):
            m_ratio_ok += 1
        rank_text = f"{rank_label} / {rank_category}" + (f" / {grade}品" if grade else "")
        m_rows.append(f"| {cn} | {rank_text} | {faction} | {office} | `{fn}` | {dna_st} | {ratio} | {st} |")
    m_n = len(ministers)

    c_rows = ["| 人物 | 位分/类别 | 派系 | 位分/职位 | 立绘 | DNA | 比例 | 状态 |", "|---|---|---|---|---|---|---|---|"]
    c_person_done = 0
    c_dna_done = 0
    c_ratio_ok = 0
    for c, fn, dna_fn in consorts:
        cn = c["name"]
        office = c.get("office", "")
        faction = c.get("faction", "")
        rank_label, rank_category, _grade = rank_cells(c)
        st = "已生成" if fn in clean else "待生成"
        if st == "已生成":
            c_person_done += 1
        dna_st = "已生成" if dna_fn in dna_clean else "待生成"
        if dna_st == "已生成":
            c_dna_done += 1
        ratio = image_ratio(OUT / fn)
        if ratio.startswith("2:3 OK"):
            c_ratio_ok += 1
        c_rows.append(f"| {cn} | {rank_label} / {rank_category} | {faction} | {office} | `{fn}` | {dna_st} | {ratio} | {st} |")
    c_person_n = len(consorts)

    # 后宫预设图池：consort_pool_1..20
    pool_have = sorted(
        int(p.name[len("consort_pool_"):-4])
        for p in OUT.glob("consort_pool_*.png")
        if p.name[len("consort_pool_"):-4].isdigit()
    ) if OUT.exists() else []
    c_done = len(pool_have)

    out = [
        "# 立绘生成进度",
        "",
        "> 自动生成：`.venv/bin/python scripts/portrait_status.py`。改图后重跑刷新。",
        "> 人员名单来源：`content/characters.json`。臣僚/外臣/流寇 = `minister_<中文名>.png`；开局后宫 = `consort_<中文名>.png`；后宫池 = `consort_pool_<N>.png`（不绑人）。",
        "",
        f"## 人物专属图（立绘 {m_done}/{m_n}，DNA {m_dna_done}/{m_n}，2:3 {m_ratio_ok}/{m_n}）",
        "",
        "\n".join(m_rows),
        "",
        f"## 开局后宫专属图（立绘 {c_person_done}/{c_person_n}，DNA {c_dna_done}/{c_person_n}，2:3 {c_ratio_ok}/{c_person_n}）",
        "",
        "\n".join(c_rows),
        "",
        f"## 后宫预设图池（{c_done}/{POOL_N} 槽已出图）",
        "",
        f"已出图槽位：{pool_have}",
        f"待补槽位：{[n for n in range(1, POOL_N + 1) if n not in pool_have]}",
        "",
    ]
    DOC.write_text("\n".join(out), encoding="utf-8")
    print(f"写 {DOC}  大臣立绘 {m_done}/{m_n} DNA {m_dna_done}/{m_n}  后宫池 {c_done}/{POOL_N}")


if __name__ == "__main__":
    main()
