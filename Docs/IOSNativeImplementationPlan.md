# 《崇祯模拟器》iOS 原生技术方案与执行路线

本文档把总设计文档中的机制拆成 iOS 原生工程方案。目标是形成可执行、可衡量、可验收的开发路线，而不是停留在概念层。

## 当前工程基线

当前项目状态：

```text
平台：iOS 17+
语言：Swift
UI：SwiftUI
设备：iPhone 竖屏
当前入口：TiangangRootView
当前玩法状态：群星谱 / NPC 档案查看器
运行时数据：Bundle JSON 种子
LLM 路由：DeepSeek 文字 + Google Gemini 图像，已在 Services 层搭好基础接口
```

当前已有资产：

- NPCDatabase：263 名 NPC 的人物基建。
- EnvironmentDatabase：两京十三省、203 个府级节点和机构/官职/品秩数据。
- Tiangang seed：36 维天罡坐标字典。
- 字体：`LXGWWenKai-Medium.ttf`。
- 现有视图：群星谱、人物画像、天罡、命数、心盘、势网页面。

第一版开发不重写这些基建，而是在其上增加游戏运行层。

## Ming 机制的选择性吸收

旧 Ming 原型的价值在于机制验证，不在于代码、资源或数据库结构。iOS 版只吸收能服务 v1 财政纵切的机制。

| 旧 Ming 机制 | iOS v1 处理 | 落地方式 |
|---|---|---|
| 月度回合 | 吸收 | 固定为“月初奏报 → 召对 → 一道旨意 → 月末结算 → 因果黑板”。 |
| 奏对目的 | 吸收 | 用 `AudienceGoal` 表示本轮政治目标，每名官员同一时间最多 1 个 active goal。 |
| 心理握手 | 吸收并简化 | 保留 `none / conditional / sealed / blocked`，先不用复杂多轮心理量表 UI。 |
| 承诺账本 | 吸收 | 用 `AgreementLedgerItem` 记录承诺、条件、履约、失信。 |
| 条件审计 | 吸收并规则优先 | 先用规则检查拨银、授权、人手、期限；LLM 只做辅助解释。 |
| 执行预评估 | 吸收并简化 | 评分只取 5 个 v1 driver：财政、承办人、心盘、派系、承诺背书。 |
| 政治因果黑板 | 吸收 | 月末用 `CausalityNote` 汇总已发生事实，不二次模拟。 |
| 记忆卡 | 吸收 v1 子集 | 只记录奏对、旨意结果、承诺、失信、财政事件。 |
| 密令 | 暂缓 | v1 不做主动密令系统，只保留 action kind 和未来接口。 |
| 动态立绘 DNA | 吸收概念，v1.5 实现 | v1 只保留 `PortraitDNA` / `PortraitSignature` 结构设计。 |
| SQLite 表设计 | 不吸收 | iOS v1 用 Codable JSON 存档，不照搬数据库表。 |
| Agno agent 架构 | 不吸收 | iOS 用现有 LLM service + Swift 合同类型。 |
| 全量军事/后宫/建筑/外交 | 不吸收进 v1 | 仅作为未来模块，不抢财政纵切资源。 |
| 旧图片、旧 prompt、旧资源路径 | 不吸收 | 禁止搬入 iOS 仓库。 |

这一取舍的核心判断：v1 先做“政治信用链路”，不做“全量明末模拟器”。

## 原生 iOS 技术栈

### UI 与交互

采用 SwiftUI 作为主 UI。游戏是文字、人物、奏报、决策和账本驱动的政治模拟，不需要第一版引入 SpriteKit 或 SceneKit。

第一版界面结构：

```text
GameRootView
├─ 朝局：月初奏报、财政摘要、当前危机
├─ 召对：选择官员、君臣对话、握手状态
├─ 旨意：草案、风险预评估、确认发布
└─ 群星谱：NPC 档案、天罡、命数、心盘、势网
```

iOS 原生要求：

- 竖屏优先，所有关键操作在 390pt 宽度可读可点。
- 单次操作链控制在 3 层 NavigationStack 以内。
- 召对、旨意确认、月末结算使用 sheet 或全屏流程，避免玩家迷路。
- LLM 等待必须有明确 loading、cancel、error 状态。
- 游戏核心流程每一步都能保存，适配 iPhone 短时游玩和随时中断。
- 重要结果用轻量 haptic 反馈，但不依赖震动表达信息。

