# 用任务系统取代 NPC 对话清账系统

## 当前清账系统分析

### 现有表结构

| 表 | 用途 | 状态 |
|----|------|------|
| `negotiation_agreements` | NPC与皇帝的握手协议 | pending → fulfilled/failed |
| `negotiation_tasks` | 协议的待办事项 | pending → fulfilled/failed |
| `conversation_goals` | 对话目的/目标 | active → sealed/blocked/expired |

### 问题

1. **玩家看不到**：所有都是隐藏机制
2. **状态复杂**：pending/fulfilled/failed/blocked/expired...
3. **与任务系统功能重叠**：都是"让NPC做某事"
4. **难以闭环**：需要月末推演判断

---

## 迁移方案：用任务系统取代

### 核心思路

**将所有"握手协议"转换为"任务"**

```
旧系统：对话 → 握手 → 协议 → 待办 → 履约 → 清账
新系统：对话 → 任务创建 → 接受任务 → 完成目标 → 领取奖励
```

### 数据映射

| 旧概念 | 新任务系统 |
|--------|------------|
| `negotiation_agreement` | `player_quest` |
| `negotiation_tasks` | `quest_objective` |
| `conversation_goal` | `player_quest` |
| `handshake_status` | `quest.status` |
| `fulfillment_score` | `quest.progress` |

### 状态映射

| 旧状态 | 新状态 | 说明 |
|--------|--------|------|
| `pending` | `active` | 进行中 |
| `fulfilled` | `completed` | 已完成 |
| `failed` | `failed` | 已失败 |
| `blocked` | `blocked` | 被阻塞 |
| `pending_conditions` | `waiting_conditions` | 等待条件 |

---

## 具体迁移方案

### 1. 对话协议 → 任务创建

```python
# 旧代码（在对话中）
agreement = create_agreement(
    minister_name="韩爌",
    topic="查明辽饷去向",
    action_kind="policy",
    handshake_status="sealed"
)

# 新代码（在对话中）
quest = quest_manager.create_quest(
    quest_key="fetched_dialogue_liaodong_audit",
    title="查明辽饷去向",
    description="与户部确认查账方案",
    category="side",
    objective_type="dialogue_agreement",
    objective_config={
        "target_npc": "韩爌",
        "required_responses": ["同意", "愿办"],
        "min_score": 70
    },
    reward_config={
        "npc_trust": {"韩爌": 5}
    },
    source_type="dialogue_agreement",
    source_id="韩爌"
)

# 自动接受（如果握手达成）
if handshake_status == "sealed":
    quest_manager.accept_quest(
        quest_key,
        source_npc_name="韩爌",
        state=state
    )
```

### 2. 协议待办 → 任务目标

```python
# 旧代码（协议待办）
create_agreement_task(
    agreement_id=123,
    description="下旨清丈田亩",
    task_kind="policy"
)

# 新代码（任务目标）
quest_manager.update_quest_progress(
    player_quest_id=456,
    progress_delta=1,
    objective_data={
        "objective_key": "issue_directive",
        "directive": "清丈田亩"
    }
)
```

### 3. 履约检查 → 任务完成

```python
# 旧代码（月末推演检查履约）
fulfillment = check_agreement_fulfillment(agreement_id)
if fulfillment >= 0.7:
    mark_agreement_fulfilled(agreement_id)

# 新代码（任务进度更新）
# 在相关事件发生时更新进度
if event_type == "directive_issued":
    update_quest_progress(player_quest_id, progress_delta=1)

# 任务完成后自动发放奖励
if player_quest.status == "completed":
    complete_quest(player_quest_id, state)
```

---

## 集成点设计

### 1. 对话系统集成

在 `web_app.py` 的对话处理函数中：

```python
# 当对话达成握手时
if post_audit.handshake_status == "sealed":
    # 创建任务
    quest_key = f"dialogue_{minister_name}_{state.turn}"
    quest = create_quest_from_dialogue(post_audit, minister_name, state)
    
    # 自动接受
    player_quest = quest_manager.accept_quest(
        quest_key,
        source_npc_name=minister_name,
        state=state
    )
    
    # 返回任务提示
    return {
        "answer": npc_answer,
        "quest_accepted": {
            "id": player_quest.id,
            "title": quest.title,
            "objectives": quest.objective_config
        }
    }
```

### 2. 诏书系统集成

在诏书执行时更新相关任务：

```python
# 诏书执行后
def execute_directive(directive_id):
    result = issue_directive(directive_id)
    
    # 查找相关任务并更新进度
    related_quests = find_quests_by_directive(directive_id)
    for pq in related_quests:
        quest_manager.update_quest_progress(
            pq.id,
            progress_delta=1,
            turn=state.turn
        )
    
    return result
```

### 3. 月末结算集成

在月末推演中：

```python
# 检查任务过期
expired = quest_manager.check_quest_expiry(state.turn)

# 重置日常任务
reset = reset_daily_quests(state.turn)

# 返回任务相关事件
events.extend([
    {
        "kind": "quest_expired",
        "detail": f"任务「{pq.title}」已过期失败"
    }
    for pq in expired
])
```

---

## UI/UX 改进

### 对话界面显示任务

```
┌──────────────────────────────────────────────┐
│  召见：韩爌（户部尚书）                       │
├──────────────────────────────────────────────┤
│                                              │
│  韩爌："陛下若准，臣愿彻查辽饷账目。"          │
│                                              │
│  [接受并创建任务]  [拒绝]  [了解更多]         │
│                                              │
│  朕：准                                      │
│                                              │
│  【✓ 任务已创建】                             │
│  任务：查明辽饷去向                           │
│  目标：0/2 （与韩爌确认 / 收集证据）           │
│  奖励：韩爌信任+5                             │
│                                              │
└──────────────────────────────────────────────┘
```

### 任务面板替代协议列表

```
┌──────────────────────────────────────────────┐
│  任务（取代"协议"）                           │
├──────────────────────────────────────────────┤
│                                              │
│  【进行中】                                   │
│  ☐ 查明辽饷去向              与韩爌对话中      │
│  ☐ 清丈江南田亩              等待诏书执行      │
│  ☐ 招募内侍                  等待人选提名      │
│                                              │
│  【可完成】                                   │
│  ✓ 密查魏党余案              领取奖励 →         │
│                                              │
└──────────────────────────────────────────────┘
```

---

## 迁移优先级

### P0：核心映射
- [x] 任务系统数据库结构
- [ ] 对话协议 → 任务创建
- [ ] 任务进度 → 协议待办

### P1：对话集成
- [ ] 在召对中创建任务
- [ ] 任务接受/拒绝流程
- [ ] 任务进度实时更新

### P2：系统集成
- [ ] 诏书系统 → 任务进度
- [ ] 月末结算 → 任务过期
- [ ] NPC 任务提示（!/?符号）

### P3：数据迁移
- [ ] 旧数据迁移脚本
- [ ] 向后兼容处理
- [ ] 旧表废弃

---

## 优势对比

| 方面 | 旧系统（协议） | 新系统（任务） |
|------|----------------|----------------|
| 可见性 | 隐藏机制 | 玩家可见 |
| 状态 | pending/fulfilled/failed | active/completed/failed |
| UI | 无 | 完整任务面板 |
| 奖励 | 分散在推演中 | 明确显示 |
| 闭环 | 需推演判断 | 事件驱动更新 |
| 理解难度 | 高 | 低（WoW式） |

---

**总结**：用任务系统取代清账系统，让NPC对话形成可追踪、有奖励、易理解的"任务"，而不是隐藏的"协议"。
