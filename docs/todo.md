# timing 可视化 + 数据架构重构 TODO

> 方案：**单一 DuckDB + talib + Grafana + Cube 兼容 YAML 驱动**
> 核心变化：
> - 所有数据统一到一个 DuckDB 文件
> - YAML 采用 Cube 兼容格式（cubes/measures/dimensions），未来可直接接 Cube Server
> - klines/indicators 为共享原始数据（无 run_id）；分析/策略/执行产出带 run_id
> - DuckDBProtocol 继承 bollydog 的 `DuckDBProtocol(CRUDProtocol)` 扩展 Schema 能力
> - Grafana 直连 DuckDB 画图

---

## 数据库统一方案

**现状 → 目标：**
```
现状（5 个文件，不利于分析）:             目标（单 DuckDB，一个数据源全覆盖）:
├── klines.duckdb                        warehouse/timing/timing.duckdb
├── retracementservice.sqlite              ├── runs         (运行元数据)
├── fib_strategy.sqlite                    ├── klines       (K 线)
├── execution_broker.sqlite                ├── indicators   (预计算指标)
└── dashboard.sqlite                       ├── checkpoints  (分析进度)
                                           ├── signals      (信号)
                                           ├── touches      (触碰)
                                           ├── analysis     (分析结果: retracement/...)
                                           ├── decisions    (策略决策)
                                           ├── orders       (订单)
                                           ├── fills        (成交)
                                           └── positions    (持仓)
```

**run_id 设计：**
```
不带 run_id（客观事实，所有 run 共享）:
  klines       ← 原始 K 线，导入一次全局共用
  indicators   ← 从 klines 计算的技术指标，参数固定时全局共用

带 run_id（每次运行的独立产出）:
  signals, decisions, orders, fills, positions, checkpoints, analysis

  run_id = "live_20260602_1"    ← 当前生产运行
  run_id = "bt_a3f2c1"         ← 一次回测
  run_id = "bt_7d9e2f"         ← 另一次回测

"上线" = 把配置中 active_run_id 指向某个 run_id
"回测" = 批量生成多组 run_id 的数据
"对比" = Grafana 选两个 run_id 叠加
```

---

## 数据流全景

```
parquet
  │ [IngestKlinesFromFile]
  ▼
klines (symbol, interval, ts, open, high, low, close, volume)  ← 无 run_id，全局共享
  │ [ComputeIndicators]
  ▼
indicators (symbol, interval, ts, sma_20, rsi_14, macd ...)    ← 无 run_id，全局共享
  │ [RunBacktest / 生产 on_bar]
  ▼
signals → decisions → orders → fills → positions
  (全部带 run_id, 同一张表, 同一个库)
  │
  ▼ [Grafana 直连 DuckDB]
Dashboard: 变量 $run_id, $symbol, $interval 筛选一切
```

---

## 一、Schema 注册表（YAML 唯一事实来源）

### 1.1 创建 `timing/schema/registry.yml`

- [ ] 定义所有表结构（字段、类型、主键）
- [ ] 定义指标计算规则（talib 函数映射）
- [ ] 定义聚合度量（Grafana/AI 用）

