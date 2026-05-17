# 附录：设计审计 — 五步法对照

对照"场景故事 → 顺序图 → 接口契约 → 数据模型 → Walking Skeleton"方法论，审计当前文档完成度。

## 总览

| 步骤 | 内容 | 状态 | 对应文档 | 缺什么 |
|------|------|------|---------|--------|
| ① 场景故事 | 一句话描述使用场景，不涉及类名 | ⚠️ 部分 | 07-trace-examples.md 标题 | 混入了技术术语，缺纯用户视角的故事 |
| ② 顺序图 | 每条箭头 = 一次方法调用（完整签名） | ⚠️ 部分 | 07-trace-examples.md 时序图 | 箭头是描述性文字，不是精确方法签名 |
| ③ 接口契约 | 从顺序图提取每个类的方法签名清单 | ❌ 缺失 | 散落在各模块文档 | 无统一的接口契约文档 |
| ④ 数据模型 | 由接口契约反推字段 | ✅ 完成 | 01-data-models.md | 部分字段未被接口契约覆盖（需 ③ 验证） |
| ⑤ Walking Skeleton | 主干路径代码，其余 stub | ❌ 缺失 | 现有代码（部分不匹配） | 无骨架实现计划 |

---

## 步骤①审计：场景故事

**要求**：用一句话描述最核心的使用场景，**不涉及任何类名**。

**现状**：07-trace-examples.md 的三个示例标题含技术术语（"Bar"、"Fib 关键位"等）。

**缺失**：需要纯用户视角的一句话故事。应写成：

| 示例 | 当前标题（含技术术语） | 应该的场景故事（纯用户视角） |
|------|---------------------|--------------------------|
| 示例一 | 新 Bar → 触碰信号 → 市价买入成交 | 推一根新K线，系统发现价格靠近关键位，自动下单买入并成交 |
| 示例二 | 信号强度不足 → 策略跳过 | 系统发现了机会但认为信号太弱，放弃不下单 |
| 示例三 | 限价买单 → 挂起 → 后续 Bar 触发成交 | 系统挂了一个限价单，等到市场价格触及目标价后自动成交 |

---

## 步骤②审计：顺序图

**要求**：每条箭头都是一次调用，写上**方法名和参数**。逼出所有接口，暴露逻辑漏洞。

**现状**：07-trace-examples.md 有 Mermaid 时序图，但箭头是**描述性文字**，不是精确方法调用。

**逐条箭头审计（示例一时序图）**：

| 当前箭头文字 | 问题 | 应该写什么 |
|-------------|------|-----------|
| `Ext->>DE: PushBars(bars=[bar])` | 缺 symbol, interval 参数 | `PushBars.__call__(symbol, interval, bars, replay=False) → dict` |
| `DE->>RS: _publish 广播 PushBars topic` | 不是方法调用，是描述 | `Exchange.match(topic) → OnBarReceived.__call__(cmd) → dict` |
| `RS->>DE: GetKlines(start_ts=ckpt+1) 跨服务获取` | 缺 symbol, interval | `hub.execute(GetKlines(symbol, interval, start_ts)) → list[dict]` |
| `DE-->>RS: list[Kline]` | 仅返回值无方法名 | （合并到上一行的返回值） |
| `RS->>FS: hub.execute(SignalEmitted) 广播` | 缺 payload 参数 | `hub.execute(cmd) → None`，其中 cmd 内含 `SignalEmitted(ts, symbol, interval, direction, strength, price, level)` |
| `FS->>BK: hub.execute(SubmitOrder)` | 缺所有参数 | `hub.execute(SubmitOrder(symbol, side, order_type, quantity, price, bar)) → FillResult` |
| `BK->>BK: _sync_emit(OrderFilled)` | 缺 event 参数 | `Broker._sync_emit(OrderFilled(order_id, symbol, side, filled_price, filled_quantity, commission, realized_pnl, ts))` |

**缺失的内部调用箭头**（时序图中被 Note 代替了，应该是箭头）：

| 缺失箭头 | 应有的方法调用 |
|----------|-------------|
| Broker → SimExchange | `SimExchangeProtocol.submit_order(order, bar) → FillResult \| None` |
| SimExchange 内部 | `SimExchangeProtocol._fill_market(order, bar) → FillResult` |
| SimExchange → Account | `Account.settle(pnl: float, commission: float) → None` |
| Broker 内部 | `Broker._process_fill(fill: FillResult) → FillResult` |
| Broker 内部 | `Position.apply_fill(fill: FillResult) → float(rpnl)` |
| Broker 内部 | `self.protocol.set("__positions", data) → None` |

