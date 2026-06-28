# 更新日志

本项目所有重要变更记录于此。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)。

## [未发布] · 工程化深化（可维护 · 可观测 · 可升级 · 可替换 · 清洗）

v0.5.0 之后连续 4 轮工程化：从原型演进为有机器强制力的工程架构。

### 工程化（骨架通电）
- **可维护**：新增 `validate_pipeline_registry()`（此前 pipeline 无 validator）；FastAPI `lifespan` 启动时强校验 module+pipeline 契约，坏契约**拒绝启动**（fail_closed）。修复了一个坏的 no-op 测试（查不存在的 `spec.name`）。新增 `tests/test_architecture_boundaries.py`：ast 扫 import 图，机器强制分层（机制层不得 import fastapi、L0 基础不得 import 上层、无环）。
- **可观测**：新增 `ming_sim/metrics.py`（零依赖）+ `/metrics` 端点（Prometheus 文本：LLM 调用/token/失败率/延迟直方图）；`agents.run_agent_text` 埋点；`token_stats.tlog` 收口到 `logging`（不再裸 print）；`dialogue_goals` 持久化 `audit_error`（此前只存 status 丢了根因）。
- **可升级**：新增 `SCHEMA_VERSION` 全局版本号 + `KV_SCHEMA_VERSION` 记录存档代际，`ensure_upgrade_schema` 幂等向前迁移（向前只增不回退）。
- **可替换**：新增 `ming_sim/llm_provider.py` 收口 agno Agent 构造 + `LLM_BACKEND` 开关；dialogue_audit 已迁入作参考实现。`requirements.txt` 加 `<next-major` 上限；新增 `requirements.lock` 锁定可复现版本集。

### 双写类根治
- 给 4 个未加闸门的 rollover tick 加 KV-day 闸门（复刻 eunuch_power 模式）：`harem_tick`/`duishi_tick`/`defection_tick`/`strife_tick`。项目历史复发的「状态 delta 双写」bug 类（petition/intrigue/session/issues/eunuch/harem/defection/strife）现 9/9 都有闸门 + 幂等测试。
- 新增 `test_balance_rails_regression.py`：皇威漂移不棘轮、势 bleed cap -3、超时自罢。

### 安全硬化
- SEC-001：移除硬编码默认邀请码 `shdl95598`（须显式设 `MING_SIM_INVITE_CODE`）。
- SEC-003：`runtime_llm.json` 写盘 `chmod 0600`。
- SEC-004：多用户服务器模式关闭 `/docs` `/redoc` `/openapi.json`。
- SEC-005：legacy `sha256:` 口令哈希加弃用警告。
- （SEC-002：`.env` API key 轮换须运营者手动做——见 [SECURITY.md](SECURITY.md)。）

### 前端 / UX
- 安装 8 个官方 GSAP skills 到 `.agents/skills/`（gsap 已是选定库，直接适用）。
- Gauge 数值滚动动画（推进时日时君威/任事平滑滚到新值，不再瞬变）。
- 聊天气泡 stagger timeline（新消息错峰浮入，而非整批同时弹）。
- 上轮 `GameData` context value `useMemo` 防整树重渲染。

### AI 协作技能
- 安装 8 个 CCGS 工作流技能（Ming 适配：smoke-check/security-audit/perf-profile/regression-suite/tech-debt/code-review/architecture-review/bug-triage）到 `.agents/skills/`。
- 跑完全部 8 技能审计，产出 `docs/tech-debt-register.md`。

### 清洗与规范化（本次）
- 删除过时追踪文件：根 `主页1.png`（7MB，已被 docs/screenshots/home.png 取代）、`test_quest_system.py`（quest 已迁移）、`scripts/runs/`（57MB 旧 log）、`docs/邸报房推敲`（prompt dump）。
- 归档已完成/迁移的设计文档到 `docs/archive/`：`quest-system-*.md`、`rebuildplan.md`。
- 新增工程规范：`CONTRIBUTING.md`、`SECURITY.md`、`docs/project-structure.md`。
- 刷新 `README.md`（工程化段 + 修正 invite code 说明）、本 CHANGELOG。
- `engineering-architecture.md` 新增「工程强制力」表（散文原则 → 机器契约）。

