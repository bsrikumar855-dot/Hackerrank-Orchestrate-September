from datetime import date
from decimal import Decimal

from core.formatting import (
    format_decimal,
    format_payment_plan,
    format_plan_amount,
    format_spending_changes,
    validate_output_row,
)
from core.models import PaymentOption, Request
from core.ranking import Plan


def _request(**overrides):
    base = dict(
        request_id="request_01", user_id="user_01", request_date=date(2026, 1, 1),
        request_type="purchase", requested_amount=Decimal("1000"),
        desired_completion_date=date(2026, 2, 1), allows_partial_payment=True, request_text="",
    )
    base.update(overrides)
    return Request(**base)


def test_format_decimal_strips_trailing_zeros():
    assert format_decimal(Decimal("100.00")) == "100"
    assert format_decimal(Decimal("17229139.20")) == "17229139.2"
    assert format_decimal(Decimal("15952906.666")) == "15952906.67"  # rounds to cents


def test_plan_amounts_pad_to_two_decimals_unlike_amount_safe_to_pay():
    # The two fields follow different rules in the solved samples:
    # payment_plan legs read 620.40 / 996.60 / 3246.10 (always 2dp when
    # fractional), while amount_safe_to_pay reads 17229139.2 / 603.3 (zeros
    # stripped). An earlier draft used one rule for both and mismatched every
    # plan with a round-cent amount.
    assert format_plan_amount(Decimal("620.4")) == "620.40"
    assert format_plan_amount(Decimal("996.6")) == "996.60"
    assert format_plan_amount(Decimal("3246.1")) == "3246.10"
    assert format_plan_amount(Decimal("15952906.67")) == "15952906.67"
    assert format_plan_amount(Decimal("25256")) == "25256"  # whole stays bare
    assert format_plan_amount(Decimal("25256.00")) == "25256"

    assert format_decimal(Decimal("620.40")) == "620.4"
    assert format_decimal(Decimal("17229139.20")) == "17229139.2"


def test_format_payment_plan_uses_the_plan_amount_rule():
    plan = Plan(
        method="wait", payments=((date(2026, 1, 3), Decimal("620.4")),),
        completes_by_deadline=True, requires_spending_changes=False,
        total_paid=Decimal("620.4"), start_date=date(2026, 1, 3),
    )
    assert format_payment_plan(plan) == "2026-01-03:620.40"


def test_format_payment_plan_none_when_no_plan():
    assert format_payment_plan(None) == "none"


def test_format_payment_plan_joins_legs():
    plan = Plan(
        method="installments", payments=((date(2026, 1, 1), Decimal("100")), (date(2026, 2, 1), Decimal("100"))),
        completes_by_deadline=True, requires_spending_changes=False, total_paid=Decimal("200"),
        start_date=date(2026, 1, 1), payment_option_id="payment_option_01",
    )
    assert format_payment_plan(plan) == "2026-01-01:100|2026-02-01:100"


def test_format_spending_changes_none_when_empty():
    plan = Plan(
        method="full_payment", payments=((date(2026, 1, 1), Decimal("100")),), completes_by_deadline=True,
        requires_spending_changes=False, total_paid=Decimal("100"), start_date=date(2026, 1, 1),
    )
    assert format_spending_changes(plan) == "none"


def test_validate_rejects_amount_safe_to_pay_out_of_bounds():
    req = _request(requested_amount=Decimal("1000"))
    row = {
        "request_id": "request_01", "amount_safe_to_pay": "1500", "affordability_status": "affordable_now",
        "recommended_payment_method": "full_payment", "payment_plan": "2026-01-01:1000",
        "earliest_date_for_full_payment": "2026-01-01", "spending_changes_needed": "none",
        "decision_explanation": "x",
    }
    errors = validate_output_row(row, req, [])
    assert any("out of bounds" in e for e in errors)


def test_validate_accepts_a_well_formed_full_payment_row():
    req = _request()
    row = {
        "request_id": "request_01", "amount_safe_to_pay": "1000", "affordability_status": "affordable_now",
        "recommended_payment_method": "full_payment", "payment_plan": "2026-01-01:1000",
        "earliest_date_for_full_payment": "2026-01-01", "spending_changes_needed": "none",
        "decision_explanation": "Pay in full today; balance stays above minimum.",
    }
    assert validate_output_row(row, req, []) == []


def test_validate_rejects_partial_payment_not_summing_to_requested_amount():
    req = _request(requested_amount=Decimal("1000"))
    row = {
        "request_id": "request_01", "amount_safe_to_pay": "400", "affordability_status": "affordable_with_plan",
        "recommended_payment_method": "partial_payment", "payment_plan": "2026-01-01:400|2026-01-15:500",
        "earliest_date_for_full_payment": "2026-01-15", "spending_changes_needed": "none",
        "decision_explanation": "x",
    }
    errors = validate_output_row(row, req, [])
    assert any("sum to requested_amount" in e for e in errors)


def test_validate_rejects_installments_not_matching_supplied_option():
    req = _request(requested_amount=Decimal("300"))
    option = PaymentOption(
        payment_option_id="payment_option_01", request_id="request_01", payment_method="installments",
        payment_amount=Decimal("100"), number_of_payments=3, first_payment_date=date(2026, 1, 1),
        payment_frequency_days=30, financing_fee=Decimal("0"), total_payable_amount=Decimal("300"),
    )
    plan = Plan(
        method="installments", payments=((date(2026, 1, 1), Decimal("150")), (date(2026, 2, 1), Decimal("150"))),
        completes_by_deadline=True, requires_spending_changes=False, total_paid=Decimal("300"),
        start_date=date(2026, 1, 1), payment_option_id="payment_option_01",
    )
    row = {
        "request_id": "request_01", "amount_safe_to_pay": "0", "affordability_status": "affordable_with_plan",
        "recommended_payment_method": "installments", "payment_plan": "2026-01-01:150|2026-02-01:150",
        "earliest_date_for_full_payment": "", "spending_changes_needed": "none", "decision_explanation": "x",
    }
    errors = validate_output_row(row, req, [option], chosen_plan=plan)
    assert any("does not exactly match" in e for e in errors)


def test_validate_rejects_stop_and_reduce_on_same_event():
    req = _request()
    row = {
        "request_id": "request_01", "amount_safe_to_pay": "1000", "affordability_status": "affordable_with_plan",
        "recommended_payment_method": "full_payment", "payment_plan": "2026-01-01:1000",
        "earliest_date_for_full_payment": "2026-01-01",
        "spending_changes_needed": "stop:event_5|reduce_to:event_5:20", "decision_explanation": "x",
    }
    errors = validate_output_row(row, req, [])
    assert any("mutually exclusive" in e for e in errors)


def test_validate_rejects_empty_explanation():
    req = _request()
    row = {
        "request_id": "request_01", "amount_safe_to_pay": "0", "affordability_status": "not_affordable",
        "recommended_payment_method": "not_recommended", "payment_plan": "none",
        "earliest_date_for_full_payment": "", "spending_changes_needed": "none", "decision_explanation": "  ",
    }
    errors = validate_output_row(row, req, [])
    assert any("empty" in e for e in errors)
