# Experiment log

This is a chronological historical record. For current results and corrections
to earlier causal/freeze claims, see [FINAL_AUDIT.md](FINAL_AUDIT.md). Public
samples were used repeatedly during development; they are not a held-out test.
The latest production changes address an installment/spending false refusal,
settlement-date recurrence, source-verified first salaries, and evidence
validation. Current score is 110/150 exact and 123.048 proximity, with 12.6%
median amount error. A standard-cadence-only detector was rejected: it removed
183 series, changed 43 evaluation rows and degraded the score to 105 exact and
117.839 proximity. Exhaustive subsets of up to three spending changes found no
further request_267-style miss.

Every hypothesis tested against `dataset/sample_requests.csv`, in one format,
including the ones that were rejected. Baseline for all entries below unless
stated otherwise (25 samples, evidence layer warm, exact match):

| Field | Baseline |
|---|---|
| `amount_safe_to_pay` | 4/25 (16%) |
| `affordability_status` | 20/25 (80%) |
| `recommended_payment_method` | 23/25 (92%) |
| `payment_plan` | 20/25 (80%) |
| `earliest_date_for_full_payment` | 15/25 (60%) |
| `spending_changes_needed` | 22/25 (88%) |
| **Total field matches** | **104/150** |

---

## D1 — Is "complete by `desired_completion_date`" an eligibility gate or a ranking preference?

