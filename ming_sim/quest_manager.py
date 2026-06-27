"""Quest system core API.

This module provides the main QuestManager class that handles
quest lifecycle: creation, acceptance, progress tracking, and completion.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from ming_sim.models import GameState


@dataclass
class QuestObjective:
    """Represents a single quest objective."""

    key: str
    type: str
    description: str
    current: int = 0
    target: int = 1
    data: Dict[str, Any] = field(default_factory=dict)
    completed: bool = False
    completed_at: Optional[int] = None


@dataclass
class QuestReward:
    """Represents quest rewards."""

    imperial_power: int = 0
    public_trust: int = 0
    faction_satisfaction: Dict[str, int] = field(default_factory=dict)
    npc_trust: Dict[str, int] = field(default_factory=dict)
    items: List[str] = field(default_factory=list)
    unlocks: List[str] = field(default_factory=list)


@dataclass
class Quest:
    """Represents a quest template."""

    quest_key: str
    title: str
    description: str = ""
    category: str = "side"
    tier: int = 1

    # Objectives
    objective_type: str = ""
    objective_config: Dict[str, Any] = field(default_factory=dict)

    # Rewards
    reward_config: Dict[str, Any] = field(default_factory=dict)

    # Prerequisites
    prerequisite_quest_keys: List[str] = field(default_factory=list)
    exclusive_of_quest_keys: List[str] = field(default_factory=list)

    # Requirements
    min_turn: int = 0
    max_turn: int = 0
    required_faction_satisfaction: float = 0.0
    required_imperial_power: float = 0.0

    # Repeat rules
    is_repeatable: bool = False
    repeat_interval_turns: int = 0
    daily_reset: bool = False

    # Metadata
    source_type: str = ""
    source_id: str = ""


@dataclass
class PlayerQuest:
    """Represents a player's active quest instance."""

    id: int
    quest_key: str
    status: str = "available"

    # Progress
    progress_current: int = 0
    progress_target: int = 1
    objective_data: Dict[str, Any] = field(default_factory=dict)

    # Time tracking
    accepted_turn: int = 0
    completed_turn: int = 0
    expires_turn: int = 0
    last_progress_turn: int = 0

    # NPCs
    source_npc_name: str = ""
    target_npc_name: str = ""

    # Related
    related_issue_id: int = 0

    # Rewards
    reward_claimed: bool = False

    # Cached quest data
    quest: Optional[Quest] = None


