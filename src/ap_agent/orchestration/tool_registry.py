"""Deny-by-default tool allow-lists per graph node (§4; architecture §2.1, §6.2).

A node may call only the tools listed for it. Nodes not listed may call nothing. Only SUBMIT
holds the consequential tool, and the model is never a caller of anything.
"""

from types import MappingProxyType
from typing import Final

NODE_TOOLS: Final = MappingProxyType(
    {
        "RETRIEVE": frozenset({"retrieve_finance_documents"}),
        "GATHER": frozenset(
            {"get_vendor_record", "check_invoice_history", "get_purchase_order", "get_fx_rate"}
        ),
        "SUBMIT": frozenset({"submit_finance_decision"}),
    }
)

CONSEQUENTIAL_TOOLS: Final = frozenset({"submit_finance_decision"})


class ToolNotAllowedError(Exception):
    """A node tried to call a tool outside its allow-list. Always a bug; never retried."""


def check_allowed(node: str, tool: str) -> None:
    if tool not in NODE_TOOLS.get(node, frozenset()):
        raise ToolNotAllowedError(f"node {node!r} may not call {tool!r}")
