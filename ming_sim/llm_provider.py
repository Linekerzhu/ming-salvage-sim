"""LLM provider 接缝（G4.1 可替换性）：集中 Agent 构造，使换 provider 改一处。

此前 agno 的 Agent(...) + OpenAIChat 构造散落 ~15 处（dialogue_audit / llm_model /
registry / rule_audit / shibi / scheduler / memories / endings / decree / simulation）。
换非 OpenAI 协议的 provider（如 Anthropic 原生 SDK）需改全部调用点。

本模块是"唯一构造入口"的起点（seam）：把 agno 构造收口到一处，配 LLM_BACKEND env
开关（默认 agno）。现有调用点可渐进迁移到此——本提交先接 dialogue_audit._agent 作为
参考实现，证明 seam 可行；其余调用点保持原样（功能不变），后续按需迁入。

设计：
- build_agent(...) 签名稳定，不暴露 agno 内部
- LLM_BACKEND=agno（默认）→ 走现有 agno + OpenAIChat 路径
- 未来 LLM_BACKEND=anthropic 等 → 在此一处分支，调用点无感
"""

from __future__ import annotations

import os
from typing import Any, Optional

from ming_sim.llm_config import LLMConfig
from ming_sim.llm_model import create_chat_model
from ming_sim.pipeline_registry import llm_output_token_budget


def _backend() -> str:
    """当前 LLM 后端。默认 agno；未来可扩展 anthropic / custom 等。"""
    return os.environ.get("LLM_BACKEND", "agno").strip().lower()


def build_agent(
    cfg: LLMConfig,
    *,
    pipeline_id: str,
    prompt: str,
    phase: str,
    agent_name: str = "",
    agent_id: str = "",
    max_tokens: Optional[int] = None,
    token_minimum: int = 0,
    temperature: float = 0.1,
    top_p: float = 0.7,
    enable_thinking: bool = False,
    force_json_output: bool = False,
    agno_db: Any = None,
    add_history_to_context: bool = False,
    markdown: bool = False,
) -> Any:
    """构造一个 LLM agent。当前唯一后端是 agno + OpenAIChat。

    换 provider 时：在 _backend() 分支处加新后端，调用点（用 build_agent 的）无感。
    本函数封装 create_chat_model 的 token 预算解析 + Agent 构造，避免散落。

    pipeline_id：pipeline_registry 的 id，用于解析 token 预算（default_max_tokens vs 配置上限）。
    """
    backend = _backend()
    if backend != "agno":
        # 未来后端在此分支。当前只有 agno，其它显式报错（避免静默走错路径）。
        raise NotImplementedError(
            f"LLM_BACKEND={backend!r} 未实现；当前仅支持 'agno'。"
            " 在 llm_provider.build_agent 中添加该后端的构造分支。"
        )
    # ── agno 后端（现有路径，原样保留语义）──
    from agno.agent import Agent  # 局部导入：保持模块导入轻（遵循 pipeline_registry 哲学）

    model = create_chat_model(
        cfg,
        temperature=temperature,
        top_p=top_p,
        max_tokens=llm_output_token_budget(
            pipeline_id,
            cfg.max_tokens,
            requested=max_tokens,
            minimum=token_minimum,
        ),
        enable_thinking=enable_thinking,
        force_json_output=force_json_output,
    )
    return Agent(
        name=agent_name or f"{pipeline_id}-{phase}",
        id=agent_id or f"{pipeline_id}-{phase}",
        model=model,
        instructions=[prompt],
        add_history_to_context=add_history_to_context,
        markdown=markdown,
    )
