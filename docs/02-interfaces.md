# Phase 5：接口契约 + 数据模型 + 序列化

从 01-sequence.md 顺序图提取所有方法签名和数据结构。

---

## 数据层 — data/

### DataEngine

继承：`AppService`　模块：`data/app.py`　存储：DuckDB

| 可见性 | 方法 | 签名 | 说明 |
|--------|------|------|------|
| public | append_bars | `(symbol: str, interval: str, bars: list[dict]) → None` | 追加 K 线 |
| public | get_klines | `(symbol: str, interval: str, start_ts: int=None, end_ts: int=None) → list[dict]` | 查询 K 线 |
| public | set_klines | `(symbol: str, interval: str, klines: list[dict]) → None` | 全量写入（A10） |

### PushBars（Command）

destination：`data.DataEngine.PushBars`

| 字段 | 类型 | 说明 |
|------|------|------|
| symbol | str | 标的代码 |
| interval | str | K 线周期 |
| bars | list[dict] | K 线数据 |
| replay | bool | false=写入, true=跳过 |

`PushBars(symbol: str, interval: str, bars: list, replay: bool) → dict`

### GetKlines（Command）

destination：`data.DataEngine.GetKlines`

| 字段 | 类型 | 说明 |
|------|------|------|
| symbol | str | 标的代码 |
| interval | str | K 线周期 |
| start_ts | int | 可选起始时间 |
| end_ts | int | 可选结束时间 |

`GetKlines(symbol: str, interval: str, start_ts: int, end_ts: int) → list`

### ImportKlines（Command）

destination：`data.DataEngine.ImportKlines`

| 字段 | 类型 | 说明 |
|------|------|------|
| symbol | str | 标的代码 |
| interval | str | K 线周期 |
| path | str | 文件路径 |

`ImportKlines(symbol: str, interval: str, path: str) → dict`

---

## 分析层 — analysis/

### AnalysisEngine（基类）

继承：`AppService`　模块：`analysis/app.py`

| 可见性 | 方法 | 签名 | 说明 |
|--------|------|------|------|
| public | on_bar | `(cmd: BaseCommand) → dict｜None` | subscriber 入口 |
| internal | _warmup | `(symbol: str, interval: str, klines: list[dict]) → None` | 子类覆盖 |
| internal | _process_bar | `(symbol: str, interval: str, bar: dict) → dict{signals, breakouts}` | 子类覆盖 |

on_bar 内部流程：

```
① protocol.get(__ckpt) → checkpoint
② if ckpt==0: GetKlines 全量 → _warmup → 剩余 bars
   else: GetKlines(start_ts=ckpt+1) → 增量 bars
③ for bar in bars: _process_bar(symbol, interval, bar)
④ protocol.set(__ckpt, last_ts)
⑤ for signal: exchange.match → hub.execute(SignalEmitted)
```

### RetracementService（子类）

继承：`AnalysisEngine`　模块：`analysis/algo/retracement/service.py`　存储：SQLite

| 可见性 | 方法 | 签名 | 说明 |
|--------|------|------|------|
| override | _warmup | `(symbol, interval, klines) → None` | compute_retracement → 缓存 |
| override | _process_bar | `(symbol, interval, bar) → dict{signals, breakouts}` | 触碰/突破检测 |

### SignalEmitted（Event）

destination：`analysis.AnalysisEngine.SignalEmitted`

| 字段 | 类型 | 说明 |
|------|------|------|
| ts | int | 信号时间戳 |
| symbol | str | 标的代码 |
| interval | str | K 线周期 |
| direction | str | long / short / neutral |
| strength | float | 信号强度 |
| source | str | 产出子服务 alias |
| price | float | 触发价格 |
| level | float | 关键位价格 |
| metadata | dict | 扩展字段 |

---

## 策略层 — strategy/

### FibStrategy

继承：`AppService`　模块：`strategy/app.py`　subscriber：`analysis.AnalysisEngine.SignalEmitted` → `on_signal`

| 可见性 | 方法 | 签名 | 说明 |
|--------|------|------|------|
| public | on_signal | `(cmd: BaseCommand) → None` | 信号过滤 + 下单 |

| 配置 | 类型 | 默认值 |
|------|------|--------|
| position_size | float | 0.1 |
| min_strength | float | 0.6 |

---

## 执行层 — execution/

### Broker

继承：`AppService`　模块：`execution/broker.py`　protocol：`SimExchangeProtocol`

