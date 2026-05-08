# costdiff

**Catch LLM cost regressions before they ship.**

`costdiff` runs your LLM scenarios on every pull request, measures the cost,
compares it to `main`, and posts a diff comment. If the PR makes your app
significantly more expensive, the check fails — just like a failing test.

Think `bundlewatch` or `size-limit`, but for tokens and dollars.

```diff
  scenario           baseline      head         Δ
  support_bot        $0.0042       $0.0044      +4.7%   🟢 ok
  research_agent     $0.0810       $0.1620     +100.0%  🔴 FAIL
  ─────────────────────────────────────────────────────────
  TOTAL              $0.0852       $0.1664      +95.3%  🔴 FAIL
```

---

## Why

A single prompt change, a model swap, or a new tool can quietly multiply
your bill. By the time monitoring catches it in production, you've already
paid.

`costdiff` shifts that signal **left**, into the PR review.

- **Deterministic.** Replays the same scenarios with the same inputs every run.
- **Provider-native.** Reads token usage from OpenAI and Anthropic SDKs via OpenTelemetry — no scraping, no estimation.
- **Noise-aware.** Repeats each scenario, computes IQR, and ignores deltas that fall inside the noise floor.
- **Drop-in.** One YAML file, one GitHub Action.

---

## 30-second quickstart

```bash
pip install inferenceci
costdiff init                       # creates costdiff.yaml + scenarios/
export OPENAI_API_KEY=...
costdiff run                        # writes report.json
```

That's it locally. Edit `scenarios/example_openai.py` to call your real code,
add scenarios you care about, and re-run.

---

## Use it in CI

Add this workflow to `.github/workflows/costdiff.yml`:

```yaml
name: costdiff
on:
  pull_request:

jobs:
  costdiff:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - uses: InferenceLabs/costdiff-action@v1
        with:
          config-path: costdiff.yaml
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

On every PR, the action will:

1. Run your scenarios on the PR head.
2. Run them again on the merge-base from `main`.
3. Diff the two reports.
4. Post or update a sticky comment on the PR.
5. Fail the check if cost regression exceeds your thresholds.

The PR comment looks like:

> ## costdiff
>
> **Result:** 🔴 FAIL — regressions exceed thresholds
>
> | metric | baseline | head | Δ | status |
> |---|---:|---:|---:|:---|
> | cost (USD) | $0.0852 | $0.1664 | +$0.0812 (+95.3%) | 🔴 regression |
> | input tokens | 12,000 | 24,000 | +12,000 (+100.0%) | 🔴 regression |
> | calls | 14 | 14 | 0 (0.0%) | ⚫ no_change |
>
> **Fail reasons:**
> - total cost regression +95.3% (threshold 15.0%)

---

## Configure (`costdiff.yaml`)

```yaml
version: 1
runs_per_scenario: 3                # repeat for noise control

providers:
  openai:    { api_key_env: OPENAI_API_KEY }
  anthropic: { api_key_env: ANTHROPIC_API_KEY }

thresholds:
  cost_increase_pct: 15             # fail if total cost up >15%
  scenario_cost_increase_pct: 25    # fail if any scenario up >25%
  ignore_below_usd: 0.001           # ignore changes smaller than this

scenarios:
  - name: support_bot
    entrypoint: scenarios/support.py:run
    input: { query: "How do I reset my password?" }
    timeout_seconds: 60

  - name: research_agent
    entrypoint: scenarios/research.py:run
    input_file: scenarios/inputs/research.json
    timeout_seconds: 300
```

A scenario is just a Python function:

```python
# scenarios/support.py
from openai import OpenAI

def run(input: dict) -> dict:
    client = OpenAI()
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": input["query"]}],
    )
    return {"answer": resp.choices[0].message.content}
