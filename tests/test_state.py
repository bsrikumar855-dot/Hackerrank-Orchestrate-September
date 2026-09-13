from datetime import date
from decimal import Decimal

from core.models import Event, Profile
from core.state import (
    UnresolvedAmountError,
    build_user_state,
    detect_recurring_series,
    get_forecast_cash_items,
)


def _profile(**overrides):
    base = dict(
        user_id="user_01", home_currency="ZAR", current_available_balance=Decimal("1000"),
        minimum_balance_to_keep=Decimal("100"), financial_priorities=(), protect_categories=frozenset(),
        reduce_categories=frozenset(), stop_categories=frozenset(),
        payment_methods_user_will_consider=frozenset({"full_payment"}), max_installment_months=None,
    )
    base.update(overrides)
    return Profile(**base)


def _settled(event_id, day, amount, category="groceries", description="Grocer", flexibility="fixed"):
    return Event(
        event_id=event_id, user_id="user_01", event_type="expense", description=description,
        category=category, direction="debit", amount=Decimal(str(amount)), currency="ZAR",
        event_date=day, settlement_date=day, status="settled", linked_event_id=None,
        flexibility=flexibility, minimum_allowed_amount=None,
    )


def test_detects_weekly_recurrence_and_uses_conservative_amount():
    events = [
        _settled("e1", date(2026, 1, 1), 100),
        _settled("e2", date(2026, 1, 8), 120),
        _settled("e3", date(2026, 1, 15), 90),
        _settled("e4", date(2026, 1, 22), 150),
    ]
    series = detect_recurring_series(events)
    assert len(series) == 1
    s = series[0]
    assert s.cadence_days == 7
    assert s.conservative_amount == Decimal("150")  # max of last 3: 120, 90, 150
    assert s.representative_event_id == "e4"


def test_no_recurrence_below_minimum_history():
    events = [_settled("e1", date(2026, 1, 1), 100), _settled("e2", date(2026, 1, 8), 100)]
    assert detect_recurring_series(events) == []


def test_no_recurrence_when_gaps_are_irregular():
    events = [
        _settled("e1", date(2026, 1, 1), 100),
        _settled("e2", date(2026, 1, 3), 100),
        _settled("e3", date(2026, 3, 1), 100),
        _settled("e4", date(2026, 3, 20), 100),
    ]
    assert detect_recurring_series(events) == []


def test_recurring_income_is_projected_using_minimum_of_recent_amounts():
    # A stable salary that steps down once (a pay cut) -- verified against
    # the real dataset as the actual shape confirmed income takes there
    # (never smoothly varying; either flat, or flat-then-flat-at-a-new-level).
    # Projected using the lowest of the last 3 amounts so income is never
    # overestimated (the mirror of using the highest for expenses).
    events = [
        Event(
            event_id=f"e{i}", user_id="user_01", event_type="income", description="Payroll credit",
            category="salary", direction="credit", amount=amt, currency="ZAR",
            event_date=date(2026, i, 15), settlement_date=date(2026, i, 15), status="settled",
            linked_event_id=None, flexibility="fixed", minimum_allowed_amount=None,
        )
        for i, amt in zip(range(1, 5), [Decimal("1000"), Decimal("1000"), Decimal("1000"), Decimal("900")])
    ]
    series = detect_recurring_series(events)
    assert len(series) == 1
    s = series[0]
    assert s.direction == "credit"
    assert s.cadence_days == 30
    assert s.conservative_amount == Decimal("900")  # min of last 3: 1000, 1000, 900
    assert s.representative_event_id == "e4"


def test_credit_series_with_no_repeated_amount_is_not_confirmed_income():
    # Regression test for sample_requests.csv's request_11: user_11's "salary"
    # category holds a stable "Base salary" (23,256,000 every month) AND
    # separate commission descriptions ("Performance commission", "Monthly
    # sales commission") where every single occurrence is a different amount
    # (16.7M, 8.9M, 16.0M, ...). Verified dataset-wide: every credit series
    # where every occurrence is a distinct amount is commission/freelance/gig
    # income (96 of 96 checked); every one with at least one exact repeat is a
    # stable-or-stepped confirmed salary (23 of 23 checked). Commission must
    # not be treated as confirmed recurring income -- each amount differs.
    events = [
        Event(
            event_id=f"c{i}", user_id="user_01", event_type="income", description="Performance commission",
            category="salary", direction="credit", amount=amt, currency="IDR",
            event_date=date(2026, i, 24), settlement_date=date(2026, i, 24), status="settled",
            linked_event_id=None, flexibility="fixed", minimum_allowed_amount=None,
        )
        for i, amt in zip(range(1, 4), [Decimal("16715584.16"), Decimal("8908379.93"), Decimal("15989420")])
    ]
    assert detect_recurring_series(events) == []


