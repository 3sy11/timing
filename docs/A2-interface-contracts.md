# 附录 A2：接口契约

从 A1 顺序图中提取每个类需要暴露的方法。按模块/服务划分，体现继承关系。

**约定**：`public` = 外部可调用，`internal` = 本类/子类内部使用（`_` 前缀），`override` = 子类覆盖基类方法。

---

## 数据层 — data/

### DataEngine

继承：`AppService`（bollydog）
模块：`data/app.py`
存储：DuckDB `{path}/data.duckdb`

| 可见性 | 方法 | 签名 | 说明 |
|--------|------|------|------|
| public | append_bars | `(symbol: str, interval: str, bars: list[dict]) → None` | 追加K线到 DuckDB |
| public | get_klines | `(symbol: str, interval: str, start_ts: int=None, end_ts: int=None) → list[dict]` | 查询K线，可选时间范围 |
| public | set_klines | `(symbol: str, interval: str, klines: list[dict]) → None` | 全量写入K线到 DuckDB（故事 A10） |

#### PushBars（Command）

模块：`data/models.py`
destination：`data.DataEngine.PushBars`

| 可见性 | 方法 | 签名 | 说明 |
|--------|------|------|------|
| public | \_\_call\_\_ | `() → dict{symbol, interval, bars}` | replay=false 时写入 DuckDB，返回后框架 _publish |

| 字段 | 类型 | 说明 |
|------|------|------|
| symbol | str | 标的代码 |
| interval | str | K线周期 |
| bars | list[dict] | K线数据 |
| replay | bool | false=写入, true=跳过写入 |

#### GetKlines（Command）

模块：`data/models.py`
destination：`data.DataEngine.GetKlines`

| 可见性 | 方法 | 签名 | 说明 |
|--------|------|------|------|
| public | \_\_call\_\_ | `() → list[dict]` | 委托 DataEngine.get_klines |

| 字段 | 类型 | 说明 |
|------|------|------|
| symbol | str | 标的代码 |
| interval | str | K线周期 |
| start_ts | int | 可选，起始时间戳 |
| end_ts | int | 可选，结束时间戳 |

#### ImportKlines（Command）

模块：`data/models.py`
destination：`data.DataEngine.ImportKlines`

| 可见性 | 方法 | 签名 | 说明 |
|--------|------|------|------|
| public | \_\_call\_\_ | `() → dict{symbol, interval, count}` | 读取文件 → set_klines 全量写入 |

| 字段 | 类型 | 说明 |
|------|------|------|
| symbol | str | 标的代码 |
| interval | str | K线周期 |
| file_path | str | 数据文件路径（csv/json） |

---

## 分析层 — analysis/

### AnalysisEngine（基类）

继承：`AppService`（bollydog）
模块：`analysis/engine.py`
角色：子服务容器 + 通用 on_bar 逻辑

| 可见性 | 方法 | 签名 | 说明 |
|--------|------|------|------|
| public | on_bar | `(cmd: BaseCommand) → dict｜None` | subscriber 入口，内部拉取数据、逐 bar 调用子类 _process_bar、广播信号 |
| public | apply_config | `(overrides: dict) → None` | 按子服务 alias 分区覆盖配置常量（故事 C2，图待补） |
| public | restart | `() → None` | mode 生命周期重置（on_stop → service_reset → on_start） |
| internal | _warmup | `(symbol: str, interval: str, klines: list[dict]) → None` | **模板方法**，子类覆盖 |
| internal | _process_bar | `(symbol: str, interval: str, bar: dict) → dict{signals, breakouts}` | **模板方法**，子类覆盖 |

on_bar 内部流程（基类实现，子类不覆盖）：

```
① protocol.get(__ckpt) → checkpoint
② if checkpoint==0: GetKlines 全量 → _warmup → 剩余 bars
   else: GetKlines(start_ts=ckpt+1) → 增量 bars
③ for bar in bars: _process_bar(symbol, interval, bar)
④ protocol.set(__ckpt, last_ts)
⑤ protocol.set(signals:{s}:{i}, signals)
⑥ for signal: exchange.match → hub.execute(SignalEmitted)
```

### RetracementService（子类）

继承：`AnalysisEngine`
模块：`analysis/algo/retracement/service.py`
存储：SQLite `{path}/analysis/retracement.sqlite`

| 可见性 | 方法 | 签名 | 说明 |
|--------|------|------|------|
| override | _warmup | `(symbol: str, interval: str, klines: list[dict]) → None` | compute_retracement → 写入 retracement 缓存 |
| override | _process_bar | `(symbol: str, interval: str, bar: dict) → dict{signals, breakouts}` | 触碰/突破检测 |

_process_bar 内部逻辑：

