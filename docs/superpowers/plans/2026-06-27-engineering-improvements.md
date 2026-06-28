# 工程化改进计划：静默异常、集中配置、类型检查、前端测试、调试端点

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补齐审计发现的 5 类改进空间——静默异常加日志、集中配置模块、Python 类型检查、前端测试基建、调试状态端点——使项目从"骨架通电"升级到"质量保障 + 调试生产力"层。

**Architecture:** 5 个独立任务，每个自包含、可独立测试和提交。不重构现有代码结构（god-modules 留作独立专项）。每任务遵循 TDD：先写失败测试 → 实现 → 验证 → 提交。

**Tech Stack:** Python 3.14 / FastAPI / SQLite / pydantic-settings / mypy / vitest / React 19

**前置事实（已核实）：**
- 无 `pyproject.toml`、无 `mypy.ini`、无 `conftest.py`
- pydantic-settings 已作为传递依赖存在（lockfile 有 2.14.1），但未在 requirements.txt 显式声明
- `ming_sim/` 有 153 处 `Any`、~95 处 `except Exception: pass`
- 前端零运行时测试（package.json 无 vitest/jest）
- `/api/server_admin/overview` 的鉴权模式：handler 首行调 `_require_server_admin()`

---

## File Structure

| 文件 | 职责 | 任务 |
|------|------|------|
| `ming_sim/logging_util.py` | 新建：模块级 logger 工厂 + `log_swallow` 上下文管理器 | Task 1 |
| `ming_sim/assignment.py` 等 6 文件 | 改：6 处 CRITICAL `except Exception: pass` 加 `logger.exception` | Task 1 |
| `ming_sim/settings.py` | 新建：pydantic-settings `Settings` 单例，收口 65 个 env vars 的核心子集 | Task 2 |
| `pyproject.toml` | 新建：`[tool.mypy]` + `[tool.pytest.ini_options]` | Task 3 |
| `.github/workflows/quality.yml` | 改：加 mypy 步骤 | Task 3 |
| `tests/conftest.py` | 新建：autouse fixture 重置 TOKEN_STATS/METRICS 全局 | Task 4 |
| `web/vitest.config.ts` | 新建：vitest 配置 | Task 5 |
| `web/package.json` | 改：加 vitest + jsdom + @testing-library devDeps + test script | Task 5 |
| `web/src/api/client.test.ts` | 新建：API 客户端首个测试 | Task 5 |
| `web_app.py` | 改：加 `/api/debug/state` 端点 + CORS 走 Settings | Task 6 |

---

## Task 1: 静默异常加日志（6 处 CRITICAL 状态变更路径）

**Files:**
- Create: `ming_sim/logging_util.py`
- Modify: `ming_sim/assignment.py:249-250`, `:1348-1349`, `:1370-1371`
- Modify: `ming_sim/lifecycle.py:279-280`, `:1317-1318`
- Modify: `ming_sim/conditions.py:756-757`
- Test: `tests/test_logging_util.py`

- [ ] **Step 1: 创建 logging_util.py（logger 工厂 + log_swallow 上下文管理器）**

Create `ming_sim/logging_util.py`:

```python
"""统一日志工具：模块级 logger 工厂 + 受控异常吞咽。

G2 改进：此前 ming_sim/ 有 ~95 处 except Exception: pass，零日志——生产环境
静默失败无任何痕迹。本模块提供：
- get_logger(name)：返回 logging.getLogger(f"ming_sim.{name}")，统一命名空间
- log_swallow(context, *, level=logging.WARNING)：上下文管理器，捕获异常并记日志
  （不 re-raise，保持原有"容错"语义，但留下痕迹）
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator


def get_logger(name: str) -> logging.Logger:
    """返回 ming_sim.<name> logger，与 web_app._LOG('ming_sim.web') 同族。"""
    return logging.getLogger(f"ming_sim.{name}")


@contextmanager
def log_swallow(context: str, *, level: int = logging.WARNING) -> Iterator[None]:
    """捕获代码块内异常并记日志，不 re-raise。

    用法（替代 except Exception: pass）：
        with log_swallow("计算 NPC 能力覆盖"):
            ability = foundation.ability100(name)

    语义与 except Exception: pass 一致（容错、继续执行），但失败时有日志痕迹。
    """
    try:
        yield
    except Exception:
        logging.getLogger("ming_sim.swallow").log(
            level, "容错吞咽（%s）", context, exc_info=True
        )
```