**示例三时序图特有的缺失**：

| 缺失箭头 | 应有的方法调用 |
|----------|-------------|
| Broker → SimExchange | `SimExchangeProtocol.check_pending(bar: dict) → list[FillResult]` |
| SimExchange 内部 | `SimExchangeProtocol._fill_market(order, bar) → FillResult`（挂单触发后） |

---

## 步骤③审计：接口契约

**要求**：从顺序图提取每个类需要暴露的方法，名字、入参、返回值。只写签名，不写实现。

**现状**：❌ 完全缺失。方法信息分散在多处：

| 所在位置 | 有什么 | 问题 |
|---------|--------|------|
| 01-data-models.md classDiagram | 服务类上写了部分方法 | 签名不精确，缺参数类型、缺返回类型 |
| 03-analysis-layer.md "通用接口" | 3 个方法名 + 一句话说明 | 无参数类型、无返回类型 |
| 04-strategy-layer.md "on_signal 伪码" | 伪码级描述 | 不是签名，是描述 |
| 05-execution-layer.md "下单流程" | 流程描述 | 不是签名，是伪码 |

**需要的接口契约清单**（从步骤②顺序图中应提取出的全部方法）：

### DataEngine

| 方法 | 签名 | 备注 |
|------|------|------|
| ? | `get_klines(symbol: str, interval: str, start_ts: int = None, end_ts: int = None) → list[dict]` | 被 GetKlines 命令包装 |
| ? | `append_bars(symbol: str, interval: str, bars: list[dict]) → None` | 被 PushBars 调用 |

### AnalysisEngine (基类)

| 方法 | 签名 | 备注 |
|------|------|------|
| ? | `on_bar(cmd: BaseCommand) → dict \| None` | subscriber 入口，由框架调用 |
| ? | `_warmup(symbol: str, interval: str, klines: list[dict]) → None` | 子类实现 |
| ? | `_process_bar(symbol: str, interval: str, bar: dict) → dict` | 子类实现，返回 {signals, breakouts} |

### FibStrategy

| 方法 | 签名 | 备注 |
|------|------|------|
| ? | `on_signal(cmd: BaseCommand) → None` | subscriber 入口 |

### Broker

| 方法 | 签名 | 备注 |
|------|------|------|
| ? | `on_submit_order(order: Order, bar: dict = None) → FillResult \| None` | 被 SubmitOrder 调用 |
| ? | `_process_fill(fill: FillResult) → FillResult` | 内部方法 |
| ? | `process_pending(bar: dict) → list[FillResult]` | 回测逐 bar 调用 |
| ? | `get_position(symbol: str) → Position` | 对外查询 |
| ? | `get_account() → Account` | 对外查询 |
| ? | `_sync_emit(event: BaseEvent) → None` | 同步广播 |

### SimExchangeProtocol

| 方法 | 签名 | 备注 |
|------|------|------|
| ? | `submit_order(order: Order, bar: dict = None) → FillResult \| None` | market 直接返回，limit/stop 返回 None |
| ? | `check_pending(bar: dict) → list[FillResult]` | 挂单触发检查 |
| ? | `_fill_market(order: Order, bar: dict) → FillResult` | 撮合计算 |
| ? | `get_balance() → Account` | 余额查询 |

### 数据模型方法

| 类 | 方法 | 签名 | 备注 |
|----|------|------|------|
| Account | settle | `settle(pnl: float, commission: float) → None` | |
| Position | apply_fill | `apply_fill(fill: FillResult) → float` | 返回 realized_pnl |
| Order | mark_filled | `mark_filled(price: float, qty: float, comm: float, ts: int) → None` | |

### 命令（Command.__call__）

| 命令 | 签名 | 备注 |
|------|------|------|
| PushBars | `__call__() → dict{symbol, interval, bars}` | replay 决定是否写入 |
| GetKlines | `__call__() → list[dict]` | |
| SubmitOrder | `__call__() → FillResult \| None` | |
| RunBacktest | `__call__() → dict` | |

---

## 步骤④审计：数据模型

**现状**：✅ 01-data-models.md 已完成精简，字段与追踪示例对齐。

**待验证项**（需步骤③接口契约完成后对照）：

