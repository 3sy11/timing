# 附录 A1：精确顺序图

从 A0 场景故事出发，每条箭头是一次精确的方法调用：`Class.method(params) → ReturnType`。

## 场景覆盖矩阵

| 顺序图 | 覆盖场景 | 核心路径 |
|--------|---------|---------|
| 图 1 | A1 | 推 bar → 触碰信号 → 市价成交（主干） |
| 图 2 | A2, A3, A4 | 分析层三条分支（无信号 / 突破重算 / 信号弱跳过） |
| 图 3 | A5, A7 | 执行层两条分支（余额拒绝 / 限价挂单触发） |
| 图 4 | B1 | 回测完整流程 |
| 图 5 | A6, A9, B2, B3 | 查询中间记录 / 查询持仓账户 / 回测结果读取 |
| 图 6 | A10 | 数据文件导入到 DataEngine 数据库 |

---

## 图 1：A1 主流程 — 推 bar → 触碰信号 → 市价成交

覆盖全部四层，所有内部调用均展开为精确箭头。

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
    RS->>RS: protocol.set("signals:{s}:{i}", signals)

    loop 每个 signal
        RS->>EX: exchange.match("analysis.AnalysisEngine.SignalEmitted")
        EX->>FS: on_signal(cmd)

        Note over Ext,SE: ══ 策略层 ══

        FS->>FS: cmd.get_event() → event_data{symbol, direction, strength, price}
        FS->>FS: protocol.append("decisions:{s}:{i}", Decision{ts, signal_ts, action=submit, order_id, quantity})
        FS->>BK: hub.execute(SubmitOrder(symbol, side=buy, order_type=market, quantity, bar))

        Note over Ext,SE: ══ 执行层 ══

        BK->>BK: SubmitOrder.__call__() → Order(order_id, symbol, side, order_type, quantity, price)
        BK->>SE: get_balance() → Account{total}
        BK->>SE: submit_order(order, bar)
        SE->>SE: _fill_market(order, bar) → FillResult
        SE->>SE: account.settle(pnl=-cost, commission)
        SE->>SE: order.mark_filled(fill_price, quantity, commission, ts)
        SE-->>BK: FillResult{order_id, symbol, side, filled_price, filled_quantity, commission, ts}
        BK->>BK: _process_fill(fill)
        BK->>BK: position.apply_fill(fill) → rpnl
        BK->>BK: protocol.set("__positions", positions)
        BK->>BK: protocol.append("__fills", fill)
        BK->>EX: _sync_emit(OrderFilled{order_id, symbol, side, filled_price, filled_quantity, commission, rpnl, ts})
    end
