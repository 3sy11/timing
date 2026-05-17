# A2b：策略层实现详述

> A2 接口契约的补充文档，展开策略层的设计哲学、决策逻辑实现细节和注意事项。

---

## 设计哲学

```
策略层的唯一输入：SignalEmitted 事件（frozen 数据快照）
策略层的唯一输出：SubmitOrder 命令（发给 Broker）

分析层 ──SignalEmitted──▶ 策略层 ──SubmitOrder──▶ 执行层
                              │
                    不 import 分析层任何模块
                    不访问分析层任何状态
                    不知道信号是怎么算出来的
```

这种完全解耦设计允许：
- 同一组分析信号被多个策略同时消费（保守/激进策略并行）
- 策略可独立替换，不影响分析层
- 回测时可固定分析结果，只调优策略参数

---

## FibStrategy 实现

### subscriber 注册

```
AnalysisEngine.on_bar → 产出 signals
  → exchange.match("analysis.AnalysisEngine.SignalEmitted")
    → 路由到 FibStrategy.on_signal(cmd)
```

### on_signal 决策逻辑（A2 已定义 6 步流程）

补充 A2 中 on_signal 流程的实现要点：

```
① cmd.get_event() → event_data{symbol, direction, strength, price, ts}
② 过滤：direction == "neutral" → skip(reason="neutral")
③ 过滤：strength < min_strength(0.6) → skip(reason="weak")
④ 方向映射：side = "buy" if direction == "long" else "sell"
⑤ 记录决策：protocol.append("decisions:{s}:{i}", StrategyDecision{...})
⑥ 下单：hub.execute(SubmitOrder(symbol, side, order_type="market", quantity=position_size, bar))
```

**实现注意事项**：

- **先记录再下单**：步骤⑤先于⑥，确保即使下单失败（余额不足等），决策记录也已持久化
- **skip 也记录**：步骤②③的 skip 分支同样 `protocol.append` 写入 Decision{action="skip", reason=...}，保证所有信号都有对应的决策记录
- **bar 传递**：SubmitOrder 需要当前 bar 数据用于市价成交计算（bar.close）

### 决策记录写入规范

每次 on_signal 回调结束时追加一条 StrategyDecision 到列表：

| 场景 | action | reason | order_id | quantity |
|------|--------|--------|----------|----------|
| 信号通过，下单 | submit | — | 订单ID | position_size |
| direction=neutral | skip | neutral | — | — |
| strength 不足 | skip | weak | — | — |

---

## 配置参数（A2 已定义）

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| position_size | float | 0.1 | 每次下单数量（固定值） |
| min_strength | float | 0.6 | 信号强度阈值，低于此值触发 skip |

---

## 缓存 key 规范

| key 格式 | 内容 | 读写方 |
|---------|------|--------|
| `decisions:{symbol}:{interval}` | 策略决策记录 `list[StrategyDecision]` | on_signal 追加写入 / 外部查询读取 |

key 路径已含 symbol 和 interval，StrategyDecision 模型不重复存储这两个字段。

---

## protocol 链

```
CacheLayer（内存缓存，读优先）
  └── SQLiteProtocol（磁盘持久化）
        路径：{path}/strategy/fib_strategy.sqlite
```

每个策略服务拥有独立的 SQLite 数据库。生产和回测通过不同 `{path}` 路径隔离。

---

## 文件清单

| 文件 | 内容 |
|------|------|
| strategy/app.py | FibStrategy 服务（on_signal 决策逻辑） |
| strategy/models.py | StrategyDecision 数据模型 |
| models/signal.py | Signal / SignalEmitted 定义（分析层产出，策略层消费） |

---

## 备注：A2 未定义项（代码实现时移除）

以下内容在旧设计文档中出现，但 A2 接口契约中无对应定义（无故事来源或无顺序图箭头）。
保留仅作参考，**在执行代码实现的时候移除**。

| 项目 | 旧文档位置 | 说明 |
|------|-----------|------|
| Signal.metadata 字段 | 旧 04 Signal 数据格式 | 旧设计中 SignalEmitted 包含 `metadata: dict` 附加信息（ratio、group_idx 等），A2 的 Signal 模型不包含此字段 |
| 后续扩展方向 | 旧 04 扩展说明 | 旧文档提及"按资金比例计算仓位、止损止盈逻辑、多信号聚合决策"，当前无对应故事，不纳入实现范围 |
