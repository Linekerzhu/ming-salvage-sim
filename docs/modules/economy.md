# 经济模块

经济模块追踪皇帝真正关心的账：国库有没有钱，内库能不能救急，本月收了多少，花了多少，欠了多少。

国库、内库不是 0-100 状态条，而是实际钱粮整数，单位**万两**。全局 0-100 局势量表只有民心、皇威两项；边防压力、动乱、士绅阻力等落在各省 / 各军队字段里。

---

## 核心账户

| 账户 | 说明 |
|------|------|
| `国库` | 朝廷公开财政，军饷/赈灾/官俸/工程均从此出 |
| `内库` | 皇帝私库，适合救急和密支，玩家可主动挪用补国库 |

---

## 省级财政模型

### 数据来源

每省 `regions` 表存以下字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `tax_per_turn` | INTEGER | 省级月税基准（万两），含田赋+辽饷+盐税+商税合计 |
| `gentry_resistance` | INTEGER 0-100 | 士绅阻力，影响实收率 |
| `unrest` | INTEGER 0-100 | 民变压力，影响实收率和解运比 |
| `fiscal` | JSON | 税种细分 + 腐败度，见下表 |

`fiscal` JSON 字段说明：

| key | 单位 | 说明 |
|-----|------|------|
| `guan_min_tian` | 万亩 | 官民田，田赋→国库 |
| `wang_tian` | 万亩 | 藩王庄田，免税；没收后转皇庄 |
| `huang_tian` | 万亩 | 皇庄，仅北直隶有，地租→内库 |
| `liao_xiang` | 万两/月 | 辽饷月摊派额 |
| `salt_tax` | 万两/月 | 盐税月基数，产盐省才>0 |
| `commerce_tax` | 万两/月 | 商税月基数 |
| `corruption` | 0-100 | 腐败度，影响解运比 |

---

### 月结算公式（`calc_province_fiscal`）

#### 第一步：系数计算

```
# 实收率：士绅阻力 + 民变决定能征到多少
collection_rate = 1.0
               - gentry_resistance / 200
               - max(0, unrest - 20) / 250
# 钳到 [0.30, 1.00]

# 解运比：腐败度 + 民变决定税银实际到账比例
transport_ratio = 0.95
               - corruption / 200
               - max(0, unrest - 30) / 300
# 钳到 [0.35, 0.92]

# 辽饷解运比：额外受皇威影响（皇威低→地方截留多）
liao_ratio = transport_ratio × (0.5 + 皇威 / 200)
# 钳到 [0.30, 0.95]
```

#### 第二步：各税源

```
# 税种拆分：从 tax_per_turn 扣除辽饷/盐税/商税固定额，剩余为田赋基数
田赋基数 = max(0, tax_per_turn - liao_xiang - salt_tax - commerce_tax)

田赋月收 = 田赋基数 × collection_rate × transport_ratio  → 国库
辽饷月收 = liao_xiang × liao_ratio                       → 国库
盐税月收 = salt_tax × transport_ratio                     → 国库
商税月收 = commerce_tax × transport_ratio                 → 国库

皇庄月收 = huang_tian × 0.57（万两/万亩/月）             → 内库
           （仅北直隶 huang_tian>0；0.57 = 20万两/月 ÷ 35万亩基准）
```

#### 第三步：全国汇总

```
国库月收 = Σ(各省 田赋 + 辽饷 + 盐税 + 商税)
内库月收 = Σ(各省 皇庄收入) + fiscal_config 皇庄_base
```

> 注：内库皇庄基准走 `fiscal_config.皇庄_base`（已校准=20万/月）；`huang_tian` 字段用于没收藩王庄田后的**增量**计算。

---

### 典型数值示例

| 省份 | corruption | gentry_resistance | unrest | transport_ratio | collection_rate | 月收（估） |
|------|-----------|------------------|--------|----------------|----------------|----------|
| 南直隶 | 44 | 85 | 25 | 0.73 | 0.56 | ~51万 |
| 浙江 | 38 | 78 | 22 | 0.76 | 0.60 | ~46万 |
| 陕西 | 72 | 42 | 78 | 0.43 | 0.51 | ~10万 |
| 北直隶 | 62 | 55 | 35 | 0.62 | 0.67 | ~22万 |

