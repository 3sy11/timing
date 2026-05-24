# Phase 0-3：场景 → 领域 → 行为 → 服务

> Issue: 20260524-initial-design — 系统初始构建，覆盖所有已实现功能的完整设计追溯。

---

## Phase 0：场景故事

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
| B1 | 命令行启动回测，系统逐根K线驱动分析、策略、执行，完成所有交易并记账 |
| B2 | 回测结束后，查看所有中间结果：每根K线产出了什么信号、策略做了什么决策、每笔订单的成交价和手续费 |
| B3 | 回测结束后，查看最终账户盈亏、总手续费、成交笔数等汇总指标 |
| B4 | 两次回测使用独立的存储空间，互不干扰，可以对比两次结果 |
| B5 | 回测中某些信号被策略跳过，某些订单被余额拒绝，最终只有部分信号变成了成交 |
| B6 | 回测中包含限价单，在历史数据的后续K线中被触发成交 |

### C 类：参数实验（批量回测 + 可视化）

| ID | 场景故事 |
|----|---------|
| C1 | 不确定指标参数是否合理，对单次回测的中间结果查表画图，判断指标是否有效 |
| C2 | 配置多组不同的指标参数，系统自动对每组参数分别执行完整回测，每组之间状态隔离 |
| C3 | 批量回测结束后，一次性查看所有实验的收益、回撤、夏普等绩效指标 |
| C4 | 在图表上叠加显示K线走势、指标关键位、信号触发点、买卖成交点 |
| C5 | 对比两组参数的收益曲线，看参数敏感度 |
| C6 | 导出某次实验的完整中间数据，用外部工具做进一步分析 |

### D 类：后管系统（Web 可视化管理）

| ID | 场景故事 |
|----|---------|
| D1 | 启动系统后打开浏览器，看到所有服务的运行状态 |
| D2 | 在界面上配置参数网格并提交批量回测任务，实时看到每组参数的执行进度 |
| D3 | 在界面上浏览历史回测结果列表，按收益率或夏普排序 |
| D4 | 点击某次回测查看详情，看到K线图叠加买卖点、权益曲线和回撤曲线 |
| D5 | 在界面上传数据文件导入到系统 |
| D6 | 回测进行中，界面实时显示当前进度和已完成的单次结果 |

---

## Phase 1：领域划分

| domain | 主语（服务） | 一句话职责 | 故事来源 |
|--------|------------|-----------|---------|
| data | DataEngine | 存储和查询 K 线数据 | A1-A10, B1 |
| analysis | AnalysisEngine / RetracementService | 从 K 线计算交易信号 | A1-A4 |
| strategy | FibStrategy | 过滤信号、决定是否下单，记录每次决策 | A1, A4, A5, B2 |
| execution | Broker | 撮合订单、管理持仓和账户 | A1, A5, A7 |
| backtest | BacktestApp | 协调回测生命周期和隔离 | B1, B4, C2 |
| timing | TimingApp | 生产入口容器，编排所有服务启动 | A8 |
| dashboard | DashboardService | 提供 Web API 和界面，管理回测任务，查看结果 | D1-D6 |

---

## Phase 2：行为设计

### 行为映射表

| 主语 | 发出的 Command | 发出的 Event | 订阅的 Topic → 反应方法 |
|------|---------------|-------------|----------------------|
| DataEngine | — | — | — |
| RetracementService | GetKlines（跨服务查询）, ComputeRetracement | SignalEmitted | `data.DataEngine.PushBars` → `on_bar` |
| FibStrategy | SubmitOrder | — | `analysis.AnalysisEngine.SignalEmitted` → `on_signal` |
| Broker | — | OrderFilled, OrderRejected | — |
| BacktestApp | RunBacktest, BatchBacktest, GetKlines | BacktestProgress | — |
| DashboardService | StartBatch → RunBacktest | BacktestProgress | — |

### destination 命名

| destination | 类型 |
|------------|------|
| `data.DataEngine.PushBars` | Command |
| `data.DataEngine.GetKlines` | Command |
| `data.DataEngine.ImportKlines` | Command |
| `analysis.RetracementService.ComputeRetracement` | Command |
| `analysis.AnalysisEngine.SignalEmitted` | Event |
| `execution.Broker.SubmitOrder` | Command |
| `execution.Broker.CancelOrder` | Command |
| `execution.Broker.OrderFilled` | Event |
| `execution.Broker.OrderRejected` | Event |
| `backtest.BacktestApp.RunBacktest` | Command |
| `backtest.BacktestApp.BatchBacktest` | Command |
| `dashboard.DashboardService.GetStatus` | Command |
| `dashboard.DashboardService.ListRuns` | Command |
| `dashboard.DashboardService.GetRun` | Command |
| `dashboard.DashboardService.StartBatch` | Command |
| `dashboard.DashboardService.ListDatasets` | Command |
| `dashboard.DashboardService.UploadData` | Command |
| `dashboard.DashboardService.BacktestProgress` | Event |

---

## Phase 3：服务职责

### DataEngine

- **状态**：DuckDB（K 线列式存储）
- **Protocol**：直接 DuckDB 连接（`db_path` 配置）
- **业务方法**：`append_bars` / `get_klines` / `set_klines`
- **depends**：无

### AnalysisEngine（基类）+ RetracementService（子类）

- **状态**：SQLite（checkpoint + 回撤结构 + 信号 + 触碰去重）
- **Protocol**：CacheLayer → SQLiteProtocol
- **业务方法**：`on_bar` / `_warmup` / `_process_bar`
- **depends**：DataEngine
- **生命周期**：on_start 创建默认 protocol 链

### FibStrategy

- **状态**：SQLite（策略决策记录 decisions）
- **Protocol**：CacheLayer → SQLiteProtocol
- **业务方法**：`on_signal`
- **depends**：无

### Broker

- **状态**：SQLite（持仓 + 订单 + 成交记录）
- **Protocol**：SimExchangeProtocol → CacheLayer → SQLiteProtocol（三层嵌套）
- **业务方法**：`on_submit_order` / `process_pending` / `get_position` / `get_all_positions` / `get_account`
- **depends**：无

### BacktestApp

- **状态**：backtest.toml 动态配置
- **业务方法**：`on_init_dependencies`（动态创建分析实例）/ `on_started`（注册 subscriber）
- **depends**：DataEngine, FibStrategy, Broker
- **commands**：RunBacktest, BatchBacktest

### TimingApp

- **状态**：无
- **业务方法**：无（纯容器）
- **depends**：DataEngine, RetracementService, FibStrategy, Broker

### DashboardService

- **状态**：SQLite（回测记录 runs + datasets + current_job）
- **Protocol**：CacheLayer → SQLiteProtocol
- **业务方法**：`run_batch_job` / `_reset_state` / `_mount_static_delayed`
- **depends**：无
- **router_mapping**：6 个 HTTP API 路由
- **静态文件**：挂载 web/ 到 HttpService
