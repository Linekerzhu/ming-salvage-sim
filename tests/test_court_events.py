"""抉择事件（CK3 化 P2）测试：触发→待决→落子→后果落库→冷却。零 LLM。"""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim import court, court_events, memorials, timeflow
from ming_sim.db import GameDB
from ming_sim.playstyle import briefing_payload, favor_chat_context_brief, record_decision_testimony
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


def _pending_secret_order(db, state, minister, title="密查内库侵冒"):
    order_id = db.create_secret_order(
        state,
        minister_name=minister,
        title=title,
        content="暗查内库票拟、内臣传递与户部兑银之间是否有人侵冒。",
        tags=["内库", "阉党", "钱粮"],
        importance=5,
        deadline_months=1,
    )
    ok = db.submit_secret_order_for_review(
        order_id,
        "已得一册兑银底账与两名小吏口供，但牵连尚未尽明。",
        state.year,
        state.period,
    )
    assert ok
    db.conn.execute(
        "UPDATE secret_orders SET sim_note=? WHERE id=?",
        ("〔崇祯元年1月〕风声略泄，内库有人连夜焚毁旧簿。", order_id),
    )
    db.conn.commit()
    return order_id


def _private_distress_case(db, state):
    actor, target = _two_ming(db)
    db.conn.execute(
        """
        UPDATE characters
        SET emp_trust=44, grievance=55, faction='东林'
        WHERE name=?
        """,
        (actor,),
    )
    db.conn.execute(
        "UPDATE characters SET emp_trust=50, grievance=30, faction='东林' WHERE name=?",
        (target,),
    )
    db.conn.execute(
        """
        INSERT OR REPLACE INTO npc_agendas (name, kind, title, target_name, intensity, status)
        VALUES (?, 'protect', '护持本党门生故旧、要害位置安插自己人', ?, 76, 'active')
        """,
        (actor, target),
    )
    court._set_opinion(db, actor, target, 64, "门生故旧", 1)
    court._set_opinion(db, target, actor, 48, "恩主提携", 1)
    db.conn.commit()
    return actor, target


def _blocked_goal_case(db, state):
    minister, _other = _two_ming(db)
    db.conn.execute(
        "UPDATE characters SET emp_trust=42, grievance=62 WHERE name=?",
        (minister,),
    )
    goal_id = db.create_conversation_goal(
        state,
        minister_name=minister,
        action_kind="court_commitment",
        title=f"举主连坐：{minister}保新人",
        target_text=f"{minister}须为荐人共办试差并交代担保边界。",
        threshold=70,
        score=100,
        status="blocked",
        condition_status="blocked",
        conditions=[{"description": "两月内回奏荐人试差证据。", "status": "failed"}],
        blockers=["举主担保已逾期未复命，须召对追问责任与证据。"],
        expires_turn=int(state.turn),
        last_delta={
            "source": f"patronage_accountability:{minister}:新人:joint_trial:sponsor",
            "monthly_pressure": {
                "kind": "overdue",
                "label": "举主担保",
                "turn": int(state.turn),
                "age": 2,
                "trust_delta": -2,
                "grievance_delta": 5,
            },
        },
    )
    return minister, goal_id


def _blocked_bargain_goal_case(db, state):
    minister, _other = _two_ming(db)
    db.conn.execute(
        "UPDATE characters SET emp_trust=45, grievance=58, faction='东林' WHERE name=?",
        (minister,),
    )
    goal_id = db.create_conversation_goal(
        state,
        minister_name=minister,
        action_kind="audience_bargain",
        title=f"旧账索证：{minister}",
        target_text=f"{minister}须围绕「条件待证：{minister}」补齐人证账册与兑现进度。",
        threshold=68,
        score=100,
        status="blocked",
        condition_status="blocked",
        conditions=[{"description": "补齐人证账册与兑现进度。", "status": "failed"}],
        blockers=["前番御前旧账迟迟未补证，已成政敌把柄。"],
        expires_turn=int(state.turn),
        last_delta={
            "source": "audience_bargain_commitment",
            "attitude": "press",
            "context_title": f"条件待证：{minister}",
            "monthly_pressure": {
                "kind": "overdue",
                "label": "御前旧账",
                "turn": int(state.turn),
                "age": 3,
                "trust_delta": -2,
                "grievance_delta": 6,
            },
        },
    )
    return minister, goal_id


