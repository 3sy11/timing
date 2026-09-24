# signals.parquet 字段含义

> 投产表路径：`warehouse/timing/signals/{signal_id}/{symbol}/{interval}/signals.parquet`
>
> 由分析规则 `fib_signal` 读 **`candidates.parquet`** + **`result.parquet`**（锚点、外推、拟合分）写出。
>
> **一行 = 一次过关的碰线信号**。回答「要不要信」和「做多还是做空、有多强」。

入场价、止损、目标、盈亏比、信号过期根数不在本表。那些是接到信号之后怎么做，属于决策层。

`level_price` 只说明碰到了哪条线，不是下单价。

---

## 可调试验参数

写在 `analysis/rules/fib_signal/profiles/{profile}.toml`。改过滤或权重应换新 `signal_id`。

### 硬过滤

不通过则正式模式不写这一行。

| 参数 | 默认 | 管什么 |
|------|------|--------|
| `min_pre_hold_n` | `2` | 拟合前 `bounce+break` 至少几次。对应 `pre_hold_n` |
| `min_pre_hold_rate` | `0.5` | 拟合前挡住率下限。`pre_hold_rate` 为 null 直接失败 |
| `max_pre_weak_share` | `0.6` | 拟合前蹭线占比上限。`pre_weak_share` 为 null 视为通过 |
| `allow_extrapolated` | `false` | 是否允许 ratio=0/1 外推线 |
| `require_cluster_aligned` | `false` | true 时必须钉在聚类中心上；默认对齐只加分 |

### 评分

```text
signal_score = score_subtotal × direction_conf
score_subtotal
  = w_pre_hold × pre_hold_rate
  − w_pre_weak × pre_weak_share
  + w_fit_hold × fit_hold_rate
  + w_cluster × is_cluster_aligned
  + w_cluster_conf × nearest_cluster_conf_norm
  + w_nearby_fib × min(nearby_fib_n, nearby_fib_cap) / nearby_fib_cap
  + w_nearby_frate × nearby_fib_hold_rate
  + w_nearby_cls × min(nearby_cluster_n, nearby_cls_cap) / nearby_cls_cap
```

率为 null 的分项记 0。`nearest_cluster_conf_norm` 按**当日仍存活的全部 result 行**做 min-max。

| 参数 | 默认 | 管什么 |
|------|------|--------|
| `w_pre_hold` | `3.0` | 拟合前挡住率，主项 |
| `w_pre_weak` | `1.0` | 蹭线扣分 |
| `w_fit_hold` | `1.0` | 拟合窗挡住率，只作对照，应小于 `w_pre_hold` |
| `w_cluster` | `1.0` | 是否聚类对齐（0 或 1） |
| `w_cluster_conf` | `0.5` | 最近中心置信度（归一化后） |
| `w_nearby_fib` / `nearby_fib_cap` | `1.0` / `3` | 价近 Fib 条数，满 3 条不再加 |
| `w_nearby_frate` | `0.8` | 邻居拟合前挡住率均值 |
| `w_nearby_cls` / `nearby_cls_cap` | `0.5` / `2` | 价近聚类中心个数 |
| `direction_conf_align` | `1.0` | 顺势乘数：up+from_above，或 down+from_below |
| `direction_conf_cross` | `0.6` | 逆势乘数：另外两种组合 |
| `min_signal_score` | `3.0` | 总分下限，低于则正式模式不写 |
| `debug_mode` | `false` | true 时未过过滤、低分也写入，并填 `filter_fail_reason` |

方向本身：

| Fib `direction` | `approach` | `signal_direction` | 乘数 |
|-----------------|------------|--------------------|------|
| up | from_above | long | align |
| up | from_below | short | cross |
| down | from_below | short | align |
| down | from_above | long | cross |

---

## 字段

### 身份

| 字段 | 含义 |
|------|------|
| `signal_id` | 本次信号实验 |
| `compute_id` / `feature_id` | 上游计算、特征实验 |
| `symbol` / `interval` / `ts` | 标的、周期、触发 K 线（与 candidates.ts 相同） |
| `fib_group_id` / `ratio` / `level_price` | 碰到的线 |
| `multiplier` / `direction` / `approach` | 尺度、拟合方向、从哪边碰到 |

### 结论

| 字段 | 含义 |
|------|------|
| `signal_direction` | `long` / `short` |
| `direction_conf` | 实际乘数（align 或 cross 的取值） |
| `signal_score` | `score_subtotal × direction_conf` |
| `pass_hard_filter` | 正式入库恒为 true |
| `filter_fail_reason` | 正式模式为 null；debug 时写第一条失败原因 |

### 分项（乘方向乘数之前）

`score_pre_hold`、`score_pre_weak`（≤0）、`score_fit_hold`、`score_cluster`、`score_cluster_conf`、`score_nearby_fib`、`score_nearby_frate`、`score_nearby_cls`、`score_subtotal`。

### 从 result 带出的质量戳

`is_cluster_aligned`、`fib_score`、`is_extrapolated`、`cluster_distance_ratio`。

---

## 这张表没有什么

| 没有 | 原因 |
|------|------|
| `entry_price` / `stop_*` / `target_*` / `meets_rrr` | 决策层按自己的入场与止损假设去算 |
| `signal_expire_ts` | 等多久才用，是决策的事 |
| candidates 原始率 | 用 `feature_id + ts + fib_group_id + ratio` 回联 |
| 当天 `outcome_atr` | 当时 ATR 窗未走完 |

---

## 相关文档

- 特征宽表：[candidates.md](./candidates.md)
- 实体维：[result_parquet_fields.md](./result_parquet_fields.md)
- 全管道：[fib_pipeline_reference.md](../fib_pipeline_reference.md)
