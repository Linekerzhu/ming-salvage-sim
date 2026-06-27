# 差使系统 P1 实施路线图

> 配套：[`assignment-hall-design.md`](./assignment-hall-design.md)（P0 设计）、[`assignment-hall-roadmap.md`](./assignment-hall-roadmap.md)（P0 已完成）。
>
> P0 让"差使大厅"可看可派；P1 让差使"有人味、有阶段、有期限、有功过"。本稿按 **价值×自洽×低风险优先** 排序。

## P1 功能清单与优先级

| 序 | 功能 | 价值 | 自洽度 | 风险 | 推荐批次 |
|---|---|---|---|---|---|
| P1.1 | **NPC 领旨表态**（遵旨/请辞/请限/请拨/附条件） | 极高（让派差有人味，P0 最大缺口） | 高（对接 build_chain 的 resistance/foundation_mods） | 低（纯规则层，零 LLM） | **第一批** |
| P1.2 | **期限自定 + 逾期追责** | 高（explicit_deadline 已解析但不可玩） | 高（领旨"请限"天然衔接） | 低 | 第一批 |
| P1.3 | **办差功过册 + 赏罚兑现** | 高（done 后 followup 只有微调，无履历） | 中（需新表/聚合） | 中 | 第二批 |
| P1.4 | **多阶段里程碑 + 中途旬报** | 中高（单进度条→阶段化） | 中（chain 结构扩展） | 中（schema） | 第二批 |
| P1.5 | **常驻差使 posting 按月产报** | 中（B0.5 桩） | 中（独立状态机） | 中 | 第三批 |
| P1.6 | **NPC 主动上奏触发器** | 中（C0 桩，奏请来源自动化） | 中（接 ambition/intrigue/memorials 信号） | 中 | 第三批 |
| P2 | 任务依赖/冲突检测/调查转弹劾 | 中 | 低（跨任务图） | 高 | P2（单独设计） |

## 推荐执行顺序与理由

1. **P1.1 领旨表态** + **P1.2 期限追责** 同批：二者在"下达瞬间"自然汇合——
   领旨的"请限"= 要期限、逾期= 期限后果。一起做避免返工。**先做这个**。
2. **P1.3 功过册**：done 后的结算深化，独立于 1/2，第二批。
3. **P1.4 里程碑**：跟踪粒度，schema 改动，第三批。
4. **P1.5 / P1.6**：posting 月报 + 自动上奏，锦上添花，最后。

---

## P1.1 · NPC 领旨表态（详细设计）

### 现状缺口
`issue_assignment` 颁出后，assignee 被静默 pick，立即开工。NPC 不会拒接、请限、讨价——
差使"没有人味"。现有 `apply_directive_audience_pressure` 只处理**事后**追问，缺**事前**领旨节点。

### 表态 stance 五态
| stance | 含义 | 数值后果 | 玩家可见动作 |
|---|---|---|---|
| `accept` 遵旨 | 全力承办 | 怨气 +1（常态）；进度无惩罚 | 无（默认推进） |
| `request_time` 请限 | 接旨但请宽限 | `exec_days ×1.4`；怨气 +2 | 可准/驳（准→延期，驳→催办） |
| `request_fund` 请拨 | 接旨但需钱粮 | 标记 `needs_support`；阻力 +5 | 走现有 fund 干预 |
| `decline` 请辞 | 辞以不逮/已满 | 进度起手 −15；怨气 +6；势 −1（君命被推） | 可强令（催办）/换人 |
| `conditional` 附条件 | 附带政治条件 | chain 记 condition；阻力 +8 | 可应允（协调阻力）/驳回 |

### 表态判定（确定性，零 LLM）
种子 = `directive_id * 100003 + day`。综合分 `willingness` 0-100：
```
willingness = 50
  + (ability-50)*0.25        # 能力高更敢接
  + (loyalty-50)*0.30        # 忠心高更愿接
  - grievance*0.30           # 怨气高想推
  - resistance*0.25          # 阻力大想推
  - max(0, active_load-2)*8  # 手里已 ≥3 件，每多一件 −8
  + trait_score              # 基座擅/痼修正（foundation.directive_modifiers）
```
- willingness ≥ 70 → `accept`
- 60-69 → `request_time`（请限）
- 50-59 → `request_fund`（请拨）
- 35-49 → `conditional`
- < 35 → `decline`
（角色若为"刚愎/优柔"等特质，可平移阈值，P1 先用基础公式）

