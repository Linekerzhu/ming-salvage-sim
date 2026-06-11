"""Runtime pipeline registry for engineering boundaries.

This module is intentionally dependency-light: importing it must not read
content, open SQLite, or initialize LLM clients. It records the project-level
contracts that frontend, web, admin, LLM, portrait, and mechanics pipelines
should obey as the codebase keeps growing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Literal, Optional, Tuple

PipelineKind = Literal["frontend", "web", "admin", "llm", "portrait", "mechanic", "data"]
FailurePolicy = Literal["fail_closed", "best_effort", "defer", "dry_run_required"]
RiskLevel = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class PipelineSpec:
    """Declarative contract for one runtime or tooling pipeline."""

    id: str
    kind: PipelineKind
    owner: str
    entrypoint: str
    reads: Tuple[str, ...] = ()
    writes: Tuple[str, ...] = ()
    llm_role: str = ""
    advanced_model: bool = False
    default_max_tokens: int = 0
    max_prompt_chars: int = 0
    timeout_seconds: float = 0.0
    failure_policy: FailurePolicy = "fail_closed"
    cache_policy: str = "none"
    risk: RiskLevel = "medium"
    notes: str = ""


def _spec(
    pipeline_id: str,
    *,
    kind: PipelineKind,
    owner: str,
    entrypoint: str,
    reads: Iterable[str] = (),
    writes: Iterable[str] = (),
    llm_role: str = "",
    advanced_model: bool = False,
    default_max_tokens: int = 0,
    max_prompt_chars: int = 0,
    timeout_seconds: float = 0.0,
    failure_policy: FailurePolicy = "fail_closed",
    cache_policy: str = "none",
    risk: RiskLevel = "medium",
    notes: str = "",
) -> PipelineSpec:
    return PipelineSpec(
        id=pipeline_id,
        kind=kind,
        owner=owner,
        entrypoint=entrypoint,
        reads=tuple(reads),
        writes=tuple(writes),
        llm_role=llm_role,
        advanced_model=advanced_model,
        default_max_tokens=max(0, int(default_max_tokens or 0)),
        max_prompt_chars=max(0, int(max_prompt_chars or 0)),
        timeout_seconds=max(0.0, float(timeout_seconds or 0)),
        failure_policy=failure_policy,
        cache_policy=cache_policy,
        risk=risk,
        notes=notes,
    )


_SPECS: Tuple[PipelineSpec, ...] = (
    _spec(
        "frontend.state_decoder",
        kind="frontend",
        owner="web/src/api/payloads.ts",
        entrypoint="normalizeGameState / decode*",
        reads=("HTTP compact field tables",),
        writes=("React view models",),
        failure_policy="fail_closed",
        cache_policy="browser memory",
        risk="medium",
        notes="字段表解码是前台和后端 payload 契约的唯一适配层。",
    ),
    _spec(
        "web.state_payload",
        kind="web",
        owner="web_app.WebGame",
        entrypoint="state_payload",
        reads=("GameSession.state", "GameDB", "GameContent"),
        writes=("HTTP /api/game/state",),
        failure_policy="fail_closed",
        cache_policy="per request",
        risk="high",
        notes="首屏轻量状态；厚数据必须走按需接口。",
    ),
    _spec(
        "web.character_detail",
        kind="web",
        owner="web_app.WebGame",
        entrypoint="public_character",
        reads=("characters", "npc_network", "dialogue goals", "skill grants"),
        writes=("HTTP /api/characters/{name}",),
        failure_policy="fail_closed",
        cache_policy="per request",
        risk="medium",
        notes="人物详情必须批量读取网络目标与技能授权，禁止 N+1 查询。",
    ),
    _spec(
        "admin.table_editor",
        kind="admin",
        owner="web_app.py + ming_sim.db.GameDB",
        entrypoint="/api/admin/table/{table}",
        reads=("ADMIN_TABLES whitelist", "SQLite schema"),
        writes=("whitelisted SQLite tables",),
        failure_policy="dry_run_required",
        cache_policy="none",
        risk="high",
        notes="管理平台必须受管理员鉴权和表白名单保护；后续数据导入先 dry-run。",
    ),
    _spec(
        "llm.minister_dialogue",
        kind="llm",
        owner="ming_sim.session.GameSession",
        entrypoint="chat_minister",
        reads=("NPC profile", "network", "memory", "active goals", "tools"),
        writes=("chat_messages", "pending directives", "secret orders"),
        llm_role="minister",
        default_max_tokens=0,
        failure_policy="fail_closed",
        cache_policy="agno session",
        risk="high",
        notes="召对是可存档交互，不能伪造 LLM 成功。",
    ),
    _spec(
        "llm.dialogue_pre_audit",
        kind="llm",
        owner="ming_sim.dialogue_audit",
        entrypoint="pre_dialogue_audit",
        reads=("recent dialogue", "goals", "NPC behavior context"),
        writes=("PreparedDialogue preview",),
        llm_role="dialogue_audit",
        advanced_model=True,
        default_max_tokens=1800,
        failure_policy="defer",
        cache_policy="none",
        risk="medium",
        notes="失败时不落档，交给玩家可见提示。",
    ),
    _spec(
        "llm.dialogue_post_audit",
        kind="llm",
        owner="ming_sim.dialogue_audit",
        entrypoint="post_dialogue_audit",
        reads=("player text", "NPC answer", "pre audit", "agreement ledger"),
        writes=("conversation goals", "negotiation agreements", "stance notes"),
        llm_role="dialogue_audit",
        advanced_model=True,
        default_max_tokens=3000,
        failure_policy="defer",
        cache_policy="none",
        risk="high",
        notes="握手/履约入账必须有结构化审计结果。",
    ),
    _spec(
        "llm.dialogue_condition_audit",
        kind="llm",
        owner="ming_sim.dialogue_audit",
        entrypoint="review_goal_conditions_audit",
        reads=("goal", "decree/report evidence"),
        writes=("goal condition state", "agreement ledger"),
        llm_role="dialogue_audit",
        advanced_model=True,
        default_max_tokens=2200,
        failure_policy="defer",
        cache_policy="none",
        risk="high",
    ),
    _spec(
        "llm.decree_writer",
        kind="llm",
        owner="ming_sim.agents",
        entrypoint="create_decree_writer_agent",
        reads=("pending directives", "closed secret order evidence"),
        writes=("formal decree text",),
        llm_role="decree_writer",
        default_max_tokens=2400,
        failure_policy="fail_closed",
        cache_policy="none",
        risk="medium",
        notes="拟旨只负责润色合并，结构化草案已经先落库。",
    ),
    _spec(
        "llm.season_simulator",
        kind="llm",
        owner="ming_sim.agents",
        entrypoint="create_season_simulator_agent",
        reads=("simulator_payload", "chapter memories", "directive context"),
        writes=("narrative report",),
        llm_role="simulator",
        advanced_model=True,
        default_max_tokens=0,
        failure_policy="fail_closed",
        cache_policy="provider prefix cache",
        risk="high",
    ),
    _spec(
        "llm.score_extractor",
        kind="llm",
        owner="ming_sim.agents",
        entrypoint="create_score_extractor_module_agent",
        reads=("narrative report", "simulator_payload", "extractor context"),
        writes=("structured deltas",),
        llm_role="extractor",
        advanced_model=True,
        default_max_tokens=0,
        failure_policy="fail_closed",
        cache_policy="provider prefix cache",
        risk="high",
    ),
    _spec(
        "llm.agreement_review",
        kind="llm",
        owner="ming_sim.agents",
        entrypoint="create_agreement_reviewer_agent",
        reads=("agreement ledger", "decree/report evidence"),
        writes=("agreement task reviews",),
        llm_role="dialogue_audit",
        advanced_model=True,
        default_max_tokens=5000,
        failure_policy="defer",
        cache_policy="none",
        risk="high",
    ),
    _spec(
        "llm.chapter_memory",
        kind="llm",
        owner="ming_sim.agents",
        entrypoint="create_chapter_memory_agent",
        reads=("decree", "report", "applied changes"),
        writes=("chapter_memories",),
        llm_role="chapter_memory",
        default_max_tokens=1600,
        failure_policy="best_effort",
        cache_policy="none",
        risk="low",
        notes="章节记忆失败不应阻断主结算，输出预算保持紧凑。",
    ),
    _spec(
        "llm.json_sanitizer",
        kind="llm",
        owner="ming_sim.agents",
        entrypoint="create_json_sanitizer_agent",
        reads=("raw malformed JSON",),
        writes=("repaired JSON string",),
        llm_role="json_sanitizer",
        default_max_tokens=4000,
        failure_policy="fail_closed",
        cache_policy="none",
        risk="medium",
    ),
    _spec(
        "llm.ending_summary",
        kind="llm",
        owner="ming_sim.agents",
        entrypoint="create_ending_summary_agent",
        reads=("full timeline", "ending status"),
        writes=("ending summary text",),
        llm_role="ending_summary",
        default_max_tokens=3200,
        failure_policy="best_effort",
        cache_policy="none",
        risk="low",
    ),
    _spec(
        "portrait.dna_sheet",
        kind="portrait",
        owner="ming_sim.portraits",
        entrypoint="build_portrait_spec / nano_banana_generate_png",
        reads=("Character", "GameState", "wardrobe references"),
        writes=("portrait_assets kind=dna",),
        failure_policy="best_effort",
        cache_policy="asset_id idempotency",
        risk="medium",
        notes="DNA 图失败只记录状态，不阻断主游戏。",
    ),
    _spec(
        "portrait.character_cutout",
        kind="portrait",
        owner="web_app.WebGame",
        entrypoint="queue_portrait_generation",
        reads=("PortraitSpec", "portrait_assets", "reference images"),
        writes=("portrait_assets kind=portrait", "characters.portrait_id"),
        failure_policy="best_effort",
        cache_policy="asset_id idempotency",
        risk="medium",
        notes="外部图片 API 是可选能力；失败不得破坏已有可用头像。",
    ),
    _spec(
        "mechanic.directive_resolution",
        kind="mechanic",
        owner="ming_sim.decree",
        entrypoint="resolve_directives",
        reads=("directives", "state", "DB evidence", "simulator/extractor outputs"),
        writes=("metrics", "regions", "armies", "characters", "issues", "logs"),
        failure_policy="fail_closed",
        cache_policy="none",
        risk="high",
    ),
    _spec(
        "mechanic.bureaucracy_assessment",
        kind="mechanic",
        owner="ming_sim.bureaucracy",
        entrypoint="directive_execution_assessments",
        reads=("directive actors", "NPC relationship/personality", "organization slots"),
        writes=("directive_context",),
        failure_policy="fail_closed",
        cache_policy="per turn",
        risk="medium",
    ),
    _spec(
        "mechanic.fixed_period_flows",
        kind="mechanic",
        owner="ming_sim.flows",
        entrypoint="apply_fixed_period_flows",
        reads=("fiscal_config", "economy_accounts", "GameState"),
        writes=("economy_accounts", "ledger/logs"),
        failure_policy="fail_closed",
        cache_policy="none",
        risk="high",
    ),
    _spec(
        "data.npc_foundation_generation",
        kind="data",
        owner="scripts/generate_npc_foundation_from_master.py",
        entrypoint="main",
        reads=("master_data.sqlite views",),
        writes=("content/characters.json", "content/npc_network.json"),
        failure_policy="fail_closed",
        cache_policy="offline generated JSON",
        risk="medium",
        notes="外部 NPC 本体库只在离线生成期使用，运行时只消费 JSON。",
    ),
)

PIPELINE_REGISTRY: Dict[str, PipelineSpec] = {spec.id: spec for spec in _SPECS}
if len(PIPELINE_REGISTRY) != len(_SPECS):  # pragma: no cover - import-time invariant
    raise RuntimeError("duplicate pipeline id in PIPELINE_REGISTRY")


def pipeline_spec(pipeline_id: str) -> PipelineSpec:
    """Return a pipeline spec by id with a clear error for missing contracts."""
    try:
        return PIPELINE_REGISTRY[pipeline_id]
    except KeyError as error:
        raise KeyError(f"unknown pipeline contract: {pipeline_id}") from error


def pipeline_specs(kind: Optional[PipelineKind] = None) -> Tuple[PipelineSpec, ...]:
    """Return specs sorted by id, optionally filtered by kind."""
    specs = PIPELINE_REGISTRY.values()
    if kind is not None:
        specs = [spec for spec in specs if spec.kind == kind]
    return tuple(sorted(specs, key=lambda spec: spec.id))


def advanced_llm_roles() -> frozenset[str]:
    """LLM roles that should use the advanced model when configured."""
    return frozenset(
        spec.llm_role
        for spec in PIPELINE_REGISTRY.values()
        if spec.kind == "llm" and spec.advanced_model and spec.llm_role
    )


def llm_output_token_budget(
    pipeline_id: str,
    configured_limit: int,
    *,
    requested: Optional[int] = None,
    minimum: int = 0,
) -> int:
    """Resolve an output-token budget for one LLM pipeline.

    ``configured_limit`` is the runtime LLMConfig cap. A spec-level
    ``default_max_tokens`` tightens low-value outputs such as chapter memories,
    while simulator/extractor specs with default 0 still inherit the configured
    cap. ``minimum`` preserves hard floors used by existing prompts.
    """
    spec = pipeline_spec(pipeline_id)
    configured = max(0, int(configured_limit or 0))
    desired = requested if requested is not None else spec.default_max_tokens
    desired = int(desired or 0)
    if desired <= 0:
        desired = configured
    if configured > 0 and desired > 0:
        desired = min(desired, configured)
    if minimum > 0:
        desired = max(minimum, desired)
    return max(1, desired)
