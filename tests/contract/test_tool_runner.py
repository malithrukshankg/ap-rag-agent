"""ToolRunner contract: schemas, deadlines, retry policy, budget, allow-list, persistence."""

import time
from collections.abc import Iterator
from dataclasses import dataclass, field

import pytest
from pydantic import BaseModel, ConfigDict

from ap_agent.evidence.tools.base import PermanentToolError, TransientToolError
from ap_agent.evidence.tools.faults import FaultInjector
from ap_agent.infra.observability.logger import RunEventLogger
from ap_agent.infra.persistence.sqlite import SqliteStore
from ap_agent.infra.settings import BudgetSettings, ToolSettings, ToolsSettings
from ap_agent.kernel.enums import FaultKind
from ap_agent.orchestration.budget import Budget, BudgetExhausted
from ap_agent.orchestration.tool_registry import ToolNotAllowedError
from ap_agent.orchestration.tool_runner import ToolRunner

TIMEOUT_S = 0.05
BACKOFF_S = 0.01


class EchoIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: int


class EchoOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doubled: int


class EchoTool:
    """Scripted behaviour per call: ok | transient | permanent | bad_output | slow | crash."""

    name = "get_vendor_record"  # an allow-listed GATHER tool name
    input_model = EchoIn
    output_model = EchoOut

    def __init__(self, script: list[str] | None = None) -> None:
        self.script = list(script or [])
        self.calls = 0

    def __call__(self, args: EchoIn) -> EchoOut:
        self.calls += 1
        step = self.script.pop(0) if self.script else "ok"
        if step == "transient":
            raise TransientToolError("backend busy")
        if step == "permanent":
            raise PermanentToolError("vendor V-0 not found")
        if step == "crash":
            raise RuntimeError("secret detail that must not be logged")
        if step == "slow":
            time.sleep(TIMEOUT_S * 3)
        if step == "bad_output":
            return EchoOut.model_construct(doubled="not-an-int")  # type: ignore[arg-type]
        return EchoOut(doubled=args.value * 2)


def tools_settings(max_retries: int = 3) -> ToolsSettings:
    t = ToolSettings(timeout_s=TIMEOUT_S, max_retries=max_retries)
    return ToolsSettings(
        fixtures_dir="fixtures/systems",
        backoff_s=BACKOFF_S,
        retrieve_finance_documents=t,
        get_vendor_record=t,
        get_purchase_order=t,
        check_invoice_history=t,
        get_fx_rate=t,
        submit_finance_decision=t,
    )


@dataclass
class Harness:
    store: SqliteStore
    budget: Budget
    sleeps: list[float] = field(default_factory=list)

    def runner(self, *, max_retries: int = 3, faults: FaultInjector | None = None) -> ToolRunner:
        return ToolRunner(
            run_id="run-1",
            settings=tools_settings(max_retries),
            budget=self.budget,
            store=self.store,
            events=RunEventLogger(self.store.audit, "run-1"),
            faults=faults,
            sleep=self.sleeps.append,
        )


def _budget(max_tool_calls: int = 25, max_retries_per_call: int = 3) -> Budget:
    return Budget(
        BudgetSettings(
            max_steps=25,
            max_tool_calls=max_tool_calls,
            max_model_calls=10,
            max_retries_per_call=max_retries_per_call,
        )
    )


@pytest.fixture
def h(store: SqliteStore) -> Iterator[Harness]:
    yield Harness(store=store, budget=_budget())


def _rows(h: Harness) -> list[tuple[int, str]]:
    return [(r.attempt, r.outcome) for r in h.store.tool_calls.list_for_run("run-1")]


# --- happy path and schemas -----------------------------------------------------------


def test_ok_first_attempt_records_row_event_and_budget(h: Harness) -> None:
    out = h.runner().run("GATHER", EchoTool(), {"value": 21})
    assert out.ok and out.output == EchoOut(doubled=42)
    assert out.attempts == 1 and out.tool_call_ids == ("run-1-tc-001",)
    assert _rows(h) == [(1, "ok")]
    row = h.store.tool_calls.list_for_run("run-1")[0]
    assert row.args_redacted == {"value": 21} and row.response_redacted == {"doubled": 42}
    events = h.store.audit.list_for_run("run-1")
    assert [(e.type, e.outcome) for e in events] == [("tool.attempt", "ok")]
    assert events[0].duration_ms is not None
    assert h.budget.used["tool_calls"] == 1


def test_invalid_input_fails_explicitly_without_charging(h: Harness) -> None:
    tool = EchoTool()
    out = h.runner().run("GATHER", tool, {"value": "x", "extra": 1})
    assert not out.ok and out.error == "invalid_input" and out.attempts == 0
    assert tool.calls == 0 and h.budget.used["tool_calls"] == 0
    assert _rows(h) == [(1, "invalid_input")]


