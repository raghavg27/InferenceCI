# Internals

## OpenTelemetry pinning

GenAI semantic conventions are still evolving. We pin minor versions and
read multiple aliases per attribute to stay forward-compatible.

| package | tested |
|---|---|
| `opentelemetry-api` | 1.41.x |
| `opentelemetry-sdk` | 1.41.x |
| `opentelemetry-semantic-conventions` | 0.62b1 |
| `opentelemetry-instrumentation-openai-v2` | 2.4b0 |
| `opentelemetry-instrumentation-anthropic` | 0.60.x |
| `openai` | ≥ 1.40 |
| `anthropic` | ≥ 0.39 |

## Span attributes read

| concept | attribute(s) |
|---|---|
| provider | `gen_ai.system`, `gen_ai.provider.name` |
| model | `gen_ai.response.model`, `gen_ai.request.model` |
| input tokens | `gen_ai.usage.input_tokens`, `gen_ai.usage.prompt_tokens` |
| output tokens | `gen_ai.usage.output_tokens`, `gen_ai.usage.completion_tokens` |
| cached input (OpenAI) | `gen_ai.usage.cached_input_tokens`, `gen_ai.openai.usage.prompt_tokens_cached` |
| cache read (Anthropic) | `gen_ai.usage.cache_read_input_tokens` |
| cache write (Anthropic) | `gen_ai.usage.cache_creation_input_tokens` |
| tool calls | `gen_ai.response.finish_reasons` (counts `tool_calls`), span events |

## Cost formula

Per call:

```
regular_input  = max(0, input_tokens − cached_input − cache_read − cache_write)
cost = regular_input  * input_per_1m
     + cached_input   * (cached_input_per_1m or input_per_1m)
     + cache_read     * (cache_read_per_1m   or input_per_1m)
     + cache_write    * (cache_write_per_1m  or input_per_1m)
     + output_tokens  * output_per_1m
all rates per 1,000,000 tokens
```

## Aggregation

For each metric and each scenario, across `runs_per_scenario` runs:

- **median** — `statistics.median` over per-run values
- **p95** — linear-interpolation percentile (numpy-style)
- **iqr** — `(q25, q75)` linear-interpolation

Per-run values that fail to price (unknown model) contribute `None` and
are dropped from the aggregate.

Top-level `totals` is **the sum across scenarios** of each summary
statistic — not a re-aggregation over raw samples.

## Diff classification

Per metric:

```
abs_delta = head.median − baseline.median
pct_delta = abs_delta / |baseline.median| * 100
overlap   = baseline.iqr ∩ head.iqr ≠ ∅
```

Status (cost only — non-cost metrics skip the `ignored` check):

```
if cost and |abs_delta| < ignore_below_usd  → ignored
elif abs_delta == 0                          → no_change
elif overlap                                 → noise
elif pct_delta > 0                           → regression
else                                         → improvement
```

A run **fails** when:

- the totals' `cost_usd` status is `regression` and `pct_delta > cost_increase_pct`, or
- any scenario's `cost_usd` status is `regression` and `pct_delta > scenario_cost_increase_pct`.
