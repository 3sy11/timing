# result.parquet 字段含义

> 投产表路径：`warehouse/timing/computation/fib_retracement/{compute_id}/{symbol}/{interval}/result.parquet`
>
> 一行 = **一条 Fib 线**。一组 7 条线打成 7 行，组级字段在这 7 行上完全相同。
>
> 数据来源：`step3_fib_groups.parquet` 经 `_flatten_to_lines` 打平。

---

## 这组线是谁、什么时候有效

| 字段 | 类型 | 含义 |
|------|------|------|
| `effective_ts` | int64 | 这组 Fib **开始生效**的 K 线时间。分析时用 `effective_ts <= bar_ts` 判断“当时已经算出来了”。 |
| `multiplier` | int | 时间尺度：`1` 短期、`2` 中期、`3` 长期。窗口大约是 `recent_bars × multiplier`。 |
| `direction` | str | 拟合方向：`up` 上升回撤，`down` 下降回撤。同一时刻同一尺度可以同时有 up/down 多组。 |
| `fib_score` | float64 | 整组网格拟合分，来自 `_score_fit`。分数越高，聚类中心和 Fib 网格对齐越好。7 行共用同一个值。 |
| `source` | str/null | 这组是怎么上线的：`initial` 启动填槽、`event_break` 旧组失效后补上、`vacancy_fill` 空槽定期补。 |
| `invalidated_ts` | int64/null | 失效时间。`NULL` 表示直到数据末尾仍有效。回测时 `invalidated_ts > bar_ts`（或为空）才算当时还活着。 |
| `invalidate_reason` | str/null | 失效原因，例如 `boundary_break`（价格破出上下边界）、`low_coverage(...)`（内层线长时间没被碰到）。 |

---

## 这组网格的价格框和时间窗

| 字段 | 类型 | 含义 |
|------|------|------|
| `leg_low` | float64 | 这组网格的**价格下沿**，由拟合解出，不是原始拐点。 |
| `leg_high` | float64 | 这组网格的**价格上沿**。跨度 `span = leg_high - leg_low`。 |
| `leg_start_ts` | int64 | 拟合用的历史窗口首时间，只说明“用哪一段 K 线算出来的”，不是这组线在图上的起点。 |
| `leg_end_ts` | int64 | 拟合用的历史窗口尾时间。 |

方向和 0%/100% 的对应：

- `up`：`ratio=0` 在 `leg_high`，`ratio=1` 在 `leg_low`
- `down`：`ratio=0` 在 `leg_low`，`ratio=1` 在 `leg_high`

---

## 这一行对应哪条 Fib 线

| 字段 | 类型 | 含义 |
|------|------|------|
| `ratio` | float64 | Fibonacci 比例：`0 / 0.236 / 0.382 / 0.5 / 0.618 / 0.786 / 1`。 |
| `price` | float64 | 这条线的价格，由 `(leg_high, leg_low, direction, ratio)` 算出来，保留两位小数。这是图上那条横线。 |
| `is_extrapolated` | bool | `true` 表示 `0` 或 `1`。这两条不是拟合锚点，是从内层 5 条线外推出来的。内层 5 条为 `false`。 |

---

## 这条线对应哪个聚类中心

拟合时窗口里会有一堆候选聚类中心（平均约 21 个，存在 step3 的 `cluster_centers_json`）。投产表**不会**把它们全展开，而是给每条 Fib 线找**价格最近的那一个**。

| 字段 | 类型 | 含义 |
|------|------|------|
| `nearest_cluster_center` | float64/null | 最近聚类中心的价格。这就是“这条 Fib 线对应的聚合 PL”。 |
| `nearest_cluster_conf` | float64/null | 该中心的累计置信度：聚到这个价位的拐点 `conf_high/conf_low` 之和。越大说明这个价位被越多检测器反复打到。 |
| `cluster_distance` | float64/null | `|price - nearest_cluster_center|`，Fib 线离这个中心有多远。 |
| `cluster_distance_ratio` | float64/null | `cluster_distance / span`，用组内跨度归一化后的距离。 |
| `is_cluster_aligned` | bool | 是否算真正对齐：`cluster_distance_ratio <= 0.02`（和拟合评分用的 2% 容差一样）。`true` 才是“这条线钉在某个聚类中心上”。 |
| `cluster_center_reused` | bool | 本组 7 条线里，是否还有别的 `ratio` 也用了**同一个**中心。 |

怎么读这几个字段：

- 内层线且 `is_cluster_aligned=true`：严格一一对应，这是拟合锚点。
- 内层线但 `aligned=false`：没有足够近的中心，仍给出最近一个，可能和邻居撞同一个中心。
- `0` / `1`：也找最近中心；外侧没有更远的中心时，会复用 `0.236` / `0.786` 的中心，此时 `cluster_center_reused=true`。

查询建议：

- 需要严格锚点关系：加 `WHERE is_cluster_aligned`
- 需要每条线都有价格参照：直接用 `nearest_cluster_center`

---

## 真实样例（fib001 同一组 7 行）

`multiplier=2, direction=down, source=initial, fib_score≈1.74`

| ratio | price | 对应中心 | 距离占比 | aligned | reused | 怎么理解 |
|------|------|---------|---------|---------|--------|---------|
| 0 | 980.91 | 980.67 | 0.5% | 是 | 否 | 外推的 0%，碰巧外侧有中心，算对齐 |
| 0.236 | 992.03 | 991.46 | 1.2% | 是 | 否 | 内层锚点 |
| 0.382 | 998.91 | 998.91 | 0 | 是 | 否 | 正好落在中心上 |
| 0.500 | 1004.47 | 1005.78 | 2.8% | 否 | 否 | 内层但超出 2%，只是最近中心 |
| 0.618 | 1010.03 | 1010.03 | 0 | 是 | 否 | 正好落在中心上 |
| 0.786 | 1017.95 | 1017.32 | 1.3% | 是 | 是 | 内层锚点；1% 也用了这个中心 |
| 1 | 1028.03 | 1017.32 | 22.7% | 否 | 是 | 外推的 1%，外侧没有中心，复用 0.786 |

画横线用 `price`。要“这条线钉在哪个市场共识价位上”用 `nearest_cluster_center`，需要可信锚点时再加 `is_cluster_aligned`。

---

## 相关文档

- 全管道说明（含 step1~3 血缘）：[fib_pipeline_reference.md](../fib_pipeline_reference.md)
- 参数扫描实验记录：[fib_param_sweep_2026-09-02.md](./fib_param_sweep_2026-09-02.md)
- 触碰事件事实表：[line_events.md](./line_events.md)
- 衍生指标表：[line_factors.md](./line_factors.md)（产物文件为 `line_factor.parquet`）
