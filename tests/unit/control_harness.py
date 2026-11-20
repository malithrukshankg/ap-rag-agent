"""Builds a ControlContext from the real fixtures, tools and retrieval (no runner, no graph)."""

from typing import Any, Literal

from ap_agent.controls.calc import Calculator
from ap_agent.controls.context import ControlContext, Evidence
from ap_agent.evidence.rag.db import IndexDb
from ap_agent.evidence.rag.facets import derive_facets
from ap_agent.evidence.rag.index import RetrievedChunk
from ap_agent.evidence.rag.queries import plan_queries, run_queries
from ap_agent.evidence.tools.backend import FixtureBackend
from ap_agent.evidence.tools.base import PermanentToolError
from ap_agent.evidence.tools.fx import FxRateTool
from ap_agent.evidence.tools.history import InvoiceHistoryTool
from ap_agent.evidence.tools.purchase_order import PurchaseOrderTool
from ap_agent.evidence.tools.schemas import (
    FxRate,
    FxRateRequest,
    InvoiceHistoryRequest,
    InvoiceHistoryResponse,
    PurchaseOrder,
    PurchaseOrderRequest,
    VendorRecord,
    VendorRequest,
)
from ap_agent.evidence.tools.vendor import VendorTool
from ap_agent.infra.settings import load_policy_params, load_settings
from ap_agent.intake.case import CaseRequest
from ap_agent.kernel.enums import IndicatorCode
from ap_agent.orchestration.evals.cases import load_cases
from tests.conftest import ROOT

PARAMS = load_policy_params(ROOT / "config" / "policy_params.yaml")
PRECEDENCE = load_settings(ROOT / "config" / "app.yaml").escalation_precedence
BACKEND = FixtureBackend(ROOT / "fixtures" / "systems")
CASES = {c.case.case_id: c for c in load_cases(ROOT / "fixtures" / "cases")}
KEY = b"test-hmac-key"
Default = Literal["default"]


def case(case_id: str, **updates: Any) -> CaseRequest:
    data = CASES[case_id].case.model_dump(mode="json")
    data.update(updates)
    return CaseRequest.model_validate(data)


def ok[T](source: str, value: T) -> Evidence[T]:
    return Evidence(source=source, value=value, tool_call_ids=(f"{source}-1",))


def failed(source: str, error: str = "timeout") -> Evidence[Any]:
    return Evidence(source=source, value=None, tool_call_ids=(f"{source}-1",), error=error)


def retrieve(db: IndexDb, c: CaseRequest) -> list[RetrievedChunk]:
    facets = derive_facets(c, entity="Northstar Group", base_currency="AUD")
    return run_queries(db, plan_queries(facets, c), c.invoice_date, top_k=5).merged


def build_ctx(
    db: IndexDb,
    c: CaseRequest,
    *,
    vendor: Evidence[VendorRecord] | Default = "default",
    history: Evidence[InvoiceHistoryResponse] | Default = "default",
    po: Evidence[PurchaseOrder] | Default | None = "default",
    fx: Evidence[FxRate] | Default | None = "default",
    retrieved: list[RetrievedChunk] | None = None,
    model_indicators: frozenset[IndicatorCode] = frozenset(),
) -> ControlContext:
    if vendor == "default":
        vendor = ok(
            "get_vendor_record", VendorTool(BACKEND, KEY)(VendorRequest(vendor_id=c.vendor_id))
        )
    if history == "default":
        req = InvoiceHistoryRequest(
            vendor_id=c.vendor_id, invoice_ref=c.invoice_ref, amount=c.amount, currency=c.currency
        )
        history = ok("check_invoice_history", InvoiceHistoryTool(BACKEND)(req))
    if po == "default":
        if c.po_ref is None:
            po = None
        else:
            try:
                po = ok(
                    "get_purchase_order",
                    PurchaseOrderTool(BACKEND)(PurchaseOrderRequest(po_ref=c.po_ref)),
                )
            except PermanentToolError:
                po = failed("get_purchase_order", "permanent")
    if fx == "default":
        if c.currency == "AUD":
            fx = None
        else:
            try:
                fx = ok(
                    "get_fx_rate",
                    FxRateTool(BACKEND)(
                        FxRateRequest(base=c.currency, quote="AUD", rate_date=c.invoice_date)
                    ),
                )
            except PermanentToolError:
                fx = failed("get_fx_rate", "permanent")
    return ControlContext(
        case=c,
        facets=derive_facets(c, entity="Northstar Group", base_currency="AUD"),
        retrieved=retrieve(db, c) if retrieved is None else retrieved,
        params=PARAMS,
        calc=Calculator(),
        vendor=vendor,
        history=history,
        purchase_order=po,
        fx=fx,
        model_indicators=model_indicators,
    )
