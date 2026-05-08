"""Diff two reports under a threshold policy.

Threshold ordering (FR-7):
  1. abs cost delta < ignore_below_usd  -> ignore (status='ignored')
  2. IQR overlap                          -> noise (status='noise', does not fail)
  3. totals pct delta > cost_increase_pct -> fail
  4. any scenario pct > scenario_cost_increase_pct -> fail
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from inferenceci.schemas import MetricsBlock, MetricStat, Report, ThresholdConfig


class DiffInputError(Exception):
    pass


def load_report(path: Path) -> Report:
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError as e:
        raise DiffInputError(f"{path}: file not found") from e
    except json.JSONDecodeError as e:
        raise DiffInputError(f"{path}: invalid JSON: {e}") from e
    try:
        return Report.model_validate(data)
    except ValidationError as e:
        raise DiffInputError(f"{path}: schema mismatch: {e.error_count()} error(s)") from e


def _iqr_overlap(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return a[0] <= b[1] and b[0] <= a[1]


def _pct(baseline: float, head: float) -> float:
    if baseline == 0:
        return 0.0 if head == 0 else float("inf")
    return (head - baseline) / abs(baseline) * 100.0


# Status set:
#   regression — head exceeds baseline beyond threshold (red)
#   improvement — head below baseline beyond threshold (green)
#   noise — IQR overlap; ignore (white circle)
#   ignored — abs cost under ignore_below_usd
#   no_change — exact zero delta
_STATUS_EMOJI = {
    "regression": "🔴",
    "improvement": "🟢",
    "noise": "⚪",
    "ignored": "⚪",
    "no_change": "⚫",
}


@dataclass
class MetricDiff:
    metric: str
    baseline_median: float
    head_median: float
    abs_delta: float
    pct_delta: float
    iqr_overlap: bool
    status: str  # see _STATUS_EMOJI keys

    @property
    def emoji(self) -> str:
        return _STATUS_EMOJI.get(self.status, "")


@dataclass
class ScenarioDiff:
    name: str
    metrics: list[MetricDiff] = field(default_factory=list)
    cost_status: str = "no_change"  # the cost_usd metric's status drives gating
    fail_reasons: list[str] = field(default_factory=list)

    @property
    def cost(self) -> MetricDiff | None:
        for m in self.metrics:
            if m.metric == "cost_usd":
                return m
        return None


@dataclass
class DiffResult:
    totals: list[MetricDiff]
    scenarios: list[ScenarioDiff]
    failed: bool
    fail_reasons: list[str]
    thresholds: dict
    notes: list[str] = field(default_factory=list)

    @property
    def total_cost(self) -> MetricDiff | None:
        for m in self.totals:
            if m.metric == "cost_usd":
                return m
        return None

    def to_dict(self) -> dict:
        return {
            "totals": [asdict(m) for m in self.totals],
            "scenarios": [
                {
                    "name": s.name,
                    "metrics": [asdict(m) for m in s.metrics],
                    "cost_status": s.cost_status,
                    "fail_reasons": s.fail_reasons,
                }
                for s in self.scenarios
            ],
            "failed": self.failed,
            "fail_reasons": self.fail_reasons,
            "thresholds": self.thresholds,
            "notes": self.notes,
        }


_METRIC_ORDER: tuple[str, ...] = (
    "cost_usd",
    "input_tokens",
    "output_tokens",
    "cached_tokens",
    "calls",
    "tool_calls",
    "latency_ms",
)


def _classify(
    metric: str,
    a: MetricStat,
    b: MetricStat,
    thresholds: ThresholdConfig,
    is_cost: bool,
    is_total: bool,
) -> MetricDiff:
    abs_delta = b.median - a.median
    pct_delta = _pct(a.median, b.median)
    overlap = _iqr_overlap(a.iqr, b.iqr)

    if is_cost and abs(abs_delta) < thresholds.ignore_below_usd:
        status = "ignored"
    elif abs_delta == 0:
        status = "no_change"
    elif overlap:
        status = "noise"
    elif pct_delta > 0:
        status = "regression"
    else:
        status = "improvement"

    return MetricDiff(
        metric=metric,
        baseline_median=a.median,
        head_median=b.median,
        abs_delta=abs_delta,
        pct_delta=pct_delta,
        iqr_overlap=overlap,
        status=status,
    )


def _diff_block(
    a: MetricsBlock, b: MetricsBlock, thresholds: ThresholdConfig, is_total: bool
) -> list[MetricDiff]:
    out: list[MetricDiff] = []
    for m in _METRIC_ORDER:
        out.append(
            _classify(
                m,
                getattr(a, m),
                getattr(b, m),
                thresholds,
                is_cost=(m == "cost_usd"),
                is_total=is_total,
            )
        )
    return out


def run_diff(baseline: Report, head: Report, thresholds: ThresholdConfig) -> DiffResult:
    totals = _diff_block(baseline.totals, head.totals, thresholds, is_total=True)

    base_by_name = {s.name: s for s in baseline.scenarios}
    head_by_name = {s.name: s for s in head.scenarios}

    scenarios: list[ScenarioDiff] = []
    notes: list[str] = []
    for name in head_by_name.keys() | base_by_name.keys():
        h = head_by_name.get(name)
        b = base_by_name.get(name)
        if h is None:
            notes.append(f"scenario {name!r} present in baseline but missing in head")
            continue
        if b is None:
            notes.append(f"scenario {name!r} new in head (no baseline)")
            sd = ScenarioDiff(name=name, metrics=[], cost_status="new")
            scenarios.append(sd)
            continue
        metrics = _diff_block(b.metrics, h.metrics, thresholds, is_total=False)
        cost = next((m for m in metrics if m.metric == "cost_usd"), None)
        sd = ScenarioDiff(
            name=name,
            metrics=metrics,
            cost_status=cost.status if cost else "no_change",
        )
        scenarios.append(sd)

    fail_reasons: list[str] = []
    total_cost = next((m for m in totals if m.metric == "cost_usd"), None)
    if (
        total_cost
        and total_cost.status == "regression"
        and total_cost.pct_delta > thresholds.cost_increase_pct
    ):
        fail_reasons.append(
            f"total cost regression {total_cost.pct_delta:+.1f}% "
            f"(threshold {thresholds.cost_increase_pct:.1f}%)"
        )
    for s in scenarios:
        c = s.cost
        if (
            c
            and c.status == "regression"
            and c.pct_delta > thresholds.scenario_cost_increase_pct
        ):
            reason = (
                f"scenario {s.name!r} cost regression {c.pct_delta:+.1f}% "
                f"(threshold {thresholds.scenario_cost_increase_pct:.1f}%)"
            )
            s.fail_reasons.append(reason)
            fail_reasons.append(reason)

    scenarios.sort(key=lambda s: s.name)

    return DiffResult(
        totals=totals,
        scenarios=scenarios,
        failed=bool(fail_reasons),
        fail_reasons=fail_reasons,
        thresholds={
            "cost_increase_pct": thresholds.cost_increase_pct,
            "scenario_cost_increase_pct": thresholds.scenario_cost_increase_pct,
            "ignore_below_usd": thresholds.ignore_below_usd,
        },
        notes=notes,
    )


# ----- renderers -----------------------------------------------------------

_PRETTY_METRIC = {
    "cost_usd": "cost (USD)",
    "input_tokens": "input tokens",
    "output_tokens": "output tokens",
    "cached_tokens": "cached tokens",
    "calls": "calls",
    "tool_calls": "tool calls",
    "latency_ms": "latency (ms)",
}


def _fmt_num(metric: str, v: float) -> str:
    if metric == "cost_usd":
        return f"${v:.4f}"
    if metric == "latency_ms":
        return f"{v:.0f}"
    return f"{v:,.0f}"


def _fmt_delta(metric: str, m: MetricDiff) -> str:
    abs_str = _fmt_num(metric, m.abs_delta)
    sign = "+" if m.abs_delta > 0 and not abs_str.startswith("-") else ""
    pct = "∞" if m.pct_delta == float("inf") else f"{m.pct_delta:+.1f}%"
    return f"{sign}{abs_str} ({pct})"


def render_text(diff: DiffResult, console: Console) -> None:
    console.rule("[bold]costdiff[/bold]")
    if diff.failed:
        console.print("[bold red]FAIL[/bold red]: regressions exceed thresholds")
    else:
        console.print("[bold green]PASS[/bold green]: within thresholds")

    t = Table(title="Totals", show_lines=False)
    t.add_column("metric")
    t.add_column("baseline", justify="right")
    t.add_column("head", justify="right")
    t.add_column("Δ", justify="right")
    t.add_column("status", justify="left")
    for m in diff.totals:
        t.add_row(
            _PRETTY_METRIC.get(m.metric, m.metric),
            _fmt_num(m.metric, m.baseline_median),
            _fmt_num(m.metric, m.head_median),
            _fmt_delta(m.metric, m),
            f"{m.emoji} {m.status}",
        )
    console.print(t)

    for s in diff.scenarios:
        sub = Table(title=f"scenario: {s.name}", show_lines=False)
        sub.add_column("metric")
        sub.add_column("baseline", justify="right")
        sub.add_column("head", justify="right")
        sub.add_column("Δ", justify="right")
        sub.add_column("status")
        for m in s.metrics:
            sub.add_row(
                _PRETTY_METRIC.get(m.metric, m.metric),
                _fmt_num(m.metric, m.baseline_median),
                _fmt_num(m.metric, m.head_median),
                _fmt_delta(m.metric, m),
                f"{m.emoji} {m.status}",
            )
        console.print(sub)

    for r in diff.fail_reasons:
        console.print(f"[red]✗[/red] {r}")
    for n in diff.notes:
        console.print(f"[yellow]note:[/yellow] {n}")


def render_markdown(diff: DiffResult) -> str:
    lines: list[str] = []
    lines.append("<!-- costdiff:comment -->")
    lines.append("## costdiff")
    lines.append("")
    lines.append(f"**Result:** {'🔴 FAIL — regressions exceed thresholds' if diff.failed else '🟢 PASS — within thresholds'}")
    lines.append("")
    lines.append("### Totals")
    lines.append("| metric | baseline | head | Δ | status |")
    lines.append("|---|---:|---:|---:|:---|")
    for m in diff.totals:
        lines.append(
            f"| {_PRETTY_METRIC.get(m.metric, m.metric)} "
            f"| {_fmt_num(m.metric, m.baseline_median)} "
            f"| {_fmt_num(m.metric, m.head_median)} "
            f"| {_fmt_delta(m.metric, m)} "
            f"| {m.emoji} {m.status} |"
        )
    lines.append("")
    for s in diff.scenarios:
        if not s.metrics:
            lines.append(f"### scenario: `{s.name}` — _new (no baseline)_")
            lines.append("")
            continue
        cost = s.cost
        emoji = cost.emoji if cost else ""
        lines.append(f"### scenario: `{s.name}` {emoji}")
        lines.append("| metric | baseline | head | Δ | status |")
        lines.append("|---|---:|---:|---:|:---|")
        for m in s.metrics:
            lines.append(
                f"| {_PRETTY_METRIC.get(m.metric, m.metric)} "
                f"| {_fmt_num(m.metric, m.baseline_median)} "
                f"| {_fmt_num(m.metric, m.head_median)} "
                f"| {_fmt_delta(m.metric, m)} "
                f"| {m.emoji} {m.status} |"
            )
        lines.append("")
    if diff.fail_reasons:
        lines.append("**Fail reasons:**")
        for r in diff.fail_reasons:
            lines.append(f"- {r}")
        lines.append("")
    if diff.notes:
        lines.append("**Notes:**")
        for n in diff.notes:
            lines.append(f"- {n}")
        lines.append("")
    th = diff.thresholds
    lines.append(
        f"_Thresholds: total ≤ {th['cost_increase_pct']}%, scenario ≤ "
        f"{th['scenario_cost_increase_pct']}%, ignore-below ${th['ignore_below_usd']}._"
    )
    return "\n".join(lines)


def render_json(diff: DiffResult) -> str:
    return json.dumps(diff.to_dict(), indent=2)
