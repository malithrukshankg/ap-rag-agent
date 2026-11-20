"""Optional Langfuse tracing: mirrors the audit trail, can never change or fail a run.

The orchestration tests use an in-process recording tracer. The adapter test drives the real
LangfuseTracer against an in-memory OpenTelemetry exporter, so nothing leaves the machine.
"""

import uuid

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from ap_agent.assessment.assess import AttemptRecord
from ap_agent.infra.observability.langfuse_tracer import LangfuseTracer
from ap_agent.infra.observability.tracing import NullTracer
from ap_agent.infra.persistence.records import AuditEvent
from ap_agent.infra.settings import ConfigError, Settings, TracingSettings
from ap_agent.kernel.enums import RunStatus
from ap_agent.orchestration.tracer_factory import build_tracer
from tests.scenarios.conftest import OrchestratorFactory
from tests.scenarios.test_fin_cases import CASES, approve, run_case

APPROVAL_CASE = next(c for c in CASES.values() if c.expected.approval_requested)


class RecordingTracer:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []
        self.generations: list[tuple[str, str, str, AttemptRecord]] = []
        self.flushed = 0

    def event(self, event: AuditEvent) -> None:
        self.events.append(event)

    def generation(
        self, run_id: str, node: str, model_call_id: str, attempt: AttemptRecord
    ) -> None:
        self.generations.append((run_id, node, model_call_id, attempt))

    def flush(self) -> None:
        self.flushed += 1


class ExplodingTracer:
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


def _attempt(prompt: str = "PROMPT-BODY", completion: str = "COMPLETION-BODY") -> AttemptRecord:
    return AttemptRecord(
        attempt=1, prompt_sha256="ab" * 32, prompt_redacted=prompt, completion_text=completion,
        parse_result="ok", error_count=0, model_id="claude-test", request_id="req-1",
        input_tokens=10, output_tokens=5, duration_ms=12,
    )  # fmt: skip


def _event(run_id: str, type_: str, outcome: str = "ok") -> AuditEvent:
    return AuditEvent(
        event_id=1, ts="2026-11-20T00:00:00.000+00:00", run_id=run_id, type=type_,
        node="FETCH", outcome=outcome, duration_ms=3, payload={"tool": "get_vendor_record"},
    )  # fmt: skip


@pytest.mark.parametrize("send_prompts", [False, True])
def test_langfuse_adapter_one_trace_per_run(send_prompts: bool) -> None:
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
    tracer.event(_event(run_id, "tool.attempt"))
    tracer.event(_event(run_id, "model.call"))  # covered by the generation: not forwarded
    tracer.event(_event(run_id, "run.failed", outcome="failed"))
    tracer.generation(run_id, "ASSESS", f"{run_id}-mc-001", _attempt())
    tracer.flush()

    spans = exporter.get_finished_spans()
    assert sorted(s.name for s in spans) == ["assess.model", "run.failed", "tool.attempt"]
    expected_trace = int(LangfuseTracer.trace_id(run_id), 16)
    assert {s.context.trace_id for s in spans if s.context} == {expected_trace}

    dumped = " ".join(str(dict(s.attributes or {})) for s in spans)
    assert run_id in dumped  # session id
    assert ("PROMPT-BODY" in dumped) is send_prompts
    assert ("COMPLETION-BODY" in dumped) is send_prompts
