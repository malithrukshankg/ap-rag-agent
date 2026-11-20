"""Tool output schemas shared across the evidence plane. Per-tool schemas are added in P3."""

from pydantic import Field

from ap_agent.kernel.types import Sha256Hex, Strict


class PaymentDetails(Strict):
    """Masked at the fixture layer: a full account number never exists past it (RA#3)."""

    last4: str = Field(pattern=r"^[0-9]{4}$")
    bank_country: str = Field(pattern=r"^[A-Z]{2}$")
    hmac_fingerprint: Sha256Hex
    changed_at: str | None = None  # ISO-8601 timestamp
