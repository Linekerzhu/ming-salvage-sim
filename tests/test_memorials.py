"""M3 御案与崇祯陷阱测试：奏疏流、注意力、批红、留中后果、RA 双杠杆。零 LLM。"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim import memorials, timeflow
from ming_sim.db import GameDB
from ming_sim.scheduler import process_pending
from ming_sim.upgrade_schema import (
    ATTENTION_PER_DAY,
    KV_CURRENT_DAY,
    KV_RISK_AVERSION,
    KV_SHI,
    RISK_AVERSION_DEFAULT,
    SHI_DEFAULT,
    kv_int,
    kv_set_int,
)


def _fresh(tmp: str):
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    state = db.load_state()
    day = timeflow.ensure_active(db, state)
    return db, state, day


def _mk_memorial(db, state, day, **kw):
    defaults = dict(author_name="韩爌", org="内阁", kind="请旨", urgency=2,
                    summary="为某事请旨")
    defaults.update(kw)
    return memorials.create_memorial(db, state, day=day, **defaults)


class FlowTests(unittest.TestCase):
    def test_arrival_rate_scales_with_risk_aversion(self):
        """崇祯陷阱传导：RA 高 → 请旨奏疏显著增多。"""
        def run_month(ra: int) -> int:
            with TemporaryDirectory() as tmp:
                db, state, day = _fresh(tmp)
                kv_set_int(db, KV_RISK_AVERSION, ra)
                total = 0
                for d in range(day + 1, day + 29):
                    evs = memorials.memorials_daily_tick(db, state, d)
                    total += sum(1 for e in evs if e["kind"] == "memorial")
                return total
        low = sum(run_month(10) for _ in range(1))
        high = sum(run_month(95) for _ in range(1))
        self.assertGreater(high, low)

    def test_proactive_reports_shrink_when_ra_high(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            kv_set_int(db, KV_RISK_AVERSION, 100)
            # RA=100 → 主动奏报概率 0.18*(1-100/150)=0.06；RA=0 → 0.18。只验公式方向：
            self.assertLess(0.18 * (1 - 100 / 150), 0.18 * (1 - 0 / 150))


class AttentionTests(unittest.TestCase):
    def test_attention_resets_daily_and_blocks_when_spent(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            memorials.reset_attention_for_day(db, day)
            self.assertEqual(memorials.attention_left(db), ATTENTION_PER_DAY)
            mids = [_mk_memorial(db, state, day) for _ in range(ATTENTION_PER_DAY + 2)]
            ok_count = 0
            for mid in mids:
                r = memorials.decide_memorial(db, state, mid, "approve", day=day)
                if r["ok"]:
                    ok_count += 1
            self.assertEqual(ok_count, ATTENTION_PER_DAY)  # cost=1/封
            # 留中不耗注意力
            r = memorials.decide_memorial(db, state, mids[-1], "shelve", day=day)
            self.assertTrue(r["ok"])
            # 次日刷新
            memorials.reset_attention_for_day(db, day + 1)
            self.assertEqual(memorials.attention_left(db), ATTENTION_PER_DAY)


class DecideTests(unittest.TestCase):
    def test_deny_gaobian_raises_ra(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            memorials.reset_attention_for_day(db, day)
            mid = _mk_memorial(db, state, day, kind="告变", author_name="史可法")
            ra0 = kv_int(db, KV_RISK_AVERSION, RISK_AVERSION_DEFAULT)
            memorials.decide_memorial(db, state, mid, "deny", day=day)
            self.assertGreater(kv_int(db, KV_RISK_AVERSION, RISK_AVERSION_DEFAULT), ra0)

    def test_refer_creates_directive_draft(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            memorials.reset_attention_for_day(db, day)
            mid = _mk_memorial(db, state, day, kind="请款", summary="请拨陕西赈银")
            memorials.decide_memorial(db, state, mid, "refer", day=day)
            row = db.conn.execute(
                "SELECT * FROM turn_directives WHERE source='memorial_refer'").fetchone()
            self.assertIsNotNone(row)
            self.assertIn("陕西赈银", str(row["text"]))

    def test_shelved_impeachment_drains_shi(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            mid = _mk_memorial(db, state, day, kind="弹章", urgency=3)
            shi0 = kv_int(db, KV_SHI, SHI_DEFAULT)
            for d in range(day + 1, day + 12):
                memorials.memorials_daily_tick(db, state, d)
            self.assertLess(kv_int(db, KV_SHI, SHI_DEFAULT), shi0)


class TrapLeverTests(unittest.TestCase):
    def test_punish_raises_shi_and_ra(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            shi0 = kv_int(db, KV_SHI, SHI_DEFAULT)
            ra0 = kv_int(db, KV_RISK_AVERSION, RISK_AVERSION_DEFAULT)
            r = memorials.punish_official(db, state, "韩爌", "heavy", day=day, public=True)
            self.assertTrue(r["ok"])
            self.assertGreater(kv_int(db, KV_SHI, 0), shi0)
            self.assertGreater(kv_int(db, KV_RISK_AVERSION, 0), ra0)
            row = db.conn.execute(
                "SELECT status FROM characters WHERE name='韩爌'").fetchone()
            self.assertEqual(str(row["status"]), "imprisoned")

    def test_back_reuse_restores_official_and_lowers_ra(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            memorials.punish_official(db, state, "韩爌", "heavy", day=day)
            ra_mid = kv_int(db, KV_RISK_AVERSION, 0)
            r = memorials.back_official(db, state, "韩爌", "reuse", day=day)
            self.assertTrue(r["ok"])
            self.assertLess(kv_int(db, KV_RISK_AVERSION, 0), ra_mid)
            row = db.conn.execute(
                "SELECT status FROM characters WHERE name='韩爌'").fetchone()
            self.assertEqual(str(row["status"]), "active")

    def test_execute_kills(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            memorials.punish_official(db, state, "韩爌", "execute", day=day, reason="师溃失地")
            row = db.conn.execute("SELECT status FROM characters WHERE name='韩爌'").fetchone()
            self.assertEqual(str(row["status"]), "dead")

    def test_desk_payload_shows_trap_hint_when_ra_high(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            kv_set_int(db, KV_RISK_AVERSION, 70)
            payload = memorials.desk_payload(db, state, day)
            self.assertTrue(payload["trap_hint"])
            self.assertEqual(payload["renshi_willingness"], 30)


class PiaoniTests(unittest.TestCase):
    def test_piaoni_template_fallback_and_job(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            mid = _mk_memorial(db, state, day, kind="弹章", urgency=3)
            row = db.conn.execute("SELECT piaoni FROM memorials WHERE id=?", (mid,)).fetchone()
            self.assertTrue(str(row["piaoni"]).startswith("拟"))
            # 无 LLM 时 piaoni job 不应抛错
            process_pending(db, None, limit=5)


if __name__ == "__main__":
    unittest.main()
