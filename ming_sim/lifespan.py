"""人物老化·病·死·继承（CK3 化 P3「王朝长河」）：让朝局自然新陈代谢。

每个人物自带 historical_death_year/month（史料卒年），原先从不触发——名册万年不变。
本模块每月初按卒年 + 高龄病故概率令在朝官员自然凋零：
  - 病逝 → status=dead（自动削职）、讣告入朝报、好感网哀荣涟漪（党羽悲恸、政敌伺机）、派系势力消长。
  - 若殁者居要职 → 入"继任"队列，由 court_events 的 succession 抉择让陛下点替（与活的宫廷/抉择联动）。
"""

from __future__ import annotations

import json
import random
from typing import Dict, List

from ming_sim.db import GameDB
from ming_sim.models import GameState

KV_VACANCIES = "upgrade.succession_vacancies"
MAX_DEATHS_PER_MONTH = 2  # 防一月齐殁，节奏可控

# 要职关键词：殁则触发继任抉择
_KEY_OFFICE = ("大学士", "尚书", "侍郎", "督师", "总督", "巡抚", "总兵", "都御史", "阁")


def _age(birth_year: int, year: int) -> int:
    return (year - int(birth_year)) if birth_year else 0


def _is_key_office(office: str) -> bool:
    return any(k in str(office or "") for k in _KEY_OFFICE)


def vacancies(db: GameDB) -> List[Dict[str, object]]:
    try:
        d = json.loads(db.kv_get(KV_VACANCIES) or "[]")
        return d if isinstance(d, list) else []
    except ValueError:
        return []


def _push_vacancy(db: GameDB, vac: Dict[str, object]) -> None:
    vs = vacancies(db)
    vs.append(vac)
    db.kv_set(KV_VACANCIES, json.dumps(vs[-8:], ensure_ascii=False))


def pop_vacancy(db: GameDB) -> Dict[str, object] | None:
    vs = vacancies(db)
    if not vs:
        return None
    head = vs.pop(0)
    db.kv_set(KV_VACANCIES, json.dumps(vs, ensure_ascii=False))
    return head


def _will_die(rng: random.Random, year: int, month: int, dy: int, dm: int, age: int) -> str:
    """返回死因（空串=不死）。史料卒年到点必殁；高龄另有递增病故概率。"""
    if dy and (year > dy or (year == dy and month >= (dm or 12))):
        return "寿数"
    if age >= 70:
        # 每月病故概率随龄递增：70→~1.2%，80→~5.2%，90→~9.2%。
        p = max(0.0, (age - 66) * 0.004)
        if rng.random() < p:
            return "高龄染疾"
    return ""


def mortality_tick(db: GameDB, state: GameState, day: int) -> List[Dict[str, object]]:
    """月初调用：在朝明廷官员按卒年/高龄自然凋零。返回讣告事件列表。"""
    from ming_sim.timeflow import LEVEL_YELLOW
    from ming_sim import court

    year, month = int(state.year), int(state.period)
    rng = random.Random((int(day) * 0x85EBCA6B) % (2 ** 31))
    rows = db.conn.execute(
        "SELECT name, office, office_type, faction, birth_year, "
        "historical_death_year AS dy, historical_death_month AS dm "
        "FROM characters WHERE status='active' AND power_id='ming' AND office_type!='后宫'"
    ).fetchall()
    events: List[Dict[str, object]] = []
    deaths = 0
    for r in rows:
        if deaths >= MAX_DEATHS_PER_MONTH:
            break
        name = str(r["name"])
        age = _age(int(r["birth_year"] or 0), year)
        cause = _will_die(rng, year, month, int(r["dy"] or 0), int(r["dm"] or 0), age)
        if not cause:
            continue
        office = str(r["office"] or "")
        office_type = str(r["office_type"] or "")
        faction = str(r["faction"] or "")
        # 好感网哀荣涟漪：党羽悲恸（怨气微增——痛失奥援），政敌伺机（无明面动作，仅记录）。
        allies = court.allies_of(db, name, limit=5)
        for a in allies:
            court._adjust_char(db, a["name"], grievance=+3)
        # 派系势力消长：本派失一人则势力降，宿党相对得隙。
        if faction and faction not in ("无", "中立"):
            db.adjust_factions({faction: {"leverage": -4, "satisfaction": -2}})
            rival = court.rivals_of(db, name, limit=1)
            if rival:
                rf = db.conn.execute("SELECT faction FROM characters WHERE name=?", (rival[0]["name"],)).fetchone()
                if rf and str(rf["faction"]) not in ("", "无", "中立", faction):
                    db.adjust_factions({str(rf["faction"]): {"leverage": +2}})
        age_txt = f"享年{age}" if age else "卒"
        db.set_character_status(state, name, "dead", f"{cause}，{age_txt}")
        db.conn.commit()
        deaths += 1
        detail = (f"{office}{name}{age_txt}，以{cause}卒于任上。"
                  + ("要缺出，亟须简替。" if _is_key_office(office) else "其缺由部院循例铨补。"))
        # 净身者全尸执念（E2a）：无宝者不得全尸，党羽哀恸更深；有宝者凑全尸稍慰。
        try:
            from ming_sim.eunuch_lore import burial_lament_on_death
            lament = burial_lament_on_death(db, state, name, day)
            if lament:
                detail += lament
        except Exception:
            pass
        events.append({
            "level": LEVEL_YELLOW, "kind": "obituary",
            "title": f"讣告：{name}{age_txt}",
            "detail": detail, "ref_kind": "character", "ref_id": name, "day": day,
        })
        db.record_log(state, f"【讣告】{office}{name}{age_txt}，{cause}。")
        if _is_key_office(office):
            _push_vacancy(db, {"office": office, "office_type": office_type,
                               "faction": faction, "deceased": name, "day": int(day)})
    return events
