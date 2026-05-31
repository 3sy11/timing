"""TimingApp / BacktestApp — 生产/回测两个入口。"""
import tomllib, logging
from mode.utils.imports import smart_import
from bollydog.models.service import AppService
from bollydog.globals import hub
from bollydog.service.exchange import _make_callback
from timing.analysis.app import AnalysisEngine, DATA_ROOT

log = logging.getLogger(__name__)


class TimingApp(AppService):
    """生产入口 — 纯容器，靠 TOML depends 把各服务串起来。"""
    domain = "timing"
    alias = "TimingApp"
    commands = []


class BacktestApp(AppService):
    """回测入口 — 读 backtest.toml 动态创建分析实例 + 注册 subscriber。"""
    domain = "backtest"
    alias = "BacktestApp"
    commands = ["timing.engine.command", "timing.engine.batch"]

    def __init__(self, backtest_config="backtest.toml", **kwargs):
        self._bt_config_path = backtest_config
        super().__init__(**kwargs)

    def on_init_dependencies(self):
        try:
            with open(self._bt_config_path, 'rb') as f:
                bt_conf = tomllib.load(f)
        except FileNotFoundError:
            log.warning(f'[回测] 配置文件 {self._bt_config_path} 不存在，跳过动态创建')
            return []
        self._bt_params = bt_conf

        deps = []
        for i, svc_conf in enumerate(bt_conf.get("services", [])):
            base_cls = smart_import(svc_conf["module"])
            alias = f'{base_cls.alias}_{i}'
            svc_cls = type(alias, (base_cls,), {'alias': alias})
            svc = svc_cls(cache_path=svc_conf.get("cache_path", f'{DATA_ROOT}/{alias}'))
            if svc_conf.get("config"):
                if isinstance(svc.config, dict): svc.config.update(svc_conf["config"])
                else: svc.config = svc_conf["config"]
            deps.append(svc)
            log.info(f'[回测] 创建分析实例 {alias}')
        return deps

    async def on_started(self):
        registered = 0
        for svc in AnalysisEngine._services.values():
            for topic, methods in type(svc).subscriber.items():
                methods = [methods] if isinstance(methods, str) else methods
                for method_name in methods:
                    bound = getattr(svc, method_name)
                    cmd_cls = _make_callback(svc, method_name, bound)
                    hub.exchange.subscribe(topic, cmd_cls)
                    registered += 1
        log.info(f'[回测] 注册了 {registered} 个事件处理器，覆盖 {len(AnalysisEngine._services)} 个分析服务')
        await super().on_started()
