# Bollydog 框架深度分析 & 交易系统适配方案

## 一、Bollydog 框架当前能力剖析

通过阅读 timing/bollydog 全部源码，当前框架的**核心设计范式**如下：

### 1.1 已有原语


| 原语                  | 类/模块                          | 职责                                                                                                                                                                     |
| ------------------- | ----------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Service 生命周期**    | `BaseService(mode.Service)`   | start/stop/crash/restart + OneForOneSupervisor 监督树；`add_dependency` 将子服务挂到父服务生命周期上                                                                                     |
| **AppService**      | `AppService(BaseService)`     | 在 BaseService 上加：protocol 绑定、autodiscover commands、router_mapping（HTTP 路由声明）、`create_from` 工厂                                                                          |
| **Command（请求-响应）**  | `BaseCommand`                 | Pydantic 模型 + `__call_`_ 抽象；带 `state`(Future/StreamState)、trace、qos、delivery_count、重试；全局注册表 `_registry` 按 module.alias 自动注册                                            |
| **Event（发后即走）**     | `BaseEvent(BaseCommand)`      | qos=0，`__call_`_ 只做 set_result(True)；与 Command 共享数据结构但语义不同                                                                                                             |
| **Broker（有序队列）**    | `Broker`                      | 内存 OrderedDict 队列，put/take/ack/nack/history；消息状态 PENDING→IN_FLIGHT→DONE/FAILED                                                                                         |
| **Router（Pub/Sub）** | `Router`                      | 按 `message.alias` 分发回调 + `*` 通配；`register(name, callback)` / `publish(message)`                                                                                        |
| **Session**         | `Session`                     | trace_id → SessionContext 映射，acquire/release/save；默认 MemoryProtocol                                                                                                    |
| **Hub（编排中枢）**       | `Hub(AppService)`             | 持有 Broker+Router+Session + apps 字典；dispatch（qos=0 走 Broker 异步，否则同步 execute）；execute = resolve_app → session.acquire → _execute → router.publish；支持 async generator（流式） |
| **Protocol（适配器端口）** | `Protocol(BaseService)`       | 抽象 create/delete + connect 上下文管理器；实现有 MemoryProtocol、RedisProtocol、FileProtocol、RDB、Neo4j、Elastic                                                                      |
| **Entrypoints**     | HttpService、SocketService、CLI | HTTP 自动从 router_mapping + Command._registry 生成路由；WS 接收 JSON 解析 command 后 dispatch；CLI 用 fire 提供 service/ls/execute/shell                                               |
| **Bootstrap**       | `Bootstrap(mode.Worker)`      | 进程入口，信号处理，启动 Hub                                                                                                                                                       |
| **Globals（上下文栈）**   | `globals.py`                  | hub/message/protocol/session/app 五个 Proxy + LocalStack，在 execute 中 push/pop                                                                                            |


### 1.2 信息流模型

```
外部请求(HTTP/WS/CLI)
    │
    ▼ 构造 BaseCommand 实例
    │
    ▼ hub.dispatch(message)
    │
    ├─ qos=0 → broker.put → run loop → _process_message → execute → router.publish
    └─ qos=1 → 直接 execute → router.publish
                │
                ├─ message.__call__()   ← 业务逻辑在此
                ├─ broker.ack / nack
                └─ router.publish(message) → 回调所有 alias 与 * 订阅者
```

关键特征：

- **Command 既是消息也是处理器**（`__call__` 就是 handler）。
- **dispatch 是唯一入口**，所有请求都走 Hub 的 dispatch → execute → router.publish。
- **Router 只在 Command 执行完毕后 publish**（后置事件）。没有「先发布再执行」或独立的事件注入。
- **Engine/Service 之间没有直接引用**，所有协调通过 Hub dispatch Command 来完成；Command 在 `__call__` 中 yield 另一个 Command 来做子编排。

---

## 二、交易系统应该如何在此基础上设计

### 2.1 核心设计范式：一切皆 Command/Event + Service 树

bollydog 的范式是**「Command 驱动 + 服务树」**，正确的使用方式是：

1. **引擎 = AppService**：有自己的生命周期（start/stop），可持有状态，可挂子服务。
2. **引擎间通信 = Command/Event**：引擎不直接调用彼此方法；而是通过 Hub dispatch Command/Event，靠 Router 订阅或 yield sub-command 来联动。
3. **组件 = 引擎的 add_dependency 子 AppService**：享受父服务的生命周期管理与监督树。
4. **数据归一化 = Command 的字段**：Command 是 Pydantic BaseModel，用字段携带数据即可。

### 2.2 timing 架构与 bollydog 范式的映射


| timing 架构                  | bollydog 范式                                                                                                           |
| -------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| DataEngine（行情接入）           | AppService，启动时 start DataClient 子服务；对外暴露 Command（如 `PushBars`、`IngestParquetFile`）                                   |
| Market Data/Cache          | AppService，对外暴露 Command（如 `GetKlines`、`AppendBar`）；内部状态为内存字典或 Protocol 后端                                             |
| Analysis Engine            | AppService，对外暴露 Command（如 `ComputeFibRetracement`、`FeedPrice`）；子服务 SwingService/FibonacciService/TouchDetectorService |
| ExecutionEngine/RiskEngine | AppService + Command（预留）                                                                                              |
| 引擎间协调                      | Command yield sub-command。例如 `ComputeFibRetracement.__call__` 里 yield `GetKlines` 从 Cache 取数据                         |
| 行情推送                       | DataEngine 收到新 Bar → dispatch 一个 `BarEvent`(BaseEvent) → Router publish → Analysis 等注册了 `barevent` 回调的服务收到            |
| 触线告警                       | TouchDetectorService 发现触线 → dispatch `FibLevelTouched`(BaseEvent) → Router publish → 外部订阅者收到                          |


### 2.3 当前 timing 架构中需要调整的设计理念


| 现状/设想                                                | 问题                                                                            | 应调整为                                                                                          |
| ---------------------------------------------------- | ----------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| 引擎「直接调用」其他引擎的方法（如 DataEngine 直接写 Cache 的 append_bar） | 违反 bollydog「引擎间通过 Command 通信」的范式；引入了强耦合                                       | DataEngine 收到数据后 dispatch 一个 `AppendBar` Command（目标是 Cache），或 dispatch `BarEvent`（Cache 自行订阅） |
| Analysis 需要「拉 Cache」读历史 K 线                          | 可以这样做，但如果想完全通过 Hub 走：用 yield GetKlines 子命令                                    | 首期**可直接注入 Cache 引用**（同进程 AppService 互访状态是合理的简化），但接口上应预留 Command 方式                            |
| ARCHITECTURE.md 中 DataEngine → Cache 是箭头（直接写入）       | 在 bollydog 里更自然的方式是：DataEngine dispatch BarEvent → Cache 订阅 BarEvent 并 append | 两种都可以；框架不禁止同进程引用，但**事件方式**更松耦合、更容易扩展为跨进程                                                      |


---

## 三、Bollydog 需要增加哪些**通用**特性

以下增强**不是面向交易系统做特化**，而是让 bollydog 作为「事件驱动服务编排框架」更完整，**任何领域（IoT、数据管道、工作流引擎等）都会受益**。

