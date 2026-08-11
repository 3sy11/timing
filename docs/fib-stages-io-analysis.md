# Fib Retracement 各阶段 输入/输出 字段分析

> 目的：为升级到 v2（密度带方案）前，彻底梳理现有各阶段数据流转

---

## 总览：数据流转链

```
klines (原始OHLCV)
   ↓
Stage 1: 拐点识别 → step1_pivots.parquet (feature_df)
   ↓
Stage 2: 置信度融合 → step2_confidence.parquet
   ↓
Stage 3: 价格聚类 → step3_clusters.parquet
   ↓
Stage 4: Fib拟合 + 生命周期 → step4_legs.parquet + result.parquet
   ↓
衍生: fib.parquet, zone.parquet
```

---

## Stage 1: 拐点识别

### 输入

| 字段 | 来源 | 说明 |
|:--|:--|:--|
| ts | klines | 毫秒时间戳 |
| open | klines | 开盘价 |
| high | klines | 最高价 |
| low | klines | 最低价 |
| close | klines | 收盘价 |
| volume | klines | 成交量 |

### 处理函数

| 函数 | 配置参数 | 作用 |
|:--|:--|:--|
| `tag_pivots(df, pivot_windows)` | `pivot_windows=[[5,5],[8,8]]` | 局部极值检测 |
| `tag_zigzag(df, zigzag_thresholds)` | `zigzag_thresholds=[0.05,0.10]` | 幅度反转标记 |
| `tag_regression(df, regression_windows)` | `regression_windows=[50,100]` | 回归偏离检测 |

### 输出: `step1_pivots.parquet`

| 字段 | 类型 | 含义 | v2处置 |
|:--|:--|:--|:--|
| ts | INT64 | bar 时间戳 | 保留 |
| open/high/low/close/volume | FLOAT64 | OHLCV | 保留 |
| pivot_high_5x5 | FLOAT64 | 5bar窗口内局部最高点价格，NaN=非极值 | **保留** |
| pivot_low_5x5 | FLOAT64 | 5bar窗口内局部最低点价格 | **保留** |
| pivot_high_8x8 | FLOAT64 | 8bar窗口内局部最高点价格 | **保留** |
| pivot_low_8x8 | FLOAT64 | 8bar窗口内局部最低点价格 | **保留** |
| zigzag_high_5 | FLOAT64 | 5%反转确认的高点价格 | **保留** |
| zigzag_low_5 | FLOAT64 | 5%反转确认的低点价格 | **保留** |
| zigzag_high_10 | FLOAT64 | 10%反转确认的高点价格 | **保留** |
| zigzag_low_10 | FLOAT64 | 10%反转确认的低点价格 | **保留** |
| reg_high_50 | FLOAT64 | 50bar回归>2σ偏离的高点 | **保留** |
| reg_low_50 | FLOAT64 | 50bar回归<-2σ偏离的低点 | **保留** |
| reg_high_100 | FLOAT64 | 100bar回归偏离高点 | **保留** |
| reg_low_100 | FLOAT64 | 100bar回归偏离低点 | **保留** |

**下游用途**: wmap（列→方法key映射）传入 Stage 2 计算置信度

---

## Stage 2: 置信度融合

### 输入

| 字段 | 来源 | 说明 |
|:--|:--|:--|
| feature_df | Stage 1 输出 | 含所有拐点标注列 |
| wmap | Stage 1 副产物 | `{col_name: method_key}` 映射 |
| weights | config | `{method_key: weight}` 如 `pivot_8: 1.0` |

### 处理函数

```python
compute_confidence(feature_df, wmap, weights)
```

逻辑: 对每个 bar，遍历所有拐点列，有值(非NaN)则累加该方法的 weight，最终归一化到 [0, 1]。

### 输出: `step2_confidence.parquet`

