import hashlib
import os
import subprocess
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

import web_app
from ming_sim.db import GameDB


class WebMultiuserAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self._env = {
            key: os.environ.get(key)
            for key in (
                "MING_SIM_SERVER_USERS",
                "MING_SIM_AUTH_USERS",
                "MING_SIM_ADMIN_USERS",
                "MING_SIM_SERVER_ADMINS",
                "MING_SIM_ADMIN_USER",
                "MING_SIM_ADMIN_PASSWORD",
                "MING_SIM_ALLOW_CLIENT_LLM_CONFIG",
                "OPENAI_API_KEY",
                "OPENAI_BASE_URL",
                "OPENAI_MODEL",
                "MING_SIM_SESSION_TTL_SECONDS",
                "MING_SIM_WEB_CHAT_HISTORY_LIMIT",
                "MING_SIM_LOGIN_RATE_LIMIT_ATTEMPTS",
                "MING_SIM_LOGIN_RATE_LIMIT_WINDOW_SECONDS",
                "MING_SIM_JSON_LOGS",
                "MING_SIM_TRUST_PROXY_HEADERS",
                "MING_SIM_MAX_RUNNING_GAMES",
                "MING_SIM_MAX_CONCURRENT_TURNS",
            )
        }
        os.environ["MING_SIM_SERVER_USERS"] = "alice:pw,bob:pw2"
        os.environ.pop("MING_SIM_AUTH_USERS", None)
        os.environ.pop("MING_SIM_ADMIN_USERS", None)
        os.environ.pop("MING_SIM_SERVER_ADMINS", None)
        os.environ.pop("MING_SIM_ADMIN_USER", None)
        os.environ.pop("MING_SIM_ADMIN_PASSWORD", None)
        os.environ.pop("MING_SIM_ALLOW_CLIENT_LLM_CONFIG", None)
        os.environ["OPENAI_API_KEY"] = "test-key"
        os.environ["OPENAI_BASE_URL"] = "https://example.test/v1"
        os.environ["OPENAI_MODEL"] = "test-model"
        os.environ.pop("MING_SIM_SESSION_TTL_SECONDS", None)
        os.environ.pop("MING_SIM_WEB_CHAT_HISTORY_LIMIT", None)
        os.environ.pop("MING_SIM_LOGIN_RATE_LIMIT_ATTEMPTS", None)
        os.environ.pop("MING_SIM_LOGIN_RATE_LIMIT_WINDOW_SECONDS", None)
        os.environ.pop("MING_SIM_JSON_LOGS", None)
        os.environ.pop("MING_SIM_TRUST_PROXY_HEADERS", None)
        os.environ.pop("MING_SIM_MAX_RUNNING_GAMES", None)
        os.environ.pop("MING_SIM_MAX_CONCURRENT_TURNS", None)

        self._user_data_dir = web_app.user_data_dir
        self._user_data_path = web_app.user_data_path
        self._load_runtime_llm = web_app.load_runtime_llm
        self._upload_portrait_dir = web_app.UPLOAD_PORTRAIT_DIR

        root = Path(self.tmp.name)

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
        web_app.UPLOAD_PORTRAIT_DIR = user_data_path("uploads", "portraits")
        with web_app._auth_sessions_lock:
            web_app._auth_sessions.clear()
        with web_app._turn_resolution_capacity_lock:
            web_app._active_turn_resolutions = 0
        web_app._close_all_running_games()

    def tearDown(self) -> None:
        web_app._close_all_running_games()
        with web_app._auth_sessions_lock:
            web_app._auth_sessions.clear()
        with web_app._turn_resolution_capacity_lock:
            web_app._active_turn_resolutions = 0
        web_app.user_data_dir = self._user_data_dir
        web_app.user_data_path = self._user_data_path
        web_app.load_runtime_llm = self._load_runtime_llm
        web_app.UPLOAD_PORTRAIT_DIR = self._upload_portrait_dir
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def test_auth_required_for_menu_and_llm_is_server_managed(self) -> None:
        client = TestClient(web_app.app)

        me = client.get("/api/auth/me")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["auth_enabled"], True)
        self.assertEqual(me.json()["authenticated"], False)

        blocked = client.get("/api/menu/status")
        self.assertEqual(blocked.status_code, 401)

        bad_login = client.post("/api/auth/login", json={"username": "alice", "password": "wrong"})
        self.assertEqual(bad_login.status_code, 401)

        login = client.post("/api/auth/login", json={"username": "alice", "password": "pw"})
        self.assertEqual(login.status_code, 200)
        self.assertEqual(login.json()["username"], "alice")

        status = client.get("/api/menu/status")
        self.assertEqual(status.status_code, 200)
        payload = status.json()
        self.assertEqual(payload["auth"]["username"], "alice")
        self.assertEqual(payload["llm"]["model"], "test-model")
        self.assertEqual(payload["llm_client_configurable"], False)

        llm_write = client.post(
            "/api/menu/llm",
            json={
                "base_url": "https://example.test/v1",
                "model": "other-model",
                "api_key": "other-key",
            },
        )
        self.assertEqual(llm_write.status_code, 403)

    def test_health_and_ready_endpoints_are_public_operational_checks(self) -> None:
        client = TestClient(web_app.app)

        health = client.get("/healthz")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["status"], "ok")
        self.assertEqual(health.json()["auth_enabled"], True)
        self.assertIn("X-Request-ID", health.headers)

        ready = client.get("/readyz")
        self.assertIn(ready.status_code, (200, 503))
        payload = ready.json()
        self.assertIn("checks", payload)
        self.assertTrue(payload["checks"]["server_state_db"])
        self.assertTrue(payload["checks"]["content_dir"])

    def test_auth_disabled_keeps_local_menu_public(self) -> None:
        os.environ.pop("MING_SIM_SERVER_USERS", None)
        os.environ.pop("MING_SIM_AUTH_USERS", None)
        client = TestClient(web_app.app)

        me = client.get("/api/auth/me")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["auth_enabled"], False)
        self.assertEqual(me.json()["authenticated"], True)

        status = client.get("/api/menu/status")
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["auth"]["enabled"], False)

    def test_large_html_responses_are_gzipped(self) -> None:
        client = TestClient(web_app.app)

        response = client.get("/server-admin", headers={"Accept-Encoding": "gzip"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("content-encoding"), "gzip")
        self.assertIn("服务器后台", response.text)

    def test_static_assets_are_cache_controlled(self) -> None:
        assets_dir = Path(web_app.WEB_DIST) / "assets"
        asset = next(iter(assets_dir.glob("*.js")), None)
        if asset is None:
            self.skipTest("web/dist assets are not built")

        client = TestClient(web_app.app)

        script = client.get(f"/assets/{asset.name}")
        index = client.get("/")

        self.assertEqual(script.status_code, 200)
        self.assertEqual(script.headers.get("cache-control"), "public, max-age=31536000, immutable")
        self.assertEqual(index.status_code, 200)
        self.assertEqual(index.headers.get("cache-control"), "no-cache")
        self.assertNotIn("fonts.googleapis.com", index.text)
        self.assertNotIn("fonts.gstatic.com", index.text)

    def test_precompressed_media_is_not_gzipped(self) -> None:
        image = Path(web_app.WEB_DIST) / "bg_state.webp"
        if not image.exists():
            self.skipTest("web/dist optimized image assets are not built")

        client = TestClient(web_app.app)

        response = client.get("/bg_state.webp", headers={"Accept-Encoding": "gzip"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("cache-control"), "public, max-age=604800")
        self.assertIsNone(response.headers.get("content-encoding"))

    def test_dist_portraits_do_not_keep_orphaned_static_files(self) -> None:
        dist_portraits = Path(web_app.WEB_DIST) / "portraits"
        public_portraits = Path(web_app.WEB_DIST).parent / "public" / "portraits"
        if not dist_portraits.exists() or not public_portraits.exists():
            self.skipTest("web portrait assets are not available")
        root = Path(web_app.WEB_DIST).parents[1]
        prune_script = root / "web" / "scripts" / "prune-dist-assets.mjs"
        if prune_script.exists():
            try:
                subprocess.run(
                    ["node", str(prune_script)],
                    cwd=root,
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except (FileNotFoundError, subprocess.CalledProcessError) as exc:
                self.skipTest(f"dist asset prune script is not runnable: {exc}")

        public_files = {
            entry.name
            for entry in public_portraits.iterdir()
            if entry.is_file()
        }
        orphaned = sorted(
            entry.name
            for entry in dist_portraits.iterdir()
            if entry.is_file() and entry.name not in public_files
        )

        self.assertEqual(orphaned, [])

    def test_pbkdf2_password_and_expiring_session(self) -> None:
        salt = "unit-test-salt"
        rounds = 100_000
        digest = hashlib.pbkdf2_hmac("sha256", b"pw", salt.encode("utf-8"), rounds).hex()
        self.assertTrue(web_app._verify_password(f"pbkdf2_sha256${rounds}${salt}${digest}", "pw"))
        self.assertFalse(web_app._verify_password(f"pbkdf2_sha256${rounds}${salt}${digest}", "wrong"))

        os.environ["MING_SIM_SESSION_TTL_SECONDS"] = "300"
        token = web_app._new_auth_session("alice")
        self.assertEqual(web_app._session_username(token), "alice")
        with web_app._auth_sessions_lock:
            web_app._auth_sessions[token]["expires_at"] = time.time() - 1
        self.assertEqual(web_app._session_username(token), "")
        with web_app._auth_sessions_lock:
            self.assertNotIn(token, web_app._auth_sessions)

    def test_auth_session_survives_memory_cache_loss(self) -> None:
        client = TestClient(web_app.app)
        login = client.post("/api/auth/login", json={"username": "alice", "password": "pw"})
        self.assertEqual(login.status_code, 200)
        token = client.cookies.get(web_app._AUTH_COOKIE)
        self.assertTrue(token)
        self.assertEqual(web_app._session_counts_by_user().get("alice"), 1)

        with web_app._auth_sessions_lock:
            web_app._auth_sessions.clear()

        self.assertEqual(web_app._session_counts_by_user().get("alice"), 1)
        self.assertEqual(web_app._session_username(token), "alice")
        status = client.get("/api/menu/status")
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["auth"]["username"], "alice")

    def test_login_failures_are_rate_limited_and_success_clears_bucket(self) -> None:
        os.environ["MING_SIM_LOGIN_RATE_LIMIT_ATTEMPTS"] = "2"
        os.environ["MING_SIM_LOGIN_RATE_LIMIT_WINDOW_SECONDS"] = "300"
        client = TestClient(web_app.app)

        self.assertEqual(client.post("/api/auth/login", json={"username": "alice", "password": "bad"}).status_code, 401)
        self.assertEqual(client.post("/api/auth/login", json={"username": "alice", "password": "bad"}).status_code, 401)
        limited = client.post("/api/auth/login", json={"username": "alice", "password": "bad"})
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.json()["detail"]["code"], "rate_limited")
        self.assertIn("Retry-After", limited.headers)

        bob = TestClient(web_app.app)
        self.assertEqual(bob.post("/api/auth/login", json={"username": "bob", "password": "pw2"}).status_code, 200)

    def test_login_rate_limit_ignores_forwarded_for_without_trusted_proxy(self) -> None:
        os.environ["MING_SIM_LOGIN_RATE_LIMIT_ATTEMPTS"] = "1"
        os.environ["MING_SIM_LOGIN_RATE_LIMIT_WINDOW_SECONDS"] = "300"
        client = TestClient(web_app.app)

        first = client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "bad"},
            headers={"x-forwarded-for": "203.0.113.10"},
        )
        self.assertEqual(first.status_code, 401)

        second = client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "bad"},
            headers={"x-forwarded-for": "203.0.113.11"},
        )
        self.assertEqual(second.status_code, 429)

    def test_recent_chat_history_loads_bounded_window(self) -> None:
        db = GameDB(str(Path(self.tmp.name) / "chat_window.db"))
        try:
            for index in range(5):
                db.append_chat_message("韩爌", 1, "user", f"m{index}")
            for index in range(3):
                db.append_chat_message("魏忠贤", 1, "minister", f"w{index}")

            history = db.load_recent_chat_history(2)
            self.assertEqual([item["content"] for item in history["韩爌"]], ["m3", "m4"])
            self.assertEqual([item["content"] for item in history["魏忠贤"]], ["w1", "w2"])
        finally:
            db.close()

    def test_server_admin_overview_requires_admin(self) -> None:
        admin = TestClient(web_app.app)
        player = TestClient(web_app.app)

        blocked = admin.get("/api/server_admin/overview")
        self.assertEqual(blocked.status_code, 401)

        self.assertEqual(player.post("/api/auth/login", json={"username": "bob", "password": "pw2"}).status_code, 200)
        self.assertEqual(player.get("/api/server_admin/overview").status_code, 403)
        self.assertEqual(player.get("/api/admin/tables").status_code, 403)

        login = admin.post("/api/auth/login", json={"username": "alice", "password": "pw"})
        self.assertEqual(login.status_code, 200)
        self.assertEqual(login.json()["is_admin"], True)
        overview = admin.get("/api/server_admin/overview")
        self.assertEqual(overview.status_code, 200)
        payload = overview.json()
        self.assertEqual(payload["admin_users"], ["alice"])
        self.assertEqual({item["username"] for item in payload["users"]}, {"alice", "bob"})
        self.assertEqual(payload["llm"]["client_configurable"], False)

    def test_mutation_response_includes_state_to_avoid_followup_fetch(self) -> None:
        class DummySession:
            def close(self) -> None:
                pass

        class DummyDb:
            def __init__(self) -> None:
                self.values = {}

            def kv_set(self, key: str, value: str) -> None:
                self.values[key] = value

        class DummyContent:
            characters = {"韩爌": object()}

        class DummyWebGame:
            def __init__(self) -> None:
                self.session = DummySession()
                self.db = DummyDb()
                self.content = DummyContent()
                self.favorites = set()

            def state_payload(self):
                return {"favorites": sorted(self.favorites), "marker": "single-response-state"}

        client = TestClient(web_app.app)
        self.assertEqual(client.post("/api/auth/login", json={"username": "alice", "password": "pw"}).status_code, 200)
        web_app._set_running_game_for_user("alice", DummyWebGame())
        try:
            response = client.post("/api/favorites/%E9%9F%A9%E7%88%8C")
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["favorites"], ["韩爌"])
            self.assertEqual(payload["state"]["favorites"], ["韩爌"])
            self.assertEqual(payload["state"]["marker"], "single-response-state")
        finally:
            web_app._set_running_game_for_user("alice", None)

    def test_users_get_isolated_game_instances_and_db_paths(self) -> None:
        class DummySession:
            def close(self) -> None:
                pass

        class DummyWebGame:
            def __init__(self, fresh: bool = False, username: str = "") -> None:
                self.username = username.strip()
                self.db_path = web_app._db_path_for_user(self.username)
                Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
                if fresh and os.path.exists(self.db_path):
                    os.remove(self.db_path)
                Path(self.db_path).write_text("dummy db", encoding="utf-8")
                self.session = DummySession()

            def state_payload(self):
                return {"username": self.username, "db_path": self.db_path}

        original_web_game = web_app.WebGame
        web_app.WebGame = DummyWebGame
        alice = TestClient(web_app.app)
        bob = TestClient(web_app.app)
        try:
            self.assertEqual(alice.post("/api/auth/login", json={"username": "alice", "password": "pw"}).status_code, 200)
            self.assertEqual(bob.post("/api/auth/login", json={"username": "bob", "password": "pw2"}).status_code, 200)

            self.assertEqual(alice.post("/api/menu/new_game").status_code, 200)
            self.assertEqual(bob.post("/api/menu/new_game").status_code, 200)

            with web_app._web_games_lock:
                alice_game = web_app._web_games["alice"]
                bob_game = web_app._web_games["bob"]
            self.assertNotEqual(alice_game.db_path, bob_game.db_path)
            self.assertIn("/users/", alice_game.db_path)
            self.assertIn("/users/", bob_game.db_path)
            self.assertTrue(os.path.isfile(alice_game.db_path))
            self.assertTrue(os.path.isfile(bob_game.db_path))

            self.assertEqual(alice.get("/api/menu/status").json()["auth"]["username"], "alice")
            self.assertEqual(bob.get("/api/menu/status").json()["auth"]["username"], "bob")
        finally:
            web_app.WebGame = original_web_game

    def test_server_admin_can_close_user_game(self) -> None:
        class DummySession:
            def close(self) -> None:
                pass

        class DummyState:
            year = 1628
            period = 1
            turn = 3

        class DummyWebGame:
            def __init__(self, fresh: bool = False, username: str = "") -> None:
                self.username = username.strip()
                self.db_path = web_app._db_path_for_user(self.username)
                Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
                Path(self.db_path).write_text("dummy db", encoding="utf-8")
                self.session = DummySession()
                self.session.campaign_id = "campaign-test"
                self.state = DummyState()

            def state_payload(self):
                return {"username": self.username}

        original_web_game = web_app.WebGame
        web_app.WebGame = DummyWebGame
        admin = TestClient(web_app.app)
        bob = TestClient(web_app.app)
        try:
            self.assertEqual(admin.post("/api/auth/login", json={"username": "alice", "password": "pw"}).status_code, 200)
            self.assertEqual(bob.post("/api/auth/login", json={"username": "bob", "password": "pw2"}).status_code, 200)
            self.assertEqual(bob.post("/api/menu/new_game").status_code, 200)
            with web_app._web_games_lock:
                self.assertIn("bob", web_app._web_games)

            response = admin.post("/api/server_admin/users/bob/close_game")
            self.assertEqual(response.status_code, 200)
            with web_app._web_games_lock:
                self.assertNotIn("bob", web_app._web_games)
            bob_card = next(item for item in response.json()["overview"]["users"] if item["username"] == "bob")
            self.assertEqual(bob_card["running"], False)
        finally:
            web_app.WebGame = original_web_game

    def test_turn_resolution_rejects_concurrent_issue_requests(self) -> None:
        class DummySession:
            def close(self) -> None:
                pass

        class DummyWebGame:
            def __init__(self) -> None:
                self.session = DummySession()
                self.turn_resolution_lock = threading.Lock()

        client = TestClient(web_app.app)
        self.assertEqual(client.post("/api/auth/login", json={"username": "alice", "password": "pw"}).status_code, 200)
        game = DummyWebGame()
        self.assertTrue(game.turn_resolution_lock.acquire(blocking=False))
        web_app._set_running_game_for_user("alice", game)
        try:
            response = client.post("/api/decree/issue", json={})
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["detail"]["code"], "turn_resolution_in_progress")
        finally:
            game.turn_resolution_lock.release()
            web_app._set_running_game_for_user("alice", None)

    def test_running_game_capacity_limit_blocks_new_users(self) -> None:
        os.environ["MING_SIM_MAX_RUNNING_GAMES"] = "1"

        class DummySession:
            def close(self) -> None:
                pass

        class DummyWebGame:
            def __init__(self, fresh: bool = False, username: str = "") -> None:
                self.username = username.strip()
                self.session = DummySession()

            def state_payload(self):
                return {"username": self.username}

        original_web_game = web_app.WebGame
        web_app.WebGame = DummyWebGame
        alice = TestClient(web_app.app)
        bob = TestClient(web_app.app)
        try:
            self.assertEqual(alice.post("/api/auth/login", json={"username": "alice", "password": "pw"}).status_code, 200)
            self.assertEqual(bob.post("/api/auth/login", json={"username": "bob", "password": "pw2"}).status_code, 200)
            self.assertEqual(alice.post("/api/menu/new_game").status_code, 200)

            blocked = bob.post("/api/menu/new_game")
            self.assertEqual(blocked.status_code, 503)
            self.assertEqual(blocked.json()["detail"]["code"], "server_capacity_full")
        finally:
            web_app.WebGame = original_web_game

    def test_global_turn_resolution_capacity_limit_blocks_issue(self) -> None:
        os.environ["MING_SIM_MAX_CONCURRENT_TURNS"] = "1"

        class DummySession:
            def close(self) -> None:
                pass

        class DummyWebGame:
            def __init__(self) -> None:
                self.session = DummySession()
                self.turn_resolution_lock = threading.Lock()

            def portrait_generation_signatures(self):
                raise AssertionError("capacity check should run before mutation work")

        client = TestClient(web_app.app)
        self.assertEqual(client.post("/api/auth/login", json={"username": "alice", "password": "pw"}).status_code, 200)
        web_app._set_running_game_for_user("alice", DummyWebGame())
        with web_app._turn_resolution_capacity_lock:
            web_app._active_turn_resolutions = 1
        try:
            response = client.post("/api/decree/issue", json={})
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["detail"]["code"], "turn_resolution_capacity_full")
        finally:
            with web_app._turn_resolution_capacity_lock:
                web_app._active_turn_resolutions = 0
            web_app._set_running_game_for_user("alice", None)


if __name__ == "__main__":
    unittest.main()
