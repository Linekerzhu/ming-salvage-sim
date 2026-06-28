"""logging_util 测试：log_swallow 吞咽异常并记日志。"""

import logging
import unittest
from io import StringIO


class LogSwallowTests(unittest.TestCase):
    def test_swallows_exception_without_reraising(self):
        """异常被吞咽，调用方不感知（保持原有 except: pass 语义）。"""
        from ming_sim.logging_util import log_swallow
        # 不应抛异常
        with log_swallow("测试吞咽"):
            raise ValueError("boom")
        # 到这里说明异常被吞了

    def test_logs_exception_with_context(self):
        """吞咽时记 WARNING 日志，含上下文描述 + traceback。"""
        from ming_sim.logging_util import log_swallow
        buf = StringIO()
        handler = logging.StreamHandler(buf)
        handler.setLevel(logging.WARNING)
        logger = logging.getLogger("ming_sim.swallow")
        logger.addHandler(handler)
        logger.setLevel(logging.WARNING)
        # 防止 propagate 到 root 重复打
        old_prop = logger.propagate
        logger.propagate = False
        try:
            with log_swallow("计算能力覆盖"):
                raise ValueError("foundation 不可用")
            output = buf.getvalue()
            self.assertIn("计算能力覆盖", output)
            self.assertIn("ValueError", output)
            self.assertIn("foundation 不可用", output)
        finally:
            logger.removeHandler(handler)
            logger.propagate = old_prop


class GetLoggerTests(unittest.TestCase):
    def test_returns_namespaced_logger(self):
        from ming_sim.logging_util import get_logger
        log = get_logger("assignment")
        self.assertEqual(log.name, "ming_sim.assignment")


if __name__ == "__main__":
    unittest.main()
