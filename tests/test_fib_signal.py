import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from computation.algo.fib_retracement.line_events import fib_group_id
from analysis.rules.fib_signal.config import FibSignalConfig
from analysis.rules.fib_signal.detect import run_detection

_GID = fib_group_id(1, 1, "up", 90.0, 110.0)
_GID_DN = fib_group_id(1, 1, "down", 90.0, 110.0)


def _result(direction="up", ratio=0.5, extrapolated=False, aligned=False, conf=1.0):
    gid_dir = direction
    return {
        "effective_ts": 1, "multiplier": 1, "direction": gid_dir,
        "leg_low": 90.0, "leg_high": 110.0, "ratio": ratio, "price": 100.0,
        "invalidated_ts": None, "is_extrapolated": extrapolated,
        "is_cluster_aligned": aligned, "fib_score": 1.8,
        "cluster_distance_ratio": 0.01 if aligned else 0.05,
        "nearest_cluster_conf": conf,
    }


def _cand(direction="up", approach="from_above", **kw):
    gid = _GID_DN if direction == "down" else _GID
    base = {
        "ts": 10, "close": 101.0, "approach": approach, "ratio": 0.5,
        "level_price": 100.0, "fib_group_id": gid, "multiplier": 1,
        "direction": direction, "compute_id": "c", "feature_id": "f",
        "pre_hold_n": 2, "pre_hold_rate": 1.0, "pre_weak_share": 0.0,
        "fit_hold_rate": None, "nearby_fib_n": 0, "nearby_fib_hold_rate": None,
        "nearby_cluster_n": 0,
    }
    base.update(kw)
    return base


def _run(cands, results, **cfg):
    return run_detection(
        pd.DataFrame(cands), pd.DataFrame(results),
        cfg=FibSignalConfig(**cfg),
        signal_id="sig", symbol="S", interval="1d",
    )["signals"]


def test_null_pre_hold_rate_rejected():
    rows = _run(
        [_cand(pre_hold_n=2, pre_hold_rate=None)],
        [_result()],
    )
    assert rows == []


def test_null_pre_weak_share_does_not_block():
    rows = _run(
        [_cand(pre_weak_share=None, pre_hold_rate=1.0)],
        [_result()],
    )
    assert len(rows) == 1
    assert rows[0]["score_pre_weak"] == 0.0
    assert rows[0]["pass_hard_filter"] is True


def test_score_equals_subtotal_and_copies_distance_atr():
    rows = _run([
        _cand(pre_hold_rate=1.0, atr=10.0, distance_atr=0.43, episode_max_dist_atr=0.51),
    ], [_result()], min_signal_score=0)
    assert len(rows) == 1
    assert "signal_direction" not in rows[0]
    assert "approach" not in rows[0]
    assert rows[0]["signal_score"] == rows[0]["score_subtotal"] == 3.0
    assert rows[0]["distance_atr"] == 0.43
    assert rows[0]["atr"] == 10.0
    assert rows[0]["episode_max_dist_atr"] == 0.51


def test_extrapolated_dropped_unless_debug():
    cand = [_cand(ratio=0.0)]
    res = [_result(ratio=0.0, extrapolated=True)]
    assert _run(cand, res) == []
    dbg = _run(cand, res, debug_mode=True)
    assert len(dbg) == 1
    assert dbg[0]["pass_hard_filter"] is False
    assert dbg[0]["filter_fail_reason"] == "extrapolated"


def test_low_score_not_written():
    cand = [_cand(pre_hold_rate=0.5, pre_weak_share=0.0)]
    res = [_result(aligned=False, conf=None)]
    assert _run(cand, res) == []
    dbg = _run(cand, res, debug_mode=True)
    assert dbg[0]["pass_hard_filter"] is True
    assert dbg[0]["signal_score"] < 3.0


def test_full_pre_hold_emits_at_min_score():
    rows = _run([_cand(pre_hold_rate=1.0)], [_result()])
    assert len(rows) == 1
    assert rows[0]["signal_score"] == 3.0
