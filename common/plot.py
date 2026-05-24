"""回测结果可视化 — Plotly 交互式图表生成。"""
import os
from typing import Optional
from plotly import graph_objects as go
from plotly.subplots import make_subplots


def plot_backtest(result: dict, metrics: dict, output_dir: str = "output", filename: str = None) -> str:
    """生成单次回测的完整可视化 HTML（K线+买卖点+权益曲线+回撤）。"""
    os.makedirs(output_dir, exist_ok=True)
    symbol = result.get("symbol", "unknown")
    interval = result.get("interval", "")
    klines = result.get("klines", [])
    fills = result.get("fills", [])
    signals = result.get("signals", [])
    equity_curve = metrics.get("equity_curve", [])

    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.03,
                        row_heights=[0.55, 0.25, 0.20],
                        subplot_titles=[f"{symbol} {interval} K线 + 交易点", "账户权益曲线", "回撤曲线"])

    # ──── Row 1: K线 + 买卖点 ────
    if klines:
        ts_list = [k.get("ts", i) for i, k in enumerate(klines)]
        fig.add_trace(go.Candlestick(
            x=ts_list, open=[k["open"] for k in klines], high=[k["high"] for k in klines],
            low=[k["low"] for k in klines], close=[k["close"] for k in klines],
            name="K线", increasing_line_color="#26a69a", decreasing_line_color="#ef5350"), row=1, col=1)

    # 信号标记（三角形）
    if signals:
        sig_ts = [s.get("ts", s.get("touch_price", 0)) for s in signals]
        sig_price = [s.get("touch_price", s.get("price", s.get("level_price", 0))) for s in signals]
        sig_dir = [s.get("direction", "neutral") for s in signals]
        fig.add_trace(go.Scatter(
            x=sig_ts, y=sig_price, mode="markers", name="信号",
            marker=dict(size=6, color=["#2196F3" if d == "long" else "#FF9800" if d == "short" else "#9E9E9E" for d in sig_dir],
                        symbol=["triangle-up" if d == "long" else "triangle-down" for d in sig_dir])), row=1, col=1)

    # 成交标记
    if fills:
        buy_fills = [f for f in fills if f.get("side") == "buy"]
        sell_fills = [f for f in fills if f.get("side") == "sell"]
        if buy_fills:
            fig.add_trace(go.Scatter(
                x=[f["ts"] for f in buy_fills], y=[f["filled_price"] for f in buy_fills],
                mode="markers", name="买入", marker=dict(size=10, color="#00C853", symbol="triangle-up")), row=1, col=1)
        if sell_fills:
            fig.add_trace(go.Scatter(
                x=[f["ts"] for f in sell_fills], y=[f["filled_price"] for f in sell_fills],
                mode="markers", name="卖出", marker=dict(size=10, color="#D50000", symbol="triangle-down")), row=1, col=1)

    # ──── Row 2: 权益曲线 ────
    if equity_curve and len(equity_curve) > 1:
        eq_ts = [pt["ts"] for pt in equity_curve]
        eq_val = [pt["equity"] for pt in equity_curve]
        fig.add_trace(go.Scatter(x=eq_ts, y=eq_val, mode="lines", name="权益",
                                 line=dict(color="#1976D2", width=2)), row=2, col=1)

    # ──── Row 3: 回撤曲线 ────
    if equity_curve and len(equity_curve) > 1:
        dd_ts, dd_vals = _drawdown_series(equity_curve)
        fig.add_trace(go.Scatter(x=dd_ts, y=dd_vals, mode="lines", name="回撤",
                                 fill="tozeroy", line=dict(color="#E53935", width=1)), row=3, col=1)

    # 布局
    total_ret = metrics.get("total_return", 0)
    max_dd = metrics.get("max_drawdown", 0)
    win_r = metrics.get("win_rate", 0)
    sharpe = metrics.get("sharpe_ratio", 0)
    trades_n = metrics.get("trade_count", 0)
    title = (f"{symbol} {interval} | 收益={total_ret*100:.2f}% 回撤={max_dd*100:.2f}% "
             f"胜率={win_r*100:.1f}% 夏普={sharpe:.2f} 交易数={trades_n}")
    fig.update_layout(title=title, height=900, xaxis_rangeslider_visible=False,
                      template="plotly_dark", showlegend=True)

    fname = filename or f"{symbol}_{interval}_backtest.html"
    path = os.path.join(output_dir, fname)
    fig.write_html(path)
    return path