### 3.1 缺失能力与建议


| #                                    | 缺失能力                                                                                                    | 影响                                                                                     | 建议增强（通用，非交易特化）                                                                                                                                                                  | 优先级   |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----- |
| **① Router 主题模式匹配**                  | 当前 Router 只按 `message.alias`（类名小写）做精确匹配 + `*` 通配                                                        | 无法做「订阅某个前缀/模式的事件」，如 `data.*` 匹配所有数据类事件                                                 | 增加**层级主题**（topic hierarchy）：`register("data.bar.*", cb)` 匹配 `data.bar.btcusdt` 等。这是所有 pub/sub 系统的标准能力（MQTT、AMQP、NATS 都有）。**不改现有 alias 逻辑**，加一层 topic 路由即可                       | **高** |
| **② Event 独立注入（非 Command 后置）**       | 当前 Router.publish 只在 `_process_message` 执行完 Command 后调用；没有「主动发一个 Event 到 Router 而不经过 Broker/execute」的通道 | 引擎内部产生的「异步事件」（如收到新 K 线、定时器触发）无法直接发布到 Router；必须包装成一个 Command、dispatch、execute、才 publish | 在 Hub 或 Router 上暴露 `**emit(event: BaseEvent)`**：直接 publish 到 Router，不经过 Broker/execute。这使得任何 Service 都能在自己的 task loop 里产生事件                                                     | **高** |
| **③ 服务间事件订阅的声明式注册**                  | 当前 Router.register 是命令式的，需要在 on_start 里手动调 `hub.router.register(name, callback)`；而且需要拿到 hub 引用          | 引擎想订阅事件时要通过 globals.hub 拿 router，耦合到全局变量                                               | 在 AppService 上加**声明式订阅**：如 `subscribe = {'barevent': 'on_bar'}` 字典或装饰器 `@subscribe('barevent')`；框架在 on_start 时自动 register 到 Router。类似 bollydog 已有的 `router_mapping`（声明 HTTP 路由） | **中** |
| **④ 定时/周期性任务**                       | 当前只有 `mode.Service.task` 可以跑 while loop；没有 cron / interval 的声明式支持                                       | 需要定时拉取、定时检查等场景时，每个 Service 都要自己写 while + asyncio.sleep                                 | 在 BaseService 上增加 `@timer(interval=60)` 或 `@cron('*/5 * * * *')` 装饰器，框架自动管理定时 task 的 start/stop。这是通用的服务编排能力（Faust/mode 本身有 `@crontab`）                                          | **中** |
| **⑤ Service 发现与引用**                  | 当前 Hub.apps 是 dict，key 是 `{domain}.{alias}`；获取另一个 Service 需要 `hub.apps.get('xxx')`                      | 引擎间如果偶尔需要「同进程直接引用」（如 Analysis 拉 Cache），没有类型安全的服务发现                                     | 在 Hub 上增加 `**get_service(cls_or_name) -> T`**，支持按类型或名称查找。这不破坏 Command 通信范式，只是给同进程简化提供便利                                                                                         | **低** |
| **⑥ Command 目标路由（destination）未充分使用** | BaseCommand 有 `destination` 字段，Hub._resolve_app 按它查 app；但当前只用来设置 execute 上下文中的 protocol，并不做「只路由到该 app」  | 想做「这个 Command 只能被 DataEngine 处理」时没有强制路由语义                                              | 完善 destination 语义：当 Command 有 destination 时，Hub 应将 execute 限定在目标 AppService 上（目标 Service 的 execute 或 handler），而非所有 Command 都由 Hub._execute(message.**call**) 执行                 | **中** |
| **⑦ Command handler 分离**             | 当前 Command 自身就是 handler（`__call_`_）；无法做「同一个 Command 类型，由不同 Service 各自处理」                                | 想让 DataEngine 和 AnalysisEngine 分别响应不同 Command 类型，需要分别定义不同 Command 类（即使逻辑相似）            | 可选增强：支持 Service 级 **handler 注册**：`@handle(CommandClass)` 装饰器在 AppService 上注册特定 Command 类型的处理逻辑，Hub 根据 destination 或路由规则分发。这与 Command.**call** 不冲突，只是多一条路径                       | **低** |


### 3.2 不需要改动的（已经够用或不应特化）


| 能力               | 为什么不需要改                                                                |
| ---------------- | ---------------------------------------------------------------------- |
| Broker 队列        | 内存有序队列 + ack/nack + 重试，对单进程编排足够；后续做分布式可换成 Redis/NATS 实现的 Broker，但接口不用改 |
| Session          | 当前的 trace_id → context 映射足够；换 Redis 后端就能跨进程                            |
| Protocol（适配器端口）  | 设计已经很通用（create/delete/connect），Memory/Redis/RDB/Neo4j/Elastic/File 都有  |
| BaseCommand 数据模型 | Pydantic BaseModel + trace + state + qos + retry，足够丰富                  |
| 入口层（HTTP/WS/CLI） | 已经能自动从 Command 生成 HTTP 路由和 WS 端点，很好用                                   |
| 日志/监控            | structlog + trace 已经够用                                                 |


---

## 四、交易系统在增强后的设计范式

假设增加了上述 ①②③ 之后，timing 交易系统的交互模式变为：

```
                      Hub
                       │
            ┌──────────┼──────────┐
            ▼          ▼          ▼
      DataEngine   CacheEngine  AnalysisEngine
         │                           │
         │  [on_start]               │  [on_start]
         │  - 启动 DataClient 子服务  │  - 注册订阅:
         │  - DataClient 连接数据源   │    @subscribe('data.bar.*')
         │                           │    def on_bar(msg): ...
         │                           │
         │  [DataClient 收到新 K 线]  │
         │  - hub.emit(BarEvent)  ───┼──→  Router publish 'data.bar.btcusdt'
         │                           │                │
         │                           │     CacheEngine.on_bar: append_bar
         │                           │     AnalysisEngine.on_bar: 计算 swing/fib/touch
         │                           │                │
         │                           │     如果触线 → hub.emit(FibLevelTouched)
         │                           │                │
         │                           │     外部订阅者 / WS 推送 / 告警
```

关键点：

1. **DataEngine 通过 `hub.emit(BarEvent)` 发布行情**，不直接写 Cache。
2. **CacheEngine 自行订阅 `data.bar.*`** 并 append_bar（Cache 是行情的消费者之一）。
3. **AnalysisEngine 也订阅 `data.bar.*`**，收到后做计算，触线时再 emit Event。
4. 所有引擎**不互相直接调用**，通过 Event 松耦合。
5. 若 Analysis 需要查历史 K 线：yield `GetKlines(...)` 子命令（Cache 的 Command），或直接引用 Cache（同进程简化）。

---

## 五、总结：优先级排序


