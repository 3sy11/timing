# Fib Retracement 方案设计文档

> 生成日期: 2026-08-11 | 模块路径: `timing/computation/algo/fib_retracement/`

---

## 1. 方案概述

本模块实现**自适应多周期 Fibonacci 回撤线计算**，核心理念：

1. **多信号源融合拐点识别** — Pivot、ZigZag、线性回归偏离三种方法加权投票
2. **聚类合并** — 相近价格的拐点聚类为支撑/阻力中心
3. **自下而上 Fib 网格拟合** — 从聚类反推最佳 (high, low) 使内层 5 线对齐真实支撑阻力
4. **6 组独立生命周期管理** — 3 个时间倍数 × 2 个方向，各自独立存活与失效
5. **衍生数据** — `fib.parquet`（逐 bar 前向填充）、`zone.parquet`（持久价位簇）

---

## 2. 数据流总览

```
klines (OHLCV)
    │
    ▼
┌──────────────────────────────────────┐
│  Stage 1: 拐点识别 (tag_pivots /     │
│           tag_zigzag / tag_regression)│
└────────────────┬─────────────────────┘
                 │ feature_df + wmap
                 ▼
┌──────────────────────────────────────┐
│  Stage 2: 置信度融合                  │
│           (compute_confidence)        │
└────────────────┬─────────────────────┘
                 │ conf_high, conf_low
                 ▼
┌──────────────────────────────────────┐
│  Stage 3: 价格聚类                    │
│           (cluster_prices × 2)        │
└────────────────┬─────────────────────┘
                 │ clusters_high_df, clusters_low_df
                 ▼
┌──────────────────────────────────────┐
│  Stage 4: Fib 网格拟合               │
│  逐 bar 推进，6 组独立生命周期:       │
│  · fit_fib_grid_to_clusters          │
│  · boundary_break 失效               │
│  · vacancy_fill 空置重试              │
└────────────────┬─────────────────────┘
                 │
        ┌────────┼────────┐
        ▼        ▼        ▼
   result     fib.parquet  zone.parquet
  .parquet    (前向填充)    (价位簇聚合)
```

---

## 3. Stage 1: 拐点识别

三种独立算法各自标注拐点候选位置：

### 3.1 Pivot 高低点 (`tag_pivots`)

- 参数: `pivot_windows = [[left, right], ...]`，如 `[[5,5], [8,8]]`
- 逻辑: 位置 i 的 high 是窗口 `[i-left, i+right]` 内最大值 → 标记为 pivot_high
- 产出列: `pivot_high_5x5`, `pivot_low_5x5`, `pivot_high_8x8`, `pivot_low_8x8`

### 3.2 ZigZag (`tag_zigzag`)

- 参数: `zigzag_thresholds = [0.05, 0.10]`（幅度百分比）
- 逻辑: 价格反转超过阈值时确认前一极值为拐点（交替 high/low）
- 产出列: `zigzag_high_5`, `zigzag_low_5`, `zigzag_high_10`, `zigzag_low_10`

### 3.3 线性回归偏离 (`tag_regression`)

- 参数: `regression_windows = [50, 100]`
- 逻辑: 窗口内做线性回归，当残差 > 2σ 标记 high，< -2σ 标记 low
- 产出列: `reg_high_50`, `reg_low_50`, `reg_high_100`, `reg_low_100`

---

## 4. Stage 2: 置信度融合

```python
compute_confidence(feature_df, wmap, weights)
```

- `wmap`: 列名 → 方法 key 的映射（如 `pivot_high_5x5 → pivot_5`）
- `weights`: 方法 key → 权重（如 `pivot_5: 0.5, pivot_8: 1.0, zigzag_10: 1.0`）
- 输出: `conf_high` / `conf_low` ∈ [0, 1]，值越大表示该 bar 越可能是拐点

**含义**: conf=1.0 意味着所有检测方法一致认可该点为拐点。

---

## 5. Stage 3: 价格聚类

```python
cluster_prices(feature_df, kind="high", tolerance_pct, min_conf)
```

- 按 `conf_high >= min_conf` 过滤出候选拐点
- 按价格排序，相邻差 ≤ `price_range × tolerance_pct` 则合并
- 每个聚类输出 `center`（置信加权中心）、`hit_count`、`total_conf`

**目的**: 相近的高点/低点合并为一个显著的支撑/阻力价位。

---

## 6. Stage 4: 6 组 Fib 独立生命周期 (`pipeline.py`)

### 6.1 核心概念：6 组 = 3 multiplier × 2 direction

| multiplier | 含义 | recent_bars 基数 |
|:-:|:--|:--|
| 1 | 短周期 | `cfg.recent_bars × 1` |
| 2 | 中周期 | `cfg.recent_bars × 2` |
| 3 | 长周期 | `cfg.recent_bars × 3` |

每个 multiplier 同时维护 up 和 down 两个方向 → 共 6 组。