全国月收合计约 **398万两**（开局基准）。

---

### 动态变化

**收入减少的路径：**
- 清查/抄家诏书 → `gentry_resistance` 短期暴涨 → `collection_rate` 下降 → 田赋减少
- 灾荒事件 → `unrest` 上升 → 双系数同时下降
- 皇威低落 → `liao_ratio` 下降 → 辽饷截留增加

**收入增加的路径：**
- issue「清丈田亩」推进到高 bar → `gentry_resistance` 下降 → 实收率提升
- 整治贪腐成功 → `corruption` 下降 → 解运比提升
- 没收藩王庄田 → `wang_tian` 转 `huang_tian` → 内库增量
- 诏书增商税/盐税 → `salt_tax`/`commerce_tax` 基数提升

---

## 固定月度支出（`fiscal_config`）

所有 base 为**季度额**，`monthly_amount(base × rate / 100)` = 月额（约 ÷3）。

### 国库支出

| 项目 | base（季） | rate | 月额（估） | 说明 |
|------|-----------|------|----------|------|
| 宗室禄米 | 360 | 70% | ~84万 | 最大包袱，削藩可降 |
| 九边补给 | 270 | 90% | ~81万 | 九边粮草，非军饷 |
| 各军军饷 | — | — | ~150万 | 按优先级逐军发放 |
| 百官俸禄 | 90 | 100% | ~30万 | 含地方折色 |
| 建筑维护 | — | — | ~75万 | 各省建筑月维护 |
| 赈灾备用 | 15 | 100% | ~5万 | 制度性预留 |
| 工部 | 15 | 100% | ~5万 | 工部日常 |

**月支出合计约 430万两**

### 内库支出

| 项目 | base（季） | rate | 月额 |
|------|-----------|------|------|
| 宫廷开支 | 22 | 100% | ~7万 |
| 内廷俸禄 | 15 | 100% | ~5万 |
| 妃嫔供奉 | 10 | 100% | ~3万 |

### 内库收入

| 项目 | base（季） | rate | 月额 |
|------|-----------|------|------|
| 皇庄 | 60 | 100% | ~20万 |
| 织造 | 35 | 100% | ~12万 |
| 矿税 | 10 | 100% | ~3万 |

---

## 开局月净

| 账户 | 月收入 | 月支出 | 月净 |
|------|--------|--------|------|
| 国库 | ~398 | ~430 | **~-32** |
| 内库 | ~35 | ~15 | **~+20** |

国库持续亏损，逼玩家开源节流。内库正向积累，作为救急储备。

---

## 腐败度（`corruption`）

- 存储位置：`regions.fiscal` JSON，key=`corruption`，0-100
- **读取**：`simulation.py` 把 `json_extract(fiscal,'$.corruption')` 喂进推演 payload
- **写入**：`apply_region_deltas` 识别 `FISCAL_SCORE_FIELDS`，解析 JSON → patch → 写回
- **LLM 触发条件**（`score_extractor.md`）：整治贪腐/巡按/抄家/杀士绅头领 → 负值 ±5~±20；放任失控 → 正值

---

## 藩王庄田没收

```
execution_evaluator 输出：
  region_delta: {"henan": {"wang_tian_transfer": 40, "reason": "查抄福王庄田"}}

apply 时：
  wang_tian -= 40
  huang_tian += 40
  内库月增量 += 40 × 0.57 = 23万两/月（持续）
  同时：gentry_resistance +15，党争压力上升
```

河南（福王）、湖广（楚王）、山西（晋王）是主要藩王省份。

---

## 代码位置

| 功能 | 文件 | 函数/位置 |
|------|------|---------|
| 省级月收计算 | `ming_sim/flows.py` | `calc_province_fiscal` |
| 月度财政 tick | `ming_sim/flows.py` | `apply_fixed_period_flows` |
| 腐败度 delta 落库 | `ming_sim/db.py` | `apply_region_deltas` → FISCAL_SCORE_FIELDS 分支 |
| 省级字段白名单 | `ming_sim/constants.py` | `FISCAL_SCORE_FIELDS` |
| fiscal_config 初始值 | `ming_sim/db.py` | `init_fiscal_config` |
| 推演 payload 含 corruption | `ming_sim/simulation.py` | `json_extract(fiscal,'$.corruption')` |