### 状态管理

最低系统为 iOS 17，因此优先使用 Observation。

建议状态结构：

```text
@Observable GameRuntimeStore
├─ currentSession: GameSessionState
├─ currentMonth: GameMonth
├─ fiscalState: FiscalRuntimeState
├─ audienceState: AudienceRuntimeState
├─ decreeState: DecreeRuntimeState
├─ memoryState: MemoryRuntimeState
└─ services: GameServiceContainer
```

原则：

- UI 局部展开、筛选、选中项用 `@State`。
- 根运行时状态由 `@Observable` store 持有。
- LLM、存档、图像生成等共享能力通过服务容器注入。
- 业务状态尽量使用 `Codable` value struct，降低存档和测试成本。
- 不引入全局单例；预览和测试要能注入 mock。

### 存档

v1 采用 Codable JSON 存档，写入 Application Support。

暂不使用 SwiftData 作为第一版核心存档，原因：

- 当前运行时结构还在探索，JSON 更利于快速调整 schema。
- 方便把存档导出给 LLM、调试脚本和人工审计。
- 263 名 NPC 与 219 个行政节点规模很小，JSON 足够承载 v1。

存档策略：

```text
ScenarioSeed：Bundle 内静态数据，不随存档修改。
GameSave：玩家运行时状态，按存档槽保存。
RuntimeOverlay：人物任免、心盘、承诺账本、记忆、财政变化。
```

量化要求：

- 新建存档到进入朝局页：目标 1 秒内。
- 单次自动保存：目标 300ms 内完成。
- 任一月末结算完成后必须自动保存。
- 存档文件需带 `schemaVersion`，从 v1 开始。

### LLM Harness

现有 `TextGenerationServicing` 和 `ImageGenerationServicing` 继续保留。第一版新增严格合同层，不让 UI 直接消费裸 LLM 文本。

建议新增合同：

```text
MinisterReplyContract        NPC 奏对回复
AudienceAuditContract        奏对目的与握手审计
DecreeDraftContract          旨意草案生成
SettlementNarrativeContract  月末结算叙事
PortraitPromptContract       立绘 / 插画 prompt
```

原则：

- LLM 可以生成文本，但不能直接修改真实数值。
- LLM 结构化输出必须 decode 成 Swift 类型；失败则不落档。
- 所有 LLM 请求带 `requestID`、用途、模型、时间戳和脱敏摘要。
- API Key 只来自环境变量或生产后端，永不写入仓库和存档。
- 网络失败时显示“本次奏对未落档”或“图像生成失败”，不伪造结果。

量化要求：

- 每个合同至少 3 个 fixture 测试：成功、缺字段、非法枚举。
- 每次游戏状态变更必须能追溯到规则事件或已验证合同。
- LLM 超时默认 45 秒；图像生成默认 120 秒；用户可取消。

### 图像与动态立绘

v1 只保留接口和静态画像使用；v1.5 再生产化动态立绘。

动态立绘技术路线：

```text
PortraitDNA：人物稳定身份种子
PortraitSignature：人物 + 官职 + 身份 + 服制 + 年龄状态
PortraitAsset：生成结果、状态、错误、图片数据
PortraitCache：Application Support 或 Caches
```

量化要求：

- 首批支持 6 名核心 NPC 的静态显示。
- v1.5 支持至少 3 种身份变化触发重绘：升官、入内廷、脱籍/出仕。
- 生成失败不影响月末结算。
- 图像缓存单存档目标控制在 50MB 内。

## v1 运行时模型草案

第一版运行时模型全部 `Codable`。这些类型先作为施工蓝图，真正实现时可按 Swift 文件拆分。

```swift
enum GamePhase: String, Codable {
    case monthBriefing
    case audience
    case decreeReview
    case settlementReport
}

struct GameSave: Codable {
    var schemaVersion: Int
    var saveID: UUID
    var createdAt: Date
    var updatedAt: Date
    var month: GameMonth
    var phase: GamePhase
    var fiscal: FiscalRuntimeState
    var npcRuntime: [String: RuntimeNPCState]
    var regionRuntime: [String: RuntimeRegionState]
    var audienceGoals: [AudienceGoal]
    var agreements: [AgreementLedgerItem]
    var memories: [MemoryCard]
    var causalityNotes: [CausalityNote]
    var lastDecree: ImperialDecree?
}
```

