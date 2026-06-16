"""朝堂剧场（S6）+ 派系主动 AI（S7）+ 双向信任抓手（S8）。L7。

支柱 P1「皇权是均衡」：
- 势（upgrade_schema.KV_SHI）已由各系统累积；本模块补**信号类指令**
  （廷杖/罪己诏/献俘——不改钱粮、只改信念）与**乾纲独断**（势>70 解锁）。
- 派系不再只被动挨打：agenda+heat 驱动每旬主动出招（弹章/联名/荐人/掣肘），
  以奏疏形式进御案——世界出招与玩家视野统一在「一摞奏疏」里（P4 一致性）。
"""

from __future__ import annotations

import json
import random
from typing import Dict, List, Optional

from ming_sim.db import GameDB
from ming_sim.models import GameState
from ming_sim.timeflow import LEVEL_RED, LEVEL_YELLOW
from ming_sim.upgrade_schema import (
    KV_RISK_AVERSION,
    KV_SHI,
    SHI_DEFAULT,
    adjust_belief,
    kv_int,
)

# ── 派系 heat ────────────────────────────────────────────────────────────────

def adjust_faction_heat(db: GameDB, faction: str, delta: int, reason: str = "") -> None:
    if not faction or faction in ("无",):
        return
    row = db.conn.execute("SELECT heat FROM factions WHERE name=?", (faction,)).fetchone()
    if row is None:
        return
    new = max(0, min(100, int(row["heat"]) + int(delta)))
    db.conn.execute("UPDATE factions SET heat=? WHERE name=?", (new, faction))
    db.conn.commit()


def faction_of(db: GameDB, name: str) -> str:
    row = db.conn.execute("SELECT faction FROM characters WHERE name=?", (name,)).fetchone()
    return str(row["faction"]) if row else ""


# ── 派系出招（每旬）──────────────────────────────────────────────────────────

_MOVE_KINDS = ("tanzhang", "lianming", "jianren", "chezhou")


