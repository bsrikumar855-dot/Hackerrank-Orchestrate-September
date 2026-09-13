#!/usr/bin/env python3
"""Scores the pipeline under TWO metrics side by side, and can score
hypothetical variants of the forecast without modifying any module.

Why two metrics. `amount_safe_to_pay` is a continuous financial quantity, but
the exact-match scorer treats it as binary. The spec grades it on "accuracy of
`amount_safe_to_pay`", which most plausibly means proximity to the true value.
Those two readings can disagree about whether a change is an improvement, and
that disagreement is decision-relevant -- so both are always reported and
neither replaces the other:

  EXACT     : all six fields exact-match.                       (max 150)
  PROXIMITY : the five categorical fields exact-match, and
              amount_safe_to_pay scored 1 - min(1, |pred-true|/true)
              per row, summed.                                   (max 150)

Note the capped rows (ground-truth `amount_safe_to_pay == requested_amount`)
are only unmeasurable for the *back-solved implied minimum balance* analysis
in EXPERIMENTS.md. Comparing the emitted amount directly against ground truth
is well defined for every row, so both metrics here use all 25.

Usage:
    python3 evaluation/score_dual.py                 # current pipeline
    python3 evaluation/score_dual.py --sweep          # pooling-weight sweep
"""
from __future__ import annotations

import argparse
import csv
import statistics
import sys
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))

import core.state as st_mod
import main as main_mod
from agent.evidence import EvidenceCache
from core import io as data_io
from core.forecast import daily_balances, min_balance
from core.models import CashItem
from core.state import build_user_state, detect_recurring_series
from main import EVIDENCE_CACHE_PATH, gather_evidence, process_request, to_adjustments

CATEGORICAL = [
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
]
DECISION = ["affordability_status", "recommended_payment_method"]
ROTATING_ESSENTIALS = {"groceries", "transport", "dining"}
SAMPLE = data_io.DATASET_DIR / "sample_requests.csv"


def load():
    return {
        "profiles": data_io.load_profiles(),
        "events": data_io.load_events(),
        "rates": data_io.load_exchange_rates(),
        "options": data_io.load_payment_options(),
        "messages": data_io.load_messages(),
        "images": data_io.load_images(),
        "requests": {r.request_id: r for r in data_io.load_requests(SAMPLE)},
        "truth": list(csv.DictReader(SAMPLE.open(encoding="utf-8"))),
        "cache": EvidenceCache(EVIDENCE_CACHE_PATH),
    }


def pooled_patch(weight: Decimal, categories=ROTATING_ESSENTIALS):
    """Return a get_forecast_cash_items that ADDS a category-level series, at
    `weight` x the category mean, for rotating-vendor essential categories
    that currently project nothing. weight=0 reproduces the shipped behaviour.
    """
    original = st_mod.get_forecast_cash_items

    def patched(state, window_start, window_days, rates, amount_overrides=None, adjustments=None):
        base = original(
            state, window_start, window_days, rates,
            amount_overrides=amount_overrides, adjustments=adjustments,
        )
        if weight == 0:
            return base
        already = {s.category for s in detect_recurring_series(state.settled_history) if s.direction == "debit"}
        window_end = window_start + timedelta(days=window_days)
        by_cat = defaultdict(list)
        for e in state.settled_history:
            if (
                e.direction == "debit"
                and e.amount is not None
                and e.event_type in ("expense", "subscription", "debt_payment")
                and e.category in categories
                and e.category not in already
            ):
                by_cat[e.category].append(e)

        extra = []
        for category, evs in by_cat.items():
            if len(evs) < 3:
                continue
            evs = sorted(evs, key=lambda e: e.event_date)
            dates = [e.event_date for e in evs]
            gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
            median_gap = statistics.median(gaps)
            cadence = next((c for c in (7, 14, 30) if abs(median_gap - c) <= 3), None)
            if cadence is None:
                continue
            amount = (sum(e.amount for e in evs) / len(evs)) * weight
            occurrence = dates[-1] + timedelta(days=cadence)
            while occurrence <= window_end:
                if occurrence >= window_start:
                    extra.append(
                        CashItem(
                            when=occurrence, signed_amount=-amount,
                            event_id=evs[-1].event_id, category=category,
                            flexibility=evs[-1].flexibility,
                            minimum_allowed_amount=evs[-1].minimum_allowed_amount,
                            is_projected=True,
                        )
                    )
                occurrence += timedelta(days=cadence)
        return sorted(base + extra, key=lambda i: i.when)

    return patched, original


