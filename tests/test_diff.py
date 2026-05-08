from __future__ import annotations

from datetime import UTC, datetime

from inferenceci.diff import _iqr_overlap, render_json, render_markdown, run_diff
from inferenceci.schemas import (
    GitInfo,
    MetricsBlock,
    MetricStat,
    Report,
    ScenarioReport,
    ThresholdConfig,
)


def stat(median, p95=None, iqr=None):
    if p95 is None:
        p95 = median
    if iqr is None:
        iqr = (median, median)
    return MetricStat(median=median, p95=p95, iqr=iqr, samples=[])


def block(cost, in_t=1000, out_t=500, calls=1, latency=100, iqr_cost=None):
    return MetricsBlock(
        cost_usd=stat(cost, iqr=iqr_cost or (cost, cost)),
        input_tokens=stat(in_t),
        output_tokens=stat(out_t),
        cached_tokens=stat(0),
        calls=stat(calls),
        tool_calls=stat(0),
        latency_ms=stat(latency),
    )


def report(scenarios: list[ScenarioReport], totals: MetricsBlock) -> Report:
    return Report(
        schema_version=1,
        generated_at=datetime.now(UTC),
        git=GitInfo(),
        config_hash="sha256:x",
        pricing_hash="sha256:y",
        totals=totals,
        scenarios=scenarios,
        warnings=[],
    )


def th(total=15, scen=25, ignore=0.001):
    return ThresholdConfig(
        cost_increase_pct=total, scenario_cost_increase_pct=scen, ignore_below_usd=ignore
    )


def test_iqr_overlap():
    assert _iqr_overlap((1.0, 2.0), (1.5, 3.0))
    assert _iqr_overlap((1.0, 2.0), (2.0, 3.0))  # touching
    assert not _iqr_overlap((1.0, 2.0), (2.5, 3.0))


def test_pass_when_under_threshold():
    base_s = ScenarioReport(name="s", runs=3, errors=0, metrics=block(0.10, iqr_cost=(0.09, 0.11)))
    head_s = ScenarioReport(name="s", runs=3, errors=0, metrics=block(0.11, iqr_cost=(0.10, 0.12)))
    base = report([base_s], block(0.10, iqr_cost=(0.09, 0.11)))
    head = report([head_s], block(0.11, iqr_cost=(0.10, 0.12)))
    d = run_diff(base, head, th(total=15, scen=25))
    assert not d.failed
    # IQR overlaps -> noise
    cost = next(m for m in d.totals if m.metric == "cost_usd")
    assert cost.status == "noise"


def test_fail_total_cost_regression():
    base_s = ScenarioReport(name="s", runs=3, errors=0, metrics=block(0.10, iqr_cost=(0.10, 0.10)))
    head_s = ScenarioReport(name="s", runs=3, errors=0, metrics=block(0.20, iqr_cost=(0.20, 0.20)))
    base = report([base_s], block(0.10, iqr_cost=(0.10, 0.10)))
    head = report([head_s], block(0.20, iqr_cost=(0.20, 0.20)))
    d = run_diff(base, head, th(total=15, scen=25))
    assert d.failed
    assert any("total cost" in r for r in d.fail_reasons)


def test_fail_scenario_cost_regression_only():
    """Total +10% (under 15%) but a scenario +30% (over 25%) — should fail."""
    s_base = ScenarioReport(name="big", runs=3, errors=0, metrics=block(0.04, iqr_cost=(0.04, 0.04)))
    s_base2 = ScenarioReport(name="small", runs=3, errors=0, metrics=block(0.06, iqr_cost=(0.06, 0.06)))
    s_head = ScenarioReport(name="big", runs=3, errors=0, metrics=block(0.052, iqr_cost=(0.052, 0.052)))
    s_head2 = ScenarioReport(name="small", runs=3, errors=0, metrics=block(0.058, iqr_cost=(0.058, 0.058)))
    base = report([s_base, s_base2], block(0.10, iqr_cost=(0.10, 0.10)))
    head = report([s_head, s_head2], block(0.110, iqr_cost=(0.11, 0.11)))
    d = run_diff(base, head, th(total=15, scen=25))
    assert d.failed
    assert any("'big'" in r for r in d.fail_reasons)


