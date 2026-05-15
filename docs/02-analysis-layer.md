# 分析层 — AnalysisEngine（含策略）

## 职责

接收 K 线数据，通过算法计算交易信号，**内部策略决策后直接向执行层下单**。
对外只暴露 AnalysisEngine，不暴露内部的算法和策略实现细节。

---

## 设计原则

```
每个 AnalysisEngine 子服务 = 三件事的封装体：
  ① 算法：数学模型计算（如斐波那契回撤）
  ② 策略：信号过滤 + 下单决策（如 FibStrategy）
  ③ 持久化：独立 SQLite 存储 checkpoint / 缓存 / 状态
```

外部看到的只是 AnalysisEngine 的统一接口：
- 输入：PushBars 事件（K 线数据）
- 输出：SubmitOrder 命令（下单指令）
- 持久化：每个实例独立的 SQLite

---

## 类继承关系

```
AppService (bollydog)
  └── AnalysisEngine (抽象基类)
        └── RetracementService (具体实现：回撤算法 + FibStrategy 策略)
```

---

## AnalysisEngine 基类

### 类级共享

| 属性 | 说明 |
|------|------|
| clock | 共享时钟（LiveClock / SimulatedClock） |
| _services | 注册表 {alias → 实例}，回测时用于枚举 |

### 生命周期

| 阶段 | 行为 |
|------|------|
| on_start | 无 TOML protocol 配置时，自动创建 CacheLayer → SQLite |
| on_started | 注册到 _services 字典 |

### on_bar 核心流程

```
① 解析 PushBars 中的 symbol / interval
② 读 checkpoint → 决定全量 or 增量
③ 全量：warmup 前 N 条 + 逐条处理
   增量：只处理 checkpoint 后的新 bar
④ 更新 checkpoint
⑤ 收集信号 → 通过 Exchange 广播给内部策略 → 策略同步下单
```

**信号广播用 hub.execute**（非 hub.emit），保证回测中分析→策略→执行同步完成。

### 子类必须实现

| 方法 | 说明 |
|------|------|
| _warmup(symbol, interval, klines) | 用历史数据初始化内部状态 |
| _process_bar(symbol, interval, bar) | 处理单根 bar，返回信号列表 |

---

## RetracementService 实现

### 算法部分（algo.py / touch.py）

斐波那契回撤分析：K 线 → pivot 点 → 趋势腿 → 回撤组 → 关键位（0.236, 0.382, 0.5, 0.618, 0.786）

```
_warmup:
  全量 K 线 → compute_retracement() → 缓存回撤结构

_process_bar:
  ① 检测"触碰"：close 靠近关键位 → 产出信号（带 strength）
  ② 检测"突破"：close 穿过边界 → 重新计算结构
```

### 策略部分（FibStrategy）

FibStrategy 订阅 SignalEmitted 事件，是分析层内部的信号消费者：

```
on_signal:
  ① direction == "neutral" → 跳过
  ② strength < min_strength (0.6) → 跳过
  ③ 通过 → 构造 SubmitOrder → hub.execute → Broker
```

### 信号输出格式

```python
{
    "ratio": 0.618,
    "level_price": 1.234,
    "touch_price": 1.230,
    "direction": "long",
    "group_idx": 2,
    "strength": 1.5
}
```

### 缓存 key

| key 格式 | 内容 |
|---------|------|
| `__ckpt:{symbol}:{interval}` | checkpoint 时间戳 |
| `retracement:{symbol}:{interval}` | 回撤结构 |
| `_touch:{symbol}:{interval}` | 触碰去重 map |

---

## 每个子服务的 protocol 链

```
CacheLayer（内存缓存）
  └── SQLiteProtocol（磁盘）
         路径：cache/analysis/{alias}.sqlite
```

回测动态实例各自有独立路径：`cache/backtest/retracement_0/`、`cache/backtest/retracement_1/`

---

## 文件清单

| 文件 | 内容 |
|------|------|
| analysis/app.py | AnalysisEngine 抽象基类 |
| analysis/algo/retracement/service.py | RetracementService |
| analysis/algo/retracement/algo.py | 核心计算 |
| analysis/algo/retracement/touch.py | 触碰/突破检测 |
| analysis/algo/retracement/config.py | RetracementConfig |
| analysis/algo/retracement/models.py | TrendLeg / FibGroup |
| analysis/algo/retracement/command.py | ComputeRetracement HTTP 命令 |
| strategy/app.py | FibStrategy（分析层内部子策略） |
| models/signal.py | Signal / SignalEmitted |