```yaml
# timing/schema/registry.yml
# 采用 Cube 兼容格式: cubes / dimensions / measures
# 扩展字段 x-storage (Cube 会忽略未知前缀) 存放 DDL 相关信息
# 未来可直接迁移为 Cube Server 数据模型

cubes:
  # ═══════════════════════════════════════════
  # 共享原始数据（无 run_id）
  # ═══════════════════════════════════════════

  - name: klines
    description: "K 线原始数据（全局共享，不含 run_id）"
    sql_table: klines
    x-storage:
      primary_key: [symbol, interval, ts]

    dimensions:
      - name: symbol
        sql: symbol
        type: string
      - name: interval
        sql: interval
        type: string
      - name: ts
        sql: ts
        type: number
      - name: open
        sql: open
        type: number
      - name: high
        sql: high
        type: number
      - name: low
        sql: low
        type: number
      - name: close
        sql: close
        type: number
      - name: volume
        sql: volume
        type: number

    measures:
      - name: avg_close
        sql: close
        type: avg
      - name: max_high
        sql: high
        type: max
      - name: min_low
        sql: low
        type: min
      - name: total_volume
        sql: volume
        type: sum

  - name: indicators
    description: "预计算技术指标（全局共享，不含 run_id）"
    sql_table: indicators
    x-storage:
      primary_key: [symbol, interval, ts]
      # 指标计算规则（talib）
      compute:
        window:
          sma_5: { fn: ta_sma, args: [close, 5] }
          sma_10: { fn: ta_sma, args: [close, 10] }
          sma_20: { fn: ta_sma, args: [close, 20] }
          sma_60: { fn: ta_sma, args: [close, 60] }
          ema_12: { fn: ta_ema, args: [close, 12] }
          ema_26: { fn: ta_ema, args: [close, 26] }
          rsi_14: { fn: ta_rsi, args: [close, 14] }
          atr_14: { fn: ta_atr, args: [high, low, close, 14] }
        multi_output:
          macd: { fn: t_macd, args: [close, 12, 26, 9], outputs: [macd, macd_signal, macd_hist] }
          bbands: { fn: t_bbands, args: [close, 20, 2.0, 2.0, 0], outputs: [bb_upper, bb_middle, bb_lower] }

    dimensions:
      - name: symbol
        sql: symbol
        type: string
      - name: interval
        sql: interval
        type: string
      - name: ts
        sql: ts
        type: number
      - name: sma_5
        sql: sma_5
        type: number
      - name: sma_10
        sql: sma_10
        type: number
      - name: sma_20
        sql: sma_20
        type: number
      - name: sma_60
        sql: sma_60
        type: number
      - name: ema_12
        sql: ema_12
        type: number
      - name: ema_26
        sql: ema_26
        type: number
      - name: rsi_14
        sql: rsi_14
        type: number
      - name: atr_14
        sql: atr_14
        type: number
      - name: macd
        sql: macd
        type: number
      - name: macd_signal
        sql: macd_signal
        type: number
      - name: macd_hist
        sql: macd_hist
        type: number
      - name: bb_upper
        sql: bb_upper
        type: number
      - name: bb_middle
        sql: bb_middle
        type: number
      - name: bb_lower
        sql: bb_lower
        type: number

  # ═══════════════════════════════════════════
  # 运行相关数据（带 run_id）
  # ═══════════════════════════════════════════

  - name: runs
    description: "运行元数据"
    sql_table: runs
    x-storage:
      primary_key: [run_id]

    dimensions:
      - name: run_id
        sql: run_id
        type: string
        primary_key: true
      - name: created_at
        sql: created_at
        type: number
      - name: status
        sql: status
        type: string   # running | completed | active | archived
      - name: mode
        sql: mode
        type: string   # live | backtest
      - name: description
        sql: description
        type: string
      - name: params
        sql: params
        type: string   # JSON

    measures:
      - name: count
        type: count

  - name: checkpoints
    description: "分析进度"
    sql_table: checkpoints
    x-storage:
      primary_key: [run_id, symbol, interval]

    dimensions:
      - name: run_id
        sql: run_id
        type: string
      - name: symbol
        sql: symbol
        type: string
      - name: interval
        sql: interval
        type: string
      - name: ts
        sql: ts
        type: number

  - name: signals
    description: "分析信号"
    sql_table: signals
    x-storage:
      primary_key: [run_id, symbol, interval, ts, source]

    dimensions:
      - name: run_id
        sql: run_id
        type: string
      - name: symbol
        sql: symbol
        type: string
      - name: interval
        sql: interval
        type: string
      - name: ts
        sql: ts
        type: number
      - name: direction
        sql: direction
        type: string
      - name: strength
        sql: strength
        type: number
      - name: price
        sql: price
        type: number
      - name: source
        sql: source
        type: string
      - name: level
        sql: level
        type: number
      - name: metadata
        sql: metadata
        type: string   # JSON

    measures:
      - name: count
        type: count

  - name: touches
    description: "触碰记录"
    sql_table: touches
    x-storage:
      primary_key: [run_id, symbol, interval, level_key]

    dimensions:
      - name: run_id
        sql: run_id
        type: string
      - name: symbol
        sql: symbol
        type: string
      - name: interval
        sql: interval
        type: string
      - name: level_key
        sql: level_key
        type: string
      - name: ts
        sql: ts
        type: number
      - name: touch_price
        sql: touch_price
        type: number
      - name: level_price
        sql: level_price
        type: number
      - name: direction
        sql: direction
        type: string
      - name: touch_count
        sql: touch_count
        type: number

  - name: analysis
    description: "分析结果（通用表，name 区分不同算法：retracement / elliott / harmonic ...）"
    sql_table: analysis
    x-storage:
      primary_key: [run_id, symbol, interval, name]

    dimensions:
      - name: run_id
        sql: run_id
        type: string
      - name: symbol
        sql: symbol
        type: string
      - name: interval
        sql: interval
        type: string
      - name: name
        sql: name
        type: string   # retracement | elliott | harmonic | ...
      - name: ts
        sql: ts
        type: number   # 最近一次计算时间
      - name: data
        sql: data
        type: string   # JSON blob: 各算法自定义结构

    measures:
      - name: count
        type: count

  - name: decisions
    description: "策略决策"
    sql_table: decisions
    x-storage:
      primary_key: [run_id, symbol, ts]

    dimensions:
      - name: run_id
        sql: run_id
        type: string
      - name: symbol
        sql: symbol
        type: string
      - name: ts
        sql: ts
        type: number
      - name: direction
        sql: direction
        type: string
      - name: strength
        sql: strength
        type: number
      - name: price
        sql: price
        type: number
      - name: action
        sql: action
        type: string
      - name: reason
        sql: reason
        type: string

  - name: orders
    description: "订单"
    sql_table: orders
    x-storage:
      primary_key: [run_id, order_id]

    dimensions:
      - name: run_id
        sql: run_id
        type: string
      - name: order_id
        sql: order_id
        type: string
        primary_key: true
      - name: symbol
        sql: symbol
        type: string
      - name: side
        sql: side
        type: string
      - name: order_type
        sql: order_type
        type: string
      - name: quantity
        sql: quantity
        type: number
      - name: price
        sql: price
        type: number
      - name: stop_price
        sql: stop_price
        type: number
      - name: status
        sql: status
        type: string
      - name: fill_price
        sql: fill_price
        type: number
      - name: filled_quantity
        sql: filled_quantity
        type: number
      - name: commission
        sql: commission
        type: number
      - name: created_at
        sql: created_at
        type: number
      - name: filled_at
        sql: filled_at
        type: number

    measures:
      - name: count
        type: count
      - name: total_commission
        sql: commission
        type: sum

  - name: fills
    description: "成交记录"
    sql_table: fills
    x-storage:
      primary_key: [run_id, order_id, ts]

    dimensions:
      - name: run_id
        sql: run_id
        type: string
      - name: order_id
        sql: order_id
        type: string
      - name: symbol
        sql: symbol
        type: string
      - name: side
        sql: side
        type: string
      - name: filled_price
        sql: filled_price
        type: number
      - name: filled_quantity
        sql: filled_quantity
        type: number
      - name: commission
        sql: commission
        type: number
      - name: ts
        sql: ts
        type: number

  - name: positions
    description: "持仓"
    sql_table: positions
    x-storage:
      primary_key: [run_id, symbol]

    dimensions:
      - name: run_id
        sql: run_id
        type: string
      - name: symbol
        sql: symbol
        type: string
      - name: side
        sql: side
        type: string
      - name: quantity
        sql: quantity
        type: number
      - name: avg_entry_price
        sql: avg_entry_price
        type: number
      - name: realized_pnl
        sql: realized_pnl
        type: number

    measures:
      - name: win_rate
        sql: "CASE WHEN {CUBE}.realized_pnl > 0 THEN 1 ELSE 0 END"
        type: avg
      - name: total_pnl
        sql: realized_pnl
        type: sum
```