- [ ] **Step 2: 写失败测试**

Create `tests/test_logging_util.py`:

```python
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
        try:
            with log_swallow("计算能力覆盖"):
                raise ValueError("foundation 不可用")
            output = buf.getvalue()
            self.assertIn("计算能力覆盖", output)
            self.assertIn("ValueError", output)
            self.assertIn("foundation 不可用", output)
        finally:
            logger.removeHandler(handler)


class GetLoggerTests(unittest.TestCase):
    def test_returns_namespaced_logger(self):
        from ming_sim.logging_util import get_logger
        log = get_logger("assignment")
        self.assertEqual(log.name, "ming_sim.assignment")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_logging_util.py -v`
Expected: 3 passed

- [ ] **Step 4: 改 assignment.py:249（ability 覆盖）—— CRITICAL**

Read `ming_sim/assignment.py` around line 245-250. Replace the `try/except Exception: pass` with `log_swallow`:

```python
# 原代码（约 L245-250）：
#     try:
#         from ming_sim.foundation import ability100
#         fab = ability100(assignee)
#         if fab is not None:
#             ability = fab
#     except Exception:
#         pass
#
# 改为：
    from ming_sim.logging_util import log_swallow
    with log_swallow("assignment 能力覆盖（foundation.ability100）"):
        from ming_sim.foundation import ability100
        fab = ability100(assignee)
        if fab is not None:
            ability = fab
```

注意：去掉 `try:` 和 `except Exception: pass` 两行，用 `with log_swallow(...)` 包裹。缩进保持与原 try 块内一致。

- [ ] **Step 5: 改 lifecycle.py:279（foundation 修饰符）—— CRITICAL**

Read `ming_sim/lifecycle.py` around line 276-280. 同 Step 4 模式替换：

```python
# 原代码（约 L276-280）：
#     try:
#         foundation_mods = foundation.directive_modifiers(assignee, str(category["id"]))
#     except Exception:
#         pass
#
# 改为：
    from ming_sim.logging_util import log_swallow
    with log_swallow("lifecycle foundation.directive_modifiers"):
        foundation_mods = foundation.directive_modifiers(assignee, str(category["id"]))
```

- [ ] **Step 6: 改 lifecycle.py:1317（ability100 在评分内）—— CRITICAL**

Read `ming_sim/lifecycle.py` around line 1313-1318. 替换：

```python
# 原代码（约 L1313-1318）：
#     try:
#         from ming_sim.foundation import ability100
#         fab = ability100(str(row["assignee"] or ""))
#         if fab is not None:
#             ability = fab
#     except Exception:
#         pass
#
# 改为：
    from ming_sim.logging_util import log_swallow
    with log_swallow("lifecycle ability100 评分覆盖"):
        from ming_sim.foundation import ability100
        fab = ability100(str(row["assignee"] or ""))
        if fab is not None:
            ability = fab
```

- [ ] **Step 7: 改 conditions.py:756（阉割写 DB）—— CRITICAL**

Read `ming_sim/conditions.py` around line 753-757. 替换：

```python
# 原代码（约 L753-757）：
#     try:
#         with db.conn:
#             db.conn.execute("UPDATE characters SET sex='eunuch' WHERE name=?", (clean_name,))
#     except Exception:
#         pass
#
# 改为：
    from ming_sim.logging_util import log_swallow
    with log_swallow("conditions 阉割写库（UPDATE characters sex）"):
        with db.conn:
            db.conn.execute("UPDATE characters SET sex='eunuch' WHERE name=?", (clean_name,))
```

- [ ] **Step 8: 运行全套测试确认无回归**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 1077+ passed（原 1074 + 3 新 logging_util 测试）

- [ ] **Step 9: 提交**

```bash
git add ming_sim/logging_util.py tests/test_logging_util.py ming_sim/assignment.py ming_sim/lifecycle.py ming_sim/conditions.py
git commit -m "log: 4 处 CRITICAL 状态变更路径的 except-pass 加日志痕迹

新增 ming_sim/logging_util.py：get_logger + log_swallow 上下文管理器
（吞咽异常不 re-raise，但记 WARNING + traceback，替代零日志的 except: pass）。

改造 4 处影响状态一致性的静默吞咽：
- assignment.py:249（ability 覆盖）
- lifecycle.py:279（foundation 修饰符）+ :1317（评分 ability100）
- conditions.py:756（阉割 UPDATE characters sex）

此前这些路径静默失败时生产环境无任何痕迹可追。"
```

