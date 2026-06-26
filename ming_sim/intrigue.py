"""宫斗阴谋 · P1：把柄（secrets/hooks）+ 厂卫侦缉（spymaster）+ 挟制（coerce）。

吸收 CK3 intrigue 内核（secrets → hooks → coerce，spymaster 侦缉，暴露风险），
落到明代最 iconic 的恐怖机器——**东厂/厂卫侦缉、诏狱、把柄挟制**：

  · **把柄（潜在 secret）**：每个有瑕之臣暗藏一桩可被拿捏的隐私（贪墨/结纳权阉/通敌养寇/怨望腹诽）。
    开局多为「潜在」（known_to_crown=0），无人知晓便奈何不得。
  · **厂卫侦缉（spymaster）**：东厂提督（魏忠贤式）秉权阉之势行侦缉——以一定胜算发掘某人把柄、密呈御前。
    侦得即成「把柄在手」（hook）。东厂愈盛（权阉高），侦缉愈酷烈。
  · **挟制（coerce hook）**：把柄在手，可挟制其人——胁其归附（畏祸输诚）、勒令致仕（以丑闻逼退）、
    或胁迫其办事（畏罪听用）。挟制留怨、伤其忠，是「以威驭下」而非「以德服人」。
  · **活的宫廷**：权阉自发以厂卫侦缉、持把柄挟制清流——无需玩家操盘，阴谋自转（CK3 living world）。

本模块：secrets 表 + seed 潜在把柄 + 东厂侦缉能力/investigate + coerce_with_secret +
changwei_tick（自发侦缉/挟制，挂 rollover）+ secrets_for（Person 卡）。无东厂则侦缉优雅降级。
"""

from __future__ import annotations

import hashlib
import random
from typing import Dict, List, Optional

from ming_sim.db import GameDB
from ming_sim.models import GameState

EUNUCH_FACTION = "阉党"

# 把柄类型（kind）→ 显示标签
SECRET_KINDS = {
    "贪墨": "贪墨纳贿",
    "朋党": "结纳权阉、植党",
    "通敌": "通敌养寇、克扣边饷",
    "怨望": "怨望腹诽、谤讪朝政",
    "私德": "私德有亏、旧案未了",
    "私情": "私情违礼、私德隐情",
}


def _stable_seed(day: int, salt: str, key: str) -> int:
    raw = f"{int(day)}:{salt}:{key}".encode("utf-8", "surrogatepass")
    return int.from_bytes(hashlib.blake2s(raw, digest_size=8).digest(), "big") & 0x7FFFFFFF


def ensure_schema(db: GameDB) -> None:
    db.conn.execute(
        """CREATE TABLE IF NOT EXISTS secrets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            holder TEXT NOT NULL,
            kind TEXT NOT NULL,
            detail TEXT NOT NULL DEFAULT '',
            severity INTEGER NOT NULL DEFAULT 50,
            known_to_crown INTEGER NOT NULL DEFAULT 0,
            discovered_day INTEGER NOT NULL DEFAULT 0,
            used INTEGER NOT NULL DEFAULT 0
        )""")
    db.conn.execute("CREATE INDEX IF NOT EXISTS idx_secrets_holder ON secrets(holder)")
    db.conn.commit()


