"""差使大厅 + NPC 奏请 Web API 端到端测试（P0）。

用 FastAPI TestClient 跑通 /api/assignments 与 /api/petitions 全套路由。
零 LLM。
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ming_sim import assignment, timeflow
from ming_sim.assignment_api import register_assignment_routes
from ming_sim.db import GameDB
from ming_sim.quest_db import apply_quest_schema


def _make_client(tmp: str):
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    state = db.load_state()
    timeflow.ensure_active(db, state)
    db.save_state(state)
    apply_quest_schema(db.conn)
    assignment.ensure_assignment_schema(db)

    app = FastAPI()
    register_assignment_routes(app, lambda: db)
    return TestClient(app), db


class AssignmentHallRouteTests(unittest.TestCase):
    def test_dashboard_and_focus_queues(self):
        with TemporaryDirectory() as tmp:
            client, db = _make_client(tmp)
            state = db.load_state()
            # 下两道旨意
            assignment.issue_assignment(db, state, kind="edict",
                                        text="着户部即拨辽东军饷三十万两")
            assignment.issue_assignment(db, state, kind="audience_commission",
                                        text="着速查辽东欠饷实数", actor="梁廷栋")

            for view in ("by_official", "by_region", "by_category", "by_status"):
                r = client.get(f"/api/assignments?view={view}")
                self.assertEqual(r.status_code, 200)
                self.assertEqual(r.json()["view"], view)
            # 大厅总数 ≥ 2
            self.assertGreaterEqual(client.get("/api/assignments").json()["total"], 2)

            # 专注队列端点不报错
            for path in ("/api/assignments/needs_action",
                         "/api/assignments/overloaded",
                         "/api/assignments/recent_settled"):
                self.assertEqual(client.get(path).status_code, 200)

    def test_invalid_view_rejected(self):
        with TemporaryDirectory() as tmp:
            client, _ = _make_client(tmp)
            r = client.get("/api/assignments?view=bogus")
            self.assertEqual(r.status_code, 400)

    def test_issue_via_api_and_secret_order_rejected(self):
        with TemporaryDirectory() as tmp:
            client, _ = _make_client(tmp)
            r = client.post("/api/assignments", json={"kind": "edict", "text": "着工部修筑边墙"})
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r.json()["ok"])
            # secret_order 经 API 也应被拒
            r2 = client.post("/api/assignments", json={"kind": "secret_order", "text": "x"})
            self.assertEqual(r2.status_code, 400)

    def test_get_assignment_detail(self):
        with TemporaryDirectory() as tmp:
            client, db = _make_client(tmp)
            state = db.load_state()
            res = assignment.issue_assignment(db, state, kind="edict", text="着户部清查账目")
            did = res["id"]
            r = client.get(f"/api/assignments/{did}")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["assignment_kind"], "edict")
            # 不存在的 id
            self.assertEqual(client.get("/api/assignments/99999").status_code, 404)

    def test_issue_via_api_forwards_depends_on(self):
        """P2.1：经 API 下达的 depends_on 必须落库（防回归：曾因路由未转发丢失）。"""
        with TemporaryDirectory() as tmp:
            client, db = _make_client(tmp)
            r1 = client.post("/api/assignments", json={"kind": "edict", "text": "查", "actor": "毕自严"})
            a = r1.json()["id"]
            r2 = client.post("/api/assignments", json={"kind": "edict", "text": "惩", "actor": "崔呈秀", "depends_on": [a]})
            import json
            row = db.conn.execute("SELECT chain FROM turn_directives WHERE id=?", (r2.json()["id"],)).fetchone()
            self.assertEqual(json.loads(row["chain"]).get("depends_on"), [a])


class PetitionRouteTests(unittest.TestCase):
    def test_submit_grant_reject_flow(self):
        with TemporaryDirectory() as tmp:
            client, db = _make_client(tmp)
            state = db.load_state()
            # 提交一条奏请
            r = client.post("/api/petitions", json={
                "petition_key": "test_relief", "title": "请赈陕西",
                "proposer_name": "周延儒", "draft_directive": "发内帑赈济陕西",
            })
            self.assertEqual(r.status_code, 200)
            pid = r.json()["petition_id"]

            # 待批列表可见
            lst = client.get("/api/petitions").json()
            self.assertEqual(lst["status"], "available")
            self.assertTrue(any(p["id"] == pid for p in lst["items"]))

            # 御批（不传 draft_text，应自动取模板 draft_directive）
            g = client.post(f"/api/petitions/{pid}/grant", json={})
            self.assertEqual(g.status_code, 200)
            self.assertEqual(g.json()["assignment_kind"], "petition_grant")
            # 奏请单已 granted
            self.assertEqual(g.json()["entry_label"], "奏请获准")

            # 不再出现在 available
            lst2 = client.get("/api/petitions").json()["items"]
            self.assertFalse(any(p["id"] == pid for p in lst2))

            # history 可见 granted
            hist = client.get("/api/petitions/history").json()
            self.assertTrue(any(p["id"] == pid for p in hist["granted"]))

    def test_reject_flow(self):
        with TemporaryDirectory() as tmp:
            client, _ = _make_client(tmp)
            r = client.post("/api/petitions", json={
                "petition_key": "test_reject", "title": "请开矿税",
                "proposer_name": "", "draft_directive": "开矿税",
            })
            pid = r.json()["petition_id"]
            rej = client.post(f"/api/petitions/{pid}/reject", json={"reason": "扰民"})
            self.assertEqual(rej.status_code, 200)
            self.assertEqual(rej.json()["status"], "rejected")
            # 重复驳回应失败（已不在 available）
            self.assertEqual(client.post(f"/api/petitions/{pid}/reject", json={}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