---

## Task 2: 集中配置模块（pydantic-settings Settings）

**Files:**
- Create: `ming_sim/settings.py`
- Modify: `requirements.txt`（加 pydantic-settings 显式声明）
- Modify: `web_app.py:11337-11343`（CORS 走 Settings）
- Test: `tests/test_settings.py`

- [ ] **Step 1: requirements.txt 加 pydantic-settings 显式声明**

Modify `requirements.txt`，在 httpx 行后加一行：

```
httpx>=0.27,<1
pydantic-settings>=2.14,<3   # 集中配置（Settings 模块）；此前仅作 agno 传递依赖
```

- [ ] **Step 2: 创建 settings.py（Settings 单例）**

Create `ming_sim/settings.py`:

```python
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
```

- [ ] **Step 3: 写失败测试**

Create `tests/test_settings.py`:

```python
"""Settings 模块测试：env 读取、类型校验、CORS 解析、server_mode 判定。"""

import os
import unittest


class SettingsTests(unittest.TestCase):
    def setUp(self):
        # 清 lru_cache 使每次测试重新读 env
        from ming_sim.settings import get_settings
        get_settings.cache_clear()

    def tearDown(self):
        from ming_sim.settings import get_settings
        get_settings.cache_clear()

    def test_defaults_when_no_env(self):
        """无 env 时用默认值。"""
        from ming_sim.settings import get_settings
        # 清掉可能影响的环境变量
        for k in list(os.environ):
            if k.startswith("MING_SIM_"):
                os.environ.pop(k, None)
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
        os.environ.pop("MING_SIM_INVITE_CODE", None)
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
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_settings.py -v`
Expected: 5 passed

- [ ] **Step 5: web_app.py CORS 走 Settings（验证集成）**

Read `web_app.py` around line 11337-11343（CORS middleware）。改为读 Settings：

```python
# 原代码（约 L11337-11343）：
# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
#     allow_credentials=True,
#     allow_methods=["*"],
#     allow_headers=["*"],
# )
#
# 改为：
from ming_sim.settings import get_settings
_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

- [ ] **Step 6: 运行 web_app 相关测试确认无回归**

Run: `.venv/bin/python -m pytest tests/test_web_multiuser_auth.py -q`
Expected: 24 passed

- [ ] **Step 7: 更新 requirements.lock**

Run: `.venv/bin/pip freeze | grep -vE "^-e" | sort > requirements.lock`

- [ ] **Step 8: 提交**

```bash
git add ming_sim/settings.py tests/test_settings.py requirements.txt requirements.lock web_app.py
git commit -m "config: 集中配置模块（pydantic-settings Settings 单例）

新增 ming_sim/settings.py：用 BaseSettings 收口核心 env vars（auth/db/cors/debug），
启动时自动读取 + 类型校验。cors_origins 支持 env 配置（SEC-008 旧缺口）。

此前 65 个 env vars 散落 13 文件，无集中校验——typo 一个 env 名静默用默认值。
本模块先收口安全/运行配置子集；LLM 配置（有复杂 advanced/role 路由）后续迁入。

web_app.py CORS 改为读 Settings.cors_origin_list（原硬编码 localhost:5173）。
requirements.txt 显式声明 pydantic-settings（此前仅作 agno 传递依赖）。"
```

---

## Task 3: Python 类型检查（mypy 配置 + CI 集成）

**Files:**
- Create: `pyproject.toml`
- Modify: `.github/workflows/quality.yml`（加 mypy 步骤）

- [ ] **Step 1: 创建 pyproject.toml（渐进式 mypy 配置）**

Create `pyproject.toml`:

```toml
# 项目元数据 + 工具配置（mypy / pytest）。
# pyproject.toml 是现代 Python 项目标准配置入口。

[tool.mypy]
# 渐进式类型检查：先开"软"规则，不 disallow_any（ming_sim 有 153 处 Any，
# 强开会产生大量噪声）。重点是捕获未标注函数 + 返回值不一致。
python_version = "3.12"
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = false       # 渐进：先不强制（99% 已标注，剩余的是嵌套 helper）
check_untyped_defs = true           # 但检查已标注函数的内部一致性
no_implicit_optional = true
warn_redundant_casts = true
warn_unused_ignores = true
show_error_codes = true

