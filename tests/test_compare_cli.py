from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from inferenceci.cli import main

CFG = """version: 1
runs_per_scenario: 1
providers:
  openai: {api_key_env: OPENAI_API_KEY}
thresholds:
  cost_increase_pct: 15
  scenario_cost_increase_pct: 25
  ignore_below_usd: 0.001
scenarios:
  - name: s
    entrypoint: scenarios/x.py:run
    input: {}
"""


def _report(cost: float, cost_iqr: tuple[float, float] | None = None) -> dict:
    iqr = cost_iqr or (cost, cost)
    block = {
        "cost_usd": {"median": cost, "p95": cost, "iqr": list(iqr), "samples": []},
        "input_tokens": {"median": 1000, "p95": 1000, "iqr": [1000, 1000], "samples": []},
        "output_tokens": {"median": 500, "p95": 500, "iqr": [500, 500], "samples": []},
        "cached_tokens": {"median": 0, "p95": 0, "iqr": [0, 0], "samples": []},
        "calls": {"median": 1, "p95": 1, "iqr": [1, 1], "samples": []},
        "tool_calls": {"median": 0, "p95": 0, "iqr": [0, 0], "samples": []},
        "latency_ms": {"median": 100, "p95": 100, "iqr": [100, 100], "samples": []},
    }
    return {
        "schema_version": 1,
        "generated_at": "2026-05-08T00:00:00Z",
        "git": {"commit": None, "branch": None, "merge_base": None},
        "config_hash": "sha256:a",
        "pricing_hash": "sha256:b",
        "totals": block,
        "scenarios": [
            {
                "name": "s",
                "runs": 1,
                "errors": 0,
                "metrics": block,
                "calls_breakdown": [],
                "error_messages": [],
            }
        ],
        "warnings": [],
    }


def _setup(tmp_path: Path, base_cost: float, head_cost: float) -> tuple[Path, Path, Path]:
    cfg = tmp_path / "costdiff.yaml"
    cfg.write_text(CFG)
    base = tmp_path / "base.json"
    head = tmp_path / "head.json"
    base.write_text(json.dumps(_report(base_cost)))
    head.write_text(json.dumps(_report(head_cost)))
    return cfg, base, head


def test_compare_pass_exit_0(tmp_path: Path):
    cfg, base, head = _setup(tmp_path, 0.10, 0.105)
    r = CliRunner().invoke(main, ["compare", str(base), str(head), "--config", str(cfg)])
    assert r.exit_code == 0


def test_compare_fail_exit_1(tmp_path: Path):
    cfg, base, head = _setup(tmp_path, 0.10, 0.20)
    r = CliRunner().invoke(main, ["compare", str(base), str(head), "--config", str(cfg)])
    assert r.exit_code == 1


def test_compare_missing_input_exit_2(tmp_path: Path):
    cfg = tmp_path / "costdiff.yaml"
    cfg.write_text(CFG)
    r = CliRunner().invoke(
        main, ["compare", str(tmp_path / "nope.json"), str(tmp_path / "nope2.json"), "--config", str(cfg)]
    )
    assert r.exit_code == 2


def test_compare_markdown_format(tmp_path: Path):
    cfg, base, head = _setup(tmp_path, 0.10, 0.20)
    r = CliRunner().invoke(
        main, ["compare", str(base), str(head), "--config", str(cfg), "--format", "markdown"]
    )
    assert r.exit_code == 1
    assert "<!-- costdiff:comment -->" in r.output
    assert "🔴" in r.output


def test_compare_json_format(tmp_path: Path):
    cfg, base, head = _setup(tmp_path, 0.10, 0.11)
    r = CliRunner().invoke(
        main, ["compare", str(base), str(head), "--config", str(cfg), "--format", "json"]
    )
    assert r.exit_code == 0
    parsed = json.loads(r.output)
    assert "totals" in parsed and "failed" in parsed
