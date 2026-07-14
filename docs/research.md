# 服务架构设计 v3

> 六模块分层 + Parquet 追加写入 + 独立实验 ID

---

## 一、总体架构

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                                                                             │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐                               │
│  │  集成     │──→│  存储     │──→│  计算     │                               │
│  │Integration│   │ Storage  │   │Computation│                               │
│  └──────────┘   └──────────┘   └──────────┘                               │
│       │               │              │                                      │
│       ▼               ▼              ▼                                      │
│  ┌─────────────────────────────────────────────┐                           │
│  │         Parquet 表（数据资产）                 │                           │
│  │  klines/ │ indicators/ │ structures/ │ ...   │                           │
│  └───────────────────┬─────────────────────────┘                           │
│                       │ 读取                                                │
│  ┌────────────────────▼────────────────────────────────────────────┐       │
│  │                                                                  │       │
│  │  ┌──────────┐   ┌──────────┐   ┌──────────┐                    │       │
│  │  │  分析     │──→│  决策     │──→│  执行     │                    │       │
│  │  │ Analysis │   │ Decision │   │ Execution│                    │       │
│  │  └──────────┘   └──────────┘   └──────────┘                    │       │
│  │       │               │              │                          │       │
│  │       ▼               ▼              ▼                          │       │
│  │  signals.parquet decisions.parquet orders.parquet               │       │
│  │                                                                  │       │
│  └──────────────────────────────────────────────────────────────────┘       │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 二、六模块职责

| 模块 | 职责 | 输入 | 输出(Parquet) | 实验ID |
|------|------|------|--------------|--------|
| **集成** | 外部数据导入（parquet文件/API/推送） | 外部数据源 | `klines/` | 无 |
| **存储** | 小文件合并、分区、归档、TTL | 各模块的 parquet | 整理后的 parquet | 无 |
| **计算** | SQL/Python 批量加工数据 | klines | `indicators/`, `structures/` | `compute_id` |
| **分析** | 实时规则匹配，检测事件 | bar + structures | `signals/` | `analysis_id` |
| **决策** | 结合上下文做交易判断 | signals + positions | `decisions/` | `decision_id` |
| **执行** | 下单、成交、持仓管理 | decisions | `orders/`, `fills/`, `positions/` | `execution_id` |

### 计算 vs 分析 vs 决策 的边界

```
计算                        分析                         决策
─────                       ─────                        ─────
"数据长什么样？"             "刚才发生了什么？"            "我应该做什么？"

• 输入: 原始数据             • 输入: 实时 bar + 结构表     • 输入: signal + 持仓
• 输出: 派生表               • 输出: signal（事实）        • 输出: decision（意图）
• 无判断，纯数学             • 规则匹配（客观判断）        • 策略权衡（主观判断）
• 批量/定时/幂等             • 逐 bar，有冷却状态         • 逐 signal，有持仓状态
• 不知道"现在"              • 知道"现在发生了什么"        • 知道"我现在持有什么"
```

**具体例子**：

| 阶段 | 问的问题 | 回答 |
|------|---------|------|
| 计算 | "500根K线的 Fib 结构是什么？" | `levels: [(0.382, 1.03), (0.618, 0.98)]` |
| 分析 | "这根 bar close=1.031，碰到什么了？" | `signal: {direction=long, level=1.03, strength=0.85}` |
| 决策 | "收到这个信号，要不要买？" | `decision: {action=buy, qty=100, reason="strength>0.6 且无持仓"}` |

**代码归属**：

| 现有代码 | 归属 | 原因 |
|---------|------|------|
| `algo.py: compute_retracement()` | 计算服务 | 纯数学加工，产出结构表 |
| `algo.py: tag_pivots/zigzag/regression` | 计算服务 | 特征工程，无判断 |
| `touch.py: compute_consensus_strength()` | 分析服务 | 规则匹配："是否触碰" |
| `touch.py: check_breakout()` | 分析服务 | 规则匹配："是否突破" |
| `touch.py: score_bar_signals()` | 分析服务 | 多维评分打分 |
| `FibStrategy: strength > threshold` | 决策服务 | 策略判断："值不值得" |
| `FibStrategy: 计算 qty/side` | 决策服务 | 仓位管理 |

---

