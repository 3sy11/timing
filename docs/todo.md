# timing 可视化基础设施落地 TODO

> 必选三件套：**Cube Core (语义层)** + **Grafana (监控+K线)** + **DuckDB talib 扩展 (SQL 技术指标)**

---

## 一、DuckDB talib 扩展集成

### 1.1 安装扩展

- [ ] 在 timing 环境的 DuckDB 中安装 talib 社区扩展
- [ ] 验证扩展在 DataEngine 的 DuckDB 实例中可用

```python
# timing/data/app.py on_start 中追加
self._conn.execute("INSTALL talib FROM community")
self._conn.execute("LOAD talib")
```

### 1.2 新增 DataEngine 方法：技术指标查询

- [ ] 在 `data/app.py` 中新增 `get_klines_with_indicators()` 方法

```python
def get_klines_with_indicators(self, symbol: str, interval: str, indicators: list[str] = None) -> list[dict]:
    """返回 K 线 + 技术指标。indicators 示例: ['sma_20', 'rsi_14', 'ema_12']"""
    ind_sql = []
    for ind in (indicators or ['sma_20', 'rsi_14']):
        name, period = ind.rsplit('_', 1)
        ind_sql.append(f'ta_{name}(close, {period}) OVER (ORDER BY ts) AS {ind}')
    cols = ', '.join(['open', 'high', 'low', 'close', 'volume', 'ts'] + ind_sql)
    sql = f"SELECT {cols} FROM klines WHERE symbol=? AND interval=? ORDER BY ts"
    result = self._conn.execute(sql, [symbol, interval]).fetchall()
    col_names = ['open','high','low','close','volume','ts'] + (indicators or ['sma_20','rsi_14'])
    return [dict(zip(col_names, r)) for r in result]
```

### 1.3 新增 Command：GetKlinesWithIndicators

- [ ] 在 `data/models.py` 中添加新 Command
- [ ] 注册到 DataEngine 的 router_mapping

```python
class GetKlinesWithIndicators(BaseCommand):
    """带技术指标的 K 线查询"""
    domain = "data"
    symbol: str
    interval: str
    indicators: list[str] = ["sma_20", "rsi_14", "ema_12"]
    start_ts: int = None
    end_ts: int = None
```

### 1.4 注册 HTTP API

- [ ] 路由: `GET /api/data/klines_indicators?symbol=159363.OF&interval=1d&indicators=sma_20,rsi_14,ema_12`

---

## 二、Cube Core 语义层部署

### 2.1 创建 Cube 项目目录

- [ ] 创建 `timing/cube/` 目录
- [ ] 创建 `timing/cube/.env`
- [ ] 创建 `timing/cube/docker-compose.yml`

```
timing/cube/
├── .env
├── docker-compose.yml
└── model/
    ├── klines.yml
    ├── signals.yml
    ├── orders.yml
    └── positions.yml
```

### 2.2 环境配置文件

- [ ] `timing/cube/.env`

```env
CUBEJS_DB_TYPE=duckdb
CUBEJS_DB_DUCKDB_DATABASE_PATH=/cube/conf/klines.duckdb
CUBEJS_DEV_MODE=true
CUBEJS_API_SECRET=timing-cube-secret
CUBEJS_EXTERNAL_DEFAULT=true
CUBEJS_SCHEDULED_REFRESH_DEFAULT=true
CUBEJS_WEB_SOCKETS=false
```

### 2.3 Docker Compose

- [ ] `timing/cube/docker-compose.yml`

```yaml
services:
  cube:
    image: cubejs/cube:latest
    ports:
      - "4000:4000"   # REST + Playground
      - "15432:15432" # SQL API (Postgres 协议)
    volumes:
      - ./:/cube/conf
      - ../warehouse/timing:/cube/data:ro
    env_file: .env
    restart: unless-stopped
```

### 2.4 数据模型文件

- [ ] `timing/cube/model/klines.yml` — K 线主 cube

```yaml
cubes:
  - name: klines
    sql_table: klines
    data_source: default

    measures:
      - name: count
        type: count
      - name: avg_close
        sql: close
        type: avg
      - name: avg_open
        sql: open
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
      - name: price_range
        sql: "({CUBE}.high - {CUBE}.low)"
        type: avg
      - name: volatility
        sql: "({CUBE}.high - {CUBE}.low) / NULLIF({CUBE}.close, 0)"
        type: avg

    dimensions:
      - name: symbol
        sql: symbol
        type: string
      - name: interval
        sql: "\"interval\""
        type: string
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
      - name: ts
        sql: ts
        type: time
```

