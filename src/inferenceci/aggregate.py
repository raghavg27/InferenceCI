from __future__ import annotations

import math
import statistics
from collections.abc import Iterable

from inferenceci.schemas import MetricStat


def _percentile(samples: list[float], p: float) -> float:
    """Linear-interpolation percentile (numpy-style)."""
    if not samples:
        return 0.0
    s = sorted(samples)
    if len(s) == 1:
        return s[0]
    rank = p / 100.0 * (len(s) - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return s[lo]
    return s[lo] + (s[hi] - s[lo]) * (rank - lo)


def _quantiles(samples: list[float]) -> tuple[float, float]:
    if not samples:
        return (0.0, 0.0)
    if len(samples) == 1:
        return (samples[0], samples[0])
    s = sorted(samples)
    return (_percentile(s, 25), _percentile(s, 75))


def stat(samples: Iterable[float | None]) -> MetricStat:
    """Compute MetricStat. None values are dropped (e.g. unpriced runs).
    If no valid samples, returns zeros."""
    valid = [float(x) for x in samples if x is not None and not (isinstance(x, float) and math.isnan(x))]
    if not valid:
        return MetricStat(median=0.0, p95=0.0, iqr=(0.0, 0.0), samples=[])
    median = float(statistics.median(valid))
    p95 = _percentile(valid, 95)
    q1, q3 = _quantiles(valid)
    return MetricStat(median=median, p95=p95, iqr=(q1, q3), samples=valid)


def sum_stats(stats: list[MetricStat]) -> MetricStat:
    """Combine per-scenario MetricStats into one totals MetricStat by summing
    each summary statistic across scenarios. Samples not retained at the top."""
    if not stats:
        return MetricStat(median=0.0, p95=0.0, iqr=(0.0, 0.0), samples=[])
    median = sum(s.median for s in stats)
    p95 = sum(s.p95 for s in stats)
    iqr_lo = sum(s.iqr[0] for s in stats)
    iqr_hi = sum(s.iqr[1] for s in stats)
    return MetricStat(median=median, p95=p95, iqr=(iqr_lo, iqr_hi), samples=[])