### 1.2 创建 `timing/schema/engine.py`

- [ ] 解析 Cube 兼容 YAML（cubes → dimensions/measures + x-storage）
- [ ] 从 YAML 生成 DDL（CREATE TABLE）
- [ ] 从 YAML 生成 INSERT OR REPLACE 语句模板
- [ ] 从 x-storage.compute 生成指标计算 SQL
- [ ] 提供 `get_columns(table)` / `get_primary_key(table)` 等查询方法

```python
# timing/schema/engine.py
import yaml, os

# Cube dimension type → DuckDB 列类型
_TYPE_MAP = {"string": "VARCHAR", "number": "DOUBLE", "time": "BIGINT", "boolean": "BOOLEAN"}

class SchemaEngine:
    def __init__(self, registry_path: str = None):
        path = registry_path or os.path.join(os.path.dirname(__file__), "registry.yml")
        with open(path) as f:
            raw = yaml.safe_load(f)
        # 建立 name → cube 映射
        self._cubes = {c["name"]: c for c in raw["cubes"]}

    def tables(self) -> list[str]:
        return list(self._cubes.keys())

    def cube(self, name: str) -> dict:
        return self._cubes[name]

    def columns(self, table: str) -> list[str]:
        return [d["name"] for d in self._cubes[table]["dimensions"]]

    def primary_key(self, table: str) -> list[str]:
        return self._cubes[table].get("x-storage", {}).get("primary_key", [])

    def ddl(self, table: str) -> str:
        c = self._cubes[table]
        cols = [f'"{d["name"]}" {_TYPE_MAP.get(d["type"], "VARCHAR")}' for d in c["dimensions"]]
        sql = f'CREATE TABLE IF NOT EXISTS {table} ({", ".join(cols)}'
        pk = self.primary_key(table)
        if pk:
            sql += f', PRIMARY KEY ({", ".join(pk)})'
        return sql + ")"

    def all_ddl(self) -> list[str]:
        return [self.ddl(t) for t in self.tables()]

    def insert_sql(self, table: str) -> str:
        cols = self.columns(table)
        col_str = ", ".join(f'"{c}"' for c in cols)
        placeholders = ", ".join(["?"] * len(cols))
        return f'INSERT OR REPLACE INTO {table} ({col_str}) VALUES ({placeholders})'

    def indicators_sql(self, symbol: str, interval: str) -> str:
        """生成完整的指标预计算 SQL（从 klines → indicators，无 run_id）"""
        compute = self._cubes["indicators"].get("x-storage", {}).get("compute", {})
        parts = ["symbol", '"interval"', "ts"]
        for col_name, spec in compute.get("window", {}).items():
            fn, args = spec["fn"], spec["args"]
            arg_str = ", ".join(str(a) for a in args)
            parts.append(f'{fn}({arg_str}) OVER (PARTITION BY symbol ORDER BY ts) AS {col_name}')
        for col_name, spec in compute.get("multi_output", {}).items():
            for out in spec["outputs"]:
                parts.append(f'NULL AS {out}')
        select = ", ".join(parts)
        return f"""INSERT OR REPLACE INTO indicators SELECT {select}
FROM klines WHERE symbol='{symbol}' AND "interval"='{interval}'"""

    def measure_sql(self, cube_name: str, measure_name: str, **filters) -> str:
        c = self._cubes[cube_name]
        m = next(m for m in c.get("measures", []) if m["name"] == measure_name)
        table = c["sql_table"]
        where_parts = [f"{k} = '{v}'" for k, v in filters.items()]
        where_str = f" WHERE {' AND '.join(where_parts)}" if where_parts else ""
        return f'SELECT {m["sql"]} AS value FROM {table}{where_str}'
```

