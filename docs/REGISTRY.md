# System Registry

> 纯当前状态快照。追溯性由 git commit trailers 承载。

---

## Services

| Service | Domain | Alias | Protocol | Depends |
|---------|--------|-------|----------|---------|
| DataEngine | data | DataEngine | DuckDB (db_path) | — |
| AnalysisEngine | analysis | AnalysisEngine | — (abstract) | DataEngine |
| RetracementService | analysis | RetracementService | CacheLayer → SQLiteProtocol | DataEngine |
| FibStrategy | strategy | FibStrategy | CacheLayer → SQLiteProtocol | — |
| Broker | execution | Broker | SimExchangeProtocol → CacheLayer → SQLiteProtocol | — |
| TimingApp | timing | TimingApp | — | DataEngine, RetracementService, FibStrategy, Broker |
| BacktestApp | backtest | BacktestApp | — | DataEngine, FibStrategy, Broker |
| DashboardService | dashboard | DashboardService | CacheLayer → SQLiteProtocol | — |

## Commands

| Command | Service | Destination | QoS |
|---------|---------|-------------|-----|
| PushBars | DataEngine | `data.DataEngine.PushBars` | — |
| GetKlines | DataEngine | `data.DataEngine.GetKlines` | — |
| ImportKlines | DataEngine | `data.DataEngine.ImportKlines` | — |
| ComputeRetracement | RetracementService | `analysis.RetracementService.ComputeRetracement` | — |
| SubmitOrder | Broker | `execution.Broker.SubmitOrder` | — |
| CancelOrder | Broker | `execution.Broker.CancelOrder` | — |
| RunBacktest | BacktestApp | `backtest.BacktestApp.RunBacktest` | — |
| BatchBacktest | BacktestApp | `backtest.BacktestApp.BatchBacktest` | — |
| GetStatus | DashboardService | `dashboard.DashboardService.GetStatus` | — |
| ListRuns | DashboardService | `dashboard.DashboardService.ListRuns` | — |
| GetRun | DashboardService | `dashboard.DashboardService.GetRun` | — |
| StartBatch | DashboardService | `dashboard.DashboardService.StartBatch` | — |
| ListDatasets | DashboardService | `dashboard.DashboardService.ListDatasets` | — |
| UploadData | DashboardService | `dashboard.DashboardService.UploadData` | — |

## Events

| Event | Source | Subscribers |
|-------|--------|-------------|
| SignalEmitted | AnalysisEngine / RunBacktest | FibStrategy.on_signal |
| OrderFilled | Broker | (none) |
| OrderRejected | Broker | (none) |
| BacktestProgress | BatchBacktest / DashboardService | (SocketService → WebSocket) |

## Protocols

| Protocol | Type | Used By | Config |
|----------|------|---------|--------|
| CacheLayer → SQLiteProtocol | KV composite | RetracementService | flush=1, path=cache/analysis/retracement.sqlite |
| CacheLayer → SQLiteProtocol | KV composite | FibStrategy | path=cache/strategy/fib_strategy.sqlite |
| SimExchangeProtocol → CacheLayer → SQLiteProtocol | Custom + KV | Broker | initial_balance=100000, path=cache/execution/broker.sqlite |
| CacheLayer → SQLiteProtocol | KV composite | DashboardService | path=cache/dashboard/dashboard.sqlite |

## TOML Nodes

```toml
["timing.engine.app.TimingApp"]
depends = ["data.DataEngine", "analysis.RetracementService", "strategy.FibStrategy", "execution.Broker"]

["timing.engine.app.BacktestApp"]
depends = ["data.DataEngine", "strategy.FibStrategy", "execution.Broker"]

["timing.dashboard.app.DashboardService"]
cache_path = "cache/dashboard"

["timing.data.app.DataEngine"]
db_path = "cache/data.duckdb"

["timing.analysis.algo.retracement.service.RetracementService"]
cache_path = "cache/analysis"
depends = ["data.DataEngine"]
subscriber = {"data.DataEngine.PushBars" = "on_bar"}

["timing.analysis.algo.retracement.service.RetracementService".protocol]
module = "bollydog.adapters.composite.CacheLayer"
flush_threshold = 1

["timing.analysis.algo.retracement.service.RetracementService".protocol.protocol]
module = "bollydog.adapters.memory.SQLiteProtocol"
path = "cache/analysis/retracement.sqlite"

["timing.strategy.app.FibStrategy"]
subscriber = {"analysis.AnalysisEngine.SignalEmitted" = "on_signal"}

["timing.strategy.app.FibStrategy".protocol]
module = "bollydog.adapters.composite.CacheLayer"

["timing.strategy.app.FibStrategy".protocol.protocol]
module = "bollydog.adapters.memory.SQLiteProtocol"
path = "cache/strategy/fib_strategy.sqlite"

["timing.execution.broker.Broker"]

["timing.execution.broker.Broker".protocol]
module = "timing.execution.adapters.sim.SimExchangeProtocol"
initial_balance = 100000
slippage_pct = 0.001
commission_rate = 0.001

["timing.execution.broker.Broker".protocol.protocol]
module = "bollydog.adapters.composite.CacheLayer"
flush_threshold = 1

["timing.execution.broker.Broker".protocol.protocol.protocol]
module = "bollydog.adapters.memory.SQLiteProtocol"
path = "cache/execution/broker.sqlite"
```