def test_invalid_output_fails_without_retry(h: Harness) -> None:
    tool = EchoTool(["bad_output"])
    out = h.runner().run("GATHER", tool, {"value": 1})
    assert out.error == "invalid_output" and out.attempts == 1 and tool.calls == 1


# --- retry policy ---------------------------------------------------------------------


def test_transient_then_ok_retries_with_backoff(h: Harness) -> None:
    tool = EchoTool(["transient", "transient", "ok"])
    out = h.runner().run("GATHER", tool, {"value": 2})
    assert out.ok and out.attempts == 3
    assert _rows(h) == [(1, "transient"), (2, "transient"), (3, "ok")]
    assert h.sleeps == [BACKOFF_S, BACKOFF_S * 2]  # exponential, bounded
    types = [e.type for e in h.store.audit.list_for_run("run-1")]
    assert types.count("tool.retry") == 2


def test_permanent_error_is_not_retried(h: Harness) -> None:
    tool = EchoTool(["permanent"])
    out = h.runner().run("GATHER", tool, {"value": 2})
    assert out.error == "permanent" and out.attempts == 1 and tool.calls == 1
    assert h.sleeps == []
    row = h.store.tool_calls.list_for_run("run-1")[0]
    assert row.error == "PermanentToolError: vendor V-0 not found"


def test_unexpected_exception_is_permanent_and_message_not_recorded(h: Harness) -> None:
    out = h.runner().run("GATHER", EchoTool(["crash"]), {"value": 2})
    assert out.error == "permanent"
    row = h.store.tool_calls.list_for_run("run-1")[0]
    assert row.error == "RuntimeError"  # class name only


def test_real_deadline_times_out_then_exhausts(h: Harness) -> None:
    tool = EchoTool(["slow", "slow", "slow"])
    out = h.runner(max_retries=2).run("GATHER", tool, {"value": 2})
    assert out.error == "timeout" and out.attempts == 3
    assert _rows(h) == [(1, "timeout"), (2, "timeout"), (3, "timeout")]
    types = [e.type for e in h.store.audit.list_for_run("run-1")]
    assert types.count("tool.timeout") == 3


def test_injected_timeouts_exhaust_retries_like_fin_004(h: Harness) -> None:
    faults = FaultInjector({"get_vendor_record": [FaultKind.TIMEOUT] * 4})
    tool = EchoTool()
    started = time.perf_counter()
    out = h.runner(faults=faults).run("GATHER", tool, {"value": 2})
    assert out.error == "timeout" and out.attempts == 4  # 1 + 3 retries
    assert tool.calls == 0  # the backend never answered
    assert time.perf_counter() - started < 2.0  # bounded by deadlines, not fault sleeps
    assert h.budget.used["tool_calls"] == 4


def test_injected_transient_then_recovers(h: Harness) -> None:
    faults = FaultInjector({"get_vendor_record": [FaultKind.TRANSIENT, FaultKind.OK]})
    out = h.runner(faults=faults).run("GATHER", EchoTool(), {"value": 3})
    assert out.ok and out.attempts == 2


def test_retries_capped_by_budget_max_retries_per_call(store: SqliteStore) -> None:
    h = Harness(store=store, budget=_budget(max_retries_per_call=1))
    tool = EchoTool(["transient"] * 5)
    out = h.runner(max_retries=5).run("GATHER", tool, {"value": 1})
    assert out.attempts == 2 and out.error == "transient"


# --- budget and allow-list ------------------------------------------------------------


def test_budget_exhaustion_raises_and_is_audited(store: SqliteStore) -> None:
    h = Harness(store=store, budget=_budget(max_tool_calls=2))
    with pytest.raises(BudgetExhausted) as exc:
        h.runner().run("GATHER", EchoTool(["transient"] * 5), {"value": 1})
    assert exc.value.counter == "tool_calls" and exc.value.limit == 2
    assert len(_rows(h)) == 2
    assert h.store.audit.list_for_run("run-1")[-1].type == "budget.exhausted"


@pytest.mark.parametrize(
    ("node", "tool_name"),
    [
        ("GATHER", "submit_finance_decision"),
        ("RETRIEVE", "get_vendor_record"),
        ("ASSESS", "retrieve_finance_documents"),
        ("UNKNOWN", "get_vendor_record"),
    ],
)
def test_allow_list_denies_by_default(h: Harness, node: str, tool_name: str) -> None:
    tool = EchoTool()
    tool.name = tool_name
    with pytest.raises(ToolNotAllowedError):
        h.runner().run(node, tool, {"value": 1})
    assert tool.calls == 0 and h.budget.used["tool_calls"] == 0
