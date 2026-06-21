#!/usr/bin/env python3
"""FastAPI web entry for Ming Salvage Sim.

薄壳：路由调 ming_sim.session.GameSession（与 CLI 共用同一流转层）。
拟旨 draft 待确认：大臣 propose_directive → pending → 前端 准/驳。
"""

from __future__ import annotations

import asyncio
import contextlib
from contextlib import closing
from contextvars import ContextVar
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import html
import json
import logging
import os
import queue
import random
import re
import secrets
import sqlite3
import threading
import time
import uuid
from typing import Any, AsyncIterator, Dict, Iterator, List, Optional, Tuple

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ming_sim.constants import ROOT_DIR
from ming_sim.paths import bundled_path, user_data_path, user_data_dir
from ming_sim.exceptions import ExitGame, LLMUnavailable
from ming_sim.llm_config import (
    load_llm_config,
    load_runtime_llm,
    normalize_openai_base_url,
    normalize_thinking_level,
    save_runtime_llm,
)
from ming_sim.agents import _dump_llm_messages
from ming_sim.bureaucracy import base_institution_specs, organization_diagnostics
from ming_sim.llm_model import extract_agent_text, verify_llm_available
from ming_sim.llm_contract import fail_if_llm_error
from ming_sim.issues import _format_issue_ongoing
from ming_sim.session import GameSession
from ming_sim.session import AUTO_SAVE_PREFIX, _parse_registered_secret_order_result
from ming_sim.skills import available_skill_ids, skill_display_name, skill_source_labels
from ming_sim.context import (
    match_minister_from_text,
    npc_network_profile,
    npc_network_recommendations,
)
from ming_sim.db import effective_stored_office_type, infer_office_type_from_office, normalize_office
from ming_sim.flows import compute_budget_lines
from ming_sim.personnel_actions import (
    convert_character_to_eunuch,
    convert_eunuch_to_commoner,
    is_eunuch_office,
)
from ming_sim.negotiation import (
    HANDSHAKE_BLOCKED,
    HANDSHAKE_CONDITIONAL,
    HANDSHAKE_SEALED,
    handshake_label,
)
from ming_sim.portraits import (
    DNA_SHEET_ASPECT_RATIO,
    GENERATED_PORTRAIT_PREFIX,
    NANO_BANANA_MODEL,
    PORTRAIT_ASPECT_RATIO,
    build_portrait_spec,
    detect_image_mime,
    image_data_url,
    nano_banana_generate_png,
    normalize_portrait_png,
)
from ming_sim.exceptions import LLMContractError  # noqa: F401  (保留：供错误处理)
from ming_sim.models import Character, LLMConfig
from ming_sim.web_payloads import (
    ARMY_FIELDS,
    BUILDING_FIELDS,
    CHARACTER_CARD_FIELDS,
    CHARACTER_INDEX_FIELDS,
    ISSUE_FIELDS,
    LEGACY_FIELDS,
    MAP_NODE_FIELDS,
    POWER_FIELDS,
    REGION_FIELDS,
    compact_armies,
    compact_map_nodes,
    compact_buildings,
    compact_character_cards,
    compact_character_index,
    compact_organization_payload,
    compact_issues,
    compact_legacies,
    compact_powers,
    compact_regions,
    monthly_followups_payload,
)
from ming_sim.web_payload_hooks import attach_state_payload, run_web_payload_hook

WEB_DIST = bundled_path("web", "dist")
# 用户上传的自定义立绘存档级目录（不随 build 清空，git 可忽略）。
# frozen 模式落 ~/.ming_sim/uploads/portraits/，源码模式落 <repo>/data/uploads/portraits/。
UPLOAD_PORTRAIT_DIR = user_data_path("uploads", "portraits")
# 自定义立绘 portrait_id 前缀；前端据此解析到 /portraits/custom/<name>.png。
CUSTOM_PORTRAIT_PREFIX = "custom:"
ALLOWED_PORTRAIT_TYPES = {"image/png", "image/jpeg", "image/webp"}
MAX_PORTRAIT_BYTES = 8 * 1024 * 1024  # 8MB 上限
GAME_START_YEAR = 1627
_PORTRAIT_KEY_PLACEHOLDERS = {
    "",
    "your_302_ai_key_here",
    "your_openai_image_key_here",
    "changeme",
    "change_me",
}


@dataclass(frozen=True)
class CharacterRuntimeIdentity:
    status: str
    status_reason: str
    status_label: str
    career_state: str
    office: str
    office_type: str
    faction: str
    portrait_id: str
    portrait_prefix: str
    power_id: str
    power_name: str
    birth_year: int
    summary: str


def _row_value(row: Optional[Any], key: str, default: Any = None) -> Any:
    if row is None:
        return default
    try:
        keys = row.keys()
        if key not in keys:
            return default
        return row[key]
    except Exception:
        try:
            return row[key]
        except Exception:
            return default


def _portrait_generation_configured() -> bool:
    key = (os.environ.get("NANO_BANANA_API_KEY", "").strip()
           or os.environ.get("OPENAI_IMAGE_KEY", "").strip())
    return key.lower() not in _PORTRAIT_KEY_PLACEHOLDERS


def _clean_obsidian_text(value: object) -> str:
    return re.sub(r"\[\[([^\]]+)\]\]", r"\1", str(value or "").strip())


# resolve/fail_condition 同时喂 extractor（需 input.factions/leverage 等技术 key）与展示给玩家。
# 展示前把技术词替换成中文，原文不动（LLM 仍读原文判定）。按长键先替，避免子串误伤。
_CONDITION_DISPLAY_REPLACEMENTS = [
    ("input.factions", "派系盘面"),
    ("input.classes", "阶级盘面"),
    ("input.regions", "地区盘面"),
    ("input.armies", "军队盘面"),
    ("input.current_state", "国势盘面"),
    ("region.", "地区："),
    ("army.", "军队："),
    ("faction.", "派系："),
    ("class.", "阶级："),
    ("power.", "势力："),
    ("maintenance_per_turn", "月饷"),
    ("registered_land", "已册田亩"),
    ("hidden_land", "隐田"),
    ("tax_per_turn", "月税"),
    ("public_support", "民心"),
    ("grain_security", "粮食"),
    ("unrest", "动乱"),
    ("gentry_resistance", "士绅阻力"),
    ("military_pressure", "边防压力"),
    ("supply", "补给"),
    ("morale", "士气"),
    ("training", "操练"),
    ("equipment", "军械"),
    ("arrears", "欠饷"),
    ("mobility", "机动"),
    ("loyalty", "忠诚"),
    ("controlled_by", "归属"),
    ("leverage", "影响力"),
    ("satisfaction", "满意度"),
    ("resolved", "达成"),
    ("failed", "失败"),
    ("region ", "地区 "),
    ("shenyang_liaoyang", "沈阳辽阳"),
    ("dongjiang_area", "东江海域"),
    ("mongol_chahar", "察哈尔蒙古"),
    ("beizhili", "北直隶"),
    ("nanzhili", "南直隶"),
    ("shandong", "山东"),
    ("shanxi", "山西"),
    ("henan", "河南"),
    ("shaanxi", "陕西"),
    ("zhejiang", "浙江"),
    ("jiangxi", "江西"),
    ("huguang", "湖广"),
    ("sichuan", "四川"),
    ("fujian", "福建"),
    ("guangdong", "广东"),
    ("guangxi", "广西"),
    ("yunnan", "云南"),
    ("guizhou", "贵州"),
    ("liaodong", "辽东"),
    ("dongjiang", "东江"),
    ("xuan_da", "宣大"),
    ("guanning", "关宁军"),
    ("jingying", "京营"),
    ("jizhen", "蓟镇"),
    ("houjin", "后金"),
    ("ming", "大明"),
    (".max", "最高值"),
    (".min", "最低值"),
    (".sum", "合计"),
    (".avg", "均值"),
    ("|", "、"),
    (".", "·"),
]


def _humanize_condition(text: str) -> str:
    """把结案/失败条件里的技术 key 替换成玩家可读中文（仅用于展示）。"""
    if not text:
        return text
    for src, dst in _CONDITION_DISPLAY_REPLACEMENTS:
        text = text.replace(src, dst)
    return text


_LEGACY_GATE_FIELD_LABELS = {
    "leverage": "影响力",
    "satisfaction": "满意度",
    "controlled_by": "归属",
    "hidden_land": "隐田",
    "gentry_resistance": "士绅阻力",
    "public_support": "民心",
    "unrest": "动乱",
    "military_pressure": "边防压力",
    "tax_per_turn": "税收",
    "morale": "士气",
    "training": "训练",
    "loyalty": "忠诚",
    "supply": "补给",
    "equipment": "装备",
}

_LEGACY_GATE_AGG_LABELS = {
    "max": "最高",
    "min": "最低",
    "sum": "合计",
    "avg": "平均",
}

_LEGACY_GATE_VALUE_LABELS = {
    "ming": "大明",
    "houjin": "后金",
    "bandits": "流寇",
}


def _legacy_gate_subject(raw_key: str, content: Any) -> str:
    parts = raw_key.split(".")
    if len(parts) < 3:
        return _humanize_condition(raw_key)
    scope, raw_ids, field = parts[0], parts[1], parts[2]
    agg = parts[3] if len(parts) > 3 else ""
    ids = [item for item in raw_ids.split("|") if item]
    if scope == "region":
        names = [getattr(content.regions.get(item), "name", item) for item in ids]
    elif scope == "faction":
        names = ids
    elif scope == "army":
        names = [getattr(content.armies.get(item), "name", item) for item in ids]
    else:
        names = ids
    entity = "、".join(str(name) for name in names)
    field_label = _LEGACY_GATE_FIELD_LABELS.get(field, _humanize_condition(field))
    agg_label = _LEGACY_GATE_AGG_LABELS.get(agg, "")
    return f"{entity}{field_label}{agg_label}"


def _humanize_legacy_gate(gate: Dict[str, str], content: Any) -> str:
    """把开局帝国修正的 clear_gate 转为中文展示文案。"""
    clauses: List[str] = []
    for raw_key, raw_expr in gate.items():
        subject = _legacy_gate_subject(str(raw_key), content)
        expr = str(raw_expr).strip()
        match = re.match(r"^(<=|>=|==|!=|<|>)\s*(.+)$", expr)
        if not match:
            clauses.append(f"{subject}达到 {expr}")
            continue
        op, value = match.groups()
        value = _LEGACY_GATE_VALUE_LABELS.get(value.strip(), value.strip())
        op_label = {
            "<=": "≤",
            ">=": "≥",
            "==": "为",
            "!=": "不为",
            "<": "<",
            ">": ">",
        }.get(op, op)
        clauses.append(f"{subject}{op_label}{value}")
    return "；".join(clauses)


def _legacy_effect_entity_name(scope: str, entity_id: str, content: Any) -> str:
    if scope == "regions":
        return str(getattr(content.regions.get(entity_id), "name", entity_id))
    if scope == "armies":
        return str(getattr(content.armies.get(entity_id), "name", entity_id))
    return entity_id


def _legacy_pct(value: int) -> str:
    return f"{'+' if value > 0 else ''}{value}%"


def _humanize_legacy_effect(modifiers: Dict[str, Any], content: Any) -> str:
    """把 legacy modifiers 转为中文展示，避免前端露出 nanzhili/guanning 等内部 id。"""
    parts: List[str] = []
    for account in ("国库", "内库", "民心", "皇威"):
        value = modifiers.get(account)
        if isinstance(value, (int, float)):
            parts.append(f"{account}{_legacy_pct(int(value))}")
    for scope in ("regions", "armies"):
        block = modifiers.get(scope)
        if not isinstance(block, dict):
            continue
        for entity_id, fields in block.items():
            if not isinstance(fields, dict):
                continue
            entity_name = _legacy_effect_entity_name(scope, str(entity_id), content)
            for field, value in fields.items():
                if not isinstance(value, (int, float)):
                    continue
                field_label = _LEGACY_GATE_FIELD_LABELS.get(str(field), _humanize_condition(str(field)))
                parts.append(f"{entity_name}{field_label}{_legacy_pct(int(value))}")
    return "、".join(parts)


def _delete_sqlite_db_files_or_raise(db_path: str) -> None:
    """删除 SQLite 主库及 WAL/SHM；失败时阻断重开，避免误读旧档。"""
    for suffix in ("", "-wal", "-shm"):
        target = db_path + suffix
        if not os.path.exists(target):
            continue
        if not os.path.isfile(target):
            raise HTTPException(
                status_code=500,
                detail=f"重开失败：无法清理主库文件 {target}，它不是普通文件。请检查该路径后再重试。",
            )
        try:
            os.remove(target)
        except PermissionError as exc:
            raise HTTPException(
                status_code=500,
                detail=(
                    f"重开失败：权限不足，无法删除主库文件 {target}。"
                    "请关闭占用该文件的程序，或用管理员权限运行游戏后重试。"
                ),
            ) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail=(
                    f"重开失败：无法删除主库文件 {target}。系统返回：{exc}。"
                    "请确认没有其他游戏进程占用该文件；若是权限问题，请用管理员权限运行游戏后重试。"
                ),
            ) from exc


def _prepare_sqlite_save_for_replace(source_path: str, db_path: str) -> str:
    """复制并校验存档，返回可 os.replace 到主库的临时 DB 路径。

    先准备临时文件再关闭/替换当前主库，避免无效存档破坏正在运行的进度。
    """
    import shutil
    import sqlite3 as _sqlite3
    import tempfile

    db_dir = os.path.dirname(db_path) or "."
    os.makedirs(db_dir, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".ming-load-", suffix=".db", dir=db_dir)
    os.close(fd)
    try:
        shutil.copy2(source_path, temp_path)
        try:
            conn = _sqlite3.connect(temp_path)
            try:
                row = conn.execute("PRAGMA quick_check").fetchone()
                if row is None or str(row[0]).lower() != "ok":
                    detail = row[0] if row else "无返回"
                    raise HTTPException(status_code=400, detail=f"存档校验失败：{detail}")
                required = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('game_state','characters','kv_store')"
                ).fetchall()
                if len(required) < 3:
                    raise HTTPException(status_code=400, detail="存档缺少必要表，不能加载。")
            finally:
                conn.close()
        except _sqlite3.DatabaseError as exc:
            raise HTTPException(status_code=400, detail=f"存档不是有效 SQLite 数据库：{exc}") from exc
        return temp_path
    except Exception:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise


def _replace_main_db_with_prepared_save(prepared_path: str, db_path: str) -> None:
    """用已校验的临时 DB 原子替换主库，并清理旧 WAL/SHM。"""
    try:
        os.replace(prepared_path, db_path)
        for suffix in ("-wal", "-shm"):
            try:
                os.remove(db_path + suffix)
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise HTTPException(
                    status_code=500,
                    detail=f"加载存档失败：无法清理旧数据库日志 {db_path + suffix}。系统返回：{exc}。",
                ) from exc
    except Exception:
        if os.path.exists(prepared_path):
            try:
                os.remove(prepared_path)
            except OSError:
                pass
        raise


def _verify_llm_configs_or_raise(config: LLMConfig) -> None:
    """校验主模型；若配置了 advanced_model，也用其实际 base/key 单独校验。"""
    try:
        verify_llm_available(config)
    except LLMUnavailable as e:
        raise HTTPException(status_code=400, detail=_llm_error_detail(e, "主模型连通性检查失败：")) from None
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=_llm_error_detail(e, "主模型连通性检查失败：")) from None

    advanced_model = (config.advanced_model or "").strip()
    if not advanced_model:
        return
    advanced_config = LLMConfig(
        api_key=(config.advanced_api_key or "").strip() or config.api_key,
        base_url=(config.advanced_base_url or "").strip() or config.base_url,
        model=advanced_model,
        max_tokens=config.max_tokens,
        timeout_seconds=config.timeout_seconds,
        thinking_level=config.advanced_thinking_level,
        advanced_model=config.advanced_model,
        advanced_base_url=config.advanced_base_url,
        advanced_api_key=config.advanced_api_key,
        advanced_thinking_level=config.advanced_thinking_level,
    )
    try:
        verify_llm_available(advanced_config)
    except LLMUnavailable as e:
        raise HTTPException(status_code=400, detail=_llm_error_detail(e, "高级模型连通性检查失败：")) from None
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=_llm_error_detail(e, "高级模型连通性检查失败：")) from None


def _llm_error_detail(exc: Exception, prefix: str = "") -> Dict[str, Any]:
    message = f"{prefix}{exc.message if hasattr(exc, 'message') else str(exc)}"
    return {
        "code": getattr(exc, "code", "llm_error"),
        "message": message,
        "provider_message": getattr(exc, "provider_message", str(exc)),
        "status_code": getattr(exc, "status_code", None),
    }


class ChatRequest(BaseModel):
    message: str
    context: Optional[Dict[str, Any]] = None


class ConversationGoalAbandonRequest(BaseModel):
    reason: str = ""


class DirectiveRequest(BaseModel):
    text: str
    notes: str = ""


class SecretOrderRequest(BaseModel):
    title: str
    content: str
    tags: List[str] = []
    deadline_months: int = 0


class DirectivePatch(BaseModel):
    text: Optional[str] = None
    notes: Optional[str] = None


class CustomInstitutionRequest(BaseModel):
    name: str
    category: str = "非常规"
    mandate: str = ""
    slots: List[str] = []


class FillVacancyRequest(BaseModel):
    institution_id: str
    slot_title: str
    method: str = "auto"


class CastrateRequest(BaseModel):
    name: str
    force: bool = False
    scheme_text: str = ""


class AgreementTaskPatch(BaseModel):
    status: str
    evidence: str = ""


class ConsortActionRequest(BaseModel):
    action: str


class TimeAdvanceRequest(BaseModel):
    days: int = 1
    stop_on_yellow: bool = True


class TimeSpeedRequest(BaseModel):
    speed: int = 1


class DirectiveInterveneRequest(BaseModel):
    action: str  # cuiban|reassign|fund|ducai|abort|bargain_blocker|pressure_blocker
    new_assignee: str = ""
    fund: int = 0


class MemorialDecideRequest(BaseModel):
    action: str  # approve|deny|shelve|refer
    note: str = ""


class PunishRequest(BaseModel):
    name: str
    severity: str  # light|heavy|execute
    public: bool = True
    reason: str = ""


class BackRequest(BaseModel):
    name: str
    kind: str  # shoulder|comfort|reuse
    cost: int = 0


class InvestigateRequest(BaseModel):
    line: str  # changwei|kedao
    target_kind: str  # army|character|directive
    target_id: str


class SignalRequest(BaseModel):
    kind: str  # tingzhang|zuiji|xianfu
    target: str = ""


class WebGame:
    """Web 端会话包装：持一个 GameSession + 网页专属态（聊天历史、收藏）。"""

    _CHAT_MENTION_BLOCKED_ALIASES = {
        "朝廷", "内廷", "外朝", "宫中", "宫里", "厂卫",
        "内阁", "司礼", "司礼监", "东厂", "锦衣卫", "北镇抚司", "南镇抚司", "镇抚司",
        "吏部", "户部", "礼部", "兵部", "刑部", "工部", "都察院", "翰林院", "詹事府",
        "大理寺", "太常寺", "光禄寺", "内官监", "御马监", "内书堂", "文书房", "南镇抚司",
        "南户部", "南京户部", "南京兵部", "南京礼部", "南京吏部", "南京工部", "南京刑部",
        "首辅", "次辅", "阁老", "前首辅", "原首辅", "大学士", "尚书", "侍郎",
        "掌印", "秉笔", "掌印太监", "秉笔太监", "都指挥使", "督师", "经略", "总督", "巡抚",
        "提督", "少司马", "本兵", "都督", "指挥", "百户", "千户", "内官", "内侍", "太监",
        "知府", "知县", "御史", "郎中", "主事", "监军", "总兵", "副将", "游击", "把总",
        "司礼监掌印", "司礼监秉笔", "司礼监文书房", "锦衣卫千户", "锦衣卫百户", "南镇抚司试百户",
    }
    _CHAT_MENTION_ORG_TOKENS = (
        "司礼", "司礼监", "东厂", "锦衣卫", "镇抚司", "内阁", "都察院", "翰林院", "詹事府",
        "大理寺", "太常寺", "光禄寺", "内官监", "御马监", "内书堂", "文书房", "南京",
    )
    _CHAT_MENTION_ORG_SUFFIXES = (
        "监", "部", "院", "寺", "厂", "卫", "司", "府", "衙", "局", "营", "镇", "房", "堂",
    )
    _CHAT_MENTION_SURNAME_TITLE_SUFFIXES = (
        "吏部", "户部", "礼部", "兵部", "刑部", "工部",
        "首辅", "次辅", "阁老", "大学士", "尚书", "侍郎", "掌印", "秉笔",
        "厂臣", "督师", "经略", "总督", "巡抚", "提督", "少司马", "本兵",
        "都督", "指挥", "百户", "千户", "公公", "伴伴",
        "太监", "内侍", "知府", "知县", "御史", "郎中", "主事", "监军",
    )
    _CHAT_MENTION_TITLE_ONLY_SUFFIXES = (
        "首辅", "次辅", "阁老", "大学士", "尚书", "侍郎", "掌印", "秉笔",
        "厂臣", "督师", "经略", "总督", "巡抚", "提督", "少司马", "本兵",
        "都督", "指挥", "百户", "千户",
    )

    def __init__(self, fresh: bool = False, username: str = "") -> None:
        """实例化 = 真正进入游戏。无 API key 直接抛 LLMUnavailable。
        fresh=True：先清空主 DB（新游戏）再建 session。"""
        self.username = username.strip()
        self.user_id = _safe_user_id(self.username) if self.username else ""
        db_path = _db_path_for_user(self.username)
        base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        model = os.environ.get("OPENAI_MODEL", "deepseek-v4-flash")
        api_key = os.environ.get("OPENAI_API_KEY", "")
        advanced_model = os.environ.get("OPENAI_ADVANCED_MODEL", "")
        advanced_base_url = os.environ.get("OPENAI_ADVANCED_BASE_URL", "")
        advanced_api_key = os.environ.get("OPENAI_ADVANCED_API_KEY", "")
        thinking_level = os.environ.get("OPENAI_THINKING_LEVEL", "")
        advanced_thinking_level = os.environ.get("OPENAI_ADVANCED_THINKING_LEVEL", "")
        timeout_seconds = float(os.environ.get("OPENAI_TIMEOUT_SECONDS", "180") or 180)
        # 菜单写的 runtime_llm.json 优先于 env，让"在网页里改的配置"重启后仍生效。
        runtime = load_runtime_llm()
        base_url = runtime.get("base_url") or base_url
        model = runtime.get("model") or model
        api_key = runtime.get("api_key") or api_key
        thinking_level = runtime.get("thinking_level") or thinking_level
        advanced_model = runtime.get("advanced_model") or advanced_model
        advanced_base_url = runtime.get("advanced_base_url") or advanced_base_url
        advanced_api_key = runtime.get("advanced_api_key") or advanced_api_key
        advanced_thinking_level = runtime.get("advanced_thinking_level") or advanced_thinking_level
        max_tokens = int(runtime.get("max_tokens") or 8000)
        timeout_seconds = float(runtime.get("timeout_seconds") or timeout_seconds)
        if not api_key:
            raise LLMUnavailable("未配 API key，请先到设置页填写。")
        random.seed(int(os.environ.get("MING_SIM_SEED", "7")))
        self.character_rng = random.SystemRandom()
        self.turn_resolution_lock = threading.Lock()
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self.db_path = db_path
        if fresh:
            _delete_sqlite_db_files_or_raise(db_path)
        adv_base = (advanced_base_url or "").strip()
        llm_config = LLMConfig(
            api_key=api_key,
            base_url=normalize_openai_base_url(base_url),
            model=model,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            thinking_level=normalize_thinking_level(thinking_level),
            advanced_model=(advanced_model or "").strip(),
            advanced_base_url=normalize_openai_base_url(adv_base) if adv_base else "",
            advanced_api_key=(advanced_api_key or "").strip(),
            advanced_thinking_level=normalize_thinking_level(advanced_thinking_level),
        )
        self.session = GameSession(db_path, llm_config)
        self.session.begin_turn()
        # 召对记录完整持久化在 chat_messages 表；Web 进程只恢复最近窗口，避免长期运行时搬运全文历史。
        self.chat_history: Dict[str, List[Dict[str, Any]]] = {}
        self._restore_chat_history_cache()
        _DEFAULT_FAVORITES = {"王承恩", "曹化淳", "李若琏", "魏忠贤", "田尔耕"}
        _fav_raw = self.db.kv_get("favorites")
        self.favorites: set = set(json.loads(_fav_raw)) if _fav_raw else set(_DEFAULT_FAVORITES)
        if not _fav_raw:
            self.db.kv_set("favorites", json.dumps(sorted(self.favorites)))
        # 异步 LLM 结算队列（升级总案 S1.4）：每存档库一个 daemon worker，
        # 消费旨意异常陈情/办结奏报等文案任务；LLM 失败有模板兜底，不阻塞主流程。
        try:
            from ming_sim.scheduler import ensure_worker
            ensure_worker(self.db_path, llm_config)
        except Exception as exc:
            print(f"[WARN] LLM 队列 worker 未启动：{exc}")

    def _restore_chat_history_cache(self) -> None:
        self.chat_history = {name: [] for name in self.session.content.characters}
        loaded = self.db.load_recent_chat_history(_web_chat_history_limit())
        for name, msgs in loaded.items():
            self.chat_history.setdefault(name, []).extend(msgs)

    def _prune_chat_history(self, minister_name: str) -> None:
        limit = _web_chat_history_limit()
        history = self.chat_history.get(minister_name, [])
        if len(history) > limit:
            self.chat_history[minister_name] = history[-limit:]

    # ── 存档管理 ─────────────────────────────────────────────────────────
    def saves_dir(self) -> str:
        return _saves_dir_for_user(self.username)

    def list_saves(self) -> List[Dict[str, Any]]:
        campaign_id = (self.db.kv_get("campaign_id") or "").strip()
        out = []
        for item in _scan_saves(self.saves_dir()):
            row = dict(item)
            save_campaign = str(row.get("campaign_id") or "")
            row["current"] = bool(save_campaign and save_campaign == campaign_id)
            out.append(row)
        return out

    def _safe_save_name(self, name: str) -> str:
        cleaned = "".join(c for c in name.strip() if c.isalnum() or c in "._-")
        if not cleaned or cleaned.startswith("."):
            raise HTTPException(status_code=400, detail="存档名非法。仅允许字母/数字/._- ")
        return cleaned

    def save_to(self, name: str) -> Dict[str, Any]:
        safe = self._safe_save_name(name)
        target = os.path.join(self.saves_dir(), f"{safe}.db")
        self.db.backup_to(target)
        return {"name": safe, "path": target}

    def delete_save(self, name: str) -> None:
        safe = self._safe_save_name(name)
        target = os.path.join(self.saves_dir(), f"{safe}.db")
        if not os.path.isfile(target):
            raise HTTPException(status_code=404, detail="存档不存在。")
        os.remove(target)

    def reset_game(self) -> None:
        """全清主 DB：停队列 worker → 关连接 → 删 sqlite 主/wal/shm → 重建空 session。
        存档目录不动。"""
        from ming_sim.scheduler import stop_worker
        stop_worker(self.db_path)
        try:
            self.session.close()
        except Exception:
            pass
        _delete_sqlite_db_files_or_raise(self.db_path)
        self._rebuild_session(self.session.llm_config)

    def load_save(self, name: str) -> None:
        """从存档热替换主 DB：备份当前 → 拷源到主 DB → 重建 session。"""
        safe = self._safe_save_name(name)
        source = os.path.join(self.saves_dir(), f"{safe}.db")
        if not os.path.isfile(source):
            raise HTTPException(status_code=404, detail="存档不存在。")
        prepared = _prepare_sqlite_save_for_replace(source, self.db_path)
        # 先停队列 worker 并关闭当前 session 的 DB 连接，
        # 避免 Windows/某些平台上的 file lock（worker 持有独立 sqlite 连接）。
        from ming_sim.scheduler import stop_worker
        stop_worker(self.db_path)
        try:
            self.session.close()
        except Exception:
            pass
        _replace_main_db_with_prepared_save(prepared, self.db_path)
        self._rebuild_session(self.session.llm_config)

    def _rebuild_session(self, llm_config: LLMConfig) -> None:
        """用新 llm_config（或换完 DB 后）重建 GameSession + 内存缓存 + 队列 worker。"""
        verify_llm_available(llm_config)
        self.session = GameSession(self.db_path, llm_config)
        self.session.begin_turn()
        # 换档/改配置后重挂队列 worker（旧 worker 已由调用方 stop；此处幂等并热更配置）
        try:
            from ming_sim.scheduler import ensure_worker
            ensure_worker(self.db_path, llm_config)
        except Exception as exc:
            print(f"[WARN] LLM 队列 worker 未启动：{exc}")
        self._restore_chat_history_cache()
        _DEFAULT_FAVORITES = {"王承恩", "曹化淳", "李若琏", "魏忠贤", "田尔耕"}
        _fav_raw = self.db.kv_get("favorites")
        self.favorites = set(json.loads(_fav_raw)) if _fav_raw else set(_DEFAULT_FAVORITES)
        if not _fav_raw:
            self.db.kv_set("favorites", json.dumps(sorted(self.favorites)))

    def apply_llm_config(
        self,
        base_url: str,
        model: str,
        api_key: str,
        max_tokens: int = 0,
        timeout_seconds: float = 0,
        thinking_level: Optional[str] = None,
        advanced_model: Optional[str] = None,
        advanced_base_url: Optional[str] = None,
        advanced_api_key: Optional[str] = None,
        advanced_thinking_level: Optional[str] = None,
    ) -> LLMConfig:
        base = normalize_openai_base_url(base_url.strip() or self.session.llm_config.base_url)
        new_model = model.strip() or self.session.llm_config.model
        new_key = api_key.strip() or self.session.llm_config.api_key
        new_max = max_tokens if max_tokens > 0 else self.session.llm_config.max_tokens
        new_timeout = timeout_seconds if timeout_seconds > 0 else self.session.llm_config.timeout_seconds
        if thinking_level is None:
            new_thinking_level = self.session.llm_config.thinking_level
        else:
            new_thinking_level = normalize_thinking_level(thinking_level)
        # advanced_* = None 表示不动；传空串表示显式清空。
        if advanced_model is None:
            new_advanced = self.session.llm_config.advanced_model
        else:
            new_advanced = advanced_model.strip()
        if advanced_base_url is None:
            new_adv_base = self.session.llm_config.advanced_base_url
        else:
            adv_base_in = advanced_base_url.strip()
            new_adv_base = normalize_openai_base_url(adv_base_in) if adv_base_in else ""
        if advanced_api_key is None:
            new_adv_key = self.session.llm_config.advanced_api_key
        else:
            new_adv_key = advanced_api_key.strip()
        if advanced_thinking_level is None:
            new_adv_thinking_level = self.session.llm_config.advanced_thinking_level
        else:
            new_adv_thinking_level = normalize_thinking_level(advanced_thinking_level)
        new_config = LLMConfig(
            api_key=new_key,
            base_url=base,
            model=new_model,
            max_tokens=new_max,
            timeout_seconds=new_timeout,
            thinking_level=new_thinking_level,
            advanced_model=new_advanced,
            advanced_base_url=new_adv_base,
            advanced_api_key=new_adv_key,
            advanced_thinking_level=new_adv_thinking_level,
        )
        _verify_llm_configs_or_raise(new_config)
        save_runtime_llm(
            new_config.base_url,
            new_config.model,
            new_config.api_key,
            new_config.max_tokens,
            new_config.timeout_seconds,
            new_config.thinking_level,
            new_config.advanced_model,
            new_config.advanced_base_url,
            new_config.advanced_api_key,
            new_config.advanced_thinking_level,
        )
        self.session.llm_config = new_config
        # 重建 registry 让大臣 Agent 用新配置
        self.session.begin_turn()
        return new_config

    # ── 便捷属性 ──────────────────────────────────────────────────────────
    @property
    def db(self):
        return self.session.db

    @property
    def state(self):
        return self.session.state

    @property
    def content(self):
        return self.session.content

    @property
    def previous_summary(self) -> str:
        return self.session.previous_summary

    @property
    def last_decree(self) -> str:
        return self.session.last_decree

    @property
    def last_report(self) -> str:
        return self.session.last_report

    def refresh_turn(self) -> None:
        self.session.begin_turn()

    # ── 自定义立绘 ────────────────────────────────────────────────────────
    def find_character(self, name: str) -> Optional[Character]:
        return self.content.characters.get(name)

    def set_custom_portrait(self, name: str, portrait_id: str) -> None:
        """落库并回写内存：把某人物 portrait_id 指向自定义立绘。"""
        self.db.set_portrait_id(name, portrait_id)
        character = self.content.characters.get(name)
        if character is not None:
            character.portrait_id = portrait_id

    def portrait_generation_signatures(self) -> Dict[str, str]:
        signatures: Dict[str, str] = {}
        for name, character in self.content.characters.items():
            signatures[name] = build_portrait_spec(character, self.state, self.session.campaign_id).asset_id
        return signatures

    def queue_portrait_generation_for_signature_changes(
        self,
        before: Dict[str, str],
        reason: str = "职服变化",
    ) -> List[Dict[str, Any]]:
        queued: List[Dict[str, Any]] = []
        if not _portrait_generation_configured():
            return queued
        rows = self.db.conn.execute(
            "SELECT name FROM characters WHERE status='active' AND power_id='ming'"
        ).fetchall()
        for row in rows:
            name = str(row["name"] or "")
            character = self.find_character(name)
            if character is None:
                continue
            after = build_portrait_spec(character, self.state, self.session.campaign_id).asset_id
            if before.get(name) == after:
                continue
            try:
                queued.append(self.queue_portrait_generation(name, reason))
            except Exception as exc:  # noqa: BLE001 - portrait queue must not break turn settlement
                print(f"[WARN] 立绘重绘排队失败 {name}: {exc}")
        return queued

    def queue_portrait_generation(self, name: str, reason: str = "manual") -> Dict[str, Any]:
        if not _portrait_generation_configured():
            raise HTTPException(status_code=409, detail="未配置 NANO_BANANA_API_KEY，无法生成立绘。")
        character = self.find_character(name)
        if character is None:
            raise HTTPException(status_code=404, detail=f"未找到人物：{name}")
        spec = build_portrait_spec(character, self.state, self.session.campaign_id)
        portrait_id = f"{GENERATED_PORTRAIT_PREFIX}{spec.asset_id}"
        dna_existing = self.db.get_portrait_asset(spec.dna_asset_id)
        dna_status = str(dna_existing["status"] or "") if dna_existing is not None else "missing"
        should_generate_dna = (
            dna_existing is None
            or dna_status == "error"
            or (dna_status == "ready" and dna_existing["image_blob"] is None)
        )
        if should_generate_dna:
            self.db.upsert_portrait_asset(
                asset_id=spec.dna_asset_id,
                character_name=character.name,
                kind="dna",
                dna_seed=spec.dna_seed,
                wardrobe_key="dna_sheet",
                prompt=spec.dna_prompt,
                provider="302.ai",
                model=NANO_BANANA_MODEL,
                status="pending",
                updated_turn=self.state.turn,
                error="",
            )
        existing = self.db.get_portrait_asset(spec.asset_id)
        if existing is not None and str(existing["status"] or "") == "ready" and existing["image_blob"] is not None:
            self.set_custom_portrait(character.name, portrait_id)
            if should_generate_dna:
                def _dna_worker() -> None:
                    try:
                        dna_png = normalize_portrait_png(
                            nano_banana_generate_png(
                                spec.dna_prompt,
                                aspect_ratio=DNA_SHEET_ASPECT_RATIO,
                                reference_images=spec.dna_reference_images,
                            ),
                            target_width=768,
                            target_aspect_ratio=DNA_SHEET_ASPECT_RATIO,
                            cutout_background=False,
                        )
                        self.db.mark_portrait_asset_ready(
                            spec.dna_asset_id,
                            dna_png,
                            mime_type=detect_image_mime(dna_png),
                        )
                    except Exception as exc:  # noqa: BLE001 - background job records player-facing status
                        self.db.mark_portrait_asset_error(spec.dna_asset_id, str(exc))

                threading.Thread(target=_dna_worker, name=f"portrait-dna-{spec.dna_asset_id}", daemon=True).start()
            return {
                "name": character.name,
                "portrait_id": portrait_id,
                "asset_id": spec.asset_id,
                "dna_asset_id": spec.dna_asset_id,
                "status": "ready",
                "dna_status": "pending" if should_generate_dna else dna_status or "ready",
                "dna_seed": spec.dna_seed,
                "wardrobe_key": spec.wardrobe_key,
            }
        self.db.upsert_portrait_asset(
            asset_id=spec.asset_id,
            character_name=character.name,
            kind="portrait",
            dna_seed=spec.dna_seed,
            wardrobe_key=spec.wardrobe_key,
            prompt=spec.prompt,
            provider="302.ai",
            model=NANO_BANANA_MODEL,
            status="pending",
            updated_turn=self.state.turn,
            error="",
        )
        self.set_custom_portrait(character.name, portrait_id)

        def _worker() -> None:
            try:
                dna_ref = ""
                if should_generate_dna:
                    dna_png = normalize_portrait_png(
                        nano_banana_generate_png(
                            spec.dna_prompt,
                            aspect_ratio=DNA_SHEET_ASPECT_RATIO,
                            reference_images=spec.dna_reference_images,
                        ),
                        target_width=768,
                        target_aspect_ratio=DNA_SHEET_ASPECT_RATIO,
                        cutout_background=False,
                    )
                    self.db.mark_portrait_asset_ready(spec.dna_asset_id, dna_png, mime_type=detect_image_mime(dna_png))
                    dna_ref = image_data_url(dna_png, detect_image_mime(dna_png))
                elif dna_existing is not None and dna_existing["image_blob"] is not None:
                    dna_bytes = bytes(dna_existing["image_blob"])
                    dna_ref = image_data_url(dna_bytes, str(dna_existing["mime_type"] or detect_image_mime(dna_bytes)))
                portrait_refs = ((dna_ref,) if dna_ref else ()) + tuple(spec.reference_images)
                png = normalize_portrait_png(
                    nano_banana_generate_png(
                        spec.prompt,
                        aspect_ratio=PORTRAIT_ASPECT_RATIO,
                        reference_images=portrait_refs,
                    ),
                    target_width=512,
                    target_aspect_ratio=PORTRAIT_ASPECT_RATIO,
                    cutout_background=True,
                )
                self.db.mark_portrait_asset_ready(spec.asset_id, png, mime_type=detect_image_mime(png))
            except Exception as exc:  # noqa: BLE001 - background job records player-facing status
                if should_generate_dna:
                    self.db.mark_portrait_asset_error(spec.dna_asset_id, str(exc))
                self.db.mark_portrait_asset_error(spec.asset_id, str(exc))

        threading.Thread(target=_worker, name=f"portrait-{spec.asset_id}", daemon=True).start()
        return {
            "name": character.name,
            "portrait_id": portrait_id,
            "asset_id": spec.asset_id,
            "dna_asset_id": spec.dna_asset_id,
            "status": "pending",
            "dna_status": "pending" if should_generate_dna else dna_status or "ready",
            "dna_seed": spec.dna_seed,
            "wardrobe_key": spec.wardrobe_key,
            "reason": reason,
        }

    def maybe_queue_portrait_generation(self, name: str, reason: str = "manual") -> Optional[Dict[str, Any]]:
        """Best-effort portrait refresh for gameplay side effects.

        Portrait generation is optional; when no image key is configured, keep
        existing static or pool portraits instead of turning them into failed
        generated assets.
        """
        if not _portrait_generation_configured():
            return None
        try:
            return self.queue_portrait_generation(name, reason)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 - image refresh must not block gameplay mutation
            print(f"[WARN] 立绘重绘排队失败 {name}: {exc}")
            return None

    # ── 序列化 ────────────────────────────────────────────────────────────
    def _public_stance_note_payload(self, row: Dict[str, Any] | Any) -> Dict[str, Any]:
        """玩家可见的奏对立场：保留证据与风险，隐藏月末推演用字段。"""
        item = dict(row)
        if "evidence" not in item:
            try:
                evidence = json.loads(str(item.get("evidence_json") or "{}"))
            except (TypeError, ValueError):
                evidence = {}
            item["evidence"] = evidence if isinstance(evidence, dict) else {}
        if "psychological" not in item:
            try:
                psychological = json.loads(str(item.get("psychological_json") or "{}"))
            except (TypeError, ValueError):
                psychological = {}
            item["psychological"] = psychological if isinstance(psychological, dict) else {}
        if "risk_tags_list" not in item:
            raw_tags = str(item.get("risk_tags") or "")
            item["risk_tags_list"] = [part for part in re.split(r"[、,，;；\s]+", raw_tags) if part]
        for private_key in (
            "evidence_json",
            "risk_tags",
            "execution_hint",
            "source_chat_turn_id",
            "psychological_json",
        ):
            item.pop(private_key, None)
        item.pop("_rn", None)
        return item

    def _public_stance_notes(self, minister_name: str, *, limit: int = 3) -> List[Dict[str, Any]]:
        return [
            self._public_stance_note_payload(row)
            for row in self.db.list_minister_stances(
            turn=self.state.turn,
            minister_name=minister_name,
            limit=limit,
            )
        ]

    def _age_payload(self, character: Character, birth_year_override: int = 0) -> Dict[str, Any]:
        birth_year = int(birth_year_override or getattr(character, "birth_year", 0) or 0)
        start_age = GAME_START_YEAR - birth_year if birth_year > 0 else 0
        if start_age <= 0:
            start_age = 0
        return {
            "birth_year": birth_year,
            "start_age": start_age,
            "age_label": f"开局{start_age}岁" if start_age else "开局年龄未详",
        }

    def _character_identity(
        self,
        character: Character,
        *,
        runtime_row: Optional[Any] = None,
        runtime_rows: Optional[Dict[str, Any]] = None,
    ) -> CharacterRuntimeIdentity:
        row = runtime_row
        if row is None and runtime_rows is not None:
            row = runtime_rows.get(character.name)
        if row is None:
            row = self.db.conn.execute(
                """
                SELECT office, office_type, faction, portrait_id, power_id, birth_year, status, status_reason
                FROM characters
                WHERE name=?
                """,
                (character.name,),
            ).fetchone()

        status = str(_row_value(row, "status", getattr(character, "status", "active")) or "active")
        status_reason = str(_row_value(row, "status_reason", "") or "")
        status_label = _STATUS_LABEL_WEB.get(status, "在朝" if status == "active" else status)
        office = str(_row_value(row, "office", character.office) or "")
        office_type = str(_row_value(row, "office_type", character.office_type) or character.office_type)
        office_type = effective_stored_office_type(office, office_type)
        faction = str(_row_value(row, "faction", character.faction) or character.faction)
        portrait_id = str(_row_value(row, "portrait_id", character.portrait_id) or character.portrait_id)
        power_id = str(_row_value(row, "power_id", getattr(character, "power_id", "ming")) or "ming")
        try:
            birth_year = int(_row_value(row, "birth_year", getattr(character, "birth_year", 0)) or 0)
        except (TypeError, ValueError):
            birth_year = int(getattr(character, "birth_year", 0) or 0)
        career_state = "出仕"
        if status == "offstage":
            career_state = "隐藏"
        elif status == "candidate":
            career_state = "待选"
        elif status in {"dismissed", "exiled", "retired"}:
            career_state = "在野"
        elif status in {"imprisoned", "dead"}:
            career_state = status_label
        power = self.content.powers.get(power_id)
        power_name = str(getattr(power, "name", "") or power_id or "大明")
        identity_bits = [power_name, faction, office_type, status_label]
        return CharacterRuntimeIdentity(
            status=status,
            status_reason=status_reason,
            status_label=status_label,
            career_state=career_state,
            office=office,
            office_type=office_type,
            faction=faction,
            portrait_id=portrait_id,
            portrait_prefix="consort_" if office_type == "后宫" else "minister_",
            power_id=power_id,
            power_name=power_name,
            birth_year=birth_year,
            summary=" · ".join(bit for bit in identity_bits if bit),
        )

    def _character_identities(self, runtime_rows: Optional[Dict[str, Any]] = None) -> Dict[str, CharacterRuntimeIdentity]:
        runtime_rows = runtime_rows if runtime_rows is not None else self._character_runtime_rows()
        return {
            character.name: self._character_identity(character, runtime_rows=runtime_rows)
            for character in self.content.characters.values()
        }

    def _character_runtime_rows(self) -> Dict[str, Any]:
        rows = self.db.conn.execute(
            """
            SELECT name, office, office_type, faction, portrait_id, power_id, birth_year, status, status_reason
            FROM characters
            """
        ).fetchall()
        return {str(row["name"]): row for row in rows}

    def _portrait_asset_meta_map(self) -> Dict[str, Dict[str, Any]]:
        rows = self.db.conn.execute(
            """
            SELECT asset_id, status, error, dna_seed, wardrobe_key,
                   CASE WHEN image_blob IS NOT NULL THEN 1 ELSE 0 END AS has_image_blob
            FROM portrait_assets
            """
        ).fetchall()
        return {
            str(row["asset_id"]): {
                "status": row["status"],
                "error": row["error"],
                "dna_seed": row["dna_seed"],
                "wardrobe_key": row["wardrobe_key"],
                "has_image_blob": bool(row["has_image_blob"]),
            }
            for row in rows
        }

    def _conversation_goal_payload_from_rows(self, rows: List[Dict[str, Any]] | List[Any]) -> List[Dict[str, Any]]:
        status_labels = {
            "active": "推进中",
            "waiting_conditions": "待条件",
            "blocked": "受阻",
            "expired": "失期",
            "sealed": "已立约",
            "fulfilled": "已履约",
            "abandoned": "已放弃",
        }
        condition_labels = {
            "none": "无条件",
            "pending": "待证",
            "satisfied": "已满足",
            "failed": "未满足",
            "blocked": "受阻",
        }
        out: List[Dict[str, Any]] = []
        current_turn = int(getattr(self.state, "turn", 0) or 0)
        for row in rows:
            item = dict(row)
            last_delta: Dict[str, Any] = {}
            raw_last_delta = item.get("last_delta")
            if isinstance(raw_last_delta, dict):
                last_delta = raw_last_delta
            else:
                try:
                    parsed = json.loads(str(item.get("last_delta_json") or "{}"))
                    last_delta = parsed if isinstance(parsed, dict) else {}
                except Exception:
                    last_delta = {}
            item["public_hint"] = str(last_delta.get("public_hint") or "")
            try:
                item["audit_confidence"] = int(last_delta.get("audit_confidence") or last_delta.get("confidence") or 0)
            except (TypeError, ValueError):
                item["audit_confidence"] = 0
            item["audit_status"] = str(last_delta.get("audit_status") or "")
            item.pop("conditions_json", None)
            item.pop("blockers_json", None)
            item.pop("last_delta_json", None)
            item.pop("last_delta", None)
            item.pop("_rn", None)
            item["progress_label"] = f"{int(item.get('score') or 0)}%"
            status = str(item.get("status") or "").strip()
            condition_status = str(item.get("condition_status") or "").strip()
            item["status_label"] = status_labels.get(status, status or "未定")
            item["condition_label"] = condition_labels.get(condition_status, condition_status or "")
            try:
                expires_turn = int(item.get("expires_turn") or 0)
            except (TypeError, ValueError):
                expires_turn = 0
            if expires_turn <= 0:
                item["due_label"] = ""
            elif expires_turn <= current_turn:
                item["due_label"] = f"已到第{expires_turn}月限"
            else:
                item["due_label"] = f"余{expires_turn - current_turn}月"
            blockers = [str(x).strip() for x in (item.get("blockers") or []) if str(x).strip()]
            item["blocker_summary"] = blockers[0] if blockers else ""
            pending = [
                cond for cond in (item.get("conditions") or [])
                if isinstance(cond, dict) and str(cond.get("status") or "pending") != "done"
            ]
            item["pending_conditions"] = pending
            item["pending_summary"] = str(pending[0].get("description") or "").strip() if pending else ""
            out.append(item)
        return out

    def _conversation_goal_payloads_by_minister(self, names: List[str], *, limit: int = 8) -> Dict[str, List[Dict[str, Any]]]:
        clean_names = sorted({str(name or "").strip() for name in names if str(name or "").strip()})
        if not clean_names:
            return {}
        capped_limit = max(1, min(200, int(limit or 8)))
        placeholders = ",".join("?" for _ in clean_names)
        rows = self.db.conn.execute(
            f"""
            SELECT * FROM (
                SELECT conversation_goals.*,
                       ROW_NUMBER() OVER (PARTITION BY minister_name ORDER BY id DESC) AS _rn
                FROM conversation_goals
                WHERE minister_name IN ({placeholders})
            )
            WHERE _rn <= ?
            ORDER BY minister_name, id DESC
            """,
            [*clean_names, capped_limit],
        ).fetchall()
        by_name: Dict[str, List[Dict[str, Any]]] = {name: [] for name in clean_names}
        for row in rows:
            parsed = self.db._parse_conversation_goal(row)
            by_name.setdefault(str(parsed.get("minister_name") or ""), []).append(parsed)
        return {
            name: self._conversation_goal_payload_from_rows(goal_rows)
            for name, goal_rows in by_name.items()
        }

    def _public_stance_notes_by_minister(self, names: List[str], *, limit: int = 3) -> Dict[str, List[Dict[str, Any]]]:
        clean_names = sorted({str(name or "").strip() for name in names if str(name or "").strip()})
        if not clean_names:
            return {}
        capped_limit = max(1, min(200, int(limit or 3)))
        placeholders = ",".join("?" for _ in clean_names)
        rows = self.db.conn.execute(
            f"""
            SELECT * FROM (
                SELECT id, turn, year, period, minister_name, topic, stance, confidence,
                       summary, conditions, related_issue_id, source_chat_turn_id,
                       user_message, minister_answer, evidence_json, risk_tags, execution_hint,
                       handshake_status, psychological_score, psychological_json, agreement_id, goal_id,
                       ROW_NUMBER() OVER (PARTITION BY minister_name ORDER BY id DESC) AS _rn
                FROM minister_stances
                WHERE turn = ? AND minister_name IN ({placeholders})
            )
            WHERE _rn <= ?
            ORDER BY minister_name, id DESC
            """,
            [int(self.state.turn), *clean_names, capped_limit],
        ).fetchall()
        by_name: Dict[str, List[Dict[str, Any]]] = {name: [] for name in clean_names}
        for row in rows:
            item = self._public_stance_note_payload(row)
            by_name.setdefault(str(item.get("minister_name") or ""), []).append(item)
        return by_name

    def _public_castration_payload(self, name: str) -> Optional[Dict[str, Any]]:
        try:
            from ming_sim.eunuch_lore import public_lore_payload
            payload = public_lore_payload(self.db, name)
        except Exception:
            payload = None
        return payload if isinstance(payload, dict) else None

    def _eunuch_lore_text_has_detail(self, text: str) -> bool:
        raw = str(text or "").strip()
        return bool(re.search(
            r"宝匣|宝用|宝约|宝案|旧匣|封蜡|油炸|石灰|香料|盐灰|楠木|杉木|黄杨|锡胆|灰瓮|"
            r"一大一小|二两|三两|一两|油封|发硬|漏尿|尿闭|尿频|尿线|小便不通|石淋|"
            r"嗓音|尖嗓|尖薄|肩背|腰腹|久跪|幻肢|幻痛|噩梦|按肩|磨刀|"
            r"贤者模式|性无能|不能人道|禁欲|净军房行事|奉旨宫刑|无麻|铜柄|银柄|檀柄|"
            r"钥匙贴身|补录宝案|查验宝匣|赐还宝匣|押在官库封存",
            raw,
            flags=re.IGNORECASE,
        ))

    def _eunuch_lore_text_has_write_intent(self, text: str) -> bool:
        raw = str(text or "").strip()
        return bool(re.search(
            r"记入旧档|记入档|记档|补记|补录|登记|入档|补档|请先记|"
            r"改用.{0,12}(?:匣|宝匣|木匣|灰瓮)|(?:宝|旧匣).{0,8}改用|"
            r"宝用|宝约|宝押|封存|收.{0,8}(?:匣|灰瓮|官库)|查验宝匣|赐还宝匣|"
            r"净军房行事|奉旨宫刑|补录宝案|钥匙贴身",
            raw,
        ))

    def _eunuch_lore_text_is_casual_query(self, text: str) -> bool:
        raw = str(text or "").strip()
        if self._eunuch_lore_text_has_write_intent(raw):
            return False
        return bool(re.search(
            r"(?:只是|只|不过|随便|单是).{0,12}(?:问|聊|说|谈|听|看|打听)"
            r"|(?:什么|何处|何人|谁|有何|有没有|可有|是否|怎么看|如何|风声|传闻|消息|旧案).{0,18}(?:吗|[？?])?",
            raw,
        ))

    def _eunuch_lore_text_looks_like_minister_answer(self, text: str) -> bool:
        raw = str(text or "").strip()
        if not raw:
            return False
        return bool(re.search(
            r"^(?:【动作】[^\n]{0,80}\n)?(?:臣|奴婢|奴才|小的|卑职|微臣).{0,12}"
            r"(?:回|遵旨|领旨|以为|听闻|不敢|这就|谨)",
            raw,
        ) or re.search(r"^.{0,18}回陛下", raw))

    def _eunuch_lore_default_speaker_allowed(self, text: str) -> bool:
        raw = str(text or "").strip()
        if self._eunuch_lore_text_is_casual_query(raw):
            return False
        if not self._eunuch_lore_text_has_detail(raw):
            return False
        return self._eunuch_lore_text_has_write_intent(raw) or bool(re.search(
            r"(?:我|臣|奴婢|奴才|小的|本人|自己|你|你的|卿|此人|这人|他|他的).{0,24}"
            r"(?:宝|宝匣|旧匣|漏尿|尿闭|嗓音|幻肢|净军房|宫刑|无麻|贤者模式|性无能)",
            raw,
        ))

    def _absorb_eunuch_lore_from_text(self, minister_name: str, text: str) -> Dict[str, Any]:
        clean = str(minister_name or "").strip()
        raw = str(text or "").strip()
        if not raw:
            return {}
        if self._eunuch_lore_text_looks_like_minister_answer(raw) and not self._eunuch_lore_text_has_write_intent(raw):
            return {}
        if self._eunuch_lore_text_is_casual_query(raw):
            return {}
        if not self._eunuch_lore_text_has_detail(raw):
            return {}
        all_mentions = self._character_mentions_in_text(raw)
        mentioned = [name for name in all_mentions if name != clean]
        candidates = list(mentioned)
        if not candidates and clean and clean in all_mentions and self._eunuch_lore_text_has_write_intent(raw):
            candidates = [clean]
        if not candidates:
            try:
                pending = self._load_pending_dialogue_action(clean)
            except Exception:
                pending = {}
            if isinstance(pending, dict) and pending.get("type") in {"castration", "eunuch_care"}:
                target = str(pending.get("target") or "").strip()
                if target:
                    candidates = [target]
        if not candidates and clean and self._eunuch_lore_default_speaker_allowed(raw):
            candidates = [clean]
        if not candidates:
            return {}
        try:
            from ming_sim.eunuch_lore import update_lore_from_text
            from ming_sim.personnel_actions import sync_castration_lore_gameplay
            day = int(self.db.kv_get("upgrade.current_day") or 0)
            targets: Dict[str, Dict[str, Any]] = {}
            for target in candidates:
                result = update_lore_from_text(self.db, target, raw, day=day)
                if isinstance(result, dict) and result.get("updated"):
                    gameplay = sync_castration_lore_gameplay(
                        self.db,
                        self.state,
                        target,
                        result.get("castration") if isinstance(result.get("castration"), dict) else {},
                        changed_keys=list((result.get("updated") or {}).keys()) if isinstance(result.get("updated"), dict) else [],
                        review_hint=raw,
                    )
                    if gameplay:
                        result["gameplay"] = gameplay
                    targets[target] = result
        except Exception:
            targets = {}
        if not targets:
            return {}
        primary_name = clean if clean in targets else next(iter(targets))
        primary = dict(targets[primary_name])
        primary["targets"] = targets
        primary["updated_targets"] = list(targets.keys())
        return primary

    def _eunuch_lore_dialogue_effect(self, update: Dict[str, Any]) -> Dict[str, Any]:
        """Turn deterministic inner-court lore maintenance into restrained chat feedback."""
        if not isinstance(update, dict) or not update.get("updated"):
            return {}
        name = str(update.get("name") or "").strip()
        changed = update.get("updated") if isinstance(update.get("updated"), dict) else {}
        gameplay = update.get("gameplay") if isinstance(update.get("gameplay"), dict) else {}
        label_map = {
            "castration_method": "旧制",
            "knife_tool": "刀具",
            "anesthesia": "麻醉",
            "procedure_note": "旧制",
            "bao_size": "形制",
            "bao_shape": "形制",
            "bao_texture": "成色",
            "bao_weight": "轻重",
            "bao_preservation": "封存",
            "bao_container": "匣藏",
            "bao_ritual": "执念",
            "aftereffect": "后患",
            "urinary_aftereffect": "尿路",
            "voice_body_change": "体声",
            "trauma_response": "惊创",
            "private_fixation": "心癖",
            "psychosexual_state": "心相",
        }
        effects: List[Dict[str, str]] = []
        priority = {
            "bao_container": 0,
            "bao_preservation": 1,
            "urinary_aftereffect": 2,
            "voice_body_change": 3,
            "trauma_response": 4,
            "psychosexual_state": 5,
            "private_fixation": 6,
        }
        ordered_changes = sorted(
            changed.items(),
            key=lambda item: (priority.get(str(item[0]), 20), str(item[0])),
        )
        for key, value in ordered_changes:
            label = label_map.get(str(key), str(key))
            text = str(value or "").strip()
            if text:
                effects.append({"kind": "eunuch_lore", "label": f"{label}：{text[:18]}", "tone": "info"})
        traits = [str(item).strip() for item in (gameplay.get("traits_added") or []) if str(item).strip()]
        items = [str(item).strip() for item in (gameplay.get("items_added") or []) if str(item).strip()]
        scheme_review = gameplay.get("scheme_review") if isinstance(gameplay.get("scheme_review"), dict) else {}
        if traits:
            effects.append({"kind": "character_trait", "label": f"新增特质：{'、'.join(traits[:3])}", "tone": "warn"})
        if items:
            quiet_items = [
                item.replace("净身旧档", "内廷旧档").replace("官没宝匣", "官库旧匣").replace("遗失宝案", "旧匣遗失")
                for item in items
            ]
            effects.append({"kind": "inventory", "label": f"入库：{'、'.join(quiet_items[:2])}", "tone": "good"})
        if scheme_review:
            tier = str(scheme_review.get("tier") or "方案复盘").strip()
            risk = int(scheme_review.get("risk_score") or 0)
            tone = "bad" if risk >= 72 else "warn" if risk >= 55 else "good"
            effects.append({"kind": "castration_scheme", "label": f"旧制复盘：{tier} 风险{risk}", "tone": tone})
        stage = ""
        castration = update.get("castration") if isinstance(update.get("castration"), dict) else {}
        profile = castration.get("voice_profile") if isinstance(castration.get("voice_profile"), dict) else {}
        cues = [str(item).strip() for item in (profile.get("stage_cues") or []) if str(item).strip()]
        if cues:
            stage = cues[0]
        message_bits = []
        if name:
            message_bits.append(f"{name}旧档更新")
        if traits:
            message_bits.append(f"新特质 {'、'.join(traits[:2])}")
        if scheme_review:
            message_bits.append(f"方案{scheme_review.get('tier')} 风险{scheme_review.get('risk_score')}")
        if not message_bits:
            message_bits.append("旧档细节已补记")
        return {
            "title": "内廷旧档补记",
            "message": "；".join(message_bits)[:120],
            "effects": effects[:12],
            "stage_direction": stage,
        }

    def public_character(
        self,
        character: Character,
        *,
        include_detail: bool = True,
        runtime_row: Optional[Any] = None,
        runtime_identity: Optional[CharacterRuntimeIdentity] = None,
        portrait_assets: Optional[Dict[str, Dict[str, Any]]] = None,
        stance_notes_by_minister: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        conversation_goals_by_minister: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        status_by_name: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        identity = runtime_identity or self._character_identity(character, runtime_row=runtime_row)
        age_payload = self._age_payload(character, identity.birth_year)
        portrait_meta = (
            self._portrait_meta(character, identity.portrait_id, identity.portrait_prefix, portrait_assets=portrait_assets)
            if include_detail
            else self._portrait_summary_meta(character, identity.portrait_id, identity.portrait_prefix, portrait_assets=portrait_assets)
        )
        payload: Dict[str, Any] = {
            "name": character.name,
            "office": identity.office,
            "office_type": identity.office_type,
            "faction": identity.faction,
            "status": identity.status,
            "status_reason": identity.status_reason,
            "status_label": identity.status_label,
            "career_state": identity.career_state,
            "summary": identity.summary,
            "portrait_id": identity.portrait_id,
            **portrait_meta,
            **age_payload,
            "power_id": identity.power_id,
            "skills": [],
            "favorite": character.name in self.favorites,
        }
        may_have_castration_lore = (
            is_eunuch_office(str(identity.office or ""), str(identity.office_type or ""))
            or bool(re.search(r"太监|宦官|内官|内廷", str(identity.faction or "")))
        )
        castration_payload = (
            self._public_castration_payload(character.name)
            if include_detail and may_have_castration_lore
            else None
        )
        if castration_payload:
            payload["castration"] = castration_payload
            scheme_profile = castration_payload.get("scheme_profile") if isinstance(castration_payload.get("scheme_profile"), dict) else {}
            risk_score = int(scheme_profile.get("risk_score") or 0) if isinstance(scheme_profile, dict) else 0
            payload["identity_tags"] = [
                {"label": "内廷奴籍", "tone": "warn"},
                {"label": "内廷旧档", "tone": "info"},
                {"label": "旧患较重" if risk_score >= 72 else "旧患线索", "tone": "warn" if risk_score >= 72 else "neutral"},
            ]
        if include_detail:
            active_skill_grants = self.db.active_skill_grants(character.name)
            skill_ids = available_skill_ids(character, active_grants=active_skill_grants)
            # 印象档案（升级总案 S3）：属性不出真值，只出评语+置信度，
            # 噪声随召对熟悉度与密查收敛——「识人」是玩法不是面板。
            try:
                from ming_sim.veil import character_evaluations
                payload["evaluations"] = character_evaluations(self.db, character)
            except Exception:
                payload["evaluations"] = []
            try:
                from ming_sim import policies as doctrine_policies
                payload["policy_ideals"] = doctrine_policies.character_policy_ideals(
                    self.db,
                    character.name,
                    context_row={
                        "office": identity.office,
                        "office_type": identity.office_type,
                        "faction": identity.faction,
                        "style": character.style,
                        "ability": getattr(character, "ability", 50),
                    },
                )
            except Exception:
                payload["policy_ideals"] = {}
            payload.update({
                "style": character.style,
                "personal_skills": list(character.personal_skills or []),
                "stance_notes": (
                    stance_notes_by_minister.get(character.name, [])
                    if stance_notes_by_minister is not None
                    else self._public_stance_notes(character.name, limit=3)
                ),
                "conversation_goals": (
                    conversation_goals_by_minister.get(character.name, [])
                    if conversation_goals_by_minister is not None
                    else self.conversation_goal_payload(minister_name=character.name, limit=8)
                ),
                "network_profile": npc_network_profile(
                    character.name,
                    db=self.db,
                    limit=8,
                    status_by_name=status_by_name,
                ),
                "skills": [
                    {
                        "id": skill_id,
                        "name": skill_display_name(skill_id),
                        "sources": skill_source_labels(character, skill_id, active_grants=active_skill_grants),
                        "description": self.content.skill_descriptions.get(skill_id, ""),
                    }
                    for skill_id in skill_ids
                ],
            })
        return payload

    def character_index_payload(
        self,
        runtime_rows: Optional[Dict[str, Any]] = None,
        portrait_assets: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """全 NPC 只读索引：轻量展示用，不携带人物网络大对象。"""
        rows: List[Dict[str, Any]] = []
        portrait_assets = portrait_assets if portrait_assets is not None else self._portrait_asset_meta_map()
        identities = self._character_identities(runtime_rows)
        for character in self.content.characters.values():
            identity = identities[character.name]
            rows.append({
                "name": character.name,
                "office": identity.office,
                "office_type": identity.office_type,
                "faction": identity.faction,
                "status": identity.status,
                "status_reason": identity.status_reason,
                "status_label": identity.status_label,
                "power_id": identity.power_id,
                "power_name": identity.power_name,
                "summary": identity.summary,
                "portrait_available": self._portrait_available(
                    character,
                    identity.portrait_id,
                    identity.portrait_prefix,
                    portrait_assets=portrait_assets,
                ),
                "can_summon": bool(identity.power_id == "ming" and identity.status == "active"),
            })
        return rows

    def organization_character_card(
        self,
        character: Character,
        *,
        runtime_row: Optional[Any] = None,
        runtime_identity: Optional[CharacterRuntimeIdentity] = None,
    ) -> Dict[str, Any]:
        identity = runtime_identity or self._character_identity(character, runtime_row=runtime_row)
        return {
            "name": character.name,
            "office": identity.office,
            "office_type": identity.office_type,
            "faction": identity.faction,
            "status": identity.status,
            "status_reason": identity.status_reason,
            "status_label": identity.status_label,
            "power_id": identity.power_id,
        }

    def character_power_id(self, character: Character, runtime_rows: Optional[Dict[str, Any]] = None) -> str:
        row = runtime_rows.get(character.name) if runtime_rows is not None else None
        if row is None:
            row = self.db.conn.execute(
                "SELECT power_id FROM characters WHERE name=?", (character.name,)
            ).fetchone()
        return (row["power_id"] if row else None) or getattr(character, "power_id", "ming") or "ming"

    def _portrait_meta(
        self,
        character: Character,
        portrait_id: str,
        portrait_prefix: str,
        *,
        portrait_assets: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        status = "missing"
        error = ""
        dna_seed = ""
        dna_asset_id = ""
        dna_status = "missing"
        wardrobe_key = ""
        available = False
        fallback_id = self._portrait_fallback_id(character, portrait_id, portrait_prefix)
        if portrait_id.startswith(GENERATED_PORTRAIT_PREFIX):
            asset_id = portrait_id.removeprefix(GENERATED_PORTRAIT_PREFIX)
            row = portrait_assets.get(asset_id) if portrait_assets is not None else self.db.get_portrait_asset(asset_id)
            if row is not None:
                status = str(row["status"] or "pending")
                error = str(row["error"] or "")
                dna_seed = str(row["dna_seed"] or "")
                wardrobe_key = str(row["wardrobe_key"] or "")
                has_image_blob = bool(row.get("has_image_blob")) if isinstance(row, dict) else bool(row["image_blob"] is not None)
                available = bool(status == "ready" and has_image_blob)
            else:
                status = "missing"
        elif portrait_id.startswith(CUSTOM_PORTRAIT_PREFIX):
            status = "ready" if _find_portrait_file(character.name, self.username) is not None else "missing"
            available = status == "ready"
        else:
            available = (
                _static_portrait_exists(f"{portrait_prefix}{character.name}.png")
                or (bool(portrait_id) and _static_portrait_exists(f"{portrait_id}.png"))
            )
            status = "ready" if available else "missing"
        spec = build_portrait_spec(character, self.state, self.session.campaign_id)
        dna_asset_id = spec.dna_asset_id
        dna_row = portrait_assets.get(dna_asset_id) if portrait_assets is not None else self.db.get_portrait_asset(dna_asset_id)
        if dna_row is not None:
            dna_status = str(dna_row["status"] or "pending")
        if not dna_seed:
            dna_seed = spec.dna_seed
        if not wardrobe_key:
            wardrobe_key = spec.wardrobe_key
        return {
            "portrait_available": bool(available or fallback_id),
            "portrait_status": status,
            "portrait_error": error,
            "portrait_fallback_id": fallback_id,
            "portrait_dna_seed": dna_seed,
            "portrait_dna_asset_id": dna_asset_id,
            "portrait_dna_status": dna_status,
            "portrait_wardrobe_key": wardrobe_key,
        }

    def _portrait_available(
        self,
        character: Character,
        portrait_id: str,
        portrait_prefix: str,
        *,
        portrait_assets: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> bool:
        return bool(self._portrait_summary_meta(
            character,
            portrait_id,
            portrait_prefix,
            portrait_assets=portrait_assets,
        )["portrait_available"])

    def _portrait_summary_meta(
        self,
        character: Character,
        portrait_id: str,
        portrait_prefix: str,
        *,
        portrait_assets: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        fallback_id = self._portrait_fallback_id(character, portrait_id, portrait_prefix)
        if portrait_id.startswith(GENERATED_PORTRAIT_PREFIX):
            asset_id = portrait_id.removeprefix(GENERATED_PORTRAIT_PREFIX)
            row = portrait_assets.get(asset_id) if portrait_assets is not None else self.db.get_portrait_asset(asset_id)
            if row is None:
                return {"portrait_available": bool(fallback_id), "portrait_status": "missing", "portrait_fallback_id": fallback_id}
            status = str(row["status"] or "pending")
            has_image_blob = bool(row.get("has_image_blob")) if isinstance(row, dict) else bool(row["image_blob"] is not None)
            available = bool(status == "ready" and has_image_blob)
            return {
                "portrait_available": bool(available or fallback_id),
                "portrait_status": status,
                "portrait_fallback_id": fallback_id,
            }
        if portrait_id.startswith(CUSTOM_PORTRAIT_PREFIX):
            available = _find_portrait_file(character.name, self.username) is not None
            return {
                "portrait_available": bool(available or fallback_id),
                "portrait_status": "ready" if available else "missing",
                "portrait_fallback_id": fallback_id,
            }
        available = (
            _static_portrait_exists(f"{portrait_prefix}{character.name}.png")
            or (bool(portrait_id) and _static_portrait_exists(f"{portrait_id}.png"))
        )
        return {
            "portrait_available": bool(available or fallback_id),
            "portrait_status": "ready" if available else "missing",
            "portrait_fallback_id": fallback_id,
        }

    def _portrait_fallback_id(self, character: Character, portrait_id: str, portrait_prefix: str) -> str:
        """Best static portrait id for UI fallback when a custom/generated image is absent."""
        if not portrait_id.startswith((GENERATED_PORTRAIT_PREFIX, CUSTOM_PORTRAIT_PREFIX)):
            return ""
        candidates: List[str] = []
        named_id = f"{portrait_prefix}{character.name}"
        candidates.append(named_id)
        pool_prefix = "consort_pool_" if portrait_prefix == "consort_" else "minister_pool_"
        pool_id = _stable_static_portrait_id(pool_prefix, character.name)
        if pool_id:
            candidates.append(pool_id)
        for candidate in candidates:
            clean = os.path.basename(str(candidate or ""))
            if clean and _static_portrait_exists(f"{clean}.png"):
                return clean
        return ""

    def directive_payload(self, row) -> Dict[str, Any]:
        policy_doctrine: Dict[str, Any] = {}
        try:
            from ming_sim import policies
            category_id = ""
            if "category" in row.keys():
                category_id = str(row["category"] or "")
            policy_doctrine = policies.directive_doctrine_review(
                self.db,
                self.state,
                str(row["text"] or ""),
                category_id=category_id,
                actor=str(row["actor"] or ""),
            )
        except Exception:
            policy_doctrine = {}
        return {
            "id": int(row["id"]),
            "event_id": row["event_id"] or "",
            "event_title": (row["event_title"] if "event_title" in row.keys() else "") or "",
            "actor": row["actor"] or "",
            "skill_id": row["skill_id"] or "",
            "skill_name": skill_display_name(str(row["skill_id"] or "")),
            "text": row["text"],
            "source": row["source"],
            "status": row["status"],
            "notes": row["notes"],
            "authority": row["notes"] or "",
            "policy_doctrine": policy_doctrine,
        }

    def directive_rows(self):
        # 颁诏候选 = draft；UI 列表含 pending
        return self.db.list_directives(self.state, statuses=("pending", "draft"))

    def _record_pending_directive(
        self,
        character: Character,
        draft_text: str,
    ) -> Optional[Dict[str, Any]]:
        draft_text = (draft_text or "").strip()
        if not draft_text:
            return None
        notes = f"由{character.name}拟旨入档"
        directive_id = self.db.add_directive(
            self.state,
            None,
            draft_text,
            "大臣拟旨",
            actor=character.name,
            notes=notes,
            status="pending",
        )
        return {
            "id": directive_id,
            "text": draft_text,
            "status": "pending",
            "source": "大臣拟旨",
            "actor": character.name,
            "notes": notes,
        }

    _DIRECTIVE_INTENT_RE = re.compile(r"(拟旨|拟诏|草案|旨意|谕旨|诏书|可直接颁布|下旨|颁布)")
    _DIRECTIVE_NEG_RE = re.compile(r"(不要|不必|无需|别|勿|暂不).{0,8}(拟旨|拟诏|草案|旨意|谕旨|诏书|下旨|颁布)")

    def _directive_intent(self, text: str) -> bool:
        raw = str(text or "")
        return bool(self._DIRECTIVE_INTENT_RE.search(raw)) and not bool(self._DIRECTIVE_NEG_RE.search(raw))

    def _directive_subject(self, text: str) -> str:
        subject = str(text or "").strip()
        subject = re.sub(r"^拟旨如下[：:\s]*", "", subject)
        subject = re.sub(r"^(请|烦请|劳烦)?(替朕|为朕)?(拟|拟定|草拟|起草|写)?(一道|一份)?(可直接颁布的)?(旨意|谕旨|诏书|草案)[：:\s，,]*", "", subject)
        subject = re.sub(r"(请|烦请)?(替朕|为朕)?(拟|拟定|草拟|起草|写)(一道|一份)?(可直接颁布的)?(旨意|谕旨|诏书|草案)[：:\s，,]*", "", subject)
        subject = re.sub(r"\s+", " ", subject).strip(" ：:，,。；;")
        if len(subject) > 150:
            subject = subject[:150].rstrip() + "…"
        return subject

    def _fallback_pending_directive(
        self,
        character: Character,
        user_text: str,
        answer: str,
    ) -> Optional[Dict[str, Any]]:
        """Guarantee the player action loop when the minister argues but forgets to draft.

        The minister's cautious reply remains intact; this only files a conservative
        editable draft so the player can continue to confirmation/edict.
        """
        if not self._directive_intent(user_text):
            return None
        subject = self._directive_subject(user_text)
        if len(subject) < 8:
            return None
        if len(subject) > 110:
            core = subject
        else:
            core = f"就{subject}"
        draft_text = (
            f"着{character.name}即会同所司，{core}逐项核实办理；凡钱粮、兵马、地方承行，"
            "须列明数目、去向与期限。限五日内具奏初案，办竣即复命；若有窒碍，不得隐匿。"
        )
        row = self.db.conn.execute(
            """
            SELECT id, text, status, source, notes, actor
            FROM turn_directives
            WHERE turn=? AND actor=? AND text=? AND status IN ('pending','draft')
            ORDER BY id DESC LIMIT 1
            """,
            (int(self.state.turn), character.name, draft_text),
        ).fetchone()
        if row is not None:
            return {
                "id": int(row["id"]),
                "text": str(row["text"] or draft_text),
                "status": str(row["status"] or "pending"),
                "source": str(row["source"] or "大臣拟旨"),
                "actor": str(row["actor"] or character.name),
                "notes": str(row["notes"] or ""),
            }
        proposed = self._record_pending_directive(character, draft_text)
        if proposed:
            proposed["fallback"] = True
            proposed["notes"] = f"由{character.name}拟旨入档（保守草案；原奏对未直接成稿）"
            self.db.conn.execute("UPDATE turn_directives SET notes=? WHERE id=?", (proposed["notes"], int(proposed["id"])))
            self.db.conn.commit()
        return proposed

    def _proposed_from_dialogue_goal(self, dialogue_goal: Optional[Dict[str, Any]], character: Character) -> Optional[Dict[str, Any]]:
        if not isinstance(dialogue_goal, dict):
            return None
        proposed = dialogue_goal.get("proposed_directive")
        if not isinstance(proposed, dict) or not proposed.get("id") or not proposed.get("text"):
            return None
        return {
            "id": int(proposed.get("id") or 0),
            "text": str(proposed.get("text") or ""),
            "status": str(proposed.get("status") or "pending"),
            "source": str(proposed.get("source") or "大臣拟旨"),
            "actor": str(proposed.get("actor") or character.name),
            "notes": str(proposed.get("notes") or ""),
        }

    def map_nodes(
        self,
        *,
        regions: Optional[List[Dict[str, Any]]] = None,
        armies: Optional[List[Dict[str, Any]]] = None,
        buildings: Optional[List[Dict[str, Any]]] = None,
        include_detail: bool = True,
    ) -> List[Dict[str, Any]]:
        region_positions = {
            "beizhili": (55.5, 41.2), "nanzhili": (70, 41), "shandong": (56.8, 47.9),
            "shanxi": (48.8, 45.2), "henan": (58, 46), "shaanxi": (51, 38),
            "zhejiang": (73.7, 57.9), "jiangxi": (67, 55), "huguang": (59, 59),
            "sichuan": (57, 52), "fujian": (73.2, 65.1), "guangdong": (62.5, 73.6),
            "guangxi": (53.9, 69.6), "yunnan": (47, 69), "guizhou": (52, 56),
            "liaodong": (61.0, 37.6), "dongjiang_area": (68.9, 43.7),
            "shenyang_liaoyang": (61.3, 39.6), "jianzhou": (64.6, 31.0),
            "korea": (67.0, 44.8), "mongol_chahar": (47.0, 31.0), "nurgan": (58.2, 21.2),
            "outer_mongolia": (43.0, 24.0), "western_regions": (25.0, 40.0),
            "tibet": (31.0, 57.0), "amur_frontier": (70.0, 24.0),
            "japan": (83.0, 49.0), "southwest_frontier": (45.0, 75.0),
            "taiwan": (78, 67),
        }
        theater_positions = {
            "liaodong": (57.76, 42.21), "dongjiang": (63.95, 42.39),
            "xuan_da": (50.49, 40.08), "shanhaiguan": (55.52, 42.84),
        }
        regions = regions if regions is not None else self.db.region_payload()
        armies = armies if armies is not None else self.db.army_payload(danger_order=True)
        # 文书化黑箱（S3）：地图口径与国势面板一致——明军兵额显示兵部账面值，
        # 否则 /api/map 会把空饷真值整个旁路掉
        try:
            from ming_sim.veil import army_reported_overlay
            armies = army_reported_overlay(self.db, armies)
        except Exception:
            pass
        if include_detail and buildings is None:
            buildings = self.db.building_payload()
        buildings_by_region: Dict[str, List[Dict[str, Any]]] = {}
        if include_detail:
            for building in buildings or []:
                buildings_by_region.setdefault(str(building.get("region_id") or ""), []).append(building)
        nodes: List[Dict[str, Any]] = []
        for region in regions:
            x, y = region_positions.get(str(region["id"]), (50, 50))
            risk = int(region["unrest"]) + int(region["military_pressure"]) + (100 - int(region["public_support"]))
            node_kind = "region" if str(region.get("controlled_by") or "ming") == "ming" else "external"
            node: Dict[str, Any] = {
                "id": region["id"],
                "kind": node_kind,
                "x": x,
                "y": y,
                "risk": risk,
            }
            if include_detail:
                stationed = [a for a in armies if self._army_belongs_to_region(a, region)]
                node.update({
                    "region": region,
                    "armies": stationed,
                    "buildings": buildings_by_region.get(str(region["id"]), []),
                })
            nodes.append(node)
        for node_id, (x, y) in theater_positions.items():
            stationed = [a for a in armies if self._army_belongs_to_theater(a, node_id)]
            if stationed:
                node = {"id": node_id, "kind": "theater", "x": x, "y": y, "label": self._theater_label(node_id), "risk": 120}
                if include_detail:
                    node["armies"] = stationed
                nodes.append(node)
        return nodes

    def _army_belongs_to_region(self, army: Dict[str, Any], region: Dict[str, Any]) -> bool:
        station = str(army["station"])
        region_name = str(region["name"])
        return (
            str(region["id"]) in station
            or region_name in station
            or station in region_name
            or any(part.strip() and part.strip() in station for part in region_name.replace("／", "/").split("/"))
        )

    def _army_belongs_to_theater(self, army: Dict[str, Any], theater_id: str) -> bool:
        text = f"{army['id']} {army['name']} {army['station']} {army['theater']}"
        mapping = {
            "liaodong": ("辽东", "宁锦", "关宁"),
            "dongjiang": ("东江", "皮岛"),
            "xuan_da": ("宣大", "宣府", "大同"),
            "shanhaiguan": ("山海关",),
        }
        return any(word in text for word in mapping.get(theater_id, ()))

    def _theater_label(self, theater_id: str) -> str:
        return {
            "liaodong": "辽东 / 宁锦",
            "dongjiang": "东江镇",
            "xuan_da": "宣大",
            "shanhaiguan": "山海关",
        }[theater_id]

    def closed_this_turn_payloads(self) -> List[Dict[str, Any]]:
        """上回合（resolve 后 state.turn 已 +1）关闭的 issue。"""
        target_turn = max(0, int(self.state.turn) - 1)
        out: List[Dict[str, Any]] = []
        for row in self.db.list_closed_issues_at(target_turn):
            status = str(row["status"])
            effect_key = "effect_on_resolve" if status == "resolved" else "effect_on_fail"
            try:
                effect = json.loads(str(row[effect_key] or "{}"))
            except Exception:
                effect = {}
            out.append({
                "id": int(row["id"]),
                "kind": row["kind"],
                "title": row["title"],
                "status": status,
                "bar_value": int(row["bar_value"]),
                "bar_good_meaning": row["bar_good_meaning"],
                "bar_bad_meaning": row["bar_bad_meaning"],
                "closed_turn": int(row["closed_turn"] or 0),
                "stage_text": row["stage_text"],
                "effect": effect,
            })
        return out

    def issue_payloads(self) -> List[Dict[str, Any]]:
        payloads: List[Dict[str, Any]] = []
        try:
            from ming_sim import policies as doctrine_policies
        except Exception:
            doctrine_policies = None
        for row in self.db.list_active_issues():
            policy_doctrine: Optional[Dict[str, Any]] = None
            if doctrine_policies is not None and str(row["origin_kind"] or "") == "doctrine":
                policy_doctrine = doctrine_policies.doctrine_issue_payload(self.db, row)
            item = {
                "id": int(row["id"]),
                "kind": row["kind"],
                "title": row["title"],
                "bar_value": int(row["bar_value"]),
                "bar_good_meaning": row["bar_good_meaning"],
                "bar_bad_meaning": row["bar_bad_meaning"],
                "phase": row["phase"],
                "stage_text": row["stage_text"],
                "severity": int(row["severity"]),
                "tags": list(json.loads(str(row["tags"] or "[]"))),
                "inertia": int(row["inertia"] or 0),
                "resolve_condition": _humanize_condition(row["resolve_condition"] or ""),
                "fail_condition": _humanize_condition(row["fail_condition"] or ""),
                "ongoing_text": _format_issue_ongoing(str(row["ongoing_effects"] or "{}")),
                "effect_on_resolve": dict(json.loads(str(row["effect_on_resolve"] or "{}"))),
                "effect_on_fail": dict(json.loads(str(row["effect_on_fail"] or "{}"))),
            }
            if policy_doctrine:
                item["policy_doctrine"] = policy_doctrine
            payloads.append(item)
        return payloads

    def legacies_payload(self) -> List[Dict[str, Any]]:
        """现行帝国修正（长期百分比修正符），给状态栏小条用。"""
        out: List[Dict[str, Any]] = []
        try:
            from ming_sim import policies as doctrine_policies
        except Exception:
            doctrine_policies = None
        opening_clear_text = {
            leg.key: leg.clear_narrative
            for leg in self.content.opening_legacies
            if leg.clear_narrative
        }
        for row in self.db.list_active_legacies(self.state):
            try:
                eff = json.loads(str(row["modifiers"] or "{}"))
            except Exception:
                eff = {}
            try:
                clear_gate = json.loads(str(row["clear_gate"] or "{}"))
            except Exception:
                clear_gate = {}
            remaining_months = self.db.legacy_remaining_months(row, self.state)
            clear_condition = opening_clear_text.get(str(row["legacy_key"] or ""), "")
            if not clear_condition and clear_gate:
                clear_condition = _humanize_legacy_gate(clear_gate, self.content)
            elif clear_condition and clear_gate:
                clear_condition = f"{clear_condition}（{_humanize_legacy_gate(clear_gate, self.content)}）"
            if not clear_condition:
                clear_condition = "无固定消除条件" if remaining_months < 0 else f"再过 {remaining_months} 月自然消退"
            policy_doctrine: Optional[Dict[str, Any]] = None
            if doctrine_policies is not None:
                policy_doctrine = doctrine_policies.doctrine_legacy_payload(row) or None
            item = {
                "id": int(row["id"]),
                "name": row["name"],
                "narrative_hint": row["narrative_hint"],
                "modifiers": eff,
                "effect_text": _humanize_legacy_effect(eff, self.content),
                "remaining_months": remaining_months,
                "clear_condition": clear_condition,
            }
            if policy_doctrine:
                item["policy_doctrine"] = policy_doctrine
            out.append(item)
        return out

    def budget_payload(self, budget: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        # 唯一定额源：flows.compute_budget_lines（与实际落账 / 大臣 treasury_budget_summary 三处统一）。
        budget = budget if budget is not None else compute_budget_lines(self.db, self.state)
        budget["国库"]["balance"] = int(self.state.metrics["国库"])
        budget["内库"]["balance"] = int(self.state.metrics["内库"])
        for account in (budget["国库"], budget["内库"]):
            income_total = sum(int(item["amount"]) for item in account["income"])
            expense_total = sum(int(item["amount"]) for item in account["expense"])
            account["income_total"] = income_total
            account["expense_total"] = expense_total
            account["net"] = income_total - expense_total
        # 本月入账（上月末结算）：上月末 LLM 推演 + 固定财政 tick 落的 ledger
        # 时序上 state.turn 在结算末尾 +1 进入新月，所以"本月可见的入账"是 cur_turn - 1 的 ledger。
        # 语义对齐玩家直觉："上月末抄家/清丈的钱，算这个月的收入"。
        # 过滤掉固定收支（已在上方"固定收入/固定支出"展示），只列一次性流水
        # （清丈追缴、抄家、赈济临支、亏空压力等 LLM 推演产物）。
        FIXED_CATEGORIES = {
            # 国库固定（category 以 ledger 实际写入值为准）
            "田赋辽饷盐商", "田赋", "辽饷", "盐税", "商税",
            "各军军饷", "宗室禄米", "百官俸禄", "工部", "赈灾备用",
            # 内库固定
            "皇庄", "织造", "矿税",
            "宫廷开支", "内廷俸禄", "妃嫔供奉",
            # 建筑（每月固定 tick）
            "建筑产出", "建筑维护",
            # 开局初始账册
            "期初",
        }
        cur_turn = int(self.state.turn)
        rows = self.db.conn.execute(
            "SELECT id, account, delta, balance_after, category, reason "
            "FROM economy_ledger WHERE turn = ? ORDER BY id",
            (cur_turn - 1,),
        ).fetchall()
        for name, account in budget.items():
            movements = [
                {
                    "delta": int(r["delta"]),
                    "balance_after": int(r["balance_after"]),
                    "category": str(r["category"] or ""),
                    "reason": str(r["reason"] or ""),
                }
                for r in rows
                if str(r["account"]) == name
                and str(r["category"] or "") not in FIXED_CATEGORIES
            ]
            account["movements"] = movements
            account["movements_total"] = sum(m["delta"] for m in movements)
        return budget

    def ending_payload(self) -> Optional[Dict[str, Any]]:
        """结局已触发时返回 {status,label,summary,timeline}，否则 None。"""
        if not self.state.ended:
            return None
        from ming_sim.context import ENDING_LABELS
        row = self.db.get_ending_summary() or {}
        return {
            "status": self.state.ending_status,
            "label": ENDING_LABELS.get(self.state.ending_status, "结局"),
            "summary": row.get("summary", ""),
            "timeline": row.get("timeline", []),
        }

    def adventure_payload(self) -> List[Dict[str, Any]]:
        return self.db.list_adventure_logs(limit=10)

    def item_payload(self) -> List[Dict[str, Any]]:
        return self.db.list_player_inventory()

    # ── 组织架构 / 人才来源 ────────────────────────────────────────────────
    def _custom_institutions(self) -> List[Dict[str, Any]]:
        raw = self.db.kv_get("custom_institutions")
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []
        return data if isinstance(data, list) else []

    def _save_custom_institutions(self, institutions: List[Dict[str, Any]]) -> None:
        self.db.kv_set("custom_institutions", json.dumps(institutions, ensure_ascii=False))

    def add_custom_institution(self, name: str, category: str, mandate: str, slots: List[str]) -> Dict[str, Any]:
        clean_name = re.sub(r"\s+", "", (name or "").strip())[:20]
        if not clean_name:
            raise HTTPException(status_code=400, detail="机构名不能为空。")
        clean_slots = [s.strip()[:18] for s in slots if s and s.strip()]
        if not clean_slots:
            clean_slots = [f"{clean_name}提举", f"{clean_name}副使"]
        current = self._custom_institutions()
        if any(str(item.get("name")) == clean_name for item in current):
            raise HTTPException(status_code=409, detail=f"{clean_name}已在组织图中。")
        item = {
            "id": f"custom-{self.state.turn}-{len(current) + 1}-{abs(hash(clean_name)) % 10000}",
            "name": clean_name,
            "category": (category or "非常规").strip()[:12] or "非常规",
            "mandate": (mandate or "奉旨新设，权责待议。").strip()[:120],
            "custom": True,
            "slots": [{"title": slot, "office_type": clean_name, "count": 1} for slot in clean_slots],
        }
        current.append(item)
        self._save_custom_institutions(current)
        return item

    def organization_payload(
        self,
        runtime_rows: Optional[Dict[str, Any]] = None,
        portrait_assets: Optional[Dict[str, Dict[str, Any]]] = None,
        stance_notes_by_minister: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        conversation_goals_by_minister: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    ) -> Dict[str, Any]:
        runtime_rows = runtime_rows if runtime_rows is not None else self._character_runtime_rows()
        identities = self._character_identities(runtime_rows)
        base_institutions: List[Dict[str, Any]] = base_institution_specs()
        custom_institutions = self._custom_institutions()
        diagnostics = organization_diagnostics(self.db, custom_institutions)
        diagnostic_by_id = {
            str(item.get("id") or ""): item
            for item in diagnostics.get("institutions", [])
            if isinstance(item, dict)
        }

        active_snapshots: List[tuple[Character, CharacterRuntimeIdentity]] = []
        for character in self.content.characters.values():
            identity = identities[character.name]
            if identity.office_type == "后宫" or identity.power_id != "ming" or identity.status != "active":
                continue
            active_snapshots.append((character, identity))

        assigned_names: set[str] = set()

        def usable_parts(office: str) -> List[str]:
            parts = [part.strip() for part in normalize_office(office).split(",") if part.strip()]
            return [
                part for part in parts
                if not re.search(r"^(前|原)|罢居|候补|归途|潜在|少年|诸生|待铨|未仕", part)
            ]

        def holders_for(slot: Dict[str, Any]) -> List[Dict[str, Any]]:
            title = str(slot.get("title") or "").strip()
            terms = [str(item).strip() for item in (slot.get("match_terms") or [title]) if str(item).strip()]
            match_re = str(slot.get("match_regex") or "").strip()
            office_types = {str(item).strip() for item in (slot.get("office_types") or []) if str(item).strip()}
            holders: List[Dict[str, Any]] = []
            for character, identity in active_snapshots:
                parts = usable_parts(identity.office)
                text = " ".join(parts)
                hit = False
                if slot.get("office_type_only") and office_types and identity.office_type in office_types:
                    hit = True
                if not hit and terms:
                    hit = any(term in part for term in terms for part in parts)
                if not hit and match_re:
                    hit = any(re.search(match_re, part) for part in parts)
                if not hit and office_types and identity.office_type in office_types:
                    hit = any(term in text for term in terms)
                if hit:
                    assigned_names.add(character.name)
                    holders.append(
                        self.organization_character_card(
                            character,
                            runtime_identity=identity,
                        )
                    )
            return holders

        institutions: List[Dict[str, Any]] = []
        vacancy_total = 0
        assigned_total = 0
        for raw in [*base_institutions, *custom_institutions]:
            slots = []
            for slot in raw.get("slots", []):
                if not isinstance(slot, dict):
                    continue
                count = max(1, int(slot.get("count") or 1))
                holders = holders_for(slot)
                open_pool = bool(slot.get("open_pool"))
                effective_count = max(count, len(holders)) if open_pool else count
                filled = len(holders) if open_pool else min(len(holders), effective_count)
                vacancy = 0 if open_pool else max(0, effective_count - len(holders))
                overflow = 0 if open_pool else max(0, len(holders) - effective_count)
                vacancy_total += vacancy
                assigned_total += filled
                slots.append({
                    "title": str(slot.get("title") or ""),
                    "office_type": str(slot.get("office_type") or ""),
                    "count": effective_count,
                    "holders": holders,
                    "filled_count": filled,
                    "vacancies": vacancy,
                    "overflow_count": overflow,
                    "open_pool": open_pool,
                    "match_hint": str(slot.get("match_hint") or ""),
                })
            diag = diagnostic_by_id.get(str(raw.get("id") or raw.get("name") or ""), {})
            institutions.append({
                "id": str(raw.get("id") or raw.get("name") or ""),
                "name": str(raw.get("name") or ""),
                "category": str(raw.get("category") or "朝堂"),
                "mandate": str(raw.get("mandate") or ""),
                "custom": bool(raw.get("custom")),
                "readiness": int(diag.get("readiness") or 0),
                "coverage": int(diag.get("coverage") or 0),
                "holder_quality": int(diag.get("holder_quality") or 0),
                "execution_summary": str(diag.get("summary") or ""),
                "execution_risks": diag.get("risks") if isinstance(diag.get("risks"), list) else [],
                "slots": slots,
                "vacancy_count": sum(int(slot["vacancies"]) for slot in slots),
                "holder_count": sum(int(slot["filled_count"]) for slot in slots),
            })
        unassigned = [
            self.organization_character_card(
                character,
                runtime_row=runtime_rows.get(character.name) if runtime_rows is not None else None,
                runtime_identity=identity,
            )
            for character, identity in active_snapshots
            if character.name not in assigned_names
            and usable_parts(identity.office)
            and identity.office_type not in {"外臣"}
        ]
        unassigned.sort(key=lambda item: (str(item.get("office_type") or ""), str(item.get("name") or "")))
        return {
            "institutions": institutions,
            "vacancy_count": vacancy_total,
            "custom_count": len(custom_institutions),
            "assigned_count": assigned_total,
            "unassigned": unassigned,
            "court_readiness": int(diagnostics.get("court_readiness") or 0),
            "risk_count": int(diagnostics.get("risk_count") or 0),
            "execution_summary": str(diagnostics.get("summary") or ""),
            "overloaded_holders": diagnostics.get("overloaded_holders", []),
        }

    def _find_institution_slot(self, institution_id: str, slot_title: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        inst_id = str(institution_id or "").strip()
        title = str(slot_title or "").strip()
        if not inst_id or not title:
            raise HTTPException(status_code=400, detail="institution_id 与 slot_title 不能为空。")
        for inst in [*base_institution_specs(), *self._custom_institutions()]:
            if str(inst.get("id") or inst.get("name") or "") != inst_id:
                continue
            for slot in inst.get("slots") or []:
                if isinstance(slot, dict) and str(slot.get("title") or "") == title:
                    return inst, slot
        raise HTTPException(status_code=404, detail="未找到该官制空缺。")

    def _office_title_for_slot(self, inst: Dict[str, Any], slot: Dict[str, Any]) -> str:
        raw_title = str(slot.get("title") or "").strip()
        office_type = str(slot.get("office_type") or "").strip()
        first = re.split(r"\s*/\s*|／", raw_title)[0].strip()
        mapping = {
            "首辅": "内阁首辅",
            "次辅": "内阁次辅",
            "大学士": "大学士",
            "御史": "都察院御史",
            "少詹事": "少詹事",
            "翰林编检": "翰林院编修",
            "宫廷艺文": "经筵讲官",
            "司礼监掌印太监": "司礼监掌印太监",
            "司礼监秉笔太监": "司礼监秉笔太监",
            "司礼监随堂": "司礼监随堂太监",
            "监军太监": "监军太监",
            "提督东厂": "提督东厂太监",
            "锦衣卫指挥使": "锦衣卫指挥使",
            "北镇抚司": "北镇抚司理刑",
            "锦衣卫缇骑": "锦衣卫千户",
            "督师": "督师",
            "总督": "总督",
            "总兵": "总兵",
            "海防与水师": "水师总兵",
            "督抚": "巡抚",
            "府县官": "知府",
            "地方武备": "兵备道",
            "待铨": "待铨候补",
            "江湖异人": "待诏供奉",
        }
        if raw_title.endswith("属官") and office_type:
            return f"{office_type}郎中"
        if first in mapping:
            return mapping[first]
        if first.endswith("侍郎") or first.endswith("尚书"):
            return first
        if office_type and first in {"属官", "编检"}:
            return f"{office_type}{first}"
        inst_name = str(inst.get("name") or "").strip()
        return first or inst_name or "待铨候补"

    def _slot_requires_inner_court_identity(self, office_type: str, office_title: str) -> bool:
        text = f"{office_type} {office_title}"
        return is_eunuch_office(office_title, office_type) or bool(re.search(r"司礼监|东厂|太监|宦官|内廷|内官|小火者", text))

    def _row_is_inner_court_person(self, row: sqlite3.Row) -> bool:
        office = str(row["office"] or "")
        office_type = str(row["office_type"] or "")
        faction = str(row["faction"] or "")
        return is_eunuch_office(office, office_type) or bool(re.search(r"司礼监|东厂|太监|宦官|内廷|内官|小火者", f"{office} {office_type} {faction}"))

    def _vacancy_candidate_rows(self, office_type: str, method: str, office_title: str = "") -> List[sqlite3.Row]:
        inner_slot = self._slot_requires_inner_court_identity(office_type, office_title)
        status_filter = "AND status='active'" if method != "restore" else "AND status IN ('dismissed','retired','offstage')"
        rows = self.db.conn.execute(
            f"""
            SELECT name, office, office_type, faction, status, ability, wisdom, integrity,
                   loyalty, courage, force, charm
            FROM characters
            WHERE power_id='ming'
              AND office_type!='后宫'
              {status_filter}
            ORDER BY rowid
            """
        ).fetchall()
        out = []
        for row in rows:
            row_office = str(row["office"] or "")
            row_type = str(row["office_type"] or "")
            row_inner = self._row_is_inner_court_person(row)
            if inner_slot:
                if row_inner:
                    out.append(row)
                continue
            if row_inner:
                continue
            if method == "restore":
                out.append(row)
                continue
            if (
                row_type in {"待铨", "未仕"}
                or not row_office
                or any(token in row_office for token in ("待铨", "候补", "举贤", "待诏", "试用"))
            ):
                out.append(row)
                continue
            if method == "promote" and office_type and row_type == office_type:
                out.append(row)
        return out

    def _vacancy_candidate_score(self, row: sqlite3.Row, office_type: str, title: str) -> int:
        ability = int(row["ability"] or 50)
        wisdom = int(row["wisdom"] or ability)
        integrity = int(row["integrity"] or 50)
        loyalty = int(row["loyalty"] or 50)
        courage = int(row["courage"] or 50)
        force = int(row["force"] or 50)
        text = f"{office_type} {title}"
        if any(token in text for token in ("户部", "财政", "钱", "粮", "税")):
            score = ability * 0.25 + wisdom * 0.30 + integrity * 0.30 + loyalty * 0.15
        elif any(token in text for token in ("兵部", "边", "总兵", "督师", "监军", "武备")):
            score = ability * 0.22 + wisdom * 0.20 + courage * 0.22 + force * 0.22 + loyalty * 0.14
        elif any(token in text for token in ("司礼", "东厂", "锦衣", "内廷")):
            score = loyalty * 0.34 + ability * 0.22 + wisdom * 0.20 + courage * 0.14 + integrity * 0.10
        elif any(token in text for token in ("都察", "刑部", "御史", "法")):
            score = integrity * 0.35 + wisdom * 0.25 + courage * 0.18 + ability * 0.14 + loyalty * 0.08
        else:
            score = wisdom * 0.27 + ability * 0.24 + integrity * 0.22 + loyalty * 0.17 + courage * 0.10
        if str(row["office_type"] or "") in {"待铨", "未仕"}:
            score += 4
        return round(score)

    def _recruit_for_vacancy(self, office_type: str, method: str, office_title: str = "") -> str:
        if self._slot_requires_inner_court_identity(office_type, office_title):
            if method in {"exam", "recommend"}:
                raise HTTPException(status_code=400, detail="内廷宦官缺不能用科举或举贤补普通大臣；请补内侍或起复旧内臣。")
            result = self.recruit_eunuch()
        elif method == "recommend":
            result = self.recommend_hidden_official()
        elif method == "exam":
            result = self.recruit_exam_official()
        else:
            result = self.recommend_hidden_official()
        minister = result.get("minister") if isinstance(result, dict) else {}
        name = str((minister or {}).get("name") or "")
        if not name:
            raise HTTPException(status_code=500, detail="取士成功但未返回人名。")
        return name

    def fill_organization_vacancy(
        self,
        institution_id: str,
        slot_title: str,
        method: str = "auto",
    ) -> Dict[str, Any]:
        method = str(method or "auto").strip() or "auto"
        if method not in {"auto", "exam", "recommend", "promote", "restore"}:
            raise HTTPException(status_code=400, detail="method 只能是 auto/exam/recommend/promote/restore。")
        inst, slot = self._find_institution_slot(institution_id, slot_title)
        if bool(slot.get("open_pool")):
            raise HTTPException(status_code=400, detail="这是开放人才池，不是需要补的一人一缺。")
        current_org = self.organization_payload()
        inst_view = next((row for row in current_org.get("institutions", []) if str(row.get("id") or "") == str(inst.get("id") or inst.get("name") or "")), {})
        slot_view = next((row for row in inst_view.get("slots", []) if str(row.get("title") or "") == str(slot_title or "")), {})
        if int(slot_view.get("vacancies") or 0) <= 0:
            raise HTTPException(status_code=409, detail="此席当前没有空缺。")

        office_type = str(slot.get("office_type") or "").strip() or str(inst.get("name") or "").strip()
        office_title = self._office_title_for_slot(inst, slot)
        inner_slot = self._slot_requires_inner_court_identity(office_type, office_title)
        if inner_slot and method in {"exam", "recommend"}:
            raise HTTPException(status_code=400, detail="内廷宦官缺不能用科举或举贤补普通大臣；请补内侍或起复旧内臣。")
        candidate_rows = self._vacancy_candidate_rows(office_type, method, office_title)
        candidate_name = ""
        if candidate_rows and method not in {"exam", "recommend"}:
            ranked = sorted(
                candidate_rows,
                key=lambda row: self._vacancy_candidate_score(row, office_type, office_title),
                reverse=True,
            )
            candidate_name = str(ranked[0]["name"] or "")
        if not candidate_name and inner_slot and method == "restore":
            raise HTTPException(status_code=409, detail="没有可起复的旧内臣；请改用补内侍。")
        if not candidate_name:
            candidate_name = self._recruit_for_vacancy(office_type, method, office_title)

        status, _reason = self.db.get_character_status(candidate_name)
        if status != "active":
            self.db.set_character_status(self.state, candidate_name, "active", f"奉旨补{office_title}")
        self.db.set_character_office(
            candidate_name,
            office_title,
            office_type,
            source=f"补{str(inst.get('name') or '')}空缺：{slot_title}",
        )
        character = self.content.characters.get(candidate_name)
        if character is not None:
            character.office = office_title
            character.office_type = office_type
            character.status = "active"
            if self.session.registry is not None:
                self.session.registry.register(character)
        self.db.record_log(self.state, f"【补缺】{candidate_name}补{str(inst.get('name') or '')}·{slot_title}，授{office_title}。")
        organizations = self.organization_payload()
        return {
            "message": f"已补缺：{candidate_name}授{office_title}，入{str(inst.get('name') or office_type)}。",
            "minister": self.public_character(self.content.characters[candidate_name]) if candidate_name in self.content.characters else {"name": candidate_name},
            "office": office_title,
            "office_type": office_type,
            "institution_id": str(inst.get("id") or inst.get("name") or ""),
            "slot_title": str(slot_title),
            "organizations": compact_organization_payload(organizations),
        }

    def _generated_name(self, source: str) -> str:
        rng = self.character_rng
        surnames = [
            "沈", "陆", "顾", "钱", "严", "许", "方", "周", "赵", "韩", "曹", "董", "袁", "程", "夏", "魏",
            "陶", "邹", "邵", "潘", "吕", "姜", "秦", "汤", "俞", "贺", "戴", "毛", "姚", "范", "葛", "卢",
            "乔", "傅", "薛", "万", "龚", "孟", "庞", "牟", "骆", "施", "盛", "郁", "鲍", "祝", "裴", "闻",
        ]
        exam_given = [
            "承谟", "允中", "廷璧", "士衡", "景明", "伯修", "文炳", "维桢", "若愚", "子衡", "介夫", "鸣谦",
            "汝楫", "怀玉", "敬修", "季同", "履常", "慎言", "梦麟", "启泰", "元鼎", "观澜", "宗周", "思问",
            "以宁", "士奇", "文炜", "拱辰", "时行", "含章", "念祖", "式谷",
        ]
        eunuch_given = [
            "承恩", "守忠", "怀谨", "进忠", "奉节", "谨言", "守义", "承旨", "谨安", "福海", "德顺", "双喜",
            "怀灯", "添禄", "宝成", "小春", "砚秋", "守拙", "进宝", "长顺", "小满", "奉先", "怀璧", "听雨",
            "守灯", "玉成", "来喜", "小砚", "存谨", "承庆", "瑞安", "德昌",
        ]
        wild_given = [
            "有恒", "元亮", "道衡", "伯言", "子实", "闻道", "衡石", "汝霖", "石樵", "云路", "维岳", "野航",
            "希孟", "抱朴", "观海", "济川", "鸣岐", "松年", "斗南", "怀远", "时敏", "砺庵", "鹤洲", "慎微",
            "东野", "南金", "雨农", "季鹰", "履霜", "守冲", "望舒", "青简",
        ]
        givens = eunuch_given if source == "eunuch" else exam_given if source == "exam" else wild_given
        for _ in range(240):
            name = rng.choice(surnames) + rng.choice(givens)
            if name not in self.content.characters:
                return name
        for _ in range(80):
            fallback_given = rng.choice(givens) + rng.choice(["之", "仲", "季", "小", "元"]) + rng.choice(["衡", "谨", "舟", "石", "安"])
            name = f"{rng.choice(surnames)}{fallback_given}"
            if name not in self.content.characters:
                return name
        return f"{rng.choice(surnames)}{rng.choice(givens)}{self.state.turn}{rng.randint(10, 99)}"

    def _add_runtime_character(self, character: Character, source: str) -> Character:
        self.db.add_character(self.state, character, source=source)
        row = self.db.conn.execute(
            "SELECT portrait_id, office, office_type, faction FROM characters WHERE name=?", (character.name,)
        ).fetchone()
        if row:
            character.portrait_id = row["portrait_id"] or character.portrait_id
            character.office = row["office"] or character.office
            character.office_type = row["office_type"] or character.office_type
            character.faction = row["faction"] or character.faction
        self.content.characters[character.name] = character
        if self.session.registry is not None and character.status == "active":
            self.session.registry.register(character)
        self.chat_history.setdefault(character.name, [])
        self.maybe_queue_portrait_generation(character.name, source)
        return character

    def recruit_from_foundation(self, name: str) -> Dict[str, Any]:
        """从 NPC 数据基座起复/征辟真实历史人物入朝（人才池闭环）。"""
        from ming_sim.foundation import build_game_character, profile as foundation_profile
        if name in self.content.characters:
            existing = self.content.characters[name]
            if existing.status != "active":
                self.db.conn.execute(
                    "UPDATE characters SET status='active', status_reason='奉旨起复', "
                    "status_changed_turn=? WHERE name=?", (self.state.turn, name))
                self.db.conn.commit()
                existing.status = "active"
                if self.session.registry is not None:
                    self.session.registry.register(existing)
                return {"message": f"{name}奉旨起复，重列朝班。"}
            return {"message": f"{name}已在朝中。"}
        character = build_game_character(name, self.state.year)
        if character is None:
            raise HTTPException(status_code=404, detail=f"国朝人事档案中查无此人：{name}")
        added = self._add_runtime_character(character, "基座起复")
        prof = foundation_profile(name) or {}
        return {
            "message": f"征{added.name}入朝听用（{prof.get('native_place') or ''}人，"
                       f"原{prof.get('service_status') or '在野'}）。吏部将安排铨选。",
            "minister": self.public_character(added),
        }

    def _active_recommender_name(self, name: str) -> str:
        clean = str(name or "").strip()
        if not clean:
            return ""
        row = self.db.conn.execute(
            "SELECT name, status FROM characters WHERE name=? AND power_id='ming'",
            (clean,),
        ).fetchone()
        if row is None or str(row["status"] or "") != "active":
            return ""
        return str(row["name"] or "")

    def _append_character_summary_note(self, name: str, note: str) -> None:
        clean_name = str(name or "").strip()
        clean_note = str(note or "").strip()
        if not clean_name or not clean_note:
            return
        row = self.db.conn.execute(
            "SELECT summary FROM characters WHERE name=?",
            (clean_name,),
        ).fetchone()
        if row is None:
            return
        summary = str(row["summary"] or "")
        if clean_note in summary:
            return
        updated = f"{summary.rstrip()} {clean_note}".strip()[:800]
        self.db.conn.execute("UPDATE characters SET summary=? WHERE name=?", (updated, clean_name))
        self.db.conn.commit()
        character = self.content.characters.get(clean_name)
        if character is not None:
            character.summary = updated

    def _record_recommendation_link(
        self,
        name: str,
        recommender: str,
        basis: str,
        evidence: str = "",
        verified_recommender: bool = False,
    ) -> None:
        candidate = str(name or "").strip()
        sponsor = str(recommender or "").strip() if verified_recommender else self._active_recommender_name(recommender)
        if not candidate or not sponsor or candidate == sponsor:
            return
        try:
            from ming_sim import court
            day = max(0, int(self.state.turn) * 30)
            court.adjust_opinion(self.db, sponsor, candidate, +28, basis or "举荐入朝", day=day, reciprocal=False)
            court.adjust_opinion(self.db, candidate, sponsor, +34, "举主恩义", day=day, reciprocal=False)
        except Exception:
            pass
        note_bits = [f"举荐来源：{sponsor}"]
        if evidence:
            note_bits.append(f"依据：{evidence[:90]}")
        note_bits.append("风险：初入朝局，仍受举主关系牵引。")
        self._append_character_summary_note(candidate, "；".join(note_bits))

    def recruit_exam_official(self, recommender: str = "") -> Dict[str, Any]:
        rng = self.character_rng
        office = rng.choice([
            "翰林院庶吉士", "吏部主事", "户部主事", "兵部主事", "礼部主事", "工部营缮司主事",
            "六科给事中", "都察院试御史", "翰林院检讨", "南京户部主事",
        ])
        faction = rng.choice(["清流", "东林党", "中立", "实务派", "乡党"])
        origin = rng.choice([
            ("京师", "北直隶寒门出身，见过京畿饥荒与勋贵气焰"),
            ("南京", "江南士林出身，文章漂亮但也懂商税水路"),
            ("山西", "山西边地士子，熟悉军饷、驿路与边民疾苦"),
            ("陕西", "陕西灾年里考出的新进士，脸上有饥荒年代的硬气"),
            ("福建", "福建海路乡绅子弟，懂盐税、海商和地方械斗"),
            ("山东", "山东乡塾清贫出身，讲礼法也敢争一口硬气"),
        ])
        archetype = rng.choice([
            {
                "style": "新科锐气，重名分与章程，说话快，袖子也压不住锋芒",
                "summary": "殿试后仍带考场火气，急着在朝堂上证明自己。",
                "skills": ["科举", "奏对", "文书", "廷议", "条陈"],
            },
            {
                "style": "书生气未退，肯办事但怕背锅，遇事先把账册翻到最细",
                "summary": "擅长把杂乱钱粮拆成条目，但还不懂老官场的暗门。",
                "skills": ["科举", "文书", "钱粮核算", "案牍"],
            },
            {
                "style": "清峻寡言，年轻却有弹劾胆色，眼神像刚磨过的刀背",
                "summary": "在同年中以敢言出名，未必圆滑，但很难被轻易收买。",
                "skills": ["科举", "奏对", "弹章", "廷议"],
            },
            {
                "style": "温吞外表下藏着急智，惯用乡里见闻破题",
                "summary": "不像标准翰林，更像从地方泥水里捞出来的读书人。",
                "skills": ["科举", "地方见闻", "文书", "说服"],
            },
            {
                "style": "少年得志而自知根基浅，行礼过分端正，心里算盘很响",
                "summary": "懂得先观察派系风向，再把锋芒藏进漂亮文章里。",
                "skills": ["科举", "奏对", "观风", "辞令"],
            },
        ])
        ability = rng.randint(58, 80)
        wisdom = min(92, ability + rng.randint(4, 16))
        integrity = rng.randint(56, 88) if faction in {"清流", "东林党"} else rng.randint(46, 78)
        character = Character(
            name=self._generated_name("exam"),
            office=office,
            office_type=infer_office_type_from_office(office, "待铨"),
            faction=faction,
            aliases=[],
            personal_skills=list(dict.fromkeys(archetype["skills"])),
            loyalty=rng.randint(52, 80),
            ability=ability,
            integrity=integrity,
            courage=rng.randint(45, 76),
            style=archetype["style"],
            birth_year=self.state.year - rng.randint(22, 40),
            power_id="ming",
            location=origin[0],
            status="active",
            summary=(
                f"{self.state.year}年科举新进士，{origin[1]}；{archetype['summary']}"
                "短板：官场资历浅，遇老成部院容易被压住声气；风险：同年清议与乡党牵扯未明。"
            ),
            force=rng.randint(35, 58),
            wisdom=wisdom,
            charm=rng.randint(48, 74),
            luck=rng.randint(42, 84),
        )
        added = self._add_runtime_character(character, "科举取士")
        self._record_recommendation_link(added.name, recommender, "奉询荐取新科", "科场新进")
        return {
            "message": f"新科取士：{added.name}补入{added.office}。",
            "minister": self.public_character(added),
            "recommender": self._active_recommender_name(recommender),
        }

    def recruit_eunuch(self, recommender: str = "") -> Dict[str, Any]:
        rng = self.character_rng
        office = rng.choice([
            "司礼监小火者", "司礼监随堂太监", "司礼监书办太监", "司礼监文书房小内官",
            "御马监小内使", "乾清宫门下随侍", "内书堂识字小火者",
        ])
        archetype = rng.choice([
            {
                "style": "谨密奉旨，先复命后议理，眼睛总像在数门闩",
                "summary": "识字早，记性细，适合传旨、抄录和暗中核对口供。",
                "skills": ["内廷传旨", "宫禁熟习", "保密复命", "文书抄录"],
                "location": "司礼监值房",
            },
            {
                "style": "出身寒微，视入宫为正途，笑得快，跪得也快",
                "summary": "从苦日子里钻出来，愿意拼命换一条内廷上升路。",
                "skills": ["内廷传旨", "宫禁熟习", "跑腿探听", "执行"],
                "location": "紫禁城",
            },
            {
                "style": "言少手快，重皇命轻清议，袖中常攥着一枚小木牌",
                "summary": "不擅高谈，却极会按时把人、信、物送到该到之处。",
                "skills": ["保密复命", "宫禁熟习", "门禁调度", "执行"],
                "location": "乾清宫门外",
            },
            {
                "style": "机灵浮躁，爱抢话，怕挨打，但脑子转得像檐下急雨",
                "summary": "宫里新来的小内官，胆子还嫩，胜在反应快、耳朵尖。",
                "skills": ["内廷传旨", "察言观色", "跑腿探听", "宫禁熟习"],
                "location": "内书堂",
            },
            {
                "style": "沉默阴柔，行走贴墙，听见一句能记三天",
                "summary": "不显山露水，却很适合在内廷缝隙里替皇帝收细消息。",
                "skills": ["保密复命", "宫禁熟习", "暗访", "察言观色"],
                "location": "司礼监廊下",
            },
        ])
        loyalty = rng.randint(80, 97)
        character = Character(
            name=self._generated_name("eunuch"),
            office=office,
            office_type="司礼监",
            faction=rng.choice(["内廷", "阉党", "皇党"]),
            aliases=[],
            personal_skills=list(dict.fromkeys(archetype["skills"])),
            loyalty=loyalty,
            ability=rng.randint(44, 74),
            integrity=rng.randint(38, 76),
            courage=rng.randint(52, 84),
            style=archetype["style"],
            birth_year=self.state.year - rng.randint(15, 32),
            power_id="ming",
            location=archetype["location"],
            status="active",
            summary=(
                f"新入内廷的低品近侍。{archetype['summary']}太监是皇帝家奴，入仕路径正常；"
                "其能力未必压倒外朝，但忠诚与执行链清晰。短板：见识多限宫禁，骤掌外事易被老吏牵着走；"
                "风险：若被旧监房或掌印系统收拢，可能生出内廷小圈子。"
            ),
            force=rng.randint(36, 64),
            wisdom=rng.randint(44, 74),
            charm=rng.randint(38, 70),
            luck=rng.randint(46, 84),
        )
        added = self._add_runtime_character(character, "招募太监")
        self._record_recommendation_link(added.name, recommender, "内廷挑补", "内书堂/司礼监甄选")
        return {
            "message": f"内廷募入：{added.name}补入{added.office}。",
            "minister": self.public_character(added),
            "recommender": self._active_recommender_name(recommender),
        }

    def recommend_hidden_official(self, recommender: str = "") -> Dict[str, Any]:
        rng = self.character_rng
        identities = self._character_identities()
        status_by_name = {name: identity.status for name, identity in identities.items()}
        clean_recommender = self._active_recommender_name(recommender)
        if clean_recommender and clean_recommender in self.content.characters:
            active_recommenders = [self.content.characters[clean_recommender]]
        else:
            active_recommenders = [
                c for c in self.content.characters.values()
                if c.name in identities
                and identities[c.name].office_type != "后宫"
                and identities[c.name].power_id == "ming"
                and identities[c.name].status == "active"
            ]
        network_hits: List[Dict[str, Any]] = []
        for recommender in active_recommenders:
            for row in npc_network_recommendations(
                recommender.name,
                db=self.db,
                limit=12,
                include_statuses={"offstage", "dismissed", "retired"},
                status_by_name=status_by_name,
            ):
                if row.get("status") != "offstage":
                    continue
                network_hits.append({**row, "recommender": recommender.name})
        network_hits.sort(key=lambda item: int(item.get("score") or 0), reverse=True)
        if network_hits:
            top_score = int(network_hits[0].get("score") or 0)
            top_band = [item for item in network_hits[:10] if int(item.get("score") or 0) >= top_score - 8]
            hit = rng.choice(top_band or network_hits[:1])
            chosen = self.content.characters[str(hit["name"])]
            office = chosen.office or "待铨（举贤入京）"
            office_type = infer_office_type_from_office(office, chosen.office_type or "待铨")
            self.db.set_character_office(chosen.name, office, office_type, source="举贤发现")
            evidence = "；".join(_clean_obsidian_text(item) for item in hit.get("evidence", [])[:3])
            self.db.set_character_status(
                self.state, chosen.name, "active",
                f"{hit['recommender']}据人脉举荐：{evidence}"[:180],
            )
            chosen.office = office
            chosen.office_type = office_type
            chosen.status = "active"
            if self.session.registry is not None:
                self.session.registry.register(chosen)
            self.maybe_queue_portrait_generation(chosen.name, "举贤发现")
            self._record_recommendation_link(
                chosen.name,
                str(hit["recommender"]),
                "人脉举荐",
                evidence,
                verified_recommender=True,
            )
            return {
                "message": f"举贤发现：{hit['recommender']}举荐{chosen.name}出仕（{evidence}）。",
                "minister": self.public_character(chosen, status_by_name=status_by_name),
                "recommender": str(hit["recommender"]),
            }

        name = self._generated_name("recommend")
        origin = rng.choice([
            ("山西", "曾在边镇粮台做幕，懂军饷的黑洞，也懂小吏的手脚"),
            ("南直隶", "江南乡绅圈里有名，能说服士绅，也知道他们怕什么"),
            ("福建", "熟海商、盐税与械斗，手上有几条不写在公文里的门路"),
            ("陕西", "灾荒县里熬出来的塾师，见过流民、催科和逃亡册籍"),
            ("山东", "乡里公议推出来的人，硬气、倔，爱拿实情顶空话"),
            ("湖广", "跑过漕路和山路，知道地方官文牍之外的另一套秩序"),
        ])
        archetype = rng.choice([
            {
                "faction": "中立",
                "style": "在野有名，初入京师，先观望各派风向，袖里藏着地方账本",
                "summary": "不是标准官样人物，说话带泥土气，但看事很准。",
                "skills": ["地方阅历", "文书", "举贤", "民情"],
            },
            {
                "faction": "清流",
                "style": "清瘦倔强，被乡里称作硬骨头，进京后仍不肯学圆滑",
                "summary": "有清名也有锋芒，适合查弊，却容易得罪人。",
                "skills": ["地方阅历", "弹章", "清查", "举贤"],
            },
            {
                "faction": "实务派",
                "style": "眼神像账房先生，算盘打得响，话却说得粗直",
                "summary": "能把亏空、徭役和漕运拆成能办的步骤。",
                "skills": ["地方阅历", "钱粮核算", "文书", "调停"],
            },
            {
                "faction": "乡党",
                "style": "人情老辣，见官不怯，懂得先递台阶再递刀子",
                "summary": "靠地方声望入京，善结人脉，也可能被人脉牵住。",
                "skills": ["地方阅历", "举贤", "说合", "情报"],
            },
            {
                "faction": "中立",
                "style": "落拓幕客气，衣摆旧，眼睛亮，像随时要讲一段奇策",
                "summary": "半在官场、半在江湖，能办非常规的小事。",
                "skills": ["幕府阅历", "文书", "情报", "机变"],
            },
        ])
        character = Character(
            name=name,
            office="待铨（举贤入京）",
            office_type="待铨",
            faction=archetype["faction"],
            aliases=[],
            personal_skills=list(dict.fromkeys(archetype["skills"])),
            loyalty=rng.randint(46, 76),
            ability=rng.randint(52, 80),
            integrity=rng.randint(48, 86),
            courage=rng.randint(44, 78),
            style=archetype["style"],
            birth_year=self.state.year - rng.randint(28, 58),
            power_id="ming",
            location=origin[0],
            status="active",
            summary=(
                f"由地方举荐入京的在野人物，{origin[1]}；{archetype['summary']}"
                "短板：朝中根基浅，容易被举主或乡党标签牵累；风险：本领多在地方，入京后未必懂部院规矩。"
            ),
            force=rng.randint(34, 60),
            wisdom=rng.randint(52, 82),
            charm=rng.randint(46, 78),
            luck=rng.randint(38, 86),
        )
        added = self._add_runtime_character(character, "举贤入京")
        sponsor = clean_recommender or (rng.choice(active_recommenders).name if active_recommenders else "")
        self._record_recommendation_link(
            added.name,
            sponsor,
            "举贤入京",
            "地方声望/人脉风闻",
            verified_recommender=bool(sponsor),
        )
        return {
            "message": f"举贤入京：{added.name}列入待铨。",
            "minister": self.public_character(added),
            "recommender": sponsor,
        }

    def _castration_consent_note(self, name: str) -> Optional[Dict[str, Any]]:
        agreement = self.db.has_successful_agreement(
            name,
            "castration",
            max_age_turns=12,
            current_turn=self.state.turn,
        )
        if agreement is not None:
            return {
                "stance": "support",
                "handshake_status": HANDSHAKE_SEALED,
                "summary": str(agreement.get("summary") or "已有净身入内廷的握手协议。"),
                "conditions": str(agreement.get("conditions") or ""),
                "agreement": agreement,
            }
        for goal in self.db.list_conversation_goals(
            minister_name=name,
            statuses=["active", "waiting_conditions", "sealed", "blocked", "expired"],
            limit=12,
        ):
            if str(goal.get("action_kind") or "") != "castration":
                continue
            status = str(goal.get("status") or "")
            if status == "sealed":
                handshake_status = HANDSHAKE_SEALED
            elif status == "waiting_conditions":
                handshake_status = HANDSHAKE_CONDITIONAL
            elif status == "blocked":
                handshake_status = HANDSHAKE_BLOCKED
            else:
                handshake_status = "none"
            conditions = "；".join(
                str(item.get("description") or "")
                for item in (goal.get("conditions") or [])
                if isinstance(item, dict) and str(item.get("status") or "pending") != "done"
            )
            return {
                "stance": "support" if handshake_status == HANDSHAKE_SEALED else "caution",
                "handshake_status": handshake_status,
                "summary": str(goal.get("title") or goal.get("target_text") or "净身入内廷奏对目的"),
                "conditions": conditions,
                "goal": goal,
            }
        latest_relevant: Optional[Dict[str, Any]] = None
        for row in self.db.list_minister_stances(turn=self.state.turn, minister_name=name, limit=12):
            psychological = row.get("psychological") if isinstance(row.get("psychological"), dict) else {}
            action_kind = str(psychological.get("action_kind") or "")
            if action_kind and action_kind != "castration":
                continue
            text = f"{row.get('topic', '')} {row.get('summary', '')} {row.get('conditions', '')}"
            if not action_kind and not re.search(r"净身|入宫|内廷|司礼监|太监|宦官|宫禁", text):
                continue
            latest_relevant = row
            if row.get("handshake_status") == HANDSHAKE_SEALED:
                return row
            break
        return latest_relevant

    def _emancipation_consent_note(self, name: str) -> Optional[Dict[str, Any]]:
        agreement = self.db.has_successful_agreement(
            name,
            "emancipation",
            max_age_turns=12,
            current_turn=self.state.turn,
        )
        if agreement is not None:
            return {
                "stance": "support",
                "handshake_status": HANDSHAKE_SEALED,
                "summary": str(agreement.get("summary") or "已有奴籍转民籍的握手协议。"),
                "conditions": str(agreement.get("conditions") or ""),
                "agreement": agreement,
            }
        for goal in self.db.list_conversation_goals(
            minister_name=name,
            statuses=["active", "waiting_conditions", "sealed", "blocked", "expired"],
            limit=12,
        ):
            if str(goal.get("action_kind") or "") != "emancipation":
                continue
            status = str(goal.get("status") or "")
            if status == "sealed":
                handshake_status = HANDSHAKE_SEALED
            elif status == "waiting_conditions":
                handshake_status = HANDSHAKE_CONDITIONAL
            elif status == "blocked":
                handshake_status = HANDSHAKE_BLOCKED
            else:
                handshake_status = "none"
            conditions = "；".join(
                str(item.get("description") or "")
                for item in (goal.get("conditions") or [])
                if isinstance(item, dict) and str(item.get("status") or "pending") != "done"
            )
            return {
                "stance": "support" if handshake_status == HANDSHAKE_SEALED else "caution",
                "handshake_status": handshake_status,
                "summary": str(goal.get("title") or goal.get("target_text") or "奴籍转民籍奏对目的"),
                "conditions": conditions,
                "goal": goal,
            }
        latest_relevant: Optional[Dict[str, Any]] = None
        for row in self.db.list_minister_stances(turn=self.state.turn, minister_name=name, limit=12):
            psychological = row.get("psychological") if isinstance(row.get("psychological"), dict) else {}
            action_kind = str(psychological.get("action_kind") or "")
            if action_kind and action_kind != "emancipation":
                continue
            text = f"{row.get('topic', '')} {row.get('summary', '')} {row.get('conditions', '')}"
            if not action_kind and not re.search(r"奴籍|民籍|脱籍|还民|转为民|转民籍|出宫为民|归为百姓|赐还为民", text):
                continue
            latest_relevant = row
            if row.get("handshake_status") == HANDSHAKE_SEALED:
                return row
            break
        return latest_relevant

    def _current_office_identity(self, character: Character) -> tuple[str, str, str]:
        row = self.db.conn.execute(
            "SELECT office, office_type, faction FROM characters WHERE name=?", (character.name,)
        ).fetchone()
        office = (row["office"] if row else character.office) or character.office or ""
        office_type = (row["office_type"] if row else character.office_type) or character.office_type or ""
        faction = (row["faction"] if row else character.faction) or character.faction or ""
        return office, effective_stored_office_type(office, office_type), faction

    def _castration_applicable(self, character: Character) -> bool:
        office, office_type, faction = self._current_office_identity(character)
        if character.office_type == "后宫" or office_type == "后宫":
            return False
        if is_eunuch_office(office, office_type) or re.search(r"太监|宦官|内官|内廷", faction):
            return False
        text = f"{office} {office_type} {faction}"
        if re.search(r"民籍|百姓|布衣|江湖|商人|隐士|传教士|后金|蒙古|朝鲜|流寇", text):
            return False
        return bool(re.search(r"内阁|吏部|户部|礼部|兵部|刑部|工部|都察院|翰林|地方|边镇|锦衣卫|待铨|官|将|督|抚|御史|尚书|侍郎|郎中|主事|总兵|千户|百户", text))

    def castrate_official(self, name: str, force: bool = False, scheme_text: str = "") -> Dict[str, Any]:
        clean_name = (name or "").strip()
        character = self.content.characters.get(clean_name)
        if character is None or character.office_type == "后宫":
            raise HTTPException(status_code=404, detail=f"未找到可改入内廷的人物：{clean_name}")
        if self.character_power_id(character) != "ming":
            raise HTTPException(status_code=409, detail=f"{clean_name}不属大明，不能入内廷。")
        status, reason = self.db.get_character_status(clean_name)
        if status != "active":
            raise HTTPException(status_code=409, detail=f"{clean_name}当前{_STATUS_LABEL_WEB.get(status, status)}，不可改入内廷。{reason}")
        if not self._castration_applicable(character):
            raise HTTPException(status_code=409, detail=f"{clean_name}并非可改入内廷的文官或武官。")
        consent = self._castration_consent_note(clean_name)
        if not force:
            if not consent:
                raise HTTPException(
                    status_code=409,
                    detail=f"尚未与{clean_name}奏对谈妥净身入内廷。请先召对劝说，待心理量表握手成功后，再行身份转换；否则只能下旨强行净身。",
                )
            if consent.get("handshake_status") != HANDSHAKE_SEALED:
                status = str(consent.get("handshake_status") or "none")
                if status == HANDSHAKE_CONDITIONAL:
                    tasks = (consent.get("agreement") or {}).get("tasks") if isinstance(consent.get("agreement"), dict) else []
                    todo = "；".join(str(item.get("description") or "") for item in tasks if isinstance(item, dict)) or str(consent.get("conditions") or "")
                    detail = f"{clean_name}只是附条件松口，尚未履约闭环：{todo or consent.get('summary', '条件不明')}。"
                elif status == HANDSHAKE_BLOCKED:
                    detail = f"{clean_name}未被说服（{consent.get('summary', '态度不明')}）。"
                else:
                    detail = f"{clean_name}本回合没有形成净身握手协议（{consent.get('summary', '态度不明')}）。"
                raise HTTPException(
                    status_code=409,
                    detail=detail + "若陛下仍要执行，只能下旨强行净身。",
                )
        new_office = "司礼监随堂太监"
        source = "强旨改入内廷" if force else "自愿改入内廷"
        character, political_reactions = convert_character_to_eunuch(
            self.db,
            self.state,
            self.content,
            clean_name,
            force=force,
            source=source,
            new_office=new_office,
            lore_text=scheme_text,
        )
        if self.session.registry is not None:
            self.session.registry.register(character)
        self.maybe_queue_portrait_generation(character.name, source)
        prefix = "强旨已下，" if force else ""
        reaction_text = f" 朝局反应：{political_reactions[0].get('summary')}" if political_reactions else ""
        relationship_text = " 关系记忆：强旨会留下身体与名节旧怨。" if force else " 关系记忆：自愿入内廷可作为近侍履约背书。"
        return {
            "message": f"{prefix}{clean_name}已净身入内廷，补为{new_office}。{reaction_text}{relationship_text}",
            "minister": self.public_character(character),
            "political_reactions": political_reactions,
        }

    def emancipate_eunuch(self, name: str, force: bool = False) -> Dict[str, Any]:
        clean_name = (name or "").strip()
        character = self.content.characters.get(clean_name)
        if character is None or character.office_type == "后宫":
            raise HTTPException(status_code=404, detail=f"未找到可转民籍的太监：{clean_name}")
        if self.character_power_id(character) != "ming":
            raise HTTPException(status_code=409, detail=f"{clean_name}不属大明，不能由内廷转出。")
        status, reason = self.db.get_character_status(clean_name)
        if status != "active":
            raise HTTPException(status_code=409, detail=f"{clean_name}当前{_STATUS_LABEL_WEB.get(status, status)}，不可转民籍。{reason}")
        office, office_type, faction = self._current_office_identity(character)
        if not (is_eunuch_office(office, office_type) or re.search(r"太监|宦官|内官|内廷", f"{faction} {office} {office_type}")):
            raise HTTPException(status_code=409, detail=f"{clean_name}并非太监/内廷奴籍，不适用奴籍转民籍。")
        consent = self._emancipation_consent_note(clean_name)
        if not force:
            if not consent:
                raise HTTPException(
                    status_code=409,
                    detail=f"尚未与{clean_name}奏对谈妥奴籍转民籍。请先劝导，待心理量表握手成功后，再行身份转换；否则只能下旨强行脱籍。",
                )
            if consent.get("handshake_status") != HANDSHAKE_SEALED:
                status = str(consent.get("handshake_status") or "none")
                if status == HANDSHAKE_CONDITIONAL:
                    tasks = (consent.get("agreement") or {}).get("tasks") if isinstance(consent.get("agreement"), dict) else []
                    todo = "；".join(str(item.get("description") or "") for item in tasks if isinstance(item, dict)) or str(consent.get("conditions") or "")
                    detail = f"{clean_name}只是附条件松口，尚未履约闭环：{todo or consent.get('summary', '条件不明')}。"
                elif status == HANDSHAKE_BLOCKED:
                    detail = f"{clean_name}未被说服（{consent.get('summary', '态度不明')}）。"
                else:
                    detail = f"{clean_name}本回合没有形成奴籍转民籍握手协议（{consent.get('summary', '态度不明')}）。"
                raise HTTPException(
                    status_code=409,
                    detail=detail + "若陛下仍要执行，只能下旨强行脱籍。",
                )
        source = "强旨奴籍转民籍" if force else "自愿奴籍转民籍"
        character, political_reactions = convert_eunuch_to_commoner(
            self.db,
            self.state,
            self.content,
            clean_name,
            force=force,
            source=source,
        )
        if self.session.registry is not None:
            self.session.registry.register(character)
        self.maybe_queue_portrait_generation(character.name, source)
        prefix = "强旨已下，" if force else ""
        relationship_text = "关系记忆：强旨脱籍会留下安身与名分旧怨。" if force else "关系记忆：自愿脱籍可作为身份履约完成记录。"
        return {
            "message": f"{prefix}{clean_name}已脱离内廷奴籍，转为民籍百姓；新立绘将改为布衣头巾。{relationship_text}",
            "minister": self.public_character(character),
            "political_reactions": political_reactions,
        }

    def perform_consort_action(self, name: str, action: str) -> Dict[str, Any]:
        consort = self.content.characters.get((name or "").strip())
        if consort is None or consort.office_type != "后宫":
            raise HTTPException(status_code=404, detail=f"未找到后宫人物：{name}")
        status, _reason = self.db.get_character_status(consort.name)
        if status != "active":
            raise HTTPException(status_code=409, detail=f"{consort.name}尚未入宫或不可行动。")
        actions = {
            "stabilize": ("协理六宫", "宫务裁断", "晓宫禁恩威", "皇威", 1),
            "treasury": ("盘点内库", "内库盘点", "谨慎钱粮", "内库", random.randint(3, 8)),
            "appease": ("安抚内廷", "内廷调停", "能缓和宫禁怨气", "皇威", 1),
        }
        if action == "recommend":
            rng = self.character_rng
            archetype = rng.choice([
                ("由宫中举荐，入册待选，礼数端正但眼神很会看人", "熟宫礼，善察言观色，像是已经学会在廊下少说半句。", ["宫礼", "察言观色"]),
                ("小心机灵，笑意轻快，走路总比旁人快半步", "由内廷女眷举荐入册，胜在反应快、记性好。", ["宫礼", "记诵", "察言观色"]),
                ("清冷少言，身段端稳，像在热闹处也能独自站住", "由宫中举荐入册，性子不热络，但很守规矩。", ["宫礼", "女红", "自持"]),
                ("活泼胆大，初入待选名册仍藏不住好奇心", "由宫中举荐入册，未必最端庄，却很有鲜活气。", ["宫礼", "歌舞", "察言观色"]),
            ])
            candidate = Character(
                name=self._generated_name("recommend"),
                office="采女（待选）",
                office_type="后宫",
                faction="后宫",
                aliases=[],
                personal_skills=list(dict.fromkeys(archetype[2])),
                loyalty=rng.randint(52, 78),
                ability=rng.randint(42, 68),
                integrity=rng.randint(45, 80),
                courage=rng.randint(35, 64),
                style=archetype[0],
                power_id="ming",
                location="紫禁城",
                status="candidate",
                summary=f"由{consort.name}举荐入册的宫人，{archetype[1]}待皇帝拣选。",
                charm=rng.randint(52, 84),
                luck=rng.randint(45, 84),
            )
            self._add_runtime_character(candidate, f"{consort.name}举荐宫人")
            return {"message": f"{consort.name}举荐{candidate.name}入待选名册。", "candidate": self.public_character(candidate)}
        if action not in actions:
            raise HTTPException(status_code=400, detail="未知后宫行动。")
        label, skill, trait, metric, delta = actions[action]
        self.db.cultivate_consort(consort.name, self.state.turn, skill=skill, trait=trait)
        if metric in {"国库", "内库"}:
            self.db.record_issue_economy_move(self.state, metric, delta, label, f"{consort.name}{label}")
        else:
            self.state.metrics[metric] = max(0, min(100, int(self.state.metrics.get(metric, 0)) + delta))
            self.db.save_state(self.state)
        return {
            "message": f"{consort.name}已{label}：{metric}+{delta}。",
            "consort": self.public_character(consort),
        }

    def agreement_payload(self, minister_name: str = "") -> List[Dict[str, Any]]:
        rows = self.db.list_negotiation_agreements(minister_name=minister_name, limit=80)
        out: List[Dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["verbal_only"] = bool(int(item.get("verbal_only") or 0))
            item["handshake_label"] = handshake_label(str(item.get("handshake_status") or "none"))
            item["core_topic"] = item.get("core_topic") or item.get("topic") or ""
            try:
                item["auto_review"] = json.loads(str(item.get("auto_review_json") or "{}"))
            except Exception:
                item["auto_review"] = {}
            try:
                item["llm_review"] = json.loads(str(item.get("llm_review_json") or "{}"))
            except Exception:
                item["llm_review"] = {}
            try:
                item["political_effect"] = json.loads(str(item.get("political_effect_json") or "{}"))
            except Exception:
                item["political_effect"] = {}
            item["tasks"] = [dict(task) for task in (item.get("tasks") or []) if isinstance(task, dict)]
            try:
                item["execution_consequence"] = self.db._agreement_execution_consequence(item, item["tasks"])
            except Exception:
                item["execution_consequence"] = ""
            out.append(item)
        return out

    def conversation_goal_payload(self, minister_name: str = "", limit: int = 80) -> List[Dict[str, Any]]:
        if minister_name:
            rows = self.db.list_conversation_goals(minister_name=minister_name, limit=limit)
        else:
            rows = self.db.list_conversation_goals(limit=limit)
        return self._conversation_goal_payload_from_rows(rows)

    def _run_web_payload_hook(self, route: str, payload: Dict[str, Any], *, method: str = "") -> Dict[str, Any]:
        """Run trusted payload hooks without letting handlers touch WebGame internals."""
        return run_web_payload_hook(self.session.hook_runner, route, payload, method=method)

    def web_payload_response(self, route: str, payload: Dict[str, Any], *, method: str = "") -> Dict[str, Any]:
        """Apply the declared Web payload contract hook at route boundaries."""
        return self._run_web_payload_hook(route, payload, method=method)

    def state_payload(self) -> Dict[str, Any]:
        runtime_rows = self._character_runtime_rows()
        portrait_assets = self._portrait_asset_meta_map()
        directives = [self.directive_payload(row) for row in self.directive_rows()]
        regions = self.db.region_payload()
        armies = self.db.army_payload()
        # 文书化黑箱（S3）：玩家口径的明军兵额是兵部账面值（含空饷虚冒），
        # 真值只驱动推演/战斗；盘验揭穿后才显实数。
        try:
            from ming_sim.veil import army_reported_overlay
            armies = army_reported_overlay(self.db, armies)
        except Exception:
            pass
        budget_lines = compute_budget_lines(self.db, self.state)
        treasury = self.db.treasury_report(self.state, budget=budget_lines)
        budget = self.budget_payload(budget=budget_lines)
        identities = self._character_identities(runtime_rows)

        ministers: List[Dict[str, Any]] = []
        consorts: List[Dict[str, Any]] = []
        for character in self.content.characters.values():
            identity = identities[character.name]
            if identity.power_id != "ming":
                continue
            card = self.public_character(
                character,
                include_detail=False,
                runtime_row=runtime_rows.get(character.name),
                runtime_identity=identity,
                portrait_assets=portrait_assets,
            )
            if identity.office_type == "后宫":
                if identity.status == "active":
                    consorts.append(card)
            else:
                ministers.append(card)

        payload = {
            "turn": {"year": self.state.year, "period": self.state.period,
                     "turn": self.state.turn, "phase": self.state.turn_phase},
            "metrics": self.state.metrics,
            "previous_summary": self.previous_summary,
            "treasury": treasury,
            "issue_fields": list(ISSUE_FIELDS),
            "issues": compact_issues(self.issue_payloads()),
            "legacy_fields": list(LEGACY_FIELDS),
            "legacies": compact_legacies(self.legacies_payload()),
            "closed_this_turn": self.closed_this_turn_payloads(),
            "budget": budget,
            "power_fields": list(POWER_FIELDS),
            "powers": compact_powers(self.db.power_payload()),
            "victory_status": self.session.victory(),
            "ending": self.ending_payload(),
            "events": [],
            "region_fields": list(REGION_FIELDS),
            "regions": compact_regions(regions),
            "army_fields": list(ARMY_FIELDS),
            "armies": compact_armies(armies),
            "map_nodes": self.map_nodes(regions=regions, armies=armies, include_detail=False),
            "minister_fields": list(CHARACTER_CARD_FIELDS),
            "ministers": compact_character_cards(ministers),
            "consorts": compact_character_cards(consorts),
            "directives": directives,
            "agreements": self.agreement_payload(),
            "conversation_goals": self.conversation_goal_payload(),
            "pending_count": self.session.pending_count(),
            "last_decree": self.last_decree,
            "last_report": self.last_report,
            # 传奇文字冒险新增数据
            "adventures": self.adventure_payload(),
            "items": self.item_payload(),
        }
        return self.web_payload_response("/api/game/state", payload)

    def situation_reports_payload(self) -> Dict[str, str]:
        """Build heavier situation prose only when a panel explicitly asks for it."""
        return {
            "region_warning": self.db.region_report(limit=5),
            "army_warning": self.db.army_report(limit=5),
            "power_warning": self.db.power_report(exclude_self=True),
        }

    # ── 聊天 ──────────────────────────────────────────────────────────────
    def _persistent_chat_minister(self, minister_name: str) -> bool:
        return minister_name not in self.session.temporary_characters

    def _minister_agno_session_id(self, minister_name: str) -> str:
        registry = self.session.registry
        if registry is None:
            campaign_id = (self.db.kv_get("campaign_id") or getattr(self.session, "campaign_id", "") or "legacy").strip()
            return f"npc-{campaign_id}-{minister_name}"
        return registry.session_ids.get(minister_name, f"npc-{registry.campaign_id}-{minister_name}")

    def can_undo_last_chat(self, minister_name: str) -> bool:
        if not self._persistent_chat_minister(minister_name):
            return False
        if self.state.turn_phase not in ("summoning", "reviewing"):
            return False
        return self.db.can_undo_last_chat_turn(minister_name, self.state.turn)

    def _start_chat_turn(self, minister_name: str) -> tuple[int, Dict[str, Any]]:
        agno_session_id = self._minister_agno_session_id(minister_name)
        runs_before = self.db.agno_runs_length(agno_session_id)
        snapshot = self.db.capture_chat_rollback_snapshot()
        chat_turn_id = self.db.create_chat_turn(
            self.state,
            minister_name,
            agno_session_id,
            runs_before,
        )
        return chat_turn_id, snapshot

    def _record_chat_rollback_items(
        self,
        chat_turn_id: int,
        before_snapshot: Dict[str, Any],
    ) -> None:
        if not chat_turn_id:
            return
        after_snapshot = self.db.capture_chat_rollback_snapshot()
        self.db.record_chat_turn_rollback_diffs(chat_turn_id, before_snapshot, after_snapshot)

    def _attendant_summon_target(self, minister_name: str, text: str) -> Dict[str, Any]:
        """Deterministic fallback for the player telling the attending eunuch to summon someone.

        The LLM is still allowed to use the summon_minister tool, but this path
        keeps the core court flow reliable when the model merely says "奴婢遵旨"
        without emitting a tool call.
        """

        current = self._summon_handler_character(minister_name)
        if current is None:
            return {}
        raw = str(text or "").strip()
        if not raw:
            return {}
        if re.search(r"(净身|宫刑|腐刑|去势|阉割|阉了|阉掉|发净军|没入内廷|入宫为奴|入内廷为奴)", raw):
            return {}
        summon_verb = r"(?:召|传|宣|请|唤|叫|找|寻|带|领|引|拉|让|命|令|把)"
        summon_arrival = r"(?:见面|过来|面圣|面前|御前|来|到|入|觐|见|聊|谈|奏对|问对|进殿|入殿)"
        direct_arrival = r"(?:见面|过来|面圣|来|入|觐|见|聊|谈|奏对|问对|进殿|入殿)"
        summon_requested = bool(
            re.search(fr"{summon_verb}.{{0,16}}{summon_arrival}", raw)
            or re.search(fr"^[\u4e00-\u9fff]{{2,4}}[？?，,、\s]*{direct_arrival}", raw)
        )
        short_named_summon = bool(re.search(
            r"(?:^|[，,。；;！!\s、])(?:找|寻|召|传|宣|请|唤|叫)"
            r"(?:了|一下|一声)?[\u4e00-\u9fff]{2,4}(?:吧|罢|[？?，,。！!\s、]|$)",
            raw,
        ))
        followup_requested = self._attendant_summon_followup_requested(raw)
        selection_followup = self._attendant_named_selection_requested(minister_name, raw)
        if not (summon_requested or short_named_summon or followup_requested or selection_followup):
            return {}
        candidates: List[Character] = []
        if summon_requested or short_named_summon or selection_followup or followup_requested:
            for character in self.session.content.characters.values():
                if character.name == minister_name:
                    continue
                haystacks = [character.name, *self._linkable_character_aliases(character)]
                if not any(alias and str(alias) in raw for alias in haystacks):
                    continue
                try:
                    target, _is_temporary = self.session.summon_character(character.name, current, allow_temporary=False)
                except ValueError:
                    continue
                ok, _reason = self.session.can_summon(target)
                if ok:
                    candidates.append(target)
        if candidates:
            candidates.sort(key=lambda c: len(c.name), reverse=True)
            self._clear_pending_dialogue_action(minister_name)
            return {"name": candidates[0].name, "generated": False, "source": "known"}
        if followup_requested:
            recent_name = self._recent_attendant_implied_summon_name(minister_name)
            if recent_name and recent_name != minister_name and recent_name in self.content.characters:
                try:
                    target, _is_temporary = self.session.summon_character(recent_name, current, allow_temporary=False)
                except ValueError:
                    target = None
                if target is not None:
                    ok, _reason = self.session.can_summon(target)
                    if ok:
                        self._clear_pending_dialogue_action(minister_name)
                        return {"name": target.name, "generated": False, "source": "followup"}
        generated = self._materialize_dialogue_mention_from_text(minister_name, raw)
        if not generated:
            return {}
        try:
            target, _is_temporary = self.session.summon_character(generated.name, current, allow_temporary=False)
        except ValueError:
            return {}
        ok, _reason = self.session.can_summon(target)
        if not ok:
            return {}
        self._clear_pending_dialogue_action(minister_name)
        source = "selection" if selection_followup else "followup" if followup_requested else "direct"
        return {"name": target.name, "generated": True, "source": source}

    def _attendant_summon_followup_requested(self, text: str) -> bool:
        raw = str(text or "").strip()
        if not raw:
            return False
        name = r"(?:小[\u4e00-\u9fff]{1,2}子|[\u4e00-\u9fff]{2,4})"
        return bool(
            re.search(
                r"(人呢|他呢|她呢|在哪|哪去了|带来|领来|引来|进来|入殿|"
                r"我来和[他她]说话|我和[他她]说话|让[他她]进来|叫[他她]进来|传[他她]|"
                r"怎么还不来|还没来|还不来|没人出现|没有人出现)",
                raw,
            )
            or re.search(fr"{name}.{{0,10}}(?:人呢|在哪|哪去了|怎么还不来|还没来|还不来|没来|不来|没出现|没人出现|没有人出现)", raw)
            or re.search(fr"(?:叫|传|召|唤|找|请)(?:了|过)?[^\n，。；：]{{0,14}}?{name}[^\n，。；：]{{0,16}}?(?:很久|半天|许久)[^\n，。；：]{{0,16}}?(?:没|不|未)[^\n，。；：]{{0,8}}?(?:来|到|进|见|出现)", raw)
        )

    def _attendant_named_selection_requested(self, minister_name: str, text: str) -> bool:
        """Treat a named choice from the attendant's recent suggestions as a summons.

        Players often answer a recruitment shortlist with "换一个，小禄子" or
        "就小禄子" instead of issuing a formal "传某入殿" command. In that
        context the natural court action is to materialize and summon the named
        person, not to leave the attendant roleplaying the handoff.
        """

        raw = str(text or "").strip()
        if not raw:
            return False
        stored = self._load_unknown_dialogue_mentions()
        names = [name for name in stored if name and name in raw]
        single_for_minister = [
            name for name, value in stored.items()
            if str(value.get("source_minister") or "") == minister_name
        ]
        if (
            not names
            and len(single_for_minister) == 1
            and re.search(r"(?:就|要|选|挑|取)(?:他|她|这人|此人|这个|这个人|这位)(?:吧|罢|[。！？!?\s]*$)", raw)
        ):
            return True
        if not names:
            names = [
                character.name
                for character in self.session.content.characters.values()
                if character.name != minister_name and character.name and character.name in raw
            ]
        if not names:
            return False
        selection_words = (
            r"(换|另|就|要|选|挑|取|这个|那个|这位|那位|先把|先叫|先传|先带|"
            r"看看|过目|见见|试试|问问|人呢|在哪|进来|入殿)"
        )
        if re.search(selection_words, raw):
            return True
        stripped = re.sub(r"[？?，,。！!\s、]+", "", raw)
        confirm_prefixes = ("好", "好的", "行", "成", "准", "可以", "就", "要", "选", "挑", "取", "传", "叫", "带")
        if any(
            stripped in {name, *(f"{prefix}{name}" for prefix in confirm_prefixes), f"就{name}吧", f"要{name}吧"}
            for name in names
        ):
            return True
        if re.search(
            r"(?:好|好的|行|成|准|可以|就|要|选|挑|取)[，,、\s]*(?:这个|那个|这位|那位)?"
            r"(?:小[\u4e00-\u9fff]{1,2}子|[\u4e00-\u9fff]{2,4})(?:吧|罢|[。！？!?\s]*$)",
            raw,
        ):
            return True
        if len(single_for_minister) == 1 and re.search(r"(?:就|要|选|挑|取)(?:他|她|这人|此人|这个|这个人|这位)(?:吧|罢|[。！？!?\s]*$)", raw):
            return True
        return False

    def _attendant_summon_answer(self, target_name: str, generated: bool = False, source: str = "") -> str:
        if generated:
            if source == "followup":
                return f"奴婢失察。{target_name}已按陛下催问补入名册，这就领入御前，不再让陛下空等。"
            if source == "selection":
                return f"奴婢遵旨。{target_name}按方才荐名补入名册，奴婢这就传其趋入御前。"
            if source == "direct":
                return f"奴婢遵旨。{target_name}既是陛下点名要见的人，奴婢已按线索补入名册，这就传其趋入御前。"
            return f"奴婢遵旨。{target_name}原只是奏对里露出的名目，奴婢已按线索补入名册，这就传其趋入御前。"
        title = f"{target_name}大人"
        try:
            target = self.session._character(target_name)
            if is_eunuch_office(target.office, target.office_type):
                title = target_name
        except Exception:
            pass
        return f"奴婢遵旨，这就传{title}趋入御前。"

    def _dialogue_action_key(self, minister_name: str) -> str:
        return f"dialogue.pending_action.{str(minister_name or '').strip()}"

    def _load_pending_dialogue_action(self, minister_name: str) -> Dict[str, Any]:
        raw = self.db.kv_get(self._dialogue_action_key(minister_name))
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        try:
            if int(data.get("turn") or self.state.turn) < int(self.state.turn):
                self._clear_pending_dialogue_action(minister_name)
                return {}
        except (TypeError, ValueError):
            pass
        return data

    def _store_pending_dialogue_action(self, minister_name: str, action: Dict[str, Any]) -> None:
        payload = dict(action)
        payload["minister"] = minister_name
        payload["turn"] = int(self.state.turn)
        self.db.kv_set(self._dialogue_action_key(minister_name), json.dumps(payload, ensure_ascii=False))

    def _clear_pending_dialogue_action(self, minister_name: str) -> None:
        self.db.kv_set(self._dialogue_action_key(minister_name), "")

    def _dialogue_speaker_self(self, minister_name: str) -> str:
        try:
            character = self.session._character(minister_name)
            if is_eunuch_office(character.office, character.office_type):
                return "奴婢"
        except Exception:
            pass
        return "臣"

    def _dialogue_confirmed(self, text: str) -> bool:
        raw = str(text or "").strip()
        if re.search(r"(准|可|允|去办|办吧|照办|依你|依议|照你说|就这么办|去招|招募|就招|招一个|挑一个|取一个|带一个|领一个|找一个|荐一个|说合|调停|调和|准奏|请太医|治一治|调养|验宝|赐还|归还|发还|钳制|拿捏|封存|安抚|照料)", raw):
            return True
        if re.search(r"(?:^|[，,。；;！!\s、])(?:好|好的|行|成|准了)(?:[，,。；;！!\s、]|$)", raw):
            return True
        return bool(re.search(r"(?:先)?(?:把)?(?:人|他|她|此人|这人|那人|这位|那位|新人).{0,8}(?:带|领|引)(?:来|过来|入殿|进来|到御前|到朕前)", raw))

    def _dialogue_rejected(self, text: str) -> bool:
        raw = str(text or "").strip()
        return bool(re.search(r"(不必|暂缓|算了|不要|不可|先别|免了|罢了|否|驳回|改日)", raw))

    def _dialogue_regex_actions_enabled(self) -> bool:
        return os.environ.get("MING_SIM_ENABLE_DIALOGUE_REGEX_ACTIONS", "").strip().lower() in ("1", "true", "yes")

    def _dialogue_action_semantic_gate(
        self,
        minister_name: str,
        action: Dict[str, Any],
        user_text: str,
        phase: str,
        pending_action: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        action_type = str(action.get("type") or "").strip()
        if action_type == "recruitment":
            return self._recruitment_semantic_gate(minister_name, action, user_text, phase, pending_action)
        normalized = dict(action)
        normalized["phase"] = phase
        if action_type not in {"mediation", "castration", "eunuch_care", "eunuch_hard_service", "bao_leverage"}:
            return {"allow": False, "phase": "none", "action_type": "none", "confidence": 100, "private_reason": "动作类型无效。"}
        if phase == "confirm" and not (
            isinstance(pending_action, dict)
            and pending_action.get("type") == action_type
        ):
            return {"allow": False, "phase": "none", "action_type": "none", "confidence": 100, "private_reason": "没有待确认的同类对白动作。"}
        if os.environ.get("MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT", "").strip().lower() in ("1", "true", "yes"):
            return {"allow": False, "phase": "none", "action_type": "none", "confidence": 0, "private_reason": "对白动作语义审计已禁用。"}
        try:
            from ming_sim.dialogue_audit import dialogue_action_intent_audit

            character = self.session._character(minister_name)
            review = dialogue_action_intent_audit(
                self.db,
                self.state,
                character,
                user_text,
                normalized,
                pending_action=pending_action if isinstance(pending_action, dict) else None,
                llm_config=self.session.llm_config,
                agno_db=self.session.agno_db,
                audit_client=self.session.dialogue_audit_client,
            )
        except Exception as exc:
            review = {
                "allow": False,
                "phase": "none",
                "action_type": "none",
                "confidence": 0,
                "private_reason": str(exc),
            }
        if review.get("allow") and review.get("phase") != phase:
            review = dict(review)
            review["allow"] = False
            review["private_reason"] = (
                str(review.get("private_reason") or "").strip()
                or f"审计阶段不匹配：{review.get('phase')} != {phase}"
            )
        if review.get("allow") and review.get("action_type") not in {"", action_type}:
            review = dict(review)
            review["allow"] = False
            review["private_reason"] = (
                str(review.get("private_reason") or "").strip()
                or f"审计动作类型不匹配：{review.get('action_type')} != {action_type}"
            )
        return review

    def _castration_action_target_is_valid(self, action: Dict[str, Any]) -> bool:
        if action.get("type") != "castration":
            return True
        target = str(action.get("target") or "").strip()
        if not target:
            return False
        character = self.content.characters.get(target)
        if character is None:
            return False
        try:
            return self._castration_applicable(character)
        except Exception:
            return False

    def _detect_recruitment_intent(self, text: str) -> Dict[str, Any]:
        # Recruitment mutates the activity save by creating new NPCs, so normal
        # play should use the LLM tool + semantic audit path instead of keyword
        # interception.  This legacy fallback is opt-in for local diagnostics.
        if os.environ.get("MING_SIM_ENABLE_RECRUITMENT_REGEX_FALLBACK", "").strip().lower() not in ("1", "true", "yes"):
            return {}
        raw = str(text or "").strip()
        if not raw:
            return {}
        ask_people = bool(re.search(
            r"(?:有没有|可有|有无|还有没有|缺不缺).{0,18}"
            r"(?:新(?:的)?|可用|人手|人选|人才|贤才|苗子|太监|内侍|内臣|内官(?!监)|"
            r"小火者|小内侍|生徒|娃娃|年幼|年轻|小一点)",
            raw,
        ))
        recruit_order = bool(
            re.search(
                r"(?:找|寻|挑|招|募|取|荐|举).{0,18}"
                r"(?:人|才|新人|苗子|太监|内侍|内臣|内官(?!监)|小火者|小内侍|生徒|娃娃)",
                raw,
            )
            or re.search(r"(?:人手|人选|人才|贤才|苗子).{0,18}(?:找|寻|挑|招|募|取|荐|举)", raw)
        )
        eunuch_subject = bool(re.search(r"(太监|内侍|内臣|内官(?!监)|小火者|小内侍|内书堂|司礼监|净身房|新太监|新内侍)", raw))
        if eunuch_subject and (ask_people or recruit_order or re.search(r"(新太监|新内侍|小内侍|小太监)", raw)):
            return {"type": "recruitment", "kind": "eunuch"}
        if (ask_people or recruit_order) and re.search(r"(科举|科场|新科|进士|庶吉士|取士|选士)", raw):
            return {"type": "recruitment", "kind": "exam"}
        if re.search(r"(举荐|荐人|荐才|保举|寻贤|人才|贤才|可有人|谁可用|有谁可用)", raw):
            return {"type": "recruitment", "kind": "recommend"}
        return {}

    def _recent_minister_answer_texts(self, minister_name: str, limit: int = 6) -> List[str]:
        texts: List[str] = []
        for message in reversed(self.chat_history.get(minister_name, [])):
            if str(message.get("role") or "") != "minister":
                continue
            content = str(message.get("content") or "").strip()
            if content:
                texts.append(content)
            if len(texts) >= limit:
                return texts
        try:
            rows = self.db.conn.execute(
                "SELECT content FROM chat_messages WHERE minister_name=? AND role='minister' "
                "ORDER BY id DESC LIMIT ?",
                (minister_name, limit),
            ).fetchall()
        except Exception:
            rows = []
        for row in rows:
            content = str(row["content"] or "").strip()
            if content and content not in texts:
                texts.append(content)
        return texts[:limit]

    def _recover_pending_dialogue_action_from_recent_answer(self, minister_name: str, text: str) -> Dict[str, Any]:
        """Recover a two-step dialogue action when a legacy save or refresh lost its KV marker."""

        if not self._dialogue_confirmed(text):
            return {}
        for answer in self._recent_minister_answer_texts(minister_name):
            proposal_like = re.search(r"(陛下若准|若陛下准|若准|不敢擅专|不能当作无根之木)", answer)
            if not proposal_like:
                continue
            if re.search(r"(内书堂|司礼监|小火者|小内侍|内侍|太监|内官(?!监))", answer) and re.search(r"(挑|招|募|取|带|领).{0,18}(?:一个|一人|小火者|内侍|太监|来)", answer):
                return {"type": "recruitment", "kind": "eunuch", "recovered": True}
            if re.search(r"(新科|庶吉士|科场|进士|取士|补入朝班)", answer):
                return {"type": "recruitment", "kind": "exam", "recovered": True}
            if re.search(r"(举荐|荐人|荐才|保举|访贤|寻贤|荐出|举出).{0,24}(?:一人|一个|新人|可试差)", answer):
                return {"type": "recruitment", "kind": "recommend", "recovered": True}
        return {}

    def _recruitment_explicitly_blocked(self, text: str) -> bool:
        raw = str(text or "").strip()
        if not raw:
            return True
        return bool(re.search(
            r"(?:不是|并非|不要|不必|不用|暂不|先不|别|勿|毋).{0,18}(?:招|募|荐|举|保举|找新人|添人|荐新人|生人)"
            r"|(?:只是|只|不过|单是).{0,12}(?:问|聊|说|谈|议|听|看)"
            r"|不要荐新人|别再荐新人|先说现有人|不是要荐人|不是要招人",
            raw,
        ))

    def _recruitment_semantic_gate(
        self,
        minister_name: str,
        action: Dict[str, Any],
        user_text: str,
        phase: str,
        pending_action: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if action.get("type") != "recruitment":
            return {"allow": True, "kind": str(action.get("kind") or ""), "phase": phase, "confidence": 100}
        if self._recruitment_explicitly_blocked(user_text):
            return {
                "allow": False,
                "kind": "",
                "phase": "none",
                "confidence": 100,
                "private_reason": "玩家明示只是询问或不要招募/举荐新人。",
            }
        normalized = dict(action)
        normalized["phase"] = phase
        kind = str(normalized.get("kind") or (pending_action or {}).get("kind") or "").strip()
        if kind not in {"eunuch", "exam", "recommend"}:
            return {"allow": False, "kind": kind, "phase": "none", "confidence": 100, "private_reason": "用人类型无效。"}
        normalized["kind"] = kind
        if phase == "confirm" and not (
            isinstance(pending_action, dict)
            and pending_action.get("type") == "recruitment"
            and str(pending_action.get("kind") or "") in {"eunuch", "exam", "recommend"}
        ):
            return {"allow": False, "kind": kind, "phase": "none", "confidence": 100, "private_reason": "没有待确认的用人意图。"}
        try:
            from ming_sim.dialogue_audit import recruitment_intent_audit

            character = self.session._character(minister_name)
            review = recruitment_intent_audit(
                self.db,
                self.state,
                character,
                user_text,
                normalized,
                pending_action=pending_action if isinstance(pending_action, dict) else None,
                llm_config=self.session.llm_config,
                agno_db=self.session.agno_db,
                audit_client=self.session.dialogue_audit_client,
            )
        except Exception as exc:
            review = {
                "allow": False,
                "kind": kind,
                "phase": "none",
                "confidence": 0,
                "private_reason": str(exc),
            }
        if review.get("allow") and review.get("phase") != phase:
            review = dict(review)
            review["allow"] = False
            review["private_reason"] = (
                str(review.get("private_reason") or "").strip()
                or f"审计阶段不匹配：{review.get('phase')} != {phase}"
            )
        return review

    def _castration_topic_mentioned(self, text: str) -> bool:
        raw = str(text or "").strip()
        return bool(re.search(r"(净身|宫刑|腐刑|去势|阉割|阉了|阉掉|发净军|没入内廷|入宫为奴|入内廷为奴)", raw))

    def _castration_explicit_order(self, text: str) -> bool:
        raw = str(text or "").strip()
        if not raw or not self._castration_topic_mentioned(raw):
            return False
        if re.search(
            r"(?:不是|并非|不要|不必|不用|暂不|先不|别|毋|勿).{0,16}"
            r"(?:办|传|行事|净身|宫刑|腐刑|去势|阉|发净军|惊动净军房|入内廷为奴|入宫为奴)"
            r"|(?:只是|只|不过|单是).{0,12}(?:问|聊|说|谈|议|听|看)",
            raw,
        ):
            return False
        if "阉党" in raw and not re.search(r"(净身|宫刑|腐刑|去势|阉割|阉了|阉掉|发净军|没入内廷|入宫为奴|入内廷为奴)", raw):
            return False
        return bool(re.search(
            r"(?:把|将|令|命|着|使|让|准令|下旨|发旨|传旨|拿|押|发|送|没入|处以|施以|施|办了|照办)"
            r".{0,28}(?:净身|宫刑|腐刑|去势|阉割|阉了|阉掉|发净军|入内廷为奴|入宫为奴|没入内廷)"
            r"|(?:净身|宫刑|腐刑|去势|阉割|阉了|阉掉).{0,28}(?:入内廷|入宫为奴|为奴|发净军|行事|照办|办了)"
            r"|(?:发净军|没入内廷|入宫为奴|入内廷为奴)",
            raw,
        ))

    def _castration_targets_current_speaker(self, text: str) -> bool:
        raw = str(text or "").strip()
        if not self._castration_explicit_order(raw):
            return False
        return bool(re.search(
            r"(?:令卿|命卿|让卿|使卿|着卿|令你|命你|让你|使你|着你|把你|将你|卿|你)"
            r".{0,20}(?:净身|宫刑|腐刑|去势|阉|入内廷|入宫为奴)"
            r"|(?:净身|宫刑|腐刑|去势|阉).{0,20}(?:卿|你)",
            raw,
        ))

    def _castration_action_is_valid(
        self,
        minister_name: str,
        action: Dict[str, Any],
        user_text: str = "",
    ) -> bool:
        if action.get("type") != "castration":
            return True
        target = str(action.get("target") or "").strip()
        if not target:
            return False
        raw = str(user_text or action.get("scheme_text") or "").strip()
        if not self._castration_explicit_order(raw):
            return False
        mentions = self._character_mentions_in_text(raw) if raw else []
        if target == minister_name and target not in mentions and not self._castration_targets_current_speaker(raw):
            return False
        character = self.content.characters.get(target)
        if character is None:
            return False
        try:
            return self._castration_applicable(character)
        except Exception:
            return False

    def _detect_castration_intent(self, text: str, minister_name: str = "") -> Dict[str, Any]:
        raw = str(text or "").strip()
        if not self._castration_explicit_order(raw):
            return {}
        mentions = [name for name in self._character_mentions_in_text(raw) if name != minister_name]
        target = mentions[0] if mentions else ""
        if not target and minister_name in self.content.characters:
            try:
                character = self.session._character(minister_name)
                if self._castration_targets_current_speaker(raw) and self._castration_applicable(character):
                    target = minister_name
            except Exception:
                target = ""
        if not target:
            return {}
        character = self.content.characters.get(target)
        if character is None:
            return {}
        try:
            if not self._castration_applicable(character):
                return {}
        except Exception:
            return {}
        return {"type": "castration", "target": target, "scheme_text": raw, "force": True}

    def _detect_eunuch_care_intent(self, text: str, minister_name: str = "") -> Dict[str, Any]:
        raw = str(text or "").strip()
        if not raw:
            return {}
        if not re.search(r"(调养|医治|治一治|太医|药|热砖|汤药|照料|安抚|压惊|旧患|漏尿|尿闭|石淋|小解|幻肢|PTSD|噩梦|刀声|按肩|嗓音|体态|验宝|宝匣|查宝|官库|全尸|封签)", raw, flags=re.IGNORECASE):
            return {}
        mentions = [name for name in self._character_mentions_in_text(raw)]
        target = mentions[0] if mentions else ""
        if not target and minister_name in self.content.characters:
            try:
                if self._public_castration_payload(minister_name):
                    target = minister_name
            except Exception:
                target = ""
        if not target:
            return {}
        try:
            from ming_sim.eunuch_lore import get_lore, normalize_care_mode
            if get_lore(self.db, target) is None:
                return {}
            mode = normalize_care_mode("", raw)
        except Exception:
            return {}
        return {"type": "eunuch_care", "target": target, "mode": mode, "note": raw}

    def _detect_eunuch_hard_service_intent(self, text: str, minister_name: str = "") -> Dict[str, Any]:
        raw = str(text or "").strip()
        if not raw:
            return {}
        if not re.search(r"(不许|不准|不必|不用|暂不|先不|照常|照旧|强派|硬派|硬撑|带患|带病|仍派|仍让|不管|不理).{0,12}(调养|旧患|漏尿|尿闭|石淋|小解|幻肢|惊创|宝匣|差事|办差|派差|当差|硬查|硬办)", raw):
            return {}
        mentions = [name for name in self._character_mentions_in_text(raw)]
        target = ""
        try:
            from ming_sim.eunuch_lore import get_lore, normalize_care_mode
            for name in mentions:
                if get_lore(self.db, name) is not None:
                    target = name
                    break
            if not target and minister_name in self.content.characters and get_lore(self.db, minister_name) is not None:
                target = minister_name
            if not target and minister_name:
                goals = self.db.list_conversation_goals(
                    minister_name=minister_name,
                    statuses=["active", "waiting_conditions", "blocked"],
                    limit=1,
                )
                if goals and str(goals[0].get("action_kind") or "") == "eunuch_care":
                    target = minister_name
            if not target:
                return {}
            mode = normalize_care_mode("", raw)
        except Exception:
            return {}
        return {"type": "eunuch_hard_service", "target": target, "mode": mode, "note": raw}

    def _detect_bao_leverage_intent(self, text: str, minister_name: str = "") -> Dict[str, Any]:
        raw = str(text or "").strip()
        if not raw or not re.search(r"(宝|宝匣|宝案|全尸|封签|官库)", raw):
            return {}
        return_intent = bool(re.search(r"(赐还|归还|发还|交还|还给|自藏|自己收|钥匙给|还他全尸|还其全尸)", raw))
        control_intent = bool(re.search(r"(钳制|拿捏|把柄|官库封存|押在官库|不赐还|封签拿住|以宝制|收着.{0,8}宝)", raw))
        if not (return_intent or control_intent):
            return {}
        mentions = [name for name in self._character_mentions_in_text(raw)]
        target = ""
        try:
            from ming_sim.eunuch_lore import get_lore, normalize_bao_leverage_mode
            for name in mentions:
                if get_lore(self.db, name) is not None:
                    target = name
                    break
            if not target and minister_name in self.content.characters and get_lore(self.db, minister_name) is not None:
                target = minister_name
            if not target:
                return {}
            mode = normalize_bao_leverage_mode("control" if control_intent else "return", raw)
        except Exception:
            return {}
        return {"type": "bao_leverage", "target": target, "mode": mode, "note": raw}

    def _summon_handler_character(self, minister_name: str) -> Optional[Character]:
        """Return a character allowed to carry direct summon commands.

        The appointed attendant is the normal path, but a player may still be
        speaking to another eunuch-like courtier in the attendant hub. Those
        characters should be able to fetch someone instead of merely roleplaying
        compliance and leaving the UI on the wrong speaker.
        """
        clean = str(minister_name or "").strip()
        if not clean:
            return None
        try:
            from ming_sim.eunuch import get_attending_eunuch
            if clean == get_attending_eunuch(self.db):
                return self.session._character(clean)
        except Exception:
            pass
        try:
            character = self.session._character(clean)
        except Exception:
            return None
        try:
            office, office_type, _faction = self._current_office_identity(character)
        except Exception:
            office, office_type = character.office, character.office_type
        try:
            from ming_sim.eunuch import is_eunuch_like
            if is_eunuch_like(office, office_type):
                return character
        except Exception:
            pass
        if is_eunuch_office(office, office_type):
            return character
        return None

    def _is_surname_title_alias(self, alias: str, character: Character) -> bool:
        name = str(getattr(character, "name", "") or "")
        if not name or not alias.startswith(name[:1]) or len(alias) > 4:
            return False
        return any(alias.endswith(suffix) for suffix in self._CHAT_MENTION_SURNAME_TITLE_SUFFIXES)

    def _raw_character_aliases(self, character: Character) -> List[str]:
        raw_aliases = getattr(character, "aliases", []) or []
        if isinstance(raw_aliases, str):
            try:
                parsed = json.loads(raw_aliases)
            except (TypeError, ValueError):
                parsed = re.split(r"[，,、\s]+", raw_aliases)
            raw_aliases = parsed
        if not isinstance(raw_aliases, list):
            return []
        return [str(term or "").strip() for term in raw_aliases if str(term or "").strip()]

    def _is_blocked_chat_mention_term(self, term: str, character: Character) -> bool:
        clean = str(term or "").strip()
        if not clean:
            return True
        if clean in self._CHAT_MENTION_BLOCKED_ALIASES:
            return True
        title_only = (
            2 <= len(clean) <= 4
            and any(clean.endswith(suffix) for suffix in self._CHAT_MENTION_TITLE_ONLY_SUFFIXES)
            and not self._is_surname_title_alias(clean, character)
        )
        if title_only:
            return True
        has_org_token = any(token in clean for token in self._CHAT_MENTION_ORG_TOKENS)
        if has_org_token and not self._is_surname_title_alias(clean, character):
            return True
        has_org_shape = 2 <= len(clean) <= 8 and any(clean.endswith(suffix) for suffix in self._CHAT_MENTION_ORG_SUFFIXES)
        if has_org_shape and not self._is_surname_title_alias(clean, character):
            return True
        return False

    def _linkable_character_aliases(self, character: Character) -> List[str]:
        aliases: List[str] = []
        for clean in self._raw_character_aliases(character):
            if len(clean) < 2 or clean == getattr(character, "name", ""):
                continue
            if self._is_blocked_chat_mention_term(clean, character):
                continue
            if clean not in aliases:
                aliases.append(clean)
        return aliases

    def _character_mentions_in_text(self, text: str) -> List[str]:
        raw = str(text or "")
        names = []
        for name, character in self.content.characters.items():
            if getattr(character, "status", "active") != "active":
                continue
            if self._is_blocked_chat_mention_term(name, character):
                continue
            haystacks = [name, *self._linkable_character_aliases(character)]
            if any(alias and str(alias) in raw for alias in haystacks):
                names.append(name)
        names.sort(key=len, reverse=True)
        return list(dict.fromkeys(names))

    def _chat_message_mentions(self, text: str) -> List[Dict[str, Any]]:
        raw = str(text or "")
        mentions: List[Dict[str, Any]] = []
        for name in self._character_mentions_in_text(raw):
            character = self.content.characters.get(name)
            if character is None:
                continue
            terms = []
            for term in [name, *self._linkable_character_aliases(character)]:
                clean = str(term or "").strip()
                if self._is_blocked_chat_mention_term(clean, character):
                    continue
                if len(clean) >= 2 and clean in raw and clean not in terms:
                    terms.append(clean)
            if not terms:
                continue
            mentions.append({
                "kind": "character",
                "name": name,
                "terms": terms,
                "has_profile": True,
                "office": character.office,
            })
        return mentions

    def _chat_history_payload(self, minister_name: str) -> List[Dict[str, Any]]:
        payload: List[Dict[str, Any]] = []
        for message in self.chat_history.get(minister_name, []):
            item = dict(message)
            mentions = self._chat_message_mentions(str(item.get("content") or ""))
            if mentions:
                item["mentions"] = mentions
            if str(item.get("role") or "") != "user":
                existing_stage = (
                    [str(line).strip() for line in item.get("stage_directions", []) if str(line).strip()]
                    if isinstance(item.get("stage_directions"), list)
                    else []
                )
                stage_directions = self._chat_stage_directions(str(item.get("content") or ""))
                combined_stage = list(dict.fromkeys([*existing_stage, *stage_directions]))
                if combined_stage:
                    item["stage_directions"] = combined_stage[:4]
                    item["content"] = self._chat_display_text(str(item.get("content") or ""), combined_stage[:4])
            payload.append(item)
        return payload

    def _chat_display_text(self, text: str, stage_directions: Optional[List[str]] = None) -> str:
        """Remove extracted action/stage cues from the dialogue bubble text."""

        display = str(text or "")
        for direction in stage_directions or []:
            clean = str(direction or "").strip()
            if not clean:
                continue
            escaped = re.escape(clean)
            display = re.sub(fr"\s*（{escaped}）\s*", "\n", display)
            display = re.sub(fr"\s*（(?:动作|神态|举止|舞台|旁白)[:：]?\s*{escaped}）\s*", "\n", display)
            display = re.sub(fr"(?m)^\s*【(?:动作|神态|举止|舞台|旁白)[:：]?\s*{escaped}】\s*$", "", display)
            display = re.sub(fr"(?m)^\s*【(?:动作|神态|举止|舞台|旁白)】\s*{escaped}\s*$", "", display)
            display = re.sub(fr"(?m)^\s*[*_]{{1,2}}{escaped}[*_]{{1,2}}\s*$", "", display)
            display = re.sub(fr"(?m)^\s*[—-]+\s*{escaped}\s*$", "", display)
        lines = []
        stage_set = {str(line or "").strip() for line in stage_directions or [] if str(line or "").strip()}
        for line in display.splitlines():
            stripped = line.strip()
            candidate = re.sub(r"^【(?:动作|神态|举止|舞台|旁白)】\s*", "", stripped)
            candidate = re.sub(r"^\s*[*_]{1,2}(.+?)[*_]{1,2}\s*$", r"\1", candidate)
            if self._normalize_stage_direction(candidate) in stage_set:
                continue
            clean = stripped.lstrip("—-").strip()
            if re.search(r"^(?:传|宣|召|唤|叫)[^\n，。；：]{1,24}(?:觐见|入殿|进殿|进来|趋入|来见|面圣)", clean):
                continue
            if stripped:
                lines.append(stripped)
        return "\n".join(lines).strip()

    def _chat_stage_directions(self, text: str) -> List[str]:
        raw = str(text or "")
        if not raw:
            return []
        directions: List[str] = []
        for match in re.finditer(r"（([^（）]{2,64})）", raw):
            body = self._normalize_stage_direction(match.group(1))
            if body and self._looks_like_stage_direction(body):
                directions.append(body)
        for match in re.finditer(r"【(?:动作|神态|举止|舞台|旁白)\s*[:：]?\s*([^】]{2,72})】", raw):
            body = self._normalize_stage_direction(match.group(1))
            if body and self._looks_like_stage_direction(body):
                directions.append(body)
        for match in re.finditer(r"(?m)^\s*【(?:动作|神态|举止|舞台|旁白)】\s*([^\n]{2,72})\s*$", raw):
            body = self._normalize_stage_direction(match.group(1))
            if body and self._looks_like_stage_direction(body):
                directions.append(body)
        for match in re.finditer(r"(?m)^\s*(?:【(?:动作|神态|举止|舞台|旁白)】\s*)?[*_]{1,2}([^*_]{2,72})[*_]{1,2}\s*$", raw):
            body = self._normalize_stage_direction(match.group(1))
            if body and self._looks_like_stage_direction(body):
                directions.append(body)
        # 兼容常见“——传某人觐见”舞台句，不让它占满聊天气泡。
        for line in raw.splitlines():
            clean = line.strip().lstrip("—-").strip()
            if re.search(r"^(?:传|宣|召|唤|叫)[^\n，。；：]{1,24}(?:觐见|入殿|进殿|进来|趋入|来见|面圣)", clean):
                directions.append(clean)
        return list(dict.fromkeys(directions[:4]))

    def _normalize_stage_direction(self, text: str) -> str:
        body = re.sub(r"^\s*(?:动作|神态|举止|舞台|旁白)\s*[:：]\s*", "", str(text or "").strip())
        body = re.sub(r"\s+", " ", body)
        return body.strip(" ：:;；，,。")

    def _looks_like_stage_direction(self, text: str) -> bool:
        body = str(text or "").strip()
        if not (2 <= len(body) <= 72):
            return False
        if re.search(r"(曰|说道|回道|奏道|奴婢|臣以为|陛下|皇上).{0,8}[，。！？]", body):
            return False
        return bool(re.search(
            r"(躬身|叩首|跪|垂手|低声|压低|退到|侍立|夹肩|夹腰|缩步|摸|攥|颤|发抖|咳|低头|抬头|转身|唤|招|让出|侧身|趋前|汗|脸色|旧创|腰腹|宝匣|钥匙|袖中|袖口|肩背|失神|破声|尖声|伏地|俯首|叩谢|定神)",
            body,
        ))

    def _fallback_eunuch_stage_directions(self, minister_name: str, answer: str) -> List[str]:
        try:
            from ming_sim.eunuch_lore import eunuch_voice_profile
            profile = eunuch_voice_profile(self.db, minister_name) or {}
        except Exception:
            profile = {}
        cues = [
            str(item).strip()
            for item in (profile.get("stage_cues") if isinstance(profile, dict) else []) or []
            if str(item).strip()
        ]
        if not cues:
            return []
        seed = sum(ord(ch) for ch in f"{minister_name}:{answer[:24]}:{self.state.turn}")
        return [cues[seed % len(cues)]]

    def _dialogue_unknown_mentions_key(self) -> str:
        return "dialogue.unknown_person_mentions"

    def _load_unknown_dialogue_mentions(self) -> Dict[str, Dict[str, Any]]:
        raw = self.db.kv_get(self._dialogue_unknown_mentions_key()) or "{}"
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        out: Dict[str, Dict[str, Any]] = {}
        for name, value in data.items():
            clean = self._normalize_dialogue_person_name(str(name or ""))
            if (
                not clean
                or clean in self.content.characters
                or (clean.startswith(("蒙", "奉")) and clean[1:] in self.content.characters)
            ):
                continue
            out[clean] = value if isinstance(value, dict) else {}
        return out

    def _save_unknown_dialogue_mentions(self, mentions: Dict[str, Dict[str, Any]]) -> None:
        cleaned = {
            name: value
            for name, value in mentions.items()
            if self._normalize_dialogue_person_name(name)
            and name not in self.content.characters
            and not (str(name or "").startswith(("蒙", "奉")) and str(name or "")[1:] in self.content.characters)
        }
        self.db.kv_set(self._dialogue_unknown_mentions_key(), json.dumps(cleaned, ensure_ascii=False))

    def _normalize_dialogue_person_name(self, raw_name: str) -> str:
        name = re.sub(r"[^\u4e00-\u9fff]", "", str(raw_name or ""))
        for suffix in ("大人", "先生", "公公", "主事", "书办", "幕客", "内侍", "太监", "小火者", "举人", "秀才", "贡生", "吏员", "百户", "千户"):
            if name.endswith(suffix) and len(name) > len(suffix) + 1:
                name = name[: -len(suffix)]
                break
        if not (2 <= len(name) <= 4):
            return ""
        stopwords = {
            "陛下", "皇上", "皇帝", "圣上", "奴婢", "臣等", "臣下", "此人", "其人", "大人", "先生",
            "朝廷", "内阁", "司礼", "司礼监", "东林", "阉党", "内廷", "外朝", "厂卫", "锦衣卫",
            "东厂", "北镇抚司", "南镇抚司", "镇抚司", "内官监", "御马监", "内书堂", "文书房", "京营",
            "吏部", "户部", "礼部", "兵部", "刑部", "工部", "都察院", "翰林院", "詹事府", "大理寺",
            "奏对", "名册", "档案", "小火者", "太监", "内侍", "内臣", "新科", "科场", "科举",
        }
        descriptor_fragments = (
            "一个", "某个", "这个", "那个", "有个", "一位", "某位", "这位", "那位", "有人",
            "来见", "见面", "过来", "面圣", "面前", "御前", "入殿", "进殿", "奏对", "问对",
        )
        org_tokens = (
            "司礼", "锦衣", "镇抚司", "内阁", "东厂", "内官监", "御马监", "内书堂", "文书房",
            "都察院", "翰林院", "詹事府", "大理寺", "太常寺", "光禄寺", "南京",
        )
        org_suffixes = ("监", "部", "院", "寺", "厂", "卫", "司", "府", "衙", "局", "营", "镇", "房", "堂")
        if (
            name in stopwords
            or any(bad in name for bad in ("陛下", "皇上", "奴婢", "朝廷", "档案"))
            or any(fragment in name for fragment in descriptor_fragments)
            or any(token in name for token in org_tokens)
            or (2 <= len(name) <= 4 and any(name.endswith(suffix) for suffix in org_suffixes))
        ):
            return ""
        compound_surnames = ("司马", "欧阳", "上官", "诸葛", "夏侯", "皇甫", "尉迟", "公孙", "东方", "南宫")
        single_surnames = (
            "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜谢邹喻"
            "窦章云苏潘葛范彭鲁韦马苗方任袁柳鲍史唐薛雷贺倪汤滕殷罗毕郝安常傅"
            "卞齐康伍余顾孟黄穆萧尹姚邵汪祁毛米贝明计成戴谈宋庞纪舒屈项祝董梁"
            "杜阮蓝闵席季贾路江童颜郭梅盛林钟徐骆高夏蔡田胡凌霍虞万管卢莫房解"
            "应宗丁宣邓杭洪左石崔龚程邢裴陆荣翁荀羊甄封靳段焦巴侯全班秋仲宫宁"
            "仇甘祖武符刘詹龙叶黎白蒲从赖卓蔺蒙池乔闻党翟谭劳姬申冉宰雍桑桂牛"
            "寿尚温庄晏柴瞿阎充慕连茹习宦艾鱼向易慎廖庾衡步都耿满匡国文寇广东"
            "欧利越师巩聂晁勾冷辛阚简饶曾沙养丰关相查后游竺权益桓公督晋楚闫法涂钦"
        )
        if len(name) >= 3 and name[:2] in compound_surnames:
            return name
        if re.fullmatch(r"小[\u4e00-\u9fff]子", name):
            return name
        if name[0] in single_surnames:
            return name
        return ""

    def _extract_unknown_person_mentions(self, text: str, include_command: bool = False) -> List[str]:
        raw = str(text or "")
        scan = re.sub(r"[*_`]+", "", raw)
        names: List[str] = []

        def add(candidate: str) -> None:
            clean = self._normalize_dialogue_person_name(candidate)
            if not clean or clean in self.content.characters or clean in names:
                return
            if clean.startswith(("蒙", "奉")) and clean[1:] in self.content.characters:
                return
            names.append(clean)

        for surname, given in re.findall(r"姓([\u4e00-\u9fff]{1,2})名([\u4e00-\u9fff]{1,2})", scan):
            add(f"{surname}{given}")
        patterns = [
            r"(?:名叫|唤作|叫作|叫做|名为|名唤|叫)([\u4e00-\u9fff]{2,4}?)(?:的|，|、|。|；|：|$)",
            r"(?:一个是|另一个是|还有一个|头一个|头一个是|第二个|第二个是|第三个|第三个是)[，、\s]*(?:叫|名叫|唤作)?([\u4e00-\u9fff]{2,4})(?:的|，|、|。|；|：|今年|原|是|$)",
            r"([\u4e00-\u9fff]{2,4})(?:此人|其人|这个人|这人|大人|先生|公公|主事|书办|幕客|内侍|太监|小火者|举人|秀才|贡生|吏员|试百户|百户|千户|游击|把总|盐商|粮长|乡绅|儒生|胥吏|山人)",
            r"([\u4e00-\u9fff]{2,4})的(?:试百户|百户|千户|游击|把总|内侍|太监|小火者|火者|书办|幕客|胥吏|乡绅|儒生|盐商|粮长)",
        ]
        if include_command:
            patterns.extend([
                r"^([\u4e00-\u9fff]{2,4})[？?，,、\s]*(?:朕要|我要|想要|要)?(?:找|寻|召|传|宣|请|唤|叫|带|领|引|拉).{0,8}(?:见面|过来|面圣|面前|御前|来|到|入|见|觐|聊|谈|奏对|问对|进殿|入殿)",
                r"^([\u4e00-\u9fff]{2,4}?)[？?，,、\s]*(?:见面|过来|面圣|来|入|见|觐|聊|谈|奏对|问对|进殿|入殿)",
                r"(?:找|寻|召|传|宣|请|唤|叫|带|领|引|拉)([\u4e00-\u9fff]{2,4}?)(?:见面|过来|面圣|面前|御前|来|到|入|见|觐|聊|谈|奏对|问对|进殿|入殿)",
                r"(?:让|命|令)([\u4e00-\u9fff]{2,4}?)(?:见面|过来|面圣|来|入|见|觐|聊|谈|奏对|问对|进殿|入殿)",
                r"把([\u4e00-\u9fff]{2,4}?)(?:带|领|引|拉).{0,6}(?:见面|过来|面圣|面前|御前|朕前|来|到|入|见|觐|进殿|入殿)",
                r"(?:传|宣|召|唤|叫|带|领|引)[^\n，。；：]{0,16}?(小[\u4e00-\u9fff]子)(?:觐见|入殿|进殿|进来|趋入|来见|面圣|到御前|到朕前)",
                r"(?:传|宣|召|唤|叫|带|领|引)[^\n，。；：]{0,16}?([\u4e00-\u9fff]{2,4})(?:觐见|入殿|进殿|进来|趋入|来见|面圣|到御前|到朕前)",
                r"(?:^|[，,。；;！!\s、])(?:找|寻|召|传|宣|请|唤|叫)(?:了|一下|一声)?([\u4e00-\u9fff]{2,4})(?:吧|罢|[？?，,。！!\s、]|$)",
                r"(小[\u4e00-\u9fff]{1,2}子|[\u4e00-\u9fff]{2,4})(?:人呢|在哪|哪去了|怎么还不来|还没来|还不来|没来|不来|没出现|没人出现|没有人出现)",
                r"(?:叫|传|召|唤|找|请)(?:了|过)?[^\n，。；：]{0,14}?(小[\u4e00-\u9fff]{1,2}子|[\u4e00-\u9fff]{2,4})[^\n，。；：]{0,16}?(?:很久|半天|许久)[^\n，。；：]{0,16}?(?:没|不|未)[^\n，。；：]{0,8}?(?:来|到|进|见|出现)",
                r"^([\u4e00-\u9fff]{2,4})[，、\s]*(?:进来|入殿|进殿|趋入|觐见|来见|面圣)",
            ])
        for pattern in patterns:
            for candidate in re.findall(pattern, scan):
                add(str(candidate))
        return names

    def _extract_summoned_names_from_answer(self, answer: str) -> List[str]:
        scan = re.sub(r"[*_`]+", "", str(answer or ""))
        names: List[str] = []

        def add(candidate: str) -> None:
            clean = self._normalize_dialogue_person_name(candidate)
            if not clean or clean in names:
                return
            names.append(clean)

        patterns = [
            r"(?:传|宣|召|唤|叫|带|领|引)[^\n，。；：]{0,18}?(小[\u4e00-\u9fff]子)(?:觐见|入殿|进殿|进来|趋入|来见|面圣|到御前|到朕前)",
            r"(?:传|宣|召|唤|叫|带|领|引)[^\n，。；：]{0,18}?([\u4e00-\u9fff]{2,4})(?:觐见|入殿|进殿|进来|趋入|来见|面圣|到御前|到朕前)",
            r"(?m)^\s*(小[\u4e00-\u9fff]子)[，、\s]*(?:进来吧?|入殿吧?|进殿吧?|趋入|觐见|来见|面圣)",
            r"(?m)^\s*([\u4e00-\u9fff]{2,4})[，、\s]*(?:进来吧?|入殿吧?|进殿吧?|趋入|觐见|来见|面圣)",
            r"(?:^|[，,。；;！!\s])(小[\u4e00-\u9fff]子)(?:已|该|就|正)?在(?:殿外|门外|廊下|御前)[^，。；：\n]{0,18}(?:候着|候旨|候问|等着)",
            r"(?:^|[，,。；;！!\s])([\u4e00-\u9fff]{2,4}?)(?:已|该|就|正)?在(?:殿外|门外|廊下|御前)[^，。；：\n]{0,18}(?:候着|候旨|候问|等着)",
            r"(?:引着|带着)(?:一个|一名)?[^\n，。；：]{0,18}?([\u4e00-\u9fff]{2,4})(?:至|到|入|进)",
        ]
        for pattern in patterns:
            for candidate in re.findall(pattern, scan, flags=re.MULTILINE):
                add(str(candidate))
        return names

    def _recent_attendant_implied_summon_name(self, minister_name: str) -> str:
        messages = list(self.chat_history.get(minister_name, []))[-12:]
        if not messages:
            try:
                rows = self.db.conn.execute(
                    "SELECT role, content, stage_directions FROM chat_messages WHERE minister_name=? ORDER BY id DESC LIMIT 12",
                    (minister_name,),
                ).fetchall()
                messages = [
                    {
                        "role": str(row["role"] or ""),
                        "content": str(row["content"] or ""),
                        "stage_directions": self.db._chat_stage_list(row["stage_directions"] if "stage_directions" in row.keys() else "[]"),
                    }
                    for row in reversed(rows)
                ]
            except Exception:
                messages = []
        for message in reversed(messages):
            if str(message.get("role") or "") != "minister":
                continue
            combined = "\n".join([
                str(message.get("content") or ""),
                *[str(line) for line in (message.get("stage_directions") or []) if str(line).strip()],
            ])
            for name in self._extract_summoned_names_from_answer(combined):
                if name != minister_name:
                    return name
        return ""

    def _attendant_answer_summon_target(self, minister_name: str, answer: str) -> Dict[str, Any]:
        current = self._summon_handler_character(minister_name)
        if current is None:
            return {}
        names = [name for name in self._extract_summoned_names_from_answer(answer) if name != minister_name]
        if not names:
            return {}
        for name in names:
            if name not in self.content.characters:
                continue
            try:
                target, _is_temporary = self.session.summon_character(name, current, allow_temporary=False)
            except ValueError:
                continue
            ok, _reason = self.session.can_summon(target)
            if ok:
                self._clear_pending_dialogue_action(minister_name)
                return {"name": target.name, "generated": False}
        stored = self._load_unknown_dialogue_mentions()
        excerpt = re.sub(r"\s+", " ", str(answer or "")).strip()[:180]
        for name in names:
            if name in self.content.characters:
                continue
            stored.setdefault(name, {
                "name": name,
                "source_minister": minister_name,
                "first_seen_turn": int(self.state.turn),
                "mention_index": len(stored),
                "excerpt": excerpt,
            })
            self._save_unknown_dialogue_mentions(stored)
            generated = self._materialize_dialogue_mention_from_text(minister_name, f"传{name}入殿。{answer}")
            if not generated:
                continue
            try:
                target, _is_temporary = self.session.summon_character(generated.name, current, allow_temporary=False)
            except ValueError:
                continue
            ok, _reason = self.session.can_summon(target)
            if not ok:
                continue
            self._clear_pending_dialogue_action(minister_name)
            return {"name": target.name, "generated": True}
        return {}

    def _record_unknown_dialogue_mentions(self, minister_name: str, answer: str) -> None:
        names = self._extract_unknown_person_mentions(answer)
        for name in self._extract_summoned_names_from_answer(answer):
            if name not in names:
                names.append(name)
        if not names:
            return
        stored = self._load_unknown_dialogue_mentions()
        excerpt = re.sub(r"\s+", " ", str(answer or "")).strip()[:160]
        changed = False
        for name in names:
            if name in stored or name in self.content.characters:
                continue
            stored[name] = {
                "name": name,
                "source_minister": minister_name,
                "first_seen_turn": int(self.state.turn),
                "mention_index": len(stored),
                "excerpt": excerpt,
            }
            changed = True
        if changed:
            self._save_unknown_dialogue_mentions(stored)

    def _referenced_unknown_dialogue_name(
        self,
        minister_name: str,
        text: str,
        stored: Dict[str, Dict[str, Any]],
    ) -> str:
        raw = str(text or "")
        if not stored:
            return ""
        rows = [
            (idx, name, value)
            for idx, (name, value) in enumerate(stored.items())
            if str(value.get("source_minister") or "") == minister_name
        ]
        if not rows:
            rows = [(idx, name, value) for idx, (name, value) in enumerate(stored.items())]
        rows.sort(key=lambda item: (int(item[2].get("first_seen_turn") or 0), int(item[2].get("mention_index") or item[0])), reverse=False)
        if not rows:
            return ""
        if re.search(r"(头一个|第一个|第一位|头一位|前一个|前头那个|前面那个)", raw):
            return rows[0][1]
        if re.search(r"(第二个|第二位|另一个|后一个|后头那个|后面那个)", raw):
            return rows[1][1] if len(rows) >= 2 else rows[-1][1]
        if re.search(r"(第三个|第三位)", raw):
            return rows[2][1] if len(rows) >= 3 else ""
        if len(rows) == 1 and self._attendant_summon_followup_requested(raw):
            return rows[0][1]
        if len(rows) == 1 and re.search(r"(?:就|要|选|挑|取)(?:他|她|这人|此人|这个|这个人|这位)(?:吧|罢|[。！？!?\s]*$)", raw):
            return rows[0][1]
        if len(rows) == 1 and re.search(r"(他|此人|这人|那人|这个人|那个人|这位|那位|此辈).{0,12}(来|入|觐|见|聊|谈|奏对|问对|进殿|入殿)", raw):
            return rows[0][1]
        return ""

    def _materialize_dialogue_mention_from_text(self, minister_name: str, text: str) -> Optional[Character]:
        stored = self._load_unknown_dialogue_mentions()
        raw = str(text or "")
        candidates = [name for name in stored if name in raw]
        if not candidates:
            candidates = [name for name in self._extract_unknown_person_mentions(raw, include_command=True) if name in stored]
        if not candidates:
            referenced = self._referenced_unknown_dialogue_name(minister_name, raw, stored)
            candidates = [referenced] if referenced else []
            if not candidates:
                direct_names = self._extract_unknown_person_mentions(raw, include_command=True)
                candidates = [name for name in direct_names if name not in self.content.characters]
                if not candidates:
                    if self._attendant_summon_followup_requested(raw):
                        recent_name = self._recent_attendant_implied_summon_name(minister_name)
                        candidates = [recent_name] if recent_name and recent_name not in self.content.characters else []
                if not candidates:
                    return None
        candidates.sort(key=len, reverse=True)
        name = candidates[0]
        source = stored.get(name, {})
        if not source:
            source = {
                "name": name,
                "source_minister": minister_name,
                "first_seen_turn": int(self.state.turn),
                "excerpt": raw[:160],
            }
        context = f"{source.get('excerpt') or ''} {raw}"
        character = self._generate_dialogue_character(name, minister_name, context, source)
        added = self._add_runtime_character(character, "对白线索补档")
        self._record_recommendation_link(
            added.name,
            str(source.get("source_minister") or minister_name),
            "对白线索奉旨寻访",
            str(source.get("excerpt") or raw),
            verified_recommender=True,
        )
        stored.pop(name, None)
        self._save_unknown_dialogue_mentions(stored)
        self.db.record_log(self.state, f"奉旨按对白线索寻访{added.name}，补入本局人物名册，可召见奏对。")
        return added

    def _dialogue_age_from_context(self, context: str) -> int:
        raw = str(context or "")

        def from_chinese(value: str) -> int:
            text = str(value or "").strip().replace("两", "二").replace("〇", "零")
            if not text:
                return 0
            if text.isdigit():
                return int(text)
            digits = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
            if "十" in text:
                left, _, right = text.partition("十")
                tens = digits.get(left, 1) if left else 1
                ones = digits.get(right[:1], 0) if right else 0
                return tens * 10 + ones
            value = 0
            for ch in text:
                if ch not in digits:
                    return 0
                value = value * 10 + digits[ch]
            return value

        patterns = [
            r"(?:今年|年方|刚满|才|只|不过|约莫|约有|年纪)[^零〇一二两三四五六七八九十\d]{0,4}(\d{1,3})\s*(?:岁|龄|来岁|上下)?",
            r"(?:今年|年方|刚满|才|只|不过|约莫|约有|年纪)[^零〇一二两三四五六七八九十\d]{0,4}([零〇一二两三四五六七八九十]{1,4})\s*(?:岁|龄|来岁|上下)?",
            r"(\d{1,3})\s*(?:岁|龄)",
            r"([零〇一二两三四五六七八九十]{1,4})\s*(?:岁|龄)",
        ]
        for pattern in patterns:
            match = re.search(pattern, raw)
            if not match:
                continue
            age = from_chinese(match.group(1))
            if 1 <= age <= 90:
                return age
        return 0

    def _dialogue_birth_year(
        self,
        context: str,
        *,
        default_min_age: int,
        default_max_age: int,
    ) -> int:
        age = self._dialogue_age_from_context(context)
        if not age:
            age = self.character_rng.randint(int(default_min_age), int(default_max_age))
        return int(self.state.year) - int(age)

    def _generate_dialogue_character(
        self,
        name: str,
        minister_name: str,
        context: str,
        source: Dict[str, Any],
    ) -> Character:
        rng = self.character_rng
        raw = str(context or "")
        palace_nickname = bool(re.fullmatch(r"小[\u4e00-\u9fff]{1,2}子", str(name or "")))
        if palace_nickname or re.search(r"(太监|内侍|内臣|内官|小火者|内书堂|司礼监|宫里|宫中)", raw):
            office = rng.choice(["内书堂识字小火者", "司礼监文书房小内官", "乾清宫门下随侍", "净身房候验小火者"])
            office_type = "司礼监"
            faction = rng.choice(["内廷", "皇党", "阉党"])
            skills = ["宫禁熟习", "传旨跑腿", "察言观色", "文书抄录"]
            style = rng.choice([
                "新入御前，跪得快，回话先讲见闻，不敢妄议外朝大政",
                "机灵谨慎，耳朵尖，懂宫里门道，但见识仍绕着内廷打转",
                "识字守口，复命细碎，遇到大事会先请旨再动",
            ])
            summary_tail = "短板：见识多限宫禁，谈外朝容易露怯；风险：若被旧监房牵住，忠心会和内廷小圈子纠缠。"
            loyalty = rng.randint(78, 96)
            ability = rng.randint(42, 68)
            min_age, max_age = (
                (10, 16)
                if palace_nickname or re.search(r"(小火者|生徒|小内官|小内侍|刚满|年纪|今年)", raw)
                else (18, 55)
            )
        elif re.search(r"(百户|千户|游击|把总|武|军|营|边|辽东|兵)", raw):
            office = "待铨（武选访得）"
            office_type = "待铨"
            faction = rng.choice(["实务派", "中立", "边镇"])
            skills = ["军务见闻", "营伍调度", "边情", "执行"]
            style = rng.choice([
                "行礼粗硬，话少但敢担风险，常把军中实情说得刺耳",
                "边地气重，先看粮饷与人马，再谈名分章程",
                "不擅辞令，却记得住营伍细账和将校脾气",
            ])
            summary_tail = "短板：朝堂辞令生疏，容易被文臣压住；风险：边镇旧关系未明，荐用需看军中牵连。"
            loyalty = rng.randint(48, 76)
            ability = rng.randint(52, 78)
            min_age, max_age = (20, 55)
        else:
            office = "待铨（对白寻访）"
            office_type = "待铨"
            faction = rng.choice(["中立", "实务派", "清流", "乡党"])
            skills = rng.choice([
                ["地方见闻", "文书", "说合", "举贤"],
                ["钱粮核算", "案牍", "民情", "执行"],
                ["幕府阅历", "情报", "机变", "文书"],
                ["清查", "弹章", "地方阅历", "奏对"],
            ])
            style = rng.choice([
                "初入御前，既想抓住机会，又怕一句话把举主牵进去",
                "衣着朴素，回话带地方口气，看事不华丽但有棱角",
                "先报来路再讲本事，懂得把风险摊在明面上",
                "有幕客气，眼神活，习惯从夹缝里找可办之处",
            ])
            summary_tail = "短板：朝中根基浅，骤入御前容易被贴上举主标签；风险：来路未深查，可能牵出地方人情债。"
            loyalty = rng.randint(46, 76)
            ability = rng.randint(50, 78)
            min_age, max_age = (18, 60)
        birth_year = self._dialogue_birth_year(raw, default_min_age=min_age, default_max_age=max_age)
        age = max(0, int(self.state.year) - int(birth_year))
        source_minister = str(source.get("source_minister") or minister_name or "").strip()
        excerpt = str(source.get("excerpt") or "").strip()
        if palace_nickname:
            identity_note = f"内廷小名补档，约{age}岁，按内书堂/司礼监候用小火者管理；"
        elif is_eunuch_office(office, office_type):
            identity_note = f"内廷补档，约{age}岁，按宫中候用内侍管理；"
        else:
            identity_note = ""
        summary = (
            f"由{source_minister or '御前奏对'}对白中提及，后奉旨按线索寻访入京；"
            f"{identity_note}此人物为当前活动存档内即时补档，可召见、可任用、可进入关系网。"
            + (f"线索：{excerpt[:90]}。" if excerpt else "")
            + summary_tail
        )
        return Character(
            name=name,
            office=office,
            office_type=infer_office_type_from_office(office, office_type),
            faction=faction,
            aliases=[],
            personal_skills=list(dict.fromkeys(skills)),
            loyalty=loyalty,
            ability=ability,
            integrity=rng.randint(42, 84),
            courage=rng.randint(40, 80),
            style=style,
            birth_year=birth_year,
            power_id="ming",
            location=rng.choice(["京师", "南京", "山西", "陕西", "山东", "南直隶", "福建", "湖广"]),
            status="active",
            summary=summary[:800],
            force=rng.randint(34, 66),
            wisdom=rng.randint(46, 82),
            charm=rng.randint(42, 78),
            luck=rng.randint(36, 84),
        )

    def _detect_mediation_intent(self, minister_name: str, text: str) -> Dict[str, Any]:
        raw = str(text or "").strip()
        if not re.search(r"(调停|调和|和解|各退一步|共办|化解|缓和|息争|说合|消弭|担保|作保|连坐|人情)", raw):
            return {}
        mode = "guarantee" if re.search(r"(担保|作保|连坐|人情|护着|护他|护她|护其)", raw) else "co_work"
        mentions = [name for name in self._character_mentions_in_text(raw) if name != minister_name]
        if len(mentions) >= 2:
            return {"type": "mediation", "actor": mentions[0], "target": mentions[1], "mode": mode}
        if len(mentions) == 1:
            return {"type": "mediation", "actor": minister_name, "target": mentions[0], "mode": mode}
        try:
            from ming_sim import court
            rivals = court.rivals_of(self.db, minister_name, limit=1)
        except Exception:
            rivals = []
        if rivals:
            return {"type": "mediation", "actor": minister_name, "target": str(rivals[0]["name"])}
        faction_rows = self.db.conn.execute("SELECT name FROM factions ORDER BY heat DESC").fetchall()
        for row in faction_rows:
            faction = str(row["name"] or "")
            if faction and faction in raw:
                return {"type": "mediation", "faction": faction, "mode": mode}
        return {"type": "mediation", "actor": minister_name, "target": "", "mode": mode}

    def _castration_scheme_preview(self, scheme_text: str, *, forced: bool = True) -> Dict[str, Any]:
        try:
            from ming_sim.eunuch_lore import BAO_FORFEIT, BAO_KEPT, castration_scheme_profile
            lore = {
                "forced": bool(forced),
                "bao_status": BAO_FORFEIT if forced else BAO_KEPT,
                "note": f"御前方案 {scheme_text or ''}".strip(),
                "castration_method": "",
                "knife_tool": "",
                "anesthesia": "",
                "procedure_note": "",
                "bao_preservation": "",
                "bao_container": "",
                "bao_ritual": "",
                "aftereffect": "",
                "urinary_aftereffect": "",
                "voice_body_change": "",
                "trauma_response": "",
            }
            return castration_scheme_profile(lore)
        except Exception:
            return {}

    def _castration_scheme_summary(self, profile: Dict[str, Any]) -> str:
        if not isinstance(profile, dict) or not profile:
            return ""
        tier = str(profile.get("tier") or "").strip()
        risk = int(profile.get("risk_score") or 0)
        brutality = int(profile.get("brutality") or 0)
        trauma = int(profile.get("trauma_risk") or 0)
        surgery = int(profile.get("surgery_risk") or 0)
        bao = int(profile.get("bao_security") or 0)
        care = int(profile.get("care_cost_delta") or 0)
        care_text = f"，调养成本{care:+d}" if care else ""
        return f"方案画像：{tier or '未明'}，风险{risk}，酷烈{brutality}/惊创{trauma}/伤身{surgery}/宝案{bao}{care_text}"

    def _castration_scheme_effects(self, profile: Dict[str, Any]) -> List[Dict[str, str]]:
        if not isinstance(profile, dict) or not profile:
            return []
        tier = str(profile.get("tier") or "").strip()
        risk = int(profile.get("risk_score") or 0)
        care = int(profile.get("care_cost_delta") or 0)
        effects: List[Dict[str, str]] = [
            {"kind": "castration_scheme", "label": f"方案：{tier or '未明'} 风险{risk}", "tone": "bad" if risk >= 72 else "warn" if risk >= 55 else "neutral"},
            {"kind": "castration_scheme", "label": f"酷烈{int(profile.get('brutality') or 0)} 惊创{int(profile.get('trauma_risk') or 0)} 伤身{int(profile.get('surgery_risk') or 0)}", "tone": "warn"},
            {"kind": "castration_scheme", "label": f"宝案安全{int(profile.get('bao_security') or 0)}", "tone": "good" if int(profile.get("bao_security") or 0) >= 70 else "neutral"},
        ]
        if care:
            effects.append({"kind": "castration_scheme", "label": f"后续调养成本{care:+d}", "tone": "bad" if care > 0 else "good"})
        for text in (profile.get("effects") or [])[:3]:
            if str(text).strip():
                effects.append({"kind": "castration_scheme_rule", "label": str(text)[:32], "tone": "neutral"})
        return effects

    def _proposal_answer_for_action(self, minister_name: str, action: Dict[str, Any]) -> str:
        self_ref = self._dialogue_speaker_self(minister_name)
        kind = str(action.get("kind") or "")
        if action.get("type") == "recruitment":
            if kind == "eunuch":
                return f"{self_ref}回陛下，内书堂和司礼监下头确有几个识字小火者可挑。只是人一入御前，便牵动监房旧例，奴婢不敢擅专。陛下若准，奴婢便去挑一个忠谨可用的来。"
            if kind == "exam":
                return f"{self_ref}回陛下，新科与庶吉士中确有可试之人。若陛下准，臣可按科场名次与部院缺口挑一人先补入朝班，试其文书与奏对。"
            return f"{self_ref}回陛下，朝外与部院夹缝里确有几个人可荐，但荐人便有举主恩怨，不能当作无根之木。陛下若准，臣便举出一人，连同来源、短处与风险一并入档。"
        if action.get("type") == "mediation":
            actor = str(action.get("actor") or "")
            target = str(action.get("target") or "")
            faction = str(action.get("faction") or "")
            mode = str(action.get("mode") or "")
            if actor and target:
                if mode == "guarantee":
                    return f"{self_ref}回陛下，{actor}与{target}这层人情可用，却不可空口护短。若陛下准，臣便按御前之意定下担保边界，令其拿一件可验小差回奏。"
                return f"{self_ref}回陛下，{actor}与{target}的嫌隙不可一言抹平，但可先令二人各退一步、共担一桩小事。若陛下准，臣便按御前调停去说合，先把怨气压下一层。"
            if faction:
                return f"{self_ref}回陛下，{faction}气焰一时压不尽，但可先给台阶、收锋芒。若陛下准，臣便按御前调停处置，稍降党争热度。"
            return f"{self_ref}回陛下，此事需先点明双方，否则说合容易落空。陛下若要调停，请明示哪两人或哪一派。"
        if action.get("type") == "castration":
            target = str(action.get("target") or "")
            scheme = str(action.get("scheme_text") or "")
            profile = self._castration_scheme_preview(scheme, forced=True)
            profile_summary = self._castration_scheme_summary(profile)
            scheme_hint = "、".join(
                part for part in (
                    "净军房" if "净军" in scheme else "",
                    "铜柄宫刀" if "铜柄" in scheme else "",
                    "无麻" if "无麻" in scheme else "",
                    "油炸封蜡" if "油炸" in scheme else "",
                    "宝匣另封" if re.search(r"楠木|黄杨|锡胆|铁皮|灰瓮", scheme) else "",
                ) if part
            ) or "按内廷旧例拟一套净身、验宝、封匣章程"
            return (
                f"{self_ref}回陛下，若只是问旧例，{self_ref}只作旧例回话，不会惊动净军房。"
                f"{target}若真要净身入内廷，便是极端身份处置，外朝也会视作强旨重罚。"
                f"{self_ref}拟按「{scheme_hint}」办，宝况、刀具、麻醉与宝匣都会入档。"
                + (f"{profile_summary}。" if profile_summary else "")
                + "这不是单纯换官名，后头会影响漏尿尿闭、惊创、调养成本和差遣风险。"
                f"陛下若准，{self_ref}才敢传净军房行事；若还要改刀具、麻醉、宝匣材质或后患记档，也可现在明示。"
            )
        if action.get("type") == "eunuch_care":
            target = str(action.get("target") or "")
            mode = str(action.get("mode") or "general")
            label_map = {
                "urinary": "尿路调养",
                "trauma": "惊创抚慰",
                "body": "体声修整",
                "bao": "宝匣安置",
                "fixation": "心癖安顿",
                "general": "内廷调养",
            }
            label = label_map.get(mode, "内廷调养")
            return (
                f"{self_ref}回陛下，{target}这桩{label}可以办，但要动内库、太医或司礼监旧档，"
                "办了便会入记忆账：能压怨、增信，也可能留下旁人议论。"
                f"陛下若准，{self_ref}就按{label}去处置。"
            )
        if action.get("type") == "eunuch_hard_service":
            target = str(action.get("target") or "")
            mode = str(action.get("mode") or "general")
            label_map = {
                "urinary": "尿路旧患",
                "trauma": "惊创旧患",
                "body": "体声失仪",
                "bao": "宝匣心结",
                "fixation": "心癖旧结",
                "general": "净身旧患",
            }
            label = label_map.get(mode, "净身旧患")
            return (
                f"{self_ref}回陛下，若不调养{target}这桩{label}，照常派他办差，眼前能省内库、不断差期，"
                "但怨望和误事风险会入档，往后再派久候、封签、刑房或宝案差事更容易反噬。"
                f"陛下若仍准，{self_ref}就按硬派旧患记入司礼监档。"
            )
        if action.get("type") == "bao_leverage":
            target = str(action.get("target") or "")
            mode = str(action.get("mode") or "return")
            if mode == "control":
                return (
                    f"{self_ref}回陛下，{target}的宝案若押在官库作把柄，短期最能让他不敢违拗；"
                    "只是这等钳制会把怨望刻深，日后遇封签宝匣差事更易失神反噬。"
                    f"陛下若准，{self_ref}才敢封作官库钳制。"
                )
            return (
                f"{self_ref}回陛下，若把{target}的宝匣赐还本人，便是给他留全尸念想，能收心降怨；"
                "只是官库也少一枚拿捏他的把柄。"
                f"陛下若准，{self_ref}就重封宝匣、交钥匙给他。"
            )
        return f"{self_ref}领会陛下意思，但此事须陛下再明白准一句，臣才敢办。"

    def _execute_recruitment_action(self, minister_name: str, action: Dict[str, Any]) -> Dict[str, Any]:
        kind = str(action.get("kind") or "")
        if kind == "eunuch":
            result = self.recruit_eunuch(recommender=minister_name)
            title = "内廷得人"
            detail = f"{result['minister']['name']} 已补入{result['minister'].get('office') or '内廷'}"
        elif kind == "exam":
            result = self.recruit_exam_official(recommender=minister_name)
            title = "新科得人"
            detail = f"{result['minister']['name']} 已补入{result['minister'].get('office') or '朝班'}"
        else:
            result = self.recommend_hidden_official(recommender=minister_name)
            title = "举贤得人"
            recommender = result.get("recommender") or minister_name
            detail = f"{recommender} 举荐 {result['minister']['name']} 入朝听用"
        minister = result.get("minister") or {}
        name = str(minister.get("name") or "")
        self._clear_pending_dialogue_action(minister_name)
        court_action = ""
        next_minister = ""
        summon_note = ""
        current = self._summon_handler_character(minister_name)
        if name and current is not None:
            try:
                target, _is_temporary = self.session.summon_character(name, current, allow_temporary=False)
                ok, _reason = self.session.can_summon(target)
            except Exception:
                ok = False
                target = None
            if ok and target is not None:
                court_action = "summon"
                next_minister = target.name
                summon_note = f" {target.name}已在殿外候旨，奴婢这就引入御前，供陛下当面试问。"
        answer = (
            f"{self._dialogue_speaker_self(minister_name)}遵旨，已办妥。"
            f"{result.get('message') or detail} 新人小传已记明来源、短板与举荐风险。"
            f"{summon_note or '陛下可召见验看。'}"
        )
        return {
            "answer": answer,
            "recruited_minister": name,
            "court_action": court_action,
            "next_minister": next_minister,
            "dialogue_effect": {
                "title": title,
                "message": detail,
                "stage_direction": f"{self._dialogue_speaker_self(minister_name)}趋至殿门，传{name or '新人'}入殿候问。" if court_action else "",
                "effects": [{"kind": "recruitment", "label": "新人可召见", "tone": "good"}],
            },
        }

    def _execute_castration_action(self, minister_name: str, action: Dict[str, Any]) -> Dict[str, Any]:
        self_ref = self._dialogue_speaker_self(minister_name)
        target = str(action.get("target") or "").strip()
        scheme_text = str(action.get("scheme_text") or "").strip()
        if not target:
            self._clear_pending_dialogue_action(minister_name)
            return {"answer": f"{self_ref}回陛下，未点明要净身之人，{self_ref}不敢虚办。"}
        try:
            result = self.castrate_official(target, force=True, scheme_text=scheme_text)
        except HTTPException as exc:
            self._clear_pending_dialogue_action(minister_name)
            detail = str(getattr(exc, "detail", "") or "净身未成。")
            return {"answer": f"{self_ref}回陛下，此事办不得：{detail}"}

        minister = result.get("minister") or {}
        castration = minister.get("castration") if isinstance(minister, dict) else {}
        labels: List[str] = []
        if isinstance(castration, dict):
            for key in (
                "method_label",
                "knife_label",
                "anesthesia_label",
                "preservation_label",
                "container_label",
                "urine_label",
                "trauma_label",
                "fixation_label",
                "psychosexual_label",
            ):
                value = str(castration.get(key) or "").strip()
                if value and value not in labels:
                    labels.append(value)
        detail = "、".join(labels[:6]) or "旧档已封存"
        scheme_profile = castration.get("scheme_profile") if isinstance(castration, dict) else {}
        scheme_summary = self._castration_scheme_summary(scheme_profile if isinstance(scheme_profile, dict) else {})
        scheme_effects = self._castration_scheme_effects(scheme_profile if isinstance(scheme_profile, dict) else {})
        self._clear_pending_dialogue_action(minister_name)
        return {
            "answer": (
                f"{self_ref}遵旨。{target}已改入内廷，内廷旧档按御前方案封存：{detail}。"
                + (f"{scheme_summary}。" if scheme_summary else "")
                + "旧匣、后患与心相已入档；后续旧疾、调养成本与差遣风险都会按此方案走。"
            ),
            "dialogue_effect": {
                "title": "内廷改籍",
                "message": f"{target}入内廷：{detail}" + (f"；{scheme_summary}" if scheme_summary else ""),
                "effects": [
                    {"kind": "castration", "label": "内廷旧档已封存", "tone": "bad"},
                    {"kind": "character", "label": f"{target} 可召见", "tone": "warn"},
                    *scheme_effects[:6],
                ],
            },
        }

    def _execute_eunuch_care_action(self, minister_name: str, action: Dict[str, Any]) -> Dict[str, Any]:
        self_ref = self._dialogue_speaker_self(minister_name)
        target = str(action.get("target") or "").strip()
        mode = str(action.get("mode") or "general").strip()
        note = str(action.get("note") or "").strip()
        try:
            from ming_sim.eunuch_lore import apply_eunuch_care
            result = apply_eunuch_care(
                self.db,
                self.state,
                target,
                mode=mode,
                note=note,
                source="dialogue",
            )
        except Exception as exc:
            result = {"ok": False, "reason": str(exc)}
        self._clear_pending_dialogue_action(minister_name)
        if not result.get("ok"):
            return {"answer": f"{self_ref}回陛下，此事暂办不得：{result.get('reason') or '旧患调养未成'}。"}
        label = str(result.get("label") or "内廷调养")
        outcome = str(result.get("outcome") or "")
        stage = str(result.get("stage_direction") or "")
        message = f"{target}{label}：{outcome}"
        lore_update = result.get("lore_update") if isinstance(result.get("lore_update"), dict) else {}
        label_map = {
            "bao_preservation": "宝存",
            "bao_container": "宝匣",
            "bao_ritual": "仪式",
            "bao_texture": "宝况",
            "bao_weight": "宝重",
            "bao_shape": "宝形",
        }
        lore_effects = [
            {
                "kind": "eunuch_lore",
                "label": f"{label_map.get(str(key), str(key))}：{str(value)[:18]}",
                "tone": "info",
            }
            for key, value in lore_update.items()
            if str(value).strip()
        ]
        item_effects = [
            {"kind": "inventory", "label": f"入库：{str(item)[:22]}", "tone": "good"}
            for item in (result.get("items_added") or [])
            if str(item).strip()
        ]
        return {
            "answer": (
                f"{self_ref}遵旨。{target}这桩{label}已入司礼监旧档，{outcome}。"
                "旧患未必根除，但他会记得陛下问过这处隐痛。"
            ),
            "dialogue_effect": {
                "title": label,
                "message": message,
                "effects": [
                    {"kind": "eunuch_care", "label": outcome or label, "tone": "good"},
                    {"kind": "character", "label": f"{target}旧患入档", "tone": "info"},
                    *lore_effects[:6],
                    *item_effects[:2],
                ],
                "stage_direction": stage,
            },
        }

    def _execute_eunuch_hard_service_action(self, minister_name: str, action: Dict[str, Any]) -> Dict[str, Any]:
        self_ref = self._dialogue_speaker_self(minister_name)
        target = str(action.get("target") or "").strip()
        mode = str(action.get("mode") or "general").strip()
        note = str(action.get("note") or "").strip()
        try:
            from ming_sim.eunuch_lore import apply_eunuch_hard_service
            result = apply_eunuch_hard_service(
                self.db,
                self.state,
                target,
                mode=mode,
                note=note,
                source="dialogue",
            )
        except Exception as exc:
            result = {"ok": False, "reason": str(exc)}
        self._clear_pending_dialogue_action(minister_name)
        if not result.get("ok"):
            return {"answer": f"{self_ref}回陛下，此事暂办不得：{result.get('reason') or '旧患硬派未成'}。"}
        label = str(result.get("label") or "旧患硬派")
        outcome = str(result.get("outcome") or "")
        stage = str(result.get("stage_direction") or "")
        message = f"{target}{label}：{outcome}"
        return {
            "answer": (
                f"{self_ref}遵旨。{target}这桩{label}已按不调养、照常派差入档，{outcome}。"
                "这能省一时差期，却会把旧患和怨气压到后头的差事里。"
            ),
            "dialogue_effect": {
                "title": label,
                "message": message,
                "effects": [
                    {"kind": "eunuch_hard_service", "label": outcome or label, "tone": "bad"},
                    {"kind": "character_trait", "label": f"新增特质：{str(result.get('trait') or '旧患硬派')}", "tone": "warn"},
                    {"kind": "character", "label": f"{target}硬派入档", "tone": "bad"},
                ],
                "stage_direction": stage,
            },
        }

    def _execute_bao_leverage_action(self, minister_name: str, action: Dict[str, Any]) -> Dict[str, Any]:
        self_ref = self._dialogue_speaker_self(minister_name)
        target = str(action.get("target") or "").strip()
        mode = str(action.get("mode") or "return").strip()
        note = str(action.get("note") or "").strip()
        try:
            from ming_sim.eunuch_lore import apply_bao_leverage
            result = apply_bao_leverage(
                self.db,
                self.state,
                target,
                mode=mode,
                note=note,
                source="dialogue",
            )
        except Exception as exc:
            result = {"ok": False, "reason": str(exc)}
        self._clear_pending_dialogue_action(minister_name)
        if not result.get("ok"):
            return {"answer": f"{self_ref}回陛下，此事暂办不得：{result.get('reason') or '宝案未成'}。"}
        label = str(result.get("label") or "宝匣筹码")
        outcome = str(result.get("outcome") or "")
        stage = str(result.get("stage_direction") or "")
        lore_update = result.get("lore_update") if isinstance(result.get("lore_update"), dict) else {}
        stake = result.get("stake_profile") if isinstance(result.get("stake_profile"), dict) else {}
        label_map = {
            "bao_status": "宝案",
            "bao_preservation": "宝存",
            "bao_container": "宝匣",
            "bao_ritual": "仪式",
        }
        lore_effects = [
            {
                "kind": "eunuch_lore",
                "label": f"{label_map.get(str(key), str(key))}：{str(value)[:18]}",
                "tone": "info",
            }
            for key, value in lore_update.items()
            if str(value).strip()
        ]
        item_effects = [
            {"kind": "inventory", "label": f"入库：{str(item)[:22]}", "tone": "good"}
            for item in (result.get("items_added") or [])
            if str(item).strip()
        ]
        stake_effects: List[Dict[str, str]] = []
        if stake:
            score = int(stake.get("score") or 0)
            summary = str(stake.get("summary") or "宝案细节").strip()
            tradeoff = str(stake.get("tradeoff") or "").strip()
            stake_effects.append({
                "kind": "bao_stake",
                "label": f"宝案筹码{score}：{summary[:18]}",
                "tone": "good" if score >= 65 and str(result.get("mode") or "") == "return" else "warn",
            })
            if tradeoff:
                stake_effects.append({"kind": "bao_tradeoff", "label": tradeoff[:28], "tone": "neutral"})
        if str(result.get("mode") or "") == "control":
            answer = (
                f"{self_ref}遵旨。{target}的宝案已封作官库把柄，{outcome}。"
                "这能压他一时，却不是无价之物；往后封签宝匣差事须防怨气反扑。"
            )
            tone = "bad"
        else:
            answer = (
                f"{self_ref}遵旨。{target}的宝匣已重封赐还，{outcome}。"
                "他未必立刻成忠仆，但这笔全尸念想会记在心里。"
            )
            tone = "good"
        return {
            "answer": answer,
            "dialogue_effect": {
                "title": label,
                "message": f"{target}{label}：{outcome}",
                "effects": [
                    {"kind": "bao_leverage", "label": outcome or label, "tone": tone},
                    {"kind": "character", "label": f"{target}宝案入档", "tone": "info"},
                    *stake_effects[:2],
                    *lore_effects[:5],
                    *item_effects[:2],
                ],
                "stage_direction": stage,
            },
        }

    def _relationship_commitment_owner(self, minister_name: str, actor: str, target: str) -> tuple[str, str]:
        if minister_name in {actor, target}:
            owner = minister_name
            other = target if owner == actor else actor
            return owner, other
        return minister_name, f"{actor}与{target}".strip("与")

    def _open_relationship_commitment(self, owner: str, title: str) -> Dict[str, Any]:
        for goal in self.db.list_conversation_goals(
            minister_name=owner,
            statuses=["active", "waiting_conditions", "sealed"],
            limit=12,
        ):
            if str(goal.get("title") or "") == title:
                return goal
        return {}

    def _create_relationship_commitment(
        self,
        minister_name: str,
        actor: str,
        target: str,
        mode: str,
    ) -> Dict[str, Any]:
        if not (actor and target):
            return {}
        owner, other = self._relationship_commitment_owner(minister_name, actor, target)
        if not owner or owner not in self.content.characters:
            return {}
        mode = "guarantee" if mode == "guarantee" else "co_work"
        owner_is_party = owner in {actor, target}
        if mode == "guarantee":
            title = f"人情担保：{owner}护{other}" if owner_is_party else f"担保说合：{actor}护{target}"
            target_text = (
                f"{owner}须替{other}说清担保边界，并共办一件可验小差。"
                if owner_is_party
                else f"{owner}须促成{actor}替{target}说清担保边界，并定下一件可验小差。"
            )
            tasks = [
                f"交代{target if not owner_is_party else other}可用之处、短板与担保边界",
                (
                    f"与{other}共办一件可验小差并回奏证据"
                    if owner_is_party
                    else f"促成{actor}与{target}共办一件可验小差并回奏证据"
                ),
            ]
            stakes = "人情担保、党援坐大、连坐风险"
            promise_type = "人情担保承诺"
        else:
            title = f"共办消怨：{actor}与{target}"
            target_text = f"{owner}须推动{actor}与{target}共办一件可验小差，回奏分工、证据与旧怨是否仍相牵。"
            tasks = [
                f"推动{actor}与{target}共办一件可验小差",
                "回奏分工、证据与是否仍借私怨拖延",
            ]
            stakes = "政敌牵制、共办翻脸、党争降温"
            promise_type = "共办消怨承诺"
        existing = self._open_relationship_commitment(owner, title)
        if existing:
            return {
                "owner": owner,
                "title": title,
                "goal_id": int(existing.get("id") or 0),
                "agreement_id": int(existing.get("agreement_id") or 0),
                "created": False,
            }
        threshold = 68
        conditions = [{"description": task, "status": "pending"} for task in tasks]
        goal_id = self.db.create_conversation_goal(
            self.state,
            minister_name=owner,
            action_kind="court_commitment",
            title=title,
            target_text=target_text,
            threshold=threshold,
            score=100,
            status="waiting_conditions",
            condition_status="pending",
            conditions=conditions,
            expires_turn=int(self.state.turn) + 3,
            last_delta={
                "source": "dialogue_relationship_commitment",
                "mode": mode,
                "actor": actor,
                "target": target,
                "owner": owner,
            },
        )
        agreement_id = self.db.create_negotiation_agreement(
            self.state,
            minister_name=owner,
            topic=title,
            action_kind="court_commitment",
            status="pending",
            stance_id=0,
            handshake_status="sealed",
            psychological_score=100,
            threshold=threshold,
            verbal_only=False,
            core_topic=title,
            target_text=target_text,
            promise_type=promise_type,
            stakes=stakes,
            due_turn=int(self.state.turn) + 2,
            conditions="；".join(tasks),
            summary=f"{owner}领下御前{title}，须限期回奏。",
            tasks=tasks,
            goal_id=goal_id,
        )
        self.db.bind_conversation_goal_agreement(goal_id, agreement_id)
        self.db.add_conversation_goal_event(
            self.state,
            goal_id,
            "agreement_created",
            status="waiting_conditions",
            score_delta=0,
            score_after=100,
            summary=f"已进入履约账本 #{agreement_id}",
            payload={"agreement_id": agreement_id, "mode": mode, "actor": actor, "target": target},
        )
        return {
            "owner": owner,
            "title": title,
            "goal_id": goal_id,
            "agreement_id": agreement_id,
            "created": True,
        }

    def _execute_mediation_action(self, minister_name: str, action: Dict[str, Any]) -> Dict[str, Any]:
        self_ref = self._dialogue_speaker_self(minister_name)
        actor = str(action.get("actor") or "").strip()
        target = str(action.get("target") or "").strip()
        faction = str(action.get("faction") or "").strip()
        mode = str(action.get("mode") or "co_work").strip()
        effects: List[Dict[str, str]] = []
        if actor and target and actor in self.content.characters and target in self.content.characters:
            try:
                from ming_sim import court
                from ming_sim.theater import adjust_faction_heat, faction_of
                day = max(0, int(self.state.turn) * 30)
                before = court.get_opinion(self.db, actor, target)
                after = court.adjust_opinion(self.db, actor, target, +18, "御前调停", day=day, reciprocal=True)
                effects.append({"kind": "relationship", "label": f"{actor}·{target} {before}→{after}", "tone": "good"})
                for person in (actor, target):
                    fa = faction_of(self.db, person)
                    if fa:
                        adjust_faction_heat(self.db, fa, -5, "御前调停")
                        effects.append({"kind": "faction_heat", "label": f"{fa}热度 -5", "tone": "good"})
            except Exception:
                pass
            commitment = self._create_relationship_commitment(minister_name, actor, target, mode)
            dialogue_goal = None
            if commitment:
                effects.append({
                    "kind": "conversation_goal",
                    "label": f"履约账本：{commitment['owner']}",
                    "tone": "warn",
                })
                try:
                    dialogue_goal = self.db.get_conversation_goal(int(commitment.get("goal_id") or 0))
                except Exception:
                    dialogue_goal = None
            self._clear_pending_dialogue_action(minister_name)
            if mode == "guarantee":
                answer = (
                    f"{self_ref}遵旨。{actor}与{target}这层人情已压成御前担保，"
                    "不许空口护短；后头要以小差、证据和边界回奏。若坏事，便按担保追问。"
                )
                message = f"{actor}替{target}担保入账"
            else:
                answer = f"{self_ref}遵旨。{actor}与{target}旧怨未必尽消，但臣已按陛下意思递了台阶，先令二人收锋、可共事一段。若后头再相攻，便要看差事成败与陛下奖惩了。"
                message = f"{actor}与{target}怨气稍缓"
            return {
                "answer": answer,
                "dialogue_effect": {
                    "title": "人情担保" if mode == "guarantee" else "御前调停",
                    "message": message,
                    "effects": effects,
                },
                "dialogue_goal": dialogue_goal,
            }
        if faction:
            try:
                from ming_sim.theater import adjust_faction_heat
                adjust_faction_heat(self.db, faction, -8, "御前调停")
                self.db.adjust_factions({faction: {"satisfaction": +2, "leverage": -1}})
                effects.append({"kind": "faction_heat", "label": f"{faction}热度 -8", "tone": "good"})
            except Exception:
                pass
            self._clear_pending_dialogue_action(minister_name)
            return {
                "answer": f"{self_ref}遵旨。{faction}这股气势已先压下一层，但只是暂缓，不是旧怨尽解；后续还要靠差事、赏罚和人事安排慢慢消磨。",
                "dialogue_effect": {
                    "title": "党争稍缓",
                    "message": f"{faction}热度下降",
                    "effects": effects,
                },
            }
        self._clear_pending_dialogue_action(minister_name)
        return {"answer": f"{self_ref}回陛下，此事缺少双方名目，臣不敢虚报已调停。请陛下点明人名或派系，再容臣去说合。"}

    def _execute_dialogue_action(self, minister_name: str, action: Dict[str, Any]) -> Dict[str, Any]:
        if action.get("type") == "recruitment":
            return self._execute_recruitment_action(minister_name, action)
        if action.get("type") == "castration":
            return self._execute_castration_action(minister_name, action)
        if action.get("type") == "mediation":
            return self._execute_mediation_action(minister_name, action)
        if action.get("type") == "eunuch_care":
            return self._execute_eunuch_care_action(minister_name, action)
        if action.get("type") == "eunuch_hard_service":
            return self._execute_eunuch_hard_service_action(minister_name, action)
        if action.get("type") == "bao_leverage":
            return self._execute_bao_leverage_action(minister_name, action)
        return {"answer": self._proposal_answer_for_action(minister_name, action)}

    def _dialogue_action_response(self, minister_name: str, text: str) -> Optional[Dict[str, Any]]:
        pending = self._load_pending_dialogue_action(minister_name)
        if pending:
            if self._dialogue_rejected(text):
                self._clear_pending_dialogue_action(minister_name)
                return {"answer": f"{self._dialogue_speaker_self(minister_name)}明白。此事暂且按下，不入档、不用人，也不惊动外朝。"}
            if self._dialogue_confirmed(text):
                if pending.get("type") == "recruitment":
                    review = self._recruitment_semantic_gate(
                        minister_name,
                        {
                            "type": "recruitment",
                            "phase": "confirm",
                            "kind": pending.get("kind"),
                            "note": text,
                        },
                        text,
                        "confirm",
                        pending,
                    )
                    if not review.get("allow"):
                        return None
                if pending.get("type") == "castration":
                    pending = dict(pending)
                    extra = str(text or "").strip()
                    if extra:
                        pending["scheme_text"] = " ".join(
                            part
                            for part in (str(pending.get("scheme_text") or "").strip(), extra)
                            if part
                        )
                elif pending.get("type") == "eunuch_care":
                    pending = dict(pending)
                    extra = str(text or "").strip()
                    if extra:
                        pending["note"] = " ".join(
                            part
                            for part in (str(pending.get("note") or "").strip(), extra)
                            if part
                        )
                elif pending.get("type") == "eunuch_hard_service":
                    pending = dict(pending)
                    extra = str(text or "").strip()
                    if extra:
                        pending["note"] = " ".join(
                            part
                            for part in (str(pending.get("note") or "").strip(), extra)
                            if part
                        )
                elif pending.get("type") == "bao_leverage":
                    pending = dict(pending)
                    extra = str(text or "").strip()
                    if extra:
                        pending["note"] = " ".join(
                            part
                            for part in (str(pending.get("note") or "").strip(), extra)
                            if part
                        )
                return self._execute_dialogue_action(minister_name, pending)
        if not self._dialogue_regex_actions_enabled():
            return None
        recovered = self._recover_pending_dialogue_action_from_recent_answer(minister_name, text)
        if recovered:
            return self._execute_dialogue_action(minister_name, recovered)
        action = (
            self._detect_castration_intent(text, minister_name)
            or self._detect_bao_leverage_intent(text, minister_name)
            or self._detect_eunuch_hard_service_intent(text, minister_name)
            or self._detect_eunuch_care_intent(text, minister_name)
            or self._detect_mediation_intent(minister_name, text)
        )
        if not action:
            return None
        if action.get("type") == "mediation" and not (action.get("target") or action.get("faction")):
            return {"answer": self._proposal_answer_for_action(minister_name, action)}
        self._store_pending_dialogue_action(minister_name, action)
        return {"answer": self._proposal_answer_for_action(minister_name, action)}

    def _dialogue_tool_response(
        self,
        minister_name: str,
        action: Optional[Dict[str, Any]],
        fallback_answer: str,
        user_text: str = "",
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(action, dict) or not action:
            return None
        phase = str(action.get("phase") or "propose")
        normalized = dict(action)
        normalized.pop("phase", None)
        if phase == "confirm":
            pending = self._load_pending_dialogue_action(minister_name)
            action_type = str(normalized.get("type") or "")
            if action_type in {"recruitment", "mediation", "castration", "eunuch_care", "eunuch_hard_service", "bao_leverage"} and not (
                pending and pending.get("type") == action_type
            ):
                return None
            if pending and pending.get("type") == normalized.get("type"):
                if normalized.get("type") == "recruitment" and not normalized.get("kind"):
                    normalized["kind"] = pending.get("kind")
                if normalized.get("type") == "castration":
                    if not normalized.get("target"):
                        normalized["target"] = pending.get("target")
                    normalized["force"] = True
                    normalized["scheme_text"] = " ".join(
                        part
                        for part in (
                            str(pending.get("scheme_text") or "").strip(),
                            str(normalized.get("scheme_text") or "").strip(),
                        )
                        if part
                    )
                if normalized.get("type") == "mediation":
                    for key in ("actor", "target", "faction"):
                        if not normalized.get(key):
                            normalized[key] = pending.get(key)
                if normalized.get("type") == "eunuch_care":
                    for key in ("target", "mode", "note"):
                        if not normalized.get(key):
                            normalized[key] = pending.get(key)
                    note_parts = [
                        str(pending.get("note") or "").strip(),
                        str(normalized.get("note") or "").strip(),
                    ]
                    normalized["note"] = " ".join(dict.fromkeys(part for part in note_parts if part))
                if normalized.get("type") == "eunuch_hard_service":
                    for key in ("target", "mode", "note"):
                        if not normalized.get(key):
                            normalized[key] = pending.get(key)
                    note_parts = [
                        str(pending.get("note") or "").strip(),
                        str(normalized.get("note") or "").strip(),
                    ]
                    normalized["note"] = " ".join(dict.fromkeys(part for part in note_parts if part))
                if normalized.get("type") == "bao_leverage":
                    for key in ("target", "mode", "note"):
                        if not normalized.get(key):
                            normalized[key] = pending.get(key)
                    note_parts = [
                        str(pending.get("note") or "").strip(),
                        str(normalized.get("note") or "").strip(),
                    ]
                    normalized["note"] = " ".join(dict.fromkeys(part for part in note_parts if part))
            if normalized.get("type") == "recruitment" and not normalized.get("kind"):
                return None
            if normalized.get("type") == "recruitment":
                if not (pending and pending.get("type") == "recruitment"):
                    return None
                review = self._recruitment_semantic_gate(minister_name, normalized, user_text, "confirm", pending)
                if not review.get("allow"):
                    return None
                if review.get("kind"):
                    normalized["kind"] = review.get("kind")
                if review.get("trigger_quote"):
                    normalized["trigger_quote"] = review.get("trigger_quote")
                if review.get("private_reason"):
                    normalized["semantic_reason"] = review.get("private_reason")
            elif normalized.get("type") in {"mediation", "castration", "eunuch_care", "eunuch_hard_service", "bao_leverage"}:
                review = self._dialogue_action_semantic_gate(minister_name, normalized, user_text, "confirm", pending)
                if not review.get("allow"):
                    return None
                for key in ("target", "actor", "faction", "kind", "mode"):
                    if review.get(key):
                        normalized[key] = review.get(key)
                if review.get("trigger_quote"):
                    normalized["trigger_quote"] = review.get("trigger_quote")
                if review.get("private_reason"):
                    normalized["semantic_reason"] = review.get("private_reason")
            if normalized.get("type") == "castration" and not self._castration_action_target_is_valid(normalized):
                return None
            return self._execute_dialogue_action(minister_name, normalized)
        if normalized.get("type") in {"recruitment", "mediation", "castration", "eunuch_care", "eunuch_hard_service", "bao_leverage"}:
            if normalized.get("type") == "recruitment":
                review = self._recruitment_semantic_gate(minister_name, normalized, user_text, "propose")
                if not review.get("allow"):
                    return None
                if review.get("kind"):
                    normalized["kind"] = review.get("kind")
                if review.get("trigger_quote"):
                    normalized["trigger_quote"] = review.get("trigger_quote")
                if review.get("private_reason"):
                    normalized["semantic_reason"] = review.get("private_reason")
            else:
                review = self._dialogue_action_semantic_gate(minister_name, normalized, user_text, "propose")
                if not review.get("allow"):
                    return None
                for key in ("target", "actor", "faction", "kind", "mode"):
                    if review.get(key):
                        normalized[key] = review.get(key)
                if review.get("trigger_quote"):
                    normalized["trigger_quote"] = review.get("trigger_quote")
                if review.get("private_reason"):
                    normalized["semantic_reason"] = review.get("private_reason")
            if normalized.get("type") == "castration":
                normalized["force"] = True
                if not str(normalized.get("scheme_text") or "").strip():
                    normalized["scheme_text"] = str(user_text or "").strip()
                if not self._castration_action_target_is_valid(normalized):
                    return None
            self._store_pending_dialogue_action(minister_name, normalized)
            return {"answer": fallback_answer or self._proposal_answer_for_action(minister_name, normalized)}
        return None

    def undo_last_chat(self, minister_name: str) -> Dict[str, Any]:
        if self.state.turn_phase not in ("summoning", "reviewing"):
            raise HTTPException(status_code=409, detail="本回合已经进入颁诏结算，不能撤回召对。")
        if not self._persistent_chat_minister(minister_name):
            raise HTTPException(status_code=409, detail="临时召见人物暂不支持撤回。")
        row = self.db.get_last_active_chat_turn(minister_name, self.state.turn)
        if row is None:
            raise HTTPException(status_code=404, detail="本回合没有可撤回的召对。")
        if not self.db.is_global_last_active_chat_turn(int(row["id"])):
            raise HTTPException(status_code=409, detail="只能撤回全局最后一轮召对。")
        if not row.get("user_message_id") or not row.get("minister_message_id"):
            raise HTTPException(status_code=409, detail="该召对尚未完整完成，不能撤回。")
        try:
            undone = self.db.undo_chat_turn(int(row["id"]))
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        self.session.refresh_runtime_after_chat_rollback()
        self._restore_chat_history_cache()
        character = self.session._character(minister_name)
        return {
            "minister": minister_name,
            "minister_profile": self.public_character(character),
            "undone_chat_turn_id": int(undone["id"]),
            "history": self._chat_history_payload(minister_name),
            "history_limit": _web_chat_history_limit(),
            "history_truncated": len(self.chat_history.get(minister_name, [])) >= _web_chat_history_limit(),
            "directives": [self.directive_payload(row) for row in self.directive_rows()],
            "pending_count": self.session.pending_count(),
            "secret_orders": self.db.list_secret_orders(),
            "suggestions": self.suggestions_for(character),
            "can_undo_last_chat": self.can_undo_last_chat(minister_name),
            "state": self.state_payload(),
        }

    def _chat_payload(
        self,
        minister_name: str,
        answer: str,
        court_action: str = "",
        next_minister: str = "",
        proposed_directive: Optional[Dict[str, Any]] = None,
        appointed_minister: str = "",
        registered_minister: str = "",
        recruited_minister: str = "",
        displaced_minister: str = "",
        displaced_effect: Optional[Dict[str, Any]] = None,
        secret_order_id: int = 0,
        secret_order_assignee: str = "",
        secret_order_effect: Optional[Dict[str, Any]] = None,
        directive_effect: Optional[Dict[str, Any]] = None,
        dialogue_effect: Optional[Dict[str, Any]] = None,
        chat_turn_id: int = 0,
        dialogue_goal: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        character = self.session._character(minister_name)
        if not court_action:
            implied_summon = self._attendant_answer_summon_target(minister_name, answer)
            if implied_summon:
                next_minister = str(implied_summon.get("name") or "")
                if next_minister:
                    court_action = "summon"
                    if implied_summon.get("generated"):
                        registered_minister = registered_minister or next_minister
        self._record_unknown_dialogue_mentions(minister_name, answer)
        stage_directions: List[str] = []
        if isinstance(dialogue_effect, dict):
            stage = str(dialogue_effect.get("stage_direction") or "").strip()
            if stage:
                stage_directions.append(stage)
        for stage in self._chat_stage_directions(answer):
            if stage and stage not in stage_directions:
                stage_directions.append(stage)
        if not stage_directions:
            stage_directions.extend(self._fallback_eunuch_stage_directions(minister_name, answer))
        display_answer = self._chat_display_text(answer, stage_directions[:4]) if stage_directions else str(answer or "").strip()
        minister_message: Dict[str, Any] = {"role": "minister", "content": display_answer}
        if stage_directions:
            minister_message["stage_directions"] = stage_directions[:4]
        self.chat_history[minister_name].append(minister_message)
        if minister_name not in self.session.temporary_characters:
            message_id = self.db.append_chat_message(
                minister_name,
                self.state.turn,
                "minister",
                display_answer,
                stage_directions=stage_directions[:4],
            )
            if chat_turn_id:
                self.db.update_chat_turn_messages(chat_turn_id, minister_message_id=message_id)
        self._prune_chat_history(minister_name)
        return {
            "minister": minister_name,
            "minister_profile": self.public_character(character),
            "answer": answer,
            "history": self._chat_history_payload(minister_name),
            "history_limit": _web_chat_history_limit(),
            "history_truncated": len(self.chat_history[minister_name]) >= _web_chat_history_limit(),
            "court_action": court_action,
            "next_minister": next_minister,
            "proposed_directive": proposed_directive,
            "appointed_minister": appointed_minister,
            "registered_minister": registered_minister,
            "recruited_minister": recruited_minister,
            "displaced_minister": displaced_minister,
            "displaced_effect": displaced_effect or {},
            "secret_order_id": secret_order_id or 0,
            "secret_order_assignee": secret_order_assignee,
            "secret_order_effect": secret_order_effect or {},
            "directive_effect": directive_effect or {},
            "dialogue_effect": dialogue_effect or {},
            "dialogue_goal": (
                self._conversation_goal_payload_from_rows([dialogue_goal])[0]
                if dialogue_goal else {}
            ),
            "directives": [self.directive_payload(row) for row in self.directive_rows()],
            "pending_count": self.session.pending_count(),
            "suggestions": self.suggestions_for(character),
            "can_undo_last_chat": self.can_undo_last_chat(minister_name),
        }

    def _auto_chat_context_brief(self, minister_name: str) -> str:
        """Best available live-state context for a plain direct summon.

        Home-screen cards pass an explicit context and remain authoritative. This
        fallback keeps ordinary person-list summons from becoming stateless small
        talk when the DB already knows the NPC has an obligation, agenda, plea,
        or unpaid favor.
        """

        try:
            from ming_sim.playstyle import (
                agenda_chat_context_brief,
                favor_chat_context_brief,
                monthly_followup_chat_context_brief,
                petition_chat_context_brief,
            )
        except Exception:
            return ""
        providers = (
            lambda: monthly_followup_chat_context_brief(self.db, minister_name),
            lambda: agenda_chat_context_brief(self.db, minister_name),
            lambda: petition_chat_context_brief(self.db, minister_name),
            lambda: favor_chat_context_brief(self.db, minister_name),
        )
        for provider in providers:
            try:
                brief = str(provider() or "").strip()
            except Exception:
                brief = ""
            if brief:
                return brief
        return ""

    def _chat_context_brief(self, minister_name: str, context: Optional[Dict[str, Any]]) -> str:
        if not isinstance(context, dict):
            return self._auto_chat_context_brief(minister_name)
        if not any(str(context.get(key) or "").strip() for key in ("kind", "ref_kind", "ref_id", "id", "actor", "target")):
            return self._auto_chat_context_brief(minister_name)
        kind = str(context.get("kind") or "").strip()
        ref_kind = str(context.get("ref_kind") or "").strip()
        if kind == "decision" or ref_kind == "decision":
            ref_id = context.get("ref_id") or context.get("id")
            try:
                from ming_sim.playstyle import decision_chat_context_brief
                return decision_chat_context_brief(
                    self.db,
                    minister_name,
                    ref_id,
                    target=str(context.get("target") or ""),
                )
            except Exception:
                return ""
        if kind == "directive" or ref_kind == "directive":
            ref_id = context.get("ref_id") or context.get("id")
            try:
                from ming_sim.lifecycle import directive_chat_context_brief
                return directive_chat_context_brief(self.db, minister_name, ref_id)
            except Exception:
                return ""
        if kind == "doctrine" or ref_kind == "doctrine":
            actor = str(context.get("actor") or "").strip()
            if actor and actor != minister_name:
                return ""
            ref_id = context.get("ref_id") or context.get("id")
            try:
                from ming_sim.playstyle import doctrine_chat_context_brief
                return doctrine_chat_context_brief(self.db, minister_name, ref_id)
            except Exception:
                return ""
        if kind == "petition":
            actor = str(context.get("actor") or context.get("ref_id") or context.get("id") or "").strip()
            if actor and actor != minister_name:
                return ""
            try:
                from ming_sim.playstyle import petition_chat_context_brief
                return petition_chat_context_brief(
                    self.db,
                    minister_name,
                    target=str(context.get("target") or ""),
                )
            except Exception:
                return ""
        if kind == "legacy" or ref_kind == "legacy":
            actor = str(context.get("actor") or "").strip()
            target = str(context.get("target") or "").strip()
            allowed = {name for name in (actor, target) if name}
            if allowed and minister_name not in allowed:
                return ""
            ref_id = context.get("ref_id") or context.get("id")
            try:
                from ming_sim.playstyle import legacy_chat_context_brief
                return legacy_chat_context_brief(self.db, minister_name, ref_id)
            except Exception:
                return ""
        if kind == "agenda":
            actor = str(context.get("actor") or context.get("ref_id") or context.get("id") or "").strip()
            if actor and actor != minister_name:
                return ""
            try:
                from ming_sim.playstyle import agenda_chat_context_brief
                return agenda_chat_context_brief(
                    self.db,
                    minister_name,
                    target=str(context.get("target") or ""),
                )
            except Exception:
                return ""
        if kind == "rivalry":
            actor = str(context.get("actor") or context.get("ref_id") or context.get("id") or "").strip()
            target = str(context.get("target") or "").strip()
            if minister_name not in {actor, target}:
                return ""
            other = target if minister_name == actor else actor
            try:
                from ming_sim.playstyle import rivalry_chat_context_brief
                return rivalry_chat_context_brief(
                    self.db,
                    minister_name,
                    target=other,
                )
            except Exception:
                return ""
        if kind == "faction" or ref_kind == "faction":
            actor = str(context.get("actor") or "").strip()
            if actor and actor != minister_name:
                return ""
            fac = str(context.get("ref_id") or context.get("target") or context.get("id") or "").strip()
            try:
                from ming_sim.playstyle import faction_chat_context_brief
                return faction_chat_context_brief(
                    self.db,
                    minister_name,
                    faction=fac,
                )
            except Exception:
                return ""
        if kind == "army" or ref_kind == "army":
            actor = str(context.get("actor") or "").strip()
            if actor and actor != minister_name:
                return ""
            ref_id = context.get("ref_id") or context.get("id")
            try:
                from ming_sim.playstyle import army_chat_context_brief
                return army_chat_context_brief(self.db, minister_name, ref_id)
            except Exception:
                return ""
        if kind == "favor":
            actor = str(context.get("actor") or "").strip()
            if actor and actor != minister_name:
                return ""
            ref_id = context.get("ref_id") or context.get("id")
            try:
                from ming_sim.playstyle import favor_chat_context_brief
                return favor_chat_context_brief(self.db, minister_name, ref_id)
            except Exception:
                return ""
        if kind == "bargain":
            actor = str(context.get("actor") or "").strip()
            if actor and actor != minister_name:
                return ""
            ref_id = context.get("ref_id") or context.get("id")
            try:
                from ming_sim.playstyle import bargain_chat_context_brief
                return bargain_chat_context_brief(self.db, minister_name, ref_id)
            except Exception:
                return ""
        if kind == "monthly_followup" or ref_kind == "monthly_followup":
            actor = str(context.get("actor") or context.get("ref_id") or context.get("id") or "").strip()
            if actor and actor != minister_name:
                return ""
            try:
                from ming_sim.playstyle import monthly_followup_chat_context_brief
                return monthly_followup_chat_context_brief(self.db, minister_name)
            except Exception:
                return ""
        if kind == "patronage":
            actor = str(context.get("actor") or "").strip()
            target = str(context.get("target") or "").strip()
            if minister_name not in {actor, target}:
                return ""
            other = target if minister_name == actor else actor
            try:
                from ming_sim.playstyle import patronage_chat_context_brief
                return patronage_chat_context_brief(self.db, minister_name, target=other)
            except Exception:
                return ""
        if kind == "relationship" or ref_kind == "relationship":
            actor = str(context.get("actor") or "").strip()
            target = str(context.get("target") or "").strip()
            if minister_name not in {actor, target}:
                return ""
            other = target if minister_name == actor else actor
            try:
                from ming_sim.playstyle import relationship_chat_context_brief
                return relationship_chat_context_brief(self.db, minister_name, target=other)
            except Exception:
                return ""
        return ""

    def _directive_chat_effect(
        self,
        minister_name: str,
        context: Optional[Dict[str, Any]],
        user_text: str,
        answer: str,
    ) -> Dict[str, Any]:
        if not isinstance(context, dict):
            return {}
        kind = str(context.get("kind") or "").strip()
        ref_kind = str(context.get("ref_kind") or "").strip()
        if kind != "directive" and ref_kind != "directive":
            return {}
        ref_id = context.get("ref_id") or context.get("id")
        try:
            from ming_sim.lifecycle import apply_directive_audience_pressure
            return apply_directive_audience_pressure(self.db, self.state, minister_name, ref_id, user_text, answer)
        except Exception:
            return {}

    def _decision_chat_effect(
        self,
        minister_name: str,
        context: Optional[Dict[str, Any]],
        user_text: str,
        answer: str,
    ) -> Dict[str, Any]:
        if not isinstance(context, dict):
            return {}
        kind = str(context.get("kind") or "").strip()
        ref_kind = str(context.get("ref_kind") or "").strip()
        if kind != "decision" and ref_kind != "decision":
            return {}
        ref_id = context.get("ref_id") or context.get("id")
        try:
            from ming_sim.playstyle import record_decision_testimony
            return record_decision_testimony(
                self.db,
                self.state,
                minister_name,
                ref_id,
                user_text,
                answer,
                target=str(context.get("target") or ""),
            )
        except Exception:
            return {}

    def _combine_dialogue_effects(self, *effects: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        cleaned: List[Dict[str, Any]] = []
        for effect in effects:
            if not isinstance(effect, dict) or not effect:
                continue
            items = effect.get("effects")
            normalized_items = items if isinstance(items, list) else []
            title = str(effect.get("title") or "").strip()
            message = str(effect.get("message") or "").strip()
            if not title and not message and not normalized_items:
                continue
            clone = dict(effect)
            clone["effects"] = normalized_items
            cleaned.append(clone)
        if not cleaned:
            return {}
        if len(cleaned) == 1:
            return cleaned[0]
        messages: List[str] = []
        stage_directions: List[str] = []
        merged_items: List[Dict[str, Any]] = []
        for effect in cleaned:
            message = str(effect.get("message") or effect.get("title") or "").strip()
            if message and message not in messages:
                messages.append(message)
            stage = str(effect.get("stage_direction") or "").strip()
            if stage and stage not in stage_directions:
                stage_directions.append(stage)
            for item in effect.get("effects") or []:
                if isinstance(item, dict):
                    merged_items.append(item)
        primary_title = next(
            (str(effect.get("title") or "").strip() for effect in cleaned if str(effect.get("title") or "").strip()),
            "奏对有动",
        )
        merged: Dict[str, Any] = {
            "title": primary_title,
            "message": "；".join(messages)[:120],
            "effects": merged_items[:10],
        }
        if stage_directions:
            merged["stage_direction"] = "；".join(stage_directions[:3])
        return merged

    def _bargain_attitude(self, user_text: str) -> str:
        raw = re.sub(r"\s+", "", str(user_text or ""))
        if not raw:
            return ""
        refuse_terms = (
            "不准", "不可", "不允", "不许", "驳回", "休想", "不能给", "不答应",
            "暂不", "先不", "不护", "不保", "免谈", "驳了",
        )
        if any(term in raw for term in refuse_terms):
            return "refuse"
        press_terms = (
            "先交", "拿出", "自证", "证据", "账册", "担保", "限期", "几日",
            "谁担责", "交账", "画押", "共办", "试差", "条件", "验明",
        )
        if any(term in raw for term in press_terms):
            return "press"
        accept_terms = (
            "朕准", "准了", "准你", "准其", "可以", "允了", "依你", "照办",
            "答应", "就这么办", "给你", "护持", "展限", "拨给", "许你",
        )
        if any(term in raw for term in accept_terms):
            return "accept"
        if re.search(r"(^|[，。；、,;])准([，。；、,;]|$)", str(user_text or "")):
            return "accept"
        return ""

    def _bargain_context_applies(self, minister_name: str, context: Optional[Dict[str, Any]]) -> bool:
        if not isinstance(context, dict):
            return False
        kind = str(context.get("kind") or "").strip()
        ref_kind = str(context.get("ref_kind") or "").strip()
        allowed = {
            "petition", "agenda", "favor", "monthly_followup", "legacy",
            "rivalry", "faction", "army", "patronage", "relationship",
            "bargain",
        }
        if kind not in allowed and ref_kind not in allowed:
            return False
        actor = str(context.get("actor") or "").strip()
        target = str(context.get("target") or "").strip()
        if kind in {"rivalry", "patronage", "relationship", "legacy"} or ref_kind in {"relationship", "legacy"}:
            allowed_names = {name for name in (actor, target) if name}
            return not allowed_names or minister_name in allowed_names
        return not actor or actor == minister_name

    def _open_bargain_commitment(self, minister_name: str, title: str) -> Dict[str, Any]:
        for goal in self.db.list_conversation_goals(
            minister_name=minister_name,
            statuses=["active", "waiting_conditions", "sealed", "blocked"],
            limit=16,
        ):
            if str(goal.get("action_kind") or "") == "audience_bargain" and str(goal.get("title") or "") == title:
                return goal
        return {}

    def _short_commitment_title(self, value: str, limit: int = 18) -> str:
        clean = re.sub(r"\s+", "", str(value or "")).strip("「」")
        if not clean:
            return "本次奏对"
        return clean[:limit] + ("…" if len(clean) > limit else "")

    def _commitment_context_kind(self, context: Optional[Dict[str, Any]]) -> str:
        if not isinstance(context, dict):
            return "general"
        return str(context.get("kind") or context.get("ref_kind") or "general").strip() or "general"

    def _generic_bargain_commitment_terms(
        self,
        minister_name: str,
        context: Dict[str, Any],
        attitude: str,
        context_title: str,
    ) -> Dict[str, Any]:
        ask = str(context.get("ask") or context.get("motive") or "").strip()
        exchange = str(context.get("exchange") or context.get("gain") or "").strip()
        cost = str(context.get("cost") or "").strip()
        refusal = str(context.get("refusal") or "").strip()
        short_title = self._short_commitment_title(context_title)
        context_kind = self._commitment_context_kind(context)
        if attitude == "press":
            title = f"御前索证：{minister_name}·{short_title}"
            target_text = f"{minister_name}须围绕「{context_title}」补交证据、账册、担保或期限，再由御前决定是否给名分、资源或差使。"
            tasks = [
                f"补交「{context_title}」相关账册、人证或担保",
                f"说明「{exchange or '可验交换'}」如何兑现、几日回奏、谁担责",
            ]
            if cost:
                tasks.append(f"交代皇帝若背书须承担的代价：{cost}")
            promise_type = "御前索证"
            message = "索证入账"
            stakes_bits = ["证据压力", "担保连坐", context_kind]
        else:
            title = f"御前许诺：{minister_name}·{short_title}"
            target_text = f"{minister_name}领下「{context_title}」相关御前许诺，须把所求转成可验差使、成效或人事回报，避免口头恩典变空账。"
            tasks = [
                f"把「{context_title}」落实为可验差使、成效或回报",
                f"围绕「{exchange or '可得收益'}」限期回奏实际结果",
            ]
            if ask:
                tasks.append(f"所求「{ask}」不得外溢为新的请托")
            if cost:
                tasks.append(f"回奏代价处置：{cost}")
            promise_type = "御前许诺"
            message = "许诺入账"
            stakes_bits = ["私恩成债", "履约压力", context_kind]
        if refusal:
            stakes_bits.append(f"拒之：{refusal}")
        return {
            "title": title,
            "target_text": target_text,
            "tasks": tasks[:4],
            "promise_type": promise_type,
            "stakes": "、".join(bit for bit in stakes_bits if bit)[:120],
            "message": message,
        }

    def _create_bargain_commitment(
        self,
        minister_name: str,
        context: Optional[Dict[str, Any]],
        attitude: str,
        user_text: str,
        chat_turn_id: int = 0,
    ) -> Dict[str, Any]:
        if not isinstance(context, dict):
            return {}
        if attitude not in {"accept", "press"}:
            return {}
        context_title = str(context.get("title") or context.get("meta") or "御前旧账").strip()
        memory_id = str(context.get("ref_id") or context.get("id") or "").strip()
        context_kind = self._commitment_context_kind(context)
        if context_kind == "bargain" and attitude == "press":
            title = f"旧账索证：{minister_name}"
            target_text = f"{minister_name}须围绕「{context_title}」补交证据、账册或担保，再由御前裁定是否兑现。"
            tasks = [
                f"补交「{context_title}」相关账册、人证或担保",
                "说明证据不足时愿领何责、由谁连坐担保",
            ]
            promise_type = "旧账索证"
            stakes = "证据压力、拖延成怨、担保连坐"
            message = "旧账索证入账"
        elif context_kind == "bargain":
            title = f"兑现旧账：{minister_name}"
            target_text = f"{minister_name}须把「{context_title}」兑现为可验差使、成效或回报，避免旧恩变空账。"
            tasks = [
                f"说明「{context_title}」兑现成何项差使、资源或回报",
                "限期回奏实际成效、受益者与外朝代价",
            ]
            promise_type = "旧账兑现"
            stakes = "旧账兑现、私恩成债、派系要价"
            message = "旧账兑现入账"
        else:
            terms = self._generic_bargain_commitment_terms(minister_name, context, attitude, context_title)
            title = str(terms["title"])
            target_text = str(terms["target_text"])
            tasks = list(terms["tasks"])
            promise_type = str(terms["promise_type"])
            stakes = str(terms["stakes"])
            message = str(terms["message"])
        existing = self._open_bargain_commitment(minister_name, title)
        if existing:
            return {
                "goal_id": int(existing.get("id") or 0),
                "agreement_id": int(existing.get("agreement_id") or 0),
                "title": title,
                "message": message,
                "created": False,
            }
        threshold = 68
        conditions = [{"description": task, "status": "pending"} for task in tasks]
        goal_id = self.db.create_conversation_goal(
            self.state,
            minister_name=minister_name,
            action_kind="audience_bargain",
            title=title,
            target_text=target_text,
            threshold=threshold,
            score=100,
            status="waiting_conditions",
            condition_status="pending",
            conditions=conditions,
            expires_turn=int(self.state.turn) + 3,
            source_chat_turn_id=chat_turn_id,
            last_delta={
                "source": "audience_bargain_commitment",
                "attitude": attitude,
                "memory_id": memory_id,
                "context_title": context_title,
                "context_kind": context_kind,
                "ask": str(context.get("ask") or context.get("motive") or "")[:80],
                "exchange": str(context.get("exchange") or context.get("gain") or "")[:100],
                "cost": str(context.get("cost") or "")[:80],
                "refusal": str(context.get("refusal") or "")[:100],
                "public_hint": f"{promise_type}：{context_title}",
                "user_text": str(user_text or "")[:120],
            },
        )
        agreement_id = self.db.create_negotiation_agreement(
            self.state,
            minister_name=minister_name,
            topic=title,
            action_kind="audience_bargain",
            status="pending",
            stance_id=0,
            handshake_status="sealed",
            psychological_score=100,
            threshold=threshold,
            verbal_only=False,
            core_topic=title,
            target_text=target_text,
            promise_type=promise_type,
            stakes=stakes,
            due_turn=int(self.state.turn) + 2,
            conditions="；".join(tasks),
            summary=f"{minister_name}领下御前{promise_type}，须限期回奏。",
            tasks=tasks,
            goal_id=goal_id,
        )
        self.db.bind_conversation_goal_agreement(goal_id, agreement_id)
        self.db.add_conversation_goal_event(
            self.state,
            goal_id,
            "agreement_created",
            status="waiting_conditions",
            score_delta=0,
            score_after=100,
            summary=f"{message}，进入履约账本 #{agreement_id}",
            payload={"agreement_id": agreement_id, "attitude": attitude, "memory_id": memory_id},
            source_chat_turn_id=chat_turn_id,
        )
        return {
            "goal_id": goal_id,
            "agreement_id": agreement_id,
            "title": title,
            "message": message,
            "created": True,
        }

    def _bargain_chat_effect(
        self,
        minister_name: str,
        context: Optional[Dict[str, Any]],
        user_text: str,
        answer: str = "",
        chat_turn_id: int = 0,
    ) -> Dict[str, Any]:
        if not self._bargain_context_applies(minister_name, context):
            return {}
        attitude = self._bargain_attitude(user_text)
        if not attitude:
            return {}
        row = self.db.conn.execute(
            "SELECT emp_trust, grievance FROM characters WHERE name=? AND status='active'",
            (minister_name,),
        ).fetchone()
        if row is None:
            return {}
        before_trust = max(0, min(100, int(row["emp_trust"] or 55)))
        before_grievance = max(0, min(100, int(row["grievance"] or 20)))
        specs = {
            "accept": {
                "title": "御前许诺",
                "log": "允其所求",
                "trust_delta": +4,
                "grievance_delta": -4,
                "sentiment": "positive",
            },
            "press": {
                "title": "御前索证",
                "log": "准而索证",
                "trust_delta": +1,
                "grievance_delta": +2,
                "sentiment": "mixed",
            },
            "refuse": {
                "title": "御前拒请",
                "log": "拒其所求",
                "trust_delta": -2,
                "grievance_delta": +5,
                "sentiment": "negative",
            },
        }
        spec = specs[attitude]
        after_trust = max(0, min(100, before_trust + int(spec["trust_delta"])))
        after_grievance = max(0, min(100, before_grievance + int(spec["grievance_delta"])))
        self.db.conn.execute(
            "UPDATE characters SET emp_trust=?, grievance=? WHERE name=?",
            (after_trust, after_grievance, minister_name),
        )
        context_title = str((context or {}).get("title") or (context or {}).get("meta") or "御前奏对").strip()
        source_kind = "chat_turn" if chat_turn_id else "dialogue"
        if chat_turn_id:
            source_id = str(chat_turn_id)
        else:
            digest = hashlib.sha1(f"{self.state.turn}:{minister_name}:{user_text}".encode("utf-8")).hexdigest()[:12]
            source_id = f"{self.state.turn}:{minister_name}:{digest}"
        memory_id = self.db.upsert_event_memory(
            self.state,
            "character",
            minister_name,
            "audience_bargain",
            str(spec["title"]),
            cause=context_title,
            process=str(user_text or "")[:80],
            outcome=(
                f"{spec['log']}；信任 {before_trust}->{after_trust}，"
                f"怨望 {before_grievance}->{after_grievance}"
            ),
            sentiment=str(spec["sentiment"]),
            importance=3,
            tags=["奏对交易", attitude, str((context or {}).get("kind") or (context or {}).get("ref_kind") or "")],
            source_kind=source_kind,
            source_id=source_id,
        )
        if memory_id:
            excerpt = f"朕：{str(user_text or '')[:90]}"
            if answer:
                excerpt = f"{excerpt} / {minister_name}：{str(answer or '')[:80]}"
            self.db.add_event_memory_source(
                memory_id,
                source_kind,
                source_id,
                excerpt=excerpt,
                locator={"minister": minister_name, "context": context_title},
            )
        self.db.record_log(
            self.state,
            (
                f"【奏对交易】{minister_name}：{spec['log']}。"
                f"信任 {before_trust}->{after_trust}，怨望 {before_grievance}->{after_grievance}。"
            ),
        )
        commitment = self._create_bargain_commitment(minister_name, context, attitude, user_text, chat_turn_id)
        trust_tone = "good" if after_trust >= before_trust else "bad"
        grievance_tone = "good" if after_grievance <= before_grievance else "bad"
        effects = [
            {"kind": "trust", "label": f"信任 {before_trust}->{after_trust}", "tone": trust_tone},
            {"kind": "grievance", "label": f"怨望 {before_grievance}->{after_grievance}", "tone": grievance_tone},
            {"kind": "memory", "label": "交易入记忆", "tone": "neutral"},
        ]
        if commitment:
            effects.append({
                "kind": "conversation_goal",
                "label": f"履约账本：{str(commitment.get('message') or '旧账')}",
                "tone": "warn",
            })
        return {
            "title": str(spec["title"]),
            "message": (
                f"{minister_name}记下御前态度：信任 {before_trust}->{after_trust}，"
                f"怨望 {before_grievance}->{after_grievance}"
            ),
            "effects": effects,
        }

    def chat(self, minister_name: str, message: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if minister_name not in self.content.characters and minister_name not in self.session.temporary_characters:
            raise HTTPException(status_code=404, detail=f"未找到大臣：{minister_name}")
        text = message.strip()
        if not text:
            raise HTTPException(status_code=400, detail="问话不能为空。")
        character = self.session._character(minister_name)
        chat_turn_id = 0
        before_snapshot: Dict[str, Any] = {}
        history_before_len = len(self.chat_history.get(minister_name, []))
        if self._persistent_chat_minister(minister_name):
            chat_turn_id, before_snapshot = self._start_chat_turn(minister_name)
        self.chat_history.setdefault(minister_name, []).append({"role": "user", "content": text})
        if minister_name not in self.session.temporary_characters:
            message_id = self.db.append_chat_message(minister_name, self.state.turn, "user", text)
            if chat_turn_id:
                self.db.update_chat_turn_messages(chat_turn_id, user_message_id=message_id)
        user_lore_effect = self._eunuch_lore_dialogue_effect(
            self._absorb_eunuch_lore_from_text(minister_name, text)
        )
        deterministic_summon = self._attendant_summon_target(minister_name, text)
        if deterministic_summon:
            target_name = str(deterministic_summon.get("name") or "")
            generated = bool(deterministic_summon.get("generated"))
            answer = self._attendant_summon_answer(
                target_name,
                generated=generated,
                source=str(deterministic_summon.get("source") or ""),
            )
            answer_lore_effect = self._eunuch_lore_dialogue_effect(
                self._absorb_eunuch_lore_from_text(minister_name, answer)
            )
            self._record_chat_rollback_items(chat_turn_id, before_snapshot)
            return self._chat_payload(
                minister_name,
                answer,
                court_action="summon",
                next_minister=target_name,
                registered_minister=target_name if generated else "",
                dialogue_effect=self._combine_dialogue_effects(user_lore_effect, answer_lore_effect),
                chat_turn_id=chat_turn_id,
            )
        dialogue_response = self._dialogue_action_response(minister_name, text)
        if dialogue_response is not None:
            answer = str(dialogue_response.get("answer") or "")
            answer_lore_effect = self._eunuch_lore_dialogue_effect(
                self._absorb_eunuch_lore_from_text(minister_name, answer)
            )
            self._record_chat_rollback_items(chat_turn_id, before_snapshot)
            return self._chat_payload(
                minister_name,
                answer,
                court_action=str(dialogue_response.get("court_action") or ""),
                next_minister=str(dialogue_response.get("next_minister") or ""),
                recruited_minister=str(dialogue_response.get("recruited_minister") or ""),
                dialogue_effect=self._combine_dialogue_effects(
                    dialogue_response.get("dialogue_effect") if isinstance(dialogue_response.get("dialogue_effect"), dict) else None,
                    user_lore_effect,
                    answer_lore_effect,
                ),
                dialogue_goal=dialogue_response.get("dialogue_goal") if isinstance(dialogue_response.get("dialogue_goal"), dict) else None,
                chat_turn_id=chat_turn_id,
            )
        context_brief = self._chat_context_brief(minister_name, context)
        try:
            result = self.session.chat(
                minister_name,
                text,
                source_chat_turn_id=chat_turn_id,
                supplemental_context=context_brief,
            )
        except Exception:
            if chat_turn_id:
                self.db.abort_chat_turn(chat_turn_id, before_snapshot)
            self.chat_history[minister_name] = self.chat_history.get(minister_name, [])[:history_before_len]
            raise
        tool_dialogue_response = self._dialogue_tool_response(
            minister_name,
            getattr(result, "dialogue_action", None),
            result.answer,
            text,
        )
        if tool_dialogue_response is not None:
            result.answer = str(tool_dialogue_response.get("answer") or result.answer)
            if not result.court_action and tool_dialogue_response.get("court_action"):
                result.court_action = str(tool_dialogue_response.get("court_action") or "")
                result.next_minister = str(tool_dialogue_response.get("next_minister") or "")
        if not result.court_action:
            implied_summon = self._attendant_answer_summon_target(minister_name, result.answer)
            if implied_summon:
                result.court_action = "summon"
                result.next_minister = str(implied_summon.get("name") or "")
                if implied_summon.get("generated"):
                    result.registered_minister = result.next_minister
        proposed = None
        if result.proposed_directive is not None:
            d = result.proposed_directive
            proposed = {
                "id": d.id,
                "text": d.text,
                "status": d.status,
                "source": d.source,
                "actor": d.actor,
                "notes": d.notes,
            }
        for portrait_name, reason in (
            (result.appointed_minister, "吏部铨选"),
            (result.registered_minister, "名册补档"),
        ):
            if portrait_name:
                self.maybe_queue_portrait_generation(portrait_name, reason)
        if proposed is None:
            proposed = self._proposed_from_dialogue_goal(result.dialogue_goal, character)
        if proposed is None:
            proposed = self._fallback_pending_directive(character, text, result.answer)
        directive_effect = self._directive_chat_effect(minister_name, context, text, result.answer)
        decision_effect = self._decision_chat_effect(minister_name, context, text, result.answer)
        bargain_effect = self._bargain_chat_effect(minister_name, context, text, result.answer, chat_turn_id)
        tool_dialogue_effect = (
            (tool_dialogue_response or {}).get("dialogue_effect")
            if isinstance((tool_dialogue_response or {}).get("dialogue_effect"), dict)
            else None
        )
        answer_lore_effect = self._eunuch_lore_dialogue_effect(
            self._absorb_eunuch_lore_from_text(minister_name, result.answer)
        )
        dialogue_effect = self._combine_dialogue_effects(
            tool_dialogue_effect,
            decision_effect,
            bargain_effect,
            user_lore_effect,
            answer_lore_effect,
        )
        self._record_chat_rollback_items(chat_turn_id, before_snapshot)
        return self._chat_payload(
            minister_name, result.answer,
            court_action=result.court_action, next_minister=result.next_minister,
            proposed_directive=proposed, appointed_minister=result.appointed_minister,
            registered_minister=result.registered_minister,
            recruited_minister=str((tool_dialogue_response or {}).get("recruited_minister") or ""),
            displaced_minister=result.displaced_minister,
            displaced_effect=result.displaced_effect,
            secret_order_id=result.secret_order_id,
            secret_order_assignee=result.secret_order_assignee,
            secret_order_effect=result.secret_order_effect,
            directive_effect=directive_effect,
            dialogue_effect=dialogue_effect,
            chat_turn_id=chat_turn_id,
            dialogue_goal=result.dialogue_goal,
        )

    def chat_stream(self, minister_name: str, message: str, context: Optional[Dict[str, Any]] = None) -> Iterator[Dict[str, Any]]:
        if minister_name not in self.content.characters and minister_name not in self.session.temporary_characters:
            yield {"type": "error", "message": f"未找到大臣：{minister_name}"}
            return
        text = message.strip()
        if not text:
            yield {"type": "error", "message": "问话不能为空。"}
            return
        chat_turn_id = 0
        before_snapshot: Dict[str, Any] = {}
        history_before_len = len(self.chat_history.get(minister_name, []))
        if self._persistent_chat_minister(minister_name):
            chat_turn_id, before_snapshot = self._start_chat_turn(minister_name)
        self.chat_history.setdefault(minister_name, []).append({"role": "user", "content": text})
        if minister_name not in self.session.temporary_characters:
            message_id = self.db.append_chat_message(minister_name, self.state.turn, "user", text)
            if chat_turn_id:
                self.db.update_chat_turn_messages(chat_turn_id, user_message_id=message_id)
        user_lore_effect = self._eunuch_lore_dialogue_effect(
            self._absorb_eunuch_lore_from_text(minister_name, text)
        )
        deterministic_summon = self._attendant_summon_target(minister_name, text)
        if deterministic_summon:
            target_name = str(deterministic_summon.get("name") or "")
            generated = bool(deterministic_summon.get("generated"))
            answer = self._attendant_summon_answer(
                target_name,
                generated=generated,
                source=str(deterministic_summon.get("source") or ""),
            )
            yield {"type": "delta", "content": answer}
            answer_lore_effect = self._eunuch_lore_dialogue_effect(
                self._absorb_eunuch_lore_from_text(minister_name, answer)
            )
            self._record_chat_rollback_items(chat_turn_id, before_snapshot)
            payload = self._chat_payload(
                minister_name,
                answer,
                court_action="summon",
                next_minister=target_name,
                registered_minister=target_name if generated else "",
                dialogue_effect=self._combine_dialogue_effects(user_lore_effect, answer_lore_effect),
                chat_turn_id=chat_turn_id,
            )
            yield {"type": "done", "payload": payload}
            return
        dialogue_response = self._dialogue_action_response(minister_name, text)
        if dialogue_response is not None:
            answer = str(dialogue_response.get("answer") or "")
            yield {"type": "delta", "content": answer}
            answer_lore_effect = self._eunuch_lore_dialogue_effect(
                self._absorb_eunuch_lore_from_text(minister_name, answer)
            )
            self._record_chat_rollback_items(chat_turn_id, before_snapshot)
            payload = self._chat_payload(
                minister_name,
                answer,
                court_action=str(dialogue_response.get("court_action") or ""),
                next_minister=str(dialogue_response.get("next_minister") or ""),
                recruited_minister=str(dialogue_response.get("recruited_minister") or ""),
                dialogue_effect=self._combine_dialogue_effects(
                    dialogue_response.get("dialogue_effect") if isinstance(dialogue_response.get("dialogue_effect"), dict) else None,
                    user_lore_effect,
                    answer_lore_effect,
                ),
                dialogue_goal=dialogue_response.get("dialogue_goal") if isinstance(dialogue_response.get("dialogue_goal"), dict) else None,
                chat_turn_id=chat_turn_id,
            )
            yield {"type": "done", "payload": payload}
            return
        character = self.session._character(minister_name)
        chunks: List[str] = []
        context_brief = self._chat_context_brief(minister_name, context)
        try:
            if self.session.registry is None:
                raise RuntimeError("GameSession.begin_turn() 未调用。")
            agent = self.session.registry.get(character)
            augmented, dialogue_prep = self.session.prepare_chat_run(
                character,
                text,
                source_chat_turn_id=chat_turn_id,
                supplemental_context=context_brief,
            )
            run_output = None
            stream = agent.run(augmented, stream=True, stream_events=True, yield_run_output=True)
            for event in stream:
                content = getattr(event, "content", None)
                event_name = getattr(event, "event", "")
                if event_name == "RunContent" and content:
                    delta = str(content)
                    chunks.append(delta)
                    yield {"type": "delta", "content": delta}
                if type(event).__name__ in ("RunOutput", "RunCompletedEvent"):
                    run_output = event
            # 流式跑完补 dump：流式 run_output(RunCompletedEvent)常无 .messages，
            # 传 agent= 让 _dump_llm_messages 走 agent.get_last_run_output() fallback 取 system/user。
            _dump_llm_messages(run_output, f"大臣对话/{minister_name}", agent=agent)
            answer = "".join(chunks).strip()
            fail_if_llm_error(answer, "LLM 调用")
            if not answer and run_output is not None:
                answer = extract_agent_text(run_output)
            if not answer:
                raise LLMUnavailable("LLM 调用失败：流式回复为空。")
            # 截 propose_directive：入 pending；截 propose_appointment：吏部铨选建档
            proposed = None
            appointed = ""
            registered = ""
            court_action = ""
            next_minister = ""
            displaced = ""
            displaced_effect: Dict[str, Any] = {}
            secret_order_id = 0
            secret_order_assignee = ""
            secret_order_effect: Dict[str, Any] = {}
            dialogue_tool_action: Dict[str, Any] = {}
            if run_output is not None:
                for tool_exec in getattr(run_output, "tools", None) or []:
                    res = str(getattr(tool_exec, "result", "") or "")
                    tool_name = getattr(tool_exec, "tool_name", "")
                    if tool_name == "propose_directive" or res.startswith("__pending_directive__"):
                        draft_text = res.removeprefix("__pending_directive__").strip()
                        if not draft_text:
                            args = getattr(tool_exec, "arguments", {}) or getattr(tool_exec, "tool_args", {}) or {}
                            draft_text = (args.get("decree_text") or "").strip()
                        proposed = self._record_pending_directive(character, draft_text)
                    elif tool_name == "propose_appointment" or res.startswith("__pending_appointment__"):
                        payload_json = res.removeprefix("__pending_appointment__").strip()
                        if not payload_json:
                            args = getattr(tool_exec, "arguments", {}) or getattr(tool_exec, "tool_args", {}) or {}
                            payload_json = json.dumps(args, ensure_ascii=False)
                        appointed, displaced, displaced_effect = self.session._apply_appointment(payload_json, character)
                    elif tool_name == "register_unlisted_person" or res.startswith("__pending_unlisted_person__"):
                        payload_json = res.removeprefix("__pending_unlisted_person__").strip()
                        if not payload_json:
                            args = getattr(tool_exec, "arguments", {}) or getattr(tool_exec, "tool_args", {}) or {}
                            payload_json = json.dumps(args, ensure_ascii=False)
                        registered, summon_after = self.session._apply_unlisted_person_registration(payload_json)
                        if registered and summon_after:
                            court_action = "summon"
                            next_minister = registered
                    elif res.startswith("__dialogue_action__") or tool_name in {
                        "propose_recruitment",
                        "confirm_recruitment",
                        "propose_castration",
                        "confirm_castration",
                        "propose_mediation",
                        "confirm_mediation",
                        "propose_eunuch_care",
                        "confirm_eunuch_care",
                        "propose_eunuch_hard_service",
                        "confirm_eunuch_hard_service",
                        "propose_bao_leverage",
                        "confirm_bao_leverage",
                    }:
                        payload_json = res.removeprefix("__dialogue_action__").strip()
                        if not payload_json:
                            args = getattr(tool_exec, "arguments", {}) or getattr(tool_exec, "tool_args", {}) or {}
                            payload_json = json.dumps(args, ensure_ascii=False)
                        try:
                            action = json.loads(payload_json) if payload_json else {}
                        except (TypeError, ValueError):
                            action = {}
                        if isinstance(action, dict):
                            dialogue_tool_action = action
                    elif tool_name == "summon_minister" or res.startswith("__summon__"):
                        target_name = res.removeprefix("__summon__").strip()
                        if not target_name:
                            args = getattr(tool_exec, "arguments", {}) or getattr(tool_exec, "tool_args", {}) or {}
                            target_name = args.get("name", "")
                        if target_name:
                            try:
                                target, _is_temporary = self.session.summon_character(
                                    target_name, character, allow_temporary=False
                                )
                            except ValueError:
                                target = None
                            if target is not None:
                                ok, _reason = self.session.can_summon(target)
                                if ok:
                                    court_action = "summon"
                                    next_minister = target.name
                    elif tool_name == "dismiss_minister" or res == "__dismiss__":
                        court_action = "dismiss"
                    elif tool_name == "issue_secret_order" or res.startswith("__secret_order_registered__") or res.startswith("__secret_order__"):
                        if res.startswith("__secret_order_registered__"):
                            secret_order_id, secret_order_assignee = _parse_registered_secret_order_result(res)
                        else:
                            payload_json = res.removeprefix("__secret_order__").strip()
                            if not payload_json:
                                args = getattr(tool_exec, "arguments", {}) or getattr(tool_exec, "tool_args", {}) or {}
                                payload_json = json.dumps(args, ensure_ascii=False)
                            try:
                                payload_data = json.loads(payload_json) if payload_json else {}
                                if isinstance(payload_data, dict):
                                    secret_order_assignee = str(payload_data.get("assignee") or "").strip() or minister_name
                            except (TypeError, ValueError):
                                secret_order_assignee = minister_name
                            secret_order_id = self.session._apply_secret_order(payload_json, minister_name)
                        if secret_order_id:
                            secret_order_effect = self.session.record_secret_order_effect(
                                secret_order_id,
                                secret_order_assignee or minister_name,
                            )
                    # 密令结案不再走大臣工具，由月末推演 + extractor 写入
            dialogue_tool_response = self._dialogue_tool_response(
                minister_name,
                dialogue_tool_action,
                answer,
                text,
            )
            if dialogue_tool_response is not None:
                updated_answer = str(dialogue_tool_response.get("answer") or answer)
                if updated_answer != answer:
                    extra = updated_answer[len(answer):] if updated_answer.startswith(answer) else f"\n{updated_answer}"
                    if extra:
                        chunks.append(extra)
                        yield {"type": "delta", "content": extra}
                    answer = updated_answer
                if not court_action and dialogue_tool_response.get("court_action"):
                    court_action = str(dialogue_tool_response.get("court_action") or "")
                    next_minister = str(dialogue_tool_response.get("next_minister") or "")
            if not court_action:
                implied_summon = self._attendant_answer_summon_target(minister_name, answer)
                if implied_summon:
                    court_action = "summon"
                    next_minister = str(implied_summon.get("name") or "")
                    if implied_summon.get("generated"):
                        registered = next_minister
            dialogue_goal = self.session.record_dialogue_after_chat(
                character,
                text,
                answer,
                dialogue_prep,
                source_chat_turn_id=chat_turn_id,
                directive_already_recorded=proposed is not None,
            )
            if proposed is None:
                proposed = self._proposed_from_dialogue_goal(dialogue_goal, character)
            if proposed is None:
                proposed = self._fallback_pending_directive(character, text, answer)
            directive_effect = self._directive_chat_effect(minister_name, context, text, answer)
            decision_effect = self._decision_chat_effect(minister_name, context, text, answer)
            bargain_effect = self._bargain_chat_effect(minister_name, context, text, answer, chat_turn_id)
            tool_dialogue_effect = (
                (dialogue_tool_response or {}).get("dialogue_effect")
                if isinstance((dialogue_tool_response or {}).get("dialogue_effect"), dict)
                else None
            )
            answer_lore_effect = self._eunuch_lore_dialogue_effect(
                self._absorb_eunuch_lore_from_text(minister_name, answer)
            )
            dialogue_effect = self._combine_dialogue_effects(
                tool_dialogue_effect,
                decision_effect,
                bargain_effect,
                user_lore_effect,
                answer_lore_effect,
            )
            self._record_chat_rollback_items(chat_turn_id, before_snapshot)
            for portrait_name, reason in (
                (appointed, "吏部铨选"),
                (registered, "名册补档"),
            ):
                if portrait_name:
                    self.maybe_queue_portrait_generation(portrait_name, reason)
            payload = self._chat_payload(
                minister_name, answer, court_action=court_action, next_minister=next_minister,
                proposed_directive=proposed, appointed_minister=appointed,
                registered_minister=registered,
                recruited_minister=str((dialogue_tool_response or {}).get("recruited_minister") or ""),
                displaced_minister=displaced,
                displaced_effect=displaced_effect,
                secret_order_id=secret_order_id,
                secret_order_assignee=secret_order_assignee,
                secret_order_effect=secret_order_effect,
                directive_effect=directive_effect,
                dialogue_effect=dialogue_effect,
                chat_turn_id=chat_turn_id,
                dialogue_goal=dialogue_goal,
            )
            yield {"type": "done", "payload": payload}
        except Exception as error:
            if chat_turn_id:
                try:
                    self.db.abort_chat_turn(chat_turn_id, before_snapshot)
                except Exception:
                    self.db.mark_chat_turn_failed(chat_turn_id)
            self.chat_history[minister_name] = self.chat_history.get(minister_name, [])[:history_before_len]
            if isinstance(error, LLMUnavailable):
                yield {"type": "error", "detail": _llm_error_detail(error)}
            else:
                yield {"type": "error", "message": str(error)}

    def _audience_stake_suggestions(self, character: Character) -> List[Dict[str, Any]]:
        """Character-specific opening hooks for summons.

        Keep these facts server-side: they are derived from simulation tables
        (agenda, relationships, favors, obligations, directives) and should not
        become client-only roleplay prompts.
        """
        name = character.name
        suggestions: List[Dict[str, Any]] = []
        seen_labels: set[str] = set()

        def add(label: str, text: str, *, prefix: bool = True) -> None:
            clean_label = str(label or "").strip()[:6]
            clean_text = re.sub(r"\s+", " ", str(text or "").strip())
            if not clean_label or not clean_text or clean_label in seen_labels:
                return
            seen_labels.add(clean_label)
            item: Dict[str, Any] = {"label": clean_label, "text": clean_text}
            if prefix:
                item["prefix"] = True
            suggestions.append(item)

        try:
            active_goals = self.db.list_conversation_goals(
                minister_name=name,
                statuses=["active", "waiting_conditions", "blocked", "expired"],
                limit=2,
            )
        except Exception:
            active_goals = []
        for goal in active_goals[:1]:
            title = str(goal.get("title") or goal.get("target_text") or "未完奏对")[:32]
            status = str(goal.get("status") or "")
            last_delta = goal.get("last_delta") if isinstance(goal.get("last_delta"), dict) else {}
            court_decision = last_delta.get("court_decision") if isinstance(last_delta.get("court_decision"), dict) else {}
            action_kind = str(goal.get("action_kind") or "").strip()
            if action_kind == "eunuch_care" or str(court_decision.get("action") or "") == "eunuch_care":
                add(
                    "问隐情",
                    f"朕听说你因「{title}」候见。别讲空话，照奴婢本分说清：是身上旧疾、惊创失神、旧匣心结，还是差遣会误事？",
                )
                add(
                    "准调养",
                    f"若确是「{title}」牵动差事，朕可准动内库调养或查验安置。你要哪一种，需花多少银钱，能换回什么差遣成效？",
                )
                add(
                    "照常派",
                    f"若朕不许调养，仍让你照常办差，你说清会误在哪一步；能硬撑就领责，撑不住也要现在明白回奏。",
                )
                continue
            add(
                "追旧约",
                f"朕记得你在「{title}」上已有话头。今日不许泛泛而谈：进展、卡点、可验凭据各是什么？"
                + ("若已过期，更要说清延误责任。" if status == "expired" else ""),
            )

        directive = next(
            (
                row for row in self.directive_rows()
                if str(row["actor"] or "").strip() == name or str(row["notes"] or "").find(name) >= 0
            ),
            None,
        )
        if directive is not None:
            d_text = str(directive["text"] or directive["notes"] or "本回合拟旨")[:36]
            add("核拟旨", f"你与「{d_text}」有关。朕先不核定，先问你：此旨真正要害、阻力和可验成效是什么？")

        followup = next(
            (item for item in (getattr(self.session, "monthly_followups", []) or [])
             if str(item.get("minister_name") or "") == name),
            None,
        )
        if isinstance(followup, dict):
            title = str(followup.get("title") or followup.get("summary") or "本月主动请安")[:42]
            risk_tags = "、".join(str(tag) for tag in (followup.get("risk_tags") or [])[:3] if str(tag).strip())
            add("听请安", f"朕准你请安。你本月为「{title}」主动求见，先说清楚：要复命、求资源，还是求一道明旨？{('风险在' + risk_tags + '，也一并说。') if risk_tags else ''}")

        try:
            from ming_sim import court
            agenda = court.agenda_of(self.db, name)
        except Exception:
            court = None
            agenda = None
        if agenda and str(agenda.get("status") or "active") == "active":
            title = str(agenda.get("title") or "私心")[:42]
            kind = str(agenda.get("kind") or "")
            target = str(agenda.get("target_name") or agenda.get("target") or "")
            try:
                from ming_sim.playstyle import _agenda_bargain_profile
                profile = _agenda_bargain_profile(kind, target)
            except Exception:
                profile = {
                    "ask": "求名分、求台阶或求差使",
                    "exchange": "给期限、要证据、设担保，再看能否任用",
                    "cost": "许之有短期收益，也会留下人情账",
                    "refusal": "拒之可能转成怨望或暗中掣肘",
                }
            add(
                "问私心",
                f"朕闻你近来有「{title}」之势。你多半要求「{profile['ask']}」。"
                f"今日自己说，是为国任事，还是另有所图？",
            )
            add(
                "设交易",
                f"若朕给你边界或差使，你须先「{profile['exchange']}」。"
                f"你做得到，还是要朕替你担下「{profile['cost']}」？",
            )

        if court is not None:
            try:
                rivals = court.rivals_of(self.db, name, limit=1, threshold=-18)
            except Exception:
                rivals = []
            if rivals:
                rival = str(rivals[0].get("name") or "")
                basis = str(rivals[0].get("basis") or "旧怨")[:28]
                add("问政敌", f"朕知道你与{rival}有嫌隙（{basis}）。若朕令你们共办一事，你肯退哪一步，又要朕给什么边界？")
            try:
                favors = court.favor_memories(self.db, name, limit=1)
            except Exception:
                favors = []
            if favors:
                favor = favors[0]
                title = str(favor.get("title") or "旧恩")[:30]
                outcome = str(favor.get("outcome") or favor.get("cause") or "")[:42]
                add("点旧恩", f"朕昔日给过你「{title}」的余地。今日召你，是要听你如何还这笔旧恩；{outcome or '不要只谢恩，要说可验差使。'}")
            try:
                allies = court.allies_of(self.db, name, limit=1, threshold=22)
            except Exception:
                allies = []
            if allies:
                ally = str(allies[0].get("name") or "")
                basis = str(allies[0].get("basis") or "声气相通")[:28]
                add("问党羽", f"你与{ally}声气相通（{basis}）。若朕让你荐人或办事，如何保证不是借公事植党？")

        return suggestions[:4]

    def suggestions_for(self, character: Character) -> List[Dict[str, Any]]:
        suggestions: List[Dict[str, Any]] = self._audience_stake_suggestions(character)
        if not suggestions:
            suggestions.extend([
                {"label": "问在办", "text": "当前在办的事项里，哪几件轻重缓急最该先理？"},
                {"label": "问阻力", "text": "眼下推进朝政，最大的阻力来自哪一方？"},
            ])
        skill_ids = set(available_skill_ids(character, self.db))
        if "check_treasury" in skill_ids:
            suggestions.insert(min(1, len(suggestions)), {"label": "查钱粮", "text": "太仓和内库实数如何？本月哪些钱最急？"})
        if "check_military" in skill_ids or "front_line_plan" in skill_ids or "strategic_review" in skill_ids:
            suggestions.insert(min(1, len(suggestions)), {"label": "查驻军", "text": "查一下关宁军、京营和陕西边军的士气、欠饷与补给。"})
        if "secret_investigation" in skill_ids:
            suggestions.insert(min(1, len(suggestions)), {"label": "密查", "text": "哪些账册和人物最该先密查？"})
        structural = [
            {"label": "拟旨", "text": "拟旨如下：", "prefix": True},
            {"label": "下密令", "text": "密令如下：", "prefix": True},
        ]
        labels = {str(item.get("label") or "") for item in suggestions}
        for item in structural:
            if item["label"] not in labels:
                suggestions.append(item)
        natural = self._llm_contextual_suggestions(character, suggestions)
        if natural:
            natural_labels = {str(item.get("label") or "") for item in natural}
            for item in structural:
                if item["label"] not in natural_labels:
                    natural.append(item)
                    natural_labels.add(item["label"])
            return natural[:6]
        return suggestions[:6]

    def _llm_contextual_suggestions(self, character: Character, seed_suggestions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if os.environ.get("MING_SIM_DISABLE_LLM_QUICK_SUGGESTIONS", "").strip().lower() in ("1", "true", "yes"):
            return []
        if not seed_suggestions:
            return []
        cache_key = ""
        try:
            history_tail = [
                str(message.get("content") or "")[:180]
                for message in self.chat_history.get(character.name, [])[-4:]
                if isinstance(message, dict)
            ]
            signature = json.dumps(
                {
                    "turn": int(self.state.turn),
                    "name": character.name,
                    "seed": [
                        {
                            "label": str(row.get("label") or ""),
                            "text": str(row.get("text") or "")[:220],
                            "prefix": bool(row.get("prefix", False)),
                        }
                        for row in seed_suggestions[:8]
                    ],
                    "history": history_tail,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            cache_key = hashlib.sha1(signature.encode("utf-8")).hexdigest()
            cache = getattr(self, "_dialogue_suggestion_cache", None)
            if not isinstance(cache, dict):
                cache = {}
                setattr(self, "_dialogue_suggestion_cache", cache)
            cached = cache.get(cache_key)
            if isinstance(cached, list):
                return [dict(item) for item in cached if isinstance(item, dict)]
        except Exception:
            cache = {}
        try:
            from ming_sim.dialogue_audit import dialogue_suggestions_audit

            natural = dialogue_suggestions_audit(
                self.db,
                self.state,
                character,
                seed_suggestions,
                llm_config=self.session.llm_config,
                agno_db=self.session.agno_db,
                audit_client=self.session.dialogue_audit_client,
            )
        except Exception:
            natural = []
        clean: List[Dict[str, Any]] = []
        for item in natural:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "").strip()
            text = str(item.get("text") or "").strip()
            if not label or not text:
                continue
            clean.append({"label": label, "text": text, "prefix": bool(item.get("prefix", True))})
            if len(clean) >= 5:
                break
        try:
            if cache_key and clean:
                cache[cache_key] = [dict(item) for item in clean]
        except Exception:
            pass
        return clean


def sse_event(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


web_game: Optional[WebGame] = None  # 未启用登录时的本地单用户兼容实例
_web_games: Dict[str, WebGame] = {}
_web_games_lock = threading.RLock()
_turn_resolution_capacity_lock = threading.RLock()
_active_turn_resolutions = 0
_auth_sessions: Dict[str, Any] = {}
_auth_sessions_lock = threading.RLock()
_current_username: ContextVar[str] = ContextVar("current_username", default="")
_AUTH_COOKIE = "ming_session"
_SERVER_STARTED_AT = time.time()
_LOG = logging.getLogger("ming_sim.web")
if not logging.getLogger().handlers and (
    os.environ.get("MING_SIM_LOG_LEVEL") or os.environ.get("MING_SIM_JSON_LOGS")
):
    logging.basicConfig(level=os.environ.get("MING_SIM_LOG_LEVEL", "INFO").upper())


def _env_int(name: str, default: int, *, minimum: int = 0, maximum: int = 10**9) -> int:
    try:
        value = int(os.environ.get(name, str(default)) or default)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def _web_chat_history_limit() -> int:
    return _env_int("MING_SIM_WEB_CHAT_HISTORY_LIMIT", 80, minimum=20, maximum=500)


def _max_running_games() -> int:
    return _env_int("MING_SIM_MAX_RUNNING_GAMES", 64, minimum=1, maximum=10000)


def _max_concurrent_turn_resolutions() -> int:
    return _env_int("MING_SIM_MAX_CONCURRENT_TURNS", 2, minimum=1, maximum=1000)


app = FastAPI(title="Ming Salvage MVP Web")


_GZIP_SKIP_PATH_SUFFIXES = (
    ".avif",
    ".gif",
    ".ico",
    ".jpg",
    ".jpeg",
    ".mp3",
    ".mp4",
    ".png",
    ".webm",
    ".webp",
    ".woff",
    ".woff2",
)


class SelectiveGZipMiddleware(GZipMiddleware):
    async def __call__(self, scope, receive, send):
        path = str(scope.get("path") or "")
        if scope.get("type") == "http" and (path.endswith("/stream") or path.lower().endswith(_GZIP_SKIP_PATH_SUFFIXES)):
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)


app.add_middleware(SelectiveGZipMiddleware, minimum_size=1024)


_STATIC_IMMUTABLE_CACHE = "public, max-age=31536000, immutable"
_STATIC_MEDIA_CACHE = "public, max-age=604800"
_STATIC_HTML_CACHE = "no-cache"
_STATIC_SCRIPT_STYLE_RE = re.compile(r"\.(?:js|css|mjs)$", re.IGNORECASE)
_STATIC_MEDIA_RE = re.compile(r"\.(?:png|jpe?g|webp|gif|svg|ico|woff2?|ttf|otf)$", re.IGNORECASE)


class CacheControlledStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: Dict[str, Any]) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code != 200:
            return response

        clean_path = path.lstrip("/")
        content_type = response.headers.get("content-type", "")
        if clean_path in {"", ".", "index.html"} or clean_path.endswith(".html") or content_type.startswith("text/html"):
            response.headers["Cache-Control"] = _STATIC_HTML_CACHE
        elif clean_path.startswith("assets/") and _STATIC_SCRIPT_STYLE_RE.search(clean_path):
            # The mobile shell is small enough to revalidate. Avoid sticky old UI
            # after deploys if a build reuses an asset basename.
            response.headers["Cache-Control"] = _STATIC_HTML_CACHE
        elif clean_path.startswith("assets/"):
            response.headers["Cache-Control"] = _STATIC_IMMUTABLE_CACHE
        elif _STATIC_MEDIA_RE.search(clean_path):
            response.headers.setdefault("Cache-Control", _STATIC_MEDIA_CACHE)
        return response


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str
    password: str
    invite_code: str


class ServerAdminActionRequest(BaseModel):
    confirm: str = ""


def _configured_auth_users() -> Dict[str, str]:
    """读取服务端账号配置。未配置账号时保持本地单用户免登录模式。

    支持：
      MING_SIM_SERVER_USERS="alice:pw,bob:pw"
      MING_SIM_AUTH_USERS="alice:pw"
      MING_SIM_ADMIN_USER / MING_SIM_ADMIN_PASSWORD
    """
    users: Dict[str, str] = _registered_auth_users()
    raw = os.environ.get("MING_SIM_SERVER_USERS", "") or os.environ.get("MING_SIM_AUTH_USERS", "")
    for part in re.split(r"[,\n;]+", raw):
        item = part.strip()
        if not item or ":" not in item:
            continue
        username, password = item.split(":", 1)
        username = username.strip()
        password = password.strip()
        if username and password:
            users[username] = password
    admin_user = os.environ.get("MING_SIM_ADMIN_USER", "").strip()
    admin_password = os.environ.get("MING_SIM_ADMIN_PASSWORD", "").strip()
    if admin_user and admin_password:
        users[admin_user] = admin_password
    return users


def _split_env_list(raw: str) -> List[str]:
    return [part.strip() for part in re.split(r"[,\n;]+", raw or "") if part.strip()]


def _configured_admin_users() -> set:
    admins = set(_split_env_list(os.environ.get("MING_SIM_ADMIN_USERS", "")))
    admins.update(_split_env_list(os.environ.get("MING_SIM_SERVER_ADMINS", "")))
    admin_user = os.environ.get("MING_SIM_ADMIN_USER", "").strip()
    if admin_user:
        admins.add(admin_user)
    if not admins:
        users = _configured_auth_users()
        if users:
            admins.add(next(iter(users.keys())))
    return admins


def _auth_enabled() -> bool:
    return bool(_configured_auth_users()) or _registration_auth_mode_enabled()


def _verify_password(stored: str, provided: str) -> bool:
    stored = stored.strip()
    provided = provided or ""
    if stored.startswith("pbkdf2_sha256$"):
        try:
            _, iterations, salt, expected = stored.split("$", 3)
            rounds = max(100_000, min(2_000_000, int(iterations)))
            actual = hashlib.pbkdf2_hmac("sha256", provided.encode("utf-8"), salt.encode("utf-8"), rounds).hex()
            return secrets.compare_digest(actual, expected.lower())
        except (TypeError, ValueError):
            return False
    if stored.startswith("sha256:"):
        expected = stored.split(":", 1)[1].strip().lower()
        actual = hashlib.sha256(provided.encode("utf-8")).hexdigest()
        return secrets.compare_digest(actual, expected)
    return secrets.compare_digest(stored, provided)


def _safe_user_id(username: str) -> str:
    base = re.sub(r"[^A-Za-z0-9_.-]+", "_", username.strip()).strip("._-")[:48] or "user"
    digest = hashlib.sha256(username.encode("utf-8")).hexdigest()[:10]
    return f"{base}_{digest}"


def _legacy_db_path() -> str:
    db_path = os.environ.get("MING_SIM_DB", "")
    if not db_path:
        return user_data_path("ming_sim.db")
    if not os.path.isabs(db_path):
        return str(user_data_dir() / db_path)
    return db_path


def _user_root_dir(username: str) -> str:
    root = user_data_dir() / "users" / _safe_user_id(username)
    root.mkdir(parents=True, exist_ok=True)
    return str(root)


def _db_path_for_user(username: str = "") -> str:
    if username.strip():
        return os.path.join(_user_root_dir(username), "ming_sim.db")
    return _legacy_db_path()


def _saves_dir_for_user(username: str = "") -> str:
    if username.strip():
        path = os.path.join(_user_root_dir(username), "saves")
        os.makedirs(path, exist_ok=True)
        return path
    return user_data_path("saves")


def _portrait_dir_for_user(username: str = "") -> str:
    if username.strip():
        path = os.path.join(_user_root_dir(username), "uploads", "portraits")
        os.makedirs(path, exist_ok=True)
        return path
    return UPLOAD_PORTRAIT_DIR


def _session_ttl_seconds() -> int:
    return _env_int("MING_SIM_SESSION_TTL_SECONDS", 60 * 60 * 24 * 7, minimum=300)


def _server_state_db_path() -> str:
    return user_data_path("server_state.sqlite3")


def _server_state_conn() -> sqlite3.Connection:
    path = _server_state_db_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS auth_sessions (
            token TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            created_at REAL NOT NULL,
            last_seen REAL NOT NULL,
            expires_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_auth_sessions_username ON auth_sessions(username);
        CREATE INDEX IF NOT EXISTS idx_auth_sessions_expires ON auth_sessions(expires_at);

        CREATE TABLE IF NOT EXISTS login_rate_limits (
            bucket TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            remote_addr TEXT NOT NULL,
            window_start REAL NOT NULL,
            attempts INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS registered_users (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            created_at REAL NOT NULL,
            last_login_at REAL NOT NULL DEFAULT 0
        );
        """
    )
    return conn


def _registration_invite_code() -> str:
    return os.environ.get("MING_SIM_INVITE_CODE", "shdl95598").strip()


def _registration_enabled() -> bool:
    return os.environ.get("MING_SIM_ALLOW_REGISTRATION", "1").strip().lower() not in ("0", "false", "no")


def _registration_auth_mode_enabled() -> bool:
    if not _registration_enabled():
        return False
    return (
        bool(os.environ.get("MING_SIM_INVITE_CODE", "").strip())
        or os.environ.get("MING_SIM_ALLOW_REGISTRATION", "").strip().lower() in ("1", "true", "yes")
    )


def _normalize_auth_username(username: str) -> str:
    return re.sub(r"\s+", "", (username or "").strip())


def _validate_register_username(username: str) -> str:
    username = _normalize_auth_username(username)
    if not re.fullmatch(r"[A-Za-z0-9_.\-\u4e00-\u9fff]{2,32}", username):
        raise HTTPException(
            status_code=400,
            detail={"code": "bad_username", "message": "用户名需为 2-32 位中文、字母、数字或 ._-。"},
        )
    return username


def _validate_register_password(password: str) -> str:
    password = password or ""
    if len(password) < 6 or len(password) > 128:
        raise HTTPException(
            status_code=400,
            detail={"code": "bad_password", "message": "密码需为 6-128 位。"},
        )
    return password


def _hash_password(password: str) -> str:
    rounds = _env_int("MING_SIM_PASSWORD_PBKDF2_ROUNDS", 200_000, minimum=100_000, maximum=2_000_000)
    salt = secrets.token_urlsafe(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), rounds).hex()
    return f"pbkdf2_sha256${rounds}${salt}${digest}"


def _registered_auth_users() -> Dict[str, str]:
    try:
        with closing(_server_state_conn()) as conn, conn:
            rows = conn.execute("SELECT username, password_hash FROM registered_users").fetchall()
            return {str(row["username"]): str(row["password_hash"]) for row in rows}
    except sqlite3.DatabaseError as exc:
        _LOG.warning("registered_user_load_failed", exc_info=exc)
        return {}


def _create_registered_user(username: str, password: str) -> None:
    now = time.time()
    try:
        with closing(_server_state_conn()) as conn, conn:
            existing = conn.execute(
                "SELECT 1 FROM registered_users WHERE username = ?",
                (username,),
            ).fetchone()
            if existing is not None:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "username_taken", "message": "这个用户名已被注册。"},
                )
            conn.execute(
                """
                INSERT INTO registered_users(username, password_hash, created_at, last_login_at)
                VALUES (?, ?, ?, 0)
                """,
                (username, _hash_password(password), now),
            )
    except HTTPException:
        raise
    except sqlite3.DatabaseError as exc:
        _LOG.warning("registered_user_create_failed", exc_info=exc)
        raise HTTPException(
            status_code=500,
            detail={"code": "registration_failed", "message": "注册失败，请稍后再试。"},
        ) from None


def _mark_registered_user_login(username: str) -> None:
    try:
        with closing(_server_state_conn()) as conn, conn:
            conn.execute("UPDATE registered_users SET last_login_at = ? WHERE username = ?", (time.time(), username))
    except sqlite3.DatabaseError as exc:
        _LOG.warning("registered_user_login_mark_failed", exc_info=exc)


def _cleanup_persistent_auth_sessions(now: Optional[float] = None) -> None:
    now = time.time() if now is None else now
    try:
        with closing(_server_state_conn()) as conn, conn:
            conn.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (now,))
    except sqlite3.DatabaseError as exc:
        _LOG.warning("auth_session_cleanup_failed", exc_info=exc)


def _persist_auth_session(token: str, record: Dict[str, Any]) -> None:
    try:
        with closing(_server_state_conn()) as conn, conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO auth_sessions(token, username, created_at, last_seen, expires_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    token,
                    str(record.get("username") or ""),
                    float(record.get("created_at") or 0),
                    float(record.get("last_seen") or 0),
                    float(record.get("expires_at") or 0),
                ),
            )
    except sqlite3.DatabaseError as exc:
        _LOG.warning("auth_session_persist_failed", exc_info=exc)


def _load_persistent_auth_session(token: str, now: Optional[float] = None) -> Dict[str, Any]:
    if not token:
        return {}
    now = time.time() if now is None else now
    try:
        with closing(_server_state_conn()) as conn, conn:
            row = conn.execute(
                "SELECT username, created_at, last_seen, expires_at FROM auth_sessions WHERE token = ?",
                (token,),
            ).fetchone()
            if row is None:
                return {}
            if float(row["expires_at"] or 0) <= now:
                conn.execute("DELETE FROM auth_sessions WHERE token = ?", (token,))
                return {}
            conn.execute("UPDATE auth_sessions SET last_seen = ? WHERE token = ?", (now, token))
            return {
                "username": str(row["username"] or ""),
                "created_at": float(row["created_at"] or now),
                "last_seen": now,
                "expires_at": float(row["expires_at"] or 0),
            }
    except sqlite3.DatabaseError as exc:
        _LOG.warning("auth_session_load_failed", exc_info=exc)
        return {}


def _delete_persistent_auth_session(token: str) -> None:
    if not token:
        return
    try:
        with closing(_server_state_conn()) as conn, conn:
            conn.execute("DELETE FROM auth_sessions WHERE token = ?", (token,))
    except sqlite3.DatabaseError as exc:
        _LOG.warning("auth_session_delete_failed", exc_info=exc)


def _delete_persistent_auth_sessions_for_user(username: str) -> None:
    if not username:
        return
    try:
        with closing(_server_state_conn()) as conn, conn:
            conn.execute("DELETE FROM auth_sessions WHERE username = ?", (username,))
    except sqlite3.DatabaseError as exc:
        _LOG.warning("auth_session_user_delete_failed", exc_info=exc)


def _persistent_session_counts(now: Optional[float] = None, *, exclude_tokens: Optional[set] = None) -> Dict[str, int]:
    now = time.time() if now is None else now
    excluded = exclude_tokens or set()
    try:
        with closing(_server_state_conn()) as conn, conn:
            conn.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (now,))
            rows = conn.execute(
                "SELECT token, username FROM auth_sessions"
            ).fetchall()
            counts: Dict[str, int] = {}
            for row in rows:
                token = str(row["token"] or "")
                if token in excluded:
                    continue
                username = str(row["username"] or "")
                if username:
                    counts[username] = counts.get(username, 0) + 1
            return counts
    except sqlite3.DatabaseError as exc:
        _LOG.warning("auth_session_count_failed", exc_info=exc)
        return {}


def _trust_proxy_headers() -> bool:
    return os.environ.get("MING_SIM_TRUST_PROXY_HEADERS", "").strip().lower() in ("1", "true", "yes")


def _remote_addr(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    if forwarded and _trust_proxy_headers():
        return forwarded[:80]
    return (request.client.host if request.client else "unknown")[:80]


def _login_rate_limit_config() -> Tuple[int, int]:
    attempts = _env_int("MING_SIM_LOGIN_RATE_LIMIT_ATTEMPTS", 8, minimum=1, maximum=1000)
    window = _env_int("MING_SIM_LOGIN_RATE_LIMIT_WINDOW_SECONDS", 300, minimum=30, maximum=86400)
    return attempts, window


def _login_rate_bucket(username: str, remote_addr: str) -> str:
    digest = hashlib.sha256(f"{username.lower()}|{remote_addr}".encode("utf-8")).hexdigest()
    return digest[:32]


def _login_rate_limited(username: str, request: Request) -> Tuple[bool, int]:
    max_attempts, window = _login_rate_limit_config()
    now = time.time()
    remote = _remote_addr(request)
    bucket = _login_rate_bucket(username, remote)
    try:
        with closing(_server_state_conn()) as conn, conn:
            row = conn.execute(
                "SELECT window_start, attempts FROM login_rate_limits WHERE bucket = ?",
                (bucket,),
            ).fetchone()
            if row is None:
                return False, 0
            window_start = float(row["window_start"] or now)
            attempts = int(row["attempts"] or 0)
            if now - window_start >= window:
                conn.execute("DELETE FROM login_rate_limits WHERE bucket = ?", (bucket,))
                return False, 0
            retry_after = max(1, int(window - (now - window_start)))
            return attempts >= max_attempts, retry_after
    except sqlite3.DatabaseError as exc:
        _LOG.warning("login_rate_limit_check_failed", exc_info=exc)
        return False, 0


def _record_login_failure(username: str, request: Request) -> None:
    _max_attempts, window = _login_rate_limit_config()
    now = time.time()
    remote = _remote_addr(request)
    bucket = _login_rate_bucket(username, remote)
    try:
        with closing(_server_state_conn()) as conn, conn:
            row = conn.execute(
                "SELECT window_start, attempts FROM login_rate_limits WHERE bucket = ?",
                (bucket,),
            ).fetchone()
            if row is None or now - float(row["window_start"] or 0) >= window:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO login_rate_limits(bucket, username, remote_addr, window_start, attempts)
                    VALUES (?, ?, ?, ?, 1)
                    """,
                    (bucket, username, remote, now),
                )
            else:
                conn.execute(
                    "UPDATE login_rate_limits SET attempts = attempts + 1 WHERE bucket = ?",
                    (bucket,),
                )
    except sqlite3.DatabaseError as exc:
        _LOG.warning("login_rate_limit_record_failed", exc_info=exc)


def _clear_login_failures(username: str, request: Request) -> None:
    bucket = _login_rate_bucket(username, _remote_addr(request))
    try:
        with closing(_server_state_conn()) as conn, conn:
            conn.execute("DELETE FROM login_rate_limits WHERE bucket = ?", (bucket,))
    except sqlite3.DatabaseError as exc:
        _LOG.warning("login_rate_limit_clear_failed", exc_info=exc)


def _new_auth_session(username: str) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    _cleanup_auth_sessions(now)
    record = {
        "username": username,
        "created_at": now,
        "last_seen": now,
        "expires_at": now + _session_ttl_seconds(),
    }
    with _auth_sessions_lock:
        _auth_sessions[token] = dict(record)
    _persist_auth_session(token, record)
    return token


def _session_record_username(record: Any) -> str:
    if isinstance(record, str):
        return record
    if isinstance(record, dict):
        return str(record.get("username") or "")
    return ""


def _cleanup_auth_sessions(now: Optional[float] = None) -> None:
    now = time.time() if now is None else now
    with _auth_sessions_lock:
        expired = [
            token
            for token, record in _auth_sessions.items()
            if isinstance(record, dict) and float(record.get("expires_at") or 0) <= now
        ]
        for token in expired:
            _auth_sessions.pop(token, None)
            _delete_persistent_auth_session(token)
    _cleanup_persistent_auth_sessions(now)


def _session_username(token: str) -> str:
    if not token:
        return ""
    now = time.time()
    with _auth_sessions_lock:
        record = _auth_sessions.get(token, "")
        if isinstance(record, dict) and float(record.get("expires_at") or 0) <= now:
            _auth_sessions.pop(token, None)
            _delete_persistent_auth_session(token)
            return ""
        username = _session_record_username(record)
        if isinstance(record, dict) and username:
            record["last_seen"] = now
            _persist_auth_session(token, record)
    if not username:
        record = _load_persistent_auth_session(token, now)
        username = _session_record_username(record)
        if username:
            with _auth_sessions_lock:
                _auth_sessions[token] = dict(record)
    if username and username in _configured_auth_users():
        return username
    return ""


def _is_admin_user(username: str) -> bool:
    if not _auth_enabled():
        return True
    return bool(username and username in _configured_admin_users())


def _require_server_admin() -> str:
    username = _current_game_username()
    if not _is_admin_user(username):
        raise HTTPException(status_code=403, detail={"code": "admin_required", "message": "需要管理员权限。"})
    return username


def _path_requires_auth(path: str) -> bool:
    if not _auth_enabled():
        return False
    if path.startswith("/api/auth"):
        return False
    return (
        path.startswith("/api/")
        or path.startswith("/portraits/generated/")
        or path.startswith("/portraits/custom/")
        or path == "/admin"
    )


def _auth_error_response() -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"detail": {"code": "auth_required", "message": "请先登录。"}},
    )


def _current_game_username() -> str:
    if not _auth_enabled():
        return ""
    username = _current_username.get()
    if not username:
        raise HTTPException(status_code=401, detail={"code": "auth_required", "message": "请先登录。"})
    return username


def _close_game(game: Optional[WebGame]) -> None:
    if game is None:
        return
    try:
        from ming_sim.scheduler import stop_worker
        stop_worker(game.db_path)
    except Exception:
        pass
    try:
        game.session.close()
    except Exception:
        pass


def _running_game_for_user(username: str) -> Optional[WebGame]:
    if username:
        with _web_games_lock:
            return _web_games.get(username)
    return web_game


def _set_running_game_for_user(username: str, game: Optional[WebGame]) -> None:
    global web_game
    if username:
        with _web_games_lock:
            old = _web_games.pop(username, None)
            if game is not None:
                _web_games[username] = game
        _close_game(old)
        return
    old = web_game
    web_game = game
    _close_game(old)


def _close_all_running_games() -> None:
    global web_game
    old_local = web_game
    web_game = None
    _close_game(old_local)
    with _web_games_lock:
        games = list(_web_games.values())
        _web_games.clear()
    for game in games:
        _close_game(game)


def _running_game_count() -> int:
    with _web_games_lock:
        return len(_web_games) + (1 if web_game is not None else 0)


def _ensure_game_capacity_for_user(username: str) -> None:
    current = _running_game_for_user(username)
    if current is not None:
        return
    if _running_game_count() >= _max_running_games():
        raise HTTPException(
            status_code=503,
            detail={
                "code": "server_capacity_full",
                "message": "服务器运行中对局已达上限，请稍后再试或联系管理员关闭空闲对局。",
            },
        )


def _try_acquire_turn_resolution_capacity() -> bool:
    global _active_turn_resolutions
    with _turn_resolution_capacity_lock:
        if _active_turn_resolutions >= _max_concurrent_turn_resolutions():
            return False
        _active_turn_resolutions += 1
        return True


def _release_turn_resolution_capacity() -> None:
    global _active_turn_resolutions
    with _turn_resolution_capacity_lock:
        _active_turn_resolutions = max(0, _active_turn_resolutions - 1)


def _active_turn_resolution_count() -> int:
    with _turn_resolution_capacity_lock:
        return int(_active_turn_resolutions)


def get_game() -> WebGame:
    """游戏路由统一入口。未开局 → 409 让前端跳回菜单页。"""
    username = _current_game_username()
    game = _running_game_for_user(username)
    if game is None:
        raise HTTPException(status_code=409, detail="尚未开局，请回菜单选择新游戏/继续/加载存档。")
    return game


def _json_logs_enabled() -> bool:
    return os.environ.get("MING_SIM_JSON_LOGS", "").strip().lower() in ("1", "true", "yes")


def _log_request(
    request: Request,
    *,
    request_id: str,
    status_code: int,
    elapsed_ms: float,
    username: str = "",
) -> None:
    payload = {
        "event": "http_request",
        "request_id": request_id,
        "method": request.method,
        "path": request.url.path,
        "status": status_code,
        "elapsed_ms": round(elapsed_ms, 2),
        "username": username or "anonymous",
        "remote_addr": _remote_addr(request),
    }
    if _json_logs_enabled():
        _LOG.info(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    else:
        _LOG.info(
            "%s %s status=%s elapsed_ms=%.2f user=%s request_id=%s",
            request.method,
            request.url.path,
            status_code,
            elapsed_ms,
            username or "anonymous",
            request_id,
        )


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    start = time.perf_counter()
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    username = ""
    if _auth_enabled():
        username = _session_username(request.cookies.get(_AUTH_COOKIE, ""))
        if _path_requires_auth(request.url.path) and request.method.upper() != "OPTIONS" and not username:
            response = _auth_error_response()
            response.headers["X-Request-ID"] = request_id
            _log_request(
                request,
                request_id=request_id,
                status_code=response.status_code,
                elapsed_ms=(time.perf_counter() - start) * 1000,
                username=username,
            )
            return response
    token = _current_username.set(username)
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        _log_request(
            request,
            request_id=request_id,
            status_code=response.status_code,
            elapsed_ms=(time.perf_counter() - start) * 1000,
            username=username,
        )
        return response
    except Exception:
        _LOG.exception(
            "http_request_failed method=%s path=%s user=%s request_id=%s",
            request.method,
            request.url.path,
            username or "anonymous",
            request_id,
        )
        raise
    finally:
        _current_username.reset(token)


def _health_payload() -> Dict[str, Any]:
    return {
        "status": "ok",
        "uptime_seconds": int(time.time() - _SERVER_STARTED_AT),
        "auth_enabled": _auth_enabled(),
        "running_games": _running_game_count(),
        "active_sessions": sum(_session_counts_by_user().values()),
        "active_turn_resolutions": _active_turn_resolution_count(),
        "limits": {
            "max_running_games": _max_running_games(),
            "max_concurrent_turns": _max_concurrent_turn_resolutions(),
        },
    }


def _server_state_ready() -> bool:
    try:
        with closing(_server_state_conn()) as conn, conn:
            conn.execute("SELECT 1").fetchone()
        return True
    except sqlite3.DatabaseError:
        return False


@app.get("/healthz")
async def healthz() -> Dict[str, Any]:
    return _health_payload()


@app.get("/readyz")
async def readyz() -> Response:
    checks = {
        "server_state_db": _server_state_ready(),
        "web_dist": os.path.isdir(WEB_DIST),
        "content_dir": os.path.isdir(bundled_path("content")),
    }
    payload = {**_health_payload(), "checks": checks}
    status = 200 if all(checks.values()) else 503
    if status != 200:
        payload["status"] = "degraded"
    return JSONResponse(payload, status_code=status)


@app.get("/api/auth/me")
async def api_auth_me() -> Dict[str, Any]:
    if not _auth_enabled():
        return {"auth_enabled": False, "authenticated": True, "username": "local", "is_admin": True}
    username = _current_username.get()
    return {"auth_enabled": True, "authenticated": bool(username), "username": username, "is_admin": _is_admin_user(username)}


@app.post("/api/auth/login")
async def api_auth_login(request: Request, body: LoginRequest) -> Response:
    if not _auth_enabled():
        return JSONResponse({"ok": True, "auth_enabled": False, "username": "local", "is_admin": True})
    username = _normalize_auth_username(body.username)
    limited, retry_after = _login_rate_limited(username, request)
    if limited:
        response = JSONResponse(
            status_code=429,
            content={"detail": {"code": "rate_limited", "message": "登录失败次数过多，请稍后再试。"}},
        )
        response.headers["Retry-After"] = str(retry_after)
        return response
    stored = _configured_auth_users().get(username, "")
    if not stored or not _verify_password(stored, body.password):
        _record_login_failure(username, request)
        raise HTTPException(status_code=401, detail={"code": "bad_credentials", "message": "用户名或密码不正确。"})
    _clear_login_failures(username, request)
    _mark_registered_user_login(username)
    token = _new_auth_session(username)
    response = JSONResponse({"ok": True, "auth_enabled": True, "username": username, "is_admin": _is_admin_user(username)})
    response.set_cookie(
        _AUTH_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=os.environ.get("MING_SIM_COOKIE_SECURE", "").strip().lower() in ("1", "true", "yes"),
        max_age=_session_ttl_seconds(),
        path="/",
    )
    return response


@app.post("/api/auth/register")
async def api_auth_register(request: Request, body: RegisterRequest) -> Response:
    if not _registration_enabled():
        raise HTTPException(status_code=403, detail={"code": "registration_disabled", "message": "注册暂未开放。"})
    expected_invite = _registration_invite_code()
    if not expected_invite or not secrets.compare_digest((body.invite_code or "").strip(), expected_invite):
        raise HTTPException(status_code=403, detail={"code": "bad_invite_code", "message": "邀请码不正确。"})

    username = _validate_register_username(body.username)
    password = _validate_register_password(body.password)
    if username in _configured_auth_users():
        raise HTTPException(status_code=409, detail={"code": "username_taken", "message": "这个用户名已被注册。"})

    limited, retry_after = _login_rate_limited(username, request)
    if limited:
        response = JSONResponse(
            status_code=429,
            content={"detail": {"code": "rate_limited", "message": "操作过于频繁，请稍后再试。"}},
        )
        response.headers["Retry-After"] = str(retry_after)
        return response

    _create_registered_user(username, password)
    _clear_login_failures(username, request)
    token = _new_auth_session(username)
    response = JSONResponse({"ok": True, "auth_enabled": True, "username": username, "is_admin": _is_admin_user(username)})
    response.set_cookie(
        _AUTH_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=os.environ.get("MING_SIM_COOKIE_SECURE", "").strip().lower() in ("1", "true", "yes"),
        max_age=_session_ttl_seconds(),
        path="/",
    )
    return response


@app.post("/api/auth/logout")
async def api_auth_logout(request: Request) -> Response:
    token = request.cookies.get(_AUTH_COOKIE, "")
    username = _session_username(token)
    if token:
        with _auth_sessions_lock:
            _auth_sessions.pop(token, None)
        _delete_persistent_auth_session(token)
    if username:
        _set_running_game_for_user(username, None)
    response = JSONResponse({"ok": True})
    response.delete_cookie(_AUTH_COOKIE, path="/")
    return response


# 自动存档文件名：auto_<campaign_id>_<year>_<period>_t<turn>_<tag>.db
_AUTO_SAVE_RE = re.compile(
    rf"^{re.escape(AUTO_SAVE_PREFIX)}(?P<cid>[0-9a-f]+)_"
    r"(?P<year>\d{4})_(?P<period>\d{2})_t(?P<turn>\d{4})_(?P<tag>\w+)$"
)

_AUTO_TAG_LABEL = {"begin": "月初", "preresolve": "结算前"}


def _parse_save_name(name: str) -> Dict[str, Any]:
    """把存档名解析成元信息。自动档归到对应 campaign，手动档 campaign_id 留空。"""
    m = _AUTO_SAVE_RE.match(name)
    if not m:
        return {"campaign_id": "", "kind": "manual", "label": name}
    year = int(m.group("year"))
    period = int(m.group("period"))
    turn = int(m.group("turn"))
    tag = m.group("tag")
    tag_label = _AUTO_TAG_LABEL.get(tag, tag)
    return {
        "campaign_id": m.group("cid"),
        "kind": "auto",
        "year": year,
        "period": period,
        "turn": turn,
        "tag": tag,
        "label": f"{year}年{period}月 · 第{turn}回合 · {tag_label}",
    }


def _main_db_campaign_id(db_path: str = "") -> str:
    db_path = db_path or _db_path_for_user(_current_username.get())
    if not os.path.isfile(db_path):
        return ""
    try:
        import sqlite3 as _sqlite3

        conn = _sqlite3.connect(db_path)
        try:
            row = conn.execute("SELECT value FROM kv_store WHERE key='campaign_id'").fetchone()
            return str(row[0]).strip() if row and row[0] else ""
        finally:
            conn.close()
    except Exception:
        return ""


def _scan_saves(saves_dir: str = "") -> List[Dict[str, Any]]:
    """扫存档目录，独立于 WebGame 实例（菜单页无 game 也要能列）。
    不再按 campaign 过滤——所有局的存档都列出，由前端按局分组。"""
    saves_dir = saves_dir or _saves_dir_for_user(_current_username.get())
    out: List[Dict[str, Any]] = []
    if not os.path.isdir(saves_dir):
        return out
    for fname in sorted(os.listdir(saves_dir)):
        if not fname.endswith(".db"):
            continue
        name = fname[:-3]
        full = os.path.join(saves_dir, fname)
        try:
            st = os.stat(full)
        except OSError:
            continue
        meta = _parse_save_name(name)
        out.append({
            "name": name,
            "size": st.st_size,
            "mtime": int(st.st_mtime),
            **meta,
        })
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out


def _scan_campaigns(saves_dir: str = "", db_path: str = "") -> List[Dict[str, Any]]:
    """把存档按局（campaign_id）分组，当前主 DB 的局标 current=True。
    手动存档（无 campaign_id）归到一个 manual 组。每组按 mtime 倒序，组也按最新档倒序。"""
    saves = _scan_saves(saves_dir)
    cur_campaign = _main_db_campaign_id(db_path)
    groups: Dict[str, Dict[str, Any]] = {}
    for s in saves:
        cid = s.get("campaign_id") or ""
        key = cid or "__manual__"
        group = groups.get(key)
        if group is None:
            group = {
                "campaign_id": cid,
                "kind": "manual" if not cid else "auto",
                "current": bool(cid) and cid == cur_campaign,
                "saves": [],
                "latest_mtime": 0,
            }
            groups[key] = group
        group["saves"].append(s)
        group["latest_mtime"] = max(group["latest_mtime"], s["mtime"])
    out = list(groups.values())
    # 当前局置顶，其余按最新档时间倒序；手动组排最后。
    out.sort(key=lambda g: (
        0 if g["current"] else (2 if g["kind"] == "manual" else 1),
        -g["latest_mtime"],
    ))
    return out


def _has_main_db(db_path: str = "") -> bool:
    """主 DB 文件是否存在 → 决定「继续」按钮可不可点。"""
    db_path = db_path or _db_path_for_user(_current_username.get())
    return os.path.isfile(db_path)


def _session_counts_by_user() -> Dict[str, int]:
    _cleanup_auth_sessions()
    with _auth_sessions_lock:
        memory_items = list(_auth_sessions.items())
    counts: Dict[str, int] = _persistent_session_counts(
        exclude_tokens={token for token, _record in memory_items}
    )
    for _token, record in memory_items:
        username = _session_record_username(record)
        if not username:
            continue
        counts[username] = counts.get(username, 0) + 1
    return counts


def _sqlite_game_summary(db_path: str) -> Dict[str, Any]:
    summary = {"campaign_id": "", "year": 0, "period": 0, "turn": 0}
    if not os.path.isfile(db_path):
        return summary
    try:
        import sqlite3 as _sqlite3

        conn = _sqlite3.connect(db_path)
        try:
            row = conn.execute("SELECT value FROM kv_store WHERE key='campaign_id'").fetchone()
            if row and row[0]:
                summary["campaign_id"] = str(row[0]).strip()
            state = conn.execute("SELECT year, period, turn FROM game_state LIMIT 1").fetchone()
            if state:
                summary["year"] = int(state[0] or 0)
                summary["period"] = int(state[1] or 0)
                summary["turn"] = int(state[2] or 0)
        finally:
            conn.close()
    except Exception:
        pass
    return summary


def _file_stat(path: str) -> Dict[str, int]:
    try:
        st = os.stat(path)
    except OSError:
        return {"size": 0, "mtime": 0}
    return {"size": int(st.st_size), "mtime": int(st.st_mtime)}


def _configured_usernames() -> List[str]:
    users = sorted(_configured_auth_users().keys())
    return users or ["local"]


def _server_admin_user_card(username: str, session_counts: Dict[str, int]) -> Dict[str, Any]:
    auth_username = "" if username == "local" and not _auth_enabled() else username
    db_path = _db_path_for_user(auth_username)
    saves_dir = _saves_dir_for_user(auth_username)
    saves = _scan_saves(saves_dir)
    saves_size = sum(int(item.get("size") or 0) for item in saves)
    running = _running_game_for_user(auth_username)
    db_summary = _sqlite_game_summary(db_path)
    if running is not None:
        try:
            db_summary.update({
                "campaign_id": running.session.campaign_id,
                "year": int(running.state.year),
                "period": int(running.state.period),
                "turn": int(running.state.turn),
            })
        except Exception:
            pass
    db_stat = _file_stat(db_path)
    return {
        "username": username,
        "is_admin": _is_admin_user(username),
        "user_id": _safe_user_id(username) if username != "local" else "local",
        "running": running is not None,
        "sessions": int(session_counts.get(username, 0)),
        "has_main_db": os.path.isfile(db_path),
        "db_path": db_path,
        "db_size": db_stat["size"],
        "db_mtime": db_stat["mtime"],
        "saves_dir": saves_dir,
        "saves_count": len(saves),
        "saves_size": saves_size,
        "latest_save_mtime": max([int(item.get("mtime") or 0) for item in saves] or [0]),
        "current_campaign": db_summary.get("campaign_id", ""),
        "year": int(db_summary.get("year") or 0),
        "period": int(db_summary.get("period") or 0),
        "turn": int(db_summary.get("turn") or 0),
        "recent_saves": saves[:5],
    }


def _server_admin_overview_payload() -> Dict[str, Any]:
    runtime = load_runtime_llm()
    session_counts = _session_counts_by_user()
    users = [_server_admin_user_card(username, session_counts) for username in _configured_usernames()]
    return {
        "auth_enabled": _auth_enabled(),
        "admin_users": sorted(_configured_admin_users()) if _auth_enabled() else ["local"],
        "uptime_seconds": int(time.time() - _SERVER_STARTED_AT),
        "running_games": sum(1 for item in users if item["running"]),
        "active_sessions": sum(int(item["sessions"]) for item in users),
        "active_turn_resolutions": _active_turn_resolution_count(),
        "limits": {
            "max_running_games": _max_running_games(),
            "max_concurrent_turns": _max_concurrent_turn_resolutions(),
        },
        "llm": {
            "base_url": runtime.get("base_url") or os.environ.get("OPENAI_BASE_URL", ""),
            "model": runtime.get("model") or os.environ.get("OPENAI_MODEL", ""),
            "has_api_key": bool(runtime.get("api_key") or os.environ.get("OPENAI_API_KEY")),
            "advanced_model": runtime.get("advanced_model") or os.environ.get("OPENAI_ADVANCED_MODEL", ""),
            "has_advanced_api_key": bool(runtime.get("advanced_api_key") or os.environ.get("OPENAI_ADVANCED_API_KEY")),
            "client_configurable": (
                not _auth_enabled()
                or os.environ.get("MING_SIM_ALLOW_CLIENT_LLM_CONFIG", "").strip().lower() in ("1", "true", "yes")
            ),
        },
        "users": users,
    }


@app.get("/api/server_admin/overview")
async def api_server_admin_overview() -> Dict[str, Any]:
    _require_server_admin()
    return _server_admin_overview_payload()


@app.post("/api/server_admin/users/{username}/close_game")
async def api_server_admin_close_game(username: str) -> Dict[str, Any]:
    _require_server_admin()
    if username not in _configured_usernames():
        raise HTTPException(status_code=404, detail={"code": "user_not_found", "message": f"未找到用户：{username}"})
    auth_username = "" if username == "local" and not _auth_enabled() else username
    _set_running_game_for_user(auth_username, None)
    return {"ok": True, "overview": _server_admin_overview_payload()}


@app.post("/api/server_admin/users/{username}/logout")
async def api_server_admin_logout_user(username: str) -> Dict[str, Any]:
    _require_server_admin()
    if username not in _configured_usernames():
        raise HTTPException(status_code=404, detail={"code": "user_not_found", "message": f"未找到用户：{username}"})
    with _auth_sessions_lock:
        stale = [token for token, owner in _auth_sessions.items() if _session_record_username(owner) == username]
        for token in stale:
            _auth_sessions.pop(token, None)
            _delete_persistent_auth_session(token)
    _delete_persistent_auth_sessions_for_user(username)
    auth_username = "" if username == "local" and not _auth_enabled() else username
    _set_running_game_for_user(auth_username, None)
    return {"ok": True, "logged_out": username, "overview": _server_admin_overview_payload()}


@app.delete("/api/server_admin/users/{username}/main_db")
async def api_server_admin_delete_main_db(username: str, body: ServerAdminActionRequest = ServerAdminActionRequest()) -> Dict[str, Any]:
    _require_server_admin()
    if username not in _configured_usernames():
        raise HTTPException(status_code=404, detail={"code": "user_not_found", "message": f"未找到用户：{username}"})
    if body.confirm not in (username, "DELETE"):
        raise HTTPException(status_code=400, detail={"code": "confirm_required", "message": f"请输入 {username} 或 DELETE 确认删除主进度。"})
    auth_username = "" if username == "local" and not _auth_enabled() else username
    _set_running_game_for_user(auth_username, None)
    # _close_game 已停该用户 worker；此处兜底再停一次（幂等），确保 Windows 句柄释放
    try:
        from ming_sim.scheduler import stop_worker
        stop_worker(_db_path_for_user(auth_username))
    except Exception:
        pass
    _delete_sqlite_db_files_or_raise(_db_path_for_user(auth_username))
    return {"ok": True, "deleted": username, "overview": _server_admin_overview_payload()}


@app.get("/api/menu/status")
async def api_menu_status() -> Dict[str, Any]:
    """菜单页状态：API key 是否配好、上次主 DB 是否存在、存档列表。"""
    username = _current_game_username()
    db_path = _db_path_for_user(username)
    saves_dir = _saves_dir_for_user(username)
    runtime = load_runtime_llm()
    has_api_key = bool(runtime.get("api_key") or os.environ.get("OPENAI_API_KEY"))
    llm_client_configurable = (
        not _auth_enabled()
        or os.environ.get("MING_SIM_ALLOW_CLIENT_LLM_CONFIG", "").strip().lower() in ("1", "true", "yes")
    )
    return {
        "has_api_key": has_api_key,
        "has_running_game": _running_game_for_user(username) is not None,
        "has_main_db": _has_main_db(db_path),
        "saves": _scan_saves(saves_dir),
        "campaigns": _scan_campaigns(saves_dir, db_path),
        "current_campaign": _main_db_campaign_id(db_path),
        "auth": {
            "enabled": _auth_enabled(),
            "username": username or "local",
            "is_admin": _is_admin_user(username or "local"),
        },
        "llm_client_configurable": llm_client_configurable,
        "llm": {
            "base_url": runtime.get("base_url") or os.environ.get("OPENAI_BASE_URL", ""),
            "model": runtime.get("model") or os.environ.get("OPENAI_MODEL", ""),
            "has_api_key": has_api_key,
            "max_tokens": int(runtime.get("max_tokens") or 8000),
            "timeout_seconds": float(runtime.get("timeout_seconds") or os.environ.get("OPENAI_TIMEOUT_SECONDS", "180") or 180),
            "thinking_level": runtime.get("thinking_level") or os.environ.get("OPENAI_THINKING_LEVEL", ""),
            "advanced_model": runtime.get("advanced_model") or os.environ.get("OPENAI_ADVANCED_MODEL", ""),
            "advanced_base_url": runtime.get("advanced_base_url") or os.environ.get("OPENAI_ADVANCED_BASE_URL", ""),
            "has_advanced_api_key": bool(runtime.get("advanced_api_key") or os.environ.get("OPENAI_ADVANCED_API_KEY")),
            "advanced_thinking_level": runtime.get("advanced_thinking_level") or os.environ.get("OPENAI_ADVANCED_THINKING_LEVEL", ""),
        },
    }


@app.post("/api/menu/new_game")
async def api_menu_new_game() -> Dict[str, Any]:
    """开始新游戏：清主 DB → 新建 WebGame。"""
    username = _current_game_username()
    _set_running_game_for_user(username, None)
    _ensure_game_capacity_for_user(username)
    try:
        game = WebGame(fresh=True, username=username)
    except LLMUnavailable as exc:
        raise HTTPException(status_code=412, detail=_llm_error_detail(exc))
    _set_running_game_for_user(username, game)
    return _web_payload_response(game, "/api/menu/new_game", {"state": game.state_payload()})


@app.post("/api/menu/continue")
async def api_menu_continue() -> Dict[str, Any]:
    """继续：用上次主 DB 启动 WebGame。"""
    username = _current_game_username()
    if not _has_main_db(_db_path_for_user(username)):
        raise HTTPException(status_code=404, detail="无上次进度可继续，请先新游戏或加载存档。")
    _set_running_game_for_user(username, None)
    _ensure_game_capacity_for_user(username)
    try:
        game = WebGame(fresh=False, username=username)
    except LLMUnavailable as exc:
        raise HTTPException(status_code=412, detail=_llm_error_detail(exc))
    _set_running_game_for_user(username, game)
    return _web_payload_response(game, "/api/menu/continue", {"state": game.state_payload()})


@app.post("/api/menu/load_save/{name}")
async def api_menu_load_save(name: str) -> Dict[str, Any]:
    """从存档启动：先启动空 WebGame（fresh）→ 调 load_save 热替换主 DB。"""
    username = _current_game_username()
    _set_running_game_for_user(username, None)
    _ensure_game_capacity_for_user(username)
    try:
        game = WebGame(fresh=False, username=username)  # 先有 session 才能 load_save
    except LLMUnavailable as exc:
        raise HTTPException(status_code=412, detail=_llm_error_detail(exc))
    game.load_save(name)
    _set_running_game_for_user(username, game)
    return _web_payload_response(game, "/api/menu/load_save/{name}", {"state": game.state_payload()})


@app.delete("/api/menu/saves/{name}")
async def api_menu_delete_save(name: str) -> Dict[str, Any]:
    """菜单页删存档：不依赖 WebGame 实例，直接删文件系统里的 <name>.db。
    与 WebGame.delete_save 同名校验，返回刷新后的 campaigns。"""
    cleaned = "".join(c for c in name.strip() if c.isalnum() or c in "._-")
    if not cleaned or cleaned.startswith("."):
        raise HTTPException(status_code=400, detail="存档名非法。仅允许字母/数字/._- ")
    username = _current_game_username()
    saves_dir = _saves_dir_for_user(username)
    target = os.path.join(saves_dir, f"{cleaned}.db")
    if not os.path.isfile(target):
        raise HTTPException(status_code=404, detail="存档不存在。")
    os.remove(target)
    return {
        "saves": _scan_saves(saves_dir),
        "campaigns": _scan_campaigns(saves_dir, _db_path_for_user(username)),
    }


@app.post("/api/menu/exit_to_menu")
async def api_menu_exit() -> Dict[str, Any]:
    """退回菜单：关 session 但不删 DB。"""
    _set_running_game_for_user(_current_game_username(), None)
    return {"ok": True}


@app.post("/api/menu/shutdown")
async def api_menu_shutdown() -> Dict[str, Any]:
    """退出整个游戏：关 session + 终止服务进程。前端收响应后自行关页面。"""
    import os as _os
    import signal as _signal
    import threading as _threading
    _close_all_running_games()
    # 先返回响应，再异步终止进程。SIGTERM 在 *nix 走优雅退出；
    # Windows 无完整 SIGTERM 语义（pywebview 主线程也不收信号），直接 os._exit 兜底。
    def _kill_later() -> None:
        import sys as _sys
        import time as _time
        _time.sleep(0.3)
        if _sys.platform == "win32":
            _os._exit(0)
        else:
            _os.kill(_os.getpid(), _signal.SIGTERM)
    _threading.Thread(target=_kill_later, daemon=True).start()
    return {"ok": True}


class LlmSetupRequest(BaseModel):
    base_url: str
    model: str
    api_key: str
    max_tokens: int = 8000
    timeout_seconds: float = 180
    thinking_level: str = ""
    advanced_model: str = ""
    advanced_base_url: str = ""
    advanced_api_key: str = ""
    advanced_thinking_level: str = ""


@app.post("/api/menu/llm")
async def api_menu_save_llm(request: LlmSetupRequest) -> Dict[str, Any]:
    """菜单页保存 LLM 配置：先发起轻量聊天校验，通过后才落盘。"""
    if _auth_enabled() and os.environ.get("MING_SIM_ALLOW_CLIENT_LLM_CONFIG", "").strip().lower() not in ("1", "true", "yes"):
        raise HTTPException(
            status_code=403,
            detail={"code": "server_managed_llm", "message": "服务器模式下 API 配置由服务端统一管理。"},
        )
    base_url = (request.base_url or "").strip()
    model = (request.model or "").strip()
    api_key = (request.api_key or "").strip()
    advanced_model = (request.advanced_model or "").strip()
    adv_base_in = (request.advanced_base_url or "").strip()
    advanced_base_url = normalize_openai_base_url(adv_base_in) if adv_base_in else ""
    advanced_api_key = (request.advanced_api_key or "").strip()
    max_tokens = request.max_tokens if request.max_tokens > 0 else 8000
    timeout_seconds = request.timeout_seconds if request.timeout_seconds > 0 else 180
    thinking_level = normalize_thinking_level(request.thinking_level)
    advanced_thinking_level = normalize_thinking_level(request.advanced_thinking_level)
    if not (base_url and model):
        raise HTTPException(status_code=400, detail="base_url / model 不能为空。")
    if not api_key:
        existing = load_runtime_llm()
        api_key = existing.get("api_key") or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=400, detail="api_key 未配置，请填写。")
    # advanced_api_key 留空：复用已存的（避免覆盖成空）。
    if advanced_model and not advanced_api_key:
        existing = load_runtime_llm()
        advanced_api_key = existing.get("advanced_api_key") or os.environ.get("OPENAI_ADVANCED_API_KEY", "")
    normalized_base_url = normalize_openai_base_url(base_url)
    config = LLMConfig(
        api_key=api_key,
        base_url=normalized_base_url,
        model=model,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        thinking_level=thinking_level,
        advanced_model=advanced_model,
        advanced_base_url=advanced_base_url,
        advanced_api_key=advanced_api_key,
        advanced_thinking_level=advanced_thinking_level,
    )
    try:
        _verify_llm_configs_or_raise(config)
    except HTTPException:
        raise
    except LLMUnavailable as exc:
        raise HTTPException(status_code=400, detail=_llm_error_detail(exc)) from None
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail={"code": "llm_validation_failed", "message": str(exc)}) from None
    save_runtime_llm(
        normalized_base_url,
        model,
        api_key,
        max_tokens,
        timeout_seconds,
        thinking_level,
        advanced_model,
        advanced_base_url,
        advanced_api_key,
        advanced_thinking_level,
    )
    return {
        "ok": True,
        "llm": {
            "base_url": normalized_base_url,
            "model": model,
            "has_api_key": True,
            "max_tokens": max_tokens,
            "timeout_seconds": timeout_seconds,
            "thinking_level": thinking_level,
            "advanced_model": advanced_model,
            "advanced_base_url": advanced_base_url,
            "has_advanced_api_key": bool(advanced_api_key),
            "advanced_thinking_level": advanced_thinking_level,
        },
    }
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/game/state")
async def api_state() -> Dict[str, Any]:
    return get_game().state_payload()


# ── 半即时时间引擎（升级总案 S1/S10/S11）────────────────────────────────────

@app.get("/api/time")
async def api_time_status() -> Dict[str, Any]:
    from ming_sim import timeflow
    game = get_game()
    status = timeflow.time_status(game.db, game.state)
    return _plain_payload({"time": status})


@app.post("/api/time/advance")
async def api_time_advance(body: TimeAdvanceRequest) -> Dict[str, Any]:
    """推进 1-30 天（规则层日 tick）。月末/红事件必停，黄事件按 stop_on_yellow。"""
    from ming_sim import timeflow
    game = get_game()
    if game.state.ended:
        raise HTTPException(status_code=409, detail="本局已终，时间不再流动。")
    days = max(1, min(30, int(body.days)))
    with _settlement_guard(game):
        result = timeflow.advance_days(game.db, game.state, days, stop_on_yellow=body.stop_on_yellow)
        # 即时复命：把 worker 已产出的到期诏书结果落库（数值生效 + 判结局）。
        try:
            result["outcomes_applied"] = game.session.drain_pending_outcomes()
        except Exception as exc:
            _LOG.warning("drain_pending_outcomes 失败：%s", exc)
    return _response_with_state(game, result)


@app.post("/api/time/speed")
async def api_time_speed(body: TimeSpeedRequest) -> Dict[str, Any]:
    from ming_sim import timeflow
    game = get_game()
    speed = timeflow.set_speed(game.db, body.speed)
    return {"time_speed": speed}


@app.get("/api/directives/lifecycle")
async def api_directive_lifecycle() -> Dict[str, Any]:
    """指令生命周期面板（S2）。integrity 只暴露奏报口径（账实分离见 S3）。"""
    from ming_sim.lifecycle import lifecycle_payload
    game = get_game()
    return _plain_payload({"directives": lifecycle_payload(game.db)})


@app.post("/api/directives/{directive_id}/intervene")
async def api_directive_intervene(directive_id: int, body: DirectiveInterveneRequest) -> Dict[str, Any]:
    """执行中旨意的中途干预：催办/换人/加拨/独断/阻力处置/收回成命。"""
    from ming_sim import timeflow
    from ming_sim.lifecycle import intervene, lifecycle_payload
    game = get_game()
    with _settlement_guard(game):
        day = timeflow.ensure_active(game.db, game.state)
        result = intervene(game.db, game.state, directive_id, body.action,
                           day=day, new_assignee=body.new_assignee, fund=body.fund)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=str(result.get("message")))
    return _response_with_state(game, {
        "message": result["message"],
        "effects": result.get("effects") or [],
        "directives": lifecycle_payload(game.db),
    })


@app.get("/api/desk")
async def api_desk() -> Dict[str, Any]:
    """御案（S4）：待批奏疏、票拟、注意力余量、势/任事意愿（崇祯陷阱双仪表）。"""
    from ming_sim import timeflow
    from ming_sim.memorials import desk_payload
    game = get_game()
    day = timeflow.ensure_active(game.db, game.state)
    return _plain_payload(desk_payload(game.db, game.state, day))


@app.post("/api/desk/{memorial_id}/decide")
async def api_desk_decide(memorial_id: int, body: MemorialDecideRequest) -> Dict[str, Any]:
    """批红：照准/驳/留中/发部议。消耗当日注意力。"""
    from ming_sim import timeflow
    from ming_sim.memorials import decide_memorial, desk_payload
    game = get_game()
    with _settlement_guard(game):
        day = timeflow.ensure_active(game.db, game.state)
        result = decide_memorial(game.db, game.state, memorial_id, body.action,
                                 day=day, note=body.note)
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=str(result.get("message")))
        message = str(result["message"])
        # 起复荐疏照准 → 从 NPC 数据基座完成入朝注册（写操作，须同在锁内）
        followup = result.get("followup") or {}
        if isinstance(followup, dict) and followup.get("kind") == "recruit_foundation":
            try:
                recruited = game.recruit_from_foundation(str(followup.get("name")))
                message = message + " " + str(recruited.get("message") or "")
            except HTTPException as exc:
                message = message + f"（入朝注册失败：{exc.detail}）"
    return _response_with_state(game, {
        "message": message,
        "desk": desk_payload(game.db, game.state, day),
    })


@app.post("/api/court/punish")
async def api_court_punish(body: PunishRequest) -> Dict[str, Any]:
    """问罪官员（S5 立威杠杆）：势+而任事意愿-。public=False 走密旨，有泄露风险。"""
    from ming_sim import timeflow
    from ming_sim.memorials import punish_official
    game = get_game()
    with _settlement_guard(game):
        day = timeflow.ensure_active(game.db, game.state)
        result = punish_official(game.db, game.state, body.name, body.severity,
                                 day=day, public=body.public, reason=body.reason)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=str(result.get("message")))
    return _response_with_state(game, result)


@app.post("/api/court/back")
async def api_court_back(body: BackRequest) -> Dict[str, Any]:
    """为失败的忠臣买单（S5 破陷阱杠杆）：担责/抚恤/复用，任事意愿回暖而势受损。"""
    from ming_sim import timeflow
    from ming_sim.memorials import back_official
    game = get_game()
    with _settlement_guard(game):
        day = timeflow.ensure_active(game.db, game.state)
        result = back_official(game.db, game.state, body.name, body.kind, day=day, cost=body.cost)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=str(result.get("message")))
    return _response_with_state(game, result)


@app.get("/api/thresholds")
async def api_thresholds() -> Dict[str, Any]:
    """硬阈值预警仪表（S10）。注意：读的是呈报口径，账被做假时仪表同样会骗你。"""
    from ming_sim import timeflow
    from ming_sim.thresholds import threshold_dashboard
    game = get_game()
    day = timeflow.ensure_active(game.db, game.state)
    board = threshold_dashboard(game.db, game.state, day)
    return _plain_payload({"board": board, "day": day})


@app.post("/api/court/signal")
async def api_court_signal(body: SignalRequest) -> Dict[str, Any]:
    """信号类指令（S6 朝堂剧场）：廷杖/罪己诏/献俘——不改钱粮，只改全体观众的信念。"""
    from ming_sim import timeflow
    from ming_sim.theater import signal_action
    game = get_game()
    with _settlement_guard(game):
        day = timeflow.ensure_active(game.db, game.state)
        result = signal_action(game.db, game.state, body.kind, day=day, target=body.target)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=str(result.get("message")))
    return _response_with_state(game, result)


@app.get("/api/court/leverage")
async def api_court_leverage(name: str = "") -> Dict[str, Any]:
    """问罪抓手（S8）：官员失诺自动生成的可问罪依据清单。"""
    from ming_sim.theater import leverage_payload
    game = get_game()
    return _plain_payload({"items": leverage_payload(game.db, game.state, name)})


@app.get("/api/foundation/candidates")
async def api_foundation_candidates(keyword: str = "", limit: int = 20) -> Dict[str, Any]:
    """国朝人事档案·赋闲人才池（NPC 数据基座）：在野真才，可征辟起复。"""
    from ming_sim.foundation import available, candidates
    game = get_game()
    if not available():
        return _plain_payload({"available": False, "candidates": []})
    in_court = {str(r["name"]) for r in game.db.conn.execute("SELECT name FROM characters")}
    return _plain_payload({
        "available": True,
        "candidates": candidates(in_court, limit=max(1, min(60, limit)), keyword=keyword),
    })


@app.post("/api/foundation/recruit")
async def api_foundation_recruit(body: CastrateRequest) -> Dict[str, Any]:
    """征辟：直接从人才池征人入朝（耗当日注意力 2 点——亲自下征辟诏不是小事）。"""
    from ming_sim import timeflow
    from ming_sim.memorials import consume_attention
    game = get_game()
    with _settlement_guard(game):
        timeflow.ensure_active(game.db, game.state)
        if not consume_attention(game.db, 2):
            raise HTTPException(status_code=400, detail="今日精力已竭，征辟之诏明日再下。")
        result = game.recruit_from_foundation(body.name)
        game.db.record_log(game.state, f"【征辟】{result.get('message')}")
    return _response_with_state(game, result)


@app.post("/api/veil/investigate")
async def api_veil_investigate(body: InvestigateRequest) -> Dict[str, Any]:
    """密查（S9）：厂卫快而准但累积政治成本且可能被反侦；科道慢且带派系滤镜。"""
    from ming_sim import timeflow
    from ming_sim.veil import start_investigation
    game = get_game()
    with _settlement_guard(game):
        day = timeflow.ensure_active(game.db, game.state)
        result = start_investigation(game.db, game.state, line=body.line,
                                     target_kind=body.target_kind, target_id=body.target_id,
                                     day=day)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=str(result.get("message")))
    return _response_with_state(game, result)


@app.get("/api/veil/contradictions")
async def api_veil_contradictions() -> Dict[str, Any]:
    """文书互证线索：未揭穿的呈报存疑条目（只给疑点，不给真值——审计即侦探）。"""
    from ming_sim.veil import ledger_contradictions
    game = get_game()
    items = ledger_contradictions(game.db)
    # 只暴露账面值与出处，actual 列绝不出 API
    for item in items:
        item.pop("actual_value", None)
    return _plain_payload({"items": items})


@app.get("/api/shibi")
async def api_shibi() -> Dict[str, Any]:
    """史笔系统（S11）：季度史评与（终局后）立传。"""
    from ming_sim.shibi import get_biography, list_quarterly_reviews
    game = get_game()
    return _plain_payload({
        "reviews": list_quarterly_reviews(game.db),
        "biography": get_biography(game.db),
    })


@app.get("/api/zhongxing")
async def api_zhongxing() -> Dict[str, Any]:
    """中兴指数（趋势仪表，非胜利条件）+ 当前阶段诏题（S12）。"""
    from ming_sim.zhongxing import compute_zhongxing, stage_payload, zhongxing_history
    game = get_game()
    return _plain_payload({
        "current": compute_zhongxing(game.db, game.state),
        "history": zhongxing_history(game.db)[-36:],
        **stage_payload(game.db, game.state),
    })


@app.get("/api/playstyle/brief")
async def api_playstyle_brief(limit: int = 5, kind: str = "") -> Dict[str, Any]:
    """朝局风向：把私心、党争、军镇、把柄转为首页可行动玩法钩子。"""
    from ming_sim.playstyle import briefing_payload
    game = get_game()
    return _plain_payload(briefing_payload(game.db, game.state, limit=limit, kind=kind))


@app.get("/api/fiscal_center")
async def api_fiscal_center() -> Dict[str, Any]:
    """财政中枢：税源、支出、欠饷、流水与国策修正的一体化账簿。"""
    from ming_sim.fiscal_center import fiscal_center_payload
    game = get_game()
    return _web_payload_response(game, "/api/fiscal_center", fiscal_center_payload(game.db, game.state))


@app.get("/api/policy_center")
async def api_policy_center() -> Dict[str, Any]:
    """国策中枢：基本国策、争议路线、约束面板与在办证据。"""
    from ming_sim.fiscal_center import fiscal_center_payload
    from ming_sim.policy_center import policy_center_payload
    game = get_game()
    fiscal = fiscal_center_payload(game.db, game.state)
    return _web_payload_response(
        game,
        "/api/policy_center",
        policy_center_payload(game.db, game.state, fiscal=fiscal),
    )


@app.get("/api/statecraft_center")
async def api_statecraft_center() -> Dict[str, Any]:
    """国家机器中枢：以库存、月流、产能和瓶颈统一经济与官僚组织。"""
    from ming_sim.fiscal_center import fiscal_center_payload
    from ming_sim.statecraft_center import statecraft_center_payload

    game = get_game()
    fiscal = fiscal_center_payload(game.db, game.state)
    organization = organization_diagnostics(game.db, game._custom_institutions())
    return _web_payload_response(
        game,
        "/api/statecraft_center",
        statecraft_center_payload(game.db, game.state, fiscal=fiscal, organization=organization),
    )


@app.get("/api/audience/summon_hints")
async def api_audience_summon_hints() -> Dict[str, Any]:
    """召见抽屉的轻量人情提示：只在玩家点开传召名单时加载，不塞入主状态包。"""
    from ming_sim.playstyle import audience_summon_hints_payload

    game = get_game()
    return _plain_payload(audience_summon_hints_payload(game.db, game.state))


@app.get("/api/beliefs")
async def api_beliefs() -> Dict[str, Any]:
    """信念变量（势/任事意愿）当前值与变动轨迹（S5/S6 趋势线数据源）。"""
    from ming_sim.upgrade_schema import (
        KV_RISK_AVERSION, KV_SHI, RISK_AVERSION_DEFAULT, SHI_DEFAULT, kv_int,
    )
    game = get_game()
    rows = game.db.conn.execute(
        "SELECT day, key, old_value, new_value, reason FROM belief_logs ORDER BY id DESC LIMIT 120"
    ).fetchall()
    return _plain_payload({
        "shi": kv_int(game.db, KV_SHI, SHI_DEFAULT),
        "renshi": 100 - kv_int(game.db, KV_RISK_AVERSION, RISK_AVERSION_DEFAULT),
        "changes": [
            {"day": int(r["day"]), "key": str(r["key"]),
             "from": int(r["old_value"]), "to": int(r["new_value"]),
             "reason": str(r["reason"])}
            for r in reversed(rows)
        ],
    })


@app.get("/api/monthly_followups")
async def api_monthly_followups() -> Dict[str, Any]:
    game = get_game()
    return _web_payload_response(
        game,
        "/api/monthly_followups",
        monthly_followups_payload(
            int(game.state.turn),
            getattr(game.session, "monthly_followups", []) or [],
        ),
    )


def _plain_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """升级总案半即时层路由的直通响应：这些路由已在 EXCLUDED_WEB_PAYLOAD_ROUTES
    声明边界，不走通用 game-data hook（要扩展时先声明半即时层专用 hook）。"""
    return payload


@contextlib.contextmanager
def _settlement_guard(game: "WebGame"):
    """半即时层写路由的结算互斥：流式颁诏（resolve_turn）在 worker 线程上
    持 turn_resolution_lock 跑数分钟，期间共享同一条 sqlite 连接与 GameState——
    任何并发写（推进时间/批红/干预/问责）都会造成旬税赋重复落账、metrics 丢更新。
    写操作全程持锁；结算进行中即 409，与 /api/decree/issue 同语义。"""
    lock = getattr(game, "turn_resolution_lock", None)
    if lock is None:
        yield
        return
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail={
            "code": "turn_resolution_in_progress",
            "message": "月末结算正在进行，朝务暂停；待邸报出来再处置。",
        })
    try:
        yield
    finally:
        lock.release()


def _guarded(handler):
    """把一个 async 写路由放进 _settlement_guard：结算进行中即 409。
    用 functools.wraps 保留原签名（inspect.signature 跟随 __wrapped__），
    FastAPI 仍能解析 body 形参。用于宫斗/边事/内廷/诏旨/后宫等会改 state+落库的路由——
    这些路由此前漏挂结算锁，与 /api/time/advance 的 drain 并发会丢更新/重复落账。"""
    import functools as _functools

    @_functools.wraps(handler)
    async def _wrapper(*args, **kwargs):
        game = get_game()
        with _settlement_guard(game):
            return await handler(*args, **kwargs)

    return _wrapper


def _response_with_state(game: WebGame, payload: Dict[str, Any], *, route: str = "", method: str = "") -> Dict[str, Any]:
    out = attach_state_payload(payload, game.state_payload())
    if route:
        return _web_payload_response(game, route, out, method=method)
    return out


def _web_payload_response(game: Any, route: str, payload: Dict[str, Any], *, method: str = "") -> Dict[str, Any]:
    responder = getattr(game, "web_payload_response", None)
    if callable(responder):
        return responder(route, payload, method=method)
    return payload


@app.get("/api/organizations")
async def api_organizations() -> Dict[str, Any]:
    game = get_game()
    return _web_payload_response(
        game,
        "/api/organizations",
        compact_organization_payload(game.organization_payload()),
    )


@app.post("/api/organizations/custom")
async def api_add_custom_institution(body: CustomInstitutionRequest) -> Dict[str, Any]:
    game = get_game()
    item = game.add_custom_institution(body.name, body.category, body.mandate, body.slots)
    return _response_with_state(game, {
        "message": f"已增设{item['name']}，空缺已进入组织图。",
        "organizations": compact_organization_payload(game.organization_payload()),
    }, route="/api/organizations/custom")


@app.post("/api/organizations/fill_vacancy")
@_guarded
async def api_fill_organization_vacancy(body: FillVacancyRequest) -> Dict[str, Any]:
    game = get_game()
    return _response_with_state(
        game,
        game.fill_organization_vacancy(body.institution_id, body.slot_title, body.method),
        route="/api/organizations/fill_vacancy",
    )


@app.post("/api/recruitment/exam")
@_guarded
async def api_recruit_exam() -> Dict[str, Any]:
    game = get_game()
    return _response_with_state(game, game.recruit_exam_official(), route="/api/recruitment/exam")


@app.post("/api/recruitment/eunuch")
@_guarded
async def api_recruit_eunuch() -> Dict[str, Any]:
    game = get_game()
    return _response_with_state(game, game.recruit_eunuch(), route="/api/recruitment/eunuch")


@app.post("/api/recruitment/recommend")
@_guarded
async def api_recommend_hidden() -> Dict[str, Any]:
    game = get_game()
    return _response_with_state(game, game.recommend_hidden_official(), route="/api/recruitment/recommend")


@app.post("/api/recruitment/castrate")
@_guarded
async def api_castrate_official(body: CastrateRequest) -> Dict[str, Any]:
    game = get_game()
    return _response_with_state(
        game,
        game.castrate_official(body.name, force=body.force, scheme_text=body.scheme_text),
        route="/api/recruitment/castrate",
    )


@app.post("/api/recruitment/emancipate")
@_guarded
async def api_emancipate_eunuch(body: CastrateRequest) -> Dict[str, Any]:
    game = get_game()
    return _response_with_state(
        game,
        game.emancipate_eunuch(body.name, force=body.force),
        route="/api/recruitment/emancipate",
    )


@app.get("/api/secret_orders")
async def api_secret_orders(status: str = "") -> Dict[str, Any]:
    """列出密令。status 为空返回全部，否则按 active/done/failed 过滤。"""
    game = get_game()
    orders = game.db.list_secret_orders(status=status or None)
    return _web_payload_response(game, "/api/secret_orders", {"orders": orders})


@app.get("/api/agreements")
async def api_agreements(minister_name: str = "") -> Dict[str, Any]:
    """列出奏对协议与履约 todo。"""
    game = get_game()
    return _web_payload_response(
        game,
        "/api/agreements",
        {"agreements": game.agreement_payload(minister_name=minister_name)},
    )


@app.get("/api/conversation_goals")
async def api_conversation_goals(minister_name: str = "") -> Dict[str, Any]:
    """列出奏对目的与心理握手进度。"""
    game = get_game()
    return _web_payload_response(
        game,
        "/api/conversation_goals",
        {"conversation_goals": game.conversation_goal_payload(minister_name=minister_name)},
    )


@app.post("/api/conversation_goals/{goal_id}/abandon")
async def api_abandon_conversation_goal(goal_id: int, body: ConversationGoalAbandonRequest) -> Dict[str, Any]:
    game = get_game()
    try:
        goal = game.db.abandon_conversation_goal(game.state, goal_id, reason=body.reason or "玩家主动放弃")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return _response_with_state(game, {"goal": goal}, route="/api/conversation_goals/{goal_id}/abandon")


@app.patch("/api/agreements/tasks/{task_id}")
async def api_update_agreement_task(task_id: int, body: AgreementTaskPatch) -> Dict[str, Any]:
    """旧手动履约入口保留为兼容路由，但不再允许玩家自点完成。"""
    raise HTTPException(status_code=409, detail="履约已改为系统自动判定：请通过诏书、邸报或明确落库事实形成证据。")


@app.get("/api/turn_extraction")
async def api_turn_extraction(turn: int = -1) -> Dict[str, Any]:
    """读 turn_extractions：默认上一回合（state.turn-1，因 resolve 已 next_period）。"""
    if turn < 0:
        turn = max(1, int(get_game().state.turn) - 1)
    data = get_game().db.get_turn_extraction(turn)
    if data is None:
        return {"turn": turn, "exists": False}
    data["exists"] = True
    return data


@app.get("/api/history/turns")
async def api_history_turns() -> Dict[str, Any]:
    """已存档回合列表（turn_reports / turn_extractions / 已颁诏 turn_directives 并集）。"""
    return {"turns": get_game().db.list_archived_turns()}


@app.get("/api/history/turn/{turn}")
async def api_history_turn(turn: int) -> Dict[str, Any]:
    """某回合历史聚合：邸报奏报 + 诏书 + 已颁草案 + extractor 输入/输出。"""
    db = get_game().db
    report = db.get_turn_report(turn)
    extraction = db.get_turn_extraction(turn)
    directives = db.list_directives_by_turn(turn)
    if not report and extraction is None and not directives:
        return {"turn": turn, "exists": False}
    decree_text = ""
    if extraction is not None:
        decree_text = str(extraction.get("decree_text") or "")
        extraction["exists"] = True
    return {
        "turn": turn,
        "exists": True,
        "year": extraction["year"] if extraction else (directives[0]["year"] if directives else 0),
        "period": extraction["period"] if extraction else (directives[0]["period"] if directives else 0),
        "report": report,
        "decree_text": decree_text,
        "directives": directives,
        "extraction": extraction,
    }


@app.get("/api/map")
async def api_map() -> Dict[str, Any]:
    game = get_game()
    return _web_payload_response(game, "/api/map", {
        "node_fields": list(MAP_NODE_FIELDS),
        "region_fields": list(REGION_FIELDS),
        "army_fields": list(ARMY_FIELDS),
        "building_fields": list(BUILDING_FIELDS),
        "nodes": compact_map_nodes(game.map_nodes()),
    })


@app.get("/api/situation_reports")
async def api_situation_reports() -> Dict[str, Any]:
    game = get_game()
    return _web_payload_response(game, "/api/situation_reports", game.situation_reports_payload())


@app.get("/api/buildings")
async def api_buildings(region_id: str = "") -> Dict[str, Any]:
    game = get_game()
    return _web_payload_response(game, "/api/buildings", {
        "building_fields": list(BUILDING_FIELDS),
        "buildings": compact_buildings(game.db.building_payload(region_id)),
    })


@app.get("/api/characters")
async def api_character_index() -> Dict[str, Any]:
    game = get_game()
    runtime_rows = game._character_runtime_rows()
    portrait_assets = game._portrait_asset_meta_map()
    return _web_payload_response(game, "/api/characters", {
        "character_fields": list(CHARACTER_INDEX_FIELDS),
        "characters": compact_character_index(
            game.character_index_payload(
                runtime_rows=runtime_rows,
                portrait_assets=portrait_assets,
            )
        ),
    })


@app.get("/api/characters/{character_name}")
async def api_character_detail(character_name: str) -> Dict[str, Any]:
    game = get_game()
    character = game.content.characters.get(character_name)
    if character is None:
        raise HTTPException(status_code=404, detail=f"未找到人物：{character_name}")
    return _web_payload_response(
        game,
        "/api/characters/{character_name}",
        {"character": game.public_character(character)},
    )


@app.get("/api/decision")
async def api_decision() -> Dict[str, Any]:
    """当前待决的抉择事件（CK3 化 P2）。无则 {decision: None}。"""
    from ming_sim.court_events import pending_payload
    from ming_sim.playstyle import decision_testimonies_for_pending
    game = get_game()
    decision = pending_payload(game.db)
    if isinstance(decision, dict):
        decision = dict(decision)
        decision["testimonies"] = decision_testimonies_for_pending(game.db)
    return _plain_payload({"decision": decision})


@app.post("/api/decision/resolve")
async def api_decision_resolve(body: Dict[str, Any]) -> Dict[str, Any]:
    """玩家落子：应用所选后果，清待决。"""
    from ming_sim import timeflow
    from ming_sim.court_events import resolve_decision
    game = get_game()
    key = str((body or {}).get("choice") or "").strip()
    with _settlement_guard(game):
        day = timeflow.ensure_active(game.db, game.state)
        result = resolve_decision(game.db, game.state, key, day=day)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=str(result.get("message")))
    return _plain_payload(result)


@app.get("/api/court/{character_name}")
async def api_court(character_name: str) -> Dict[str, Any]:
    """活的宫廷（CK3 化 P1）：某官员的私心 + 党羽 + 政敌（双向好感网络）。"""
    from ming_sim.court import court_payload
    game = get_game()
    if character_name not in game.content.characters:
        raise HTTPException(status_code=404, detail=f"未找到人物：{character_name}")
    return _plain_payload(court_payload(game.db, character_name))


@app.post("/api/favorites/{minister_name}")
async def api_add_favorite(minister_name: str) -> Dict[str, Any]:
    game = get_game()
    if minister_name not in game.content.characters:
        raise HTTPException(status_code=404, detail=f"未找到：{minister_name}")
    game.favorites.add(minister_name)
    game.db.kv_set("favorites", json.dumps(sorted(game.favorites)))
    return _response_with_state(
        game,
        {"favorites": sorted(game.favorites)},
        route="/api/favorites/{minister_name}",
        method="POST",
    )


@app.delete("/api/favorites/{minister_name}")
async def api_remove_favorite(minister_name: str) -> Dict[str, Any]:
    game = get_game()
    game.favorites.discard(minister_name)
    game.db.kv_set("favorites", json.dumps(sorted(game.favorites)))
    return _response_with_state(
        game,
        {"favorites": sorted(game.favorites)},
        route="/api/favorites/{minister_name}",
        method="DELETE",
    )


_STATUS_LABEL_WEB = {
    "active": "在朝", "offstage": "尚未登场", "dead": "已殁", "dismissed": "已罢黜",
    "imprisoned": "下狱", "exiled": "流放", "retired": "致仕", "candidate": "待选",
}


def _require_active_minister(minister_name: str, action_label: str = "召见") -> None:
    if minister_name in get_game().session.temporary_characters:
        return
    if minister_name not in get_game().content.characters:
        raise HTTPException(status_code=404, detail=f"未找到人物：{minister_name}")
    if get_game().character_power_id(get_game().content.characters[minister_name]) != "ming":
        raise HTTPException(status_code=409, detail=f"{minister_name}不属大明朝廷，无法{action_label}。")
    status, reason = get_game().db.get_character_status(minister_name)
    if status != "active":
        label = _STATUS_LABEL_WEB.get(status, status)
        detail = f"{minister_name}{label}，无法{action_label}。" + (reason or "")
        raise HTTPException(status_code=409, detail=detail.strip())


@app.get("/api/eunuch")
async def api_eunuch() -> Dict[str, Any]:
    """在任随侍太监（人治之门）：皇帝直接对话之人。无则 None（召对回退直选官员）。"""
    from ming_sim.eunuch import eunuch_role_brief, get_attending_eunuch
    game = get_game()
    name = get_attending_eunuch(game.db)
    if not name:
        return {"eunuch": None}
    character = game.session._character(name)
    card = game.public_character(character)
    return {"eunuch": card, "brief": eunuch_role_brief(name, card.get("office", "") if isinstance(card, dict) else "")}


@app.get("/api/eunuch/candidates")
async def api_eunuch_candidates() -> Dict[str, Any]:
    """可任随侍者（宦官置顶；人皆可换）。"""
    from ming_sim.eunuch import list_candidates
    game = get_game()
    return {"candidates": list_candidates(game.db)}


@app.post("/api/eunuch/replace")
@_guarded
async def api_eunuch_replace(body: Dict[str, Any]) -> Dict[str, Any]:
    """换随侍太监。"""
    from ming_sim.eunuch import set_attending_eunuch
    game = get_game()
    name = str((body or {}).get("name") or "").strip()
    result = set_attending_eunuch(game.db, name)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=str(result.get("message")))
    character = game.session._character(name)
    return {"message": result["message"], "eunuch": game.public_character(character)}


@app.get("/api/eunuch/daipihong")
async def api_daipihong_status() -> Dict[str, Any]:
    """代批红现状（E1）：开关 + 权阉之势 + 委任者 + 委任者是否忠谨。"""
    from ming_sim.eunuch_power import (
        daipihong_keeper, get_eunuch_power, is_daipihong_on, keeper_disposition)
    game = get_game()
    keeper = daipihong_keeper(game.db)
    return {"on": is_daipihong_on(game.db), "eunuch_power": get_eunuch_power(game.db),
            "keeper": keeper, "keeper_upright": keeper_disposition(game.db, keeper) == "upright"}


@app.post("/api/eunuch/daipihong")
@_guarded
async def api_daipihong_toggle(body: Dict[str, Any]) -> Dict[str, Any]:
    """开/罢代批红，可同时改委任者（keeper）。善恶由委任者品性决定：
    委忠谨内臣＝据实拟行、弹章呈御览；委权阉＝留中劾阉、阉党自固。"""
    from ming_sim.eunuch_power import (
        daipihong_keeper, set_daipihong, get_eunuch_power, keeper_disposition)
    from ming_sim.upgrade_schema import KV_CURRENT_DAY, kv_int
    game = get_game()
    payload = body or {}
    on = bool(payload.get("on"))
    keeper_arg = payload.get("keeper")
    result = set_daipihong(game.db, on, keeper=(str(keeper_arg) if keeper_arg else None),
                           day=kv_int(game.db, KV_CURRENT_DAY, 0))
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=str(result.get("message")))
    result["eunuch_power"] = get_eunuch_power(game.db)
    k = daipihong_keeper(game.db)
    result.setdefault("keeper", k)
    result.setdefault("keeper_upright", keeper_disposition(game.db, k) == "upright")
    return result


@app.post("/api/intrigue/investigate")
@_guarded
async def api_intrigue_investigate(body: Dict[str, Any]) -> Dict[str, Any]:
    """令东厂侦缉某人，发掘把柄密呈御前（宫斗阴谋 P1）。"""
    from ming_sim.intrigue import investigate
    from ming_sim.upgrade_schema import KV_CURRENT_DAY, kv_int
    game = get_game()
    target = str((body or {}).get("name") or "").strip()
    if not target:
        raise HTTPException(status_code=400, detail="name 必填")
    res = investigate(game.db, target, kv_int(game.db, KV_CURRENT_DAY, 0))
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=str(res.get("message")))
    return res


@app.post("/api/intrigue/coerce")
@_guarded
async def api_intrigue_coerce(body: Dict[str, Any]) -> Dict[str, Any]:
    """凭已握把柄挟制其人：submit 输诚归附 / retire 逼令致仕 / serve 胁迫听用（宫斗阴谋 P1）。"""
    from ming_sim.intrigue import coerce_with_secret
    from ming_sim.upgrade_schema import KV_CURRENT_DAY, kv_int
    game = get_game()
    payload = body or {}
    holder = str(payload.get("name") or "").strip()
    mode = str(payload.get("mode") or "serve").strip()
    if not holder:
        raise HTTPException(status_code=400, detail="name 必填")
    res = coerce_with_secret(game.db, game.state, holder, mode, kv_int(game.db, KV_CURRENT_DAY, 0))
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=str(res.get("message")))
    game.db.save_state(game.state)
    return res


@app.post("/api/intrigue/fabricate")
@_guarded
async def api_intrigue_fabricate(body: Dict[str, Any]) -> Dict[str, Any]:
    """罗织罪名构陷某人下诏狱（宫斗阴谋 P2）：清誉高难陷、陷则易暴露反噬。"""
    from ming_sim.intrigue import fabricate
    from ming_sim.upgrade_schema import KV_CURRENT_DAY, kv_int
    game = get_game()
    target = str((body or {}).get("name") or "").strip()
    if not target:
        raise HTTPException(status_code=400, detail="name 必填")
    res = fabricate(game.db, game.state, target, kv_int(game.db, KV_CURRENT_DAY, 0))
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=str(res.get("message")))
    return res


@app.post("/api/frontier/supervisor")
@_guarded
async def api_frontier_supervisor(body: Dict[str, Any]) -> Dict[str, Any]:
    """遣/撤监军太监（E4）：{army_id, eunuch} 派监军；{army_id, recall:true} 撤监军。
    eunuch 省略则遣东厂提督/任一在朝宦官。"""
    from ming_sim.frontier import dispatch_supervisor, recall_supervisor
    from ming_sim.upgrade_schema import KV_CURRENT_DAY, kv_int
    game = get_game()
    payload = body or {}
    army_id = str(payload.get("army_id") or "").strip()
    if not army_id:
        raise HTTPException(status_code=400, detail="army_id 必填")
    day = kv_int(game.db, KV_CURRENT_DAY, 0)
    if payload.get("recall"):
        res = recall_supervisor(game.db, game.state, army_id, day)
    else:
        eunuch = str(payload.get("eunuch") or "").strip()
        if not eunuch:  # 默认遣东厂提督，缺则任一在朝宦官
            from ming_sim.intrigue import dongchang_chief
            eunuch = dongchang_chief(game.db) or ""
            if not eunuch:
                from ming_sim.eunuch import list_candidates
                for c in list_candidates(game.db):
                    if c.get("is_eunuch"):
                        eunuch = str(c["name"]); break
        res = dispatch_supervisor(game.db, game.state, army_id, eunuch, day)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=str(res.get("message")))
    game.db.save_state(game.state)
    return res


@app.get("/api/treasury/privy_relief")
async def api_privy_relief_status() -> Dict[str, Any]:
    """内帑助饷现状：内库余额 + 全军欠饷总额（供前端给出建议发帑额）。"""
    game = get_game()
    nei = int(game.state.metrics.get("内库", 0))
    row = game.db.conn.execute(
        "SELECT COALESCE(SUM(arrears),0) AS a FROM armies WHERE owner_power='ming' AND arrears>0"
    ).fetchone()
    arrears_total = int((row and row["a"]) or 0)
    return {"nei_ku": nei, "guo_ku": int(game.state.metrics.get("国库", 0)),
            "arrears_total": arrears_total,
            "suggested": min(nei, arrears_total) if arrears_total else min(nei, 50)}


@app.post("/api/treasury/privy_relief")
@_guarded
async def api_privy_relief(body: Dict[str, Any]) -> Dict[str, Any]:
    """发内帑助饷（内库→国库/清边军欠饷）。{amount} 万两；省略则按欠饷总额。"""
    from ming_sim.flows import release_privy_funds
    from ming_sim.upgrade_schema import KV_CURRENT_DAY, kv_int
    game = get_game()
    payload = body or {}
    nei = int(game.state.metrics.get("内库", 0))
    if payload.get("amount") is not None:
        amount = max(0, int(payload.get("amount") or 0))
    else:
        row = game.db.conn.execute(
            "SELECT COALESCE(SUM(arrears),0) AS a FROM armies WHERE owner_power='ming' AND arrears>0"
        ).fetchone()
        amount = min(nei, int((row and row["a"]) or 0)) or min(nei, 50)
    res = release_privy_funds(game.db, game.state, amount, kv_int(game.db, KV_CURRENT_DAY, 0))
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=str(res.get("message")))
    res["nei_ku"] = int(game.state.metrics.get("内库", 0))
    res["guo_ku"] = int(game.state.metrics.get("国库", 0))
    return res


@app.post("/api/intrigue/discord")
@_guarded
async def api_intrigue_discord(body: Dict[str, Any]) -> Dict[str, Any]:
    """离间二人（宫斗阴谋 P2）：挑其相疑；笃实忠正者识破、反损君威。"""
    from ming_sim.intrigue import sow_discord
    from ming_sim.upgrade_schema import KV_CURRENT_DAY, kv_int
    game = get_game()
    payload = body or {}
    a = str(payload.get("a") or "").strip()
    b = str(payload.get("b") or "").strip()
    if not a or not b:
        raise HTTPException(status_code=400, detail="a、b 必填")
    res = sow_discord(game.db, game.state, a, b, kv_int(game.db, KV_CURRENT_DAY, 0))
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=str(res.get("message")))
    game.db.save_state(game.state)
    return res


@app.get("/api/ministers/{minister_name}/chat")
async def api_chat_history(minister_name: str) -> Dict[str, Any]:
    _require_active_minister(minister_name)
    game = get_game()
    character = game.session._character(minister_name)
    history = game.chat_history.get(minister_name, [])
    return {
        "minister": game.public_character(character),
        "history": game._chat_history_payload(minister_name),
        "history_limit": _web_chat_history_limit(),
        "history_truncated": len(history) >= _web_chat_history_limit(),
        "suggestions": game.suggestions_for(character),
        "can_undo_last_chat": game.can_undo_last_chat(minister_name),
    }


@app.post("/api/ministers/{minister_name}/secret_order")
async def api_create_secret_order(minister_name: str, request: SecretOrderRequest) -> Dict[str, Any]:
    """皇帝直接下达密令，不经 LLM，直接落库。"""
    game = get_game()
    _require_active_minister(minister_name, "下达密令")
    character = game.session.content.characters.get(minister_name)
    if not character:
        raise HTTPException(status_code=404, detail=f"未找到大臣：{minister_name}")
    if game.character_power_id(character) != "ming":
        raise HTTPException(status_code=409, detail=f"{minister_name}不属大明朝廷，无法下达密令。")
    title = request.title.strip()[:20]
    content = request.content.strip()
    if not title or not content:
        raise HTTPException(status_code=400, detail="title 和 content 不能为空")
    try:
        order_id = game.db.create_secret_order(
            game.session.state, minister_name, title, content, request.tags, deadline_months=request.deadline_months
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    effect = game.session.record_secret_order_effect(order_id, minister_name)
    print(f"[secret_order/api] 直接落库 minister={minister_name} title={title!r} id={order_id}")
    return _web_payload_response(
        game,
        "/api/ministers/{minister_name}/secret_order",
        {"order_id": order_id, "minister_name": minister_name, "title": title, "status": "active", "effect": effect},
    )


@app.post("/api/ministers/{minister_name}/chat")
async def api_chat(minister_name: str, request: ChatRequest) -> Dict[str, Any]:
    _require_active_minister(minister_name)
    return get_game().chat(minister_name, request.message, request.context)


@app.post("/api/ministers/{minister_name}/chat/undo")
async def api_undo_chat(minister_name: str) -> Dict[str, Any]:
    return get_game().undo_last_chat(minister_name)


@app.post("/api/ministers/{minister_name}/chat/stream")
async def api_chat_stream(minister_name: str, request: ChatRequest) -> StreamingResponse:
    _require_active_minister(minister_name)
    async def generate() -> AsyncIterator[str]:
        for item in get_game().chat_stream(minister_name, request.message, request.context):
            item_type = str(item.get("type", "message"))
            if item_type == "delta":
                yield sse_event("delta", {"content": item.get("content", "")})
            elif item_type == "done":
                yield sse_event("done", item.get("payload", {}))
            elif item_type == "error":
                yield sse_event("error", item.get("detail") or {"message": item.get("message", "流式回复失败。")})
            await asyncio.sleep(0)

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.post("/api/directives")
@_guarded
async def api_create_directive(request: DirectiveRequest) -> Dict[str, Any]:
    if not request.text.strip():
        raise HTTPException(status_code=400, detail="指令内容不能为空。")
    game = get_game()
    try:
        dv = game.session.add_directive(request.text.strip(), notes=request.notes)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return _web_payload_response(game, "/api/directives", {
        "directive": {"id": dv.id, "text": dv.text, "status": dv.status},
        "directives": [game.directive_payload(item) for item in game.directive_rows()],
    })


@app.patch("/api/directives/{directive_id}")
@_guarded
async def api_update_directive(directive_id: int, request: DirectivePatch) -> Dict[str, Any]:
    game = get_game()
    rows = game.directive_rows()
    row = next((item for item in rows if int(item["id"]) == directive_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail="未找到草案。")
    text = request.text if request.text is not None else str(row["text"])
    if not text.strip():
        raise HTTPException(status_code=400, detail="指令内容不能为空。")
    try:
        game.session.update_directive(directive_id, text.strip())
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return _web_payload_response(
        game,
        "/api/directives/{directive_id}",
        {"directives": [game.directive_payload(item) for item in game.directive_rows()]},
        method="PATCH",
    )


@app.delete("/api/directives/{directive_id}")
@_guarded
async def api_delete_directive(directive_id: int) -> Dict[str, Any]:
    game = get_game()
    try:
        game.session.delete_directive(directive_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return _web_payload_response(
        game,
        "/api/directives/{directive_id}",
        {"directives": [game.directive_payload(item) for item in game.directive_rows()]},
        method="DELETE",
    )


@app.post("/api/directives/{directive_id}/confirm")
@_guarded
async def api_confirm_directive(directive_id: int) -> Dict[str, Any]:
    """大臣拟旨经皇帝核定：pending → draft。"""
    game = get_game()
    try:
        game.session.confirm_directive(directive_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return _web_payload_response(
        game,
        "/api/directives/{directive_id}/confirm",
        {
            "directives": [game.directive_payload(item) for item in game.directive_rows()],
            "pending_count": game.session.pending_count(),
        },
    )


@app.post("/api/directives/{directive_id}/reject")
@_guarded
async def api_reject_directive(directive_id: int) -> Dict[str, Any]:
    """皇帝驳回大臣拟旨：pending → rejected。"""
    game = get_game()
    try:
        game.session.reject_directive(directive_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return _web_payload_response(
        game,
        "/api/directives/{directive_id}/reject",
        {
            "directives": [game.directive_payload(item) for item in game.directive_rows()],
            "pending_count": game.session.pending_count(),
        },
    )


@app.post("/api/decree/write")
async def api_write_decree() -> Dict[str, Any]:
    game = get_game()
    try:
        decree = game.session.write_decree()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return _web_payload_response(game, "/api/decree/write", {"decree": decree})


class EditDecreeRequest(BaseModel):
    decree: str


@app.patch("/api/decree")
async def api_edit_decree(body: EditDecreeRequest) -> Dict[str, Any]:
    """皇帝手动改定诏书正文（拟诏后、颁诏前）。"""
    game = get_game()
    try:
        decree = game.session.set_decree(body.decree)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return _web_payload_response(game, "/api/decree", {"decree": decree})


class IssueDecreeRequest(BaseModel):
    # 作弊控制台（Ctrl+~）下的强制结算项；一次性，颁诏即用。普通颁诏留空。
    cheat: str = ""


@app.post("/api/decree/issue")
async def api_issue_decree(body: IssueDecreeRequest = IssueDecreeRequest()) -> Dict[str, Any]:
    """非流式颁诏（保留兼容）。前端默认走 /api/decree/issue/stream。"""
    game = get_game()
    lock = getattr(game, "turn_resolution_lock", None)
    acquired = lock.acquire(blocking=False) if lock is not None else True
    if not acquired:
        raise HTTPException(status_code=409, detail={"code": "turn_resolution_in_progress", "message": "本局正在结算，请等待当前颁诏完成。"})
    capacity_acquired = _try_acquire_turn_resolution_capacity()
    if not capacity_acquired:
        if lock is not None and acquired:
            lock.release()
        raise HTTPException(status_code=503, detail={"code": "turn_resolution_capacity_full", "message": "服务器结算任务已达上限，请稍后再试。"})
    portrait_before = game.portrait_generation_signatures()
    try:
        report = game.session.resolve_turn(cheat_directive=body.cheat)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    finally:
        _release_turn_resolution_capacity()
        if lock is not None and acquired:
            lock.release()
    decree = game.session.last_decree
    game.refresh_turn()
    portrait_jobs = game.queue_portrait_generation_for_signature_changes(portrait_before, "月末职服变化")
    return _web_payload_response(
        game,
        "/api/decree/issue",
        {"decree": decree, "report": report, "state": game.state_payload(), "portrait_jobs": portrait_jobs},
    )


@app.post("/api/decree/issue/stream")
async def api_issue_decree_stream(body: IssueDecreeRequest = IssueDecreeRequest()) -> StreamingResponse:
    """流式颁诏：推演过程（阶段/思考/正文）实时 SSE 推给前端。

    resolve_turn 是阻塞的同步调用，且 on_event 是 push 式回调。
    用 worker 线程跑 resolve_turn，回调把事件投进 Queue；
    async generator 从 Queue 拉事件转成 SSE。
    """
    ev_queue: "queue.Queue[tuple[str, Any]]" = queue.Queue()
    game = get_game()

    def on_event(kind: str, data: str) -> None:
        ev_queue.put((kind, data))

    def worker() -> None:
        lock = getattr(game, "turn_resolution_lock", None)
        acquired = lock.acquire(blocking=False) if lock is not None else True
        if not acquired:
            ev_queue.put(("__error__", {"code": "turn_resolution_in_progress", "message": "本局正在结算，请等待当前颁诏完成。"}))
            return
        capacity_acquired = _try_acquire_turn_resolution_capacity()
        if not capacity_acquired:
            if lock is not None and acquired:
                lock.release()
            ev_queue.put(("__error__", {"code": "turn_resolution_capacity_full", "message": "服务器结算任务已达上限，请稍后再试。"}))
            return
        try:
            portrait_before = game.portrait_generation_signatures()
            report = game.session.resolve_turn(on_event=on_event, cheat_directive=body.cheat)
            decree = game.session.last_decree
            game.refresh_turn()
            portrait_jobs = game.queue_portrait_generation_for_signature_changes(portrait_before, "月末职服变化")
            ev_queue.put(("__done__", {
                "decree": decree,
                "report": report,
                "state": game.state_payload(),
                "portrait_jobs": portrait_jobs,
            }))
        except ValueError as e:
            ev_queue.put(("__error__", str(e)))
        except Exception as e:  # noqa: BLE001
            ev_queue.put(("__error__", _llm_error_detail(e) if isinstance(e, LLMUnavailable) else str(e)))
        finally:
            _release_turn_resolution_capacity()
            if lock is not None and acquired:
                lock.release()

    async def generate() -> AsyncIterator[str]:
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        loop = asyncio.get_running_loop()
        while True:
            kind, data = await loop.run_in_executor(None, ev_queue.get)
            if kind == "__done__":
                yield sse_event("done", data)
                break
            if kind == "__error__":
                yield sse_event("error", data if isinstance(data, dict) else {"message": data})
                break
            # stage / thinking / text
            yield sse_event(kind, {"content": data})

    return StreamingResponse(generate(), media_type="text/event-stream")


class SaveCreateRequest(BaseModel):
    name: str


class LLMConfigRequest(BaseModel):
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    max_tokens: int = 0
    timeout_seconds: float = 0
    thinking_level: str = "__keep__"
    # None=不动，""=显式清空，其他=覆写。pydantic v1 默认 None 走不进来；用 sentinel "__keep__"
    advanced_model: str = "__keep__"
    advanced_base_url: str = "__keep__"
    advanced_api_key: str = "__keep__"
    advanced_thinking_level: str = "__keep__"


@app.get("/api/consorts/candidates")
async def api_consort_candidates() -> Dict[str, Any]:
    """返回 status=candidate 的待选秀女，供选妃事件展示。"""
    game = get_game()
    candidates = [
        game.public_character(c)
        for c in game.content.characters.values()
        if c.office_type == "后宫"
        and game.db.get_character_status(c.name)[0] == "candidate"
        and game.character_power_id(c) == "ming"
    ]
    return _web_payload_response(game, "/api/consorts/candidates", {"candidates": candidates})


@app.post("/api/consorts/{name}/select")
@_guarded
async def api_select_consort(name: str) -> Dict[str, Any]:
    """皇帝选中某秀女，转 active 并赋予初始位份。"""
    game = get_game()
    consort = game.content.characters.get(name)
    if consort is None or consort.office_type != "后宫":
        raise HTTPException(status_code=404, detail=f"未找到候选秀女：{name}")
    status, reason = game.db.get_character_status(name)
    if status != "candidate":
        label = _STATUS_LABEL_WEB.get(status, status)
        suffix = f"（{reason}）" if reason else ""
        raise HTTPException(status_code=409, detail=f"{name} 当前状态为 {label}{suffix}，不可再选。")
    game.db.set_character_office(name, "嫔", "后宫", source="皇帝选妃")
    game.db.set_character_status(game.state, name, "active", "皇帝选中入宫")
    consort.office = "嫔"
    consort.office_type = "后宫"
    consort.status = "active"
    # 同步进 registry（新增 agent）
    game.session.registry.register(consort)
    game.chat_history.setdefault(name, [])
    game.maybe_queue_portrait_generation(consort.name, "皇帝选妃")
    return _web_payload_response(game, "/api/consorts/{name}/select", {"selected": game.public_character(consort)})


@app.post("/api/consorts/{name}/action")
@_guarded
async def api_consort_action(name: str, body: ConsortActionRequest) -> Dict[str, Any]:
    game = get_game()
    return _response_with_state(
        game,
        game.perform_consort_action(name, body.action),
        route="/api/consorts/{name}/action",
    )


@app.get("/api/saves")
async def api_list_saves() -> Dict[str, Any]:
    return {"saves": get_game().list_saves()}


@app.post("/api/saves")
async def api_create_save(request: SaveCreateRequest) -> Dict[str, Any]:
    info = get_game().save_to(request.name)
    return {"save": info, "saves": get_game().list_saves()}


@app.delete("/api/saves/{name}")
async def api_delete_save(name: str) -> Dict[str, Any]:
    get_game().delete_save(name)
    return {"saves": get_game().list_saves()}


@app.post("/api/saves/{name}/load")
async def api_load_save(name: str) -> Dict[str, Any]:
    game = get_game()
    game.load_save(name)
    return _web_payload_response(game, "/api/saves/{name}/load", {"state": game.state_payload()})


@app.post("/api/game/reset")
async def api_reset_game() -> Dict[str, Any]:
    """清空主 DB 重开新局。存档目录保留。"""
    game = get_game()
    game.reset_game()
    return _web_payload_response(game, "/api/game/reset", {"state": game.state_payload()})


@app.get("/api/llm/config")
async def api_get_llm_config() -> Dict[str, Any]:
    """读当前生效的 LLM 配置。api_key 不回传明文，只回是否已设置。"""
    cfg = get_game().session.llm_config
    saved = load_runtime_llm()
    return {
        "base_url": cfg.base_url,
        "model": cfg.model,
        "max_tokens": cfg.max_tokens,
        "timeout_seconds": cfg.timeout_seconds,
        "thinking_level": cfg.thinking_level,
        "advanced_model": cfg.advanced_model,
        "advanced_base_url": cfg.advanced_base_url,
        "has_advanced_api_key": bool(cfg.advanced_api_key),
        "advanced_thinking_level": cfg.advanced_thinking_level,
        "has_api_key": bool(cfg.api_key),
        "persisted": {
            "base_url": saved.get("base_url", ""),
            "model": saved.get("model", ""),
            "has_api_key": bool(saved.get("api_key", "")),
            "max_tokens": int(saved.get("max_tokens") or 8000),
            "timeout_seconds": float(saved.get("timeout_seconds") or 180),
            "thinking_level": saved.get("thinking_level", ""),
            "advanced_model": saved.get("advanced_model", ""),
            "advanced_base_url": saved.get("advanced_base_url", ""),
            "has_advanced_api_key": bool(saved.get("advanced_api_key", "")),
            "advanced_thinking_level": saved.get("advanced_thinking_level", ""),
        },
    }


@app.post("/api/llm/config")
async def api_set_llm_config(request: LLMConfigRequest) -> Dict[str, Any]:
    if _auth_enabled() and os.environ.get("MING_SIM_ALLOW_CLIENT_LLM_CONFIG", "").strip().lower() not in ("1", "true", "yes"):
        raise HTTPException(
            status_code=403,
            detail={"code": "server_managed_llm", "message": "服务器模式下 API 配置由服务端统一管理。"},
        )
    thinking_level = None if request.thinking_level == "__keep__" else request.thinking_level
    advanced = None if request.advanced_model == "__keep__" else request.advanced_model
    adv_base = None if request.advanced_base_url == "__keep__" else request.advanced_base_url
    adv_key = None if request.advanced_api_key == "__keep__" else request.advanced_api_key
    adv_thinking = None if request.advanced_thinking_level == "__keep__" else request.advanced_thinking_level
    try:
        cfg = get_game().apply_llm_config(
            request.base_url,
            request.model,
            request.api_key,
            request.max_tokens,
            request.timeout_seconds,
            thinking_level=thinking_level,
            advanced_model=advanced,
            advanced_base_url=adv_base,
            advanced_api_key=adv_key,
            advanced_thinking_level=adv_thinking,
        )
    except LLMUnavailable as e:
        raise HTTPException(status_code=400, detail=_llm_error_detail(e)) from None
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=_llm_error_detail(e)) from None
    return {
        "base_url": cfg.base_url,
        "model": cfg.model,
        "max_tokens": cfg.max_tokens,
        "timeout_seconds": cfg.timeout_seconds,
        "thinking_level": cfg.thinking_level,
        "advanced_model": cfg.advanced_model,
        "advanced_base_url": cfg.advanced_base_url,
        "has_advanced_api_key": bool(cfg.advanced_api_key),
        "advanced_thinking_level": cfg.advanced_thinking_level,
        "has_api_key": bool(cfg.api_key),
    }


# ── 自定义立绘上传/读取 ──────────────────────────────────────────────────────
# content_type → 存盘扩展名。一人一图，上传新图会顶掉旧扩展名的文件。
_PORTRAIT_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


def _find_portrait_file(name: str, username: str = "") -> Optional[str]:
    """找该人物已存在的自定义立绘文件（任一扩展名），无则 None。"""
    portrait_dir = _portrait_dir_for_user(username)
    for ext in _PORTRAIT_EXT.values():
        path = os.path.join(portrait_dir, f"{name}.{ext}")
        if os.path.exists(path):
            return path
    return None


@lru_cache(maxsize=2)
def _static_portrait_filenames() -> frozenset[str]:
    names: set[str] = set()
    for base in (
        bundled_path("web", "public", "portraits"),
        bundled_path("web", "dist", "portraits"),
    ):
        try:
            names.update(item for item in os.listdir(str(base)) if not item.startswith("."))
        except OSError:
            continue
    return frozenset(names)


def _static_portrait_exists(filename: str) -> bool:
    """检查随包/源码静态立绘是否存在。"""
    clean = os.path.basename(str(filename or ""))
    return bool(clean and clean in _static_portrait_filenames())


def _find_static_portrait_file(filename: str) -> Optional[str]:
    """Return a bundled/static portrait path, if present."""
    clean = os.path.basename(str(filename or ""))
    if not clean:
        return None
    for base in (
        bundled_path("web", "public", "portraits"),
        bundled_path("web", "dist", "portraits"),
    ):
        path = os.path.join(str(base), clean)
        if os.path.isfile(path):
            return path
    return None


@lru_cache(maxsize=16)
def _static_portrait_ids_with_prefix(prefix: str) -> tuple[str, ...]:
    clean_prefix = os.path.basename(str(prefix or ""))
    ids: List[str] = []
    for filename in _static_portrait_filenames():
        if not filename.endswith(".png") or not filename.startswith(clean_prefix):
            continue
        portrait_id = filename[:-4]
        if " " in portrait_id:
            continue
        ids.append(portrait_id)
    return tuple(sorted(ids))


def _stable_static_portrait_id(prefix: str, key: str) -> str:
    ids = _static_portrait_ids_with_prefix(prefix)
    if not ids:
        return ""
    digest = hashlib.sha256(str(key or "").encode("utf-8")).hexdigest()
    return ids[int(digest[:8], 16) % len(ids)]


def _fallback_portrait_svg(name: str, family: str) -> str:
    """Deterministic SVG bust for characters without a painted portrait."""
    seed = int(hashlib.sha256(f"{family}:{name}".encode("utf-8")).hexdigest()[:8], 16)
    robe_palette = ["#243a4a", "#3e2e4c", "#31513e", "#55372d", "#42483a", "#2d4057"]
    accent_palette = ["#b68a43", "#9d4d3f", "#6c8b5b", "#7b5ca5", "#a36a3b", "#587f9b"]
    robe = robe_palette[seed % len(robe_palette)]
    accent = accent_palette[(seed // 7) % len(accent_palette)]
    face = "#d3a06c" if family == "minister" else "#d9a978"
    hat = "#171717" if family == "minister" else "#2b1822"
    label = html.escape(str(name or "未命名"), quote=True)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="512" height="768" viewBox="0 0 512 768" role="img" aria-label="{label}">
  <defs>
    <radialGradient id="halo" cx="50%" cy="16%" r="58%">
      <stop offset="0%" stop-color="#f1d89a" stop-opacity=".28"/>
      <stop offset="100%" stop-color="#21170f" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="robe" x1="0" x2="0" y1="0" y2="1">
      <stop offset="0%" stop-color="{robe}"/>
      <stop offset="100%" stop-color="#17130f"/>
    </linearGradient>
  </defs>
  <rect width="512" height="768" fill="#24180f"/>
  <rect width="512" height="768" fill="url(#halo)"/>
  <ellipse cx="256" cy="670" rx="172" ry="40" fill="#0d0a08" opacity=".38"/>
  <path d="M126 720c12-182 55-314 130-314s118 132 130 314z" fill="url(#robe)"/>
  <path d="M166 720c11-112 38-198 90-244 52 46 79 132 90 244z" fill="{accent}" opacity=".38"/>
  <path d="M145 500c34-55 71-80 111-80s77 25 111 80c-34 22-70 33-111 33s-77-11-111-33z" fill="#16100d" opacity=".58"/>
  <ellipse cx="256" cy="272" rx="91" ry="109" fill="{face}"/>
  <path d="M178 250c18-64 56-96 78-96s60 32 78 96c-32-19-58-27-78-27s-46 8-78 27z" fill="#2b1b14" opacity=".34"/>
  <path d="M181 185h150l-18-58H199z" fill="{hat}"/>
  <rect x="207" y="98" width="98" height="52" rx="8" fill="{hat}"/>
  <path d="M185 194c40 14 102 14 142 0" fill="none" stroke="{accent}" stroke-width="10" stroke-linecap="round"/>
  <circle cx="224" cy="275" r="7" fill="#2b1b14"/>
  <circle cx="288" cy="275" r="7" fill="#2b1b14"/>
  <path d="M232 327c18 10 30 10 48 0" fill="none" stroke="#54331f" stroke-width="6" stroke-linecap="round"/>
  <path d="M220 352c22 24 50 24 72 0" fill="#2b1b14" opacity=".42"/>
  <path d="M172 618c51 22 117 22 168 0" fill="none" stroke="#e0c27d" stroke-width="10" opacity=".28"/>
</svg>"""


@app.get("/portraits/{filename}")
async def api_static_or_pool_portrait(filename: str) -> Response:
    """Serve static portraits and deterministic pool fallbacks for missing names.

    Mobile chat bubbles still request /portraits/minister_<name>.png directly.
    Runtime-created or unpainted figures should get a face from the pool instead
    of a 404 and a text-only badge.
    """
    clean = os.path.basename(str(filename or ""))
    if not clean.endswith(".png"):
        raise HTTPException(status_code=404, detail="立绘不存在")
    path = _find_static_portrait_file(clean)
    if path:
        return FileResponse(path, media_type="image/png", headers={"Cache-Control": _STATIC_MEDIA_CACHE})

    match = re.match(r"^(minister|consort)_(.+)\.png$", clean)
    if match:
        family, raw_name = match.groups()
        pool_prefix = "minister_pool_" if family == "minister" else "consort_pool_"
        fallback_id = _stable_static_portrait_id(pool_prefix, raw_name)
        fallback_path = _find_static_portrait_file(f"{fallback_id}.png") if fallback_id else None
        if fallback_path:
            return FileResponse(
                fallback_path,
                media_type="image/png",
                headers={"Cache-Control": _STATIC_MEDIA_CACHE, "X-Portrait-Fallback": fallback_id},
            )
        return Response(
            content=_fallback_portrait_svg(raw_name, family),
            media_type="image/svg+xml",
            headers={"Cache-Control": _STATIC_MEDIA_CACHE, "X-Portrait-Fallback": "svg-bust"},
        )
    raise HTTPException(status_code=404, detail="立绘不存在")


@app.get("/portraits/generated/{asset_id}.png")
async def api_generated_portrait(asset_id: str) -> Response:
    clean = re.sub(r"[^0-9a-f]", "", asset_id.lower())[:40]
    if not clean:
        raise HTTPException(status_code=404, detail="立绘不存在")
    row = get_game().db.get_portrait_asset(clean)
    if row is None or str(row["status"] or "") != "ready" or row["image_blob"] is None:
        raise HTTPException(status_code=404, detail="立绘尚未绘成")
    blob = bytes(row["image_blob"])
    mime_type = str(row["mime_type"] or "image/png")
    if str(row["kind"] or "") == "portrait":
        repaired = normalize_portrait_png(
            blob,
            target_width=512,
            target_aspect_ratio=PORTRAIT_ASPECT_RATIO,
            cutout_background=True,
            use_rembg=False,
        )
        if repaired != blob:
            blob = repaired
            mime_type = detect_image_mime(blob)
            try:
                get_game().db.mark_portrait_asset_ready(clean, blob, mime_type=mime_type)
            except Exception as exc:  # noqa: BLE001 - serving the repaired image is more important than writeback
                print(f"[WARN] 旧立绘透明化回写失败 {clean}: {exc}")
    return Response(
        content=blob,
        media_type=mime_type,
        headers={"Cache-Control": "private, max-age=3600"},
    )


@app.post("/api/portraits/{name}/generate")
async def api_generate_portrait(name: str) -> Dict[str, Any]:
    game = get_game()
    job = game.queue_portrait_generation(name, "皇命重绘")
    character = game.find_character(name)
    return _response_with_state(
        game,
        {"job": job, "character": game.public_character(character) if character else None},
        route="/api/portraits/{name}/generate",
    )


@app.get("/api/portraits/{name}/status")
async def api_portrait_status(name: str) -> Dict[str, Any]:
    game = get_game()
    character = game.find_character(name)
    if character is None:
        raise HTTPException(status_code=404, detail=f"未找到人物：{name}")
    spec = build_portrait_spec(character, game.state, game.session.campaign_id)
    dna_row = game.db.get_portrait_asset(spec.dna_asset_id)
    dna_status = str(dna_row["status"] or "pending") if dna_row is not None else "missing"
    row = game.db.latest_character_portrait_asset(character.name)
    if row is None:
        return {
            "name": character.name,
            "status": "missing",
            "dna_seed": spec.dna_seed,
            "dna_asset_id": spec.dna_asset_id,
            "dna_status": dna_status,
            "wardrobe_key": spec.wardrobe_key,
            "portrait_id": character.portrait_id,
        }
    return {
        "name": character.name,
        "asset_id": row["asset_id"],
        "status": row["status"],
        "error": row["error"],
        "dna_seed": row["dna_seed"],
        "dna_asset_id": spec.dna_asset_id,
        "dna_status": dna_status,
        "wardrobe_key": row["wardrobe_key"],
        "portrait_id": character.portrait_id,
    }


@app.post("/api/consorts/{name}/portrait")
async def api_upload_portrait(name: str, file: UploadFile = File(...)) -> Dict[str, Any]:
    # 只接受已存在的人物名 → 集合固定，杜绝路径穿越/任意写。
    game = get_game()
    character = game.find_character(name)
    if character is None:
        raise HTTPException(status_code=404, detail="未找到该人物")
    ext = _PORTRAIT_EXT.get(file.content_type or "")
    if ext is None:
        raise HTTPException(status_code=400, detail="仅支持 PNG/JPEG/WebP 图片")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="文件为空")
    if len(data) > MAX_PORTRAIT_BYTES:
        raise HTTPException(status_code=400, detail="图片过大（上限 8MB）")
    processed = normalize_portrait_png(
        data,
        target_width=512,
        target_aspect_ratio=PORTRAIT_ASPECT_RATIO,
        cutout_background=True,
    )
    if detect_image_mime(processed) != "image/png":
        raise HTTPException(status_code=400, detail="图片无法解析或后处理失败")
    portrait_dir = _portrait_dir_for_user(game.username)
    os.makedirs(portrait_dir, exist_ok=True)
    # 后处理成功后再清旧图，避免失败上传导致原立绘丢失。
    old = _find_portrait_file(name, game.username)
    if old is not None:
        os.remove(old)
    with open(os.path.join(portrait_dir, f"{name}.png"), "wb") as fh:
        fh.write(processed)
    game.set_custom_portrait(name, f"{CUSTOM_PORTRAIT_PREFIX}{name}")
    return _response_with_state(
        game,
        {"name": name, "portrait_id": f"{CUSTOM_PORTRAIT_PREFIX}{name}"},
        route="/api/consorts/{name}/portrait",
        method="POST",
    )


@app.delete("/api/consorts/{name}/portrait")
async def api_delete_portrait(name: str) -> Dict[str, Any]:
    game = get_game()
    character = game.find_character(name)
    if character is None:
        raise HTTPException(status_code=404, detail="未找到该人物")
    old = _find_portrait_file(name, game.username)
    if old is not None:
        os.remove(old)
    # 复位 portrait_id：清空 → 前端回落到池图（add/seed 时会按 office_type 再分配）。
    game.set_custom_portrait(name, "")
    return _web_payload_response(
        game,
        "/api/consorts/{name}/portrait",
        {"name": name, "portrait_id": ""},
        method="DELETE",
    )


@app.get("/api/court_layout")
async def api_get_court_layout() -> Dict[str, Any]:
    val = get_game().db.kv_get("court_layout")
    return {"layout": val or "{}"}


@app.post("/api/court_layout")
async def api_set_court_layout(body: Dict[str, Any]) -> Dict[str, Any]:
    get_game().db.kv_set("court_layout", body.get("layout", "{}"))
    return {"ok": True}


@app.get("/portraits/custom/{name}")
async def api_get_portrait(name: str):
    path = _find_portrait_file(name, _current_game_username())
    if path is None:
        raise HTTPException(status_code=404, detail="无自定义立绘")
    return FileResponse(path)


# ── 调试台：直接读写核心表 ─────────────────────────────────────
@app.get("/api/admin/tables")
async def api_admin_tables() -> Dict[str, Any]:
    _require_server_admin()
    return {"tables": list(get_game().db.ADMIN_TABLES.keys())}


@app.get("/api/admin/table/{table}")
async def api_admin_table(table: str) -> Dict[str, Any]:
    _require_server_admin()
    db = get_game().db
    try:
        return {
            "table": table,
            "pk": db.admin_check_table(table),
            "columns": db.admin_columns(table),
            "rows": db.admin_rows(table),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/admin/table/{table}/upsert")
async def api_admin_upsert(table: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    _require_server_admin()
    game = get_game()
    try:
        row = game.db.admin_upsert(table, payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # 同步当前回合内存 state，否则改动要到下回合 begin_turn 才生效。
    st = game.state
    if table == "metrics" and row.get("key") in st.metrics:
        st.metrics[row["key"]] = int(row["value"])
    elif table == "game_state":
        st.year, st.period, st.turn = int(row["year"]), int(row["period"]), int(row["turn"])
    return {"row": row}


@app.post("/api/admin/table/{table}/delete")
async def api_admin_delete(table: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    _require_server_admin()
    pk_value = payload.get("pk_value")
    if pk_value in (None, ""):
        raise HTTPException(status_code=400, detail="缺 pk_value")
    try:
        return {"deleted": get_game().db.admin_delete(table, pk_value)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/admin")
async def admin_page():
    _require_server_admin()
    return HTMLResponse(_ADMIN_HTML)


@app.get("/server-admin")
async def server_admin_page():
    return HTMLResponse(_SERVER_ADMIN_HTML)


@app.get("/server_admin")
async def server_admin_page_alias():
    return HTMLResponse(_SERVER_ADMIN_HTML)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=204)


if os.path.isdir(WEB_DIST):
    app.mount("/", CacheControlledStaticFiles(directory=WEB_DIST, html=True), name="web")


_SERVER_ADMIN_HTML = """<!doctype html>
<html lang="zh"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>服务器后台 · 明末力挽狂澜</title>
<style>
  :root{color-scheme:dark;--bg:#17130f;--panel:#241d16;--line:#3b3025;--txt:#eadfc9;--muted:#a89978;--accent:#d4aa5c;--ok:#6fa77e;--warn:#d2a04d;--bad:#c96452}
  *{box-sizing:border-box}
  body{margin:0;background:linear-gradient(135deg,#1e1812,#11100d);color:var(--txt);font:14px/1.5 -apple-system,BlinkMacSystemFont,"PingFang SC","Noto Sans SC",sans-serif}
  header{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:18px 22px;border-bottom:1px solid var(--line);background:rgba(20,16,12,.86);position:sticky;top:0;z-index:2}
  h1{margin:0;color:var(--accent);font-size:19px;letter-spacing:.08em}
  main{display:grid;gap:16px;max-width:1180px;margin:0 auto;padding:18px}
  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px}
  .card,.panel{border:1px solid var(--line);border-radius:8px;background:rgba(36,29,22,.9);box-shadow:0 12px 30px rgba(0,0,0,.24)}
  .card{padding:13px 14px}.card span{display:block;color:var(--muted);font-size:12px}.card b{display:block;margin-top:5px;font-size:20px;color:#fff4d2;word-break:break-all}
  .panel{overflow:hidden}.panel-head{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 14px;border-bottom:1px solid var(--line)}
  .panel-head h2{margin:0;color:#f0d28c;font-size:15px}.hint{color:var(--muted);font-size:12px}
  table{width:100%;border-collapse:collapse}th,td{border-bottom:1px solid rgba(59,48,37,.72);padding:9px 10px;text-align:left;vertical-align:top}th{color:#d6be86;font-size:12px;background:rgba(255,224,156,.05)}td{font-size:13px}
  code{color:#f7d98c;word-break:break-all}.badge{display:inline-block;padding:2px 7px;border-radius:999px;border:1px solid var(--line);font-size:12px;color:var(--muted)}
  .badge.ok{border-color:rgba(111,167,126,.5);color:#9fd1aa}.badge.warn{border-color:rgba(210,160,77,.5);color:#e8bf72}.badge.bad{border-color:rgba(201,100,82,.5);color:#e89b8f}
  button,a.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;min-height:32px;padding:6px 10px;border:1px solid rgba(212,170,92,.48);border-radius:6px;color:#f3dfae;background:rgba(212,170,92,.08);text-decoration:none;cursor:pointer;font:inherit}
  button:hover,a.btn:hover{background:rgba(212,170,92,.18)}button.danger{border-color:rgba(201,100,82,.6);color:#ffb8aa;background:rgba(201,100,82,.1)}button:disabled{opacity:.45;cursor:not-allowed}
  .actions{display:flex;flex-wrap:wrap;gap:6px}.top-actions{display:flex;flex-wrap:wrap;gap:8px}.msg{min-height:20px;color:var(--muted)}
  .login{max-width:390px;margin:12vh auto 0;padding:22px;border:1px solid var(--line);border-radius:8px;background:var(--panel)}
  .login h2{margin:0 0 10px;color:var(--accent)}label{display:grid;gap:5px;margin:10px 0;color:#d7c59b}input{width:100%;min-height:38px;border:1px solid var(--line);border-radius:6px;background:#15110d;color:var(--txt);padding:8px 10px;font:inherit}
  .err{padding:9px 10px;border:1px solid rgba(201,100,82,.55);border-radius:6px;color:#ffc0b5;background:rgba(201,100,82,.12);white-space:pre-wrap}
  @media(max-width:760px){header{align-items:flex-start;flex-direction:column}main{padding:12px}table{font-size:12px}th:nth-child(4),td:nth-child(4),th:nth-child(6),td:nth-child(6){display:none}.actions{flex-direction:column}}
</style></head><body>
<header>
  <div><h1>服务器后台</h1><div class="hint">多用户对局、在线会话、服务端 LLM 配置</div></div>
  <div class="top-actions">
    <a class="btn" href="/">玩家入口</a>
    <a class="btn" href="/admin">核心表调试台</a>
    <button id="refreshBtn">刷新</button>
    <button id="logoutBtn">退出登录</button>
  </div>
</header>
<main id="app"><div class="msg">加载中...</div></main>
<script>
const $=s=>document.querySelector(s);
let overview=null;
function fmtBytes(n){n=Number(n||0);if(n<1024)return n+" B";if(n<1048576)return (n/1024).toFixed(1)+" KB";return (n/1048576).toFixed(1)+" MB"}
function fmtTime(ts){return ts?new Date(ts*1000).toLocaleString():"-"}
async function req(url,opt={}){
  const r=await fetch(url,{credentials:"same-origin",headers:{"content-type":"application/json",...(opt.headers||{})},...opt});
  const data=await r.json().catch(()=>({detail:r.statusText}));
  if(!r.ok){const d=data.detail||data;throw new Error(d.message||d.detail||r.statusText)}
  return data;
}
function renderLogin(err=""){
  $("#app").innerHTML=`<section class="login"><h2>管理员登录</h2><p class="hint">使用服务端配置的管理员账号。</p>${err?`<div class="err">${err}</div>`:""}<form id="loginForm"><label>用户名<input id="u" autocomplete="username" autofocus></label><label>密码<input id="p" type="password" autocomplete="current-password"></label><button type="submit">登录</button></form></section>`;
  $("#loginForm").onsubmit=async e=>{e.preventDefault();try{await req("/api/auth/login",{method:"POST",body:JSON.stringify({username:$("#u").value,password:$("#p").value})});await load()}catch(ex){renderLogin(ex.message)}};
}
function badge(text,cls){return `<span class="badge ${cls||""}">${text}</span>`}
function render(data){
  overview=data;
  const llm=data.llm||{};
  const users=(data.users||[]).map(u=>{
    const turn=u.turn?`${u.year}.${String(u.period).padStart(2,"0")} / 第${u.turn}回合`:"-";
    return `<tr>
      <td><b>${u.username}</b><br>${u.is_admin?badge("管理员","warn"):badge("玩家","")}</td>
      <td>${u.sessions?badge(`${u.sessions} 会话`,"ok"):badge("离线","")}<br>${u.running?badge("对局运行中","ok"):badge("未运行","")}</td>
      <td>${u.has_main_db?badge("有主进度","ok"):badge("无主进度","bad")}<br><span class="hint">${turn}</span></td>
      <td><code>${u.current_campaign||"-"}</code><br><span class="hint">${fmtTime(u.db_mtime)} · ${fmtBytes(u.db_size)}</span></td>
      <td>${u.saves_count} 个<br><span class="hint">${fmtBytes(u.saves_size)} · 最新 ${fmtTime(u.latest_save_mtime)}</span></td>
      <td><code>${u.db_path}</code></td>
      <td><div class="actions">
        <button data-act="close" data-u="${u.username}" ${u.running?"":"disabled"}>关闭对局</button>
        <button data-act="logout" data-u="${u.username}" ${u.sessions?"":"disabled"}>强制登出</button>
        <button class="danger" data-act="delete" data-u="${u.username}" ${u.has_main_db?"":"disabled"}>删主进度</button>
      </div></td>
    </tr>`;
  }).join("");
  $("#app").innerHTML=`<section class="cards">
    <div class="card"><span>运行对局</span><b>${data.running_games}</b></div>
    <div class="card"><span>在线会话</span><b>${data.active_sessions}</b></div>
    <div class="card"><span>服务端模型</span><b>${llm.model||"-"}</b><span>${llm.base_url||""}</span></div>
    <div class="card"><span>API Key</span><b>${llm.has_api_key?"已配置":"未配置"}</b><span>${llm.client_configurable?"允许网页修改":"服务端托管"}</span></div>
  </section>
  <section class="panel"><div class="panel-head"><h2>用户与对局</h2><span class="hint">管理员：${(data.admin_users||[]).join(", ")||"-"} · 已运行 ${Math.floor((data.uptime_seconds||0)/60)} 分钟</span></div>
  <table><thead><tr><th>用户</th><th>在线</th><th>主进度</th><th>战局</th><th>存档</th><th>路径</th><th>操作</th></tr></thead><tbody>${users||`<tr><td colspan="7">无用户</td></tr>`}</tbody></table></section><div class="msg" id="msg"></div>`;
  document.querySelectorAll("button[data-act]").forEach(b=>b.onclick=()=>act(b.dataset.act,b.dataset.u));
}
async function act(kind,user){
  try{
    if(kind==="close"&&!confirm(`关闭 ${user} 的运行对局？`))return;
    if(kind==="logout"&&!confirm(`强制 ${user} 退出登录并关闭对局？`))return;
    let data;
    if(kind==="close")data=await req(`/api/server_admin/users/${encodeURIComponent(user)}/close_game`,{method:"POST"});
    if(kind==="logout")data=await req(`/api/server_admin/users/${encodeURIComponent(user)}/logout`,{method:"POST"});
    if(kind==="delete"){
      const confirmText=prompt(`删除 ${user} 的主进度数据库？不会删除存档。输入 ${user} 或 DELETE 确认。`);
      if(!confirmText)return;
      data=await req(`/api/server_admin/users/${encodeURIComponent(user)}/main_db`,{method:"DELETE",body:JSON.stringify({confirm:confirmText})});
    }
    render(data.overview||await req("/api/server_admin/overview"));
  }catch(ex){$("#msg").textContent="操作失败："+ex.message}
}
async function load(){
  try{
    const me=await req("/api/auth/me");
    if(me.auth_enabled&&!me.authenticated){renderLogin();return}
    render(await req("/api/server_admin/overview"));
  }catch(ex){
    if(String(ex.message).includes("请先登录"))renderLogin();
    else $("#app").innerHTML=`<div class="err">${ex.message}</div>`;
  }
}
$("#refreshBtn").onclick=load;
$("#logoutBtn").onclick=async()=>{await req("/api/auth/logout",{method:"POST"}).catch(()=>{});renderLogin()};
load();
</script></body></html>"""


_ADMIN_HTML = """<!doctype html>
<html lang="zh"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>调试台 · 核心表增删改查</title>
<style>
  :root{--bg:#1b1712;--panel:#26211a;--line:#3a3228;--txt:#e8dcc6;--accent:#c8a35a;--danger:#b5503f;}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--txt);font:14px/1.5 -apple-system,"PingFang SC",monospace}
  header{padding:12px 16px;border-bottom:1px solid var(--line);display:flex;gap:8px;align-items:center;flex-wrap:wrap}
  header h1{font-size:16px;margin:0 12px 0 0;color:var(--accent)}
  .tab{padding:5px 12px;border:1px solid var(--line);background:var(--panel);color:var(--txt);border-radius:4px;cursor:pointer}
  .tab.active{background:var(--accent);color:#1b1712;font-weight:600}
  #bar{padding:8px 16px;border-bottom:1px solid var(--line);display:flex;gap:8px;align-items:center}
  button.act{padding:5px 12px;border:1px solid var(--accent);background:transparent;color:var(--accent);border-radius:4px;cursor:pointer}
  button.act:hover{background:var(--accent);color:#1b1712}
  #wrap{overflow:auto;height:calc(100vh - 110px)}
  table{border-collapse:collapse;width:100%;font-size:13px}
  th,td{border:1px solid var(--line);padding:4px 6px;text-align:left;white-space:nowrap}
  th{position:sticky;top:0;background:var(--panel);color:var(--accent);z-index:1}
  th.pk{color:#e8c87a}
  td input{width:100%;min-width:90px;background:#15110c;border:1px solid var(--line);color:var(--txt);padding:3px 5px;border-radius:3px;font:13px monospace}
  td input:focus{border-color:var(--accent);outline:none}
  tr.dirty td{background:#2e2718}
  td.ops{white-space:nowrap}
  .sm{padding:3px 8px;font-size:12px;border-radius:3px;cursor:pointer;border:1px solid var(--line);background:var(--panel);color:var(--txt)}
  .sm.save{border-color:var(--accent);color:var(--accent)}
  .sm.del{border-color:var(--danger);color:var(--danger)}
  #msg{margin-left:auto;color:#9c8c6a;font-size:12px}
  .hint{color:#6f6552;font-size:12px}
</style></head><body>
<header><h1>调试台 · 直改核心表</h1><span id="tabs"></span></header>
<div id="bar">
  <button class="act" id="addBtn">+ 新增行</button>
  <button class="act" id="reload">↻ 重载</button>
  <span class="hint">改格变黄→点行尾「存」。新增行须填主键才能存。删除不可撤销。</span>
  <span id="msg"></span>
</div>
<div id="wrap"><table id="grid"></table></div>
<script>
let cur=null, cols=[], pk=null, rows=[];
const $=s=>document.querySelector(s), msg=t=>{$("#msg").textContent=t;};
async function jget(u){const r=await fetch(u);if(!r.ok)throw new Error((await r.json()).detail||r.status);return r.json();}
async function jpost(u,b){const r=await fetch(u,{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify(b)});if(!r.ok)throw new Error((await r.json()).detail||r.status);return r.json();}
async function init(){
  const tabs=(await jget("/api/admin/tables")).tables;
  $("#tabs").innerHTML=tabs.map(t=>`<span class="tab" data-t="${t}">${t}</span>`).join("");
  document.querySelectorAll(".tab").forEach(e=>e.onclick=()=>load(e.dataset.t));
  load(tabs[0]);
}
async function load(t){
  cur=t; msg("加载…");
  document.querySelectorAll(".tab").forEach(e=>e.classList.toggle("active",e.dataset.t===t));
  const d=await jget("/api/admin/table/"+t);
  cols=d.columns; pk=d.pk; rows=d.rows; render(); msg(rows.length+" 行");
}
function render(){
  const g=$("#grid");
  const head="<tr>"+cols.map(c=>`<th class="${c.pk?'pk':''}">${c.name}${c.pk?' 🔑':''}<br><span class="hint">${c.type}</span></th>`).join("")+"<th>操作</th></tr>";
  g.innerHTML=head+rows.map((r,i)=>rowHtml(r,i)).join("");
  g.querySelectorAll("input").forEach(inp=>inp.oninput=()=>inp.closest("tr").classList.add("dirty"));
  g.querySelectorAll(".save").forEach(b=>b.onclick=()=>saveRow(+b.dataset.i));
  g.querySelectorAll(".del").forEach(b=>b.onclick=()=>delRow(+b.dataset.i));
}
function rowHtml(r,i){
  const tds=cols.map(c=>{
    const v=r[c.name]==null?"":r[c.name];
    return `<td><input data-c="${c.name}" value="${String(v).replace(/"/g,'&quot;')}"></td>`;
  }).join("");
  return `<tr data-i="${i}">${tds}<td class="ops"><button class="sm save" data-i="${i}">存</button> <button class="sm del" data-i="${i}">删</button></td></tr>`;
}
function readRow(i){
  const tr=document.querySelector(`tr[data-i="${i}"]`), o={};
  tr.querySelectorAll("input").forEach(inp=>{
    const c=cols.find(x=>x.name===inp.dataset.c); let v=inp.value;
    if(v===""){o[inp.dataset.c]=null;return;}
    if(c && /INT/i.test(c.type)) v=parseInt(v,10);
    o[inp.dataset.c]=v;
  });
  return o;
}
async function saveRow(i){
  try{
    const body=readRow(i);
    if(body[pk]==null||body[pk]===""){msg("⚠ 主键 "+pk+" 不能空");return;}
    const d=await jpost(`/api/admin/table/${cur}/upsert`,body);
    rows[i]=d.row; render(); msg("✓ 已存 "+body[pk]);
  }catch(e){msg("✗ "+e.message);}
}
async function delRow(i){
  const key=rows[i][pk];
  if(key!=null&&key!==""&&!confirm(`删除 ${cur} 行：${pk}=${key} ？不可撤销`))return;
  try{
    if(key==null||key===""){rows.splice(i,1);render();msg("已移除未存行");return;}
    const d=await jpost(`/api/admin/table/${cur}/delete`,{pk_value:key});
    rows.splice(i,1); render(); msg("✓ 删 "+d.deleted+" 行");
  }catch(e){msg("✗ "+e.message);}
}
$("#addBtn").onclick=()=>{const o={};cols.forEach(c=>o[c.name]=null);rows.unshift(o);render();msg("新增空行，填主键后点存");};
$("#reload").onclick=()=>load(cur);
init().catch(e=>msg("初始化失败:"+e.message));
</script></body></html>"""
