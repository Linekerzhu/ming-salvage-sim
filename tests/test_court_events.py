"""抉择事件（CK3 化 P2）测试：触发→待决→落子→后果落库→冷却。零 LLM。"""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim import court, court_events, memorials, timeflow
from ming_sim.db import GameDB
from ming_sim.upgrade_schema import KV_SHI, kv_int


def _fresh(tmp: str):
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    state = db.load_state()
    day = timeflow.ensure_active(db, state)
    return db, state, day


def _two_ming(db):
    return [r["name"] for r in db.conn.execute(
        "SELECT name FROM characters WHERE status='active' AND power_id='ming' "
        "AND office_type!='后宫' LIMIT 2")]


def _erupt(db, a, b, opinion=-75):
    """制造一对深仇 + 一封 a 劾 b 的弹章（宿敌互讦的触发前提：事已上台面）。"""
    court._set_opinion(db, a, b, opinion, "夙仇", 0)
    court._set_opinion(db, b, a, opinion, "夙仇", 0)
    from ming_sim.memorials import create_memorial
    create_memorial(db, None, day=1, author_name=a, org="都察院", kind="弹章", urgency=3,
                    summary=f"{a}劾{b}", full_text="劾其植党。", ref_kind="character", ref_id=b)
    db.conn.commit()


def _due_agreement(db, state, minister, title="难差自证"):
    goal_id = db.create_conversation_goal(
        state,
        minister_name=minister,
        action_kind="court_commitment",
        title=title,
        target_text=f"{minister}须就「{title}」回奏可验结果。",
        threshold=70,
        score=100,
        status="waiting_conditions",
        condition_status="pending",
        conditions=[{"description": "限期回奏可验证据", "status": "pending"}],
        expires_turn=int(state.turn) + 2,
        last_delta={"source": f"test:{minister}:{title}"},
    )
    agreement_id = db.create_negotiation_agreement(
        state,
        minister_name=minister,
        topic=title,
        action_kind="court_commitment",
        status="pending",
        stance_id=0,
        handshake_status="sealed",
        psychological_score=100,
        threshold=70,
        verbal_only=False,
        core_topic=title,
        target_text=f"{minister}须就「{title}」回奏可验结果。",
        promise_type="通用奏对承诺",
        stakes="制度名分",
        due_turn=int(state.turn),
        conditions="限期回奏可验证据",
        summary=f"{minister}领下{title}",
        tasks=["限期回奏可验证据"],
        goal_id=goal_id,
    )
    db.bind_conversation_goal_agreement(goal_id, agreement_id)
    return agreement_id, goal_id


def _tax_legacy(db, state, stem="辽饷", minxin=-9, duration_months=-1):
    return db.insert_legacy(
        state,
        name=f"苛税余波：{stem}",
        modifiers={"民心": minxin},
        narrative_hint=f"旨意加重{stem}，钱粮见长，民心恢复受压。",
        duration_months=duration_months,
        legacy_key=f"directive_tax:999:{stem}",
    )


