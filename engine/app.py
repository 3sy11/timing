"""
TimingApp / BacktestApp — 两个同级的应用入口。

【设计思路】
  两者共用同一套分析/策略/执行服务，区别仅在于"谁触发 on_bar"：
  - TimingApp：生产模式，外部推 bar → PushBars 广播 → 分析服务被动响应
  - BacktestApp：回测模式，RunBacktest 命令主动把历史数据喂给分析服务

【依赖关系（config.toml 中 depends 字段）】
  TimingApp   → DataEngine, RetracementService, FibStrategy, Broker
  BacktestApp → DataEngine, FibStrategy, Broker
    + 动态创建的回测分析实例（从 backtest.toml 读取）
"""
import tomllib, logging
from mode.utils.imports import smart_import
from bollydog.models.service import AppService
from bollydog.globals import hub
from bollydog.service.exchange import _make_callback
from timing.analysis.app import AnalysisEngine, CACHE_PATH

log = logging.getLogger(__name__)


class TimingApp(AppService):
    """生产入口 — 纯容器，不含自身逻辑，靠 TOML depends 把各服务串起来。"""
    domain = "timing"
    alias = "TimingApp"
    commands = []


class BacktestApp(AppService):
    """
    回测入口 — 做两件事：
    1. on_init_dependencies：读 backtest.toml，动态创建多组不同参数的分析子服务
    2. on_started：把这些动态实例的 subscriber 手动注册到 Exchange
    """
    domain = "backtest"
    alias = "BacktestApp"
    commands = ["timing.engine.command"]

    def __init__(self, backtest_config="backtest.toml", **kwargs):
        self._bt_config_path = backtest_config
        super().__init__(**kwargs)

    def on_init_dependencies(self):
        """
        读取 backtest.toml，为每组参数创建一个带序号的分析子服务实例。
        例如 RetracementService_0、RetracementService_1，各自有不同的 config 覆盖。
        返回的列表会被 mode 框架作为依赖启动。
        """
        try:
            with open(self._bt_config_path, 'rb') as f:
                bt_conf = tomllib.load(f)
        except FileNotFoundError:
            log.warning(f'[回测] 配置文件 {self._bt_config_path} 不存在，跳过动态创建')
            return []
        self._bt_params = bt_conf

        deps = []
        for i, svc_conf in enumerate(bt_conf.get("services", [])):
            # 导入基类（如 RetracementService）
            base_cls = smart_import(svc_conf["module"])
            # 生成带序号的 alias 避免 _apps 字典 key 冲突
            alias = f'{base_cls.alias}_{i}'
            svc_cls = type(alias, (base_cls,), {'alias': alias})
            svc = svc_cls(cache_path=svc_conf.get("cache_path", f'{CACHE_PATH}/{alias}'))
            # 用 backtest.toml 中的 config 覆盖默认参数
            if svc_conf.get("config"):
                if isinstance(svc.config, dict): svc.config.update(svc_conf["config"])
                else: svc.config = svc_conf["config"]
            deps.append(svc)
            log.info(f'[回测] 创建分析实例 {alias}')
        return deps

    async def on_started(self):
        """
        动态实例创建晚于 Exchange.on_started，所以需要在这里手动把它们的
        subscriber 注册到 Exchange，否则 RunBacktest 的 exchange.match 找不到它们。
        """
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
