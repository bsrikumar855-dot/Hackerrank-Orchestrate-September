"""Synthetic edge-case robustness pass (does not touch the frozen forecast
parameters at all). These stress-test the shipped pipeline against inputs
the 25 solved samples may not exercise, checking invariants (schema
conformance, bounds, no crash) rather than accuracy against a ground truth
that doesn't exist for synthetic cases.
"""
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from core.affordability import amount_safe_to_pay
from core.forecast import daily_balances, is_safe, min_balance
from core.formatting import validate_output_row
from core.fx import MissingExchangeRateError, to_home_currency
from core.models import CashItem, Profile, Request, SeriesAdjustment
from core.options import PaymentOption, completion_date, expand_schedule
from core.state import RecurringSeries, _adjusted_series_amount


def _profile(**overrides):
    base = dict(
        user_id="user_synthetic", home_currency="ZAR", current_available_balance=Decimal("1000"),
        minimum_balance_to_keep=Decimal("100"), financial_priorities=(), protect_categories=frozenset(),
        reduce_categories=frozenset(), stop_categories=frozenset(),
        payment_methods_user_will_consider=frozenset(), max_installment_months=None,
    )
    base.update(overrides)
    return Profile(**base)


def _request(**overrides):
    base = dict(
        request_id="request_synthetic", user_id="user_synthetic", request_date=date(2026, 1, 1),
        request_type="purchase", requested_amount=Decimal("500"),
        desired_completion_date=date(2026, 2, 1), allows_partial_payment=True, request_text="",
    )
    base.update(overrides)
    return Request(**base)


def _item(day, amount, event_id="e1"):
    return CashItem(when=day, signed_amount=amount, event_id=event_id, category="misc",
                     flexibility=None, minimum_allowed_amount=None)


# --- 1. Same-date collision among three or more events ------------------

def test_four_way_same_date_collision_does_not_crash():
    # Two debits and two credits, all on the same date -- confirms the
    # debits-first convention generalizes to N events sharing a day, not
    # just the two-event case D2 was originally verified against. All of a
    # day's debits are summed and post together (the intraday low), then all
    # of that day's credits are summed and added (the end-of-day figure).
    items = [
        _item(date(2026, 1, 10), Decimal("-300"), "d1"),
        _item(date(2026, 1, 10), Decimal("-150"), "d2"),
        _item(date(2026, 1, 10), Decimal("200"), "c1"),
        _item(date(2026, 1, 10), Decimal("50"), "c2"),
    ]
    trace = daily_balances(Decimal("1000"), items, date(2026, 1, 1), 30)
    same_day = [b for d, b in trace if d == date(2026, 1, 10)]
    # intraday low: 1000 - 300 - 150 = 550; end of day: 550 + 200 + 50 = 800
    assert same_day == [Decimal("550"), Decimal("800")]
    assert min_balance(trace) == Decimal("550")
    assert is_safe(trace, Decimal("500"))
    assert not is_safe(trace, Decimal("600"))  # the intraday low, not just the end-of-day figure, must bind
    amt = amount_safe_to_pay(trace, Decimal("100"), Decimal("500"))
    assert Decimal("0") <= amt <= Decimal("500")


def test_five_way_same_date_collision_all_debits():
    items = [_item(date(2026, 1, 10), Decimal("-100"), f"d{i}") for i in range(5)]
    trace = daily_balances(Decimal("1000"), items, date(2026, 1, 1), 30)
    same_day = [b for d, b in trace if d == date(2026, 1, 10)]
    assert same_day == [Decimal("500")]  # 1000 - 5*100
    assert min_balance(trace) == Decimal("500")


# --- 2. Income terminates (new_amount: 0) exactly on request_date -------

