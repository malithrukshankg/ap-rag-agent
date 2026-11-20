"""Everything a control may look at. Built by orchestration from tool outcomes; controls
never call tools, read config, or touch storage."""

from collections.abc import Sequence
from dataclasses import dataclass, field

from ap_agent.controls.calc import Calculator
from ap_agent.controls.params import PolicyParams
from ap_agent.evidence.rag.facets import Facets
from ap_agent.evidence.rag.index import RetrievedChunk
from ap_agent.evidence.tools.schemas import (
    FxRate,
    InvoiceHistoryResponse,
    PurchaseOrder,
    VendorRecord,
)
from ap_agent.intake.case import CaseRequest
from ap_agent.kernel.enums import IndicatorCode


@dataclass(frozen=True)
class Evidence[T]:
    """A tool result as controls see it: the value, or why it is missing."""

    source: str  # tool name
    value: T | None
    tool_call_ids: tuple[str, ...] = ()
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.value is not None


@dataclass(frozen=True)
class ControlContext:
    case: CaseRequest
    facets: Facets
    retrieved: Sequence[RetrievedChunk]  # the run's merged retrieval set
    params: PolicyParams
    calc: Calculator
    vendor: Evidence[VendorRecord]
    history: Evidence[InvoiceHistoryResponse]
    purchase_order: Evidence[PurchaseOrder] | None  # None: no PO reference, not called
    fx: Evidence[FxRate] | None  # None: invoice is in base currency, not called
    # Indicators the model found in untrusted text, already citation-verified (P5).
    model_indicators: frozenset[IndicatorCode] = field(default_factory=frozenset)

    def free_text(self) -> list[str]:
        """The case's own untrusted text. The one place controls read Tainted.text."""
        texts = [self.case.notes.text] if self.case.notes else []
        texts.extend(a.text.text for a in self.case.attachments)
        return texts
