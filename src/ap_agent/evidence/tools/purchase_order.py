"""get_purchase_order (SIMULATED). Lines, totals, currency, tolerances, approval status and
goods receipts (§3)."""

from ap_agent.evidence.tools.backend import FixtureBackend
from ap_agent.evidence.tools.base import PermanentToolError
from ap_agent.evidence.tools.schemas import PurchaseOrder, PurchaseOrderRequest


class PurchaseOrderTool:
    name = "get_purchase_order"
    input_model = PurchaseOrderRequest
    output_model = PurchaseOrder

    def __init__(self, backend: FixtureBackend) -> None:
        self._backend = backend

    def __call__(self, args: PurchaseOrderRequest) -> PurchaseOrder:
        po = self._backend.purchase_orders.get(args.po_ref)
        if po is None:
            raise PermanentToolError(f"purchase order {args.po_ref} not found")
        return PurchaseOrder.model_validate(po.model_dump())
