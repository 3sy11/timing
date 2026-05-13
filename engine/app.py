"""TimingApp / BacktestApp — 同级应用入口。

TimingApp：生产模式，subscriber 由 TOML create_from 注册到 Exchange。
BacktestApp：读 backtest.toml 动态创建多参数分析实例，on_started 手动注册 subscriber。
"""
import tomllib, logging
from mode.utils.imports import smart_import
from bollydog.models.service import AppService
from bollydog.globals import hub
from bollydog.service.exchange import _make_callback
from timing.analysis.app import AnalysisEngine, CACHE_PATH

log = logging.getLogger(__name__)


class TimingApp(AppService):
    domain = "timing"
    alias = "TimingApp"
    commands = []


class BacktestApp(AppService):
    domain = "backtest"
    alias = "BacktestApp"
    commands = ["timing.engine.command"]

    def __init__(self, backtest_config="backtest.toml", **kwargs):
        self._bt_config_path = backtest_config
        super().__init__(**kwargs)

    def on_init_dependencies(self):
        """读取 backtest.toml，动态创建多参数分析子服务实例。"""
        try:
            with open(self._bt_config_path, 'rb') as f:
                bt_conf = tomllib.load(f)
        except FileNotFoundError:
            log.warning(f'[BacktestApp] {self._bt_config_path} not found')
            return []
        self._bt_params = bt_conf

        # 按 [[services]] 逐个创建带序号 alias 的子服务
        deps = []
        for i, svc_conf in enumerate(bt_conf.get("services", [])):
            base_cls = smart_import(svc_conf["module"])
            alias = f'{base_cls.alias}_{i}'
            svc_cls = type(alias, (base_cls,), {'alias': alias})
            svc = svc_cls(cache_path=svc_conf.get("cache_path", f'{CACHE_PATH}/{alias}'))
            # 覆盖子服务配置参数
            if svc.config and svc_conf.get("config"):
                for k, v in svc_conf["config"].items():
                    if hasattr(svc.config, k): setattr(svc.config, k, v)
            deps.append(svc)
            log.info(f'[BacktestApp] created {alias}')
        return deps

    async def on_started(self):
        """动态实例的 subscriber 手动注册到 Exchange（Exchange.on_started 先于动态创建）。"""
        registered = 0
        for svc in AnalysisEngine._services.values():
            for topic, methods in type(svc).subscriber.items():
                methods = [methods] if isinstance(methods, str) else methods
                for method_name in methods:
                    bound = getattr(svc, method_name)
                    cmd_cls = _make_callback(svc, method_name, bound)
                    hub.exchange.subscribe(topic, cmd_cls)
                    registered += 1
        log.info(f'[BacktestApp] registered {registered} handlers for {len(AnalysisEngine._services)} services')
        await super().on_started()
