"""Read-tool contracts against fixtures/systems, plus fixture-case consistency."""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import BaseModel, ValidationError

from ap_agent.evidence.tools.backend import FixtureBackend
from ap_agent.evidence.tools.base import PermanentToolError
from ap_agent.evidence.tools.faults import FaultInjector
from ap_agent.evidence.tools.fx import FxRateTool
from ap_agent.evidence.tools.history import InvoiceHistoryTool, normalize_invoice_ref
from ap_agent.evidence.tools.purchase_order import PurchaseOrderTool
from ap_agent.evidence.tools.retrieve import RetrieveTool
from ap_agent.evidence.tools.schemas import (
    FxRateRequest,
    InvoiceHistoryRequest,
    PurchaseOrderRequest,
    RetrieveRequest,
    VendorRequest,
)
from ap_agent.evidence.tools.vendor import VendorTool, account_fingerprint
from ap_agent.infra.observability.logger import RunEventLogger
from ap_agent.infra.persistence.sqlite import SqliteStore
from ap_agent.kernel.enums import FaultKind, Trust
from ap_agent.orchestration.budget import Budget
from ap_agent.orchestration.evals.cases import load_cases
from ap_agent.orchestration.tool_runner import ToolRunner
from tests.conftest import ROOT
from tests.contract.test_tool_runner import _budget, tools_settings

FIXTURES = ROOT / "fixtures" / "systems"
CASES = load_cases(ROOT / "fixtures" / "cases")
KEY = b"test-hmac-key"


@pytest.fixture(scope="module")
def backend() -> FixtureBackend:
    return FixtureBackend(FIXTURES)


# --- get_vendor_record ----------------------------------------------------------------


def test_vendor_record_masks_payment_details(backend: FixtureBackend) -> None:
    rec = VendorTool(backend, KEY)(VendorRequest(vendor_id="V-1001"))
    assert rec.status == "ACTIVE" and rec.updated_at
    assert rec.payment_details.last4 == "7723"
    assert rec.payment_details.hmac_fingerprint == account_fingerprint(KEY, "062-000", "10457723")
    dumped = rec.model_dump_json()
    assert "10457723" not in dumped and "062-000" not in dumped and "062000" not in dumped


def test_fingerprint_ignores_formatting_but_depends_on_key() -> None:
    a = account_fingerprint(KEY, "062-000", "10457723")
    assert a == account_fingerprint(KEY, "062000", "1045 7723")
    assert a != account_fingerprint(b"other-key", "062-000", "10457723")
    assert a != account_fingerprint(KEY, "062-000", "10457724")


def test_vendor_tool_requires_a_key(backend: FixtureBackend) -> None:
    with pytest.raises(ValueError, match="HMAC"):
        VendorTool(backend, b"")


def test_unknown_vendor_is_permanent(backend: FixtureBackend) -> None:
    with pytest.raises(PermanentToolError):
        VendorTool(backend, KEY)(VendorRequest(vendor_id="V-0000"))


# --- get_purchase_order ---------------------------------------------------------------


def test_purchase_order_lines_receipts_and_tolerance(backend: FixtureBackend) -> None:
    po = PurchaseOrderTool(backend)(PurchaseOrderRequest(po_ref="PO-1001"))
    assert po.currency == "AUD" and po.approval_status == "APPROVED"
    assert sum(line.line_value for line in po.lines) == po.total == Decimal("5500.00")
    assert {r.line_no for r in po.receipts} == {1, 2}
    assert po.tolerance.absolute == Decimal("50.00")


def test_fin_004_po_has_no_receipt(backend: FixtureBackend) -> None:
    po = PurchaseOrderTool(backend)(PurchaseOrderRequest(po_ref="PO-4004"))
    assert po.receipts == []


def test_unknown_po_is_permanent(backend: FixtureBackend) -> None:
    with pytest.raises(PermanentToolError):
        PurchaseOrderTool(backend)(PurchaseOrderRequest(po_ref="PO-0000"))


# --- check_invoice_history ------------------------------------------------------------


def test_history_returns_vendor_candidates_with_stable_ids(backend: FixtureBackend) -> None:
    req = InvoiceHistoryRequest(
        vendor_id="V-2002", invoice_ref="INV-2002-0803", amount=Decimal("3300.00"), currency="AUD"
    )
    res = InvoiceHistoryTool(backend)(req)
    assert [c.record_id for c in res.candidates] == ["H-0002"]
    assert res.candidates[0].status == "PAID"
    assert res.candidates[0].fingerprint == res.request_fingerprint  # FIN-002 exact match


def test_history_fingerprint_normalises_ref(backend: FixtureBackend) -> None:
    tool = InvoiceHistoryTool(backend)
    a = tool(InvoiceHistoryRequest(vendor_id="V-2002", invoice_ref="inv 2002/0803",
                                   amount=Decimal("3300.00"), currency="AUD"))  # fmt: skip
    assert a.request_fingerprint == a.candidates[0].fingerprint
    assert normalize_invoice_ref("inv-2002 / 0803") == "INV20020803"


