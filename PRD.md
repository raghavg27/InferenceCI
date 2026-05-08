# PRD — Cost Diff CI (`InferenceCI`)

## For

Claude Code. Build this in order. Do not skip ahead. Ask before deviating.

## Product

A CLI tool and GitHub Action. Runs in CI on pull requests. Replays a developer-defined set of LLM scenarios. Measures token usage, model calls, tool calls, latency, and cost. Compares the PR run against a baseline from `main`. Posts a sticky PR comment with the diff. Fails the CI check if cost regression exceeds configured thresholds.

Bundlewatch / size-limit, but for LLM cost.

## User

Engineering teams shipping LLM-backed features. They use OpenAI or Anthropic SDKs (or LangChain / LangGraph / CrewAI / OpenAI Agents SDK on top). They run GitHub Actions. They have been burned by a deploy that 4x'd their bill.

## Problem

A prompt change, a new tool, a memory-module rewrite, or a bad model swap can multiply LLM cost overnight. Existing observability tools detect it after it ships. We want to detect it before merge.

## Non-goals (v1)

- Live production observability
- Quality / accuracy evaluation
- Auto-fix or auto-PR generation
- Multi-tenant SaaS backend
- Per-customer cost attribution
- A new tracing protocol (use OTel GenAI semantic conventions)

## User stories

1. As a dev, I run `costdiff init` and get a working starter config.
2. As a dev, I run `costdiff run` locally and see per-scenario cost.
3. As a dev, I open a PR, GitHub Action runs `costdiff`, comments the diff, and fails the check if I regressed cost > threshold.
4. As a tech lead, I edit `costdiff.yaml` to add a new scenario or change thresholds without touching code.
5. As a dev, I trust the diff is not noise — repeated runs converge.

## Functional requirements

### FR-1. Config file

Path: `costdiff.yaml` at repo root.

Schema (Pydantic, strict):

```yaml
version: 1
runs_per_scenario: 3                    # int, 1–10
providers:
  openai:
    api_key_env: OPENAI_API_KEY
  anthropic:
    api_key_env: ANTHROPIC_API_KEY
pricing_file: pricing.yaml              # path, optional, defaults to bundled
thresholds:
  cost_increase_pct: 15                 # fail if total cost up >15%
  scenario_cost_increase_pct: 25        # fail if any single scenario up >25%
  ignore_below_usd: 0.001               # ignore deltas under this absolute value
scenarios:
  - name: support_bot_simple_query
    entrypoint: scenarios/support_bot.py:run
    input:
      query: "How do I reset my password?"
    timeout_seconds: 60
  - name: research_agent_5_step
    entrypoint: scenarios/research.py:run
    input_file: scenarios/inputs/research_q1.json
    timeout_seconds: 300
```

Validate strictly. Reject unknown keys. Print actionable errors with line numbers.

### FR-2. CLI commands

All commands return non-zero on error. Use Click. Use `rich` for output.

- `costdiff init` — creates `costdiff.yaml`, `pricing.yaml`, `scenarios/` directory with one example scenario file. Idempotent: refuse to overwrite existing files unless `--force`.
- `costdiff run [--config PATH] [--output PATH] [--scenario NAME]` — executes scenarios per config. Writes `report.json` (default `./report.json`). `--scenario` runs only one.
- `costdiff compare BASELINE_JSON HEAD_JSON [--config PATH] [--format text|json|markdown]` — diffs two reports. Prints to stdout. Exit code 0 if within thresholds, 1 if exceeded, 2 on input error.
- `costdiff pricing list` — prints loaded pricing table.
- `costdiff version` — prints version.

### FR-3. Scenario execution

A scenario is a Python function with signature:

```python
def run(input: dict) -> dict:
    # Calls one or more LLM providers via OpenAI/Anthropic SDKs.
    # Returns whatever; we don't care, we capture telemetry.
```

Runner imports the entrypoint via `importlib`, invokes `run(input)`, captures all OTel spans during execution, repeats `runs_per_scenario` times.

