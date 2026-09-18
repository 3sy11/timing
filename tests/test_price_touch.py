import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from computation.algo.fib_retracement.line_events import fib_group_id
from analysis.rules.price_touch.config import PriceTouchConfig
from analysis.rules.price_touch.detect import run_detection


def _klines(rows):
    return pd.DataFrame(rows)


def _line(**kwargs):
    base = {
        "effective_ts": 10, "multiplier": 1, "direction": "up",
        "leg_low": 90.0, "leg_high": 110.0, "leg_start_ts": 8, "leg_end_ts": 10,
        "invalidated_ts": None, "ratio": 0.5, "price": 100.0,
        "nearest_cluster_center": 100.0,
    }
    base.update(kwargs)
    return base


def _gid(rec):
    return fib_group_id(
        rec["effective_ts"], rec["multiplier"], rec["direction"],
        rec["leg_low"], rec["leg_high"],
    )


def _ev(rec, period, outcome, kind="fib", entered=1):
    return {
        "fib_group_id": _gid(rec),
        "ratio": rec["ratio"],
        "target_kind": kind,
        "period": period,
        "outcome_atr": outcome,
        "entered_ts": entered,
    }


def _cfg(**kwargs):
    return PriceTouchConfig(**kwargs)


def _run(klines, lines, events, **cfg):
    return run_detection(
        _klines(klines),
        pd.DataFrame(lines),
        pd.DataFrame(events) if events else pd.DataFrame(),
        cfg=_cfg(**cfg),
        compute_id="t", analysis_id="a",
    )


def test_pit_only_living_line_at_t():
    a = _line(effective_ts=5, invalidated_ts=15, price=100.0, ratio=0.5)
    b = _line(
        effective_ts=20, invalidated_ts=None, price=100.0, ratio=0.618,
        multiplier=2, leg_low=80.0, leg_high=120.0,
    )
    klines = [
        {"ts": 10, "high": 100.05, "low": 99.95, "close": 100.0},
        {"ts": 20, "high": 100.05, "low": 99.95, "close": 100.0},
    ]
    events = [
        _ev(a, "prefit", "bounce"),
        _ev(b, "prefit", "break"),
    ]
    out = _run(klines, [a, b], events)["candidates"]
    assert {(r["ts"], r["ratio"]) for r in out} == {(10, 0.5), (20, 0.618)}
    assert all(r["pre_hold_rate"] is not None for r in out)


def test_pre_and_fit_are_separate():
    rec = _line()
    klines = [{"ts": 12, "high": 100.05, "low": 99.95, "close": 100.1}]
    events = [
        _ev(rec, "prefit", "bounce"),
        _ev(rec, "prefit", "bounce"),
        _ev(rec, "prefit", "break"),
        _ev(rec, "fitwin", "break"),
        _ev(rec, "fitwin", "break"),
    ]
    row = _run(klines, [rec], events)["candidates"][0]
    assert row["pre_test_n"] == 3
    assert row["pre_hold_n"] == 3
    assert row["pre_hold_rate"] == 0.6667
    assert row["fit_test_n"] == 2
    assert row["fit_hold_n"] == 2
    assert row["fit_hold_rate"] == 0.0


def test_weak_not_in_hold_denom():
    rec = _line()
    klines = [{"ts": 12, "high": 100.05, "low": 99.95, "close": 100.0}]
    events = [
        _ev(rec, "prefit", "bounce"),
        _ev(rec, "prefit", "break"),
        _ev(rec, "prefit", "weak"),
        _ev(rec, "prefit", "weak"),
    ]
    row = _run(klines, [rec], events)["candidates"][0]
    assert row["pre_test_n"] == 4
    assert row["pre_hold_n"] == 2
    assert row["pre_hold_rate"] == 0.5
    assert row["pre_weak_share"] == 0.5


def test_nearby_rates_use_prefit_only():
    a = _line(price=100.0, ratio=0.5, nearest_cluster_center=100.0)
    b = _line(
        price=100.05, ratio=0.618, nearest_cluster_center=100.05,
        multiplier=2, leg_low=80.0, leg_high=120.0,
    )
    klines = [{"ts": 12, "high": 100.05, "low": 99.95, "close": 100.0}]
    events = [
        _ev(a, "prefit", "bounce"),
        _ev(b, "prefit", "bounce"),
        _ev(b, "fitwin", "break"),
        _ev(b, "fitwin", "break"),
        _ev(b, "prefit", "bounce", kind="cluster"),
    ]
    rows = _run(klines, [a, b], events, neighbor_k=0.001)["candidates"]
    self_row = next(r for r in rows if r["ratio"] == 0.5)
    assert self_row["nearby_fib_n"] == 1
    assert self_row["nearby_fib_hold_rate"] == 1.0
    assert self_row["nearby_cluster_n"] >= 1
    assert self_row["nearby_cluster_hold_rate"] == 1.0


def test_invalidated_line_not_emitted():
    rec = _line(effective_ts=5, invalidated_ts=12)
    klines = [{"ts": 12, "high": 100.05, "low": 99.95, "close": 100.0}]
    events = [_ev(rec, "prefit", "bounce")]
    assert _run(klines, [rec], events)["candidates"] == []


def test_emit_if_no_prefit():
    rec = _line()
    klines = [{"ts": 12, "high": 100.05, "low": 99.95, "close": 100.0}]
    yes = _run(klines, [rec], [], emit_if_no_prefit=True)["candidates"]
    no = _run(klines, [rec], [], emit_if_no_prefit=False)["candidates"]
    assert len(yes) == 1
    assert yes[0]["pre_hold_rate"] is None
    assert yes[0]["pre_test_n"] == 0
    assert no == []


def test_nearby_cluster_dedupes_centers():
    a = _line(price=100.0, ratio=0.5, nearest_cluster_center=100.0)
    b = _line(
        price=100.04, ratio=0.382, nearest_cluster_center=100.0,
        multiplier=2, leg_low=85.0, leg_high=115.0,
    )
    c = _line(
        price=100.06, ratio=0.618, nearest_cluster_center=100.50,
        multiplier=3, leg_low=70.0, leg_high=130.0,
    )
    klines = [{"ts": 12, "high": 100.05, "low": 99.95, "close": 100.0}]
    events = [_ev(a, "prefit", "bounce"), _ev(b, "prefit", "break"), _ev(c, "prefit", "bounce")]
    rows = _run(klines, [a, b, c], events, neighbor_k=0.001)["candidates"]
    self_row = next(r for r in rows if r["ratio"] == 0.5)
    assert self_row["nearby_fib_n"] == 2
    assert self_row["nearby_cluster_n"] == 1


def test_scan_bars_limits_history():
    rec = _line(effective_ts=1)
    klines = [
        {"ts": 10, "high": 100.05, "low": 99.95, "close": 100.0},
        {"ts": 20, "high": 100.05, "low": 99.95, "close": 100.0},
    ]
    events = [_ev(rec, "prefit", "bounce")]
    all_rows = _run(klines, [rec], events, scan_bars=0)["candidates"]
    last = _run(klines, [rec], events, scan_bars=1)["candidates"]
    assert {r["ts"] for r in all_rows} == {10, 20}
    assert {r["ts"] for r in last} == {20}
