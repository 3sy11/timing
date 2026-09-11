import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from computation.algo.fib_retracement.pipeline import _flatten_to_lines

_ALL_RATIOS = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]


def _levels(high, low):
    span = high - low
    return [[r, high - span * r] for r in _ALL_RATIOS]


def _record(levels, centers, low=89.0, high=100.0):
    return {
        "effective_ts": 1,
        "multiplier": 1,
        "direction": "up",
        "score": 2.5,
        "leg_low": low,
        "leg_high": high,
        "leg_start_ts": 1,
        "leg_end_ts": 2,
        "levels_json": json.dumps(levels),
        "cluster_centers_json": json.dumps(centers),
        "invalidated_ts": None,
        "invalidate_reason": None,
        "source": "initial",
    }


def test_row_count_equals_level_count():
    """行数只由 levels_json 决定, 不受候选中心数量影响。"""
    levels = _levels(100.0, 89.0)
    many_centers = [[89.0 + i * 0.5, 1.0] for i in range(30)]

    df = _flatten_to_lines([_record(levels, many_centers)])

    assert len(df) == len(levels) == 7


def test_inner_ratios_map_one_to_one_and_endpoints_may_reuse():
    """内层 5 条线各自对应一个中心; 0 和 1 无更外侧中心时复用相邻内层的中心。"""
    levels = _levels(100.0, 89.0)
    inner_prices = [p for r, p in levels if 0.0 < r < 1.0]
    centers = [[round(p, 2), 1.5] for p in inner_prices]

    df = _flatten_to_lines([_record(levels, centers)])
    by_ratio = dict(zip(df["ratio"], df["nearest_cluster_center"]))

    inner = df[(df["ratio"] > 0.0) & (df["ratio"] < 1.0)]
    assert inner["nearest_cluster_center"].nunique() == len(inner)
    assert inner["is_cluster_aligned"].all()

    assert by_ratio[0.0] == by_ratio[0.236]
    assert by_ratio[1.0] == by_ratio[0.786]
    assert df.loc[df["ratio"] == 0.0, "cluster_center_reused"].iloc[0]
    assert df.loc[df["ratio"] == 1.0, "cluster_center_reused"].iloc[0]


def test_alignment_flag_uses_fit_tolerance():
    """距离超过 2% 跨度的匹配仍给出最近中心, 但标记为未对齐。"""
    levels = [[0.5, 100.0]]
    df = _flatten_to_lines([_record(levels, [[95.0, 1.0]], low=90.0, high=110.0)])

    row = df.iloc[0]
    assert row["nearest_cluster_center"] == 95.0
    assert row["cluster_distance"] == 5.0
    assert row["cluster_distance_ratio"] == 0.25
    assert not row["is_cluster_aligned"]


def test_flatten_handles_missing_cluster_centers():
    df = _flatten_to_lines([_record([[0.0, 100.0]], [])])

    row = df.iloc[0]
    assert row["nearest_cluster_center"] is None
    assert row["nearest_cluster_conf"] is None
    assert not row["is_cluster_aligned"]
    assert not row["cluster_center_reused"]


def test_result_has_no_raw_centers_json():
    """cluster_centers_json 已按 level 打平, 投产表不再保留原始 JSON 列。"""
    df = _flatten_to_lines([_record(_levels(100.0, 89.0), [[95.0, 1.0]])])

    assert "cluster_centers_json" not in df.columns
