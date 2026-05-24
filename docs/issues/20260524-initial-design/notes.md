# Notes — 20260524-initial-design

---

## Intersection Analysis

本 issue 为系统初始构建，REGISTRY.md 为空，无需交叉分析。所有实体均为新增。

| 问题 | 答案 | 动作 |
|------|------|------|
| 新 domain? | Y — data, analysis, strategy, execution, backtest, timing, dashboard | 7 个新 domain |
| 新 Service? | Y — 8 个 AppService | 全部新增 |
| 新 Command? | Y — 14 个 | 全部新增 |
| 新 Event? | Y — 4 个 | 全部新增 |
| 新 Protocol? | Y — SimExchangeProtocol | 自定义 Protocol + 框架标准组合 |

---

## Registry Delta

```
+ service DataEngine domain=data protocol=DuckDB
+ service AnalysisEngine domain=analysis
+ service RetracementService domain=analysis protocol=CacheLayer→SQLiteProtocol
+ service FibStrategy domain=strategy protocol=CacheLayer→SQLiteProtocol
+ service Broker domain=execution protocol=SimExchangeProtocol→CacheLayer→SQLiteProtocol
+ service TimingApp domain=timing
+ service BacktestApp domain=backtest
+ service DashboardService domain=dashboard protocol=CacheLayer→SQLiteProtocol
+ command PushBars dest=data.DataEngine.PushBars
+ command GetKlines dest=data.DataEngine.GetKlines
+ command ImportKlines dest=data.DataEngine.ImportKlines
+ command ComputeRetracement dest=analysis.RetracementService.ComputeRetracement
+ command SubmitOrder dest=execution.Broker.SubmitOrder
+ command CancelOrder dest=execution.Broker.CancelOrder
+ command RunBacktest dest=backtest.BacktestApp.RunBacktest
+ command BatchBacktest dest=backtest.BacktestApp.BatchBacktest
+ command GetStatus dest=dashboard.DashboardService.GetStatus
+ command ListRuns dest=dashboard.DashboardService.ListRuns
+ command GetRun dest=dashboard.DashboardService.GetRun
+ command StartBatch dest=dashboard.DashboardService.StartBatch
+ command ListDatasets dest=dashboard.DashboardService.ListDatasets
+ command UploadData dest=dashboard.DashboardService.UploadData
+ event SignalEmitted source=AnalysisEngine subscribers=FibStrategy.on_signal
+ event OrderFilled source=Broker
+ event OrderRejected source=Broker
+ event BacktestProgress source=BatchBacktest/DashboardService
+ protocol SimExchangeProtocol type=Custom used_by=Broker
+ protocol CacheLayer→SQLiteProtocol type=KV used_by=RetracementService
+ protocol CacheLayer→SQLiteProtocol type=KV used_by=FibStrategy
+ protocol CacheLayer→SQLiteProtocol type=KV used_by=DashboardService
+ depends TimingApp +DataEngine,RetracementService,FibStrategy,Broker
+ depends BacktestApp +DataEngine,FibStrategy,Broker
+ depends RetracementService +DataEngine
+ config timing.engine.app.TimingApp
+ config timing.engine.app.BacktestApp
+ config timing.dashboard.app.DashboardService
+ config timing.data.app.DataEngine
+ config timing.analysis.algo.retracement.service.RetracementService
+ config timing.strategy.app.FibStrategy
+ config timing.execution.broker.Broker
```

---

## Skeleton Notes

### 当前状态

系统已跑通所有主流程，无 `_stub_` 方法。

### 验证命令

```bash
# 列出所有 Command
.venv/bin/python3 main.py ls --config config.toml

# 导入数据
.venv/bin/python3 main.py execute ImportKlines \
  --config config.toml --path cache/159363.OF_1d.parquet \
  --symbol 159363.OF --interval 1d

# 单次回测
.venv/bin/python3 main.py execute RunBacktest \
  --config config.toml --symbol 159363.OF --interval 1d

# 批量参数回测
.venv/bin/python3 run_batch.py --config batch_config.toml

# 启动完整服务（HTTP + WS + Dashboard）
ENTRYPOINT_HTTP_ENABLED=1 ENTRYPOINT_WS_ENABLED=1 \
  .venv/bin/python3 main.py service --config config.toml

# 验证 API
curl http://localhost:8000/api/dashboard/status

# 验证前端
open http://localhost:8000/
```

