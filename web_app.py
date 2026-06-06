#!/usr/bin/env python3
"""FastAPI web entry for Ming Salvage Sim.

薄壳：路由调 ming_sim.session.GameSession（与 CLI 共用同一流转层）。
拟旨 draft 待确认：大臣 propose_directive → pending → 前端 准/驳。
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import random
import re
import threading
from typing import Any, AsyncIterator, Dict, Iterator, List, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
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
    npc_tiangang_profile,
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


class CastrateRequest(BaseModel):
    name: str
    force: bool = False


class AgreementTaskPatch(BaseModel):
    status: str
    evidence: str = ""


class ConsortActionRequest(BaseModel):
    action: str


class WebGame:
    """Web 端会话包装：持一个 GameSession + 网页专属态（聊天历史、收藏）。"""

    def __init__(self, fresh: bool = False) -> None:
        """实例化 = 真正进入游戏。无 API key 直接抛 LLMUnavailable。
        fresh=True：先清空主 DB（新游戏）再建 session。"""
        db_path = os.environ.get("MING_SIM_DB", "")
        # 默认存到用户数据目录（frozen=~/.ming_sim/ming_sim.db；源码=<repo>/data/ming_sim.db）。
        if not db_path:
            db_path = user_data_path("ming_sim.db")
        elif not os.path.isabs(db_path):
            db_path = str(user_data_dir() / db_path)
        base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
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
        # 召对记录持久化在 chat_messages 表，启动时恢复进内存缓存。
        self.chat_history: Dict[str, List[Dict[str, str]]] = {
            name: [] for name in self.session.content.characters
        }
        for name, msgs in self.db.load_all_chat_history().items():
            self.chat_history.setdefault(name, []).extend(msgs)
        _DEFAULT_FAVORITES = {"王承恩", "曹化淳", "李若琏", "魏忠贤", "田尔耕"}
        _fav_raw = self.db.kv_get("favorites")
        self.favorites: set = set(json.loads(_fav_raw)) if _fav_raw else set(_DEFAULT_FAVORITES)
        if not _fav_raw:
            self.db.kv_set("favorites", json.dumps(sorted(self.favorites)))

    # ── 存档管理 ─────────────────────────────────────────────────────────
    def saves_dir(self) -> str:
        return user_data_path("saves")

    def list_saves(self) -> List[Dict[str, Any]]:
        campaign_id = (self.db.kv_get("campaign_id") or "").strip()
        out = []
        for item in _scan_saves():
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
        """全清主 DB：关连接 → 删 sqlite 主/wal/shm → 重建空 session。
        存档目录不动。"""
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
        # 先关闭当前 session 的 DB 连接，避免 Windows/某些平台上的 file lock。
        try:
            self.session.close()
        except Exception:
            pass
        _replace_main_db_with_prepared_save(prepared, self.db_path)
        self._rebuild_session(self.session.llm_config)

    def _rebuild_session(self, llm_config: LLMConfig) -> None:
        """用新 llm_config（或换完 DB 后）重建 GameSession + 内存缓存。"""
        verify_llm_available(llm_config)
        self.session = GameSession(self.db_path, llm_config)
        self.session.begin_turn()
        self.chat_history = {name: [] for name in self.session.content.characters}
        for name, msgs in self.db.load_all_chat_history().items():
            self.chat_history.setdefault(name, []).extend(msgs)
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
    def _public_stance_notes(self, minister_name: str, *, limit: int = 3) -> List[Dict[str, Any]]:
        """玩家可见的奏对立场：保留证据与风险，隐藏月末推演用字段。"""
        public_rows: List[Dict[str, Any]] = []
        for row in self.db.list_minister_stances(
            turn=self.state.turn,
            minister_name=minister_name,
            limit=limit,
        ):
            item = dict(row)
            for private_key in (
                "evidence_json",
                "risk_tags",
                "execution_hint",
                "source_chat_turn_id",
            ):
                item.pop(private_key, None)
            public_rows.append(item)
        return public_rows

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

    def public_character(self, character: Character, *, include_detail: bool = True) -> Dict[str, Any]:
        status, status_reason = self.db.get_character_status(character.name)
        status_label = _STATUS_LABEL_WEB.get(status, "在朝" if status == "active" else status)
        db_row = self.db.conn.execute(
            "SELECT office, office_type, faction, portrait_id, power_id, birth_year FROM characters WHERE name=?", (character.name,)
        ).fetchone()
        office = (db_row["office"] if db_row else character.office) or ""  # 去职者已被清空，可能为空串
        office_type = (db_row["office_type"] if db_row else character.office_type) or character.office_type
        office_type = effective_stored_office_type(office, office_type)
        faction = (db_row["faction"] if db_row else character.faction) or character.faction
        portrait_id = (db_row["portrait_id"] if db_row else character.portrait_id) or character.portrait_id
        power_id = (db_row["power_id"] if db_row else None) or getattr(character, "power_id", "ming") or "ming"
        age_payload = self._age_payload(character, int(db_row["birth_year"] or 0) if db_row else 0)
        portrait_prefix = "consort_" if office_type == "后宫" else "minister_"
        portrait_meta = self._portrait_meta(character, portrait_id, portrait_prefix)
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
        power_name = str(getattr(power, "name", "") or "大明")
        identity_bits = [power_name, faction, office_type, status_label]
        # 公开摘要只展示身份与处境；性情/行事逻辑交给天罡谱尺和人物网络表达。
        summary = " · ".join(bit for bit in identity_bits if bit)
        payload: Dict[str, Any] = {
            "name": character.name,
            "office": office,
            "office_type": office_type,
            "faction": faction,
            "status": status,
            "status_reason": status_reason,
            "status_label": status_label,
            "career_state": career_state,
            "summary": summary,
            "portrait_id": portrait_id,
            **portrait_meta,
            **age_payload,
            "power_id": power_id,
            "stance_notes": self._public_stance_notes(character.name, limit=3),
            "conversation_goals": self.conversation_goal_payload(minister_name=character.name, limit=8),
            "xinpan_profile": self.db.get_xinpan_profile(character.name, self.state),
            "skills": [],
            "favorite": character.name in self.favorites,
        }
        if include_detail:
            payload.update({
                "network_profile": npc_network_profile(character.name, db=self.db, limit=8),
                "tiangang_profile": npc_tiangang_profile(character.name),
                "skills": [
                    {
                        "id": skill_id,
                        "name": skill_display_name(skill_id),
                        "sources": skill_source_labels(character, skill_id, self.db),
                        "description": self.content.skill_descriptions.get(skill_id, ""),
                    }
                    for skill_id in available_skill_ids(character, self.db)
                ],
            })
        return payload

    def character_index_payload(self) -> List[Dict[str, Any]]:
        """全 NPC 只读索引：轻量展示用，不携带天罡/人脉大对象。"""
        rows: List[Dict[str, Any]] = []
        for character in self.content.characters.values():
            status, status_reason = self.db.get_character_status(character.name)
            status_label = _STATUS_LABEL_WEB.get(status, "在朝" if status == "active" else status)
            db_row = self.db.conn.execute(
                "SELECT office, office_type, faction, portrait_id, power_id, birth_year FROM characters WHERE name=?",
                (character.name,),
            ).fetchone()
            office = (db_row["office"] if db_row else character.office) or ""
            office_type = (db_row["office_type"] if db_row else character.office_type) or character.office_type
            office_type = effective_stored_office_type(office, office_type)
            faction = (db_row["faction"] if db_row else character.faction) or character.faction
            portrait_id = (db_row["portrait_id"] if db_row else character.portrait_id) or character.portrait_id
            power_id = (db_row["power_id"] if db_row else None) or getattr(character, "power_id", "ming") or "ming"
            age_payload = self._age_payload(character, int(db_row["birth_year"] or 0) if db_row else 0)
            power = self.content.powers.get(power_id)
            power_name = str(getattr(power, "name", "") or power_id or "大明")
            portrait_prefix = "consort_" if office_type == "后宫" else "minister_"
            portrait_meta = self._portrait_meta(character, portrait_id, portrait_prefix)
            identity_bits = [power_name, faction, office_type, status_label]
            rows.append({
                "name": character.name,
                "office": office,
                "office_type": office_type,
                "faction": faction,
                "status": status,
                "status_reason": status_reason,
                "status_label": status_label,
                "power_id": power_id,
                "power_name": power_name,
                "summary": " · ".join(bit for bit in identity_bits if bit),
                "xinpan_quadrant": str((self.db.get_xinpan_profile(character.name, self.state) or {}).get("quadrant") or ""),
                **age_payload,
                **portrait_meta,
                "can_summon": bool(power_id == "ming" and status == "active"),
            })
        return rows

    def character_power_id(self, character: Character) -> str:
        row = self.db.conn.execute(
            "SELECT power_id FROM characters WHERE name=?", (character.name,)
        ).fetchone()
        return (row["power_id"] if row else None) or getattr(character, "power_id", "ming") or "ming"

    def _portrait_meta(self, character: Character, portrait_id: str, portrait_prefix: str) -> Dict[str, Any]:
        status = "missing"
        error = ""
        dna_seed = ""
        dna_asset_id = ""
        dna_status = "missing"
        wardrobe_key = ""
        available = False
        if portrait_id.startswith(GENERATED_PORTRAIT_PREFIX):
            asset_id = portrait_id.removeprefix(GENERATED_PORTRAIT_PREFIX)
            row = self.db.get_portrait_asset(asset_id)
            if row is not None:
                status = str(row["status"] or "pending")
                error = str(row["error"] or "")
                dna_seed = str(row["dna_seed"] or "")
                wardrobe_key = str(row["wardrobe_key"] or "")
                available = bool(status == "ready" and row["image_blob"] is not None)
            else:
                status = "missing"
        elif portrait_id.startswith(CUSTOM_PORTRAIT_PREFIX):
            status = "ready" if _find_portrait_file(character.name) is not None else "missing"
            available = status == "ready"
        else:
            available = (
                _static_portrait_exists(f"{portrait_prefix}{character.name}.png")
                or (bool(portrait_id) and _static_portrait_exists(f"{portrait_id}.png"))
            )
            status = "ready" if available else "missing"
        spec = build_portrait_spec(character, self.state, self.session.campaign_id)
        dna_asset_id = spec.dna_asset_id
        dna_row = self.db.get_portrait_asset(dna_asset_id)
        if dna_row is not None:
            dna_status = str(dna_row["status"] or "pending")
        if not dna_seed:
            dna_seed = spec.dna_seed
        if not wardrobe_key:
            wardrobe_key = spec.wardrobe_key
        return {
            "portrait_available": available,
            "portrait_status": status,
            "portrait_error": error,
            "portrait_dna_seed": dna_seed,
            "portrait_dna_asset_id": dna_asset_id,
            "portrait_dna_status": dna_status,
            "portrait_wardrobe_key": wardrobe_key,
        }

    def directive_payload(self, row) -> Dict[str, Any]:
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

    def map_nodes(self) -> List[Dict[str, Any]]:
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
        armies = self.db.army_payload(danger_order=True)
        nodes: List[Dict[str, Any]] = []
        for region in self.db.region_payload():
            x, y = region_positions.get(str(region["id"]), (50, 50))
            stationed = [a for a in armies if self._army_belongs_to_region(a, region)]
            buildings = self.db.building_payload(str(region["id"]))
            risk = int(region["unrest"]) + int(region["military_pressure"]) + (100 - int(region["public_support"]))
            node_kind = "region" if str(region.get("controlled_by") or "ming") == "ming" else "external"
            nodes.append({"id": region["id"], "kind": node_kind, "x": x, "y": y, "region": region, "armies": stationed, "buildings": buildings, "risk": risk})
        for node_id, (x, y) in theater_positions.items():
            stationed = [a for a in armies if self._army_belongs_to_theater(a, node_id)]
            if stationed:
                nodes.append({"id": node_id, "kind": "theater", "x": x, "y": y, "label": self._theater_label(node_id), "armies": stationed, "risk": 120})
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
        for row in self.db.list_active_issues():
            payloads.append({
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
            })
        return payloads

    def legacies_payload(self) -> List[Dict[str, Any]]:
        """现行帝国修正（长期百分比修正符），给状态栏小条用。"""
        out: List[Dict[str, Any]] = []
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
            out.append({
                "id": int(row["id"]),
                "name": row["name"],
                "narrative_hint": row["narrative_hint"],
                "modifiers": eff,
                "effect_text": _humanize_legacy_effect(eff, self.content),
                "remaining_months": remaining_months,
                "clear_condition": clear_condition,
            })
        return out

    def budget_payload(self) -> Dict[str, Any]:
        # 唯一定额源：flows.compute_budget_lines（与实际落账 / 大臣 treasury_budget_summary 三处统一）。
        budget = compute_budget_lines(self.db, self.state)
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

    def organization_payload(self) -> Dict[str, Any]:
        base_institutions: List[Dict[str, Any]] = base_institution_specs()
        custom_institutions = self._custom_institutions()
        diagnostics = organization_diagnostics(self.db, custom_institutions)
        diagnostic_by_id = {
            str(item.get("id") or ""): item
            for item in diagnostics.get("institutions", [])
            if isinstance(item, dict)
        }

        characters = [
            c for c in self.content.characters.values()
            if c.office_type != "后宫" and self.character_power_id(c) == "ming"
        ]
        active_snapshots: List[tuple[Character, str, str]] = []
        for character in characters:
            status, _reason = self.db.get_character_status(character.name)
            if status != "active":
                continue
            row = self.db.conn.execute(
                "SELECT office, office_type FROM characters WHERE name=?", (character.name,)
            ).fetchone()
            office = (row["office"] if row else character.office) or ""
            office_type = (row["office_type"] if row else character.office_type) or character.office_type
            office_type = effective_stored_office_type(office, office_type)
            active_snapshots.append((character, office, office_type))

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
            for character, office, actual_type in active_snapshots:
                parts = usable_parts(office)
                text = " ".join(parts)
                hit = False
                if slot.get("office_type_only") and office_types and actual_type in office_types:
                    hit = True
                if not hit and terms:
                    hit = any(term in part for term in terms for part in parts)
                if not hit and match_re:
                    hit = any(re.search(match_re, part) for part in parts)
                if not hit and office_types and actual_type in office_types:
                    hit = any(term in text for term in terms)
                if hit:
                    assigned_names.add(character.name)
                    holders.append(self.public_character(character, include_detail=False))
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
            self.public_character(character, include_detail=False)
            for character, office, actual_type in active_snapshots
            if character.name not in assigned_names
            and usable_parts(office)
            and actual_type not in {"外臣"}
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

    def recruit_exam_official(self) -> Dict[str, Any]:
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
            summary=f"{self.state.year}年科举新进士，{origin[1]}；{archetype['summary']}",
            force=rng.randint(35, 58),
            wisdom=wisdom,
            charm=rng.randint(48, 74),
            luck=rng.randint(42, 84),
        )
        added = self._add_runtime_character(character, "科举取士")
        return {"message": f"新科取士：{added.name}补入{added.office}。", "minister": self.public_character(added)}

    def recruit_eunuch(self) -> Dict[str, Any]:
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
            summary=f"净身入宫的内廷新人。{archetype['summary']}太监是皇帝家奴，入仕路径正常；其能力未必压倒外朝，但忠诚与执行链清晰。",
            force=rng.randint(36, 64),
            wisdom=rng.randint(44, 74),
            charm=rng.randint(38, 70),
            luck=rng.randint(46, 84),
        )
        added = self._add_runtime_character(character, "招募太监")
        return {"message": f"内廷募入：{added.name}补入{added.office}。", "minister": self.public_character(added)}

    def recommend_hidden_official(self) -> Dict[str, Any]:
        rng = self.character_rng
        active_recommenders = [
            c for c in self.content.characters.values()
            if c.office_type != "后宫"
            and self.character_power_id(c) == "ming"
            and self.db.get_character_status(c.name)[0] == "active"
        ]
        network_hits: List[Dict[str, Any]] = []
        for recommender in active_recommenders:
            for row in npc_network_recommendations(
                recommender.name,
                db=self.db,
                limit=12,
                include_statuses={"offstage", "dismissed", "retired"},
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
            return {
                "message": f"举贤发现：{hit['recommender']}举荐{chosen.name}出仕（{evidence}）。",
                "minister": self.public_character(chosen),
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
            summary=f"由地方举荐入京的在野人物，{origin[1]}；{archetype['summary']}尚无稳固靠山。",
            force=rng.randint(34, 60),
            wisdom=rng.randint(52, 82),
            charm=rng.randint(46, 78),
            luck=rng.randint(38, 86),
        )
        added = self._add_runtime_character(character, "举贤入京")
        return {"message": f"举贤入京：{added.name}列入待铨。", "minister": self.public_character(added)}

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
            text = f"{row.get('topic', '')} {row.get('summary', '')} {row.get('conditions', '')}"
            if not re.search(r"净身|入宫|内廷|司礼监|太监|宦官|宫禁", text):
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
            text = f"{row.get('topic', '')} {row.get('summary', '')} {row.get('conditions', '')}"
            if not re.search(r"奴籍|民籍|脱籍|还民|转为民|转民籍|出宫为民|归为百姓|赐还为民", text):
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

    @staticmethod
    def _xinpan_effect_summary(before: Optional[Dict[str, object]], after: Optional[Dict[str, object]]) -> str:
        if not isinstance(after, dict):
            return ""
        before_map = before if isinstance(before, dict) else {}

        def value(data: Dict[str, object], key: str, fallback: float = 0.0) -> float:
            try:
                return float(data.get(key, fallback) or fallback)
            except (TypeError, ValueError):
                return fallback

        def signed(delta: float, digits: int = 1) -> str:
            rounded = round(delta, digits)
            text = f"{rounded:.{digits}f}".rstrip("0").rstrip(".")
            if text == "-0":
                text = "0"
            return f"+{text}" if rounded > 0 else text

        parts: List[str] = []
        for key, label, digits, threshold in (
            ("dao_he", "道", 1, 0.05),
            ("shi_he", "势", 1, 0.05),
            ("fear", "惧", 1, 0.05),
            ("hatred", "恨", 1, 0.05),
            ("trust_coeff", "信", 3, 0.004),
        ):
            fallback = 1.0 if key == "trust_coeff" else 0.0
            delta = value(after, key, fallback) - value(before_map, key, fallback)
            if abs(delta) >= threshold:
                parts.append(f"{label}{signed(delta, digits)}")
        if not parts:
            return ""
        quadrant = str(after.get("quadrant") or "")
        tail = f"；现为{quadrant}" if quadrant else ""
        return f"心盘实记：{' · '.join(parts)}{tail}。"

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

    def castrate_official(self, name: str, force: bool = False) -> Dict[str, Any]:
        clean_name = (name or "").strip()
        character = self.content.characters.get(clean_name)
        if character is None or character.office_type == "后宫":
            raise HTTPException(status_code=404, detail=f"未找到可净身入内廷的人物：{clean_name}")
        if self.character_power_id(character) != "ming":
            raise HTTPException(status_code=409, detail=f"{clean_name}不属大明，不能入内廷。")
        status, reason = self.db.get_character_status(clean_name)
        if status != "active":
            raise HTTPException(status_code=409, detail=f"{clean_name}当前{_STATUS_LABEL_WEB.get(status, status)}，不可净身入宫。{reason}")
        if not self._castration_applicable(character):
            raise HTTPException(status_code=409, detail=f"{clean_name}并非可净身入内廷的文官或武官。")
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
        source = "强旨净身入宫" if force else "自愿净身入宫"
        try:
            xinpan_before = self.db.get_xinpan_profile(clean_name, self.state)
        except Exception:
            xinpan_before = {}
        character, political_reactions = convert_character_to_eunuch(
            self.db,
            self.state,
            self.content,
            clean_name,
            force=force,
            source=source,
            new_office=new_office,
        )
        if self.session.registry is not None:
            self.session.registry.register(character)
        xinpan_effect_text = ""
        try:
            if force:
                xinpan_after = self.db.apply_direct_xinpan_adjustment(
                    self.state,
                    character.name,
                    shi_delta=-48,
                    fear_delta=24,
                    hatred_delta=52,
                    trust_multiplier=0.58,
                    event="强旨净身入内廷",
                    source_kind="identity_conversion",
                    source_id="forced_castration",
                )
            else:
                xinpan_after = self.db.apply_direct_xinpan_adjustment(
                    self.state,
                    character.name,
                    shi_delta=16,
                    fear_delta=-2,
                    hatred_delta=-3,
                    trust_multiplier=1.06,
                    event="自愿净身入内廷，转入近侍执行链",
                    source_kind="identity_conversion",
                    source_id="voluntary_castration",
                )
            xinpan_effect_text = self._xinpan_effect_summary(xinpan_before, xinpan_after)
        except Exception as exc:  # noqa: BLE001 - identity conversion should still succeed
            print(f"[WARN] 净身入内廷心盘更新失败 {character.name}: {exc}")
        self.maybe_queue_portrait_generation(character.name, source)
        prefix = "强旨已下，" if force else ""
        reaction_text = f" 朝局反应：{political_reactions[0].get('summary')}" if political_reactions else ""
        return {
            "message": f"{prefix}{clean_name}已净身入内廷，补为{new_office}。{reaction_text}{(' ' + xinpan_effect_text) if xinpan_effect_text else ''}",
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
        try:
            xinpan_before = self.db.get_xinpan_profile(clean_name, self.state)
        except Exception:
            xinpan_before = {}
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
        xinpan_effect_text = ""
        try:
            if force:
                xinpan_after = self.db.apply_direct_xinpan_adjustment(
                    self.state,
                    character.name,
                    shi_delta=-36,
                    fear_delta=10,
                    hatred_delta=58,
                    trust_multiplier=0.76,
                    event="强旨赶出内廷，脱籍为民",
                    source_kind="identity_conversion",
                    source_id="forced_emancipation",
                )
            else:
                xinpan_after = self.db.apply_direct_xinpan_adjustment(
                    self.state,
                    character.name,
                    shi_delta=6,
                    fear_delta=-2,
                    hatred_delta=0,
                    trust_multiplier=1.02,
                    event="自愿脱离内廷奴籍，放归民籍",
                    source_kind="identity_conversion",
                    source_id="voluntary_emancipation",
                )
            xinpan_effect_text = self._xinpan_effect_summary(xinpan_before, xinpan_after)
        except Exception as exc:  # noqa: BLE001 - identity conversion should still succeed
            print(f"[WARN] 奴籍转民籍心盘更新失败 {character.name}: {exc}")
        self.maybe_queue_portrait_generation(character.name, source)
        prefix = "强旨已下，" if force else ""
        return {
            "message": f"{prefix}{clean_name}已脱离内廷奴籍，转为民籍百姓；新立绘将改为布衣头巾。{(' ' + xinpan_effect_text) if xinpan_effect_text else ''}",
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
        out: List[Dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item.pop("conditions_json", None)
            item.pop("blockers_json", None)
            item.pop("last_delta_json", None)
            item["progress_label"] = f"{int(item.get('score') or 0)}%"
            pending = [
                cond for cond in (item.get("conditions") or [])
                if isinstance(cond, dict) and str(cond.get("status") or "pending") != "done"
            ]
            item["pending_conditions"] = pending
            out.append(item)
        return out

    def state_payload(self) -> Dict[str, Any]:
        directives = [self.directive_payload(row) for row in self.directive_rows()]
        return {
            "turn": {"year": self.state.year, "period": self.state.period,
                     "turn": self.state.turn, "phase": self.state.turn_phase},
            "metrics": self.state.metrics,
            "previous_summary": self.previous_summary,
            "treasury": self.db.treasury_report(self.state),
            "issues": self.issue_payloads(),
            "legacies": self.legacies_payload(),
            "closed_this_turn": self.closed_this_turn_payloads(),
            "budget": self.budget_payload(),
            "region_warning": self.db.region_report(limit=5),
            "army_warning": self.db.army_report(limit=5),
            "power_warning": self.db.power_report(exclude_self=True),
            "powers": self.db.power_payload(),
            "victory_status": self.session.victory(),
            "ending": self.ending_payload(),
            "events": [],
            "regions": self.db.region_payload(),
            "armies": self.db.army_payload(),
            "map_nodes": self.map_nodes(),
            "organizations": self.organization_payload(),
            "character_index": self.character_index_payload(),
            "ministers": [
                self.public_character(c, include_detail=False)
                for c in self.content.characters.values()
                if c.office_type != "后宫" and self.character_power_id(c) == "ming"
            ],
            "consorts": [
                self.public_character(c, include_detail=False)
                for c in self.content.characters.values()
                if c.office_type == "后宫"
                and self.db.get_character_status(c.name)[0] == "active"
                and self.character_power_id(c) == "ming"
            ],
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
        self.chat_history = {name: [] for name in self.session.content.characters}
        for name, msgs in self.db.load_all_chat_history().items():
            self.chat_history.setdefault(name, []).extend(msgs)
        character = self.session._character(minister_name)
        return {
            "minister": minister_name,
            "minister_profile": self.public_character(character),
            "undone_chat_turn_id": int(undone["id"]),
            "history": self.chat_history.get(minister_name, []),
            "directives": [self.directive_payload(row) for row in self.directive_rows()],
            "pending_count": self.session.pending_count(),
            "secret_orders": self.db.list_secret_orders(),
            "suggestions": self.suggestions_for(character),
            "can_undo_last_chat": self.can_undo_last_chat(minister_name),
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
        displaced_minister: str = "",
        displaced_effect: Optional[Dict[str, Any]] = None,
        secret_order_id: int = 0,
        secret_order_assignee: str = "",
        secret_order_effect: Optional[Dict[str, Any]] = None,
        chat_turn_id: int = 0,
    ) -> Dict[str, Any]:
        character = self.session._character(minister_name)
        self.chat_history[minister_name].append({"role": "minister", "content": answer})
        if minister_name not in self.session.temporary_characters:
            message_id = self.db.append_chat_message(minister_name, self.state.turn, "minister", answer)
            if chat_turn_id:
                self.db.update_chat_turn_messages(chat_turn_id, minister_message_id=message_id)
        return {
            "minister": minister_name,
            "minister_profile": self.public_character(character),
            "answer": answer,
            "history": self.chat_history[minister_name],
            "court_action": court_action,
            "next_minister": next_minister,
            "proposed_directive": proposed_directive,
            "appointed_minister": appointed_minister,
            "registered_minister": registered_minister,
            "displaced_minister": displaced_minister,
            "displaced_effect": displaced_effect or {},
            "secret_order_id": secret_order_id or 0,
            "secret_order_assignee": secret_order_assignee,
            "secret_order_effect": secret_order_effect or {},
            "directives": [self.directive_payload(row) for row in self.directive_rows()],
            "pending_count": self.session.pending_count(),
            "suggestions": self.suggestions_for(character),
            "can_undo_last_chat": self.can_undo_last_chat(minister_name),
        }

    def chat(self, minister_name: str, message: str) -> Dict[str, Any]:
        if minister_name not in self.content.characters and minister_name not in self.session.temporary_characters:
            raise HTTPException(status_code=404, detail=f"未找到大臣：{minister_name}")
        text = message.strip()
        if not text:
            raise HTTPException(status_code=400, detail="问话不能为空。")
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
        try:
            result = self.session.chat(minister_name, text, source_chat_turn_id=chat_turn_id)
            self._record_chat_rollback_items(chat_turn_id, before_snapshot)
        except Exception:
            if chat_turn_id:
                self.db.abort_chat_turn(chat_turn_id, before_snapshot)
            self.chat_history[minister_name] = self.chat_history.get(minister_name, [])[:history_before_len]
            raise
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
        return self._chat_payload(
            minister_name, result.answer,
            court_action=result.court_action, next_minister=result.next_minister,
            proposed_directive=proposed, appointed_minister=result.appointed_minister,
            registered_minister=result.registered_minister,
            displaced_minister=result.displaced_minister,
            displaced_effect=result.displaced_effect,
            secret_order_id=result.secret_order_id,
            secret_order_assignee=result.secret_order_assignee,
            secret_order_effect=result.secret_order_effect,
            chat_turn_id=chat_turn_id,
        )

    def chat_stream(self, minister_name: str, message: str) -> Iterator[Dict[str, Any]]:
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
        character = self.session._character(minister_name)
        chunks: List[str] = []
        try:
            if self.session.registry is None:
                raise RuntimeError("GameSession.begin_turn() 未调用。")
            agent = self.session.registry.get(character)
            augmented, dialogue_prep = self.session.prepare_chat_run(
                character,
                text,
                source_chat_turn_id=chat_turn_id,
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
            self.session.record_dialogue_after_chat(
                character,
                text,
                answer,
                dialogue_prep,
                source_chat_turn_id=chat_turn_id,
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
                displaced_minister=displaced,
                displaced_effect=displaced_effect,
                secret_order_id=secret_order_id,
                secret_order_assignee=secret_order_assignee,
                secret_order_effect=secret_order_effect,
                chat_turn_id=chat_turn_id,
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

    def suggestions_for(self, character: Character) -> List[Dict[str, str]]:
        suggestions = [
            {"label": "问在办事项", "text": "当前在办的事项里，哪几件轻重缓急最该先理？"},
            {"label": "问阻力", "text": "眼下推进朝政，最大的阻力来自哪一方？"},
            {"label": "拟旨", "text": "拟旨如下：", "prefix": True},
            {"label": "下密令", "text": "密令如下：", "prefix": True},
        ]
        skill_ids = set(available_skill_ids(character, self.db))
        if "check_treasury" in skill_ids:
            suggestions.insert(1, {"label": "查钱粮", "text": "太仓和内库实数如何？本月哪些钱最急？"})
        if "check_military" in skill_ids or "front_line_plan" in skill_ids or "strategic_review" in skill_ids:
            suggestions.insert(1, {"label": "查驻军", "text": "查一下关宁军、京营和陕西边军的士气、欠饷与补给。"})
        if "secret_investigation" in skill_ids:
            suggestions.insert(1, {"label": "密查", "text": "哪些账册和人物最该先密查？"})
        return suggestions[:6]


def sse_event(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


web_game: Optional[WebGame] = None  # 懒加载：菜单页点「新游戏/继续/加载存档」才实例化
app = FastAPI(title="Ming Salvage MVP Web")


def get_game() -> WebGame:
    """游戏路由统一入口。未开局 → 409 让前端跳回菜单页。"""
    if web_game is None:
        raise HTTPException(status_code=409, detail="尚未开局，请回菜单选择新游戏/继续/加载存档。")
    return web_game


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


def _main_db_campaign_id() -> str:
    db_path = os.environ.get("MING_SIM_DB", "") or user_data_path("ming_sim.db")
    if not os.path.isabs(db_path):
        db_path = str(user_data_dir() / db_path)
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


def _scan_saves() -> List[Dict[str, Any]]:
    """扫存档目录，独立于 WebGame 实例（菜单页无 game 也要能列）。
    不再按 campaign 过滤——所有局的存档都列出，由前端按局分组。"""
    saves_dir = user_data_path("saves")
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


def _scan_campaigns() -> List[Dict[str, Any]]:
    """把存档按局（campaign_id）分组，当前主 DB 的局标 current=True。
    手动存档（无 campaign_id）归到一个 manual 组。每组按 mtime 倒序，组也按最新档倒序。"""
    saves = _scan_saves()
    cur_campaign = _main_db_campaign_id()
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


def _has_main_db() -> bool:
    """主 DB 文件是否存在 → 决定「继续」按钮可不可点。"""
    db_path = os.environ.get("MING_SIM_DB", "") or user_data_path("ming_sim.db")
    if not os.path.isabs(db_path):
        db_path = str(user_data_dir() / db_path)
    return os.path.isfile(db_path)


@app.get("/api/menu/status")
async def api_menu_status() -> Dict[str, Any]:
    """菜单页状态：API key 是否配好、上次主 DB 是否存在、存档列表。"""
    runtime = load_runtime_llm()
    has_api_key = bool(runtime.get("api_key") or os.environ.get("OPENAI_API_KEY"))
    return {
        "has_api_key": has_api_key,
        "has_running_game": web_game is not None,
        "has_main_db": _has_main_db(),
        "saves": _scan_saves(),
        "campaigns": _scan_campaigns(),
        "current_campaign": _main_db_campaign_id(),
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
    global web_game
    if web_game is not None:
        try:
            web_game.session.close()
        except Exception:
            pass
        web_game = None
    try:
        web_game = WebGame(fresh=True)
    except LLMUnavailable as exc:
        raise HTTPException(status_code=412, detail=_llm_error_detail(exc))
    return {"state": web_game.state_payload()}


@app.post("/api/menu/continue")
async def api_menu_continue() -> Dict[str, Any]:
    """继续：用上次主 DB 启动 WebGame。"""
    global web_game
    if not _has_main_db():
        raise HTTPException(status_code=404, detail="无上次进度可继续，请先新游戏或加载存档。")
    try:
        web_game = WebGame(fresh=False)
    except LLMUnavailable as exc:
        raise HTTPException(status_code=412, detail=_llm_error_detail(exc))
    return {"state": web_game.state_payload()}


@app.post("/api/menu/load_save/{name}")
async def api_menu_load_save(name: str) -> Dict[str, Any]:
    """从存档启动：先启动空 WebGame（fresh）→ 调 load_save 热替换主 DB。"""
    global web_game
    try:
        web_game = WebGame(fresh=False)  # 先有 session 才能 load_save
    except LLMUnavailable as exc:
        raise HTTPException(status_code=412, detail=_llm_error_detail(exc))
    web_game.load_save(name)
    return {"state": web_game.state_payload()}


@app.delete("/api/menu/saves/{name}")
async def api_menu_delete_save(name: str) -> Dict[str, Any]:
    """菜单页删存档：不依赖 WebGame 实例，直接删文件系统里的 <name>.db。
    与 WebGame.delete_save 同名校验，返回刷新后的 campaigns。"""
    cleaned = "".join(c for c in name.strip() if c.isalnum() or c in "._-")
    if not cleaned or cleaned.startswith("."):
        raise HTTPException(status_code=400, detail="存档名非法。仅允许字母/数字/._- ")
    target = os.path.join(user_data_path("saves"), f"{cleaned}.db")
    if not os.path.isfile(target):
        raise HTTPException(status_code=404, detail="存档不存在。")
    os.remove(target)
    return {"saves": _scan_saves(), "campaigns": _scan_campaigns()}


@app.post("/api/menu/exit_to_menu")
async def api_menu_exit() -> Dict[str, Any]:
    """退回菜单：关 session 但不删 DB。"""
    global web_game
    if web_game is not None:
        try:
            web_game.session.close()
        except Exception:
            pass
        web_game = None
    return {"ok": True}


@app.post("/api/menu/shutdown")
async def api_menu_shutdown() -> Dict[str, Any]:
    """退出整个游戏：关 session + 终止服务进程。前端收响应后自行关页面。"""
    import os as _os
    import signal as _signal
    import threading as _threading
    global web_game
    if web_game is not None:
        try:
            web_game.session.close()
        except Exception:
            pass
        web_game = None
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


@app.get("/api/organizations")
async def api_organizations() -> Dict[str, Any]:
    return get_game().organization_payload()


@app.post("/api/organizations/custom")
async def api_add_custom_institution(body: CustomInstitutionRequest) -> Dict[str, Any]:
    game = get_game()
    item = game.add_custom_institution(body.name, body.category, body.mandate, body.slots)
    return {
        "message": f"已增设{item['name']}，空缺已进入组织图。",
        "organizations": game.organization_payload(),
    }


@app.post("/api/recruitment/exam")
async def api_recruit_exam() -> Dict[str, Any]:
    return get_game().recruit_exam_official()


@app.post("/api/recruitment/eunuch")
async def api_recruit_eunuch() -> Dict[str, Any]:
    return get_game().recruit_eunuch()


@app.post("/api/recruitment/recommend")
async def api_recommend_hidden() -> Dict[str, Any]:
    return get_game().recommend_hidden_official()


@app.post("/api/recruitment/castrate")
async def api_castrate_official(body: CastrateRequest) -> Dict[str, Any]:
    return get_game().castrate_official(body.name, force=body.force)


@app.post("/api/recruitment/emancipate")
async def api_emancipate_eunuch(body: CastrateRequest) -> Dict[str, Any]:
    return get_game().emancipate_eunuch(body.name, force=body.force)


@app.get("/api/secret_orders")
async def api_secret_orders(status: str = "") -> Dict[str, Any]:
    """列出密令。status 为空返回全部，否则按 active/done/failed 过滤。"""
    orders = get_game().db.list_secret_orders(status=status or None)
    return {"orders": orders}


@app.get("/api/agreements")
async def api_agreements(minister_name: str = "") -> Dict[str, Any]:
    """列出奏对协议与履约 todo。"""
    return {"agreements": get_game().agreement_payload(minister_name=minister_name)}


@app.get("/api/conversation_goals")
async def api_conversation_goals(minister_name: str = "") -> Dict[str, Any]:
    """列出奏对目的与心理握手进度。"""
    return {"conversation_goals": get_game().conversation_goal_payload(minister_name=minister_name)}


@app.post("/api/conversation_goals/{goal_id}/abandon")
async def api_abandon_conversation_goal(goal_id: int, body: ConversationGoalAbandonRequest) -> Dict[str, Any]:
    game = get_game()
    try:
        goal = game.db.abandon_conversation_goal(game.state, goal_id, reason=body.reason or "玩家主动放弃")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return {"goal": goal, "state": game.state_payload()}


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
    return {"nodes": get_game().map_nodes()}


@app.get("/api/buildings")
async def api_buildings(region_id: str = "") -> Dict[str, Any]:
    return {"buildings": get_game().db.building_payload(region_id)}


@app.get("/api/characters/{character_name}")
async def api_character_detail(character_name: str) -> Dict[str, Any]:
    character = get_game().content.characters.get(character_name)
    if character is None:
        raise HTTPException(status_code=404, detail=f"未找到人物：{character_name}")
    return {"character": get_game().public_character(character)}


@app.post("/api/favorites/{minister_name}")
async def api_add_favorite(minister_name: str) -> Dict[str, Any]:
    if minister_name not in get_game().content.characters:
        raise HTTPException(status_code=404, detail=f"未找到：{minister_name}")
    get_game().favorites.add(minister_name)
    get_game().db.kv_set("favorites", json.dumps(sorted(get_game().favorites)))
    return {"favorites": sorted(get_game().favorites)}


@app.delete("/api/favorites/{minister_name}")
async def api_remove_favorite(minister_name: str) -> Dict[str, Any]:
    get_game().favorites.discard(minister_name)
    get_game().db.kv_set("favorites", json.dumps(sorted(get_game().favorites)))
    return {"favorites": sorted(get_game().favorites)}


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


@app.get("/api/ministers/{minister_name}/chat")
async def api_chat_history(minister_name: str) -> Dict[str, Any]:
    _require_active_minister(minister_name)
    character = get_game().session._character(minister_name)
    return {
        "minister": get_game().public_character(character),
        "history": get_game().chat_history.get(minister_name, []),
        "suggestions": get_game().suggestions_for(character),
        "can_undo_last_chat": get_game().can_undo_last_chat(minister_name),
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
    return {"order_id": order_id, "minister_name": minister_name, "title": title, "status": "active", "effect": effect}


@app.post("/api/ministers/{minister_name}/chat")
async def api_chat(minister_name: str, request: ChatRequest) -> Dict[str, Any]:
    _require_active_minister(minister_name)
    return get_game().chat(minister_name, request.message)


@app.post("/api/ministers/{minister_name}/chat/undo")
async def api_undo_chat(minister_name: str) -> Dict[str, Any]:
    return get_game().undo_last_chat(minister_name)


@app.post("/api/ministers/{minister_name}/chat/stream")
async def api_chat_stream(minister_name: str, request: ChatRequest) -> StreamingResponse:
    _require_active_minister(minister_name)
    async def generate() -> AsyncIterator[str]:
        for item in get_game().chat_stream(minister_name, request.message):
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
async def api_create_directive(request: DirectiveRequest) -> Dict[str, Any]:
    if not request.text.strip():
        raise HTTPException(status_code=400, detail="指令内容不能为空。")
    try:
        dv = get_game().session.add_directive(request.text.strip(), notes=request.notes)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return {
        "directive": {"id": dv.id, "text": dv.text, "status": dv.status},
        "directives": [get_game().directive_payload(item) for item in get_game().directive_rows()],
    }


@app.patch("/api/directives/{directive_id}")
async def api_update_directive(directive_id: int, request: DirectivePatch) -> Dict[str, Any]:
    rows = get_game().directive_rows()
    row = next((item for item in rows if int(item["id"]) == directive_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail="未找到草案。")
    text = request.text if request.text is not None else str(row["text"])
    if not text.strip():
        raise HTTPException(status_code=400, detail="指令内容不能为空。")
    try:
        get_game().session.update_directive(directive_id, text.strip())
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return {"directives": [get_game().directive_payload(item) for item in get_game().directive_rows()]}


@app.delete("/api/directives/{directive_id}")
async def api_delete_directive(directive_id: int) -> Dict[str, Any]:
    try:
        get_game().session.delete_directive(directive_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return {"directives": [get_game().directive_payload(item) for item in get_game().directive_rows()]}


@app.post("/api/directives/{directive_id}/confirm")
async def api_confirm_directive(directive_id: int) -> Dict[str, Any]:
    """大臣拟旨经皇帝核定：pending → draft。"""
    try:
        get_game().session.confirm_directive(directive_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return {
        "directives": [get_game().directive_payload(item) for item in get_game().directive_rows()],
        "pending_count": get_game().session.pending_count(),
    }


@app.post("/api/directives/{directive_id}/reject")
async def api_reject_directive(directive_id: int) -> Dict[str, Any]:
    """皇帝驳回大臣拟旨：pending → rejected。"""
    try:
        get_game().session.reject_directive(directive_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return {
        "directives": [get_game().directive_payload(item) for item in get_game().directive_rows()],
        "pending_count": get_game().session.pending_count(),
    }


@app.post("/api/decree/write")
async def api_write_decree() -> Dict[str, Any]:
    try:
        decree = get_game().session.write_decree()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return {"decree": decree}


class EditDecreeRequest(BaseModel):
    decree: str


@app.patch("/api/decree")
async def api_edit_decree(body: EditDecreeRequest) -> Dict[str, Any]:
    """皇帝手动改定诏书正文（拟诏后、颁诏前）。"""
    try:
        decree = get_game().session.set_decree(body.decree)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return {"decree": decree}


class IssueDecreeRequest(BaseModel):
    # 作弊控制台（Ctrl+~）下的强制结算项；一次性，颁诏即用。普通颁诏留空。
    cheat: str = ""


@app.post("/api/decree/issue")
async def api_issue_decree(body: IssueDecreeRequest = IssueDecreeRequest()) -> Dict[str, Any]:
    """非流式颁诏（保留兼容）。前端默认走 /api/decree/issue/stream。"""
    game = get_game()
    portrait_before = game.portrait_generation_signatures()
    try:
        report = game.session.resolve_turn(cheat_directive=body.cheat)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    decree = game.session.last_decree
    game.refresh_turn()
    portrait_jobs = game.queue_portrait_generation_for_signature_changes(portrait_before, "月末职服变化")
    return {"decree": decree, "report": report, "state": game.state_payload(), "portrait_jobs": portrait_jobs}


@app.post("/api/decree/issue/stream")
async def api_issue_decree_stream(body: IssueDecreeRequest = IssueDecreeRequest()) -> StreamingResponse:
    """流式颁诏：推演过程（阶段/思考/正文）实时 SSE 推给前端。

    resolve_turn 是阻塞的同步调用，且 on_event 是 push 式回调。
    用 worker 线程跑 resolve_turn，回调把事件投进 Queue；
    async generator 从 Queue 拉事件转成 SSE。
    """
    ev_queue: "queue.Queue[tuple[str, Any]]" = queue.Queue()

    def on_event(kind: str, data: str) -> None:
        ev_queue.put((kind, data))

    def worker() -> None:
        try:
            game = get_game()
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
    return {"candidates": candidates}


@app.post("/api/consorts/{name}/select")
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
    return {"selected": game.public_character(consort)}


@app.post("/api/consorts/{name}/action")
async def api_consort_action(name: str, body: ConsortActionRequest) -> Dict[str, Any]:
    return get_game().perform_consort_action(name, body.action)


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
    get_game().load_save(name)
    return {"state": get_game().state_payload()}


@app.post("/api/game/reset")
async def api_reset_game() -> Dict[str, Any]:
    """清空主 DB 重开新局。存档目录保留。"""
    get_game().reset_game()
    return {"state": get_game().state_payload()}


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


def _find_portrait_file(name: str) -> Optional[str]:
    """找该人物已存在的自定义立绘文件（任一扩展名），无则 None。"""
    for ext in _PORTRAIT_EXT.values():
        path = os.path.join(UPLOAD_PORTRAIT_DIR, f"{name}.{ext}")
        if os.path.exists(path):
            return path
    return None


def _static_portrait_exists(filename: str) -> bool:
    """检查随包/源码静态立绘是否存在。"""
    clean = os.path.basename(str(filename or ""))
    if not clean:
        return False
    for base in (
        bundled_path("web", "public", "portraits"),
        bundled_path("web", "dist", "portraits"),
    ):
        if os.path.exists(os.path.join(str(base), clean)):
            return True
    return False


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
    return {"job": job, "character": game.public_character(character) if character else None}


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
    character = get_game().find_character(name)
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
    os.makedirs(UPLOAD_PORTRAIT_DIR, exist_ok=True)
    # 后处理成功后再清旧图，避免失败上传导致原立绘丢失。
    old = _find_portrait_file(name)
    if old is not None:
        os.remove(old)
    with open(os.path.join(UPLOAD_PORTRAIT_DIR, f"{name}.png"), "wb") as fh:
        fh.write(processed)
    get_game().set_custom_portrait(name, f"{CUSTOM_PORTRAIT_PREFIX}{name}")
    return {"name": name, "portrait_id": f"{CUSTOM_PORTRAIT_PREFIX}{name}"}


@app.delete("/api/consorts/{name}/portrait")
async def api_delete_portrait(name: str) -> Dict[str, Any]:
    character = get_game().find_character(name)
    if character is None:
        raise HTTPException(status_code=404, detail="未找到该人物")
    old = _find_portrait_file(name)
    if old is not None:
        os.remove(old)
    # 复位 portrait_id：清空 → 前端回落到池图（add/seed 时会按 office_type 再分配）。
    get_game().set_custom_portrait(name, "")
    return {"name": name, "portrait_id": ""}


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
    path = _find_portrait_file(name)
    if path is None:
        raise HTTPException(status_code=404, detail="无自定义立绘")
    return FileResponse(path)


# ── 调试台：直接读写核心表 ─────────────────────────────────────
@app.get("/api/admin/tables")
async def api_admin_tables() -> Dict[str, Any]:
    return {"tables": list(get_game().db.ADMIN_TABLES.keys())}


@app.get("/api/admin/table/{table}")
async def api_admin_table(table: str) -> Dict[str, Any]:
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
    pk_value = payload.get("pk_value")
    if pk_value in (None, ""):
        raise HTTPException(status_code=400, detail="缺 pk_value")
    try:
        return {"deleted": get_game().db.admin_delete(table, pk_value)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/admin")
async def admin_page():
    return HTMLResponse(_ADMIN_HTML)


if os.path.isdir(WEB_DIST):
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")


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
