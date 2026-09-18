# line_events.parquet 字段含义

> 投产表路径：`warehouse/timing/computation/fib_retracement/{compute_id}/{symbol}/{interval}/line_events.parquet`
>
> 由 `result.parquet` + K 线加工。**一行 = 一次已完成的触碰事件**，不是一条线、也不是一根碰线 K 线。
>
> 用 `effective_ts, multiplier, direction, leg_low, leg_high, ratio` 回联 result 的一行。

只打 **ATR 口径 `outcome_atr`**（按进入时波动量，弹开/刺穿了多远）。反弹率要对多行聚合；邻居组数不在这张表里。

存活期（`effective_ts` 之后）不写事件。预测日当天碰线也不写在这里。

---

## 可调试验参数

写在 Fib 的 `RetracementConfig` / `profiles/{compute_id}.toml`，也可用 `--override event_touch_k=0.0015`。`tmp/run_line_events.py` 会按 compute_id 读 profile。

这些参数在事实表写出后应视为冻结。分析层 `price_touch` **不得**再引用它们来改标签。

| 参数 | 默认 | 管什么 | 怎么调 |
|------|------|--------|--------|
| `event_touch_k` | `0.001`（价的 0.1%） | 触碰带的下限：至少是目标价的这么宽 | 更大更容易进带；更小更严 |
| `event_atr_band_k` | `0.25` | 触碰带按 ATR 加宽：实际带宽 = `max(价×touch_k, 此系数×进入时ATR)`。`0` 则只用百分带宽 | 波动大的日子带变宽，减少噪声触碰 |
| `event_atr_period` | `14` | 算 ATR 用最近多少根的真实波幅（不含进入当根） | 更长更稳、反应更慢 |
| `event_atr_horizon` | `5` | 从进入根起向前看几根，量弹开/刺穿 | 更长才算「走出一段」 |
| `event_atr_threshold` | `0.5` | ATR 竞赛门槛：先达到 0.5 个 ATR 的有利偏移算挡住，先达到 0.5 个 ATR 的刺穿算破 | 更大更严，更多 `weak` |

这些参数**不改变** result 里有哪些线，只改变事件怎么切、标签怎么打。

---

## 一行是什么

价格进入触碰带，再离开该带，才落一行。然后从进入根起看 ATR 窗，打 `outcome_atr`。

```text
进入触碰带（连续多根在带内仍算一次）
    ↓
离开触碰带（时期结束还在带里 → 不入库）
    ↓
从进入起看 horizon 根，量弹开/刺穿几个 ATR
    → bounce / break / weak
```

触碰带：

```text
r = max(target_price × event_touch_k, event_atr_band_k × ATR)
高低点与 [price − r, price + r] 相交 → 进入
```

ATR 口径（`outcome_atr`）：

- 从进入根起看 `event_atr_horizon` 根（被时期右端截断也照算，并记下 `atr_bars`）
- `mfe_atr` = 往接近方向最大偏移 / 进入时 ATR（弹开了几个正常波动）
- `mae_atr` = 往另一侧最大穿透 / ATR（刺穿了几个正常波动）
- 先达到 `event_atr_threshold` 个 ATR 的有利偏移，且当时刺穿还没到门槛 → `bounce`（挡住）
- 先达到门槛的刺穿 → `break`（破）
- 两边都没到 → `weak`（蹭线）
- 同一根两边都到：刺穿幅度更大记 `break`，否则 `bounce`
- `close_back`：ATR 窗最后一根 close 是否仍在接近侧（不管出没出带）
- `completed_ts`：ATR 窗最后一根的时间（不是离开带的时间）
- 进入时还没有 ATR → 这些列为 null

---

## 回联实体的键

| 字段 | 类型 | 含义 | 用来干什么 |
|------|------|------|------------|
| `compute_id` | str | 哪次 Fib 计算实验 | 分区、溯源 |
| `symbol` | str | 标的 | 与 klines、result 对齐 |
| `interval` | str | 周期 | 与 klines、result 对齐 |
| `effective_ts` | int64 | 该组生效时刻 | 与 result 组键对齐 |
| `multiplier` | int | 1 短期 / 2 中期 / 3 长期 | 参与组身份 |
| `direction` | str | `up` / `down` | 参与组身份 |
| `leg_low` / `leg_high` | float64 | 网格价格框 | 与 result 组键对齐 |
| `ratio` | float64 | Fib 比例 | 定位 result 哪一行 |
| `fib_group_id` | str | `hash(effective_ts, multiplier, direction, round(leg_low,4), round(leg_high,4))` 前 16 位 | 一组 7 线共用；不同尺度/方向是不同组 |

拟合描述在 result，本表不重复。

---

## 测的是哪条价、哪段窗

| 字段 | 类型 | 含义 |
|------|------|------|
| `target_kind` | str | `fib` = `result.price`；`cluster` = `nearest_cluster_center`。无中心则无 cluster 行 |
| `target_price` | float64 | 本次检测用的价格 |
| `period` | str | `prefit`：`ts < leg_start_ts`（独立先验）；`fitwin`：`leg_start_ts <= ts <= effective_ts`（拟合自证，不能单独当「能拐」） |

---

## 事件字段

| 字段 | 类型 | 含义 |
|------|------|------|
| `event_id` | str | 组 + ratio + kind + period + entered_ts 的哈希 |
| `entered_ts` | int64 | 第一次进入触碰带 |
| `completed_ts` | int64 | ATR 窗最后一根的时间 |
| `approach` | str | `from_above` / `from_below`，由进入前一根 close 决定 |
| `touch_bar_count` | int | 这次在带内停了几根 |
| `atr` | float64/null | 进入时的 ATR（用进入前的 K 线） |
| `mfe_atr` | float64/null | 弹开距离 / ATR |
| `mae_atr` | float64/null | 刺穿距离 / ATR |
| `close_back` | bool/null | ATR 窗末日 close 是否在接近侧 |
| `outcome_atr` | str/null | `bounce` / `break` / `weak` |
| `atr_bars` | int | ATR 窗实际用了几根 |

本表没有现成的 `bounce_rate`，要对多行聚合。没有旧的收盘出带 `outcome` / `confirm_bars`。

---

## 怎么聚合成「像不像能拐」

```text
有意义测试 = outcome_atr 为 bounce 或 break
挡住率     = bounce / (bounce + break)
weak 单独计数：weak_share = weak / (bounce + break + weak)
weak 不当失败、不进挡住率分母
```

`fitwin_*` 同样算法，不得单独当主过滤。

预测时：用 result 生命周期判断当天是否还活着；只用本表 prefit/fitwin；当天碰线与邻居写在 [candidates.md](./candidates.md)，不回写本表。

---

## 这张表没有什么

| 没有 | 原因 |
|------|------|
| `fib_score`、对齐字段 | 在 result |
| 汇总反弹率 | 由本表聚合，见 candidates |
| 邻居组数 | 关系/截面，不是一次触碰 |
| 存活期触碰、终态共识 | 含未来信息 |
| 当前 bar 候选信号 | 使用层 [candidates.md](./candidates.md) |
| 旧口径 `outcome` / `confirm_bars` | 已废弃，只保留 ATR 口径 |

---

## 相关文档

- 实体维：[result_parquet_fields.md](./result_parquet_fields.md)
- 分析候选：[candidates.md](./candidates.md)
- 全管道：[fib_pipeline_reference.md](../fib_pipeline_reference.md)
