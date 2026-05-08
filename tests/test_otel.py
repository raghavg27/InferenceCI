from __future__ import annotations

from opentelemetry import trace

from inferenceci.extractor import extract_calls
from inferenceci.otel_setup import capture, install_instrumentation
from inferenceci.pricing import load_pricing_table


def _emit_openai_span(tracer, model="gpt-4o", in_t=800, out_t=200, cached=0):
    with tracer.start_as_current_span(f"chat {model}") as sp:
        sp.set_attribute("gen_ai.system", "openai")
        sp.set_attribute("gen_ai.request.model", model)
        sp.set_attribute("gen_ai.response.model", model)
        sp.set_attribute("gen_ai.usage.input_tokens", in_t)
        sp.set_attribute("gen_ai.usage.output_tokens", out_t)
        if cached:
            sp.set_attribute("gen_ai.usage.cached_input_tokens", cached)


def _emit_anthropic_span(tracer, model="claude-opus-4-7", in_t=1000, out_t=500, cr=0, cw=0):
    with tracer.start_as_current_span(f"chat {model}") as sp:
        sp.set_attribute("gen_ai.system", "anthropic")
        sp.set_attribute("gen_ai.request.model", model)
        sp.set_attribute("gen_ai.response.model", model)
        sp.set_attribute("gen_ai.usage.input_tokens", in_t)
        sp.set_attribute("gen_ai.usage.output_tokens", out_t)
        if cr:
            sp.set_attribute("gen_ai.usage.cache_read_input_tokens", cr)
        if cw:
            sp.set_attribute("gen_ai.usage.cache_creation_input_tokens", cw)


def test_capture_yields_emitted_spans():
    install_instrumentation()
    tracer = trace.get_tracer("test")
    with capture() as exp:
        _emit_openai_span(tracer)
    spans = exp.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].attributes["gen_ai.system"] == "openai"


def test_capture_isolation_between_runs():
    install_instrumentation()
    tracer = trace.get_tracer("test")
    with capture():
        _emit_openai_span(tracer, model="gpt-4o")
    with capture() as exp2:
        _emit_anthropic_span(tracer)
    # Second capture only sees its own span; clear() ran at entry.
    spans = exp2.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].attributes["gen_ai.system"] == "anthropic"


def test_extract_openai_with_pricing():
    install_instrumentation()
    tracer = trace.get_tracer("test")
    pricing, _ = load_pricing_table(None)
    with capture() as exp:
        _emit_openai_span(tracer, in_t=1_000_000, out_t=500_000)
    calls, missing = extract_calls(exp.get_finished_spans(), pricing)
    assert len(calls) == 1
    c = calls[0]
    assert c.provider == "openai"
    assert c.model == "gpt-4o"
    assert c.input_tokens == 1_000_000 and c.output_tokens == 500_000
    assert c.cost_usd is not None and abs(c.cost_usd - 7.50) < 1e-9
    assert missing == set()


def test_extract_anthropic_cache_tokens():
    install_instrumentation()
    tracer = trace.get_tracer("test")
    pricing, _ = load_pricing_table(None)
    with capture() as exp:
        _emit_anthropic_span(
            tracer,
            model="claude-opus-4-7",
            in_t=1_000_000,
            out_t=200_000,
            cr=300_000,
            cw=200_000,
        )
    calls, missing = extract_calls(exp.get_finished_spans(), pricing)
    assert len(calls) == 1
    c = calls[0]
    assert c.cache_read_tokens == 300_000
    assert c.cache_write_tokens == 200_000
    # 500k*15 + 300k*1.5 + 200k*18.75 + 200k*75 all per 1M = 26.70
    assert c.cost_usd is not None and abs(c.cost_usd - 26.70) < 1e-9
    assert missing == set()


def test_extract_unknown_model_records_missing():
    install_instrumentation()
    tracer = trace.get_tracer("test")
    pricing, _ = load_pricing_table(None)
    with capture() as exp:
        _emit_openai_span(tracer, model="foo-bar")
    calls, missing = extract_calls(exp.get_finished_spans(), pricing)
    assert len(calls) == 1
    assert calls[0].cost_usd is None
    assert "foo-bar" in missing


def test_non_genai_span_ignored():
    install_instrumentation()
    tracer = trace.get_tracer("test")
    with capture() as exp, tracer.start_as_current_span("unrelated"):
        pass
    calls, _ = extract_calls(exp.get_finished_spans(), None)
    assert calls == []


def test_tool_call_count_from_finish_reasons():
    install_instrumentation()
    tracer = trace.get_tracer("test")
    with capture() as exp, tracer.start_as_current_span("chat tool") as sp:
        sp.set_attribute("gen_ai.system", "openai")
        sp.set_attribute("gen_ai.request.model", "gpt-4o")
        sp.set_attribute("gen_ai.usage.input_tokens", 100)
        sp.set_attribute("gen_ai.usage.output_tokens", 50)
        sp.set_attribute("gen_ai.response.finish_reasons", ["tool_calls", "tool_calls"])
    calls, _ = extract_calls(exp.get_finished_spans(), None)
    assert calls[0].tool_calls == 2