- [ ] `timing/cube/model/signals.yml` — 信号 cube

```yaml
cubes:
  - name: signals
    sql: >
      SELECT symbol, direction, strength, price, ts
      FROM read_parquet('/cube/data/*/signals.parquet')
    # 如果信号存在 SQLite，改为:
    # sql_table: signals (需配 sqlite 数据源)

    measures:
      - name: count
        type: count
      - name: avg_strength
        sql: strength
        type: avg
      - name: max_strength
        sql: strength
        type: max

    dimensions:
      - name: symbol
        sql: symbol
        type: string
      - name: direction
        sql: direction
        type: string
      - name: strength
        sql: strength
        type: number
      - name: price
        sql: price
        type: number
      - name: ts
        sql: ts
        type: time
```

- [ ] `timing/cube/model/orders.yml` — 订单 cube（从 Broker SQLite 读）

```yaml
cubes:
  - name: orders
    # Broker 的订单持久化在 SQLite，Cube 可通过 DuckDB sqlite 扩展读
    sql: >
      SELECT * FROM sqlite_scan('/cube/data/broker/broker.sqlite', 'orders')

    measures:
      - name: count
        type: count
      - name: total_filled_qty
        sql: filled_qty
        type: sum
      - name: avg_fill_price
        sql: fill_price
        type: avg

    dimensions:
      - name: symbol
        sql: symbol
        type: string
      - name: side
        sql: side
        type: string
      - name: status
        sql: status
        type: string
      - name: created_at
        sql: created_at
        type: time
```

### 2.5 Cube MCP 配置

- [ ] 在项目 `.cursor/mcp.json` 中添加 Cube MCP Server

```json
{
  "mcpServers": {
    "cube": {
      "command": "npx",
      "args": ["@cube-dev/mcp-server"],
      "env": {
        "CUBE_API_URL": "http://localhost:4000/cubejs-api/v1",
        "CUBE_API_SECRET": "timing-cube-secret"
      }
    }
  }
}
```

### 2.6 启动脚本

- [ ] `timing/scripts/start_cube.sh`

```bash
#!/bin/bash
cd "$(dirname "$0")/../cube"
docker compose up -d
echo "Cube Playground: http://localhost:4000"
echo "Cube SQL API:    postgresql://localhost:15432/cube"
```

---

## 三、Grafana 部署 + 配置

### 3.1 Docker Compose（合并到 cube 的 compose 中）

- [ ] 扩展 `timing/cube/docker-compose.yml` 加入 Grafana

```yaml
  grafana:
    image: grafana/grafana:latest
    ports:
      - "3001:3000"
    volumes:
      - grafana-data:/var/lib/grafana
      - ./grafana/provisioning:/etc/grafana/provisioning
      - ../warehouse/timing:/data:ro
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=timing
      - GF_INSTALL_PLUGINS=motherduckdb-duckdb-datasource
    restart: unless-stopped
    depends_on:
      - cube

volumes:
  grafana-data:
```

### 3.2 Grafana 数据源自动配置 (Provisioning)

- [ ] `timing/cube/grafana/provisioning/datasources/default.yml`

```yaml
apiVersion: 1
datasources:
  # 直连 DuckDB 文件（K 线原始数据 + talib）
  - name: DuckDB-Klines
    type: motherduckdb-duckdb-datasource
    access: proxy
    jsonData:
      path: /data/klines.duckdb
    isDefault: true

  # 连 Cube SQL API（语义层聚合）
  - name: Cube-SQL
    type: postgres
    url: cube:15432
    database: cube
    user: cube
    jsonData:
      sslmode: disable
```

### 3.3 Grafana Dashboard 预配置

- [ ] `timing/cube/grafana/provisioning/dashboards/default.yml`

```yaml
apiVersion: 1
providers:
  - name: timing
    folder: timing
    type: file
    options:
      path: /etc/grafana/provisioning/dashboards/json
```

- [ ] `timing/cube/grafana/provisioning/dashboards/json/kline-overview.json`
  - K 线 Candlestick 面板（数据源 = DuckDB-Klines）
  - 技术指标叠加（SMA20, RSI14）
  - 信号标注 Annotation
  - 变量：$symbol, $interval

