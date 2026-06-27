# 差使大厅 · 实施路线图（P0）

> 配套设计稿：[`assignment-hall-design.md`](./assignment-hall-design.md)
>
> 本稿把设计稿拆成可执行的分阶段任务清单，沿用 `rebuildplan.md` 的颗粒度：每条任务可独立提交、可独立验收。打勾即完成。

## 任务编号约定

- `A0.x` 地基（表结构、统一写入函数、大厅只读查询）
- `B0.x` 入口收敛（五种 assignment_kind 接入统一写入）
- `C0.x` quest_* 重新定位为 NPC 奏请
- `D0.x` API 与路由
- `E0.x` 验收与回归

里程碑顺序：`A0 → B0 → C0 → D0 → E0`。A0/C0 可并行，B0 依赖 A0，D0 依赖 B0+C0。

---

## A0 · 地基

### A0.1 表结构迁移（幂等）
- [x] A0.1.1 在 `upgrade_schema.py` 增加：`turn_directives.assignment_kind TEXT NOT NULL DEFAULT 'edict'`
- [x] A0.1.2 增加：`turn_directives.source_petition_id INTEGER NOT NULL DEFAULT 0`
- [x] A0.1.3 老存档验证：`test_old_directives_default_to_edict`（老 INSERT 不带新列 → 默认 edict）
- [ ] A0.1.4 player_quests 新状态枚举注释 + 旧 `active` 开档迁移置 `settled`（C0 一并做）

### A0.2 统一写入入口 `ming_sim/assignment.py`
- [x] A0.2.1 新建 `ming_sim/assignment.py`，提供 `issue_assignment(db, state, *, kind, text, actor, day, ...)`
- [x] A0.2.2 内部流程：INSERT → 取回 row → `init_directive_lifecycles` → 回写两列
- [x] A0.2.3 `secret_order` 拒绝入 issue_assignment（**修正**：密旨走独立 `secret_orders` 表，不落 turn_directives）
- [x] A0.2.4 `source_context` 序列化进 `chain` JSON
- [x] A0.2.5 返回结构含 `{id, assignment_kind, entry_label, eta_day, assignee, resistance, chain}`
- [x] A0.2.6 单元测试：三种可写 kind 下达 + secret_order 拒绝 + source_context 合并

### A0.3 差使大厅聚合查询（**跨表：turn_directives + secret_orders**）
- [x] A0.3.1 `assignment_dashboard(db, view, include_done, limit)`：`by_official / by_region / by_category / by_status`
- [x] A0.3.2 `_secret_order_cards()` 归一化 secret_orders 行（状态映射 active→executing / pending_review→stalled）
- [x] A0.3.3 `assignments_needs_action(db)`：跨表——封驳/候核议 + done 未追问 + 逾期
- [x] A0.3.4 `assignments_overloaded(db, threshold=3)`：跨表按 assignee 聚合（旨意+密旨合计）
- [x] A0.3.5 `assignments_recent_settled(db, days=30)`
- [x] A0.3.6 单元测试：跨表聚合、四种视图、pending_review 进 needs_action

---

## B0 · 入口收敛

### B0.1 颁诏入口
- [x] B0.1.1 迁移默认值 `assignment_kind='edict'` 已覆盖现有颁诏路径（`decree.resolve_directives` / `db.add_directive` 无需改动）
- [x] B0.1.2 验证颁诏批处理多道旨意仍正常入大厅（回归 `test_lifecycle` / `test_edict_outcome` 83 用例全绿）

### B0.2 密旨入口（**修正：跨表聚合，不收敛写入**）
- [x] B0.2.1 确认密旨已有独立 `secret_orders` 表 + `active/pending_review` 引擎（不迁移）
- [x] B0.2.2 `_secret_order_cards()` 归一化密旨行进大厅，状态映射 `pending_review→stalled`（进 needs_action）
- [x] B0.2.3 `issue_assignment(kind='secret_order')` 显式拒绝，引导调用方用 `db.create_secret_order()`
- [x] B0.2.4 验证密旨在大厅按主办官/类别(secret)/状态正确分组

### B0.3 召对交办入口
- [x] B0.3.1 sealed 握手改走 `issue_assignment(kind='audience_commission')`（dialogue_goals.py:1620）
- [x] B0.3.2 source_context 带召对上下文（minister/agreement_id/action_kind/core_topic）
- [x] B0.3.3 验证召对交办出现在大厅且 entry_label=召对交办（test_quest_dialogue_integration 更新）

### B0.4 奏请获准入口（依赖 C0）
- [x] B0.4.1 `grant_petition(db, state, petition_id, draft_text, ...)`：player_quests → granted + issue_assignment(petition_grant)
- [x] B0.4.2 差使办结回写：`settle_petition_on_directive_done` 按 source_petition_id 置 settled
- [x] B0.4.3 `reject_petition` / `submit_petition` / `list_petitions` 配套
- [x] B0.4.4 验证奏请单与差使可双向跳转（test_assignment_api 端到端）

