# 大明游戏任务系统重新设计

> 目标：设计一个堪比魔兽世界的任务系统，清晰、直观、有叙事深度。

## 一、魔兽世界任务系统核心要素

### 1.1 任务符号系统
- **!**：可接受任务
- **?**：可完成任务
- **灰色**：低级任务

### 1.2 任务分类
- **主线任务**（Campaign）：推动剧情主线
- **支线任务**（Side）：角色故事、世界观补充
- **日常任务**（Daily）：可重复
- **精英任务**（Elite）：高难度、高奖励
- **稀有任务**（Rare）：限时/特殊条件

### 1.3 任务目标类型
- 杀死特定数量敌人
- 收集特定物品
- 与特定NPC对话
- 探索特定区域
- 使用特定物品
- 护送NPC

### 1.4 任务链设计
- 前置任务 → 后续任务
- 分支选择
- 任务线终结

### 1.5 奖励系统
- 经验值
- 金币
- 物品
- 声望
- 解锁内容

### 1.6 追踪显示
- 始终可见的任务列表
- 进度显示（X/Y）
- 任务描述和目标

---

## 二、大明游戏任务系统映射

### 2.1 任务符号（UI设计）
```
召见大臣时：
- 【！】有新任务提议
- 【？】有任务可完成
- 【…】有任务进行中
```

### 2.2 任务分类（大明语境）

| 分类 | 对应大明概念 | 示例 |
|------|--------------|------|
| **主线** | 朝局危机、重大事件 | "辽东军饷危机"、"流寇逼近京师" |
| **支线** | 人物故事、地方事务 | "韩爌的家族恩怨"、"江南灾民安置" |
| **日常** | 月度例行事务 | "月度财政审计"、"军队点验" |
| **精英** | 高难度改革 | "清丈江南田亩"、"整顿漕运" |
| **稀有** | 限时/特殊条件 | "科举取士"、"平叛机会" |

### 2.3 任务目标类型（大明版）

| 目标类型 | 说明 | 示例 |
|----------|------|------|
| **对话确认** | 与NPC达成协议 | "与韩爌确认查账方案" |
| **下诏执行** | 颁布相关诏书 | "下旨清丈田亩" |
| **等待时日** | 等待时间推进 | "等待三月观察效果" |
| **收集证据** | 通过密令获取 | "获取魏忠贤余党名单" |
| **人事任免** | 任命/罢黜官员 | "任命张宗衡为兵部督师" |
| **调停矛盾** | 解决派系冲突 | "调停东林与阉党矛盾" |
| **资源调配** | 分配钱粮人手 | "调拨十万两赈灾" |

### 2.4 奖励系统（大明语境）

| 奖励类型 | 说明 |
|----------|------|
| **皇威** | 成功执行圣旨提高皇威 |
| **民心** | 善政提高民心 |
| **派系满意度** | 满足特定派系 |
| **人物信任** | 提高特定NPC的信任 |
| **解锁内容** | 新的奏对选项、新的任免可能 |
| **物品** | 独特道具、情报 |

---

## 三、数据模型设计

### 3.1 任务表（quests）

```sql
CREATE TABLE quests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quest_key TEXT UNIQUE NOT NULL,           -- 任务唯一标识
    title TEXT NOT NULL,                       -- 任务标题
    description TEXT,                          -- 任务描述（故事背景）
    category TEXT NOT NULL,                    -- 主线/支线/日常/精英/稀有
    tier INTEGER DEFAULT 1,                    -- 任务层级（1=地方, 2=六部, 3=军国）

    -- 任务目标
    objective_type TEXT NOT NULL,              -- 目标类型
    objective_config TEXT NOT NULL,             -- 目标配置（JSON）

    -- 奖励
    reward_config TEXT,                        -- 奖励配置（JSON）

    -- 前置关系
    prerequisite_quest_keys TEXT,              -- 前置任务keys（JSON数组）
    exclusive_of_quest_keys TEXT,              -- 互斥任务keys

    -- 限制条件
    min_turn INTEGER,                          -- 最早开始回合
    max_turn INTEGER,                          -- 最晚开始回合
    required_faction_satisfaction REAL,        -- 所需派系满意度
    required_imperial_power REAL,             -- 所需皇威

    -- 刷新规则
    is_repeatable BOOLEAN DEFAULT FALSE,      -- 是否可重复
    repeat_interval_turns INTEGER DEFAULT 0,   -- 重复间隔回合
    daily_reset BOOLEAN DEFAULT FALSE,         -- 是否每日重置

    -- 元数据
    source_type TEXT,                          -- 任务来源类型
    source_id TEXT,                            -- 任务来源ID
    created_at INTEGER DEFAULT (strftime('%s', 'now'))
);
```

