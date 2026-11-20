"""List evaluation results use case (brief §2): run FIN-001..005, keep the latest report.

Each run uses its own fresh database (see orchestration/evals/runner.py); the latest report
is stored as JSON next to the main database so it survives a restart.
"""

import json
from pathlib import Path

from ap_agent.controls.params import PolicyParams
from ap_agent.evidence.rag.registry import PolicyRegistry
from ap_agent.infra.settings import Settings
from ap_agent.orchestration.evals.cases import load_cases
from ap_agent.orchestration.evals.metrics import EvalReport
from ap_agent.orchestration.evals.runner import run_evals
from ap_agent.services.errors import NoEvalReport


class EvalService:
    def __init__(
        self,
        settings: Settings,
        *,
        registry: PolicyRegistry,
        params: PolicyParams,
        cases_dir: Path,
    ) -> None:
        self._settings = settings
        self._registry = registry
        self._params = params
        self._cases_dir = cases_dir

    @property
    def report_path(self) -> Path:
        return Path(self._settings.storage.sqlite_path).parent / "evals" / "latest.json"

    def run(self) -> EvalReport:
        report = run_evals(
            self._settings,
            registry=self._registry,
            params=self._params,
            cases=load_cases(self._cases_dir),
        )
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        self.report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        return report

    def latest(self) -> EvalReport:
        if not self.report_path.exists():
            raise NoEvalReport
        return EvalReport.model_validate(json.loads(self.report_path.read_text(encoding="utf-8")))