| 检查项 | 状态 | 说明 |
|--------|------|------|
| Signal 字段是否被 on_bar 返回值覆盖 | ⚠️ | on_bar 返回 dict 还是 Signal 对象？信号输出格式（03文档）与 Signal 模型（01文档）字段不完全一致 |
| StrategyDecision 字段是否被 on_signal 写入覆盖 | ⚠️ | on_signal 中 StrategyDecision 序列化未实现，字段定义与实际写入时机未确认 |
| FillResult 字段是否被 _fill_market 返回值覆盖 | ✅ | 字段与 _fill_market 构造完全一致 |
| Position 字段是否被 apply_fill 更新覆盖 | ⚠️ | apply_fill 的完整逻辑未在文档中明确（如何计算 realized_pnl、如何更新 avg_entry_price） |

**04-strategy-layer.md 不一致**：Signal 字段表仍包含 `metadata` 字段（已从 01-data-models.md 移除）。

---

## 步骤⑤审计：Walking Skeleton

**现状**：❌ 不存在。现有代码与文档设计有多处偏差（见 99-issues.md）。

**主干路径**（步骤②示例一的最薄切片）：

```
外部推 1 根 bar
  → DataEngine 写入
  → RetracementService 冷启动 + 产出 1 个 signal
  → FibStrategy 通过过滤 → 产出 1 个 SubmitOrder
  → Broker 市价撮合 → 1 个 FillResult
  → 返回最终 Account + Position
```

**现有代码与骨架的差距**：

| 骨架需要 | 现有代码 | 差距 |
|---------|---------|------|
| 逐 bar 外层循环 | RunBacktest 用"一次触发批量" | 需重构为逐 bar 循环 |
| Signal 序列化 | 未实现 | 需在 on_bar 末尾写入 |
| StrategyDecision 序列化 | 未实现 | 需在 on_signal 末尾写入 |
| FillResult 序列化 | 未实现 | 需在 _process_fill 中写入 |
| process_pending 调用 | RunBacktest 中有 TODO 未调用 | 需在逐 bar 循环中调用 |
| 回测结果读取 | 未实现 | 需从各模块 protocol 读取汇总 |

---

## 行动清单（按依赖顺序）

以下事项按严格的依赖顺序排列，每一步的输出是下一步的输入：

### Phase 1：精确化设计（文档）

| # | 任务 | 输入 | 输出 | 涉及文档 |
|---|------|------|------|---------|
| 1.1 | 补写纯场景故事（一句话，零类名） | 当前示例标题 | 3 句用户故事 | 07 开头 |
| 1.2 | 精确化顺序图：每条箭头 = `Class.method(params) → ReturnType` | 当前时序图 | 3 张精确时序图 | 07 三个示例 |
| 1.3 | 提取接口契约：从顺序图导出每个类的方法签名清单 | 精确时序图 | 接口契约表 | 新增 07 附录或独立文档 |
| 1.4 | 验证数据模型：接口契约的入参/返回值是否覆盖所有字段 | 接口契约 + 01 | 标记不一致项 | 01 / 04 |
| 1.5 | 修复文档不一致（04 中 Signal 仍含 metadata） | 验证结果 | 文档一致 | 04 |

### Phase 2：Walking Skeleton（代码）

| # | 任务 | 依赖 | 说明 |
|---|------|------|------|
| 2.1 | 重构 RunBacktest 为逐 bar 外层循环 | 1.2 时序图确认流程 | 最关键：当前"一次触发批量"无法支撑主干路径 |
| 2.2 | 实现 Signal 序列化（on_bar → protocol.set） | 1.3 确认 Signal 字段 | |
| 2.3 | 实现 StrategyDecision 序列化（on_signal → protocol.set） | 1.3 确认 Decision 字段 | |
| 2.4 | 实现 FillResult 序列化（_process_fill → protocol.set） | 1.3 确认 FillResult 字段 | |
| 2.5 | RunBacktest 循环中调用 broker.process_pending(bar) | 2.1 完成 | |
| 2.6 | RunBacktest 末尾从各模块 protocol 读取结果汇总 | 2.2-2.4 完成 | |
| 2.7 | 端到端跑通：推 1 根 bar → 拿到 Account + Position | 全部 | 骨架验收标准 |

### 依赖关系图

```
1.1 场景故事 ──▶ 1.2 精确时序图 ──▶ 1.3 接口契约 ──▶ 1.4 验证数据模型 ──▶ 1.5 修复不一致
                      │
                      ▼
              2.1 重构 RunBacktest ──▶ 2.5 process_pending
                      │
                      ▼
              1.3 接口契约 ──▶ 2.2 Signal 序列化
                            ──▶ 2.3 Decision 序列化
                            ──▶ 2.4 FillResult 序列化
                                        │
                                        ▼
                                 2.6 结果汇总 ──▶ 2.7 端到端验收
```
