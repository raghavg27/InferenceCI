from __future__ import annotations

from pathlib import Path

import pytest

from inferenceci.config_loader import load_config
from inferenceci.runner import RunnerError, run_all

STUB_SCENARIO = '''\
from opentelemetry import trace

def run(input: dict) -> dict:
    tracer = trace.get_tracer("stub")
    model = input.get("model", "gpt-4o")
    in_t = int(input.get("in_t", 1000))
    out_t = int(input.get("out_t", 500))
    with tracer.start_as_current_span(f"chat {model}") as sp:
        sp.set_attribute("gen_ai.system", input.get("provider", "openai"))
        sp.set_attribute("gen_ai.request.model", model)
        sp.set_attribute("gen_ai.usage.input_tokens", in_t)
        sp.set_attribute("gen_ai.usage.output_tokens", out_t)
    return {"ok": True}
'''


def _setup(tmp_path: Path, runs=3, scenarios_extra: str = "") -> Path:
    sdir = tmp_path / "scenarios"
    sdir.mkdir()
    (sdir / "stub.py").write_text(STUB_SCENARIO)
    cfg = tmp_path / "costdiff.yaml"
    cfg.write_text(
        f"""version: 1
runs_per_scenario: {runs}
providers:
  openai:
    api_key_env: OPENAI_API_KEY
thresholds:
  cost_increase_pct: 15
  scenario_cost_increase_pct: 25
  ignore_below_usd: 0.001
scenarios:
  - name: simple
    entrypoint: scenarios/stub.py:run
    input:
      model: gpt-4o
      in_t: 1000000
      out_t: 500000
    timeout_seconds: 5
{scenarios_extra}
"""
    )
    return cfg


def test_run_all_basic(tmp_path: Path):
    cfg_path = _setup(tmp_path, runs=3)
    config = load_config(cfg_path)
    report = run_all(config, cfg_path)

    assert report.schema_version == 1
    assert len(report.scenarios) == 1
    sr = report.scenarios[0]
    assert sr.name == "simple"
    assert sr.runs == 3
    assert sr.errors == 0
    # 1M input * 2.50 + 500k * 10.00 per 1M = 2.50 + 5.00 = 7.50
    assert sr.metrics.cost_usd.median == pytest.approx(7.50)
    assert sr.metrics.input_tokens.median == 1_000_000
    assert sr.metrics.output_tokens.median == 500_000
    assert sr.metrics.calls.median == 1
    # totals == sum across scenarios (single scenario)
    assert report.totals.cost_usd.median == pytest.approx(7.50)
    assert len(sr.calls_breakdown) == 1
    assert sr.calls_breakdown[0].model == "gpt-4o"
    assert sr.warnings == [] if hasattr(sr, "warnings") else True
    assert report.warnings == []


def test_run_all_unknown_model_warning(tmp_path: Path):
    cfg_path = _setup(tmp_path, runs=2)
    cfg_path.write_text(
        cfg_path.read_text().replace("model: gpt-4o", "model: foo-bar")
    )
    config = load_config(cfg_path)
    report = run_all(config, cfg_path)
    assert any("foo-bar" in w for w in report.warnings)
    assert report.scenarios[0].metrics.cost_usd.median == 0.0


def test_run_all_only_scenario(tmp_path: Path):
    extra = """  - name: second
    entrypoint: scenarios/stub.py:run
    input:
      model: gpt-4o-mini
      in_t: 100
      out_t: 50
    timeout_seconds: 5
"""
    cfg_path = _setup(tmp_path, runs=1, scenarios_extra=extra)
    config = load_config(cfg_path)
    report = run_all(config, cfg_path, only_scenario="second")
    assert len(report.scenarios) == 1
    assert report.scenarios[0].name == "second"


def test_run_all_unknown_only_scenario(tmp_path: Path):
    cfg_path = _setup(tmp_path, runs=1)
    config = load_config(cfg_path)
    with pytest.raises(RunnerError):
        run_all(config, cfg_path, only_scenario="nope")


def test_scenario_error_is_recorded(tmp_path: Path):
    sdir = tmp_path / "scenarios"
    sdir.mkdir()
    (sdir / "boom.py").write_text(
        "def run(input):\n    raise RuntimeError('boom')\n"
    )
    cfg = tmp_path / "costdiff.yaml"
    cfg.write_text(
        """version: 1
runs_per_scenario: 2
providers:
  openai:
    api_key_env: OPENAI_API_KEY
thresholds:
  cost_increase_pct: 15
  scenario_cost_increase_pct: 25
  ignore_below_usd: 0.001
scenarios:
  - name: boom
    entrypoint: scenarios/boom.py:run
    input: {}
    timeout_seconds: 5
"""
    )
    config = load_config(cfg)
    report = run_all(config, cfg)
    sr = report.scenarios[0]
    assert sr.errors == 2
    assert all("RuntimeError" in m for m in sr.error_messages)


def test_scenario_timeout(tmp_path: Path):
    sdir = tmp_path / "scenarios"
    sdir.mkdir()
    (sdir / "slow.py").write_text(
        "import time\ndef run(input):\n    time.sleep(5)\n    return {}\n"
    )
    cfg = tmp_path / "costdiff.yaml"
    cfg.write_text(
        """version: 1
runs_per_scenario: 1
providers:
  openai:
    api_key_env: OPENAI_API_KEY
thresholds:
  cost_increase_pct: 15
  scenario_cost_increase_pct: 25
  ignore_below_usd: 0.001
scenarios:
  - name: slow
    entrypoint: scenarios/slow.py:run
    input: {}
    timeout_seconds: 1
"""
    )
    config = load_config(cfg)
    report = run_all(config, cfg)
    sr = report.scenarios[0]
    assert sr.errors == 1
    assert "timeout" in sr.error_messages[0]


def test_input_file_loading(tmp_path: Path):
    sdir = tmp_path / "scenarios"
    sdir.mkdir()
    (sdir / "stub.py").write_text(STUB_SCENARIO)
    (sdir / "in.json").write_text('{"model":"gpt-4o","in_t":1000000,"out_t":500000}')
    cfg = tmp_path / "costdiff.yaml"
    cfg.write_text(
        """version: 1
runs_per_scenario: 1
providers:
  openai:
    api_key_env: OPENAI_API_KEY
thresholds:
  cost_increase_pct: 15
  scenario_cost_increase_pct: 25
  ignore_below_usd: 0.001
scenarios:
  - name: viafile
    entrypoint: scenarios/stub.py:run
    input_file: scenarios/in.json
    timeout_seconds: 5
"""
    )
    config = load_config(cfg)
    report = run_all(config, cfg)
    assert report.scenarios[0].metrics.cost_usd.median == pytest.approx(7.50)
