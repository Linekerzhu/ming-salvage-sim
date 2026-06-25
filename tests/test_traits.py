"""性格特质系统（CK3 化 P4）测试：推导、播种、行为偏置、皇帝特质修正。零 LLM。"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim import traits, timeflow
from ming_sim.db import GameDB


def _fresh(tmp: str):
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    state = db.load_state()
    timeflow.ensure_active(db, state)
    return db, state


class DeriveTests(unittest.TestCase):
    def test_detects_logic_markers_and_aliases(self):
        ts = traits.derive_traits("痼疾在贪墨成性、猜忌多疑；强项铁面无私、知兵。", 50, 50, 50)
        self.assertIn("贪墨成性", ts)
        self.assertIn("猜忌多疑", ts)
        self.assertIn("清廉自守", ts)  # 铁面无私→清廉自守 别名
        self.assertIn("知兵", ts)

    def test_stat_derived_positive_traits(self):
        ts = traits.derive_traits("", integrity=85, ability=85, loyalty=90)
        self.assertIn("清廉自守", ts)
        self.assertIn("干练", ts)
        self.assertIn("忠谨", ts)


class SeedTests(unittest.TestCase):
    def test_seed_populates_and_idempotent(self):
        with TemporaryDirectory() as tmp:
            db, _ = _fresh(tmp)
            n = db.conn.execute("SELECT COUNT(*) FROM character_traits").fetchone()[0]
            self.assertGreater(n, 30, "应播出可观的特质")
            self.assertEqual(traits.seed_traits(db), 0, "再播应幂等无新增")

    def test_traits_of_returns_valence(self):
        with TemporaryDirectory() as tmp:
            db, _ = _fresh(tmp)
            row = db.conn.execute("SELECT name FROM character_traits LIMIT 1").fetchone()
            ts = traits.traits_of(db, row["name"])
            self.assertTrue(ts)
            self.assertIn("valence", ts[0])
            self.assertIn("desc", ts[0])

    def test_public_traits_can_hide_medical_record_traits(self):
        with TemporaryDirectory() as tmp:
            db, _ = _fresh(tmp)
            db.conn.execute(
                "INSERT OR REPLACE INTO character_traits (name, trait, valence) VALUES (?,?,?)",
                ("测甲", "情欲异化", -1),
            )
            db.conn.execute(
                "INSERT OR REPLACE INTO character_traits (name, trait, valence) VALUES (?,?,?)",
                ("测甲", "直言不讳", 1),
            )
            db.conn.commit()

            public = traits.traits_of(db, "测甲", include_medical=False)
            raw = traits.traits_of(db, "测甲")

            self.assertIn("情欲异化", {item["key"] for item in raw})
            self.assertNotIn("情欲异化", {item["key"] for item in public})
            self.assertIn("直言不讳", {item["key"] for item in public})


class BiasTests(unittest.TestCase):
    def test_trait_bias_reflects_traits(self):
        with TemporaryDirectory() as tmp:
            db, _ = _fresh(tmp)
            db.conn.execute("INSERT OR REPLACE INTO character_traits VALUES ('测甲','直言不讳',1)")
            db.conn.execute("INSERT OR REPLACE INTO character_traits VALUES ('测乙','结党营私',-1)")
            db.conn.commit()
            self.assertGreater(traits.trait_bias(db, "测甲")["impeach"], 0.3)
            self.assertGreater(traits.trait_bias(db, "测乙")["patronage"], 0.3)
            self.assertEqual(traits.trait_bias(db, "无此人")["impeach"], 0.0)

    def test_emperor_amplifier(self):
        with TemporaryDirectory() as tmp:
            db, _ = _fresh(tmp)
            self.assertGreater(traits.emperor_renshi_amplifier(db), 1.0,
                               "崇祯猜忌多疑应放大任事负向波动")


if __name__ == "__main__":
    unittest.main()