| 优先级    | 要做的事                                                          | 类别          |
| ------ | ------------------------------------------------------------- | ----------- |
| **P0** | bollydog 增加 `hub.emit(event)` — Event 独立注入，不经过 Broker/execute | 框架增强（通用）    |
| **P0** | bollydog Router 增加 topic 模式匹配（至少支持 `prefix.`*）                | 框架增强（通用）    |
| **P1** | bollydog AppService 增加声明式订阅（`subscribe` 字典或 `@subscribe` 装饰器） | 框架增强（通用）    |
| **P1** | 更新 ARCHITECTURE.md 中引擎间通信方式：从「直接调用」改为「通过 Event/Command 通信」    | timing 架构调整 |
| **P2** | bollydog 增加 `@timer` / `@cron` 装饰器                            | 框架增强（通用）    |
| **P2** | 完善 Command.destination 的路由语义                                  | 框架增强（通用）    |
| **P3** | Hub.get_service 类型安全的服务发现                                     | 框架增强（通用）    |
| **P3** | Service 级 handler 注册（`@handle(CommandClass)`）                 | 框架增强（通用）    |


**核心结论**：bollydog 的 Command/Service/Broker/Router 已经是一套事件驱动服务编排框架的核心，但缺少**「Event 独立注入」和「Router 主题模式匹配」**两个关键通用能力。加上这两点后，交易系统（以及任何其他领域的系统）可以完全按照「引擎间通过事件松耦合通信」的范式来设计，无需任何交易特化改造。

---

---

# 案例二：AI Multi-Agent 系统适配分析

## 六、主流 Agent 架构范式概览

### 6.1 当下主流的 Agent 交互模式


| 模式                          | 代表框架/协议                              | 核心机制                                                                                     |
| --------------------------- | ------------------------------------ | ---------------------------------------------------------------------------------------- |
| **Supervisor-Worker**       | LangGraph、Letta、AutoGen              | 一个 Supervisor Agent 分解任务 → 动态派发给多个 Worker Agent → 收集结果 → 聚合返回。Worker 可并行，Supervisor 掌控全局 |
| **Pipeline（链式）**            | LangChain/LangGraph                  | Agent A 的输出作为 Agent B 的输入，顺序执行；适合线性工作流（如 RAG → 摘要 → 格式化）                                 |
| **Router（动态路由）**            | LangGraph Router、CrewAI              | 根据用户输入/意图动态选择由哪个 Agent 处理（类似策略模式）                                                        |
| **Peer-to-Peer / Delegate** | CrewAI @Delegate、AutoGen GroupChat   | Agent 之间可以互相委托（handoff）；一个 Agent 把当前任务或子任务交给另一个 Agent，对方完成后交回                            |
| **ReAct Loop**              | 所有主流框架的底层                            | Observe → Think → Act（Tool Call）→ Observe → …，单个 Agent 的核心循环                             |
| **Tool Use（MCP）**           | Model Context Protocol               | Agent 通过标准协议调用外部工具/数据源/Prompt；工具暴露 JSON-RPC 接口，Agent 发现并调用                               |
| **Shared Memory**           | Letta ArchivalMemory、LangGraph State | 多 Agent 共享的知识库/状态：Working Memory（当前上下文）、Summary Memory（压缩历史）、Long-term Memory（持久知识）      |


### 6.2 一个 Agent 的典型内部结构

```
┌────────────────────────────────────────────┐
│  Agent                                      │
│  ┌──────────┐  ┌──────────┐  ┌───────────┐ │
│  │ System   │  │ Memory   │  │ Tools     │ │
│  │ Prompt   │  │ (layers) │  │ (MCP/自有) │ │
│  └────┬─────┘  └────┬─────┘  └─────┬─────┘ │
│       └──────────────┼──────────────┘       │
│                      ▼                      │
│              ┌──────────────┐               │
│              │  LLM / Model │               │
│              └──────┬───────┘               │
│                     │                       │
│              ┌──────▼───────┐               │
│              │ ReAct Loop   │               │
│              │ (think→act→  │               │
│              │  observe→…)  │               │
│              └──────┬───────┘               │
│                     │                       │
│              ┌──────▼───────┐               │
│              │ Output /     │               │
│              │ Handoff /    │               │
│              │ Tool Call    │               │
│              └──────────────┘               │
└────────────────────────────────────────────┘
```

### 6.3 多 Agent 交互的典型拓扑

```
                    ┌─────────────┐
                    │ Supervisor  │  (或 Router / Orchestrator)
                    │ Agent       │
                    └──────┬──────┘
                           │ 分发任务 / handoff / delegate
            ┌──────────────┼──────────────┐
            ▼              ▼              ▼
      ┌───────────┐  ┌───────────┐  ┌───────────┐
      │ Worker A  │  │ Worker B  │  │ Worker C  │
      │ (研究)    │  │ (编码)    │  │ (审查)    │
      └─────┬─────┘  └─────┬─────┘  └─────┬─────┘
            │              │              │
            └──────────────┼──────────────┘
                           ▼
                    Shared Memory / State
                    (上下文、中间产物、最终结果)
```

---

## 七、Bollydog 已有原语与 Agent 概念的映射


| Agent 概念                   | bollydog 已有原语                                                | 契合度  | 说明                                                                                                                         |
| -------------------------- | ------------------------------------------------------------ | ---- | -------------------------------------------------------------------------------------------------------------------------- |
| **单个 Agent**               | `AppService`                                                 | ★★★★ | AppService 有生命周期（start/stop）、可持有状态（Memory）、可挂子服务（Tools），可以是一个 Agent 的载体                                                    |
| **Agent 的 ReAct Loop**     | `BaseCommand.__call__` + async generator (yield sub-command) | ★★★☆ | Command 的 `__call__` 可以是一次 LLM 调用；async generator + yield sub-command 可以实现「think → act(tool call) → observe → think → …」循环 |
| **Tool Call**              | `BaseCommand`（子命令）+ `Protocol`（适配器）                          | ★★★☆ | Agent 的 Tool 可以建模为 Command（yield 一个 ToolCallCommand → Hub execute → 返回结果）；Protocol 可以封装 MCP Server 连接                      |
| **Supervisor → Worker 分发** | Hub.dispatch + Command yield                                 | ★★★☆ | Supervisor Agent 的 `__call__` 里 yield 多个 WorkerCommand，Hub 分别 dispatch 并返回结果                                               |
| **Pub/Sub 事件广播**           | Router                                                       | ★★☆☆ | 有基础能力，但缺 topic 模式匹配、缺 Event 独立注入（已在交易系统分析中指出）                                                                              |
| **Agent 间 Handoff**        | 无                                                            | ★☆☆☆ | 当前没有「把当前执行上下文从 Agent A 转移到 Agent B」的原语                                                                                     |
| **Shared Memory**          | Session（trace→context）                                       | ★★☆☆ | Session 只做短生命周期的 trace 上下文；缺乏多 Agent 共享的持久化/分层记忆                                                                           |
| **Agent Card（能力发现）**       | BaseCommand._registry + AppService.router_mapping            | ★★☆☆ | 有命令注册表和路由映射，但没有标准化的「Agent 能力声明」                                                                                            |
| **Streaming 输出**           | StreamState (async generator)                                | ★★★★ | 已原生支持 SSE/WS 流式输出，和 LLM streaming 天然契合                                                                                     |


---

## 八、搭建 Multi-Agent 系统时 Bollydog 缺失的特性

以下**全部为通用框架增强**，不特定于 AI Agent；IoT 编排、微服务工作流同样受益。与交易系统分析中已列的增强项合并时，标注来源。

