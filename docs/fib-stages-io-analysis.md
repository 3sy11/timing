# Fib Retracement v3 各阶段 输入/输出 字段分析

> 基于 fib_revision_plan_v3.docx 实施后的当前方案

---

## 总览：数据流转链

```
klines (原始OHLCV)
   ↓
Stage 1: 拐点识别 (保留不变)
         tag_pivots / tag_zigzag / tag_regression
   ↓  → feature_df + wmap
Stage 2: 置信度融合 (保留不变)
         compute_confidence(feature_df, wmap, weights)
   ↓  → feature_df（含 conf_high / conf_low）
Stage 3: 价格线聚合 (v3 重构)
         aggregate_price_lines(window_df, cfg)
   ↓  → List[PriceLine]  ×3 multiplier
   ↓  → step3_price_lines.parquet
Stage 4: Fib 拟合与解释 (v3 重构)
         fit_fib_to_price_lines(price_lines, cfg)
   ↓  → FibResult（含 FibLevel × 7, 每条标注锚点）
   ↓  → step4_fib_result.parquet
输出层:
   ├── result.parquet      (主输出, 每 multiplier 一行)
   ├── fib.parquet         (前向填充, 逐bar×fib线)
   └── zone.parquet        (价格线 → 直接作为 zone)
```

---

## Stage 1: 拐点识别（保留不变）

### 输入

| 字段 | 来源 | 说明 |
|:--|:--|:--|
| ts | klines | 毫秒时间戳 |
| open/high/low/close | klines | OHLC 价格 |
| volume | klines | 成交量 |

### 处理函数

| 函数 | 配置参数 | 作用 |
|:--|:--|:--|
| `tag_pivots(df, pivot_windows)` | `pivot_windows=[[5,5],[8,8]]` | 局部极值检测 |
| `tag_zigzag(df, zigzag_thresholds)` | `zigzag_thresholds=[0.05,0.10]` | 幅度反转标记 |
| `tag_regression(df, regression_windows)` | `regression_windows=[50,100]` | 回归偏离检测 |

### 输出字段（feature_df 新增列）

| 字段 | 类型 | 含义 |
|:--|:--|:--|
| pivot_high_5x5 | FLOAT64 | 5bar窗口内局部最高点价格，NaN=非极值 |
| pivot_low_5x5 | FLOAT64 | 5bar窗口内局部最低点价格 |
| pivot_high_8x8 | FLOAT64 | 8bar窗口 |
| pivot_low_8x8 | FLOAT64 | 8bar窗口 |
| zigzag_high_5 | FLOAT64 | 5%反转确认的高点 |
| zigzag_low_5 | FLOAT64 | 5%反转确认的低点 |
| zigzag_high_10 | FLOAT64 | 10%反转确认 |
| zigzag_low_10 | FLOAT64 | 10%反转确认 |
| reg_high_50 | FLOAT64 | 50bar回归>2σ偏离 |
| reg_low_50 | FLOAT64 | 50bar回归<-2σ |
| reg_high_100 | FLOAT64 | 100bar回归偏离 |
| reg_low_100 | FLOAT64 | 100bar回归偏离 |

副产物: `wmap = {col_name: method_key}` 映射表

---

## Stage 2: 置信度融合（保留不变）

### 输入

| 字段 | 来源 | 说明 |
|:--|:--|:--|
| feature_df | Stage 1 输出 | 含所有拐点标注列 |
| wmap | Stage 1 副产物 | `{col_name: method_key}` |
| weights | config | `{method_key: weight}` 如 `pivot_8: 1.0` |

### 处理函数

```python
compute_confidence(feature_df, wmap, weights)
```

逻辑: 对每个 bar，遍历所有拐点列，有值(非NaN)则累加该方法的 weight，最终归一化到 [0, 1]。

### 输出（feature_df 新增列）

| 字段 | 类型 | 含义 |
|:--|:--|:--|
| conf_high | FLOAT64 | 该 bar 作为高点的置信度 [0, 1] |
| conf_low | FLOAT64 | 该 bar 作为低点的置信度 [0, 1] |

**下游用途**: conf_high / conf_low 作为 Stage 3 的候选拐点来源

---

## Stage 3: 价格线聚合（v3 重构）

### 核心理念

- high/low 拐点**全部合并**到一个列表，不区分来源
- tolerance 用**相对值** = price_range × cluster_tolerance_pct
- N（输出数量）完全由参数决定，不是固定值
- 每条价格线是独立有效的支撑/阻力位，不依赖 Fib

### 输入

| 字段 | 来源 | 说明 |
|:--|:--|:--|
| feature_df（窗口切片） | Stage 2 输出 | 含 conf_high, conf_low, high, low, ts |
| cfg.min_cluster_conf | config | 最低置信度门槛（进入候选的条件） |
| cfg.cluster_tolerance_pct | config | 聚类容差百分比 |
| cfg.max_price_lines | config | 输出上限 |
| cfg.min_line_strength | config | 最低强度门槛 |

