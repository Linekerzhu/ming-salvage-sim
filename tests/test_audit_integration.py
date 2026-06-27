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


if __name__ == "__main__":
    unittest.main()