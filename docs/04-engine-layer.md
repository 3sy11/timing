# 引擎层 — TimingApp / BacktestApp

## 两种运行模式

| | 生产模式 | 回测模式 |
|---|---------|---------|
| 入口 | TimingApp | BacktestApp |
| 启动方式 | `bollydog service --config config.toml` | `python main.py execute RunBacktest --config config.toml` |
| 数据触发 | 外部推 bar → PushBars 广播 | execute 主动喂历史数据 |
| 信号传递 | 异步 create_task | 同步 hub.execute |
| 持续运行 | 是（service 常驻） | 否（execute 完即退出） |

---

## TimingApp（生产）

纯容器，无自身逻辑。通过 TOML depends 声明依赖，框架自动启动全部服务。

```toml
["timing.engine.app.TimingApp"]
depends = ["data.DataEngine", "analysis.RetracementService", "strategy.FibStrategy", "execution.Broker"]
```

启动后系统进入 service 常驻模式，等待外部推数据触发处理链路。

---

## BacktestApp（回测）

### 职责

1. **动态创建分析实例**（on_init_dependencies）  
   读 backtest.toml → 为每组参数创建独立的 AnalysisEngine 子服务

2. **注册 subscriber**（on_started）  
   因为动态实例创建晚于 Exchange.on_started，需手动注册到 Exchange

3. **execute 模式**  
   通过 `RunBacktest` 命令入口执行回测逻辑，执行完即退出

### execute 执行流程

```
① 清除 AnalysisEngine._services（隔离生产配置，只保留回测实例）
② 清除回测实例的 checkpoint（保证全量重跑）
③ GetKlines 拉取全部历史 K 线
④ exchange.match 找到所有 on_bar handler
⑤ asyncio.gather 并行执行所有分析服务
⑥ 每个 on_bar 内部同步完成：分析 → 信号 → 策略 → 下单 → 撮合
⑦ 汇总结果返回
```

### _services 隔离机制

回测前必须清除 `_services`，原因：

```
Hub 启动时同时加载 TimingApp 和 BacktestApp 的依赖
  → _services 中混入了生产配置的 RetracementService
  → 如果不清除，回测会操作生产实例的 checkpoint / 缓存
  → 清除后只保留 BacktestApp 动态创建的实例，互不干扰
```

---

## backtest.toml 格式

```toml
warmup_bars = 200
symbol = "BTCUSDT"
interval = "1h"

[[services]]
module = "timing.analysis.algo.retracement.service.RetracementService"
cache_path = "cache/backtest/retracement_0"
[services.config]
touch_tolerance = 0.3

[[services]]
module = "timing.analysis.algo.retracement.service.RetracementService"
cache_path = "cache/backtest/retracement_1"
[services.config]
touch_tolerance = 0.5
```

每个 `[[services]]` 块 → 一个独立的分析实例（独立 alias、独立 SQLite、独立参数）。

---

## 启动顺序

```
bollydog CLI
  → load_from_config(config.toml)
    → create_from 创建所有服务
    → 解析 depends 建立依赖
    → 加载 commands
  → Hub 启动
    → Exchange.on_started 注册 TOML subscriber
    → Hub.on_started 依次 maybe_start 所有服务
      → BacktestApp.on_init_dependencies → 创建动态实例
      → BacktestApp.on_started → 注册动态 subscriber
  → hub.execute(RunBacktest) → 执行回测
```

---

## 文件清单

| 文件 | 内容 |
|------|------|
| engine/app.py | TimingApp + BacktestApp |
| engine/command.py | RunBacktest 命令（BacktestApp execute 入口） |
| config.toml | 生产配置 |
| backtest.toml | 回测参数 |
| main.py | CLI 入口 |
