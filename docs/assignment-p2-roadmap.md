# 差使系统 P2 实施路线图 · 任务关联

> 配套：P0/P1 已完成。P2 让差使之间形成**关联图**：依赖、冲突预警、调查→弹劾转化。

## P2 功能清单

| 序 | 功能 | 价值 | 风险 | 落地 |
|---|---|---|---|---|
| P2.1 | **任务依赖**：B 等 A 办成才开展 | 中高（让差使成链：彻查→惩治→追赃） | 低（chain meta + tick 冻结） | 第一批 |
| P2.2 | **冲突预警**：派人时检测主办超载 | 中（防顾此失彼，已检测→主动告警） | 低（issue 返回 warning） | 第一批 |
| P2.3 | **调查转弹劾**：查出的线索一键转新差使 | 高（信息→行动闭环） | 低（读 report_ledger/blocker 生成新差使） | 第一批 |

## P2.1 任务依赖（设计）

- `issue_assignment(..., depends_on=[did, ...])` 参数 → 存 chain meta `depends_on`
- tick：执行中差使若 `_unmet_dependencies` 非空，**冻结进度推进**（不发 旬检定异常）；
  依赖全部 done 时解冻，各发一次 `directive_unblocked` 事件
- lifecycle_payload 暴露 `depends_on` / `blocked_by_deps`

## P2.2 冲突预警（设计）

- `issue_assignment` 时统计主办活跃差使数；≥3 在返回里带 `overload_warning`
- 不阻止下达（玩家可强派），仅告警；领旨 willingness 已因此降档

## P2.3 调查转弹劾（设计）

- `transform_investigation(db, state, directive_id, day)`：仅对 done 的 audit/secret 类差使生效
- 读其 `report_ledger` 截留记录或 `blocker_clue`（person），生成一道 audit_purge/personnel 差使，actor 指向被查出者
- 返回新差使 id；写入原差使 chain `transformed_to`

---

## P2 落地与验收（全部完成）

- [x] P2.1 依赖：`issue_assignment(depends_on=...)` 存 chain；`_unmet_dependencies` tick 冻结进度+一次性 `directive_unblocked` 事件；payload 暴露 `depends_on/deps_blocked`
- [x] P2.2 冲突预警：`issue_assignment` 返回 `overload_warning`（主办含本件≥3 时告警，不阻止）
- [x] P2.3 调查转弹劾：`transform_investigation` 自动定位对象（截留承办人/blocker_clue/显式 target），生成 audit_purge 差使，原差使标 `transformed_to`；防重复转化
- [x] API：`POST /api/postings`、`/api/postings/{id}/revoke`、`POST /api/assignments/{id}/transform`
- [x] 前端：AssignmentCard 加 `depends_on/deps_blocked` 类型 + 大厅「待前置」徽标
- [x] 硬化修复：GSAP 脉冲动画（alpha 0→0 无效 → 金色光圈放大消散）
- [x] 全量 933 测试通过；前端 build 通过
- [x] **前端 P2 UI**：
  - `AssignmentComposer` 下达器（类别/主办/限期/依赖多选）→ 下达后回显领旨 + 超载预警（P2.2）
  - `HallCard` 依赖链显示（"待前置：X、Y"，解析 id→title）+ 「据查转弹劾」按钮（done 的 audit/secret 类，P2.3）
  - 「待前置」徽标（P2.1）
  - 全栈联调验证：A→B 依赖、矿税太监 posting、transform 路由均通

## 差使系统全景（P0+P1+P2 完成）

```
下达 ──► 领旨表态(P1.1) ──► 冲突预警(P2.2)
   │           │
   │      依赖链(P2.1): B等A done ──► 解冻
   │
   ├─ 阶段里程碑(P1.4, GSAP动画)
   ├─ 期限(P1.2a) ──► 逾期自动咬人(P1.2b) ──► 玩家追责(P1.2c)
   ├─ 旬检定/账实分离/干预(既有 P0)
   ├─ 常驻差使月报(P1.5)
   ├─ 办结 ──► 功过册(P1.3) ──► 赏罚兑现六档
   └─ 调查类办结 ──► 转弹劾新差使(P2.3) ┐
                                          └─ 任务关联闭环
NPC 主动上奏(P1.6) ──► 御批 ──► 转差使(奏请获准)
密旨(独立 secret_orders 表) ──► 大厅跨表聚合
```
