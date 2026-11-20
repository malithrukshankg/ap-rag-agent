"""Budget exhaustion, crash and restart, submit refusals, and the redaction canary."""

import json
import sqlite3
from decimal import Decimal

import pytest

from ap_agent.decision.submit import SubmitFinanceDecisionTool, SubmitRefused, SubmitRequest
from ap_agent.infra.observability.logger import RunEventLogger
from ap_agent.infra.persistence.records import RunRecord
from ap_agent.infra.settings import BudgetSettings, ConfigError, Settings
from ap_agent.kernel.enums import ApprovalVerdict, DecisionCode, RunStatus
from ap_agent.orchestration.runtime import Orchestrator
from tests.conftest import ROOT
from tests.scenarios.conftest import CrashInjected, OrchestratorFactory
from tests.scenarios.test_fin_cases import CASES, approve, run_case

TS = "2026-11-20T00:00:00.000+00:00"


def _crash() -> None:
    raise CrashInjected


# --- budget ---------------------------------------------------------------------------


def test_budget_exhaustion_fails_with_no_decision(
    make_orchestrator: OrchestratorFactory, settings: Settings
) -> None:
    tight = settings.model_copy(
        update={
            "budget": BudgetSettings(
                max_steps=25, max_tool_calls=5, max_model_calls=10, max_retries_per_call=3
            )
        }
    )
    o = make_orchestrator(tight)
    view = run_case(o, CASES["FIN-001"])
    assert view.status is RunStatus.FAILED and view.failure_reason == "budget_exhausted"
    assert view.budget["tool_calls_used"] == 5
    assert o.store.decisions.list_for_case("FIN-001") == []
    assert o.store.db.query_one("SELECT COUNT(*) FROM sim_ledger")[0] == 0  # type: ignore[index]
    types = [e["type"] for e in view.audit_events]
    assert "budget.exhausted" in types and types[-1] == "run.failed"
    assert o.saver.get_tuple({"configurable": {"thread_id": view.run_id}}) is None


# --- crash and restart ----------------------------------------------------------------


def test_crash_between_ledger_and_decision_posts_exactly_once(
    make_orchestrator: OrchestratorFactory,
) -> None:
    crashing = make_orchestrator(after_ledger=_crash)
    view = run_case(crashing, CASES["FIN-001"])
    with pytest.raises(CrashInjected):
        approve(crashing, view)
    assert (
        crashing.store.ledger.count_for_key(
            crashing.store.db.query_one("SELECT idempotency_key FROM sim_ledger")[0]  # type: ignore[index]
        )
        == 1
    )
    assert crashing.store.decisions.list_for_case("FIN-001") == []
    crashing.close()

    restarted = make_orchestrator()  # process restart, same database
    assert restarted.recover_interrupted() == []  # a paused run is not "interrupted"
    assert restarted.get_run(view.run_id).status is RunStatus.AWAITING_APPROVAL
    reply = approve(restarted, view)  # the callback is redelivered
    assert reply.accepted and reply.response is not None
    assert reply.response["decision"] == "POST_FOR_PAYMENT"
    assert restarted.store.db.query_one("SELECT COUNT(*) FROM sim_ledger")[0] == 1  # type: ignore[index]
    assert len(restarted.store.decisions.list_for_case("FIN-001")) == 1
    assert restarted.get_run(view.run_id).status is RunStatus.COMPLETED


def test_crash_on_start_path_is_failed_then_rerun_once(
    make_orchestrator: OrchestratorFactory,
) -> None:
    crashing = make_orchestrator(after_ledger=_crash)
    with pytest.raises(CrashInjected):
        run_case(crashing, CASES["FIN-002"])
    crashing.close()

    restarted = make_orchestrator()
    interrupted = restarted.recover_interrupted()  # O7
    assert len(interrupted) == 1
    assert restarted.get_run(interrupted[0]).failure_reason == "interrupted"
    rerun = run_case(restarted, CASES["FIN-002"])  # the caller re-runs the case
    assert rerun.status is RunStatus.COMPLETED
    assert restarted.store.db.query_one("SELECT COUNT(*) FROM sim_ledger")[0] == 1  # type: ignore[index]
    assert len(restarted.store.decisions.list_for_case("FIN-002")) == 1


def test_restart_while_awaiting_approval_resumes(make_orchestrator: OrchestratorFactory) -> None:
    first = make_orchestrator()
    view = run_case(first, CASES["FIN-005"])
    assert view.status is RunStatus.AWAITING_APPROVAL
    first.close()

    second = make_orchestrator()
    assert second.saver.get_tuple({"configurable": {"thread_id": view.run_id}}) is not None
    reply = approve(second, view)
    assert reply.accepted and reply.response is not None
    assert reply.response["decision"] == "POST_FOR_PAYMENT"
    assert second.get_run(view.run_id).status is RunStatus.COMPLETED