## 三、Parquet 追加写入模式

### 3.1 写入规则

- 每个模块管理自己的 Parquet 目录
- **追加写入**：每次产出新文件，不修改已有文件
- 文件命名：`{table}/{exp_id}/{timestamp}.parquet` 或 `{table}/{exp_id}/part-{seq}.parquet`
- 读取时用 DuckDB `read_parquet('path/**/*.parquet')` glob 读取

### 3.2 目录结构与命名规范

**命名规则**（参考业界 ODS/DWD/DWS/ADS 分层，简化适配）：

| 前缀 | 含义 | 可读性 |
|------|------|--------|
| `step{N}_` | 中间计算结果，N 表示步骤顺序 | 一看就知道是第几步、什么内容 |
| `result_` | 最终投产表，下游服务直接消费 | 一看就知道是可用的最终数据 |
| 无前缀 | 原始数据或公共衍生数据 | klines、indicators |

**文件命名格式**：`{前缀}{描述}_{symbol}_{interval}.parquet`

```
warehouse/timing/
│
├── klines/                                          # 集成产出 — 原始数据（无实验ID）
│   ├── 159363.OF/1d/
│   │   └── 159363.OF.parquet
│   └── 510050.SH/1d/
│       └── ...
│
├── indicators/                                      # 计算产出 — 公共指标（无实验ID）
│   ├── 159363.OF/1d/
│   │   └── data.parquet
│   └── ...
│
├── computation/                                      # 计算产出 — computation 前缀统一归类
│   └── fib_retracement/                             #   算法名
│       ├── {compute_id}/                            #     实验ID 子目录
│       │   ├── step1_pivots_159363.OF_1d.parquet   #     中间表: 拐点标记
│       │   ├── step2_confidence_159363.OF_1d.parquet#     中间表: 置信度
│       │   ├── step3_clusters_159363.OF_1d.parquet #     中间表: 价格聚类
│       │   ├── step4_legs_159363.OF_1d.parquet     #     中间表: 趋势腿
│       │   └── result_159363.OF_1d.parquet         #     投产表: Fib levels
│       └── {compute_id_2}/
│           └── ...
│
├── signals/                                         # 分析产出 — 实验数据
│   └── {analysis_id}/
│       └── part-{ts}.parquet
│
├── decisions/                                       # 决策产出 — 实验数据
│   └── {decision_id}/
│       └── part-{ts}.parquet
│
├── orders/                                          # 执行产出 — 实验数据
│   └── {execution_id}/
│       └── part-{ts}.parquet
│
├── fills/
│   └── {execution_id}/
│       └── part-{ts}.parquet
│
└── positions/
    └── {execution_id}/
        └── part-{ts}.parquet
```

**分析模块读取计算结果的方式**：

```python
# 分析服务配置中指定算法名 + 实验ID
algo = "fib_retracement"
compute_id = "base_v1"
# 读取投产表 — computation/ 前缀 + 算法名 + 实验ID
path = f"warehouse/timing/computation/{algo}/{compute_id}/result_{symbol}_{interval}.parquet"
structure = duckdb.sql(f"SELECT * FROM read_parquet('{path}')").fetchone()
```

分析和计算之间**纯数据交互**：只传递表的路径索引（算法名 + compute_id + symbol + interval）。

### 3.3 主键设计

每张表的主键 = `实验ID + 时间戳`（+ 业务键）

| 表 | 主键 | 实验ID | 路径格式 |
|----|------|--------|---------|
| klines | `symbol, interval, ts` | 无 | `klines/{symbol}/{interval}/` |
| indicators | `symbol, interval, ts` | 无 | `indicators/{symbol}/{interval}/` |
| step{N}_xxx | `compute_id, symbol, interval, ts` | `compute_id` | `computation/{algo}/{compute_id}/step{N}_xxx_{sym}_{iv}.parquet` |
| result | `compute_id, symbol, interval, ts` | `compute_id` | `computation/{algo}/{compute_id}/result_{sym}_{iv}.parquet` |
| signals | `analysis_id, symbol, ts` | `analysis_id` | `signals/{analysis_id}/part-{ts}.parquet` |
| decisions | `decision_id, symbol, ts` | `decision_id` | `decisions/{decision_id}/part-{ts}.parquet` |
| orders | `execution_id, order_id, ts` | `execution_id` | `orders/{execution_id}/part-{ts}.parquet` |
| fills | `execution_id, order_id, ts` | `execution_id` | `fills/{execution_id}/part-{ts}.parquet` |
| positions | `execution_id, symbol, ts` | `execution_id` | `positions/{execution_id}/part-{ts}.parquet` |

