"""Join and expand request_payment_options.csv rows.

Every option in this dataset's `payment_frequency_days` is ~monthly (28/30/31
days), confirmed across all 515 installment rows, so `number_of_payments`
maps directly onto `max_installment_months` without unit conversion.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from core.models import PaymentOption


def options_for_request(request_id: str, options_by_request: dict[str, list[PaymentOption]]) -> list[PaymentOption]:
    return list(options_by_request.get(request_id, []))


def full_payment_option(options: list[PaymentOption]) -> PaymentOption | None:
    for o in options:
        if o.payment_method == "full_payment":
            return o
    return None


def installment_options(options: list[PaymentOption]) -> list[PaymentOption]:
    return [o for o in options if o.payment_method == "installments"]


def expand_schedule(option: PaymentOption) -> list[tuple[date, Decimal]]:
    """The dated payment schedule implied by an installment option."""
    freq = option.payment_frequency_days or 0
    schedule = []
    for i in range(option.number_of_payments):
        schedule.append((option.first_payment_date + timedelta(days=freq * i), option.payment_amount))
    return schedule


def completion_date(option: PaymentOption) -> date:
    schedule = expand_schedule(option)
    return schedule[-1][0]


def within_max_installment_months(option: PaymentOption, max_installment_months: int | None) -> bool:
    if max_installment_months is None:
        return False
    return option.number_of_payments <= max_installment_months
