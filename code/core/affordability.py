"""Step 4 & 5: amount_safe_to_pay and earliest_date_for_full_payment.

Both are closed-form over the baseline (no-optional-spending-change,
no-candidate-payment) daily balance trace for the fixed 90-day window,
rather than a search that re-simulates per candidate — see code/README.md.

A lump-sum payment made on day `d` (>= window_start) reduces the baseline
balance on every day from `d` onward by that amount, and leaves every day
before `d` untouched. So:

- amount_safe_to_pay (paid on window_start itself) is safe iff
  `baseline_balance(day) - amount >= minimum` for every day in the window,
  i.e. `amount <= min(baseline_balances) - minimum`, capped to
  `[0, requested_amount]`.
- earliest_date_for_full_payment is the first day `d` where every day before
  `d` was already safe on its own (a pre-existing baseline dip before `d`
  can't be fixed by paying later) AND every day from `d` onward stays safe
  after subtracting the full requested amount.

A same-day-as-income convention, checked and rejected: a same-day debit and
credit are made to post debits-first everywhere else in this codebase (see
core/forecast.py's daily_balances), so it seemed consistent to also treat a
*candidate* payment date that shares a day with a real income credit as
"debit posts first, income not yet available." That produces a real
mathematical inconsistency (re-simulating the recommended `wait` plan showed
it breaching the minimum on 63 of 250 real requests) — but sample_requests.csv
falsifies the fix directly: request_04's ground truth `wait` date is the same
calendar day its salary lands, not the day after. The reference evidently
treats "wait for payday, then pay" as funded by that day's own income, which
is a different (and here, correct) convention from two unrelated pre-existing
events colliding on a date neither was chosen for. Reverted; see
EXPERIMENTS.md for the full account, including why the inconsistency itself
was real without the chosen fix being right.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_DOWN
from typing import Optional

from core.forecast import min_balance

CENTS = Decimal("0.01")


def amount_safe_to_pay(
    baseline_trace: list[tuple[date, Decimal]],
    minimum_balance_to_keep: Decimal,
    requested_amount: Decimal,
) -> Decimal:
    headroom = min_balance(baseline_trace) - minimum_balance_to_keep
    amount = max(Decimal("0"), min(headroom, requested_amount))
    return amount.quantize(CENTS, rounding=ROUND_DOWN)


def earliest_date_for_full_payment(
    baseline_trace: list[tuple[date, Decimal]],
    minimum_balance_to_keep: Decimal,
    requested_amount: Decimal,
) -> Optional[date]:
    n = len(baseline_trace)
    dates = [d for d, _ in baseline_trace]
    balances = [b for _, b in baseline_trace]

    suffix_min: list[Decimal] = [Decimal("0")] * n
    running = None
    for i in range(n - 1, -1, -1):
        running = balances[i] if running is None else min(running, balances[i])
        suffix_min[i] = running

    prefix_min_before: list[Optional[Decimal]] = [None] * n
    running = None
    for i in range(n):
        prefix_min_before[i] = running
        running = balances[i] if running is None else min(running, balances[i])

    for i in range(n):
        before_ok = prefix_min_before[i] is None or prefix_min_before[i] >= minimum_balance_to_keep
        after_ok = (suffix_min[i] - requested_amount) >= minimum_balance_to_keep
        if before_ok and after_ok:
            return dates[i]
    return None
