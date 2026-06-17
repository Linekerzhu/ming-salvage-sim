"""阉祸危机 E3 测试：权阉炽极弹危机大抉择，翦除/隐忍/倚阉三路后果。零 LLM。"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim import court_events, timeflow
from ming_sim.db import GameDB
from ming_sim.eunuch_power import KV_EUNUCH_POWER, get_eunuch_power
from ming_sim.upgrade_schema import KV_SHI, kv_int, kv_set_int


def _fresh(tmp: str):
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    state = db.load_state()
    day = timeflow.ensure_active(db, state)
    db.kv_set(court_events.KV_PENDING, "")
    db.kv_set(court_events.KV_COOLDOWN, "{}")
    return db, state, day


class TriggerTests(unittest.TestCase):
    def test_no_crisis_when_power_low(self):
        with TemporaryDirectory() as tmp:
            db, _, _ = _fresh(tmp)
            kv_set_int(db, KV_EUNUCH_POWER, 40)
            self.assertIsNone(court_events._eunuch_crisis(db))

    def test_crisis_fires_when_power_extreme(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            kv_set_int(db, KV_EUNUCH_POWER, 85)
            ctx = court_events._eunuch_crisis(db)
            self.assertIsNotNone(ctx)
            self.assertEqual(ctx["eunuch"], "魏忠贤")  # 东厂提督
            # 阉祸优先级最高 → evaluate 命中即此
            payload = court_events.evaluate_decisions(db, state, day)
            self.assertIsNotNone(payload)
            self.assertEqual(payload["id"], "eunuch_crisis")


class ResolveTests(unittest.TestCase):
    def _arm(self, db, state, day):
        kv_set_int(db, KV_EUNUCH_POWER, 85)
        court_events.evaluate_decisions(db, state, day)

    def test_purge_jails_eunuch_and_collapses_power(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            kv_set_int(db, KV_SHI, 55)  # 君威足 → 干净翦除
            self._arm(db, state, day)
            r = court_events.resolve_decision(db, state, "purge", day)
            self.assertTrue(r["ok"])
            self.assertEqual(db.get_character_status("魏忠贤")[0], "imprisoned")  # 下诏狱
            self.assertLess(get_eunuch_power(db), 50)  # 权阉土崩

    def test_purge_when_weak_is_messy_but_still_jails(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            kv_set_int(db, KV_SHI, 30)  # 君威弱 → 仓促动荡
            self._arm(db, state, day)
            shi0 = kv_int(db, KV_SHI, 30)
            r = court_events.resolve_decision(db, state, "purge", day)
            self.assertTrue(r["ok"])
            self.assertEqual(db.get_character_status("魏忠贤")[0], "imprisoned")
            self.assertLess(kv_int(db, KV_SHI, 30), shi0)  # 仓促除阉反损君威

    def test_rely_pushes_power_higher(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            self._arm(db, state, day)
            p0 = get_eunuch_power(db)
            r = court_events.resolve_decision(db, state, "rely", day)
            self.assertTrue(r["ok"])
            self.assertGreaterEqual(get_eunuch_power(db), p0)  # 倚阉 → 权阉冲顶
            self.assertEqual(db.get_character_status("魏忠贤")[0], "active")  # 未除

    def test_endure_mildly_curbs(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            self._arm(db, state, day)
            p0 = get_eunuch_power(db)
            r = court_events.resolve_decision(db, state, "endure", day)
            self.assertTrue(r["ok"])
            self.assertLess(get_eunuch_power(db), p0)  # 略抑
            self.assertGreater(get_eunuch_power(db), 50)  # 但未除根


if __name__ == "__main__":
    unittest.main()
