"""Optional Langfuse tracing: mirrors the audit trail, can never change or fail a run.

The orchestration tests use an in-process recording tracer. The adapter tests drive the real
LangfuseTracer against an in-memory OpenTelemetry exporter, so nothing leaves the machine.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from ap_agent.assessment.assess import AttemptRecord
from ap_agent.infra.observability.langfuse_tracer import LangfuseTracer, root_span_id, trace_id
from ap_agent.infra.observability.tracing import NullTracer, RunSegment
from ap_agent.infra.persistence.records import AuditEvent
from ap_agent.infra.settings import ConfigError, Settings, TracingSettings
from ap_agent.kernel.enums import RunStatus
from ap_agent.orchestration.tracer_factory import build_tracer
from tests.scenarios.conftest import OrchestratorFactory
from tests.scenarios.test_fin_cases import CASES, approve, run_case

APPROVAL_CASE = next(c for c in CASES.values() if c.expected.approval_requested)


class RecordingTracer:
    def __init__(self) -> None:
        self.segments: list[RunSegment] = []
        self.events: list[AuditEvent] = []
        self.generations: list[tuple[str, str, str, AttemptRecord]] = []
        self.flushed = 0

    def run_segment(self, segment: RunSegment) -> None:
        self.segments.append(segment)

    def event(self, event: AuditEvent) -> None:
        self.events.append(event)

    def generation(
        self, run_id: str, node: str, model_call_id: str, attempt: AttemptRecord
    ) -> None:
        self.generations.append((run_id, node, model_call_id, attempt))

    def flush(self) -> None:
        self.flushed += 1


class ExplodingTracer:
    def run_segment(self, segment: RunSegment) -> None:
        raise RuntimeError("collector down")

    def event(self, event: AuditEvent) -> None:
        raise RuntimeError("collector down")

    def generation(
        self, run_id: str, node: str, model_call_id: str, attempt: AttemptRecord
    ) -> None:
        raise RuntimeError("collector down")

    def flush(self) -> None:
        raise RuntimeError("collector down")


def test_tracer_mirrors_audit_trail_across_approval(make_orchestrator: OrchestratorFactory) -> None:
    tracer = RecordingTracer()
    o = make_orchestrator(tracer=tracer)
    view = run_case(o, APPROVAL_CASE)
    approve(o, view)

    audit = o.store.audit.list_for_run(view.run_id)
    assert [e.event_id for e in tracer.events] == [e.event_id for e in audit]  # same, in order
    assert all(e.event_id is not None for e in tracer.events)  # forwarded after persistence
    calls = o.store.model_calls.list_for_run(view.run_id)
    assert [g[2] for g in tracer.generations] == [c.model_call_id for c in calls]

    # One segment when the run pauses for approval, one when the resumed run completes.
    paused, done = tracer.segments
    assert (paused.status, done.status) == ("AWAITING_APPROVAL", "COMPLETED")
    assert paused.started_at == done.started_at  # both describe the same run span
    assert (paused.decision, done.decision) == (None, "POST_FOR_PAYMENT")
    assert done.outcome == "APPROVE_FOR_POSTING" and done.case_id == APPROVAL_CASE.case.case_id
    o.close()
    assert tracer.flushed == 1


def test_tracer_failure_never_affects_the_run(make_orchestrator: OrchestratorFactory) -> None:
    traced = make_orchestrator(tracer=ExplodingTracer())
    view = run_case(traced, APPROVAL_CASE)
    reply = approve(traced, view)
    assert reply.accepted
    assert traced.get_run(view.run_id).status is RunStatus.COMPLETED
    traced.close()  # flush error swallowed too


def test_disabled_by_default_and_requires_keys_when_enabled(settings: Settings) -> None:
    assert isinstance(build_tracer(settings), NullTracer)
    enabled = settings.model_copy(update={"tracing": TracingSettings(enabled=True)})
    enabled = enabled.model_copy(update={"langfuse_public_key": None, "langfuse_secret_key": None})
    with pytest.raises(ConfigError, match="LANGFUSE_PUBLIC_KEY"):
        build_tracer(enabled)


# --- the real adapter, against an in-memory exporter --------------------------------------

T0 = datetime(2026, 11, 20, 6, 0, 0, tzinfo=UTC)


def _ts(ms: int) -> str:
    return (T0 + timedelta(milliseconds=ms)).isoformat(timespec="milliseconds")


def _event(run_id: str, event_id: int, type_: str, node: str | None, ms: int,
           duration_ms: int | None = None, outcome: str = "ok") -> AuditEvent:  # fmt: skip
    return AuditEvent(
        event_id=event_id, ts=_ts(ms), run_id=run_id, type=type_, node=node, outcome=outcome,
        duration_ms=duration_ms, payload={"tool": "get_vendor_record"},
    )  # fmt: skip


def _attempt(duration_ms: int = 40) -> AttemptRecord:
    return AttemptRecord(
        attempt=1, prompt_sha256="ab" * 32, prompt_redacted="PROMPT-BODY",
        completion_text="COMPLETION-BODY", parse_result="ok", error_count=0,
        model_id="claude-test", request_id="req-1", input_tokens=10, output_tokens=5,
        duration_ms=duration_ms,
    )  # fmt: skip


def _segment(run_id: str, status: str, ended_ms: int, decision: str | None) -> RunSegment:
    return RunSegment(
        run_id=run_id, case_id="FIN-001", vendor_id="V-1", invoice_ref="INV-1",
        amount="1234.50", currency="AUD", started_at=_ts(0), ended_at=_ts(ended_ms),
        status=status, current_node="REQUEST_APPROVAL" if decision is None else "END",
        outcome="APPROVE_FOR_POSTING", decision=decision, failure_reason=None,
    )  # fmt: skip


def _traced_run(send_prompts: bool) -> tuple[str, list[ReadableSpan]]:
    exporter = InMemorySpanExporter()
    tracer = LangfuseTracer(
        public_key=f"pk-lf-test-{uuid.uuid4().hex}",  # the SDK keeps one client per key
        secret_key="sk-lf-test",
        host="http://127.0.0.1:9",  # unused: spans go to the in-memory exporter
        environment="test",
        send_prompts=send_prompts,
        span_exporter=exporter,
    )
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    tracer.event(_event(run_id, 1, "node.entered", "GATHER", 100))
    tracer.event(_event(run_id, 2, "tool.attempt", "GATHER", 400, duration_ms=250))
    tracer.event(_event(run_id, 3, "node.exited", "GATHER", 450, duration_ms=350))
    tracer.event(_event(run_id, 4, "node.entered", "ASSESS", 500))
    tracer.event(_event(run_id, 5, "model.call", "ASSESS", 600))  # covered by the generation
    tracer.generation(run_id, "ASSESS", f"{run_id}-mc-001", _attempt())
    tracer.event(_event(run_id, 6, "node.exited", "ASSESS", 700, duration_ms=200))
    tracer.run_segment(_segment(run_id, "AWAITING_APPROVAL", 800, None))
    # resumed later, possibly in another process: same trace, same root
    tracer.event(_event(run_id, 7, "approval.granted", "APPROVAL", 5000))
    tracer.event(_event(run_id, 8, "run.completed", "END", 5100))
    tracer.run_segment(_segment(run_id, "COMPLETED", 5200, "POST_FOR_PAYMENT"))
    tracer.flush()
    return run_id, list(exporter.get_finished_spans())


def _by_name(spans: list[ReadableSpan], name: str) -> ReadableSpan:
    return next(s for s in spans if s.name == name)


def _parent_id(span: ReadableSpan) -> int | None:
    return span.parent.span_id if span.parent else None


def _span_id(span: ReadableSpan) -> int:
    assert span.context is not None
    return int(span.context.span_id)


def test_langfuse_adapter_builds_a_tree_under_one_root() -> None:
    run_id, spans = _traced_run(send_prompts=False)
    assert {s.context.trace_id for s in spans if s.context} == {trace_id(run_id)}
    assert "model.call" not in {s.name for s in spans}

    roots = [s for s in spans if s.name == "run"]
    assert len(roots) == 2  # sent at the pause and at completion; Langfuse keeps the latest
    assert {_span_id(r) for r in roots} == {root_span_id(run_id)}
    assert all(r.parent is None for r in roots)

    gather, assess = _by_name(spans, "GATHER"), _by_name(spans, "ASSESS")
    assert _parent_id(gather) == _parent_id(assess) == root_span_id(run_id)
    assert _parent_id(_by_name(spans, "tool.attempt")) == _span_id(gather)
    assert _parent_id(_by_name(spans, "assess.model")) == _span_id(assess)
    assert _parent_id(_by_name(spans, "approval.granted")) == root_span_id(run_id)
    # every parent is a span in this trace: nothing dangles
    ids = {_span_id(s) for s in spans}
    assert all(_parent_id(s) in ids for s in spans if s.parent)


def test_langfuse_adapter_keeps_real_timings() -> None:
    _, spans = _traced_run(send_prompts=False)

    def ms(span: ReadableSpan) -> float:
        assert span.start_time is not None and span.end_time is not None
        return (span.end_time - span.start_time) / 1e6

    # within ~1 ms: event times carry a microsecond ordering offset
    assert ms(_by_name(spans, "GATHER")) == pytest.approx(350, abs=1.5)  # entered → exited
    assert ms(_by_name(spans, "tool.attempt")) == pytest.approx(250, abs=1.5)  # duration_ms
    final_root = max((s for s in spans if s.name == "run"), key=lambda s: s.end_time or 0)
    assert ms(final_root) == pytest.approx(5200, abs=1.5)  # whole run, across the approval pause


def test_langfuse_adapter_orders_same_millisecond_events_by_audit_order() -> None:
    exporter = InMemorySpanExporter()
    tracer = LangfuseTracer(public_key=f"pk-lf-test-{uuid.uuid4().hex}", secret_key="sk",
                            host="http://127.0.0.1:9", environment="test",
                            span_exporter=exporter)  # fmt: skip
    run_id = "run-sameclock"
    names = ["tool.attempt", "retrieval.performed", "tool.retry", "control.evaluated"]
    for event_id, name in enumerate(names, start=1):
        tracer.event(_event(run_id, event_id, name, None, 100))  # all in the same millisecond
    tracer.flush()
    spans = sorted(exporter.get_finished_spans(), key=lambda s: s.start_time or 0)
    assert [s.name for s in spans] == names


@pytest.mark.parametrize("send_prompts", [False, True])
def test_langfuse_adapter_withholds_text_and_amounts_by_default(send_prompts: bool) -> None:
    run_id, spans = _traced_run(send_prompts)
    dumped = " ".join(str(dict(s.attributes or {})) for s in spans)
    assert run_id in dumped  # session id
    assert "FIN-001" in dumped and "POST_FOR_PAYMENT" in dumped  # root input/output
    assert ("PROMPT-BODY" in dumped) is send_prompts
    assert ("COMPLETION-BODY" in dumped) is send_prompts
    assert ("1234.50" in dumped) is send_prompts