**CLAIM.** The implementation treats deadline completion as a *hard eligibility
gate*, which filters out 434 of 515 (84%) supplied installment options before
ranking. The spec is genuinely ambiguous: `problem_statement.md` states it
conjunctively under the 90-day safety check ("The plan must complete the
request by `desired_completion_date` **and** keep the user above their minimum
balance"), but also lists it as tie-break criterion #1 under "Choosing Between
Safe Plans", which reads as a preference over already-eligible plans. If the
gate is wrong, a large share of the 250 installment recommendations are being
suppressed before ranking ever runs.

**METRIC.** For every ground-truth row whose `recommended_payment_method` is
`installments`, does the plan's last payment date fall after that row's
`desired_completion_date`? A single late-finishing ground-truth plan disproves
the gate.

**BASELINE.** Gate active (current behaviour).

**RESULT.** 0 of 5 ground-truth installment plans finish after the deadline:

| request | last payment | deadline | verdict |
|---|---|---|---|
| request_02 | 2025-10-07 | 2025-10-10 | within |
| request_07 | 2024-11-07 | 2024-11-14 | within |
| request_12 | 2026-06-20 | 2026-06-20 | within (exactly on) |
| request_17 | 2026-04-30 | 2026-05-04 | within |
| request_22 | 2025-02-02 | 2025-02-10 | within |

Two further observations. `request_12` completes *exactly on* its deadline,
which corroborates that the comparison is `<=` rather than `<` — the
implementation already allows equality. And because only ~16% of supplied
installment options finish on time at all, five independent ground-truth
choices all landing inside the deadline is unlikely under a
preference-only reading (selection is not random, so this is directional, not
a p-value).

**BLAST RADIUS.** No code changed. Had the gate been demoted, it would have
affected eligibility for every request with installment options (275 of 275),
i.e. the highest-blast-radius change available in this codebase.

**VERDICT.** **Gate corroborated — kept.** Conjunctive reading in
`problem_statement.md` plus 5/5 sample agreement. Noted as the one design
decision where the spec text alone is genuinely ambiguous, so it is called out
explicitly in `INTERVIEW.md` rather than presented as obvious.

---

## D2 — Same-date debit/credit ordering *(accepted — the only accepted change of the three)*

**CLAIM.** `daily_balances` netted each day's debits and credits into a single
end-of-day figure. That hides the intraday low when a debit and a credit share
a date: if an expense clears before the salary arrives, the true low is below
the netted end-of-day balance. The dataset supplies no intraday timing, so the
conservative convention is debits before credits — and netting would produce
exactly the observed error signature (`earliest_date_for_full_payment` too
early, `amount_safe_to_pay` too high).

**METRIC.** Per-field exact match over all 25 samples, plus the signed gap
between the projected minimum balance and the minimum implied by ground truth
(positive = too optimistic). Relevance first: 8 of 25 sample rows contain a
same-date debit+credit collision (21 such days), so the convention is
material, not theoretical.

**BASELINE.** Netted end-of-day balance; 104/150 field matches.

**RESULT.** Debits post first, contributing an intraday check point as well as
an end-of-day one; both must satisfy the minimum.

| Field | Netted | Debits-first |
|---|---|---|
| `amount_safe_to_pay` (exact) | 4/25 | 4/25 |
| `amount_safe_to_pay` (within 2%) | 4/25 | 6/25 |
| `affordability_status` | 20/25 | **21/25** |
| `recommended_payment_method` | 23/25 | 23/25 |
| `payment_plan` | 20/25 | **21/25** |
| `earliest_date_for_full_payment` | 15/25 | **18/25** |
| `spending_changes_needed` | 22/25 | 22/25 |
| **Total field matches** | 104 | **109** |

Error-signature check (the point of the test): rows landing on the optimistic
side of ground truth fell from **25/25 to 20/25**; rows within 2% rose 6→7 and
within 5% 11→12. The *median* gap was unchanged at 5.58%, which is the
expected shape — only the 8 collision rows can move, and the median row has no
collision. So this is a targeted mechanism fix, not a global shift.

Full 25-row diff: 8 rows changed on at least one field. `request_04` corrected
on four fields at once (status, method, plan, earliest date — `full_payment`
→ `wait`, matching truth). `request_23` and `request_06` corrected their
earliest date. One regression: `request_06`'s method/plan went
`full_payment` → `not_recommended` where truth is `full_payment`, i.e. that
row became too conservative. Net +5 field matches, accepted with the
regression recorded rather than hidden.

**BLAST RADIUS.** `core/forecast.py::daily_balances` only — but that function
underpins every numeric output, so this is a wide change by construction. The
candidate payment is itself a debit and therefore posts before same-day
income, which is the conservative placement. Verified: 104 unit tests pass
(2 new ones pin the ordering and prove netting would hide a real breach), the
full 250-row run regenerated, and `evaluation/validate_output.py` passes with
zero errors.

**VERDICT.** **Accepted.** Principled (conservative convention where the data
is silent, consistent with the spec's "financially safer interpretation"
tiebreak) *and* measured (+5 field matches, optimism reduced on 5 rows).

---

## D3 — Buffer applied only to estimated-amount series *(rejected)*

**CLAIM.** Distinct from the uniform uplift rejected earlier (D6 below).
Categories with rotating vendors (groceries, transport, dining, shopping,
entertainment) have known *timing* but an *estimated amount*, so a buffer on
just those is principled, whereas a buffer on fixed recurring amounts that are
known exactly is not — which would explain why the uniform sweep found no
optimum.

**METRIC.** Per-field exact match over all 25 samples at buffer 1.00 / 1.10 /
1.20 / 1.30. Pre-registered falsification rule: monotonic worsening as the
buffer grows is evidence that 1.10 is real signal; a flat or noisy curve means
it is a fit and must be rejected.

Surface area checked first, rather than assumed: of 176 projected debit series
across the sample users, **78 have varying recent amounts** (the projected
figure is genuinely an estimate) and 98 have identical recent amounts (known
exactly). The varying ones include groceries (11), transport (10),
entertainment (10), shopping (12), dining (2), plus utilities (25) and
healthcare (8). So the hypothesis was testable. Note the selector is derived
from the data ("do this series' recent amounts differ?") rather than a
hardcoded category list.

**BASELINE.** No buffer; 109/150 field matches (post-D2).

**RESULT.**

| Buffer | Total | `amount` exact | `amount` ≤2% | status | method | plan | earliest | spend |
|---|---|---|---|---|---|---|---|---|
| **1.00** | **109** | 4 | 6 | 21 | 23 | 21 | 18 | 22 |
| 1.10 | 102 | 3 | 5 | 19 | 22 | 20 | 17 | 21 |
| 1.20 | 102 | 3 | 3 | 19 | 21 | 19 | 18 | 22 |
| 1.30 | 105 | 3 | 4 | 20 | 21 | 19 | 20 | 22 |

Every buffer level is worse than no buffer, and the curve is **not
monotonic** (1.30 scores above both 1.20 and 1.10). That fails the
pre-registered criterion.

**BLAST RADIUS.** None — tested by patching `detect_recurring_series` in a
scratch harness; no module was modified.

**VERDICT.** **Rejected.** Mechanically the reason is clear in hindsight: the
projected amount for a debit series is already `max` of the last three
occurrences, which is itself a conservative estimator for a varying series.
Layering a buffer on top of an already-conservative maximum double-counts the
caution and overshoots. This is a *different* hypothesis from the uniform
uplift and was rejected on its own evidence, not by analogy.

---

## Earlier experiments (recorded before this log existed)

### D4 — Category-level recurrence pooling *(rejected)*

**CLAIM.** A category like `groceries` splits across several vendor
descriptions whose individual cadence looks irregular, while the pooled
category-level cadence is perfectly clean (verified on user_23: 25 grocery
occurrences, every 7 days, population stdev 0.0; transport every 14 days,
stdev 0.0). Grouping by `(category, description)` therefore projects nothing
for those categories and undercounts essential spending.

**METRIC.** Per-field exact match on the 25 samples, with both `max`-of-3 and
`mean`-of-3 as the per-occurrence amount.

**RESULT.** `affordability_status` and `recommended_payment_method` fell to
52–60%, versus 80–92% with per-description grouping. A flat per-occurrence
amount compounds across ~13 weekly occurrences and overshoots badly.

**VERDICT.** **Rejected and reverted.** Cross-check that settled it: for
user_23, ground truth's implied minimum balance sits ~2.4k from the
per-description projection, whereas counting pooled groceries would move it by
~22k. The reference does not count that spending, so pooling is wrong here
despite the clean cadence.

### D5 — Burn-rate projection of irregular categories *(rejected)*

**CLAIM.** For categories with ≥3 settled debits whose per-vendor series are
all rejected as irregular, project total historical spend ÷ observed span ×
90 days.

**METRIC.** Signed gap between projected minimum balance and the minimum
implied by ground truth, per row.

**RESULT.** Closer on 4 rows, **worse on 19 of 23** scorable rows, overshooting
heavily (e.g. request_02 gap +361k → −4.86M).

**VERDICT.** **Rejected.**

### D6 — Uniform conservative uplift on all projected expenses *(rejected)*

**CLAIM.** "Forecast essential variable spending conservatively" might mean a
flat safety margin on projected expenses.

**METRIC.** Sweep 0 / 5 / 8 / 10 / 12 / 15 / 20 / 25%; count rows within 2%
and 5% of ground truth, plus median relative gap.

**RESULT.** No optimum. Rows within 2% moved 7→9 around 8–10% but rows within
5% did not improve at any level, and the median gap hovered ~4% throughout.

**VERDICT.** **Rejected** — the residual is not a uniform buffer. D3 above
tested the narrower, better-motivated version of this idea and also rejected
it.

### D7 — Explicit-vs-projected collision suppression *(accepted)*

**CLAIM.** An explicit `scheduled` "Next confirmed salary" row can land on the
same date as the projected occurrence of the payroll series for the same
amount, so the paycheck is counted twice.

**METRIC.** Count collisions dataset-wide; then per-field match.

**RESULT.** **45 credit collisions** (44 with matching amounts) and 9 debit
ones across the dataset — the salary case is real and systematic. Suppressing
the projection when an explicit row of the same category and direction lands
within 3 days for an amount within 15% gave a net +1 field match on the
samples (93→94 measured on the deterministic core alone) and corrected
`request_25`'s `earliest_date_for_full_payment` to exactly match truth.

**VERDICT.** **Accepted.** The amount-similarity guard is what keeps unrelated
same-category one-offs (a "Pending fuel authorization" vs a recurring
"Commuter pass") additive; without it the rule would wrongly suppress real
expenses. Regression-tested in both directions.

---

## Net effect of the accepted changes

| Field | Before D2/D7 | Now |
|---|---|---|
| `amount_safe_to_pay` (exact) | 4/25 (16%) | 4/25 (16%) |
| `amount_safe_to_pay` (within 2%) | 4/25 (16%) | 6/25 (24%) |
| `affordability_status` | 20/25 (80%) | 21/25 (84%) |
| `recommended_payment_method` | 23/25 (92%) | 23/25 (92%) |
| `payment_plan` | 20/25 (80%) | 21/25 (84%) |
| `earliest_date_for_full_payment` | 15/25 (60%) | 18/25 (72%) |
| `spending_changes_needed` | 22/25 (88%) | 22/25 (88%) |
| **Total** | **104/150** | **109/150** |

Three hypotheses accepted (D2, D7, and the calendar-month drift fix), four
rejected on measurement (D3, D4, D5, D6). With 25 samples each row is worth 4
percentage points, so all figures here are directional, not precise.

---

# Second round: hunting the one-sided residual

Motivation for reopening: the residual is **not** noise. Excluding the 4 rows
where ground truth's `amount_safe_to_pay` is capped at `requested_amount`
(there the implied minimum is only a lower bound, so the row is unmeasurable),
**16 of 21** measurable rows sit on the optimistic side, median +4.47% of
90-day debits. Genuine per-user noise would be ~50/50. Every hypothesis below
was ranked by "can it plausibly make the forecast *one-sidedly* optimistic?"
and given a pre-registered SHIP/REJECT criterion before running.

## D8 — Hypothesis A: recurring debits with only 2 observations are dropped *(rejected on sizing)*

**CLAIM.** The detector needs 3 observations; a real recurring expense with 2
is dropped, which is one-sided optimistic.

**METRIC.** Debit mass in dropped 2-observation series as % of each row's
projected 90-day debits. Pre-registered: ~0% → reject; same order as the 4.5%
gap → test a 2-observation rule.

**RESULT.** Naive sizing (every pair projected at its own gap): median
**41.7%** of projected debits. Cadence-constrained (gap within ±3 days of
7/14/30): median **19.1%**, and the constrained pairs are *entirely* groceries
(15 pairs), dining (14) and transport (12) — rotating vendors.

**VERDICT.** **Rejected.** The dropped mass is 4–9× larger than the gap and
sits in exactly the categories D4 showed the reference does not count. A
2-observation rule would overshoot the way pooling did. Reported anyway: it is
evidence about what the reference is *not* doing.

## D9 — Hypothesis B: event statuses and one-time future debits *(clean)*

**RESULT.** Counted, not just read: inside the 25 sample windows there are
**3 scheduled debits, 8 pending debits, 5 scheduled credits, 1 pending
credit** (the last excluded, per spec). 10 of 25 rows have a future debit in
window. **Every scheduled and pending debit is present in the forecast's cash
items; none missing.** One-off future debits enter through the same
explicit-row path as recurring ones.

**VERDICT.** **Clean.**

## D10 — Hypothesis C: horizon inclusivity and per-day enforcement *(clean, now pinned by tests)*

**RESULT.** Horizon is `[request_date, request_date+90]` inclusive on both
ends (91 check days); an expense landing exactly on day 90 is counted, one on
day 91 is not. The minimum is enforced on every check point, not only on
payment dates: a one-day breach on day 40 with no payment near it caps
today's safe amount and blocks every later date. `tests/test_forecast_boundary.py`
(5 tests) pins all of this. One of my own assertions was initially wrong (a
dip to 150 against a 100 minimum is a dip, not a breach) — fixed the test,
not the code.

**VERDICT.** **Clean.**

## D11 — Hypothesis D: distribution of my 250 vs ground truth *(corroborates systematic optimism)*

| | my 250 | truth 25 |
|---|---|---|
| `affordable_now` | **31%** | 12% |
| `not_affordable` | 19% | **28%** |
| `spending_changes_needed` ≠ none | 0% | 12% |
| `earliest_date_for_full_payment` blank | 14% | 28% |

**VERDICT.** Signal only, never tuned to. The optimism is real and scales
beyond the 25 rows I can score.

## D12 — "Essential variable spending" = variable categories the user protects *(rejected)*

**CLAIM.** "Forecast **essential** variable spending conservatively" — and
"essential" has a concrete field, `expense_categories_to_protect`.

**RESULT.** Falsified in both directions: `request_23` has groceries protected
(24% pooled mass available) with a −0.5% gap; `request_06` has transport
protected with −0.2%; meanwhile `request_07/11/18/20` protect no variable
category and still show +7–11% gaps.

**VERDICT.** **Rejected.**

## D13 — Add a category-level series for rotating-vendor essentials *(rejected on the decision fields)*

**CLAIM.** Unlike D4 (which *replaced* per-description grouping everywhere at
max-of-3), this *adds* one category-level series only where no per-vendor
series exists, at the category **mean**. Localisation first: the three
outlier rows (`10` +56.8%, `13` +34.9%, `05` +23.8%) all reach their minimum
*before any income lands*, so income-side variants cannot move them (seven
income variants tested: zero effect). The gap is on the debit side.

**METRIC (balance).** Optimistic rows, rows within 2%/5%, median signed gap.
**METRIC (decisions; the pre-registered SHIP rule).** Total field matches
> 109, optimistic count down, ≤ 2 `recommended_payment_method` regressions.

**RESULT — balance metric, strikingly good:**

| variant | within 2% | within 5% | optimistic | median gap | mean abs gap |
|---|---|---|---|---|---|
| as-is | 6 | 11 | 16/21 | +4.47% | 9.14% |
| + groceries (mean) | 10 | 17 | 14/21 | +0.62% | 3.54% |
| + groceries + transport (mean) | 10 | 17 | **10/21** | **−0.05%** | 4.07% |
| + groceries + transport + dining | 10 | 18 | 7/21 | −1.21% | 4.21% |

Groceries + transport at the mean centres the bias almost exactly (10/21, i.e.
50/50) and halves the mean gap. On `request_13` it lands the minimum at
1731.08 against a 1733.40 target (0.13%).

**RESULT — decision metric, decisive the other way:**

| variant | TOTAL | amt | status | method | plan | earliest | spend |
|---|---|---|---|---|---|---|---|
| as-is | **109** | 4 | 21 | 23 | 21 | 18 | 22 |
| groceries (mean) | 101 | 3 | 19 | 20 | 19 | 18 | 22 |
| groceries+transport (mean / median / max-of-3) | 101 | 3 | 19 | 20 | 19 | 18 | 22 |
| all three / any category (mean) | 95 | 2 | 18 | 19 | 18 | 16 | 22 |

Three `recommended_payment_method` regressions (`08`, `11`, `12` flip to
`not_recommended`/`wait` where truth has a plan) — over the pre-registered
limit — and the total falls by 8. The full 25-row diff shows the pattern:
eight rows move to within ~5% of truth's amount (`14`: 594.87 vs 597.74;
`15`: 78.44 vs 83.05; `17`, `18`, `24` similar) while others overshoot badly
(`05` and `08` collapse to 0; `23` halves to 4,328 vs 9,152).

