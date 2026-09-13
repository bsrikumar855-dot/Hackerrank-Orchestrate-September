"""Hypothesis C from the diagnostics: horizon inclusivity and per-day
enforcement. Either of these being wrong would be a one-sided optimistic
mechanism (a dropped day 90 hides any expense landing there; enforcing the
minimum only on payment dates ignores dips between them).
"""
from datetime import date, timedelta
from decimal import Decimal

from core.affordability import amount_safe_to_pay, earliest_date_for_full_payment
from core.forecast import FORECAST_DAYS, daily_balances, is_safe
from core.models import CashItem


def _item(day, amount):
    return CashItem(when=day, signed_amount=amount, event_id="e", category="misc",
                    flexibility=None, minimum_allowed_amount=None)


def test_horizon_is_inclusive_on_both_ends():
    start = date(2026, 1, 1)
    trace = daily_balances(Decimal("1000"), [], start, FORECAST_DAYS)
    days = [d for d, _ in trace]
    assert days[0] == start
    assert days[-1] == start + timedelta(days=90)   # day 90 itself is checked
    assert len(set(days)) == 91


def test_expense_landing_exactly_on_day_90_is_counted():
    start = date(2026, 1, 1)
    last_day = start + timedelta(days=90)
    items = [_item(last_day, Decimal("-950"))]
    trace = daily_balances(Decimal("1000"), items, start, FORECAST_DAYS)
    assert not is_safe(trace, Decimal("100"))            # 1000-950=50 < 100
    assert amount_safe_to_pay(trace, Decimal("100"), Decimal("500")) == Decimal("0")


def test_expense_one_day_past_the_horizon_is_not_counted():
    start = date(2026, 1, 1)
    items = [_item(start + timedelta(days=91), Decimal("-950"))]
    trace = daily_balances(Decimal("1000"), items, start, FORECAST_DAYS)
    assert is_safe(trace, Decimal("100"))


def test_minimum_is_enforced_on_every_day_not_only_payment_dates():
    # A one-day breach on day 40 -- nowhere near any payment date, and fully
    # recovered the next day -- must still cap today's safe amount and must
    # still block every later date for a full payment, because the spec
    # requires the minimum to hold throughout the period, not just on the
    # days a payment lands.
    start = date(2026, 1, 1)
    items = [_item(start + timedelta(days=40), Decimal("-950")),   # 1000 -> 50, breaches 100
             _item(start + timedelta(days=41), Decimal("+950"))]   # recovers to 1000
    trace = daily_balances(Decimal("1000"), items, start, FORECAST_DAYS)
    assert min_balance_below(trace, Decimal("100"))
    assert amount_safe_to_pay(trace, Decimal("100"), Decimal("500")) == Decimal("0")
    # every candidate date has the day-40 breach in its "before" set (or is
    # itself under water), so no date can be reported safe
    assert earliest_date_for_full_payment(trace, Decimal("100"), Decimal("500")) is None


def test_dip_that_stays_above_minimum_is_not_a_violation():
    # Control for the test above: the same shape, but the dip bottoms at 150
    # (above the 100 minimum). It caps today's amount at 50 but does NOT poison
    # later dates -- the search correctly resumes once the balance recovers.
    start = date(2026, 1, 1)
    items = [_item(start + timedelta(days=40), Decimal("-850")),
             _item(start + timedelta(days=41), Decimal("+850"))]
    trace = daily_balances(Decimal("1000"), items, start, FORECAST_DAYS)
    assert amount_safe_to_pay(trace, Decimal("100"), Decimal("500")) == Decimal("50")
    assert earliest_date_for_full_payment(trace, Decimal("100"), Decimal("500")) == start + timedelta(days=41)


def min_balance_below(trace, minimum):
    return min(b for _, b in trace) < minimum
