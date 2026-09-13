"""Plain data containers for the deterministic core. No model calls anywhere in this module."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional


@dataclass(frozen=True)
class Profile:
    user_id: str
    home_currency: str
    current_available_balance: Decimal
    minimum_balance_to_keep: Decimal
    financial_priorities: tuple[str, ...]
    protect_categories: frozenset[str]
    reduce_categories: frozenset[str]
    stop_categories: frozenset[str]
    payment_methods_user_will_consider: frozenset[str]
    max_installment_months: Optional[int]


@dataclass(frozen=True)
class Event:
    event_id: str
    user_id: str
    event_type: str
    description: str
    category: str
    direction: str  # debit | credit | non_cash
    amount: Optional[Decimal]
    currency: str
    event_date: date
    settlement_date: Optional[date]  # blank only for status="unrealized" (never settles)
    status: str  # settled | pending | scheduled | cancelled | failed | unrealized
    linked_event_id: Optional[str]
    flexibility: Optional[str]  # fixed | reducible | stoppable | reducible_or_stoppable | None
    minimum_allowed_amount: Optional[Decimal]


@dataclass(frozen=True)
class ExchangeRate:
    rate_date: date
    from_currency: str
    to_currency: str
    rate: Decimal


@dataclass(frozen=True)
class Request:
    request_id: str
    user_id: str
    request_date: date
    request_type: str
    requested_amount: Decimal
    desired_completion_date: date
    allows_partial_payment: bool
    request_text: str


@dataclass(frozen=True)
class PaymentOption:
    payment_option_id: str
    request_id: str
    payment_method: str  # full_payment | installments (partial_payment never appears here)
    payment_amount: Decimal
    number_of_payments: int
    first_payment_date: date
    payment_frequency_days: Optional[int]
    financing_fee: Decimal
    total_payable_amount: Decimal


@dataclass(frozen=True)
class Message:
    message_id: str
    user_id: str
    request_id: Optional[str]
    related_event_id: Optional[str]
    sent_at: str
    source_type: str
    message_text: str


@dataclass(frozen=True)
class ImageRef:
    image_id: str
    user_id: str
    request_id: Optional[str]
    related_event_id: Optional[str]


@dataclass(frozen=True)
class SeriesAdjustment:
    """A validated amendment to a recurring series (a new salary figure, a
    rent increase, a temporary pay cut), applied deterministically to that
    series' projected occurrences.

    The agent layer produces these from message evidence, but this type
    carries no model concepts and core/ never imports agent/ -- core applies
    an adjustment mechanically and has no idea where it came from.
    """

    category: str
    direction: str  # debit | credit
    scope: str  # ongoing | next_occurrence_only
    new_amount: Optional[Decimal] = None
    change_pct: Optional[Decimal] = None
    currency: Optional[str] = None
    effective_date: Optional[date] = None
    source: str = ""


@dataclass(frozen=True)
class CashItem:
    """One dated cash-flow contribution used by the 90-day simulator.

    ``event_id`` is always a real financial_events.csv id. For a projected
    recurring instance (``is_projected=True``) it is the *representative*
    id — the most recent historical settlement of that recurring series —
    since the projected date itself has no row of its own. That id is what
    `spending_changes_needed` cites to mean "stop/reduce this ongoing
    recurring commitment going forward", not literally that one past row.
    """

    when: date
    signed_amount: Decimal  # positive = credit, negative = debit, home currency
    event_id: Optional[str]
    category: str
    flexibility: Optional[str]
    minimum_allowed_amount: Optional[Decimal]
    is_projected: bool = False
