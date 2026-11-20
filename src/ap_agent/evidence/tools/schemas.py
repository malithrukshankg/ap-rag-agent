"""Explicit input/output schemas for the read tools (brief §3). Unknown fields are rejected."""

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field

from ap_agent.kernel.types import Currency, Money, Sha256Hex, Strict

Quantity = Annotated[Decimal, Field(ge=0, max_digits=15, decimal_places=4)]
VendorStatus = Literal["ACTIVE", "BLOCKED", "DORMANT", "SANCTIONS_REVIEW", "PENDING_VERIFICATION"]


# --- get_vendor_record ----------------------------------------------------------------


class PaymentDetails(Strict):
    """Masked at the fixture layer: a full account number never exists past it (RA#3)."""

    last4: str = Field(pattern=r"^[0-9]{4}$")
    bank_country: str = Field(pattern=r"^[A-Z]{2}$")
    hmac_fingerprint: Sha256Hex
    changed_at: str | None = None  # ISO-8601 timestamp


class VendorRequest(Strict):
    vendor_id: str = Field(min_length=1, max_length=64)


class VendorRecord(Strict):
    vendor_id: str
    legal_name: str
    status: VendorStatus
    risk_flags: list[str]
    payment_details: PaymentDetails
    created_at: str
    created_by: str
    updated_at: str  # last-updated timestamp (§3)


# --- get_purchase_order ---------------------------------------------------------------


class PurchaseOrderRequest(Strict):
    po_ref: str = Field(min_length=1, max_length=64)


class POLine(Strict):
    line_no: int = Field(ge=1)
    description: str
    category: Literal["goods", "services", "freight"]
    quantity: Quantity
    unit_price: Money
    line_value: Money


class GoodsReceipt(Strict):
    receipt_id: str
    line_no: int = Field(ge=1)
    quantity_received: Quantity
    received_at: str
    receipter_id: str


class POTolerance(Strict):
    """PO-level tolerance. Recorded next to the policy tolerance; policy wins (RA#8)."""

    absolute: Money
    percent: Decimal


class PurchaseOrder(Strict):
    po_ref: str
    vendor_id: str
    currency: Currency
    requester_id: str
    approval_status: Literal["APPROVED", "PENDING", "REJECTED"]
    approved_by: str | None
    tolerance: POTolerance
    permits_conversion: bool  # FIN-POL-009 §3
    lines: list[POLine]
    total: Money
    receipts: list[GoodsReceipt]


# --- check_invoice_history ------------------------------------------------------------


class InvoiceHistoryRequest(Strict):
    vendor_id: str = Field(min_length=1, max_length=64)
    invoice_ref: str = Field(min_length=1, max_length=64)
    amount: Money
    currency: Currency


class HistoryRecord(Strict):
    record_id: str  # stable id (§3)
    vendor_id: str
    invoice_ref: str
    normalized_ref: str
    amount: Money
    currency: Currency
    invoice_date: date
    po_ref: str | None
    status: Literal["PAID", "POSTED", "HELD", "REJECTED"]
    paid_at: str | None
    fingerprint: Sha256Hex


class InvoiceHistoryResponse(Strict):
    """Candidate records for the vendor. Exact vs fuzzy matching is decided by C2, in code."""

    request_fingerprint: Sha256Hex
    candidates: list[HistoryRecord]


# --- get_fx_rate ----------------------------------------------------------------------


class FxRateRequest(Strict):
    base: Currency
    quote: Currency
    rate_date: date


class FxRate(Strict):
    base: Currency
    quote: Currency
    rate: Decimal = Field(gt=0)
    rate_date: date
    source: str


# --- approver authority register (SIMULATED; used by the approval gate, RA#6) ----------


class Approver(Strict):
    approver_id: str
    name: str
    role: str
    authority_limit: Money
    currency: Currency
    active: bool
    valid_from: date
    valid_to: date


# --- retrieve_finance_documents -------------------------------------------------------


class RetrieveRequest(Strict):
    label: str = Field(min_length=1, max_length=16)  # C1..C6, C4-FX, C4-NOPO, CASE
    text: str = Field(min_length=1, max_length=2000)
    invoice_date: date
    top_k: int = Field(gt=0, le=50)
