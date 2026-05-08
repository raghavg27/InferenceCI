"""End-to-end against a stubbed OpenAI HTTP backend.

Uses httpx.MockTransport to return canned chat-completion responses with known
token counts. Exercises the real OpenAI SDK -> OTel auto-instrumentation ->
runner aggregation -> report write -> diff path.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from inferenceci.config_loader import load_config
from inferenceci.diff import load_report, render_markdown, run_diff
from inferenceci.runner import run_all


def _canned_chat_response(model: str, prompt_tokens: int, completion_tokens: int) -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 0,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "pong"},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


@pytest.fixture
def stub_openai(monkeypatch):
    """Patch openai.OpenAI to inject an httpx MockTransport returning canned data."""
    from openai import OpenAI as _RealOpenAI

    state = {"prompt_tokens": 800_000, "completion_tokens": 200_000, "model": "gpt-4o-mini"}

    def _handler(request: httpx.Request) -> httpx.Response:
        body = _canned_chat_response(state["model"], state["prompt_tokens"], state["completion_tokens"])
        return httpx.Response(200, json=body)

    transport = httpx.MockTransport(_handler)

    real_init = _RealOpenAI.__init__

    def patched_init(self, *args, **kwargs):
        kwargs.setdefault("api_key", "test-key")
        kwargs["http_client"] = httpx.Client(transport=transport, base_url="https://api.openai.com")
        return real_init(self, *args, **kwargs)

    monkeypatch.setattr(_RealOpenAI, "__init__", patched_init)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    return state


SCENARIO_FILE = '''\
from openai import OpenAI


def run(input: dict) -> dict:
    client = OpenAI()
    resp = client.chat.completions.create(
        model=input["model"],
        messages=[{"role": "user", "content": input["query"]}],
        max_tokens=16,
    )
    return {"answer": resp.choices[0].message.content}
'''


def _write_project(tmp_path: Path, model: str = "gpt-4o-mini") -> Path:
    sdir = tmp_path / "scenarios"
    sdir.mkdir()
    (sdir / "ping.py").write_text(SCENARIO_FILE)
    cfg = tmp_path / "costdiff.yaml"
    cfg.write_text(
        f"""version: 1
runs_per_scenario: 3
providers:
  openai: {{api_key_env: OPENAI_API_KEY}}
thresholds:
  cost_increase_pct: 15
  scenario_cost_increase_pct: 25
  ignore_below_usd: 0.001
scenarios:
  - name: ping
    entrypoint: scenarios/ping.py:run
    input:
      model: {model}
      query: "ping"
    timeout_seconds: 30
"""
    )
    return cfg


def test_end_to_end_run_and_compare(tmp_path: Path, stub_openai):
    cfg = _write_project(tmp_path)
    config = load_config(cfg)

    # Baseline: 800k in / 200k out per run @ gpt-4o-mini
    # cost = (800k * 0.15 + 200k * 0.60) / 1e6 = 0.12 + 0.12 = 0.24 / run
    baseline_path = tmp_path / "baseline.json"
    baseline = run_all(config, cfg)
    baseline_path.write_text(baseline.model_dump_json(indent=2))

    sr = baseline.scenarios[0]
    assert sr.runs == 3
    assert sr.errors == 0
    assert sr.metrics.input_tokens.median == 800_000
    assert sr.metrics.output_tokens.median == 200_000
    assert sr.metrics.calls.median == 1
    assert sr.metrics.cost_usd.median == pytest.approx(0.24, abs=1e-9)
    assert sr.calls_breakdown
    assert sr.calls_breakdown[0].model == "gpt-4o-mini"

    # Head: regress tokens 4x -> cost 4x
    stub_openai["prompt_tokens"] = 3_200_000
    stub_openai["completion_tokens"] = 800_000
    head_path = tmp_path / "head.json"
    head = run_all(config, cfg)
    head_path.write_text(head.model_dump_json(indent=2))

    base = load_report(baseline_path)
    h = load_report(head_path)
    diff = run_diff(base, h, config.thresholds)
    assert diff.failed
    assert diff.total_cost is not None
    assert diff.total_cost.pct_delta > 100  # 4x increase
    md = render_markdown(diff)
    assert "🔴" in md and "<!-- costdiff:comment -->" in md


def test_known_reports_diff_is_deterministic(tmp_path: Path, stub_openai):
    """Same baseline twice should diff as no_change / noise (within thresholds)."""
    cfg = _write_project(tmp_path)
    config = load_config(cfg)

    a = run_all(config, cfg)
    b = run_all(config, cfg)
    diff = run_diff(a, b, config.thresholds)
    assert not diff.failed
    assert diff.total_cost is not None
    assert diff.total_cost.status in ("no_change", "noise", "ignored")


def test_unknown_model_warns(tmp_path: Path, stub_openai):
    cfg = _write_project(tmp_path, model="not-a-real-model")
    stub_openai["model"] = "not-a-real-model"
    config = load_config(cfg)
    report = run_all(config, cfg)
    assert any("not-a-real-model" in w for w in report.warnings)
