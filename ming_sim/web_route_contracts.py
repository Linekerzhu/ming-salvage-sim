"""Declarative contracts for Web game-data payload routes.

This registry keeps the quasi-pluggable Web payload hook from becoming a bag
of route strings scattered through ``web_app.py``. It is intentionally
dependency-light: importing it must not initialize FastAPI, content, SQLite, or
LLM clients.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Literal, Tuple

PayloadSurface = Literal["state", "panel", "mutation", "menu"]
RiskLevel = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class WebPayloadRouteSpec:
    """One game-data HTTP response that may pass through web.payload.encode."""

    route: str
    methods: Tuple[str, ...]
    surface: PayloadSurface
    includes_state: bool = False
    compact: bool = False
    risk: RiskLevel = "medium"
    notes: str = ""


def _route(
    route: str,
    methods: Iterable[str],
    surface: PayloadSurface,
    *,
    includes_state: bool = False,
    compact: bool = False,
    risk: RiskLevel = "medium",
    notes: str = "",
) -> WebPayloadRouteSpec:
    return WebPayloadRouteSpec(
        route=route,
        methods=tuple(method.upper() for method in methods),
        surface=surface,
        includes_state=bool(includes_state),
        compact=bool(compact),
        risk=risk,
        notes=notes,
    )


_SPECS: Tuple[WebPayloadRouteSpec, ...] = (
    _route("/api/game/state", ("GET",), "state", includes_state=True, compact=True, risk="high"),
    _route("/api/menu/new_game", ("POST",), "menu", includes_state=True, risk="high"),
    _route("/api/menu/continue", ("POST",), "menu", includes_state=True, risk="high"),
    _route("/api/menu/load_save/{name}", ("POST",), "menu", includes_state=True, risk="high"),
    _route("/api/monthly_followups", ("GET",), "panel", compact=True, risk="low"),
    _route("/api/organizations", ("GET",), "panel", compact=True, risk="medium"),
    _route("/api/organizations/custom", ("POST",), "mutation", includes_state=True, compact=True, risk="high"),
    _route("/api/recruitment/exam", ("POST",), "mutation", includes_state=True, risk="medium"),
    _route("/api/recruitment/eunuch", ("POST",), "mutation", includes_state=True, risk="medium"),
    _route("/api/recruitment/recommend", ("POST",), "mutation", includes_state=True, risk="medium"),
    _route("/api/recruitment/castrate", ("POST",), "mutation", includes_state=True, risk="high"),
    _route("/api/recruitment/emancipate", ("POST",), "mutation", includes_state=True, risk="high"),
    _route("/api/secret_orders", ("GET",), "panel", risk="medium"),
    _route("/api/agreements", ("GET",), "panel", risk="medium"),
    _route("/api/conversation_goals", ("GET",), "panel", risk="medium"),
    _route("/api/conversation_goals/{goal_id}/abandon", ("POST",), "mutation", includes_state=True, risk="high"),
    _route("/api/map", ("GET",), "panel", compact=True, risk="medium"),
    _route("/api/situation_reports", ("GET",), "panel", risk="medium"),
    _route("/api/buildings", ("GET",), "panel", compact=True, risk="low"),
    _route("/api/characters", ("GET",), "panel", compact=True, risk="medium"),
    _route("/api/characters/{character_name}", ("GET",), "panel", risk="medium"),
    _route("/api/favorites/{minister_name}", ("POST", "DELETE"), "mutation", includes_state=True, risk="low"),
    _route("/api/ministers/{minister_name}/secret_order", ("POST",), "mutation", risk="high"),
    _route("/api/directives", ("POST",), "mutation", risk="medium"),
    _route("/api/directives/{directive_id}", ("PATCH", "DELETE"), "mutation", risk="medium"),
    _route("/api/directives/{directive_id}/confirm", ("POST",), "mutation", risk="medium"),
    _route("/api/directives/{directive_id}/reject", ("POST",), "mutation", risk="medium"),
    _route("/api/decree/write", ("POST",), "mutation", risk="medium"),
    _route("/api/decree", ("PATCH",), "mutation", risk="medium"),
    _route("/api/decree/issue", ("POST",), "mutation", includes_state=True, risk="high"),
    _route("/api/consorts/candidates", ("GET",), "panel", risk="low"),
    _route("/api/consorts/{name}/select", ("POST",), "mutation", risk="medium"),
    _route("/api/consorts/{name}/action", ("POST",), "mutation", includes_state=True, risk="medium"),
    _route("/api/saves/{name}/load", ("POST",), "mutation", includes_state=True, risk="high"),
    _route("/api/game/reset", ("POST",), "mutation", includes_state=True, risk="high"),
    _route("/api/portraits/{name}/generate", ("POST",), "mutation", includes_state=True, risk="medium"),
    _route("/api/consorts/{name}/portrait", ("POST", "DELETE"), "mutation", includes_state=True, risk="medium"),
)

EXCLUDED_WEB_PAYLOAD_ROUTES: Dict[str, str] = {
    "/api/auth": "Authentication/session bootstrap is not game-data payload.",
    "/api/auth/login": "Authentication boundary.",
    "/api/auth/register": "Authentication registration boundary.",
    "/api/auth/logout": "Authentication boundary.",
    "/api/auth/me": "Authentication boundary.",
    "/api/llm/config": "Contains LLM configuration metadata and must stay outside game payload hooks.",
    "/api/server_admin/overview": "Server-admin boundary.",
    "/api/server_admin/users/{username}/close_game": "Server-admin boundary.",
    "/api/server_admin/users/{username}/logout": "Server-admin boundary.",
    "/api/server_admin/users/{username}/main_db": "Server-admin file boundary.",
    "/api/admin/tables": "Admin table editor boundary.",
    "/api/admin/table/{table}": "Admin table editor boundary.",
    "/api/admin/table/{table}/upsert": "Admin table editor boundary.",
    "/api/admin/table/{table}/delete": "Admin table editor boundary.",
    "/api/decree/issue/stream": "SSE stream; declare a separate stream hook before extending.",
    "/api/ministers/{minister_name}/chat/stream": "SSE stream; declare a separate stream hook before extending.",
    "/api/agreements/tasks/{task_id}": "Deprecated manual task mutation; always returns a conflict.",
    "/api/court_layout": "Client layout preference, not simulation payload.",
    "/api/history/turns": "Archive index; should move to a history service before generic payload hooks.",
    "/api/history/turn/{turn}": "Archive/detail bundle; should move to a history service before generic payload hooks.",
    "/api/turn_extraction": "Raw extractor debug payload; keep outside generic game-data hooks.",
    "/api/menu/status": "Pre-game bootstrap/menu status boundary.",
    "/api/menu/llm": "Menu-time LLM config persistence boundary.",
    "/api/menu/saves/{name}": "Pre-game save-file operation boundary.",
    "/api/menu/exit_to_menu": "Process/session lifecycle boundary.",
    "/api/menu/shutdown": "Process lifecycle boundary.",
    "/api/ministers/{minister_name}/chat": "Dialogue pipeline response; use a chat-specific hook before extending.",
    "/api/ministers/{minister_name}/chat/undo": "Dialogue history mutation; use a chat-specific hook before extending.",
    "/api/portraits/{name}/status": "Portrait job status boundary; use a portrait-specific hook before extending.",
    "/api/saves": "In-game save-file menu boundary.",
    "/api/saves/{name}": "In-game save-file operation boundary.",
}


def web_payload_route_specs() -> Tuple[WebPayloadRouteSpec, ...]:
    """Return all registered game-data payload route contracts."""

    return _SPECS


def web_payload_route_map() -> Dict[str, WebPayloadRouteSpec]:
    """Return route contracts keyed by route template."""

    return {spec.route: spec for spec in _SPECS}


def web_payload_hook_routes() -> Tuple[str, ...]:
    """Return route templates that are allowed to call web.payload.encode."""

    return tuple(spec.route for spec in _SPECS)


def ensure_payload_hook_route(route: str) -> WebPayloadRouteSpec:
    """Return a route spec or fail closed for undeclared hook routes."""

    spec, _method = resolve_payload_hook_route(route)
    return spec


def resolve_payload_hook_route(route: str, method: str = "") -> Tuple[WebPayloadRouteSpec, str]:
    """Return a route spec and concrete method or fail closed for ambiguity."""

    spec = web_payload_route_map().get(route)
    if spec is None:
        raise KeyError(f"Undeclared web.payload.encode route: {route}")
    normalized_method = str(method or "").strip().upper()
    if not normalized_method:
        if len(spec.methods) != 1:
            raise ValueError(f"web.payload.encode route needs explicit method: {route}")
        normalized_method = spec.methods[0]
    if normalized_method not in spec.methods:
        raise ValueError(f"Method {normalized_method} is not declared for web.payload.encode route: {route}")
    return spec, normalized_method


def validate_web_payload_route_registry() -> Tuple[str, ...]:
    """Validate registry shape without importing the web app."""

    issues = []
    seen = set()
    excluded = set(EXCLUDED_WEB_PAYLOAD_ROUTES)
    for route, reason in EXCLUDED_WEB_PAYLOAD_ROUTES.items():
        if not route.startswith("/api/"):
            issues.append(f"excluded route must start with /api/: {route}")
        if any(char.isspace() for char in route):
            issues.append(f"excluded route contains whitespace: {route}")
        if not str(reason).strip():
            issues.append(f"excluded route needs a reason: {route}")
    for spec in _SPECS:
        if spec.route in seen:
            issues.append(f"duplicate route: {spec.route}")
        seen.add(spec.route)
        if not spec.route.startswith("/api/"):
            issues.append(f"route must start with /api/: {spec.route}")
        if any(char.isspace() for char in spec.route):
            issues.append(f"route contains whitespace: {spec.route}")
        if spec.route in excluded:
            issues.append(f"route is both payload-enabled and excluded: {spec.route}")
        if not spec.methods:
            issues.append(f"route has no methods: {spec.route}")
        for method in spec.methods:
            if method != method.upper():
                issues.append(f"method must be uppercase: {spec.route} {method}")
        if spec.includes_state and spec.surface not in {"state", "mutation", "menu"}:
            issues.append(f"stateful route surface mismatch: {spec.route}")
        if spec.compact and spec.surface == "mutation" and not spec.includes_state:
            issues.append(f"compact mutation should include refreshed state: {spec.route}")
    return tuple(issues)