```

### 图 1 方法调用清单

| # | 调用方 | 被调用方 | 方法签名 |
|---|--------|---------|---------|
| 1 | 外部 | DataEngine | `PushBars.__call__() → dict{symbol, interval, bars}` |
| 2 | PushBars | DataEngine | `append_bars(symbol, interval, bars) → None` |
| 3 | 框架 | Exchange | `_publish(topic, result)` |
| 4 | Exchange | RetracementService | `on_bar(cmd) → dict｜None` |
| 5 | RetracementService | protocol | `get("__ckpt:{s}:{i}") → int｜None` |
| 6 | RetracementService | DataEngine | `hub.execute(GetKlines(symbol, interval, start_ts)) → list[dict]` |
| 7 | RetracementService | self | `_process_bar(symbol, interval, bar) → dict{signals, breakouts}` |
| 8 | RetracementService | protocol | `set("__ckpt:{s}:{i}", ts) → None` |
| 9 | RetracementService | protocol | `set("signals:{s}:{i}", list[Signal]) → None` |
| 10 | Exchange | FibStrategy | `on_signal(cmd) → None` |
| 11 | FibStrategy | protocol | `append("decisions:{s}:{i}", StrategyDecision) → None` |
| 12 | FibStrategy | Broker | `hub.execute(SubmitOrder(symbol, side, order_type, quantity, bar)) → FillResult｜None` |
| 13 | SubmitOrder | Broker | `on_submit_order(Order, bar) → FillResult｜None` |
| 14 | Broker | SimExchange | `get_balance() → Account` |
| 15 | Broker | SimExchange | `submit_order(Order, bar) → FillResult｜None` |
| 16 | SimExchange | self | `_fill_market(Order, bar) → FillResult` |
| 17 | SimExchange | Account | `settle(pnl, commission) → None` |
| 18 | SimExchange | Order | `mark_filled(price, qty, comm, ts) → None` |
| 19 | Broker | self | `_process_fill(FillResult) → FillResult` |
| 20 | Broker | Position | `apply_fill(FillResult) → float(rpnl)` |
| 21 | Broker | protocol | `set("__positions", dict) → None` |
| 22 | Broker | protocol | `append("__fills", FillResult) → None` |
| 23 | Broker | Exchange | `_sync_emit(OrderFilled) → None` |

---

## 图 2：A2/A3/A4 分析层三条分支

三条分支都从 `_process_bar` 的返回值开始分岔，前置流程（推 bar → on_bar → GetKlines → 循环）与图 1 相同。

```mermaid
sequenceDiagram
    participant RS as RetracementService
    participant DE as DataEngine
    participant EX as Exchange
    participant FS as FibStrategy

    Note over RS,FS: _process_bar(symbol, interval, bar) 返回值决定分支

    alt A2: signals=[], breakouts=[] — 无触碰无突破
        RS->>RS: protocol.set("__ckpt:{s}:{i}", bar_ts)
        Note over RS: 无 signal → 不广播 → 链路结束

    else A3: signals=[], breakouts=[group] — 突破边界
        RS->>DE: hub.execute(GetKlines(symbol, interval)) → 全量
        DE-->>RS: list[dict]
        RS->>RS: compute_retracement(klines) → new_result
        RS->>RS: protocol.set("retracement:{s}:{i}", new_result)
        RS->>RS: protocol.set("__ckpt:{s}:{i}", bar_ts)
        Note over RS: 指标结构已更新，无 signal → 链路结束

    else A4: signals=[sig{strength=0.3}] — 信号弱
        RS->>RS: protocol.set("signals:{s}:{i}", signals)
        RS->>EX: exchange.match(SignalEmitted.destination)
        EX->>FS: on_signal(cmd)
        FS->>FS: cmd.get_event() → {direction=long, strength=0.3}
        FS->>FS: strength(0.3) < min_strength(0.6) → skip
        FS->>FS: protocol.append("decisions:{s}:{i}", Decision{action=skip, reason=weak})
        Note over FS: 不产出 SubmitOrder → 链路结束
    end
```

### 图 2 新增方法（图 1 未出现的）

| # | 调用方 | 被调用方 | 方法签名 | 场景 |
|---|--------|---------|---------|------|
| 24 | RetracementService | algo | `compute_retracement(klines) → RetraceResult` | A3 |
| 25 | RetracementService | protocol | `set("retracement:{s}:{i}", RetraceResult) → None` | A3 |

---

## 图 3：A5 余额拒绝 + A7 限价挂单触发

### 3a: A5 — 余额不足拒绝

从 Broker.on_submit_order 开始分岔（前置流程同图 1 直到策略产出 SubmitOrder）。

```mermaid
sequenceDiagram
    participant FS as FibStrategy
    participant BK as Broker
    participant SE as SimExchangeProtocol
    participant EX as Exchange

    FS->>BK: hub.execute(SubmitOrder(symbol, side=buy, quantity=100, bar={close:100}))
    BK->>BK: SubmitOrder.__call__() → Order(quantity=100)
    BK->>SE: get_balance() → Account{total=5000}
    BK->>BK: cost = 100 × 100 = 10000, free(5000) < cost(10000)
    BK->>EX: _sync_emit(OrderRejected{order_id, symbol, reason="余额不足"})
    BK-->>FS: None
```

### 3b: A7 — 限价挂单 → 后续 bar 触发

```mermaid
sequenceDiagram
    participant FS as FibStrategy
    participant BK as Broker
    participant SE as SimExchangeProtocol
    participant EX as Exchange

    Note over FS,EX: ══ Bar N: 提交限价单 ══

    FS->>BK: hub.execute(SubmitOrder(symbol, side=buy, order_type=limit, price=1.200, quantity=10, bar))
    BK->>BK: SubmitOrder.__call__() → Order(order_type=limit, price=1.200)
    BK->>SE: get_balance() → Account{total=100000}
    BK->>SE: submit_order(Order{type=limit}, bar)
    SE->>SE: _pending_orders.append(order)
    SE->>SE: order.status = "submitted"
    SE-->>BK: None
    BK-->>FS: None

    Note over FS,EX: ══ Bar N+1: low=1.250, 未触发 ══

    BK->>SE: check_pending(bar{low=1.250})
    SE->>SE: limit buy: low(1.250) ≤ price(1.200)? 否
    SE-->>BK: []

    Note over FS,EX: ══ Bar N+2: low=1.180, 触发 ══

    BK->>SE: check_pending(bar{low=1.180})
    SE->>SE: limit buy: low(1.180) ≤ price(1.200)? ✓
    SE->>SE: _fill_market(order, bar) → FillResult
    SE->>SE: account.settle(pnl, commission)
    SE->>SE: order.mark_filled(fill_price, quantity, commission, ts)
    SE-->>BK: [FillResult]

    BK->>BK: _process_fill(fill)
    BK->>BK: position.apply_fill(fill) → rpnl
    BK->>BK: protocol.set("__positions", positions)
    BK->>BK: protocol.append("__fills", fill)
    BK->>EX: _sync_emit(OrderFilled{...})
