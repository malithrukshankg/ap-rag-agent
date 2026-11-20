"""submit_finance_decision (SIMULATED sandbox ledger, §8): the only consequential tool.

Ordered steps (architecture §6.3):
1. schema validation (ToolRunner + SubmitRequest)
2. idempotency lookup: same key + same payload → stored response (REPLAYED); same key +
   different payload → conflict. Runs before the grant check, so replaying a decision whose
   grant is already consumed still succeeds.
3. approval gate: POST_FOR_PAYMENT needs an unconsumed grant for this run whose fingerprint
   and amount match (requires_approval = decision == POST_FOR_PAYMENT, enforced here).
4. echoed arguments verified against the run's stored recommendation; the COMPUTED total is
   recorded, never the supplied one.
5. run-state precondition: the run is not terminal.
6. simulated ledger posting, idempotent on the key, in its own transaction.
7. one transaction: write the decision, consume the grant, append decision.recorded.
8. typed result: RECORDED or REPLAYED.

Internal only: never exposed on the HTTP surface. Only the SUBMIT node may call it.
"""

import uuid
from collections.abc import Callable
from decimal import Decimal

from pydantic import Field

from ap_agent.decision.fingerprint import idempotency_key, payload_sha256
from ap_agent.decision.routing import DECISION_ROUTES, VERDICT_DECISIONS
from ap_agent.evidence.tools.base import PermanentToolError
from ap_agent.infra.observability.events import DecisionRecorded, DecisionReplayed
from ap_agent.infra.observability.logger import RunEventLogger
from ap_agent.infra.persistence.protocols import DuplicateKeyError, Store
from ap_agent.infra.persistence.records import DecisionRecord, LedgerEntry
from ap_agent.kernel.enums import (
    ApprovalStatus,
    ApprovalVerdict,
    DecisionCode,
    RecommendationCode,
    RunStatus,
    SubmitStatus,
)
from ap_agent.kernel.types import Currency, Money, Strict


class SubmitRequest(Strict):
    run_id: str
    case_id: str
    invoice_ref: str
    vendor_id: str
    decision: DecisionCode
    amount: Money  # echoed by the caller; verified, never recorded as-is
    currency: Currency
    grant_id: str | None = None
    decision_fingerprint: str | None = None
    escalation_target: str | None = Field(default=None, max_length=100)


class SubmitResult(Strict):
    status: SubmitStatus
    decision_id: str
    decision: DecisionCode
    idempotency_key: str
    ledger_ref: str
    computed_total: Money
    currency: Currency


class SubmitRefused(PermanentToolError):
    """A precondition failed. Never retried; nothing was written."""


