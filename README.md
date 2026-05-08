# InferenceCI — `costdiff`

> Bundlewatch / size-limit, **but for LLM cost**.

`costdiff` replays a developer-defined set of LLM scenarios in CI, captures
token usage and cost via OpenTelemetry GenAI auto-instrumentation, compares
the PR run to a baseline from `main`, posts a sticky PR comment with the
diff, and fails the check if cost regression exceeds your thresholds.

The GitHub Action lives in a separate repo (`costdiff-action`); this repo is
the CLI, schemas, and core engine.

---

## Quickstart

```bash
pip install inferenceci

# scaffold
costdiff init
$EDITOR costdiff.yaml          # add scenarios, set thresholds
export OPENAI_API_KEY=...

# run twice and diff
costdiff run --output baseline.json
# (edit a prompt or swap a model)
costdiff run --output head.json
costdiff compare baseline.json head.json
```

Exit codes:

| code | meaning                            |
|-----:|------------------------------------|
| 0    | within thresholds                  |
| 1    | regression exceeded a threshold    |
| 2    | input error (missing file, schema) |

---

## CLI reference

| command                              | does                                                                |
|--------------------------------------|---------------------------------------------------------------------|
| `costdiff init [--force] [--dir D]`  | Scaffold `costdiff.yaml` + `scenarios/`                             |
| `costdiff run [--config P] [--output P] [--scenario NAME]` | Execute scenarios, write `report.json`     |
| `costdiff compare BASELINE HEAD [--config P] [--format text\|json\|markdown]` | Diff two reports, exit 0/1/2 |
| `costdiff pricing list [--pricing-file P]` | Print loaded pricing table                                    |
| `costdiff version`                   | Print version                                                       |

---

## Config reference (`costdiff.yaml`)

```yaml
version: 1                              # required, must be 1
runs_per_scenario: 3                    # 1–10
providers:
  openai:    { api_key_env: OPENAI_API_KEY }
  anthropic: { api_key_env: ANTHROPIC_API_KEY }

pricing_file: pricing.yaml              # optional; defaults to bundled

thresholds:
  cost_increase_pct: 15                 # fail if total cost up >15%
  scenario_cost_increase_pct: 25        # fail if any single scenario up >25%
  ignore_below_usd: 0.001               # ignore deltas under this absolute value

redact_prompts: true                    # default true; never write prompts to reports

scenarios:
  - name: support_bot_simple_query      # unique
    entrypoint: scenarios/support.py:run  # path-to-file:function_name
    input: { query: "How do I reset my password?" }
    timeout_seconds: 60
  - name: research_5_step
    entrypoint: scenarios/research.py:run
    input_file: scenarios/inputs/research_q1.json
    timeout_seconds: 300
```

Validation is **strict** — unknown keys are rejected with the field path.
`input` and `input_file` are mutually exclusive; exactly one must be set.
Scenario names must be unique. Timeouts are bounded 1–3600s.

### Scenario contract

```python
def run(input: dict) -> dict:
    # Make any number of OpenAI / Anthropic SDK calls.
    # Return value is recorded but never inspected.
    ...
```

The runner imports your entrypoint via `importlib`, invokes `run(input)`,
and captures every OTel span produced during the call. It runs each scenario
`runs_per_scenario` times.

### Pricing file (`pricing.yaml`)

Bundled by default. Override per-config with `pricing_file:`. Entries:

```yaml
version: 1
last_updated: 2026-05-01
providers:
  openai:
    gpt-4o:
      input_per_1m: 2.50
      output_per_1m: 10.00
      cached_input_per_1m: 1.25     # OpenAI prompt-cache hits
  anthropic:
    claude-opus-4-7:
      input_per_1m: 15.00
      output_per_1m: 75.00
      cache_write_per_1m: 18.75     # Anthropic cache creation
      cache_read_per_1m: 1.50       # Anthropic cache reads
```

Cost is computed per call as:

```
(input_tokens − cached − cache_read − cache_write) × input_rate
+ cached × cached_input_rate (or input_rate)
+ cache_read × cache_read_rate (or input_rate)
+ cache_write × cache_write_rate (or input_rate)
+ output_tokens × output_rate
```

If a model is missing from the table, the call's tokens are still counted
but cost is reported as `null` and the model surfaces in `report.warnings`.

