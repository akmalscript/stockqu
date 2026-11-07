import math

import pytest

from src.utils import formulas as F


def test_avg_daily_sales_uses_180_days():
    assert F.avg_daily_sales(5400) == 30


def test_days_to_stockout():
    assert F.days_to_stockout(100, 25) == 4
    assert math.isinf(F.days_to_stockout(100, 0))


def test_safety_stock_default_3_days():
    assert F.safety_stock(20) == 60


def test_kebutuhan_restock_formula():
    # (30 x (3 + 3)) - 100 = 80
    assert F.kebutuhan_restock(30, 3, 100) == 80
    assert F.restock_qty(30, 3, 100) == 80


def test_restock_never_negative():
    assert F.kebutuhan_restock(10, 3, 500) < 0
    assert F.restock_qty(10, 3, 500) == 0


def test_restock_rounds_up():
    assert F.restock_qty(10.5, 3, 0) == 63


@pytest.mark.parametrize(
    "days, lead, expected",
    [
        (3.3, 3, F.RISK_URGENT),
        (4.0, 3, F.RISK_URGENT),
        (5.0, 3, F.RISK_WARNING),
        (7.0, 3, F.RISK_WARNING),
        (7.1, 3, F.RISK_SAFE),
        (5.0, 6, F.RISK_URGENT),  # habis sebelum kiriman replenishment tiba
        (None, 3, F.RISK_NO_DATA),
    ],
)
def test_classify_risk(days, lead, expected):
    assert F.classify_risk(days, lead) == expected


def test_negative_inputs_rejected():
    with pytest.raises(ValueError):
        F.days_to_stockout(-1, 10)
    with pytest.raises(ValueError):
        F.avg_daily_sales(-5)
