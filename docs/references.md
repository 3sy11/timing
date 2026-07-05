# 参考：实验追踪模式（MLflow / W&B）

> timing 的 `run_id` 统一设计借鉴了 MLflow/W&B 的实验追踪范式。
> 本文档记录核心概念映射和后续可借鉴的能力。

---

## 一、核心设计理念

### 传统方式 vs 实验追踪模式

```
传统设计:
  生产数据 → prod 表/库
  回测数据 → backtest 表/库
  对比 → 跨库 JOIN，维护两套逻辑

实验追踪模式（MLflow/W&B/timing）:
  所有数据 → 同一张表，run_id 区分
  "生产" = 当前激活的 run_id（active_run_id）
  "回测" = 批量生成多组 run_id
  "上线" = 把 active_run_id 指向表现最好的实验
  "回滚" = 切回旧的 run_id
```

### 为什么这样做

- 代码层没有 prod/backtest 分支，逻辑完全统一
- 对比实验 = 同表不同 run_id 的 WHERE/GROUP BY
- Grafana 用 `$run_id` 变量一键切换视角
- 回滚 = 改一个配置值

---

## 二、MLflow 核心概念

| 概念 | 说明 |
|------|------|
| **Experiment** | 一组相关的 Run，如 "retracement 参数调优" |
| **Run** | 单次执行，有唯一 run_id |
| **Parameters** | 本次 run 的输入配置（超参数） |
| **Metrics** | 本次 run 的输出指标（accuracy、loss 等） |
| **Artifacts** | 产出文件（模型、图表、数据文件） |
| **Tags** | 自由标签，用于分组/筛选 |
| **Model Registry** | 将最佳 run 的模型"注册"为生产版本 |

### MLflow 典型用法

```python
import mlflow

# 开始一次实验
with mlflow.start_run(run_name="retracement_v3"):
    # 记录参数
    mlflow.log_param("touch_tolerance", 0.35)
    mlflow.log_param("min_leg_span_pct", 0.05)
    mlflow.log_param("symbol", "159363.OF")

    # 执行计算...
    results = run_analysis(...)

    # 记录指标
    mlflow.log_metric("signal_count", len(results.signals))
    mlflow.log_metric("bull_ratio", results.bull_ratio)
    mlflow.log_metric("avg_strength", results.avg_strength)

    # 保存产出文件
    mlflow.log_artifact("results/signals.parquet")

# 对比多次实验
runs = mlflow.search_runs(
    experiment_ids=["1"],
    filter_string="metrics.signal_count > 100",
    order_by=["metrics.avg_strength DESC"]
)
```

### MLflow 参数扫描（Parent-Child Run）

```python
with mlflow.start_run(run_name="grid_search") as parent:
    mlflow.log_param("n_trials", 27)

    for params in param_grid:
        with mlflow.start_run(nested=True):
            mlflow.log_params(params)
            result = run_with_params(params)
            mlflow.log_metrics(result.metrics)

    # 记录最佳结果
    mlflow.log_param("best_run_id", best.run_id)
```

---

## 三、W&B (Weights & Biases) 补充

W&B 在 MLflow 基础上增强了：

| 能力 | 说明 |
|------|------|
| **Sweep** | 声明式参数搜索（grid/random/bayesian） |
| **Compare** | 自动生成 parallel coordinates / scatter 对比图 |
| **Tables** | 结构化数据表可视化（类似 DataFrame in UI） |
| **Alerts** | 指标异常自动通知 |
| **Reports** | 从实验数据自动生成分析报告 |

### W&B Sweep 声明式配置

```yaml
# wandb sweep config
method: grid
parameters:
  touch_tolerance:
    values: [0.2, 0.35, 0.5]
  min_leg_span_pct:
    values: [0.03, 0.05, 0.08]
  warmup_bars:
    values: [80, 120, 200]
metric:
  name: avg_strength
  goal: maximize
```

这就是 timing todo.md 中「方案 B：参数空间声明式配置」的原型。

