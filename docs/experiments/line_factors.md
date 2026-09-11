# line_factors.parquet 字段含义

> 路径：`warehouse/timing/computation/fib_retracement/{compute_id}/{symbol}/{interval}/line_factors.parquet`
>
> 由 `result.parquet` + K 线加工，**一行对应 result 的一条 Fib 线**。不是交易信号。
>
> 用 `effective_ts, multiplier, direction, leg_low, leg_high, ratio` 回联 result。

## 时间口径

- 已失效：统计 `[effective_ts, invalidated_ts)`，`t_end = invalidated_ts`
- 仍有效：统计到当前最后一根 K 线，`t_end = last_ts`

共识看的是 **t_end 截面**（当时还活着的组），不是历史上曾经重叠过的组。

## 身份列

与 result 对齐的键，外加 `t_end`、`nearest_cluster_center`、`nearest_cluster_conf`、`is_cluster_aligned`、`fib_score`。

## bounce（`fib_` / `pl_` 各一套）

触碰半径 = 目标价 × 0.1%。目标：`fib_` 用 `price`，`pl_` 用 `nearest_cluster_center`。

| 字段 | 含义 |
|------|------|
| `*_touch_bar_count` | 存活期内碰到该价的 bar 根数 |
| `*_bounce_from_above` | 上一根 close 在线上，触碰后下一根 close 上涨 |
| `*_bounce_from_below` | 上一根 close 在线下，触碰后下一根 close 下跌 |
| `*_bounce_count` | 两方向之和 |
| `*_bounce_rate` | bounce / touch，无触碰为 null |
| `*_…_recent` | 同上，但只看存活期内最近 200 根 |

无 PL 时 `pl_*` 为 null。

## consensus（`fib_` / `pl_` 各一套）

同价半径 0.1%。同一 Fib 组只计 1。自己计 1。

| 字段 | 含义 |
|------|------|
| `*_consensus_group_count` | t_end 仍活着、同价的独立组数 |
| `*_consensus_multiplier_count` | 覆盖几个尺度 |
| `*_consensus_direction_count` | up/down 出现几种 |
| `*_consensus_line_count` | 去重前有多少条比例行落在带内 |
| `*_peer_bounce_rate_mean` | 其他组代表线的近窗 bounce_rate 平均 |
| `*_peer_bounce_rate_min` | 同伴近窗 rate 最小值 |
| `*_peer_aligned_ratio` | 同伴代表线 aligned 比例 |
| `*_peer_conf_sum` | 同伴 `nearest_cluster_conf` 之和 |

`group_count` 不默认越大越好。同伴质量不折进人数。
