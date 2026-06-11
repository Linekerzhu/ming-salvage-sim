"""Declarative runtime module registry for quasi-pluggable architecture.

The registry is deliberately static and import-light. It describes extension
surfaces, dependency order, and hot-swap policy without importing gameplay
modules or executing plugin code. This gives the project a safe stepping stone
toward hot-pluggable mechanics while keeping arbitrary runtime imports out of
the trusted path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Literal, Optional, Sequence, Tuple

from ming_sim.pipeline_registry import PIPELINE_REGISTRY

ModuleKind = Literal["frontend", "web", "admin", "service", "llm", "portrait", "mechanic", "data", "content"]
HotSwapPolicy = Literal["static", "restart_required", "session_reload", "declarative", "runtime_safe"]
RiskLevel = Literal["low", "medium", "high"]
HookTiming = Literal["before", "during", "after", "observe"]


@dataclass(frozen=True)
class HookSpec:
    """One named extension point exposed or consumed by a module."""

    name: str
    timing: HookTiming
    contract: str
    idempotent: bool = True
    order: int = 100


@dataclass(frozen=True)
class ModuleSpec:
    """Declarative contract for one replaceable project module."""

    id: str
    kind: ModuleKind
    owner: str
    entrypoint: str
    hooks: Tuple[HookSpec, ...] = ()
    depends_on: Tuple[str, ...] = ()
    pipelines: Tuple[str, ...] = ()
    reads: Tuple[str, ...] = ()
    writes: Tuple[str, ...] = ()
    hot_swap: HotSwapPolicy = "static"
    risk: RiskLevel = "medium"
    enabled_by_default: bool = True
    notes: str = ""


def _hook(
    name: str,
    *,
    timing: HookTiming,
    contract: str,
    idempotent: bool = True,
    order: int = 100,
) -> HookSpec:
    return HookSpec(name=name, timing=timing, contract=contract, idempotent=idempotent, order=order)


def _module(
    module_id: str,
    *,
    kind: ModuleKind,
    owner: str,
    entrypoint: str,
    hooks: Iterable[HookSpec] = (),
    depends_on: Iterable[str] = (),
    pipelines: Iterable[str] = (),
    reads: Iterable[str] = (),
    writes: Iterable[str] = (),
    hot_swap: HotSwapPolicy = "static",
    risk: RiskLevel = "medium",
    enabled_by_default: bool = True,
    notes: str = "",
) -> ModuleSpec:
    return ModuleSpec(
        id=module_id,
        kind=kind,
        owner=owner,
        entrypoint=entrypoint,
        hooks=tuple(hooks),
        depends_on=tuple(depends_on),
        pipelines=tuple(pipelines),
        reads=tuple(reads),
        writes=tuple(writes),
        hot_swap=hot_swap,
        risk=risk,
        enabled_by_default=enabled_by_default,
        notes=notes,
    )


_SPECS: Tuple[ModuleSpec, ...] = (
    _module(
        "content.static_content",
        kind="content",
        owner="ming_sim.content.GameContent",
        entrypoint="GameContent.load",
        hooks=(_hook("content.load", timing="during", contract="JSON content -> immutable runtime copy"),),
        reads=("content/*.json",),
        writes=("GameContent runtime copy",),
        hot_swap="session_reload",
        risk="high",
        notes="Runtime consumes generated JSON only; external source DBs stay offline.",
    ),
    _module(
        "data.npc_foundation",
        kind="data",
        owner="scripts/generate_npc_foundation_from_master.py",
        entrypoint="main",
        hooks=(_hook("data.generate.npc", timing="during", contract="master SQLite views -> content JSON"),),
        pipelines=("data.npc_foundation_generation",),
        reads=("master_data.sqlite",),
        writes=("content/characters.json", "content/npc_network.json"),
        hot_swap="restart_required",
        risk="medium",
        enabled_by_default=False,
    ),
    _module(
        "frontend.api_client",
        kind="frontend",
        owner="web/src/api/client.ts",
        entrypoint="api / streamJsonSse",
        hooks=(
            _hook("web.request.json", timing="during", contract="browser request -> typed JSON"),
            _hook("web.request.sse", timing="during", contract="browser request -> streamed JSON events"),
        ),
        reads=("HTTP endpoints",),
        writes=("frontend view models",),
        hot_swap="static",
        risk="medium",
    ),
    _module(
        "frontend.payload_decoder",
        kind="frontend",
        owner="web/src/api/payloads.ts",
        entrypoint="normalizeGameState / decode*",
        hooks=(_hook("web.payload.decode", timing="during", contract="compact field tables -> UI models"),),
        pipelines=("frontend.state_decoder",),
        depends_on=("frontend.api_client",),
        reads=("HTTP compact payloads",),
        writes=("React state shapes",),
        hot_swap="static",
        risk="medium",
    ),
    _module(
        "web.static_assets",
        kind="web",
        owner="web/scripts/prune-dist-assets.mjs",
        entrypoint="npm run build",
        hooks=(_hook("asset.release.prune", timing="after", contract="web/public -> clean web/dist"),),
        reads=("web/public",),
        writes=("web/dist",),
        hot_swap="declarative",
        risk="medium",
        notes="Source assets are authoritative; dist is disposable build output.",
    ),
    _module(
        "web.payload_contracts",
        kind="web",
        owner="ming_sim.web_payloads + ming_sim.web_route_contracts + ming_sim.web_payload_hooks",
        entrypoint="compact_* / *_payload / web_payload_route_specs / run_web_payload_hook",
        hooks=(_hook("web.payload.encode", timing="during", contract="runtime objects -> compact HTTP payloads"),),
        depends_on=("content.static_content",),
        pipelines=("web.state_payload", "web.character_detail"),
        reads=("GameState", "GameDB snapshots", "GameContent", "WebPayloadRouteSpec registry"),
        writes=("HTTP response dictionaries", "declared payload route contracts"),
        hot_swap="session_reload",
        risk="high",
    ),
    _module(
        "admin.table_editor",
        kind="admin",
        owner="web_app.py + ming_sim.db.GameDB",
        entrypoint="/api/admin/table/{table}",
        hooks=(_hook("admin.mutate.table", timing="during", contract="admin request -> whitelisted table diff"),),
        pipelines=("admin.table_editor",),
        reads=("ADMIN_TABLES whitelist", "SQLite schema"),
        writes=("whitelisted SQLite tables",),
        hot_swap="declarative",
        risk="high",
        notes="Admin mutation remains table-whitelisted and should grow toward dry-run services.",
    ),
    _module(
        "service.hook_runner",
        kind="service",
        owner="ming_sim.hook_runner",
        entrypoint="HookRunner / build_default_hook_runner",
        hooks=(
            _hook("hook.register", timing="during", contract="trusted callable -> declared hook handler"),
            _hook("hook.run", timing="during", contract="hook payload -> ordered handler chain"),
        ),
        reads=("MODULE_REGISTRY",),
        writes=("in-process handler table",),
        hot_swap="static",
        risk="medium",
        notes="Safe bridge toward quasi-hot-plug modules; never imports arbitrary code.",
    ),
    _module(
        "mechanic.agreement_ledger",
        kind="mechanic",
        owner="ming_sim.negotiation",
        entrypoint="agreement/task ledger helpers",
        hooks=(
            _hook("chat.agreement.record", timing="after", contract="dialogue audit -> durable agreement/task"),
            _hook("turn.agreement.review", timing="observe", contract="turn evidence -> agreement progress"),
        ),
        reads=("conversation_goals", "negotiation_agreements"),
        writes=("negotiation_agreements", "agreement_tasks"),
        hot_swap="session_reload",
        risk="high",
    ),
    _module(
        "mechanic.bureaucracy",
        kind="mechanic",
        owner="ming_sim.bureaucracy",
        entrypoint="directive_execution_assessments",
        hooks=(_hook("directive.assess", timing="before", contract="directive + actor + organization -> execution profile"),),
        depends_on=("content.static_content", "mechanic.agreement_ledger"),
        pipelines=("mechanic.bureaucracy_assessment",),
        reads=("NPC relationship/personality", "organization slots", "directive actors"),
        writes=("directive_context",),
        hot_swap="runtime_safe",
        risk="medium",
    ),
    _module(
        "mechanic.directive_resolution",
        kind="mechanic",
        owner="ming_sim.decree",
        entrypoint="resolve_directives",
        hooks=(_hook("turn.resolve", timing="during", contract="directives + extracted deltas -> state/log changes", idempotent=False),),
        depends_on=("mechanic.bureaucracy", "mechanic.agreement_ledger"),
        pipelines=("mechanic.directive_resolution",),
        reads=("directives", "extractor outputs", "GameState"),
        writes=("metrics", "regions", "armies", "characters", "issues", "logs"),
        hot_swap="session_reload",
        risk="high",
    ),
    _module(
        "mechanic.fixed_period_flows",
        kind="mechanic",
        owner="ming_sim.flows",
        entrypoint="apply_fixed_period_flows",
        hooks=(_hook("turn.fixed_flows", timing="after", contract="period close -> economy ledger"),),
        pipelines=("mechanic.fixed_period_flows",),
        reads=("economy accounts", "fiscal config", "GameState"),
        writes=("economy ledger", "account balances"),
        hot_swap="session_reload",
        risk="high",
    ),
    _module(
        "llm.dialogue_pipeline",
        kind="llm",
        owner="ming_sim.session + ming_sim.dialogue_audit",
        entrypoint="chat_minister / pre/post audits",
        hooks=(
            _hook("chat.prepare", timing="before", contract="character + memory + relationship -> prompt context"),
            _hook("chat.audit", timing="after", contract="conversation -> goals/agreements/stances"),
        ),
        depends_on=("content.static_content", "mechanic.agreement_ledger"),
        pipelines=("llm.minister_dialogue", "llm.dialogue_pre_audit", "llm.dialogue_post_audit", "llm.dialogue_condition_audit"),
        reads=("NPC profiles", "memory", "relationship network", "agreement ledger"),
        writes=("chat history", "goals", "stances", "agreements"),
        hot_swap="session_reload",
        risk="high",
    ),
    _module(
        "llm.season_pipeline",
        kind="llm",
        owner="ming_sim.agents + ming_sim.simulation",
        entrypoint="season simulator / score extractor",
        hooks=(
            _hook("turn.simulate", timing="during", contract="settlement payload -> narrative report"),
            _hook("turn.extract", timing="after", contract="narrative report -> structured deltas"),
        ),
        depends_on=("mechanic.bureaucracy", "mechanic.directive_resolution"),
        pipelines=("llm.season_simulator", "llm.score_extractor", "llm.agreement_review", "llm.chapter_memory"),
        reads=("directives", "state snapshot", "chapter memories", "agreement evidence"),
        writes=("monthly report", "structured deltas", "chapter memories"),
        hot_swap="session_reload",
        risk="high",
    ),
    _module(
        "portrait.pipeline",
        kind="portrait",
        owner="ming_sim.portraits + web_app.WebGame",
        entrypoint="build_portrait_spec / queue_portrait_generation",
        hooks=(
            _hook("portrait.queue", timing="during", contract="character identity -> idempotent portrait job"),
            _hook("portrait.asset.ready", timing="after", contract="generated image -> portrait_assets row"),
        ),
        depends_on=("content.static_content",),
        pipelines=("portrait.dna_sheet", "portrait.character_cutout"),
        reads=("Character", "GameState", "reference images"),
        writes=("portrait_assets", "characters.portrait_id"),
        hot_swap="declarative",
        risk="medium",
    ),
    _module(
        "service.game_session",
        kind="service",
        owner="ming_sim.session.GameSession",
        entrypoint="load_turn / chat_minister / issue_decree / advance_turn",
        hooks=(
            _hook("turn.load", timing="during", contract="save DB + content -> active session"),
            _hook("decree.issue", timing="during", contract="pending directives -> decree and settlement"),
        ),
        depends_on=(
            "content.static_content",
            "llm.dialogue_pipeline",
            "llm.season_pipeline",
            "mechanic.directive_resolution",
            "mechanic.fixed_period_flows",
            "portrait.pipeline",
        ),
        reads=("GameContent", "GameDB", "LLM config"),
        writes=("save DB", "runtime registry", "turn state"),
        hot_swap="session_reload",
        risk="high",
    ),
    _module(
        "web.game_routes",
        kind="web",
        owner="web_app.py",
        entrypoint="FastAPI routes / WebGame facade",
        hooks=(_hook("web.route.dispatch", timing="during", contract="HTTP request -> service call -> response"),),
        depends_on=("service.game_session", "web.payload_contracts", "admin.table_editor", "portrait.pipeline"),
        reads=("HTTP requests", "sessions", "GameSession"),
        writes=("HTTP responses",),
        hot_swap="restart_required",
        risk="high",
    ),
)

MODULE_REGISTRY: Dict[str, ModuleSpec] = {spec.id: spec for spec in _SPECS}
if len(MODULE_REGISTRY) != len(_SPECS):  # pragma: no cover - import-time invariant
    raise RuntimeError("duplicate module id in MODULE_REGISTRY")


def module_spec(module_id: str) -> ModuleSpec:
    """Return a module spec by id with a clear error for missing contracts."""
    try:
        return MODULE_REGISTRY[module_id]
    except KeyError as error:
        raise KeyError(f"unknown runtime module: {module_id}") from error


def module_specs(kind: Optional[ModuleKind] = None, *, enabled_only: bool = False) -> Tuple[ModuleSpec, ...]:
    """Return module specs sorted by id, optionally filtered by kind/enabled."""
    specs: Sequence[ModuleSpec] = tuple(MODULE_REGISTRY.values())
    if kind is not None:
        specs = tuple(spec for spec in specs if spec.kind == kind)
    if enabled_only:
        specs = tuple(spec for spec in specs if spec.enabled_by_default)
    return tuple(sorted(specs, key=lambda spec: spec.id))


def modules_for_hook(hook_name: str) -> Tuple[ModuleSpec, ...]:
    """Return enabled modules participating in a hook, sorted by hook order."""
    participants = [
        spec
        for spec in MODULE_REGISTRY.values()
        if spec.enabled_by_default and any(hook.name == hook_name for hook in spec.hooks)
    ]
    return tuple(
        sorted(
            participants,
            key=lambda spec: (
                min(hook.order for hook in spec.hooks if hook.name == hook_name),
                spec.id,
            ),
        )
    )


def extension_hooks() -> Tuple[str, ...]:
    """Return all declared hook names."""
    return tuple(sorted({hook.name for spec in MODULE_REGISTRY.values() for hook in spec.hooks}))


def module_dependency_order(*, enabled_only: bool = True) -> Tuple[str, ...]:
    """Return module ids in dependency order.

    This is a lightweight topological sort for diagnostics and future app
    factory wiring. It ignores disabled modules by default so offline tools do
    not force runtime dependencies.
    """
    specs = {spec.id: spec for spec in module_specs(enabled_only=enabled_only)}
    ordered: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(module_id: str) -> None:
        if module_id in visited:
            return
        if module_id in visiting:
            raise RuntimeError(f"cyclic module dependency at {module_id}")
        visiting.add(module_id)
        spec = specs[module_id]
        for dependency in spec.depends_on:
            if dependency in specs:
                visit(dependency)
        visiting.remove(module_id)
        visited.add(module_id)
        ordered.append(module_id)

    for module_id in sorted(specs):
        visit(module_id)
    return tuple(ordered)


def validate_module_registry() -> Tuple[str, ...]:
    """Return human-readable registry issues; empty means the contract is sane."""
    issues: list[str] = []
    known_modules = set(MODULE_REGISTRY)
    known_pipelines = set(PIPELINE_REGISTRY)
    for spec in MODULE_REGISTRY.values():
        if not spec.owner:
            issues.append(f"{spec.id}: missing owner")
        if not spec.entrypoint:
            issues.append(f"{spec.id}: missing entrypoint")
        for dependency in spec.depends_on:
            if dependency not in known_modules:
                issues.append(f"{spec.id}: unknown dependency {dependency}")
        for pipeline in spec.pipelines:
            if pipeline not in known_pipelines:
                issues.append(f"{spec.id}: unknown pipeline {pipeline}")
        for hook in spec.hooks:
            if not hook.name or "." not in hook.name:
                issues.append(f"{spec.id}: invalid hook name {hook.name!r}")
            if not hook.contract:
                issues.append(f"{spec.id}: hook {hook.name} missing contract")
        if spec.risk == "high" and spec.hot_swap == "runtime_safe":
            issues.append(f"{spec.id}: high-risk module cannot be runtime_safe")
    try:
        module_dependency_order()
    except RuntimeError as error:
        issues.append(str(error))
    return tuple(sorted(issues))
