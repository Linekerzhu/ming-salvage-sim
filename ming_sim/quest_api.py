"""Quest system Web API routes.

本模块原为 RPG 任务系统接口，已在差使大厅（P0）落地时"软重定位"为
NPC 奏请单：玩家不再"接任务"，而是由 NPC 上奏请托，玩家可御批/驳回。
原 /api/quests/* 端点保留路径，但全部返回 410 Gone + 指向新接口，避免
误调用旧 RPG 语义污染数据。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import HTTPException


# 旧 RPG 任务端点 → 新奏请端点 迁移提示
_PETITION_HINTS = {
    "GET /api/quests": "GET /api/petitions?status=available",
    "GET /api/quests/{quest_key}": "GET /api/petitions (按 petition_key 过滤)",
    "POST /api/quests/{quest_key}/accept": "POST /api/petitions/{id}/grant",
    "POST /api/quests/{id}/progress": "差使走 /api/assignments/{id}/intervene",
    "POST /api/quests/{id}/complete": "差使结案走 /api/assignments/{id} (lifecycle done)",
    "POST /api/quests/{id}/abandon": "POST /api/petitions/{id}/reject",
    "GET /api/quests/npc/{name}": "GET /api/petitions?npc={name}",
    "POST /api/quests/sync/check_expiry": "差使逾期由 lifecycle 自动结算",
}


def _gone(endpoint: str):
    raise HTTPException(
        status_code=410,
        detail={
            "error": "gone",
            "message": (
                "Quest 系统已重定位为 NPC 奏请（assignment-hall P0）。"
                f"请改用 {_PETITION_HINTS.get(endpoint, '/api/petitions')}。"
            ),
            "endpoint": endpoint,
        },
    )


def register_quest_routes(app, get_db_func):
    """Register quest system API routes (all retired — 410 Gone)."""

    @app.get("/api/quests")
    def get_quests(include_completed: bool = False):
        """[已弃用] Quest → 奏请。详见 /api/petitions。"""
        _gone("GET /api/quests")

    @app.get("/api/quests/{quest_key}")
    def get_quest_detail(quest_key: str):
        """[已弃用] Quest → 奏请。详见 /api/petitions。"""
        _gone("GET /api/quests/{quest_key}")

    @app.post("/api/quests/{quest_key}/accept")
    def accept_quest(quest_key: str, source_npc_name: str = ""):
        """[已弃用] Quest → 奏请。请改用 /api/petitions/{id}/grant。"""
        _gone("POST /api/quests/{quest_key}/accept")

    @app.post("/api/quests/{player_quest_id}/progress")
    def update_quest_progress(
        player_quest_id: int,
        progress_delta: int = 1,
        objective_data: Optional[Dict[str, Any]] = None,
    ):
        """[已弃用] Quest → 差使。请改用 /api/assignments/{id}/intervene。"""
        _gone("POST /api/quests/{id}/progress")

    @app.post("/api/quests/{player_quest_id}/complete")
    def complete_quest(player_quest_id: int):
        """[已弃用] Quest → 差使结案。详见 lifecycle done。"""
        _gone("POST /api/quests/{id}/complete")

    @app.post("/api/quests/{player_quest_id}/abandon")
    def abandon_quest(player_quest_id: int):
        """[已弃用] Quest → 奏请。请改用 /api/petitions/{id}/reject。"""
        _gone("POST /api/quests/{id}/abandon")

    @app.get("/api/quests/npc/{npc_name}")
    def get_npc_quests(npc_name: str):
        """[已弃用] Quest → 奏请。请改用 GET /api/petitions?npc={name}。"""
        _gone("GET /api/quests/npc/{name}")

    @app.post("/api/quests/sync/check_expiry")
    def check_quest_expiry():
        """[已弃用] 差使逾期由 lifecycle 自动结算。"""
        _gone("POST /api/quests/sync/check_expiry")
