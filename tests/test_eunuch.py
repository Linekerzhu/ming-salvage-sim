"""随侍太监枢纽回归（零 LLM）：默认挑选/换人/候选/降级/角色简报。"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim import eunuch
from ming_sim.content import GameContent
from ming_sim.db import GameDB
from ming_sim.models import Character
from ming_sim.registry import persona_self_address_rule
from ming_sim.session import _sync_offices_from_db_impl
from ming_sim.veil import build_info_scope_brief


def _fresh(tmp: str):
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    return db


class EunuchHubTests(unittest.TestCase):
    def test_default_pick_is_wangchengen(self):
        with TemporaryDirectory() as tmp:
            db = _fresh(tmp)
            self.assertEqual(eunuch.get_attending_eunuch(db), "王承恩")
            # 落库幂等
            self.assertEqual(db.kv_get(eunuch.KV_ATTENDING_EUNUCH), "王承恩")

    def test_candidates_eunuchs_first(self):
        with TemporaryDirectory() as tmp:
            db = _fresh(tmp)
            cands = eunuch.list_candidates(db)
            self.assertTrue(cands)
            names = [c["name"] for c in cands]
            self.assertIn("王承恩", names)
            # 宦官置顶：首个候选应为宦官
            self.assertTrue(cands[0]["is_eunuch"])
            wce = next(c for c in cands if c["name"] == "王承恩")
            self.assertTrue(wce["is_eunuch"])

    def test_replace_to_another_eunuch(self):
        with TemporaryDirectory() as tmp:
            db = _fresh(tmp)
            r = eunuch.set_attending_eunuch(db, "曹化淳")
            self.assertTrue(r["ok"], r)
            self.assertEqual(eunuch.get_attending_eunuch(db), "曹化淳")

    def test_replace_rejects_non_eunuch_identity(self):
        with TemporaryDirectory() as tmp:
            db = _fresh(tmp)
            r = eunuch.set_attending_eunuch(db, "韩爌")
            self.assertFalse(r["ok"])
            self.assertIn("阉人", r["message"])
            self.assertEqual(eunuch.get_attending_eunuch(db), "王承恩")

    def test_stale_kv_non_eunuch_is_ignored(self):
        with TemporaryDirectory() as tmp:
            db = _fresh(tmp)
            db.kv_set(eunuch.KV_ATTENDING_EUNUCH, "韩爌")
            self.assertEqual(eunuch.get_attending_eunuch(db), "王承恩")
            self.assertEqual(db.kv_get(eunuch.KV_ATTENDING_EUNUCH), "王承恩")

    def test_replace_rejects_unknown(self):
        with TemporaryDirectory() as tmp:
            db = _fresh(tmp)
            r = eunuch.set_attending_eunuch(db, "查无此人")
            self.assertFalse(r["ok"])
            # 在任者不变（仍为默认）
            self.assertEqual(eunuch.get_attending_eunuch(db), "王承恩")

    def test_fallback_when_preferred_inactive(self):
        with TemporaryDirectory() as tmp:
            db = _fresh(tmp)
            db.conn.execute("UPDATE characters SET status='dead' WHERE name='王承恩'")
            db.conn.commit()
            pick = eunuch.get_attending_eunuch(db)
            self.assertIsNotNone(pick)
            self.assertNotEqual(pick, "王承恩")
            row = db.conn.execute(
                "SELECT office, office_type FROM characters WHERE name=?", (pick,)).fetchone()
            self.assertTrue(eunuch.is_eunuch_like(str(row["office"]), str(row["office_type"])))

    def test_role_brief_mentions_name(self):
        brief = eunuch.eunuch_role_brief("王承恩", "内官监御前")
        self.assertIn("王承恩", brief)
        self.assertIn("随侍", brief)
        self.assertIn("不是内阁大学士", brief)
        self.assertIn("奴婢听闻", brief)
        self.assertIn("不得自称「臣」", brief)
        self.assertIn("贴身内侍", brief)

    def test_inner_court_self_address_overrides_generic_examples(self):
        character = Character(
            name="王承恩",
            office="内官监御前",
            office_type="内廷",
            faction="内廷",
            aliases=[],
            personal_skills=[],
            loyalty=92,
            ability=62,
            integrity=70,
            courage=66,
            style="谨慎近侍",
            power_id="ming",
        )
        rule = persona_self_address_rule(character)
        self.assertIn("奴婢", rule)
        self.assertIn("高于所有通用示例", rule)
        self.assertIn("臣领旨", rule)
        self.assertIn("奴婢领旨", rule)

    def test_info_scope_keeps_attendant_from_speaking_as_grand_secretary(self):
        with TemporaryDirectory() as tmp:
            db = _fresh(tmp)
            character = Character(
                name="王承恩",
                office="内官监御前",
                office_type="内廷",
                faction="内廷",
                aliases=[],
                personal_skills=[],
                loyalty=92,
                ability=62,
                integrity=70,
                courage=66,
                style="谨慎近侍",
                power_id="ming",
            )
            brief = build_info_scope_brief(db, character)
            self.assertIn("不是内阁大学士", brief)
            self.assertIn("传旨催办", brief)
            self.assertIn("须问内阁/该部", brief)
            self.assertIn("不要主动铺陈完整政策蓝图", brief)

    def test_explicit_male_is_not_treated_as_eunuch_by_inner_office_text(self):
        character = Character(
            name="错档男官",
            office="司礼监随堂太监",
            office_type="司礼监",
            faction="内廷",
            aliases=[],
            personal_skills=[],
            loyalty=60,
            ability=55,
            integrity=55,
            courage=55,
            style="错档测试",
            power_id="ming",
            sex="male",
        )

        rule = persona_self_address_rule(character)
        self.assertIn("外朝/军镇/地方官员身份", rule)
        self.assertIn("臣", rule)
        self.assertIn("不要自称「奴婢", rule)
        self.assertNotIn("在御前自称以「奴婢」为主", rule)

        with TemporaryDirectory() as tmp:
            db = _fresh(tmp)
            brief = build_info_scope_brief(db, character)
            self.assertNotIn("奴婢听闻", brief)
            self.assertNotIn("不是内阁大学士", brief)

    def test_unknown_sex_keeps_legacy_inner_office_fallback(self):
        character = Character(
            name="旧档内侍",
            office="司礼监随堂太监",
            office_type="司礼监",
            faction="内廷",
            aliases=[],
            personal_skills=[],
            loyalty=60,
            ability=55,
            integrity=55,
            courage=55,
            style="旧档测试",
            power_id="ming",
            sex="unknown",
        )

        rule = persona_self_address_rule(character)
        self.assertIn("奴婢", rule)

        with TemporaryDirectory() as tmp:
            db = _fresh(tmp)
            brief = build_info_scope_brief(db, character)
            self.assertIn("奴婢听闻", brief)
            self.assertIn("不是内阁大学士", brief)

    def test_db_sync_preserves_sex_identity(self):
        with TemporaryDirectory() as tmp:
            content = GameContent.load()
            db = GameDB(str(Path(tmp) / "sex-sync.db"), content=content)
            db.seed_static_data()
            db.conn.execute(
                """
                UPDATE characters
                SET office='司礼监随堂太监', office_type='司礼监', faction='内廷', sex='male'
                WHERE name='韩爌'
                """
            )
            db.conn.commit()

            _sync_offices_from_db_impl(content, db)
            character = content.characters["韩爌"]

            self.assertEqual(character.sex, "male")
            rule = persona_self_address_rule(character)
            self.assertIn("外朝/军镇/地方官员身份", rule)
            self.assertNotIn("在御前自称以「奴婢」为主", rule)


if __name__ == "__main__":
    unittest.main()