核心枚举先压到最小：

```swift
enum AudienceActionKind: String, Codable {
    case fiscalPolicy
    case personnel
    case secretOrder
    case courtCommitment
    case general
}

enum HandshakeStatus: String, Codable {
    case none
    case conditional
    case sealed
    case blocked
}

enum AgreementTargetStatus: String, Codable {
    case pendingConditions
    case achieved
    case failed
    case blocked
}
```

第一版不实现净身、脱籍等高风险身份转换握手。旧 Ming 中这些机制很有价值，但会把 v1 拉向内廷/后宫/身份系统，先暂缓。

## v1 Alpha 初始数值

以下数值是 alpha 调参口径，不声称是精确史实。单位用于游戏内部一致性。

```text
银两单位：万两
粮储单位：万石
评分单位：0-100
```

开局财政：

| 字段 | 初始值 | 用途 |
|---|---:|---|
| 国库银 | 80 | 外朝可调度资金。 |
| 内库银 | 30 | 皇帝私人可调度资金。 |
| 粮储 | 180 | 赈灾、军需和民心缓冲。 |
| 月收入 | 22 | 常规税粮折银收入。 |
| 月固定支出 | 36 | 官俸、行政、边饷常额。 |
| 辽饷压力 | 75 | 越高越容易触发边镇军费问题。 |
| 陕西赈灾需求 | 65 | 越高越容易触发流民与民变。 |
| 亏空风险 | 70 | 越高越容易吞噬拨款。 |
| 民心 | 42 | 低于 35 进入明显危险区。 |
| 皇威 | 52 | 影响强旨成本和官员服从表象。 |
| 奏疏拥堵 | 48 | 影响事件发现和延迟。 |

第一版财政月流：

```text
月初收入：国库银 += 月收入
固定支出：国库银 -= 月固定支出
若国库银 < 0：亏空风险 +8，皇威 -2，奏疏拥堵 +4
若粮储 < 100：民心 -2，陕西赈灾需求 +5
若辽饷压力 > 70 且未拨款：边防压力事件提示增强
```

## v1 第一批政策动作

先实现 5 种财政相关动作，避免自然语言一开始就覆盖所有政治行为。

| 动作 | 输入字段 | 成本 | 正面效果 | 风险 |
|---|---|---:|---|---|
| 拨辽饷 | 银两、承办人 | 国库或内库银 | 辽饷压力下降 | 钱不足则皇威下降、边臣不满。 |
| 陕西赈灾 | 银两、粮储、承办人 | 银 + 粮 | 民心上升、赈灾需求下降 | 亏空风险高时被截留。 |
| 清查亏空 | 承办人、目标衙门 | 皇威或派系成本 | 亏空风险下降，可能追回银 | 阉党/地方阻力上升。 |
| 加派/加税 | 地区或全国、幅度 | 民心成本 | 月收入上升 | 民心下降、灾区反噬。 |
| 动用内库 | 银两、用途 | 内库银 | 快速补外朝资金 | 内廷势力或皇帝私库压力上升。 |

每个动作必须输出：

```text
RuleDelta[]
ExecutionAssessment
CausalityNote[]
MemoryCard[]
```

## 规则优先的握手算法

Ming 的握手机制很强，但 iOS v1 不需要一开始就复刻完整心理量表。先做一个规则优先、LLM 辅助的两阶段算法。

### 阶段一：目的识别

输入：

```text
玩家发言
当前 NPC
当前财政奏报
当前 active AudienceGoal
最近 3 条相关记忆
```

输出：

```text
AudienceGoal?
AudienceActionKind
confidence 0-100
```

规则兜底：

- 含“拨、饷、赈、银、粮、清丈、亏空、加派、内库” → `fiscalPolicy`
- 含“任、擢、罢、调、尚书、侍郎、承办” → `personnel`
- 含“密查、暗查、取证、厂卫、锦衣卫” → `secretOrder`
- 含“劝、背书、调停、保密、代奏” → `courtCommitment`
- 无明确要求 → `general`

### 阶段二：握手裁判

输入：

