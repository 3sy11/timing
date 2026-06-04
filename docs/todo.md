# timing 系统设计复盘 & 优化计划

> 基于回测功能的完整实现过程，复盘系统设计和框架使用中的不合理之处，规划后续优化方向。

---

## 一、timing 应用层问题

### 1.1 框架定位与计算任务的错配

`bollydog` 为长驻微服务设计（HTTP、消息总线、事件驱动），回测本质是**批计算任务**。
每次 `execute` 启动完整服务栈（Exchange、Session、Queue）只为跑一个 for 循环：

```
启动耗时 ~3s → 实际计算 30s → os._exit(0)
```

为绕过框架限制，已经做了以下 hack：
- 创建 `config_backtest.toml` 去掉不相关服务
- `_ensure_data` 判断 DataEngine 是否存在来决定行为
- 从 parquet 直读而非走 hub 消息获取数据
- `SimulatedClock` 替代框架时钟
- `svc._process_bar()` 直调绕过 hub 消息路由

**本质**：回测执行路径完全绕过了框架核心能力（消息路由、事件驱动），框架变成纯启动开销。

### 1.2 两阶段操作增加认知负担

```bash
# 用户必须记住三步
1. 编辑 TOML（手动管理参数组合）
2. execute RunBacktest（可能开多个终端）
3. execute MergeBacktest（必须记得执行）
```

- 忘记 merge → 结果散落在 `bt_tmp/`
- merge 前改了 schema → 合并可能失败
- 无法一键 "跑完整流程"

### 1.3 参数探索没有一等支持

当前做参数扫描：
- 手动维护 N 个 TOML 文件
- 或在一个 TOML 里堆 21 个 `[[services]]`（可读性差）
- 没有 grid search / 随机搜索的抽象
- 无法表达 "对所有品种，分别用这 3 套参数" 的声明式意图

### 1.4 结果分析链路断裂

回测产出 → DuckDB → Grafana，但：
- 缺少 **run 级别的元数据对比**：哪个 run 用了什么参数、哪个表现好
- 没有 **对比视图**：想看 run_a vs run_b 的信号分布差异，需手写 SQL
- **信号→决策→订单** 的因果链在数据库中只是扁平表，没有追溯能力
- 每次都要看原始 signals 表，缺少自动 summary

### 1.5 DuckDB 单文件天花板

单个 `.duckdb` 文件 = 单写者锁。在下列场景出问题：
- 多进程并行写（已绕过，用临时文件）
- 生产 + 回测同时运行（互斥）
- 外部工具（DBeaver）打开时无法运行任何命令

---

## 二、bollydog 框架层问题

### 2.1 execute 模式无轻量启动

```python
# bollydog/entrypoint/cli/__init__.py:72-87
async def _run():
    async with hub:  # ← 启动完整服务栈
        await asyncio.wait_for(hub.execute(msg), timeout=timeout)
```

`async with hub` 会触发 `Hub.on_started()` → 遍历启动所有 `AppService._apps`：

```python
# bollydog/service/app.py:67-71
async def on_started(self):
    for key, svc in list(AppService._apps.items()):
        if svc is self: continue
        await svc.maybe_start()
```

**问题**：
- `execute` 不支持 `--domains` 过滤（`service` 模式支持）
- 即使只跑一条命令，也启动 Exchange 订阅扫描、Session、Queue worker
- 无 `--headless` / `--minimal` 开关
- `testing.py` 有 `run_command`（不走 Hub），但 CLI 没有对应入口

### 2.2 超时机制三层叠加且默认值冲突

| 层级 | 默认值 | 来源 |
|------|--------|------|
| CLI `timeout` | **300s** | `execute(timeout: int = 300)` |
| `message.expire_time` | **3600s** | `COMMAND_EXPIRE_TIME` 环境变量 |
| 用户 `ClassVar` | **无效** | `expire_time: ClassVar[int] = 7200` 不参与 Pydantic |

**陷阱**：`ClassVar` 声明的 `expire_time` 实际不生效（Pydantic 排除 ClassVar），
正确写法是 `expire_time: float = 7200`。测试代码用的就是实例字段。