```
① 读缓存 protocol.get(retracement:{s}:{i}) → groups
② compute_consensus_strength(close, groups) → strength
③ 触碰检测：distance < tolerance && 非冷却中 → signals.append(Signal)
④ 突破检测：check_breakout(close, groups)
⑤ if 突破: GetKlines 全量 → compute_retracement → 更新缓存
⑥ return {signals, breakouts}
```

#### 纯函数（algo.py / touch.py）

模块：`analysis/algo/retracement/algo.py`, `analysis/algo/retracement/touch.py`

| 函数 | 签名 | 说明 |
|------|------|------|
| compute_retracement | `(klines: list[dict], cfg: Config) → RetraceResult` | K线 → pivot → TrendLeg → FibGroup → 关键位（图 2, 4） |

#### 分析层内部模型（models.py）

| 模型 | 字段 | 说明 |
|------|------|------|
| TrendLeg | start_idx, end_idx, start_price, end_price, direction | 一段趋势腿 |
| FibGroup | leg: TrendLeg, levels: list[FibLevel] | 基于趋势腿的回撤组 |
| FibLevel | ratio, price | 单个关键位（如 0.618 → 1.234） |

#### SignalEmitted（Event）

模块：`models/signal.py`
destination：`analysis.AnalysisEngine.SignalEmitted`

| 字段 | 类型 | 说明 |
|------|------|------|
| ts | int | 信号时间戳 |
| symbol | str | 标的代码 |
| interval | str | K线周期 |
| direction | str | long / short / neutral |
| strength | float | 信号强度 |
| source | str | 产出子服务 alias |
| price | float | 触发价格 |
| level | float | 关键位价格 |

---

## 策略层 — strategy/

### FibStrategy

继承：`AppService`（bollydog）
模块：`strategy/app.py`
存储：SQLite `{path}/strategy/fib_strategy.sqlite`
subscriber：`analysis.AnalysisEngine.SignalEmitted` → `on_signal`

| 可见性 | 方法 | 签名 | 说明 |
|--------|------|------|------|
| public | on_signal | `(cmd: BaseCommand) → None` | 接收信号，过滤判断，通过则下单 |

on_signal 内部逻辑：

```
① cmd.get_event() → {symbol, direction, strength, price, ts}
② if direction == "neutral": skip(reason="neutral")
③ if strength < min_strength: skip(reason="weak")
④ side = "buy" if direction == "long" else "sell"
⑤ protocol.append(decisions:{s}:{i}, StrategyDecision)
⑥ hub.execute(SubmitOrder(symbol, side, quantity, bar))
```

| 配置 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| position_size | float | 0.1 | 每次下单数量 |
| min_strength | float | 0.6 | 信号强度阈值 |

---

## 执行层 — execution/

### Broker

继承：`AppService`（bollydog）
模块：`execution/broker.py`
存储：SQLite `{path}/execution/broker.sqlite`
protocol：`SimExchangeProtocol`（或 `LiveExchangeProtocol`）

| 可见性 | 方法 | 签名 | 说明 |
|--------|------|------|------|
| public | on_submit_order | `(order: Order, bar: dict=None) → FillResult｜None` | 余额检查 → 委托 protocol 撮合 → _process_fill |
| public | process_pending | `(bar: dict) → list[FillResult]` | 检查挂单触发，逐个 _process_fill |
| public | get_position | `(symbol: str) → Position` | 查询单标的持仓 |
| public | get_all_positions | `() → dict{symbol: Position}` | 查询全部持仓 |
| public | get_account | `() → Account` | 查询账户（委托 protocol.get_balance） |
| internal | _process_fill | `(fill: FillResult) → FillResult` | 更新持仓 + 持久化 + 广播 OrderFilled |
| internal | _sync_emit | `(event: BaseEvent) → None` | exchange.match + hub.execute 同步广播 |

on_submit_order 内部逻辑：

```
① protocol.get_balance() → Account
② cost = price × quantity (市价用 bar.close)
③ if side=="buy" && free < cost: _sync_emit(OrderRejected) → return None
④ protocol.submit_order(order, bar) → FillResult | None
⑤ if fill: _process_fill(fill)
```

_process_fill 内部逻辑：

```
① position.apply_fill(fill) → rpnl
② protocol.set("__positions", positions)
③ protocol.append("__fills", fill)
④ _sync_emit(OrderFilled{..., realized_pnl=rpnl})
```

#### SubmitOrder（Command）

模块：`execution/models.py`
destination：`execution.Broker.SubmitOrder`

| 可见性 | 方法 | 签名 | 说明 |
|--------|------|------|------|
| public | \_\_call\_\_ | `() → FillResult｜None` | 构造 Order → Broker.on_submit_order |