```

### 图 3 新增方法

| # | 调用方 | 被调用方 | 方法签名 | 场景 |
|---|--------|---------|---------|------|
| 26 | Broker | Exchange | `_sync_emit(OrderRejected{order_id, symbol, reason}) → None` | A5 |
| 27 | Broker | SimExchange | `check_pending(bar) → list[FillResult]` | A7 |
| 28 | Broker | self | `process_pending(bar) → list[FillResult]` | A7 |

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
    participant SE as SimExchangeProtocol

    CLI->>RB: hub.execute(RunBacktest(symbol, interval))

    Note over CLI,SE: ══ 准备阶段 ══

    RB->>DE: hub.execute(GetKlines(symbol, interval)) → 全量
    DE-->>RB: klines (500根)
    RB->>RS: protocol.remove("__ckpt:{s}:{i}")
    RB->>RS: restart() → on_stop → service_reset → on_start

    Note over CLI,SE: ══ warmup 阶段 ══

    RB->>RS: _warmup(symbol, interval, klines[:200])
    RS->>RS: compute_retracement(klines[:200]) → result
    RS->>RS: protocol.set("retracement:{s}:{i}", result)

    Note over CLI,SE: ══ 逐 bar 回放 ══

    loop bar in klines[200:]
        RB->>RB: clock.set_time_ms(bar.ts)

        RB->>BK: process_pending(bar)
        BK->>SE: check_pending(bar) → list[FillResult]
        opt 有挂单触发
            BK->>BK: _process_fill(fill)
        end

        RB->>RS: hub.execute(PushBars(symbol, interval, bars=[bar], replay=true))
        Note over RS: on_bar → _process_bar → signals
        RS->>RS: protocol.set("signals:{s}:{i}", signals)

        opt 有 signal
            RS->>FS: on_signal(cmd)
            FS->>FS: protocol.append("decisions:{s}:{i}", decision)
            opt action == submit
                FS->>BK: hub.execute(SubmitOrder(...))
                BK->>SE: submit_order(order, bar) → FillResult | None
                opt 市价成交
                    BK->>BK: _process_fill(fill)
                    BK->>BK: protocol.set("__positions", ...)
                    BK->>BK: protocol.append("__fills", fill)
                end
            end
        end
    end

    Note over CLI,SE: ══ 结果汇总 ══

    RB->>RS: protocol.get("signals:{s}:{i}") → list[Signal]
    RB->>FS: protocol.get("decisions:{s}:{i}") → list[Decision]
    RB->>BK: protocol.get("__fills") → list[FillResult]
    RB->>BK: get_account() → Account
    RB->>BK: get_all_positions() → dict{symbol: Position}
    RB-->>CLI: dict{signals, decisions, fills, account, positions}
```

### 图 4 新增方法

| # | 调用方 | 被调用方 | 方法签名 | 备注 |
|---|--------|---------|---------|------|
| 29 | RunBacktest | DataEngine | `hub.execute(GetKlines(symbol, interval)) → list[dict]` | 全量拉取 |
| 30 | RunBacktest | RetracementService | `protocol.remove("__ckpt:{s}:{i}") → None` | 清除进度 |
| 31 | RunBacktest | RetracementService | `restart() → None` | mode 生命周期重置 |
| 32 | RunBacktest | RetracementService | `_warmup(symbol, interval, klines) → None` | 初始化指标 |
| 33 | RunBacktest | Broker | `process_pending(bar) → list[FillResult]` | 逐 bar 挂单检查 |
| 34 | RunBacktest | Broker | `get_account() → Account` | 汇总 |
| 35 | RunBacktest | Broker | `get_all_positions() → dict{symbol: Position}` | 汇总 |
| 36 | RunBacktest | 各模块 protocol | `get(key) → serialized_data` | 读取中间结果 |

---

## 图 5：A6/A9/B2/B3 查询场景

所有中间结果都存在各模块自有的序列化存储中，通过 protocol 或服务方法读取。

