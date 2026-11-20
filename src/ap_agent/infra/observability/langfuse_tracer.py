"""Langfuse adapter for the Tracer port. The only module that imports the langfuse SDK.

Layout in Langfuse: one trace per run. The trace id is derived from the run id
(`create_trace_id(seed=run_id)`), so a run that pauses for approval and resumes later, even
in another process, lands in the same trace. session_id is the run id too, for filtering.

- audit events → observations named by event type ("tool.attempt", "control.evaluated", ...);
  tool events are typed `tool`, retrieval events `retriever`, the rest `span`.
- model attempts → `generation` observations with model id, token usage and parse result.
  Prompt and completion text are sent only when `send_prompts` is true. Even then it is the
  redacted prompt already stored in model_calls (no full account numbers exist outside the
  fixture layer, Resolved ambiguity #3), but it still holds invoice amounts and vendor names,
  so leaving the vendor's servers is opt-in (§4 safe logging, FIN-POL-010 §2).

Durations are kept in metadata (`duration_ms`) rather than replayed as span timings: events
are forwarded after the fact, and the audit log stays the source for timing.
"""

from typing import Any, Literal

from langfuse import Langfuse, is_langfuse_span, propagate_attributes
from opentelemetry.sdk.trace.export import SpanExporter

from ap_agent.assessment.assess import AttemptRecord
from ap_agent.infra.persistence.records import AuditEvent

ObservationType = Literal["span", "tool", "retriever"]
Level = Literal["DEFAULT", "WARNING", "ERROR"]

TRACE_NAME = "ap-run"
TAGS = ["ap-rag-agent"]

# model.call duplicates the generation observation, so it is not forwarded twice.
SKIPPED_EVENTS = {"model.call"}


def _observation_type(event_type: str) -> ObservationType:
    if event_type.startswith("tool."):
        return "tool"
    if event_type.startswith("retrieval."):
        return "retriever"
    return "span"


def _level(outcome: str) -> Level:
    if outcome == "ok":
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
        self._client = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            base_url=host,
            environment=environment,
            release=release,
            # Export only our own observations, never other OpenTelemetry spans in-process.
            should_export_span=is_langfuse_span,
            span_exporter=span_exporter,
        )
        self._send_prompts = send_prompts

    @staticmethod
    def trace_id(run_id: str) -> str:
        return Langfuse.create_trace_id(seed=run_id)

    def _start(self, run_id: str, **kwargs: Any) -> None:
        with propagate_attributes(trace_name=TRACE_NAME, session_id=run_id, tags=TAGS):
            obs = self._client.start_observation(
                trace_context={"trace_id": self.trace_id(run_id)}, **kwargs
            )
            obs.end()

    def event(self, event: AuditEvent) -> None:
        if event.type in SKIPPED_EVENTS:
            return
        self._start(
            event.run_id,
            name=event.type,
            as_type=_observation_type(event.type),
            output=event.payload,
            level=_level(event.outcome),
            metadata={
                "event_id": event.event_id,
                "ts": event.ts,
                "node": event.node,
                "outcome": event.outcome,
                "duration_ms": event.duration_ms,
            },
        )

    def generation(
        self, run_id: str, node: str, model_call_id: str, attempt: AttemptRecord
    ) -> None:
        self._start(
            run_id,
            name=f"{node.lower()}.model",
            as_type="generation",
            model=attempt.model_id,
            input=attempt.prompt_redacted if self._send_prompts else None,
            output=attempt.completion_text if self._send_prompts else None,
            usage_details={"input": attempt.input_tokens, "output": attempt.output_tokens},
            level="DEFAULT" if attempt.parse_result == "ok" else "WARNING",
            metadata={
                "model_call_id": model_call_id,
                "attempt": attempt.attempt,
                "prompt_sha256": attempt.prompt_sha256,
                "parse_result": attempt.parse_result,
                "request_id": attempt.request_id,
                "duration_ms": attempt.duration_ms,
            },
        )

    def flush(self) -> None:
        self._client.flush()
