# 问题附录

已解决的方案已分发到对应模块文档：

| 原问题 | 去向 |
|--------|------|
| 回测结果收集方案（BacktestCollector） | 06-engine-layer.md § 回测结果收集 |
| Signal 持久化 | 03-analysis-layer.md § TODO |
| FillResult 持久化 | 05-execution-layer.md § TODO |
| EquitySnap / PositionSnap 数据模型 | 01-data-models.md § ER 图（回测域） |

---

## 待讨论：端到端追踪中发现的设计问题

### 问题 1：RunBacktest"一次触发批量处理"与文档"逐 bar 循环"设计不一致

**现状**：`RunBacktest.__call__` 清除 checkpoint 后构造一个 `PushBars(bars=[], replay=True)`，通过 `exchange.match` 触发一次 `on_bar`。`on_bar` 内部通过 `GetKlines` 拉全量数据并自行逐 bar 循环 `_process_bar`。

**文档设计**（06-engine-layer.md § 逐 bar 循环伪码）：`RunBacktest` 外层逐 bar 循环 `PushBars(bars=[bar])`，每根 bar 后调用 `broker.process_pending(bar)` 和 `BacktestCollector` 采集。

**影响**：
- `broker.process_pending(bar)` 无法在每根 bar 后调用 → **Limit/Stop 挂单在回测中永远不会被触发**
- `BacktestCollector` 的逐 bar 权益采集无法实现 → **无法生成 equity_curve**
- 外层无法感知 on_bar 内部处理了多少根 bar → **结果统计信息不完整**

**可选方案**：
- **A. 重构 RunBacktest 为真正的逐 bar 循环**：外层 `for bar in klines[warmup:]`，每根 bar dispatch `PushBars(bars=[bar])` → 与文档设计一致，但需要解决 `on_bar` 内部 `GetKlines` 重复拉取的问题
- **B. 在 on_bar 内部增加回调机制**：每处理完一根 bar 后回调 `RunBacktest` 提供的 hook → 可调用 `process_pending` 和采集权益，但侵入分析层基类

### 问题 2：BacktestCollector 仅存在于文档设计中

**现状**：06-engine-layer.md 中详细设计了 `BacktestCollector` 的结构、收集时机和汇总指标，但实际代码中没有实现。当前 `RunBacktest` 仅返回 `{success, failed, errors}` 统计。

**依赖**：依赖问题 1 的解决（需要逐 bar 循环才能按文档设计采集数据）。

### 问题 3：三项 TODO 持久化均未实现

| 持久化项 | 标注位置 | key | 状态 |
|----------|---------|-----|------|
| Signal 序列化 | 03-analysis-layer.md § TODO | `signals:{s}:{i}` | 未实现 |
| StrategyDecision 序列化 | 04-strategy-layer.md § on_signal | `decisions:{s}:{i}` | 未实现 |
| FillResult 序列化 | 05-execution-layer.md § TODO | `__fills` | 未实现 |

这三项影响回测结果完整性和审计追溯能力，优先级应跟随 BacktestCollector 一起实现。

### 问题 4：OrderFilled / OrderRejected 无下游 subscriber

**现状**：`Broker._sync_emit(OrderFilled)` 会调用 `exchange.match` 查找 subscriber，但 `config.toml` 和代码中均未注册任何 subscriber 监听这两个事件。

**影响**：
- `_sync_emit` 执行后 `for handler_cls in ...` 循环体不会进入 → 事件发出但无人接收
- 如果未来需要风控模块或统计模块订阅成交事件，需要注册 subscriber

**建议**：当前不影响功能（事件广播是单向的），但应在文档中明确标注"OrderFilled/OrderRejected 当前无 subscriber，预留扩展接口"。

### 问题 5：FibStrategy 使用信号价格构造 bar 传给 Broker

**现状**（strategy/app.py）：

```python
bar = {"close": price, "ts": event_data.get("ts", 0)}
order = SubmitOrder(symbol=symbol, side=side, quantity=..., bar=bar)
```

`price` 来自 `SignalEmitted.price`（即信号的触发价格 / touch_price），不是当前 K 线的 close。

**影响**：Broker 市价撮合使用 `bar["close"]` 计算成交价：

```python
# SimExchangeProtocol._fill_market
base_price = bar.get("close", bar.get("open", 0.0))  # 这里的 bar 是 FibStrategy 构造的
fill_price = base_price * (1 + self.slippage_pct * slip_direction)
```

这意味着撮合基准价是**信号的触发价格**而非**当前 bar 的实际 close**。在大多数场景下两者接近（信号本身就是对当前 bar 的检测），但在突破重算等场景中可能产生偏差。

**建议**：明确语义——如果设计意图是按信号价格成交（类似市价单 + 指定价格），则无问题但应在文档中说明；如果期望按原始 bar close 成交，需要在 `on_bar` → `SignalEmitted` 链路中传递原始 bar 数据。

### 问题 6：Limit/Stop 单触发后按 bar close 而非限价成交

**现状**（execution/adapters/sim.py）：`check_pending` 检测到触发后直接调用 `_fill_market(order, bar)`，成交价按 `bar["close"] ± slippage` 计算。

**影响**：
- **Limit 单**：设定限价 1.200，触发 bar 的 close=1.190 → 成交价 ≈ 1.191。实际交易所应按限价 1.200 或更优价格成交（对买单而言 ≤ 1.200）
- **Stop 单**：触发后转市价，按 close 成交是合理的

**建议**：Limit 单触发后应按 `min(order.price, bar_close)` (买) / `max(order.price, bar_close)` (卖) 成交，而非直接用 close。