### 6.2 Fib 网格拟合 (`fit_fib_grid_to_clusters`)

**自下而上拟合**（非传统"找极值画 Fib"）：

1. 从当前窗口聚类中心取任意 2 点
2. 假设它们分别对应内层 5 条线中的某 2 条（穷举 C(5,2)=10 种分配）
3. 反解出对应的 `(high, low)` → 数学推导 0% / 100% 位置
4. 评分: 计算所有聚类中心与该网格 5 条线的对齐度
5. 取分数最高的网格作为该 (mult, dir) 的 Fib 组

**评分公式** (`_score_fit`):
```
对齐度 = Σ (conf_i × (1 - dist_i/tol)) × coverage_ratio
coverage_ratio = 被覆盖的 ratio 数 / 5
```

### 6.3 生命周期管理

```
初始化(start_pos) → 逐 bar 推进:
  ├─ 活跃组: boundary_break 检测
  │    └─ close 连续 N bar 超出 [leg_low, leg_high] → 杀死 + 重算
  └─ 空置组: 每 vacancy_retry_interval bar 重试一次
```

- **boundary_break**: close > leg_high 或 close < leg_low 连续 `invalidate_break_bars` 次
- **重算**: 在当前 bar 位置重新 fit，过 `min_fit_score` 门槛则上线
- **空置重试**: 没有活跃组时，按间隔尝试重新拟合

### 6.4 输出: `result.parquet`

| 字段 | 类型 | 含义 |
|:--|:--|:--|
| effective_ts | INT64 | 该组生效时间（bar ts） |
| multiplier | INT64 | 时间倍数 (1/2/3) |
| direction | VARCHAR | 趋势方向 (up/down) |
| score | FLOAT64 | 拟合评分 |
| leg_start_ts | INT64 | 窗口起始 bar ts |
| leg_end_ts | INT64 | 窗口结束 bar ts |
| leg_low | FLOAT64 | Fib 网格下界 (100% 线) |
| leg_high | FLOAT64 | Fib 网格上界 (0% 线) |
| levels_json | VARCHAR | 7 条线: `[[ratio, price], ...]` |
| invalidated_ts | FLOAT64 | 失效时间（NULL=仍活跃） |
| invalidate_reason | VARCHAR | 失效原因 |

---

## 7. 衍生数据

### 7.1 `fib.parquet` — 逐 bar 前向填充

将 `result.parquet` 中每个 (effective_ts, multiplier, direction) 的 7 条 Fib 线前向填充到所有 kline bar，形成连续时间序列。

| 字段 | 含义 |
|:--|:--|
| ts | bar 时间戳 |
| multiplier | 1/2/3 |
| direction | up/down |
| fib_0.000 ~ fib_1.000 | 7 条 Fib 线价位 |

**特点**: 每组每 bar 一行，值不变直到该组失效后被新组替换。

### 7.2 `zone.parquet` — 持久价位簇

**目的**: 把 6 组共 42 条 Fib 线中**价位相近的线聚合为 Zone**，形成具有上下边界和中心线的价格带。

**算法**:
1. 按 `effective_ts` 划分时间片段（每次 Fib 组集合变化为一个新片段）
2. 每个片段收集当时 6 组的全部 42 条线（含 0% 和 100%）
3. 按价位排序后相邻聚类（容差 = 参考 close × 0.5%）
4. 每个簇输出一条 zone 记录

| 字段 | 含义 |
|:--|:--|
| start_ts | zone 生效起始 |
| end_ts | zone 生效结束（下个片段起始） |
| zone_low | 簇内最低价 |
| zone_high | 簇内最高价 |
| zone_mid | 簇内中位价格 |
| zone_width | 上下界宽度 (点数) |
| consensus | 簇内 Fib 线数量 |
| unique_groups | 涉及的独立 (mult,dir) 组数 |
| hit_mults / hit_dirs / hit_ratios | 元数据 |
| zone_lines_json | 簇内各线明细 |

**含义**: consensus 越高 = 越多独立信号在此价位汇聚 → 支撑/阻力越强。

---

## 8. Profile 配置体系

配置优先级: **DEFAULTS < profile.toml < CLI overrides**

### 8.1 已有 Profile

| Profile | 定位 | 关键差异 |
|:--|:--|:--|
| `base_v1` | 基础保守 | 标准参数，top_n=6 |
| `fib001` | 短期敏感 | pivot=[3,5], zigzag=[3%,5%], recent=60, span≥2% |
| `fib002` | 中期平衡 | pivot=[5,8], zigzag=[5%,10%], recent=90, span≥3% |
| `fib003` | 长期稳健 | pivot=[8,13], zigzag=[8%,15%], recent=150, span≥5% |
| `fib004` | 宽松聚类 | cluster_tol=1.2%, min_conf=0.15, top_n=10 |
| `fib005` | 超长周期 | pivot=[13,21], zigzag=[10%,20%], recent=200, span≥8% |

