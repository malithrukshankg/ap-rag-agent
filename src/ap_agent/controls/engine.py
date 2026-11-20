"""Runs every control on every invoice (architecture §4.5). The list is fixed; nothing in
the case, the documents or the model can skip a control."""

from collections.abc import Callable

from ap_agent.controls import (
    c1_evidence,
    c2_duplicate,
    c3_vendor,
    c4_match,
    c5_authority,
    c6_risk,
)
from ap_agent.controls.context import ControlContext
from ap_agent.controls.result import ControlResult

CONTROLS: tuple[Callable[[ControlContext], ControlResult], ...] = (
    c1_evidence.check,
    c2_duplicate.check,
    c3_vendor.check,
    c4_match.check,
    c5_authority.check,
    c6_risk.check,
)


def run_controls(ctx: ControlContext) -> list[ControlResult]:
    return [control(ctx) for control in CONTROLS]
