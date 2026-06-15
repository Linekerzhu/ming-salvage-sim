"""后宫自主政治（CK3 化 P6）：妃嫔不再是装饰，而是有诉求、会借枕边风干政、彼此争宠的活人。

后宫开局为空（须经纳妃才有人），故 harem_tick 无人时优雅空转。一旦有宠妃在侧：
  - **枕边风荐人**：为母家/本党要人吹风求官——抬其党势力，留枕席之私的隐患。
  - **进谗**：构陷其所恶的清流重臣——蚀其对帝信任、增其怨，谗言入耳即起波澜。
  - **争宠**：诸妃彼此为敌，新宠既立，旧人侧目（妃嫔间好感跌）。
全长在关系网（P1）上：宠妃与大臣、妃嫔彼此的好感都走 relationships 表。
"""

from __future__ import annotations

import random
from typing import Dict, List

from ming_sim.db import GameDB
from ming_sim.models import GameState

_REAL = lambda f: bool(f) and f not in ("无", "中立")  # noqa: E731


def active_consorts(db: GameDB) -> List[Dict[str, object]]:
    rows = db.conn.execute(
        "SELECT name, faction, charm, ability FROM characters "
        "WHERE office_type='后宫' AND status='active' AND power_id='ming'"
    ).fetchall()
    return [{"name": str(r["name"]), "faction": str(r["faction"] or ""),
             "charm": int(r["charm"] or 50), "ability": int(r["ability"] or 50)} for r in rows]


def consort_agenda(faction: str, ability: int) -> str:
    """宠妃的暗中诉求（决定其枕边风方向）。"""
    if _REAL(faction):
        return "进荐母家本党、固其外朝奥援"
    if ability >= 70:
        return "干预朝政、植自己的声气"
    return "固宠承欢、排挤新进"


def harem_tick(db: GameDB, state: GameState, day: int) -> List[Dict[str, object]]:
    """月初：在侧宠妃至多一记枕边风（荐人/进谗）+ 诸妃争宠。无妃则空转。"""
    from ming_sim.timeflow import LEVEL_YELLOW
    from ming_sim import court

    consorts = active_consorts(db)
    if not consorts:
        return []
    rng = random.Random((int(day) * 0xD1B54A35) % (2 ** 31))
    events: List[Dict[str, object]] = []

    # 争宠：诸妃相忌——彼此好感缓跌（新宠既立，旧人侧目）。
    if len(consorts) >= 2:
        a, b = rng.sample(consorts, 2)
        court.adjust_opinion(db, a["name"], b["name"], -6, "争宠", day=day)

    # 枕边风：取最得宠者（charm 高者）出手。
    fav = max(consorts, key=lambda c: c["charm"])
    name, faction = fav["name"], fav["faction"]
    agenda = consort_agenda(faction, fav["ability"])

    # 进荐母家本党：抬本党势力 + 事件（留枕席之私的隐患）。
    if _REAL(faction) and rng.random() < 0.5:
        kin = db.conn.execute(
            "SELECT name, office FROM characters WHERE status='active' AND power_id='ming' "
            "AND faction=? AND office_type!='后宫' ORDER BY ability DESC LIMIT 1", (faction,)).fetchone()
        if kin:
            db.adjust_factions({faction: {"leverage": 2, "satisfaction": 1}})
            court.adjust_opinion(db, str(kin["name"]), name, +8, "宫闱奥援", day=day, reciprocal=False)
            db.record_log(state, f"【枕边风】{name}为{faction}{kin['name']}吹嘘求进。")
            return [{
                "level": LEVEL_YELLOW, "kind": "harem_move",
                "title": f"枕边风：{name}荐{kin['name']}",
                "detail": f"{name}夜阑为{faction}{str(kin['office'])}{kin['name']}美言求进。"
                          f"外戚内宠相结，其党愈炽——纳之则坐大，拒之则失欢。",
                "ref_kind": "character", "ref_id": name, "day": day,
            }]

    # 进谗：构陷其所恶的清流重臣（异党、高节者）。
    target = db.conn.execute(
        "SELECT name, office FROM characters WHERE status='active' AND power_id='ming' "
        "AND office_type!='后宫' AND integrity>=68 AND faction!=? "
        "ORDER BY integrity DESC LIMIT 1", (faction or "中立",)).fetchone()
    if target:
        court._adjust_char(db, str(target["name"]), emp_trust=-5, grievance=+4)
        db.conn.commit()
        db.record_log(state, f"【进谗】{name}于御前谗{target['name']}。")
        return [{
            "level": LEVEL_YELLOW, "kind": "harem_move",
            "title": f"枕边进谗：{name}谮{target['name']}",
            "detail": f"{name}于御前屡言{str(target['office'])}{target['name']}之短。"
                      f"谗言入耳，君臣之间渐生芥蒂——清流恐为所中。",
            "ref_kind": "character", "ref_id": str(target["name"]), "day": day,
        }]
    return []
