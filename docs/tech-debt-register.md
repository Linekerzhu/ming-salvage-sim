## Technical Debt Register
Last updated: 2026-06-27
Total items: 11 | Estimated total effort: ~2 L + 4 M + 5 S

> 战略背景：`architecture_inventory.md` 要求把功能压缩到 5 个一级入口（御前/御案/召对/诏旨/国策）。
> 本清单按该目标加权排序。生成自 `/ccgs-tech-debt scan`。

| ID | Category | Description | Files | Effort | Impact | Priority | Added |
|----|----------|-------------|-------|--------|--------|----------|-------|
| TD-001 | Architecture | ~~5 个 `quest_*` 遗留模块仍在仓库（死代码）~~ **误报，已否决**：经核实 `quest_db.apply_quest_schema` 是 `player_quests`/`quests` 表的 schema 来源，被 `assignment.py`（生产）+ 6 个测试文件活跃引用；`quest_api.register_quest_routes` 仍在 `web_app.py` 注册。删除会断构建。正确归属：归入 召对/诏旨 子系统或正式登记为第 17 子系统。 | ming_sim/quest_{api,db,loader,manager,refresh}.py | — | — | — | 2026-06-27 |
| TD-002 | Dependency | 全部 7 个依赖用 `>=` 无上限，0 个 `==` 锁定；`pip install` 可能拉到不兼容大版本 | requirements.txt | S | Med | 2 | 2026-06-27 |
| TD-003 | Architecture | God module：`db.py` = 8701 行，远超 500 行阈值，全项目最大单体，改动风险大、难单测 | ming_sim/db.py | L | High | 3 | 2026-06-27 |
| TD-004 | Architecture | `assignment.py` 残留 2 个 P1 TODO（posting 常驻差使 lead/exec 时序、密旨入口收敛） | ming_sim/assignment.py:122,1410-11 | M | Med | 4 | 2026-06-27 |
| TD-005 | Architecture | 48 个模块无对应 `tests/test_*` 文件（含 session/db/policies/issues 等核心模块，由 test_audit_integration.py 间接覆盖，但无直接单测） | 见 scan 列表 | M | Med | 5 | 2026-06-27 |
| TD-006 | Code Quality | 9 个模块 >2000 行（db 8701 / playstyle 4968 / eunuch_lore 3698 / court_events 3238 / dialogue_audit 2974 / session 2771 / context 2271 / lifecycle 2195 / issues 2126），复杂度集中 | ming_sim/*.py | L | Med | 6 | 2026-06-27 |
| TD-007 | Documentation | `architecture_inventory.md` 记录 74 后端模块，实际 90（+16 未登记，drift） | architecture_inventory.md | S | Low | 7 | 2026-06-27 |
| TD-008 | Code Quality | assignment.py 含 3 处 TODO/FIXME（全项目唯一含 TODO 的文件，整体纪律好） | ming_sim/assignment.py | S | Low | 8 | 2026-06-27 |
| TD-009 | Test Debt | 90 模块 / 59 测试文件，名义覆盖率 ~66%；核心模块无直接单测文件 | tests/ | M | Med | 9 | 2026-06-27 |
| TD-010 | Performance | dialogue_audit 2974 行、session 2771 行，per-turn LLM 审计热路径集中于此；已做合并优化但仍有进一步拆分空间 | ming_sim/dialogue_audit.py, session.py | M | Med | 10 | 2026-06-27 |
| TD-011 | Code Quality | eunuch_lore.py 3698 行、playstyle.py 4968 行 多为数据/lore，可考虑外迁到 content/ JSON | ming_sim/eunuch_lore.py, playstyle.py | M | Low | 11 | 2026-06-27 |

### 战略优先级说明（按"压缩到 5 入口"目标加权）

1. **TD-001**（删 quest_* 遗留）— effort S，直接减少模块数，无行为风险
2. **TD-002**（依赖加上限/lockfile）— effort S，防止未来构建断裂
3. **TD-003**（拆 db.py）— effort L，但风险最高，需谨慎规划
4. **TD-004**（清 assignment TODO）— effort M，清 P1 悬留
5. **TD-005**（无父级模块映射）— effort M，先文档后合并，支撑 5 入口收敛

### 规则备忘
- 技术债务是工具，不是罪。本清单追踪的是有意识的决定。
- 每条都应能回答"当初为什么接受"（截止日、原型、缺信息）。
- 每个工作周期至少 `scan` 一次。
- 超过 ~1 个月未处理的条目，要么修，要么有书面理由接受。
