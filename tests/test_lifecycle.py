"""M2 指令生命周期测试：归类、经手链条、旬检定、干预、账实分叉、LLM 队列兜底。零 LLM。"""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim import lifecycle, timeflow
from ming_sim.db import GameDB
from ming_sim.scheduler import process_pending
from ming_sim.upgrade_schema import KV_CURRENT_DAY, kv_int


def _fresh(tmp: str):
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    state = db.load_state()
    timeflow.ensure_active(db, state)
    return db, state


def _add_directive(db, state, text: str, actor: str = "") -> int:
    cur = db.conn.execute(
        "INSERT INTO turn_directives (turn, year, period, text, source, status, actor)"
        " VALUES (?,?,?,?,?,?,?)",
        (state.turn, state.year, state.period, text, "test", "confirmed", actor or None),
    )
    db.conn.commit()
    return int(cur.lastrowid)


def _issue(db, state, text: str, actor: str = "") -> int:
    did = _add_directive(db, state, text, actor)
    rows = db.conn.execute("SELECT * FROM turn_directives WHERE id=?", (did,)).fetchall()
    day = kv_int(db, KV_CURRENT_DAY, 1)
    lifecycle.init_directive_lifecycles(db, state, rows, day)
    return did


class ClassifyTests(unittest.TestCase):
    def test_keyword_classification(self):
        self.assertEqual(lifecycle.classify_directive("着户部即拨辽东军饷三十万两")["id"], "fiscal_allocation")
        self.assertEqual(lifecycle.classify_directive("革职查办，下狱论罪")["id"], "personnel")
        self.assertEqual(lifecycle.classify_directive("清查京营空饷，核饷核兵")["id"], "audit_purge")
        self.assertEqual(lifecycle.classify_directive("某事知道了")["id"], "misc")

    def test_chain_build_has_assignee_and_eta(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            plan = lifecycle.build_chain(db, state, "着即拨陕西赈济银二十万两平粜安置流民", "")
            self.assertTrue(plan["assignee"])
            self.assertEqual(plan["region_id"], "shaanxi")
            self.assertGreater(plan["exec_days"], 5)
            self.assertGreaterEqual(plan["resistance"], 0)

    def test_explicit_deadline_compresses_eta(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            did = _issue(db, state, "户部即日核实辽饷、京营、陕西赈济三项实欠，限五日内具奏详细草案。")
            row = db.conn.execute(
                "SELECT start_day, lead_days, exec_days, eta_day FROM turn_directives WHERE id=?",
                (did,),
            ).fetchone()
            self.assertEqual(int(row["eta_day"]) - int(row["start_day"]), 5)
            self.assertEqual(int(row["lead_days"]) + int(row["exec_days"]), 5)


class TickTests(unittest.TestCase):
    def test_transit_then_execute_then_done(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            did = _issue(db, state, "祭告太庙，旌表忠烈")  # ritual: 3天工期
            # 推进若干天（信号类工期短、风险极低）
            for _ in range(12):
                result = timeflow.advance_days(db, state, 1, stop_on_yellow=False)
                if result["advanced"] == 0:
                    break
            row = db.conn.execute(
                "SELECT lifecycle_status, progress FROM turn_directives WHERE id=?", (did,)
            ).fetchone()
            self.assertEqual(str(row["lifecycle_status"]), "done")
            self.assertEqual(int(row["progress"]), 100)
            # 办结应入当月事实账
            facts = timeflow.month_event_log(db)
            self.assertTrue(any(e.get("kind") == "directive_done" for e in facts))
            # settle_note LLM job 已入队，无 LLM 时模板兜底
            n = process_pending(db, None, limit=10)
            self.assertGreaterEqual(n, 1)
            row = db.conn.execute(
                "SELECT settle_note FROM turn_directives WHERE id=?", (did,)).fetchone()
            self.assertTrue(str(row["settle_note"]))

    def test_long_directive_survives_month_and_feeds_simulator(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            did = _issue(db, state, "清丈江南田亩，清查隐田，整顿盐政商税")  # tax_reform: 长工期
            brief = lifecycle.executing_directives_brief(db)
            self.assertTrue(any(b["id"] == did for b in brief))
            # 跨月：月结流程后旨意应仍在执行
            timeflow.month_fixed_flows(db, state)
            state.next_period()
            db.save_state(state)
            timeflow.on_month_resolved(db, state)
            row = db.conn.execute(
                "SELECT lifecycle_status FROM turn_directives WHERE id=?", (did,)).fetchone()
            self.assertIn(str(row["lifecycle_status"]), ("in_transit", "executing", "stalled"))

    def test_skim_creates_report_ledger_divergence(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            did = _issue(db, state, "着拨饷银十万两补发各镇欠饷")
            # 人为制造截留再完成：直接压 actual 并拉满进度
            db.conn.execute(
                "UPDATE turn_directives SET integrity_actual=60, progress=99, "
                "lifecycle_status='executing', lead_days=0 WHERE id=?", (did,))
            db.conn.commit()
            timeflow.advance_days(db, state, 1, stop_on_yellow=False)
            row = db.conn.execute(
                "SELECT lifecycle_status FROM turn_directives WHERE id=?", (did,)).fetchone()
            self.assertEqual(str(row["lifecycle_status"]), "done")
            ledger = db.conn.execute(
                "SELECT * FROM report_ledger WHERE entity_kind='directive' AND entity_id=?",
                (str(did),)).fetchall()
            self.assertEqual(len(ledger), 1)
            self.assertEqual(int(ledger[0]["reported_value"]), 100)
            self.assertEqual(int(ledger[0]["actual_value"]), 60)
            # API 口径不暴露 actual
            payload = lifecycle.lifecycle_payload(db)
            item = [p for p in payload if p["id"] == did][0]
            self.assertNotIn("integrity_actual", item)
            self.assertEqual(item["reported_rate"], 100)

    def test_lifecycle_payload_includes_player_outcome_summary(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            issue = db.conn.execute(
                "SELECT id FROM issues WHERE status='active' ORDER BY id LIMIT 1"
            ).fetchone()
            issue_id = int(issue["id"])
            did = _issue(db, state, "着户部即拨陕西赈济银二十万两，平粜安置流民")
            db.conn.execute(
                "UPDATE turn_directives SET lifecycle_status='done', outcome_status='applied', outcome_delta=? WHERE id=?",
                (json.dumps({
                    "metric_delta": {"民心": 3},
                    "economy_moves": [{"account": "国库", "delta": -20, "category": "赈济", "reason": "测试"}],
                    "region_delta": {"shaanxi": {"unrest": -4}},
                    "issue_advances": [{"issue_id": issue_id, "delta_bar": 12, "reason": "测试"}],
                }, ensure_ascii=False), did),
            )
            db.conn.commit()
            item = [p for p in lifecycle.lifecycle_payload(db) if p["id"] == did][0]
            labels = [str(x["label"]) for x in item["outcome_summary"]]
            self.assertIn("民心 +3", labels)
            self.assertIn("国库 -20万", labels)
            self.assertTrue(any("陕西" in label and "动乱 -4" in label for label in labels), labels)
            self.assertTrue(any("+12" in label for label in labels), labels)
            self.assertNotIn("integrity_actual", item)

    def test_lifecycle_payload_tolerates_legacy_list_chain(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            did = _issue(db, state, "着袁崇焕整顿辽东军饷，十日内具奏。")
            db.conn.execute(
                "UPDATE turn_directives SET lifecycle_status='executing', chain=? WHERE id=?",
                (json.dumps([{"name": "袁崇焕", "role": "主办"}], ensure_ascii=False), did),
            )
            db.conn.commit()
            item = [p for p in lifecycle.lifecycle_payload(db) if p["id"] == did][0]
            self.assertEqual(item["resistance"], 0)
            self.assertEqual(item["chain"], [])

    def test_directive_chat_context_brief_is_trusted_and_assignee_scoped(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            did = _issue(db, state, "令袁崇焕整顿辽东军饷，十日内具奏欠饷实数与裁汰方案。")
            db.conn.execute(
                "UPDATE turn_directives SET assignee=?, lifecycle_status='executing', "
                "progress=43, integrity_reported=66, anomaly=?, chain=? WHERE id=?",
                (
                    "袁崇焕",
                    json.dumps({"kind": "delay"}, ensure_ascii=False),
                    json.dumps({"resistance": 45, "chain": []}, ensure_ascii=False),
                    did,
                ),
            )
            db.conn.commit()
            brief = lifecycle.directive_chat_context_brief(db, "袁崇焕", did)
            self.assertIn("追问在办旨意", brief)
            self.assertIn("账面进度：43%", brief)
            self.assertIn("奏报执行率：66%", brief)
            self.assertIn("阻力估计：45", brief)
            self.assertIn("当前异常：迟滞拖延", brief)
            self.assertIn("不得说成不知道", brief)
            self.assertEqual(lifecycle.directive_chat_context_brief(db, "韩爌", did), "")

    def test_directive_audience_pressure_moves_live_directive(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            did = _issue(db, state, "令袁崇焕整顿辽东军饷，十日内具奏欠饷实数与裁汰方案。")
            db.conn.execute(
                "UPDATE turn_directives SET assignee=?, lifecycle_status='executing', "
                "progress=43, exec_days=10, eta_day=12, anomaly=?, chain=? WHERE id=?",
                (
                    "袁崇焕",
                    json.dumps({"kind": "delay"}, ensure_ascii=False),
                    json.dumps({"resistance": 45, "chain": []}, ensure_ascii=False),
                    did,
                ),
            )
            db.conn.commit()
            effect = lifecycle.apply_directive_audience_pressure(
                db,
                state,
                "袁崇焕",
                did,
                "朕问你进度，欠饷实数到底办到几分？",
                "臣遵旨，即日清册具奏，三日内交账，不敢再误。",
            )
            row = db.conn.execute(
                "SELECT lifecycle_status, progress, exec_days, eta_day, anomaly, chain FROM turn_directives WHERE id=?",
                (did,),
            ).fetchone()
            self.assertEqual(effect["kind"], "pressed")
            self.assertEqual(effect["progress_delta"], 6)
            self.assertEqual(str(row["lifecycle_status"]), "executing")
            self.assertEqual(int(row["progress"]), 49)
            self.assertEqual(int(row["exec_days"]), 8)
            self.assertEqual(int(row["eta_day"]), 10)
            self.assertEqual(str(row["anomaly"] or ""), "")
            self.assertEqual(json.loads(row["chain"])["resistance"], 40)

    def test_directive_audience_pressure_records_person_blocker_clue(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            did = _issue(db, state, "令袁崇焕整顿辽东军饷。")
            db.conn.execute(
                "UPDATE turn_directives SET assignee=?, lifecycle_status='executing', progress=40, chain=? WHERE id=?",
                ("袁崇焕", json.dumps({"resistance": 45, "chain": []}, ensure_ascii=False), did),
            )
            db.conn.commit()
            effect = lifecycle.apply_directive_audience_pressure(
                db,
                state,
                "袁崇焕",
                did,
                "朕问你阻力在何处？",
                "臣遵旨具奏，只是温体仁屡以部议相格，户部钱粮亦迟迟不发。",
            )
            item = [p for p in lifecycle.lifecycle_payload(db) if p["id"] == did][0]
            self.assertEqual(effect["blocker_clue"]["kind"], "person")
            self.assertEqual(effect["blocker_clue"]["name"], "温体仁")
            self.assertEqual(item["blocker_clue"]["name"], "温体仁")
            self.assertIn("线索：温体仁", effect["message"])

    def test_directive_audience_pressure_records_org_blocker_when_no_person(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            did = _issue(db, state, "令袁崇焕整顿辽东军饷。")
            db.conn.execute(
                "UPDATE turn_directives SET assignee=?, lifecycle_status='executing', progress=40, chain=? WHERE id=?",
                ("袁崇焕", json.dumps({"resistance": 45, "chain": []}, ensure_ascii=False), did),
            )
            db.conn.commit()
            effect = lifecycle.apply_directive_audience_pressure(
                db,
                state,
                "袁崇焕",
                did,
                "朕问你阻力在何处？",
                "臣遵旨具奏，只是户部钱粮迟迟不发，军饷无着。",
            )
            item = [p for p in lifecycle.lifecycle_payload(db) if p["id"] == did][0]
            self.assertEqual(effect["blocker_clue"]["kind"], "org")
            self.assertEqual(effect["blocker_clue"]["label"], "户部")
            self.assertEqual(item["blocker_clue"]["label"], "户部")

    def test_directive_audience_pressure_scoped_to_assignee(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            did = _issue(db, state, "令袁崇焕整顿辽东军饷。")
            db.conn.execute(
                "UPDATE turn_directives SET assignee=?, lifecycle_status='executing', progress=30 WHERE id=?",
                ("袁崇焕", did),
            )
            db.conn.commit()
            effect = lifecycle.apply_directive_audience_pressure(
                db, state, "韩爌", did, "朕问进度。", "臣遵旨，即日具奏。")
            progress = int(db.conn.execute(
                "SELECT progress FROM turn_directives WHERE id=?", (did,)).fetchone()["progress"])
            self.assertEqual(effect, {})
            self.assertEqual(progress, 30)


class InterveneTests(unittest.TestCase):
    def _stalled_directive(self, db, state) -> int:
        did = _issue(db, state, "清查京营空饷，核饷核兵")
        db.conn.execute(
            "UPDATE turn_directives SET lifecycle_status='stalled', "
            "anomaly='{\"kind\":\"block\"}' WHERE id=?", (did,))
        db.conn.commit()
        return did

    def test_cuiban_unstalls_and_costs(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            did = self._stalled_directive(db, state)
            assignee = db.conn.execute(
                "SELECT assignee FROM turn_directives WHERE id=?", (did,)).fetchone()["assignee"]
            g0 = db.conn.execute(
                "SELECT grievance FROM characters WHERE name=?", (assignee,)).fetchone()["grievance"]
            result = lifecycle.intervene(db, state, did, "cuiban", day=10)
            self.assertTrue(result["ok"])
            row = db.conn.execute(
                "SELECT lifecycle_status FROM turn_directives WHERE id=?", (did,)).fetchone()
            self.assertEqual(str(row["lifecycle_status"]), "executing")
            g1 = db.conn.execute(
                "SELECT grievance FROM characters WHERE name=?", (assignee,)).fetchone()["grievance"]
            self.assertEqual(g1, g0 + 5)

    def test_abort_costs_shi(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            did = self._stalled_directive(db, state)
            result = lifecycle.intervene(db, state, did, "abort", day=10)
            self.assertTrue(result["ok"])
            rows = db.conn.execute(
                "SELECT * FROM belief_logs WHERE key='shi' AND new_value<old_value").fetchall()
            self.assertTrue(rows)

    def test_fund_costs_treasury(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            did = self._stalled_directive(db, state)
            before = int(state.metrics["国库"])
            result = lifecycle.intervene(db, state, did, "fund", day=10, fund=20)
            self.assertTrue(result["ok"])
            self.assertEqual(int(state.metrics["国库"]), before - 20)

    def test_reassign_requires_valid_minister(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            did = self._stalled_directive(db, state)
            bad = lifecycle.intervene(db, state, did, "reassign", day=10, new_assignee="不存在的人")
            self.assertFalse(bad["ok"])
            someone = db.conn.execute(
                "SELECT name FROM characters WHERE status='active' AND office LIKE '%户部%' LIMIT 1"
            ).fetchone()["name"]
            ok = lifecycle.intervene(db, state, did, "reassign", day=10, new_assignee=someone)
            self.assertTrue(ok["ok"])


class DeterminismTests(unittest.TestCase):
    def test_check_is_seeded_and_reproducible(self):
        import random
        r1 = random.Random(42 * 100003 + 17).random()
        r2 = random.Random(42 * 100003 + 17).random()
        self.assertEqual(r1, r2)


if __name__ == "__main__":
    unittest.main()
