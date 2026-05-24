# Phase 4：端到端顺序图

> Issue: 20260524-initial-design

---

## 场景覆盖矩阵

| 顺序图 | 覆盖场景 | 核心路径 |
|--------|---------|---------|
| 图 1 | A1 | 推 bar → 触碰信号 → 市价成交 |
| 图 2 | A2, A3, A4 | 分析层分支（无信号 / 突破重算 / 信号弱跳过） |
| 图 3 | A5, A7 | 执行层分支（余额拒绝 / 限价挂单触发） |
| 图 4 | B1 | 回测完整流程（逐 bar 循环） |
| 图 5 | A6, A9, B2, B3 | 查询场景 |
| 图 6 | A10 | 数据文件导入 |
| 图 7 | C2 | 批量参数回测 |
| 图 8 | D2, D6 | Dashboard 启动批量 + 实时进度 |

---

## 图 1：A1 — 推 bar → 触碰信号 → 市价成交

```
外部
  → hub.execute(PushBars(symbol: str, interval: str, bars: list, replay: bool)) → dict
    → app.append_bars(symbol, interval, bars) → None
    → protocol(TableCacheLayer).set(key, bars)
  ← return {symbol, interval, bars}
  → _publish("data.DataEngine.PushBars")
    → Exchange.match → RetracementService.on_bar(cmd)
      → protocol.get("__ckpt:{s}:{i}") → int|None
      → hub.execute(GetKlines(symbol, interval, start_ts=ckpt+1)) → list
      → loop 每根增量 bar:
          clock.set_time_ms(bar.ts)
          _process_bar(symbol, interval, bar) → {signals, breakouts}
      → protocol.set("__ckpt:{s}:{i}", last_ts)
      → loop 每个 signal:
          → Exchange.match("analysis.AnalysisEngine.SignalEmitted")
            → FibStrategy.on_signal(cmd)
              → cmd.get_event() → {symbol, direction, strength, price}
              → strength >= min_strength → 通过
              → hub.execute(SubmitOrder(symbol, side, order_type=market, quantity, bar)) → dict|None
                → app.get_balance() → Account
                → app.submit_order(order, bar) → FillResult
                  → _fill_market(order, bar) → FillResult
                  → account.settle(pnl, commission)
                → position.apply_fill(fill) → rpnl
                → protocol.set("__positions", positions)
                → hub.emit(OrderFilled{order_id, symbol, ...})
              ← return fill.model_dump()
```

---

## 图 2：A2/A3/A4 — 分析层三条分支

```
alt A2: signals=[], breakouts=[] — 无触碰无突破
  → protocol.set("__ckpt:{s}:{i}", bar_ts)
  ← 链路结束

alt A3: breakouts=[group] — 突破边界
  → hub.execute(GetKlines(symbol, interval)) → 全量 klines
  → compute_retracement(klines) → new_result
  → protocol.set("retracement:{s}:{i}", new_result)
  ← 链路结束（指标结构更新，无 signal）

alt A4: signals=[sig{strength=0.3}]
  → Exchange.match → FibStrategy.on_signal(cmd)
    → strength(0.3) < min_strength(0.6) → skip
    → protocol.set("decisions:{s}", [..., {action: "skip", reason: ...}])
  ← 链路结束
```

---

## 图 3：A5/A7 — 执行层分支

### 3a: A5 — 余额不足

```
FibStrategy
  → hub.execute(SubmitOrder(symbol, side=buy, quantity=100, bar)) → None
    → app.get_balance() → Account{total=5000}
    → free(5000) < cost(10000) → reject
    → hub.emit(OrderRejected{order_id, symbol, reason="insufficient_balance"})
  ← return None
```

### 3b: A7 — 限价挂单 → 后续 bar 触发

```
Bar N:
  → SubmitOrder(symbol, side=buy, order_type=limit, price=1.200, bar)
    → app.submit_order(order, bar) → None (入挂单队列 _pending_orders)
  ← return None

Bar N+2 (via process_pending):
  → broker.process_pending(bar{low=1.180})
    → SimExchange.check_pending(bar) → [FillResult]
      → bar.low ≤ order.price → _fill_market(order, bar) → FillResult
    → position.apply_fill(fill) → rpnl
    → protocol.set("__positions", ...)
    → hub.emit(OrderFilled{...})
```

---

## 图 4：B1 — 回测主流程（逐 bar 循环）

```
CLI
  → hub.execute(RunBacktest(symbol: str, interval: str, warmup_bars: int)) → dict
    → hub.execute(GetKlines(symbol, interval)) → 全量 klines
    → analysis_svcs = AnalysisEngine._services.values()
    → broker = app._children[Broker]
    → clock = SimulatedClock()
    → AnalysisEngine.clock = clock

    准备阶段:
    → loop 每个分析服务:
        protocol.remove("__ckpt" + "signals" + "_touch")
        _warmup(symbol, interval, klines[:warmup_n])

    逐 bar 循环 (i = warmup_n .. N):
    → loop 每根 replay bar:
        clock.set_time_ms(bar.ts)
        broker.process_pending(bar)
        → loop 每个分析服务:
            svc._process_bar(symbol, interval, bar) → {signals, breakouts}
            → if signals:
                protocol.set("signals:{s}:{i}", append)
                → loop 每个 signal:
                    hub.execute(SignalEmitted{ts, symbol, direction, strength, price, ...})
                      → FibStrategy.on_signal → SubmitOrder → Broker

    结果收集:
    → protocol.get("signals:{s}:{i}") → all_signals
    → protocol.get("decisions:{s}") → all_decisions
    → protocol.keys("__fills:*") → all_fills
    → broker.get_account() → Account
    → broker.get_all_positions() → dict

  ← return {symbol, interval, klines, signals, decisions, fills, account, positions}
```

