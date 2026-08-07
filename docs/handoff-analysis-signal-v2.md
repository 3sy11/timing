# Handoff：Analysis v2 信号产出与降噪设计

> 日期：2026-08-07  
> 范围：`timing` 项目 — Fib 触线 Analysis 层改造、Grafana 信号时间轴、信号降噪方案讨论  
> 下一会话建议焦点：**实现方案 B（价位簇合并）或方案 A（周期内择向），再视效果推进 Decision**

---

## 1. 当前状态（已完成）

### 1.1 Analysis v2（纯定量）

已落地，Decision 层**尚未**按新设计改造。

| 项 | 说明 |
|----|------|
| 核心文件 | `timing/analysis/rules/fib_touch/detect.py`、`config.py` |
| Profiles | `profiles/standard.toml`（`proximity_k=0.12`）、`profiles/wide.toml`（`0.20`） |
| 生成脚本 | `timing/tmp/run_ana_v2.py` |
| 实验映射 | fib001→ana001/ana002；fib002→ana003/ana004；fib003→ana005/ana006 |
| 品种 | `885003.WI` / `1d` |

**Schema（`signals.parquet`）**：`ts, close, multiplier, direction, ratio, level_price, distance, proximity, bounce_rate, touch_count, volume_ratio, consensus, approach, score_derived`

要点：

- 全部 7 线参与测量；不再有 `type=touch|breakout|warning`
- `score_derived` = 加权衍生分（权重在 config：`w_proximity/w_bounce/w_volume/w_consensus/w_ratio`）
- Analysis 只做测量；定性门禁留给 Decision（未做）

### 1.2 Grafana 信号时间轴

看板 UID：`bfr3s4obyazggb`（斐波那契水平回撤实验数据筛选）  
元数据库：`timing/infra/grafana.db`（compose 挂载，非 warehouse 内副本）

已修：

1. 旧 SQL 依赖已删除的 `type` 字段 → 改为 `score_derived`
2. `epoch_ms($__timeFrom())` 与 DuckDB 宏不兼容 → 改为 `to_timestamp(ts/1000) >= $__timeFrom()`
3. 展示：按日合并；`up` 为正、`down` 为负做净和；柱 = 日净和，线 = 5 日均线

### 1.3 观察到的问题（驱动本次设计讨论）

- 6 组 Fib（3 周期 × up/down）平权出信号 → **同日多空并存极常见**（约 60%+ 交易日）
- `direction` 是 **leg 来源方向**，不是交易方向；Grafana 用 up+/down− 净和只是权宜展示
- 用户反馈：6 线信号过杂，希望收敛产出逻辑

---

## 2. 设计结论（讨论共识，未实现）

**Computation 继续保留 6 组**（主图、失效、`better_fit` 需要双侧候选）。  
**噪声应在 Analysis 输出粒度上收敛**，不要在 Computation 砍对向腿。

### 方案对照

| 方案 | 做法 | 状态 |
|------|------|------|
| **A 周期内择向** | 每个 `multiplier` 只留 up 或 down 一组再测线（6→3） | 建议可选；未实现 |
| **B 价位簇合并** | 输出单元改为「价格带」，同价位多线并成一条 | **用户已要求详细解释；优先候选** |
| C 每周期只留最佳触线 | 每 multiplier 一条最高分 | 适合对照实验 |
| D 符号绑 approach | 正负用接近方向，不绑 leg.direction | 可与 A/B 叠加 |

推荐落地顺序（先前会话建议）：

```
Computation 6 组不变
  → Analysis 可选 A（周期择向）
  → 再 B（价位聚类成带）
  → 符号优先 approach；leg.direction 作特征
  → Decision：跨周期同号 consensus 再下单
```

---

## 3. 方案 B 详解（下一会话重点）

### 问题

当前粒度 = **每根 Fib 线 × 每次靠近 → 一条信号**。  
同一支撑/阻力带可被短-up、中-down、长-up 等多根线同时描述 → 多条正负信号。

### 目标粒度

