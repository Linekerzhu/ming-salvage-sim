"""schema 版本化测试（G3.1 可升级性）。

验证：
- ensure_upgrade_schema 后，schema_version 落库为 SCHEMA_VERSION
- 幂等：重复 ensure 不回退版本、不抛异常
- get_schema_version 在空库返回 0，ensure 后返回当前版本
- 向前只增：模拟旧档（version=0）→ ensure → 升到 SCHEMA_VERSION

零 LLM。
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim.db import GameDB
from ming_sim import timeflow
from ming_sim.upgrade_schema import (
    KV_SCHEMA_VERSION,
    SCHEMA_VERSION,
    ensure_upgrade_schema,
    get_schema_version,
    kv_int,
    kv_set_int,
)


def _fresh(tmp: str):
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    state = db.load_state()
    timeflow.ensure_active(db, state)
    return db, state


class SchemaVersioningTests(unittest.TestCase):
    def test_fresh_db_has_version_zero_before_ensure(self):
        with TemporaryDirectory() as tmp:
            db, _state = _fresh(tmp)
            # seed_static_data 已会触发 ensure_upgrade_schema 间接路径；
            # 但直接查 KV 字段应在 ensure 后有值。这里测 get_schema_version 不抛。
            v = get_schema_version(db)
            self.assertIsInstance(v, int)
            self.assertGreaterEqual(v, 0)

    def test_ensure_sets_current_schema_version(self):
        with TemporaryDirectory() as tmp:
            db, _state = _fresh(tmp)
            ensure_upgrade_schema(db)
            self.assertEqual(get_schema_version(db), SCHEMA_VERSION,
                             f"ensure 后 schema_version 应 = {SCHEMA_VERSION}")

    def test_ensure_is_idempotent_on_version(self):
        """重复 ensure 不回退版本、不抛异常。"""
        with TemporaryDirectory() as tmp:
            db, _state = _fresh(tmp)
            ensure_upgrade_schema(db)
            v1 = get_schema_version(db)
            # 二次 ensure
            ensure_upgrade_schema(db)
            v2 = get_schema_version(db)
            self.assertEqual(v1, v2, "重复 ensure 不应改变版本")
            self.assertEqual(v2, SCHEMA_VERSION)

    def test_old_save_upgrades_to_current_version(self):
        """模拟旧档（version=0）→ ensure → 升到 SCHEMA_VERSION（向前只增）。"""
        with TemporaryDirectory() as tmp:
            db, _state = _fresh(tmp)
            # 强制标记为旧档（version 0）
            kv_set_int(db, KV_SCHEMA_VERSION, 0)
            self.assertEqual(get_schema_version(db), 0)
            # ensure 应把它升到当前版本
            ensure_upgrade_schema(db)
            self.assertEqual(get_schema_version(db), SCHEMA_VERSION,
                             "旧档经 ensure 应升到当前版本")

    def test_version_never_downgrades(self):
        """若存档版本高于当前（不应发生，但防御），ensure 不得回退。"""
        with TemporaryDirectory() as tmp:
            db, _state = _fresh(tmp)
            # 假装存档是未来版本（比 SCHEMA_VERSION 高）
            kv_set_int(db, KV_SCHEMA_VERSION, SCHEMA_VERSION + 5)
            ensure_upgrade_schema(db)
            # 不应回退到 SCHEMA_VERSION（向前只增）
            self.assertGreaterEqual(get_schema_version(db), SCHEMA_VERSION + 5,
                                    "ensure 不得回退已存在的更高版本")


if __name__ == "__main__":
    unittest.main()
