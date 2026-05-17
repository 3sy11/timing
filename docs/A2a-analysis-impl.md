# A2a：分析层实现详述

> A2 接口契约的补充文档，展开分析层各方法的实现细节、内部算法、配置管理和注意事项。

---

## 设计哲学

```
每个 AnalysisEngine 子服务 = 算法 + 持久化 的封装体
唯一对外输出：Signal（frozen 数据快照），通过 SignalEmitted 事件广播
下游（策略层）通过 subscriber 独立消费，分析层不关心谁消费、如何消费
```

---

## AnalysisEngine 容器

### 子服务管理

通过 bollydog 的 `add_dependency` 机制管理分析子服务（如 RetracementService）。
子服务在 `on_init_dependencies` 中注册，由框架统一管理生命周期（start/stop/restart）。

### 配置管理

| 机制 | 说明 |
|------|------|
| TOMLFileProtocol | 持久化分析配置到 `{path}/analysis/config.toml` |
| add_dependency 自动发现 | 子服务的 `config` 模块通过 `dep.config.apply_overrides` 注册 |
| on_start 加载 | 启动时读取 TOML，按子服务 alias 分区覆盖模块常量 |
| apply_config（A2 已定义） | 运行时覆盖入口，C2 批量实验时由 RunBacktest 调用 |

### on_bar 实现要点（A2 已定义 6 步流程）

补充 A2 中 on_bar 流程的实现注意事项：

1. **checkpoint 增量机制**：首次 on_bar 时 `__ckpt=None`，走全量路径（GetKlines 全量 → `_warmup` → 剩余 bars）；后续走增量路径（`GetKlines(start_ts=ckpt+1)`），避免重复处理
2. **信号持久化优先**：先 `protocol.set("signals:{s}:{i}", signals)` 写入信号列表，再逐个 `exchange.match` 广播 SignalEmitted，确保信号先持久化再传递
3. **模板方法模式**：on_bar 是基类固定实现，子类只需 override `_warmup` 和 `_process_bar`

---

## RetracementService 实现

### 算法概述

斐波那契回撤分析流程：

```
K线序列 → pivot 点检测 → 趋势腿(TrendLeg)识别 → 回撤组(FibGroup)计算 → 关键位(FibLevel)
标准比率：0.236, 0.382, 0.5, 0.618, 0.786
```

### _warmup 实现（A2 已定义签名）

```
① 调用方提供 klines（回测时为 klines[:warmup_bars]）
② compute_retracement(klines) → RetraceResult{groups: list[FibGroup]}
③ protocol.set("retracement:{s}:{i}", result) → 缓存回撤结构
```

### _process_bar 内部算法（A2 已定义签名和返回值）

A2 定义 `_process_bar → dict{signals, breakouts}`，以下展开内部步骤：

```
① protocol.get("retracement:{s}:{i}") → groups: list[FibGroup]
② 综合强度计算：遍历所有 group.levels，根据 close 与 level.price 的距离加权
③ 触碰检测：distance(close, level.price) < TOUCH_TOLERANCE && 非冷却期
   → signals.append(Signal{ts, symbol, interval, direction, strength, source, price=close, level=level.price})
④ 突破检测：close 超出 group 有效边界（最高/最低 level 之外）
   → breakouts.append(group)
⑤ 若有突破：GetKlines 全量 → compute_retracement → protocol.set 更新缓存
⑥ return {signals, breakouts}
```

**冷却机制**：同一个 FibLevel 被触碰后进入冷却（TOUCH_COOLDOWN 根K线），避免相邻 bar 重复产出信号。

### compute_retracement 纯函数（A2 已定义签名）

```
输入：klines: list[dict], cfg: Config
输出：RetraceResult{groups: list[FibGroup]}

内部步骤：
① detect_pivots(klines, window=ALGO_PIVOT_WINDOW) → pivots
② build_trend_legs(pivots) → legs: list[TrendLeg]
③ for leg in legs: calc_fib_levels(leg, ALGO_FIB_RATIOS) → FibGroup
④ 过滤无效/过短的 group
⑤ return RetraceResult{groups}
```

