"""Builds what the model may see (RA#3; FIN-POL-010 §2 "only the data necessary").

Everything is built from field allow-lists, never by removing known-sensitive keys:
- case facts: a fixed list of typed fields (no requester identity, no bank details);
- control results: a fixed list of fields (no tool payloads);
- evidence items: retrieved chunks verbatim, plus the case's own free text with any long
  digit run (an account number) masked to its last four digits.

Each item carries a source id, a trust label and the sha256 the citation check (TB3) will
re-verify. Chunk text is passed unmodified so its hash still matches the index.
"""

import hashlib
import re
from collections.abc import Sequence
from typing import Literal

from ap_agent.controls.result import ControlResult
from ap_agent.evidence.rag.index import RetrievedChunk
from ap_agent.intake.case import CaseRequest
from ap_agent.kernel.enums import Trust
from ap_agent.kernel.types import Strict

CASE_FACT_FIELDS = (
    "case_id",
    "invoice_ref",
    "vendor_name",
    "amount",
    "tax_amount",
    "currency",
    "invoice_date",
    "po_ref",
)
CONTROL_FIELDS = (
    "control_id",
    "status",
    "outcome_floor",
    "exception_category",
    "rule",
    "expected",
    "observed",
    "threshold",
)
# A plain run of 6-17 digits, or 3+ space-separated digit groups ("1045 7723 99").
# Dashed forms are left alone so dates and invoice numbers survive.
_ACCOUNT_LIKE = re.compile(r"\b\d{6,17}\b|\b\d{2,6}(?: \d{2,6}){2,}\b")


class EvidenceItem(Strict):
    source_id: str
    kind: Literal["chunk", "case_text"]
    trust: Trust
    label: str
    text: str  # exactly as shown to the model (before markup escaping)
    expected_sha256: str


class EvidencePack(Strict):
    case_facts: dict[str, str]
    controls: list[dict[str, str]]
    items: list[EvidenceItem]

    def item(self, source_id: str) -> EvidenceItem | None:
        return next((i for i in self.items if i.source_id == source_id), None)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def mask_account_numbers(text: str) -> str:
    """Account-like digit runs → ****last4 (FIN-POL-010 §2)."""

    def repl(m: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        return f"****{digits[-4:]}" if len(digits) >= 6 else m.group(0)

    return _ACCOUNT_LIKE.sub(repl, text)


def build_evidence_pack(
    case: CaseRequest, retrieved: Sequence[RetrievedChunk], results: Sequence[ControlResult]
) -> EvidencePack:
    data = case.model_dump(mode="json")
    facts = {k: str(data[k]) for k in CASE_FACT_FIELDS if data.get(k) is not None}

    controls = []
    for r in results:
        dumped = r.model_dump(mode="json")
        controls.append({k: str(dumped[k]) for k in CONTROL_FIELDS if dumped.get(k) is not None})

    items = [
        EvidenceItem(
            source_id=c.chunk_id,
            kind="chunk",
            trust=c.trust,
            label=f"{c.doc_id} v{c.version} §{c.section} ({c.doc_type})",
            text=c.text,
            expected_sha256=c.text_sha256,
        )
        for c in retrieved
    ]
    case_texts: list[tuple[str, str, str]] = []
    if case.notes is not None:
        case_texts.append(("case:notes", "case notes", case.notes.text))
    for n, a in enumerate(case.attachments, start=1):
        case_texts.append((f"case:attachment:{n}", f"case attachment {a.name}", a.text.text))
    for source_id, label, raw in case_texts:
        text = mask_account_numbers(raw)
        items.append(
            EvidenceItem(
                source_id=source_id,
                kind="case_text",
                trust=Trust.UNTRUSTED,
                label=label,
                text=text,
                expected_sha256=_sha(text),
            )
        )
    return EvidencePack(case_facts=facts, controls=controls, items=items)
