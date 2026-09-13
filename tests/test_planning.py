from datetime import date
from decimal import Decimal

from core.models import CashItem, PaymentOption, Profile, Request
from core.planning import build_decision


def _profile(**overrides):
    base = dict(
        user_id="user_01", home_currency="ZAR", current_available_balance=Decimal("1000"),
        minimum_balance_to_keep=Decimal("100"), financial_priorities=(), protect_categories=frozenset(),
        reduce_categories=frozenset(), stop_categories=frozenset(),
        payment_methods_user_will_consider=frozenset({"full_payment"}), max_installment_months=None,
    )
    base.update(overrides)
    return Profile(**base)


def _request(**overrides):
    base = dict(
        request_id="request_01", user_id="user_01", request_date=date(2026, 1, 1),
        request_type="purchase", requested_amount=Decimal("500"),
        desired_completion_date=date(2026, 3, 1), allows_partial_payment=True, request_text="",
    )
    base.update(overrides)
    return Request(**base)


def test_affordable_now():
    profile = _profile(current_available_balance=Decimal("10000"), minimum_balance_to_keep=Decimal("100"))
    request = _request(requested_amount=Decimal("500"))
    d = build_decision(request, profile, [], profile.current_available_balance, [])
    assert d.affordability_status == "affordable_now"
    assert d.recommended_payment_method == "full_payment"
    assert d.amount_safe_to_pay == Decimal("500")
    assert d.chosen_plan.payments == ((date(2026, 1, 1), Decimal("500")),)


def test_affordable_later_via_wait():
    profile = _profile(current_available_balance=Decimal("100"), minimum_balance_to_keep=Decimal("50"))
    request = _request(requested_amount=Decimal("500"), desired_completion_date=date(2026, 3, 1))
    salary = CashItem(
        when=date(2026, 1, 20), signed_amount=Decimal("1000"), event_id="e1", category="salary",
        flexibility="fixed", minimum_allowed_amount=None,
    )
    d = build_decision(request, profile, [salary], profile.current_available_balance, [])
    assert d.affordability_status == "affordable_later"
    assert d.recommended_payment_method == "wait"
    assert d.earliest_date_for_full_payment == date(2026, 1, 20)


def test_partial_payment_when_allowed_and_eligible():
    profile = _profile(
        current_available_balance=Decimal("400"), minimum_balance_to_keep=Decimal("100"),
        payment_methods_user_will_consider=frozenset({"partial_payment", "full_payment"}),
    )
    request = _request(requested_amount=Decimal("500"), allows_partial_payment=True, desired_completion_date=date(2026, 3, 1))
    salary = CashItem(
        when=date(2026, 1, 20), signed_amount=Decimal("1000"), event_id="e1", category="salary",
        flexibility="fixed", minimum_allowed_amount=None,
    )
    d = build_decision(request, profile, [salary], profile.current_available_balance, [])
    assert d.affordability_status == "affordable_with_plan"
    assert d.recommended_payment_method == "partial_payment"
    first, second = d.chosen_plan.payments
    assert first == (date(2026, 1, 1), Decimal("300"))  # 400 - 100 minimum
    assert second == (date(2026, 1, 20), Decimal("200"))
    assert first[1] + second[1] == request.requested_amount


def test_installments_chosen_when_that_is_the_only_accepted_method():
    profile = _profile(
        current_available_balance=Decimal("1000"), minimum_balance_to_keep=Decimal("100"),
        payment_methods_user_will_consider=frozenset({"installments"}), max_installment_months=6,
    )
    request = _request(requested_amount=Decimal("900"), allows_partial_payment=False, desired_completion_date=date(2026, 6, 1))
    option = PaymentOption(
        payment_option_id="payment_option_01", request_id="request_01", payment_method="installments",
        payment_amount=Decimal("300"), number_of_payments=3, first_payment_date=date(2026, 1, 1),
        payment_frequency_days=30, financing_fee=Decimal("0"), total_payable_amount=Decimal("900"),
    )
    d = build_decision(request, profile, [], profile.current_available_balance, [option])
    assert d.recommended_payment_method == "installments"
    assert d.affordability_status == "affordable_with_plan"
    assert d.chosen_plan.payment_option_id == "payment_option_01"


def test_not_recommended_when_nothing_is_safe_or_eligible():
    profile = _profile(
        current_available_balance=Decimal("50"), minimum_balance_to_keep=Decimal("40"),
        payment_methods_user_will_consider=frozenset({"full_payment"}),
    )
    request = _request(requested_amount=Decimal("5000"), allows_partial_payment=False, desired_completion_date=date(2026, 1, 5))
    d = build_decision(request, profile, [], profile.current_available_balance, [])
    assert d.recommended_payment_method == "not_recommended"
    assert d.affordability_status == "not_affordable"
    assert d.chosen_plan is None


def test_spending_change_fallback_used_when_nothing_else_safe():
    profile = _profile(
        current_available_balance=Decimal("500"), minimum_balance_to_keep=Decimal("100"),
        payment_methods_user_will_consider=frozenset({"full_payment"}),
    )
    request = _request(requested_amount=Decimal("450"), allows_partial_payment=False, desired_completion_date=date(2026, 1, 10))

    def fake_finder(*args, **kwargs):
        from core.spending import SpendingAction
        return [SpendingAction(event_id="event_9", action="stop", freed_amount=Decimal("100"))]

    d = build_decision(request, profile, [], profile.current_available_balance, [], spending_plan_finder=fake_finder)
    assert d.recommended_payment_method == "full_payment"
    assert d.affordability_status == "affordable_with_plan"
    assert d.chosen_plan.requires_spending_changes is True
    assert d.chosen_plan.spending_changes == ("stop:event_9",)
