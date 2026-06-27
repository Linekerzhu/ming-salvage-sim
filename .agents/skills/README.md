# CCGS 工作流技能（Ming 适配版）

本目录是从 [Claude Code Game Studios](https://github.com/donchitos/claude-code-game-studios)
（49 agent / 72 skill 的游戏工作室框架）中**精选 8 个引擎无关的工作流技能**，并适配到本项目的
Python / FastAPI / SQLite / agno-LLM / React-TS 技术栈。

> 原仓库的其余 ~60 个技能是 Godot / Unity / Unreal 引擎专用，与本项目（文本政治模拟）无关，未安装。

## 为什么是这 8 个

本项目（`architecture_inventory.md` 记录）的核心工程风险是：**模块过多、缺父级、状态双写**。
这 8 个技能恰好覆盖识别→追踪→验证的完整闭环：

| 技能 | 用途 | 关键产出 |
|------|------|----------|
| `/ccgs-smoke-check` | 发布前冒烟门禁（pytest + tsc + 5 入口） | `docs/qa/smoke-[date].md` |
| `/ccgs-regression-suite` | 把 tests/ 映射到关键路径，找出缺回归的 bug | `tests/regression-suite.md` |
| `/ccgs-security-audit` | SQL注入 / LLM提示注入 / 鉴权 / 密钥泄露 | `docs/security/security-audit-[date].md` |
| `/ccgs-perf-profile` | 后端热路径 / 每回合 LLM 调用数 / SQLite 查询数 / 前端包体 | 报告 |
| `/ccgs-tech-debt` | 扫描债务（含"无父级模块"这一本项目特有类别） | `docs/tech-debt-register.md` |
| `/ccgs-code-review` | 单模块代码审查（按 engineering-architecture.md 分层） | 报告（只读） |
| `/ccgs-architecture-review` | 架构审查 + 模块→入口点映射 + 孤儿模块清单 | `docs/architecture-review-[date].md` |
| `/ccgs-bug-triage` | bug 分类，**专门盯"状态 delta 双写"这一历史复发模式** | `docs/bugs/bug-triage-[date].md` |

## 适配要点

每个技能都做了以下本地化（而非原样照搬）：

- **测试运行器**：原版用 Godot headless / Unity editor；本版用 `.venv/bin/python -m pytest tests/`
- **关键路径来源**：原版用 GDD acceptance criteria；本版用 5 个一级入口（御前/御案/召对/诏旨/国策）+ `architecture_inventory.md` 的 16 子系统
- **架构标准**：原版用 ADR；本版用 `docs/engineering-architecture.md` 的 6 条不变量
- **安全类别**：原版 save-tampering / network-packet / anti-cheat；本版 SQL注入 / LLM提示注入 / FastAPI鉴权 / 密钥泄露
- **性能预算**：原版 FPS / draw call / 帧时间；本版 每回合墙钟时间 / LLM调用数 / SQLite查询数 / 前端包体
- **历史 bug 模式**：`ccgs-bug-triage` 和 `ccgs-smoke-check` 都把"状态 delta 双写"（petition/intrigue/session/issues/eunuch 的历史复发类）作为一类一等公民来盯

## 调用方式

在对话中输入 `/ccgs-smoke-check full` 等，或让 agent 在合适时机自动调用。
所有技能都遵守 CCGS 的协作协议：**只读分析→展示草稿→经批准才写文件**。
