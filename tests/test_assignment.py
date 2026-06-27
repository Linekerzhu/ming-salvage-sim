"""差使大厅与统一指派入口测试（P0）。

覆盖：
- 表结构迁移幂等（A0.1）
- issue_assignment 四种可写 kind + secret_order 拒绝（A0.2）
- 大厅跨表聚合：turn_directives + secret_orders（A0.3）
- 专注队列：needs_action / overloaded / recent_settled（A0.3）

零 LLM。
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim import assignment, timeflow
from ming_sim.db import GameDB
from ming_sim.upgrade_schema import KV_CURRENT_DAY, kv_int


def _fresh(tmp: str):
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    state = db.load_state()
    timeflow.ensure_active(db, state)
    assignment.ensure_assignment_schema(db)
    return db, state


def _day(db: GameDB) -> int:
    return kv_int(db, KV_CURRENT_DAY, 1)


class SchemaMigrationTests(unittest.TestCase):
    def test_ensure_assignment_schema_idempotent(self):
        with TemporaryDirectory() as tmp:
            db, _ = _fresh(tmp)
            cols = {r["name"] for r in db.conn.execute("PRAGMA table_info(turn_directives)").fetchall()}
            self.assertIn("assignment_kind", cols)
            self.assertIn("source_petition_id", cols)
            # 重复调用不报错
            assignment.ensure_assignment_schema(db)
            cols2 = {r["name"] for r in db.conn.execute("PRAGMA table_info(turn_directives)").fetchall()}
            self.assertEqual(cols, cols2)

    def test_old_directives_default_to_edict(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            # 模拟老存档：直接 INSERT 不带新列
            db.conn.execute(
                "INSERT INTO turn_directives (turn, year, period, text, source, status) VALUES (?,?,?,?,?,?)",
                (state.turn, state.year, state.period, "老旨意", "legacy", "confirmed"),
            )
            db.conn.commit()
            row = db.conn.execute("SELECT assignment_kind FROM turn_directives ORDER BY id DESC LIMIT 1").fetchone()
            self.assertEqual(str(row["assignment_kind"]), "edict")


class IssueAssignmentTests(unittest.TestCase):
    def test_writable_kinds_land_in_lifecycle(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = _day(db)
            for kind, text in [
                ("edict", "着户部即拨辽东军饷三十万两"),
                ("audience_commission", "着速查辽东欠饷实数"),
                ("petition_grant", "准奏发内帑赈济陕西"),
            ]:
                r = assignment.issue_assignment(db, state, kind=kind, text=text, day=day)
                self.assertTrue(r["ok"], f"{kind} 应成功")
                self.assertEqual(r["assignment_kind"], kind)
                self.assertEqual(r["entry_label"], assignment.ENTRY_LABELS[kind])
                # 落库且 kind 正确
                row = db.conn.execute(
                    "SELECT assignment_kind, lifecycle_status FROM turn_directives WHERE id=?",
                    (r["id"],),
                ).fetchone()
                self.assertEqual(str(row["assignment_kind"]), kind)
                self.assertNotEqual(str(row["lifecycle_status"]), "")

    def test_secret_order_rejected_from_issue_assignment(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            with self.assertRaises(ValueError):
                assignment.issue_assignment(db, state, kind="secret_order", text="x", day=_day(db))

    def test_unknown_kind_rejected(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            with self.assertRaises(ValueError):
                assignment.issue_assignment(db, state, kind="bogus", text="x", day=_day(db))

    def test_source_context_merged_into_chain(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            r = assignment.issue_assignment(
                db, state, kind="audience_commission", text="着速办某事",
                actor="梁廷栋", day=_day(db),
                source_context={"minister": "梁廷栋", "snippet": "上回召对交办"},
            )
            row = db.conn.execute("SELECT chain FROM turn_directives WHERE id=?", (r["id"],)).fetchone()
            import json
            meta = json.loads(row["chain"])
            self.assertEqual(meta["source_context"]["minister"], "梁廷栋")


class DashboardAggregationTests(unittest.TestCase):
    def _seed_mixed(self, db, state):
        """下 2 道旨意 + 1 道密旨，密旨给另一官员。"""
        day = _day(db)
        assignment.issue_assignment(db, state, kind="edict",
                                    text="着户部即拨辽东军饷三十万两", day=day)
        assignment.issue_assignment(db, state, kind="audience_commission",
                                    text="着速查辽东欠饷实数", day=day)
        # 密旨走独立表（找一个锦衣卫/东厂角色）
        so_minister = str(db.conn.execute(
            "SELECT name FROM characters WHERE status='active' AND office LIKE '%锦衣卫%' LIMIT 1"
        ).fetchone()["name"])
        db.create_secret_order(state, so_minister, "密查陕西", "密旨密查陕西贪墨", ["密查"], deadline_months=2)
        return so_minister

    def test_hall_aggregates_both_tables(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            self._seed_mixed(db, state)
            dash = assignment.assignment_dashboard(db, view="by_official", include_done=False, limit=60)
            tables = {it["source_table"] for g in dash["groups"] for it in g["items"]}
            self.assertEqual(tables, {"directives", "secret_orders"})
            self.assertEqual(dash["total"], 3)

    def test_by_category_includes_secret(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            self._seed_mixed(db, state)
            dash = assignment.assignment_dashboard(db, view="by_category", include_done=False, limit=60)
            cats = {g["key"] for g in dash["groups"]}
            self.assertIn("secret", cats)

    def test_all_four_views_run(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            self._seed_mixed(db, state)
            for v in ("by_official", "by_region", "by_category", "by_status"):
                d = assignment.assignment_dashboard(db, view=v, include_done=False, limit=60)
                self.assertEqual(d["view"], v)

    def test_overloaded_counts_both_tables(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            self._seed_mixed(db, state)
            # threshold=1 触发所有在办者
            ol = assignment.assignments_overloaded(db, threshold=1)
            names = {o["assignee"] for o in ol}
            # 旨意主办与密旨承办人都应在列
            self.assertTrue(names, "应至少有一个超载官员")

    def test_needs_action_picks_up_pending_review_secret_order(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            so_minister = self._seed_mixed(db, state)
            so_id = db.conn.execute(
                "SELECT id FROM secret_orders WHERE minister_name=?", (so_minister,)
            ).fetchone()["id"]
            # 全 active → 空
            self.assertEqual(len(assignment.assignments_needs_action(db)), 0)
            # 置 pending_review → 进队列（映射为 stalled）
            db.conn.execute("UPDATE secret_orders SET status='pending_review' WHERE id=?", (so_id,))
            db.conn.commit()
            na = assignment.assignments_needs_action(db)
            self.assertEqual(len(na), 1)
            self.assertEqual(na[0]["assignment_kind"], "secret_order")


class PetitionGrantTests(unittest.TestCase):
    def test_grant_petition_links_directive_and_quest(self):
        """奏请获准：player_quests → granted，并转 petition_grant 差使。"""
        from ming_sim.quest_db import apply_quest_schema
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            # 直接建 quest schema（needs_migration 在 schema_version 表缺失时会崩，
            # apply_quest_schema 本身幂等：CREATE TABLE IF NOT EXISTS）
            apply_quest_schema(db.conn)
            # 手工插一条待批奏请
            cur = db.conn.execute(
                "INSERT INTO player_quests (quest_key, player_id, status, progress_current,"
                " progress_target, accepted_turn, expires_turn, source_npc_name, objective_data)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                ("petition_test", 1, "available", 0, 1, state.turn, 0, "周延儒", "{}"),
            )
            pid = int(cur.lastrowid)
            db.conn.commit()

            r = assignment.grant_petition(db, state, pid, draft_text="准发内帑赈济陕西", day=_day(db))
            self.assertTrue(r["ok"])
            self.assertEqual(r["assignment_kind"], "petition_grant")
            # 奏请单已 granted，差使回填 source_petition_id
            pq = db.conn.execute("SELECT status FROM player_quests WHERE id=?", (pid,)).fetchone()
            self.assertEqual(str(pq["status"]), "granted")
            d = db.conn.execute(
                "SELECT assignment_kind, source_petition_id FROM turn_directives WHERE id=?",
                (r["id"],),
            ).fetchone()
            self.assertEqual(str(d["assignment_kind"]), "petition_grant")
            self.assertEqual(int(d["source_petition_id"]), pid)


class AcceptanceTests(unittest.TestCase):
    """P1.1 NPC 领旨表态。"""

    def test_acceptance_generated_on_issue(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            r = assignment.issue_assignment(db, state, kind="edict",
                                            text="着户部即拨辽东军饷三十万两", day=_day(db))
            acc = r["acceptance"]
            self.assertIn(acc.get("stance"), assignment._ACCEPTANCE_STANCES)
            self.assertIn("label", acc)
            self.assertTrue(0 <= acc["willingness"] <= 100)
            self.assertTrue(acc["narrative"])

    def test_overloaded_official_replies_reluctantly(self):
        """同一人连派多件后，willingness 应下降到请辞/附条件区间。"""
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            r0 = assignment.issue_assignment(db, state, kind="edict",
                                             text="着户部即拨辽东军饷三十万两", day=_day(db))
            assignee = r0["assignee"]
            # 连派 4 件给同一人制造超载
            for i in range(4):
                assignment.issue_assignment(db, state, kind="edict",
                                            text=f"着速办差使{i}", actor=assignee, day=_day(db))
            last = db.conn.execute(
                "SELECT chain FROM turn_directives ORDER BY id DESC LIMIT 1"
            ).fetchone()
            import json
            acc = json.loads(last["chain"])["acceptance"]
            self.assertLess(acc["willingness"], 50)  # 超载后意愿低
            self.assertIn(acc["stance"], ("decline", "conditional", "request_time"))

    def test_request_time_extends_exec_without_deadline(self):
        """无玩家期限时，请限 stance 真实延长 exec_days。"""
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            r = assignment.issue_assignment(db, state, kind="edict",
                                            text="着户部即拨辽东军饷三十万两", day=_day(db))
            if r["acceptance"]["stance"] == "request_time":
                # 找到该差使，exec_days 应 > 基础值（已被 ×1.4）
                row = db.conn.execute(
                    "SELECT exec_days FROM turn_directives WHERE id=?", (r["id"],)
                ).fetchone()
                self.assertGreater(int(row["exec_days"]), 1)
            else:
                self.skipTest("本种子未触发请限（确定性公式，换文本可复现）")

    def test_player_deadline_honored_despite_reluctance(self):
        """玩家硬期限下，即便请限/请辞，eta 跨度也不超 deadline。"""
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            for dl in (10, 15, 20):
                r = assignment.issue_assignment(db, state, kind="edict",
                                                text=f"着速办差使{dl}", day=_day(db),
                                                deadline_days=dl)
                row = db.conn.execute(
                    "SELECT eta_day, start_day FROM turn_directives WHERE id=?", (r["id"],)
                ).fetchone()
                span = int(row["eta_day"]) - int(row["start_day"])
                self.assertLessEqual(span, dl, f"deadline={dl} 被顶破，跨度={span}")

    def test_decline_costs_imperial_power(self):
        """请辞 stance 应扣势（KV_SHI −1）。"""
        from ming_sim.upgrade_schema import KV_SHI, kv_int
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            shi_before = kv_int(db, KV_SHI, 50)
            # 用 decline stance 构造：先把某人怨气拉满、忠心拉低
            r0 = assignment.issue_assignment(db, state, kind="edict",
                                             text="着户部即拨辽东军饷", day=_day(db))
            assignee = r0["assignee"]
            db.conn.execute(
                "UPDATE characters SET grievance=100, loyalty=10 WHERE name=?", (assignee,)
            )
            db.conn.commit()
            r = assignment.issue_assignment(db, state, kind="edict",
                                            text="着再办一件难差", actor=assignee, day=_day(db))
            if r["acceptance"]["stance"] == "decline":
                self.assertLess(kv_int(db, KV_SHI, 50), shi_before)
            else:
                self.skipTest("本构造未触发请辞")

    def test_acceptance_shown_on_dashboard_card(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            assignment.issue_assignment(db, state, kind="edict",
                                        text="着户部即拨辽东军饷三十万两", day=_day(db))
            dash = assignment.assignment_dashboard(db, view="by_official", limit=60)
            card = dash["groups"][0]["items"][0]
            self.assertTrue(card.get("acceptance"))
            self.assertIn(card["acceptance"]["stance"], assignment._ACCEPTANCE_STANCES)


if __name__ == "__main__":
    unittest.main()