**VERDICT.** **Rejected.** The reference evidently counts rotating-vendor
essential spending *partially* — somewhere between my zero and full pooling —
and no uniform form reproduces it: every form that closes the balance gap
breaks decisions on 3+ rows. This is the sharpest statement of the limitation
I can make (see `INTERVIEW.md` §5).

## D14 — Looser per-vendor regularity as the "partial" mechanism *(rejected)*

**CLAIM.** A vendor seen every ~77 days projected at its *own* gap contributes
~1 occurrence, not 13 — a natural way to get a partial count.

**RESULT.** Every loosening is worse than as-is (109): stdev≤10 → 103;
stdev≤20 → 93; min 2 obs + stdev≤10 → 76; no stdev filter → 67 / 62.

**VERDICT.** **Rejected.** The current filter (≥3 observations, gap stdev
≤ 5 days, snap to 7/14/30 ± 3) is right.

## `request_06` regression from D2 — answered

Not a second mechanism. My projected minimum is **1398.01**; truth's implied
minimum is **1403.30** — a €5.29 difference on a €1,942 balance (0.3%). But
the requested €620.40 sits on a knife edge: my shortfall is 22.39 and truth's
is 17.10, and the only permitted spending change (stopping the €19/month
family streaming plan, `event_476`) frees exactly €19 by the binding date.
Under truth's forecast €19 covers the shortfall, so it recommends "stop the
plan, then pay"; under mine it falls €3.39 short, so no spending plan reaches
safety and the row degrades to `not_recommended`. The spending-change
machinery found the right action (`stop:event_476`, frees 57 over the window)
— it simply was not enough under a forecast 0.3% more conservative. One row of
near-noise, not a defect.

## Stop

Hypotheses A–C measured (A rejected on sizing, B and C clean), D corroborates
the bias at scale, and the leading debit-side candidate was tested in three
families (protected-category gating; category-level pooling in six forms;
loosened per-vendor filters in five) and rejected on the decision fields every
time. No fifth speculative hypothesis was started.

---

# Third round: is the metric itself right?

## D15 — Exact-match vs proximity grading for `amount_safe_to_pay` *(both metrics agree: REJECT pooling)*

**CLAIM.** Every pooling rejection so far used "field matches", which scores
`amount_safe_to_pay` as binary exact-match. But the spec grades *accuracy* of
that field, which most plausibly means proximity to the true value. Under a
proximity reading, pooling takes the median signed gap from +4.47% to −0.05%
and the exact-match scorer credits that with nothing. If the proxy and the
target diverge here, the divergence decides whether the strongest finding
ships.

**METRIC.** A second scorer (`evaluation/score_dual.py`, kept permanently
alongside the first, neither replacing the other):

- `EXACT`     — all six fields exact-match. Max 150.
- `PROXIMITY` — the five categorical fields exact-match; `amount_safe_to_pay`
  scored `1 − min(1, |pred−true|/true)` per row and summed. Max 150.

**BASELINE (shipped pipeline).** `EXACT 109.0/150`, `PROXIMITY 122.0/150`.
The amount field alone earns **4/25 exact but 17.00/25 on proximity** — so the
proximity scorer does exactly what it should: it credits near-misses the exact
scorer throws away. Median relative error on the amount: 16.9%.

**RESULT.** Both scorers reject pooling, at every weight:

