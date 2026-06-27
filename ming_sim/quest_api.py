"""Quest system Web API routes.

This module provides FastAPI endpoints for the quest system.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import HTTPException


def register_quest_routes(app, get_db_func):
    """Register quest system API routes."""

    @app.get("/api/quests")
    def get_quests(include_completed: bool = False):
        """获取所有任务列表"""
        db = get_db_func()
        from ming_sim.quest_loader import initialize_quest_system
        from ming_sim.quest_manager import get_quest_manager

        # Ensure quest system is initialized
        initialize_quest_system(db, "/Users/zhujianzheng/Desktop/Ming/content")

        manager = get_quest_manager(db)
        available = manager.get_available_quests(db.session.state)
        active = manager.get_active_player_quests()

        result = {
            "available": [
                {
                    "quest_key": q.quest_key,
                    "title": q.title,
                    "description": q.description,
                    "category": q.category,
                    "tier": q.tier,
                }
                for q in available
            ],
            "active": [
                {
                    "id": pq.id,
                    "quest_key": pq.quest_key,
                    "title": pq.quest.title if pq.quest else pq.quest_key,
                    "status": pq.status,
                    "progress_current": pq.progress_current,
                    "progress_target": pq.progress_target,
                    "expires_turn": pq.expires_turn,
                }
                for pq in active
            ],
        }

        if include_completed:
            completed = manager.get_completed_quests()
            result["completed"] = [
                {
                    "id": pq.id,
                    "quest_key": pq.quest_key,
                    "title": pq.quest.title if pq.quest else pq.quest_key,
                    "reward_claimed": pq.reward_claimed,
                }
                for pq in completed
            ]

        return result

    @app.get("/api/quests/{quest_key}")
    def get_quest_detail(quest_key: str):
        """获取任务详情"""
        db = get_db_func()
        from ming_sim.quest_manager import get_quest_manager

        manager = get_quest_manager(db)
        quest = manager.get_quest(quest_key)
        player_quest = manager.get_player_quest(quest_key)

        if not quest:
            raise HTTPException(status_code=404, detail="任务不存在")

        result = {
            "quest_key": quest.quest_key,
            "title": quest.title,
            "description": quest.description,
            "category": quest.category,
            "tier": quest.tier,
            "objective_type": quest.objective_type,
            "objective_config": quest.objective_config,
            "reward_config": quest.reward_config,
            "prerequisite_quest_keys": quest.prerequisite_quest_keys,
            "min_turn": quest.min_turn,
            "max_turn": quest.max_turn,
        }

        if player_quest:
            result["player_progress"] = {
                "id": player_quest.id,
                "status": player_quest.status,
                "progress_current": player_quest.progress_current,
                "progress_target": player_quest.progress_target,
                "accepted_turn": player_quest.accepted_turn,
                "expires_turn": player_quest.expires_turn,
                "source_npc_name": player_quest.source_npc_name,
            }

        return result

    @app.post("/api/quests/{quest_key}/accept")
    def accept_quest(quest_key: str, source_npc_name: str = ""):
        """接受任务"""
        db = get_db_func()
        from ming_sim.quest_manager import get_quest_manager

        manager = get_quest_manager(db)
        player_quest = manager.accept_quest(
            quest_key,
            source_npc_name=source_npc_name,
            state=db.session.state,
        )

        if not player_quest:
            raise HTTPException(status_code=400, detail="无法接受任务（可能不可用或条件未满足）")

        return {
            "success": True,
            "player_quest_id": player_quest.id,
            "quest_key": player_quest.quest_key,
            "status": player_quest.status,
        }

    @app.post("/api/quests/{player_quest_id}/progress")
    def update_quest_progress(
        player_quest_id: int,
        progress_delta: int = 1,
        objective_data: Optional[Dict[str, Any]] = None,
    ):
        """更新任务进度"""
        db = get_db_func()
        from ming_sim.quest_manager import get_quest_manager

        manager = get_quest_manager(db)
        updated = manager.update_quest_progress(
            player_quest_id,
            progress_delta=progress_delta,
            objective_data=objective_data,
            turn=db.session.state.turn,
        )

        if not updated:
            raise HTTPException(status_code=400, detail="无法更新任务进度")

        return {
            "success": True,
            "player_quest_id": updated.id,
            "status": updated.status,
            "progress_current": updated.progress_current,
            "progress_target": updated.progress_target,
            "completed": updated.status == "completed",
        }

    @app.post("/api/quests/{player_quest_id}/complete")
    def complete_quest(player_quest_id: int):
        """完成任务并领取奖励"""
        db = get_db_func()
        from ming_sim.quest_manager import get_quest_manager

        manager = get_quest_manager(db)
        result = manager.complete_quest(player_quest_id, db.session.state)

        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("reason", "无法完成任务"))

        return result

    @app.post("/api/quests/{player_quest_id}/abandon")
    def abandon_quest(player_quest_id: int):
        """放弃任务"""
        db = get_db_func()
        from ming_sim.quest_manager import get_quest_manager

        manager = get_quest_manager(db)
        success = manager.abandon_quest(player_quest_id, db.session.state.turn)

        if not success:
            raise HTTPException(status_code=400, detail="无法放弃任务")

        return {"success": True}

    @app.get("/api/quests/npc/{npc_name}")
    def get_npc_quests(npc_name: str):
        """获取特定NPC的任务"""
        db = get_db_func()
        from ming_sim.quest_manager import get_quest_manager

        manager = get_quest_manager(db)

        # Get quests where this NPC is the source
        available = [
            q for q in manager.get_available_quests(db.session.state)
            if q.source_type == "npc_proposal" and q.source_id == npc_name
        ]

        # Get active quests from this NPC
        active = [
            pq
            for pq in manager.get_active_player_quests()
            if pq.source_npc_name == npc_name
        ]

        # Get completable quests with this NPC
        completable = [
            pq
            for pq in manager.get_active_player_quests()
            if pq.status == "active"
            and pq.progress_current >= pq.progress_target
            and (pq.target_npc_name == npc_name or (pq.quest and pq.quest.objective_type == "dialogue_agreement" and pq.quest.objective_config.get("target_npc") == npc_name))
        ]

        return {
            "available": [
                {
                    "quest_key": q.quest_key,
                    "title": q.title,
                    "description": q.description,
                    "category": q.category,
                    "tier": q.tier,
                }
                for q in available
            ],
            "active": [
                {
                    "id": pq.id,
                    "quest_key": pq.quest_key,
                    "title": pq.quest.title if pq.quest else pq.quest_key,
                    "progress_current": pq.progress_current,
                    "progress_target": pq.progress_target,
                }
                for pq in active
            ],
            "completable": [
                {
                    "id": pq.id,
                    "quest_key": pq.quest_key,
                    "title": pq.quest.title if pq.quest else pq.quest_key,
                    "reward_config": pq.quest.reward_config if pq.quest else {},
                }
                for pq in completable
            ],
        }

    @app.post("/api/quests/sync/check_expiry")
    def check_quest_expiry():
        """检查任务过期"""
        db = get_db_func()
        from ming_sim.quest_manager import get_quest_manager

        manager = get_quest_manager(db)
        expired = manager.check_quest_expiry(db.session.state.turn)

        return {
            "expired_count": len(expired),
            "expired": [
                {
                    "id": pq.id,
                    "quest_key": pq.quest_key,
                    "title": pq.quest.title if pq.quest else pq.quest_key,
                }
                for pq in expired
            ],
        }