```text
NPC 回复
NPC 初始能力与派系
RuntimeNPCState 心盘
AudienceGoal
LLM 审计结果（可选）
```

先用规则判断硬条件：

- 明确拒绝 → `blocked`
- 提出可验证条件 → `conditional`
- 明确承担且无未决条件 → 可进入 sealed 候选
- 只有恭敬、畏惧或含糊服从 → `none`

然后计算简化分：

```text
base = 50
+ 道合影响：-10...+10
+ 势合影响：-15...+15
+ 信任影响：-10...+10
- 仇恨影响：0...20
+ 承办适配：-10...+10
+ LLM 审计置信：-5...+5
```

阈值：

```text
财政政策：66
人事任用：72
密查取证：72
通用承诺：68
```

只有分数过线且有明确承诺，才能 `sealed`。畏惧不加真承诺分，只影响公开反抗成本。

## 承诺账本落地结构

`AgreementLedgerItem` 先不要做复杂数据库表，直接进入 `GameSave.agreements`。

```swift
struct AgreementLedgerItem: Codable, Identifiable {
    var id: UUID
    var createdMonth: GameMonth
    var npcID: String
    var actionKind: AudienceActionKind
    var coreTopic: String
    var targetText: String
    var handshakeStatus: HandshakeStatus
    var conditionStatus: AgreementConditionStatus
    var targetStatus: AgreementTargetStatus
    var score: Int
    var threshold: Int
    var dueMonth: GameMonth?
    var conditions: [AgreementCondition]
    var evidence: [EvidenceRef]
    var politicalEffectApplied: Bool
}
```

v1 状态转换：

```text
conditional + 条件未满足 → pendingConditions
conditional + 条件满足 → achieved
sealed + 无条件 → achieved
blocked → blocked
到期仍 pendingConditions → failed
```

兑现效果：

```text
achieved：NPC 势合 +5，信任 +0.04，相关执行阻力 -8
failed：NPC 势合 -8，仇恨 +5，信任 -0.10，皇威 -1
blocked：相关执行阻力 +10，强推时额外写入因果黑板
```

## 执行预评估公式

v1 使用确定性公式，不让 LLM 决定成功率。

```text
score =
  fiscalSupport * 0.30
+ actorFit * 0.25
+ xinpanSupport * 0.20
+ agreementBacking * 0.15
+ factionClimate * 0.10
```

字段来源：

| Driver | 来源 | 计算口径 |
|---|---|---|
| fiscalSupport | `FiscalRuntimeState` | 钱粮够则 70-90，不够则 20-50。 |
| actorFit | NPCDatabase + 任官状态 | 财政能力、官职适配、是否在任。 |
| xinpanSupport | `RuntimeNPCState` | 道合、势合、信任、仇恨。 |
| agreementBacking | 承诺账本 | achieved=90，pending=40，failed=20，none=50。 |
| factionClimate | 派系状态 | 满意与影响力简化加权。 |

风险标签：

```text
money_shortage
actor_mismatch
low_trust
high_hatred
pending_conditions
faction_resistance
local_pressure
```

分数解释：

```text
80-100：顺行，仍可能有小折损
60-79：可行，但需要承担政治成本
40-59：高风险，结果大概率变形
0-39：强推，必须写入明显反噬或失败因果
```

## 第一批 Swift 文件落点

建议从这些文件开始，先搭运行时骨架：

```text
Models/Runtime/GameRuntimeModels.swift
Models/Runtime/FiscalRuntimeModels.swift
Models/Runtime/AudienceRuntimeModels.swift
Models/Runtime/AgreementModels.swift
Models/Runtime/SettlementModels.swift
Models/Runtime/MemoryModels.swift
Services/GameRuntimeStore.swift
Services/SaveGameService.swift
Services/FiscalRuleEngine.swift
Services/AudienceRuleEngine.swift
Services/SettlementEngine.swift
Services/LLMContractValidator.swift
Views/GameRootView.swift
Views/Court/CourtOverviewView.swift
Views/Audience/AudienceView.swift
Views/Decree/DecreeReviewView.swift
Views/Settlement/SettlementReportView.swift
```

第一批不要拆太碎。等 P4 跑通之后，再把规则引擎细分。

## P1-P4 具体实施顺序

### Step 1：保留群星谱，接入 GameRootView

最小改动：

