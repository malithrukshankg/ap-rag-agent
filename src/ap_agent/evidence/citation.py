"""Citation metadata for a retrieved chunk (architecture §3.6)."""

from pydantic import Field

from ap_agent.kernel.enums import Trust
from ap_agent.kernel.types import Sha256Hex, Strict


class Citation(Strict):
    """Produced by code from the run's retrieval set; never taken from the model as-is."""

    chunk_id: str
    doc_id: str
    version: str
    section: int = Field(ge=0)
    text_sha256: Sha256Hex
    quote: str
    trust: Trust
