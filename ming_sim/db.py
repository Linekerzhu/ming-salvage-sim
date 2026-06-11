"""GameDB：所有 SQLite 持久化。L3。

init_schema 建表，seed_static_data 从 GameContent 初始化静态盘面。
GameDB 持有 self.content（GameContent），seed 类方法从中读人物/地区/军队等。
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from typing import Any, Dict, List, Optional, Tuple

from ming_sim.assets import format_money, format_money_delta
from ming_sim.constants import (
    ARMY_FIELD_ALIASES, ARMY_FIELD_LABELS, ARMY_QUANTITY_FIELDS, ARMY_SCORE_FIELDS, ARMY_TEXT_FIELDS,
    BUILDING_CATEGORIES, BUILDING_FIELD_LABELS, BUILDING_OUTPUT_METRICS,
    BUILDING_QUANTITY_FIELDS, BUILDING_SCORE_FIELDS, BUILDING_TEXT_FIELDS,
    ECONOMY_ACCOUNTS, POWER_FIELD_LABELS, POWER_SCORE_FIELDS,
    POWER_FIELD_ALIASES, POWER_TEXT_FIELDS, MONEY_UNIT, REGION_FIELD_LABELS, REGION_QUANTITY_FIELDS,
    FISCAL_SCORE_FIELDS, REGION_FIELD_ALIASES, REGION_SCORE_FIELDS, REGION_TEXT_FIELDS, TURN_UNIT,
)
from ming_sim.content import GameContent
from ming_sim.matching import match_army_id_from_text, match_region_id_from_text
from ming_sim.models import Event, GameState, monthly_amount, period_label
from ming_sim.negotiation import classify_task_kind
from ming_sim.token_stats import tlog


def normalize_office(office: str) -> str:
    """官职多职统一为半角逗号分隔：旧「兼/兼掌/兼署」与全角「，」「、」一律归一逗号，
    去空分项、去重、保序。是 office 字段落库的唯一规范化入口——所有写 characters.office
    的路径都过它，保证去重/顶缺时能按逗号分项匹配。"""
    s = (office or "").strip()
    if not s:
        return ""
    s = s.replace("兼掌", ",").replace("兼署", ",").replace("兼", ",")
    s = s.replace("，", ",").replace("、", ",")
    seen: set = set()
    parts: List[str] = []
    for p in (x.strip() for x in s.split(",")):
        if p and p not in seen:
            seen.add(p)
            parts.append(p)
    return ",".join(parts)


COURT_OFFICE_TYPES = {"内阁", "吏部", "户部", "礼部", "兵部", "刑部", "工部"}
MINISTRY_OFFICE_TYPES = {"吏部", "户部", "礼部", "兵部", "刑部", "工部"}
CHARACTER_STATUS_LABELS = {
    "active": "在朝",
    "offstage": "尚未登场",
    "candidate": "待选",
    "dismissed": "已罢黜",
    "imprisoned": "下狱",
    "exiled": "流放",
    "retired": "致仕",
    "dead": "已故",
}


def infer_office_type_from_office(office: str, current_type: str = "") -> str:
    """用 office 文本校正 office_type，避免旧标签把无实职人物塞进内阁/六部。"""
    kind = (current_type or "").strip()
    if kind == "后宫":
        return kind
    text = normalize_office(office)
    if not text:
        return "待铨" if kind in COURT_OFFICE_TYPES or not kind else kind

    if re.search(r"内阁|大学士|首辅|次辅", text):
        return "内阁"
    for ministry in MINISTRY_OFFICE_TYPES:
        if ministry in text and re.search(r"尚书|侍郎|郎中|员外郎|主事|给事中", text):
            return ministry

    if re.search(r"司礼监|秉笔太监|掌印太监|随堂太监", text):
        return "司礼监"
    if re.search(r"东厂|提督东厂", text):
        return "东厂"
    if re.search(r"锦衣卫|北镇抚司|镇抚司|都指挥使|千户", text):
        return "锦衣卫"
    if re.search(r"都察院|都御史|御史|巡按", text):
        return "都察院"
    if re.search(r"翰林院|翰林|编修|检讨|庶吉士|詹事", text):
        return "翰林院"
    if re.search(r"总督|巡抚|布政使|按察使|参政|知府|知县|兵备道|督粮", text):
        return "地方"
    if re.search(r"督师|经略|总兵|副总兵|游击|参将|守备|山海关|辽东|蓟辽|东江|大同|宣大", text):
        return "边镇"

    return "待铨" if kind in COURT_OFFICE_TYPES or not kind else kind


def infer_assignment_office_type(office: str, office_type: str = "", current_type: str = "") -> str:
    """授官/调任口径：无法归入常设体系的实职，保留为非常设官位类型。"""
    clean_office = normalize_office(office)
    explicit_type = (office_type or "").strip()
    if not clean_office:
        return infer_office_type_from_office(clean_office, explicit_type or current_type)
    if explicit_type:
        inferred = infer_office_type_from_office(clean_office, explicit_type)
    else:
        # 调任时不能让旧官署兜底吞掉新授的原创官位。
        inferred = infer_office_type_from_office(clean_office, "")
    if inferred == "待铨":
        first_title = clean_office.split(",", 1)[0].strip()
        if first_title and not re.search(r"^(前|原)|罢居|候补|归途|潜在|少年|诸生|待铨|未仕", first_title):
            return first_title[:20]
    return inferred


def effective_stored_office_type(office: str, stored_type: str = "") -> str:
    """读档/展示口径：旧存档若存了旧官署，用当前 office 文本重算有效类型。"""
    raw_type = (stored_type or "").strip()
    if raw_type == "后宫":
        return raw_type
    return infer_assignment_office_type(office, current_type=raw_type)


class GameDB:
    def __init__(self, path: str, content: Optional[GameContent] = None):
        self.path = path
        # 静态设定来源。过渡期 content 可省略，省略时自行加载；
        # 步骤7 起由 GameSession 统一传入同一份 GameContent。
        self.content = content if content is not None else GameContent.load()
        # check_same_thread=False：流式颁诏在 worker 线程跑 resolve_turn。
        # 画像后台任务不用这条连接写库，避免多个线程同时操作同一 sqlite3.Connection。
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._configure_connection()
        self._portrait_asset_lock = threading.RLock()
        # 遗产修正符缓存：legacy_modifiers 在落账热路径被频繁调用，缓存聚合结果，
        # 仅在 active 遗产集变化（insert_legacy / expire_legacies）时失效。
        self._legacy_mod_cache: Optional[Dict[str, object]] = None
        self.init_schema()

    def _configure_connection(self) -> None:
        pragmas = (
            "PRAGMA busy_timeout=5000",
            "PRAGMA journal_mode=WAL",
            "PRAGMA synchronous=NORMAL",
            "PRAGMA temp_store=MEMORY",
        )
        for pragma in pragmas:
            try:
                self.conn.execute(pragma)
            except sqlite3.DatabaseError:
                continue

    def init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS game_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                year INTEGER NOT NULL,
                period INTEGER NOT NULL,
                turn INTEGER NOT NULL,
                turn_phase TEXT NOT NULL DEFAULT 'summoning'
            );

            CREATE TABLE IF NOT EXISTS metrics (
                key TEXT PRIMARY KEY,
                value INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS offices (
                office_type TEXT PRIMARY KEY,
                skills TEXT NOT NULL,
                tools TEXT NOT NULL,
                authority_scope TEXT NOT NULL,
                power INTEGER NOT NULL,
                responsibility INTEGER NOT NULL,
                corruption_risk INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS characters (
                name TEXT PRIMARY KEY,
                office TEXT NOT NULL,
                office_type TEXT NOT NULL,
                faction TEXT NOT NULL,
                personal_skills TEXT NOT NULL,
                loyalty INTEGER NOT NULL,
                ability INTEGER NOT NULL,
                integrity INTEGER NOT NULL,
                courage INTEGER NOT NULL,
                style TEXT NOT NULL,
                birth_year INTEGER NOT NULL DEFAULT 0,
                historical_death_year INTEGER NOT NULL DEFAULT 0,
                historical_death_month INTEGER NOT NULL DEFAULT 0,
                debut_year INTEGER NOT NULL DEFAULT 0,
                debut_month INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'active',
                status_reason TEXT NOT NULL DEFAULT '',
                status_changed_turn INTEGER NOT NULL DEFAULT 0,
                power_id TEXT NOT NULL DEFAULT 'ming',
                location TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS character_offices (
                character_name TEXT PRIMARY KEY,
                office_title TEXT NOT NULL,
                office_type TEXT NOT NULL,
                source TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(character_name) REFERENCES characters(name),
                FOREIGN KEY(office_type) REFERENCES offices(office_type)
            );

            CREATE TABLE IF NOT EXISTS portrait_assets (
                asset_id TEXT PRIMARY KEY,
                character_name TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'portrait',
                dna_seed TEXT NOT NULL DEFAULT '',
                wardrobe_key TEXT NOT NULL DEFAULT '',
                prompt TEXT NOT NULL DEFAULT '',
                provider TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                mime_type TEXT NOT NULL DEFAULT 'image/png',
                image_blob BLOB,
                width INTEGER NOT NULL DEFAULT 0,
                height INTEGER NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT '',
                updated_turn INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(character_name) REFERENCES characters(name)
            );

            CREATE TABLE IF NOT EXISTS factions (
                name TEXT PRIMARY KEY,
                satisfaction INTEGER NOT NULL,
                leverage INTEGER NOT NULL,
                agenda TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS powers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL,
                leader TEXT NOT NULL,
                stance TEXT NOT NULL,
                leverage INTEGER NOT NULL,
                satisfaction INTEGER NOT NULL,
                military_strength INTEGER NOT NULL,
                cohesion INTEGER NOT NULL,
                supply INTEGER NOT NULL,
                agenda TEXT NOT NULL,
                status TEXT NOT NULL,
                last_action TEXT NOT NULL DEFAULT '',
                aliases TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS power_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                turn INTEGER NOT NULL,
                year INTEGER NOT NULL,
                period INTEGER NOT NULL,
                power_id TEXT NOT NULL,
                field TEXT NOT NULL,
                old_value TEXT NOT NULL,
                new_value TEXT NOT NULL,
                delta INTEGER,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(power_id) REFERENCES powers(id)
            );

            CREATE TABLE IF NOT EXISTS power_name_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                turn INTEGER NOT NULL,
                year INTEGER NOT NULL,
                period INTEGER NOT NULL,
                power_id TEXT NOT NULL,
                old_name TEXT NOT NULL,
                new_name TEXT NOT NULL,
                old_aliases TEXT NOT NULL DEFAULT '',
                new_aliases TEXT NOT NULL DEFAULT '',
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(power_id) REFERENCES powers(id)
            );

            CREATE TABLE IF NOT EXISTS regions (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL,
                population INTEGER NOT NULL,
                public_support INTEGER NOT NULL,
                unrest INTEGER NOT NULL,
                natural_disaster TEXT NOT NULL,
                human_disaster TEXT NOT NULL,
                registered_land INTEGER NOT NULL,
                hidden_land INTEGER NOT NULL,
                tax_per_turn INTEGER NOT NULL,
                grain_security INTEGER NOT NULL,
                gentry_resistance INTEGER NOT NULL,
                military_pressure INTEGER NOT NULL,
                status TEXT NOT NULL,
                controlled_by TEXT NOT NULL DEFAULT 'ming',
                fiscal TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(controlled_by) REFERENCES powers(id)
            );

            CREATE TABLE IF NOT EXISTS region_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                turn INTEGER NOT NULL,
                year INTEGER NOT NULL,
                period INTEGER NOT NULL,
                region_id TEXT NOT NULL,
                field TEXT NOT NULL,
                old_value TEXT NOT NULL,
                new_value TEXT NOT NULL,
                delta INTEGER,
                reason TEXT NOT NULL,
                event_id TEXT,
                edict_id INTEGER,
                actor TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(region_id) REFERENCES regions(id)
            );

            CREATE TABLE IF NOT EXISTS armies (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                station TEXT NOT NULL,
                theater TEXT NOT NULL,
                commander TEXT NOT NULL,
                controller TEXT NOT NULL,
                troop_type TEXT NOT NULL,
                manpower INTEGER NOT NULL,
                maintenance_per_turn INTEGER NOT NULL,
                supply INTEGER NOT NULL,
                morale INTEGER NOT NULL,
                training INTEGER NOT NULL,
                equipment INTEGER NOT NULL,
                arrears INTEGER NOT NULL,
                mobility INTEGER NOT NULL,
                loyalty INTEGER NOT NULL,
                status TEXT NOT NULL,
                owner_power TEXT NOT NULL DEFAULT 'ming',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(owner_power) REFERENCES powers(id)
            );

            CREATE TABLE IF NOT EXISTS army_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                turn INTEGER NOT NULL,
                year INTEGER NOT NULL,
                period INTEGER NOT NULL,
                army_id TEXT NOT NULL,
                field TEXT NOT NULL,
                old_value TEXT NOT NULL,
                new_value TEXT NOT NULL,
                delta INTEGER,
                reason TEXT NOT NULL,
                event_id TEXT,
                edict_id INTEGER,
                actor TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(army_id) REFERENCES armies(id)
            );

            CREATE TABLE IF NOT EXISTS buildings (
                id TEXT PRIMARY KEY,
                region_id TEXT NOT NULL,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                level INTEGER NOT NULL,
                condition INTEGER NOT NULL,
                maintenance INTEGER NOT NULL,
                risk INTEGER NOT NULL,
                output_metric TEXT NOT NULL DEFAULT '',
                output_amount INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                origin TEXT NOT NULL DEFAULT 'preset',
                created_turn INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(region_id) REFERENCES regions(id)
            );

            CREATE TABLE IF NOT EXISTS building_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                turn INTEGER NOT NULL,
                year INTEGER NOT NULL,
                period INTEGER NOT NULL,
                building_id TEXT NOT NULL,
                field TEXT NOT NULL,
                old_value TEXT NOT NULL,
                new_value TEXT NOT NULL,
                delta INTEGER,
                reason TEXT NOT NULL,
                event_id TEXT,
                edict_id INTEGER,
                actor TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(building_id) REFERENCES buildings(id)
            );

            CREATE TABLE IF NOT EXISTS economy_accounts (
                account TEXT PRIMARY KEY,
                metric_key TEXT NOT NULL UNIQUE,
                balance INTEGER NOT NULL,
                note TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS economy_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                turn INTEGER NOT NULL,
                year INTEGER NOT NULL,
                period INTEGER NOT NULL,
                account TEXT NOT NULL,
                delta INTEGER NOT NULL,
                balance_after INTEGER NOT NULL,
                category TEXT NOT NULL,
                reason TEXT NOT NULL,
                event_id TEXT,
                edict_id INTEGER,
                actor TEXT,
                purpose TEXT,
                target_kind TEXT,
                target_id TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(account) REFERENCES economy_accounts(account)
            );

            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                kind TEXT NOT NULL,
                summary TEXT NOT NULL,
                urgency INTEGER NOT NULL,
                severity INTEGER NOT NULL,
                credibility INTEGER NOT NULL,
                interests TEXT NOT NULL,
                audiences TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS event_triggers (
                event_id TEXT PRIMARY KEY,
                turn INTEGER NOT NULL,
                year INTEGER NOT NULL,
                period INTEGER NOT NULL,
                source TEXT NOT NULL DEFAULT 'simulation',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(event_id) REFERENCES events(id)
            );

            CREATE TABLE IF NOT EXISTS turn_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                turn INTEGER NOT NULL,
                year INTEGER NOT NULL,
                period INTEGER NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS turn_reports (
                turn INTEGER PRIMARY KEY,
                year INTEGER NOT NULL,
                period INTEGER NOT NULL,
                report TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            -- 推演链每个 agent 的原始输入/输出留痕，每回合一行，便于事后追查。
            CREATE TABLE IF NOT EXISTS turn_extractions (
                turn INTEGER PRIMARY KEY,
                year INTEGER NOT NULL,
                period INTEGER NOT NULL,
                decree_text TEXT NOT NULL DEFAULT '',
                narrative TEXT NOT NULL DEFAULT '',
                extractor_input TEXT NOT NULL DEFAULT '',
                extractor_output TEXT NOT NULL DEFAULT '',
                causal_notes TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            -- 召对聊天记录持久化，每条消息一行，进程重启不丢。
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                minister_name TEXT NOT NULL,
                turn INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_chat_messages_minister
                ON chat_messages(minister_name, id);
            CREATE INDEX IF NOT EXISTS idx_chat_messages_turn
                ON chat_messages(turn);
            CREATE INDEX IF NOT EXISTS idx_chat_messages_turn_minister
                ON chat_messages(turn, minister_name, id);

            CREATE TABLE IF NOT EXISTS chat_turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                minister_name TEXT NOT NULL,
                turn INTEGER NOT NULL,
                year INTEGER NOT NULL,
                period INTEGER NOT NULL,
                user_message_id INTEGER,
                minister_message_id INTEGER,
                agno_session_id TEXT NOT NULL DEFAULT '',
                agno_runs_before INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                undone_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_chat_turns_minister_turn
                ON chat_turns(minister_name, turn, status, id);
            CREATE INDEX IF NOT EXISTS idx_chat_turns_status_id
                ON chat_turns(status, id);

            CREATE TABLE IF NOT EXISTS chat_turn_rollback_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_turn_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                target_table TEXT NOT NULL,
                target_id TEXT NOT NULL,
                before_json TEXT NOT NULL DEFAULT '',
                after_json TEXT NOT NULL DEFAULT '',
                rollback_strategy TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(chat_turn_id) REFERENCES chat_turns(id)
            );
            CREATE INDEX IF NOT EXISTS idx_chat_turn_rollback_items_turn
                ON chat_turn_rollback_items(chat_turn_id, id);

            CREATE TABLE IF NOT EXISTS minister_stances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                turn INTEGER NOT NULL,
                year INTEGER NOT NULL,
                period INTEGER NOT NULL,
                minister_name TEXT NOT NULL,
                topic TEXT NOT NULL,
                stance TEXT NOT NULL,
                confidence INTEGER NOT NULL DEFAULT 3,
                summary TEXT NOT NULL DEFAULT '',
                conditions TEXT NOT NULL DEFAULT '',
                related_issue_id INTEGER NOT NULL DEFAULT 0,
                source_chat_turn_id INTEGER NOT NULL DEFAULT 0,
                user_message TEXT NOT NULL DEFAULT '',
                minister_answer TEXT NOT NULL DEFAULT '',
                evidence_json TEXT NOT NULL DEFAULT '{}',
                risk_tags TEXT NOT NULL DEFAULT '',
                execution_hint TEXT NOT NULL DEFAULT '',
                handshake_status TEXT NOT NULL DEFAULT 'none',
                psychological_score INTEGER NOT NULL DEFAULT 0,
                psychological_json TEXT NOT NULL DEFAULT '{}',
                agreement_id INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_minister_stances_turn
                ON minister_stances(turn, minister_name, id);
            CREATE INDEX IF NOT EXISTS idx_minister_stances_turn_id
                ON minister_stances(turn, id DESC);
            CREATE INDEX IF NOT EXISTS idx_minister_stances_minister_id
                ON minister_stances(minister_name, id DESC);
            CREATE INDEX IF NOT EXISTS idx_minister_stances_issue
                ON minister_stances(related_issue_id, turn);

            CREATE TABLE IF NOT EXISTS conversation_goals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                minister_name TEXT NOT NULL,
                action_kind TEXT NOT NULL DEFAULT 'general',
                title TEXT NOT NULL DEFAULT '',
                target_text TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                score INTEGER NOT NULL DEFAULT 0,
                threshold INTEGER NOT NULL DEFAULT 0,
                condition_status TEXT NOT NULL DEFAULT 'none',
                conditions_json TEXT NOT NULL DEFAULT '[]',
                blockers_json TEXT NOT NULL DEFAULT '[]',
                related_issue_id INTEGER NOT NULL DEFAULT 0,
                agreement_id INTEGER NOT NULL DEFAULT 0,
                source_chat_turn_id INTEGER NOT NULL DEFAULT 0,
                last_delta_json TEXT NOT NULL DEFAULT '{}',
                created_turn INTEGER NOT NULL DEFAULT 0,
                expires_turn INTEGER NOT NULL DEFAULT 0,
                abandoned_reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_conversation_goals_minister_status
                ON conversation_goals(minister_name, status, id);
            CREATE INDEX IF NOT EXISTS idx_conversation_goals_minister_id
                ON conversation_goals(minister_name, id DESC);
            CREATE INDEX IF NOT EXISTS idx_conversation_goals_status_id
                ON conversation_goals(status, id DESC);
            CREATE INDEX IF NOT EXISTS idx_conversation_goals_agreement
                ON conversation_goals(agreement_id);

            CREATE TABLE IF NOT EXISTS conversation_goal_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                goal_id INTEGER NOT NULL,
                turn INTEGER NOT NULL,
                year INTEGER NOT NULL,
                period INTEGER NOT NULL,
                minister_name TEXT NOT NULL DEFAULT '',
                event_kind TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT '',
                score_delta INTEGER NOT NULL DEFAULT 0,
                score_after INTEGER NOT NULL DEFAULT 0,
                summary TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                source_chat_turn_id INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(goal_id) REFERENCES conversation_goals(id)
            );
            CREATE INDEX IF NOT EXISTS idx_conversation_goal_events_goal
                ON conversation_goal_events(goal_id, id);

            CREATE TABLE IF NOT EXISTS xinpan_states (
                character_name TEXT PRIMARY KEY,
                dao_he REAL NOT NULL DEFAULT 0,
                shi_he REAL NOT NULL DEFAULT 0,
                fear REAL NOT NULL DEFAULT 0,
                trust_coeff REAL NOT NULL DEFAULT 1.0,
                hatred REAL NOT NULL DEFAULT 0,
                quadrant TEXT NOT NULL DEFAULT '',
                core_concerns_json TEXT NOT NULL DEFAULT '[]',
                perception_json TEXT NOT NULL DEFAULT '{}',
                flags_json TEXT NOT NULL DEFAULT '{}',
                updated_turn INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(character_name) REFERENCES characters(name)
            );

            CREATE TABLE IF NOT EXISTS xinpan_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                turn INTEGER NOT NULL,
                year INTEGER NOT NULL,
                period INTEGER NOT NULL,
                character_name TEXT NOT NULL,
                source_kind TEXT NOT NULL DEFAULT '',
                source_id TEXT NOT NULL DEFAULT '',
                event TEXT NOT NULL DEFAULT '',
                dao_delta REAL NOT NULL DEFAULT 0,
                shi_delta REAL NOT NULL DEFAULT 0,
                fear_delta REAL NOT NULL DEFAULT 0,
                hatred_delta REAL NOT NULL DEFAULT 0,
                trust_delta REAL NOT NULL DEFAULT 0,
                before_json TEXT NOT NULL DEFAULT '{}',
                after_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(character_name) REFERENCES characters(name)
            );
            CREATE INDEX IF NOT EXISTS idx_xinpan_logs_character
                ON xinpan_logs(character_name, turn, id);
            CREATE INDEX IF NOT EXISTS idx_xinpan_states_quadrant
                ON xinpan_states(quadrant);

            CREATE TABLE IF NOT EXISTS negotiation_agreements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                turn_created INTEGER NOT NULL,
                year_created INTEGER NOT NULL,
                period_created INTEGER NOT NULL,
                minister_name TEXT NOT NULL,
                topic TEXT NOT NULL,
                core_topic TEXT NOT NULL DEFAULT '',
                target_text TEXT NOT NULL DEFAULT '',
                action_kind TEXT NOT NULL DEFAULT 'general',
                promise_type TEXT NOT NULL DEFAULT '',
                stakes TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                condition_status TEXT NOT NULL DEFAULT 'pending',
                target_status TEXT NOT NULL DEFAULT 'pending_conditions',
                stance_id INTEGER NOT NULL DEFAULT 0,
                goal_id INTEGER NOT NULL DEFAULT 0,
                handshake_status TEXT NOT NULL DEFAULT 'none',
                psychological_score INTEGER NOT NULL DEFAULT 0,
                threshold INTEGER NOT NULL DEFAULT 0,
                verbal_only INTEGER NOT NULL DEFAULT 0,
                due_turn INTEGER NOT NULL DEFAULT 0,
                last_checked_turn INTEGER NOT NULL DEFAULT 0,
                resolved_turn INTEGER NOT NULL DEFAULT 0,
                fulfillment_score INTEGER NOT NULL DEFAULT 0,
                fulfillment_evidence TEXT NOT NULL DEFAULT '',
                target_evidence TEXT NOT NULL DEFAULT '',
                political_effect_json TEXT NOT NULL DEFAULT '{}',
                auto_review_json TEXT NOT NULL DEFAULT '{}',
                llm_review_json TEXT NOT NULL DEFAULT '{}',
                conditions TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_negotiation_agreements_minister
                ON negotiation_agreements(minister_name, action_kind, status, id);
            CREATE INDEX IF NOT EXISTS idx_negotiation_agreements_minister_id
                ON negotiation_agreements(minister_name, id DESC);
            CREATE INDEX IF NOT EXISTS idx_negotiation_agreements_status_id
                ON negotiation_agreements(status, id);

            CREATE TABLE IF NOT EXISTS negotiation_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agreement_id INTEGER NOT NULL,
                description TEXT NOT NULL,
                task_kind TEXT NOT NULL DEFAULT 'general',
                status TEXT NOT NULL DEFAULT 'pending',
                evidence TEXT NOT NULL DEFAULT '',
                last_checked_turn INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(agreement_id) REFERENCES negotiation_agreements(id)
            );
            CREATE INDEX IF NOT EXISTS idx_negotiation_tasks_agreement
                ON negotiation_tasks(agreement_id, status, id);

            CREATE TABLE IF NOT EXISTS secret_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                turn_issued INTEGER NOT NULL,
                due_turn INTEGER NOT NULL DEFAULT 0,
                year_issued INTEGER NOT NULL,
                period_issued INTEGER NOT NULL,
                minister_name TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                tags TEXT NOT NULL DEFAULT '[]',
                importance INTEGER NOT NULL DEFAULT 4,
                status TEXT NOT NULL DEFAULT 'active',
                result TEXT NOT NULL DEFAULT '',
                sim_note TEXT NOT NULL DEFAULT '',
                turn_closed INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_secret_orders_minister
                ON secret_orders(minister_name, status);
            CREATE INDEX IF NOT EXISTS idx_secret_orders_turn
                ON secret_orders(turn_issued, status);
            CREATE INDEX IF NOT EXISTS idx_secret_orders_status
                ON secret_orders(status);
            CREATE INDEX IF NOT EXISTS idx_secret_orders_status_due
                ON secret_orders(status, due_turn, id);

            CREATE TABLE IF NOT EXISTS skill_grants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                character_name TEXT NOT NULL,
                skill_id TEXT NOT NULL,
                granted_by TEXT NOT NULL,
                source_turn INTEGER NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(character_name) REFERENCES characters(name)
            );

            CREATE TABLE IF NOT EXISTS turn_directives (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                turn INTEGER NOT NULL,
                year INTEGER NOT NULL,
                period INTEGER NOT NULL,
                event_id TEXT,
                actor TEXT,
                skill_id TEXT,
                text TEXT NOT NULL,
                source TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft',
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(event_id) REFERENCES events(id),
                FOREIGN KEY(actor) REFERENCES characters(name)
            );

            CREATE INDEX IF NOT EXISTS idx_economy_ledger_turn
            ON economy_ledger(turn, account);

            CREATE TABLE IF NOT EXISTS fiscal_config (
                key   TEXT PRIMARY KEY,
                value INTEGER NOT NULL,
                kind  TEXT NOT NULL,
                note  TEXT NOT NULL DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_turn_directives_turn
            ON turn_directives(turn, status);

            CREATE INDEX IF NOT EXISTS idx_region_logs_turn
            ON region_logs(turn, region_id);

            CREATE INDEX IF NOT EXISTS idx_army_logs_turn
            ON army_logs(turn, army_id);

            CREATE INDEX IF NOT EXISTS idx_building_logs_turn
            ON building_logs(turn, building_id);

            CREATE INDEX IF NOT EXISTS idx_power_logs_turn
            ON power_logs(turn, power_id);

            CREATE TABLE IF NOT EXISTS issues (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                origin_kind TEXT NOT NULL DEFAULT '',
                origin_ref TEXT NOT NULL DEFAULT '',
                origin_turn INTEGER NOT NULL,
                bar_value INTEGER NOT NULL DEFAULT 40,
                bar_good_meaning TEXT NOT NULL DEFAULT '已平',
                bar_bad_meaning TEXT NOT NULL DEFAULT '失控',
                inertia INTEGER NOT NULL DEFAULT 0,
                phase TEXT NOT NULL DEFAULT '起',
                stage_text TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                severity INTEGER NOT NULL DEFAULT 50,
                region_hint TEXT NOT NULL DEFAULT '',
                faction_hint TEXT NOT NULL DEFAULT '',
                tags TEXT NOT NULL DEFAULT '[]',
                ongoing_effects TEXT NOT NULL DEFAULT '{}',
                cancellable TEXT NOT NULL DEFAULT 'never',
                cancel_cost TEXT NOT NULL DEFAULT '{}',
                effect_on_resolve TEXT NOT NULL DEFAULT '{}',
                effect_on_fail TEXT NOT NULL DEFAULT '{}',
                resolve_condition TEXT NOT NULL DEFAULT '',
                fail_condition TEXT NOT NULL DEFAULT '',
                resolution_summary TEXT NOT NULL DEFAULT '',
                last_advance_turn INTEGER NOT NULL DEFAULT 0,
                closed_turn INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS issue_advances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                issue_id INTEGER NOT NULL,
                turn INTEGER NOT NULL,
                trigger_kind TEXT NOT NULL,
                trigger_ref TEXT NOT NULL DEFAULT '',
                delta_bar INTEGER NOT NULL DEFAULT 0,
                from_value INTEGER NOT NULL DEFAULT 0,
                to_value INTEGER NOT NULL DEFAULT 0,
                from_stage_text TEXT NOT NULL DEFAULT '',
                to_stage_text TEXT NOT NULL DEFAULT '',
                narrative TEXT NOT NULL DEFAULT '',
                metric_delta TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(issue_id) REFERENCES issues(id)
            );

            CREATE TABLE IF NOT EXISTS legacies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                source_issue_id INTEGER,                    -- 产生它的 issue（可空）
                modifiers TEXT NOT NULL DEFAULT '{}',  -- 各维度带符号百分比修正符 {"国库":10,"regions":{...},"armies":{...}}
                narrative_hint TEXT NOT NULL DEFAULT '',    -- 一句话说明（仅展示用，不喂 simulator）
                start_month INTEGER NOT NULL,               -- 绝对月 = year*12+period
                duration_months INTEGER NOT NULL DEFAULT 24,-- 时长；-1=永久
                status TEXT NOT NULL DEFAULT 'active',      -- active / expired / cleared
                clear_gate TEXT NOT NULL DEFAULT '{}',      -- 机器消除条件（同 _gate_passed 语法）；非空=靠程序判定消除而非时长
                legacy_key TEXT NOT NULL DEFAULT '',        -- 开局负面修正对应 opening_legacies.key，去重用
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_legacies_active
            ON legacies(status);

            CREATE INDEX IF NOT EXISTS idx_issues_active
            ON issues(kind, status, severity DESC);

            CREATE INDEX IF NOT EXISTS idx_issue_advances_issue
            ON issue_advances(issue_id, turn);

            CREATE TABLE IF NOT EXISTS classes (
                name TEXT NOT NULL,
                region_id TEXT NOT NULL DEFAULT '',
                population INTEGER NOT NULL,
                satisfaction INTEGER NOT NULL,
                leverage INTEGER NOT NULL,
                agenda TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (name, region_id)
            );

            CREATE INDEX IF NOT EXISTS idx_classes_region
            ON classes(region_id, name);

            CREATE TABLE IF NOT EXISTS event_memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject_type TEXT NOT NULL,
                subject_id TEXT NOT NULL,
                turn INTEGER NOT NULL,
                year INTEGER NOT NULL,
                period INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                title TEXT NOT NULL,
                cause TEXT NOT NULL DEFAULT '',
                process TEXT NOT NULL DEFAULT '',
                outcome TEXT NOT NULL DEFAULT '',
                sentiment TEXT NOT NULL DEFAULT 'neutral',
                importance INTEGER NOT NULL DEFAULT 3,
                tags TEXT NOT NULL DEFAULT '[]',
                source_kind TEXT NOT NULL,
                source_id TEXT NOT NULL,
                expires_turn INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(subject_type, subject_id, event_type, source_kind, source_id)
            );

            CREATE INDEX IF NOT EXISTS idx_event_memories_subject
            ON event_memories(subject_type, subject_id, turn);

            CREATE INDEX IF NOT EXISTS idx_event_memories_turn
            ON event_memories(turn, importance);
            CREATE INDEX IF NOT EXISTS idx_event_memories_turn_importance
            ON event_memories(turn, importance, id);
            CREATE INDEX IF NOT EXISTS idx_event_memories_event_turn
            ON event_memories(event_type, turn);

            CREATE INDEX IF NOT EXISTS idx_event_memories_expiry
            ON event_memories(expires_turn, turn);


            CREATE TABLE IF NOT EXISTS event_memory_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id INTEGER NOT NULL,
                source_kind TEXT NOT NULL,
                source_id TEXT NOT NULL,
                excerpt TEXT NOT NULL DEFAULT '',
                locator TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(memory_id) REFERENCES event_memories(id) ON DELETE CASCADE,
                UNIQUE(memory_id, source_kind, source_id, locator)
            );

            CREATE INDEX IF NOT EXISTS idx_event_memory_sources_memory
            ON event_memory_sources(memory_id);

            CREATE TABLE IF NOT EXISTS kv_store (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        for column, definition in {
            "military_strength": "INTEGER NOT NULL DEFAULT 50",
            "cohesion": "INTEGER NOT NULL DEFAULT 50",
            "supply": "INTEGER NOT NULL DEFAULT 50",
            "last_action": "TEXT NOT NULL DEFAULT ''",
            "kind": "TEXT NOT NULL DEFAULT '敌国'",
            "aliases": "TEXT NOT NULL DEFAULT ''",
        }.items():
            self.ensure_column("powers", column, definition)
        self.ensure_column("armies", "owner_power", "TEXT NOT NULL DEFAULT 'ming'")
        self.ensure_column("regions", "controlled_by", "TEXT NOT NULL DEFAULT 'ming'")
        self.ensure_column("characters", "power_id", "TEXT NOT NULL DEFAULT 'ming'")
        self.ensure_column("characters", "location", "TEXT NOT NULL DEFAULT ''")
        self.ensure_column("issues", "resolve_condition", "TEXT NOT NULL DEFAULT ''")
        self.ensure_column("issues", "fail_condition", "TEXT NOT NULL DEFAULT ''")
        self.ensure_column("characters", "birth_year", "INTEGER NOT NULL DEFAULT 0")
        self.ensure_column("characters", "historical_death_year", "INTEGER NOT NULL DEFAULT 0")
        self.ensure_column("characters", "historical_death_month", "INTEGER NOT NULL DEFAULT 0")
        self.ensure_column("characters", "debut_year", "INTEGER NOT NULL DEFAULT 0")
        self.ensure_column("characters", "debut_month", "INTEGER NOT NULL DEFAULT 0")
        self.ensure_column("characters", "status", "TEXT NOT NULL DEFAULT 'active'")
        self.ensure_column("characters", "status_reason", "TEXT NOT NULL DEFAULT ''")
        self.ensure_column("characters", "status_changed_turn", "INTEGER NOT NULL DEFAULT 0")
        self.ensure_column("characters", "portrait_id", "TEXT NOT NULL DEFAULT ''")
        self.ensure_column("characters", "court_role", "TEXT NOT NULL DEFAULT ''")
        self.ensure_column("characters", "summary", "TEXT NOT NULL DEFAULT ''")
        self.ensure_column("characters", "aliases", "TEXT NOT NULL DEFAULT '[]'")
        # ── 人物校量（兼容旧档迁移）──
        self.ensure_column("characters", "force", "INTEGER NOT NULL DEFAULT 50")
        self.ensure_column("characters", "wisdom", "INTEGER NOT NULL DEFAULT 50")
        self.ensure_column("characters", "charm", "INTEGER NOT NULL DEFAULT 50")
        self.ensure_column("characters", "luck", "INTEGER NOT NULL DEFAULT 50")
        self.ensure_column("characters", "cultivation", "INTEGER NOT NULL DEFAULT 0")
        self.ensure_column("characters", "hp", "INTEGER NOT NULL DEFAULT 100")
        self.ensure_column("characters", "max_hp", "INTEGER NOT NULL DEFAULT 100")
        self.ensure_column("characters", "exp", "INTEGER NOT NULL DEFAULT 0")
        self.ensure_column("characters", "level", "INTEGER NOT NULL DEFAULT 1")
        # 步骤7：回合阶段（旧库迁移，schema 升级非 fallback）
        self.ensure_column("game_state", "turn_phase", "TEXT NOT NULL DEFAULT 'summoning'")
        # 结局：ended=1 时游戏终结；ending_status 为 context.ENDING_* 类型。
        self.ensure_column("game_state", "ended", "INTEGER NOT NULL DEFAULT 0")
        self.ensure_column("game_state", "ending_status", "TEXT NOT NULL DEFAULT ''")
        # 密令推演副作用列（result 留给承办人进展，sim_note 给推演写泄漏/反弹，互不覆盖）
        self.ensure_column("secret_orders", "sim_note", "TEXT NOT NULL DEFAULT ''")
        # 密令期限：0=无硬期限；到 due_turn 时自动转入待核议，由推演当月判 done/failed。
        self.ensure_column("secret_orders", "due_turn", "INTEGER NOT NULL DEFAULT 0")
        # fiscal_config 科目元数据列（数据驱动预算目录）：budget_role=fixed 的 base 项靠
        # account/direction/display 由 flows.compute_budget_lines 动态生成预算行；
        # dynamic 项（田赋/辽饷/盐税/商税/皇庄）走省级公式/皇庄专路，这三列留空。
        self.ensure_column("fiscal_config", "budget_role", "TEXT NOT NULL DEFAULT 'fixed'")
        self.ensure_column("fiscal_config", "account", "TEXT NOT NULL DEFAULT ''")
        self.ensure_column("fiscal_config", "direction", "TEXT NOT NULL DEFAULT ''")
        self.ensure_column("fiscal_config", "display", "TEXT NOT NULL DEFAULT ''")
        self.ensure_column("fiscal_config", "sort_order", "INTEGER NOT NULL DEFAULT 9999")
        # economy_ledger 支出结构化标签：仅 extractor 抽出的 economy_moves 填这三列；
        # flows 月固定支出与所有收入留 NULL。purpose 受控枚举见 constants.ECONOMY_PURPOSES。
        self.ensure_column("economy_ledger", "purpose", "TEXT")
        self.ensure_column("economy_ledger", "target_kind", "TEXT")
        self.ensure_column("economy_ledger", "target_id", "TEXT")
        # 政治黑板：召对证据与月末成因札记。旧档为空，前端按缺省隐藏。
        self.ensure_column("minister_stances", "evidence_json", "TEXT NOT NULL DEFAULT '{}'")
        self.ensure_column("minister_stances", "risk_tags", "TEXT NOT NULL DEFAULT ''")
        self.ensure_column("minister_stances", "execution_hint", "TEXT NOT NULL DEFAULT ''")
        self.ensure_column("minister_stances", "handshake_status", "TEXT NOT NULL DEFAULT 'none'")
        self.ensure_column("minister_stances", "psychological_score", "INTEGER NOT NULL DEFAULT 0")
        self.ensure_column("minister_stances", "psychological_json", "TEXT NOT NULL DEFAULT '{}'")
        self.ensure_column("minister_stances", "agreement_id", "INTEGER NOT NULL DEFAULT 0")
        self.ensure_column("minister_stances", "goal_id", "INTEGER NOT NULL DEFAULT 0")
        self.ensure_column("negotiation_agreements", "core_topic", "TEXT NOT NULL DEFAULT ''")
        self.ensure_column("negotiation_agreements", "target_text", "TEXT NOT NULL DEFAULT ''")
        self.ensure_column("negotiation_agreements", "promise_type", "TEXT NOT NULL DEFAULT ''")
        self.ensure_column("negotiation_agreements", "stakes", "TEXT NOT NULL DEFAULT ''")
        self.ensure_column("negotiation_agreements", "condition_status", "TEXT NOT NULL DEFAULT 'pending'")
        self.ensure_column("negotiation_agreements", "target_status", "TEXT NOT NULL DEFAULT 'pending_conditions'")
        self.ensure_column("negotiation_agreements", "due_turn", "INTEGER NOT NULL DEFAULT 0")
        self.ensure_column("negotiation_agreements", "last_checked_turn", "INTEGER NOT NULL DEFAULT 0")
        self.ensure_column("negotiation_agreements", "resolved_turn", "INTEGER NOT NULL DEFAULT 0")
        self.ensure_column("negotiation_agreements", "fulfillment_score", "INTEGER NOT NULL DEFAULT 0")
        self.ensure_column("negotiation_agreements", "fulfillment_evidence", "TEXT NOT NULL DEFAULT ''")
        self.ensure_column("negotiation_agreements", "target_evidence", "TEXT NOT NULL DEFAULT ''")
        self.ensure_column("negotiation_agreements", "political_effect_json", "TEXT NOT NULL DEFAULT '{}'")
        self.ensure_column("negotiation_agreements", "auto_review_json", "TEXT NOT NULL DEFAULT '{}'")
        self.ensure_column("negotiation_agreements", "llm_review_json", "TEXT NOT NULL DEFAULT '{}'")
        self.ensure_column("negotiation_agreements", "goal_id", "INTEGER NOT NULL DEFAULT 0")
        self.ensure_column("negotiation_tasks", "task_kind", "TEXT NOT NULL DEFAULT 'general'")
        self.ensure_column("negotiation_tasks", "last_checked_turn", "INTEGER NOT NULL DEFAULT 0")
        self.ensure_column("xinpan_states", "flags_json", "TEXT NOT NULL DEFAULT '{}'")
        self.ensure_column("xinpan_states", "updated_turn", "INTEGER NOT NULL DEFAULT 0")
        self.ensure_column("xinpan_logs", "trust_delta", "REAL NOT NULL DEFAULT 0")
        self.ensure_column("turn_extractions", "causal_notes", "TEXT NOT NULL DEFAULT '[]'")
        # 开局负面帝国修正：clear_gate(机器消除条件)、legacy_key(对应 opening_legacies.key，开局修正去重用)
        self.ensure_column("legacies", "clear_gate", "TEXT NOT NULL DEFAULT '{}'")
        self.ensure_column("legacies", "legacy_key", "TEXT NOT NULL DEFAULT ''")
        # 章节记忆正文：event_type='chapter_summary' 用，存整段叙事章节（不受 outcome 80 字限）。
        self.ensure_column("event_memories", "body", "TEXT NOT NULL DEFAULT ''")
        # 后宫调教记录
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS consort_traits (
                name TEXT PRIMARY KEY,
                extra_skills TEXT NOT NULL DEFAULT '',
                extra_traits TEXT NOT NULL DEFAULT '',
                updated_turn INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # 结局总结：每局结局触发时落一条（单 campaign 一库，turn 为主键，对齐 turn_reports）。
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS ending_summary (
                turn INTEGER PRIMARY KEY,
                year INTEGER NOT NULL,
                period INTEGER NOT NULL,
                ending_status TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                timeline TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # ── 天命异闻新增表 ──
        # 玩家/皇帝物品栏
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS player_inventory (
                item_id TEXT PRIMARY KEY,
                quantity INTEGER NOT NULL DEFAULT 1,
                equipped INTEGER NOT NULL DEFAULT 0,
                acquired_turn INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # 玩家/皇帝自身校量。不要把崇祯塞进 NPC 名册，否则会污染召见名单。
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS player_profile (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                name TEXT NOT NULL DEFAULT '崇祯',
                force INTEGER NOT NULL DEFAULT 45,
                wisdom INTEGER NOT NULL DEFAULT 76,
                charm INTEGER NOT NULL DEFAULT 62,
                luck INTEGER NOT NULL DEFAULT 55,
                cultivation INTEGER NOT NULL DEFAULT 0,
                hp INTEGER NOT NULL DEFAULT 105,
                max_hp INTEGER NOT NULL DEFAULT 105,
                exp INTEGER NOT NULL DEFAULT 0,
                level INTEGER NOT NULL DEFAULT 3,
                updated_turn INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.conn.execute(
            """
            INSERT OR IGNORE INTO player_profile
                (id, name, force, wisdom, charm, luck, cultivation, hp, max_hp, exp, level)
            VALUES (1, '崇祯', 45, 76, 62, 55, 0, 105, 105, 0, 3)
            """
        )
        # 人物装备栏
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS character_equipment (
                character_name TEXT PRIMARY KEY,
                weapon_id TEXT NOT NULL DEFAULT '',
                armor_id TEXT NOT NULL DEFAULT '',
                accessory_id TEXT NOT NULL DEFAULT '',
                updated_turn INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # 奇遇记录
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS adventure_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                turn INTEGER NOT NULL,
                year INTEGER NOT NULL,
                period INTEGER NOT NULL,
                adventure_id TEXT NOT NULL,
                title TEXT NOT NULL,
                chosen_index INTEGER NOT NULL,
                choice_text TEXT NOT NULL DEFAULT '',
                success INTEGER NOT NULL DEFAULT 0,
                narrative TEXT NOT NULL DEFAULT '',
                effects TEXT NOT NULL DEFAULT '{}',
                item_reward TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.conn.commit()
        self.init_fiscal_config()

    def get_player_profile(self) -> Dict[str, Any]:
        row = self.conn.execute("SELECT * FROM player_profile WHERE id = 1").fetchone()
        if row is None:
            self.conn.execute(
                """
                INSERT INTO player_profile
                    (id, name, force, wisdom, charm, luck, cultivation, hp, max_hp, exp, level)
                VALUES (1, '崇祯', 45, 76, 62, 55, 0, 105, 105, 0, 3)
                """
            )
            self.conn.commit()
            row = self.conn.execute("SELECT * FROM player_profile WHERE id = 1").fetchone()
        return self._row_dict(row)

    def apply_player_profile_delta(self, state: GameState, field: str, delta: int) -> None:
        limits = {
            "force": (0, 100),
            "wisdom": (0, 100),
            "charm": (0, 100),
            "luck": (0, 100),
            "cultivation": (0, 100),
            "hp": (0, 200),
            "max_hp": (1, 200),
            "exp": (0, 999999),
            "level": (1, 99),
        }
        if field not in limits:
            return
        profile = self.get_player_profile()
        low, high = limits[field]
        new_value = max(low, min(high, int(profile.get(field, 0)) + int(delta)))
        self.conn.execute(
            f"UPDATE player_profile SET {field} = ?, updated_turn = ?, updated_at = CURRENT_TIMESTAMP WHERE id = 1",
            (new_value, int(state.turn)),
        )

    def grant_player_item(self, item_id: str, state: GameState, quantity: int = 1) -> None:
        clean_id = str(item_id or "").strip()
        if not clean_id:
            return
        qty = max(1, int(quantity or 1))
        self.conn.execute(
            """
            INSERT INTO player_inventory (item_id, quantity, equipped, acquired_turn)
            VALUES (?, ?, 0, ?)
            ON CONFLICT(item_id) DO UPDATE SET
                quantity = player_inventory.quantity + excluded.quantity,
                acquired_turn = CASE
                    WHEN player_inventory.acquired_turn = 0 THEN excluded.acquired_turn
                    ELSE player_inventory.acquired_turn
                END
            """,
            (clean_id, qty, int(state.turn)),
        )

    def list_adventure_logs(self, limit: int = 10) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT turn, year, period, adventure_id, title, choice_text,
                   success, narrative, effects, item_reward
            FROM adventure_log
            ORDER BY turn DESC, id DESC
            LIMIT ?
            """,
            (max(1, int(limit or 10)),),
        ).fetchall()
        output: List[Dict[str, Any]] = []
        for row in rows:
            try:
                effects = json.loads(row["effects"] or "{}")
            except (TypeError, ValueError):
                effects = {}
            output.append({
                "turn": int(row["turn"]),
                "year": int(row["year"]),
                "period": int(row["period"]),
                "adventure_id": str(row["adventure_id"]),
                "title": str(row["title"]),
                "choice": str(row["choice_text"]),
                "success": bool(row["success"]),
                "narrative": str(row["narrative"]),
                "items_found": [str(row["item_reward"])] if row["item_reward"] else [],
                "metrics_change": effects if isinstance(effects, dict) else {},
            })
        return output

    def list_player_inventory(self) -> List[Dict[str, Any]]:
        catalog = {
            str(item.get("id")): item
            for item in getattr(self.content, "items", [])
            if isinstance(item, dict) and item.get("id")
        }
        rows = self.conn.execute(
            """
            SELECT item_id, quantity, equipped
            FROM player_inventory
            ORDER BY equipped DESC, item_id ASC
            """
        ).fetchall()
        output: List[Dict[str, Any]] = []
        for row in rows:
            item_id = str(row["item_id"])
            meta = catalog.get(item_id, {})
            output.append({
                "id": item_id,
                "name": str(meta.get("name") or item_id),
                "category": str(meta.get("category") or "未知"),
                "rarity": str(meta.get("rarity") or "普通"),
                "quantity": int(row["quantity"]),
                "equipped": bool(row["equipped"]),
            })
        return output

    def init_fiscal_config(self) -> None:
        """从 content/fiscal_config.json（self.content.fiscal_items）seed 财政科目目录。

        base/rate 单位为【月度】万两/%。科目目录与元数据全走 JSON 设定（铁律：设定走 JSON）；
        加新税源只改 JSON 加两行（base+rate）并升 schema_version，零 Python。

        schema 版本来自 JSON 的 schema_version。旧 DB 用 INSERT OR IGNORE 保留玩家中途的
        set_fiscal_config 改动；JSON 升 schema_version 即整体重置玩家未改动的默认值（走
        ON CONFLICT UPDATE 全量覆盖 value/元数据列）。
        """
        items = list(self.content.fiscal_items)
        if not items or "__schema_version" not in items[0]:
            raise SystemExit("init_fiscal_config: fiscal_items 缺 __schema_version 头，中止。")
        schema_version = int(items[0]["__schema_version"])
        rows = items[1:]

        def _meta(rec: Dict[str, object]) -> tuple:
            return (
                str(rec["key"]), int(rec["value"]), str(rec["kind"]), str(rec["note"]),
                str(rec.get("budget_role", "fixed")),
                str(rec.get("account", "")), str(rec.get("direction", "")),
                str(rec.get("display", "")), int(rec.get("order", 9999)),
            )

        cols = "(key, value, kind, note, budget_role, account, direction, display, sort_order)"
        cur_ver_row = self.conn.execute(
            "SELECT value FROM fiscal_config WHERE key = '__schema_version'"
        ).fetchone()
        cur_ver = int(cur_ver_row["value"]) if cur_ver_row else 0
        if cur_ver < schema_version:
            for rec in rows:
                self.conn.execute(
                    f"INSERT INTO fiscal_config {cols} VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, kind=excluded.kind, "
                    "note=excluded.note, budget_role=excluded.budget_role, account=excluded.account, "
                    "direction=excluded.direction, display=excluded.display, sort_order=excluded.sort_order",
                    _meta(rec),
                )
            self.conn.execute(
                "INSERT INTO fiscal_config (key, value, kind, note) VALUES "
                "('__schema_version', ?, 'meta', '财政默认值大版本号，升即重置玩家未改动的默认值') "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (schema_version,),
            )
        else:
            self.conn.executemany(
                f"INSERT OR IGNORE INTO fiscal_config {cols} VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [_meta(rec) for rec in rows],
            )
        self.conn.commit()

    def iter_budget_items(self) -> "List[Dict[str, object]]":
        """返回 budget_role=fixed 的 base 科目（含 account/direction/display/sort_order）。

        flows.compute_budget_lines 据此动态生成固定收支预算行——加新税源不必改代码。
        每项配套的 *_rate 由调用方按 stem 自取（rate 项 budget_role 同 fixed 但 kind=rate，
        不在本列表里）。dynamic 项（田赋/辽饷/盐税/商税/皇庄）走省级公式，这里不返回。
        """
        rows = self.conn.execute(
            "SELECT key, account, direction, display, note, sort_order FROM fiscal_config "
            "WHERE budget_role = 'fixed' AND kind = 'base' AND key LIKE '%\\_base' ESCAPE '\\' "
            "ORDER BY sort_order, key"
        ).fetchall()
        return [
            {
                "key": str(r["key"]),
                "account": str(r["account"]),
                "direction": str(r["direction"]),
                "display": str(r["display"]),
                "note": str(r["note"] or ""),
            }
            for r in rows
        ]

    def get_fiscal_config(self) -> Dict[str, int]:
        rows = self.conn.execute(
            "SELECT key, value FROM fiscal_config WHERE key NOT LIKE '\\_\\_%' ESCAPE '\\'"
        ).fetchall()
        return {str(r["key"]): int(r["value"]) for r in rows}

    def set_fiscal_config(self, key: str, value: int) -> None:
        self.conn.execute(
            "UPDATE fiscal_config SET value = ? WHERE key = ?", (value, key)
        )
        self.conn.commit()

    def create_fiscal_item(
        self,
        key: str,
        account: str,
        direction: str,
        display: str,
        init_value: int,
        note: str = "",
    ) -> Optional[str]:
        """LLM 推演中凭空新立一个月固定收支项（budget_role=fixed）。

        落 base+rate 两行：`<stem>_base`=init_value、`<stem>_rate`=100。
        既存 base key 直接返回 None（不覆盖，由 fiscal_changes 调增量）。
        返回新建的 base key；冲突或非法返回 None。元数据走 fixed 预算目录，
        flows.iter_budget_items 下{月}起自动遍历落账——零代码加新税种／新月俸。
        """
        stem = key[:-5] if key.endswith("_base") else key
        if not stem:
            return None
        base_key = f"{stem}_base"
        rate_key = f"{stem}_rate"
        exists = self.conn.execute(
            "SELECT 1 FROM fiscal_config WHERE key = ?", (base_key,)
        ).fetchone()
        if exists is not None:
            return None
        sort_order = self.conn.execute(
            "SELECT COALESCE(MAX(sort_order), 0) + 10 FROM fiscal_config"
        ).fetchone()[0]
        self.conn.execute(
            "INSERT INTO fiscal_config "
            "(key, value, kind, budget_role, account, direction, display, sort_order, note) "
            "VALUES (?, ?, 'base', 'fixed', ?, ?, ?, ?, ?)",
            (base_key, max(0, init_value), account, direction, display, sort_order, note),
        )
        self.conn.execute(
            "INSERT INTO fiscal_config "
            "(key, value, kind, budget_role, account, direction, display, sort_order, note) "
            "VALUES (?, 100, 'rate', 'fixed', ?, ?, ?, ?, ?)",
            (rate_key, account, direction, display, sort_order, f"{display}实收率%"),
        )
        self.conn.commit()
        return base_key

    # dynamic 税科目 → regions.fiscal 子字段映射。dynamic 税实收走 calc_province_fiscal
    # 读 region.fiscal（不读 fiscal_config 的 base），故对这些 key 做裁撤/调额必须同步改
    # 各省 fiscal 字段才真生效——否则只动目录不动钱（账目与叙事脱节）。
    #   田赋无独立字段（=tax_per_turn 减其余三税的残差），裁撤走 tax_per_turn 压低；
    #   皇庄收入真读 fiscal_config.皇庄_base，裁撤/调额改 config 即生效，不在本表。
    _DYNAMIC_REGION_FIELD = {
        "辽饷": "liao_xiang", "盐税": "salt_tax", "商税": "commerce_tax",
    }

    def _stem_of(self, key: str) -> str:
        if key.endswith("_base") or key.endswith("_rate"):
            return key[:-5]
        return key

    def apply_dynamic_fiscal_scale(self, stem: str, ratio: float) -> int:
        """按 ratio 缩放所有省 regions.fiscal 中该 dynamic 税字段（辽饷/盐税/商税）。

        ratio=0 即彻底罢废（字段归零）；0<ratio<1 即按比例削减。田赋走 _scale_tian_fu。
        返回被改动的省数。皇庄不在此（走 fiscal_config）。命中映射外的 stem 返回 0。
        """
        field = self._DYNAMIC_REGION_FIELD.get(stem)
        if field is None:
            return 0
        touched = 0
        for row in self.conn.execute("SELECT id, fiscal FROM regions").fetchall():
            fiscal: dict = json.loads(str(row["fiscal"] or "{}"))
            old = int(fiscal.get(field, 0) or 0)
            if old <= 0:
                continue
            new = max(0, round(old * ratio))
            if new == old:
                continue
            fiscal[field] = new
            self.conn.execute(
                "UPDATE regions SET fiscal = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (json.dumps(fiscal, ensure_ascii=False), str(row["id"])),
            )
            touched += 1
        if touched:
            self.conn.commit()
        return touched

    def scale_tian_fu(self, ratio: float) -> int:
        """田赋无独立字段（=tax_per_turn 减辽饷/盐税/商税的残差）。按 ratio 缩放田赋部分：
        新 tax_per_turn = 三税之和 + 田赋残差×ratio。ratio=0 即罢田赋（仅留三税基）。
        返回被改动的省数。"""
        touched = 0
        for row in self.conn.execute(
            "SELECT id, tax_per_turn, fiscal FROM regions"
        ).fetchall():
            fiscal: dict = json.loads(str(row["fiscal"] or "{}"))
            others = (int(fiscal.get("liao_xiang", 0) or 0)
                      + int(fiscal.get("salt_tax", 0) or 0)
                      + int(fiscal.get("commerce_tax", 0) or 0))
            tax = int(row["tax_per_turn"])
            tian_fu = max(0, tax - others)
            new_tax = others + max(0, round(tian_fu * ratio))
            if new_tax == tax:
                continue
            self.conn.execute(
                "UPDATE regions SET tax_per_turn = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (new_tax, str(row["id"])),
            )
            touched += 1
        if touched:
            self.conn.commit()
        return touched

    def remove_fiscal_item(self, key: str) -> Optional[str]:
        """彻底裁撤一个月固定收支项（罢税/裁俸）：删 base+rate 两行。

        完全放开——含 dynamic（田赋/辽饷/盐税/商税/皇庄），后果玩家自负。
        - fixed 项：删目录条目即停止逐月落账。
        - dynamic 税（辽饷/盐税/商税）：实收走 region.fiscal，故同步把各省该字段归零；
          田赋走 tax_per_turn 压到仅留三税基；皇庄收入读 fiscal_config，删 config 即停。
          这样「永久罢辽饷」当真停收，不再只动目录不动钱。
        删不存在的项返回 None。返回被删的 base key（按 stem 归一）。
        """
        stem = self._stem_of(key)
        if not stem:
            return None
        base_key = f"{stem}_base"
        rate_key = f"{stem}_rate"
        # 存在性查 base 或 rate 任一——田赋只有 田赋_rate（无 base），但仍是可裁撤的 dynamic 项。
        exists = self.conn.execute(
            "SELECT 1 FROM fiscal_config WHERE key IN (?, ?)", (base_key, rate_key)
        ).fetchone()
        if exists is None:
            return None
        self.conn.execute(
            "DELETE FROM fiscal_config WHERE key IN (?, ?)", (base_key, rate_key)
        )
        # dynamic 税：同步罢废各省实收字段（皇庄走 config 不在此）。
        if stem in self._DYNAMIC_REGION_FIELD:
            self.apply_dynamic_fiscal_scale(stem, 0.0)
        elif stem == "田赋":
            self.scale_tian_fu(0.0)
        self.conn.commit()
        return base_key

    def ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {row["name"] for row in self.conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def table_has_rows(self, table: str) -> bool:
        row = self.conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
        return row is not None

    def seed_static_data(self) -> None:
        if not self.table_has_rows("offices"):
            for office_type, definition in self.content.office_definitions.items():
                self.conn.execute(
                    """
                    INSERT INTO offices
                    (office_type, skills, tools, authority_scope, power, responsibility, corruption_risk)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        office_type,
                        json.dumps(definition["skills"], ensure_ascii=False),
                        json.dumps(definition["tools"], ensure_ascii=False),
                        str(definition["authority_scope"]),
                        int(definition["power"]),
                        int(definition["responsibility"]),
                        int(definition["corruption_risk"]),
                    ),
                )

        if not self.table_has_rows("characters"):
            for character in self.content.characters.values():
                office = normalize_office(character.office)
                office_type = infer_office_type_from_office(office, character.office_type)
                self.conn.execute(
                    """
                    INSERT INTO characters
                    (name, office, office_type, faction, aliases, personal_skills, loyalty, ability, integrity, courage, style,
                     birth_year, historical_death_year, historical_death_month, debut_year, debut_month,
                     status, status_reason, status_changed_turn, portrait_id, power_id, location, summary,
                     force, wisdom, charm, luck, cultivation, hp, max_hp, exp, level)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        character.name,
                        office,
                        office_type,
                        character.faction,
                        json.dumps(character.aliases, ensure_ascii=False),
                        json.dumps(character.personal_skills, ensure_ascii=False),
                        character.loyalty,
                        character.ability,
                        character.integrity,
                        character.courage,
                        character.style,
                        character.birth_year,
                        character.historical_death_year,
                        character.historical_death_month,
                        character.debut_year,
                        character.debut_month,
                        character.status,
                        "",
                        0,
                        character.portrait_id,
                        character.power_id,
                        character.location,
                        character.summary,
                        character.force,
                        character.wisdom,
                        character.charm,
                        character.luck,
                        character.cultivation,
                        character.hp,
                        character.max_hp,
                        character.exp,
                        character.level,
                    ),
                )
        else:
            for character in self.content.characters.values():
                self.conn.execute(
                    """
                    UPDATE characters
                    SET summary = ?
                    WHERE name = ? AND (summary = '' OR summary IS NULL)
                    """,
                    (character.summary, character.name),
                )
                if character.birth_year:
                    self.conn.execute(
                        """
                        UPDATE characters
                        SET birth_year = ?
                        WHERE name = ? AND (birth_year = 0 OR birth_year IS NULL)
                        """,
                        (int(character.birth_year), character.name),
                    )
        if not self.table_has_rows("character_offices"):
            for row in self.conn.execute("SELECT name, office, office_type FROM characters").fetchall():
                self.conn.execute(
                    """
                    INSERT INTO character_offices (character_name, office_title, office_type, source)
                    VALUES (?, ?, ?, ?)
                    """,
                    (row["name"], row["office"], row["office_type"], "存档迁移"),
                )
        self._reconcile_character_office_types()

        if not self.table_has_rows("factions"):
            for faction in self.content.factions.values():
                self.conn.execute(
                    """
                    INSERT INTO factions (name, satisfaction, leverage, agenda)
                    VALUES (?, ?, ?, ?)
                    """,
                    (faction.name, faction.satisfaction, faction.leverage, faction.agenda),
                )
        if not self.table_has_rows("classes"):
            for cls in self.content.classes.values():
                self.conn.execute(
                    """
                    INSERT INTO classes (name, region_id, population, satisfaction, leverage, agenda)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (cls.name, cls.region_id, cls.population, cls.satisfaction, cls.leverage, cls.agenda),
                )
        for power in self.content.powers.values():
            self.conn.execute(
                """
                INSERT OR IGNORE INTO powers
                (id, name, kind, leader, stance, leverage, satisfaction, military_strength,
                 cohesion, supply, agenda, status, last_action, aliases)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    power.id,
                    power.name,
                    power.kind,
                    power.leader,
                    power.stance,
                    power.leverage,
                    power.satisfaction,
                    power.military_strength,
                    power.cohesion,
                    power.supply,
                    power.agenda,
                    power.status,
                    power.last_action,
                    power.aliases,
                ),
            )
        for region in self.content.regions.values():
            self.conn.execute(
                """
                INSERT OR IGNORE INTO regions
                (id, name, kind, population, public_support, unrest, natural_disaster, human_disaster,
                 registered_land, hidden_land, tax_per_turn, grain_security, gentry_resistance,
                 military_pressure, status, controlled_by, fiscal)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    region.id,
                    region.name,
                    region.kind,
                    region.population,
                    region.public_support,
                    region.unrest,
                    region.natural_disaster,
                    region.human_disaster,
                    region.registered_land,
                    region.hidden_land,
                    region.tax_per_turn,
                    region.grain_security,
                    region.gentry_resistance,
                    region.military_pressure,
                    region.status,
                    region.controlled_by,
                    json.dumps(region.fiscal, ensure_ascii=False),
                ),
            )
        is_fresh_armies_seed = not self.table_has_rows("armies")
        if is_fresh_armies_seed:
            for army in self.content.armies.values():
                self.conn.execute(
                    """
                    INSERT INTO armies
                    (id, name, station, theater, commander, controller, troop_type, manpower,
                     maintenance_per_turn, supply, morale, training, equipment, arrears,
                     mobility, loyalty, status, owner_power)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        army.id,
                        army.name,
                        army.station,
                        army.theater,
                        army.commander,
                        army.controller,
                        army.troop_type,
                        army.manpower,
                        army.maintenance_per_turn,
                        army.supply,
                        army.morale,
                        army.training,
                        army.equipment,
                        army.arrears,
                        army.mobility,
                        army.loyalty,
                        army.status,
                        army.owner_power,
                    ),
                )
        if not self.table_has_rows("buildings"):
            for building in self.content.buildings.values():
                self.conn.execute(
                    """
                    INSERT INTO buildings
                    (id, region_id, name, category, level, condition, maintenance, risk,
                     output_metric, output_amount, status, origin, created_turn)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'preset', 0)
                    """,
                    (
                        building.id,
                        building.region_id,
                        building.name,
                        building.category,
                        building.level,
                        building.condition,
                        building.maintenance,
                        building.risk,
                        building.output_metric,
                        building.output_amount,
                        building.status,
                    ),
                )
        if not self.table_has_rows("events"):
            for event in (*self.content.events, *self.content.seed_events):
                self.conn.execute(
                    """
                    INSERT INTO events
                    (id, title, kind, summary, urgency, severity, credibility, interests, audiences)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.id,
                        event.title,
                        event.kind,
                        event.summary,
                        event.urgency,
                        event.severity,
                        event.credibility,
                        json.dumps(event.interests, ensure_ascii=False),
                        json.dumps(event.audiences, ensure_ascii=False),
                    ),
                )
        self._migrate_arrears_unit_to_silver(is_fresh_armies_seed)
        self.conn.commit()

    def _reconcile_character_office_types(self) -> int:
        """迁移旧档：修正 office 与 office_type 不一致的原创/非常设官位。"""
        rows = self.conn.execute(
            "SELECT name, office, office_type FROM characters"
        ).fetchall()
        changed = 0
        for row in rows:
            name = str(row["name"] or "")
            office = str(row["office"] or "")
            old_type = str(row["office_type"] or "")
            new_type = effective_stored_office_type(office, old_type)
            if not name or new_type == old_type:
                continue
            self.conn.execute(
                "UPDATE characters SET office_type=? WHERE name=?",
                (new_type, name),
            )
            self.conn.execute(
                """
                UPDATE character_offices
                SET office_type=?, updated_at=CURRENT_TIMESTAMP
                WHERE character_name=?
                """,
                (new_type, name),
            )
            if name in self.content.characters:
                self.content.characters[name].office_type = new_type
            changed += 1
        if changed:
            self.conn.commit()
        return changed

    def _migrate_arrears_unit_to_silver(self, is_fresh_armies_seed: bool) -> None:
        """一次性迁移：armies.arrears 从 0-100 抽象分换成累计欠饷万两。
        旧档按 arrears * maintenance_per_turn / 25 估算（粗略：旧分数 ≈ 4 倍欠饷月数）。

        区分新老档：
        - 新档（is_fresh_armies_seed=True）：armies 由本版 seed_armies 刚刚写入，arrears
          已经是万两。直接打 version=1，跳过换算。
        - 老档（is_fresh_armies_seed=False）：armies 表早已存在数据；若 fiscal_config 中
          无 __arrears_unit_version 标记，说明从未跑过本迁移 → 走换算逻辑。
        """
        ARREARS_UNIT_VERSION = 1
        row = self.conn.execute(
            "SELECT value FROM fiscal_config WHERE key = '__arrears_unit_version'"
        ).fetchone()
        cur = int(row["value"]) if row else 0
        if cur >= ARREARS_UNIT_VERSION:
            return
        if not is_fresh_armies_seed:
            # 真老档：换算分数 → 万两
            self.conn.execute(
                "UPDATE armies SET arrears = CAST(arrears * maintenance_per_turn / 25.0 AS INTEGER) "
                "WHERE maintenance_per_turn > 0"
            )
        # 无论新老档，都把 version 打上，下次启动直接跳过
        self.conn.execute(
            "INSERT INTO fiscal_config (key, value, kind, note) VALUES "
            "('__arrears_unit_version', ?, 'meta', 'arrears 单位由 0-100 分迁至累计欠饷万两的版本号') "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, note = excluded.note",
            (ARREARS_UNIT_VERSION,),
        )

    def has_state(self) -> bool:
        row = self.conn.execute("SELECT 1 FROM game_state WHERE id = 1").fetchone()
        return row is not None

    def save_state(self, state: GameState) -> None:
        self.conn.execute(
            """
            INSERT INTO game_state (id, year, period, turn, turn_phase, ended, ending_status)
            VALUES (1, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET year = excluded.year, period = excluded.period,
                turn = excluded.turn, turn_phase = excluded.turn_phase,
                ended = excluded.ended, ending_status = excluded.ending_status
            """,
            (
                state.year, state.period, state.turn, state.turn_phase,
                1 if state.ended else 0, state.ending_status,
            ),
        )
        for key, value in state.metrics.items():
            self.conn.execute(
                """
                INSERT INTO metrics (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )
        self.sync_economy_accounts(state)
        self.conn.commit()

    def load_state(self, start_ym: str = "") -> GameState:
        row = self.conn.execute(
            "SELECT year, period, turn, turn_phase, ended, ending_status FROM game_state WHERE id = 1"
        ).fetchone()
        if row is None:
            state = GameState()
            if start_ym:
                try:
                    y_str, m_str = start_ym.split(".")
                    y, m = int(y_str), int(m_str)
                except (ValueError, AttributeError):
                    raise SystemExit(f"--start-ym 格式非法：{start_ym!r}，应为 YYYY.MM（如 1629.04）。")
                if not (1627 <= y <= 1644 and 1 <= m <= 12):
                    raise SystemExit(f"--start-ym 超范围：{start_ym!r}，年须 1627-1644、月 1-12。")
                state.turn = (y - 1627) * 12 + (m - 10) + 1
                state.year, state.period = y, m
                print(f"[调试] 跳到 {y}年{m}月起手（turn={state.turn}）。")
            self.save_state(state)
            self.ensure_opening_ledger(state)
            self.seed_opening_crises(state)
            self.seed_opening_gazette(state)
            return state
        metrics = {
            metric["key"]: int(metric["value"])
            for metric in self.conn.execute("SELECT key, value FROM metrics").fetchall()
        }
        state = GameState(
            year=int(row["year"]), period=int(row["period"]), turn=int(row["turn"]),
            turn_phase=str(row["turn_phase"] or "summoning"),
            ended=bool(row["ended"]) if "ended" in row.keys() else False,
            ending_status=str(row["ending_status"] or "") if "ending_status" in row.keys() else "",
        )
        if metrics:
            # 只接当前 GameState 默认 dict 里有的 key，避免旧 DB 残留废弃 metric 灌入。
            valid_keys = set(state.metrics.keys())
            state.metrics.update({k: v for k, v in metrics.items() if k in valid_keys})
        account_rows = self.conn.execute("SELECT account, balance FROM economy_accounts").fetchall()
        for account in account_rows:
            account_name = str(account["account"])
            balance = int(account["balance"])
            state.metrics[account_name] = balance
        self.sync_economy_accounts(state)
        self.ensure_opening_ledger(state)
        self.conn.commit()
        return state

    def sync_economy_accounts(self, state: GameState) -> None:
        notes = {
            "国库": "朝廷公开财政，用于军饷、赈济、官俸和工程。",
            "内库": "皇帝可直接调度的钱物，用于救急、密支和政治缓冲。",
        }
        for account in ECONOMY_ACCOUNTS:
            self.conn.execute(
                """
                INSERT INTO economy_accounts (account, metric_key, balance, note)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(account) DO UPDATE SET
                    balance = excluded.balance,
                    note = excluded.note,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (account, account, int(state.metrics[account]), notes[account]),
            )

    def ensure_opening_ledger(self, state: GameState) -> None:
        for account in ECONOMY_ACCOUNTS:
            exists = self.conn.execute(
                "SELECT 1 FROM economy_ledger WHERE account = ? LIMIT 1",
                (account,),
            ).fetchone()
            if exists:
                continue
            balance = int(state.metrics[account])
            self.conn.execute(
                """
                INSERT INTO economy_ledger
                (turn, year, period, account, delta, balance_after, category, reason, actor)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (state.turn, state.year, state.period, account, balance, balance, "期初", "登基初始账册", "内阁"),
            )
        self.conn.commit()

    def seed_opening_gazette(self, state: GameState) -> None:
        """新档塞一份「即位前一月」邸报（turn=state.turn-1），让大臣首回合即可经 read_past_report
        查到开局朝局速览，不必凭空臆议。已存在则不覆盖。文本来自 content/opening_gazette.md。"""
        prev_turn = state.turn - 1
        prev_year, prev_period = state.year, state.period - 1
        if prev_period < 1:
            prev_period = 12
            prev_year -= 1
        exists = self.conn.execute(
            "SELECT 1 FROM turn_reports WHERE turn = ?",
            (prev_turn,),
        ).fetchone()
        if exists is not None:
            return
        from pathlib import Path
        from ming_sim.paths import bundled_path
        gazette_path = Path(bundled_path("content", "opening_gazette.md"))
        if not gazette_path.is_file():
            return
        text = gazette_path.read_text(encoding="utf-8").strip()
        if not text:
            return
        self.conn.execute(
            "INSERT INTO turn_reports (turn, year, period, report) VALUES (?, ?, ?, ?)",
            (prev_turn, prev_year, prev_period, text),
        )
        self.conn.commit()

    def seed_opening_crises(self, state: GameState) -> None:
        """新档首次进入时塞 1627 即位即面对的危机为 active situation issue。
        数据源已并入 seed_events.json：取标了 auto_trigger 且 trigger_gate 为空（开局盘面无条件
        即达标）的 situation 事件，开局直接立项，使玩家召见前就看到三大危机。
        其余带 gate 的 seed 事件靠 auto_trigger_seed_issues 在 gate 达标的回合再硬立。"""
        if not getattr(self, "content", None):
            return
        for ev in self.content.seed_events:
            if not ev.auto_trigger or ev.trigger_gate:
                continue
            if ev.event_type != "situation":
                continue
            if self.find_any_issue_by_origin("event_pool", ev.id) is not None:
                continue
            # 推导默认 bar / inertia / ongoing / effect，与 event_to_issue 同口径；精调字段优先
            bar = ev.bar_value or max(20, min(60, 50 - int(ev.severity / 5)))
            inertia = ev.issue_inertia  # 默认 0=不漂；要月漂在 seed 里显式填
            try:
                self.insert_issue(
                    state,
                    kind="situation",
                    title=ev.title,
                    origin_kind="event_pool",
                    origin_ref=ev.id,
                    bar_value=bar,
                    bar_good_meaning=ev.bar_good_meaning or "已平",
                    bar_bad_meaning=ev.bar_bad_meaning or "失控",
                    inertia=inertia,
                    stage_text=ev.stage_text or ev.summary[:80],
                    severity=int(ev.severity),
                    region_hint=ev.region_hint,
                    faction_hint=",".join(ev.interests[:2]),
                    tags=ev.issue_tags or [ev.kind],
                    ongoing_effects=ev.ongoing_effects,
                    cancellable="never",
                    effect_on_resolve=ev.effect_on_resolve,
                    effect_on_fail=ev.effect_on_fail,
                    resolve_condition=ev.resolve_condition,
                    fail_condition=ev.fail_condition,
                )
            except Exception as exc:
                print(f"[WARN] 开局危机落库失败：{exc}；跳过 {ev.title}")

    def set_character_status(
        self,
        state: GameState,
        name: str,
        status: str,
        reason: str = "",
    ) -> None:
        """改人物状态：active/offstage/candidate/dismissed/imprisoned/exiled/retired/dead。
        大臣走 characters 表；后宫（consorts）走内存对象 + consort_traits 备档。"""
        valid = {"active", "offstage", "candidate", "dismissed", "imprisoned", "exiled", "retired", "dead"}
        if status not in valid:
            raise ValueError(f"character status 非法：{status}")
        # 去职（下狱/革职/流放/致仕/死）即削职：清空 characters.office，
        # 原职仍留在 character_offices 备档可追溯。复职（active/offstage）不动 office。
        ousted = status in {"dismissed", "imprisoned", "exiled", "retired", "dead"}
        if ousted:
            self.conn.execute(
                "UPDATE characters SET status=?, status_reason=?, status_changed_turn=?, office='' WHERE name=?",
                (status, reason[:200], state.turn, name),
            )
        else:
            self.conn.execute(
                "UPDATE characters SET status=?, status_reason=?, status_changed_turn=? WHERE name=?",
                (status, reason[:200], state.turn, name),
            )
        if status != "active":
            label = CHARACTER_STATUS_LABELS.get(status, status)
            detail = f"承办人{name}{label}，密令中止。"
            if reason:
                detail += reason
            self.fail_active_secret_orders_for_minister(name, state, detail)
        self.conn.commit()

    def get_character_status(self, name: str) -> Tuple[str, str]:
        row = self.conn.execute(
            "SELECT status, status_reason FROM characters WHERE name=?", (name,)
        ).fetchone()
        if row is None:
            return ("active", "")
        return (row["status"], row["status_reason"] or "")

    def character_status_map(self) -> Dict[str, str]:
        rows = self.conn.execute("SELECT name, status FROM characters").fetchall()
        return {str(row["name"]): str(row["status"] or "active") for row in rows}

    def character_status_detail_map(self) -> Dict[str, Tuple[str, str, str]]:
        rows = self.conn.execute("SELECT name, status, status_reason, power_id FROM characters").fetchall()
        return {
            str(row["name"]): (
                str(row["status"] or "active"),
                str(row["status_reason"] or ""),
                str(row["power_id"] or "ming"),
            )
            for row in rows
        }

    def apply_character_power_changes(
        self,
        changes: List[Dict[str, object]],
        state: Optional[GameState] = None,
    ) -> List[Dict[str, object]]:
        """据 extractor 输出改人物 power_id（降将/叛臣/归正）。new_power 须为合法 power id。"""
        applied: List[Dict[str, object]] = []
        if not isinstance(changes, list):
            return applied
        valid_powers = {r["id"] for r in self.conn.execute("SELECT id FROM powers").fetchall()}
        for raw in changes:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or raw.get("姓名") or "").strip()
            new_power = str(raw.get("new_power") or raw.get("新势力") or "").strip()
            reason = str(raw.get("reason") or raw.get("原因") or "")[:120]
            if not name or not new_power:
                print(f"[WARN] character_power_changes 缺 name/new_power → 跳过: {raw}")
                continue
            if new_power not in valid_powers:
                print(f"[WARN] character_power_changes new_power '{new_power}' 未在 powers → 跳过 {name}")
                continue
            row = self.conn.execute(
                "SELECT power_id FROM characters WHERE name=?", (name,)
            ).fetchone()
            if row is None:
                print(f"[WARN] character_power_changes 人物 '{name}' 未入库 → 跳过")
                continue
            old_power = row["power_id"] or "ming"
            if old_power == new_power:
                continue
            self.conn.execute(
                "UPDATE characters SET power_id = ? WHERE name = ?",
                (new_power, name),
            )
            if old_power == "ming" and new_power != "ming":
                current_state = state or self.load_state("")
                self.fail_active_secret_orders_for_minister(
                    name,
                    current_state,
                    f"承办人{name}转投{new_power}，不再属大明朝廷，密令中止。" + reason,
                )
            applied.append({"name": name, "old_power": old_power, "new_power": new_power, "reason": reason})
        self.conn.commit()
        return applied

    def set_character_office(
        self,
        name: str,
        office: str,
        office_type: str = "",
        source: str = "诏书调任",
    ) -> None:
        """既有官员调任/升迁：改 characters.office（office_type 给空则不动），
        同步 character_offices 备档。状态不变（仍 active）。"""
        office = normalize_office(office)
        current_type = (
            self.conn.execute(
                "SELECT office_type FROM characters WHERE name=? AND power_id='ming'", (name,)
            ).fetchone() or {"office_type": ""}
        )["office_type"]
        if not current_type:
            raise ValueError(f"{name}不属大明朝廷，不能授予大明官职")
        eff_type = infer_assignment_office_type(office, office_type=office_type, current_type=current_type)
        if office_type or eff_type != current_type:
            self.conn.execute(
                "UPDATE characters SET office=?, office_type=? WHERE name=?",
                (office, eff_type, name),
            )
        else:
            self.conn.execute(
                "UPDATE characters SET office=? WHERE name=?",
                (office, name),
            )
        self.conn.execute(
            """
            INSERT INTO character_offices (character_name, office_title, office_type, source)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(character_name) DO UPDATE SET
                office_title = excluded.office_title,
                office_type = excluded.office_type,
                source = excluded.source,
                updated_at = CURRENT_TIMESTAMP
            """,
            (name, office, eff_type, source),
        )
        self.conn.commit()
        if name in self.content.characters:
            self.content.characters[name].office = office
            self.content.characters[name].office_type = eff_type

    def apply_historical_deaths(self, state: GameState) -> List[Dict[str, str]]:
        """月初 tick：只有仍 active 的人到点自然死。被玩家提前罢/狱/流/杀的不走此分支。
        只打讣闻、改 status=dead，不动派系/metric。是否升级 issue 由 LLM 看本月邸报判断。
        返回 [{name, office, faction}] 喂给 simulator 当月上下文。
        """
        rows = self.conn.execute(
            """SELECT name, office, faction, historical_death_year, historical_death_month
               FROM characters
               WHERE status = 'active' AND historical_death_year > 0"""
        ).fetchall()
        died: List[Dict[str, str]] = []
        for r in rows:
            year = int(r["historical_death_year"])
            month = int(r["historical_death_month"] or 0)
            triggered = state.year > year or (
                state.year == year and (month == 0 or state.period >= month)
            )
            if not triggered:
                continue
            name = r["name"]
            self.set_character_status(state, name, "dead", f"历史卒于 {year}年{month or '?'}月")
            died.append({
                "name": name,
                "office": r["office"] or "重臣",
                "faction": r["faction"] or "",
            })
        return died

    def apply_historical_debuts(self, state: GameState) -> List[Dict[str, str]]:
        """月初 tick：offstage 人物到历史登场年月，自动转 active 并发"起用"讯息。
        debut_year=0 视为开局即在场（不会处于 offstage）。
        返回 [{name, office, faction}] 喂给 simulator 当月上下文，由 LLM 写进邸报。
        """
        rows = self.conn.execute(
            """SELECT name, office, faction, debut_year, debut_month
               FROM characters
               WHERE status = 'offstage' AND debut_year > 0"""
        ).fetchall()
        debuted: List[Dict[str, str]] = []
        for r in rows:
            year = int(r["debut_year"])
            month = int(r["debut_month"] or 0)
            triggered = state.year > year or (
                state.year == year and (month == 0 or state.period >= month)
            )
            if not triggered:
                continue
            name = r["name"]
            self.set_character_status(state, name, "active", f"历史登场 {year}年{month or '?'}月")
            debuted.append({
                "name": name,
                "office": r["office"] or "重臣",
                "faction": r["faction"] or "",
            })
        return debuted

    def apply_historical_power_renames(self, state: GameState) -> List[Dict[str, object]]:
        """月初 tick：历史国号/称谓变化。稳定 id 不变，只改展示名与别名。"""
        changes: List[Dict[str, object]] = []
        if state.year > 1636 or (state.year == 1636 and state.period >= 4):
            changed = self.apply_power_rename(
                state,
                "houjin",
                "大清",
                aliases="后金，清，大清",
                reason="皇太极称帝，改国号大清",
                status="皇太极称帝改国号大清，建元崇德，整合满蒙汉诸部南向争明",
                last_action="皇太极称帝改国号大清",
            )
            if changed:
                changes.append(changed)
        return changes

    # ── 后宫调教 ──────────────────────────────────────────────────────────

    def get_consort_traits(self, name: str) -> dict:
        """返回 {extra_skills: [...], extra_traits: [...]}，不存在时返回空。"""
        row = self.conn.execute(
            "SELECT extra_skills, extra_traits FROM consort_traits WHERE name=?", (name,)
        ).fetchone()
        if not row:
            return {"extra_skills": [], "extra_traits": []}
        skills = [s.strip() for s in row["extra_skills"].split("，") if s.strip()]
        traits = [t.strip() for t in row["extra_traits"].split("，") if t.strip()]
        return {"extra_skills": skills, "extra_traits": traits}

    def cultivate_consort(self, name: str, turn: int, skill: str = "", trait: str = "") -> dict:
        """追加技能或性格词，去重后持久化。返回最新值。"""
        current = self.get_consort_traits(name)
        skills = current["extra_skills"]
        traits = current["extra_traits"]
        if skill and skill not in skills:
            skills.append(skill)
        if trait and trait not in traits:
            traits.append(trait)
        self.conn.execute(
            """INSERT INTO consort_traits(name, extra_skills, extra_traits, updated_turn)
               VALUES(?,?,?,?)
               ON CONFLICT(name) DO UPDATE SET
                 extra_skills=excluded.extra_skills,
                 extra_traits=excluded.extra_traits,
                 updated_turn=excluded.updated_turn,
                 updated_at=CURRENT_TIMESTAMP""",
            (name, "，".join(skills), "，".join(traits), turn),
        )
        self.conn.commit()
        return {"extra_skills": skills, "extra_traits": traits}

    def next_pool_portrait_id(self, prefix: str = "minister_pool_") -> str:
        """分配下一个预设头像 ID（顺序递增，不循环）。
        minister_pool: 60 个槽；consort_pool: 20 个槽。
        实际可用槽位 = web/public/portraits/<prefix><N>.png 真存在的编号集合（中途删图会跳过缺号）。
        全部用完后再回退到递增（前端 onError fallback 占位符）。"""
        rows = self.conn.execute(
            "SELECT portrait_id FROM characters WHERE portrait_id LIKE ?",
            (prefix + "%",),
        ).fetchall()
        used = set()
        for r in rows:
            try:
                used.add(int(r["portrait_id"].replace(prefix, "")))
            except ValueError:
                pass
        # 扫真实存在的图编号（frozen 模式走 _MEIPASS，源码走 <repo>/web/public/portraits）
        from pathlib import Path
        from ming_sim.paths import bundled_path
        portraits_dir = Path(bundled_path("web", "public", "portraits"))
        available: set[int] = set()
        if portraits_dir.is_dir():
            for p in portraits_dir.glob(f"{prefix}*.png"):
                suffix = p.stem[len(prefix):]
                if suffix.isdigit():
                    available.add(int(suffix))
        free = sorted(available - used)
        if free:
            return f"{prefix}{free[0]}"
        # 真实图全用完：递增分配，但跳过已知中途缺号（如手动删过的 consort_pool_14）。
        # 编号上限：available 最大值 + 缺号集；超出后继续递增（前端 onError fallback 占位符）。
        max_known = max(available, default=0)
        missing = {n for n in range(1, max_known + 1) if n not in available}
        n = 1
        while n in used or n in missing:
            n += 1
        return f"{prefix}{n}"

    def set_portrait_id(self, name: str, portrait_id: str) -> None:
        """改某人物的头像标识（如皇帝上传自定义立绘后落库）。"""
        self.conn.execute(
            "UPDATE characters SET portrait_id=? WHERE name=?",
            (portrait_id, name),
        )
        self.conn.commit()

    def upsert_portrait_asset(
        self,
        *,
        asset_id: str,
        character_name: str,
        kind: str,
        dna_seed: str,
        wardrobe_key: str,
        prompt: str,
        provider: str,
        model: str,
        status: str,
        updated_turn: int,
        error: str = "",
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO portrait_assets
                (asset_id, character_name, kind, dna_seed, wardrobe_key, prompt, provider, model,
                 status, error, updated_turn)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(asset_id) DO UPDATE SET
                character_name=excluded.character_name,
                kind=excluded.kind,
                dna_seed=excluded.dna_seed,
                wardrobe_key=excluded.wardrobe_key,
                prompt=excluded.prompt,
                provider=excluded.provider,
                model=excluded.model,
                status=excluded.status,
                error=excluded.error,
                updated_turn=excluded.updated_turn,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                asset_id, character_name, kind, dna_seed, wardrobe_key, prompt,
                provider, model, status, error[:500], updated_turn,
            ),
        )
        self.conn.commit()

    def mark_portrait_asset_ready(
        self,
        asset_id: str,
        image_blob: bytes,
        *,
        mime_type: str = "image/png",
        width: int = 0,
        height: int = 0,
    ) -> None:
        self._execute_portrait_asset_update(
            """
            UPDATE portrait_assets
            SET status='ready', image_blob=?, mime_type=?, width=?, height=?,
                error='', updated_at=CURRENT_TIMESTAMP
            WHERE asset_id=?
            """,
            (image_blob, mime_type, width, height, asset_id),
        )

    def mark_portrait_asset_error(self, asset_id: str, error: str) -> None:
        self._execute_portrait_asset_update(
            """
            UPDATE portrait_assets
            SET status='error', error=?, updated_at=CURRENT_TIMESTAMP
            WHERE asset_id=?
            """,
            (error[:500], asset_id),
        )

    def _execute_portrait_asset_update(self, sql: str, params: Tuple[Any, ...]) -> None:
        """Serialize portrait-worker writes without sharing the main connection."""
        with self._portrait_asset_lock:
            if self.path == ":memory:":
                self.conn.execute(sql, params)
                self.conn.commit()
                return
            conn = sqlite3.connect(self.path, timeout=30)
            try:
                conn.execute("PRAGMA busy_timeout=30000")
                conn.execute(sql, params)
                conn.commit()
            finally:
                conn.close()

    def get_portrait_asset(self, asset_id: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM portrait_assets WHERE asset_id=?",
            (asset_id,),
        ).fetchone()

    def latest_character_portrait_asset(self, character_name: str, kind: str = "portrait") -> Optional[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT * FROM portrait_assets
            WHERE character_name=? AND kind=?
            ORDER BY updated_at DESC, created_at DESC
            LIMIT 1
            """,
            (character_name, kind),
        ).fetchone()

    def add_character(self, state: GameState, character: "Character", source: str = "") -> None:
        """运行时新建人物（吏部任命/皇帝点名）。已存在同名则不动，避免覆盖既有状态。"""
        existing = self.conn.execute(
            "SELECT name FROM characters WHERE name=?", (character.name,)
        ).fetchone()
        if existing is not None:
            return
        character.office = normalize_office(character.office)
        character.office_type = infer_assignment_office_type(character.office, office_type=character.office_type)
        # 若没有专属 portrait_id，按 office_type 分配预设池头像
        portrait_id = character.portrait_id
        if not portrait_id:
            prefix = "consort_pool_" if character.office_type == "后宫" else "minister_pool_"
            portrait_id = self.next_pool_portrait_id(prefix)
        source_label = source or ("吏部铨选任命" if character.office_type != "后宫" else "诏书纳妃")
        office_source = source or ("吏部任命" if character.office_type != "后宫" else "诏书纳妃")
        self.conn.execute(
            """
            INSERT INTO characters
            (name, office, office_type, faction, aliases, personal_skills, loyalty, ability, integrity, courage, style,
             birth_year, historical_death_year, historical_death_month, debut_year, debut_month,
             status, status_reason, status_changed_turn, portrait_id, power_id, location, summary,
             force, wisdom, charm, luck, cultivation, hp, max_hp, exp, level)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                character.name,
                character.office,
                character.office_type,
                character.faction,
                json.dumps(character.aliases, ensure_ascii=False),
                json.dumps(character.personal_skills, ensure_ascii=False),
                character.loyalty,
                character.ability,
                character.integrity,
                character.courage,
                character.style,
                character.birth_year,
                character.historical_death_year,
                character.historical_death_month,
                character.debut_year,
                character.debut_month,
                character.status,
                source_label,
                state.turn,
                portrait_id,
                getattr(character, "power_id", "ming") or "ming",
                getattr(character, "location", "") or "",
                getattr(character, "summary", "") or "",
                getattr(character, "force", 50),
                getattr(character, "wisdom", 50),
                getattr(character, "charm", 50),
                getattr(character, "luck", 50),
                getattr(character, "cultivation", 0),
                getattr(character, "hp", 100),
                getattr(character, "max_hp", 100),
                getattr(character, "exp", 0),
                getattr(character, "level", 1),
            ),
        )
        self.conn.execute(
            """
            INSERT INTO character_offices (character_name, office_title, office_type, source)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(character_name) DO UPDATE SET
                office_title = excluded.office_title,
                office_type = excluded.office_type,
                source = excluded.source,
                updated_at = CURRENT_TIMESTAMP
            """,
            (character.name, character.office, character.office_type, office_source),
        )
        self.conn.commit()

    def record_economy_moves(
        self,
        state: GameState,
        event: Event,
        edict_id: int,
        actor: str,
        moves: List[Dict[str, object]],
    ) -> None:
        if not moves:
            self.sync_economy_accounts(state)
            self.conn.commit()
            return
        for move in moves:
            self.conn.execute(
                """
                INSERT INTO economy_ledger
                (turn, year, period, account, delta, balance_after, category, reason, event_id, edict_id, actor)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    state.turn,
                    state.year,
                    state.period,
                    str(move["account"]),
                    int(move["delta"]),
                    int(move["balance_after"]),
                    str(move["category"]),
                    str(move["reason"]),
                    event.id,
                    edict_id,
                    actor,
                ),
            )
        self.sync_economy_accounts(state)
        self.conn.commit()

    def treasury_budget_summary(
        self,
        state: "GameState | None" = None,
        *,
        budget: Optional[Dict[str, Dict[str, list]]] = None,
    ) -> str:
        # 三套口径统一：直接调 flows.compute_budget_lines（唯一定额源），此处只负责拼文本。
        if budget is None:
            from ming_sim.flows import compute_budget_lines  # 局部 import 避免与 flows 顶层循环依赖
            st = state if state is not None else self.load_state("")
            budget = compute_budget_lines(self, st)

        def _sum(acc: str, direction: str) -> int:
            return sum(int(it["amount"]) for it in budget[acc][direction])

        def _amt(acc: str, direction: str, name: str) -> int:
            return sum(int(it["amount"]) for it in budget[acc][direction] if it["name"] == name)

        gk_in, gk_out = _sum("国库", "income"), _sum("国库", "expense")
        nk_in, nk_out = _sum("内库", "income"), _sum("内库", "expense")
        return (
            f"{TURN_UNIT}度预算基准：国库入{format_money(gk_in)}"
            f"（田赋+辽饷+盐税+商税+建筑产出{format_money(_amt('国库', 'income', '建筑产出'))}）"
            f"出{format_money(gk_out)}"
            f"（军饷{format_money(_amt('国库', 'expense', '各军军饷'))}+宗室+官俸+补给+"
            f"建筑维护{format_money(_amt('国库', 'expense', '建筑维护'))}）"
            f"净{format_money_delta(gk_in - gk_out)}；"
            f"内库入{format_money(nk_in)}"
            f"出{format_money(nk_out)}"
            f"（内廷维护{format_money(_amt('内库', 'expense', '建筑维护'))}）"
            f"净{format_money_delta(nk_in - nk_out)}。"
        )

    def treasury_report(
        self,
        state: GameState,
        limit: int = 6,
        *,
        budget: Optional[Dict[str, Dict[str, list]]] = None,
    ) -> str:
        account_rows = self.conn.execute(
            "SELECT account, balance FROM economy_accounts ORDER BY account DESC"
        ).fetchall()
        if not account_rows:
            account_text = f"国库{format_money(state.metrics['国库'])}，内库{format_money(state.metrics['内库'])}"
        else:
            account_text = "，".join(f"{row['account']}{format_money(int(row['balance']))}" for row in account_rows)

        period_rows = self.conn.execute(
            """
            SELECT account,
                   SUM(CASE WHEN delta > 0 THEN delta ELSE 0 END) AS income,
                   SUM(CASE WHEN delta < 0 THEN -delta ELSE 0 END) AS expense
            FROM economy_ledger
            WHERE turn = ?
            GROUP BY account
            ORDER BY account DESC
            """,
            (state.turn,),
        ).fetchall()
        period_text = "；".join(
            f"{row['account']}入{format_money(int(row['income'] or 0))}出{format_money(int(row['expense'] or 0))}"
            for row in period_rows
        )
        if not period_text:
            period_text = f"本{TURN_UNIT}尚无新账"

        ledger_rows = self.conn.execute(
            """
            SELECT year, period, account, delta, category, reason, actor
            FROM economy_ledger
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        recent = []
        for row in reversed(ledger_rows):
            delta = int(row["delta"])
            sign = "+" if delta > 0 else ""
            recent.append(
                f"{period_label(int(row['year']), int(row['period']))} {row['account']}{sign}{format_money(delta)} {row['category']}：{row['reason']}"
            )
        recent_text = "；".join(recent) if recent else "未见流水"
        budget_summary = self.treasury_budget_summary(state, budget=budget)
        return f"{budget_summary}账面：{account_text}。本{TURN_UNIT}收支：{period_text}。近账：{recent_text}。"

    def faction_satisfaction(self, faction: str) -> int:
        row = self.conn.execute("SELECT satisfaction FROM factions WHERE name = ?", (faction,)).fetchone()
        return int(row["satisfaction"]) if row else 50

    def faction_leverage(self, faction: str) -> int:
        row = self.conn.execute("SELECT leverage FROM factions WHERE name = ?", (faction,)).fetchone()
        return int(row["leverage"]) if row else 50

    def faction_report(self) -> str:
        rows = self.conn.execute(
            "SELECT name, satisfaction, leverage, agenda FROM factions ORDER BY name"
        ).fetchall()
        if not rows:
            return "派系未建档。"
        return "；".join(
            f"{row['name']}满意{row['satisfaction']}、势力{row['leverage']}，所求：{row['agenda']}"
            for row in rows
        )

    def class_rows(self, region_id: str = "") -> List[sqlite3.Row]:
        """region_id="" 取全国汇总行；其它取该省切片。"""
        return self.conn.execute(
            "SELECT name, region_id, population, satisfaction, leverage, agenda "
            "FROM classes WHERE region_id = ? ORDER BY name",
            (region_id,),
        ).fetchall()

    def class_report(self) -> str:
        """全国汇总 + 各省紧张切片（sat<=30 且 lev>=60）。"""
        national = self.class_rows("")
        if not national:
            return "阶级未建档。"
        head = "；".join(
            f"{row['name']}满意{row['satisfaction']}、势力{row['leverage']}（{row['agenda']}）"
            for row in national
        )
        hot = self.conn.execute(
            """
            SELECT c.name, c.region_id, c.satisfaction, c.leverage, r.name AS region_name
            FROM classes c
            LEFT JOIN regions r ON r.id = c.region_id
            WHERE c.region_id <> '' AND c.satisfaction <= 30 AND c.leverage >= 60
            ORDER BY c.satisfaction ASC, c.leverage DESC
            """
        ).fetchall()
        if not hot:
            return f"阶级总览：{head}。各省阶级暂无高压预警。"
        warn = "；".join(
            f"{row['region_name'] or row['region_id']} {row['name']}满意{row['satisfaction']}/势力{row['leverage']}"
            for row in hot
        )
        return f"阶级总览：{head}。高压预警：{warn}。"

    def adjust_classes(self, deltas: Dict[str, Dict[str, int]]) -> None:
        """deltas 结构：{ key: {satisfaction: +/-N, leverage: +/-N} }
        key 形式：'农民' (全国) 或 '农民@shaanxi' (省级)。
        """
        for key, fields in deltas.items():
            if not fields:
                continue
            if "@" in key:
                name, region_id = key.split("@", 1)
            else:
                name, region_id = key, ""
            row = self.conn.execute(
                "SELECT satisfaction, leverage FROM classes WHERE name = ? AND region_id = ?",
                (name.strip(), region_id.strip()),
            ).fetchone()
            if not row:
                continue
            sat = int(row["satisfaction"]) + int(fields.get("satisfaction", 0) or 0)
            lev = int(row["leverage"]) + int(fields.get("leverage", 0) or 0)
            sat = max(0, min(100, sat))
            lev = max(0, min(100, lev))
            self.conn.execute(
                "UPDATE classes SET satisfaction = ?, leverage = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE name = ? AND region_id = ?",
                (sat, lev, name.strip(), region_id.strip()),
            )
        self.conn.commit()

    def power_rows(self, exclude_self: bool = False) -> List[sqlite3.Row]:
        where = "WHERE id != 'ming'" if exclude_self else ""
        return self.conn.execute(
            f"""
            SELECT *
            FROM powers
            {where}
            ORDER BY CASE id
                WHEN 'ming' THEN 0
                WHEN 'houjin' THEN 1
                WHEN 'mongol' THEN 2
                WHEN 'korea' THEN 3
                WHEN 'japan' THEN 4
                WHEN 'dutch' THEN 5
                WHEN 'bandits' THEN 6
                ELSE 9
            END, name
            """
        ).fetchall()

    def power_payload(self, exclude_self: bool = False) -> List[Dict[str, object]]:
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "kind": row["kind"],
                "leader": row["leader"],
                "stance": row["stance"],
                "leverage": int(row["leverage"]),
                "satisfaction": int(row["satisfaction"]),
                "military_strength": int(row["military_strength"]),
                "cohesion": int(row["cohesion"]),
                "supply": int(row["supply"]),
                "agenda": row["agenda"],
                "status": row["status"],
                "last_action": row["last_action"],
                "aliases": row["aliases"],
            }
            for row in self.power_rows(exclude_self=exclude_self)
        ]

    def power_report(self, exclude_self: bool = True) -> str:
        rows = self.power_rows(exclude_self=exclude_self)
        if not rows:
            return "势力未建档。"
        return "；".join(
            f"{row['name']}（{row['leader']}）：{row['stance']}，威望{row['leverage']}、"
            f"实力{row['military_strength']}、经济{row['supply']}，"
            f"{row['status']}；近动：{row['last_action'] or '尚无新动'}"
            for row in rows
        )

    def apply_power_deltas(
        self,
        state: GameState,
        updates: Dict[str, Dict[str, object]],
    ) -> List[Dict[str, object]]:
        allowed_fields = {"leverage", "military_strength", "supply"}
        changes: List[Dict[str, object]] = []
        for power_id, raw_changes in updates.items():
            if power_id == "ming":
                print("[WARN] power_updates 不再处理大明自身 → 跳过")
                continue
            row = self.conn.execute("SELECT * FROM powers WHERE id = ?", (power_id,)).fetchone()
            if row is None:
                print(f"[WARN] power_updates 引用未入库势力 '{power_id}' → 跳过")
                continue
            reason = str(
                raw_changes.get("reason")
                or raw_changes.get("原因")
                or raw_changes.get("last_action")
                or raw_changes.get("近动")
                or "势力推演"
            ).strip()[:120]
            for raw_field, value in raw_changes.items():
                field = POWER_FIELD_ALIASES.get(str(raw_field).strip(), str(raw_field).strip())
                if field == "reason":
                    continue
                if field not in allowed_fields:
                    print(f"[WARN] power_updates 只允许 威望/实力/经济，'{raw_field}' → 跳过")
                    continue
                old_value = row[field]
                delta = int(value)
                new_value = max(0, min(100, int(old_value) + delta))
                actual_delta = new_value - int(old_value)
                if actual_delta == 0:
                    continue
                stored_new: object = new_value
                log_delta: int | None = actual_delta
                self.conn.execute(
                    f"UPDATE powers SET {field} = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (stored_new, power_id),
                )
                self.conn.execute(
                    """
                    INSERT INTO power_logs
                    (turn, year, period, power_id, field, old_value, new_value, delta, reason)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        state.turn,
                        state.year,
                        state.period,
                        power_id,
                        field,
                        str(old_value),
                        str(stored_new),
                        log_delta,
                        reason,
                    ),
                )
                changes.append({
                    "power": row["name"],
                    "field": field,
                    "label": POWER_FIELD_LABELS.get(field, field),
                    "old": old_value,
                    "new": stored_new,
                    "delta": log_delta,
                    "reason": reason,
                })
        self.conn.commit()
        return changes

    def apply_power_rename(
        self,
        state: GameState,
        power_id: str,
        new_name: str,
        *,
        reason: str,
        aliases: str = "",
        status: str = "",
        last_action: str = "",
    ) -> Dict[str, object] | None:
        """Rename a power while keeping its stable id for references.

        Used for dynastic/name changes such as houjin 后金 -> 大清.
        """
        power_id = str(power_id or "").strip()
        new_name = str(new_name or "").strip()
        if not power_id or not new_name:
            return None
        row = self.conn.execute("SELECT * FROM powers WHERE id = ?", (power_id,)).fetchone()
        if row is None:
            print(f"[WARN] power_rename 引用未入库势力 '{power_id}' → 跳过")
            return None
        old_name = str(row["name"] or "")
        old_aliases = str(row["aliases"] or "")
        merged_aliases = [x.strip() for x in (aliases or old_aliases).replace("，", ",").split(",") if x.strip()]
        for alias in (old_name, new_name):
            if alias and alias not in merged_aliases:
                merged_aliases.append(alias)
        new_aliases = "，".join(merged_aliases)
        new_status = str(status or row["status"] or "")[:200]
        new_last_action = str(last_action or reason or row["last_action"] or "")[:200]
        if old_name == new_name and old_aliases == new_aliases and row["status"] == new_status and row["last_action"] == new_last_action:
            return None
        self.conn.execute(
            """
            UPDATE powers
            SET name=?, aliases=?, status=?, last_action=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (new_name, new_aliases, new_status, new_last_action, power_id),
        )
        self.conn.execute(
            """
            INSERT INTO power_name_logs
            (turn, year, period, power_id, old_name, new_name, old_aliases, new_aliases, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (state.turn, state.year, state.period, power_id, old_name, new_name, old_aliases, new_aliases, reason[:200]),
        )
        self.conn.commit()
        return {
            "power_id": power_id,
            "old_name": old_name,
            "new_name": new_name,
            "old_aliases": old_aliases,
            "new_aliases": new_aliases,
            "reason": reason,
        }

    def turn_power_summary(self, turn: int, limit: int = 10) -> str:
        rows = self.conn.execute(
            """
            SELECT pl.*, p.name AS power_name
            FROM power_logs pl
            JOIN powers p ON p.id = pl.power_id
            WHERE pl.turn = ?
            ORDER BY pl.id
            LIMIT ?
            """,
            (turn, limit),
        ).fetchall()
        if not rows:
            return f"本{TURN_UNIT}势力无明确变化。"
        parts = []
        for row in rows:
            label = POWER_FIELD_LABELS.get(str(row["field"]), str(row["field"]))
            delta = row["delta"]
            if delta is None:
                parts.append(f"{row['power_name']}{label}改为{row['new_value']}（{row['reason']}）")
            else:
                sign = "+" if int(delta) > 0 else ""
                parts.append(f"{row['power_name']}{label}{sign}{int(delta)}（{row['reason']}）")
        return "；".join(parts) + "。"

    def region_rows(self, limit: int | None = None, danger_order: bool = False) -> List[sqlite3.Row]:
        order = (
            "(unrest + military_pressure + gentry_resistance + (100 - public_support)) DESC, name"
            if danger_order
            else "kind DESC, name"
        )
        sql = f"""
            SELECT *
            FROM regions
            ORDER BY {order}
        """
        params: Tuple[object, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        return self.conn.execute(sql, params).fetchall()

    def region_payload(self, limit: int | None = None, danger_order: bool = False) -> List[Dict[str, object]]:
        payload: List[Dict[str, object]] = []
        for row in self.region_rows(limit=limit, danger_order=danger_order):
            payload.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "kind": row["kind"],
                    "population": int(row["population"]),
                    "public_support": int(row["public_support"]),
                    "unrest": int(row["unrest"]),
                    "natural_disaster": row["natural_disaster"],
                    "human_disaster": row["human_disaster"],
                    "registered_land": int(row["registered_land"]),
                    "hidden_land": int(row["hidden_land"]),
                    "tax_per_turn": int(row["tax_per_turn"]),
                    "grain_security": int(row["grain_security"]),
                    "gentry_resistance": int(row["gentry_resistance"]),
                    "military_pressure": int(row["military_pressure"]),
                    "status": row["status"],
                    "controlled_by": row["controlled_by"],
                }
            )
        return payload

    def power_display_name(self, power_id: str) -> str:
        """power_id → 显示名（如 houjin→后金）。缺则回退 id。"""
        row = self.conn.execute(
            "SELECT name FROM powers WHERE id = ?", (str(power_id),)
        ).fetchone()
        return str(row["name"]) if row else str(power_id)

    def region_report(self, limit: int = 5) -> str:
        rows = self.region_rows(limit=limit, danger_order=True)
        if not rows:
            return "地区尚未建档。"
        total_tax = self.conn.execute("SELECT SUM(tax_per_turn) AS total FROM regions").fetchone()
        total_tax_value = int(total_tax["total"] or 0)
        parts = []
        for row in rows:
            held = ""
            if str(row["controlled_by"]) != "ming":
                held = f"【已为{self.power_display_name(row['controlled_by'])}所据】"
            parts.append(
                f"{row['name']}{held}：民心{row['public_support']}、动乱{row['unrest']}、"
                f"粮食{row['grain_security']}万石、税{format_money(monthly_amount(int(row['tax_per_turn'])))}/{TURN_UNIT}，{row['status']}"
            )
        return f"地区警讯：{'；'.join(parts)}。两京十三省账面{TURN_UNIT}税合计{format_money(monthly_amount(total_tax_value))}。"

    def region_detail(self, raw_name: str) -> str:
        region_id = match_region_id_from_text(raw_name, self.content.regions)
        if region_id is None:
            raise ValueError(f"未找到地区：{raw_name}")
        row = self.conn.execute("SELECT * FROM regions WHERE id = ?", (region_id,)).fetchone()
        if row is None:
            raise ValueError(f"地区未入库：{raw_name}")
        held = ""
        if str(row["controlled_by"]) != "ming":
            held = f"，控制权：已为{self.power_display_name(row['controlled_by'])}所据（非大明辖治）"
        return (
            f"{row['name']}（{row['kind']}）{held}：人口{row['population']}万人，"
            f"民心{row['public_support']}，动乱{row['unrest']}，粮食{row['grain_security']}万石，"
            f"田亩{row['registered_land']}万亩，隐田{row['hidden_land']}万亩，"
            f"账面税收{format_money(monthly_amount(int(row['tax_per_turn'])))}/{TURN_UNIT}，"
            f"士绅阻力{row['gentry_resistance']}，军事压力{row['military_pressure']}。"
            f"天灾：{row['natural_disaster']}；人祸：{row['human_disaster']}；状态：{row['status']}"
        )

    def turn_region_summary(self, turn: int, limit: int = 10) -> str:
        rows = self.conn.execute(
            """
            SELECT rl.*, r.name AS region_name
            FROM region_logs rl
            JOIN regions r ON r.id = rl.region_id
            WHERE rl.turn = ?
            ORDER BY rl.id
            LIMIT ?
            """,
            (turn, limit),
        ).fetchall()
        if not rows:
            return f"本{TURN_UNIT}地区盘面无明确变化。"
        parts = []
        for row in rows:
            label = REGION_FIELD_LABELS.get(str(row["field"]), str(row["field"]))
            delta = row["delta"]
            if delta is None:
                parts.append(f"{row['region_name']}{label}改为{row['new_value']}（{row['reason']}）")
            else:
                sign = "+" if int(delta) > 0 else ""
                parts.append(f"{row['region_name']}{label}{sign}{int(delta)}（{row['reason']}）")
        return "；".join(parts) + "。"

    def apply_region_deltas(
        self,
        state: GameState,
        event: Event,
        edict_id: int | None,
        actor: str,
        region_deltas: Dict[str, Dict[str, object]],
    ) -> List[Dict[str, object]]:
        changes: List[Dict[str, object]] = []
        for region_id, raw_changes in region_deltas.items():
            row = self.conn.execute("SELECT * FROM regions WHERE id = ?", (region_id,)).fetchone()
            if row is None:
                print(f"[WARN] region_delta 引用未入库地区 '{region_id}' → 跳过")
                continue
            reason = str(raw_changes.get("reason") or raw_changes.get("原因") or event.title).strip()[:80]
            for raw_field, value in raw_changes.items():
                field = REGION_FIELD_ALIASES.get(str(raw_field).strip(), str(raw_field).strip())
                if field == "reason":
                    continue
                # 先判字段合法，再取值：非法字段直接报清楚。
                all_direct = REGION_SCORE_FIELDS + REGION_QUANTITY_FIELDS + REGION_TEXT_FIELDS
                if field not in all_direct and field not in FISCAL_SCORE_FIELDS:
                    raise LLMContractError(
                        f"{TURN_UNIT}末执行评估引用了非法地区字段：'{raw_field}'（地区 '{region_id}'）。"
                        f"合法字段：{all_direct + FISCAL_SCORE_FIELDS}"
                    )

                # ── fiscal JSON 子字段（corruption 等）────────────────────────
                if field in FISCAL_SCORE_FIELDS:
                    fiscal: dict = json.loads(str(row["fiscal"] or "{}"))
                    old_value = fiscal.get(field, 50)
                    delta = int(value)
                    # 帝国修正：该地区该字段若有 active 修正符，先放大/缩小 delta
                    net_pct = int(((self.legacy_modifiers(state).get("regions") or {})
                                   .get(region_id) or {}).get(field, 0) or 0)
                    if net_pct:
                        delta = self.apply_legacy_pct(delta, net_pct)
                    new_value = max(0, min(100, int(old_value) + delta))
                    actual_delta = new_value - int(old_value)
                    if actual_delta == 0:
                        continue
                    fiscal[field] = new_value
                    self.conn.execute(
                        "UPDATE regions SET fiscal = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (json.dumps(fiscal, ensure_ascii=False), region_id),
                    )
                    self.conn.execute(
                        """
                        INSERT INTO region_logs
                        (turn, year, period, region_id, field, old_value, new_value, delta, reason, event_id, edict_id, actor)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (state.turn, state.year, state.period, region_id,
                         field, str(old_value), str(new_value), actual_delta,
                         reason, event.id, edict_id, actor),
                    )
                    changes.append({
                        "region": row["name"], "field": field,
                        "label": REGION_FIELD_LABELS.get(field, field),
                        "old": old_value, "new": new_value,
                        "delta": actual_delta, "reason": reason,
                    })
                    continue

                # ── 直接列字段 ────────────────────────────────────────────────
                old_value = row[field]
                if field in REGION_SCORE_FIELDS:
                    delta = int(value)
                    # 遗产百分比修正：该地区该字段若有 active 遗产修正符，先放大/缩小 delta
                    net_pct = int(((self.legacy_modifiers(state).get("regions") or {})
                                   .get(region_id) or {}).get(field, 0) or 0)
                    if net_pct:
                        delta = self.apply_legacy_pct(delta, net_pct)
                    new_value = max(0, min(100, int(old_value) + delta))
                    actual_delta = new_value - int(old_value)
                    if actual_delta == 0:
                        continue
                    stored_new: object = new_value
                    log_delta: int | None = actual_delta
                elif field in REGION_QUANTITY_FIELDS:
                    delta = int(value)
                    new_value = max(0, int(old_value) + delta)
                    actual_delta = new_value - int(old_value)
                    if actual_delta == 0:
                        continue
                    stored_new = new_value
                    log_delta = actual_delta
                else:  # REGION_TEXT_FIELDS
                    text_value = str(value).strip()[:160]
                    if not text_value or text_value == str(old_value):
                        continue
                    stored_new = text_value
                    log_delta = None
                self.conn.execute(
                    f"UPDATE regions SET {field} = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (stored_new, region_id),
                )
                self.conn.execute(
                    """
                    INSERT INTO region_logs
                    (turn, year, period, region_id, field, old_value, new_value, delta, reason, event_id, edict_id, actor)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        state.turn, state.year, state.period, region_id,
                        field, str(old_value), str(stored_new), log_delta,
                        reason, event.id, edict_id, actor,
                    ),
                )
                changes.append(
                    {
                        "region": row["name"], "field": field,
                        "label": REGION_FIELD_LABELS.get(field, field),
                        "old": old_value, "new": stored_new,
                        "delta": log_delta, "reason": reason,
                    }
                )

                # ── 收复触发：controlled_by 由非 ming → ming，覆盖 on_restore 预置 ──
                if (
                    field == "controlled_by"
                    and str(stored_new) == "ming"
                    and str(old_value) != "ming"
                ):
                    extra = self._apply_on_restore(state, region_id, event, edict_id, actor, reason)
                    changes.extend(extra)
        self.conn.commit()
        return changes

    def _apply_on_restore(
        self,
        state: GameState,
        region_id: str,
        event: Event,
        edict_id: int | None,
        actor: str,
        reason: str,
    ) -> List[Dict[str, object]]:
        """收复瞬间用 region.on_restore 覆盖主字段，记 region_logs。"""
        region_def = self.content.regions.get(region_id)
        if region_def is None or not region_def.on_restore:
            return []
        preset = region_def.on_restore
        row = self.conn.execute("SELECT * FROM regions WHERE id = ?", (region_id,)).fetchone()
        if row is None:
            return []
        all_direct = REGION_SCORE_FIELDS + REGION_QUANTITY_FIELDS + REGION_TEXT_FIELDS
        out: List[Dict[str, object]] = []
        for raw_field, value in preset.items():
            if raw_field == "fiscal":
                if not isinstance(value, dict):
                    continue
                fiscal = json.loads(str(row["fiscal"] or "{}"))
                for sub_field, sub_val in value.items():
                    if sub_field not in FISCAL_SCORE_FIELDS:
                        continue
                    old_sub = fiscal.get(sub_field, 0)
                    new_sub = int(sub_val)
                    if int(old_sub) == new_sub:
                        continue
                    fiscal[sub_field] = new_sub
                    self.conn.execute(
                        "INSERT INTO region_logs (turn, year, period, region_id, field, old_value, new_value, delta, reason, event_id, edict_id, actor) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (state.turn, state.year, state.period, region_id,
                         sub_field, str(old_sub), str(new_sub), new_sub - int(old_sub),
                         f"收复重置：{reason}", event.id, edict_id, actor),
                    )
                    out.append({
                        "region": row["name"], "field": sub_field,
                        "label": REGION_FIELD_LABELS.get(sub_field, sub_field),
                        "old": old_sub, "new": new_sub,
                        "delta": new_sub - int(old_sub), "reason": f"收复重置：{reason}",
                    })
                self.conn.execute(
                    "UPDATE regions SET fiscal = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (json.dumps(fiscal, ensure_ascii=False), region_id),
                )
                continue
            if raw_field == "controlled_by":
                continue  # 控制权已写完，跳过
            if raw_field not in all_direct:
                continue
            old_val = row[raw_field]
            if raw_field in (REGION_SCORE_FIELDS + REGION_QUANTITY_FIELDS):
                new_val: object = int(value)
            else:
                new_val = str(value)
            if str(old_val) == str(new_val):
                continue
            self.conn.execute(
                f"UPDATE regions SET {raw_field} = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (new_val, region_id),
            )
            log_delta = (int(new_val) - int(old_val)) if raw_field in (REGION_SCORE_FIELDS + REGION_QUANTITY_FIELDS) else None
            self.conn.execute(
                "INSERT INTO region_logs (turn, year, period, region_id, field, old_value, new_value, delta, reason, event_id, edict_id, actor) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (state.turn, state.year, state.period, region_id,
                 raw_field, str(old_val), str(new_val), log_delta,
                 f"收复重置：{reason}", event.id, edict_id, actor),
            )
            out.append({
                "region": row["name"], "field": raw_field,
                "label": REGION_FIELD_LABELS.get(raw_field, raw_field),
                "old": old_val, "new": new_val,
                "delta": log_delta, "reason": f"收复重置：{reason}",
            })
        return out

    def army_rows(self, limit: int | None = None, danger_order: bool = False) -> List[sqlite3.Row]:
        # arrears 是累计欠饷万两，须按 maintenance 归一成"欠饷月数*10"再加权（0-100 量级）
        # CASE 兼容 SQLite（无标量 MIN/LEAST）：maintenance=0 视为 0；归一后截至 100。
        arrears_norm = (
            "CASE "
            "WHEN maintenance_per_turn IS NULL OR maintenance_per_turn = 0 THEN 0 "
            "WHEN arrears * 10 / maintenance_per_turn > 100 THEN 100 "
            "ELSE arrears * 10 / maintenance_per_turn "
            "END"
        )
        order = (
            f"({arrears_norm} + (100 - supply) + (100 - morale) + (100 - loyalty) + (100 - training)) DESC, name"
            if danger_order
            else "theater, name"
        )
        sql = f"""
            SELECT *
            FROM armies
            ORDER BY {order}
        """
        params: Tuple[object, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        return self.conn.execute(sql, params).fetchall()

    def army_payload(self, limit: int | None = None, danger_order: bool = False) -> List[Dict[str, object]]:
        payload: List[Dict[str, object]] = []
        for row in self.army_rows(limit=limit, danger_order=danger_order):
            payload.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "station": row["station"],
                    "theater": row["theater"],
                    "commander": row["commander"],
                    "controller": row["controller"],
                    "troop_type": row["troop_type"],
                    "manpower": int(row["manpower"]),
                    "maintenance_per_turn": int(row["maintenance_per_turn"]),
                    "supply": int(row["supply"]),
                    "morale": int(row["morale"]),
                    "training": int(row["training"]),
                    "equipment": int(row["equipment"]),
                    "arrears": int(row["arrears"]),
                    "mobility": int(row["mobility"]),
                    "loyalty": int(row["loyalty"]),
                    "status": row["status"],
                    "owner_power": row["owner_power"],
                }
            )
        return payload

    def army_report(self, limit: int = 5) -> str:
        rows = self.army_rows(limit=limit, danger_order=True)
        if not rows:
            return "军队尚未建档。"
        total_manpower = self.conn.execute("SELECT SUM(manpower) AS total FROM armies").fetchone()
        total_maintenance = self.conn.execute("SELECT SUM(maintenance_per_turn) AS total FROM armies").fetchone()
        parts = []
        for row in rows:
            maint = int(row["maintenance_per_turn"]) or 0
            arr = int(row["arrears"]) or 0
            if maint > 0 and arr > 0:
                months = arr / maint
                arr_text = f"欠饷{arr}万两（约{months:.1f}月军饷）"
            else:
                arr_text = f"欠饷{arr}万两"
            parts.append(
                f"{row['name']}：驻{row['station']}，兵{row['manpower']}，"
                f"饷{format_money(monthly_amount(maint))} /{TURN_UNIT}，补给{row['supply']}、"
                f"士气{row['morale']}、{arr_text}，{row['status']}"
            )
        return (
            f"军队警讯：{'；'.join(parts)}。"
            f"建档兵力合计{int(total_manpower['total'] or 0)}人，账面{TURN_UNIT}维护费{format_money(monthly_amount(int(total_maintenance['total'] or 0)))}。"
        )

    def army_detail(self, raw_name: str) -> str:
        army_id = match_army_id_from_text(raw_name, self.content.armies)
        if army_id is None:
            raise ValueError(f"未找到军队：{raw_name}")
        row = self.conn.execute("SELECT * FROM armies WHERE id = ?", (army_id,)).fetchone()
        if row is None:
            raise ValueError(f"军队未入库：{raw_name}")
        maint = int(row["maintenance_per_turn"]) or 0
        arr = int(row["arrears"]) or 0
        if maint > 0 and arr > 0:
            months = arr / maint
            arr_text = f"欠饷{arr}万两（约{months:.1f}月军饷）"
        else:
            arr_text = f"欠饷{arr}万两"
        return (
            f"{row['name']}：驻扎地{row['station']}，统帅{row['commander']}，"
            f"兵种{row['troop_type']}，人数{row['manpower']}人，"
            f"维护费{format_money(monthly_amount(maint))} /{TURN_UNIT}，补给{row['supply']}，"
            f"士气{row['morale']}，训练{row['training']}，装备{row['equipment']}，"
            f"{arr_text}，机动{row['mobility']}，忠诚{row['loyalty']}。"
            f"状态：{row['status']}"
        )

    def army_roster(self) -> str:
        """全军名册——表格（| 分隔）压 token。大明各军给全字段，敌对/外藩军只给可见情报。
        固定喂进大臣 system；去掉 list_armies/inspect_army 后大臣据此作答。
        欠饷万两=精确累计欠饷（整数无上限，非 0-100 抽象分）；欠饷月数=欠饷/月饷，便于直接奏对。"""
        rows = self.conn.execute(
            "SELECT * FROM armies ORDER BY owner_power='ming' DESC, theater, name"
        ).fetchall()
        if not rows:
            return ""
        own: List[str] = []
        other: List[str] = []
        for row in rows:
            maint = int(row["maintenance_per_turn"]) or 0
            arr = int(row["arrears"]) or 0
            # 全按月度：maintenance_per_turn 就是月饷，不除 3（别被 monthly_amount 命名误导）。
            monthly_pay = maint
            months = f"{arr / monthly_pay:.1f}" if monthly_pay > 0 and arr > 0 else "0"
            if str(row["owner_power"]) == "ming":
                # 列序见表头。兵力/月饷/欠饷单位万两；补给…忠诚为 0-100。
                own.append(
                    "|".join(str(x) for x in (
                        row["name"], row["station"], row["commander"], row["troop_type"],
                        row["manpower"], monthly_pay, row["supply"], row["morale"],
                        row["training"], row["equipment"], row["mobility"], row["loyalty"],
                        arr, months, row["status"],
                    ))
                )
            else:
                other.append(
                    "|".join(str(x) for x in (
                        row["name"], row["owner_power"], row["station"],
                        row["commander"], row["troop_type"], row["manpower"], row["status"],
                    ))
                )
        out = [
            "【全军名册（现状以此为准，谈某军欠饷/补给/士气直接据此；欠饷万两为精确累计值，非抽象分）】",
            "大明各军（| 分隔，列序＝军名|驻地|统帅|兵种|兵力|月饷万两|补给|士气|训练|装备|机动|忠诚|欠饷万两|欠饷月数|状态；补给…忠诚为0-100）：",
            *own,
        ]
        if other:
            out.append("敌对/外藩军（可见情报，列序＝军名|势力|驻地|统帅|兵种|兵力|状态）：")
            out.extend(other)
        return "\n".join(out)

    def turn_army_summary(self, turn: int, limit: int = 10) -> str:
        rows = self.conn.execute(
            """
            SELECT al.*, a.name AS army_name
            FROM army_logs al
            JOIN armies a ON a.id = al.army_id
            WHERE al.turn = ?
            ORDER BY al.id
            LIMIT ?
            """,
            (turn, limit),
        ).fetchall()
        if not rows:
            return f"本{TURN_UNIT}军队盘面无明确变化。"
        parts = []
        for row in rows:
            label = ARMY_FIELD_LABELS.get(str(row["field"]), str(row["field"]))
            delta = row["delta"]
            if delta is None:
                parts.append(f"{row['army_name']}{label}改为{row['new_value']}（{row['reason']}）")
            else:
                sign = "+" if int(delta) > 0 else ""
                if row["field"] == "manpower":
                    parts.append(f"{row['army_name']}{label}{sign}{int(delta)}人（{row['reason']}）")
                elif row["field"] == "maintenance_per_turn":
                    parts.append(f"{row['army_name']}{label}{format_money_delta(int(delta))}（{row['reason']}）")
                else:
                    parts.append(f"{row['army_name']}{label}{sign}{int(delta)}（{row['reason']}）")
        return "；".join(parts) + "。"

    def apply_army_deltas(
        self,
        state: GameState,
        event: Event,
        edict_id: int | None,
        actor: str,
        army_deltas: Dict[str, Dict[str, object]],
    ) -> List[Dict[str, object]]:
        changes: List[Dict[str, object]] = []
        for army_id, raw_changes in army_deltas.items():
            row = self.conn.execute("SELECT * FROM armies WHERE id = ?", (army_id,)).fetchone()
            if row is None:
                print(f"[WARN] army_delta 引用未入库军队 '{army_id}' → 跳过")
                continue
            reason = str(raw_changes.get("reason") or raw_changes.get("原因") or event.title).strip()[:80]
            _valid_army_fields = set(ARMY_SCORE_FIELDS + ARMY_QUANTITY_FIELDS + ARMY_TEXT_FIELDS)
            for raw_field, value in raw_changes.items():
                field = ARMY_FIELD_ALIASES.get(str(raw_field).strip(), str(raw_field).strip())
                if field == "reason":
                    continue
                if field not in _valid_army_fields:
                    print(f"[WARN] army_delta 引用非法字段 '{raw_field}' → 跳过")
                    continue
                old_value = row[field]
                if field == "arrears":
                    # arrears 单位=累计欠饷万两，无上限，按需累加。
                    # 正常情况由 flows 唯一变更；此处兜底允许 extractor 在战损/裁军等
                    # 非现金原因下写入（提示词已禁，但保留兜底以防 LLM 越界不至于截断）。
                    delta = int(value)
                    new_value = max(0, int(old_value) + delta)
                    actual_delta = new_value - int(old_value)
                    if actual_delta == 0:
                        continue
                    stored_new: object = new_value
                    log_delta: int | None = actual_delta
                elif field in ARMY_SCORE_FIELDS:
                    delta = int(value)
                    # 遗产百分比修正：该军该字段若有 active 遗产修正符，先放大/缩小 delta
                    net_pct = int(((self.legacy_modifiers(state).get("armies") or {})
                                   .get(army_id) or {}).get(field, 0) or 0)
                    if net_pct:
                        delta = self.apply_legacy_pct(delta, net_pct)
                    new_value = max(0, min(100, int(old_value) + delta))
                    actual_delta = new_value - int(old_value)
                    if actual_delta == 0:
                        continue
                    stored_new = new_value
                    log_delta = actual_delta
                elif field == "manpower":
                    delta = int(value)
                    new_value = max(0, int(old_value) + delta)
                    actual_delta = new_value - int(old_value)
                    if actual_delta == 0:
                        continue
                    stored_new = new_value
                    log_delta = actual_delta
                elif field == "maintenance_per_turn":
                    delta = int(value)
                    new_value = max(0, int(old_value) + delta)
                    actual_delta = new_value - int(old_value)
                    if actual_delta == 0:
                        continue
                    stored_new = new_value
                    log_delta = actual_delta
                elif field in ARMY_TEXT_FIELDS:
                    text_value = str(value).strip()[:160]
                    if not text_value or text_value == str(old_value):
                        continue
                    stored_new = text_value
                    log_delta = None
                else:
                    print(f"[WARN] army_delta 未处理字段 '{field}' → 跳过")
                    continue
                self.conn.execute(
                    f"UPDATE armies SET {field} = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (stored_new, army_id),
                )
                self.conn.execute(
                    """
                    INSERT INTO army_logs
                    (turn, year, period, army_id, field, old_value, new_value, delta, reason, event_id, edict_id, actor)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        state.turn,
                        state.year,
                        state.period,
                        army_id,
                        field,
                        str(old_value),
                        str(stored_new),
                        log_delta,
                        reason,
                        event.id,
                        edict_id,
                        actor,
                    ),
                )
                changes.append(
                    {
                        "army": row["name"],
                        "field": field,
                        "label": ARMY_FIELD_LABELS.get(field, field),
                        "old": old_value,
                        "new": stored_new,
                        "delta": log_delta,
                        "reason": reason,
                    }
                )
        self.conn.commit()
        return changes

    def create_armies_from_extraction(
        self,
        state: GameState,
        new_armies: List[Dict[str, object]],
        actor: str = "档房",
    ) -> List[Dict[str, object]]:
        """据 extractor 输出建新军队。同 id/name 已存在 → 把 manpower 当扩军增量。owner_power 必须是已知 power。"""
        valid_powers = {r["id"] for r in self.conn.execute("SELECT id FROM powers").fetchall()}
        created: List[Dict[str, object]] = []
        for raw in new_armies:
            if not isinstance(raw, dict):
                continue
            item = {POWER_FIELD_ALIASES.get(k, k) if False else k: v for k, v in raw.items()}
            # 规范键：复用 ARMY_FIELD_ALIASES（兼容中文）
            from ming_sim.constants import ARMY_FIELD_ALIASES as _AA
            item = {_AA.get(str(k).strip(), str(k).strip()): v for k, v in raw.items()}
            aid = str(item.get("id") or "").strip()
            if not aid:
                print(f"[WARN] new_armies 缺 id → 跳过: {raw}")
                continue
            owner = str(item.get("owner_power") or "ming").strip() or "ming"
            if owner not in valid_powers:
                print(f"[WARN] new_armies owner_power '{owner}' 未在 powers → 跳过 {aid}")
                continue
            name = str(item.get("name") or aid).strip()
            # 查重：同 id 或 同 name → 转 manpower 扩军增量
            existing = self.conn.execute(
                "SELECT id, name FROM armies WHERE id = ? OR name = ?", (aid, name)
            ).fetchone()
            if existing is not None:
                manpower = item.get("manpower")
                if manpower is None:
                    print(f"[WARN] new_armies 重复 id/name '{aid}' 且无 manpower → 跳过")
                    continue
                try:
                    delta = int(manpower)
                except (TypeError, ValueError):
                    print(f"[WARN] new_armies '{aid}' manpower 非整数 → 跳过")
                    continue
                if delta == 0:
                    continue
                reason = str(item.get("reason") or item.get("status") or "扩军")[:80]
                pseudo_event = type("E", (), {"id": "season", "title": reason})()
                self.apply_army_deltas(
                    state, pseudo_event, None, actor, {existing["id"]: {"manpower": delta, "reason": reason}}
                )
                created.append({"army": existing["name"], "manpower_added": delta, "merged_into_existing": True})
                continue
            # 必填字段
            try:
                manpower = int(item["manpower"])
                maintenance = int(item["maintenance_per_turn"])
            except (KeyError, TypeError, ValueError):
                print(f"[WARN] new_armies '{aid}' 缺 manpower/maintenance_per_turn → 跳过")
                continue
            def _score(field: str, default: int = 50) -> int:
                try:
                    return max(0, min(100, int(item.get(field, default))))
                except (TypeError, ValueError):
                    return default
            def _arrears_init() -> int:
                # arrears 单位=累计欠饷万两，无上限；新军默认 0
                try:
                    return max(0, int(item.get("arrears", 0)))
                except (TypeError, ValueError):
                    return 0
            commander = str(item.get("commander") or "")
            row = (
                aid,
                name,
                str(item.get("station") or ""),
                str(item.get("theater") or ""),
                commander,
                str(item.get("controller") or commander),
                str(item.get("troop_type") or ""),
                max(0, manpower),
                max(0, maintenance),
                _score("supply"),
                _score("morale"),
                _score("training"),
                _score("equipment"),
                _arrears_init(),
                _score("mobility"),
                _score("loyalty"),
                str(item.get("status") or "新立"),
                owner,
            )
            try:
                self.conn.execute(
                    """
                    INSERT INTO armies
                    (id, name, station, theater, commander, controller, troop_type, manpower,
                     maintenance_per_turn, supply, morale, training, equipment, arrears,
                     mobility, loyalty, status, owner_power)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    row,
                )
            except sqlite3.IntegrityError as exc:
                print(f"[WARN] new_armies INSERT 失败 '{aid}': {exc}")
                continue
            reason = str(item.get("reason") or item.get("status") or "新立军队")[:80]
            self.conn.execute(
                """
                INSERT INTO army_logs
                (turn, year, period, army_id, field, old_value, new_value, delta, reason, event_id, edict_id, actor)
                VALUES (?, ?, ?, ?, 'created', '', ?, ?, ?, 'season', NULL, ?)
                """,
                (state.turn, state.year, state.period, aid, str(manpower), manpower, reason, actor),
            )
            created.append({
                "army": name,
                "id": aid,
                "owner_power": owner,
                "manpower": manpower,
                "created": True,
                "reason": reason,
            })
        self.conn.commit()
        return created

    # ── 建筑 ──────────────────────────────────────────────────────────────────

    def add_building(
        self,
        state: GameState,
        region_id: str,
        name: str,
        category: str,
        *,
        level: int = 1,
        condition: int = 60,
        maintenance: int = 0,
        risk: int = 30,
        output_metric: str = "",
        output_amount: int = 0,
        status: str = "",
        origin: str = "decree",
    ) -> str:
        """运行时新立建筑（玩家诏书）。category / output_metric 走白名单硬校验，违规 ValueError。"""
        if category not in BUILDING_CATEGORIES:
            raise ValueError(f"建筑 category 非法 '{category}'，白名单 {BUILDING_CATEGORIES}")
        if output_metric not in BUILDING_OUTPUT_METRICS:
            raise ValueError(f"建筑 output_metric 非法 '{output_metric}'，白名单 {BUILDING_OUTPUT_METRICS}")
        if self.conn.execute("SELECT 1 FROM regions WHERE id = ?", (region_id,)).fetchone() is None:
            raise ValueError(f"建筑 region_id 引用未入库地区 '{region_id}'")
        base = re.sub(r"[^a-z0-9]+", "", (region_id or "rgn").lower()) or "rgn"
        seq = self.conn.execute(
            "SELECT COUNT(*) FROM buildings WHERE region_id = ?", (region_id,)
        ).fetchone()[0]
        building_id = f"{base}_b{int(seq) + 1}"
        while self.conn.execute("SELECT 1 FROM buildings WHERE id = ?", (building_id,)).fetchone():
            seq += 1
            building_id = f"{base}_b{int(seq) + 1}"
        self.conn.execute(
            """
            INSERT INTO buildings
            (id, region_id, name, category, level, condition, maintenance, risk,
             output_metric, output_amount, status, origin, created_turn)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                building_id,
                region_id,
                name.strip()[:60] or "无名建筑",
                category,
                max(1, min(5, int(level))),
                max(0, min(100, int(condition))),
                max(0, int(maintenance)),
                max(0, min(100, int(risk))),
                output_metric,
                max(0, int(output_amount)),
                status.strip()[:160] or "新立，尚在筹建。",
                origin,
                state.turn,
            ),
        )
        self.conn.execute(
            """
            INSERT INTO building_logs
            (turn, year, period, building_id, field, old_value, new_value, delta, reason, actor)
            VALUES (?, ?, ?, ?, 'create', '', ?, NULL, ?, '档房')
            """,
            (state.turn, state.year, state.period, building_id, name.strip()[:60], "诏书新立建筑"),
        )
        self.conn.commit()
        return building_id

    def remove_building(self, state: GameState, building_id: str, reason: str = "") -> bool:
        """拆除/废止建筑（issue 失败或撤销结案）。返回是否真删了一行。"""
        row = self.conn.execute("SELECT name FROM buildings WHERE id = ?", (building_id,)).fetchone()
        if row is None:
            return False
        self.conn.execute(
            """
            INSERT INTO building_logs
            (turn, year, period, building_id, field, old_value, new_value, delta, reason, actor)
            VALUES (?, ?, ?, ?, 'remove', ?, '', NULL, ?, '档房')
            """,
            (state.turn, state.year, state.period, building_id,
             str(row["name"]), (reason or "建筑废止").strip()[:80]),
        )
        self.conn.execute("DELETE FROM buildings WHERE id = ?", (building_id,))
        self.conn.commit()
        return True

    def apply_building_deltas(
        self,
        state: GameState,
        event: Event,
        edict_id: int | None,
        actor: str,
        building_deltas: Dict[str, Dict[str, object]],
    ) -> List[Dict[str, object]]:
        """改既有建筑。仿 apply_army_deltas。供 issue effect 落地复用。"""
        changes: List[Dict[str, object]] = []
        valid_fields = set(BUILDING_SCORE_FIELDS + BUILDING_QUANTITY_FIELDS + BUILDING_TEXT_FIELDS)
        for building_id, raw_changes in building_deltas.items():
            row = self.conn.execute("SELECT * FROM buildings WHERE id = ?", (building_id,)).fetchone()
            if row is None:
                print(f"[WARN] building_delta 引用未入库建筑 '{building_id}' → 跳过")
                continue
            reason = str(raw_changes.get("reason") or event.title).strip()[:80]
            for field, value in raw_changes.items():
                if field == "reason":
                    continue
                if field not in valid_fields:
                    print(f"[WARN] building_delta 引用非法字段 '{field}' → 跳过")
                    continue
                old_value = row[field]
                if field in BUILDING_SCORE_FIELDS:
                    new_value = max(0, min(100, int(old_value) + int(value)))
                    actual_delta = new_value - int(old_value)
                    if actual_delta == 0:
                        continue
                    stored_new: object = new_value
                    log_delta: int | None = actual_delta
                elif field == "level":
                    new_value = max(1, min(5, int(old_value) + int(value)))
                    actual_delta = new_value - int(old_value)
                    if actual_delta == 0:
                        continue
                    stored_new = new_value
                    log_delta = actual_delta
                elif field in ("maintenance", "output_amount"):
                    new_value = max(0, int(old_value) + int(value))
                    actual_delta = new_value - int(old_value)
                    if actual_delta == 0:
                        continue
                    stored_new = new_value
                    log_delta = actual_delta
                elif field == "output_metric":
                    text_value = str(value).strip()
                    if text_value not in BUILDING_OUTPUT_METRICS:
                        print(f"[WARN] building_delta output_metric 非法 '{text_value}' → 跳过")
                        continue
                    if text_value == str(old_value):
                        continue
                    stored_new = text_value
                    log_delta = None
                elif field in BUILDING_TEXT_FIELDS:
                    text_value = str(value).strip()[:160]
                    if not text_value or text_value == str(old_value):
                        continue
                    stored_new = text_value
                    log_delta = None
                else:
                    print(f"[WARN] building_delta 未处理字段 '{field}' → 跳过")
                    continue
                self.conn.execute(
                    f"UPDATE buildings SET {field} = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (stored_new, building_id),
                )
                self.conn.execute(
                    """
                    INSERT INTO building_logs
                    (turn, year, period, building_id, field, old_value, new_value, delta, reason, event_id, edict_id, actor)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        state.turn, state.year, state.period, building_id, field,
                        str(old_value), str(stored_new), log_delta, reason,
                        event.id, edict_id, actor,
                    ),
                )
                changes.append({
                    "building": row["name"],
                    "field": field,
                    "label": BUILDING_FIELD_LABELS.get(field, field),
                    "old": old_value,
                    "new": stored_new,
                    "delta": log_delta,
                    "reason": reason,
                })
        self.conn.commit()
        return changes

    def buildings_report(self, region_id: str = "") -> str:
        """月末奏报 / web 用建筑盘面摘要。region_id 为空取全国。"""
        if region_id:
            rows = self.conn.execute(
                "SELECT * FROM buildings WHERE region_id = ? ORDER BY category, name", (region_id,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM buildings ORDER BY region_id, category, name"
            ).fetchall()
        if not rows:
            return "（暂无建筑在册）"
        lines: List[str] = []
        for r in rows:
            metric = str(r["output_metric"])
            if metric:
                out = f"产出{metric}{r['output_amount']}"
            else:
                out = "无结算产出"
            lines.append(
                f"{r['name']}（{r['category']}·{r['region_id']}）等级{r['level']}，"
                f"完好{r['condition']}，维护{r['maintenance']}{MONEY_UNIT}/{TURN_UNIT}，"
                f"风险{r['risk']}，{out}。{r['status']}"
            )
        return "\n".join(lines)

    def building_payload(self, region_id: str = "") -> List[Dict[str, object]]:
        """建筑结构化清单，供 web。region_id 为空取全国。"""
        if region_id:
            rows = self.conn.execute(
                "SELECT * FROM buildings WHERE region_id = ? ORDER BY category, name", (region_id,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM buildings ORDER BY region_id, category, name"
            ).fetchall()
        return [
            {
                "id": str(r["id"]),
                "region_id": str(r["region_id"]),
                "name": str(r["name"]),
                "category": str(r["category"]),
                "level": int(r["level"]),
                "condition": int(r["condition"]),
                "maintenance": int(r["maintenance"]),
                "risk": int(r["risk"]),
                "output_metric": str(r["output_metric"]),
                "output_amount": int(r["output_amount"]),
                "status": str(r["status"]),
                "origin": str(r["origin"]),
            }
            for r in rows
        ]

    def building_detail(self, name_or_id: str) -> str:
        key = (name_or_id or "").strip()
        row = self.conn.execute(
            "SELECT * FROM buildings WHERE id = ? OR name = ?", (key, key)
        ).fetchone()
        if row is None:
            row = self.conn.execute(
                "SELECT * FROM buildings WHERE name LIKE ?", (f"%{key}%",)
            ).fetchone()
        if row is None:
            raise ValueError(f"未找到建筑 '{name_or_id}'")
        metric = str(row["output_metric"])
        out = f"产出{metric}{row['output_amount']}/{TURN_UNIT}" if metric else "无结算产出"
        return (
            f"{row['name']}（{row['category']}，{row['region_id']}，{row['origin']}）："
            f"等级{row['level']}，完好{row['condition']}，"
            f"维护{row['maintenance']}{MONEY_UNIT}/{TURN_UNIT}，风险{row['risk']}，{out}。\n"
            f"{row['status']}"
        )

    def adjust_factions(self, deltas: Dict[str, object]) -> None:
        for faction, val in deltas.items():
            if isinstance(val, dict):
                sat_d = int(val.get("satisfaction") or 0)
                lev_d = int(val.get("leverage") or 0)
            else:
                try:
                    sat_d = int(val)  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    continue
                lev_d = 0
            if sat_d == 0 and lev_d == 0:
                continue
            row = self.conn.execute(
                "SELECT satisfaction, leverage FROM factions WHERE name = ?", (faction,)
            ).fetchone()
            if not row:
                continue
            new_sat = max(0, min(100, int(row["satisfaction"]) + sat_d))
            new_lev = max(0, min(100, int(row["leverage"]) + lev_d))
            self.conn.execute(
                "UPDATE factions SET satisfaction = ?, leverage = ? WHERE name = ?",
                (new_sat, new_lev, faction),
            )
        self.conn.commit()

    def turn_economy_summary(self, turn: int) -> str:
        rows = self.conn.execute(
            """
            SELECT account,
                   SUM(CASE WHEN delta > 0 THEN delta ELSE 0 END) AS income,
                   SUM(CASE WHEN delta < 0 THEN -delta ELSE 0 END) AS expense,
                   SUM(delta) AS net
            FROM economy_ledger
            WHERE turn = ? AND category <> '期初'
            GROUP BY account
            ORDER BY account DESC
            """,
            (turn,),
        ).fetchall()
        if not rows:
            return f"本{TURN_UNIT}无新增收支。"
        parts = []
        for row in rows:
            income = int(row["income"] or 0)
            expense = int(row["expense"] or 0)
            net = int(row["net"] or 0)
            parts.append(
                f"{row['account']}收入{format_money(income)}、支出{format_money(expense)}、净变{format_money_delta(net)}"
            )
        return "；".join(parts) + "。"

    def treasury_ledger(self, account: str, turns: int = 6) -> str:
        """查国库或内库最近 N 回合流水明细。"""
        rows = self.conn.execute(
            """
            SELECT turn, year, period, delta, balance_after, category, reason, actor
            FROM economy_ledger
            WHERE account = ? AND category <> '期初'
            ORDER BY id DESC
            LIMIT ?
            """,
            (account, turns * 20),
        ).fetchall()
        if not rows:
            return f"{account}无流水记录。"
        lines = [f"【{account}近{turns}回合流水（最新在前）】"]
        for r in rows:
            sign = "+" if int(r["delta"]) > 0 else ""
            lines.append(
                f"{r['year']}年{r['period']}月（turn{r['turn']}）"
                f" {sign}{format_money_delta(int(r['delta']))} → 余{format_money(int(r['balance_after']))} "
                f"[{r['category']}] {r['reason']}"
                + (f"（{r['actor']}）" if r["actor"] else "")
            )
        return "\n".join(lines)

    def previous_turn_summary(self, state: GameState) -> str:
        previous_turn = state.turn - 1
        # turn=0 是开局即位邸报（seed_opening_gazette 落库）；turn<0 才算未登基前。
        if previous_turn < 0:
            return f"登基伊始，尚无上{TURN_UNIT}回奏。"

        # 上回合奏报单独存在 turn_reports，直接取。
        report = self.get_turn_report(previous_turn)
        if report:
            return report
        if previous_turn == 0:
            return f"登基伊始，尚无上{TURN_UNIT}回奏。"

        logs = self.conn.execute(
            "SELECT message FROM turn_logs WHERE turn = ? ORDER BY id",
            (previous_turn,),
        ).fetchall()
        if not logs:
            return f"上{TURN_UNIT}未见正式记录。"

        lines = [
            f"上{TURN_UNIT}回顾：",
            f"钱粮：{self.turn_economy_summary(previous_turn)}",
            f"地区：{self.turn_region_summary(previous_turn)}",
            f"军队：{self.turn_army_summary(previous_turn)}",
            f"势力：{self.turn_power_summary(previous_turn)}",
        ]
        return "\n".join(lines)

    def record_log(self, state: GameState, message: str) -> None:
        self.conn.execute(
            "INSERT INTO turn_logs (turn, year, period, message) VALUES (?, ?, ?, ?)",
            (state.turn, state.year, state.period, message),
        )
        self.conn.commit()

    def append_chat_message(self, minister_name: str, turn: int, role: str, content: str) -> int:
        """召对聊天单条消息落库（chat_messages）。"""
        cur = self.conn.execute(
            "INSERT INTO chat_messages (minister_name, turn, role, content) VALUES (?, ?, ?, ?)",
            (minister_name, turn, role, content),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def load_all_chat_history(self) -> Dict[str, List[Dict[str, str]]]:
        """读出全部召对记录，按大臣分组，供进程启动时恢复内存缓存。"""
        rows = self.conn.execute(
            "SELECT minister_name, role, content FROM chat_messages ORDER BY id"
        ).fetchall()
        history: Dict[str, List[Dict[str, str]]] = {}
        for row in rows:
            history.setdefault(row["minister_name"], []).append(
                {"role": row["role"], "content": row["content"]}
            )
        return history

    def load_recent_chat_history(self, limit_per_minister: int = 80) -> Dict[str, List[Dict[str, str]]]:
        """读出每名 NPC 最近 N 条召对记录，供 Web 进程恢复轻量缓存。"""
        try:
            limit = max(1, int(limit_per_minister or 80))
        except (TypeError, ValueError):
            limit = 80
        try:
            rows = self.conn.execute(
                """
                SELECT minister_name, role, content FROM (
                    SELECT
                        minister_name,
                        role,
                        content,
                        ROW_NUMBER() OVER (PARTITION BY minister_name ORDER BY id DESC) AS rn
                    FROM chat_messages
                )
                WHERE rn <= ?
                ORDER BY minister_name, rn DESC
                """,
                (limit,),
            ).fetchall()
        except sqlite3.DatabaseError:
            rows = self.conn.execute(
                "SELECT minister_name, role, content FROM chat_messages ORDER BY id"
            ).fetchall()
            trimmed: Dict[str, List[Any]] = {}
            for row in rows:
                bucket = trimmed.setdefault(row["minister_name"], [])
                bucket.append(row)
                if len(bucket) > limit:
                    del bucket[0]
            rows = [row for bucket in trimmed.values() for row in bucket]
        history: Dict[str, List[Dict[str, str]]] = {}
        for row in rows:
            history.setdefault(row["minister_name"], []).append(
                {"role": row["role"], "content": row["content"]}
            )
        return history

    # ----- chat_turns（本回合召对撤回）-----

    _ROLLBACK_TABLE_PK = {
        "turn_directives": "id",
        "secret_orders": "id",
        "characters": "name",
        "character_offices": "character_name",
        "consort_traits": "name",
        "conversation_goals": "id",
        "conversation_goal_events": "id",
        "minister_stances": "id",
        "xinpan_states": "character_name",
        "xinpan_logs": "id",
        "negotiation_agreements": "id",
        "negotiation_tasks": "id",
    }

    def _row_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {key: row[key] for key in row.keys()}

    def _json_dump_row(self, row: Dict[str, Any]) -> str:
        return json.dumps(row, ensure_ascii=False, sort_keys=True)

    def _json_load_row(self, raw: str) -> Dict[str, Any]:
        if not raw:
            return {}
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}

    def _table_exists(self, table: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        return row is not None

    def _snapshot_table(self, table: str, pk: str) -> Dict[str, Dict[str, Any]]:
        rows = self.conn.execute(f"SELECT * FROM {table}").fetchall()
        return {str(row[pk]): self._row_dict(row) for row in rows}

    def capture_chat_rollback_snapshot(self) -> Dict[str, Dict[str, Dict[str, Any]]]:
        """截取召对前后的可回滚业务表状态，用于撤回时做差异还原。"""
        return {
            table: self._snapshot_table(table, pk)
            for table, pk in self._ROLLBACK_TABLE_PK.items()
        }

    def create_chat_turn(
        self,
        state: GameState,
        minister_name: str,
        agno_session_id: str,
        agno_runs_before: int,
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO chat_turns
                (minister_name, turn, year, period, agno_session_id, agno_runs_before)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                minister_name,
                int(state.turn),
                int(state.year),
                int(state.period),
                agno_session_id,
                max(0, int(agno_runs_before)),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def update_chat_turn_messages(
        self,
        chat_turn_id: int,
        user_message_id: Optional[int] = None,
        minister_message_id: Optional[int] = None,
    ) -> None:
        assignments: List[str] = []
        params: List[Any] = []
        if user_message_id is not None:
            assignments.append("user_message_id = ?")
            params.append(int(user_message_id))
        if minister_message_id is not None:
            assignments.append("minister_message_id = ?")
            params.append(int(minister_message_id))
        if not assignments:
            return
        params.append(int(chat_turn_id))
        self.conn.execute(
            f"UPDATE chat_turns SET {', '.join(assignments)} WHERE id = ?",
            params,
        )
        self.conn.commit()

    def mark_chat_turn_failed(self, chat_turn_id: int) -> None:
        self.conn.execute(
            "UPDATE chat_turns SET status = 'failed' WHERE id = ? AND status = 'active'",
            (int(chat_turn_id),),
        )
        self.conn.commit()

    def abort_chat_turn(
        self,
        chat_turn_id: int,
        before_snapshot: Optional[Dict[str, Dict[str, Dict[str, Any]]]] = None,
    ) -> Dict[str, Any]:
        """Abort a failed summons cleanly.

        A failed LLM/tool run must not leave an orphan user message, partial
        Agno history, or side-effect rows that later contaminate the same NPC.
        """
        row = self.conn.execute(
            "SELECT * FROM chat_turns WHERE id = ?",
            (int(chat_turn_id),),
        ).fetchone()
        if row is None:
            return {}
        turn_row = self._row_dict(row)
        if turn_row.get("status") != "active":
            return turn_row
        if before_snapshot:
            self.record_chat_turn_rollback_diffs(
                int(chat_turn_id),
                before_snapshot,
                self.capture_chat_rollback_snapshot(),
            )
        items = self.conn.execute(
            """
            SELECT * FROM chat_turn_rollback_items
            WHERE chat_turn_id = ?
            ORDER BY id DESC
            """,
            (int(chat_turn_id),),
        ).fetchall()
        message_ids = [
            int(mid)
            for mid in (turn_row.get("user_message_id"), turn_row.get("minister_message_id"))
            if mid
        ]
        with self.conn:
            for item in items:
                table = str(item["target_table"])
                strategy = str(item["rollback_strategy"])
                target_id = str(item["target_id"])
                if strategy == "delete_inserted_row":
                    self._delete_row_in_tx(table, target_id)
                elif strategy in {"restore_row", "restore_deleted_row"}:
                    before_row = self._json_load_row(item["before_json"])
                    self._restore_row_in_tx(table, before_row)
                else:
                    raise ValueError(f"不支持的回滚策略：{strategy}")
            if message_ids:
                placeholders = ",".join("?" for _ in message_ids)
                self.conn.execute(
                    f"DELETE FROM chat_messages WHERE id IN ({placeholders})",
                    message_ids,
                )
            self.conn.execute(
                """
                UPDATE chat_turns
                SET status = 'failed'
                WHERE id = ?
                """,
                (int(chat_turn_id),),
            )
            self._truncate_agno_runs_in_tx(
                str(turn_row.get("agno_session_id") or ""),
                int(turn_row.get("agno_runs_before") or 0),
            )
        return turn_row

    def record_chat_turn_rollback_diffs(
        self,
        chat_turn_id: int,
        before: Dict[str, Dict[str, Dict[str, Any]]],
        after: Dict[str, Dict[str, Dict[str, Any]]],
    ) -> None:
        rows: List[Tuple[int, str, str, str, str, str, str]] = []
        for table, before_rows in before.items():
            after_rows = after.get(table, {})
            all_ids = set(before_rows) | set(after_rows)
            for target_id in sorted(all_ids):
                before_row = before_rows.get(target_id)
                after_row = after_rows.get(target_id)
                if before_row == after_row:
                    continue
                if before_row is None and after_row is not None:
                    strategy = "delete_inserted_row"
                elif before_row is not None and after_row is None:
                    strategy = "restore_deleted_row"
                else:
                    strategy = "restore_row"
                rows.append(
                    (
                        int(chat_turn_id),
                        table,
                        table,
                        str(target_id),
                        self._json_dump_row(before_row or {}),
                        self._json_dump_row(after_row or {}),
                        strategy,
                    )
                )
        if not rows:
            return
        self.conn.executemany(
            """
            INSERT INTO chat_turn_rollback_items
                (chat_turn_id, kind, target_table, target_id, before_json, after_json, rollback_strategy)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        self.conn.commit()

    def agno_runs_length(self, session_id: str) -> int:
        if not session_id or not self._table_exists("agno_sessions"):
            return 0
        row = self.conn.execute(
            "SELECT runs FROM agno_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            return 0
        runs, _encoded_as_string = self._decode_agno_runs(row["runs"])
        return len(runs)

    def _decode_agno_runs(self, raw: Any) -> Tuple[List[Any], bool]:
        if raw in (None, ""):
            return [], False
        try:
            decoded = json.loads(raw)
            encoded_as_string = isinstance(decoded, str)
            if encoded_as_string:
                decoded = json.loads(decoded or "[]")
            return (decoded if isinstance(decoded, list) else []), encoded_as_string
        except (TypeError, ValueError):
            return [], False

    def _encode_agno_runs(self, runs: List[Any], encoded_as_string: bool) -> str:
        if encoded_as_string:
            return json.dumps(json.dumps(runs, ensure_ascii=False), ensure_ascii=False)
        return json.dumps(runs, ensure_ascii=False)

    def _truncate_agno_runs_in_tx(self, session_id: str, keep_count: int) -> None:
        if not session_id or not self._table_exists("agno_sessions"):
            return
        row = self.conn.execute(
            "SELECT runs FROM agno_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            return
        runs, encoded_as_string = self._decode_agno_runs(row["runs"])
        kept = runs[: max(0, int(keep_count))]
        self.conn.execute(
            "UPDATE agno_sessions SET runs = ?, updated_at = strftime('%s','now') WHERE session_id = ?",
            (self._encode_agno_runs(kept, encoded_as_string), session_id),
        )

    def get_last_active_chat_turn(self, minister_name: str, turn: int) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            """
            SELECT * FROM chat_turns
            WHERE minister_name = ? AND turn = ? AND status = 'active'
            ORDER BY id DESC
            LIMIT 1
            """,
            (minister_name, int(turn)),
        ).fetchone()
        return self._row_dict(row) if row is not None else None

    def is_global_last_active_chat_turn(self, chat_turn_id: int) -> bool:
        row = self.conn.execute(
            "SELECT id FROM chat_turns WHERE status = 'active' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return bool(row and int(row["id"]) == int(chat_turn_id))

    def can_undo_last_chat_turn(self, minister_name: str, turn: int) -> bool:
        row = self.get_last_active_chat_turn(minister_name, turn)
        if row is None:
            return False
        if not row.get("user_message_id") or not row.get("minister_message_id"):
            return False
        return self.is_global_last_active_chat_turn(int(row["id"]))

    # ----- conversation goals（奏对目的 / 心理握手状态机）-----

    _GOAL_STATUSES = {"active", "waiting_conditions", "sealed", "blocked", "abandoned", "expired"}

    def _goal_json_list(self, raw: object) -> List[Dict[str, object]]:
        data: object
        if isinstance(raw, list):
            data = raw
        else:
            try:
                data = json.loads(str(raw or "[]"))
            except (TypeError, ValueError):
                data = []
        out: List[Dict[str, object]] = []
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    out.append(dict(item))
                elif str(item or "").strip():
                    out.append({"description": str(item).strip(), "status": "pending", "evidence": ""})
        return out

    def _goal_json_dict(self, raw: object) -> Dict[str, object]:
        if isinstance(raw, dict):
            return dict(raw)
        try:
            data = json.loads(str(raw or "{}"))
        except (TypeError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _serialize_goal_conditions(self, conditions: List[object] | None) -> str:
        clean: List[Dict[str, object]] = []
        for item in conditions or []:
            if isinstance(item, dict):
                desc = str(item.get("description") or item.get("text") or "").strip()
                status = str(item.get("status") or "pending").strip()
                evidence = str(item.get("evidence") or "").strip()
            else:
                desc = str(item or "").strip()
                status = "pending"
                evidence = ""
            if not desc:
                continue
            if status not in {"pending", "done", "failed"}:
                status = "pending"
            clean.append({"description": desc[:180], "status": status, "evidence": evidence[:240]})
        return json.dumps(clean[:8], ensure_ascii=False)

    def _serialize_goal_blockers(self, blockers: List[object] | None) -> str:
        clean = [str(item or "").strip()[:160] for item in blockers or [] if str(item or "").strip()]
        return json.dumps(clean[:8], ensure_ascii=False)

    def create_conversation_goal(
        self,
        state: GameState,
        *,
        minister_name: str,
        action_kind: str,
        title: str,
        target_text: str,
        threshold: int,
        score: int = 0,
        status: str = "active",
        condition_status: str = "none",
        conditions: List[object] | None = None,
        blockers: List[object] | None = None,
        related_issue_id: int = 0,
        source_chat_turn_id: int = 0,
        expires_turn: int = 0,
        last_delta: Dict[str, object] | None = None,
    ) -> int:
        clean_name = str(minister_name or "").strip()
        if not clean_name:
            return 0
        status = str(status or "active").strip()
        if status not in self._GOAL_STATUSES:
            status = "active"
        cur = self.conn.execute(
            """
            INSERT INTO conversation_goals
                (minister_name, action_kind, title, target_text, status, score, threshold,
                 condition_status, conditions_json, blockers_json, related_issue_id,
                 source_chat_turn_id, last_delta_json, created_turn, expires_turn)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                clean_name,
                str(action_kind or "general").strip()[:40],
                str(title or "").strip()[:120],
                str(target_text or title or "").strip()[:240],
                status,
                max(0, min(100, int(score or 0))),
                max(0, min(100, int(threshold or 0))),
                str(condition_status or "none").strip()[:40],
                self._serialize_goal_conditions(conditions),
                self._serialize_goal_blockers(blockers),
                max(0, int(related_issue_id or 0)),
                max(0, int(source_chat_turn_id or 0)),
                json.dumps(last_delta or {}, ensure_ascii=False),
                int(state.turn),
                max(0, int(expires_turn or 0)),
            ),
        )
        goal_id = int(cur.lastrowid)
        self.add_conversation_goal_event(
            state,
            goal_id,
            "created",
            status=status,
            score_delta=max(0, min(100, int(score or 0))),
            score_after=max(0, min(100, int(score or 0))),
            summary=str(title or target_text or "新建奏对目的")[:180],
            payload=last_delta or {},
            source_chat_turn_id=source_chat_turn_id,
            commit=False,
        )
        self.conn.commit()
        return goal_id

    def add_conversation_goal_event(
        self,
        state: GameState,
        goal_id: int,
        event_kind: str,
        *,
        status: str = "",
        score_delta: int = 0,
        score_after: int = 0,
        summary: str = "",
        payload: Dict[str, object] | None = None,
        source_chat_turn_id: int = 0,
        commit: bool = True,
    ) -> int:
        goal_id = int(goal_id or 0)
        if goal_id <= 0:
            return 0
        row = self.conn.execute(
            "SELECT minister_name, status, score FROM conversation_goals WHERE id=?",
            (goal_id,),
        ).fetchone()
        minister = str(row["minister_name"] or "") if row else ""
        event_status = str(status or (row["status"] if row else "") or "")
        score_after = int(score_after if score_after is not None else (row["score"] if row else 0))
        cur = self.conn.execute(
            """
            INSERT INTO conversation_goal_events
                (goal_id, turn, year, period, minister_name, event_kind, status,
                 score_delta, score_after, summary, payload_json, source_chat_turn_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                goal_id,
                int(state.turn), int(state.year), int(state.period),
                minister,
                str(event_kind or "note").strip()[:40],
                event_status[:40],
                int(score_delta or 0),
                max(0, min(100, int(score_after or 0))),
                str(summary or "").strip()[:240],
                json.dumps(payload or {}, ensure_ascii=False),
                max(0, int(source_chat_turn_id or 0)),
            ),
        )
        if commit:
            self.conn.commit()
        return int(cur.lastrowid)

    def update_conversation_goal(
        self,
        goal_id: int,
        *,
        state: Optional[GameState] = None,
        event_kind: str = "",
        event_summary: str = "",
        source_chat_turn_id: int = 0,
        **fields: object,
    ) -> None:
        goal_id = int(goal_id or 0)
        if goal_id <= 0:
            return
        assignments: List[str] = []
        params: List[Any] = []
        allowed = {
            "action_kind", "title", "target_text", "status", "score", "threshold",
            "condition_status", "conditions_json", "blockers_json", "related_issue_id",
            "agreement_id", "source_chat_turn_id", "last_delta_json", "expires_turn",
            "abandoned_reason",
        }
        old = self.conn.execute("SELECT score, status FROM conversation_goals WHERE id=?", (goal_id,)).fetchone()
        old_score = int(old["score"] or 0) if old else 0
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key == "status":
                value = str(value or "active").strip()
                if value not in self._GOAL_STATUSES:
                    value = "active"
            elif key in {"score", "threshold"}:
                value = max(0, min(100, int(value or 0)))
            elif key in {"related_issue_id", "agreement_id", "source_chat_turn_id", "expires_turn"}:
                value = max(0, int(value or 0))
            elif key in {"conditions_json", "blockers_json", "last_delta_json"} and not isinstance(value, str):
                value = json.dumps(value or ([] if key != "last_delta_json" else {}), ensure_ascii=False)
            else:
                value = str(value or "")
            assignments.append(f"{key}=?")
            params.append(value)
        if not assignments:
            return
        assignments.append("updated_at=CURRENT_TIMESTAMP")
        params.append(goal_id)
        self.conn.execute(
            f"UPDATE conversation_goals SET {', '.join(assignments)} WHERE id=?",
            params,
        )
        if state is not None and event_kind:
            new_score = int(fields.get("score", old_score) or 0)
            self.add_conversation_goal_event(
                state,
                goal_id,
                event_kind,
                status=str(fields.get("status") or (old["status"] if old else "") or ""),
                score_delta=new_score - old_score,
                score_after=new_score,
                summary=event_summary,
                payload=self._goal_json_dict(fields.get("last_delta_json", {})),
                source_chat_turn_id=source_chat_turn_id,
                commit=False,
            )
        self.conn.commit()

    def get_conversation_goal(self, goal_id: int) -> Optional[Dict[str, object]]:
        row = self.conn.execute("SELECT * FROM conversation_goals WHERE id=?", (int(goal_id),)).fetchone()
        return self._parse_conversation_goal(row) if row is not None else None

    def _parse_conversation_goal(self, row: sqlite3.Row | Dict[str, object]) -> Dict[str, object]:
        item = dict(row)
        item["conditions"] = self._goal_json_list(item.get("conditions_json"))
        try:
            raw_blockers = json.loads(str(item.get("blockers_json") or "[]"))
        except (TypeError, ValueError):
            raw_blockers = []
        if isinstance(raw_blockers, list):
            item["blockers"] = [str(x) for x in raw_blockers if str(x).strip()]
        else:
            item["blockers"] = []
        item["last_delta"] = self._goal_json_dict(item.get("last_delta_json"))
        return item

    def get_active_conversation_goal(self, minister_name: str) -> Optional[Dict[str, object]]:
        row = self.conn.execute(
            """
            SELECT * FROM conversation_goals
            WHERE minister_name=? AND status IN ('active', 'waiting_conditions')
            ORDER BY id DESC
            LIMIT 1
            """,
            (str(minister_name or "").strip(),),
        ).fetchone()
        return self._parse_conversation_goal(row) if row is not None else None

    def list_conversation_goals(
        self,
        minister_name: str = "",
        *,
        statuses: Optional[List[str]] = None,
        limit: int = 80,
    ) -> List[Dict[str, object]]:
        clauses: List[str] = []
        params: List[Any] = []
        if minister_name:
            clauses.append("minister_name=?")
            params.append(str(minister_name).strip())
        if statuses:
            clean_statuses = [str(status).strip() for status in statuses if str(status).strip()]
            if clean_statuses:
                clauses.append("status IN (" + ",".join("?" for _ in clean_statuses) + ")")
                params.extend(clean_statuses)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(max(1, min(200, int(limit or 80))))
        rows = self.conn.execute(
            f"""
            SELECT * FROM conversation_goals
            {where}
            ORDER BY id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [self._parse_conversation_goal(row) for row in rows]

    def abandon_conversation_goal(
        self,
        state: GameState,
        goal_id: int,
        *,
        reason: str = "",
        source_chat_turn_id: int = 0,
    ) -> Dict[str, object]:
        goal = self.get_conversation_goal(goal_id)
        if not goal:
            raise ValueError("奏对目的不存在。")
        if str(goal.get("status") or "") == "sealed" or int(goal.get("agreement_id") or 0):
            raise ValueError("已握手入账的奏对目的不可放弃，只能由履约账本裁断。")
        if str(goal.get("status") or "") not in {"active", "waiting_conditions", "blocked", "expired"}:
            raise ValueError("该奏对目的当前不可放弃。")
        self.update_conversation_goal(
            int(goal_id),
            state=state,
            event_kind="abandoned",
            event_summary=str(reason or "玩家主动放弃奏对目的。")[:180],
            source_chat_turn_id=source_chat_turn_id,
            status="abandoned",
            abandoned_reason=str(reason or "玩家主动放弃")[:180],
            last_delta_json={"reason": str(reason or "玩家主动放弃")[:180]},
        )
        updated = self.get_conversation_goal(goal_id)
        return updated or goal

    def bind_conversation_goal_agreement(self, goal_id: int, agreement_id: int) -> None:
        if not goal_id or not agreement_id:
            return
        self.conn.execute(
            """
            UPDATE conversation_goals
            SET agreement_id=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (max(0, int(agreement_id or 0)), int(goal_id)),
        )
        self.conn.execute(
            """
            UPDATE negotiation_agreements
            SET goal_id=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (int(goal_id), int(agreement_id)),
        )
        self.conn.commit()

    def record_minister_stance(
        self,
        state: GameState,
        minister_name: str,
        topic: str,
        stance: str,
        confidence: int = 3,
        summary: str = "",
        conditions: str = "",
        related_issue_id: int = 0,
        source_chat_turn_id: int = 0,
        user_message: str = "",
        minister_answer: str = "",
        evidence: Dict[str, object] | None = None,
        risk_tags: List[str] | None = None,
        execution_hint: str = "",
        handshake_status: str = "none",
        psychological_score: int = 0,
        psychological: Dict[str, object] | None = None,
        agreement_id: int = 0,
        goal_id: int = 0,
    ) -> int:
        """记录本回合召对后，某官对某事的真实立场/承诺，供月末推演读取。"""
        minister_name = str(minister_name or "").strip()
        topic = str(topic or "").strip()[:80]
        stance = str(stance or "neutral").strip()
        if stance not in {"support", "oppose", "caution", "neutral"}:
            stance = "neutral"
        handshake_status = str(handshake_status or "none").strip()
        if handshake_status not in {"sealed", "conditional", "blocked", "none"}:
            handshake_status = "none"
        if not minister_name or not topic:
            return 0
        cur = self.conn.execute(
            """
            INSERT INTO minister_stances
                (turn, year, period, minister_name, topic, stance, confidence, summary,
                 conditions, related_issue_id, source_chat_turn_id, user_message, minister_answer,
                 evidence_json, risk_tags, execution_hint, handshake_status,
                 psychological_score, psychological_json, agreement_id, goal_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(state.turn), int(state.year), int(state.period),
                minister_name, topic, stance, max(1, min(5, int(confidence or 3))),
                str(summary or "").strip()[:240],
                str(conditions or "").strip()[:240],
                max(0, int(related_issue_id or 0)),
                max(0, int(source_chat_turn_id or 0)),
                str(user_message or "").strip()[:400],
                str(minister_answer or "").strip()[:600],
                json.dumps(evidence or {}, ensure_ascii=False),
                "、".join(str(tag).strip() for tag in (risk_tags or []) if str(tag).strip())[:160],
                str(execution_hint or "").strip()[:180],
                handshake_status,
                max(0, min(100, int(psychological_score or 0))),
                json.dumps(psychological or {}, ensure_ascii=False),
                max(0, int(agreement_id or 0)),
                max(0, int(goal_id or 0)),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def update_minister_stance_agreement(self, stance_id: int, agreement_id: int) -> None:
        if not stance_id:
            return
        self.conn.execute(
            """
            UPDATE minister_stances
            SET agreement_id = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (max(0, int(agreement_id or 0)), int(stance_id)),
        )
        self.conn.commit()

    def list_minister_stances(
        self,
        turn: Optional[int] = None,
        minister_name: str = "",
        limit: int = 40,
    ) -> List[Dict[str, object]]:
        where: List[str] = []
        params: List[Any] = []
        if turn is not None:
            where.append("turn = ?")
            params.append(int(turn))
        if minister_name:
            where.append("minister_name = ?")
            params.append(str(minister_name))
        sql_where = ("WHERE " + " AND ".join(where)) if where else ""
        params.append(max(1, min(200, int(limit or 40))))
        rows = self.conn.execute(
            f"""
            SELECT id, turn, year, period, minister_name, topic, stance, confidence,
                   summary, conditions, related_issue_id, source_chat_turn_id,
                   user_message, minister_answer, evidence_json, risk_tags, execution_hint, handshake_status,
                   psychological_score, psychological_json, agreement_id, goal_id
            FROM minister_stances
            {sql_where}
            ORDER BY id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        parsed: List[Dict[str, object]] = []
        for row in rows:
            item = dict(row)
            try:
                evidence = json.loads(str(item.get("evidence_json") or "{}"))
            except (TypeError, ValueError):
                evidence = {}
            item["evidence"] = evidence if isinstance(evidence, dict) else {}
            try:
                psychological = json.loads(str(item.get("psychological_json") or "{}"))
            except (TypeError, ValueError):
                psychological = {}
            item["psychological"] = psychological if isinstance(psychological, dict) else {}
            raw_tags = str(item.get("risk_tags") or "")
            item["risk_tags_list"] = [part for part in re.split(r"[、,，;；\s]+", raw_tags) if part]
            parsed.append(item)
        return parsed

    # ----- legacy xinpan compatibility no-ops -----
    #
    # NPC behavior is now driven by personality, relationship, memory, and the
    # negotiation agreement ledger.  These methods remain only so older call
    # sites or debug tools do not crash; they must not create/update xinpan data.

    def ensure_xinpan_states(self, state: Optional[GameState] = None) -> int:
        _ = state
        return 0

    def get_xinpan_profile(self, name: str, state: Optional[GameState] = None) -> Dict[str, object]:
        _ = (name, state)
        return {}

    def xinpan_agent_brief(self, name: str, state: Optional[GameState] = None) -> str:
        _ = (name, state)
        return ""

    def xinpan_simulator_rows(self, state: Optional[GameState] = None, limit: int = 80) -> List[Dict[str, object]]:
        _ = (state, limit)
        return []

    def apply_chat_xinpan_update(
        self,
        state: GameState,
        minister_name: str,
        user_text: str,
        answer: str,
        *,
        stance: str = "neutral",
        handshake_status: str = "none",
        psychological_score: int = 0,
        source_chat_turn_id: int = 0,
        goal_context: Optional[Dict[str, object]] = None,
        xinpan_delta: Optional[Dict[str, object]] = None,
    ) -> Optional[Dict[str, object]]:
        _ = (
            state,
            minister_name,
            user_text,
            answer,
            stance,
            handshake_status,
            psychological_score,
            source_chat_turn_id,
            goal_context,
            xinpan_delta,
        )
        return None

    def apply_turn_xinpan_update(
        self,
        state: GameState,
        decree_text: str,
        narrative: str,
        applied: Dict[str, object],
    ) -> Dict[str, object]:
        _ = (state, decree_text, narrative, applied)
        return {}

    def apply_direct_xinpan_adjustment(
        self,
        state: GameState,
        name: str,
        *,
        shi_delta: float = 0.0,
        fear_delta: float = 0.0,
        hatred_delta: float = 0.0,
        trust_multiplier: float = 1.0,
        event: str = "直接处置更新心盘",
        source_kind: str = "direct",
        source_id: str = "",
    ) -> Optional[Dict[str, object]]:
        _ = (
            state,
            name,
            shi_delta,
            fear_delta,
            hatred_delta,
            trust_multiplier,
            event,
            source_kind,
            source_id,
        )
        return None

    # ----- negotiation agreements（奏对协议 / 履约系统）-----

    def create_negotiation_agreement(
        self,
        state: GameState,
        *,
        minister_name: str,
        topic: str,
        action_kind: str,
        status: str,
        stance_id: int,
        handshake_status: str,
        psychological_score: int,
        threshold: int,
        verbal_only: bool,
        core_topic: str = "",
        target_text: str = "",
        promise_type: str = "",
        stakes: str = "",
        due_turn: int = 0,
        conditions: str = "",
        summary: str = "",
        tasks: List[str] | None = None,
        goal_id: int = 0,
    ) -> int:
        clean_name = str(minister_name or "").strip()
        if not clean_name:
            return 0
        status = str(status or "pending").strip()
        if status not in {"sealed", "pending", "blocked", "fulfilled", "failed"}:
            status = "pending"
        task_list = [str(task or "").strip() for task in (tasks or []) if str(task or "").strip()]
        if status == "blocked":
            condition_status = "failed"
            target_status = "blocked"
            fulfillment_score = 0
            fulfillment_evidence = ""
            target_evidence = "奏对未说服，标的未达成。"
        elif task_list:
            status = "pending"
            condition_status = "pending"
            target_status = "pending_conditions"
            fulfillment_score = 0
            fulfillment_evidence = ""
            target_evidence = "条件未全部满足，标的暂未达成。"
        else:
            status = "fulfilled" if status == "sealed" else status
            condition_status = "satisfied" if status in {"fulfilled", "sealed"} else "pending"
            target_status = "achieved" if status in {"fulfilled", "sealed"} else "pending_conditions"
            fulfillment_score = 100 if status in {"fulfilled", "sealed"} else 0
            fulfillment_evidence = "无待办条件，已形成即时政治承诺。" if status in {"fulfilled", "sealed"} else ""
            target_evidence = "条件已满足，标的即时达成。" if status in {"fulfilled", "sealed"} else ""
        cur = self.conn.execute(
            """
            INSERT INTO negotiation_agreements
                (turn_created, year_created, period_created, minister_name, topic,
                 core_topic, target_text, action_kind, promise_type, stakes, status,
                 condition_status, target_status, stance_id, goal_id, handshake_status,
                 psychological_score, threshold, verbal_only, due_turn,
                 fulfillment_score, fulfillment_evidence, target_evidence, conditions, summary)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(state.turn), int(state.year), int(state.period),
                clean_name, str(topic or "").strip()[:120],
                str(core_topic or topic or "").strip()[:120],
                str(target_text or core_topic or topic or "").strip()[:180],
                str(action_kind or "general").strip()[:40],
                str(promise_type or "").strip()[:40],
                str(stakes or "").strip()[:120],
                status, condition_status, target_status,
                max(0, int(stance_id or 0)),
                max(0, int(goal_id or 0)),
                str(handshake_status or "none").strip(),
                max(0, min(100, int(psychological_score or 0))),
                max(0, min(100, int(threshold or 0))),
                1 if verbal_only else 0,
                max(0, int(due_turn or 0)),
                fulfillment_score,
                fulfillment_evidence,
                target_evidence,
                str(conditions or "").strip()[:400],
                str(summary or "").strip()[:300],
            ),
        )
        agreement_id = int(cur.lastrowid)
        for desc in task_list:
            self.conn.execute(
                """
                INSERT INTO negotiation_tasks (agreement_id, description, task_kind, status)
                VALUES (?, ?, ?, 'pending')
                """,
                (agreement_id, desc[:180], classify_task_kind(desc)),
            )
        self.conn.commit()
        return agreement_id

    def update_negotiation_task(self, task_id: int, status: str, evidence: str = "") -> None:
        status = str(status or "pending").strip()
        if status not in {"pending", "done", "failed"}:
            status = "pending"
        self.conn.execute(
            """
            UPDATE negotiation_tasks
            SET status=?, evidence=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (status, str(evidence or "").strip()[:240], int(task_id)),
        )
        row = self.conn.execute(
            "SELECT agreement_id FROM negotiation_tasks WHERE id=?", (int(task_id),)
        ).fetchone()
        if row is not None:
            self._refresh_negotiation_agreement_status(int(row["agreement_id"]))
        self.conn.commit()

    def _refresh_negotiation_agreement_status(self, agreement_id: int) -> None:
        agreement = self.conn.execute(
            "SELECT status FROM negotiation_agreements WHERE id=?", (int(agreement_id),)
        ).fetchone()
        if agreement is None:
            return
        rows = self.conn.execute(
            "SELECT status FROM negotiation_tasks WHERE agreement_id=?",
            (int(agreement_id),),
        ).fetchall()
        if not rows:
            return
        statuses = [str(row["status"] or "pending") for row in rows]
        if any(status == "failed" for status in statuses):
            next_status = "failed"
        elif all(status == "done" for status in statuses):
            next_status = "fulfilled"
        else:
            next_status = "pending"
        self.conn.execute(
            "UPDATE negotiation_agreements SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (next_status, int(agreement_id)),
        )

    def _agreement_review_context(
        self,
        *,
        decree_text: str = "",
        narrative: str = "",
        directives: Optional[List[Any]] = None,
        applied: Optional[Dict[str, object]] = None,
    ) -> str:
        parts: List[str] = []
        if decree_text:
            parts.append(f"诏书：{decree_text[:4000]}")
        for row in directives or []:
            try:
                actor = str(row["actor"] or "")
                title = str(row["event_title"] or "")
                text = str(row["text"] or "")
            except Exception:
                actor = str(getattr(row, "actor", "") or "")
                title = str(getattr(row, "event_title", "") or "")
                text = str(getattr(row, "text", "") or "")
            if actor or title or text:
                parts.append(f"草案：{actor} {title} {text[:600]}")
        if narrative:
            parts.append(f"邸报：{narrative[:5000]}")
        if applied:
            try:
                parts.append("落库：" + json.dumps(applied, ensure_ascii=False)[:2400])
            except Exception:
                pass
        return "\n".join(parts)

    def _agreement_keywords(self, agreement: Dict[str, object]) -> List[str]:
        base = " ".join(
            str(agreement.get(key) or "")
            for key in ("minister_name", "topic", "core_topic", "target_text", "conditions", "summary", "stakes")
        )
        stop = {
            "本次", "奏对", "事项", "条件", "顾虑", "奏对量表", "握手成功", "附条件",
            "未说服", "未成约", "臣愿", "陛下", "皇帝", "大明", "此事", "本回合",
        }
        words: List[str] = []
        for term in (
            "辽饷", "辽东", "关宁", "山海关", "陕西", "赈灾", "流寇", "户部", "太仓",
            "国库", "内库", "阉党", "东厂", "锦衣卫", "司礼监", "东林", "清流",
            "廷议", "密旨", "密令", "净身", "民籍", "奴籍", "清丈", "商税", "盐课",
        ):
            if term in base and term not in words:
                words.append(term)
        for word in re.findall(r"[\u4e00-\u9fff]{2,12}", base):
            if word in stop:
                continue
            if re.fullmatch(r"(银两|人手|名分|期限|派系|地方|军务|保密|条件|顾虑)", word):
                continue
            if word not in words:
                words.append(word)
        return words[:10]

    def _agreement_relevant_in_context(self, agreement: Dict[str, object], context: str) -> bool:
        if not context:
            return False
        minister = str(agreement.get("minister_name") or "").strip()
        if minister and minister in context:
            return True
        core = str(agreement.get("core_topic") or agreement.get("topic") or "").strip()
        if core and core in context:
            return True
        hits = 0
        for word in self._agreement_keywords(agreement):
            if word and word in context:
                hits += 1
            if hits >= 2:
                return True
        action_kind = str(agreement.get("action_kind") or "")
        if action_kind == "castration" and re.search(r"净身|入内廷|司礼监|太监|宦官", context):
            return True
        if action_kind == "emancipation" and re.search(r"奴籍|民籍|脱籍|还民|出宫为民", context):
            return True
        if action_kind == "court_commitment" and re.search(
            r"劝|说服|游说|调停|转圜|斡旋|背书|代奏|联络|试探|探口风|保密|守口|不泄|承办|协办|办成|奉旨|照办",
            context,
        ):
            return True
        return False

    def _task_relevant_in_context(self, description: str, context: str) -> bool:
        if not description or not context:
            return False
        for term in (
            "辽饷", "辽东", "关宁", "山海关", "陕西", "赈灾", "流寇", "户部", "太仓",
            "国库", "内库", "家眷", "安置", "抚恤", "廷议", "会审", "明旨", "密旨",
            "密令", "净身", "民籍", "奴籍", "人手", "胥吏", "粮", "银", "饷",
        ):
            if term in description and term in context:
                return True
        return False

    def _task_auto_decision(
        self,
        agreement: Dict[str, object],
        task: Dict[str, object],
        context: str,
        *,
        state: GameState,
        phase: str,
    ) -> tuple[str, str]:
        current = str(task.get("status") or "pending")
        if current in {"done", "failed"}:
            return current, str(task.get("evidence") or "")
        if not context:
            return current, str(task.get("evidence") or "")

        desc = str(task.get("description") or "")
        relevant = self._agreement_relevant_in_context(agreement, context) or self._task_relevant_in_context(desc, context)
        kind = str(task.get("task_kind") or "")
        if not kind or kind == "general":
            kind = classify_task_kind(desc)
        combined = f"{desc}\n{context}"
        contradiction = (
            relevant
            and re.search(
                r"未准|驳回|搁置|不予|不许|未拨|无银可拨|未给|未设|未议|未下|未见|"
                r"无.{0,8}(安置|保全|抚恤|明旨|廷议|拨|给|人手|银|粮|饷)|"
                r"食言|失信|背约|不兑现|作罢|强旨|强行|勒令",
                combined,
            )
        )
        if contradiction:
            return "failed", "自动判定：诏书或邸报出现未兑现/强推/驳回等相反证据。"

        done_patterns = {
            "resource": r"(拨|发|支|给|赏|赐|解|筹|调|运|采买|平粜).{0,18}(银|钱|饷|粮|米|经费|内库|国库|太仓)|(银|钱|饷|粮|米).{0,18}(拨|发|给|解|支|赏|赐)",
            "staff": r"(添|派|拨|调|给|差).{0,18}(人手|胥吏|差役|属官|书吏|匠|兵|校尉|人)",
            "legitimacy": r"明旨|圣旨|诏|廷议|会审|议覆|部议|章程|条议|成例|定例|专责|授权|交.{0,12}办理|会同",
            "protection": r"保全|安置|抚恤|不辱|体面|遮护|家眷|家小|族人|免罪|免坐",
            "office": r"(任|授|补|擢|升|调|加|赏|赐).{0,18}(官|职|衔|缺|银|蟒|服)|职掌|边界|专责",
            "deadline": r"(限|准|许|给|赐|宽).{0,12}(日|旬|月|年)|[一二三四五六七八九十百\d]+.{0,4}(日|旬|月|年)(内|后|间)?|月内|旬日|刻期|缓办|展限",
            "secrecy": r"密旨|密令|暗查|密查|秘|不得泄|封口|耳目|线人|取证",
            "general": r"准|照办|奉旨|已办|成议|允行|照准|如议",
        }
        pattern = done_patterns.get(kind, done_patterns["general"])
        if relevant and re.search(pattern, combined):
            return "done", f"自动判定：发现与「{desc[:48]}」相符的{kind}类履约证据。"

        due_turn = int(agreement.get("due_turn") or 0)
        if phase == "postresolve" and due_turn and int(state.turn) >= due_turn:
            return "failed", "自动判定：本回合诏书、邸报与落库记录未见条件兑现，承诺逾期失信。"
        return current, str(task.get("evidence") or "")

    def _normalize_agreement_reviews(
        self,
        reviews: Optional[object],
    ) -> Dict[int, Dict[str, object]]:
        if not reviews:
            return {}
        if isinstance(reviews, dict):
            raw_items = reviews.get("reviews") if isinstance(reviews.get("reviews"), list) else list(reviews.values())
        elif isinstance(reviews, list):
            raw_items = reviews
        else:
            return {}
        out: Dict[int, Dict[str, object]] = {}
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            try:
                agreement_id = int(item.get("agreement_id") or item.get("id") or 0)
            except (TypeError, ValueError):
                agreement_id = 0
            if agreement_id:
                out[agreement_id] = item
        return out

    def _llm_task_review(
        self,
        llm_review: Dict[str, object],
        task: Dict[str, object],
    ) -> Optional[tuple[str, str]]:
        task_reviews = llm_review.get("task_reviews")
        if not isinstance(task_reviews, list):
            return None
        desc = str(task.get("description") or "")
        task_id = int(task.get("id") or 0)
        for item in task_reviews:
            if not isinstance(item, dict):
                continue
            try:
                reviewed_task_id = int(item.get("task_id") or item.get("id") or 0)
            except (TypeError, ValueError):
                reviewed_task_id = 0
            same_id = bool(task_id and reviewed_task_id == task_id)
            same_desc = desc and str(item.get("description") or "").strip() == desc
            if not (same_id or same_desc):
                continue
            status = str(item.get("status") or "").strip()
            if status not in {"pending", "done", "failed"}:
                condition_status = str(item.get("condition_status") or "").strip()
                status = {"satisfied": "done", "failed": "failed", "pending": "pending"}.get(condition_status, "")
            if status in {"pending", "done", "failed"}:
                evidence = str(item.get("evidence") or item.get("reason") or "LLM 判定。").strip()
                return status, f"LLM判定：{evidence}"[:240]
        return None

    def _condition_target_statuses(
        self,
        agreement: Dict[str, object],
        tasks: List[Dict[str, object]],
        llm_review: Dict[str, object],
        *,
        phase: str,
        state: GameState,
        allow_due_fallback: bool = False,
    ) -> tuple[str, str, int, str, str]:
        old_target = str(agreement.get("target_status") or "")
        if old_target == "blocked":
            return "failed", "blocked", 0, str(agreement.get("fulfillment_evidence") or ""), str(agreement.get("target_evidence") or "")

        statuses = [str(task.get("status") or "pending") for task in tasks]
        llm_condition = str(llm_review.get("condition_status") or "").strip()
        llm_reason = str(llm_review.get("condition_evidence") or llm_review.get("reason") or "").strip()

        if tasks:
            if llm_condition == "satisfied" and not any(status == "failed" for status in statuses):
                for task in tasks:
                    if task.get("status") == "pending":
                        task["status"] = "done"
                        task["evidence"] = f"LLM判定：{llm_reason or '全部条件已有足够证据。'}"[:240]
                statuses = [str(task.get("status") or "pending") for task in tasks]
            elif llm_condition == "failed":
                for task in tasks:
                    if task.get("status") == "pending":
                        task["status"] = "failed"
                        task["evidence"] = f"LLM判定：{llm_reason or '关键条件未兑现或已被否定。'}"[:240]
                statuses = [str(task.get("status") or "pending") for task in tasks]

        if not tasks:
            condition_status = "satisfied"
            condition_score = 100
            condition_evidence = str(agreement.get("fulfillment_evidence") or "无待办条件，条件视为满足。")
        elif any(status == "failed" for status in statuses):
            condition_status = "failed"
            condition_score = 0
            condition_evidence = next(
                (str(task.get("evidence") or "") for task in tasks if task.get("status") == "failed" and task.get("evidence")),
                "存在条件未兑现或被否定。",
            )
        elif all(status == "done" for status in statuses):
            condition_status = "satisfied"
            condition_score = 100
            condition_evidence = "全部履约条件均已有显式证据。"
        else:
            condition_status = "pending"
            condition_score = round(100 * statuses.count("done") / max(1, len(statuses)))
            condition_evidence = "仍有条件未见显式证据。"

        if condition_status == "satisfied":
            llm_target = str(llm_review.get("target_status") or "").strip()
            if llm_target in {"failed", "blocked"}:
                target_status = llm_target
            else:
                target_status = "achieved"
            target_evidence = str(
                llm_review.get("target_evidence")
                or llm_review.get("reason")
                or "条件已全部满足，标的达成。"
            ).strip()
        elif condition_status == "failed":
            target_status = "failed"
            target_evidence = "条件未满足或已失败，标的未达成。"
        else:
            due_turn = int(agreement.get("due_turn") or 0)
            if allow_due_fallback and phase == "postresolve" and due_turn and int(state.turn) >= due_turn:
                condition_status = "failed"
                condition_score = 0
                condition_evidence = "本回合已到期，仍未见全部条件兑现。"
                target_status = "failed"
                target_evidence = "条件逾期未满足，标的未达成。"
            else:
                target_status = "pending_conditions"
                target_evidence = "条件未全部满足，标的暂未达成。"

        return condition_status, target_status, condition_score, condition_evidence[:240], target_evidence[:240]

    def _agreement_status_from_target(self, target_status: str) -> str:
        if target_status == "achieved":
            return "fulfilled"
        if target_status == "blocked":
            return "blocked"
        if target_status == "failed":
            return "failed"
        return "pending"

    def _apply_negotiation_political_effect(
        self,
        state: GameState,
        agreement: Dict[str, object],
        *,
        new_status: str,
        evidence: str,
    ) -> Dict[str, object]:
        try:
            old_effect = json.loads(str(agreement.get("political_effect_json") or "{}"))
        except (TypeError, ValueError):
            old_effect = {}
        if isinstance(old_effect, dict) and old_effect.get("applied_status") == new_status:
            return old_effect

        minister = str(agreement.get("minister_name") or "")
        action_kind = str(agreement.get("action_kind") or "general")
        stakes = str(agreement.get("stakes") or "")
        row = self.conn.execute(
            "SELECT faction FROM characters WHERE name=?",
            (minister,),
        ).fetchone()
        faction = str(row["faction"] or "") if row else ""
        effect: Dict[str, object] = {
            "applied_turn": int(state.turn),
            "applied_status": new_status,
            "evidence": evidence[:180],
            "metric_delta": {},
            "faction_delta": {},
            "npc_influence": {},
        }
        if new_status == "fulfilled":
            wei_delta = 1 if action_kind in {"policy", "personnel", "secret_order", "castration", "emancipation", "court_commitment"} else 0
            if wei_delta:
                state.metrics["皇威"] = max(0, min(100, int(state.metrics.get("皇威", 0)) + wei_delta))
                effect["metric_delta"] = {"皇威": wei_delta}
            if faction:
                self.adjust_factions({faction: {"satisfaction": 2}})
                effect["faction_delta"] = {faction: {"satisfaction": 2}}
            effect["npc_influence"] = {
                "memory_signal": "agreement_fulfilled",
                "expected_behavior": "后续召对可把此事当作皇帝守约与本人履约资本；同党可据此背书，政敌也可质疑邀功。",
            }
            self.record_log(state, f"奏对标的达成：{minister}「{agreement.get('target_text') or agreement.get('core_topic') or agreement.get('topic')}」。")
        elif new_status == "failed":
            wei_delta = -2 if re.search(r"身家名节|制度名分|军国成败", stakes) else -1
            state.metrics["皇威"] = max(0, min(100, int(state.metrics.get("皇威", 0)) + wei_delta))
            effect["metric_delta"] = {"皇威": wei_delta}
            if faction:
                self.adjust_factions({faction: {"satisfaction": -4}})
                effect["faction_delta"] = {faction: {"satisfaction": -4}}
            effect["npc_influence"] = {
                "memory_signal": "agreement_failed",
                "expected_behavior": "后续召对须把此事当作失信旧账；本人可能补奏、拖延、求保全，相关党争关系会借题清算。",
            }
            self.record_log(state, f"奏对标的失信：{minister}「{agreement.get('target_text') or agreement.get('core_topic') or agreement.get('topic')}」未达成。")
        self.save_state(state)
        return effect

    def auto_review_negotiation_agreements(
        self,
        state: GameState,
        *,
        decree_text: str = "",
        narrative: str = "",
        directives: Optional[List[Any]] = None,
        applied: Optional[Dict[str, object]] = None,
        llm_reviews: Optional[object] = None,
        phase: str = "preresolve",
        limit: int = 80,
    ) -> List[Dict[str, object]]:
        """Automatically judge negotiation fulfillment from explicit game evidence.

        This replaces the old "player clicks done" loop. The review reads official
        texts and structured results; unresolved conditional promises become court
        debt, fulfilled promises become political capital, and breaches damage trust.
        """
        context = self._agreement_review_context(
            decree_text=decree_text,
            narrative=narrative,
            directives=directives,
            applied=applied,
        )
        llm_review_by_id = self._normalize_agreement_reviews(llm_reviews)
        rows = self.conn.execute(
            """
            SELECT * FROM negotiation_agreements
            WHERE status IN ('pending', 'sealed')
            ORDER BY id DESC
            LIMIT ?
            """,
            (max(1, min(200, int(limit or 80))),),
        ).fetchall()
        reviewed: List[Dict[str, object]] = []
        for row in rows:
            agreement = dict(row)
            llm_review = llm_review_by_id.get(int(agreement["id"]), {})
            if not llm_review:
                continue
            tasks = [
                dict(task)
                for task in self.conn.execute(
                    "SELECT * FROM negotiation_tasks WHERE agreement_id=? ORDER BY id",
                    (int(agreement["id"]),),
                ).fetchall()
            ]
            changed_tasks = False
            for task in tasks:
                llm_task = self._llm_task_review(llm_review, task) if isinstance(llm_review, dict) else None
                if llm_task is not None:
                    next_status, evidence = llm_task
                else:
                    next_status, evidence = str(task.get("status") or "pending"), str(task.get("evidence") or "")
                if next_status != str(task.get("status") or "pending") or evidence != str(task.get("evidence") or ""):
                    self.conn.execute(
                        """
                        UPDATE negotiation_tasks
                        SET status=?, evidence=?, last_checked_turn=?, updated_at=CURRENT_TIMESTAMP
                        WHERE id=?
                        """,
                        (next_status, evidence[:240], int(state.turn), int(task["id"])),
                    )
                    task["status"] = next_status
                    task["evidence"] = evidence
                    changed_tasks = True
                elif phase == "postresolve":
                    self.conn.execute(
                        "UPDATE negotiation_tasks SET last_checked_turn=? WHERE id=?",
                        (int(state.turn), int(task["id"])),
                    )
            condition_status, target_status, score, evidence, target_evidence = self._condition_target_statuses(
                agreement,
                tasks,
                llm_review if isinstance(llm_review, dict) else {},
                phase=phase,
                state=state,
                allow_due_fallback=False,
            )
            for task in tasks:
                self.conn.execute(
                    """
                    UPDATE negotiation_tasks
                    SET status=?, evidence=?, last_checked_turn=?, updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                    """,
                    (
                        str(task.get("status") or "pending"),
                        str(task.get("evidence") or "")[:240],
                        int(state.turn),
                        int(task["id"]),
                    ),
                )
            next_status = self._agreement_status_from_target(target_status)

            effect: Dict[str, object] = {}
            old_target_status = str(agreement.get("target_status") or "")
            if target_status in {"achieved", "failed", "blocked"} and target_status != old_target_status:
                effect = self._apply_negotiation_political_effect(
                    state,
                    agreement,
                    new_status=next_status,
                    evidence=target_evidence or evidence,
                )
            review = {
                "phase": phase,
                "turn": int(state.turn),
                "status": next_status,
                "condition_status": condition_status,
                "target_status": target_status,
                "condition_score": score,
                "condition_evidence": evidence[:180],
                "target_evidence": target_evidence[:180],
                "llm_used": bool(llm_review),
                "tasks": [{"id": task.get("id"), "status": task.get("status"), "evidence": task.get("evidence")} for task in tasks],
            }
            self.conn.execute(
                """
                UPDATE negotiation_agreements
                SET status=?, condition_status=?, target_status=?,
                    last_checked_turn=?,
                    resolved_turn=CASE WHEN ? IN ('fulfilled','failed','blocked') THEN ? ELSE resolved_turn END,
                    fulfillment_score=?, fulfillment_evidence=?, target_evidence=?,
                    political_effect_json=?, auto_review_json=?, llm_review_json=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (
                    next_status,
                    condition_status,
                    target_status,
                    int(state.turn),
                    next_status,
                    int(state.turn),
                    score,
                    evidence[:240],
                    target_evidence[:240],
                    json.dumps(effect, ensure_ascii=False) if effect else str(agreement.get("political_effect_json") or "{}"),
                    json.dumps(review, ensure_ascii=False),
                    json.dumps(llm_review, ensure_ascii=False) if isinstance(llm_review, dict) else "{}",
                    int(agreement["id"]),
                ),
            )
            if (
                changed_tasks
                or next_status != str(agreement.get("status") or "")
                or condition_status != str(agreement.get("condition_status") or "")
                or target_status != str(agreement.get("target_status") or "")
            ):
                reviewed.append({
                    **agreement,
                    "status": next_status,
                    "condition_status": condition_status,
                    "target_status": target_status,
                    "fulfillment_score": score,
                    "review": review,
                })
        self.conn.commit()
        return reviewed

    def list_negotiation_agreements(
        self,
        minister_name: str = "",
        action_kind: str = "",
        status: str = "",
        limit: int = 80,
    ) -> List[Dict[str, object]]:
        clauses: List[str] = []
        params: List[Any] = []
        if minister_name:
            clauses.append("minister_name=?")
            params.append(str(minister_name))
        if action_kind:
            clauses.append("action_kind=?")
            params.append(str(action_kind))
        if status:
            clauses.append("status=?")
            params.append(str(status))
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(max(1, min(200, int(limit or 80))))
        rows = self.conn.execute(
            f"""
            SELECT * FROM negotiation_agreements
            {where}
            ORDER BY id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        if not rows:
            return []
        agreement_ids = [int(row["id"]) for row in rows]
        placeholders = ",".join("?" for _ in agreement_ids)
        task_rows = self.conn.execute(
            f"""
            SELECT id, agreement_id, description, task_kind, status, evidence, last_checked_turn
            FROM negotiation_tasks
            WHERE agreement_id IN ({placeholders})
            ORDER BY agreement_id, id
            """,
            agreement_ids,
        ).fetchall()
        tasks_by_agreement: Dict[int, List[Dict[str, object]]] = {}
        for task in task_rows:
            item = dict(task)
            agreement_id = int(item.pop("agreement_id"))
            tasks_by_agreement.setdefault(agreement_id, []).append(item)
        out: List[Dict[str, object]] = []
        for row in rows:
            item = dict(row)
            item["tasks"] = tasks_by_agreement.get(int(row["id"]), [])
            out.append(item)
        return out

    def negotiation_agreement_ledger(
        self,
        state: Optional[GameState] = None,
        *,
        minister_name: str = "",
        limit: int = 80,
    ) -> List[Dict[str, object]]:
        rows = self.list_negotiation_agreements(minister_name=minister_name, limit=limit)
        ledger: List[Dict[str, object]] = []
        for row in rows:
            status = str(row.get("status") or "")
            if status not in {"pending", "sealed", "fulfilled", "failed", "blocked"}:
                continue
            try:
                auto_review = json.loads(str(row.get("auto_review_json") or "{}"))
            except (TypeError, ValueError):
                auto_review = {}
            try:
                political_effect = json.loads(str(row.get("political_effect_json") or "{}"))
            except (TypeError, ValueError):
                political_effect = {}
            try:
                llm_review = json.loads(str(row.get("llm_review_json") or "{}"))
            except (TypeError, ValueError):
                llm_review = {}
            target_status = str(row.get("target_status") or "")
            condition_status = str(row.get("condition_status") or "")
            if not target_status or (target_status == "pending_conditions" and status in {"fulfilled", "sealed"}):
                target_status = "achieved"
            if not condition_status or (condition_status == "pending" and status in {"fulfilled", "sealed"}):
                condition_status = "satisfied"
            tasks = []
            for task in row.get("tasks") or []:
                if not isinstance(task, dict):
                    continue
                tasks.append({
                    "description": task.get("description") or "",
                    "kind": task.get("task_kind") or classify_task_kind(str(task.get("description") or "")),
                    "status": task.get("status") or "pending",
                    "evidence": task.get("evidence") or "",
                })
            item = {
                "id": row.get("id"),
                "minister_name": row.get("minister_name"),
                "core_topic": row.get("core_topic") or row.get("topic"),
                "topic": row.get("topic"),
                "target_text": row.get("target_text") or row.get("core_topic") or row.get("topic"),
                "action_kind": row.get("action_kind"),
                "goal_id": row.get("goal_id") or 0,
                "promise_type": row.get("promise_type") or "",
                "stakes": row.get("stakes") or "",
                "status": status,
                "condition_status": condition_status,
                "target_status": target_status,
                "handshake_status": row.get("handshake_status"),
                "psychological_score": row.get("psychological_score"),
                "threshold": row.get("threshold"),
                "due_turn": row.get("due_turn") or 0,
                "age_turns": (int(state.turn) - int(row.get("turn_created") or 0)) if state is not None else 0,
                "fulfillment_score": row.get("fulfillment_score") or 0,
                "fulfillment_evidence": row.get("fulfillment_evidence") or "",
                "target_evidence": row.get("target_evidence") or "",
                "conditions": row.get("conditions") or "",
                "execution_consequence": self._agreement_execution_consequence(row, tasks),
                "tasks": tasks,
                "auto_review": auto_review if isinstance(auto_review, dict) else {},
                "llm_review": llm_review if isinstance(llm_review, dict) else {},
                "political_effect": political_effect if isinstance(political_effect, dict) else {},
            }
            ledger.append(item)
        return ledger

    def _agreement_execution_consequence(
        self,
        row: Dict[str, object],
        tasks: List[Dict[str, object]],
    ) -> str:
        status = str(row.get("status") or "")
        target_status = str(row.get("target_status") or "")
        condition_status = str(row.get("condition_status") or "")
        stakes = str(row.get("stakes") or "")
        if target_status == "achieved" or status == "fulfilled":
            return "可作为真实政治资本：降低该官个人拖延，增强其后续履约意愿与政治信用；仍须检验外部资源与派系阻力。"
        if condition_status == "satisfied" and status == "sealed":
            return "无待办条件的即时承诺：可作执行背书，但若后续诏书反向处置，会转为失信伤害。"
        if condition_status == "pending" or target_status == "pending_conditions" or status == "pending":
            pending = [str(task.get("description") or "") for task in tasks if task.get("status") == "pending"]
            return f"条件未闭环，标的未达成：{('；'.join(pending[:3]) or row.get('conditions') or '条件未明')}。未兑现前不得当作自愿配合。"
        if target_status == "failed" or status == "failed":
            return f"已成失信记录：刺痛{stakes or '一般政务'}，月末推演应写入不信任、补奏、拖延、清议或派系反噬。"
        if target_status == "blocked" or status == "blocked":
            return "未说服：若强推，按高压诏令和真实阻力结算，不能视为臣工协办。"
        return ""

    def has_successful_agreement(
        self,
        minister_name: str,
        action_kind: str,
        *,
        max_age_turns: int = 12,
        current_turn: int = 0,
    ) -> Optional[Dict[str, object]]:
        rows = self.list_negotiation_agreements(
            minister_name=minister_name,
            action_kind=action_kind,
            limit=20,
        )
        for row in rows:
            target_status = str(row.get("target_status") or "")
            status = str(row.get("status") or "")
            if target_status != "achieved" and status not in {"sealed", "fulfilled"}:
                continue
            if max_age_turns and current_turn:
                created = int(row.get("turn_created") or 0)
                if created and int(current_turn) - created > max_age_turns:
                    continue
            return row
        return None

    def _restore_row_in_tx(self, table: str, row: Dict[str, Any]) -> None:
        if not row:
            return
        if table not in self._ROLLBACK_TABLE_PK:
            raise ValueError(f"不支持回滚表：{table}")
        columns = list(row.keys())
        placeholders = ",".join("?" for _ in columns)
        column_sql = ",".join(columns)
        self.conn.execute(
            f"INSERT OR REPLACE INTO {table} ({column_sql}) VALUES ({placeholders})",
            [row[column] for column in columns],
        )

    def _delete_row_in_tx(self, table: str, target_id: str) -> None:
        pk = self._ROLLBACK_TABLE_PK.get(table)
        if not pk:
            raise ValueError(f"不支持回滚表：{table}")
        self.conn.execute(f"DELETE FROM {table} WHERE {pk} = ?", (target_id,))

    def undo_chat_turn(self, chat_turn_id: int) -> Dict[str, Any]:
        row = self.conn.execute(
            "SELECT * FROM chat_turns WHERE id = ?",
            (int(chat_turn_id),),
        ).fetchone()
        if row is None:
            raise ValueError("召对轮次不存在。")
        turn_row = self._row_dict(row)
        if turn_row["status"] != "active":
            raise ValueError("该召对已经撤回或不可撤回。")
        if not self.is_global_last_active_chat_turn(int(chat_turn_id)):
            raise ValueError("只能撤回全局最后一轮召对。")
        items = self.conn.execute(
            """
            SELECT * FROM chat_turn_rollback_items
            WHERE chat_turn_id = ?
            ORDER BY id DESC
            """,
            (int(chat_turn_id),),
        ).fetchall()
        message_ids = [
            int(mid)
            for mid in (turn_row.get("user_message_id"), turn_row.get("minister_message_id"))
            if mid
        ]
        with self.conn:
            for item in items:
                table = str(item["target_table"])
                strategy = str(item["rollback_strategy"])
                target_id = str(item["target_id"])
                if strategy == "delete_inserted_row":
                    self._delete_row_in_tx(table, target_id)
                elif strategy in {"restore_row", "restore_deleted_row"}:
                    before_row = self._json_load_row(item["before_json"])
                    self._restore_row_in_tx(table, before_row)
                else:
                    raise ValueError(f"不支持的回滚策略：{strategy}")
            if message_ids:
                placeholders = ",".join("?" for _ in message_ids)
                self.conn.execute(
                    f"DELETE FROM chat_messages WHERE id IN ({placeholders})",
                    message_ids,
                )
            self.conn.execute(
                """
                UPDATE chat_turns
                SET status = 'undone', undone_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (int(chat_turn_id),),
            )
            self._truncate_agno_runs_in_tx(
                str(turn_row.get("agno_session_id") or ""),
                int(turn_row.get("agno_runs_before") or 0),
            )
        return turn_row

    # ----- event memories（渐进式记忆：摘要卡 + 来源摘录） -----

    def upsert_event_memory(
        self,
        state: GameState,
        subject_type: str,
        subject_id: str,
        event_type: str,
        title: str,
        cause: str = "",
        process: str = "",
        outcome: str = "",
        sentiment: str = "neutral",
        importance: int = 3,
        tags: Optional[List[str]] = None,
        source_kind: str = "system",
        source_id: str = "",
        expires_turn: Optional[int] = None,
    ) -> int:
        """写入/更新一张事件记忆摘要卡，按主体+类型+来源去重。"""
        subject_type = (subject_type or "").strip()
        subject_id = (subject_id or "").strip()
        event_type = (event_type or "").strip()
        source_kind = (source_kind or "system").strip()
        source_id = str(source_id or "").strip()
        if not subject_type or not subject_id or not event_type or not source_id:
            return 0
        importance = max(1, min(5, int(importance or 3)))
        if expires_turn is None:
            # 按重要度自动衰减；importance=5 永久保留（None）
            _ttl = {1: 6, 2: 12, 3: 24, 4: 48}
            ttl = _ttl.get(importance)
            if ttl is not None:
                expires_turn = int(state.turn) + ttl
        clean_tags = []
        for tag in tags or []:
            t = str(tag).strip()
            if t and t not in clean_tags:
                clean_tags.append(t[:40])
        existed = self.conn.execute(
            """
            SELECT id FROM event_memories
            WHERE subject_type=? AND subject_id=? AND event_type=? AND source_kind=? AND source_id=?
            """,
            (subject_type, subject_id, event_type, source_kind, source_id),
        ).fetchone()
        self.conn.execute(
            """
            INSERT INTO event_memories
                (subject_type, subject_id, turn, year, period, event_type, title,
                 cause, process, outcome, sentiment, importance, tags,
                 source_kind, source_id, expires_turn)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(subject_type, subject_id, event_type, source_kind, source_id)
            DO UPDATE SET
                turn = excluded.turn,
                year = excluded.year,
                period = excluded.period,
                title = excluded.title,
                cause = excluded.cause,
                process = excluded.process,
                outcome = excluded.outcome,
                sentiment = excluded.sentiment,
                importance = excluded.importance,
                tags = excluded.tags,
                expires_turn = excluded.expires_turn,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                subject_type, subject_id, state.turn, state.year, state.period,
                event_type, str(title or "")[:40], str(cause or "")[:80],
                str(process or "")[:80], str(outcome or "")[:80],
                sentiment if sentiment in {"positive", "neutral", "negative", "mixed"} else "neutral",
                importance, json.dumps(clean_tags, ensure_ascii=False),
                source_kind, source_id, expires_turn,
            ),
        )
        row = self.conn.execute(
            """
            SELECT id FROM event_memories
            WHERE subject_type=? AND subject_id=? AND event_type=? AND source_kind=? AND source_id=?
            """,
            (subject_type, subject_id, event_type, source_kind, source_id),
        ).fetchone()
        self.conn.commit()
        action = "更新" if existed else "保存"
        tlog(
            f"[memory/{action}] #{int(row['id']) if row else '?'} "
            f"{subject_type}:{subject_id} {event_type}《{str(title or '')[:24]}》"
            f" imp={importance} src={source_kind}:{source_id}"
        )
        tlog(
            f"[MEM-IO/db.upsert/BODY] #{int(row['id']) if row else '?'} "
            f"title={str(title or '')!r} cause={str(cause or '')!r} "
            f"process={str(process or '')!r} outcome={str(outcome or '')!r} "
            f"sentiment={sentiment} tags={clean_tags} expires_turn={expires_turn}"
        )
        return int(row["id"]) if row else 0

    def add_event_memory_source(
        self,
        memory_id: int,
        source_kind: str,
        source_id: str,
        excerpt: str = "",
        locator: Optional[Dict[str, object]] = None,
    ) -> None:
        if not memory_id:
            return
        locator_json = json.dumps(locator or {}, ensure_ascii=False, sort_keys=True)
        self.conn.execute(
            """
            INSERT INTO event_memory_sources
                (memory_id, source_kind, source_id, excerpt, locator)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(memory_id, source_kind, source_id, locator)
            DO UPDATE SET
                excerpt = excluded.excerpt,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                int(memory_id), str(source_kind or "system"), str(source_id or ""),
                str(excerpt or "")[:200], locator_json,
            ),
        )
        self.conn.commit()
        tlog(
            f"[memory/source] memory=#{int(memory_id)} {source_kind}:{source_id} "
            f"excerpt={str(excerpt or '')[:48]}"
        )

    def prune_event_memories_for_turn(self, turn: int, per_subject: int = 3) -> None:
        """同一主体同回合只保留若干高价值摘要卡，避免记忆膨胀。"""
        rows = self.conn.execute(
            """
            SELECT id, subject_type, subject_id, importance, updated_at
            FROM event_memories
            WHERE turn = ?
            ORDER BY subject_type, subject_id, importance DESC, id DESC
            """,
            (int(turn),),
        ).fetchall()
        seen: Dict[Tuple[str, str], int] = {}
        delete_ids: List[int] = []
        for row in rows:
            key = (row["subject_type"], row["subject_id"])
            seen[key] = seen.get(key, 0) + 1
            if seen[key] > per_subject:
                delete_ids.append(int(row["id"]))
        if delete_ids:
            placeholders = ",".join("?" for _ in delete_ids)
            self.conn.execute(f"DELETE FROM event_memory_sources WHERE memory_id IN ({placeholders})", delete_ids)
            self.conn.execute(f"DELETE FROM event_memories WHERE id IN ({placeholders})", delete_ids)
            self.conn.commit()
            tlog(f"[memory/prune] turn={turn} deleted={delete_ids}")

    def get_relevant_event_memories(
        self,
        character_name: str,
        faction: str,
        office_type: str,
        turn: int,
        limit: int = 5,
        ignore_expiry: bool = False,
    ) -> List[Dict[str, object]]:
        """召见前取少量相关旧事摘要；纯结构化检索，不走向量库。
        ignore_expiry=True 时按历史时点查，不受 expires_turn 过滤。
        """
        active_issues = self.list_active_issues()
        active_issue_tags: List[str] = []
        for issue in active_issues[:12]:
            active_issue_tags.append(f"#{int(issue['id'])}")
            if issue["title"]:
                active_issue_tags.append(str(issue["title"])[:20])
        tag_needles = [character_name, faction, office_type] + active_issue_tags
        expiry_clause = "" if ignore_expiry else "AND (expires_turn IS NULL OR expires_turn >= ?)"
        params: list = [int(turn)]
        if not ignore_expiry:
            params.append(int(turn))
        params += [character_name, faction, f"%{character_name}%", f"%{faction}%", f"%{office_type}%"]
        rows = self.conn.execute(
            f"""
            SELECT *
            FROM event_memories
            WHERE turn <= ?
              {expiry_clause}
              AND (
                (subject_type='character' AND subject_id=?)
                OR (subject_type='faction' AND subject_id=?)
                OR (subject_type='court' AND importance>=4)
                OR tags LIKE ?
                OR tags LIKE ?
                OR tags LIKE ?
              )
            """,
            params,
        ).fetchall()
        scored: List[Tuple[int, sqlite3.Row, List[str]]] = []
        for row in rows:
            age = max(0, int(turn) - int(row["turn"]))
            if int(row["importance"]) <= 1 and not (
                row["subject_type"] == "character" and row["subject_id"] == character_name and age <= 3
            ):
                continue
            try:
                tags = json.loads(row["tags"] or "[]")
            except Exception:
                tags = []
            tag_matches = [t for t in tag_needles if t and any(str(t) in str(tag) or str(tag) in str(t) for tag in tags)]
            exact = row["subject_type"] == "character" and row["subject_id"] == character_name
            active_hit = any(str(t).startswith("#") or t in active_issue_tags for t in tag_matches)
            score = (
                int(row["importance"]) * 10
                + (20 if exact else 0)
                + len(tag_matches) * 4
                + max(0, 10 - age)
                + (12 if active_hit else 0)
            )
            scored.append((score, row, tag_matches))
        scored.sort(key=lambda item: (item[0], int(item[1]["turn"]), int(item[1]["id"])), reverse=True)
        result: List[Dict[str, object]] = []
        for _score, row, _matches in scored[:limit]:
            result.append({
                "id": int(row["id"]),
                "subject_type": row["subject_type"],
                "subject_id": row["subject_id"],
                "turn": int(row["turn"]),
                "year": int(row["year"]),
                "period": int(row["period"]),
                "event_type": row["event_type"],
                "title": row["title"],
                "cause": row["cause"],
                "process": row["process"],
                "outcome": row["outcome"],
                "sentiment": row["sentiment"],
                "importance": int(row["importance"]),
                "tags": json.loads(row["tags"] or "[]"),
            })
        if result:
            ids = ",".join(str(item["id"]) for item in result)
            tlog(f"[memory/recall] {character_name} hit={len(result)} ids={ids}")
            tlog(f"[MEM-IO/db.recall/OUTPUT] {character_name} full={json.dumps(result, ensure_ascii=False)}")
        else:
            tlog(f"[memory/recall] {character_name} hit=0")
        return result

    def get_recent_event_memories(
        self,
        turn: int,
        window: int = 5,
        limit: int = 100,
    ) -> List[Dict[str, object]]:
        """取近 window 回合内所有 event_memories，按 turn/id 升序，上限 limit 条。"""
        since = max(1, turn - window + 1)
        rows = self.conn.execute(
            """
            SELECT id, subject_type, subject_id, turn, year, period,
                   event_type, title, cause, process, outcome, sentiment, importance, tags
            FROM event_memories
            WHERE turn >= ? AND turn <= ?
            ORDER BY turn ASC, id ASC
            LIMIT ?
            """,
            (since, turn, limit),
        ).fetchall()
        result = []
        for row in rows:
            result.append({
                "id": int(row["id"]),
                "subject_type": row["subject_type"],
                "subject_id": row["subject_id"],
                "turn": int(row["turn"]),
                "year": int(row["year"]),
                "period": int(row["period"]),
                "event_type": row["event_type"],
                "title": row["title"],
                "cause": row["cause"],
                "process": row["process"],
                "outcome": row["outcome"],
                "sentiment": row["sentiment"],
                "importance": int(row["importance"]),
                "tags": json.loads(row["tags"] or "[]"),
            })
        tlog(f"[memory/recent] turn={turn} window={window} hit={len(result)}")
        if result:
            tlog(f"[MEM-IO/db.recent/OUTPUT] turn={turn} window={window} full={json.dumps(result, ensure_ascii=False)}")
        return result

    def get_memories_by_keywords(
        self,
        keywords: List[str],
        turn: int,
        limit: int = 10,
        ignore_expiry: bool = False,
    ) -> List[Dict[str, object]]:
        """推演前按关键词集合检索相关记忆，供 simulator/extractor 注入。

        keywords 来自 memory_retrieval agent 抽取的人名/地区/军队/势力/操作词。
        每个词对 tags JSON 做 LIKE 匹配，命中任一词即入候选，按 importance+时效评分。
        ignore_expiry=True 时按历史时点查，不受 expires_turn 过滤。
        """
        if not keywords:
            return []
        active_issue_tags = [
            f"#{int(r['id'])}"
            for r in self.conn.execute(
                "SELECT id FROM issues WHERE status='active'"
            ).fetchall()
        ]
        needles = list(dict.fromkeys([k for k in keywords if k] + active_issue_tags))
        like_clauses = " OR ".join(["tags LIKE ?" for _ in needles])
        like_params = [f"%{n}%" for n in needles]
        expiry_clause = "" if ignore_expiry else "AND (expires_turn IS NULL OR expires_turn >= ?)"
        base_params: list = [int(turn)]
        if not ignore_expiry:
            base_params.append(int(turn))

        rows = self.conn.execute(
            f"""
            SELECT * FROM event_memories
            WHERE turn <= ?
              {expiry_clause}
              AND ({like_clauses})
            ORDER BY importance DESC, turn DESC
            LIMIT ?
            """,
            base_params + like_params + [limit * 3],
        ).fetchall()

        scored: List[tuple] = []
        for row in rows:
            age = max(0, int(turn) - int(row["turn"]))
            try:
                tags = json.loads(row["tags"] or "[]")
            except Exception:
                tags = []
            hit_count = sum(
                1 for n in needles
                if any(n in str(t) or str(t) in n for t in tags)
            )
            score = int(row["importance"]) * 10 + hit_count * 5 + max(0, 8 - age)
            scored.append((score, row))

        scored.sort(key=lambda x: x[0], reverse=True)
        result = []
        for _score, row in scored[:limit]:
            result.append({
                "id": int(row["id"]),
                "subject_type": row["subject_type"],
                "subject_id": row["subject_id"],
                "turn": int(row["turn"]),
                "year": int(row["year"]),
                "period": int(row["period"]),
                "title": row["title"],
                "cause": row["cause"],
                "outcome": row["outcome"],
                "importance": int(row["importance"]),
                "tags": json.loads(row["tags"] or "[]"),
                "source_kind": row["source_kind"],  # 演算记忆 vs 大臣记忆
            })
        tlog(f"[memory/keywords] needles={len(needles)} hit={len(result)}")
        tlog(f"[MEM-IO/db.keywords/INPUT] keywords={keywords} turn={turn} ignore_expiry={ignore_expiry} needles={needles}")
        if result:
            tlog(f"[MEM-IO/db.keywords/OUTPUT] full={json.dumps(result, ensure_ascii=False)}")
        return result

    def event_memory_detail(self, memory_id: int) -> str:
        tlog(f"[memory/detail] request=#{int(memory_id)}")
        memory = self.conn.execute(
            "SELECT * FROM event_memories WHERE id = ?",
            (int(memory_id),),
        ).fetchone()
        if memory is None:
            return f"未找到旧事记忆 #{memory_id}。"
        sources = self.conn.execute(
            """
            SELECT source_kind, source_id, excerpt, locator
            FROM event_memory_sources
            WHERE memory_id = ?
            ORDER BY id
            """,
            (int(memory_id),),
        ).fetchall()
        header = (
            f"旧事 #{memory['id']}：{memory['year']}年{memory['period']}月，{memory['title']}。"
            f"起因：{memory['cause']}。经过：{memory['process']}。结果：{memory['outcome']}。"
        )
        if not sources:
            return header + "\n未存原始摘录。"
        lines = [header, "来源摘录："]
        for idx, row in enumerate(sources, 1):
            locator = row["locator"] or "{}"
            lines.append(
                f"{idx}. [{row['source_kind']}:{row['source_id']}] {row['excerpt']}"
                + (f"（定位 {locator}）" if locator and locator != "{}" else "")
            )
        out = "\n".join(lines)
        tlog(f"[MEM-IO/db.detail/OUTPUT] #{memory_id} ({len(out)}字):\n{out}")
        return out

    def save_turn_report(self, state: GameState, report: str) -> None:
        """每回合月末奏报单独存档（turn_reports），与 turn_logs 日志解耦。"""
        self.conn.execute(
            """
            INSERT INTO turn_reports (turn, year, period, report)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(turn) DO UPDATE SET
                year = excluded.year,
                period = excluded.period,
                report = excluded.report
            """,
            (state.turn, state.year, state.period, report),
        )
        self.conn.commit()

    def get_turn_report(self, turn: int) -> str:
        row = self.conn.execute(
            "SELECT report FROM turn_reports WHERE turn = ?",
            (turn,),
        ).fetchone()
        return (row["report"] if row else "") or ""

    # ── 章节记忆（event_memories 的 chapter_summary 类，每回合一条，importance=5 永久）──

    def save_chapter_memory(
        self, state: GameState, title: str, body: str, tags: Optional[List[str]] = None
    ) -> int:
        """落本回合章节记忆。subject 固定 court/chapter，event_type=chapter_summary，
        source_id=turn 保证每回合唯一。body 存整段叙事章节（不受 outcome 80 字限）。

        tags：除固定的 `章节`/`turnN` 外，并入 LLM 抽出的人物/地点/派系/事件召回标签，
        供 recall_memories 按人名/派系命中本章。"""
        base_tags = ["章节", f"turn{state.turn}"]
        for t in tags or []:
            t = str(t).strip()
            if t and t not in base_tags:
                base_tags.append(t)
        memory_id = self.upsert_event_memory(
            state,
            subject_type="court",
            subject_id="chapter",
            event_type="chapter_summary",
            title=str(title or f"崇祯{state.year}年{state.period}月")[:40],
            outcome=str(title or "")[:80],
            sentiment="neutral",
            importance=5,
            tags=base_tags,
            source_kind="turn_report",
            source_id=str(state.turn),
            expires_turn=None,
        )
        if memory_id:
            self.conn.execute(
                "UPDATE event_memories SET body = ? WHERE id = ?",
                (str(body or ""), memory_id),
            )
            self.conn.commit()
        return memory_id

    def list_chapter_memories(
        self, upto_turn: Optional[int] = None, recent: Optional[int] = None
    ) -> List[Dict[str, object]]:
        """取章节记忆，按 turn 升序。upto_turn 限上界；recent 只取最近 N 回合（喂大臣/推演用）。"""
        clauses = ["event_type = 'chapter_summary'"]
        params: list = []
        if upto_turn is not None:
            clauses.append("turn <= ?")
            params.append(int(upto_turn))
        if recent is not None and upto_turn is not None:
            clauses.append("turn >= ?")
            params.append(max(1, int(upto_turn) - int(recent) + 1))
        where = " AND ".join(clauses)
        rows = self.conn.execute(
            f"SELECT turn, year, period, title, body FROM event_memories "
            f"WHERE {where} ORDER BY turn ASC",
            params,
        ).fetchall()
        return [
            {
                "turn": int(r["turn"]),
                "year": int(r["year"]),
                "period": int(r["period"]),
                "title": r["title"] or "",
                "body": r["body"] or "",
            }
            for r in rows
        ]

    # ── 结局总结 ──

    def save_ending_summary(
        self, state: GameState, ending_status: str, summary: str, timeline: List[Dict[str, object]]
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO ending_summary (turn, year, period, ending_status, summary, timeline)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(turn) DO UPDATE SET
                year = excluded.year, period = excluded.period,
                ending_status = excluded.ending_status,
                summary = excluded.summary, timeline = excluded.timeline
            """,
            (
                state.turn, state.year, state.period, str(ending_status or ""),
                str(summary or ""), json.dumps(timeline or [], ensure_ascii=False),
            ),
        )
        self.conn.commit()

    def get_ending_summary(self) -> Optional[Dict[str, object]]:
        """取最近一条结局总结（单库一局，按 turn 取最大）。无则 None。"""
        row = self.conn.execute(
            "SELECT turn, year, period, ending_status, summary, timeline "
            "FROM ending_summary ORDER BY turn DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        try:
            timeline = json.loads(row["timeline"] or "[]")
        except Exception:
            timeline = []
        return {
            "turn": int(row["turn"]),
            "year": int(row["year"]),
            "period": int(row["period"]),
            "ending_status": row["ending_status"],
            "summary": row["summary"] or "",
            "timeline": timeline,
        }

    def list_archived_turns(self) -> List[Dict[str, object]]:
        """所有已存档回合（turn_reports/turn_extractions/turn_directives 任一有数据）。
        返回按 turn 升序的元信息列表，每项含 turn/year/period 与各来源是否存在。"""
        rows = self.conn.execute(
            """
            SELECT t.turn AS turn,
                   MAX(t.year) AS year,
                   MAX(t.period) AS period,
                   MAX(t.has_report) AS has_report,
                   MAX(t.has_extraction) AS has_extraction,
                   MAX(t.has_directive) AS has_directive
            FROM (
                SELECT turn, year, period, 1 AS has_report, 0 AS has_extraction, 0 AS has_directive
                FROM turn_reports
                UNION ALL
                SELECT turn, year, period, 0, 1, 0 FROM turn_extractions
                UNION ALL
                SELECT turn, year, period, 0, 0, 1 FROM turn_directives
                WHERE status = 'issued'
            ) AS t
            GROUP BY t.turn
            ORDER BY t.turn
            """
        ).fetchall()
        return [
            {
                "turn": int(r["turn"]),
                "year": int(r["year"]),
                "period": int(r["period"]),
                "has_report": bool(r["has_report"]),
                "has_extraction": bool(r["has_extraction"]),
                "has_directive": bool(r["has_directive"]),
            }
            for r in rows
        ]

    def list_directives_by_turn(self, turn: int) -> List[Dict[str, object]]:
        """读某回合已颁诏（issued）草案，按 id 升序。"""
        rows = self.conn.execute(
            """
            SELECT d.id, d.turn, d.year, d.period, d.event_id, d.actor,
                   d.skill_id, d.text, d.source, d.status, d.notes,
                   d.created_at, d.updated_at,
                   e.title AS event_title
            FROM turn_directives d
            LEFT JOIN events e ON e.id = d.event_id
            WHERE d.turn = ? AND d.status = 'issued'
            ORDER BY d.id
            """,
            (int(turn),),
        ).fetchall()
        return [
            {
                "id": int(r["id"]),
                "turn": int(r["turn"]),
                "year": int(r["year"]),
                "period": int(r["period"]),
                "event_id": r["event_id"] or "",
                "event_title": r["event_title"] or "",
                "actor": r["actor"] or "",
                "skill_id": r["skill_id"] or "",
                "text": r["text"] or "",
                "source": r["source"] or "",
                "status": r["status"] or "",
                "notes": r["notes"] or "",
                "created_at": r["created_at"] or "",
                "updated_at": r["updated_at"] or "",
            }
            for r in rows
        ]

    def save_turn_extraction(
        self,
        state: GameState,
        decree_text: str = "",
        narrative: str = "",
        extractor_input: str = "",
        extractor_output: str = "",
        causal_notes: List[Dict[str, object]] | None = None,
    ) -> None:
        """推演链原始输入/输出留痕（turn_extractions），事后可追可重放。"""
        self.conn.execute(
            """
            INSERT INTO turn_extractions
                (turn, year, period, decree_text, narrative, extractor_input, extractor_output, causal_notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(turn) DO UPDATE SET
                year = excluded.year,
                period = excluded.period,
                decree_text = excluded.decree_text,
                narrative = excluded.narrative,
                extractor_input = excluded.extractor_input,
                extractor_output = excluded.extractor_output,
                causal_notes = excluded.causal_notes
            """,
            (state.turn, state.year, state.period, decree_text, narrative,
             extractor_input, extractor_output,
             json.dumps(causal_notes or [], ensure_ascii=False)),
        )
        self.conn.commit()

    def get_turn_extraction(self, turn: int) -> Optional[Dict[str, object]]:
        """读 turn_extractions 一行；extractor_output JSON 解析失败时原样回字符串。"""
        row = self.conn.execute(
            "SELECT turn, year, period, decree_text, narrative, extractor_input, extractor_output, causal_notes "
            "FROM turn_extractions WHERE turn = ?",
            (int(turn),),
        ).fetchone()
        if row is None:
            return None
        def _parse(text: str) -> object:
            text = (text or "").strip()
            if not text:
                return None
            try:
                return json.loads(text)
            except Exception:
                pass
            # LLM 多输出一个 }，顶层提前关闭，trailing 是被截出的字段。
            # 去掉多余 }，接回 trailing（trailing 本身以顶层 } 结尾）。
            try:
                dec = json.JSONDecoder()
                obj, end = dec.raw_decode(text)
                trailing = text[end:].strip()
                if trailing.startswith(","):
                    prefix = text[:end].rstrip()
                    if prefix.endswith("}"):
                        fixed = prefix[:-1] + trailing
                        try:
                            return json.loads(fixed)
                        except Exception:
                            pass
                return obj
            except Exception:
                pass
            return text
        return {
            "turn": int(row["turn"]),
            "year": int(row["year"]),
            "period": int(row["period"]),
            "decree_text": row["decree_text"] or "",
            "narrative": row["narrative"] or "",
            "extractor_input": _parse(row["extractor_input"] or ""),
            "extractor_output": _parse(row["extractor_output"] or ""),
            "causal_notes": _parse(row["causal_notes"] or "[]") or [],
        }

    def grant_skill(self, state: GameState, character_name: str, skill_id: str, granted_by: str = "皇帝") -> bool:
        exists = self.conn.execute(
            """
            SELECT 1 FROM skill_grants
            WHERE character_name = ? AND skill_id = ? AND active = 1
            LIMIT 1
            """,
            (character_name, skill_id),
        ).fetchone()
        if exists:
            return False
        self.conn.execute(
            """
            INSERT INTO skill_grants (character_name, skill_id, granted_by, source_turn, active)
            VALUES (?, ?, ?, ?, 1)
            """,
            (character_name, skill_id, granted_by, state.turn),
        )
        self.conn.commit()
        return True

    def revoke_skill(self, character_name: str, skill_id: str) -> bool:
        cursor = self.conn.execute(
            """
            UPDATE skill_grants
            SET active = 0
            WHERE character_name = ? AND skill_id = ? AND active = 1
            """,
            (character_name, skill_id),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def active_skill_grants(self, character_name: str) -> List[str]:
        rows = self.conn.execute(
            """
            SELECT skill_id FROM skill_grants
            WHERE character_name = ? AND active = 1
            ORDER BY id
            """,
            (character_name,),
        ).fetchall()
        return [str(row["skill_id"]) for row in rows]

    def add_directive(
        self,
        state: GameState,
        event: Event | None,
        text: str,
        source: str,
        actor: str = "",
        skill_id: str = "",
        notes: str = "",
        status: str = "draft",
    ) -> int:
        # status: 'draft'=已确认颁诏候选；'pending'=大臣拟旨待皇帝核定。
        cursor = self.conn.execute(
            """
            INSERT INTO turn_directives
            (turn, year, period, event_id, actor, skill_id, text, source, status, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (state.turn, state.year, state.period, event.id if event else "",
             actor, skill_id, text, source, status, notes),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def list_directives(
        self, state: GameState, statuses: Tuple[str, ...] = ("draft",)
    ) -> List[sqlite3.Row]:
        # 默认只取 draft（颁诏候选）；UI 列表传 ('pending','draft') 一起取，前端按 status 分区。
        placeholders = ",".join("?" for _ in statuses)
        return self.conn.execute(
            f"""
            SELECT d.*, e.title AS event_title
            FROM turn_directives d
            LEFT JOIN events e ON e.id = d.event_id
            WHERE d.turn = ? AND d.status IN ({placeholders})
            ORDER BY d.id
            """,
            (state.turn, *statuses),
        ).fetchall()

    def confirm_directive(self, directive_id: int) -> None:
        """大臣拟旨经皇帝核定：pending → draft（进入颁诏候选池）。"""
        self.conn.execute(
            """
            UPDATE turn_directives
            SET status = 'draft', updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'pending'
            """,
            (directive_id,),
        )
        self.conn.commit()

    def reject_directive(self, directive_id: int) -> None:
        """皇帝驳回大臣拟旨：pending → rejected。"""
        self.conn.execute(
            """
            UPDATE turn_directives
            SET status = 'rejected', updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'pending'
            """,
            (directive_id,),
        )
        self.conn.commit()

    def count_pending_directives(self, state: GameState) -> int:
        """本回合待核定（pending）的大臣拟旨数。颁诏前须为 0。"""
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM turn_directives WHERE turn = ? AND status = 'pending'",
            (state.turn,),
        ).fetchone()
        return int(row["n"]) if row else 0

    def update_directive_text(self, directive_id: int, text: str) -> None:
        self.conn.execute(
            """
            UPDATE turn_directives
            SET text = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (text, directive_id),
        )
        self.conn.commit()

    def update_directive(
        self,
        directive_id: int,
        event: Event,
        actor: str,
        skill_id: str,
        text: str,
        notes: str,
    ) -> None:
        self.conn.execute(
            """
            UPDATE turn_directives
            SET event_id = ?,
                actor = ?,
                skill_id = ?,
                text = ?,
                notes = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (event.id, actor, skill_id, text, notes, directive_id),
        )
        self.conn.commit()

    def delete_directive(self, directive_id: int) -> None:
        self.conn.execute(
            """
            UPDATE turn_directives
            SET status = 'deleted', updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (directive_id,),
        )
        self.conn.commit()

    def mark_directives_issued(self, state: GameState) -> None:
        self.conn.execute(
            """
            UPDATE turn_directives
            SET status = 'issued', updated_at = CURRENT_TIMESTAMP
            WHERE turn = ? AND status = 'draft'
            """,
            (state.turn,),
        )
        self.conn.commit()

    # ----- issues (双类事项 + 双向进度条) -----

    def _derive_issue_phase(self, bar: int) -> str:
        if bar <= 0:
            return "终"
        if bar < 30:
            return "起"
        if bar < 70:
            return "中"
        if bar < 100:
            return "终前"
        return "终"

    def list_active_issues(self, kind: str | None = None) -> List[sqlite3.Row]:
        sql = "SELECT * FROM issues WHERE status = 'active'"
        args: List[object] = []
        if kind:
            sql += " AND kind = ?"
            args.append(kind)
        sql += " ORDER BY severity DESC, id ASC"
        return self.conn.execute(sql, args).fetchall()

    def list_closed_issues_at(self, closed_turn: int) -> List[sqlite3.Row]:
        """指定 turn 关闭（resolved / failed / dropped）的 issue。"""
        return self.conn.execute(
            "SELECT * FROM issues WHERE closed_turn = ? AND status IN ('resolved','failed','dropped') ORDER BY id",
            (int(closed_turn),),
        ).fetchall()

    def count_active_initiatives(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM issues WHERE kind='initiative' AND status='active'"
        ).fetchone()
        return int(row["n"] or 0)

    def find_active_issue_by_origin(self, origin_kind: str, origin_ref: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM issues WHERE origin_kind=? AND origin_ref=? AND status='active' LIMIT 1",
            (origin_kind, origin_ref),
        ).fetchone()

    def find_any_issue_by_origin(self, origin_kind: str, origin_ref: str) -> sqlite3.Row | None:
        """查任意状态（含 resolved/failed/dropped）的同源 issue，用于 spawn 去重。"""
        return self.conn.execute(
            "SELECT * FROM issues WHERE origin_kind=? AND origin_ref=? LIMIT 1",
            (origin_kind, origin_ref),
        ).fetchone()

    def has_event_triggered(self, event_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM event_triggers WHERE event_id=? LIMIT 1",
            (event_id,),
        ).fetchone()
        return row is not None

    def mark_event_triggered(self, state: GameState, event_id: str, source: str = "simulation") -> None:
        self.conn.execute(
            """
            INSERT OR IGNORE INTO event_triggers (event_id, turn, year, period, source)
            VALUES (?, ?, ?, ?, ?)
            """,
            (event_id, state.turn, state.year, state.period, source),
        )
        self.conn.commit()

    def insert_issue(
        self,
        state: GameState,
        *,
        kind: str,
        title: str,
        origin_kind: str = "",
        origin_ref: str = "",
        bar_value: int = 40,
        bar_good_meaning: str = "已平",
        bar_bad_meaning: str = "失控",
        inertia: int = 0,
        stage_text: str = "",
        severity: int = 50,
        region_hint: str = "",
        faction_hint: str = "",
        tags: List[str] | None = None,
        ongoing_effects: Dict[str, object] | None = None,
        cancellable: str = "never",
        cancel_cost: Dict[str, object] | None = None,
        effect_on_resolve: Dict[str, object] | None = None,
        effect_on_fail: Dict[str, object] | None = None,
        resolve_condition: str = "",
        fail_condition: str = "",
    ) -> int:
        if kind not in ("situation", "initiative"):
            raise ValueError(f"issue kind 非法：{kind}")
        if cancellable not in ("decree", "never", "by_progress"):
            raise ValueError(f"cancellable 非法：{cancellable}")
        bar_value = max(0, min(100, int(bar_value)))
        phase = self._derive_issue_phase(bar_value)
        cur = self.conn.execute(
            """
            INSERT INTO issues (
                kind, title, origin_kind, origin_ref, origin_turn,
                bar_value, bar_good_meaning, bar_bad_meaning, inertia,
                phase, stage_text, status, severity, region_hint, faction_hint,
                tags, ongoing_effects, cancellable, cancel_cost,
                effect_on_resolve, effect_on_fail, resolve_condition, fail_condition,
                last_advance_turn
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                kind, title, origin_kind, origin_ref, state.turn,
                bar_value, bar_good_meaning, bar_bad_meaning, int(inertia),
                phase, stage_text, int(severity), region_hint, faction_hint,
                json.dumps(tags or [], ensure_ascii=False),
                json.dumps(ongoing_effects or {}, ensure_ascii=False),
                cancellable,
                json.dumps(cancel_cost or {}, ensure_ascii=False),
                json.dumps(effect_on_resolve or {}, ensure_ascii=False),
                json.dumps(effect_on_fail or {}, ensure_ascii=False),
                resolve_condition, fail_condition,
                state.turn,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def advance_issue(
        self,
        state: GameState,
        issue_id: int,
        *,
        trigger_kind: str,
        trigger_ref: str = "",
        delta_bar: int = 0,
        stage_text: str = "",
        narrative: str = "",
        metric_delta: Dict[str, int] | None = None,
        inertia_delta: int = 0,
    ) -> sqlite3.Row | None:
        row = self.conn.execute("SELECT * FROM issues WHERE id=?", (issue_id,)).fetchone()
        if row is None or row["status"] != "active":
            return None
        # 崩坏能力由 effect_on_fail 是否非空判定：有崩坏效果=会崩坏（bar 能到 0、failed 终结）；
        # 空=不会崩坏（天灾/正面机遇等不可控或无失败态局势，bar 下限钳到 1，永不 failed，
        # 只靠 ongoing_effects 每月持续流血）。
        can_collapse = bool(json.loads(row["effect_on_fail"] or "{}"))
        floor = 0 if can_collapse else 1
        # clamp single advance
        delta_bar = max(-50, min(50, int(delta_bar)))
        from_value = int(row["bar_value"])
        to_value = max(floor, min(100, from_value + delta_bar))
        actual_delta = to_value - from_value
        from_stage_text = row["stage_text"]
        to_stage_text = stage_text or from_stage_text
        new_phase = self._derive_issue_phase(to_value)
        new_status = row["status"]
        closed_turn = row["closed_turn"]
        if to_value >= 100:
            new_status = "resolved"
            closed_turn = state.turn
        elif to_value <= 0 and can_collapse:
            new_status = "failed"
            closed_turn = state.turn
        # inertia 可被本次行动改变（钳到 -10..+10 五档区间）
        new_inertia = int(row["inertia"]) + int(inertia_delta)
        new_inertia = max(-10, min(10, new_inertia))
        self.conn.execute(
            """
            UPDATE issues SET bar_value=?, phase=?, stage_text=?, status=?, inertia=?,
                              closed_turn=?, last_advance_turn=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (to_value, new_phase, to_stage_text, new_status, new_inertia, closed_turn, state.turn, issue_id),
        )
        self.conn.execute(
            """
            INSERT INTO issue_advances (
                issue_id, turn, trigger_kind, trigger_ref,
                delta_bar, from_value, to_value,
                from_stage_text, to_stage_text, narrative, metric_delta
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                issue_id, state.turn, trigger_kind, trigger_ref,
                actual_delta, from_value, to_value,
                from_stage_text, to_stage_text, narrative,
                json.dumps(metric_delta or {}, ensure_ascii=False),
            ),
        )
        self.conn.commit()
        return self.conn.execute("SELECT * FROM issues WHERE id=?", (issue_id,)).fetchone()

    def close_issue(
        self,
        state: GameState,
        issue_id: int,
        *,
        reason: str,
        narrative: str = "",
    ) -> sqlite3.Row | None:
        """LLM 主动通知收尾。reason 必须是 'resolved' 或 'failed'。不看 bar 门槛。"""
        if reason not in ("resolved", "failed"):
            raise ValueError(f"close_issue reason 非法：{reason}")
        row = self.conn.execute("SELECT * FROM issues WHERE id=?", (issue_id,)).fetchone()
        if row is None or row["status"] != "active":
            return None
        # 不可崩坏局势（effect_on_fail 空：天灾/不可控灾害）没有「失败终结」态——LLM 误判 failed
        # 时拒绝结案，留 active 继续靠 ongoing_effects 流血，只能靠 resolved（赈济平息）收尾。
        if reason == "failed" and not json.loads(row["effect_on_fail"] or "{}"):
            print(f"[INFO] close_issue 已拒：issue {issue_id}（{row['title']}）无 effect_on_fail，不可崩坏，保持 active。")
            return None
        from_value = int(row["bar_value"])
        # resolved → 抬到 100；failed → 压到 0；用于 inertia/UI 一眼看懂
        to_value = 100 if reason == "resolved" else 0
        actual_delta = to_value - from_value
        from_stage_text = row["stage_text"]
        to_stage_text = narrative or from_stage_text
        new_phase = self._derive_issue_phase(to_value)
        self.conn.execute(
            """
            UPDATE issues SET bar_value=?, phase=?, stage_text=?, status=?,
                              closed_turn=?, last_advance_turn=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (to_value, new_phase, to_stage_text, reason, state.turn, state.turn, issue_id),
        )
        self.conn.execute(
            """
            INSERT INTO issue_advances (
                issue_id, turn, trigger_kind, trigger_ref,
                delta_bar, from_value, to_value,
                from_stage_text, to_stage_text, narrative, metric_delta
            ) VALUES (?, ?, 'close', ?, ?, ?, ?, ?, ?, ?, '{}')
            """,
            (
                issue_id, state.turn, reason,
                actual_delta, from_value, to_value,
                from_stage_text, to_stage_text, narrative,
            ),
        )
        self.conn.commit()
        return self.conn.execute("SELECT * FROM issues WHERE id=?", (issue_id,)).fetchone()

    # ── 帝国修正（legacies 表）：结案留下的长期百分比修正符，落账层放大/缩小增量 ────
    def insert_legacy(
        self,
        state: GameState,
        *,
        name: str,
        modifiers: Dict[str, object],
        narrative_hint: str = "",
        duration_months: int = 24,
        source_issue_id: int | None = None,
        clear_gate: Dict[str, str] | None = None,
        legacy_key: str = "",
    ) -> int:
        """结案产生持续修正符。start_month=当前绝对月，duration_months=-1 为永久。
        clear_gate 非空时：靠程序按 _gate_passed 判定消除（见 issues.clear_gated_legacies），与时长无关。"""
        start_month = int(state.year) * 12 + int(state.period)
        cur = self.conn.execute(
            """INSERT INTO legacies
               (name, source_issue_id, modifiers, narrative_hint,
                start_month, duration_months, status, clear_gate, legacy_key)
               VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
            (
                str(name)[:60], source_issue_id,
                json.dumps(modifiers, ensure_ascii=False),
                str(narrative_hint)[:200],
                start_month, int(duration_months),
                json.dumps(clear_gate or {}, ensure_ascii=False),
                str(legacy_key)[:60],
            ),
        )
        self.conn.commit()
        self._legacy_mod_cache = None  # active 集变了，修正符缓存失效
        return int(cur.lastrowid)

    def list_active_legacies(self, state: GameState) -> List[sqlite3.Row]:
        """当前仍生效的帝国修正，顺手把已到期的失活。"""
        self.expire_legacies(state)
        return self.conn.execute(
            "SELECT * FROM legacies WHERE status='active' ORDER BY id"
        ).fetchall()

    def expire_legacies(self, state: GameState) -> List[int]:
        """到期失活：当前月 >= start_month + duration_months（永久 -1 永不到期）。"""
        now = int(state.year) * 12 + int(state.period)
        rows = self.conn.execute(
            "SELECT id, start_month, duration_months FROM legacies WHERE status='active'"
        ).fetchall()
        expired: List[int] = []
        for r in rows:
            dur = int(r["duration_months"])
            if dur < 0:
                continue
            if now >= int(r["start_month"]) + dur:
                expired.append(int(r["id"]))
        if expired:
            self.conn.executemany(
                "UPDATE legacies SET status='expired' WHERE id=?",
                [(i,) for i in expired],
            )
            self.conn.commit()
            self._legacy_mod_cache = None  # active 集变了，修正符缓存失效
        return expired

    def legacy_remaining_months(self, row: sqlite3.Row, state: GameState) -> int:
        """剩余月数；-1=永久。"""
        dur = int(row["duration_months"])
        if dur < 0:
            return -1
        now = int(state.year) * 12 + int(state.period)
        return max(0, int(row["start_month"]) + dur - now)

    def legacy_modifiers(self, state: GameState) -> Dict[str, object]:
        """聚合所有 active 遗产的百分比修正符，同维度累加（A 方案）。返回：
        {
          "国库": net_pct, "内库": net_pct, "民心": net_pct, "皇威": net_pct,
          "regions": {region_id: {field: net_pct, ...}, ...},
          "armies":  {army_id:  {field: net_pct, ...}, ...},
        }
        net_pct 为带符号整数百分比；落账时 base>=0 用 ×(1+net/100)，base<0 用 ×(1-net/100)。
        结果缓存，active 遗产集变化时由 insert_legacy/expire_legacies 清空。
        """
        # expire 可能改变 active 集 → 先跑（其内部会在有变动时清缓存）
        self.expire_legacies(state)
        if self._legacy_mod_cache is not None:
            return self._legacy_mod_cache
        agg: Dict[str, object] = {"国库": 0, "内库": 0, "民心": 0, "皇威": 0, "regions": {}, "armies": {}}
        for lg in self.conn.execute(
            "SELECT modifiers FROM legacies WHERE status='active' ORDER BY id"
        ).fetchall():
            try:
                eff = json.loads(str(lg["modifiers"] or "{}"))
            except Exception:
                continue
            for acc in ("国库", "内库", "民心", "皇威"):
                v = eff.get(acc)
                if isinstance(v, (int, float)):
                    agg[acc] = int(agg[acc]) + int(v)
            for scope in ("regions", "armies"):
                block = eff.get(scope)
                if not isinstance(block, dict):
                    continue
                dst = agg[scope]  # type: ignore[assignment]
                for entity_id, fields in block.items():
                    if not isinstance(fields, dict):
                        continue
                    bucket = dst.setdefault(str(entity_id), {})  # type: ignore[union-attr]
                    for field, pct in fields.items():
                        if isinstance(pct, (int, float)):
                            bucket[str(field)] = int(bucket.get(str(field), 0)) + int(pct)
        self._legacy_mod_cache = agg
        return agg

    @staticmethod
    def apply_legacy_pct(base: int, net_pct: int) -> int:
        """遗产百分比修正：base>=0 → base×(1+net/100)；base<0 → base×(1-net/100)。net=0 原样。"""
        if net_pct == 0 or base == 0:
            return int(base)
        factor = (1 + net_pct / 100.0) if base >= 0 else (1 - net_pct / 100.0)
        return int(round(base * factor))

    def cancel_issue(
        self,
        state: GameState,
        issue_id: int,
        *,
        narrative: str = "",
        applied_cost: Dict[str, object] | None = None,
    ) -> sqlite3.Row | None:
        row = self.conn.execute("SELECT * FROM issues WHERE id=?", (issue_id,)).fetchone()
        if row is None or row["status"] != "active":
            return None
        self.conn.execute(
            "UPDATE issues SET status='dropped', closed_turn=?, last_advance_turn=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (state.turn, state.turn, issue_id),
        )
        self.conn.execute(
            """
            INSERT INTO issue_advances (
                issue_id, turn, trigger_kind, delta_bar,
                from_value, to_value, narrative, metric_delta
            ) VALUES (?, ?, 'cancel', 0, ?, ?, ?, ?)
            """,
            (
                issue_id, state.turn,
                int(row["bar_value"]), int(row["bar_value"]),
                narrative,
                json.dumps(applied_cost or {}, ensure_ascii=False),
            ),
        )
        self.conn.commit()
        return self.conn.execute("SELECT * FROM issues WHERE id=?", (issue_id,)).fetchone()

    def list_recent_issue_advances(self, issue_id: int, limit: int = 3) -> List[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM issue_advances WHERE issue_id=? ORDER BY id DESC LIMIT ?",
            (issue_id, limit),
        ).fetchall()

    def record_issue_economy_move(
        self,
        state: GameState,
        account: str,
        delta: int,
        category: str,
        reason: str,
        purpose: str | None = None,
        target_kind: str | None = None,
        target_id: str | None = None,
    ) -> int:
        """记一笔经济流水到 economy_ledger，同步更新 metrics[account]。

        purpose/target_kind/target_id 仅对 extractor 抽出的 economy_moves（自由拨款）填，
        flows 月固定支出与所有收入一律 None。受控枚举见 constants.ECONOMY_PURPOSES。

        遗产修正：account 上若有 active 遗产百分比修正符，先按 apply_legacy_pct 放大/缩小 delta
        再落账（base>=0 ×(1+net/100)，base<0 ×(1-net/100)）。修正折进本笔流水，不另立账行。
        category=='局势遗产' 时不再二次修正（避免自乘，且当前已无该类调用）。
        """
        if category != "局势遗产":
            net_pct = int(self.legacy_modifiers(state).get(account, 0) or 0)  # type: ignore[arg-type]
            if net_pct:
                delta = self.apply_legacy_pct(int(delta), net_pct)
        before = int(state.metrics[account])
        after = max(0, before + int(delta))
        actual = after - before
        if actual == 0:
            return 0
        state.metrics[account] = after
        self.conn.execute(
            """
            INSERT INTO economy_ledger
            (turn, year, period, account, delta, balance_after, category, reason,
             event_id, edict_id, actor, purpose, target_kind, target_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, '事项推演', ?, ?, ?)
            """,
            (state.turn, state.year, state.period, account, actual, after,
             category, reason, purpose, target_kind, target_id),
        )
        self.sync_economy_accounts(state)
        self.conn.commit()
        return actual

    def kv_get(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM kv_store WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def kv_set(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO kv_store(key,value,updated_at) VALUES(?,?,CURRENT_TIMESTAMP) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP",
            (key, value),
        )
        self.conn.commit()

    # ----- secret_orders（密令系统）-----

    def fail_active_secret_orders_for_minister(
        self,
        minister_name: str,
        state: GameState,
        reason: str = "",
    ) -> List[Dict[str, object]]:
        """承办人退场/转势力时，中止其 active 密令。

        已提交核议的 pending_review 保留给月末推演判成败；这里仅处理仍在执行中的密令，
        防止罢黜、下狱、死亡或投敌后仍继续推进。
        """
        clean_minister = str(minister_name or "").strip()
        if not clean_minister:
            return []
        rows = self.conn.execute(
            """
            SELECT id, title, result FROM secret_orders
            WHERE status = 'active' AND minister_name = ?
            ORDER BY id
            """,
            (clean_minister,),
        ).fetchall()
        if not rows:
            return []
        note = (reason or f"承办人{clean_minister}已不可承办，密令中止。").strip()
        stamp = f"〔{period_label(state.year, state.period)}〕[承办中止] "
        failed: List[Dict[str, object]] = []
        for row in rows:
            prev = row["result"] or ""
            lines = [ln for ln in prev.split("\n") if ln.strip()]
            if not any("[承办中止]" in ln for ln in lines):
                lines.append(f"{stamp}{note[:300]}")
            self.conn.execute(
                """
                UPDATE secret_orders
                SET status = 'failed', result = ?, turn_closed = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                ("\n".join(lines), int(state.turn), int(row["id"])),
            )
            failed.append({"id": int(row["id"]), "title": row["title"], "reason": note})
        self.conn.commit()
        tlog(f"[secret_order] fail_assignee minister={clean_minister} ids={[item['id'] for item in failed]}")
        return failed

    def create_secret_order(
        self,
        state: GameState,
        minister_name: str,
        title: str,
        content: str,
        tags: List[str],
        importance: int = 4,
        deadline_months: int = 0,
    ) -> int:
        clean_minister = str(minister_name or "").strip()
        if not clean_minister:
            raise ValueError("密令承办人不能为空。")
        assignee = self.conn.execute(
            "SELECT status, status_reason, power_id FROM characters WHERE name=?",
            (clean_minister,),
        ).fetchone()
        if assignee is None:
            raise ValueError(f"密令承办人未在大明名册：{clean_minister}。请先补档入朝，再下密令。")
        if str(assignee["power_id"] or "ming") != "ming":
            raise ValueError(f"{clean_minister}不属大明朝廷，不能承办密令。")
        assignee_status = str(assignee["status"] or "")
        if assignee_status != "active":
            label = CHARACTER_STATUS_LABELS.get(assignee_status, assignee_status)
            reason = str(assignee["status_reason"] or "")
            raise ValueError(f"{clean_minister}{label}，不能承办密令。" + reason)
        active_count = self.conn.execute(
            "SELECT COUNT(*) FROM secret_orders WHERE status='active'"
        ).fetchone()[0]
        if active_count >= 20:
            raise ValueError(f"进行中密令已达上限（20条），请先结案部分密令再下新令。当前：{active_count} 条。")
        tags_json = json.dumps(tags, ensure_ascii=False)
        deadline = max(0, min(int(deadline_months or 0), 36))
        due_turn = int(state.turn) + deadline if deadline else 0
        cur = self.conn.execute(
            """
            INSERT INTO secret_orders
                (turn_issued, due_turn, year_issued, period_issued, minister_name, title, content, tags, importance, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')
            """,
            (state.turn, due_turn, state.year, state.period, clean_minister, title[:20], content, tags_json, importance),
        )
        self.conn.commit()
        tlog(f"[secret_order] create id={cur.lastrowid} minister={clean_minister} title={title[:20]}")
        return cur.lastrowid  # type: ignore[return-value]

    def list_secret_orders(
        self,
        status: Optional[str] = None,
        minister_name: Optional[str] = None,
    ) -> List[Dict[str, object]]:
        clauses, params = [], []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if minister_name:
            clauses.append("minister_name = ?")
            params.append(minister_name)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self.conn.execute(
            f"SELECT * FROM secret_orders {where} ORDER BY id DESC",
            params,
        ).fetchall()
        return [
            {
                "id": int(r["id"]),
                "turn_issued": int(r["turn_issued"]),
                "due_turn": int(r["due_turn"] if "due_turn" in r.keys() else 0),
                "year_issued": int(r["year_issued"]),
                "period_issued": int(r["period_issued"]),
                "minister_name": r["minister_name"],
                "title": r["title"],
                "content": r["content"],
                "tags": json.loads(r["tags"] or "[]"),
                "importance": int(r["importance"]),
                "status": r["status"],
                "result": r["result"] or "",
                "sim_note": (r["sim_note"] if "sim_note" in r.keys() else "") or "",
                "turn_closed": r["turn_closed"],
            }
            for r in rows
        ]

    def get_active_secret_orders_for_minister(self, minister_name: str) -> List[Dict[str, object]]:
        """返回该大臣名下未结案密令（active + pending_review）。done/failed 已结案不再返回。"""
        active = self.list_secret_orders(status="active", minister_name=minister_name)
        pending = self.list_secret_orders(status="pending_review", minister_name=minister_name)
        return active + pending

    def close_secret_order(self, order_id: int, status: str, result: str, turn_closed: int) -> None:
        self.conn.execute(
            """
            UPDATE secret_orders
            SET status = ?, result = ?, turn_closed = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, result, turn_closed, int(order_id)),
        )
        self.conn.commit()
        tlog(f"[secret_order] close id={order_id} status={status}")

    def submit_secret_order_for_review(self, order_id: int, claim: str, year: int, period: int) -> bool:
        """大臣提交密令待推演核议：active → pending_review。
        claim 按月戳追加进 result 时间线（与 progress 同列，但带 "[提交核议]" 标记），
        让推演看时同时知道大臣自述。仅 active 状态可提交。"""
        row = self.conn.execute(
            "SELECT status FROM secret_orders WHERE id = ?", (int(order_id),)
        ).fetchone()
        if not row or row["status"] != "active":
            return False
        stamp = f"〔{period_label(year, period)}〕[提交核议] "
        note = (claim or "").strip()
        prev = self.conn.execute(
            "SELECT result FROM secret_orders WHERE id = ?", (int(order_id),)
        ).fetchone()["result"] or ""
        lines = [ln for ln in prev.split("\n") if ln.strip()]
        lines.append(f"{stamp}{note[:300]}")
        self.conn.execute(
            """
            UPDATE secret_orders
            SET status = 'pending_review', result = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            ("\n".join(lines), int(order_id)),
        )
        self.conn.commit()
        tlog(f"[secret_order] submit_for_review id={order_id} claim={note[:60]!r}")
        return True

    def _has_secret_order_period_line(self, order_id: int, column: str, year: int, period: int) -> bool:
        """本年月该列是否已有一行（用于一回合一步闸门）。"""
        stamp = f"〔{period_label(year, period)}〕"
        row = self.conn.execute(
            f"SELECT {column} AS v FROM secret_orders WHERE id = ?", (int(order_id),)
        ).fetchone()
        if row is None:
            return False
        return any(ln.startswith(stamp) for ln in str(row["v"] or "").split("\n"))

    def _append_secret_order_line(
        self, order_id: int, column: str, note: str, year: int, period: int,
        reject_if_same_period: bool = False,
    ) -> bool:
        """把一条带年月戳的进展/副作用追加进密令的 result/sim_note，存成历史时间线。
        reject_if_same_period=True 时，本年月已有行则拒写（返回 False，用于一回合一步）；
        否则同年月再写替换当月行。不同年月一律新增。返回是否实际写入。"""
        assert column in ("result", "sim_note")
        stamp = f"〔{period_label(year, period)}〕"
        row = self.conn.execute(
            f"SELECT {column} AS v FROM secret_orders WHERE id = ? AND status = 'active'",
            (int(order_id),),
        ).fetchone()
        if row is None:
            return False  # 已结案或不存在，不追加
        lines = [ln for ln in str(row["v"] or "").split("\n") if ln.strip()]
        if reject_if_same_period and any(ln.startswith(stamp) for ln in lines):
            return False  # 本回合已推过一步，拒
        lines = [ln for ln in lines if not ln.startswith(stamp)]  # 去掉当月旧行
        lines.append(f"{stamp}{note.strip()}")
        # 按〔年月〕戳排序，保证时间线顺序（同月替换后不致错位）
        def _stamp_key(ln: str):
            import re as _re
            m = _re.match(r"〔(\d+)年(\d+)月〕", ln)
            return (int(m.group(1)), int(m.group(2))) if m else (0, 0)
        lines.sort(key=_stamp_key)
        self.conn.execute(
            f"UPDATE secret_orders SET {column} = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            ("\n".join(lines), int(order_id)),
        )
        self.conn.commit()
        return True

    def update_secret_order_progress(
        self, order_id: int, progress_note: str, year: int = 0, period: int = 0
    ) -> bool:
        """承办人推进一步：按年月追加进 result 历史时间线，不改 status。
        一回合只能推一步——本回合已有进展行则拒（返回 False），不覆盖、不叠加。"""
        ok = self._append_secret_order_line(
            order_id, "result", progress_note, year, period, reject_if_same_period=True
        )
        tlog(f"[secret_order] progress id={order_id} ok={ok} note={progress_note[:40]!r}")
        return ok

    def update_secret_order_sim_note(
        self, order_id: int, sim_note: str, year: int = 0, period: int = 0
    ) -> None:
        """推演写密令副作用（泄漏/反弹等），按年月追加进 sim_note 历史时间线，
        不动 result/status。同月再写替换（推演每月一次）。与承办人进展分列。"""
        self._append_secret_order_line(order_id, "sim_note", sim_note, year, period)
        tlog(f"[secret_order] sim_note id={order_id} note={sim_note[:40]!r}")

    def rush_secret_order(
        self,
        order_id: int,
        state: GameState,
        deadline_months: int = 1,
        reason: str = "",
    ) -> Dict[str, object]:
        """缩短 active 密令期限。deadline_months<=0 表示本月立即送核议。"""
        row = self.conn.execute(
            "SELECT id, title, status, result, due_turn FROM secret_orders WHERE id = ?",
            (int(order_id),),
        ).fetchone()
        if row is None:
            raise ValueError("密令不存在")
        if row["status"] != "active":
            raise ValueError(f"当前状态 {row['status']}，不能催办")
        try:
            months = max(0, min(int(deadline_months or 0), 36))
        except (TypeError, ValueError):
            months = 1
        target_turn = int(state.turn) + months
        old_due = int(row["due_turn"] or 0)
        stamp = f"〔{period_label(state.year, state.period)}〕"
        why = (reason or "").strip()[:120] or "奉旨加急"
        prev = row["result"] or ""
        lines = [ln for ln in prev.split("\n") if ln.strip()]
        if months <= 0:
            lines.append(f"{stamp}[奉旨即核] {why}；本月即移交密旨核议。")
            self.conn.execute(
                """
                UPDATE secret_orders
                SET status = 'pending_review', due_turn = ?, result = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (int(state.turn), "\n".join(lines), int(order_id)),
            )
            status = "pending_review"
            due_turn = int(state.turn)
        else:
            due_turn = target_turn if old_due <= 0 else min(old_due, target_turn)
            lines.append(f"{stamp}[奉旨加急] {why}；御限改为 {months} 个月内核议。")
            self.conn.execute(
                """
                UPDATE secret_orders
                SET due_turn = ?, result = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (due_turn, "\n".join(lines), int(order_id)),
            )
            status = "active"
        self.conn.commit()
        tlog(f"[secret_order] rush id={order_id} old_due={old_due} due={due_turn} status={status}")
        return {"id": int(order_id), "title": row["title"], "status": status, "due_turn": due_turn}

    def get_secret_order(self, order_id: int) -> Optional[Dict[str, object]]:
        """单查一条密令（任意状态），给承办人查进度工具用。不存在返回 None。"""
        r = self.conn.execute(
            "SELECT * FROM secret_orders WHERE id = ?", (int(order_id),)
        ).fetchone()
        if not r:
            return None
        return {
            "id": int(r["id"]), "minister_name": r["minister_name"],
            "title": r["title"], "content": r["content"],
            "status": r["status"], "result": r["result"] or "",
            "sim_note": (r["sim_note"] if "sim_note" in r.keys() else "") or "",
            "turn_issued": int(r["turn_issued"]),
            "due_turn": int(r["due_turn"] if "due_turn" in r.keys() else 0),
            "turn_closed": r["turn_closed"],
        }

    def auto_submit_due_secret_orders(self, state: GameState) -> List[Dict[str, object]]:
        """把到期 active 密令自动转入 pending_review，保证当月推演必须给终判。"""
        rows = self.conn.execute(
            """
            SELECT id, title, result FROM secret_orders
            WHERE status = 'active' AND due_turn > 0 AND due_turn <= ?
            ORDER BY id
            """,
            (int(state.turn),),
        ).fetchall()
        submitted: List[Dict[str, object]] = []
        for row in rows:
            stamp = f"〔{period_label(state.year, state.period)}〕[期限届满] "
            note = "御限已至，移交月末密旨核议；据既有查办、风声与盘面定成败。"
            prev = row["result"] or ""
            lines = [ln for ln in prev.split("\n") if ln.strip()]
            if not any("[期限届满]" in ln for ln in lines):
                lines.append(f"{stamp}{note}")
            self.conn.execute(
                """
                UPDATE secret_orders
                SET status = 'pending_review', result = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                ("\n".join(lines), int(row["id"])),
            )
            submitted.append({"id": int(row["id"]), "title": row["title"]})
        if rows:
            self.conn.commit()
            tlog(f"[secret_order] auto_submit_due count={len(submitted)} ids={[x['id'] for x in submitted]}")
        return submitted

    def get_secret_orders_by_keywords(
        self, keywords: List[str], limit: int = 5, current_turn: int = 0
    ) -> List[Dict[str, object]]:
        """检索进行中（active）密令，tags LIKE 匹配，供推演 secret_orders 字段注入。
        完结/失败密令靠 event_memory（chat_message 来源）进入 relevant_memories，不在此返回。"""
        if not keywords:
            return self.list_secret_orders(status="active")[:limit]
        like_clauses = " OR ".join(["tags LIKE ?" for _ in keywords])
        like_params = [f"%{k}%" for k in keywords]
        rows = self.conn.execute(
            f"""
            SELECT * FROM secret_orders
            WHERE status = 'active' AND ({like_clauses})
            ORDER BY importance DESC, id DESC
            LIMIT ?
            """,
            like_params + [limit],
        ).fetchall()
        if not rows:
            return self.list_secret_orders(status="active")[:limit]
        return [
            {
                "id": int(r["id"]),
                "turn_issued": int(r["turn_issued"]),
                "year_issued": int(r["year_issued"]),
                "period_issued": int(r["period_issued"]),
                "minister_name": r["minister_name"],
                "title": r["title"],
                "content": r["content"],
                "tags": json.loads(r["tags"] or "[]") if isinstance(r["tags"], str) else (r["tags"] or []),
                "importance": int(r["importance"]),
                "status": r["status"],
                "result": r["result"] or "",
            }
            for r in rows
        ]

    # ----- chat_messages 补充查询 -----

    def get_chat_messages_for_turn(self, turn: int) -> Dict[str, List[Dict[str, str]]]:
        """查当月所有召对，按大臣分组，供 chat_memory agent 按人提取。"""
        rows = self.conn.execute(
            "SELECT minister_name, role, content FROM chat_messages WHERE turn = ? ORDER BY id",
            (int(turn),),
        ).fetchall()
        result: Dict[str, List[Dict[str, str]]] = {}
        for row in rows:
            result.setdefault(row["minister_name"], []).append(
                {"role": row["role"], "content": row["content"]}
            )
        return result

    def recent_chat_memory_for_minister(
        self,
        minister_name: str,
        *,
        upto_turn: int,
        window: int = 2,
        limit: int = 10,
    ) -> List[Dict[str, object]]:
        """Recent audience snippets that this NPC should remember.

        Includes both the NPC's own direct talks with the emperor and other
        ministers' audiences where this NPC is mentioned by name or alias. This
        is a read-only digest over existing chat_messages; it does not introduce
        a new runtime dependency or save schema.
        """
        name = str(minister_name or "").strip()
        if not name:
            return []
        start_turn = max(1, int(upto_turn) - max(1, int(window or 1)) + 1)
        mention_terms: List[str] = []

        def add_term(raw: object) -> None:
            term = str(raw or "").strip()
            if term and term not in mention_terms:
                mention_terms.append(term)

        add_term(name)
        character = self.content.characters.get(name) if self.content else None
        if character is None and self.content:
            for candidate in self.content.characters.values():
                if name in (candidate.aliases or []):
                    character = candidate
                    add_term(candidate.name)
                    break
        if character is not None:
            for alias in character.aliases or []:
                add_term(alias)
        mention_terms = mention_terms[:8]

        def like_term(term: str) -> str:
            escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            return f"%{escaped}%"

        direct_placeholders = ", ".join("?" for _ in mention_terms)
        mention_clauses = " OR ".join("content LIKE ? ESCAPE '\\'" for _ in mention_terms)
        rows = self.conn.execute(
            f"""
            SELECT id, turn, minister_name, role, content
            FROM chat_messages
            WHERE turn BETWEEN ? AND ?
              AND (minister_name IN ({direct_placeholders}) OR {mention_clauses})
            ORDER BY turn DESC, id DESC
            LIMIT ?
            """,
            (
                start_turn,
                int(upto_turn),
                *mention_terms,
                *(like_term(term) for term in mention_terms),
                max(1, int(limit or 10)),
            ),
        ).fetchall()
        direct_names = set(mention_terms)
        return [
            {
                "id": int(row["id"]),
                "turn": int(row["turn"]),
                "minister_name": str(row["minister_name"] or ""),
                "role": str(row["role"] or ""),
                "content": str(row["content"] or ""),
                "direct": str(row["minister_name"] or "") in direct_names,
            }
            for row in rows
        ]

    # ── 调试用通用 CRUD（仅限白名单核心表）──────────────────────
    # 表名 → 主键列。只暴露核心几张，防误删元数据/日志表。
    ADMIN_TABLES: Dict[str, str] = {
        "game_state": "id",        # 局势
        "metrics": "key",          # 国家修正（国库/内库/民心/皇威）
        "regions": "id",           # 地区
        "armies": "id",            # 军队
        "characters": "name",      # 人物
        "buildings": "id",         # 建筑
    }

    def admin_check_table(self, table: str) -> str:
        pk = self.ADMIN_TABLES.get(table)
        if pk is None:
            raise ValueError(f"表 {table!r} 不在调试白名单")
        return pk

    def admin_columns(self, table: str) -> List[Dict[str, object]]:
        """PRAGMA 取列定义：name/type/notnull/pk/default。"""
        self.admin_check_table(table)
        cur = self.conn.execute(f"PRAGMA table_info({table})")
        return [
            {
                "name": r["name"],
                "type": r["type"],
                "notnull": bool(r["notnull"]),
                "pk": bool(r["pk"]),
                "default": r["dflt_value"],
            }
            for r in cur.fetchall()
        ]

    def admin_rows(self, table: str) -> List[Dict[str, object]]:
        pk = self.admin_check_table(table)
        cur = self.conn.execute(f"SELECT * FROM {table} ORDER BY {pk}")
        return [dict(r) for r in cur.fetchall()]

    def _admin_valid_cols(self, table: str) -> set:
        return {c["name"] for c in self.admin_columns(table)}

    def admin_upsert(self, table: str, values: Dict[str, object]) -> Dict[str, object]:
        """按主键 INSERT OR REPLACE，返回落库后的行。只接受表内有的列。"""
        pk = self.admin_check_table(table)
        valid = self._admin_valid_cols(table)
        data = {k: v for k, v in values.items() if k in valid}
        if pk not in data or data[pk] in (None, ""):
            raise ValueError(f"缺主键 {pk}")
        cols = list(data.keys())
        placeholders = ",".join("?" for _ in cols)
        collist = ",".join(cols)
        self.conn.execute(
            f"INSERT OR REPLACE INTO {table} ({collist}) VALUES ({placeholders})",
            [data[c] for c in cols],
        )
        # 国库/内库同时落在 economy_accounts.balance，load_state 会用后者盖回 metrics。
        # 只改 metrics 表会在下回合被覆盖，故此处同步 economy_accounts。
        if table == "metrics" and data.get("key") in ("国库", "内库") and "value" in data:
            self.conn.execute(
                "UPDATE economy_accounts SET balance = ? WHERE account = ?",
                (int(data["value"]), data["key"]),
            )
        self.conn.commit()
        row = self.conn.execute(f"SELECT * FROM {table} WHERE {pk}=?", (data[pk],)).fetchone()
        return dict(row) if row else {}

    def admin_delete(self, table: str, pk_value: object) -> int:
        """按主键删行，返回受影响行数。"""
        pk = self.admin_check_table(table)
        cur = self.conn.execute(f"DELETE FROM {table} WHERE {pk}=?", (pk_value,))
        self.conn.commit()
        return cur.rowcount

    def close(self) -> None:
        self.conn.close()

    def backup_to(self, target_path: str) -> None:
        """SQLite backup API 热备到 target_path。不需关闭主连接。"""
        import os as _os
        _os.makedirs(_os.path.dirname(target_path) or ".", exist_ok=True)
        dest = sqlite3.connect(target_path)
        try:
            self.conn.commit()
            self.conn.backup(dest)
        finally:
            dest.close()