```text
ChongzhenSimulatorApp → GameRootView
GameRootView 内嵌 TiangangRootView 作为“群星谱”页
新增朝局 / 召对 / 旨意空状态页
```

完成即验收：

- App 启动看到 4 个主入口。
- 群星谱功能不丢。
- 空状态页说明当前阶段与下一步。

### Step 2：新局与 JSON 存档

最小改动：

```text
GameRuntimeStore.createNewGame()
SaveGameService.save()
SaveGameService.loadLatest()
GameSave schemaVersion = 1
```

完成即验收：

- 新局创建崇祯元年正月。
- 自动保存到 Application Support。
- 重启恢复月份与财政状态。

### Step 3：财政奏报

最小改动：

```text
FiscalRuntimeState 使用 v1 alpha 初始数值
FiscalReport 由规则生成，不调 LLM
CourtOverviewView 展示 5 个关键指标
```

完成即验收：

- 朝局页展示国库银、内库银、粮储、辽饷压力、赈灾需求。
- 有一段固定模板财政奏报。
- 不配置 LLM 也可运行。

### Step 4：召对与规则握手

最小改动：

```text
AudienceView 选择 6 名核心 NPC
玩家输入一句话
先用规则分类 actionKind
mock NPC 回复或 LLM 回复都可进入 AudienceRuleEngine
生成 AudienceGoal / HandshakeStatus
```

完成即验收：

- 普通问策得到 `none`。
- 明确财政请求可得到 `conditional` 或 `sealed`。
- 明确拒绝样本得到 `blocked`。
- 附条件显示 1-3 条条件。

### Step 5：旨意草案与预评估

最小改动：

```text
从 AudienceGoal 生成 ImperialDirectiveDraft
玩家选择承办人和资源
ExecutionAssessment 给 0-100 分和 driver
```

完成即验收：

- 有草案正文。
- 有资源成本。
- 有 3 条风险 driver。
- 可确认或放弃。

### Step 6：月末结算与因果黑板

最小改动：

```text
固定财政流
应用一道旨意
更新财政状态
更新一个 NPC 心盘
复查一个承诺
生成 3 条 CausalityNote
月份 +1
自动保存
```

完成即验收：

- 正月能推进到二月。
- 财政状态变化可见。
- 承诺账本有 achieved / pendingConditions / failed 至少一种变化。
- 因果黑板能解释变化来源。

## 模块拆分

### M1 App Shell 与导航

目标：从单一群星谱入口升级为游戏入口。

交付物：

- `GameRootView`
- `GameTab`
- `GameRoute`
- `GameSheet`
- `GameRuntimeStore`

验收标准：

- App 启动后进入游戏主界面，而不是只进入群星谱。
- 至少 4 个主入口：朝局、召对、旨意、群星谱。
- 群星谱作为既有功能完整保留。
- 所有主入口在 iPhone 竖屏下无文字重叠。

### M2 数据接入与运行时状态

目标：把现有静态 JSON 种子接入第一版游戏状态。

交付物：

- `ScenarioSeed`
- `GameSessionState`
- `GameMonth`
- `RuntimeNPCState`
- `RuntimeRegionState`
- `GameSave`
- `SaveGameService`

初始量化口径：

```text
开局年份：1628
初始月份：正月
NPC 可见核心样本：至少 6 人
一级行政区：15 个
府级节点：203 个
第一版活跃财政变量：8-12 个
```

验收标准：

- 启动新局后可从 Bundle seed 构建运行时状态。
- 保存、退出、重进后月份、财政、承诺、记忆不丢失。
- 存档 JSON 不包含 API Key。

### M3 财政危机纵切

目标：先做一个能跑通的国家治理压力源。

第一版财政变量：

```text
国库银
内库银
粮储
月收入
月支出
辽饷压力
赈灾需求
官俸与行政支出
亏空风险
民心
皇威
奏疏拥堵
```

交付物：

- `FiscalRuntimeState`
- `FiscalReport`
- `FiscalPolicyDraft`
- `FiscalSettlementRule`
- `MonthlyReport`

验收标准：

- 月初能生成一份财政奏报。
- 玩家发布一道财政旨意后，月末至少改变 3 类状态：钱粮、民心/皇威、官员心盘或派系。
- 每项变化都有规则来源或因果说明。
- 财政数字只由本地规则改动，不由 LLM 裸文本直接改动。