### 3.4 追溯链字段

每个下游表额外记录上游实验 ID，形成可追溯链路：

| 表 | 自身ID | 追溯字段 | 含义 |
|----|--------|---------|------|
| result（投产表） | `compute_id` | — | 顶层，路径含算法名 |
| signals | `analysis_id` | `algo + compute_id` | 用了哪个算法的哪组结果 |
| decisions | `decision_id` | `analysis_id` | 消费了哪组 signals |
| orders | `execution_id` | `decision_id` | 执行了哪组 decisions |

任何一笔成交可一路追溯：`execution_id → decision_id → analysis_id → compute_id`

### 3.5 公共数据 vs 实验数据

| 类别 | 特征 | 表 | 是否需要实验ID |
|------|------|---|--------------|
| **公共数据** | 固定参数、确定性、所有实验共享 | klines, indicators | ❌ |
| **实验数据** | 参数可调、不同配置产出不同结果 | structures, signals, decisions, orders, fills, positions | ✅ |

indicators（SMA(5), RSI(14), ATR(14) 等）是 klines 的确定性扩展——相同输入必定相同输出，没有"实验"概念。只有当算法参数不同时（如 structure 的 pivot_windows）才需要实验 ID 区分。

---

## 四、各模块详细设计

### 4.1 集成（Integration）

```
职责：将外部数据标准化为 klines Parquet
触发：手动导入 / API 采集 / 实时推送
产出：warehouse/klines/{symbol}/{interval}/{file}.parquet
```

- 不需要实验 ID（原始数据全局唯一）
- 追加写入：新数据追加新文件，不覆盖历史
- 去重：读取时由 DuckDB `DISTINCT ON` 处理

### 4.2 存储（Storage）

```
职责：物理文件管理（合并、归档、清理）
触发：定时 / 文件数阈值 / 手动
产出：整理后的 Parquet（原地合并或归档目录）
```

- 小文件合并：将 `signals/{id}/part-*.parquet` 合并为单文件
- TTL：超过 N 天的回测实验数据可归档/删除
- 分区策略：按 symbol + 时间范围分区

### 4.3 计算（Computation）

```
职责：从 klines 加工出 indicators 和算法结果（含中间表）
触发：数据导入后 / 定时 / 手动 / 突破事件
产出：warehouse/timing/indicators/  warehouse/timing/computation/{algo_name}/{compute_id}/
实验：compute_id 标识一组计算参数
```

**统一命令**：

```bash
python main.py execute Compute --algo fib_retracement --compute_id base_v1 \
    --symbol 159363.OF --interval 1d
```

**两种计算类型**：

| 类型 | 引擎 | 产出目录 | 示例 |
|------|------|---------|------|
| SQL | DuckDB + talib | `indicators/{symbol}/{interval}/` | `ta_sma`, `ta_rsi`（占位） |
| Python | 算法管道 | `computation/{algo_name}/{compute_id}/` | `fib_retracement` |

**算法注册**：每个算法在 `computation/algo/` 下有自己的子目录，通过 `--algo` 参数路由。

**fib_retracement 管道**：

| 步骤 | 文件名 | 内容 | 依赖 |
|------|--------|------|------|
| step1 | `step1_pivots_{sym}_{iv}.parquet` | swing 拐点 + zigzag + regression 标记 | klines |
| step2 | `step2_confidence_{sym}_{iv}.parquet` | 多方法融合置信度打分 | step1 |
| step3 | `step3_clusters_{sym}_{iv}.parquet` | 价格聚类中心 | step2 |
| step4 | `step4_legs_{sym}_{iv}.parquet` | 趋势腿提取 + 排名 | step2, step3 |
| result | `result_{sym}_{iv}.parquet` | 最终 Fib levels（投产表） | step4 |

**投产表 schema（result）**：

