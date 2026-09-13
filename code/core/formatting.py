"""Step 7: format enforcement. Every output row is built here and then
validated here before main.py writes it — reject-and-retry rather than ever
emitting a row that fails its own invariants.
"""
from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from core.models import PaymentOption, Request
from core.options import expand_schedule
from core.ranking import Plan

CENTS = Decimal("0.01")
ALLOWED_STATUS = {"affordable_now", "affordable_with_plan", "affordable_later", "not_affordable"}
ALLOWED_METHOD = {"full_payment", "partial_payment", "installments", "wait", "not_recommended"}

_PAYMENT_LEG_RE = re.compile(r"^\d{4}-\d{2}-\d{2}:-?\d+(\.\d+)?$")
_STOP_RE = re.compile(r"^stop:(.+)$")
_REDUCE_RE = re.compile(r"^reduce_to:([^:]+):(-?\d+(\.\d+)?)$")


def format_decimal(d: Decimal) -> str:
    """`amount_safe_to_pay` style: trailing zeros stripped.

    Matched to the solved samples, whose values read 17229139.2, 603.3,
    433.4 alongside 87170.56 and 25256 -- i.e. no zero padding.
    """
    q = d.quantize(CENTS, rounding=ROUND_HALF_UP)
    s = format(q, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def format_plan_amount(d: Decimal) -> str:
    """`payment_plan` leg style: whole amounts bare, fractional amounts to
    exactly two decimals.

    This differs from format_decimal on purpose. The solved samples show
    payment_plan legs as 620.40, 996.60, 941.60, 3246.10, 1574.40 and
    15952906.67 -- always two decimals when fractional -- while whole legs
    appear bare (25256, 5491000, 38016, 28820). Stripping the trailing zero
    here, as an earlier draft did, mismatched every plan with a round-cent
    amount.
    """
    q = d.quantize(CENTS, rounding=ROUND_HALF_UP)
    if q == q.to_integral_value():
        return format(q.to_integral_value(), "f")
    return format(q, "f")


def format_date(d) -> str:
    return d.isoformat() if d is not None else ""


def format_payment_plan(plan: Optional[Plan]) -> str:
    if plan is None or not plan.payments:
        return "none"
    return "|".join(f"{d.isoformat()}:{format_plan_amount(amt)}" for d, amt in plan.payments)


def format_spending_changes(plan: Optional[Plan]) -> str:
    if plan is None or not plan.spending_changes:
        return "none"
    return "|".join(plan.spending_changes)


def build_output_row(
    request: Request,
    amount_safe_to_pay: Decimal,
    affordability_status: str,
    recommended_payment_method: str,
    chosen_plan: Optional[Plan],
    earliest_date_for_full_payment,
    decision_explanation: str,
) -> dict:
    return {
        "request_id": request.request_id,
        "amount_safe_to_pay": format_decimal(amount_safe_to_pay),
        "affordability_status": affordability_status,
        "recommended_payment_method": recommended_payment_method,
        "payment_plan": format_payment_plan(chosen_plan),
        "earliest_date_for_full_payment": format_date(earliest_date_for_full_payment),
        "spending_changes_needed": format_spending_changes(chosen_plan),
        "decision_explanation": decision_explanation,
    }


def not_recommended_row(request: Request, reason: str) -> dict:
    return {
        "request_id": request.request_id,
        "amount_safe_to_pay": "0",
        "affordability_status": "not_affordable",
        "recommended_payment_method": "not_recommended",
        "payment_plan": "none",
        "earliest_date_for_full_payment": "",
        "spending_changes_needed": "none",
        "decision_explanation": reason,
    }


def validate_output_row(
    row: dict,
    request: Request,
    options_for_request: list[PaymentOption],
    chosen_plan: Optional[Plan] = None,
) -> list[str]:
    """Returns a list of violation messages; empty means the row is valid."""
    errors: list[str] = []

    if row["request_id"] != request.request_id:
        errors.append("request_id mismatch")

    try:
        amount_safe = Decimal(row["amount_safe_to_pay"])
    except Exception:
        errors.append("amount_safe_to_pay is not a decimal")
        amount_safe = None
    if amount_safe is not None and not (Decimal("0") <= amount_safe <= request.requested_amount):
        errors.append(
            f"amount_safe_to_pay {amount_safe} out of bounds [0, {request.requested_amount}]"
        )

    if row["affordability_status"] not in ALLOWED_STATUS:
        errors.append(f"invalid affordability_status {row['affordability_status']!r}")

    method = row["recommended_payment_method"]
    if method not in ALLOWED_METHOD:
        errors.append(f"invalid recommended_payment_method {method!r}")

    plan_str = row["payment_plan"]
    if plan_str != "none":
        legs = plan_str.split("|")
        if not all(_PAYMENT_LEG_RE.match(leg) for leg in legs):
            errors.append("payment_plan has a malformed leg")
        else:
            parsed = [(leg.split(":")[0], Decimal(leg.split(":")[1])) for leg in legs]
            if parsed != sorted(parsed, key=lambda p: p[0]):
                errors.append("payment_plan legs are not in chronological order")
            total = sum((amt for _, amt in parsed), Decimal("0"))

            if method == "partial_payment":
                if len(parsed) != 2:
                    errors.append("partial_payment must have exactly two payments")
                elif total != request.requested_amount:
                    errors.append("partial_payment legs do not sum to requested_amount")
                elif amount_safe is not None and Decimal(parsed[0][1]) != amount_safe:
                    errors.append("partial_payment first leg must equal amount_safe_to_pay")
                elif not (Decimal("0") < amount_safe < request.requested_amount):
                    errors.append("partial_payment requires 0 < amount_safe_to_pay < requested_amount")

            if method == "installments":
                option_id = chosen_plan.payment_option_id if chosen_plan else None
                option = next(
                    (o for o in options_for_request if o.payment_option_id == option_id), None
                )
                if option is None:
                    errors.append("installments plan does not reference a supplied payment_option_id")
                else:
                    expected = [(d.isoformat(), format_decimal(amt)) for d, amt in expand_schedule(option)]
                    got = [(d, format_decimal(amt)) for d, amt in parsed]
                    if expected != got:
                        errors.append("installments schedule does not exactly match the supplied payment option")

            if method in ("full_payment", "wait") and total != request.requested_amount:
                errors.append(f"{method} plan does not total requested_amount")
    else:
        if method != "not_recommended" or row["affordability_status"] != "not_affordable":
            errors.append(f"payment_plan is 'none' but recommended_payment_method is {method!r}")

    if row["affordability_status"] == "affordable_now":
        if row["earliest_date_for_full_payment"] != request.request_date.isoformat():
            errors.append("affordable_now requires earliest_date_for_full_payment == request_date")

    changes_str = row["spending_changes_needed"]
    if changes_str != "none":
        entries = changes_str.split("|")
        if len(entries) > 3:
            errors.append("spending_changes_needed has more than 3 entries")
        targeted_events: dict[str, set[str]] = {}
        for entry in entries:
            stop_m = _STOP_RE.match(entry)
            reduce_m = _REDUCE_RE.match(entry)
            if stop_m:
                targeted_events.setdefault(stop_m.group(1), set()).add("stop")
            elif reduce_m:
                targeted_events.setdefault(reduce_m.group(1), set()).add("reduce")
            else:
                errors.append(f"malformed spending change entry {entry!r}")
        for event_id, kinds in targeted_events.items():
            if len(kinds) > 1:
                errors.append(f"event {event_id} has both stop and reduce_to (mutually exclusive)")

    if not row["decision_explanation"] or not row["decision_explanation"].strip():
        errors.append("decision_explanation is empty")

    return errors
