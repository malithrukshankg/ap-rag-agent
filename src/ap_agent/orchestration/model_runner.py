"""Runs the ASSESS loop with persistence and budget (architecture §5, §9).

assessment/ is pure; this is where each attempt is charged to the budget, written to
model_calls (prompt_sha256, prompt_redacted, completion) and audited, and where every
rejected citation becomes a `citation.rejected` event.
"""

from collections.abc import Callable, Sequence

from ap_agent.assessment.assess import AssessmentOutcome, AttemptRecord, assess
from ap_agent.assessment.client import ModelClient
from ap_agent.assessment.prompt import build_request
from ap_agent.assessment.redaction import EvidencePack
from ap_agent.assessment.verify import VerifiedAssessment, verify_assessment
from ap_agent.controls.result import ControlResult
from ap_agent.infra.observability.events import BudgetExhausted as BudgetExhaustedEvent
from ap_agent.infra.observability.events import CitationRejected, ModelCall, ModelRepair
from ap_agent.infra.observability.logger import RunEventLogger, utc_now_iso
from ap_agent.infra.observability.tracing import NULL_TRACER, Tracer
from ap_agent.infra.persistence.protocols import Store
from ap_agent.infra.persistence.records import ModelCallRecord
from ap_agent.orchestration.budget import Budget, BudgetExhausted


class ModelRunner:
    def __init__(
        self,
        *,
        run_id: str,
        client: ModelClient,
        store: Store,
        events: RunEventLogger,
        budget: Budget,
        max_tokens: int,
        max_repairs: int,
        node: str = "ASSESS",
        clock: Callable[[], str] = utc_now_iso,
        tracer: Tracer = NULL_TRACER,
    ) -> None:
        self._tracer = tracer
        self._run_id = run_id
        self._client = client
        self._store = store
        self._events = events
        self._budget = budget
        self._max_tokens = max_tokens
        self._max_repairs = max_repairs
        self._node = node
        self._clock = clock

    def _charge(self) -> None:
        try:
            self._budget.charge("model_calls")
        except BudgetExhausted as exc:
            self._events.emit(
                BudgetExhaustedEvent(counter="model_calls", limit=exc.limit),
                node=self._node,
                outcome="failed",
            )
            raise

    def _record(self, a: AttemptRecord) -> None:
        call_id = f"{self._run_id}-mc-{a.attempt:03d}"
        self._store.model_calls.save(
            ModelCallRecord(
                model_call_id=call_id,
                run_id=self._run_id,
                node=self._node,
                attempt=a.attempt,
                prompt_sha256=a.prompt_sha256,
                prompt_redacted=a.prompt_redacted,
                completion_text=a.completion_text,
                parse_result=a.parse_result,
                model_id=a.model_id,
                request_id=a.request_id,
                input_tokens=a.input_tokens,
                output_tokens=a.output_tokens,
                duration_ms=a.duration_ms,
                ts=self._clock(),
            )
        )
        self._tracer.generation(self._run_id, self._node, call_id, a)
        ok = a.parse_result == "ok"
        self._events.emit(
            ModelCall(
                model_call_id=call_id,
                attempt=a.attempt,
                prompt_sha256=a.prompt_sha256,
                parse_ok=ok,
                input_tokens=a.input_tokens,
                output_tokens=a.output_tokens,
            ),
            node=self._node,
            outcome="ok" if ok else "invalid",
            duration_ms=a.duration_ms,
        )
        if not ok and a.attempt <= self._max_repairs:
            self._events.emit(
                ModelRepair(attempt=a.attempt + 1, error_count=a.error_count),
                node=self._node,
                outcome="retry",
            )

    def assess(self, pack: EvidencePack) -> AssessmentOutcome:
        return assess(
            self._client,
            build_request(pack, self._max_tokens),
            max_repairs=self._max_repairs,
            charge=self._charge,
            record=self._record,
        )

    def verify(
        self, outcome: AssessmentOutcome, pack: EvidencePack, results: Sequence[ControlResult]
    ) -> VerifiedAssessment | None:
        if outcome.assessment is None:
            return None
        verified = verify_assessment(outcome.assessment, pack, results)
        for rejection in verified.rejections:
            self._events.emit(
                CitationRejected(chunk_id=rejection.chunk_id, reason=rejection.reason),
                node=self._node,
                outcome="rejected",
            )
        return verified