### 验证
1074/1074 测试通过（v0.5.0 后净增 ~35 测试），tsc 干净。

## [2026-06-12] · v0.5.0「半即时 · 黑箱 · 基座」

大版本：半即时时间引擎与升级总案（docs/upgrade-master-plan.md）M0-M6 全量落地 + NPC 数据基座深度接入。
旧档自动迁移，向后兼容。

### 修复（发布前审计，docs/upgrade-master-plan.md 状态附录同步）
- **队列 worker 生命周期（P0）**：新游戏/读档/退出登录/管理员删库前停掉 LLM 队列 worker 并释放其 sqlite 连接——否则 Windows 上换档因文件句柄被占必然失败，POSIX 上 worker 绑死旧 inode 静默失效；换档/改 LLM 配置后自动重挂并热更配置。
- **结算互斥（P0）**：半即时层全部写路由（推进时间/批红/干预/问责/买单/信号/密查/征辟）加 `turn_resolution_lock` 守卫——流式颁诏结算期间并发写会造成旬税赋重复落账与 metrics 丢更新，现统一 409。
- **地图兵额泄露（P1）**：`/api/map` 的明军兵额改为兵部账面口径，与国势面板一致（此前真值直出，整个空饷黑箱被旁路）。
- **朔日逻辑（P1）**：派系 heat 月衰减与注意力刷新挪到开月例行（日 tick 从每月第 2 日起，原 dim==1 分支是死代码——heat 只升不降、朔日批红用残量精力）。
- **召对撤回时间线闸门（P1）**：召对快照不含 kv/奏疏/调度表，「召对→推进N天→撤回」会把执行中旨意回滚而日历不回滚；现记录召对发生日，跨日禁撤。
- **进度自校正（P2）**：旨意进度改按「剩余进度/剩余天数」推进——工期中途延长自动摊薄、提前颁诏跳日自动追平、完成日与 eta 严格对齐（原实现 >100 天工期会提前封顶完成）。
- 其它：密查回报结算失败不再静默吞掉（留痕并以挫败叙事兜底，节点不白费政治成本）；队列未知任务标 failed 而非空 done；基座共享只读连接全程持锁（不再依赖 sqlite3 编译期线程模式）；冻结发行版基座路径支持 `~/.ming_sim/foundation.sqlite`；前端时钟条改 fixed 定位不再遮挡国势栏、结算后自动刷新、「奏报100成」文案修正；`.env.example` 邀请码改占位符并补 `MING_FOUNDATION_DB` 说明；requirements 显式声明 httpx。

### 新增（NPC 数据基座深度接入）
- **基座适配层** `ming_sim/foundation.py`：只读挂载 `~/Documents/大明王朝1628/master_data.sqlite`（可经 `MING_FOUNDATION_DB` 或 `data/foundation.sqlite` 覆盖）。581 NPC 与游戏 263 人名册 100% 命中；基座缺席时全部函数静默降级，游戏照常可玩。基座永不写回——运行态变化仍走游戏 characters 表。
- **大臣 agent 人格深化**：每个大臣的 system 静态段注入基座 `full_prompt`（身份/韬治识略望/八轴人格/人格简报/擅痼特质/职权）+ 明史体小传 + 扮演铁律（标志性行为必须演出来、立场裂缝是可说动处、痼疾是行为模式而非说明书）。按人静态、前缀缓存友好。
- **执行检定深接（S2×基座）**：主办官员才总(46-80)折百替代游戏 ability；擅/绝艺对口类别给检定 +score 与工期因子（知兵→军务+10/×0.85、精于钱谷→钱粮赈济、清丈能手→税政清查、考成法→全类+8 且截留/拖延权重-20…）；痼疾偏置异常权重（贪墨成性→截留+30、优柔寡断→拖延+20、刚愎自用→封驳+10、好大喜功→意外+15…）。换人接办时新主办的特质修正即时重算。
- **印象档案风闻（S3×基座）**：擅=朝野共知即刻可见；痼/绝艺须召对≥20 次或密查后方显，未显时仅提示「此人尚有未显之性」——初见知其长，久识方知其病。
- **人才池闭环（S7×基座）**：250 名在世大明赋闲/在野真实历史人物（高斗枢/张学颜/孙传庭/卢象升…按才总排序）。三条入口：派系荐人出招四成概率保举起复真人（批准即入朝）、司天台「遗贤在野」直接征辟（耗注意力2）、`/api/foundation/candidates`+`/api/foundation/recruit`。入朝映射：loyalty←世轴、integrity←义轴、courage←豪轴、ability←才总、wisdom←识。
- **史笔×命批**：终局立传 payload 注入身故要员的基座命批诗，史官可化作传中谶语/挽笔。
- 测试：`tests/test_foundation.py`（10 个，含降级路径与名册全覆盖校验）+ `tests/test_release_fixes.py`（审计修复回归 5 个）；本版全套 162 测试通过。

