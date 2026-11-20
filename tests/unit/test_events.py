import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ap_agent.infra.observability.events import (
    ControlEvaluated,
    RunStarted,
    ToolAttempt,
    all_event_types,
)
from ap_agent.infra.observability.logger import RunEventLogger, configure_logging
from ap_agent.infra.persistence.sqlite import SqliteStore
from ap_agent.kernel.enums import ControlId, ControlStatus, RecommendationCode


def test_event_types_are_unique_and_cover_architecture_list() -> None:
    types = set(all_event_types())
    for expected in (
        "run.started",
        "run.completed",
        "run.failed",
        "node.entered",
        "node.exited",
        "retrieval.performed",
        "tool.attempt",
        "tool.retry",
        "tool.timeout",
        "model.call",
        "model.repair",
        "citation.rejected",
        "control.evaluated",
        "approval.requested",
        "approval.check_failed",
        "approval.granted",
        "decision.recorded",
        "decision.replayed",
        "budget.exhausted",
    ):
        assert expected in types


def test_payload_is_an_allow_list() -> None:
    with pytest.raises(ValidationError):
        RunStarted(case_id="FIN-001", account_number="123456789")  # type: ignore[call-arg]


def test_emit_writes_audit_row_and_json_log_with_run_id(store: SqliteStore, tmp_path: Path) -> None:
    log_path = tmp_path / "logs" / "ap.jsonl"
    configure_logging("INFO", log_path)
    log = RunEventLogger(store.audit, "run-1", clock=lambda: "2026-11-20T00:00:00.000+00:00")

    log.emit(ToolAttempt(tool="get_vendor_record", tool_call_id="tc-1", attempt=1), node="GATHER")
    log.emit(
        ControlEvaluated(
            control_id=ControlId.C1,
            status=ControlStatus.PASS,
            outcome_floor=RecommendationCode.APPROVE_FOR_POSTING,
        ),
        node="RECONCILE",
        duration_ms=3,
    )

    rows = store.audit.list_for_run("run-1")
    assert [r.type for r in rows] == ["tool.attempt", "control.evaluated"]
    assert rows[1].payload == {
        "control_id": "C1",
        "status": "PASS",
        "outcome_floor": "APPROVE_FOR_POSTING",
        "model_indicators": None,  # set only when C6 is re-run in DECIDE
    }
    assert rows[1].duration_ms == 3

    lines = [json.loads(line) for line in log_path.read_text("utf-8").splitlines()]
    assert len(lines) == 2
    assert all(line["run_id"] == "run-1" for line in lines)
    assert lines[0]["event"] == "tool.attempt"
    assert "timestamp" in lines[0]
    assert lines[1]["outcome"] == "ok"
