from datetime import date
from decimal import Decimal

from core.models import CashItem, Profile
from core.spending import find_spending_plan


def _profile(**overrides):
    base = dict(
        user_id="user_01", home_currency="ZAR", current_available_balance=Decimal("500"),
        minimum_balance_to_keep=Decimal("100"), financial_priorities=(), protect_categories=frozenset(),
        reduce_categories=frozenset({"entertainment"}), stop_categories=frozenset({"delivery_membership"}),
        payment_methods_user_will_consider=frozenset({"full_payment"}), max_installment_months=None,
    )
    base.update(overrides)
    return Profile(**base)


def _item(event_id, day, amount, category, flexibility, minimum_allowed_amount=None, is_projected=True):
    return CashItem(
        when=day, signed_amount=amount, event_id=event_id, category=category,
        flexibility=flexibility, minimum_allowed_amount=minimum_allowed_amount,
        is_projected=is_projected,
    )


def test_stops_a_flexible_debit_to_reach_safety():
    items = [_item("event_9", date(2026, 1, 5), Decimal("-150"), "delivery_membership", "stoppable")]
    profile = _profile(current_available_balance=Decimal("550"), minimum_balance_to_keep=Decimal("100"))
    # payment alone (day1): 550-400=150, safe. With the day-5 subscription also
    # active: 150-150=0 on day5, unsafe. Stopping it keeps day5 at 150 -> safe.
    result = find_spending_plan(
        Decimal("550"), items, profile, date(2026, 1, 1), 90, date(2026, 1, 1), Decimal("400")
    )
    assert result is not None
    assert [a.formatted for a in result] == ["stop:event_9"]


def test_never_touches_a_protected_category():
    items = [_item("event_9", date(2026, 1, 5), Decimal("-150"), "delivery_membership", "stoppable")]
    profile = _profile(current_available_balance=Decimal("550"), protect_categories=frozenset({"delivery_membership"}))
    result = find_spending_plan(
        Decimal("550"), items, profile, date(2026, 1, 1), 90, date(2026, 1, 1), Decimal("400")
    )
    assert result is None  # only candidate is protected, so nothing can be freed


def test_never_changes_an_explicit_one_off_future_debit():
    items = [_item(
        "event_9", date(2026, 1, 5), Decimal("-150"),
        "delivery_membership", "stoppable", is_projected=False,
    )]
    profile = _profile(current_available_balance=Decimal("550"))
    result = find_spending_plan(
        Decimal("550"), items, profile, date(2026, 1, 1), 90,
        date(2026, 1, 1), Decimal("400"),
    )
    assert result is None


def test_reduce_respects_minimum_allowed_amount():
    items = [_item("event_9", date(2026, 1, 5), Decimal("-200"), "entertainment", "reducible", Decimal("50"))]
    profile = _profile(current_available_balance=Decimal("500"), minimum_balance_to_keep=Decimal("100"))
    # payment alone (day1): 500-350=150, safe. With the day-5 expense also
    # active: 150-200=-50, unsafe. Reducing to the 50 floor frees 150 -> day5
    # lands exactly at the 100 minimum, which is safe (>= is allowed).
    result = find_spending_plan(
        Decimal("500"), items, profile, date(2026, 1, 1), 90, date(2026, 1, 1), Decimal("350")
    )
    assert result is not None
    assert result[0].formatted == "reduce_to:event_9:50"


def test_gives_up_when_no_combination_of_up_to_three_changes_suffices():
    items = [_item("event_9", date(2026, 1, 5), Decimal("-10"), "delivery_membership", "stoppable")]
    profile = _profile(current_available_balance=Decimal("100"), minimum_balance_to_keep=Decimal("100"))
    result = find_spending_plan(
        Decimal("100"), items, profile, date(2026, 1, 1), 90, date(2026, 1, 1), Decimal("5000")
    )
    assert result is None


def test_stop_and_reduce_are_mutually_exclusive_per_event():
    # a reducible_or_stoppable event should appear as exactly one action, never both
    items = [_item("event_9", date(2026, 1, 5), Decimal("-150"), "entertainment", "reducible_or_stoppable", Decimal("0"))]
    profile = _profile(
        current_available_balance=Decimal("550"), minimum_balance_to_keep=Decimal("100"),
        reduce_categories=frozenset({"entertainment"}), stop_categories=frozenset({"entertainment"}),
    )
    result = find_spending_plan(
        Decimal("550"), items, profile, date(2026, 1, 1), 90, date(2026, 1, 1), Decimal("400")
    )
    assert result is not None
    assert len(result) == 1  # not both a stop and a reduce for event_9
