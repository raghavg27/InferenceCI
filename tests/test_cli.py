from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from inferenceci.cli import main


def test_version():
    r = CliRunner().invoke(main, ["version"])
    assert r.exit_code == 0
    assert r.output.strip()


def test_init_creates_files(tmp_path: Path):
    runner = CliRunner()
    r = runner.invoke(main, ["init", "--dir", str(tmp_path)])
    assert r.exit_code == 0, r.output
    assert (tmp_path / "costdiff.yaml").exists()
    assert (tmp_path / "scenarios" / "example_openai.py").exists()


def test_init_refuses_overwrite(tmp_path: Path):
    runner = CliRunner()
    r = runner.invoke(main, ["init", "--dir", str(tmp_path)])
    assert r.exit_code == 0
    r2 = runner.invoke(main, ["init", "--dir", str(tmp_path)])
    assert r2.exit_code == 2
    assert "refusing to overwrite" in r2.stderr if r2.stderr_bytes else "refusing" in r2.output


def test_init_force_overwrites(tmp_path: Path):
    runner = CliRunner()
    runner.invoke(main, ["init", "--dir", str(tmp_path)])
    (tmp_path / "costdiff.yaml").write_text("scratched")
    r = runner.invoke(main, ["init", "--dir", str(tmp_path), "--force"])
    assert r.exit_code == 0
    assert "scratched" not in (tmp_path / "costdiff.yaml").read_text()


def test_pricing_list_runs():
    r = CliRunner().invoke(main, ["pricing", "list"])
    assert r.exit_code == 0
    out = r.output
    assert "openai" in out and "anthropic" in out
    # model names may be ellipsis-truncated by rich at narrow widths
    assert "gpt-4" in out and "claude-" in out


def test_run_writes_report_json(tmp_path: Path):
    sdir = tmp_path / "scenarios"
    sdir.mkdir()
    (sdir / "stub.py").write_text(
        "from opentelemetry import trace\n"
        "def run(input):\n"
        "    t = trace.get_tracer('x')\n"
        "    with t.start_as_current_span('chat gpt-4o') as sp:\n"
        "        sp.set_attribute('gen_ai.system','openai')\n"
        "        sp.set_attribute('gen_ai.request.model','gpt-4o')\n"
        "        sp.set_attribute('gen_ai.usage.input_tokens', 1000)\n"
        "        sp.set_attribute('gen_ai.usage.output_tokens', 500)\n"
        "    return {}\n"
    )
    cfg = tmp_path / "costdiff.yaml"
    cfg.write_text(
        """version: 1
runs_per_scenario: 2
providers:
  openai: {api_key_env: OPENAI_API_KEY}
thresholds: {cost_increase_pct: 15, scenario_cost_increase_pct: 25, ignore_below_usd: 0.001}
scenarios:
  - name: s
    entrypoint: scenarios/stub.py:run
    input: {}
    timeout_seconds: 5
"""
    )
    out = tmp_path / "report.json"
    runner = CliRunner()
    r = runner.invoke(
        main,
        ["run", "--config", str(cfg), "--output", str(out)],
    )
    assert r.exit_code == 0, r.output
    assert out.exists()
    data = json.loads(out.read_text())
    assert data["scenarios"][0]["name"] == "s"


def test_run_bad_config_exits_2(tmp_path: Path):
    cfg = tmp_path / "bad.yaml"
    cfg.write_text("version: 1\n")  # missing required fields
    r = CliRunner().invoke(main, ["run", "--config", str(cfg)])
    assert r.exit_code == 2
