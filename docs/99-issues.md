# 问题附录 — 已知缺陷、设计疑点、待完善项

---

## 🔴 需修复

### 1. Broker.emit(OrderFilled) 在回测中是异步的

**现状：** `Broker.on_submit_order` 末尾 `await hub.emit(OrderFilled)` 走 `asyncio.create_task`，回测中下游（风控/统计）来不及收到。

**建议：** 参考 AnalysisEngine.on_bar 中对 SignalEmitted 的处理方式，改为 `exchange.match + hub.execute` 同步广播。

### 2. SimExchange.check_pending 从未被调用

**现状：** `check_pending(bar)` 检查 Limit/Stop 挂单触发，但无调用点。

**影响：** Limit/Stop 单永远不会成交。

**建议：** 回测逐 bar 循环中每根 bar 后调用 `broker.protocol.check_pending(bar)`。

### 3. Account.lock/unlock 在 Market 单场景下冗余

**现状：** `_fill_market` 中 Market 单立即成交，lock→unlock 无实际锁定期。

**建议：** Market 单跳过 lock/unlock；Limit 单提交时 lock，成交时 unlock。

### 4. FibStrategy 作为独立 AppService 的架构冗余

**现状：** FibStrategy 在 config.toml 中注册为独立 AppService（框架要求），但架构上属于分析层内部子策略。

**建议：** 长期方向是将策略逻辑直接内嵌到 AnalysisEngine 子类中（如 RetracementService.on_bar 末尾直接做策略判断+下单），消除 FibStrategy 的独立服务身份。

---

## 🟡 设计疑点

### 5. 生产 vs 回测信号传递方式不一致

**生产：** `hub.emit` → `asyncio.create_task` 异步  
**回测：** `exchange.match + hub.execute` 同步

**疑点：** 同一段 on_bar 代码用同步方式，生产模式下每个信号都阻塞后续 bar 处理。

**评估：** 单 bar 内信号数通常不多（1-3 个），阻塞影响有限。如果未来高频场景需要，可通过环境变量切换。

### 6. subscriber 可能重复注册

**现状：** TOML 声明的 RetracementService 被 Exchange.on_started 注册一次，BacktestApp.on_started 遍历 _services 时可能再注册一次。

**影响：** `exchange.match` 返回重复 handler，同一服务执行两次 on_bar。

**建议：** BacktestApp.on_started 中只注册动态实例（alias 带 `_N` 后缀的）。

### 7. LedgerEntry 已定义但未使用

**现状：** 模型已定义完整字段，但无任何代码向其中写入记录。

**建议：** 在 Broker.on_submit_order 成交后写入 LedgerEntry，或在未来增加台账查询 API。

---

## 🟢 待完善

### 8. LiveExchangeProtocol 未实现

**位置：** execution/adapters/live.py  
**说明：** 接真实交易所时需实现

### 9. 风控模块为空

**位置：** risk/engine.py  
**建议：** 订阅 OrderFilled / SignalEmitted 做限仓、止损、频率限制

### 10. 回测结果收集

**现状：** 只返回基础统计 `{services, klines_total, handlers, errors}`。  
**缺失：** PnL 曲线、胜率、盈亏比、成交记录导出。  
**建议：** Broker 增加成交记录列表，回测后汇总输出。

### 11. Signal / FillResult 未持久化

**现状：** Signal 只在 on_bar 临时变量中存在，FillResult 只在下单回调中传递。  
**建议：** 增加 SQLite 表存储信号和成交记录，便于回测分析和审计。

### 12. 多标的并行回测

**现状：** execute 一次只处理一个 symbol/interval。  
**建议：** 扩展为接受列表参数。

---

## 📋 技术债务

| 项 | 位置 | 说明 |
|----|------|------|
| execution/engine.py | 空文件 | 遗留，可删除 |
| execution/simulated.py | 空文件 | 遗留，可删除 |
| analysis/models.py | 几乎为空 | 考虑合并或保留 |
| docs/ARCHITECTURE.md | 旧文档 | 已被本系列文档替代 |
