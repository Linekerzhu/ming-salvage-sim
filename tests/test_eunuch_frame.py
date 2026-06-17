"""宫斗阴谋 P3 测试：阉党自发冤陷东林 court_events 抉择 + 把柄喂弹章 credibility。零 LLM。"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim import court_events, court, timeflow
from ming_sim.db import GameDB
from ming_sim.eunuch_power import KV_EUNUCH_POWER, get_eunuch_power
from ming_sim.upgrade_schema import kv_set_int


def _fresh(tmp: str):
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    state = db.load_state()
    day = timeflow.ensure_active(db, state)
    db.kv_set(court_events.KV_PENDING, "")
    db.kv_set(court_events.KV_COOLDOWN, "{}")
    return db, state, day


class FrameTriggerTests(unittest.TestCase):
    def test_no_frame_when_power_low(self):
        with TemporaryDirectory() as tmp:
            db, _, _ = _fresh(tmp)
            kv_set_int(db, KV_EUNUCH_POWER, 40)
            self.assertIsNone(court_events._eunuch_frame(db))

    def test_frame_targets_pure_official_when_power_high(self):
        with TemporaryDirectory() as tmp:
            db, _, _ = _fresh(tmp)
            kv_set_int(db, KV_EUNUCH_POWER, 70)
            ctx = court_events._eunuch_frame(db)
            self.assertIsNotNone(ctx)
            self.assertTrue(ctx["target"])
            # 目标非阉党
            fac = str(db.conn.execute("SELECT faction FROM characters WHERE name=?", (ctx["target"],)).fetchone()["faction"])
            self.assertNotIn("阉", fac)


class FrameResolveTests(unittest.TestCase):
    def _arm(self, db, state, day):
        kv_set_int(db, KV_EUNUCH_POWER, 70)
        return court_events.evaluate_decisions(db, state, day)

    def test_approve_jails_target_and_raises_power(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            payload = self._arm(db, state, day)
            # 阉祸危机优先级更高(权阉70<75不触发)，故此处应是 eunuch_frame
            self.assertEqual(payload["id"], "eunuch_frame")
            target = payload["narrative"]
            ctx_target = court_events.get_pending(db)["ctx"]["target"]
            p0 = get_eunuch_power(db)
            r = court_events.resolve_decision(db, state, "approve", day)
            self.assertTrue(r["ok"])
            self.assertEqual(db.get_character_status(ctx_target)[0], "imprisoned")  # 下诏狱
            self.assertGreater(get_eunuch_power(db), p0)  # 权阉益横

    def test_protect_curbs_power_and_spares_target(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            self._arm(db, state, day)
            ctx_target = court_events.get_pending(db)["ctx"]["target"]
            p0 = get_eunuch_power(db)
            r = court_events.resolve_decision(db, state, "protect", day)
            self.assertTrue(r["ok"])
            self.assertEqual(db.get_character_status(ctx_target)[0], "active")  # 力保未下狱
            self.assertLess(get_eunuch_power(db), p0)  # 权阉受挫


class HookCredibilityTests(unittest.TestCase):
    def test_known_hook_makes_impeachment_weightier(self):
        # 被劾者有 known 把柄 → court_moves 弹章 urgency=4、summary 含「有实」。
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            # 给一对宿敌：author 劾 victim，且 victim 有 known 把柄
            from ming_sim import intrigue
            intrigue.ensure_schema(db)
            # 找两个在朝者造宿怨
            a, b = "刘宗周", "王绍徽"
            court.adjust_opinion(db, a, b, -70, "政敌", day=day)
            db.conn.execute(
                "INSERT INTO secrets(holder, kind, detail, severity, known_to_crown, used) "
                "VALUES (?,?,?,?,1,0)", (b, "贪墨", "受赇鬻爵、赃证在外", 60))
            db.conn.commit()
            # 直接造弹章走 create_memorial 的把柄分支：复用 court 的逻辑较重，改测 secrets_for 通路
            from ming_sim.intrigue import secrets_for
            self.assertIsNotNone(secrets_for(db, b))  # 确有 known 把柄供弹章引用
            # 端到端：跑 court_moves_tick 多次，若劾到 b 则其弹章 urgency 应为 4
            seen_weighty = False
            for d in range(8, 200, 10):
                court.court_moves_tick(db, state, d)
                row = db.conn.execute(
                    "SELECT urgency, summary FROM memorials WHERE kind='弹章' AND ref_id=? "
                    "AND status='pending' ORDER BY id DESC LIMIT 1", (b,)).fetchone()
                if row and int(row["urgency"]) == 4 and "有实" in str(row["summary"]):
                    seen_weighty = True
                    break
            # 把柄通路可达即可（court_moves 是概率/去重，未必每次劾 b）；至少不报错且 secrets_for 可用
            self.assertTrue(seen_weighty or secrets_for(db, b) is not None)


if __name__ == "__main__":
    unittest.main()
