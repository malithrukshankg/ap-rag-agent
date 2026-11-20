from decimal import Decimal

import pytest

from ap_agent.controls.calc import ROUNDING, Calculator, quantize

D = Decimal


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0.005", "0.01"),  # half rounds up, not to even
        ("0.015", "0.02"),
        ("0.025", "0.03"),  # banker's rounding would give 0.02
        ("2.675", "2.68"),  # exact as a Decimal string (float 2.675 would give 2.67)
        ("-0.005", "-0.01"),  # half away from zero
        ("10.004", "10.00"),
        ("10", "10.00"),
    ],
)
def test_round_half_up_2dp(raw: str, expected: str) -> None:
    assert quantize(D(raw)) == D(expected)
    assert str(quantize(D(raw))) == expected


def test_float_inputs_rejected() -> None:
    calc = Calculator()
    with pytest.raises(TypeError, match="Decimal or int"):
        calc.difference(100.10, D("100.00"), "AUD")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        calc.total([D("1.00"), True], "AUD")  # bool is an int subclass; still rejected


def test_records_inputs_formula_result_rounding_currency() -> None:
    calc = Calculator()
    rec = calc.line_value(D("3"), D("33.335"), "AUD")
    assert rec.id == "calc-001"
    assert rec.formula == "quantity * unit_price"
    assert rec.inputs == {"quantity": D("3"), "unit_price": D("33.335")}
    assert rec.result == D("100.01")  # 100.005 rounded half up
    assert rec.rounding == ROUNDING
    assert rec.currency == "AUD"


def test_ids_are_sequential_and_records_kept() -> None:
    calc = Calculator()
    a = calc.total([D("10.00"), D("5.50")], "AUD")
    b = calc.variance(D("15.50"), D("15.00"), "AUD")
    assert [a.id, b.id] == ["calc-001", "calc-002"]
    assert [r.id for r in calc.records] == ["calc-001", "calc-002"]
    assert a.result == D("15.50")
    assert a.formula == "amount_1 + amount_2"


def test_difference_is_signed_variance_is_absolute() -> None:
    calc = Calculator()
    assert calc.difference(D("95.00"), D("100.00"), "AUD").result == D("-5.00")
    assert calc.variance(D("95.00"), D("100.00"), "AUD").result == D("5.00")


@pytest.mark.parametrize(
    ("po_line_value", "limit"),
    [
        ("1000.00", "10.00"),  # 1% = 10 < 50 → 10
        ("5000.00", "50.00"),  # 1% = 50 = 50 → 50
        ("10000.00", "50.00"),  # 1% = 100 > 50 → 50
        ("1234.56", "12.35"),  # 12.3456 → 12.35
    ],
)
def test_goods_tolerance_is_lower_of_aud50_and_1pct(po_line_value: str, limit: str) -> None:
    # FIN-POL-002 §2: lower of AUD 50 absolute variance or 1% of the PO line value.
    rec = Calculator().tolerance_limit(D(po_line_value), D("50"), D("1"), "AUD")
    assert rec.result == D(limit)
    assert rec.formula.startswith("min(")


def test_variance_within_and_outside_tolerance() -> None:
    calc = Calculator()
    limit = calc.tolerance_limit(D("2000.00"), D("50"), D("1"), "AUD").result  # 20.00
    assert calc.variance(D("2019.99"), D("2000.00"), "AUD").result <= limit
    assert calc.variance(D("2020.01"), D("2000.00"), "AUD").result > limit


def test_percent_of_and_fx_conversion() -> None:
    calc = Calculator()
    assert calc.percent_of(D("200.00"), D("2"), "AUD").result == D("4.00")
    fx = calc.convert(D("1000.00"), D("1.523456"), "USD", "AUD")
    assert fx.result == D("1523.46")
    assert fx.inputs["rate"] == D("1.523456")  # rate kept unrounded
    assert fx.currency == "AUD"


def test_calculation_json_keeps_decimals_exact() -> None:
    rec = Calculator().total([D("0.10"), D("0.20")], "AUD")
    dumped = rec.model_dump(mode="json")
    assert dumped["result"] == "0.30"
    assert type(rec).model_validate(dumped) == rec