| 列 | 类型 | 说明 |
|----|------|------|
| compute_id | VARCHAR | 实验ID |
| symbol | VARCHAR | 品种 |
| interval | VARCHAR | 周期 |
| ts | BIGINT | 最后一根 bar 的时间戳 |
| groups_json | VARCHAR | FibGroup 列表 JSON |

**中间表用途**：
- 对比不同 `compute_id` 的中间结果，判断哪组参数更优
- Grafana 可视化中间过程（pivots 分布、clusters 位置等）
- 调试算法时逐步检查

### 4.4 分析（Analysis）

```
职责：接收 bar → 读取投产表 → detect 信号
触发：新 bar 到达（实时）/ 回测批量
产出：warehouse/timing/signals/{analysis_id}/
实验：analysis_id 标识检测参数 + 依赖的 algo + compute_id
```

**核心纯函数**：

```python
def detect(bar: dict, structure: list[FibGroup], state: DetectState, cfg: DetectConfig) -> DetectResult:
    """纯函数：bar + 预计算结构 → 信号列表"""
```

**分析配置（通过表索引指向计算结果）**：

```toml
[analysis.retracement_0]
analysis_id = "ret_v1_tight"
algo = "fib_retracement"         # 算法名 → 目录名
compute_id = "base_v1"           # 实验ID → 子目录名
symbol = "159363.OF"
interval = "1d"
# 实际读取路径: warehouse/timing/computation/fib_retracement/base_v1/result_159363.OF_1d.parquet
config = { touch_tolerance = 0.3, cooldown_bars = 3 }

[analysis.retracement_1]
analysis_id = "ret_v1_loose"
algo = "fib_retracement"
compute_id = "base_v1"
symbol = "159363.OF"
interval = "1d"
config = { touch_tolerance = 0.8, cooldown_bars = 5 }
```

**输出格式 — signals**：

| analysis_id | algo | compute_id | symbol | ts | direction | strength | price | level | metadata |
|-------------|------|------------|--------|-----|-----------|----------|-------|-------|----------|
| ret_v1_tight | fib_retracement | base_v1 | 159363.OF | 1711152000 | long | 0.85 | 1.05 | 1.03 | {...} |

### 4.5 决策（Decision）

```
职责：接收 signal → 结合策略参数 → 产出决策
触发：signal 事件（实时）/ 批量回测
产出：warehouse/decisions/{decision_id}/
实验：decision_id 标识策略参数 + 依赖的 analysis_id
```

**核心纯函数**：

```python
def decide(signal: dict, position: dict | None, cfg: StrategyConfig) -> Decision:
    """纯函数：信号 + 持仓 → 交易决定"""
```

**决策配置**：

```toml
[decision.fib_0]
decision_id = "fib_aggressive"
analysis_id = "ret_v1_tight"     # 消费哪个分析实验的 signals
min_strength = 0.5
position_size = 0.2

[decision.fib_1]
decision_id = "fib_conservative"
analysis_id = "ret_v1_loose"
min_strength = 0.7
position_size = 0.1
```

**输出格式 — decisions**：

| decision_id | analysis_id | symbol | ts | action | side | quantity | reason |
|-------------|-------------|--------|-----|--------|------|----------|--------|
| fib_aggressive | ret_v1_tight | 159363.OF | 1711152000 | submit | buy | 0.2 | strength=0.85>0.5 |

### 4.6 执行（Execution）

```
职责：接收 decision → 下单 → 成交 → 更新持仓
触发：decision 事件 / 批量回测
产出：warehouse/orders/ warehouse/fills/ warehouse/positions/ （各自 {execution_id}/）
实验：execution_id 标识执行参数 + 依赖的 decision_id
```

**执行配置**：

```toml
[execution.sim_0]
execution_id = "sim_run_001"
decision_id = "fib_aggressive"    # 消费哪个决策实验的 decisions
exchange = "sim"
commission_rate = 0.001
slippage = 0.0005
```

---

## 五、实验 ID 链路追踪

每个下游服务记录它依赖的上游实验 ID，形成 DAG：

```
compute_id: "base_v1"
    │
    ├──→ analysis_id: "ret_v1_tight"
    │        │
    │        ├──→ decision_id: "fib_aggressive"
    │        │        │
    │        │        └──→ execution_id: "sim_run_001"
    │        │
    │        └──→ decision_id: "fib_conservative"
    │                 │
    │                 └──→ execution_id: "sim_run_002"
    │
    └──→ analysis_id: "ret_v1_loose"
             └──→ ...
```

