"""Offline audit of the current implementation; never uses organizer data.

python evaluation/final_audit.py
Writes audit_replay.csv and final_audit.json; does not overwrite submission.
"""
import csv
import hashlib
import json
import sys
from datetime import date, timedelta
from dataclasses import replace
from collections import Counter
from pathlib import Path
from decimal import Decimal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'code'))
from core import io
from core.state import build_user_state, detect_recurring_series, get_forecast_cash_items
from core.forecast import daily_balances, is_safe
from core.spending import _candidate_actions
from core.options import expand_schedule, within_max_installment_months, completion_date
from core.models import CashItem
from main import process_request, gather_evidence, to_adjustments, EVIDENCE_CACHE_PATH
from agent.evidence import EvidenceCache, messages_for_request
from checkpoint import OUTPUT_COLUMNS


def main():
    profiles, events, rates = io.load_profiles(), io.load_events(), io.load_exchange_rates()
    options, messages, images = io.load_payment_options(), io.load_messages(), io.load_images()
    cache = EvidenceCache(EVIDENCE_CACHE_PATH)
    original = {r['request_id']: r for r in csv.DictReader((ROOT/'output.csv').open(encoding='utf-8', newline=''))}
    rows, findings = [], {'changed_rows': [], 'invalid_sources': [], 'vision_metadata_issues': [],
                         'spending_search_opportunities': [], 'rejection_with_full_capacity': [],
                         'fx_exclusions': [], 'sample_mismatches': [], 'worst_amount_traces': [],
                         'payment_safety_failures': [], 'installment_change_details': []}
    for eid, value in cache._data['amounts'].items():
        ev = next(e for e in events if e.event_id == eid)
        meta = cache._data.get('amount_meta', {}).get(eid, {})
        if meta.get('currency') != ev.currency or meta.get('confidence') != 'high':
            findings['vision_metadata_issues'].append(eid)
    requests = io.load_requests(io.DATASET_DIR/'requests.csv')
    samples = io.load_requests(io.DATASET_DIR/'sample_requests.csv')
    truths = {r['request_id']: r for r in csv.DictReader((io.DATASET_DIR/'sample_requests.csv').open(encoding='utf-8', newline=''))}
    for req in requests + samples:
        p = profiles[req.user_id]
        state = build_user_state(req.user_id, p, events)
        selected = messages_for_request(messages, req.user_id, req.request_id)
        allowed = {m.message_id for m in selected}
        raw = cache.get_amendments(req.user_id) or {}
        for a in raw.get('amendments', []):
            if a.get('source_message_id') not in allowed:
                findings['invalid_sources'].append([req.request_id, a.get('source_message_id')])
        ev = gather_evidence(req, p, state, messages, images, None, cache)
        items = get_forecast_cash_items(state, req.request_date, 90, rates, ev.amount_overrides, to_adjustments(ev))
        items.extend(CashItem(c.settlement_date, c.amount, None, "salary", "fixed", None, False)
                     for c in ev.confirmed_future_credits)
        row = process_request(req, p, events, rates, options, messages, images, None, cache)
        if state.fx_exclusion_notes:
            findings['fx_exclusions'].append([req.request_id, state.fx_exclusion_notes])
        if req.request_id in truths:
            truth = truths[req.request_id]
            mismatch = {k: [row[k], truth[k]] for k in OUTPUT_COLUMNS[1:-1] if row[k] != truth[k]}
            if mismatch:
                findings['sample_mismatches'].append({'request': req.request_id, 'fields': mismatch})
            if req.request_id in {'request_04','request_05','request_08','request_10','request_13','request_15','request_18','request_20'}:
                series = detect_recurring_series(state.settled_history)
                trace = daily_balances(p.current_available_balance, items, req.request_date)
                findings['worst_amount_traces'].append({'request': req.request_id,
                    'minimum': str(min(b for _, b in trace)),
                    'binding_dates': [d.isoformat() for d,b in trace if b == min(x for _,x in trace)],
                    'implied_reference_minimum': str(Decimal(truth['amount_safe_to_pay']) + p.minimum_balance_to_keep),
                    'detected_essential_series': [(s.category,s.description) for s in series if s.category in {'groceries','dining','transport'}]})
            continue
        rows.append(row)
        if row['payment_plan'] != 'none':
            adjusted = list(items)
            for change in row['spending_changes_needed'].split('|'):
                if change.startswith('stop:'):
                    adjusted = [i for i in adjusted if i.event_id != change[5:]]
                elif change.startswith('reduce_to:'):
                    _, eid, amount = change.split(':')
                    adjusted = [replace(i,signed_amount=-Decimal(amount)) if i.event_id == eid else i for i in adjusted]
            legs = [(date.fromisoformat(x.split(':')[0]),Decimal(x.split(':')[1])) for x in row['payment_plan'].split('|')]
            # Independent arithmetic, not daily_balances. Future wait/partial
            # payments occur after that day's income, per documented convention.
            balance = p.current_available_balance
            minimum = balance
            for day in range(91):
                d = req.request_date + timedelta(days=day)
                payment = sum((a for when,a in legs if when == d),Decimal(0))
                late = row['recommended_payment_method'] in {'wait','partial_payment'} and d > req.request_date
                balance += sum((i.signed_amount for i in adjusted if i.when == d and i.signed_amount < 0),Decimal(0))
                if not late:
                    balance -= payment
                minimum = min(minimum,balance)
                balance += sum((i.signed_amount for i in adjusted if i.when == d and i.signed_amount > 0),Decimal(0))
                if late:
                    balance -= payment
                minimum = min(minimum,balance)
            if minimum < p.minimum_balance_to_keep:
                findings['payment_safety_failures'].append({'request':req.request_id,'minimum':str(minimum),'required':str(p.minimum_balance_to_keep)})
            if row['recommended_payment_method'] == 'installments' and row['spending_changes_needed'] != 'none':
                findings['installment_change_details'].append({'request':req.request_id,'minimum':str(minimum),'required':str(p.minimum_balance_to_keep),'plan':row['payment_plan'],'changes':row['spending_changes_needed']})
        if row != original.get(req.request_id):
            findings['changed_rows'].append({'request': req.request_id, 'fields': [k for k in OUTPUT_COLUMNS if row[k] != original.get(req.request_id, {}).get(k)]})
        if row['recommended_payment_method'] == 'not_recommended' and row['earliest_date_for_full_payment']:
            findings['rejection_with_full_capacity'].append(req.request_id)
        # Counterexample search: apply every permitted action (an optimistic upper
        # bound, not a legal <=3-action plan), then test eligible installments.
        # A failure even here rules out these combinations under this forecast.
        if row['recommended_payment_method'] == 'not_recommended':
            actions = {a.event_id: a for a in _candidate_actions(items, p)}
            adjusted = []
            for i in items:
                a = actions.get(i.event_id)
                if a and a.action == 'stop':
                    continue
                adjusted.append(CashItem(i.when, -a.new_amount, i.event_id, i.category, i.flexibility, i.minimum_allowed_amount, i.is_projected) if a else i)
            for o in options.get(req.request_id, []):
                if o.payment_method != 'installments' or 'installments' not in p.payment_methods_user_will_consider:
                    continue
                if not within_max_installment_months(o, p.max_installment_months) or completion_date(o) > req.desired_completion_date:
                    continue
                payments = [CashItem(d,-a,None,'__payment__',None,None) for d,a in expand_schedule(o)]
                if is_safe(daily_balances(p.current_available_balance, adjusted+payments, req.request_date), p.minimum_balance_to_keep):
                    findings['spending_search_opportunities'].append({'request':req.request_id, 'option':o.payment_option_id, 'actions_upper_bound':len(actions)})
    path = ROOT/'evaluation'/'audit_replay.csv'
    with path.open('w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS); w.writeheader(); w.writerows(rows)
    findings['rows'] = len(rows)
    findings['output_md5'] = hashlib.md5((ROOT/'output.csv').read_bytes()).hexdigest()
    findings['replay_md5'] = hashlib.md5(path.read_bytes()).hexdigest()
    findings['methods'] = dict(Counter(r['recommended_payment_method'] for r in rows))
    (ROOT/'evaluation'/'final_audit.json').write_text(json.dumps(findings, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in findings.items() if k not in {'sample_mismatches','worst_amount_traces'}}, indent=2))

if __name__ == '__main__':
    main()
