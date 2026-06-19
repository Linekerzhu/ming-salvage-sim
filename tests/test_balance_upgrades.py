"""平衡升级回归测试（零 LLM）：
- 皇威向威权基线漂移（破棘轮）
- 封驳搁置势折损封顶 + 久搁自罢（破势崩盘）
- 内帑助饷 release_privy_funds（内库去处 + 国库/欠饷救济）
"""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim import flows, lifecycle, timeflow
from ming_sim.db import GameDB
from ming_sim.upgrade_schema import KV_SHI, kv_int


def _fresh(tmp: str):
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    state = db.load_state()
    day = timeflow.ensure_active(db, state)
    return db, state, day


class HuangweiDriftTests(unittest.TestCase):
    def test_high_huangwei_decays_toward_baseline(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            state.metrics["皇威"] = 100  # 棘轮顶
            timeflow._huangwei_drift_to_baseline(db, state, day)
            self.assertLess(state.metrics["皇威"], 100)  # 不再钉死 100

    def test_low_huangwei_recovers_toward_baseline(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            state.metrics["皇威"] = 5
            before = state.metrics["皇威"]
            timeflow._huangwei_drift_to_baseline(db, state, day)
            self.assertGreater(state.metrics["皇威"], before)  # 向基线回升

    def test_baseline_tracks_authority(self):
        # 势高则皇威基线更高：从 0 起，高势局回升得更高
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            from ming_sim.upgrade_schema import adjust_belief
            adjust_belief(db, KV_SHI, 25, "test", day=day)  # 推高势
            state.metrics["皇威"] = 10
            timeflow._huangwei_drift_to_baseline(db, state, day)
            high_shi = state.metrics["皇威"]
            self.assertGreater(high_shi, 10)


class StallBleedTests(unittest.TestCase):
    def _make_stalled(self, db, day, age_days):
        start = day - 5 - 5 - age_days  # lead=5, exec=5, 其余=搁置时长
        db.conn.execute(
            """INSERT INTO turn_directives
               (turn, year, period, text, source, assignee, lifecycle_status,
                start_day, lead_days, exec_days, eta_day, progress,
                integrity_actual, integrity_reported, anomaly, category)
               VALUES (1,1628,1,'清丈田亩','test','韩爌','stalled',?,5,5,?,40,80,90,'','政务')""",
            (start, start + 10),
        )
        db.conn.commit()
        return int(db.conn.execute("SELECT last_insert_rowid() AS i").fetchone()["i"])

    def test_stall_bleed_caps_at_three(self):
        with TemporaryDirectory() as tmp:
            db, state, day0 = _fresh(tmp)
            did = self._make_stalled(db, day0, age_days=0)
            shi_before = kv_int(db, KV_SHI, 55)
            # 推进 80 天（>8 个旬），若无封顶会扣 −8；封顶应只扣 −3
            d = day0
            for _ in range(8):
                d += 10
                lifecycle.tick_directives(db, state, d)
            row = db.conn.execute(
                "SELECT lifecycle_status, anomaly FROM turn_directives WHERE id=?", (did,)
            ).fetchone()
            # 久搁（>30 日）应已自动作罢
            self.assertEqual(row["lifecycle_status"], "aborted")
            shi_after = kv_int(db, KV_SHI, 55)
            # 累计折损不超过 3（封顶），不会棘轮碾压
            self.assertGreaterEqual(shi_after, shi_before - 3)

    def test_chronic_stall_auto_aborts(self):
        with TemporaryDirectory() as tmp:
            db, state, day0 = _fresh(tmp)
            did = self._make_stalled(db, day0, age_days=40)  # 已搁置 40 日
            lifecycle.tick_directives(db, state, day0 + 10)
            status = db.conn.execute(
                "SELECT lifecycle_status FROM turn_directives WHERE id=?", (did,)
            ).fetchone()["lifecycle_status"]
            self.assertEqual(status, "aborted")  # 久搁自罢，止血清积压


class PrivyReliefTests(unittest.TestCase):
    def test_moves_nei_to_guo(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            state.metrics["内库"] = 200
            state.metrics["国库"] = 50
            res = flows.release_privy_funds(db, state, 80, day)
            self.assertTrue(res["ok"])
            self.assertEqual(res["moved"], 80)
            self.assertEqual(state.metrics["内库"], 120)
            # 国库净增 = moved − 已清欠饷
            self.assertEqual(state.metrics["国库"], 50 + 80 - res["arrears_cleared"])

    def test_capped_by_nei_balance(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            state.metrics["内库"] = 30
            res = flows.release_privy_funds(db, state, 500, day)
            self.assertEqual(res["moved"], 30)  # 封顶于内库余额
            self.assertEqual(state.metrics["内库"], 0)

    def test_empty_nei_rejected(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            state.metrics["内库"] = 0
            res = flows.release_privy_funds(db, state, 50, day)
            self.assertFalse(res["ok"])

    def test_clears_arrears_and_lifts_morale(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            state.metrics["内库"] = 300
            row = db.conn.execute(
                "SELECT id, arrears, morale FROM armies WHERE owner_power='ming' AND arrears>0 "
                "ORDER BY arrears DESC LIMIT 1"
            ).fetchone()
            if row is None:  # 无欠饷军则造一笔
                db.conn.execute(
                    "UPDATE armies SET arrears=60, morale=40 WHERE owner_power='ming' "
                    "AND id=(SELECT id FROM armies WHERE owner_power='ming' LIMIT 1)")
                db.conn.commit()
                row = db.conn.execute(
                    "SELECT id, arrears, morale FROM armies WHERE arrears>0 LIMIT 1").fetchone()
            mor_before = int(row["morale"])
            res = flows.release_privy_funds(db, state, 300, day)
            self.assertGreater(res["arrears_cleared"], 0)
            new_mor = int(db.conn.execute(
                "SELECT morale FROM armies WHERE id=?", (row["id"],)).fetchone()["morale"])
            self.assertGreaterEqual(new_mor, mor_before)  # 清欠即振士气


class EunuchBriefCardTests(unittest.TestCase):
    def _set_power(self, db, day, value):
        from ming_sim.eunuch_power import adjust_eunuch_power, get_eunuch_power
        adjust_eunuch_power(db, value - int(get_eunuch_power(db)), "test", day=day)

    def test_midband_surfaces_eunuch_card(self):
        from ming_sim import playstyle
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            self._set_power(db, day, 58)  # 中段 45–74
            cards = playstyle._briefing_candidates(db, state)
            eu = [c for c in cards if c.get("kind") == "eunuch"]
            self.assertEqual(len(eu), 1)
            self.assertIn("权阉之势", str(eu[0]["title"]))

    def test_low_power_no_card(self):
        from ming_sim import playstyle
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            self._set_power(db, day, 30)  # 低于 45 不足为虑
            eu = [c for c in playstyle._briefing_candidates(db, state) if c.get("kind") == "eunuch"]
            self.assertEqual(len(eu), 0)

    def test_crisis_band_defers_to_decision(self):
        from ming_sim import playstyle
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            self._set_power(db, day, 80)  # ≥75 走阉祸危机抉择，brief 不重复
            eu = [c for c in playstyle._briefing_candidates(db, state) if c.get("kind") == "eunuch"]
            self.assertEqual(len(eu), 0)


if __name__ == "__main__":
    unittest.main()
