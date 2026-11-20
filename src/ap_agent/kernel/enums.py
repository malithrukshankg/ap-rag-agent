"""Closed vocabularies. Corpus codes are spelled exactly as the policy documents spell them."""

from enum import StrEnum


class RecommendationCode(StrEnum):
    """Recommendation vocabulary (FIN-POL-001 §3). A recommendation is not an approval."""

    APPROVE_FOR_POSTING = "APPROVE_FOR_POSTING"
    HOLD_FOR_INFORMATION = "HOLD_FOR_INFORMATION"
    REJECT_DUPLICATE = "REJECT_DUPLICATE"
    REJECT_INVALID = "REJECT_INVALID"
    ESCALATE_CONTROL_REVIEW = "ESCALATE_CONTROL_REVIEW"


class DecisionCode(StrEnum):
    """Argument vocabulary of submit_finance_decision (§3)."""

    POST_FOR_PAYMENT = "POST_FOR_PAYMENT"
    HOLD = "HOLD"
    REJECTED = "REJECTED"


class ExceptionCategory(StrEnum):
    """One primary category per exception (FIN-POL-007 §1)."""

    MISSING_PO = "MISSING_PO"
    MISSING_RECEIPT = "MISSING_RECEIPT"
    PRICE_VARIANCE = "PRICE_VARIANCE"
    QUANTITY_VARIANCE = "QUANTITY_VARIANCE"
    DUPLICATE_RISK = "DUPLICATE_RISK"
    VENDOR_BLOCK = "VENDOR_BLOCK"
    BANK_CHANGE = "BANK_CHANGE"
    AUTHORITY_GAP = "AUTHORITY_GAP"
    TAX_QUERY = "TAX_QUERY"
    OTHER_CONTROL_RISK = "OTHER_CONTROL_RISK"


class ControlId(StrEnum):
    C1 = "C1"  # minimum evidence
    C2 = "C2"  # duplicate
    C3 = "C3"  # vendor and payment details
    C4 = "C4"  # three-way match
    C5 = "C5"  # authority
    C6 = "C6"  # untrusted-content risk


class ControlStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    INDETERMINATE = "INDETERMINATE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class Trust(StrEnum):
    """Assigned by provenance (policy registry membership), never by content."""

    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"


class CitationUse(StrEnum):
    AUTHORITY = "authority"
    RISK_EVIDENCE = "risk_evidence"


class IndicatorCode(StrEnum):
    """Fraud indicators (FIN-POL-005 §3) plus the disable-checks indicator (FIN-POL-005 §4)."""

    NEW_BANK_DETAILS = "NEW_BANK_DETAILS"
    URGENT_OR_SECRET_LANGUAGE = "URGENT_OR_SECRET_LANGUAGE"
    UNUSUAL_DOMAIN = "UNUSUAL_DOMAIN"
    MISMATCHED_VENDOR_NAME = "MISMATCHED_VENDOR_NAME"
    NEW_COUNTRY_PAYMENT = "NEW_COUNTRY_PAYMENT"
    WEEKEND_MANUAL_PAYMENT = "WEEKEND_MANUAL_PAYMENT"
    REPEATED_ROUND_DOLLAR = "REPEATED_ROUND_DOLLAR"
    BYPASS_APPROVAL_REQUEST = "BYPASS_APPROVAL_REQUEST"
    DISABLE_CHECKS_INSTRUCTION = "DISABLE_CHECKS_INSTRUCTION"


class ApprovalVerdict(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class SubmitStatus(StrEnum):
    RECORDED = "RECORDED"
    REPLAYED = "REPLAYED"


class ConfidenceLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class AssessmentStatus(StrEnum):
    OK = "OK"
    UNAVAILABLE = "UNAVAILABLE"


class FaultKind(StrEnum):
    """Per-attempt fault injection entries (architecture §7)."""

    TIMEOUT = "timeout"
    TRANSIENT = "transient"
    OK = "ok"
