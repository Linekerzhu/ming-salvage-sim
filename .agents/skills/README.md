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

---

# GSAP 动画技能（GreenSock 官方）

来自 [greensock/gsap-skills](https://github.com/greensock/gsap-skills) 的 **8 个官方 GSAP 技能**，
原样安装（无需适配——GSAP 已是本项目选定动画库，`gsap@3.15` + `@gsap/react@2.1` 已在 `web/package.json`）。

> GSAP 自 Webflow 收购后全免费（含 SplitText/MorphSVG 等原 Club 插件），公共 npm 包即可，无需 token。

## 8 个技能

| 技能 | 用途 | 本项目典型场景 |
|------|------|----------------|
| `gsap-core` | `gsap.to/from/fromTo`、ease、duration、stagger、`gsap.matchMedia`（响应式/减弱动画） | 数值滚动、入场动画 |
| `gsap-timeline` | `gsap.timeline()`、position 参数、label、嵌套、回放 | 聊天气泡错峰编排、多步高光 |
| `gsap-scrolltrigger` | 滚动联动、pin、scrub、refresh、cleanup | （本项目单页 tab，暂未用） |
| `gsap-plugins` | Flip/Draggable/SplitText/ScrambleText/MorphSVG/CustomEase 等 | ScrambleText 可做"密旨解密"动效 |
| `gsap-utils` | `clamp/mapRange/interpolate/random/snap/wrap/pipe` | 数值映射、计数动画的 proxy |
| `gsap-react` | `useGSAP` hook、refs、`gsap.context()`、cleanup、SSR | **本项目所有 GSAP 代码的强制模式** |
| `gsap-performance` | transform 优先、will-change、batching | 60fps 保障 |
| `gsap-frameworks` | Vue/Svelte 等生命周期 | （本项目用 React，见 gsap-react） |

## 本项目 GSAP 使用约定（强制）

所有 GSAP 代码必须遵循 **gsap-react skill** 的模式，已有两个文件（`MilestoneProgress.tsx`、`AssignmentViews.tsx`）是范本：

1. **用 `useGSAP()` 而非裸 `useEffect`**——自动 cleanup，避免泄漏
2. **传 scope**（ref/element）——选择器局限于该容器，不跨组件误匹配
3. **用 refs 定位目标 DOM**——不依赖裸选择器字符串
4. **`gsap.registerPlugin()` 只调一次**（模块顶层，非每次渲染）
5. **CSS 动画作降级**——GSAP 接管主要动画，CSS 保留为无 JS 时的兜底

## 何处该用 GSAP、何处该用 CSS

| 场景 | 选 |
|------|-----|
| 数值滚动（gauge 数字 38→43） | **GSAP**（CSS 无法插值文本数字） |
| 多元素错峰/时序编排（气泡 stagger） | **GSAP timeline**（CSS stagger 表达力弱） |
| 一次性入场/退场 toast | CSS（GSAP 收益小） |
| hover/focus 微交互 | CSS（更轻、声明式） |
| 滚动驱动（本项目暂无） | GSAP ScrollTrigger |

