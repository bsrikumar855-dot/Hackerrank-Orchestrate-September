"""Amendments extracted from messages must reach the forecast, and must be
applied deterministically by core/ (which knows nothing about where they came
from).
"""
from datetime import date
from decimal import Decimal

from core.models import Event, Profile, SeriesAdjustment
from core.state import build_user_state, get_forecast_cash_items


def _profile(**overrides):
    base = dict(
        user_id="user_01", home_currency="ZAR", current_available_balance=Decimal("1000"),
        minimum_balance_to_keep=Decimal("100"), financial_priorities=(), protect_categories=frozenset(),
        reduce_categories=frozenset(), stop_categories=frozenset(),
        payment_methods_user_will_consider=frozenset({"full_payment"}), max_installment_months=None,
    )
    base.update(overrides)
    return Profile(**base)


def _salary(event_id, day, amount=Decimal("1000")):
    return Event(
        event_id=event_id, user_id="user_01", event_type="income", description="Payroll credit",
        category="salary", direction="credit", amount=amount, currency="ZAR",
        event_date=day, settlement_date=day, status="settled", linked_event_id=None,
        flexibility="fixed", minimum_allowed_amount=None,
    )


def _rent(event_id, day, amount=Decimal("500")):
    return Event(
        event_id=event_id, user_id="user_01", event_type="expense", description="Monthly rent",
        category="rent", direction="debit", amount=amount, currency="ZAR",
        event_date=day, settlement_date=day, status="settled", linked_event_id=None,
        flexibility="fixed", minimum_allowed_amount=None,
    )


def _state(events):
    return build_user_state("user_01", _profile(), events)


SALARY_HISTORY = [_salary(f"s{i}", date(2026, i, 15)) for i in range(1, 5)]
RENT_HISTORY = [_rent(f"r{i}", date(2026, i, 2)) for i in range(1, 5)]


def test_absolute_income_amendment_applies_from_its_effective_date():
    state = _state(SALARY_HISTORY)
    adj = [
        SeriesAdjustment(
            category="salary", direction="credit", scope="ongoing",
            new_amount=Decimal("1500"), currency="ZAR",
            effective_date=date(2026, 6, 1), source="message_01",
        )
    ]
    items = get_forecast_cash_items(state, date(2026, 4, 16), 90, {}, adjustments=adj)
    projected = {i.when: i.signed_amount for i in items if i.is_projected}
    assert projected[date(2026, 5, 15)] == Decimal("1000")  # before effective date: unchanged
    assert projected[date(2026, 6, 15)] == Decimal("1500")  # on/after: amended
    assert projected[date(2026, 7, 15)] == Decimal("1500")


def test_percentage_expense_amendment_scales_the_projection():
    state = _state(RENT_HISTORY)
    adj = [
        SeriesAdjustment(
            category="rent", direction="debit", scope="ongoing",
            change_pct=Decimal("12"), effective_date=None, source="message_12",
        )
    ]
    items = get_forecast_cash_items(state, date(2026, 4, 3), 90, {}, adjustments=adj)
    projected = [i.signed_amount for i in items if i.is_projected]
    assert projected  # 500 * 1.12 = 560, as a debit
    assert all(a == Decimal("-560.00") for a in projected)


def test_next_occurrence_only_amendment_touches_just_the_first_occurrence():
    state = _state(SALARY_HISTORY)
    adj = [
        SeriesAdjustment(
            category="salary", direction="credit", scope="next_occurrence_only",
            new_amount=Decimal("400"), currency="ZAR", effective_date=None, source="message_04",
        )
    ]
    items = get_forecast_cash_items(state, date(2026, 4, 16), 90, {}, adjustments=adj)
    projected = sorted(((i.when, i.signed_amount) for i in items if i.is_projected))
    assert projected[0][1] == Decimal("400")  # the temporary reduced pay cycle
    assert all(amt == Decimal("1000") for _, amt in projected[1:])  # then back to normal


def test_amendment_for_a_different_category_is_ignored():
    state = _state(SALARY_HISTORY)
    adj = [
        SeriesAdjustment(
            category="rent", direction="debit", scope="ongoing",
            new_amount=Decimal("9999"), currency="ZAR", effective_date=None, source="message_x",
        )
    ]
    items = get_forecast_cash_items(state, date(2026, 4, 16), 90, {}, adjustments=adj)
    assert all(i.signed_amount == Decimal("1000") for i in items if i.is_projected)


def test_absolute_amendment_in_a_mismatched_currency_is_skipped_not_applied():
    state = _state(SALARY_HISTORY)
    adj = [
        SeriesAdjustment(
            category="salary", direction="credit", scope="ongoing",
            new_amount=Decimal("1500"), currency="USD",  # series is ZAR
            effective_date=None, source="message_09",
        )
    ]
    items = get_forecast_cash_items(state, date(2026, 4, 16), 90, {}, adjustments=adj)
    assert all(i.signed_amount == Decimal("1000") for i in items if i.is_projected)
    assert any("skipped amendment" in n for n in state.adjustment_notes)


def test_zero_amendment_terminates_the_series_in_the_forecast():
    # A seasonal contract that has ended must stop producing projected income
    # entirely -- not produce 0-value entries, and definitely not keep paying.
    state = _state(SALARY_HISTORY)
    adj = [
        SeriesAdjustment(
            category="salary", direction="credit", scope="ongoing",
            new_amount=Decimal("0"), currency="ZAR",
            effective_date=date(2026, 6, 1), source="message_21",
        )
    ]
    items = get_forecast_cash_items(state, date(2026, 4, 16), 90, {}, adjustments=adj)
    projected = sorted(((i.when, i.signed_amount) for i in items if i.is_projected))
    assert projected == [(date(2026, 5, 15), Decimal("1000"))]  # only the pre-termination cycle


def test_no_adjustments_leaves_the_forecast_untouched():
    state = _state(SALARY_HISTORY)
    items = get_forecast_cash_items(state, date(2026, 4, 16), 90, {})
    assert all(i.signed_amount == Decimal("1000") for i in items if i.is_projected)
    assert state.adjustment_notes == []
