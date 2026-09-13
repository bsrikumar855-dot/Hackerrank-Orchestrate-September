"""Proves the 6-level tie-break order is actually implemented as specified,
not just 'mostly right' — every criterion disagrees between the two plans in
each pairwise case below, so only a correctly-ordered comparison chain picks
the intended winner for the right reason.
"""
from datetime import date
from decimal import Decimal

from core.ranking import Plan, best_plan, rank_plans


def _plan(**overrides):
    base = dict(
        method="installments",
        payments=((date(2026, 1, 10), Decimal("100")),),
        completes_by_deadline=True,
        requires_spending_changes=False,
        total_paid=Decimal("100"),
        start_date=date(2026, 1, 10),
        payment_option_id="payment_option_05",
    )
    base.update(overrides)
    return Plan(**base)


def test_criterion_1_deadline_beats_everything_else():
    # A misses the deadline but is otherwise strictly better on every other
    # criterion (cheaper, earlier, fewer payments, lower option id). B is
    # worse on all of those but meets the deadline. B must still win.
    a = _plan(
        completes_by_deadline=False,
        requires_spending_changes=False,
        total_paid=Decimal("50"),
        start_date=date(2026, 1, 1),
        payments=((date(2026, 1, 1), Decimal("50")),),
        payment_option_id="payment_option_01",
    )
    b = _plan(
        completes_by_deadline=True,
        requires_spending_changes=True,
        total_paid=Decimal("999"),
        start_date=date(2026, 6, 1),
        payments=tuple((date(2026, m, 1), Decimal("333")) for m in (6, 7, 8)),
        payment_option_id="payment_option_09",
    )
    assert best_plan([a, b]) is b


def test_criterion_2_no_spending_changes_beats_cost_start_and_count():
    # Both meet the deadline. A needs spending changes but is cheaper,
    # earlier, and uses fewer payments. B needs no spending changes but is
    # worse on all three. B must still win.
    a = _plan(
        requires_spending_changes=True,
        total_paid=Decimal("50"),
        start_date=date(2026, 1, 1),
        payments=((date(2026, 1, 1), Decimal("50")),),
        payment_option_id="payment_option_01",
    )
    b = _plan(
        requires_spending_changes=False,
        total_paid=Decimal("999"),
        start_date=date(2026, 6, 1),
        payments=tuple((date(2026, m, 1), Decimal("333")) for m in (6, 7, 8)),
        payment_option_id="payment_option_09",
    )
    assert best_plan([a, b]) is b


def test_criterion_3_lower_total_cost_beats_earlier_start_and_fewer_payments():
    a = _plan(
        total_paid=Decimal("200"),
        start_date=date(2026, 1, 1),
        payments=((date(2026, 1, 1), Decimal("200")),),
        payment_option_id="payment_option_01",
    )
    b = _plan(
        total_paid=Decimal("150"),
        start_date=date(2026, 6, 1),
        payments=tuple((date(2026, m, 1), Decimal("50")) for m in (6, 7, 8)),
        payment_option_id="payment_option_09",
    )
    assert best_plan([a, b]) is b


def test_criterion_4_earlier_start_beats_fewer_payments():
    a = _plan(
        total_paid=Decimal("100"),
        start_date=date(2026, 3, 1),
        payments=((date(2026, 3, 1), Decimal("100")),),
        payment_option_id="payment_option_01",
    )
    b = _plan(
        total_paid=Decimal("100"),
        start_date=date(2026, 1, 1),
        payments=tuple((date(2026, m, 1), Decimal("50")) for m in (1, 2)),
        payment_option_id="payment_option_09",
    )
    assert best_plan([a, b]) is b


def test_criterion_5_fewer_payments_beats_lower_option_id():
    a = _plan(
        total_paid=Decimal("100"),
        start_date=date(2026, 1, 1),
        payments=tuple((date(2026, m, 1), Decimal("33.33")) for m in (1, 2, 3)),
        payment_option_id="payment_option_01",
    )
    b = _plan(
        total_paid=Decimal("100"),
        start_date=date(2026, 1, 1),
        payments=((date(2026, 1, 1), Decimal("50")), (date(2026, 2, 1), Decimal("50"))),
        payment_option_id="payment_option_09",
    )
    assert best_plan([a, b]) is b


def test_criterion_6_lowest_payment_option_id_is_final_tiebreak():
    a = _plan(payment_option_id="payment_option_09")
    b = _plan(payment_option_id="payment_option_02")
    assert best_plan([a, b]) is b


def test_full_chain_every_criterion_disagrees():
    # Constructed so each of the 6 criteria, taken alone, would pick the
    # OTHER plan than the correct winner — only the exact specified priority
    # order gets this right.
    winner = _plan(
        completes_by_deadline=True,       # best on #1
        requires_spending_changes=True,   # worse on #2
        total_paid=Decimal("500"),        # worse on #3
        start_date=date(2026, 1, 1),      # best on #4
        payments=((date(2026, 1, 1), Decimal("500")),),  # best on #5 (1 payment)
        payment_option_id="payment_option_08",  # worse on #6
    )
    loser = _plan(
        completes_by_deadline=False,      # worse on #1 -> disqualifying
        requires_spending_changes=False,  # best on #2
        total_paid=Decimal("400"),        # best on #3
        start_date=date(2026, 2, 1),      # worse on #4
        payments=((date(2026, 2, 1), Decimal("200")), (date(2026, 3, 1), Decimal("200"))),  # worse on #5
        payment_option_id="payment_option_01",  # best on #6
    )
    assert best_plan([winner, loser]) is winner
    assert rank_plans([loser, winner]) == [winner, loser]
