# Fib Retracement Pipeline 完整参考文档

> 本文档描述计算模块 `fib_retracement` 的完整管道，覆盖每个阶段的输入/输出、字段含义、示例数据，以及从原始 K 线到最终信号的字段血缘关系。

---

## 目录

1. [总览](#总览)
2. [Stage 1: 拐点识别 (Pivot Detection)](#stage-1-拐点识别)
3. [Stage 2: 置信度计算 (Confidence)](#stage-2-置信度计算)
4. [Stage 3: 聚类 + Fib 网格拟合 + 生命周期管理](#stage-3-聚类--fib-网格拟合--生命周期管理)
5. [Result: 打平投产表](#result-打平投产表)
6. [Analysis: fib_touch 信号检测](#analysis-fib_touch-信号检测)
7. [字段血缘图](#字段血缘图)

---

## 总览

```
K线 (klines.parquet)
  │
  ├── Stage 1: tag_pivots + tag_zigzag + tag_regression
  │     └─→ step1_pivots.parquet
  │
  ├── Stage 2: compute_confidence
  │     └─→ step2_confidence.parquet
  │
  ├── Stage 3: cluster_prices + fit_fib_grid_to_clusters + 生命周期管理
  │     └─→ step3_fib_groups.parquet    ← 核心中间产物（Fib 组维度）
  │
  ├── Result: _flatten_to_lines
  │     └─→ result.parquet              ← 投产表（Fib Level 维度）
  │
  └── Analysis: fib_touch detect
        └─→ signals.parquet             ← 信号输出
```

**源文件**:
- 管道编排: `timing/computation/algo/fib_retracement/pipeline.py`
- 纯函数算法: `timing/computation/algo/fib_retracement/algo.py`
- 配置: `timing/computation/algo/fib_retracement/config.py`
- 数据模型: `timing/computation/algo/fib_retracement/models.py`
- 信号检测: `timing/analysis/rules/fib_touch/detect.py`

**数据目录结构**:
```
warehouse/timing/
  ├── klines/{symbol}/{interval}/{symbol}.parquet
  └── computation/fib_retracement/{compute_id}/{symbol}/{interval}/
        ├── step1_pivots.parquet
        ├── step2_confidence.parquet
        ├── step3_fib_groups.parquet
        ├── result.parquet
        └── manifest.json
```

---

## Stage 1: 拐点识别

### 函数

| 函数 | 文件 | 作用 |
|------|------|------|
| `base_df(klines)` | algo.py | 将 dict 列表转为标准 DataFrame |
| `tag_pivots(df, windows)` | algo.py | 局部极值拐点检测 |
| `tag_zigzag(df, thresholds)` | algo.py | ZigZag 算法识别趋势反转点 |
| `tag_regression(df, windows)` | algo.py | 线性回归残差检测价格偏离 |

### 输入

**klines** — 原始 K 线数据

| 字段 | 类型 | 含义 |
|------|------|------|
| `ts` | int64 | Unix 时间戳 (秒) |
| `open` | float64 | 开盘价 |
| `high` | float64 | 最高价 |
| `low` | float64 | 最低价 |
| `close` | float64 | 收盘价 |
| `volume` | float64 | 成交量 |

### 配置参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `pivot_windows` | `[[5,5], [8,8]]` | 局部窗口大小 [left_bars, right_bars] |
| `zigzag_thresholds` | `[0.05, 0.10]` | ZigZag 反转百分比阈值 |
| `regression_windows` | `[50, 100]` | 线性回归窗口长度 |
| `weights` | 见 config.py | 每种检测器的置信度权重 |

### 输出: step1_pivots.parquet

基础 K 线字段 + 每个检测器的高/低点标记列。

| 字段 | 类型 | 含义 | 来源 |
|------|------|------|------|
| `ts` | int64 | 时间戳 | klines.ts |
| `open/high/low/close/volume` | float64 | K 线 OHLCV | klines 原样 |
| `pivot_high_5x5` | float64/NaN | 窗口(5,5)的高点拐点价格 | tag_pivots: high 在窗口内为最大值时标记 |
| `pivot_low_5x5` | float64/NaN | 窗口(5,5)的低点拐点价格 | tag_pivots: low 在窗口内为最小值时标记 |
| `pivot_high_8x8` | float64/NaN | 窗口(8,8)的高点拐点价格 | 同上，更大窗口 |
| `pivot_low_8x8` | float64/NaN | 窗口(8,8)的低点拐点价格 | 同上 |
| `zigzag_high_5` | float64/NaN | 5% ZigZag 高点 | tag_zigzag: 涨幅>=5%的反转高点 |
| `zigzag_low_5` | float64/NaN | 5% ZigZag 低点 | tag_zigzag: 跌幅>=5%的反转低点 |
| `zigzag_high_10` | float64/NaN | 10% ZigZag 高点 | 同上，更大阈值 |
| `zigzag_low_10` | float64/NaN | 10% ZigZag 低点 | 同上 |
| `reg_high_50` | float64/NaN | 50 期回归偏高点 | tag_regression: 残差 > 2σ 时标记 high |
| `reg_low_50` | float64/NaN | 50 期回归偏低点 | tag_regression: 残差 < -2σ 时标记 low |
| `reg_high_100` | float64/NaN | 100 期回归偏高点 | 同上 |
| `reg_low_100` | float64/NaN | 100 期回归偏低点 | 同上 |

### 示例数据

```
| ts         | high    | low     | close   | pivot_high_5x5 | pivot_low_5x5 | zigzag_high_5 | reg_high_50 |
|------------|---------|---------|---------|-----------------|---------------|---------------|-------------|
| 1719187200 | 3412.50 | 3380.10 | 3400.20 | NaN             | NaN           | NaN           | NaN         |
| 1719273600 | 3450.80 | 3395.30 | 3445.60 | 3450.80         | NaN           | 3450.80       | NaN         |
| 1719360000 | 3430.20 | 3370.50 | 3375.40 | NaN             | 3370.50       | NaN           | NaN         |
| 1719446400 | 3410.00 | 3360.80 | 3380.90 | NaN             | NaN           | NaN           | 3360.80     |
```

**解读**: 非 NaN 的值表示该 bar 被对应检测器识别为拐点，NaN 表示该 bar 不是拐点。

---

## Stage 2: 置信度计算

### 函数

| 函数 | 文件 | 作用 |
|------|------|------|
| `compute_confidence(df, wmap, weights)` | algo.py | 将多检测器的拐点信号加权合并为 [0, 1] 置信度 |

### 输入

Stage 1 输出的 DataFrame + `wmap`（列名 → 权重 key 的映射）+ `weights`（权重 key → 数值权重）

### 计算逻辑

```
conf_high = Σ(检测器i标记了high? × weight_i) / Σ(所有weight)   → clip [0, 1]
conf_low  = Σ(检测器i标记了low?  × weight_i) / Σ(所有weight)   → clip [0, 1]
```

### 输出: step2_confidence.parquet

| 字段 | 类型 | 含义 | 来源 |
|------|------|------|------|
| `ts` | int64 | 时间戳 | klines.ts |
| `high` | float64 | 最高价 | klines.high |
| `low` | float64 | 最低价 | klines.low |
| `close` | float64 | 收盘价 | klines.close |
| `conf_high` | float64 [0,1] | 高点置信度 | 加权汇总 stage1 所有 `*_high_*` 列 |
| `conf_low` | float64 [0,1] | 低点置信度 | 加权汇总 stage1 所有 `*_low_*` 列 |

### 示例数据

```
| ts         | high    | low     | close   | conf_high | conf_low |
|------------|---------|---------|---------|-----------|----------|
| 1719187200 | 3412.50 | 3380.10 | 3400.20 | 0.00      | 0.00     |
| 1719273600 | 3450.80 | 3395.30 | 3445.60 | 0.65      | 0.00     |
| 1719360000 | 3430.20 | 3370.50 | 3375.40 | 0.00      | 0.42     |
| 1719446400 | 3410.00 | 3360.80 | 3380.90 | 0.00      | 0.18     |
```

**解读**: `conf_high=0.65` 表示该 bar 的 high 价格被多个检测器共同识别为高点拐点，置信度 65%。

---

## Stage 3: 聚类 + Fib 网格拟合 + 生命周期管理

这是核心阶段，包含 3 个子步骤：

### 子步骤 3a: 价格聚类 (cluster_prices)

**输入**: Stage 2 的 DataFrame（含 conf_high/conf_low），按窗口截取 `recent_df`

**函数**: `cluster_prices(recent_df, kind="high"|"low", tolerance_pct, min_conf)`

**逻辑**:
1. 筛选置信度 >= `min_conf` 的点
2. 按价格排序，相邻点距离 <= `price_range × tolerance_pct` 则归入同一聚类
3. 聚类中心 = 加权平均价格（以 conf 为权重）

**中间产物**: `centers` 列表 — `[(center_price, total_conf), ...]`

```
centers 示例:
[
  [3362.45, 2.85],    ← 聚类中心价格 3362.45，累计置信度 2.85
  [3398.20, 1.40],
  [3425.60, 3.10],
  [3448.90, 2.25],
  [3470.30, 1.80]
]
```

**关键**: `centers` 就是所有 Fib 计算的基础——它代表了"市场在这些价位反复出现拐点"。

### 子步骤 3b: Fib 网格拟合 (fit_fib_grid_to_clusters)

**输入**: `centers` + `direction`（up/down）

**函数**: `fit_fib_grid_to_clusters(centers, direction, min_span_pct, min_assigned)`

**逻辑**:
1. 从 `centers` 中取 2 个点，假设它们分别对应 `_FIT_RATIOS = [0.236, 0.382, 0.5, 0.618, 0.786]` 中的两个比例
2. 由 2 个 (price, ratio) 对解出 (high, low)：`_solve_hl(p1, r1, p2, r2, direction)`
3. 用 `_score_fit` 评分：检查所有 centers 与 7 线（含 0%）的对齐程度
4. 按 score 降序返回最多 2 个候选

**拟合与评分的比例**:
- **拟合用** `_FIT_RATIOS = [0.236, 0.382, 0.5, 0.618, 0.786]` — 从这 5 条线中选 2 条做定位锚点
- **评分用** `_SCORE_RATIOS = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786]` — 额外检查 0% 是否也有聚类对齐
- **输出用** `_ALL_RATIOS = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]` — 完整 7 线

**_score_fit 细节**:
```
对每个 cluster center:
  implied_ratio = (high - price) / span    (direction=up)
                = (price - low) / span     (direction=down)
  找 _SCORE_RATIOS 中最接近的 actual_ratio
  如果 |implied_ratio - actual_ratio| <= tol_pct (0.02):
    该 center 被判定为"对齐"该 Fib 线
    score += conf × (1 - distance/tol_pct)
coverage = 被覆盖的 ratio 数 / 总 ratio 数
final_score = score × coverage
```

**输出**: `[{high, low, score, assigned}, ...]`

### 子步骤 3c: 生命周期管理

**每个 (multiplier, direction) 维护最多 MAX_ACTIVE=3 个活跃 Fib 组。**

| multiplier | 含义 | 实际窗口 = recent_bars × mult |
|------------|------|------|
| 1 | 短期 | ~80-100 bars |
| 2 | 中期 | ~160-200 bars |
| 3 | 长期 | ~240-300 bars |

**direction**: `"up"` (上升趋势回撤) 或 `"down"` (下降趋势回撤)

**失效机制**:

| 失效类型 | 触发条件 | 说明 |
|----------|----------|------|
| `boundary_break` | close 超出 `leg_high + buffer` 或 `leg_low - buffer` 累计 >= `break_bars` 次 | buffer = span × `boundary_tolerance_k` |
| `low_coverage` | 最近 `stale_lookback` 根 bar 中，5 条内层线 (0.236~0.786) 的覆盖率 < `stale_min_coverage` | 说明 Fib 线长期不被触碰，失去解释力 |

**填充机制**:

| 类型 | 触发条件 | 说明 |
|------|----------|------|
| `initial` | 管道启动时 | 为每个 key 尽量填满 3 个组 |
| `event_break` | 某组失效后 | 立即尝试计算新组替补 |
| `vacancy_fill` | 槽位空缺持续 >= `VACANCY_INTERVAL` bars | 定期尝试填充空位 |

### 输出: step3_fib_groups.parquet

| 字段 | 类型 | 含义 | 来源 |
|------|------|------|------|
| `effective_ts` | int64 | 组生效时间戳 | 计算时 feature_df 末尾 bar 的 ts |
| `multiplier` | int | 时间尺度 (1/2/3) | 窗口倍数 |
| `direction` | str | 趋势方向 ("up"/"down") | fit 搜索方向 |
| `score` | float64 | Fib 网格拟合得分 | `_score_fit` 返回 |
| `leg_start_ts` | int64 | 窗口起始时间戳 | recent_df 首 bar 的 ts |
| `leg_end_ts` | int64 | 窗口结束时间戳 | recent_df 末 bar 的 ts |
| `leg_low` | float64 | Fib 组 0% 价格 | `_solve_hl` 解出的 low |
| `leg_high` | float64 | Fib 组 100% 价格 | `_solve_hl` 解出的 high |
| `levels_json` | str (JSON) | 7 条 Fib 线 `[[ratio, price], ...]` | `levels_from_hl(high, low, direction)` |
| `cluster_centers_json` | str (JSON) | 聚类中心 `[[price, conf], ...]` | 当次窗口 cluster_prices 的结果 |
| `invalidated_ts` | int64/null | 失效时间戳 | 生命周期管理判定 |
| `invalidate_reason` | str/null | 失效原因 | `"boundary_break"` / `"low_coverage(0.20)"` |
| `source` | str/null | 产生来源 | `"initial"` / `"event_break"` / `"vacancy_fill"` |
| `parent_eff_ts` | int64/null | 前任组的 effective_ts | 仅 `event_break` 时有值 |

### 示例数据

```
| effective_ts | mult | dir  | score | leg_low  | leg_high | levels_json                                          | cluster_centers_json                         | invalidated_ts | invalidate_reason | source  |
|-------------|------|------|-------|----------|----------|------------------------------------------------------|----------------------------------------------|----------------|-------------------|---------|
| 1719532800  | 1    | up   | 4.52  | 3350.00  | 3480.00  | [[0.0,3480.0],[0.236,3449.3],...,[1.0,3350.0]]       | [[3362.45,2.85],[3398.2,1.4],...,[3470.3,1.8]] | 1719792000     | boundary_break    | initial |
| 1719532800  | 1    | down | 3.80  | 3320.00  | 3460.00  | [[0.0,3320.0],[0.236,3353.0],...,[1.0,3460.0]]       | [[3362.45,2.85],[3398.2,1.4],...,[3470.3,1.8]] | null           | null              | initial |
| 1719792000  | 1    | up   | 5.10  | 3370.00  | 3510.00  | [[0.0,3510.0],[0.236,3476.9],...,[1.0,3370.0]]       | [[3378.1,3.2],[3425.6,2.0],...,[3505.0,1.5]]   | null           | null              | event_break |
```

### levels_json 结构详解

```json
[
  [0.0,   3480.00],   // ratio=0%   → leg_high (direction=up)，数学确定
  [0.236, 3449.32],   // ratio=23.6%，由 high - span×0.236 计算
  [0.382, 3430.36],   // ratio=38.2%
  [0.5,   3415.00],   // ratio=50%
  [0.618, 3399.64],   // ratio=61.8%
  [0.786, 3377.88],   // ratio=78.6%
  [1.0,   3350.00]    // ratio=100% → leg_low (direction=up)，数学外推
]
```

### cluster_centers_json 结构详解

```json
[
  [3362.45, 2.85],    // [聚类中心价格, 累计置信度]
  [3398.20, 1.40],    // 置信度 = 该聚类内所有拐点的 conf_high/conf_low 之和
  [3425.60, 3.10],
  [3448.90, 2.25],
  [3470.30, 1.80]
]
```

**用途**: 通过 `cluster_centers_json`，可以找到每条 Fib 线对齐了哪个原始聚类中心 —— 即从 Fib line 反向追溯到"市场共识价位"。

---

## Result: 打平投产表

### 函数

`_flatten_to_lines(fib_records)` — 将 step3 的 Fib 组维度打平为 Fib Level 维度。

**转换关系**: 1 个 Fib 组 (step3 的 1 行) → 7 行 (result 中 7 条 Fib Level)

### 输出: result.parquet

| 字段 | 类型 | 含义 | 来源 |
|------|------|------|------|
| `effective_ts` | int64 | 组生效时间戳 | step3.effective_ts |
| `multiplier` | int | 时间尺度 | step3.multiplier |
| `direction` | str | 趋势方向 | step3.direction |
| `fib_score` | float64 | 拟合得分 | step3.score |
| `leg_low` | float64 | Fib 组 0% | step3.leg_low |
| `leg_high` | float64 | Fib 组 100% | step3.leg_high |
| `leg_start_ts` | int64 | 窗口起始 | step3.leg_start_ts |
| `leg_end_ts` | int64 | 窗口结束 | step3.leg_end_ts |
| `invalidated_ts` | int64/null | 失效时间 | step3.invalidated_ts |
| `invalidate_reason` | str/null | 失效原因 | step3.invalidate_reason |
| `source` | str/null | 产生来源 | step3.source |
| `ratio` | float64 | Fib 比例 (0.0~1.0) | levels_json 展开 |
| `price` | float64 | Fib 线价格 | levels_json 展开 |
| `is_extrapolated` | bool | 是否为外推线 (ratio=1.0) | ratio == 1.0 |

### 示例数据

```
| effective_ts | mult | dir | fib_score | leg_low | leg_high | ratio | price   | is_extrapolated |
|-------------|------|-----|-----------|---------|----------|-------|---------|-----------------|
| 1719532800  | 1    | up  | 4.52      | 3350.00 | 3480.00  | 0.000 | 3480.00 | false           |
| 1719532800  | 1    | up  | 4.52      | 3350.00 | 3480.00  | 0.236 | 3449.32 | false           |
| 1719532800  | 1    | up  | 4.52      | 3350.00 | 3480.00  | 0.382 | 3430.36 | false           |
| 1719532800  | 1    | up  | 4.52      | 3350.00 | 3480.00  | 0.500 | 3415.00 | false           |
| 1719532800  | 1    | up  | 4.52      | 3350.00 | 3480.00  | 0.618 | 3399.64 | false           |
| 1719532800  | 1    | up  | 4.52      | 3350.00 | 3480.00  | 0.786 | 3377.88 | false           |
| 1719532800  | 1    | up  | 4.52      | 3350.00 | 3480.00  | 1.000 | 3350.00 | true            |
```

---

## Analysis: fib_touch 信号检测

### 数据流

```
step3_fib_groups.parquet ──→ read_structures_timeseries ──→ FibGroup 列表
                                                              │
klines.parquet ────────────→ base_df ─────────────────────────┤
                                                              ▼
                                                    detect_bar_signals (每根 bar)
                                                              │
                                                              ▼
                                                    signals.parquet
```

### 核心函数 (detect.py)

| 函数 | 作用 |
|------|------|
| `measure_proximity(close, groups, cfg)` | 对所有 7 线测量距离，返回感知半径内的记录 |
| `compute_consensus(records, tolerance)` | 统计同一价位附近有多少独立 Fib 组共振 |
| `evaluate_level_history(df, level_price, tol, bar_idx, lookback)` | 计算某价位的历史触碰次数和弹回率 |
| `compute_volume_ratio(df, bar_idx, lookback)` | 当前 bar 成交量 / 过去均量 |
| `compute_score_derived(proximity, bounce, vol, consensus, ratio, cfg)` | 加权综合评分 |

### proximity 计算

```
leg_range = group.leg.high - group.leg.low
max_dist  = leg_range × proximity_k            (默认 0.15)
distance  = |close - level_price|
proximity = 1 - distance / max_dist             (仅当 distance <= max_dist 时产出)
```

### score_derived 计算

```
score = proximity      × w_proximity  (2.0)
      + bounce_rate    × w_bounce     (1.5)
      + vol_ratio_norm × w_volume     (0.5)
      + consensus      × w_consensus  (1.0)
      + ratio_import   × w_ratio      (0.5)
```

其中:
- `vol_ratio_norm = min(volume_ratio, volume_cap) / volume_cap`
- `ratio_import` 由 `_RATIO_IMPORTANCE` 字典查得: 0.618→1.0, 0.5→0.9, 0.382/0.786→0.7, 0.236→0.5, 0/1→0.3

### 输出: signals.parquet

| 字段 | 类型 | 含义 | 来源 |
|------|------|------|------|
| `ts` | int64 | Bar 时间戳 | klines.ts |
| `close` | float64 | Bar 收盘价 | klines.close |
| `multiplier` | int | Fib 组时间尺度 | step3.multiplier |
| `direction` | str | Fib 组方向 | step3.direction |
| `ratio` | float64 | 被触碰的 Fib 比例 | levels_json 展开 |
| `level_price` | float64 | 被触碰的 Fib 线价格 | levels_json 展开 |
| `distance` | float64 | \|close - level_price\| | 实时计算 |
| `proximity` | float64 [0,1] | 接近度 (越高越近) | 1 - distance/max_dist |
| `bounce_rate` | float64 [0,1] | 历史弹回率 | evaluate_level_history |
| `touch_count` | int | 历史触碰次数 | evaluate_level_history |
| `volume_ratio` | float64 | 当前量/均量 | compute_volume_ratio |
| `consensus` | int | 共振组数 | compute_consensus |
| `approach` | str | 接近方向 | "from_above"/"from_below"/"at_level" |
| `score_derived` | float64 | 综合评分 | 加权公式 |

---

## 字段血缘图

从原始 K 线到最终信号，每个字段的完整流转路径：

```
┌────────────────────────────────────────────────────────────────────────────┐
│ klines.parquet                                                             │
│  ts, open, high, low, close, volume                                        │
└──┬──────────┬─────────┬──────────┬─────────────────────────────────────────┘
   │          │         │          │
   │          ▼         ▼          ▼
   │   ┌──────────────────────────────────────────────────────────────┐
   │   │ Stage 1: step1_pivots.parquet                                │
   │   │  pivot_high_5x5  ← high 在 [i-5, i+5] 窗口内为最大值        │
   │   │  pivot_low_5x5   ← low 在 [i-5, i+5] 窗口内为最小值         │
   │   │  zigzag_high_5   ← ZigZag 5% 反转高点                       │
   │   │  zigzag_low_5    ← ZigZag 5% 反转低点                       │
   │   │  reg_high_50     ← 50 期回归残差 > 2σ                       │
   │   │  reg_low_50      ← 50 期回归残差 < -2σ                      │
   │   └──┬───────────────────────────────────────────────────────────┘
   │      │
   │      ▼
   │   ┌──────────────────────────────────────────────────────────────┐
   │   │ Stage 2: step2_confidence.parquet                            │
   │   │  conf_high ← Σ(各检测器标记high × weight) / Σ(weights)       │
   │   │  conf_low  ← Σ(各检测器标记low  × weight) / Σ(weights)       │
   │   └──┬───────────────────────────────────────────────────────────┘
   │      │
   │      ▼
   │   ┌──────────────────────────────────────────────────────────────┐
   │   │ cluster_prices (内存中间产物)                                 │
   │   │  center    ← Σ(price_i × conf_i) / Σ(conf_i)   加权平均     │
   │   │  total_conf ← Σ(conf_high 或 conf_low)                      │
   │   │  hit_count ← 聚类内点数                                      │
   │   └──┬───────────────────────────────────────────────────────────┘
   │      │
   │      ▼
   │   ┌──────────────────────────────────────────────────────────────┐
   │   │ fit_fib_grid_to_clusters (内存)                              │
   │   │  high     ← _solve_hl(center_i, ratio_a, center_j, ratio_b) │
   │   │  low      ← 同上                                            │
   │   │  score    ← _score_fit(所有 centers 对 7 线的对齐度)          │
   │   │  assigned ← 对齐的聚类数                                     │
   │   └──┬───────────────────────────────────────────────────────────┘
   │      │
   │      ▼
   │   ┌──────────────────────────────────────────────────────────────┐
   │   │ Stage 3: step3_fib_groups.parquet (Fib 组维度)               │
   │   │  effective_ts  ← feature_df 末 bar 的 ts                     │
   │   │  multiplier    ← 窗口倍数 1/2/3                              │
   │   │  direction     ← fit 搜索方向 up/down                        │
   │   │  score         ← fit_fib_grid_to_clusters 返回的 score        │
   │   │  leg_low       ← fit 解出的 low                              │
   │   │  leg_high      ← fit 解出的 high                             │
   │   │  leg_start_ts  ← recent_df 首 bar ts                        │
   │   │  leg_end_ts    ← recent_df 末 bar ts                        │
   │   │  levels_json   ← levels_from_hl(high, low, dir) → 7 条线    │
   │   │  cluster_centers_json ← 当次窗口 cluster_prices 产出的聚类    │
   │   │  invalidated_ts ← 生命周期管理判定失效时间                     │
   │   │  invalidate_reason ← "boundary_break" / "low_coverage(x)"    │
   │   │  source         ← "initial"/"event_break"/"vacancy_fill"     │
   │   │  parent_eff_ts  ← event_break 时前任组的 effective_ts         │
   │   └──┬───────────────────────────────────────────────────────────┘
   │      │
   │      ▼
   │   ┌──────────────────────────────────────────────────────────────┐
   │   │ Result: result.parquet (Fib Level 维度, 每行 1 条线)          │
   │   │  effective_ts    ← step3.effective_ts                        │
   │   │  multiplier      ← step3.multiplier                         │
   │   │  direction       ← step3.direction                          │
   │   │  fib_score       ← step3.score                              │
   │   │  leg_low/high    ← step3.leg_low/high                       │
   │   │  ratio           ← levels_json[i][0]  展开                  │
   │   │  price           ← levels_json[i][1]  展开                  │
   │   │  is_extrapolated ← ratio == 1.0                             │
   │   └──┬───────────────────────────────────────────────────────────┘
   │      │
   ▼      ▼
┌──────────────────────────────────────────────────────────────────────┐
│ Analysis: signals.parquet                                            │
│  ts            ← klines.ts                                           │
│  close         ← klines.close                                        │
│  multiplier    ← step3.multiplier   (经 FibGroup 模型传递)            │
│  direction     ← step3.direction                                     │
│  ratio         ← levels_json → FibGroup.levels[i][0]                 │
│  level_price   ← levels_json → FibGroup.levels[i][1]                 │
│  distance      ← |close - level_price|                               │
│  proximity     ← 1 - distance / (leg_range × proximity_k)            │
│  bounce_rate   ← 回看 N bar 中 level_price 附近弹回次数/触碰次数      │
│  touch_count   ← 回看 N bar 中 level_price 被触碰次数                │
│  volume_ratio  ← 当前 bar volume / 过去 N bar 平均 volume             │
│  consensus     ← 该价位附近有多少个独立 (mult,dir) Fib 组共振          │
│  approach      ← 从上/从下/在线上                                     │
│  score_derived ← proximity×2 + bounce×1.5 + vol×0.5 + consensus×1    │
│                   + ratio_importance×0.5                              │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 附录: 关键追溯路径

### 从信号追溯到原始聚类

```
signals.parquet 中某条信号:
  ts=1719792000, level_price=3449.32, ratio=0.236, multiplier=1, direction=up

  1. 在 step3_fib_groups.parquet 中找:
     effective_ts <= 1719792000 AND multiplier=1 AND direction='up'
     AND invalidated_ts IS NULL OR invalidated_ts > 1719792000
     → 找到对应 Fib 组 (leg_low=3350, leg_high=3480)

  2. 该组的 cluster_centers_json:
     [[3362.45, 2.85], [3398.20, 1.40], [3425.60, 3.10], [3448.90, 2.25], [3470.30, 1.80]]

  3. level_price=3449.32 最近的 cluster center = 3448.90 (距离 0.42)
     → span = 3480-3350 = 130, 0.42/130 = 0.3% < 2% 容差
     → 该 Fib 线对齐了 center=3448.90 (conf=2.25) 这个聚合价格

  4. center=3448.90 来源于 cluster_prices:
     是多个 conf_high >= min_conf 的拐点价格的加权平均
     → 追溯到 step2 的 conf_high > 0 的 bar 群
     → 追溯到 step1 中被 pivot/zigzag/regression 标记的拐点 bar
```

### 从 Fib 组追溯到拟合锚点

```
step3 中某 Fib 组: leg_low=3350, leg_high=3480, direction=up

  fit_fib_grid_to_clusters 使用两个 cluster center 解出 (high, low):
    假设 center_i=3425.60 对应 ratio=0.382, center_j=3448.90 对应 ratio=0.236
    _solve_hl(3448.90, 0.236, 3425.60, 0.382, "up"):
      span = (3448.90 - 3425.60) / (0.382 - 0.236) = 23.3 / 0.146 = 159.6
      high = 3448.90 + 159.6 × 0.236 = 3486.57
      low = 3486.57 - 159.6 = 3326.97

  这两个 center 就是定位该 Fib 组的"锚点聚类"。
  通过 cluster_centers_json 可以完整还原。
```
