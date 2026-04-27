"""OpenTelemetry GenAI tracing for the brain sidecar.

Every chat turn is a run, and every run wants a hierarchical span
trace: run → orchestration nodes → LLM calls → tool invocations.
This module ships the SDK init, the tracer accessor, and a few
semconv-aware helpers the orchestration code wraps its hot spots in.

The exporter is opt-in via env var:

* ``THALYN_OTEL_OTLP_ENDPOINT`` — base URL of an OTLP/HTTP receiver
  (Langfuse, Tempo, Honeycomb, …). When set, spans are POSTed there
  via ``opentelemetry-exporter-otlp-proto-http``.
* Unset — spans are still recorded inside the SDK but no exporter is
  attached; nothing leaves the machine. This is the default and
  satisfies the F10.3 / NFR4 "zero external telemetry" promise.

Tests attach an in-memory exporter via :func:`add_span_processor`,
which adds onto the live provider rather than swapping it (OTel's
:func:`set_tracer_provider` is once-only).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, Status, StatusCode

from thalyn_brain import __version__

# Semantic-convention attribute names. Following the
# `gen_ai.*` conventions stable in opentelemetry-semantic-conventions
# 0.62 (released 2026-Q1). Inlined here so the orchestration code
# doesn't have to depend on the upstream constants directly.
GEN_AI_SYSTEM = "gen_ai.system"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
GEN_AI_RESPONSE_FINISH_REASON = "gen_ai.response.finish_reasons"
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_TOOL_NAME = "gen_ai.tool.name"

# Thalyn-specific attribute names. Kept under a `thalyn.` namespace
# so they don't collide with upstream conventions.
THALYN_RUN_ID = "thalyn.run.id"
THALYN_PARENT_RUN_ID = "thalyn.run.parent_id"
THALYN_RUN_STATUS = "thalyn.run.status"
THALYN_NODE_NAME = "thalyn.node.name"


_SERVICE_NAME = "thalyn-brain"
_INSTRUMENTATION_NAME = "thalyn.brain"


def init_tracer(
    *,
    span_processors: list[SpanProcessor] | None = None,
    otlp_endpoint: str | None = None,
) -> TracerProvider:
    """Set up the global tracer provider once, then add processors.

    OTel's ``set_tracer_provider`` is once-only — subsequent calls
    are silently dropped. So the first invocation creates a
    ``TracerProvider`` and registers it; later calls (and the test
    fixture) add their processors to the live provider via
    :func:`add_span_processor`.

    ``otlp_endpoint`` overrides the env var; ``span_processors`` is
    appended on top of the default OTLP-or-no-op selection.
    """
    current = trace.get_tracer_provider()
    if isinstance(current, TracerProvider):
        # Already initialised — augment with any passed-in processors.
        for processor in span_processors or []:
            current.add_span_processor(processor)
        return current

    resource = Resource.create(
        {
            "service.name": _SERVICE_NAME,
            "service.version": __version__,
        }
    )
    provider = TracerProvider(resource=resource)
    for processor in _default_processors(otlp_endpoint):
        provider.add_span_processor(processor)
    for processor in span_processors or []:
        provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)
    return provider


def add_span_processor(processor: SpanProcessor) -> None:
    """Attach an additional processor to the live tracer provider.

    Tests use this to capture spans into an in-memory exporter
    without trying to swap the tracer provider out from under the
    rest of the brain.
    """
    provider = init_tracer()
    provider.add_span_processor(processor)


def get_tracer() -> trace.Tracer:
    """Return a tracer for the brain sidecar's instrumentation."""
    init_tracer()
    return trace.get_tracer(_INSTRUMENTATION_NAME, __version__)


def _default_processors(override: str | None) -> list[SpanProcessor]:
    endpoint = override or os.environ.get("THALYN_OTEL_OTLP_ENDPOINT")
    if not endpoint:
        # No exporter — spans still record inside the SDK but go
        # nowhere on the wire. Satisfies F10.3 / NFR4 by default.
        return []
    exporter = OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces")
    return [BatchSpanProcessor(exporter)]


@contextmanager
def run_span(
    *,
    run_id: str,
    parent_run_id: str | None,
    provider_id: str,
    session_id: str,
) -> Iterator[Span]:
    """Span the orchestration runner wraps each top-level run in.

    Children of this span (planner LLM call, sub-agent runs, tool
    invocations, etc.) inherit the run-id attribute via the active
    context.
    """
    tracer = get_tracer()
    attributes: dict[str, Any] = {
        THALYN_RUN_ID: run_id,
        GEN_AI_OPERATION_NAME: "agent",
        GEN_AI_SYSTEM: "thalyn",
        "thalyn.session.id": session_id,
        "thalyn.provider.id": provider_id,
    }
    if parent_run_id is not None:
        attributes[THALYN_PARENT_RUN_ID] = parent_run_id
    with tracer.start_as_current_span(name="agent.run", attributes=attributes) as span:
        try:
            yield span
        except Exception as exc:
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            span.record_exception(exc)
            raise


@contextmanager
def llm_call_span(
    *,
    provider_id: str,
    model: str,
    operation: str = "chat",
) -> Iterator[Span]:
    """Span around one provider call (e.g., the Anthropic SDK invocation)."""
    tracer = get_tracer()
    attributes = {
        GEN_AI_SYSTEM: provider_id,
        GEN_AI_REQUEST_MODEL: model,
        GEN_AI_OPERATION_NAME: operation,
    }
    with tracer.start_as_current_span(
        name=f"{provider_id}.{operation}", attributes=attributes
    ) as span:
        try:
            yield span
        except Exception as exc:
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            span.record_exception(exc)
            raise


def annotate_llm_response(
    span: Span,
    *,
    finish_reason: str | None = None,
    response_model: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> None:
    """Apply standard response-side semconv attributes to an llm span."""
    if finish_reason is not None:
        span.set_attribute(GEN_AI_RESPONSE_FINISH_REASON, [finish_reason])
    if response_model is not None:
        span.set_attribute(GEN_AI_RESPONSE_MODEL, response_model)
    if input_tokens is not None:
        span.set_attribute(GEN_AI_USAGE_INPUT_TOKENS, input_tokens)
    if output_tokens is not None:
        span.set_attribute(GEN_AI_USAGE_OUTPUT_TOKENS, output_tokens)


@contextmanager
def tool_call_span(*, tool_name: str) -> Iterator[Span]:
    """Span around one agent tool invocation (browser_navigate, etc.)."""
    tracer = get_tracer()
    with tracer.start_as_current_span(
        name=f"tool.{tool_name}",
        attributes={
            GEN_AI_TOOL_NAME: tool_name,
            GEN_AI_OPERATION_NAME: "execute_tool",
        },
    ) as span:
        try:
            yield span
        except Exception as exc:
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            span.record_exception(exc)
            raise


@contextmanager
def node_span(*, node: str) -> Iterator[Span]:
    """Span around one orchestration-graph node transition (plan, execute, …)."""
    tracer = get_tracer()
    with tracer.start_as_current_span(
        name=f"node.{node}",
        attributes={THALYN_NODE_NAME: node},
    ) as span:
        try:
            yield span
        except Exception as exc:
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            span.record_exception(exc)
            raise
