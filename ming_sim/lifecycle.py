"""指令生命周期（S2）：让「迟滞的官僚体系」可见、可博弈。L7。

每道颁出的旨意成为有状态的过程：
    issued → in_transit(送达) → executing(旬检定) → done / stalled / aborted
- 工期 = 基础工期(directive_categories.json) × 官员能力修正 × 距离修正 × 阻力修正
- 每旬执行检定（确定性种子随机，可复盘）：顺利/拖延/截留/封驳/意外
- 截留走账实分离（S3）：integrity_actual 降而 integrity_reported 照报，
  完成时落 report_ledger，等密查/盘库揭穿
- 玩家中途干预：催办/换人/加拨/收回成命（各有代价，更新 势/RA/怨气）

全规则层零 LLM；异常的叙事文本由 scheduler 的 LLM 任务补写（失败有模板兜底）。
"""

from __future__ import annotations

import json
import random
import re
from typing import Dict, List, Optional

from ming_sim.db import GameDB
from ming_sim.models import GameState
from ming_sim.paths import bundled_path
from ming_sim.timeflow import LEVEL_BLUE, LEVEL_RED, LEVEL_YELLOW
from ming_sim.upgrade_schema import (
    KV_RISK_AVERSION,
    KV_SHI,
    RISK_AVERSION_DEFAULT,
    SHI_DEFAULT,
    adjust_belief,
    kv_int,
)

_CATS_CACHE: Optional[Dict[str, object]] = None

LIVE_STATUSES = ("in_transit", "executing", "stalled")


def load_categories() -> Dict[str, object]:
    global _CATS_CACHE
    if _CATS_CACHE is None:
        with open(bundled_path("content", "directive_categories.json"), encoding="utf-8") as fh:
            _CATS_CACHE = json.load(fh)
    return _CATS_CACHE


def classify_directive(text: str) -> Dict[str, object]:
    """关键词归类。多类命中取命中数最多者；并列取表序靠前。"""
    cfg = load_categories()
    best, best_hits = None, 0
    for cat in cfg.get("categories") or []:
        hits = sum(1 for kw in (cat.get("keywords") or []) if kw and kw in text)
        if hits > best_hits:
            best, best_hits = cat, hits
    if best is None:
        default_id = str(cfg.get("default_category") or "misc")
        best = next((c for c in cfg["categories"] if c["id"] == default_id), cfg["categories"][-1])
    return best


# 类别 → 主办衙门关键词（assignee 兜底查询用）
_CATEGORY_OFFICE = {
    "fiscal_allocation": "户部", "tax_reform": "户部", "relief": "户部",
    "personnel": "吏部", "military_ops": "兵部", "audit_purge": "都察院",
    "construction": "工部", "diplomacy": "礼部", "ritual_signal": "礼部",
    "secret_investigation": "锦衣卫", "misc": "内阁",
}


def _detect_region(db: GameDB, text: str) -> str:
    rows = db.conn.execute("SELECT id, name FROM regions").fetchall()
    for row in rows:
        if str(row["name"]) and str(row["name"]) in text:
            return str(row["id"])
    return "beizhili"


def _pick_assignee(db: GameDB, text: str, actor: str, category_id: str) -> str:
    """主办官员：旨意 actor 优先；否则按类别找对口衙门主官；再兜底内阁。"""
    if actor:
        row = db.conn.execute(
            "SELECT name FROM characters WHERE name=? AND status='active'", (actor,)
        ).fetchone()
        if row:
            return str(row["name"])
    # 旨意正文里点名的在朝官员
    rows = db.conn.execute(
        "SELECT name FROM characters WHERE status='active' AND office_type!='后宫'"
    ).fetchall()
    for row in rows:
        if str(row["name"]) in text:
            return str(row["name"])
    office_kw = _CATEGORY_OFFICE.get(category_id, "内阁")
    row = db.conn.execute(
        "SELECT name FROM characters WHERE status='active' AND office LIKE ? "
        "ORDER BY ability DESC LIMIT 1",
        (f"%{office_kw}%",),
    ).fetchone()
    if row:
        return str(row["name"])
    row = db.conn.execute(
        "SELECT name FROM characters WHERE status='active' AND office LIKE '%阁%' "
        "ORDER BY ability DESC LIMIT 1"
    ).fetchone()
    return str(row["name"]) if row else ""