class TriggerTests(unittest.TestCase):
    def test_deep_rivalry_triggers_feud_decision(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            a, b = _two_ming(db)
            _erupt(db, a, b)
            payload = court_events.evaluate_decisions(db, state, day)
            self.assertIsNotNone(payload, "深仇当触发宿敌互讦抉择")
            self.assertEqual(payload["id"], "rival_feud")
            self.assertGreaterEqual(len(payload["choices"]), 3)

    def test_payload_includes_structured_choice_effects(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            a, b = _two_ming(db)
            _erupt(db, a, b)
            payload = court_events.evaluate_decisions(db, state, day)
            both = next(c for c in payload["choices"] if c["key"] == "both")
            labels = [e["label"] for e in both["effects"]]
            tones = {e["label"]: e["tone"] for e in both["effects"]}
            self.assertIn("君威 +3", labels)
            self.assertIn("任事 -5", labels)
            self.assertEqual(tones["君威 +3"], "good")
            self.assertEqual(tones["任事 -5"], "bad")

    def test_one_pending_at_a_time(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            a, b = _two_ming(db)
            _erupt(db, a, b)
            first = court_events.evaluate_decisions(db, state, day)
            self.assertIsNotNone(first)
            second = court_events.evaluate_decisions(db, state, day)  # 已有待决
            self.assertIsNone(second, "一次至多一道待决")

    def test_high_grievance_petition_triggers_dilemma(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            petitioner, rival = _two_ming(db)
            db.conn.execute(
                "UPDATE characters SET emp_trust=24, grievance=84, faction='东林' WHERE name=?",
                (petitioner,),
            )
            db.conn.execute(
                "UPDATE characters SET faction='阉党' WHERE name=?",
                (rival,),
            )
            court._set_opinion(db, petitioner, rival, -78, "夺功旧怨", day)
            court._set_opinion(db, rival, petitioner, -72, "夺功旧怨", day)
            db.conn.commit()

            payload = court_events.evaluate_decisions(db, state, day)

            self.assertIsNotNone(payload)
            self.assertEqual(payload["id"], "imperial_petition")
            self.assertIn(petitioner, str(payload["title"]))
            keys = {str(ch["key"]) for ch in payload["choices"]}
            self.assertEqual(keys, {"protect", "demand_service", "co_work", "shelve"})
            protect = next(ch for ch in payload["choices"] if ch["key"] == "protect")
            labels = [str(e["label"]) for e in protect["effects"]]
            self.assertIn(f"{petitioner}信任 +10", labels)
            self.assertIn("东林满意 +4", labels)
            self.assertIn("阉党热度 +5", labels)
            demand = next(ch for ch in payload["choices"] if ch["key"] == "demand_service")
            self.assertIn(f"履约账本：{petitioner}", [str(e["label"]) for e in demand["effects"]])

    def test_overdue_agreement_triggers_accountability_decision(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            minister, _ = _two_ming(db)
            agreement_id, _goal_id = _due_agreement(db, state, minister, "共办消怨")

            payload = court_events.evaluate_decisions(db, state, day)

            self.assertIsNotNone(payload)
            self.assertEqual(payload["id"], "overdue_obligation")
            self.assertIn(minister, str(payload["title"]))
            keys = {str(ch["key"]) for ch in payload["choices"]}
            self.assertEqual(keys, {"press", "grant_time", "punish"})
            press = next(ch for ch in payload["choices"] if ch["key"] == "press")
            self.assertIn("履约展限 1月", [str(e["label"]) for e in press["effects"]])
            pending = court_events.get_pending(db) or {}
            self.assertEqual(str(pending.get("cooldown_key")), f"overdue_obligation:{agreement_id}")

    def test_overdue_favored_official_triggers_favor_debt_choice(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            minister, _ = _two_ming(db)
            favor = memorials.back_official(db, state, minister, "comfort", day=day)
            self.assertTrue(favor["ok"], favor)
            agreement_id, _goal_id = _due_agreement(db, state, minister, "还恩难差")

            payload = court_events.evaluate_decisions(db, state, day)

            self.assertIsNotNone(payload)
            self.assertEqual(payload["id"], "overdue_obligation")
            self.assertIn("忘恩负约", str(payload["title"]))
            self.assertIn("旧恩", str(payload["narrative"]))
            keys = {str(ch["key"]) for ch in payload["choices"]}
            self.assertEqual(keys, {"call_favor", "press", "grant_time", "punish"})
            call = next(ch for ch in payload["choices"] if ch["key"] == "call_favor")
            labels = [str(e["label"]) for e in call["effects"]]
            self.assertIn("君威 +2", labels)
            self.assertIn("任事 +1", labels)
            self.assertIn(f"{minister}怨望 +6", labels)
            self.assertIn("履约展限 1月", labels)
            pending = court_events.get_pending(db) or {}
            self.assertEqual(str(pending.get("cooldown_key")), f"overdue_obligation:{agreement_id}")

    def test_tax_policy_legacy_triggers_aftershock_decision(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            legacy_id = _tax_legacy(db, state)

            payload = court_events.evaluate_decisions(db, state, day)

            self.assertIsNotNone(payload)
            self.assertEqual(payload["id"], "policy_aftershock")
            self.assertIn("苛税余波：辽饷", str(payload["title"]))
            keys = {str(ch["key"]) for ch in payload["choices"]}
            self.assertEqual(keys, {"keep_collecting", "relieve_now", "audit_middlemen"})
            relieve = next(ch for ch in payload["choices"] if ch["key"] == "relieve_now")
            self.assertIn("旧政缓和：辽饷", [str(e["label"]) for e in relieve["effects"]])
            pending = court_events.get_pending(db) or {}
            self.assertEqual(str(pending.get("cooldown_key")), f"policy_aftershock:{legacy_id}")


class ResolveTests(unittest.TestCase):
    def test_resolve_applies_effect_and_clears(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            a, b = _two_ming(db)
            _erupt(db, a, b)
            court_events.evaluate_decisions(db, state, day)
            shi_before = kv_int(db, KV_SHI, 55)
            res = court_events.resolve_decision(db, state, "both", day=day)
            self.assertTrue(res["ok"])
            self.assertIn("君威", res["effect"])  # both 选项含 shi+3
            labels = [e["label"] for e in res["effects"]]
            self.assertIn("君威 +3", labels)
            self.assertIn("任事 -5", labels)
            self.assertGreater(kv_int(db, KV_SHI, 55), shi_before, "各打五十大板立威，君威应升")
            self.assertIsNone(court_events.get_pending(db), "落子后待决应清空")

    def test_cooldown_blocks_immediate_refire(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            a, b = _two_ming(db)
            _erupt(db, a, b)
            court_events.evaluate_decisions(db, state, day)
            court_events.resolve_decision(db, state, "ignore", day=day)
            # 同一触发仍在（opinion 仍深），但冷却应拦住即刻重弹
            again = court_events.evaluate_decisions(db, state, day + 1)
            self.assertIsNone(again, "同类抉择 60 日内不应重复弹出")

    def test_resolve_without_pending_is_graceful(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            res = court_events.resolve_decision(db, state, "both", day=day)
            self.assertFalse(res["ok"])

    def test_petition_protection_changes_people_and_faction_heat(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            petitioner, rival = _two_ming(db)
            db.conn.execute(
                "UPDATE characters SET emp_trust=24, grievance=84, faction='东林' WHERE name=?",
                (petitioner,),
            )
            db.conn.execute(
                "UPDATE characters SET emp_trust=55, grievance=20, faction='阉党' WHERE name=?",
                (rival,),
            )
            court._set_opinion(db, petitioner, rival, -78, "夺功旧怨", day)
            court._set_opinion(db, rival, petitioner, -72, "夺功旧怨", day)
            db.conn.commit()
            heat_before = int(db.conn.execute(
                "SELECT heat FROM factions WHERE name='阉党'"
            ).fetchone()["heat"])

            court_events.evaluate_decisions(db, state, day)
            res = court_events.resolve_decision(db, state, "protect", day=day)

            self.assertTrue(res["ok"], res)
            prow = db.conn.execute(
                "SELECT emp_trust, grievance FROM characters WHERE name=?", (petitioner,)
            ).fetchone()
            rrow = db.conn.execute(
                "SELECT emp_trust, grievance FROM characters WHERE name=?", (rival,)
            ).fetchone()
            self.assertEqual(int(prow["emp_trust"]), 34)
            self.assertEqual(int(prow["grievance"]), 72)
            self.assertEqual(int(rrow["emp_trust"]), 52)
            self.assertEqual(int(rrow["grievance"]), 25)
            heat_after = int(db.conn.execute(
                "SELECT heat FROM factions WHERE name='阉党'"
            ).fetchone()["heat"])
            self.assertEqual(heat_after, heat_before + 5)
            labels = [str(e["label"]) for e in res["effects"]]
            self.assertIn("阉党热度 +5", labels)
            self.assertIsNone(court_events.get_pending(db))

    def test_petition_demand_service_creates_followup_obligation(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            start_turn = int(state.turn)
            petitioner, rival = _two_ming(db)
            db.conn.execute(
                "UPDATE characters SET emp_trust=24, grievance=84, faction='东林' WHERE name=?",
                (petitioner,),
            )
            db.conn.execute(
                "UPDATE characters SET faction='阉党' WHERE name=?",
                (rival,),
            )
            court._set_opinion(db, petitioner, rival, -78, "夺功旧怨", day)
            court._set_opinion(db, rival, petitioner, -72, "夺功旧怨", day)
            db.conn.commit()

            court_events.evaluate_decisions(db, state, day)
            res = court_events.resolve_decision(db, state, "demand_service", day=day)

            self.assertTrue(res["ok"], res)
            self.assertIn(f"履约账本：{petitioner}", [str(e["label"]) for e in res["effects"]])
            self.assertIn(f"{petitioner}负约待办", str(res["effect"]))
            goals = db.list_conversation_goals(
                minister_name=petitioner,
                statuses=["waiting_conditions"],
            )
            self.assertEqual(len(goals), 1)
            self.assertIn("难差自证", str(goals[0]["title"]))
            self.assertGreater(int(goals[0]["agreement_id"]), 0)
            agreements = db.list_negotiation_agreements(
                minister_name=petitioner,
                action_kind="court_commitment",
                status="pending",
            )
            self.assertEqual(len(agreements), 1)
            self.assertEqual(int(agreements[0]["due_turn"]), start_turn + 3)
            self.assertTrue(any("三日内回奏一件可验难差" in str(t["description"]) for t in agreements[0]["tasks"]))

    def test_petition_co_work_binds_both_sides_to_tasks(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            petitioner, rival = _two_ming(db)
            db.conn.execute(
                "UPDATE characters SET emp_trust=24, grievance=84, faction='东林' WHERE name=?",
                (petitioner,),
            )
            db.conn.execute(
                "UPDATE characters SET emp_trust=55, grievance=20, faction='阉党' WHERE name=?",
                (rival,),
            )
            court._set_opinion(db, petitioner, rival, -78, "夺功旧怨", day)
            court._set_opinion(db, rival, petitioner, -72, "夺功旧怨", day)
            db.conn.commit()

            court_events.evaluate_decisions(db, state, day)
            res = court_events.resolve_decision(db, state, "co_work", day=day)

            self.assertTrue(res["ok"], res)
            labels = [str(e["label"]) for e in res["effects"]]
            self.assertIn(f"履约账本：{petitioner}", labels)
            self.assertIn(f"履约账本：{rival}", labels)
            for name, other in ((petitioner, rival), (rival, petitioner)):
                goals = db.list_conversation_goals(
                    minister_name=name,
                    statuses=["waiting_conditions"],
                )
                self.assertEqual(len(goals), 1)
                self.assertIn(other, str(goals[0]["target_text"]))
                agreements = db.list_negotiation_agreements(
                    minister_name=name,
                    action_kind="court_commitment",
                    status="pending",
                )
                self.assertEqual(len(agreements), 1)
                self.assertTrue(any(other in str(t["description"]) for t in agreements[0]["tasks"]))

    def test_overdue_press_extends_agreement_deadline(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            minister, _ = _two_ming(db)
            agreement_id, goal_id = _due_agreement(db, state, minister, "难差自证")

            court_events.evaluate_decisions(db, state, day)
            res = court_events.resolve_decision(db, state, "press", day=day)

            self.assertTrue(res["ok"], res)
            self.assertIn("履约展限 1月", [str(e["label"]) for e in res["effects"]])
            row = db.conn.execute(
                "SELECT status, condition_status, target_status, due_turn FROM negotiation_agreements WHERE id=?",
                (agreement_id,),
            ).fetchone()
            self.assertEqual(str(row["status"]), "pending")
            self.assertEqual(str(row["target_status"]), "pending_conditions")
            self.assertEqual(int(row["due_turn"]), int(state.turn) + 1)
            goal = db.get_conversation_goal(goal_id) or {}
            self.assertEqual(str(goal.get("status")), "waiting_conditions")
            self.assertEqual(int(goal.get("expires_turn") or 0), int(state.turn) + 3)

    def test_overdue_punish_fails_agreement_and_goal(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            minister, _ = _two_ming(db)
            agreement_id, goal_id = _due_agreement(db, state, minister, "共办消怨")

            court_events.evaluate_decisions(db, state, day)
            res = court_events.resolve_decision(db, state, "punish", day=day)

            self.assertTrue(res["ok"], res)
            self.assertIn("履约追责", [str(e["label"]) for e in res["effects"]])
            row = db.conn.execute(
                "SELECT status, condition_status, target_status, resolved_turn FROM negotiation_agreements WHERE id=?",
                (agreement_id,),
            ).fetchone()
            self.assertEqual(str(row["status"]), "failed")
            self.assertEqual(str(row["condition_status"]), "failed")
            self.assertEqual(str(row["target_status"]), "failed")
            self.assertEqual(int(row["resolved_turn"]), int(state.turn))
            task = db.conn.execute(
                "SELECT status, evidence FROM negotiation_tasks WHERE agreement_id=?",
                (agreement_id,),
            ).fetchone()
            self.assertEqual(str(task["status"]), "failed")
            self.assertIn("按失期问责", str(task["evidence"]))
            goal = db.get_conversation_goal(goal_id) or {}
            self.assertEqual(str(goal.get("status")), "expired")

    def test_overdue_call_favor_extends_with_favor_evidence(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            minister, _ = _two_ming(db)
            memorials.back_official(db, state, minister, "comfort", day=day)
            agreement_id, goal_id = _due_agreement(db, state, minister, "还恩难差")
            before = db.conn.execute(
                "SELECT emp_trust, grievance FROM characters WHERE name=?",
                (minister,),
            ).fetchone()

            court_events.evaluate_decisions(db, state, day)
            res = court_events.resolve_decision(db, state, "call_favor", day=day)

            self.assertTrue(res["ok"], res)
            labels = [str(e["label"]) for e in res["effects"]]
            self.assertIn("履约展限 1月", labels)
            row = db.conn.execute(
                "SELECT status, due_turn, auto_review_json FROM negotiation_agreements WHERE id=?",
                (agreement_id,),
            ).fetchone()
            self.assertEqual(str(row["status"]), "pending")
            self.assertEqual(int(row["due_turn"]), int(state.turn) + 1)
            self.assertIn("不得装作两清", str(row["auto_review_json"]))
            after = db.conn.execute(
                "SELECT emp_trust, grievance FROM characters WHERE name=?",
                (minister,),
            ).fetchone()
            self.assertEqual(int(after["emp_trust"]), max(0, int(before["emp_trust"]) - 1))
            self.assertEqual(int(after["grievance"]), int(before["grievance"]) + 6)
            goal = db.get_conversation_goal(goal_id) or {}
            self.assertEqual(str(goal.get("status")), "waiting_conditions")
            self.assertIn("不得装作两清", str(goal.get("last_delta_json")))
            self.assertIsNone(court_events.get_pending(db))

    def test_policy_aftershock_relief_softens_legacy(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            legacy_id = _tax_legacy(db, state, minxin=-9)
            treasury_before = int(state.metrics.get("国库", 0))

            court_events.evaluate_decisions(db, state, day)
            res = court_events.resolve_decision(db, state, "relieve_now", day=day)

            self.assertTrue(res["ok"], res)
            self.assertIn("旧政缓和：辽饷", [str(e["label"]) for e in res["effects"]])
            row = db.conn.execute("SELECT modifiers, narrative_hint FROM legacies WHERE id=?", (legacy_id,)).fetchone()
            modifiers = json.loads(str(row["modifiers"] or "{}"))
            self.assertEqual(int(modifiers["民心"]), -5)
            self.assertIn("蠲缓", str(row["narrative_hint"]))
            self.assertEqual(int(state.metrics.get("国库", 0)), treasury_before - 8)
            self.assertIsNone(court_events.get_pending(db))

    def test_policy_aftershock_audit_creates_followup_obligation(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            legacy_id = _tax_legacy(db, state, minxin=-9)

            court_events.evaluate_decisions(db, state, day)
            pending = court_events.get_pending(db) or {}
            actor = str((pending.get("ctx") or {}).get("actor") or "")
            self.assertTrue(actor)
            res = court_events.resolve_decision(db, state, "audit_middlemen", day=day)

            self.assertTrue(res["ok"], res)
            labels = [str(e["label"]) for e in res["effects"]]
            self.assertIn("旧政缓和：辽饷", labels)
            self.assertIn(f"履约账本：{actor}", labels)
            row = db.conn.execute("SELECT modifiers FROM legacies WHERE id=?", (legacy_id,)).fetchone()
            modifiers = json.loads(str(row["modifiers"] or "{}"))
            self.assertEqual(int(modifiers["民心"]), -7)
            goals = db.list_conversation_goals(
                minister_name=actor,
                statuses=["waiting_conditions"],
            )
            self.assertEqual(len(goals), 1)
            self.assertIn("清查辽饷加派侵吞", str(goals[0]["title"]))

    def test_overdue_cooldown_is_scoped_per_agreement(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            first, second = _two_ming(db)
            first_agreement, _ = _due_agreement(db, state, first, "难差自证")
            second_agreement, _ = _due_agreement(db, state, second, "共办消怨")

            first_payload = court_events.evaluate_decisions(db, state, day)
            self.assertIsNotNone(first_payload)
            first_pending = court_events.get_pending(db) or {}
            first_cooldown = str(first_pending.get("cooldown_key") or "")
            court_events.resolve_decision(db, state, "punish", day=day)
            second_payload = court_events.evaluate_decisions(db, state, day)

            self.assertIsNotNone(second_payload)
            pending = court_events.get_pending(db) or {}
            self.assertIn(str(pending.get("cooldown_key")), {
                f"overdue_obligation:{first_agreement}",
                f"overdue_obligation:{second_agreement}",
            })
            self.assertNotEqual(str(pending.get("cooldown_key")), first_cooldown)


class IntegrationTests(unittest.TestCase):
    def test_decision_red_event_halts_advance(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            a, b = _two_ming(db)
            _erupt(db, a, b, opinion=-80)
            result = timeflow.advance_days(db, state, 12, stop_on_yellow=False)
            evs = [e for r in result["reports"] for e in r["events"]]
            self.assertTrue(any(e["kind"] == "decision" for e in evs), "抉择应作为红事件出现")
            self.assertEqual(result["stopped_by"], "red", "待决抉择应令推进停下待裁断")
            self.assertIsNotNone(court_events.pending_payload(db))


if __name__ == "__main__":
    unittest.main()
