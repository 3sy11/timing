# P4 顺序图

## V1: 查看生产数据 K 线 + 信号

```
浏览器
  → GET /api/data/symbols
    → ListSymbols() → list[dict]
      → DataEngine.list_symbols() → [{symbol, interval, count, first_ts, last_ts}]
  ← 渲染品种选择器

浏览器
  → GET /api/data/klines?symbol=159363.OF&interval=1d
    → GetKlinesAPI(symbol: str, interval: str, limit: int) → list[dict]
      → DataEngine.get_klines(symbol, interval) → [{ts, open, high, low, close, volume}]
  ← 渲染 TradingView K 线图

浏览器
  → GET /api/data/signals?symbol=159363.OF&interval=1d
    → GetSignals(symbol: str, interval: str) → list[dict]
      → RetracementService.protocol.get("signals:{symbol}:{interval}") → [{ts, direction, strength, touch_price, level_price}]
  ← 叠加信号 Markers 到 K 线图
```

## V2: 查看回测实验结果

```
浏览器
  → GET /api/dashboard/runs?limit=100
    → ListRuns(limit: int, offset: int) → dict
      → DashboardService.protocol.get("__runs") → [{run_id, symbol, interval, params, metrics, status}]
  ← 渲染实验列表

浏览器 (用户选择某次 run)
  → GET /api/dashboard/runs/{run_id}
    → GetRun(run_id: str) → dict | None
      → DashboardService.protocol.get("__run_detail:{run_id}")
        → {run_id, symbol, interval, params, metrics, result: {klines, fills, signals, account}}
  ← 解析 result:
     klines → TradingView K 线图
     fills  → Markers (绿▲买入 / 红▼卖出)
     signals → Markers (蓝△多 / 橙▽空)
     从 fills 重建 equity_curve → ECharts 权益曲线
     从 equity_curve 计算 drawdown → ECharts 回撤面积图
```

## V4: 无数据

```
浏览器
  → GET /api/data/klines?symbol=XXX&interval=1d
    → GetKlinesAPI(symbol: str, interval: str, limit: int) → list[dict]
      → DataEngine.get_klines("XXX", "1d") → []
  ← 返回空数组 → 前端显示 "该品种无数据"
```
