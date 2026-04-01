"""分析引擎 AppService。"""
from typing import Dict, List, Optional, Tuple

from timing.analysis.algo.touch import TouchDetector
from timing.analysis.models import OnBarForAnalysis
from bollydog.models.service import AppService


class AnalysisEngine(AppService):
    domain = "timing"
    alias = "AnalysisEngine"
    commands = ["models"]
    subscribe = {"timing.DataEngine.BarEvent": OnBarForAnalysis}

    def __init__(
        self,
        touch_tolerance: float = 0.5,
        touch_cooldown_sec: float = 60.0,
        protocol=None,
        router_mapping=None,
        subscribe=None,
        **kwargs,
    ):
        super().__init__(protocol=protocol, router_mapping=router_mapping, subscribe=subscribe, **kwargs)
        self.touch_tolerance = float(touch_tolerance)
        self.touch_cooldown_sec = float(touch_cooldown_sec)
        self._fib_levels: Dict[Tuple[str, str], List[Tuple[float, float]]] = {}
        self._detectors: Dict[Tuple[str, str], TouchDetector] = {}

    def set_fib_state(
        self,
        symbol: str,
        interval: str,
        levels: List[Tuple[float, float]],
        leg_low: float = 0.0,
        leg_high: float = 0.0,
    ) -> None:
        key = (symbol, interval)
        self._fib_levels[key] = list(levels)
        self._detectors[key] = TouchDetector(cooldown_sec=self.touch_cooldown_sec)

    def get_levels(self, symbol: str, interval: str) -> Optional[List[Tuple[float, float]]]:
        return self._fib_levels.get((symbol, interval))

    def get_touch_detector(self, symbol: str, interval: str):
        key = (symbol, interval)
        if key not in self._detectors:
            self._detectors[key] = TouchDetector(cooldown_sec=self.touch_cooldown_sec)
        return self._detectors[key]
