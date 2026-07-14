"""Execute command — 执行模块 CLI 入口。

读取决策数据 + K 线，通过指定交易所服务撮合，产出 orders/fills/positions。
"""
import logging
from typing import Any, ClassVar
import duckdb
from bollydog.globals import app
from bollydog.models.base import BaseCommand
from .runner import run_execution
from .writer import ExecutionWriter

log = logging.getLogger(__name__)


class Execute(BaseCommand):
    destination: ClassVar[str] = "execution.ExecutionService.Execute"
    execution_id: str = ""
    decision_id: str = ""
    exchange: str = "sim"
    symbol: str = ""
    interval: str = ""
    initial_balance: float = 100_000.0
    slippage_pct: float = 0.001
    commission_rate: float = 0.001

    async def __call__(self, *args, **kwargs) -> Any:
        if not (self.execution_id and self.decision_id):
            log.error('[执行] Execute 缺少必要参数: execution_id, decision_id')
            return None
        warehouse = app.warehouse_path
        decisions = self._load_decisions(warehouse)
        if not decisions:
            log.error(f'[执行] 无 submit 决策: decision_id={self.decision_id}')
            return None
        symbols = set(d["symbol"] for d in decisions)
        klines = self._load_klines(warehouse, symbols)
        if not klines:
            log.error(f'[执行] 无 K 线数据: symbols={symbols}')
            return None
        exchange_svc = self._get_exchange()
        log.info(f'[执行] 开始 execution_id={self.execution_id} exchange={self.exchange} '
                 f'decisions={len(decisions)} symbols={symbols}')
        result = run_execution(decisions, klines, exchange_svc,
                               execution_id=self.execution_id)
        writer = ExecutionWriter(warehouse=warehouse, execution_id=self.execution_id)
        writer.write_orders(result["orders"])
        writer.write_fills(result["fills"])
        writer.write_positions(result["positions"])
        summary = {"orders": len(result["orders"]), "fills": len(result["fills"]),
                   "positions": len(result["positions"]),
                   "account": exchange_svc.get_account() if hasattr(exchange_svc, "get_account") else {}}
        trace = self._build_trace(warehouse)
        writer.write_manifest(decision_id=self.decision_id, exchange_name=self.exchange,
                              config={"initial_balance": self.initial_balance,
                                      "slippage_pct": self.slippage_pct,
                                      "commission_rate": self.commission_rate},
                              summary=summary, trace=trace)
        log.info(f'[执行] 完成 execution_id={self.execution_id} → {summary}')
        return summary

    def _get_exchange(self):
        """获取交易所服务实例。优先从 app 注册服务中查找，否则创建本地实例。"""
        try:
            exchange_svc = app.get_service("ExchangeService")
            if exchange_svc:
                exchange_svc.reset()
                return exchange_svc
        except Exception:
            pass
        from timing.exchange.mock import SimExchange
        sim = SimExchange(initial_balance=self.initial_balance,
                          slippage_pct=self.slippage_pct,
                          commission_rate=self.commission_rate)
        log.info(f'[执行] 使用本地 SimExchange: balance={self.initial_balance}')
        return _LocalExchangeWrapper(sim)

    def _load_decisions(self, warehouse: str) -> list[dict]:
        path = f"{warehouse}/decisions/{self.decision_id}/decisions.parquet"
        try:
            with duckdb.connect() as conn:
                sql = f"SELECT * FROM read_parquet('{path}') WHERE action = 'submit'"
                if self.symbol:
                    sql += f" AND symbol = '{self.symbol}'"
                sql += " ORDER BY ts"
                return conn.execute(sql).fetchdf().to_dict("records")
        except Exception as e:
            log.error(f'[执行] 读取 decisions 失败: {e}')
            return []

    def _load_klines(self, warehouse: str, symbols: set) -> dict[str, list[dict]]:
        klines = {}
        for symbol in symbols:
            interval = self.interval or "1d"
            pattern = f"{warehouse}/klines/{symbol}/{interval}/*.parquet"
            try:
                with duckdb.connect() as conn:
                    rows = conn.execute(
                        f"SELECT * FROM read_parquet('{pattern}') ORDER BY ts"
                    ).fetchdf().to_dict("records")
                    if rows:
                        klines[symbol] = rows
            except Exception as e:
                log.warning(f'[执行] 加载 klines {symbol} 失败: {e}')
        return klines

    def _build_trace(self, warehouse: str) -> dict:
        import json
        manifest_path = f"{warehouse}/decisions/{self.decision_id}/manifest.json"
        try:
            with open(manifest_path) as f:
                m = json.load(f)
            return {"decision_id": self.decision_id,
                    "strategy": m.get("strategy", ""),
                    "analysis_id": m.get("analysis_id", ""),
                    "compute_id": m.get("trace", {}).get("compute_id", "")}
        except Exception:
            return {"decision_id": self.decision_id}


class _LocalExchangeWrapper:
    """当 ExchangeService 不可用时包装本地 SimExchange 保持接口一致。"""
    def __init__(self, sim):
        self._sim = sim
    def submit_order(self, order, bar):
        return self._sim.submit_order(order, bar)
    def check_pending(self, bar):
        return self._sim.check_pending(bar)
    def cancel_order(self, order_id):
        return self._sim.cancel_order(order_id)
    def get_account(self):
        return {"initial_balance": self._sim.initial_balance, "total": self._sim.total,
                "net_pnl": self._sim.total - self._sim.initial_balance}
    def reset(self):
        self._sim.reset()
