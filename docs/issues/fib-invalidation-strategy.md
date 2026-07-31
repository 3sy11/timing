# Fib 失效后重算策略研究

## 1. 现状诊断

### 1.1 数据事实（fib001, 885003.WI, 日线）

| 指标 | 值 |
|------|-----|
| 总记录 | 1590 (265扫描点 × 6组) |
| 仍有效（未提前失效） | 72.3% |
| boundary_break 提前失效 | 26.5% |
| stale（无触线）提前失效 | 0.9% |
| better_fit 重算 | **0%（代码路径不存在）** |

### 1.2 当前架构

```
每 scan_bars=20 bar → 无条件全量重算 3×2 组
   ↓
周期内逐 bar 检测失效条件 → 打标记 invalidated_ts
   ↓
失效后什么都不做，等下一个扫描点才有新组
```

### 1.3 问题

- **空窗期**：提前失效后平均空窗 15~17 天（中位周期间隔 28 天），这期间该(mult,dir)无有效 fib
- **stale 近乎无用**：阈值 `stale_n = recent_bars × mult × 0.2` 对 fib001 短期 = 12 bar，而 scan_bars=20，stale 刚要触发时新周期已到
- **失效 ≠ 重算**：失效只是过滤，不是生成器——100% 的新组来自固定周期

---

## 2. 行业参考

### 2.1 经典 Fibonacci 失效规则（交易实践共识）

| 条件 | 含义 | 行动 |
|------|------|------|
| 收盘穿越 78.6% | 趋势回撤过深，结构存疑 | 警告/减仓 |
| **收盘穿越 100%（swing origin）** | 原始趋势腿被完全否定 | **立即作废，重新锚定新 swing** |
| 价格超出 swing 15%+ | 老 fib 结构性过时 | 丢弃，用新极值重画 |

**核心原则**：一旦 100% anchor 被穿，该组 fib 的结构意义消失，应立即用"新确认的 swing 极值"重建。

### 2.2 自适应 Fibonacci 指标的通用算法（TradingView 工具参考）

1. **Swing 检测**：`ta.pivothigh / ta.pivotlow`，Lookback=10~20 bar，需左右确认
2. **质量门控**：Swing 幅度 ≥ ATR × 1.5，过滤噪声腿
3. **Delete-before-Create**：新 qualified swing 出现时，删旧网格，立刻重建
4. **多时间框架叠加**：同品种在日/周级别各维持一套独立 fib，寻找 confluence

### 2.3 与我们系统的对应关系

| 行业概念 | 我们的实现 |
|---------|-----------|
| swing 检测 | `tag_pivots` + `tag_zigzag` + `compute_confidence` |
| ATR 幅度门控 | `min_leg_span_pct` + `cluster_tolerance_pct` |
| 周期性重画 | `scan_bars` 固定周期全量计算 |
| 失效触发 | `boundary_break` / `stale`（只标记不重算）|
| Delete-before-Create | **缺失** ← 这是核心差距 |

---

## 3. 方案设计

### 3.1 核心思路：失效即刻重算（事件驱动） + 固定周期兜底

```
固定周期扫描点 → 无条件全量计算 3×2 组（基础产线，保证覆盖）
        +
逐 bar 失效检测 → 失效时立刻对该 (mult, dir) 调用 _compute_fib_at（事件产线）
```

两条产线产出的记录都写入 result，Grafana 根据 `effective_ts` 自然取最新。

### 3.2 失效条件精化

| 条件 | 当前实现 | 建议调整 | 理由 |
|------|---------|---------|------|
| boundary_break | 连续 N=2 根 bar close 超出 [leg_low, leg_high] | **改为突破 0%/100% 扩展线**，而非 leg 端点 | 我们的 0%/100% 就是 swing origin；内层线超出不算失效，只有 anchor 被穿才算 |
| stale_no_touch | N = recent_bars × mult × 0.2 bar 无触线 | **保留但调大比例为 0.5**（短期=30bar, 中期=60, 长期=90） | 当前 0.2 太小导致几乎不触发；调大后提供"市场忽视"信号 |
| better_fit（新增） | 不存在 | **新 swing 出现时主动检查**：如果新聚类 fit score 超出当前 score × 1.3，则替换 | 捕获结构性变化 |
| trend_reversal（新增） | 不存在 | **ZigZag 方向翻转**：当 zigzag 确认了与当前组方向相反的新 swing | 最强的结构否定信号 |

### 3.3 重算策略

失效触发后的重算不是简单重复 `_compute_fib_at`，需要区分场景：

