# Timing 系统总览

## 一句话描述

基于 bollydog 微服务框架的**量化交易信号回测系统**，三层流水线：数据 → 分析（含策略） → 执行。

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
│  ┌───────────┐      ┌──────────────────────────────┐      ┌──────────┐ │
│  │  数据层    │─bar─▶│         分析层                │─order▶│  执行层  │ │
│  │ DataEngine │      │                              │      │  Broker  │ │
│  │            │      │  ┌────────────────────────┐  │      │          │ │
│  │ DuckDB    │      │  │ RetracementService     │  │      │ SimExch  │ │
│  │ (K线存储)  │      │  │  ├─ 回撤算法 (algo)    │  │      │ (撮合)   │ │
│  │            │      │  │  ├─ FibStrategy (策略)  │  │      │          │ │
│  │            │      │  │  └─ SQLite (状态持久化) │  │      │ SQLite   │ │
│  │            │      │  └────────────────────────┘  │      │ (持仓)   │ │
│  └───────────┘      └──────────────────────────────┘      └──────────┘ │
│                                                                         │
│  * 每个分析子服务 = 算法 + 策略 + 独立 SQLite                              │
│  * 对外只暴露 AnalysisEngine，不暴露内部策略细节                            │
└─────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                          引擎层（两种运行模式）                            │
│  生产模式：bollydog service  │  回测模式：BacktestApp execute             │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 核心设计原则

1. **策略内嵌分析层**：每个 AnalysisEngine 子服务封装"算法 + 策略 + 持久化"三件事，对外只通过 AnalysisEngine 统一表达，不暴露具体策略实现
2. **每个子服务独立 SQLite**：checkpoint、缓存、触碰记录各自隔离，互不干扰
3. **两种模式一套代码**：生产 service 模式和回测 execute 模式共用同一套分析/执行服务

---

## 核心调用链路

### 生产模式（service）

```
bollydog service --config config.toml
  → Hub 启动 → 依次启动 DataEngine / AnalysisEngine子服务 / Broker
  → 外部推 bar → PushBars 写入 DataEngine
  → _publish 广播 → Exchange 路由到各 AnalysisEngine.on_bar
  → 分析产出信号 → 内部策略决策 → SubmitOrder → Broker 撮合
```

### 回测模式（execute）

```
python main.py execute RunBacktest --config config.toml --symbol X --interval Y
  → BacktestApp 启动 → 读 backtest.toml 动态创建分析实例
  → 清除 _services 隔离生产配置
  → 清除 checkpoint 保证全量重跑
  → GetKlines 拉取全部历史数据
  → exchange.match 找到所有 on_bar handler
  → asyncio.gather 并行执行
    → on_bar 内部同步走完：分析 → 策略 → 下单 → 撮合
  → 汇总结果返回
```

---

## 模块依赖关系

```
config.toml depends 字段定义的服务树：

生产模式 TimingApp
  ├── DataEngine
  ├── RetracementService ──depends──▶ DataEngine
  │     └─ 内嵌 FibStrategy 逻辑
  └── Broker

回测模式 BacktestApp
  ├── DataEngine
  ├── Broker
  └── (动态) RetracementService_0, _1, ...
        └─ 从 backtest.toml 创建，各有独立参数 + 独立 SQLite
```

**注意：** FibStrategy 在 config.toml 中仍然作为独立 AppService 注册（框架要求），
但在架构视角上它是分析层的内部子策略，外部不感知它的存在。

---

## 三层职责

| 层 | 服务 | 职责 | 持久化 |
|----|------|------|--------|
| 数据层 | DataEngine | K 线存储 + 读写 + 广播 | DuckDB |
| 分析层 | AnalysisEngine 子服务 | 算法计算 + 策略决策 + 信号广播 | 各自独立 SQLite |
| 执行层 | Broker | 撮合下单 + 持仓管理 + 账户管理 | SimExchange + SQLite |

---

## 文件目录

```
timing/
├── main.py                  # CLI 入口
├── config.toml              # 生产配置
├── backtest.toml            # 回测配置
├── common/clock.py          # 时钟抽象
├── models/                  # 共享数据模型（ER 图见 06-data-models.md）
│   ├── kline.py             # K 线
│   ├── signal.py            # 信号
│   ├── order.py             # 订单（含状态: filled/rejected）
│   ├── position.py          # 持仓（事实表）
│   └── account.py           # 账户（事实表）
├── data/                    # 数据层
├── analysis/                # 分析层（含策略）
├── strategy/                # 策略服务（FibStrategy — 分析层的内部子策略）
├── execution/               # 执行层
├── engine/                  # 引擎入口（TimingApp / BacktestApp）
└── risk/                    # 风控（预留）
```
