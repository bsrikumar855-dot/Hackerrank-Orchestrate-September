"""Step 1: financial-state reconstruction.

Design decisions verified against the actual dataset before being encoded here
(see code/README.md for the full rationale — this is not guesswork):

- `financial_profiles.csv.current_available_balance` is already the user's
  balance as of "now" for that request (every settled event's settlement_date
  is strictly before its user's request_date, confirmed across all 275
  profiles/requests; no settled event ever falls inside the forecast window).
  So the simulator never re-derives the starting balance from history — it
  only uses history to detect recurring patterns.
- No pending/scheduled event ever has a settlement_date before its request_date
  (confirmed across the dataset) — there is no "overdue" case to special-case.
- `linked_event_id` chains in this dataset are always two *distinct* real cash
  legs (a debit then its later refund credit, a cancelled attempt then the
  settled retry, a failed payment then its rescheduled successor, an
  investment purchase then its unrealized valuation or its later sale) — never
  a literal duplicate of the same leg. Verified by inspecting all 58 linked
  rows. So no special dedup collapsing is applied: every event is included or
  excluded purely by its own status/direction, exactly as instructed
  ("the link alone does not determine whether a row counts").
- Explicit future `scheduled`/`pending` rows use different descriptions than
  the recurring series they might resemble (e.g. history's recurring
  "Household utility payment" vs. a one-off "Scheduled utility debit" row) —
  confirmed across every non-salary scheduled/pending row in the dataset.
  They are additive to projected recurring series, not overlapping
  occurrences of them, so no anchor-merging is needed between the two.
- Recurring salary IS projected forward, not just counted from an explicit
  `scheduled` row. Verified against sample_requests.csv: several ground-truth
  `earliest_date_for_full_payment`/`wait` dates land on a user's *third or
  later* future monthly payroll date (e.g. request_03: 5 consecutive months
  of an identical "Payroll credit" in history, request_date 2019-09-03,
  ground truth earliest date 2019-11-15 — the third projected cycle ahead).
  A first draft of this module projected only expenses and treated
  "do not invent unsupported future income" as "never project income" —
  that was wrong and is corrected here: the rule is about not inventing
  income with no historical support (a bonus, a raise, a one-off), not about
  refusing to project a clearly recurring, multiply-repeated salary. Income
  recurrence uses the same detection as expenses, but the conservative
  amount is the *minimum* of the last 3 occurrences (don't overestimate
  incoming money), where expenses use the *maximum* (don't underestimate
  outgoing money) — both directions favor the same thing: never overstate
  the user's safety margin.
- Monthly series must be projected by calendar month, not by adding a flat
  30 days repeatedly. First draft did the latter and it drifted: request_03's
  history is "Payroll credit" on the 15th of five straight months, so
  last_historical_date=2019-08-15, cadence snaps to the 30-day standard
  bucket, and repeatedly adding 30 days gives 2019-09-14, -10-14, -11-13 —
  drifting a day earlier every cycle because real months aren't 30 days.
  Ground truth for that same series is 2019-11-15, exactly 3 calendar months
  later on the same day-of-month. Fixed by stepping monthly-bucket series
  with calendar-month arithmetic (clamped to the target month's last day)
  and only using flat day-count stepping for the weekly/biweekly/custom
  buckets, where it's exact.
"""
from __future__ import annotations

import calendar
import statistics
from collections import defaultdict
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from core.fx import MissingExchangeRateError, to_home_currency
from core.models import CashItem, Event, Profile, SeriesAdjustment

RECURRING_EVENT_TYPES = frozenset({"expense", "subscription", "debt_payment", "income"})
# A real explicit future row and a projected occurrence of the same recurring
# series can be the SAME economic event recorded twice -- most often
# "Next confirmed salary" landing exactly on the projected payroll date for an
# identical amount. Measured across the dataset: 45 such credit collisions
# (44 with matching amounts) and 9 debit ones. Counting both double-counts the
# paycheck and biases every downstream number optimistic, so a projection is
# suppressed when an explicit row of the same category and direction lands
# within this many days for an amount within this tolerance. Requiring the
# amount to match is what keeps genuinely unrelated same-category one-offs
# (a "Pending fuel authorization" vs a recurring "Commuter pass") additive.
COLLISION_TOLERANCE_DAYS = 3
COLLISION_AMOUNT_TOLERANCE = Decimal("0.15")
MIN_HISTORY_FOR_RECURRENCE = 3
# Income gets a lower bar: verified against sample_requests.csv's request_15
# (a brand-new job, only 2 "First-job payroll" settlements before the
# request, same amount and day-of-month both times) where ground truth
# already treats it as ongoing salary. Two consistent occurrences of a
# stated payroll credit is reasonable confirmation of an ongoing job; expenses
# keep the stricter bar since false positives there are cheaper.
MIN_HISTORY_FOR_INCOME = 2
STANDARD_CADENCES_DAYS = (7, 14, 30)
CADENCE_TOLERANCE_DAYS = 3
GAP_STDEV_MAX_DAYS = 5