def run_variant(ctx, patch=None):
    """Produce every sample row's output, optionally under a patched forecast.
    Always restores the real function, so one variant can never leak into the
    next measurement."""
    saved_state = st_mod.get_forecast_cash_items
    saved_main = main_mod.get_forecast_cash_items
    if patch is not None:
        st_mod.get_forecast_cash_items = patch
        main_mod.get_forecast_cash_items = patch
    try:
        rows = {}
        for t in ctx["truth"]:
            request = ctx["requests"][t["request_id"]]
            profile = ctx["profiles"][request.user_id]
            rows[t["request_id"]] = process_request(
                request, profile, ctx["events"], ctx["rates"], ctx["options"],
                all_messages=ctx["messages"], all_images=ctx["images"],
                client=None, cache=ctx["cache"],
            )
        return rows
    finally:
        st_mod.get_forecast_cash_items = saved_state
        main_mod.get_forecast_cash_items = saved_main


def proximity(pred: str, want: str) -> float:
    p, w = Decimal(pred), Decimal(want)
    if w == 0:
        return 1.0 if p == 0 else 0.0
    return float(1 - min(Decimal(1), abs(p - w) / abs(w)))


def score(ctx, rows) -> dict:
    exact = {f: 0 for f in ["amount_safe_to_pay"] + CATEGORICAL}
    prox_sum = 0.0
    rel_errors = []
    for t in ctx["truth"]:
        row = rows[t["request_id"]]
        for f in CATEGORICAL:
            exact[f] += row[f] == t[f]
        exact["amount_safe_to_pay"] += Decimal(row["amount_safe_to_pay"]) == Decimal(t["amount_safe_to_pay"])
        s = proximity(row["amount_safe_to_pay"], t["amount_safe_to_pay"])
        prox_sum += s
        rel_errors.append(1 - s)
    categorical_total = sum(exact[f] for f in CATEGORICAL)
    return {
        "exact_per_field": exact,
        "exact_total": categorical_total + exact["amount_safe_to_pay"],
        "prox_amount": prox_sum,
        "prox_total": categorical_total + prox_sum,
        "median_rel_error": statistics.median(rel_errors),
        "mean_rel_error": statistics.mean(rel_errors),
    }


def signed_gap_stats(ctx, patch=None):
    """Median signed gap between projected minimum balance and the minimum
    implied by ground truth, over the measurable (non-capped) rows."""
    forecast = patch or st_mod.get_forecast_cash_items
    if True:
        gaps = []
        for t in ctx["truth"]:
            request = ctx["requests"][t["request_id"]]
            profile = ctx["profiles"][request.user_id]
            if Decimal(t["amount_safe_to_pay"]) == request.requested_amount:
                continue  # capped: implied minimum is only a lower bound
            state = build_user_state(request.user_id, profile, ctx["events"])
            evidence = gather_evidence(
                request, profile, state, ctx["messages"], ctx["images"], None, ctx["cache"]
            )
            items = forecast(
                state, request.request_date, 90, ctx["rates"],
                amount_overrides=evidence.amount_overrides,
                adjustments=to_adjustments(evidence),
            )
            trace = daily_balances(profile.current_available_balance, items, request.request_date, 90)
            implied = Decimal(t["amount_safe_to_pay"]) + profile.minimum_balance_to_keep
            debits = sum(-i.signed_amount for i in items if i.signed_amount < 0) or Decimal(1)
            gaps.append(float((min_balance(trace) - implied) / debits))
        return {
            "median_signed_gap": statistics.median(gaps),
            "optimistic": sum(1 for g in gaps if g > 0),
            "measurable": len(gaps),
        }


