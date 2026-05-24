"""回测绩效指标计算。"""
import math


def compute_metrics(fills: list[dict], initial_balance: float, final_balance: float) -> dict:
    """从成交记录 + 初始/最终余额计算全部绩效指标。"""
    if not fills or initial_balance <= 0:
        return {"total_return": 0, "max_drawdown": 0, "win_rate": 0, "profit_factor": 0, "sharpe_ratio": 0, "trade_count": 0, "total_commission": 0}

    fills_sorted = sorted(fills, key=lambda f: f.get("ts", 0))
    total_return = (final_balance - initial_balance) / initial_balance

    # 重建逐笔权益曲线
    equity_curve = _build_equity_curve(fills_sorted, initial_balance)
    max_drawdown = _max_drawdown(equity_curve)

    # 配对交易：同一 symbol 的 buy/sell 匹配为 round-trip
    trades = _pair_trades(fills_sorted)
    win_rate, profit_factor = _trade_stats(trades)
    sharpe = _sharpe_ratio(equity_curve, initial_balance)
    total_commission = sum(f.get("commission", 0) for f in fills_sorted)

    return {"total_return": round(total_return, 6), "max_drawdown": round(max_drawdown, 6),
            "win_rate": round(win_rate, 4), "profit_factor": round(profit_factor, 4),
            "sharpe_ratio": round(sharpe, 4), "trade_count": len(trades),
            "total_commission": round(total_commission, 6), "equity_curve": equity_curve}


def _build_equity_curve(fills: list[dict], initial: float) -> list[dict]:
    """逐笔成交后的账户净值序列。"""
    balance = initial
    curve = [{"ts": 0, "equity": balance}]
    for f in fills:
        qty = f.get("filled_quantity", 0)
        price = f.get("filled_price", 0)
        commission = f.get("commission", 0)
        if f.get("side") == "buy":
            balance -= price * qty + commission
        else:
            balance += price * qty - commission
        curve.append({"ts": f.get("ts", 0), "equity": balance})
    return curve


def _max_drawdown(curve: list[dict]) -> float:
    """最大回撤比例。"""
    if len(curve) < 2: return 0
    peak = curve[0]["equity"]
    max_dd = 0
    for pt in curve:
        if pt["equity"] > peak: peak = pt["equity"]
        dd = (peak - pt["equity"]) / peak if peak > 0 else 0
        if dd > max_dd: max_dd = dd
    return max_dd


def _pair_trades(fills: list[dict]) -> list[dict]:
    """将 buy/sell 成交配对为 round-trip 交易。FIFO 匹配。"""
    open_positions: dict[str, list] = {}  # symbol → [(qty, price, ts)]
    trades = []
    for f in fills:
        sym = f.get("symbol", "")
        side = f.get("side", "")
        qty = f.get("filled_quantity", 0)
        price = f.get("filled_price", 0)
        ts = f.get("ts", 0)
        commission = f.get("commission", 0)
        if side == "buy":
            open_positions.setdefault(sym, []).append({"qty": qty, "price": price, "ts": ts, "commission": commission})
        elif side == "sell" and sym in open_positions and open_positions[sym]:
            remaining = qty
            entry_cost = 0
            entry_commission = 0
            while remaining > 0 and open_positions[sym]:
                entry = open_positions[sym][0]
                matched = min(remaining, entry["qty"])
                entry_cost += matched * entry["price"]
                entry_commission += entry["commission"] * (matched / entry["qty"]) if entry["qty"] > 0 else 0
                entry["qty"] -= matched
                remaining -= matched
                if entry["qty"] <= 0: open_positions[sym].pop(0)
            exit_value = (qty - remaining) * price
            pnl = exit_value - entry_cost - commission - entry_commission
            trades.append({"symbol": sym, "pnl": pnl, "entry_price": entry_cost / (qty - remaining) if (qty - remaining) > 0 else 0,
                           "exit_price": price, "quantity": qty - remaining, "entry_ts": ts, "exit_ts": ts})
    return trades


def _trade_stats(trades: list[dict]) -> tuple[float, float]:
    """胜率 + 盈亏比。"""
    if not trades: return 0, 0
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] < 0]
    win_rate = len(wins) / len(trades)
    total_win = sum(t["pnl"] for t in wins) if wins else 0
    total_loss = abs(sum(t["pnl"] for t in losses)) if losses else 0
    profit_factor = total_win / total_loss if total_loss > 0 else (float('inf') if total_win > 0 else 0)
    return win_rate, profit_factor


def _sharpe_ratio(curve: list[dict], initial: float, risk_free: float = 0.0) -> float:
    """基于权益曲线计算年化夏普比率（假设每步等间距）。"""
    if len(curve) < 3: return 0
    returns = []
    for i in range(1, len(curve)):
        prev = curve[i - 1]["equity"]
        if prev <= 0: continue
        returns.append((curve[i]["equity"] - prev) / prev)
    if not returns: return 0
    mean_r = sum(returns) / len(returns)
    var_r = sum((r - mean_r) ** 2 for r in returns) / len(returns)
    std_r = math.sqrt(var_r)
    if std_r == 0: return 0
    return (mean_r - risk_free) / std_r * math.sqrt(252)