def _resistance(db: GameDB, category: Dict[str, object], region_id: str) -> int:
    """经手阻力 0-100：类别伤害的阶级在目标地区/全国的抵抗。"""
    harms = (category.get("harms") or {}).get("classes") or []
    res = 0
    row = db.conn.execute(
        "SELECT gentry_resistance, unrest, json_extract(fiscal,'$.corruption') AS corruption "
        "FROM regions WHERE id=?", (region_id,),
    ).fetchone()
    gentry = int(row["gentry_resistance"]) if row else 40
    corruption = int(row["corruption"] or 50) if row else 50
    unrest = int(row["unrest"]) if row else 30
    if "士绅" in harms:
        res += round(gentry * 0.45)
    if "官僚" in harms:
        res += round(corruption * 0.35)
    if "商人" in harms:
        res += 10
    if "军户" in harms or "匠户" in harms:
        res += round(unrest * 0.15)
    return max(0, min(100, res))


def _char_row(db: GameDB, name: str):
    if not name:
        return None
    return db.conn.execute(
        "SELECT name, office, faction, loyalty, ability, integrity, grievance FROM characters WHERE name=?",
        (name,),
    ).fetchone()


def build_chain(db: GameDB, state: GameState, text: str, actor: str) -> Dict[str, object]:
    """归类 + 主办 + 工期 + 阻力。颁诏界面同口径展示（玩家可见的承诺）。"""
    cfg = load_categories()
    category = classify_directive(text)
    region_id = _detect_region(db, text)
    assignee = _pick_assignee(db, text, actor, str(category["id"]))
    arow = _char_row(db, assignee)
    ability = int(arow["ability"]) if arow else 50
    # NPC 数据基座深接：才总折百替代游戏 ability；擅/痼特质给检定修正与工期因子
    foundation_mods = {"score": 0, "exec_factor": 1.0, "anomaly_bias": {}, "resistance": 0, "notes": []}
    try:
        from ming_sim import foundation
        fab = foundation.ability100(assignee)
        if fab is not None:
            ability = fab
        foundation_mods = foundation.directive_modifiers(assignee, str(category["id"]))
    except Exception:
        pass
    distance = float((cfg.get("distance_factors") or {}).get(
        region_id, (cfg.get("distance_factors") or {}).get("default", 1.4)))
    resistance = _resistance(db, category, region_id) + int(foundation_mods.get("resistance") or 0)
    resistance = max(0, min(100, resistance))
    ability_factor = 1.35 - ability / 200.0          # ability 100 → 0.85；50 → 1.10
    resistance_factor = 1.0 + resistance / 150.0     # 阻力 100 → ×1.67
    exec_days = max(2, round(int(category["base_days"]) * ability_factor * distance
                             * resistance_factor * float(foundation_mods.get("exec_factor") or 1.0)))
    lead_days = max(1, round(int(category["lead_days"]) * distance))
    chain = [
        {"role": "主办", "name": assignee, "office": str(arow["office"]) if arow else "",
         "faction": str(arow["faction"]) if arow else ""},
        {"role": "承行", "name": "", "office": _CATEGORY_OFFICE.get(str(category["id"]), "内阁")},
        {"role": "地方", "name": "", "office": region_id},
    ]
    # 异常权重合并基座特质偏置（贪墨→截留+、优柔→拖延+、刚愎→封驳+…），下限 0
    check_risk = dict(category.get("check_risk") or {})
    for kind, delta in (foundation_mods.get("anomaly_bias") or {}).items():
        if kind in ("delay", "skim", "block", "surprise"):
            check_risk[kind] = max(0, int(check_risk.get(kind) or 0) + int(delta))
    return {
        "category": str(category["id"]),
        "category_name": str(category["name"]),
        "region_id": region_id,
        "assignee": assignee,
        "lead_days": lead_days,
        "exec_days": exec_days,
        "resistance": resistance,
        "chain": chain,
        "check_risk": check_risk,
        "trait_score": int(foundation_mods.get("score") or 0),
        "trait_notes": list(foundation_mods.get("notes") or []),
    }


