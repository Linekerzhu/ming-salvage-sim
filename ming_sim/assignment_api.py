"""差使大厅与 NPC 奏请 Web API（P0）。

注册两组路由：
- /api/assignments/*    差使大厅（跨表只读聚合 + 统一下达）
- /api/petitions/*      NPC 奏请（quest_* 重定位后的新语义）

配套设计：docs/assignment-hall-design.md §7。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from pydantic import BaseModel

from ming_sim import assignment
from ming_sim.upgrade_schema import KV_CURRENT_DAY, kv_int


# ── 请求体模型 ───────────────────────────────────────────────────────────────

class IssueAssignmentBody(BaseModel):
    kind: str = "edict"
    text: str
    actor: str = ""
    deadline_days: int = 0          # P1.2a 玩家自定限期（0=不限期）
    depends_on: Optional[List[int]] = None   # P2.1 依赖列表
    source_context: Optional[Dict[str, Any]] = None
    source_petition_id: int = 0


class SubmitPetitionBody(BaseModel):
    petition_key: str
    title: str
    proposer_name: str = ""
    draft_directive: str = ""


class GrantPetitionBody(BaseModel):
    draft_text: str = ""          # 空则取奏请模板里的 draft_directive
    actor: str = ""               # 可指定主办，空则 lifecycle 自动 pick


class RejectPetitionBody(BaseModel):
    reason: str = ""


class MeritActionBody(BaseModel):
    tier: str                    # reward: merit_mark/raise/promote; punish: reprimand/fine/demote
    reason: str = ""


class CreatePostingBody(BaseModel):
    minister: str
    duty_type: str = "general_duty"   # mine_tax/frontier_commander/regional_inspector/grain_admin/general_duty
    title: str = ""


# ── 路由注册 ─────────────────────────────────────────────────────────────────

def register_assignment_routes(app, get_db_func) -> None:
    """注册差使大厅 + 奏请两组路由。"""

    def _db():
        db = get_db_func()
        assignment.ensure_assignment_schema(db)  # 防御性幂等补列
        return db

    def _state(db):
        # 用 db.load_state() 取当前态（裸 GameDB 无 .session 属性，quest_api 的
        # db.session.state 在非 WebGame 包裹下会失败；load_state 稳健且与持久化一致）。
        return db.load_state()

    # ════════════ 差使大厅（/api/assignments）════════════

    @app.get("/api/assignments")
    def list_assignments(
        view: str = "by_official",
        include_done: bool = False,
        limit: int = 60,
    ):
        """差使大厅聚合视图。view ∈ by_official/by_region/by_category/by_status。"""
        db = _db()
        try:
            return assignment.assignment_dashboard(
                db, view=view, include_done=include_done, limit=limit
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/api/assignments/needs_action")
    def assignments_needs_action():
        """待处置专注队列：封驳/候核议 + 已复命未追问 + 逾期。"""
        return {"items": assignment.assignments_needs_action(_db())}

    @app.get("/api/assignments/overloaded")
    def assignments_overloaded(threshold: int = 3):
        """超载官员（旨意+密旨在办合计 ≥ threshold）。"""
        return {"items": assignment.assignments_overloaded(_db(), threshold=threshold)}

    @app.get("/api/assignments/recent_settled")
    def assignments_recent_settled(days: int = 30):
        """近期结案差使。"""
        return {"items": assignment.assignments_recent_settled(_db(), days=days)}

    @app.get("/api/assignments/{directive_id}")
    def get_assignment(directive_id: int):
        """单条差使详情（lifecycle 单条 + 来源字段）。"""
        db = _db()
        from ming_sim.lifecycle import lifecycle_payload
        cards = lifecycle_payload(db, include_done=True, limit=500)
        match = next((c for c in cards if int(c.get("id") or 0) == directive_id), None)
        if not match:
            raise HTTPException(status_code=404, detail="差使不存在")
        # 补来源字段 + 领旨表态（P1.1）
        kind_map = assignment._kind_fields(db, [directive_id])
        kf = kind_map.get(directive_id, {})
        match["assignment_kind"] = kf.get("assignment_kind", "edict")
        match["entry_label"] = kf.get("entry_label", "颁诏")
        match["source_petition_id"] = kf.get("source_petition_id", 0)
        acc_map = assignment._acceptance_fields(db, [directive_id])
        match["acceptance"] = acc_map.get(directive_id, {})
        return match

    @app.post("/api/assignments")
    def issue_assignment_endpoint(body: IssueAssignmentBody):
        """统一下达差使（edict/audience_commission/petition_grant/posting）。

        密旨（secret_order）不在此处——用既有密旨下达流程。
        """
        db = _db()
        try:
            result = assignment.issue_assignment(
                db, _state(db),
                kind=body.kind,
                text=body.text,
                actor=body.actor,
                deadline_days=body.deadline_days,
                depends_on=body.depends_on,
                day=kv_int(db, KV_CURRENT_DAY, 1),
                source_petition_id=body.source_petition_id,
                source_context=body.source_context,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return result

    # ════════════ NPC 奏请（/api/petitions）════════════

    @app.get("/api/petitions")
    def list_petitions(status: str = "available"):
        """奏请单列表。status ∈ available/granted/rejected/settled。"""
        return {"status": status, "items": assignment.list_petitions(_db(), status=status)}

    @app.post("/api/petitions")
    def submit_petition(body: SubmitPetitionBody):
        """提交一条 NPC 奏请（召对/事件模块触发，待批态）。"""
        db = _db()
        return assignment.submit_petition(
            db, _state(db),
            petition_key=body.petition_key,
            title=body.title,
            proposer_name=body.proposer_name,
            draft_directive=body.draft_directive,
        )

    @app.post("/api/petitions/{petition_id}/grant")
    def grant_petition(petition_id: int, body: GrantPetitionBody):
        """御批奏请 → granted + 转一道 petition_grant 差使。"""
        db = _db()
        # draft_text 为空时从模板取 draft_directive
        draft = body.draft_text
        if not draft:
            petitions = assignment.list_petitions(db, status="available")
            match = next((p for p in petitions if int(p["id"]) == petition_id), None)
            draft = str(match["draft_directive"] or match["title"]) if match else ""
        if not draft:
            raise HTTPException(status_code=400, detail="须提供 draft_text 或奏请须有 draft_directive")
        try:
            return assignment.grant_petition(
                db, _state(db), petition_id,
                draft_text=draft, actor=body.actor,
                day=kv_int(db, KV_CURRENT_DAY, 1),
            )
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/petitions/{petition_id}/reject")
    def reject_petition(petition_id: int, body: RejectPetitionBody):
        """驳回奏请 → rejected。"""
        result = assignment.reject_petition(_db(), petition_id, reason=body.reason)
        if not result["ok"]:
            raise HTTPException(status_code=400, detail="奏请不存在或不在待批态")
        return result

    @app.get("/api/petitions/history")
    def petition_history():
        """已批/已驳/已结 奏请历史。"""
        db = _db()
        return {
            "granted": assignment.list_petitions(db, status="granted"),
            "rejected": assignment.list_petitions(db, status="rejected"),
            "settled": assignment.list_petitions(db, status="settled"),
        }

    # ════════════ 办差功过册 + 赏罚兑现（/api/merit，P1.3）════════════

    @app.get("/api/merit")
    def merit_overview_route():
        """全员功过册排行（按功过分降序）。"""
        return {"items": assignment.merit_overview(_db())}

    @app.get("/api/merit/actions")
    def merit_actions_route(minister: str = ""):
        """赏罚兑现历史（可按官员过滤）。"""
        return {"items": assignment.list_merit_actions(_db(), minister=minister)}

    @app.get("/api/merit/{minister}")
    def minister_merit(minister: str):
        """某官员的办差功过册（成/半/败/截留/逾期 + 近期差使）。"""
        return assignment.minister_merit_ledger(_db(), minister)

    @app.post("/api/merit/{minister}/reward")
    def grant_reward_route(minister: str, body: MeritActionBody):
        """据功过册奖叙。tier ∈ merit_mark/raise/promote。"""
        db = _db()
        try:
            return assignment.grant_reward(
                db, _state(db), minister, tier=body.tier, reason=body.reason,
                day=kv_int(db, KV_CURRENT_DAY, 1),
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/merit/{minister}/punish")
    def apply_punishment_route(minister: str, body: MeritActionBody):
        """据功过册惩处。tier ∈ reprimand/fine/demote。"""
        db = _db()
        try:
            return assignment.apply_punishment(
                db, _state(db), minister, tier=body.tier, reason=body.reason,
                day=kv_int(db, KV_CURRENT_DAY, 1),
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    # ════════════ 常驻差使（/api/postings，P1.5）════════════

    @app.post("/api/postings")
    def create_posting_route(body: CreatePostingBody):
        """授常驻差使（督师/矿税太监/巡按…），按月产报。"""
        db = _db()
        return assignment.create_posting(
            db, _state(db), minister=body.minister, duty_type=body.duty_type,
            title=body.title, day=kv_int(db, KV_CURRENT_DAY, 1),
        )

    @app.post("/api/postings/{directive_id}/revoke")
    def revoke_posting_route(directive_id: int):
        """撤差（撤去常驻差使）。"""
        db = _db()
        result = assignment.revoke_posting(db, _state(db), int(directive_id), day=kv_int(db, KV_CURRENT_DAY, 1))
        if not result["ok"]:
            raise HTTPException(status_code=400, detail=result["message"])
        return result

    # ════════════ P2 任务关联 ═════════════

    @app.post("/api/assignments/{directive_id}/transform")
    def transform_investigation_route(directive_id: int, body: dict = {}):
        """P2.3 调查转弹劾：已复命的调查差使 → 新的惩办差使。"""
        db = _db()
        result = assignment.transform_investigation(
            db, _state(db), int(directive_id),
            day=kv_int(db, KV_CURRENT_DAY, 1),
            target=str(body.get("target") or ""),
            reason=str(body.get("reason") or ""),
        )
        if not result["ok"]:
            raise HTTPException(status_code=400, detail=result["message"])
        return result
