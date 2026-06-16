"""升级总案 API 冒烟测试：全部半即时层路由真实走一遍（TestClient，零 LLM）。"""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

import web_app
import ming_sim.session as session_module


class UpgradeApiSmokeTests(unittest.TestCase):
    def _with_game(self, fn):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_user_data_dir = web_app.user_data_dir
            old_user_data_path = web_app.user_data_path
            old_load_runtime_llm = web_app.load_runtime_llm
            old_verify_llm = session_module.verify_llm_available
            old_env = {k: os.environ.get(k)
                       for k in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL")}

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
                try:
                    fn(game, TestClient(web_app.app))
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

    def test_new_game_restarts_queue_worker_for_replaced_game(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_user_data_dir = web_app.user_data_dir
            old_user_data_path = web_app.user_data_path
            old_load_runtime_llm = web_app.load_runtime_llm
            old_verify_llm = session_module.verify_llm_available
            old_web_game = web_app.web_game
            old_env = {k: os.environ.get(k)
                       for k in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL")}

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
            try:
                web_app.web_game = web_app.WebGame(fresh=True)
                db_path = web_app.web_game.db_path
                from ming_sim.scheduler import _WORKERS
                self.assertIn(db_path, _WORKERS)

                client = TestClient(web_app.app)
                r = client.post("/api/menu/new_game")
                self.assertEqual(r.status_code, 200)
                self.assertIsNotNone(web_app.web_game)
                self.assertEqual(web_app.web_game.db_path, db_path)
                self.assertIn(db_path, _WORKERS)
                self.assertTrue(_WORKERS[db_path]._thread.is_alive())
            finally:
                web_app._set_running_game_for_user("", None)
                web_app.web_game = old_web_game
                web_app.user_data_dir = old_user_data_dir
                web_app.user_data_path = old_user_data_path
                web_app.load_runtime_llm = old_load_runtime_llm
                session_module.verify_llm_available = old_verify_llm
                for key, value in old_env.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_all_semirealtime_routes(self):
        def run(game, client):
            # 时钟
            r = client.get("/api/time")
            self.assertEqual(r.status_code, 200)
            self.assertIn("time", r.json())
            # 推进 3 天
            r = client.post("/api/time/advance", json={"days": 3, "stop_on_yellow": False})
            self.assertEqual(r.status_code, 200)
            self.assertGreaterEqual(r.json()["advanced"], 1)
            # 调速
            r = client.post("/api/time/speed", json={"speed": 3})
            self.assertEqual(r.status_code, 200)
            # 预警仪表
            r = client.get("/api/thresholds")
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r.json()["board"])
            # 御案
            r = client.get("/api/desk")
            self.assertEqual(r.status_code, 200)
            self.assertIn("attention_left", r.json())
            # 指令生命周期
            r = client.get("/api/directives/lifecycle")
            self.assertEqual(r.status_code, 200)
            # 信念与中兴
            r = client.get("/api/beliefs")
            self.assertEqual(r.status_code, 200)
            self.assertIn("shi", r.json())
            r = client.get("/api/zhongxing")
            self.assertEqual(r.status_code, 200)
            self.assertIn("current", r.json())
            self.assertTrue(r.json()["stage"])
            # 史笔
            r = client.get("/api/shibi")
            self.assertEqual(r.status_code, 200)
            # 文书互证 + 密查
            r = client.get("/api/veil/contradictions")
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r.json()["items"])  # 开局空饷分叉已播种
            r = client.post("/api/veil/investigate", json={
                "line": "changwei", "target_kind": "army", "target_id": "jingying"})
            self.assertEqual(r.status_code, 200)
            # 问责与买单
            r = client.post("/api/court/punish", json={
                "name": "韩爌", "severity": "light", "public": True, "reason": "试问责"})
            self.assertEqual(r.status_code, 200)
            r = client.post("/api/court/back", json={"name": "韩爌", "kind": "comfort"})
            self.assertEqual(r.status_code, 200)
            # 信号
            r = client.post("/api/court/signal", json={"kind": "zuiji"})
            self.assertEqual(r.status_code, 200)
            r = client.get("/api/court/leverage")
            self.assertEqual(r.status_code, 200)
            # 人才池（基座未挂载时 available=False 也应 200）
            r = client.get("/api/foundation/candidates?limit=3")
            self.assertEqual(r.status_code, 200)
            self.assertIn("candidates", r.json())
            # 批红一封（先造一封奏疏）
            from ming_sim import memorials, timeflow
            day = timeflow.ensure_active(game.db, game.state)
            memorials.reset_attention_for_day(game.db, day)
            mid = memorials.create_memorial(
                game.db, game.state, day=day, author_name="韩爌", org="内阁",
                kind="请旨", urgency=2, summary="冒烟测试请旨")
            r = client.post(f"/api/desk/{mid}/decide", json={"action": "approve"})
            self.assertEqual(r.status_code, 200)

        self._with_game(run)

    def test_public_goal_payload_is_compact_and_favicon_is_clean(self):
        def run(game, client):
            payload = game._conversation_goal_payload_from_rows([{
                "id": 1,
                "minister_name": "毕自严",
                "title": "核实财政草案",
                "score": 0,
                "conditions": [{"description": "明旨授权", "status": "pending"}],
                "last_delta": {
                    "audit": {"private_reason": "x" * 2000, "pre_audit": {"raw": "huge"}},
                    "public_hint": "毕自严领旨。",
                    "audit_confidence": 85,
                    "audit_status": "recorded",
                },
                "last_delta_json": "{}",
            }])[0]
            self.assertNotIn("last_delta", payload)
            self.assertNotIn("last_delta_json", payload)
            self.assertEqual(payload["public_hint"], "毕自严领旨。")
            self.assertEqual(payload["audit_confidence"], 85)
            self.assertEqual(payload["pending_conditions"][0]["description"], "明旨授权")

            r = client.get("/favicon.ico")
            self.assertEqual(r.status_code, 204)

        self._with_game(run)


if __name__ == "__main__":
    unittest.main()
