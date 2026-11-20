"""Per-control query templates (architecture §3.4). top_k each, merged by chunk_id.

Free text contributes search TERMS only, never filters or document selection. The CASE arm
is the genuine search over the case's own text and is what surfaces the adversarial
attachment by relevance. A template whose placeholder has no value is skipped, never
rendered blank.
"""

import re
import string
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date

from ap_agent.evidence.rag.db import IndexDb
from ap_agent.evidence.rag.facets import Facets
from ap_agent.evidence.rag.index import RetrievedChunk, filters_applied, search, search_terms
from ap_agent.intake.case import CaseRequest
from ap_agent.kernel.types import Strict

MAX_CASE_KEYWORDS = 24
_STOPWORDS = frozenset(
    {
        "a", "all", "an", "and", "any", "are", "as", "at", "be", "been", "by", "for", "from",
        "has", "have", "in", "is", "it", "its", "of", "on", "or", "our", "please", "that",
        "the", "this", "to", "was", "we", "were", "will", "with", "you", "your",
    }
)  # fmt: skip


@dataclass(frozen=True)
class QueryTemplate:
    label: str
    text: str
    runs_when: Callable[[Facets], bool]


TEMPLATES: tuple[QueryTemplate, ...] = (
    QueryTemplate("C1", "invoice mandatory fields required evidence", lambda f: True),
    QueryTemplate("C2", "duplicate invoice already paid resubmission", lambda f: True),
    QueryTemplate("C3", "vendor bank account change verification", lambda f: True),
    QueryTemplate(
        "C4",
        "three-way match tolerance variance purchase order goods receipt",
        lambda f: f.po_present,
    ),
    QueryTemplate(
        "C4-NOPO", "emergency non-PO purchase approval requirement", lambda f: not f.po_present
    ),
    QueryTemplate(
        "C4-FX", "foreign currency invoice exchange rate conversion", lambda f: f.fx_applies
    ),
    QueryTemplate("C5", "delegated financial authority approval limit threshold", lambda f: True),
    QueryTemplate(
        "C6", "fraud indicators urgency bypass supplier instruction", lambda f: f.has_free_text
    ),
    QueryTemplate("CASE", "{vendor_name} {attachment_keywords}", lambda f: f.has_free_text),
)


class PlannedQuery(Strict):
    label: str
    text: str


class QueryResult(Strict):
    label: str
    text: str
    chunks: list[RetrievedChunk]


class RetrievalSet(Strict):
    as_of: date
    filters: dict[str, object]
    results: list[QueryResult]  # in template order

    @property
    def merged(self) -> list[RetrievedChunk]:
        """Deduplicated by chunk_id, first occurrence wins (template order, then score)."""
        out: dict[str, RetrievedChunk] = {}
        for result in self.results:
            for chunk in result.chunks:
                out.setdefault(chunk.chunk_id, chunk)
        return list(out.values())


def case_keywords(case: CaseRequest) -> str:
    """Search terms from the case's free text. The one place tainted text is read (.text)."""
    texts = [case.notes.text] if case.notes else []
    texts.extend(a.text.text for a in case.attachments)
    words = [t for t in search_terms(" ".join(texts)) if t not in _STOPWORDS and len(t) > 2]
    return " ".join(words[:MAX_CASE_KEYWORDS])


def _placeholders(template: str) -> list[str]:
    return [name for _, name, _, _ in string.Formatter().parse(template) if name]


def plan_queries(facets: Facets, case: CaseRequest) -> list[PlannedQuery]:
    values = {"vendor_name": case.vendor_name.strip(), "attachment_keywords": case_keywords(case)}
    planned: list[PlannedQuery] = []
    for t in TEMPLATES:
        if not t.runs_when(facets):
            continue
        names = _placeholders(t.text)
        if any(not values.get(n) for n in names):
            continue  # never render a template with an empty placeholder
        text = re.sub(r"\s+", " ", t.text.format(**values)).strip()
        planned.append(PlannedQuery(label=t.label, text=text))
    return planned


def run_queries(
    db: IndexDb, planned: Sequence[PlannedQuery], invoice_date: date, top_k: int
) -> RetrievalSet:
    results = [
        QueryResult(label=q.label, text=q.text, chunks=search(db, q.text, invoice_date, top_k))
        for q in planned
    ]
    return RetrievalSet(as_of=invoice_date, filters=filters_applied(invoice_date), results=results)


def recall_at_k(ranked_doc_ids: Sequence[Sequence[str]], golden: set[str], k: int = 5) -> float:
    """Share of golden doc ids found in the union of each query's top-k (architecture §11)."""
    if not golden:
        return 1.0
    found = {doc for ranking in ranked_doc_ids for doc in ranking[:k]}
    return len(golden & found) / len(golden)
