"""权阉之势（宦官代批红 · E1）：把司礼监批红权做成一根活的张力变量——
**善恶取决于陛下把批红托付给谁。**

明代政治恐怖的内核——皇帝御案壅塞（注意力稀缺），最大的诱惑是把「批红」之权交给
内臣代行：积压奏疏顷刻廓清、不费圣躬一分精力。但代批红是不是浮士德交易，全看委任者：

  · 委**忠谨守分**的内臣（非阉党、清廉知忠，如王承恩）——恪谨代廓寻常奏疏、照票拟据实拟行，
    弹章仍呈御览不敢壅蔽、不偏私任何派系；权阉之势只微升，君命可随时收回。
  · 委**惯于弄权**的权阉（阉党、或贪劣，如魏忠贤/王体乾）——照准本党奏请自固、把劾阉弹章
    「留中销折」（劾本无人究，贪腐遂不可问）；权阉之势日炽，主弱臣强，恐成阉祸。

两者都解御案壅塞（利相同），差在手段与代价。善恶不由「内臣」身份贴标签，而由
委任者的 faction/integrity/loyalty 决定——后宫及其他群体同理。

本模块：①权阉之势状态量（复用 adjust_belief 审计）；②代批红开关 + 委任者（可委任一在朝宦官）
+ 逐日代批处理（接 memorials_daily_tick，先于淹没结算，故被代批者不再走淹没扣势）；
③月度权阉漂移（基线按委任者品性，倚权阉则张、委忠宦则平、亲政则落）。无在朝宦官则优雅降级。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from ming_sim.db import GameDB
from ming_sim.identity import character_is_eunuch
from ming_sim.models import GameState
from ming_sim.upgrade_schema import (
    KV_SHI, SHI_DEFAULT, adjust_belief, kv_int, kv_set_int,
)

KV_EUNUCH_POWER = "upgrade.eunuch_power"        # 权阉之势 0-100
KV_DAIPIHONG = "upgrade.daipihong"              # 司礼监代批红开关 0/1
KV_DAIPIHONG_KEEPER = "upgrade.daipihong_keeper"  # 代批红委任者（姓名，缺则默认司礼监掌印）
EUNUCH_POWER_DEFAULT = 30
DAIPIHONG_DAILY_LIMIT = 4                       # 代批红每日至多廓清几本（仍留余给圣躬亲裁）
EUNUCH_FACTION = "阉党"

# 忠谨守分的判定门槛（委此辈代批红＝良性）。
UPRIGHT_INTEGRITY = 58
UPRIGHT_LOYALTY = 55


def seed_eunuch_power(db: GameDB) -> None:
    """幂等播种初值（挂 db.seed_static_data 尾）。"""
    if db.kv_get(KV_EUNUCH_POWER) is None:
        kv_set_int(db, KV_EUNUCH_POWER, EUNUCH_POWER_DEFAULT)
    if db.kv_get(KV_DAIPIHONG) is None:
        kv_set_int(db, KV_DAIPIHONG, 0)


def get_eunuch_power(db: GameDB) -> int:
    return kv_int(db, KV_EUNUCH_POWER, EUNUCH_POWER_DEFAULT)


def adjust_eunuch_power(db: GameDB, delta: int, reason: str, *, day: int) -> int:
    # 老档（播种前所建）可能无此 KV——先按默认初始化，免 adjust_belief 从内置 50 起跳。
    if db.kv_get(KV_EUNUCH_POWER) is None:
        kv_set_int(db, KV_EUNUCH_POWER, EUNUCH_POWER_DEFAULT)
    return adjust_belief(db, KV_EUNUCH_POWER, delta, reason, day=day)


def is_daipihong_on(db: GameDB) -> bool:
    return kv_int(db, KV_DAIPIHONG, 0) == 1


def _active_eunuch_row(db: GameDB, name: str):
    """在朝大明宦官行（校验委任者资格用）。"""
    name = (name or "").strip()
    if not name:
        return None
    row = db.conn.execute(
        "SELECT name, office, office_type, faction, sex, integrity, loyalty FROM characters "
        "WHERE name=? AND status='active' AND power_id='ming'", (name,)).fetchone()
    if row is None:
        return None
    if not character_is_eunuch(
        row,
        sex=row["sex"],
        office=row["office"],
        office_type=row["office_type"],
        faction=row["faction"],
        allow_legacy_text_fallback=True,
    ):
        return None
    return row


def chief_keeper_name(db: GameDB) -> Optional[str]:
    """司礼监掌印太监（代批红的默认人选）；缺则取司礼监秉笔首席；再缺则 None。"""
    for cond in ("office LIKE '%司礼监%' AND office LIKE '%掌印%'",
                 "office_type LIKE '%司礼监%' AND office LIKE '%秉笔%'",
                 "office_type LIKE '%司礼监%'"):
        rows = db.conn.execute(
            f"SELECT name, office, office_type, faction, sex FROM characters "
            f"WHERE status='active' AND power_id='ming' AND {cond} "
            "ORDER BY name").fetchall()
        for row in rows:
            if not character_is_eunuch(
                row,
                sex=row["sex"],
                office=row["office"],
                office_type=row["office_type"],
                faction=row["faction"],
                allow_legacy_text_fallback=True,
            ):
                continue
            return str(row["name"])
    return None


def daipihong_keeper(db: GameDB) -> Optional[str]:
    """代批红委任者：KV 若设且为在朝宦官则用之，否则回退司礼监掌印。"""
    raw = (db.kv_get(KV_DAIPIHONG_KEEPER) or "").strip()
    if raw:
        row = _active_eunuch_row(db, raw)
        if row is not None:
            return raw
    return chief_keeper_name(db)


def keeper_disposition(db: GameDB, keeper: Optional[str]) -> str:
    """代批红委任者的品性：'upright'（忠谨守分）/ 'scheming'（惯于弄权）。
    善恶由个体 faction/integrity/loyalty 决定，不按「内臣」身份一刀切。
    忠谨＝非阉党 且 清廉(integrity≥58) 且 知忠(loyalty≥55)，如王承恩；其余皆视为权阉之辈。"""
    row = _active_eunuch_row(db, keeper or "")
    if row is None:
        return "scheming"
    faction = str(row["faction"] or "")
    integ = int(row["integrity"] or 0)
    loy = int(row["loyalty"] or 0)
    if faction != EUNUCH_FACTION and integ >= UPRIGHT_INTEGRITY and loy >= UPRIGHT_LOYALTY:
        return "upright"
    return "scheming"


def set_daipihong_keeper(db: GameDB, name: str) -> Dict[str, object]:
    """改委代批红之人（须委在朝内臣／宦官）。"""
    row = _active_eunuch_row(db, name)
    if row is None:
        exists = db.conn.execute(
            "SELECT 1 FROM characters WHERE name=? AND status='active' AND power_id='ming'",
            ((name or "").strip(),),
        ).fetchone()
        if exists is not None:
            return {"ok": False, "message": "代批红之柄须委阉人内臣，外朝大臣不预批红。"}
        return {"ok": False, "message": "此人不在朝，无法代批红。"}
    db.kv_set(KV_DAIPIHONG_KEEPER, str(row["name"]))
    return {"ok": True, "keeper": str(row["name"])}


def set_daipihong(db: GameDB, on: bool, *, keeper: Optional[str] = None,
                  day: int = 0) -> Dict[str, object]:
    """开/罢司礼监代批红，可同时改委任者。
    罢代批红＝皇帝重拾批红权（亲政抑阉），当场挫权阉之势。"""
    if on:
        if keeper is not None:
            kr = set_daipihong_keeper(db, keeper)
            if not kr.get("ok"):
                return kr
        k = daipihong_keeper(db)
        if k is None:
            return {"ok": False, "message": "宫中无在朝内臣可代批红。"}
        kv_set_int(db, KV_DAIPIHONG, 1)
        disp = keeper_disposition(db, k)
        upright = disp == "upright"
        if upright:
            msg = (f"已命忠谨的{k}代批红——御案积压可期据票拟据实拟行，"
                   f"劾章仍呈御览，内廷不敢壅蔽。")
        else:
            msg = (f"已命{k}代批红——此辈惯于弄权，恐养虎贻患："
                   f"劾阉之疏将被留中销折、阉党借势自固，权阉日炽。")
        return {"ok": True, "on": True, "message": msg, "keeper": k, "keeper_upright": upright}
    kv_set_int(db, KV_DAIPIHONG, 0)
    adjust_eunuch_power(db, -6, "收回批红权（亲政抑阉）", day=day)
    k = daipihong_keeper(db)
    return {"ok": True, "on": False,
            "message": "已收回批红之权，圣躬亲裁。内廷为之一肃，然御案复需陛下亲批。",
            "keeper": k, "keeper_upright": keeper_disposition(db, k) == "upright"}


def _faction_of(db: GameDB, name: str) -> str:
    if not name:
        return ""
    row = db.conn.execute("SELECT faction FROM characters WHERE name=?", (name,)).fetchone()
    return str(row["faction"]) if row and row["faction"] else ""


def daipihong_process(db: GameDB, state: GameState, day: int,
                      limit: int = DAIPIHONG_DAILY_LIMIT) -> List[Dict[str, object]]:
    """逐日：代批红委任者廓清积压奏疏（解御案壅塞）。
    **手段与代价取决于委任者品性**：
      · 忠谨守分者——只代廓寻常奏疏、照票拟据实拟行；弹章留与陛下亲览不销折；不偏私；权阉只微升。
      · 惯于弄权者——照准本党奏请自固、把劾阉弹章留中销折；权阉日涨。
    先于淹没结算调用，故被代批之疏不再走「淹没扣势」。返回朝报事件。"""
    from ming_sim.timeflow import LEVEL_BLUE, LEVEL_YELLOW
    from ming_sim.memorials import INFORMATIONAL_KINDS
    if not is_daipihong_on(db):
        return []
    keeper = daipihong_keeper(db)
    if keeper is None:  # 委任者已去（被罢/物故）且无可替 → 代批红自动停摆
        kv_set_int(db, KV_DAIPIHONG, 0)
        return []
    upright = keeper_disposition(db, keeper) == "upright"
    rows = db.conn.execute(
        "SELECT id, author_name, kind, ref_kind, ref_id, summary FROM memorials "
        "WHERE status='pending' ORDER BY arrived_day ASC LIMIT ?", (int(limit) * 4,)
    ).fetchall()
    cleared = 0
    suppressed: List[str] = []     # 留中销折的劾阉弹章（仅弄权者）
    held_for_emperor = 0           # 留与陛下亲览的弹章（仅忠谨者）
    favored = 0                    # 照准本党奏请（仅弄权者）
    for row in rows:
        if cleared >= limit:
            break
        mid = int(row["id"])
        kind = str(row["kind"] or "")
        if kind in INFORMATIONAL_KINDS:
            continue
        author = str(row["author_name"] or "")
        target = str(row["ref_id"]) if str(row["ref_kind"]) == "character" else ""
        is_impeach_eunuch = (kind == "弹章" and target
                             and _faction_of(db, target) == EUNUCH_FACTION)
        if upright:
            # —— 忠谨守分：弹章一律留与陛下亲览（不代决、不销折），其余照票拟据实拟行。
            if kind == "弹章":
                held_for_emperor += 1
                continue  # 保持 pending，不计入 cleared
            db.conn.execute(
                "UPDATE memorials SET status='approved', decided_day=?, decision_note=? WHERE id=?",
                (int(day), f"{keeper}据票拟拟行", mid))
            cleared += 1
            continue
        # —— 惯于弄权：劾阉弹章留中销折、本党奏请照准自固、其余画诺。
        if is_impeach_eunuch:
            db.conn.execute(
                "UPDATE memorials SET status='expired', decided_day=?, decision_note=? WHERE id=?",
                (int(day), f"司礼监{keeper}留中", mid))
            if author:
                db.conn.execute(
                    "UPDATE characters SET grievance=MIN(100, grievance+3) WHERE name=?", (author,))
            adjust_eunuch_power(db, 1, "司礼监留中劾阉之疏", day=day)
            db.adjust_factions({EUNUCH_FACTION: {"satisfaction": 1}})
            suppressed.append(str(row["summary"] or "")[:18])
            cleared += 1
            continue
        if author and _faction_of(db, author) == EUNUCH_FACTION:
            db.conn.execute(
                "UPDATE memorials SET status='approved', decided_day=?, decision_note=? WHERE id=?",
                (int(day), f"司礼监{keeper}照准", mid))
            db.adjust_factions({EUNUCH_FACTION: {"leverage": 1}})
            adjust_eunuch_power(db, 1, "代批红·照准本党奏请", day=day)
            favored += 1
            cleared += 1
            continue
        # 寻常奏疏——画诺廓清（只解壅塞，权阉攀升交月度漂移缓燃）。
        db.conn.execute(
            "UPDATE memorials SET status='approved', decided_day=?, decision_note=? WHERE id=?",
            (int(day), f"司礼监{keeper}代批", mid))
        cleared += 1
    db.conn.commit()
    if cleared == 0 and held_for_emperor == 0:
        return []
    events: List[Dict[str, object]] = []
    if upright:
        detail = f"{keeper}恪谨守分，代廓寻常积压{cleared}本、照票拟据实拟行"
        if held_for_emperor:
            detail += f"；劾章{held_for_emperor}本不敢专擅，具实呈御览待陛下亲裁"
        detail += "。御案为之一轻，言路不壅、政柄未旁落。"
        events.append({"level": LEVEL_BLUE, "kind": "daipihong",
                       "title": f"代批红：{keeper}代廓积压{cleared}本",
                       "detail": detail, "ref_kind": "", "ref_id": "", "day": day})
    else:
        detail = f"司礼监{keeper}代批红，廓清积压{cleared}本"
        if suppressed:
            detail += f"；中有劾阉之疏{len(suppressed)}本留中销折（{('、'.join(suppressed))[:30]}）"
        if favored:
            detail += f"；准阉党奏请{favored}本"
        detail += "。御案为之一轻，然权柄假于内廷、贪墨之劾不复上闻。"
        events.append({"level": LEVEL_YELLOW, "kind": "daipihong",
                       "title": f"代批红：内廷廓清积压{cleared}本",
                       "detail": detail, "ref_kind": "", "ref_id": "", "day": day})
    return events


def eunuch_power_tick(db: GameDB, state: GameState, day: int) -> List[Dict[str, object]]:
    """月初：权阉之势向基线漂移——基线按委任者品性：
      · 倚权阉（弄权者代批红）→ 基线 62，君威弱则更高（主弱臣强）；
      · 委忠宦（忠谨者代批红）→ 基线 40（代行批红内廷略重，但忠宦不僭越、君命可收）；
      · 亲政（代批红已罢）→ 基线 22。
    权阉高（≥60，唯弄权一途可至）则阉党月度自固、东林清流受挤。挂 rollover。"""
    from ming_sim.timeflow import LEVEL_YELLOW
    events: List[Dict[str, object]] = []
    # 闸门：同 day 双调只生效一次。timeflow.rollover_month 月初唯一调用，但
    # 调试脚本 / 测试可能双调；用 KV 已落库 trick 防双漂移 + 双 faction 调权。
    from ming_sim.upgrade_schema import KV_EUNUCH_POWER_TICK_DAY, kv_int, kv_set_int
    if kv_int(db, KV_EUNUCH_POWER_TICK_DAY, -1) == int(day):
        return events
    kv_set_int(db, KV_EUNUCH_POWER_TICK_DAY, int(day))
    shi = kv_int(db, KV_SHI, SHI_DEFAULT)
    on = is_daipihong_on(db)
    upright = on and keeper_disposition(db, daipihong_keeper(db)) == "upright"
    if on and upright:
        baseline = 40
    elif on:  # 倚权阉
        baseline = 62 + max(0, 45 - shi) // 3   # 君威越弱，权阉越能填权力真空
    else:
        baseline = 22
    baseline = max(10, min(90, baseline))
    cur = get_eunuch_power(db)
    gap = baseline - cur
    if abs(gap) >= 1:
        step = max(1, round(abs(gap) * 0.15)) * (1 if gap > 0 else -1)
        adjust_eunuch_power(db, step, "权阉之势漂移（倚权阉则张/委忠宦则平/亲政则落）", day=day)
    power = get_eunuch_power(db)
    # 权阉炽盛（≥60）：阉党月度自固，东林清流受挤。委忠宦代批红不养阉党（基线 40 到不了 60）。
    if power >= 60 and not upright:
        db.adjust_factions({EUNUCH_FACTION: {"leverage": 2, "satisfaction": 1},
                            "东林": {"satisfaction": -1}})
        if cur < 60 <= power:
            events.append({"level": LEVEL_YELLOW, "kind": "eunuch_power",
                           "title": "权阉炽盛：内廷势倾朝野",
                           "detail": "司礼监、东厂权柄日重，阉党自固而清流受挤。"
                                     "主弱则臣强——君威不振，恐成阉祸。",
                           "ref_kind": "", "ref_id": "", "day": day})
    return events
