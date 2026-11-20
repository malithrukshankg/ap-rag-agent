"""Langfuse adapter for the Tracer port. The only module that imports the langfuse SDK.

Spans are plain OpenTelemetry spans from a TracerProvider this adapter owns; the Langfuse
client attaches its exporter to that provider and only spans from this adapter's scope are
exported. Plain OTel (rather than the SDK's start_observation) is used because the adapter
must set span ids, parents and start/end times explicitly:

    trace (id = create_trace_id(seed=run_id))
    └── run            root span, id derived from run_id; input = case summary, output = state
        ├── INTAKE     one span per graph node: node.entered → node.exited, real duration
        │   └── run.started
        ├── RETRIEVE
        │   ├── tool.attempt       leaf: [ts - duration_ms, ts]
        │   └── retrieval.performed
        ├── ASSESS
        │   └── assess.model       generation: model, token usage, parse result
        ├── approval.granted       events outside a node hang off the root
        └── ...

Deterministic ids make the trace span processes: a run that pauses for approval and is
resumed later (even after a restart) lands in the same trace under the same root. The root
is sent at the end of every graph invocation (`run_segment`); Langfuse keeps one row per span
id and the later send wins, so the root's output moves from AWAITING_APPROVAL to COMPLETED.

Data: event outputs are the allow-listed audit payloads. Prompt/completion text, and the
invoice amount in the root input, are sent only with `send_prompts` (§4 safe logging,
FIN-POL-010 §2). Langfuse stores input/output as JSON text; the UI renders it as JSON.
"""

import contextvars
import hashlib
import json
import threading
import time
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

from langfuse import Langfuse
from langfuse import LangfuseOtelSpanAttributes as Attr
from opentelemetry import trace as otel
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter
from opentelemetry.sdk.trace.id_generator import IdGenerator, RandomIdGenerator

from ap_agent.assessment.assess import AttemptRecord
from ap_agent.infra.observability.tracing import RunSegment
from ap_agent.infra.persistence.records import AuditEvent

SCOPE = "ap-rag-agent"
TRACE_NAME = "ap-run"
TAG = "ap-rag-agent"

ObservationType = Literal["span", "tool", "retriever", "generation"]
Level = Literal["DEFAULT", "WARNING", "ERROR"]

# model.call duplicates the generation observation; node enter/exit become the node span.
SKIPPED_EVENTS = {"model.call"}
NODE_ENTERED, NODE_EXITED = "node.entered", "node.exited"

_PINNED: contextvars.ContextVar[tuple[int, int] | None] = contextvars.ContextVar(
    "ap_pinned_ids", default=None
)


class _PinnedIdGenerator(IdGenerator):
    """Random ids, except while the root span is started: then the run's fixed ids."""

    def __init__(self) -> None:
        self._random = RandomIdGenerator()

    def generate_span_id(self) -> int:
        pinned = _PINNED.get()
        return pinned[1] if pinned else self._random.generate_span_id()

    def generate_trace_id(self) -> int:
        pinned = _PINNED.get()
        return pinned[0] if pinned else self._random.generate_trace_id()


def trace_id(run_id: str) -> int:
    return int(Langfuse.create_trace_id(seed=run_id), 16)


def root_span_id(run_id: str) -> int:
    return int(hashlib.sha256(f"{run_id}:run".encode()).hexdigest()[:16], 16) or 1


def _ns(ts: str) -> int:
    return int(datetime.fromisoformat(ts).timestamp() * 1_000_000_000)


def _event_ns(event: AuditEvent) -> int:
    """Event time plus event_id microseconds. Audit timestamps have millisecond precision, so
    events in the same millisecond would tie and Langfuse would show them in random order; the
    offset (tens of microseconds at most) keeps the audit order."""
    return _ns(event.ts) + (event.event_id or 0) * 1_000


def _json(value: Any) -> str:
    return json.dumps(value, default=str, sort_keys=True)


