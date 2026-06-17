"""即时复命管线回归（零 LLM）：
  - 诏书办结 → tick 入队 edict_outcome；
  - worker handler（llm_config=None 走模板）→ outcome_status='extracted' + 复命奏报落御案；
  - handler 幂等（worker 重试不重复）；
  - session.drain_pending_outcomes 主线程把暂存 delta 落库（metric/economy）+ 幂等不双算。
"""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim import lifecycle, scheduler, timeflow
from ming_sim.db import GameDB
from ming_sim.models import Character, CourtContext, LLMConfig
from ming_sim.registry import build_recent_directive_memory_brief
from ming_sim.session import GameSession
from ming_sim.upgrade_schema import KV_CURRENT_DAY, kv_int


def _fresh(tmp: str):
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    state = db.load_state()
    timeflow.ensure_active(db, state)
    return db, state


def _issue(db, state, text: str) -> int:
    cur = db.conn.execute(
        "INSERT INTO turn_directives (turn, year, period, text, source, status)"
        " VALUES (?,?,?,?,?,?)",
        (state.turn, state.year, state.period, text, "test", "confirmed"),
    )
    did = int(cur.lastrowid)
    db.conn.commit()
    rows = db.conn.execute("SELECT * FROM turn_directives WHERE id=?", (did,)).fetchall()
    lifecycle.init_directive_lifecycles(db, state, rows, kv_int(db, KV_CURRENT_DAY, 1))
    return did


def _force_done(db, state, did: int, *, integrity_actual: int = 100) -> None:
    db.conn.execute(
        "UPDATE turn_directives SET integrity_actual=?, progress=99, "
        "lifecycle_status='executing', lead_days=0 WHERE id=?", (integrity_actual, did))
    db.conn.commit()
    timeflow.advance_days(db, state, 1, stop_on_yellow=False)


def _drain_all(db, llm_config=None) -> int:
    done = 0
    while True:
        n = scheduler.process_pending(db, llm_config, limit=20)
        done += n
        if n == 0:
            break
    return done