### Stub 盘点

```bash
grep -rn "_stub_" timing/
# 结果：无
```

### 功能缺口（非 stub，属预留）

| # | 功能 | 位置 | 说明 |
|---|------|------|------|
| 1 | CancelOrder 执行逻辑 | execution/models.py | Command 已定义，`__call__` 未实现 |
| 2 | LiveExchangeProtocol | execution/adapters/live.py | 预留文件 |
| 3 | FibLevelTouched / FibInvalidated | analysis/algo/retracement/models.py | Event 定义了未 dispatch |

---

## Decisions

### D1: 逐 bar 循环由 RunBacktest 驱动

RunBacktest 内部循环调用 `_process_bar` + `hub.execute(SignalEmitted)` 实现同步链路。
回测不走 Exchange subscriber 路径（即不触发 PushBars 的 on_bar），避免异步和批量处理的复杂性。

### D2: 状态隔离通过 protocol.remove 实现

BatchBacktest 和 DashboardService 的 `_reset_state` 方法在每次回测前清除所有 protocol 键，
而非重建服务实例。优点是速度快、无需重新初始化 Protocol 连接。

### D3: Dashboard 静态文件延迟挂载

`DashboardService.on_started` 使用 `asyncio.ensure_future + sleep(0.5)` 延迟挂载 StaticFiles，
原因是 HttpService 的 on_started 会遍历路由打日志，Mount 对象没有 `methods` 属性会报错。

### D4: BacktestProgress 通过 hub.dispatch 广播

进度事件使用 `hub.dispatch`（异步，不阻塞回测循环），SocketService 自动转发到 WebSocket 客户端。
前端通过 WebSocket 监听该事件实现实时进度更新。

### D5: Protocol 组合模式

| 服务 | Protocol 链 | 理由 |
|------|------------|------|
| DataEngine | 直接 DuckDB | 列式存储，大量 K 线数据适合列式引擎 |
| RetracementService | CacheLayer → SQLiteProtocol | 频繁读写 checkpoint 和信号，需要热缓存 |
| FibStrategy | CacheLayer → SQLiteProtocol | decisions 需持久化但读写频率不高 |
| Broker | SimExchangeProtocol → CacheLayer → SQLiteProtocol | 三层：交易逻辑层 + 缓存层 + 持久化层 |
| DashboardService | CacheLayer → SQLiteProtocol | 回测记录存储，按需刷盘 |

### D6: TOML 配置最小化原则

config.toml 只包含框架 wiring 键和非默认参数覆盖。
服务的 `position_size`、`min_strength` 等业务参数在类 `__init__` 中定义默认值，
TOML 不重复声明这些默认值。

---

## 文件清单

```
timing/
├── main.py                                     # CLI 入口
├── run_batch.py                                # 批量回测脚本
├── config.toml                                 # 主配置
├── backtest.toml                               # 回测分析实例覆盖
├── batch_config.toml                           # 批量参数网格配置
├── common/
│   ├── clock.py                                # LiveClock / SimulatedClock
│   ├── metrics.py                              # compute_metrics
│   └── plot.py                                 # plot_backtest / plot_batch_comparison
├── models/
│   ├── kline.py, signal.py, order.py, position.py, account.py
├── data/
│   ├── app.py (DataEngine), models.py (PushBars/GetKlines/ImportKlines)
│   └── clients/file.py (read_file)
├── analysis/
│   ├── app.py (AnalysisEngine)
│   └── algo/retracement/ (service.py, command.py, config.py, algo.py, touch.py, models.py)
├── strategy/
│   ├── app.py (FibStrategy), models.py (StrategyDecision)
├── execution/
│   ├── broker.py (Broker), models.py (SubmitOrder/CancelOrder)
│   └── adapters/ (base.py, sim.py, live.py)
├── engine/
│   ├── app.py (TimingApp/BacktestApp), command.py (RunBacktest), batch.py (BatchBacktest)
├── dashboard/
│   ├── app.py (DashboardService), commands.py, models.py
└── web/
    ├── index.html, css/, js/ (Vue 3 CDN 前端)
```