def plot_batch_comparison(batch_results: list[dict], output_dir: str = "output") -> str:
    """批量回测结果对比图：各参数组合的收益/回撤/夏普散点 + 排名表。"""
    os.makedirs(output_dir, exist_ok=True)
    from timing.common.metrics import compute_metrics

    records = []
    for item in batch_results:
        params = item.get("params", {})
        result = item.get("result")
        if not result: continue
        fills = result.get("fills", [])
        account = result.get("account", {})
        initial = account.get("initial_balance", 100000)
        final = account.get("total", initial)
        m = compute_metrics(fills, initial, final)
        records.append({"params": params, "total_return": m["total_return"], "max_drawdown": m["max_drawdown"],
                        "win_rate": m["win_rate"], "sharpe_ratio": m["sharpe_ratio"],
                        "trade_count": m["trade_count"], "profit_factor": m["profit_factor"]})

    if not records:
        return ""

    fig = make_subplots(rows=2, cols=2, subplot_titles=["收益率分布", "最大回撤分布", "收益 vs 回撤", "夏普比率排名"])
    labels = [str(r["params"]) for r in records]

    # 收益率柱状图
    fig.add_trace(go.Bar(x=list(range(len(records))), y=[r["total_return"] * 100 for r in records],
                         text=labels, name="收益率%", marker_color="#26a69a"), row=1, col=1)

    # 回撤柱状图
    fig.add_trace(go.Bar(x=list(range(len(records))), y=[r["max_drawdown"] * 100 for r in records],
                         text=labels, name="最大回撤%", marker_color="#ef5350"), row=1, col=2)

    # 收益 vs 回撤散点图
    fig.add_trace(go.Scatter(
        x=[r["max_drawdown"] * 100 for r in records], y=[r["total_return"] * 100 for r in records],
        mode="markers+text", text=[f'{i}' for i in range(len(records))],
        marker=dict(size=10, color=[r["sharpe_ratio"] for r in records], colorscale="Viridis", showscale=True),
        name="收益/回撤"), row=2, col=1)

    # 夏普排名
    sorted_records = sorted(enumerate(records), key=lambda x: x[1]["sharpe_ratio"], reverse=True)
    fig.add_trace(go.Bar(
        x=[f'#{i}' for i, _ in sorted_records[:20]],
        y=[r["sharpe_ratio"] for _, r in sorted_records[:20]],
        name="夏普比率", marker_color="#1976D2"), row=2, col=2)

    fig.update_layout(title=f"批量回测对比 ({len(records)}组参数)", height=800, template="plotly_dark", showlegend=False)
    path = os.path.join(output_dir, "batch_comparison.html")
    fig.write_html(path)

    # 输出排名 CSV
    csv_path = os.path.join(output_dir, "batch_ranking.csv")
    with open(csv_path, "w") as f:
        f.write("rank,params,total_return,max_drawdown,win_rate,profit_factor,sharpe_ratio,trade_count\n")
        for rank, (idx, r) in enumerate(sorted_records, 1):
            f.write(f'{rank},"{r["params"]}",{r["total_return"]:.6f},{r["max_drawdown"]:.6f},'
                    f'{r["win_rate"]:.4f},{r["profit_factor"]:.4f},{r["sharpe_ratio"]:.4f},{r["trade_count"]}\n')
    return path


def _drawdown_series(equity_curve: list[dict]) -> tuple[list, list]:
    """从权益曲线计算回撤时间序列。"""
    ts_list, dd_list = [], []
    peak = equity_curve[0]["equity"]
    for pt in equity_curve:
        if pt["equity"] > peak: peak = pt["equity"]
        dd = (peak - pt["equity"]) / peak if peak > 0 else 0
        ts_list.append(pt["ts"])
        dd_list.append(-dd * 100)  # 负数表示回撤
    return ts_list, dd_list
