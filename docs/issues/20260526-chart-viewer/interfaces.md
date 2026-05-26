# P5 接口契约

## 新增 Command

### GetKlinesAPI

```python
class GetKlinesAPI(BaseCommand):
    destination: ClassVar[str] = "data.DataEngine.GetKlinesAPI"
    symbol: str = ""
    interval: str = ""
    start_ts: int = None
    end_ts: int = None
    limit: int = 5000
    async def __call__(self) -> list[dict]: ...
```

路由: `GET /api/data/klines?symbol=&interval=&start_ts=&end_ts=&limit=`

返回: `[{ts: int, open: float, high: float, low: float, close: float, volume: float}]`

### ListSymbols

```python
class ListSymbols(BaseCommand):
    destination: ClassVar[str] = "data.DataEngine.ListSymbols"
    async def __call__(self) -> list[dict]: ...
```

路由: `GET /api/data/symbols`

返回: `[{symbol: str, interval: str, count: int, first_ts: int, last_ts: int}]`

### GetSignals

```python
class GetSignals(BaseCommand):
    destination: ClassVar[str] = "analysis.RetracementService.GetSignals"
    symbol: str = ""
    interval: str = ""
    async def __call__(self) -> list[dict]: ...
```

路由: `GET /api/data/signals?symbol=&interval=`

返回: `[{ts: int, direction: str, strength: float, touch_price: float, level_price: float, source: str}]`

## 已有 Command（无需修改）

- `ListRuns(limit: int, offset: int) → dict` — `GET /api/dashboard/runs`
- `GetRun(run_id: str) → dict | None` — `GET /api/dashboard/runs/{run_id}`

## DataEngine 新增路由映射

```python
router_mapping = {
    "PushBars": ["POST", "/api/timing/push_bars"],
    "ImportKlines": ["POST", "/api/timing/import_klines"],
    "GetKlinesAPI": ["GET", "/api/data/klines"],      # 新增
    "ListSymbols": ["GET", "/api/data/symbols"],       # 新增
}
```

## RetracementService 新增路由映射

```python
router_mapping = {
    "GetSignals": ["GET", "/api/data/signals"],        # 新增
}
```

## 前端数据类型（TypeScript）

```typescript
interface Kline {
  ts: number
  open: number
  high: number
  low: number
  close: number
  volume: number
}

interface Signal {
  ts: number
  direction: 'long' | 'short' | 'neutral'
  strength: number
  touch_price: number
  level_price: number
  source: string
}

interface Fill {
  order_id: string
  symbol: string
  side: 'buy' | 'sell'
  filled_price: number
  filled_quantity: number
  commission: number
  ts: number
}

interface RunSummary {
  run_id: string
  symbol: string
  interval: string
  params: Record<string, number>
  metrics: Metrics
  status: string
}

interface Metrics {
  total_return: number
  max_drawdown: number
  win_rate: number
  sharpe_ratio: number
  profit_factor: number
  trade_count: number
}

interface RunDetail extends RunSummary {
  result: {
    klines: Kline[]
    fills: Fill[]
    signals: Signal[]
    account: { initial_balance: number; total: number }
  }
}
```
