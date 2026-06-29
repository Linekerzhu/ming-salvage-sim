"""Settings 模块测试：env 读取、类型校验、CORS 解析、server_mode 判定。"""

import os
import unittest


class SettingsTests(unittest.TestCase):
    def setUp(self):
        # 清 lru_cache 使每次测试重新读 env
        from ming_sim.settings import get_settings
        get_settings.cache_clear()
        # 保存并清掉所有 MING_SIM_ env，保证测试隔离
        self._saved = {}
        for k in list(os.environ):
            if k.startswith("MING_SIM_"):
                self._saved[k] = os.environ.pop(k)

    def tearDown(self):
        from ming_sim.settings import get_settings
        get_settings.cache_clear()
        # 先清掉测试中新建的 MING_SIM_ 变量，再恢复进入测试前的快照。
        for k in list(os.environ):
            if k.startswith("MING_SIM_"):
                os.environ.pop(k)
        for k, v in self._saved.items():
            os.environ[k] = v

    def test_defaults_when_no_env(self):
        """无 env 时用默认值。"""
        from ming_sim.settings import get_settings
        get_settings.cache_clear()
        s = get_settings()
        self.assertEqual(s.log_level, "INFO")
        self.assertFalse(s.json_logs)
        self.assertFalse(s.is_server_mode)
        self.assertFalse(s.debug_state)

    def test_cors_origins_parsed_to_list(self):
        from ming_sim.settings import get_settings
        os.environ["MING_SIM_CORS_ORIGINS"] = "https://a.com, https://b.com"
        get_settings.cache_clear()
        s = get_settings()
        self.assertEqual(s.cors_origin_list, ["https://a.com", "https://b.com"])

    def test_server_mode_detected_from_server_users(self):
        from ming_sim.settings import get_settings
        os.environ["MING_SIM_SERVER_USERS"] = "alice:pw"
        get_settings.cache_clear()
        s = get_settings()
        self.assertTrue(s.is_server_mode)

    def test_invite_code_defaults_empty(self):
        """SEC-001：invite_code 无默认值（空 = 注册关闭）。"""
        from ming_sim.settings import get_settings
        get_settings.cache_clear()
        s = get_settings()
        self.assertEqual(s.invite_code, "")

    def test_debug_state_env_parsed_as_bool(self):
        from ming_sim.settings import get_settings
        os.environ["MING_SIM_DEBUG_STATE"] = "1"
        get_settings.cache_clear()
        s = get_settings()
        self.assertTrue(s.debug_state)


if __name__ == "__main__":
    unittest.main()