class UnresolvedAmountError(Exception):
    """Raised when a blank-amount event that is needed for a forecast has no
    resolved value yet. Never silently treated as zero."""

    def __init__(self, event_id: str):
        self.event_id = event_id
        super().__init__(f"event {event_id} has a blank amount with no resolved override")


@dataclass(frozen=True)
class RecurringSeries:
    category: str
    description: str
    direction: str  # "debit" (recurring expense/subscription/debt_payment) or "credit" (recurring income)
    currency: str
    cadence_days: int
    is_monthly: bool  # step by calendar month (same day-of-month) rather than a flat day count
    conservative_amount: Decimal
    last_historical_date: date
    flexibility: Optional[str]
    minimum_allowed_amount: Optional[Decimal]
    representative_event_id: str  # last historical settled event_id — used to cite this
    # ongoing recurring commitment in spending_changes_needed, since only a
    # real financial_events.csv row can be cited there and a projected future
    # occurrence has no row of its own.


@dataclass
class UserState:
    profile: Profile
    events_by_id: dict[str, Event]
    settled_history: list[Event]
    future_events: list[Event]  # status in {scheduled, pending}, any date (caller filters window)
    fx_exclusion_notes: list[str]
    adjustment_notes: list[str] = dataclass_field(default_factory=list)


def _resolve_cadence(median_gap_days: float) -> int:
    for standard in STANDARD_CADENCES_DAYS:
        if abs(median_gap_days - standard) <= CADENCE_TOLERANCE_DAYS:
            return standard
    return max(1, round(median_gap_days))


def _add_calendar_months(d: date, months: int) -> date:
    total = d.month - 1 + months
    year = d.year + total // 12
    month = total % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _next_occurrence(previous: date, cadence_days: int, is_monthly: bool) -> date:
    if is_monthly:
        return _add_calendar_months(previous, 1)
    return previous + timedelta(days=cadence_days)


