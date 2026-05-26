# Notes

## Intersection Analysis

读取 REGISTRY.md，与本 issue 新增内容的交集分析：

| 已有实体 | 本 issue 关系 |
|---------|--------------|
| DataEngine | 新增 2 个只读 Command + router_mapping |
| RetracementService | 新增 1 个只读 Command + router_mapping |
| DashboardService.ListRuns | 前端直接调用，无修改 |
| DashboardService.GetRun | 前端直接调用，无修改 |

**无冲突**：所有新增都是只读查询，不影响现有写入逻辑。

## Design Decisions

1. **前端框架**: React + Vite，不用 Vue — 方便复用 Kainex/TradingView 社区组件
2. **图表库**: TradingView Lightweight Charts v5（K线）+ ECharts 5（权益/回撤）
3. **UI 组件**: Ant Design 5（开箱即用暗色主题、表格、选择器、布局）
4. **不使用 Tailwind**: 直接用 antd 组件，减少配置
5. **数据流**: 前端只做 HTTP GET，不需要 WebSocket（查看已有结果，不监听实时）
6. **旧 `web/` 目录**: 废弃，新前端构建产物 `frontend/dist/` 替代
7. **生产部署**: `DashboardService` 静态挂载从 `web/` 改为 `frontend/dist/`
8. **只读原则**: 前端不触发回测、不上传数据、不修改配置

## Skeleton Plan

```
1. 后端: 新增 3 个 Command (GetKlinesAPI, ListSymbols, GetSignals)
   → verify: curl /api/data/symbols 返回列表
   → verify: curl /api/data/klines?symbol=159363.OF&interval=1d 返回数据

2. 前端: Vite + React + antd 脚手架 (pnpm)
   → verify: pnpm dev 能跑起来

3. 前端: KlineChart 组件 (TradingView LW Charts)
   → verify: 能渲染 K 线 + Markers

4. 前端: 生产数据页面 (品种选择 → K 线 + 信号)
   → verify: 选品种后图表正确渲染

5. 前端: 回测列表页面 (列表 → 选择 → 详情图表)
   → verify: 点击 run 后看到 K 线 + 买卖点 + 权益 + 回撤

6. 构建: npm run build → dist/ → DashboardService 挂载
   → verify: bollydog service 启动后浏览器能访问
```

## Registry Delta

```
+ command GetKlinesAPI dest=data.DataEngine.GetKlinesAPI
+ command ListSymbols dest=data.DataEngine.ListSymbols
+ command GetSignals dest=analysis.RetracementService.GetSignals
~ service DataEngine router_mapping += GetKlinesAPI, ListSymbols
~ service RetracementService router_mapping += GetSignals
```
