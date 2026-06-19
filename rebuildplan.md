# 以国策与财政为核心的重构方案

## Summary

本次重构把当前多模块并行、互相解释国策与财政的结构，收敛为“国策中枢 + 可解释财政 + 奏对/御案/诏旨/履约闭环”。核心目标不是推倒旧存档，而是复用现有 `issues`、`legacies`、`turn_directives`、`memorials`、`negotiation_agreements`，让玩家在一个中枢里看见国策、疆域、财政、军队如何互相约束。

财政改为玩家可见的一等系统：每月固定税源、税基、到账率、支出、欠饷都必须显式展示，并能追溯到省份、军队与账簿流水。

补充重构：经济系统与官僚组织统一归入“国家机器”层，参考《钢铁之心》的顶栏资源、月度流量、产能面板和瓶颈警报，但不照搬现代工业国模型。新增 `StatecraftCenter` 只聚合 `FiscalCenter` 与 `organization_diagnostics()`，把国库/内库、税源、支出、建筑、官僚产能、空缺和欠饷放到同一个玩家读法里。

## 核心原则

1. `content/policy_doctrines.json` 仍是国策静态定义唯一来源。
2. `issues(origin_kind='doctrine')` 表示未成正统的路线争议。
3. `legacies(legacy_key='doctrine:*')` 表示已成正统的基本国策。
4. `flows.compute_budget_lines()` 是财政预算唯一计算入口。
5. 所有一次性财政动作必须进入 `economy_ledger`。
6. 移动端主导航将“天下”改为“国策”，但舆图、军队、建筑仍作为国策约束明细保留。

## 国策中枢

新增 `ming_sim/policy_center.py`，提供 `policy_center_payload(db, state, fiscal=...)`。它不新建完整国策状态表，而是聚合现有 runtime 数据：

- `routes`：全部国策路线，按 `orthodox / contested / latent` 分类。
- `orthodox`：已成正统的基本国策。
- `contested`：正在御案、诏旨、奏对中争夺正统性的路线。
- `latent`：尚未进入正式争议的路线潜势。
- `strategic_snapshot.fiscal`：国库/内库月入、月支、月净、下月缺口。
- `strategic_snapshot.territory`：动乱省份、糜烂省份、平均民望、重点风险地区。
- `strategic_snapshot.army`：军饷总额、欠饷总额、欠饷军镇、平均士气。
- `workstreams.memorials`：相关奏疏。
- `workstreams.directives`：相关在办旨意。
- `workstreams.agreements`：相关履约承诺。
- `inner_court_tools`：净身/宦官线的高风险工具约束。

API：

```http
GET /api/policy_center
```

模块边界：

| 模块 | 新定位 |
|------|--------|
| 国策 | 主中枢，解释路线、冲突、正统化进度与约束 |
| 疆域 | 国策约束面板，不再只是散落地图数字 |
| 财政 | 国策约束面板，回答钱从哪里来、为什么少、下月缺多少 |
| 军队 | 国策约束面板，显示军饷、欠饷、士气和边防压力 |
| 奏对 | 问询、试探、生成承诺或草案，不直接改国策进度 |
| 御案/弹劾 | 把路线争议正式推入批红流程 |
| 诏旨 | 成命、传旨、承办、阻力、复命和后果 |
| 握手履约 | 承诺兑现，不再作为平行主循环 |
| 净身/宦官线 | 归入“内廷制衡外朝”路线的高风险工具链 |

## 财政中枢

新增 `ming_sim/fiscal_center.py`，提供 `fiscal_center_payload(db, state)`。财政中枢复用 `compute_budget_lines()` 与 `calc_province_fiscal()`，不另造预算口径。

API：

```http
GET /api/fiscal_center
```

返回字段：

