# A2d：引擎层实现详述

> A2 接口契约的补充文档，展开引擎层两种运行模式的实现细节、回测流程、结果收集和注意事项。

---

## 两种运行模式

| | 生产模式 | 回测模式 |
|---|---------|---------|
| 入口服务 | TimingApp | BacktestApp |
| 启动方式 | `bollydog service --config config.toml` | `python main.py execute RunBacktest` |
| 数据触发 | 外部推 bar → PushBars 广播 | RunBacktest 逐 bar dispatch PushBars(replay=true) |
| 信号传递 | 异步 `create_task`（框架 `_publish`） | 同步 `hub.execute` + `exchange.match` |
| 持续运行 | 是（service 常驻） | 否（execute 完即退出） |

---

## TimingApp（生产模式）

纯容器，无自身业务逻辑。通过 `add_dependency` 声明依赖，框架自动启动全部服务。

```
TimingApp
  ├── DataEngine
  ├── AnalysisEngine
  │     └── RetracementService
  ├── FibStrategy
  └── Broker + SimExchangeProtocol
```

启动后系统进入 service 常驻模式，等待外部推送数据触发处理链路。

> TimingApp 无 A2 接口条目（纯容器无方法），在 A2 继承关系总览中列出。

---

## BacktestApp（回测模式）

### 服务树

```
BacktestApp
  ├── DataEngine（指向原始数据库，replay 模式跳过写入）
  ├── AnalysisEngine（常驻子服务，Hub 启动时 Exchange 自然发现 subscriber）
  │     └── RetracementService
  ├── FibStrategy（消费 SignalEmitted）
  └── Broker + SimExchangeProtocol（撮合 + 持仓管理）
```

AnalysisEngine 作为 BacktestApp 的常驻 dependency，不再每次回测动态创建。
每次 RunBacktest 通过 `restart()` 走完整的 mode 生命周期重置状态。

### on_init_dependencies 实现（A2 已定义，故事 A8，图待补）

读取 `backtest.toml` 配置文件，根据配置动态创建子服务实例列表并返回。
框架调用 `add_dependency` 统一注册。

### on_started 实现（A2 已定义，故事 A8，图待补）

手工将分析服务的 subscriber（OnBarReceived）注册到 Hub 的 Exchange 中。
确保 PushBars 广播后能路由到分析服务。

---

## RunBacktest 执行流程（A2 已定义 7 步）

补充 A2 中 RunBacktest.__call__ 的实现细节：

```
① GetKlines 全量 → klines（500根示例）
   hub.execute(GetKlines(symbol, interval)) → list[dict]

② restart() 重置分析服务
   protocol.remove("__ckpt:{s}:{i}")  ← 清除 checkpoint，强制全量重算
   analysis.restart()                  ← on_stop → service_reset → on_start

③ _warmup(klines[:warmup_bars])
   analysis._warmup(symbol, interval, klines[:200])
   → compute_retracement → 初始化回撤结构

④ 逐 bar 回放
   for bar in klines[warmup_bars:]:
     clock.set_time_ms(bar.ts)                    ← 推进模拟时钟
     broker.process_pending(bar)                   ← 先检查挂单
     hub.execute(PushBars(symbol, interval, [bar], replay=true))
       → on_bar → _process_bar → signals
         → on_signal → SubmitOrder → fill（完整链路同步完成）

⑤ 从各模块 protocol.get 读取中间结果
   signals   = analysis.protocol.get("signals:{s}:{i}")
   decisions = strategy.protocol.get("decisions:{s}:{i}")
   fills     = broker.protocol.get("__fills")

⑥ 读取最终状态
   account   = broker.get_account()
   positions = broker.get_all_positions()

⑦ return {signals, decisions, fills, account, positions}
```

### 逐 bar 循环顺序

每根 bar 的处理顺序严格固定：

```
① clock.set_time_ms(bar.ts)     ← 先推时钟
② broker.process_pending(bar)    ← 先处理上一轮挂单
③ PushBars → 触发完整信号链路    ← 再处理新数据
```

挂单在新 bar 开头处理，确保限价单按正确的 bar 价格触发。

### 同步 vs 异步：为什么用 hub.execute 而非 dispatch

`dispatch` 内部用 `asyncio.create_task`，subscriber 跑在独立 task 中。
回测无法保证 bar N 的 subscriber 在 bar N+1 之前完成。

`hub.execute` 是同步完成，配合 `exchange.match` 手动触发 subscriber，
保证每根 bar 的完整链路（信号 → 决策 → 成交）在下一根 bar 之前全部完成。

---

## 回测结果收集

回测结束后，各层数据可从各模块的序列化存储中按需读取：

| 数据 | 来源模块 | 获取方式 |
|------|---------|---------|
| Signal | RetracementService | `protocol.get("signals:{s}:{i}")` |
| StrategyDecision | FibStrategy | `protocol.get("decisions:{s}:{i}")` |
| FillResult | Broker | `protocol.get("__fills")` |
| Position | Broker | `get_position(symbol)` / `get_all_positions()` |
| Account | Broker | `get_account()` |

### 返回值结构

```python
{
    "symbol": str, "interval": str,
    "signals": list[Signal],
    "decisions": list[StrategyDecision],
    "fills": list[FillResult],
    "final_account": Account,
    "final_positions": dict[str, Position]
}
```

汇总指标（收益率、最大回撤、胜率等）从上述原始数据按需计算，不需要额外数据模型。

---

## 启动顺序

```
bollydog CLI
  → load_from_config(backtest.toml)
  → Hub 启动
    → BacktestApp.on_init_dependencies() → 创建子服务
    → Exchange.on_started() → 遍历 hub.apps 注册所有 subscriber
    → Hub.on_started() → 依次 maybe_start 所有服务
    → BacktestApp.on_started() → 手工注册 subscriber
  → hub.execute(RunBacktest(symbol, interval))
  → 返回结果后退出
```

---

## 文件清单

| 文件 | 内容 |
|------|------|
| engine/app.py | TimingApp + BacktestApp |
| engine/command.py | RunBacktest 命令 |
| backtest.toml | 回测配置文件 |

---

## 备注：A2 未定义项（代码实现时移除）

以下内容在旧设计文档中出现，但 A2 接口契约中的 RunBacktest.__call__ 流程未包含。
保留仅作参考，**在执行代码实现的时候移除**。

| 项目 | 旧文档位置 | 说明 |
|------|-----------|------|
| RunBacktest 中 `apply_config(overrides)` 步骤 | 旧 06 RunBacktest 步骤② | 旧设计在 restart 后、warmup 前插入 `apply_config(overrides)` 覆盖子服务配置。A2 中 `apply_config` 归属故事 C2（批量参数实验），B1（单次回测）使用默认配置。待 C2 顺序图补充后再加入 RunBacktest 流程 |