### 3.2 玩家任务进度表（player_quests）

```sql
CREATE TABLE player_quests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quest_key TEXT NOT NULL,                   -- 关联quests表
    player_id INTEGER DEFAULT 1,               -- 玩家ID（预留多人）

    -- 状态
    status TEXT NOT NULL DEFAULT 'available',  -- available/active/completed/failed/cancelled

    -- 进度
    progress_current INTEGER DEFAULT 0,        -- 当前进度
    progress_target INTEGER DEFAULT 1,         -- 目标进度
    objective_data TEXT,                       -- 目标数据（JSON，动态追踪）

    -- 时间
    accepted_turn INTEGER,                     -- 接受回合
    completed_turn INTEGER,                    -- 完成回合
    expires_turn INTEGER,                      -- 过期回合
    last_progress_turn INTEGER,                -- 上次进度更新回合

    -- 关联
    source_npc_name TEXT,                      -- 发布任务的NPC
    target_npc_name TEXT,                      -- 需要交互的NPC
    related_issue_id INTEGER,                  -- 关联事项ID

    -- 奖励领取
    reward_claimed BOOLEAN DEFAULT FALSE,      -- 是否已领取奖励

    FOREIGN KEY (quest_key) REFERENCES quests(quest_key)
);
```

### 3.3 任务目标配置示例（objective_config JSON）

```json
// 对话确认类
{
  "type": "dialogue_agreement",
  "target_npc": "韩爌",
  "required_responses": ["同意", "愿办"],
  "min_score": 70,
  "dialogue_keywords": ["查账", "辽饷"]
}

// 下诏执行类
{
  "type": "issue_directive",
  "directive_templates": ["清丈田亩", "调查税源"],
  "target_regions": ["江南"],
  "required_resources": {"银两": 50000, "人手": 100}
}

// 等待时日类
{
  "type": "wait_turns",
  "turns": 3,
  "check_condition": "无民变爆发"
}

// 收集证据类
{
  "type": "collect_evidence",
  "evidence_types": ["账册", "口供", "密报"],
  "sources": ["密令", "厂卫", "都察院"]
}

// 人事任免类
{
  "type": "personnel_change",
  "action": "appoint",
  "target_office": "兵部督师",
  "candidate_name": "张宗衡"
}
```

---

## 四、任务流程设计

### 4.1 任务生命周期

