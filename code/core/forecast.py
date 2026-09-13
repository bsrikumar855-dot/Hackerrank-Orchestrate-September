"""Step 3: 90-day forward cash-flow simulation and the safety check.

The forecast window is fixed per request: [request_date, request_date + 90
days], evaluated once. `amount_safe_to_pay` and `earliest_date_for_full_payment`
both reason about safety *within this one fixed window* rather than a rolling
window recomputed per candidate date — see code/README.md for why that reading
was chosen over the rolling-window alternative.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from core.models import CashItem

FORECAST_DAYS = 90


def daily_balances(
    starting_balance: Decimal,
    cash_items: list[CashItem],
    window_start: date,
    window_days: int = FORECAST_DAYS,
) -> list[tuple[date, Decimal]]:
    """Balance check points across [window_start, window_start+window_days].

    Same-date ordering: the dataset supplies no intraday timing, so when a
    debit and a credit land on the same date the conservative convention is
    that **debits post first**. That day therefore contributes two check
    points -- the intraday low after its debits, and the end-of-day balance
    after its credits -- and the safety check must hold at both.

    Netting a day's debits and credits into one end-of-day figure (the first
    draft here) hides that intraday low, and the hidden dip is real: 8 of the
    25 solved samples have same-date debit/credit collisions (21 such days),
    and every one of the 25 came out on the optimistic side of ground truth
    under netting versus 20 of 25 under debits-first. Measured effect on the
    samples: total field matches 104 -> 109, with earliest_date_for_full_payment
    15/25 -> 18/25. See evaluation/EXPERIMENTS.md (D2).

    A request's own candidate payment is a debit, so it posts with that day's
    debits -- i.e. before any same-day income is assumed to have arrived.
    """
    window_end = window_start + timedelta(days=window_days)
    debits: dict[date, Decimal] = {}
    credits: dict[date, Decimal] = {}
    for item in cash_items:
        if window_start <= item.when <= window_end:
            bucket = debits if item.signed_amount < 0 else credits
            bucket[item.when] = bucket.get(item.when, Decimal("0")) + item.signed_amount

    trace: list[tuple[date, Decimal]] = []
    balance = starting_balance
    d = window_start
    while d <= window_end:
        day_debits = debits.get(d, Decimal("0"))
        day_credits = credits.get(d, Decimal("0"))
        if day_debits and day_credits:
            balance += day_debits
            trace.append((d, balance))  # intraday low, before credits land
            balance += day_credits
        else:
            balance += day_debits + day_credits
        trace.append((d, balance))  # end of day
        d += timedelta(days=1)
    return trace


def is_safe(trace: list[tuple[date, Decimal]], minimum_balance_to_keep: Decimal) -> bool:
    return all(balance >= minimum_balance_to_keep for _, balance in trace)


def min_balance(trace: list[tuple[date, Decimal]]) -> Decimal:
    return min(balance for _, balance in trace)
