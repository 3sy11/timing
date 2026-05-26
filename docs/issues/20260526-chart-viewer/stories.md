# P0-P3: Chart Viewer

## P0 场景故事

| ID | 场景 |
|----|------|
| V1 | 用户打开浏览器，选择一个品种和周期，系统从数据库读取K线和该品种的策略信号/成交记录，渲染出带有买卖标记的K线图 |
| V2 | 用户在回测实验列表中选择一次回测结果，系统从数据库读取该次回测的K线、信号、成交和权益数据，渲染出完整的回测图表（K线+标记+权益曲线+回撤） |
| V3 | 用户在列表中切换不同的回测实验，图表区域刷新为对应实验的数据 |
| V4 | 数据库中没有该品种的数据时，页面提示"无数据" |

## P1 领域边界

| domain | Subject | 职责 |
|--------|---------|------|
| viewer | ChartViewer（前端 React SPA） | 从 API 读取数据，渲染 K 线图表和策略标记 |
| data | DataEngine（已有） | 提供 K 线数据查询 |
| dashboard | DashboardService（已有） | 提供回测 run 列表和详情 |

本次 issue **不新增后端 Service**，只新增 2 个只读 API 路由 + 一个 React 前端。

## P2 行为设计

| Subject | Commands | Events | Subscribed |
|---------|----------|--------|------------|
| DataEngine | **+GetKlinesAPI**（新增只读查询）, **+ListSymbols**（新增） | — | — |
| DashboardService | ListRuns（已有）, GetRun（已有） | — | — |
| ChartViewer（前端） | — | — | — |

前端不产生 Command/Event，只消费 HTTP GET 接口。

## P3 服务职责

### DataEngine 新增方法

- `get_klines(symbol, interval, start_ts?, end_ts?, limit?) → list[dict]` — 已有方法，只需加 HTTP 路由
- `list_symbols() → list[dict]` — 新增，扫描 DuckDB 中的 distinct symbol+interval

### DashboardService 无变更

- `ListRuns` / `GetRun` 已存在，前端直接调用

### ChartViewer（前端）

- 纯只读 React SPA
- 从 2 个数据源读取：
  1. `/api/data/klines` → K 线 + 信号标记（生产数据）
  2. `/api/dashboard/runs/{id}` → 回测详情（含 klines + fills + signals）
- 用 TradingView Lightweight Charts 渲染 K 线 + Markers
- 用 ECharts 渲染权益曲线 + 回撤
