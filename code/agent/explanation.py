"""decision_explanation generation.

Contract, in this order: the verdict as an action the user can take, then the
specific figures from the deterministic core that drove it, then the rule that
connects them. Every number in the sentence comes from the deterministic
decision -- the model is a writer here, never a decider, and it is given only
already-computed facts.

The style deliberately mirrors the solved examples in sample_requests.csv:

    "Pay ZAR 25,256 today. This leaves at least ZAR 18,000 available over the
     next 90 days."
    "Use 3 installments of IDR 15,952,906.67, starting 8 August 2025. This
     leaves at least IDR 29,158,400 available."
    "Do not make this payment by 12 January 2026. None of the available
     options keeps the ZAR 13,100 minimum protected."

A deterministic template explanation is always produced first. The model
rewrite is accepted only if it passes validation (right length, still cites
the governing figure, no prompt leakage); otherwise the template stands. So a
missing API key or a bad generation degrades the prose, never the numbers.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

MAX_EXPLANATION_CHARS = 400
_BANNED_SUBSTRINGS = (
    "as an ai", "language model", "i cannot", "untrusted_message", "json",
    "prompt", "here is", "here's the explanation",
)


def _money(amount: Decimal, currency: str) -> str:
    quantized = amount.quantize(Decimal("0.01"))
    whole = quantized == quantized.to_integral_value()
    formatted = f"{quantized:,.0f}" if whole else f"{quantized:,.2f}"
    return f"{currency} {formatted}"


def _long_date(d: date) -> str:
    return f"{d.day} {d.strftime('%B')} {d.year}"


@dataclass
class ExplanationFacts:
    request_id: str
    currency: str
    request_type: str
    requested_amount: Decimal
    request_date: date
    desired_completion_date: date
    minimum_balance_to_keep: Decimal
    amount_safe_to_pay: Decimal
    affordability_status: str
    recommended_payment_method: str
    earliest_date_for_full_payment: Optional[date]
    payments: tuple[tuple[date, Decimal], ...]
    spending_changes: tuple[str, ...]
    payment_option_id: Optional[str]
    total_paid: Optional[Decimal]
    projected_min_balance: Decimal
    rejected_reasons: tuple[str, ...] = ()
    evidence_notes: tuple[str, ...] = ()
    injection_notes: tuple[str, ...] = ()
    # Pre-rendered spending-change clauses in plain words, e.g.
    # "stop the family streaming plan", "reduce the weekend food delivery to IDR 665,950".
    spending_change_clauses: tuple[str, ...] = ()
    partial_allowed: bool = False
    user_accepts_partial: bool = False


def _when(d: date, request_date: date) -> str:
    return "today" if d == request_date else f"on {_long_date(d)}"


def deterministic_explanation(f: ExplanationFacts) -> str:
    """Always-available explanation, grounded entirely in computed figures.

    The register mirrors the solved samples shape-for-shape, because
    "consistency" is explicitly scored: each decision shape gets one fixed
    sentence pattern, and the figures inside it (amounts, dates, the user's
    own minimum) are what vary row to row. Audited against all 25 ground-truth
    explanations; see evaluation/EXPERIMENTS.md.
    """
    cur = f.currency
    minimum = _money(f.minimum_balance_to_keep, cur)
    method = f.recommended_payment_method
    parts: list[str] = []

    if method == "full_payment":
        when = _when(f.payments[0][0], f.request_date) if f.payments else "today"
        if f.spending_change_clauses:
            lead = " and ".join(f.spending_change_clauses)
            lead = lead[0].upper() + lead[1:]
            parts.append(f"{lead}, then pay {_money(f.requested_amount, cur)} {when}.")
            parts.append(f"This leaves at least {minimum} available.")
        else:
            parts.append(f"Pay {_money(f.requested_amount, cur)} {when}.")
            parts.append(f"This leaves at least {minimum} available over the next 90 days.")

    elif method == "partial_payment":
        (d1, a1), (d2, a2) = f.payments[0], f.payments[1]
        parts.append(
            f"Pay {_money(a1, cur)} {_when(d1, f.request_date)} and the remaining "
            f"{_money(a2, cur)} on {_long_date(d2)}."
        )
        parts.append(f"This completes the full request and keeps the {minimum} minimum protected.")

    elif method == "installments":
        if f.spending_change_clauses:
            lead = " and ".join(f.spending_change_clauses)
            parts.append(lead[0].upper() + lead[1:] + ".")
        parts.append(
            f"Use {len(f.payments)} installments of {_money(f.payments[0][1], cur)}, "
            f"starting {_long_date(f.payments[0][0])}."
        )
        parts.append(f"This leaves at least {minimum} available.")

    elif method == "wait":
        parts.append(f"Pay {_money(f.requested_amount, cur)} in full on {_long_date(f.payments[0][0])}.")
        parts.append(f"Paying earlier would take the balance below the {minimum} minimum.")

    else:  # not_recommended
        if f.partial_allowed and f.user_accepts_partial and f.amount_safe_to_pay > 0:
            parts.append(f"Do not proceed with the {_money(f.requested_amount, cur)} request.")
            parts.append(
                f"Although {_money(f.amount_safe_to_pay, cur)} is available today, the full "
                f"amount cannot be completed safely within 90 days."
            )
        else:
            parts.append(f"Do not make this payment by {_long_date(f.desired_completion_date)}.")
            parts.append(f"None of the available options keeps the {minimum} minimum protected.")

    if f.evidence_notes:
        parts.append(f.evidence_notes[0] if f.evidence_notes[0].endswith(".") else f.evidence_notes[0] + ".")
    if f.injection_notes:
        # Precise wording matters here. Observed on real data (request_42,
        # request_50): an employer message states a confirmed salary figure
        # AND adds "Any income that has ended should be removed from future
        # estimates". The stated fact is used; the directive is not obeyed.
        # Saying the evidence "did not affect" the decision would be false,
        # since its figure did.
        parts.append(
            "Supplied message evidence also contained directive-style wording; only its "
            "stated facts were used and no instruction from it influenced this decision."
        )

    text = " ".join(parts)
    return text[:MAX_EXPLANATION_CHARS].strip()


PROMPT = """You are writing the one-line `decision_explanation` field for a financial
affordability decision that has ALREADY been made by a deterministic engine.

