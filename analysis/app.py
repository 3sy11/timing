"""
AnalysisEngine — 所有分析子服务的抽象基类。

【调用链路】
  生产模式：外部推送 bar → PushBars 命令执行 → _publish 广播 → Exchange 匹配 subscriber → on_bar
  回测模式：RunBacktest 命令 → exchange.match 找到 on_bar handler → hub.execute 同步执行

【子类要做的事】
  1. 继承本类，设置 alias（如 "RetracementService"）
  2. 实现 _warmup(symbol, interval, klines) — 用历史数据初始化内部状态
  3. 实现 _process_bar(symbol, interval, bar) — 处理单根 K 线，返回信号列表

【数据持久化】
  self.protocol 是一条链：CacheLayer(内存) → SQLiteProtocol(磁盘)
  - TOML 配置了 protocol 时，框架自动构建
  - 未配置时，on_start 中创建默认链（路径 = cache_path/alias.sqlite）
  - checkpoint 和缓存数据全部通过 self.protocol.get/set 读写
"""
import os, logging
from typing import ClassVar
from mode.utils.imports import smart_import
from bollydog.models.service import AppService
from bollydog.adapters.composite import CacheLayer
from bollydog.adapters.memory import SQLiteProtocol
from bollydog.globals import hub
from timing.data.models import GetKlines
from timing.models.signal import SignalEmitted

log = logging.getLogger(__name__)

# 时钟类型：生产用 LiveClock，回测可通过环境变量切换为 SimulatedClock
CLOCK_MODULE = os.environ.get("TIMING_CLOCK", "timing.common.clock.LiveClock")
# 默认缓存目录
CACHE_PATH = os.environ.get("TIMING_ANALYSIS_CACHE_PATH", "cache/analysis")


class AnalysisEngine(AppService, abstract=True):
    """分析引擎基类 — 子类继承后只需实现 _warmup 和 _process_bar。"""
    domain = "analysis"
    alias = "AnalysisEngine"
    # 类级共享时钟（所有分析子服务看到同一个时间）
    clock = smart_import(CLOCK_MODULE)()
    # 注册表：alias → 实例，便于 RunBacktest 批量操作
    _services: ClassVar[dict] = {}
    config = None

    def __init_subclass__(cls, abstract=False, **kwargs):
        if 'domain' not in cls.__dict__:
            cls.domain = "analysis"
        super().__init_subclass__(abstract=abstract, **kwargs)

    def __init__(self, cache_path=None, **kwargs):
        self._cache_path = cache_path or CACHE_PATH
        os.makedirs(self._cache_path, exist_ok=True)
        super().__init__(**kwargs)

    # ──────────────── 生命周期 ────────────────

    async def on_start(self) -> None:
        """启动时：如果 TOML 没配 protocol，就创建默认的 CacheLayer → SQLite 链。"""
        if not self.protocol:
            db_path = os.path.join(self._cache_path, f"{self.alias.lower()}.sqlite")
            inner = SQLiteProtocol(path=db_path)
            cache = CacheLayer(flush_threshold=1)
            cache.add_dependency(inner)
            self.protocol = cache
            await cache.maybe_start()
            log.info(f'[{self.alias}] 协议链就绪: CacheLayer → SQLite({db_path})')
        await super().on_start()

    async def on_started(self):
        """启动完成：把自己注册到 _services 字典，供 RunBacktest 枚举。"""
        AnalysisEngine._services[self.alias] = self
        log.info(f'[{self.alias}] 分析服务已注册')
        await super().on_started()

    # ──────────────── 核心入口：on_bar ────────────────

    async def on_bar(self, cmd):
        """
        收到 PushBars 事件后的处理入口。

        流程：
        1. 从事件中解析 symbol/interval
        2. 读 checkpoint → 决定全量处理还是增量处理
        3. 全量时先 warmup 前 N 条，再逐条 _process_bar 剩余的
        4. 增量时只处理 checkpoint 之后的新 bar
        5. 汇总所有产出的信号，同步广播给下游（策略层）
        """
        # ① 解析事件中的 symbol / interval
        event = cmd.get_event(-1)
        if not event: return None
        state_result = event.get("state", [None, None])[1]
        if not isinstance(state_result, dict): return None
        symbol = state_result.get("symbol", "")
        interval = state_result.get("interval", "")
        if not (symbol and interval): return None

        # ② 读 checkpoint（上次处理到哪根 bar 的时间戳）
        checkpoint_ts = await self.protocol.get(f"__ckpt:{symbol}:{interval}") or 0

        # ③ 根据 checkpoint 决定拉取范围
        if checkpoint_ts == 0:
            # 首次：全量拉取 → 前 N 条做 warmup → 剩余逐条处理
            result = await hub.execute(GetKlines(symbol=symbol, interval=interval))
            all_klines = result.state.result()
            if not all_klines: return None
            warmup_size = getattr(self.config, 'warmup_bars', 200) if self.config else 200
            if len(all_klines) <= warmup_size: return None
            await self._warmup(symbol, interval, all_klines[:warmup_size])
            new_bars = all_klines[warmup_size:]
        else:
            # 增量：只拉 checkpoint 之后的新数据
            result = await hub.execute(GetKlines(symbol=symbol, interval=interval, start_ts=checkpoint_ts + 1))
            new_bars = result.state.result()

        if not new_bars: return None

        # ④ 逐条处理每根 bar，收集信号
        output = {"signals": [], "breakouts": [], "recomputed": False}
        for bar in new_bars:
            self.clock.set_time_ms(int(bar["ts"]))
            bar_result = await self._process_bar(symbol, interval, bar)
            if bar_result:
                output["signals"].extend(bar_result.get("signals", []))
                output["breakouts"].extend(bar_result.get("breakouts", []))
                if bar_result.get("recomputed"): output["recomputed"] = True

        # ⑤ 更新 checkpoint（记录本次处理到的最后一根 bar 时间）
        await self.protocol.set(f"__ckpt:{symbol}:{interval}", int(new_bars[-1]["ts"]))

        # ⑥ 把产出的信号同步广播给策略层（用 hub.execute 保证回测中链路走完）
        for sig in output["signals"]:
            event = SignalEmitted(
                ts=self.clock.now_ms(), symbol=symbol, interval=interval,
                direction=sig.get("direction", "neutral"),
                strength=sig.get("strength", 0.5),
                source=sig.get("source", self.alias),
                price=sig.get("touch_price", sig.get("price", 0.0)),
                level=sig.get("level_price", sig.get("level")),
                metadata={k: v for k, v in sig.items() if k not in ("direction", "strength", "source", "touch_price", "price", "level_price", "level")})
            for handler_cls in hub.exchange.match(type(event).destination):
                cmd = handler_cls()
                cmd.add_event(event)
                await hub.execute(cmd)

        log.info(f'[{self.alias}] 处理完成 {symbol}/{interval} 共{len(new_bars)}根bar 产出{len(output["signals"])}个信号')
        return output

    # ──────────────── 子类必须实现 ────────────────

    async def _process_bar(self, symbol: str, interval: str, bar: dict) -> dict:
        """处理单根 bar，返回 {"signals": [...], "breakouts": [...], "recomputed": bool}"""
        raise NotImplementedError

    async def _warmup(self, symbol: str, interval: str, klines: list):
        """用前 N 根历史 K 线初始化内部计算状态（如回撤结构）"""
        raise NotImplementedError