class EdictOutcomeHandlerTests(unittest.TestCase):
    def test_done_enqueues_edict_outcome(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            did = _issue(db, state, "着户部即拨辽东军饷三十万两，毋得稽延")
            _force_done(db, state, did)
            self.assertEqual(
                str(db.conn.execute(
                    "SELECT lifecycle_status FROM turn_directives WHERE id=?", (did,)
                ).fetchone()["lifecycle_status"]), "done")
            kinds = [str(r["kind"]) for r in db.conn.execute(
                "SELECT kind FROM llm_jobs WHERE status='pending'").fetchall()]
            self.assertIn("edict_outcome", kinds)
            self.assertNotIn("settle_note", kinds)  # 已被 edict_outcome 取代

    def test_handler_template_fallback_writes_memorial(self):
        """llm_config=None → 模板复命：outcome_status=extracted、settle_note 有值、复命奏报入御案。"""
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            did = _issue(db, state, "着户部即拨辽东军饷三十万两，毋得稽延")
            _force_done(db, state, did)
            _drain_all(db, llm_config=None)
            row = db.conn.execute(
                "SELECT outcome_status, outcome_delta, settle_note FROM turn_directives WHERE id=?",
                (did,)).fetchone()
            self.assertEqual(str(row["outcome_status"]), "extracted")
            self.assertEqual(json.loads(row["outcome_delta"] or "{}"), {})  # 模板兜底无数值
            self.assertTrue(str(row["settle_note"]))
            mem = db.conn.execute(
                "SELECT * FROM memorials WHERE kind='复命' AND ref_kind='directive' AND ref_id=?",
                (str(did),)).fetchall()
            self.assertEqual(len(mem), 1)
            self.assertEqual(str(mem[0]["status"]), "pending")

    def test_eunuch_outcome_fallback_uses_inner_court_voice(self):
        """王承恩这类内廷承办人复命，不应再落成外朝大臣的“臣谨奏”。"""
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            did = _issue(db, state, "敕曰：试办内书堂一所，着内官监御前王承恩协办章程。")
            db.conn.execute(
                "UPDATE turn_directives SET assignee=?, integrity_actual=100, "
                "integrity_reported=100, outcome_status='' WHERE id=?",
                ("王承恩", did),
            )
            db.conn.commit()

            from ming_sim.edict_outcome import handle_edict_outcome

            memorial = handle_edict_outcome(db, None, {"directive_id": did})

            self.assertIn("奴婢谨奏", memorial)
            self.assertNotIn("臣谨奏", memorial)
            row = db.conn.execute(
                "SELECT settle_note FROM turn_directives WHERE id=?", (did,)
            ).fetchone()
            self.assertIn("奴婢谨奏", str(row["settle_note"]))

    def test_handler_idempotent(self):
        """worker 重复消费同一诏书不重复落复命奏报、状态不回退。"""
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            did = _issue(db, state, "祭告太庙，旌表忠烈")
            _force_done(db, state, did)
            from ming_sim.edict_outcome import handle_edict_outcome
            handle_edict_outcome(db, None, {"directive_id": did})
            handle_edict_outcome(db, None, {"directive_id": did})  # 再来一次
            mem = db.conn.execute(
                "SELECT COUNT(*) c FROM memorials WHERE kind='复命' AND ref_id=?",
                (str(did),)).fetchone()["c"]
            self.assertEqual(mem, 1)


class DrainPendingOutcomesTests(unittest.TestCase):
    def _session(self, tmp: str) -> GameSession:
        cfg = LLMConfig(api_key="test", base_url="http://test.invalid/v1", model="test-model")
        return GameSession(str(Path(tmp) / "g.db"), cfg, verify_llm=False)

    def _stage_done(self, sess: GameSession, text: str, delta: dict) -> int:
        cur = sess.db.conn.execute(
            "INSERT INTO turn_directives (turn, year, period, text, source, status, "
            "lifecycle_status, progress, integrity_actual, integrity_reported, "
            "outcome_delta, outcome_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (sess.state.turn, sess.state.year, sess.state.period, text, "test", "confirmed",
             "done", 100, 100, 100, json.dumps(delta, ensure_ascii=False), "extracted"),
        )
        sess.db.conn.commit()
        return int(cur.lastrowid)

    def test_drain_applies_metric_and_economy(self):
        with TemporaryDirectory() as tmp:
            sess = self._session(tmp)
            try:
                minxin0 = int(sess.state.metrics.get("民心", 0))
                guoku0 = int(sess.state.metrics.get("国库", 0))
                did = self._stage_done(sess, "着即拨陕西赈济银二十万两，平粜安置流民", {
                    "metric_delta": {"民心": 4},
                    "economy_moves": [{"account": "国库", "delta": -20,
                                       "category": "赈济", "reason": "陕西平粜"}],
                })
                results = sess.drain_pending_outcomes()
                self.assertEqual(len(results), 1)
                self.assertEqual(int(results[0]["directive_id"]), did)
                self.assertEqual(int(sess.state.metrics.get("民心", 0)), minxin0 + 4)
                # 国库支出经手有损耗（record_issue_economy_move 既有行为），只断言确有支出
                self.assertLess(int(sess.state.metrics.get("国库", 0)), guoku0)
                self.assertEqual(str(sess.db.conn.execute(
                    "SELECT outcome_status FROM turn_directives WHERE id=?", (did,)
                ).fetchone()["outcome_status"]), "applied")
            finally:
                sess.close()

    def test_extracted_economy_moves_apply_exact_without_legacy_drift(self):
        """单诏抽取出的硬银数应按 JSON 精确落账，不再被月度遗产修正放大。"""
        with TemporaryDirectory() as tmp:
            sess = self._session(tmp)
            try:
                guoku0 = int(sess.state.metrics.get("国库", 0))
                neiku0 = int(sess.state.metrics.get("内库", 0))
                arrears0 = int(sess.db.conn.execute(
                    "SELECT SUM(arrears) AS n FROM armies WHERE owner_power='ming'"
                ).fetchone()["n"] or 0)
                did = self._stage_done(sess, "拨国库四十五万两、内库五万两补饷", {
                    "economy_moves": [
                        {"account": "国库", "delta": -45, "category": "军饷",
                         "reason": "硬数回归", "purpose": "补饷"},
                        {"account": "内库", "delta": -5, "category": "借支",
                         "reason": "硬数回归"},
                    ],
                })
                results = sess.drain_pending_outcomes()
                self.assertEqual(int(results[0]["directive_id"]), did)
                self.assertEqual(int(sess.state.metrics.get("国库", 0)), guoku0 - 45)
                self.assertEqual(int(sess.state.metrics.get("内库", 0)), neiku0 - 5)
                arrears1 = int(sess.db.conn.execute(
                    "SELECT SUM(arrears) AS n FROM armies WHERE owner_power='ming'"
                ).fetchone()["n"] or 0)
                self.assertEqual(arrears1, arrears0 - 45)
                applied = results[0]["applied"]["economy_moves"]
                self.assertTrue(any(m["account"] == "国库" and m["delta"] == -45 for m in applied), applied)
                self.assertTrue(any(m["account"] == "内库" and m["delta"] == -5 for m in applied), applied)
            finally:
                sess.close()

    def test_drain_accepts_legacy_issue_delta_key(self):
        """即时复命旧字段 delta 也应推进局势，避免叙事说缓解但局势条不动。"""
        with TemporaryDirectory() as tmp:
            sess = self._session(tmp)
            try:
                issue = sess.db.conn.execute(
                    "SELECT id, bar_value FROM issues WHERE status='active' ORDER BY id LIMIT 1"
                ).fetchone()
                issue_id = int(issue["id"])
                before = int(issue["bar_value"])
                self._stage_done(sess, "着户部核实钱粮，缓解当前急务", {
                    "issue_advances": [{"issue_id": issue_id, "delta": 12, "reason": "复命旧字段"}],
                })
                results = sess.drain_pending_outcomes()
                after = int(sess.db.conn.execute(
                    "SELECT bar_value FROM issues WHERE id=?", (issue_id,)
                ).fetchone()["bar_value"])
                self.assertEqual(len(results), 1)
                self.assertEqual(after, min(100, before + 12))
            finally:
                sess.close()

    def test_recent_done_directives_feed_npc_dialogue_context(self):
        """连续时间制下，已复命圣旨应即时进入 NPC 召对事实，而非等月末章节记忆。"""
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            try:
                db.conn.execute(
                    """
                    INSERT INTO turn_directives
                        (turn, year, period, text, source, status, lifecycle_status,
                         progress, assignee, settle_note, outcome_status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        1,
                        1627,
                        10,
                        "敕曰：试办内书堂一所，着内官监御前王承恩协办章程。",
                        "test",
                        "issued",
                        "done",
                        100,
                        "王承恩",
                        "奴婢承恩谨奏：遵旨试办内书堂，已于京西潭柘寺畔择地设学，诸事就绪。",
                        "applied",
                    ),
                )
                db.conn.commit()
                state.turn = 2
                state.year = 1627
                state.period = 11
                character = Character(
                    name="王承恩",
                    office="内官监御前",
                    office_type="内廷",
                    faction="皇党",
                    aliases=[],
                    personal_skills=[],
                    loyalty=80,
                    ability=65,
                    integrity=75,
                    courage=70,
                    style="谨慎",
                    power_id="ming",
                )
                brief = build_recent_directive_memory_brief(character, CourtContext(state, db))
                self.assertIn("内书堂", brief)
                self.assertIn("已复命，结果已落库", brief)
                self.assertIn("不得继续把已复命之事说成未办", brief)
                self.assertIn("奴婢承恩谨奏", brief)
            finally:
                db.close()

    def test_directive_audience_context_enters_chat_prompt(self):
        """从诏旨召主办时，具体旨意上下文应进入本轮 NPC prompt。"""
        with TemporaryDirectory() as tmp:
            sess = GameSession(
                str(Path(tmp) / "directive_audience_context.db"),
                LLMConfig(api_key="test", base_url="http://test.invalid/v1", model="test-model"),
                verify_llm=False,
            )
            try:
                sess.dialogue_audit_client = lambda phase, payload: {  # type: ignore[assignment]
                    "goal_decision": "none",
                    "confidence": 90,
                }
                character = sess.content.characters["袁崇焕"]
                supplemental = (
                    "【本次召对事项：追问在办旨意】\n"
                    "旨意#7：令袁崇焕整顿辽东军饷。\n"
                    "主办官：袁崇焕；当前状态：承办中；账面进度：43%。"
                )
                augmented, prepared = sess.prepare_chat_run(
                    character,
                    "朕交你的旨意办到几分？",
                    supplemental_context=supplemental,
                )
                self.assertIn("追问在办旨意", augmented)
                self.assertIn("账面进度：43%", augmented)
                self.assertIn("追问在办旨意", prepared.behavior_context)
            finally:
                sess.close()

    def test_drain_idempotent(self):
        """再次 drain 不重复落 delta（已 applied 不再处理）。"""
        with TemporaryDirectory() as tmp:
            sess = self._session(tmp)
            try:
                self._stage_done(sess, "祭告太庙", {"metric_delta": {"皇威": 3}})
                huangwei0 = int(sess.state.metrics.get("皇威", 0))
                sess.drain_pending_outcomes()
                after = int(sess.state.metrics.get("皇威", 0))
                self.assertEqual(after, huangwei0 + 3)
                # 再 drain：无 extracted 行，皇威不再变
                self.assertEqual(sess.drain_pending_outcomes(), [])
                self.assertEqual(int(sess.state.metrics.get("皇威", 0)), after)
            finally:
                sess.close()


class IssueDecreeContinuousFlowTests(unittest.TestCase):
    """端到端（零 LLM）：颁诏解耦(write+lifecycle，不月末结算) → 连续推进跨月不卡 →
    诏书到期 worker 产复命 → drain 落库。"""

    def _session(self, tmp: str) -> GameSession:
        cfg = LLMConfig(api_key="test", base_url="http://test.invalid/v1", model="test-model")
        return GameSession(str(Path(tmp) / "g.db"), cfg, verify_llm=False)

    def test_issue_then_mature_then_drain(self):
        with TemporaryDirectory() as tmp:
            sess = self._session(tmp)
            try:
                # 一条 draft，提供 decree 文本避免拟诏 LLM
                sess.db.conn.execute(
                    "INSERT INTO turn_directives (turn, year, period, text, source, status)"
                    " VALUES (?,?,?,?,?,?)",
                    (sess.state.turn, sess.state.year, sess.state.period,
                     "着即拨陕西赈济银二十万两，平粜安置流民", "test", "draft"))
                sess.db.conn.commit()
                sess.resolve_turn(decree="着即拨陕西赈济银二十万两，平粜安置流民")
                # 入了生命周期、不终结回合（仍可亲政）
                did = int(sess.db.conn.execute(
                    "SELECT id FROM turn_directives WHERE lifecycle_status!='' ORDER BY id DESC LIMIT 1"
                ).fetchone()["id"])
                self.assertEqual(str(sess.state.turn_phase), "summoning")

                # 强制办结 + 连续推进（验证不卡月末）
                turn0 = sess.state.turn
                _force_done(sess.db, sess.state, did)
                self.assertEqual(str(sess.db.conn.execute(
                    "SELECT lifecycle_status FROM turn_directives WHERE id=?", (did,)
                ).fetchone()["lifecycle_status"]), "done")
                # worker（模板）产复命 + 暂存
                _drain_all(sess.db, llm_config=None)
                # 复命奏报已落御案
                self.assertEqual(sess.db.conn.execute(
                    "SELECT COUNT(*) c FROM memorials WHERE kind='复命' AND ref_id=?",
                    (str(did),)).fetchone()["c"], 1)
                # drain 落库（模板 delta 为空，状态仍应转 applied）
                sess.drain_pending_outcomes()
                self.assertEqual(str(sess.db.conn.execute(
                    "SELECT outcome_status FROM turn_directives WHERE id=?", (did,)
                ).fetchone()["outcome_status"]), "applied")

                # 连续推进跨月：turn 自增、不停在月末
                guard = 0
                while sess.state.turn == turn0 and guard < 80:
                    guard += 1
                    r = timeflow.advance_days(sess.db, sess.state, 5, stop_on_yellow=False)
                    if r["advanced"] == 0:
                        break
                self.assertGreater(sess.state.turn, turn0)
            finally:
                sess.close()

    def test_resolve_turn_does_not_double_wrap_formal_decree(self):
        with TemporaryDirectory() as tmp:
            sess = self._session(tmp)
            try:
                sess.db.conn.execute(
                    "INSERT INTO turn_directives (turn, year, period, text, source, status)"
                    " VALUES (?,?,?,?,?,?)",
                    (sess.state.turn, sess.state.year, sess.state.period,
                     "着户部五日内核实辽饷", "test", "draft"))
                sess.db.conn.commit()
                report = sess.resolve_turn(decree="奉天承运皇帝诏曰：\n着户部五日内核实辽饷。")
                self.assertEqual(report.count("奉天承运皇帝"), 1)
            finally:
                sess.close()


class OfficeChangeTests(unittest.TestCase):
    """人事诏：office_changes 名字护栏 + drain 真正改职（连续时间下不再失效）。"""

    def test_scope_delta_drops_unnamed_office_changes(self):
        from ming_sim.edict_outcome import _scope_delta
        ctx = {"directive": "起复孙承宗，任为蓟辽督师，督理关宁", "allowed_issue_ids": []}
        delta = {"office_changes": [
            {"name": "孙承宗", "new_office": "蓟辽督师"},   # 原文有 → 保留
            {"name": "张三丰", "new_office": "户部尚书"},    # 原文无 → 丢弃（防幽灵建档）
            {"name": "", "new_office": "x"},                  # 空名 → 丢弃
        ]}
        out = _scope_delta(delta, ctx)["office_changes"]
        self.assertEqual([o["name"] for o in out], ["孙承宗"])

    def test_scope_delta_normalizes_hard_money_and_issue_delta(self):
        from ming_sim.edict_outcome import _scope_delta
        ctx = {
            "directive": (
                "户部右侍郎毕自严即日核实辽饷、京营、陕西赈济三项实欠，"
                "从太仓存银支二十万两、两淮盐课截留十五万两、浒墅关等三关关税催解十万两、"
                "内库借支五万两，合计五十万两，分拨辽东、京营、陕西。"
            ),
            "allowed_issue_ids": [1],
        }
        out = _scope_delta({
            "economy_moves": [
                {"account": "国库", "delta": -30, "category": "军饷", "reason": "模型少抽"},
                {"account": "内库", "delta": -5, "category": "借支", "reason": "模型少抽"},
            ],
            "issue_advances": [{"issue_id": 1, "delta": 8, "reason": "旧字段"}],
        }, ctx)
        moves = out["economy_moves"]
        self.assertEqual([(m["account"], m["delta"]) for m in moves], [("国库", -45), ("内库", -5)])
        self.assertEqual(moves[0].get("purpose"), "补饷")
        self.assertEqual(out["issue_advances"][0]["delta_bar"], 8)

    def test_drain_applies_office_change_for_active_minister(self):
        cfg = LLMConfig(api_key="test", base_url="http://test.invalid/v1", model="test-model")
        with TemporaryDirectory() as tmp:
            sess = GameSession(str(Path(tmp) / "g.db"), cfg, verify_llm=False)
            try:
                # 取一名在朝大臣
                row = sess.db.conn.execute(
                    "SELECT name, office FROM characters WHERE status='active' AND power_id='ming' "
                    "AND office_type NOT IN ('后宫') LIMIT 1").fetchone()
                name = str(row["name"]); old_office = str(row["office"] or "")
                new_office = "钦命专办大臣"
                sess.db.conn.execute(
                    "INSERT INTO turn_directives (turn, year, period, text, source, status, "
                    "lifecycle_status, progress, integrity_actual, integrity_reported, outcome_delta, outcome_status) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (sess.state.turn, sess.state.year, sess.state.period, f"着{name}改任{new_office}",
                     "test", "confirmed", "done", 100, 100, 100,
                     json.dumps({"office_changes": [{"name": name, "new_office": new_office, "reason": "test"}]}),
                     "extracted"))
                sess.db.conn.commit()
                sess.drain_pending_outcomes()
                cur = str(sess.db.conn.execute(
                    "SELECT office FROM characters WHERE name=?", (name,)).fetchone()["office"] or "")
                self.assertEqual(cur, new_office, f"{name} 改职未生效（{old_office}→应 {new_office}，实 {cur}）")
            finally:
                sess.close()


class StatusChangeTests(unittest.TestCase):
    """罢黜/革职 via 诏：character_status_changes 名字+状态护栏 + drain 真正去职。"""

    def test_scope_delta_guards_status_changes(self):
        from ming_sim.edict_outcome import _scope_delta
        ctx = {"directive": "革职兵部尚书崔呈秀，下狱论罪", "allowed_issue_ids": []}
        delta = {"character_status_changes": [
            {"name": "崔呈秀", "status": "imprisoned"},  # 原文有+合法 → 保留
            {"name": "崔呈秀", "status": "banished"},     # 非法状态 → 丢
            {"name": "李四", "status": "dead"},           # 原文无 → 丢
        ]}
        out = _scope_delta(delta, ctx)["character_status_changes"]
        self.assertEqual(out, [{"name": "崔呈秀", "status": "imprisoned"}])

    def test_drain_applies_status_change(self):
        cfg = LLMConfig(api_key="test", base_url="http://test.invalid/v1", model="test-model")
        with TemporaryDirectory() as tmp:
            sess = GameSession(str(Path(tmp) / "g.db"), cfg, verify_llm=False)
            try:
                row = sess.db.conn.execute(
                    "SELECT name FROM characters WHERE status='active' AND power_id='ming' "
                    "AND office_type NOT IN ('后宫') LIMIT 1").fetchone()
                name = str(row["name"])
                sess.db.conn.execute(
                    "INSERT INTO turn_directives (turn, year, period, text, source, status, "
                    "lifecycle_status, progress, integrity_actual, integrity_reported, outcome_delta, outcome_status) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (sess.state.turn, sess.state.year, sess.state.period, f"革职{name}，下狱论罪",
                     "test", "confirmed", "done", 100, 100, 100,
                     json.dumps({"character_status_changes": [{"name": name, "status": "imprisoned", "reason": "test"}]}),
                     "extracted"))
                sess.db.conn.commit()
                sess.drain_pending_outcomes()
                cur = str(sess.db.conn.execute(
                    "SELECT status FROM characters WHERE name=?", (name,)).fetchone()["status"])
                self.assertEqual(cur, "imprisoned", f"{name} 罢黜未生效（应 imprisoned，实 {cur}）")
            finally:
                sess.close()


class CastrationByDecreeTests(unittest.TestCase):
    """宫刑作刑罚 via 诏（E2b）：character_status_changes status=castrated → 强阉没入内廷。"""

    def test_scope_delta_allows_castrated(self):
        from ming_sim.edict_outcome import _scope_delta
        ctx = {"directive": "着将贪墨之兵部主事王二处宫刑，发净军", "allowed_issue_ids": []}
        delta = {"character_status_changes": [{"name": "王二", "status": "castrated"}]}
        out = _scope_delta(delta, ctx)["character_status_changes"]
        self.assertEqual(out, [{"name": "王二", "status": "castrated"}])

    def test_drain_applies_castration(self):
        from ming_sim import eunuch_lore as el
        from ming_sim.eunuch import is_eunuch_like
        cfg = LLMConfig(api_key="test", base_url="http://test.invalid/v1", model="test-model")
        with TemporaryDirectory() as tmp:
            sess = GameSession(str(Path(tmp) / "g.db"), cfg, verify_llm=False)
            try:
                row = sess.db.conn.execute(
                    "SELECT name FROM characters WHERE status='active' AND power_id='ming' "
                    "AND office_type NOT IN ('后宫','司礼监','内官监御前') "
                    "AND office NOT LIKE '%太监%' LIMIT 1").fetchone()
                name = str(row["name"])
                sess.db.conn.execute(
                    "INSERT INTO turn_directives (turn, year, period, text, source, status, "
                    "lifecycle_status, progress, integrity_actual, integrity_reported, outcome_delta, outcome_status) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (sess.state.turn, sess.state.year, sess.state.period, f"着将{name}处宫刑，没入内廷为奴",
                     "test", "confirmed", "done", 100, 100, 100,
                     json.dumps({"character_status_changes": [{"name": name, "status": "castrated", "reason": "贪墨"}]}),
                     "extracted"))
                sess.db.conn.commit()
                sess.drain_pending_outcomes()
                crow = sess.db.conn.execute(
                    "SELECT status, office, office_type, faction FROM characters WHERE name=?", (name,)).fetchone()
                self.assertEqual(str(crow["status"]), "active")               # 没入内廷仍在朝（为奴役）
                self.assertTrue(is_eunuch_like(str(crow["office"]), str(crow["office_type"])))  # 已成内臣
                lore = el.get_lore(sess.db, name)
                self.assertIsNotNone(lore)
                self.assertTrue(lore["forced"])                                # 强阉
                self.assertEqual(lore["bao_status"], el.BAO_FORFEIT)           # 宝官没
            finally:
                sess.close()


class InformationalMemorialTests(unittest.TestCase):
    """复命/捷报作「结果通知」：已阅免精力、到期静默归档、不计淹没问责、不压 backlog。"""

    def test_ack_costs_no_attention(self):
        from ming_sim import memorials
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = kv_int(db, KV_CURRENT_DAY, 1)
            memorials.reset_attention_for_day(db, day)
            before = memorials.attention_left(db)
            mid = memorials.create_memorial(db, state, day=day, author_name="孙承宗", org="辽东经略",
                                            kind="复命", urgency=2, summary="复命：起复授辽东经略")
            r = memorials.decide_memorial(db, state, mid, "ack", day=day)
            self.assertTrue(r["ok"])
            self.assertEqual(memorials.attention_left(db), before)  # 免精力
            self.assertEqual(str(db.conn.execute(
                "SELECT status FROM memorials WHERE id=?", (mid,)).fetchone()["status"]), "approved")

    def test_informational_silent_expire_no_penalty(self):
        from ming_sim import memorials
        from ming_sim.upgrade_schema import KV_RISK_AVERSION, kv_int as _kvi
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            mid = memorials.create_memorial(db, state, day=1, author_name="孙承宗", org="辽东经略",
                                            kind="复命", urgency=2, summary="复命")
            ra_before = _kvi(db, KV_RISK_AVERSION, 40)
            events = memorials.memorials_daily_tick(db, state, day=60)  # shelved=59 >= deadline(40)
            self.assertEqual(str(db.conn.execute(
                "SELECT status FROM memorials WHERE id=?", (mid,)).fetchone()["status"]), "expired")
            # 无「淹没」问责事件、RA 不因结果通知而升
            self.assertFalse(any(e.get("kind") == "memorial_expired" and e.get("ref_id") == str(mid)
                                 for e in events))
            self.assertEqual(_kvi(db, KV_RISK_AVERSION, 40), ra_before)

    def test_backlog_excludes_informational(self):
        from ming_sim import memorials
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            day = kv_int(db, KV_CURRENT_DAY, 1)
            memorials.create_memorial(db, state, day=day, author_name="某", org="户部",
                                      kind="请款", urgency=2, summary="请款")
            memorials.create_memorial(db, state, day=day, author_name="孙承宗", org="辽东",
                                      kind="复命", urgency=2, summary="复命")
            desk = memorials.desk_payload(db, state, day)
            self.assertEqual(desk["backlog"], 1)       # 只算请款
            self.assertEqual(desk.get("info_count"), 1)  # 复命另计


if __name__ == "__main__":
    unittest.main()