### M4 君臣奏对与握手

目标：把自然语言对话变成可追踪的政治目的。

交付物：

- `AudienceSession`
- `AudienceGoal`
- `HandshakeStatus`
- `MinisterReply`
- `AudienceAuditResult`
- `AgreementCondition`

第一版握手状态：

```text
none
conditional
sealed
blocked
```

验收标准：

- 玩家可以召见至少 6 名核心 NPC。
- 每次奏对最多推进 1 个主目的，避免状态失控。
- 系统能区分普通问策和真实政治要求。
- “臣谨听”“容臣斟酌”“不敢不从”不会被自动判为 `sealed`。
- 附条件必须显示 1-3 条可审计条件。

### M5 旨意草案与执行预评估

目标：让玩家说出的话变成可确认、可结算、可追责的一道旨意。

交付物：

- `ImperialDirectiveDraft`
- `ImperialDecree`
- `ExecutionAssessment`
- `ExecutionRisk`
- `DecreeReviewView`

执行预评估维度：

```text
官署完整度
承办人适配
承办人心盘
派系阻力
财政硬盘面
承诺账本背书
地方压力
```

验收标准：

- 每月默认只发布 1 道核心旨意。
- 发布前展示风险评分，范围 0-100。
- 至少 3 条风险 driver 可见。
- 玩家可以确认、修改或放弃草案。
- 强推高风险旨意要进入因果黑板。

### M6 月末结算

目标：让一个月真正过去，并把政治后果落成状态。

交付物：

- `MonthSettlementEngine`
- `SettlementInput`
- `SettlementResult`
- `RuleDelta`
- `MonthlyGazette`

结算顺序：

```text
固定财政流
  ↓
旨意成本检查
  ↓
执行预评估
  ↓
财政与环境状态变化
  ↓
NPC 心盘变化
  ↓
承诺条件复查
  ↓
记忆卡与因果黑板
  ↓
自动保存
```

验收标准：

- 点“颁旨并结算”后月份前进 1。
- 结算结果至少包含：财政变化、NPC 变化、承诺变化、因果说明。
- 无 LLM 时也能用规则跑出可测试的最小结果。
- LLM 叙事失败不回滚规则结算。

### M7 承诺账本与失信

目标：让政治信用成为可计算资产。

交付物：

- `AgreementLedgerItem`
- `AgreementStatus`
- `AgreementTargetStatus`
- `AgreementReviewService`
- `AgreementLedgerView`

状态口径：

```text
conditionStatus：none / pending / satisfied / failed
targetStatus：pending_conditions / achieved / failed / blocked
```

验收标准：

- `achieved` 承诺能降低后续相关旨意阻力。
- `pending_conditions` 不能作为执行背书。
- `failed` 会降低信任、增加仇恨或伤害皇威。
- 承诺账本至少显示最近 20 条。

### M8 记忆与政治因果黑板

目标：让玩家看见长期因果，而不是只看一次性月报。

交付物：

- `MemoryCard`
- `MemoryImportance`
- `MemoryRetrievalService`
- `CausalityNote`
- `CausalityBoardView`

记忆类型：

```text
private_audience
decree_result
promise
breach
appointment
punishment
intel_report
fiscal_event
```

验收标准：

- 每月最多生成 3-8 张新记忆卡。
- 重要度 5 永久保留；低重要度可随月份衰减。
- 月末因果黑板至少展示 3 条原因。
- NPC 回复只注入相关记忆，避免无限上下文膨胀。

### M9 LLM 合同与调试工具

目标：让 LLM 成为可控组件，而不是不透明魔法。

交付物：

- `LLMContract`
- `LLMContractValidator`
- `LLMRequestLog`
- `LLMDebugPanel`
- fixture 测试样本

验收标准：

- 所有结构化 LLM 输出都有 Swift Decodable 类型。
- 非法 JSON、缺字段、非法枚举必须失败，不落档。
- Debug build 可查看最近 20 次 LLM 请求摘要。
- 日志不显示 API Key。

### M10 原生体验与性能

目标：让它像 iOS 游戏，而不是网页搬家。

交付物：

- 竖屏主流程。
- 可中断自动保存。
- 明确的 loading/error/cancel 状态。
- VoiceOver 基础标签。
- 可读中文字号与长文本折行。

