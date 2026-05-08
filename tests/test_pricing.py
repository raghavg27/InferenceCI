from __future__ import annotations

import math
from pathlib import Path

import pytest

from inferenceci.config_loader import ConfigError
from inferenceci.pricing import (
    CallTokens,
    bundled_pricing_path,
    compute_cost,
    load_pricing_table,
)
from inferenceci.schemas import ModelPricing


def test_bundled_pricing_loads():
    table, path = load_pricing_table(None)
    assert path == bundled_pricing_path()
    assert "openai" in table.providers and "anthropic" in table.providers
    assert "gpt-4o" in table.providers["openai"]
    assert "claude-opus-4-7" in table.providers["anthropic"]


def test_lookup_qualified_and_unqualified():
    table, _ = load_pricing_table(None)
    assert table.lookup("openai", "gpt-4o") is not None
    assert table.lookup(None, "gpt-4o") is not None
    assert table.lookup("anthropic", "gpt-4o") is None


def test_compute_cost_openai_basic():
    p = ModelPricing(input_per_1m=2.50, output_per_1m=10.00, cached_input_per_1m=1.25)
    t = CallTokens(input_tokens=1_000_000, output_tokens=500_000)
    cost = compute_cost(p, t)
    # 1M*2.50 + 0.5M*10 = 2.50 + 5.00 = 7.50
    assert math.isclose(cost, 7.50)


def test_compute_cost_with_cached_input():
    p = ModelPricing(input_per_1m=2.50, output_per_1m=10.00, cached_input_per_1m=1.25)
    t = CallTokens(input_tokens=1_000_000, output_tokens=0, cached_input_tokens=400_000)
    # 600k*2.50 + 400k*1.25 = 1.50 + 0.50 = 2.00
    cost = compute_cost(p, t)
    assert math.isclose(cost, 2.00)


def test_compute_cost_anthropic_cache_read_write():
    p = ModelPricing(
        input_per_1m=15.00,
        output_per_1m=75.00,
        cache_write_per_1m=18.75,
        cache_read_per_1m=1.50,
    )
    t = CallTokens(
        input_tokens=1_000_000,
        output_tokens=200_000,
        cache_read_tokens=300_000,
        cache_write_tokens=200_000,
    )
    # regular = 500k -> 500k*15 = 7.50
    # cache_read = 300k*1.50 = 0.45
    # cache_write = 200k*18.75 = 3.75
    # output = 200k*75 = 15.00
    # total 7.50 + 0.45 + 3.75 + 15.00 = 26.70
    cost = compute_cost(p, t)
    assert math.isclose(cost, 26.70)


def test_compute_cost_missing_pricing():
    assert compute_cost(None, CallTokens(input_tokens=100, output_tokens=100)) is None


def test_compute_cost_fallback_to_input_rate():
    """Models with no cached rate set should fall back to input_per_1m."""
    p = ModelPricing(input_per_1m=2.50, output_per_1m=10.00)
    t = CallTokens(input_tokens=1_000_000, output_tokens=0, cached_input_tokens=400_000)
    cost = compute_cost(p, t)
    # 600k*2.50 + 400k*2.50 = 2.50  (full input rate)
    assert math.isclose(cost, 2.50)


def test_compute_cost_no_negative_regular():
    """Cached tokens > input_tokens (malformed span) doesn't yield negative cost."""
    p = ModelPricing(input_per_1m=2.50, output_per_1m=10.00, cached_input_per_1m=1.25)
    t = CallTokens(input_tokens=1000, output_tokens=0, cached_input_tokens=2000)
    cost = compute_cost(p, t)
    # regular clamped to 0; only cached counted: 2000 * 1.25 / 1e6
    assert math.isclose(cost, 2000 * 1.25 / 1_000_000)


def test_pricing_invalid_yaml_raises_configerror(tmp_path: Path):
    p = tmp_path / "bad.yaml"
    p.write_text("version: 2\nlast_updated: 2026-01-01\nproviders: {}\n")
    with pytest.raises(ConfigError):
        load_pricing_table(p)


def test_pricing_unknown_field_rejected(tmp_path: Path):
    p = tmp_path / "bad.yaml"
    p.write_text(
        "version: 1\nlast_updated: 2026-01-01\n"
        "providers:\n  openai:\n    gpt-4o:\n"
        "      input_per_1m: 1\n      output_per_1m: 2\n      typo: 3\n"
    )
    with pytest.raises(ConfigError) as e:
        load_pricing_table(p)
    assert "typo" in str(e.value)
