"""宫斗阴谋 P1 测试：把柄 seed、厂卫侦缉、挟制、活的宫廷自发侦缉。零 LLM。"""

import random
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim import intrigue, timeflow
from ming_sim.db import GameDB


def _fresh(tmp: str):
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    state = db.load_state()
    day = timeflow.ensure_active(db, state)
    return db, state, day


class SeedTests(unittest.TestCase):
    def test_compromised_have_latent_secrets(self):
        with TemporaryDirectory() as tmp:
            db, _, _ = _fresh(tmp)
            n = db.conn.execute("SELECT COUNT(*) c FROM secrets").fetchone()["c"]
            self.assertGreater(n, 0)  # 阉党/贪墨之臣有潜在把柄
            # 魏忠贤（阉党 integrity28）必有把柄
            self.assertIsNotNone(intrigue.latent_secret_of(db, "魏忠贤"))

    def test_secrets_latent_not_known_at_start(self):
        with TemporaryDirectory() as tmp:
            db, _, _ = _fresh(tmp)
            known = db.conn.execute("SELECT COUNT(*) c FROM secrets WHERE known_to_crown=1").fetchone()["c"]
            self.assertEqual(known, 0)  # 开局皆潜在，须侦缉方现
            self.assertIsNone(intrigue.secrets_for(db, "魏忠贤"))  # Person 卡不剧透潜在把柄


class InvestigateTests(unittest.TestCase):
    def test_default_rng_seed_is_stable(self):
        self.assertEqual(
            intrigue._stable_seed(3, "investigate", "崔呈秀"),
            intrigue._stable_seed(3, "investigate", "崔呈秀"),
        )
        self.assertNotEqual(
            intrigue._stable_seed(3, "investigate", "崔呈秀"),
            intrigue._stable_seed(3, "fabricate", "崔呈秀"),
        )

    def test_dongchang_chief_is_weizhongxian(self):
        with TemporaryDirectory() as tmp:
            db, _, _ = _fresh(tmp)
            self.assertEqual(intrigue.dongchang_chief(db), "魏忠贤")  # 东厂提督

    def test_investigate_reveals_secret(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            # 用高胜算 rng 确保侦得
            rng = random.Random(1)
            target = "崔呈秀"  # 阉党，有把柄
            self.assertIsNotNone(intrigue.latent_secret_of(db, target))
            res = None
            for seed in range(20):
                res = intrigue.investigate(db, target, day, rng=random.Random(seed))
                if res.get("found"):
                    break
            self.assertTrue(res["found"])
            self.assertTrue(intrigue.secrets_for(db, target))  # 侦得后 Person 卡可见

    def test_investigate_clean_official_finds_nothing(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            # 找一个无把柄者（清流高节）
            clean = db.conn.execute(
                "SELECT name FROM characters c WHERE c.status='active' AND c.power_id='ming' "
                "AND c.office_type!='后宫' AND c.integrity>=70 "
                "AND NOT EXISTS (SELECT 1 FROM secrets s WHERE s.holder=c.name) LIMIT 1").fetchone()
            self.assertIsNotNone(clean)
            res = intrigue.investigate(db, str(clean["name"]), day)
            self.assertTrue(res["ok"])
            self.assertFalse(res["found"])  # 无把柄可指（除非构陷，P2）


class CoerceTests(unittest.TestCase):
    def _reveal(self, db, state, day, target):
        for seed in range(40):
            if intrigue.investigate(db, target, day, rng=random.Random(seed)).get("found"):
                return True
        return False

    def test_coerce_requires_known_hook(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            r = intrigue.coerce_with_secret(db, state, "崔呈秀", "serve", day)
            self.assertFalse(r["ok"])  # 把柄未发覆，不能挟制

    def test_coerce_submit_flips_to_imperial(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            target = "崔呈秀"
            self.assertTrue(self._reveal(db, state, day, target))
            r = intrigue.coerce_with_secret(db, state, target, "submit", day)
            self.assertTrue(r["ok"])
            fac = str(db.conn.execute("SELECT faction FROM characters WHERE name=?", (target,)).fetchone()["faction"])
            self.assertEqual(fac, "皇党")  # 畏祸输诚归附

    def test_coerce_retire_removes_from_office(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            target = "王绍徽"
            self.assertTrue(self._reveal(db, state, day, target))
            r = intrigue.coerce_with_secret(db, state, target, "retire", day)
            self.assertTrue(r["ok"])
            self.assertEqual(db.get_character_status(target)[0], "retired")


class ChangweiTickTests(unittest.TestCase):
    def test_no_probe_when_eunuch_power_low(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            from ming_sim.upgrade_schema import kv_set_int
            from ming_sim.eunuch_power import KV_EUNUCH_POWER
            kv_set_int(db, KV_EUNUCH_POWER, 30)  # 权阉低 → 厂卫不横行
            self.assertEqual(intrigue.changwei_tick(db, state, day), [])

    def test_probe_when_eunuch_power_high(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            from ming_sim.upgrade_schema import kv_set_int
            from ming_sim.eunuch_power import KV_EUNUCH_POWER
            kv_set_int(db, KV_EUNUCH_POWER, 80)  # 权阉炽盛 → 东厂自发侦缉异党
            fired = False
            for m in range(12):
                if any(e["kind"] == "changwei_probe" for e in intrigue.changwei_tick(db, state, day + m * 30)):
                    fired = True
                    break
            self.assertTrue(fired)


if __name__ == "__main__":
    unittest.main()
