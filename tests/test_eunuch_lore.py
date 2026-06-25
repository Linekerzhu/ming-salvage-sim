"""净身恶趣味 E2a 测试：宝处置、奴性分野、还阳传言、全尸执念。零 LLM。"""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim import eunuch_lore as el, timeflow
from ming_sim.conditions import public_condition_payload
from ming_sim.db import GameDB


def _fresh(tmp: str):
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    state = db.load_state()
    day = timeflow.ensure_active(db, state)
    return db, state, day


class SeedTests(unittest.TestCase):
    def test_existing_eunuchs_seeded_with_lore(self):
        with TemporaryDirectory() as tmp:
            db, _, _ = _fresh(tmp)
            self.assertGreater(db.conn.execute("SELECT COUNT(*) c FROM eunuch_lore").fetchone()["c"], 0)
            for nm in ("魏忠贤", "王体乾", "王承恩"):
                lore = el.get_lore(db, nm)
                self.assertIsNotNone(lore)
                self.assertIn(lore["bao_status"], (el.BAO_KEPT, el.BAO_FORFEIT, el.BAO_LOST))

    def test_non_eunuch_has_no_lore(self):
        with TemporaryDirectory() as tmp:
            db, _, _ = _fresh(tmp)
            self.assertIsNone(el.get_lore(db, "韩爌"))  # 外朝大臣无净身记录


