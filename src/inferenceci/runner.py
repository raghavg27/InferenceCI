from __future__ import annotations

import importlib.util
import json
import logging
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

from inferenceci.aggregate import stat, sum_stats
from inferenceci.config_loader import hash_bytes, hash_file
from inferenceci.extractor import CallMetrics, extract_calls
from inferenceci.git_info import collect as collect_git
from inferenceci.otel_setup import capture, install_instrumentation
from inferenceci.pricing import bundled_pricing_path, load_pricing_table
from inferenceci.schemas import (
    CallBreakdown,
    CostDiffConfig,
    MetricsBlock,
    PricingTable,
    Report,
    ScenarioConfig,
    ScenarioReport,
)

log = logging.getLogger(__name__)


class RunnerError(Exception):
    pass


def _load_module(path: Path) -> ModuleType:
    if not path.exists():
        raise RunnerError(f"entrypoint file not found: {path}")
    mod_name = f"_inferenceci_scenario_{abs(hash(str(path.resolve())))}"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise RunnerError(f"cannot load scenario module from {path}")
    module = importlib.util.module_from_spec(spec)
    parent = str(path.resolve().parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    sys.modules[mod_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        raise RunnerError(f"failed importing {path}: {e}") from e
    return module


def _resolve_input(scenario: ScenarioConfig, config_dir: Path) -> dict[str, Any]:
    if scenario.input is not None:
        return scenario.input
    assert scenario.input_file is not None
    p = (config_dir / scenario.input_file).resolve()
    try:
        return json.loads(p.read_text())
    except FileNotFoundError as e:
        raise RunnerError(f"input_file not found: {p}") from e
    except json.JSONDecodeError as e:
        raise RunnerError(f"input_file {p} is not valid JSON: {e}") from e


def _run_one(func, payload: dict, timeout_s: int) -> tuple[Any, float, str | None]:
    """Execute one scenario invocation. Returns (result, latency_ms, error)."""
    t0 = time.perf_counter()
    err: str | None = None
    result: Any = None
    with ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(func, payload)
        try:
            result = fut.result(timeout=timeout_s)
        except FuturesTimeout:
            err = f"timeout after {timeout_s}s"
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
    latency = (time.perf_counter() - t0) * 1000.0
    return result, latency, err


def _aggregate_calls_breakdown(per_run_calls: list[list[CallMetrics]]) -> list[CallBreakdown]:
    by_model: dict[tuple[str | None, str], list[CallMetrics]] = defaultdict(list)
    counts_per_run: dict[tuple[str | None, str], list[int]] = defaultdict(list)

    for run_calls in per_run_calls:
        run_counts: dict[tuple[str | None, str], int] = defaultdict(int)
        for c in run_calls:
            key = (c.provider, c.model)
            by_model[key].append(c)
            run_counts[key] += 1
        seen_keys = set(run_counts.keys()) | set(counts_per_run.keys())
        for k in seen_keys:
            counts_per_run[k].append(run_counts.get(k, 0))

    breakdown: list[CallBreakdown] = []
    for key, calls in by_model.items():
        provider, model = key
        in_tokens = [c.input_tokens for c in calls]
        out_tokens = [c.output_tokens for c in calls]
        costs = [c.cost_usd for c in calls if c.cost_usd is not None]
        breakdown.append(
            CallBreakdown(
                provider=provider,
                model=model,
                calls=stat(counts_per_run[key]).median if counts_per_run[key] else float(len(calls)),
                input_tokens_median=stat(in_tokens).median,
                output_tokens_median=stat(out_tokens).median,
                cost_median_usd=stat(costs).median if costs else None,
            )
        )
    breakdown.sort(key=lambda b: (b.provider or "", b.model))
    return breakdown


def _scenario_metrics(per_run_calls: list[list[CallMetrics]], per_run_latency: list[float]) -> MetricsBlock:
    cost_per_run: list[float | None] = []
    input_per_run: list[float] = []
    output_per_run: list[float] = []
    cached_per_run: list[float] = []
    calls_per_run: list[float] = []
    tool_calls_per_run: list[float] = []

    for run in per_run_calls:
        if not run:
            cost_per_run.append(0.0)
            input_per_run.append(0.0)
            output_per_run.append(0.0)
            cached_per_run.append(0.0)
            calls_per_run.append(0.0)
            tool_calls_per_run.append(0.0)
            continue
        total_cost = 0.0
        any_unpriced = False
        for c in run:
            if c.cost_usd is None:
                any_unpriced = True
            else:
                total_cost += c.cost_usd
        cost_per_run.append(None if any_unpriced else total_cost)
        input_per_run.append(sum(c.input_tokens for c in run))
        output_per_run.append(sum(c.output_tokens for c in run))
        cached_per_run.append(
            sum(c.cached_input_tokens + c.cache_read_tokens + c.cache_write_tokens for c in run)
        )
        calls_per_run.append(len(run))
        tool_calls_per_run.append(sum(c.tool_calls for c in run))

    return MetricsBlock(
        cost_usd=stat(cost_per_run),
        input_tokens=stat(input_per_run),
        output_tokens=stat(output_per_run),
        cached_tokens=stat(cached_per_run),
        calls=stat(calls_per_run),
        tool_calls=stat(tool_calls_per_run),
        latency_ms=stat(per_run_latency),
    )


def _run_scenario(
    scenario: ScenarioConfig,
    config: CostDiffConfig,
    config_dir: Path,
    pricing: PricingTable,
) -> tuple[ScenarioReport, set[str]]:
    rel_path, func_name = scenario.parsed_entrypoint()
    abs_path = (config_dir / rel_path).resolve()
    module = _load_module(abs_path)
    func = getattr(module, func_name, None)
    if not callable(func):
        raise RunnerError(f"{abs_path}:{func_name} is not a callable")

    payload = _resolve_input(scenario, config_dir)

    per_run_calls: list[list[CallMetrics]] = []
    per_run_latency: list[float] = []
    errors: list[str] = []
    missing_pricing: set[str] = set()

    for i in range(config.runs_per_scenario):
        with capture() as exp:
            _result, latency_ms, err = _run_one(func, payload, scenario.timeout_seconds)
        spans = exp.get_finished_spans()
        calls, missing = extract_calls(list(spans), pricing)
        if missing:
            missing_pricing |= missing
        per_run_calls.append(calls)
        per_run_latency.append(latency_ms)
        if err:
            errors.append(f"run {i + 1}: {err}")

    metrics = _scenario_metrics(per_run_calls, per_run_latency)
    breakdown = _aggregate_calls_breakdown(per_run_calls)

    return (
        ScenarioReport(
            name=scenario.name,
            runs=config.runs_per_scenario,
            errors=len(errors),
            metrics=metrics,
            calls_breakdown=breakdown,
            error_messages=errors,
        ),
        missing_pricing,
    )


def _totals(scenarios: list[ScenarioReport]) -> MetricsBlock:
    return MetricsBlock(
        cost_usd=sum_stats([s.metrics.cost_usd for s in scenarios]),
        input_tokens=sum_stats([s.metrics.input_tokens for s in scenarios]),
        output_tokens=sum_stats([s.metrics.output_tokens for s in scenarios]),
        cached_tokens=sum_stats([s.metrics.cached_tokens for s in scenarios]),
        calls=sum_stats([s.metrics.calls for s in scenarios]),
        tool_calls=sum_stats([s.metrics.tool_calls for s in scenarios]),
        latency_ms=sum_stats([s.metrics.latency_ms for s in scenarios]),
    )


def run_all(
    config: CostDiffConfig,
    config_path: Path,
    only_scenario: str | None = None,
) -> Report:
    install_instrumentation()
    config_dir = config_path.parent.resolve()

    pricing_path = (
        (config_dir / config.pricing_file).resolve() if config.pricing_file else bundled_pricing_path()
    )
    pricing, pricing_path_used = load_pricing_table(pricing_path)

    scenarios = config.scenarios
    if only_scenario:
        scenarios = [s for s in scenarios if s.name == only_scenario]
        if not scenarios:
            raise RunnerError(f"--scenario {only_scenario!r} not found in config")

    reports: list[ScenarioReport] = []
    all_missing: set[str] = set()
    for s in scenarios:
        rep, missing = _run_scenario(s, config, config_dir, pricing)
        reports.append(rep)
        all_missing |= missing

    warnings: list[str] = []
    if all_missing:
        warnings.append(
            f"Models without pricing data: {', '.join(sorted(all_missing))}"
        )

    return Report(
        schema_version=1,
        generated_at=datetime.now(UTC),
        git=collect_git(),
        config_hash=hash_file(config_path),
        pricing_hash=hash_bytes(pricing_path_used.read_bytes()),
        totals=_totals(reports),
        scenarios=reports,
        warnings=warnings,
    )
