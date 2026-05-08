"""In-process OTel SDK + GenAI auto-instrumentation, with an in-memory exporter.

Installation is idempotent. The caller uses `capture()` to bracket a scenario
run and read just the spans produced inside it.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

log = logging.getLogger(__name__)

_lock = threading.Lock()
_installed = False
_exporter: InMemorySpanExporter | None = None


def install_instrumentation() -> InMemorySpanExporter:
    """Install TracerProvider + GenAI instrumentors once. Returns the exporter."""
    global _installed, _exporter
    with _lock:
        if _installed:
            assert _exporter is not None
            return _exporter
        provider = TracerProvider()
        exporter = InMemorySpanExporter()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        _exporter = exporter

        try:
            from opentelemetry.instrumentation.openai_v2 import OpenAIInstrumentor

            OpenAIInstrumentor().instrument(tracer_provider=provider)
        except Exception as e:
            log.warning("OpenAI instrumentation unavailable: %s", e)

        try:
            from opentelemetry.instrumentation.anthropic import AnthropicInstrumentor

            AnthropicInstrumentor().instrument(tracer_provider=provider)
        except Exception as e:
            log.warning("Anthropic instrumentation unavailable: %s", e)

        _installed = True
        return exporter


def get_exporter() -> InMemorySpanExporter:
    return install_instrumentation()


@contextmanager
def capture() -> Iterator[InMemorySpanExporter]:
    """Clear in-memory spans, yield exporter; spans accumulate during the
    context. Caller reads `exporter.get_finished_spans()` after exit."""
    exp = install_instrumentation()
    exp.clear()
    try:
        yield exp
    finally:
        # Force-flush so SimpleSpanProcessor commits anything pending.
        provider = trace.get_tracer_provider()
        flush = getattr(provider, "force_flush", None)
        if callable(flush):
            with contextlib.suppress(Exception):
                flush()
