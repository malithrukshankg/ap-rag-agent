"""get_fx_rate (SIMULATED, RA#7). The corporate daily rate for an exact date (FIN-POL-009 §2).

No nearest-date fallback: "rates must not be invented", so a missing date is a permanent
failure and the dependent controls become INDETERMINATE.
"""

from ap_agent.evidence.tools.backend import FixtureBackend
from ap_agent.evidence.tools.base import PermanentToolError
from ap_agent.evidence.tools.schemas import FxRate, FxRateRequest


class FxRateTool:
    name = "get_fx_rate"
    input_model = FxRateRequest
    output_model = FxRate

    def __init__(self, backend: FixtureBackend) -> None:
        self._backend = backend

    def __call__(self, args: FxRateRequest) -> FxRate:
        for r in self._backend.fx_rates:
            if (r.base, r.quote, r.rate_date) == (args.base, args.quote, args.rate_date):
                return FxRate.model_validate(r.model_dump())
        raise PermanentToolError(
            f"no {args.base}->{args.quote} rate for {args.rate_date.isoformat()}"
        )