---

## 四、timing 系统的概念映射

| MLflow/W&B | timing 当前实现 | 差距 |
|---|---|---|
| Experiment | 无（run_id 是扁平的） | 需要 experiment 分组 |
| Run | `runs` 表 (run_id, status, params) | 已有，params 是 JSON 字符串 |
| Parameters | backtest.toml 中的 config | 未自动记录到 runs 表 |
| Metrics | 无 | 需要自动 summary（信号数/胜率/强度） |
| Artifacts | bt_tmp/*.duckdb 或 parquet | 有文件，无注册机制 |
| Compare | 需手写 SQL | 需要自动对比视图 |
| Promote | active_run_id 配置 | 设计上支持，未实现切换 UI |
| Sweep | 手动编辑 TOML | 需要声明式参数空间 |
| Parent-Child | 无 | 可用 run_id 前缀约定模拟 |

---

## 五、后续可借鉴的方向

### 5.1 轻量实现（不引入 MLflow 服务器）

在 `runs` 表中扩展字段即可覆盖 80% 需求：

```sql
CREATE TABLE runs (
  run_id      VARCHAR PRIMARY KEY,
  experiment  VARCHAR,           -- 实验分组
  parent_id   VARCHAR,           -- 父 run（sweep 场景）
  created_at  BIGINT,
  status      VARCHAR,           -- running/completed/failed
  mode        VARCHAR,           -- backtest/live
  description VARCHAR,
  params      JSON,              -- 完整参数快照
  metrics     JSON,              -- 自动计算的摘要指标
  tags        JSON               -- 自由标签
);
```

### 5.2 自动 Metrics 计算

每次 run 完成后自动填充 metrics：

```python
metrics = {
    "signal_count": count_signals(run_id),
    "bull_ratio": bull_signals / total_signals,
    "avg_strength": avg(signals.strength),
    "symbols": list_of_symbols,
    "duration_sec": end_time - start_time,
}
update_run_metrics(run_id, metrics)
```

### 5.3 对比查询

```sql
-- 对比两个 run 的信号分布
SELECT run_id, symbol, direction, count(*), avg(strength)
FROM signals
WHERE run_id IN ('exp_a', 'exp_b')
GROUP BY run_id, symbol, direction;
```

### 5.4 Grafana 变量切换

```sql
-- Grafana 变量定义
SELECT DISTINCT run_id FROM runs
WHERE status = 'completed'
ORDER BY created_at DESC;
```

Dashboard 中所有 panel 用 `WHERE run_id = '$run_id'`，
一键切换查看不同实验结果。

---

## 六、是否需要引入 MLflow？

| 场景 | 建议 |
|------|------|
| 当前阶段（< 100 runs） | 不需要，runs 表 + Grafana 够用 |
| 需要 UI 对比 + 自动图表 | 考虑 MLflow Tracking Server |
| 需要 bayesian 参数搜索 | 考虑 Optuna + MLflow |
| 团队协作 / 共享实验 | MLflow + 远程 backend store |
| 需要模型版本管理 | MLflow Model Registry |

当前 timing 项目建议：**先把 runs 表的 metrics/params 做丰富**，
等实验量上来后再考虑引入 MLflow 服务器。核心设计（run_id 统一）
已经和 MLflow 思路对齐，迁移成本很低。

---

## 七、计算模块实验管理方案对比（2026-07）

> 计算模块（Computation）需要在每个 `compute_id` 目录中记录实验变量，
> 以便追溯参数、对比不同配置的计算结果。以下是调研的方案对比。

### 7.1 候选方案

| 方案 | 代表工具 | 核心模式 | 适合规模 |
|------|---------|---------|---------|
| **A. JSON manifest in directory** | Hydra outputs/、expbox artifacts/ | 每次实验在产出目录写入 config snapshot | 小/中 |
| **B. SQLite/DuckDB tracking** | Beacon、自建 runs 表 | 统一追踪表记录 params + metrics | 中 |
| **C. Git-anchored snapshot** | DVC params.yaml、expbox | Git commit 绑定 + 参数文件追踪 | 中/大 |
| **D. Tracking Server** | MLflow、W&B | 独立服务存储、UI 对比、API 查询 | 大 |
| **E. Hydra-style compose** | Hydra、hydra-zen、Beacon | 声明式 config 组合 + 自动输出目录 | 中/大 |

### 7.2 方案详细分析

#### A. JSON Manifest in Directory（选定）

**代表**：Hydra 的 `outputs/{date}/{time}/.hydra/config.yaml`、expbox 的 `results/{exp_id}/artifacts/config.yaml`

```
computation/fib_retracement/base_v1/
├── manifest.json          # 完整参数 + 元数据
├── step1_pivots_*.parquet
└── result_*.parquet
```

- 优点：零依赖、自包含、可 `jq`/`diff` 查看、DuckDB `read_json` 批量查询
- 缺点：无 UI、需手动编排写入逻辑
- 适合：当前阶段（实验量 < 100，单人开发）

#### B. SQLite/DuckDB Tracking

**代表**：Beacon 的内置 SQLite tracking、timing 现有 `runs` 表

```python
# Beacon 风格
tracker.log_run(config=cfg, metrics=result, tags=["fib", "v1"])
```

- 优点：统一查询、可 JOIN 对比、Grafana 可视化
- 缺点：与文件系统分离（需两处看）、需维护 schema
- 适合：中期（实验量 100+，需要快速筛选最优参数）

#### C. Git-Anchored Snapshot

**代表**：expbox 的 git commit hash 记录、DVC `params.yaml` 追踪

```json
{"git_commit": "abc1234", "git_branch": "feat/fib-v2", "dirty": false}
```

- 优点：完整代码溯源（参数 + 代码版本绑定）
- 缺点：要求干净 commit、频繁实验时 git 历史膨胀
- 适合：需要精确复现的正式实验

#### D. Tracking Server（MLflow/W&B）

- 优点：UI 对比、团队共享、自动图表、Bayesian sweep
- 缺点：部署维护成本、引入重依赖
- 适合：团队协作、实验量 1000+

#### E. Hydra-Style Compose

**代表**：Hydra config groups、hydra-zen、Beacon `compose_hierarchy`

```yaml
# config group: computation/fib_retracement
defaults:
  - base
  - override: tight
```

- 优点：声明式组合、自动覆盖、config 继承
- 缺点：引入框架约束、学习成本
- 适合：参数维度多（10+）、需要组合爆炸管理

### 7.3 选型决策

**当前选定方案 A（JSON manifest）**，理由：

1. 与现有目录结构天然契合（`computation/{algo}/{compute_id}/`）
2. 不引入新依赖（项目当前无 MLflow/Hydra）
3. DuckDB 可直接 `read_json('*/manifest.json')` 批量查询
4. 可平滑演进：manifest 数据可作为 runs 表的数据源（方案 B 升级路径）

**升级路径**：

```
阶段 1（当前）: manifest.json per directory
    ↓ 实验量增长
阶段 2: manifest.json + 汇总到 DuckDB runs 表（自动 ingest）
    ↓ 需要团队协作/UI
阶段 3: 引入 MLflow Tracking Server（manifest 作为 artifact 保留）
```

### 7.4 参考链接

- [expbox](https://github.com/mizuno-group/expbox) — 轻量本地实验盒子，Git-anchored + config snapshot
- [Beacon](https://pypi.org/project/beacon-python/) — Hydra 风格组合 + SQLite tracking，MultiScope 命名空间隔离
- [hydra-zen](https://github.com/mit-ll-responsible-ai/hydra-zen/) — 无 YAML 的 Hydra，Python dataclass 生成配置
- [config_spec](https://github.com/dibyaghosh/config_spec) — 轻量 JSON-serializable config，类似 Hydra instantiate
