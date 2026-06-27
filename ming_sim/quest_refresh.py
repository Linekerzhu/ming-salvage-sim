"""Quest refresh system.

This module provides automatic quest refresh based on game state changes.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from ming_sim.models import GameState
from ming_sim.quest_manager import QuestManager, get_quest_manager


class QuestRefreshTrigger:
    """Base class for quest refresh triggers."""

    def should_refresh(self, context: "QuestRefreshContext") -> bool:
        """Check if this trigger should fire."""
        raise NotImplementedError


class TurnRangeTrigger(QuestRefreshTrigger):
    """Refresh quests when turn reaches certain points."""

    def __init__(self, turn_points: List[int]):
        self.turn_points = sorted(turn_points)

    def should_refresh(self, context: "QuestRefreshContext") -> bool:
        if not context.prev_turn or not context.current_turn:
            return False
        prev, curr = context.prev_turn, context.current_turn

        # Check if we crossed any turn point
        for point in self.turn_points:
            if prev < point <= curr:
                return True
        return False


class PrerequisiteCompletedTrigger(QuestRefreshTrigger):
    """Refresh quests when a prerequisite quest is completed."""

    def __init__(self):
        self.last_completed_count = 0

    def should_refresh(self, context: "QuestRefreshContext") -> bool:
        current_count = len(context.manager.get_completed_quests())
        if current_count > self.last_completed_count:
            self.last_completed_count = current_count
            return True
        return False


class EventTrigger(QuestRefreshTrigger):
    """Refresh quests when specific game events occur."""

    def __init__(self, event_types: List[str]):
        self.event_types = set(event_types)

    def should_refresh(self, context: "QuestRefreshContext") -> bool:
        return context.last_event_type in self.event_types


class DailyResetTrigger(QuestRefreshTrigger):
    """Refresh daily quests at the start of each month/period."""

    def __init__(self):
        self.last_period = None

    def should_refresh(self, context: "QuestRefreshContext") -> bool:
        if not context.state:
            return False
        curr_period = context.state.period
        if self.last_period != curr_period:
            self.last_period = curr_period
            return True
        return False


class QuestRefreshContext:
    """Context for quest refresh evaluation."""

    def __init__(
        self,
        manager: QuestManager,
        state: Optional[GameState] = None,
        prev_turn: Optional[int] = None,
        current_turn: Optional[int] = None,
        last_event_type: Optional[str] = None,
    ):
        self.manager = manager
        self.state = state
        self.prev_turn = prev_turn
        self.current_turn = current_turn
        self.last_event_type = last_event_type


class QuestRefreshManager:
    """Manages automatic quest refresh based on game state changes."""

    def __init__(self, db: Any):
        self.db = db
        self.manager = get_quest_manager(db)
        self.triggers: List[QuestRefreshTrigger] = []
        self._setup_default_triggers()

    def _setup_default_triggers(self):
        """Setup default refresh triggers."""
        # Turn-based triggers (e.g., at turns 10, 20, 30...)
        self.triggers.append(TurnRangeTrigger([10, 20, 36, 72]))

        # Prerequisite completion trigger
        self.triggers.append(PrerequisiteCompletedTrigger())

        # Event triggers
        self.triggers.append(EventTrigger(["rebellion_started", "faction_defeated", "emperor_died"]))

        # Daily reset trigger (monthly for Ming game)
        self.triggers.append(DailyResetTrigger())

    def add_trigger(self, trigger: QuestRefreshTrigger):
        """Add a custom refresh trigger."""
        self.triggers.append(trigger)

    def check_refresh_needed(
        self,
        state: Optional[GameState] = None,
        prev_turn: Optional[int] = None,
        current_turn: Optional[int] = None,
        last_event_type: Optional[str] = None,
    ) -> bool:
        """Check if any refresh trigger should fire."""
        context = QuestRefreshContext(
            manager=self.manager,
            state=state,
            prev_turn=prev_turn,
            current_turn=current_turn,
            last_event_type=last_event_type,
        )

        for trigger in self.triggers:
            if trigger.should_refresh(context):
                return True
        return False

    def refresh_available_quests(self, state: Optional[GameState] = None) -> Dict[str, Any]:
        """Refresh the list of available quests and return new quests."""
        if not state:
            state = self.db.session.state

        old_available_keys = {q.quest_key for q in self.manager.get_available_quests(state)}

        # Clear cache to force reload
        self.manager._cache.clear()

        new_available = self.manager.get_available_quests(state)
        new_available_keys = {q.quest_key for q in new_available}

        new_quests = new_available_keys - old_available_keys

        return {
            "total_available": len(new_available),
            "new_quests": [
                {
                    "quest_key": key,
                    "quest": next(q for q in new_available if q.quest_key == key),
                }
                for key in new_quests
            ],
            "removed_quests": old_available_keys - new_available_keys,
        }

    def reset_daily_quests(self, turn: int) -> List[Dict[str, Any]]:
        """Reset daily quests that are eligible for repeat."""
        rows = self.db.conn.execute(
            """
            SELECT id, quest_key FROM player_quests
            WHERE status='completed'
              AND reward_claimed=TRUE
            """
        ).fetchall()

        reset = []
        for row in rows:
            player_quest_id = int(row["id"])
            quest_key = str(row["quest_key"])

            quest = self.manager.get_quest(quest_key)
            if quest and quest.daily_reset:
                # Check if enough turns have passed
                pq = self.manager.get_player_quest_by_id(player_quest_id)
                if pq and pq.completed_turn:
                    turns_since = turn - pq.completed_turn
                    if turns_since >= 1:  # At least 1 turn (month) passed
                        # Create a new instance
                        new_pq = self.manager.accept_quest(
                            quest_key,
                            source_npc_name=pq.source_npc_name,
                            state=self.db.session.state,
                        )
                        if new_pq:
                            reset.append({
                                "player_quest_id": new_pq.id,
                                "quest_key": quest_key,
                                "title": quest.title,
                            })

        return reset


# Convenience function for integration

def refresh_quests_on_turn_change(db: Any, prev_turn: int, current_turn: int) -> Dict[str, Any]:
    """Refresh quests when game turn changes."""
    from ming_sim.quest_refresh import QuestRefreshManager

    refresh_mgr = QuestRefreshManager(db)

    if refresh_mgr.check_refresh_needed(
        state=db.session.state,
        prev_turn=prev_turn,
        current_turn=current_turn,
    ):
        return refresh_mgr.refresh_available_quests()

    return {"total_available": 0, "new_quests": [], "removed_quests": set()}


def refresh_quests_on_event(db: Any, event_type: str, event_data: Dict[str, Any]) -> Dict[str, Any]:
    """Refresh quests when a game event occurs."""
    from ming_sim.quest_refresh import QuestRefreshManager

    refresh_mgr = QuestRefreshManager(db)

    if refresh_mgr.check_refresh_needed(
        state=db.session.state,
        last_event_type=event_type,
    ):
        result = refresh_mgr.refresh_available_quests()

        # Auto-accept quests if event data indicates
        auto_accept = event_data.get("auto_accept_quests", [])
        for quest_key in auto_accept:
            pq = refresh_mgr.manager.accept_quest(
                quest_key,
                source_npc_name=event_data.get("source_npc", ""),
                state=db.session.state,
            )
            if pq:
                result.setdefault("auto_accepted", []).append(quest_key)

        return result

    return {"total_available": 0, "new_quests": [], "removed_quests": set()}


def refresh_daily_quests(db: Any) -> List[Dict[str, Any]]:
    """Reset daily quests (call at period/turn change)."""
    from ming_sim.quest_refresh import QuestRefreshManager

    refresh_mgr = QuestRefreshManager(db)
    return refresh_mgr.reset_daily_quests(db.session.state.turn)