def _settled_income(event_id, day, amount, description="Payroll credit"):
    return Event(
        event_id=event_id, user_id="user_01", event_type="income", description=description,
        category="salary", direction="credit", amount=Decimal(str(amount)), currency="ZAR",
        event_date=day, settlement_date=day, status="settled", linked_event_id=None,
        flexibility="fixed", minimum_allowed_amount=None,
    )


def test_monthly_projection_does_not_drift_off_the_calendar_day():
    # Regression test: naively adding a flat 30 days per cycle drifts away
    # from the 15th (Aug15 -> Sep14 -> Oct14 -> Nov13, each real month being
    # 28-31 days). Projection must stay pinned to day-of-month via calendar
    # arithmetic, matching the pattern in sample_requests.csv's request_03.
    dates = [date(2019, 4, 15), date(2019, 5, 15), date(2019, 6, 15), date(2019, 7, 15), date(2019, 8, 15)]
    events = [_settled_income(f"e{i}", d, 4365000) for i, d in enumerate(dates)]
    state = build_user_state("user_01", _profile(), events)
    items = get_forecast_cash_items(state, date(2019, 9, 3), 90, {})
    projected_dates = sorted(i.when for i in items if i.is_projected)
    assert projected_dates == [date(2019, 9, 15), date(2019, 10, 15), date(2019, 11, 15)]


def test_final_payroll_event_ends_recurring_salary_projection():
    # Regression test for sample_requests.csv's request_05: a "Payroll credit"
    # series recurs cleanly for months, then a single "Final employer payroll"
    # event (a different description, so never grouped with the series)
    # marks the end of the job. No further salary should ever be projected
    # after that, even though the group's own history still looks regular.
    dates = [date(2025, 7, 15), date(2025, 8, 15), date(2025, 9, 15)]
    events = [_settled_income(f"e{i}", d, 14740) for i, d in enumerate(dates)]
    events.append(_settled_income("e_final", date(2025, 10, 15), 14740, description="Final employer payroll"))
    state = build_user_state("user_01", _profile(), events)
    series = detect_recurring_series(state.settled_history)
    assert series == []  # the termination marker suppresses the series entirely

    items = get_forecast_cash_items(state, date(2025, 11, 6), 90, {})
    assert all(not i.is_projected for i in items)  # no future salary projected


def test_projected_income_is_a_credit_in_the_forecast():
    events = [
        Event(
            event_id=f"e{i}", user_id="user_01", event_type="income", description="Payroll credit",
            category="salary", direction="credit", amount=Decimal("1000"), currency="ZAR",
            event_date=date(2026, i, 15), settlement_date=date(2026, i, 15), status="settled",
            linked_event_id=None, flexibility="fixed", minimum_allowed_amount=None,
        )
        for i in range(1, 5)
    ]
    state = build_user_state("user_01", _profile(), events)
    items = get_forecast_cash_items(state, date(2026, 4, 16), 30, {})
    projected = [i for i in items if i.is_projected]
    assert len(projected) == 1
    assert projected[0].when == date(2026, 5, 15)
    assert projected[0].signed_amount == Decimal("1000")  # positive: a credit, not a debit


def test_projected_recurrence_appears_in_forecast_window_using_representative_event_id():
    events = [
        _settled("e1", date(2026, 1, 1), 100),
        _settled("e2", date(2026, 1, 8), 100),
        _settled("e3", date(2026, 1, 15), 100),
        _settled("e4", date(2026, 1, 22), 100),
    ]
    state = build_user_state("user_01", _profile(), events)
    items = get_forecast_cash_items(state, date(2026, 1, 23), 14, {})
    projected = [i for i in items if i.is_projected]
    assert len(projected) == 2  # Jan 29 and Feb 5 fall within a 14-day window from Jan 23
    assert all(i.event_id == "e4" for i in projected)
    assert all(i.signed_amount == Decimal("-100") for i in projected)


def test_explicit_confirmed_salary_suppresses_the_duplicate_projection():
    # Regression test: "Next confirmed salary" (an explicit scheduled row)
    # lands on the very date the recurring payroll series projects, for the
    # same amount. Counting both double-counts the paycheck -- measured at 45
    # such collisions across the dataset, biasing every downstream number
    # optimistic. The explicit row wins; the projection is suppressed.
    history = [_settled_income(f"e{i}", date(2026, i, 15), 1000) for i in range(1, 4)]
    confirmed = Event(
        event_id="e_next", user_id="user_01", event_type="income",
        description="Next confirmed salary", category="salary", direction="credit",
        amount=Decimal("1000"), currency="ZAR", event_date=date(2026, 4, 15),
        settlement_date=date(2026, 4, 15), status="scheduled", linked_event_id=None,
        flexibility="fixed", minimum_allowed_amount=None,
    )
    state = build_user_state("user_01", _profile(), history + [confirmed])
    items = get_forecast_cash_items(state, date(2026, 4, 1), 20, {})
    on_that_day = [i for i in items if i.when == date(2026, 4, 15)]
    assert len(on_that_day) == 1
    assert on_that_day[0].is_projected is False  # the real row, not the projection
    assert any("suppressed projected salary" in n for n in state.adjustment_notes)


