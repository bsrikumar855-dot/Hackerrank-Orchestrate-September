# Interview rehearsal

Use [the final audit](evaluation/FINAL_AUDIT.md) for full questions and evidence.
These are short spoken answers; do not replace uncertainty with confidence.

## Opener

“I separated semantic extraction from financial calculation. Gemini reads
document amounts and message amendments; deterministic code projects cash
flows and ranks eligible plans. The delivered output has 250 rows and the suite
has 142 passing tests. The final audits found a real false refusal, a
settlement-date recurrence defect, and discarded first-salary evidence. Stopping
an allowed streaming subscription made an offered installment plan safe. I
fixed that without changing the other 249 rows or the public sample score.
Amount accuracy remains the main limitation: only four of 25 public examples
match exactly. Arithmetic checks do not eliminate forecast uncertainty.”

## Why use a model here?

“Pixels and free-text amendments need interpretation. The model returns facts,
not a payment recommendation. Those facts still affect the numbers, so I check
their source membership, units, magnitude and direction before using them.
The actual message dataset is English; I have no empirical multilingual claim.”

## Does the deterministic core guarantee safety?

“Only relative to its modeled cash flows and timing convention. I replayed all
201 recommended plans independently and found no reserve breaches. But omitted
essential expenses or misread facts could invalidate that forecast. The public
amount errors show why I would not claim a real-world guarantee.”

## Why is the amount score weak?

“Per-description recurrence misses much spending spread across vendors.
The tested restoration variants often overcorrected and damaged decisions.
That explains a substantial concern, not every residual exactly. Some errors
are conservative, and one of the eight worst cases does detect a grocery
series. I did build an independent 652-sample temporal holdout on real
transaction history to test this properly — a category-level estimator cut
isolated forecast error from 100% to roughly a fifth. But three separate
versions of that fix, tested through the real decision pipeline, all made
actual decisions worse, including breaking two rows that were already exact.
So the weakness is real and independently measured, and I still don't have a
safe fix for it.”

## Are you overfitting the samples?

“The 25 examples were repeatedly consulted during development, so they are not
held out. Production does not look up their answers. The latest fix came from
an evaluation-row counterexample with a supplied option and permitted action,
not sample-score tuning. I have no independent hidden accuracy estimate.”

## What changed your mind during the final audit?

“The old conclusion said installment-plus-spending search lacked concrete
benefit. Request_267 disproved that. Its three payments leave EUR 1,496.16
without changes, below a EUR 1,500 floor. One permitted streaming stop raises
that to EUR 1,652.16. The same bounded search could test that schedule with a
small change. Regression tests and full replay justified shipping it.”

## Tell me about a rejected change

“An earlier blanket pre-credit convention removed 63 internal discrepancies
but contradicted the public example that pays after salary arrives that day.
The change was reverted. The lesson is that an internally consistent check
can encode the wrong assumption. Current timing conventions are explicitly
documented and the final replay follows them.”

## Can injection or hallucination still get through?

“Potentially. A plausible incorrect fact with a valid citation can pass schema
checks. Fencing, source validation and conservative guards reduce the risk;
they do not prove immunity. Optional model prose also lacks an entailment
check, which is why the submitted explanations are templates.”

## How much did it cost?

“The final replay made no model calls. The recorded history contains 35 calls
and 80,940 tokens, about USD 0.0237 using configured rate estimates. Five older
vision calls were not captured, so the historical figure is a lower bound,
not an exact total or invoice.”

## Why not call it Top 3?

“There is no verified leaderboard evidence. The architecture, debugging and
experimental discipline are strengths, but weak amount accuracy and
incomplete forecast coverage could keep it out of any particular rank. I do
have an independent generalization estimate now — a 652-sample holdout — and
it confirms the weakness is real rather than closing it; four fixes it
motivated were all rejected after end-to-end testing, including one that
preserved every categorical decision and still cost amount accuracy. I can
defend what was built and what was tested without promising a rank.”

