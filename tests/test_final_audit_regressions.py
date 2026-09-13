"""Adversarial evidence contracts and installment/spending counterexamples."""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
import pytest
from agent import evidence
from agent.evidence import EvidenceCache, extract_amendments, _validate_amendment
from core.models import CashItem, Message, PaymentOption
from core.planning import build_decision
from tests.test_planning import _profile, _request


@pytest.mark.parametrize('value', ['NaN', 'Infinity', '-Infinity'])
@pytest.mark.parametrize('field', ['new_amount', 'change_pct'])
def test_nonfinite_amendments_are_rejected(value, field):
    raw = dict(kind='income_change', category='salary', **{field: value})
    amendment, reason = _validate_amendment(raw, {('salary','credit'): Decimal('100')}, date(2026,1,1))
    assert amendment is None
    assert 'finite' in reason


def test_cross_user_citation_cannot_amend_income(tmp_path):
    cache = EvidenceCache(tmp_path/'cache.json')
    cache.put_amendments('u', {'amendments': [dict(kind='income_change',category='salary',new_amount=200,source_message_id='other_message')]})
    messages = [Message('own_message','u',None,None,'2026-01-01','employer','Salary unchanged.')]
    amendments, _, rejected = extract_amendments('u',messages,{('salary','credit'):Decimal('100')},'EUR',date(2026,1,1),None,cache)
    assert not amendments and 'source_message_id' in rejected[0]


@pytest.mark.parametrize('amount,currency,confidence', [('100','USD','high'),('100','EUR','low'),('Infinity','EUR','high'),('NaN','EUR','high')])
def test_vision_rejects_unusable_units_or_numbers(tmp_path, monkeypatch, amount, currency, confidence):
    monkeypatch.setattr(evidence, 'IMAGES_DIR', tmp_path)
    (tmp_path/'image.png').write_bytes(b'fake image consumed only by stub')
    event = SimpleNamespace(event_id='e',currency='EUR',description='bill',category='rent',event_type='expense',direction='debit',event_date=date(2026,1,1))
    client = SimpleNamespace(generate_json=lambda *a,**k: dict(amount=amount,currency=currency,confidence=confidence))
    cache = EvidenceCache(tmp_path/'cache.json')
    assert evidence.resolve_blank_amount(event,SimpleNamespace(image_id='image'),client,cache) is None
    assert cache.get_amount('e') is None


def test_cached_vision_checks_currency_and_confidence(tmp_path):
    cache = EvidenceCache(tmp_path/'cache.json')
    cache.put_amount('e',Decimal('100'),dict(currency='USD',confidence='high'))
    assert cache.get_amount('e','EUR') is None
    assert cache.get_amount('e','USD') == Decimal('100')


@pytest.mark.parametrize('protected,deadline,accepts,expected', [
    (False,date(2026,3,1),True,'installments'),
    (True,date(2026,3,1),True,'not_recommended'),
    (False,date(2026,1,2),True,'not_recommended'),
    (False,date(2026,3,1),False,'not_recommended'),
])
def test_installment_spending_rescue_respects_constraints(protected,deadline,accepts,expected):
    p = _profile(current_available_balance=Decimal('650'),minimum_balance_to_keep=Decimal('100'),
        payment_methods_user_will_consider=frozenset({'installments'} if accepts else {}),max_installment_months=2,
        stop_categories=frozenset({'streaming'}),protect_categories=frozenset({'streaming'} if protected else {}))
    r = _request(requested_amount=Decimal('500'),desired_completion_date=deadline)
    option = PaymentOption('o',r.request_id,'installments',Decimal('250'),2,date(2026,1,2),30,Decimal('0'),Decimal('500'))
    expense = CashItem(date(2026,2,3),Decimal('-100'),'stream','streaming','stoppable',None,True)
    d = build_decision(r,p,[expense],p.current_available_balance,[option])
    assert d.recommended_payment_method == expected
    assert d.amount_safe_to_pay == Decimal('450')
    if expected == 'installments':
        assert d.chosen_plan.spending_changes == ('stop:stream',)
        assert d.chosen_plan.payments == ((date(2026,1,2),Decimal('250')),(date(2026,2,1),Decimal('250')))


def test_unchanged_installment_does_not_stop_spending():
    p = _profile(current_available_balance=Decimal('1000'),payment_methods_user_will_consider=frozenset({'installments'}),max_installment_months=2,stop_categories=frozenset({'streaming'}))
    r = _request()
    option = PaymentOption('o',r.request_id,'installments',Decimal('250'),2,date(2026,1,2),30,Decimal('0'),Decimal('500'))
    expense = CashItem(date(2026,2,3),Decimal('-100'),'stream','streaming','stoppable',None,True)
    d = build_decision(r,p,[expense],p.current_available_balance,[option])
    assert d.chosen_plan.spending_changes == ()
