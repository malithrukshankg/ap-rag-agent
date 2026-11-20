"""Runs FIN-001..005 and reports pass/fail (brief §2 "List evaluation results", §5).

Each eval run uses its own fresh database, so metrics such as "exactly one posting" are
measured in isolation and production data is never touched. The model is the configured
adapter: scripted (stable tier) or anthropic (live tier).
"""

import uuid
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from ap_agent.assessment.client import ModelClient
from ap_agent.controls.params import PolicyParams
from ap_agent.evidence.rag.registry import PolicyRegistry
from ap_agent.infra.observability.logger import utc_now_iso
from ap_agent.infra.settings import Settings
from ap_agent.kernel.enums import ApprovalVerdict, RunStatus
from ap_agent.orchestration.evals.cases import CaseFixture
from ap_agent.orchestration.evals.metrics import (
    CaseEval,
    EvalReport,
    aggregate_metrics,
    evaluate_case,
)
from ap_agent.orchestration.runtime import Orchestrator


def eval_db_path(settings: Settings) -> Path:
    return Path(settings.storage.sqlite_path).parent / "evals" / f"eval-{uuid.uuid4().hex[:8]}.db"


def _approver_for(o: Orchestrator, amount: Any, currency: str) -> str:
    """An active approver whose limit covers the amount (the lowest sufficient one)."""
    candidates = sorted(
        (a for a in o.backend.approvers.values()
         if a.active and a.currency == currency and a.authority_limit >= amount),
        key=lambda a: a.authority_limit,
    )  # fmt: skip
    return candidates[0].approver_id if candidates else "none"


def run_evals(
    settings: Settings,
    *,
    registry: PolicyRegistry,
    params: PolicyParams,
    cases: Sequence[CaseFixture],
    model_client_factory: Callable[[CaseFixture], ModelClient | None] = lambda _c: None,
) -> EvalReport:
    storage = settings.storage.model_copy(update={"sqlite_path": eval_db_path(settings)})
    o = Orchestrator(settings.model_copy(update={"storage": storage}),
                     registry=registry, params=params)  # fmt: skip
    results: list[CaseEval] = []
    try:
        for fixture in cases:
            view = o.start_run(
                fixture.case,
                faults=fixture.faults,
                scripted=fixture.scripted_model,
                model_client=model_client_factory(fixture),
            )
            requested = view.status is RunStatus.AWAITING_APPROVAL
            replies: list[dict[str, Any] | None] = []
            if requested and view.result is not None:
                approver = _approver_for(o, fixture.case.amount, fixture.case.currency)
                for _ in range(max(fixture.expected.duplicate_callbacks, 1)):
                    reply = o.resolve_approval(
                        view.run_id,
                        verdict=ApprovalVerdict.APPROVE,
                        approver_id=approver,
                        decision_fingerprint=view.result["decision_fingerprint"],
                    )
                    replies.append(reply.model_dump(mode="json"))
            results.append(
                evaluate_case(
                    o.store, fixture, view.run_id, approval_was_requested=requested, replies=replies
                )
            )
    finally:
        o.close()
    metrics = aggregate_metrics(results)
    return EvalReport(
        run_at=utc_now_iso(),
        adapter=settings.model.adapter,
        passed=all(c.passed for c in results) and all(m.passed for m in metrics),
        metrics=metrics,
        cases=results,
    )