- `revenue_sources`：收入来源，含账户、科目、月额、说明。
- `expense_sources`：支出来源，含账户、科目、月额、说明。
- `revenue_family_rows`：玩家可读税源拆账，把国库动态税拆成田赋、辽饷、盐税、商税，并列出内库固定收入。
- `expense_family_rows`：玩家可读支出分组，说明军饷、宗室、官俸、宫廷、恩赏等钱为什么会花出去。
- `province_tax_rows`：每省税源明细。
- `army_pay_rows`：各军月饷、欠饷、士气、自专。
- `net_by_account`：国库/内库余额、月入、月支、月净、经营缺口、下月现金缺口。
- `account_cards`：国库/内库账户摘要。
- `ledger_movements`：最近经济流水。
- `ledger_summary`：过滤期初账后的近期流水摘要，用于解释余额为什么变了。
- `policy_modifiers`：国策/遗产修正。
- `explainers`：玩家可读的短缺原因。
- `money_questions`：财政三问：怎么赚钱、钱花在哪、为什么余额变了。
- `player_model`：财政读账边界：月预算、余额流水、国库/内库、欠饷。

财政口径统一为“月度万两”：

| 账户 | 收入 |
|------|------|
| 国库 | 各省 `田赋 + 辽饷 + 盐税 + 商税` |
| 内库 | `皇庄 + 织造 + 矿税 + 建筑产出` |

| 账户 | 支出 |
|------|------|
| 国库 | 宗室禄米、百官俸禄、工部、赈灾备用、各军军饷、建筑维护 |
| 内库 | 宫廷开支、内廷俸禄、妃嫔供奉、恩赏赍予、内廷建筑维护 |

`calc_province_fiscal()` 必须返回每省税基、田赋、辽饷、盐税、商税、综合到账率、辽饷到账率、势系数、腐败、士绅阻力、动乱、民望、边防压力。

## 国家机器中枢

详见 [docs/statecraft-hoi-rework.md](docs/statecraft-hoi-rework.md)。新增 `ming_sim/statecraft_center.py` 与 `GET /api/statecraft_center`，作为经济和官僚组织的共同解释入口。

核心读法：

| 层级 | 内容 |
|------|------|
| 库存 | 国库、内库、欠饷、朝廷执行力 |
| 月流 | 国库税源、国库支出、内库收支、营造资产净贡献 |
| 产能 | 财政署理、军政后勤、营造军工、地方贯彻、铨选任官、票拟程序、厂卫监察、内廷传旨 |
| 生产线 | 在办旨意按命中的财政/军政/营造/地方/程序等产能排队 |
| 官僚泳道 | 每个产能领域显示当前压着几道旨意、是否空闲/运转/过载/堵塞 |
| 瓶颈 | 现金缺口、欠饷、部门断裂、建筑失修、机构执行风险 |

设计纪律：

- `StatecraftCenter` 不结算经济，不改官职，只解释。
- 经济事实来自 `FiscalCenter`。
- 官僚事实来自 `organization_diagnostics()`。
- 建筑只作为营造资产聚合，不单独发明工业点。
- 在办旨意来自 `turn_directives.lifecycle_status`，只做生产线映射，不改执行结果。
- 后续诏旨、建筑、财政改革、军务都应显示自己受哪个产能限制。

## 税制变更纪律

- 调辽饷、盐税、商税：同步修改 `regions.fiscal` 对应字段。
- 调田赋：修改 `regions.tax_per_turn` 的田赋残差。
- 皇庄、织造、矿税等固定项：走 `fiscal_config`。
- 一次性拨款、抄没、赈济、补饷：进入 `economy_ledger`。
- 月结、UI、财政中心、国策中心都通过 `compute_budget_lines()` 取预算。

## UI Plan

移动端底部导航：

- 原“天下”改为“国策”。
- 新 `PolicyCenterView` 复用旧舆图、军队、建筑明细，但把页面第一屏改成国策中枢。

`PolicyCenterView` 信息层级：

