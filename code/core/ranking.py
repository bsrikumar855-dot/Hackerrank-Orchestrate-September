"""Step 6: the 6-level tie-break, as a pure sort/filter function.

Deliberately has zero dependency on state/forecast/options — it only knows
about the Plan shape below, so the tie-break order can be unit tested with
hand-built Plan objects where every criterion disagrees (see
tests/test_ranking.py), independent of any simulation logic.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

_TRAILING_DIGITS = re.compile(r"(\d+)$")


@dataclass(frozen=True)
class Plan:
    method: str  # full_payment | partial_payment | installments | wait
    payments: tuple[tuple[date, Decimal], ...]  # chronological (date, amount)
    completes_by_deadline: bool
    requires_spending_changes: bool
    total_paid: Decimal
    start_date: date
    payment_option_id: Optional[str] = None
    spending_changes: tuple[str, ...] = ()  # formatted stop:/reduce_to: strings

    @property
    def num_payments(self) -> int:
        return len(self.payments)


def _option_sort_key(payment_option_id: Optional[str]) -> tuple[int, str]:
    if payment_option_id is None:
        return (1, "")  # sorts after any real option id
    m = _TRAILING_DIGITS.search(payment_option_id)
    return (0, f"{int(m.group(1)):010d}" if m else payment_option_id)


def rank_key(plan: Plan) -> tuple:
    """The 6-level order from problem_statement.md, most-preferred first:
    1. completes the full request by desired_completion_date
    2. requires no spending changes
    3. minimizes total amount paid
    4. starts earlier
    5. uses fewer payments
    6. lowest payment_option_id
    """
    return (
        0 if plan.completes_by_deadline else 1,
        0 if not plan.requires_spending_changes else 1,
        plan.total_paid,
        plan.start_date,
        plan.num_payments,
        _option_sort_key(plan.payment_option_id),
    )


def rank_plans(plans: list[Plan]) -> list[Plan]:
    return sorted(plans, key=rank_key)


def best_plan(plans: list[Plan]) -> Optional[Plan]:
    ranked = rank_plans(plans)
    return ranked[0] if ranked else None