def test_zero_income_amendment_effective_exactly_on_request_date():
    series = RecurringSeries(
        category="salary", direction="credit", description="Payroll credit", currency="ZAR",
        cadence_days=30, is_monthly=True, conservative_amount=Decimal("2000"),
        last_historical_date=date(2025, 12, 1), flexibility="fixed", minimum_allowed_amount=None,
        representative_event_id="e_hist",
    )
    request_date = date(2026, 1, 1)
    adjustment = SeriesAdjustment(
        category="salary", direction="credit", scope="ongoing",
        new_amount=Decimal("0"), effective_date=request_date, source="test",
    )
    notes = []
    # An occurrence landing exactly ON request_date (the effective_date) must
    # already see the zero -- the boundary is inclusive, not exclusive.
    amount_on_boundary = _adjusted_series_amount(series, request_date, 0, [adjustment], notes)
    assert amount_on_boundary == Decimal("0")
    # An occurrence one day earlier is unaffected (unreachable in a real
    # forecast anyway, since projection only ever starts at window_start, but
    # the boundary semantics should still be exactly right, not off-by-one).
    amount_before = _adjusted_series_amount(
        series, request_date - timedelta(days=1), 0, [adjustment], notes
    )
    assert amount_before == Decimal("2000")


def test_zero_income_termination_produces_a_safe_not_recommended_or_correct_forecast():
    # End to end: a user whose only income terminates to zero exactly at
    # request_date, with essential debits continuing. Must not crash, and
    # amount_safe_to_pay must respect the resulting (lower) trajectory.
    request_date = date(2026, 1, 1)
    items = [_item(request_date + timedelta(days=30 * i), Decimal("-400"), f"rent{i}") for i in range(1, 4)]
    trace = daily_balances(Decimal("1000"), items, request_date, 90)
    assert min_balance(trace) == Decimal("1000") - 3 * Decimal("400")  # no income at all after termination
    amt = amount_safe_to_pay(trace, Decimal("100"), Decimal("5000"))
    assert Decimal("0") <= amt <= Decimal("5000")


# --- 3. Multi-hop currency chain: rejected explicitly, never silently chained ---

def test_two_hop_fx_chain_is_rejected_not_silently_chained():
    # USD -> EUR and EUR -> ZAR both exist for this date, but USD -> ZAR does
    # not. The converter must NOT chain through EUR on its own initiative --
    # only an exact (date, from, to) row is ever used.
    on_date = date(2026, 1, 15)
    rates = {
        (on_date, "USD", "EUR"): Decimal("0.9"),
        (on_date, "EUR", "ZAR"): Decimal("20"),
    }
    with pytest.raises(MissingExchangeRateError):
        to_home_currency(Decimal("100"), "USD", on_date, "ZAR", rates)


def test_direct_rate_still_used_when_present_alongside_an_unrelated_chain():
    on_date = date(2026, 1, 15)
    rates = {
        (on_date, "USD", "EUR"): Decimal("0.9"),
        (on_date, "EUR", "ZAR"): Decimal("20"),
        (on_date, "USD", "ZAR"): Decimal("18.5"),  # the actual direct rate
    }
    result = to_home_currency(Decimal("100"), "USD", on_date, "ZAR", rates)
    assert result == Decimal("1850")  # uses the direct rate, not 100*0.9*20=1800


# --- 4. Installments crossing a leap-day / month-end boundary -----------

def test_installment_schedule_across_month_end_and_leap_day():
    # Jan 31 + 30 days (explicit frequency, not inferred) must NOT collapse
    # to a nonexistent date -- it is exact day-count arithmetic per the
    # dataset's own payment_frequency_days field, unrelated to the
    # calendar-month-stepping fix for INFERRED recurring series.
    option = PaymentOption(
        payment_option_id="payment_option_test", request_id="request_synthetic",
        payment_method="installments", payment_amount=Decimal("100"), number_of_payments=3,
        first_payment_date=date(2028, 1, 31), payment_frequency_days=30,
        financing_fee=Decimal("0"), total_payable_amount=Decimal("300"),
    )
    schedule = expand_schedule(option)
    assert schedule == [
        (date(2028, 1, 31), Decimal("100")),
        (date(2028, 3, 1), Decimal("100")),   # 2028 is a leap year: Jan31+30d crosses Feb29
        (date(2028, 3, 31), Decimal("100")),
    ]
    assert completion_date(option) == date(2028, 3, 31)


