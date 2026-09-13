# Buy or Wait? — evaluator briefing

The detailed final findings, ratings, gap table and adversarial questions are
in [evaluation/FINAL_AUDIT.md](evaluation/FINAL_AUDIT.md). The experiment history
is preserved in [evaluation/EXPERIMENTS.md](evaluation/EXPERIMENTS.md).

## What the implementation demonstrates

Financial decisions combine structured cash events with image-only amounts
and message amendments. The deterministic core reconstructs recurring cash
flows, projects a fixed 90-day window, computes capacity, builds eligible
payment plans and ranks them. Gemini extracts facts that are not already
structured. The submitted explanations are deterministic templates; model
rewriting is optional and not used in the delivered output.

This separation improves auditability, but does not make extracted facts or
future spending certain. An LLM can influence financial inputs. Its facts must
pass schema, source, magnitude, currency and confidence checks as applicable.
The core does not import the agent package; neutral adjustments cross the boundary.

## Verified current results

| Measure | Result |
|---|---|
| Evaluation output | 250/250 rows, independent structural validation passes |
| Fresh offline replay | All requests recomputed; 249 unchanged from prior output, request_267 repaired |
| Regression suite | 142 passing tests; no network required |
| Arithmetic plan replay | 201 recommendations, zero breaches under the documented timing/forecast |
| Public development sample score | 110/150 exact; 123.048/150 under our proximity metric |
| Amount accuracy | 4/25 exact; proximity sum 17.048/25; median relative error 12.6% |
| Other exact fields | status 21/25, method 23/25, plan 21/25, earliest 19/25, spending 22/25 |
| Recorded usage | 35 historical calls, 80,940 tokens; ~USD 0.0237 at configured estimates |
| Final audit model usage | Zero new calls; cached model-derived evidence reused |

The samples are public development examples, not a held-out test. The cost is
a tracked historical floor: five earlier vision calls predate the ledger.
No hidden-test score, competitor score or leaderboard position is known.

## The strongest implementation story

The final audit challenged a previously accepted search limitation. For
request_267, full payment is unacceptable to the user, but a supplied three-leg
installment schedule becomes safe after stopping one permitted streaming
subscription. Before the fix, that combination was never tested. After the fix,
the projected minimum is EUR 1,652.16 against a EUR 1,500 reserve; without the
change it is EUR 1,496.16. The full request completes by the deadline.

The change reuses the existing bounded spending search and ranking. It changes
one evaluation row, no capacity fields, and no sample scores. It is justified
by the supplied option, profile and forecast, not by a hardcoded request ID.

A second story matters just as much: an independent 652-sample temporal
holdout, built entirely from real historical transactions with zero use of
the 25 public rows, proved the current recurrence mechanism has a real
generalization weakness (100% median category-forecast error, 76% of the
time predicting zero spend where real spend existed). Three separately
designed fixes for that weakness were each built, validated on that same
independent evidence, and then rejected after full end-to-end testing showed
each one made actual decisions worse — including two that broke amount rows
the system was already getting exactly right. Nothing shipped from that
line of work except the finding itself. That is the discipline this
submission is asking to be judged on: not "the forecast is imperfect," which
is disclosed everywhere, but "every proposed fix for it was tested against
real decisions before being trusted, and rejected when the evidence said so."
Full methodology and per-candidate numbers: `evaluation/FINAL_AUDIT.md` and
`evaluation/EXPERIMENTS.md`.

## Earlier discoveries worth defending

- Salary amendments could fan out across unrelated variable commission series.
  The prior experiment record documents the dataset-level diagnosis and fix.
- Zero-valued amendments need directional reasoning: stopping income reduces
  apparent capacity, while removing an expense raises it.
- Calendar-month recurrence must not drift by adding a flat 30 days.
- Explicit scheduled salaries can collide with projections and double-count
  income; collision handling is separate from linked-event lifecycle handling.
- A blanket pre-credit payment experiment fixed an internal replay discrepancy
  but contradicted a public payday example. It was reverted. Current wait and
  partial-payment semantics permit paying after that day's income; installment
  simulation remains debit-first. This convention is explicit, not a universal
  claim about real banking settlement.

See the experiment log for dated measurements. Historical test and output
counts there describe those rounds; the table above is current.

## Limits a judge can legitimately challenge

The forecast omits much variable essential spending when vendor descriptions
rotate. The tested pooling variants did not earn a place, but that does not
prove the data cannot support a better method. Five of 21 uncapped sample
amount comparisons are conservative; not every error is optimism. The audit
also found a grocery series in request_04, contrary to the earlier universal
omission claim. No single root cause has been proven for every discrepancy.

The spending search is greedy and capped at three actions, not exhaustive.
Source membership does not prove a message supports an extraction. Model
confidence is not calibrated. Cache and output checkpoints use IDs, so changes
to data/code require a fresh replay; `--no-llm` still consumes cached facts.
Blank-amount failures explicitly refuse, whereas missing message amendments
can leave an incomplete forecast. Optional model prose is not semantically
verified; it is not part of the delivered output.

These limits are reasons to avoid a 9.5/10 or guaranteed Top-3 claim. The
defensible claim is disciplined, reproducible engineering with material
forecast uncertainty openly quantified.

## Where to inspect

| Question | File |
|---|---|
| Setup and architecture | code/README.md |
| Current independent findings and judge questions | evaluation/FINAL_AUDIT.md |
| Reproduce the audit | evaluation/final_audit.py |
| Machine-readable audit evidence | evaluation/final_audit.json |
| Historical experiments, including rejections | evaluation/EXPERIMENTS.md |
| Sample scoring | evaluation/score_dual.py |
| Output structural validation | evaluation/validate_output.py |
| Cost floor and ledger limits | evaluation/usage_report.md |
| Full conversation trail, submitted separately | log.txt |
