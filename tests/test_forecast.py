from datetime import date
from decimal import Decimal

from core.forecast import daily_balances, is_safe, min_balance
from core.models import CashItem


def _item(day, amount, category="misc"):
    return CashItem(
        when=day, signed_amount=amount, event_id="e1", category=category,
        flexibility=None, minimum_allowed_amount=None,
    )


def test_flat_balance_when_no_items():
    trace = daily_balances(Decimal("1000"), [], date(2026, 1, 1), 10)
    assert len(trace) == 11
    assert all(b == Decimal("1000") for _, b in trace)


def test_debit_reduces_balance_from_its_date_onward():
    items = [_item(date(2026, 1, 5), Decimal("-200"))]
    trace = daily_balances(Decimal("1000"), items, date(2026, 1, 1), 10)
    by_date = dict(trace)
    assert by_date[date(2026, 1, 4)] == Decimal("1000")
    assert by_date[date(2026, 1, 5)] == Decimal("800")
    assert by_date[date(2026, 1, 10)] == Decimal("800")


def test_same_day_debit_posts_before_credit():
    # No intraday timing is supplied, so the conservative convention is that
    # the day's debits clear before its credits. The day contributes an
    # intraday low as well as an end-of-day balance, and both are checked.
    items = [_item(date(2026, 1, 5), Decimal("-200")), _item(date(2026, 1, 5), Decimal("50"))]
    trace = daily_balances(Decimal("1000"), items, date(2026, 1, 1), 10)
    same_day = [b for d, b in trace if d == date(2026, 1, 5)]
    assert same_day == [Decimal("800"), Decimal("850")]  # intraday low, then end of day


def test_same_day_ordering_exposes_a_dip_netting_would_hide():
    # Netting would show 1000 - 600 + 500 = 900, comfortably above a 500
    # minimum, and call this safe. Debits-first shows the real intraday low of
    # 400, which breaches it.
    items = [_item(date(2026, 1, 5), Decimal("-600")), _item(date(2026, 1, 5), Decimal("500"))]
    trace = daily_balances(Decimal("1000"), items, date(2026, 1, 1), 10)
    assert min_balance(trace) == Decimal("400")
    assert not is_safe(trace, Decimal("500"))


def test_debit_only_and_credit_only_days_have_one_check_point():
    items = [_item(date(2026, 1, 3), Decimal("-100")), _item(date(2026, 1, 4), Decimal("100"))]
    trace = daily_balances(Decimal("1000"), items, date(2026, 1, 1), 10)
    assert len([1 for d, _ in trace if d == date(2026, 1, 3)]) == 1
    assert len([1 for d, _ in trace if d == date(2026, 1, 4)]) == 1


def test_is_safe_detects_a_dip_below_minimum():
    items = [_item(date(2026, 1, 5), Decimal("-950"))]
    trace = daily_balances(Decimal("1000"), items, date(2026, 1, 1), 10)
    assert not is_safe(trace, Decimal("100"))
    assert min_balance(trace) == Decimal("50")


def test_is_safe_true_when_never_below_minimum():
    items = [_item(date(2026, 1, 5), Decimal("-100"))]
    trace = daily_balances(Decimal("1000"), items, date(2026, 1, 1), 10)
    assert is_safe(trace, Decimal("100"))