### 8.1 新增缺失能力（Agent 场景驱动）


| #                                              | 缺失能力                                                                                                                                                                          | Agent 场景需求                                                                                                                                                                                                                                                                           | 建议增强              | 优先级 |
| ---------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------- | --- |
| **⑧ 有状态多轮会话（Conversation/Thread）**             | Agent 的 ReAct Loop 通常是多轮的：LLM 输出 → Tool Call → 再 LLM → …，中间需要保持上下文（消息历史、工具结果）。当前 Session 只绑 trace_id 做单次 acquire/release，无法跨多轮保持                                              | 扩展 Session 或新增 **Thread** 概念：一个 Thread = 一组有序 Message/Event 的持久化会话；支持 `append_message(thread_id, msg)` / `get_history(thread_id)` / TTL 自动过期。后端可插 Memory/Redis/RDB。**不是 Agent 特化**——任何多轮交互（客服、工单、审批流）都需要                                                                             | **高**             |     |
| **⑨ Handoff（执行上下文转移）**                         | Supervisor 把任务派给 Worker 后，Worker 需要在「同一个会话」上下文中继续执行，且可以把控制权交回 Supervisor 或交给另一个 Worker。当前无此原语                                                                                 | 在 Hub 上增加 `**handoff(from_service, to_service, context)`** 或建模为特殊 Command：`HandoffCommand(target_agent, thread_id, payload)`。Hub 将当前 Thread 上下文传递给目标 Agent 并切换 execute 上下文。**通用**：工作流引擎的「节点流转」、客服转接都是同一模式                                                                            | **高**             |     |
| **⑩ Middleware / 拦截器链**                        | Agent 系统需要在 Command 执行前/后做通用处理：鉴权、Guardrail（内容安全）、Token 计量、日志审计、输入验证、Rate Limiting。当前 Hub._execute 是硬编码流程，无法插入自定义中间件                                                          | 在 Hub.execute 流程中引入 **Middleware 链**：`before_execute(message)` → execute → `after_execute(message, result)`。AppService 或全局级别都可注册。**通用**——任何框架都需要 middleware（参考 Starlette middleware、Django middleware、gRPC interceptor）                                                              | **高**             |     |
| **⑪ 并行扇出/聚合（Fan-out/Fan-in）**                  | Supervisor 同时给 3 个 Worker 派任务（fan-out），全部完成后聚合结果（fan-in）。当前 yield sub-command 是顺序的（yield A → await → yield B → await）                                                         | 在 Hub 或 Command 层面支持 **parallel dispatch**：如 `results = await hub.gather([cmd1, cmd2, cmd3])`，或在 async generator 里 yield 一个 `Parallel([cmd1, cmd2, cmd3])` 特殊对象，Hub 识别后并行 execute 并收集结果。**通用**——并行扇出是所有编排引擎的标准能力（Airflow fan-out、Step Functions Parallel）                            | **高**             |     |
| **⑫ Agent/Service 能力声明（Agent Card）**           | 多 Agent 系统中，Supervisor 需要知道「有哪些 Worker、每个 Worker 能做什么」来做路由决策。当前 AppService 只有 domain/alias，没有结构化的能力描述                                                                         | 在 AppService 上增加可选的 `**capabilities`** 声明：如 `capabilities = {'description': '...', 'skills': [...], 'input_schema': ..., 'output_schema': ...}`。Hub 提供 `list_capabilities()` 方法返回所有注册 Service 的能力。**通用**——MCP 的 Tool discovery、微服务注册中心的元数据都是同一模式（Google A2A 的 AgentCard 也是此概念）       | **中**             |     |
| **⑬ Tool 抽象层（统一工具接口）**                         | Agent 需要调用各种工具（搜索、数据库、API、文件、代码执行等）；MCP 定义了标准的 Tool 接口（name、description、inputSchema、call）。当前 bollydog 没有「Tool」作为一等概念                                                          | 在 Command 或 Protocol 之外，新增 **Tool** 抽象：`class Tool(name, description, input_schema, output_schema)` + `execute(**kwargs)`。或约定一类特殊 Command 为 Tool。同时支持**外部 MCP Server 适配**：一个 `McpProtocol(BaseService)` 连接 MCP Server，自动将其暴露的 Tools 注册为 bollydog Commands。**通用**——Tool 是任何可扩展系统的标准概念 | **中**             |     |
| **⑭ 分层 Memory（Working / Summary / Long-term）** | 生产级 Agent 需要多层记忆：当前上下文（Working）、历史摘要（Summary）、长期知识（Archival/Long-term）。当前 Session 只有扁平的 dict                                                                                  | 扩展 Session 或新增 **Memory** 子系统：支持多层 KV（Working: 当前 Thread 上下文 / Summary: 压缩后的历史 / Archival: 可搜索的长期知识）。后端可插 Memory/Redis/向量数据库。**通用**——状态化服务（游戏、IoT、长期任务）都需要分层状态管理                                                                                                                     | **中**             |     |
| **⑮ 执行状态机 / 工作流图**                             | 复杂多 Agent 流程（如 Plan → Research → Draft → Review → Publish）本质是一个有向图/状态机，节点是 Agent/Command，边是条件跳转。当前 bollydog 的编排只有 linear（yield A → B → C）和 dispatch（fire-and-forget），无法声明式定义图 | 可选增强：引入 **Workflow / DAG** 原语，允许声明式定义节点（Command/Agent）、边（条件）、并行/串行。不必自己实现引擎，可以复用 `mode` 的 task 能力 + 上述 fan-out/fan-in。**通用**——Airflow、Temporal、Step Functions 都是此模式                                                                                                                  | **低**（首期可用代码编排替代） |     |


### 8.2 交易系统分析中已列的增强项（Agent 同样需要）


| #                       | 增强项                                                                                  | Agent 场景为什么也需要 |
| ----------------------- | ------------------------------------------------------------------------------------ | -------------- |
| ① Router 主题模式匹配         | Agent 发布 `agent.research.done`、`agent.coding.error` 等事件，Supervisor 订阅 `agent.`* 统一监控 |                |
| ② Event 独立注入 `hub.emit` | Agent 在 ReAct loop 中间产生「观察到新信息」事件，需要主动 emit 而非等 Command 结束                           |                |
| ③ 声明式订阅                 | Agent 声明自己关心哪些事件，如 `@subscribe('agent.*.done')`                                      |                |
| ④ 定时任务                  | Agent 定期巡检、定期摘要压缩 Memory、定期清理过期 Thread                                               |                |
| ⑤ Service 发现            | Supervisor 需要 `hub.get_service(WorkerAgent)` 获取所有可用 Worker                           |                |
| ⑥ destination 路由        | 指定某个 Command 只能由特定 Agent 处理                                                          |                |
| ⑦ handler 分离            | 同一个 `ToolCallCommand` 可能由不同 Agent 各自实现不同的 handler                                    |                |


---

## 九、Agent 系统在增强后的 bollydog 上的设计范式

