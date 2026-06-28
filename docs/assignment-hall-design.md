# 差使大厅与统一指派入口设计

> 目标：把"玩家给 NPC 派任务"这件事，从分散在颁诏/密旨/召对/人事任命的多条暗线，收敛为一个玩家可读、可管、可追问的统一系统——**差使大厅（Assignment Hall）**。
>
> 本稿是 P0 阶段（地基）的详细设计，不涉及 P1/P2 的玩法深化（NPC 表态、多阶段、期限、关联等留待后续设计稿）。

## 一、背景与现状诊断

### 1.1 两套并存的"任务"系统

| | 系统 A：`quest_*` | 系统 B：`lifecycle.py` 指令生命周期 |
|---|---|---|
| 方向 | NPC → 玩家（NPC 发任务，玩家接/弃/领奖） | **玩家 → NPC**（玩家颁旨，NPC 承办） |
| 表 | `quests` / `player_quests` | `turn_directives` |
| 奖励 | 给玩家的 imperial_power / npc_trust | 落到国库 / 民心 / 势 / RA |
| 完成度 | 半成品，`dialogue_to_quest.py` 桥接生硬 | 成熟：工期 / 旬检定 / 账实分离 / 干预 / 因果伏笔全有 |
| 设计依据 | `docs/archive/quest-system-redesign.md` 照搬魔兽世界 RPG 任务 | 与"你是崇祯下旨"的核心机制一致 |

**核心判断**：本游戏玩家扮演崇祯下旨，"玩家给 NPC 的任务"本体就是系统 B。系统 A 方向相反（NPC 给玩家发任务），与游戏机制冲突，历史设计文档承认了这一点。

### 1.2 系统 B 的成熟之处（保留不动）

`lifecycle.py` 已具备的能力，本设计**不改其内部状态机**，只在外层包一个统一读法：

- 状态机：`issued → in_transit → executing → done / stalled / aborted`
- 工期：`lead_days + exec_days`，由能力 / 距离 / 阻力 / 基座特质 / 国策 / 治术修正
- 旬检定：`delay / skim / block / surprise` 四类异常，确定性种子可复盘
- 账实分离：`integrity_actual`（真实）vs `integrity_reported`（奏报），截留黑箱
- 玩家干预：`cuiban / reassign / fund / ducai / abort / bargain_blocker / pressure_blocker`
- 复命追问：done 后 `rewarded / accounted / followup_evasive / next_step / reviewed`
- 因果伏笔：办结时 `_plant_consequences` 埋延迟代价

### 1.3 系统 B 当前缺口（本稿只解前两个）

| 缺口 | 本稿是否处理 |
|---|---|
| 1. 指派入口分散（颁诏/密旨/召对交办/人事任命互不通气） | **是（P0）** |
| 2. 无"差使大厅"聚合视图，玩家看不全自己派出去的差使 | **是（P0）** |
| 3. 任务品种单一（无密差/常驻差使等独立状态机） | 否（P1） |
| 4. NPC 不会领旨表态/讨价 | 否（P1） |
| 5. 跟踪粒度粗（单进度条，无里程碑/中途旬报） | 否（P1） |
| 6. 结果反馈单薄（无功过册/赏罚兑现） | 否（P1） |
| 7. 任务间无关联（依赖/冲突/转化） | 否（P2） |
| 8. 期限不可玩 | 否（P1） |

### 1.4 quest_* 系统的新定位

**重新定位为「NPC 奏请 / 请托」**：保留表结构与基础设施，但语义从"NPC 给玩家发任务"反转为"NPC 向皇帝提出的奏请"，玩家可批 / 驳，与玩家主动派差（系统 B）形成**双向闭环**。

- 旧：`NPC --(quest)--> 玩家接受 --> 玩家完成 --> 玩家领奖`
- 新：`NPC --(奏请)--> 玩家御批 --> 转为一道差使(Assignment) --> NPC 承办`

这样 `quest_*` 不再是平行于游戏机制的外挂，而是**差使的来源之一**（玩家也可以主动颁诏、密旨、召对交办，殊途同归进入差使大厅）。

## 二、核心原则

