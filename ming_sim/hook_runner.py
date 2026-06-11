"""Trusted in-process hook runner for declared runtime modules.

This runner is intentionally not a plugin loader. It never imports arbitrary
module paths from configuration. Code that is already imported by the trusted
application may register handlers for hooks declared in ``MODULE_REGISTRY``.
That gives the project a safe bridge from static contracts toward modular,
quasi-hot-pluggable mechanics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from ming_sim.module_registry import HookSpec, ModuleSpec, module_specs

HookHandler = Callable[[Any], Any]


class HookExecutionError(RuntimeError):
    """Raised when a hook handler fails in fail-closed mode."""


@dataclass(frozen=True)
class RegisteredHookHandler:
    """One trusted handler bound to one declared hook."""

    hook_name: str
    module_id: str
    handler_id: str
    order: int
    handler: HookHandler


@dataclass(frozen=True)
class HookRunRecord:
    """Payload-free execution summary for recent hook runs."""

    hook_name: str
    handler_count: int
    executed_handlers: Tuple[str, ...]
    failed_handlers: Tuple[str, ...] = ()
    success: bool = True
    fail_closed: bool = True

    def to_dict(self) -> Dict[str, Any]:
        """Return a serializable, payload-free summary."""

        return {
            "hook_name": self.hook_name,
            "handler_count": self.handler_count,
            "executed_handlers": list(self.executed_handlers),
            "failed_handlers": list(self.failed_handlers),
            "success": self.success,
            "fail_closed": self.fail_closed,
        }


class HookRunner:
    """Register and execute trusted handlers for declared hooks."""

    def __init__(self, specs: Iterable[ModuleSpec], *, history_limit: int = 50):
        self._modules: Dict[str, ModuleSpec] = {spec.id: spec for spec in specs}
        self._hooks_by_module: Dict[str, Dict[str, HookSpec]] = {
            spec.id: {hook.name: hook for hook in spec.hooks}
            for spec in specs
        }
        self._known_hooks = frozenset(
            hook.name
            for spec in specs
            for hook in spec.hooks
        )
        self._handlers: Dict[str, List[RegisteredHookHandler]] = {}
        self._handler_keys: set[tuple[str, str, str]] = set()
        self._history_limit = max(0, int(history_limit))
        self._run_history: List[HookRunRecord] = []

    @classmethod
    def from_module_registry(cls, *, enabled_only: bool = True) -> "HookRunner":
        return cls(module_specs(enabled_only=enabled_only))

    @property
    def known_hooks(self) -> frozenset[str]:
        return self._known_hooks

    def register(
        self,
        module_id: str,
        hook_name: str,
        handler: HookHandler,
        *,
        handler_id: str = "",
        order: Optional[int] = None,
    ) -> RegisteredHookHandler:
        """Register a trusted handler for a hook declared by ``module_id``."""
        if not callable(handler):
            raise TypeError(f"hook handler for {hook_name} must be callable")
        if module_id not in self._modules:
            raise KeyError(f"unknown hook module: {module_id}")
        if hook_name not in self._known_hooks:
            raise KeyError(f"unknown hook: {hook_name}")
        hook = self._hooks_by_module.get(module_id, {}).get(hook_name)
        if hook is None:
            raise ValueError(f"module {module_id} does not declare hook {hook_name}")

        clean_handler_id = handler_id or getattr(handler, "__name__", "") or "handler"
        key = (hook_name, module_id, clean_handler_id)
        if key in self._handler_keys:
            raise ValueError(f"duplicate hook handler: {hook_name}/{module_id}/{clean_handler_id}")

        registered = RegisteredHookHandler(
            hook_name=hook_name,
            module_id=module_id,
            handler_id=clean_handler_id,
            order=hook.order if order is None else int(order),
            handler=handler,
        )
        self._handler_keys.add(key)
        self._handlers.setdefault(hook_name, []).append(registered)
        self._handlers[hook_name].sort(key=lambda item: (item.order, item.module_id, item.handler_id))
        return registered

    def handlers_for(self, hook_name: str) -> Tuple[RegisteredHookHandler, ...]:
        if hook_name not in self._known_hooks:
            raise KeyError(f"unknown hook: {hook_name}")
        return tuple(self._handlers.get(hook_name, ()))

    def recent_runs(self, limit: Optional[int] = None) -> Tuple[HookRunRecord, ...]:
        """Return recent hook execution summaries without payload data."""

        if limit is None:
            return tuple(self._run_history)
        count = max(0, int(limit))
        if count == 0:
            return ()
        return tuple(self._run_history[-count:])

    def clear_run_history(self) -> None:
        """Clear in-memory hook run summaries."""

        self._run_history.clear()

    def diagnostics(self, *, run_limit: int = 20) -> Dict[str, Any]:
        """Return serializable hook registrations and recent run summaries."""

        handlers = []
        for hook_name in sorted(self._handlers):
            for registered in self._handlers[hook_name]:
                handlers.append({
                    "hook_name": registered.hook_name,
                    "module_id": registered.module_id,
                    "handler_id": registered.handler_id,
                    "order": registered.order,
                })
        return {
            "known_hooks": sorted(self._known_hooks),
            "registered_handler_count": len(handlers),
            "registered_handlers": handlers,
            "recent_runs": [record.to_dict() for record in self.recent_runs(limit=run_limit)],
            "history_limit": self._history_limit,
        }

    def _record_run(self, record: HookRunRecord) -> None:
        if self._history_limit <= 0:
            return
        self._run_history.append(record)
        if len(self._run_history) > self._history_limit:
            del self._run_history[:len(self._run_history) - self._history_limit]

    def run(self, hook_name: str, payload: Any, *, fail_closed: bool = True) -> Any:
        """Run handlers for ``hook_name`` in deterministic order.

        A handler may return a replacement payload. Returning ``None`` means
        "no mutation", which is useful for observer-style hooks.
        """
        if hook_name not in self._known_hooks:
            raise KeyError(f"unknown hook: {hook_name}")
        current = payload
        handlers = self._handlers.get(hook_name, ())
        executed_handlers: List[str] = []
        failed_handlers: List[str] = []
        for registered in handlers:
            handler_ref = f"{registered.module_id}/{registered.handler_id}"
            try:
                result = registered.handler(current)
            except Exception as error:  # noqa: BLE001 - wrap with hook context
                failed_handlers.append(handler_ref)
                if fail_closed:
                    self._record_run(
                        HookRunRecord(
                            hook_name=hook_name,
                            handler_count=len(handlers),
                            executed_handlers=tuple(executed_handlers),
                            failed_handlers=tuple(failed_handlers),
                            success=False,
                            fail_closed=True,
                        )
                    )
                    raise HookExecutionError(
                        f"hook {hook_name} failed in {registered.module_id}/{registered.handler_id}: {error}"
                    ) from error
                continue
            executed_handlers.append(handler_ref)
            if result is not None:
                current = result
        self._record_run(
            HookRunRecord(
                hook_name=hook_name,
                handler_count=len(handlers),
                executed_handlers=tuple(executed_handlers),
                failed_handlers=tuple(failed_handlers),
                success=True,
                fail_closed=fail_closed,
            )
        )
        return current


def build_default_hook_runner() -> HookRunner:
    """Create an empty runner over enabled module contracts."""
    return HookRunner.from_module_registry(enabled_only=True)
