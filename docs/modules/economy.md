# 经济模块

经济模块追踪皇帝真正关心的账：国库有没有钱，内库能不能救急，本月收了多少，花了多少，欠了多少。所有预算口径统一为**月度万两**。

国库、内库不是 0-100 状态条，而是实际钱粮整数，单位**万两**。全局 0-100 局势量表只有民心、皇威等抽象指标；边防压力、动乱、士绅阻力等落在各省 / 各军队字段里。

---

## 核心账户

| 账户 | 说明 |
|------|------|
| `国库` | 朝廷公开财政，军饷、赈灾、官俸、工程均从此出 |
| `内库` | 皇帝私库，适合救急和密支，玩家可主动挪用补国库 |

## 玩家读账模型

财政面板必须先回答三件事：

| 问题 | 数据来源 | 玩家读法 |
|------|----------|----------|
| 怎么赚钱 | `revenue_family_rows` / `province_tax_rows` | 国库靠省份田赋、辽饷、盐税、商税；内库靠皇庄、织造、矿税、建筑产出 |
| 钱花在哪 | `expense_family_rows` / `army_pay_rows` | 国库花在军饷、宗室、官俸、工部、赈灾、建筑；内库花在宫廷、内廷、妃嫔、恩赏、建筑 |
| 余额为什么变了 | `ledger_summary` / `ledger_movements` | 月结税收、俸禄、军饷和一次性拨款都会落 `economy_ledger`，只有流水能解释余额刚刚为什么增减 |

这三者不能混用：

- `compute_budget_lines()` 回答“按当前制度，下月自然会收多少、花多少”。
- `economy_ledger` 回答“余额刚刚因为哪一笔收入/支出变了”。
- `province_tax_rows` 回答“国库税收为什么没有按账面税基足额到账”。
- `armies.arrears` 回答“国库没钱时，哪些军饷没有真的发出去”。
- `economy_ledger.category='期初'` 只是开局底账，不计入“近期流水净变动”。

---

## 唯一预算入口

`ming_sim.flows.compute_budget_lines(db, state)` 是预算唯一计算入口。月结、首页预算、财政中枢都必须复用它，禁止各模块自行重算收入/支出。

`GET /api/fiscal_center` 在此基础上提供可解释账簿：

- `revenue_sources`：月收入来源，按国库/内库列出。
- `expense_sources`：月支出来源，按国库/内库列出。
- `revenue_family_rows`：玩家可读税源拆账，把 `田赋辽饷盐商` 拆成田赋、辽饷、盐税、商税，并列出皇庄、织造、矿税等固定收入。
- `expense_family_rows`：玩家可读支出分组，给每个支出项说明为什么会花钱。
- `province_tax_rows`：每省税基、田赋、辽饷、盐税、商税、到账率和影响因子。
- `army_pay_rows`：各军月饷、欠饷、士气、自专风险。
- `net_by_account`：国库/内库余额、月入、月支、月净、下月现金缺口。
- `account_cards`：国库/内库账户摘要，含余额、月入、月支、月净、预计余额和头部收支项。
- `ledger_movements`：最近经济流水；一次性拨款、抄没、赈济、补饷必须进此账。
- `ledger_summary`：过滤期初账后的近期流水摘要，用来解释余额变化。
- `policy_modifiers`：已成正统国策或其他遗产对财政的百分比修正。
- `money_questions`：财政三问的直接答案，供国策页第一屏展示。
- `player_model`：财政读法约束，说明月预算、流水、国库/内库、欠饷的边界。

`GET /api/statecraft_center` 再把财政账簿与官僚组织诊断合并成国家机器面板。财政中心回答“钱本身怎么流”，国家机器回答“钱、建筑、衙门和承办产能合起来为什么让国家跑得动或跑不动”。

---

## 省级财政模型

每省 `regions` 表存以下字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `tax_per_turn` | INTEGER | 省级月税基准，含田赋+辽饷+盐税+商税合计 |
| `gentry_resistance` | INTEGER 0-100 | 士绅阻力，压低到账率 |
| `unrest` | INTEGER 0-100 | 民变压力，压低到账率 |
| `fiscal` | JSON | 税种细分 + 腐败度 |

`fiscal` JSON 字段：

| key | 单位 | 说明 |
|-----|------|------|
| `guan_min_tian` | 万亩 | 官民田，背景税基 |
| `wang_tian` | 万亩 | 藩王庄田，免税；没收后转皇庄 |
| `huang_tian` | 万亩 | 皇庄增量田亩 |
| `liao_xiang` | 万两/月 | 辽饷月摊派额 |
| `salt_tax` | 万两/月 | 盐税月基数 |
| `commerce_tax` | 万两/月 | 商税月基数 |
| `corruption` | 0-100 | 腐败度，压低到账率 |

