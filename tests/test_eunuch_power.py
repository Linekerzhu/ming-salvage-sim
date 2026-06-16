"""权阉之势（宦官恶趣味 E1）测试：代批红廓清积压、护阉党、权阉日涨、月度漂移。零 LLM。"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim import eunuch_power, memorials, timeflow
from ming_sim.db import GameDB
from ming_sim.upgrade_schema import KV_SHI, kv_set_int


def _fresh(tmp: str):
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    state = db.load_state()
    day = timeflow.ensure_active(db, state)
    return db, state, day


def _eunuch_party_member(db):
    row = db.conn.execute(
        "SELECT name, office FROM characters WHERE status='active' AND power_id='ming' "
        "AND faction='阉党' AND office_type!='后宫' ORDER BY name LIMIT 1").fetchone()
    return (str(row["name"]), str(row["office"])) if row else (None, None)


class SeedAndToggleTests(unittest.TestCase):
    def test_seed_defaults(self):
        with TemporaryDirectory() as tmp:
            db, _, _ = _fresh(tmp)
            self.assertEqual(eunuch_power.get_eunuch_power(db), eunuch_power.EUNUCH_POWER_DEFAULT)
            self.assertFalse(eunuch_power.is_daipihong_on(db))

    def test_toggle_on_requires_keeper_then_curb_lowers_power(self):
        with TemporaryDirectory() as tmp:
            db, _, day = _fresh(tmp)
            self.assertIsNotNone(eunuch_power.chief_keeper_name(db))  # 司礼监掌印在朝
            r = eunuch_power.set_daipihong(db, True, day=day)
            self.assertTrue(r["ok"] and eunuch_power.is_daipihong_on(db))
            kv_set_int(db, eunuch_power.KV_EUNUCH_POWER, 50)
            r2 = eunuch_power.set_daipihong(db, False, day=day)
            self.assertTrue(r2["ok"])
            self.assertFalse(eunuch_power.is_daipihong_on(db))
            self.assertLess(eunuch_power.get_eunuch_power(db), 50)  # 收回批红权挫权阉


class DaipihongProcessTests(unittest.TestCase):
    def test_off_is_noop(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            memorials.create_memorial(db, state, day=day, author_name="韩爌", org="内阁",
                                      kind="请旨", urgency=2, summary="为某事请旨")
            before = db.conn.execute("SELECT COUNT(*) c FROM memorials WHERE status='pending'").fetchone()["c"]
            evs = eunuch_power.daipihong_process(db, state, day + 1)
            self.assertEqual(evs, [])
            after = db.conn.execute("SELECT COUNT(*) c FROM memorials WHERE status='pending'").fetchone()["c"]
            self.assertEqual(before, after)

    def test_clears_backlog_within_daily_limit(self):
        # 寻常奏疏：代批红只解壅塞、不直接加权阉（攀升交月度漂移）。
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            eunuch_power.set_daipihong(db, True, day=day)
            for i in range(8):
                memorials.create_memorial(db, state, day=day, author_name="韩爌", org="内阁",
                                          kind="请旨", urgency=2, summary=f"请旨{i}")
            pend0 = db.conn.execute("SELECT COUNT(*) c FROM memorials WHERE status='pending'").fetchone()["c"]
            evs = eunuch_power.daipihong_process(db, state, day + 1)
            pend1 = db.conn.execute("SELECT COUNT(*) c FROM memorials WHERE status='pending'").fetchone()["c"]
            self.assertLess(pend1, pend0)  # 代批红廓清积压
            self.assertLessEqual(pend0 - pend1, eunuch_power.DAIPIHONG_DAILY_LIMIT)  # 每日有限
            self.assertTrue(evs)

    def test_favoring_eunuch_party_raises_power(self):
        # 照准阉党本党奏请＝实际弄权 → 权阉日涨。
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            eunuch_power.set_daipihong(db, True, day=day)
            author, office = _eunuch_party_member(db)
            self.assertIsNotNone(author)
            for i in range(3):
                memorials.create_memorial(db, state, day=day, author_name=author, org=office,
                                          kind="请旨", urgency=2, summary=f"阉党奏请{i}")
            p0 = eunuch_power.get_eunuch_power(db)
            eunuch_power.daipihong_process(db, state, day + 1)
            self.assertGreater(eunuch_power.get_eunuch_power(db), p0)

    def test_suppresses_impeachment_against_eunuch_party(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            eunuch_power.set_daipihong(db, True, day=day)
            target, _ = _eunuch_party_member(db)
            self.assertIsNotNone(target)
            mid = memorials.create_memorial(
                db, state, day=day, author_name="刘宗周", org="都察院", kind="弹章", urgency=3,
                summary=f"劾{target}", ref_kind="character", ref_id=target)
            eunuch_power.daipihong_process(db, state, day + 1)
            row = db.conn.execute("SELECT status, decision_note FROM memorials WHERE id=?", (mid,)).fetchone()
            self.assertEqual(str(row["status"]), "expired")          # 留中销折
            self.assertIn("留中", str(row["decision_note"]))          # 司礼监护党


class DispositionTests(unittest.TestCase):
    """善恶由委任者品性决定，不按「内臣」身份一刀切。"""

    def test_default_keeper_is_scheming_chief_keeper(self):
        with TemporaryDirectory() as tmp:
            db, _, _ = _fresh(tmp)
            keeper = eunuch_power.daipihong_keeper(db)
            self.assertEqual(keeper, eunuch_power.chief_keeper_name(db))  # 默认司礼监掌印
            # 掌印王体乾属阉党 → 惯于弄权
            self.assertEqual(eunuch_power.keeper_disposition(db, keeper), "scheming")

    def test_wang_chengen_is_upright(self):
        with TemporaryDirectory() as tmp:
            db, _, _ = _fresh(tmp)
            self.assertIsNotNone(eunuch_power._active_eunuch_row(db, "王承恩"))
            self.assertEqual(eunuch_power.keeper_disposition(db, "王承恩"), "upright")

    def test_set_keeper_must_be_eunuch(self):
        with TemporaryDirectory() as tmp:
            db, _, _ = _fresh(tmp)
            self.assertFalse(eunuch_power.set_daipihong_keeper(db, "韩爌")["ok"])  # 外朝大臣不预批红
            r = eunuch_power.set_daipihong_keeper(db, "王承恩")
            self.assertTrue(r["ok"])
            self.assertEqual(eunuch_power.daipihong_keeper(db), "王承恩")

    def test_upright_keeper_holds_impeachment_for_emperor(self):
        # 忠谨者：弹章留与陛下亲览（保持 pending、不销折），寻常奏疏据实拟行。
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            eunuch_power.set_daipihong(db, True, keeper="王承恩", day=day)
            target, _ = _eunuch_party_member(db)
            mid = memorials.create_memorial(
                db, state, day=day, author_name="刘宗周", org="都察院", kind="弹章", urgency=3,
                summary=f"劾{target}", ref_kind="character", ref_id=target)
            ord_mid = memorials.create_memorial(
                db, state, day=day, author_name="韩爌", org="内阁", kind="请旨", urgency=2,
                summary="为某事请旨")
            evs = eunuch_power.daipihong_process(db, state, day + 1)
            imp = db.conn.execute("SELECT status FROM memorials WHERE id=?", (mid,)).fetchone()
            self.assertEqual(str(imp["status"]), "pending")  # 弹章未被销折，留与陛下
            ordr = db.conn.execute("SELECT status FROM memorials WHERE id=?", (ord_mid,)).fetchone()
            self.assertEqual(str(ordr["status"]), "approved")  # 寻常奏疏据实拟行
            self.assertTrue(evs)

    def test_upright_keeper_does_not_favor_eunuch_party_power(self):
        # 忠谨者代批红：权阉不因照准本党而日涨（廓清寻常奏疏不直接加权阉）。
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            eunuch_power.set_daipihong(db, True, keeper="王承恩", day=day)
            author, office = _eunuch_party_member(db)
            for i in range(3):
                memorials.create_memorial(db, state, day=day, author_name=author, org=office,
                                          kind="请旨", urgency=2, summary=f"奏请{i}")
            p0 = eunuch_power.get_eunuch_power(db)
            eunuch_power.daipihong_process(db, state, day + 1)
            self.assertEqual(eunuch_power.get_eunuch_power(db), p0)  # 忠宦不自固

    def test_upright_baseline_low_scheming_baseline_high(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            kv_set_int(db, KV_SHI, 55)
            # 委忠宦：基线 40
            eunuch_power.set_daipihong(db, True, keeper="王承恩", day=day)
            kv_set_int(db, eunuch_power.KV_EUNUCH_POWER, 80)
            eunuch_power.eunuch_power_tick(db, state, day)
            up = eunuch_power.get_eunuch_power(db)
            self.assertLess(up, 80)  # 向低基线 40 落
            # 委权阉：基线 62
            eunuch_power.set_daipihong(db, True, keeper=eunuch_power.chief_keeper_name(db), day=day)
            kv_set_int(db, eunuch_power.KV_EUNUCH_POWER, 30)
            eunuch_power.eunuch_power_tick(db, state, day)
            self.assertGreater(eunuch_power.get_eunuch_power(db), 30)  # 向高基线 62 张


class DriftTests(unittest.TestCase):
    def test_power_rises_when_relying_falls_when_governing(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            kv_set_int(db, KV_SHI, 55)
            # 倚阉：代批红在行 → 权阉向高基线漂移
            eunuch_power.set_daipihong(db, True, day=day)
            kv_set_int(db, eunuch_power.KV_EUNUCH_POWER, 30)
            eunuch_power.eunuch_power_tick(db, state, day)
            rely = eunuch_power.get_eunuch_power(db)
            self.assertGreater(rely, 30)
            # 亲政：代批红已罢 → 权阉向低基线漂移
            kv_set_int(db, eunuch_power.KV_DAIPIHONG, 0)
            kv_set_int(db, eunuch_power.KV_EUNUCH_POWER, 60)
            eunuch_power.eunuch_power_tick(db, state, day)
            self.assertLess(eunuch_power.get_eunuch_power(db), 60)


if __name__ == "__main__":
    unittest.main()
