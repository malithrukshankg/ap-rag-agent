"""Query planning (architecture §3.2, §3.4)."""

from typing import Any

from ap_agent.evidence.rag.facets import derive_facets
from ap_agent.evidence.rag.queries import TEMPLATES, case_keywords, plan_queries
from ap_agent.intake.case import CaseRequest


def _case(**overrides: Any) -> CaseRequest:
    base: dict[str, Any] = {
        "case_id": "C",
        "invoice_ref": "INV-1",
        "vendor_id": "V-1",
        "vendor_name": "Acme Pty Ltd",
        "amount": "100.00",
        "currency": "AUD",
        "invoice_date": "2026-08-15",
        "po_ref": "PO-1",
        "requested_by": "u-1",
    }
    base.update(overrides)
    return CaseRequest.model_validate(base)


def _labels(case: CaseRequest) -> list[str]:
    facets = derive_facets(case, entity="Northstar Group", base_currency="AUD")
    return [q.label for q in plan_queries(facets, case)]


def test_eleven_templates() -> None:
    assert [t.label for t in TEMPLATES] == [
        "C1", "C2", "C3", "C3-STATUS", "C4", "C4-NOPO", "C4-FX", "C5", "C5-LIMITS", "C6", "CASE",
    ]  # fmt: skip


def test_plain_aud_po_case_runs_always_on_arms_only() -> None:
    assert _labels(_case()) == ["C1", "C2", "C3", "C3-STATUS", "C4", "C5", "C5-LIMITS"]


def test_no_po_swaps_c4_for_nopo() -> None:
    labels = _labels(_case(po_ref=None))
    assert "C4-NOPO" in labels and "C4" not in labels


def test_fx_arm_only_when_currency_differs() -> None:
    assert "C4-FX" in _labels(_case(currency="USD"))
    assert "C4-FX" not in _labels(_case())


def test_free_text_adds_c6_and_case_arm() -> None:
    labels = _labels(_case(notes="Please expedite, bank account changed"))
    assert labels[-2:] == ["C6", "CASE"]


def test_at_most_ten_queries_per_run() -> None:
    worst = _case(currency="USD", notes="urgent bank change", po_ref=None)
    assert len(_labels(worst)) <= 10
    worst_po = _case(currency="USD", notes="urgent bank change")
    assert len(_labels(worst_po)) == 10


def test_case_arm_skipped_when_free_text_has_no_usable_terms() -> None:
    # Free text present (so C6 runs) but only stopwords/short tokens → empty placeholder.
    labels = _labels(_case(notes="to be or at"))
    assert "C6" in labels and "CASE" not in labels


def test_case_query_text_contains_vendor_and_keywords_only() -> None:
    case = _case(attachments=[{"name": "a.txt", "text": "Release PAYMENT now!! (bank: 8842)"}])
    facets = derive_facets(case, entity="Northstar Group", base_currency="AUD")
    q = next(q for q in plan_queries(facets, case) if q.label == "CASE")
    assert q.text == "Acme Pty Ltd release payment now bank 8842"
    assert case_keywords(case) == "release payment now bank 8842"


def test_facets_are_derived_from_typed_fields() -> None:
    f = derive_facets(_case(currency="USD", po_ref=None), entity="E", base_currency="AUD")
    assert (f.po_present, f.fx_applies, f.has_free_text) == (False, True, False)