三层超时中谁更严谁先触发：
- CLI 300s 先于 expire 3600s → 用户不传 `--timeout 7200` 则 5 分钟被杀
- 框架没有 "批处理超时策略" 的统一设计

### 2.3 双轨执行路径不可避免

| 路径 | 用于 | 特点 |
|------|------|------|
| `hub.dispatch` → Queue → Exchange | 生产 / 实时 | 异步、有订阅链、有超时重试 |
| `svc._process_bar()` 直调 | 回测 / 批处理 | 同步循环、绕过 hub、无事件广播 |

框架未提供官方的 "直调 AppService 方法" 模式，导致：
- 回测代码依赖 Service 的私有方法 (`_process_bar`, `_warmup`)
- 生产链路（bar → Event → subscriber → signal）和回测链路是两套代码
- 算法修改后需确保两条路径行为一致

### 2.4 DuckDB 多进程非一等公民

框架层 `DuckDBProtocol`：
- `duckdb.connect(self.url)` 在 `on_start` 时拿写锁
- `_run` 用 `asyncio.to_thread` → 与 DuckDB 连接线程安全冲突
- 无 read-only / 内存 / per-run 连接策略
- 无 fork-safe 或连接工厂设计

应用层不得不：
- 覆写 `_run` 为同步执行（避免 "No open result set"）
- 手工管理临时文件 + Merge（避免文件锁）
- `_shared` 单例不跨进程，完全依赖应用层协调

### 2.5 `os._exit(0)` 硬退出

```python
# bollydog/entrypoint/cli/__init__.py:87
os._exit(0)
```

跳过正常 shutdown 钩子，DuckDB WAL、日志 flush 可能来不及完成。

### 2.6 AppService 全局注册无隔离

`AppService._apps` 是进程级 `ClassVar` dict，所有 `load_from_config` 实例化的服务共享同一命名空间。
在测试或多配置场景下需要 `reset_shared()` 之类的清理，框架没有提供官方 teardown。

---

## 三、优化方案

### 方案 D (P0)：Parquet 输出 + glob 查询

**投入**：半天 | **收益**：彻底消除 DuckDB 锁、merge 步骤

将回测结果直接写 Parquet 文件（一个 run = 一组 parquet），完全无锁：

```
warehouse/results/
  exp_a/signals.parquet
  exp_a/analysis.parquet
  exp_b/signals.parquet
  exp_b/analysis.parquet
```

查询时用 DuckDB 的 `read_parquet` with glob 动态聚合：

```sql
SELECT * FROM read_parquet('warehouse/results/*/signals.parquet', filename=true)
WHERE run_id = 'exp_a';
```

**好处**：
- 零锁冲突，写入就是写文件
- 不需要 merge 步骤（`read_parquet` 就是 "虚拟合并"）
- 文件级版本管理（rsync / 清理方便）
- Grafana DuckDB 插件原生支持

### 方案 A (P1)：独立 Runner 脚本

**投入**：1 天 | **收益**：去掉框架开销，启动快 10×

将回测从 bollydog 框架中完全独立：

```python
# timing/backtest/runner.py — 不依赖 bollydog
def run_backtest(config_path: str, run_id: str, ods_dir: str, output_dir: str):
    """纯函数式回测：读 parquet → 计算 → 写 parquet"""
    conf = tomllib.load(open(config_path, 'rb'))
    for svc_conf in conf["services"]:
        svc = create_analysis_service(svc_conf)
        klines = load_from_parquet(svc_conf["symbol"], ods_dir)
        results = replay(svc, klines, svc_conf["warmup_bars"])
        write_parquet(results, f"{output_dir}/{run_id}/")
```

**好处**：
- 启动零开销（无 hub/exchange/http）
- 天然支持 `multiprocessing.Pool`
- 可被 Jupyter / Airflow / 任何调度器调用
- 测试更简单（纯函数，无框架依赖）

**代价**：生产和回测共享算法类但不共享执行引擎，需维护两套调用入口。

### 方案 C (P1)：自动 Summary

**投入**：半天 | **收益**：结果可用性大幅提升

