# A2c：执行层实现详述

> A2 接口契约的补充文档，展开执行层的架构设计、撮合规则、事件广播机制和注意事项。

---

## 架构概览

```
策略层 → SubmitOrder → Broker.on_submit_order
                          │
               ┌──────────┴──────────┐
               │   protocol 链        │
               │                      │
               │  SimExchangeProtocol  │ ← 撮合 + 账户管理
               │    └── CacheLayer    │ ← 内存缓存
               │          └── SQLite  │ ← 持久化
               └─────────────────────┘
```

ExchangeProtocol 的双重角色：
- **对 Broker**：暴露撮合接口（`submit_order / check_pending / get_balance`）
- **对框架**：暴露 KV 接口（`get / set / remove`），委托内层 CacheLayer 做持久化

---

## Broker 下单流程（A2 已定义）

### on_submit_order 实现要点

A2 定义了 5 步流程，补充实现细节：

```
① protocol.get_balance() → Account{total, free}
② cost = price × quantity
   市价：price = bar.close
   限价：price = order.price
③ 余额检查（仅 buy 方向）：
   if side == "buy" && account.free < cost:
     _sync_emit(OrderRejected{reason="余额不足"}) → return None
④ protocol.submit_order(order, bar) → FillResult | None
   market → 立即成交返回 FillResult
   limit/stop → 入挂单队列返回 None
⑤ if fill: _process_fill(fill) → 更新持仓 + 持久化 + 广播
```

**注意**：sell 方向不检查余额（卖出已有持仓），只检查 buy 方向。

### _process_fill 实现要点

A2 定义了 4 步流程，补充细节：

```
① position = positions.get(fill.symbol, Position.empty(fill.symbol))
   position.apply_fill(fill) → rpnl（已实现盈亏）
② protocol.set("__positions", positions) → 持久化所有持仓
③ protocol.append("__fills", fill) → 追加成交记录
④ _sync_emit(OrderFilled{order_id, symbol, side, filled_price, filled_quantity, commission, realized_pnl=rpnl, ts})
```

**持久化顺序**：先写 positions 和 fills（②③），再广播事件（④），确保数据先落盘。

### 事件广播机制 — _sync_emit

```python
def _sync_emit(self, event):
    for subscriber in self.app.hub.exchange.match(event.destination):
        self.app.hub.execute(subscriber, event)
```

使用 `exchange.match + hub.execute` 同步广播，而非 `asyncio.create_task`。
保证回测模式下所有下游 subscriber 在当前 bar 内完成处理。

### 挂单检查 — process_pending

由回测逐 bar 循环在 **每根 bar 开头** 调用（先处理挂单，再处理新信号）：

```
① fills = protocol.check_pending(bar) → list[FillResult]
② for fill in fills: _process_fill(fill)
③ return fills
```

---

## SimExchangeProtocol 撮合规则（A2 已定义）

### _fill_market 实现（A2 已定义 6 步）

补充成交价计算细节：

| 方向 | 成交价计算 | pnl |
|------|-----------|-----|
| buy | `bar.close × (1 + slippage_pct)` | `-fill_price × quantity` |
| sell | `bar.close × (1 - slippage_pct)` | `+fill_price × quantity` |

滑点方向：买入加价（不利），卖出降价（不利），模拟真实市场的滑点损耗。

### check_pending 触发条件（A2 已定义）

| 类型 | side | 触发条件 | 成交价 |
|------|------|---------|--------|
| limit | buy | bar.low ≤ order.price | order.price（限价） |
| limit | sell | bar.high ≥ order.price | order.price（限价） |
| stop | buy | bar.high ≥ order.stop_price | bar.close（市价） |
| stop | sell | bar.low ≤ order.stop_price | bar.close（市价） |

**挂单管理**：触发后从 `_pending_orders` 列表移除，调用 `_fill_market` 走完整成交流程。

### 资金管理 — Account.settle

```
account.settle(pnl, commission):
  total += pnl - commission
```

- 买入：pnl = -cost（扣钱），total 减少
- 卖出：pnl = +cost（回款），total 增加
- commission 始终为正值，从 total 额外扣除

---

## 缓存 key 规范

| key 格式 | 内容 | 读写方 |
|---------|------|--------|
| `__positions` | 全部持仓 `dict{symbol: Position}` | `_process_fill` 写入 / `get_position`/`get_all_positions` 读取 |
| `__fills` | 成交记录列表 `list[FillResult]` | `_process_fill` 追加 / 外部查询读取 |

`__` 前缀表示 Broker 私有 key，非分析/策略层的 `{symbol}:{interval}` 命名空间。

---

## TOML 配置

```toml
["timing.execution.broker.Broker".protocol]
module = "timing.execution.adapters.sim.SimExchangeProtocol"
initial_balance = 100000
slippage_pct = 0.001
commission_rate = 0.001
```

| 配置 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| initial_balance | float | 100000 | 初始资金 |
| slippage_pct | float | 0.001 | 滑点百分比（0.1%） |
| commission_rate | float | 0.001 | 手续费率（0.1%） |

---

## protocol 链

```
SimExchangeProtocol（撮合 + 账户）
  └── CacheLayer（内存缓存）
        └── SQLiteProtocol（磁盘持久化）
              路径：{path}/execution/broker.sqlite
```

SimExchangeProtocol 同时承担撮合接口和 KV protocol 的双重角色。

---

## 文件清单

| 文件 | 内容 |
|------|------|
| execution/broker.py | Broker 服务（下单 + 持仓管理 + 事件广播） |
| execution/models.py | SubmitOrder 命令 |
| execution/adapters/base.py | ExchangeProtocol 抽象基类 |
| execution/adapters/sim.py | SimExchangeProtocol 模拟撮合 |
| models/order.py | Order / FillResult / OrderFilled / OrderRejected |
| models/position.py | Position |
| models/account.py | Account |

---

## 备注：A2 未定义项（代码实现时移除）

以下内容在旧设计文档中出现，但 A2 接口契约中无对应定义（无故事来源或无顺序图箭头）。
保留仅作参考，**在执行代码实现的时候移除**。

| 项目 | 旧文档位置 | 说明 |
|------|-----------|------|
| `cancel_order` 方法 | 旧 05 架构说明 + ExchangeProtocol | 旧设计中 ExchangeProtocol/SimExchangeProtocol 定义了 `cancel_order(order_id) → bool`，A2 中已移除（无取消订单的故事） |
| `CancelOrder` 命令 | 旧 05 文件清单 execution/models.py | 旧设计中 models.py 包含 `CancelOrder` 命令定义，A2 中不存在 |
| `LiveExchangeProtocol` | 旧 05 TODO + 文件清单 | 旧设计中 `execution/adapters/live.py` 为占位实现，A2 中无对应故事（真实交易所对接），不纳入当前实现 |