def init_directive_lifecycles(db: GameDB, state: GameState, directives, day: int) -> List[Dict[str, object]]:
    """颁诏时初始化各旨意生命周期。幂等（已有 lifecycle_status 的跳过）。"""
    initialized = []
    for row in directives or []:
        did = int(row["id"])
        cur = db.conn.execute(
            "SELECT lifecycle_status FROM turn_directives WHERE id=?", (did,)
        ).fetchone()
        if cur is None or str(cur["lifecycle_status"] or ""):
            continue
        text = str(row["text"] or "")
        actor = str(row["actor"] or "") if "actor" in row.keys() else ""
        plan = build_chain(db, state, text, actor)
        eta = int(day) + int(plan["lead_days"]) + int(plan["exec_days"])
        db.conn.execute(
            """UPDATE turn_directives SET lifecycle_status='in_transit', category=?,
               progress=0, lead_days=?, exec_days=?, start_day=?, eta_day=?,
               assignee=?, chain=?, integrity_actual=100, integrity_reported=100
               WHERE id=?""",
            (plan["category"], int(plan["lead_days"]), int(plan["exec_days"]),
             int(day), eta, plan["assignee"],
             json.dumps({"chain": plan["chain"], "region_id": plan["region_id"],
                         "resistance": plan["resistance"],
                         "check_risk": plan["check_risk"],
                         "trait_score": int(plan.get("trait_score") or 0),
                         "trait_notes": plan.get("trait_notes") or [],
                         "score_bonus": 0}, ensure_ascii=False),
             did),
        )
        # 崇祯陷阱被动信号（S5）：严谴问罪之旨，百官自动更新「任事有多危险」的先验
        punitive = ("下狱", "处死", "弃市", "处斩", "斩立决", "传首", "逮问", "革职查办", "廷杖")
        if any(kw in text for kw in punitive):
            adjust_belief(db, KV_RISK_AVERSION, +3, f"严谴之旨（#{did}）", day=day)
        initialized.append({"id": did, **plan, "eta_day": eta})
    db.conn.commit()
    return initialized


# ── 旬检定 ───────────────────────────────────────────────────────────────────

def _chain_meta(row) -> Dict[str, object]:
    try:
        return json.loads(row["chain"] or "{}")
    except ValueError:
        return {}


def _save_chain_meta(db: GameDB, did: int, meta: Dict[str, object]) -> None:
    db.conn.execute("UPDATE turn_directives SET chain=? WHERE id=?",
                    (json.dumps(meta, ensure_ascii=False), did))


def _execution_score(db: GameDB, row, meta: Dict[str, object]) -> int:
    arow = _char_row(db, str(row["assignee"] or ""))
    ability = int(arow["ability"]) if arow else 50
    loyalty = int(arow["loyalty"]) if arow else 50
    grievance = int(arow["grievance"]) if arow and "grievance" in arow.keys() else 20
    shi = kv_int(db, KV_SHI, SHI_DEFAULT)
    ra = kv_int(db, KV_RISK_AVERSION, RISK_AVERSION_DEFAULT)
    resistance = int(meta.get("resistance") or 0)
    bonus = int(meta.get("score_bonus") or 0) + int(meta.get("trait_score") or 0)
    # 基座五维能力（才总折百）优先于游戏 ability
    try:
        from ming_sim.foundation import ability100
        fab = ability100(str(row["assignee"] or ""))
        if fab is not None:
            ability = fab
    except Exception:
        pass
    score = (50
             + round((ability - 50) * 0.40)
             + round((loyalty - 50) * 0.15)
             + round((shi - 50) * 0.30)
             - round(resistance * 0.40)
             - round(max(0, ra - 40) * 0.20)
             - round(grievance * 0.20)
             + bonus)
    return max(5, min(95, score))


def _pick_anomaly(rng: random.Random, check_risk: Dict[str, object]) -> str:
    pool: List[str] = []
    for kind in ("delay", "skim", "block", "surprise"):
        pool.extend([kind] * max(0, int(check_risk.get(kind) or 0)))
    return rng.choice(pool) if pool else "delay"


def _enqueue(db: GameDB, kind: str, payload: Dict[str, object]) -> None:
    try:
        from ming_sim.scheduler import enqueue_job
        enqueue_job(db, kind, payload)
    except Exception:
        pass


_ANOMALY_TEXT = {
    "delay": ("主办陈情迁延", "称钱粮未齐、吏胥推诿，乞展限办理。可催办、换人或加拨钱粮。"),
    "block": ("科道封驳抗辩", "给事中以事体未协封还，旨意冻结待圣裁。可催办（强令施行）、换人或收回成命。"),
    "surprise": ("地方实情有变", "原议与地方情形不符，须更张办法，工期顺延。"),
}