## What's the hardest technical problem here?

“Distinguishing a genuine recurring commitment from a coincidental pattern
in rotating-vendor spending. Groceries, dining and transport get paid to a
different vendor almost every time, so per-vendor grouping either finds
nothing or occasionally latches onto one vendor's coincidental spacing as if
it were a standalone commitment. I traced and quantified both failure modes,
tried five structurally different fixes validated against 652 independent
holdout samples, and none produced a safe end-to-end improvement. That's the
open problem.”

## How do you handle corrupt or missing images?

“A missing image file returns no amount rather than guessing, and that path
never even calls the model. A corrupted one that reaches the vision call
fails there, and the caller catches it per-event so one bad image degrades
only that event's evidence, not the whole request. If the unresolved amount
is actually needed for the forecast, the row falls back to a schema-valid
`not_recommended` with an explanation naming the specific event, verified
against a real corrupted-image test case with no crash and no fabricated
number.”

## How do you handle foreign currency?

“Every cash event converts through the dataset's own fixed, dated exchange
rate for its settlement date and currency pair — never inverted, never
chained through an intermediate currency, never guessed. If no matching rate
exists, that event is excluded from the forecast with a logged reason rather
than converted at a nearby date. `decision_explanation` always cites the
user's home currency, verified against every real case where an event's
native currency differs from it.”

## How do you handle pending, scheduled, and settled transactions?

“Settled events set the starting balance and build recurrence history.
Scheduled and pending debits inside the forecast window are reserved as real
future cash outflows. Pending credits are not counted until they settle —
a bonus or refund in flight isn't safe to spend against. This follows the
spec's conflict-resolution order directly rather than a rule I invented.”

## How do you prevent inventing future income?

“Income is only projected when history actually supports it — a repeated,
confirmed salary pattern, not a one-off bonus or a single unconfirmed
gig payout. A credit series is treated as confirmed only if at least one
historical occurrence exactly repeats, which separates stable-or-stepped
salaries from genuinely variable commission pay across the whole dataset,
not just the public rows.”

## Why is a bounded, capped spending search acceptable?

“It's greedy and capped at three actions, documented as a known
simplification rather than dressed up as exhaustive. But it was also
exhaustively enumerated against every currently permitted action subset for
every current schedule across the real dataset, and that found zero further
misses beyond the one genuine counterexample, request_267, which is now
fixed and tested.”

## What makes this genuinely agentic, not just a script with an API call?

“The model does three things a fixed script can't: read a number off a
scanned document, extract a structured fact from free-text third-party
messages, and judge whether a message's evidence is trustworthy enough to
use. All three are real interpretation tasks. Every one of those outputs
then has to pass a fixed schema and a deterministic validation gate before
it can touch a financial number — the model never calculates, it only
perceives and extracts.”

## How is this reproducible?

“The delivered run uses cached evidence, so replaying it makes zero new
model calls. I've verified byte-for-byte reproduction of `output.csv`
directly from the extracted submission ZIP, in a clean temporary directory,
with no API key and no network access, more than once this session.”

## What would you improve with another week?

“A principled way to handle rotating-vendor category spend — probably a
proper uncertainty-aware cash-flow envelope rather than a point estimate,
since every point-estimate version I tried this session cost more accuracy
than it recovered. I'd also want a real semantic-entailment check on
extracted evidence instead of just structural provenance checks.”

## What's the biggest limitation, in one sentence?

“Amount forecasting for rotating-vendor essential spending is measurably
weak — independently confirmed on 652 real samples — and I don't yet have a
fix that improves it without costing something else.”

## Why should I trust this system at all?

“Not because it's accurate on every field — it isn't, and I say exactly
where. Trust it because every claim in this repository is checked against
real data before being written down, every accepted change has a
counterexample and a regression test, and every rejected change is left
visible with the numbers that killed it. That's a process you can audit,
not a score you have to take on faith.”
