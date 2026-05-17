# Timing 系统总览

## 一句话描述

基于 bollydog 微服务框架的**量化交易信号回测系统**，四层流水线：数据 → 分析 → 策略 → 执行。

---

## 系统架构图

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          bollydog 框架层                                 │
│  Hub(消息中枢) ── Exchange(发布订阅) ── Queue(命令队列) ── Session        │
└─────────────────────────────────────────────────────────────────────────┘
         │ dispatch / execute / emit
         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                          timing 业务层                                   │
│                                                                         │
│  ┌──────────┐     ┌─────────────────┐     ┌──────────┐     ┌────────┐ │
│  │  数据层   │─bar─▶│     分析层      │─sig─▶│  策略层   │─ord─▶│ 执行层 │ │
│  │DataEngine│     │AnalysisEngine   │     │FibStrategy│     │ Broker │ │
│  │          │     │                 │     │           │     │        │ │
│  │ DuckDB   │     │ Retracement     │     │ 信号过滤   │     │SimExch │ │
│  │(K线存储)  │     │  ├─ algo 算法   │     │ 仓位计算   │     │(撮合)  │ │
│  │          │     │  └─ touch 检测  │     │ 下单决策   │     │        │ │
│  │          │     │ SQLite(状态)    │     │ SQLite    │     │ SQLite │ │
│  └──────────┘     └─────────────────┘     │(决策记录) │     │(持仓)  │ │
│                                           └──────────┘     └────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                          引擎层（两种运行模式）                            │
│  生产模式：bollydog service  │  回测模式：BacktestApp execute             │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 四层职责与边界

| 层 | 服务 | 输入 | 输出 | 持久化 |
|----|------|------|------|--------|
| 数据层 | DataEngine | 外部推送 / 文件导入 | PushBars 广播 | DuckDB |
| 分析层 | AnalysisEngine 子服务 | K 线数据 | Signal（交易信号） | 各自独立 SQLite |
| 策略层 | FibStrategy 等 | Signal（交易信号） | SubmitOrder（下单指令） | 各自独立 SQLite |
| 执行层 | Broker | SubmitOrder | OrderFilled / OrderRejected | SQLite（持仓 + 成交） |

### 关键边界

```
分析层 ──Signal(frozen)──▶ 策略层 ──SubmitOrder──▶ 执行层
         ↑                        ↑
      唯一输出                   唯一输入
```

- **分析层只产出 Signal**：包含算法计算、指标生成、信号检测，但不做任何交易决策，不知道 Broker 的存在
- **策略层只消费 Signal**：通过 subscriber 订阅 `SignalEmitted` 事件，完全独立处理信号过滤、仓位计算、下单决策，不与分析层的算法产生任何交互
- **Signal 是解耦契约**：frozen 数据快照，分析层写入、策略层读取，两层之间零 import

---

## 核心设计原则

1. **四层单向流水线**：数据 → 分析 → 策略 → 执行，每层只依赖上游输出，不反向引用
2. **Signal 驱动解耦**：分析与策略通过 `SignalEmitted` 事件连接，策略可独立替换、独立回测
3. **每个服务独立 SQLite**：checkpoint、缓存、状态、决策记录各自隔离
4. **路径隔离区分环境**：生产和回测共用一套代码，仅通过指定不同的数据库路径区分运行环境

### 持久化路径约定

```
生产模式：cache/
  ├── data.duckdb                    ← DataEngine
  ├── analysis/
  │     ├── config.toml              ← AnalysisEngine TOML 配置
  │     └── retracement.sqlite       ← RetracementService
  ├── strategy/
  │     └── fib_strategy.sqlite      ← FibStrategy
  └── execution/
        └── broker.sqlite            ← Broker

回测模式：cache/backtest_{symbol}_{interval}_{timestamp}/
  ├── data.duckdb                    ← 指向原始数据（只读）
  ├── analysis/...                   ← 回测独立副本
  ├── strategy/...                   ← 回测独立副本
  └── execution/...                  ← 回测独立副本
```

每次回测通过 `restart()` 重置所有服务状态，数据库路径隔离保证运行间互不干扰。

---

## 核心调用链路

### 生产模式（service）

```
bollydog service --config config.toml
  → Hub 启动 → 依次启动 DataEngine / AnalysisEngine子服务 / FibStrategy / Broker
  → 外部推 bar → PushBars 写入 DataEngine
  → _publish 广播 → Exchange 路由到 RetracementService.on_bar
  → 分析产出 Signal → SignalEmitted 广播 → FibStrategy.on_signal
  → 策略决策 → SubmitOrder → Broker 撮合
```

### 回测模式（execute）

```
python main.py execute RunBacktest --config config.toml --symbol X --interval Y
  → BacktestApp 启动（DataEngine + AnalysisEngine + FibStrategy + Broker）
  → analysis.restart() 清理状态
  → apply_config → warmup
  → 逐 bar 回放：hub.execute(PushBars replay=True)
    → exchange.match + hub.execute 同步触发 subscriber 链
    → on_bar → Signal → on_signal → SubmitOrder → Broker
  → 汇总结果返回
```

---

## 模块依赖关系

```
TimingApp / BacktestApp
  ├── DataEngine                      ← 无依赖
  ├── AnalysisEngine                  ← depends DataEngine
  │     └── RetracementService
  ├── FibStrategy                     ← subscribes SignalEmitted（独立服务）
  └── Broker                          ← 无依赖（接收 SubmitOrder）
```

信号传递全部通过 Exchange pub/sub，服务间无直接代码引用：
- `DataEngine.PushBars` → `RetracementService.on_bar`（subscriber）
- `AnalysisEngine.SignalEmitted` → `FibStrategy.on_signal`（subscriber）
- `FibStrategy` → `SubmitOrder` → `Broker`（command destination）

---

## 文件目录

```
timing/
├── main.py                  # CLI 入口
├── config.toml              # 生产配置
├── common/clock.py          # 时钟抽象（LiveClock / SimulatedClock）
├── models/                  # 共享数据模型（见 01-data-models.md）
│   ├── kline.py             # K 线
│   ├── signal.py            # 信号 + SignalEmitted 事件
│   ├── order.py             # 订单
│   ├── position.py          # 持仓
│   └── account.py           # 账户
├── data/                    # 数据层（见 02-data-layer.md）
├── analysis/                # 分析层（见 03-analysis-layer.md）
├── strategy/                # 策略层（见 04-strategy-layer.md）
├── execution/               # 执行层（见 05-execution-layer.md）
└── engine/                  # 引擎入口（见 06-engine-layer.md）
```
