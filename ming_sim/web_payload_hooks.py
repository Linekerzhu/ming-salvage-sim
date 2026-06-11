"""Helpers for the trusted Web payload hook contract.

The FastAPI app owns HTTP routing, but the shape of ``web.payload.encode``
belongs to the reusable simulation/service layer. Keeping envelope construction
here makes the quasi-pluggable hook easier to test without importing FastAPI,
opening SQLite, or booting a ``WebGame``.
"""

from __future__ import annotations

from typing import Any, Dict

from ming_sim.web_route_contracts import WebPayloadRouteSpec, resolve_payload_hook_route

WEB_PAYLOAD_ENCODE_HOOK = "web.payload.encode"


def route_spec_payload(spec: WebPayloadRouteSpec) -> Dict[str, Any]:
    """Return the serializable subset of a route contract exposed to handlers."""

    return {
        "route": spec.route,
        "methods": list(spec.methods),
        "surface": spec.surface,
        "includes_state": spec.includes_state,
        "compact": spec.compact,
        "risk": spec.risk,
    }


def build_web_payload_hook_envelope(route: str, payload: Dict[str, Any], *, method: str = "") -> Dict[str, Any]:
    """Build the canonical ``web.payload.encode`` envelope for trusted handlers."""

    spec, resolved_method = resolve_payload_hook_route(route, method)
    return {
        "route": route,
        "method": resolved_method,
        "surface": spec.surface,
        "route_spec": route_spec_payload(spec),
        "payload": payload,
    }


def unwrap_web_payload_hook_result(result: Any) -> Dict[str, Any]:
    """Normalize supported handler return shapes into an HTTP payload dict."""

    if isinstance(result, dict):
        if "payload" in result:
            if isinstance(result["payload"], dict):
                return dict(result["payload"])
            raise RuntimeError("web.payload.encode hook returned a non-dict payload")
        return dict(result)
    raise RuntimeError("web.payload.encode hook must return a payload dict or {'payload': dict}")


def attach_state_payload(payload: Dict[str, Any], state_payload: Dict[str, Any]) -> Dict[str, Any]:
    """Return a mutation response payload with a refreshed game state attached."""

    out = dict(payload)
    out["state"] = state_payload
    return out


def run_web_payload_hook(hook_runner: Any, route: str, payload: Dict[str, Any], *, method: str = "") -> Dict[str, Any]:
    """Run ``web.payload.encode`` through a trusted hook runner."""

    envelope = build_web_payload_hook_envelope(route, payload, method=method)
    return unwrap_web_payload_hook_result(hook_runner.run(WEB_PAYLOAD_ENCODE_HOOK, envelope))
