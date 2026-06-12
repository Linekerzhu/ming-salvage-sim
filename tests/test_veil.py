"""M4 文书化黑箱测试：印象评语、空饷账面、密查两线、信息集隔离。零 LLM。"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim import timeflow, veil
from ming_sim.db import GameDB


def _fresh(tmp: str):
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    state = db.load_state()
    day = timeflow.ensure_active(db, state)
    return db, state, day


class PerceptionTests(unittest.TestCase):
    def test_perceive_is_stable_and_noisy(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            p1, label1, conf1 = veil.perceive(db, "袁崇焕", "loyalty", 70)
            p2, label2, conf2 = veil.perceive(db, "袁崇焕", "loyalty", 70)
            self.assertEqual((p1, label1, conf1), (p2, label2, conf2))  # 同人稳定地被看错
            self.assertEqual(conf1, "仅凭风闻")  # 生人置信度最低

    def test_familiarity_shrinks_noise(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            for i in range(40):
                db.conn.execute(
                    "INSERT INTO chat_messages (minister_name, turn, role, content) "
                    "VALUES ('袁崇焕', 1, 'user', '召对')")
            db.conn.commit()
            _, _, conf = veil.perceive(db, "袁崇焕", "loyalty", 70)
            self.assertNotEqual(conf, "仅凭风闻")

    def test_character_evaluations_no_true_values(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            from ming_sim.content import GameContent
            content = db.content
            char = next(iter(content.characters.values()))
            evals = veil.character_evaluations(db, char)
            # 四条属性评语 + （若基座挂载）特质风闻
            attr_evals = [e for e in evals if e["attr"] != "trait"]
            self.assertEqual(len(attr_evals), 4)
            for e in evals:
                self.assertIn("label", e)
                self.assertIn("confidence", e)


class ArmyDivergenceTests(unittest.TestCase):
    def test_seed_and_overlay(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            # ensure_active 开月时已播种；幂等
            self.assertEqual(veil.seed_army_divergences(db, state, day), 0)
            actual = int(db.conn.execute(
                "SELECT manpower FROM armies WHERE id='jingying'").fetchone()["manpower"])
            armies = [{"id": "jingying", "owner_power": "ming", "manpower": actual}]
            veil.army_reported_overlay(db, armies)
            self.assertGreater(int(armies[0]["manpower"]), actual)  # 账面虚冒
            self.assertEqual(armies[0]["manpower_source"], "兵部造册")

    def test_contradictions_hide_actual(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            items = veil.ledger_contradictions(db)
            self.assertTrue(items)
            for item in items:
                self.assertNotIn("actual_value", item)


class InvestigationTests(unittest.TestCase):
    def test_changwei_investigation_exposes_or_misses(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            r = veil.start_investigation(db, state, line="changwei",
                                         target_kind="army", target_id="jingying", day=day)
            self.assertTrue(r["ok"])
            # 推进到回报日（中途可能被其它黄事件打断，循环推进）
            seen_intel = False
            for _ in range(30):
                result = timeflow.advance_days(db, state, 30, stop_on_yellow=True)
                events = [e for rep in result["reports"] for e in rep["events"]]
                if any(e["kind"] == "intel_arrival" for e in events):
                    seen_intel = True
                    break
                if result["advanced"] == 0:
                    break
            self.assertTrue(seen_intel)
            # 密揭已入御案
            row = db.conn.execute(
                "SELECT * FROM memorials WHERE kind='密揭' ORDER BY id DESC LIMIT 1").fetchone()
            self.assertIsNotNone(row)
            self.assertIn("盘验", str(row["summary"]))

    def test_changwei_heat_spawns_issue(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            for i in range(3):
                veil.start_investigation(db, state, line="changwei",
                                         target_kind="character", target_id=f"目标{i}", day=day + i)
            row = db.conn.execute(
                "SELECT * FROM issues WHERE title LIKE '%厂卫横行%'").fetchone()
            self.assertIsNotNone(row)

    def test_kedao_line_slower(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            r = veil.start_investigation(db, state, line="kedao",
                                         target_kind="character", target_id="韩爌", day=day)
            self.assertTrue(r["ok"])
            self.assertGreaterEqual(int(r["due_day"]) - day, 18)


class InfoScopeTests(unittest.TestCase):
    def test_scope_brief_mentions_entitlement_and_bounds(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            from ming_sim.models import Character
            char = Character(
                name="毕自严", office="户部尚书", office_type="户部", faction="无",
                aliases=[], personal_skills=[], loyalty=70, ability=80, integrity=75,
                courage=55, style="干练", power_id="ming")
            brief = veil.build_info_scope_brief(db, char)
            self.assertIn("信息边界", brief)
            self.assertIn("国库出入", brief)
            self.assertIn("不得开天眼", brief)


if __name__ == "__main__":
    unittest.main()