```
                    ┌─────────────────────────────────────────┐
                    │          Entrypoints                     │
                    │  HTTP / WS / CLI / MCP Client            │
                    └─────────────────┬───────────────────────┘
                                      │
                    ┌─────────────────▼───────────────────────┐
                    │              Hub (bollydog)               │
                    │  Broker │ Router │ Session/Thread         │
                    │  Middleware 链（guardrail / auth / meter）│
                    └─────────────────┬───────────────────────┘
                                      │
            ┌─────────────────────────┼─────────────────────────┐
            ▼                         ▼                         ▼
  ┌──────────────────┐    ┌──────────────────┐     ┌──────────────────┐
  │ SupervisorAgent  │    │ WorkerAgent A    │     │ WorkerAgent B    │
  │ (AppService)     │    │ (AppService)     │     │ (AppService)     │
  │                  │    │                  │     │                  │
  │ capabilities:    │    │ capabilities:    │     │ capabilities:    │
  │  "任务分解/聚合" │    │  "研究/搜索"     │     │  "编码/执行"     │
  │                  │    │                  │     │                  │
  │ on_start:        │    │ Tools:           │     │ Tools:           │
  │  @subscribe(     │    │  - WebSearch     │     │  - CodeExec      │
  │   'agent.*.done')│    │  - DocRetrieval  │     │  - FileWrite     │
  │                  │    │                  │     │                  │
  │ __call__:        │    │ Memory:          │     │ Memory:          │
  │  1. 分解任务     │    │  - Working       │     │  - Working       │
  │  2. hub.gather(  │    │  - Archival      │     │  - Archival      │
  │     [TaskA,TaskB])│    └──────────────────┘     └──────────────────┘
  │  3. 聚合结果     │               │                         │
  │  4. 或 handoff   │               │                         │
  └──────────────────┘               └────────────┬────────────┘
                                                  ▼
                                      ┌──────────────────┐
                                      │ Shared Memory    │
                                      │ (Thread/Archival)│
                                      │ Protocol 后端:   │
                                      │ Redis / RDB /    │
                                      │ Vector DB        │
                                      └──────────────────┘
```

**关键交互流程示例：Supervisor 分发任务给两个 Worker**

```python
class PlanAndExecute(BaseCommand):
    """Supervisor 的编排命令"""
    destination = 'agent.supervisor'
    query: str

    async def __call__(self):
        # 1. 分解任务（可调 LLM）
        subtasks = await plan(self.query)

        # 2. 并行分发给 Worker（fan-out / fan-in）
        cmds = [ResearchTask(topic=t) for t in subtasks]
        results = yield Parallel(cmds)  # Hub 识别 Parallel → gather

        # 3. 聚合（可调 LLM）
        final = await aggregate(results)
        return final


class ResearchTask(BaseCommand):
    """Worker A 的 Tool-Use ReAct Loop"""
    destination = 'agent.worker_a'
    topic: str

    async def __call__(self):
        # ReAct loop
        while not done:
            thought = await llm_call(prompt, memory.working)
            if thought.tool_call:
                result = yield ToolCallCommand(name=thought.tool_name, args=thought.tool_args)
                memory.working.append(result)
            else:
                return thought.answer
```

---

## 十、Bollydog 升级：统一增强项汇总（交易 + Agent + 通用）

将交易系统和 Agent 系统两个案例的需求合并去重，得到 bollydog 框架升级的完整路线图：

### 10.1 P0（必须做，两个场景都阻塞）


| #   | 增强项                                   | 交易  | Agent | 说明                                                            |
| --- | ------------------------------------- | --- | ----- | ------------------------------------------------------------- |
| ②   | **Event 独立注入 `hub.emit(event)`**      | ✅   | ✅     | 任何 Service/Agent 在 task loop 中主动发布事件                          |
| ①   | **Router 主题模式匹配**                     | ✅   | ✅     | `register("data.bar.*", cb)` / `register("agent.*.done", cb)` |
| ⑪   | **并行扇出/聚合 `hub.gather` / `Parallel`** | ☐   | ✅     | Supervisor 并行派 Worker；交易中可选但 Agent 必须                         |
| ⑧   | **有状态多轮会话 Thread**                    | ☐   | ✅     | Agent ReAct 多轮上下文；扩展现有 Session，增加持久化 + 追加语义                   |
| ⑩   | **Middleware / 拦截器链**                 | ☐   | ✅     | Guardrail / Auth / Token 计量 / Rate Limit；通用框架标配               |


### 10.2 P1（强烈建议）


| #   | 增强项                           | 交易  | Agent | 说明                          |
| --- | ----------------------------- | --- | ----- | --------------------------- |
| ③   | **声明式订阅 `@subscribe`**        | ✅   | ✅     | 框架自动 register               |
| ⑥   | **destination 路由语义完善**        | ✅   | ✅     | Command 路由到指定 Service/Agent |
| ⑨   | **Handoff（上下文转移）**            | ☐   | ✅     | Agent 间/工作流节点间流转            |
| ⑫   | **Service 能力声明 capabilities** | ☐   | ✅     | Agent Card / 服务注册元数据        |
| ⑬   | **Tool 抽象层 + MCP 适配**         | ☐   | ✅     | 统一工具接口，自动发现外部 MCP Tools     |


### 10.3 P2（锦上添花）


| #   | 增强项                                     | 交易  | Agent | 说明                         |
| --- | --------------------------------------- | --- | ----- | -------------------------- |
| ④   | `**@timer` / `@cron` 定时任务**             | ✅   | ✅     | 定期拉取 / 定期 Memory 清理        |
| ⑤   | `**hub.get_service(cls)` 服务发现**         | ✅   | ✅     | 类型安全的同进程引用                 |
| ⑦   | **Service 级 handler 注册**                | ✅   | ✅     | 同一 Command 不同 Service 各自处理 |
| ⑭   | **分层 Memory（Working/Summary/Archival）** | ☐   | ✅     | 可在 Thread 基础上扩展；后端可插向量数据库  |
| ⑮   | **声明式工作流 DAG**                          | ☐   | ☐     | 首期可用代码编排替代；后续若有复杂流程再做      |


### 10.2 技术实现估算


| 增强项              | 改动范围                                         | 估算工作量     |
| ---------------- | -------------------------------------------- | --------- |
| ① Router 主题匹配    | Router.register/publish 增加 fnmatch 或 trie    | 小（~50 行）  |
| ② hub.emit       | Hub 加一个方法直接调 router.publish                  | 小（~10 行）  |
| ③ 声明式订阅          | AppService + Hub.on_started 扫描               | 小（~30 行）  |
| ⑥ destination 路由 | Hub._execute 增加 resolve → 目标 Service handler | 中（~50 行）  |
| ⑧ Thread         | 扩展 Session 或新类，增加 append/get_history         | 中（~100 行） |
| ⑨ Handoff        | 特殊 Command + Hub 上下文切换                       | 中（~80 行）  |
| ⑩ Middleware     | Hub.execute 前后 hook list                     | 中（~60 行）  |
| ⑪ Parallel       | Hub.gather + async generator 识别              | 中（~80 行）  |
| ⑫ capabilities   | AppService 新字段 + Hub.list_capabilities       | 小（~30 行）  |
| ⑬ Tool + MCP     | Tool 类 + McpProtocol 适配器                     | 大（~200 行） |
| ⑭ 分层 Memory      | Memory 子系统 + Protocol 后端                     | 大（~200 行） |


---

## 十一、三案例总结

