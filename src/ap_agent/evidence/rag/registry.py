"""Policy registry model (architecture §3.3). Authoritative over front-matter `status`.

doc_id listed     → eligible only at its pinned version, effective-dated → trust=trusted.
doc_id not listed → always eligible, trust=untrusted, citable only as risk evidence.
Loaded from config/policy_registry.yaml by infra/settings.py.
"""

import hashlib
import json

from ap_agent.kernel.types import Strict


class RegistryEntry(Strict):
    pinned_version: str | None  # None: never eligible (O2)
    doc_type: str
    superseded_by: str | None = None


class PolicyRegistry(Strict):
    documents: dict[str, RegistryEntry]

    def content_hash(self) -> str:
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