| 可见性 | 方法 | 签名 | 说明 |
|--------|------|------|------|
| public | on_submit_order | `(order: Order, bar: dict=None) → FillResult｜None` | 余额检查 → 撮合 → 持仓更新 |
| public | get_position | `(symbol: str) → Position` | 查询持仓 |
| public | get_all_positions | `() → dict{str: Position}` | 全部持仓 |
| public | get_account | `() → Account` | 查询账户 |

### SubmitOrder（Command）

destination：`execution.Broker.SubmitOrder`

| 字段 | 类型 | 说明 |
|------|------|------|
| symbol | str | 标的代码 |
| side | str | buy / sell |
| order_type | str | market / limit / stop |
| quantity | float | 下单数量 |
| price | float | 限价 |
| stop_price | float | 止损价 |
| bar | dict | 当前 bar |

`SubmitOrder(symbol: str, side: str, order_type: str, quantity: float, price: float, stop_price: float, bar: dict) → dict | None`

### SimExchangeProtocol

继承：`ExchangeProtocol`　模块：`execution/adapters/sim.py`

| 可见性 | 方法 | 签名 | 说明 |
|--------|------|------|------|
| override | submit_order | `(order: Order, bar=None) → FillResult｜None` | market 直接撮合; limit/stop 入队列 |
| override | check_pending | `(bar: dict) → list[FillResult]` | 遍历挂单触发 |
| override | get_balance | `() → Account` | 内存 Account |
| internal | _fill_market | `(order: Order, bar: dict) → FillResult` | close ± 滑点 → settle → mark_filled |

| 配置 | 类型 | 默认值 |
|------|------|--------|
| initial_balance | float | 100000 |
| slippage_pct | float | 0.001 |
| commission_rate | float | 0.001 |

### OrderFilled / OrderRejected（Event）

| 事件 | destination | 关键字段 |
|------|------------|---------|
| OrderFilled | `execution.Broker.OrderFilled` | order_id, symbol, side, filled_price, filled_quantity, commission, realized_pnl, ts |
| OrderRejected | `execution.Broker.OrderRejected` | order_id, symbol, reason, ts |

---

## 引擎层 — engine/

### BacktestApp

模块：`engine/app.py`

| 方法 | 签名 | 说明 |
|------|------|------|
| on_init_dependencies | `() → list[AppService]` | 读 backtest.toml 创建动态分析实例 |
| on_started | `() → None` | 注册动态 subscriber 到 Exchange |

### RunBacktest（Command）

destination：`backtest.BacktestApp.RunBacktest`

| 字段 | 类型 |
|------|------|
| symbol | str |
| interval | str |

`RunBacktest(symbol: str, interval: str) → dict`

---

## 数据模型

### Kline（frozen）

存储：DuckDB `klines` 表

| 字段 | 类型 | 说明 |
|------|------|------|
| symbol | str | PK 分区键 |
| interval | str | PK 分区键 |
| ts | int | PK 时间戳（毫秒） |
| open | float | 开盘价 |
| high | float | 最高价 |
| low | float | 最低价 |
| close | float | 收盘价 |
| volume | float | 成交量 |

### Signal（frozen）

存储：运行时内存

| 字段 | 类型 | 说明 |
|------|------|------|
| ts | int | 信号产出时间戳 |
| symbol | str | 标的代码 |
| interval | str | K 线周期 |
| direction | str | long / short / neutral |
| strength | float | 信号强度 |
| source | str | 产出子服务 alias |
| price | float | 触发价格 |
| level | float | 关键位价格（nullable） |
| expires_at | int | 过期时间（nullable） |
| metadata | dict | 扩展字段 |

### Order（可变，持久化）

存储：SQLite `__orders` via Broker protocol 链

| 字段 | 类型 | 说明 |
|------|------|------|
| order_id | str | uuid 自动生成 |
| symbol | str | 标的代码 |
| side | str | buy / sell |
| order_type | str | market / limit / stop / stop_limit |
| quantity | float | 下单数量 |
| price | float | 限价 |
| stop_price | float | 止损价 |
| status | str | pending → submitted → filled / rejected / canceled |
| filled_quantity | float | 成交后回填 |
| filled_price | float | 成交后回填 |
| commission | float | 成交后回填 |
| created_at | int | 创建时间戳 |
| updated_at | int | 更新时间戳 |

方法：`mark_filled(price, qty, comm, ts) → None`

