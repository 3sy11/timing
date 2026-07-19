# 实验参数研究

记录各模块实验参数的设计思路、方案对比和决策过程。

---

## 0. Computation 模块 — fib_retracement 参数设计

### 0.1 Fib Group 有效性判定（当前逻辑）

一组 Fib 线从产出到最终使用经过以下链路：

```
拐点检测 → 置信度融合 → 聚类 → 趋势腿提取 → 排序筛选 → 加权合并 → 生成 FibGroup
```

#### 拐点检测的规则条件

**三种独立方法并行检测拐点**：

| 方法 | 参数 | 规则 |
|------|------|------|
| Pivot | `pivot_windows=[[5,5],[8,8]]` | bar.high ≥ 窗口内所有 high（左右各 N 根） |
| Zigzag | `zigzag_thresholds=[0.05,0.10]` | 反向运动幅度 ≥ threshold 才确认拐点 |
| Regression | `regression_windows=[50,100]` | 收盘价残差 > 2σ 偏离线性回归 |

**置信度融合**：每个方法命中则加对应权重，最终得到 `conf_high / conf_low ∈ [0, 1]`。

#### 趋势腿提取的规则条件

从 `conf > 0` 的点中两两配对（low→high 或 high→low）：
1. `idx_b - idx_a ≥ 3` — 腿至少跨 3 根 bar
2. `span_pct ≥ min_leg_span_pct` — 波段幅度 ≥ 阈值（fib001=2%, fib003=5%）

#### 排序评分（score_and_rank）

```
final_score = span_pct × conf_score × recency × length_penalty
```

其中：
- `span_pct`：波段幅度百分比（越大越好）
- `conf_score`：起止点的置信度之和 + 聚类 bonus
- `recency`：`end_idx / max_idx`（越新越好）
- `length_penalty`：占全量数据 >60% 的超长腿打折

排序后取 `top_n`（fib001=8），up/down 各半，去重嵌套腿。

#### 加权合并

同方向 top 腿按 conf_score 做加权平均，合并为 1 条最终腿 → 生成 FibGroup。

#### 关键结论

**当前没有任何"有效性门禁"**：只要 span_pct 达标且 top_n 有空位，任何腿都会被保留。最终 group 的 score 可以非常低（如 0.08 / 0.003），这些低质量 group 直接传给 analysis 模块使用。

---

### 0.2 案例分析：score=0.08 的 group

2018-05-29 计算点产出的唯一 group：
- `leg_low=3493, leg_high=3580`，range=87 点（仅 2.5%）
- 5 条回撤线全部挤在 3511~3559 的 48 点区间内
- 2018-09-10 K线 close=3528.9，**落在 5 条线中间**，touch_tolerance=20 导致同时"触碰"多条线
- 该 group score=0.08 极低，但无任何过滤机制阻止它产出信号

#### 问题根因

| 层级 | 问题 |
|------|------|
| Computation | `min_leg_span_pct=0.02` 允许了极窄波段（87 点 / 2.5%），导致 levels 过于密集 |
| Computation | 没有 score 下限，极低质量 group 也进入输出 |
| Analysis | `touch_tolerance=20`（固定点数）对窄波段来说覆盖了大半条腿 |
| Analysis | 不区分 group quality，低分 group 的触碰和高分 group 一样计入信号 |

---

### 0.3 Computation 参数设计研究

#### 核心参数对信号质量的影响

| 参数 | 当前值(fib001) | 影响 |
|------|------|------|
| `min_leg_span_pct` | 0.02 (2%) | 越低→越多窄波段→levels越密集→误触越多 |
| `top_n` | 8 | 每步保留 8 条腿再合并，更多=更多噪声 |
| `recent_bars` | 60 | 短期窗口，可能只覆盖小振荡 |
| `min_cluster_conf` | 0.2 | 低阈值允许低置信度拐点进入聚类 |
| `pivot_windows` | [[3,3],[5,5]] | 小窗口→短期噪声拐点多 |
| `zigzag_thresholds` | [0.03, 0.05] | 低阈值→微幅振荡也识别为拐点 |

#### 应该在 Computation 还是 Analysis 层解决？

| 策略 | 做法 | 适用场景 |
|------|------|------|
| **源头把控** | 提高 `min_leg_span_pct` / 加 score 下限 | 从根本上减少低质量 group |
| **下游过滤** | analysis 层按 group score 加权/过滤 | 保持 computation 通用性，让 analysis 自行选择 |
| **混合** | computation 保留宽松输出 + analysis 用 group score 做信号权重 | 推荐 |

---

## 1. Analysis 模块 — fib_touch 参数设计

