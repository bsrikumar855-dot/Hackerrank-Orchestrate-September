"""Step 2: currency normalization. Exact (date, from_currency, to_currency) lookups only.
Never interpolates, never falls back to a nearby date, never guesses a rate that
is not in exchange_rates.csv.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal


class MissingExchangeRateError(Exception):
    def __init__(self, rate_date: date, from_currency: str, to_currency: str):
        self.rate_date = rate_date
        self.from_currency = from_currency
        self.to_currency = to_currency
        super().__init__(
            f"no exchange rate for {from_currency}->{to_currency} on {rate_date.isoformat()}"
        )


def to_home_currency(
    amount: Decimal,
    from_currency: str,
    on_date: date,
    home_currency: str,
    rates: dict[tuple[date, str, str], Decimal],
) -> Decimal:
    """Convert `amount` in `from_currency`, settled on `on_date`, into `home_currency`.

    Uses the settlement date and stated currency pair exactly as the dataset
    contract specifies. Raises MissingExchangeRateError rather than guessing
    when the exact (date, from, to) row is absent.
    """
    if from_currency == home_currency:
        return amount

    direct = rates.get((on_date, from_currency, home_currency))
    if direct is not None:
        return amount * direct

    raise MissingExchangeRateError(on_date, from_currency, home_currency)
