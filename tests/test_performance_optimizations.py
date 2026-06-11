import ast
import json
import os
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

import web_app
from ming_sim.content import GameContent
from ming_sim.context import bind_content, match_minister_from_text, npc_network_recommendations
from ming_sim.db import GameDB
from ming_sim.llm_config import for_role as llm_config_for_role
from ming_sim.models import CourtContext, LLMConfig
from ming_sim.hook_runner import HookExecutionError, build_default_hook_runner
from ming_sim.module_registry import (
    extension_hooks,
    module_dependency_order,
    module_specs,
    modules_for_hook,
    validate_module_registry,
)
from ming_sim.pipeline_registry import (
    advanced_llm_roles,
    llm_output_token_budget,
    pipeline_specs,
)
from ming_sim.registry import build_common_minister_context
from ming_sim.web_payloads import (
    ARMY_FIELDS,
    BUILDING_FIELDS,
    CHARACTER_CARD_FIELDS,
    CHARACTER_INDEX_FIELDS,
    ISSUE_FIELDS,
    LEGACY_FIELDS,
    MAP_NODE_FIELDS,
    MONTHLY_FOLLOWUP_FIELDS,
    ORG_INSTITUTION_FIELDS,
    ORG_PERSON_FIELDS,
    ORG_SLOT_FIELDS,
    POWER_FIELDS,
    REGION_FIELDS,
    compact_armies,
    compact_buildings,
    compact_character_cards,
    compact_character_index,
    compact_map_nodes,
    compact_organization_payload,
    compact_issues,
    compact_legacies,
    compact_powers,
    compact_regions,
    monthly_followups_payload,
)
from ming_sim.web_payload_hooks import (
    attach_state_payload,
    build_web_payload_hook_envelope,
    run_web_payload_hook,
    unwrap_web_payload_hook_result,
)
from ming_sim.web_route_contracts import (
    EXCLUDED_WEB_PAYLOAD_ROUTES,
    resolve_payload_hook_route,
    validate_web_payload_route_registry,
    web_payload_hook_routes,
    web_payload_route_map,
)
import ming_sim.session as session_module