def tick_directives(db: GameDB, state: GameState, day: int) -> List[Dict[str, object]]:
    """日推进：送达→执行；执行中按日涨进度；逢旬（每10日）做执行检定。
    返回 timeflow 事件 dict 列表。"""
    events: List[Dict[str, object]] = []
    rows = db.conn.execute(
        "SELECT * FROM turn_directives WHERE lifecycle_status IN ('in_transit','executing','stalled')"
    ).fetchall()
    for row in rows:
        did = int(row["id"])
        status = str(row["lifecycle_status"])
        start_day = int(row["start_day"])
        lead = int(row["lead_days"])
        meta = _chain_meta(row)

        if status == "in_transit":
            if day >= start_day + lead:
                db.conn.execute(
                    "UPDATE turn_directives SET lifecycle_status='executing' WHERE id=?", (did,))
            continue

        if status == "stalled":
            # 封驳搁置：每旬皇威折损（诏令不行）
            if (day - start_day) % 10 == 0:
                adjust_belief(db, KV_SHI, -1, f"旨意#{did}遭封驳搁置", day=day)
            continue

        # executing：按「剩余进度/剩余天数」自校正推进——
        # 工期中途延长（delay/surprise）自动摊薄、干预加成自动提前、
        # 提前颁诏跳日后自动追平，完成日与 eta_day 严格对齐（审计修复 P2-1）。
        exec_days = max(1, int(row["exec_days"]))
        eta = start_day + lead + exec_days
        remaining_days = eta - day + 1
        old_progress = int(row["progress"])
        if remaining_days <= 1:
            progress = 100
        else:
            progress = min(100, old_progress + max(1, round((100 - old_progress) / remaining_days)))
        db.conn.execute("UPDATE turn_directives SET progress=? WHERE id=?", (progress, did))

        # 旬检定（执行期内每满10天一次；完成日不再检）
        days_in_exec = day - start_day - lead
        if progress < 100 and days_in_exec > 0 and days_in_exec % 10 == 0:
            rng = random.Random(did * 100003 + day)
            score = _execution_score(db, row, meta)
            anomaly_prob = max(0.05, min(0.60, (70 - score) / 100.0))
            if rng.random() < anomaly_prob:
                kind = _pick_anomaly(rng, dict(meta.get("check_risk") or {}))
                title_text = str(row["text"] or "")[:24]
                if kind == "skim":
                    # 截留打折：账实分叉，玩家此刻看不到（这正是黑箱）
                    cut = rng.randint(15, 30)
                    new_actual = max(20, int(row["integrity_actual"]) - cut)
                    db.conn.execute(
                        "UPDATE turn_directives SET integrity_actual=? WHERE id=?",
                        (new_actual, did))
                elif kind == "delay":
                    extra = rng.randint(5, 10)
                    db.conn.execute(
                        "UPDATE turn_directives SET exec_days=exec_days+?, eta_day=eta_day+?, anomaly=? WHERE id=?",
                        (extra, extra,
                         json.dumps({"kind": "delay", "day": day}, ensure_ascii=False), did))
                    t, d = _ANOMALY_TEXT["delay"]
                    events.append({"level": LEVEL_YELLOW, "kind": "directive_anomaly",
                                   "title": f"〔{title_text}〕{t}",
                                   "detail": d, "ref_kind": "directive", "ref_id": str(did), "day": day})
                    _enqueue(db, "anomaly_text", {"directive_id": did, "anomaly_kind": "delay"})
                elif kind == "block":
                    db.conn.execute(
                        "UPDATE turn_directives SET lifecycle_status='stalled', anomaly=? WHERE id=?",
                        (json.dumps({"kind": "block", "day": day}, ensure_ascii=False), did))
                    t, d = _ANOMALY_TEXT["block"]
                    events.append({"level": LEVEL_RED, "kind": "directive_anomaly",
                                   "title": f"〔{title_text}〕{t}",
                                   "detail": d, "ref_kind": "directive", "ref_id": str(did), "day": day})
                    _enqueue(db, "anomaly_text", {"directive_id": did, "anomaly_kind": "block"})
                else:  # surprise
                    extra = rng.randint(6, 14)
                    db.conn.execute(
                        "UPDATE turn_directives SET exec_days=exec_days+?, eta_day=eta_day+?, anomaly=? WHERE id=?",
                        (extra, extra,
                         json.dumps({"kind": "surprise", "day": day}, ensure_ascii=False), did))
                    t, d = _ANOMALY_TEXT["surprise"]
                    events.append({"level": LEVEL_YELLOW, "kind": "directive_anomaly",
                                   "title": f"〔{title_text}〕{t}",
                                   "detail": d, "ref_kind": "directive", "ref_id": str(did), "day": day})
                    _enqueue(db, "anomaly_text", {"directive_id": did, "anomaly_kind": "surprise"})

        # 完成
        row2 = db.conn.execute("SELECT progress, integrity_actual, integrity_reported, assignee, text "
                               "FROM turn_directives WHERE id=?", (did,)).fetchone()
        if int(row2["progress"]) >= 100:
            actual = int(row2["integrity_actual"])
            reported = int(row2["integrity_reported"])
            db.conn.execute(
                "UPDATE turn_directives SET lifecycle_status='done', progress=100, anomaly='' WHERE id=?",
                (did,))
            adjust_belief(db, KV_SHI, +1, f"旨意#{did}如期办结", day=day)
            if actual < 85:
                # 奏报粉饰：账实分离落 report_ledger（S3），待密查/盘库揭穿
                arow = _char_row(db, str(row2["assignee"] or ""))
                db.conn.execute(
                    """INSERT INTO report_ledger
                       (entity_kind, entity_id, field, reported_value, actual_value,
                        author_org, author_character, reported_day, note)
                       VALUES ('directive', ?, 'execution_rate', ?, ?, ?, ?, ?, ?)""",
                    (str(did), float(reported), float(actual),
                     str(arow["office"]) if arow else "",
                     str(row2["assignee"] or ""), int(day),
                     f"旨意办结奏报：{str(row2['text'] or '')[:40]}"),
                )
            events.append({"level": LEVEL_YELLOW, "kind": "directive_done",
                           "title": f"〔{str(row2['text'] or '')[:24]}〕办结奏闻",
                           "detail": f"主办{row2['assignee']}奏称已遵旨办竣。",
                           "ref_kind": "directive", "ref_id": str(did), "day": day})
            _enqueue(db, "settle_note", {"directive_id": did})
    db.conn.commit()
    return events