**一次完整实验 = 一条 ID 链路**：`compute_id → analysis_id → decision_id → execution_id`

切换"生产配置" = 修改当前激活的 ID 链路。

---

## 六、Parquet 追加写入的具体机制

### 6.1 写入

```python
import pyarrow as pa
import pyarrow.parquet as pq

def append_to_parquet(table_dir: str, exp_id: str, rows: list[dict], schema: pa.Schema):
    """追加写入：每次调用生成一个新 parquet 文件"""
    dir_path = f"warehouse/{table_dir}/{exp_id}"
    os.makedirs(dir_path, exist_ok=True)
    filename = f"{dir_path}/{int(time.time() * 1000)}.parquet"
    table = pa.Table.from_pylist(rows, schema=schema)
    pq.write_table(table, filename)
```

### 6.2 读取

```python
import duckdb

def read_table(table_dir: str, exp_id: str, where: str = "") -> list[dict]:
    """读取某个实验 ID 下的所有数据"""
    path = f"warehouse/{table_dir}/{exp_id}/**/*.parquet"
    sql = f"SELECT * FROM read_parquet('{path}') {where} ORDER BY ts"
    return duckdb.sql(sql).fetchdf().to_dict("records")

def read_latest_structure(compute_id: str, symbol: str) -> dict:
    """读取最新的 structure"""
    path = f"warehouse/structures/{compute_id}/**/*.parquet"
    sql = f"""
        SELECT * FROM read_parquet('{path}')
        WHERE symbol = '{symbol}'
        ORDER BY ts DESC LIMIT 1
    """
    return duckdb.sql(sql).fetchone()
```

### 6.3 并发安全

- **写入**：每次写新文件，不修改旧文件 → 无锁
- **读取**：DuckDB `read_parquet` 对 glob 是快照读 → 不会读到写了一半的文件
- **多进程回测**：每个进程写自己的 `{exp_id}/` 目录 → 完全隔离，无冲突

### 6.4 小文件问题

长时间运行会产生大量小文件。由存储模块定期合并：

```python
def compact(table_dir: str, exp_id: str):
    """合并小文件为单个大文件"""
    path = f"warehouse/{table_dir}/{exp_id}"
    df = duckdb.sql(f"SELECT * FROM read_parquet('{path}/**/*.parquet') ORDER BY ts").fetchdf()
    # 写入临时文件
    tmp = f"{path}/_compacted.parquet"
    df.to_parquet(tmp)
    # 删除旧文件，重命名临时文件
    for f in glob(f"{path}/*.parquet"):
        if f != tmp: os.remove(f)
    os.rename(tmp, f"{path}/data.parquet")
```

---

## 七、实时链路与回测链路对比

### 7.1 实时（生产）

```
新 bar 到达
    │
    ├─→ [集成] 追加写入 klines/{symbol}/{interval}/
    │
    ├─→ [计算] 增量: indicators 追加一行（SQL）
    │         结构: 通常不重算（除非收到突破信号）
    │
    └─→ [分析] 从内存读 structure + detect(bar) → 追加 signals
         │
         └─→ [决策] decide(signal) → 追加 decisions
              │
              └─→ [执行] execute(decision) → 追加 orders/fills/positions
```

### 7.2 回测

```
配置: compute_id=X, analysis_id=Y, decision_id=Z, execution_id=W

Step 1: [计算] 全量 klines → indicators + structures（写入 X 目录）
Step 2: [分析] 逐 bar detect(bar, structure_from_X) → signals（写入 Y 目录）
Step 3: [决策] 逐 signal decide() → decisions（写入 Z 目录）
Step 4: [执行] 逐 decision execute() → orders/fills（写入 W 目录）
```

**同一组纯函数**，不同的是：
- 实时：逐条触发，增量追加
- 回测：批量跑完，一次写入

### 7.3 Notebook / 研究

