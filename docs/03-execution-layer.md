# 执行层 — Broker + ExchangeProtocol

## 职责

接收分析层的下单命令，通过交易所协议撮合，维护持仓（事实表）和账户（事实表）。

---

## 架构

```
分析层 → SubmitOrder → Broker.on_submit_order
                          │
               ┌──────────┴──────────┐
               │   protocol 链        │
               │                      │
               │  SimExchangeProtocol  │ ← 撮合 + 账户管理
               │    └── CacheLayer    │ ← 内存缓存
               │          └── SQLite  │ ← 持久化（持仓/账户）
               └─────────────────────┘
```

ExchangeProtocol 是双重角色：
- 对 Broker：暴露 `submit_order / cancel_order / get_balance`
- 对框架：暴露 `get / set / remove`（委托内层 CacheLayer 做 KV 持久化）

---

## Broker 下单流程

```
on_submit_order(order, bar):
  ① 查余额 → 不足 → emit OrderRejected（Order.status → rejected）
  ② protocol.submit_order → 交易所撮合 → FillResult
  ③ Position.apply_fill → 更新持仓 + 计算盈亏
  ④ 持久化持仓到 SQLite
  ⑤ emit OrderFilled（Order.status → filled）
```

---

## SimExchangeProtocol 撮合规则

| 类型 | 触发条件 | 成交价 |
|------|---------|--------|
| Market | 立即 | bar.close × (1 ± slippage) |
| Limit Buy | bar.low ≤ price | limit_price |
| Limit Sell | bar.high ≥ price | limit_price |
| Stop Buy | bar.high ≥ stop_price | market 成交 |
| Stop Sell | bar.low ≤ stop_price | market 成交 |

手续费 = fill_price × quantity × commission_rate

---

## TOML 配置

```toml
["timing.execution.broker.Broker".protocol]
module = "timing.execution.adapters.sim.SimExchangeProtocol"
initial_balance = 100000
slippage_pct = 0.001
commission_rate = 0.001
```

---

## 文件清单

| 文件 | 内容 |
|------|------|
| execution/broker.py | Broker 服务 |
| execution/models.py | SubmitOrder / CancelOrder 命令 |
| execution/adapters/base.py | ExchangeProtocol 抽象 |
| execution/adapters/sim.py | SimExchangeProtocol 模拟撮合 |
| execution/adapters/live.py | LiveExchangeProtocol（占位） |
