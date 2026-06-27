"""Quest system database schema and migrations.

This module defines the new quest system tables that provide a
World-of-Warcraft-style quest experience for the Ming game.
"""

from __future__ import annotations

import sqlite3
from typing import List, Tuple


def _quest_schema_version() -> int:
    return 1


def _create_quests_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS quests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quest_key TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            category TEXT NOT NULL,
            tier INTEGER DEFAULT 1,

            objective_type TEXT NOT NULL,
            objective_config TEXT NOT NULL,

            reward_config TEXT,

            prerequisite_quest_keys TEXT,
            exclusive_of_quest_keys TEXT,

            min_turn INTEGER,
            max_turn INTEGER,
            required_faction_satisfaction REAL DEFAULT 0,
            required_imperial_power REAL DEFAULT 0,

            is_repeatable BOOLEAN DEFAULT FALSE,
            repeat_interval_turns INTEGER DEFAULT 0,
            daily_reset BOOLEAN DEFAULT FALSE,

            source_type TEXT,
            source_id TEXT,

            created_at INTEGER DEFAULT (strftime('%s', 'now')),
            updated_at INTEGER DEFAULT (strftime('%s', 'now'))
        );
        """
    )

    # Indexes
    conn.execute("CREATE INDEX IF NOT EXISTS idx_quests_category ON quests(category);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_quests_tier ON quests(tier);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_quests_source ON quests(source_type, source_id);")


def _create_player_quests_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS player_quests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quest_key TEXT NOT NULL,
            player_id INTEGER DEFAULT 1,

            status TEXT NOT NULL DEFAULT 'available',

            progress_current INTEGER DEFAULT 0,
            progress_target INTEGER DEFAULT 1,
            objective_data TEXT,

            accepted_turn INTEGER,
            completed_turn INTEGER,
            expires_turn INTEGER,
            last_progress_turn INTEGER,

            source_npc_name TEXT,
            target_npc_name TEXT,
            related_issue_id INTEGER,

            reward_claimed BOOLEAN DEFAULT FALSE,

            created_at INTEGER DEFAULT (strftime('%s', 'now')),
            updated_at INTEGER DEFAULT (strftime('%s', 'now')),

            FOREIGN KEY (quest_key) REFERENCES quests(quest_key)
        );
        """
    )

    # Indexes
    conn.execute("CREATE INDEX IF NOT EXISTS idx_player_quests_status ON player_quests(status);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_player_quests_quest_key ON player_quests(quest_key);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_player_quests_source_npc ON player_quests(source_npc_name);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_player_quests_expires ON player_quests(expires_turn);")


def _create_quest_progress_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS quest_progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_quest_id INTEGER NOT NULL,
            objective_key TEXT NOT NULL,
            objective_type TEXT NOT NULL,
            progress_current INTEGER DEFAULT 0,
            progress_target INTEGER DEFAULT 1,
            data_json TEXT,
            completed_at INTEGER,
            created_at INTEGER DEFAULT (strftime('%s', 'now')),

            FOREIGN KEY (player_quest_id) REFERENCES player_quests(id)
        );
        """
    )

    conn.execute("CREATE INDEX IF NOT EXISTS idx_quest_progress_player_quest ON quest_progress(player_quest_id);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_quest_progress_objective_key ON quest_progress(objective_key);")


def _create_quest_events_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS quest_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_quest_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            event_data TEXT,
            turn INTEGER,
            created_at INTEGER DEFAULT (strftime('%s', 'now')),

            FOREIGN KEY (player_quest_id) REFERENCES player_quests(id)
        );
        """
    )

    conn.execute("CREATE INDEX IF NOT EXISTS idx_quest_events_player_quest ON quest_events(player_quest_id);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_quest_events_turn ON quest_events(turn);")


def apply_quest_schema(conn: sqlite3.Connection) -> None:
    """Apply all quest system schema changes."""
    # Ensure schema_version table exists
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            module_name TEXT PRIMARY KEY,
            version INTEGER NOT NULL,
            applied_at INTEGER
        );
        """
    )

    _create_quests_table(conn)
    _create_player_quests_table(conn)
    _create_quest_progress_table(conn)
    _create_quest_events_table(conn)

    # Mark schema version
    conn.execute(
        """
        INSERT OR REPLACE INTO schema_version (module_name, version, applied_at)
        VALUES ('quest_system', ?, strftime('%s', 'now'))
        """,
        (_quest_schema_version(),),
    )
    conn.commit()


def _get_current_schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT version FROM schema_version WHERE module_name='quest_system'"
    ).fetchone()
    return int(row["version"] if row else 0)


def needs_migration(conn: sqlite3.Connection) -> bool:
    """Check if quest system schema needs to be applied."""
    current = _get_current_schema_version(conn)
    return current < _quest_schema_version()


# Quest categories (WoW-inspired mapped to Ming context)
QUEST_CATEGORIES = {
    "campaign": "主线",
    "side": "支线",
    "daily": "日常",
    "elite": "精英",
    "rare": "稀有",
    "tutorial": "教程",
}

# Quest objective types
QUEST_OBJECTIVE_TYPES = {
    "dialogue_agreement": "对话确认",
    "issue_directive": "下诏执行",
    "wait_turns": "等待时日",
    "collect_evidence": "收集证据",
    "personnel_change": "人事任免",
    "mediate_conflict": "调停矛盾",
    "allocate_resource": "资源调配",
    "explore_region": "探索地区",
    "escort_npc": "护送NPC",
}

# Quest status values
QUEST_STATUS = {
    "hidden": "隐藏",
    "available": "可用",
    "active": "进行中",
    "completed": "已完成",
    "failed": "已失败",
    "cancelled": "已取消",
}

# Quest event types
QUEST_EVENT_TYPES = {
    "accepted": "接受任务",
    "progress": "进度更新",
    "completed": "完成任务",
    "failed": "任务失败",
    "abandoned": "放弃任务",
    "reward_claimed": "领取奖励",
}
