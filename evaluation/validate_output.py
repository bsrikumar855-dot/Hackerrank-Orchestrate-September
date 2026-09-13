#!/usr/bin/env python3
"""Independent pre-submission validation of the generated output.csv.

This re-checks the delivered file from scratch, using only the dataset and
the spec's stated rules -- it deliberately shares no code with the writer's
own validation, so a bug in `core/formatting.py` cannot hide itself here.

    python3 code/evaluation/validate_output.py
    python3 code/evaluation/validate_output.py --output output.csv

Exits non-zero if anything is wrong, so it can gate submission.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))

from core import io as data_io

REPO_ROOT = Path(__file__).resolve().parents[1]

EXPECTED_COLUMNS = [
    "request_id",
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
    "decision_explanation",
]
ALLOWED_STATUS = {"affordable_now", "affordable_with_plan", "affordable_later", "not_affordable"}
ALLOWED_METHOD = {"full_payment", "partial_payment", "installments", "wait", "not_recommended"}
LEG_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}):(\d+(?:\.\d+)?)$")
STOP_RE = re.compile(r"^stop:(\S+)$")
REDUCE_RE = re.compile(r"^reduce_to:([^:]+):(\d+(?:\.\d+)?)$")
FORECAST_DAYS = 90


def _d(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(REPO_ROOT / "output.csv"))
    parser.add_argument("--requests", default=str(data_io.DATASET_DIR / "requests.csv"))
    args = parser.parse_args()

    output_path, requests_path = Path(args.output), Path(args.requests)
    errors: list[str] = []
    warnings: list[str] = []

    requests = {r.request_id: r for r in data_io.load_requests(requests_path)}
    options_by_request = data_io.load_payment_options()
    profiles = data_io.load_profiles()
    events = data_io.load_events()
    flexible_by_event = {
        e.event_id: (e.category, e.flexibility, e.minimum_allowed_amount) for e in events
    }

    with output_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        columns = reader.fieldnames or []
        rows = list(reader)

    if columns != EXPECTED_COLUMNS:
        errors.append(f"column mismatch:\n  got  {columns}\n  want {EXPECTED_COLUMNS}")

    seen: set[str] = set()
    for i, row in enumerate(rows, start=2):  # line 1 is the header
        rid = row.get("request_id", "")
        where = f"row {i} ({rid})"
        if rid in seen:
            errors.append(f"{where}: duplicate request_id")
        seen.add(rid)
        request = requests.get(rid)
        if request is None:
            errors.append(f"{where}: request_id is not in {requests_path.name}")
            continue

        profile = profiles[request.user_id]
        status = row["affordability_status"]
        method = row["recommended_payment_method"]

        if status not in ALLOWED_STATUS:
            errors.append(f"{where}: bad affordability_status {status!r}")
        if method not in ALLOWED_METHOD:
            errors.append(f"{where}: bad recommended_payment_method {method!r}")

        # 0 <= amount_safe_to_pay <= requested_amount
        try:
            safe = Decimal(row["amount_safe_to_pay"])
        except (InvalidOperation, ValueError):
            errors.append(f"{where}: amount_safe_to_pay {row['amount_safe_to_pay']!r} is not a number")
            safe = None
        if safe is not None and not (Decimal("0") <= safe <= request.requested_amount):
            errors.append(
                f"{where}: amount_safe_to_pay {safe} outside [0, {request.requested_amount}]"
            )

        # payment_plan
        plan_raw = row["payment_plan"]
        legs: list[tuple[date, Decimal]] = []
        if plan_raw != "none":
            for part in plan_raw.split("|"):
                m = LEG_RE.match(part)
                if not m:
                    errors.append(f"{where}: malformed payment_plan leg {part!r}")
                    continue
                legs.append((_d(m.group(1)), Decimal(m.group(2))))
            if legs != sorted(legs, key=lambda x: x[0]):
                errors.append(f"{where}: payment_plan legs not in chronological order")
        elif method != "not_recommended":
            errors.append(f"{where}: payment_plan is 'none' but method is {method!r}")

        total = sum((a for _, a in legs), Decimal("0"))

        if method in ("full_payment", "wait"):
            if len(legs) != 1:
                errors.append(f"{where}: {method} should be a single payment, got {len(legs)}")
            if total != request.requested_amount:
                errors.append(
                    f"{where}: {method} total {total} != requested_amount {request.requested_amount}"
                )
        if method == "partial_payment":
            if not request.allows_partial_payment:
                errors.append(f"{where}: partial_payment but the request disallows partial payment")
            if "partial_payment" not in profile.payment_methods_user_will_consider:
                errors.append(f"{where}: partial_payment but the user does not accept it")
            if len(legs) != 2:
                errors.append(f"{where}: partial_payment needs exactly 2 legs, got {len(legs)}")
            else:
                if total != request.requested_amount:
                    errors.append(f"{where}: partial legs sum to {total}, not {request.requested_amount}")
                if legs[0][0] != request.request_date:
                    errors.append(f"{where}: partial first leg dated {legs[0][0]}, not request_date")
                if safe is not None and legs[0][1] != safe:
                    errors.append(f"{where}: partial first leg {legs[0][1]} != amount_safe_to_pay {safe}")
                if safe is not None and not (Decimal("0") < safe < request.requested_amount):
                    errors.append(f"{where}: partial_payment requires 0 < amount_safe_to_pay < requested")
                if legs[1][0] > request.desired_completion_date:
                    errors.append(f"{where}: partial second leg {legs[1][0]} is past the deadline")
            if status != "affordable_with_plan":
                errors.append(f"{where}: partial_payment requires affordable_with_plan, got {status!r}")
        if method == "installments":
            if "installments" not in profile.payment_methods_user_will_consider:
                errors.append(f"{where}: installments but the user does not accept them")
            opts = [o for o in options_by_request.get(rid, []) if o.payment_method == "installments"]
            matched = None
            for o in opts:
                freq = o.payment_frequency_days or 0
                expected = [
                    (o.first_payment_date + timedelta(days=freq * k), o.payment_amount)
                    for k in range(o.number_of_payments)
                ]
                if len(expected) == len(legs) and all(
                    ed == gd and eam.compare(gam) == 0 for (ed, eam), (gd, gam) in zip(expected, legs)
                ):
                    matched = o
                    break
            if matched is None:
                errors.append(f"{where}: installment plan matches no supplied payment option exactly")
            elif profile.max_installment_months is not None and (
                matched.number_of_payments > profile.max_installment_months
            ):
                errors.append(
                    f"{where}: {matched.number_of_payments} installments exceeds the user's "
                    f"max_installment_months {profile.max_installment_months}"
                )
            elif profile.max_installment_months is None:
                errors.append(f"{where}: installments recommended but max_installment_months is blank")
        if method == "full_payment" and legs and legs[0][0] != request.request_date:
            warnings.append(f"{where}: full_payment dated {legs[0][0]}, not request_date")
        if method == "wait" and legs and legs[0][0] <= request.request_date:
            errors.append(f"{where}: 'wait' but the payment is dated on/before request_date")

        # earliest_date_for_full_payment
        earliest = row["earliest_date_for_full_payment"]
        if status == "affordable_now" and earliest != request.request_date.isoformat():
            errors.append(f"{where}: affordable_now requires earliest == request_date, got {earliest!r}")
        if earliest:
            try:
                ed = _d(earliest)
            except ValueError:
                errors.append(f"{where}: unparseable earliest_date_for_full_payment {earliest!r}")
            else:
                window_end = request.request_date + timedelta(days=FORECAST_DAYS)
                if not (request.request_date <= ed <= window_end):
                    errors.append(f"{where}: earliest_date {ed} outside the 90-day forecast window")

        # spending_changes_needed
        changes = row["spending_changes_needed"]
        if changes != "none":
            entries = changes.split("|")
            if len(entries) > 3:
                errors.append(f"{where}: {len(entries)} spending changes, max is 3")
            targets: dict[str, set[str]] = {}
            for entry in entries:
                sm, rm = STOP_RE.match(entry), REDUCE_RE.match(entry)
                if sm:
                    event_id, kind, new_amount = sm.group(1), "stop", None
                elif rm:
                    event_id, kind, new_amount = rm.group(1), "reduce", Decimal(rm.group(2))
                else:
                    errors.append(f"{where}: malformed spending change {entry!r}")
                    continue
                targets.setdefault(event_id, set()).add(kind)
                meta = flexible_by_event.get(event_id)
                if meta is None:
                    errors.append(f"{where}: spending change targets unknown event {event_id}")
                    continue
                category, flexibility, min_allowed = meta
                if category in profile.protect_categories:
                    errors.append(f"{where}: spending change touches protected category {category!r}")
                if flexibility not in ("stoppable", "reducible", "reducible_or_stoppable"):
                    errors.append(
                        f"{where}: {event_id} has flexibility {flexibility!r} and cannot be changed"
                    )
                if kind == "stop":
                    if flexibility not in ("stoppable", "reducible_or_stoppable"):
                        errors.append(f"{where}: {event_id} is not stoppable")
                    if category not in profile.stop_categories:
                        errors.append(f"{where}: user does not permit stopping {category!r}")
                else:
                    if flexibility not in ("reducible", "reducible_or_stoppable"):
                        errors.append(f"{where}: {event_id} is not reducible")
                    if category not in profile.reduce_categories:
                        errors.append(f"{where}: user does not permit reducing {category!r}")
                    if min_allowed is not None and new_amount < min_allowed:
                        errors.append(
                            f"{where}: reduce_to {new_amount} is below minimum_allowed_amount {min_allowed}"
                        )
            for event_id, kinds in targets.items():
                if len(kinds) > 1:
                    errors.append(f"{where}: {event_id} has both stop and reduce_to")

        if not (row["decision_explanation"] or "").strip():
            errors.append(f"{where}: empty decision_explanation")

    missing = set(requests) - seen
    if missing:
        errors.append(f"{len(missing)} request_id(s) missing from output: {sorted(missing)[:10]}")
    if len(rows) != len(requests):
        errors.append(f"row count {len(rows)} != request count {len(requests)}")

    print(f"Validated {output_path.name}: {len(rows)} rows against {len(requests)} requests")
    if warnings:
        print(f"\n{len(warnings)} warning(s):")
        for w in warnings[:20]:
            print(f"  - {w}")
    if errors:
        print(f"\n{len(errors)} ERROR(S):")
        for e in errors[:60]:
            print(f"  - {e}")
        if len(errors) > 60:
            print(f"  ... and {len(errors) - 60} more")
        return 1
    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
