# Findings & Decisions

## Requirements
- 在 **timing** 项目下做一套**交易系统**，基础建立在 **bollydog** 核心库上。
- **首期效果**：
  1. 在给定时间范围内的 K 线上**先做 swing 识别、取出一笔趋势腿**，再计算该笔的**水平斐波那契回撤线**。
  2. 系统能接受**实时价格数据**，**判断价格是否触碰到回撤线**。

## Research Findings
- **NautilusTrader 架构**（[architecture](https://nautilustrader.io/docs/latest/concepts/architecture/)）：Kernel 编排、MessageBus 消息骨干、DataEngine 处理行情、ExecutionEngine 订单生命周期、RiskEngine 预检、Cache 高性能缓存；数据流与执行流分离；单线程内核 + 异步 I/O；DDD + 事件驱动 + Ports/Adapters。
- **bollydog（timing 内）**：`Hub` 为入口，内聚 `Router`（发布/订阅）、`Broker`（有序消息队列）、`Session`；消息为 `BaseCommand`/`BaseEvent`，经 `Broker.put` → 消费 `Broker.take` → `Router.publish` 分发；入口有 HTTP（Starlette）、WebSocket、CLI；适配器有 Redis、RDB、Neo4j、local、Elastic。
- **NautilusTrader 行情层**（[adapters](https://nautilustrader.io/docs/latest/concepts/adapters/)）：适配器含 HttpClient、WebSocketClient、InstrumentProvider、**DataClient**、ExecutionClient。DataClient 负责将交易所数据归一化为 Nautilus 类型，支持 **request**（如 request_instrument、request_bars，经回调返回）与 **subscribe**（如 subscribe_trade_ticks、subscribe_bars，实时回调 on_trade_tick、on_bar）。DataEngine 通过事件驱动接收 DataClient 推送并路由。
- **斐波那契水平回撤（正确画法）**：0% 和 100% 应为**同一笔趋势的起终点**（swing low → swing high 或 swing high → swing low），回撤线 = 起点价 + (终点价 - 起点价) * ratio。若用时间窗口内 max/min 则是「区间法」，非经典回撤。
- **Swing 识别**：swing high = 某根 K 的 high 在**左侧 N 根、右侧 N 根**内均为最高；swing low = 某根 K 的 low 在左右各 N 根内均为最低。得到高低拐点序列后，**选一笔腿**：相邻 swing_low→swing_high 为上涨腿，swing_high→swing_low 为下跌腿；首期可约定取「最近一笔」或按 direction 参数选。

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| 用 bollydog 的 Hub/Broker/Router 充当“消息骨干” | 与 Nautilus 的 MessageBus 角色对应，已有 Command/Event 与 HTTP/WS 入口 |
| 抽象 Data / Analysis / Execution / Risk 四块，首期只实现 Analysis（斐波那契+触线） | 架构可扩展，首期范围可控 |
| 触线判定用“价格与某条回撤线距离 ≤ 容差” | 避免浮点严格相等；容差可配置 |
| 规划文件放在 timing/ 根目录 | 符合 planning-with-files 的项目目录约定 |
| 首期 K 线来源用内存/列表，不强制 Redis | 先跑通计算与触线逻辑，数据源可后续换适配器 |
| 斐波那契比例首期写死常用集合，接口接受 ratios 列表 | 便于以后从配置或 UI 传入 |
| **先做 swing 识别再算斐波那契**，斐波那契输入为一笔的 (low, high) | 符合经典回撤判断标准，回撤线才有明确支撑/阻力含义 |
| Swing 参数：左右各 N 根判定拐点（如 left_bars=5, right_bars=5），选笔规则可配置（最近一笔涨/跌/指定方向） | Phase 2 定具体默认值 |
| **使用 uv 管理依赖与虚拟环境**：项目根 pyproject.toml、.python-version、uv venv / uv sync；.gitignore 含 .venv/ | 环境可复现；首期 dependencies=[]，bollydog 等放 optional-dependencies |

## Issues Encountered
| Issue | Resolution |
|-------|------------|
| (暂无) | - |

## Resources
- NautilusTrader Architecture: https://nautilustrader.io/docs/latest/concepts/architecture/
- NautilusTrader Adapters (DataClient): https://nautilustrader.io/docs/latest/concepts/adapters/
- timing 项目：`/Users/akulaku/3sy11/timing/`，核心库 `timing/bollydog/`
- **整体架构与行情接入层设计**：`timing/ARCHITECTURE.md`（单一事实来源，便于逐一检查）
- 规划技能：`.cursor/skills/planning-with-files/SKILL.md`

## Visual/Browser Findings
- 架构图已用 ASCII 形式写入 task_plan.md，便于在纯文本下维护与版本控制。
