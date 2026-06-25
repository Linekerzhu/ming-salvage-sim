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


def seed_starting_consort(db: GameDB) -> None:
    """开局即在场的中宫皇后（周皇后，崇祯发妻），令后宫从第一天起就有「演员」——
    枕边风/进谗/争宠/对食皆有所附丽（pillar ③ 自始可玩，而非纳妃后才活）。幂等。
    周皇后史载贤德端正：integrity 高、faction=中宫（不党），故开局只是「活着的后宫」，
    要起波澜仍须玩家纳入有党之妃或权阉对食——史实上她正是清流一脉、与阉党不睦。"""
    from ming_sim.models import Character

    exists = db.conn.execute(
        "SELECT 1 FROM characters WHERE name=? LIMIT 1", ("周皇后",)
    ).fetchone()
    if exists is not None:
        return
    # 已有任一在场后宫则不强加（玩家自纳的局也算有演员）
    any_active = db.conn.execute(
        "SELECT 1 FROM characters WHERE office_type='后宫' AND status='active' "
        "AND power_id='ming' LIMIT 1"
    ).fetchone()
    if any_active is not None:
        return
    empress = Character(
        name="周皇后",
        office="皇后",
        office_type="后宫",
        faction="中宫",
        aliases=["周后", "中宫"],
        personal_skills=["端淑", "勤俭", "明大体"],
        loyalty=92, ability=58, integrity=88, courage=60,
        style="端庄贤淑，俭素自持，每以民艰、外戚干政为忧，与阉党不相能。",
        power_id="ming",
        status="active",
        summary="崇祯发妻，苏州人。性严慎，常规谏皇帝、远小人；母仪天下而不预外政，唯于阉宦干纪、外戚骄纵处必争。",
        rank="皇后", rank_category="harem",
        charm=78, wisdom=66, luck=55, birth_year=1611,
    )
    db.add_character(db.load_state(), empress, source="开局中宫")


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

    # 进谗：构陷其所恶的清流重臣（异党、高节者）。有对食阉党撑腰则谗言更毒。
    target = db.conn.execute(
        "SELECT name, office FROM characters WHERE status='active' AND power_id='ming' "
        "AND office_type!='后宫' AND integrity>=68 AND faction!=? "
        "ORDER BY integrity DESC LIMIT 1", (faction or "中立",)).fetchone()
    if target:
        partner = duishi_partner(db, name)
        bite = -7 if partner else -5
        court._adjust_char(db, str(target["name"]), emp_trust=bite, grievance=+4)
        db.conn.commit()
        backing = f"，内有对食{partner}于司礼监为之关说" if partner else ""
        db.record_log(state, f"【进谗】{name}于御前谗{target['name']}{backing}。")
        return [{
            "level": LEVEL_YELLOW, "kind": "harem_move",
            "title": f"枕边进谗：{name}谮{target['name']}",
            "detail": f"{name}于御前屡言{str(target['office'])}{target['name']}之短{backing}。"
                      f"谗言入耳，君臣之间渐生芥蒂——清流恐为所中。",
            "ref_kind": "character", "ref_id": str(target["name"]), "day": day,
        }]
    return []


# ── 对食（宦官后宫恶趣味 E2c）：内宠与权阉结为名义夫妻（魏忠贤×客氏式），内外勾连。──

def duishi_partner(db: GameDB, name: str) -> str:
    """某人的对食伴侣（relationships basis='对食'）；无则空。"""
    row = db.conn.execute(
        "SELECT CASE WHEN a_name=? THEN b_name ELSE a_name END AS p FROM relationships "
        "WHERE basis='对食' AND (a_name=? OR b_name=?) LIMIT 1", (name, name, name)).fetchone()
    return str(row["p"]) if row else ""