```python
from timing.algo.detect import detect
from timing.algo.strategy import decide
from timing.pipeline.runner import run_backtest

# 读取已有计算结果
structures = read_table("structures", "base_v1", "WHERE symbol='159363.OF'")

# 跑自定义分析
for bar in klines:
    result = detect(bar, structures[-1], state, my_config)
    # 直接看结果，不写文件
```

---

## 八、问题分析

### 8.1 Parquet 不支持更新（如 order 状态变更）

**问题**：Order 有状态变迁（pending → filled / cancelled），Parquet 追加写入意味着同一个 order 会有多行。

**方案：事件溯源（A）**

每次状态变更追加一行，带 `event_type` 列：

| execution_id | order_id | ts | event_type | status | filled_price | ... |
|---|---|---|---|---|---|---|
| sim_001 | ord_001 | 1711152000 | created | pending | - | ... |
| sim_001 | ord_001 | 1711152005 | filled | filled | 1.051 | ... |

查询当前状态：`WHERE order_id=X ORDER BY ts DESC LIMIT 1`

好处：完整审计轨迹，知道订单从创建到终态经历了什么。

### 8.2 positions 的追加式表示

**问题**：持仓是随时变化的状态。

**方案**：每次持仓变动追加一行快照：

| execution_id | symbol | ts | side | quantity | avg_price | realized_pnl | event |
|---|---|---|---|---|---|---|---|
| sim_001 | 159363.OF | 1711152000 | long | 100 | 1.05 | 0 | open |
| sim_001 | 159363.OF | 1711238400 | long | 0 | 0 | 0.03 | close |

查询当前持仓 = `WHERE execution_id=X ORDER BY ts DESC LIMIT 1 PER symbol`

### 8.3 依赖链的版本一致性

**问题**：如果分析服务正在逐 bar detect，此时有人触发了 structure 重新计算，分析服务前半段用旧结构、后半段用新结构，结果是否一致？

**不存在此问题**：

1. 同一个 `compute_id` 约定**只写一次**（参数确定 → 结果确定）
2. 想改参数就创建新的 `compute_id`（如 `base_v2`）
3. 分析服务配置中绑定了 `compute_id`，读取的永远是那一份

如果发生"突破后要用新结构"的情况：
- 触发新的计算任务，产出新 `compute_id`
- 分析服务检测到突破后，切换读取新的 `compute_id`（配置热更新）
- 或者直接创建新的 `analysis_id` 用新 structure 重跑

### 8.4 小文件数量

**问题**：实时场景每个 bar 一个 parquet 文件，一天 250 根 bar × 6 个品种 = 1500 个文件/天。

**应对**：
- 短期：内存攒 batch（如 100 条 / 5 分钟），一次写一个文件
- 中期：存储模块定时 compact
- 实际上日频场景文件量极小（每天 1-6 条信号），不是问题

### 8.5 structure 读取策略：不缓存，每次实时读取

**方案**：分析服务**不在启动时缓存** structure，而是每次 detect 时直接从 Parquet 读取最新结构。

```python
# 每次 detect 前读一次（不缓存）
def load_structure(compute_id: str, symbol: str):
    return duckdb.sql(f"""
        SELECT groups_json FROM read_parquet('warehouse/structures/{compute_id}/*.parquet')
        WHERE symbol='{symbol}' ORDER BY ts DESC LIMIT 1
    """).fetchone()
```

**好处**：
- structure 一旦被重新计算，下一个 bar 立即用到新版本
- 不存在缓存过期问题
- 逻辑简单，无需更新通知机制

**性能**：DuckDB 读单个 Parquet 取最新一行 ≈ 1-5ms，日频场景无压力。

**如果 structure 不存在**：分析服务返回空信号（不产出）。需要先确保计算模块跑过一次。

### 8.6 跨实验对比

**优势**：因为每个实验 ID 独立目录，Grafana 可以轻松做对比：

```sql
SELECT analysis_id, COUNT(*) as signal_count,
       AVG(strength) as avg_strength
FROM read_parquet('warehouse/signals/**/*.parquet')
GROUP BY analysis_id
```

---

---

## 十、目录结构

> 规则：service 是一个模块（目录），command 必须绑定在 service 上