For each run, capture:
- Wall-clock latency (ms)
- Per-LLM-call: provider, model, prompt tokens, completion tokens, cached tokens, cost (computed from pricing), tool calls if surfaced
- Total per-run: tokens, cost, calls, tool calls

Aggregate across runs: median, p95, IQR for each metric.

### FR-4. Instrumentation

Use existing OTel auto-instrumentation:
- `opentelemetry-instrumentation-openai-v2`
- `opentelemetry-instrumentation-anthropic` (or community equivalent)

Set up an in-process OTel SDK with a custom span exporter that writes to memory. Read spans after each run.

Follow OTel GenAI semantic conventions for attribute names (`gen_ai.usage.input_tokens`, `gen_ai.response.model`, etc.). Document the version pinned.

### FR-5. Pricing table

Static YAML at `pricing.yaml`. Bundled default in package. Override via config.

Schema:

```yaml
version: 1
last_updated: 2026-05-01
providers:
  openai:
    gpt-4o:
      input_per_1m: 2.50
      output_per_1m: 10.00
      cached_input_per_1m: 1.25
    gpt-4o-mini:
      input_per_1m: 0.15
      output_per_1m: 0.60
      cached_input_per_1m: 0.075
  anthropic:
    claude-opus-4-7:
      input_per_1m: 15.00
      output_per_1m: 75.00
      cache_write_per_1m: 18.75
      cache_read_per_1m: 1.50
```

Cost calc: `(input_tokens - cached_tokens) * input_rate + cached_tokens * cached_rate + output_tokens * output_rate`. Apply per call.

If a model is missing from the table, log a warning, count tokens but report cost as `null` for that call. Surface this prominently in the report ("3 calls had no pricing data").

### FR-6. Report JSON schema

```json
{
  "schema_version": 1,
  "generated_at": "2026-05-08T12:34:56Z",
  "git": {
    "commit": "abc123",
    "branch": "feature/new-prompt",
    "merge_base": "def456"
  },
  "config_hash": "sha256:...",
  "pricing_hash": "sha256:...",
  "totals": {
    "cost_usd": { "median": 0.42, "p95": 0.51, "iqr": [0.40, 0.45] },
    "input_tokens": { "median": 12000, "p95": 13500, "iqr": [11800, 12400] },
    "output_tokens": { ... },
    "calls": { "median": 14, ... },
    "latency_ms": { "median": 4200, "p95": 5100, ... }
  },
  "scenarios": [
    {
      "name": "support_bot_simple_query",
      "runs": 3,
      "errors": 0,
      "metrics": { ...same shape as totals... },
      "calls_breakdown": [
        { "model": "gpt-4o", "calls": 1, "input_tokens_median": 800, "output_tokens_median": 200, "cost_median_usd": 0.004 }
      ]
    }
  ],
  "warnings": ["Model 'foo-bar' not in pricing table; 2 calls"]
}
```

### FR-7. Diff engine

`costdiff compare baseline.json head.json`:

For each metric, compute:
- Absolute delta (head − baseline)
- Percent delta
- IQR overlap test: if `head.iqr` overlaps `baseline.iqr`, mark as "noise"

Apply thresholds in this order:
1. If absolute cost delta < `ignore_below_usd` → ignore.
2. If IQR overlap → mark as `noise`, do not fail.
3. If pct delta > `cost_increase_pct` (totals) → fail.
4. If any scenario pct delta > `scenario_cost_increase_pct` → fail.

Output formats:
- `text` — `rich` table for terminal.
- `markdown` — table suitable for GitHub PR comment, with emoji indicators (🔴 regression, 🟢 improvement, ⚪ noise, ⚫ no change).
- `json` — machine-readable diff result.

Exit codes:
- 0 = within thresholds
- 1 = thresholds exceeded
- 2 = input error (file missing, schema mismatch)

### FR-8. GitHub Action (separate repo, second deliverable)

Repo name: `costdiff-action`. Composite action.

Inputs:
- `config-path` (default `costdiff.yaml`)
- `baseline-source` (default `merge-base`; alt: `main-artifact`)
- `comment-on-pr` (default `true`)
- `fail-on-regression` (default `true`)

