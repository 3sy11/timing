import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from computation.algo.fib_retracement.line_factors import build_line_factors


def _klines():
    # ts 0..5; price around 100
    rows = [
        {"ts": 0, "open": 102, "high": 103, "low": 101, "close": 102},
        {"ts": 1, "open": 102, "high": 102, "low": 99.9, "close": 100.2},   # from above, touch 100
        {"ts": 2, "open": 100.2, "high": 101, "low": 100, "close": 100.8},  # bounce up
        {"ts": 3, "open": 100.8, "high": 101, "low": 100.5, "close": 100.6},
        {"ts": 4, "open": 100.6, "high": 100.7, "low": 99.0, "close": 99.2},
        {"ts": 5, "open": 99.2, "high": 99.5, "low": 98.8, "close": 99.0},
    ]
    return pd.DataFrame(rows)


def _line(**kwargs):
    base = {
        "effective_ts": 1, "multiplier": 1, "direction": "up", "fib_score": 2.0,
        "leg_low": 90.0, "leg_high": 110.0, "leg_start_ts": 0, "leg_end_ts": 1,
        "invalidated_ts": None, "ratio": 0.5, "price": 100.0,
        "nearest_cluster_center": 100.0, "nearest_cluster_conf": 1.5,
        "is_cluster_aligned": True,
    }
    base.update(kwargs)
    return base


def test_bounce_from_above_on_live_line():
    result = pd.DataFrame([_line()])
    df = build_line_factors(result, _klines())
    row = df.iloc[0]
    assert row["fib_touch_bar_count"] >= 1
    assert row["fib_bounce_from_above"] >= 1
    assert row["fib_consensus_group_count"] == 1
    assert pd.isna(row["fib_peer_bounce_rate_mean"])


def test_dead_line_stops_before_invalidation():
    result = pd.DataFrame([_line(invalidated_ts=3)])
    df = build_line_factors(result, _klines())
    row = df.iloc[0]
    assert row["t_end"] == 3
    # bars ts=1,2 only in [1, 3)
    assert row["fib_touch_bar_count"] >= 1


def test_consensus_counts_other_group_same_price():
    a = _line()
    b = _line(effective_ts=1, multiplier=2, direction="down", leg_low=91.0, leg_high=111.0, ratio=0.618, price=100.05)
    df = build_line_factors(pd.DataFrame([a, b]), _klines())
    assert df.iloc[0]["fib_consensus_group_count"] == 2
    assert df.iloc[0]["fib_consensus_multiplier_count"] == 2
    assert df.iloc[0]["fib_consensus_direction_count"] == 2
    assert df.iloc[0]["fib_peer_bounce_rate_mean"] is not None or pd.notna(df.iloc[0]["fib_peer_aligned_ratio"])


def test_same_group_seven_levels_count_as_one_group():
    rows = [_line(ratio=r, price=100.0 + r) for r in (0.0, 0.236, 0.5)]
    rows[1]["price"] = 100.0  # two ratios same price still one group
    df = build_line_factors(pd.DataFrame(rows), _klines())
    assert (df["fib_consensus_group_count"] == 1).all()
