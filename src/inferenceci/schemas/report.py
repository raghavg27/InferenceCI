from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

_STRICT = ConfigDict(extra="forbid", strict=False)


class GitInfo(BaseModel):
    model_config = _STRICT
    commit: str | None = None
    branch: str | None = None
    merge_base: str | None = None


class MetricStat(BaseModel):
    model_config = _STRICT
    median: float
    p95: float
    iqr: tuple[float, float]
    samples: list[float] = Field(default_factory=list)


class MetricsBlock(BaseModel):
    """Aggregated metrics across runs.

    `cost_usd` may carry None samples when pricing is unavailable for some
    calls; the median/p95 are computed over runs where all calls had pricing.
    Per-run aggregates that can't be priced contribute NaN samples and are
    excluded from stats.
    """

    model_config = _STRICT
    cost_usd: MetricStat
    input_tokens: MetricStat
    output_tokens: MetricStat
    cached_tokens: MetricStat
    calls: MetricStat
    tool_calls: MetricStat
    latency_ms: MetricStat


class CallBreakdown(BaseModel):
    model_config = _STRICT
    provider: str | None = None
    model: str
    calls: float
    input_tokens_median: float
    output_tokens_median: float
    cost_median_usd: float | None = None


class ScenarioReport(BaseModel):
    model_config = _STRICT
    name: str
    runs: int
    errors: int
    metrics: MetricsBlock
    calls_breakdown: list[CallBreakdown] = Field(default_factory=list)
    error_messages: list[str] = Field(default_factory=list)


class Report(BaseModel):
    model_config = _STRICT

    schema_version: int = 1
    generated_at: datetime
    git: GitInfo = Field(default_factory=GitInfo)
    config_hash: str
    pricing_hash: str
    totals: MetricsBlock
    scenarios: list[ScenarioReport]
    warnings: list[str] = Field(default_factory=list)