```
┌─────────────────────────────────────────────────────────┐
│                    任务生命周期                          │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  [隐藏]    [可用]    [进行中]    [可完成]    [已完成]     │
│    │         │          │           │          │         │
│    │         │          │           │          │         │
│  前置     条件满足    玩家接受    进度达标    玩家确认    │
│  未完成   自动出现    开始追踪    可交付     领取奖励    │
│            可手动    可执行     可交付      任务关闭    │
│            忽略      可放弃                          │
│                     可追踪                           │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

### 4.2 任务触发方式

| 触发方式 | 说明 | 示例 |
|----------|------|------|
| **自动触发** | 条件满足自动可用 | 回合到达1629.04 → "辽东危机"任务出现 |
| **NPC提议** | 召对时NPC提出 | 韩爌："陛下若准，臣愿查辽饷" |
| **事件触发** | 特定事件解锁 | 民变爆发 → "赈灾"任务解锁 |
| **玩家主动** | 玩家发起目标 | 玩家："朕要清丈田亩" → 创建任务 |
| **前置完成** | 完成前置任务 | "调查贪腐"完成 → "惩治贪官"解锁 |

### 4.3 任务执行方式

| 执行方式 | 说明 | 示例 |
|----------|------|------|
| **对话推进** | 通过召对大臣推进 | 与韩爌确认查账方案 |
| **下诏推进** | 通过颁诏推进 | 颁布清丈田亩诏书 |
| **等待推进** | 时间自动推进 | 等待三月观察效果 |
| **密令推进** | 通过密令获取证据 | 派锦衣卫暗查 |
| **人事推进** | 通过任免推进 | 任命张宗衡为兵部督师 |

### 4.4 任务完成判定

```python
# 任务完成判定逻辑
def check_quest_completion(player_quest: dict, game_state: GameState) -> bool:
    objective_type = player_quest['objective_type']
    objective_config = json.loads(player_quest['objective_config'])

    if objective_type == 'dialogue_agreement':
        # 检查是否与NPC达成协议
        return has_agreement(game_state, objective_config['target_npc'])

    elif objective_type == 'issue_directive':
        # 检查是否已下相关诏书
        return has_directive(game_state, objective_config['directive_templates'])

    elif objective_type == 'wait_turns':
        # 检查是否已过足够回合且条件满足
        turns_passed = game_state.turn - player_quest['accepted_turn']
        return turns_passed >= objective_config['turns']

    elif objective_type == 'collect_evidence':
        # 检查是否收集到足够证据
        return count_evidence(game_state, objective_config['evidence_types']) >= 3

    # ... 其他类型
```

---

## 五、UI/UX设计

### 5.1 任务列表界面

```
┌────────────────────────────────────────────────────────┐
│                        任务                             │
├────────────────────────────────────────────────────────┤
│                                                         │
│  【主线】○ 辽东军饷危机                           [新]  │
│    关东将士嗷嗷待哺，急需查明军饷去向                   │
│    进度：0/2 (与户部确认 / 查明去向)                    │
│    来源：袁崇焕奏对 (1629.04)                           │
│    奖励：皇威+5, 袁崇焕信任+10                          │
│                                                         │
│  【支线】○ 江南士绅欠税                              [！]│
│    据报江南士绅大量隐匿田产，逃避税赋                   │
│    进度：等待接受                                       │
│    来源：事件触发                                       │
│    奖励：解锁清丈田亩诏书                               │
│                                                         │
│  【日常】✓ 本月财政审计                              [？]│
│    例行核查各部门财政状况                               │
│    进度：2/2 (户部/工部已核查)                          │
│    可交付 → 奖励：民心+2                                │
│                                                         │
└────────────────────────────────────────────────────────┘
```

### 5.2 召对界面提示

```
┌────────────────────────────────────────────────────────┐
│  召见：韩爌（户部尚书）                                  │
├────────────────────────────────────────────────────────┤
│                                                         │
│  【韩爌的提议】                                         │
│                                                         │
│  ！ 有新任务：查明辽饷去向                              │
│    "陛下若准，臣愿彻查辽饷账目，追回被贪墨的银两"        │
│    [接受任务] [暂不考虑] [详情]                         │
│                                                         │
│  ？ 可完成任务：密令调查已就绪                          │
│    "锦衣卫已将账册呈上，请陛下过目"                      │
│    [查看证据] [完成并奖励]                               │
│                                                         │
│  … 进行中：清丈江南田亩                                │
│    "清丈工作进行中，还需等待三月方可完成"                │
│    [查看进度]                                           │
│                                                         │
└────────────────────────────────────────────────────────┘
```

### 5.3 任务详情界面

```
┌────────────────────────────────────────────────────────┐
│  任务：查明辽饷去向                         [主线任务]  │
├────────────────────────────────────────────────────────┤
│                                                         │
│  【任务描述】                                           │
│  辽东前线传来急报，将士已三月未领军饷，军心不稳。据查，   │
│  军饷在发放过程中被层层截留，最终抵达前线不足三成。需    │
│  彻查此事，追回被贪墨的银两，以安军心。                 │
│                                                         │
│  【任务目标】                                           │
│  ☐ 1. 与户部尚书韩爌确认查账方案                        │
│  ☐ 2. 查明军饷去向（需要证据）                          │
│                                                         │
│  【奖励】                                               │
│  • 皇威 +5                                             │
│  • 韩爌信任 +10                                         │
│  • 袁崇焕信任 +15                                       │
│  • 解锁：惩治贪官任务链                                 │
│                                                         │
│  【相关信息】                                           │
│  • 前置：无                                             │
│  • 后续：惩治贪官 → 追回赃款 → 重整辽东                 │
│  • 关联人物：韩爌、袁崇焕、锦衣卫                        │
│  • 截止：1629.06（逾期任务失败）                        │
│                                                         │
│  [放弃任务]                                             │
│                                                         │
└────────────────────────────────────────────────────────┘
```

---

## 六、与现有系统的兼容

### 6.1 现有系统映射

| 现有概念 | 新任务系统 |
|----------|------------|
| conversation_goal | player_quest |
| agreement | quest objective |
| conditions | quest objectives |
| obligations | daily quests |
| issues | quest sources |

### 6.2 迁移策略

1. **第一阶段**：创建新表，与现有系统并存
2. **第二阶段**：新任务使用新系统，旧任务保持不变
3. **第三阶段**：逐步迁移旧任务到新系统
4. **第四阶段**：废弃旧系统

### 6.3 API 设计

```python
# 任务创建
quest = create_quest(
    quest_key="liaodong_payroll_crisis",
    title="辽东军饷危机",
    category="主线",
    objective_type="dialogue_agreement",
    objective_config={...},
    reward_config={...}
)