# ── 玩家中途干预 ─────────────────────────────────────────────────────────────

def intervene(db: GameDB, state: GameState, directive_id: int, action: str,
              *, day: int, new_assignee: str = "", fund: int = 0) -> Dict[str, object]:
    """催办 cuiban / 换人 reassign / 加拨 fund / 收回成命 abort。"""
    row = db.conn.execute("SELECT * FROM turn_directives WHERE id=?", (int(directive_id),)).fetchone()
    if row is None or str(row["lifecycle_status"]) not in LIVE_STATUSES:
        return {"ok": False, "message": "该旨意不在执行中。"}
    did = int(row["id"])
    meta = _chain_meta(row)
    assignee = str(row["assignee"] or "")

    if action == "cuiban":
        db.conn.execute(
            "UPDATE turn_directives SET lifecycle_status='executing', "
            "progress=MIN(100, progress+8), exec_days=MAX(2, exec_days-4), anomaly='' WHERE id=?",
            (did,))
        if assignee:
            db.conn.execute(
                "UPDATE characters SET grievance=MIN(100, grievance+5) WHERE name=?", (assignee,))
        adjust_belief(db, KV_RISK_AVERSION, +1, f"严旨催办#{did}", day=day)
        msg = f"严旨切责，{assignee}惶恐加紧办理（进度+，主办怨气+，百官观望+）。"
    elif action == "reassign":
        if not new_assignee or _char_row(db, new_assignee) is None:
            return {"ok": False, "message": "须指定在朝官员接办。"}
        if assignee:
            db.conn.execute(
                "UPDATE characters SET grievance=MIN(100, grievance+10) WHERE name=?", (assignee,))
        # 换人重算基座特质修正（新主办的擅/痼接管检定与异常偏置）
        try:
            from ming_sim import foundation
            cat_id = str(row["category"] or "misc")
            mods = foundation.directive_modifiers(new_assignee, cat_id)
            meta["trait_score"] = int(mods.get("score") or 0)
            meta["trait_notes"] = list(mods.get("notes") or [])
            base_risk = dict((load_categories().get("categories") and next(
                (c.get("check_risk") or {} for c in load_categories()["categories"]
                 if c["id"] == cat_id), {})) or {})
            for kind, delta in (mods.get("anomaly_bias") or {}).items():
                base_risk[kind] = max(0, int(base_risk.get(kind) or 0) + int(delta))
            if base_risk:
                meta["check_risk"] = base_risk
            _save_chain_meta(db, did, meta)
        except Exception:
            pass
        db.conn.execute(
            "UPDATE turn_directives SET assignee=?, lifecycle_status='executing', "
            "progress=MAX(0, progress-15), anomaly='' WHERE id=?",
            (new_assignee, did))
        msg = f"改命{new_assignee}接办（交接折损进度，原主办{assignee}怨望）。"
    elif action == "fund":
        amount = max(1, min(200, int(fund or 10)))
        actual = db.record_issue_economy_move(
            state, "国库", -amount, "加拨办差", f"加拨旨意#{did}经费{amount}万两")
        if not actual:
            return {"ok": False, "message": "国库不敷，拨不出银子。"}
        meta["score_bonus"] = int(meta.get("score_bonus") or 0) + 12
        meta["resistance"] = max(0, int(meta.get("resistance") or 0) - 10)
        _save_chain_meta(db, did, meta)
        db.conn.execute(
            "UPDATE turn_directives SET lifecycle_status='executing', "
            "progress=MIN(100, progress+5), anomaly='' WHERE id=?", (did,))
        msg = f"加拨{abs(actual)}万两疏通办差（阻力-，检定+）。"
    elif action == "ducai":
        # 乾纲独断（S6）：势>70 解锁——绕过部议强推，但独断与寒心连动（RA+3）
        from ming_sim.theater import can_ducai
        if not can_ducai(db):
            return {"ok": False, "message": "君威未隆（势不足七十），独断之旨出不了午门。"}
        meta["score_bonus"] = int(meta.get("score_bonus") or 0) + 20
        meta["resistance"] = max(0, int(meta.get("resistance") or 0) - 20)
        _save_chain_meta(db, did, meta)
        db.conn.execute(
            "UPDATE turn_directives SET lifecycle_status='executing', "
            "progress=MIN(100, progress+25), anomaly='' WHERE id=?", (did,))
        adjust_belief(db, KV_RISK_AVERSION, +3, f"乾纲独断强推#{did}", day=day)
        msg = "中旨直下，绕开部议封驳，所司不敢复言（进度+25，阻力-20）。然独断日久，任事之心日灰（百官观望+3）。"
    elif action == "abort":
        db.conn.execute(
            "UPDATE turn_directives SET lifecycle_status='aborted', anomaly='' WHERE id=?", (did,))
        adjust_belief(db, KV_SHI, -3, f"收回成命#{did}（朝令夕改）", day=day)
        adjust_belief(db, KV_RISK_AVERSION, +2, f"旨意#{did}半途而废", day=day)
        msg = "收回成命。诏令反复，势有所损，百官益发观望。"
    else:
        return {"ok": False, "message": f"未知处置：{action}"}
    db.conn.commit()
    db.save_state(state)
    return {"ok": True, "message": msg}