### 1.1 Touch Tolerance（触碰容忍度）

#### 当前问题

固定值 `touch_tolerance = 20`（点数），导致：
- 对于价格 3500 的标的，20 点 ≈ 0.57%，过于宽松
- 对于价格 50 的标的，20 点 ≈ 40%，完全不可用
- 未做归一化，不同价格水平的标的之间不可比

#### 实际数据验证（885003.WI，2018-09-10，close=3528.9）

| 方案 | 参数 | tolerance | 距离17.73是否触发 |
|------|------|------|------|
| 固定点数 | 20 | 20.00 | ✓ 触发 |
| 百分比 0.5% | price×0.5% | 17.64 | ✗ 刚好不触发 |
| 百分比 0.3% | price×0.3% | 10.59 | ✗ 不触发 |
| std(20)×1.0 | 7.26 | 7.26 | ✗ 不触发 |
| std(50)×1.0 | 12.18 | 12.18 | ✗ 不触发 |
| ATR(20)×1.0 | 3.99 | 3.99 | ✗ 不触发 |

#### 标准差方案详解

`tolerance = std(close, N) * k`

- `std(close, N)` = 最近 N 根 bar 收盘价的标准差
- 物理含义：**价格在当前时段的"正常波动幅度"**
- 1×std ≈ 68% 概率的正常波动范围（假设正态）

**885003.WI 2018-09 附近**：
```
std(20) = 7.26   (价格的 0.21%)  — 20天短期波动
std(50) = 12.18  (价格的 0.35%)  — 50天中期波动
std(100) = 13.85 (价格的 0.39%) — 100天长期波动

日均振幅 = 4.43  (价格的 0.13%) — 单日平均波动

距离 17.73 = 1.46×std(50) = 4.0×日均振幅
```

**解读**：17.73 的距离相当于 1.5 倍中期标准差，或 4 个正常交易日的累积方向移动。这远超"触碰"的直觉。

#### 方案对比（含标准差）

| 方案 | 公式 | 优点 | 缺点 | 推荐 k 值 |
|------|------|------|------|------|
| A. 百分比 | `price × k%` | 简单；跨标的归一化 | 不适应波动率变化 | 0.2~0.5% |
| B. ATR 倍数 | `ATR(N) × k` | 适应波动率 | 纯 close 数据 ATR=日涨跌幅；极低波动期可能过紧 | 0.5~1.5 |
| D. 标准差 | `std(close, N) × k` | 统计含义清晰；自适应 | 需额外计算；参数两层（N, k） | 0.5~1.0 |
| E. 波段比例 | `(leg_high - leg_low) × k` | 直接关联 group 的宽度 | 窄波段可能过紧 | 0.05~0.15 |

**方案 E 的亮点**：tolerance 与 leg 的宽度成正比。一个 500 点的波段，5% tolerance=25 点合理。一个 87 点的窄波段，5% tolerance=4.35 点 → 自动变严格，窄波段几乎不可能"触碰"。

#### 讨论

**标准差 vs 百分比**：
- 百分比是"固定比例尺"，所有时段一视同仁
- 标准差是"动态比例尺"，高波动期放宽、低波动期收紧
- 对于震荡收窄时期（如 2018年9月 std 仅 0.21%），std 会给出非常紧的 tolerance，这恰好是正确的行为——低波动期价格密集，"触碰"的判定应该更严格

**关键洞察**：问题不只是 tolerance 的绝对大小，还有 **group 的宽度**。一个 87 点的窄波段，它的 7 条 levels 挤在 48 点范围内，任何合理的 tolerance 都会同时命中多条线。因此：
- 要么从 computation 层过滤掉过窄波段
- 要么在 analysis 层用方案 E（`tolerance ∝ leg_range`）让窄波段自然失效

---

### 1.2 Group Score 在信号中的作用

#### 当前问题

- 检测逻辑对所有 Fib Group 一视同仁：score=0.08 和 score=3.75 的 group 触碰时产出同等信号
- 低分组来自窄波段，其 levels 过于密集，容易批量触发
- 信号得分公式中没有引入 group quality 因子

#### Group Score 的实际含义

```
final_score = span_pct × conf_score × recency × length_penalty
```

- `span_pct` 大 → 大波段 → 回撤 levels 间距大 → 触碰更有意义
- `conf_score` 高 → 拐点确认度高 → 波段识别更可靠
- `recency` 高 → 近期波段 → 对当前行情更有参考价值

score 综合反映了"这组 Fib 线对当前行情的参考价值"。

#### 方案对比