### 落库与集成
- [x] 新增 `assignment.generate_acceptance(db, state, directive_id, day) -> dict`
- [x] 在 `issue_assignment` 内、`init_directive_lifecycles` 之后调用（失败不阻塞下达）
- [x] 结果写进 `chain.acceptance`：`{stance, willingness, narrative, applied_effects}`
- [x] 数值后果即刻落库（exec_days 延期 / 阻力 / 怨气 / needs_support 标记 / 势）
- [x] `lifecycle_payload` 卡片经 `_acceptance_fields` 暴露 `acceptance` 字段
- [x] 领旨叙事用模板（零 LLM）
- [x] API：`GET /api/assignments/{id}` 暴露 acceptance

### 验收
- [x] 高能力低怨气官员 → willingness 高（accept 区间）
- [x] 超载官员（active≥3）→ willingness 骤降，倾向 request_time/decline/conditional
- [x] 请限真实延期 exec_days；请拨打 needs_support 标记；请辞扣势 −1、怨气 +6
- [x] 大厅卡片显示 acceptance；公式确定性可复盘
- [x] 全量 894 测试通过

---

## P1.2 · 期限自定 + 逾期追责（详细设计）

### 现状缺口
`explicit_deadline_days` 只**解析**旨意正文里的"限 X 日内"，玩家无法在下达时主动设期限；
逾期除 stalled 自动作罢外，无主动追责（降黜/申饬）。

### 设计
- **下达设期**（P1.2a ✅）：`issue_assignment` 增 `deadline_days` 参数；非 0 时覆盖 `eta_day = start_day + deadline_days`，
  并压缩 lead/exec 使其不超期。chain 标 `player_deadline_days`，领旨"请限"在硬期限下不延期而转阻力。
- **逾期判定**（P0 已有）：lifecycle tick 有 eta_day；"逾期未结"= live 状态且 `eta_day < today`，大厅 `overdue` 标记已实现。
- **逾期追责**（P1.2b ✅）：每旬逾期触发一次自动后果（确定性），力度随逾期旬数递增：
  - 主办怨气 +(2+旬数−1)、信任 −(2+旬数−1)
  - 势 −1（君命滞于下）
  - 发 `directive_overdue` 事件进御案（"钦定期已过N旬仍无复命，可申饬追责或宽限"）
  - 触发条件：有钦定期（玩家自定 deadline_days 或旨意正文 explicit_deadline_days）且 `day > 钦定期`；逾期相对**固定钦定期**计（异常顶远运行时 eta 不影响逾期判定）
- **玩家主动追责**（P1.2c ✅）：`intervene` 加 `reprimand_overdue` 动作，severity 三档：
  - `reprimand` 申饬：怨气+8、信任−3、势+1、RA+1
  - `fine` 罚俸：怨气+10、信任−5、国库+5万、势+1、RA+1
  - `demote` 降黜：怨气+15、信任−15、势+2、RA+2、chain 标 `pending_demote`（候人事后续）
  - 逾期时 `_intervention_options` 自动露出三档；未逾期/无钦定期则拒绝

### 验收
- [x] P1.2a 下达时可设 deadline_days，eta_day 准确；硬期限下请限不顶破（转阻力）
- [x] P1.2b 逾期指令每旬触发怨气/信任/势后果，力度递增；无钦定期不追责
- [x] P1.2c reprimand_overdue 三档可执行；未逾期/无钦定期被拒
- [x] 全量 902 测试通过

---

## P1.3 · 办差功过册 + 赏罚兑现（详细设计）

### 现状缺口
done 后只有微调（followup 调信任/怨气几点）+ outcome_delta chips，**无官员履历**：看不出"某人办成几件、
办砸几件、截留几次"。赏罚也无明确兑现动作。

### 数据来源（聚合按需，零新表读取）
功过册不新建读取表，**聚合自现有数据**：
- `turn_directives`（done/aborted，integrity_actual）→ 成(≥85)/半(60-84)/败(aborted或<60)
- `report_ledger`（entity_kind='directive'）→ 截留；或 done 且 actual<85
- `chain` meta（overdue_deca_count / last_reprimand）→ 逾期/被申饬
- 新表 `merit_actions`（append-only）→ 赏罚兑现历史

