"""Approval grant (architecture §6.1). The only thing that lets a POST through TB4."""

from decimal import Decimal

from ap_agent.kernel.enums import ApprovalVerdict
from ap_agent.kernel.types import Currency, Strict


class ApprovalGrant(Strict):
    """Single-use; minted after the five approval checks pass."""

    grant_id: str
    run_id: str
    decision_fingerprint: str
    verdict: ApprovalVerdict
    approver_id: str
    amount: Decimal
    currency: Currency
