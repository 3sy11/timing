# Task Plan: 基于 bollydog 的交易系统（首期：斐波那契回撤 + 触线检测）

## Goal

在 timing 项目的 bollydog 核心库之上构建一套交易系统；首期实现：对指定标的在给定时间范围内的 K 线**先做 swing 识别、取出一笔趋势腿**，再基于该笔计算水平斐波那契回撤线，并接入实时价格数据，判断价格是否触碰到回撤线。

## Current Phase

Phase 1

## 整体架构（参考 NautilusTrader + bollydog）

**详细架构、各层子系统/组件清单、引擎层定义见 [timing/ARCHITECTURE.md](ARCHITECTURE.md)**，以下为概要。

- **架构图与分层**：Entrypoints → Hub(bollydog) → **四个引擎**（DataEngine、Market Data/Cache、Analysis Engine、ExecutionEngine/RiskEngine 预留）→ Adapters。每层所含**子系统/组件**见 ARCHITECTURE.md §1.2。
- **服务模型**：每个**引擎**为独立 `bollydog.AppService`；每个引擎内的**组件**为独立 `bollydog.AppService`，作为该引擎的**子服务**（add_dependency / add_service）。
- **四引擎**：DataEngine（行情接入）；Market Data/Cache（K 线、快照）；Analysis Engine（指标、斐波那契回撤、触线检测，**首期只做这块**）；ExecutionEngine / RiskEngine（预留）。
- **行情接入层**：DataClient 端口、DataEngine（AppService）、Cache（AppService）、事件与 Router；目录与检查项见 ARCHITECTURE.md §3。

## Phases

### Phase 1: 需求与发现

- 理解用户意图：交易系统基础 + 首期斐波那契回撤与触线
- 梳理 bollydog 能力与 timing 目录结构
- 记录到 findings.md、确定架构
- **Status:** complete

### Phase 2: 规划与结构

- 确定 K 线数据模型与来源（内存/文件/Redis）
- **确定 swing 识别规则**：swing high/low 定义（左侧/右侧 N 根 K 的极值）、如何选「一笔腿」（最近一笔涨/跌、或指定方向）
- 确定斐波那契回撤线计算接口（**输入为一笔的 high/low**，比例）；与 swing 输出衔接
- 确定实时价格输入形式（WebSocket/HTTP/Command）与触线判定规则（容差、去抖）
- 在 timing 下划分子包：如 `timing/data`、`timing/analysis`（含 swing、fibonacci、touch）、`timing/signals`
- **Status:** in_progress

### Phase 2b: 引擎层·行情接入层设计

- 撰写 **timing/ARCHITECTURE.md**：整体架构 + 引擎层总览 + **行情接入层**（DataEngine、DataClient 端口、数据类型、Cache、数据流、目录与接口清单、检查项），参考 NautilusTrader 代码结构
- 按 ARCHITECTURE.md §3.6 检查项逐项实现（DataClient 抽象、ListDataClient、DataEngine、Cache、BarEvent 与 Router）
- **Status:** 设计完成，实现待做

**首期模块与接口（草案）**

- `timing/data`：`OHLCV` / `Kline` 模型；`KlineSource` 抽象（内存/列表/后续可接 Redis）。
- `**timing/analysis/swing`**：`find_swing_highs_lows(klines, left_bars, right_bars)` → 拐点序列（含 ts, price, type=high|low）；`select_trend_leg(swings, direction='up'|'down'|'latest')` → 一笔的 (start_ts, end_ts, low, high) 或等价的 K 下标/区间，供斐波那契使用。
- `timing/analysis/fibonacci`：`compute_retracement_levels(high, low, ratios)` → `List[Tuple[float, float]]`（ratio, price）；`**retracement_from_leg(leg_low, leg_high, ratios)**` 基于一笔的 low/high（leg 来自 swing 模块）；保留可选 `retracement_from_klines(klines, ratios)` 作「区间极值」兼容。
- `timing/signals`（或 `timing/analysis/touch`）：`check_touch(price, levels, tolerance)` → 触中的 level 列表；可选 `TouchDetector` 状态（去抖：同一 level 冷却时间内不重复发）。
- 接入 bollydog：Command 如 `ComputeFibRetracement(symbol, interval, start_ts, end_ts, leg_direction?)`（内部先 swing 再 fib）、`FeedPrice(...)`；Event 如 `FibLevelTouched(...)`；由 Hub 注册 handler，Router 订阅发布。

### Phase 3: 实现（首期）