def _patronage_case(db):
    sponsor, candidate = _two_ming(db)
    db.conn.execute(
        """
        UPDATE characters
        SET emp_trust=55, grievance=18, faction='东林'
        WHERE name=?
        """,
        (sponsor,),
    )
    db.conn.execute(
        """
        UPDATE characters
        SET emp_trust=45, grievance=26, faction='中立', ability=63, integrity=44,
            summary=?
        WHERE name=?
        """,
        (
            f"由地方举荐入京。举荐来源：{sponsor}；短板：朝中根基浅；风险：仍受举主关系牵引。",
            candidate,
        ),
    )
    db.conn.execute("DELETE FROM npc_agendas WHERE name IN (?, ?)", (sponsor, candidate))
    court._set_opinion(db, sponsor, candidate, 38, "举荐入朝", 1)
    court._set_opinion(db, candidate, sponsor, 42, "举主恩义", 1)
    db.conn.commit()
    return sponsor, candidate


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

    def test_rival_feud_testimony_unlocks_inquest_choice(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            a, b = _two_ming(db)
            _erupt(db, a, b)
            payload = court_events.evaluate_decisions(db, state, day)
            self.assertNotIn("inquest", {str(c["key"]) for c in payload["choices"]})

            record_decision_testimony(
                db,
                state,
                a,
                "rival_feud",
                f"朕未裁断前，先问你弹劾{b}有何证据？",
                "臣有账册与人证，愿限一月查验，若虚言愿担责。",
                target=b,
            )

            updated = court_events.pending_payload(db)
            inquest = next(c for c in updated["choices"] if c["key"] == "inquest")
            labels = [str(e["label"]) for e in inquest["effects"]]
            self.assertIn("君威 +2", labels)
            self.assertIn("任事 +1", labels)
            self.assertIn(f"履约账本：{a}", labels)
            self.assertIn("已问1人", str(inquest["hint"]))

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

    def test_mature_favor_can_be_called_in_as_service_debt(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            minister, _ = _two_ming(db)
            favor = memorials.back_official(db, state, minister, "comfort", day=day)
            self.assertTrue(favor["ok"], favor)
            db.conn.execute(
                "UPDATE characters SET emp_trust=66, grievance=32, faction='东林' WHERE name=?",
                (minister,),
            )
            state.turn += 2
            db.save_state(state)

            payload = court_events.evaluate_decisions(db, state, day + 60)

            self.assertIsNotNone(payload)
            self.assertEqual(payload["id"], "favor_debt_pressure")
            self.assertIn("旧恩求偿", str(payload["title"]))
            self.assertIn("旧恩", str(payload["narrative"]))
            keys = {str(ch["key"]) for ch in payload["choices"]}
            self.assertEqual(keys, {"call_service", "renew_grace", "public_account", "let_cool"})
            call = next(ch for ch in payload["choices"] if ch["key"] == "call_service")
            self.assertIn(f"履约账本：{minister}", [str(e["label"]) for e in call["effects"]])

            res = court_events.resolve_decision(db, state, "call_service", day=day + 60)

            self.assertTrue(res["ok"], res)
            self.assertIn(f"履约账本：{minister}", [str(e["label"]) for e in res["effects"]])
            goals = db.list_conversation_goals(
                minister_name=minister,
                statuses=["waiting_conditions"],
            )
            self.assertEqual(len(goals), 1)
            self.assertIn("还恩差使", str(goals[0]["title"]))
            self.assertTrue(any("两月内回奏" in str(t["description"]) for t in goals[0]["conditions"]))
            self.assertIsNone(court_events.evaluate_decisions(db, state, day + 61))

    def test_tax_policy_legacy_triggers_aftershock_decision(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            legacy_id = _tax_legacy(db, state)

            payload = court_events.evaluate_decisions(db, state, day)

            self.assertIsNotNone(payload)
            self.assertEqual(payload["id"], "policy_aftershock")
            self.assertIn("苛税余波：辽饷", str(payload["title"]))
            self.assertIn("求见", str(payload["narrative"]))
            pending_ctx = (court_events.get_pending(db) or {}).get("ctx") or {}
            actor = str(pending_ctx.get("actor") or "")
            self.assertTrue(actor)
            self.assertIn(actor, str(payload["title"]))
            self.assertIn(actor, str(payload["narrative"]))
            keys = {str(ch["key"]) for ch in payload["choices"]}
            self.assertEqual(keys, {"keep_collecting", "relieve_now", "audit_middlemen"})
            keep = next(ch for ch in payload["choices"] if ch["key"] == "keep_collecting")
            self.assertIn(f"{actor}怨望 +5", [str(e["label"]) for e in keep["effects"]])
            relieve = next(ch for ch in payload["choices"] if ch["key"] == "relieve_now")
            self.assertIn("旧政缓和：辽饷", [str(e["label"]) for e in relieve["effects"]])
            pending = court_events.get_pending(db) or {}
            self.assertEqual(str(pending.get("cooldown_key")), f"policy_aftershock:{legacy_id}")

    def test_pending_secret_order_triggers_review_decision(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            minister, _ = _two_ming(db)
            order_id = _pending_secret_order(db, state, minister)

            payload = court_events.evaluate_decisions(db, state, day)

            self.assertIsNotNone(payload)
            self.assertEqual(payload["id"], "secret_order_review")
            self.assertIn(minister, str(payload["title"]))
            self.assertIn("密令核议", str(payload["title"]))
            self.assertIn("风声", str(payload["narrative"]))
            keys = {str(ch["key"]) for ch in payload["choices"]}
            self.assertEqual(keys, {"publish", "seal_continue", "question_assignee", "bury"})
            seal = next(ch for ch in payload["choices"] if ch["key"] == "seal_continue")
            self.assertIn("密令续查 2月", [str(e["label"]) for e in seal["effects"]])
            pending = court_events.get_pending(db) or {}
            self.assertEqual(str(pending.get("cooldown_key")), f"secret_order_review:{order_id}")

    def test_private_distress_triggers_personal_help_decision(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            actor, target = _private_distress_case(db, state)

            payload = court_events.evaluate_decisions(db, state, day)

            self.assertIsNotNone(payload)
            self.assertEqual(payload["id"], "private_distress")
            self.assertIn(actor, str(payload["title"]))
            self.assertIn(target, str(payload["narrative"]))
            keys = {str(ch["key"]) for ch in payload["choices"]}
            self.assertEqual(keys, {"grant_private_grace", "trade_for_service", "public_review", "refuse_private"})
            trade = next(ch for ch in payload["choices"] if ch["key"] == "trade_for_service")
            labels = [str(e["label"]) for e in trade["effects"]]
            self.assertIn(f"{actor}旧恩入账", labels)
            self.assertIn(f"履约账本：{actor}", labels)
            pending = court_events.get_pending(db) or {}
            self.assertEqual(str(pending.get("cooldown_key")), f"private_distress:{actor}")

    def test_patronage_bond_triggers_accountability_decision(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            sponsor, candidate = _patronage_case(db)

            payload = court_events.evaluate_decisions(db, state, day)

            self.assertIsNotNone(payload)
            self.assertEqual(payload["id"], "patronage_accountability")
            self.assertIn(sponsor, str(payload["title"]))
            self.assertIn(candidate, str(payload["title"]))
            self.assertIn("免费人情", str(payload["narrative"]))
            keys = {str(ch["key"]) for ch in payload["choices"]}
            self.assertEqual(keys, {"joint_trial", "sponsor_bond", "separate_trial", "reject_chain"})
            joint = next(ch for ch in payload["choices"] if ch["key"] == "joint_trial")
            labels = [str(e["label"]) for e in joint["effects"]]
            self.assertIn(f"履约账本：{sponsor}", labels)
            self.assertIn(f"履约账本：{candidate}", labels)
            pending = court_events.get_pending(db) or {}
            self.assertEqual(str(pending.get("cooldown_key")), f"patronage_accountability:{sponsor}:{candidate}")

    def test_blocked_goal_pressure_triggers_help_decision(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            minister, goal_id = _blocked_goal_case(db, state)

            payload = court_events.evaluate_decisions(db, state, day)

            self.assertIsNotNone(payload)
            self.assertEqual(payload["id"], "goal_obligation_help")
            self.assertIn(minister, str(payload["title"]))
            self.assertIn("旧约求裁", str(payload["title"]))
            self.assertIn("举主担保", str(payload["narrative"]))
            keys = {str(ch["key"]) for ch in payload["choices"]}
            self.assertEqual(keys, {"protect", "resource_support", "demand_evidence", "public_rebuke", "self_prove"})
            support = next(ch for ch in payload["choices"] if ch["key"] == "resource_support")
            support_labels = [str(e["label"]) for e in support["effects"]]
            self.assertIn("国库 -3", support_labels)
            self.assertIn("旧约拨助 1月", support_labels)
            demand = next(ch for ch in payload["choices"] if ch["key"] == "demand_evidence")
            self.assertIn("旧约展限 1月", [str(e["label"]) for e in demand["effects"]])
            rebuke = next(ch for ch in payload["choices"] if ch["key"] == "public_rebuke")
            self.assertIn("旧约追责", [str(e["label"]) for e in rebuke["effects"]])
            pending = court_events.get_pending(db) or {}
            self.assertEqual(str(pending.get("cooldown_key")), f"goal_obligation_help:{goal_id}")

    def test_bargain_goal_pressure_uses_old_account_dilemma(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            minister, goal_id = _blocked_bargain_goal_case(db, state)

            payload = court_events.evaluate_decisions(db, state, day)

            self.assertIsNotNone(payload)
            self.assertEqual(payload["id"], "goal_obligation_help")
            self.assertIn("旧账逼问", str(payload["title"]))
            self.assertIn("前番御前旧账", str(payload["narrative"]))
            self.assertIn("条件待证", str(payload["narrative"]))
            choices = {str(ch["key"]): ch for ch in payload["choices"]}
            self.assertEqual(set(choices), {"protect", "resource_support", "demand_evidence", "public_rebuke", "self_prove"})
            self.assertIn("补齐旧账证据", str(choices["demand_evidence"]["label"]))
            self.assertIn("旧账作废", str(choices["public_rebuke"]["label"]))
            self.assertIn("查证", str(choices["resource_support"]["hint"]))

            res = court_events.resolve_decision(db, state, "demand_evidence", day=day)

            self.assertTrue(res["ok"], res)
            self.assertIn("旧账逼问", str(res["title"]))
            goal = db.get_conversation_goal(goal_id) or {}
            self.assertEqual(goal.get("status"), "waiting_conditions")
            self.assertIn("旧账", json.dumps(goal.get("last_delta") or {}, ensure_ascii=False))
            event = db.conn.execute(
                """
                SELECT summary
                FROM conversation_goal_events
                WHERE goal_id=? AND event_kind='goal_extended'
                ORDER BY id DESC
                LIMIT 1
                """,
                (goal_id,),
            ).fetchone()
            self.assertIsNotNone(event)
            self.assertIn("条件待证", str(event["summary"]))


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

    def test_resolve_inquest_creates_evidence_obligation(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            a, b = _two_ming(db)
            _erupt(db, a, b)
            court_events.evaluate_decisions(db, state, day)
            record_decision_testimony(
                db,
                state,
                a,
                "rival_feud",
                f"朕未裁断前，先问你弹劾{b}有何证据？",
                "臣有账册与人证，愿限一月查验，若虚言愿担责。",
                target=b,
            )

            res = court_events.resolve_decision(db, state, "inquest", day=day)

            self.assertTrue(res["ok"])
            labels = [str(e["label"]) for e in res["effects"]]
            self.assertIn(f"履约账本：{a}", labels)
            self.assertIn("补证", str(res["choice"]))
            goals = db.list_conversation_goals(minister_name=a, statuses=["waiting_conditions"])
            self.assertEqual(len(goals), 1)
            self.assertIn("廷鞫核证", str(goals[0]["title"]))
            self.assertIn("补呈可核证据", str(goals[0]["target_text"]))
            self.assertIsNone(court_events.get_pending(db))

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
            self.assertIn(f"{petitioner}旧恩入账", labels)
            favors = court.favor_memories(db, petitioner, limit=3)
            self.assertEqual(len(favors), 1)
            self.assertIn("求援护持", str(favors[0]["title"]))
            self.assertIn("领难差", str(favors[0]["outcome"]))
            payload = briefing_payload(db, state, limit=5, kind="favor")
            card = next(c for c in payload["cards"] if c["actor"] == petitioner)
            self.assertEqual(card["kind"], "favor")
            self.assertIn("旧恩未报", str(card["title"]))
            brief = favor_chat_context_brief(db, petitioner, str(card["ref_id"]))
            self.assertIn("本次召对事项：旧恩未报", brief)
            self.assertIn("求援护持", brief)
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

    def test_goal_help_demand_evidence_revives_blocked_goal(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            minister, goal_id = _blocked_goal_case(db, state)
            court_events.evaluate_decisions(db, state, day)

            res = court_events.resolve_decision(db, state, "demand_evidence", day=day)

            self.assertTrue(res["ok"], res)
            self.assertIn("旧约展限 1月", [str(e["label"]) for e in res["effects"]])
            self.assertIn(f"{minister}旧约展限1月", str(res["effect"]))
            goal = db.get_conversation_goal(goal_id)
            self.assertEqual(goal["status"], "waiting_conditions")
            self.assertEqual(goal["condition_status"], "pending")
            self.assertGreater(int(goal["expires_turn"]), int(state.turn))
            self.assertEqual(goal["conditions"][0]["status"], "pending")
            self.assertEqual(goal["blockers"], [])
            self.assertEqual(goal["last_delta"]["court_decision"]["action"], "extend")

    def test_goal_help_resource_support_adds_accountability_conditions(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            minister, goal_id = _blocked_goal_case(db, state)
            treasury_before = int(state.metrics.get("国库", 0))
            court_events.evaluate_decisions(db, state, day)

            res = court_events.resolve_decision(db, state, "resource_support", day=day)

            self.assertTrue(res["ok"], res)
            labels = [str(e["label"]) for e in res["effects"]]
            self.assertIn("旧约拨助 1月", labels)
            self.assertIn("国库 -3", labels)
            self.assertIn(f"{minister}旧恩入账", labels)
            self.assertIn(f"{minister}得助复办1月", str(res["effect"]))
            self.assertEqual(int(state.metrics.get("国库", 0)), treasury_before - 3)
            goal = db.get_conversation_goal(goal_id)
            self.assertEqual(goal["status"], "waiting_conditions")
            self.assertEqual(goal["condition_status"], "pending")
            self.assertEqual(goal["blockers"], [])
            descriptions = [str(item.get("description") or "") for item in goal["conditions"]]
            self.assertTrue(any("新拨人手" in text for text in descriptions), descriptions)
            self.assertTrue(any("已用资源" in text for text in descriptions), descriptions)
            self.assertEqual(goal["last_delta"]["court_decision"]["action"], "resource")
            self.assertIn("support_tasks", goal["last_delta"])
            favors = court.favor_memories(db, minister, limit=3)
            self.assertEqual(len(favors), 1)
            self.assertIn("旧恩未报", str(favors[0]["title"]))
            self.assertIn("资源复办之恩", str(favors[0]["outcome"]))
            self.assertIn("不许装作两清", str(favors[0]["outcome"]))

    def test_goal_help_public_rebuke_fails_goal(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            minister, goal_id = _blocked_goal_case(db, state)
            court_events.evaluate_decisions(db, state, day)

            res = court_events.resolve_decision(db, state, "public_rebuke", day=day)

            self.assertTrue(res["ok"], res)
            self.assertIn("旧约追责", [str(e["label"]) for e in res["effects"]])
            self.assertIn(f"{minister}旧约追责", str(res["effect"]))
            goal = db.get_conversation_goal(goal_id)
            self.assertEqual(goal["status"], "expired")
            self.assertEqual(goal["condition_status"], "failed")
            self.assertEqual(goal["last_delta"]["court_decision"]["action"], "fail")

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
            actor = str(((court_events.get_pending(db) or {}).get("ctx") or {}).get("actor") or "")
            self.assertTrue(actor)
            before = db.conn.execute(
                "SELECT emp_trust, grievance FROM characters WHERE name=?",
                (actor,),
            ).fetchone()
            res = court_events.resolve_decision(db, state, "relieve_now", day=day)

            self.assertTrue(res["ok"], res)
            labels = [str(e["label"]) for e in res["effects"]]
            self.assertIn("旧政缓和：辽饷", labels)
            self.assertIn(f"{actor}信任 +4", labels)
            self.assertIn(f"{actor}怨望 -5", labels)
            row = db.conn.execute("SELECT modifiers, narrative_hint FROM legacies WHERE id=?", (legacy_id,)).fetchone()
            modifiers = json.loads(str(row["modifiers"] or "{}"))
            self.assertEqual(int(modifiers["民心"]), -5)
            self.assertIn("蠲缓", str(row["narrative_hint"]))
            self.assertEqual(int(state.metrics.get("国库", 0)), treasury_before - 8)
            after = db.conn.execute(
                "SELECT emp_trust, grievance FROM characters WHERE name=?",
                (actor,),
            ).fetchone()
            self.assertEqual(int(after["emp_trust"]), min(100, int(before["emp_trust"]) + 4))
            self.assertEqual(int(after["grievance"]), max(0, int(before["grievance"]) - 5))
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

    def test_secret_order_seal_continue_reopens_long_tail(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            minister, _ = _two_ming(db)
            order_id = _pending_secret_order(db, state, minister)

            court_events.evaluate_decisions(db, state, day)
            res = court_events.resolve_decision(db, state, "seal_continue", day=day)

            self.assertTrue(res["ok"], res)
            labels = [str(e["label"]) for e in res["effects"]]
            self.assertIn("密令续查 2月", labels)
            row = db.conn.execute(
                "SELECT status, due_turn, result, turn_closed FROM secret_orders WHERE id=?",
                (order_id,),
            ).fetchone()
            self.assertEqual(str(row["status"]), "active")
            self.assertEqual(int(row["due_turn"]), int(state.turn) + 2)
            self.assertIn("密押续查", str(row["result"]))
            self.assertIsNone(row["turn_closed"])
            self.assertIsNone(court_events.get_pending(db))

    def test_secret_order_question_creates_evidence_obligation(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            minister, _ = _two_ming(db)
            order_id = _pending_secret_order(db, state, minister)

            court_events.evaluate_decisions(db, state, day)
            res = court_events.resolve_decision(db, state, "question_assignee", day=day)

            self.assertTrue(res["ok"], res)
            labels = [str(e["label"]) for e in res["effects"]]
            self.assertIn("密令续查 1月", labels)
            self.assertIn(f"履约账本：{minister}", labels)
            row = db.conn.execute(
                "SELECT status, due_turn, result FROM secret_orders WHERE id=?",
                (order_id,),
            ).fetchone()
            self.assertEqual(str(row["status"]), "active")
            self.assertEqual(int(row["due_turn"]), int(state.turn) + 1)
            self.assertIn("补证问责", str(row["result"]))
            goals = db.list_conversation_goals(
                minister_name=minister,
                statuses=["waiting_conditions"],
            )
            self.assertEqual(len(goals), 1)
            self.assertIn("补证密令", str(goals[0]["title"]))
            self.assertTrue(any("可核验证据" in str(t["description"]) for t in goals[0]["conditions"]))

    def test_private_distress_trade_records_favor_and_followup(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            actor, target = _private_distress_case(db, state)
            before = db.conn.execute(
                "SELECT emp_trust, grievance FROM characters WHERE name=?",
                (actor,),
            ).fetchone()

            court_events.evaluate_decisions(db, state, day)
            res = court_events.resolve_decision(db, state, "trade_for_service", day=day)

            self.assertTrue(res["ok"], res)
            labels = [str(e["label"]) for e in res["effects"]]
            self.assertIn(f"{actor}旧恩入账", labels)
            self.assertIn(f"履约账本：{actor}", labels)
            favors = court.favor_memories(db, actor, limit=3)
            self.assertEqual(len(favors), 1)
            self.assertIn("旧恩未报", str(favors[0]["title"]))
            self.assertIn("可验差使偿还", str(favors[0]["outcome"]))
            goals = db.list_conversation_goals(
                minister_name=actor,
                statuses=["waiting_conditions"],
            )
            self.assertEqual(len(goals), 1)
            self.assertIn("偿恩差使", str(goals[0]["title"]))
            agreements = db.list_negotiation_agreements(
                minister_name=actor,
                action_kind="court_commitment",
                status="pending",
            )
            self.assertEqual(len(agreements), 1)
            self.assertEqual(int(agreements[0]["due_turn"]), int(state.turn) + 2)
            after = db.conn.execute(
                "SELECT emp_trust, grievance FROM characters WHERE name=?",
                (actor,),
            ).fetchone()
            self.assertEqual(int(after["emp_trust"]), min(100, int(before["emp_trust"]) + 6))
            self.assertEqual(int(after["grievance"]), max(0, int(before["grievance"]) - 4))
            opinion = court.get_opinion(db, actor, target)
            self.assertGreater(opinion, 64)
            self.assertIsNone(court_events.get_pending(db))

    def test_patronage_joint_trial_creates_two_followup_obligations(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            sponsor, candidate = _patronage_case(db)
            before_candidate = db.conn.execute(
                "SELECT emp_trust, grievance FROM characters WHERE name=?",
                (candidate,),
            ).fetchone()

            court_events.evaluate_decisions(db, state, day)
            res = court_events.resolve_decision(db, state, "joint_trial", day=day)

            self.assertTrue(res["ok"], res)
            labels = [str(e["label"]) for e in res["effects"]]
            self.assertIn(f"履约账本：{sponsor}", labels)
            self.assertIn(f"履约账本：{candidate}", labels)
            sponsor_goals = db.list_conversation_goals(
                minister_name=sponsor,
                statuses=["waiting_conditions"],
            )
            candidate_goals = db.list_conversation_goals(
                minister_name=candidate,
                statuses=["waiting_conditions"],
            )
            self.assertEqual(len(sponsor_goals), 1)
            self.assertEqual(len(candidate_goals), 1)
            self.assertIn("举主连坐", str(sponsor_goals[0]["title"]))
            self.assertIn("新人试差", str(candidate_goals[0]["title"]))
            self.assertGreater(court.get_opinion(db, sponsor, candidate), 38)
            self.assertGreater(court.get_opinion(db, candidate, sponsor), 42)
            after_candidate = db.conn.execute(
                "SELECT emp_trust, grievance FROM characters WHERE name=?",
                (candidate,),
            ).fetchone()
            self.assertEqual(int(after_candidate["emp_trust"]), int(before_candidate["emp_trust"]) + 5)
            self.assertEqual(int(after_candidate["grievance"]), int(before_candidate["grievance"]) + 1)
            self.assertIsNone(court_events.get_pending(db))

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
