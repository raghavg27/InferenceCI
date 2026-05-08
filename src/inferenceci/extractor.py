"""Pull GenAI call metrics out of OTel spans.

Follows OpenTelemetry GenAI semantic conventions
(opentelemetry-semantic-conventions ~= 0.62b1, 2026). Attribute names:
  - `gen_ai.system`              -> provider ("openai" / "anthropic")
  - `gen_ai.request.model`       -> requested model
  - `gen_ai.response.model`      -> actual model used (preferred when present)
  - `gen_ai.usage.input_tokens`
  - `gen_ai.usage.output_tokens`

Cache-token attributes vary by provider/version; we accept several aliases:
  - cached input (OpenAI prompt cache hit):
      `gen_ai.usage.cached_input_tokens`,
      `gen_ai.openai.usage.prompt_tokens_cached`,
      `gen_ai.usage.cache_read_input_tokens`     (Anthropic alt name)
  - cache write (Anthropic cache creation):
      `gen_ai.usage.cache_creation_input_tokens`
"""

from __future__ import annotations

from dataclasses import dataclass

from opentelemetry.sdk.trace import ReadableSpan

from inferenceci.pricing import CallTokens, compute_cost
from inferenceci.schemas import PricingTable

_INPUT_TOKEN_KEYS = ("gen_ai.usage.input_tokens", "gen_ai.usage.prompt_tokens")
_OUTPUT_TOKEN_KEYS = ("gen_ai.usage.output_tokens", "gen_ai.usage.completion_tokens")
_CACHED_INPUT_KEYS = (
    "gen_ai.usage.cached_input_tokens",
    "gen_ai.openai.usage.prompt_tokens_cached",
)
_CACHE_READ_KEYS = (
    "gen_ai.usage.cache_read_input_tokens",
    "gen_ai.anthropic.usage.cache_read_input_tokens",
)
_CACHE_WRITE_KEYS = (
    "gen_ai.usage.cache_creation_input_tokens",
    "gen_ai.anthropic.usage.cache_creation_input_tokens",
)
_MODEL_KEYS = ("gen_ai.response.model", "gen_ai.request.model")
_PROVIDER_KEYS = ("gen_ai.system", "gen_ai.provider.name")
_FINISH_KEYS = ("gen_ai.response.finish_reasons",)


@dataclass(frozen=True)
class CallMetrics:
    provider: str | None
    model: str
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    tool_calls: int
    cost_usd: float | None
    latency_ms: float


def _first(attrs: dict, keys) -> object | None:
    for k in keys:
        if k in attrs and attrs[k] is not None:
            return attrs[k]
    return None


def _as_int(v: object | None) -> int:
    if v is None:
        return 0
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _is_genai_span(span: ReadableSpan) -> bool:
    attrs = span.attributes or {}
    if any(k in attrs for k in _PROVIDER_KEYS):
        return True
    return any(k in attrs for k in _MODEL_KEYS)


def _count_tool_calls(span: ReadableSpan) -> int:
    """Best-effort tool-call count.

    Strategies (in order):
      1. `gen_ai.response.finish_reasons` contains 'tool_calls'.
      2. Sum `gen_ai.tool.calls.count` over span events.
      3. Count events whose name starts with `gen_ai.tool` or contains 'tool'.
    """
    attrs = span.attributes or {}
    finish = attrs.get(_FINISH_KEYS[0])
    if isinstance(finish, (list, tuple)) and any(str(r) == "tool_calls" for r in finish):
        tc = sum(1 for r in finish if str(r) == "tool_calls")
        if tc > 0:
            return tc

    total = 0
    for ev in getattr(span, "events", []) or []:
        ev_attrs = dict(ev.attributes or {})
        cnt = ev_attrs.get("gen_ai.tool.calls.count")
        if isinstance(cnt, int) and cnt > 0:
            total += cnt
            continue
        name = (ev.name or "").lower()
        if name.startswith("gen_ai.tool") or "tool_call" in name:
            total += 1
    return total


def extract_calls(
    spans: list[ReadableSpan], pricing: PricingTable | None
) -> tuple[list[CallMetrics], set[str]]:
    """Convert spans -> CallMetrics list. Returns (calls, missing_pricing_models)."""
    calls: list[CallMetrics] = []
    missing: set[str] = set()
    for sp in spans:
        if not _is_genai_span(sp):
            continue
        attrs = dict(sp.attributes or {})
        provider = _first(attrs, _PROVIDER_KEYS)
        model = _first(attrs, _MODEL_KEYS)
        if not model:
            continue
        provider_s = str(provider) if provider else None
        model_s = str(model)

        in_t = _as_int(_first(attrs, _INPUT_TOKEN_KEYS))
        out_t = _as_int(_first(attrs, _OUTPUT_TOKEN_KEYS))
        cached_in = _as_int(_first(attrs, _CACHED_INPUT_KEYS))
        cache_r = _as_int(_first(attrs, _CACHE_READ_KEYS))
        cache_w = _as_int(_first(attrs, _CACHE_WRITE_KEYS))

        tokens = CallTokens(
            input_tokens=in_t,
            output_tokens=out_t,
            cached_input_tokens=cached_in,
            cache_read_tokens=cache_r,
            cache_write_tokens=cache_w,
        )

        rate = pricing.lookup(provider_s, model_s) if pricing else None
        if rate is None:
            missing.add(model_s)
        cost = compute_cost(rate, tokens)

        latency_ns = (sp.end_time or 0) - (sp.start_time or 0)
        latency_ms = latency_ns / 1_000_000.0 if latency_ns > 0 else 0.0

        calls.append(
            CallMetrics(
                provider=provider_s,
                model=model_s,
                input_tokens=in_t,
                output_tokens=out_t,
                cached_input_tokens=cached_in,
                cache_read_tokens=cache_r,
                cache_write_tokens=cache_w,
                tool_calls=_count_tool_calls(sp),
                cost_usd=cost,
                latency_ms=latency_ms,
            )
        )
    return calls, missing
