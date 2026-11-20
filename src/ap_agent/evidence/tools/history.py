"""check_invoice_history (SIMULATED). Returns the vendor's prior invoices with stable record
ids, status and fingerprints (§3). Deciding exact vs fuzzy duplicate is C2's job, in code."""

import hashlib
import re
from decimal import Decimal

from ap_agent.evidence.tools.backend import FixtureBackend
from ap_agent.evidence.tools.schemas import (
    HistoryRecord,
    InvoiceHistoryRequest,
    InvoiceHistoryResponse,
)

_NON_ALNUM = re.compile(r"[^A-Z0-9]")


def normalize_invoice_ref(ref: str) -> str:
    """Upper-case, punctuation and spaces stripped (FIN-POL-005 §1 fuzzy rule)."""
    return _NON_ALNUM.sub("", ref.upper())


def invoice_fingerprint(vendor_id: str, invoice_ref: str, currency: str, amount: Decimal) -> str:
    """Exact-match key (FIN-POL-005 §1): vendor, normalised number, currency, gross amount."""
    key = f"{vendor_id}|{normalize_invoice_ref(invoice_ref)}|{currency}|{amount:.2f}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


class InvoiceHistoryTool:
    name = "check_invoice_history"
    input_model = InvoiceHistoryRequest
    output_model = InvoiceHistoryResponse

    def __init__(self, backend: FixtureBackend) -> None:
        self._backend = backend

    def __call__(self, args: InvoiceHistoryRequest) -> InvoiceHistoryResponse:
        candidates = [
            HistoryRecord(
                record_id=h.record_id,
                vendor_id=h.vendor_id,
                invoice_ref=h.invoice_ref,
                normalized_ref=normalize_invoice_ref(h.invoice_ref),
                amount=h.amount,
                currency=h.currency,
                invoice_date=h.invoice_date,
                po_ref=h.po_ref,
                status=h.status,
                paid_at=h.paid_at,
                fingerprint=invoice_fingerprint(h.vendor_id, h.invoice_ref, h.currency, h.amount),
            )
            for h in self._backend.history
            if h.vendor_id == args.vendor_id
        ]
        return InvoiceHistoryResponse(
            request_fingerprint=invoice_fingerprint(
                args.vendor_id, args.invoice_ref, args.currency, args.amount
            ),
            candidates=candidates,
        )