---

## Threshold tuning guide

Three knobs:

- **`ignore_below_usd`** — absolute floor. Scenarios that move pennies
  shouldn't fail CI. Set to `0.001` (one tenth of a cent) or more for tiny
  scenarios. Set to `0` for cost-critical paths where any movement matters.
- **`cost_increase_pct`** (totals) — your "did the suite get more expensive
  overall?" gate. 15% is a reasonable default. Tighten to 5–10% on stable
  prompts; loosen to 25% during heavy refactors.
- **`scenario_cost_increase_pct`** (per scenario) — catches localized
  blowups even when totals are flat. Should be ≥ `cost_increase_pct`. 25%
  default tolerates ordinary prompt fiddling without firing on jitter.

`costdiff` also computes IQR overlap between baseline and head per metric.
If the interquartile ranges overlap, the metric is marked `noise` (⚪) and
will not fail the run, no matter how large the median delta. This is the
primary noise filter — bump `runs_per_scenario` if you see false positives.

Fail order (FR-7):

1. `|abs cost delta| < ignore_below_usd` → ignored.
2. IQR overlap → `noise`, no fail.
3. Total `pct_delta > cost_increase_pct` → fail.
4. Any scenario `pct_delta > scenario_cost_increase_pct` → fail.

---

## Report (`report.json`) shape

```jsonc
{
  "schema_version": 1,
  "generated_at": "2026-05-08T12:34:56Z",
  "git": {"commit": "...", "branch": "...", "merge_base": "..."},
  "config_hash": "sha256:...",
  "pricing_hash": "sha256:...",
  "totals": {"cost_usd": {...}, "input_tokens": {...}, ...},
  "scenarios": [
    {
      "name": "...",
      "runs": 3, "errors": 0,
      "metrics": {"cost_usd": {"median": ..., "p95": ..., "iqr": [..., ...], "samples": [...]}, ...},
      "calls_breakdown": [
        {"provider": "openai", "model": "gpt-4o", "calls": 1,
         "input_tokens_median": 800, "output_tokens_median": 200,
         "cost_median_usd": 0.004}
      ],
      "error_messages": []
    }
  ],
  "warnings": ["Models without pricing data: foo-bar"]
}
```

Top-level `totals` is the **sum** across scenarios of each summary statistic
(median + p95 + iqr endpoints), not a re-aggregation of raw samples.

---

## OpenTelemetry pinning

`costdiff` uses the official OTel GenAI auto-instrumentations and follows
the GenAI semantic conventions. The conventions are still evolving, so
versions are pinned. Tested with:

| package                                          | version  |
|--------------------------------------------------|----------|
| `opentelemetry-api`                              | 1.41.x   |
| `opentelemetry-sdk`                              | 1.41.x   |
| `opentelemetry-semantic-conventions`             | 0.62b1   |
| `opentelemetry-instrumentation-openai-v2`        | 2.4b0    |
| `opentelemetry-instrumentation-anthropic`        | 0.60.x   |
| `openai`                                         | ≥ 1.40   |
| `anthropic`                                      | ≥ 0.39   |

Attribute keys read from spans (multiple aliases supported):

- `gen_ai.system`, `gen_ai.provider.name` → provider
- `gen_ai.request.model`, `gen_ai.response.model` → model
- `gen_ai.usage.input_tokens`, `gen_ai.usage.prompt_tokens`
- `gen_ai.usage.output_tokens`, `gen_ai.usage.completion_tokens`
- `gen_ai.usage.cached_input_tokens`, `gen_ai.openai.usage.prompt_tokens_cached`
- `gen_ai.usage.cache_read_input_tokens` (Anthropic)
- `gen_ai.usage.cache_creation_input_tokens` (Anthropic)
- `gen_ai.response.finish_reasons` → tool-call count fallback

---

## Non-goals (v1)

Live observability, quality / accuracy eval, auto-fix PRs, multi-tenant
backend, per-customer attribution, new tracing protocols. See `PRD.md`
for the full list.

---

## Development

```bash
uv venv --python 3.11 .venv
uv pip install -e ".[dev]"
.venv/bin/pytest -q
.venv/bin/ruff check .
```

License: Proprietary. © 2026 InferenceLabs.