量化要求：

- 390pt 宽度下无主要按钮文字溢出。
- 主界面首屏加载目标 1 秒内。
- 群星谱滚动保持稳定，避免因状态变化全量重绘。
- 单次月末结算本地规则目标 500ms 内。
- LLM 请求全部异步，不阻塞主线程。

## 里程碑路线

### P0 文档与架构锁定

目标：完成设计总纲、技术方案、模块边界。

完成标准：

- `Docs/GameDesign.md` 明确游戏机制。
- 本文档明确 iOS 技术栈和可执行路线。
- README 链接到两份文档。

### P1 原生游戏壳

目标：App 从查看器变成可进入游戏流程的壳。

完成标准：

- `GameRootView` 可切换朝局、召对、旨意、群星谱。
- 新局状态可创建、保存、读取。
- 群星谱保持可用。

### P2 财政月报

目标：第一个月初能给玩家压力。

完成标准：

- 1628 正月开局。
- 朝局页展示财政奏报。
- 财政变量可视化，不依赖 LLM。

### P3 奏对握手

目标：玩家可以召见官员，并得到可审计的握手结果。

完成标准：

- 6 名核心 NPC 可召见。
- 至少 4 类握手状态能出现。
- 附条件能进入待履约列表。

### P4 一道旨意与月末结算

目标：跑通第一条完整游戏链路。

完成标准：

- 奏对后生成旨意草案。
- 玩家确认后月末结算。
- 结算改变财政、NPC 心盘、承诺账本和记忆。

### P5 因果黑板与存档稳定

目标：玩家看得懂结果，并且局面能持续。

完成标准：

- 月末结果有因果黑板。
- 连续 6 个月流程无崩溃。
- 保存/读取后状态一致。

### P6 动态立绘 v1.5

目标：角色视觉状态开始跟随政治身份变化。

完成标准：

- `PortraitDNA` 与 `PortraitAsset` 进入存档结构。
- 至少 3 种身份变化可触发新签名。
- 生成失败不影响主流程。

## 第一批核心 NPC

第一版先围绕已有画像和高频政治功能选择 6 人：

```text
韩爌：内阁 / 清流缓冲
毕自严：户部 / 财政主轴
崔呈秀：阉党 / 风险与派系压力
曹化淳：内廷 / 私人执行链
袁崇焕：边防 / 军费压力外显
祖大寿：边镇 / 军饷与将领态度
```

财政纵切中，优先召见韩爌、毕自严、曹化淳。袁崇焕和祖大寿先作为辽饷压力来源，后续再进入军事模块。

## 验收总标准

当 v1 纵切完成时，玩家应能做到：

1. 新建崇祯元年正月存档。
2. 阅读月初财政奏报。
3. 召见一名关键官员并进行 1-3 轮奏对。
4. 得到 `none / conditional / sealed / blocked` 中的一种握手结果。
5. 将奏对结果转成一道旨意草案。
6. 查看执行预评估和风险 driver。
7. 确认发布旨意并进入月末结算。
8. 看到财政、民心/皇威、NPC 心盘、承诺账本的变化。
9. 看到至少 3 条因果黑板说明。
10. 保存后重启，状态仍一致。

## 测试与质量门槛

每个里程碑至少跑：

```text
数据审计：Tools/audit_npc_database.py、Tools/audit_environment_database.py
Xcode build：iOS Simulator Debug build
单元测试：运行时规则、合同解析、存档 round-trip
UI 冒烟：进入朝局、召对、旨意、群星谱四个主入口
敏感信息检查：文档、日志、存档不包含 API Key
```

第一批测试目标：

- `GameSave` round-trip：至少 5 个场景。
- 财政结算规则：至少 8 个单元测试。
- 握手合同解析：至少 12 个 fixture。
- 承诺账本状态转换：至少 8 个单元测试。
- 月末结算连续 6 个月压力测试：至少 1 个集成测试。

## 暂缓事项

以下内容进入 v1 之后，不抢第一版纵切资源：

- 完整军事战斗系统。
- 外交与外部势力全量模拟。
- 后宫完整玩法。
- 全地图交互。
- 县级精确行政管理。
- 实时动态立绘生产化。
- SwiftData 替换 JSON 存档。
- SpriteKit / SceneKit 动画场景。
