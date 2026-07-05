# Grafana 实验数据查看手册

> 通过 Grafana + DuckDB 插件查看 computation 模块产出的实验文件（Parquet + manifest.json）。

---

## 一、环境信息

| 项目 | 值 |
|------|------|
| Grafana 地址 | http://localhost:3000 |
| 账号/密码 | admin / timing |
| 数据源名称 | TimingDuckDB |
| 数据源类型 | motherduck-duckdb-datasource（`:memory:` 模式） |
| 容器内数据路径 | `/warehouse/timing/` |
| 启动方式 | `cd infra && podman-compose up -d` |

---

## 二、数据目录结构

```
/warehouse/timing/computation/{algo}/{compute_id}/{symbol}/{interval}/
```

示例：

```
/warehouse/timing/computation/fib_retracement/
├── base_v1/                          # compute_id = 参数 profile 名
│   ├── 159363.OF/1d/                 # symbol/interval
│   │   ├── manifest.json             # 实验元数据
│   │   ├── step1_pivots.parquet
│   │   ├── step2_confidence.parquet
│   │   ├── step3_clusters.parquet
│   │   ├── step4_legs.parquet
│   │   └── result.parquet
│   └── 510300.OF/1d/                 # 同一 profile 可跑多个品种
│       └── ...
└── wide_scan/
    └── 159363.OF/1d/
        └── ...
```

**设计原则**：
- `compute_id` = 纯参数配置名（对应 `profiles/{name}.toml`），不绑定品种
- 每个 `{compute_id}/{symbol}/{interval}` 三元组唯一标识一次实验运行
- 同一 profile 可对多个品种执行，互不干扰

---

## 三、Grafana 变量配置

### 3.1 变量 `compute_id`（实验参数组）

- **Name**: `compute_id`
- **Type**: Query
- **Query**:

```sql
SELECT DISTINCT compute_id
FROM read_json('/warehouse/timing/computation/fib_retracement/*/*/*/manifest.json')
ORDER BY compute_id
```

### 3.2 变量 `symbol`（品种，级联过滤）

- **Name**: `symbol`
- **Type**: Query
- **Query**（依赖 `compute_id`）：

```sql
SELECT DISTINCT symbol
FROM read_json(
  '/warehouse/timing/computation/fib_retracement/${compute_id:raw}/*/*/manifest.json'
)
ORDER BY symbol
```

> 这样选了 `compute_id` 后，`symbol` 只显示该参数组已运行过的品种。

### 3.3 变量 `interval`（周期，级联过滤）

- **Name**: `interval`
- **Type**: Query
- **Query**（依赖 `compute_id` + `symbol`）：

```sql
SELECT DISTINCT interval
FROM read_json(
  '/warehouse/timing/computation/fib_retracement/${compute_id:raw}/${symbol:raw}/*/manifest.json'
)
ORDER BY interval
```

> 三个变量形成级联关系：`compute_id` → `symbol` → `interval`，确保组合始终有效。

---

## 四、面板 SQL 模板

> 路径模式：`/warehouse/timing/computation/fib_retracement/${compute_id:raw}/${symbol:raw}/${interval:raw}/`

### 4.1 实验总览表（Table）

```sql
SELECT
  compute_id,
  symbol,
  interval,
  created_at,
  status,
  source.klines_count AS klines_count,
  config.recent_bars AS recent_bars,
  config.top_n AS top_n,
  config.min_leg_span_pct AS min_leg_span_pct
FROM read_json('/warehouse/timing/computation/fib_retracement/*/*/*/manifest.json')
ORDER BY created_at DESC
```

### 4.2 Fib Levels 结果（Table）

```sql
SELECT multiplier, direction, score, leg_low, leg_high, levels_json
FROM read_parquet(
  '/warehouse/timing/computation/fib_retracement/${compute_id:raw}/${symbol:raw}/${interval:raw}/result.parquet'
)
ORDER BY score DESC
```

### 4.3 K线 + 三组 Fib 回撤线（Candlestick）

