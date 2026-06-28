# 项目目录结构

本文是「明末力挽狂澜」的目录地图——每个顶层目录与入口点的职责。配合 [`engineering-architecture.md`](engineering-architecture.md)（分层不变量）与 [`architecture_inventory.md`](../architecture_inventory.md)（模块户籍）阅读。

## 顶层布局

```
ming-salvage-sim/
├── ming_sim/          # 后端核心：机制 + 管道 + DB（Python）
├── web/               # 前端：React + TypeScript + Vite
├── content/           # 静态游戏数据（JSON）：人物/事件/地区/军队/国策
├── tests/             # 测试套件（unittest，pytest 发现）
├── scripts/           # 一次性工具：生成/探针/立绘/打包
├── docs/              # 文档：设计/工程/路线图/规范
├── .agents/skills/    # AI 协作技能（CCGS + GSAP）
├── .github/workflows/ # CI：quality.yml（测试）/ release-win.yml（打包）
├── web_app.py         # FastAPI 应用（Web 入口）
├── main.py            # CLI 入口
├── launcher.py        # 桌面打包入口（pywebview）
├── start.sh           # 开发启动（前端 build + 起 web_app）
├── start-server.sh    # 服务器 systemd 启动（不跑 npm，用预构建 web/dist）
├── requirements.txt   # 直接依赖（带上限）
├── requirements.lock  # 锁定的可复现版本集
├── README.md / CHANGELOG.md / CONTRIBUTING.md / SECURITY.md
├── Dockerfile / docker-compose.yml / MingSalvageSim.spec  # 部署与打包
└── .env.example       # 环境变量模板（.env 本地不入库）
```

## 入口点

| 入口 | 用途 | 何时用 |
|------|------|--------|
| `main.py` | CLI 驱动（argparse + 启动 ming_sim CLI） | 纯文字游玩 / 调试机制 |
| `web_app.py` | FastAPI 应用（Web 入口，~13000 行） | 服务器 / 开发（`uvicorn web_app:app`） |
| `launcher.py` | 桌面打包（uvicorn 子线程 + pywebview 套壳） | PyInstaller 打包桌面版 |
| `start.sh` | 开发：前端 `npm build` + 起 web_app | 本地开发主力 |
| `start-server.sh` | 服务器 systemd：用预构建 web/dist，不跑 npm | 生产部署 |

## `ming_sim/` —— 后端核心（90 个顶层 .py + cli/ 子包）

按分层（见 `engineering-architecture.md` 的 mermaid）：

- **L0 基础**：`models.py` / `exceptions.py` / `paths.py` / `constants.py` —— 不得 import 上层（机器强制）。
- **DB 层**：`db.py`（GameDB 类，SQLite + WAL）、`upgrade_schema.py`（幂等迁移 + `SCHEMA_VERSION`）。
- **机制层**：国策/财政/疆域/军队/官制等 —— `policy_center.py` / `fiscal_center.py` / `frontier.py` / `theater.py` / `bureaucracy.py` / `lifecycle.py` / `memorials.py` / `intrigue.py` / `harem.py` / `eunuch_power.py` / `faction_dynamics.py` ... **不得 import fastapi**（机器强制，除 `*_api.py`）。
- **管道层**：`dialogue_audit.py` / `dialogue_semantics.py` / `agents.py`（LLM 审计）、`portraits.py`（立绘）、`simulation.py`。
- **应用层**：`session.py`（GameSession）、`timeflow.py`（半即时 tick）。
- **Web 接缝**：`quest_api.py` / `assignment_api.py`（仅这两个 `*_api.py` 允许 import fastapi）。
- **契约/注册**：`module_registry.py` / `pipeline_registry.py` / `hook_runner.py` / `web_payloads.py` / `web_route_contracts.py`。
- **可观测/可替换**：`metrics.py`（/metrics）、`token_stats.py`（token 统计）、`llm_provider.py`（provider 接缝）、`llm_config.py` / `llm_contract.py` / `llm_model.py`。
- **CLI**：`ming_sim/cli/`（终端/试玩/dryrun）。

## `web/` —— 前端（React 19 + TypeScript + Vite）

- `src/mobile/`：主应用（5 标签：御前/御案/召对/诏旨/国策）—— `App.tsx` / `GameData.tsx`（全局上下文）/ `ChatPane.tsx`（对话）/ `views/`（各 tab 视图）。
- `src/api/`：前端 API 客户端 + 缓存层。
- `src/upgrade.tsx` / `src/council.tsx`：管理平台 UI。
- `src/styles.css` / `src/mobile.css`：样式（~14000 行，含动画 keyframes）。
- 动画：GSAP（`MilestoneProgress.tsx` / `AssignmentViews.tsx` / `App.tsx` Gauge / `ChatPane.tsx` 是范本）。

## `content/` —— 静态游戏数据（JSON，运行时只读）

人物（`characters.json`）、事件、地区、军队、建筑、国策（`directive_categories.json`）、财政（`fiscal_config.json`）、冒险、因果。运行时依赖从轻原则：只读这些 JSON + SQLite + 静态资产。

## `tests/` —— 测试套件（unittest，1074+ 测试）

- `test_audit_integration.py`：主回归集（双写类幂等、registry 契约、启动校验）。
- `test_architecture_boundaries.py`：分层机器检查（ast import 图）。
- `test_metrics.py` / `test_schema_versioning.py` / `test_llm_provider.py`：工程基础设施。
- `test_balance_rails_regression.py`：平衡护栏（皇威漂移/势 bleed cap/超时自罢）。
- 其余按子系统：`test_assignment*` / `test_eunuch*` / `test_intrigue*` / `test_lifecycle*` ...

## `scripts/` —— 一次性工具（非运行时依赖）

生成（`generate_characters.py` / `generate_npc_foundation_from_master.py`）、立绘（`gen_portraits.py` / `compress_portraits.py` / `rembg_portraits.py`）、探针（`*_probe.py`，调试用）、打包（`make_steam_assets.py`）。**运行时不依赖这些**（engineering-architecture.md 不变量）。

> 注：`scripts/runs/`（旧 bench/probe/llm_dump log）已于 2026-06-27 清理删除。

## `docs/` —— 文档

- **规范/工程**：`engineering-architecture.md`（分层 + 工程强制力）、`project-structure.md`（本文）、`tech-debt-register.md`、`production-runbook.md`。
- **设计**：`assignment-hall-design.md`、`decree-cycle-and-reports.md`、`design-upgrade-semi-realtime.md`、`setting-outline.md`、`strategy-100turn-tech-tree.md`、`portrait-generation-spec.md`、`docs/modules/`（各子系统 GDD）。
- **架构**：`../architecture_inventory.md`（根级，模块户籍 + 5 入口战略）。
- **归档**：`docs/archive/`（已完成/迁移的旧设计：quest-system-*.md、rebuildplan.md）。

## 本地大文件（`.gitignore` 覆盖，不入库）

- `data/`：`ming_sim.db*`（运行库）、`saves/`（存档，可达数十 GB）、`server_state.sqlite3`、`runtime_llm.json`（含 api_key，0600）。
- `__pycache__/`、`web/node_modules/`、`web/dist/`、`build/`、`artifacts/`（playtest 截图，本地）。

## `.agents/skills/` —— AI 协作技能

- **CCGS**（8 个，Ming 适配）：smoke-check / security-audit / perf-profile / regression-suite / tech-debt / code-review / architecture-review / bug-triage。见 `.agents/skills/README.md`。
- **GSAP**（8 个，官方）：core / timeline / scrolltrigger / plugins / utils / react / performance / frameworks。
