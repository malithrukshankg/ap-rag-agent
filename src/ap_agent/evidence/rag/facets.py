"""Facets: deterministic from the case and config, never from free text (architecture §3.2)."""

from datetime import date

from ap_agent.intake.case import CaseRequest
from ap_agent.kernel.types import Strict


class Facets(Strict):
    entity: str
    base_currency: str
    invoice_date: date
    po_present: bool
    fx_applies: bool
    has_free_text: bool


def derive_facets(case: CaseRequest, *, entity: str, base_currency: str) -> Facets:
    return Facets(
        entity=entity,
        base_currency=base_currency,
        invoice_date=case.invoice_date,
        po_present=case.po_ref is not None,
        fx_applies=case.currency != base_currency,
        has_free_text=case.has_free_text,
    )