1. **不推翻 lifecycle 状态机**。`turn_directives` 的内部推进逻辑（`tick_directives`、`intervene`、`apply_directive_audience_pressure`）保持不变，差使大厅是它之上的一层只读聚合 + 入口收敛。
2. **一个差使，一个来源标记**。所有进入大厅的差使都有 `assignment_kind`，标明它是怎么来的（颁诏 / 密旨 / 召对交办 / NPC 奏请转批 / 人事任命到差），但底层执行口径统一。
3. **大厅只读、入口可写**。大厅负责"看清"，各入口负责"下达"。大厅本身不下旨，只跳转到对应入口。
4. **quest_* 转软不转硬**。NPC 奏请被御批后，不直接复用 player_quests 的完成逻辑，而是落一道新的 `turn_directives`（带 `source='npc_petition'`），由 lifecycle 推进。player_quests 只保留"奏请单"语义。
5. **零 LLM**。P0 全部是规则层聚合查询，不引入新的 LLM 节点。
6. **向后兼容**。现有存档的 `turn_directives` 不需要迁移即可进入大厅；新字段全部带默认值。

## 三、核心抽象：Assignment（差使）

### 3.1 统一模型

"差使"= 玩家交给某个 NPC 去办的一件事。当前唯一实体是 `turn_directives`，本设计给它补一个**入口分类维度**，而不是新建一张表：

```
Assignment = turn_directives + assignment_kind + (可选) source_petition_id
```

### 3.2 assignment_kind 取值

> **实现修正（落地时发现）**：密旨已有独立的 `secret_orders` 表与 `active/pending_review` 引擎，
> 强行塞进 `turn_directives` 会破坏现有功能。故 **secret_order 改为"跨表聚合"**——
> 大厅归一化 `secret_orders` 行展示，下达仍走 `db.create_secret_order()`，不落 `turn_directives`。
> 其余四种 kind 落 `turn_directives`。

| kind | 中文 | 来源入口 | 底层处理 |
|---|---|---|---|
| `edict` | 颁诏 | 颁诏界面（现有） | 走 `lifecycle.init_directive_lifecycles`（现状） |
| `secret_order` | 密旨 | `db.create_secret_order()`（现有独立引擎） | **不落 turn_directives**；大厅跨表聚合，状态 `active→executing / pending_review→stalled / done / failed→aborted` |
| `audience_commission` | 召对交办 | 召对时口头交办 | 走 lifecycle，`source='audience'`，自动带召对上下文 |
| `petition_grant` | 奏请获准 | NPC 奏请被玩家御批 | 走 lifecycle，`source='npc_petition'`，关联 player_quests.id |
| `posting` | 常驻差使 | 人事任命到差（督师/经略/矿税太监等） | **P1 扩展**，P0 先登记进大厅、按月产奏报占位 |

> P0 阶段 `posting` 只做**登记**（让玩家在大厅看见"某人现挂着什么差使"），其按月推进的状态机留给 P1。`edict/audience_commission/petition_grant` 三种经 `issue_assignment()` 落 `turn_directives`；`secret_order` 经 `db.create_secret_order()` 落 `secret_orders`，大厅统一读。

### 3.3 数据模型变更（最小侵入）

`turn_directives` 已有字段不变，仅**新增两列**（带默认值，老存档自动兼容）：

```sql
ALTER TABLE turn_directives ADD COLUMN assignment_kind TEXT NOT NULL DEFAULT 'edict';
ALTER TABLE turn_directives ADD COLUMN source_petition_id INTEGER NOT NULL DEFAULT 0;
```

- `assignment_kind`：见上表。老数据默认 `edict`。
- `source_petition_id`：若本差使由 NPC 奏请转化而来，指向 `player_quests.id`；否则 0。

> 不新建 assignment 表。理由：lifecycle 的全部推进逻辑都绑在 `turn_directives` 上，另起一张表会造成双写与状态分叉，得不偿失。把 turn_directives **就是** assignment 的物理实体，只补入口维度。

## 四、差使大厅（Assignment Hall）

### 4.1 大厅解决什么

让玩家在一个地方回答四个问题：

1. **我派出去的差使，现在都在谁手里、办到哪了？**（按主办官 / 地区 / 类别 / 状态分组）
2. **哪些要我现在处理？**（异常待处置、已复命待追问、逾期）
3. **某个人手里压了几件事？**（超载预警）
4. **这道差使是怎么来的？**（来源追溯：颁诏 / 密旨 / 召对 / 奏请）

### 4.2 聚合视图（分组维度）

大厅提供四种正交切片，前端可切换：

