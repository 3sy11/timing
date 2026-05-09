"""TimingApp / BacktestApp — 同级应用，TOML depends 管理依赖。

TimingApp：生产入口，subscriber 由 TOML create_from 自动注册到 Exchange。
BacktestApp：on_init_dependencies 读 backtest.toml 动态创建带序号 alias 的分析子服务，
             on_started 手动注册动态实例的 subscriber 到 Exchange。
"""
import logging
from bollydog.models.service import AppService
from timing.analysis.engine import AnalysisEngine, CACHE_PATH

log = logging.getLogger(__name__)


class TimingApp(AppService):
    domain = "timing"
    alias = "TimingApp"
    commands = []
    analysis = AnalysisEngine


class BacktestApp(AppService):
    domain = "backtest"
    alias = "BacktestApp"
    commands = ["timing.engine.command"]
    analysis = AnalysisEngine

    def __init__(self, backtest_config="backtest.toml", **kwargs):
        self._bt_config_path = backtest_config
        super().__init__(**kwargs)

    def on_init_dependencies(self):
        import tomllib
        from mode.utils.imports import smart_import
        try:
            with open(self._bt_config_path, 'rb') as f:
                bt_conf = tomllib.load(f)
        except FileNotFoundError:
            log.warning(f'[BacktestApp] {self._bt_config_path} not found')
            return []
        self._bt_params = bt_conf
        deps = []
        for i, svc_conf in enumerate(bt_conf.get("services", [])):
            base_cls = smart_import(svc_conf["module"])
            alias = f'{base_cls.alias}_{i}'
            svc_cls = type(alias, (base_cls,), {'alias': alias})
            svc = svc_cls(cache_path=svc_conf.get("cache_path", f'{CACHE_PATH}/{alias}'))
            if svc.config and svc_conf.get("config"):
                for k, v in svc_conf["config"].items():
                    if hasattr(svc.config, k):
                        setattr(svc.config, k, v)
            deps.append(svc)
            log.info(f'[BacktestApp] created {alias}')
        return deps

    async def on_started(self):
        from bollydog.globals import hub
        from bollydog.service.exchange import _make_callback
        registered = 0
        for svc in AnalysisEngine._services.values():
            for topic, methods in type(svc).subscriber.items():
                methods = [methods] if isinstance(methods, str) else methods
                for method_name in methods:
                    bound = getattr(svc, method_name)
                    cmd_cls = _make_callback(svc, method_name, bound)
                    hub.exchange.subscribe(topic, cmd_cls)
                    registered += 1
        log.info(f'[BacktestApp] registered {registered} subscriber handlers for {len(AnalysisEngine._services)} services')
        await super().on_started()
