from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from inferenceci.config_loader import hash_file, load_pricing
from inferenceci.schemas import ModelPricing, PricingTable

_PER_M = 1_000_000.0


def bundled_pricing_path() -> Path:
    return Path(__file__).parent / "data" / "pricing.yaml"


def load_pricing_table(path: Path | None) -> tuple[PricingTable, Path]:
    p = path if path is not None else bundled_pricing_path()
    return load_pricing(p), p


@dataclass(frozen=True)
class CallTokens:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0   # OpenAI prompt-cache read
    cache_read_tokens: int = 0     # Anthropic cache read
    cache_write_tokens: int = 0    # Anthropic cache write/creation

    @property
    def cached_total(self) -> int:
        return self.cached_input_tokens + self.cache_read_tokens + self.cache_write_tokens


def compute_cost(pricing: ModelPricing | None, t: CallTokens) -> float | None:
    """Apply per-call pricing. Return None if no pricing supplied.

    Cost = (input - cached - cache_read - cache_write) * input_rate
         + cached_input * (cached_input_rate or input_rate)
         + cache_read * (cache_read_rate or input_rate)
         + cache_write * (cache_write_rate or input_rate)
         + output * output_rate
    All rates are per 1M tokens.
    """
    if pricing is None:
        return None
    input_rate = pricing.input_per_1m
    output_rate = pricing.output_per_1m
    cached_in_rate = pricing.cached_input_per_1m if pricing.cached_input_per_1m is not None else input_rate
    cache_read_rate = pricing.cache_read_per_1m if pricing.cache_read_per_1m is not None else input_rate
    cache_write_rate = pricing.cache_write_per_1m if pricing.cache_write_per_1m is not None else input_rate

    regular = max(t.input_tokens - t.cached_input_tokens - t.cache_read_tokens - t.cache_write_tokens, 0)
    cost = (
        regular * input_rate
        + t.cached_input_tokens * cached_in_rate
        + t.cache_read_tokens * cache_read_rate
        + t.cache_write_tokens * cache_write_rate
        + t.output_tokens * output_rate
    )
    return cost / _PER_M


def pricing_hash(path: Path) -> str:
    return hash_file(path)