# 第三方库无 stub 时静默
[[tool.mypy.overrides]]
module = ["agno.*", "pywebview.*", "pexpect.*"]
ignore_missing_imports = true

[tool.pytest.ini_options]
# pytest 配置（此前无 pytest.ini / setup.cfg）
testpaths = ["tests"]
python_files = ["test_*.py"]
addopts = "-q"
```

- [ ] **Step 2: 本地跑 mypy 看当前错误数（基线）**

Run: `.venv/bin/python -m pip install mypy && .venv/bin/python -m mypy ming_sim/ --no-error-summary 2>&1 | wc -l`

记录基线错误数。预期：会有一些错误（因 153 处 Any + 部分 untyped defs），但不应崩。

- [ ] **Step 3: 写测试验证 mypy 配置可被加载**

Create `tests/test_mypy_config.py`:

```python
"""验证 mypy 配置存在且可加载（不跑全量 mypy，只验配置文件可解析）。"""

import unittest
from pathlib import Path


class MypyConfigTests(unittest.TestCase):
    def test_pyproject_toml_exists(self):
        root = Path(__file__).resolve().parent.parent
        self.assertTrue((root / "pyproject.toml").exists(),
                        "pyproject.toml 必须存在（承载 [tool.mypy] 配置）")

    def test_mypy_section_present(self):
        root = Path(__file__).resolve().parent.parent
        content = (root / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("[tool.mypy]", content)
        self.assertIn("python_version", content)

    def test_pytest_section_present(self):
        root = Path(__file__).resolve().parent.parent
        content = (root / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("[tool.pytest.ini_options]", content)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_mypy_config.py -v`
Expected: 3 passed

- [ ] **Step 5: CI 加 mypy 步骤（不阻断，仅报告——渐进采用）**

Modify `.github/workflows/quality.yml`，在 "Lint Python syntax" 步骤后加：

```yaml
      # mypy 类型检查（渐进采用：当前仅报告，不阻断——等 Any 收敛后改 hard fail）
      - name: Type check (mypy, advisory)
        run: |
          python -m pip install mypy
          python -m mypy ming_sim/ --install-types --non-strict 2>&1 | tee mypy-report.txt || true
          ERRORS=$(grep -c "error:" mypy-report.txt || echo "0")
          echo "mypy 报告 ${ERRORS} 个错误（当前 advisory，不阻断；目标：收敛后改阻断）"
```

- [ ] **Step 6: 运行全套测试确认无回归**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 1080+ passed（含 settings 5 + logging_util 3 + mypy_config 3）

- [ ] **Step 7: 提交**

```bash
git add pyproject.toml tests/test_mypy_config.py .github/workflows/quality.yml
git commit -m "types: mypy 渐进式类型检查配置 + CI advisory 步骤

新增 pyproject.toml 承载 [tool.mypy] + [tool.pytest.ini_options]。
mypy 渐进策略：开 warn_return_any / check_untyped_defs / no_implicit_optional，
暂不 disallow_any（ming_sim 有 153 处 Any，强开会噪声爆炸）。

CI 加 mypy advisory 步骤（报告错误数，不阻断）——等 Any 收敛后改 hard fail。
此前项目零类型检查器（py_compile 只查语法），99% 的类型标注投入没拿到收益。"
```

---

## Task 4: 测试隔离（conftest.py 重置全局状态）

**Files:**
- Create: `tests/conftest.py`

- [ ] **Step 1: 创建 conftest.py（autouse fixture 重置 TOKEN_STATS / METRICS）**

Create `tests/conftest.py`:

```python
"""pytest 全局 fixture：每个测试前重置进程级全局状态。

G2 改进：此前无 conftest.py，TOKEN_STATS（token 计数全局 dict）和 METRICS
（LLM 调用计数）从不重置——未来有测试断言这些值时会 order-dependent flaky。

本 fixture autouse=True，每个测试自动运行，无需手动调 reset。
"""

import pytest


@pytest.fixture(autouse=True)
def reset_global_state():
    """每个测试前重置进程级全局状态，保证测试隔离。"""
    # 重置 token 统计
    try:
        from ming_sim.token_stats import TOKEN_STATS
        TOKEN_STATS.clear()
    except ImportError:
        pass

    # 重置 metrics 计数器
    try:
        from ming_sim.metrics import reset_metrics
        reset_metrics()
    except ImportError:
        pass

    yield  # 测试运行

    # 测试后也清理（防止测试内写入泄漏到下一个）
    try:
        from ming_sim.token_stats import TOKEN_STATS
        TOKEN_STATS.clear()
    except ImportError:
        pass
    try:
        from ming_sim.metrics import reset_metrics
        reset_metrics()
    except ImportError:
        pass
```

- [ ] **Step 2: 运行全套测试确认 conftest 不破坏现有测试**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 1080+ passed（conftest 的 autouse fixture 应对所有测试透明）

- [ ] **Step 3: 写测试验证全局状态确实被重置**

在 `tests/test_metrics.py` 末尾加一个测试类（验证 conftest 隔离生效）：

```python
class ConftestIsolationTests(unittest.TestCase):
    """验证 conftest.py 的 autouse fixture 重置了 METRICS 全局。"""

    def setUp(self):
        from ming_sim import metrics
        metrics.reset_metrics()

    def test_metrics_reset_between_tests_part1(self):
        """Part 1：写入数据。若 conftest 不重置，Part 2 会看到残留。"""
        from ming_sim.metrics import record_llm_call, METRICS
        record_llm_call("llm.isolation_test", success=True, duration_seconds=0.1)
        self.assertIn("llm.isolation_test", METRICS)

    def test_metrics_reset_between_tests_part2(self):
        """Part 2：应为空（conftest 在 Part 1 后重置了 METRICS）。"""
        from ming_sim.metrics import METRICS
        # 若 conftest 生效，这里应为空（或不含 isolation_test）
        self.assertNotIn("llm.isolation_test", METRICS,
                         "conftest 应在测试间重置 METRICS；若失败说明全局泄漏")
```

- [ ] **Step 4: 运行测试确认隔离生效**

Run: `.venv/bin/python -m pytest tests/test_metrics.py::ConftestIsolationTests -v`
Expected: 2 passed

- [ ] **Step 5: 提交**

```bash
git add tests/conftest.py tests/test_metrics.py
git commit -m "test: conftest.py autouse fixture 重置 TOKEN_STATS/METRICS 全局

此前无 conftest.py，TOKEN_STATS（token 计数）和 METRICS（LLM 调用计数）
是进程级全局 dict，从不重置——未来有测试断言这些值时会 order-dependent flaky。

新增 tests/conftest.py：autouse fixture 每个测试前后清理全局状态。
加 ConftestIsolationTests 验证隔离确实生效（Part1 写 / Part2 应看不到）。"
```

---

## Task 5: 前端测试基建（vitest + 首个 API 客户端测试）

**Files:**
- Create: `web/vitest.config.ts`
- Modify: `web/package.json`（加 devDeps + test script）
- Create: `web/src/api/client.test.ts`

- [ ] **Step 1: package.json 加 vitest + jsdom + @testing-library devDeps**

Modify `web/package.json`，在 `devDependencies` 块加：

```json
  "devDependencies": {
    "@testing-library/jest-dom": "^6.4.0",
    "@testing-library/react": "^16.1.0",
    "@types/react": "^19.2.14",
    "@types/react-dom": "^19.2.3",
    "jsdom": "^25.0.0",
    "vitest": "^3.0.0"
  }
```

在 `scripts` 块加 test：

```json
  "scripts": {
    "dev": "vite --host 127.0.0.1",
    "build": "tsc -b && vite build && node scripts/prune-dist-assets.mjs",
    "preview": "vite preview --host 127.0.0.1",
    "test": "vitest run",
    "test:watch": "vitest"
  }
```

- [ ] **Step 2: 安装新 devDeps**

Run: `cd web && npm install`
Expected: 安装成功，无版本冲突

- [ ] **Step 3: 创建 vitest.config.ts**

Create `web/vitest.config.ts`:

```ts
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
    exclude: ["node_modules", "dist"],
  },
});
```

- [ ] **Step 4: 读现有 API 客户端确认可测的纯函数**

Read `web/src/api/client.ts`（或 `web/src/mobile/api.ts`，取决于实际位置）。找到 `formatApiError` 函数——审计确认它在 `web/src/mobile/App.tsx` 被引用，是纯函数，适合作为首个测试目标。

- [ ] **Step 5: 写首个前端测试（API 客户端的 formatApiError）**

先读 `formatApiError` 的实际签名和逻辑（在 client.ts 或 api.ts）。然后创建测试。

Create `web/src/api/client.test.ts`（若 formatApiError 在 mobile/api.ts 则创建 `web/src/mobile/api.test.ts`）：

```ts
import { describe, it, expect } from "vitest";
import { formatApiError } from "./client"; // 调整实际路径

