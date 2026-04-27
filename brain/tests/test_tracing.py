"""OTel GenAI tracing scaffolding."""

from __future__ import annotations

from typing import Any

import pytest
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from thalyn_brain.tracing import (
    GEN_AI_OPERATION_NAME,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_RESPONSE_MODEL,
    GEN_AI_SYSTEM,
    GEN_AI_TOOL_NAME,
    THALYN_NODE_NAME,
    THALYN_RUN_ID,
    add_span_processor,
    annotate_llm_response,
    init_tracer,
    llm_call_span,
    node_span,
    run_span,
    tool_call_span,
)


@pytest.fixture
def captured_spans() -> Any:
    """Attach an in-memory exporter to the live tracer provider.

    Cleans up after itself by clearing the exporter's accumulated
    spans (the processor stays attached — OTel's tracer provider is
    once-only, so we share it across tests rather than tearing it
    down each time).
    """
    exporter = InMemorySpanExporter()
    add_span_processor(SimpleSpanProcessor(exporter))
    exporter.clear()
    yield exporter
    exporter.clear()


def _by_name(spans: tuple[ReadableSpan, ...], name: str) -> ReadableSpan:
    for span in spans:
        if span.name == name:
            return span
    raise AssertionError(f"no span named {name!r} in {[s.name for s in spans]}")


def test_run_span_records_attributes(captured_spans: InMemorySpanExporter) -> None:
    with run_span(
        run_id="r_test",
        parent_run_id=None,
        provider_id="anthropic",
        session_id="sess_1",
    ):
        pass
    span = _by_name(captured_spans.get_finished_spans(), "agent.run")
    attrs = dict(span.attributes or {})
    assert attrs[THALYN_RUN_ID] == "r_test"
    assert attrs[GEN_AI_OPERATION_NAME] == "agent"
    assert "thalyn.run.parent_id" not in attrs


def test_run_span_carries_parent_id(captured_spans: InMemorySpanExporter) -> None:
    with run_span(
        run_id="r_child",
        parent_run_id="r_parent",
        provider_id="anthropic",
        session_id="sess_1",
    ):
        pass
    span = _by_name(captured_spans.get_finished_spans(), "agent.run")
    attrs = dict(span.attributes or {})
    assert attrs["thalyn.run.parent_id"] == "r_parent"


def test_llm_call_span_carries_genai_semconv(captured_spans: InMemorySpanExporter) -> None:
    with llm_call_span(provider_id="anthropic", model="claude-sonnet-4-6") as live_span:
        annotate_llm_response(
            live_span,
            finish_reason="end_turn",
            response_model="claude-sonnet-4-6",
            input_tokens=42,
            output_tokens=128,
        )
    finished = _by_name(captured_spans.get_finished_spans(), "anthropic.chat")
    attrs = dict(finished.attributes or {})
    assert attrs[GEN_AI_SYSTEM] == "anthropic"
    assert attrs[GEN_AI_REQUEST_MODEL] == "claude-sonnet-4-6"
    assert attrs[GEN_AI_RESPONSE_MODEL] == "claude-sonnet-4-6"
    assert attrs["gen_ai.usage.input_tokens"] == 42
    assert attrs["gen_ai.usage.output_tokens"] == 128
    finish_reasons = attrs["gen_ai.response.finish_reasons"]
    assert isinstance(finish_reasons, tuple | list)
    assert list(finish_reasons) == ["end_turn"]


def test_llm_call_span_records_exceptions(captured_spans: InMemorySpanExporter) -> None:
    with pytest.raises(RuntimeError):
        with llm_call_span(provider_id="anthropic", model="claude-sonnet-4-6"):
            raise RuntimeError("upstream blew up")
    span = _by_name(captured_spans.get_finished_spans(), "anthropic.chat")
    assert span.status.status_code.name == "ERROR"
    event_names = [event.name for event in span.events]
    assert "exception" in event_names


def test_tool_call_span_uses_tool_name(captured_spans: InMemorySpanExporter) -> None:
    with tool_call_span(tool_name="browser_navigate"):
        pass
    span = _by_name(captured_spans.get_finished_spans(), "tool.browser_navigate")
    attrs = dict(span.attributes or {})
    assert attrs[GEN_AI_TOOL_NAME] == "browser_navigate"
    assert attrs[GEN_AI_OPERATION_NAME] == "execute_tool"


def test_node_span_uses_node_name(captured_spans: InMemorySpanExporter) -> None:
    with node_span(node="planner"):
        pass
    span = _by_name(captured_spans.get_finished_spans(), "node.planner")
    attrs = dict(span.attributes or {})
    assert attrs[THALYN_NODE_NAME] == "planner"


def test_default_init_emits_no_otlp_processor(monkeypatch: Any) -> None:
    """With no THALYN_OTEL_OTLP_ENDPOINT the default tracer wires no
    exporters — spans record but nothing leaves the machine."""
    monkeypatch.delenv("THALYN_OTEL_OTLP_ENDPOINT", raising=False)
    # init_tracer is idempotent: even though other tests may have
    # added processors, this confirms the default selection ignores
    # the env var. We assert via _default_processors directly.
    from thalyn_brain.tracing import _default_processors

    assert _default_processors(None) == []


def test_default_init_with_endpoint_attaches_otlp_processor(monkeypatch: Any) -> None:
    """When the user opts in via THALYN_OTEL_OTLP_ENDPOINT, the
    default selection includes a single OTLP processor."""
    monkeypatch.setenv("THALYN_OTEL_OTLP_ENDPOINT", "http://localhost:4318")
    from thalyn_brain.tracing import _default_processors

    processors = _default_processors(None)
    assert len(processors) == 1


def test_init_tracer_is_idempotent() -> None:
    a = init_tracer()
    b = init_tracer()
    assert a is b
