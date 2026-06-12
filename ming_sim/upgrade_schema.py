"""升级总案（docs/upgrade-master-plan.md）新增表与新列的唯一迁移入口。L2。

所有 M1-M6 新系统共用此 schema；ensure_upgrade_schema(db) 幂等，可在 GameDB
init_schema 后随时调用。游戏状态扩展量（current_day/势/risk_aversion 等）走
kv_store，避免动 GameState dataclass 与 save_state/load_state 的旧契约。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ming_sim.db import GameDB

# kv_store 键名约定（timeflow/theater/memorials 共用）
KV_CURRENT_DAY = "upgrade.current_day"          # 绝对日序号：1 = 1627-10-01
KV_TIME_SPEED = "upgrade.time_speed"            # 0=暂停 1=常速 3=快速
KV_DEFICIT_STREAK = "upgrade.treasury_deficit_streak"  # 国库连月赤字计数
KV_MONTH_START_BALANCE = "upgrade.month_start_treasury"  # 月初国库余额（赤字判定基准）
KV_SHI = "upgrade.shi"                          # 势 0-100（S6）
KV_RISK_AVERSION = "upgrade.risk_aversion"      # 任事风险先验 0-100（S5）
KV_ATTENTION = "upgrade.attention_today"        # 今日注意力余量（S4）
KV_DAILY_BUDGET_CACHE = "upgrade.daily_budget_cache"   # 当月日摊预算缓存 JSON

DAYS_PER_MONTH = 30  # 抽象月长；turn = (current_day-1)//30 + 1

SHI_DEFAULT = 55
RISK_AVERSION_DEFAULT = 40
ATTENTION_PER_DAY = 12


def ensure_upgrade_schema(db: "GameDB") -> None:
    """建升级总案全部新表/新列。幂等。"""
    db.conn.executescript(
        """
        -- S1 调度表：半即时系统的心脏
        CREATE TABLE IF NOT EXISTS scheduled_resolutions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,            -- directive_step|event_check|report|agreement_due
                                           -- |intel_arrival|faction_move|memorial_arrival|seed_check
            due_day INTEGER NOT NULL,
            payload TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'pending',  -- pending|fired|cancelled
            requires_llm INTEGER NOT NULL DEFAULT 0,
            auto_pause INTEGER NOT NULL DEFAULT 0,   -- 0=蓝(角标) 1=黄(默认停) 2=红(必停)
            created_day INTEGER NOT NULL DEFAULT 0,
            fired_day INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_sched_due
            ON scheduled_resolutions(status, due_day);

        -- S3 账实分离：每个关键数字都有作者
        CREATE TABLE IF NOT EXISTS report_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_kind TEXT NOT NULL,     -- army|region|building|directive|treasury
            entity_id TEXT NOT NULL,
            field TEXT NOT NULL,
            reported_value REAL NOT NULL,
            actual_value REAL NOT NULL,
            author_org TEXT NOT NULL DEFAULT '',
            author_character TEXT NOT NULL DEFAULT '',
            reported_day INTEGER NOT NULL DEFAULT 0,
            note TEXT NOT NULL DEFAULT '',
            exposed INTEGER NOT NULL DEFAULT 0,      -- 1=已被盘库/密查揭穿
            exposed_day INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_report_entity
            ON report_ledger(entity_kind, entity_id, field);

        -- S4 奏疏流：御案系统
        CREATE TABLE IF NOT EXISTS memorials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            author_name TEXT NOT NULL DEFAULT '',
            org TEXT NOT NULL DEFAULT '',
            kind TEXT NOT NULL DEFAULT '请旨',  -- 请款|荐人|弹章|告变|请罪|请旨|密揭|捷报|陈情
            urgency INTEGER NOT NULL DEFAULT 2, -- 1低 2中 3高（红）
            summary TEXT NOT NULL DEFAULT '',
            full_text TEXT NOT NULL DEFAULT '',
            piaoni TEXT NOT NULL DEFAULT '',    -- 内阁票拟
            piaoni_author TEXT NOT NULL DEFAULT '',
            arrived_day INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending', -- pending|approved|denied|shelved|referred|expired
            shelved_days INTEGER NOT NULL DEFAULT 0,
            ref_kind TEXT NOT NULL DEFAULT '',  -- directive|issue|faction_move|intel|anomaly
            ref_id TEXT NOT NULL DEFAULT '',
            decision_note TEXT NOT NULL DEFAULT '',
            decided_day INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_memorials_status
            ON memorials(status, arrived_day);

        -- S10 因果伏笔：延迟代价
        CREATE TABLE IF NOT EXISTS causal_seeds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_day INTEGER NOT NULL DEFAULT 0,
            trigger_gate TEXT NOT NULL DEFAULT '{}',  -- issues 的 gate 语法 JSON；空={} 表示仅靠引信
            fuse_day INTEGER NOT NULL DEFAULT 0,      -- 引信日（绝对日，到日才开始检查 gate）
            payload TEXT NOT NULL DEFAULT '{}',       -- {event:{...Event 字段}} 或 {note:...}
            status TEXT NOT NULL DEFAULT 'armed',     -- armed|sprouted|defused
            note TEXT NOT NULL DEFAULT ''
        );

        -- S10 阈值触发去重/冷却
        CREATE TABLE IF NOT EXISTS threshold_firings (
            rule_id TEXT NOT NULL,
            object_id TEXT NOT NULL DEFAULT '',
            fired_day INTEGER NOT NULL,
            PRIMARY KEY (rule_id, object_id, fired_day)
        );

        -- S1 异步 LLM 结算队列（scheduler.py 后台 worker 消费）
        CREATE TABLE IF NOT EXISTS llm_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,            -- anomaly_text|settle_note|faction_move|piaoni
            payload TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'pending',  -- pending|running|done|failed
            result TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_llm_jobs_status ON llm_jobs(status, id);

        -- S5/S6 信念变量变动审计（势/任事意愿趋势线数据源）
        CREATE TABLE IF NOT EXISTS belief_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            day INTEGER NOT NULL,
            key TEXT NOT NULL,             -- shi|risk_aversion
            old_value INTEGER NOT NULL,
            new_value INTEGER NOT NULL,
            reason TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_belief_logs_day ON belief_logs(key, day);
        """
    )
    # S2 指令生命周期列（turn_directives 增列）
    for column, definition in {
        "lifecycle_status": "TEXT NOT NULL DEFAULT ''",   # ''(旧档/未颁)|in_transit|executing|stalled|done|aborted
        "category": "TEXT NOT NULL DEFAULT ''",
        "progress": "INTEGER NOT NULL DEFAULT 0",          # 0-100
        "lead_days": "INTEGER NOT NULL DEFAULT 0",
        "exec_days": "INTEGER NOT NULL DEFAULT 0",
        "start_day": "INTEGER NOT NULL DEFAULT 0",         # 颁诏日
        "eta_day": "INTEGER NOT NULL DEFAULT 0",           # 预计完成日
        "assignee": "TEXT NOT NULL DEFAULT ''",            # 主办官员
        "chain": "TEXT NOT NULL DEFAULT '[]'",             # 经手链条 JSON
        "integrity_actual": "INTEGER NOT NULL DEFAULT 100",    # 真实执行率（隐藏）
        "integrity_reported": "INTEGER NOT NULL DEFAULT 100",  # 奏报执行率（玩家可见）
        "anomaly": "TEXT NOT NULL DEFAULT ''",             # 当前未处置异常 JSON
        "settle_note": "TEXT NOT NULL DEFAULT ''",         # 完成结算叙事摘要
    }.items():
        db.ensure_column("turn_directives", column, definition)

    # S5/S8 官员扩展列
    for column, definition in {
        "grievance": "INTEGER NOT NULL DEFAULT 20",   # 怨气 0-100
        "emp_trust": "INTEGER NOT NULL DEFAULT 55",   # 对皇帝的信任 0-100
    }.items():
        db.ensure_column("characters", column, definition)

    # S7 派系扩展列
    db.ensure_column("factions", "heat", "INTEGER NOT NULL DEFAULT 20")  # 敌意度 0-100

    # 召对撤回的时间线闸门（审计修复 P1-3）：召对快照只覆盖业务表，不含 kv/奏疏/调度表；
    # 「召对→推进N天→撤回」会把执行中旨意回滚 N 天而日历不回滚。记快照日，跨日禁撤。
    db.ensure_column("chat_turns", "created_day", "INTEGER NOT NULL DEFAULT 0")

    db.conn.commit()


# ── kv 包装：升级状态量统一存取 ─────────────────────────────────────────────

def kv_int(db: "GameDB", key: str, default: int) -> int:
    raw = db.kv_get(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def kv_set_int(db: "GameDB", key: str, value: int) -> None:
    db.kv_set(key, str(int(value)))


def get_current_day(db: "GameDB", turn: int = 0) -> int:
    """读绝对日。旧档无值时按 turn 反推（落在该月第 1 天）并落库。"""
    raw = db.kv_get(KV_CURRENT_DAY)
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    day = max(0, int(turn) - 1) * DAYS_PER_MONTH + 1
    kv_set_int(db, KV_CURRENT_DAY, day)
    return day


def day_to_turn(day: int) -> int:
    return max(1, (int(day) - 1) // DAYS_PER_MONTH + 1)


def day_in_month(day: int) -> int:
    """月内第几天，1-30。"""
    return (int(day) - 1) % DAYS_PER_MONTH + 1


def day_to_ym(day: int) -> tuple[int, int]:
    """绝对日 → (公历年, 月)。day 1 = 1627年10月1日。"""
    months = (int(day) - 1) // DAYS_PER_MONTH
    total = (1627 * 12 + (10 - 1)) + months
    return total // 12, total % 12 + 1


def adjust_belief(db: "GameDB", key: str, delta: int, reason: str, *, day: int,
                  lo: int = 0, hi: int = 100) -> int:
    """统一修改 势/risk_aversion 并留审计日志。返回新值。key 用 KV_SHI / KV_RISK_AVERSION。"""
    defaults = {KV_SHI: SHI_DEFAULT, KV_RISK_AVERSION: RISK_AVERSION_DEFAULT}
    old = kv_int(db, key, defaults.get(key, 50))
    new = max(lo, min(hi, old + int(delta)))
    if new != old:
        kv_set_int(db, key, new)
        short = key.rsplit(".", 1)[-1]
        db.conn.execute(
            "INSERT INTO belief_logs (day, key, old_value, new_value, reason) VALUES (?,?,?,?,?)",
            (int(day), short, old, new, str(reason)[:120]),
        )
        db.conn.commit()
    return new
