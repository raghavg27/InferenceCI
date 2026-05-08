from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_STRICT = ConfigDict(extra="forbid", strict=False)

_ENTRYPOINT_RE = re.compile(r"^(?P<path>[^:]+\.py):(?P<func>[A-Za-z_][A-Za-z0-9_]*)$")


class ProviderConfig(BaseModel):
    model_config = _STRICT
    api_key_env: str = Field(min_length=1)


class ThresholdConfig(BaseModel):
    model_config = _STRICT
    cost_increase_pct: float = Field(ge=0)
    scenario_cost_increase_pct: float = Field(ge=0)
    ignore_below_usd: float = Field(ge=0, default=0.0)


class ScenarioConfig(BaseModel):
    model_config = _STRICT

    name: str = Field(min_length=1)
    entrypoint: str
    input: dict[str, Any] | None = None
    input_file: str | None = None
    timeout_seconds: int = Field(default=120, ge=1, le=3600)

    @field_validator("entrypoint")
    @classmethod
    def _validate_entrypoint(cls, v: str) -> str:
        if not _ENTRYPOINT_RE.match(v):
            raise ValueError(
                f"entrypoint must be 'path/to/file.py:function_name', got {v!r}"
            )
        return v

    @model_validator(mode="after")
    def _exactly_one_input(self) -> ScenarioConfig:
        if self.input is None and self.input_file is None:
            raise ValueError("scenario must define `input` or `input_file`")
        if self.input is not None and self.input_file is not None:
            raise ValueError("scenario cannot define both `input` and `input_file`")
        return self

    def parsed_entrypoint(self) -> tuple[Path, str]:
        m = _ENTRYPOINT_RE.match(self.entrypoint)
        assert m, "validated above"
        return Path(m.group("path")), m.group("func")


class CostDiffConfig(BaseModel):
    model_config = _STRICT

    version: int = Field(ge=1, le=1)
    runs_per_scenario: int = Field(ge=1, le=10, default=3)
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    pricing_file: str | None = None
    thresholds: ThresholdConfig
    redact_prompts: bool = True
    scenarios: list[ScenarioConfig] = Field(min_length=1)

    @field_validator("scenarios")
    @classmethod
    def _unique_scenario_names(cls, v: list[ScenarioConfig]) -> list[ScenarioConfig]:
        seen: set[str] = set()
        for s in v:
            if s.name in seen:
                raise ValueError(f"duplicate scenario name: {s.name!r}")
            seen.add(s.name)
        return v