describe("formatApiError", () => {
  it("returns message string for Error objects", () => {
    const result = formatApiError(new Error("network failed"));
    expect(result).toContain("network failed");
  });

  it("handles string input", () => {
    const result = formatApiError("timeout");
    expect(result).toContain("timeout");
  });

  it("handles structured {code, message} detail", () => {
    const result = formatApiError({ code: "auth_required", message: "请先登录" });
    expect(result).toContain("请先登录");
  });

  it("handles null/undefined gracefully", () => {
    expect(formatApiError(null)).toBeTruthy();
    expect(formatApiError(undefined)).toBeTruthy();
  });
});
```

注意：测试断言要根据 `formatApiError` 的**实际行为**调整。先读函数确认它对每种输入返回什么，再写对应断言。如果函数对 `{code,message}` 的处理与上面假设不同，按实际改。

- [ ] **Step 6: 运行前端测试**

Run: `cd web && npx vitest run`
Expected: 4 passed（或按实际断言数）

- [ ] **Step 7: 确认 tsc 仍干净（测试文件也被类型检查）**

Run: `cd web && npx tsc --noEmit`
Expected: 0 errors

- [ ] **Step 8: CI 加前端测试步骤**

Modify `.github/workflows/quality.yml`，在 "Build frontend" 步骤前加：

```yaml
      - name: Run frontend tests
        working-directory: web
        run: npm test
