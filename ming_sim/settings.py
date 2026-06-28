"""集中配置模块：用 pydantic-settings 收口核心 env vars，启动时 fail-fast 校验。

G2 改进：此前 65 个 env vars 散落 13 文件，无集中校验——typo 一个 env 名
静默用默认值。本模块收口最关键的安全/运行配置子集（auth、db、cors、debug），
在 Settings() 实例化时自动从 env 读取 + 类型校验。

不收口 LLM 特有配置（仍走 llm_config.load_llm_config，因有复杂的 advanced/role 路由）。
LLM 配置可后续渐进迁入。
"""

from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """核心运行配置。从环境变量 + .env 自动读取。"""

    model_config = SettingsConfigDict(
        env_prefix="MING_SIM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # 忽略未声明的 env vars（LLM_* / OPENAI_* 等仍由各自模块读）
    )

    # ── 运行时 ──
    db: str = ""                    # 主库路径；空则走 user_data_dir 默认
    data_dir: str = ""              # 数据目录
    log_level: str = "INFO"
    json_logs: bool = False         # MING_SIM_JSON_LOGS=1 → True

    # ── 鉴权 ──
    server_users: str = ""          # "alice:pw,bob:pw2"
    auth_users: str = ""            # 别名
    admin_user: str = ""
    admin_password: str = ""
    admin_users: str = ""           # 管理员用户名列表（逗号分隔）
    server_admins: str = ""
    allow_registration: bool = True  # 默认开放（保持旧行为）
    invite_code: str = ""           # SEC-001：无默认值，空则注册关闭
    cookie_secure: bool = False
    trust_proxy_headers: bool = False

    # ── CORS ──
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # ── 调试 ──
    debug_state: bool = False       # /api/debug/state 端点开关（SECURITY：默认关）

    @property
    def cors_origin_list(self) -> List[str]:
        """CORS origins 解析为列表。"""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_server_mode(self) -> bool:
        """多用户服务器模式（启用鉴权）。"""
        return bool(self.server_users.strip() or self.auth_users.strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """返回 Settings 单例（lru_cache 保证只读一次 env）。"""
    return Settings()
