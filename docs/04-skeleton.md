# Phase 7：Walking Skeleton

---

## 当前骨架状态

系统已可跑通端到端主流程（推 bar → 分析 → 信号 → 策略 → 下单 → 撮合），具备完整的服务树。

### 主干路径验证

```bash
# 导入数据
.venv/bin/python3 main.py execute ImportKlines \
  --config config.toml --path cache/159363.OF_1d.parquet \
  --symbol 159363.OF --interval 1d

# 执行回测
.venv/bin/python3 main.py execute RunBacktest \
  --config config.toml --symbol 159363.OF --interval 1d
```

---

## TOML 配置

```toml
# ── 生产入口（纯容器）──
["timing.engine.app.TimingApp"]
depends = ["data.DataEngine", "analysis.RetracementService", "strategy.FibStrategy", "execution.Broker"]

# ── 回测入口 ──
["timing.engine.app.BacktestApp"]
depends = ["data.DataEngine", "strategy.FibStrategy", "execution.Broker"]

# ── 数据层 ──
["timing.data.app.DataEngine"]
db_path = "cache/data.duckdb"

# ── 分析层 ──
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

# ── 策略层（每个实例独立 SQLite）──
["timing.strategy.app.FibStrategy"]
subscriber = {"analysis.AnalysisEngine.SignalEmitted" = "on_signal"}

["timing.strategy.app.FibStrategy".protocol]
module = "bollydog.adapters.composite.CacheLayer"

["timing.strategy.app.FibStrategy".protocol.protocol]
module = "bollydog.adapters.memory.SQLiteProtocol"
path = "cache/strategy/fib_strategy.sqlite"

# ── 执行层（每个实例独立 SQLite）──
["timing.execution.broker.Broker"]

["timing.execution.broker.Broker".protocol]
module = "timing.execution.adapters.sim.SimExchangeProtocol"

["timing.execution.broker.Broker".protocol.protocol]
module = "bollydog.adapters.composite.CacheLayer"

["timing.execution.broker.Broker".protocol.protocol.protocol]
module = "bollydog.adapters.memory.SQLiteProtocol"
path = "cache/execution/broker.sqlite"
```

---

## 文件清单

```
timing/
├── main.py                                     # CLI 入口
├── config.toml                                 # 生产 + 回测配置
├── backtest.toml                               # 回测覆盖配置
├── common/
│   └── clock.py                                # LiveClock / SimulatedClock
├── models/
│   ├── __init__.py
│   ├── kline.py                                # Kline 模型
│   ├── signal.py                               # Signal + SignalEmitted
│   ├── order.py                                # Order + FillResult + OrderFilled/Rejected
│   ├── position.py                             # Position
│   └── account.py                              # Account
├── data/
│   ├── app.py                                  # DataEngine
│   └── models.py                               # PushBars / GetKlines / ImportKlines
├── analysis/
│   ├── app.py                                  # AnalysisEngine 基类
│   └── algo/retracement/
│       ├── service.py                          # RetracementService
│       ├── config.py                           # RetracementConfig
│       ├── algo.py                             # compute_retracement
│       └── touch.py                            # 触碰检测
├── strategy/
│   └── app.py                                  # FibStrategy
├── execution/
│   ├── broker.py                               # Broker
│   ├── models.py                               # SubmitOrder / CancelOrder
│   └── adapters/
│       ├── base.py                             # ExchangeProtocol 抽象
│       └── sim.py                              # SimExchangeProtocol
└── engine/
    ├── app.py                                  # TimingApp / BacktestApp
    └── command.py                              # RunBacktest
```

---

## Stub 盘点

当前无 `_stub_` 前缀方法。但以下功能虽有接口定义却**无实际实现**，属于隐性 stub：

| # | 功能 | 位置 | 状态 |
|---|------|------|------|
| 1 | Signal 序列化 | analysis/app.py on_bar | 内存流转，未写入 protocol |
| 2 | StrategyDecision 序列化 | strategy/app.py on_signal | 模型未定义，无写入 |
| 3 | Order 序列化 | execution/broker.py | 未写入 protocol |
| 3a| FillResult 序列化 | execution/broker.py | 未写入 protocol |
| 4 | process_pending(bar) | execution/broker.py | 方法不存在 |
| 5 | RunBacktest 逐 bar 循环 | engine/command.py | 使用一次触发批量方式 |
| 6 | RunBacktest 结果汇总 | engine/command.py | 返回统计而非完整中间结果 |
| 7 | CancelOrder 逻辑 | execution/models.py | Command 定义了但无执行逻辑 |
| 8 | Order.cancel 状态 | models/order.py | 状态定义了但无 CancelOrder 逻辑 |

---

## 验证命令

```bash
# 检查注册的 Command
.venv/bin/python3 main.py ls --config config.toml

# 端到端执行
.venv/bin/python3 main.py execute RunBacktest \
  --config config.toml --symbol 159363.OF --interval 1d

# 查看隐性 stub（接口定义但未实现的功能）
rg "TODO|FIXME|NotImplemented" timing/
```

---

## 差异设计描述

以设计文档为准，后续代码迭代时统一处理：

- [x] 后续新增的 stub 方法统一加 `_stub_` 前缀
- [x] RunBacktest 一次触发批量 + 返回完整中间结果
- [x] BacktestApp 动态创建实例 + 清除 _services 隔离
- [x] config.toml 中为 FibStrategy 增加 protocol 配置（CacheLayer → SQLiteProtocol）
- [ ] 生产模式验证：确保 `bollydog service --config config.toml` 可正常启动
