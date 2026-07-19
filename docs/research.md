# 实验参数研究

记录各模块实验参数的设计思路、方案对比和决策过程。

---

## 0. Computation 模块 — fib_retracement 参数设计

**设计决策 [P0]**：Computation 保留宽松输出，不做硬过滤。下游（Analysis/Decision）负责质量判定。

### 0.1 简化后的可控变量（3 个）

Computation 模块输出的 Fib Group 质量由大量内部参数决定，但对实验者暴露的核心旋钮应精简为 **3 个**：

| 变量 | 含义 | 控制什么 | 参考范围 |
|------|------|------|------|
| `recent_bars` | 回溯窗口（乘以 1/2/3 得到三组时间尺度） | Fib 线覆盖的历史深度 | 60~200 |
| `min_leg_span_pct` | 最小波段幅度 | 过滤过窄波段 | 0.03~0.10 |
| `scan_bars` | 滑动步长（0=只算最新点） | 输出的时间粒度 | 10~30 |

其余参数（pivot_windows, zigzag_thresholds, regression_windows, weights, top_n 等）作为内部实现固定或走 profile 预设，**不作为实验变量**。

### 0.2 Fib Group 生成规则（精简版）

```
输入: K线序列
步骤:
  1. 三种方法（Pivot/Zigzag/Regression）检测拐点 → 置信度融合
  2. 按 recent_bars × (1,2,3) 三个窗口分别提取趋势腿
  3. 过滤: span_pct < min_leg_span_pct 的腿丢弃
  4. 评分排序 → top_n → 加权合并同方向腿 → FibGroup
  5. 每隔 scan_bars 根 bar 重新计算一次
输出: 每个计算点 × 3窗口 × (up/down) → 最多6组 FibGroup
```

每个 FibGroup 带有 `score` 字段（`span_pct × conf × recency`），由 Analysis 层自行决定如何使用。

### 0.3 案例：为何 score=0.08 的 group 被保留

- `leg=[3493,3580]`，span_pct=2.5%，刚过 `min_leg_span_pct=2%` 阈值
- 7 条 levels 挤在 48 点内，任何 tolerance 都导致多条命中
- **设计决策**：Computation 继续输出，由 Analysis 的方案 E（tolerance ∝ leg_range）自然过滤

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

### 1.2 Group Score 加权方案 [实施]

**设计决策**：tolerance ∝ leg_range + signal_score × group.score

实施细节：
```python
dynamic_tolerance = (group.leg.high - group.leg.low) * tolerance_k  # k ∈ [0.05, 0.15]
distance = abs(price - level_price)
if distance > dynamic_tolerance:
    continue  # 不产出信号

proximity = 1.0 - distance / dynamic_tolerance  # [0, 1] 越近越高
signal_quality = proximity * (group.score / score_normalizer)
```

效果：
- 窄波段（87点）× 0.08 → tolerance=4.35, 且 quality 乘以 0.08 → 信号极弱
- 宽波段（500点）× 2.5 → tolerance=25, 且 quality 乘以 2.5 → 信号强

---

### 1.3 信号→订单转化：得分阈值的根本问题

#### 实证：当前 score 无预测力

用 ana001 (12,899 个信号) 对 885003.WI 做 5 日 forward return 验证：

| 条件 | 样本 | 5日胜率 | 平均方向收益 |
|------|------|------|------|
| 全量 | 12,899 | 49.4% | -0.019% |
| score ≥ 5（当前决策阈值） | 12,838 | 49.4% | -0.019% |
| score [5,10) | 1,296 | **57.6%** | +0.081% |
| score [15,20) | 3,703 | 47.6% | -0.018% |
| score [25,33) | 2,551 | 48.0% | -0.035% |

**结论：score 越高反而越差**。原因：高 score 主要由 `touch_count` 贡献（线被反复触碰→失效）。

#### 真正有预测力的特征

| 特征 | 最佳分组 | 胜率 | 含义 |
|------|------|------|------|
| `bounce_rate ≥ 0.7` | 165 样本 | **63.0%** | 该 level 历史上 70%+ 的触碰产生了反弹 |
| `distance < 0.1%` | 1,632 样本 | 52.3% | 真正"接触"到了线 |
| `consensus ≤ 3` | 3,752 样本 | **55.9%** | 少组命中 = 大波段的单条线 |
| `touch_count < 10` | 700 样本 | 53.3% | "新鲜"level，未被消耗 |

#### 组合条件验证