| 字段 | 类型 | 含义 | v2处置 |
|:--|:--|:--|:--|
| ts | INT64 | bar 时间戳 | 保留 |
| high | FLOAT64 | 最高价（作为 high 拐点的价格） | 保留 |
| low | FLOAT64 | 最低价（作为 low 拐点的价格） | 保留 |
| close | FLOAT64 | 收盘价 | 保留 |
| conf_high | FLOAT64 | 该 bar 作为高点的置信度 [0,1] | **保留** |
| conf_low | FLOAT64 | 该 bar 作为低点的置信度 [0,1] | **保留** |

**下游用途**:
- conf_high >= min_conf 的 bar 提取 high 价格 → Stage 3 聚类
- conf_low >= min_conf 的 bar 提取 low 价格 → Stage 3 聚类
- v2: conf_high 和 conf_low 合并进入全局密度图

---

## Stage 3: 价格聚类

### 输入

| 字段 | 来源 | 说明 |
|:--|:--|:--|
| feature_df | Stage 2 输出 | 含 conf_high, conf_low |
| kind | 调用参数 | `"high"` 或 `"low"` — **分别调用两次** |
| tolerance_pct | config | 聚类容差，默认 0.005 |
| min_conf | config | 最低置信度门槛，默认 0.3 |

### 处理函数

```python
clusters_high_df = cluster_prices(feature_df, "high", tolerance_pct, min_conf)
clusters_low_df = cluster_prices(feature_df, "low", tolerance_pct, min_conf)
```

逻辑: 按价格排序后相邻聚类，价格差 ≤ price_range × tolerance_pct 则合并。

### 输出: `step3_clusters.parquet`（high + low 合并存储）

| 字段 | 类型 | 含义 | v2处置 |
|:--|:--|:--|:--|
| kind | VARCHAR | `"high"` 或 `"low"`，表示来源类型 | **废弃** → 全局合并不区分 |
| center | FLOAT64 | 聚类中心价格（置信加权均值） | **保留** → 成为 density_band.center |
| hit_count | INT64 | 落入该簇的拐点个数 | **保留** → 成为 density_band.hit_count |
| total_conf | FLOAT64 | 落入该簇所有拐点的置信度之和 | **保留** → 成为 density_band.total_conf |
| last_index | INT64 | 最后一个拐点的 df 行索引 | **保留** → 用于计算 time_span |
| last_ts | INT64 | 最后一个拐点的时间戳 | **保留** → 成为 last_touch_ts |

**下游用途**:
- clusters_high_df.center → 作为 Stage 4 `fit_fib_grid_to_clusters` 的输入
- clusters_low_df.center → 同上
- 两者合并为 `centers = [(price, conf), ...]` 传入拟合

### v2 核心改造点

```
旧: cluster_prices("high") + cluster_prices("low") → 分别得到两个列表 → 合并传入 Fib 拟合
新: build_price_density(feature_df) → 一次性全局聚类 → 输出 density_bands
    - high 和 low 拐点的价格放在一起
    - 新增字段: has_high, has_low, is_bidirectional, time_span_ratio, band_strength
```

---

## Stage 4: Fib 网格拟合 + 6组生命周期

这是最复杂的阶段，**也是 v2 改动最大的阶段**。拆解为两个子步骤：

### 4a. Fib 网格拟合 (`_compute_fib_at`)

#### 输入

| 字段 | 来源 | 说明 |
|:--|:--|:--|
| feature_df[:end_idx] | Stage 2 完整 feature_df 的切片 | 截止到当前 bar 的数据 |
| target_keys | 循环变量 | `{(mult, direction)}` 指定计算哪组 |
| cfg.recent_bars | config | 基础窗口长度 |
| cfg.min_leg_span_pct | config | 最小腿幅度 |
| cfg.min_fit_score | config | 最低拟合分 |

#### 内部流程

```
1. 窗口切片: recent_df = effective_df[actual_start:] (长度 = recent_bars × mult)
2. 窗口内聚类: cluster_prices(recent_df, "high") + cluster_prices(recent_df, "low")
3. 合并聚类中心: centers = [(price, conf), ...] 排序
4. 穷举拟合: fit_fib_grid_to_clusters(centers, direction)
   - 任取2个center假设对应内层5线中的某2条
   - 反解 (high, low) → 检查其余center的对齐度
   - 取分最高的 (high, low)
5. 生成7条线: levels_from_hl(high, low, direction)
```