# 任务接受
player_quest = accept_quest(quest_key="liaodong_payroll_crisis")

# 任务进度更新
update_quest_progress(player_quest_id, progress_delta=1)

# 任务完成
complete_quest(player_quest_id)

# 任务放弃
abandon_quest(player_quest_id)

# 查询可用任务
available_quests = get_available_quests()

# 查询进行中任务
active_quests = get_active_player_quests()
```

---

## 七、实现优先级

### P0（核心功能）
- [ ] 任务数据表创建
- [ ] 任务配置文件格式
- [ ] 任务创建/接受/放弃逻辑
- [ ] 简单对话确认类任务

### P1（基础功能）
- [ ] 任务进度追踪
- [ ] 任务完成判定
- [ ] 任务奖励发放
- [ ] 任务列表UI

### P2（扩展功能）
- [ ] 下诏执行类任务
- [ ] 密令调查类任务
- [ ] 等待时日类任务
- [ ] 人事任免类任务

### P3（高级功能）
- [ ] 任务链设计
- [ ] 分支选择
- [ ] 日常任务刷新
- [ ] 任务失败处理

---

## 八、参考示例

### 8.1 主线任务链设计

```
【辽东危机任务链】

1. 辽东军饷危机（主线）
   └─ 目标：与户部确认查账方案
   └─ 奖励：解锁后续任务

   2. 查明军饷去向（主线）
      ├─ 目标：收集证据（密令/奏对）
      └─ 奖励：发现贪腐线索

      3. 惩治贪官（主线）
         ├─ 目标：下诏逮捕贪官
         └─ 奖励：追回赃款

         4. 重整辽东（主线）
            ├─ 目标：补发军饷
            └─ 奖励：辽东军心稳定
```

### 8.2 支线任务设计

```
【韩爌的家族恩怨】

触发：韩爌主动提及
目标：调停韩爌与魏党矛盾
奖励：韩爌忠诚度提升
影响：解锁韩爌的隐秘情报
```

---

*本文档持续更新中*
