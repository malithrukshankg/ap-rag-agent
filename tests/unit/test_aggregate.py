"""Aggregation table, O4 model cap, O5 precedence, and confidence (architecture §4.6, §8)."""

import pytest

from ap_agent.controls.aggregate import aggregate, effective_floor
from ap_agent.controls.result import ControlResult
from ap_agent.decision.confidence import compute_confidence
from ap_agent.kernel.enums import (
    ConfidenceLevel,
    ControlId,
    ControlStatus,
    ExceptionCategory,
    RecommendationCode,
)
from tests.unit.control_harness import PRECEDENCE

R = RecommendationCode
S = ControlStatus
E = ExceptionCategory


def mk(
    cid: ControlId,
    status: ControlStatus = S.PASS,
    floor: RecommendationCode = R.APPROVE_FOR_POSTING,
    cat: ExceptionCategory | None = None,
) -> ControlResult:
    return ControlResult(
        control_id=cid, status=status, outcome_floor=floor, exception_category=cat, rule="r"
    )


ALL_PASS = [mk(c) for c in ControlId]


def _with(*overrides: ControlResult) -> list[ControlResult]:
    by_id = {r.control_id: r for r in ALL_PASS}
    by_id.update({r.control_id: r for r in overrides})
    return list(by_id.values())


def test_all_pass_approves_with_no_category() -> None:
    agg = aggregate(ALL_PASS, model_proposal=None, precedence=PRECEDENCE)
    assert agg.outcome is R.APPROVE_FOR_POSTING
    assert agg.exception_category is None and agg.escalation_target is None


def test_not_applicable_does_not_block_approval() -> None:
    results = _with(mk(ControlId.C6, S.NOT_APPLICABLE))
    assert (
        aggregate(results, model_proposal=None, precedence=PRECEDENCE).outcome
        is R.APPROVE_FOR_POSTING
    )


def test_any_indeterminate_yields_at_least_hold() -> None:
    results = _with(mk(ControlId.C4, S.INDETERMINATE, R.HOLD_FOR_INFORMATION, E.MISSING_RECEIPT))
    agg = aggregate(results, model_proposal=R.APPROVE_FOR_POSTING, precedence=PRECEDENCE)
    assert agg.outcome is R.HOLD_FOR_INFORMATION
    assert agg.exception_category is E.MISSING_RECEIPT and agg.escalation_target == "Receipter"


def test_fail_can_never_approve_even_if_floor_is_wrong() -> None:
    bug = mk(ControlId.C2, S.FAIL, R.APPROVE_FOR_POSTING)
    assert effective_floor(bug) is R.HOLD_FOR_INFORMATION


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ([(ControlId.C1, R.HOLD_FOR_INFORMATION, E.MISSING_PO)], R.HOLD_FOR_INFORMATION),
        ([(ControlId.C1, R.REJECT_INVALID, E.OTHER_CONTROL_RISK),
          (ControlId.C4, R.HOLD_FOR_INFORMATION, E.PRICE_VARIANCE)], R.REJECT_INVALID),
        ([(ControlId.C2, R.REJECT_DUPLICATE, E.DUPLICATE_RISK),
          (ControlId.C1, R.REJECT_INVALID, E.OTHER_CONTROL_RISK)], R.REJECT_DUPLICATE),
        ([(ControlId.C2, R.REJECT_DUPLICATE, E.DUPLICATE_RISK),
          (ControlId.C5, R.ESCALATE_CONTROL_REVIEW, E.AUTHORITY_GAP)], R.ESCALATE_CONTROL_REVIEW),
    ],
)  # fmt: skip
def test_highest_floor_wins(
    overrides: list[tuple[ControlId, RecommendationCode, ExceptionCategory]],
    expected: RecommendationCode,
) -> None:
    results = _with(*(mk(cid, S.FAIL, floor, cat) for cid, floor, cat in overrides))
    assert aggregate(results, model_proposal=None, precedence=PRECEDENCE).outcome is expected


def test_o5_precedence_picks_fraud_over_bank_change() -> None:
    results = _with(
        mk(ControlId.C3, S.FAIL, R.ESCALATE_CONTROL_REVIEW, E.BANK_CHANGE),
        mk(ControlId.C6, S.FAIL, R.ESCALATE_CONTROL_REVIEW, E.OTHER_CONTROL_RISK),
    )
    agg = aggregate(results, model_proposal=None, precedence=PRECEDENCE)
    assert agg.exception_category is E.OTHER_CONTROL_RISK
    assert agg.escalation_target == "Financial Crime and Controls"
    assert set(agg.drivers) == {ControlId.C3, ControlId.C6}


def test_o5_bank_change_beats_authority_gap() -> None:
    results = _with(
        mk(ControlId.C5, S.FAIL, R.ESCALATE_CONTROL_REVIEW, E.AUTHORITY_GAP),
        mk(ControlId.C3, S.FAIL, R.ESCALATE_CONTROL_REVIEW, E.BANK_CHANGE),
    )
    agg = aggregate(results, model_proposal=None, precedence=PRECEDENCE)
    assert (agg.exception_category, agg.escalation_target) == (E.BANK_CHANGE, "Vendor Governance")


# --- the model can only tighten (O4) ---------------------------------------------------


@pytest.mark.parametrize("proposal", [R.REJECT_INVALID, R.REJECT_DUPLICATE])
def test_o4_model_reject_is_capped_at_hold(proposal: RecommendationCode) -> None:
    agg = aggregate(ALL_PASS, model_proposal=proposal, precedence=PRECEDENCE)
    assert agg.outcome is R.HOLD_FOR_INFORMATION
    assert agg.model_proposal_capped and agg.model_raised_outcome


def test_model_cannot_loosen_a_control_floor() -> None:
    results = _with(mk(ControlId.C2, S.FAIL, R.REJECT_DUPLICATE, E.DUPLICATE_RISK))
    agg = aggregate(results, model_proposal=R.APPROVE_FOR_POSTING, precedence=PRECEDENCE)
    assert agg.outcome is R.REJECT_DUPLICATE and not agg.model_raised_outcome


def test_model_escalation_alone_raises_with_generic_category() -> None:
    agg = aggregate(ALL_PASS, model_proposal=R.ESCALATE_CONTROL_REVIEW, precedence=PRECEDENCE)
    assert agg.outcome is R.ESCALATE_CONTROL_REVIEW
    assert agg.exception_category is E.OTHER_CONTROL_RISK
    assert agg.escalation_target == "Financial Control"  # not the C6-sourced rule


# --- confidence ------------------------------------------------------------------------


def test_confidence_low_when_any_indeterminate() -> None:
    results = _with(mk(ControlId.C4, S.INDETERMINATE, R.HOLD_FOR_INFORMATION))
    c = compute_confidence(results, claims_dropped=0, assessment_available=True)
    assert c.level is ConfidenceLevel.LOW and "C4" in c.basis[0]


def test_confidence_medium_when_claims_dropped_or_no_assessment() -> None:
    assert (
        compute_confidence(ALL_PASS, claims_dropped=1, assessment_available=True).level
        is ConfidenceLevel.MEDIUM
    )
    assert (
        compute_confidence(ALL_PASS, claims_dropped=0, assessment_available=False).level
        is ConfidenceLevel.MEDIUM
    )


def test_confidence_high_otherwise() -> None:
    c = compute_confidence(ALL_PASS, claims_dropped=0, assessment_available=True)
    assert c.level is ConfidenceLevel.HIGH