class SubmitFinanceDecisionTool:
    name = "submit_finance_decision"
    input_model = SubmitRequest
    output_model = SubmitResult

    def __init__(
        self,
        store: Store,
        events: RunEventLogger,
        *,
        clock: Callable[[], str],
        node: str = "SUBMIT",
        after_ledger: Callable[[], None] | None = None,  # crash-injection hook for tests
    ) -> None:
        self._store = store
        self._events = events
        self._clock = clock
        self._node = node
        self._after_ledger = after_ledger

    def _replay(self, record: DecisionRecord) -> SubmitResult:
        self._events.emit(
            DecisionReplayed(
                decision_id=record.decision_id, idempotency_key=record.idempotency_key
            ),
            node=self._node,
            outcome="replayed",
        )
        return SubmitResult.model_validate({**record.response, "status": SubmitStatus.REPLAYED})

    def __call__(self, args: SubmitRequest) -> SubmitResult:
        key = idempotency_key(args.case_id, args.invoice_ref, args.vendor_id, args.decision)
        payload = payload_sha256(
            args.case_id, args.invoice_ref, args.vendor_id, args.decision, args.amount,
            args.currency,
        )  # fmt: skip

        # 2. idempotency lookup
        existing = self._store.decisions.get_by_key(key)
        if existing is not None:
            if existing.payload_sha256 != payload:
                raise SubmitRefused("idempotency key reused with a different payload")
            return self._replay(existing)

        run = self._store.runs.get(args.run_id)
        if run is None or run.result is None:
            raise SubmitRefused(f"run {args.run_id} has no recommendation")

        # 3. approval gate (deny-by-default for postings)
        grant = None
        if args.grant_id is not None:
            grant = self._store.approvals.get_by_grant(args.grant_id)
        if args.decision is DecisionCode.POST_FOR_PAYMENT and (
            grant is None
            or grant.run_id != args.run_id
            or grant.status is not ApprovalStatus.APPROVED
            or grant.grant_consumed_at is not None
            or grant.decision_fingerprint != args.decision_fingerprint
            or grant.amount != args.amount
        ):
            raise SubmitRefused("POST_FOR_PAYMENT requires a valid, unconsumed approval grant")

        # 4. verify echoed arguments against the stored recommendation
        outcome = RecommendationCode(run.result["recommendation"]["outcome"])
        if outcome is RecommendationCode.APPROVE_FOR_POSTING:
            if grant is None or grant.verdict is None:
                raise SubmitRefused("an approval-gated recommendation needs an approver verdict")
            allowed = VERDICT_DECISIONS[ApprovalVerdict(grant.verdict)]
        else:
            route_decision = DECISION_ROUTES[outcome].decision
            assert route_decision is not None
            allowed = route_decision
        if args.decision is not allowed:
            raise SubmitRefused(f"decision {args.decision.value} does not match {outcome.value}")
        computed_total = Decimal(str(run.request_redacted["amount"]))
        if args.amount != computed_total or args.currency != run.request_redacted["currency"]:
            raise SubmitRefused("echoed amount or currency does not match the case")
        if (args.case_id, args.invoice_ref, args.vendor_id) != (
            run.case_id,
            run.request_redacted["invoice_ref"],
            run.request_redacted["vendor_id"],
        ):
            raise SubmitRefused("echoed case identity does not match the run")

        # 5. run-state precondition
        if run.status not in (RunStatus.RUNNING, RunStatus.AWAITING_APPROVAL):
            raise SubmitRefused(f"run is {run.status.value}")

        # 6. simulated ledger posting, idempotent on the key
        ledger = self._store.ledger.post(
            LedgerEntry(
                ledger_ref=f"L-{uuid.uuid4().hex[:12]}",
                idempotency_key=key,
                case_id=args.case_id,
                decision=args.decision,
                amount=computed_total,
                currency=args.currency,
                created_at=self._clock(),
            )
        )
        if self._after_ledger is not None:
            self._after_ledger()

        # 7. one transaction: decision + grant consumption + audit event
        decision_id = f"dec-{uuid.uuid4().hex[:12]}"
        result = SubmitResult(
            status=SubmitStatus.RECORDED,
            decision_id=decision_id,
            decision=args.decision,
            idempotency_key=key,
            ledger_ref=ledger.ledger_ref,
            computed_total=computed_total,
            currency=args.currency,
        )
        try:
            with self._store.transaction():
                self._store.decisions.insert(
                    DecisionRecord(
                        decision_id=decision_id,
                        run_id=args.run_id,
                        case_id=args.case_id,
                        idempotency_key=key,
                        decision=args.decision,
                        payload_sha256=payload,
                        computed_total=computed_total,
                        currency=args.currency,
                        grant_id=args.grant_id,
                        ledger_ref=ledger.ledger_ref,
                        response=result.model_dump(mode="json"),
                        created_at=self._clock(),
                    )
                )
                if grant is not None and not self._store.approvals.consume_grant(
                    grant.grant_id or "", self._clock()
                ):
                    raise SubmitRefused("approval grant already consumed")
                self._events.emit(
                    DecisionRecorded(
                        decision_id=decision_id,
                        decision=args.decision,
                        idempotency_key=key,
                        ledger_ref=ledger.ledger_ref,
                    ),
                    node=self._node,
                )
        except DuplicateKeyError:
            # A concurrent delivery won the race; return its decision.
            winner = self._store.decisions.get_by_key(key)
            assert winner is not None
            return self._replay(winner)
        return result