| 视图 | 分组键 | 用途 |
|---|---|---|
| `by_official` | assignee（主办官） | 看某人手里几件事、办差能力是否过载 |
| `by_region` | chain.region_id | 看某省在办几件事、是否堆在灾区和前线 |
| `by_category` | category（户部/兵部/吏部…） | 看哪条战线最吃紧 |
| `by_status` | lifecycle_status | 看异常 / 待追问 / 逾期的优先级队列 |

外加三个**专注队列**（不分组的待办筛选）：

- `needs_action`：`stalled`（封驳待圣裁）+ `done` 但未 followup（已复命未追问）+ 逾期未结
- `overloaded_officials`：同一 assignee 活跃差使 ≥ 3，标黄
- `recent_settled`：近 30 日 done/aborted 的差使（看刚结的账）

### 4.3 单条差使卡片字段（复用 lifecycle_payload，补来源）

每张卡片在现有 `lifecycle_payload` 字段基础上**追加**：

```json
{
  "assignment_kind": "edict | secret_order | audience_commission | petition_grant | posting",
  "source_petition_id": 0,
  "source_petition_title": "（仅 petition_grant 时，奏请单标题）",
  "entry_label": "颁诏 | 密旨 | 召对交办 | 奏请获准 | 常驻差使"
}
```

其余字段（id / text / status / progress / assignee / eta_day / resistance / anomaly / intervention_options / outcome_summary 等）完全沿用 `lifecycle_payload`，前端无需改读法。

## 五、统一入口（Unified Entry）

### 5.1 入口收敛图

```
            ┌─ 颁诏界面 ──────────────── assignment_kind=edict ──────┐
            ├─ 密旨/中旨界面 ─────────── assignment_kind=secret_order┤
玩家 ────┬──┼─ 召对「交办」语义 ──────── assignment_kind=audience_commission ──┤──→ turn_directives ──→ lifecycle
         │  ├─ 人事任命「授某差使」 ──── assignment_kind=posting(P0仅登记) ┤
         │  └─ 御批 NPC 奏请 ─────────── assignment_kind=petition_grant ┘
         │                                          ↑
         └─ NPC 奏请单(player_quests 重新定位) ─────┘
```

### 5.2 各入口的 P0 改动量

| 入口 | P0 改动 |
|---|---|
| 颁诏 | **几乎无改动**。颁诏落库时给 `assignment_kind` 默认填 `edict`（即现状，加默认列即可） |
| 密旨 | 收敛现有 `negotiation.py` 的 `secret_order` 分支，落 `turn_directives` 时标 `secret_order`，并设 `chain.timing_profile='secret'`（内廷即办，不走上谕发抄） |
| 召对交办 | 召对中识别"交办/着你去办/限你办"等语义（先走规则正则，P1 再接语义审计），生成 `turn_directives` 标 `audience_commission`，把召对上下文存进 `chain.source_context` |
| 奏请获准 | 新增「御批奏请」动作：把一条 `player_quests`（NPC 奏请单）转为 `turn_directives` 标 `petition_grant`，回填 `source_petition_id`，并把 player_quests 状态置 `granted` |
| 常驻差使 | P0 仅登记：人事任命到某差使（如督师）时，写一条 `assignment_kind=posting` 的 `turn_directives`，`lifecycle_status='executing'`、`exec_days` 设很大，**暂不推进**（P1 再做按月产报） |

### 5.3 统一写入函数

新增 `ming_sim/assignment.py`，提供唯一一个写入入口（各 kind 的具体语义在此收口）：

```python
def issue_assignment(
    db, state, *,
    kind: str,            # edict/secret_order/audience_commission/petition_grant/posting
    text: str,            # 差使内容（旨意正文 / 口头交办 / 奏请转写）
    actor: str = "",      # 指定主办（空则 lifecycle 自动 pick）
    day: int,             # 当前日
    source_petition_id: int = 0,
    source_context: dict | None = None,   # 召对片段、密旨缘起等
) -> dict:
    """统一差使下达入口。内部仍走 lifecycle.build_chain + init_directive_lifecycles，
    仅补 assignment_kind / source_petition_id 两列与 kind 相关的 timing 覆盖。"""
```

各业务入口（颁诏 handler、密旨 handler、召对 handler、奏请御批 handler）P0 内统一改为调 `issue_assignment`，杜绝再各自直接 `INSERT turn_directives`。

## 六、quest_* 重新定位为 NPC 奏请

### 6.1 语义反转

