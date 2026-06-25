import json
import io
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient
import web_app
from ming_sim.custody import (
    dialogue_custody_brief,
    public_custody_payload,
    record_custody_from_status_item,
    sync_custodies_for_character_status,
)
from ming_sim.db import GameDB
from ming_sim.dialogue_audit import post_dialogue_audit
from ming_sim.models import LLMConfig
from ming_sim.session import GameSession


def _fresh(tmp: str):
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    state = db.load_state()
    return db, state


class CustodyRecordTests(unittest.TestCase):
    def test_record_custody_infers_zhaoyu_and_dialogue_pressure(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            name = str(db.conn.execute(
                "SELECT name FROM characters WHERE status='active' AND power_id='ming' LIMIT 1"
            ).fetchone()["name"])

            row = record_custody_from_status_item(
                db,
                state,
                {
                    "name": name,
                    "status": "imprisoned",
                    "reason": "锦衣卫拿入昭狱，严刑拷讯",
                    "coercion_goal": "交代党羽",
                },
                directive_text=f"着锦衣卫将{name}拿入昭狱，严刑拷讯，逼其交代党羽",
                source_kind="test",
                source_id="1",
            )

            self.assertEqual(row["agency"], "锦衣卫")
            self.assertEqual(row["facility"], "北镇抚司昭狱")
            self.assertEqual(row["severity"], 4)
            self.assertEqual(row["coercion_goal"], "交代党羽")
            public = public_custody_payload(db, name)
            self.assertIn("严刑威逼", public["tags"])
            self.assertIn("北镇抚司昭狱", public["summary"])
            brief = dialogue_custody_brief(db, name)
            self.assertIn("威逼强度4/5", brief)
            self.assertIn("被迫意味", brief)

    def test_status_change_closes_active_custody(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            names = [str(r["name"]) for r in db.conn.execute(
                "SELECT name FROM characters WHERE status='active' AND power_id='ming' LIMIT 2"
            ).fetchall()]
            dead_name, exile_name = names
            for idx, name in enumerate(names):
                record_custody_from_status_item(
                    db,
                    state,
                    {"name": name, "reason": "锦衣卫拿入昭狱", "facility": "北镇抚司昭狱"},
                    source_kind="test",
                    source_id=str(idx),
                )

            dead_closed = sync_custodies_for_character_status(db, state, dead_name, "dead", "死刑已决")
            exile_closed = sync_custodies_for_character_status(db, state, exile_name, "exiled", "流放三千里")

            self.assertEqual(dead_closed[0]["status"], "dead")
            self.assertEqual(exile_closed[0]["status"], "transferred")
            self.assertEqual(public_custody_payload(db, dead_name), {})
            self.assertEqual(dialogue_custody_brief(db, exile_name), "")
            statuses = {
                str(r["name"]): str(r["status"])
                for r in db.conn.execute(
                    "SELECT name, status FROM character_custodies WHERE name IN (?, ?)",
                    (dead_name, exile_name),
                ).fetchall()
            }
            self.assertEqual(statuses[dead_name], "dead")
            self.assertEqual(statuses[exile_name], "transferred")

    def test_imprisoned_character_can_be_summoned_for_interrogation(self):
        cfg = LLMConfig(api_key="test", base_url="http://test.invalid/v1", model="test-model")
        with TemporaryDirectory() as tmp:
            sess = GameSession(str(Path(tmp) / "g.db"), cfg, verify_llm=False)
            try:
                name = str(sess.db.conn.execute(
                    "SELECT name FROM characters WHERE status='active' AND power_id='ming' LIMIT 1"
                ).fetchone()["name"])
                character = sess._character(name)
                sess.db.set_character_status(sess.state, name, "imprisoned", "锦衣卫拿问")
                record_custody_from_status_item(
                    sess.db,
                    sess.state,
                    {"name": name, "reason": "锦衣卫拿入昭狱，严刑拷讯", "facility": "北镇抚司昭狱"},
                    source_kind="test",
                    source_id="summon",
                )

                ok, reason = sess.can_summon(character)
                augmented, _prepared = sess.prepare_chat_run(character, "朕要听你的供状。")

                self.assertTrue(ok, reason)
                self.assertIn("羁押/昭狱状态", augmented)
                self.assertIn("北镇抚司昭狱", augmented)
            finally:
                sess.close()

    def test_minister_prompt_allows_imprisoned_interrogation_route(self):
        prompt_path = Path(__file__).resolve().parents[1] / "content" / "prompts" / "minister_agent.md"
        prompt = prompt_path.read_text(encoding="utf-8")

        self.assertIn("命中 imprisoned（下狱）人物", prompt)
        self.assertIn("押来/提来/审问/问供", prompt)
        self.assertIn("summon_minister(canonical_name)", prompt)
        self.assertNotIn("已罢、下狱、流放、致仕、已故，不得绕成临时人物，须据状态回奏不可召见", prompt)

    def test_cli_choose_minister_allows_imprisoned_character(self):
        from ming_sim.cli.terminal import choose_minister

        cfg = LLMConfig(api_key="test", base_url="http://test.invalid/v1", model="test-model")
        with TemporaryDirectory() as tmp:
            sess = GameSession(str(Path(tmp) / "g.db"), cfg, verify_llm=False)
            try:
                rows = sess.db.conn.execute(
                    "SELECT name FROM characters WHERE status='active' AND power_id='ming' LIMIT 2"
                ).fetchall()
                name = str(rows[0]["name"])
                unavailable_name = str(rows[1]["name"]) if len(rows) > 1 else ""
                sess.db.set_character_status(sess.state, name, "imprisoned", "锦衣卫拿问")
                if unavailable_name:
                    sess.db.set_character_status(sess.state, unavailable_name, "dead", "病故")

                output = io.StringIO()
                with patch("builtins.input", side_effect=[name]), patch("sys.stdout", new=output):
                    selected = choose_minister(sess)

                self.assertIsNotNone(selected)
                self.assertEqual(selected.name, name)
                self.assertIn(name, output.getvalue())
                if unavailable_name:
                    self.assertNotIn(unavailable_name, output.getvalue())
            finally:
                sess.close()

    def test_severe_custody_can_force_dialogue_commitment(self):
        cfg = LLMConfig(api_key="test", base_url="http://test.invalid/v1", model="test-model")
        with TemporaryDirectory() as tmp:
            sess = GameSession(str(Path(tmp) / "g.db"), cfg, verify_llm=False)
            try:
                name = str(sess.db.conn.execute(
                    "SELECT name FROM characters WHERE status='active' AND power_id='ming' LIMIT 1"
                ).fetchone()["name"])
                character = sess._character(name)
                sess.db.set_character_status(sess.state, name, "imprisoned", "锦衣卫拿问")
                record_custody_from_status_item(
                    sess.db,
                    sess.state,
                    {
                        "name": name,
                        "reason": "锦衣卫拿入昭狱，严刑拷讯",
                        "facility": "北镇抚司昭狱",
                        "pressure": 4,
                        "coercion_goal": "迫使奉旨清查同党",
                    },
                    source_kind="test",
                    source_id="coerce",
                )

                class Audit:
                    def post(self, payload):
                        return {
                            "goal_decision": "new",
                            "goal_relation": "distinct_goal",
                            "action_kind": "secret_order",
                            "title": "清查同党",
                            "target_text": "逼其奉旨清查同党",
                            "stance": "caution",
                            "handshake_status": "none",
                            "goal_status": "active",
                            "score_delta": 0,
                            "score_after": 35,
                            "threshold": 70,
                            "conditions": [],
                            "blockers": ["心理量表未过线（35/70）"],
                            "agreement_action": "none",
                            "confidence": 92,
                            "public_hint": "尚未真心承诺。",
                            "private_reason": "原文只是惧祸。",
                        }

                post = post_dialogue_audit(
                    sess.db,
                    sess.state,
                    character,
                    "朕在昭狱中问你，此事若不奉旨，刑讯不止。",
                    "臣不敢不从，愿按旨交代同党线索。",
                    audit_client=Audit(),
                )

                self.assertEqual(post.goal_status, "active")
                self.assertEqual(post.handshake_status, "none")
                self.assertEqual(post.agreement_action, "none")
                self.assertNotIn("custody_coercion", post.raw)
                self.assertIn("尚未真心承诺", post.public_hint)
            finally:
                sess.close()

    def test_custody_coercion_does_not_override_identity_conversion_consent(self):
        cfg = LLMConfig(api_key="test", base_url="http://test.invalid/v1", model="test-model")
        with TemporaryDirectory() as tmp:
            sess = GameSession(str(Path(tmp) / "g.db"), cfg, verify_llm=False)
            try:
                name = str(sess.db.conn.execute(
                    "SELECT name FROM characters WHERE status='active' AND power_id='ming' LIMIT 1"
                ).fetchone()["name"])
                character = sess._character(name)
                sess.db.set_character_status(sess.state, name, "imprisoned", "锦衣卫拿问")
                record_custody_from_status_item(
                    sess.db,
                    sess.state,
                    {"name": name, "reason": "昭狱严刑", "facility": "北镇抚司昭狱", "pressure": 5},
                    source_kind="test",
                    source_id="identity",
                )

                class Audit:
                    def post(self, payload):
                        return {
                            "goal_decision": "new",
                            "goal_relation": "distinct_goal",
                            "action_kind": "castration",
                            "title": "没入内廷",
                            "target_text": "令其净身入内廷",
                            "stance": "support",
                            "handshake_status": "none",
                            "goal_status": "active",
                            "score_delta": 0,
                            "score_after": 40,
                            "threshold": 70,
                            "conditions": [],
                            "blockers": ["心理量表未过线（40/70）"],
                            "explicit_consent": False,
                            "agreement_action": "none",
                            "confidence": 92,
                            "public_hint": "严刑下屈服，不是自愿。",
                            "private_reason": "不具备明确自愿。",
                        }

                post = post_dialogue_audit(
                    sess.db,
                    sess.state,
                    character,
                    "若不净身入内廷，昭狱严刑不止。",
                    "臣不敢不从。",
                    audit_client=Audit(),
                )

                self.assertNotEqual(post.goal_status, "sealed")
                self.assertNotIn("custody_coercion", post.raw)
            finally:
                sess.close()

    def test_public_character_marks_imprisoned_as_summonable(self):
        old_user_data_dir = web_app.user_data_dir
        old_user_data_path = web_app.user_data_path
        old_load_runtime_llm = web_app.load_runtime_llm
        import ming_sim.session as session_module
        old_verify_llm = session_module.verify_llm_available
        old_env = {
            key: os.environ.get(key)
            for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL")
        }
        with TemporaryDirectory() as tmp:
            root = Path(tmp)

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
            game = web_app.WebGame(fresh=True)
            try:
                name = str(game.db.conn.execute(
                    "SELECT name FROM characters WHERE status='active' AND power_id='ming' LIMIT 1"
                ).fetchone()["name"])
                game.db.set_character_status(game.state, name, "imprisoned", "锦衣卫拿问")

                card = game.public_character(game.content.characters[name])
                old_web_game = web_app.web_game
                web_app.web_game = game
                try:
                    response = TestClient(web_app.app).get(f"/api/ministers/{name}/chat")
                finally:
                    web_app.web_game = old_web_game

                self.assertEqual(card["status"], "imprisoned")
                self.assertTrue(card["can_summon"])
                self.assertEqual(response.status_code, 200)
            finally:
                try:
                    from ming_sim.scheduler import stop_worker
                    stop_worker(game.db_path)
                finally:
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


class CustodyDrainTests(unittest.TestCase):
    def test_drain_imprisoned_status_creates_custody_record(self):
        cfg = LLMConfig(api_key="test", base_url="http://test.invalid/v1", model="test-model")
        with TemporaryDirectory() as tmp:
            sess = GameSession(str(Path(tmp) / "g.db"), cfg, verify_llm=False)
            try:
                name = str(sess.db.conn.execute(
                    "SELECT name FROM characters WHERE status='active' AND power_id='ming' "
                    "AND office_type NOT IN ('后宫') LIMIT 1"
                ).fetchone()["name"])
                delta = {
                    "character_status_changes": [{
                        "name": name,
                        "status": "imprisoned",
                        "reason": "锦衣卫拿问",
                        "agency": "锦衣卫",
                        "facility": "北镇抚司昭狱",
                        "pressure": 4,
                        "coercion_goal": "逼其接受旨意",
                    }]
                }
                cur = sess.db.conn.execute(
                    "INSERT INTO turn_directives (turn, year, period, text, source, status, "
                    "lifecycle_status, progress, integrity_actual, integrity_reported, outcome_delta, outcome_status) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        sess.state.turn,
                        sess.state.year,
                        sess.state.period,
                        f"着锦衣卫将{name}拿入昭狱，严刑逼其接受旨意",
                        "test",
                        "confirmed",
                        "done",
                        100,
                        100,
                        100,
                        json.dumps(delta, ensure_ascii=False),
                        "extracted",
                    ),
                )
                did = int(cur.lastrowid)
                sess.db.conn.commit()

                results = sess.drain_pending_outcomes()

                custody = results[0]["applied"]["character_status_changes"][0]["custody"]
                self.assertEqual(custody["facility"], "北镇抚司昭狱")
                stored = sess.db.conn.execute(
                    "SELECT status, agency, facility, severity, coercion_goal, source_kind, source_id "
                    "FROM character_custodies WHERE name=?",
                    (name,),
                ).fetchone()
                self.assertEqual(str(stored["status"]), "active")
                self.assertEqual(str(stored["agency"]), "锦衣卫")
                self.assertEqual(str(stored["facility"]), "北镇抚司昭狱")
                self.assertEqual(int(stored["severity"]), 4)
                self.assertEqual(str(stored["coercion_goal"]), "逼其接受旨意")
                self.assertEqual(str(stored["source_kind"]), "directive")
                self.assertEqual(str(stored["source_id"]), str(did))
            finally:
                sess.close()


if __name__ == "__main__":
    unittest.main()