### 窗口策略

每个 multiplier 独立调用一次 `aggregate_price_lines`:

| multiplier | 数据窗口 | 典型价格线数 | 特点 |
|:--|:--|:--|:--|
| ×1 | recent_bars × 1 | 2-5条 | 近期结构，反应快 |
| ×2 | recent_bars × 2 | 5-8条 | 主力参考，稳定 |
| ×3 | recent_bars × 3 | 6-10条 | 长期结构，覆盖广 |

### 处理函数

```python
aggregate_price_lines(feature_df: pd.DataFrame, cfg) -> List[PriceLine]
```

**五步逻辑:**

1. **收集候选拐点**: conf_high ≥ min_conf → 取 high 价格; conf_low ≥ min_conf → 取 low 价格
2. **按价格排序 + 滑动合并**: `tol = price_range × cluster_tolerance_pct`，以加权中心判断距离（避免链式漂移）
3. **计算指标**: center(加权均值) / hit_count / total_conf / time_span_ratio / has_high/has_low / line_strength
4. **过滤**: line_strength ≥ min_line_strength
5. **截取**: Top-N (max_price_lines)

**强度公式:**
```
line_strength = total_conf × (1 + time_span_ratio) × (1.5 if is_bidirectional else 1.0)
```

### 输出: `step3_price_lines.parquet`

| 字段 | 类型 | 含义 |
|:--|:--|:--|
| compute_ts | INT64 | 本次计算时间戳 |
| multiplier | INT64 | 来自哪个倍数窗口 (1/2/3) |
| center | FLOAT64 | 置信度加权均值，代表价格 |
| tolerance | FLOAT64 | 有效吸引范围 = price_range × cluster_tolerance_pct |
| hit_count | INT64 | 落入该线的拐点总数 (high + low 合并) |
| total_conf | FLOAT64 | 置信度加权和 |
| time_span_ratio | FLOAT64 | (last_idx - first_idx) / 窗口长度 ∈ [0, 1] |
| has_high | BOOL | 包含 high 拐点 |
| has_low | BOOL | 包含 low 拐点 |
| is_bidirectional | BOOL | has_high AND has_low（双向记忆，最强信号） |
| line_strength | FLOAT64 | 综合强度分 |
| first_touch_ts | INT64 | 最早拐点时间戳 |
| last_touch_ts | INT64 | 最近拐点时间戳 |
| first_touch_idx | INT64 | 最早拐点在窗口内的索引 |
| last_touch_idx | INT64 | 最近拐点在窗口内的索引 |

**下游用途**: 直接传入 Stage 4 作为 Fib 拟合输入；直接写入 zone.parquet 作为 zone

---

## Stage 4: Fib 拟合与解释层（v3 重构）

### 核心理念

- Fib 是**解释者**：用价格线的分布来寻找最优 Fib 网格
- 每条 Fib 线追溯到对应的价格线来源（有锚点）或标记为推算（无锚点）
- fib_quality 量化拟合可信度，低于阈值时 price_lines 仍然有效输出

### 输入

| 字段 | 来源 | 说明 |
|:--|:--|:--|
| price_lines | Stage 3 输出 | 当前 multiplier 的 PriceLine 列表 |
| cfg.top_lines_for_fit | config | 参与组合的价格线数量上限 |
| cfg.std_ratios | config | [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0] |
| cfg.max_ratio_error | config | ratio 误差容忍上限 (0.05) |
| cfg.min_leg_span_pct | config | 最小腿跨度 (0.03) |
| cfg.min_fib_quality | config | 最低拟合质量 (0.2) |

### 处理函数

```python
fit_fib_to_price_lines(price_lines: List[PriceLine], cfg) -> FibResult
```

**四步逻辑:**

1. **候选 (H, L) 对生成**: 从 Top-K 价格线两两组合，span/L ≥ min_leg_span_pct
2. **对齐评分**: 对每对 (H, L)，计算所有价格线与 Fib ratio 的匹配度
   ```
   score = H_strength + L_strength + Σ(pl.strength × (1 - error/max_error))
   ```
3. **fib_quality 计算**: `best_score / Σ(all line_strength)`，归一化 ∈ [0, 1]
4. **生成 FibLevel + 锚点标注**: 对每个标准 ratio，计算 fib_price，在 price_lines 中查找 tolerance 范围内最强的锚点

### 输出: `step4_fib_result.parquet`