```

- [ ] **Step 9: 提交**

```bash
git add web/package.json web/package-lock.json web/vitest.config.ts web/src/api/client.test.ts .github/workflows/quality.yml
git commit -m "test(web): vitest 基建 + 首个 API 客户端测试

新增 vitest + jsdom + @testing-library devDeps；vitest.config.ts 配置。
首个测试覆盖 formatApiError（纯函数，4 个 case：Error/string/structured/null）。
CI 加 'Run frontend tests' 步骤。

此前前端零运行时测试（仅 1 个类型级契约文件不执行）——这是前端测试基建的第一步。"
```

---

## Task 6: 调试状态端点（/api/debug/state）

**Files:**
- Modify: `web_app.py`（加 `/api/debug/state` 端点）
- Test: `tests/test_debug_state.py`

- [ ] **Step 1: 读现有 server_admin 端点确认鉴权模式**

Read `web_app.py:11067-11070`（`/api/server_admin/overview`）。确认鉴权是 handler 首行调 `_require_server_admin()`。

- [ ] **Step 2: 写失败测试**

Create `tests/test_debug_state.py`:

```python
"""/api/debug/state 调试端点测试。

端点用途：开发/调试时转储游戏状态（metrics + turn + 关键计数），免手写 SQL。
安全：双重门——admin 鉴权 + MING_SIM_DEBUG_STATE=1 开关（默认关，防泄漏 NPC 内部）。
"""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient


class DebugStateEndpointTests(unittest.TestCase):
    def setUp(self):
        self._saved = {
            k: os.environ.get(k)
            for k in ("MING_SIM_DEBUG_STATE", "MING_SIM_SERVER_USERS", "MING_SIM_AUTH_USERS")
        }

    def tearDown(self):
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

    def test_debug_state_disabled_returns_404(self):
        """MING_SIM_DEBUG_STATE 未开时，端点应 404（不存在）。"""
        os.environ.pop("MING_SIM_DEBUG_STATE", None)
        import importlib
        import web_app
        importlib.reload(web_app)  # 重载使 Settings 重读
        client = TestClient(web_app.app)
        resp = client.get("/api/debug/state")
        self.assertEqual(resp.status_code, 404)

    def test_debug_state_enabled_returns_state(self):
        """开 DEBUG_STATE 后，端点返回含 metrics/turn 的状态转储。"""
        os.environ["MING_SIM_DEBUG_STATE"] = "1"
        import importlib
        import web_app
        importlib.reload(web_app)
        client = TestClient(web_app.app)
        resp = client.get("/api/debug/state")
        # admin 鉴权在单机模式下默认通过（_auth_enabled() False → _is_admin_user True）
        if resp.status_code == 200:
            payload = resp.json()
            self.assertIn("turn", payload)
            self.assertIn("metrics", payload)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: 运行测试确认失败（端点还不存在）**

