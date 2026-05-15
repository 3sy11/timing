# 数据模型架构

## ER 图

```
┌─────────────────────────────────────────────────────────────┐
│                       数据域                                 │
│                                                             │
│  ┌──────────────────┐                                       │
│  │      Kline       │ frozen                                │
│  ├──────────────────┤                                       │
│  │ PK symbol   str  │                                       │
│  │ PK interval str  │                                       │
│  │ PK ts       int  │                                       │
│  │    open     float│                                       │
│  │    high     float│                                       │
│  │    low      float│                                       │
│  │    close    float│                                       │
│  │    volume   float│                                       │
│  └──────────────────┘                                       │
│           │ 分析计算                                         │
│           ▼                                                 │
│  ┌──────────────────┐                                       │
│  │     Signal       │ frozen（纯数据快照）                    │
│  ├──────────────────┤                                       │
│  │    ts        int │                                       │
│  │    symbol    str │                                       │
│  │    interval  str │                                       │
│  │    direction str │ ← long / short / neutral              │
│  │    strength  f64 │                                       │
│  │    source    str │                                       │
│  │    price     f64 │                                       │
│  │    level     f64 │ nullable                              │
│  │    expires_at int│ nullable                              │
│  │    metadata  dict│                                       │
│  └──────────────────┘                                       │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                       交易域                                 │
│                                                             │
│  ┌──────────────────────┐                                   │
│  │       Order          │ 可变（状态机）                      │
│  ├──────────────────────┤                                   │
│  │ PK order_id     str  │ uuid 自动生成                      │
│  │    symbol       str  │                                   │
│  │    side         str  │ ← buy / sell                      │
│  │    order_type   str  │ ← market / limit / stop           │
│  │    quantity     f64  │                                   │
│  │    price        f64  │                                   │
│  │    stop_price   f64  │                                   │
│  │    status       str  │ ← 状态机（见下方）                  │
│  │    filled_qty   f64  │ 成交后回填                          │
│  │    filled_price f64  │ 成交后回填                          │
│  │    commission   f64  │ 成交后回填                          │
│  │    created_at   int  │                                   │
│  │    updated_at   int  │                                   │
│  └──────────┬───────────┘                                   │
│             │ status 变更                                    │
│             │                                               │
│  ┌──────────▼───────────┐                                   │
│  │   FillResult         │ frozen（成交快照）                  │
│  ├──────────────────────┤                                   │
│  │    order_id     str  │ ← FK → Order.order_id             │
│  │    symbol       str  │                                   │
│  │    side         str  │                                   │
│  │    filled_price f64  │                                   │
│  │    filled_qty   f64  │                                   │
│  │    commission   f64  │                                   │
│  │    ts           int  │                                   │
│  └──────────┬───────────┘                                   │
│             │ apply_fill                                    │
│             ▼                                               │
│  ┌──────────────────────┐                                   │
│  │     Position         │ 事实表（持久化到 SQLite）            │
│  ├──────────────────────┤                                   │
│  │ PK symbol       str  │                                   │
│  │    side         str  │ ← long / short / flat             │
│  │    quantity     f64  │                                   │
│  │    avg_entry    f64  │ 加权平均入场价                      │
│  │    realized_pnl f64  │ 已实现累计盈亏                      │
│  │    unrealized   f64  │ 按最新价 mark                      │
│  │    commission   f64  │ 累计手续费                          │
│  │    trade_count  int  │ 成交次数                            │
│  │    open_ts      int  │ 首次建仓时间                        │
│  │    updated_at   int  │ 最后更新时间                        │
│  └──────────────────────┘                                   │
│                                                             │
│  ┌──────────────────────┐                                   │
│  │     Account          │ 事实表（SimExchange 内存持有）       │
│  ├──────────────────────┤                                   │
│  │ PK account_id   str  │ 默认 "default"                    │
│  │    currency     str  │ 默认 "CNY"                        │
│  │    initial_bal  f64  │ 初始资金（不变）                    │
│  │    total        f64  │ 当前总资产                          │
│  │    locked       f64  │ 冻结金额                            │
│  │    free         f64  │ computed: total - locked           │
│  │    net_pnl      f64  │ computed: total - initial          │
│  │    total_comm   f64  │ 累计手续费                          │
│  │    total_rpnl   f64  │ 累计已实现盈亏                      │
│  │    trade_count  int  │ 总成交次数                          │
│  │    updated_at   int  │ 最后更新时间                        │
│  └──────────────────────┘                                   │
│                                                             │
│  ┌──────────────────────┐                                   │
│  │    LedgerEntry       │ frozen（台账流水）                  │
│  ├──────────────────────┤                                   │
│  │    ts           int  │ 自动生成                            │
│  │    entry_type   str  │ ← commission/rpnl/deposit/...     │
│  │    amount       f64  │                                   │
│  │    balance_after f64 │                                   │
│  │    order_id     str  │ ← FK → Order.order_id             │
│  │    symbol       str  │                                   │
│  │    memo         str  │                                   │
│  └──────────────────────┘                                   │
└─────────────────────────────────────────────────────────────┘
```

