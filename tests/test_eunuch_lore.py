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
