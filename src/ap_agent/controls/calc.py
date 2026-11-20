"""Decimal arithmetic that records every operation as a Calculation (FIN-POL-002 §5).

The only place a number in a result is produced (§4). Rules (our decision):
- Inputs must be `Decimal` (or `int`). A `float` raises `TypeError`: binary floats cannot
  represent most cent values exactly.
- Each operation computes exactly, then rounds its result once: ROUND_HALF_UP to 2 dp.
  Inputs are recorded unrounded (an FX rate keeps its precision).
- Calculation ids are sequential per Calculator (one Calculator per run), so a replayed run
  produces the same ids.
"""

from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal

from ap_agent.kernel.types import Currency, Strict

CENT = Decimal("0.01")
ROUNDING = "ROUND_HALF_UP to 2 dp"

Number = Decimal | int


class Calculation(Strict):
    """Deterministic arithmetic record: inputs, formula, result, rounding, currency."""

    id: str
    formula: str
    inputs: dict[str, Decimal]
    result: Decimal
    rounding: str
    currency: Currency


def _dec(name: str, value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, Decimal | int):
        raise TypeError(f"{name} must be Decimal or int, got {type(value).__name__}")
    return Decimal(value)


def quantize(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


class Calculator:
    """Collects the Calculation records for one run."""

    def __init__(self, prefix: str = "calc") -> None:
        self._prefix = prefix
        self._records: list[Calculation] = []

    @property
    def records(self) -> list[Calculation]:
        return list(self._records)

    def _record(
        self, formula: str, inputs: Mapping[str, Decimal], exact: Decimal, currency: str
    ) -> Calculation:
        calc = Calculation(
            id=f"{self._prefix}-{len(self._records) + 1:03d}",
            formula=formula,
            inputs=dict(inputs),
            result=quantize(exact),
            rounding=ROUNDING,
            currency=currency,
        )
        self._records.append(calc)
        return calc

    def line_value(self, quantity: Number, unit_price: Number, currency: str) -> Calculation:
        q, p = _dec("quantity", quantity), _dec("unit_price", unit_price)
        return self._record(
            "quantity * unit_price", {"quantity": q, "unit_price": p}, q * p, currency
        )

    def total(self, amounts: Sequence[Number], currency: str) -> Calculation:
        values = [_dec(f"amount_{i}", a) for i, a in enumerate(amounts, start=1)]
        inputs = {f"amount_{i}": v for i, v in enumerate(values, start=1)}
        formula = " + ".join(inputs) if inputs else "0"
        return self._record(formula, inputs, sum(values, Decimal(0)), currency)

    def difference(self, actual: Number, expected: Number, currency: str) -> Calculation:
        """Signed difference: actual - expected."""
        a, e = _dec("actual", actual), _dec("expected", expected)
        return self._record("actual - expected", {"actual": a, "expected": e}, a - e, currency)

    def variance(self, actual: Number, expected: Number, currency: str) -> Calculation:
        """Absolute variance: |actual - expected|."""
        a, e = _dec("actual", actual), _dec("expected", expected)
        return self._record(
            "abs(actual - expected)", {"actual": a, "expected": e}, abs(a - e), currency
        )

    def percent_of(self, base: Number, percent: Number, currency: str) -> Calculation:
        b, p = _dec("base", base), _dec("percent", percent)
        return self._record(
            "base * percent / 100", {"base": b, "percent": p}, b * p / 100, currency
        )

    def tolerance_limit(
        self, po_line_value: Number, absolute_limit: Number, percent_limit: Number, currency: str
    ) -> Calculation:
        """Lower of an absolute limit and a percentage of the PO line value (FIN-POL-002 §2)."""
        v = _dec("po_line_value", po_line_value)
        a = _dec("absolute_limit", absolute_limit)
        p = _dec("percent_limit", percent_limit)
        return self._record(
            "min(absolute_limit, po_line_value * percent_limit / 100)",
            {"po_line_value": v, "absolute_limit": a, "percent_limit": p},
            min(a, v * p / 100),
            currency,
        )

    def convert(
        self, amount: Number, rate: Number, from_currency: str, to_currency: str
    ) -> Calculation:
        """amount (from_currency) * rate = result (to_currency).

        The rate's source and date are recorded by the calling control (FIN-POL-009 §2).
        """
        m, r = _dec("amount", amount), _dec("rate", rate)
        return self._record(
            f"amount[{from_currency}] * rate[{from_currency}->{to_currency}]",
            {"amount": m, "rate": r},
            m * r,
            to_currency,
        )
