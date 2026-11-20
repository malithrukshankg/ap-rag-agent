"""The ASSESS loop (architecture §5.1): validate → repair with the errors → UNAVAILABLE.

At most 1 + max_repairs attempts (O6: 2 repairs, so 3 calls). Each attempt first calls
`charge()` (the run budget; may raise and end the run) and afterwards `record()` (the
caller persists a model_calls row and audit events). This module does no I/O itself, so the
assessment plane never touches storage or the budget directly.

Invalid means any of: transport error, refusal, truncation, not JSON, or JSON that fails the
ModelAssessment schema. Invalid output is never silently trusted (§4): it is repaired or the
assessment is UNAVAILABLE, which the caller turns into at least HOLD.
"""

import hashlib
import json
import time
from collections.abc import Callable

from pydantic import ValidationError

from ap_agent.assessment.client import ModelCallError, ModelClient, ModelRequest
from ap_agent.assessment.prompt import build_repair_request, full_prompt_text
from ap_agent.assessment.schema import ModelAssessment
from ap_agent.kernel.enums import AssessmentStatus
from ap_agent.kernel.types import Strict

BAD_STOP_REASONS = {"refusal", "max_tokens"}


class AttemptRecord(Strict):
    attempt: int
    prompt_sha256: str
    prompt_redacted: str
    completion_text: str | None
    parse_result: str  # "ok" | "invalid: ..." | "error: ..."
    error_count: int
    model_id: str | None
    request_id: str | None
    input_tokens: int
    output_tokens: int
    duration_ms: int


class AssessmentOutcome(Strict):
    status: AssessmentStatus
    assessment: ModelAssessment | None
    attempts: int


def extract_json(text: str) -> str:
    """Tolerate a ```json fence or prose around the object; the schema still decides."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else ""
        stripped = stripped.rsplit("```", 1)[0]
    start, end = stripped.find("{"), stripped.rfind("}")
    return stripped[start : end + 1] if start != -1 and end > start else stripped


def parse_assessment(text: str) -> tuple[ModelAssessment | None, list[str]]:
    try:
        data = json.loads(extract_json(text))
    except json.JSONDecodeError as exc:
        return None, [f"not valid JSON: {exc.msg} at position {exc.pos}"]
    try:
        return ModelAssessment.model_validate(data), []
    except ValidationError as exc:
        return None, [
            f"{'.'.join(str(p) for p in e['loc']) or '<root>'}: {e['msg']}"
            for e in exc.errors(include_input=False, include_url=False)
        ]


def assess(
    client: ModelClient,
    request: ModelRequest,
    *,
    max_repairs: int,
    charge: Callable[[], None],
    record: Callable[[AttemptRecord], None],
    clock: Callable[[], float] = time.perf_counter,
) -> AssessmentOutcome:
    current = request
    for attempt in range(1, max_repairs + 2):
        charge()
        prompt = full_prompt_text(current)
        started = clock()
        errors: list[str]
        assessment: ModelAssessment | None = None
        completion: str | None = None
        model_id = request_id = None
        tokens_in = tokens_out = 0
        try:
            response = client.complete(current)
        except ModelCallError as exc:
            errors, parse_result = [f"model call failed: {exc}"], f"error: {exc}"
        else:
            completion, model_id, request_id = response.text, response.model_id, response.request_id
            tokens_in, tokens_out = response.input_tokens, response.output_tokens
            if response.stop_reason in BAD_STOP_REASONS:
                errors = [f"completion stopped early: {response.stop_reason}"]
            else:
                assessment, errors = parse_assessment(response.text)
            parse_result = "ok" if assessment else f"invalid: {len(errors)} error(s)"

        record(
            AttemptRecord(
                attempt=attempt,
                prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                prompt_redacted=prompt,
                completion_text=completion,
                parse_result=parse_result,
                error_count=len(errors),
                model_id=model_id,
                request_id=request_id,
                input_tokens=tokens_in,
                output_tokens=tokens_out,
                duration_ms=int((clock() - started) * 1000),
            )
        )
        if assessment is not None:
            return AssessmentOutcome(
                status=AssessmentStatus.OK, assessment=assessment, attempts=attempt
            )
        if completion is not None:
            current = build_repair_request(request, completion, errors)
        # transport errors retry the same request: there is nothing to repair

    return AssessmentOutcome(
        status=AssessmentStatus.UNAVAILABLE, assessment=None, attempts=max_repairs + 1
    )