class PerformanceOptimizationTests(unittest.TestCase):
    def test_pipeline_registry_covers_core_engineering_surfaces(self) -> None:
        specs = pipeline_specs()
        by_id = {spec.id: spec for spec in specs}
        kinds = {spec.kind for spec in specs}

        self.assertTrue({"frontend", "web", "admin", "llm", "portrait", "mechanic", "data"}.issubset(kinds))
        for pipeline_id in (
            "frontend.state_decoder",
            "web.state_payload",
            "web.character_detail",
            "admin.table_editor",
            "llm.minister_dialogue",
            "llm.dialogue_pre_audit",
            "llm.dialogue_post_audit",
            "llm.dialogue_condition_audit",
            "llm.season_simulator",
            "llm.score_extractor",
            "portrait.character_cutout",
            "mechanic.directive_resolution",
            "data.npc_foundation_generation",
        ):
            self.assertIn(pipeline_id, by_id)

        self.assertEqual(advanced_llm_roles(), frozenset({"simulator", "extractor", "dialogue_audit"}))
        self.assertEqual(by_id["portrait.character_cutout"].failure_policy, "best_effort")
        self.assertEqual(by_id["admin.table_editor"].failure_policy, "dry_run_required")
        self.assertEqual(by_id["web.state_payload"].risk, "high")
        self.assertEqual(by_id["frontend.state_decoder"].owner, "web/src/api/payloads.ts")

    def test_pipeline_registry_controls_llm_role_and_token_budgets(self) -> None:
        cfg = LLMConfig(
            api_key="test",
            base_url="https://example.test/v1",
            model="cheap-model",
            max_tokens=8000,
            advanced_model="strong-model",
        )

        self.assertEqual(llm_config_for_role(cfg, "simulator").model, "strong-model")
        self.assertEqual(llm_config_for_role(cfg, "extractor").model, "strong-model")
        self.assertEqual(llm_config_for_role(cfg, "dialogue_audit").model, "strong-model")
        self.assertEqual(llm_config_for_role(cfg, "chapter_memory").model, "cheap-model")

        self.assertEqual(llm_output_token_budget("llm.season_simulator", cfg.max_tokens), 8000)
        self.assertEqual(llm_output_token_budget("llm.score_extractor", cfg.max_tokens), 8000)
        self.assertEqual(llm_output_token_budget("llm.chapter_memory", cfg.max_tokens, minimum=1200), 1600)
        self.assertEqual(llm_output_token_budget("llm.decree_writer", cfg.max_tokens, minimum=1200), 2400)
        self.assertEqual(llm_output_token_budget("llm.ending_summary", cfg.max_tokens, minimum=2400), 3200)
        self.assertEqual(
            llm_output_token_budget("llm.dialogue_post_audit", cfg.max_tokens, requested=3000, minimum=1200),
            3000,
        )

    def test_frontend_api_payload_decoding_has_module_boundary(self) -> None:
        root = Path(__file__).resolve().parents[1]
        main_src = (root / "web" / "src" / "main.tsx").read_text(encoding="utf-8")
        client_src = (root / "web" / "src" / "api" / "client.ts").read_text(encoding="utf-8")
        payloads_src = (root / "web" / "src" / "api" / "payloads.ts").read_text(encoding="utf-8")

        self.assertIn('from "./api/client"', main_src)
        self.assertIn('from "./api/payloads"', main_src)
        self.assertIn("export const api", client_src)
        self.assertIn("export const streamJsonSse", client_src)
        self.assertIn("export const normalizeGameState", payloads_src)
        self.assertIn("export const decodeMapNodes", payloads_src)
        self.assertNotIn("const decodeRows =", main_src)
        self.assertNotIn("const normalizeGameState =", main_src)
        self.assertNotIn("const parseSseMessage =", main_src)

    def test_module_registry_defines_quasi_hot_plug_boundaries(self) -> None:
        specs = module_specs()
        by_id = {spec.id: spec for spec in specs}
        kinds = {spec.kind for spec in specs}

        self.assertEqual(validate_module_registry(), ())
        self.assertTrue(
            {"frontend", "web", "admin", "service", "llm", "portrait", "mechanic", "data", "content"}.issubset(kinds)
        )
        for module_id in (
            "content.static_content",
            "frontend.api_client",
            "frontend.payload_decoder",
            "web.payload_contracts",
            "web.game_routes",
            "admin.table_editor",
            "service.hook_runner",
            "service.game_session",
            "llm.dialogue_pipeline",
            "llm.season_pipeline",
            "portrait.pipeline",
            "mechanic.bureaucracy",
            "mechanic.directive_resolution",
            "mechanic.agreement_ledger",
            "data.npc_foundation",
        ):
            self.assertIn(module_id, by_id)

        self.assertEqual(by_id["frontend.payload_decoder"].pipelines, ("frontend.state_decoder",))
        self.assertEqual(by_id["admin.table_editor"].hot_swap, "declarative")
        self.assertEqual(by_id["web.game_routes"].hot_swap, "restart_required")
        self.assertFalse(by_id["data.npc_foundation"].enabled_by_default)

        hooks = extension_hooks()
        for hook_name in ("turn.load", "turn.resolve", "chat.audit", "portrait.queue", "web.payload.encode", "hook.run"):
            self.assertIn(hook_name, hooks)

        turn_resolution_modules = [spec.id for spec in modules_for_hook("turn.resolve")]
        self.assertEqual(turn_resolution_modules, ["mechanic.directive_resolution"])

        order = list(module_dependency_order())
        self.assertLess(order.index("content.static_content"), order.index("service.game_session"))
        self.assertLess(order.index("web.payload_contracts"), order.index("web.game_routes"))
        self.assertLess(order.index("mechanic.bureaucracy"), order.index("mechanic.directive_resolution"))

    def test_web_payload_route_contract_registry_covers_hooked_routes(self) -> None:
        root = Path(__file__).resolve().parents[1]
        web_src = (root / "web_app.py").read_text(encoding="utf-8")
        tree = ast.parse(web_src)
        hooked_routes: set[str] = set()
        hooked_route_methods: list[tuple[str, str]] = []
        api_routes: set[str] = set()
        api_methods_by_route: dict[str, set[str]] = {}

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in node.decorator_list:
                    if not isinstance(decorator, ast.Call):
                        continue
                    func = decorator.func
                    if (
                        isinstance(func, ast.Attribute)
                        and isinstance(func.value, ast.Name)
                        and func.value.id == "app"
                        and func.attr in {"get", "post", "patch", "delete"}
                        and decorator.args
                        and isinstance(decorator.args[0], ast.Constant)
                    ):
                        route = str(decorator.args[0].value)
                        if route.startswith("/api/"):
                            api_routes.add(route)
                            api_methods_by_route.setdefault(route, set()).add(func.attr.upper())
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name) and func.id == "_web_payload_response":
                if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                    route = str(node.args[1].value)
                    hooked_routes.add(route)
                    method = ""
                    for keyword in node.keywords:
                        if keyword.arg == "method" and isinstance(keyword.value, ast.Constant):
                            method = str(keyword.value.value).upper()
                    hooked_route_methods.append((route, method))
            if isinstance(func, ast.Attribute) and func.attr == "web_payload_response":
                if node.args and isinstance(node.args[0], ast.Constant):
                    route = str(node.args[0].value)
                    hooked_routes.add(route)
                    method = ""
                    for keyword in node.keywords:
                        if keyword.arg == "method" and isinstance(keyword.value, ast.Constant):
                            method = str(keyword.value.value).upper()
                    hooked_route_methods.append((route, method))
            if isinstance(func, ast.Name) and func.id == "_response_with_state":
                for keyword in node.keywords:
                    if keyword.arg == "route" and isinstance(keyword.value, ast.Constant):
                        route = str(keyword.value.value)
                        hooked_routes.add(route)
                        method = ""
                        for method_keyword in node.keywords:
                            if method_keyword.arg == "method" and isinstance(method_keyword.value, ast.Constant):
                                method = str(method_keyword.value.value).upper()
                        hooked_route_methods.append((route, method))

        routes = set(web_payload_hook_routes())
        route_map = web_payload_route_map()
        classified_routes = routes | set(EXCLUDED_WEB_PAYLOAD_ROUTES)

        self.assertEqual(validate_web_payload_route_registry(), ())
        self.assertTrue(api_routes)
        self.assertTrue(hooked_routes)
        self.assertTrue(api_routes.issubset(classified_routes))
        self.assertTrue(routes.issubset(api_routes))
        self.assertTrue(hooked_routes.issubset(routes))
        self.assertIn("/api/game/state", routes)
        self.assertIn("/api/characters", routes)
        self.assertIn("/api/favorites/{minister_name}", routes)
        self.assertNotIn("/api/llm/config", routes)
        self.assertNotIn("/api/decree/issue/stream", routes)
        self.assertIn("/api/llm/config", EXCLUDED_WEB_PAYLOAD_ROUTES)
        self.assertTrue(route_map["/api/game/state"].includes_state)
        self.assertTrue(route_map["/api/characters"].compact)
        self.assertEqual(route_map["/api/favorites/{minister_name}"].methods, ("POST", "DELETE"))
        self.assertEqual(resolve_payload_hook_route("/api/characters"), (route_map["/api/characters"], "GET"))
        self.assertEqual(
            resolve_payload_hook_route("/api/favorites/{minister_name}", "post"),
            (route_map["/api/favorites/{minister_name}"], "POST"),
        )
        with self.assertRaises(ValueError):
            resolve_payload_hook_route("/api/favorites/{minister_name}")
        with self.assertRaises(ValueError):
            resolve_payload_hook_route("/api/favorites/{minister_name}", "PATCH")
        for route, method in hooked_route_methods:
            if len(route_map[route].methods) > 1:
                self.assertIn(method, route_map[route].methods)
        for route, spec in route_map.items():
            self.assertTrue(api_methods_by_route[route].issubset(set(spec.methods)))

    def test_web_payload_hook_helpers_are_framework_independent(self) -> None:
        base_payload = {"message": "已办"}
        state_payload = {"turn": {"turn": 1}}
        stateful_payload = attach_state_payload(base_payload, state_payload)

        self.assertEqual(stateful_payload, {"message": "已办", "state": state_payload})
        self.assertEqual(base_payload, {"message": "已办"})
        self.assertIs(stateful_payload["state"], state_payload)

        envelope = build_web_payload_hook_envelope(
            "/api/favorites/{minister_name}",
            {"favorites": ["韩爌"]},
            method="post",
        )

        self.assertEqual(envelope["route"], "/api/favorites/{minister_name}")
        self.assertEqual(envelope["method"], "POST")
        self.assertEqual(envelope["surface"], "mutation")
        self.assertEqual(envelope["route_spec"]["methods"], ["POST", "DELETE"])
        self.assertTrue(envelope["route_spec"]["includes_state"])
        self.assertEqual(envelope["payload"], {"favorites": ["韩爌"]})

        self.assertEqual(unwrap_web_payload_hook_result({"payload": {"ok": True}}), {"ok": True})
        self.assertEqual(unwrap_web_payload_hook_result({"ok": True}), {"ok": True})
        with self.assertRaises(RuntimeError):
            unwrap_web_payload_hook_result({"payload": "bad"})
        with self.assertRaises(RuntimeError):
            unwrap_web_payload_hook_result(["not", "a", "dict"])

        runner = build_default_hook_runner()
        seen: list[tuple[str, str, str]] = []

        def mark_payload(payload_envelope: dict) -> dict:
            seen.append(
                (
                    str(payload_envelope["route"]),
                    str(payload_envelope["method"]),
                    str(payload_envelope["route_spec"]["risk"]),
                )
            )
            payload = dict(payload_envelope["payload"])
            payload["hooked"] = True
            return {"payload": payload}

        runner.register("web.payload_contracts", "web.payload.encode", mark_payload, handler_id="mark_payload")

        result = run_web_payload_hook(runner, "/api/characters", {"characters": []})

        self.assertEqual(seen, [("/api/characters", "GET", "medium")])
        self.assertEqual(result, {"characters": [], "hooked": True})

    def test_hook_runner_executes_declared_handlers_safely(self) -> None:
        runner = build_default_hook_runner()
        calls: list[str] = []

        def first(payload: dict[str, list[str]]) -> dict[str, list[str]]:
            calls.append("first")
            return {"steps": payload["steps"] + ["first"]}

        def second(payload: dict[str, list[str]]) -> dict[str, list[str]]:
            calls.append("second")
            return {"steps": payload["steps"] + ["second"]}

        runner.register("mechanic.bureaucracy", "directive.assess", second, handler_id="second", order=20)
        runner.register("mechanic.bureaucracy", "directive.assess", first, handler_id="first", order=10)

        result = runner.run("directive.assess", {"steps": []})

        self.assertEqual(calls, ["first", "second"])
        self.assertEqual(result, {"steps": ["first", "second"]})
        self.assertEqual(
            [handler.handler_id for handler in runner.handlers_for("directive.assess")],
            ["first", "second"],
        )
        first_run = runner.recent_runs()[-1]
        self.assertEqual(first_run.hook_name, "directive.assess")
        self.assertEqual(first_run.handler_count, 2)
        self.assertEqual(
            first_run.executed_handlers,
            ("mechanic.bureaucracy/first", "mechanic.bureaucracy/second"),
        )
        self.assertEqual(first_run.failed_handlers, ())
        self.assertTrue(first_run.success)
        self.assertNotIn("steps", repr(first_run))

        with self.assertRaises(ValueError):
            runner.register("mechanic.bureaucracy", "directive.assess", first, handler_id="first")
        with self.assertRaises(ValueError):
            runner.register("frontend.api_client", "directive.assess", first, handler_id="wrong-module")
        with self.assertRaises(KeyError):
            runner.register("mechanic.bureaucracy", "unknown.hook", first)

        def broken(payload: dict[str, list[str]]) -> dict[str, list[str]]:
            raise RuntimeError("boom")

        runner.register("mechanic.bureaucracy", "directive.assess", broken, handler_id="broken", order=30)
        with self.assertRaises(HookExecutionError):
            runner.run("directive.assess", {"steps": []})
        failed_run = runner.recent_runs()[-1]
        self.assertFalse(failed_run.success)
        self.assertEqual(failed_run.failed_handlers, ("mechanic.bureaucracy/broken",))
        self.assertEqual(
            failed_run.executed_handlers,
            ("mechanic.bureaucracy/first", "mechanic.bureaucracy/second"),
        )
        self.assertEqual(
            runner.run("directive.assess", {"steps": []}, fail_closed=False),
            {"steps": ["first", "second"]},
        )
        fail_open_run = runner.recent_runs()[-1]
        self.assertTrue(fail_open_run.success)
        self.assertFalse(fail_open_run.fail_closed)
        self.assertEqual(fail_open_run.failed_handlers, ("mechanic.bureaucracy/broken",))
        self.assertEqual(runner.recent_runs(limit=1), (fail_open_run,))
        diagnostics = runner.diagnostics(run_limit=2)
        self.assertIn("directive.assess", diagnostics["known_hooks"])
        self.assertEqual(diagnostics["registered_handler_count"], 3)
        self.assertEqual(
            [item["handler_id"] for item in diagnostics["registered_handlers"] if item["hook_name"] == "directive.assess"],
            ["first", "second", "broken"],
        )
        self.assertEqual(len(diagnostics["recent_runs"]), 2)
        self.assertEqual(diagnostics["recent_runs"][-1]["failed_handlers"], ["mechanic.bureaucracy/broken"])
        self.assertNotIn("steps", repr(diagnostics))
        for section in ("registered_handlers", "recent_runs"):
            for item in diagnostics[section]:
                self.assertNotIn("payload", item)
                self.assertNotIn("result", item)
        runner.clear_run_history()
        self.assertEqual(runner.recent_runs(), ())

    def test_game_session_begin_turn_runs_declared_turn_load_hook(self) -> None:
        with TemporaryDirectory() as tmp:
            runner = build_default_hook_runner()
            calls: list[int] = []

            def mark_snapshot(snapshot):
                calls.append(snapshot.turn)
                return replace(snapshot, previous_summary=f"{snapshot.previous_summary} [hooked]".strip())

            runner.register("service.game_session", "turn.load", mark_snapshot, handler_id="mark_snapshot")
            session = session_module.GameSession(
                str(Path(tmp) / "hooked_session.db"),
                LLMConfig(api_key="test", base_url="http://test.invalid/v1", model="test-model"),
                content=GameContent.load(),
                verify_llm=False,
                hook_runner=runner,
            )
            try:
                snapshot = session.begin_turn()
                self.assertEqual(calls, [snapshot.turn])
                self.assertTrue(snapshot.previous_summary.endswith("[hooked]"))
                self.assertFalse(session.previous_summary.endswith("[hooked]"))
            finally:
                session.close()

    def test_web_state_payload_runs_declared_payload_hook(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_user_data_dir = web_app.user_data_dir
            old_user_data_path = web_app.user_data_path
            old_load_runtime_llm = web_app.load_runtime_llm
            old_verify_llm = session_module.verify_llm_available
            old_env = {
                key: os.environ.get(key)
                for key in (
                    "OPENAI_API_KEY",
                    "OPENAI_BASE_URL",
                    "OPENAI_MODEL",
                    "MING_SIM_SERVER_USERS",
                    "MING_SIM_AUTH_USERS",
                    "MING_SIM_ADMIN_USERS",
                    "MING_SIM_SERVER_ADMINS",
                )
            }

            def user_data_dir() -> Path:
                root.mkdir(parents=True, exist_ok=True)
                return root

            def user_data_path(*parts: str) -> str:
                path = root.joinpath(*parts)
                path.parent.mkdir(parents=True, exist_ok=True)
                return str(path)

            web_app.user_data_dir = user_data_dir
            web_app.user_data_path = user_data_path
            web_app.load_runtime_llm = lambda: {}
            session_module.verify_llm_available = lambda _config: None
            os.environ["OPENAI_API_KEY"] = "test-key"
            os.environ["OPENAI_BASE_URL"] = "https://example.test/v1"
            os.environ["OPENAI_MODEL"] = "test-model"
            os.environ.pop("MING_SIM_SERVER_USERS", None)
            os.environ.pop("MING_SIM_AUTH_USERS", None)
            os.environ.pop("MING_SIM_ADMIN_USERS", None)
            os.environ.pop("MING_SIM_SERVER_ADMINS", None)

            game = None
            try:
                game = web_app.WebGame(fresh=True)
                default_payload = game.state_payload()
                self.assertNotIn("_hook_marker", default_payload)

                calls: list[tuple[str, str, str, bool]] = []

                def mark_payload(envelope: dict) -> dict:
                    route = str(envelope["route"])
                    method = str(envelope["method"])
                    route_spec = dict(envelope["route_spec"])
                    calls.append((route, method, str(route_spec["surface"]), bool(route_spec["compact"])))
                    payload = dict(envelope["payload"])
                    payload["_hook_marker"] = "web.payload.encode"
                    payload["_hook_route"] = route
                    payload["_hook_method"] = method
                    payload["_hook_surface"] = str(route_spec["surface"])
                    return {"route": envelope["route"], "payload": payload}

                game.session.hook_runner.register(
                    "web.payload_contracts",
                    "web.payload.encode",
                    mark_payload,
                    handler_id="mark_state_payload",
                )

                payload = game.state_payload()

                self.assertEqual(calls, [("/api/game/state", "GET", "state", True)])
                self.assertEqual(payload["_hook_marker"], "web.payload.encode")
                self.assertEqual(payload["_hook_route"], "/api/game/state")
                self.assertEqual(payload["_hook_method"], "GET")
                self.assertEqual(payload["_hook_surface"], "state")
                self.assertIn("turn", payload)
                self.assertIn("minister_fields", payload)

                old_web_game = web_app.web_game
                web_app.web_game = game
                try:
                    client = TestClient(web_app.app)

                    calls.clear()
                    index_response = client.get("/api/characters")
                    self.assertEqual(index_response.status_code, 200)
                    index_payload = index_response.json()
                    self.assertEqual(calls, [("/api/characters", "GET", "panel", True)])
                    self.assertEqual(index_payload["_hook_route"], "/api/characters")
                    self.assertEqual(index_payload["_hook_method"], "GET")
                    self.assertEqual(index_payload["_hook_surface"], "panel")
                    self.assertIn("character_fields", index_payload)

                    calls.clear()
                    favorite_response = client.post("/api/favorites/韩爌")
                    self.assertEqual(favorite_response.status_code, 200)
                    favorite_payload = favorite_response.json()
                    self.assertEqual(
                        calls,
                        [
                            ("/api/game/state", "GET", "state", True),
                            ("/api/favorites/{minister_name}", "POST", "mutation", False),
                        ],
                    )
                    self.assertEqual(favorite_payload["_hook_route"], "/api/favorites/{minister_name}")
                    self.assertEqual(favorite_payload["_hook_method"], "POST")
                    self.assertEqual(favorite_payload["_hook_surface"], "mutation")
                    self.assertEqual(favorite_payload["state"]["_hook_route"], "/api/game/state")
                    self.assertEqual(favorite_payload["state"]["_hook_method"], "GET")
                finally:
                    web_app.web_game = old_web_game
            finally:
                if game is not None:
                    game.session.close()
                web_app.user_data_dir = old_user_data_dir
                web_app.user_data_path = old_user_data_path
                web_app.load_runtime_llm = old_load_runtime_llm
                session_module.verify_llm_available = old_verify_llm
                for key, value in old_env.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_common_minister_context_reuses_fixed_monthly_blocks(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_user_data_dir = web_app.user_data_dir
            old_user_data_path = web_app.user_data_path
            old_load_runtime_llm = web_app.load_runtime_llm
            old_verify_llm = session_module.verify_llm_available
            old_env = {
                key: os.environ.get(key)
                for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL")
            }

            def user_data_dir() -> Path:
                root.mkdir(parents=True, exist_ok=True)
                return root

            def user_data_path(*parts: str) -> str:
                path = root.joinpath(*parts)
                path.parent.mkdir(parents=True, exist_ok=True)
                return str(path)

            web_app.user_data_dir = user_data_dir
            web_app.user_data_path = user_data_path
            web_app.load_runtime_llm = lambda: {}
            session_module.verify_llm_available = lambda _config: None
            os.environ["OPENAI_API_KEY"] = "test-key"
            os.environ["OPENAI_BASE_URL"] = "https://example.test/v1"
            os.environ["OPENAI_MODEL"] = "test-model"

            game = None
            try:
                game = web_app.WebGame(fresh=True)
                self.assertIsNotNone(game.session.registry)
                context = game.session.registry.context if game.session.registry else CourtContext(game.state, game.db)

                first_statements: list[str] = []
                game.db.conn.set_trace_callback(first_statements.append)
                first = build_common_minister_context(context)
                game.db.conn.set_trace_callback(None)

                second_statements: list[str] = []
                game.db.conn.set_trace_callback(second_statements.append)
                second = build_common_minister_context(context)
                game.db.conn.set_trace_callback(None)

                self.assertIs(first, second)
                self.assertIn("court_brief", first)
                self.assertIn("court_roster", first)
                self.assertIn("army_roster", first)
                self.assertIn("memory_brief", first)
                self.assertLessEqual(
                    sum("FROM memories" in sql and "chapter" in sql for sql in first_statements),
                    1,
                )
                self.assertGreater(len(first_statements), 0)
                self.assertEqual(second_statements, [])
            finally:
                if game is not None:
                    game.db.conn.set_trace_callback(None)
                    game.session.close()
                web_app.user_data_dir = old_user_data_dir
                web_app.user_data_path = old_user_data_path
                web_app.load_runtime_llm = old_load_runtime_llm
                session_module.verify_llm_available = old_verify_llm
                for key, value in old_env.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_hot_history_queries_use_growth_safe_indexes(self) -> None:
        with TemporaryDirectory() as tmp:
            db = GameDB(str(Path(tmp) / "indexes.db"))
            try:
                def plan(sql: str, params: tuple = ()) -> list[str]:
                    rows = db.conn.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()
                    return [str(row["detail"]) for row in rows]

                chapter_plan = plan(
                    """
                    SELECT turn, year, period, title, body
                    FROM event_memories
                    WHERE event_type = 'chapter_summary'
                      AND turn <= ?
                      AND turn >= ?
                    ORDER BY turn ASC
                    """,
                    (20, 16),
                )
                goal_plan = plan(
                    """
                    SELECT * FROM conversation_goals
                    WHERE minister_name=?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    ("韩爌", 8),
                )
                goal_status_plan = plan(
                    """
                    SELECT * FROM conversation_goals
                    WHERE status IN ('active','waiting_conditions','blocked')
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (80,),
                )
                agreement_plan = plan(
                    """
                    SELECT * FROM negotiation_agreements
                    WHERE minister_name=?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    ("韩爌", 12),
                )
                stance_turn_plan = plan(
                    """
                    SELECT id, turn, year, period, minister_name, topic, stance
                    FROM minister_stances
                    WHERE turn = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (12, 80),
                )
                stance_minister_plan = plan(
                    """
                    SELECT id, turn, year, period, minister_name, topic, stance
                    FROM minister_stances
                    WHERE minister_name = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    ("韩爌", 8),
                )

                self.assertTrue(any("idx_event_memories_event_turn" in detail for detail in chapter_plan))
                self.assertTrue(any("idx_conversation_goals_minister_id" in detail for detail in goal_plan))
                self.assertTrue(any("idx_conversation_goals_status_id" in detail for detail in goal_status_plan))
                self.assertTrue(any("idx_negotiation_agreements_minister_id" in detail for detail in agreement_plan))
                self.assertTrue(any("idx_minister_stances_turn_id" in detail for detail in stance_turn_plan))
                self.assertTrue(any("idx_minister_stances_minister_id" in detail for detail in stance_minister_plan))
                self.assertFalse(any("TEMP B-TREE" in detail for detail in goal_plan))
                self.assertFalse(any("SCAN conversation_goals" in detail for detail in goal_status_plan))
                self.assertFalse(any("TEMP B-TREE" in detail for detail in agreement_plan))
                self.assertFalse(any("TEMP B-TREE" in detail for detail in stance_turn_plan))
                self.assertFalse(any("SCAN minister_stances" in detail for detail in stance_minister_plan))
            finally:
                db.close()

    def test_web_payload_contracts_are_compact_and_stable(self) -> None:
        card = {
            "name": "韩爌",
            "office": "武英殿大学士",
            "office_type": "内阁",
            "faction": "东林",
            "status": "active",
            "status_label": "在朝",
            "summary": "大明 · 东林 · 内阁 · 在朝",
            "portrait_status": "ready",
            "favorite": True,
            "style": "详情接口才返回",
        }
        encoded = compact_character_cards([card])
        index_encoded = compact_character_index([
            {
                "name": "韩爌",
                "office": "武英殿大学士",
                "office_type": "内阁",
                "faction": "东林",
                "status": "active",
                "portrait_available": True,
                "status_reason": "",
                "can_summon": True,
                "power_id": "ming",
                "summary": "前端派生",
            }
        ])
        region_encoded = compact_regions([{"id": "shanxi", "name": "山西", "controlled_by": "ming"}])
        army_encoded = compact_armies([{"id": "liaodong", "name": "辽东军", "owner_power": "ming"}])
        power_encoded = compact_powers([{"id": "ming", "name": "大明", "aliases": ""}])
        building_encoded = compact_buildings([{"id": "school", "region_id": "shanxi", "name": "社学", "origin": "官建"}])
        map_encoded = compact_map_nodes([
            {
                "id": "shanxi",
                "kind": "region",
                "x": 50,
                "y": 45,
                "risk": 80,
                "region": {"id": "shanxi", "name": "山西", "controlled_by": "ming"},
                "armies": [{"id": "liaodong", "name": "辽东军", "owner_power": "ming"}],
                "buildings": [{"id": "school", "region_id": "shanxi", "name": "社学", "origin": "官建"}],
            }
        ])
        organization_encoded = compact_organization_payload({
            "institutions": [
                {
                    "id": "cabinet",
                    "name": "内阁",
                    "category": "朝堂",
                    "mandate": "票拟机务",
                    "custom": False,
                    "readiness": 70,
                    "coverage": 80,
                    "holder_quality": 75,
                    "execution_summary": "",
                    "execution_risks": [],
                    "slots": [
                        {
                            "title": "大学士",
                            "office_type": "内阁",
                            "count": 2,
                            "holders": [
                                {
                                    "name": "韩爌",
                                    "office": "武英殿大学士",
                                    "office_type": "内阁",
                                    "faction": "东林",
                                    "status": "active",
                                    "status_reason": "",
                                    "status_label": "在朝",
                                    "power_id": "ming",
                                }
                            ],
                            "filled_count": 1,
                            "vacancies": 1,
                            "overflow_count": 0,
                            "open_pool": False,
                            "match_hint": "",
                        }
                    ],
                    "vacancy_count": 1,
                    "holder_count": 1,
                }
            ],
            "unassigned": [],
            "vacancy_count": 1,
            "custom_count": 0,
        })
        issue_encoded = compact_issues([
            {
                "id": 1,
                "title": "边饷告急",
                "bar_value": 42,
                "phase": "warning",
                "stage_text": "军心浮动",
                "severity": 3,
                "tags": ["辽东", "钱粮"],
                "kind": "situation",
                "inertia": 0,
            }
        ])
        legacy_encoded = compact_legacies([
            {
                "id": 1,
                "name": "追饷余波",
                "narrative_hint": "士绅怨望未平",
                "modifiers": {},
                "effect_text": "地方阻力暂升",
                "clear_condition": "缓和士绅",
                "remaining_months": -1,
            }
        ])

        self.assertEqual(CHARACTER_CARD_FIELDS[0], "name")
        self.assertEqual(CHARACTER_CARD_FIELDS[-1], "favorite")
        self.assertEqual(encoded[0][0], "韩爌")
        self.assertEqual(encoded[0][-1], True)
        self.assertEqual(len(encoded[0]), len(CHARACTER_CARD_FIELDS))
        self.assertNotIn("style", CHARACTER_CARD_FIELDS)
        self.assertNotIn("summary", CHARACTER_CARD_FIELDS)
        self.assertNotIn("age_label", CHARACTER_CARD_FIELDS)
        self.assertNotIn("power_id", CHARACTER_CARD_FIELDS)
        self.assertNotIn("status_label", CHARACTER_CARD_FIELDS)
        self.assertNotIn("career_state", CHARACTER_CARD_FIELDS)
        self.assertNotIn("start_age", CHARACTER_CARD_FIELDS)
        self.assertEqual(CHARACTER_INDEX_FIELDS[0], "name")
        self.assertEqual(CHARACTER_INDEX_FIELDS[-1], "power_id")
        self.assertEqual(index_encoded[0][0], "韩爌")
        self.assertLess(len(index_encoded[0]), len(CHARACTER_INDEX_FIELDS))
        self.assertNotIn("summary", CHARACTER_INDEX_FIELDS)
        self.assertNotIn("status_label", CHARACTER_INDEX_FIELDS)
        self.assertNotIn("power_name", CHARACTER_INDEX_FIELDS)
        self.assertEqual(REGION_FIELDS[0], "id")
        self.assertEqual(REGION_FIELDS[-1], "controlled_by")
        self.assertEqual(region_encoded[0][0], "shanxi")
        self.assertEqual(region_encoded[0][-1], "ming")
        self.assertEqual(ARMY_FIELDS[0], "id")
        self.assertEqual(ARMY_FIELDS[-1], "owner_power")
        self.assertEqual(army_encoded[0][0], "liaodong")
        self.assertEqual(army_encoded[0][-1], "ming")
        self.assertEqual(POWER_FIELDS[0], "id")
        self.assertEqual(POWER_FIELDS[-1], "aliases")
        self.assertEqual(power_encoded[0][0], "ming")
        self.assertEqual(power_encoded[0][-1], "")
        self.assertEqual(BUILDING_FIELDS[0], "id")
        self.assertEqual(BUILDING_FIELDS[-1], "origin")
        self.assertEqual(building_encoded[0][0], "school")
        self.assertEqual(building_encoded[0][-1], "官建")
        self.assertEqual(MAP_NODE_FIELDS[0], "id")
        self.assertEqual(MAP_NODE_FIELDS[-1], "label")
        self.assertEqual(map_encoded[0][0], "shanxi")
        self.assertLess(len(map_encoded[0]), len(MAP_NODE_FIELDS))
        self.assertIsInstance(map_encoded[0][5], list)
        self.assertIsInstance(map_encoded[0][6][0], list)
        self.assertIsInstance(map_encoded[0][7][0], list)
        self.assertEqual(ORG_PERSON_FIELDS[0], "name")
        self.assertEqual(ORG_PERSON_FIELDS[-1], "power_id")
        self.assertEqual(ORG_SLOT_FIELDS[0], "title")
        self.assertEqual(ORG_SLOT_FIELDS[-1], "match_hint")
        self.assertEqual(ORG_INSTITUTION_FIELDS[0], "id")
        self.assertEqual(ORG_INSTITUTION_FIELDS[-1], "holder_count")
        self.assertIn("org_person_fields", organization_encoded)
        self.assertIsInstance(organization_encoded["institutions"][0], list)
        self.assertIsInstance(organization_encoded["institutions"][0][10][0], list)
        self.assertIsInstance(organization_encoded["institutions"][0][10][0][3][0], list)
        self.assertEqual(ISSUE_FIELDS[0], "id")
        self.assertEqual(ISSUE_FIELDS[-1], "inertia")
        self.assertEqual(issue_encoded[0][0], 1)
        self.assertLess(len(issue_encoded[0]), len(ISSUE_FIELDS))
        self.assertEqual(LEGACY_FIELDS[0], "id")
        self.assertEqual(LEGACY_FIELDS[-1], "remaining_months")
        self.assertEqual(legacy_encoded[0][0], 1)
        self.assertLess(len(legacy_encoded[0]), len(LEGACY_FIELDS))

        followups = monthly_followups_payload(3, [{"minister_name": "韩爌", "title": "请安"}])
        self.assertEqual(followups["turn"], 3)
        self.assertEqual(followups["followup_fields"], list(MONTHLY_FOLLOWUP_FIELDS))
        self.assertEqual(followups["followup_defaults"]["title"], "请安")
        self.assertEqual(followups["followups"][0][0], "韩爌")
        self.assertIsInstance(followups["followups"][0], list)

    def test_game_content_cache_returns_isolated_runtime_characters(self) -> None:
        GameContent.clear_load_cache()
        first = GameContent.load()
        second = GameContent.load()

        self.assertIsNot(first, second)
        self.assertIsNot(first.characters, second.characters)
        name = next(iter(first.characters))
        original_office = second.characters[name].office
        first.characters[name].office = "性能测试临时官"

        self.assertEqual(second.characters[name].office, original_office)

    def test_network_recommendations_prefetch_statuses_once(self) -> None:
        content = GameContent.load()
        bind_content(content)

        class FakeDB:
            def __init__(self) -> None:
                self.calls = 0

            def character_status_map(self):
                self.calls += 1
                return {name: character.status for name, character in content.characters.items()}

            def get_character_status(self, name: str):  # pragma: no cover - regression guard
                raise AssertionError(f"should not query status per candidate: {name}")

        db = FakeDB()
        result = npc_network_recommendations("韩爌", db=db, limit=8)

        self.assertEqual(db.calls, 1)
        self.assertTrue(result)

    def test_match_minister_exact_name_uses_fast_path(self) -> None:
        content = GameContent.load()
        bind_content(content)

        self.assertEqual(match_minister_from_text("请韩爌入殿奏对").name, "韩爌")
        self.assertIsNone(match_minister_from_text("请东林诸臣议事"))

    def test_character_detail_batches_network_relation_refs(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_user_data_dir = web_app.user_data_dir
            old_user_data_path = web_app.user_data_path
            old_load_runtime_llm = web_app.load_runtime_llm
            old_verify_llm = session_module.verify_llm_available
            old_env = {
                key: os.environ.get(key)
                for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL")
            }

            def user_data_dir() -> Path:
                root.mkdir(parents=True, exist_ok=True)
                return root

            def user_data_path(*parts: str) -> str:
                path = root.joinpath(*parts)
                path.parent.mkdir(parents=True, exist_ok=True)
                return str(path)

            web_app.user_data_dir = user_data_dir
            web_app.user_data_path = user_data_path
            web_app.load_runtime_llm = lambda: {}
            session_module.verify_llm_available = lambda _config: None
            os.environ["OPENAI_API_KEY"] = "test-key"
            os.environ["OPENAI_BASE_URL"] = "https://example.test/v1"
            os.environ["OPENAI_MODEL"] = "test-model"

            game = None
            try:
                game = web_app.WebGame(fresh=True)
                old_web_game = web_app.web_game
                web_app.web_game = game
                statements: list[str] = []
                try:
                    game.db.conn.set_trace_callback(statements.append)
                    response = TestClient(web_app.app).get("/api/characters/韩爌")
                    game.db.conn.set_trace_callback(None)
                finally:
                    web_app.web_game = old_web_game

                self.assertEqual(response.status_code, 200)
                character = response.json()["character"]
                self.assertIn("network_profile", character)
                self.assertLessEqual(len(statements), 10)
                self.assertEqual(
                    sum("office, office_type, faction, power_id FROM characters WHERE name" in sql for sql in statements),
                    0,
                )
                self.assertLessEqual(
                    sum("FROM skill_grants" in sql for sql in statements),
                    1,
                )
                self.assertLessEqual(
                    sum("status, status_reason FROM characters WHERE name" in sql for sql in statements),
                    1,
                )
            finally:
                if game is not None:
                    game.db.conn.set_trace_callback(None)
                    game.session.close()
                web_app.user_data_dir = old_user_data_dir
                web_app.user_data_path = old_user_data_path
                web_app.load_runtime_llm = old_load_runtime_llm
                session_module.verify_llm_available = old_verify_llm
                for key, value in old_env.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_list_negotiation_agreements_batches_task_lookup(self) -> None:
        with TemporaryDirectory() as tmp:
            db = GameDB(str(Path(tmp) / "agreements.db"))
            try:
                agreement_ids = []
                for index in range(5):
                    cursor = db.conn.execute(
                        """
                        INSERT INTO negotiation_agreements
                        (turn_created, year_created, period_created, minister_name, topic)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (index + 1, 1628, index + 1, "韩爌", f"议题{index}"),
                    )
                    agreement_ids.append(int(cursor.lastrowid))
                for agreement_id in agreement_ids:
                    db.conn.execute(
                        """
                        INSERT INTO negotiation_tasks (agreement_id, description, task_kind)
                        VALUES (?, ?, ?)
                        """,
                        (agreement_id, f"任务{agreement_id}", "general"),
                    )
                db.conn.commit()

                statements: list[str] = []
                db.conn.set_trace_callback(statements.append)
                rows = db.list_negotiation_agreements(limit=10)
                db.conn.set_trace_callback(None)

                task_selects = [
                    sql for sql in statements
                    if "FROM negotiation_tasks" in sql and "agreement_id IN" in sql
                ]
                self.assertEqual(len(rows), 5)
                self.assertTrue(all(len(row["tasks"]) == 1 for row in rows))
                self.assertEqual(len(task_selects), 1)
            finally:
                db.close()

    def test_state_payload_batches_character_card_queries(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_user_data_dir = web_app.user_data_dir
            old_user_data_path = web_app.user_data_path
            old_load_runtime_llm = web_app.load_runtime_llm
            old_verify_llm = session_module.verify_llm_available
            old_env = {
                key: os.environ.get(key)
                for key in (
                    "OPENAI_API_KEY",
                    "OPENAI_BASE_URL",
                    "OPENAI_MODEL",
                    "MING_SIM_SERVER_USERS",
                    "MING_SIM_AUTH_USERS",
                    "MING_SIM_ADMIN_USERS",
                    "MING_SIM_SERVER_ADMINS",
                )
            }

            def user_data_dir() -> Path:
                root.mkdir(parents=True, exist_ok=True)
                return root

            def user_data_path(*parts: str) -> str:
                path = root.joinpath(*parts)
                path.parent.mkdir(parents=True, exist_ok=True)
                return str(path)

            web_app.user_data_dir = user_data_dir
            web_app.user_data_path = user_data_path
            web_app.load_runtime_llm = lambda: {}
            session_module.verify_llm_available = lambda _config: None
            os.environ["OPENAI_API_KEY"] = "test-key"
            os.environ["OPENAI_BASE_URL"] = "https://example.test/v1"
            os.environ["OPENAI_MODEL"] = "test-model"
            os.environ.pop("MING_SIM_SERVER_USERS", None)
            os.environ.pop("MING_SIM_AUTH_USERS", None)
            os.environ.pop("MING_SIM_ADMIN_USERS", None)
            os.environ.pop("MING_SIM_SERVER_ADMINS", None)

            game = None
            try:
                game = web_app.WebGame(fresh=True)
                statements: list[str] = []
                game.db.conn.set_trace_callback(statements.append)
                payload = game.state_payload()
                game.db.conn.set_trace_callback(None)
                state_bytes = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
                minister_bytes = len(json.dumps(payload["ministers"], ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
                region_bytes = len(json.dumps(payload["regions"], ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
                army_bytes = len(json.dumps(payload["armies"], ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
                power_bytes = len(json.dumps(payload["powers"], ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
                issue_bytes = len(json.dumps(payload["issues"], ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
                legacy_bytes = len(json.dumps(payload["legacies"], ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
                map_bytes = len(json.dumps(payload["map_nodes"], ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

                self.assertNotIn("character_index", payload)
                self.assertNotIn("organizations", payload)
                self.assertNotIn("monthly_followups", payload)
                self.assertNotIn("region_warning", payload)
                self.assertNotIn("army_warning", payload)
                self.assertNotIn("power_warning", payload)
                self.assertNotIn("region", payload["map_nodes"][0])
                self.assertNotIn("buildings", payload["map_nodes"][0])
                self.assertNotIn("armies", payload["map_nodes"][0])
                self.assertIn("minister_fields", payload)
                self.assertIn("region_fields", payload)
                self.assertIn("army_fields", payload)
                self.assertIn("power_fields", payload)
                self.assertIn("issue_fields", payload)
                self.assertIn("legacy_fields", payload)
                self.assertIsInstance(payload["ministers"][0], list)
                self.assertIsInstance(payload["regions"][0], list)
                self.assertIsInstance(payload["armies"][0], list)
                self.assertIsInstance(payload["powers"][0], list)
                self.assertIsInstance(payload["issues"][0], list)
                self.assertIsInstance(payload["legacies"][0], list)
                self.assertLessEqual(state_bytes, 46_000)
                self.assertLessEqual(minister_bytes, 20_000)
                self.assertLessEqual(region_bytes, 6_500)
                self.assertLessEqual(army_bytes, 4_500)
                self.assertLessEqual(power_bytes, 3_500)
                self.assertLessEqual(issue_bytes, 2_000)
                self.assertLessEqual(legacy_bytes, 1_600)
                self.assertLessEqual(map_bytes, 2_500)
                self.assertEqual(
                    sum("status, status_reason FROM characters WHERE name" in sql for sql in statements),
                    0,
                )
                self.assertEqual(
                    sum("power_id FROM characters WHERE name" in sql for sql in statements),
                    0,
                )
                self.assertEqual(
                    sum("FROM portrait_assets WHERE asset_id" in sql for sql in statements),
                    0,
                )
                self.assertLessEqual(len(statements), 80)
                self.assertLessEqual(sum("FROM conversation_goals" in sql for sql in statements), 2)
                self.assertLessEqual(sum("FROM minister_stances" in sql for sql in statements), 1)
                field_names = payload["minister_fields"]
                self.assertNotIn("style", field_names)
                self.assertNotIn("personal_skills", field_names)
                self.assertNotIn("conversation_goals", field_names)
                self.assertNotIn("stance_notes", field_names)
                self.assertNotIn("portrait_dna_seed", field_names)
                self.assertNotIn("skills", field_names)
                self.assertNotIn("summary", field_names)
                self.assertNotIn("age_label", field_names)
                self.assertNotIn("power_id", field_names)
                self.assertNotIn("status_label", field_names)
                self.assertNotIn("career_state", field_names)
                self.assertNotIn("start_age", field_names)
                self.assertLessEqual(len(payload["ministers"][0]), 8)

                old_web_game = web_app.web_game
                web_app.web_game = game
                try:
                    response = TestClient(web_app.app).get("/api/characters")
                    org_response = TestClient(web_app.app).get("/api/organizations")
                    map_response = TestClient(web_app.app).get("/api/map")
                    buildings_response = TestClient(web_app.app).get("/api/buildings")
                    followup_response = TestClient(web_app.app).get("/api/monthly_followups")
                    situation_response = TestClient(web_app.app).get("/api/situation_reports")
                finally:
                    web_app.web_game = old_web_game
                self.assertEqual(response.status_code, 200)
                index_payload = response.json()
                self.assertIn("character_fields", index_payload)
                index = index_payload["characters"]
                self.assertEqual(len(index), len(game.content.characters))
                self.assertIsInstance(index[0], list)
                self.assertLessEqual(
                    len(json.dumps(index_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")),
                    20_000,
                )
                self.assertNotIn("portrait_status", index[0])
                self.assertNotIn("portrait_dna_seed", index[0])
                self.assertNotIn("summary", index_payload["character_fields"])
                self.assertNotIn("status_label", index_payload["character_fields"])
                self.assertNotIn("power_name", index_payload["character_fields"])
                self.assertEqual(org_response.status_code, 200)
                organization_payload = org_response.json()
                self.assertIn("org_person_fields", organization_payload)
                self.assertIn("org_slot_fields", organization_payload)
                self.assertIn("org_institution_fields", organization_payload)
                self.assertEqual(organization_payload["org_person_fields"], list(ORG_PERSON_FIELDS))
                self.assertEqual(organization_payload["org_slot_fields"], list(ORG_SLOT_FIELDS))
                self.assertEqual(organization_payload["org_institution_fields"], list(ORG_INSTITUTION_FIELDS))
                self.assertIsInstance(organization_payload["institutions"][0], list)
                self.assertLessEqual(
                    len(json.dumps(organization_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")),
                    23_000,
                )
                institution = organization_payload["institutions"][0]
                slots = institution[list(ORG_INSTITUTION_FIELDS).index("slots")]
                self.assertIsInstance(slots[0], list)
                holders = slots[0][list(ORG_SLOT_FIELDS).index("holders")]
                self.assertIsInstance(holders[0], list)
                self.assertEqual(map_response.status_code, 200)
                map_payload = map_response.json()
                self.assertIn("node_fields", map_payload)
                self.assertIn("building_fields", map_payload)
                self.assertEqual(map_payload["node_fields"], list(MAP_NODE_FIELDS))
                self.assertEqual(map_payload["building_fields"], list(BUILDING_FIELDS))
                full_nodes = map_payload["nodes"]
                self.assertTrue(full_nodes)
                self.assertIsInstance(full_nodes[0], list)
                self.assertLessEqual(
                    len(json.dumps(map_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")),
                    20_500,
                )
                self.assertEqual(buildings_response.status_code, 200)
                buildings_payload = buildings_response.json()
                self.assertEqual(buildings_payload["building_fields"], list(BUILDING_FIELDS))
                self.assertIsInstance(buildings_payload["buildings"][0], list)
                self.assertLessEqual(
                    len(json.dumps(buildings_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")),
                    5_000,
                )
                self.assertEqual(followup_response.status_code, 200)
                followup_payload = followup_response.json()
                self.assertIn("followup_fields", followup_payload)
                self.assertIn("followup_defaults", followup_payload)
                self.assertEqual(followup_payload["followup_fields"], list(MONTHLY_FOLLOWUP_FIELDS))
                self.assertIsInstance(followup_payload["followups"][0], list)
                self.assertLessEqual(
                    len(json.dumps(followup_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")),
                    5_800,
                )
                self.assertEqual(situation_response.status_code, 200)
                situation_payload = situation_response.json()
                self.assertIn("region_warning", situation_payload)
                self.assertIn("army_warning", situation_payload)
                self.assertIn("power_warning", situation_payload)
            finally:
                if game is not None:
                    game.db.conn.set_trace_callback(None)
                    game.session.close()
                web_app.user_data_dir = old_user_data_dir
                web_app.user_data_path = old_user_data_path
                web_app.load_runtime_llm = old_load_runtime_llm
                session_module.verify_llm_available = old_verify_llm
                for key, value in old_env.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_organization_payload_batches_character_identity_snapshot(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_user_data_dir = web_app.user_data_dir
            old_user_data_path = web_app.user_data_path
            old_load_runtime_llm = web_app.load_runtime_llm
            old_verify_llm = session_module.verify_llm_available
            old_env = {
                key: os.environ.get(key)
                for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL")
            }

            def user_data_dir() -> Path:
                root.mkdir(parents=True, exist_ok=True)
                return root

            def user_data_path(*parts: str) -> str:
                path = root.joinpath(*parts)
                path.parent.mkdir(parents=True, exist_ok=True)
                return str(path)

            web_app.user_data_dir = user_data_dir
            web_app.user_data_path = user_data_path
            web_app.load_runtime_llm = lambda: {}
            session_module.verify_llm_available = lambda _config: None
            os.environ["OPENAI_API_KEY"] = "test-key"
            os.environ["OPENAI_BASE_URL"] = "https://example.test/v1"
            os.environ["OPENAI_MODEL"] = "test-model"

            game = None
            try:
                game = web_app.WebGame(fresh=True)
                statements: list[str] = []
                game.db.conn.set_trace_callback(statements.append)
                payload = game.organization_payload()
                game.db.conn.set_trace_callback(None)

                self.assertTrue(payload["institutions"])
                self.assertIn("unassigned", payload)
                self.assertEqual(
                    sum("FROM characters WHERE name=" in sql for sql in statements),
                    0,
                )
                self.assertEqual(
                    sum("status, status_reason FROM characters WHERE name" in sql for sql in statements),
                    0,
                )
                self.assertEqual(
                    sum("power_id FROM characters WHERE name" in sql for sql in statements),
                    0,
                )
                self.assertLessEqual(len(statements), 70)
            finally:
                if game is not None:
                    game.db.conn.set_trace_callback(None)
                    game.session.close()
                web_app.user_data_dir = old_user_data_dir
                web_app.user_data_path = old_user_data_path
                web_app.load_runtime_llm = old_load_runtime_llm
                session_module.verify_llm_available = old_verify_llm
                for key, value in old_env.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_character_index_payload_default_path_uses_bulk_snapshots(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_user_data_dir = web_app.user_data_dir
            old_user_data_path = web_app.user_data_path
            old_load_runtime_llm = web_app.load_runtime_llm
            old_verify_llm = session_module.verify_llm_available
            old_env = {
                key: os.environ.get(key)
                for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL")
            }

            def user_data_dir() -> Path:
                root.mkdir(parents=True, exist_ok=True)
                return root

            def user_data_path(*parts: str) -> str:
                path = root.joinpath(*parts)
                path.parent.mkdir(parents=True, exist_ok=True)
                return str(path)

            web_app.user_data_dir = user_data_dir
            web_app.user_data_path = user_data_path
            web_app.load_runtime_llm = lambda: {}
            session_module.verify_llm_available = lambda _config: None
            os.environ["OPENAI_API_KEY"] = "test-key"
            os.environ["OPENAI_BASE_URL"] = "https://example.test/v1"
            os.environ["OPENAI_MODEL"] = "test-model"

            game = None
            try:
                game = web_app.WebGame(fresh=True)
                statements: list[str] = []
                game.db.conn.set_trace_callback(statements.append)
                payload = game.character_index_payload()
                game.db.conn.set_trace_callback(None)

                self.assertEqual(len(payload), len(game.content.characters))
                self.assertEqual(
                    sum("FROM characters WHERE name=" in sql for sql in statements),
                    0,
                )
                self.assertEqual(
                    sum("FROM portrait_assets WHERE asset_id" in sql for sql in statements),
                    0,
                )
                self.assertLessEqual(
                    sum("FROM characters" in sql for sql in statements),
                    1,
                )
                self.assertLessEqual(
                    sum("FROM portrait_assets" in sql for sql in statements),
                    1,
                )
            finally:
                if game is not None:
                    game.db.conn.set_trace_callback(None)
                    game.session.close()
                web_app.user_data_dir = old_user_data_dir
                web_app.user_data_path = old_user_data_path
                web_app.load_runtime_llm = old_load_runtime_llm
                session_module.verify_llm_available = old_verify_llm
                for key, value in old_env.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_recommend_hidden_official_prefilters_recommenders_from_bulk_snapshot(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_user_data_dir = web_app.user_data_dir
            old_user_data_path = web_app.user_data_path
            old_load_runtime_llm = web_app.load_runtime_llm
            old_verify_llm = session_module.verify_llm_available
            old_env = {
                key: os.environ.get(key)
                for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL")
            }

            def user_data_dir() -> Path:
                root.mkdir(parents=True, exist_ok=True)
                return root

            def user_data_path(*parts: str) -> str:
                path = root.joinpath(*parts)
                path.parent.mkdir(parents=True, exist_ok=True)
                return str(path)

            web_app.user_data_dir = user_data_dir
            web_app.user_data_path = user_data_path
            web_app.load_runtime_llm = lambda: {}
            session_module.verify_llm_available = lambda _config: None
            os.environ["OPENAI_API_KEY"] = "test-key"
            os.environ["OPENAI_BASE_URL"] = "https://example.test/v1"
            os.environ["OPENAI_MODEL"] = "test-model"

            game = None
            try:
                game = web_app.WebGame(fresh=True)
                statements: list[str] = []
                game.db.conn.set_trace_callback(statements.append)
                payload = game.recommend_hidden_official()
                game.db.conn.set_trace_callback(None)

                self.assertIn("minister", payload)
                self.assertEqual(
                    sum("SELECT power_id FROM characters WHERE name=" in sql for sql in statements),
                    0,
                )
                self.assertEqual(
                    sum("SELECT status, status_reason FROM characters WHERE name=" in sql for sql in statements),
                    0,
                )
                self.assertLessEqual(
                    sum("SELECT name, office, office_type, faction, portrait_id, power_id, birth_year, status, status_reason" in sql for sql in statements),
                    1,
                )
                self.assertEqual(
                    sum("SELECT name, status FROM characters" in sql for sql in statements),
                    0,
                )
                self.assertLessEqual(
                    sum("SELECT name, status, status_reason, power_id FROM characters" in sql for sql in statements),
                    1,
                )
            finally:
                if game is not None:
                    game.db.conn.set_trace_callback(None)
                    game.session.close()
                web_app.user_data_dir = old_user_data_dir
                web_app.user_data_path = old_user_data_path
                web_app.load_runtime_llm = old_load_runtime_llm
                session_module.verify_llm_available = old_verify_llm
                for key, value in old_env.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
