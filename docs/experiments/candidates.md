# candidates.parquet 字段含义

> 投产表路径：`warehouse/timing/signals/{analysis_id}/{symbol}/{interval}/candidates.parquet`
>
> 由 `price_touch` 读 **当天 K 线** + **当时仍存活的 `result.parquet` 行** + **`line_events.parquet` 历史事实** 写出。
>
> **一行 = 某根 K 线碰到一条当时还活着的 Fib 线**。没有强度分、没有综合 score。

本表只回答四件事：

1. 这根 K 线碰到了哪条还活着的 Fib 线
2. 这条线自己在拟合前 / 拟合窗里，历史上弹开和蹭线各怎样（两套数分开看）
3. 今天价位附近还有几条别的 Fib 线、几个聚类中心
4. 这些邻居自己拟合前弹开和蹭线怎样（**只用 prefit，不用 fitwin**）

`line_events` 里怎么切带、怎么打 `outcome_atr`，由 Computation 的 `event_*` 冻结；本表只调「当天算不算碰到」「多近算邻居」。

---

## 可调试验参数

写在 `analysis/rules/price_touch/profiles/{profile}.toml`，也可用 `--override proximity_k=0.0015`。**不要**从 Computation 的 `event_*` 抄过来。

| 参数 | 默认 | 管什么 | 怎么调 |
|------|------|--------|--------|
| `proximity_k` | `0.001`（价的 0.1%） | 当天高低点与线价的碰到带宽：`[price×(1−k), price×(1+k)]` | 更大更多候选；更小更严 |
| `neighbor_k` | `0.001` | 邻居带宽：别的存活 Fib 价 / 聚类中心与本线价差 ≤ `price×k` 才算近 | 更大邻居更多 |
| `scan_bars` | `0` | 从最新往回扫几根 K 线。`0` = 全历史 | 调试时可改小 |
| `emit_if_no_prefit` | `true` | 这条线拟合前没有一次 bounce/break 时是否仍出候选 | `false` 则这类行不写，`pre_hold_rate` 不会是 null |

这些参数**不改变** result 里有哪些线，也**不改变** line_events 里已有的 ATR 标签。

---

## 一行是什么

```text
某根 K 线高低点碰到一条当时存活的 Fib 线
    ↓
挂上这条线自己的 prefit / fitwin 弹、磨（来自 line_events）
    ↓
数今天价近的其他 Fib 行、价近的聚类中心
    ↓
邻居的弹、磨只用各邻居自己的 prefit
```

存活判定（PIT，看当时，不看终态）：

```text
effective_ts <= bar.ts
且 (invalidated_ts 为空 或 invalidated_ts > bar.ts)
```

碰到判定：

```text
high/low 与 [level_price × (1 − proximity_k), level_price × (1 + proximity_k)] 相交
```

同一根 K 线碰到 N 条存活线 → N 行。同一条线在不同天被碰到 → 多行，历史统计随当时已有事件更新（事件表本身只含拟合前/窗，不会偷看存活期）。

---

## 当天碰到了谁

| 字段 | 类型 | 含义 |
|------|------|------|
| `ts` | int64 | 这根 K 线时间 |
| `close` | float64 | 这根收盘价 |
| `approach` | str | `from_above` / `from_below`：上一根 close 在线价上方还是下方（第一根则看本根 close） |
| `ratio` | float64 | 碰到的 Fib 比例 |
| `level_price` | float64 | 这条线的价格（`result.price`） |
| `fib_group_id` | str | 组身份，算法与 line_events 相同：`hash(effective_ts, multiplier, direction, round(leg_low,4), round(leg_high,4))` 前 16 位 |
| `multiplier` | int | 1 短期 / 2 中期 / 3 长期 |
| `direction` | str | `up` / `down` |
| `effective_ts` | int64 | 这组开始生效 |
| `invalidated_ts` | int64/null | 这组失效时间；当时仍活着，此值要么为空要么大于 `ts` |
| `compute_id` | str | 上游 Fib 实验 |
| `analysis_id` | str | 本次分析实验 |

拟合描述（`leg_*`、`fib_score`、对齐字段）在 result，本表不重复。

---

## 这条线自己的历史（prefit / fitwin 分开）

