import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory

from ming_sim.content import GameContent
from ming_sim.context import (
    build_npc_monthly_followups,
    bind_content as bind_context,
    npc_dialogue_behavior_profile,
    npc_network_recommendations,
    npc_relation_perspective,
)
from ming_sim.db import GameDB
from ming_sim.dialogue_audit import post_dialogue_audit, pre_dialogue_audit
from ming_sim.dialogue_goals import record_dialogue_effects
from ming_sim.issues import bind_content as bind_issues
from ming_sim.models import CourtContext, GameState, LLMConfig
from ming_sim.registry import (
    bind_content as bind_registry,
    build_monthly_followup_brief,
    build_personal_chat_memory_brief,
    build_stance_brief,
)
from ming_sim.session import GameSession
from ming_sim.simulation import build_simulator_payload
from ming_sim.skills import bind_content as bind_skills
from ming_sim.tools import build_minister_tools
from ming_sim.bureaucracy import directive_execution_assessments, secret_order_actor_assessment
from web_app import WebGame


class Row(dict):
    def keys(self):
        return super().keys()


class SimpleGoalAudit:
    def __init__(self, *, action_kind: str = "policy", title: str = "本次奏对目的", stance: str = "caution"):
        self.action_kind = action_kind
        self.title = title
        self.stance = stance

    def pre(self, payload):
        return {
            "goal_decision": "new",
            "goal_relation": "distinct_goal",
            "action_kind": self.action_kind,
            "title": self.title,
            "target_text": self.title,
            "confidence": 94,
            "public_hint": "识别为奏对目的。",
        }

    def post(self, payload):
        return {
            "goal_decision": "new",
            "goal_relation": "distinct_goal",
            "action_kind": self.action_kind,
            "title": self.title,
            "target_text": self.title,
            "stance": self.stance,
            "handshake_status": "none",
            "goal_status": "active",
            "score_delta": 20,
            "score_after": 20,
            "threshold": 70,
            "conditions": [],
            "blockers": [],
            "agreement_action": "none",
            "confidence": 92,
            "public_hint": "本轮只形成倾向，尚未握手。",
            "private_reason": str(payload.get("npc_answer") or ""),
        }


class CapturingAudit:
    def __init__(self) -> None:
        self.payloads = {}

    def pre(self, payload):
        self.payloads["pre"] = payload
        return {
            "goal_decision": "none",
            "confidence": 90,
            "public_hint": "无新目的。",
        }

    def post(self, payload):
        self.payloads["post"] = payload
        return {
            "goal_decision": "none",
            "goal_relation": "none",
            "action_kind": "general",
            "stance": "neutral",
            "handshake_status": "none",
            "goal_status": "active",
            "score_delta": 0,
            "score_after": 0,
            "threshold": 70,
            "conditions": [],
            "blockers": [],
            "agreement_action": "none",
            "confidence": 90,
            "public_hint": "无新目的。",
        }


class NPCBehaviorCrossPressureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.content = GameContent.load()
        for binder in (bind_context, bind_issues, bind_registry, bind_skills):
            binder(cls.content)

    def test_rival_mention_pushes_opposition_and_selective_truth(self) -> None:
        profile = npc_dialogue_behavior_profile(
            "韩爌",
            text="魏忠贤上疏说清查东林不宜太急，卿怎么看？",
        )

        self.assertEqual(profile["preferred_stance"], "oppose")
        self.assertEqual(profile["truth_mode"], "选择性真话")
        self.assertIn("魏忠贤（党争敌对）", profile["network_pressure"]["rivals"])
        self.assertIn("政敌牵动", profile["risk_tags"])

    def test_deceptive_traits_allow_half_truths(self) -> None:
        profile = npc_dialogue_behavior_profile(
            "温体仁",
            text="钱谦益建议起复东林旧臣，卿怎么看？",
        )

        self.assertEqual(profile["truth_mode"], "半真半假")
        self.assertIn("话术不实", profile["risk_tags"])
        self.assertIn("钱谦益（同门）", profile["network_pressure"]["allies"])
        self.assertTrue(
            {"阳奉阴违", "善观风色", "猜忌多疑", "结党营私"}.intersection(
                set(profile["network_pressure"]["traits"])
            )
        )

    def test_dialogue_behavior_ignores_legacy_xinpan_input(self) -> None:
        baseline = npc_dialogue_behavior_profile(
            "韩爌",
            text="魏忠贤上疏说清查东林不宜太急，卿怎么看？",
        )
        with_legacy_input = npc_dialogue_behavior_profile(
            "韩爌",
            xinpan_profile={
                "quadrant": "股肱",
                "fear": 100,
                "hatred": 100,
                "trust_coeff": 0.1,
                "behavior_hint": "旧系统不应进入新谈话。",
            },
            text="魏忠贤上疏说清查东林不宜太急，卿怎么看？",
        )

        self.assertEqual(with_legacy_input["preferred_stance"], baseline["preferred_stance"])
        self.assertEqual(with_legacy_input["truth_mode"], baseline["truth_mode"])
        self.assertEqual(with_legacy_input["network_pressure"], baseline["network_pressure"])
        self.assertNotIn("旧系统不应进入新谈话。", str(with_legacy_input))

    def test_relation_perspective_frames_rival_advice_as_accusation(self) -> None:
        perspective = npc_relation_perspective(
            "韩爌",
            "魏忠贤",
            topic="魏忠贤建议清查东林不宜太急",
        )

        self.assertTrue(perspective["found"])
        self.assertEqual("rival", perspective["relation_class"])
        self.assertEqual("oppose", perspective["posture"])
        self.assertEqual("选择性真话", perspective["truth_mode"])
        self.assertIn("政敌告状", perspective["risk_tags"])
        self.assertIn("质疑动机", perspective["guidance"])

    def test_relation_perspective_frames_ally_as_shielded_half_truth(self) -> None:
        perspective = npc_relation_perspective(
            "温体仁",
            "钱谦益",
            topic="钱谦益建议起复东林旧臣",
        )

        self.assertTrue(perspective["found"])
        self.assertEqual("ally", perspective["relation_class"])
        self.assertEqual("shield", perspective["posture"])
        self.assertEqual("半真半假", perspective["truth_mode"])
        self.assertIn("同党背书", perspective["risk_tags"])
        self.assertIn("话术不实", perspective["risk_tags"])
        self.assertIn("转圜", perspective["guidance"])

    def test_dialogue_audit_payload_includes_behavior_profile(self) -> None:
        with TemporaryDirectory() as tmp:
            db = GameDB(str(Path(tmp) / "npc_audit_behavior.db"), content=self.content)
            db.seed_static_data()
            state = GameState(
                year=1628,
                period=1,
                turn=1,
                metrics={"国库": 100, "内库": 50, "民心": 50, "皇威": 50},
            )
            han = self.content.characters["韩爌"]
            audit = CapturingAudit()
            user_text = "魏忠贤余党仍在朝中，卿可替朕议一议如何清查？"
            answer = "魏忠贤乱政久矣，臣愿据实奏劾其余党，但查办须有明旨。"

            pre = pre_dialogue_audit(db, state, han, user_text, audit_client=audit)
            post_dialogue_audit(db, state, han, user_text, answer, pre_audit=pre, audit_client=audit)

            for phase in ("pre", "post"):
                payload = audit.payloads[phase]
                profile = payload["behavior_profile"]
                self.assertEqual("选择性真话", profile["truth_mode"])
                self.assertIn("魏忠贤（党争敌对）", profile["network_pressure"]["rivals"])
                self.assertIn("政敌牵动", profile["risk_tags"])
                self.assertIn("NPC对话行为档案", payload["behavior_brief"])
                self.assertIn("魏忠贤余党", payload["behavior_source_excerpt"])
            db.conn.close()

    def test_hostile_relations_are_not_positive_recommendations(self) -> None:
        rows = npc_network_recommendations("韩爌", limit=30)
        names = {str(row["name"]) for row in rows}

        self.assertNotIn("魏忠贤", names)
        self.assertNotIn("崔呈秀", names)
        mixed = next(row for row in rows if row["name"] == "黄立极")
        self.assertTrue(mixed.get("conflicts"))
        self.assertTrue(any("党争敌对" in item for item in mixed["conflicts"]))

    def test_appointment_tool_marks_rival_as_relation_risk(self) -> None:
        with TemporaryDirectory() as tmp:
            db = GameDB(str(Path(tmp) / "npc_appointment_risk.db"), content=self.content)
            db.seed_static_data()
            state = GameState(
                year=1628,
                period=1,
                turn=1,
                metrics={"国库": 100, "内库": 50, "民心": 50, "皇威": 50},
            )
            han_as_personnel = replace(self.content.characters["韩爌"], office_type="吏部")
            tools = build_minister_tools(han_as_personnel, CourtContext(state=state, db=db))
            propose = next(tool for tool in tools if getattr(tool, "__name__", "") == "propose_appointment")

            result = propose("魏忠贤", "司礼监掌印", faction="阉党", reason="皇帝点名制衡")

            self.assertIn("__pending_appointment__", result)
            self.assertIn("关系风险", result)
            self.assertIn("党争敌对", result)
            self.assertIn("不是私人举荐", result)
            db.conn.close()

    def test_recommendation_tool_surfaces_ability_fit_and_character_risk(self) -> None:
        with TemporaryDirectory() as tmp:
            db = GameDB(str(Path(tmp) / "npc_recommend_fit.db"), content=self.content)
            db.seed_static_data()
            state = GameState(
                year=1628,
                period=1,
                turn=1,
                metrics={"国库": 100, "内库": 50, "民心": 50, "皇威": 50},
            )
            tools = build_minister_tools(self.content.characters["韩爌"], CourtContext(state=state, db=db))
            recommend = next(tool for tool in tools if getattr(tool, "__name__", "") == "recommend_candidates_by_network")

            result = recommend("辽事军务与钱粮清查", "兵部督师", limit=2)

            self.assertIn("能力命中", result)
            self.assertIn("辽事军务", result)
            self.assertIn("性格风险", result)
            self.assertIn("承办边界", result)
            self.assertIn("拖延", result)
            db.conn.close()

    def test_appointment_payload_carries_candidate_fit_and_risk(self) -> None:
        with TemporaryDirectory() as tmp:
            db = GameDB(str(Path(tmp) / "npc_appointment_fit.db"), content=self.content)
            db.seed_static_data()
            state = GameState(
                year=1628,
                period=1,
                turn=1,
                metrics={"国库": 100, "内库": 50, "民心": 50, "皇威": 50},
            )
            han_as_personnel = replace(self.content.characters["韩爌"], office_type="吏部")
            tools = build_minister_tools(han_as_personnel, CourtContext(state=state, db=db))
            propose = next(tool for tool in tools if getattr(tool, "__name__", "") == "propose_appointment")

            result = propose("张宗衡", "兵部督师", faction="中立", reason="辽事军务与钱粮清查")

            self.assertIn("__pending_appointment__", result)
            self.assertIn("能力命中", result)
            self.assertIn("辽事军务", result)
            self.assertIn("性格风险", result)
            self.assertIn("承办边界", result)
            db.conn.close()

    def test_assess_person_tool_combines_relation_pressure_with_target_fit(self) -> None:
        with TemporaryDirectory() as tmp:
            db = GameDB(str(Path(tmp) / "npc_assess_fit.db"), content=self.content)
            db.seed_static_data()
            state = GameState(
                year=1628,
                period=1,
                turn=1,
                metrics={"国库": 100, "内库": 50, "民心": 50, "皇威": 50},
            )
            tools = build_minister_tools(self.content.characters["韩爌"], CourtContext(state=state, db=db))
            assess = next(tool for tool in tools if getattr(tool, "__name__", "") == "assess_person_by_network")

            result = assess("魏忠贤", "让魏忠贤承办密查清流与查办东林")

            self.assertIn("目标匹配", result)
            self.assertIn("能力命中", result)
            self.assertIn("密查耳目", result)
            self.assertIn("性格风险", result)
            self.assertIn("过度用刑", result)
            self.assertIn("关系：党争敌对", result)
            self.assertIn("建议口径", result)
            db.conn.close()

    def test_directive_context_carries_backing_and_party_blockers(self) -> None:
        with TemporaryDirectory() as tmp:
            db = GameDB(str(Path(tmp) / "npc_cross.db"), content=self.content)
            db.seed_static_data()
            state = GameState(
                year=1628,
                period=1,
                turn=1,
                metrics={"国库": 100, "内库": 50, "民心": 50, "皇威": 50},
            )
            directive = Row(
                id=1,
                actor="韩爌",
                source="test",
                event_title="查阉党",
                text="命韩爌会同都察院清查魏忠贤余党，整肃阉党人事。",
            )

            payload = build_simulator_payload(state, db, "测试诏书", "", directives=[directive])
            directive_context = payload["directive_context"][0]
            pressure = directive_context["cross_pressure"]

            self.assertIn("钱龙锡：党附", pressure["usable_backing"])
            self.assertIn("魏忠贤：党争敌对", pressure["likely_blockers"])
            self.assertIn("党争或旧怨阻力", pressure["execution_read"])
            self.assertIn("personality_behavior", directive_context)
            self.assertNotIn("tiangang_behavior", directive_context)
            self.assertNotIn("xinpan", directive_context)
            self.assertNotIn("xinpan_board", payload)
            db.conn.close()

    def test_execution_assessment_uses_ability_axes_as_modifier(self) -> None:
        with TemporaryDirectory() as tmp:
            db = GameDB(str(Path(tmp) / "npc_trait_positive.db"), content=self.content)
            db.seed_static_data()
            state = GameState(
                year=1628,
                period=1,
                turn=1,
                metrics={"国库": 100, "内库": 50, "民心": 50, "皇威": 50},
            )
            directive = Row(
                id=3,
                actor="毕自严",
                source="test",
                event_title="整顿钱粮",
                text="命毕自严清查江南积欠、整顿盐课钱粮。",
            )

            assessment = directive_execution_assessments(state, db, [directive])[0]

            self.assertGreater(assessment["trait_modifier"], 0)
            self.assertIn("钱粮经世能力轴", assessment["trait_note"])
            self.assertTrue(any("能力/trait修正" in item for item in assessment["drivers"]))
            db.conn.close()

    def test_execution_assessment_surfaces_trait_risks(self) -> None:
        with TemporaryDirectory() as tmp:
            db = GameDB(str(Path(tmp) / "npc_trait_negative.db"), content=self.content)
            db.seed_static_data()
            state = GameState(
                year=1628,
                period=1,
                turn=1,
                metrics={"国库": 100, "内库": 50, "民心": 50, "皇威": 50},
            )
            directive = Row(
                id=4,
                actor="温体仁",
                source="test",
                event_title="铨选东林",
                text="命温体仁会同吏部铨选东林旧臣起复章程。",
            )

            assessment = directive_execution_assessments(state, db, [directive])[0]

            self.assertLess(assessment["trait_modifier"], 0)
            self.assertIn("门户人事风险", assessment["trait_note"])
            self.assertTrue(any("阳奉阴违" in item or "口头顺从" in item for item in assessment["risks"]))
            db.conn.close()

    def test_execution_assessment_uses_only_relevant_dialogue_stance_risks(self) -> None:
        with TemporaryDirectory() as tmp:
            db = GameDB(str(Path(tmp) / "npc_relevant_stance_risk.db"), content=self.content)
            db.seed_static_data()
            state = GameState(
                year=1628,
                period=1,
                turn=1,
                metrics={"国库": 100, "内库": 50, "民心": 50, "皇威": 50},
            )
            wen = self.content.characters["温体仁"]
            record_dialogue_effects(
                db,
                state,
                wen,
                "钱谦益建议起复东林旧臣，卿觉得可行吗？",
                "钱谦益素有文望，臣以为不可遽定，宜会审留余地，容臣再查。",
                audit_client=SimpleGoalAudit(action_kind="court_commitment", title="评价钱谦益起复建议", stance="caution"),
            )
            directives = [
                Row(
                    id=31,
                    actor="温体仁",
                    source="test",
                    event_title="起复钱谦益",
                    text="命温体仁会同吏部拟钱谦益起复章程，并议东林旧臣去留。",
                ),
                Row(
                    id=32,
                    actor="温体仁",
                    source="test",
                    event_title="整顿钱粮",
                    text="命温体仁会同户部清核仓场钱粮，严禁胥吏侵吞。",
                ),
            ]

            relevant, unrelated = directive_execution_assessments(state, db, directives)

            self.assertTrue(any("同党/恩主" in item for item in relevant["stance_risks"]))
            self.assertTrue(any("话术有保留" in item for item in relevant["stance_risks"]))
            self.assertTrue(any("钱谦益" in item for item in relevant["risks"]))
            self.assertIn("评价钱谦益起复建议", relevant["stance_score"] and relevant["drivers"][-1])
            self.assertEqual([], unrelated["stance_risks"])
            self.assertIn("未命中本旨", unrelated["drivers"][-1])
            self.assertFalse(any("钱谦益" in item for item in unrelated["risks"]))
            db.conn.close()

    def test_secret_order_actor_assessment_carries_relevant_trait_and_stance_risks(self) -> None:
        with TemporaryDirectory() as tmp:
            db = GameDB(str(Path(tmp) / "npc_secret_actor_assessment.db"), content=self.content)
            db.seed_static_data()
            state = GameState(
                year=1628,
                period=1,
                turn=1,
                metrics={"国库": 100, "内库": 50, "民心": 50, "皇威": 50},
            )
            wen = self.content.characters["温体仁"]
            record_dialogue_effects(
                db,
                state,
                wen,
                "钱谦益建议起复东林旧臣，卿觉得可行吗？",
                "钱谦益素有文望，臣以为不可遽定，宜会审留余地，容臣再查。",
                audit_client=SimpleGoalAudit(action_kind="secret_order", title="密查钱谦益起复", stance="caution"),
            )
            related_id = db.create_secret_order(
                state,
                "温体仁",
                "密查钱谦益",
                "暗查钱谦益起复东林旧臣之议，摸清同党牵连。",
                ["钱谦益", "东林", "起复"],
            )
            unrelated_id = db.create_secret_order(
                state,
                "温体仁",
                "密查仓场钱粮",
                "暗查仓场钱粮胥吏侵吞旧弊，摸清账册缺口。",
                ["仓场", "钱粮"],
            )

            related = secret_order_actor_assessment(state, db, db.get_secret_order(related_id) or {})
            unrelated = secret_order_actor_assessment(state, db, db.get_secret_order(unrelated_id) or {})

            self.assertIn("温体仁", related["actor"])
            self.assertTrue(any("同党/恩主" in item for item in related["stance_risks"]))
            self.assertTrue(any("话术有保留" in item for item in related["stance_risks"]))
            self.assertTrue(any("口头顺从" in item for item in related["risks"]))
            self.assertIn("钱谦益（同门）", related["personality_behavior"])
            self.assertEqual([], unrelated["stance_risks"])
            self.assertIn("未命中本旨", unrelated["drivers"][-1])
            self.assertFalse(any("钱谦益" in item for item in unrelated["risks"]))
            db.conn.close()

    def test_secret_order_tools_return_actor_behavior_brief(self) -> None:
        with TemporaryDirectory() as tmp:
            db = GameDB(str(Path(tmp) / "npc_secret_tool_brief.db"), content=self.content)
            db.seed_static_data()
            state = GameState(
                year=1628,
                period=1,
                turn=1,
                metrics={"国库": 100, "内库": 50, "民心": 50, "皇威": 50},
            )
            wen = self.content.characters["温体仁"]
            record_dialogue_effects(
                db,
                state,
                wen,
                "钱谦益建议起复东林旧臣，卿觉得可行吗？",
                "钱谦益素有文望，臣以为不可遽定，宜会审留余地，容臣再查。",
                audit_client=SimpleGoalAudit(action_kind="secret_order", title="密查钱谦益起复", stance="caution"),
            )
            order_id = db.create_secret_order(
                state,
                "温体仁",
                "密查钱谦益",
                "暗查钱谦益起复东林旧臣之议，摸清同党牵连。",
                ["钱谦益", "东林", "起复"],
            )
            tools = build_minister_tools(wen, CourtContext(state=state, db=db))
            report = next(tool for tool in tools if getattr(tool, "__name__", "") == "report_secret_order_progress")
            submit = next(tool for tool in tools if getattr(tool, "__name__", "") == "submit_secret_order_for_review")

            progress_result = report(order_id, "探得钱谦益门生往来频密，尚须核实名帖。")
            submit_result = submit(order_id, "已查得钱谦益门生往来名帖，臣请付核。")

            for result in (progress_result, submit_result):
                self.assertIn("密令承办画像", result)
                self.assertIn("承办适配", result)
                self.assertIn("风险", result)
                self.assertIn("话术有保留", result)
                self.assertIn("同党/恩主", result)
                self.assertIn("行为口径", result)
                self.assertIn("表面恭顺", result)
            self.assertIn("本月即建档当月", progress_result)
            self.assertIn("已提交待推演核议", submit_result)
            db.conn.close()

    def test_secret_order_issue_and_rush_return_actor_behavior_brief(self) -> None:
        with TemporaryDirectory() as tmp:
            db = GameDB(str(Path(tmp) / "npc_secret_issue_brief.db"), content=self.content)
            db.seed_static_data()
            state = GameState(
                year=1628,
                period=1,
                turn=1,
                metrics={"国库": 100, "内库": 50, "民心": 50, "皇威": 50},
            )
            wen = self.content.characters["温体仁"]
            record_dialogue_effects(
                db,
                state,
                wen,
                "钱谦益建议起复东林旧臣，卿觉得可行吗？",
                "钱谦益素有文望，臣以为不可遽定，宜会审留余地，容臣再查。",
                audit_client=SimpleGoalAudit(action_kind="secret_order", title="密查钱谦益起复", stance="caution"),
            )
            tools = build_minister_tools(wen, CourtContext(state=state, db=db))
            issue = next(tool for tool in tools if getattr(tool, "__name__", "") == "issue_secret_order")
            rush = next(tool for tool in tools if getattr(tool, "__name__", "") == "rush_secret_order")

            issue_result = issue(
                "密查钱谦益",
                "暗查钱谦益起复东林旧臣之议，摸清同党牵连。",
                tags_json='["钱谦益","东林","起复"]',
                deadline_months=2,
            )
            order_id = int(issue_result.split("__")[2])
            rush_result = rush(order_id, deadline_months=0, reason="事涉东林起复，须即月核议。")

            self.assertTrue(issue_result.startswith("__secret_order_registered__"))
            for result in (issue_result, rush_result):
                self.assertIn("密令承办画像", result)
                self.assertIn("承办适配", result)
                self.assertIn("风险", result)
                self.assertIn("话术有保留", result)
                self.assertIn("同党/恩主", result)
                self.assertIn("行为口径", result)
            self.assertIn("已奉旨即核", rush_result)
            db.conn.close()

    def test_agreement_political_effect_uses_npc_influence_not_xinpan(self) -> None:
        with TemporaryDirectory() as tmp:
            db = GameDB(str(Path(tmp) / "npc_agreement_effect.db"), content=self.content)
            db.seed_static_data()
            state = GameState(
                year=1628,
                period=2,
                turn=2,
                metrics={"国库": 100, "内库": 50, "民心": 50, "皇威": 50},
            )
            agreement = {
                "id": 7,
                "minister_name": "韩爌",
                "action_kind": "policy",
                "stakes": "制度名分",
                "target_text": "清查阉党旧案",
                "political_effect_json": "{}",
            }

            effect = db._apply_negotiation_political_effect(
                state,
                agreement,
                new_status="fulfilled",
                evidence="条件已经兑现。",
            )
            logs = db.conn.execute("SELECT COUNT(*) AS n FROM xinpan_logs").fetchone()["n"]

            self.assertNotIn("xinpan", effect)
            self.assertEqual("agreement_fulfilled", effect["npc_influence"]["memory_signal"])
            self.assertIn("履约资本", effect["npc_influence"]["expected_behavior"])
            self.assertEqual(0, int(logs or 0))
            self.assertEqual({}, db.get_xinpan_profile("韩爌", state))
            self.assertEqual("", db.xinpan_agent_brief("韩爌", state))
            self.assertEqual([], db.xinpan_simulator_rows(state))
            self.assertEqual(0, db.ensure_xinpan_states(state))
            self.assertIsNone(
                db.apply_direct_xinpan_adjustment(
                    state,
                    "韩爌",
                    shi_delta=99,
                    fear_delta=99,
                    hatred_delta=99,
                    trust_multiplier=0.1,
                )
            )
            logs_after_legacy_call = db.conn.execute("SELECT COUNT(*) AS n FROM xinpan_logs").fetchone()["n"]
            self.assertEqual(0, int(logs_after_legacy_call or 0))
            db.conn.close()

    def test_directive_context_reads_recorded_speech_pressure(self) -> None:
        with TemporaryDirectory() as tmp:
            db = GameDB(str(Path(tmp) / "npc_speech_directive.db"), content=self.content)
            db.seed_static_data()
            state = GameState(
                year=1628,
                period=1,
                turn=1,
                metrics={"国库": 100, "内库": 50, "民心": 50, "皇威": 50},
            )
            han = self.content.characters["韩爌"]
            record_dialogue_effects(
                db,
                state,
                han,
                "魏忠贤余党仍在朝中，卿可替朕议一议如何清查？",
                "魏忠贤乱政久矣，臣愿据实奏劾其余党，但查办须有明旨。",
                audit_client=SimpleGoalAudit(title="清查魏忠贤余党", stance="caution"),
            )
            directive = Row(
                id=2,
                actor="韩爌",
                source="test",
                event_title="清查阉党",
                text="命韩爌清查魏忠贤余党，整饬言路与厂卫旧案。",
            )

            payload = build_simulator_payload(state, db, "测试诏书", "", directives=[directive])
            pressure = payload["directive_context"][0]["cross_pressure"]

            self.assertTrue(any("借题告状" in item for item in pressure["dialogue_speech"]))
            self.assertTrue(any("选择性真话" in item for item in pressure["truth_risks"]))
            self.assertIn("政敌告状", pressure["stance_risk_tags"])
            self.assertIn("本回合召对话术", pressure["execution_read"])
            db.conn.close()

    def test_recorded_stance_persists_accusation_pressure(self) -> None:
        with TemporaryDirectory() as tmp:
            db = GameDB(str(Path(tmp) / "npc_accusation.db"), content=self.content)
            db.seed_static_data()
            state = GameState(
                year=1628,
                period=1,
                turn=1,
                metrics={"国库": 100, "内库": 50, "民心": 50, "皇威": 50},
            )
            han = self.content.characters["韩爌"]

            record_dialogue_effects(
                db,
                state,
                han,
                "魏忠贤余党仍在朝中，卿可替朕议一议如何清查？",
                "魏忠贤乱政久矣，臣愿据实奏劾其余党，但查办须有明旨。",
                audit_client=SimpleGoalAudit(title="清查魏忠贤余党", stance="caution"),
            )
            stance = db.list_minister_stances(minister_name="韩爌", limit=1)[0]
            speech = stance["evidence"]["speech_profile"]

            self.assertIn("accusation", speech["speech_acts"])
            self.assertIn("政敌告状", stance["risk_tags_list"])
            self.assertIn("魏忠贤（党争敌对）", speech["network_pressure"]["rivals"])
            self.assertIn("涉政敌告状", stance["execution_hint"])
            db.conn.close()

    def test_next_dialogue_stance_brief_keeps_accusation_continuity(self) -> None:
        with TemporaryDirectory() as tmp:
            db = GameDB(str(Path(tmp) / "npc_accusation_brief.db"), content=self.content)
            db.seed_static_data()
            state = GameState(
                year=1628,
                period=1,
                turn=1,
                metrics={"国库": 100, "内库": 50, "民心": 50, "皇威": 50},
            )
            han = self.content.characters["韩爌"]
            record_dialogue_effects(
                db,
                state,
                han,
                "魏忠贤余党仍在朝中，卿可替朕议一议如何清查？",
                "魏忠贤乱政久矣，臣愿据实奏劾其余党，但查办须有明旨。",
                audit_client=SimpleGoalAudit(title="清查魏忠贤余党", stance="caution"),
            )

            brief = build_stance_brief(han, CourtContext(state=state, db=db))

            self.assertIn("后续口径", brief)
            self.assertIn("告状/奏劾", brief)
            self.assertIn("不突然替其开脱", brief)
            self.assertIn("魏忠贤（党争敌对）", brief)
            db.conn.close()

    def test_monthly_followups_surface_memory_and_due_updates(self) -> None:
        with TemporaryDirectory() as tmp:
            db = GameDB(str(Path(tmp) / "npc_monthly_followup.db"), content=self.content)
            db.seed_static_data()
            last_state = GameState(
                year=1628,
                period=1,
                turn=1,
                metrics={"国库": 100, "内库": 50, "民心": 50, "皇威": 50},
            )
            han = self.content.characters["韩爌"]
            record_dialogue_effects(
                db,
                last_state,
                han,
                "魏忠贤余党仍在朝中，卿可替朕议一议如何清查？",
                "魏忠贤乱政久矣，臣愿据实奏劾其余党，但查办须有明旨。",
                audit_client=SimpleGoalAudit(title="清查魏忠贤余党", stance="caution"),
            )
            db.create_secret_order(
                last_state,
                "韩爌",
                "密查阉党旧案",
                "查魏忠贤余党旧案。",
                ["魏忠贤", "阉党"],
                deadline_months=1,
            )
            current_state = GameState(
                year=1628,
                period=2,
                turn=2,
                metrics={"国库": 100, "内库": 50, "民心": 50, "皇威": 50},
            )

            followups = build_npc_monthly_followups(db, current_state, limit=5)
            han_item = next(item for item in followups if item["minister_name"] == "韩爌")

            self.assertIn("conversation_goal:active", han_item["reason_types"])
            self.assertIn("secret_order_due", han_item["reason_types"])
            self.assertIn("last_month_stance", han_item["reason_types"])
            self.assertIn("speech_continuity", han_item["reason_types"])
            self.assertIn("选择性真话", han_item["truth_mode"])
            self.assertIn("政敌牵动", han_item["risk_tags"])
            self.assertIn("请安", han_item["suggested_opening"])
            db.conn.close()

    def test_monthly_followup_brief_injects_only_current_npc(self) -> None:
        state = GameState(
            year=1628,
            period=2,
            turn=2,
            metrics={"国库": 100, "内库": 50, "民心": 50, "皇威": 50},
        )
        context = CourtContext(
            state=state,
            db=object(),
            monthly_followups=[
                {
                    "minister_name": "韩爌",
                    "title": "未完奏对「清查魏忠贤余党」仍需复命或请旨。",
                    "summary": "韩爌应回奏阉党旧案。",
                    "memory_hooks": ["密令 #1「密查阉党旧案」已到限期，应请安回奏进展。"],
                    "reason_types": ["secret_order_due", "speech_continuity"],
                    "suggested_opening": "请安后可主动复命，请求明旨或资源，把事往前推。",
                    "truth_mode": "选择性真话",
                    "personality_cue": "继续选择性陈述事实，可放大有利证据、压低不利动机",
                    "risk_tags": ["政敌牵动", "密令回奏"],
                },
                {
                    "minister_name": "温体仁",
                    "title": "温体仁另有回奏。",
                    "summary": "不应泄给韩爌。",
                },
            ],
        )

        brief = build_monthly_followup_brief(self.content.characters["韩爌"], context)

        self.assertIn("本月候见/请安提示", brief)
        self.assertIn("密查阉党旧案", brief)
        self.assertIn("选择性真话", brief)
        self.assertIn("政敌牵动", brief)
        self.assertNotIn("温体仁另有回奏", brief)

    def test_session_begin_turn_snapshot_carries_monthly_followups(self) -> None:
        with TemporaryDirectory() as tmp:
            session = GameSession(
                str(Path(tmp) / "npc_session_followups.db"),
                LLMConfig(api_key="test", base_url="http://test.invalid/v1", model="test-model"),
                content=self.content,
                verify_llm=False,
            )
            session.auto_save = lambda tag: None  # type: ignore[method-assign]
            last_state = GameState(
                year=1628,
                period=1,
                turn=1,
                metrics={"国库": 100, "内库": 50, "民心": 50, "皇威": 50},
            )
            han = self.content.characters["韩爌"]
            record_dialogue_effects(
                session.db,
                last_state,
                han,
                "魏忠贤余党仍在朝中，卿可替朕议一议如何清查？",
                "魏忠贤乱政久矣，臣愿据实奏劾其余党，但查办须有明旨。",
                audit_client=SimpleGoalAudit(title="清查魏忠贤余党", stance="caution"),
            )
            session.db.create_secret_order(
                last_state,
                "韩爌",
                "密查阉党旧案",
                "查魏忠贤余党旧案。",
                ["魏忠贤", "阉党"],
                deadline_months=1,
            )
            current_state = GameState(
                year=1628,
                period=2,
                turn=2,
                metrics={"国库": 100, "内库": 50, "民心": 50, "皇威": 50},
            )
            session.db.save_state(current_state)

            snapshot = session.begin_turn()
            han_item = next(item for item in snapshot.monthly_followups if item["minister_name"] == "韩爌")

            self.assertEqual(snapshot.monthly_followups, session.monthly_followups)
            self.assertIn("secret_order_due", han_item["reason_types"])
            self.assertIn("speech_continuity", han_item["reason_types"])
            self.assertIn("密查阉党旧案", han_item["summary"])
            session.close()

    def test_session_behavior_profile_reads_chapter_memory_without_player_keyword(self) -> None:
        with TemporaryDirectory() as tmp:
            session = GameSession(
                str(Path(tmp) / "npc_session_memory_behavior.db"),
                LLMConfig(api_key="test", base_url="http://test.invalid/v1", model="test-model"),
                content=self.content,
                verify_llm=False,
            )
            session.dialogue_audit_client = lambda phase, payload: {  # type: ignore[assignment]
                "goal_decision": "none",
                "confidence": 90,
            }
            session.db.save_chapter_memory(
                session.state,
                title="上月密查阉党",
                body="上月密令韩爌密查魏忠贤余党，旧约履约未了，仍待回奏。",
                tags=["韩爌", "魏忠贤", "阉党"],
            )

            augmented, prepared = session.prepare_chat_run(
                self.content.characters["韩爌"],
                "卿今日入阁，有何见闻？",
            )

            self.assertIn("NPC对话行为档案", augmented)
            self.assertIn("旧事/履约牵引", augmented)
            self.assertIn("旧事牵引", augmented)
            self.assertIn("近来朝局", augmented)
            self.assertIn("上月密令韩爌", prepared.behavior_context)
            self.assertIn("旧事牵引", prepared.behavior_brief)
            session.close()

    def test_next_chat_prepare_refreshes_same_turn_secret_order_memory(self) -> None:
        with TemporaryDirectory() as tmp:
            session = GameSession(
                str(Path(tmp) / "npc_session_live_secret.db"),
                LLMConfig(api_key="test", base_url="http://test.invalid/v1", model="test-model"),
                content=self.content,
                verify_llm=False,
            )
            session.dialogue_audit_client = lambda phase, payload: {  # type: ignore[assignment]
                "goal_decision": "none",
                "confidence": 90,
            }
            session.db.create_secret_order(
                session.state,
                "韩爌",
                "密查阉党旧案",
                "查魏忠贤余党旧案。",
                ["魏忠贤", "阉党"],
            )

            augmented, prepared = session.prepare_chat_run(
                self.content.characters["韩爌"],
                "卿还有何要回奏？",
            )

            self.assertIn("本轮动态记忆/立场", augmented)
            self.assertIn("你身上还在办的密令", augmented)
            self.assertIn("密查阉党旧案", augmented)
            self.assertIn("本月尚未推进", augmented)
            self.assertIn("密查阉党旧案", prepared.behavior_context)
            self.assertIn("旧事牵引", prepared.behavior_brief)
            session.close()

    def test_recorded_stance_uses_prepared_memory_behavior_context(self) -> None:
        with TemporaryDirectory() as tmp:
            session = GameSession(
                str(Path(tmp) / "npc_session_memory_stance.db"),
                LLMConfig(api_key="test", base_url="http://test.invalid/v1", model="test-model"),
                content=self.content,
                verify_llm=False,
            )
            session.dialogue_audit_client = SimpleGoalAudit(title="询问密查旧案", stance="caution")
            session.db.save_chapter_memory(
                session.state,
                title="上月密查阉党",
                body="上月密令韩爌密查魏忠贤余党，旧约履约未了，仍待回奏。",
                tags=["韩爌", "魏忠贤", "阉党"],
            )
            user_text = "卿今日入阁，有何见闻？"
            answer = "臣已有几件旧案在手，须请圣上给一个明白章程。"
            _, prepared = session.prepare_chat_run(self.content.characters["韩爌"], user_text)

            session.record_dialogue_after_chat(
                self.content.characters["韩爌"],
                user_text,
                answer,
                prepared,
            )
            stance = session.db.list_minister_stances(minister_name="韩爌", limit=1)[0]
            speech = stance["evidence"]["speech_profile"]

            self.assertIn("旧事牵引", speech["risk_tags"])
            self.assertIn("旧事牵引", stance["risk_tags_list"])
            self.assertIn("复命与履约闭环", stance["execution_hint"])
            session.close()

    def test_next_chat_prepare_uses_current_turn_stance_without_rebuilding_agent(self) -> None:
        with TemporaryDirectory() as tmp:
            session = GameSession(
                str(Path(tmp) / "npc_session_live_stance.db"),
                LLMConfig(api_key="test", base_url="http://test.invalid/v1", model="test-model"),
                content=self.content,
                verify_llm=False,
            )
            han = self.content.characters["韩爌"]
            session.dialogue_audit_client = SimpleGoalAudit(title="清查魏忠贤余党", stance="caution")
            first_user = "魏忠贤余党仍在朝中，卿可替朕议一议如何清查？"
            first_answer = "魏忠贤乱政久矣，臣愿据实奏劾其余党，但查办须有明旨。"
            _, first_prepared = session.prepare_chat_run(han, first_user)
            session.record_dialogue_after_chat(han, first_user, first_answer, first_prepared)

            session.dialogue_audit_client = lambda phase, payload: {  # type: ignore[assignment]
                "goal_decision": "none",
                "confidence": 90,
            }
            augmented, prepared = session.prepare_chat_run(han, "卿还有何补充？")

            self.assertIn("本轮动态记忆/立场", augmented)
            self.assertIn("本回合你已表明的立场/承诺", augmented)
            self.assertIn("清查魏忠贤余党", augmented)
            self.assertIn("魏忠贤（党争敌对）", prepared.behavior_brief)
            self.assertIn("政敌牵动", prepared.behavior_brief)
            session.close()

    def test_personal_chat_memory_includes_self_and_other_mentions(self) -> None:
        with TemporaryDirectory() as tmp:
            db = GameDB(str(Path(tmp) / "npc_personal_memory.db"), content=self.content)
            db.seed_static_data()
            han_alias = "韩阁老"
            db.append_chat_message("韩爌", 2, "user", "魏忠贤余党仍在朝中，卿可替朕议一议如何清查？")
            db.append_chat_message("韩爌", 2, "assistant", "臣愿据实奏劾其余党，但查办须有明旨。")
            db.append_chat_message("温体仁", 2, "assistant", f"{han_alias}执拗，若骤查阉党，恐牵动朝局。")
            db.append_chat_message("魏忠贤", 2, "assistant", f"{han_alias}借清名翻旧案，臣请陛下勿听门户之言。")
            db.append_chat_message("毕自严", 2, "assistant", "臣只论钱粮，不敢妄议边臣。")
            state = GameState(
                year=1628,
                period=2,
                turn=2,
                metrics={"国库": 100, "内库": 50, "民心": 50, "皇威": 50},
            )

            brief = build_personal_chat_memory_brief(
                self.content.characters["韩爌"],
                CourtContext(state=state, db=db),
            )

            self.assertIn("你与皇帝", brief)
            self.assertIn("魏忠贤余党", brief)
            self.assertIn("朝房风闻", brief)
            self.assertIn("召见温体仁", brief)
            self.assertIn(f"{han_alias}执拗", brief)
            self.assertIn("召见魏忠贤", brief)
            self.assertIn("关系=党争敌对", brief)
            self.assertIn("政敌牵动", brief)
            self.assertNotIn("臣只论钱粮", brief)
            db.conn.close()

    def test_search_memories_returns_personalized_recall_behavior_hint(self) -> None:
        with TemporaryDirectory() as tmp:
            db = GameDB(str(Path(tmp) / "npc_memory_search_hint.db"), content=self.content)
            db.seed_static_data()
            old_state = GameState(
                year=1628,
                period=1,
                turn=1,
                metrics={"国库": 100, "内库": 50, "民心": 50, "皇威": 50},
            )
            db.save_chapter_memory(
                old_state,
                title="上月密查阉党",
                body="上月密令韩爌密查魏忠贤余党，旧约履约未了，仍待回奏。",
                tags=["韩爌", "魏忠贤", "阉党"],
            )
            state = GameState(
                year=1628,
                period=2,
                turn=2,
                metrics={"国库": 100, "内库": 50, "民心": 50, "皇威": 50},
            )
            tools = build_minister_tools(self.content.characters["韩爌"], CourtContext(state=state, db=db))
            search = next(tool for tool in tools if getattr(tool, "__name__", "") == "search_memories")

            result = search("魏忠贤,阉党")

            self.assertIn("旧事入戏提示", result)
            self.assertIn("旧事点名你本人", result)
            self.assertIn("魏忠贤（党争敌对）", result)
            self.assertIn("旧事牵引", result)
            self.assertIn("选择性真话", result)
            db.conn.close()

    def test_web_character_payload_exposes_public_personality_inputs(self) -> None:
        with TemporaryDirectory() as tmp:
            db = GameDB(str(Path(tmp) / "npc_web_payload.db"), content=self.content)
            db.seed_static_data()
            state = GameState(
                year=1628,
                period=2,
                turn=2,
                metrics={"国库": 100, "内库": 50, "民心": 50, "皇威": 50},
            )
            game = WebGame.__new__(WebGame)
            game.session = SimpleNamespace(
                db=db,
                state=state,
                content=self.content,
                campaign_id="test",
            )
            game.favorites = set()

            payload = WebGame.public_character(game, self.content.characters["韩爌"])

            self.assertTrue(payload["style"])
            self.assertTrue(payload["personal_skills"])
            self.assertIn("network_profile", payload)
            self.assertNotIn("tiangang_profile", payload)
            self.assertNotIn("xinpan_profile", payload)
            db.conn.close()

    def test_recorded_stance_persists_half_truth_and_shielding(self) -> None:
        with TemporaryDirectory() as tmp:
            db = GameDB(str(Path(tmp) / "npc_half_truth.db"), content=self.content)
            db.seed_static_data()
            state = GameState(
                year=1628,
                period=1,
                turn=1,
                metrics={"国库": 100, "内库": 50, "民心": 50, "皇威": 50},
            )
            wen = self.content.characters["温体仁"]

            record_dialogue_effects(
                db,
                state,
                wen,
                "钱谦益建议起复东林旧臣，卿觉得可行吗？",
                "钱谦益素有文望，臣以为不可遽定，宜会审留余地，容臣再查。",
                audit_client=SimpleGoalAudit(action_kind="court_commitment", title="评价钱谦益起复建议", stance="caution"),
            )
            stance = db.list_minister_stances(minister_name="温体仁", limit=1)[0]
            speech = stance["evidence"]["speech_profile"]

            self.assertIn("misdirection", speech["speech_acts"])
            self.assertIn("shielding", speech["speech_acts"])
            self.assertEqual("半真半假", speech["truth_mode"])
            self.assertIn("话术不实", stance["risk_tags_list"])
            self.assertIn("钱谦益（同门）", speech["network_pressure"]["allies"])
            db.conn.close()

    def test_next_dialogue_stance_brief_keeps_half_truth_continuity(self) -> None:
        with TemporaryDirectory() as tmp:
            db = GameDB(str(Path(tmp) / "npc_half_truth_brief.db"), content=self.content)
            db.seed_static_data()
            state = GameState(
                year=1628,
                period=1,
                turn=1,
                metrics={"国库": 100, "内库": 50, "民心": 50, "皇威": 50},
            )
            wen = self.content.characters["温体仁"]
            record_dialogue_effects(
                db,
                state,
                wen,
                "钱谦益建议起复东林旧臣，卿觉得可行吗？",
                "钱谦益素有文望，臣以为不可遽定，宜会审留余地，容臣再查。",
                audit_client=SimpleGoalAudit(action_kind="court_commitment", title="评价钱谦益起复建议", stance="caution"),
            )

            brief = build_stance_brief(wen, CourtContext(state=state, db=db))

            self.assertIn("后续口径", brief)
            self.assertIn("半真半假", brief)
            self.assertIn("不要自曝底牌", brief)
            self.assertIn("继续留余地", brief)
            self.assertIn("钱谦益（同门）", brief)
            db.conn.close()


if __name__ == "__main__":
    unittest.main()