def detect_recurring_series(settled_history: list[Event]) -> list[RecurringSeries]:
    def cash_date(event: Event) -> date:
        """Settled recurrence follows when cash actually posted.

        Event date can describe initiation or the nominal payroll date. The
        balance changes on settlement_date; falling back keeps this helper
        robust for synthetic settled rows that omit it.
        """
        return event.settlement_date or event.event_date

    # Grouped by (category, description): a category like "groceries" is
    # split across several vendor descriptions (Neighbourhood grocer, Bulk
    # pantry shop, ...) whose *pooled* category-level cadence can look
    # perfectly regular (verified: user_23's groceries, 25 occurrences, every
    # 7 days, stdev=0) even though it is not one economically continuous
    # commitment. Tried category-only pooling with both max-of-3 and mean-of-3
    # as the conservative amount; both scored substantially worse against
    # sample_requests.csv (52-60% vs 68-80% on affordability_status /
    # recommended_payment_method) than per-(category, description) grouping,
    # apparently because a flat per-occurrence amount compounds across many
    # weekly occurrences and overshoots real spending. Reverted to
    # per-description grouping, which is also what correctly separates a
    # single stable commitment (rent, salary, a subscription — one
    # description per category anyway, so this is a no-op there) from a
    # basket of unrelated small purchases that merely share a category label.
    groups: dict[tuple[str, str, str], list[Event]] = defaultdict(list)
    for e in settled_history:
        if e.event_type in RECURRING_EVENT_TYPES and e.direction in ("debit", "credit"):
            groups[(e.category, e.description, e.direction)].append(e)

    # Termination markers: an explicit amendment ("Final employer payroll")
    # that ends an otherwise-recurring series, even though it has a different
    # description than the series itself and so isn't grouped with it above.
    # Verified against sample_requests.csv's request_05: user_05's "Payroll
    # credit" salary recurs cleanly for months, then a single "Final employer
    # payroll" event ends it; the request is dated after that. Ground truth
    # correctly stops counting salary from that point (a tiny amount_safe_to_pay,
    # not_affordable), which a first draft missed entirely by exact-matching
    # descriptions and still projecting "Payroll credit" forward indefinitely.
    # AGENTS.md's conflict-resolution order puts an explicit amendment first,
    # ahead of continuing an established pattern -- this is exactly that case.
    termination_dates: dict[tuple[str, str], date] = {}
    for e in settled_history:
        if "final" in e.description.lower():
            key = (e.category, e.direction)
            d = cash_date(e)
            termination_dates[key] = max(termination_dates.get(key, d), d)

    series: list[RecurringSeries] = []
    for (category, description, direction), evs in groups.items():
        if (category, direction) in termination_dates:
            continue  # an explicit "final" event ended this recurring commitment
        min_history = MIN_HISTORY_FOR_INCOME if direction == "credit" else MIN_HISTORY_FOR_RECURRENCE
        if len(evs) < min_history:
            continue
        evs = sorted(evs, key=cash_date)
        if direction == "credit":
            # A confirmed, ongoing income stream in this dataset either pays
            # the identical amount every time, or steps cleanly from one flat
            # level to another (a raise/cut) -- verified dataset-wide: every
            # salary-type series with an exact repeated amount among its
            # occurrences is a stable-or-stepped series (23/23, confirmed by
            # eye), while every series where every single occurrence differs
            # is commission/freelance/gig-style variable pay (96/96). A
            # series with zero repeats is therefore excluded here, regardless
            # of how low its coefficient of variation happens to be by
            # chance on a small sample -- see request_11 in EXPERIMENTS.md.
            amounts_seen = [e.amount for e in evs if e.amount is not None]
            if len(set(amounts_seen)) == len(amounts_seen):
                continue  # every occurrence a different amount: not confirmed income
        gaps = [(cash_date(evs[i + 1]) - cash_date(evs[i])).days for i in range(len(evs) - 1)]
        if not gaps:
            continue
        if len(gaps) > 1 and statistics.pstdev(gaps) > GAP_STDEV_MAX_DAYS:
            continue  # history too irregular to treat as recurring
        cadence = _resolve_cadence(statistics.median(gaps))
        recent = evs[-3:]
        amounts = [e.amount for e in recent if e.amount is not None]
        if not amounts:
            continue
        # Conservative always means "don't overestimate the user's safety
        # margin": the highest recent amount for a recurring debit (assume
        # the expense stays as large as it's recently been), the lowest
        # recent amount for a recurring credit (don't count on income being
        # as high as its best recent instance).
        conservative_amount = max(amounts) if direction == "debit" else min(amounts)
        last = evs[-1]
        series.append(
            RecurringSeries(
                category=category,
                direction=direction,
                description=description,
                currency=last.currency,
                cadence_days=cadence,
                is_monthly=(cadence == 30),
                conservative_amount=conservative_amount,
                last_historical_date=cash_date(last),
                flexibility=last.flexibility,
                minimum_allowed_amount=last.minimum_allowed_amount,
                representative_event_id=last.event_id,
            )
        )
    return series


def build_user_state(user_id: str, profile: Profile, all_events: list[Event]) -> UserState:
    user_events = [e for e in all_events if e.user_id == user_id]
    events_by_id = {e.event_id: e for e in user_events}
    settled_history = [e for e in user_events if e.status == "settled"]
    future_events = [e for e in user_events if e.status in ("scheduled", "pending")]
    return UserState(
        profile=profile,
        events_by_id=events_by_id,
        settled_history=settled_history,
        future_events=future_events,
        fx_exclusion_notes=[],
    )


def _event_amount_home_currency(
    e: Event,
    home_currency: str,
    rates: dict[tuple[date, str, str], Decimal],
    amount_overrides: dict[str, Decimal],
    fx_exclusion_notes: list[str],
) -> Optional[Decimal]:
    amount = amount_overrides.get(e.event_id, e.amount)
    if amount is None:
        raise UnresolvedAmountError(e.event_id)
    try:
        return to_home_currency(amount, e.currency, e.settlement_date, home_currency, rates)
    except MissingExchangeRateError as exc:
        fx_exclusion_notes.append(
            f"excluded {e.event_id} ({e.settlement_date.isoformat()}, {e.currency}): {exc}"
        )
        return None


def _adjusted_series_amount(
    series: RecurringSeries,
    occurrence: date,
    occurrence_index: int,
    adjustments: list[SeriesAdjustment],
    notes: list[str],
) -> Decimal:
    """Apply validated amendments to one projected occurrence of a series.

    Amendments are applied in effective-date order. An absolute new_amount
    replaces the projected figure; a change_pct scales it. An absolute amount
    stated in a different currency than the series is skipped rather than
    applied across a unit mismatch.
    """
    amount = series.conservative_amount
    relevant = [
        a
        for a in adjustments
        if a.category == series.category and a.direction == series.direction
    ]
    for adj in sorted(relevant, key=lambda a: (a.effective_date or date.min)):
        if adj.effective_date is not None and occurrence < adj.effective_date:
            continue
        if adj.scope == "next_occurrence_only" and occurrence_index > 0:
            continue
        if adj.new_amount is not None:
            if adj.currency and adj.currency != series.currency:
                notes.append(
                    f"skipped amendment from {adj.source}: amount stated in {adj.currency} "
                    f"but {series.category} series is in {series.currency}"
                )
                continue
            amount = adj.new_amount
        elif adj.change_pct is not None:
            amount = amount * (Decimal("100") + adj.change_pct) / Decimal("100")
        notes.append(
            f"applied amendment from {adj.source} to {series.category} on "
            f"{occurrence.isoformat()}: amount now {amount}"
        )
    return amount