# ── 查询与推演注入 ───────────────────────────────────────────────────────────

def lifecycle_payload(db: GameDB, *, include_done: bool = True, limit: int = 40) -> List[Dict[str, object]]:
    """前端指令进度面板。注意：integrity 只暴露 reported（账面），actual 不出 API（S3）。"""
    sql = ("SELECT id, text, lifecycle_status, category, progress, lead_days, exec_days, "
           "start_day, eta_day, assignee, chain, integrity_reported, anomaly, settle_note "
           "FROM turn_directives WHERE lifecycle_status!=''")
    if not include_done:
        sql += " AND lifecycle_status IN ('in_transit','executing','stalled')"
    sql += " ORDER BY id DESC LIMIT ?"
    out = []
    for row in db.conn.execute(sql, (int(limit),)).fetchall():
        meta = _chain_meta(row)
        out.append({
            "id": int(row["id"]),
            "text": str(row["text"] or ""),
            "status": str(row["lifecycle_status"]),
            "category": str(row["category"] or ""),
            "progress": int(row["progress"]),
            "assignee": str(row["assignee"] or ""),
            "start_day": int(row["start_day"]),
            "eta_day": int(row["eta_day"]),
            "resistance": int(meta.get("resistance") or 0),
            "chain": meta.get("chain") or [],
            "reported_rate": int(row["integrity_reported"]),
            "anomaly": str(row["anomaly"] or ""),
            "settle_note": str(row["settle_note"] or ""),
        })
    return out


def executing_directives_brief(db: GameDB) -> List[Dict[str, object]]:
    """喂 simulator：执行中旨意清单（效果未落地，邸报不得按已完成叙述）。"""
    return [
        {"id": item["id"], "text": item["text"][:80], "status": item["status"],
         "progress": item["progress"], "assignee": item["assignee"]}
        for item in lifecycle_payload(db, include_done=False, limit=20)
    ]