| 组合 | 样本 | 5日胜率 | MFE(20d) | MAE(20d) | R/R中位 |
|------|------|------|------|------|------|
| 全量 | 12,868 | 49.4% | +0.89% | -0.95% | 0.97 |
| bounce≥0.5 & dist<0.3% & cons≤3 | 1,040 | **56.8%** | +0.80% | -0.64% | **1.57** |
| bounce≥0.7 | 165 | **63.0%** | +1.16% | -0.94% | **3.03** |
| bounce≥0.7 & dist<0.3% | 96 | **63.5%** | +0.89% | -0.74% | **3.00** |

#### 关键发现

1. **线性加权得分无用**：信号的"可执行性"不是各维度的加权总和
2. **必要条件才是关键**：`bounce_rate ≥ 阈值` 是最强单一预测因子（从 49% → 63%）
3. **物理含义**：bounce_rate 高 = 这条 level 在历史上真的起到了支撑/阻力作用
4. **consensus 少反而好**：说明触碰的是大波段的重要线位，不是密集小波段的噪声

---

### 1.4 信号→订单的正确范式

#### 对比：权重打分 vs 条件门禁 vs 概率模型

| 范式 | 做法 | 问题 |
|------|------|------|
| A. 权重阈值（当前） | `weighted_sum > threshold → trade` | 无预测力；高分=噪声 |
| B. 条件门禁 | 满足 N 个必要条件 → trade | 简单有效；但硬边界 |
| C. 概率估计 | `P(win) = f(features) > threshold` | 需要大量数据训练；过拟合风险 |
| D. 期望收益 | `E[R] = P(win)×avg_win - P(loss)×avg_loss > 0` | 最合理但估计困难 |
| E. 条件门禁 + 仓位调节 | 门禁决定是否交易，特征决定仓位大小 | 平衡简单性和灵活性 |

#### 推荐方案：条件门禁 + 信号强度分级（范式 B+E 混合）

**为什么不用权重打分**：
- 打分假设各特征线性可加且量纲可比 — 不成立
- 无法表达"必要条件"语义（bounce_rate 低就不该交易，无论其他多好）
- 实证证明高分不代表高质量

**为什么不用概率模型**：
- 样本量不足以训练可靠模型（尤其是高 bounce_rate 的样本仅 165 个）
- 过拟合风险高
- 黑盒，不可解释

**推荐方案**：

```
决策 = 门禁过滤 + 分级执行

门禁（全部满足才交易）:
  1. proximity: distance / leg_range < tolerance_k (方案 E)
  2. freshness: 该 level 的 bounce_rate ≥ min_bounce_rate
  3. quality: group.score ≥ min_group_score (或通过 tolerance_k 隐式实现)

分级（通过后决定仓位）:
  - A 级: bounce_rate ≥ 0.7 & distance < 0.1% → 标准仓位
  - B 级: bounce_rate ≥ 0.5 & distance < 0.3% → 半仓
  - C 级: 其余通过门禁的 → 最小仓位或仅观察
```

#### 三个核心特征解释

**Proximity（接近度）**

衡量"价格离 Fib 线有多近"，归一化到 [0, 1]。

```
示例: Fib 线在 3546，tolerance = leg_range × 0.08 = 500 × 0.08 = 40
  - close = 3540 → distance = 6 → proximity = 1 - 6/40 = 0.85 ← 很近
  - close = 3520 → distance = 26 → proximity = 1 - 26/40 = 0.35 ← 较远
  - close = 3500 → distance = 46 → proximity < 0 → 超出范围，不产出信号
```

物理含义：**"这根K线和 Fib 线有多亲密"**。0.9 = 几乎贴着线；0.5 = 在 tolerance 的一半位置。

---

**Bounce Rate（反弹率）**

衡量"这条 Fib 线在过去是否真的起到了支撑/阻力作用"。

```
示例: 过去 200 根 bar 里，这条 Fib 线被价格触碰了 20 次
  - 其中 14 次价格触碰后反转了 → bounce_rate = 14/20 = 0.70
  - 含义：历史上 70% 的情况下，价格碰到这条线会弹回去

对比:
  - bounce_rate = 0.7 → 这条线"很硬"，真的有阻力效果 → 交易信号可信
  - bounce_rate = 0.3 → 这条线"很虚"，价格碰到后大概率直接穿过 → 别在这交易
```

物理含义：**"这条线历史上管不管用"**。高 = 真正的支撑/阻力；低 = 纸老虎。

---

**Freshness（新鲜度）**

用 `touch_count` 的反面来衡量——一条线被触碰的次数越多，它的"能量"越消耗。