---

## 图 5：A6/A9/B2/B3 — 查询场景

```
查询方
  → broker.get_position(symbol) → Position
  → broker.get_all_positions() → dict{symbol: Position}
  → broker.get_account() → Account
```

---

## 图 6：A10 — 数据文件导入

```
CLI
  → hub.execute(ImportKlines(symbol: str, interval: str, path: str)) → dict
    → read_file(path) → list[dict] (支持 parquet/csv/json)
    → app.set_klines(symbol, interval, klines)
  ← return {symbol, interval, count}
```

---

## 图 7：C2 — 批量参数回测

```
CLI
  → hub.execute(BatchBacktest(symbol: str, interval: str, warmup_bars: int, param_grid: dict)) → list
    → itertools.product(param_grid) → combos[]
    → loop 每组参数 combo[idx]:
        → 应用参数到分析服务: svc.config.update(analysis_params)
        → 应用参数到策略服务: svc.position_size / min_strength = ...
        → _reset_state(symbol, interval)
            → 清除 ckpt / signals / touch / retracement / decisions / fills / orders / positions / account
        → hub.dispatch(BacktestProgress{job_id, run_index=idx, status=running, params=combo})
        → hub.execute(RunBacktest(symbol, interval, warmup_bars)) → dict
        → compute_metrics(fills, initial, final) → metrics
        → hub.dispatch(BacktestProgress{job_id, run_index=idx, status=completed, metrics})
  ← return [{params, result, metrics}, ...]
```

---

## 图 8：D2/D6 — Dashboard 启动批量 + 实时进度

```
浏览器
  → POST /api/dashboard/batch {symbol, interval, param_grid}
    → hub.dispatch(StartBatch(symbol, interval, warmup_bars, param_grid)) → dict
      → svc = DashboardService
      → job = BatchJob(...)
      → protocol.set("__current_job", job)
      → asyncio.ensure_future(svc.run_batch_job(job))
    ← return {job_id, status="started"}

异步执行 run_batch_job(job):
  → loop 每组参数:
      → _reset_state(symbol, interval)
      → hub.dispatch(BacktestProgress{job_id, run_index, status=running})
      → hub.execute(RunBacktest(symbol, interval, warmup_bars)) → result
      → compute_metrics → metrics
      → protocol.set("__run_detail:{run_id}", {run + result})
      → hub.dispatch(BacktestProgress{job_id, run_index, status=completed, metrics})
        → Exchange → SocketService → WebSocket push → 浏览器
      → protocol.set("__runs", append run)

浏览器 (WebSocket):
  ← 接收 BacktestProgress{run_index, status, metrics}
  ← 实时更新进度条 + 结果表格
```

---

## 方法签名汇总

### Command 签名

| 命令 | 签名 | 出现图 |
|------|------|--------|
| PushBars | `PushBars(symbol: str, interval: str, bars: list, replay: bool) → dict` | 1 |
| GetKlines | `GetKlines(symbol: str, interval: str, start_ts: int, end_ts: int, offset: int, limit: int) → list` | 1, 2, 4 |
| ImportKlines | `ImportKlines(symbol: str, interval: str, path: str) → dict` | 6 |
| ComputeRetracement | `ComputeRetracement(symbol: str, interval: str, klines: list|None) → dict|None` | — |
| SubmitOrder | `SubmitOrder(symbol: str, side: str, order_type: str, quantity: float, price: float, stop_price: float, bar: dict) → dict|None` | 1, 3, 4 |
| CancelOrder | `CancelOrder(order_id: str) → dict|None` | — |
| RunBacktest | `RunBacktest(symbol: str, interval: str, warmup_bars: int) → dict` | 4, 7, 8 |
| BatchBacktest | `BatchBacktest(symbol: str, interval: str, warmup_bars: int, param_grid: dict) → list` | 7 |
| GetStatus | `GetStatus() → dict` | 8 |
| ListRuns | `ListRuns(limit: int, offset: int) → dict` | 8 |
| GetRun | `GetRun(run_id: str) → dict|None` | 8 |
| StartBatch | `StartBatch(symbol: str, interval: str, warmup_bars: int, param_grid: dict) → dict` | 8 |
| ListDatasets | `ListDatasets() → dict` | — |
| UploadData | `UploadData(symbol: str, interval: str, file: dict) → dict` | — |

### 服务方法

| 服务 | 方法 | 签名 |
|------|------|------|
| DataEngine | append_bars | `(symbol, interval, bars) → None` |
| DataEngine | get_klines | `(symbol, interval, start_ts=None, end_ts=None) → list[dict]` |
| DataEngine | set_klines | `(symbol, interval, klines) → None` |
| AnalysisEngine | on_bar | `(cmd) → dict|None` |
| AnalysisEngine | _warmup | `(symbol, interval, klines) → None` |
| AnalysisEngine | _process_bar | `(symbol, interval, bar) → dict{signals, breakouts}` |
| FibStrategy | on_signal | `(cmd) → None` |
| Broker | on_submit_order | `(order, bar=None) → FillResult|None` |
| Broker | process_pending | `(bar: dict) → None` |
| Broker | get_position | `(symbol) → Position` |
| Broker | get_all_positions | `() → dict{symbol: Position}` |
| Broker | get_account | `() → Account` |
| SimExchangeProtocol | submit_order | `(order, bar=None) → FillResult|None` |
| SimExchangeProtocol | check_pending | `(bar) → list[FillResult]` |
| SimExchangeProtocol | get_balance | `() → Account` |
| DashboardService | run_batch_job | `(job: BatchJob) → None` |
| DashboardService | _reset_state | `(symbol, interval) → None` |
