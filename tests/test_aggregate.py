from __future__ import annotations

import math

from inferenceci.aggregate import stat, sum_stats


def test_stat_single_sample():
    s = stat([5.0])
    assert s.median == 5.0
    assert s.p95 == 5.0
    assert s.iqr == (5.0, 5.0)


def test_stat_three_samples():
    s = stat([1.0, 2.0, 4.0])
    assert s.median == 2.0
    # p95 of [1,2,4] (linear interp) = 1 + 2*(2.9) - check: rank=0.95*2=1.9 → 2 + (4-2)*0.9 = 3.8
    assert math.isclose(s.p95, 3.8)
    # q1 = rank 0.5 → 1 + (2-1)*0.5 = 1.5; q3 = rank 1.5 → 2 + (4-2)*0.5 = 3.0
    assert s.iqr == (1.5, 3.0)


def test_stat_drops_none():
    s = stat([1.0, None, 3.0])
    assert s.median == 2.0


def test_stat_all_none_returns_zeros():
    s = stat([None, None])
    assert s.median == 0.0 and s.p95 == 0.0 and s.iqr == (0.0, 0.0)


def test_sum_stats_sums_summary_fields():
    a = stat([1.0, 2.0, 3.0])
    b = stat([10.0, 20.0, 30.0])
    out = sum_stats([a, b])
    assert math.isclose(out.median, a.median + b.median)
    assert math.isclose(out.p95, a.p95 + b.p95)
    assert math.isclose(out.iqr[0], a.iqr[0] + b.iqr[0])
    assert math.isclose(out.iqr[1], a.iqr[1] + b.iqr[1])
