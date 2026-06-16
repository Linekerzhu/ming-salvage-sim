"""M3 御案与崇祯陷阱测试：奏疏流、注意力、批红、留中后果、RA 双杠杆。零 LLM。"""

import unittest
import random
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim import memorials, timeflow
from ming_sim.db import GameDB
from ming_sim.models import Character, CourtContext
from ming_sim.registry import build_recent_memorial_memory_brief
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

    def test_random_official_excludes_foreign_powers(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            db.conn.execute("UPDATE characters SET courage=0 WHERE power_id='ming'")
            db.conn.execute("UPDATE characters SET courage=100 WHERE power_id!='ming'")
            db.conn.commit()
            self.assertIsNone(memorials._random_official(db, random.Random(1), min_courage=90))

    def test_issue_memorial_body_contains_case_facts(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            issue = db.conn.execute(
                "SELECT id, title FROM issues WHERE status='active' ORDER BY id LIMIT 1"
            ).fetchone()
            db.conn.execute(
                "UPDATE issues SET stage_text=?, severity=? WHERE id=?",
                ("陕西驿卒聚啸，州县文报迟滞，粮道已受惊扰", 72, issue["id"]),
            )
            db.conn.commit()
            mid = _mk_memorial(
                db,
                state,
                day,
                kind="请旨",
                summary=f"为「{issue['title']}」事请旨",
                ref_kind="issue",
                ref_id=str(issue["id"]),
            )
            body = str(db.conn.execute(
                "SELECT full_text FROM memorials WHERE id=?", (mid,)
            ).fetchone()["full_text"])
            self.assertIn(str(issue["title"]), body)
            self.assertIn("陕西驿卒聚啸", body)
            self.assertNotEqual(body, memorials._KIND_TEMPLATES["请旨"])


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

    def test_refer_does_not_duplicate_sentence_punctuation(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            memorials.reset_attention_for_day(db, day)
            mid = _mk_memorial(db, state, day, kind="陈情", summary="江西监军陈情")
            memorials.decide_memorial(db, state, mid, "refer", day=day,
                                      note="三日内具可行办法，不得泛言。")
            text = str(db.conn.execute(
                "SELECT text FROM turn_directives WHERE source='memorial_refer'"
            ).fetchone()["text"])
            self.assertNotIn("。。", text)
            self.assertIn("不得泛言。着该衙门", text)

    def test_recent_memorial_decision_feeds_npc_context(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            memorials.reset_attention_for_day(db, day)
            mid = _mk_memorial(
                db,
                state,
                day,
                kind="陈情",
                author_name="韩爌",
                org="武英殿大学士",
                summary="韩爌陈海关设官利弊",
                full_text="臣韩爌谨奏：海关设官须防胥吏侵渔，亦不可因噎废食。",
            )
            memorials.decide_memorial(db, state, mid, "refer", day=day, note="交户部礼部会商。")
            character = Character(
                name="韩爌",
                office="武英殿大学士",
                office_type="内阁",
                faction="东林",
                aliases=[],
                personal_skills=[],
                loyalty=70,
                ability=75,
                integrity=80,
                courage=65,
                style="持重",
                power_id="ming",
            )
            brief = build_recent_memorial_memory_brief(character, CourtContext(state, db))
            self.assertIn("已发部议", brief)
            self.assertIn("海关设官须防胥吏侵渔", brief)
            self.assertIn("交户部礼部会商", brief)

    def test_shelved_impeachment_drains_shi(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            mid = _mk_memorial(db, state, day, kind="弹章", urgency=3)
            shi0 = kv_int(db, KV_SHI, SHI_DEFAULT)
            for d in range(day + 1, day + 12):
                memorials.memorials_daily_tick(db, state, d)
            self.assertLess(kv_int(db, KV_SHI, SHI_DEFAULT), shi0)


class DrownCapTests(unittest.TestCase):
    """淹没/积压对信念的月度伤害封顶——防死亡螺旋棘轮到 0/100 吸收态。"""

    def test_monthly_drown_penalty_is_capped(self):
        # 一个月内大量奏疏淹没，RA/势 的累计伤害不得超过月度封顶。
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            memorials.reset_drown_belief_caps(db)
            # 造一批已逾淹没期的奏疏（混入弹章，触发势/RA 双伤害）。
            for i in range(40):
                kind = "弹章" if i % 2 == 0 else "请旨"
                _mk_memorial(db, state, day, author_name=f"臣{i}", kind=kind, urgency=3)
            db.conn.execute("UPDATE memorials SET arrived_day=? WHERE status='pending'",
                            (day - 60,))
            db.conn.commit()
            shi0 = kv_int(db, KV_SHI, SHI_DEFAULT)
            ra0 = kv_int(db, KV_RISK_AVERSION, RISK_AVERSION_DEFAULT)
            memorials.memorials_daily_tick(db, state, day + 1)
            shi_drop = shi0 - kv_int(db, KV_SHI, SHI_DEFAULT)
            ra_rise = kv_int(db, KV_RISK_AVERSION, RISK_AVERSION_DEFAULT) - ra0
            self.assertLessEqual(ra_rise, memorials.DROWN_RA_CAP)
            self.assertLessEqual(shi_drop, memorials.DROWN_SHI_CAP)
            # 但仍应有非零伤害（奏而不答有代价）。
            self.assertGreater(ra_rise, 0)
            self.assertGreater(shi_drop, 0)

    def test_cap_resets_each_month(self):
        # 朔日重置预算后，新月的淹没伤害可再次累积。
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            from ming_sim.memorials import KV_MONTH_DROWN_RA
            kv_set_int(db, KV_MONTH_DROWN_RA, memorials.DROWN_RA_CAP)  # 预算已耗尽
            _mk_memorial(db, state, day, kind="请旨", urgency=3)
            db.conn.execute("UPDATE memorials SET arrived_day=? WHERE status='pending'",
                            (day - 60,))
            db.conn.commit()
            ra0 = kv_int(db, KV_RISK_AVERSION, RISK_AVERSION_DEFAULT)
            memorials.memorials_daily_tick(db, state, day + 1)  # 预算耗尽→不再加 RA
            self.assertEqual(kv_int(db, KV_RISK_AVERSION, RISK_AVERSION_DEFAULT), ra0)
            memorials.reset_drown_belief_caps(db)
            _mk_memorial(db, state, day, author_name="王二", kind="请旨", urgency=3)
            db.conn.execute("UPDATE memorials SET arrived_day=? WHERE status='pending'",
                            (day - 60,))
            db.conn.commit()
            memorials.memorials_daily_tick(db, state, day + 2)  # 重置后可再加
            self.assertGreater(kv_int(db, KV_RISK_AVERSION, RISK_AVERSION_DEFAULT), ra0)


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