# --- get_fx_rate ----------------------------------------------------------------------


def test_fx_exact_date(backend: FixtureBackend) -> None:
    r = FxRateTool(backend)(FxRateRequest(base="USD", quote="AUD", rate_date=date(2026, 8, 15)))
    assert r.rate == Decimal("1.5234") and "Corporate daily rate" in r.source


def test_fx_missing_date_is_permanent_never_nearest(backend: FixtureBackend) -> None:
    with pytest.raises(PermanentToolError):
        FxRateTool(backend)(FxRateRequest(base="USD", quote="AUD", rate_date=date(2026, 8, 16)))


# --- retrieve_finance_documents -------------------------------------------------------


def test_retrieve_returns_ranked_chunks_with_citation_metadata(indexed_store: SqliteStore) -> None:
    res = RetrieveTool(indexed_store.db)(
        RetrieveRequest(
            label="C3",
            text="vendor bank account change verification",
            invoice_date=date(2026, 8, 20),
            top_k=5,
        )
    )
    assert res.label == "C3" and 0 < len(res.chunks) <= 5
    top = res.chunks[0]
    assert top.doc_id and top.version and top.text_sha256 and top.score > 0
    assert top.trust in (Trust.TRUSTED, Trust.UNTRUSTED)


# --- schemas --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (VendorRequest, {"vendor_id": "V-1001", "include_bank": True}),
        (PurchaseOrderRequest, {"po_ref": ""}),
        (FxRateRequest, {"base": "usd", "quote": "AUD", "rate_date": "2026-08-15"}),
        (RetrieveRequest, {"label": "C1", "text": "x", "invoice_date": "2026-08-15", "top_k": 0}),
    ],
)
def test_tool_inputs_are_strict(model: type[BaseModel], payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(payload)


# --- through the runner with the FIN-004 fault profile ---------------------------------


def test_fin_004_po_timeouts_through_runner(store: SqliteStore, backend: FixtureBackend) -> None:
    fin4 = next(c for c in CASES if c.case.case_id == "FIN-004")
    budget: Budget = _budget()
    runner = ToolRunner(
        run_id="run-fin4",
        settings=tools_settings(),
        budget=budget,
        store=store,
        events=RunEventLogger(store.audit, "run-fin4"),
        faults=FaultInjector(fin4.faults),
        sleep=lambda _s: None,
    )
    po = runner.run("GATHER", PurchaseOrderTool(backend), {"po_ref": fin4.case.po_ref})
    vendor = runner.run("GATHER", VendorTool(backend, KEY), {"vendor_id": fin4.case.vendor_id})
    assert po.error == "timeout" and po.attempts == 4
    assert vendor.ok  # other tools unaffected
    assert budget.used["tool_calls"] == 5


# --- fixture cases --------------------------------------------------------------------


def test_five_cases_load_with_valid_dates() -> None:
    assert [c.case.case_id for c in CASES] == [f"FIN-00{i}" for i in range(1, 6)]
    assert all(c.case.invoice_date >= date(2026, 8, 1) for c in CASES)


def test_case_amounts_are_internally_consistent() -> None:
    for c in CASES:
        lines = sum(line.line_total for line in c.case.lines)
        assert lines + (c.case.tax_amount or 0) == c.case.amount, c.case.case_id
        for line in c.case.lines:
            assert line.quantity * line.unit_price == line.line_total, c.case.case_id


def test_cases_reference_existing_fixture_records(backend: FixtureBackend) -> None:
    for c in CASES:
        assert c.case.vendor_id in backend.vendors
        assert c.case.po_ref in backend.purchase_orders
        assert backend.purchase_orders[c.case.po_ref or ""].vendor_id == c.case.vendor_id


def test_fin_003_claims_an_account_that_mismatches_master(backend: FixtureBackend) -> None:
    fin3 = next(c for c in CASES if c.case.case_id == "FIN-003")
    assert "8842" in fin3.case.attachments[0].text.text
    rec = VendorTool(backend, KEY)(VendorRequest(vendor_id=fin3.case.vendor_id))
    assert rec.payment_details.last4 != "8842"


def test_fin_004_fault_profile_and_expectations() -> None:
    fin4 = next(c for c in CASES if c.case.case_id == "FIN-004")
    assert fin4.faults == {"get_purchase_order": [FaultKind.TIMEOUT] * 4}
    assert not fin4.expected.approval_requested


def test_fault_injector_consumes_per_attempt_then_ok() -> None:
    f = FaultInjector({"t": [FaultKind.TIMEOUT, FaultKind.TRANSIENT]})
    assert [f.next("t"), f.next("t"), f.next("t")] == [
        FaultKind.TIMEOUT, FaultKind.TRANSIENT, FaultKind.OK,
    ]  # fmt: skip
    assert f.next("other") is FaultKind.OK and f.remaining("t") == 0
