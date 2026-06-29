"""NPC 数据基座接入测试：适配层、检定修正、风闻、人才池、起复闭环。

基座文件不存在时整组跳过（降级路径由 test_degraded 验证）。零 LLM。
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim import foundation, lifecycle, memorials, timeflow
from ming_sim.db import GameDB

HAS_FOUNDATION = foundation.available()
needs_foundation = unittest.skipUnless(HAS_FOUNDATION, "NPC 数据基座未挂载")


def _fresh(tmp: str):
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    state = db.load_state()
    day = timeflow.ensure_active(db, state)
    return db, state, day


@needs_foundation
class AdapterTests(unittest.TestCase):
    def test_profile_and_ability(self):
        p = foundation.profile("袁崇焕")
        self.assertIsNotNone(p)
        self.assertTrue(p["full_prompt"])
        self.assertTrue(p["biography"])
        self.assertTrue(any(t["trait_name"] == "知兵" for t in p["traits"]))
        self.assertEqual(foundation.ability100("袁崇焕"), 80)
        self.assertIsNone(foundation.profile("不存在的人"))
        self.assertIsNone(foundation.ability100("不存在的人"))

    def test_agent_block_contains_persona(self):
        block = foundation.agent_block("袁崇焕")
        self.assertIn("国朝人事档案", block)
        self.assertIn("人格", block)
        self.assertIn("生平", block)

    def test_directive_modifiers_skill_and_flaw(self):
        mil = foundation.directive_modifiers("袁崇焕", "military_ops")
        self.assertGreater(mil["score"], 10)          # 知兵+久历边事
        self.assertLess(mil["exec_factor"], 0.9)
        self.assertGreater(mil["anomaly_bias"].get("block", 0), 0)  # 刚愎自用
        relief = foundation.directive_modifiers("袁崇焕", "relief")
        self.assertEqual(relief["score"], 0)          # 不对口无加成
        self.assertGreater(relief["anomaly_bias"].get("block", 0), 0)  # 痼疾跟人走

    def test_full_game_roster_covered(self):
        import json
        with open(Path(__file__).resolve().parent.parent / "content" / "characters.json", encoding="utf-8") as fh:
            game = json.load(fh)
        chars = game if isinstance(game, list) else game.get("characters", game)
        names = [c["name"] for c in chars] if isinstance(chars, list) else list(chars)
        missing = [n for n in names if foundation.profile(n) is None]
        self.assertEqual(missing, [], f"游戏名册应 100% 命中基座：{missing[:10]}")


@needs_foundation
class RumorTests(unittest.TestCase):
    def test_flaws_hidden_until_familiar(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            fresh_rumors = foundation.evaluation_rumors("袁崇焕", familiarity=0, intel_boost=0)
            kinds = [r["kind"] for r in fresh_rumors]
            self.assertIn("朝野共知", kinds)            # 擅可见
            self.assertNotIn("深知其病", kinds)         # 痼隐藏
            self.assertIn("讳莫如深", kinds)            # 但提示有隐藏面
            deep = foundation.evaluation_rumors("袁崇焕", familiarity=30, intel_boost=0)
            self.assertTrue(any(r["kind"] == "深知其病" and "刚愎" in r["text"] for r in deep))


@needs_foundation
class LifecycleIntegrationTests(unittest.TestCase):
    def test_chain_uses_foundation_ability_and_traits(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            plan = lifecycle.build_chain(db, state, "命袁崇焕整军备战，进剿东虏，驰援辽东", "袁崇焕")
            self.assertEqual(plan["assignee"], "袁崇焕")
            self.assertEqual(plan["category"], "military_ops")
            self.assertGreater(int(plan["trait_score"]), 10)
            self.assertTrue(any("知兵" in n for n in plan["trait_notes"]))
            self.assertGreater(int(plan["check_risk"].get("block", 0)), 10)  # 基座偏置已并入


@needs_foundation
class TalentPoolTests(unittest.TestCase):
    def test_pool_excludes_court_and_emperor(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            in_court = {str(r["name"]) for r in db.conn.execute("SELECT name FROM characters")}
            pool = foundation.candidates(in_court, limit=50)
            names = [c["name"] for c in pool]
            self.assertTrue(names)
            self.assertNotIn("朱由检", names)
            self.assertFalse(set(names) & in_court)
            # 按才总降序，名将名臣应在前列
            self.assertGreaterEqual(pool[0]["ability100"], pool[-1]["ability100"])

    def test_build_game_character_mapping(self):
        ch = foundation.build_game_character("卢象升", 1628)
        self.assertIsNotNone(ch)
        self.assertGreaterEqual(ch.ability, 80)       # 才总 92 折百
        self.assertGreaterEqual(ch.loyalty, 60)       # 以身许国侧
        self.assertEqual(ch.status, "active")
        self.assertTrue(ch.summary)

    def test_recruit_memorial_followup(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            memorials.reset_attention_for_day(db, day)
            mid = memorials.create_memorial(
                db, state, day=day, author_name="韩爌", org="内阁",
                kind="荐人", urgency=2, summary="保举起复卢象升",
                ref_kind="foundation_npc", ref_id="卢象升")
            result = memorials.decide_memorial(db, state, mid, "approve", day=day)
            self.assertTrue(result["ok"])
            self.assertEqual(result.get("followup", {}).get("kind"), "recruit_foundation")
            self.assertEqual(result["followup"]["name"], "卢象升")


class DegradedTests(unittest.TestCase):
    def test_everything_degrades_without_foundation(self):
        import os
        old_env = os.environ.get("MING_FOUNDATION_DB")
        try:
            os.environ["MING_FOUNDATION_DB"] = "/nonexistent/no.sqlite"
            foundation.reset_for_tests()
            # 路径解析仍可能落到 Documents 默认位置——用猴补丁彻底切断
            orig = foundation._db_path
            foundation._db_path = lambda: None
            try:
                self.assertFalse(foundation.available())
                self.assertIsNone(foundation.profile("袁崇焕"))
                self.assertEqual(foundation.agent_block("袁崇焕"), "")
                mods = foundation.directive_modifiers("袁崇焕", "military_ops")
                self.assertEqual(mods["score"], 0)
                self.assertEqual(foundation.candidates(set(), limit=5), [])
                self.assertIsNone(foundation.build_game_character("卢象升", 1628))
            finally:
                foundation._db_path = orig
        finally:
            if old_env is None:
                os.environ.pop("MING_FOUNDATION_DB", None)
            else:
                os.environ["MING_FOUNDATION_DB"] = old_env
            foundation.reset_for_tests()


if __name__ == "__main__":
    unittest.main()
