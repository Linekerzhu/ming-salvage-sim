"""净身恶趣味 · E2a：净身「宝」的处置、奴性表达、还阳传说、全尸执念。

净身入宫不只是身份转换——明代内廷的阴郁肌理：
  · 净身割下之「宝」（势）须有处置：自赎保存（kept，盛于匣中供奉，望来世全尸、还阳做完整人）、
    被官府收没（forfeit，强阉之奇辱）、或客死遗失（lost，终身憾）。
  · 升司礼监要职须「验宝」——无宝者为同侪所轻、迁转受沮。
  · 老宦官多信「还阳」之说——攒宝礼佛、望来世；亦有「年老阳气还生」的流言。
  · 强阉夺宝者奴性多扭曲（谄媚卑顺而心结阴鸷），自宫求进／自愿入者多恭谨守分——
    **奴性的表达随净身情形分野**，喂入与其对话的角色简报。
  · 殁时无全尸（forfeit/lost）是终身大憾，党羽哀恸更深。

本模块：eunuch_lore 表（每个净身者一行）+ 净身时登记（接 convert_character_to_eunuch）+
奴性/宝况注入对话简报（接 context 身份叠加层）+ 还阳传言月度 tick（挂 rollover）+
殁时全尸执念涟漪（接 lifespan.mortality_tick）。无净身记录则优雅降级。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from ming_sim.db import GameDB
from ming_sim.models import GameState

# 宝（势）之处置
BAO_KEPT = "kept"        # 自赎保存，盛匣供奉
BAO_FORFEIT = "forfeit"  # 被官府收没（强阉之辱）
BAO_LOST = "lost"        # 客死遗失，无凭

_AGED_FOR_REINCARNATION = 58   # 信「还阳」之说的高龄门槛


def ensure_schema(db: GameDB) -> None:
    db.conn.execute(
        """CREATE TABLE IF NOT EXISTS eunuch_lore (
            name TEXT PRIMARY KEY,
            bao_status TEXT NOT NULL DEFAULT 'kept',
            forced INTEGER NOT NULL DEFAULT 0,
            servility INTEGER NOT NULL DEFAULT 45,
            castration_day INTEGER NOT NULL DEFAULT 0,
            reincarnation INTEGER NOT NULL DEFAULT 0,
            note TEXT NOT NULL DEFAULT ''
        )""")
    db.conn.commit()


def seed_eunuch_lore(db: GameDB) -> None:
    """幂等：为开局已在朝的内廷／司礼监宦官补默认净身记录（多系少年自宫入仕，宝存恭谨）。"""
    ensure_schema(db)
    from ming_sim.eunuch import is_eunuch_like
    rows = db.conn.execute(
        "SELECT name, office, office_type, integrity, loyalty, status_reason FROM characters "
        "WHERE status='active' AND power_id='ming'").fetchall()
    for r in rows:
        name = str(r["name"])
        if not is_eunuch_like(str(r["office"] or ""), str(r["office_type"] or "")):
            continue
        if db.conn.execute("SELECT 1 FROM eunuch_lore WHERE name=?", (name,)).fetchone():
            continue
        reason = str(r["status_reason"] or "")
        forced = 1 if ("强旨" in reason or "心结" in reason) else 0
        integ = int(r["integrity"] or 50)
        loy = int(r["loyalty"] or 50)
        # 奴性：自愿入仕的少年宦官多恭谨自安；忠厚者奴性平、阴鸷弄权者奴性低（不甘为奴）。
        servility = 45 + max(0, 55 - integ) // 4 - max(0, loy - 60) // 5
        servility = max(15, min(85, servility + (25 if forced else 0)))
        db.conn.execute(
            "INSERT INTO eunuch_lore(name, bao_status, forced, servility, castration_day, note) "
            "VALUES (?,?,?,?,0,?)",
            (name, BAO_FORFEIT if forced else BAO_KEPT, forced, int(servility),
             "少年净身入仕，宝匣自藏" if not forced else "奉强旨净身，宝为官没"))
    db.conn.commit()


def record_castration(db: GameDB, name: str, *, forced: bool, day: int) -> Dict[str, object]:
    """净身时登记「宝」之处置与奴性（接 convert_character_to_eunuch）。
    强阉＝宝被官府收没、奴性扭曲（谄而心结深）；自愿＝宝可自赎保存、奴性恭谨。"""
    ensure_schema(db)
    name = (name or "").strip()
    if not name:
        return {}
    bao = BAO_FORFEIT if forced else BAO_KEPT
    servility = 78 if forced else 46
    note = "奉强旨净身，宝为官没——奇辱深结" if forced else "奏对同意后净身，宝匣自藏供奉"
    db.conn.execute(
        "INSERT INTO eunuch_lore(name, bao_status, forced, servility, castration_day, reincarnation, note) "
        "VALUES (?,?,?,?,?,0,?) "
        "ON CONFLICT(name) DO UPDATE SET bao_status=excluded.bao_status, forced=excluded.forced, "
        "servility=excluded.servility, castration_day=excluded.castration_day, note=excluded.note",
        (name, bao, 1 if forced else 0, int(servility), int(day), note))
    db.conn.commit()
    return {"name": name, "bao_status": bao, "forced": bool(forced), "servility": servility}


def get_lore(db: GameDB, name: str) -> Optional[Dict[str, object]]:
    ensure_schema(db)
    row = db.conn.execute(
        "SELECT name, bao_status, forced, servility, castration_day, reincarnation, note "
        "FROM eunuch_lore WHERE name=?", ((name or "").strip(),)).fetchone()
    if row is None:
        return None
    return {"name": str(row["name"]), "bao_status": str(row["bao_status"]),
            "forced": bool(row["forced"]), "servility": int(row["servility"]),
            "castration_day": int(row["castration_day"]), "reincarnation": bool(row["reincarnation"]),
            "note": str(row["note"] or "")}


_BAO_LABEL = {BAO_KEPT: "宝匣自藏（望来世全尸）", BAO_FORFEIT: "宝为官没（强阉之辱）",
              BAO_LOST: "宝已遗失（客死无凭）"}


def servility_brief(db: GameDB, name: str) -> str:
    """注入与净身者对话的角色简报：奴性表达 + 宝况心结，随净身情形分野。无记录则空。"""
    lore = get_lore(db, name)
    if lore is None:
        return ""
    serv = int(lore["servility"])
    forced = bool(lore["forced"])
    bao = str(lore["bao_status"])
    parts: List[str] = ["【净身·心相】"]
    if forced or bao == BAO_FORFEIT:
        parts.append("你是被强旨净身入宫的——这是奇辱。表面卑顺谄媚、口称奴婢叩首不迭，"
                     "内里却结着深重心结，逢迎之中带几分阴鸷与不甘；对昔日羞辱你的外朝，怨而隐忍。")
    elif serv >= 60:
        parts.append("你自净身入宫，安于为奴：恭顺谄媚、揣摩上意唯恐不及，以伺候得陛下欢心为荣，"
                     "言必称奴婢、奴才，事事先意承旨。")
    else:
        parts.append("你净身入宫多年，奴性已化为分寸：恭谨守礼而不谄佞，知所进退；"
                     "口称奴婢，却存一段读书人或老内臣的体面。")
    if bao == BAO_KEPT:
        parts.append("你私藏「宝」于匣中供奉，礼佛攒福，望来世做个全尸完整之人——此念你深藏不露。")
    elif bao == BAO_FORFEIT:
        parts.append("你的「宝」被官府收没，每念及死后不得全尸，便如鲠在喉，是你最深的隐痛。")
    return "".join(parts)


def lore_overlay(db: GameDB, name: str) -> Dict[str, str]:
    """供 context 身份叠加层取用：把奴性与宝况折成 biography/risk 增补片段。"""
    brief = servility_brief(db, name)
    if not brief:
        return {}
    return {"servility": brief}


def reincarnation_tick(db: GameDB, state: GameState, day: int) -> List[Dict[str, object]]:
    """月初：老宦官的「还阳」传言与攒宝礼佛之念（迷信 flavor，至多一桩/月）。挂 rollover。"""
    from ming_sim.timeflow import LEVEL_BLUE
    from ming_sim.lifespan import _age  # 复用年龄算法
    ensure_schema(db)
    year = int(getattr(state, "year", 0) or 0)
    rows = db.conn.execute(
        "SELECT l.name, l.bao_status, l.reincarnation, c.office, c.birth_year "
        "FROM eunuch_lore l JOIN characters c ON c.name=l.name "
        "WHERE c.status='active' AND c.power_id='ming'").fetchall()
    # 确定性挑选（不用随机）：取尚未起过还阳传言、年最高的一位老宦官。
    aged = []
    for r in rows:
        age = _age(int(r["birth_year"] or 0), year) if year else 0
        if age >= _AGED_FOR_REINCARNATION and not int(r["reincarnation"]):
            aged.append((age, str(r["name"]), str(r["office"] or ""), str(r["bao_status"])))
    if not aged:
        return []
    # 仅在「月之朔逢特定节律」起一桩，避免月月刷屏：用 day 的整除性当确定性闸门。
    if (int(day) // 30) % 4 != 0:
        return []
    aged.sort(reverse=True)
    age, name, office, bao = aged[0]
    db.conn.execute("UPDATE eunuch_lore SET reincarnation=1 WHERE name=?", (name,))
    db.conn.commit()
    if bao == BAO_KEPT:
        detail = (f"老内臣{office}{name}（{age}岁）于佛前供奉宝匣、日诵往生，"
                  "私望来世得全尸、还阳做个完整之人。宫中老监闻之，多有戚戚。")
    else:
        detail = (f"宫中传：老内臣{office}{name}（{age}岁）年高阳气复生、「还阳」之兆——"
                  "无稽之谈，然老监无宝者闻之，竟有羡叹焚香求之者。")
    return [{"level": LEVEL_BLUE, "kind": "eunuch_reincarnation",
             "title": f"内廷异闻：{name}与「还阳」",
             "detail": detail, "ref_kind": "character", "ref_id": name, "day": day}]


# ── 民间募新宦官（E2d）：灾年民困，良家子自宫求进、卖身入宫充内侍 ──
_RECRUIT_SURNAMES = "王李张刘陈杨赵黄周吴徐孙马朱"
_RECRUIT_GIVEN = ("进忠", "永贞", "国泰", "得禄", "承恩", "守义", "小顺", "三才", "应元",
                  "化淳", "良辅", "时敏", "本忠", "永寿", "尽忠", "存仁")
_RECRUIT_OFFICES = ("内官监小火者", "司礼监随堂", "净军", "御马监勇士营", "尚膳监差使")
_RECRUIT_UNREST_GATE = 48      # 流民／民困到此程度，自宫求进者渐多
_RECRUIT_CAP = 8               # 自动募入的上限（免灌爆名册）


def _auto_recruited_count(db: GameDB) -> int:
    ensure_schema(db)
    return int(db.conn.execute(
        "SELECT COUNT(*) c FROM eunuch_lore WHERE note LIKE '%自宫求进%'").fetchone()["c"])


def recruit_tick(db: GameDB, state: GameState, day: int) -> List[Dict[str, object]]:
    """月初：灾年民困（流民多）→ 良家子自宫求进、卖身入宫充内侍，为内廷添新血。
    生成低品宦官 NPC 入册（非净身现成大臣）。挂 rollover。无灾或已满额则空转。"""
    from ming_sim.timeflow import LEVEL_BLUE
    from ming_sim.models import Character
    ensure_schema(db)
    if _auto_recruited_count(db) >= _RECRUIT_CAP:
        return []
    row = db.conn.execute("SELECT AVG(unrest) a FROM regions").fetchone()
    avg_unrest = float(row["a"] or 0)
    if avg_unrest < _RECRUIT_UNREST_GATE:
        return []
    # 确定性节律：每两月一审，民困越深越易来人（用 day 整除当闸门，避免月月新人）。
    if (int(day) // 30) % 2 != 0:
        return []
    existing = {str(r["name"]) for r in db.conn.execute("SELECT name FROM characters").fetchall()}
    idx = (int(day) // 60)
    name = ""
    for off in range(len(_RECRUIT_GIVEN)):
        cand = _RECRUIT_SURNAMES[(idx + off) % len(_RECRUIT_SURNAMES)] + _RECRUIT_GIVEN[(idx + off) % len(_RECRUIT_GIVEN)]
        if cand not in existing:
            name = cand
            break
    if not name:
        return []
    office = _RECRUIT_OFFICES[idx % len(_RECRUIT_OFFICES)]
    db.add_character(state, Character(
        name=name, office=office, office_type="内官监", faction="内廷", aliases=[],
        personal_skills=["洒扫", "传话"], loyalty=58, ability=38, integrity=50, courage=40,
        style="自宫求进、卑微谨愿的小内侍，凡事唯恐失欢", power_id="ming", status="active",
        summary=f"灾年贫家子，自宫求进入宫，充{office}。", rank_category="eunuch"),
        source="民间自宫求进入宫")
    # 自宫求进者奴性偏重（求生卑微），宝多自藏（望来世）。
    record_castration(db, name, forced=False, day=day)
    db.conn.execute("UPDATE eunuch_lore SET servility=66, note=? WHERE name=?",
                    ("灾年自宫求进入宫，宝匣自藏", name))
    db.conn.commit()
    return [{"level": LEVEL_BLUE, "kind": "eunuch_recruit",
             "title": f"自宫求进：{name}入内廷",
             "detail": f"岁饥民困，贫家子{name}自宫求进、卖身入宫，充{office}。"
                       "外朝叹曰：净身求活者日多，亦世道之凄惶。内廷由是益众。",
             "ref_kind": "character", "ref_id": name, "day": day}]


def burial_lament_on_death(db: GameDB, state: GameState, name: str, day: int) -> Optional[str]:
    """殁时全尸执念（接 lifespan.mortality_tick）：净身者无宝（forfeit/lost）→ 不得全尸之憾，
    党羽哀恸更深（额外 grievance）；有宝（kept）→ 凑得全尸、稍慰。返回讣告增补语或 None。"""
    lore = get_lore(db, name)
    if lore is None:
        return None
    bao = str(lore["bao_status"])
    try:
        from ming_sim import court
        allies = court.allies_of(db, name, limit=4)
    except Exception:
        allies = []
    if bao == BAO_KEPT:
        return "宝匣得以同棺，凑成全尸下葬，老监谓其「圆满去得」，稍慰生平之缺。"
    # 无宝：不得全尸，党羽哀恸更深
    for a in allies:
        try:
            from ming_sim import court
            court._adjust_char(db, a["name"], grievance=+2)
        except Exception:
            pass
    if bao == BAO_FORFEIT:
        return "宝为官没、终不得全尸——生前奇辱、身后犹缺，党羽哀其不圆，怨气深结。"
    return "客死无凭、宝已遗失，不得全尸而葬，宫人嗟叹。"