```
示例:
  - touch_count = 3 → freshness 高：这条线刚形成，市场对它的反应还未充分计价
  - touch_count = 75 → freshness 低：这条线已被反复触碰，所有人都知道它在这里
    → 支撑/阻力被"消耗"了，下次大概率被突破

类比: 一块冰第一次被踩还很硬(fresh)，踩了 75 次后就碎了(exhausted)
```

物理含义：**"这条线还有没有剩余的支撑/阻力能量"**。新线价值高，老线已失效。

---

#### 与当前模块架构的映射

| 模块 | 职责变化 |
|------|------|
| Analysis | 产出信号 + 携带特征（proximity, bounce_rate, touch_count 等） |
| Decision | 条件门禁（替代 score 阈值）+ 分级仓位 |
| Execution | 不变 |

**信号不再需要"得分"**——它携带结构化特征，由 Decision 层用条件门禁做判定。


---

## 2. Decision 模块 — fib_gate 重设计

### 2.1 当前问题

```python
# 当前决策逻辑
if signal.score >= min_strength:  # 5.0
    submit_order()
```

这个方案被实证证伪：score 无预测力。

### 2.2 新方案：条件门禁

**Decision 的输入**不再是一个"分数"，而是信号携带的**结构化特征**：

```python
signal = {
    "proximity": 0.85,         # 距离/tolerance 的反数 [0,1]
    "bounce_rate": 0.72,       # 该 level 历史反弹率
    "group_score": 1.5,        # 来源 group 的质量分
    "consensus": 2,            # 命中组数
    "touch_count": 8,          # 该 level 被触碰的历史次数
    "direction": "up",
    ...
}
```

**门禁规则**（Decision 模块的核心逻辑）：

```python
def should_trade(signal, cfg):
    # 必要条件（全部满足）
    if signal["proximity"] < cfg["min_proximity"]:       # 0.7
        return False, "too_far"
    if signal["bounce_rate"] < cfg["min_bounce_rate"]:   # 0.5
        return False, "weak_level"
    if signal["touch_count"] > cfg["max_touch_count"]:   # 50
        return False, "exhausted_level"
    return True, "passed"

def compute_position_size(signal, cfg):
    # 分级仓位
    if signal["bounce_rate"] >= 0.7 and signal["proximity"] >= 0.9:
        return cfg["full_size"]      # A级
    elif signal["bounce_rate"] >= 0.5:
        return cfg["half_size"]      # B级
    return cfg["min_size"]           # C级
```

### 2.3 可控变量（Decision 层）

| 变量 | 含义 | 参考值 |
|------|------|------|
| `min_bounce_rate` | level 历史反弹率下限 | 0.5 |
| `min_proximity` | 距离衰减后的接近度下限 | 0.7 |
| `max_touch_count` | level 历史触碰上限（新鲜度） | 30~50 |

---

## 3. Execution 模块 — 模拟执行参数

（待补充）

---

---

## 4. 全链路参数总结

### 可控变量清单

| 层 | 变量 | 含义 | 实验范围 |
|----|------|------|------|
| Computation | `recent_bars` | 回溯窗口 | 60~200 |
| Computation | `min_leg_span_pct` | 最小波段幅度 | 0.03~0.10 |
| Computation | `scan_bars` | 滑动步长 | 10~30 |
| Analysis | `tolerance_k` | tolerance = leg_range × k | 0.05~0.15 |
| Decision | `min_bounce_rate` | level 历史反弹率下限 | 0.4~0.7 |
| Decision | `min_proximity` | 距离衰减后接近度下限 | 0.5~0.9 |
| Decision | `max_touch_count` | level 新鲜度上限 | 20~60 |

共 7 个核心变量，覆盖从特征生产到交易决策的全链路。

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

### A.3 信号预测力验证（ana001, 885003.WI, 5日 forward return）

```
score 分层胜率:
  [0,5):   n=78    胜率=52.6%  ← 低分信号反而不差
  [5,10):  n=1,296 胜率=57.6%  ← 最佳
  [10,15): n=2,795 胜率=52.2%
  [15,20): n=3,703 胜率=47.6%  ← 开始低于随机
  [20,25): n=2,476 胜率=46.2%
  [25,33): n=2,551 胜率=48.0%

结论: 当前 score 越高预测越差 (touch_count 堆分 → level 已失效)

有效特征:
  bounce_rate ≥ 0.7:             胜率=63.0%, R/R中位=3.03
  distance < 0.1%:               胜率=52.3%
  consensus ≤ 3:                 胜率=55.9%
  组合(bounce≥0.5,dist<0.3%,cons≤3): 胜率=56.8%, R/R中位=1.57
```
