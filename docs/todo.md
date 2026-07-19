# TODO: 统一规则引擎

## 目标

将 Analysis 和 Decision 模块中的 Rules 部分抽象为统一的规则引擎模式，使两者遵循相同的接口契约。

## 核心模式

```
输入: 一张由实验 ID 定位的 Parquet 表
配置: 特定规则的参数（profile / overrides）
输出: 规则处理后的结果表
```

## 当前状态

### Analysis Rules（已实现）

| 组件 | 路径 | 职责 |
|------|------|------|
| Rule Meta | `analysis/rules/{rule_name}/__init__.py` | 注册 name, upstream_algo, config_class, detect_fn |
| Config | `analysis/rules/{rule_name}/config.py` | 参数管理，支持 profile + overrides |
| Detect | `analysis/rules/{rule_name}/detect.py` | 纯检测逻辑，输入 DataFrame → 输出 signals |
| Profile | `analysis/rules/{rule_name}/profiles/*.toml` | 预设参数组合 |
| Registry | `analysis/rules/__init__.py` | RULE_REGISTRY 自动发现 |

**流程**: `read_structures(compute_id)` → `detect_fn(df, config)` → `write_signals(analysis_id)`

### Decision Rules（已实现，待统一）

| 组件 | 路径 | 职责 |
|------|------|------|
| Gates | `decision/strategies/{strategy_name}/rules.py` | 门禁函数链 `(signal, ctx, cfg) → (bool, reason)` |
| Sizing | `decision/strategies/{strategy_name}/rules.py` | 仓位计算 `(signal, ctx, cfg) → order_params` |
| Registry | `decision/strategies/__init__.py` | STRATEGY_REGISTRY |

**流程**: `read_signals(analysis_id)` → `gates + sizing` → `write_decisions(decision_id)`

## 统一设计方向

### 1. 统一接口

```python
class Rule(Protocol):
    name: str
    input_schema: str          # 输入表的 schema 标识
    output_schema: str         # 输出表的 schema 标识
    config_class: type         # 参数类（支持 profile + overrides）

    def run(self, input_df: DataFrame, config: dict) -> DataFrame:
        """纯函数：输入表 + 配置 → 输出表"""
        ...
```

### 2. 统一配置管理

- 每个 rule 有自己的 `profiles/` 目录
- 配置通过 `{rule_name}/profiles/{profile}.toml` 加载
- 支持 CLI overrides：`--override key=value`
- 实验 manifest 中记录完整 config snapshot

### 3. 统一实验追踪

```
compute_id → analysis_id → decision_id → execution_id
    ↓              ↓              ↓              ↓
 特征表         信号表         决策表        订单/成交表
```

每个环节：
- 输入：上游实验 ID 定位的 Parquet 表
- 输出：本层实验 ID 目录下的 Parquet 表 + manifest.json

### 4. 待实施改进

- [ ] 将 `decision/strategies/` 重构为 `decision/rules/` 结构，与 analysis 对齐
- [ ] 为 decision rules 增加 profile 机制（TOML 配置文件）
- [ ] 抽取公共 `RuleBase` 基类到独立 package
- [ ] 统一 config 加载逻辑（目前 analysis 用 `FibTouchConfig.from_profile`，decision 直接传 dict）
- [ ] 增加 rule 输入/输出的 schema 校验（与 `schema/registry.yml` 对接）
- [ ] 考虑 rule 组合（pipeline）：多个 rule 串联处理

## 优先级

1. **P1**: Decision rules profile 机制（使实验可复现）
2. **P2**: 目录结构对齐 `strategies/` → `rules/`
3. **P3**: 公共基类抽取 + schema 校验

---

## 待研究项

### Computation: Fib 重算触发机制

当前 `scan_bars` 按固定步长重算，不考虑市场状态。但直觉上：

- 一组 Fib 线被**突破**（价格穿出 leg 范围）后，该组线已失效，应触发重算
- 震荡趋势中 Fib 回撤有效；一旦突破进入单边行情，回撤策略可能失效
- 需要区分"回撤中的正常触碰"和"突破导致的失效"

**待设计**：
- [ ] 突破检测 → 标记 group 失效 → 触发重算
- [ ] 重算策略：失效后立即重算 vs 等待新拐点确认后再算
- [ ] 突破阈值：price > leg_high + tolerance 还是 price > 某个百分比

### Decision: 概率估计模型（方案 C）

用历史数据估计 `P(win) = f(bounce_rate, proximity, freshness, ...)`。

- 优点：数据驱动，精确
- 风险：样本量不足时过拟合（当前高 bounce_rate 样本仅 165 个）
- 待研究：是否有足够数据支持简单的逻辑回归 / 决策树模型
- [ ] 收集更多标的数据扩充样本
- [ ] 简单模型 vs 条件门禁的对比实验

### Decision: 期望收益模型（方案 D）

`E[R] = P(win) × avg_win - P(loss) × avg_loss > 0`

- 需要同时估计 P(win)、avg_win、avg_loss
- 可结合 MFE/MAE 分布来估计盈亏幅度
- 比纯胜率更合理：一个 40% 胜率但盈亏比 3:1 的策略好于 60% 胜率盈亏比 1:2 的
- [ ] 研究不同条件组合下的 MFE/MAE 分布特征
- [ ] 用期望收益替代胜率作为决策依据的可行性验证