def test_stale_running_run_is_marked_interrupted(make_orchestrator: OrchestratorFactory) -> None:
    o = make_orchestrator()
    o.store.runs.create(
        RunRecord(
            run_id="run-stale",
            case_id="FIN-001",
            status=RunStatus.RUNNING,
            current_node="GATHER",
            request_redacted={},
            config_snapshot={},
            budget={},
            created_at=TS,
            updated_at=TS,
        )
    )
    assert o.recover_interrupted() == ["run-stale"]
    view = o.get_run("run-stale")
    assert view.status is RunStatus.FAILED and view.failure_reason == "interrupted"
    assert view.audit_events[-1]["payload"]["reason"] == "interrupted"


# --- submit refusals ------------------------------------------------------------------


def _submit_tool(o: Orchestrator) -> SubmitFinanceDecisionTool:
    return SubmitFinanceDecisionTool(o.store, RunEventLogger(o.store.audit, "t"), clock=lambda: TS)


def _request(view, decision: DecisionCode, **kw: object) -> SubmitRequest:  # type: ignore[no-untyped-def]
    case = CASES[view.case_id].case
    return SubmitRequest(
        run_id=view.run_id, case_id=case.case_id, invoice_ref=case.invoice_ref,
        vendor_id=case.vendor_id, decision=decision, amount=case.amount, currency=case.currency,
        **kw,
    )  # fmt: skip


def test_post_without_a_grant_is_refused(make_orchestrator: OrchestratorFactory) -> None:
    o = make_orchestrator()
    view = run_case(o, CASES["FIN-001"])
    tool = _submit_tool(o)
    with pytest.raises(SubmitRefused, match="approval grant"):
        tool(_request(view, DecisionCode.POST_FOR_PAYMENT))
    with pytest.raises(SubmitRefused, match="approval grant"):
        tool(_request(view, DecisionCode.POST_FOR_PAYMENT, grant_id="grant-forged",
                      decision_fingerprint=view.result["decision_fingerprint"]))  # type: ignore[index]  # fmt: skip
    assert o.store.db.query_one("SELECT COUNT(*) FROM sim_ledger")[0] == 0  # type: ignore[index]


def test_post_against_a_hold_recommendation_is_refused(
    make_orchestrator: OrchestratorFactory,
) -> None:
    o = make_orchestrator()
    view = run_case(o, CASES["FIN-004"])  # completed as HOLD; tool must still refuse a POST
    with pytest.raises(SubmitRefused):
        _submit_tool(o)(_request(view, DecisionCode.POST_FOR_PAYMENT))


def test_same_key_with_different_payload_is_a_conflict(
    make_orchestrator: OrchestratorFactory,
) -> None:
    o = make_orchestrator()
    view = run_case(o, CASES["FIN-002"])  # HOLD recorded
    tampered = _request(view, DecisionCode.HOLD).model_copy(update={"amount": Decimal("1.00")})
    with pytest.raises(SubmitRefused, match="different payload"):
        _submit_tool(o)(tampered)
    replay = _submit_tool(o)(_request(view, DecisionCode.HOLD))
    assert replay.status.value == "REPLAYED"


# --- canary ---------------------------------------------------------------------------


def test_canary_no_full_account_number_anywhere(
    make_orchestrator: OrchestratorFactory, settings: Settings
) -> None:
    o = make_orchestrator()
    for case_id in sorted(CASES):
        view = run_case(o, CASES[case_id])
        if view.status is RunStatus.AWAITING_APPROVAL:
            approve(o, view)
            approve(o, view)
    reject = run_case(o, CASES["FIN-001"].model_copy())
    approve(o, reject, verdict=ApprovalVerdict.REJECT)
    o.close()

    vendors = json.loads((ROOT / "fixtures/systems/vendors.json").read_text("utf-8"))["vendors"]
    secrets = [v["bank"]["account_number"] for v in vendors]
    conn = sqlite3.connect(str(settings.storage.sqlite_path))
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
    scanned = 0
    for table in tables:
        for row in conn.execute(f'SELECT * FROM "{table}"'):
            for value in row:
                if value is None:
                    continue
                text = value.decode("latin-1") if isinstance(value, bytes) else str(value)
                scanned += 1
                for secret in secrets:
                    assert secret not in text, f"{secret} leaked into {table}"
    conn.close()
    assert scanned > 1000
    # The scan includes model_calls.prompt_redacted, tool_calls, audit_events and checkpoints.
    log_text = settings.logging.path.read_text("utf-8")
    assert log_text and all(secret not in log_text for secret in secrets)


def test_live_mode_without_a_key_fails_fast(
    make_orchestrator: OrchestratorFactory, settings: Settings
) -> None:
    live = settings.model_copy(
        update={
            "model": settings.model.model_copy(update={"adapter": "anthropic"}),
            "anthropic_api_key": None,
        }
    )
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        make_orchestrator(live)