```

`costdiff` doesn't care what the function returns — it only watches the
SDK calls it makes.

---

## CLI

```
costdiff init                                  # scaffold a project
costdiff run [--output report.json]            # run scenarios, write a report
costdiff compare baseline.json head.json       # diff two reports
costdiff pricing list                          # show the pricing table
costdiff version
```

`compare` exit codes: **0** within thresholds, **1** regression, **2** input error.

`compare` formats: `text` (default), `markdown` (PR comments), `json`
(machine-readable).

---

## Tuning thresholds

Three knobs — start with the defaults, tighten as your prompts stabilize.

| knob | default | tighten when… | loosen when… |
|---|---|---|---|
| `cost_increase_pct` | 15 | prompts are stable, cost is critical | actively iterating |
| `scenario_cost_increase_pct` | 25 | catching localized blowups matters | long-tail scenarios are noisy |
| `ignore_below_usd` | 0.001 | every cent counts | scenarios are tiny by design |

If you see false positives, **bump `runs_per_scenario`** before touching
thresholds. The IQR-overlap test treats noisy metrics as `noise` (⚪) and
won't fail on them, so more runs = less flake.

---

## What it tracks

For each scenario:

- **cost** in USD (per provider, per call, summed per run)
- input / output / cached tokens
- LLM calls + tool calls
- wall-clock latency

For each metric: **median**, **p95**, and **IQR** across runs.

Top-level totals are the sum across scenarios.

---

## Pricing

A pricing table for OpenAI and Anthropic ships with the package. To override:

```yaml
pricing_file: pricing.yaml
```

```yaml
# pricing.yaml
version: 1
last_updated: 2026-05-01
providers:
  openai:
    gpt-4o:
      input_per_1m: 2.50
      output_per_1m: 10.00
      cached_input_per_1m: 1.25
  anthropic:
    claude-opus-4-7:
      input_per_1m: 15.00
      output_per_1m: 75.00
      cache_write_per_1m: 18.75
      cache_read_per_1m: 1.50
```

If a model isn't in the table, its tokens are still counted, but the report
flags it under `warnings` and reports `cost_median_usd: null` for those calls.

---

## How it works

`costdiff` installs OpenTelemetry GenAI auto-instrumentation for the official
OpenAI and Anthropic SDKs, runs your scenarios in-process, and reads token
counts directly from the spans the instrumentations emit.

No SDK forks. No HTTP scraping. No accuracy tradeoffs. The same numbers your
provider would bill you for.

Versions are pinned because GenAI semantic conventions are still moving. See
[`docs/internals.md`](docs/internals.md) for the attribute list.

---

## Privacy

Prompts are **never** written to reports by default (`redact_prompts: true`).
Only token counts, costs, and model names leave your process. API keys are
read from environment variables and never logged.

---

## Frequently asked

**Does this call my LLM provider for real?**
Yes. Scenarios run real SDK calls, which is how you get real token counts
and real cost. Use small `max_tokens` and cheap models on the scenarios you
run in CI; or use prompt caching so the per-PR cost is cents, not dollars.

**Can I use it with LangChain / LangGraph / CrewAI?**
Yes — anything that ultimately calls the OpenAI or Anthropic SDKs is
instrumented automatically. Other providers are on the roadmap.

**What about quality? Doesn't a cheaper model mean worse output?**
`costdiff` is intentionally **not** a quality evaluator — it's a cost guard.
Pair it with your existing eval suite.

**My runs are noisy.**
Bump `runs_per_scenario` from 3 → 5 or 7. The IQR-overlap test will mark
overlapping metrics as noise instead of failing.

---

## Roadmap

- [ ] Google Gemini support
- [ ] Cached baselines (skip re-running `main`)
- [ ] OpenTelemetry trace export (send to your APM)
- [ ] VS Code inline annotations

Out of scope: live production monitoring, quality evaluation, auto-PRs,
cost-by-team attribution. See [`PRD.md`](PRD.md).

---

## Develop

```bash
uv venv --python 3.11 .venv
uv pip install -e ".[dev]"
.venv/bin/pytest -q
.venv/bin/ruff check .
```

---

## License

Proprietary. © 2026 InferenceLabs.
