"""LLM gates for rule-layer state mutations.

These audits sit between deterministic lifecycle timing and irreversible save
mutations. Regex may collect candidates, but the final write decision belongs
to the semantic gate when LLM auditing is enabled.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from agno.agent import Agent

from ming_sim.agents import parse_agent_json, run_agent_text
from ming_sim.llm_config import for_role as llm_for_role
from ming_sim.llm_model import create_chat_model
from ming_sim.models import GameState, LLMConfig
from ming_sim.pipeline_registry import llm_output_token_budget

CONFIDENCE_FLOOR = 70


def _compact(value: object, limit: int = 400) -> str:
    raw = str(value or "").strip()
    raw = " ".join(raw.split())
    if len(raw) <= limit:
        return raw
    return raw[: max(1, limit - 1)] + "…"


def _list_strings(value: object, *, limit: int = 8, item_limit: int = 80) -> List[str]:
    if not isinstance(value, list):
        return []
    out: List[str] = []
    for item in value:
        text = _compact(item, item_limit)
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _confidence(value: object) -> int:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        parsed = 0.0
    if 0 < parsed <= 1:
        parsed *= 100
    return max(0, min(100, int(round(parsed))))


def _call_fake(audit_client: object, phase: str, payload: Dict[str, object]) -> Optional[Dict[str, object]]:
    if audit_client is None:
        return None
    if callable(audit_client):
        data = audit_client(phase, payload)
    else:
        method = getattr(audit_client, phase, None)
        if method is None:
            method = getattr(audit_client, f"{phase}_audit", None)
        data = method(payload) if callable(method) else None
    return data if isinstance(data, dict) else None


def _normalize_directive_castration_execution(data: Dict[str, object]) -> Dict[str, object]:
    confidence = _confidence(data.get("confidence"))
    target = _compact(data.get("target_name") or data.get("target"), 80)
    allow = bool(data.get("allow")) and bool(target) and confidence >= CONFIDENCE_FLOOR
    return {
        "allow": allow,
        "target_name": target if allow else "",
        "confidence": confidence,
        "trigger_quote": _compact(data.get("trigger_quote"), 160),
        "public_hint": _compact(data.get("public_hint"), 200),
        "private_reason": _compact(data.get("private_reason") or data.get("reason"), 520),
        "raw": data,
    }


DIRECTIVE_CASTRATION_EXECUTION_PROMPT = """
你是明末历史策略游戏的“旨意强制净身执行审计官”。你只输出 JSON，不写 Markdown。
任务：阅读一道已经到期办结的旨意，判断是否允许执行“把某个具体人物净身/宫刑/没入内廷”的不可逆身份处置，并指出目标人物。

核心原则：
- 这是语义判定，不按“净身、宫刑、净军房、入内廷”等词机械触发。
- allow=true 只用于旨意本身明确命令对 target_name 执行净身、宫刑、腐刑、去势、发净军房、没入内廷为奴等身份处置。
- 普通调查、制度改革、查净军房弊端、询问旧例、传闻、假设、比较方案、禁止执行、暂缓、不许惊动净军房，必须 allow=false。
- 目标人物只能从 candidate_names 中选择；不要把承办人 assignee 当成目标，除非旨意明确命令处置承办人本人。
- 如果一句话只是“查某人净身旧例/查净军房/不要把某人净身/若某人净身会怎样”，allow=false。
- trigger_quote 必须引用原旨意中最能证明“执行净身”的短句；没有证据 allow=false。

JSON 字段：
{
  "allow": false,
  "target_name": "候选人物姓名",
  "trigger_quote": "原文短句",
  "public_hint": "一句玩家可见提示",
  "private_reason": "审计理由",
  "confidence": 0
}
""".strip()


def _candidate_character_rows(db: Any, names: List[str]) -> List[Dict[str, object]]:
    if not names:
        return []
    rows: List[Dict[str, object]] = []
    for name in names[:8]:
        try:
            row = db.conn.execute(
                "SELECT name, office, office_type, faction, status, power_id FROM characters WHERE name=?",
                (name,),
            ).fetchone()
        except Exception:
            row = None
        if row is None:
            rows.append({"name": name})
            continue
        rows.append({
            "name": str(row["name"] or ""),
            "office": str(row["office"] or ""),
            "office_type": str(row["office_type"] or ""),
            "faction": str(row["faction"] or ""),
            "status": str(row["status"] or ""),
            "power_id": str(row["power_id"] or ""),
        })
    return rows


def directive_castration_execution_audit(
    db: Any,
    state: GameState,
    decree_text: str,
    *,
    assignee: str = "",
    directive_id: int = 0,
    day: int = 0,
    candidate_names: Optional[List[str]] = None,
    llm_config: Optional[LLMConfig] = None,
    audit_client: object = None,
) -> Dict[str, object]:
    candidates = _list_strings(candidate_names or [], limit=8, item_limit=80)
    payload: Dict[str, object] = {
        "directive_id": int(directive_id or 0),
        "day": int(day or 0),
        "turn": int(getattr(state, "turn", 0) or 0),
        "year": int(getattr(state, "year", 0) or 0),
        "period": int(getattr(state, "period", 0) or 0),
        "decree_text": _compact(decree_text, 1600),
        "assignee": _compact(assignee, 80),
        "candidate_names": candidates,
        "candidate_characters": _candidate_character_rows(db, candidates),
    }
    try:
        fake = _call_fake(audit_client, "directive_castration_execution", payload)
        if fake is not None:
            return _normalize_directive_castration_execution(fake)
        if llm_config is None:
            return _normalize_directive_castration_execution({
                "allow": False,
                "target_name": "",
                "confidence": 0,
                "private_reason": "未配置 LLM，强制净身执行审计不落库。",
            })
        role_cfg = llm_for_role(llm_config, "dialogue_audit")
        agent = Agent(
            name="旨意强制净身执行审计",
            id="rule-audit-directive-castration-execution",
            session_id="rule-audit-directive-castration-execution",
            model=create_chat_model(
                role_cfg,
                temperature=0,
                max_tokens=llm_output_token_budget(
                    "llm.directive_castration_execution",
                    role_cfg.max_tokens,
                    minimum=800,
                ),
            ),
            instructions=[DIRECTIVE_CASTRATION_EXECUTION_PROMPT],
            markdown=False,
        )
        raw = run_agent_text(
            agent,
            json.dumps(payload, ensure_ascii=False, sort_keys=False),
            tag="rule-audit/directive-castration-execution",
        )
        data = parse_agent_json(raw, "旨意强制净身执行审计")
        return _normalize_directive_castration_execution(data)
    except Exception as exc:
        return _normalize_directive_castration_execution({
            "allow": False,
            "target_name": "",
            "confidence": 0,
            "private_reason": str(exc),
        })