### 3.4 Grafana 面板 SQL 模板

K 线面板查询（DuckDB 数据源）:
```sql
SELECT ts AS time, open, high, low, close, volume
FROM klines
WHERE symbol = '${symbol}' AND "interval" = '${interval}'
  AND $__timeFilter(ts)
ORDER BY ts
```

技术指标叠加:
```sql
SELECT ts AS time,
  ta_sma(close, 20) OVER (ORDER BY ts) AS sma_20,
  ta_ema(close, 12) OVER (ORDER BY ts) AS ema_12
FROM klines
WHERE symbol = '${symbol}' AND "interval" = '${interval}'
  AND $__timeFilter(ts)
ORDER BY ts
```

信号标注查询（另一个 Annotation 数据源）:
```sql
SELECT ts AS time, direction AS text, strength AS tags
FROM signals
WHERE symbol = '${symbol}'
  AND $__timeFilter(ts)
```

### 3.5 报警规则

- [ ] 新信号触发时报警（Grafana Alert）
- [ ] 账户 drawdown > 5% 报警

---

## 四、项目代码变更清单

### 4.1 需要修改的文件

| 文件 | 变更内容 |
|------|----------|
| `timing/data/app.py` | 追加 `LOAD talib` + 新增 `get_klines_with_indicators()` |
| `timing/data/models.py` | 新增 `GetKlinesWithIndicators` command |
| `timing/models/kline.py` | 无变更（DDL 已够用） |
| `timing/config.toml` | DataEngine router_mapping 追加新路由 |

### 4.2 需要新建的文件

| 文件 | 用途 |
|------|------|
| `timing/cube/` 目录 | Cube 项目（模型+配置） |
| `timing/cube/.env` | Cube 环境变量 |
| `timing/cube/docker-compose.yml` | Cube + Grafana 容器编排 |
| `timing/cube/model/klines.yml` | K 线语义模型 |
| `timing/cube/model/signals.yml` | 信号语义模型 |
| `timing/cube/model/orders.yml` | 订单语义模型 |
| `timing/cube/grafana/provisioning/...` | Grafana 预配置 |
| `timing/scripts/start_cube.sh` | 启动脚本 |
| `timing/scripts/dashboard.py` | Streamlit 快速原型（开发用） |

### 4.3 需要删除的文件（可选）

| 文件 | 理由 |
|------|------|
| `timing/frontend/` 目录 | 不再自建前端（被 Grafana 替代） |
| `timing/dashboard/` 模块 | DashboardService 被 Grafana + Cube 替代 |

> ⚠️ 删除需确认：dashboard 模块中的 `run_batch_job` 逻辑是否仍需要。如仍需批量回测管理，保留 DashboardService 但移除前端挂载逻辑。

---

## 五、使用场景示例

### 场景 A：回测完成后查看 K 线 + 指标 + 信号

```
1. timing 回测引擎执行 → 数据写入 DuckDB (klines) + SQLite (signals/orders)
2. 打开 Grafana http://localhost:3001
3. 选择 Dashboard "K线概览"
4. 下拉选 symbol=159363.OF, interval=1d
5. 面板自动渲染:
   - 上方: Candlestick K 线 + SMA20/EMA12 叠加
   - 中间: RSI(14) 曲线
   - 下方: 信号标注（买/卖箭头）
   - 右侧: 账户权益曲线
```

### 场景 B：AI 通过 Cube MCP 查询数据

```
在 Cursor 中:

用户: "159363.OF 最近 20 根 K 线的均价？"

AI → Cube MCP:
  POST /cubejs-api/v1/load
  {
    "query": {
      "measures": ["klines.avg_close"],
      "timeDimensions": [{
        "dimension": "klines.ts",
        "granularity": "day",
        "dateRange": "last 20 days"
      }],
      "filters": [{"dimension": "klines.symbol", "operator": "equals", "values": ["159363.OF"]}]
    }
  }

返回: { "data": [{"klines.avg_close": 1.234}] }
```

### 场景 C：SQL 中直接计算技术指标（DuckDB talib）