#### 输出（单次调用返回1条记录）

| 字段 | 类型 | 含义 | v2处置 |
|:--|:--|:--|:--|
| effective_ts | INT64 | 该组生效时间 | **改为** density_band 的 ts |
| multiplier | INT64 | 时间倍数 (1/2/3) | **废弃** → 不再按组产出 |
| direction | VARCHAR | up/down | **改为** fib_direction（可选标注） |
| score | FLOAT64 | 拟合评分（聚类对齐度） | **改为** band_strength |
| leg_start_ts | INT64 | 窗口起始 bar ts | **废弃** |
| leg_end_ts | INT64 | 窗口结束 bar ts | **废弃** |
| leg_low | FLOAT64 | 反推的 Fib 腿下界 | **改为** fib_leg_low（可选） |
| leg_high | FLOAT64 | 反推的 Fib 腿上界 | **改为** fib_leg_high（可选） |
| levels_json | VARCHAR | 7 条线 `[[ratio, price], ...]` | **废弃** → 由 density_band + fib_ratio 替代 |
| invalidated_ts | FLOAT64 | 失效时间 | **保留** 含义不变 |
| invalidate_reason | VARCHAR | 失效原因 | **保留** 增加 `replaced` 类型 |

### 4b. 6 组生命周期管理 (`run_pipeline` 主循环)

#### 管理状态

| 变量 | 含义 | v2处置 |
|:--|:--|:--|
| active[key] | 当前活跃的 (mult, dir) 组记录 | **废弃** → 改为 active_bands[] |
| break_counts[key] | 连续突破边界计数 | **保留** 但基于 band 而非 (mult,dir) |
| vacancy_counters[key] | 空置重试计数 | **废弃** → 改为 recalc_interval |

#### 逐 bar 检测逻辑

```
对每个活跃组:
  if close > leg_high or close < leg_low:
    break_count += 1
    if break_count >= invalidate_break_bars:
      → 杀死该组，写入 result（带 invalidated_ts）
      → 重算该 (mult, dir) 的 Fib
  else:
    break_count = 0

对空置组:
  vacancy_counter += 1
  if vacancy_counter >= vacancy_retry_interval:
    → 尝试重新 fit
```

#### v2 改造

```
对每个活跃密度带:
  if close 连续 N bar 超出 [band_low-buffer, band_high+buffer]:
    → boundary_break → 标记失效
  if 连续 N bar 无 close 进入 [band_low, band_high]:
    → 衰减 band_strength × decay_factor
    → 连续两次衰减 → 标记失效
```

### 输出: `step4_legs.parquet`（辅助诊断，非主输出）

| 字段 | 类型 | 含义 | v2处置 |
|:--|:--|:--|:--|
| start_idx | INT64 | 腿起点在窗口内索引 | **改为** 腿端点注入记录 |
| end_idx | INT64 | 腿终点在窗口内索引 | 同上 |
| start_ts | INT64 | 腿起点时间戳 | 同上 |
| end_ts | INT64 | 腿终点时间戳 | 同上 |
| low | FLOAT64 | 腿低点价格 | **保留** → 注入 density_band |
| high | FLOAT64 | 腿高点价格 | **保留** → 注入 density_band |
| direction | VARCHAR | up/down | 保留 |
| span_pct | FLOAT64 | 腿幅度占比 | 保留（用于过滤） |
| conf_score | FLOAT64 | 腿置信度综合分 | 保留 |
| multiplier | INT64 | 时间倍数 | 保留（标注来源） |

---

## 衍生输出

### fib.parquet — 逐 bar 前向填充

#### 输入: result.parquet
#### 生成逻辑: 每个 (effective_ts, mult, dir) 的 levels_json 展开为 7 列，前向填充到该组存活的所有 bar

