"""Orchestrates one request's candidate-plan construction: wires state,
forecast, affordability, options, and the spending-change fallback together,
then hands the candidate list to ranking.rank_plans for the pure tie-break.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional

from core.affordability import amount_safe_to_pay as compute_amount_safe_to_pay
from core.affordability import earliest_date_for_full_payment as compute_earliest_full_date
from core.forecast import FORECAST_DAYS, daily_balances, is_safe
from core.models import CashItem, PaymentOption, Profile, Request
from core.options import (
    completion_date,
    expand_schedule,
    full_payment_option,
    installment_options,
    within_max_installment_months,
)
from core.ranking import Plan, best_plan, rank_plans


@dataclass
class RequestDecision:
    amount_safe_to_pay: Decimal
    earliest_date_for_full_payment: Optional[date]
    affordability_status: str
    recommended_payment_method: str
    chosen_plan: Optional[Plan]
    all_candidates: list[Plan] = field(default_factory=list)


def _affordability_status(plan: Optional[Plan], request_date: date) -> str:
    if plan is None:
        return "not_affordable"
    if plan.method == "wait":
        return "affordable_later"
    if plan.method == "full_payment" and not plan.requires_spending_changes and plan.start_date == request_date:
        return "affordable_now"
    return "affordable_with_plan"


def build_decision(
    request: Request,
    profile: Profile,
    baseline_cash_items: list[CashItem],
    starting_balance: Decimal,
    options: list[PaymentOption],
    spending_plan_finder=None,
) -> RequestDecision:
    """`spending_plan_finder(starting_balance, baseline_cash_items, profile,
    window_start, window_days, payment_date, payment_amount) -> Optional[list[SpendingAction]]`
    — injected so this stays testable without importing core.spending directly
    (defaults to it when not given).
    """
    if spending_plan_finder is None:
        from core.spending import find_spending_plan as spending_plan_finder

    window_start = request.request_date
    baseline_trace = daily_balances(starting_balance, baseline_cash_items, window_start, FORECAST_DAYS)

    safe_amount = compute_amount_safe_to_pay(
        baseline_trace, profile.minimum_balance_to_keep, request.requested_amount
    )
    earliest_full = compute_earliest_full_date(
        baseline_trace, profile.minimum_balance_to_keep, request.requested_amount
    )

    candidates: list[Plan] = []
    accepted = profile.payment_methods_user_will_consider
    fp_option = full_payment_option(options)
    fp_option_id = fp_option.payment_option_id if fp_option else None
    within_deadline_today = request.request_date <= request.desired_completion_date

    if "full_payment" in accepted and safe_amount == request.requested_amount and within_deadline_today:
        candidates.append(
            Plan(
                method="full_payment",
                payments=((request.request_date, request.requested_amount),),
                completes_by_deadline=True,
                requires_spending_changes=False,
                total_paid=request.requested_amount,
                start_date=request.request_date,
                payment_option_id=fp_option_id,
            )
        )

    if (
        "full_payment" in accepted
        and earliest_full is not None
        and earliest_full != request.request_date
        and earliest_full <= request.desired_completion_date
    ):
        candidates.append(
            Plan(
                method="wait",
                payments=((earliest_full, request.requested_amount),),
                completes_by_deadline=True,
                requires_spending_changes=False,
                total_paid=request.requested_amount,
                start_date=earliest_full,
                payment_option_id=fp_option_id,
            )
        )

    if (
        request.allows_partial_payment
        and "partial_payment" in accepted
        and Decimal("0") < safe_amount < request.requested_amount
        and earliest_full is not None
        and earliest_full <= request.desired_completion_date
    ):
        remainder = request.requested_amount - safe_amount
        candidates.append(
            Plan(
                method="partial_payment",
                payments=((request.request_date, safe_amount), (earliest_full, remainder)),
                completes_by_deadline=True,
                requires_spending_changes=False,
                total_paid=request.requested_amount,
                start_date=request.request_date,
                payment_option_id=None,
            )
        )

    if "installments" in accepted:
        for option in installment_options(options):
            if not within_max_installment_months(option, profile.max_installment_months):
                continue
            if completion_date(option) > request.desired_completion_date:
                continue
            schedule = expand_schedule(option)
            extra_items = [
                CashItem(
                    when=d, signed_amount=-amt, event_id=None,
                    category="__payment__", flexibility=None, minimum_allowed_amount=None,
                )
                for d, amt in schedule
            ]
            trace = daily_balances(starting_balance, baseline_cash_items + extra_items, window_start, FORECAST_DAYS)
            if not is_safe(trace, profile.minimum_balance_to_keep):
                continue
            candidates.append(
                Plan(
                    method="installments",
                    payments=tuple(schedule),
                    completes_by_deadline=True,
                    requires_spending_changes=False,
                    total_paid=option.total_payable_amount,
                    start_date=option.first_payment_date,
                    payment_option_id=option.payment_option_id,
                )
            )

    has_unchanged_plan = bool(candidates)
    if not has_unchanged_plan and "full_payment" in accepted and within_deadline_today:
        actions = spending_plan_finder(
            starting_balance, baseline_cash_items, profile, window_start, FORECAST_DAYS,
            request.request_date, request.requested_amount,
        )
        if actions is not None:
            candidates.append(
                Plan(
                    method="full_payment",
                    payments=((request.request_date, request.requested_amount),),
                    completes_by_deadline=True,
                    requires_spending_changes=True,
                    total_paid=request.requested_amount,
                    start_date=request.request_date,
                    payment_option_id=fp_option_id,
                    spending_changes=tuple(a.formatted for a in actions),
                )
            )

    if not has_unchanged_plan and "installments" in accepted and within_deadline_today:
        from core.spending import find_spending_plan_for_schedule

        for option in installment_options(options):
            if not within_max_installment_months(option, profile.max_installment_months):
                continue
            if completion_date(option) > request.desired_completion_date:
                continue
            schedule = expand_schedule(option)
            actions = find_spending_plan_for_schedule(
                starting_balance, baseline_cash_items, profile, window_start,
                FORECAST_DAYS, schedule,
            )
            if actions:
                candidates.append(Plan(
                    method="installments", payments=tuple(schedule),
                    completes_by_deadline=True, requires_spending_changes=True,
                    total_paid=option.total_payable_amount, start_date=option.first_payment_date,
                    payment_option_id=option.payment_option_id,
                    spending_changes=tuple(a.formatted for a in actions),
                ))

    ranked = rank_plans(candidates)
    chosen = ranked[0] if ranked else None
    status = _affordability_status(chosen, request.request_date)
    method = chosen.method if chosen else "not_recommended"

    return RequestDecision(
        amount_safe_to_pay=safe_amount,
        earliest_date_for_full_payment=earliest_full,
        affordability_status=status,
        recommended_payment_method=method,
        chosen_plan=chosen,
        all_candidates=ranked,
    )
