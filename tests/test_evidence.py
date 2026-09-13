"""Validation guardrails on model-extracted evidence. No API calls: these
test the code that decides whether to *trust* an extraction, which is where a
hallucinated figure would otherwise reach the forecast.
"""
from datetime import date
from decimal import Decimal

from agent.evidence import EvidenceCache, _validate_amendment, extract_confirmed_future_credits
from core.models import Message

KNOWN = {
    ("salary", "credit"): Decimal("30000"),
    ("rent", "debit"): Decimal("1000"),
}
REQUEST_DATE = date(2025, 8, 1)


def _raw(**overrides):
    base = {
        "kind": "income_change",
        "category": "salary",
        "new_amount": 33000,
        "change_pct": None,
        "currency": "IDR",
        "effective_date": "2025-08-15",
        "scope": "ongoing",
        "confidence": "high",
        "source_message_id": "message_01",
        "reason": "salary rises to 33000 effective 2025-08-15",
    }
    base.update(overrides)
    return base


def test_accepts_a_well_formed_salary_amendment():
    amendment, reason = _validate_amendment(_raw(), KNOWN, REQUEST_DATE)
    assert reason is None
    assert amendment.new_amount == Decimal("33000")
    assert amendment.effective_date == date(2025, 8, 15)


def test_rejects_category_the_user_has_no_history_in():
    amendment, reason = _validate_amendment(_raw(category="yacht_maintenance"), KNOWN, REQUEST_DATE)
    assert amendment is None
    assert "no matching recurring" in reason


def test_rejects_an_implausible_magnitude_jump():
    # a 100x "salary" is a misread (decimal point, wrong line on the payslip),
    # not a raise -- it must never reach the forecast
    amendment, reason = _validate_amendment(_raw(new_amount=3000000), KNOWN, REQUEST_DATE)
    assert amendment is None
    assert "plausible" in reason


def test_rejects_both_amount_and_percentage():
    amendment, reason = _validate_amendment(_raw(change_pct=12), KNOWN, REQUEST_DATE)
    assert amendment is None
    assert "exactly one" in reason


def test_rejects_neither_amount_nor_percentage():
    amendment, reason = _validate_amendment(_raw(new_amount=None), KNOWN, REQUEST_DATE)
    assert amendment is None
    assert "exactly one" in reason


def test_rejects_unparseable_effective_date():
    amendment, reason = _validate_amendment(_raw(effective_date="next Tuesday"), KNOWN, REQUEST_DATE)
    assert amendment is None
    assert "effective_date" in reason


def test_rejects_effective_date_far_outside_the_users_timeline():
    amendment, reason = _validate_amendment(_raw(effective_date="2040-01-01"), KNOWN, REQUEST_DATE)
    assert amendment is None
    assert "implausibly far" in reason


def test_low_confidence_optimistic_amendment_is_dropped():
    # a low-confidence *raise* would inflate the safety margin -> drop it
    amendment, reason = _validate_amendment(
        _raw(confidence="low", new_amount=33000), KNOWN, REQUEST_DATE
    )
    assert amendment is None
    assert "optimistic" in reason


def test_low_confidence_conservative_amendment_is_kept():
    # a low-confidence *pay cut* reduces the safety margin -> safe to apply
    amendment, reason = _validate_amendment(
        _raw(confidence="low", new_amount=21000), KNOWN, REQUEST_DATE
    )
    assert reason is None
    assert amendment.new_amount == Decimal("21000")


def test_low_confidence_expense_increase_is_kept():
    amendment, reason = _validate_amendment(
        _raw(kind="expense_change", category="rent", confidence="low", new_amount=None, change_pct=12),
        KNOWN,
        REQUEST_DATE,
    )
    assert reason is None
    assert amendment.change_pct == Decimal("12")


def test_accepts_income_termination_as_zero():
    # "The current seasonal contract has ended. No off-season income or
    # renewal has been confirmed." -> the salary series stops. Zero is the
    # meaningful value here, and it is the conservative direction, so it must
    # survive validation (an earlier draft rejected it as "must be positive",
    # which silently kept projecting phantom salary).
    amendment, reason = _validate_amendment(
        _raw(new_amount=0, reason="current seasonal contract has ended"), KNOWN, REQUEST_DATE
    )
    assert reason is None
    assert amendment.new_amount == Decimal("0")


def test_accepts_low_confidence_income_termination():
    amendment, reason = _validate_amendment(
        _raw(new_amount=0, confidence="low"), KNOWN, REQUEST_DATE
    )
    assert reason is None
    assert amendment.new_amount == Decimal("0")


def test_expense_termination_needs_high_confidence():
    amendment, reason = _validate_amendment(
        _raw(kind="expense_change", category="rent", new_amount=0, confidence="medium"),
        KNOWN,
        REQUEST_DATE,
    )
    assert amendment is None
    assert "optimistic" in reason


def test_rejects_negative_amount():
    amendment, reason = _validate_amendment(_raw(new_amount=-500), KNOWN, REQUEST_DATE)
    assert amendment is None
    assert "negative" in reason


def test_rejects_out_of_range_percentage():
    amendment, reason = _validate_amendment(
        _raw(kind="expense_change", category="rent", new_amount=None, change_pct=5000),
        KNOWN,
        REQUEST_DATE,
    )
    assert amendment is None
    assert "outside" in reason


def test_confirmed_first_salary_becomes_one_credit_without_inventing_recurrence(tmp_path):
    cache = EvidenceCache(tmp_path / "cache.json")
    cache.put_amendments("user_new", {"amendments": [{
        "kind": "income_change", "category": "salary", "new_amount": 16910000,
        "change_pct": None, "currency": "IDR", "effective_date": "2025-11-15",
        "scope": "ongoing", "confidence": "high", "source_message_id": "m1",
    }]})
    message = Message(
        "m1", "user_new", None, None, "2025-11-01", "employer",
        "Gaji pertama Anda sebesar IDR 16910000 dijadwalkan pada 2025-11-15.",
    )
    credits, rejected = extract_confirmed_future_credits(
        "user_new", [message], {}, "IDR", date(2025, 11, 1), cache,
    )
    assert rejected == []
    assert [(c.amount, c.settlement_date) for c in credits] == [
        (Decimal("16910000"), date(2025, 11, 15))
    ]


def test_first_salary_requires_fact_tokens_in_owned_source(tmp_path):
    cache = EvidenceCache(tmp_path / "cache.json")
    cache.put_amendments("user_new", {"amendments": [{
        "kind": "income_change", "category": "salary", "new_amount": 99999999,
        "change_pct": None, "currency": "IDR", "effective_date": "2025-11-15",
        "scope": "ongoing", "confidence": "high", "source_message_id": "m1",
    }]})
    message = Message(
        "m1", "user_new", None, None, "2025-11-01", "employer",
        "Your first salary of IDR 16910000 is scheduled for 2025-11-15.",
    )
    credits, rejected = extract_confirmed_future_credits(
        "user_new", [message], {}, "IDR", date(2025, 11, 1), cache,
    )
    assert credits == []
    assert rejected