| variant | EXACT | PROXIMITY | amount exact | amount prox | median abs err |
|---|---|---|---|---|---|
| **w=0 (shipped)** | **109.0** | **122.0** | 4/25 | 17.00/25 | 16.9% |
| w=0.2 | 106.0 | 119.1 | 4/25 | 17.14/25 | 15.3% |
| w=0.4 | 106.0 | 120.1 | 4/25 | **18.10/25** | **12.6%** |
| w=0.6 | 96.0 | 110.7 | 3/25 | 17.67/25 | 13.4% |
| w=0.8 | 88.0 | 100.0 | 2/25 | 13.99/25 | 35.9% |
| w=1.0 | 77.0 | 85.6 | 2/25 | 10.61/25 | 67.3% |

**Where the two scorers disagree, precisely.** They disagree about the
**amount field in isolation**: proximity says pooling at w=0.4 improves it
(18.10 vs 17.00, median error 12.6% vs 16.9%), while exact-match says it
changes nothing (4/25 either way). They **agree about the total**, because
the categorical losses dominate: at w=0.4 the amount gains +1.10 proximity
points while the five categorical fields lose 3 exact matches, netting −1.9.

That asymmetry is structural, not a quirk of this dataset: **five of the six
scored fields are categorical.** The amount field is at most 1/6 of the score
under either reading, so it cannot pay for damage to the other five. This is
the substantive answer to "am I optimising a proxy instead of the target" —
here the proxy and the target point the same way, and the reason they do is
that the target is mostly categorical.

**VERDICT.** **Pooling rejected under both metrics.** The question is closed:
it was not an artefact of exact-match scoring. Both scorers remain available
(`evaluation/score_dual.py`) so any future change is judged under both.

## D16 — Pareto-safe partial pooling weight *(no such weight exists)*

**CLAIM.** Full pooling centres the bias but flips decisions. Perhaps a
partial weight closes some of the gap while flipping none.

**METRIC (pre-registered SHIP rule).** Ship only a weight where
**decisions flipped = 0 AND the amount error strictly improves.** Anything
else rejects the whole family. "Decisions flipped" counts rows where
`affordability_status` or `recommended_payment_method` changes versus w=0;
any-field flips reported alongside.

**RESULT.**

| w | decision flips | any-field flips | amount proximity | median signed gap | optimistic rows | verdict |
|---|---|---|---|---|---|---|
| 0.2 | **1** | 3 | 17.14 (↑) | +3.34% | 16/21 | reject |
| 0.4 | **1** | 3 | 18.10 (↑) | +1.42% | 15/21 | reject |
| 0.6 | 4 | 7 | 17.67 (↑) | −0.28% | 9/21 | reject |
| 0.8 | 8 | 11 | 13.99 (↓) | −3.01% | 6/21 | reject |
| 1.0 | 10 | 11 | 10.61 (↓) | −5.06% | 4/21 | reject |

The bias does respond smoothly and monotonically to the weight — median gap
+4.47% → +3.34% → +1.42% → −0.28% → −3.01% → −5.06%, crossing zero near
w≈0.55, with optimistic rows falling 16/21 → 4/21. So the mechanism is real
and it is a *dose-response* curve, which is strong evidence it is not noise.
But **the smallest weight that measurably moves the gap already flips a
decision.** There is no dead zone.

**VERDICT.** **Whole family rejected.** No Pareto-safe weight exists: the
bias cannot be closed without moving decisions. That is the cleanest available
statement of the limitation, and it is stated as such in `INTERVIEW.md` §5.

## D17 — Why zero spending changes across 250 rows *(reachable, correctly gated, symptom of the bias)*

**CLAIM.** Ground truth has spending changes on 12% of sample rows; my 250-row
output has 2 (0.8%). A branch that almost never fires is either unreachable or
gated too tightly.

**METRIC.** Instrument the gate over all 250 real requests and count where
each one stops.

**RESULT.**

| outcome | rows |
|---|---|
| total | 250 |
| gate not reached — a no-change plan was **already safe** | **198** |
| gate not reached — user does not accept `full_payment` | 32 |
| **enumeration reached** | **20** |
|  → eligible actions existed but freed too little | 18 |
|  → **winner produced** | **2** (`request_94`, `request_114`) |
|  → zero eligible actions (all vetoed) | 0 |

So the branch is **reachable and correctly gated**, not dead code, and nothing
is being vetoed away: on all 20 reached rows eligible actions were found. On 18
of them the available flexible spending is one to two orders of magnitude
smaller than the shortfall — e.g. `request_108` can free 1,263.90 against a
50,571 shortfall; `request_29` can free 3,476.44 against 27,418. No
combination of ≤3 changes could ever close those, so emitting `none` is
correct, not a miss.

**The dominant term is the 198 rows where a no-change plan was already safe** —
which is precisely the optimism bias expressing itself in a second scored
field. Tracing all three sample rows that ground truth solves with a change:

- `request_11` — gate **not reached**. My `amount_safe_to_pay` is exactly the
  requested 13,110,000 so full payment looks safe with no change; truth has
  12,510,645, i.e. 4.6% short, so truth *must* change spending.
- `request_21` — gate **not reached**, same shape: mine 1,574.40 (= requested),
  truth 1,543.35, 2.0% short.
- `request_06` — gate **reached**, correct action **found**
  (`stop:event_476`), but €3.39 short of safety. Knife edge, analysed above.

**VERDICT.** Not an independent bug. Two of three are symptoms of the same
forecast optimism; the third is a rounding-scale shortfall. No change made.

**One structural gap recorded honestly:** the 32 rows are users who accept
`partial_payment`/`installments` but not `full_payment`, and my
spending-change search only ever attempts a full lump sum, so it never runs
for them. The spec's `affordable_with_plan` does allow changes to combine with
other methods. Supporting evidence for the current restriction: **all 3
ground-truth spending-change rows are `full_payment`**, so the samples give no
example of a change paired with installments or partial payment. Documented in
`core/spending.py` as a known simplification rather than presented as complete.

## Explanation register audit

All 25 ground-truth explanations were grouped by decision shape and compared
with 10 random rows of mine. Findings: ground truth says **"today"** when the
payment is on the request date (mine said "on 3 August 2025"); the `wait`
shape gives the *reason* ("Paying earlier would take the balance below the
EUR 800 minimum"); spending changes name the item in plain words ("Stop the
family streaming plan"), never an event id; and `not_recommended` has two
forms — "Do not make this payment by <deadline>. None of the available options
keeps the <minimum> minimum protected." and, when the request allows partial
payment and the user accepts it, "Do not proceed with the <amount> request.
Although <safe> is available today, the full amount cannot be completed safely
within 90 days." (matches `request_14`/`24`; `request_10` is the one
exception). The deterministic template was rewritten shape-for-shape to that
register. No row's explanation is pasteable onto another row unchanged: every
sentence carries that row's amounts, dates and the user's own minimum.

---

# Fourth round: Part A/B/C/D discipline pass (13h-to-deadline check-in)

Classification-before-fixing, as instructed. Every remaining categorical
mismatch on the 25 samples was traced end to end BEFORE any code changed.

## Part A — root-causing every remaining categorical miss

9 unique rows carry a categorical mismatch (status, method, plan, or earliest
date): `request_06, 07, 11, 13, 17, 18, 19, 21, 22`. Each traced individually:
compared its projected minimum balance against ground truth's implied minimum
(`amount_safe_to_pay + minimum_balance_to_keep`), and checked whether
full-weight rotating-vendor pooling (the already-rejected D4/D13 mechanism)
closes the gap exactly.

