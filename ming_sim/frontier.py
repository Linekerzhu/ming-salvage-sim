"""地方割据制衡（CK3 化 P7）：边镇/督抚不再是听调的数字，而是有独立基盘的"封疆"。

欠饷日久、君威不振、将帅不忠、又生拥兵自重之志者，其镇渐生**离心（autonomy）**：
  - 自专日炽 → 阳奉阴违（忠诚续跌）、截留钱粮（国库少入，本镇自肥）。
  - 至尾大不掉（autonomy≥72）→ 触发"封疆跋扈"抉择：加饷羁縻 / 削权裁抑 / 暂事姑息。
中央与地方的张力是明末命脉；此层让"欠饷"不再只是数字，而会反噬为割据。
"""

from __future__ import annotations

import random
from typing import Dict, List

from ming_sim.db import GameDB
from ming_sim.models import GameState
from ming_sim.upgrade_schema import KV_SHI, SHI_DEFAULT, kv_int

KV_WARLORDS = "upgrade.warlord_queue"


def _clamp(v: int) -> int:
    return max(0, min(100, int(v)))


def _commander_entrenched(db: GameDB, commander: str) -> bool:
    """主帅是否怀拥兵自重之志（court 私心 kind=entrench）。"""
    row = db.conn.execute("SELECT kind FROM npc_agendas WHERE name=?", (commander,)).fetchone()
    return bool(row) and str(row["kind"]) == "entrench"


def warlord_queue(db: GameDB) -> List[Dict[str, object]]:
    import json
    try:
        d = json.loads(db.kv_get(KV_WARLORDS) or "[]")
        return d if isinstance(d, list) else []
    except ValueError:
        return []


def pop_warlord(db: GameDB) -> Dict[str, object] | None:
    import json
    q = warlord_queue(db)
    if not q:
        return None
    head = q.pop(0)
    db.kv_set(KV_WARLORDS, json.dumps(q, ensure_ascii=False))
    return head


def _push_warlord(db: GameDB, vac: Dict[str, object]) -> None:
    import json
    q = warlord_queue(db)
    if any(v.get("army_id") == vac["army_id"] for v in q):
        return
    q.append(vac)
    db.kv_set(KV_WARLORDS, json.dumps(q[-6:], ensure_ascii=False))


def _eunuch_active(db: GameDB, name: str) -> bool:
    """某人是否在朝内臣（监军须是宦官）。"""
    if not name:
        return False
    from ming_sim.eunuch import is_eunuch_like
    row = db.conn.execute(
        "SELECT office, office_type, status FROM characters WHERE name=?", (name,)).fetchone()
    return (bool(row) and str(row["status"]) == "active"
            and is_eunuch_like(str(row["office"] or ""), str(row["office_type"] or "")))


def dispatch_supervisor(db: GameDB, state: GameState, army_id: str, eunuch: str, day: int
                        ) -> Dict[str, object]:
    """遣监军太监镇某军（E4）：天子耳目就近钳制——抑割据，然掣肘军务、主帅受辱怨望、权阉伸入军。"""
    from ming_sim import court
    arow = db.conn.execute(
        "SELECT name, commander, supervisor FROM armies WHERE id=? AND owner_power='ming'",
        (army_id,)).fetchone()
    if arow is None:
        return {"ok": False, "message": "无此镇。"}
    if not _eunuch_active(db, eunuch):
        return {"ok": False, "message": "监军须遣在朝内臣（宦官）。"}
    db.conn.execute("UPDATE armies SET supervisor=? WHERE id=?", (eunuch, army_id))
    commander = str(arow["commander"] or "")
    if commander:
        court._adjust_char(db, commander, grievance=+5)   # 受太监监临之辱
    try:
        from ming_sim.eunuch_power import adjust_eunuch_power
        adjust_eunuch_power(db, 2, "遣监军（内廷入军）", day=day)
    except Exception:
        pass
    db.conn.commit()
    return {"ok": True, "army": str(arow["name"]), "supervisor": eunuch,
            "message": f"遣{eunuch}监{arow['name']}军——天子耳目就近钳制，然{commander or '主帅'}受制掣肘、心怀怨望。"}


def recall_supervisor(db: GameDB, state: GameState, army_id: str, day: int) -> Dict[str, object]:
    """撤监军：去其掣肘——主帅稍安，权阉略退。"""
    from ming_sim import court
    arow = db.conn.execute(
        "SELECT name, commander, supervisor FROM armies WHERE id=? AND owner_power='ming'",
        (army_id,)).fetchone()
    if arow is None or not str(arow["supervisor"] or ""):
        return {"ok": False, "message": "此镇并无监军。"}
    db.conn.execute("UPDATE armies SET supervisor='' WHERE id=?", (army_id,))
    commander = str(arow["commander"] or "")
    if commander:
        court._adjust_char(db, commander, grievance=-2)
    try:
        from ming_sim.eunuch_power import adjust_eunuch_power
        adjust_eunuch_power(db, -1, "撤监军", day=day)
    except Exception:
        pass
    db.conn.commit()
    return {"ok": True, "army": str(arow["name"]),
            "message": f"撤{arow['supervisor']}监军，{arow['name']}去其掣肘，{commander or '主帅'}稍安。"}