### 功过分
`merit_score = 成×2 + 半×1 − 败×3 − 截留×2 − 逾期旬数 − 被申饬次数`

### 赏罚兑现三档
| 奖（grant_reward） | 效果 | 罚（apply_punishment） | 效果 |
|---|---|---|---|
| 记功 merit_mark | 信任+3 怨气−2 | 申饬 reprimand | 信任−3 怨气+5 任事观望+1 |
| 加俸 raise | 信任+5 怨气−3 国库−3 | 罚俸 fine | 信任−5 怨气+8 国库+5 |
| 超擢 promote | 信任+8 怨气−5 势+1 候升迁 | 降黜 demote | 信任−12 怨气+12 势+2 候降黜 |

### 落地
- [x] `upgrade_schema` 加 `merit_actions` 表；`assignment.ensure_merit_schema` 防御性建表
- [x] `minister_merit_ledger(db, assignee)` 单官功过册；`merit_overview(db)` 全员排行
- [x] `grant_reward` / `apply_punishment`（三档各三），写 merit_actions
- [x] `list_merit_actions(db, minister)` 赏罚历史
- [x] API：`GET /api/merit`、`GET /api/merit/{minister}`、`GET /api/merit/actions`、`POST /api/merit/{minister}/reward|punish`

### 验收
- [x] 功过册正确区分成/半/败/截留（aborted 不计截留）；report_ledger 标截留
- [x] 功过分公式正确；merit_overview 按分排行
- [x] 奖罚三档数值正确；降黜加势；赏罚入 merit_actions 历史
- [x] 全量 911 测试通过

---

## P1.4 · 多阶段里程碑（详细设计）

### 现状缺口
单条 0-100 进度条，复杂差使（如清丈全国田亩）无阶段反馈，玩家看不到"勘查到哪了、造册没"。

### 设计
- **里程碑生成**（`_generate_milestones`）：按类别 flavor + 工期分段。
  - 工期 ≤15日 → 2 段；15-40 → 3 段；>40 → 4 段；末段固定「复命」阈值 100。
  - 类别模板：fiscal→钱粮征解/解部/入库、tax→清丈/造册/征解、military→调兵/布防/进剿、
    audit→查账/追赃/具奏、secret→布线/取证/回奏……（fallback 勘查/议办/施行）
- **进度越阈值**（`_check_milestone_progress`，tick 内）：标记 done + 发 `directive_milestone` 蓝色事件
  （中途旬报"X阶段复命"）；**最终段不发**（由 directive_done 发，避免重复）。
- **存储**：chain meta `milestones` 数组；`lifecycle_payload` 暴露 `milestones` 字段。
- **前端动画**（GSAP）：`MilestoneProgress` 组件用 useGSAP 平滑动画填充宽度 + 标记错峰浮入 + 已达成脉冲。

### 落地
- [x] `_generate_milestones` / `_check_milestone_progress`（lifecycle.py）
- [x] init_directive_lifecycles 注入 milestones；tick 调用越阈值检查；lifecycle_payload 暴露
- [x] 前端 `DirectiveLifecycle` 类型加 milestones / acceptance
- [x] `MilestoneProgress.tsx`（GSAP useGSAP + scope + refs，遵循 gsap-react skill）
- [x] EdictsView 接入 MilestoneProgress；CSS 加里程碑标记样式
- [x] 安装 gsap + @gsap/react（npm）

### 验收
- [x] 长差使生成 ≥3 段；末段「复命」阈值 100；类别 flavor 正确
- [x] 进度越阈值标记 done + 发阶段事件；最终段不重复发
- [x] lifecycle_payload 暴露 milestones
- [x] 前端 `npm run build` 通过（TS + GSAP 打包）
- [x] 全量 916 测试通过
- [ ] 视觉验证（需运行态：进度推进时填充动画 + 标记浮入；可后续联调）

### GSAP skill 集成说明
按 gsap-react skill 最佳实践：`useGSAP(() => {...}, { scope, dependencies })` + `gsap.registerPlugin(useGSAP)`
+ refs 定位 + 自动 cleanup。动画：填充宽度 power2.out、标记 back.out(2) 错峰浮入、已达成脉冲。

---

## P1.5 · 常驻差使（posting）按月产报（详细设计）

### 设计
- `create_posting(db, state, minister, duty_type)`：授常驻差使，落 turn_directives(assignment_kind='posting')，
  exec_days=9999 防自动结案，chain 标 `is_posting`+`posting{duty_type}`。