def test_ignore_below_usd_does_not_fail():
    base = report([ScenarioReport(name="s", runs=3, errors=0, metrics=block(0.0001, iqr_cost=(0.0001, 0.0001)))], block(0.0001, iqr_cost=(0.0001, 0.0001)))
    head = report([ScenarioReport(name="s", runs=3, errors=0, metrics=block(0.0005, iqr_cost=(0.0005, 0.0005)))], block(0.0005, iqr_cost=(0.0005, 0.0005)))
    d = run_diff(base, head, th(total=15, scen=25, ignore=0.001))
    # 4x change but absolute < 0.001 => ignored, not failed
    assert not d.failed
    cost = next(m for m in d.totals if m.metric == "cost_usd")
    assert cost.status == "ignored"


def test_iqr_overlap_marks_noise_not_fail():
    """Big pct delta but IQR overlap -> noise."""
    base = report([ScenarioReport(name="s", runs=3, errors=0, metrics=block(0.10, iqr_cost=(0.05, 0.20)))], block(0.10, iqr_cost=(0.05, 0.20)))
    head = report([ScenarioReport(name="s", runs=3, errors=0, metrics=block(0.30, iqr_cost=(0.10, 0.40)))], block(0.30, iqr_cost=(0.10, 0.40)))
    d = run_diff(base, head, th(total=15, scen=25))
    assert not d.failed
    cost = next(m for m in d.totals if m.metric == "cost_usd")
    assert cost.status == "noise"


def test_improvement_marked_green_not_fail():
    base = report([ScenarioReport(name="s", runs=3, errors=0, metrics=block(0.10, iqr_cost=(0.10, 0.10)))], block(0.10, iqr_cost=(0.10, 0.10)))
    head = report([ScenarioReport(name="s", runs=3, errors=0, metrics=block(0.05, iqr_cost=(0.05, 0.05)))], block(0.05, iqr_cost=(0.05, 0.05)))
    d = run_diff(base, head, th(total=15, scen=25))
    assert not d.failed
    cost = next(m for m in d.totals if m.metric == "cost_usd")
    assert cost.status == "improvement"


def test_new_scenario_in_head():
    base = report([], block(0.0))
    head = report([ScenarioReport(name="new", runs=3, errors=0, metrics=block(0.05, iqr_cost=(0.05,0.05)))], block(0.05, iqr_cost=(0.05,0.05)))
    d = run_diff(base, head, th())
    assert any("new in head" in n for n in d.notes)


def test_render_markdown_contains_marker_and_emoji():
    base = report([ScenarioReport(name="s", runs=3, errors=0, metrics=block(0.10, iqr_cost=(0.10, 0.10)))], block(0.10, iqr_cost=(0.10, 0.10)))
    head = report([ScenarioReport(name="s", runs=3, errors=0, metrics=block(0.20, iqr_cost=(0.20, 0.20)))], block(0.20, iqr_cost=(0.20, 0.20)))
    md = render_markdown(run_diff(base, head, th()))
    assert "<!-- costdiff:comment -->" in md
    assert "🔴" in md  # regression


def test_render_json_is_valid():
    import json
    base = report([ScenarioReport(name="s", runs=3, errors=0, metrics=block(0.10, iqr_cost=(0.10, 0.10)))], block(0.10, iqr_cost=(0.10, 0.10)))
    head = report([ScenarioReport(name="s", runs=3, errors=0, metrics=block(0.11, iqr_cost=(0.11, 0.11)))], block(0.11, iqr_cost=(0.11, 0.11)))
    j = json.loads(render_json(run_diff(base, head, th())))
    assert "totals" in j and "scenarios" in j and "failed" in j