### B0.5 常驻差使入口（P0 仅登记）
- [ ] B0.5.1 `personnel_actions.py` 授差使时调 `issue_assignment(kind='posting')`（留 P1，当前 posting 可经 POST /api/assignments 手工登记）

---

## C0 · quest_* 重新定位为 NPC 奏请

> **落地策略改为软迁移**：保留 quest_manager 旧方法作遗产基础设施（硬删会破坏已注册的 quest_api/quest_refresh），
> 通过新 content/路由实现语义重定位。quest_* 表现为「NPC 奏请模板存储」。

### C0.1 状态机与奏请语义（软重定位）
- [x] C0.1.1 新增 `assignment.grant_petition / reject_petition / submit_petition / list_petitions / settle_petition_on_directive_done`
- [x] C0.1.2 player_quests 新状态流转：available → granted(转差使) / rejected；差使办结 → settled
- [x] C0.1.3 旧 quest_manager 方法保留（create_quest/accept_quest/update_quest_progress 作遗产，供旧路由与 quest_refresh 过渡）
- [ ] C0.1.4 player_quests 旧 `active` 存量开档迁移置 `settled`（待 E0 老存档验收时一并）

### C0.2 废弃桥接
- [x] C0.2.1 删除 `dialogue_to_quest.py`
- [x] C0.2.2 dialogue_goals.py:1620 sealed 握手改调 `issue_assignment(audience_commission)`，全仓无残留引用

### C0.3 content 语义重写
- [x] C0.3.1 `content/quests_examples.json` 改 `petitions` 结构（8 条明末奏请：辽饷/清丈/赈陕/科举/惩贪…）
- [x] C0.3.2 `quest_loader.py` 适配：优先读 petitions，向后兼容旧 quests；新增 `_create_petition_from_dict`
- [x] C0.3.3 奏请模板落 quests 表（source_type='npc_petition'），draft_directive/proposer 存 objective_config

---

## D0 · API 与路由

### D0.1 差使大厅路由（`ming_sim/assignment_api.py`）
- [x] D0.1.1 `GET /api/assignments?view=by_official|by_region|by_category|by_status`
- [x] D0.1.2 `GET /api/assignments/needs_action`
- [x] D0.1.3 `GET /api/assignments/overloaded`
- [x] D0.1.4 `GET /api/assignments/recent_settled`
- [x] D0.1.5 `GET /api/assignments/{id}` 单条详情

### D0.2 统一下达路由
- [x] D0.2.1 `POST /api/assignments` body `{kind, text, actor, source_context}` → issue_assignment
- [x] D0.2.2 secret_order 经 API 显式 400 拒绝（密旨走独立 create_secret_order）
- [x] D0.2.3 web_app.py 注册 `register_assignment_routes`

### D0.3 奏请路由
- [x] D0.3.1 `GET/POST /api/petitions`、`POST /api/petitions/{id}/grant|reject`、`GET /api/petitions/history`
- [x] D0.3.2 grant 时 draft_text 为空自动取模板 draft_directive
- [ ] D0.3.3 旧 `/api/quests/*` 路由返回 410 Gone 别名（当前保留运行，过渡期未下线）

---

## E0 · 验收与回归

- [x] E0.1 颁诏 / 密旨(聚合) / 召对交办 / 奏请御批 四种下达，均出现在同一大厅，entry_label 正确
- [x] E0.2 大厅按主办官分组，同一人 ≥3 活跃差使标黄（含密旨）
- [x] E0.3 待处置专注队列汇集：封驳/候核议 + 已复命未追问 + 逾期
- [x] E0.4 御批奏请后奏请单 `granted`、大厅出现 `petition_grant` 差使，二者可互跳
- [x] E0.5 老存档加载验证：真实存档（data/saves/auto_*_preresolve.db）经 GameDB 加载 → 迁移幂等补两列 → 老旨意默认 edict → draft 候选正确排除 → 颁诏后入大厅 → 跨表（旨意+密旨+召对交办）聚合正确（验证在 /tmp 副本，真实存档未改动）
- [x] E0.6 全流程零新增 LLM 调用
- [x] E0.7 全量 888 测试通过（含新增 test_assignment / test_assignment_api / 更新的 test_quest_dialogue_integration）

---

## 依赖与风险

| 风险 | 缓解 |
|---|---|
| lifecycle 状态机被误改 | A0.2 严守"只在外层包"，`init_directive_lifecycles` 内部不动 |
| 老存档 player_quests.active 脏数据 | C0.1 开档迁移置 `settled`，不尝试还原 RPG 语义 |
| 密旨/召对入口分散在多处 | B0.2/B0.3 先 grep 全部落库点，逐个改调 `issue_assignment` |
| 前端旧"任务"页 | D0.3 旧路由留 410 别名过渡，前端单独排期下线 |

## 不在本路线图（P1/P2）

NPC 领旨表态、多阶段里程碑、期限自定与逾期追责、办差功过册、任务依赖/冲突/转化、NPC 主动上奏触发器、常驻差使按月推进。详见设计稿第十二节。
