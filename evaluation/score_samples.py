#!/usr/bin/env python3
"""Scores the solution against dataset/sample_requests.csv's 25 solved
examples, per output field.

sample_requests.csv is used here ONLY as a public development accuracy check against
its own already-published ground-truth columns -- never as a lookup table for
the 250 real evaluation requests. No code path anywhere in this repo
special-cases a request_id: grep for `request_0` outside this file and the
dataset to confirm.

Usage:
    python3 evaluation/score_samples.py             # with the model layer if a key is present
    python3 evaluation/score_samples.py --no-llm     # deterministic core only
    python3 evaluation/score_samples.py --tolerance 0.02
"""
from __future__ import annotations

import argparse
import csv
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))

from agent import llm as llm_mod
from agent.evidence import EvidenceCache
from agent.usage import UsageTracker
from core import io as data_io
from main import EVIDENCE_CACHE_PATH, prefetch_amendments, process_request

SAMPLE_PATH = data_io.DATASET_DIR / "sample_requests.csv"
FIELDS = [
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
]


def _within_tolerance(got: str, want: str, tolerance: float) -> bool:
    try:
        g, w = Decimal(got), Decimal(want)
    except (InvalidOperation, ValueError):
        return False
    if g == w:
        return True
    if w == 0:
        return g == 0
    return abs((g - w) / w) <= Decimal(str(tolerance))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-llm", action="store_true", help="Deterministic core only.")
    parser.add_argument(
        "--tolerance", type=float, default=0.02,
        help="Relative tolerance for amount_safe_to_pay near-misses (default 0.02 = 2%%).",
    )
    parser.add_argument("--verbose", action="store_true", help="Print every mismatch.")
    args = parser.parse_args()

    profiles = data_io.load_profiles()
    all_events = data_io.load_events()
    rates = data_io.load_exchange_rates()
    options_by_request = data_io.load_payment_options()
    all_messages = data_io.load_messages()
    all_images = data_io.load_images()
    requests = {r.request_id: r for r in data_io.load_requests(SAMPLE_PATH)}

    tracker = UsageTracker()
    client = None
    if not args.no_llm and llm_mod.is_available():
        client = llm_mod.GeminiClient(tracker, model=llm_mod.DEFAULT_MODEL)
        print(f"Model layer: {llm_mod.DEFAULT_MODEL}\n")
    else:
        print("Model layer: disabled (deterministic core only)\n")
    cache = EvidenceCache(EVIDENCE_CACHE_PATH)
    print(cache.describe(), "\n")
    note = prefetch_amendments(
        list(requests.values()), profiles, all_events, all_messages, client, cache
    )
    if note:
        print(note + "\n")

    with SAMPLE_PATH.open(newline="", encoding="utf-8") as fh:
        truth_rows = list(csv.DictReader(fh))

    exact = {f: 0 for f in FIELDS}
    near = 0
    mismatches: list[tuple[str, dict]] = []

    for truth in truth_rows:
        rid = truth["request_id"]
        request = requests[rid]
        profile = profiles[request.user_id]
        row = process_request(
            request, profile, all_events, rates, options_by_request,
            all_messages=all_messages, all_images=all_images,
            client=client, cache=cache,
            use_llm_explanation=False,  # prose is judged separately; keep scoring deterministic
        )

        row_diffs = {}
        for f in FIELDS:
            got, want = row[f], truth[f]
            if f == "amount_safe_to_pay":
                try:
                    is_match = Decimal(got) == Decimal(want)
                except (InvalidOperation, ValueError):
                    is_match = False
            else:
                is_match = got == want
            if is_match:
                exact[f] += 1
            else:
                row_diffs[f] = (got, want)
        if "amount_safe_to_pay" in row_diffs:
            got, want = row_diffs["amount_safe_to_pay"]
            if _within_tolerance(got, want, args.tolerance):
                near += 1
        if row_diffs:
            mismatches.append((rid, row_diffs))

    n = len(truth_rows)
    print(f"Scored {n} sample requests\n")
    for f in FIELDS:
        print(f"  {f:32} {exact[f]:3}/{n}  ({100 * exact[f] / n:5.1f}%)")
    print(
        f"\n  amount_safe_to_pay within {args.tolerance:.0%}      "
        f"{exact['amount_safe_to_pay'] + near:3}/{n}  "
        f"({100 * (exact['amount_safe_to_pay'] + near) / n:5.1f}%)"
    )
    print(f"\n{len(mismatches)} rows had at least one mismatch")

    if args.verbose:
        print()
        for rid, diffs in mismatches:
            print(f"  {rid}:")
            for f, (got, want) in diffs.items():
                print(f"    {f:32} got={got!r:44} want={want!r}")


if __name__ == "__main__":
    main()