---

## Order 状态机

Order 是统一的订单模型，成交和拒绝只是其 status 字段的不同状态：

```
pending ──submit──▶ submitted ──match──▶ filled
   │                    │                   │
   │                    │              partially_filled
   │                    │
   │               cancel──▶ canceled
   │
   └──reject──▶ rejected
```

**OrderFilled / OrderRejected 是事件（BaseEvent），不是独立数据模型**：
- 它们是 Order 状态变更时的**通知事件**，走 Exchange pub/sub 广播
- 数据源头是 Order 本身 + FillResult
- 下游（风控/统计）订阅事件即可，不需要独立存储

---

## 事实表设计说明

### Position — 持仓事实表

| 字段 | 说明 | 设计理由 |
|------|------|---------|
| symbol (PK) | 每个标的一条记录 | 自然主键，一对一 |
| side | long/short/flat | 反映当前方向 |
| quantity | 当前持有 | 可能部分平仓，所以不是 fixed |
| avg_entry_price | 加权平均入场价 | 多次加仓后的综合成本 |
| realized_pnl | **累计**已实现盈亏 | 每次平仓追加，不归零 |
| unrealized_pnl | 按最新价 mark | 每次 mark_to_market 更新 |
| commission | **累计**手续费 | 含所有成交的手续费 |
| trade_count | 成交笔数 | 便于统计换手率 |
| open_ts | 建仓时间 | 完全平仓后重开会更新 |
| updated_at | 最后更新 | 每次 apply_fill 更新 |

存储方式：`Broker.protocol.set("__positions", {symbol: position_dict})`

### Account — 账户事实表

| 字段 | 说明 | 设计理由 |
|------|------|---------|
| account_id (PK) | 默认 "default" | 预留多账户扩展 |
| currency | 计价币种 | 默认 CNY |
| initial_balance | 初始资金 | 不变，用于计算总收益率 |
| total | 当前总资产 | 买入扣减，卖出增加 |
| locked | 冻结金额 | Limit 单冻结用 |
| total_commission | 累计手续费 | 所有成交手续费之和 |
| total_realized_pnl | 累计已实现盈亏 | 所有平仓盈亏之和 |
| trade_count | 总成交笔数 | 全局统计 |
| updated_at | 最后更新 | 每次 settle 更新 |

存储方式：SimExchangeProtocol 内存持有，可通过 protocol 链持久化。

---

## 模型分类总结

| 类型 | 模型 | frozen | 存储 |
|------|------|--------|------|
| 输入数据 | Kline | ✅ | DuckDB |
| 分析快照 | Signal | ✅ | 未持久化 |
| 交易指令 | Order | ❌ | 内存（运行时） |
| 成交快照 | FillResult | ✅ | 未持久化 |
| 事实记录 | Position | ❌ | SQLite |
| 事实记录 | Account | ❌ | 内存 + 可落盘 |
| 流水日志 | LedgerEntry | ✅ | 未使用 |

---

## 公共模块：Clock

| 类 | 用途 |
|----|------|
| LiveClock | 生产：system time |
| SimulatedClock | 回测：由 DataClient 推进 |

接口：`now_ms()` / `set_time_ms(ts)` / `sleep(seconds)`