class QuestManager:
    """Main quest system manager."""

    def __init__(self, db: Any):
        self.db = db
        self._cache: Dict[str, Quest] = {}

    def create_quest(
        self,
        quest_key: str,
        title: str,
        *,
        description: str = "",
        category: str = "side",
        tier: int = 1,
        objective_type: str = "",
        objective_config: Optional[Dict[str, Any]] = None,
        reward_config: Optional[Dict[str, Any]] = None,
        prerequisite_quest_keys: Optional[List[str]] = None,
        min_turn: int = 0,
        max_turn: int = 0,
        is_repeatable: bool = False,
        **kwargs: Any,
    ) -> Quest:
        """Create a new quest template."""
        quest = Quest(
            quest_key=quest_key,
            title=title,
            description=description,
            category=category,
            tier=tier,
            objective_type=objective_type,
            objective_config=objective_config or {},
            reward_config=reward_config or {},
            prerequisite_quest_keys=prerequisite_quest_keys or [],
            min_turn=min_turn,
            max_turn=max_turn,
            is_repeatable=is_repeatable,
            **kwargs,
        )

        # Save to database
        self.db.conn.execute(
            """
            INSERT OR REPLACE INTO quests
                (quest_key, title, description, category, tier,
                 objective_type, objective_config, reward_config,
                 prerequisite_quest_keys, min_turn, max_turn, is_repeatable,
                 source_type, source_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                quest.quest_key,
                quest.title,
                quest.description,
                quest.category,
                quest.tier,
                quest.objective_type,
                json.dumps(quest.objective_config, ensure_ascii=False),
                json.dumps(quest.reward_config, ensure_ascii=False),
                json.dumps(quest.prerequisite_quest_keys, ensure_ascii=False),
                quest.min_turn,
                quest.max_turn,
                int(quest.is_repeatable),
                kwargs.get("source_type", ""),
                kwargs.get("source_id", ""),
            ),
        )
        self.db.conn.commit()

        self._cache[quest_key] = quest
        return quest

    def get_quest(self, quest_key: str) -> Optional[Quest]:
        """Get a quest template by key."""
        if quest_key in self._cache:
            return self._cache[quest_key]

        row = self.db.conn.execute(
            "SELECT * FROM quests WHERE quest_key=?", (quest_key,)
        ).fetchone()

        if not row:
            return None

        # Helper to safely get row values (sqlite3.Row doesn't support .get())
        def get_col(row, key, default=""):
            try:
                return row[key]
            except (KeyError, IndexError):
                return default

        quest = Quest(
            quest_key=str(row["quest_key"]),
            title=str(row["title"]),
            description=str(get_col(row, "description", "")),
            category=str(row["category"]),
            tier=int(row["tier"]),
            objective_type=str(row["objective_type"]),
            objective_config=json.loads(row["objective_config"]),
            reward_config=json.loads(get_col(row, "reward_config") or "{}"),
            prerequisite_quest_keys=json.loads(get_col(row, "prerequisite_quest_keys") or "[]"),
            min_turn=int(get_col(row, "min_turn") or 0),
            max_turn=int(get_col(row, "max_turn") or 0),
            is_repeatable=bool(get_col(row, "is_repeatable")),
            source_type=str(get_col(row, "source_type") or ""),
            source_id=str(get_col(row, "source_id") or ""),
        )

        self._cache[quest_key] = quest
        return quest

    def is_quest_available(self, quest_key: str, state: GameState) -> bool:
        """Check if a quest is available to the player."""
        quest = self.get_quest(quest_key)
        if not quest:
            return False

        # Check turn range
        if quest.min_turn > 0 and state.turn < quest.min_turn:
            return False
        if quest.max_turn > 0 and state.turn > quest.max_turn:
            return False

        # Check prerequisites
        for prereq_key in quest.prerequisite_quest_keys:
            if not self._is_quest_completed(prereq_key):
                return False

        # Check if already active/completed (unless repeatable)
        if not quest.is_repeatable:
            existing = self.get_player_quest(quest_key)
            if existing and existing.status in ("active", "completed"):
                return False

        return True

    def _is_quest_completed(self, quest_key: str) -> bool:
        """Check if a quest has been completed."""
        row = self.db.conn.execute(
            """
            SELECT status FROM player_quests
            WHERE quest_key=? AND status='completed'
            LIMIT 1
            """,
            (quest_key,),
        ).fetchone()
        return row is not None

    def get_player_quest(self, quest_key: str, player_id: int = 1) -> Optional[PlayerQuest]:
        """Get a player's quest instance."""
        row = self.db.conn.execute(
            """
            SELECT * FROM player_quests
            WHERE quest_key=? AND player_id=?
            ORDER BY id DESC LIMIT 1
            """,
            (quest_key, player_id),
        ).fetchone()

        if not row:
            return None

        quest = self.get_quest(quest_key)
        return PlayerQuest(
            id=int(row["id"]),
            quest_key=str(row["quest_key"]),
            status=str(row["status"]),
            progress_current=int(row["progress_current"] or 0),
            progress_target=int(row["progress_target"] or 1),
            objective_data=json.loads(row["objective_data"] or "{}"),
            accepted_turn=int(row["accepted_turn"] or 0),
            completed_turn=int(row["completed_turn"] or 0),
            expires_turn=int(row["expires_turn"] or 0),
            last_progress_turn=int(row["last_progress_turn"] or 0),
            source_npc_name=str(row["source_npc_name"] or ""),
            target_npc_name=str(row["target_npc_name"] or ""),
            related_issue_id=int(row["related_issue_id"] or 0),
            reward_claimed=bool(row["reward_claimed"]),
            quest=quest,
        )

    def accept_quest(
        self,
        quest_key: str,
        *,
        player_id: int = 1,
        source_npc_name: str = "",
        expires_turn: int = 0,
        state: Optional[GameState] = None,
    ) -> Optional[PlayerQuest]:
        """Accept a quest, creating a player quest instance."""
        if not self.is_quest_available(quest_key, state):
            return None

        quest = self.get_quest(quest_key)
        if not quest:
            return None

        # Calculate expiry
        if expires_turn == 0 and state:
            expires_turn = state.turn + 12  # Default 12 turns (~1 year)

        cur = self.db.conn.execute(
            """
            INSERT INTO player_quests
                (quest_key, player_id, status, progress_current, progress_target,
                 accepted_turn, expires_turn, source_npc_name, objective_data)
            VALUES (?, ?, 'active', 0, ?, ?, ?, ?, ?)
            """,
            (
                quest_key,
                player_id,
                quest.objective_config.get("target_count", 1),
                state.turn if state else 0,
                expires_turn,
                source_npc_name,
                json.dumps(quest.objective_config, ensure_ascii=False),
            ),
        )

        player_quest_id = int(cur.lastrowid)

        # Log event
        self.db.conn.execute(
            """
            INSERT INTO quest_events (player_quest_id, event_type, event_data, turn)
            VALUES (?, 'accepted', ?, ?)
            """,
            (player_quest_id, f"接受任务：{quest.title}", state.turn if state else 0),
        )

        self.db.conn.commit()
        return self.get_player_quest_by_id(player_quest_id)

    def get_player_quest_by_id(self, player_quest_id: int) -> Optional[PlayerQuest]:
        """Get a player quest by its ID."""
        row = self.db.conn.execute(
            "SELECT * FROM player_quests WHERE id=?", (player_quest_id,)
        ).fetchone()

        if not row:
            return None

        quest_key = str(row["quest_key"])
        quest = self.get_quest(quest_key)

        return PlayerQuest(
            id=int(row["id"]),
            quest_key=quest_key,
            status=str(row["status"]),
            progress_current=int(row["progress_current"] or 0),
            progress_target=int(row["progress_target"] or 1),
            objective_data=json.loads(row["objective_data"] or "{}"),
            accepted_turn=int(row["accepted_turn"] or 0),
            completed_turn=int(row["completed_turn"] or 0),
            expires_turn=int(row["expires_turn"] or 0),
            last_progress_turn=int(row["last_progress_turn"] or 0),
            source_npc_name=str(row["source_npc_name"] or ""),
            target_npc_name=str(row["target_npc_name"] or ""),
            related_issue_id=int(row["related_issue_id"] or 0),
            reward_claimed=bool(row["reward_claimed"]),
            quest=quest,
        )

    def update_quest_progress(
        self,
        player_quest_id: int,
        progress_delta: int = 1,
        objective_data: Optional[Dict[str, Any]] = None,
        turn: int = 0,
    ) -> Optional[PlayerQuest]:
        """Update progress on a quest."""
        player_quest = self.get_player_quest_by_id(player_quest_id)
        if not player_quest or player_quest.status != "active":
            return None

        new_progress = player_quest.progress_current + progress_delta
        new_progress = min(new_progress, player_quest.progress_target)

        self.db.conn.execute(
            """
            UPDATE player_quests
            SET progress_current=?, last_progress_turn=?, objective_data=?
            WHERE id=?
            """,
            (
                new_progress,
                turn,
                json.dumps(objective_data or player_quest.objective_data, ensure_ascii=False),
                player_quest_id,
            ),
        )

        # Log event
        self.db.conn.execute(
            """
            INSERT INTO quest_events (player_quest_id, event_type, event_data, turn)
            VALUES (?, 'progress', ?, ?)
            """,
            (player_quest_id, f"进度更新：{new_progress}/{player_quest.progress_target}", turn),
        )

        self.db.conn.commit()

        # Check if completed
        if new_progress >= player_quest.progress_target:
            self._mark_quest_completed(player_quest_id, turn)

        return self.get_player_quest_by_id(player_quest_id)

    def _mark_quest_completed(self, player_quest_id: int, turn: int) -> None:
        """Mark a quest as completed."""
        self.db.conn.execute(
            """
            UPDATE player_quests
            SET status='completed', completed_turn=?
            WHERE id=?
            """,
            (turn, player_quest_id),
        )

        # Log event
        self.db.conn.execute(
            """
            INSERT INTO quest_events (player_quest_id, event_type, event_data, turn)
            VALUES (?, 'completed', ?, ?)
            """,
            (player_quest_id, "任务完成", turn),
        )

    def complete_quest(
        self,
        player_quest_id: int,
        state: GameState,
    ) -> Dict[str, Any]:
        """Complete a quest and grant rewards."""
        player_quest = self.get_player_quest_by_id(player_quest_id)
        if not player_quest or player_quest.status != "completed":
            return {"success": False, "reason": "任务未完成或不存在"}

        if player_quest.reward_claimed:
            return {"success": False, "reason": "奖励已领取"}

        quest = player_quest.quest
        if not quest:
            return {"success": False, "reason": "任务配置缺失"}

        rewards = quest.reward_config
        result: Dict[str, Any] = {"success": True, "rewards": {}}

        # Grant imperial power
        if rewards.get("imperial_power", 0) > 0:
            delta = int(rewards["imperial_power"])
            # Update imperial power in game state
            result["rewards"]["imperial_power"] = delta

        # Grant NPC trust
        npc_rewards = rewards.get("npc_trust", {})
        if isinstance(npc_rewards, dict):
            for npc_name, delta in npc_rewards.items():
                # Update NPC trust in database
                self.db.conn.execute(
                    "UPDATE characters SET emp_trust=MIN(100, emp_trust+?) WHERE name=?",
                    (int(delta), str(npc_name)),
                )
            result["rewards"]["npc_trust"] = npc_rewards

        # Mark rewards as claimed
        self.db.conn.execute(
            "UPDATE player_quests SET reward_claimed=TRUE WHERE id=?",
            (player_quest_id,),
        )

        # Log event
        self.db.conn.execute(
            """
            INSERT INTO quest_events (player_quest_id, event_type, event_data, turn)
            VALUES (?, 'reward_claimed', ?, ?)
            """,
            (player_quest_id, f"领取奖励：{quest.title}", state.turn),
        )

        self.db.conn.commit()
        return result

    def abandon_quest(self, player_quest_id: int, turn: int) -> bool:
        """Abandon a quest."""
        player_quest = self.get_player_quest_by_id(player_quest_id)
        if not player_quest or player_quest.status not in ("active", "available"):
            return False

        self.db.conn.execute(
            "UPDATE player_quests SET status='cancelled', completed_turn=? WHERE id=?",
            (turn, player_quest_id),
        )

        # Log event
        self.db.conn.execute(
            """
            INSERT INTO quest_events (player_quest_id, event_type, event_data, turn)
            VALUES (?, 'abandoned', ?, ?)
            """,
            (player_quest_id, "放弃任务", turn),
        )

        self.db.conn.commit()
        return True

    def get_available_quests(self, state: GameState) -> List[Quest]:
        """Get all available quests."""
        rows = self.db.conn.execute("SELECT quest_key FROM quests").fetchall()

        available = []
        for row in rows:
            quest_key = str(row["quest_key"])
            if self.is_quest_available(quest_key, state):
                quest = self.get_quest(quest_key)
                if quest:
                    available.append(quest)

        # Sort by tier then title
        available.sort(key=lambda q: (q.tier, q.title))
        return available

    def get_active_player_quests(self, player_id: int = 1) -> List[PlayerQuest]:
        """Get all active quests for a player."""
        rows = self.db.conn.execute(
            """
            SELECT id FROM player_quests
            WHERE player_id=? AND status='active'
            ORDER BY accepted_turn ASC
            """,
            (player_id,),
        ).fetchall()

        active = []
        for row in rows:
            pq = self.get_player_quest_by_id(int(row["id"]))
            if pq:
                active.append(pq)

        return active

    def get_completed_quests(self, player_id: int = 1) -> List[PlayerQuest]:
        """Get all completed quests for a player."""
        rows = self.db.conn.execute(
            """
            SELECT id FROM player_quests
            WHERE player_id=? AND status='completed'
            ORDER BY completed_turn DESC
            """,
            (player_id,),
        ).fetchall()

        completed = []
        for row in rows:
            pq = self.get_player_quest_by_id(int(row["id"]))
            if pq:
                completed.append(pq)

        return completed

    def check_quest_expiry(self, turn: int) -> List[PlayerQuest]:
        """Check and mark expired quests."""
        rows = self.db.conn.execute(
            """
            SELECT id FROM player_quests
            WHERE status='active' AND expires_turn > 0 AND expires_turn <= ?
            """,
            (turn,),
        ).fetchall()

        expired = []
        for row in rows:
            player_quest_id = int(row["id"])
            self.db.conn.execute(
                "UPDATE player_quests SET status='failed', completed_turn=? WHERE id=?",
                (turn, player_quest_id),
            )

            # Log event
            self.db.conn.execute(
                """
                INSERT INTO quest_events (player_quest_id, event_type, event_data, turn)
                VALUES (?, 'failed', ?, ?)
                """,
                (player_quest_id, "任务过期失败", turn),
            )

            pq = self.get_player_quest_by_id(player_quest_id)
            if pq:
                expired.append(pq)

        if expired:
            self.db.conn.commit()

        return expired


def get_quest_manager(db: Any) -> QuestManager:
    """Get or create a quest manager instance."""
    return QuestManager(db)