1. 现行基本国策、争议路线、路线冲突。
2. 财政总览：国库/内库月入、月支、月净、下月缺口。
3. 疆域与军队约束：动乱、税收效率、边防压力、欠饷。
4. 相关奏疏、在办旨意、履约承诺、密令证据。
5. 舆图、省份、军队、建筑、官制等下钻明细。

首页只保留国策摘要，不再重复解释财政和路线。

## 旨意周期与回报口径

详见 [docs/decree-cycle-and-reports.md](docs/decree-cycle-and-reports.md)。本轮梳理确认：当前玩家感觉“下达旨意要十几天”，根因是通用生命周期把 `lead_days + exec_days` 作为一条进度展示，而 `lead_days` 实际只是传旨/送达天数，`exec_days` 才是承办工期。下达本身应当是玩家确认后的瞬时成命。

新的硬边界：

| 阶段 | 语义 | 是否耗时 |
|------|------|----------|
| 成命 | 皇帝确认颁行，旨意立即入档 | 否 |
| 传旨 | 命令送到部院、地方、军镇、内廷承办处 | 可为 0 |
| 承办 | 真正办理、部议、调拨、执行、地方落地 | 可为 0 |
| 复命 | 正式结果回到御案 | 通常同完成日 |
| 复盘 | 皇帝召主办追问水分、责任、下一手 | 可选 |

因此 UI 和规则层不得再把“预计复命 X 日”写成“下达需 X 日”。长周期只属于传旨与承办，不属于成命。

即时旨意必须从通用生命周期中剥离：

- 强旨净身、发净军、宫刑、没入内廷。
- 赦出内廷、奴籍转民籍。
- 殿内收押、廷杖、革差。
- 京中近身任差、撤差。
- 内廷旧患调养、宝匣封存等档案动作。

这些动作已有部分即时路径，例如 `castrate_official()`、`/api/recruitment/castrate`、`INSTANT_AGREEMENT_ACTIONS`。后续实现要让普通旨意入口也先识别这类动作，命中后同日执行、同日/次日复命，而不是落入 `lead_days >= 1`、`exec_days >= 2` 的地方行政模型。

回报口径统一如下：

| 名称 | 归属 | 定义 |
|------|------|------|
| 复命 | 御案 | 正式结果通知，来自已办结旨意，落 `memorials(kind='复命')` |
| 复盘/追问 | 诏旨 + 召对 | 对在办或已复命旨意问水分、责任和下一手 |
| 履约回访 | 召对 | 对 `conversation_goals`、`negotiation_agreements` 的旧约核查 |
| 密揭/密奏 | 密查/密令 | 暗线结果，可进入御案或召对追问 |

禁止再用泛泛的“回报”同时指代以上四类事项。国策中枢只接收它们产出的证据，不替代御案复命或奏对履约。

## 人格与召对活人感

详见 [docs/personality-dialogue-rework.md](docs/personality-dialogue-rework.md)。本轮梳理确认：当前不是没有性格数据，而是性格没有成为玩家可感知的输出合同。`characters.style`、`npc_network`、`npc_tiangang`、`npc_dialogue_behavior_brief()`、履约账本和旧事记忆已经存在，但它们大量停留在隐藏提示、审计 payload 和执行风险里，最终回答容易被通用官场 prompt 压成同一种稳妥话术。

新的硬边界：

| 层级 | 人格必须显化为 |
|------|----------------|
| 称谓 | 臣、奴婢、臣妾、外臣等身份称谓不能错 |
| 第一关注点 | 户部谈源额去向，边将谈饷械军心，清流谈名分程序，内廷谈明旨保密 |
| 条件 | 支持必须带本人会在意的承办边界 |
| 拒绝 | 反对必须有符合身份、派系、关系网的理由 |
| 隐瞒 | 半真半假者可选择性陈述，不应突然全知全诚 |
| 人情 | 同党、恩主、政敌、旧怨要改变说法 |
| 句式 | 老臣、边将、内侍、格物臣、后宫人物要有不同节奏 |

