"""净身恶趣味 E2a 测试：宝处置、奴性分野、还阳传言、全尸执念。零 LLM。"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim import eunuch_lore as el, timeflow
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
            self.assertGreaterEqual(lore["servility"], 70)  # 强阉奴性扭曲偏高
            self.assertTrue(lore["knife_tool"])
            self.assertTrue(lore["anesthesia"])
            self.assertTrue(lore["urinary_aftereffect"])
            self.assertTrue(lore["voice_body_change"])
            self.assertTrue(lore["trauma_response"])
            self.assertTrue(lore["private_fixation"])
            self.assertTrue(lore["psychosexual_state"])
            public = el.public_lore_payload(db, "韩爌")
            self.assertIn("强阉", public["bao_label"])
            self.assertIn("尿路", public["condition_line"])
            self.assertIn("惊创", public["condition_line"])
            self.assertIn("癖性", public["condition_line"])

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

    def test_record_castration_applies_chosen_scheme_text_immediately(self):
        with TemporaryDirectory() as tmp:
            db, _, day = _fresh(tmp)
            r = el.record_castration(
                db,
                "韩爌",
                forced=True,
                day=day,
                detail_text=(
                    "净军房行事，铜柄宫刀，无麻；宝约二两八钱，一大一小，"
                    "油封后发硬，油炸封蜡，收黄杨木描金匣。"
                ),
            )
            self.assertIn("scheme_applied", r)
            lore = el.get_lore(db, "韩爌")
            self.assertEqual(lore["castration_method"], "净军房夜割")
            self.assertEqual(lore["knife_tool"], "铜柄宫刀")
            self.assertEqual(lore["anesthesia"], "无麻，冷汗硬熬")
            self.assertEqual(lore["bao_weight"], "约二两八钱")
            self.assertEqual(lore["bao_shape"], "一大一小")
            self.assertEqual(lore["bao_texture"], "油封后发硬")
            self.assertEqual(lore["bao_preservation"], "油炸封蜡")
            self.assertEqual(lore["bao_container"], "黄杨木描金匣")

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
            self.assertIn("收没", forced_brief)        # 宝被官府收没之痛
            self.assertIn("匣中供奉", volun_brief)       # 自藏宝匣望来世
            self.assertNotEqual(forced_brief, volun_brief)

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

    def test_assignment_risk_profile_turns_old_wounds_into_dispatch_risk_and_care_mitigates(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            name = "韩爌"
            el.record_castration(
                db,
                name,
                forced=True,
                day=day,
                detail_text="净军房无麻，宝官库石灰封存；近来漏尿尿闭，幻肢痛，按肩会僵住。",
            )
            task = "密查刑房封签，夜间久候盯梢，拿问口供。"

            before = el.assignment_risk_profile(db, name, task, domains=["investigation", "inner"])

            self.assertLess(int(before["score_delta"]), 0)
            self.assertTrue(any("尿路旧患" in item for item in before["risks"]))
            self.assertTrue(any("惊创未平" in item for item in before["risks"]))
            self.assertTrue(before["stage_cues"])

            el.apply_eunuch_care(db, state, name, mode="urinary", note="先治尿闭漏尿，再派久候盯梢。")
            after = el.assignment_risk_profile(db, name, task, domains=["investigation", "inner"])

            self.assertGreater(int(after["score_delta"]), int(before["score_delta"]))
            self.assertTrue(any("尿路旧患已有御前调养" in item for item in after["mitigations"]))

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
