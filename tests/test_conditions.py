import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ming_sim.conditions import (
    add_condition,
    apply_condition_changes,
    apply_dialogue_answer_impairment,
    condition_summary,
    dialogue_condition_brief,
    public_condition_payload,
    speech_impairment_level,
    sync_castration_medical_record,
)
from ming_sim.db import GameDB


def _fresh(tmp: str):
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    state = db.load_state()
    return db, state


class CharacterConditionTests(unittest.TestCase):
    def test_add_condition_updates_public_payload_and_dialogue_brief(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            name = str(db.conn.execute(
                "SELECT name FROM characters WHERE status='active' AND power_id='ming' LIMIT 1"
            ).fetchone()["name"])

            row = add_condition(
                db,
                state,
                name,
                kind="punishment",
                system="speech",
                label="舌伤",
                severity=5,
                stage="disabled",
                note="诏命割舌",
                effects={"speech": "口齿含混，不能长篇奏对"},
                source_kind="test",
                source_id="1",
            )

            self.assertEqual(row["name"], name)
            self.assertEqual(row["system"], "speech")
            payload = public_condition_payload(db, name)
            self.assertIn("言语受损", payload["tags"])
            self.assertLess(payload["health_score"], 100)
            self.assertIn("舌伤", payload["summary"])
            brief = dialogue_condition_brief(db, name)
            self.assertIn("口齿", brief)
            self.assertIn("不得流利长篇奏对", brief)
            self.assertEqual(speech_impairment_level(db, name), 5)
            impaired = apply_dialogue_answer_impairment(
                db,
                name,
                "臣愿为陛下详陈此事，先查首恶，再按部核验党羽名单。",
            )
            self.assertIn("口舌受损", impaired)
            self.assertIn("……", impaired)
            self.assertNotIn("详陈此事，先查首恶，再按部核验", impaired)

    def test_dialogue_answer_impairment_leaves_normal_speech_unchanged(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            name = str(db.conn.execute(
                "SELECT name FROM characters WHERE status='active' AND power_id='ming' LIMIT 1"
            ).fetchone()["name"])

            answer = "臣愿为陛下详陈此事。"
            self.assertEqual(apply_dialogue_answer_impairment(db, name, answer), answer)

    def test_public_payload_includes_baseline_health_without_conditions(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            name = str(db.conn.execute(
                "SELECT name FROM characters WHERE status='active' AND power_id='ming' LIMIT 1"
            ).fetchone()["name"])

            payload = public_condition_payload(db, name)

            self.assertEqual(payload["title"], "病历")
            self.assertEqual(payload["conditions"], [])
            self.assertEqual(payload["groups"], [])
            self.assertEqual(payload["mortality_risk"], "stable")
            self.assertIn("体况平稳", payload["tags"])
            self.assertIn("暂无显性病历记录", payload["summary"])
            self.assertGreater(int(payload["hp"]), 0)
            self.assertGreaterEqual(int(payload["max_hp"]), int(payload["hp"]))

    def test_public_character_includes_baseline_health_detail(self):
        import web_app
        import ming_sim.session as session_module

        with TemporaryDirectory() as tmp:
            root = Path(tmp)

            def user_data_dir() -> Path:
                root.mkdir(parents=True, exist_ok=True)
                return root

            def user_data_path(*parts: str) -> str:
                path = root.joinpath(*parts)
                path.parent.mkdir(parents=True, exist_ok=True)
                return str(path)

            with (
                patch.object(web_app, "user_data_dir", user_data_dir),
                patch.object(web_app, "user_data_path", user_data_path),
                patch.object(web_app, "load_runtime_llm", lambda: {}),
                patch.object(session_module, "verify_llm_available", lambda _config: None),
                patch.dict(os.environ, {
                    "OPENAI_API_KEY": "test-key",
                    "OPENAI_BASE_URL": "https://example.test/v1",
                    "OPENAI_MODEL": "test-model",
                }, clear=False),
            ):
                game = web_app.WebGame(fresh=True)
                try:
                    name = str(game.db.conn.execute(
                        "SELECT name FROM characters WHERE status='active' AND power_id='ming' LIMIT 1"
                    ).fetchone()["name"])
                    card = game.public_character(game.content.characters[name], include_detail=True)

                    self.assertIn("medical_record", card)
                    self.assertIn("conditions", card)
                    self.assertIs(card["medical_record"], card["conditions"])
                    self.assertEqual(card["conditions"]["conditions"], [])
                    self.assertIn("体况平稳", card["conditions"]["tags"])
                    self.assertNotIn("castration", card)
                finally:
                    try:
                        from ming_sim.scheduler import stop_worker
                        stop_worker(game.db_path)
                    finally:
                        game.session.close()

    def test_public_character_traits_fallback_when_only_medical_traits_are_stored(self):
        import web_app
        import ming_sim.session as session_module

        with TemporaryDirectory() as tmp:
            root = Path(tmp)

            def user_data_dir() -> Path:
                root.mkdir(parents=True, exist_ok=True)
                return root

            def user_data_path(*parts: str) -> str:
                path = root.joinpath(*parts)
                path.parent.mkdir(parents=True, exist_ok=True)
                return str(path)

            with (
                patch.object(web_app, "user_data_dir", user_data_dir),
                patch.object(web_app, "user_data_path", user_data_path),
                patch.object(web_app, "load_runtime_llm", lambda: {}),
                patch.object(session_module, "verify_llm_available", lambda _config: None),
                patch.dict(os.environ, {
                    "OPENAI_API_KEY": "test-key",
                    "OPENAI_BASE_URL": "https://example.test/v1",
                    "OPENAI_MODEL": "test-model",
                }, clear=False),
            ):
                game = web_app.WebGame(fresh=True)
                try:
                    name = "王承恩"
                    game.db.conn.execute("DELETE FROM character_traits WHERE name=?", (name,))
                    game.db.conn.execute(
                        "INSERT OR REPLACE INTO character_traits (name, trait, valence) VALUES (?,?,?)",
                        (name, "尿路旧患", -1),
                    )
                    game.db.conn.commit()

                    card = game.public_character(game.content.characters[name], include_detail=True)
                    trait_keys = {item["key"] for item in card.get("traits", [])}

                    self.assertTrue(trait_keys)
                    self.assertNotIn("尿路旧患", trait_keys)
                finally:
                    try:
                        from ming_sim.scheduler import stop_worker
                        stop_worker(game.db_path)
                    finally:
                        game.session.close()

    def test_public_character_projects_eunuch_lore_to_medical_record(self):
        import web_app
        import ming_sim.session as session_module

        with TemporaryDirectory() as tmp:
            root = Path(tmp)

            def user_data_dir() -> Path:
                root.mkdir(parents=True, exist_ok=True)
                return root

            def user_data_path(*parts: str) -> str:
                path = root.joinpath(*parts)
                path.parent.mkdir(parents=True, exist_ok=True)
                return str(path)

            with (
                patch.object(web_app, "user_data_dir", user_data_dir),
                patch.object(web_app, "user_data_path", user_data_path),
                patch.object(web_app, "load_runtime_llm", lambda: {}),
                patch.object(session_module, "verify_llm_available", lambda _config: None),
                patch.dict(os.environ, {
                    "OPENAI_API_KEY": "test-key",
                    "OPENAI_BASE_URL": "https://example.test/v1",
                    "OPENAI_MODEL": "test-model",
                }, clear=False),
            ):
                game = web_app.WebGame(fresh=True)
                try:
                    name = "王承恩"
                    game.db.conn.execute(
                        "INSERT OR REPLACE INTO character_traits (name, trait, valence) VALUES (?,?,?)",
                        (name, "忠谨", 1),
                    )
                    game.db.conn.execute(
                        "INSERT OR REPLACE INTO character_traits (name, trait, valence) VALUES (?,?,?)",
                        (name, "尿路旧患", -1),
                    )
                    game.db.conn.commit()
                    card = game.public_character(game.content.characters[name], include_detail=True)
                    medical = card["medical_record"]

                    self.assertNotIn("castration", card)
                    self.assertIs(medical, card["conditions"])
                    self.assertIn("traits", card)
                    self.assertIn("忠谨", {item["key"] for item in card["traits"]})
                    self.assertNotIn("尿路旧患", {item["key"] for item in card["traits"]})
                    from ming_sim import court
                    court_card = court.court_payload(game.db, name)
                    self.assertIn("traits", court_card)
                    self.assertNotIn("castration", court_card)
                    group_labels = [group["label"] for group in medical["groups"]]
                    self.assertIn("器质性", group_labels)
                    self.assertIn("病理性", group_labels)
                    rendered = " ".join(
                        str(item.get("title") or "")
                        + " "
                        + str(item.get("note") or "")
                        for group in medical["groups"]
                        for item in group.get("items", [])
                    )
                    self.assertIn("左侧睾丸：缺失", rendered)
                    self.assertIn("性功能丧失", rendered)
                    self.assertNotIn("宝匣", rendered)
                    self.assertNotIn("旧匣", rendered)
                    self.assertNotIn("钥匙", rendered)
                    self.assertNotIn("铜柄", rendered)
                    self.assertNotIn("黄杨", rendered)
                finally:
                    try:
                        from ming_sim.scheduler import stop_worker
                        stop_worker(game.db_path)
                    finally:
                        game.session.close()

    def test_castration_organ_loss_notes_are_compacted_in_public_record(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            name = "韩爌"

            sync_castration_medical_record(
                db,
                state,
                name,
                forced=False,
                note="净身入内廷",
                source_kind="test",
                source_id="compact-note",
            )
            payload = public_condition_payload(db, name)
            organic = next(group for group in payload["groups"] if group["label"] == "器质性")
            organ_items = [
                item for item in organic["items"]
                if item.get("title") in {"左侧睾丸：缺失", "右侧睾丸：缺失", "阴茎：缺失"}
            ]

            self.assertEqual(len(organ_items), 3)
            self.assertTrue(all(not item.get("note") for item in organ_items))
            self.assertNotIn("/5", str(payload.get("summary") or ""))
            self.assertNotIn("性功能丧失", str(payload.get("summary") or ""))
            visible_notes = [
                str(item.get("note") or "")
                for group in payload["groups"]
                for item in group.get("items", [])
                if item.get("note")
            ]
            self.assertLessEqual(visible_notes.count("净身入内廷"), 1)
            rendered = " ".join(
                str(item.get("title") or "") + " " + str(item.get("note") or "")
                for group in payload["groups"]
                for item in group.get("items", [])
            )
            self.assertNotIn("宝匣", rendered)
            self.assertNotIn("旧匣", rendered)
            self.assertNotIn("钥匙", rendered)

    def test_condition_summary_hides_hidden_facts(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            name = str(db.conn.execute(
                "SELECT name FROM characters WHERE status='active' AND power_id='ming' LIMIT 1"
            ).fetchone()["name"])

            add_condition(
                db,
                state,
                name,
                kind="disease",
                system="respiratory",
                label="痰喘",
                severity=4,
                hidden=True,
                source_kind="test",
                source_id="hidden",
            )

            self.assertEqual(condition_summary(db, name)["conditions"], [])

    def test_terminal_condition_lowers_hp_without_killing(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            name = str(db.conn.execute(
                "SELECT name FROM characters WHERE status='active' AND power_id='ming' LIMIT 1"
            ).fetchone()["name"])

            row = add_condition(
                db,
                state,
                name,
                kind="terminal",
                system="circulatory",
                label="危笃",
                severity=5,
                stage="critical",
                note="病入膏肓，尚有一息",
                source_kind="test",
                source_id="terminal",
            )

            self.assertEqual(row["health"]["hp_after"], 1)
            stored = db.conn.execute(
                "SELECT hp, status FROM characters WHERE name=?",
                (name,),
            ).fetchone()
            self.assertEqual(int(stored["hp"]), 1)
            self.assertEqual(str(stored["status"]), "active")
            payload = public_condition_payload(db, name)
            self.assertEqual(payload["mortality_risk"], "terminal")
            self.assertEqual(payload["hp"], 1)

    def test_fatal_condition_marks_character_dead(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            name = str(db.conn.execute(
                "SELECT name FROM characters WHERE status='active' AND power_id='ming' LIMIT 1"
            ).fetchone()["name"])

            row = add_condition(
                db,
                state,
                name,
                kind="disease",
                system="general",
                label="暴毙",
                severity=5,
                stage="dead",
                note="急病暴毙",
                effects={"fatal": True},
                source_kind="test",
                source_id="fatal",
            )

            self.assertEqual(row["health"]["hp_after"], 0)
            self.assertEqual(row["health"]["status"], "dead")
            stored = db.conn.execute(
                "SELECT hp, status, status_reason FROM characters WHERE name=?",
                (name,),
            ).fetchone()
            self.assertEqual(int(stored["hp"]), 0)
            self.assertEqual(str(stored["status"]), "dead")
            self.assertIn("暴毙", str(stored["status_reason"]))


class ApplyConditionExtractionTests(unittest.TestCase):
    def test_catalog_condition_uses_fixed_effects_over_llm_effects(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            name = str(db.conn.execute(
                "SELECT name FROM characters WHERE status='active' AND power_id='ming' LIMIT 1"
            ).fetchone()["name"])

            applied = apply_condition_changes(db, state, [{
                "name": name,
                "kind": "terminal",
                "system": "circulatory",
                "label": "风寒",
                "severity": 5,
                "stage": "dead",
                "reason": "他昨夜染风寒",
                "confidence": 0.92,
                "decision_source": "llm",
                "effects": {"fatal": True, "impact": "LLM自由编造后果"},
            }], source_kind="dialogue", source_id="wind")

            self.assertEqual(applied[0]["label"], "风寒")
            self.assertEqual(applied[0]["system"], "respiratory")
            self.assertEqual(applied[0]["severity"], 2)
            self.assertEqual(applied[0]["stage"], "active")
            effects = json.loads(db.conn.execute(
                "SELECT effects_json FROM character_conditions WHERE name=? AND condition_key='natural:wind_cold'",
                (name,),
            ).fetchone()["effects_json"])
            self.assertTrue(effects["catalog_fixed"])
            self.assertNotEqual(effects.get("impact"), "LLM自由编造后果")
            self.assertNotIn("fatal", effects)

    def test_apply_score_extraction_persists_condition_change(self):
        from ming_sim.content import GameContent
        from ming_sim.issues import apply_score_extraction, bind_content

        with TemporaryDirectory() as tmp:
            bind_content(GameContent.load())
            db, state = _fresh(tmp)
            name = str(db.conn.execute(
                "SELECT name FROM characters WHERE status='active' AND power_id='ming' LIMIT 1"
            ).fetchone()["name"])

            result = apply_score_extraction(db, state, {
                "condition_changes": [{
                    "name": name,
                    "kind": "prison_effect",
                    "system": "musculoskeletal",
                    "label": "狱中杖创",
                    "severity": 4,
                    "stage": "serious",
                    "reason": "昭狱拷掠",
                    "effects": {"ability_delta": "行走久立困难"},
                }],
                "_directive_id": 88,
            })

            self.assertEqual(result["condition_changes"][0]["label"], "狱中杖创")
            stored = db.conn.execute(
                "SELECT kind, system, severity, source_kind, source_id, effects_json "
                "FROM character_conditions WHERE name=?",
                (name,),
            ).fetchone()
            self.assertEqual(str(stored["kind"]), "prison_effect")
            self.assertEqual(str(stored["system"]), "musculoskeletal")
            self.assertEqual(int(stored["severity"]), 4)
            self.assertEqual(str(stored["source_kind"]), "directive")
            self.assertEqual(str(stored["source_id"]), "88")
            self.assertEqual(json.loads(stored["effects_json"])["ability_delta"], "行走久立困难")


if __name__ == "__main__":
    unittest.main()