```
timing/
│
├── integration/                 # ══ 集成服务 ══
│   ├── __init__.py
│   ├── app.py                  # IntegrationService(AppService)
│   │                           #   alias = "IntegrationService"
│   │                           #   commands = ["timing.integration.command"]
│   ├── command.py              # ImportKlines, PushBars — 绑定在 IntegrationService 上
│   └── importers.py            # 各种数据源适配（parquet/API/CSV）
│
├── storage/                     # ══ 存储服务 ══
│   ├── __init__.py
│   ├── app.py                  # StorageService(AppService)
│   │                           #   alias = "StorageService"
│   │                           #   commands = ["timing.storage.command"]
│   └── command.py              # Compact, Archive, Cleanup — 绑定在 StorageService 上
│
├── computation/                 # ══ 计算服务 ══（回答：数据长什么样？）
│   ├── __init__.py
│   ├── app.py                  # ComputationService(AppService)
│   │                           #   alias = "ComputationService"
│   │                           #   commands = ["timing.computation.command"]
│   ├── command.py              # Compute(algo, compute_id, symbol, interval)
│   ├── writer.py               # 统一中间表/结果表 Parquet 写入工具
│   └── algo/                   # 算法目录（每个算法一个子目录）
│       ├── __init__.py
│       ├── registry.py         # ALGO_REGISTRY: {"fib_retracement": pipeline}
│       └── fib_retracement/    # ── fib 回撤算法 ──
│           ├── __init__.py
│           ├── algo.py         # 纯函数: tag_pivots, zigzag, regression, ...
│           ├── config.py       # FibRetracementConfig
│           ├── models.py       # TrendLeg, FibGroup
│           └── pipeline.py     # 管道编排: step1→step2→step3→step4→result
│
├── analysis/                    # ══ 分析服务 ══（回答：刚才发生了什么？）
│   ├── __init__.py
│   ├── app.py                  # AnalysisService(AppService) — 编排层
│   │                           #   alias = "AnalysisService"
│   │                           #   commands = ["timing.analysis.command"]
│   │                           #   on_bar → 读 structure → 规则匹配 → 写 signals
│   ├── command.py              # RerunDetect 等手动命令
│   └── rules/                  # 检测规则（纯函数，判断事实）
│       ├── __init__.py
│       ├── touch.py            # 触碰检测：price 靠近 level → signal
│       ├── breakout.py         # 突破检测：price 穿越 structure → signal
│       └── ...                 # 未来可扩展：均线交叉、RSI阈值等
│
├── decision/                    # ══ 决策服务 ══（回答：我应该做什么？）
│   ├── __init__.py
│   ├── app.py                  # DecisionService(AppService) — 编排层
│   │                           #   alias = "DecisionService"
│   │                           #   commands = ["timing.decision.command"]
│   │                           #   on_signal → 结合持仓 → decide → 写 decisions
│   ├── command.py              # 手动命令
│   └── strategies/             # 策略函数（纯函数，判断意图）
│       ├── __init__.py
│       └── fib.py              # decide(signal, position, cfg) → Decision
│
├── exchange/                    # ══ 交易所服务（独立模块）══
│   ├── __init__.py
│   ├── app.py                  # ExchangeService(AppService) — 交易所接口
│   │                           #   alias = "ExchangeService"
│   │                           #   提供 submit_order / check_pending / cancel_order
│   └── mock.py                 # SimExchange — 模拟撮合引擎（默认交易所）
│
├── execution/                   # ══ 执行服务 ══
│   ├── __init__.py
│   ├── app.py                  # ExecutionService(AppService) — 编排层
│   │                           #   alias = "ExecutionService"
│   │                           #   commands = ["timing.execution.command"]
│   ├── command.py              # Execute(execution_id, decision_id, exchange)
│   ├── runner.py               # run_execution(decisions, klines, exchange) 纯函数
│   └── writer.py               # ExecutionWriter → execution/{id}/*.parquet
│
├── common/                      # ══ 公共工具（非服务）══
│   ├── __init__.py
│   ├── parquet_io.py           # 统一 Parquet 读写接口
│   └── clock.py                # LiveClock / SimulatedClock
│
├── pipeline/                    # ══ 回测管道（非服务）══
│   ├── __init__.py
│   └── runner.py               # run_backtest() — 调用各层纯函数
│
├── warehouse/                   # ══ 数据资产目录（.gitignore）══
│   ├── klines/
│   ├── indicators/
│   ├── structures/{compute_id}/
│   ├── signals/{analysis_id}/
│   ├── decisions/{decision_id}/
│   └── execution/{execution_id}/
│       ├── orders.parquet
│       ├── fills.parquet
│       ├── positions.parquet
│       └── manifest.json
│
└── config.toml                  # 主配置（声明服务 + 实验参数）
```