bollydog 本身就是一个**「事件驱动 + 服务树 + 消息编排」**的通用框架内核。通过三个案例的分析：

1. **案例一·交易系统**：主要缺 Event 独立注入、Router 主题匹配、声明式订阅——让引擎间能事件松耦合。
2. **案例二·AI Agent 系统**：在此基础上还需要：多轮会话 Thread、并行扇出聚合、Middleware 链、Handoff、Agent 能力声明、Tool 抽象与 MCP 适配、分层 Memory。
3. **案例三·分布式多 Hub 集群**：需要 Transport 传输层、Registry 服务注册表、RemoteDispatch 位置透明路由、Event Federation 事件联邦。**与业务逻辑零冲突**——分布式是在 dispatch→execute 之间插入的透明层，业务代码不感知。
4. **三层正交叠加**：L0 核心（已有） → L1 分布式（Transport/Registry）→ L2 编排（Thread/Middleware/Parallel）→ L3 业务（交易/Agent/IoT/...）。每层只依赖下层，不互相耦合。
5. **所有增强项都是通用的**，不面向某个特定领域做特化。
6. **总代码量估算**：L2 编排增强 ~300 行 + L1 分布式基础 ~370 行 ≈ **~670 行**即可让 bollydog 从单进程编排框架升级为分布式多场景平台。

---

# 案例三：分布式多 Hub 集群适配分析

## 十二、问题定义

bollydog 在设计预想中不仅是单进程框架，而是**分布式服务编排平台**：多个 bollydog 实例（每个跑一个 Hub）组成集群，具备：
- Hub 之间可以**路由任务**和**通信**
- **无主模型**（mesh）或**有网关路由/重定向**的混合拓扑
- **服务发现**：每个 Hub 知道集群中有哪些其他 Hub、各自注册了哪些 Service/Command

**核心问题**：这些分布式能力会和业务系统逻辑产生冲突吗？bollydog 的非特化能力应该如何设计？

## 十三、与业务逻辑是否冲突——结论：不冲突

### 13.1 为什么不冲突

业务代码（交易系统、AI Agent 系统、任何系统）与 bollydog 的交互**只有两个接触面**：

```
业务代码接触面          bollydog 内部
─────────────          ────────────
hub.dispatch(cmd)  →   [分发层]  →  execute  →  cmd.__call__()
yield sub_command  →   [分发层]  →  execute  →  sub_cmd.__call__()
```

业务代码**从不关心** dispatch 之后消息经过了哪些中间环节——它只关心「我发了一个 Command，拿到结果」。分布式路由是在 **dispatch 和 execute 之间** 插入的透明层：

```
                        本地                          分布式
dispatch(cmd)           dispatch(cmd)
    │                       │
    ▼                       ▼
_resolve_app(本地dict)    _resolve_destination
    │                       ├─ 本地？ → _resolve_app(本地)
    ▼                       └─ 远程？ → serialize → transport → 远端 Hub.execute
execute(cmd)                         → deserialize result ← transport
    │                       │
    ▼                       ▼
cmd.__call__()          cmd.__call__() (业务逻辑完全不变)
```

**关键设计原则：Location Transparency（位置透明）**
- `destination` 已经是逻辑地址（`domain.alias` 字符串），不是 Python 对象引用
- 只需让 `_resolve_destination` 先查本地 `apps`，查不到再查服务注册表→路由到远端
- 业务代码**零改动**

### 13.2 具体验证：三个场景均无冲突

| 场景 | 业务代码 | 分布式下的变化 | 冲突？ |
|------|----------|----------------|--------|
| 交易系统 DataEngine dispatch BarEvent | `hub.emit(BarEvent(...))` | 若 AnalysisEngine 在另一个 Hub 上，Router 将 Event federation 到远端 Hub 的 Router | 无——业务只 emit，不关心谁在哪 |
| AI Agent Supervisor yield ResearchTask | `yield ResearchTask(topic=...)` | 若 WorkerAgent 在另一个 Hub 上，Hub 把 ResearchTask 序列化发到远端执行 | 无——yield 的语义不变，拿到结果 |
| 普通微服务 ServiceA 调 ServiceB | `yield QueryOrder(order_id=...)` | ServiceB 在另一台机器上，dispatch 走远程路由 | 无——接口不变 |

## 十四、当前 bollydog 的单进程硬假设（必须打破的点）

通过源码分析，以下是阻碍分布式的硬编码单进程假设：

| # | 单进程假设 | 所在代码 | 分布式下的问题 |
|---|-----------|----------|----------------|
| A | `Hub.apps` 是进程内 dict | `app.py:33` `self.apps = {...}` | 只能查到本进程注册的 Service；远端 Service 不在 dict 里 |
| B | `_resolve_app` 只查本地 dict | `app.py:80-83` | destination 解析不到远端 |
| C | `Broker._store` 用 `asyncio.Future` 做结果容器 | `broker.py:18,31` | Future 不能跨进程 await |
| D | `message.state` 是 `asyncio.Future` / `StreamState` | `base.py:87,121` | 调用方 `await message.state` 只在本进程有效 |
| E | `Router.callbacks` 是本地 callback set | `router.py:10` | 事件只能发布给本进程的订阅者 |
| F | `globals` 的 `LocalStack` | `globals.py:4-8` | 协程本地，同进程内有效 |

## 十五、分布式 bollydog 的通用能力设计

### 15.1 架构总览：Transport + Registry + Federation

```
 ┌────────────────────────────────────────────────────────────────┐
 │                      bollydog Cluster                          │
 │                                                                │
 │  ┌──────────────┐         ┌──────────────┐                     │
 │  │  Hub A        │         │  Hub B        │                    │
 │  │  ┌─────────┐ │ Transport│  ┌─────────┐ │                    │
 │  │  │Broker   │ │◄────────►│  │Broker   │ │                    │
 │  │  │Router   │ │         │  │Router   │ │                    │
 │  │  │Session  │ │         │  │Session  │ │                    │
 │  │  ├─────────┤ │         │  ├─────────┤ │                    │
 │  │  │ServiceX │ │         │  │ServiceY │ │                    │
 │  │  │ServiceZ │ │         │  │ServiceW │ │                    │
 │  │  └─────────┘ │         │  └─────────┘ │                    │
 │  └──────┬───────┘         └──────┬───────┘                    │
 │         │                        │                             │
 │         └────────┬───────────────┘                             │
 │                  ▼                                             │
 │          ┌──────────────┐                                      │
 │          │  Registry     │  (服务注册表 —— Redis/etcd/gossip)   │
 │          │  hub_a:       │                                      │
 │          │   - ServiceX  │                                      │
 │          │   - ServiceZ  │                                      │
 │          │  hub_b:       │                                      │
 │          │   - ServiceY  │                                      │
 │          │   - ServiceW  │                                      │
 │          └──────────────┘                                      │
 └────────────────────────────────────────────────────────────────┘
```

### 15.2 四个核心分布式原语

#### ⑯ Transport（传输层）

**职责**：Hub 之间的消息传输。