---

## 配置常量（config.py）

模块级大写常量，通过 `apply_overrides(dict)` 覆盖：

| 前缀 | 示例常量 | 说明 |
|------|---------|------|
| ALGO_* | `ALGO_PIVOT_WINDOW`, `ALGO_FIB_RATIOS` | 算法参数（pivot 窗口大小、标准比率列表） |
| TOUCH_* | `TOUCH_TOLERANCE`, `TOUCH_COOLDOWN`, `TOUCH_BREAKOUT_THRESHOLD` | 触碰检测参数（距离容差、冷却根数、突破阈值） |

---

## 缓存 key 规范

| key 格式 | 内容 | 读写方 |
|---------|------|--------|
| `retracement:{symbol}:{interval}` | 回撤结构（RetraceResult 序列化） | `_warmup` 写入 / `_process_bar` 读写 |
| `__ckpt:{symbol}:{interval}` | 最后处理 bar 的时间戳 | `on_bar` 读写 |
| `signals:{symbol}:{interval}` | 产出信号列表 `list[Signal]` | `on_bar` 写入 / 外部查询读取 |

---

## protocol 链

```
CacheLayer（内存缓存，读优先）
  └── SQLiteProtocol（磁盘持久化）
        路径：{path}/analysis/retracement.sqlite
```

每个分析子服务拥有独立的 SQLite 数据库。生产和回测通过不同 `{path}` 路径隔离。

---

## subscriber 注册

RetracementService 通过 `OnBarReceived` 命令订阅 `data.DataEngine.PushBars` 主题：

```
PushBars.__call__() 返回 → 框架 _publish → Exchange.match("data.DataEngine.PushBars")
  → 路由到 OnBarReceived → 调用 RetracementService.on_bar(cmd)
```

OnBarReceived 是 bollydog subscriber 机制的命令包装器，不包含业务逻辑。

---

## 文件清单

| 文件 | 内容 |
|------|------|
| analysis/engine.py | AnalysisEngine 容器（TOML 配置 + 子服务管理 + on_bar 模板） |
| analysis/algo/retracement/service.py | RetracementService（_warmup + _process_bar） |
| analysis/algo/retracement/algo.py | compute_retracement 纯函数 |
| analysis/algo/retracement/touch.py | 触碰/突破检测纯函数（compute_consensus_strength, check_breakout） |
| analysis/algo/retracement/config.py | ALGO_*/TOUCH_* 模块级配置常量 |
| analysis/algo/retracement/models.py | TrendLeg / FibGroup / FibLevel |
| analysis/algo/retracement/command.py | OnBarReceived subscriber 命令 |
| models/signal.py | Signal / SignalEmitted |

---

## 备注：A2 未定义项（代码实现时移除）

以下内容在旧设计文档中出现，但 A2 接口契约中无对应定义（无故事来源或无顺序图箭头）。
保留仅作参考，**在执行代码实现的时候移除**。

| 项目 | 旧文档位置 | 说明 |
|------|-----------|------|
| `FibLevelTouched` 事件 | 旧 03 subscriber 链 | 旧设计中触碰后广播此事件，A2 中只定义了 `SignalEmitted` 作为唯一输出事件 |
| `FibInvalidated` 事件 | 旧 03 subscriber 链 | 旧设计中突破后广播此事件，A2 中不存在。突破后的处理是内部重算，不对外广播 |
| `ComputeRetracement` 命令 | 旧 03 文件清单 command.py | 旧设计中作为独立 Command 存在，A2 中 `compute_retracement` 是纯函数调用而非 Command |
| Signal 旧字段 `ratio`, `group_idx` | 旧 03 信号输出格式 | 旧设计中 Signal 包含 `ratio`(0.618)、`group_idx`(2) 等字段，A2 的 Signal 模型不包含这些内部算法细节 |