### 新增（升级总案 docs/upgrade-master-plan.md 全量实施，M0-M6）
- **半即时时间引擎（S1）**：最小单位「日」，turn=月=30日；规则层日 tick 零 LLM（旬税赋 10/20/30 日三等分落账、军饷/建筑朔日整月结、调度表节点、因果伏笔检查）；月末必停等颁诏，红事件必停、黄事件可选停。模块 `ming_sim/timeflow.py`；`decree.resolve_directives` 的固定财政入口改走 `timeflow.month_fixed_flows`（未激活时完全兼容旧整月落账）。API：`/api/time`、`/api/time/advance`、`/api/time/speed`。
- **硬阈值系统（S10）**：「是否发生由规则决定，如何发生由 LLM 叙述」。`content/threshold_rules.json` 数据驱动（军队哗变=欠饷≥6月且士气≤40、地区民变、国库连月赤字危机、民心崩解），日扫描经 `event_to_issue` 硬立项，冷却防重复；提取 schema 既成事实由 `month_event_log` 注入推演（史官约束）。前端预警仪表 `/api/thresholds`（读呈报口径——账被做假仪表同样骗你，与 S3 设计统一）。因果伏笔表 `causal_seeds`（裁驿站→两年后驿卒从贼式延迟代价）。
- **指令生命周期（S2）**：每道旨意 颁诏→送达→执行（旬检定）→办结/封驳/收回；工期=基础工期×官员能力×距离×阻力（`content/directive_categories.json` 11 类）；旬检定产出拖延/截留/封驳/意外，截留走账实分叉（奏报十成、实办六成），办结落 `report_ledger` 待密查揭穿；中途干预：催办/换人/加拨/收回/独断（各有 势/任事/怨气 代价）。跨月持续执行，注入 simulator（`executing_directives`，效果未落地不得按完成抽取）。模块 `ming_sim/lifecycle.py`。
- **异步 LLM 队列（S1.4）**：`ming_sim/scheduler.py` + `llm_jobs` 表，每存档一个 daemon worker（独立 DB 连接）；任务：异常陈情疏、办结复命、票拟、派系出招文案；LLM 失败一律模板兜底，游戏不停摆。
- **御案系统（S4）**：奏疏流（请旨/请款/陈情/告变/荐人/弹章/密揭）随日送达；每日注意力 12 点（细读2/批答1/留中免——留中的诱惑即陷阱）；批红四式：照准/驳回/留中/发部议（自动生成旨意草案随诏下达）；留中积压→上疏人怨气、弹章留中折势、逾月增观望；票拟由当值阁臣 LLM 生成（带派系滤镜，模板兜底）。模块 `ming_sim/memorials.py`，API `/api/desk`。
- **崇祯陷阱（S5，核心循环）**：全局 `risk_aversion`（任事风险先验）——严惩失败者↑（公开问罪+8~15、廷杖+6、严谴诏书+3、驳告变+2）→ 请旨奏疏量×(1+RA/100)、主动奏报×(1-RA/150)、执行推诿↑；破局靠反直觉的「为忠臣买单」：公开担责/抚恤/败后复用（RA-5~10 但势-或花钱）。势与任事意愿同屏（`/api/beliefs` 趋势线 + 御案 trap_hint）。
- **朝堂剧场（S6）**：全局「势」=百官服从预期（诏令落地+1、抗命未罚-、朝令夕改-4、弹章留中-2）；信号类指令廷杖/罪己诏/献俘（只改信念不改钱粮）；势>70 解锁「乾纲独断」（绕部议强推但 RA+3——独断与寒心连动）；密旨问罪有 35% 泄露风险（泄露按公开结算）。模块 `ming_sim/theater.py`。
- **派系主动 AI（S7）**：派系 `heat`（同党被惩/弹章被驳累积，朔日衰减），逢 5/15/25 日按 heat 概率出招（全局每旬≤2）：弹章（批红表态本身是落子：准则两派 heat 连锁）、联名上书、荐人、暗中掣肘（给在办旨意叠阻力，来源不点破）；官员主动奏报概率随 RA 萎缩。
- **文书化黑箱（S3/S9）**：官员属性 API 不出真值，改「印象评语+置信度」（噪声按人定种、随召对熟悉度与密查收敛——初见走眼、久识方真）；明军兵额账实分离（armies.manpower=实在兵驱动推演，玩家看兵部账面虚冒值，京营 1.65×）；密查两线：厂卫（6-12日、准、累积「厂卫横行」issue、可能被反侦喂假）/科道（18-32日、带派系滤镜）；产出密揭入御案；文书互证面板 `/api/veil/contradictions`（只给疑点不给真值——审计即侦探）。模块 `ming_sim/veil.py`。
- **信息集隔离（S8 架构铁律）**：每个大臣 agent 注入「信息边界」块——只知本衙门账面、本派系内情、公开邸报与风闻，不得开天眼；自家册里的虚冒心知肚明但绝不主动吐实。欺骗与信息差由此涌现。另注入双向信任块（对皇帝的 trust 与 grievance 支配其条件苛严度），官员失诺自动生成「问罪抓手」（`/api/court/leverage`）。
- **史笔系统（S11）**：终局由明史馆史官按实际作为立传（《明史·本纪》体，「上多疑而好杀」vs「知人善任」笔调由 势/任事轨迹+章节记忆定）；每季度起居注「史臣曰」中途预览（`/api/shibi`）。模块 `ming_sim/shibi.py`。
- **节奏层（S12）**：中兴指数（财政/边备/流寇压制/吏治/民心五分项月刷，留 240 月折线）；阶段诏题四章（站稳脚跟→己巳之劫→双线之困→中兴之望，`content/stage_goals.json`，兼软教程）；结局光谱（中兴在望/划江守成/苟延残喘/功败垂成/乾纲独断的代价/回天乏术）替代二值结局，喂史笔定基调。模块 `ming_sim/zhongxing.py`，API `/api/zhongxing`。
- **前端半即时层**（`web/src/upgrade.tsx`）：顶部时钟条（日历进度、+1日/+5日/至有事、势/任事/精力仪表、事件流）；右栏新增「御案·批红」「司天台·朝局观测」两抽屉（预警仪表、中兴指数与诏题、在办旨意进度条与五式干预、文书互证与两线密查、史臣曰）。
- **测试**：新增 `tests/test_timeflow.py`、`test_lifecycle.py`、`test_memorials.py`、`test_veil.py`、`test_theater.py`、`test_zhongxing.py`、`test_upgrade_api.py` 共 46 个零 LLM 测试；前端 tsc + vite build 通过。
- **存档兼容**：旧档自动迁移（current_day=按 turn 反推；schema 全部增量 ALTER+默认值；时间引擎未激活时财政走旧整月路径）。

