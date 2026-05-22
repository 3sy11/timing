# Phase 4：端到端顺序图

从 00-scenarios.md 场景故事出发，每条箭头是一次精确的方法调用：`Class.method(params) → ReturnType`。

## 场景覆盖矩阵

| 顺序图 | 覆盖场景 | 核心路径 |
|--------|---------|---------|
| 图 1 | A1 | 推 bar → 触碰信号 → 市价成交（主干） |
| 图 2 | A2, A3, A4 | 分析层三条分支（无信号 / 突破重算 / 信号弱跳过） |
| 图 3 | A5, A7 | 执行层两条分支（余额拒绝 / 限价挂单触发） |
| 图 4 | B1 | 回测完整流程 |
| 图 5 | A6, A9, B2, B3 | 查询中间记录 / 查询持仓账户 / 回测结果读取 |
| 图 6 | A10 | 数据文件导入 |

---

## 图 1：A1 主流程 — 推 bar → 触碰信号 → 市价成交

```mermaid
sequenceDiagram
    participant Ext as 外部
    participant DE as DataEngine
    participant EX as Exchange
    participant RS as RetracementService
    participant FS as FibStrategy
    participant BK as Broker
    participant SE as SimExchangeProtocol

    Note over Ext,SE: ══ 数据层 ══
    Ext->>DE: hub.execute(PushBars(symbol, interval, bars, replay=false))
    DE->>DE: append_bars(symbol, interval, normalized)
    DE-->>Ext: dict{symbol, interval, bars}
    DE->>EX: _publish("data.DataEngine.PushBars", result)

    Note over Ext,SE: ══ 分析层 ══
    EX->>RS: on_bar(cmd)
    RS->>RS: protocol.get("__ckpt:{s}:{i}") → ckpt_ts
    RS->>DE: hub.execute(GetKlines(symbol, interval, start_ts=ckpt_ts+1))
    DE-->>RS: list[dict]
    loop 每根增量 bar
        RS->>RS: clock.set_time_ms(bar.ts)
        RS->>RS: _process_bar(symbol, interval, bar) → {signals, breakouts}
    end
    RS->>RS: protocol.set("__ckpt:{s}:{i}", last_bar_ts)

    loop 每个 signal
        RS->>EX: exchange.match("analysis.AnalysisEngine.SignalEmitted")
        EX->>FS: on_signal(cmd)

        Note over Ext,SE: ══ 策略层 ══
        FS->>FS: cmd.get_event() → event_data{symbol, direction, strength, price}
        FS->>BK: hub.execute(SubmitOrder(symbol, side=buy, order_type=market, quantity, bar))

        Note over Ext,SE: ══ 执行层 ══
        BK->>BK: SubmitOrder(symbol, side, order_type, quantity, price, bar) → dict|None
        BK->>SE: get_balance() → Account{total}
        BK->>SE: submit_order(order, bar)
        SE->>SE: _fill_market(order, bar) → FillResult
        SE->>SE: account.settle(pnl, commission)
        SE->>SE: order.mark_filled(fill_price, quantity, commission, ts)
        SE-->>BK: FillResult{...}
        BK->>BK: position.apply_fill(fill) → rpnl
        BK->>BK: protocol.set("__positions", positions)
        BK->>EX: hub.emit(OrderFilled{...})
    end
```

### 图 1 方法调用清单