### 1.3 模型处理（全部消除独立 Pydantic 模型）

**原则：数据结构由 YAML 定义，业务逻辑归属 Service，数据用 dict 流转。**

- [ ] 删除整个 `timing/models/` 目录中的数据模型类
- [ ] 业务逻辑迁移到对应 Service：
  - `Position.apply_fill()` → `Broker._apply_fill(position: dict, fill: dict) -> dict`
  - `Order.mark_filled()` → `Broker._mark_order_filled(order: dict, fill: dict) -> dict`
  - `SimExchange` — 保留为独立类（它是模拟引擎，不是数据模型）
- [ ] Event 类保留为纯消息载体（不入库），可用 dataclass 或 bollydog BaseEvent

最终 `timing/models/` 目录：
```
timing/models/
├── __init__.py
├── events.py       ← SignalEmitted / OrderFilled / OrderRejected (消息类, BaseEvent)
└── exchange.py     ← SimExchange (模拟撮合引擎，非数据模型)
```

**逻辑归属对照：**
```
旧: Position(BaseModel).apply_fill(fill)     → 新: Broker._apply_fill(pos_dict, fill_dict) -> dict
旧: Order(BaseModel).mark_filled(price, qty)  → 新: Broker._mark_order_filled(order_dict, ...) -> dict
旧: Kline(BaseModel).ddl()                    → 新: SchemaEngine.ddl("klines")
旧: Signal(BaseModel)                         → 新: 直接用 dict，结构由 YAML 定义
```

