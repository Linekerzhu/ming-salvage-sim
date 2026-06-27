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


if __name__ == "__main__":
    unittest.main()