| # | 调用方 | 被调用方 | 方法签名 |
|---|--------|---------|---------|
| 1 | 外部 | DataEngine | `PushBars(symbol: str, interval: str, bars: list, replay: bool) → dict` |
| 2 | PushBars | DataEngine | `append_bars(symbol, interval, bars) → None` |
| 3 | 框架 | Exchange | `_publish(topic, result)` |
| 4 | Exchange | RetracementService | `on_bar(cmd) → dict｜None` |
| 5 | RetracementService | protocol | `get("__ckpt:{s}:{i}") → int｜None` |
| 6 | RetracementService | DataEngine | `hub.execute(GetKlines(symbol, interval, start_ts)) → list[dict]` |
| 7 | RetracementService | self | `_process_bar(symbol, interval, bar) → dict{signals, breakouts}` |
| 8 | RetracementService | protocol | `set("__ckpt:{s}:{i}", ts) → None` |
| 9 | Exchange | FibStrategy | `on_signal(cmd) → None` |
| 10 | FibStrategy | Broker | `hub.execute(SubmitOrder(...)) → dict｜None` |
| 11 | Broker | SimExchange | `get_balance() → Account` |
| 12 | Broker | SimExchange | `submit_order(Order, bar) → FillResult｜None` |
| 13 | SimExchange | self | `_fill_market(Order, bar) → FillResult` |
| 14 | SimExchange | Account | `settle(pnl, commission) → None` |
| 15 | Broker | Position | `apply_fill(FillResult) → float(rpnl)` |
| 16 | Broker | protocol | `set("__positions", dict) → None` |
| 17 | Broker | Exchange | `hub.emit(OrderFilled) → None` |

---

## 图 2：A2/A3/A4 分析层三条分支

```mermaid
sequenceDiagram
    participant RS as RetracementService
    participant DE as DataEngine
    participant EX as Exchange
    participant FS as FibStrategy

    alt A2: signals=[], breakouts=[] — 无触碰无突破
        RS->>RS: protocol.set("__ckpt:{s}:{i}", bar_ts)
        Note over RS: 无 signal → 不广播 → 链路结束
    else A3: breakouts=[group] — 突破边界
        RS->>DE: hub.execute(GetKlines(symbol, interval)) → 全量
        RS->>RS: compute_retracement(klines) → new_result
        RS->>RS: protocol.set("retracement:{s}:{i}", new_result)
        Note over RS: 指标结构更新，无 signal → 链路结束
    else A4: signals=[sig{strength=0.3}] — 信号弱
        RS->>EX: exchange.match(SignalEmitted.destination)
        EX->>FS: on_signal(cmd)
        FS->>FS: strength(0.3) < min_strength(0.6) → skip
        Note over FS: 不产出 SubmitOrder → 链路结束
    end
```

---

## 图 3：A5 余额拒绝 + A7 限价挂单触发

### 3a: A5 — 余额不足

```mermaid
sequenceDiagram
    participant FS as FibStrategy
    participant BK as Broker
    participant SE as SimExchangeProtocol

    FS->>BK: hub.execute(SubmitOrder(symbol, side=buy, quantity=100))
    BK->>SE: get_balance() → Account{total=5000}
    BK->>BK: free(5000) < cost(10000) → emit OrderRejected
    BK-->>FS: None
```

### 3b: A7 — 限价挂单 → 后续 bar 触发

```mermaid
sequenceDiagram
    participant BK as Broker
    participant SE as SimExchangeProtocol

    Note over BK,SE: Bar N: 提交限价单
    BK->>SE: submit_order(Order{type=limit, price=1.200}) → None（入挂单队列）

    Note over BK,SE: Bar N+2: low=1.180 ≤ 1.200 触发
    BK->>SE: check_pending(bar)
    SE->>SE: _fill_market(order, bar) → FillResult
    SE-->>BK: [FillResult]
    BK->>BK: position.apply_fill(fill) → rpnl
    BK->>BK: protocol.set("__positions", ...)
```

---

## 图 4：B1 回测主流程

```mermaid
sequenceDiagram
    participant CLI as 命令行
    participant RB as RunBacktest
    participant DE as DataEngine
    participant RS as RetracementService
    participant FS as FibStrategy
    participant BK as Broker

    CLI->>RB: hub.execute(RunBacktest(symbol, interval))

    Note over CLI,BK: 准备阶段
    RB->>DE: hub.execute(GetKlines(symbol, interval)) → 全量 klines
    RB->>RS: 清除 _services 隔离生产配置
    RB->>RS: 清除 checkpoint

    Note over CLI,BK: 触发 on_bar（一次性）
    RB->>RS: exchange.match(PushBars.destination) → handlers
    RB->>RS: asyncio.gather(hub.execute(cmd) for cmd in handlers)

    Note over RS: on_bar 内部完成全量处理
    RS->>RS: GetKlines 全量 → warmup → 逐 bar _process_bar
    RS->>RS: 每个 signal → exchange.match → hub.execute → FibStrategy.on_signal
    RS->>FS: on_signal → SubmitOrder → Broker

    RB-->>CLI: {symbol, interval, services, klines_total, handlers, errors}
```

