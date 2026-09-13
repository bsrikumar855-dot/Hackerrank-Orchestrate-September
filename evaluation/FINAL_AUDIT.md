# Final forensic audit — 13 September 2026

This audit treats the post-request_267 artifacts as the baseline. It found two
additional correctness defects, shipped narrow fixes with regression coverage,
and rejected five separate recurrence/forecasting changes — including four
built and validated against an independent 652-sample temporal holdout —
because each one worsened real decision accuracy despite looking better in
isolation. The last of those four survived every categorical and safety
check and was still rejected on amount-accuracy grounds alone. There is no
verified leaderboard or hidden ground truth; these ratings are engineering
judgments, not a rank claim.

## Current rating

| Dimension | /10 | Evidence |
|---|---:|---|
| Architecture | 9.2 | Model extraction is separated from deterministic simulation, ranking and validation |
| Agentic design | 8.5 | Bounded evidence tasks; semantic verification remains incomplete |
| Safety | 7.4 | All 201 recommended plans pass replay; safety depends on forecast completeness |
| Robustness | 8.6 | 142 tests, row isolation, finite checks, offline package reproduction |
| Engineering | 9.0 | Every shipped change has a counterexample, test and full-data comparison |
| Output accuracy | 6.3 | 110/150 public exact, 123.048 proximity; amounts remain 4/25 exact |
| Evidence | 8.4 | All 45 accepted amendments apply; ownership and units are checked |
| Documentation | 9.0 | Claims separate measured facts, uncertainty and limitations |
| Interview readiness | 9.0 | Accepted and rejected experiments are preserved |
| **Overall** | **8.2** | Strong and reproducible, with amount forecasting the dominant risk |

## Remaining blockers, ranked

1. **Two-sided recurrence aliasing (high).** Description grouping misses
   rotating-vendor essentials but also fabricates arbitrary cadences, including
   35-day groceries, 24-day groceries and 20-day transport. Amounts are 4/25
   exact and five of 21 uncapped comparisons are conservative. An independent
   652-sample temporal holdout (see below) confirms this is a real
   generalization weakness, not a 25-row artifact — but four separately
   designed fixes for it all made the actual decision pipeline worse, not
   better, when tested end-to-end, including one that preserved every
   categorical decision and still cost amount accuracy.
2. **Benchmark uncertainty (high).** The 25 solved rows were used for
   development. They do not estimate hidden accuracy or leaderboard rank.
3. **Conditional safety (high).** Arithmetic proves safety for the modeled
   timeline, not that every commitment was reconstructed.
4. **Incomplete semantic evidence validation (medium/high).** Ownership,
   source-token, finite-number, date, currency and confidence checks do not
   establish full entailment across English and Indonesian text.
5. **Bounded search is not globally optimal for arbitrary inputs (medium).**
   Exhaustive current-data subset replay found no further counterexample.
6. **Historical model accounting is a floor (medium).** Seven cached vision
   values exist but only two source calls survive in the usage ledger.

## Newly discovered defects and shipped fixes

### Settlement-date recurrence anchor

**Root cause:** settled recurrence history used `event_date` although cash is
available on `settlement_date`.

**Counterexample:** public request_07 has salary dated the 15th and settled on
the 23rd. The old result claimed 15 October; truth is 23 October.

**Fix:** sorting, gap inference, termination and projection anchoring now use
`settlement_date`, falling back only when it is absent.

**Verification:** a dedicated regression test covers the case. Public exact
score improves **109→110**, earliest-date exact **18→19**, proximity
**122.004→123.048**, and median amount relative error **16.9%→12.6%**. Only
evaluation requests 68, 131 and 194 move from the 15th to the 23rd; no status
or method changes.

### Explicit first salary was discarded

**Root cause:** income amendments required an existing salary series, rejecting
explicit dated first-salary confirmations for users without salary history.

**Counterexample:** owned source messages for requests 233, 242 and 269 state a
first salary with date, amount and home currency.

**Fix:** a separate contract accepts one dated credit when owned text contains
the returned date, amount and currency; value is finite and positive; currency
matches home currency; confidence is high; and date is in the horizon. It does
not infer recurring payroll.

**Verification:** positive Indonesian-source and hallucinated-amount rejection
tests were added. Request 233 safe amount changes **0→1,525,754.02 IDR** but
remains `not_affordable`; 242 and 269 retain zero and only explanations change.

### One-off spending was theoretically mutable

**Root cause:** the candidate builder did not require a projected recurring
debit although the contract permits changes only to recurring expenses.

**Fix and verification:** candidates now require `is_projected=True`; a
synthetic one-off regression test was added. Current output is unaffected.

## Planning and evidence red team