def _eligible_court_eunuch(db: GameDB):
    """可结对食的在朝权阉（优先司礼监/东厂等近御要津，未有对食者）。"""
    from ming_sim.identity import character_is_eunuch
    rows = db.conn.execute(
        "SELECT name, office, office_type, faction, sex FROM characters "
        "WHERE status='active' AND power_id='ming' AND office_type!='后宫' "
        "ORDER BY (office LIKE '%司礼监%') DESC, (office LIKE '%东厂%') DESC, ability DESC").fetchall()
    for r in rows:
        if not character_is_eunuch(
            r,
            sex=r["sex"],
            office=r["office"],
            office_type=r["office_type"],
            faction=r["faction"],
            allow_legacy_text_fallback=True,
        ):
            continue
        if duishi_partner(db, str(r["name"])):
            continue
        return r
    return None


def duishi_tick(db: GameDB, state: GameState, day: int) -> List[Dict[str, object]]:
    """月初：内宠与权阉结对食 / 已成对食者内外勾连自固 / 偶被察觉成丑闻。挂 rollover。"""
    from ming_sim.timeflow import LEVEL_YELLOW
    from ming_sim import court
    consorts = active_consorts(db)
    if not consorts:
        return []
    rng = random.Random((int(day) * 0x27D4EB2F ^ 0x5A17) % (2 ** 31))
    events: List[Dict[str, object]] = []

    # 已成对食：内外勾连——权阉之势涨、本党得援、彼此情坚；低概率被御前察觉成丑闻。
    pairs = db.conn.execute("SELECT a_name, b_name FROM relationships WHERE basis='对食'").fetchall()
    for pr in pairs:
        a, b = str(pr["a_name"]), str(pr["b_name"])
        if db.get_character_status(a)[0] != "active" or db.get_character_status(b)[0] != "active":
            db.conn.execute("DELETE FROM relationships WHERE basis='对食' AND a_name=? AND b_name=?", (a, b))
            continue
        try:
            from ming_sim.eunuch_power import adjust_eunuch_power
            adjust_eunuch_power(db, 1, "对食内外勾连", day=day)
        except Exception:
            pass
        court.adjust_opinion(db, a, b, +3, "对食情坚", day=day)
        if rng.random() < 0.18:  # 东窗事发
            court._adjust_char(db, a, grievance=+5)
            court._adjust_char(db, b, grievance=+5)
            try:
                from ming_sim.upgrade_schema import KV_SHI, adjust_belief
                adjust_belief(db, KV_SHI, -1, "对食丑闻·宫禁失体", day=day)
            except Exception:
                pass
            events.append({"level": LEVEL_YELLOW, "kind": "duishi_scandal",
                           "title": f"宫闱丑闻：{a}与{b}对食事泄",
                           "detail": f"{a}与{b}对食（结为夫妇）之事为外朝风闻，物议沸然，谓宫禁失体、内外交通。"
                                     "君威为之微损，二人惶惧。",
                           "ref_kind": "character", "ref_id": a, "day": day})
    db.conn.commit()
    if events:
        return events

    # 尚无对食且有内宠：得宠妃与近御权阉一拍即合，结为对食（约每数月一成）。
    if pairs or rng.random() >= 0.35:
        return events
    fav = max(consorts, key=lambda c: c["charm"])
    if duishi_partner(db, fav["name"]):
        return events
    eunuch = _eligible_court_eunuch(db)
    if eunuch is None:
        return events
    en = str(eunuch["name"])
    court.adjust_opinion(db, en, fav["name"], +28, "对食", day=day)  # basis 即标记对食关系
    db.record_log(state, f"【对食】{en}与{fav['name']}结为对食。")
    return [{"level": LEVEL_YELLOW, "kind": "duishi_form",
             "title": f"内外勾连：{en}与{fav['name']}结对食",
             "detail": f"司礼监{en}与{fav['name']}私结对食（宫中名义夫妇）。内宠得权阉为奥援、"
                       f"权阉借枕席通宫闱——魏珰客氏之故事，恐复见于今日。",
             "ref_kind": "character", "ref_id": en, "day": day}]