```sql
WITH ranked AS (
  SELECT
    multiplier, leg_low, leg_high,
    ROW_NUMBER() OVER (PARTITION BY multiplier ORDER BY score DESC) AS rn
  FROM read_parquet(
    '/warehouse/timing/computation/fib_retracement/${compute_id:raw}/${symbol:raw}/${interval:raw}/result.parquet'
  )
)
SELECT
  to_timestamp(k.ts / 1000) AS time,
  k.open, k.high, k.low, k.close,
  -- 短期 mult=1
  s.leg_high AS "短期_0%",
  s.leg_low + (s.leg_high - s.leg_low) * 0.618 AS "短期_38.2%",
  (s.leg_low + s.leg_high) / 2 AS "短期_50%",
  s.leg_low + (s.leg_high - s.leg_low) * 0.382 AS "短期_61.8%",
  s.leg_low AS "短期_100%",
  -- 中期 mult=2
  m.leg_high AS "中期_0%",
  m.leg_low + (m.leg_high - m.leg_low) * 0.618 AS "中期_38.2%",
  (m.leg_low + m.leg_high) / 2 AS "中期_50%",
  m.leg_low + (m.leg_high - m.leg_low) * 0.382 AS "中期_61.8%",
  m.leg_low AS "中期_100%",
  -- 长期 mult=3
  l.leg_high AS "长期_0%",
  l.leg_low + (l.leg_high - l.leg_low) * 0.618 AS "长期_38.2%",
  (l.leg_low + l.leg_high) / 2 AS "长期_50%",
  l.leg_low + (l.leg_high - l.leg_low) * 0.382 AS "长期_61.8%",
  l.leg_low AS "长期_100%"
FROM read_parquet('/warehouse/timing/klines/${symbol:raw}/${interval:raw}/*.parquet') k
CROSS JOIN (SELECT * FROM ranked WHERE multiplier = 1 AND rn = 1) s
CROSS JOIN (SELECT * FROM ranked WHERE multiplier = 2 AND rn = 1) m
CROSS JOIN (SELECT * FROM ranked WHERE multiplier = 3 AND rn = 1) l
ORDER BY k.ts
```

**Override 颜色**：
- `/^短期/` → 橙色，Line style: Dash
- `/^中期/` → 蓝色，Line style: Dash
- `/^长期/` → 绿色，Line style: Dash

### 4.4 置信度时间序列（Time series）

```sql
SELECT
  to_timestamp(ts / 1000) AS time,
  close,
  conf_high,
  conf_low
FROM read_parquet(
  '/warehouse/timing/computation/fib_retracement/${compute_id:raw}/${symbol:raw}/${interval:raw}/step2_confidence.parquet'
)
ORDER BY ts
```

### 4.5 趋势腿分布（Table / Bar chart）

```sql
SELECT multiplier, direction, span_pct, conf_score, low, high
FROM read_parquet(
  '/warehouse/timing/computation/fib_retracement/${compute_id:raw}/${symbol:raw}/${interval:raw}/step4_legs.parquet'
)
ORDER BY conf_score DESC
```

### 4.6 价格聚类中心（Bar chart）

```sql
SELECT kind, center, hit_count, total_conf
FROM read_parquet(
  '/warehouse/timing/computation/fib_retracement/${compute_id:raw}/${symbol:raw}/${interval:raw}/step3_clusters.parquet'
)
ORDER BY total_conf DESC
```

---

## 五、实验对比面板

### 5.1 多实验参数对比（Table）

```sql
SELECT
  compute_id,
  symbol,
  config.recent_bars AS recent_bars,
  config.top_n AS top_n,
  config.skip_recent AS skip_recent,
  config.min_leg_span_pct AS min_leg_span_pct,
  to_json(config.pivot_windows)::VARCHAR AS pivot_windows,
  to_json(config.zigzag_thresholds)::VARCHAR AS zigzag_thresholds
FROM read_json('/warehouse/timing/computation/fib_retracement/*/*/*/manifest.json')
ORDER BY created_at DESC
```

### 5.2 同品种跨参数 Fib 分数对比

```sql
SELECT
  replace(replace(filename, '/warehouse/timing/computation/fib_retracement/', ''), '/result.parquet', '') AS experiment,
  multiplier,
  direction,
  score
FROM read_parquet(
  '/warehouse/timing/computation/fib_retracement/*/${symbol:raw}/${interval:raw}/result.parquet',
  filename=true
)
ORDER BY score DESC
```

---

## 六、操作步骤总结

1. 打开 http://localhost:3000，登录 `admin / timing`
2. **Dashboards → New → New Dashboard**
3. **⚙️ Settings → Variables**，按顺序添加 `compute_id` → `symbol` → `interval`（级联）
4. 添加面板，数据源选 **TimingDuckDB**，Code 模式粘贴 SQL
5. 顶部三个下拉联动筛选，确保组合始终有效

---

## 七、命令行快速查看

```bash
# 列出所有实验
duckdb -c "
  SELECT compute_id, symbol, interval, created_at
  FROM read_json('warehouse/timing/computation/fib_retracement/*/*/*/manifest.json')
  ORDER BY created_at DESC
"

# 查看某实验的 Fib Levels
duckdb -c "
  SELECT multiplier, direction, score, leg_low, leg_high
  FROM read_parquet('warehouse/timing/computation/fib_retracement/base_v1/159363.OF/1d/result.parquet')
  ORDER BY score DESC
"
```

---

## 八、注意事项

1. **级联变量**：`symbol` 和 `interval` 的 Query 引用上级变量，选择顺序从左到右
2. **嵌套字段**：`config.field` 直接取值，复杂对象用 `to_json(...)::VARCHAR`
3. **时间戳**：`to_timestamp(ts / 1000)` 转换毫秒时间戳
4. **Glob 路径**：`*` 匹配一级目录，3 个 `*` 对应 `compute_id/symbol/interval`
5. **变量格式**：路径中必须用 `${var:raw}` 避免 Grafana 额外加引号
6. **参数管理**：`profiles/{compute_id}.toml` 只存计算参数，品种在执行时指定