**每一个价格带（簇）→ 一条信号**（可选：只保留离 close 最近的一簇）。

### 流程（概念）

1. 与现逻辑相同：`measure_proximity` 收集感知半径内所有触线记录  
2. 按 `level_price` 排序；相邻 `|p1-p2| ≤ tol` 则并簇（`tol` 可用 `leg_range × k`）  
3. 每簇输出一行：`zone_price`、`consensus`、综合 `score`、`hit_groups`、`bias`  
4. 正负号候选：  
   - **approach**（from_above/from_below）— 更贴近盘面  
   - 簇内 up/down 加权得分投票  
   - 或 Analysis 只出正强度，方向全交 Decision  

### 与现有 `consensus` 的关系

`detect.py` 里已有 `compute_consensus`，但是**每条线的附属字段**。  
方案 B 是把「价位共振」升级为**信号主键/输出单元**。

### 与方案 A 的差别

- A：先减少参与的组（按周期选方向）  
- B：不先砍组，按价位合并描述同一块墙  
- 可叠加：先 A 再 B

对照实验建议：同一 `fib001` 上 `raw_6` / `pick_dir_per_mult` / `price_cluster` 三组 ana，比密度、同日冲突率、后续 Decision 胜率。

### 小例子

```
close=5120
  5103/5107/5112 → 簇A（短up+长up+中down）→ 一条高 consensus 信号
  5200           → 簇B（短down）→ 弱/可丢
图上不再「同日又多又空的一堆柱」，而是「这块区域有多强」
```

---

## 4. 未完成 / 待办

- [ ] Decision 层（观察期状态机、止损/目标价、R:R、连续仓位）— 用户明确说过先不做  
- [ ] 方案 A 或 B 的代码实现 + 重生 ana 数据  
- [ ] Grafana 信号时间轴适配新 schema（簇级或择向后）  
- [ ] `run_all.py` 与 Decision/旧 profile（`gate_*`）对齐清理  
- [ ] research 中 proximity/bounce 预测力用 v2 数据重验（见 `timing/docs/research.md` §3）

---

## 5. 关键路径速查

| 用途 | 路径 |
|------|------|
| 触线检测 | `timing/analysis/rules/fib_touch/detect.py` |
| 分析配置 | `timing/analysis/rules/fib_touch/config.py` |
| Ana 批跑 | `timing/tmp/run_ana_v2.py` |
| Fib 计算管线 | `timing/computation/algo/fib_retracement/pipeline.py` |
| 参数研究 | `timing/docs/research.md` |
| 架构设计 | `timing/docs/design.md` |
| Grafana 注意点 | `timing/infra/grafana/grafana.md` |
| Compose / DB | `timing/infra/docker-compose.yml`、`timing/infra/grafana.db` |
| 信号数据 | `warehouse/timing/signals/ana00{1-6}/885003.WI/1d/` |

---

## 6. 运维备忘

- Grafana：`http://localhost:3000`，容器名 `infra-grafana-1`，datasource UID `PF6BE4C1702A928CD`（TimingDuckDB）  
- 本地跑脚本用 `timing/.venv/bin/python`（系统 `python3` 无 duckdb）  
- 勿把账号密码写入文档；沿用既有 infra 配置

---

## 7. Suggested skills

下一会话开始前建议读取并遵循：

1. **`/Users/akulaku/3sy11/.cursor/skills/planning-with-files/SKILL.md`** — 若落地方案 B/A 步骤较多，先建 `task_plan.md` / `findings.md` / `progress.md`  
2. **`/Users/akulaku/.claude/skills/karpathy-guidelines/SKILL.md`** — 改 `detect.py` 时手术式修改，并定义可验证成功标准（信号密度、同日冲突率）  
3. **`user-context7`（MCP）** — Grafana / DuckDB 插件行为需查最新文档时使用  
4. 仓库约定：临时产物落 `tmp/`；未要求不 commit；回复中文  

若用户明确要求「实现方案 B」：Agent 改 `detect.py` + profile + `run_ana_v2.py` 重生数据，并更新看板第二张图 SQL。