| 字段 | 类型 | 含义 |
|:--|:--|:--|
| compute_ts | INT64 | 本次计算时间戳 |
| multiplier | INT64 | 来自哪个倍数窗口 |
| fib_quality | FLOAT64 | 拟合质量 ∈ [0, 1]，越高解释力越强 |
| leg_high | FLOAT64 | Fib 腿高点 |
| leg_low | FLOAT64 | Fib 腿低点 |
| is_valid | BOOL | fib_quality ≥ min_fib_quality |
| ratio | FLOAT64 | 标准 Fib 比率 (0.0 ~ 1.0) |
| price | FLOAT64 | 该比率对应的实际价格 |
| is_anchored | BOOL | True=有价格线来源, False=纯推算 |
| anchor_center | FLOAT64/null | 来源价格线的 center |
| anchor_strength | FLOAT64 | 来源价格线的 line_strength (无锚点时为 0) |

**每个 multiplier 产出 7 行**（7 条标准 Fib 线）

### 拟合失败处理

当 `fib_quality < min_fib_quality` 时:
- `is_valid = False`
- price_lines 仍然正常输出（价格线独立于 Fib）
- zone.parquet 不受影响（直接来自 price_lines）

---

## 输出层

### result.parquet（主输出，每 multiplier 一行）

| 字段 | 类型 | 含义 |
|:--|:--|:--|
| effective_ts | INT64 | 生效时间 |
| multiplier | INT64 | 时间倍数 (1/2/3) |
| direction | VARCHAR/null | v3 不区分方向，固定 null |
| fib_quality | FLOAT64 | 拟合质量 |
| is_valid | BOOL | Fib 拟合是否有效 |
| leg_high | FLOAT64 | Fib 腿高点 |
| leg_low | FLOAT64 | Fib 腿低点 |
| levels_json | VARCHAR | 7条线 JSON: `[{ratio, price, is_anchored, anchor_center, anchor_strength}]` |
| price_lines_json | VARCHAR | 本次全部价格线 JSON: `[{center, line_strength, hit_count, is_bidirectional}]` |
| invalidated_ts | INT64/null | 失效时间 |
| invalidate_reason | VARCHAR/null | 失效原因 |

### fib.parquet（前向填充，逐 bar × Fib 线）

| 字段 | 类型 | 含义 |
|:--|:--|:--|
| ts | INT64 | bar 时间戳 |
| multiplier | INT64 | 来源窗口 |
| ratio | FLOAT64 | Fib 比率 |
| price | FLOAT64 | Fib 价格 |
| is_anchored | BOOL | 是否有锚点 |
| anchor_strength | FLOAT64 | 锚点强度 |

生成逻辑: `is_valid=True` 的 Fib 线 × 窗口内所有 bar 时间戳，前向填充。

### zone.parquet（直接来自 price_lines）

| 字段 | 类型 | 含义 |
|:--|:--|:--|
| compute_ts | INT64 | 计算时间戳 |
| multiplier | INT64 | 来源窗口 |
| zone_mid | FLOAT64 | = price_line.center |
| zone_low | FLOAT64 | = center - tolerance |
| zone_high | FLOAT64 | = center + tolerance |
| consensus | INT64 | = hit_count（真实拐点命中数） |
| line_strength | FLOAT64 | 综合强度 |
| is_bidirectional | BOOL | 双向记忆 |
| has_high | BOOL | 含压力信号 |
| has_low | BOOL | 含支撑信号 |

zone 直接来自 price_lines，不再是 42 条 Fib 线的二次聚合。consensus 含义从「簇内 Fib 线数量」变为「真实拐点命中数」。

---

## 配置参数（v3 当前值）

### Stage 1/2 参数（保留不变）

| 参数 | 默认值 | 作用 |
|:--|:--|:--|
| pivot_windows | [[5,5],[8,8]] | 局部极值窗口 |
| zigzag_thresholds | [0.05, 0.10] | 幅度反转阈值 |
| regression_windows | [50, 100] | 回归窗口 |
| weights | {pivot_5:0.5, pivot_8:1.0, ...} | 方法权重 |

### Stage 3 参数

| 参数 | 默认值 | 作用 | 影响 |
|:--|:--|:--|:--|
| min_cluster_conf | 0.3 | 进入候选的最低置信度 | 越高 → 候选越少 |
| cluster_tolerance_pct | 0.005 | 聚类容差 (× price_range) | 越大 → 合并越多 → N 越小 |
| max_price_lines | 12 | 输出上限 | 硬限制 |
| min_line_strength | 0.5 | 最低强度门槛 | 越高 → 弱线被过滤 → N 越小 |

### Stage 4 参数

| 参数 | 默认值 | 作用 |
|:--|:--|:--|
| top_lines_for_fit | 8 | 参与 (H,L) 组合的价格线数量 |
| max_ratio_error | 0.05 | ratio 误差容忍上限 (5%) |
| min_fib_quality | 0.2 | Fib 拟合最低质量门槛 |
| std_ratios | [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0] | 标准比率列表 |
| min_leg_span_pct | 0.03 | 最小腿跨度 |