def faction_moves_tick(db: GameDB, state: GameState, day: int) -> List[Dict[str, object]]:
    """逢旬（每10日）执行：heat 驱动出招，全局每旬至多 2 招；朔日 heat 自然衰减。"""
    from ming_sim.upgrade_schema import DAYS_PER_MONTH
    events: List[Dict[str, object]] = []
    dim = (int(day) - 1) % DAYS_PER_MONTH + 1
    # heat 朔日衰减在 timeflow._ensure_month_open（日 tick 永远从第 2 日起，dim==1 在此不可达）
    if dim not in (5, 15, 25):  # 错开旬税赋/旬检定日，避免节点拥挤
        return events
    # 缺口2：势驱动派系气焰（P1 皇权是均衡）。君威不振→百僚轻朝廷，党争气焰日炽（heat 逐旬涨）；
    # 君威隆盛→慑服敛迹（heat 加速衰减）。出招概率亦随之放大/压低。
    shi = kv_int(db, KV_SHI, SHI_DEFAULT)
    if shi < 45:
        db.conn.execute("UPDATE factions SET heat=MIN(100, heat + ?)", (max(1, (45 - shi) // 12),))
        db.conn.commit()
    elif shi > 65:
        db.conn.execute("UPDATE factions SET heat=MAX(0, heat - ?)", (max(1, (shi - 65) // 15),))
        db.conn.commit()
    embolden = max(0.55, min(1.7, 1.0 + (55 - shi) / 90.0))  # 势55→1.0；势0→1.6；势100→0.55
    rng = random.Random(day * 2654435761 % (2 ** 31))
    rows = db.conn.execute(
        "SELECT name, heat, agenda FROM factions WHERE heat>0 ORDER BY heat DESC"
    ).fetchall()
    moves_left = 2
    for row in rows:
        if moves_left <= 0:
            break
        heat = int(row["heat"])
        if rng.random() >= (heat / 250.0) * embolden:
            continue
        ev = _execute_move(db, state, str(row["name"]), str(row["agenda"] or ""), day, rng)
        if ev is not None:
            events.append(ev)
            moves_left -= 1
    return events


def _faction_member(db: GameDB, faction: str, rng: random.Random, *, min_courage: int = 0):
    rows = db.conn.execute(
        "SELECT name, office, courage FROM characters "
        "WHERE status='active' AND power_id='ming' AND faction=? AND office_type!='后宫' AND courage>=?",
        (faction, int(min_courage)),
    ).fetchall()
    return rng.choice(rows) if rows else None


def _rival_target(db: GameDB, faction: str, rng: random.Random):
    rows = db.conn.execute(
        "SELECT name, office, faction FROM characters "
        "WHERE status='active' AND power_id='ming' AND faction!=? AND faction!='无' AND office_type!='后宫'",
        (faction,),
    ).fetchall()
    return rng.choice(rows) if rows else None


def _execute_move(db: GameDB, state: GameState, faction: str, agenda: str,
                  day: int, rng: random.Random) -> Optional[Dict[str, object]]:
    from ming_sim.memorials import create_memorial
    kind = rng.choice(_MOVE_KINDS)
    if kind == "tanzhang":
        author = _faction_member(db, faction, rng, min_courage=55)
        target = _rival_target(db, faction, rng)
        if author is None or target is None:
            return None
        mid = create_memorial(
            db, None, day=day, author_name=str(author["name"]), org=str(author["office"]),
            kind="弹章", urgency=3,
            summary=f"{author['name']}劾{target['office']}{target['name']}",
            full_text=(f"臣风闻{str(target['office'])}{str(target['name'])}居官有玷，"
                       f"或贪墨、或欺罔、或植党，乞敕下究问，以肃朝纲。"),
            ref_kind="character", ref_id=str(target["name"]))
        _enqueue_move_text(db, mid, faction, agenda, "弹章")
        return {"level": LEVEL_RED, "kind": "faction_move",
                "title": f"弹章入奏：{author['name']}劾{target['name']}",
                "detail": f"{faction}出手。批红表态本身就是落子：查办、留中、申斥言官，各有人看在眼里。",
                "ref_kind": "memorial", "ref_id": str(mid), "day": day}
    if kind == "lianming":
        author = _faction_member(db, faction, rng)
        issue = db.conn.execute(
            "SELECT id, title FROM issues WHERE status='active' ORDER BY RANDOM() LIMIT 1").fetchone()
        if author is None or issue is None:
            return None
        mid = create_memorial(
            db, None, day=day, author_name=str(author["name"]), org=str(author["office"]),
            kind="弹章", urgency=3,
            summary=f"{faction}诸臣联名为「{issue['title']}」事进言",
            full_text=f"臣等谨联名上言：{str(issue['title'])}事关国本，{agenda or '伏乞圣断'}。臣等不胜惓惓之至。",
            ref_kind="issue", ref_id=str(issue["id"]))
        _enqueue_move_text(db, mid, faction, agenda, "联名")
        return {"level": LEVEL_YELLOW, "kind": "faction_move",
                "title": f"{faction}联名上书",
                "detail": "联名是示威：留中则积怨折势，从之则派系坐大。",
                "ref_kind": "memorial", "ref_id": str(mid), "day": day}
    if kind == "jianren":
        author = _faction_member(db, faction, rng)
        if author is None:
            return None
        # 四成概率从 NPC 数据基座的赋闲人才池荐真实历史人物（批准即起复入朝），
        # 否则荐本派在朝同僚（塞要害位置）。
        if rng.random() < 0.4:
            try:
                from ming_sim.foundation import random_candidate
                in_court = {str(r["name"]) for r in db.conn.execute("SELECT name FROM characters")}
                pick = random_candidate(in_court, rng)
            except Exception:
                pick = None
            if pick is not None:
                mid = create_memorial(
                    db, None, day=day, author_name=str(author["name"]), org=str(author["office"]),
                    kind="荐人", urgency=2,
                    summary=f"{author['name']}保举起复{pick['name']}",
                    full_text=(f"臣谨保举{pick['native_place']}{pick['name']}（{pick['status']}，"
                               f"{pick['rank'] or '白身'}），{pick['traits'] or '才堪大用'}。"
                               f"今其赋闲于野，乞陛下起复擢用。"),
                    ref_kind="foundation_npc", ref_id=str(pick["name"]))
                return {"level": LEVEL_YELLOW, "kind": "faction_move",
                        "title": f"{faction}荐起复{pick['name']}",
                        "detail": "荐的是在野真才还是自己人？批红前不妨先遣人访查。",
                        "ref_kind": "memorial", "ref_id": str(mid), "day": day}
        candidate = _faction_member(db, faction, rng)
        if candidate is None or author["name"] == candidate["name"]:
            return None
        mid = create_memorial(
            db, None, day=day, author_name=str(author["name"]), org=str(author["office"]),
            kind="荐人", urgency=2,
            summary=f"{author['name']}保举{candidate['name']}",
            full_text=f"臣谨保举{str(candidate['office'])}{str(candidate['name'])}，才堪大用，乞量才擢用。",
            ref_kind="character", ref_id=str(candidate["name"]))
        return {"level": LEVEL_YELLOW, "kind": "faction_move",
                "title": f"{faction}荐人：{candidate['name']}",
                "detail": "往要害处塞自己人——准与不准，都改变派系格局。",
                "ref_kind": "memorial", "ref_id": str(mid), "day": day}
    # chezhou 掣肘：给执行中的旨意叠阻力
    row = db.conn.execute(
        "SELECT id, text, chain FROM turn_directives "
        "WHERE lifecycle_status IN ('executing','in_transit') ORDER BY RANDOM() LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    try:
        meta = json.loads(row["chain"] or "{}")
    except ValueError:
        meta = {}
    meta["resistance"] = min(100, int(meta.get("resistance") or 0) + 15)
    db.conn.execute("UPDATE turn_directives SET chain=? WHERE id=?",
                    (json.dumps(meta, ensure_ascii=False), int(row["id"])))
    db.conn.commit()
    # 掣肘来源对玩家不可见（黑箱）：事件文案不点破是哪一派在使绊子
    return {"level": LEVEL_YELLOW, "kind": "faction_move",
            "title": f"〔{str(row['text'])[:20]}〕办理处处受阻",
            "detail": "地方文移迁延、吏胥借端推诿，似有人暗中掣肘——是谁，须自己去查。",
            "ref_kind": "directive", "ref_id": str(row["id"]), "day": day}


def _enqueue_move_text(db: GameDB, memorial_id: int, faction: str, agenda: str, move_label: str) -> None:
    try:
        from ming_sim.scheduler import enqueue_job
        enqueue_job(db, "faction_move", {"memorial_id": memorial_id, "faction": faction,
                                         "agenda": agenda, "move": move_label})
    except Exception:
        pass


FACTION_MOVE_PROMPT = """你是明末某派系的谋主，正替本派官员润色一份奏疏（弹章或联名疏）。
你会收到：奏疏现有概要与正文、本派系名称与 agenda（阶段诉求）、招式类型。
要求：120-200 字明代奏疏体正文。立场要为本派 agenda 服务，但措辞必须以公义为名——党争藏在「为国除奸」「为民请命」之后。直接输出正文。"""


def _handle_faction_move(db, llm_config, payload: Dict[str, object]) -> str:
    mid = int(payload.get("memorial_id") or 0)
    row = db.conn.execute("SELECT * FROM memorials WHERE id=?", (mid,)).fetchone()
    if row is None:
        return ""
    text = ""
    try:
        from ming_sim.scheduler import _run_text
        text = _run_text(llm_config, agent_id="faction-strategist", prompt=FACTION_MOVE_PROMPT,
                         payload={"memorial": {"summary": str(row["summary"]),
                                               "full_text": str(row["full_text"])[:300]},
                                  "faction": str(payload.get("faction") or ""),
                                  "agenda": str(payload.get("agenda") or ""),
                                  "move": str(payload.get("move") or "")},
                         minimum=400)
    except Exception:
        text = ""
    if text:
        db.conn.execute("UPDATE memorials SET full_text=? WHERE id=?", (text[:800], mid))
        db.conn.commit()
    return text


try:
    from ming_sim.scheduler import register_handler as _register
    _register("faction_move", _handle_faction_move)
except Exception:
    pass


# ── 信号类指令（纯改信念，不改钱粮）──────────────────────────────────────────

def signal_action(db: GameDB, state: GameState, kind: str, *, day: int,
                  target: str = "") -> Dict[str, object]:
    """tingzhang 廷杖 / zuiji 罪己诏 / xianfu 午门献俘。朝堂即剧场：受众是全体官员。"""
    if kind == "tingzhang":
        if not target:
            return {"ok": False, "message": "廷杖须指明对象。"}
        row = db.conn.execute(
            "SELECT name, faction FROM characters WHERE name=? AND status='active'", (target,)
        ).fetchone()
        if row is None:
            return {"ok": False, "message": "无此在朝官员。"}
        adjust_belief(db, KV_SHI, +3, f"廷杖{target}（杀鸡儆猴）", day=day)
        adjust_belief(db, KV_RISK_AVERSION, +6, f"廷杖{target}（言路侧目）", day=day)
        state.metrics["皇威"] = min(100, int(state.metrics.get("皇威", 20)) + 2)
        db.conn.execute(
            "UPDATE characters SET grievance=MIN(100, grievance+30), "
            "emp_trust=MAX(0, emp_trust-25) WHERE name=?", (target,))
        adjust_faction_heat(db, str(row["faction"]), +12, "同党遭廷杖")
        db.record_log(state, f"【廷杖】杖{target}于午门。观者股栗。")
        msg = f"杖{target}于午门。势+3、皇威+2，而百官钳口（任事意愿-6），其同党衔恨。"
    elif kind == "zuiji":
        adjust_belief(db, KV_SHI, -5, "下罪己诏（自承其过）", day=day)
        adjust_belief(db, KV_RISK_AVERSION, -8, "罪己诏安人心", day=day)
        state.metrics["民心"] = min(100, int(state.metrics.get("民心", 50)) + 3)
        db.record_log(state, "【罪己】上下诏罪己，减膳撤乐，与天下更始。")
        msg = "罪己诏颁行天下。民心+3，臣下感奋（任事意愿+8），然君威自抑（势-5）。"
    elif kind == "xianfu":
        adjust_belief(db, KV_SHI, +5, "午门献俘（武功昭示）", day=day)
        state.metrics["民心"] = min(100, int(state.metrics.get("民心", 50)) + 2)
        state.metrics["皇威"] = min(100, int(state.metrics.get("皇威", 20)) + 4)
        db.record_log(state, "【献俘】御午门受俘，百官朝贺。")
        msg = "午门献俘，中外振奋。势+5、皇威+4，民心+2。（若无实捷而虚饰献俘，邸报与史笔自有公论。）"
    else:
        return {"ok": False, "message": f"未知信号：{kind}"}
    db.conn.commit()
    db.save_state(state)
    return {"ok": True, "message": msg}


# ── 乾纲独断（势>70 解锁的独断处置，见 lifecycle.intervene 'ducai'）──────────

DUCAI_SHI_THRESHOLD = 70


def can_ducai(db: GameDB) -> bool:
    return kv_int(db, KV_SHI, SHI_DEFAULT) >= DUCAI_SHI_THRESHOLD


# ── 问罪抓手（S8：官员失诺自动生成可问罪依据）────────────────────────────────

def leverage_payload(db: GameDB, state: GameState, name: str = "") -> List[Dict[str, object]]:
    sql = ("SELECT id, minister_name, target_text, status, updated_at FROM negotiation_agreements "
           "WHERE status IN ('failed','blocked')")
    args: tuple = ()
    if name:
        sql += " AND minister_name=?"
        args = (name,)
    sql += " ORDER BY id DESC LIMIT 30"
    out = []
    for row in db.conn.execute(sql, args).fetchall():
        out.append({
            "agreement_id": int(row["id"]),
            "minister": str(row["minister_name"]),
            "promise": str(row["target_text"] or "")[:120],
            "status": str(row["status"]),
            "usage": "可在召对中出示问责（修正握手），或交科道发起弹劾（转指令）。",
        })
    return out
