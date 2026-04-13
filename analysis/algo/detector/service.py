"""DetectorService：管理 detectors 状态，对外暴露 check_bars / reset。"""
from typing import Dict, List, Tuple
from bollydog.models.service import AppService
from timing.analysis.config import FibConfig
from timing.common.clock import Clock, LiveClock
from .touch import TouchDetector


class DetectorService(AppService):
    domain = "timing"
    alias = "DetectorService"
    commands = []

    def __init__(self, clock: Clock = None, **kwargs):
        super().__init__(**kwargs)
        self.clock = clock or LiveClock()
        self._detectors: Dict[Tuple[str, str], TouchDetector] = {}

    def _get_or_create(self, symbol: str, interval: str, config: FibConfig) -> TouchDetector:
        key = (symbol, interval)
        if key not in self._detectors:
            self._detectors[key] = TouchDetector(cooldown_sec=config.touch_cooldown_sec, clock=self.clock)
        return self._detectors[key]

    def check_bars(self, symbol: str, interval: str, bars: List[dict],
                   levels: List[Tuple[float, float]], config: FibConfig) -> List[Tuple[float, float, float]]:
        det = self._get_or_create(symbol, interval, config)
        all_touched: List[Tuple[float, float, float]] = []
        for bar in bars:
            price = float(bar.get("close", 0))
            for r, p in det.check(price, levels, config.touch_tolerance):
                all_touched.append((r, p, price))
        return all_touched

    def reset(self, symbol: str, interval: str):
        self._detectors.pop((symbol, interval), None)

    async def on_reset(self) -> None:
        self._detectors.clear()