### 窗口与生命周期参数

| 参数 | 默认值 | 作用 |
|:--|:--|:--|
| recent_bars | 90 | 基础窗口长度 |
| skip_recent | 10 | 跳过末尾 N 根 bar |
| invalidate_break_bars | 3 | 连续突破 N bar 则失效 |
| band_decay_factor | 0.7 | 无触碰衰减系数 |
| band_decay_n | 0.20 | 触发衰减的无触碰 bar 比例 |
| recalc_interval | 20 | 定期全量重算间隔 |

---

## Profile 配置一览

| Profile | 定位 | max_price_lines | min_line_strength | min_cluster_conf | recent_bars |
|:--|:--|:--|:--|:--|:--|
| fib001 | 短期敏感 | 10 | 0.3 | 0.2 | 60 |
| fib002 | 中期平衡 | 8 | 0.5 | 0.3 | 90 |
| fib003 | 长期稳健 | 6 | 0.5 | 0.5 | 150 |
| fib004 | 宽松聚类 | 15 | 0.2 | 0.15 | 120 |
| fib005 | 超长周期 | 5 | 0.5 | 0.4 | 200 |

---

## 实际产出示例 (885003.WI / 1d / fib002)

### Stage 3 价格线

| mult | center | hit_count | line_strength | 类型 |
|:--|:--|:--|:--|:--|
| 1 | 5020.4 | 1 | 0.67 | 支撑 |
| 1 | 5206.0 | 1 | 0.67 | 压力 |
| 2 | 5152.0 | 2 | 1.86 | 双向 |
| 2 | 4981.8 | 2 | 1.09 | 支撑 |
| 3 | 4981.0 | 5 | 3.57 | 双向 |
| 3 | 4977.2 | 3 | 2.24 | 双向 |
| 3 | 5152.0 | 2 | 1.68 | 双向 |
| 3 | 5044.1 | 2 | 1.31 | 双向 |

### Stage 4 Fib 拟合

| mult | fib_quality | leg_high | leg_low | 锚定数 |
|:--|:--|:--|:--|:--|
| 1 | 1.000 | 5206.0 | 5020.4 | 2/7 |
| 2 | 0.711 | 5152.0 | 4977.4 | 2/7 |
| 3 | 0.689 | 5152.0 | 4981.0 | 4/7 |

---

## 与旧方案的关键差异

| 维度 | 旧方案 (v1) | 当前方案 (v3) |
|:--|:--|:--|
| 核心输入 | 拐点 → 直接拟合6组Fib → 42条线 | 拐点 → 价格线(N条) → Fib解释 |
| 第一公民 | Fib 组 (6组×7条) | 价格线 (N条, 参数可控) |
| Fib 角色 | 决定者: 先画框架 | 解释者: 价格线先存在, Fib 命名它们 |
| N 的控制 | 固定6组 | 完全由聚类参数决定 |
| 强度来源 | 拟合分 (conf_score) | 真实拐点数量 + 置信度 + 时间跨度 |
| 无法 Fib 解释的位置 | 被丢弃 | 依然作为价格线输出 |
| zone 的 consensus | 虚高 (42线重复计数) | 真实 hit_count |
| 锚点追溯 | 无 | 每条Fib线标注 is_anchored + anchor |
| 拟合质量 | 无归一化指标 | fib_quality ∈ [0, 1] |

---

## 文件布局

```
warehouse/timing/computation/fib_retracement/
└── {profile}/
    └── {symbol}/
        └── {interval}/
            ├── step3_price_lines.parquet   (Stage 3 中间结果)
            ├── step4_fib_result.parquet    (Stage 4 中间结果)
            ├── result.parquet             (主输出)
            ├── fib.parquet                (前向填充衍生)
            └── zone.parquet               (价格线直接导出)
```

---

## Grafana 面板

| 面板 | 类型 | 数据来源 | 展示内容 |
|:--|:--|:--|:--|
| Stage3: 价格线聚合 | timeseries | step3_price_lines.parquet | K线 + 3×mult 的价格线水平线 (红/蓝/绿) |
| Stage3: 价格线详情 | table | step3_price_lines.parquet | 窗口/中心/命中数/强度/类型 |
| Stage4: Fib拟合详情 | table | step4_fib_result.parquet | ratio/价格/锚定状态/锚点强度 |
| Stage4: K线+最优Fib网格 | timeseries | step4_fib_result.parquet | K线 + 7条Fib水平线 |
| 输出: Zone色带 | timeseries | zone.parquet | K线 + Top-5价格线的±tolerance色带 |
| 总览: Fib拟合质量 | table | step4_fib_result.parquet | 各窗口的fib_quality/锚定数汇总 |