数据在系统中全程以 `dict` 流转，Service 方法接收 dict、处理后写入 DuckDB（也是 dict）。
Event 类只用于 Hub 事件分发，携带必要字段即可。

---

## 二、DuckDB 统一 Protocol

### 2.1 创建 `timing/adapters/duckdb.py`

- [ ] **继承 bollydog 的 `DuckDBProtocol`**（已有 on_start/on_stop/execute_raw/add/list 等基础能力）
- [ ] 扩展 Schema 驱动能力：自动建表、类型化 put/get/append
- [ ] API 兼容旧 `StructuredSQLiteProtocol`：`get() / put() / append() / delete() / all()`
- [ ] 单例共享：所有服务用同一个 Protocol 实例

```python
# timing/adapters/duckdb.py
import json, logging
from bollydog.adapters.sqlalchemy import DuckDBProtocol as BaseDuckDBProtocol
from timing.schema.engine import SchemaEngine

log = logging.getLogger(__name__)

class TimingDuckDBProtocol(BaseDuckDBProtocol):
    """继承 bollydog DuckDBProtocol，扩展 Schema 驱动能力。

    bollydog 基类提供:
      - on_start(): duckdb.connect + dialect 加载
      - on_stop(): close
      - execute_raw(sql): 裸 SQL
      - adapter: duckdb connection
      - _run(fn, *a): asyncio.to_thread 包装

    本类扩展:
      - Schema 驱动自动建表
      - 类型化 CRUD (put/get/append/delete/all)
      - talib 扩展加载
      - JSON 列自动编解码
    """

    def __init__(self, url: str, schema: SchemaEngine = None, **kwargs):
        super().__init__(url=url, **kwargs)
        self._schema = schema or SchemaEngine()

    async def on_start(self) -> None:
        await super().on_start()  # duckdb.connect + dialect
        self.adapter.execute("INSTALL talib FROM community")
        self.adapter.execute("LOAD talib")
        for ddl in self._schema.all_ddl():
            self.adapter.execute(ddl)
        log.info(f'[TimingDuckDB] 就绪: {self.url}, tables={self._schema.tables()}')

    @property
    def schema(self) -> SchemaEngine:
        return self._schema

    async def get(self, table: str = None, **where) -> dict | list[dict]:
        pk = self._schema.primary_key(table)
        cols = self._schema.columns(table)
        sql = f'SELECT * FROM {table}'
        params = []
        if where:
            conds = [f'"{k}"=?' for k in where]
            sql += f' WHERE {" AND ".join(conds)}'
            params = list(where.values())
        result = await self._run(lambda: self.adapter.execute(sql, params).fetchall())
        rows = [dict(zip(cols, r)) for r in result]
        for row in rows: self._decode_json(table, row)
        if pk and set(where.keys()) >= set(pk):
            return rows[0] if rows else None
        return rows

    async def put(self, table: str, data: dict, **_):
        cols = self._schema.columns(table)
        values = [self._encode(table, c, data.get(c)) for c in cols]
        col_str = ", ".join(f'"{c}"' for c in cols)
        placeholders = ", ".join(["?"] * len(cols))
        await self._run(lambda: self.adapter.execute(
            f'INSERT OR REPLACE INTO {table} ({col_str}) VALUES ({placeholders})', values))

    async def append(self, table: str, data):
        rows = data if isinstance(data, list) else [data]
        cols = self._schema.columns(table)
        col_str = ", ".join(f'"{c}"' for c in cols)
        placeholders = ", ".join(["?"] * len(cols))
        for row in rows:
            values = [self._encode(table, c, row.get(c)) for c in cols]
            await self._run(lambda v=values: self.adapter.execute(
                f'INSERT INTO {table} ({col_str}) VALUES ({placeholders})', v))

    async def delete(self, table: str = None, **where):
        if where:
            conds = [f'"{k}"=?' for k in where]
            await self._run(lambda: self.adapter.execute(
                f'DELETE FROM {table} WHERE {" AND ".join(conds)}', list(where.values())))
        else:
            await self._run(lambda: self.adapter.execute(f'DELETE FROM {table}'))

    async def all(self, table: str = None, **where) -> list[dict]:
        return await self.get(table=table, **where) if where else await self.get(table=table)

    def _encode(self, table: str, col: str, val):
        dims = {d["name"]: d for d in self._schema.cube(table)["dimensions"]}
        # JSON 列在 Cube 格式中 type=string 但字段名含 metadata/data/params
        if col in ("data", "metadata", "params") and not isinstance(val, str):
            return json.dumps(val, ensure_ascii=False) if val else None
        return val

    def _decode_json(self, table: str, row: dict):
        for col in ("data", "metadata", "params"):
            if col in row and isinstance(row[col], str):
                try: row[col] = json.loads(row[col])
                except: pass
```

