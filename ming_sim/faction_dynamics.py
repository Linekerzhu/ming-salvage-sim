"""派系流动（CK3 化 P5）：派系不再是死的收益数字——成员会改换门庭、分裂的党会内耗。

长在关系网（P1）+ 性格（P4）之上：
  - **改换门庭（defection）**：与异党要人交厚、对本党失望、又生性善观风色的官员，会投向其奥援所在之党。
    中立骑墙者最易被拉拢入党；委身之党则坐大（势力涨），旧党失人（势力跌），旧党同僚视其为背主（好感跌）。
  - **党内倾轧（strife）**：内部有深怨对立的派系，是分裂之家——势力与满意逐月被内耗蚀蚀。
"""

from __future__ import annotations

import random
from typing import Dict, List

from ming_sim.db import GameDB
from ming_sim.models import GameState

_REAL = lambda f: bool(f) and f not in ("无", "中立")  # noqa: E731


def _strongest_cross_ally(db: GameDB, name: str, own: str):
    """返回 (拉拢之党, 该党要人, 好感)——此人最交厚的异党奥援。无则 None。"""
    from ming_sim import court
    best = None
    for a in court.allies_of(db, name, limit=6, threshold=30):
        row = db.conn.execute(
            "SELECT faction FROM characters WHERE name=? AND status='active' AND power_id='ming'",
            (a["name"],)).fetchone()
        if row is None:
            continue
        fac = str(row["faction"] or "")
        if _REAL(fac) and fac != own:
            if best is None or a["opinion"] > best[2]:
                best = (fac, a["name"], int(a["opinion"]))
    return best


def defection_tick(db: GameDB, state: GameState, day: int) -> List[Dict[str, object]]:
    """月初至多 1 人改换门庭。返回事件列表。"""
    from ming_sim.timeflow import LEVEL_YELLOW
    from ming_sim import court
    try:
        from ming_sim.traits import trait_bias
    except ImportError:
        trait_bias = None

    rng = random.Random((int(day) * 0xC2B2AE35) % (2 ** 31))
    rows = db.conn.execute(
        "SELECT name, office, faction FROM characters "
        "WHERE status='active' AND power_id='ming' AND office_type!='后宫' "
        "ORDER BY name"
    ).fetchall()
    cands = []
    for r in rows:
        name, own = str(r["name"]), str(r["faction"] or "")
        pull = _strongest_cross_ally(db, name, own)
        if pull is None:
            continue
        pull_fac, patron, pull_op = pull
        own_sat = db.faction_satisfaction(own) if _REAL(own) else 28  # 中立委身极浅，最易被拉拢入党
        opportunist = 0.0
        if trait_bias:
            b = trait_bias(db, name)
            opportunist = b["deceptive"] + b["patronage"] * 0.5
        # 改换门庭意向分：奥援越厚、本党越失意、越善观风色，越想投靠。
        score = pull_op + (50 - own_sat) * 0.5 + opportunist * 25
        if score >= 72:
            cands.append((score, str(r["office"]), name, own, pull_fac, patron))
    if not cands:
        return []
    cands.sort(reverse=True)
    # 取意向最强者，仍留一点变数（八成概率落定）。
    _, office, name, own, pull_fac, patron = cands[0]
    if rng.random() >= 0.8:
        return []

    db.conn.execute("UPDATE characters SET faction=? WHERE name=?", (pull_fac, name))
    if _REAL(own):
        db.adjust_factions({own: {"leverage": -3, "satisfaction": -2}})
    db.adjust_factions({pull_fac: {"leverage": 3, "satisfaction": 2}})
    # 旧党同僚视其背主：好感跌。
    for r in db.conn.execute(
        "SELECT name FROM characters WHERE status='active' AND power_id='ming' AND faction=? AND name!=? LIMIT 6",
        (own, name)).fetchall():
        court.adjust_opinion(db, str(r["name"]), name, -10, "背党改投", day=day, reciprocal=False)
    db.conn.commit()
    db.record_log(state, f"【改换门庭】{office}{name}由{own or '中立'}投{pull_fac}（{patron}引为奥援）。")
    return [{
        "level": LEVEL_YELLOW, "kind": "defection",
        "title": f"改换门庭：{name} 投{pull_fac}",
        "detail": f"{office}{name}与{patron}交厚，由{own or '中立'}转投{pull_fac}。"
                  f"{pull_fac}得人坐大，旧党失援、目其为背主。",
        "ref_kind": "character", "ref_id": name, "day": day,
    }]


def strife_tick(db: GameDB, state: GameState, day: int) -> None:
    """党内倾轧：内部有深怨对立(opinion≤-50)的派系，势力/满意被内耗逐月蚀蚀。"""
    rows = db.conn.execute(
        "SELECT DISTINCT ca.faction AS fac FROM relationships r "
        "JOIN characters ca ON ca.name=r.a_name AND ca.status='active' AND ca.power_id='ming' "
        "JOIN characters cb ON cb.name=r.b_name AND cb.status='active' AND cb.power_id='ming' "
        "WHERE r.opinion<=-50 AND ca.faction=cb.faction AND ca.faction NOT IN ('无','中立')"
    ).fetchall()
    for r in rows:
        db.adjust_factions({str(r["fac"]): {"leverage": -1, "satisfaction": -2}})
    db.conn.commit()
