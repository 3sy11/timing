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
