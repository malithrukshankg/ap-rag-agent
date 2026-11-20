"""Decision fingerprint and idempotency key (architecture §6.1, §6.4).

The fingerprint binds an approval to exactly what the approver saw (FIN-POL-003 §5): case
identity, amount, currency, outcome, every control status, and the hashes of the cited
evidence. Any change to those produces a different fingerprint, so an old approval cannot be
replayed against a changed case.
"""

import hashlib
import json
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from ap_agent.controls.result import ControlResult
from ap_agent.intake.case import CaseRequest
from ap_agent.kernel.enums import DecisionCode, RecommendationCode


def canonical_json(value: Any) -> str:
    """Sorted keys, no whitespace, Decimals as strings: the same input always hashes the same."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def decision_fingerprint(
    case: CaseRequest, outcome: RecommendationCode, results: Sequence[ControlResult]
) -> str:
    return sha256_hex(
        canonical_json(
            {
                "case_id": case.case_id,
                "invoice_ref": case.invoice_ref,
                "vendor_id": case.vendor_id,
                "amount": str(case.amount),
                "currency": case.currency,
                "outcome": outcome.value,
                "controls": {r.control_id.value: r.status.value for r in results},
                "evidence": sorted({c.text_sha256 for r in results for c in r.citations}),
            }
        )
    )


def idempotency_key(case_id: str, invoice_ref: str, vendor_id: str, decision: DecisionCode) -> str:
    """Case-scoped (RA#4): a second run of the same case cannot record a second decision."""
    return sha256_hex(f"{case_id}|{invoice_ref}|{vendor_id}|{decision.value}")


def payload_sha256(
    case_id: str, invoice_ref: str, vendor_id: str, decision: DecisionCode, amount: Decimal,
    currency: str,
) -> str:  # fmt: skip
    """Detects the same idempotency key arriving with a different payload (a conflict)."""
    return sha256_hex(
        canonical_json(
            {
                "case_id": case_id,
                "invoice_ref": invoice_ref,
                "vendor_id": vendor_id,
                "decision": decision.value,
                "amount": f"{amount:.2f}",
                "currency": currency,
            }
        )
    )
