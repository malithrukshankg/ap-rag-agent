"""Scenario fixtures: real Orchestrators over a temp database, scripted model, fast faults.

A "restart" is simulated by closing one Orchestrator and opening a new one on the same file.
"""

import contextlib
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path

import pytest

from ap_agent.infra.observability.logger import configure_logging
from ap_agent.infra.settings import (
    Settings,
    ToolSettings,
    load_policy_params,
    load_registry,
    load_settings,
)
from ap_agent.orchestration.runtime import Orchestrator
from tests.conftest import ROOT

TODAY = date(2026, 11, 20)
OrchestratorFactory = Callable[..., Orchestrator]


class CrashInjected(BaseException):
    """Simulates the process dying: not an Exception, so nothing in the app catches it."""


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.chdir(ROOT)
    monkeypatch.setenv("AP_STORAGE__SQLITE_PATH", str(tmp_path / "ap.db"))
    monkeypatch.setenv("AP_LOGGING__PATH", str(tmp_path / "ap.jsonl"))
    monkeypatch.setenv("AP_PAYMENT_HMAC_KEY", "scenario-test-key")
    # The stable tier never calls a real model, whatever config/app.yaml says (brief §7).
    monkeypatch.setenv("AP_MODEL__ADAPTER", "scripted")
    monkeypatch.setenv("AP_TRACING__ENABLED", "false")  # and never exports traces
    s = load_settings(ROOT / "config" / "app.yaml")
    assert s.model.adapter == "scripted"
    assert not s.tracing.enabled
    tools = s.tools.model_copy(
        update={
            "backoff_s": 0.0,
            "get_purchase_order": ToolSettings(timeout_s=0.05, max_retries=3),
        }
    )
    configure_logging("INFO", s.logging.path)
    return s.model_copy(update={"tools": tools})


@pytest.fixture
def make_orchestrator(settings: Settings) -> Iterator[OrchestratorFactory]:
    opened: list[Orchestrator] = []
    registry = load_registry(settings.corpus.registry_path)
    params = load_policy_params(settings.policy.params_path)

    def make(s: Settings | None = None, **kwargs: object) -> Orchestrator:
        o = Orchestrator(
            s or settings,
            registry=registry,
            params=params,
            today=lambda: TODAY,
            **kwargs,  # type: ignore[arg-type]
        )
        opened.append(o)
        return o

    yield make
    for o in opened:
        with contextlib.suppress(Exception):
            o.close()
