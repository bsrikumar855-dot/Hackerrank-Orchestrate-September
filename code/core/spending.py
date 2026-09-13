"""Optional spending-change search.

Used only as a fallback: when no full_payment/partial_payment/installments
plan is safe on its own, try to make a full lump-sum or supplied installment schedule safe by
stopping or reducing up to 3 flexible, non-protected, category-permitted
recurring debits. Greedy by largest freed-amount first — a deterministic,
bounded heuristic. It is not a guaranteed-optimal minimum-disruption search;
documented as a known simplification given the contest time budget (see
code/README.md).

Stop and reduce are mutually exclusive per event (enforced by picking at
most one action per event_id — whichever frees more).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

from core.forecast import daily_balances, is_safe
from core.formatting import format_decimal
from core.models import CashItem, Profile

MAX_SPENDING_CHANGES = 3


@dataclass(frozen=True)
class SpendingAction:
    event_id: str
    action: str  # "stop" | "reduce"
    freed_amount: Decimal
    new_amount: Optional[Decimal] = None  # per-occurrence amount after reduce

    @property
    def formatted(self) -> str:
        if self.action == "stop":
            return f"stop:{self.event_id}"
        return f"reduce_to:{self.event_id}:{format_decimal(self.new_amount)}"


def _candidate_actions(cash_items: list[CashItem], profile: Profile) -> list[SpendingAction]:
    groups: dict[str, list[CashItem]] = {}
    for item in cash_items:
        # The output contract permits changing recurring expenses only. A
        # flexible-looking explicit pending/scheduled row is still a real
        # obligation unless recurrence projection identifies its commitment.
        if not item.is_projected:
            continue
        if item.signed_amount >= 0 or item.category in profile.protect_categories:
            continue
        if item.flexibility not in ("stoppable", "reducible", "reducible_or_stoppable"):
            continue
        groups.setdefault(item.event_id, []).append(item)

    actions: list[SpendingAction] = []
    for event_id, items in groups.items():
        category = items[0].category
        flexibility = items[0].flexibility
        can_stop = flexibility in ("stoppable", "reducible_or_stoppable") and category in profile.stop_categories
        can_reduce = flexibility in ("reducible", "reducible_or_stoppable") and category in profile.reduce_categories
        total_amount = sum((-i.signed_amount for i in items), Decimal("0"))

        stop_option = SpendingAction(event_id, "stop", total_amount) if can_stop else None
        reduce_option = None
        if can_reduce:
            min_allowed = items[0].minimum_allowed_amount or Decimal("0")
            per_occurrence = -items[0].signed_amount
            if min_allowed < per_occurrence:
                freed = (per_occurrence - min_allowed) * len(items)
                reduce_option = SpendingAction(event_id, "reduce", freed, new_amount=min_allowed)

        # mutually exclusive per event: keep whichever frees more
        best = max((o for o in (stop_option, reduce_option) if o), key=lambda a: a.freed_amount, default=None)
        if best is not None:
            actions.append(best)
    return actions


def find_spending_plan(
    starting_balance: Decimal,
    baseline_cash_items: list[CashItem],
    profile: Profile,
    window_start: date,
    window_days: int,
    payment_date: date,
    payment_amount: Decimal,
) -> Optional[list[SpendingAction]]:
    return find_spending_plan_for_schedule(
        starting_balance, baseline_cash_items, profile, window_start, window_days,
        [(payment_date, payment_amount)],
    )


def find_spending_plan_for_schedule(
    starting_balance: Decimal,
    baseline_cash_items: list[CashItem],
    profile: Profile,
    window_start: date,
    window_days: int,
    payments: list[tuple[date, Decimal]],
) -> Optional[list[SpendingAction]]:
    """Use the same bounded search against every leg of a supplied schedule."""
    payment_items = [
        CashItem(d, -amount, None, "__payment__", None, None)
        for d, amount in payments
    ]

    def _safe(items: list[CashItem]) -> bool:
        trace = daily_balances(starting_balance, items + payment_items, window_start, window_days)
        return is_safe(trace, profile.minimum_balance_to_keep)

    ranked = sorted(
        _candidate_actions(baseline_cash_items, profile), key=lambda a: a.freed_amount, reverse=True
    )

    remaining = list(baseline_cash_items)
    chosen: list[SpendingAction] = []
    for action in ranked:
        if len(chosen) >= MAX_SPENDING_CHANGES:
            break
        if action.action == "stop":
            remaining = [i for i in remaining if i.event_id != action.event_id]
        else:
            remaining = [
                CashItem(
                    when=i.when,
                    signed_amount=-action.new_amount,
                    event_id=i.event_id,
                    category=i.category,
                    flexibility=i.flexibility,
                    minimum_allowed_amount=i.minimum_allowed_amount,
                    is_projected=i.is_projected,
                )
                if i.event_id == action.event_id
                else i
                for i in remaining
            ]
        chosen.append(action)
        if _safe(remaining):
            return chosen
    return None
