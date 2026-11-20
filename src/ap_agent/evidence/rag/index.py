"""BM25 search with the registry-scoped pre-filter (architecture §3.3).

Registry membership decides whether the policy filter applies at all:
- doc_id NOT in policy_registry → always eligible (must still rank), trust = untrusted.
- doc_id IN policy_registry → eligible only if version = pinned_version AND effective-dated
  for the invoice date AND jurisdiction in ('au', 'global'); trust = trusted.
Front-matter `status` is never used: it is self-declared and forgeable.

Free text becomes search terms only: it is reduced to alphanumeric tokens and each token
is double-quoted, so FTS5 syntax in the input (NEAR, *, ^, column filters, AND/NOT, quotes)
is inert. Terms are OR-ed and BM25 ranks the matches.
"""

import re
from datetime import date

from pydantic import Field

from ap_agent.evidence.rag.db import IndexDb
from ap_agent.kernel.enums import Trust
from ap_agent.kernel.types import Strict

JURISDICTIONS = ("au", "global")
MAX_TERMS = 32
_TOKEN = re.compile(r"[A-Za-z0-9]+")

_SQL = f"""
SELECT c.chunk_id, c.doc_id, c.version, c.section, c.heading, c.text, c.text_sha256,
       c.effective_date, c.status,
       COALESCE(r.doc_type, 'unregistered') AS doc_type,
       CASE WHEN r.doc_id IS NULL THEN 'untrusted' ELSE 'trusted' END AS trust,
       -bm25(chunks_fts) AS score
FROM chunks_fts
JOIN chunks c ON c.chunk_id = chunks_fts.chunk_id
LEFT JOIN policy_registry r ON r.doc_id = c.doc_id
WHERE chunks_fts MATCH :terms
  AND ( r.doc_id IS NULL
     OR ( c.version = r.pinned_version
      AND c.effective_date <= :invoice_date
      AND (c.superseded_date IS NULL OR c.superseded_date > :invoice_date)
      AND c.jurisdiction IN ({", ".join(f"'{j}'" for j in JURISDICTIONS)}) ) )
ORDER BY bm25(chunks_fts), c.chunk_id
LIMIT :top_k
"""


class RetrievedChunk(Strict):
    chunk_id: str
    doc_id: str
    version: str
    section: int
    heading: str
    text: str
    text_sha256: str
    effective_date: str | None
    status: str | None  # display only
    doc_type: str
    trust: Trust
    score: float = Field(ge=0)


def search_terms(text: str) -> list[str]:
    """Lowercase alphanumeric tokens, de-duplicated in order, capped at MAX_TERMS."""
    seen: dict[str, None] = {}
    for tok in _TOKEN.findall(text.lower()):
        seen.setdefault(tok, None)
    return list(seen)[:MAX_TERMS]


def to_fts_query(text: str) -> str | None:
    terms = search_terms(text)
    return " OR ".join(f'"{t}"' for t in terms) if terms else None


def filters_applied(invoice_date: date) -> dict[str, object]:
    """What retrievals.filters records for this search."""
    return {
        "as_of": invoice_date.isoformat(),
        "registry_scoped": True,
        "jurisdictions": list(JURISDICTIONS),
    }


def search(db: IndexDb, text: str, invoice_date: date, top_k: int) -> list[RetrievedChunk]:
    terms = to_fts_query(text)
    if terms is None:
        return []
    rows = db.query(
        _SQL, {"terms": terms, "invoice_date": invoice_date.isoformat(), "top_k": top_k}
    )
    return [RetrievedChunk.model_validate(dict(r)) for r in rows]
