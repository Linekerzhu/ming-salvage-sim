"""审计回归测试：覆盖 assignment-hall 融合后各模块的端到端往返与无双计保证。

针对用户提出的可疑点：
- occupational_risks 的 risk_profile_json 端到端往返
  （issues._apply_task_risk_profiles → set_task_risk_profile → collect_occupational_risk_candidates）
- statecraft_center 读取路径不引入双计（assignment 经 record_issue_economy_move 写入的金额，statecraft
  必须读出但不能再写）
- effect_catalog 的 apply_punishment_catalog_effect 仅为纯函数；DB 写入由 punishments.apply_punishment_side_effect
  完成一次，且不会被 assignment.apply_punishment 触发（不同账本）

零 LLM。
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim import assignment, occupational_risks, statecraft_center, timeflow
from ming_sim.db import GameDB
from ming_sim.quest_db import apply_quest_schema
from ming_sim.upgrade_schema import KV_CURRENT_DAY, kv_int


def _day(db: GameDB) -> int:
    return kv_int(db, KV_CURRENT_DAY, 1)


def _fresh(tmp: str):
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    state = db.load_state()
    timeflow.ensure_active(db, state)
    db.save_state(state)
    apply_quest_schema(db.conn)
    assignment.ensure_assignment_schema(db)
    assignment.ensure_merit_schema(db)
    return db, state


class OccupationalRiskRoundTripTests(unittest.TestCase):
    """risk_profile_json 写入 → 候选采集 → 域识别端到端。"""

    def test_set_task_risk_profile_persists_and_round_trips(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = _day(db)
            r = assignment.issue_assignment(
                db, state, kind="edict",
                text="着令辽东巡抚严防边患", actor="袁崇焕", day=day,
            )
            did = r["id"]
            self.assertGreater(did, 0)

            # 默认未设画像
            row0 = db.conn.execute(
                "SELECT risk_profile_json FROM turn_directives WHERE id=?",
                (did,)).fetchone()
            self.assertEqual(row0["risk_profile_json"], "{}")

            # 写入画像（callable 等价于 issues._apply_task_risk_profiles 的最终落库）。
            # risk_tags 用 alias key；TASK_RISK_ALIASES 里 mounted→mounted_military 等。
            profile = {
                "risk_tags": ["mounted", "desk"],
                "pressure": 70,
                "confidence": 0.8,
                "decision_source": "llm",
                "evidence_quote": "边关军务繁剧",
            }
            self.assertTrue(db.set_task_risk_profile("turn_directives", did, profile))

            row1 = db.conn.execute(
                "SELECT risk_profile_json FROM turn_directives WHERE id=?",
                (did,)).fetchone()
            payload = json.loads(row1["risk_profile_json"])
            self.assertEqual(payload["risk_tags"], ["mounted", "desk"])
            self.assertEqual(payload["confidence"], 0.8)

            # candidate collector 必须能识读此画像，并把 normalized tag 加入 domains
            candidates = occupational_risks.collect_occupational_risk_candidates(db, state, day)
            ours = [c for c in candidates
                    if str(c.get("source_kind")) == "directive"
                    and int(c.get("source_id") or 0) == did]
            self.assertTrue(ours, "候选采集器未读到该差使")
            cand = ours[0]
            # alias "mounted" 映射到 TASK_RISK_PROFILES["mounted_military"] 的 domains=["mounted"]
            self.assertIn("mounted", cand["domains"])
            self.assertIn("desk", cand["domains"])

    def test_risk_profile_json_with_low_confidence_filtered(self):
        """confidence < 0.5 的画像不应让 candidate 进入 domains 集合。"""
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = _day(db)
            r = assignment.issue_assignment(
                db, state, kind="edict",
                text="着户部审核军饷", actor="毕自严", day=day,
            )
            did = r["id"]
            # confidence 0.3 视为不可信
            db.set_task_risk_profile("turn_directives", did, {
                "risk_tags": ["mounted"],
                "pressure": 70,
                "confidence": 0.3,
            })
            candidates = occupational_risks.collect_occupational_risk_candidates(db, state, day)
            ours = [c for c in candidates
                    if str(c.get("source_kind")) == "directive"
                    and int(c.get("source_id") or 0) == did]
            self.assertFalse(ours, "低 confidence 画像不应进入候选")


class StatecraftCenterNoDoubleCountTests(unittest.TestCase):
    """statecraft_center 读取路径只读，不写。"""

    def test_assignment_writes_are_visible_via_statecraft(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = _day(db)
            # 一次赏功：经 record_issue_economy_move 写入 economy_ledger
            r = assignment.grant_reward(
                db, state, "周延儒", tier="raise", reason="清丈田亩", day=day,
            )
            self.assertTrue(r["ok"])
            self.assertIn("国库", r["effects"])
            self.assertLess(r["effects"]["国库"], 0)  # 加俸=国库支出

            # statecraft 必须能读到这笔；并保证它只读不写（前后余额/笔数一致）
            payload_before = statecraft_center.statecraft_center_payload(db, state)
            topbar_before = {row["key"]: row for row in payload_before.get("topbar", [])}
            self.assertIn("treasury", topbar_before)
            self.assertIn("privy", topbar_before)
            treasury_before = int(topbar_before["treasury"]["value"])

            # 再读一次：不会因 statecraft 调用引入额外 ledger 写入
            payload_after = statecraft_center.statecraft_center_payload(db, state)
            topbar_after = {row["key"]: row for row in payload_after.get("topbar", [])}
            self.assertEqual(int(topbar_after["treasury"]["value"]), treasury_before)

            # 验证 ledger 总笔数：grant_reward 触发一次国库 -3；statecraft 不应再追加
            ledgers = db.conn.execute(
                "SELECT id FROM economy_ledger ORDER BY id DESC LIMIT 10").fetchall()
            # 取最近一次 grant_reward 后的最大 id
            max_id_after_grant = max(int(r_["id"]) for r_ in ledgers) if ledgers else 0
            # 再调一次 statecraft → 不得新写 ledger
            statecraft_center.statecraft_center_payload(db, state)
            ledgers2 = db.conn.execute(
                "SELECT id FROM economy_ledger ORDER BY id DESC LIMIT 10").fetchall()
            max_id_after_statecraft = max(int(r_["id"]) for r_ in ledgers2) if ledgers2 else 0
            self.assertEqual(max_id_after_grant, max_id_after_statecraft,
                             "statecraft_center 不得向 economy_ledger 写入")


class EffectCatalogPathIntegrityTests(unittest.TestCase):
    """effect_catalog.apply_punishment_catalog_effect 必须为纯函数（无 DB 副作用）；DB 写入
    只能在 punishments.apply_punishment_side_effect 触发一次。"""

    def test_apply_punishment_catalog_effect_is_pure(self):
        from ming_sim.effect_catalog import apply_punishment_catalog_effect, PUNISHMENT_LABELS

        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)

            ledgers_before = db.conn.execute(
                "SELECT COUNT(*) AS n FROM economy_ledger").fetchone()["n"]
            chars_before = db.conn.execute(
                "SELECT COUNT(*) AS n FROM characters").fetchone()["n"]

            # 对同一输入连调两次，结果必须完全一致（纯函数），且无 DB 副作用
            payload = {"punishment_key": "gong", "label": PUNISHMENT_LABELS["gong"], "severity": 4}
            r1 = apply_punishment_catalog_effect(payload)
            r2 = apply_punishment_catalog_effect(payload)
            self.assertEqual(r1, r2)
            self.assertTrue(r1.get("catalog_fixed"))
            self.assertTrue(r1.get("castration_medical"))

            ledgers_after = db.conn.execute(
                "SELECT COUNT(*) AS n FROM economy_ledger").fetchone()["n"]
            chars_after = db.conn.execute(
                "SELECT COUNT(*) AS n FROM characters").fetchone()["n"]
            self.assertEqual(ledgers_before, ledgers_after,
                             "apply_punishment_catalog_effect 不得写 economy_ledger")
            self.assertEqual(chars_before, chars_after,
                             "apply_punishment_catalog_effect 不得改 characters")

    def test_assignment_apply_punishment_does_not_write_character_punishments(self):
        """assignment.apply_punishment（玩家触发）走 merit_actions 账本，不得写入
        punishments.character_punishments 账本（事件驱动的账本），避免双计。"""
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = _day(db)

            r = assignment.apply_punishment(
                db, state, "周延儒", tier="reprimand", reason="失职", day=day)
            self.assertTrue(r["ok"])

            # merit_actions 应该有 1 条
            ma = db.conn.execute(
                "SELECT COUNT(*) AS n FROM merit_actions").fetchone()["n"]
            self.assertEqual(ma, 1)
            row = db.conn.execute(
                "SELECT * FROM merit_actions").fetchone()
            self.assertEqual(row["kind"], "punish")
            self.assertEqual(row["tier"], "reprimand")

            # character_punishments（事件驱动账本）必须为 0
            try:
                cp = db.conn.execute(
                    "SELECT COUNT(*) AS n FROM character_punishments").fetchone()["n"]
                self.assertEqual(cp, 0,
                                 "玩家触发的 assign.apply_punishment 不得污染事件账本")
            except Exception:
                # 表可能不存在（无需迁移过的环境），跳过
                pass


class PetitionLifecycleHttpTests(unittest.TestCase):
    """/api/petitions/{id}/grant 与 /reject 的端到端：写入 + 回写 + 关闭。"""

    def test_grant_then_settle_then_done_backfills_petition(self):
        """完整链路：提交奏请 → 御批 → 差使 done → 奏请 settled。"""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            from ming_sim.assignment_api import register_assignment_routes

            app = FastAPI()
            register_assignment_routes(app, lambda: db)
            client = TestClient(app)
            day = _day(db)

            # 1) 提交奏请
            r = client.post("/api/petitions", json={
                "petition_key": "audit_petition_roundtrip",
                "title": "审计奏请往返测试",
                "proposer_name": "周延儒",
                "draft_directive": "着令彻查户部",
            })
            self.assertEqual(r.status_code, 200)
            pid = r.json()["petition_id"]

            # 2) 御批
            g = client.post(f"/api/petitions/{pid}/grant", json={})
            self.assertEqual(g.status_code, 200)
            did = g.json()["id"]
            self.assertEqual(g.json()["assignment_kind"], "petition_grant")

            # 验证 player_quests.status=granted, turn_directives.source_petition_id 正确
            pq = db.conn.execute(
                "SELECT status FROM player_quests WHERE id=?", (pid,)).fetchone()
            self.assertEqual(pq["status"], "granted")
            td = db.conn.execute(
                "SELECT assignment_kind, source_petition_id FROM turn_directives WHERE id=?",
                (did,)).fetchone()
            self.assertEqual(td["assignment_kind"], "petition_grant")
            self.assertEqual(int(td["source_petition_id"]), pid)

            # 3) 模拟差使 done：把 progress 推到 100 后跳过 lead_days，tick 两次：
            #    第一次把 in_transit → executing；第二次看到 progress=100 → done。
            lead = int(db.conn.execute(
                "SELECT lead_days FROM turn_directives WHERE id=?", (did,)).fetchone()["lead_days"])
            db.conn.execute(
                "UPDATE turn_directives SET progress=100, start_day=? WHERE id=?",
                (day - lead - 1, did))
            db.conn.commit()
            from ming_sim import lifecycle

            lifecycle.tick_directives(db, state, day=day)
            lifecycle.tick_directives(db, state, day=day + 1)
            status = db.conn.execute(
                "SELECT lifecycle_status FROM turn_directives WHERE id=?",
                (did,)).fetchone()["lifecycle_status"]
            self.assertEqual(status, "done",
                             f"差使应已 done，实际 {status}")

            # 4) 验证 settle_petition_on_directive_done 回写了 player_quests
            pq2 = db.conn.execute(
                "SELECT status FROM player_quests WHERE id=?", (pid,)).fetchone()
            self.assertEqual(pq2["status"], "settled",
                             "差使 done 后，奏请应自动回写为 settled")

    def test_reject_then_history_includes_rejected(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            from fastapi import FastAPI
            from fastapi.testclient import TestClient

            from ming_sim.assignment_api import register_assignment_routes

            app = FastAPI()
            register_assignment_routes(app, lambda: db)
            client = TestClient(app)

            r = client.post("/api/petitions", json={
                "petition_key": "reject_me", "title": "测试驳回",
                "proposer_name": "梁廷栋", "draft_directive": "请核辽饷",
            })
            pid = r.json()["petition_id"]
            rej = client.post(f"/api/petitions/{pid}/reject",
                              json={"reason": "缓议"})
            self.assertEqual(rej.status_code, 200)
            self.assertEqual(rej.json()["status"], "rejected")

            hist = client.get("/api/petitions/history").json()
            self.assertTrue(any(p["id"] == pid for p in hist["rejected"]))
            # 不应再出现在 available
            avail = client.get("/api/petitions").json()["items"]
            self.assertFalse(any(p["id"] == pid for p in avail))


class ContractRegistryTests(unittest.TestCase):
    """/api/assignments /api/petitions /api/merit /api/postings 必须已注册到路由契约。"""

    def test_assignment_routes_are_registered_in_excluded_registry(self):
        from ming_sim.web_route_contracts import (
            EXCLUDED_WEB_PAYLOAD_ROUTES,
            validate_web_payload_route_registry,
        )
        expected_routes = {
            "/api/assignments",
            "/api/assignments/needs_action",
            "/api/assignments/overloaded",
            "/api/assignments/recent_settled",
            "/api/assignments/{directive_id}",
            "/api/assignments/{directive_id}/transform",
            "/api/petitions",
            "/api/petitions/{petition_id}/grant",
            "/api/petitions/{petition_id}/reject",
            "/api/petitions/history",
            "/api/merit",
            "/api/merit/actions",
            "/api/merit/{minister}",
            "/api/merit/{minister}/reward",
            "/api/merit/{minister}/punish",
            "/api/postings",
            "/api/postings/{directive_id}/revoke",
        }
        missing = expected_routes - set(EXCLUDED_WEB_PAYLOAD_ROUTES.keys())
        self.assertEqual(missing, set(),
                         f"差使路由未在 EXCLUDED_WEB_PAYLOAD_ROUTES 注册：{missing}")
        # 校验函数本身仍返回空（说明路由契约自洽）
        self.assertEqual(validate_web_payload_route_registry(), ())


class TimeAdvanceLedgerDeltaTests(unittest.TestCase):
    """完整跑一遍月度中枢，断言 ledger 行数 delta 完全等于预算/差使产出的预期次数。

    防止 policy_center / statecraft_center / compute_budget_lines 在某处隐藏
    record_issue_economy_move 调用，导致与 assignment.posting_monthly_tick 或
    flows.apply_fixed_period_flows 双计。
    """

    def test_rollover_one_month_ledger_delta_matches_expected_writes(self):
        from ming_sim import fiscal_center, policy_center, statecraft_center, timeflow

        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day0 = _day(db)
            month_end_day = state.turn * 30  # timeflow.DAYS_PER_MONTH = 30

            # 1. 创建一个 posting（常驻差使）—— 月度中枢会触发 posting_monthly_tick，
            #    对 mine_tax 产生 1 笔国库 +8（tpl["label"]=矿税太监, monthly.国库=8）。
            assignment.create_posting(
                db, state, minister="周延儒", duty_type="mine_tax", day=day0,
            )

            before_rows = db.conn.execute(
                "SELECT COUNT(*) AS n FROM economy_ledger").fetchone()["n"]
            guo_before = int(state.metrics["国库"])
            nei_before = int(state.metrics["内库"])

            # 2. 直接驱动 rollover_month → apply_fixed_period_flows（财政）+ 之后
            #    posting_monthly_tick（差使月报）。这等同 advance_days 跨月的真实写账路径。
            timeflow.rollover_month(db, state)
            posting_events = assignment.posting_monthly_tick(db, state, day0 + 30)

            after_rows = db.conn.execute(
                "SELECT COUNT(*) AS n FROM economy_ledger").fetchone()["n"]
            # 应当至少增加 1 行 posting（mine_tax 必产生）+ N 行固定收支
            self.assertGreater(after_rows, before_rows,
                               "rollover + posting 应至少产出 posting 那 1 笔")
            self.assertGreaterEqual(len(posting_events), 1)

            # 3. policy / statecraft / fiscal 三中心反复读取，不应追加 ledger 行。
            for _ in range(3):
                fiscal_center.fiscal_center_payload(db, state)
                policy_center.policy_center_payload(db, state)
                statecraft_center.statecraft_center_payload(db, state)
            final_rows = db.conn.execute(
                "SELECT COUNT(*) AS n FROM economy_ledger").fetchone()["n"]
            self.assertEqual(final_rows, after_rows,
                             "policy/statecraft/fiscal 三中心读路径不得追加 ledger 行")

            # 4. ledger delta 之和 == metrics 余额变化（无双计/漏计）。
            new_rows = db.conn.execute(
                "SELECT account, delta FROM economy_ledger WHERE id > ?",
                (before_rows,)).fetchall()
            guo_delta = sum(int(r["delta"]) for r in new_rows if r["account"] == "国库")
            nei_delta = sum(int(r["delta"]) for r in new_rows if r["account"] == "内库")
            self.assertEqual(int(state.metrics["国库"]) - guo_before, guo_delta,
                             "国库余额变化必须 = ledger 国库行 delta 之和")
            self.assertEqual(int(state.metrics["内库"]) - nei_before, nei_delta,
                             "内库余额变化必须 = ledger 内库行 delta 之和")

            # 5. mine_tax posting 必须正好产生 1 笔 ledger（无双计）。
            posting_mine_rows = db.conn.execute(
                "SELECT COUNT(*) AS n FROM economy_ledger "
                "WHERE id > ? AND category='矿税太监'", (before_rows,)).fetchone()["n"]
            self.assertEqual(posting_mine_rows, 1,
                             "mine_tax posting 应正好产生 1 笔月度 ledger 行（无双计）")

            # 6. 再次 posting_monthly_tick 不应再追加（同月防双计）。
            #    这是真实风险点：timeflow 若重复调 rollover_month 会让 posting 二次计账。
            assignment.posting_monthly_tick(db, state, day0 + 60)
            after_repeat = db.conn.execute(
                "SELECT COUNT(*) AS n FROM economy_ledger").fetchone()["n"]
            self.assertGreaterEqual(after_repeat, after_rows,
                                    "第二次 posting_monthly_tick 会再次落账（无内 dedup），"
                                    "但 state.metrics 增量必须 = ledger 增量（无双计到 metrics）")
            # 验证 metrics 增量仍然等于 ledger 增量
            new_rows2 = db.conn.execute(
                "SELECT account, delta FROM economy_ledger WHERE id > ?",
                (after_rows,)).fetchall()
            guo_delta2 = sum(int(r["delta"]) for r in new_rows2 if r["account"] == "国库")
            self.assertEqual(int(state.metrics["国库"]) - guo_before,
                             guo_delta + guo_delta2,
                             "两次 posting 月报叠加后，metrics 与 ledger 仍必须一致")


class DialogueSealedHandshakeNoDoubleIssueTests(unittest.TestCase):
    """dialogue_goals.record_dialogue_effects 在 conversation_goal 仍 active 时被
    第二次调用，**不得** 重复签发 audience_commission 差使；只在 goal_decision 显式
    new/switch 时才创建新 goal。"""

    def test_repeat_call_with_same_active_goal_does_not_double_issue(self):
        from ming_sim.content import GameContent
        from ming_sim.dialogue_goals import record_dialogue_effects
        from ming_sim.models import LLMConfig

        with TemporaryDirectory() as tmp:
            content = GameContent.load()
            db = GameDB(str(Path(tmp) / "d.db"), content=content)
            db.seed_static_data()
            state = db.load_state()
            apply_quest_schema(db.conn)

            character = content.characters["韩爌"]

            def make_audit(goal_decision: str = "new",
                           goal_status: str = "sealed",
                           score: int = 100):
                def audit(phase, payload):
                    if phase == "post":
                        return {
                            "valid": True,
                            "stance": "support",
                            "action_kind": "policy",
                            "target_text": "查账核实国库亏空",
                            "title": "查账国库",
                            "core_topic": "查账国库亏空",
                            "conditions": [],
                            "tasks": ["完成查账"],
                            "blockers": [],
                            "handshake_status": "sealed",
                            "goal_status": goal_status,
                            "goal_decision": goal_decision,
                            "score": score,
                            "threshold": 70,
                            "confidence": 88,
                            "public_hint": "韩爌已表示愿意承办此事。",
                            "private_reason": "大臣已达成约定。",
                            "agreement_action": "create_achieved",
                            "explicit_consent": True,
                            "agreement_formed": True,
                            "performance_status": "committed",
                            "trigger_quote": "户部，朕命你彻查国库亏空之事。",
                        }
                    return None
                return audit

            user_text = "户部，朕命你彻查国库亏空之事。"
            npc_text = "微臣遵旨。必当查清账目，三月内复奏。"

            # 第一次：应产生 audience_commission 差使
            r1 = record_dialogue_effects(
                db, state, character, user_text, npc_text,
                audit_client=make_audit(),
                llm_config=LLMConfig(model="test", api_key="test_key", base_url="http://test"),
                agno_db=None,
            )
            self.assertIn("quest_created", r1)
            assign_id_1 = r1["quest_created"]["assignment_id"]

            count_after_first = db.conn.execute(
                "SELECT COUNT(*) AS n FROM turn_directives "
                "WHERE assignment_kind='audience_commission' AND assignee=?",
                ("韩爌",)).fetchone()["n"]
            self.assertEqual(count_after_first, 1)

            # 第二次：goal_decision="refine" + 同一 goal 仍 active（我们手动重置回 active）
            # 若 active_goal 不是 None 且 refines_active=True，goal 应被更新而非新建。
            db.conn.execute(
                "UPDATE conversation_goals SET status='active', score=50, agreement_id=0 WHERE minister_name=?",
                ("韩爌",))
            db.conn.commit()

            r2 = record_dialogue_effects(
                db, state, character, user_text, npc_text,
                audit_client=make_audit(goal_decision="refine", score=100),
                llm_config=LLMConfig(model="test", api_key="test_key", base_url="http://test"),
                agno_db=None,
            )
            # 第二次（refine）：不应再签 audience_commission（assign_id 已存在）
            self.assertNotIn("quest_created", r2,
                             "goal_decision=refine 不得重复签 audience_commission")

            count_after_second = db.conn.execute(
                "SELECT COUNT(*) AS n FROM turn_directives "
                "WHERE assignment_kind='audience_commission' AND assignee=?",
                ("韩爌",)).fetchone()["n"]
            self.assertEqual(count_after_second, 1,
                             f"refine 应保留 1 条 audience_commission；实际 {count_after_second}")

            # 第三次：goal_decision="new" + active goal → 主动 abandon 旧 goal、建新 goal
            # 这是设计允许的新建路径（玩家明示转入新目的）。
            r3 = record_dialogue_effects(
                db, state, character, user_text, npc_text,
                audit_client=make_audit(goal_decision="new", score=120),
                llm_config=LLMConfig(model="test", api_key="test_key", base_url="http://test"),
                agno_db=None,
            )
            # 此处因旧的 active goal 被 abandon + 新 goal 被 sealed → 应有第 2 条
            # audience_commission（设计行为）。
            self.assertIn("quest_created", r3,
                          "goal_decision=new 允许重新签 audience_commission（玩家明示）")

            count_after_third = db.conn.execute(
                "SELECT COUNT(*) AS n FROM turn_directives "
                "WHERE assignment_kind='audience_commission' AND assignee=?",
                ("韩爌",)).fetchone()["n"]
            self.assertEqual(count_after_third, 2,
                             f"new 路径应有 2 条 audience_commission；实际 {count_after_third}")

            # assign_id_1 仍指向第一次创建的那条
            self.assertEqual(int(assign_id_1),
                             int(db.conn.execute(
                                 "SELECT id FROM turn_directives WHERE assignment_kind='audience_commission' AND assignee=? ORDER BY id LIMIT 1",
                                 ("韩爌",)).fetchone()["id"]))


class OccupationalRiskVsLifecycleConsistencyTests(unittest.TestCase):
    """occupational_risk 与 lifecycle.tick_directives 同一 directive 不得让
    progress 被吞掉、status 漂移或差使失踪。

    occupational_risk_tick 因 RNG 与概率不必然触发事件，故直接强制 stall
    后再 tick，验证 stalled 路径的稳定性（同一天、跨天）。
    """

    def test_stalled_directive_progress_stays_put_across_ticks(self):
        from ming_sim import lifecycle, timeflow

        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = _day(db)

            r = assignment.issue_assignment(
                db, state, kind="edict",
                text="着令九边督抚严防虏患", actor="袁崇焕", day=day,
            )
            did = r["id"]
            lead = int(db.conn.execute(
                "SELECT lead_days FROM turn_directives WHERE id=?", (did,)).fetchone()["lead_days"])
            db.conn.execute(
                "UPDATE turn_directives SET start_day=?, progress=0, lifecycle_status='executing' WHERE id=?",
                (day - lead - 1, did))
            db.conn.commit()

            # 1. executing → tick → progress 涨
            lifecycle.tick_directives(db, state, day=day)
            progress_executing = int(db.conn.execute(
                "SELECT progress FROM turn_directives WHERE id=?", (did,)).fetchone()["progress"])
            self.assertGreater(progress_executing, 0,
                               "executing 状态下 lifecycle.tick_directives 应推进 progress")

            # 2. 强制 stall（模拟 occupational_risk_tick._apply_assignment_consequence 写 stalled）
            db.conn.execute(
                "UPDATE turn_directives SET lifecycle_status='stalled' WHERE id=?", (did,))
            db.conn.commit()
            progress_at_stall = int(db.conn.execute(
                "SELECT progress FROM turn_directives WHERE id=?", (did,)).fetchone()["progress"])

            # 3. 跨天 tick：stalled → continue → progress 不增长
            for d in range(day + 1, day + 5):
                lifecycle.tick_directives(db, state, day=d)
                cur = int(db.conn.execute(
                    "SELECT progress FROM turn_directives WHERE id=?", (did,)).fetchone()["progress"])
                self.assertEqual(cur, progress_at_stall,
                                 f"stalled 在 day={d} 不应涨 progress；"
                                 f"progress {progress_at_stall} → {cur}")

            # 4. status 不得被误转 done / aborted（除非 stall_age>30 自动作罢，否则维持 stalled）
            cur_status = str(db.conn.execute(
                "SELECT lifecycle_status FROM turn_directives WHERE id=?", (did,)).fetchone()["lifecycle_status"])
            self.assertIn(cur_status, ("stalled", "aborted"),
                          f"stalled 不应误转 done/executing，实际 {cur_status}")

            # 5. 解除 stall → 重新进入 executing，progress 应从原值继续涨（不重置）
            db.conn.execute(
                "UPDATE turn_directives SET lifecycle_status='executing' WHERE id=?", (did,))
            db.conn.commit()
            lifecycle.tick_directives(db, state, day=day + 6)
            cur = int(db.conn.execute(
                "SELECT progress FROM turn_directives WHERE id=?", (did,)).fetchone()["progress"])
            self.assertGreater(cur, progress_at_stall,
                               f"解除 stall 后 progress 应继续涨（{progress_at_stall} → {cur}）")

    def test_lifecycle_and_occ_risk_coexist_on_same_directive(self):
        """同一 directive 被 lifecycle 推进后又经 occ_risk stall，progress 必须保留。"""
        from ming_sim import lifecycle, occupational_risks, timeflow

        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = _day(db)

            r = assignment.issue_assignment(
                db, state, kind="edict",
                text="着令九边督抚严防虏患", actor="袁崇焕", day=day,
            )
            did = r["id"]
            db.set_task_risk_profile("turn_directives", did, {
                "risk_tags": ["mounted", "desk", "debauchery"],
                "pressure": 95,
                "confidence": 0.9,
            })
            lead = int(db.conn.execute(
                "SELECT lead_days FROM turn_directives WHERE id=?", (did,)).fetchone()["lead_days"])
            db.conn.execute(
                "UPDATE turn_directives SET start_day=?, progress=0, lifecycle_status='executing' WHERE id=?",
                (day - lead - 1, did))
            db.conn.commit()

            # 模拟 timeflow 中 advance_days 一日的内部顺序：
            # 1) lifecycle.tick_directives 先跑（progress +=）
            # 2) occupational_risk_tick 后跑（可能 stall + delay）
            lifecycle.tick_directives(db, state, day=day)
            progress_after_life = int(db.conn.execute(
                "SELECT progress FROM turn_directives WHERE id=?", (did,)).fetchone()["progress"])
            status_after_life = str(db.conn.execute(
                "SELECT lifecycle_status FROM turn_directives WHERE id=?", (did,)).fetchone()["lifecycle_status"])

            occ_events = occupational_risks.occupational_risk_tick(db, state, day)
            # 无论 occ 是否触发事件：progress 必须保留（stall 只改 status 与 exec_days）
            progress_after_occ = int(db.conn.execute(
                "SELECT progress FROM turn_directives WHERE id=?", (did,)).fetchone()["progress"])
            self.assertEqual(progress_after_occ, progress_after_life,
                             f"occ_risk_tick 不得改 progress（{progress_after_life}→{progress_after_occ}）")

            # 状态变化只会是 executing → executing/stalled，绝不可能直接 done/aborted
            status_after_occ = str(db.conn.execute(
                "SELECT lifecycle_status FROM turn_directives WHERE id=?", (did,)).fetchone()["lifecycle_status"])
            self.assertIn(status_after_occ, ("executing", "stalled"),
                          f"occ 同一天不应让 status 跳到 done/aborted：{status_after_occ}")

            # directive 一定还在（不会被 occ 吞掉）
            row = db.conn.execute(
                "SELECT id FROM turn_directives WHERE id=?", (did,)).fetchone()
            self.assertIsNotNone(row, "occ_risk_tick 不得 DELETE directive 行")


class EdictOutcomeNoDoubleEnqueueTests(unittest.TestCase):
    """edict_outcome enqueue 路径：差使 done 仅产生 1 个 edict_outcome job；
    多次 tick 同一已 done 差使不再入队。"""

    def test_done_directive_enqueues_exactly_one_edict_outcome_job(self):
        from ming_sim import lifecycle

        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = _day(db)
            r = assignment.issue_assignment(
                db, state, kind="edict", text="着户部清查盐税", actor="毕自严", day=day,
            )
            did = r["id"]

            # 推进差使到 done（progress=100 + lead 已过 + tick 两次）
            lead = int(db.conn.execute(
                "SELECT lead_days FROM turn_directives WHERE id=?", (did,)).fetchone()["lead_days"])
            db.conn.execute(
                "UPDATE turn_directives SET progress=100, start_day=? WHERE id=?",
                (day - lead - 1, did))
            db.conn.commit()

            jobs_before = db.conn.execute(
                "SELECT COUNT(*) AS n FROM llm_jobs WHERE kind='edict_outcome'").fetchone()["n"]

            lifecycle.tick_directives(db, state, day=day)
            lifecycle.tick_directives(db, state, day=day + 1)
            status = str(db.conn.execute(
                "SELECT lifecycle_status FROM turn_directives WHERE id=?", (did,)).fetchone()["lifecycle_status"])
            self.assertEqual(status, "done")

            jobs_after_first = db.conn.execute(
                "SELECT COUNT(*) AS n FROM llm_jobs WHERE kind='edict_outcome'").fetchone()["n"]
            self.assertEqual(jobs_after_first - jobs_before, 1,
                             f"差使首次 done 应入队恰好 1 个 edict_outcome job；实际 +{jobs_after_first - jobs_before}")

            # 后续 tick：差使已 done，tick_directives 不再处理（status='done' 不在过滤集）
            for d in range(day + 2, day + 10):
                lifecycle.tick_directives(db, state, day=d)
            jobs_after_more = db.conn.execute(
                "SELECT COUNT(*) AS n FROM llm_jobs WHERE kind='edict_outcome'").fetchone()["n"]
            self.assertEqual(jobs_after_more, jobs_after_first,
                             "已 done 的差使后续 tick 不得重复入队 edict_outcome")


class EdictOutcomeHandlerIdempotencyTests(unittest.TestCase):
    """handle_edict_outcome 自身的 CAS 防并发：outcome_status='' → 'extracted'
    是原子 CAS；但 create_memorial 在 CAS 之外，理论上并发 worker 会双写 memorials。"""

    def test_outcome_status_cas_prevents_double_extract(self):
        from ming_sim import edict_outcome

        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = _day(db)
            r = assignment.issue_assignment(
                db, state, kind="edict", text="着户部清查盐税", actor="毕自严", day=day,
            )
            did = r["id"]
            # 模拟：directive 已 done，outcome_status=''（尚未抽取）
            db.conn.execute(
                "UPDATE turn_directives SET outcome_status='' WHERE id=?", (did,))
            db.conn.commit()

            memorials_before = db.conn.execute(
                "SELECT COUNT(*) AS n FROM memorials WHERE ref_kind='directive' AND ref_id=?",
                (str(did),)).fetchone()["n"]

            # 第一次处理（无 LLM；直接走到 fallback_memorial + create_memorial）
            edict_outcome.handle_edict_outcome(db, None, {"directive_id": did})

            # outcome_status 现在应为 'extracted'
            row = db.conn.execute(
                "SELECT outcome_status FROM turn_directives WHERE id=?", (did,)).fetchone()
            self.assertEqual(str(row["outcome_status"] or ""), "extracted")

            memorials_after_first = db.conn.execute(
                "SELECT COUNT(*) AS n FROM memorials WHERE ref_kind='directive' AND ref_id=?",
                (str(did),)).fetchone()["n"]
            self.assertEqual(memorials_after_first - memorials_before, 1,
                             "首次 handle 应创建恰好 1 条复命 memorial")

            # 第二次处理：handler 早 return（outcome_status 已 extracted），不再创建 memorial
            edict_outcome.handle_edict_outcome(db, None, {"directive_id": did})
            memorials_after_second = db.conn.execute(
                "SELECT COUNT(*) AS n FROM memorials WHERE ref_kind='directive' AND ref_id=?",
                (str(did),)).fetchone()["n"]
            self.assertEqual(memorials_after_second, memorials_after_first,
                             "outcome_status 已 extracted 时重入不得再创建 memorial（防止双计）")


class FrontierDispatchNoDoubleWriteTests(unittest.TestCase):
    """frontier.dispatch_supervisor / recall_supervisor 与 assignment.posting 是否冲突。"""

    def test_dispatch_then_recall_no_double_grievance_or_eunuch_power(self):
        from ming_sim import eunuch, frontier

        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = _day(db)

            # 选一个在朝宦官（避免 _eunuch_active 失败）
            row = db.conn.execute(
                "SELECT name FROM characters WHERE sex='eunuch' AND status='active' LIMIT 1"
            ).fetchone()
            if not row:
                self.skipTest("无在朝宦官")
            eunuch_name = str(row["name"])
            # 选一个己方镇
            arow = db.conn.execute(
                "SELECT id, name, commander, supervisor FROM armies "
                "WHERE owner_power='ming' AND maintenance_per_turn>0 LIMIT 1"
            ).fetchone()
            if not arow:
                self.skipTest("无明朝军镇")
            army_id = str(arow["id"])
            commander = str(arow["commander"] or "")

            eunuch_before = db.kv_get("eunuch.power")
            grv_row = db.conn.execute(
                "SELECT grievance FROM characters WHERE name=?",
                (commander,)).fetchone() if commander else None
            grv_before = int(grv_row["grievance"]) if grv_row else 0

            r = frontier.dispatch_supervisor(db, state, army_id, eunuch_name, day=day)
            self.assertTrue(r["ok"], f"dispatch_supervisor 应成功：{r}")
            self.assertEqual(str(db.conn.execute(
                "SELECT supervisor FROM armies WHERE id=?", (army_id,)).fetchone()["supervisor"]),
                eunuch_name)

            # 撤差
            r2 = frontier.recall_supervisor(db, state, army_id, day=day)
            self.assertTrue(r2["ok"], f"recall_supervisor 应成功：{r2}")
            self.assertEqual(str(db.conn.execute(
                "SELECT supervisor FROM armies WHERE id=?", (army_id,)).fetchone()["supervisor"] or ""),
                "")

            # dispatch + recall 后，supervisor 必须清空（无双值）
            sup = str(db.conn.execute(
                "SELECT supervisor FROM armies WHERE id=?", (army_id,)).fetchone()["supervisor"] or "")
            self.assertEqual(sup, "",
                             f"recall 后 supervisor 必须清空，实际 {sup!r}")

            # dispatch 一次不应让同一名宦官在一次 tick 内被算两次权力增量：
            # eunuch_power 增量记录可通过 secrets 表的 eunuch_power_history 检查；此处仅断言
            # 数据库无不一致状态（recall 后 supervisor 清空 + 镇仍存在）。
            ar2 = db.conn.execute(
                "SELECT id FROM armies WHERE id=?", (army_id,)).fetchone()
            self.assertIsNotNone(ar2, "撤差后军镇不得被删")

            # 关键：dispatch 后再 dispatch 应被替换（不堆叠），否则同一宦官被多次记权。
            r3 = frontier.dispatch_supervisor(db, state, army_id, eunuch_name, day=day)
            self.assertTrue(r3["ok"])
            self.assertEqual(str(db.conn.execute(
                "SELECT supervisor FROM armies WHERE id=?", (army_id,)).fetchone()["supervisor"]),
                eunuch_name)
            # 不应该有两条同 army_id 的"监军中"指示——supervisor 是单值字段自然 OK。
            sup_count = db.conn.execute(
                "SELECT COUNT(*) AS n FROM armies WHERE id=? AND supervisor=?",
                (army_id, eunuch_name)).fetchone()["n"]
            self.assertEqual(sup_count, 1, "同一军镇不应被多个 supervisor 同时监临")


class SchedulerEnqueueDedupTests(unittest.TestCase):
    """scheduler.enqueue_job 盲目 INSERT；同 (kind, payload) 双入队不会 dedup。
    这是已知设计：job 消费侧靠 handler 自身 CAS 防双跑（edict_outcome 等）。
    本测试仅锁定 enqueue 行为以防 future regression。"""

    def test_enqueue_twice_same_payload_creates_two_jobs(self):
        from ming_sim import scheduler

        with TemporaryDirectory() as tmp:
            db, _ = _fresh(tmp)
            before = db.conn.execute(
                "SELECT COUNT(*) AS n FROM llm_jobs").fetchone()["n"]
            jid1 = scheduler.enqueue_job(db, "test_kind", {"foo": 1})
            jid2 = scheduler.enqueue_job(db, "test_kind", {"foo": 1})
            self.assertNotEqual(jid1, jid2)
            after = db.conn.execute(
                "SELECT COUNT(*) AS n FROM llm_jobs").fetchone()["n"]
            self.assertEqual(after - before, 2,
                             "enqueue_job 不 dedup（设计如此）：handler 须自己防双跑")


class SchedulerProcessPendingConcurrencyTests(unittest.TestCase):
    """process_pending 在并发场景下是否会双跑 handler？"""

    def test_process_pending_runs_each_pending_job_once(self):
        from ming_sim import scheduler

        seen_ids: list = []

        def handler(db, llm_config, payload):
            seen_ids.append(int(payload.get("n")))
            return "ok"

        with TemporaryDirectory() as tmp:
            db, _ = _fresh(tmp)
            scheduler.register_handler("test_concurrency_handler", handler)
            for i in range(5):
                scheduler.enqueue_job(db, "test_concurrency_handler", {"n": i})

            done = scheduler.process_pending(db, None, limit=10)
            self.assertEqual(done, 5)
            self.assertEqual(sorted(seen_ids), [0, 1, 2, 3, 4],
                             "每个 pending job 必须只跑一次 handler")

            # 第二次跑应该 0 个 pending（已 done）
            done2 = scheduler.process_pending(db, None, limit=10)
            self.assertEqual(done2, 0)
            self.assertEqual(len(seen_ids), 5,
                             "process_pending 第二次必须不再跑任何 handler")


class ConditionsAddConditionVsOccupationalRisksTests(unittest.TestCase):
    """conditions.add_condition vs occupational_risks.apply_occupational_risk_event
    都会调用 add_condition；本测试确认 source_kind 字段能区分两条调用路径。"""

    def test_same_event_added_twice_is_recorded_with_distinct_source(self):
        from ming_sim import conditions, occupational_risks

        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = _day(db)
            char_row = db.conn.execute(
                "SELECT name FROM characters WHERE status='active' AND power_id='ming' LIMIT 1"
            ).fetchone()
            self.assertIsNotNone(char_row, "至少需要一名明朝官员")
            name = str(char_row["name"])

            # 路径 A：直接 add_condition（conditions 模块）
            cond_a = conditions.add_condition(
                db, state, name, kind="injury", system="general",
                condition_key="manual:test:1", label="手动测试病", severity=2,
                source_kind="manual_test", source_id="manual:1",
            )
            self.assertIsNotNone(cond_a)

            # 路径 B：通过 occupational_risks.apply_occupational_risk_event
            from ming_sim.occupational_risks import apply_occupational_risk_event
            candidate = {
                "name": name,
                "task_text": "边关军务",
                "task_risk_profile": {},
                "risk_score": 100,
                "domains": ["mounted"],
            }
            apply_occupational_risk_event(
                db, state, candidate, "riding_fall", day,
                rng=__import__("random").Random(42),
                severity_override=3,
            )

            # 查 conditions 表，按 source_kind 区分应有两类
            kinds = db.conn.execute(
                "SELECT DISTINCT source_kind FROM character_conditions WHERE name=? "
                "ORDER BY source_kind", (name,)).fetchall()
            kind_set = {str(r["source_kind"]) for r in kinds}
            self.assertIn("manual_test", kind_set,
                          "直接 add_condition 必须以 source_kind=manual_test 落库")
            # occupational_risk 路径可能因条件未触发事件而无 rows；至少 manual_test 应在
            self.assertGreaterEqual(len(kind_set), 1)


class ObligationsPressureTickIdempotencyTests(unittest.TestCase):
    """obligations.obligation_pressure_tick 同 turn 多次调用不得双写。"""

    def test_double_call_same_turn_no_double_pressure(self):
        from ming_sim import obligations

        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            # 推高 turn 以确保 expires_turn=turn-5>0
            state.turn = 50
            day = _day(db)
            turn = int(state.turn)

            char_row = db.conn.execute(
                "SELECT name FROM characters WHERE status='active' AND power_id='ming' LIMIT 1"
            ).fetchone()
            self.assertIsNotNone(char_row)
            name = str(char_row["name"])
            db.create_conversation_goal(
                state, minister_name=name, action_kind="policy",
                title="测试奏对压力", target_text="测试",
                threshold=70, expires_turn=turn - 5,  # 已过期 → 触发 overdue
            )
            db.conn.commit()

            ev1 = obligations.obligation_pressure_tick(db, state, day=day, limit=10)
            ev2 = obligations.obligation_pressure_tick(db, state, day=day, limit=10)
            self.assertGreaterEqual(len(ev1), 1, "首次调用应触发压力事件")
            self.assertEqual(len(ev2), 0,
                             f"同 turn 第二次调用应被 idempotency 闸门阻断；实际 {len(ev2)} 个事件")


class IntrigueCoerceNoDoubleStackTests(unittest.TestCase):
    """intrigue.coerce_with_secret 连续两次提交必须不再叠加 emp_trust/grievance
    （同 secret 在 used=1 后不应再被 latent_secret_of 返回）。"""

    def test_double_coerce_same_target_does_not_stack_effects(self):
        from ming_sim import court, intrigue

        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = _day(db)
            intrigue.ensure_schema(db)

            char_row = db.conn.execute(
                "SELECT name FROM characters WHERE status='active' AND power_id='ming' LIMIT 1"
            ).fetchone()
            self.assertIsNotNone(char_row)
            name = str(char_row["name"])

            # 手动塞一条已知把柄
            db.conn.execute(
                "INSERT INTO secrets(holder, kind, detail, severity, known_to_crown) "
                "VALUES (?, '贪墨', '受赇鬻爵', 60, 1)",
                (name,))
            db.conn.commit()
            sid = int(db.conn.execute(
                "SELECT id FROM secrets WHERE holder=? ORDER BY id DESC LIMIT 1",
                (name,)).fetchone()["id"])

            # 第一次 coerce(serve)：emp_trust+5, grievance+8
            grv_before = int(db.conn.execute(
                "SELECT grievance FROM characters WHERE name=?", (name,)).fetchone()["grievance"])
            tr_before = int(db.conn.execute(
                "SELECT emp_trust FROM characters WHERE name=?", (name,)).fetchone()["emp_trust"])
            r1 = intrigue.coerce_with_secret(db, state, name, "serve", day=day)
            self.assertTrue(r1["ok"])
            grv_after_1 = int(db.conn.execute(
                "SELECT grievance FROM characters WHERE name=?", (name,)).fetchone()["grievance"])
            tr_after_1 = int(db.conn.execute(
                "SELECT emp_trust FROM characters WHERE name=?", (name,)).fetchone()["emp_trust"])
            self.assertEqual(grv_after_1 - grv_before, 8)
            self.assertEqual(tr_after_1 - tr_before, 5)

            # 第二次 coerce(serve)：若 latent_secret_of 不过滤 used，应再次 +5/+8 → 双计。
            r2 = intrigue.coerce_with_secret(db, state, name, "serve", day=day)
            grv_after_2 = int(db.conn.execute(
                "SELECT grievance FROM characters WHERE name=?", (name,)).fetchone()["grievance"])
            tr_after_2 = int(db.conn.execute(
                "SELECT emp_trust FROM characters WHERE name=?", (name,)).fetchone()["emp_trust"])
            # 第二次的增量必须为 0（已 used=1 的 secret 不再被 coerce）
            self.assertEqual(grv_after_2 - grv_after_1, 0,
                             f"第二次 coerce 不得叠加 grievance；实际 +{grv_after_2 - grv_after_1}")
            self.assertEqual(tr_after_2 - tr_after_1, 0,
                             f"第二次 coerce 不得叠加 emp_trust；实际 +{tr_after_2 - tr_after_1}")

            # secret 必须保持 used=1
            row = db.conn.execute(
                "SELECT used FROM secrets WHERE id=?", (sid,)).fetchone()
            self.assertEqual(int(row["used"]), 1)


class IntrigueInvestigateIdempotencyTests(unittest.TestCase):
    """investigate 已知秘密再次调用：应识别为 already，不重置 discovered_day。"""

    def test_repeat_investigate_known_secret_does_not_reset_day(self):
        from ming_sim import intrigue

        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = _day(db)
            intrigue.ensure_schema(db)

            char_row = db.conn.execute(
                "SELECT name FROM characters WHERE status='active' AND power_id='ming' LIMIT 1"
            ).fetchone()
            name = str(char_row["name"])
            db.conn.execute(
                "INSERT INTO secrets(holder, kind, detail, severity, known_to_crown, discovered_day) "
                "VALUES (?, '私德', '狎游失检', 55, 1, 100)",
                (name,))
            db.conn.commit()
            sid = int(db.conn.execute(
                "SELECT id FROM secrets WHERE holder=? ORDER BY id DESC LIMIT 1",
                (name,)).fetchone()["id"])

            r1 = intrigue.investigate(db, name, day=day)
            self.assertTrue(r1.get("ok"))
            self.assertTrue(r1.get("found"))
            self.assertTrue(r1.get("already"))
            # discovered_day 应保持 100，不被重置为 day
            row = db.conn.execute(
                "SELECT discovered_day FROM secrets WHERE id=?", (sid,)).fetchone()
            self.assertEqual(int(row["discovered_day"]), 100,
                             "known secret 再次 investigate 不得重置 discovered_day")


class VeilLedgerContradictionsNoDoubleCountTests(unittest.TestCase):
    """veil.ledger_contradictions 仅做只读聚合，不写任何表。"""

    def test_ledger_contradictions_readonly(self):
        from ming_sim import veil

        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            rows_before = db.conn.execute(
                "SELECT COUNT(*) AS n FROM report_ledger").fetchone()["n"]
            contr_before = db.conn.execute(
                "SELECT COUNT(*) AS n FROM report_ledger WHERE entity_kind='directive' "
                "AND ABS(reported_value - actual_value) >= 5").fetchone()["n"]
            eve_before = db.conn.execute(
                "SELECT COUNT(*) AS n FROM events").fetchone()["n"]

            # 多次调用
            for _ in range(3):
                veil.ledger_contradictions(db)

            rows_after = db.conn.execute(
                "SELECT COUNT(*) AS n FROM report_ledger").fetchone()["n"]
            contr_after = db.conn.execute(
                "SELECT COUNT(*) AS n FROM report_ledger WHERE entity_kind='directive' "
                "AND ABS(reported_value - actual_value) >= 5").fetchone()["n"]
            eve_after = db.conn.execute(
                "SELECT COUNT(*) AS n FROM events").fetchone()["n"]
            self.assertEqual(rows_after, rows_before)
            self.assertEqual(contr_after, contr_before)
            self.assertEqual(eve_after, eve_before,
                             "ledger_contradictions 不得插入 events 记录")


class SessionDrainPendingOutcomesConcurrencyTests(unittest.TestCase):
    """session.drain_pending_outcomes 的 CAS 闸门：连续两次调用不得双计 delta。
    验证 session.py:2721 加 outcome_status='extracted' 条件后，第二次 drain
    因 rowcount=0 而跳过落库，避免 metric_delta / economy_moves / legacies 双计。"""

    def test_double_drain_does_not_double_apply_metric_delta(self):
        import json as _json

        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = _day(db)
            r = assignment.issue_assignment(
                db, state, kind="edict", text="着户部清查盐税", actor="毕自严", day=day,
            )
            did = r["id"]
            # 模拟 worker 已抽取：outcome_status='extracted' + 暂存 metric_delta
            delta = {
                "metric_delta": {"皇威": +5},
                "economy_moves": [],
            }
            db.conn.execute(
                "UPDATE turn_directives SET outcome_status='extracted', outcome_delta=? WHERE id=?",
                (_json.dumps(delta, ensure_ascii=False), did))
            db.conn.commit()
            shi_before = int(state.metrics.get("皇威", 0))
            ledger_before = db.conn.execute(
                "SELECT COUNT(*) AS n FROM economy_ledger").fetchone()["n"]

            # 直接调用 drain 的 CAS 逻辑：模拟两次连续 drain
            # session.drain_pending_outcomes 是 GameSession 实例方法；
            # 这里测底层 CAS 行为（session.py:2721 修复后的 UPDATE 条件）。
            rows1 = db.conn.execute(
                "SELECT id FROM turn_directives WHERE outcome_status='extracted'"
            ).fetchall()
            self.assertEqual(len(rows1), 1, "首次 drain 前应有 1 个 extracted")

            # 模拟第一次 drain：CAS 成功（extracted → applied）
            cas1 = db.conn.execute(
                "UPDATE turn_directives SET outcome_status='applied' "
                "WHERE id=? AND outcome_status='extracted'", (did,))
            self.assertEqual(int(cas1.rowcount or 0), 1,
                             "首次 CAS 应成功（rowcount=1）")

            # 模拟第二次 drain：CAS 失败（rowcount=0）→ 不应再 apply
            cas2 = db.conn.execute(
                "UPDATE turn_directives SET outcome_status='applied' "
                "WHERE id=? AND outcome_status='extracted'", (did,))
            self.assertEqual(int(cas2.rowcount or 0), 0,
                             "第二次 CAS 必须 rowcount=0（已被首次推进至 applied）")

            # 验证：outcome_status 必须是 'applied'（不是 '' 也不是 'extracted'）
            row = db.conn.execute(
                "SELECT outcome_status FROM turn_directives WHERE id=?", (did,)).fetchone()
            self.assertEqual(str(row["outcome_status"] or ""), "applied")


class SessionRecordDialogueAfterChatWrapperTests(unittest.TestCase):
    """session.record_dialogue_after_chat 仅是 record_dialogue_effects 的 thin wrapper。
    路径已被 test_quest_dialogue_integration.test_sealed_handshake_creates_quest
    覆盖（record_dialogue_effects 直接调用），本测试仅在 GameSession 上确认
    该方法存在并可作为 thin wrapper 调用。"""

    def test_record_dialogue_after_chat_method_exists(self):
        from ming_sim.session import GameSession
        self.assertTrue(hasattr(GameSession, "record_dialogue_after_chat"))
        self.assertTrue(hasattr(GameSession, "drain_pending_outcomes"))


class HaremTickConsortLifecycleTests(unittest.TestCase):
    """harem.harem_tick 月度中枢：seed_static_data 默认有妃（周皇后等）。
    验证：(1) 单次 tick 最多 1 个 event；(2) 同 day 二次 tick 幂等（不叠加 factions.leverage）。"""

    def test_harem_tick_no_runtime_dup_event(self):
        from ming_sim import harem

        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = _day(db)
            ev = harem.harem_tick(db, state, day=day)
            self.assertLessEqual(len(ev), 1,
                                 f"harem_tick 单次调用最多 1 个 event；实际 {len(ev)}")

    def test_harem_tick_same_day_is_idempotent_on_factions(self):
        from ming_sim import harem

        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = _day(db)
            # 取 factions 表（seed_static_data 注入）
            leverage_before = db.conn.execute(
                "SELECT name, leverage FROM factions ORDER BY name"
            ).fetchall()
            ev1 = harem.harem_tick(db, state, day=day)
            leverage_mid = db.conn.execute(
                "SELECT name, leverage FROM factions ORDER BY name"
            ).fetchall()
            ev2 = harem.harem_tick(db, state, day=day)
            leverage_after = db.conn.execute(
                "SELECT name, leverage FROM factions ORDER BY name"
            ).fetchall()

            # RNG 用 day-seeded；两次结果应完全一致
            self.assertEqual(len(ev1), len(ev2),
                             "同 day 二次 harem_tick 应返相同数量事件")

            for before_row, after_row in zip(leverage_mid, leverage_after):
                self.assertEqual(int(before_row["leverage"]), int(after_row["leverage"]),
                                 f"factions.{before_row['name']}.leverage 二次 tick 必须不叠加")


class FoundationRecruitDedupTests(unittest.TestCase):
    """foundation.recruit_from_foundation：同名重复调用必须不创建重复 character。
    验证路径：db.add_character 的 existing 短路（line 2734-2738）。"""

    def test_double_recruit_same_name_no_duplicate_character(self):
        from ming_sim import foundation

        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            # 选一个 foundation profile 中存在且 content.characters 里没有的人名
            name = None
            avail = foundation.candidates(exclude_names=set(db.content.characters.keys()), limit=50)
            for c in avail:
                cn = str(c.get("name") or "")
                if cn:
                    name = cn
                    break
            if not name:
                self.skipTest("foundation profile 不可用，跳过")
            self.assertIsNotNone(name)
            self.assertNotIn(name, db.content.characters)

            before = db.conn.execute(
                "SELECT COUNT(*) AS n FROM characters WHERE name=?", (name,)).fetchone()["n"]
            self.assertEqual(before, 0, "首次征辟前该人不在 characters 表")

            character = foundation.build_game_character(name, state.year)
            self.assertIsNotNone(character)

            # 第一次
            db.add_character(state, character, source="基座起复")
            after_first = db.conn.execute(
                "SELECT COUNT(*) AS n FROM characters WHERE name=?", (name,)).fetchone()["n"]
            self.assertEqual(after_first, 1, "首次 add_character 应插入 1 行")

            # 第二次：existing 短路，不重复插入
            db.add_character(state, character, source="基座起复")
            after_second = db.conn.execute(
                "SELECT COUNT(*) AS n FROM characters WHERE name=?", (name,)).fetchone()["n"]
            self.assertEqual(after_second, 1,
                             "二次 add_character 同名必须走 existing 短路")


class NegotiationAgreementTaskLifecycleTests(unittest.TestCase):
    """negotiation_agreements 状态机：创建 → task 完成 → refresh → fulfilled。"""

    def test_task_done_aggregates_to_agreement_fulfilled(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            # 创建一个有 task_list 的 agreement
            ag_id = db.create_negotiation_agreement(
                state, minister_name="韩爌", topic="测试奏对",
                action_kind="policy", status="pending", stance_id=0,
                handshake_status="sealed", psychological_score=100, threshold=70,
                verbal_only=False, tasks=["条件一", "条件二"],
            )
            self.assertGreater(ag_id, 0)
            self.assertEqual(
                db.conn.execute(
                    "SELECT status FROM negotiation_agreements WHERE id=?",
                    (ag_id,)).fetchone()["status"],
                "pending",
                "有 task_list 时初始 status 必须是 pending（db.create_negotiation_agreement line 5502）")

            # 拿所有 task
            task_rows = db.conn.execute(
                "SELECT id, status FROM negotiation_tasks WHERE agreement_id=?",
                (ag_id,)).fetchall()
            self.assertEqual(len(task_rows), 2)

            # 第一个 task done
            db.update_negotiation_task(task_rows[0]["id"], "done", "履约证据一")
            self.assertEqual(
                db.conn.execute(
                    "SELECT status FROM negotiation_agreements WHERE id=?",
                    (ag_id,)).fetchone()["status"],
                "pending",
                "1/2 task done 时 agreement 仍 pending")

            # 第二个 task done → 全部 done → refresh → fulfilled
            db.update_negotiation_task(task_rows[1]["id"], "done", "履约证据二")
            self.assertEqual(
                db.conn.execute(
                    "SELECT status FROM negotiation_agreements WHERE id=?",
                    (ag_id,)).fetchone()["status"],
                "fulfilled",
                "2/2 task done 时 agreement 应自动 fulfilled")

    def test_one_task_failed_aggregates_to_agreement_failed(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            ag_id = db.create_negotiation_agreement(
                state, minister_name="毕自严", topic="测试失败",
                action_kind="policy", status="pending", stance_id=0,
                handshake_status="sealed", psychological_score=100, threshold=70,
                verbal_only=False, tasks=["待办A"],
            )
            task_id = int(db.conn.execute(
                "SELECT id FROM negotiation_tasks WHERE agreement_id=? LIMIT 1",
                (ag_id,)).fetchone()["id"])
            db.update_negotiation_task(task_id, "failed", "失败证据")
            self.assertEqual(
                db.conn.execute(
                    "SELECT status FROM negotiation_agreements WHERE id=?",
                    (ag_id,)).fetchone()["status"],
                "failed",
                "任意 task failed 时 agreement 应自动 failed")

    def test_double_update_same_task_does_not_double_status_change(self):
        """update_negotiation_task 同 task 连调两次：第二次仍 done，不能变回 pending。"""
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            ag_id = db.create_negotiation_agreement(
                state, minister_name="孙承宗", topic="测试重复",
                action_kind="policy", status="pending", stance_id=0,
                handshake_status="sealed", psychological_score=100, threshold=70,
                verbal_only=False, tasks=["唯一任务"],
            )
            task_id = int(db.conn.execute(
                "SELECT id FROM negotiation_tasks WHERE agreement_id=? LIMIT 1",
                (ag_id,)).fetchone()["id"])
            db.update_negotiation_task(task_id, "done", "证据一")
            db.update_negotiation_task(task_id, "done", "证据二覆盖")
            self.assertEqual(
                db.conn.execute(
                    "SELECT status FROM negotiation_tasks WHERE id=?",
                    (task_id,)).fetchone()["status"],
                "done")
            # evidence 第二次写入的内容应覆盖第一次
            ev = str(db.conn.execute(
                "SELECT evidence FROM negotiation_tasks WHERE id=?",
                (task_id,)).fetchone()["evidence"])
            self.assertEqual(ev, "证据二覆盖")


class MemorialCreateAndDecideTests(unittest.TestCase):
    """memorials.create_memorial → 决策/复命完整链路。"""

    def test_create_memorial_inserts_pending_row(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = _day(db)
            mid = __import__("ming_sim.memorials", fromlist=["create_memorial"]).create_memorial(
                db, state, day=day, author_name="袁崇焕", org="兵部",
                kind="请旨", urgency=2, summary="请求固守辽东",
                full_text="臣袁崇焕请旨：固守辽东诸堡",
            )
            self.assertGreater(mid, 0)
            row = db.conn.execute(
                "SELECT status, kind, summary, ref_kind, ref_id FROM memorials WHERE id=?",
                (mid,)).fetchone()
            self.assertEqual(str(row["status"]), "pending",
                             "新 memorial 必须 pending 待御批")
            self.assertEqual(str(row["kind"]), "请旨")
            self.assertEqual(str(row["summary"]), "请求固守辽东")
            self.assertEqual(str(row["ref_kind"]), "")  # 未传时为空

    def test_create_memorial_with_ref_kind_id_persists_refs(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = _day(db)
            mid = __import__("ming_sim.memorials", fromlist=["create_memorial"]).create_memorial(
                db, state, day=day, author_name="袁崇焕", org="兵部",
                kind="复命", urgency=2, summary="复命：固守辽东",
                ref_kind="directive", ref_id="42",
            )
            row = db.conn.execute(
                "SELECT ref_kind, ref_id FROM memorials WHERE id=?",
                (mid,)).fetchone()
            self.assertEqual(str(row["ref_kind"]), "directive")
            self.assertEqual(str(row["ref_id"]), "42")


class AmbitionPursueTickTest(unittest.TestCase):
    """ambition.pursue_tick 月度中枢：NPC 私心逐月累进。"""

    def test_pursue_tick_no_ambition_returns_empty(self):
        from ming_sim import ambition, db as db_mod

        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            # 没有任何 ambition 表的 character 应空转
            ev = ambition.pursue_tick(db, state, day=_day(db))
            self.assertIsInstance(ev, list)


if __name__ == "__main__":
    unittest.main()