# Phase 5：接口契约 + 数据模型 + 序列化

> Issue: 20260524-initial-design

---

## 数据层 — data/

### DataEngine

| 可见性 | 方法 | 签名 | 说明 |
|--------|------|------|------|
| public | append_bars | `(symbol: str, interval: str, bars: list[dict]) → None` | 追加 K 线 |
| public | get_klines | `(symbol: str, interval: str, start_ts: int=None, end_ts: int=None) → list[dict]` | 查询 K 线 |
| public | set_klines | `(symbol: str, interval: str, klines: list[dict]) → None` | 全量写入 |

### PushBars

destination：`data.DataEngine.PushBars`

```python
class PushBars(BaseCommand):
    destination: ClassVar[str] = "data.DataEngine.PushBars"
    symbol: str = ""
    interval: str = ""
    bars: list = Field(default_factory=list)
    replay: bool = False
    async def __call__(self) -> dict: ...
```

### GetKlines

destination：`data.DataEngine.GetKlines`

```python
class GetKlines(BaseCommand):
    destination: ClassVar[str] = "data.DataEngine.GetKlines"
    symbol: str = ""
    interval: str = ""
    start_ts: int = 0
    end_ts: int = 0
    offset: int = 0
    limit: int = 0
    async def __call__(self) -> list: ...
```

### ImportKlines

destination：`data.DataEngine.ImportKlines`

```python
class ImportKlines(BaseCommand):
    destination: ClassVar[str] = "data.DataEngine.ImportKlines"
    path: str = ""
    symbol: str = ""
    interval: str = ""
    async def __call__(self) -> dict: ...
```

---

## 分析层 — analysis/

### AnalysisEngine（基类）

| 可见性 | 方法 | 签名 | 说明 |
|--------|------|------|------|
| public | on_bar | `(cmd: BaseCommand) → dict|None` | subscriber 入口 |
| internal | _warmup | `(symbol: str, interval: str, klines: list[dict]) → None` | 子类覆盖 |
| internal | _process_bar | `(symbol: str, interval: str, bar: dict) → dict{signals, breakouts}` | 子类覆盖 |

### ComputeRetracement

destination：`analysis.RetracementService.ComputeRetracement`

```python
class ComputeRetracement(BaseCommand):
    destination: ClassVar[str] = "analysis.RetracementService.ComputeRetracement"
    symbol: str = ""
    interval: str = ""
    klines: list = None
    async def __call__(self) -> dict | None: ...
```

### SignalEmitted（Event）

destination：`analysis.AnalysisEngine.SignalEmitted`

```python
class SignalEmitted(BaseEvent):
    destination: ClassVar[str] = "analysis.AnalysisEngine.SignalEmitted"
    ts: int = 0
    symbol: str = ""
    interval: str = ""
    direction: str = "neutral"
    strength: float = 0.0
    source: str = ""
    price: float = 0.0
    level: float = 0.0
    expires_at: int = 0
    metadata: dict = Field(default_factory=dict)
```

---

## 策略层 — strategy/

### FibStrategy

| 可见性 | 方法 | 签名 | 说明 |
|--------|------|------|------|
| public | on_signal | `(cmd: BaseCommand) → None` | 信号过滤 + 下单 |

配置：`position_size: float = 0.1` / `min_strength: float = 0.6`

subscriber：`{"analysis.AnalysisEngine.SignalEmitted": "on_signal"}`

---

## 执行层 — execution/

### Broker

| 可见性 | 方法 | 签名 | 说明 |
|--------|------|------|------|
| public | on_submit_order | `(order: Order, bar: dict=None) → FillResult|None` | 撮合入口 |
| public | process_pending | `(bar: dict) → None` | 挂单检查 |
| public | get_position | `(symbol: str) → Position` | 查询持仓 |
| public | get_all_positions | `() → dict{str: Position}` | 全部持仓 |
| public | get_account | `() → Account` | 查询账户 |

### SubmitOrder

destination：`execution.Broker.SubmitOrder`

```python
class SubmitOrder(BaseCommand):
    destination: ClassVar[str] = "execution.Broker.SubmitOrder"
    symbol: str = ""
    side: str = ""        # buy / sell
    order_type: str = ""  # market / limit / stop
    quantity: float = 0.0
    price: float = 0.0
    stop_price: float = 0.0
    bar: dict = Field(default_factory=dict)
    async def __call__(self) -> dict | None: ...
```