| 方案 | 描述 | 优点 | 缺点 |
|------|------|------|------|
| A. 硬阈值 | `if group.score < min_score: skip` | 简单直接 | 生硬；不同标的 score 分布不同 |
| B. 分位数 | 只用 score top N% 的 groups | 自适应 | 依赖分布；不稳定 |
| C. 乘性权重 | `signal_score *= group.score / max_score` | 不丢弃任何线，但低质量线自然弱化 | 需归一化 |
| D. tolerance 关联 | `tolerance = leg_range × k`（方案 E） | 窄波段自动变严格 | 间接实现 |
| E. 混合 | tolerance 关联 + score 乘性权重 | 双重自适应 | 参数交互 |

#### 讨论

**不应单纯"过滤掉"低分 group**——图上的 Fib 线本身是合理的。问题是：
1. 窄波段的 levels 太密集，tolerance 应该按波段宽度自适应（§1.1 方案 E）
2. 低分 group 的触碰信号权重应该更低（方案 C）

推荐组合：**tolerance ∝ leg_range + signal_score × group.score**

---

### 1.3 信号得分逻辑重设计

#### 当前公式的问题

```python
score = (groups_hit * w_consensus +
         bounce_rate * w_bounce_rate +
         touch_count * w_touch_count +
         is_high_volume * w_volume +
         is_counter_trend * w_counter_trend +
         has_pattern * w_candle)
```

问题：
1. **线性叠加无必要条件**：touch_count=75 贡献 15 分就能凑出高分信号
2. **维度量纲不统一**：bounce_rate ∈ [0,1]，touch_count ∈ [0,∞)，直接加权不合理
3. **缺乏 group quality 因子**：不区分来自好波段还是坏波段的触碰

#### 改进方向

**分层结构**：
```
1. 前置过滤（必要条件）
   - 距离 ≤ dynamic_tolerance（方案 D/E）
   - group.score ≥ min_threshold（或距离本身依赖 group 宽度）

2. 基础得分 = f(距离, group_score)
   - 越近得分越高（线性/指数衰减）
   - group.score 作为乘数

3. 加分项（归一化后叠加）
   - 历史反弹率（0~1）
   - 成交量确认（0/1）
   - 多组共识（0~N）
   - 逆势接近（0/1）
```

**距离衰减函数**（替代 tolerance 二值判定）：
```python
distance_ratio = abs(price - level) / dynamic_tolerance
if distance_ratio > 1.0:
    return 0  # 超出范围
proximity_score = 1.0 - distance_ratio  # 越近越高
```

这比当前的"在 tolerance 内=1，外=0"更合理。

---

### 1.4 K 线形态（Candle Pattern）

#### Doji（十字星）

技术分析中的一种 K 线形态：**收盘价 ≈ 开盘价**，实体极短，表示多空力量均衡/犹豫。通常出现在趋势转折点。

常见变体：
- 标准十字星：上下影线对称
- 蜻蜓十字（T字线）：长下影、无上影 → 低位出现看涨
- 墓碑十字（倒T线）：长上影、无下影 → 高位出现看跌

当前判定（代码）：
```python
if body < rng * 0.1:  # 实体 < 振幅 10%
    patterns.append("doji")
```

**885003.WI 的数据问题**：O=H=L=C → rng=0 → 每根 bar 都是 doji。这是 close-only 数据的固有限制，candle pattern 维度对此类数据应直接跳过。

---

### 1.5 信号点 Y 轴位置

用收盘价（close）作为信号点位置。信号本质是"这根 K 线接近了某条回撤线"，标记在 K 线上更直观。`level_price` 作为附加信息保留。

---

## 2. Decision 模块 — fib_gate 参数设计

（待补充）

---

## 3. Execution 模块 — 模拟执行参数

（待补充）

---

## 附录 A：数据验证记录

### A.1 885003.WI 统计特征

```
全量: 5470 根日线, 价格 981 ~ 5206
日涨跌幅(绝对值): 均值 0.244%, 中位 0.155%, P90 0.550%

2018-09 附近局部:
  close range: 3528 ~ 3573
  std(20) = 7.26 (0.21% of price)
  std(50) = 12.18 (0.35% of price)
  平均日振幅 = 4.43 (0.13% of price)
```

### A.2 fib001 Group Score 分布

```
全量 1131 个 groups:
  score: min=0.003, P25=0.128, P50=0.258, P75=0.448, P90=0.834, max=3.748
  leg_range: min=21点, mean=141点, max=746点
  leg_pct: min=2.01%, mean=5.52%, max=52.54%

score < 0.1: 222 个 (20%)
score < 0.5: 901 个 (80%)
score >= 1.0: 85 个 (7.5%)
leg_pct < 3%: 420 个 (37%)
```
