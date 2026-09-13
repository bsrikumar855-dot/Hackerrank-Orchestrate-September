from datetime import date
from decimal import Decimal

from core.affordability import amount_safe_to_pay, earliest_date_for_full_payment
from core.forecast import daily_balances
from core.models import CashItem


def _item(day, amount):
    return CashItem(when=day, signed_amount=amount, event_id="e1", category="misc",
                     flexibility=None, minimum_allowed_amount=None)


def test_amount_safe_to_pay_is_capped_by_requested_amount():
    trace = daily_balances(Decimal("10000"), [], date(2026, 1, 1), 90)
    amt = amount_safe_to_pay(trace, Decimal("1000"), Decimal("500"))
    assert amt == Decimal("500")  # plenty of headroom, capped at requested_amount


def test_amount_safe_to_pay_respects_minimum_balance():
    # balance 1000, minimum 800 -> only 200 headroom regardless of a bigger ask
    trace = daily_balances(Decimal("1000"), [], date(2026, 1, 1), 90)
    amt = amount_safe_to_pay(trace, Decimal("800"), Decimal("5000"))
    assert amt == Decimal("200")


def test_amount_safe_to_pay_accounts_for_a_future_dip():
    # a big debit lands on day 30; today's safe amount must respect that dip too
    items = [_item(date(2026, 1, 31), Decimal("-700"))]
    trace = daily_balances(Decimal("1000"), items, date(2026, 1, 1), 90)
    amt = amount_safe_to_pay(trace, Decimal("100"), Decimal("5000"))
    # after the dip: 1000-700=300, minus minimum 100 -> only 200 safe today
    assert amt == Decimal("200")


def test_amount_safe_to_pay_never_negative():
    items = [_item(date(2026, 1, 1), Decimal("-2000"))]
    trace = daily_balances(Decimal("1000"), items, date(2026, 1, 1), 90)
    amt = amount_safe_to_pay(trace, Decimal("100"), Decimal("500"))
    assert amt == Decimal("0")


def test_earliest_date_for_full_payment_is_request_date_when_already_safe():
    trace = daily_balances(Decimal("10000"), [], date(2026, 1, 1), 90)
    d = earliest_date_for_full_payment(trace, Decimal("1000"), Decimal("500"))
    assert d == date(2026, 1, 1)


def test_earliest_date_for_full_payment_waits_for_a_future_credit():
    # not enough today, but a salary credit on day 15 makes it safe from then on.
    # A recommended payment is treated as funded by that same day's own income
    # (the natural reading of "wait until payday, then pay") -- verified
    # against sample_requests.csv's request_04, whose ground-truth `wait` date
    # is the exact day its salary lands, not the day after. A same-day-debit-
    # before-credit convention was tried here and rejected on this evidence;
    # see EXPERIMENTS.md.
    items = [_item(date(2026, 1, 15), Decimal("2000"))]
    trace = daily_balances(Decimal("1000"), items, date(2026, 1, 1), 90)
    d = earliest_date_for_full_payment(trace, Decimal("500"), Decimal("2000"))
    assert d == date(2026, 1, 15)


def test_earliest_date_for_full_payment_none_when_never_safe():
    trace = daily_balances(Decimal("100"), [], date(2026, 1, 1), 90)
    d = earliest_date_for_full_payment(trace, Decimal("50"), Decimal("100000"))
    assert d is None


def test_earliest_date_cannot_be_before_a_preexisting_baseline_violation():
    # baseline dips below minimum on day 5 (unrelated to this request), then
    # fully recovers on day 6 via a big credit. A day-6+ payment would look
    # individually safe in isolation, but the whole 90-day window must never
    # dip below minimum -- and it already did, on day 5 -- so no date is safe.
    items = [
        _item(date(2026, 1, 5), Decimal("-2000")),  # 2000 -> 0, violates minimum 100
        _item(date(2026, 1, 6), Decimal("5000")),  # 0 -> 5000, recovers
    ]
    trace = daily_balances(Decimal("2000"), items, date(2026, 1, 1), 20)
    d = earliest_date_for_full_payment(trace, Decimal("100"), Decimal("10"))
    assert d is None
