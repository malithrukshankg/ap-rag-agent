"""The ASSESS loop: validate → repair → UNAVAILABLE (architecture §5.1, O6)."""

import json
from typing import Any

import pytest

from ap_agent.assessment.adapters.scripted import ScriptedModelClient
from ap_agent.assessment.assess import AttemptRecord, assess, extract_json, parse_assessment
from ap_agent.assessment.client import ModelCallError, ModelRequest, ModelResponse
from ap_agent.kernel.enums import AssessmentStatus

VALID: dict[str, Any] = {
    "findings": [],
    "inferences": [],
    "unknowns": [],
    "risk_indicators": [],
    "proposed_outcome": "HOLD_FOR_INFORMATION",
}
REQUEST = ModelRequest(system="SYSTEM", user="EVIDENCE", max_tokens=1000)


class Calls:
    def __init__(self, budget: int = 99) -> None:
        self.records: list[AttemptRecord] = []
        self.charges = 0
        self.budget = budget

    def charge(self) -> None:
        if self.charges >= self.budget:
            raise RuntimeError("budget exhausted")
        self.charges += 1

    def record(self, a: AttemptRecord) -> None:
        self.records.append(a)


def _run(client: Any, calls: Calls, max_repairs: int = 2) -> Any:
    return assess(
        client, REQUEST, max_repairs=max_repairs, charge=calls.charge, record=calls.record
    )


def test_valid_first_attempt() -> None:
    calls = Calls()
    out = _run(ScriptedModelClient([VALID]), calls)
    assert out.status is AssessmentStatus.OK and out.attempts == 1
    assert calls.charges == 1 and [r.parse_result for r in calls.records] == ["ok"]


def test_repair_succeeds_on_second_attempt() -> None:
    client = ScriptedModelClient(['{"findings": [', VALID])
    calls = Calls()
    out = _run(client, calls)
    assert out.status is AssessmentStatus.OK and out.attempts == 2
    assert calls.charges == 2
    assert calls.records[0].parse_result.startswith("invalid")
    repair = client.requests[1].user
    assert repair.startswith("EVIDENCE") and "previous reply was rejected" in repair
    assert "not valid JSON" in repair
    assert client.requests[1].system == "SYSTEM"  # system prompt never changes


def test_schema_invalid_json_is_repaired_with_field_errors() -> None:
    bad = {**VALID, "confidence": 0.99, "proposed_outcome": "PAY_NOW"}
    client = ScriptedModelClient([bad, VALID])
    out = _run(client, Calls())
    assert out.status is AssessmentStatus.OK
    assert "confidence" in client.requests[1].user and "proposed_outcome" in client.requests[1].user


def test_three_bad_completions_are_unavailable() -> None:
    calls = Calls()
    out = _run(ScriptedModelClient(["nope", "{}", '{"findings": 1}']), calls)
    assert out.status is AssessmentStatus.UNAVAILABLE and out.assessment is None
    assert out.attempts == 3 and calls.charges == 3  # 1 + 2 repairs (O6)
    assert all(r.parse_result.startswith("invalid") for r in calls.records)


def test_transport_error_retries_same_request() -> None:
    class Flaky:
        def __init__(self) -> None:
            self.requests: list[ModelRequest] = []

        def complete(self, request: ModelRequest) -> ModelResponse:
            self.requests.append(request)
            if len(self.requests) == 1:
                raise ModelCallError("APITimeoutError")
            return ModelResponse(text=json.dumps(VALID), model_id="m")

    client, calls = Flaky(), Calls()
    out = _run(client, calls)
    assert out.status is AssessmentStatus.OK and out.attempts == 2
    assert client.requests[0] == client.requests[1]
    assert calls.records[0].parse_result == "error: APITimeoutError"
    assert calls.records[0].completion_text is None


def test_refusal_or_truncation_is_invalid() -> None:
    class Truncating:
        def complete(self, request: ModelRequest) -> ModelResponse:
            return ModelResponse(text=json.dumps(VALID), model_id="m", stop_reason="max_tokens")

    out = _run(Truncating(), Calls(), max_repairs=0)
    assert out.status is AssessmentStatus.UNAVAILABLE


def test_budget_exhaustion_propagates() -> None:
    with pytest.raises(RuntimeError, match="budget"):
        _run(ScriptedModelClient(["x", "y", "z"]), Calls(budget=1))


def test_every_attempt_records_prompt_hash_and_text() -> None:
    calls = Calls()
    _run(ScriptedModelClient(["bad", VALID]), calls)
    first, second = calls.records
    assert first.prompt_redacted.startswith("[system]\nSYSTEM")
    assert len(first.prompt_sha256) == 64 and first.prompt_sha256 != second.prompt_sha256
    assert first.completion_text == "bad"


def test_json_extraction_tolerates_fences_and_prose() -> None:
    fenced = "```json\n" + json.dumps(VALID) + "\n```"
    assert parse_assessment(fenced)[0] is not None
    assert parse_assessment("Here you go: " + json.dumps(VALID) + " thanks")[0] is not None
    assert extract_json("no json here") == "no json here"