def report(label, s, gap=None, flipped=None):
    e = s["exact_per_field"]
    line = (
        f"{label:24} EXACT {s['exact_total']:6.1f}/150   PROX {s['prox_total']:6.1f}/150   "
        f"amt: exact {e['amount_safe_to_pay']}/25  prox {s['prox_amount']:5.2f}/25   "
        f"med|err| {s['median_rel_error']*100:5.1f}%"
    )
    if gap:
        line += f"   gap {gap['median_signed_gap']*100:+5.2f}% opt {gap['optimistic']}/{gap['measurable']}"
    if flipped is not None:
        line += f"   flips {flipped}"
    print(line)


def count_flips(base_rows, rows, fields=DECISION):
    return sum(
        1 for rid in base_rows if any(base_rows[rid][f] != rows[rid][f] for f in fields)
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sweep", action="store_true", help="Sweep the pooling weight.")
    args = ap.parse_args()

    ctx = load()
    print(ctx["cache"].describe(), "\n")

    base_rows = run_variant(ctx)
    base = score(ctx, base_rows)
    base_gap = signed_gap_stats(ctx)
    print("=== shipped pipeline, both metrics ===")
    report("w=0 (shipped)", base, base_gap)
    print()
    print("per-field exact:", {k: f"{v}/25" for k, v in base["exact_per_field"].items()})

    if not args.sweep:
        return

    print("\n=== pooling-weight sweep on rotating-vendor essentials (groceries, transport) ===")
    print("SHIP rule: decisions flipped == 0 AND amount error strictly improves.\n")
    original = st_mod.get_forecast_cash_items
    original_main = main_mod.get_forecast_cash_items
    results = []
    try:
        for w in [Decimal("0.2"), Decimal("0.4"), Decimal("0.6"), Decimal("0.8"), Decimal("1.0")]:
            patch, _ = pooled_patch(w)
            st_mod.get_forecast_cash_items = patch
            main_mod.get_forecast_cash_items = patch
            rows = {}
            for t in ctx["truth"]:
                request = ctx["requests"][t["request_id"]]
                profile = ctx["profiles"][request.user_id]
                rows[t["request_id"]] = process_request(
                    request, profile, ctx["events"], ctx["rates"], ctx["options"],
                    all_messages=ctx["messages"], all_images=ctx["images"],
                    client=None, cache=ctx["cache"],
                )
            s = score(ctx, rows)
            gap = signed_gap_stats(ctx, patch)
            flips = count_flips(base_rows, rows)
            any_flips = count_flips(base_rows, rows, CATEGORICAL)
            results.append((w, s, gap, flips, any_flips, rows))
            report(f"w={w}", s, gap, flips)
    finally:
        st_mod.get_forecast_cash_items = original
        main_mod.get_forecast_cash_items = original_main

    print("\n=== SHIP evaluation ===")
    shippable = [
        (w, s) for w, s, g, flips, any_flips, _ in results
        if flips == 0 and s["prox_amount"] > base["prox_amount"]
    ]
    for w, s, g, flips, any_flips, _ in results:
        verdict = (
            "SHIPPABLE" if (flips == 0 and s["prox_amount"] > base["prox_amount"])
            else f"reject (decision flips={flips}, any-field flips={any_flips}, "
                 f"amt prox {s['prox_amount']:.2f} vs {base['prox_amount']:.2f})"
        )
        print(f"  w={w}: {verdict}")
    if not shippable:
        print("\n  => No Pareto-safe weight exists: every non-zero weight moves at least")
        print("     one decision. The bias cannot be closed without moving decisions.")


if __name__ == "__main__":
    main()
