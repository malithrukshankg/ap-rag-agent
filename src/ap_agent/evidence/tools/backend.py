"""SIMULATED backends: JSON files under fixtures/systems (brief §3 permits mocks).

Each file is read once and validated into the fixture's own schema. These fixture models are
private to the evidence plane; tools translate them into the public output schemas, and the
vendor tool is where full account numbers stop.
"""

import json
from datetime import date
from decimal import Decimal
from functools import cached_property
from pathlib import Path
from typing import Any, Literal

from ap_agent.evidence.tools.schemas import (
    Approver,
    GoodsReceipt,
    POLine,
    POTolerance,
    VendorStatus,
)
from ap_agent.kernel.types import Currency, Money, Strict


class FixtureBank(Strict):
    bsb: str
    account_number: str  # FULL number: never leaves this module's callers unmasked
    bank_country: str
    changed_at: str | None


class FixtureVendor(Strict):
    vendor_id: str
    legal_name: str
    status: VendorStatus
    risk_flags: list[str]
    created_at: str
    created_by: str
    updated_at: str
    bank: FixtureBank


class FixturePurchaseOrder(Strict):
    po_ref: str
    vendor_id: str
    currency: Currency
    requester_id: str
    approval_status: Literal["APPROVED", "PENDING", "REJECTED"]
    approved_by: str | None
    tolerance: POTolerance
    permits_conversion: bool
    lines: list[POLine]
    total: Money
    receipts: list[GoodsReceipt]


class FixtureHistory(Strict):
    record_id: str
    vendor_id: str
    invoice_ref: str
    amount: Money
    currency: Currency
    invoice_date: date
    po_ref: str | None
    status: Literal["PAID", "POSTED", "HELD", "REJECTED"]
    paid_at: str | None


class FixtureRate(Strict):
    base: Currency
    quote: Currency
    rate: Decimal
    rate_date: date
    source: str


def _load(path: Path, key: str) -> list[Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data[key]
    if not isinstance(items, list):
        raise ValueError(f"{path}: '{key}' must be a list")
    return items


class FixtureBackend:
    """Read-only access to fixtures/systems/*.json."""

    def __init__(self, fixtures_dir: Path) -> None:
        self._dir = fixtures_dir

    @cached_property
    def vendors(self) -> dict[str, FixtureVendor]:
        rows = _load(self._dir / "vendors.json", "vendors")
        return {v.vendor_id: v for v in (FixtureVendor.model_validate(r) for r in rows)}

    @cached_property
    def purchase_orders(self) -> dict[str, FixturePurchaseOrder]:
        rows = _load(self._dir / "purchase_orders.json", "purchase_orders")
        return {p.po_ref: p for p in (FixturePurchaseOrder.model_validate(r) for r in rows)}

    @cached_property
    def history(self) -> list[FixtureHistory]:
        rows = _load(self._dir / "invoice_history.json", "records")
        return [FixtureHistory.model_validate(r) for r in rows]

    @cached_property
    def fx_rates(self) -> list[FixtureRate]:
        rows = _load(self._dir / "fx_rates.json", "rates")
        return [FixtureRate.model_validate(r) for r in rows]

    @cached_property
    def approvers(self) -> dict[str, Approver]:
        rows = _load(self._dir / "approvers.json", "approvers")
        return {a.approver_id: a for a in (Approver.model_validate(r) for r in rows)}
