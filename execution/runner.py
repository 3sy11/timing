"""run_execution — 纯函数：decisions + klines → orders/fills/positions。

接收 submit 决策列表和 K 线数据，通过交易所撮合引擎逐 bar 处理，
产出三组数据：orders（订单事件流）、fills（成交记录）、positions（持仓快照）。
"""
import uuid, logging
from typing import Any

log = logging.getLogger(__name__)


def run_execution(decisions: list[dict], klines: dict[str, list[dict]],
                  exchange: Any, execution_id: str = "") -> dict:
    """执行编排主函数。
    Args:
        decisions: action=submit 的决策列表（已按 ts 排序）
        klines: {symbol: [bar, ...]} 按 ts 升序
        exchange: SimExchange 实例（提供 submit_order/check_pending）
        execution_id: 实验 ID
    Returns:
        {"orders": [...], "fills": [...], "positions": [...]}
    """
    orders, fills, positions_snap = [], [], []
    positions: dict[str, dict] = {}
    decision_queue = sorted(decisions, key=lambda d: d.get("ts", 0))
    decision_idx: dict[str, int] = {sym: 0 for sym in set(d.get("symbol", "") for d in decision_queue)}
    sym_decisions: dict[str, list[dict]] = {}
    for d in decision_queue:
        sym_decisions.setdefault(d.get("symbol", ""), []).append(d)
    all_symbols = set(sym_decisions.keys()) | set(klines.keys())
    bar_timeline = _build_timeline(klines, all_symbols)
    for ts, symbol, bar in bar_timeline:
        pending_fills = exchange.check_pending(bar)
        for fill in pending_fills:
            fills.append({**fill, "execution_id": execution_id})
            _apply_fill(positions, fill)
            positions_snap.append(_snapshot_position(positions, symbol, ts, execution_id))
        sym_decs = sym_decisions.get(symbol, [])
        idx = decision_idx.get(symbol, 0)
        while idx < len(sym_decs) and sym_decs[idx].get("ts", 0) <= ts:
            dec = sym_decs[idx]
            order = _build_order(dec, execution_id)
            orders.append(order)
            fill = exchange.submit_order(order, bar)
            if fill:
                fills.append({**fill, "execution_id": execution_id})
                _apply_fill(positions, fill)
                positions_snap.append(_snapshot_position(positions, symbol, ts, execution_id))
            idx += 1
        decision_idx[symbol] = idx
    log.info(f'[执行] run_execution 完成: orders={len(orders)} fills={len(fills)} snapshots={len(positions_snap)}')
    return {"orders": orders, "fills": fills, "positions": positions_snap}


def _build_timeline(klines: dict[str, list[dict]], symbols: set) -> list[tuple]:
    """合并所有品种的 bar 按时间排序。"""
    timeline = []
    for symbol in symbols:
        for bar in klines.get(symbol, []):
            timeline.append((bar.get("ts", 0), symbol, bar))
    timeline.sort(key=lambda x: x[0])
    return timeline


def _build_order(decision: dict, execution_id: str) -> dict:
    return {"order_id": str(uuid.uuid4())[:8], "execution_id": execution_id,
            "symbol": decision.get("symbol", ""), "side": decision.get("side", ""),
            "quantity": decision.get("quantity", 0), "order_type": "market",
            "price": decision.get("price", 0), "status": "pending",
            "ts": decision.get("ts", 0), "decision_id": decision.get("decision_id", "")}


def _apply_fill(positions: dict, fill: dict):
    """根据成交更新持仓状态机。"""
    symbol = fill.get("symbol", "")
    side = fill.get("side", "")
    qty = fill.get("filled_quantity", 0)
    price = fill.get("filled_price", 0)
    pos = positions.get(symbol, {"side": "flat", "quantity": 0, "avg_price": 0, "realized_pnl": 0})
    if pos["side"] == "flat":
        pos = {"side": "long" if side == "buy" else "short", "quantity": qty,
               "avg_price": price, "realized_pnl": pos.get("realized_pnl", 0)}
    elif (pos["side"] == "long" and side == "sell") or (pos["side"] == "short" and side == "buy"):
        close_qty = min(qty, pos["quantity"])
        pnl_per = (price - pos["avg_price"]) if pos["side"] == "long" else (pos["avg_price"] - price)
        realized = pnl_per * close_qty - fill.get("commission", 0)
        remain = pos["quantity"] - close_qty
        if remain <= 0:
            pos = {"side": "flat", "quantity": 0, "avg_price": 0,
                   "realized_pnl": pos.get("realized_pnl", 0) + realized}
        else:
            pos = {"side": pos["side"], "quantity": remain, "avg_price": pos["avg_price"],
                   "realized_pnl": pos.get("realized_pnl", 0) + realized}
    else:
        total_qty = pos["quantity"] + qty
        pos["avg_price"] = (pos["avg_price"] * pos["quantity"] + price * qty) / total_qty
        pos["quantity"] = total_qty
    positions[symbol] = pos


def _snapshot_position(positions: dict, symbol: str, ts: int, execution_id: str) -> dict:
    pos = positions.get(symbol, {"side": "flat", "quantity": 0, "avg_price": 0, "realized_pnl": 0})
    return {"execution_id": execution_id, "symbol": symbol, "ts": ts,
            "side": pos["side"], "quantity": pos["quantity"],
            "avg_price": pos["avg_price"], "realized_pnl": pos.get("realized_pnl", 0)}
