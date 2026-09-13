from datetime import date
from decimal import Decimal

from core.models import PaymentOption
from core.options import completion_date, expand_schedule, within_max_installment_months


def _option(n=3, freq=30, first=date(2026, 1, 10), amount=Decimal("100")):
    return PaymentOption(
        payment_option_id="payment_option_01", request_id="request_01", payment_method="installments",
        payment_amount=amount, number_of_payments=n, first_payment_date=first,
        payment_frequency_days=freq, financing_fee=Decimal("0"), total_payable_amount=amount * n,
    )


def test_expand_schedule_dates_and_amounts():
    schedule = expand_schedule(_option(n=3, freq=30, first=date(2026, 1, 10), amount=Decimal("100")))
    assert schedule == [
        (date(2026, 1, 10), Decimal("100")),
        (date(2026, 2, 9), Decimal("100")),
        (date(2026, 3, 11), Decimal("100")),
    ]


def test_completion_date_is_last_scheduled_payment():
    assert completion_date(_option(n=3, freq=30, first=date(2026, 1, 10))) == date(2026, 3, 11)


def test_within_max_installment_months_respects_none_as_never():
    opt = _option(n=6)
    assert within_max_installment_months(opt, None) is False
    assert within_max_installment_months(opt, 6) is True
    assert within_max_installment_months(opt, 5) is False