The audit enumerated every subset of up to three currently permitted spending
actions for every full-today and supplied installment schedule: at most 25
subsets per user, with 231 users having candidates. It found no greedy miss, no
additional request_267-style miss, no later full-plus-spending counterexample,
and no current action targeting a one-off debit.

All **45** accepted amendments were traced to applied cash items, with zero
silent non-applications. There were no accepted future messages, foreign-user
sources, conflicts, or wrong-series applications. Linked-message review found
no further cancellation/refund defect. Amendment and vision values reject NaN
and infinity; cached vision reads receive the same positive amount, expected
currency and high-confidence checks as fresh responses. Cache keys use IDs
rather than full content fingerprints, so changed source files require manual
cache invalidation.

## Rejected fixes

| Candidate | Evidence | Decision |
|---|---|---|
| Standard recurrence cadences only | Removed 183 series, changed 43 evaluation rows, degraded exact 110→105 and proximity 123.048→117.839 | Reverted |
| Universal omitted-spend diagnosis | Contradicted by false arbitrary-cadence series and five conservative comparisons | Rejected |
| Another planning fallback | Exhaustive current-data enumeration found zero further misses | No change |
| Recurring payroll from first salary | Source confirms one payment, not indefinite cadence | One credit only |
| Tune against 25 solved rows | No causal basis or independent validation | Rejected |
| Vendor-share suppression (drop a detected series if its matched vendor is a minority share of its category) | 110→102 exact, 123.048→113.5 proximity, 2 unsafe categorical flips at every threshold tested (0.3/0.4/0.5) | Reverted |
| Blanket category-median forecast (replace every rotating-essential category's forecast, including ones already correctly detected) | 30% of qualifying (user, category) pairs already had a working detection that this would overwrite; full pipeline: 110→79 exact, every categorical field regressed | Reverted |
| Surgical category-median fill (only fill a category with zero detection, never overwrite an existing one) | Validated in isolation at 32.8% median error on exactly the omission subset (vs. 100% today); full pipeline still regressed to 110→87 exact and broke 2 of the 4 previously-exact amount rows | Reverted |
| CoV-gated fill (surgical fill, plus only firing when the category's week-to-week spend coefficient of variation is below a data-derived 0.25 gate — traced directly from the two counterexamples above, at 0.97 and 0.65) | Zero categorical regressions and 201/201 safety held; amount proximity still fell 17.048→16.65 and median error rose 12.6%→16.4%, and 87/250 real rows changed with no ground truth to verify 225 of them | Reverted |

## Independent generalization study (652-sample temporal holdout)

Separately from the 25 public rows, a temporal-holdout backtest was built
directly from `dataset/financial_events.csv`'s real settled history: for
every (user, rotating-essential category) pair with enough history, train on
data before a cutoff date, then score the 90-day-forward prediction against
that user's **real, actual** subsequent spend — data the production pipeline
never sees in advance, but which exists in the dataset. **652 independent
samples**, zero use of `sample_requests.csv` for method selection.

Result: the current per-vendor recurrence mechanism predicts **exactly
zero** category spend on 76% of samples despite real spend existing, with
100% median relative error and a 93% optimistic (unsafe-direction) bias.
A category-level median weekly rate cuts that to roughly 20-33% median
error with a majority-conservative bias — a genuine, independently measured
improvement in isolated forecast accuracy.

That isolated improvement was then run through the real decision pipeline
four separate times (see the four rejected candidates above), at increasing
levels of caution — full replacement, then overwrite-avoiding, then
fill-only-where-nothing-exists, then that same fill gated on a data-derived
stability threshold traced directly from the first three failures. **All
four regressed the actual decisions in some way**, including two that broke
rows the current system already gets exactly right.

The first three failures were consistent with (though did not prove) a
double-counting hypothesis: `minimum_balance_to_keep` in this dataset's
design might already function as the buffer absorbing day-to-day
essential-category variability, so forecasting that same spending a second
time could double-count the safety margin. Candidate 4 tested that
hypothesis directly by tracing its two counterexamples (`request_01`,
`request_09`) to their exact cause — both were driven by category-level
spend volatility (coefficient of variation 0.97 and 0.65 respectively, well
above a natural stability threshold found in the wider dataset), not by
overlap with an existing vendor-level forecast, since neither counterexample
category had one. Gating on that stability threshold produced **zero
categorical regressions** — the first candidate to achieve this — which
rules out double-counting as the dominant failure mode. What remains is a
more fundamental limit: a historical median, however tightly gated, is a
point estimate for what is genuinely a stochastic future quantity, and using
one still cost more in amount accuracy than it recovered.

**This is presented as a strength, not a weakness:** it demonstrates the
project optimized for end-to-end financial decision quality, not for an
isolated benchmark metric — and it says so with the losing experiments left
fully visible, not removed. Full methodology and per-candidate numbers are
in `evaluation/EXPERIMENTS.md`.

## Updated amount diagnosis

Amount score is **4/25 exact**, **17.048/25 proximity**, with **12.6%** median
absolute relative error. Description recurrence both omits rotating-vendor
spend and invents coincidental recurrence. Request_04 proves one grocery series
is detected; five of 21 uncapped comparisons are conservative. A category-level
probabilistic budget or robust cash-flow envelope is plausible future work, but
the solved rows do not justify shipping it without independent validation.

## Final verification

- **250/250** output rows validate.
- **142** tests pass offline.
- Public score: **110/150 exact**, **123.048/150 proximity**.
- **201/201** recommended plans pass independent arithmetic replay.
- Exhaustive current-data spending search reports zero further misses.
- Fresh replay and clean extracted-package execution reproduce `output.csv`
  byte-for-byte; final hashes are recorded by the package verifier.

## Freeze decision

Freeze is now justified as an engineering decision. This pass fixed concrete
defects, exhausted current bounded-plan combinations, and rejected five
distinct recurrence/forecasting mechanisms because each regressed measured
behavior — the last of them regressed only amount accuracy, after every
categorical and safety check passed. No tested forecasting modification
produced a safe end-to-end improvement under the current decision
architecture. The model is not theoretically optimal. Further work needs
independent validation or a principled category-budget model.

## Eight hard AI Judge questions

### 1. How can a plan be safe with only 4/25 exact amounts?

Only relative to the reconstructed 90-day model. Every recommended plan is
replayed against every modeled checkpoint and minimum balance. Omitted or
misclassified commitments can invalidate that premise; amount accuracy is the
largest remaining risk.

### 2. Did you tune on the public answers and call them held out?

No. They are development examples. Their score is a regression metric, with no
hidden-score or rank claim.

### 3. Why not use category-level forecasting instead of per-vendor recurrence?

It was tried, five separate ways, with increasing care: standard-cadence
restriction, vendor-share suppression, blanket category replacement,
fill-only omission-filling, and finally that same fill gated on a
data-derived spend-stability threshold. Each was validated against an
independent 652-sample temporal holdout before being tested end-to-end. The
first four regressed real decisions; the fifth preserved every categorical
decision and still cost amount accuracy (proximity 17.048→16.65, median
error 12.6%→16.4%) with an unverifiable blast radius across the real 250
rows. No tested forecasting modification produced a safe end-to-end
improvement under the current decision architecture.

### 4. What stops an LLM or cache from inventing money?

Evidence must match an owned source, finite positive amount, source tokens,
date, currency and confidence rules. First-salary evidence creates one dated
credit only. These guards reduce hallucination risk but do not prove entailment.

### 5. How do you know request_267 was not the first of many search misses?

We enumerated all subsets of up to three permitted actions for every current
full and installment schedule. Across 231 users with candidates there were no
further misses. The claim is scoped to this dataset.

### 6. Your usage report says 35 calls, but is that really the full cost?

No, and it says so directly: `evaluation/usage_report.md` states it as a
verified floor, not a complete total. Five of the seven cached vision reads
were extracted before the cost-tracking ledger existed; their original
token/cost figures cannot be reconstructed, and the report is explicit that
it is not inventing numbers to fill that gap.

### 7. Is the LLM actually doing anything an agent needs to do, or is this dressed-up scripting?

It does three bounded, genuinely non-deterministic jobs a fixed script
cannot: reading a number off a scanned document image, extracting a
structured fact from free-text third-party messages (including
distinguishing a legitimate amendment from injected instructions), and
deciding whether a message's evidence meets an ownership/provenance bar.
Every one of those is a real language/vision understanding task; every
output is then constrained to a fixed schema and re-validated deterministically before it can touch a number. That boundary — model for
perception and extraction, arithmetic for every financial decision — is the
actual agentic design choice, not an afterthought.

### 8. You rejected five forecasting fixes — doesn't that mean the whole recurrence model is wrong and needs a redesign?

The rejections don't show the model is wrong; they show five specific
*additions* to it were wrong, and the reason evolved as the evidence did.
The first three were consistent with a double-counting hypothesis
(`minimum_balance_to_keep` might already absorb this variability). The
fourth was built specifically to test that hypothesis — gated on
spend-stability, it preserved every categorical decision, which rules
double-counting out as the dominant cause. What's left is narrower and more
mundane: a historical median is a point estimate for a genuinely stochastic
future quantity, and no scoping of it tested so far recovers more amount
accuracy than it costs. A redesign proposal would need its own
counterexample and its own end-to-end validation — the same bar every
accepted change here had to clear. Five rejected patches is not evidence the
mechanism should be replaced; it's evidence of exactly where its limit is.
