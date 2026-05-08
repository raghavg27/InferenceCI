from __future__ import annotations

import hashlib
from pathlib import Path

import yaml
from pydantic import ValidationError

from inferenceci.schemas import CostDiffConfig, PricingTable


class ConfigError(Exception):
    """User-facing config error. CLI prints `str(e)` and exits 2."""


def _read_yaml(path: Path) -> dict:
    try:
        text = path.read_text()
    except FileNotFoundError as e:
        raise ConfigError(f"{path}: file not found") from e
    except OSError as e:
        raise ConfigError(f"{path}: {e}") from e
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        loc = f"line {mark.line + 1}, col {mark.column + 1}" if mark else "unknown loc"
        raise ConfigError(f"{path}: YAML parse error at {loc}: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top-level YAML must be a mapping")
    return data


def _format_validation_error(path: Path, exc: ValidationError) -> str:
    lines = [f"{path}: invalid config ({exc.error_count()} error(s))"]
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "<root>"
        lines.append(f"  {loc}: {err['msg']}")
    return "\n".join(lines)


def load_config(path: Path) -> CostDiffConfig:
    data = _read_yaml(path)
    try:
        return CostDiffConfig.model_validate(data)
    except ValidationError as e:
        raise ConfigError(_format_validation_error(path, e)) from e


def load_pricing(path: Path) -> PricingTable:
    data = _read_yaml(path)
    try:
        return PricingTable.model_validate(data)
    except ValidationError as e:
        raise ConfigError(_format_validation_error(path, e)) from e


def hash_file(path: Path) -> str:
    h = hashlib.sha256(path.read_bytes()).hexdigest()
    return f"sha256:{h}"


def hash_bytes(b: bytes) -> str:
    return f"sha256:{hashlib.sha256(b).hexdigest()}"