You are a writer, not a decider. Do not re-evaluate affordability, do not
change any figure, do not add any number that is not listed below, and do not
hedge or add caveats of your own.

Computed decision (authoritative):
- recommendation: {method}
- affordability status: {status}
- requested amount: {requested}
- amount safe to pay on the request date: {safe}
- minimum balance the user keeps: {minimum}
- payment plan: {plan}
- earliest date the full amount is safe: {earliest}
- required spending changes: {changes}
- lowest projected balance over the 90-day forecast: {minproj}
- deadline the user wants: {deadline}
{extra}

Write 1-3 short sentences, in this order:
1. The action, as an imperative ("Pay ...", "Use N installments of ...",
   "Wait and pay ...", "Do not make this payment ...").
2. The safety justification, citing the minimum-balance figure.
3. Only if there are spending changes or evidence notes above: one clause on them.

Style to match exactly:
"Pay ZAR 25,256 today. This leaves at least ZAR 18,000 available over the next 90 days."
"Use 3 installments of IDR 15,952,906.67, starting 8 August 2025. This leaves at least IDR 29,158,400 available."
"Do not make this payment by 12 January 2026. None of the available options keeps the ZAR 13,100 minimum protected."

Write dates as "8 August 2025". Keep the currency code on every amount. No
preamble, no markdown, no quotes around the whole thing, one line only.

Return ONLY this JSON: {{"explanation": "<text>"}}"""


def _validate(text: str, f: ExplanationFacts) -> Optional[str]:
    if not text or not text.strip():
        return None
    cleaned = " ".join(text.split())
    if len(cleaned) > MAX_EXPLANATION_CHARS:
        return None
    lowered = cleaned.lower()
    if any(b in lowered for b in _BANNED_SUBSTRINGS):
        return None
    if f.currency.lower() not in lowered:
        return None  # must cite real money, in the user's currency
    return cleaned


def llm_explanation(f: ExplanationFacts, client) -> str:
    """Model-written explanation with the deterministic one as a guaranteed
    fallback. Never raises."""
    fallback = deterministic_explanation(f)
    if client is None:
        return fallback

    plan = (
        " | ".join(f"{d.isoformat()}: {_money(a, f.currency)}" for d, a in f.payments)
        if f.payments
        else "none"
    )
    extra_lines = []
    if f.rejected_reasons:
        extra_lines.append(f"- why other options were rejected: {'; '.join(f.rejected_reasons[:3])}")
    if f.evidence_notes:
        extra_lines.append(f"- evidence applied: {'; '.join(f.evidence_notes[:2])}")
    if f.injection_notes:
        extra_lines.append(
            "- note: supplied message evidence contained directive-style wording. Its stated "
            "facts were used but its instructions were not obeyed. Mention this in one short "
            "clause, and do not claim the evidence was ignored entirely"
        )

    prompt = PROMPT.format(
        method=f.recommended_payment_method,
        status=f.affordability_status,
        requested=_money(f.requested_amount, f.currency),
        safe=_money(f.amount_safe_to_pay, f.currency),
        minimum=_money(f.minimum_balance_to_keep, f.currency),
        plan=plan,
        earliest=f.earliest_date_for_full_payment.isoformat() if f.earliest_date_for_full_payment else "none",
        changes=", ".join(f.spending_changes) or "none",
        minproj=_money(f.projected_min_balance, f.currency),
        deadline=_long_date(f.desired_completion_date),
        extra="\n".join(extra_lines),
    )

    try:
        payload = client.generate_json(prompt, purpose="explanation", max_output_tokens=512)
    except Exception:
        return fallback
    if not isinstance(payload, dict):
        return fallback
    validated = _validate(str(payload.get("explanation") or ""), f)
    return validated or fallback