```python
class Transport(BaseService, abstract=True):
    """Hub 间传输抽象，子类实现具体协议"""
    @abc.abstractmethod
    async def send(self, target_hub_id: str, envelope: Envelope) -> None: ...
    @abc.abstractmethod
    async def recv(self) -> Envelope: ...
    @abc.abstractmethod
    async def request(self, target_hub_id: str, envelope: Envelope) -> Envelope:
        """请求-响应语义（替代 await Future）"""
        ...

class Envelope(BaseModel):
    """跨 Hub 的消息信封：序列化后的 Command + 路由元数据"""
    source_hub: str
    target_hub: str
    correlation_id: str      # 请求-响应关联 ID（替代本地 Future）
    command_type: str         # Command 的 registry key（module.alias）
    payload: dict             # command.model_dump()
    result: dict | None = None
    error: str | None = None
    msg_type: str = 'request' # request / response / event
```

**实现选择**（均为可插拔，通过 Protocol 模式）：

| 实现 | 适用场景 | 说明 |
|------|----------|------|
| `RedisTransport` | 开发/中小规模 | 用 Redis Streams 或 Pub/Sub；已有 RedisProtocol 可复用连接 |
| `NatsTransport` | 生产/大规模 | NATS 天然支持 request-reply、pub/sub、queue groups |
| `GrpcTransport` | 低延迟点对点 | gRPC 双向流，适合 Agent 间高频通信 |
| `HttpTransport` | 跨网络/防火墙 | REST 或 SSE，兼容性最强 |

**关键**：Transport 不是 Protocol（Protocol 是「命令执行时的数据源适配器」）。Transport 是 Hub 基础设施的一部分，和 Broker/Router 同级。

#### ⑰ Registry（服务注册表）

**职责**：维护全集群的「逻辑服务 ID → 物理 Hub 地址」映射。

```python
class Registry(BaseService, abstract=True):
    """服务注册/发现抽象"""
    @abc.abstractmethod
    async def register(self, hub_id: str, services: list[ServiceMeta]) -> None: ...
    @abc.abstractmethod
    async def deregister(self, hub_id: str) -> None: ...
    @abc.abstractmethod
    async def lookup(self, destination: str) -> list[str]:
        """给定 destination(domain.alias)，返回所有拥有该 Service 的 hub_id 列表"""
        ...
    @abc.abstractmethod
    async def list_all(self) -> dict[str, list[ServiceMeta]]: ...

class ServiceMeta(BaseModel):
    destination: str          # domain.alias
    capabilities: dict = {}   # 与 Agent Card 复用
    hub_id: str
    hub_address: str          # Transport 可达地址
```

**实现选择**：

| 实现 | 说明 |
|------|------|
| `RedisRegistry` | Hash/Set 结构 + TTL 心跳，简单可靠 |
| `EtcdRegistry` | 强一致 + watch 变更通知 |
| `GossipRegistry` | 无中心依赖，AP 模型，适合边缘/mesh |
| `MemoryRegistry` | 单机测试用，进程内 dict |

#### ⑱ RemoteDispatch（位置透明路由）

**职责**：改造 `Hub.dispatch`，加入远程路由判断。

当前 `dispatch` 的流程：
```
dispatch(msg) → qos判断 → put_message(broker) 或 execute(本地)
```

增强后：
```
dispatch(msg)
    │
    ▼ _resolve_destination(msg)
    ├─ 本地 apps 里有？ → 走原有本地路径（execute / put_message）
    ├─ 本地没有？ → registry.lookup(msg.destination)
    │   ├─ 找到远端 hub_id？ → transport.request(hub_id, envelope)
    │   │   └─ 远端 Hub 收到 → deserialize → execute → serialize result → transport.respond
    │   └─ 找不到？ → raise DestinationNotFoundError
    └─ destination 为 None？ → 本地 execute（当前行为不变）
```

**核心代码改动点**（在 Hub 中）：

```python
async def dispatch(self, message: Message) -> Message:
    # 先尝试本地解析
    if self._is_local(message):
        # 原有逻辑不变
        if message.qos == 0 and self.state == "running":
            return await self.put_message(message)
        return await self.execute(message)
    # 远程路由
    return await self._remote_dispatch(message)

def _is_local(self, message: Message) -> bool:
    if not message.destination:
        return True  # 无 destination → 本地执行（向后兼容）
    return message.destination in self.apps

async def _remote_dispatch(self, message: Message) -> Message:
    hub_ids = await self.registry.lookup(message.destination)
    if not hub_ids:
        raise DestinationNotFoundError(message.destination)
    target = self._select_target(hub_ids)  # 负载均衡策略
    envelope = Envelope.from_message(message)
    result_envelope = await self.transport.request(target, envelope)
    message.state.set_result(result_envelope.result)
    return message
```

**关键设计决策：`message.state` 的跨进程替代**

本地模式下，调用方 `await message.state`（asyncio.Future）等待结果。远程模式下：
- 调用方的 `message.state` 仍然是本地 Future
- `_remote_dispatch` 通过 Transport 的 request-response 拿到结果后，`set_result` 到本地 Future
- 调用方**感知不到差异**

#### ⑲ Event Federation（事件联邦）

**职责**：让 `hub.emit(event)` 的事件可以跨 Hub 传播到远端订阅者。

```
Hub A: hub.emit(BarEvent)
    │
    ▼ 本地 Router.publish(BarEvent)  → 本地订阅者收到
    │
    ▼ EventFederator（Hub A 的子服务）
    │  检查：有没有远端 Hub 订阅了匹配的 topic？
    │  有 → transport.send(hub_b, Envelope(msg_type='event', ...))
    │
    ▼ Hub B 收到 event envelope
    │  → deserialize → 本地 Router.publish(BarEvent) → Hub B 本地订阅者收到
```

**订阅注册**：远端 Hub B 想订阅 Hub A 发布的 `data.bar.*` 事件时：
- Hub B 向 Registry 注册自己的**订阅兴趣**（subscription interest）
- 或通过 Transport 直接向 Hub A 发送 `SubscribeCommand(topic='data.bar.*')`
- Hub A 的 EventFederator 维护一个「远程订阅表」

### 15.3 拓扑模式：Mesh vs Gateway vs 混合

| 拓扑 | 描述 | 适用场景 |
|------|------|----------|
| **Mesh（无主）** | 每个 Hub 平等，直接 P2P 通信；Registry 用 gossip 或共享存储 | 小集群（<10 节点）、低延迟、容错要求高 |
| **Gateway（有主路由）** | 一个 Gateway Hub 做所有跨 Hub 路由；其他 Hub 只和 Gateway 通信 | 简单部署、集中管控、流量审计 |
| **混合** | 同区域 Hub 走 mesh；跨区域走 Gateway；Registry 决定路径 | 大规模、多区域 |

**bollydog 不应硬编码拓扑**，而是通过 Transport + Registry 的**可插拔组合**来决定：
- Mesh = `NatsTransport` + `GossipRegistry`
- Gateway = `HttpTransport` + `RedisRegistry` + 一个专门的 GatewayHub
- 混合 = 两者皆可配置

### 15.4 与单进程模式的兼容

**不配 Transport/Registry 时**，Hub 行为与现在**完全一致**：
- `_is_local` 始终返回 True（因为 destination 要么 None 要么在 apps 里）
- `_remote_dispatch` 永远不会被调用
- 零开销，零行为变化

