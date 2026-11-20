"""The service layer, used directly (no HTTP): the same use cases the API delegates to."""

import pytest

from ap_agent.infra.settings import Settings, load_policy_params, load_registry
from ap_agent.kernel.enums import ApprovalVerdict, RunStatus
from ap_agent.services import NoEvalReport, RunNotFound, ServiceContainer
from tests.conftest import ROOT
from tests.scenarios.conftest import OrchestratorFactory
from tests.scenarios.test_fin_cases import CASES


@pytest.fixture
def container(make_orchestrator: OrchestratorFactory, settings: Settings) -> ServiceContainer:
    return ServiceContainer(
        settings=settings,
        registry=load_registry(settings.corpus.registry_path),
        params=load_policy_params(settings.policy.params_path),
        orchestrator=make_orchestrator(),
        cases_dir=ROOT / "fixtures" / "cases",
    )


def test_start_get_with_timeline_and_approve(container: ServiceContainer) -> None:
    f = CASES["FIN-001"]
    run = container.runs.start(f.case, faults=f.faults, scripted=f.scripted_model)
    assert run.status is RunStatus.AWAITING_APPROVAL and run.timeline is None
    detailed = container.runs.get(run.run_id, timeline=True)
    assert detailed.timeline and detailed.timeline[0].type == "node.entered"
    assert run.result is not None
    result = container.approvals.resolve(
        run.run_id,
        verdict=ApprovalVerdict.APPROVE,
        approver_id="A-CCM-01",
        decision_fingerprint=run.result["decision_fingerprint"],
    )
    assert result.accepted and container.runs.get(run.run_id).status is RunStatus.COMPLETED


def test_unknown_run_raises_run_not_found(container: ServiceContainer) -> None:
    with pytest.raises(RunNotFound):
        container.runs.get("run-nope")
    with pytest.raises(RunNotFound):
        container.approvals.resolve(
            "run-nope", verdict=ApprovalVerdict.APPROVE, approver_id="A-CCM-01",
            decision_fingerprint="0" * 64,
        )  # fmt: skip


def test_evals_run_then_latest(container: ServiceContainer) -> None:
    with pytest.raises(NoEvalReport):
        container.evals.latest()
    report = container.evals.run()
    assert report.passed
    assert container.evals.latest() == report