def autonomy_tick(db: GameDB, state: GameState, day: int) -> List[Dict[str, object]]:
    """月初：按欠饷/君威/忠诚/主帅志向更新各镇离心度，自专者阳奉阴违、截留钱粮。
    有监军者：天子耳目抑割据，然掣肘军务（士气沮/侵饷/主帅怨/权阉涨），偶诬报主帅成把柄。"""
    from ming_sim.timeflow import LEVEL_RED, LEVEL_YELLOW

    shi = kv_int(db, KV_SHI, SHI_DEFAULT)
    rng = random.Random((int(day) * 0x27D4EB2F) % (2 ** 31))
    events: List[Dict[str, object]] = []
    rows = db.conn.execute(
        "SELECT id, name, commander, arrears, maintenance_per_turn, loyalty, autonomy, supervisor "
        "FROM armies WHERE owner_power='ming' AND maintenance_per_turn>0"
    ).fetchall()
    for r in rows:
        maint = max(1, int(r["maintenance_per_turn"]))
        arrears_months = int(r["arrears"]) / maint
        loyalty = int(r["loyalty"] or 50)
        cur = int(r["autonomy"] or 0)
        commander = str(r["commander"] or "")
        # 离心漂移：欠饷越久、君威越弱、越不忠、主帅越想自重 → 自专日炽；反之渐归朝廷。
        drift = 0.0
        drift += max(0.0, arrears_months - 2) * 3.0       # 欠饷逾两月起涨
        drift += max(0.0, (45 - shi)) * 0.25              # 君威不振
        drift += max(0.0, (50 - loyalty)) * 0.20          # 将帅离心
        if _commander_entrenched(db, commander):
            drift += 6
        if arrears_months <= 0.5 and shi >= 60:
            drift -= 8                                    # 足饷 + 君威隆盛 → 慑服归朝
        # 监军太监坐镇（E4）：天子耳目抑割据，但掣肘军务、侵饷、主帅含怨、权阉伸入。
        supervisor = str(r["supervisor"] or "")
        if supervisor and _eunuch_active(db, supervisor):
            drift -= 12                                   # 监临钳制 → 抑离心
            db.conn.execute(
                "UPDATE armies SET morale=MAX(0, morale-2), arrears=arrears+? WHERE id=?",
                (max(1, maint // 4), r["id"]))            # 士气沮 + 监军侵饷
            if commander:
                from ming_sim import court
                court._adjust_char(db, commander, grievance=+1)
            try:
                from ming_sim.eunuch_power import adjust_eunuch_power
                adjust_eunuch_power(db, 1, "监军遍镇", day=day)
            except Exception:
                pass
            if commander and rng.random() < 0.2:          # 监军诬报主帅 → 锻造把柄
                try:
                    from ming_sim.intrigue import ensure_schema as _es
                    _es(db)
                    has = db.conn.execute(
                        "SELECT 1 FROM secrets WHERE holder=? AND known_to_crown=1", (commander,)).fetchone()
                    if not has:
                        db.conn.execute(
                            "INSERT INTO secrets(holder, kind, detail, severity, known_to_crown, discovered_day, used) "
                            "VALUES (?,?,?,?,1,?,0)",
                            (commander, "通敌", f"监军{supervisor}密劾其克饷通敌（未必属实）", 58, int(day)))
                        events.append({
                            "level": LEVEL_YELLOW, "kind": "supervisor_smear",
                            "title": f"监军密劾：{supervisor}劾{commander}",
                            "detail": f"监{r['name']}军之{supervisor}密劾主帅{commander}克饷通敌——未必属实，"
                                      "然把柄入御前，将帅愈寒、边事愈坏。",
                            "ref_kind": "character", "ref_id": commander, "day": day})
                except Exception:
                    pass
        elif supervisor:  # 监军已殁/去职 → 自动撤
            db.conn.execute("UPDATE armies SET supervisor='' WHERE id=?", (r["id"],))
        new = _clamp(cur + int(round(drift)))
        if new != cur:
            db.conn.execute("UPDATE armies SET autonomy=? WHERE id=?", (new, r["id"]))

        # 自专成形（≥45）持续截留钱粮（国库少入、本镇自肥）——经济代价每月默默生效。
        if new >= 45:
            db.conn.execute("UPDATE armies SET loyalty=MAX(0, loyalty-1) WHERE id=?", (r["id"],))
            state.metrics["国库"] = int(state.metrics.get("国库", 0)) - (1 + new // 30)

        # 事件只在「跨档上行」时报一次（避免每月刷屏）：安靖<45 / 自专45-71 / 跋扈≥72。
        old_band = 0 if cur < 45 else (1 if cur < 72 else 2)
        new_band = 0 if new < 45 else (1 if new < 72 else 2)
        if new_band > old_band and new_band == 1:
            events.append({
                "level": LEVEL_YELLOW, "kind": "autonomy",
                "title": f"渐成自专：{r['name']}",
                "detail": f"{r['name']}（{commander}）以欠饷为辞，截留钱粮、阳奉阴违，离心已著（自专{new}）。"
                          f"补饷可挽，再纵则尾大不掉。",
                "ref_kind": "army", "ref_id": str(r["id"]), "day": day,
            })
        elif new_band > old_band and new_band == 2:
            _push_warlord(db, {"army_id": r["id"], "army": str(r["name"]),
                               "commander": commander, "autonomy": new})
            events.append({
                "level": LEVEL_RED, "kind": "autonomy",
                "title": f"尾大不掉：{r['name']}跋扈",
                "detail": f"{r['name']}（{commander}）拥兵自重、几不奉诏，已成尾大不掉之势，亟待陛下裁断。",
                "ref_kind": "army", "ref_id": str(r["id"]), "day": day,
            })
    db.conn.commit()
    db.save_state(state)
    return events
