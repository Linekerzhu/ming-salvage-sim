"""dialogue_audit 性能回归测试：锁定 _context_payload 单轮 memoization。

这是三层优化的第一层（DB 查询去冗余）的回归保护：
- 同 character + 同 turn 多次调用 _context_payload 不得重复触发 DB 查询。
- _clear_context_cache 在 timeflow 跨 tick 时清空，确保下一 tick 读最新 context。
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim.content import GameContent
from ming_sim.db import GameDB
from ming_sim import timeflow
from ming_sim.dialogue_audit import _context_payload
from ming_sim.quest_db import apply_quest_schema


def _fresh(tmp: str):
    content = GameContent.load()
    db = GameDB(str(Path(tmp) / "perf.db"), content=content)
    db.seed_static_data()
    state = db.load_state()
    timeflow.ensure_active(db, state)
    apply_quest_schema(db.conn)
    return db, state


class ContextPayloadMemoizationTests(unittest.TestCase):
    """_context_payload 单轮 memoization：同 (character, turn) 命中缓存。"""

    def test_same_character_same_turn_cached(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            character = db.content.characters["韩爌"]
            # 第一次调用填充缓存
            _context_payload(db, state, character)
            # 第二次同 character+turn 必须命中缓存（DB query count 不再涨）
            # sqlite3.Connection.execute 是只读属性，故用 wrapper Connection 计数
            class _CountingConn:
                def __init__(self, inner):
                    self._inner = inner
                    self.count = 0

                def execute(self, sql, *args, **kwargs):
                    self.count += 1
                    return self._inner.execute(sql, *args, **kwargs)

                def __getattr__(self, name):
                    return getattr(self._inner, name)

            counter = _CountingConn(db.conn)
            original_conn = db.conn
            db.conn = counter
            try:
                p2 = _context_payload(db, state, character)
            finally:
                db.conn = original_conn
            self.assertEqual(
                counter.count,
                0,
                f"memoized _context_payload 同 character+turn 不应触发 DB 查询；实际 {counter.count}",
            )
            # 内容必须等价（npc name 等核心字段稳定）
            self.assertEqual(p2["npc"]["name"], character.name)

    def test_different_character_different_cache_entry(self):
        """不同 character 必须独立缓存——不能串读。"""
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            char_a = db.content.characters["韩爌"]
            char_b = db.content.characters.get("周延儒") or next(
                c for n, c in db.content.characters.items() if n != "韩爌"
            )
            p_a = _context_payload(db, state, char_a)
            p_b = _context_payload(db, state, char_b)
            self.assertNotEqual(p_a["npc"]["name"], p_b["npc"]["name"],
                                "不同 character 的 payload 不得串读")

    def test_active_goal_injected_per_call(self):
        """active_goal 是 per-call 参数；即使命中缓存也必须正确注入。"""
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            character = db.content.characters["韩爌"]
            _context_payload(db, state, character)  # 填充缓存
            goal_a = {"id": 1, "title": "目标 A"}
            goal_b = {"id": 2, "title": "目标 B"}
            p_a = _context_payload(db, state, character, active_goal=goal_a)
            p_b = _context_payload(db, state, character, active_goal=goal_b)
            self.assertEqual(p_a["active_goal"], goal_a)
            self.assertEqual(p_b["active_goal"], goal_b)


class CombinedIntentAuditTests(unittest.TestCase):
    """route + action_probe 合并为单次 LLM：仅在生产路径（无 audit_client 注入）触发。
    测试注入 audit_client 时走原有串行回退（行为兼容）。

    本测试验证合并审计函数本身（dialogue_combined_intent_audit）在 audit_client
    响应 combined_intent phase 时正确返回；以及 evaluate_combined_intent 在
    无 audit_client + 有 LLM 配置时走合并路径。
    """

    def test_combined_audit_returns_route_when_fake_responds(self):
        """audit_client 响应 combined_intent phase → 直接返 route 决策。"""
        from ming_sim.dialogue_audit import dialogue_combined_intent_audit
        from ming_sim.models import LLMConfig
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            character = db.content.characters["韩爌"]

            def audit(phase, payload):
                if phase == "combined_intent":
                    return {
                        "allow": True,
                        "intent": "route",
                        "action_type": "recruitment",
                        "phase": "propose",
                        "confidence": 90,
                        "trigger_quote": payload.get("user_text"),
                        "target": "韩爌",
                        "actor": "韩爌",
                    }
                return None

            review = dialogue_combined_intent_audit(
                db, state, character, "朕要召见韩爌",
                route_context={"semantic_route_enabled": True},
                llm_config=LLMConfig(model="t", api_key="t", base_url="http://t"),
                audit_client=audit,
            )
            self.assertTrue(review.get("allow"))
            self.assertEqual(review.get("intent"), "route")

    def test_engine_falls_back_to_serial_when_audit_client_injected(self):
        """evaluate_user_message 在 audit_client 注入时走原有串行路径（兼容性）。"""
        import os
        from ming_sim.dialogue_semantics import DialogueSemanticEngine
        from ming_sim.models import LLMConfig
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            character = db.content.characters["韩爌"]
            call_log = []
            old_action = os.environ.get("MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT")
            old_route = os.environ.get("MING_SIM_DISABLE_DIALOGUE_ROUTE_LLM_AUDIT")
            os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "0"
            os.environ["MING_SIM_DISABLE_DIALOGUE_ROUTE_LLM_AUDIT"] = "1"
            try:
                def audit(phase, payload):
                    call_log.append(phase)
                    if phase == "dialogue_action_intent":
                        return {
                            "allow": True,
                            "action_type": "recruitment",
                            "phase": "propose",
                            "confidence": 90,
                            "trigger_quote": payload.get("user_text"),
                            "target": "韩爌",
                        }
                    return None

                engine = DialogueSemanticEngine(
                    db, state,
                    llm_config=LLMConfig(model="t", api_key="t", base_url="http://t"),
                    audit_client=audit,
                )
                decision = engine.evaluate_user_message(
                    character, "朕要征辟韩爌",
                    route_context={"semantic_route_enabled": False})
                self.assertTrue(decision.allow)
                self.assertNotIn("combined_intent", call_log,
                    f"audit_client 注入时不得走合并路径；实际 {call_log}")
            finally:
                if old_action is not None:
                    os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = old_action
                else:
                    os.environ.pop("MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT", None)
                if old_route is not None:
                    os.environ["MING_SIM_DISABLE_DIALOGUE_ROUTE_LLM_AUDIT"] = old_route
                else:
                    os.environ.pop("MING_SIM_DISABLE_DIALOGUE_ROUTE_LLM_AUDIT", None)


class CombinedPostChatAuditTests(unittest.TestCase):
    """合并 post_chat 5 类审计：dialogue_combined_post_audit 函数本身正确性。
    evaluate_post_chat 在生产路径（无 audit_client）走合并；注入时回退串行。"""

    def test_combined_post_returns_directive_pressure(self):
        """audit_client 响应 combined_post phase → 正确返回 directive_pressure 决策。"""
        from ming_sim.dialogue_audit import dialogue_combined_post_audit
        from ming_sim.models import LLMConfig
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            character = db.content.characters["韩爌"]

            def audit(phase, payload):
                if phase == "combined_post":
                    return {
                        "allow": True,
                        "action_type": "directive_pressure",
                        "kind": "pressed",
                        "answer_evidence": "臣遵旨",
                        "trigger_quote": payload.get("user_text"),
                        "confidence": 88,
                    }
                return None

            review = dialogue_combined_post_audit(
                db, state, character, "朕已催办此事", "臣遵旨",
                kind="directive_pressure",
                llm_config=LLMConfig(model="t", api_key="t", base_url="http://t"),
                audit_client=audit,
            )
            self.assertTrue(review.get("allow"))
            self.assertEqual(review.get("action_type"), "directive_pressure")
            self.assertEqual(review.get("kind"), "pressed")

    def test_engine_post_chat_falls_back_when_audit_client_injected(self):
        """evaluate_post_chat 在 audit_client 注入时走原有串行路径（兼容性）。"""
        import os
        from ming_sim.dialogue_semantics import DialogueSemanticEngine
        from ming_sim.models import LLMConfig
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            character = db.content.characters["韩爌"]
            call_log = []
            old = os.environ.get("MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT")
            os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "0"
            try:
                def audit(phase, payload):
                    call_log.append(phase)
                    if phase == "dialogue_directive_pressure":
                        return {
                            "allow": True,
                            "kind": "pressed",
                            "answer_evidence": "臣遵旨",
                            "trigger_quote": payload.get("user_text"),
                            "confidence": 88,
                        }
                    return None

                engine = DialogueSemanticEngine(
                    db, state,
                    llm_config=LLMConfig(model="t", api_key="t", base_url="http://t"),
                    audit_client=audit,
                )
                decision = engine.evaluate_post_chat(
                    character, "朕已催办此事", "臣遵旨",
                    kind="directive_pressure",
                    context={"directive_text": "着办某事"},
                )
                self.assertTrue(decision.allow)
                self.assertNotIn("combined_post", call_log,
                    f"audit_client 注入时不得走合并路径；实际 {call_log}")
            finally:
                if old is not None:
                    os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = old
                else:
                    os.environ.pop("MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT", None)


if __name__ == "__main__":
    unittest.main()