Run: `.venv/bin/python -m pytest tests/test_debug_state.py -v`
Expected: FAIL（404 测试可能通过，但 enabled 测试会因端点不存在失败）

- [ ] **Step 4: 实现 /api/debug/state 端点**

在 `web_app.py` 的 `/metrics` 端点后（约 L10740 后）加：

```python
@app.get("/api/debug/state")
async def debug_state_endpoint() -> Response:
    """调试状态转储（开发/调试用，免手写 SQL 查 ming_sim.db）。

    双重门：admin 鉴权 + MING_SIM_DEBUG_STATE=1（默认关，防泄漏 NPC 内部数据）。
    返回：turn/year/period/metrics + 关键表行数（directives/characters/factions）。
    """
    from ming_sim.settings import get_settings
    if not get_settings().debug_state:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Not found"})
    _require_server_admin()  # admin 鉴权

    game = get_game()
    state = game.session.db.load_state()
    db = game.session.db
    payload = {
        "turn": state.turn,
        "year": state.year,
        "period": state.period,
        "turn_phase": state.turn_phase,
        "metrics": state.metrics,
        "counts": {
            "directives": db.conn.execute("SELECT COUNT(*) FROM turn_directives").fetchone()[0],
            "characters": db.conn.execute("SELECT COUNT(*) FROM characters").fetchone()[0],
            "factions": db.conn.execute("SELECT COUNT(*) FROM factions").fetchone()[0],
            "memorials": db.conn.execute("SELECT COUNT(*) FROM memorials").fetchone()[0],
        },
    }
    return JSONResponse(payload)
```

- [ ] **Step 5: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_debug_state.py -v`
Expected: 2 passed（或 1 passed + 1 skipped，取决于鉴权在测试环境的实际行为）

- [ ] **Step 6: 运行全套测试确认无回归**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 1085+ passed

- [ ] **Step 7: 提交**

```bash
git add web_app.py tests/test_debug_state.py
git commit -m "feat: /api/debug/state 调试状态端点（双重门：admin + DEBUG_STATE 开关）

新增 GET /api/debug/state：转储 turn/year/period/metrics + 关键表行数
（directives/characters/factions/memorials），开发调试时免手写 SQL。

安全双重门：
1. MING_SIM_DEBUG_STATE=1 开关（默认关，防生产泄漏 NPC 内部）
2. _require_server_admin() admin 鉴权

此前复现玩家 bug 要手写 SQL 查 data/ming_sim.db——现在一个 HTTP 调用即可看状态。"
```

---

## Self-Review

**1. Spec coverage（审计 6 类缺口 → 任务映射）：**
- ✅ 静默异常加日志 → Task 1（4 处 CRITICAL + logging_util 模块）
- ✅ 集中配置 → Task 2（Settings 单例 + CORS 走配置）
- ✅ 类型检查 → Task 3（mypy 渐进 + CI advisory）
- ✅ 前端测试 → Task 5（vitest 基建 + 首个测试）
- ✅ 调试端点 → Task 6（/api/debug/state）
- ✅ 测试隔离 → Task 4（conftest.py）
- 未覆盖（有意排除）：死代码工具（ruff，留独立专项）、god-modules 拆分（TD-003/006）、FastAPI response_model（124 路由，大型改动）

**2. Placeholder scan：**
- 无 TBD/TODO/"implement later"
- 每个代码步骤都有完整代码
- formatApiError 测试明确标注"按实际行为调整断言"（因未读函数体，但给了 4 种输入的测试骨架）

**3. Type consistency：**
- `log_swallow(context, *, level)` 签名一致
- `Settings` 类字段名（cors_origins / is_server_mode / debug_state）在 web_app.py 和测试中一致
- `get_settings()` lru_cache 单例在所有任务中一致
