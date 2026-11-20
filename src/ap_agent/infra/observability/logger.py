"""Structured JSON logging plus the audit trail (§4: timestamps, run id, outcome, duration).

Every event is written twice from one source: a row in audit_events (system of record) and a
JSON log line (operational). `run_id` is bound once per run as the correlation id.
"""

import logging
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

import structlog

from ap_agent.infra.observability.events import EventPayload
from ap_agent.infra.observability.tracing import NULL_TRACER, Tracer
from ap_agent.infra.persistence.protocols import AuditRepository
from ap_agent.infra.persistence.records import AuditEvent

_log_file: TextIO | None = None


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def configure_logging(level: str = "INFO", path: Path | None = None) -> None:
    """JSON lines to `path` (appended) or stdout. Called once at startup by the app."""
    global _log_file
    if _log_file is not None:
        _log_file.close()
        _log_file = None
    sink: TextIO = sys.stdout
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        _log_file = sink = path.open("a", encoding="utf-8")
    factory = structlog.WriteLoggerFactory(file=sink)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(sort_keys=True),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        logger_factory=factory,
        cache_logger_on_first_use=False,
    )


class Stopwatch:
    def __init__(self, clock: Callable[[], float] = time.perf_counter) -> None:
        self._clock = clock
        self._start = clock()

    def elapsed_ms(self) -> int:
        return int((self._clock() - self._start) * 1000)


class RunEventLogger:
    """Emits audit events for one run. Payloads are allow-listed models, never dicts."""

    def __init__(
        self,
        audit: AuditRepository,
        run_id: str,
        clock: Callable[[], str] = utc_now_iso,
        tracer: Tracer = NULL_TRACER,
    ) -> None:
        self._audit = audit
        self._run_id = run_id
        self._clock = clock
        self._tracer = tracer
        self._log = structlog.get_logger("ap.audit").bind(run_id=run_id)

    @property
    def run_id(self) -> str:
        return self._run_id

    def emit(
        self,
        payload: EventPayload,
        *,
        node: str | None = None,
        outcome: str = "ok",
        duration_ms: int | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            ts=self._clock(),
            run_id=self._run_id,
            type=payload.event_type,
            node=node,
            outcome=outcome,
            duration_ms=duration_ms,
            payload=payload.model_dump(mode="json"),
        )
        event_id = self._audit.append(event)
        self._log.info(
            event.type,
            event_id=event_id,
            node=node,
            outcome=outcome,
            duration_ms=duration_ms,
            payload=event.payload,
        )
        persisted = event.model_copy(update={"event_id": event_id})
        self._tracer.event(persisted)  # after the audit row: tracing mirrors, never leads
        return persisted