class RecordCastrationTests(unittest.TestCase):
    def test_forced_forfeits_bao_and_high_servility(self):
        with TemporaryDirectory() as tmp:
            db, _, day = _fresh(tmp)
            r = el.record_castration(db, "韩爌", forced=True, day=day)
            self.assertEqual(r["bao_status"], el.BAO_FORFEIT)
            self.assertTrue(r["forced"])
            lore = el.get_lore(db, "韩爌")
            self.assertGreaterEqual(lore["servility"], 70)  # 强制净身后旧念更重
            self.assertEqual(lore["castration_method"], "净身房登记")
            self.assertEqual(lore["knife_tool"], "")
            self.assertEqual(lore["anesthesia"], "")
            self.assertEqual(lore["urinary_aftereffect"], "")
            self.assertEqual(lore["voice_body_change"], "")
            self.assertTrue(lore["trauma_response"])
            self.assertTrue(lore["private_fixation"])
            self.assertEqual(lore["psychosexual_state"], "")
            public = el.public_lore_payload(db, "韩爌")
            self.assertIn("宝贝官库", public["bao_label"])
            self.assertIn("净身后遗症", public["condition_line"])
            self.assertIn("惊创", public["condition_line"])
            self.assertNotIn("宝匣", public["condition_line"])
            self.assertNotIn("钥匙", public["condition_line"])
            self.assertNotIn("癖性", public["condition_line"])

    def test_dialogue_text_can_maintain_lore_fields(self):
        with TemporaryDirectory() as tmp:
            db, _, day = _fresh(tmp)
            el.record_castration(db, "韩爌", forced=True, day=day)
            result = el.update_lore_from_text(
                db,
                "韩爌",
                "以后他的宝用黑漆楠木匣，油炸封蜡，约二两八钱，一大一小，油封后发硬。"
                "此人近来漏尿尿闭，嗓音尖薄，常有幻肢痛和贤者模式。",
                day=day + 1,
            )
            self.assertIn("updated", result)
            lore = el.get_lore(db, "韩爌")
            self.assertEqual(lore["bao_container"], "黑漆楠木匣")
            self.assertEqual(lore["bao_preservation"], "油炸封蜡")
            self.assertEqual(lore["bao_weight"], "约二两八钱")
            self.assertEqual(lore["bao_shape"], "一大一小")
            self.assertEqual(lore["bao_texture"], "油封后发硬")
            self.assertIn("漏尿", lore["urinary_aftereffect"])
            self.assertIn("嗓音尖薄", lore["voice_body_change"])
            self.assertIn("幻肢痛", lore["trauma_response"])
            self.assertIn("贤者模式", lore["psychosexual_state"])

            payload = public_condition_payload(db, "韩爌")
            group_labels = [str(group["label"]) for group in payload["groups"]]
            self.assertIn("器质性", group_labels)
            self.assertIn("病理性", group_labels)
            self.assertIn("心理/照护", group_labels)
            rendered = " ".join(
                str(item.get("title") or "")
                for group in payload["groups"]
                for item in group.get("items", [])
            )
            self.assertIn("尿道狭窄", rendered)
            self.assertIn("漏尿", rendered)
            self.assertIn("宝贝/全尸执念", rendered)
            self.assertNotIn("宝匣", rendered)
            self.assertNotIn("旧匣", rendered)
            self.assertNotIn("钥匙", rendered)

    def test_plain_clean_word_does_not_imply_jingshenfang_method(self):
        with TemporaryDirectory() as tmp:
            db, _, day = _fresh(tmp)
            el.record_castration(
                db,
                "韩爌",
                forced=True,
                day=day,
                detail_text="奉旨宫刑，刑房薄刃，无麻。",
            )
            before = el.get_lore(db, "韩爌")
            self.assertEqual(before["castration_method"], "净身房登记")

            el.update_lore_from_text(db, "韩爌", "此人衣褶洁净，差事谨慎。", day=day + 1)

            after = el.get_lore(db, "韩爌")
            self.assertEqual(after["castration_method"], "净身房登记")

    def test_record_castration_syncs_medical_record_idempotently(self):
        with TemporaryDirectory() as tmp:
            db, _, day = _fresh(tmp)
            first = el.record_castration(db, "韩爌", forced=True, day=day)
            count_first = int(db.conn.execute(
                "SELECT COUNT(*) c FROM character_conditions "
                "WHERE name=? AND condition_key LIKE 'castration:%'",
                ("韩爌",),
            ).fetchone()["c"])

            second = el.record_castration(db, "韩爌", forced=True, day=day)
            count_second = int(db.conn.execute(
                "SELECT COUNT(*) c FROM character_conditions "
                "WHERE name=? AND condition_key LIKE 'castration:%'",
                ("韩爌",),
            ).fetchone()["c"])

            self.assertGreaterEqual(count_first, 7)
            self.assertEqual(count_first, count_second)
            self.assertGreaterEqual(len(first["medical_record"]), 7)
            self.assertGreaterEqual(len(second["medical_record"]), 7)

    def test_castration_medical_sync_repairs_legacy_hp_overcompression(self):
        with TemporaryDirectory() as tmp:
            db, _, day = _fresh(tmp)
            el.record_castration(db, "韩爌", forced=False, day=day)
            db.conn.execute("UPDATE characters SET hp=1, max_hp=100 WHERE name=?", ("韩爌",))
            db.conn.commit()

            el.record_castration(db, "韩爌", forced=False, day=day)

            row = db.conn.execute("SELECT hp, max_hp FROM characters WHERE name=?", ("韩爌",)).fetchone()
            self.assertGreaterEqual(int(row["hp"]), 60)
            payload = public_condition_payload(db, "韩爌")
            self.assertNotEqual(payload["mortality_risk"], "terminal")
            self.assertIn("生殖伤残", payload["tags"])

    def test_record_castration_does_not_regex_parse_scheme_text_by_default(self):
        with TemporaryDirectory() as tmp:
            db, _, day = _fresh(tmp)
            r = el.record_castration(
                db,
                "韩爌",
                forced=True,
                day=day,
                detail_text=(
                    "净身房行事，铜柄宫刀，无麻；宝约二两八钱，一大一小，"
                    "油封后发硬，油炸封蜡，收黄杨木描金匣。"
                ),
            )
            self.assertNotIn("scheme_applied", r)
            lore = el.get_lore(db, "韩爌")
            self.assertEqual(lore["castration_method"], "净身房登记")
            self.assertEqual(lore["knife_tool"], "")
            self.assertEqual(lore["anesthesia"], "")
            self.assertEqual(lore["bao_weight"], "")
            self.assertEqual(lore["bao_shape"], "")
            self.assertEqual(lore["bao_texture"], "")
            self.assertEqual(lore["bao_preservation"], "宝贝由官库收存")
            self.assertEqual(lore["bao_container"], "")

    def test_record_castration_legacy_regex_update_requires_explicit_opt_in(self):
        old = os.environ.get("MING_SIM_ENABLE_LEGACY_EUNUCH_LORE_REGEX_UPDATE")
        os.environ["MING_SIM_ENABLE_LEGACY_EUNUCH_LORE_REGEX_UPDATE"] = "1"
        try:
            with TemporaryDirectory() as tmp:
                db, _, day = _fresh(tmp)
                r = el.record_castration(
                    db,
                    "韩爌",
                    forced=True,
                    day=day,
                    detail_text=(
                        "净身房行事，铜柄宫刀，无麻；宝约二两八钱，一大一小，"
                        "油封后发硬，油炸封蜡，收黄杨木描金匣。"
                    ),
                )
                self.assertIn("scheme_applied", r)
                lore = el.get_lore(db, "韩爌")
                self.assertEqual(lore["castration_method"], "净身房夜割")
                self.assertEqual(lore["knife_tool"], "铜柄宫刀")
                self.assertEqual(lore["anesthesia"], "无麻，冷汗硬熬")
                self.assertEqual(lore["bao_weight"], "约二两八钱")
                self.assertEqual(lore["bao_shape"], "一大一小")
                self.assertEqual(lore["bao_texture"], "油封后发硬")
                self.assertEqual(lore["bao_preservation"], "油炸封蜡")
                self.assertEqual(lore["bao_container"], "黄杨木描金匣")
        finally:
            if old is None:
                os.environ.pop("MING_SIM_ENABLE_LEGACY_EUNUCH_LORE_REGEX_UPDATE", None)
            else:
                os.environ["MING_SIM_ENABLE_LEGACY_EUNUCH_LORE_REGEX_UPDATE"] = old

    def test_harsh_castration_scheme_has_playable_risk_profile_and_care_cost(self):
        old = os.environ.get("MING_SIM_ENABLE_LEGACY_EUNUCH_LORE_REGEX_UPDATE")
        os.environ["MING_SIM_ENABLE_LEGACY_EUNUCH_LORE_REGEX_UPDATE"] = "1"
        try:
            self._harsh_castration_scheme_has_playable_risk_profile_and_care_cost()
        finally:
            if old is None:
                os.environ.pop("MING_SIM_ENABLE_LEGACY_EUNUCH_LORE_REGEX_UPDATE", None)
            else:
                os.environ["MING_SIM_ENABLE_LEGACY_EUNUCH_LORE_REGEX_UPDATE"] = old

    def _harsh_castration_scheme_has_playable_risk_profile_and_care_cost(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            name = "韩爌"
            el.record_castration(
                db,
                name,
                forced=True,
                day=day,
                detail_text=(
                    "净身房行事，铜柄宫刀，无麻；宝约二两八钱，一大一小，"
                    "油封后发硬，油炸封蜡，官库石灰封存，收黄杨木描金匣。"
                    "近来漏尿尿闭，嗓音尖薄，幻肢痛。"
                ),
            )

            public = el.public_lore_payload(db, name)
            profile = public["scheme_profile"]
            self.assertTrue(profile["explicit"])
            self.assertEqual(profile["tier"], "酷烈高危")
            self.assertGreaterEqual(int(profile["risk_score"]), 70)
            self.assertGreater(int(profile["care_cost_delta"]), 0)
            self.assertTrue(any("无麻" in item for item in profile["effects"]))

            risk = el.assignment_risk_profile(
                db,
                name,
                "夜守刑房封签，久候拿问净房旧案。",
                domains=["investigation", "inner"],
            )
            self.assertLessEqual(int(risk["score_delta"]), -8)
            self.assertTrue(any("净身方案画像" in item for item in risk["risks"]))

            state.metrics["内库"] = 50
            db.save_state(state)
            care = el.apply_eunuch_care(db, state, name, mode="urinary", note="准调养尿闭旧患。")
            self.assertEqual(care["cost"], 7)
            self.assertIn("方案调养+4", care["outcome"])

    def test_bao_specimen_details_change_scheme_risk_and_dispatch(self):
        with TemporaryDirectory() as tmp:
            db, _state, day = _fresh(tmp)
            heavy = "韩爌"
            shrunken = "钱谦益"
            el.record_castration(
                db,
                heavy,
                forced=True,
                day=day,
                detail_text=(
                    "净身房行事，铜柄宫刀，无麻；宝约二两八钱，偏沉粗大，一大一小，"
                    "油封后发硬，油炸封蜡，收黄杨木描金匣。"
                ),
            )
            el.update_lore_from_text(
                db,
                heavy,
                "净身房行事，铜柄宫刀，无麻；宝约二两八钱，偏沉粗大，一大一小，"
                "油封后发硬，油炸封蜡，收黄杨木描金匣。",
                day=day,
            )
            el.record_castration(
                db,
                shrunken,
                forced=True,
                day=day,
                detail_text=(
                    "净身房行事，铜柄宫刀，无麻；宝约一两二钱，小如雀卵，瘪坠不匀，"
                    "干皱如旧枣，官库石灰封存，收白签灰瓮。"
                ),
            )
            el.update_lore_from_text(
                db,
                shrunken,
                "净身房行事，铜柄宫刀，无麻；宝约一两二钱，小如雀卵，瘪坠不匀，"
                "干皱如旧枣，官库石灰封存，收白签灰瓮。",
                day=day,
            )

            heavy_profile = el.public_lore_payload(db, heavy)["scheme_profile"]
            shrunken_profile = el.public_lore_payload(db, shrunken)["scheme_profile"]

            self.assertTrue(any("宝相偏沉" in item for item in heavy_profile["effects"]))
            self.assertTrue(any("宝形偏异" in item for item in shrunken_profile["effects"]))
            self.assertTrue(any("宝相寒缩" in item for item in shrunken_profile["effects"]))
            self.assertGreater(
                int(heavy_profile["bao_security"]),
                int(shrunken_profile["bao_security"]),
            )
            self.assertGreaterEqual(int(shrunken_profile["trauma_risk"]), 70)
            self.assertGreaterEqual(int(heavy_profile["trauma_risk"]), 70)

            risk = el.assignment_risk_profile(
                db,
                shrunken,
                "命其查官库封签宝匣旧案，核验净身房旧册。",
                domains=["investigation", "inner"],
            )

            self.assertLess(int(risk["score_delta"]), 0)
            self.assertTrue(any("净身方案画像" in item for item in risk["risks"]))
            self.assertTrue(risk["dispatch_strategies"])

    def test_harsh_scheme_adds_extra_complication_window_until_general_care(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            name = "韩爌"
            el.record_castration(
                db,
                name,
                forced=True,
                day=day,
                detail_text=(
                    "净身房行事，铜柄宫刀，无麻；宝油炸封蜡，官库石灰封存。"
                    "近来漏尿尿闭，嗓音尖薄，幻肢痛。"
                ),
            )
            el.update_lore_from_text(
                db,
                name,
                "净身房行事，铜柄宫刀，无麻；宝油炸封蜡，官库石灰封存。"
                "近来漏尿尿闭，嗓音尖薄，幻肢痛。",
                day=day,
            )
            db.conn.execute("DELETE FROM eunuch_lore WHERE name!=?", (name,))
            db.conn.commit()

            evs = el.castration_complication_tick(db, state, 7)

            self.assertEqual(len(evs), 1)
            self.assertTrue(evs[0]["scheme_surge"])
            self.assertEqual(evs[0]["scheme_profile"]["tier"], "酷烈高危")
            self.assertIn("方案压迫", evs[0]["effect"])
            self.assertIn("方案画像", evs[0]["detail"])

            state.metrics["内库"] = 80
            db.save_state(state)
            care = el.apply_eunuch_care(db, state, name, mode="general", note="总调养压住净房旧患。")

            self.assertTrue(care["ok"])
            self.assertEqual(care["trait"], "御前调养")
            self.assertEqual(el.castration_complication_tick(db, state, 13), [])

    def test_careful_scheme_does_not_add_extra_complication_window(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            name = "钱谦益"
            el.record_castration(
                db,
                name,
                forced=False,
                day=day,
                detail_text=(
                    "内书堂老匠细净，麻沸散浅麻，先沐浴焚香，"
                    "宝匣交本人收执，香料腌藏，黄杨木描金匣。"
                ),
            )
            db.conn.execute("DELETE FROM eunuch_lore WHERE name!=?", (name,))
            db.conn.commit()

            self.assertEqual(el.castration_complication_tick(db, state, 7), [])

    def test_voluntary_keeps_bao_and_lower_servility(self):
        with TemporaryDirectory() as tmp:
            db, _, day = _fresh(tmp)
            r = el.record_castration(db, "钱谦益", forced=False, day=day)
            self.assertEqual(r["bao_status"], el.BAO_KEPT)
            self.assertFalse(r["forced"])
            self.assertLess(el.get_lore(db, "钱谦益")["servility"], 70)

    def test_servility_brief_diverges_by_disposition(self):
        with TemporaryDirectory() as tmp:
            db, _, day = _fresh(tmp)
            el.record_castration(db, "甲", forced=True, day=day)
            el.record_castration(db, "乙", forced=False, day=day)
            forced_brief = el.servility_brief(db, "甲")
            volun_brief = el.servility_brief(db, "乙")
            self.assertIn("强旨", forced_brief)
            self.assertIn("入官库", forced_brief)        # 宝贝入官库之痛
            self.assertIn("宝贝存留", volun_brief)       # 自藏宝贝旧念
            self.assertNotEqual(forced_brief, volun_brief)

    def test_voice_profile_drives_public_payload_and_servility_brief(self):
        with TemporaryDirectory() as tmp:
            db, _, day = _fresh(tmp)
            name = "韩爌"
            el.record_castration(db, name, forced=True, day=day)
            db.conn.execute(
                """
                UPDATE characters
                SET ability=42, wisdom=38, courage=35,
                    style='识字不多的小火者，出身寒微，胆怯'
                WHERE name=?
                """,
                (name,),
            )
            db.conn.execute(
                """
                UPDATE eunuch_lore
                SET urinary_aftereffect='漏尿尿闭，冬夜尤难久站',
                    voice_body_change='嗓音尖薄，肩背微缩',
                    trauma_response='幻肢痛，听见净房旧话会失神',
                    bao_ritual='宝贝封签由官库收着，封签声最刺耳'
                WHERE name=?
                """,
                (name,),
            )
            db.conn.commit()

            profile = el.eunuch_voice_profile(db, name)
            self.assertIsNotNone(profile)
            self.assertIn("低文化内侍", str(profile["register"]))
            self.assertIn("胆怯", str(profile["register"]))
            self.assertIn("净身旧念", str(profile["register"]))
            self.assertIn("不要替内阁", str(profile["speech_rule"]))
            self.assertIn("奴婢晓得", profile["pet_phrases"])
            self.assertEqual(profile["self_address"], "奴婢")
            self.assertIn("短句", str(profile["sentence_shape"]))
            self.assertTrue(any("殿门" in item for item in profile["knowledge_scope"]))
            self.assertIn("自称奴婢", str(profile["style_contract"]))
            self.assertIn("大政只说", str(profile["style_contract"]))
            self.assertTrue(any("奴婢晓得" in item for item in profile["sample_openers"]))
            self.assertTrue(any("值房" in item for item in profile["allowed_moves"]))
            self.assertTrue(any("内阁大学士" in item or "财政" in item for item in profile["forbidden_moves"]))
            self.assertIn("封签没对上", profile["slang"])
            self.assertTrue(any("夹腰" in item for item in profile["stage_cues"]))
            self.assertTrue(any("嗓音" in item for item in profile["stage_cues"]))
            self.assertTrue(any("失神" in item for item in profile["stage_cues"]))
            self.assertTrue(any("封签" in item or "宝贝" in item for item in profile["stage_cues"]))
            self.assertIn("低文化口径", profile["dispatch_traits"])
            self.assertIn("胆怯", profile["dispatch_traits"])
            self.assertTrue(any("门房值房" in item for item in profile["fit_rules"]))

            public = el.public_lore_payload(db, name)
            self.assertEqual(public["voice_profile"]["register"], profile["register"])
            self.assertIn("封签没对上", public["voice_profile"]["slang"])
            self.assertIn("style_contract", public["voice_profile"])
            self.assertIn("knowledge_scope", public["voice_profile"])
            brief = el.servility_brief(db, name)
            self.assertIn("【口吻差异】", brief)
            self.assertIn("【口吻合约】", brief)
            self.assertIn("【说话边界】", brief)
            self.assertIn("低文化内侍", brief)
            self.assertIn("奴婢晓得", brief)
            self.assertIn("自称奴婢", brief)
            self.assertIn("大政只说", brief)
            self.assertIn("宫里切口", brief)
            self.assertIn("禁用话术", brief)
            self.assertIn("不要讲内阁大学士式长篇", brief)
            self.assertIn("【动作神态】", brief)
            self.assertIn("动作神态必须与对白分离", brief)
            self.assertIn("【动作】", brief)
            self.assertIn("【神态】", brief)
            self.assertIn("夹腰", brief)

    def test_voice_profile_changes_dispatch_fit(self):
        with TemporaryDirectory() as tmp:
            db, _, day = _fresh(tmp)
            name = "韩爌"
            el.record_castration(db, name, forced=True, day=day)
            db.conn.execute(
                """
                UPDATE characters
                SET ability=40, wisdom=36, courage=78,
                    style='识字不多的小火者，出身寒微，粗直急躁',
                    office='净身房候验小火者'
                WHERE name=?
                """,
                (name,),
            )
            db.conn.commit()

            public_task = el.assignment_risk_profile(
                db,
                name,
                "公开传旨并与外朝官员密谈账册，劝他们交出口供。",
                domains=["public", "bureaucracy"],
            )
            inner_task = el.assignment_risk_profile(
                db,
                name,
                "去门上值房打听谁递话、谁吩咐，跑腿传个近身风声。",
                domains=["inner"],
            )

            self.assertIn("低文化口径", public_task["voice_fit"]["traits"])
            self.assertIn("急性子", public_task["voice_fit"]["traits"])
            self.assertTrue(any("口吻错配" in item for item in public_task["risks"]))
            self.assertTrue(any("低文化口径不合" in item for item in public_task["voice_fit"]["notes"]))
            self.assertTrue(any("急性子不合" in item for item in public_task["voice_fit"]["notes"]))
            self.assertTrue(any("粗直口径贴近值房门上" in item for item in inner_task["voice_fit"]["notes"]))

    def test_underage_lore_suppresses_psychosexual_fixations_in_dialogue_brief(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            name = "小禄子"
            db.conn.execute(
                """
                INSERT INTO characters (
                    name, office, office_type, faction, aliases, personal_skills,
                    loyalty, ability, integrity, courage, style, power_id, status, birth_year
                ) VALUES (?, ?, ?, ?, '[]', '[]', 70, 38, 55, 30, ?, 'ming', 'active', ?)
                """,
                (
                    name,
                    "内书堂识字小火者",
                    "司礼监",
                    "内廷",
                    "保定逃荒入京，识字不多，胆怯",
                    int(state.year) - 11,
                ),
            )
            db.conn.execute("DELETE FROM eunuch_lore WHERE name!=?", (name,))
            db.conn.commit()

            el.record_castration(
                db,
                name,
                forced=False,
                day=day,
                detail_text="他说自己有贤者模式、性无能、受罚束缚依恋，但近来漏尿尿闭、嗓音尖薄。",
            )
            el.update_lore_from_text(
                db,
                name,
                "他说自己有贤者模式、性无能、受罚束缚依恋，但近来漏尿尿闭、嗓音尖薄。",
                day=day,
            )
            lore = el.get_lore(db, name)
            public = el.public_lore_payload(db, name)
            brief = el.servility_brief(db, name)

            self.assertEqual(lore["psychosexual_state"], "")
            self.assertEqual(public["psychosexual_label"], "")
            self.assertNotRegex(public["condition_line"], r"贤者|性无能|受罚|束缚|调教|畸恋")
            self.assertNotRegex(brief, r"贤者|性无能|受罚|束缚|调教|畸恋")
            self.assertIn("漏尿", brief)
            self.assertIn("嗓音尖薄", brief)
            self.assertIn("低文化内侍", brief)

    def test_no_lore_brief_is_empty(self):
        with TemporaryDirectory() as tmp:
            db, _, _ = _fresh(tmp)
            self.assertEqual(el.servility_brief(db, "韩爌"), "")

    def test_castration_complication_tick_changes_stats_and_is_idempotent(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            name = "韩爌"
            el.record_castration(db, name, forced=True, day=day)
            db.conn.execute("DELETE FROM eunuch_lore WHERE name!=?", (name,))
            db.conn.execute(
                """
                UPDATE eunuch_lore
                SET urinary_aftereffect='尿线细弱，冬日易尿闭',
                    trauma_response='',
                    voice_body_change='',
                    bao_ritual='',
                    private_fixation='',
                    psychosexual_state=''
                WHERE name=?
                """,
                (name,),
            )
            db.conn.execute(
                "UPDATE characters SET ability=60, charm=58, grievance=20 WHERE name=?",
                (name,),
            )
            db.conn.commit()

            evs = el.castration_complication_tick(db, state, 3)

            self.assertEqual(len(evs), 1)
            self.assertEqual(evs[0]["kind"], "eunuch_complication")
            self.assertEqual(evs[0]["complication"], "urinary")
            self.assertIn("stage_direction", evs[0])
            row = db.conn.execute(
                "SELECT ability, charm, grievance FROM characters WHERE name=?",
                (name,),
            ).fetchone()
            self.assertEqual(int(row["ability"]), 59)
            self.assertEqual(int(row["charm"]), 57)
            self.assertEqual(int(row["grievance"]), 22)
            memory = db.conn.execute(
                """
                SELECT id FROM event_memories
                WHERE subject_id=? AND event_type='eunuch_complication'
                """,
                (name,),
            ).fetchone()
            self.assertIsNotNone(memory)

            self.assertEqual(el.castration_complication_tick(db, state, 3), [])
            row2 = db.conn.execute(
                "SELECT ability, charm, grievance FROM characters WHERE name=?",
                (name,),
            ).fetchone()
            self.assertEqual(int(row2["ability"]), 59)
            self.assertEqual(int(row2["charm"]), 57)
            self.assertEqual(int(row2["grievance"]), 22)

    def test_apply_eunuch_care_costs_inner_treasury_and_softens_old_wound(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            name = "韩爌"
            el.record_castration(db, name, forced=True, day=day)
            state.metrics["内库"] = 50
            db.save_state(state)
            db.conn.execute(
                "UPDATE characters SET emp_trust=50, grievance=40, ability=55, charm=54 WHERE name=?",
                (name,),
            )
            db.conn.commit()

            result = el.apply_eunuch_care(db, state, name, mode="urinary", note="请太医治尿闭旧患。")

            self.assertTrue(result["ok"])
            self.assertEqual(result["mode"], "urinary")
            self.assertEqual(result["cost"], 3)
            self.assertEqual(state.metrics["内库"], 47)
            row = db.conn.execute(
                "SELECT emp_trust, grievance, ability, charm FROM characters WHERE name=?",
                (name,),
            ).fetchone()
            self.assertEqual(int(row["emp_trust"]), 52)
            self.assertEqual(int(row["grievance"]), 34)
            self.assertEqual(int(row["ability"]), 56)
            self.assertEqual(int(row["charm"]), 55)
            trait = db.conn.execute(
                "SELECT 1 FROM character_traits WHERE name=? AND trait='旧患调养'",
                (name,),
            ).fetchone()
            self.assertIsNotNone(trait)
            memory = db.conn.execute(
                "SELECT 1 FROM event_memories WHERE subject_id=? AND event_type='eunuch_care'",
                (name,),
            ).fetchone()
            self.assertIsNotNone(memory)

    def test_psychosexual_care_is_playable_and_mitigates_assignment_risk(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            name = "韩爌"
            el.record_castration(db, name, forced=True, day=day)
            db.conn.execute(
                """
                UPDATE characters
                SET emp_trust=50, grievance=40, charm=54, luck=49, birth_year=?
                WHERE name=?
                """,
                (int(state.year) - 30, name),
            )
            db.conn.execute(
                """
                UPDATE eunuch_lore
                SET psychosexual_state='贤者模式式空心麻木，欲念退潮后只剩畏冷与厌烦'
                WHERE name=?
                """,
                (name,),
            )
            db.conn.commit()

            task = "命他近身掌钥匙封匣，规训小内侍，暗查宝匣把柄。"
            before = el.assignment_risk_profile(db, name, task, domains=["inner"])
            self.assertTrue(any("心相旧结" in item for item in before["risks"]))

            state.metrics["内库"] = 50
            db.save_state(state)
            care = el.apply_eunuch_care(db, state, name, mode="贤者模式", note="准心相安顿，别让他近身差事走样。")

            self.assertTrue(care["ok"])
            self.assertEqual(care["mode"], "psychosexual")
            self.assertEqual(care["label"], "心相安顿")
            self.assertEqual(care["trait"], "心相安顿")
            self.assertEqual(care["cost"], 2)
            self.assertEqual(state.metrics["内库"], 48)
            self.assertIn("贤者模式", care["process"])
            row = db.conn.execute(
                "SELECT emp_trust, grievance, charm, luck FROM characters WHERE name=?",
                (name,),
            ).fetchone()
            self.assertEqual(int(row["emp_trust"]), 52)
            self.assertEqual(int(row["grievance"]), 36)
            self.assertEqual(int(row["charm"]), 55)
            self.assertEqual(int(row["luck"]), 50)
            self.assertIsNotNone(db.conn.execute(
                "SELECT 1 FROM character_traits WHERE name=? AND trait='心相安顿'",
                (name,),
            ).fetchone())

            after = el.assignment_risk_profile(db, name, task, domains=["inner"])
            self.assertGreater(int(after["score_delta"]), int(before["score_delta"]))
            self.assertTrue(any("心相已有安顿" in item for item in after["mitigations"]))

    def test_psychosexual_hard_service_alias_raises_grievance(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            name = "韩爌"
            el.record_castration(db, name, forced=True, day=day)
            db.conn.execute(
                """
                UPDATE characters
                SET emp_trust=50, grievance=30, charm=54, luck=50, birth_year=?
                WHERE name=?
                """,
                (int(state.year) - 30, name),
            )
            db.conn.execute(
                """
                UPDATE eunuch_lore
                SET psychosexual_state='性无能自知，转以权柄、服从与封匣仪式代偿'
                WHERE name=?
                """,
                (name,),
            )
            db.conn.commit()

            result = el.apply_eunuch_hard_service(db, state, name, mode="性无能", note="不许调养，照旧近身办差。")

            self.assertTrue(result["ok"])
            self.assertEqual(result["mode"], "psychosexual")
            self.assertEqual(result["label"], "心相硬压")
            self.assertEqual(result["trait"], "旧患硬派")
            row = db.conn.execute(
                "SELECT emp_trust, grievance, charm FROM characters WHERE name=?",
                (name,),
            ).fetchone()
            self.assertEqual(int(row["emp_trust"]), 49)
            self.assertEqual(int(row["grievance"]), 36)
            self.assertEqual(int(row["charm"]), 53)
            self.assertIsNotNone(db.conn.execute(
                "SELECT 1 FROM event_memories WHERE subject_id=? AND event_type='eunuch_hard_service'",
                (name,),
            ).fetchone())

    def test_complication_creates_help_goal_and_care_fulfills_it(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            name = "韩爌"
            el.record_castration(db, name, forced=True, day=day)
            db.conn.execute("DELETE FROM eunuch_lore WHERE name!=?", (name,))
            db.conn.execute(
                """
                UPDATE eunuch_lore
                SET urinary_aftereffect='漏尿，夜间须垫旧布',
                    trauma_response='',
                    voice_body_change='',
                    bao_ritual='',
                    private_fixation='',
                    psychosexual_state=''
                WHERE name=?
                """,
                (name,),
            )
            db.conn.commit()

            evs = el.castration_complication_tick(db, state, 3)

            self.assertEqual(len(evs), 1)
            goal_id = int(evs[0]["goal_id"])
            self.assertGreater(goal_id, 0)
            goal = db.get_conversation_goal(goal_id)
            self.assertEqual(goal["minister_name"], name)
            self.assertEqual(goal["action_kind"], "eunuch_care")
            self.assertEqual(goal["status"], "waiting_conditions")
            self.assertIn("尿路调养", goal["title"])

            result = el.apply_eunuch_care(db, state, name, mode="urinary", note="准动内库调养漏尿旧患。")

            self.assertTrue(result["ok"])
            self.assertEqual(int(result["goal_id"]), goal_id)
            fulfilled = db.get_conversation_goal(goal_id)
            self.assertEqual(fulfilled["status"], "fulfilled")
            self.assertEqual(fulfilled["condition_status"], "satisfied")

    def test_hard_service_fulfills_old_wound_goal_and_raises_future_risk(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            name = "韩爌"
            el.record_castration(
                db,
                name,
                forced=True,
                day=day,
                detail_text="净身房无麻；近来漏尿尿闭，夜里小解不畅。",
            )
            db.conn.execute(
                "UPDATE characters SET emp_trust=50, grievance=30, ability=55, charm=54, luck=50 WHERE name=?",
                (name,),
            )
            db.conn.commit()
            goal_id = db.create_conversation_goal(
                state,
                minister_name=name,
                action_kind="eunuch_care",
                title=f"尿路调养求助：{name}",
                target_text=f"{name}因内廷旧疾主动候见。",
                threshold=70,
                score=35,
                status="waiting_conditions",
                condition_status="pending",
                conditions=[{"description": "裁断调养或照常派差。", "status": "pending"}],
                expires_turn=int(state.turn) + 2,
                last_delta={"source": "eunuch_complication", "complication": "urinary"},
            )

            result = el.apply_eunuch_hard_service(db, state, name, mode="urinary", note="不许调养，照常派差。")

            self.assertTrue(result["ok"])
            self.assertEqual(result["mode"], "urinary")
            self.assertEqual(result["label"], "带患当差")
            self.assertEqual(int(result["goal_id"]), goal_id)
            row = db.conn.execute(
                "SELECT emp_trust, grievance, ability, charm, luck FROM characters WHERE name=?",
                (name,),
            ).fetchone()
            self.assertEqual(int(row["emp_trust"]), 49)
            self.assertGreaterEqual(int(row["grievance"]), 37)
            self.assertLess(int(row["ability"]), 55)
            self.assertLess(int(row["charm"]), 54)
            self.assertIsNotNone(db.conn.execute(
                "SELECT 1 FROM character_traits WHERE name=? AND trait='旧患硬派'",
                (name,),
            ).fetchone())
            self.assertIsNotNone(db.conn.execute(
                "SELECT 1 FROM event_memories WHERE subject_id=? AND event_type='eunuch_hard_service'",
                (name,),
            ).fetchone())
            fulfilled = db.get_conversation_goal(goal_id)
            self.assertEqual(fulfilled["status"], "fulfilled")
            self.assertEqual(fulfilled["last_delta"]["source"], "eunuch_hard_service")

            risk = el.assignment_risk_profile(db, name, "夜守久候，限期查封签。", domains=["investigation"])
            self.assertTrue(any("旧患硬派在案" in item for item in risk["risks"]))

    def test_assignment_risk_profile_turns_old_wounds_into_dispatch_risk_and_care_mitigates(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            name = "韩爌"
            el.record_castration(
                db,
                name,
                forced=True,
                day=day,
                detail_text="净身房无麻，宝官库石灰封存；近来漏尿尿闭，幻肢痛，按肩会僵住。",
            )
            el.update_lore_from_text(
                db,
                name,
                "净身房无麻，宝官库石灰封存；近来漏尿尿闭，幻肢痛，按肩会僵住。",
                day=day,
            )
            task = "密查刑房封签，夜间久候盯梢，拿问口供。"

            before = el.assignment_risk_profile(db, name, task, domains=["investigation", "inner"])

            self.assertLess(int(before["score_delta"]), 0)
            self.assertTrue(any("尿路旧患" in item for item in before["risks"]))
            self.assertTrue(any("惊创未平" in item for item in before["risks"]))
            self.assertTrue(before["stage_cues"])
            self.assertEqual(before["flare"]["mode"], "urinary")
            self.assertGreaterEqual(int(before["flare"]["severity"]), 70)
            self.assertIn("relay", before["flare"]["counterplay"])
            self.assertIn("漏尿尿闭", before["flare"]["likely_failure"])

            el.apply_eunuch_care(db, state, name, mode="urinary", note="先治尿闭漏尿，再派久候盯梢。")
            after = el.assignment_risk_profile(db, name, task, domains=["investigation", "inner"])

            self.assertGreater(int(after["score_delta"]), int(before["score_delta"]))
            self.assertTrue(any("尿路旧患已有御前调养" in item for item in after["mitigations"]))
            self.assertTrue(after["flare"])

    def test_dispatch_strategy_relays_old_wound_secret_order_risk(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            name = "韩爌"
            el.record_castration(
                db,
                name,
                forced=True,
                day=day,
                detail_text="净身房无麻，宝官库石灰封存；近来漏尿尿闭，幻肢痛，按肩会僵住。",
            )
            el.update_lore_from_text(
                db,
                name,
                "净身房无麻，宝官库石灰封存；近来漏尿尿闭，幻肢痛，按肩会僵住。",
                day=day,
            )
            task = "夜间久候盯梢刑房封签，拿问口供，查清官库旧案。"
            state.metrics["内库"] = 20
            db.save_state(state)
            order_id = db.create_secret_order(
                state,
                name,
                "密查净身房封签",
                task,
                ["刑房", "封签", "净身房"],
                deadline_months=1,
            )

            before = el.assignment_risk_profile(db, name, task, domains=["investigation", "inner"])
            self.assertTrue(any(item["key"] == "relay" for item in before["dispatch_strategies"]))

            result = el.apply_eunuch_dispatch_strategy(
                db,
                state,
                name,
                task,
                "relay",
                order_id=order_id,
                domains=["investigation", "inner"],
                note="准副手接力，别硬撑坏事。",
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["strategy"], "relay")
            self.assertEqual(result["cost"], 1)
            self.assertIn("旧患风险", result["outcome"])
            self.assertTrue(result["flare_before"])
            self.assertTrue(result["flare_after"])
            self.assertGreater(
                int(result["risk_after"]["score_delta"]),
                int(result["risk_before"]["score_delta"]),
            )
            order = db.get_secret_order(order_id)
            self.assertIn("分班轮值", order["sim_note"])
            memory = db.conn.execute(
                """
                SELECT 1 FROM event_memories
                WHERE subject_id=? AND event_type='eunuch_dispatch_strategy'
                """,
                (name,),
            ).fetchone()
            self.assertIsNotNone(memory)
            self.assertEqual(state.metrics["内库"], 19)

    def test_secret_order_old_wound_tick_delays_unprotected_eunuch_task(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            name = "韩爌"
            el.record_castration(
                db,
                name,
                forced=True,
                day=day,
                detail_text="净身房无麻，宝官库石灰封存；近来漏尿尿闭，幻肢痛，按肩会僵住。",
            )
            task = "夜间久候盯梢刑房封签，拿问口供，查清官库旧案。"
            order_id = db.create_secret_order(
                state,
                name,
                "密查净身房封签",
                task,
                ["刑房", "封签", "净身房"],
                deadline_months=1,
            )
            before = db.get_secret_order(order_id)
            self.assertEqual(int(before["due_turn"]), int(state.turn) + 1)

            evs = el.secret_order_old_wound_tick(db, state, 4)

            self.assertEqual(len(evs), 1)
            self.assertEqual(evs[0]["kind"], "eunuch_secret_order_old_wound")
            self.assertEqual(evs[0]["order_id"], order_id)
            self.assertGreater(int(evs[0]["goal_id"]), 0)
            updated = db.get_secret_order(order_id)
            self.assertEqual(int(updated["due_turn"]), int(before["due_turn"]) + 1)
            self.assertIn("[旧患拖累]", str(updated["sim_note"]))
            self.assertIn("旧患发作", str(updated["sim_note"]))
            memory = db.conn.execute(
                """
                SELECT 1 FROM event_memories
                WHERE subject_type='secret_order' AND subject_id=? AND event_type='eunuch_secret_order_old_wound'
                """,
                (str(order_id),),
            ).fetchone()
            self.assertIsNotNone(memory)
            goal = db.get_conversation_goal(int(evs[0]["goal_id"]))
            self.assertEqual(goal["minister_name"], name)
            self.assertEqual(goal["action_kind"], "eunuch_care")
            self.assertEqual(el.secret_order_old_wound_tick(db, state, 11), [])

    def test_secret_order_old_wound_tick_respects_dispatch_strategy(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            name = "韩爌"
            state.metrics["内库"] = 20
            db.save_state(state)
            el.record_castration(
                db,
                name,
                forced=True,
                day=day,
                detail_text="净身房无麻，宝官库石灰封存；近来漏尿尿闭，幻肢痛，按肩会僵住。",
            )
            task = "夜间久候盯梢刑房封签，拿问口供，查清官库旧案。"
            order_id = db.create_secret_order(
                state,
                name,
                "密查净身房封签",
                task,
                ["刑房", "封签", "净身房"],
                deadline_months=1,
            )
            before_due = int((db.get_secret_order(order_id) or {})["due_turn"])

            strategy = el.apply_eunuch_dispatch_strategy(
                db,
                state,
                name,
                task,
                "relay",
                order_id=order_id,
                domains=["investigation", "inner"],
                note="准副手分班轮值，别硬撑坏事。",
            )
            self.assertTrue(strategy["ok"])

            self.assertEqual(el.secret_order_old_wound_tick(db, state, 4), [])
            updated = db.get_secret_order(order_id)
            self.assertEqual(int(updated["due_turn"]), before_due)
            self.assertIn("分班轮值", str(updated["sim_note"]))
            self.assertNotIn("[旧患拖累]", str(updated["sim_note"]))

    def test_bao_instability_tick_creates_goal_and_is_mitigated_by_bao_care(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            name = "韩爌"
            el.record_castration(
                db,
                name,
                forced=True,
                day=day,
                detail_text="奉旨宫刑，宝官库石灰封存，收白签灰瓮；暗记官库封签，终身惦念。",
            )
            db.conn.execute("DELETE FROM eunuch_lore WHERE name!=?", (name,))
            db.conn.execute(
                "UPDATE characters SET emp_trust=50, grievance=20, wisdom=55, luck=52 WHERE name=?",
                (name,),
            )
            db.conn.commit()

            evs = el.bao_instability_tick(db, state, 6)

            self.assertEqual(len(evs), 1)
            self.assertEqual(evs[0]["kind"], "eunuch_bao_instability")
            self.assertIn("官库封签", evs[0]["title"])
            self.assertGreaterEqual(int(evs[0]["bao_risk"]), 50)
            self.assertGreater(int(evs[0]["goal_id"]), 0)
            row = db.conn.execute(
                "SELECT emp_trust, grievance, wisdom, luck FROM characters WHERE name=?",
                (name,),
            ).fetchone()
            self.assertEqual(int(row["emp_trust"]), 49)
            self.assertGreater(int(row["grievance"]), 20)
            self.assertLessEqual(int(row["wisdom"]), 55)

            self.assertEqual(el.bao_instability_tick(db, state, 6), [])
            state.metrics["内库"] = 50
            db.save_state(state)
            care = el.apply_eunuch_care(db, state, name, mode="bao", note="命官库查封签、补录宝案。")
            self.assertTrue(care["ok"])
            self.assertEqual(care["trait"], "宝贝安置")
            self.assertEqual(el.bao_instability_tick(db, state, 16), [])

    def test_bao_care_absorbs_storage_scheme_and_grants_settlement_item(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            name = "韩爌"
            el.record_castration(
                db,
                name,
                forced=True,
                day=day,
                detail_text="奉旨宫刑，宝官库石灰封存，收白签灰瓮；暗记官库封签，终身惦念。",
            )
            db.conn.execute("DELETE FROM eunuch_lore WHERE name!=?", (name,))
            db.conn.commit()
            before_risk = el._bao_instability_score(el.get_lore(db, name), set())

            care = el.apply_eunuch_care(
                db,
                state,
                name,
                mode="bao",
                note="准验宝，改用锡胆小木匣，香料腌藏，钥匙贴身，补录宝案。",
            )

            self.assertTrue(care["ok"])
            self.assertEqual(care["mode"], "bao")
            self.assertEqual(care["trait"], "宝贝安置")
            self.assertIn("宝档更新", care["outcome"])
            self.assertEqual(care["lore_update"]["bao_container"], "锡胆小木匣")
            self.assertEqual(care["lore_update"]["bao_preservation"], "香料腌藏")
            self.assertEqual(care["lore_update"]["bao_ritual"], "夜半验看，封签贴身")
            self.assertIn(f"宝案安置：{name}", care["items_added"])

            lore = el.get_lore(db, name)
            self.assertEqual(lore["bao_container"], "锡胆小木匣")
            self.assertEqual(lore["bao_preservation"], "香料腌藏")
            self.assertEqual(lore["bao_ritual"], "夜半验看，封签贴身")
            after_risk = el._bao_instability_score(lore, {"宝贝安置"})
            self.assertLess(after_risk, before_risk)
            inventory_ids = {str(item["id"]) for item in db.list_player_inventory()}
            self.assertIn(f"宝案安置：{name}", inventory_ids)

    def test_bao_leverage_return_reduces_grievance_and_future_instability(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            name = "韩爌"
            el.record_castration(
                db,
                name,
                forced=True,
                day=day,
                detail_text="奉旨宫刑，宝官库石灰封存，收白签灰瓮；暗记官库封签，终身惦念。",
            )
            db.conn.execute(
                "UPDATE characters SET emp_trust=40, grievance=50, wisdom=55, luck=50 WHERE name=?",
                (name,),
            )
            db.conn.commit()
            before_risk = el._bao_instability_score(el.get_lore(db, name), set())

            result = el.apply_bao_leverage(
                db,
                state,
                name,
                mode="return",
                note="赐还宝匣，改用锡胆小木匣，香料腌藏，钥匙贴身。",
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["mode"], "return")
            self.assertEqual(result["trait"], "御赐宝贝")
            self.assertGreater(int(result["delta"]["emp_trust"]), 0)
            self.assertLess(int(result["delta"]["grievance"]), 0)
            self.assertEqual(result["stake_profile"]["mode"], "return")
            self.assertGreaterEqual(int(result["stake_profile"]["score"]), 0)
            self.assertLessEqual(int(result["stake_profile"]["score"]), 100)
            self.assertIn("安置体面", result["stake_profile"]["summary"])
            self.assertIn("筹码值", result["outcome"])
            row = db.conn.execute("SELECT emp_trust, grievance FROM characters WHERE name=?", (name,)).fetchone()
            self.assertEqual(int(row["emp_trust"]), 40 + int(result["delta"]["emp_trust"]))
            self.assertEqual(int(row["grievance"]), 50 + int(result["delta"]["grievance"]))
            lore = el.get_lore(db, name)
            self.assertEqual(lore["bao_status"], el.BAO_KEPT)
            self.assertEqual(lore["bao_container"], "锡胆小木匣")
            self.assertEqual(lore["bao_preservation"], "香料腌藏")
            self.assertIn("赐还", lore["bao_ritual"])
            self.assertNotIn("钥匙", lore["bao_ritual"])
            after_risk = el._bao_instability_score(lore, {"御赐宝贝"})
            self.assertLess(after_risk, before_risk)
            self.assertIn(f"御赐宝贝：{name}", {str(item["id"]) for item in db.list_player_inventory()})
            self.assertIsNotNone(db.conn.execute(
                "SELECT 1 FROM event_memories WHERE subject_id=? AND event_type='eunuch_bao_leverage'",
                (name,),
            ).fetchone())

    def test_bao_leverage_control_raises_grievance_and_assignment_risk(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            name = "韩爌"
            el.record_castration(
                db,
                name,
                forced=True,
                day=day,
                detail_text="奉旨宫刑，宝官库石灰封存，收白签灰瓮；暗记官库封签，终身惦念。",
            )
            db.conn.execute(
                "UPDATE characters SET emp_trust=60, grievance=20, wisdom=55, luck=55 WHERE name=?",
                (name,),
            )
            db.conn.commit()
            before_risk = el._bao_instability_score(el.get_lore(db, name), set())

            result = el.apply_bao_leverage(
                db,
                state,
                name,
                mode="control",
                note="将他的宝押在官库封存，铁皮锁匣，封签拿住作把柄。",
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["mode"], "control")
            self.assertEqual(result["trait"], "宝案钳制")
            self.assertLess(int(result["delta"]["emp_trust"]), 0)
            self.assertGreater(int(result["delta"]["grievance"]), 0)
            self.assertLessEqual(int(result["delta"]["luck"]), 0)
            self.assertEqual(result["stake_profile"]["mode"], "control")
            self.assertGreaterEqual(int(result["stake_profile"]["score"]), 0)
            self.assertLessEqual(int(result["stake_profile"]["score"]), 100)
            self.assertIn("官库粗封", result["stake_profile"]["summary"])
            self.assertIn("筹码值", result["outcome"])
            lore = el.get_lore(db, name)
            self.assertEqual(lore["bao_status"], el.BAO_FORFEIT)
            self.assertIn("官库封签", lore["bao_ritual"])
            after_risk = el._bao_instability_score(lore, {"宝案钳制"})
            self.assertGreater(after_risk, before_risk)
            profile = el.assignment_risk_profile(db, name, "命其查官库封签宝匣旧案", domains=["inner"])
            self.assertTrue(any("宝案钳制" in item for item in profile["risks"]))
            self.assertIn(f"官库宝案把柄：{name}", {str(item["id"]) for item in db.list_player_inventory()})

    def test_timeflow_surfaces_castration_complication_events(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            name = "韩爌"
            el.record_castration(db, name, forced=True, day=day)
            db.conn.execute("DELETE FROM eunuch_lore WHERE name!=?", (name,))
            db.conn.execute(
                """
                UPDATE eunuch_lore
                SET urinary_aftereffect='漏尿，夜间须垫旧布',
                    trauma_response='',
                    voice_body_change='',
                    bao_ritual='',
                    private_fixation='',
                    psychosexual_state=''
                WHERE name=?
                """,
                (name,),
            )
            db.conn.commit()

            result = timeflow.advance_days(db, state, 2, stop_on_yellow=False)
            events = [event for report in result["reports"] for event in report["events"]]

            self.assertTrue(any(event["kind"] == "eunuch_complication" for event in events))

    def test_timeflow_surfaces_bao_instability_events(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            name = "韩爌"
            el.record_castration(
                db,
                name,
                forced=True,
                day=day,
                detail_text="奉旨宫刑，宝官库石灰封存，收白签灰瓮；暗记官库封签，终身惦念。",
            )
            db.conn.execute("DELETE FROM eunuch_lore WHERE name!=?", (name,))
            db.conn.commit()

            result = timeflow.advance_days(db, state, 5, stop_on_yellow=False)
            events = [event for report in result["reports"] for event in report["events"]]

            self.assertTrue(any(event["kind"] == "eunuch_bao_instability" for event in events))


class ReincarnationTests(unittest.TestCase):
    def test_aged_eunuch_gets_reincarnation_rumor_once(self):
        with TemporaryDirectory() as tmp:
            db, state, _ = _fresh(tmp)
            # 造一名高龄宦官（生年早 → 年高）
            db.conn.execute("UPDATE characters SET birth_year=1555 WHERE name='王体乾'")
            db.conn.commit()
            # day//30 %4==0 的朔日闸门：day=0 命中
            evs = el.reincarnation_tick(db, state, 0)
            self.assertTrue(any(e["kind"] == "eunuch_reincarnation" for e in evs))
            self.assertTrue(el.get_lore(db, "王体乾")["reincarnation"])
            # 已起过 → 不再重复
            evs2 = el.reincarnation_tick(db, state, 0)
            self.assertFalse(any(e.get("ref_id") == "王体乾" for e in evs2))


class BurialTests(unittest.TestCase):
    def test_kept_bao_burial_is_consoling(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            el.record_castration(db, "丙", forced=False, day=day)  # kept
            lament = el.burial_lament_on_death(db, state, "丙", day)
            self.assertIsNotNone(lament)
            self.assertIn("全尸", lament)

    def test_forfeit_bao_burial_is_a_regret(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            el.record_castration(db, "丁", forced=True, day=day)  # forfeit
            lament = el.burial_lament_on_death(db, state, "丁", day)
            self.assertIsNotNone(lament)
            self.assertIn("不得全尸", lament)


if __name__ == "__main__":
    unittest.main()