每次 run 完成后自动生成 summary：

```sql
CREATE TABLE run_summary AS
SELECT run_id, symbol, source,
       count(*) as signal_count,
       avg(strength) as avg_strength,
       count(CASE WHEN direction='bullish' THEN 1 END) as bull_signals,
       min(ts) as first_signal_ts, max(ts) as last_signal_ts
FROM read_parquet('warehouse/results/*/signals.parquet')
GROUP BY run_id, symbol, source;
```

Grafana 中直接看 summary 对比，不用写复杂 SQL。

### 方案 B (P2)：参数空间声明式配置

**投入**：1 天 | **收益**：批量实验体验质变

TOML 改为声明式参数空间，自动展开笛卡尔积：

```toml
[sweep]
run_id_prefix = "grid_exp"
symbols = ["159363.OF", "510300.OF", "513090.OF"]
interval = "1d"
warmup_bars = [80, 120, 200]

[sweep.config]
touch_tolerance = [0.2, 0.35, 0.5]
min_leg_span_pct = [0.03, 0.05, 0.08]
```

引擎自动展开为 3×3×3=27 组实验，自动分配 run_id，自动并行。

---

## 四、bollydog 框架建议改进

### 4.1 CLI：增加 `execute --minimal`

```python
def execute(command, timeout=None, minimal=False, **kwargs):
    if minimal:
        # 仅 import 目标 App + 必要 Protocol，不启 Hub 消费者
        return run_command(cmd)
    ...
```

或支持 `execute --domains backtest,data` 过滤启动范围。

### 4.2 超时：统一为单层

- CLI `timeout` 默认值改为 `None`（无限等待）
- 以 `message.expire_time` 为唯一超时控制点
- 或：CLI 默认继承 `COMMAND_EXPIRE_TIME` 环境变量

### 4.3 批处理 Profile

```python
load_from_config(config, profile="batch")
# batch profile: 只启动 Hub + 目标 App，跳过 Exchange 订阅
```

### 4.4 DuckDB Protocol 增强

- 提供 `read_only=True` 连接模式
- 提供 `per_run` 连接工厂（不走单例）
- 文档化 "一进程一文件" 模式
- `_run` 默认行为对 DuckDB 应为同步（嵌入式引擎）

### 4.5 官方 BatchRunner / 直调模式

让 "直调 Service 方法" 成为一等公民而非 hack：

```python
class BatchRunner:
    """直调 Service 方法，不走 Hub/Queue/Exchange。"""
    def __init__(self, app_service):
        self.app = app_service

    async def call(self, method_name, **kwargs):
        method = getattr(self.app, method_name)
        return await method(**kwargs)
```

### 4.6 graceful shutdown

`execute` 完成后走正常 `hub.stop()` 而非 `os._exit(0)`，确保 WAL flush、日志完成。

---

## 五、执行优先级

| 优先级 | 改动 | 位置 | 投入 | 目标 |
|--------|------|------|------|------|
| **P0** | Parquet 输出替代 tmp DuckDB | timing/engine/command.py | 半天 | 消除锁 + 消除 merge |
| **P1** | 独立 runner 脚本 | timing/backtest/runner.py | 1天 | 去框架开销 |
| **P1** | 自动 run_summary | timing/engine/command.py | 半天 | 结果易用 |
| **P2** | 参数空间声明 | timing/backtest/sweep.py | 1天 | 批量实验 |
| **P2** | bollydog execute --minimal | bollydog/entrypoint/cli | 1天 | 框架层修复 |
| **P3** | bollydog 超时统一 | bollydog/models/base.py | 半天 | 避免误杀 |
| **P3** | bollydog DuckDB Protocol 增强 | bollydog/adapters | 1天 | 多进程友好 |

---

## 六、当前状态

- [x] 回测功能已实现（多进程并行 + 临时文件 + merge）
- [x] 多品种多参数配置支持
- [ ] P0: Parquet 输出模式
- [ ] P1: 独立 runner（脱离框架）
- [ ] P1: 自动 summary
- [ ] P2: 参数空间声明
- [ ] P2-P3: bollydog 框架改进
