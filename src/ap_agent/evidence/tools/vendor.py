"""get_vendor_record (SIMULATED). Returns status, masked payment details, risk flags and the
last-updated timestamp (§3).

Payment details are masked here, at the fixture layer (RA#3): the output carries last4, bank
country, an HMAC-SHA256 fingerprint of the full account and the change timestamp. The full
number never reaches a tool response, a log, a prompt or the database. The fingerprint lets
C3 compare an account supplied elsewhere without ever holding the number.
"""

import hashlib
import hmac
import re

from ap_agent.evidence.tools.backend import FixtureBackend
from ap_agent.evidence.tools.base import PermanentToolError
from ap_agent.evidence.tools.schemas import PaymentDetails, VendorRecord, VendorRequest

_NON_DIGIT = re.compile(r"\D")


def account_fingerprint(key: bytes, bsb: str, account_number: str) -> str:
    """HMAC-SHA256 over the digits of BSB and account, so formatting never changes it."""
    message = f"{_NON_DIGIT.sub('', bsb)}|{_NON_DIGIT.sub('', account_number)}"
    return hmac.new(key, message.encode("ascii"), hashlib.sha256).hexdigest()


class VendorTool:
    name = "get_vendor_record"
    input_model = VendorRequest
    output_model = VendorRecord

    def __init__(self, backend: FixtureBackend, hmac_key: bytes) -> None:
        if not hmac_key:
            raise ValueError("get_vendor_record needs a non-empty HMAC key (AP_PAYMENT_HMAC_KEY)")
        self._backend = backend
        self._key = hmac_key

    def __call__(self, args: VendorRequest) -> VendorRecord:
        vendor = self._backend.vendors.get(args.vendor_id)
        if vendor is None:
            raise PermanentToolError(f"vendor {args.vendor_id} not found")
        digits = _NON_DIGIT.sub("", vendor.bank.account_number)
        return VendorRecord(
            vendor_id=vendor.vendor_id,
            legal_name=vendor.legal_name,
            status=vendor.status,
            risk_flags=list(vendor.risk_flags),
            payment_details=PaymentDetails(
                last4=digits[-4:],
                bank_country=vendor.bank.bank_country,
                hmac_fingerprint=account_fingerprint(
                    self._key, vendor.bank.bsb, vendor.bank.account_number
                ),
                changed_at=vendor.bank.changed_at,
            ),
            created_at=vendor.created_at,
            created_by=vendor.created_by,
            updated_at=vendor.updated_at,
        )