**Classification: 1 SPEC BUG (shipped), 0 EVIDENCE GAP, 8 REFERENCE DIFFERENCE.**

| Row | Class | Evidence |
|---|---|---|
| request_11 | **SPEC BUG** | See below — fixed and shipped |
| request_06, 17, 18, 19 | REFERENCE DIFFERENCE | Full-weight groc+transport pooling closes the gap to within rounding *exactly* |
| request_07, 13, 21 | REFERENCE DIFFERENCE | Same mechanism, wider surface: these three carry heavy **dining**-category vendor rotation (6-7 distinct one-to-three-occurrence "dining" vendor descriptions each) that D13's groc+transport-only test never covered. Confirms the mechanism generalizes past the two categories originally tested, not a new cause. |
| request_22 | REFERENCE DIFFERENCE | Balance gap is 2.73 on ~975 (0.28%) — tiny — but the date is a full month off. This is the expected shape at a monthly-cadence boundary: a near-zero balance difference can flip which side of a step function the search lands on. Not a separate bug. |

Two messages looked like candidates for EVIDENCE GAP and were checked and
ruled out: `request_18`'s "transfer between your two accounts" note (no
`related_event_id`, confirmatory in tone, and pooling already closes that row
exactly) and `request_22`'s "portfolio value has increased, no units sold"
note (already-correct behavior — unrealized valuations are already excluded
from cash flow by construction). Neither describes an action the pipeline is
missing.

**8 of 9 landing in REFERENCE DIFFERENCE closes the accuracy question
honestly, per the stop rule** — most of the residual is the already-measured,
already-rejected-as-shippable rotating-vendor mechanism (D4/D12/D13/D16), now
confirmed to also explain three rows via dining specifically.

### The one SPEC BUG: commission fanned out across a category-wide amendment