已新增 `npc_dialogue_voice_contract()`，并注入大臣/后宫 Agent 的 system instructions；`npc_dialogue_behavior_brief()` 也增加本轮话术自检。后续验收不能只检查“行为档案是否有差异”，必须检查玩家最终听到的回答是否有差异。

## 触发条件重排

### 路线争议进入御案

- 奏对中出现明确路线承诺。
- 指令命中国策关键词但未成正统。
- 弹劾、荐疏、请款等奏疏引用 doctrine issue。
- 冲突国策达到上限时，只能显示“可改弦”，不能直接成为正统。

### 路线成为基本国策

- `issues(origin_kind='doctrine')` 推进到足够 bar。
- 无 active 冲突国策 blocker。
- 通过御案、诏旨或履约证据闭环。
- 成功后写入 `legacies(legacy_key='doctrine:*')`。

### 净身/宦官工具链

净身与宦官线不再作为随时可按的平行按钮，必须满足至少一个条件：

- 已有奏对承诺。
- 有身份转换证据或把柄证据。
- “内廷制衡外朝”路线已进入争议或成为正统。
- 高风险操作必须留下密令/履约/弹劾证据，可被反噬。

## 迁移顺序

1. 写入本方案文档，明确模块边界和财政纪律。
2. 增加 `PolicyCenter` 与 `/api/policy_center`。
3. 增加 `FiscalCenter` 与 `/api/fiscal_center`。
4. 扩展 `calc_province_fiscal()` 明细字段。
5. 修正文档与代码注释中的财政口径冲突，统一“月度万两”。
6. 重构移动端“天下”为“国策”，把财政、疆域、军队嵌入国策页。
7. 拆分旨意成命/传旨/承办/复命/复盘，修正“下达周期”误读。
8. 把人格声音合约纳入召对主循环，保证性格、人脉、旧事和履约压力能显成玩家听得见的差异。
9. 收敛旧入口，避免御前、御案、诏旨、奏对各自重复解释国策和财政。
10. 最后清理重复文案、旧财政展示和无主状态。

## Test Plan

国策测试：

- latent / contested / orthodox 路线分类正确。
- 冲突国策达到上限后不能直接成正统。
- 奏疏、旨意、履约证据能按规则挂到路线中枢。
- 同日直接处置只能作为路线证据，不能伪装成长周期政策执行。

财政测试：

- `FiscalCenter` 月收入合计等于 `compute_budget_lines()`。
- 每省税源明细之和等于国库动态税收。
- 调整辽饷、盐税、商税会同步改变 `regions.fiscal` 并影响下月收入。
- 调整田赋会同步改变 `tax_per_turn` 残差。
- 军饷不足会增加 `armies.arrears` 并进入财政面板。

前端验证：

- `npm run build`
- 国策页能显示财政来源、支出、月净、欠饷和路线约束。
- Desk / Edicts / Audience 的路线入口能回到同一国策详情。
- 诏旨页显示“已下达 + 传旨/承办/预计复命”，不得显示“下达需 X 日”。
- 净身等同日处置当天办结，并能进入御案复命和国策证据。
- 御案复命、诏旨复盘、奏对履约回访在文案和入口上能清楚区分。
- 同一问题召见不同 NPC，回答必须能看出本职关注点、称谓、条件、隐瞒或关系压力差异。

## 当前实现落点

- 后端已新增 `ming_sim/policy_center.py`。
- 后端已新增 `ming_sim/fiscal_center.py`。
- FastAPI 已新增 `/api/policy_center` 与 `/api/fiscal_center`。
- `calc_province_fiscal()` 已返回可解释税源明细。
- 移动端已将底部“天下”改为“国策”，`PolicyCenterView` 消费两个中心 payload。
- `docs/modules/economy.md` 已统一“月度万两”口径。