全部来自 `line_events`，且 `target_kind=fib`、同一 `fib_group_id + ratio`。

| 字段 | 类型 | 含义 |
|------|------|------|
| `pre_test_n` | int | 拟合前事件数：`bounce + break + weak` |
| `pre_hold_n` | int | 拟合前有意义测试数：`bounce + break`（**不含 weak**） |
| `pre_hold_rate` | float64/null | 拟合前挡住率：`bounce / (bounce + break)`。分母为 0 则为 null |
| `pre_weak_share` | float64/null | 拟合前蹭线占比：`weak / test_n`。没有事件则为 null |
| `fit_test_n` | int | 拟合窗事件数，算法同上 |
| `fit_hold_n` | int | 拟合窗 `bounce + break` |
| `fit_hold_rate` | float64/null | 拟合窗挡住率。**自证，不能单独当「能拐」** |
| `fit_weak_share` | float64/null | 拟合窗蹭线占比 |

聚合约定：

```text
挡住率 hold_rate = bounce / (bounce + break)
蹭线占比 weak_share = weak / (bounce + break + weak)
weak 不进挡住率分母
```

`emit_if_no_prefit=true` 且拟合前没有 bounce/break 时：仍出这一行，`pre_hold_rate` 为 null，`pre_test_n` 可能为 0（全无事件）或等于 weak 次数（只有蹭线）。

---

## 今天价近的其他 Fib 线

「今天」= 这根 K 线当时仍存活的 `result` 行。邻居统计**只用各邻居的 prefit**，不用它们的 fitwin。

| 字段 | 类型 | 含义 |
|------|------|------|
| `nearby_fib_n` | int | 当时存活、且 `|对方 price − 本线价| ≤ 本线价 × neighbor_k` 的**其他** Fib 行数（按 result 行计，不含自己） |
| `nearby_fib_hold_n` | int | 上述邻居里，拟合前 `hold_rate` 非空的条数 |
| `nearby_fib_hold_rate` | float64/null | 这些非空 `hold_rate` 的算术平均 |
| `nearby_fib_weak_n` | int | 上述邻居里，拟合前 `weak_share` 非空的条数 |
| `nearby_fib_weak_share` | float64/null | 这些非空 `weak_share` 的算术平均 |

没有价近邻居时，`nearby_fib_n=0`，四个率/计数为 0 或 null（率为 null）。

---

## 今天价近的聚类中心

中心价来自当时存活行的 `nearest_cluster_center`。按**中心价格**（保留两位）去重，不是按 Fib 行数。含本行自己的中心（若有且价近）。

| 字段 | 类型 | 含义 |
|------|------|------|
| `nearby_cluster_n` | int | 当时存活行上、与本线价差 ≤ `本线价 × neighbor_k` 的**不同**中心价个数 |
| `nearby_cluster_hold_n` | int | 这些中心里，能算出拟合前挡住率的个数 |
| `nearby_cluster_hold_rate` | float64/null | 各中心挡住率的算术平均。单个中心的挡住率 = 映射到该中心的存活行，各自 `target_kind=cluster` 且 `period=prefit` 的 `hold_rate` 再平均 |
| `nearby_cluster_weak_n` | int | 能算出拟合前蹭线占比的中心个数 |
| `nearby_cluster_weak_share` | float64/null | 各中心蹭线占比的算术平均 |

没有价近中心时，`nearby_cluster_n=0`，率为 null。

---

## 这张表没有什么

| 没有 | 原因 |
|------|------|
| `score` / `strength` / 加权综合分 | 本层只挂事实，不做排序分 |
| 存活期弹开统计 | 含未来；事件表本身就不写存活期 |
| 邻居的 fitwin 率 | 拟合自证，邻居只看独立先验 |
| 当天这次碰线的 `outcome_atr` | 当时还没走完 ATR 窗，不属于候选表 |
| `event_*` 参数 | 冻结在 Computation / line_events，本表不回写、不改口径 |

---

## 相关文档

- 实体维：[result_parquet_fields.md](./result_parquet_fields.md)
- 事件事实：[line_events.md](./line_events.md)
- 全管道：[fib_pipeline_reference.md](../fib_pipeline_reference.md)