`request_11`'s message states a confirmed base salary figure. The extracted
amendment (`category=salary, scope=ongoing, new_amount=38,760,000`) was
applied by `_adjusted_series_amount`, which matches purely on
`(category, direction)` — so it fanned out across **three** separate
`salary`-category credit series: the real "Base salary" series, and two
separate commission series ("Performance commission", "Monthly sales
commission") that happened to share the category. Each got overwritten to the
same confirmed-base figure, so the same month showed **three** 38.76M credits
instead of one — tripling projected income for that user.

Root cause traced one level deeper: those two commission series should never
have been treated as confirmed recurring income at all. Every one of their
historical occurrences is a *different* amount (16.7M, 8.9M, 16.0M, ...) —
the opposite of a real employer's fixed payroll. Sized dataset-wide before
touching any code (per the classification discipline: only ship a rule that
generalizes): computed the coefficient of variation of amounts for every
credit series in the dataset with 2+ occurrences. The separation is total —
23 series with an exact repeated amount among their occurrences (a stable
salary, or one clean step-change/raise/cut) all have amount-CoV effectively 0
or a clean one-time step; 96 series where every single occurrence differs
(the commission/freelance/gig-payout population) all have CoV >= 0.297. No
overlap, no middle ground requiring a threshold judgment call.

**Fix:** a credit series is only treated as confirmed recurring income if at
least one of its historical amounts repeats exactly. Verified this doesn't
regress the many legitimate "clean pay cut" users already in the dataset
(their repeated pre-cut amount trivially satisfies the rule): 23/23 in that
population correctly survive, 96/96 genuinely variable series are correctly
excluded, zero misclassifications either direction.

**Sized on the real 250 before shipping:** 14 of 250 real requests hit this
exact duplication pattern (a salary amendment landing on a user with 2+ raw
same-category credit series). Not a sample artifact.

**Latent gap recorded, not fixed:** two users (`user_230`, `user_238`) have
TWO *legitimately stable* income series sharing a category ("Primary
household salary" + "Second household income" — a two-earner household).
The underlying architectural issue (an amendment still matches by category
alone, not by which specific series it describes) remains for that
configuration. Checked whether it's ever exercised: neither user has a salary
amendment message in the real dataset, so it's a latent risk, not an active
bug. Documented in `core/state.py` rather than fixed under time pressure for
a configuration that doesn't currently fire.

Regenerated and re-validated after this fix: 250/250 rows, all checks pass,
110 unit tests (1 new, pinning the CoV-population separation). Sample score
unaffected (request_11's binding constraint sits earlier in the window than
where the fix operates) — expected, and reported as such rather than implied
otherwise; the fix is justified by the 14-request dataset-wide count, not by
sample movement.

## Part B — investigating, then reverting, a second candidate SPEC BUG

Instrumenting the 198/32/20 split (below) required re-simulating each
recommended "no-change" plan independently to measure its safety margin. That
re-simulation surfaced a second apparent inconsistency: **63 of 250** chosen
`wait`/`partial_payment` plans, when materialized as a real payment and
re-run through `daily_balances`/`is_safe`, came back as violating the minimum
balance — every single failure was a `wait` plan specifically.

**Root cause traced:** `core/forecast.py`'s debits-first same-day convention
(D2, validated and shipped) means a *materialized* debit always posts before
a same-day credit. But `earliest_date_for_full_payment`'s closed-form search
picks a candidate date by checking that date's *end-of-day* balance in the
baseline trace — which, for a day whose only activity is an incoming credit,
already includes that credit. So the search can select a day whose safety
depends on income that, once the recommended payment is actually made that
day, would not yet have posted (debits-first) — a real internal
inconsistency, not a diagnostic artifact.

**Built and tested a fix:** a new `daily_pre_credit_balances` per-day trace
(balance after existing debits, before existing credits — the correct
reference point for "would a new debit landing here be safe"), used only for
the payment day itself in both closed-form functions. Re-verified the
inconsistency this fixed: sanity failures dropped from 63/250 to **0/250**.

**Then checked it against the samples before shipping, per the classification
discipline — and it failed.** `sample_requests.csv`'s `request_04` has a
ground-truth `wait` date of **the same calendar day its salary lands**
(2024-06-15), not the day after. The fix moved this exact row's answer to
2024-06-16 and broke it. Full sample re-score confirmed the damage was not
isolated to that one row: total field matches fell from 109 to 92 and
`earliest_date_for_full_payment` from 18/25 to 11/25.

**Why the reference is right and the "fix" was wrong, on reflection:** D2's
debits-first convention is the correct conservative default for two
*unrelated* pre-existing events that happen to share a date — there is no
intentional ordering between them, so assume the worse one. But
`earliest_date_for_full_payment` is not observing unrelated events; it is
choosing *when the user should make a deliberate payment*, and "wait until
payday, then pay" is inherently sequenced the other way around — the whole
point of the recommendation is that the day's income funds that day's
payment. Same-day ordering is not one universal fact about a date; it depends
on whether the two flows are causally related or coincidental, and the
reference evidently encodes exactly that distinction while my fix erased it.

**Reverted in full**, including the new `daily_pre_credit_balances` function
(removed entirely — an unused, dead branch of reasoning has no place in the
zip per the Part C review pass) and every test built around it. Re-verified
afterward: 109/150 exact, 122/150 proximity, 110 tests passing (0 tests from
this sub-branch remain — reverting a wrong hypothesis should leave no trace
beyond this record of why it was tried).

**Why this is reported as a rejection, not hidden as a false start:** the
underlying inconsistency it was chasing was real (63/250 mismatches between
two internally-consistent pieces of the same codebase) — but "internally
consistent with itself" and "consistent with the reference" are different
properties, and only the sample data can adjudicate between competing
internally-consistent conventions. This is the same discipline as D3's buffer
sweep and D13's pooling test: a plausible, carefully-reasoned fix that the
evidence overturns is exactly what pre-registration and full-diff checking
are for.

## Part B continued — the 198/32/20 split, re-verified after the revert

Re-ran the instrumentation (unaffected by the affordability revert, which
only touched date/amount computation, not the gating logic itself):

| outcome | rows (of 250) |
|---|---|
| gate not reached — a no-change plan was already safe | 198 |
| gate not reached — user does not accept `full_payment` | 32 |
| enumeration reached | 20 |
| -> eligible actions found but they free too little | 18 |
| -> winner produced | 2 |
| -> zero eligible actions (all vetoed) | 0 |

**Margin analysis on the (then-)202** (how much headroom the chosen no-change plan
has beyond the minimum, as % of `requested_amount`, using the corrected
affordability functions): of 198 no-change candidates found or generatable,
191 independently re-verify as genuinely safe (0 sanity failures, confirming
the revert). Margins: median **28.5%** of requested_amount; only 2/191 rows
are "barely passing" (<1% margin); 42/191 have >=50% margin (comfortably,
unambiguously safe). This is **explanation (1) from the instructions**: most
of that population are not marginal — the unchanged plan has real headroom, so no
spending change is needed, and that is correct behavior. It is a downstream
symptom of the forecast's own ~4.5% optimism (some rows that should show a
small margin instead show a larger one), not evidence of an overly strict
gate. The three sample rows needing a change (`06`, `11`, `21`) all confirm
this same reading: two never reach the gate because the safe amount lands
exactly on the requested amount (no margin to spare, but no margin *needed*
either, per the pipeline's own arithmetic), and the third (`request_06`)
reaches the gate, finds the textually-correct action, and misses by €3.39 —
a margin-scale gap, not a logic gap.

**No gate-tightness bug found.** Re-read `problem_statement.md`'s language on
whether spending changes are ever *required* rather than optional: the spec
defines `affordable_with_plan` as completing the request via a schedule OR
installments OR permitted spending changes — a disjunction, not a mandate to
prefer changes when an unchanged plan is already safe. Combined with tie-break
criterion #2 ("no spending changes" is preferred whenever available), a
no-change-needed reading is not just permitted but the specified preference.
No evidence the reference emits a change on a row that would also be safe
without one — the three sample rows that need a change all fail without one
first.

## Part C — code.zip review pass (reviewer's-eye read)

Read the packaged zip fresh, not the source tree, since that's what a grader
opens. Findings:

- **Entry point discoverable in <10s**: `code/main.py` at the top of `code/`,
  `code/README.md`'s first section names the deterministic/agentic boundary
  and the module table before any setup instructions.
- **Boundary visible from file layout alone**: `code/core/` (no model calls —
  verified nothing in that directory imports `agent`) vs `code/agent/` (the
  only directory that imports the LLM client). A reviewer does not need the
  README to see the split, only to have it explained.
- **No dead code found from this session's rejected hypotheses.** Checked
  specifically for residue from D3 (buffer), D4/D13 (pooling), D14 (loosened
  filters), and this round's reverted pre-credit branch — all were either
  never committed to `code/` (tested via scratch scripts / monkeypatching
  outside the package) or, in the pre-credit case, fully removed including
  its tests. No orphaned functions, no commented-out blocks, no
  `_v2`/`_old`/`_backup` files.
- **No `helper.py`/`utils.py`/`misc.py`** anywhere in `code/`; every module
  name (`affordability.py`, `spending.py`, `ranking.py`, `evidence.py`, ...)
  describes its single responsibility.
- **README commands re-verified to actually run**, not just read: `pytest
  tests/ -q`, `python3 code/main.py --limit 5`, `python3
  evaluation/validate_output.py`, `python3 evaluation/score_samples.py
  --verbose`, `python3 evaluation/score_dual.py` — all executed fresh this
  session, not assumed from memory.
- **All cited numbers re-verified against artifacts current as of THIS
  round** (after both the shipped fix and the revert), not carried over from
  an earlier round: 250/250 rows, 110 tests, 109/150 exact, 122/150
  proximity, 35 model calls / ~$0.0237 in `evaluation/usage_report.md`.

## Part D — transcript sanity (log.txt)

Skimmed `log.txt` end to end as a grader would. Present and in the right
shape: planning before building (the initial module-layout proposal, stated
before any file was written), stated constraints (the deadline gate decision,
justified from `problem_statement.md` before being tested against samples),
real debugging (the quota misdiagnosis, the UTF-16 `.env` issue, the batch
truncation issue), and changes rejected with reasons (D3/D4/D5/D6/D8-D10/
D12-D14, plus this round's pre-credit revert, all logged with what was tried
and why it didn't ship). No secret-shaped content found (grepped for `AIza`,
`sk-`, `ghp_` patterns — none present; the same scan `package_submission.py`
runs before allowing a zip to be built). Nothing critical missing.

## Stop

Part A's classification is complete (1 SPEC BUG shipped, 8 REFERENCE
DIFFERENCE, 0 EVIDENCE GAP). Part B's gate is confirmed correctly reachable
and correctly gated — no fix needed there. Part C found no cleanup required
beyond what this round already removed. Part D found the transcript already
in the right shape. No fifth global hypothesis family opened. All three
artifacts rebuilt and re-validated after the one accepted change.

---

# Fifth round: the graduated pooling sweep, run to completion (rejected)

## D18 — Pareto-safe pooling weight on {groceries, dining, transport} *(rejected at every weight)*

**CLAIM.** Part A found that `request_07/13/21`'s residual gap is explained by
the *same* rotating-vendor mechanism as D13/D16, just via **dining** rather
than groceries/transport alone. The earlier D16 sweep only pooled
groceries+transport; re-running it with dining included, using the same
pre-registered rule (ship the largest `w` where decisions flipped = 0 **and**
relative-error score improves over `w=0`), closes the loop this session left
open.

**METRIC.** Field matches (exact), proximity score, median signed gap,
decisions flipped vs `w=0` — identical methodology to D13/D16
(`evaluation/score_dual.py --sweep`, weight applied to the mean of each
newly-pooled category's occurrences).

**BASELINE.** `w=0`: EXACT 109.0/150, PROX 122.0/150, amount proximity
17.00/25, median gap +4.47%.

**RESULT.**

| w | EXACT | PROX | amount proximity | median signed gap | optimistic rows | decision flips |
|---|---|---|---|---|---|---|
| 0.2 | 101.0 | 114.9 | 16.90 (↓) | +2.98% | 15/21 | **2** |
| 0.4 | 103.0 | 117.1 | 17.08 (↑, barely) | +0.59% | 13/21 | **2** |
| 0.6 | 78.0 | 92.8 | 14.76 (↓) | −1.87% | 6/21 | **9** |
| 0.8 | 67.0 | 77.0 | 9.97 (↓) | −4.70% | 3/21 | **12** |

Every weight fails the pre-registered rule. `w=0.2` and `w=0.4` land 2
decision flips each — even the least-damaging point in the sweep is already
over the flips=0 bar the rule requires — and `w=0.4`'s proximity gain (17.08
vs 17.00) is marginal even before the flips disqualify it. `w=0.6` and above
collapse outright (EXACT falls below even D16's full-pooling endpoint at
these smaller weights, because adding dining to the pooled set makes the
mean-based projection overshoot faster, not slower).

**Comparison to D16 (groceries+transport only, no dining):** that sweep had
flips of 1/1/4/8/10 at the same five weights — already a rejection, but with
a narrow near-miss at `w=0.2-0.4`. Adding dining removes even that near-miss:
flips jump to 2 at the smallest weight tested. This is not a weaker
rejection than D16 — it is confirmation that widening the pooled category set
to match Part A's own finding makes the family *more* fragile, not less.

**VERDICT.** **Rejected at all four weights, no exceptions taken** (no
intermediate values, no category-specific weights, no other variant run,
per the pre-registered stop condition). Combined with D16's full-pooling
endpoint (`w=1.0`, rejected) and this round's `w=0.2-0.8` (rejected), the
family is now rejected across its entire range on the categories Part A
identified. **The two endpoints (w=0, exact and correct on decisions; w=1,
correct on balance) bracket the truth with no safe interior point.** That is
the final, complete word on this limitation — recorded here and in
`INTERVIEW.md` §5.

---

# Sixth round: reproducibility and full-diff sanity, before freeze

## Check 1 — fresh-environment reproducibility

Unzipped `code.zip` (the actual submission artifact, not the working
directory) into a clean scratch directory, created a fresh venv, ran
`pip install -r requirements.txt` then `python -m pytest -q` with no other
setup. **110/110 passed** — matches README/INTERVIEW/REHEARSAL exactly. No
missing dependency pin, no working-directory leakage, no path assumption that
only holds locally. Confirmed `tests/` has zero references to `DATASET_DIR`
or `dataset/` (grepped), so the suite needs nothing beyond what ships in the
zip. Clean.

## Check 2 — full diff of current output.csv against pre-commission-fix behavior

Reconstructed the pre-fix pipeline exactly (no saved pre-fix `output.csv`
existed to diff against, since the working tree isn't committed) by
monkeypatching `detect_recurring_series` back to its pre-fix form — an exact
copy with only the exact-repeated-amount credit filter deleted — and
regenerating all 250 rows under it. Diffed every field of every row against
the currently-shipped `output.csv`.

**Result: 10 of 250 rows changed. All 10 are explained by the shipped fix —
zero unexplained.** But the true mechanism is broader than the "14 affected
requests" figure reported when the fix shipped: that count only tracked one
trigger path (an amendment landing on a category with 2+ raw credit series).
The code itself has no such precondition — `detect_recurring_series` applies
the exact-repeat filter unconditionally to every user's raw settled history,
amendment or not. Checked all 8 rows that weren't in the original 14-request
list and found every one is the *direct* channel: a user whose entire
`salary`-category history is gig/freelance/seasonal income with no repeated
amount anywhere (`request_29`: "Peak-season wages" / "Seasonal contract
payment", zero repeats; `request_94`: six different contract/project/
consulting descriptions, zero repeats across all of them; `request_126`,
`request_133`, `request_166`, `request_251`: same shape). `request_270` is a
live instance of the two-earner-household duplication risk flagged as latent
in Part A ("Primary household salary" + "Second household income," both
raw series present) — turns out it does occur in the real 250, and the
general rule (not an amendment-specific patch) handles it correctly without
needing the targeted fix that was deferred.

So the "14" and "10" are both correct, for different questions: 14 requests
have the raw *structural* pattern an amendment could exploit; 10 requests are
where the fix actually changes the final decision (via either the amendment
path or the direct gig-income-exclusion path). Recorded here rather than
left as a discrepancy between two true numbers measuring different things.

**No anomaly found; the forecast question is not reopened.** Both checks pass
cleanly with no code change required.

---

# Seventh round: FX segmentation (final forecast diagnostic) + synthetic robustness pass

## D19 — FX/currency involvement as a segmentation axis *(rejected immediately — no distribution to split)*

**CLAIM.** Category, observation-count, and burn-rate have all been tested as
segmentation axes for the residual. Currency/FX involvement had not.

**METRIC.** Real per-row relative error on `amount_safe_to_pay` for all 25
samples (not an assumed distribution), split into FX-involved vs
single-currency groups two ways: "ever" (any event of the user's, any
status, in a currency other than `home_currency`) and "in-window" (a
scheduled/pending event inside the 90-day forecast window in a foreign
currency). Pre-registered rule: only act on a clean split (FX rows cluster
meaningfully higher); reject immediately on heavy overlap, no further
segmentation.

**Raw per-row relative errors, all 25 (4 capped/unmeasurable excluded from
the split):** 0.0, 5.8, 20.3, 51.0, 850.6, 0.9, 16.9, 52.0, 0.0, 1460.0, 4.8,
0.0, 117.3, 8.1, 139.2, 0.0, 12.6, 43.3, 34.6, 178.9, 2.0, 0.6, 5.1, 23.4,
18.2 (%).

**RESULT.** Only **1 of 25** sample users has any foreign-currency event at
all (`request_25`), under either definition — consistent with the
dataset-wide finding from the very first build phase that only 140/25,342
events (0.55%) are foreign-currency, concentrated in a small user subset that
this 25-row sample mostly missed by chance. With n=1 in the FX group there is
no distribution to compare against, and that single point (18.2% error) sits
unremarkably inside the non-FX group's own range (0.6%-1460%, median 21.8%)
— it is not an outlier by any measure.

**VERDICT.** **Rejected immediately, no further segmentation attempted**, per
the pre-registered rule. FX/currency conversion is not a contributing axis to
the residual on this sample; there isn't enough FX-involved data in the
samples to say more than that. This is the ninth and final forecast-adjacent
round (D1-D19 plus the Part-B build-and-revert): category, observation-count,
burn-rate, uniform/targeted buffers, protected-category gating, looser
regularity, same-day debit/credit conventions (twice, on two category sets),
and now currency segmentation have all been tested. No further forecast
investigation follows this round.

## Synthetic edge-case robustness pass *(different in kind — no forecast parameter touched)*

Not accuracy tuning: this stress-tests the already-shipped, already-frozen
pipeline against inputs the 25 samples may not exercise, checking invariants
(schema conformance, bounds, no crash) rather than matching a ground truth
that doesn't exist for synthetic cases. `tests/test_robustness.py`, 11 tests:

1. **Three-plus-way same-date collision.** Two debits and two credits on one
   date. Confirms the D2 debits-first convention generalizes to N colliding
   events, not just the two-event case it was originally verified against —
   all of a day's debits sum and post together (the intraday low), then all
   of that day's credits sum and post (end of day).
2. **Income terminating (`new_amount: 0`) exactly on `request_date`.**
   Confirms the amendment boundary is inclusive: an occurrence landing
   exactly on `effective_date` already sees the zero, not just occurrences
   strictly after it.
3. **A two-hop FX chain** (`USD->EUR` and `EUR->ZAR` both present,
   `USD->ZAR` absent). Confirms the converter does not chain through the
   intermediate currency on its own initiative — it raises
   `MissingExchangeRateError` rather than silently computing a rate nobody
   supplied. A companion test confirms a genuine direct rate is still used
   correctly even when an unrelated same-date chain also exists.
4. **Installments crossing a leap day and a month-end.** `2028-01-31` (a
   leap year) plus an explicit 30-day frequency, twice; a direct leap-day
   crossing (`2028-02-28` + 1 day); and the equivalent non-leap-year case.
   Confirms `payment_frequency_days` is applied as exact day-count
   arithmetic (Python's `date` library is exact here by construction) and is
   deliberately NOT subject to the calendar-month-stepping fix, which only
   applies to *inferred* recurring cadences, not this dataset's *explicit*
   frequency field.
5. **Every payment method unsafe.** Two scenarios (no methods accepted at
   all; all methods accepted but balance/deadline make every one
   impossible). Confirms the row still passes independent schema validation,
   `recommended_payment_method` is `not_recommended`, and the explanation is
   genuinely row-specific — cites the user's own minimum balance and
   deadline, and demonstrably changes when those figures change, rather than
   reading as a static fallback string.

**Two test-writing bugs found and fixed while building these; zero pipeline
bugs found.** The first synthetic collision test initially expected a single
netted balance point, forgetting the day had both debits and credits and so
should show the intraday-low/end-of-day split by the shipped D2 convention —
fixed the test's expectation, not the code, after confirming the two-point
output directly. The not-recommended explanation test initially asserted the
explanation must contain `requested_amount`, which the actual (and, checked
against ground truth's own samples, correct) not-recommended register never
cites in that branch — fixed the assertion to check the figures the register
actually does cite (minimum balance, deadline) instead. Both are recorded
because "the test was wrong, not the code" is exactly the kind of claim this
project holds to the same evidentiary standard as everything else — verified
via the same `Decimal` arithmetic and a second, independently-run check, not
asserted.

**Verdict: no SPEC BUG found; all 11 tests added as permanent regression
coverage.** 121 total unit tests, no regression on the 25 samples (confirmed
by re-running the dual scorer — unchanged at 109/150 exact, 122/150
proximity, since no production file was touched this round).

## Independent temporal-holdout study, and three rejected category-forecast mechanisms

**Not a 25-row tuning round.** This investigation was built and run entirely
against `dataset/financial_events.csv`'s real historical transactions, with
zero use of `sample_requests.csv` for method selection. The public samples
were used only afterward, to check whether a method validated independently
also holds up in the actual decision pipeline.

**Methodology.** For every (user, rotating-essential category) pair with
≥12 real settled transactions spanning ≥150 days, walked cutoff dates
forward every 30 days. At each cutoff, trained candidate methods on data
strictly before it, then scored their 90-day-forward prediction against that
user's real, actual subsequent spend in that category — data the production
system never sees in advance, but which exists in the dataset. **652
independent samples** across 3 categories.

| Method | median rel. err | mean rel. err | optimistic | conservative | predicts $0 despite real spend |
|---|---|---|---|---|---|
| Current production (per-vendor recurrence) | **100.0%** | 85.5% | 93.4% | 6.6% | **76.1%** |
| Category median weekly rate (all quantiles 0.25-0.40 clustered here) | 19.3-21.6% | 35-41% | 71.8-79.0% | 21.0-28.2% | 0.0% |
| Category median, restricted to the 496 true-omission holdout samples only | 32.8% | 40.3% | 25.0% | 75.0% | n/a (by construction) |

This is genuine, independent evidence that the current per-vendor recurrence
mechanism has a real generalization weakness on rotating-essential
categories. It is **not**, on its own, evidence that any specific fix is
safe to ship — see below.

**Candidate 1 — vendor-share suppression.** Suppress a detected series when
its category has ≥3 vendor descriptions and the matched description's
historical share is below a threshold. Tested at 0.3/0.4/0.5: **110→102
exact, 123.048→113.5 proximity, earliest-date 19→13, 2 unsafe categorical
flips** (`request_04`, `request_06`, both toward `affordable_now`/
`full_payment` when that wasn't safe). Rejected.

**Candidate 2 — blanket category-median replacement.** Replace *every*
rotating-essential category's forecast with the category median, regardless
of whether a per-vendor series already existed. Sized first: of 594
qualifying (user, category) pairs across the real 250 rows, 178 (30%)
already had a detected series that this blanket version would overwrite.
Result: **110→79 exact, 123.048→92.3 proximity, every single categorical
field regressed** (status 21→14, method 23→15, plan 21→15, earliest 19→14,
spending 22→19). Rejected and reverted in full.

**Candidate 3 — surgical fill-only (never overwrite).** Only add the
category baseline where per-vendor detection found literally nothing for
that category; any category with an existing detection, however imperfect,
is left untouched. Validated specifically on the 496-sample true-omission
subset first (32.8% median error, 75% conservative-biased — a real
improvement over predicting zero). Result when run through the full
pipeline: **still 110→87 exact, 123.048→100.9 proximity, every categorical
field still regressed**, and critically, **two of the four previously-exact
amount rows broke** (`request_01`: 25,256→13,721.88; `request_09`:
166.61→126). Rejected and reverted in full.

**Why all three fail the same way.** Each candidate improved measurably on
an *isolated* category-spend-forecast metric, and each still made the full
decision pipeline worse — including breaking rows the current system already
gets exactly right. The most likely explanation: `minimum_balance_to_keep`
in this dataset's design already functions as the buffer that absorbs
day-to-day variable essential spending (groceries/dining/transport), so
projecting that same spending a second time as an explicit forecast line
item double-counts the safety margin rather than filling a real gap. This
is a hypothesis, not a proven fact about the reference generator — but it is
consistent with all three independent failures, including one (Candidate 3)
specifically designed to touch nothing that wasn't already a hard omission.

**Candidate 4 — CoV-gated fill (one more attempt, made after directly
tracing Candidate 3's two counterexamples).** Rather than stopping at
"the mechanism fights the reference design," `request_01` and `request_09`
were traced to their exact cause: `request_01`'s failure came entirely from
a **dining** fill built on one volatile "Family dinner" outlier (weekly-spend
coefficient of variation 0.97); `request_09`'s came from a **groceries**
fill on a thin safety margin (CoV 0.65) — groceries being the
best-behaved category in the original holdout. Computing CoV across all 594
qualifying (user, category) pairs in the real 250-row dataset showed a clear
gap in the distribution (p25 = 0.16, median = 0.64); both failures sit well
above it. Candidate 4 kept Candidate 3's fill-only-never-overwrite scope and
added one data-derived gate: only fire when CoV < 0.25 (inside that gap,
admitting the genuinely stable minority — 157 of 594 pairs — and excluding
both known failures without naming them directly).

Result: **zero categorical regressions** — all five categorical fields on
the 25 samples came back exactly unchanged, the first of the four candidates
to achieve this. Safety held (201/201 plan replay, unchanged). But amount
proximity still fell **17.048→16.65** and median relative error still rose
**12.6%→16.4%** on the rows with known ground truth, and 87 of the real 250
rows changed with no ground truth available to verify 225 of them. Rejected
on amount-accuracy regression and unverifiable blast radius, not on safety
or categorical grounds — the closest any candidate came, and precisely
because of that, the most informative result: it rules out double-counting
against `minimum_balance_to_keep` as the dominant failure mode (categorical
decisions held even where the mechanism fired) and narrows the real cause to
a more fundamental one — a historical median, however stability-gated, is
still a point estimate for what is genuinely a stochastic future quantity,
and using one costs more in accuracy than it recovers, even when scoped as
tightly as the evidence allows.

**Verdict:** four structurally different mechanisms, one independent
652-sample validation study, zero survivors — the last survived every gate
except amount accuracy and verified blast radius. No further variant of
"add rotating-essential category spend to the forecast" was attempted after
Candidate 4: it is the version this line of investigation was always
converging toward, and it still wasn't enough. No tested forecasting
modification produced a safe end-to-end improvement under the current
decision architecture. Freeze holds. `output.csv` unaffected throughout (all
four candidates were reverted before any regeneration); 142/142 tests pass.