Behavior:
1. Check out PR head.
2. Install `costdiff` (pip).
3. Run `costdiff run` → `head_report.json`.
4. Resolve baseline: check out merge base, run again → `baseline_report.json`. (Cache later.)
5. `costdiff compare baseline_report.json head_report.json --format markdown` → comment body.
6. Post or update sticky comment via `gh` CLI or GitHub API. Use marker `<!-- costdiff:comment -->`.
7. Set check status from exit code.

## Non-functional requirements

- Python 3.11+ supported.
- Cold install + first run on small repo < 30 seconds (excluding LLM call time).
- No network calls except to configured LLM providers and explicit pricing fetches.
- All secrets read from env vars, never logged.
- Optional prompt redaction in reports (`redact_prompts: true` in config).
- Apache 2.0 license.

## Acceptance criteria — v1 ships when

- `costdiff init` produces a working scaffold.
- `costdiff run` executes 3 example scenarios (one OpenAI, one Anthropic, one multi-step agent) and writes a valid `report.json`.
- `costdiff compare` produces correct markdown for two known reports.
- A demo PR in a test repo shows the GitHub Action posting a real comment and failing on a deliberate regression.
- Unit tests cover: config validation, pricing calc (incl. cached tokens), diff engine threshold logic, IQR overlap.
- Integration test: end-to-end run against a stubbed provider that returns canned responses with known token counts.
- README with quickstart, full config reference, threshold tuning guide.

## Tech stack — locked

- Python 3.11+
- Click (CLI)
- Pydantic v2 (config + schemas)
- OpenTelemetry SDK + GenAI auto-instrumentations (pinned versions, document them)
- httpx (only if needed beyond SDKs)
- rich (terminal output)
- pytest (tests)
- uv (package + venv mgmt)
- ruff (lint + format)

## Build order — strict

1. Repo scaffold, `pyproject.toml`, `uv` setup, ruff config, pre-commit.
2. Pydantic schemas: config, report, pricing.
3. Pricing module + bundled `pricing.yaml` (start with OpenAI + Anthropic only).
4. OTel instrumentation setup. Verify spans capture token counts in unit test with stubbed provider.
5. Runner: load entrypoint, execute N times, aggregate, write report.
6. CLI: `init`, `run`, `pricing list`, `version`.
7. Diff engine + thresholds + IQR overlap logic.
8. CLI: `compare` with text + markdown + json formats.
9. Example scenarios (3): OpenAI single-shot, Anthropic single-shot, fake multi-step agent.
10. Integration test against stubbed providers.
11. README + docs.
12. Tag v0.1.0, publish to PyPI.

After v0.1.0 lands and is usable, start `costdiff-action` repo.

## Open questions answered by the human (do not assume)

1. Project name: keep `costdiff` or change? -> `InferenceCI`
2. PyPI package name same as project? -> Yes
3. GitHub org for the repo? -> `InferenceLabs`
4. Initial provider list: OpenAI + Anthropic only for v1, or add Google Gemini up front? -> OpenAI + Anthropic only
5. Should `compare` also accept a remote URL for baseline (e.g. fetch from GitHub artifacts)? -> No (manual file upload for now)
6. Prompt redaction default: on or off? -> On
7. License confirmed Apache 2.0? -> `Proprietary`

## Out of scope — explicit list (v1)

- Hosted backend / dashboard
- Slack / Linear integration
- LangSmith / Langfuse trace ingestion (we generate our own traces)
- Quality eval / LLM-as-judge scoring
- Auto-remediation, auto-PR
- Cost attribution by team / customer / feature
- Prompt efficiency scoring
- Recursive loop detection (separate product, later)
- Anything that runs in production

If a request lands in this list, log it in `BACKLOG.md` and move on.

## Notes for Claude Code

- Do not invent features not in this PRD.
- Do not add dependencies not listed in tech stack without flagging first.
- Write tests alongside features, not after.
- Commit small. Conventional commits.
- If a requirement here is ambiguous, surface it in the open questions list and stop. Do not guess.
- OTel GenAI semantic conventions are still evolving. Pin specific package versions and document them in the README.
- When in doubt, ship narrower, not wider.
