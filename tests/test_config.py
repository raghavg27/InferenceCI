from __future__ import annotations

from pathlib import Path

import pytest

from inferenceci.config_loader import ConfigError, load_config


def write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(body)
    return p


VALID = """\
version: 1
runs_per_scenario: 3
providers:
  openai:
    api_key_env: OPENAI_API_KEY
thresholds:
  cost_increase_pct: 15
  scenario_cost_increase_pct: 25
  ignore_below_usd: 0.001
scenarios:
  - name: s1
    entrypoint: scenarios/x.py:run
    input:
      q: hi
    timeout_seconds: 30
"""


def test_valid(tmp_path):
    p = write(tmp_path, "ok.yaml", VALID)
    cfg = load_config(p)
    assert cfg.version == 1
    assert cfg.runs_per_scenario == 3
    assert cfg.redact_prompts is True
    assert cfg.scenarios[0].parsed_entrypoint() == (Path("scenarios/x.py"), "run")


def test_unknown_top_key_rejected(tmp_path):
    body = VALID + "extra_key: 1\n"
    p = write(tmp_path, "bad.yaml", body)
    with pytest.raises(ConfigError) as e:
        load_config(p)
    assert "extra_key" in str(e.value)


def test_unknown_scenario_key_rejected(tmp_path):
    body = VALID.replace("timeout_seconds: 30", "timeout_seconds: 30\n    typo: 1")
    p = write(tmp_path, "bad.yaml", body)
    with pytest.raises(ConfigError) as e:
        load_config(p)
    assert "typo" in str(e.value)


def test_runs_per_scenario_bounds(tmp_path):
    body = VALID.replace("runs_per_scenario: 3", "runs_per_scenario: 11")
    p = write(tmp_path, "bad.yaml", body)
    with pytest.raises(ConfigError):
        load_config(p)


def test_bad_entrypoint_format(tmp_path):
    body = VALID.replace("scenarios/x.py:run", "not_a_path")
    p = write(tmp_path, "bad.yaml", body)
    with pytest.raises(ConfigError) as e:
        load_config(p)
    assert "entrypoint" in str(e.value)


def test_input_xor_input_file(tmp_path):
    body = VALID.replace(
        "    input:\n      q: hi",
        "    input:\n      q: hi\n    input_file: foo.json",
    )
    p = write(tmp_path, "bad.yaml", body)
    with pytest.raises(ConfigError):
        load_config(p)


def test_missing_input_and_input_file(tmp_path):
    body = VALID.replace("    input:\n      q: hi\n    timeout_seconds: 30", "    timeout_seconds: 30")
    p = write(tmp_path, "bad.yaml", body)
    with pytest.raises(ConfigError):
        load_config(p)


def test_duplicate_scenario_names(tmp_path):
    body = VALID + """\
  - name: s1
    entrypoint: scenarios/y.py:run
    input: {q: 2}
"""
    p = write(tmp_path, "bad.yaml", body)
    with pytest.raises(ConfigError) as e:
        load_config(p)
    assert "duplicate" in str(e.value)


def test_yaml_parse_error_includes_line(tmp_path):
    p = write(tmp_path, "bad.yaml", "version: 1\n  bad: : :\n")
    with pytest.raises(ConfigError) as e:
        load_config(p)
    assert "line" in str(e.value)


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError) as e:
        load_config(tmp_path / "nope.yaml")
    assert "not found" in str(e.value)
