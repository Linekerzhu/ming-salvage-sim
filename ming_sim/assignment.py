"""差使大厅与统一指派入口（P0 地基）。L7。

本模块是"玩家给 NPC 派任务"的统一收口层，位于 lifecycle 状态机之上：
- issue_assignment(): 五种 assignment_kind 的唯一下达入口（edict/secret_order/
  audience_commission/petition_grant/posting），内部仍走 lifecycle.init_directive_lifecycles，
  不另起状态机。
- assignment_dashboard(): 大厅只读聚合（按主办官/地区/类别/状态分组 + 专注队列）。

设计稿：docs/assignment-hall-design.md；路线图：docs/assignment-hall-roadmap.md。
全 P0 零 LLM；带 P1 标记的为占位。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from ming_sim.db import GameDB
from ming_sim.models import GameState
from ming_sim.upgrade_schema import (
    KV_CURRENT_DAY,
    KV_RISK_AVERSION,
    KV_SHI,
    adjust_belief,
    kv_int,
)

# ── assignment_kind 定义 ────────────────────────────────────────────────────
# 大厅展示用全集：四类经 issue_assignment 落 turn_directives，secret_order 来自独立的
# secret_orders 表（跨表聚合，不迁移写入——密旨有自己的 active/pending_review 引擎）。
WRITABLE_KINDS = ("edict", "audience_commission", "petition_grant", "posting")
DISPLAY_KINDS = WRITABLE_KINDS + ("secret_order",)

ENTRY_LABELS: Dict[str, str] = {
    "edict": "颁诏",
    "secret_order": "密旨",
    "audience_commission": "召对交办",
    "petition_grant": "奏请获准",
    "posting": "常驻差使",
}

# secret_orders.status → 大厅统一 status（与 lifecycle 对齐，便于跨表分组与专注队列）
_SO_STATUS_MAP = {
    "active": "executing",
    "pending_review": "stalled",   # 候月末核议 = 待圣裁，进 needs_action
    "done": "done",
    "failed": "aborted",
}

# lifecycle 中表示"在办"的状态（与 lifecycle.LIVE_STATUSES 对齐，避免循环导入）
_LIVE_STATUSES = ("in_transit", "executing", "stalled")


# ── 表结构迁移（幂等）────────────────────────────────────────────────────────

def ensure_assignment_schema(db: GameDB) -> None:
    """给 turn_directives 补入口分类两列。主迁移点在 upgrade_schema.py；
    此处为防御性幂等补列（供不跑全量迁移的测试/工具调用）。已存在则跳过。"""
    db.ensure_column("turn_directives", "assignment_kind", "TEXT NOT NULL DEFAULT 'edict'")
    db.ensure_column("turn_directives", "source_petition_id", "INTEGER NOT NULL DEFAULT 0")


# ── 统一下达入口 ────────────────────────────────────────────────────────────

def issue_assignment(
    db: GameDB,
    state: GameState,
    *,
    kind: str,
    text: str,
    actor: str = "",
    day: Optional[int] = None,
    deadline_days: int = 0,
    source_petition_id: int = 0,
    source_context: Optional[Dict[str, Any]] = None,
    source_tag: str = "",
    depends_on: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """统一差使下达入口（落 turn_directives，走 lifecycle 推进）。

    可写 kind ∈ WRITABLE_KINDS（edict/audience_commission/petition_grant/posting）。
    **密旨（secret_order）不在此处**：它有独立的 secret_orders 表与 active/pending_review
    引擎，下达请用 ``db.create_secret_order(...)``；大厅会跨表把它聚合进来。

    deadline_days: 玩家自定限期（P1.2a）。非 0 时覆盖 eta_day 并压缩 lead/exec 使不超期。
    depends_on: P2.1 依赖列表——本差使须等这些 directive_id 全部 done 才推进进度。
    颁出后会触发 NPC 领旨表态（P1.1），结果写进 chain.acceptance；超载时带 overload_warning（P2.2）。

    Returns: {ok, id, assignment_kind, entry_label, eta_day, assignee, resistance, chain, acceptance, overload_warning}
    """
    if kind not in WRITABLE_KINDS:
        if kind == "secret_order":
            raise ValueError("密旨请用 db.create_secret_order(...) 下达；大厅跨表聚合，不落 turn_directives。")
        raise ValueError(f"未知 assignment_kind：{kind}（可写 {', '.join(WRITABLE_KINDS)}）")
    ensure_assignment_schema(db)
    if day is None:
        day = kv_int(db, KV_CURRENT_DAY, 1)

    # 1) 落库（lifecycle_status 留空，交由 init_directive_lifecycles 填充）
    #    status='confirmed' 表示已正式下达（区别于 draft 颁诏候选）。
    cur = db.conn.execute(
        "INSERT INTO turn_directives (turn, year, period, text, source, status, actor) "
        "VALUES (?, ?, ?, ?, ?, 'confirmed', ?)",
        (state.turn, state.year, state.period, text, source_tag or kind, actor or None),
    )
    did = int(cur.lastrowid)

    # 2) 回写入口分类两列（init 前写好，便于 build_chain 阶段也能读到，若将来需要）
    db.conn.execute(
        "UPDATE turn_directives SET assignment_kind=?, source_petition_id=? WHERE id=?",
        (kind, int(source_petition_id), did),
    )

    # 3) 召对/密旨的上下文片段塞进 chain JSON（init 之后由 _merge_source_context 处理）
    # 4) 进入 lifecycle 推进引擎（状态机本身不动）
    from ming_sim.lifecycle import init_directive_lifecycles
    row = db.conn.execute("SELECT * FROM turn_directives WHERE id=?", (did,)).fetchone()
    inited_list = init_directive_lifecycles(db, state, [row], day)
    inited = inited_list[0] if inited_list else {}

    # 5) kind 时序覆盖（密旨不在此处；预留 posting 月报等 P1 时序覆盖钩子）
    # TODO(P1): posting 常驻差使的 lead/exec 时序与按月产报。

    # 5.5) 期限自定（P1.2a）：玩家设 deadline_days 时覆盖 eta_day 并压缩工期。
    if int(deadline_days or 0) > 0:
        _apply_player_deadline(db, did, int(deadline_days))

    # 6) source_context 合并进 chain JSON（不破坏 lifecycle 已写的 chain 结构）
    if source_context:
        _merge_source_context(db, did, source_context)

    # 6.1) P2.1 依赖列表写入 chain（tick 据此冻结进度）
    if depends_on:
        deps = [int(d) for d in depends_on if int(d) != did]
        if deps:
            row = db.conn.execute("SELECT chain FROM turn_directives WHERE id=?", (did,)).fetchone()
            meta = _load_chain_meta(row["chain"]) if row else {}
            meta["depends_on"] = deps
            db.conn.execute(
                "UPDATE turn_directives SET chain=? WHERE id=?",
                (json.dumps(meta, ensure_ascii=False), did),
            )

    # 6.5) NPC 领旨表态（P1.1）：确定性生成，数值后果即刻落库。失败不阻塞下达。
    acceptance: Dict[str, Any] = {}
    try:
        acceptance = generate_acceptance(db, state, did, day)
    except Exception:
        acceptance = {}

    db.conn.commit()

    # 7) 取回展示字段
    fresh = db.conn.execute(
        "SELECT assignee, eta_day, chain FROM turn_directives WHERE id=?", (did,)
    ).fetchone()
    chain_meta = _load_chain_meta(fresh["chain"]) if fresh else {}
    assignee_now = str(fresh["assignee"] or "") if fresh else ""

    # 6.6) P2.2 冲突预警：主办活跃差使数（含本差使）≥3 告警（不阻止）
    overload_warning = ""
    if assignee_now:
        active_n = _active_load(db, assignee_now)
        if active_n >= 3:
            overload_warning = f"{assignee_now}已在办{active_n}件差使，再派恐顾此失彼。"

    return {
        "ok": True,
        "id": did,
        "assignment_kind": kind,
        "entry_label": ENTRY_LABELS.get(kind, kind),
        "eta_day": int(fresh["eta_day"]) if fresh else 0,
        "assignee": assignee_now,
        "resistance": int(chain_meta.get("resistance") or 0),
        "chain": chain_meta.get("chain") or [],
        "acceptance": acceptance,
        "overload_warning": overload_warning,
        "preview": inited,
    }


def _load_chain_meta(raw: object) -> Dict[str, Any]:
    try:
        data = json.loads(str(raw or "{}"))
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _merge_source_context(db: GameDB, did: int, ctx: Dict[str, Any]) -> None:
    row = db.conn.execute("SELECT chain FROM turn_directives WHERE id=?", (did,)).fetchone()
    meta = _load_chain_meta(row["chain"]) if row else {}
    meta["source_context"] = ctx
    db.conn.execute(
        "UPDATE turn_directives SET chain=? WHERE id=?",
        (json.dumps(meta, ensure_ascii=False), did),
    )


def _apply_player_deadline(db: GameDB, did: int, deadline_days: int) -> None:
    """P1.2a：玩家自定限期。覆盖 eta_day = start_day + deadline，并压缩 lead/exec 使不超期。
    在 chain 标记 player_deadline_days，供领旨表态感知硬期限（请限不再延期，转阻力）。"""
    row = db.conn.execute(
        "SELECT start_day, lead_days, exec_days, chain FROM turn_directives WHERE id=?", (did,)
    ).fetchone()
    if not row:
        return
    start = int(row["start_day"] or 0)
    lead = max(0, int(row["lead_days"] or 0))
    execd = max(1, int(row["exec_days"] or 1))
    deadline = max(1, int(deadline_days))
    # 压缩：lead 至多 deadline-1，exec = 剩余
    lead = min(lead, max(0, deadline - 1))
    execd = max(1, deadline - lead)
    eta = start + lead + execd
    meta = _load_chain_meta(row["chain"])
    meta["player_deadline_days"] = deadline
    db.conn.execute(
        "UPDATE turn_directives SET lead_days=?, exec_days=?, eta_day=?, chain=? WHERE id=?",
        (lead, execd, eta, json.dumps(meta, ensure_ascii=False), did),
    )


# ── P1.1 NPC 领旨表态 ───────────────────────────────────────────────────────

_ACCEPTANCE_STANCES = ("accept", "request_time", "request_fund", "conditional", "decline")
_ACCEPTANCE_LABELS = {
    "accept": "遵旨",
    "request_time": "请限",
    "request_fund": "请拨",
    "conditional": "附条件",
    "decline": "请辞",
}


def _char_acceptance_inputs(db: GameDB, assignee: str) -> Dict[str, int]:
    """取领旨判定的角色输入：ability/loyalty/grievance（基座才总折百优先）。"""
    row = db.conn.execute(
        "SELECT ability, loyalty, grievance FROM characters WHERE name=?", (assignee,)
    ).fetchone()
    if not row:
        return {"ability": 50, "loyalty": 50, "grievance": 20}
    ability = int(row["ability"] or 50)
    try:
        from ming_sim.foundation import ability100
        fab = ability100(assignee)
        if fab is not None:
            ability = fab
    except Exception:
        pass
    return {
        "ability": ability,
        "loyalty": int(row["loyalty"] or 50),
        "grievance": int(row["grievance"] or 20),
    }


def _active_load(db: GameDB, assignee: str) -> int:
    """该官员当前在办差使数（旨意 + 密旨合计）。"""
    nd = db.conn.execute(
        "SELECT COUNT(*) FROM turn_directives WHERE assignee=? "
        "AND lifecycle_status IN ('in_transit','executing','stalled')",
        (assignee,),
    ).fetchone()[0]
    nso = db.conn.execute(
        "SELECT COUNT(*) FROM secret_orders WHERE minister_name=? AND status IN ('active','pending_review')",
        (assignee,),
    ).fetchone()[0]
    return int(nd) + int(nso)


def _stance_from_willingness(w: int) -> str:
    if w >= 70:
        return "accept"
    if w >= 60:
        return "request_time"
    if w >= 50:
        return "request_fund"
    if w >= 35:
        return "conditional"
    return "decline"


def _acceptance_narrative(stance: str, assignee: str, office: str, extra: str = "") -> str:
    """零 LLM 模板叙事。"""
    who = f"{assignee}" + (f"（{office}）" if office else "")
    tail = f" {extra}" if extra else ""
    return {
        "accept": f"{who}叩首领旨，称臣即刻承办。{tail}".strip(),
        "request_time": f"{who}领旨，惟称事体繁重、钱粮未齐，乞宽限时日。{tail}".strip(),
        "request_fund": f"{who}领旨，然奏称非增拨钱粮人手恐难克期，请加拨办理。{tail}".strip(),
        "conditional": f"{who}附条件领旨，言须某某配合方可行，否则恐生掣肘。{tail}".strip(),
        "decline": f"{who}辞以才不堪任、或差使已满，乞另简贤能。{tail}".strip(),
    }.get(stance, f"{who}领旨。")


def generate_acceptance(db: GameDB, state: GameState, directive_id: int, day: int) -> Dict[str, Any]:
    """P1.1：NPC 领旨表态。确定性（种子 directive_id*100003+day），零 LLM。

    综合能力/忠心/怨气/阻力/在办数/基座特质 → willingness → stance → 数值后果落库。
    结果写进 chain.acceptance，返回 {stance, label, willingness, narrative, effects}。
    """
    import random
    row = db.conn.execute(
        "SELECT assignee, category, chain, exec_days, lead_days, start_day "
        "FROM turn_directives WHERE id=?",
        (int(directive_id),),
    ).fetchone()
    if not row:
        return {}
    assignee = str(row["assignee"] or "")
    if not assignee:
        return {}
    meta = _load_chain_meta(row["chain"])
    resistance = int(meta.get("resistance") or 0)
    trait_score = int(meta.get("trait_score") or 0)
    inp = _char_acceptance_inputs(db, assignee)
    load = _active_load(db, assignee)

    willingness = (
        50
        + round((inp["ability"] - 50) * 0.25)
        + round((inp["loyalty"] - 50) * 0.30)
        - round(inp["grievance"] * 0.30)
        - round(resistance * 0.25)
        - max(0, load - 2) * 8       # 手里已 ≥3 件，每多一件 −8（本差使已计入 load）
        + trait_score
    )
    willingness = max(0, min(100, willingness))
    stance = _stance_from_willingness(willingness)

    # ── 数值后果 ──
    office_row = db.conn.execute("SELECT office FROM characters WHERE name=?", (assignee,)).fetchone()
    office = str(office_row["office"] or "") if office_row else ""
    effects: List[str] = []
    exec_factor = 1.0
    extra_narrative = ""

    if stance == "accept":
        _bump_grievance(db, assignee, +1)
        effects.append("怨气 +1")
    elif stance == "request_time":
        exec_factor = 1.4
        _bump_grievance(db, assignee, +2)
        effects += ["工期 ×1.4", "怨气 +2"]
    elif stance == "request_fund":
        meta["needs_support"] = True
        meta["resistance"] = max(0, resistance + 5)
        _bump_grievance(db, assignee, +2)
        effects += ["阻力 +5", "需加拨", "怨气 +2"]
    elif stance == "conditional":
        meta["resistance"] = max(0, resistance + 8)
        _bump_grievance(db, assignee, +3)
        effects += ["阻力 +8", "怨气 +3"]
    else:  # decline
        exec_factor = 1.5
        _bump_grievance(db, assignee, +6)
        adjust_belief(db, KV_SHI, -1, f"领旨请辞（差使#{directive_id}）", day=day)
        effects += ["工期 ×1.5", "怨气 +6", "势 −1（君命被推）"]
        extra_narrative = "可强令催办或改派他人。"

    # 工期延期落地（玩家硬期限 player_deadline_days 下不延期，转阻力+检定风险）
    if exec_factor != 1.0:
        old_exec = max(1, int(row["exec_days"] or 1))
        lead = max(0, int(row["lead_days"] or 0))
        start = int(row["start_day"] or 0)
        new_exec = max(1, round(old_exec * exec_factor))
        player_deadline = int(meta.get("player_deadline_days") or 0)
        if player_deadline > 0:
            # 君命既定限期：请限/请辞不得延后 eta，超出的工期转为阻力与封驳/拖延风险
            capped_exec = min(new_exec, max(1, player_deadline - lead))
            if capped_exec < new_exec:
                overflow = new_exec - capped_exec
                meta["resistance"] = max(0, int(meta.get("resistance") or 0) + overflow * 2)
                check_risk = dict(meta.get("check_risk") or {})
                check_risk["delay"] = max(0, int(check_risk.get("delay") or 0) + overflow)
                check_risk["skim"] = max(0, int(check_risk.get("skim") or 0) + overflow)
                meta["check_risk"] = check_risk
                effects.append(f"限期硬压：阻力 +{overflow*2}（磨洋工/走样风险升）")
            new_exec = capped_exec
        new_eta = start + lead + new_exec
        db.conn.execute(
            "UPDATE turn_directives SET exec_days=?, eta_day=? WHERE id=?",
            (new_exec, new_eta, int(directive_id)),
        )

    narrative = _acceptance_narrative(stance, assignee, office, extra_narrative)
    meta["acceptance"] = {
        "stance": stance,
        "label": _ACCEPTANCE_LABELS[stance],
        "willingness": willingness,
        "narrative": narrative,
        "effects": effects,
        "day": int(day),
    }
    db.conn.execute(
        "UPDATE turn_directives SET chain=? WHERE id=?",
        (json.dumps(meta, ensure_ascii=False), int(directive_id)),
    )
    return meta["acceptance"]


def _bump_grievance(db: GameDB, name: str, delta: int) -> None:
    if not name or not delta:
        return
    db.conn.execute(
        "UPDATE characters SET grievance=MAX(0, MIN(100, grievance+?)) WHERE name=?",
        (int(delta), name),
    )


# ── P1.3 办差功过册 + 赏罚兑现 ────────────────────────────────────────────────

_REWARD_TIERS = ("merit_mark", "raise", "promote")    # 记功 / 加俸 / 超擢
_PUNISH_TIERS = ("reprimand", "fine", "demote")        # 申饬 / 罚俸 / 降黜
_TIER_LABELS = {
    "merit_mark": "记功", "raise": "加俸", "promote": "超擢",
    "reprimand": "申饬", "fine": "罚俸", "demote": "降黜",
}


def ensure_merit_schema(db: GameDB) -> None:
    """防御性幂等建 merit_actions 表（主迁移点在 upgrade_schema）。"""
    db.conn.execute(
        "CREATE TABLE IF NOT EXISTS merit_actions ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, minister_name TEXT NOT NULL, "
        "kind TEXT NOT NULL, tier TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', "
        "effects_json TEXT NOT NULL DEFAULT '{}', turn INTEGER NOT NULL DEFAULT 0, "
        "day INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    db.conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_merit_actions_minister "
        "ON merit_actions(minister_name, kind, id)"
    )


def ensure_quest_schema(db: GameDB) -> None:
    """防御性幂等建 quest_* 表（player_quests/quests），供 NPC 奏请系统用。
    老存档可能未跑过 quest 初始化；此函数保证 petition 端点不因缺表 500。"""
    try:
        from ming_sim.quest_db import apply_quest_schema
        apply_quest_schema(db.conn)
    except Exception:
        pass


def _skim_directive_ids(db: GameDB) -> set:
    rows = db.conn.execute(
        "SELECT entity_id FROM report_ledger WHERE entity_kind='directive'"
    ).fetchall()
    return {str(r["entity_id"]) for r in rows}


def minister_merit_ledger(db: GameDB, assignee: str, *, recent: int = 8) -> Dict[str, Any]:
    """P1.3：某官员的办差功过册（聚合自 turn_directives done/aborted + report_ledger + chain）。

    返回 {assignee, totals:{completed,succeeded,partial,failed,skim,overdue,reprimand},
          avg_integrity, merit_score, recent:[...], reward_count, punish_count}。
    """
    assignee = str(assignee or "").strip()
    rows = db.conn.execute(
        "SELECT id, text, category, lifecycle_status, integrity_actual, chain, "
        "eta_day, start_day FROM turn_directives "
        "WHERE assignee=? AND lifecycle_status IN ('done','aborted') ORDER BY id DESC LIMIT 200",
        (assignee,),
    ).fetchall()
    skim_ids = _skim_directive_ids(db)
    totals = {"completed": 0, "succeeded": 0, "partial": 0, "failed": 0,
              "skim": 0, "overdue": 0, "reprimand": 0}
    integrity_sum = 0
    recents: List[Dict[str, Any]] = []
    for r in rows:
        totals["completed"] += 1
        status = str(r["lifecycle_status"])
        actual = int(r["integrity_actual"] or 100)
        integrity_sum += actual
        meta = _load_chain_meta(r["chain"])
        overdue = int(meta.get("overdue_deca_count") or 0)
        reprimanded = 1 if meta.get("last_reprimand") else 0
        # 截留只对 done 计：aborted 是未办成（败），不属截留
        skim = 1 if (status == "done" and (str(r["id"]) in skim_ids or actual < 85)) else 0
        if status == "aborted":
            grade = "failed"
            totals["failed"] += 1
        elif actual >= 85:
            grade = "succeeded"
            totals["succeeded"] += 1
        elif actual >= 60:
            grade = "partial"
            totals["partial"] += 1
        else:
            grade = "failed"
            totals["failed"] += 1
        totals["skim"] += skim
        totals["overdue"] += overdue
        totals["reprimand"] += reprimanded
        if len(recents) < recent:
            recents.append({
                "directive_id": int(r["id"]),
                "text": str(r["text"] or "")[:40],
                "category": str(r["category"] or ""),
                "grade": grade,
                "integrity_actual": actual,
                "skim": bool(skim),
                "overdue_deca": overdue,
                "day": int(r["eta_day"] or r["start_day"] or 0),
            })
    avg_integrity = round(integrity_sum / totals["completed"]) if totals["completed"] else 0
    # 功过分：成+2 半+1 败-3 截留-2 逾期-1/旬 申饬-1
    merit_score = (totals["succeeded"] * 2 + totals["partial"]
                   - totals["failed"] * 3 - totals["skim"] * 2
                   - totals["overdue"] - totals["reprimand"])
    # 历史奖罚计数
    reward_n = db.conn.execute(
        "SELECT COUNT(*) FROM merit_actions WHERE minister_name=? AND kind='reward'",
        (assignee,),
    ).fetchone()[0]
    punish_n = db.conn.execute(
        "SELECT COUNT(*) FROM merit_actions WHERE minister_name=? AND kind='punish'",
        (assignee,),
    ).fetchone()[0]
    return {
        "assignee": assignee,
        "totals": totals,
        "avg_integrity": avg_integrity,
        "merit_score": merit_score,
        "reward_count": int(reward_n),
        "punish_count": int(punish_n),
        "recent": recents,
    }


def merit_overview(db: GameDB, *, limit: int = 50) -> List[Dict[str, Any]]:
    """P1.3：全员功过册排行（按功过分降序），供功过册总览面板。"""
    rows = db.conn.execute(
        "SELECT DISTINCT assignee FROM turn_directives "
        "WHERE assignee!='' AND lifecycle_status IN ('done','aborted')"
    ).fetchall()
    overview: List[Dict[str, Any]] = []
    for r in rows:
        name = str(r["assignee"])
        if not name:
            continue
        overview.append(minister_merit_ledger(db, name, recent=0))
    overview.sort(key=lambda x: x["merit_score"], reverse=True)
    return overview[:limit]


def _record_merit_action(db: GameDB, state: GameState, *, minister: str, kind: str,
                         tier: str, reason: str, effects: Dict[str, Any], day: int) -> None:
    db.conn.execute(
        "INSERT INTO merit_actions (minister_name, kind, tier, reason, effects_json, turn, day) "
        "VALUES (?,?,?,?,?,?,?)",
        (minister, kind, tier, str(reason)[:200],
         json.dumps(effects, ensure_ascii=False), int(state.turn), int(day)),
    )


def grant_reward(
    db: GameDB, state: GameState, minister: str, *, tier: str, reason: str = "", day: Optional[int] = None,
) -> Dict[str, Any]:
    """P1.3 赏：据功过册奖叙。tier ∈ merit_mark(记功)/raise(加俸)/promote(超擢)。"""
    if tier not in _REWARD_TIERS:
        raise ValueError(f"未知奖叙力度：{tier}（{_REWARD_TIERS}）")
    ensure_merit_schema(db)
    if day is None:
        day = kv_int(db, KV_CURRENT_DAY, 1)
    effects: Dict[str, Any] = {}
    if tier == "merit_mark":          # 记功
        _adjust_char(db, minister, emp_trust=+3, grievance=-2)
        effects = {"emp_trust": +3, "grievance": -2}
    elif tier == "raise":             # 加俸（赐银，国库支出）
        _adjust_char(db, minister, emp_trust=+5, grievance=-3)
        db.record_issue_economy_move(state, "国库", -3, "赏功", f"加俸赐银：{minister}")
        effects = {"emp_trust": +5, "grievance": -3, "国库": -3}
    else:                             # promote 超擢
        _adjust_char(db, minister, emp_trust=+8, grievance=-5)
        adjust_belief(db, KV_SHI, +1, f"超擢奖叙：{minister}", day=day)
        effects = {"emp_trust": +8, "grievance": -5, "势": +1, "pending_promote": True}
    _record_merit_action(db, state, minister=minister, kind="reward", tier=tier,
                         reason=reason, effects=effects, day=day)
    db.conn.commit()
    return {"ok": True, "minister": minister, "kind": "reward", "tier": tier,
            "label": _TIER_LABELS[tier], "effects": effects}


def apply_punishment(
    db: GameDB, state: GameState, minister: str, *, tier: str, reason: str = "", day: Optional[int] = None,
) -> Dict[str, Any]:
    """P1.3 罚：据功过册惩处。tier ∈ reprimand(申饬)/fine(罚俸)/demote(降黜)。"""
    if tier not in _PUNISH_TIERS:
        raise ValueError(f"未知惩处力度：{tier}（{_PUNISH_TIERS}）")
    ensure_merit_schema(db)
    if day is None:
        day = kv_int(db, KV_CURRENT_DAY, 1)
    effects: Dict[str, Any] = {}
    if tier == "reprimand":           # 申饬
        _adjust_char(db, minister, emp_trust=-3, grievance=+5)
        adjust_belief(db, KV_RISK_AVERSION, +1, f"申饬：{minister}", day=day)
        effects = {"emp_trust": -3, "grievance": +5, "任事观望": +1}
    elif tier == "fine":              # 罚俸（追银入国库）
        _adjust_char(db, minister, emp_trust=-5, grievance=+8)
        db.record_issue_economy_move(state, "国库", +5, "罚俸", f"罚俸追银：{minister}")
        effects = {"emp_trust": -5, "grievance": +8, "国库": +5}
    else:                             # demote 降黜
        _adjust_char(db, minister, emp_trust=-12, grievance=+12)
        adjust_belief(db, KV_SHI, +2, f"降黜立威：{minister}", day=day)
        adjust_belief(db, KV_RISK_AVERSION, +2, f"降黜：{minister}", day=day)
        effects = {"emp_trust": -12, "grievance": +12, "势": +2, "任事观望": +2, "pending_demote": True}
    _record_merit_action(db, state, minister=minister, kind="punish", tier=tier,
                         reason=reason, effects=effects, day=day)
    db.conn.commit()
    return {"ok": True, "minister": minister, "kind": "punish", "tier": tier,
            "label": _TIER_LABELS[tier], "effects": effects}


def list_merit_actions(db: GameDB, minister: str = "", *, limit: int = 50) -> List[Dict[str, Any]]:
    """查赏罚兑现历史（某官员或全员）。"""
    ensure_merit_schema(db)
    if minister:
        rows = db.conn.execute(
            "SELECT * FROM merit_actions WHERE minister_name=? ORDER BY id DESC LIMIT ?",
            (str(minister), int(limit)),
        ).fetchall()
    else:
        rows = db.conn.execute(
            "SELECT * FROM merit_actions ORDER BY id DESC LIMIT ?", (int(limit),)
        ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        out.append({
            "id": int(r["id"]),
            "minister": str(r["minister_name"]),
            "kind": str(r["kind"]),
            "tier": str(r["tier"]),
            "label": _TIER_LABELS.get(str(r["tier"]), str(r["tier"])),
            "reason": str(r["reason"] or ""),
            "effects": _load_chain_meta(r["effects_json"]) if r["effects_json"] else {},
            "day": int(r["day"] or 0),
        })
    return out


def _adjust_char(db: GameDB, name: str, *, emp_trust: int = 0, grievance: int = 0) -> None:
    """小幅调整官员信任/怨气，夹在 0-100。"""
    if not name:
        return
    db.conn.execute(
        "UPDATE characters SET emp_trust=MAX(0, MIN(100, emp_trust+?)), "
        "grievance=MAX(0, MIN(100, grievance+?)) WHERE name=?",
        (int(emp_trust), int(grievance), name),
    )


# ── 大厅只读聚合 ────────────────────────────────────────────────────────────

def _current_day(db: GameDB) -> int:
    return kv_int(db, KV_CURRENT_DAY, 1)


def _kind_fields(db: GameDB, ids: List[int]) -> Dict[int, Dict[str, Any]]:
    """取一组 directive 的入口分类字段，供合并进 lifecycle 卡片。"""
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = db.conn.execute(
        f"SELECT id, assignment_kind, source_petition_id FROM turn_directives "
        f"WHERE id IN ({placeholders})",
        ids,
    ).fetchall()
    out: Dict[int, Dict[str, Any]] = {}
    for r in rows:
        kind = str(r["assignment_kind"] or "edict")
        out[int(r["id"])] = {
            "assignment_kind": kind,
            "entry_label": ENTRY_LABELS.get(kind, kind),
            "source_petition_id": int(r["source_petition_id"] or 0),
        }
    return out


def _assignment_cards(db: GameDB, *, include_done: bool, limit: int) -> List[Dict[str, Any]]:
    """跨表聚合：turn_directives 富卡片（经 lifecycle_payload）+ secret_orders 归一化卡片。

    两源合并成统一的 card dict（含 source_table/uid 用于消歧），供大厅分组与专注队列。
    """
    cards: List[Dict[str, Any]] = []
    # ── 源 1：turn_directives（edict/audience_commission/petition_grant/posting）──
    from ming_sim.lifecycle import lifecycle_payload
    dir_cards = lifecycle_payload(db, include_done=include_done, limit=limit)
    dir_ids = [int(c["id"]) for c in dir_cards]
    kind_map = _kind_fields(db, dir_ids)
    acc_map = _acceptance_fields(db, dir_ids)
    for c in dir_cards:
        cid = int(c["id"])
        kf = kind_map.get(cid, {})
        c["source_table"] = "directives"
        c["uid"] = f"d:{cid}"
        c["assignment_kind"] = kf.get("assignment_kind", "edict")
        c["entry_label"] = kf.get("entry_label", "颁诏")
        c["source_petition_id"] = kf.get("source_petition_id", 0)
        c["eta_turn"] = 0  # turn_directives 用 day 计时
        c["overdue"] = _directive_overdue(db, c)
        c["acceptance"] = acc_map.get(cid, {})
        cards.append(c)
    # ── 源 2：secret_orders（密旨，跨表聚合不迁移）──
    cards.extend(_secret_order_cards(db, include_done=include_done))
    return cards[:limit] if limit else cards


def _acceptance_fields(db: GameDB, ids: List[int]) -> Dict[int, Dict[str, Any]]:
    """取一组 directive 的领旨表态（chain.acceptance），供大厅卡片展示。"""
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = db.conn.execute(
        f"SELECT id, chain FROM turn_directives WHERE id IN ({placeholders})", ids
    ).fetchall()
    out: Dict[int, Dict[str, Any]] = {}
    for r in rows:
        meta = _load_chain_meta(r["chain"])
        acc = meta.get("acceptance")
        if isinstance(acc, dict):
            out[int(r["id"])] = acc
    return out


def _directive_overdue(db: GameDB, card: Dict[str, Any]) -> bool:
    st = str(card.get("status") or "")
    if st not in ("in_transit", "executing"):
        return False
    eta = int(card.get("eta_day") or 0)
    return bool(eta) and eta < _current_day(db)


def _secret_order_cards(db: GameDB, *, include_done: bool) -> List[Dict[str, Any]]:
    """把 secret_orders 行归一化成大厅 card dict。状态映射见 _SO_STATUS_MAP。"""
    statuses = ("active", "pending_review") if not include_done else (
        "active", "pending_review", "done", "failed")
    cards: List[Dict[str, Any]] = []
    state_turn = _safe_state_turn(db)
    today = _current_day(db)
    for so in db.list_secret_orders():
        st_raw = str(so.get("status") or "")
        if st_raw not in statuses:
            continue
        status = _SO_STATUS_MAP.get(st_raw, "executing")
        due_turn = int(so.get("due_turn") or 0)
        overdue = bool(due_turn) and due_turn <= state_turn and st_raw == "active"
        sid = int(so.get("id") or 0)
        cards.append({
            "id": sid,
            "uid": f"so:{sid}",
            "source_table": "secret_orders",
            "assignment_kind": "secret_order",
            "entry_label": "密旨",
            "text": str(so.get("content") or so.get("title") or ""),
            "title": str(so.get("title") or ""),
            "status": status,
            "category": "secret",            # 密旨无行政类别
            "progress": 0,                    # secret_orders 无进度条
            "assignee": str(so.get("minister_name") or ""),
            "start_day": 0,
            "eta_day": 0,
            "eta_turn": due_turn,             # 密旨用 turn 计期
            "resistance": 0,
            "chain": [],                      # 密旨无经手链
            "reported_rate": 100,
            "anomaly": "",
            "settle_note": str(so.get("result") or ""),
            "importance": int(so.get("importance") or 4),
            "overdue": overdue,
            "today": today,
        })
    return cards


def _safe_state_turn(db: GameDB) -> int:
    """取当前回合（密旨按 turn 计期）；取不到退化为 0。"""
    try:
        return int(db.load_state().turn)
    except Exception:
        return 0


_DASH_VIEWS = ("by_official", "by_region", "by_category", "by_status")


def assignment_dashboard(
    db: GameDB,
    *,
    view: str = "by_official",
    include_done: bool = False,
    limit: int = 60,
) -> Dict[str, Any]:
    """差使大厅聚合视图。

    view ∈ by_official / by_region / by_category / by_status。
    返回 {view, groups:[{key, ..., active_count, overloaded, items[]}], total, summary}
    """
    if view not in _DASH_VIEWS:
        raise ValueError(f"未知视图：{view}（可选 {', '.join(_DASH_VIEWS)}）")
    cards = _assignment_cards(db, include_done=include_done, limit=limit)

    def _group_key(c: Dict[str, Any]) -> str:
        if view == "by_official":
            return str(c.get("assignee") or "（未指派）")
        if view == "by_region":
            chain = c.get("chain") or []
            # chain 结构见 lifecycle.build_chain：[主办, 承行, 地方]
            region = ""
            if isinstance(chain, list) and len(chain) >= 3:
                region = str((chain[2] or {}).get("office") or "")
            return region or "京师"
        if view == "by_category":
            return str(c.get("category") or "misc")
        return str(c.get("status") or "unknown")

    groups: Dict[str, List[Dict[str, Any]]] = {}
    for c in cards:
        groups.setdefault(_group_key(c), []).append(c)

    # 按主办官视图需要超载标记 + 官职
    office_lookup = _office_lookup(db) if view == "by_official" else {}

    group_list: List[Dict[str, Any]] = []
    for key, items in groups.items():
        active = sum(1 for it in items if it.get("status") in _LIVE_STATUSES)
        g: Dict[str, Any] = {
            "key": key,
            "active_count": active,
            "overloaded": active >= 3,
            "items": items,
        }
        if view == "by_official":
            g["assignee"] = key
            g["office"] = office_lookup.get(key, "")
        group_list.append(g)

    # 排序：活跃数降序、再按 key
    group_list.sort(key=lambda g: (-g["active_count"], g["key"]))

    return {
        "view": view,
        "groups": group_list,
        "total": len(cards),
        "summary": _summary_counts(cards),
    }


def _office_lookup(db: GameDB) -> Dict[str, str]:
    rows = db.conn.execute("SELECT name, office FROM characters").fetchall()
    return {str(r["name"]): str(r["office"] or "") for r in rows}


def _summary_counts(cards: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {"in_transit": 0, "executing": 0, "stalled": 0, "done_unfollowed": 0}
    for c in cards:
        st = str(c.get("status") or "")
        if st in counts:
            counts[st] += 1
        if st == "done" and not (c.get("followup_action") or c.get("followup_history")):
            counts["done_unfollowed"] += 1
    return counts


# ── 专注队列 ─────────────────────────────────────────────────────────────────

def assignments_needs_action(db: GameDB) -> List[Dict[str, Any]]:
    """待处置专注队列（跨表）：封驳/候核议待圣裁 + 已复命未追问 + 逾期未结。

    - stalled：含 directives 封驳 + secret_orders pending_review（均待圣裁）
    - overdue：两源各自按 eta_day / due_turn 判定（卡片已带 overdue 标记）
    - done 未追问：仅 directives（密旨无 followup 概念）
    """
    cards = _assignment_cards(db, include_done=True, limit=500)
    out: List[Dict[str, Any]] = []
    for c in cards:
        st = str(c.get("status") or "")
        if st == "stalled":
            out.append(c)
        elif c.get("overdue"):
            out.append(c)
        elif st == "done" and c.get("source_table") == "directives":
            if not (c.get("followup_action") or c.get("followup_history")):
                out.append(c)
    return out


def assignments_overloaded(db: GameDB, threshold: int = 3) -> List[Dict[str, Any]]:
    """超载官员（跨表）：同一人在办差使数（旨意 + 密旨）≥ threshold。"""
    cards = _assignment_cards(db, include_done=False, limit=500)
    counts: Dict[str, int] = {}
    for c in cards:
        if c.get("status") in _LIVE_STATUSES and c.get("assignee"):
            counts[c["assignee"]] = counts.get(c["assignee"], 0) + 1
    office_lookup = _office_lookup(db)
    return [
        {"assignee": k, "office": office_lookup.get(k, ""),
         "active_count": v, "overloaded": v >= threshold}
        for k, v in sorted(counts.items(), key=lambda x: -x[1]) if v >= threshold
    ]


def assignments_recent_settled(db: GameDB, days: int = 30) -> List[Dict[str, Any]]:
    """近期结案（跨表）：近 days 日内 done/aborted 的差使。

    directives 按 eta_day 过滤；secret_orders 无日粒度时间戳，done/failed 一并纳入
    （数量有上限，不至于污染）。
    """
    today = _current_day(db)
    since = today - int(days)
    cards = _assignment_cards(db, include_done=True, limit=500)
    out: List[Dict[str, Any]] = []
    for c in cards:
        st = str(c.get("status") or "")
        if st not in ("done", "aborted"):
            continue
        if c.get("source_table") == "directives":
            eta = int(c.get("eta_day") or 0)
            if eta and eta >= since:
                out.append(c)
        else:
            out.append(c)
    return out


# ── 奏请获准（quest_* 新语义）─────────────────────────────────────────────────
# P1 完整接线见 roadmap B0.4 / C0.1。此处给最小可用形态供联调。

def grant_petition(
    db: GameDB,
    state: GameState,
    petition_id: int,
    *,
    draft_text: str,
    actor: str = "",
    day: Optional[int] = None,
) -> Dict[str, Any]:
    """御批一条 NPC 奏请：player_quests → granted，并转一道 petition_grant 差使。

    P0 最小实现：不依赖 quest_manager 内部，直接置 status='granted'。
    quest_manager 改造（C0.1）后由其 grant_petition 调本函数。
    """
    ensure_quest_schema(db)
    db.conn.execute(
        "UPDATE player_quests SET status='granted' WHERE id=? AND status='available'",
        (int(petition_id),),
    )
    result = issue_assignment(
        db, state,
        kind="petition_grant",
        text=draft_text,
        actor=actor,
        day=day,
        source_petition_id=int(petition_id),
        source_context={"petition_id": int(petition_id)},
        source_tag="npc_petition",
    )
    db.conn.commit()
    return result


def settle_petition_on_directive_done(db: GameDB, directive_id: int) -> int:
    """差使办结时回写奏请单 → settled。返回被置 settled 的 petition_id（0 表示无）。"""
    row = db.conn.execute(
        "SELECT source_petition_id FROM turn_directives WHERE id=?",
        (int(directive_id),),
    ).fetchone()
    if not row:
        return 0
    pid = int(row["source_petition_id"] or 0)
    if pid <= 0:
        return 0
    db.conn.execute(
        "UPDATE player_quests SET status='settled' WHERE id=? AND status='granted'",
        (pid,),
    )
    db.conn.commit()
    return pid


def reject_petition(db: GameDB, petition_id: int, *, reason: str = "") -> Dict[str, Any]:
    """驳回一条 NPC 奏请：player_quests → rejected。不产生差使。

    P0 最小实现：直接置 status='rejected'，可选记 reason 进 objective_data。
    """
    ensure_quest_schema(db)
    cur = db.conn.execute(
        "UPDATE player_quests SET status='rejected' WHERE id=? AND status='available'",
        (int(petition_id),),
    )
    if cur.rowcount and reason:
        row = db.conn.execute(
            "SELECT objective_data FROM player_quests WHERE id=?", (int(petition_id),)
        ).fetchone()
        if row:
            import json as _json
            try:
                data = _json.loads(row["objective_data"] or "{}")
            except (TypeError, ValueError):
                data = {}
            data["reject_reason"] = str(reason)[:200]
            db.conn.execute(
                "UPDATE player_quests SET objective_data=? WHERE id=?",
                (_json.dumps(data, ensure_ascii=False), int(petition_id)),
            )
    db.conn.commit()
    return {"ok": cur.rowcount > 0, "petition_id": int(petition_id), "status": "rejected"}


def list_petitions(
    db: GameDB,
    *,
    status: str = "available",
    npc: str = "",
) -> List[Dict[str, Any]]:
    """列出奏请单（默认待批）。合并 quests 模板里的 draft_directive/proposer 信息。

    npc 为非空时只返回 proposer_name 匹配该 NPC 的奏请，用于
    ``/api/petitions?npc=<name>`` 过滤召对面板里的"该大臣相关奏请"。
    """
    ensure_quest_schema(db)
    sql = (
        "SELECT pq.id, pq.quest_key, pq.status, pq.source_npc_name, pq.objective_data, "
        "q.title, q.description, q.objective_config AS tpl_objective "
        "FROM player_quests pq LEFT JOIN quests q ON q.quest_key=pq.quest_key "
        "WHERE pq.status=?"
    )
    params: List[Any] = [status]
    if npc:
        sql += " AND pq.source_npc_name=?"
        params.append(npc)
    sql += " ORDER BY pq.id DESC"
    rows = db.conn.execute(sql, params).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        import json as _json
        try:
            tpl = _json.loads(r["tpl_objective"] or "{}")
        except (TypeError, ValueError):
            tpl = {}
        try:
            obj = _json.loads(r["objective_data"] or "{}")
        except (TypeError, ValueError):
            obj = {}
        # 标题：quests 模板 > objective_data.title > quest_key（自动上奏无模板行时回退）
        title = str(r["title"] or obj.get("title") or r["quest_key"] or "")
        out.append({
            "id": int(r["id"]),
            "petition_key": str(r["quest_key"] or ""),
            "title": title,
            "description": str(r["description"] or ""),
            "status": str(r["status"] or ""),
            "proposer_name": str(r["source_npc_name"] or tpl.get("proposer_name") or ""),
            "proposer_office": str(tpl.get("proposer_office") or obj.get("proposer_office") or ""),
            "proposer_faction": str(tpl.get("proposer_faction") or ""),
            "draft_directive": str(tpl.get("draft_directive") or obj.get("draft_directive") or ""),
            "category_hint": str(tpl.get("category_hint") or obj.get("category_hint") or tpl.get("stakes") or ""),
            "stakes": str(tpl.get("stakes") or ""),
        })
    return out


def submit_petition(
    db: GameDB,
    state: GameState,
    *,
    petition_key: str,
    title: str,
    proposer_name: str = "",
    draft_directive: str = "",
) -> Dict[str, Any]:
    """提交一条 NPC 奏请（available 态）。供召对/事件模块手工触发用。

    P0 不做自动上奏触发器；此为手工入口。
    """
    ensure_quest_schema(db)
    cur = db.conn.execute(
        "INSERT INTO player_quests (quest_key, player_id, status, progress_current, "
        " progress_target, accepted_turn, expires_turn, source_npc_name, objective_data) "
        "VALUES (?,?, 'available', 0, 1, ?, 0, ?, ?)",
        (str(petition_key), 1, int(state.turn), str(proposer_name),
         _json_dumps({"draft_directive": str(draft_directive), "title": str(title)})),
    )
    pid = int(cur.lastrowid)
    db.conn.commit()
    return {"ok": True, "petition_id": pid, "status": "available"}


def _json_dumps(obj: Any) -> str:
    import json as _json
    return _json.dumps(obj, ensure_ascii=False)


# ── P1.5 常驻差使（posting）按月产报 ──────────────────────────────────────────

# 差使类型 → 月度效果模板。月度 tick 应用 effects 并产奏报事件。
_POSTING_DUTIES: Dict[str, Dict[str, Any]] = {
    "mine_tax": {
        "label": "矿税太监", "domain": "税课",
        "monthly": {"国库": 8, "民心": -2, "grievance": 3},
        "report": "税课进银入内帑，然地方骚然、商民怨望。",
    },
    "frontier_commander": {
        "label": "督师经略", "domain": "边防",
        "monthly": {"势": 1},
        "report": "边情月报：按视军备、督饬防戍，陈奏辽东/九边动静。",
    },
    "regional_inspector": {
        "label": "巡按御史", "domain": "吏治",
        "monthly": {"民心": 1, "grievance": -1},
        "report": "按治地方吏治，劾贪奖廉，吏治稍清、民困略苏。",
    },
    "grain_admin": {
        "label": "总督粮储", "domain": "粮储",
        "monthly": {"国库": 3},
        "report": "粮储月报：征解、支放、仓储虚实造册奏闻。",
    },
    "general_duty": {
        "label": "专差", "domain": "承办",
        "monthly": {},
        "report": "月度承办奏报：本月差使进展与掣肘。",
    },
}


def create_posting(
    db: GameDB, state: GameState, *, minister: str, duty_type: str = "general_duty",
    title: str = "", day: Optional[int] = None,
) -> Dict[str, Any]:
    """P1.5：授一名官员常驻差使（督师/矿税太监/巡按…），按月产报、持续生效，可撤差。

    底层落 turn_directives(assignment_kind='posting')，exec_days 极大使其不自动结案；
    月度由 posting_monthly_tick 驱动效果与奏报。
    """
    if duty_type not in _POSTING_DUTIES:
        duty_type = "general_duty"
    tpl = _POSTING_DUTIES[duty_type]
    if day is None:
        day = kv_int(db, KV_CURRENT_DAY, 1)
    text = title or f"授{minister}为{tpl['label']}，督办{tpl['domain']}事宜"
    result = issue_assignment(
        db, state, kind="posting", text=text, actor=minister, day=day,
        source_context={"duty_type": duty_type, "domain": tpl["domain"]},
        source_tag=f"posting:{duty_type}",
    )
    # 把 posting 元数据写进 chain，并设极大 exec_days 防自动结案
    row = db.conn.execute("SELECT chain FROM turn_directives WHERE id=?", (result["id"],)).fetchone()
    meta = _load_chain_meta(row["chain"])
    meta["posting"] = {"duty_type": duty_type, "label": tpl["label"], "domain": tpl["domain"]}
    meta["is_posting"] = True
    db.conn.execute(
        "UPDATE turn_directives SET exec_days=9999, chain=? WHERE id=?",
        (json.dumps(meta, ensure_ascii=False), result["id"]),
    )
    db.conn.commit()
    result["duty_type"] = duty_type
    result["duty_label"] = tpl["label"]
    return result


def posting_monthly_tick(db: GameDB, state: GameState, day: int) -> List[Dict[str, Any]]:
    """P1.5 月初：所有在办常驻差使产月报 + 应用月度效果。返回事件列表。

    供 timeflow 月度中枢调用（与 ambition.pursue_tick 同模式）。
    """
    from ming_sim.timeflow import LEVEL_BLUE
    events: List[Dict[str, Any]] = []
    rows = db.conn.execute(
        "SELECT id, assignee, text, chain FROM turn_directives "
        "WHERE assignment_kind='posting' AND lifecycle_status IN ('in_transit','executing')"
    ).fetchall()
    for row in rows:
        meta = _load_chain_meta(row["chain"])
        posting = meta.get("posting") if isinstance(meta.get("posting"), dict) else {}
        duty = str(posting.get("duty_type") or "general_duty")
        tpl = _POSTING_DUTIES.get(duty, _POSTING_DUTIES["general_duty"])
        monthly = tpl.get("monthly") or {}
        assignee = str(row["assignee"] or "")
        # 应用月度效果
        silver = int(monthly.get("国库") or 0)
        if silver:
            db.record_issue_economy_move(state, "国库", silver, tpl["label"], f"{assignee}月度差使")
        mx = int(monthly.get("民心") or 0)
        if mx:
            state.metrics["民心"] = max(0, min(100, int(state.metrics.get("民心", 50)) + mx))
        g = int(monthly.get("grievance") or 0)
        if g and assignee:
            _bump_grievance(db, assignee, g)
        shi = int(monthly.get("势") or 0)
        if shi:
            adjust_belief(db, KV_SHI, shi, f"{assignee}{tpl['label']}月度差使", day=day)
        meta["last_report_day"] = int(day)
        db.conn.execute(
            "UPDATE turn_directives SET chain=? WHERE id=?",
            (json.dumps(meta, ensure_ascii=False), int(row["id"])),
        )
        title_text = str(row["text"] or "")[:20]
        events.append({
            "level": LEVEL_BLUE, "kind": "posting_monthly_report",
            "title": f"〔{title_text}〕{tpl['label']}月报",
            "detail": f"{assignee}奏：{tpl['report']}",
            "ref_kind": "directive", "ref_id": str(row["id"]), "day": day,
        })
    if rows:
        db.conn.commit()
        db.save_state(state)
    return events


def revoke_posting(db: GameDB, state: GameState, directive_id: int, *, day: int) -> Dict[str, Any]:
    """P1.5：撤差（撤去常驻差使）。区别于收回成命（edict abort）：撤差是人事处置。"""
    row = db.conn.execute(
        "SELECT assignment_kind, assignee FROM turn_directives WHERE id=?", (int(directive_id),)
    ).fetchone()
    if not row:
        return {"ok": False, "message": "差使不存在。"}
    if str(row["assignment_kind"] or "") != "posting":
        return {"ok": False, "message": "非常驻差使，请用收回成命。"}
    db.conn.execute(
        "UPDATE turn_directives SET lifecycle_status='aborted' WHERE id=?", (int(directive_id),)
    )
    assignee = str(row["assignee"] or "")
    if assignee:
        _bump_grievance(db, assignee, +4)   # 被撤差者怨望
    db.conn.commit()
    db.save_state(state)
    return {"ok": True, "message": f"撤去{assignee}差使。其人怨望（怨气+4）。"}


# ── P2.3 调查转弹劾 ───────────────────────────────────────────────────────────

def transform_investigation(
    db: GameDB, state: GameState, directive_id: int, *, day: Optional[int] = None,
    target: str = "", reason: str = "",
) -> Dict[str, Any]:
    """P2.3：把一道已复命的调查/审计差使转化为新的弹劾/惩办差使。

    自动定位被查出的对象（report_ledger 截留承办人 或 chain.blocker_clue）；也可由玩家显式
    target 指定。生成 audit_purge 类差使，actor 指向对象，原差使 chain 标 transformed_to。
    """
    if day is None:
        day = kv_int(db, KV_CURRENT_DAY, 1)
    row = db.conn.execute(
        "SELECT id, text, category, lifecycle_status, assignee, chain FROM turn_directives WHERE id=?",
        (int(directive_id),)
    ).fetchone()
    if not row:
        return {"ok": False, "message": "原差使不存在。"}
    if str(row["lifecycle_status"]) != "done":
        return {"ok": False, "message": "仅已复命的调查差使可转弹劾。"}
    meta = _load_chain_meta(row["chain"])
    if meta.get("transformed_to"):
        return {"ok": False, "message": "此调查已转过弹劾差使。"}
    # 定位对象：显式 target > blocker_clue 人 > report_ledger 截留承办人
    obj = str(target or "").strip()
    if not obj:
        clue = meta.get("blocker_clue") if isinstance(meta.get("blocker_clue"), dict) else {}
        if str(clue.get("kind")) == "person":
            obj = str(clue.get("name") or "")
    if not obj:
        rl = db.conn.execute(
            "SELECT author_character FROM report_ledger WHERE entity_kind='directive' "
            "AND entity_id=? AND COALESCE(author_character,'')!='' LIMIT 1",
            (str(directive_id),),
        ).fetchone()
        if rl:
            obj = str(rl["author_character"])
    if not obj:
        return {"ok": False, "message": "未查出可弹劾对象（无截留、无阻力线索），请显式指明 target。"}
    # 验证对象在朝
    exists = db.conn.execute("SELECT 1 FROM characters WHERE name=?", (obj,)).fetchone()
    if not exists:
        return {"ok": False, "message": f"对象{obj}不在名册。"}
    cat = str(row["category"] or "")
    new_kind = "audit_purge" if cat in ("audit_purge", "secret_investigation") else "misc"
    text = reason or f"据差使#{directive_id}查得，逮问{obj}，追赃具奏"
    new = issue_assignment(
        db, state, kind="edict", text=text, actor=obj, day=day,
        source_context={"transformed_from": int(directive_id), "target": obj},
        source_tag=f"transform:{directive_id}",
    )
    # 覆盖类别为 audit_purge（issue_assignment 走 build_chain 按 text 归类，这里强制）
    db.conn.execute(
        "UPDATE turn_directives SET category=? WHERE id=?", (new_kind, new["id"]))
    meta["transformed_to"] = int(new["id"])
    db.conn.execute(
        "UPDATE turn_directives SET chain=? WHERE id=?",
        (json.dumps(meta, ensure_ascii=False), int(directive_id)))
    db.conn.commit()
    return {"ok": True, "original_id": int(directive_id), "new_assignment_id": int(new["id"]),
            "target": obj, "category": new_kind}


# ── P1.6 NPC 主动上奏触发器 ───────────────────────────────────────────────────

# 奏请模板：信号 → 奏请单。draft_directive 供御批后落为差使。
_PETITION_TEMPLATES = {
    "ambition_advancement": {
        "petition_key": "ambition_advancement",
        "title": "请恩超擢",
        "draft_directive": "超擢授职，简以要任",
        "category_hint": "personnel",
    },
    "regional_relief": {
        "petition_key": "regional_relief",
        "title": "请发帑赈灾",
        "draft_directive": "发帑银赈济灾区，以缓民变",
        "category_hint": "relief",
    },
    "faction_attack": {
        "petition_key": "faction_attack",
        "title": "弹劾政敌",
        "draft_directive": "逮问弹劾，查办其事",
        "category_hint": "audit_purge",
    },
}


def petition_auto_tick(db: GameDB, state: GameState, day: int, *, max_new: int = 2) -> List[Dict[str, Any]]:
    """P1.6 月初：扫描 ambition / 地方民变 / 派系信号，自动生成 NPC 奏请（available 态）。

    供 timeflow 月度中枢调用。每月至多 max_new 条，避免淹没御案。
    """
    from ming_sim.timeflow import LEVEL_BLUE
    events: List[Dict[str, Any]] = []
    created = 0
    # 信号1：高野心在朝官员 → 奏请超擢（取 ambition 进度最高的）
    if created < max_new:
        try:
            amb = db.conn.execute(
                "SELECT a.name, a.progress, c.office FROM npc_agendas a "
                "JOIN characters c ON c.name=a.name "
                "WHERE a.status='active' AND c.status='active' AND c.power_id='ming' "
                "AND c.office_type!='后宫' AND a.progress>=70 "
                "ORDER BY a.progress DESC LIMIT 1"
            ).fetchone()
            if amb and not _recent_petition(db, str(amb["name"]), "ambition_advancement", months=2):
                tpl = _PETITION_TEMPLATES["ambition_advancement"]
                _create_auto_petition(db, state, minister=str(amb["name"]),
                                      office=str(amb["office"] or ""), **tpl)
                created += 1
                events.append({"level": LEVEL_BLUE, "kind": "petition_auto",
                               "title": f"{amb['name']}上奏请恩超擢",
                               "detail": f"{amb['name']}（{amb['office'] or ''}）具疏陈功，乞请超擢简任。",
                               "day": day})
        except Exception:
            pass
    # 信号2：地方民变高 → 该地长官奏请赈济
    if created < max_new:
        try:
            region = db.conn.execute(
                "SELECT id, name, unrest FROM regions WHERE unrest>=60 ORDER BY unrest DESC LIMIT 1"
            ).fetchone()
            if region and not _recent_petition(db, "", "relief_", months=2):
                tpl = _PETITION_TEMPLATES["regional_relief"]
                rname = str(region["name"])
                draft = f"发帑银赈济{rname}，以缓民变"
                title = f"请赈{rname}"
                _create_auto_petition(db, state, minister="", office="户部",
                                      petition_key=f"relief_{region['id']}",
                                      title=title, draft_directive=draft,
                                      category_hint="relief")
                created += 1
                events.append({"level": LEVEL_BLUE, "kind": "petition_auto",
                               "title": f"户部奏请赈济{rname}",
                               "detail": f"{rname}民变频仍（民乱{region['unrest']}），户部请发帑赈灾。",
                               "day": day})
        except Exception:
            pass
    if created:
        db.conn.commit()
    return events


def _recent_petition(db: GameDB, minister: str, key_prefix: str, *, months: int = 2) -> bool:
    """近 N 月内是否已有同官员同类奏请（去重，避免刷屏）。"""
    threshold_turn = 0
    try:
        threshold_turn = max(0, int(db.load_state().turn) - months)
    except Exception:
        pass
    row = db.conn.execute(
        "SELECT 1 FROM player_quests WHERE source_npc_name=? AND quest_key LIKE ? "
        "AND accepted_turn>=? LIMIT 1",
        (minister, f"{key_prefix}%", threshold_turn),
    ).fetchone()
    return row is not None


def _create_auto_petition(
    db: GameDB, state: GameState, *, minister: str, office: str,
    petition_key: str, title: str, draft_directive: str, category_hint: str,
) -> int:
    ensure_merit_schema(db)  # 确保 player_quests 存在（quest schema）
    from ming_sim.quest_db import apply_quest_schema
    apply_quest_schema(db.conn)
    cur = db.conn.execute(
        "INSERT INTO player_quests (quest_key, player_id, status, progress_current, "
        " progress_target, accepted_turn, expires_turn, source_npc_name, objective_data) "
        "VALUES (?,?, 'available', 0, 1, ?, 0, ?, ?)",
        (str(petition_key), 1, int(state.turn), str(minister),
         _json_dumps({"draft_directive": draft_directive, "title": title,
                      "proposer_office": office, "category_hint": category_hint})),
    )
    return int(cur.lastrowid)


# TODO: 召对交办语义识别可接语义审计（当前 sealed 握手已走 issue_assignment）。
# TODO: 密旨入口收敛 negotiation.py 的 secret_order 分支（密旨走独立 secret_orders 表，已聚合）。