### 2.2 修改各服务

- [ ] `DataEngine` — 不再自己管 DuckDB 连接，使用 `DuckDBProtocol.shared()`
- [ ] `AnalysisEngine` — 替换 `StructuredSQLiteProtocol` → `DuckDBProtocol.shared()`
- [ ] `FibStrategy` — 替换 `StructuredSQLiteProtocol` → `DuckDBProtocol.shared()`
- [ ] `Broker` — 替换 `StructuredSQLiteProtocol` → `DuckDBProtocol.shared()`
- [ ] 所有 `put()/append()` 调用加上 `run_id` 字段

---

## 三、run_id 机制

### 3.1 运行配置

- [ ] `timing/config.toml` 中增加

```toml
[run]
active_run_id = "live_default"
mode = "live"  # live | backtest
```

### 3.2 运行管理

- [ ] 回测开始时生成新 run_id，写入 runs 表
- [ ] 生产模式从配置读取 active_run_id
- [ ] 切换生产 = 修改 config 中的 active_run_id

### 3.3 Grafana 变量

- [ ] `$run_id` 变量查询：`SELECT run_id, description FROM runs ORDER BY created_at DESC`
- [ ] 所有面板 WHERE 条件加 `run_id = '${run_id}'`

---

## 四、talib 指标预计算

### 4.1 ComputeIndicators Command

- [ ] 新增 `ComputeIndicators` command
- [ ] 实现：读 SchemaEngine 的 `indicator_definitions` → 生成 SQL → 执行写入 indicators 表
- [ ] 窗口函数直接 INSERT SELECT
- [ ] 多输出函数（MACD/BBands）单独处理

### 4.2 串联到导入流程

- [ ] `IngestKlinesFromFile` 完成后自动触发 `ComputeIndicators`

---

## 五、Grafana

### 5.1 部署

- [ ] `timing/infra/docker-compose.yml` — Grafana + DuckDB 插件
- [ ] 数据源配置指向 `warehouse/timing/timing.duckdb`

### 5.2 Dashboard 面板

- [ ] K 线 Candlestick: `SELECT ts, open, high, low, close FROM klines WHERE run_id='${run_id}' AND symbol='${symbol}'`
- [ ] 指标叠加: `SELECT ts, sma_20, ema_12, bb_upper, bb_lower FROM indicators WHERE ...`
- [ ] RSI: `SELECT ts, rsi_14 FROM indicators WHERE ...`
- [ ] MACD: `SELECT ts, macd, macd_signal, macd_hist FROM indicators WHERE ...`
- [ ] 信号标注: `SELECT ts, direction, strength FROM signals WHERE run_id='${run_id}' AND ...`
- [ ] 权益曲线: 从 fills 计算累计 PnL
- [ ] 订单表: `SELECT * FROM orders WHERE run_id='${run_id}' AND symbol='${symbol}'`

### 5.3 多 run 对比面板

- [ ] 支持选多个 run_id，叠加权益曲线对比

---

## 六、删除清单