### CancelOrder

destination：`execution.Broker.CancelOrder`

```python
class CancelOrder(BaseCommand):
    destination: ClassVar[str] = "execution.Broker.CancelOrder"
    order_id: str = ""
    async def __call__(self) -> dict | None: ...
```

### SimExchangeProtocol

| 可见性 | 方法 | 签名 | 说明 |
|--------|------|------|------|
| override | submit_order | `(order: Order, bar=None) → FillResult|None` | market 撮合; limit/stop 入队 |
| override | check_pending | `(bar: dict) → list[FillResult]` | 遍历挂单触发 |
| override | get_balance | `() → Account` | 内存 Account |
| internal | _fill_market | `(order: Order, bar: dict) → FillResult` | 成交逻辑 |

配置：`initial_balance: float = 100000` / `slippage_pct: float = 0.001` / `commission_rate: float = 0.001`

### OrderFilled / OrderRejected（Event）

```python
class OrderFilled(BaseEvent):
    destination: ClassVar[str] = "execution.Broker.OrderFilled"
    order_id: str = ""
    symbol: str = ""
    side: str = ""
    filled_price: float = 0.0
    filled_quantity: float = 0.0
    commission: float = 0.0
    realized_pnl: float = 0.0
    ts: int = 0

class OrderRejected(BaseEvent):
    destination: ClassVar[str] = "execution.Broker.OrderRejected"
    order_id: str = ""
    symbol: str = ""
    reason: str = ""
    ts: int = 0
```

---

## 引擎层 — engine/

### RunBacktest

destination：`backtest.BacktestApp.RunBacktest`

```python
class RunBacktest(BaseCommand):
    destination: ClassVar[str] = "backtest.BacktestApp.RunBacktest"
    symbol: str = ""
    interval: str = ""
    warmup_bars: int = 200
    async def __call__(self) -> dict: ...
```

返回：`{symbol, interval, klines, signals, decisions, fills, account, positions}`

### BatchBacktest

destination：`backtest.BacktestApp.BatchBacktest`

```python
class BatchBacktest(BaseCommand):
    destination: ClassVar[str] = "backtest.BacktestApp.BatchBacktest"
    symbol: str = ""
    interval: str = ""
    warmup_bars: int = 200
    param_grid: dict = Field(default_factory=dict)
    async def __call__(self) -> list: ...
```

返回：`[{params: dict, result: dict, metrics: dict}, ...]`

---

## 后管层 — dashboard/

### GetStatus / ListRuns / GetRun / StartBatch / ListDatasets / UploadData

```python
class GetStatus(BaseCommand):
    destination: ClassVar[str] = "dashboard.DashboardService.GetStatus"
    async def __call__(self) -> dict: ...  # {services, current_job}

class ListRuns(BaseCommand):
    destination: ClassVar[str] = "dashboard.DashboardService.ListRuns"
    limit: int = 50
    offset: int = 0
    async def __call__(self) -> dict: ...  # {runs, total}

class GetRun(BaseCommand):
    destination: ClassVar[str] = "dashboard.DashboardService.GetRun"
    run_id: str = ""
    async def __call__(self) -> dict | None: ...

class StartBatch(BaseCommand):
    destination: ClassVar[str] = "dashboard.DashboardService.StartBatch"
    symbol: str = ""
    interval: str = ""
    warmup_bars: int = 200
    param_grid: dict = Field(default_factory=dict)
    async def __call__(self) -> dict: ...  # {job_id, status="started"}

class ListDatasets(BaseCommand):
    destination: ClassVar[str] = "dashboard.DashboardService.ListDatasets"
    async def __call__(self) -> dict: ...  # {datasets}

class UploadData(BaseCommand):
    destination: ClassVar[str] = "dashboard.DashboardService.UploadData"
    symbol: str = ""
    interval: str = ""
    file: dict = Field(default_factory=dict)
    async def __call__(self) -> dict: ...  # {symbol, interval, count}
```

### BacktestProgress（Event）

