from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field, model_validator

_STRICT = ConfigDict(extra="forbid", strict=False)


class ModelPricing(BaseModel):
    """Per-model pricing in USD per 1M tokens.

    OpenAI-style models use `cached_input_per_1m`. Anthropic-style models use
    `cache_write_per_1m` + `cache_read_per_1m`. Either flavor (or none) is
    accepted; missing fields fall back to `input_per_1m`.
    """

    model_config = _STRICT

    input_per_1m: float = Field(ge=0)
    output_per_1m: float = Field(ge=0)
    cached_input_per_1m: float | None = Field(default=None, ge=0)
    cache_write_per_1m: float | None = Field(default=None, ge=0)
    cache_read_per_1m: float | None = Field(default=None, ge=0)


class PricingTable(BaseModel):
    model_config = _STRICT

    version: int = Field(ge=1, le=1)
    last_updated: date
    providers: dict[str, dict[str, ModelPricing]]

    @model_validator(mode="after")
    def _nonempty(self) -> PricingTable:
        if not self.providers:
            raise ValueError("pricing.providers cannot be empty")
        for prov, models in self.providers.items():
            if not models:
                raise ValueError(f"pricing.providers.{prov} cannot be empty")
        return self

    def lookup(self, provider: str | None, model: str | None) -> ModelPricing | None:
        if not model:
            return None
        if provider:
            p = self.providers.get(provider)
            return p.get(model) if p else None
        for models in self.providers.values():
            if model in models:
                return models[model]
        return None
