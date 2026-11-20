"""FIN-001..005 end to end through both graphs with the scripted model (brief §5)."""

import pytest

from ap_agent.decision.fingerprint import idempotency_key
from ap_agent.kernel.enums import ApprovalVerdict, DecisionCode, RunStatus, Trust
from ap_agent.orchestration.evals.cases import CaseFixture, load_cases
from ap_agent.orchestration.runtime import ApprovalReply, Orchestrator, RunView
from tests.conftest import ROOT
from tests.scenarios.conftest import OrchestratorFactory

CASES = {c.case.case_id: c for c in load_cases(ROOT / "fixtures" / "cases")}


def run_case(o: Orchestrator, fixture: CaseFixture) -> RunView:
    return o.start_run(fixture.case, faults=fixture.faults, scripted=fixture.scripted_model)


def approve(
    o: Orchestrator,
    view: RunView,
    approver: str = "A-CCM-01",
    verdict: ApprovalVerdict = ApprovalVerdict.APPROVE,
) -> ApprovalReply:
    assert view.result is not None
    return o.resolve_approval(
        view.run_id,
        verdict=verdict,
        approver_id=approver,
        decision_fingerprint=view.result["decision_fingerprint"],
    )


def _ledger_rows(o: Orchestrator, case_id: str) -> int:
    row = o.store.db.query_one("SELECT COUNT(*) FROM sim_ledger WHERE case_id = ?", (case_id,))
    return int(row[0]) if row else 0


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_case_end_to_end(make_orchestrator: OrchestratorFactory, case_id: str) -> None:
    o, fixture = make_orchestrator(), CASES[case_id]
    exp = fixture.expected
    view = run_case(o, fixture)
    assert view.result is not None
    assert view.result["recommendation"]["outcome"] == exp.recommendation.value
    assert (view.status is RunStatus.AWAITING_APPROVAL) == exp.approval_requested

    if exp.approval_requested:
        assert o.store.decisions.list_for_case(case_id) == []  # nothing posted before approval
        assert o.saver.get_tuple({"configurable": {"thread_id": view.run_id}}) is not None
        replies = [approve(o, view) for _ in range(max(exp.duplicate_callbacks, 1))]
        assert all(r.accepted for r in replies)
        assert all(r.response == replies[0].response for r in replies)  # replay-safe
        assert replies[0].response is not None
        assert replies[0].response["decision"] == DecisionCode.POST_FOR_PAYMENT.value

    final = o.get_run(view.run_id)
    assert final.status is RunStatus.COMPLETED
    decisions = o.store.decisions.list_for_case(case_id)
    assert [d.decision for d in decisions] == [exp.final_decision]  # exactly one decision
    assert _ledger_rows(o, case_id) == 1
    assert o.saver.get_tuple({"configurable": {"thread_id": view.run_id}}) is None
    has_approval = o.store.approvals.get_pending_for_run(view.run_id) is not None or any(
        e["type"] == "approval.requested" for e in final.audit_events
    )
    assert has_approval == exp.approval_requested


def test_result_is_typed_grounded_and_versioned(make_orchestrator: OrchestratorFactory) -> None:
    o = make_orchestrator()
    view = run_case(o, CASES["FIN-001"])
    r = view.result
    assert r is not None
    for key in ("sourced_facts", "calculations", "inferences", "unknowns", "policy_findings",
                "actions_taken"):  # fmt: skip
        assert key in r
    assert r["calculations"] and all(c["rounding"] for c in r["calculations"])
    assert r["applied_policy_versions"]["FIN-POL-003"] == "4.0"  # never the superseded v1
    assert "FIN-POL-003-OLD" not in r["applied_policy_versions"]
    assert r["recommendation"]["confidence"]["level"] == "HIGH"


def test_fin_003_injection_never_reaches_authority(make_orchestrator: OrchestratorFactory) -> None:
    o = make_orchestrator()
    view = run_case(o, CASES["FIN-003"])
    assert view.result is not None
    for finding in view.result["policy_findings"]:
        for c in finding["citations"]:
            assert c["trust"] == Trust.TRUSTED.value and not c["doc_id"].startswith("ADV-")
    assert o.store.approvals.get_pending_for_run(view.run_id) is None
    c6 = next(f for f in view.result["policy_findings"] if f["control_id"] == "C6")
    assert c6["status"] == "FAIL" and "4 indicators" in c6["observed"]
    # verified model indicators were fed back: C6 was re-evaluated in DECIDE
    assert any(
        e["type"] == "control.evaluated"
        and e["node"] == "DECIDE"
        and e["payload"]["control_id"] == "C6"
        for e in view.audit_events
    )


def test_fin_004_retries_then_exposes_missing_evidence(
    make_orchestrator: OrchestratorFactory,
) -> None:
    o = make_orchestrator()
    view = run_case(o, CASES["FIN-004"])
    calls = [
        t for t in o.store.tool_calls.list_for_run(view.run_id) if t.tool == "get_purchase_order"
    ]
    assert [t.outcome for t in calls] == ["timeout"] * 4  # bounded: 1 + 3 retries
    assert view.result is not None
    unknowns = " ".join(u["what"] for u in view.result["unknowns"])
    assert "purchase order PO-4004" in unknowns and "goods receipt" in unknowns
    assert [
        m.parse_result.split(":")[0] for m in o.store.model_calls.list_for_run(view.run_id)
    ] == [
        "invalid",
        "ok",
    ]  # malformed model output repaired


def test_reject_records_rejected_and_never_posts(make_orchestrator: OrchestratorFactory) -> None:
    o = make_orchestrator()
    view = run_case(o, CASES["FIN-001"])
    reply = approve(o, view, verdict=ApprovalVerdict.REJECT)
    assert reply.response is not None and reply.response["decision"] == "REJECTED"
    assert [d.decision for d in o.store.decisions.list_for_case("FIN-001")] == [
        DecisionCode.REJECTED
    ]


def test_failed_approval_check_leaves_run_pending(make_orchestrator: OrchestratorFactory) -> None:
    o = make_orchestrator()
    view = run_case(o, CASES["FIN-001"])
    bad = o.resolve_approval(view.run_id, verdict=ApprovalVerdict.APPROVE,
                             approver_id="A-CCM-01", decision_fingerprint="0" * 64)  # fmt: skip
    assert not bad.accepted and bad.failed_check == "fingerprint_mismatch"
    expired = approve(o, view, approver="A-CCM-EXP")
    assert not expired.accepted and expired.failed_check == "delegation_not_valid"
    assert o.get_run(view.run_id).status is RunStatus.AWAITING_APPROVAL
    assert o.store.decisions.list_for_case("FIN-001") == []
    events = [e["type"] for e in o.get_run(view.run_id).audit_events]
    assert events.count("approval.check_failed") == 2
    assert approve(o, view).accepted  # the right approver can still approve


def test_second_run_of_same_case_cannot_double_post(make_orchestrator: OrchestratorFactory) -> None:
    o = make_orchestrator()
    first = run_case(o, CASES["FIN-005"])
    approve(o, first)
    second = run_case(o, CASES["FIN-005"])
    reply = approve(o, second)
    key = idempotency_key("FIN-005", "INV-1001-0901", "V-1001", DecisionCode.POST_FOR_PAYMENT)
    assert reply.response is not None and reply.response["idempotency_key"] == key
    assert o.store.ledger.count_for_key(key) == 1
    assert len(o.store.decisions.list_for_case("FIN-005")) == 1
    assert o.get_run(second.run_id).status is RunStatus.COMPLETED  # not stuck awaiting
