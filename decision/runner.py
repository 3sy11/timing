"""run_strategy — 批量编排：signals → 规则链 → 模拟持仓 → decisions。"""
import logging
from typing import Callable

log = logging.getLogger(__name__)


def run_strategy(signals: list[dict], strategy_meta: dict, cfg: dict,
                 decision_id: str = "", analysis_id: str = "") -> list[dict]:
    """纯函数：signals + 规则链 + 配置 → decisions 列表。内部维护模拟持仓。"""
    gates: list[Callable] = strategy_meta["gates"]
    sizing: Callable = strategy_meta["sizing"]
    positions: dict = {}
    decisions: list[dict] = []
    sorted_signals = sorted(signals, key=lambda s: s.get("ts", 0))
    for signal in sorted_signals:
        symbol = signal.get("symbol", "")
        ctx = {"position": positions.get(symbol)}
        passed, reason = True, ""
        for gate in gates:
            passed, reason = gate(signal, ctx, cfg)
            if not passed:
                break
        base = {"decision_id": decision_id, "analysis_id": analysis_id,
                "symbol": symbol, "ts": signal.get("ts", 0),
                "direction": signal.get("direction", ""),
                "strength": signal.get("score", signal.get("strength", 0)),
                "price": signal.get("level_price", signal.get("close", signal.get("price", 0)))}
        if passed:
            order_params = sizing(signal, ctx, cfg)
            decisions.append({**base, "action": "submit", "side": order_params.get("side", ""),
                              "quantity": order_params.get("quantity", 0), "reason": "passed"})
            _update_position(positions, symbol, order_params)
        else:
            decisions.append({**base, "action": "skip", "side": "", "quantity": 0.0, "reason": reason})
    log.info(f'[决策] run_strategy 完成: signals={len(sorted_signals)} '
             f'submit={sum(1 for d in decisions if d["action"]=="submit")} '
             f'skip={sum(1 for d in decisions if d["action"]=="skip")}')
    return decisions


def _update_position(positions: dict, symbol: str, order_params: dict):
    """乐观更新模拟持仓：假设 submit 立即成交。"""
    side = order_params.get("side", "")
    qty = order_params.get("quantity", 0)
    pos = positions.get(symbol)
    if pos is None or pos.get("side") in (None, "flat"):
        positions[symbol] = {"side": "long" if side == "buy" else "short", "quantity": qty}
    elif (pos["side"] == "long" and side == "sell") or (pos["side"] == "short" and side == "buy"):
        remain = pos["quantity"] - qty
        if remain <= 0:
            positions[symbol] = {"side": "flat", "quantity": 0}
        else:
            positions[symbol] = {"side": pos["side"], "quantity": remain}
    else:
        positions[symbol] = {"side": pos["side"], "quantity": pos["quantity"] + qty}
