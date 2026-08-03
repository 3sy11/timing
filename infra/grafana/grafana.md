# Grafana 使用注意点（Timing 项目）

> 基于 Dashboard「斐波那契水平回撤实验数据筛选」实际踩坑整理，后续改看板优先查阅本文。

## 1. 环境与访问

| 项 | 值 |
|----|-----|
| 地址 | http://localhost:3000 |
| 账号 | `admin` / `timing` |
| 镜像 | `grafana/grafana:13.1.0-ubuntu` |
| 数据源 | `TimingDuckDB`（`motherduck-duckdb-datasource`） |
| 仓库挂载 | `warehouse/timing` → 容器内 `/warehouse/timing` |
| 元数据库 | `warehouse/timing/grafana.db` |

启动：`cd timing/infra && podman compose up -d`（需先 `podman machine start`）。

插件安装：`infra/setup-plugin.sh`（DuckDB 插件固定 **v0.4.5**）。

---

## 2. DuckDB 数据源 Query.format（最易踩坑）

`motherduck-duckdb-datasource` 的 `format` 是 **整数枚举**，且与常见 Grafana SQL 插件语义不完全一致：

| format | 实际效果 | 适用面板 |
|--------|----------|----------|
| `0` | **Time series**：字符串列会变成 label，表头变成 `{字段="值", ...}` | 一般不直接当 Table 用 |
| `1` | **Table**：每列独立字段名，无 label 展开 | **Table 面板必须用 1** |
| `2` | 也是按列展开（实测与 1 类似） | 备用 |

**注意：**

- 不能传字符串 `"table"` / `"time_series"`，会报：`cannot unmarshal string into Go struct field Query.format`
- Time series / Candlestick 图表常用 `format: 1`（与插件约定一致，按当前版本实测）
- **Table 面板若出现「一堆 label 粘在表头」→ 立刻把 format 改成 `1`，不要用 `0`**

API 测查询示例：

```bash
curl -u admin:timing -X POST http://localhost:3000/api/ds/query \
  -H 'Content-Type: application/json' \
  -d '{"queries":[{"refId":"A","datasourceId":1,"format":1,"rawSql":"SELECT 1 AS x"}],"from":"now-1h","to":"now"}'
```

---

## 3. 时间列排序

DuckDB 插件对带 `time` / 时间戳列的结果常要求：

```sql
ORDER BY ts ASC   -- 必须升序
```

`ORDER BY ts DESC` 可能报错：

> unable to process the data because it is not sorted in ascending order by time

表格仍要倒序看时：在 Grafana Table **面板 options → Sort by** 设为时间降序，不要在 SQL 里 DESC。

---

## 4. 变量设计约定

与数仓实验 ID 一致，变量应 **从数据读取**，不要写死枚举。

推荐级联：

```
compute_id  →  symbol  →  analysis_id  →  decision_id  →  execution_id
（interval 隐藏，默认 1d）
```

`513090.OF` 实测两条完整链路：

| compute_id | analysis_id | decision_id | execution_id | signals |
|------------|-------------|-------------|--------------|---------|
| `exp_a` | `ana_tight` | `aggressive_v1` | `sim_001` | 355 |
| `exp_b` | `ana_loose` | `loose_v1` | `sim_loose` | 570 |

示例：

```sql
-- compute_id
SELECT DISTINCT compute_id
FROM read_json('/warehouse/timing/computation/fib_retracement/*/*/*/manifest.json')
ORDER BY compute_id

-- symbol（依赖 compute_id）
SELECT DISTINCT symbol
FROM read_json('/warehouse/timing/computation/fib_retracement/${compute_id:raw}/*/*/manifest.json')
ORDER BY symbol

-- analysis_id（依赖 compute_id + symbol）
SELECT DISTINCT analysis_id
FROM read_parquet('/warehouse/timing/signals/**/*.parquet')
WHERE compute_id = '${compute_id:raw}' AND symbol = '${symbol:raw}' AND interval = '${interval:raw}'
ORDER BY analysis_id

-- decision_id（依赖 analysis_id + symbol）
SELECT DISTINCT decision_id
FROM read_parquet('/warehouse/timing/decisions/**/*.parquet')
WHERE analysis_id = '${analysis_id:raw}' AND symbol = '${symbol:raw}'
ORDER BY decision_id

-- execution_id（依赖 decision_id + symbol）
SELECT DISTINCT execution_id
FROM read_parquet('/warehouse/timing/execution/*/orders.parquet')
WHERE decision_id = '${decision_id:raw}' AND symbol = '${symbol:raw}'
ORDER BY execution_id
```

