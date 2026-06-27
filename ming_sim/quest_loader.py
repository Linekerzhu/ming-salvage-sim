"""Quest loader from JSON configuration.

This module provides functionality to load quest definitions from
JSON files and populate the quest database.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from ming_sim.quest_manager import QuestManager, get_quest_manager


class QuestLoader:
    """Load quests from JSON configuration files."""

    def __init__(self, quest_manager: QuestManager):
        self.quest_manager = quest_manager

    def load_from_json(self, json_path: str) -> Dict[str, Any]:
        """Load petitions from a JSON file.

        兼容两种结构：新 ``petitions`` 数组（NPC 奏请，推荐）与旧 ``quests`` 数组
        （向后兼容）。新结构字段：petition_key/title/description/proposer_office/
        proposer_faction/proposer_name/draft_directive/category_hint/stakes/tier。
        """
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            return {"success": False, "error": str(e)}

        results = {
            "success": True,
            "petitions_created": 0,
            "quests_created": 0,
            "errors": []
        }

        # 优先读新 petitions 结构
        petitions = data.get("petitions")
        if isinstance(petitions, list):
            for item in petitions:
                try:
                    self._create_petition_from_dict(item)
                    results["petitions_created"] += 1
                except Exception as e:
                    results["errors"].append(f"奏请 {item.get('petition_key', '?')} 加载失败: {e}")
            return results

        # 向后兼容：旧 quests 结构
        for quest_data in data.get("quests", []):
            try:
                self._create_quest_from_dict(quest_data)
                results["quests_created"] += 1
            except Exception as e:
                results["errors"].append(f"任务 {quest_data.get('quest_key', '?')} 加载失败: {e}")

        return results

    def _create_petition_from_dict(self, data: Dict[str, Any]) -> None:
        """Create a petition template (repositioned quest_* → NPC 奏请)。

        奏请模板存进 quests 表（复用基础设施），source_type='npc_petition'，
        draft_directive / proposer 信息存进 objective_config 供 grant_petition 取用。
        """
        self.quest_manager.create_quest(
            quest_key=str(data["petition_key"]),
            title=str(data.get("title") or data["petition_key"]),
            description=str(data.get("description", "")),
            category=str(data.get("category_hint") or "misc"),
            tier=int(data.get("tier", 1) or 1),
            objective_type="npc_petition",
            objective_config={
                "draft_directive": str(data.get("draft_directive") or data.get("title") or ""),
                "proposer_office": str(data.get("proposer_office") or ""),
                "proposer_faction": str(data.get("proposer_faction") or ""),
                "proposer_name": str(data.get("proposer_name") or ""),
                "stakes": str(data.get("stakes") or ""),
                "trigger_hint": data.get("trigger_hint") or {},
            },
            reward_config={},
            prerequisite_quest_keys=data.get("prerequisite_petition_keys", []),
            min_turn=int(data.get("min_turn", 0) or 0),
            max_turn=int(data.get("max_turn", 0) or 0),
            is_repeatable=bool(data.get("is_repeatable", False)),
            source_type="npc_petition",
            source_id=str(data.get("proposer_name") or data.get("proposer_office") or ""),
        )

    def _create_quest_from_dict(self, data: Dict[str, Any]) -> None:
        """Create a quest from dictionary data."""
        self.quest_manager.create_quest(
            quest_key=data["quest_key"],
            title=data["title"],
            description=data.get("description", ""),
            category=data.get("category", "side"),
            tier=data.get("tier", 1),
            objective_type=data.get("objective_type", ""),
            objective_config=data.get("objective_config", {}),
            reward_config=data.get("reward_config", {}),
            prerequisite_quest_keys=data.get("prerequisite_quest_keys", []),
            min_turn=data.get("min_turn", 0),
            max_turn=data.get("max_turn", 0),
            is_repeatable=data.get("is_repeatable", False),
            repeat_interval_turns=data.get("repeat_interval_turns", 0),
            daily_reset=data.get("daily_reset", False),
            source_type=data.get("source_type", ""),
            source_id=data.get("source_id", ""),
        )

    def load_all_quests(self, content_dir: str) -> Dict[str, Any]:
        """Load all quest files from content directory."""
        content_path = Path(content_dir)
        quest_files = list(content_path.glob("quests*.json"))

        if not quest_files:
            # Try examples file
            examples_file = content_path / "quests_examples.json"
            if examples_file.exists():
                return self.load_from_json(str(examples_file))
            return {"success": True, "quests_created": 0, "message": "未找到任务配置文件"}

        total_results = {
            "success": True,
            "quests_created": 0,
            "petitions_created": 0,
            "files_loaded": 0,
            "errors": []
        }

        for quest_file in quest_files:
            result = self.load_from_json(str(quest_file))
            if result["success"]:
                total_results["quests_created"] += result.get("quests_created", 0)
                total_results["petitions_created"] += result.get("petitions_created", 0)
                total_results["files_loaded"] += 1
            else:
                total_results["errors"].append(f"{quest_file.name}: {result.get('error', '?')}")

        return total_results


def initialize_quest_system(db: Any, content_dir: str) -> Dict[str, Any]:
    """Initialize the quest system with all quests from content directory."""
    from ming_sim.quest_db import apply_quest_schema, needs_migration

    # Apply schema if needed
    if needs_migration(db.conn):
        apply_quest_schema(db.conn)

    # Load quests
    manager = get_quest_manager(db)
    loader = QuestLoader(manager)
    return loader.load_all_quests(content_dir)


# Quest integration helpers for existing systems

def quest_from_dialogue_proposal(
    db: Any,
    proposal_data: Dict[str, Any],
    source_npc: str,
    state: Any,
) -> Optional[int]:
    """Create a quest from a dialogue proposal."""
    manager = get_quest_manager(db)

    quest_key = proposal_data.get("quest_key", f"dialogue_{source_npc}_{state.turn}")
    quest = manager.create_quest(
        quest_key=quest_key,
        title=proposal_data.get("title", "奏对任务"),
        description=proposal_data.get("description", ""),
        category="side",
        objective_type="dialogue_agreement",
        objective_config=proposal_data.get("objective_config", {}),
        reward_config=proposal_data.get("reward_config", {}),
        source_type="dialogue_proposal",
        source_id=source_npc,
    )

    # Auto-accept if proposal indicates so
    if proposal_data.get("auto_accept", False):
        player_quest = manager.accept_quest(
            quest_key,
            source_npc_name=source_npc,
            state=state,
        )
        return player_quest.id if player_quest else None

    return None


def update_quest_from_dialogue(
    db: Any,
    player_quest_id: int,
    dialogue_outcome: Dict[str, Any],
    turn: int,
) -> Optional[Any]:
    """Update quest progress from dialogue outcome."""
    manager = get_quest_manager(db)

    progress_delta = dialogue_outcome.get("progress_delta", 0)
    objective_data = dialogue_outcome.get("objective_data")

    return manager.update_quest_progress(
        player_quest_id,
        progress_delta=progress_delta,
        objective_data=objective_data,
        turn=turn,
    )


def check_quest_completion_from_directive(
    db: Any,
    directive_data: Dict[str, Any],
    turn: int,
) -> List[Dict[str, Any]]:
    """Check and update quests that can be completed by a directive."""
    manager = get_quest_manager(db)

    completed = []
    active_quests = manager.get_active_player_quests()

    for player_quest in active_quests:
        if player_quest.quest and player_quest.quest.objective_type == "issue_directive":
            objective_config = player_quest.quest.objective_config

            # Check if directive matches quest objective
            directive_templates = objective_config.get("directive_templates", [])
            if any(template in directive_data.get("text", "") for template in directive_templates):
                # Complete the quest
                updated = manager.update_quest_progress(player_quest.id, progress_delta=1, turn=turn)
                if updated and updated.status == "completed":
                    completed.append({
                        "player_quest_id": player_quest.id,
                        "quest_key": player_quest.quest_key,
                        "title": player_quest.quest.title if player_quest.quest else "",
                    })

    return completed