### 新增
- **事件记忆系统**：每回合结算后自动提炼记忆卡，按人物/派系/官职类型建索引；大臣召见时注入「旧事记忆」块，上限5条，对话前后贯通。支持规则提取（`record_event_memories_from_resolution`）与 LLM 提取（`memory_extractor` agent）两条路径；每科目保留最近3条，超出自动剪枝。
- **推演记忆注入**：结算链新增 step 1.8——`memory_retrieval` agent 从本月诏书提取人名/地区/军队/势力/关键词（含可选 year/period），按 tags LIKE 匹配召回相关历史记忆（≤10条），注入 `season_simulator` 与 `score_extractor` payload；两个 prompt 同步说明字段含义与使用方式。
- **记忆自动衰减**：写入时按 importance 设 `expires_turn` TTL（importance 1→6回合、2→12、3→24、4→48、5→永久）；查询默认过滤过期记录，按年月查时可 `ignore_expiry=True` 追溯历史档案。
- **大臣按时间回忆**：新增 tool `recall_memories_by_time(year, period, keywords)`——时间查（精确该月，ignore_expiry）与关键词查（当前有效期内）合并去重返回；`memory-recall` skill 说明同步更新。
- **DB 索引**：`event_memories` 新增 `idx_event_memories_expiry(expires_turn, turn)` 加速过期过滤；`get_memories_by_keywords` 支持 `ignore_expiry` 参数。
- 后宫妃嫔卡片支持上传本机图片作专属立绘，存 `data/uploads/`，记入 `portrait_id`，重启后自动复用（`POST/DELETE/GET /api/consorts/{name}/portrait`）。
- 立绘工具脚本：`gen_portraits.py`（调生图接口出图）、`compress_portraits.py`（缩 512 压体积）、`portrait_status.py`（进度表）；附后宫预设图池与寝宫背景图。