```sql
-- 在 timing 回测流程中、Grafana 查询中、或 Cube 模型中都可用

-- 计算 MACD
SELECT t_macd(list(close ORDER BY ts), 12, 26, 9) AS macd_result
FROM klines WHERE symbol = '159363.OF';
-- 返回: struct{macd: -0.003, signal: -0.002, hist: -0.001}

-- 计算布林带
SELECT t_bbands(list(close ORDER BY ts), 20, 2.0, 2.0, 0) AS bbands
FROM klines WHERE symbol = '159363.OF';
-- 返回: struct{upper: 1.30, middle: 1.25, lower: 1.20}

-- K 线形态识别（十字星）
SELECT t_cdldoji(
  list(open ORDER BY ts), list(high ORDER BY ts),
  list(low ORDER BY ts), list(close ORDER BY ts)
) AS doji_pattern
FROM klines WHERE symbol = '159363.OF';

-- 批量计算所有品种的 RSI
SELECT symbol, ts, close,
  ta_rsi(close, 14) OVER (PARTITION BY symbol ORDER BY ts) AS rsi_14
FROM klines
ORDER BY symbol, ts;
```

### 场景 D：Cube REST API 供给 TradingView 前端

```javascript
// 前端直接调 Cube REST API 拿数据，不需要自己写 backend
const resp = await fetch('http://localhost:4000/cubejs-api/v1/load', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'Authorization': 'timing-cube-secret'
  },
  body: JSON.stringify({
    query: {
      dimensions: ['klines.ts', 'klines.open', 'klines.high', 'klines.low', 'klines.close'],
      filters: [{ dimension: 'klines.symbol', operator: 'equals', values: ['159363.OF'] }],
      order: { 'klines.ts': 'asc' },
      limit: 5000
    }
  })
});
const { data } = await resp.json();
// data = [{klines.ts: "2025-01-02", klines.open: 1.23, ...}, ...]
// 直接喂给 lightweight-charts
```

### 场景 E：Grafana 报警 — 新信号触发通知

```yaml
# Grafana Alert Rule (在 UI 中配置，或 provisioning JSON)
# 条件: signals 表在最近 5 分钟有新行
# SQL:
#   SELECT COUNT(*) FROM signals WHERE ts > epoch_ms(now() - interval '5 minutes')
# 阈值: > 0
# 通知: 钉钉 Webhook / Slack / Email
```

### 场景 F：Cube 聚合 + Grafana 权益曲线

```sql
-- Grafana 面板 SQL (连 Cube SQL API, 端口 15432)
-- Cube 已预聚合，查询亚秒返回
SELECT
  klines.ts AS time,
  klines.avg_close AS avg_price,
  klines.total_volume AS volume
FROM klines
WHERE klines.symbol = '${symbol}'
  AND klines.ts >= $__timeFrom
  AND klines.ts <= $__timeTo
ORDER BY time
```

---

## 六、执行顺序

| 序号 | 任务 | 依赖 | 预计耗时 |
|------|------|------|----------|
| 1 | DataEngine 中 INSTALL+LOAD talib 扩展 | 无 | 10 min |
| 2 | 新增 `get_klines_with_indicators()` + Command | #1 | 30 min |
| 3 | 创建 `timing/cube/` 目录 + 模型文件 | 无 | 30 min |
| 4 | 创建 docker-compose.yml (Cube + Grafana) | #3 | 15 min |
| 5 | 配置 Grafana provisioning (数据源+dashboard) | #4 | 45 min |
| 6 | 验证 Grafana K 线面板 + talib 指标叠加 | #1,#5 | 30 min |
| 7 | 配置 Cube MCP Server → Cursor 联调 | #3 | 15 min |
| 8 | 创建 `scripts/start_cube.sh` 启动脚本 | #4 | 5 min |
| 9 | (可选) 评估是否删除 dashboard/ + frontend/ | #6 | - |
| 10 | (可选) Streamlit 开发辅助脚本 | #1 | 15 min |

**总预计耗时: 约 3 小时**

---

## 七、验收标准

- [ ] `docker compose up` 一键启动 Cube + Grafana
- [ ] Grafana 打开即看到 K 线 Candlestick + SMA/EMA/RSI 叠加
- [ ] Grafana 变量切换 symbol 后面板数据自动更新
- [ ] Cube Playground (http://localhost:4000) 可交互查询 klines
- [ ] Cursor 中通过 Cube MCP 可自然语言查 timing 数据
- [ ] `timing/data/app.py` 中 talib 扩展可用，API 返回带指标的 K 线数据