| 字段 | 旧含义 | 新含义 |
|---|---|---|
| `quests`（模板） | NPC 发的任务模板 | NPC 奏请模板（哪类官员会提哪类请） |
| `player_quests`（实例） | 玩家接的任务 | 待御批的奏请单 |
| `status=available` | 玩家可接 | 奏请已上达，待批 |
| `status=active` | 玩家在做 | **删除此态**（玩家不亲自做，御批即转差使） |
| `status=completed` | 玩家做完领奖 | **改为 granted**：已御批，已转为差使 |
| `reward_config` | 给玩家奖励 | **删除**（奖励逻辑移交给差使办结的 outcome_delta） |

### 6.2 状态机收敛

```
player_quests.status:  available(待批) ──御批──→ granted(已转差使) ──差使办结──→ settled
                          │
                          └──驳回──→ rejected
```

- `granted` 时写一条 `turn_directives(assignment_kind='petition_grant', source_petition_id=<pq.id>)`。
- 差使 `done` 时，回写 player_quests.status=`settled`（用 `source_petition_id` 反查）。
- 删除 `accept_quest / update_quest_progress / complete_quest / abandon_quest` 在新语义下无意义的路径；`quest_api.py` 的 `/accept /progress /complete` 路由改为 `/grant /reject`。

### 6.3 NPC 奏请的产生（P0 最小实现）

P0 不做"NPC 主动上奏"的触发器，只**暴露手工入口**：

- `POST /api/petitions` ：由召对 / 事件模块把 NPC 的请托写成一条 `player_quests(status='available')`。
- 前端在"奏疏/御案"侧展示待批奏请，玩家点「准」→ 走 `issue_assignment(kind='petition_grant')`。

> NPC 主动上奏的触发逻辑（基于派系诉求、地方情形、个人野心）留 P1，复用 `memorials` / `intrigue` / `ambition` 现有信号。

## 七、API 设计

### 7.1 差使大厅（只读聚合）

```http
GET /api/assignments?view=by_official&include_done=false&limit=60
GET /api/assignments?view=by_region
GET /api/assignments?view=by_category
GET /api/assignments?view=by_status
GET /api/assignments/needs_action     # 待处置专注队列
GET /api/assignments/overloaded       # 超载官员
GET /api/assignments/recent_settled   # 近期结案
GET /api/assignments/{directive_id}   # 单条详情（= 现有 lifecycle 单条 + 来源字段）
```

`view=by_official` 返回结构示例：

```json
{
  "view": "by_official",
  "groups": [
    {
      "key": "周延儒",
      "assignee": "周延儒",
      "office": "内阁首辅",
      "active_count": 3,
      "overloaded": true,
      "items": [ { ...单条差使卡片... }, ... ]
    },
    ...
  ],
  "total": 12,
  "summary": { "in_transit": 2, "executing": 7, "stalled": 1, "done_unfollowed": 2 }
}
```

### 7.2 统一下达（写）

```http
POST /api/assignments
body: { "kind": "audience_commission", "text": "着兵部速查辽东欠饷实数", "actor": "梁廷栋", "source_context": {...} }
→ 调 issue_assignment(...)，返回新差使 id 与 lifecycle 预览（工期/阻力/chain）
```

> 颁诏仍是独立路由（它一次性可能下多道旨、走批处理），但内部调 `issue_assignment(kind='edict')`。

### 7.3 NPC 奏请（quest_* 新语义）

```http
GET    /api/petitions                 # 待批奏请单（status=available）
POST   /api/petitions                 # 模块提交一条奏请（召对/事件用）
POST   /api/petitions/{id}/grant      # 御批 → 转 petition_grant 差使
POST   /api/petitions/{id}/reject     # 驳回 → status=rejected
GET    /api/petitions/history         # 已批/已驳历史
```

### 7.4 路由迁移

| 旧路由（quest_api.py） | 处置 |
|---|---|
| `GET /api/quests` | 改名 `/api/petitions`，语义为待批奏请 |
| `POST /api/quests/{key}/accept` | **删除**（玩家不再"接"任务） |
| `POST /api/quests/{id}/progress` | **删除**（进度由差使 lifecycle 推进） |
| `POST /api/quests/{id}/complete` | **删除**（完成由差使办结触发） |
| `POST /api/quests/{id}/abandon` | 改为 `/api/petitions/{id}/reject` |
| `GET /api/quests/npc/{name}` | 改为 `/api/petitions?npc={name}` |

`dialogue_to_quest.py` 整体废弃（其 create_quest_from_dialogue 的握手转任务逻辑，改由召对 handler 直接 `issue_assignment(kind='audience_commission')`）。

