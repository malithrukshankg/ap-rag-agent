"""Policy parameters and the literal check (architecture §4.1).

A control may use a parameter only if the parameter's literal string appears in a TRUSTED
chunk of the cited document, section and version that was retrieved in this run. Otherwise
the control is INDETERMINATE. The match yields the control's citation, built by code.

Scope (our reading of "the chunk it retrieved"): any chunk in the run's retrieval set,
not only the control's own query arm. The citation check at TB3 uses the same set.
"""

from collections.abc import Sequence
from decimal import Decimal

from ap_agent.evidence.citation import Citation
from ap_agent.evidence.rag.index import RetrievedChunk
from ap_agent.kernel.enums import Trust
from ap_agent.kernel.types import Strict


class PolicyParam(Strict):
    value: str
    doc_id: str
    section: int
    version: str
    literal: str

    def decimal(self) -> Decimal:
        return Decimal(self.value)

    def describe(self) -> str:
        return f"{self.doc_id} §{self.section} v{self.version}"


class PolicyParams(Strict):
    params: dict[str, PolicyParam]

    def __getitem__(self, key: str) -> PolicyParam:
        return self.params[key]


def verify_literal(param: PolicyParam, retrieved: Sequence[RetrievedChunk]) -> Citation | None:
    for chunk in retrieved:
        if (
            chunk.trust is Trust.TRUSTED
            and chunk.doc_id == param.doc_id
            and chunk.section == param.section
            and chunk.version == param.version
            and param.literal in chunk.text
        ):
            return Citation(
                chunk_id=chunk.chunk_id,
                doc_id=chunk.doc_id,
                version=chunk.version,
                section=chunk.section,
                text_sha256=chunk.text_sha256,
                quote=param.literal,
                trust=chunk.trust,
            )
    return None