---

## 图 5：A6/A9/B2/B3 查询场景

```mermaid
sequenceDiagram
    participant User as 查询方
    participant BK as Broker

    User->>BK: get_position(symbol) → Position
    User->>BK: get_all_positions() → dict{symbol: Position}
    User->>BK: get_account() → Account
```

---

## 图 6：A10 数据文件导入

```mermaid
sequenceDiagram
    participant CLI as 命令行
    participant IK as ImportKlines
    participant DE as DataEngine

    CLI->>IK: hub.execute(ImportKlines(symbol, interval, path))
    IK->>DE: set_klines(symbol, interval, klines)
    IK-->>CLI: dict{symbol, interval, count}
```

---

## 方法签名汇总

### Command 签名

| 命令 | 签名 | 出现图 |
|------|------|--------|
| PushBars | `PushBars(symbol: str, interval: str, bars: list, replay: bool) → dict` | 1, 4 |
| GetKlines | `GetKlines(symbol: str, interval: str, start_ts: int, end_ts: int) → list` | 1, 2, 4 |
| SubmitOrder | `SubmitOrder(symbol: str, side: str, order_type: str, quantity: float, price: float, stop_price: float, bar: dict) → dict｜None` | 1, 3, 4 |
| RunBacktest | `RunBacktest(symbol: str, interval: str) → dict` | 4 |
| ImportKlines | `ImportKlines(symbol: str, interval: str, path: str) → dict` | 6 |

### 服务方法

| 服务 | 方法 | 签名 |
|------|------|------|
| DataEngine | append_bars | `(symbol, interval, bars) → None` |
| DataEngine | get_klines | `(symbol, interval, start_ts=None) → list[dict]` |
| DataEngine | set_klines | `(symbol, interval, klines) → None` |
| AnalysisEngine | on_bar | `(cmd) → dict｜None` |
| AnalysisEngine | _warmup | `(symbol, interval, klines) → None` |
| AnalysisEngine | _process_bar | `(symbol, interval, bar) → dict{signals, breakouts}` |
| FibStrategy | on_signal | `(cmd) → None` |
| Broker | on_submit_order | `(order, bar=None) → FillResult｜None` |
| Broker | get_position | `(symbol) → Position` |
| Broker | get_all_positions | `() → dict{symbol: Position}` |
| Broker | get_account | `() → Account` |
| SimExchange | submit_order | `(order, bar=None) → FillResult｜None` |
| SimExchange | _fill_market | `(order, bar) → FillResult` |
| SimExchange | check_pending | `(bar) → list[FillResult]` |
| SimExchange | get_balance | `() → Account` |

---

## 差异设计描述

以设计文档为准，后续代码迭代时统一处理：

- [x] analysis/app.py：on_bar 末尾增加 `protocol.set("signals:{s}:{i}", signals)` 持久化信号
- [x] strategy/app.py：on_signal 中增加 `protocol.set("decisions:{s}", Decision)` 持久化决策
- [x] execution/broker.py：_process_fill 中增加 `protocol.set("__fills", fills)` 持久化成交
- [x] execution/broker.py：`_sync_emit`（同步广播）已在代码中实现
- [ ] engine/command.py：图 4 以代码现状为准——一次触发批量方式（清除 _services + checkpoint → 构造 PushBars 触发一次 on_bar → on_bar 内部全量处理）。**图 4 顺序图已按代码现状更新。**
- [x] engine/command.py：RunBacktest 返回值改为 `{signals, decisions, fills, account, positions, ...}`
- [x] execution/broker.py：增加 `process_pending(bar)` 方法，委托 SimExchange.check_pending
- [x] execution/broker.py：SubmitOrder 中增加 `_persist_order(order)` 持久化订单
- [x] execution/broker.py：mark_filled 后更新 `_persist_order(order)` 状态
