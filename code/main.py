#!/usr/bin/env python3
"""Entry point for the Buy or Wait? solution.

    python3 code/main.py                          # full run over dataset/requests.csv
    python3 code/main.py --limit 20                # smoke test on the first 20 rows
    python3 code/main.py --no-llm                   # deterministic core only, zero model calls
    python3 code/main.py --requests dataset/sample_requests.csv   # score-yourself run

Writes `output.csv` in the repository root (one row per request_id) and
`evaluation/usage_report.md` for the run.

Architecture (see code/README.md for the full boundary):
  - The deterministic core decides everything numeric: state reconstruction,
    currency normalization, the 90-day simulation, amount_safe_to_pay,
    earliest_date_for_full_payment, eligibility, the 6-level tie-break, and
    every output format rule.
  - The agentic layer only reads evidence that is not machine-readable
    (amounts that exist only inside a PNG, amendments buried in multilingual
    free-text messages) and writes the human-facing explanation prose.

Resilience: every row is wrapped in its own try/except and written through to
output.csv immediately, so a rate limit or a single bad row never costs the
batch (see code/checkpoint.py).
"""
from __future__ import annotations

import argparse
import csv
import sys
import traceback
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent import evidence as evidence_mod
from agent import llm as llm_mod
from agent.evidence import EvidenceCache, EvidenceResult
from agent.explanation import ExplanationFacts, deterministic_explanation, llm_explanation
from agent.usage import UsageTracker
from checkpoint import OUTPUT_COLUMNS, OutputWriter, load_completed
from core import io as data_io
from core.forecast import FORECAST_DAYS, daily_balances, min_balance
from core.formatting import build_output_row, not_recommended_row, validate_output_row
from core.models import CashItem, SeriesAdjustment
from core.planning import build_decision
from core.state import (
    UnresolvedAmountError,
    build_user_state,
    detect_recurring_series,
    get_forecast_cash_items,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = REPO_ROOT / "output.csv"
# The challenge requires the token-usage file at exactly `evaluation/usage_report.md`
# inside code.zip, so the evaluation folder lives at the repo root, not under code/.
EVALUATION_DIR = REPO_ROOT / "evaluation"
EVIDENCE_CACHE_PATH = EVALUATION_DIR / "evidence_cache.json"
USAGE_REPORT_PATH = EVALUATION_DIR / "usage_report.md"
USAGE_RAW_PATH = EVALUATION_DIR / "usage_raw.json"


def gather_evidence(
    request,
    profile,
    state,
    all_messages,
    all_images,
    client,
    cache: EvidenceCache,
) -> EvidenceResult:
    """Resolve blank amounts from linked images and extract message
    amendments. Degrades to an empty result (never a wrong one) when no model
    is available."""
    result = EvidenceResult()

    window_end = request.request_date + timedelta(days=FORECAST_DAYS)
    blank_in_window = [
        e
        for e in state.future_events
        if e.amount is None
        and e.settlement_date is not None
        and request.request_date <= e.settlement_date <= window_end
    ]

    def _evidence_sentence(event, amount) -> str:
        from agent.explanation import _money as money

        return (
            f"The {money(amount, event.currency)} for the {event.description.lower()} was read "
            f"from its linked document image."
        )

    for event in blank_in_window:
        cached = cache.get_amount(event.event_id, event.currency)
        if cached is not None:
            result.amount_overrides[event.event_id] = cached
            result.notes.append(f"Read {event.event_id}'s amount ({cached}) from cache.")
            result.evidence_sentences.append(_evidence_sentence(event, cached))
            continue
        image = evidence_mod.images_for_event(all_images, event.event_id)
        if image is None or client is None:
            result.unresolved_events.append(event.event_id)
            continue
        try:
            amount = evidence_mod.resolve_blank_amount(event, image, client, cache)
        except Exception as exc:
            result.notes.append(f"image extraction failed for {event.event_id}: {exc}")
            amount = None
        if amount is None:
            result.unresolved_events.append(event.event_id)
        else:
            result.amount_overrides[event.event_id] = amount
            result.notes.append(
                f"Read {event.event_id}'s amount ({amount}) from linked document {image.image_id}."
            )
            result.evidence_sentences.append(_evidence_sentence(event, amount))

    messages = evidence_mod.messages_for_request(all_messages, request.user_id, request.request_id)
    if messages:
        known_series = {
            (s.category, s.direction): s.conservative_amount
            for s in detect_recurring_series(state.settled_history)
        }
        try:
            # Works with a live client, or with no client at all when this
            # user's extraction is already cached (offline reproducibility).
            amendments, injections, rejections = evidence_mod.extract_amendments(
                request.user_id, messages, known_series, profile.home_currency,
                request.request_date, client, cache,
            )
            result.amendments = amendments
            result.injection_notes = injections
            result.notes.extend(f"rejected extracted amendment: {r}" for r in rejections)
            credits, credit_rejections = evidence_mod.extract_confirmed_future_credits(
                request.user_id, messages, known_series, profile.home_currency,
                request.request_date, cache,
            )
            result.confirmed_future_credits = credits
            result.notes.extend(f"rejected confirmed credit: {r}" for r in credit_rejections)
            for credit in credits:
                from agent.explanation import _long_date, _money
                result.evidence_sentences.append(
                    f"The confirmed first salary of {_money(credit.amount, credit.currency)} "
                    f"on {_long_date(credit.settlement_date)} was included."
                )
        except Exception as exc:
            result.notes.append(f"message amendment extraction failed: {exc}")

        if client is None and not result.injection_notes:
            # Independent injection scan always runs, even with no model, so
            # untrusted-content detection is never silently skipped.
            from agent.sanitizer import scan_for_injection

            for m in messages:
                for finding in scan_for_injection(m.message_id, m.message_text):
                    result.injection_notes.append(
                        f"{finding.source_id}: matched instruction-like text"
                    )
    return result


def prefetch_amendments(
    requests,
    profiles,
    all_events,
    all_messages,
    client,
    cache: EvidenceCache,
) -> Optional[str]:
    """Batched evidence prefetch, run once before the per-row loop.

    The binding constraint on a full run is requests-per-minute, not tokens,
    so message amendments are extracted for several users per call up front
    instead of one call per row. The per-row loop then reads a warm cache.
    Returns a note for the usage report, or None if there was nothing to do.
    """
    if client is None:
        return None

    batch_items = []
    for request in requests:
        profile = profiles.get(request.user_id)
        if profile is None:
            continue
        messages = evidence_mod.messages_for_request(
            all_messages, request.user_id, request.request_id
        )
        if not messages or cache.get_amendments(request.user_id) is not None:
            continue
        state = build_user_state(request.user_id, profile, all_events)
        batch_items.append(
            evidence_mod.BatchItem(
                user_id=request.user_id,
                messages=messages,
                known_series={
                    (s.category, s.direction): s.conservative_amount
                    for s in detect_recurring_series(state.settled_history)
                },
                home_currency=profile.home_currency,
                request_date=request.request_date,
            )
        )
    if not batch_items:
        return None

    def _progress(done: int, total_items: int) -> None:
        print(f"...evidence prefetch {done}/{total_items} users", file=sys.stderr)

    calls = evidence_mod.extract_amendments_batched(
        batch_items, client, cache, progress=_progress
    )
    return (
        f"Batched message-amendment prefetch: {len(batch_items)} users in {calls} "
        f"API call(s) (batch size {evidence_mod.DEFAULT_BATCH_SIZE})."
    )


def to_adjustments(result: EvidenceResult) -> list[SeriesAdjustment]:
    out: list[SeriesAdjustment] = []
    for a in result.amendments:
        out.append(
            SeriesAdjustment(
                category=a.category,
                direction="credit" if a.kind == "income_change" else "debit",
                scope=a.scope,
                new_amount=a.new_amount,
                change_pct=a.change_pct,
                currency=a.currency,
                effective_date=a.effective_date,
                source=a.source_message_id,
            )
        )
    return out


def process_request(
    request,
    profile,
    all_events,
    rates,
    options_by_request,
    all_messages=(),
    all_images=(),
    client=None,
    cache: Optional[EvidenceCache] = None,
    explanation_client=None,
    use_llm_explanation: bool = False,
) -> dict:
    state = build_user_state(request.user_id, profile, all_events)
    options = options_by_request.get(request.request_id, [])

    result = EvidenceResult()
    if cache is not None:
        result = gather_evidence(
            request, profile, state, all_messages, all_images, client, cache
        )

    try:
        cash_items = get_forecast_cash_items(
            state,
            request.request_date,
            FORECAST_DAYS,
            rates,
            amount_overrides=result.amount_overrides,
            adjustments=to_adjustments(result),
        )
        cash_items.extend(
            CashItem(
                when=c.settlement_date, signed_amount=c.amount, event_id=None,
                category="salary", flexibility="fixed", minimum_allowed_amount=None,
                is_projected=False,
            )
            for c in result.confirmed_future_credits
        )
    except UnresolvedAmountError as exc:
        return not_recommended_row(
            request,
            f"Cannot decide safely: event {exc.event_id} has a blank amount and its linked "
            f"document could not be read, so its value is unknown. Treating an unknown amount "
            f"as zero would overstate what is safe to pay.",
        )

    decision = build_decision(
        request, profile, cash_items, profile.current_available_balance, options
    )

    baseline_trace = daily_balances(
        profile.current_available_balance, cash_items, request.request_date, FORECAST_DAYS
    )
    plan = decision.chosen_plan

    # Spending changes in plain words, the way the solved samples phrase them
    # ("Stop the family streaming plan", "Reduce the weekend food delivery to
    # IDR 665,950") -- an event_id means nothing to the user reading the row.
    def _clause(change: str) -> str:
        from agent.explanation import _money as money

        if change.startswith("stop:"):
            ev = state.events_by_id.get(change[5:])
            return f"stop the {ev.description.lower()}" if ev else f"stop {change[5:]}"
        _, event_id, amount = change.split(":", 2)
        ev = state.events_by_id.get(event_id)
        label = ev.description.lower() if ev else event_id
        return f"reduce the {label} to {money(Decimal(amount), profile.home_currency)}"

    facts = ExplanationFacts(
        request_id=request.request_id,
        currency=profile.home_currency,
        request_type=request.request_type,
        requested_amount=request.requested_amount,
        request_date=request.request_date,
        desired_completion_date=request.desired_completion_date,
        minimum_balance_to_keep=profile.minimum_balance_to_keep,
        amount_safe_to_pay=decision.amount_safe_to_pay,
        affordability_status=decision.affordability_status,
        recommended_payment_method=decision.recommended_payment_method,
        earliest_date_for_full_payment=decision.earliest_date_for_full_payment,
        payments=plan.payments if plan else (),
        spending_changes=plan.spending_changes if plan else (),
        payment_option_id=plan.payment_option_id if plan else None,
        total_paid=plan.total_paid if plan else None,
        projected_min_balance=min_balance(baseline_trace),
        evidence_notes=tuple(result.evidence_sentences),
        injection_notes=tuple(result.injection_notes),
        spending_change_clauses=tuple(_clause(c) for c in (plan.spending_changes if plan else ())),
        partial_allowed=request.allows_partial_payment,
        user_accepts_partial="partial_payment" in profile.payment_methods_user_will_consider,
    )
    writer_client = explanation_client or (client if use_llm_explanation else None)
    explanation = (
        llm_explanation(facts, writer_client) if writer_client else deterministic_explanation(facts)
    )

    row = build_output_row(
        request,
        decision.amount_safe_to_pay,
        decision.affordability_status,
        decision.recommended_payment_method,
        decision.chosen_plan,
        decision.earliest_date_for_full_payment,
        explanation,
    )
    errors = validate_output_row(row, request, options, decision.chosen_plan)
    if errors:
        return not_recommended_row(
            request, "Internal validation failed before writing: " + "; ".join(errors)
        )
    return row


def run(
    requests_path: Path,
    limit: Optional[int],
    use_llm: bool,
    llm_explanations: bool = False,
    call_budget: Optional[int] = None,
) -> tuple[Path, UsageTracker, list[str]]:
    profiles = data_io.load_profiles()
    all_events = data_io.load_events()
    rates = data_io.load_exchange_rates()
    options_by_request = data_io.load_payment_options()
    all_messages = data_io.load_messages()
    all_images = data_io.load_images()
    requests = data_io.load_requests(requests_path)
    if limit:
        requests = requests[:limit]

    tracker = UsageTracker()
    adopted = tracker.load_prior(USAGE_RAW_PATH)
    notes: list[str] = []
    if adopted:
        notes.append(
            f"Adopted {adopted} call record(s) from earlier runs of this pipeline whose "
            f"cached evidence this output reuses; figures are cumulative."
        )
    client = None
    explanation_client = None
    if use_llm:
        if llm_mod.is_available():
            client = llm_mod.GeminiClient(tracker, model=llm_mod.DEFAULT_MODEL)
            client.call_budget = call_budget
            if call_budget is not None:
                notes.append(
                    f"API call budget for this run: {call_budget}. The observed free tier "
                    f"grants 20 requests/day/model, so a budget lets the run finish "
                    f"deterministically with the evidence already cached instead of "
                    f"stalling on calls that cannot succeed."
                )
            notes.append(
                f"Evidence model: {llm_mod.DEFAULT_MODEL} (vision amount extraction, "
                f"message amendment extraction)."
            )
            if llm_explanations:
                explanation_client = llm_mod.GeminiClient(tracker, model=llm_mod.EXPLANATION_MODEL)
                notes.append(f"Explanation model: {llm_mod.EXPLANATION_MODEL}.")
            else:
                notes.append(
                    "Explanations written by the deterministic template (--llm-explanations off), "
                    "so the API quota is reserved for the evidence calls that affect numbers."
                )
            notes.append(f"Client-side rate limit: {llm_mod.REQUESTS_PER_MINUTE} requests/minute.")
        else:
            notes.append(
                "Model layer requested but unavailable (no GEMINI_API_KEY/GOOGLE_API_KEY in the "
                "environment, or google-genai not installed): ran the deterministic core only. "
                "Blank-amount events without a cached extraction fall back to not_recommended "
                "with an explicit explanation rather than being treated as zero."
            )
    else:
        notes.append("Model layer disabled by --no-llm: deterministic core only.")

    cache = EvidenceCache(EVIDENCE_CACHE_PATH)
    print(cache.describe(), file=sys.stderr)
    notes.append(cache.describe())

    prefetch_note = prefetch_amendments(
        requests, profiles, all_events, all_messages, client, cache
    )
    if prefetch_note:
        notes.append(prefetch_note)

    writer = OutputWriter(OUTPUT_PATH)
    total = len(requests)
    failures = 0

    for i, request in enumerate(requests, 1):
        if writer.already_done(request.request_id):
            continue
        try:
            profile = profiles.get(request.user_id)
            if profile is None:
                row = not_recommended_row(request, "No financial profile found for this user_id.")
            else:
                row = process_request(
                    request, profile, all_events, rates, options_by_request,
                    all_messages=all_messages, all_images=all_images,
                    client=client, cache=cache,
                    explanation_client=explanation_client,
                )
        except Exception as exc:  # per-row isolation: one bad row never aborts the batch
            failures += 1
            traceback.print_exc(file=sys.stderr)
            row = not_recommended_row(
                request,
                f"Internal error while processing this request: {exc.__class__.__name__}: {exc}",
            )
        writer.write(row)
        tracker.note_request()
        if i % 25 == 0 or i == total:
            print(f"...{i}/{total} requests processed", file=sys.stderr)
    writer.close()

    # Final ordered rewrite: output.csv lists rows in requests.csv's order
    # regardless of how many resumed runs it took to finish the batch.
    completed = load_completed(OUTPUT_PATH)
    with OUTPUT_PATH.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=OUTPUT_COLUMNS)
        w.writeheader()
        for request in requests:
            row = completed.get(request.request_id) or not_recommended_row(
                request, "Missing after run (not reached)."
            )
            w.writerow(row)

    # requests_processed must describe the rows this output.csv covers, not
    # only the rows recomputed in this invocation -- a resumed run skips
    # already-written rows, which would otherwise divide by a tiny number and
    # report absurd per-request averages.
    tracker.requests_processed = len(requests)
    if client is not None:
        notes.append(
            f"API calls attempted by the evidence client in this invocation: "
            f"{client.calls_attempted} (model at finish: {client.model})."
        )
    if failures:
        notes.append(f"{failures} row(s) hit a per-row error and fell back to not_recommended.")
    notes.append(f"Requests in this run: {total}; rows written: {len(completed)}.")
    return OUTPUT_PATH, tracker, notes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--requests", type=str, default=str(data_io.DATASET_DIR / "requests.csv"),
        help="Requests CSV to score (default: dataset/requests.csv).",
    )
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N requests.")
    parser.add_argument(
        "--no-llm", action="store_true",
        help="Run the deterministic core only, with zero model calls.",
    )
    parser.add_argument(
        "--max-evidence-calls", type=int, default=None,
        help=(
            "Cap the number of API calls this run may attempt (0 disables new calls "
            "while still using everything already cached). Useful because the free tier "
            "grants only 20 requests/day/model."
        ),
    )
    parser.add_argument(
        "--llm-explanations", action="store_true",
        help=(
            "Also have the model write decision_explanation prose (one extra call per row, "
            f"on {llm_mod.EXPLANATION_MODEL}). Off by default so the API quota goes to the "
            "evidence calls that affect numbers; the deterministic template is used instead."
        ),
    )
    args = parser.parse_args()

    output_path, tracker, notes = run(
        Path(args.requests), args.limit,
        use_llm=not args.no_llm, llm_explanations=args.llm_explanations,
        call_budget=args.max_evidence_calls,
    )
    tracker.write_report(USAGE_REPORT_PATH, notes=notes)
    tracker.write_raw(USAGE_RAW_PATH)
    print(f"Wrote {output_path}")
    print(f"Wrote {USAGE_REPORT_PATH}")


if __name__ == "__main__":
    main()
