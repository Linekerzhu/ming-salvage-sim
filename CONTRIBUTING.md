# 贡献规范

感谢参与「明末力挽狂澜」！本文件是所有贡献者（含 AI 协作者）须遵守的工程约定。

## 快速开始

```bash
# 后端（Python 3.11+）
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt        # 直接依赖（带上限）
# 或装锁定版本：pip install -r requirements.lock

# 前端
cd web && npm install

# 跑测试（改动前必跑）
.venv/bin/python -m pytest tests/ -q   # 当前 1074+ 测试须全绿
cd web && npx tsc --noEmit             # TypeScript 须干净
```

## 分支与提交

- 主分支 `main`，PR 合入。不在 `main` 上直接提交（除非紧急 hotfix）。
- 分支命名：`feat/<scope>`、`fix/<scope>`、`docs/<scope>`、`chore/<scope>`。
- 提交信息：首行祈使句（`Add ...` / `Fix ...` / `Refactor ...`），空行后写动机与影响。
  - 涉及性能用 `perf(scope): ...`，涉及安全用 `sec(scope): ...`。
- 大改动的提交体写清「为什么」和「验证了什么」（测试数、tsc、构建耗时）。

## 工程强制力（机器执行，非散文）

下列项**违反即测试失败或启动拒绝**——贡献前必读 [`docs/engineering-architecture.md` §工程强制力](docs/engineering-architecture.md)：

| 原则 | 强制方式 | 违反后果 |
|------|----------|----------|
| module + pipeline registry 契约自洽 | `validate_module_registry()` + `validate_pipeline_registry()` 在 FastAPI lifespan 启动时跑 | 坏契约 → **服务器拒绝启动** |
| 机制层不依赖 Web 框架 | `tests/test_architecture_boundaries.py` ast 扫 import 图 | `ming_sim/*.py`（除 `*_api.py`）import fastapi → **测试失败** |
| L0 基础层不依赖上层 | 同上边界测试 | models/exceptions/paths/constants import 上层 → **测试失败** |
| 无循环依赖 | `module_dependency_order()` | 环 → **测试失败** |
| schema 版本可追溯 | `SCHEMA_VERSION` + `KV_SCHEMA_VERSION` | — |
| LLM 管道可观测 | `/metrics` 端点 + `audit_error` 落库 | — |

## 如何加一个新模块（ming_sim/）

1. 在 `ming_sim/` 下建 `<module>.py`，遵守分层（见 [`docs/project-structure.md`](docs/project-structure.md)）。
2. **必须登记 `module_registry.py` 的 `_SPECS`**：声明 id / owner / entrypoint / depends_on / pipelines / hooks / risk / hot_swap。未登记的模块在架构审查里会被标 ORPHAN。
3. 若模块含 LLM 管道，**必须登记 `pipeline_registry.py` 的 `_SPECS`**：声明 kind=llm / llm_role / failure_policy / default_max_tokens / risk。high-risk 管道不得用 `best_effort`（会静默丢）。
4. 加测试：`tests/test_<module>.py`（或并入 `tests/test_audit_integration.py` 的回归集）。
5. 跑全套测试 + tsc，确认绿。

## 如何加一个新状态 delta（双写类高风险）

本项目有历史复发的「状态 delta 双写」bug 类（petition/intrigue/session/issues/eunuch/harem/defection/strife 均曾中招）。**任何新 rollover tick / 状态变更必须加 KV-day 或 CAS 闸门**，照搬 `eunuch_power.py:295-298` 的 `KV_*_DAY` 模式：

```python
from ming_sim.upgrade_schema import KV_X_TICK_DAY, kv_int, kv_set_int
if kv_int(db, KV_X_TICK_DAY, -1) == int(day):
    return []  # 同 day 已跑过，防双漂移
kv_set_int(db, KV_X_TICK_DAY, int(day))
```

并配同 day 幂等 + 跨 day 重启的回归测试（范本：`tests/test_audit_integration.py::EunuchPowerTickIdempotencyTests`）。

## 如何改前端

- 动画：GSAP 已装（`gsap@3.15` + `@gsap/react`）。**必须用 `useGSAP` + scope + refs + 自动 cleanup**（见 `.agents/skills/gsap-react`）。范本：`web/src/mobile/views/MilestoneProgress.tsx`。
- 何处用 GSAP vs CSS：数值滚动 / 多元素错峰用 GSAP；hover/focus 微交互用 CSS。见 [`.agents/skills/README.md`](.agents/skills/README.md) 判定表。
- 业务逻辑**不得**进前端——模拟状态由 `ming_sim` + SQLite 负责（分层不变量）。

## 可观测性

- LLM 调用：经 `agents.run_agent_text`（已埋点 `metrics.record_llm_call_timed`），自动记调用/token/失败/延迟。
- 日志：HTTP 走 `logging`，LLM 走 `logging.getLogger('ming_sim.llm')`（已收口，不再裸 print）。`MING_SIM_JSON_LOGS=1` 开结构化。
- 指标：`GET /metrics`（Prometheus 文本格式）。
- 健康：`GET /healthz`（liveness）、`GET /readyz`（DB/磁盘就绪检查，失败 503）。

## 测试约定

- 零 LLM：绝大多数测试不调真实 LLM（用 fake `audit_client` 注入）。涉及 LLM 的测试用 dummy key，只验构造/解析。
- 双写回归：历史 bug 类的 9 个 tick 都有同 day 幂等测试（`tests/test_audit_integration.py`）。
- 新增功能**必须**带测试；无测试的 PR 不合入。

## 依赖管理

- `requirements.txt`：直接依赖，带上限（`>=X,<next-major`）。
- `requirements.lock`：可复现的完整版本集（`pip freeze` 生成）。部署用 lock。
- 加新依赖：先评估必要性（零新依赖优先），加到 `requirements.txt` 带上限，`pip freeze` 更新 lock，在 PR 说明用途。
