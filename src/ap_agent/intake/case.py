"""Processing request (§2.1) validated at TB1. Case text is tainted at construction."""

from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import Field, field_validator

from ap_agent.kernel.types import Currency, Money, Strict

TaintOrigin = Literal["case_note", "attachment", "invoice_line"]


class Tainted(Strict):
    """Untrusted text from the case (§2.1, §4). Nothing promotes it to trusted.

    `str()` deliberately hides the content so tainted text cannot slip into a log line or a
    prompt by accident; callers must read `.text` explicitly at the one place that needs it.
    """

    text: str
    origin: TaintOrigin

    def __str__(self) -> str:
        return f"<tainted {self.origin}: {len(self.text)} chars>"

    __repr__ = __str__


def _taint(value: Any, origin: TaintOrigin) -> Any:
    if isinstance(value, str):
        return Tainted(text=value, origin=origin)
    return value


class InvoiceLine(Strict):
    line_no: int = Field(ge=1)
    description: Tainted
    quantity: Annotated[Decimal, Field(gt=0, max_digits=15, decimal_places=4)]
    unit_price: Money
    line_total: Money
    po_line_no: int | None = None

    @field_validator("description", mode="before")
    @classmethod
    def _taint_description(cls, v: Any) -> Any:
        return _taint(v, "invoice_line")


class Attachment(Strict):
    name: str = Field(min_length=1, max_length=255)
    text: Tainted

    @field_validator("text", mode="before")
    @classmethod
    def _taint_text(cls, v: Any) -> Any:
        return _taint(v, "attachment")


class CaseRequest(Strict):
    """Invoices arrive as structured JSON; PDF extraction is out of scope (architecture §5)."""

    case_id: str = Field(min_length=1, max_length=64)
    invoice_ref: str = Field(min_length=1, max_length=64)
    vendor_id: str = Field(min_length=1, max_length=64)
    vendor_name: str = Field(min_length=1, max_length=255)
    amount: Annotated[Decimal, Field(gt=0, max_digits=15, decimal_places=2)]  # gross
    tax_amount: Money | None = None
    currency: Currency
    invoice_date: date
    po_ref: str | None = None
    lines: list[InvoiceLine] = Field(default_factory=list)
    requested_by: str = Field(min_length=1, max_length=64)
    notes: Tainted | None = None
    attachments: list[Attachment] = Field(default_factory=list)

    @field_validator("notes", mode="before")
    @classmethod
    def _taint_notes(cls, v: Any) -> Any:
        return _taint(v, "case_note")

    @property
    def has_free_text(self) -> bool:
        return self.notes is not None or bool(self.attachments)