**注意：** 品种下拉依赖 computation 的 `manifest.json`。若只有 `result.parquet` 没有 manifest，该 compute_id / symbol 不会出现在下拉中。

变量用法：

- 路径插值用 `${xxx:raw}`（避免多余引号）
- SQL 字符串比较用 `'${xxx:raw}'`
- **单选** `multi: false`（与实验 ID 选择方式一致，不要默认多选复选框）
- `refresh: 1` 或 `2`：变量/时间变化时重新查询选项
- **`query` 必须是 SQL 字符串**，并同步写 `definition`；不要写成 `{"rawSql": "..."}` 对象，否则下拉为空

变量为空时下游 Query 会得到 0 行（表现为 No data），排查时先看变量当前值。

---

## 5. 在 K 线/Time series 上打点

### 5.1 推荐组合：散点 Query + Annotation

| 层 | 手段 | 作用 |
|----|------|------|
| 价位定位 | 额外 Query，`drawStyle: points` | 圆点落在对应 Y 价位 |
| 详情悬浮 | Dashboard Annotation | 显示 title/text 完整信息 |

Grafana 原生 Time series 的 tooltip：

- `single`：只显示最近一条序列名+数值，**不会**自动带同行其它列
- `all`：同时间点所有序列（K线、Fib、打点）一起刷屏

所以「悬浮只看某一类信号的完整字段」不要指望 tooltip  alone，用 Annotation 更稳。

### 5.2 去掉 Annotation 竖线（Grafana 13）

面板 Options → Annotations → **Hide lines and areas**，对应 JSON：

```json
"options": {
  "annotations": {
    "clustering": -1,
    "multiLane": false,
    "lines": { "width": 0 },
    "regions": { "opacity": 0 }
  }
}
```

保留顶部三角标记 + 悬浮气泡，不画贯穿面板的竖线。

### 5.3 多层打点视觉分层

同一面板上有多类点时建议：

| 系列 | 颜色示例 | 点大小 | Y 轴 |
|------|----------|--------|------|
| 分析信号 | `#FF6666` | 5px | 原始价位（如 `level_price`） |
| 决策 submit | `#00FFFF` | 12px | `price * 1.008`（略上移，避免完全重叠） |

用 Field override：`byName` → `drawStyle: points`、`lineWidth: 0`、`showPoints: always`。

Query 面板上的「眼睛」图标可开关该系列（等价于显示/隐藏打点层）。

### 5.4 Annotation SQL 列约定

```sql
SELECT
  to_timestamp(ts / 1000) AS time,
  '短标题' AS title,
  '详细说明...' AS text,
  tags_col AS tags
FROM ...
ORDER BY ts ASC
```

---

## 6. Parquet 路径

容器内路径前缀统一：

```
/warehouse/timing/...
```

读取实验数据常用 glob：

```sql
read_parquet('/warehouse/timing/signals/**/*.parquet')
read_parquet('/warehouse/timing/decisions/**/*.parquet')
read_json('/warehouse/timing/computation/fib_retracement/*/*/*/manifest.json')
```

变量拼路径时务必 `${var:raw}`，例如：

```
/warehouse/timing/computation/fib_retracement/${compute_id:raw}/${symbol:raw}/${interval:raw}/result.parquet
```

**无数据时先核对：**

1. 变量选的 experiment_id / symbol 是否真有对应 Parquet  
2. 例如 decisions 若只生成了某一品种，其它 `symbol` 会 No data  

---

## 7. Table 面板实践

1. `format = 1`（不要用 0）  
2. `ORDER BY ts ASC`  
3. 中文表头用 SQL 别名：`AS "时间"`、`AS "决策实验ID"`  
4. buy/sell 着色用 Field override + value mapping  
5. UI 排序用 panel `sortBy`，不改 SQL 降序  

决策订单列表示例过滤：

```sql
WHERE decision_id = '${decision_id:raw}'
  AND analysis_id = '${analysis_id:raw}'
  AND symbol = '${symbol:raw}'
  AND action = 'submit'
ORDER BY ts ASC
```

---

## 8. Grafana 13 Unified Storage