## 八、模块边界

| 模块 | 新定位 | P0 改动 |
|---|---|---|
| `lifecycle.py` | 差使的物理推进引擎，**不动内部状态机** | 仅 `init_directive_lifecycles` / `build_chain` 接收并透传 `assignment_kind` |
| `assignment.py`（新） | 统一下达入口 + 大厅聚合查询 | 新增 |
| `quest_manager.py` | 降格为"NPC 奏请单"管理 | 收敛状态机，删玩家领奖路径 |
| `quest_api.py` | 改名 `petition_api.py`，路由按 7.4 迁移 | 改写 |
| `dialogue_to_quest.py` | **废弃** | 删除 |
| `quest_loader.py` | 加载 NPC 奏请模板（content/quests*.json 改语义） | 微调 |
| `negotiation.py`（密旨） | 密旨分支改调 `issue_assignment(kind='secret_order')` | 收敛 |
| `personnel_actions.py` | 授差使时登记 `posting`（P0 仅入大厅） | 加登记调用 |
| 召对 handler | 识别"交办"语义，调 `issue_assignment(kind='audience_commission')` | P0 用正则，P1 接语义审计 |

## 九、content 配置

### 9.1 content/quests*.json 语义重写

文件保留，但字段语义从"任务模板"改为"NPC 奏请模板"：

```json
{
  "petitions": [
    {
      "petition_key": "户部请赈陕西",
      "title": "请发帑银赈济陕西",
      "description": "陕西连岁大旱，流民四起，户部奏请发内帑二十万两赈济。",
      "proposer_office": "户部",
      "proposer_faction": "清流",
      "trigger_hint": { "region": "shaanxi", "unrest_min": 60 },
      "draft_directive": "发内帑二十万两赈济陕西",
      "category_hint": "relief"
    }
  ]
}
```

- `draft_directive`：御批后落为 `turn_directives.text` 的草稿（玩家可改）。
- `trigger_hint`：P1 的自动上奏触发器用；P0 手工提交时忽略。

### 9.2 不新增 content

P0 不为 `assignment_kind` 新建配置文件——kind 的差异由代码里的 timing 覆盖（密旨即办、召对带上下文、奏请带 draft）处理，不进 JSON。

## 十、迁移与兼容策略

1. **表结构**：`turn_directives` 加两列带默认值，`upgrade_schema` 里加幂等 `ALTER`（`PRAGMA table_info` 检测列存在性，老存档开档自动补）。
2. **player_quests 状态**：开档时把存量 `status='active'` 的旧数据视为脏数据，统一置 `settled`（旧 RPG 任务语义已不适用，不尝试还原）。
3. **路由**：`quest_api.py` 旧路由保留 30 天别名（返回 410 Gone + 指向新路由），再删除。
4. **前端**：差使大厅是新页面；旧"任务"入口若存在则下线，统一指向大厅与奏请页。

## 十一、验收标准（P0）

- [ ] 玩家颁一道诏、下一道密旨、召对交办一件事、御批一份奏请——四者都出现在**同一个差使大厅**里，且 `entry_label` 正确。
- [ ] 大厅按主办官分组时，能看出某人手里活跃差使数；同一人 ≥3 时标黄。
- [ ] 待处置专注队列正确汇集：封驳待圣裁 + 已复命未追问 + 逾期。
- [ ] 御批一份 NPC 奏请后，奏请单状态变 `granted`，同时大厅出现一条 `petition_grant` 差使，二者可互跳。
- [ ] 老存档加载后，原有 turn_directives 全部以 `edict` 身份出现在大厅，无数据丢失、无报错。
- [ ] 全流程零新增 LLM 调用。

## 十二、本稿明确不做（留给后续设计稿）

- 任务品种独立状态机（密差/常驻差使的按月推进）→ P1
- NPC 领旨表态 / 讨价 / 请辞 → P1
- 多阶段里程碑 / 中途旬报 → P1
- 期限自定与逾期追责 → P1
- 办差功过册 / 赏罚兑现 → P1
- 任务依赖 / 冲突检测 / 调查转弹劾 → P2
- NPC 主动上奏触发器（基于派系/地方/野心）→ P1

---

附：本设计与 `rebuildplan.md` 的关系——`policy_center` 是"国策怎么看"，本设计的差使大厅是"差使怎么管"，二者同属玩家中枢层，但职责正交：国策解释路线与财政约束，差使大厅解释执行与人事。