### 月结算公式（`calc_province_fiscal`）

```
base_efficiency =
  1
  - gentry_resistance / 100 * 0.55
  - corruption / 100 * 0.45
  - max(0, unrest - 20) / 100 * 0.30

shi_factor = clamp(0.85 + 势 / 333, 0.85, 1.15)
efficiency = clamp(base_efficiency * shi_factor, 0.05, 1.00)

liao_efficiency = clamp(efficiency * (0.5 + 皇威 / 200), 0.10, 1.00)
```

```
田赋基数 = max(0, tax_per_turn - liao_xiang - salt_tax - commerce_tax)

田赋月收 = 田赋基数 * efficiency       → 国库
辽饷月收 = liao_xiang * liao_efficiency → 国库
盐税月收 = salt_tax * efficiency        → 国库
商税月收 = commerce_tax * efficiency    → 国库
```

皇庄开局基准不从省份重复计算，而走 `content/fiscal_config.json` 的 `皇庄_base`。`huang_tian` 用于记录没收藩王庄田后的增量。

---

## 月度固定收支（`content/fiscal_config.json`）

`fiscal_config` 的 `base` 全是**月度万两**，`rate` 为百分比。固定项由 `db.iter_budget_items()` 给 `compute_budget_lines()` 遍历；动态税项（田赋、辽饷、盐税、商税、皇庄）按各自专路计算。

### 国库收入

| 项目 | 来源 |
|------|------|
| 田赋、辽饷、盐税、商税 | 各省 `regions.tax_per_turn` 与 `regions.fiscal`，经 `calc_province_fiscal()` 折算 |

### 内库收入

| 项目 | 月度基数 | rate | 说明 |
|------|----------|------|------|
| 皇庄 | 20 | 100% | 皇庄地租基准 |
| 织造 | 12 | 100% | 苏杭织造局上缴 |
| 矿税 | 3 | 100% | 矿税残余 |

### 国库支出

| 项目 | 月度基数 | rate | 月额 | 说明 |
|------|----------|------|------|------|
| 宗室禄米 | 120 | 55% | 66 | 诸藩宗室禄米，削藩可降 |
| 百官俸禄 | 25 | 100% | 25 | 在京百官俸禄，含地方折色 |
| 工部 | 5 | 100% | 5 | 工部日常维护 |
| 赈灾备用 | 5 | 100% | 5 | 制度性赈灾备用 |
| 各军军饷 | 动态 | — | `SUM(armies.maintenance_per_turn)` | 按明军当前维护费计算 |
| 建筑维护 | 动态 | — | `SUM(buildings.maintenance)` | 内廷建筑扣内库，其余扣国库 |

### 内库支出

| 项目 | 月度基数 | rate | 月额 |
|------|----------|------|------|
| 宫廷开支 | 7 | 100% | 7 |
| 内廷俸禄 | 5 | 100% | 5 |
| 妃嫔供奉 | 3 | 100% | 3 |
| 恩赏赍予 | 12 | 100% | 12 |
| 内廷建筑维护 | 动态 | — | 由建筑表计算 |

---

## 税制变更纪律

- 调辽饷、盐税、商税：必须同步修改 `regions.fiscal` 对应字段。
- 调田赋：必须修改 `regions.tax_per_turn` 中的田赋残差。
- 皇庄、织造、矿税等固定项：走 `content/fiscal_config.json` / `fiscal_config` 表。
- 一次性拨款、抄没、赈济、补饷：必须进入 `economy_ledger`，不能只改余额。
- `apply_dynamic_fiscal_scale()` 和 `scale_tian_fu()` 是动态税制调整的受控入口。

---

## 代码位置

| 功能 | 文件 | 函数/位置 |
|------|------|---------|
| 省级月收计算 | `ming_sim/flows.py` | `calc_province_fiscal` |
| 预算唯一入口 | `ming_sim/flows.py` | `compute_budget_lines` |
| 财政中枢 payload | `ming_sim/fiscal_center.py` | `fiscal_center_payload` |
| 月度财政 tick | `ming_sim/flows.py` | `apply_fixed_period_flows` |
| 腐败度 delta 落库 | `ming_sim/db.py` | `apply_region_deltas` → `FISCAL_SCORE_FIELDS` |
| 动态税同步 | `ming_sim/db.py` | `apply_dynamic_fiscal_scale` / `scale_tian_fu` |
| fiscal_config 初始值 | `content/fiscal_config.json` | 月度万两配置 |