def test_installment_schedule_across_leap_day_directly():
    option = PaymentOption(
        payment_option_id="payment_option_test2", request_id="request_synthetic",
        payment_method="installments", payment_amount=Decimal("50"), number_of_payments=2,
        first_payment_date=date(2028, 2, 28), payment_frequency_days=1,
        financing_fee=Decimal("0"), total_payable_amount=Decimal("100"),
    )
    schedule = expand_schedule(option)
    assert schedule[1][0] == date(2028, 2, 29)  # leap day exists and is used, not skipped


def test_installment_schedule_non_leap_year_february():
    option = PaymentOption(
        payment_option_id="payment_option_test3", request_id="request_synthetic",
        payment_method="installments", payment_amount=Decimal("50"), number_of_payments=2,
        first_payment_date=date(2027, 1, 30), payment_frequency_days=30,
        financing_fee=Decimal("0"), total_payable_amount=Decimal("100"),
    )
    schedule = expand_schedule(option)
    assert schedule[1][0] == date(2027, 3, 1)  # 2027 not a leap year: Jan30+30d


# --- 5. Every payment method unsafe -> not_recommended, schema-valid -----

def test_every_method_unsafe_produces_a_valid_not_recommended_row():
    from agent.evidence import EvidenceCache
    from main import process_request

    profile = _profile(
        current_available_balance=Decimal("50"), minimum_balance_to_keep=Decimal("40"),
        payment_methods_user_will_consider=frozenset(),  # accepts NOTHING
    )
    request = _request(requested_amount=Decimal("1000000"), desired_completion_date=date(2026, 1, 3))
    cache = EvidenceCache(Path("/tmp/nonexistent_synthetic_cache.json"))

    row = process_request(
        request, profile, all_events=[], rates={}, options_by_request={},
        all_messages=[], all_images=[], client=None, cache=cache,
    )

    errors = validate_output_row(row, request, [], chosen_plan=None)
    assert errors == [], f"synthetic not_recommended row failed schema validation: {errors}"
    assert row["recommended_payment_method"] == "not_recommended"
    assert row["affordability_status"] == "not_affordable"
    assert row["payment_plan"] == "none"
    assert row["spending_changes_needed"] == "none"
    assert Decimal("0") <= Decimal(row["amount_safe_to_pay"]) <= request.requested_amount
    # non-generic: must cite this request's own figures (its minimum balance
    # and deadline), matching the register verified against ground truth's
    # own not_recommended rows -- not a static string with no row-specific data.
    explanation = row["decision_explanation"]
    assert "40" in explanation  # the user's own minimum_balance_to_keep
    assert "2026" in explanation  # the deadline, not a placeholder date
    assert len(explanation.strip()) > 20

    # And genuinely non-generic: a different minimum/deadline must produce a
    # different sentence, not the same string reused across rows.
    profile2 = _profile(
        current_available_balance=Decimal("50"), minimum_balance_to_keep=Decimal("777"),
        payment_methods_user_will_consider=frozenset(),
    )
    request2 = _request(requested_amount=Decimal("1000000"), desired_completion_date=date(2026, 6, 6))
    row2 = process_request(
        request2, profile2, all_events=[], rates={}, options_by_request={},
        all_messages=[], all_images=[], client=None, cache=cache,
    )
    assert row2["decision_explanation"] != explanation
    assert "777" in row2["decision_explanation"]


def test_every_method_unsafe_even_when_all_methods_accepted():
    from agent.evidence import EvidenceCache
    from main import process_request

    # All methods accepted, but balance is catastrophically insufficient and
    # the deadline is immediate, so nothing -- full, partial, installments,
    # or wait -- can possibly be safe.
    profile = _profile(
        current_available_balance=Decimal("10"), minimum_balance_to_keep=Decimal("500"),
        payment_methods_user_will_consider=frozenset({"full_payment", "partial_payment", "installments"}),
    )
    request = _request(
        requested_amount=Decimal("999999"), allows_partial_payment=True,
        desired_completion_date=date(2026, 1, 1),
    )
    cache = EvidenceCache(Path("/tmp/nonexistent_synthetic_cache_2.json"))
    row = process_request(
        request, profile, all_events=[], rates={}, options_by_request={},
        all_messages=[], all_images=[], client=None, cache=cache,
    )
    errors = validate_output_row(row, request, [], chosen_plan=None)
    assert errors == []
    assert row["recommended_payment_method"] == "not_recommended"