这是**关键的设计约束**：分布式能力是**可选的叠加层**，不是必须的。

## 十六、分布式场景下各业务系统的表现

### 16.1 交易系统：跨 Hub 部署

```
┌─── Hub A (行情节点) ───┐    ┌─── Hub B (分析节点) ───┐
│ DataEngine             │    │ AnalysisEngine         │
│ CacheEngine            │    │   SwingService         │
│                        │    │   FibonacciService     │
│ hub.emit(BarEvent)  ───┼──► │   TouchDetector        │
│  (Event Federation)    │    │                        │
│                        │    │ yield GetKlines(...)   │
│                     ◄──┼──  │  (Remote Command)      │
└────────────────────────┘    └────────────────────────┘
```

- DataEngine emit BarEvent → Event Federation → Hub B 的 AnalysisEngine 收到
- AnalysisEngine yield GetKlines → Remote Dispatch → Hub A 的 CacheEngine 执行 → 结果回传
- **业务代码零改动**

### 16.2 AI Agent 系统：Worker 分布在不同 Hub

```
┌─── Hub A (Supervisor) ──┐    ┌─── Hub B (Worker) ──────┐
│ SupervisorAgent          │    │ ResearchWorkerAgent      │
│                          │    │  - WebSearchTool         │
│ yield Parallel([         │    │  - DocRetrievalTool      │
│   ResearchTask(...)  ────┼──► │                          │
│ ])                       │    └──────────────────────────┘
│                          │    ┌─── Hub C (Worker) ──────┐
│   CodingTask(...)  ──────┼──► │ CodingWorkerAgent        │
│                          │    │  - CodeExecTool          │
│ results = await ...      │    └──────────────────────────┘
└──────────────────────────┘
```

- Supervisor 的 `yield Parallel([ResearchTask, CodingTask])` 自动路由到对应 Worker Hub
- 结果通过 Transport 的 request-response 回传
- **业务代码零改动**

## 十七、bollydog 分布式增强项汇总

| # | 增强项 | 类别 | 说明 | 优先级 |
|---|--------|------|------|--------|
| ⑯ | **Transport 传输层抽象** | 基础设施 | Hub 间消息传输：send/recv/request-response；可插拔实现（Redis Streams / NATS / gRPC / HTTP） | **高** |
| ⑰ | **Registry 服务注册表** | 基础设施 | 服务元数据注册/发现/lookup；可插拔实现（Redis / etcd / gossip / Memory）；Hub on_started 自动注册，on_stop 自动注销 | **高** |
| ⑱ | **RemoteDispatch 位置透明路由** | Hub 核心 | 改造 dispatch：本地优先、查不到走 Registry → Transport；需解决 message.state 跨进程问题（本地 Future + 远程 request-response 桥接） | **高** |
| ⑲ | **Event Federation 事件联邦** | Router 扩展 | 跨 Hub 的 Event 传播；远端订阅注册 + EventFederator 子服务 | **中** |
| ⑳ | **负载均衡 / 路由策略** | Hub 核心 | 同一 destination 多个 Hub 提供时的选择策略（round-robin / least-load / random / affinity） | **中** |
| ㉑ | **Envelope 序列化协议** | 基础设施 | Command → Envelope → bytes 的序列化/反序列化；需处理 Pydantic model_dump / model_validate + Command 类型 resolve | **高** |
| ㉒ | **分布式 Trace 传播** | 可观测性 | 跨 Hub 时 trace_id / span_id / parent_span_id 自动传播（当前字段已有，只需 Envelope 携带） | **低** |
| ㉓ | **Hub Identity（hub_id）** | 基础设施 | 每个 Hub 需要唯一 ID + 可达地址，用于 Registry 注册和 Transport 寻址 | **高** |

## 十八、与已有增强项的关系——不冲突，正交叠加

```
              bollydog 能力层次
─────────────────────────────────────────────
L3  业务层    交易系统 / AI Agent / IoT / ...
             (只用 dispatch / yield / emit)
─────────────────────────────────────────────
L2  编排层    Thread / Middleware / Parallel /
             Handoff / Subscribe / Capabilities
             (案例一&二的增强项)
─────────────────────────────────────────────
L1  分布式层  Transport / Registry / RemoteDispatch
             / Event Federation / LoadBalance
             (本案例三的增强项)
─────────────────────────────────────────────
L0  核心层    Hub / Broker / Router / Session /
             Command / Event / AppService / Protocol
             (当前 bollydog 已有)
─────────────────────────────────────────────
```

- **L1（分布式）和 L2（编排）完全正交**：Middleware 在本地 execute 前后生效（不管消息来自本地还是远端）；Thread 在本地 Session 管理（远端通过共享存储同步）；Parallel 的 gather 在本地 Hub 发起（子命令可能路由到远端）
- **L3（业务）对 L1/L2 完全透明**：业务只面向 L2 的 API（dispatch / emit / yield / subscribe），不感知 L1 的存在
- **L1 在 L0 之上增量构建**：不改 Broker/Router/Session 的核心逻辑，只在 Hub.dispatch 加一个分叉点

## 十九、实现路径建议

### Phase 1：单机先做 L2（编排层增强）
- 先落 P0 的 emit / topic / parallel / thread / middleware
- 业务系统（交易/Agent）在单 Hub 上可以跑起来

### Phase 2：加 L1 基础（Transport + Registry + RemoteDispatch）
- 实现 `MemoryTransport`（单进程测试用，模拟跨 Hub）+ `MemoryRegistry`
- 改造 Hub.dispatch 加 `_is_local` / `_remote_dispatch` 分叉
- 写集成测试：两个 Hub 实例共享 MemoryTransport，验证跨 Hub Command 路由

### Phase 3：加 L1 生产实现（Redis/NATS Transport + Registry）
- `RedisTransport`（Redis Streams request-response）
- `RedisRegistry`（Hash + TTL heartbeat）
- Event Federation

### Phase 4：拓扑与运维
- 负载均衡策略
- 分布式 Trace（已有字段，只需 Envelope 携带）
- 健康检查 / 熔断 / 优雅下线

## 二十、技术实现估算（分布式部分）

| 增强项 | 改动范围 | 估算 |
|--------|----------|------|
| ⑯ Transport 抽象 + MemoryTransport | 新模块 `bollydog/transport/` | 中（~120 行） |
| ⑰ Registry 抽象 + MemoryRegistry | 新模块 `bollydog/registry/` | 中（~100 行） |
| ⑱ RemoteDispatch（Hub 改造） | `app.py` dispatch/execute 增加分叉 | 中（~80 行） |
| ⑲ Event Federation | Router 扩展 + EventFederator 子服务 | 中（~100 行） |
| ㉑ Envelope 序列化 | 新类 + Command 序列化/反序列化 | 小（~60 行） |
| ㉓ Hub Identity | Hub 增加 hub_id + address 字段 | 小（~10 行） |
| RedisTransport | `transport/redis.py` | 中（~150 行） |
| RedisRegistry | `registry/redis.py` | 中（~100 行） |

**Phase 2 核心**（MemoryTransport + MemoryRegistry + RemoteDispatch + Envelope）约 **370 行**，可在 L2 编排层之后快速叠加。