| 场景 | 重算方式 | 说明 |
|------|---------|------|
| boundary_break（价格突破 anchor） | **以当前 bar 为终点重新 fit**，窗口缩短为 recent_bars × 0.5（只看最新结构） | 价格突破说明旧结构过时，用更短窗口捕获新结构 |
| stale（长期无触线） | **保持原窗口长度重新 fit** | 市场忽视≠结构翻转，可能只是 range shift |
| better_fit（更优聚类出现） | **直接用新 fit 替换** | 渐进式进化，不需要特殊窗口 |
| trend_reversal | **翻转方向重新计算** | 比如旧组是 up，zigzag 翻为 down swing 后重算 down 方向 |

### 3.4 防抖机制

防止频繁失效→重算→又失效的震荡：

- **冷却期**：同一 (mult, dir) 在重算后至少 `cooldown = scan_bars / 2` 根 bar 内不再触发失效检测
- **最小存活**：新生成的组至少存活 `min_alive = 5` 根 bar 后才开始失效检测
- **单日上限**：同一 (mult, dir) 在同一扫描周期内最多重算 2 次

### 3.5 数据标记

为了追踪和分析，result 中增加字段：

| 字段 | 含义 |
|------|------|
| `source` | `"periodic"` / `"event_break"` / `"event_stale"` / `"event_better"` / `"event_reversal"` |
| `parent_eff_ts` | 如果是事件重算，记录被替换的旧组的 effective_ts |

Grafana 验证表可按 `source` 筛选，验证事件驱动产线的质量。

---

## 4. 实施计划

### Phase 1：核心改动（pipeline.py）

1. 在 `for bi in range(sp + 1, next_sp + 1)` 的失效检测循环中，失效后调用 `_compute_fib_at` 生成新组
2. 新组插入 `all_records` 并更新 `active` 状态，后续 bar 对新组做检测
3. 增加 `source` 字段

### Phase 2：失效条件调整

1. 修改 `check_invalidation`：boundary 判断改为检查 0%/100% 价格（而非 leg_high/leg_low）
2. 增加 `stale_ratio` 到 0.5
3. 增加 `better_fit` 逻辑：每 `check_interval` bar 对比当前 fit 与新 fit 的 score

### Phase 3：防抖 + trend_reversal

1. 加入 cooldown / min_alive / 单日上限
2. 增加 zigzag 翻转检测

### Phase 4：Grafana 可视化

- 验证表增加 `source` 列筛选
- 图表上对事件驱动产出的 fib 用不同标记（如半透明色）以便对比

---

## 5. 预期效果

| 指标 | 当前 | 预期 |
|------|------|------|
| 空窗期（失效后到新组生效） | 15~17天 | **0天**（立刻重算） |
| 事件驱动产出占比 | 0% | 预计 20~30% |
| stale 触发率 | 0.9% | 预计 5~10%（调大阈值后） |
| 新组对市场结构的响应延迟 | 最多 28 天 | 最多 2~3 bar（检测+冷却） |

---

## 6. 风险与注意事项

1. **过拟合风险**：事件驱动频繁重算可能导致 fib 线"追涨杀跌"——用短窗口算出的结构可能只是噪声
2. **计算量**：每次失效触发一次 `_compute_fib_at`（含聚类计算），需确认性能可接受
3. **回测一致性**：事件驱动的 fib 依赖前序 bar 的状态，需确保回测时严格按时间顺序计算，不引入前视偏差
4. **参数膨胀**：新增 cooldown / min_alive / better_fit_ratio 等参数，需要合理默认值

---

## 7. 决策点（待讨论）

1. **boundary_break 的判断基准**：用 `leg_high/leg_low`（腿端点）还是用 `0%/100% 价格`（扩展后的 anchor）？
   - 当前：用 leg 端点 → 内层线刚偏离就可能触发
   - 建议：用 0%/100% 价格 → 只有 anchor 被穿才算真正失效

2. **重算窗口长度**：突破后是用 `recent_bars × mult`（原长）还是缩短？
   - 缩短（× 0.5）：更快适应新结构，但可能拟合噪声
   - 原长：稳健但可能算出和旧组相似的结果

3. **是否需要 trend_reversal**：还是让 boundary_break 自然覆盖这种情况？
   - boundary_break 本质就是"价格否定了原 swing"
   - trend_reversal 是更明确的"结构确认翻转"（zigzag 完成）

4. **stale 的实际意义**：如果市场无视所有 fib 线（横盘整理），重算是否有意义？
   - 可能重算出来的新 fib 依然被无视
   - 替代方案：stale 时不重算，而是降低该组权重/置信度