| 字段 | 类型 | 说明 |
|------|------|------|
| symbol | str | 标的代码 |
| side | str | buy / sell |
| order_type | str | market / limit / stop |
| quantity | float | 下单数量 |
| price | float | 限价（limit 单用） |
| stop_price | float | 止损价（stop 单用） |
| bar | dict | 当前 bar 数据 |

#### OrderFilled（Event）

destination：`execution.Broker.OrderFilled`

| 字段 | 类型 | 说明 |
|------|------|------|
| order_id | str | 订单ID |
| symbol | str | 标的代码 |
| side | str | buy / sell |
| filled_price | float | 成交价格 |
| filled_quantity | float | 成交数量 |
| commission | float | 手续费 |
| realized_pnl | float | 本次已实现盈亏 |
| ts | int | 成交时间戳 |

#### OrderRejected（Event）

destination：`execution.Broker.OrderRejected`

| 字段 | 类型 | 说明 |
|------|------|------|
| order_id | str | 订单ID |
| symbol | str | 标的代码 |
| reason | str | 拒绝原因 |

### ExchangeProtocol（抽象基类）

继承：`Protocol`（bollydog）
模块：`execution/adapters/base.py`
角色：定义交易所撮合接口，Broker 通过 protocol 链调用

| 可见性 | 方法 | 签名 | 说明 |
|--------|------|------|------|
| public | submit_order | `(order: Order, bar: dict=None) → FillResult｜None` | **抽象**：提交订单撮合 |
| public | check_pending | `(bar: dict) → list[FillResult]` | **抽象**：检查挂单触发 |
| public | get_balance | `() → Account` | **抽象**：查询余额 |

### SimExchangeProtocol（具体实现）

继承：`ExchangeProtocol`
模块：`execution/adapters/sim.py`
角色：模拟交易所，内存撮合

| 可见性 | 方法 | 签名 | 说明 |
|--------|------|------|------|
| override | submit_order | `(order: Order, bar: dict=None) → FillResult｜None` | market → _fill_market; limit/stop → 入挂单队列返回 None |
| override | check_pending | `(bar: dict) → list[FillResult]` | 遍历挂单，按条件触发 _fill_market |
| override | get_balance | `() → Account` | 返回内存中的 Account |
| internal | _fill_market | `(order: Order, bar: dict) → FillResult` | 计算成交价（close±滑点）→ settle → mark_filled |

_fill_market 内部逻辑：

```
① fill_price = bar.close × (1 + slippage × direction)
② commission = fill_price × quantity × commission_rate
③ pnl = -cost (buy) / +cost (sell)
④ account.settle(pnl, commission)
⑤ order.mark_filled(fill_price, quantity, commission, ts)
⑥ return FillResult{...}
```

check_pending 触发条件：

| 类型 | side | 触发条件 |
|------|------|---------|
| limit | buy | bar.low ≤ order.price |
| limit | sell | bar.high ≥ order.price |
| stop | buy | bar.high ≥ order.stop_price |
| stop | sell | bar.low ≤ order.stop_price |

| 配置 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| initial_balance | float | 100000 | 初始资金 |
| slippage_pct | float | 0.001 | 滑点百分比 |
| commission_rate | float | 0.001 | 手续费率 |

---

## 引擎层 — engine/

### BacktestApp

继承：`AppService`（bollydog）
模块：`engine/app.py`

| 可见性 | 方法 | 签名 | 说明 |
|--------|------|------|------|
| public | on_init_dependencies | `() → list[AppService]` | 读 backtest.toml → 动态创建子服务（故事 A8，图待补） |
| public | on_started | `() → None` | 手工注册分析服务的 subscriber 到 Exchange（故事 A8，图待补） |

#### RunBacktest（Command）

模块：`engine/command.py`
destination：`engine.BacktestApp.RunBacktest`

| 可见性 | 方法 | 签名 | 说明 |
|--------|------|------|------|
| public | \_\_call\_\_ | `() → dict{signals, decisions, fills, account, positions}` | 完整回测流程 |

| 字段 | 类型 | 说明 |
|------|------|------|
| symbol | str | 回测标的 |
| interval | str | K线周期 |

\_\_call\_\_ 内部流程：

```
① GetKlines 全量 → klines
② restart() 重置分析服务
③ _warmup(klines[:warmup_bars])
④ for bar in klines[warmup_bars:]:
     clock.set_time_ms(bar.ts)
     broker.process_pending(bar)
     hub.execute(PushBars(symbol, interval, [bar], replay=true))
       → on_bar → signal → on_signal → SubmitOrder → fill
⑤ 从各模块 protocol.get 读取 signals/decisions/fills
⑥ broker.get_account() / get_all_positions()
⑦ return {signals, decisions, fills, account, positions}
```

---

## 数据模型 — models/

### Signal

模块：`models/signal.py`
性质：frozen，由 RetracementService 产出