- 差使类型模板（月度效果）：矿税太监(国库+8/民心-2/怨气+3)、督师经略(势+1)、巡按御史(民心+1/怨气-1)、
  总督粮储(国库+3)、专差(承办)。
- `posting_monthly_tick`：月初对所有在办 posting 产月报事件 + 应用效果（接 timeflow 月度中枢）。
- 日常 tick：posting 跳过进度推进/结案（仅完成 in_transit→executing 送达转换）。
- `revoke_posting`：撤差（人事处置，区别于收回成命；被撤者怨气+4）。

### 落地与验收
- [x] create_posting + 5 类差使模板；posting 送达转 executing 但不推进/不结案
- [x] posting_monthly_tick 月度效果+奏报事件，接 timeflow 月度中枢
- [x] revoke_posting；reject 非常驻差使
- [x] 全量 924 测试通过

## P1.6 · NPC 主动上奏触发器（详细设计）

### 设计
- `petition_auto_tick(db, state, day, max_new=2)`：月初扫描信号自动生成 available 奏请，接 timeflow 月度中枢。
  - 信号1 ambition：进度≥70 的高野心官员 → 奏请超擢（petition_key=ambition_advancement）
  - 信号2 地方民变：regions.unrest≥60 → 户部奏请赈济（petition_key=relief_<regionid>）
- 去重：`_recent_petition` 近 2 月同类/同官员不重复（避免刷屏）；每月至多 max_new 条。

### 落地与验收
- [x] 地方民变→赈济奏请；高野心→超擢奏请；无信号不生成
- [x] 去重不刷屏；list_petitions 标题对自动上奏（无模板行）回退 objective_data
- [x] 全量 924 测试通过

---

## P1 完成总览

| 项 | 状态 | 核心交付 |
|---|---|---|
| P1.1 领旨表态 | ✅ | generate_acceptance 五态 + 数值后果 |
| P1.2 期限追责 | ✅ | deadline 自定 + 逾期旬自动咬人 + reprimand 三档 |
| P1.3 功过册 | ✅ | merit_ledger 聚合 + 赏罚兑现六档 |
| P1.4 里程碑 | ✅ | 多阶段生成 + 越阈值事件 + GSAP 前端动画 |
| P1.5 常驻差使 | ✅ | posting 月报 + 5 类差使 + 撤差 |
| P1.6 自动上奏 | ✅ | ambition/民变 信号驱动 + 去重 |

**差使系统全链路闭合**：领旨→设期→阶段里程碑→逾期追责→办结落功过册→赏罚兑现；
密旨跨表聚合；常驻差使月报；NPC 主动上奏。全量 924 测试通过，零 LLM 新增。

下一步：前端优化（差使大厅/功过册/奏请面板 + GSAP 打磨）。

---

## 前端优化（P1 收尾）

### 落地
- [x] GSAP skill 安装（`~/.config/opencode/skills/gsap-*`）；项目装 `gsap` + `@gsap/react`
- [x] `MilestoneProgress.tsx`（P1.4）：useGSAP 平滑进度填充 + 里程碑 `back.out(2)` 错峰浮入 + 已达成脉冲
- [x] `AssignmentViews.tsx`：`AssignmentHallView`（四视图+待处置队列）、`MeritLedgerView`（排行+奖罚六档）、`PetitionsView`（准/驳），统一 `useEnter` GSAP 入场
- [x] `EdictsView` 升级为「差使中枢」：子 tab 旨意/差使大厅/功过册/奏请
- [x] `api.ts` 全类型覆盖：AssignmentDashboard/Card、MeritLedger、Petition + fetcher/动作
- [x] CSS：子 tab、大厅卡片（逾期红框/超载标）、功过册（成/半/败色点）、奏请卡
- [x] `npm run build` 通过（TS + GSAP 打包，57 模块）

### GSAP 用法（遵循 gsap-react skill）
`useGSAP(() => { gsap.from(".m-enter", { y, opacity, stagger, ease }) }, { scope: ref })` +
`gsap.registerPlugin(useGSAP)` + refs + 自动 cleanup。所有列表/卡片入场、进度填充、里程碑标记均走此模式。

### 视觉验证（待运行态）
进度推进→填充动画；切视图→卡片错峰浮入；功过册展开→近期差使滑入。需启动前后端联调实测。
