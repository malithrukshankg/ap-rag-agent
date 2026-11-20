"""Tracer port: an optional, observe-only mirror of the audit trail (Langfuse, §10 extension).

The system of record stays audit_events and model_calls in SQLite. A tracer receives copies
*after* they are persisted, so it can never change, delay or fail a run:

- `event` gets the already-persisted AuditEvent. Its payload is an allow-listed model dump
  (events.py), so nothing reaches a tracer that the audit log would not hold.
- `generation` gets one model attempt. Whether redacted prompt and completion text go with it
  is the adapter's choice (`tracing.send_prompts`, off by default).

`FailSafeTracer` wraps every tracer the Orchestrator uses: a tracer exception is logged
once per call and swallowed. Observability is never a reason for a run to fail.
"""

from typing import Protocol

import structlog

from ap_agent.assessment.assess import AttemptRecord
from ap_agent.infra.persistence.records import AuditEvent


class Tracer(Protocol):
    def event(self, event: AuditEvent) -> None: ...

    def generation(
        self, run_id: str, node: str, model_call_id: str, attempt: AttemptRecord
    ) -> None: ...

    def flush(self) -> None: ...


class NullTracer:
    """Default: tracing disabled. No network, no dependency loaded."""

    def event(self, event: AuditEvent) -> None:
        return None

    def generation(
        self, run_id: str, node: str, model_call_id: str, attempt: AttemptRecord
    ) -> None:
        return None

    def flush(self) -> None:
        return None


NULL_TRACER = NullTracer()


class FailSafeTracer:
    def __init__(self, inner: Tracer) -> None:
        self._inner = inner
        self._log = structlog.get_logger("ap.tracing")

    @property
    def inner(self) -> Tracer:
        return self._inner

    def _warn(self, op: str, exc: Exception) -> None:
        # Exception class only: messages from exporters can echo request bodies.
        self._log.warning("tracer.error", op=op, error_type=type(exc).__name__)

    def event(self, event: AuditEvent) -> None:
        try:
            self._inner.event(event)
        except Exception as exc:
            self._warn("event", exc)

    def generation(
        self, run_id: str, node: str, model_call_id: str, attempt: AttemptRecord
    ) -> None:
        try:
            self._inner.generation(run_id, node, model_call_id, attempt)
        except Exception as exc:
            self._warn("generation", exc)

    def flush(self) -> None:
        try:
            self._inner.flush()
        except Exception as exc:
            self._warn("flush", exc)