```python
class BacktestProgress(BaseEvent):
    destination: ClassVar[str] = "dashboard.DashboardService.BacktestProgress"
    job_id: str = ""
    run_index: int = 0
    total_runs: int = 0
    status: str = "running"  # running / completed / failed
    params: dict = Field(default_factory=dict)
    metrics: dict = Field(default_factory=dict)
    run_id: str = ""
```

---

## 数据模型

### Kline

| 字段 | 类型 | 说明 |
|------|------|------|
| symbol | str | PK 分区键 |
| interval | str | PK 分区键 |
| ts | int | PK 时间戳 ms |
| open | float | 开盘价 |
| high | float | 最高价 |
| low | float | 最低价 |
| close | float | 收盘价 |
| volume | float | 成交量 |

### Order

| 字段 | 类型 | 说明 |
|------|------|------|
| order_id | str | uuid |
| symbol | str | 标的 |
| side | str | buy/sell |
| order_type | str | market/limit/stop |
| quantity | float | 数量 |
| price | float | 限价 |
| stop_price | float | 止损价 |
| status | str | pending→submitted→filled/rejected/canceled |
| filled_quantity | float | 成交量 |
| filled_price | float | 成交价 |
| commission | float | 手续费 |
| created_at | int | 创建 ts |
| updated_at | int | 更新 ts |

### FillResult

| 字段 | 类型 | 说明 |
|------|------|------|
| order_id | str | FK→Order |
| symbol | str | 标的 |
| side | str | buy/sell |
| filled_price | float | 成交价 |
| filled_quantity | float | 成交量 |
| commission | float | 手续费 |
| ts | int | 成交 ts |

### Position

| 字段 | 类型 | 说明 |
|------|------|------|
| symbol | str | PK |
| side | str | long/short/flat |
| quantity | float | 持有量 |
| avg_entry_price | float | 加权均价 |
| realized_pnl | float | 已实现盈亏 |

### Account

| 字段 | 类型 | 说明 |
|------|------|------|
| initial_balance | float | 初始资金 |
| total | float | 当前总资产 |

### BacktestRun

| 字段 | 类型 | 说明 |
|------|------|------|
| run_id | str | uuid[:12] |
| status | str | pending/running/completed/failed |
| symbol | str | 标的 |
| interval | str | 周期 |
| params | dict | 参数 |
| metrics | dict | 绩效 |
| created_at | int | 创建 ts |
| completed_at | int | 完成 ts |
| error | str | 失败原因 |

### BatchJob

| 字段 | 类型 | 说明 |
|------|------|------|
| job_id | str | uuid[:12] |
| status | str | pending/running/completed |
| symbol | str | 标的 |
| interval | str | 周期 |
| param_grid | dict | 参数网格 |
| warmup_bars | int | 预热数 |
| total_runs | int | 总组数 |
| completed_runs | int | 已完成 |
| runs | list[str] | run_id 列表 |
| created_at | int | 创建 ts |

---

## 序列化方案

| 数据模型 | 存储位置 | 序列化 | 读写时机 |
|---------|---------|--------|---------|
| Kline | DuckDB `klines` 表 | 列式原生 | PushBars 写 / GetKlines 读 |
| Signal | SQLite `signals:{s}:{i}` | list[dict] JSON | _process_bar 写 / 回测汇总读 |
| StrategyDecision | SQLite `decisions:{s}` | list[dict] JSON | on_signal 写 / 回测汇总读 |
| Order | SQLite `__orders:{id}` | model_dump() | SubmitOrder 写 / mark_filled 更新 |
| FillResult | SQLite `__fills:{id}` | model_dump() | _process_fill 写 / 回测汇总读 |
| Position | SQLite `__positions` | dict JSON | apply_fill 写 / on_started 读 |
| Account | 内存 | — | on_start 初始化 |
| 回撤结构 | SQLite `retracement:{s}:{i}` | dict JSON | _warmup 写 / _process_bar 读 |
| checkpoint | SQLite `__ckpt:{s}:{i}` | int | on_bar 写读 |
| BacktestRun | SQLite `__runs` | list[model_dump()] | run_batch_job 写 / ListRuns 读 |
| BatchJob | SQLite `__current_job` | model_dump() | StartBatch 写 / GetStatus 读 |
| run detail | SQLite `__run_detail:{id}` | dict JSON | run_batch_job 写 / GetRun 读 |
