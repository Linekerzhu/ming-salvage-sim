import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim import timeflow
from ming_sim.content import GameContent
from ming_sim.context import bind_content as bind_context, npc_dialogue_behavior_brief, npc_dialogue_behavior_profile
from ming_sim.db import GameDB
from ming_sim.obligations import obligation_pressure_tick
from ming_sim.upgrade_schema import DAYS_PER_MONTH, KV_CURRENT_DAY, kv_set_int


def _fresh(tmp: str):
    db = GameDB(str(Path(tmp) / "obligations.db"))
    db.seed_static_data()
    state = db.load_state()
    timeflow.ensure_active(db, state)
    return db, state


def _active_minister(db: GameDB) -> str:
    row = db.conn.execute(
        """
        SELECT name
        FROM characters
        WHERE status='active' AND power_id='ming' AND office_type!='后宫'
        ORDER BY
          CASE WHEN name='韩爌' THEN 0 ELSE 1 END,
          name
        LIMIT 1
        """
    ).fetchone()
    assert row is not None
    return str(row["name"])


class ObligationPressureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.content = GameContent.load()
        bind_context(cls.content)

    def test_overdue_conversation_goal_becomes_pressure_once_per_month(self) -> None:
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            name = _active_minister(db)
            row = db.conn.execute(
                "SELECT emp_trust, grievance FROM characters WHERE name=?", (name,)
            ).fetchone()
            trust0 = int(row["emp_trust"])
            grievance0 = int(row["grievance"])
            goal_id = db.create_conversation_goal(
                state,
                minister_name=name,
                action_kind="court_commitment",
                title=f"举主连坐：{name}保新人",
                target_text=f"{name}须为荐人共办试差并交代担保边界。",
                threshold=70,
                score=100,
                status="waiting_conditions",
                condition_status="pending",
                conditions=[{"description": "两月内回奏荐人试差证据。", "status": "pending"}],
                expires_turn=2,
                last_delta={"source": f"patronage_accountability:{name}:新人:joint_trial:sponsor"},
            )
            state.turn = 2
            state.period += 1
            db.save_state(state)

            events = obligation_pressure_tick(db, state, day=DAYS_PER_MONTH + 1)

            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["kind"], "conversation_goal_pressure")
            self.assertEqual(events[0]["level"], "yellow")
            self.assertEqual(events[0]["ref_id"], str(goal_id))
            self.assertIn("举主担保", str(events[0]["detail"]))
            updated = db.get_conversation_goal(goal_id)
            self.assertIsNotNone(updated)
            self.assertEqual(updated["status"], "blocked")
            self.assertEqual(updated["condition_status"], "blocked")
            self.assertIn("逾期未复命", " ".join(updated["blockers"]))
            self.assertEqual(updated["conditions"][0]["status"], "failed")
            row2 = db.conn.execute(
                "SELECT emp_trust, grievance FROM characters WHERE name=?", (name,)
            ).fetchone()
            self.assertEqual(int(row2["emp_trust"]), max(0, trust0 - 2))
            self.assertEqual(int(row2["grievance"]), min(100, grievance0 + 5))
            pressure_events = db.conn.execute(
                """
                SELECT COUNT(*) c
                FROM conversation_goal_events
                WHERE goal_id=? AND turn=? AND event_kind='monthly_pressure'
                """,
                (goal_id, state.turn),
            ).fetchone()["c"]
            self.assertEqual(int(pressure_events), 1)

            self.assertEqual(obligation_pressure_tick(db, state, day=DAYS_PER_MONTH + 1), [])
            pressure_events2 = db.conn.execute(
                """
                SELECT COUNT(*) c
                FROM conversation_goal_events
                WHERE goal_id=? AND turn=? AND event_kind='monthly_pressure'
                """,
                (goal_id, state.turn),
            ).fetchone()["c"]
            self.assertEqual(int(pressure_events2), 1)
            db.conn.close()

    def test_obligation_pressure_ripples_through_relationship_web(self) -> None:
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            name = _active_minister(db)
            ally = "孙承宗"
            rival = "魏忠贤"
            db.conn.execute(
                "DELETE FROM relationships WHERE a_name IN (?,?,?) OR b_name IN (?,?,?)",
                (name, ally, rival, name, ally, rival),
            )
            db.conn.executemany(
                "INSERT INTO relationships (a_name, b_name, opinion, basis, updated_day) VALUES (?,?,?,?,0)",
                [
                    (name, ally, 30, "同道"),
                    (ally, name, 24, "同道"),
                    (name, rival, -30, "政敌"),
                    (rival, name, -24, "政敌"),
                ],
            )
            db.conn.commit()
            goal_id = db.create_conversation_goal(
                state,
                minister_name=name,
                action_kind="court_commitment",
                title=f"举主连坐：{name}保新人",
                target_text=f"{name}须为荐人共办试差并交代担保边界。",
                threshold=70,
                score=100,
                status="waiting_conditions",
                condition_status="pending",
                conditions=[{"description": "两月内回奏荐人试差证据。", "status": "pending"}],
                expires_turn=2,
                last_delta={"source": f"patronage_accountability:{name}:新人:joint_trial:sponsor"},
            )
            state.turn = 2
            state.period += 1
            db.save_state(state)

            events = obligation_pressure_tick(db, state, day=DAYS_PER_MONTH + 1)

            self.assertTrue(events)
            self.assertIn("关系网震荡", [str(e["label"]) for e in events[0]["effects"]])
            self.assertEqual(
                int(db.conn.execute(
                    "SELECT opinion FROM relationships WHERE a_name=? AND b_name=?", (ally, name)
                ).fetchone()["opinion"]),
                26,
            )
            self.assertEqual(
                int(db.conn.execute(
                    "SELECT opinion FROM relationships WHERE a_name=? AND b_name=?", (rival, name)
                ).fetchone()["opinion"]),
                -26,
            )
            updated = db.get_conversation_goal(goal_id)
            pressure = updated["last_delta"]["monthly_pressure"]
            self.assertIn(ally, pressure["network_touch"]["allies"])
            self.assertIn(rival, pressure["network_touch"]["rivals"])
            db.conn.close()

    def test_dialogue_behavior_reads_recent_obligation_pressure(self) -> None:
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            name = "韩爌"
            goal_id = db.create_conversation_goal(
                state,
                minister_name=name,
                action_kind="court_commitment",
                title=f"举主连坐：{name}保新人",
                target_text=f"{name}须为荐人共办试差并交代担保边界。",
                threshold=70,
                score=100,
                status="waiting_conditions",
                condition_status="pending",
                conditions=[{"description": "两月内回奏荐人试差证据。", "status": "pending"}],
                expires_turn=2,
                last_delta={"source": f"patronage_accountability:{name}:新人:joint_trial:sponsor"},
            )
            state.turn = 2
            state.period += 1
            db.save_state(state)
            obligation_pressure_tick(db, state, day=DAYS_PER_MONTH + 1)
            self.assertTrue(goal_id)

            profile = npc_dialogue_behavior_profile(
                name,
                text="朕问你上月旧约如何交账？",
                character=self.content.characters[name],
                db=db,
            )
            brief = npc_dialogue_behavior_brief(
                name,
                text="朕问你上月旧约如何交账？",
                character=self.content.characters[name],
                db=db,
            )

            self.assertIn("旧约发酵", profile["risk_tags"])
            self.assertIn("近期旧约压力", brief)
            self.assertIn("举主担保", brief)
            self.assertIn("先主动说明旧约为何未了", brief)
            db.conn.close()

    def test_month_rollover_reports_obligation_pressure(self) -> None:
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            name = _active_minister(db)
            goal_id = db.create_conversation_goal(
                state,
                minister_name=name,
                action_kind="court_commitment",
                title="共办消怨：同办海防钱粮",
                target_text="须与政敌共办海防钱粮，并各退一步。",
                threshold=70,
                score=80,
                status="waiting_conditions",
                condition_status="pending",
                conditions=[{"description": "下月回奏分工。", "status": "pending"}],
                expires_turn=2,
                last_delta={"source": "co_work:test"},
            )
            kv_set_int(db, KV_CURRENT_DAY, state.turn * DAYS_PER_MONTH)

            result = timeflow.advance_days(db, state, 1, stop_on_yellow=False)

            self.assertEqual(state.turn, 2)
            reports = result["reports"]
            flat = [event for report in reports for event in report["events"]]
            pressure = [event for event in flat if event.get("kind") == "conversation_goal_pressure"]
            self.assertTrue(pressure)
            self.assertEqual(str(pressure[0]["ref_id"]), str(goal_id))
            self.assertIn("旧约发酵", str(pressure[0]["title"]))
            db.conn.close()


if __name__ == "__main__":
    unittest.main()