def get_forecast_cash_items(
    state: UserState,
    window_start: date,
    window_days: int,
    rates: dict[tuple[date, str, str], Decimal],
    amount_overrides: Optional[dict[str, Decimal]] = None,
    adjustments: Optional[list[SeriesAdjustment]] = None,
) -> list[CashItem]:
    """Real explicit future events (scheduled/pending, in-window) plus
    projected recurring series, as home-currency CashItems sorted by date.

    `adjustments` are validated recurring-series amendments (see
    core/models.SeriesAdjustment); they are applied mechanically here.
    """
    amount_overrides = amount_overrides or {}
    adjustments = adjustments or []
    home_currency = state.profile.home_currency
    window_end = window_start + timedelta(days=window_days)
    items: list[CashItem] = []

    for e in state.future_events:
        if not (window_start <= e.settlement_date <= window_end):
            continue
        if e.direction == "non_cash":
            continue
        if e.direction == "credit" and e.status != "scheduled":
            continue  # pending credits: not counted until they settle
        home_amount = _event_amount_home_currency(
            e, home_currency, rates, amount_overrides, state.fx_exclusion_notes
        )
        if home_amount is None:
            continue
        signed = home_amount if e.direction == "credit" else -home_amount
        items.append(
            CashItem(
                when=e.settlement_date,
                signed_amount=signed,
                event_id=e.event_id,
                category=e.category,
                flexibility=e.flexibility,
                minimum_allowed_amount=e.minimum_allowed_amount,
                is_projected=False,
            )
        )

    explicit_items = list(items)  # real rows only; projections are appended below

    def _already_recorded_explicitly(category: str, direction: str, when: date, amount: Decimal) -> bool:
        want_credit = direction == "credit"
        for existing in explicit_items:
            if existing.category != category:
                continue
            if (existing.signed_amount > 0) != want_credit:
                continue
            if abs((existing.when - when).days) > COLLISION_TOLERANCE_DAYS:
                continue
            if abs(abs(existing.signed_amount) - amount) <= amount * COLLISION_AMOUNT_TOLERANCE:
                return True
        return False

    for series in detect_recurring_series(state.settled_history):
        occurrence = _next_occurrence(series.last_historical_date, series.cadence_days, series.is_monthly)
        occurrence_index = 0
        while occurrence <= window_end:
            if occurrence >= window_start:
                projected_amount = _adjusted_series_amount(
                    series, occurrence, occurrence_index, adjustments, state.adjustment_notes
                )
                if projected_amount <= 0:
                    # An amendment zeroed this series (a contract ended, a
                    # final payroll): emit nothing rather than a 0-value item.
                    occurrence_index += 1
                    occurrence = _next_occurrence(occurrence, series.cadence_days, series.is_monthly)
                    continue
                try:
                    home_amount = to_home_currency(
                        projected_amount, series.currency, occurrence, home_currency, rates
                    )
                except MissingExchangeRateError:
                    home_amount = None
                    state.fx_exclusion_notes.append(
                        f"excluded projected {series.category}/{series.description} on "
                        f"{occurrence.isoformat()}: no exchange rate"
                    )
                if home_amount is not None and _already_recorded_explicitly(
                    series.category, series.direction, occurrence, home_amount
                ):
                    state.adjustment_notes.append(
                        f"suppressed projected {series.category} on {occurrence.isoformat()}: "
                        f"already present as an explicit scheduled/pending row"
                    )
                    home_amount = None
                if home_amount is not None:
                    signed = home_amount if series.direction == "credit" else -home_amount
                    items.append(
                        CashItem(
                            when=occurrence,
                            signed_amount=signed,
                            event_id=series.representative_event_id,
                            category=series.category,
                            flexibility=series.flexibility,
                            minimum_allowed_amount=series.minimum_allowed_amount,
                            is_projected=True,
                        )
                    )
                occurrence_index += 1
            occurrence = _next_occurrence(occurrence, series.cadence_days, series.is_monthly)

    items.sort(key=lambda c: c.when)
    return items
