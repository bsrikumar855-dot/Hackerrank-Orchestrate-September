"""CSV loading for dataset/. Read-only, no writes here. Every parser is total for the
columns the dataset actually contains — a genuinely malformed row raises rather than
silently coercing, so bad input surfaces immediately instead of poisoning a forecast.
"""
from __future__ import annotations

import csv
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

from core.models import (
    Event,
    ExchangeRate,
    ImageRef,
    Message,
    PaymentOption,
    Profile,
    Request,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = REPO_ROOT / "dataset"


def _dec(s: str) -> Decimal:
    try:
        return Decimal(s.strip())
    except (InvalidOperation, AttributeError) as exc:
        raise ValueError(f"not a decimal: {s!r}") from exc


def _opt_dec(s: str) -> Optional[Decimal]:
    s = (s or "").strip()
    return _dec(s) if s else None


def _date(s: str) -> date:
    return datetime.strptime(s.strip(), "%Y-%m-%d").date()


def _opt_date(s: str) -> Optional[date]:
    s = (s or "").strip()
    return _date(s) if s else None


def _opt_str(s: str) -> Optional[str]:
    s = (s or "").strip()
    return s or None


def _opt_int(s: str) -> Optional[int]:
    s = (s or "").strip()
    return int(s) if s else None


def _pipe_set(s: str) -> frozenset[str]:
    s = (s or "").strip()
    return frozenset(x for x in s.split("|") if x)


def _pipe_tuple(s: str) -> tuple[str, ...]:
    s = (s or "").strip()
    return tuple(x for x in s.split("|") if x)


def _rows(path: Path):
    with path.open(newline="", encoding="utf-8") as fh:
        yield from csv.DictReader(fh)


def load_profiles(path: Path = DATASET_DIR / "financial_profiles.csv") -> dict[str, Profile]:
    out: dict[str, Profile] = {}
    for r in _rows(path):
        out[r["user_id"]] = Profile(
            user_id=r["user_id"],
            home_currency=r["home_currency"].strip(),
            current_available_balance=_dec(r["current_available_balance"]),
            minimum_balance_to_keep=_dec(r["minimum_balance_to_keep"]),
            financial_priorities=_pipe_tuple(r["financial_priorities"]),
            protect_categories=_pipe_set(r["expense_categories_to_protect"]),
            reduce_categories=_pipe_set(r["expense_categories_user_is_willing_to_reduce"]),
            stop_categories=_pipe_set(r["expense_categories_user_is_willing_to_stop"]),
            payment_methods_user_will_consider=_pipe_set(r["payment_methods_user_will_consider"]),
            max_installment_months=_opt_int(r["max_installment_months"]),
        )
    return out


def load_events(path: Path = DATASET_DIR / "financial_events.csv") -> list[Event]:
    out: list[Event] = []
    for r in _rows(path):
        out.append(
            Event(
                event_id=r["event_id"],
                user_id=r["user_id"],
                event_type=r["event_type"],
                description=r["description"],
                category=r["category"],
                direction=r["direction"],
                amount=_opt_dec(r["amount"]),
                currency=r["currency"].strip(),
                event_date=_date(r["event_date"]),
                settlement_date=_opt_date(r["settlement_date"]),
                status=r["status"].strip(),
                linked_event_id=_opt_str(r["linked_event_id"]),
                flexibility=_opt_str(r["flexibility"]),
                minimum_allowed_amount=_opt_dec(r["minimum_allowed_amount"]),
            )
        )
    return out


def load_exchange_rates(path: Path = DATASET_DIR / "exchange_rates.csv") -> dict[tuple[date, str, str], Decimal]:
    out: dict[tuple[date, str, str], Decimal] = {}
    for r in _rows(path):
        key = (_date(r["rate_date"]), r["from_currency"].strip(), r["to_currency"].strip())
        out[key] = _dec(r["rate"])
    return out


def load_requests(path: Path) -> list[Request]:
    out: list[Request] = []
    for r in _rows(path):
        out.append(
            Request(
                request_id=r["request_id"],
                user_id=r["user_id"],
                request_date=_date(r["request_date"]),
                request_type=r["request_type"],
                requested_amount=_dec(r["requested_amount"]),
                desired_completion_date=_date(r["desired_completion_date"]),
                allows_partial_payment=r["allows_partial_payment"].strip().lower() == "true",
                request_text=r["request_text"],
            )
        )
    return out


def load_payment_options(path: Path = DATASET_DIR / "request_payment_options.csv") -> dict[str, list[PaymentOption]]:
    out: dict[str, list[PaymentOption]] = {}
    for r in _rows(path):
        opt = PaymentOption(
            payment_option_id=r["payment_option_id"],
            request_id=r["request_id"],
            payment_method=r["payment_method"].strip(),
            payment_amount=_dec(r["payment_amount"]),
            number_of_payments=int(r["number_of_payments"]),
            first_payment_date=_date(r["first_payment_date"]),
            payment_frequency_days=_opt_int(r["payment_frequency_days"]),
            financing_fee=_dec(r["financing_fee"] or "0"),
            total_payable_amount=_dec(r["total_payable_amount"]),
        )
        out.setdefault(opt.request_id, []).append(opt)
    return out


def load_messages(path: Path = DATASET_DIR / "messages.csv") -> list[Message]:
    out: list[Message] = []
    for r in _rows(path):
        out.append(
            Message(
                message_id=r["message_id"],
                user_id=r["user_id"],
                request_id=_opt_str(r["request_id"]),
                related_event_id=_opt_str(r["related_event_id"]),
                sent_at=r["sent_at"],
                source_type=r["source_type"],
                message_text=r["message_text"],
            )
        )
    return out


def load_images(path: Path = DATASET_DIR / "images.csv") -> list[ImageRef]:
    out: list[ImageRef] = []
    for r in _rows(path):
        out.append(
            ImageRef(
                image_id=r["image_id"],
                user_id=r["user_id"],
                request_id=_opt_str(r["request_id"]),
                related_event_id=_opt_str(r["related_event_id"]),
            )
        )
    return out