def _observation_type(event_type: str) -> ObservationType:
    if event_type.startswith("tool."):
        return "tool"
    if event_type.startswith("retrieval."):
        return "retriever"
    return "span"


def _level(outcome: str | None) -> Level:
    if outcome in (None, "ok"):
        return "DEFAULT"
    if outcome == "failed":
        return "ERROR"
    return "WARNING"  # retry, timeout, invalid, rejected, ...


class LangfuseTracer:
    def __init__(
        self,
        *,
        public_key: str,
        secret_key: str,
        host: str,
        environment: str,
        release: str | None = None,
        send_prompts: bool = False,
        span_exporter: SpanExporter | None = None,  # tests inject an in-memory exporter
    ) -> None:
        self._provider = TracerProvider(id_generator=_PinnedIdGenerator())
        self._client = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            base_url=host,
            environment=environment,
            release=release,
            tracer_provider=self._provider,
            should_export_span=self._is_ours,
            span_exporter=span_exporter,
        )
        self._tracer = self._provider.get_tracer(SCOPE)
        self._environment, self._release = environment, release
        self._send_prompts = send_prompts
        self._open: dict[str, tuple[str, otel.Span]] = {}  # run_id → (node, open node span)
        self._lock = threading.Lock()

    @staticmethod
    def _is_ours(span: ReadableSpan) -> bool:
        return span.instrumentation_scope is not None and span.instrumentation_scope.name == SCOPE

    # --- span helpers ------------------------------------------------------------------

    def _attrs(self, run_id: str, kind: ObservationType, level: Level,
               metadata: Mapping[str, Any]) -> dict[str, Any]:  # fmt: skip
        attrs: dict[str, Any] = {
            Attr.TRACE_NAME: TRACE_NAME,
            Attr.TRACE_SESSION_ID: run_id,
            Attr.ENVIRONMENT: self._environment,
            Attr.OBSERVATION_TYPE: kind,
            Attr.OBSERVATION_LEVEL: level,
        }
        if self._release:
            attrs[Attr.RELEASE] = self._release
        for key, value in metadata.items():
            if value is not None:
                attrs[f"{Attr.OBSERVATION_METADATA}.{key}"] = (
                    value if isinstance(value, (str, int, float, bool)) else _json(value)
                )
        return attrs

    def _parent(self, run_id: str, node: str | None) -> Any:
        """The open node span for this run when the event belongs to it, else the root."""
        with self._lock:
            current = self._open.get(run_id)
        if current is not None and (node is None or node == current[0]):
            return otel.set_span_in_context(current[1])
        root = otel.SpanContext(
            trace_id=trace_id(run_id),
            span_id=root_span_id(run_id),
            is_remote=True,
            trace_flags=otel.TraceFlags(otel.TraceFlags.SAMPLED),
        )
        return otel.set_span_in_context(otel.NonRecordingSpan(root))

    def _leaf(self, run_id: str, name: str, *, parent_node: str | None, start: int, end: int,
              attrs: dict[str, Any]) -> None:  # fmt: skip
        span = self._tracer.start_span(
            name, context=self._parent(run_id, parent_node), start_time=start, attributes=attrs
        )
        span.end(end_time=max(end, start))

    def _close_open(self, run_id: str, end: int, level: Level | None = None) -> None:
        with self._lock:
            current = self._open.pop(run_id, None)
        if current is not None:
            if level is not None:
                current[1].set_attribute(Attr.OBSERVATION_LEVEL, level)
            current[1].end(end_time=end)

    # --- Tracer port ------------------------------------------------------------------

    def run_segment(self, segment: RunSegment) -> None:
        end = _ns(segment.ended_at) + 1_000_000  # past the last event's ordering offset
        self._close_open(segment.run_id, end)  # a node left open by a failure ends here
        summary: dict[str, Any] = {
            "case_id": segment.case_id,
            "vendor_id": segment.vendor_id,
            "invoice_ref": segment.invoice_ref,
        }
        if self._send_prompts:
            summary |= {"amount": segment.amount, "currency": segment.currency}
        state = {
            "status": segment.status,
            "outcome": segment.outcome,
            "decision": segment.decision,
            "failure_reason": segment.failure_reason,
            "waiting_at": segment.current_node if segment.status == "AWAITING_APPROVAL" else None,
        }
        level: Level = "ERROR" if segment.status == "FAILED" else "DEFAULT"
        attrs = self._attrs(segment.run_id, "span", level, {"case_id": segment.case_id})
        attrs |= {
            Attr.OBSERVATION_INPUT: _json(summary),
            Attr.OBSERVATION_OUTPUT: _json({k: v for k, v in state.items() if v is not None}),
            Attr.TRACE_TAGS: [TAG, segment.case_id],
        }
        token = _PINNED.set((trace_id(segment.run_id), root_span_id(segment.run_id)))
        try:
            root = self._tracer.start_span(
                "run",
                context=otel.set_span_in_context(otel.INVALID_SPAN),  # no parent: trace root
                start_time=_ns(segment.started_at),
                attributes=attrs,
            )
        finally:
            _PINNED.reset(token)
        root.end(end_time=end)

    def event(self, event: AuditEvent) -> None:
        if event.type in SKIPPED_EVENTS:
            return
        ts = _event_ns(event)
        if event.type == NODE_ENTERED and event.node:
            self._close_open(event.run_id, ts)  # defensive: one open node per run
            span = self._tracer.start_span(
                event.node,
                context=self._parent(event.run_id, None),
                start_time=ts,
                attributes=self._attrs(event.run_id, "span", "DEFAULT", {"node": event.node}),
            )
            with self._lock:
                self._open[event.run_id] = (event.node, span)
            return
        if event.type == NODE_EXITED:
            self._close_open(event.run_id, ts, _level(event.outcome))
            return

        start = ts - (event.duration_ms or 0) * 1_000_000
        attrs = self._attrs(
            event.run_id,
            _observation_type(event.type),
            _level(event.outcome),
            {
                "event_id": event.event_id,
                "node": event.node,
                "outcome": event.outcome,
                "duration_ms": event.duration_ms,
            },
        )
        attrs[Attr.OBSERVATION_OUTPUT] = _json(event.payload)
        self._leaf(event.run_id, event.type, parent_node=event.node, start=start, end=ts,
                   attrs=attrs)  # fmt: skip
        if event.type == "run.failed":
            self._close_open(event.run_id, ts, "ERROR")

    def generation(
        self, run_id: str, node: str, model_call_id: str, attempt: AttemptRecord
    ) -> None:
        end = time.time_ns()
        attrs = self._attrs(
            run_id,
            "generation",
            "DEFAULT" if attempt.parse_result == "ok" else "WARNING",
            {
                "model_call_id": model_call_id,
                "attempt": attempt.attempt,
                "prompt_sha256": attempt.prompt_sha256,
                "parse_result": attempt.parse_result,
                "request_id": attempt.request_id,
                "duration_ms": attempt.duration_ms,
            },
        )
        attrs[Attr.OBSERVATION_USAGE_DETAILS] = _json(
            {"input": attempt.input_tokens, "output": attempt.output_tokens}
        )
        if attempt.model_id:
            attrs[Attr.OBSERVATION_MODEL] = attempt.model_id
        if self._send_prompts:
            attrs[Attr.OBSERVATION_INPUT] = _json(attempt.prompt_redacted)
            if attempt.completion_text is not None:
                attrs[Attr.OBSERVATION_OUTPUT] = _json(attempt.completion_text)
        self._leaf(run_id, f"{node.lower()}.model", parent_node=node,
                   start=end - attempt.duration_ms * 1_000_000, end=end, attrs=attrs)  # fmt: skip

    def flush(self) -> None:
        self._client.flush()
