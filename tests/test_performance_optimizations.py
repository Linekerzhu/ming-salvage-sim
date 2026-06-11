import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

import web_app
from ming_sim.content import GameContent
from ming_sim.context import bind_content, match_minister_from_text, npc_network_recommendations
from ming_sim.db import GameDB
import ming_sim.session as session_module


class PerformanceOptimizationTests(unittest.TestCase):
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

                self.assertNotIn("character_index", payload)
                self.assertNotIn("organizations", payload)
                self.assertNotIn("monthly_followups", payload)
                self.assertNotIn("buildings", payload["map_nodes"][0])
                self.assertNotIn("armies", payload["map_nodes"][0])
                self.assertIn("minister_fields", payload)
                self.assertIsInstance(payload["ministers"][0], list)
                self.assertLessEqual(state_bytes, 95_000)
                self.assertLessEqual(minister_bytes, 45_000)
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

                old_web_game = web_app.web_game
                web_app.web_game = game
                try:
                    response = TestClient(web_app.app).get("/api/characters")
                    org_response = TestClient(web_app.app).get("/api/organizations")
                    map_response = TestClient(web_app.app).get("/api/map")
                    followup_response = TestClient(web_app.app).get("/api/monthly_followups")
                finally:
                    web_app.web_game = old_web_game
                self.assertEqual(response.status_code, 200)
                index = response.json()["characters"]
                self.assertEqual(len(index), len(game.content.characters))
                self.assertNotIn("portrait_status", index[0])
                self.assertNotIn("portrait_dna_seed", index[0])
                self.assertEqual(org_response.status_code, 200)
                organization_payload = org_response.json()
                holder = next(
                    (
                        row
                        for institution in organization_payload["institutions"]
                        for slot in institution["slots"]
                        for row in slot["holders"]
                    ),
                    None,
                )
                self.assertIsNotNone(holder)
                self.assertIn("name", holder)
                self.assertIn("office", holder)
                self.assertNotIn("conversation_goals", holder)
                self.assertNotIn("stance_notes", holder)
                self.assertNotIn("portrait_id", holder)
                self.assertEqual(map_response.status_code, 200)
                full_nodes = map_response.json()["nodes"]
                self.assertTrue(any("armies" in node for node in full_nodes))
                self.assertTrue(any("buildings" in node for node in full_nodes))
                self.assertEqual(followup_response.status_code, 200)
                self.assertIn("followups", followup_response.json())
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
