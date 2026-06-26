import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim.db import GameDB
from ming_sim.custody import record_custody_from_status_item, public_custody_payload
from ming_sim.conditions import public_condition_payload
from ming_sim.punishments import apply_punishment_changes, public_punishment_payload


def _fresh(tmp: str):
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    state = db.load_state()
    return db, state


class PunishmentRecordTests(unittest.TestCase):
    def test_tongue_cut_records_punishment_and_condition(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            name = str(db.conn.execute(
                "SELECT name FROM characters WHERE status='active' AND power_id='ming' LIMIT 1"
            ).fetchone()["name"])

            applied = apply_punishment_changes(db, state, [{
                "name": name,
                "taxonomy": "ordinary",
                "punishment": "割舌",
                "severity": 5,
                "stage": "executed",
                "executor": "锦衣卫",
                "reason": "禁其妄言",
            }], source_kind="test", source_id="tongue")

            self.assertEqual(applied[0]["punishment_key"], "tongue_cut")
            self.assertEqual(applied[0]["side_effect"]["condition"]["system"], "speech")
            row = db.conn.execute(
                "SELECT system, label, severity FROM character_conditions WHERE name=?",
                (name,),
            ).fetchone()
            self.assertEqual(str(row["system"]), "speech")
            self.assertEqual(str(row["label"]), "舌伤")
            self.assertEqual(int(row["severity"]), 5)
            public = public_punishment_payload(db, name)
            self.assertIn("割舌", public["summary"])

    def test_ming_five_exile_and_death_apply_status(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            names = [str(r["name"]) for r in db.conn.execute(
                "SELECT name FROM characters WHERE status='active' AND power_id='ming' LIMIT 2"
            ).fetchall()]
            exile_name, death_name = names

            applied = apply_punishment_changes(db, state, [
                {"name": exile_name, "taxonomy": "ming_five", "punishment": "流刑", "stage": "executed"},
                {"name": death_name, "taxonomy": "ming_five", "punishment": "死刑", "stage": "executed"},
            ], source_kind="test", source_id="five")

            self.assertEqual(applied[0]["side_effect"]["status"], "exiled")
            self.assertEqual(applied[1]["side_effect"]["status"], "dead")
            exile_row = db.conn.execute("SELECT status FROM characters WHERE name=?", (exile_name,)).fetchone()
            death_row = db.conn.execute("SELECT status, hp FROM characters WHERE name=?", (death_name,)).fetchone()
            self.assertEqual(str(exile_row["status"]), "exiled")
            self.assertEqual(str(death_row["status"]), "dead")
            self.assertEqual(int(death_row["hp"]), 0)

    def test_gong_punishment_records_punishment_and_medical_record(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            name = str(db.conn.execute(
                "SELECT name FROM characters WHERE status='active' AND power_id='ming' LIMIT 1"
            ).fetchone()["name"])

            applied = apply_punishment_changes(db, state, [{
                "name": name,
                "taxonomy": "ancient_five",
                "punishment": "宫刑",
                "severity": 5,
                "stage": "executed",
                "executor": "锦衣卫",
                "reason": "奉旨强制宫刑",
            }], source_kind="test", source_id="gong")

            self.assertEqual(applied[0]["punishment_key"], "gong")
            self.assertEqual(applied[0]["side_effect"]["condition"]["system"], "reproductive")
            self.assertGreaterEqual(len(applied[0]["side_effect"]["conditions"]), 7)
            punishment_row = db.conn.execute(
                "SELECT label FROM character_punishments WHERE name=? AND punishment_key='gong'",
                (name,),
            ).fetchone()
            self.assertEqual(str(punishment_row["label"]), "宫刑")
            sex_row = db.conn.execute("SELECT sex FROM characters WHERE name=?", (name,)).fetchone()
            self.assertEqual(str(sex_row["sex"]), "eunuch")
            payload = public_condition_payload(db, name)
            groups = {str(group["key"]): group for group in payload["groups"]}
            self.assertIn("organic", groups)
            self.assertIn("pathological", groups)
            titles = " ".join(
                str(item.get("title") or "")
                for group in payload["groups"]
                for item in group.get("items", [])
            )
            self.assertIn("左侧睾丸：缺失", titles)
            self.assertIn("右侧睾丸：缺失", titles)
            self.assertIn("阴茎：缺失", titles)
            self.assertIn("绝育", titles)
            self.assertIn("尿道狭窄", titles)
            self.assertIn("生殖伤残", payload["tags"])

    def test_gong_catalog_forces_ancient_five_identity_transform(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            name = str(db.conn.execute(
                "SELECT name FROM characters WHERE status='active' AND power_id='ming' LIMIT 1"
            ).fetchone()["name"])

            applied = apply_punishment_changes(db, state, [{
                "name": name,
                "taxonomy": "ordinary",
                "punishment": "宫刑",
                "severity": 2,
                "stage": "executed",
                "reason": "处宫刑",
            }], source_kind="test", source_id="gong-catalog")

            self.assertEqual(applied[0]["taxonomy"], "ancient_five")
            self.assertEqual(applied[0]["severity"], 5)
            self.assertTrue(applied[0]["side_effect"]["conditions"])
            self.assertEqual(
                str(db.conn.execute("SELECT sex FROM characters WHERE name=?", (name,)).fetchone()["sex"]),
                "eunuch",
            )

    def test_tingzhang_catalog_forces_ming_five_label(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            name = str(db.conn.execute(
                "SELECT name FROM characters WHERE status='active' AND power_id='ming' LIMIT 1"
            ).fetchone()["name"])

            applied = apply_punishment_changes(db, state, [{
                "name": name,
                "taxonomy": "ordinary",
                "punishment": "廷杖",
                "severity": 2,
                "stage": "executed",
                "reason": "廷杖示惩",
            }], source_kind="test", source_id="tingzhang-catalog")

            self.assertEqual(applied[0]["punishment_key"], "zhang")
            self.assertEqual(applied[0]["taxonomy"], "ming_five")
            self.assertEqual(applied[0]["label"], "杖刑")

    def test_dialogue_prompt_no_longer_lists_tingzhang_as_ordinary(self):
        import inspect
        from ming_sim import dialogue_audit

        source = inspect.getsource(dialogue_audit)

        self.assertIn("普通酷刑/伤残用 taxonomy=ordinary，例如 punishment=割舌|割耳|断腿|拷掠|夹棍。", source)
        self.assertNotIn("普通酷刑/伤残用 taxonomy=ordinary，例如 punishment=割舌|割耳|断腿|拷掠|夹棍|廷杖。", source)

    def test_voluntary_castration_conversion_writes_medical_record_without_punishment(self):
        from ming_sim.content import GameContent
        from ming_sim.personnel_actions import convert_character_to_eunuch

        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            content = GameContent.load()
            name = "韩爌"

            convert_character_to_eunuch(
                db,
                state,
                content,
                name,
                force=False,
                source="奏对自愿净身入内廷",
                new_office="司礼监随堂太监",
            )

            punishment_count = int(db.conn.execute(
                "SELECT COUNT(*) c FROM character_punishments WHERE name=?",
                (name,),
            ).fetchone()["c"])
            self.assertEqual(punishment_count, 0)
            sex_row = db.conn.execute("SELECT sex FROM characters WHERE name=?", (name,)).fetchone()
            self.assertEqual(str(sex_row["sex"]), "eunuch")
            payload = public_condition_payload(db, name)
            titles = " ".join(
                str(item.get("title") or "")
                for group in payload["groups"]
                for item in group.get("items", [])
            )
            self.assertIn("左侧睾丸：缺失", titles)
            self.assertIn("阴茎：缺失", titles)
            self.assertIn("绝育", titles)

    def test_corporal_punishments_write_structured_medical_facts(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            names = [str(r["name"]) for r in db.conn.execute(
                "SELECT name FROM characters WHERE status='active' AND power_id='ming' LIMIT 2"
            ).fetchall()]
            ear_name, leg_name = names

            apply_punishment_changes(db, state, [
                {"name": ear_name, "punishment": "割耳", "severity": 4, "stage": "executed", "reason": "割去右耳示众"},
                {"name": leg_name, "punishment": "断腿", "severity": 5, "stage": "executed", "reason": "打断左腿"},
            ], source_kind="test", source_id="body-parts")

            ear_payload = public_condition_payload(db, ear_name)
            leg_payload = public_condition_payload(db, leg_name)
            ear_titles = " ".join(
                str(item.get("title") or "")
                for group in ear_payload["groups"]
                for item in group.get("items", [])
            )
            leg_titles = " ".join(
                str(item.get("title") or "")
                for group in leg_payload["groups"]
                for item in group.get("items", [])
            )
            self.assertIn("右耳：缺失", ear_titles)
            self.assertIn("左腿：骨折", leg_titles)
            self.assertIn("行走：行走久立困难", leg_titles)

    def test_death_penalty_closes_existing_custody_context(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            name = str(db.conn.execute(
                "SELECT name FROM characters WHERE status='active' AND power_id='ming' LIMIT 1"
            ).fetchone()["name"])
            record_custody_from_status_item(
                db,
                state,
                {"name": name, "reason": "锦衣卫拿入昭狱", "facility": "北镇抚司昭狱"},
                source_kind="test",
                source_id="custody",
            )

            applied = apply_punishment_changes(db, state, [{
                "name": name,
                "taxonomy": "ming_five",
                "punishment": "死刑",
                "stage": "executed",
                "reason": "奉旨处死",
            }], source_kind="test", source_id="death")

            self.assertEqual(applied[0]["side_effect"]["status"], "dead")
            self.assertEqual(public_custody_payload(db, name), {})
            row = db.conn.execute(
                "SELECT status, end_turn FROM character_custodies WHERE name=?",
                (name,),
            ).fetchone()
            self.assertEqual(str(row["status"]), "dead")
            self.assertEqual(int(row["end_turn"]), state.turn)

    def test_executed_punishments_apply_to_imprisoned_characters(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            names = [str(r["name"]) for r in db.conn.execute(
                "SELECT name FROM characters WHERE status='active' AND power_id='ming' LIMIT 2"
            ).fetchall()]
            exile_name, death_name = names
            for idx, name in enumerate(names):
                db.set_character_status(state, name, "imprisoned", "锦衣卫拿问")
                record_custody_from_status_item(
                    db,
                    state,
                    {"name": name, "reason": "锦衣卫拿入昭狱", "facility": "北镇抚司昭狱"},
                    source_kind="test",
                    source_id=f"prison-{idx}",
                )

            applied = apply_punishment_changes(db, state, [
                {"name": exile_name, "taxonomy": "ming_five", "punishment": "流刑", "stage": "executed"},
                {"name": death_name, "taxonomy": "ming_five", "punishment": "死刑", "stage": "executed"},
            ], source_kind="test", source_id="prison-sentence")

            self.assertEqual(applied[0]["side_effect"]["status"], "exiled")
            self.assertEqual(applied[1]["side_effect"]["status"], "dead")
            exile_row = db.conn.execute(
                "SELECT status FROM characters WHERE name=?", (exile_name,)
            ).fetchone()
            death_row = db.conn.execute(
                "SELECT status, hp FROM characters WHERE name=?", (death_name,)
            ).fetchone()
            self.assertEqual(str(exile_row["status"]), "exiled")
            self.assertEqual(str(death_row["status"]), "dead")
            self.assertEqual(int(death_row["hp"]), 0)
            custody_statuses = {
                str(r["name"]): str(r["status"])
                for r in db.conn.execute(
                    "SELECT name, status FROM character_custodies WHERE name IN (?, ?)",
                    (exile_name, death_name),
                ).fetchall()
            }
            self.assertEqual(custody_statuses[exile_name], "transferred")
            self.assertEqual(custody_statuses[death_name], "dead")


class PunishmentExtractionTests(unittest.TestCase):
    def test_apply_score_extraction_applies_punishment_changes(self):
        from ming_sim.content import GameContent
        from ming_sim.issues import apply_score_extraction, bind_content

        with TemporaryDirectory() as tmp:
            bind_content(GameContent.load())
            db, state = _fresh(tmp)
            name = str(db.conn.execute(
                "SELECT name FROM characters WHERE status='active' AND power_id='ming' LIMIT 1"
            ).fetchone()["name"])

            result = apply_score_extraction(db, state, {
                "punishment_changes": [{
                    "name": name,
                    "taxonomy": "ancient_five",
                    "punishment": "劓刑",
                    "severity": 4,
                    "stage": "executed",
                    "executor": "锦衣卫",
                    "reason": "重刑示众",
                }],
                "_directive_id": 99,
            })

            punishment = result["punishment_changes"][0]
            self.assertEqual(punishment["punishment_key"], "yi")
            self.assertEqual(punishment["source_kind"], "directive")
            self.assertEqual(punishment["source_id"], "99")
            conditions = db.conn.execute(
                "SELECT system, label FROM character_conditions WHERE name=?",
                (name,),
            ).fetchall()
            self.assertTrue(any(str(row["system"]) == "respiratory" for row in conditions))
            self.assertTrue(any("劓刑" in str(row["label"]) for row in conditions))


class EdictPunishmentScopeTests(unittest.TestCase):
    def test_scope_delta_guards_punishment_changes(self):
        from ming_sim.edict_outcome import _scope_delta

        ctx = {"directive": "着锦衣卫将崔呈秀割舌，禁其妄言", "allowed_issue_ids": []}
        out = _scope_delta({
            "punishment_changes": [
                {"name": "崔呈秀", "punishment": "割舌", "severity": 9},
                {"name": "李四", "punishment": "割舌", "severity": 5},
                {"name": "崔呈秀", "severity": 5},
            ]
        }, ctx)["punishment_changes"]

        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["name"], "崔呈秀")
        self.assertEqual(out[0]["severity"], 5)


if __name__ == "__main__":
    unittest.main()
