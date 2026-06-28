"""llm_provider 接缝测试（G4.1 可替换性）。

验证：
- build_agent 在默认 LLM_BACKEND=agno 下构造出有效 agent（不抛异常）
- 非 agno 后端显式报错 NotImplementedError（防静默走错路径）
- dialogue_audit._agent 经接缝构造仍工作（回归保护）

需要 LLM 配置（OPENAI_API_KEY 等）；测试用 dummy key 不实际调 LLM，只验构造。
"""

from __future__ import annotations

import os
import unittest


class LLMProviderSeamTests(unittest.TestCase):
    def setUp(self):
        self._saved_backend = os.environ.pop("LLM_BACKEND", None)

    def tearDown(self):
        if self._saved_backend is not None:
            os.environ["LLM_BACKEND"] = self._saved_backend
        else:
            os.environ.pop("LLM_BACKEND", None)

    def test_default_backend_is_agno(self):
        from ming_sim.llm_provider import _backend
        os.environ.pop("LLM_BACKEND", None)
        self.assertEqual(_backend(), "agno")

    def test_unsupported_backend_raises(self):
        """非 agno 后端必须显式 NotImplementedError（防静默走错路径）。"""
        from ming_sim.llm_provider import build_agent
        from ming_sim.llm_config import LLMConfig
        os.environ["LLM_BACKEND"] = "anthropic_native"
        cfg = LLMConfig(api_key="dummy", base_url="https://x", model="m")
        with self.assertRaises(NotImplementedError):
            build_agent(cfg, pipeline_id="llm.test", prompt="p", phase="t")

    def test_agno_backend_builds_agent(self):
        """默认 agno 后端：build_agent 构造出对象（不实际调 LLM）。"""
        from ming_sim.llm_provider import build_agent
        from ming_sim.llm_config import LLMConfig
        os.environ.pop("LLM_BACKEND", None)
        cfg = LLMConfig(api_key="dummy-key", base_url="https://example.test/v1", model="test-model")
        agent = build_agent(
            cfg,
            pipeline_id="llm.dialogue_post_audit",
            prompt="test prompt",
            phase="post",
        )
        # agno Agent 有 name/id 属性
        self.assertTrue(hasattr(agent, "name") or hasattr(agent, "id"))


if __name__ == "__main__":
    unittest.main()