**Order 状态机**：filled / rejected / canceled 都只是 status 的值，OrderFilled / OrderRejected 是状态变更的通知事件。

### FillResult（frozen）

存储：SQLite `__fills` via Broker protocol 链

| 字段 | 类型 | 说明 |
|------|------|------|
| order_id | str | FK → Order |
| symbol | str | 标的代码 |
| side | str | buy / sell |
| filled_price | float | 成交价格 |
| filled_quantity | float | 成交数量 |
| commission | float | 手续费 |
| ts | int | 成交时间戳 |

### Position（事实表，可变）

存储：SQLite `__positions` via Broker protocol 链

| 字段 | 类型 | 说明 |
|------|------|------|
| symbol | str | PK |
| side | str | long / short / flat |
| quantity | float | 当前持有量 |
| avg_entry_price | float | 加权平均入场价 |
| realized_pnl | float | 累计已实现盈亏 |

方法：`apply_fill(fill: FillResult) → float(rpnl)`

### Account（事实表，可变）

存储：SimExchangeProtocol 内存

| 字段 | 类型 | 说明 |
|------|------|------|
| initial_balance | float | 初始资金（不变） |
| total | float | 当前总资产 |

计算属性：`free = total` / `net_pnl = total - initial_balance`
方法：`settle(pnl, commission)`

---

## 序列化方案

| 数据模型 | 存储位置 | 序列化方式 | 读写时机 |
|---------|---------|-----------|---------|
| Kline | DuckDB klines 表 | 列式原生类型 | PushBars 写 / GetKlines 读 |
| Signal | SQLite `signals:{s}:{i}` | model_dump() JSON | on_bar 末尾写 / 回测汇总读 |
| StrategyDecision | SQLite `decisions:{s}:{i}` | model_dump() JSON | on_signal 末尾写 / 回测汇总读 |
| Order | SQLite `__orders` | model_dump() JSON | SubmitOrder 写 / mark_filled 更新 / 查询读 |
| FillResult | SQLite `__fills` | model_dump() JSON | _process_fill 写 / 回测汇总读 |
| Position | SQLite `__positions` | model_dump() JSON | apply_fill 后写 / on_started 时读 |
| Account | 内存 | — | SimExchange.on_start 初始化 |
| 回撤结构 | SQLite `retracement:{s}:{i}` | 自定义 dict | _warmup 写 / _process_bar 读 |
| checkpoint | SQLite `__ckpt:{s}:{i}` | int | on_bar 写 / on_bar 读 |

---

## 继承关系总览

```
AppService (bollydog)
├── DataEngine                          data/app.py
├── AnalysisEngine                      analysis/app.py
│     └── RetracementService            analysis/algo/retracement/service.py
├── FibStrategy                         strategy/app.py
├── Broker                              execution/broker.py
├── BacktestApp                         engine/app.py
└── TimingApp                           engine/app.py

Protocol (bollydog)
└── ExchangeProtocol                    execution/adapters/base.py
      └── SimExchangeProtocol           execution/adapters/sim.py

BaseCommand (bollydog)
├── PushBars                            data/models.py
├── GetKlines                           data/models.py
├── ImportKlines                        data/models.py
├── SubmitOrder                         execution/models.py
├── CancelOrder                         execution/models.py
└── RunBacktest                         engine/command.py

BaseEvent (bollydog)
├── SignalEmitted                       models/signal.py
├── OrderFilled                         models/order.py
└── OrderRejected                       models/order.py
```

---

## 差异设计描述

以设计文档为准，后续代码迭代时统一处理：

- [x] analysis/app.py：增加 Signal 持久化到 `signals:{s}:{i}`
- [x] strategy/models.py：定义 StrategyDecision 模型，strategy/app.py 中持久化到 `decisions:{s}`
- [x] execution/broker.py：增加 Order 持久化到 `__orders:{order_id}`
- [x] execution/broker.py：增加 FillResult 持久化到 `__fills`
- [x] models/position.py：精简为 5 字段（symbol, side, quantity, avg_entry_price, realized_pnl）
- [x] models/account.py：精简为 2 字段（initial_balance, total）
- [x] models/account.py：settle 签名统一为 `settle(pnl, commission)` 两参数
- [x] data/models.py：`IngestKlinesFromFile` 重命名为 `ImportKlines`
- [x] models/account.py：移除 LedgerEntry 模型及相关代码（代码中已不存在）
