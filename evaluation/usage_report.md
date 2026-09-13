# Token Usage And Cost Report

Covers the final full-dataset run that produced the submitted `output.csv` (one row per request in `dataset/requests.csv`).

Final revision: on 2026-09-13, `evaluation/final_audit.py` freshly recomputed
all 250 requests with cached evidence and deterministic explanations. The
validated replay was promoted to `output.csv` (MD5
`882b333a24692786deb8021644b8350f`). These audit invocations made **0 model calls,
0 input tokens, 0 output tokens, and USD 0 incremental model cost**. It changed
one decision through deterministic installment/spending search, then corrected
settlement-date recurrence and admitted three source-verified first salaries.
The timestamps
below identify the earlier ledger-producing invocation, not this later replay.
Historical totals, per-model figures and the missing-five-call caveat remain
unchanged; no unrecorded usage has been invented.

- Run started (UTC): `2026-09-12T23:26:56.842263+00:00`
- Run finished (UTC): `2026-09-12T23:27:01.139097+00:00`
- Provider: Google (Gemini API)
- Requests processed: **250**

## How this run is accounted

Evidence extraction (vision amounts, message amendments) is cached on disk, so the run that finally writes `output.csv` can legitimately make few or no calls while still depending on extractions paid for in earlier runs of the same pipeline. Reporting only the last invocation would understate the true cost of this output, so the figures below are **cumulative across every run captured by this usage ledger** (`evaluation/usage_raw.json`).

Caveat: `evaluation/evidence_cache.json` currently holds 7 vision-extracted amounts, but this ledger contains only 2 `vision_amount_extraction` call records — the other 5 were extracted in earlier development sessions before this cost-tracking ledger existed, so their original token/cost figures were never captured and cannot be reconstructed after the fact. The totals below are therefore a verified floor on the true historical cost, not an exact total.

| Scope | Calls | Input tokens | Output tokens | Cost (USD) |
|---|---|---|---|---|
| Final invocation (wrote output.csv) | 0 | 0 | 0 | $0.0000 |
| Earlier runs (cached evidence reused) | 35 | 76,400 | 4,540 | $0.0237 |
| **Cumulative (this output.csv)** | **35** | **76,400** | **4,540** | **$0.0237** |

## Totals

| Metric | Value |
|---|---|
| Model calls | 35 |
| Failed/retried calls | 7 |
| Input tokens | 76,400 |
| Output tokens | 4,540 |
| Total tokens | 80,940 |
| Estimated total cost (USD) | $0.0237 |

## Per request averages

| Metric | Value |
|---|---|
| Model calls / request | 0.140 |
| Total tokens / request | 323.8 |
| Estimated cost / request (USD) | $0.000095 |

## Per model

| Model | Calls | Input tokens | Output tokens | Cached | $/Mtok in | $/Mtok out | Cost (USD) | Rate source |
|---|---|---|---|---|---|---|---|---|
| `gemini-3-flash-preview` | 19 | 51,127 | 1,928 | 20,270 | 0.30 | 2.50 | $0.0202 | configured default estimate (not verified against the live price list) |
| `gemini-3.1-flash-lite` | 16 | 25,273 | 2,612 | 0 | 0.10 | 0.40 | $0.0036 | configured default estimate (not verified against the live price list) |

## Per call purpose

| Purpose | Calls | Input tokens | Output tokens |
|---|---|---|---|
| message_amendments | 21 | 20,905 | 1,356 |
| message_amendments_batched | 12 | 52,438 | 3,079 |
| vision_amount_extraction | 2 | 3,057 | 105 |

## Method

- Token counts are taken from each API response's own usage metadata (`usage_metadata.prompt_token_count` / `candidates_token_count`), accumulated per call as the run proceeds -- not estimated after the fact.
- Cost is derived as `input_tokens/1e6 * rate_in + output_tokens/1e6 * rate_out` using the rate table in `code/agent/usage.py`, overridable per run via the `GEMINI_PRICE_IN` / `GEMINI_PRICE_OUT` environment variables. Rates are a configured input, not a value returned by the API.
- No API keys, credentials, or configuration secrets are included in this report.

## Run notes

- Adopted 35 call record(s) from earlier runs of this pipeline whose cached evidence this output reuses; figures are cumulative.
- API call budget for this run: 0. The observed free tier grants 20 requests/day/model, so a budget lets the run finish deterministically with the evidence already cached instead of stalling on calls that cannot succeed.
- Evidence model: gemini-3.6-flash (vision amount extraction, message amendment extraction).
- Explanations written by the deterministic template (--llm-explanations off), so the API quota is reserved for the evidence calls that affect numbers.
- Client-side rate limit: 30 requests/minute.
- evidence cache: 215 user amendment record(s), 7 resolved amount(s) from D:\Orchestrate September\evaluation\evidence_cache.json
- API calls attempted by the evidence client in this invocation: 0 (model at finish: gemini-3.6-flash).
- Requests in this run: 250; rows written: 250.
