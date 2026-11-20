"""Confidence, computed in code (RA#9; architecture §8). First match wins:
LOW if any control is INDETERMINATE · MEDIUM if any model claim was dropped or the
assessment was unavailable · HIGH otherwise. A model-emitted number never reaches it."""

from collections.abc import Sequence

from ap_agent.controls.result import ControlResult
from ap_agent.decision.result import Confidence
from ap_agent.kernel.enums import ConfidenceLevel, ControlStatus


def compute_confidence(
    results: Sequence[ControlResult], *, claims_dropped: int, assessment_available: bool
) -> Confidence:
    indeterminate = [r.control_id.value for r in results if r.status is ControlStatus.INDETERMINATE]
    if indeterminate:
        return Confidence(
            level=ConfidenceLevel.LOW,
            basis=[f"control(s) INDETERMINATE: {', '.join(indeterminate)}"],
        )
    basis: list[str] = []
    if not assessment_available:
        basis.append("model assessment unavailable")
    if claims_dropped:
        basis.append(f"{claims_dropped} model claim(s) dropped as unverifiable")
    if basis:
        return Confidence(level=ConfidenceLevel.MEDIUM, basis=basis)
    return Confidence(
        level=ConfidenceLevel.HIGH,
        basis=["every control determinate", "every model claim verified"],
    )