| 文件/目录 | 理由 |
|-----------|------|
| `timing/dashboard/` | 整个模块删除，Grafana 替代 |
| `timing/frontend/` | 删除，不再自建前端 |
| `timing/adapters/sqlite.py` | DuckDBProtocol 替代 |
| `timing/models/kline.py` 中 DDL 相关 | SchemaEngine 替代 |
| `timing/models/signal.py` | 纯字段定义，YAML 替代 |
| `timing/models/checkpoint.py` | YAML 替代 |
| `timing/models/account.py` | 内联到 Broker/SimExchange |
| `config.toml` 中 DashboardService 配置 | 删除 |

---

## 七、项目目录（改后）

```
timing/
├── schema/
│   ├── registry.yml        ← 唯一事实来源（表+指标+度量）
│   └── engine.py           ← YAML → SQL 生成器
├── adapters/
│   └── duckdb.py           ← 统一 DuckDB Protocol
├── models/
│   ├── position.py         ← 只有 apply_fill() 业务逻辑
│   ├── order.py            ← 只有 mark_filled() 业务逻辑
│   └── events.py           ← SignalEmitted / OrderFilled / OrderRejected
├── data/
│   ├── app.py              ← DataEngine (用 DuckDBProtocol)
│   ├── models.py           ← Commands: IngestKlines, ComputeIndicators, GetKlines, PushBars
│   └── clients/file.py
├── analysis/
│   ├── app.py              ← AnalysisEngine (用 DuckDBProtocol)
│   └── algo/retracement/   ← 算法逻辑不动
├── strategy/
│   └── app.py              ← FibStrategy (用 DuckDBProtocol)
├── execution/
│   ├── broker.py           ← Broker (用 DuckDBProtocol)
│   └── adapters/sim.py     ← SimExchange
├── engine/
│   ├── app.py              ← TimingApp / BacktestApp
│   └── command.py          ← RunBacktest
├── infra/
│   ├── docker-compose.yml  ← Grafana
│   └── grafana/provisioning/
├── scripts/
│   ├── start_grafana.sh
│   └── batch_ingest_and_compute.py
├── config.toml
└── backtest.toml
```

---

## 八、执行顺序

| # | 任务 | 耗时 |
|---|------|------|
| 1 | 创建 `schema/registry.yml` | 20 min |
| 2 | 创建 `schema/engine.py` (DDL/INSERT/指标 SQL 生成) | 40 min |
| 3 | 创建 `adapters/duckdb.py` (统一 Protocol) | 30 min |
| 4 | 改造 `DataEngine` — 用 DuckDBProtocol + SchemaEngine | 30 min |
| 5 | 改造 `AnalysisEngine` — 去掉 SQLite，用共享 DuckDBProtocol | 20 min |
| 6 | 改造 `FibStrategy` — 同上 | 10 min |
| 7 | 改造 `Broker` — 同上 | 15 min |
| 8 | 精简 `models/` — 删除纯数据定义，保留业务逻辑 | 20 min |
| 9 | 删除 `dashboard/`, `frontend/`, `adapters/sqlite.py` | 5 min |
| 10 | 实现 ComputeIndicators (talib 预计算) | 30 min |
| 11 | 改造 RunBacktest — 加 run_id + 结果写入 | 20 min |
| 12 | 修改 batch_ingest_and_compute.py | 10 min |
| 13 | Grafana 部署 + provisioning | 30 min |
| 14 | Grafana Dashboard JSON | 45 min |
| 15 | 端到端验证 | 30 min |

**总耗时约 6 小时**

---

## 九、验收标准

- [ ] 只有一个数据库文件 `timing.duckdb`，所有旧 SQLite 文件不再使用
- [ ] `registry.yml` 修改字段后，重启即自动建表/加列（DDL idempotent）
- [ ] 回测写入数据带 run_id，Grafana 可按 run_id 筛选
- [ ] `ComputeIndicators` 执行后 indicators 表有 sma/rsi/macd 等字段值
- [ ] Grafana K 线面板 + 指标叠加 + 信号标注 正常渲染
- [ ] 切换 $run_id 变量后，面板数据正确切换
- [ ] `timing/models/` 中无冗余的纯字段定义文件
