"""Composition root: loads configuration once, owns the Orchestrator, exposes the services.

Delivery layers (HTTP today; CLI or a worker later) depend only on this container and the
service classes, never on the Orchestrator or infrastructure directly.
"""

from dataclasses import dataclass, field
from pathlib import Path

from ap_agent.controls.params import PolicyParams
from ap_agent.evidence.rag.registry import PolicyRegistry
from ap_agent.infra.observability.logger import configure_logging
from ap_agent.infra.settings import (
    Settings,
    load_policy_params,
    load_registry,
    load_settings,
)
from ap_agent.orchestration.runtime import Orchestrator
from ap_agent.services.approvals import ApprovalService
from ap_agent.services.evals import EvalService
from ap_agent.services.runs import RunService

DEFAULT_CASES_DIR = Path("fixtures/cases")


@dataclass
class ServiceContainer:
    settings: Settings
    registry: PolicyRegistry
    params: PolicyParams
    orchestrator: Orchestrator
    cases_dir: Path = field(default=DEFAULT_CASES_DIR)

    @property
    def runs(self) -> RunService:
        return RunService(self.orchestrator)

    @property
    def approvals(self) -> ApprovalService:
        return ApprovalService(self.orchestrator)

    @property
    def evals(self) -> EvalService:
        return EvalService(
            self.settings, registry=self.registry, params=self.params, cases_dir=self.cases_dir
        )

    def close(self) -> None:
        self.orchestrator.close()


def build_container() -> ServiceContainer:
    """Default wiring from config/app.yaml and the environment; runs the O7 startup hook."""
    settings = load_settings()
    configure_logging(settings.logging.level, settings.logging.path)
    registry = load_registry(settings.corpus.registry_path)
    params = load_policy_params(settings.policy.params_path)
    container = ServiceContainer(
        settings=settings,
        registry=registry,
        params=params,
        orchestrator=Orchestrator(settings, registry=registry, params=params),
    )
    container.runs.recover_interrupted()
    return container