def _derive_secret(faction: str, integrity: int, loyalty: int, grievance: int,
                   office: str, agenda_kind: str) -> Optional[Dict[str, object]]:
    """据信号推一桩最重的潜在把柄；清廉守分而无瑕者无把柄（厂卫无从罗织——除非构陷，见 P2）。"""
    fa, o = str(faction or ""), str(office or "")
    is_frontier = ("总兵" in o or "督师" in o or "镇" in o or "经略" in o)
    # 边镇低忠 → 通敌养寇/克饷（最重）
    if is_frontier and loyalty < 52:
        return {"kind": "通敌", "severity": 60 + min(20, 52 - loyalty),
                "detail": "私通关外、虚冒兵额、克扣边饷以自肥"}
    # 贪墨成性
    if integrity < 42 or agenda_kind == "enrich":
        return {"kind": "贪墨", "severity": 50 + min(22, max(0, 42 - integrity)),
                "detail": "受赇鬻爵、侵渔钱粮，赃证在外"}
    # 阉党 → 结纳权阉、朋党
    if EUNUCH_FACTION in fa or "阉" in fa:
        return {"kind": "朋党", "severity": 55, "detail": "厚结权阉、植党营私，旧附逆案"}
    # 深怨 → 怨望腹诽
    if grievance >= 55:
        return {"kind": "怨望", "severity": 42 + min(18, (grievance - 55) // 2),
                "detail": "怨望腹诽、私议朝政、谤讪乘舆"}
    return None


def seed_secrets(db: GameDB, *, force: bool = False) -> int:
    """幂等播种潜在把柄（开局多未发覆，须厂卫侦缉方现）。挂 db.seed_static_data。"""
    ensure_schema(db)
    if not force and db.conn.execute("SELECT 1 FROM secrets LIMIT 1").fetchone():
        return 0
    rows = db.conn.execute(
        "SELECT name, office, faction, integrity, loyalty, grievance FROM characters "
        "WHERE status='active' AND power_id='ming' AND office_type!='后宫'").fetchall()
    n = 0
    for r in rows:
        name = str(r["name"])
        try:
            from ming_sim.court import agenda_of
            ag = agenda_of(db, name)
            agk = str(ag["kind"]) if ag else ""
        except Exception:
            agk = ""
        sec = _derive_secret(str(r["faction"] or ""), int(r["integrity"] or 50),
                             int(r["loyalty"] or 50), int(r["grievance"] or 0),
                             str(r["office"] or ""), agk)
        if sec is None:
            continue
        if db.conn.execute("SELECT 1 FROM secrets WHERE holder=?", (name,)).fetchone():
            continue
        db.conn.execute(
            "INSERT INTO secrets(holder, kind, detail, severity, known_to_crown) VALUES (?,?,?,?,0)",
            (name, sec["kind"], sec["detail"], int(sec["severity"])))
        n += 1
    db.conn.commit()
    return n


def dongchang_chief(db: GameDB) -> Optional[str]:
    """东厂提督（厂卫侦缉之主）；无则取司礼监掌印代领，再无则 None。"""
    from ming_sim.identity import character_is_eunuch

    rows = db.conn.execute(
        "SELECT name, office, office_type, faction, sex FROM characters "
        "WHERE status='active' AND power_id='ming' AND office LIKE '%东厂%' "
        "ORDER BY ability DESC"
    ).fetchall()
    for row in rows:
        if character_is_eunuch(
            row,
            sex=row["sex"],
            office=row["office"],
            office_type=row["office_type"],
            faction=row["faction"],
            allow_legacy_text_fallback=True,
        ):
            return str(row["name"])
    try:
        from ming_sim.eunuch_power import chief_keeper_name
        return chief_keeper_name(db)
    except Exception:
        return None


def investigate_capability(db: GameDB) -> int:
    """厂卫侦缉能力：东厂提督在 + 权阉之势越盛，侦缉越酷烈。无厂卫则极弱。"""
    chief = dongchang_chief(db)
    if chief is None:
        return 10
    cap = 40
    try:
        from ming_sim.eunuch_power import get_eunuch_power
        cap += get_eunuch_power(db) // 2   # 权阉之势加持
    except Exception:
        pass
    row = db.conn.execute("SELECT ability FROM characters WHERE name=?", (chief,)).fetchone()
    if row:
        cap += int(row["ability"] or 0) // 5
    return min(95, cap)


def _concealment(db: GameDB, name: str) -> int:
    row = db.conn.execute(
        "SELECT integrity, ability, courage FROM characters WHERE name=?", (name,)).fetchone()
    if row is None:
        return 40
    return 25 + int(row["integrity"] or 0) // 4 + int(row["ability"] or 0) // 5 + int(row["courage"] or 0) // 6


def latent_secret_of(db: GameDB, name: str) -> Optional[Dict[str, object]]:
    ensure_schema(db)
    row = db.conn.execute(
        "SELECT id, kind, detail, severity, known_to_crown FROM secrets "
        "WHERE holder=? ORDER BY known_to_crown ASC, severity DESC LIMIT 1", (name,)).fetchone()
    if row is None:
        return None
    return {"id": int(row["id"]), "kind": str(row["kind"]), "detail": str(row["detail"]),
            "severity": int(row["severity"]), "known_to_crown": bool(row["known_to_crown"])}


def investigate(db: GameDB, target: str, day: int, *, rng: Optional[random.Random] = None
                ) -> Dict[str, object]:
    """厂卫侦缉某人：以胜算发掘其潜在把柄，密呈御前（known_to_crown=1）。
    返回 {ok, found, chief, secret?, message}。无把柄可查或侦缉落空均如实返回。"""
    ensure_schema(db)
    target = (target or "").strip()
    chief = dongchang_chief(db)
    if chief is None:
        return {"ok": False, "found": False, "message": "无东厂可遣，厂卫侦缉无从着手。"}
    if db.get_character_status(target)[0] != "active":
        return {"ok": False, "found": False, "chief": chief, "message": "此人不在朝，无从侦缉。"}
    sec = latent_secret_of(db, target)
    if sec is None:
        return {"ok": True, "found": False, "chief": chief,
                "message": f"东厂{chief}遍缉{target}，竟无实据可指——其人或谨饬，或藏匿深。"}
    if sec["known_to_crown"]:
        return {"ok": True, "found": True, "chief": chief, "secret": sec, "already": True,
                "message": f"{target}之「{SECRET_KINDS.get(sec['kind'], sec['kind'])}」把柄久在御前掌握。"}
    rng = rng or random.Random(_stable_seed(day, "investigate", target))
    margin = investigate_capability(db) - _concealment(db, target) + rng.randint(-15, 25)
    if margin <= 0:
        return {"ok": True, "found": False, "chief": chief,
                "message": f"东厂{chief}密缉{target}，一时未得确证——其事隐秘，尚需再查。"}
    db.conn.execute("UPDATE secrets SET known_to_crown=1, discovered_day=? WHERE id=?",
                    (int(day), int(sec["id"])))
    db.conn.commit()
    sec["known_to_crown"] = True
    return {"ok": True, "found": True, "chief": chief, "secret": sec,
            "message": (f"东厂{chief}侦得{target}「{SECRET_KINDS.get(sec['kind'], sec['kind'])}」之实："
                        f"{sec['detail']}。把柄密呈御前——其人自此在握。")}


# 挟制方式
COERCE_MODES = {
    "submit": "胁其输诚归附",
    "retire": "以丑闻逼其致仕",
    "serve": "胁迫其听用办事",
}


def coerce_with_secret(db: GameDB, state: GameState, holder: str, mode: str, day: int
                       ) -> Dict[str, object]:
    """凭已掌握的把柄挟制其人。须该把柄已 known_to_crown。以威驭下：留怨、伤忠，但即收其用。
    submit=畏祸输诚（faction→皇党倾、emp_trust↑、grievance↑）；retire=以丑闻逼退（致仕去职）；
    serve=胁迫听用（emp_trust↑、grievance↑、本回合可驱使）。"""
    ensure_schema(db)
    from ming_sim import court
    holder = (holder or "").strip()
    sec = latent_secret_of(db, holder)
    if sec is None or not sec["known_to_crown"]:
        return {"ok": False, "message": "未握其把柄，无从挟制——先令厂卫侦缉。"}
    if db.get_character_status(holder)[0] != "active":
        return {"ok": False, "message": "此人不在朝。"}
    label = SECRET_KINDS.get(sec["kind"], sec["kind"])
    if mode == "retire":
        old = court_political_office(db, holder)
        db.set_character_status(state, holder, "retired", f"以「{label}」把柄逼令致仕")
        try:
            from ming_sim.court import ripple_personnel
            ripple_personnel(db, holder, "oust", day=day)
        except Exception:
            pass
        db.conn.execute("UPDATE secrets SET used=1 WHERE id=?", (int(sec["id"]),))
        db.conn.commit()
        return {"ok": True, "mode": mode, "message": f"以「{label}」之柄逼{holder}自陈致仕，{old}一缺旷出。"}
    if mode == "submit":
        court._adjust_char(db, holder, emp_trust=+8, grievance=+10)
        db.conn.execute("UPDATE characters SET loyalty=MIN(100, loyalty+6) WHERE name=?", (holder,))
        # 畏祸输诚：若非皇党，向皇党倾（输诚效忠）
        row = db.conn.execute("SELECT faction FROM characters WHERE name=?", (holder,)).fetchone()
        if row and str(row["faction"] or "") not in ("皇党", "", "无", "中立"):
            db.conn.execute("UPDATE characters SET faction='皇党' WHERE name=?", (holder,))
        db.conn.execute("UPDATE secrets SET used=1 WHERE id=?", (int(sec["id"]),))
        db.conn.commit()
        return {"ok": True, "mode": mode,
                "message": f"以「{label}」之柄慑{holder}，其人惶惧输诚、俯首听命——然怨结于心。"}
    # serve（默认）：胁迫听用
    court._adjust_char(db, holder, emp_trust=+5, grievance=+8)
    db.conn.execute("UPDATE secrets SET used=1 WHERE id=?", (int(sec["id"]),))
    db.conn.commit()
    return {"ok": True, "mode": "serve",
            "message": f"以「{label}」之柄胁{holder}听用办事，其畏罪不敢不从——然衔恨在心。"}


def court_political_office(db: GameDB, name: str) -> str:
    row = db.conn.execute("SELECT office FROM characters WHERE name=?", (name,)).fetchone()
    return str(row["office"] or "") if row else ""


def secrets_for(db: GameDB, name: str) -> Optional[Dict[str, object]]:
    """Person 卡：某人已被掌握的把柄（仅 known_to_crown 才显，潜在把柄不剧透）。"""
    ensure_schema(db)
    row = db.conn.execute(
        "SELECT kind, detail, severity, used FROM secrets WHERE holder=? AND known_to_crown=1 "
        "ORDER BY severity DESC LIMIT 1", (name,)).fetchone()
    if row is None:
        return None
    return {"kind": str(row["kind"]), "label": SECRET_KINDS.get(str(row["kind"]), str(row["kind"])),
            "detail": str(row["detail"]), "severity": int(row["severity"]), "used": bool(row["used"])}


def preview_intrigue_effects(db: GameDB, name: str, *, ally: str = "") -> Dict[str, List[Dict[str, str]]]:
    """Read-only previews for Person-card intrigue actions."""
    ensure_schema(db)
    name = (name or "").strip()
    try:
        active = db.get_character_status(name)[0] == "active"
    except Exception:
        active = False
    chief = dongchang_chief(db)
    previews: Dict[str, List[Dict[str, str]]] = {}
    if active:
        previews["investigate"] = (
            [
                {"kind": "secret", "label": "可能得把柄", "tone": "good"},
                {"kind": "risk", "label": "不保证有实", "tone": "neutral"},
            ]
            if chief
            else [{"kind": "risk", "label": "无东厂可遣", "tone": "bad"}]
        )
        previews["fabricate"] = (
            [
                {"kind": "status", "label": "若成：下诏狱", "tone": "bad"},
                {"kind": "faction", "label": "权阉/阉党 +", "tone": "bad"},
                {"kind": "people", "label": "民心 -1", "tone": "bad"},
                {"kind": "risk", "label": "若败：势 -3", "tone": "bad"},
            ]
            if chief
            else [{"kind": "risk", "label": "无东厂可遣", "tone": "bad"}]
        )
    secret = secrets_for(db, name)
    if active and secret and not bool(secret.get("used")):
        previews["coerce_submit"] = [
            {"kind": "faction", "label": "归附皇党", "tone": "good"},
            {"kind": "court", "label": "忠诚 +6", "tone": "good"},
            {"kind": "court", "label": "怨气 +10", "tone": "bad"},
            {"kind": "secret", "label": "把柄消耗", "tone": "bad"},
        ]
        previews["coerce_serve"] = [
            {"kind": "court", "label": "畏罪听用", "tone": "good"},
            {"kind": "court", "label": "信任 +5", "tone": "good"},
            {"kind": "court", "label": "怨气 +8", "tone": "bad"},
            {"kind": "secret", "label": "把柄消耗", "tone": "bad"},
        ]
        previews["coerce_retire"] = [
            {"kind": "status", "label": "致仕去职", "tone": "good"},
            {"kind": "network", "label": "党羽怨怒", "tone": "bad"},
            {"kind": "network", "label": "政敌称快", "tone": "good"},
            {"kind": "secret", "label": "把柄消耗", "tone": "bad"},
        ]
    if active and ally:
        previews["discord"] = [
            {"kind": "network", "label": "关系骤跌", "tone": "good"},
            {"kind": "court", "label": "双方怨气 +3", "tone": "bad"},
            {"kind": "risk", "label": "忠正可识破", "tone": "bad"},
        ]
    return previews


def _is_pure(faction: str) -> bool:
    return "东林" in str(faction or "") or "清流" in str(faction or "")


def fabricate(db: GameDB, state: GameState, target: str, day: int, *,
              rng: Optional[random.Random] = None) -> Dict[str, object]:
    """罗织罪名构陷某人下诏狱（CK3 fabricate / 明季锻炼冤狱）。**与 P1 侦缉真把柄不同，此为凭空罗织。**
    成败系于厂卫之势 vs 目标清誉——**清誉越高越难陷、且陷之易暴露反噬**（君威损、清流声援、权阉受挫）。
    得逞→目标下诏狱(imprisoned)+伪造把柄+清流寒心、权阉益横；暴露→构陷不成、反伤君威。"""
    ensure_schema(db)
    from ming_sim import court
    from ming_sim.upgrade_schema import KV_SHI, adjust_belief
    target = (target or "").strip()
    chief = dongchang_chief(db)
    if chief is None:
        return {"ok": False, "message": "无东厂可遣，无从锻炼罗织。"}
    row = db.conn.execute(
        "SELECT integrity, faction FROM characters WHERE name=? AND status='active' AND power_id='ming'",
        (target,)).fetchone()
    if row is None:
        return {"ok": False, "message": "此人不在朝，无从构陷。"}
    integrity = int(row["integrity"] or 50)
    pure = _is_pure(str(row["faction"] or ""))
    prestige = integrity + (12 if pure else 0)   # 清誉护身：高节者难陷、陷则易露
    rng = rng or random.Random(_stable_seed(day, "fabricate", target))
    margin = investigate_capability(db) - prestige + rng.randint(-20, 20)
    if margin > 0:
        # 罗织得逞 → 下诏狱
        db.set_character_status(state, target, "imprisoned", f"东厂{chief}锻炼成狱（构陷）")
        db.conn.execute(
            "INSERT INTO secrets(holder, kind, detail, severity, known_to_crown, discovered_day, used) "
            "VALUES (?,?,?,?,1,?,1)",
            (target, "构陷", "厂卫锻炼之冤狱、罗织成罪（实无其事）", 72, int(day)))
        try:
            court.ripple_personnel(db, target, "oust", day=day)
        except Exception:
            pass
        db.adjust_factions({EUNUCH_FACTION: {"leverage": 2, "satisfaction": 1}, "东林": {"satisfaction": -2}})
        adjust_belief(db, KV_SHI, 1, "构陷立威（暴虐）", day=day)
        state.metrics["民心"] = max(0, int(state.metrics.get("民心", 0)) - 1)
        db.conn.commit()
        db.save_state(state)
        return {"ok": True, "success": True, "imprisoned": True, "chief": chief,
                "message": (f"东厂{chief}罗织{target}成罪、锻炼下诏狱。冤狱既兴，"
                            "清流为之夺气、权阉益横——以暴立威，恐失天下心。")}
    # 暴露反噬
    adjust_belief(db, KV_SHI, -3, "构陷暴露·反噬", day=day)
    db.adjust_factions({"东林": {"satisfaction": 3}, EUNUCH_FACTION: {"satisfaction": -2}})
    court._adjust_char(db, target, emp_trust=+6, grievance=-4)  # 蒙冤获同情、君心慰留
    db.conn.commit()
    db.save_state(state)
    return {"ok": True, "success": False, "chief": chief,
            "message": (f"罗织{target}事泄，物议哗然——清流交章声援，反伤君威。"
                        f"{'高节者清誉素著，本难诬陷。' if pure else ''}构陷不成。")}


def sow_discord(db: GameDB, state: GameState, a: str, b: str, day: int, *,
                rng: Optional[random.Random] = None) -> Dict[str, object]:
    """离间二人（三国志式反间）：挑其相疑、好感骤跌，党羽或由是离心。
    **笃实忠正者（清廉且知忠）易窥破反间**，离间打折、反损君威。"""
    ensure_schema(db)
    from ming_sim import court
    a, b = (a or "").strip(), (b or "").strip()
    if not a or not b or a == b:
        return {"ok": False, "message": "离间须指两人。"}
    rows = {str(r["name"]): r for r in db.conn.execute(
        "SELECT name, integrity, loyalty FROM characters WHERE name IN (?,?) AND status='active'",
        (a, b)).fetchall()}
    if a not in rows or b not in rows:
        return {"ok": False, "message": "二人须皆在朝。"}
    rng = rng or random.Random(_stable_seed(day, "discord", f"{a}:{b}"))
    resist = sum(int(rows[x]["integrity"] or 0) + int(rows[x]["loyalty"] or 0) for x in (a, b)) // 2
    drop = -(22 + rng.randint(0, 20))
    if resist >= 150 and rng.random() < 0.55:   # 皆笃实 → 窥破反间
        from ming_sim.upgrade_schema import KV_SHI, adjust_belief
        court.adjust_opinion(db, a, b, drop // 4, "离间(为所窥破)", day=day, reciprocal=True)
        adjust_belief(db, KV_SHI, -1, "反间为忠正所窥破", day=day)
        return {"ok": True, "success": False,
                "message": f"{a}与{b}皆笃实之臣，反间之计为所窥破，二人不为所动——反损君威。"}
    court.adjust_opinion(db, a, b, drop, "离间", day=day, reciprocal=True)
    court._adjust_char(db, a, grievance=+3)
    court._adjust_char(db, b, grievance=+3)
    db.conn.commit()
    return {"ok": True, "success": True,
            "message": f"密遣反间，挑{a}与{b}相疑——二人由是生隙、彼此提防。"}


def changwei_tick(db: GameDB, state: GameState, day: int) -> List[Dict[str, object]]:
    """月初：权阉炽盛时，东厂自发侦缉清流／持把柄挟制（活的宫廷，无需玩家操盘）。挂 rollover。
    仅在权阉之势盛（≥55）且东厂在时启动——主弱臣强，厂卫横行。"""
    from ming_sim.timeflow import LEVEL_YELLOW
    from ming_sim import court
    ensure_schema(db)
    try:
        from ming_sim.eunuch_power import get_eunuch_power
        power = get_eunuch_power(db)
    except Exception:
        power = 0
    chief = dongchang_chief(db)
    if chief is None or power < 55:
        return []
    rng = random.Random((int(day) * 0x85EBCA6B ^ 0x1337) & 0x7FFFFFFF)
    events: List[Dict[str, object]] = []
    # 东厂自发侦得一桩异党／清流把柄（阉党用厂卫整异己）。
    row = db.conn.execute(
        "SELECT s.holder FROM secrets s JOIN characters c ON c.name=s.holder "
        "WHERE s.known_to_crown=0 AND c.status='active' AND c.power_id='ming' "
        "AND c.faction!=? ORDER BY s.severity DESC LIMIT 1", (EUNUCH_FACTION,)).fetchone()
    if row and rng.random() < 0.55:
        res = investigate(db, str(row["holder"]), day, rng=rng)
        if res.get("found") and not res.get("already"):
            sec = res["secret"]
            holder = str(row["holder"])
            court._adjust_char(db, holder, grievance=+5)  # 被厂卫盯上，惶惧怨望
            events.append({"level": LEVEL_YELLOW, "kind": "changwei_probe",
                           "title": f"厂卫侦缉：{chief}缉得{holder}把柄",
                           "detail": (f"东厂{chief}秉势密缉，得{holder}「"
                                      f"{SECRET_KINDS.get(sec['kind'], sec['kind'])}」之实：{sec['detail']}。"
                                      "厂卫横行、人人自危——把柄入权阉之手，恐为锻炼诏狱之资。"),
                           "ref_kind": "character", "ref_id": holder, "day": day})
    db.conn.commit()
    return events