### 变更
- **推演 agent（season_simulator）改 skill+tool 模式**：不再把全量盘面静态塞入 payload；挂 10 个只读工具（`view_state`/`check_treasury`/`list_regions`/`inspect_region`/`list_armies`/`inspect_army`/`list_issues`/`inspect_issue`/`list_external_powers` + `submit_report`），按需查盘面，写完邸报调 `submit_report` 提交正文；`submit_report` docstring 承载完整奏章写作规范（结构/笔法/局势/末章/禁忌），`season_simulator.md` 从 141 行精简至 54 行。
- **结算 agent（score_extractor）改 skill+tool 模式**：payload 去掉 regions/armies/buildings/ministers 五张全表，只保留 narrative + issues摘要 + id列表 + fiscal_config；挂 7 个工具（`get_region`/`get_army`/`get_external_power`/`get_active_ministers`/`get_issue_detail`/`get_faction_class_state` + `submit_extraction`），按章节按需查当前值算 delta；`submit_extraction` docstring 承载完整 JSON schema、16 字段约束、档位标准与骨架示例，`score_extractor.md` 从 266 行精简至 50 行；去掉 `force_json_output`，改由 tool docstring 约束格式。

## [2026-05-24]

### 新增
- 后宫系统：打通选妃流程，司礼监从秀女池遴选候选呈选、降诏册封入宫；调教 tool 提权，妃嫔学技艺/改性子写入永久记忆；修复 candidate 升格。
- 人物据实奏对：大臣与月末邸报按在朝名册查现职状态，不再凭史实记忆乱报官职；朝堂名册按官品排序。

### 修复
- 财政：`economy_moves` 的 account 按钱实际出自哪库判定，不再按用途误判。

### 文档
- README 重写「已实现」为分模块表格；补后宫、省级财政、月度收支、人物头像等说明。
- 立绘提示词改现代古风；新增 GPL-3.0 许可证。

## [2026-05-23]

### 新增
- extractor：支持人事任命与人物状态变更落库；开局校准到 1627.10。
- 网页结算悬浮框加「本月一次性入账」段；建筑支出改走内库。

### 变更
- 推演重构：叙事零数值化，extractor 按章节扫描，prompt 瘦身。

## [2026-05-22]

### 新增
- 建筑系统：御窑厂/边堡/仓储/工坊/河工，等级状态维护产出按月落账，新建须立项推进；推演 token 优化与遥测。
- 网页地图节点重定位与取点工具；菜单改中央弹窗。

### 文档
- README 加游戏截图与头图。

### 杂项
- 移除 `.vscode` 出版本管理。

## [2026-05-22] — 首次公开发布

晚明对话式政略模拟器初版：月度回合制、大臣召见与拟旨、诏令结算、月末邸报、两京十三省与军队/外部势力盘面、CLI 与网页双端、本地存档、内容外置。