| 字段 | 类型 | 含义 | v2处置 |
|:--|:--|:--|:--|
| ts | INT64 | bar 时间戳 | 保留 |
| multiplier | INT64 | 1/2/3 | **废弃**，统一填 1 |
| direction | VARCHAR | up/down | 保留（fib_direction） |
| fib_0.000 | FLOAT64 | 0%线价位 (leg_high 或 leg_low) | 保留（有 Fib 解释时填入） |
| fib_0.236 | FLOAT64 | 23.6%线价位 | 保留 |
| fib_0.382 | FLOAT64 | 38.2%线价位 | 保留 |
| fib_0.500 | FLOAT64 | 50%线价位 | 保留 |
| fib_0.618 | FLOAT64 | 61.8%线价位 | 保留 |
| fib_0.786 | FLOAT64 | 78.6%线价位 | 保留 |
| fib_1.000 | FLOAT64 | 100%线价位 | 保留 |
| *center* | FLOAT64 | (新增) 密度带中心 | **新增** |
| *band_strength* | FLOAT64 | (新增) 带强度 | **新增** |
| *fib_ratio* | FLOAT64 | (新增) 该行对应的 ratio | **新增** |

### zone.parquet — 持久价位簇

#### 输入: result.parquet（现有）→ v2 改为直接来自 density_bands
#### 当前生成逻辑: 按 effective_ts 片段聚合 42 条 Fib 线

| 字段 | 类型 | 现含义 | v2新含义 |
|:--|:--|:--|:--|
| start_ts | INT64 | zone 生效起始 | 不变 |
| end_ts | INT64 | zone 生效结束 | 不变 |
| zone_low | FLOAT64 | 簇内最低 Fib 线价格 | → band_low |
| zone_high | FLOAT64 | 簇内最高 Fib 线价格 | → band_high |
| zone_mid | FLOAT64 | 簇内中位数 | → center |
| zone_width | FLOAT64 | high-low | 不变 |
| consensus | INT64 | 簇内 Fib 线数量(含重复) | → hit_count(真实独立信号) |
| unique_groups | INT64 | 涉及的(mult,dir)组数 | → 时间段层级数 |
| hit_mults | VARCHAR | JSON: 参与的 multiplier | 改为 fib_ratio 标注 |
| hit_dirs | VARCHAR | JSON: 参与的 direction | 保留 |
| hit_ratios | VARCHAR | JSON: 参与的 ratio | 改为 fib_ratio |
| zone_lines_json | VARCHAR | 簇内所有线明细 | 废弃 |

---

## 关键问题总结（对照 v2 修订计划）

| # | 现有问题 | 现有字段表现 | v2 解决方式 |
|:-:|:--|:--|:--|
| 1 | 6组不独立，共享底层数据 | step3 对窗口内数据分别聚类，multiplier 只是窗口大小不同 | 全局聚类一次，multiplier 只贡献腿端点 |
| 2 | consensus 虚高 | zone.consensus=42 实际是同一腿被3窗口重复计算 | hit_count 基于真实独立拐点数 |
| 3 | kind 分离丢失双向信息 | step3 分 "high"/"low" 两组聚类 | 合并聚类，新增 is_bidirectional |
| 4 | Fib 是决定者 | result.levels_json 42条线先画出来 | Fib 只标注已有的 density_band |
| 5 | 42条线噪音 | fib.parquet 6组×7=42行/bar | 最终 ≤10 个 band，信息清晰 |
| 6 | 生命周期粒度太粗 | 一条线 break → 整组(7线)失效 | 每个 band 独立生命周期 |

---

## 简化路线图（实施优先级）

```
Phase 1 (保留):  Stage 1 + Stage 2 → 代码不动，输出不变
Phase 2 (重写):  Stage 3 → build_price_density() 全局合并聚类
Phase 3 (新增):  Stage 3b → inject_leg_endpoints() 腿端点注入
Phase 4 (重写):  Stage 4 → explain_with_fib() Fib 解释层
Phase 5 (重写):  pipeline.py → 密度带生命周期（替代6组管理）
Phase 6 (适配):  输出格式 + Grafana Dashboard
```
