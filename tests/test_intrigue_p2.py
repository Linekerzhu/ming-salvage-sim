"""宫斗阴谋 P2 测试：构陷诏狱（清誉护身/暴露反噬）+ 离间计（笃实识破）。零 LLM。"""

import random
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim import intrigue, timeflow
from ming_sim.db import GameDB
from ming_sim.upgrade_schema import KV_SHI, kv_int, kv_set_int
from ming_sim.eunuch_power import KV_EUNUCH_POWER


def _fresh(tmp: str):
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    state = db.load_state()
    day = timeflow.ensure_active(db, state)
    return db, state, day


class FabricateTests(unittest.TestCase):
    def test_fabricate_jails_when_eunuch_power_high(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            kv_set_int(db, KV_EUNUCH_POWER, 90)  # 厂卫炽盛 → 罗织易成
            # 取一个非清流、可被陷者
            target = db.conn.execute(
                "SELECT name FROM characters WHERE status='active' AND power_id='ming' "
                "AND office_type!='后宫' AND integrity<=55 AND faction NOT LIKE '%东林%' LIMIT 1").fetchone()["name"]
            jailed = False
            for seed in range(30):
                res = intrigue.fabricate(db, state, str(target), day, rng=random.Random(seed))
                if res.get("imprisoned"):
                    jailed = True
                    break
                # 复原以便重试（被反噬不改状态）
            self.assertTrue(jailed)
            self.assertEqual(db.get_character_status(str(target))[0], "imprisoned")

    def test_fabricate_backfires_hurts_shi(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            kv_set_int(db, KV_EUNUCH_POWER, 20)   # 厂卫弱
            kv_set_int(db, KV_SHI, 60)
            # 清流高节者：清誉护身 → 必暴露反噬
            target = db.conn.execute(
                "SELECT name FROM characters WHERE status='active' AND power_id='ming' "
                "AND office_type!='后宫' AND integrity>=72 ORDER BY integrity DESC LIMIT 1").fetchone()["name"]
            shi0 = kv_int(db, KV_SHI, 60)
            res = intrigue.fabricate(db, state, str(target), day, rng=random.Random(3))
            self.assertTrue(res["ok"])
            self.assertFalse(res["success"])                          # 高节难陷 → 反噬
            self.assertEqual(db.get_character_status(str(target))[0], "active")  # 未下狱
            self.assertLess(kv_int(db, KV_SHI, 60), shi0)             # 君威受损

    def test_fabricate_no_dongchang(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            # 东厂提督回退司礼监掌印，故须并去东厂+司礼监方无厂卫可遣
            db.conn.execute("UPDATE characters SET status='dead' "
                            "WHERE office LIKE '%东厂%' OR office LIKE '%司礼监%' OR office_type LIKE '%司礼监%'")
            db.conn.commit()
            self.assertIsNone(intrigue.dongchang_chief(db))
            res = intrigue.fabricate(db, state, "韩爌", day)
            self.assertFalse(res["ok"])  # 无东厂可遣


class DiscordTests(unittest.TestCase):
    def test_discord_sours_relationship(self):
        from ming_sim import court
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            # 造一对低节者（不会识破），先结好感
            db.conn.execute("UPDATE characters SET integrity=40, loyalty=40 WHERE name IN ('崔呈秀','王绍徽')")
            db.conn.commit()
            court.adjust_opinion(db, "崔呈秀", "王绍徽", 40, "同党", day=day)
            before = court.get_opinion(db, "崔呈秀", "王绍徽")
            res = intrigue.sow_discord(db, state, "崔呈秀", "王绍徽", day, rng=random.Random(1))
            self.assertTrue(res["success"])
            self.assertLess(court.get_opinion(db, "崔呈秀", "王绍徽"), before)  # 好感跌

    def test_discord_seen_through_by_loyal(self):
        from ming_sim import court
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            db.conn.execute("UPDATE characters SET integrity=85, loyalty=85 WHERE name IN ('韩爌','刘宗周')")
            db.conn.commit()
            shi0 = kv_int(db, KV_SHI, 55)
            # 笃实者多半识破（多 seed 取识破样本）
            seen = False
            for seed in range(20):
                # 重置关系
                db.conn.execute("DELETE FROM relationships WHERE a_name IN ('韩爌','刘宗周') AND b_name IN ('韩爌','刘宗周')")
                db.conn.commit()
                res = intrigue.sow_discord(db, state, "韩爌", "刘宗周", day, rng=random.Random(seed))
                if not res["success"]:
                    seen = True
                    break
            self.assertTrue(seen)  # 存在被识破的情形


if __name__ == "__main__":
    unittest.main()