- 实现 K 线模型与时间范围内数据获取（`timing/data`: Kline, ListKlineSource）
- **实现 swing 识别**：`timing/analysis/swing` 中 find_swing_highs_lows、select_trend_leg，得到 (leg_low, leg_high)
- 实现水平斐波那契回撤线计算：`timing/analysis/fibonacci` 中 retracement_from_leg、retracement_from_klines
- 实现「实时价格 → 与回撤线比较 → 触线事件」逻辑：`timing/analysis/touch` 中 check_touch、TouchDetector（去抖）
- 通过 bollydog Command/Event 接入：ComputeFibRetracement、FeedPrice、FibLevelTouched（后续）
- **Status:** complete（核心链路已通；bollydog 接入待做）

### Phase 4: 测试与验证

- 单元测试：**swing 识别**（已知拐点的序列）、回撤线计算、触线判定
- 集成：用历史 K 线 → swing → 一笔 → fib → 模拟实时价触线，端到端验证
- 结果记入 progress.md
- **Status:** pending

### Phase 5: 交付

- 代码就绪，可运行示例：`cd 项目根(3sy11) && python3 timing/example_fib_touch.py`
- **使用 uv 管理依赖与虚拟环境**：项目根(3sy11) 下 `pyproject.toml`、`.python-version`、`.venv`；`uv venv` / `uv sync` / `uv run python timing/example_fib_touch.py`
- 更新 README/文档说明首期能力与用法
- **Status:** in_progress

## Key Questions

1. K 线数据从哪来？历史用文件/Redis，实时用现有行情网关还是 mock？→ Phase 2 定
2. **Swing 参数**：左右各几根 K 判定拐点（如 left=5, right=5）？选笔规则：最近一笔涨/跌/还是由调用方传 direction？→ Phase 2 定
3. 触线判定：绝对相等还是区间容差？是否去抖（连续 N 笔触线才告警）？→ Phase 2 定
4. 斐波那契比例集合是否可配置？→ 首期可写死常用比例，后续做成配置

## Decisions Made


| Decision                                                                      | Rationale                                           |
| ----------------------------------------------------------------------------- | --------------------------------------------------- |
| 架构沿用 bollydog Hub/Broker/Router，新增 Data/Analysis/Execution/Risk 概念层           | 与 Nautilus 的 Kernel+MessageBus+Engines 对应，复用现有消息与入口 |
| 首期只做斐波那契回撤 + 触线检测，不做实盘下单与风控引擎                                                 | 降低范围，验证数据流与事件流                                      |
| **斐波那契基于 swing 识别的一笔腿**（0%/100% 为趋势腿的起终点）                                     | 符合经典回撤画法，而非单纯区间极值                                   |
| 规划文件放在 timing/ 根目录                                                            | planning-with-files 约定规划文件在项目目录                     |
| **使用 uv 做依赖与虚拟环境**：pyproject.toml 在项目根(3sy11)，.venv 在根目录，.python-version=3.11 | 统一环境、可复现安装；后续 bollydog 等用 optional-dependencies     |


## Errors Encountered


| Error | Attempt | Resolution |
| ----- | ------- | ---------- |
| (暂无)  | -       | -          |


## 环境与依赖（uv）

- **位置**：`pyproject.toml`、`.python-version` 在项目根（3sy11）；虚拟环境为根目录下的 `.venv`。
- **常用命令**：`uv venv` 创建虚拟环境；`uv sync` 安装依赖并可编辑安装 timing；`uv run python timing/example_fib_touch.py` 运行示例。激活环境：`source .venv/bin/activate`。
- **依赖**：首期 `dependencies = []`（timing.data / timing.analysis 无第三方依赖）；后续接入 bollydog 可用 `[project.optional-dependencies]` 如 `bollydog = ["pydantic>=2", "mode", ...]`，安装时 `uv sync --extra bollydog`。

## Notes

- **Swing**：swing high = 某根 K 的 high 在左右各 N 根内为最高；swing low = 某根 K 的 low 在左右各 N 根内为最低。得到拐点序列后，选「一笔」：上涨腿 = 相邻 swing_low → swing_high，下跌腿 = swing_high → swing_low；可约定取「最近一笔」或按 direction 过滤。
- **斐波那契**：基于该笔的 leg_low、leg_high，回撤线 = leg_low + (leg_high - leg_low) * ratio；上涨腿回撤线在下方为支撑，下跌腿在上方为阻力。
- **触线**：当前价 last 与某条回撤线 level 满足 |last - level| <= tolerance，即视为触碰；可选去抖（同一 level 冷却时间内不重复发）。

