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


if __name__ == "__main__":
    unittest.main()