### 8.2 关键参数说明

| 参数 | 默认值 | 作用 |
|:--|:--|:--|
| `pivot_windows` | [[5,5],[8,8]] | Pivot 检测的左右窗口 |
| `zigzag_thresholds` | [0.05, 0.10] | ZigZag 反转幅度门槛 |
| `regression_windows` | [50, 100] | 回归窗口 bar 数 |
| `recent_bars` | 90 | 基础回溯 bar 数（×1/×2/×3） |
| `skip_recent` | 10 | 跳过最近 N bar（避免未确认拐点） |
| `min_leg_span_pct` | 0.03 | 最小趋势腿幅度 |
| `top_n` | 6 | 排名保留腿数 |
| `cluster_tolerance_pct` | 0.005 | 聚类容差（价格范围占比） |
| `min_cluster_conf` | 0.3 | 最低置信度门槛 |
| `min_fit_score` | 1.0 | Fib 网格最低拟合分 |
| `invalidate_break_bars` | 3 | 连续突破 N bar 触发失效 |
| `vacancy_retry_interval` | 5 | 空置槽位重试间隔 |
| `trend_min_move_pct` | 0.10 | 趋势前置条件: 最小波幅 |

---

## 9. Grafana 可视化

Dashboard UID: `bfr3s4obyazggb`，4 个面板:

| # | 面板 | 数据源 | 展示内容 |
|:-:|:--|:--|:--|
| 0 | 结构视图：K线 + Fib水平 | fib.parquet + klines | K线 + 6 组 Fib 线 |
| 1 | 信号时间轴 | analysis result | 加权得分 + 移动均线 |
| 2 | 决策视图：净值 + 交易点 | decision result | 回测净值曲线 |
| 3 | K线 + 价位簇(Zone) | zone.parquet + klines | K线 + Zone 色带 |

### Zone 面板 SQL 逻辑

- 每个时间片段取 consensus 最高的 top-N zone
- JOIN kline bar 展开为连续时序（水平线效果）
- 上界/下界之间 fillBelowTo 形成半透明色带
- 中线以虚线展示

---

## 10. 仓库文件结构

```
warehouse/timing/computation/fib_retracement/
└── {compute_id}/              # fib001, fib002, fib003 ...
    └── {symbol}/              # 885003.WI
        └── {interval}/        # 1d
            ├── step1_pivots.parquet     # Stage1 拐点标注
            ├── step2_confidence.parquet # Stage2 置信度
            ├── step3_clusters.parquet   # Stage3 聚类中心
            ├── step4_legs.parquet       # Stage4 趋势腿
            ├── result.parquet           # 主输出：6组Fib生命周期
            ├── fib.parquet              # 衍生：逐bar前向填充
            └── zone.parquet             # 衍生：持久价位簇
```

---

## 11. 设计要点与决策记录

### Q: 为什么用"自下而上拟合"而非传统"极值画 Fib"？

传统方式依赖人工选择高低点，对同一段行情可画出无数种 Fib。本方案反转逻辑：先用多种方法找到**真实有效的支撑阻力价位**（聚类中心），再搜索哪个 Fib 网格最能解释这些价位 → 客观、可重复、无人工干预。

### Q: 为什么是 6 组而非 1 组？

- 不同 multiplier 捕捉不同级别的趋势（短/中/长）
- 同一级别可以同时存在上升回撤和下降回撤
- 6 组独立管理避免互相干扰，各自有生命周期

### Q: Zone 有什么用？

单独看 42 条线太杂乱。Zone 把相近的线聚合为"价格带"，consensus 高的带代表**多个独立信号在此汇聚**，是更可靠的支撑/阻力区域。视觉上用色带包裹 K 线，直观展示价格在哪个 zone 内运行。

### Q: boundary_break 为何设 3 bar？

避免日内假突破误杀。连续 3 bar 收盘在边界外才认为该 Fib 格局已失效，平衡灵敏度与稳定性。

---

## 12. 运行方式

```bash
# 单个 profile 执行
python main.py execute Compute --algo fib_retracement \
    --compute_id fib001 --symbol 885003.WI --interval 1d

# 批量执行 (tmp/run_ana_v2.py)
python tmp/run_ana_v2.py
```

配置通过 profile 文件加载:
```python
cfg = RetracementConfig.from_profile("fib001", overrides=["recent_bars=80"])
```

---

## 附录 A: Fib 标准 Ratio

| Ratio | 含义 |
|:--|:--|
| 0.000 | 趋势起点（up→high, down→low） |
| 0.236 | 浅回撤 |
| 0.382 | 弱回撤 |
| 0.500 | 半回撤 |
| 0.618 | 黄金分割回撤 |
| 0.786 | 深回撤 |
| 1.000 | 趋势终点（up→low, down→high） |

对于 up 方向: `price = high - span × ratio`
对于 down 方向: `price = low + span × ratio`
