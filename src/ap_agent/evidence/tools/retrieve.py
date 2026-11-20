"""retrieve_finance_documents (REAL: local SQLite FTS5). One call per planned query.

Returns ranked chunks with document id, type, version/section, relevance score, trust and
citation metadata (§3). Adversarial and irrelevant documents are returned when they rank,
labelled untrusted by registry provenance, never filtered out.
"""

from ap_agent.evidence.rag.db import IndexDb
from ap_agent.evidence.rag.index import search
from ap_agent.evidence.rag.queries import QueryResult
from ap_agent.evidence.tools.schemas import RetrieveRequest


class RetrieveTool:
    name = "retrieve_finance_documents"
    input_model = RetrieveRequest
    output_model = QueryResult

    def __init__(self, db: IndexDb) -> None:
        self._db = db

    def __call__(self, args: RetrieveRequest) -> QueryResult:
        return QueryResult(
            label=args.label,
            text=args.text,
            chunks=search(self._db, args.text, args.invoice_date, args.top_k),
        )
