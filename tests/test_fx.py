from datetime import date
from decimal import Decimal

import pytest

from core.fx import MissingExchangeRateError, to_home_currency


def test_same_currency_is_identity():
    assert to_home_currency(Decimal("100"), "ZAR", date(2024, 1, 1), "ZAR", {}) == Decimal("100")


def test_exact_match_converts():
    rates = {(date(2024, 1, 15), "USD", "INR"): Decimal("83")}
    result = to_home_currency(Decimal("10"), "USD", date(2024, 1, 15), "INR", rates)
    assert result == Decimal("830")


def test_missing_rate_raises_rather_than_guessing():
    rates = {(date(2024, 1, 15), "USD", "INR"): Decimal("83")}
    with pytest.raises(MissingExchangeRateError):
        to_home_currency(Decimal("10"), "USD", date(2024, 1, 16), "INR", rates)  # one day off


def test_does_not_invert_a_same_date_reverse_rate():
    # Only EUR->ZAR is given; USD-home users needing ZAR->EUR must not get an
    # invented inverse — exact (date, from, to) match only.
    rates = {(date(2024, 1, 15), "EUR", "ZAR"): Decimal("20")}
    with pytest.raises(MissingExchangeRateError):
        to_home_currency(Decimal("100"), "ZAR", date(2024, 1, 15), "EUR", rates)
