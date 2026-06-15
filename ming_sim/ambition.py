"""NPC 持续私人目标（CK3 化 P8）：私心不再只是召对口风与旬日一招，而会逐月累进、终于成局。

每个在朝官员的 agenda 有 progress(0-100)，月初按其才/势/特质暗暗推进；满则"成局"，按 kind 兑现为
朝局上的真实后果，并归零再图：
  - climb 进取：资望已成 → 朝野属望大用（自身见重 emp_trust↑、本党得势），续谋。
  - enrich 自肥：赃私已著 → 或事泄被劾（贪墨败露事件 + 信任跌），或暂掩（积怨）。
  - protect 护党：经营已固 → 本党势力涨。
  - entrench 自重：根基已深 → 所将之镇离心涨（喂地方割据 P7）。
  - survive 自保：洗白渐成 → 罪迹渐隐（怨气降）。
  - revenge 夙怨：清议已张 → 对其政敌再加弹劾压力（政敌信任跌）。
"""

from __future__ import annotations

import random
from typing import Dict, List

from ming_sim.db import GameDB
from ming_sim.models import GameState


def _advance(intensity: int, ability: int, rng: random.Random) -> int:
    """月推进量：意向越炽、才力越强，谋事越快（带少量变数）。"""
    base = intensity * 0.12 + ability * 0.06
    return max(1, int(round(base * (0.7 + rng.random() * 0.6))))


def pursue_tick(db: GameDB, state: GameState, day: int) -> List[Dict[str, object]]:
    """月初：在朝官员暗暗推进私心；满 100 则成局兑现，返回成局事件。"""
    from ming_sim.timeflow import LEVEL_YELLOW, LEVEL_RED
    from ming_sim import court

    rng = random.Random((int(day) * 0x9E3779B9) % (2 ** 31))
    rows = db.conn.execute(
        "SELECT a.name, a.kind, a.intensity, a.progress, c.office, c.faction, c.ability "
        "FROM npc_agendas a JOIN characters c ON c.name=a.name "
        "WHERE a.status='active' AND c.status='active' AND c.power_id='ming' AND c.office_type!='后宫' "
        "ORDER BY a.intensity DESC LIMIT 40"
    ).fetchall()
    events: List[Dict[str, object]] = []
    matured = 0
    for r in rows:
        name = str(r["name"])
        prog = int(r["progress"] or 0) + _advance(int(r["intensity"] or 50), int(r["ability"] or 50), rng)
        if prog < 100:
            db.conn.execute("UPDATE npc_agendas SET progress=? WHERE name=?", (min(99, prog), name))
            continue
        if matured >= 2:  # 一月至多两桩成局，节奏可控
            db.conn.execute("UPDATE npc_agendas SET progress=95 WHERE name=?", (name,))
            continue
        db.conn.execute("UPDATE npc_agendas SET progress=0 WHERE name=?", (name,))  # 成局后归零再图
        matured += 1
        kind = str(r["kind"])
        office, faction = str(r["office"] or ""), str(r["faction"] or "")
        ev = _mature(db, state, name, kind, office, faction, day, court, LEVEL_YELLOW, LEVEL_RED, rng)
        if ev:
            events.append(ev)
    db.conn.commit()
    return events


def _mature(db, state, name, kind, office, faction, day, court, YELLOW, RED, rng):
    real = faction and faction not in ("无", "中立")
    if kind == "climb":
        court._adjust_char(db, name, emp_trust=+5)
        if real:
            db.adjust_factions({faction: {"satisfaction": 2, "leverage": 1}})
        db.record_log(state, f"【资望渐隆】{office}{name}声望日著，朝野属望大用。")
        return {"level": YELLOW, "kind": "ambition",
                "title": f"资望渐隆：{name}", "ref_kind": "character", "ref_id": name, "day": day,
                "detail": f"{office}{name}积资结援，已为朝野所重——大用之议渐起。"}
    if kind == "enrich":
        if rng.random() < 0.5:  # 事泄被劾
            court._adjust_char(db, name, emp_trust=-8, grievance=+5)
            db.conn.execute("UPDATE characters SET integrity=MAX(0, integrity-6) WHERE name=?", (name,))
            db.record_log(state, f"【贪墨败露】{office}{name}侵蚀事泄，物议沸然。")
            return {"level": RED, "kind": "ambition",
                    "title": f"贪墨败露：{name}", "ref_kind": "character", "ref_id": name, "day": day,
                    "detail": f"{office}{name}久来侵蚀钱粮，今事泄被讦——查办、薄惩、还是回护？"}
        court._adjust_char(db, name, grievance=+3)
        return None
    if kind == "protect" and real:
        db.adjust_factions({faction: {"leverage": 3, "satisfaction": 2}})
        db.record_log(state, f"【植党已固】{office}{name}经营{faction}日固。")
        return {"level": YELLOW, "kind": "ambition",
                "title": f"植党已固：{faction}", "ref_kind": "character", "ref_id": name, "day": day,
                "detail": f"{office}{name}经营本党、安插要津，{faction}之势愈固。"}
    if kind == "entrench":
        # 自重者：所将之镇离心涨（喂地方割据 P7）。
        db.conn.execute("UPDATE armies SET autonomy=MIN(100, autonomy+6) WHERE commander=? AND owner_power='ming'",
                        (name,))
        db.record_log(state, f"【拥兵自固】{office}{name}厚结部曲、根基日深。")
        return {"level": YELLOW, "kind": "ambition",
                "title": f"拥兵自固：{name}", "ref_kind": "character", "ref_id": name, "day": day,
                "detail": f"{office}{name}厚自封殖、固结部曲，其镇离心又增。"}
    if kind == "survive":
        court._adjust_char(db, name, grievance=-6)
        return None
    if kind == "revenge":
        rv = court.rivals_of(db, name, limit=1)
        if rv:
            court._adjust_char(db, rv[0]["name"], emp_trust=-5)
            court.adjust_opinion(db, name, rv[0]["name"], -6, "清议相攻", day=day)
            return {"level": YELLOW, "kind": "ambition",
                    "title": f"清议相攻：{name}劾{rv[0]['name']}", "ref_kind": "character",
                    "ref_id": rv[0]["name"], "day": day,
                    "detail": f"{office}{name}伸张清议、连章劾{rv[0]['name']}，欲去之而后快。"}
    return None
