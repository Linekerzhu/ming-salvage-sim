# 召对模块 LLM 语义引擎算法优化

**Goal:** 通过三层优化将每轮 chat 的 DB 查询从 60-90 次降至 9-18 次，LLM 串行调用从 7-10 次降至 4-6 次，并消除微优化层面的重复计算。

**Architecture:**
1. `_context_payload` 单轮 memoization（按 db identity + character.name + state.turn 缓存）
2. 合并 `evaluate_route` + `evaluate_action_probe` 为单次 LLM；合并 `evaluate_post_chat` 5 类为单次 LLM
3. 模块级 `re.compile`、去重复 `_compact`、缓存 `accepted_task_risk_profile`

## 实施记录

| Task | Commit | 描述 |
|---|---|---|
| 1 | `99c5c53` | 基线测试（red）：`_context_payload` memoization 断言 |
| 2 | `7257687` | Layer 1：`_CONTEXT_CACHE` + `_clear_context_cache`（timeflow 跨 tick 清） |
| 3 | `e8379b8` | Layer 3：模块级 re.compile + `_cached_risk_profile` + walrus dedup `_compact` |
| 4 | `ef2bd6b` | Layer 2-A：`dialogue_combined_intent_audit` 合并 route + action_probe |
| 5 | `b0f6d47` | Layer 2-B：`dialogue_combined_post_audit` 合并 5 类 post_chat |
| 6 | (本 commit) | `id(db)` 加入 cache key 防跨测试串读 + plan doc |

## 性能收益

- **DB 查询**：同 character 同 turn 的 7-10 次 `_context_payload` 调用从每次 9+ 次查询降至 0 次（缓存命中）→ 每轮 60-90 次 → 9-18 次
- **LLM 调用**：route + action_probe 从 2 次降至 1 次（生产路径）；post_chat 5 类合并为 1 次
- **微优化**：4 个 `re.sub` 不再每次编译；`_compact` 不再双调用；`accepted_task_risk_profile` 不再双算

## 兼容性

- audit_client 注入的测试走原有串行回退（fake 通常只响应单一 phase）
- `_context_payload` cache key 含 `id(db)` 防止不同 GameDB 实例（测试场景）串读
- `_clear_context_cache` 在 timeflow `_tick_day` 顶部清空，确保跨 tick 读最新 context

## 验证

- pytest 1018/1018 通过
- tsc --noEmit exit=0