### 服务与 Command 的绑定关系

| 服务 | 绑定的 Commands | 触发方式 |
|------|----------------|---------|
| IntegrationService | `ImportKlines`, `PushBars` | CLI execute / API |
| StorageService | `Compact`, `Archive`, `Cleanup` | CLI execute / 定时 |
| ComputationService | `ComputeIndicators`, `ComputeStructure` | CLI execute / 事件 |
| AnalysisService | `RerunDetect` | CLI execute（手动重跑） |
| DecisionService | (暂无) | — |
| ExchangeService | — | （提供撮合接口，无 CLI 命令） |
| ExecutionService | `Execute` | CLI execute |

### 服务运行模式

| 服务 | service 模式（长驻） | execute 模式（一次性） |
|------|-------|---------|
| IntegrationService | 监听推送、接收 API | 导入历史数据 |
| StorageService | 定时巡检文件 | 手动触发合并 |
| ComputationService | 监听数据变更自动算 | 手动触发计算 |
| AnalysisService | 监听 bar 事件 → detect | — |
| DecisionService | 监听 signal 事件 → decide | — |
| ExchangeService | 常驻提供撮合接口 | — |
| ExecutionService | 监听 decision 事件 → execute | 手动触发回测 |

### config.toml 示例

```toml
[services.integration]
module = "timing.integration.app.IntegrationService"
warehouse_path = "warehouse"

[services.storage]
module = "timing.storage.app.StorageService"
compact_threshold = 100  # 文件数超过此值触发合并

[services.computation]
module = "timing.computation.app.ComputationService"
compute_id = "base_v1"
symbols = ["159363.OF", "510050.SH"]
interval = "1d"

[services.computation.indicators]
sma_5 = { fn = "ta_sma", args = ["close", 5] }
sma_20 = { fn = "ta_sma", args = ["close", 20] }
rsi_14 = { fn = "ta_rsi", args = ["close", 14] }

[services.computation.structure]
fn = "timing.analysis.algo.retracement.algo.compute_retracement"
config = { pivot_windows = [[5,5],[8,8]], zigzag_thresholds = [0.05, 0.10] }

[services.analysis]
module = "timing.analysis.app.AnalysisService"
analysis_id = "ret_v1"
compute_id = "base_v1"
symbol = "159363.OF"
interval = "1d"
config = { touch_tolerance = 0.5, cooldown_bars = 5 }

[services.decision]
module = "timing.decision.app.DecisionService"
decision_id = "fib_v1"
analysis_id = "ret_v1"
min_strength = 0.6
position_size = 0.1

[services.exchange]
module = "timing.exchange.app.ExchangeService"
initial_balance = 100_000.0
slippage_pct = 0.001
commission_rate = 0.001

[services.execution]
module = "timing.execution.app.ExecutionService"
execution_id = "sim_001"
decision_id = "fib_v1"
exchange = "sim"
```

---

## 十一、执行计划

| 步骤 | 内容 | 依赖 |
|------|------|------|
| 1 | 实现 `common/parquet_io.py`（统一读写接口） | 无 |
| 2 | 提取 `analysis/algo/detect.py` 纯函数 | 无 |
| 3 | 提取 `decision/strategies/fib.py` 纯函数 | 无 |
| 4 | 实现 `computation/indicators.py`（SQL 模板） | 步骤 1 |
| 5 | 实现 `computation/structure.py`（调用 algo） | 步骤 1 |
| 6 | 实现 `pipeline/runner.py`（回测全链路） | 步骤 2,3,4,5 |
| 7 | 重写 `analysis/app.py`（瘦身为纯编排） | 步骤 2 |
| 8 | 重写 `decision/app.py`（瘦身为纯编排） | 步骤 3 |
| 9 | 验证：回测 pipeline 对比旧结果 | 步骤 6 |
| 10 | 实现 `storage/compact.py` | 步骤 1 |
