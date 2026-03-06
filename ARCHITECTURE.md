# Timing 交易系统架构文档

本文档为**整体架构**与**引擎层**的单一事实来源，便于逐项检查与后续实现对齐。设计参考 [NautilusTrader Architecture](https://nautilustrader.io/docs/latest/concepts/architecture/) 与 [Adapters](https://nautilustrader.io/docs/latest/concepts/adapters/)。

**服务模型**：Hub 及以下各层中，**每个引擎**均为独立 `bollydog.AppService`；**每个引擎内部的每个组件**均为独立 `bollydog.AppService`，作为该引擎的**子服务**（通过引擎的 `add_dependency(component)` 或引擎级 `add_service(component)` 挂载，与 Hub 挂载引擎方式一致）。

---

## 1. 整体架构

### 1.1 架构图

```
                    ┌─────────────────────────────────────────────────────────┐
                    │                    Entrypoints                          │
                    │   HTTP API │ WebSocket (行情/指令) │ CLI │ 定时任务      │
                    └─────────────────────────┬───────────────────────────────┘
                                              │
                    ┌─────────────────────────▼───────────────────────────────┐
                    │                      Hub (bollydog)                      │
                    │   Broker(消息队列) │ Router(发布/订阅) │ Session          │
                    └─────────────────────────┬───────────────────────────────┘
                                              │
     ┌────────────────────────────────────────┼────────────────────────────────────────┐
     │                    │                   │                    │                    │
     ▼                    ▼                   ▼                    ▼                    ▼
┌─────────┐        ┌─────────────┐     ┌──────────┐        ┌─────────────┐      ┌──────────┐
│ Data    │        │ Market      │     │ Analysis │        │ Execution   │      │ Risk     │
│ Engine  │───────▶│ Data/Cache  │────▶│ Engine   │        │ Engine      │      │ Engine   │
│(行情接入)│        │(K线/快照)   │     │(指标/回撤)│        │(下单/回报)   │      │(预检)    │
└─────────┘        └─────────────┘     └──────────┘        └─────────────┘      └──────────┘
     │                     │                   │                    │                    │
     └─────────────────────┴───────────────────┴────────────────────┴────────────────────┘
                                              │
                    ┌─────────────────────────▼───────────────────────────────┐
                    │              Adapters (bollydog + timing)                │
                    │   Redis │ RDB │ 行情 DataClient 适配器 │ 券商 ExecutionClient  │
                    └─────────────────────────────────────────────────────────┘
```

### 1.2 分层与子系统/组件清单

以下每一层的**子系统/组件**均需在实现时对应到具体服务或模块；其中引擎及其组件均以 `bollydog.AppService` 形式存在。

#### 1.2.1 Entrypoints（入口层）

| 子系统/组件 | 说明 | 实现形态 |
|-------------|------|----------|
| **HTTP API** | REST 请求入口，将请求转为 Command 交给 Hub | bollydog HttpService（Starlette） |
| **WebSocket** | 行情/指令长连接，推拉结合 | bollydog SocketService |
| **CLI** | 命令行入口、脚本与运维 | bollydog entrypoint.cli |
| **定时任务** | 定时触发的 Command（如定时拉 K 线） | 可选：cron + Command 或内置 Scheduler 服务 |

#### 1.2.2 Hub（bollydog）

| 子系统/组件 | 说明 | 实现形态 |
|-------------|------|----------|
| **Hub** | 总入口 AppService，挂载 Broker、Router、Session 与四个引擎 | bollydog.service.app.Hub |
| **Broker** | 消息队列，有序消费 Command | bollydog.service.broker.Broker（Hub 的 dependency） |
| **Router** | 发布/订阅，分发 Event | bollydog.service.router.Router（Hub 的 dependency） |
| **Session** | 会话/上下文管理 | bollydog.service.session.Session（Hub 的 dependency） |
| **DataEngine** | 行情接入引擎（见 §2.1） | timing 引擎，Hub 通过 add_service 挂载 |
| **Market Data/Cache** | K 线、快照存储引擎（见 §2.2） | timing 引擎，Hub 通过 add_service 挂载 |
| **Analysis Engine** | 指标、斐波那契回撤、触线检测（见 §2.3） | timing 引擎，Hub 通过 add_service 挂载 |
| **ExecutionEngine** | 下单/回报（预留） | timing 引擎，Hub 通过 add_service 挂载 |
| **RiskEngine** | 预检（预留） | timing 引擎，Hub 通过 add_service 挂载 |

#### 1.2.3 Engines（引擎层，四类引擎）

引擎层**仅包含以下四个引擎**，每个引擎为一个独立 `bollydog.AppService`；每个引擎内部的每一项「组件」为一个独立 `bollydog.AppService`，作为该引擎的子服务。

| 引擎 | 职责 | 组件（子服务 AppService） | 当前状态 |
|------|------|---------------------------|----------|
| **DataEngine** | 行情接入 | 见 §2.1 | 设计完成，见 §3 |
| **Market Data/Cache** | K 线、快照存储 | 见 §2.2 | 接口预留 |
| **Analysis Engine** | 指标、斐波那契回撤、触线检测 | 见 §2.3 | **首期只做本引擎** |
| **ExecutionEngine / RiskEngine** | 下单/回报；预检 | 预留 | 预留 |

#### 1.2.4 Adapters（适配器层）

| 子系统/组件 | 说明 | 实现形态 |
|-------------|------|----------|
| **Redis** | 缓存/会话等 | bollydog.adapters.redis |
| **RDB** | 关系型存储 | bollydog.adapters.rdb |
| **行情 DataClient 适配器** | 各数据源 DataClient 实现（List/File/Redis/REST/WS） | timing.engine.data.clients.* |
| **券商 ExecutionClient** | 下单与回报（预留） | 预留 |

### 1.3 数据流 / 指令流 / 事件流

- **数据流**：外部行情 → DataClient 适配器（归一化）→ DataEngine → Market Data/Cache 写入 + MessageBus 发布 → Analysis/策略等订阅消费。
- **指令流**：Command 经 Hub/Broker → RiskEngine 预检 → ExecutionEngine → ExecutionClient 适配器 → 交易所/券商。
- **事件流**：Event 经 Router 发布，订阅者（触线告警、风控、日志等）响应。

---

## 2. 引擎层定义与组件（AppService 模型）

引擎层仅包含以下**四个引擎**。每个引擎 = 一个 `bollydog.AppService`；每个引擎下列出的**组件** = 一个 `bollydog.AppService`，作为该引擎的**子服务**（由引擎通过 `add_dependency` 或引擎级 `add_service` 挂载）。

### 2.1 DataEngine（行情接入）

- **职责**：管理 DataClient、订阅/请求、接收归一化数据并写入 Cache、向 MessageBus 发布行情事件。
- **组件（每个均为 AppService 子服务）**：

| 组件 | 职责 |
|------|------|
| **DataClientRegistry** | 注册/管理多个 DataClient，转发 request/subscribe 到对应 Client |
| **DataClient 实例**（可选按源拆子服务） | 各数据源实现（List/File/Redis/REST/WS），连接、请求历史、订阅实时、归一化输出 |

实现时 DataEngine 本身为一个 AppService；其内部可持有 Registry + 若干 DataClient 实现类实例，若将「每个 DataClient 连接」拆成独立生命周期，则可把每个 DataClient 实现为 AppService 子服务并由 DataEngine add_dependency。

### 2.2 Market Data/Cache（K 线、快照）

- **职责**：存储 K 线序列（按 symbol+interval）、最新价、可选订单簿快照，供 Analysis/策略查询。
- **组件（每个均为 AppService 子服务）**：

| 组件 | 职责 |
|------|------|
| **KlineStore** | 按 symbol+interval 的 K 线写入与按时间范围查询（get_klines、append_bar） |
| **SnapshotStore**（可选） | 最新价、订单簿快照等，可选实现 |

首期可合并为单一 Cache AppService（仅 KlineStore），后续再拆 SnapshotStore 为独立子服务。

### 2.3 Analysis Engine（指标、斐波那契回撤、触线检测）← 首期只做本引擎

- **职责**：指标计算、Swing 识别、斐波那契回撤线、触线检测等。
- **组件（每个均为 AppService 子服务）**：

| 组件 | 职责 |
|------|------|
| **SwingService** | Swing 拐点识别与选笔（find_swing_highs_lows、select_trend_leg） |
| **FibonacciService** | 斐波那契回撤线计算（retracement_from_leg、retracement_from_klines） |
| **TouchDetectorService** | 触线检测与去抖（check_touch、TouchDetector） |

当前 timing/analysis 为无状态函数与类，接入 bollydog 时：Analysis Engine 为一个 AppService，上述三者可封装为三个 AppService 子服务，由 Analysis Engine add_dependency 挂载。

### 2.4 ExecutionEngine / RiskEngine（预留）

- **ExecutionEngine**：订单路由、生命周期、回报处理；组件预留（如 OrderManager、PositionManager 等）。
- **RiskEngine**：预交易风控检查；组件预留（如 PreTradeRisk、LimitChecker 等）。

两者均为独立 AppService，内部组件在后续设计中再列为 AppService 子服务。

---

## 3. 行情接入层（DataEngine + DataClient）设计

本节为 **DataEngine** 及其组件的详细设计，对齐 NautilusTrader 的 DataEngine / DataClient / 数据流。

### 3.1 设计目标

- 统一从多种数据源（内存、文件、Redis、REST、WebSocket 行情网关）接入行情。
- 将**原始/各源格式**归一化为 timing 内部数据类型（Bar/Kline、Quote、Trade 等）。
- 支持**按需请求**（如历史 K 线）与**订阅推送**（如实时 Tick/Bar）。
- 数据经 DataEngine 写入 Cache 并经由 MessageBus 发布，供 Analysis/策略消费。

### 3.2 与 NautilusTrader 的对应关系

| NautilusTrader | Timing 设计 |
|----------------|-------------|
| DataClient（适配器内） | **DataClient** 抽象 + 各数据源实现（List、File、Redis、REST、WS） |
| 归一化为 Nautilus 类型（Bar, TradeTick 等） | 归一化为 timing 类型（**Bar/Kline, Quote, Trade** 等） |
| request_instrument / request_bars / 回调 | **request_klines(symbol, interval, start_ts, end_ts)** 等请求 + 回调/异步返回 |
| subscribe_trade_ticks / subscribe_bars / on_trade_tick, on_bar | **subscribe_bars(symbol, interval)** / **subscribe_ticks(symbol)** + 通过 MessageBus 推送事件 |
| DataEngine 接收并路由数据 | **DataEngine**（AppService）持有 DataClient 列表、管理订阅、收数据 → 写 Cache + 发 Event |

### 3.3 数据流（行情接入）

```
  [ 数据源: 内存/文件/Redis/交易所API ]
              │
              ▼
  ┌─────────────────────────────────────┐
  │  DataClient (适配器实现，可为子服务)   │
  │  - 连接/断开                          │
  │  - 请求历史: request_klines(...)     │
  │  - 订阅实时: subscribe_bars / ticks   │
  │  - 归一化: 原始格式 → timing 数据类型  │
  └─────────────────────────┬───────────┘
                            │ 回调 / 异步推送
                            ▼
  ┌─────────────────────────────────────┐
  │  DataEngine (AppService)             │
  │  - 注册/管理多个 DataClient（子服务）  │
  │  - 转发请求到对应 Client             │
  │  - 接收归一化数据 → 写 Cache          │
  │  - 发布 Bar/Tick 等 Event 到 Router  │
  └─────────────────────────┬───────────┘
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
  Market Data/Cache    MessageBus(Router)    [ 日志/监控 ]
  (K线/快照持久化)      (BarEvent/TickEvent)
        │                   │
        ▼                   ▼
  [ 按需查询 ]        [ Analysis / 策略 订阅 ]
```

### 3.4 组件定义

#### 3.4.1 数据类型（归一化后）

| 类型 | 说明 | 字段（草案） |
|------|------|--------------|
| **Bar / Kline** | 已存在 `timing.data.Kline` (OHLCV + ts) | open, high, low, close, volume, ts_ms |
| **Quote** | 买卖盘快照（可选） | bid, ask, bid_qty, ask_qty, ts_ms |
| **Trade** | 成交/逐笔（可选） | price, qty, side, ts_ms |

首期以 **Bar/Kline** 为主；Quote/Trade 可后续扩展。

#### 3.4.2 DataClient（端口/抽象）

- **连接生命周期**：`connect()` / `disconnect()`（或异步 `aclose()`）。
- **请求历史**：`request_klines(symbol, interval, start_ts, end_ts) -> AsyncIterator[Kline]` 或回调。
- **订阅实时**：`subscribe_bars(symbol, interval)` / `subscribe_ticks(symbol)`，数据通过回调或异步队列交给 DataEngine。
- **归一化**：适配器内部将交易所/文件/Redis 格式转为 `Kline`（及后续 Quote/Trade）。

#### 3.4.3 DataEngine（AppService）

- **注册 DataClient**：`register_client(client: DataClient)`，可按 venue/source 标识；Client 可为 AppService 子服务。
- **请求转发**：`request_klines(source_id, symbol, interval, start_ts, end_ts)` 转发到对应 Client，结果写 Cache 并可选发布事件。
- **订阅管理**：`subscribe_bars(symbol, interval)` 在对应 Client 上建立订阅；收到数据 → 写 Cache + 发布 `BarEvent` 等至 Router。
- **与 Hub 对接**：通过 bollydog Router 发布事件（如 `BarEvent`、`TickEvent`），下游 Analysis/策略通过 Router 订阅。

#### 3.4.4 Market Data / Cache（AppService，见 §2.2）

- **职责**：存储最新 K 线序列（按 symbol+interval）、最新价、可选订单簿快照。
- **接口（草案）**：`get_klines(symbol, interval, start_ts, end_ts)`；`append_bar(symbol, interval, bar)`；可选 `get_last_price(symbol)`。
- **首期**：可与现有 `KlineSource` / 内存列表并存；后续可接 Redis 等。

### 3.5 目录与接口清单（行情接入层）

| 层级 | 路径/模块 | 内容 |
|------|-----------|------|
| **数据类型** | `timing/data/kline.py` | 已有 `Kline`(OHLCV)；可扩展 `Quote`/`Trade` 于 `timing/data/types.py` |
| **DataClient 端口** | `timing/engine/data/client.py` | 抽象类 `DataClient`：connect/disconnect、request_klines、subscribe_bars（及可选 subscribe_ticks） |
| **DataClient 实现** | `timing/engine/data/clients/` | `ListDataClient`（包装现有 ListKlineSource）；后续 `FileDataClient`、`RedisDataClient`、`RestDataClient`、`WsDataClient` |
| **DataEngine** | `timing/engine/data/engine.py` | `DataEngine`（AppService）：register_client、request_klines、subscribe_bars、接收回调写 Cache 并发布 Event |
| **Cache** | `timing/engine/cache/` 或 `timing/data/cache.py` | 内存 Cache（AppService）：get_klines、append_bar；可选与 KlineSource 统一抽象 |
| **事件** | `timing/engine/events.py` 或 bollydog Event | `BarEvent`(symbol, interval, bar)、`TickEvent`(symbol, trade) 等，经 Router 发布 |

### 3.6 检查项（行情接入层）

- [ ] **DataClient 抽象**：定义 connect/disconnect、request_klines、subscribe_bars（及可选 subscribe_ticks）；归一化输出为 timing 数据类型（至少 Kline）。
- [ ] **ListDataClient**：基于现有 KlineSource/ListKlineSource 实现请求历史 K 线，可选支持“模拟推送”；可为 AppService 子服务。
- [ ] **DataEngine（AppService）**：可注册多个 DataClient；request_klines 转发到指定 Client 并落 Cache/发布事件；组件以 AppService 子服务形式挂载。
- [ ] **DataEngine 订阅**：subscribe_bars 在对应 Client 上建立订阅；收到 Bar 后写 Cache 并发布 BarEvent。
- [ ] **Cache（AppService）**：至少支持按 symbol+interval 的 K 线写入与按时间范围查询（get_klines、append_bar）。
- [ ] **事件与 Router**：BarEvent（含 symbol, interval, bar）经 bollydog Router 发布；下游可订阅。
- [ ] **文档与计划**：本架构文档与 task_plan/findings 一致；后续实现不偏离上述数据流与接口。

---

## 4. 文档维护

- **整体架构**：以本文 §1 为准；若调整分层或框图，同步更新 task_plan.md 中的架构图引用或精简版。
- **引擎层**：仅包含四个引擎（DataEngine、Market Data/Cache、Analysis Engine、ExecutionEngine/RiskEngine）；每个引擎及其组件均为 bollydog AppService/子服务，以 §2 与 §1.2.3 为准。
- **各层子系统/组件**：以 §1.2 各表为准，新增或更名时同步更新表格。
- **检查项**：§3.6 为行情接入层检查清单；Execution/Risk 等层设计时补充对应检查项。