升级到 Grafana 13 后，Dashboard 存在 **Unified Storage**（`resource` 表），旧库 `dashboard` 表中的看板可能 **列表搜不到 / API search 为 0**。

安全重建步骤：

1. 从旧库或 API 导出 Dashboard JSON  
2. 停 Grafana，删除/重建 `grafana.db`（或换新 `GF_DATABASE_PATH`）  
3. 启动后 provisioning 数据源/插件  
4. `POST /api/dashboards/db` 重新导入  

不要依赖「关掉 unified storage」降级；以 API 重导入为准。

---

## 9. Dashboard 更新方式

本地脚本常用：

```python
import requests
auth = ("admin", "timing")
uid = "bfr3s4obyazggb"
dash = requests.get(f"http://localhost:3000/api/dashboards/uid/{uid}", auth=auth).json()["dashboard"]
# ... 修改 dash ...
requests.post("http://localhost:3000/api/dashboards/db", auth=auth, json={"dashboard": dash, "overwrite": True})
```

注意：`PUT` 可能返回 Not found，用 `POST /api/dashboards/db` + `overwrite: true`。

---

## 10. 当前看板能力速查

Dashboard：`斐波那契水平回撤实验数据筛选`（uid: `bfr3s4obyazggb`）

| 能力 | 实现 |
|------|------|
| Fib 水平线 + OHLC | Query A |
| 分析信号圆点 | Query B + Annotation「信号详情」 |
| 决策 submit 圆点 | Query C（上移价位）+ Annotation「决策详情」 |
| 决策订单表（非 skip） | Table 面板，`action='submit'`，`format=1` |
| 持仓市值变化 | Time series 面板，`position_value` + `realized_pnl` 双线 |
| 当前持仓快照 | Table 面板，最新一条持仓状态 |

级联变量：`compute_id → symbol → analysis_id → decision_id → execution_id`（`interval` 隐藏）

`513090.OF` 两条可用链路：
- `exp_a` → `ana_tight` → `aggressive_v1` → `sim_001`
- `exp_b` → `ana_loose` → `loose_v1` → `sim_loose`

开关打点：面板图例/查询行左侧眼睛，或 Dashboard Annotations 开关。

### 持仓市值面板 SQL

```sql
SELECT to_timestamp(ts / 1000) AS time,
       CASE WHEN side = 'flat' THEN 0.0 ELSE quantity * avg_price END AS position_value,
       realized_pnl
FROM read_parquet('/warehouse/timing/execution/${execution_id:raw}/positions.parquet')
WHERE symbol = '${symbol:raw}'
QUALIFY ROW_NUMBER() OVER (PARTITION BY ts ORDER BY realized_pnl DESC) = 1
ORDER BY ts ASC
```

### 持仓快照表 SQL

```sql
SELECT to_timestamp(ts / 1000) AS "时间", symbol AS "品种",
       side AS "方向", quantity AS "数量",
       ROUND(avg_price, 4) AS "均价", ROUND(realized_pnl, 6) AS "已实现盈亏"
FROM read_parquet('/warehouse/timing/execution/${execution_id:raw}/positions.parquet')
WHERE symbol = '${symbol:raw}'
QUALIFY ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY ts DESC, realized_pnl DESC) = 1
ORDER BY ts ASC
```

---

## 11. 快速排障清单

1. **No data**：变量是否空？symbol/experiment 下是否有文件？  
2. **表头一堆 `{k=v}`**：Table 的 format 是否误设为 `0`？改成 `1`。  
3. **查询报 unsorted by time**：改 `ORDER BY ... ASC`。  
4. **format 字符串报错**：改用整数 `0/1/2`。  
5. **Annotation 竖线太乱**：`lines.width=0`、`regions.opacity=0`。  
6. **升级后看板消失**：Unified Storage，导出后重建 DB 再导入。  
7. **Podman 连不上**：`podman machine start`，必要时设置正确 API socket。  
8. **面板 Datasource not found / 持仓表无数据**：检查面板 datasource `uid` 是否等于 `TimingDuckDB` 的真实 uid（当前 `PF6BE4C1702A928CD`）。写错 uid（如残留假 uid）时 Grafana 返回 404，面板表现为 No data。  
9. **品种下拉没有某个标的**：对应 `compute_id` 目录下是否缺少 `manifest.json`（仅有 `result.parquet` 不够）。  
