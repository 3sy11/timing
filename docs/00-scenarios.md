# Phase 0-3：场景 → 领域 → 行为 → 服务

---

## Phase 0：场景故事

每条故事一句话，零类名，零技术术语。

### A 类：生产模式（实时推送）

| ID | 场景故事 |
|----|---------|
| A1 | 推一根新K线，系统检测到价格靠近关键位，产出交易信号，策略判定信号有效后下单买入并记账 |
| A2 | 推一根新K线，系统检测后未触碰任何关键位，不产出信号，链路静默结束 |
| A3 | 推一根新K线，价格突破了指标的有效边界，系统重新计算指标结构并保存更新后的数据 |
| A4 | 推一根新K线，系统产出交易信号，但策略判定信号强度不够，放弃下单并记录跳过原因 |
| A5 | 系统产出信号且策略通过，但账户余额不足以下单，记录拒绝并广播拒绝事件 |
| A6 | 查看系统中所有的中间记录：哪些信号被产出、哪些被策略接受或拒绝、哪些订单成交了 |
| A7 | 策略下了一个限价单，当时没成交，后续某根K线价格触及目标价后自动成交并记账 |
| A8 | 系统启动后加载配置，初始化所有服务，等待外部推送数据 |
| A9 | 查看当前持仓和账户余额 |
| A10 | 通过命令行将数据文件中的K线导入到系统数据库中 |

### B 类：回测模式（命令行启动）

| ID | 场景故事 |
|----|---------|
| B1 | 命令行启动回测，系统读取历史K线，分析处理后产出一组交易信号，策略逐个判断后下单，执行器完成所有交易并记账 |
| B2 | 回测结束后，查看所有中间结果：每根K线产出了什么信号、策略做了什么决策、每笔订单的成交价和手续费 |
| B3 | 回测结束后，查看最终账户盈亏、总手续费、成交笔数等汇总指标 |
| B4 | 两次回测使用独立的存储空间，互不干扰，可以对比两次结果 |
| B5 | 回测中某些信号被策略跳过，某些订单被余额拒绝，最终只有部分信号变成了成交 |
| B6 | 回测中包含限价单，在历史数据的后续K线中被触发成交 |

### C 类：参数实验（批量回测 + 可视化）

| ID | 场景故事 |
|----|---------|
| C1 | 不确定指标参数是否合理，对单次回测的中间结果查表画图，判断指标是否有效 |
| C2 | 配置多组不同的指标参数，系统自动对每组参数分别执行完整回测 |
| C3 | 批量回测结束后，一次性查看所有实验的中间过程和最终结果 |
| C4 | 在图表上叠加显示K线走势、指标关键位、信号触发点、买卖成交点 |
| C5 | 对比两组参数的收益曲线，看参数敏感度 |
| C6 | 导出某次实验的完整中间数据，用外部工具做进一步分析 |

### 故事之外的实现细节

| 细节 | 归属步骤 | 说明 |
|------|---------|------|
| 分析服务的 checkpoint 增量处理 | Phase 4 顺序图 | on_bar 按 checkpoint 只获取增量数据 |
| 指标突破后的全量重算 | Phase 4 顺序图 | A3 触发条件和重算范围 |
| 生产异步 vs 回测同步 | Phase 4 顺序图 | 事件传递方式不同 |
| 逐 bar 循环的执行者 | Phase 4 顺序图 | 外层推入 vs 分析服务内部拉取 |
| 存储路径隔离 | Phase 4 顺序图 | B4 独立空间的路径规则 |
| 中间结果序列化时机 | Phase 4 顺序图 | A6/B2 依赖各层写入时机 |

---

## Phase 1：领域划分

从场景故事中提取主语（动作发起者），按职责分组。

| domain | 主语（服务） | 一句话职责 | 故事来源 |
|--------|------------|-----------|---------|
| data | DataEngine | 存储和查询 K 线数据 | A1-A10, B1 |
| analysis | AnalysisEngine / RetracementService | 从 K 线计算交易信号 | A1-A4 |
| strategy | FibStrategy | 过滤信号、决定是否下单，记录每次决策 | A1, A4, A5, A6, B2 |
| execution | Broker | 撮合订单、管理持仓和账户 | A1, A5, A7 |
| engine | BacktestApp / TimingApp | 协调所有服务的启动和运行模式 | A8, B1, B4, C2 |

FibStrategy 是独立的 AppService，通过 subscriber 订阅 `SignalEmitted` 事件与分析层解耦。
每个 FibStrategy 实例拥有自己的 SQLite 存储策略决策记录，支持 A4/A6/B2 中的"查看策略决策"场景。

---

## Phase 2：行为设计

### 行为映射表

| 主语 | 发出的 Command | 发出的 Event | 订阅的 Topic → 反应方法 |
|------|---------------|-------------|----------------------|
| DataEngine | — | — | — |
| RetracementService | GetKlines (跨服务查询) | SignalEmitted | `data.DataEngine.PushBars` → `on_bar` |
| FibStrategy | SubmitOrder | — | `analysis.AnalysisEngine.SignalEmitted` → `on_signal` |
| Broker | — | OrderFilled / OrderRejected | — |
| BacktestApp | RunBacktest, GetKlines | — | — |

### destination 命名

| destination | 类型 |
|------------|------|
| `data.DataEngine.PushBars` | Command |
| `data.DataEngine.GetKlines` | Command |
| `data.DataEngine.ImportKlines` | Command |
| `analysis.AnalysisEngine.SignalEmitted` | Event |
| `execution.Broker.SubmitOrder` | Command |
| `execution.Broker.OrderFilled` | Event |
| `execution.Broker.OrderRejected` | Event |
| `backtest.BacktestApp.RunBacktest` | Command |

---

## Phase 3：服务职责

### DataEngine

- **持有的状态**：DuckDB（K 线存储）
- **Protocol 组合**：TableCacheLayer → DuckDBProtocol
- **业务方法**：append_bars / get_klines / set_klines
- **depends**：无

### AnalysisEngine（基类）+ RetracementService（子类）

- **持有的状态**：SQLite（checkpoint + 回撤结构 + 信号缓存）
- **Protocol 组合**：CacheLayer → SQLiteProtocol（每个实例独立路径）
- **业务方法**：on_bar / _warmup / _process_bar
- **depends**：DataEngine
- **生命周期**：on_start 创建默认 protocol 链（如 TOML 未配置）

### FibStrategy

- **持有的状态**：SQLite（策略决策记录 decisions）
- **Protocol 组合**：CacheLayer → SQLiteProtocol（每个实例独立路径）
- **业务方法**：on_signal
- **depends**：无

### Broker

- **持有的状态**：SQLite（持仓 + 订单 + 成交记录）
- **Protocol 组合**：SimExchangeProtocol → CacheLayer → SQLiteProtocol（每个实例独立路径）
- **业务方法**：on_submit_order / get_position / get_all_positions / get_account
- **depends**：无

### BacktestApp

- **持有的状态**：backtest.toml 配置
- **业务方法**：on_init_dependencies / on_started
- **depends**：DataEngine, FibStrategy, Broker
- **生命周期**：on_init_dependencies 动态创建分析实例，on_started 注册 subscriber

---

## 差异设计描述

以下是文档设计与当前代码的差异，以设计文档为准，后续代码迭代时统一处理：

- [x] FibStrategy 增加 protocol 配置（CacheLayer → SQLiteProtocol），持久化 decisions，支持 A4/A6/B2 场景
- [x] 代码中命令名 `IngestKlinesFromFile` 重命名为 `ImportKlines`，统一接口契约