| 字段 | 类型 | 说明 |
|------|------|------|
| ts | int | 信号产出时间戳 |
| symbol | str | 标的代码 |
| interval | str | K线周期 |
| direction | str | long / short / neutral |
| strength | float | 信号强度 |
| source | str | 产出子服务 alias |
| price | float | 触发价格 |
| level | float | 关键位价格 |

无方法。

### StrategyDecision

模块：`strategy/models.py`
性质：frozen，由 FibStrategy 产出

| 字段 | 类型 | 说明 |
|------|------|------|
| ts | int | 决策时间戳 |
| signal_ts | int | FK → Signal.ts |
| action | str | submit / skip |
| reason | str | 决策原因（skip 时） |
| order_id | str | FK → Order（submit 时） |
| quantity | float | 下单数量（submit 时） |

无方法。

### Order

模块：`models/order.py`
性质：运行时对象，不持久化

| 字段 | 类型 | 说明 |
|------|------|------|
| order_id | str | uuid 自动生成 |
| symbol | str | 标的代码 |
| side | str | buy / sell |
| order_type | str | market / limit / stop |
| quantity | float | 下单数量 |
| price | float | 限价 |
| stop_price | float | 止损价 |
| status | str | pending → submitted → filled |

| 方法 | 签名 | 说明 |
|------|------|------|
| mark_filled | `(price: float, qty: float, comm: float, ts: int) → None` | 更新状态为 filled，回填成交信息 |

### FillResult

模块：`models/order.py`
性质：frozen，由 SimExchangeProtocol 产出

| 字段 | 类型 | 说明 |
|------|------|------|
| order_id | str | FK → Order |
| symbol | str | 标的代码 |
| side | str | buy / sell |
| filled_price | float | 成交价格 |
| filled_quantity | float | 成交数量 |
| commission | float | 手续费 |
| ts | int | 成交时间戳 |

无方法。

### Position

模块：`models/position.py`
性质：可变事实记录，由 Broker 持有

| 字段 | 类型 | 说明 |
|------|------|------|
| symbol | str | 标的代码（PK） |
| side | str | long / short / flat |
| quantity | float | 当前持有量 |
| avg_entry_price | float | 加权平均入场价 |
| realized_pnl | float | 累计已实现盈亏 |

| 方法 | 签名 | 说明 |
|------|------|------|
| apply_fill | `(fill: FillResult) → float` | 更新持仓，返回本次 realized_pnl |

### Account

模块：`models/account.py`
性质：可变事实记录，由 SimExchangeProtocol 持有

| 字段 | 类型 | 说明 |
|------|------|------|
| initial_balance | float | 初始资金（不变） |
| total | float | 当前总资产 |

| 方法/属性 | 签名 | 说明 |
|-----------|------|------|
| free | `@property → float` | 可用余额，= total |
| settle | `(pnl: float, commission: float) → None` | total += pnl - commission |

---

## 公共模块 — common/

### Clock（接口）

模块：`common/clock.py`

| 方法 | 签名 | 说明 |
|------|------|------|
| now_ms | `() → int` | 当前时间戳（毫秒） |
| set_time_ms | `(ts: int) → None` | 设置时间（回测用） |

| 实现类 | 说明 |
|--------|------|
| LiveClock | 生产模式，返回系统时间 |
| SimulatedClock | 回测模式，由 RunBacktest 逐 bar 推进 |

---

## protocol 通用接口

每个服务通过 protocol 链（CacheLayer → SQLiteProtocol）读写自有数据。

| 方法 | 签名 | 说明 |
|------|------|------|
| get | `(key: str) → Any｜None` | 读取 |
| set | `(key: str, value: Any) → None` | 写入 / 覆盖 |
| append | `(key: str, item: Any) → None` | 追加到列表型 value |
| remove | `(key: str) → None` | 删除 |

protocol 链结构：

```
CacheLayer（内存缓存，读优先）
  └── SQLiteProtocol（磁盘持久化）
        路径：由服务的 cache_path 配置决定
```

---

## 继承关系总览

```
AppService (bollydog)
├── DataEngine                          data/app.py
├── AnalysisEngine                      analysis/engine.py
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
├── SubmitOrder                         execution/models.py
├── ImportKlines                        data/models.py
├── RunBacktest                         engine/command.py
└── OnBarReceived                       analysis/algo/retracement/command.py

BaseEvent (bollydog)
├── SignalEmitted                       models/signal.py
├── OrderFilled                         models/order.py
└── OrderRejected                       models/order.py

BaseModel (pydantic)
├── Signal                              models/signal.py
├── StrategyDecision                    strategy/models.py
├── Order                               models/order.py
├── FillResult                          models/order.py
├── Position                            models/position.py
└── Account                             models/account.py
```