def test_unrelated_same_category_oneoff_stays_additive():
    # A one-off "Pending fuel authorization" must NOT suppress a recurring
    # transport projection just because they share a category and land close
    # together -- the amounts differ, so they are different economic events.
    history = [
        _settled(f"t{i}", date(2026, 1, 5) + __import__("datetime").timedelta(days=30 * i),
                 500, category="transport", description="Commuter pass")
        for i in range(3)
    ]
    oneoff = Event(
        event_id="t_fuel", user_id="user_01", event_type="expense",
        description="Pending fuel authorization", category="transport", direction="debit",
        amount=Decimal("40"), currency="ZAR", event_date=date(2026, 4, 5),
        settlement_date=date(2026, 4, 5), status="pending", linked_event_id=None,
        flexibility="fixed", minimum_allowed_amount=None,
    )
    state = build_user_state("user_01", _profile(), history + [oneoff])
    items = get_forecast_cash_items(state, date(2026, 4, 1), 20, {})
    amounts = sorted(abs(i.signed_amount) for i in items)
    assert Decimal("40") in amounts and Decimal("500") in amounts  # both counted


def test_unresolved_blank_amount_raises_rather_than_defaulting_to_zero():
    blank = Event(
        event_id="e9", user_id="user_01", event_type="expense", description="Pending charge",
        category="shopping", direction="debit", amount=None, currency="ZAR",
        event_date=date(2026, 2, 1), settlement_date=date(2026, 2, 1), status="pending",
        linked_event_id=None, flexibility="fixed", minimum_allowed_amount=None,
    )
    state = build_user_state("user_01", _profile(), [blank])
    try:
        get_forecast_cash_items(state, date(2026, 1, 25), 30, {})
        assert False, "expected UnresolvedAmountError"
    except UnresolvedAmountError as exc:
        assert exc.event_id == "e9"


def test_pending_credit_excluded_but_scheduled_credit_counted():
    pending_credit = Event(
        event_id="e1", user_id="user_01", event_type="refund", description="Refund",
        category="shopping", direction="credit", amount=Decimal("500"), currency="ZAR",
        event_date=date(2026, 2, 1), settlement_date=date(2026, 2, 1), status="pending",
        linked_event_id=None, flexibility=None, minimum_allowed_amount=None,
    )
    scheduled_credit = Event(
        event_id="e2", user_id="user_01", event_type="income", description="Salary",
        category="salary", direction="credit", amount=Decimal("2000"), currency="ZAR",
        event_date=date(2026, 2, 2), settlement_date=date(2026, 2, 2), status="scheduled",
        linked_event_id=None, flexibility="fixed", minimum_allowed_amount=None,
    )
    state = build_user_state("user_01", _profile(), [pending_credit, scheduled_credit])
    items = get_forecast_cash_items(state, date(2026, 1, 25), 30, {})
    assert len(items) == 1
    assert items[0].event_id == "e2"
    assert items[0].signed_amount == Decimal("2000")


def test_recurring_cash_flow_anchors_on_settlement_not_event_date():
    """A nominal 15th payroll that consistently settles on the 23rd cannot
    fund a recommendation on the 15th. This mirrors the independently found
    request_07 counterexample without relying on its ID or labels.
    """
    events = [
        Event(
            event_id=f"salary_{month}", user_id="user_01", event_type="income",
            description="Payroll credit", category="salary", direction="credit",
            amount=Decimal("1000"), currency="ZAR",
            event_date=date(2026, month, 15), settlement_date=date(2026, month, 23),
            status="settled", linked_event_id=None, flexibility="fixed",
            minimum_allowed_amount=None,
        )
        for month in (1, 2, 3)
    ]
    series = detect_recurring_series(events)
    assert len(series) == 1
    assert series[0].last_historical_date == date(2026, 3, 23)

    state = build_user_state("user_01", _profile(), events)
    items = get_forecast_cash_items(state, date(2026, 4, 1), 40, {})
    assert [(i.when, i.signed_amount) for i in items] == [
        (date(2026, 4, 23), Decimal("1000"))
    ]


def test_pending_debit_is_reserved():
    pending_debit = Event(
        event_id="e1", user_id="user_01", event_type="expense", description="Fuel hold",
        category="transport", direction="debit", amount=Decimal("50"), currency="ZAR",
        event_date=date(2026, 2, 1), settlement_date=date(2026, 2, 1), status="pending",
        linked_event_id=None, flexibility="fixed", minimum_allowed_amount=None,
    )
    state = build_user_state("user_01", _profile(), [pending_debit])
    items = get_forecast_cash_items(state, date(2026, 1, 25), 30, {})
    assert len(items) == 1
    assert items[0].signed_amount == Decimal("-50")
