"""三个整合缺口修复的回归测试（零 LLM）：
  缺口1 因果伏笔在「裁驿」类政策办结时被埋种；
  缺口2 势驱动派系气焰(heat) 与 税收到账率；
  缺口3 截留(integrity_actual<85)办结即施加民怨/民变压力机械后果。
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim import lifecycle, theater, timeflow
from ming_sim.db import GameDB
from ming_sim.flows import calc_province_fiscal
from ming_sim.upgrade_schema import KV_CURRENT_DAY, KV_SHI, kv_int, kv_set_int


def _fresh(tmp: str):
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    state = db.load_state()
    timeflow.ensure_active(db, state)
    return db, state


def _issue(db, state, text: str) -> int:
    cur = db.conn.execute(
        "INSERT INTO turn_directives (turn, year, period, text, source, status)"
        " VALUES (?,?,?,?,?,?)",
        (state.turn, state.year, state.period, text, "test", "confirmed"),
    )
    did = int(cur.lastrowid)
    db.conn.commit()
    rows = db.conn.execute("SELECT * FROM turn_directives WHERE id=?", (did,)).fetchall()
    lifecycle.init_directive_lifecycles(db, state, rows, kv_int(db, KV_CURRENT_DAY, 1))
    return did


def _force_done(db, state, did: int, *, integrity_actual: int = 100) -> None:
    db.conn.execute(
        "UPDATE turn_directives SET integrity_actual=?, progress=99, "
        "lifecycle_status='executing', lead_days=0 WHERE id=?", (integrity_actual, did))
    db.conn.commit()
    timeflow.advance_days(db, state, 1, stop_on_yellow=False)


class CausalSeedPlantingTests(unittest.TestCase):
    def test_post_station_cut_plants_seed(self):
        """缺口1：裁驿类旨意办结 → causal_seeds 埋下流寇伏笔。"""
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            self.assertEqual(
                db.conn.execute("SELECT COUNT(*) c FROM causal_seeds").fetchone()["c"], 0)
            did = _issue(db, state, "裁撤天下驿递冗员，岁省驿银")
            _force_done(db, state, did)
            seeds = db.conn.execute("SELECT * FROM causal_seeds").fetchall()
            self.assertGreaterEqual(len(seeds), 1)
            self.assertEqual(str(seeds[0]["status"]), "armed")
            self.assertGreater(int(seeds[0]["fuse_day"]), int(seeds[0]["created_day"]))

    def test_benign_directive_plants_nothing(self):
        """无延迟代价的礼仪信号旨意不埋伏笔。"""
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            did = _issue(db, state, "祭告太庙，旌表忠烈")
            _force_done(db, state, did)
            self.assertEqual(
                db.conn.execute("SELECT COUNT(*) c FROM causal_seeds").fetchone()["c"], 0)

    def test_planted_seed_sprouts_into_issue(self):
        """埋下的伏笔过引信日后萌发为情势 issue（空 gate）。"""
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            did = _issue(db, state, "裁撤天下驿递冗员，岁省驿银")
            _force_done(db, state, did)
            n0 = db.conn.execute("SELECT COUNT(*) c FROM issues").fetchone()["c"]
            # 快进到引信日之后（裁驿 fuse=75 天）；分月推进绕过月末停驻
            for _ in range(4):
                timeflow.advance_days(db, state, 30, stop_on_yellow=False)
                timeflow.month_fixed_flows(db, state)
                state.next_period()
                db.save_state(state)
                timeflow.on_month_resolved(db, state)
            sprouted = db.conn.execute(
                "SELECT COUNT(*) c FROM causal_seeds WHERE status='sprouted'").fetchone()["c"]
            self.assertGreaterEqual(sprouted, 1)
            self.assertGreater(
                db.conn.execute("SELECT COUNT(*) c FROM issues").fetchone()["c"], n0)


class ShiConsumptionTests(unittest.TestCase):
    def test_low_shi_emboldens_factions(self):
        """缺口2a：势低 → 派系 heat 逐旬上涨（君威不振，党争气焰盛）。"""
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            kv_set_int(db, KV_SHI, 15)
            before = db.conn.execute("SELECT SUM(heat) s FROM factions").fetchone()["s"]
            theater.faction_moves_tick(db, state, day=5)  # 旬日
            after = db.conn.execute("SELECT SUM(heat) s FROM factions").fetchone()["s"]
            self.assertGreater(after, before)

    def test_high_shi_cows_factions(self):
        """势高 → heat 加速衰减（慑服敛迹）。"""
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            kv_set_int(db, KV_SHI, 95)
            db.conn.execute("UPDATE factions SET heat=50")
            db.conn.commit()
            before = db.conn.execute("SELECT SUM(heat) s FROM factions").fetchone()["s"]
            theater.faction_moves_tick(db, state, day=15)
            after = db.conn.execute("SELECT SUM(heat) s FROM factions").fetchone()["s"]
            self.assertLess(after, before)

    def test_tax_efficiency_scales_with_shi(self):
        """缺口2b：势高 → 税收到账率高于势低（皇权不振→地方截留）。"""
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            kv_set_int(db, KV_SHI, 5)
            low_guo, _, _ = calc_province_fiscal(state, db)
            kv_set_int(db, KV_SHI, 95)
            high_guo, _, _ = calc_province_fiscal(state, db)
            self.assertGreater(high_guo, low_guo)


class SkimConsequenceTests(unittest.TestCase):
    def test_skim_drops_minxin_and_raises_unrest(self):
        """缺口3：截留办结 → 民心折损 + 经手省份民变压力上升（不再是无后果黑箱）。"""
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            did = _issue(db, state, "着拨饷银十万两补发各镇欠饷")
            region = lifecycle._chain_meta(
                db.conn.execute("SELECT chain FROM turn_directives WHERE id=?", (did,)).fetchone()
            ).get("region_id")
            minxin0 = int(state.metrics["民心"])
            unrest0 = db.conn.execute(
                "SELECT unrest FROM regions WHERE id=?", (region,)).fetchone()["unrest"]
            _force_done(db, state, did, integrity_actual=55)  # 重度截留
            state = db.load_state()
            self.assertLess(int(state.metrics["民心"]), minxin0)
            unrest1 = db.conn.execute(
                "SELECT unrest FROM regions WHERE id=?", (region,)).fetchone()["unrest"]
            self.assertGreater(unrest1, unrest0)

    def test_clean_execution_no_consequence(self):
        """洁净办结(integrity 满)不施加民怨后果。"""
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            did = _issue(db, state, "着拨饷银十万两补发各镇欠饷")
            minxin0 = int(state.metrics["民心"])
            _force_done(db, state, did, integrity_actual=100)
            state = db.load_state()
            self.assertEqual(int(state.metrics["民心"]), minxin0)


class MemorialExpiryUITests(unittest.TestCase):
    """奏疏淹没：御案倒计时(days_to_expire)与日tick淹没判定同源，且到期即出队。"""

    def test_countdown_matches_tick_expiry(self):
        from ming_sim.memorials import create_memorial, desk_payload, expire_deadline_days
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            create_memorial(db, state, day=1, author_name="某", org="都察院",
                            kind="弹章", urgency=3, summary="劾某")
            deadline = expire_deadline_days("弹章", 3)  # 35
            # 倒计时：到案后第 (deadline-5) 日，应剩 5 日淹没
            dp = desk_payload(db, state, day=1 + deadline - 5)
            self.assertEqual(dp["pending"][0]["days_to_expire"], 5)
            # 推进过淹没期：弹章应出队（status=expired），不再 pending
            for _ in range(3):
                timeflow.advance_days(db, state, 30, stop_on_yellow=False)
                timeflow.month_fixed_flows(db, state)
                state.next_period(); db.save_state(state); timeflow.on_month_resolved(db, state)
            row = db.conn.execute(
                "SELECT status FROM memorials WHERE kind='弹章' ORDER BY id LIMIT 1").fetchone()
            self.assertEqual(str(row["status"]), "expired")


if __name__ == "__main__":
    unittest.main()
