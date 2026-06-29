"""/api/debug/state 调试端点测试。

端点用途：开发/调试时转储游戏状态（metrics + turn + 关键计数），免手写 SQL。
安全：双重门——admin 鉴权 + MING_SIM_DEBUG_STATE=1 开关（默认关，防泄漏 NPC 内部）。
"""

import os
import unittest


class DebugStateEndpointTests(unittest.TestCase):
    def setUp(self):
        self._saved = {
            k: os.environ.get(k)
            for k in ("MING_SIM_DEBUG_STATE", "MING_SIM_SERVER_USERS", "MING_SIM_AUTH_USERS")
        }
        from ming_sim.settings import get_settings
        get_settings.cache_clear()

    def tearDown(self):
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)
        from ming_sim.settings import get_settings
        get_settings.cache_clear()

    def test_debug_state_disabled_by_default(self):
        """MING_SIM_DEBUG_STATE 未开时，端点应 404（不存在 / 隐藏）。"""
        os.environ.pop("MING_SIM_DEBUG_STATE", None)
        from ming_sim.settings import get_settings
        get_settings.cache_clear()
        s = get_settings()
        self.assertFalse(s.debug_state, "debug_state 默认应为 False（安全）")

    def test_debug_state_enabled_via_env(self):
        """开 DEBUG_STATE=1 后，Settings.debug_state 为 True。"""
        os.environ["MING_SIM_DEBUG_STATE"] = "1"
        from ming_sim.settings import get_settings
        get_settings.cache_clear()
        s = get_settings()
        self.assertTrue(s.debug_state)

    def test_debug_state_endpoint_404_when_disabled(self):
        """真实端点在 DEBUG_STATE 关闭时返回 404（不暴露存在）。"""
        from fastapi.testclient import TestClient
        from ming_sim.settings import get_settings
        import web_app

        os.environ.pop("MING_SIM_DEBUG_STATE", None)
        os.environ.pop("MING_SIM_SERVER_USERS", None)
        os.environ.pop("MING_SIM_AUTH_USERS", None)
        get_settings.cache_clear()

        client = TestClient(web_app.app)
        resp = client.get("/api/debug/state")
        self.assertEqual(resp.status_code, 404)

    def test_debug_state_endpoint_requires_login_when_auth_enabled(self):
        """DEBUG_STATE 开启后，真实端点仍需先通过登录中间件。"""
        from fastapi.testclient import TestClient
        from ming_sim.settings import get_settings
        import web_app

        os.environ["MING_SIM_DEBUG_STATE"] = "1"
        os.environ["MING_SIM_SERVER_USERS"] = "alice:pw"
        os.environ.pop("MING_SIM_AUTH_USERS", None)
        get_settings.cache_clear()

        client = TestClient(web_app.app)
        resp = client.get("/api/debug/state")
        self.assertEqual(resp.status_code, 401)


if __name__ == "__main__":
    unittest.main()