```mermaid
sequenceDiagram
    participant User as 查询方
    participant RS as RetracementService
    participant FS as FibStrategy
    participant BK as Broker

    Note over User,BK: A6/B2: 查看中间记录

    User->>RS: protocol.get("signals:{s}:{i}") → list[Signal]
    User->>FS: protocol.get("decisions:{s}:{i}") → list[Decision]
    User->>BK: protocol.get("__fills") → list[FillResult]

    Note over User,BK: A9/B3: 查看持仓和账户

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

    CLI->>IK: hub.execute(ImportKlines(symbol, interval, file_path))
    IK->>IK: read_file(file_path) → list[dict]
    IK->>DE: set_klines(symbol, interval, klines)
    DE->>DE: DuckDB 全量写入
    IK-->>CLI: dict{symbol, interval, count}
```

### 图 6 方法调用清单

| # | 调用方 | 被调用方 | 方法签名 |
|---|--------|---------|---------|
| 37 | CLI | ImportKlines | `hub.execute(ImportKlines(symbol, interval, file_path)) → dict` |
| 38 | ImportKlines | self | `read_file(file_path) → list[dict]` |
| 39 | ImportKlines | DataEngine | `set_klines(symbol, interval, klines) → None` |

---

## 方法签名汇总（步骤③接口契约雏形）

从全部 5 张顺序图中提取，每个方法只列一次。

### 命令（Command.__call__）

| 命令 | 签名 | 出现图 |
|------|------|--------|
| PushBars | `__call__() → dict{symbol, interval, bars}` | 1, 4 |
| GetKlines | `__call__() → list[dict]` | 1, 2, 4 |
| SubmitOrder | `__call__() → FillResult｜None` | 1, 3, 4 |
| RunBacktest | `__call__() → dict{signals, decisions, fills, account, positions}` | 4 |
| ImportKlines | `__call__() → dict{symbol, interval, count}` | 6 |

### DataEngine

| 方法 | 签名 | 出现图 |
|------|------|--------|
| append_bars | `(symbol: str, interval: str, bars: list[dict]) → None` | 1 |
| get_klines | `(symbol: str, interval: str, start_ts: int=None) → list[dict]` | 1, 2, 4 |
| set_klines | `(symbol: str, interval: str, klines: list[dict]) → None` | 6 |

### RetracementService（AnalysisEngine 基类 + 子类）

| 方法 | 签名 | 出现图 |
|------|------|--------|
| on_bar | `(cmd) → dict｜None` | 1, 2, 4 |
| _warmup | `(symbol: str, interval: str, klines: list[dict]) → None` | 4 |
| _process_bar | `(symbol: str, interval: str, bar: dict) → dict{signals, breakouts}` | 1, 2 |
| compute_retracement | `(klines: list[dict]) → RetraceResult` | 2, 4 |
| restart | `() → None` | 4 |

### FibStrategy

| 方法 | 签名 | 出现图 |
|------|------|--------|
| on_signal | `(cmd) → None` | 1, 2, 4 |

### Broker

| 方法 | 签名 | 出现图 |
|------|------|--------|
| on_submit_order | `(order: Order, bar: dict=None) → FillResult｜None` | 1, 3 |
| _process_fill | `(fill: FillResult) → FillResult` | 1, 3, 4 |
| process_pending | `(bar: dict) → list[FillResult]` | 3, 4 |
| get_position | `(symbol: str) → Position` | 5 |
| get_all_positions | `() → dict{symbol: Position}` | 4, 5 |
| get_account | `() → Account` | 4, 5 |
| _sync_emit | `(event: BaseEvent) → None` | 1, 3 |

### SimExchangeProtocol

| 方法 | 签名 | 出现图 |
|------|------|--------|
| submit_order | `(order: Order, bar: dict=None) → FillResult｜None` | 1, 3 |
| _fill_market | `(order: Order, bar: dict) → FillResult` | 1, 3 |
| check_pending | `(bar: dict) → list[FillResult]` | 3, 4 |
| get_balance | `() → Account` | 1, 3 |

### 数据模型方法

| 类 | 方法 | 签名 | 出现图 |
|----|------|------|--------|
| Account | settle | `(pnl: float, commission: float) → None` | 1, 3 |
| Position | apply_fill | `(fill: FillResult) → float(rpnl)` | 1, 3, 4 |
| Order | mark_filled | `(price: float, qty: float, comm: float, ts: int) → None` | 1, 3 |

### protocol 通用方法

| 方法 | 签名 | 说明 |
|------|------|------|
| get | `(key: str) → Any｜None` | 读取缓存/持久化值 |
| set | `(key: str, value: Any) → None` | 写入缓存/持久化值 |
| append | `(key: str, item: Any) → None` | 追加到列表型 value |
| remove | `(key: str) → None` | 删除 key |
