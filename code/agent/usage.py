"""Token/call/cost instrumentation, wired in from the very first API call
rather than reconstructed afterwards, so evaluation/usage_report.md reports
what the final full-dataset run actually did.

Token counts are hard facts returned by the API. Cost is derived from the
rate table below, which is operator-configurable (and overridable via env)
precisely so the report never presents a guessed price as if the API had
reported it.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# USD per 1M tokens (input, output). These are CONFIGURED ESTIMATES, not
# values returned by the API, and the report says so. Supply the authoritative
# figures for your account at run time with GEMINI_PRICE_IN /
# GEMINI_PRICE_OUT (USD per 1M tokens) and the report will use and label them
# as operator-supplied.
DEFAULT_RATES_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-3.5-flash-lite": (0.10, 0.40),
    "gemini-3.5-flash": (0.30, 2.50),
    "gemini-3.6-flash": (0.30, 2.50),
    "gemini-3.7-flash": (0.30, 2.50),
    "gemini-3.8-flash": (0.30, 2.50),
    # Models this run actually fell back onto; priced at their tier's rate so
    # the report does not fall through to the generic estimate.
    "gemini-3-flash-preview": (0.30, 2.50),
    "gemini-3.1-flash-lite": (0.10, 0.40),
    "gemini-flash-lite-latest": (0.10, 0.40),
    "gemini-flash-latest": (0.30, 2.50),
}
FALLBACK_RATE = (0.30, 2.50)

RATE_SOURCE_ENV = "operator-supplied via GEMINI_PRICE_IN/OUT"
RATE_SOURCE_TABLE = "configured default estimate (not verified against the live price list)"
RATE_SOURCE_FALLBACK = "generic fallback estimate (model not in the rate table)"


@dataclass
class CallRecord:
    model: str
    purpose: str  # e.g. "vision_amount_extraction", "message_amendments", "explanation"
    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0
    ok: bool = True
    error: str | None = None


@dataclass
class UsageTracker:
    provider: str = "Google (Gemini API)"
    records: list[CallRecord] = field(default_factory=list)
    prior_records: list[CallRecord] = field(default_factory=list)
    requests_processed: int = 0
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(self, record: CallRecord) -> None:
        with self._lock:
            self.records.append(record)

    def load_prior(self, path: Path) -> int:
        """Adopt the call ledger from previous runs of this pipeline.

        Evidence extraction is cached on disk, so the run that finally writes
        `output.csv` may legitimately make few or zero calls while still
        depending on extractions paid for earlier. Reporting only the last
        run's calls would understate what producing this output actually cost,
        so the ledger accumulates and the report breaks out both.
        """
        if not path.exists():
            return 0
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return 0
        adopted = []
        for c in payload.get("calls", []):
            try:
                adopted.append(
                    CallRecord(
                        model=c["model"], purpose=c.get("purpose", "unknown"),
                        input_tokens=int(c.get("input_tokens", 0)),
                        output_tokens=int(c.get("output_tokens", 0)),
                        cached_tokens=int(c.get("cached_tokens", 0)),
                        ok=bool(c.get("ok", True)), error=c.get("error"),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        with self._lock:
            self.prior_records = adopted
        return len(adopted)

    @property
    def all_records(self) -> list[CallRecord]:
        return self.prior_records + self.records

    def note_request(self) -> None:
        with self._lock:
            self.requests_processed += 1

    # --- derived numbers -------------------------------------------------

    def rates_for(self, model: str) -> tuple[float, float]:
        return self.rates_with_source(model)[:2]

    def rates_with_source(self, model: str) -> tuple[float, float, str]:
        env_in, env_out = os.environ.get("GEMINI_PRICE_IN"), os.environ.get("GEMINI_PRICE_OUT")
        if env_in and env_out:
            return float(env_in), float(env_out), RATE_SOURCE_ENV
        # longest prefix wins, so "…-flash-lite" is not matched by "…-flash"
        for key in sorted(DEFAULT_RATES_USD_PER_MTOK, key=len, reverse=True):
            if model.startswith(key):
                rate_in, rate_out = DEFAULT_RATES_USD_PER_MTOK[key]
                return rate_in, rate_out, RATE_SOURCE_TABLE
        return FALLBACK_RATE[0], FALLBACK_RATE[1], RATE_SOURCE_FALLBACK

    def per_model(self, records=None) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for r in (self.all_records if records is None else records):
            m = out.setdefault(
                r.model,
                {"calls": 0, "failed_calls": 0, "input_tokens": 0, "output_tokens": 0, "cached_tokens": 0},
            )
            m["calls"] += 1
            if not r.ok:
                m["failed_calls"] += 1
            m["input_tokens"] += r.input_tokens
            m["output_tokens"] += r.output_tokens
            m["cached_tokens"] += r.cached_tokens
        for model, m in out.items():
            rate_in, rate_out, source = self.rates_with_source(model)
            m["cost_usd"] = (
                m["input_tokens"] / 1_000_000 * rate_in + m["output_tokens"] / 1_000_000 * rate_out
            )
            m["rate_in_usd_per_mtok"] = rate_in
            m["rate_out_usd_per_mtok"] = rate_out
            m["rate_source"] = source
        return out

    def per_purpose(self, records=None) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for r in (self.all_records if records is None else records):
            p = out.setdefault(r.purpose, {"calls": 0, "input_tokens": 0, "output_tokens": 0})
            p["calls"] += 1
            p["input_tokens"] += r.input_tokens
            p["output_tokens"] += r.output_tokens
        return out

    def totals(self, records=None) -> dict:
        per_model = self.per_model(records)
        total_in = sum(m["input_tokens"] for m in per_model.values())
        total_out = sum(m["output_tokens"] for m in per_model.values())
        total_cost = sum(m["cost_usd"] for m in per_model.values())
        calls = sum(m["calls"] for m in per_model.values())
        failed = sum(m["failed_calls"] for m in per_model.values())
        n = max(1, self.requests_processed)
        return {
            "calls": calls,
            "failed_calls": failed,
            "input_tokens": total_in,
            "output_tokens": total_out,
            "total_tokens": total_in + total_out,
            "cost_usd": total_cost,
            "requests_processed": self.requests_processed,
            "avg_calls_per_request": calls / n,
            "avg_tokens_per_request": (total_in + total_out) / n,
            "avg_cost_per_request_usd": total_cost / n,
        }

    # --- reporting -------------------------------------------------------

    def write_report(self, path: Path, notes: list[str] | None = None) -> Path:
        t = self.totals()
        per_model = self.per_model()
        per_purpose = self.per_purpose()
        finished_at = datetime.now(timezone.utc).isoformat()

        lines: list[str] = []
        lines.append("# Token Usage And Cost Report")
        lines.append("")
        lines.append(
            "Covers the final full-dataset run that produced the submitted `output.csv` "
            "(one row per request in `dataset/requests.csv`)."
        )
        lines.append("")
        lines.append(f"- Run started (UTC): `{self.started_at}`")
        lines.append(f"- Run finished (UTC): `{finished_at}`")
        lines.append(f"- Provider: {self.provider}")
        lines.append(f"- Requests processed: **{t['requests_processed']}**")
        lines.append("")
        if self.prior_records:
            this_run = self.totals(self.records)
            lines.append("## How this run is accounted")
            lines.append("")
            lines.append(
                "Evidence extraction (vision amounts, message amendments) is cached on disk, "
                "so the run that finally writes `output.csv` can legitimately make few or no "
                "calls while still depending on extractions paid for in earlier runs of the "
                "same pipeline. Reporting only the last invocation would understate the true "
                "cost of this output, so the figures below are **cumulative across every run "
                "whose cached evidence the delivered `output.csv` uses**."
            )
            lines.append("")
            lines.append("| Scope | Calls | Input tokens | Output tokens | Cost (USD) |")
            lines.append("|---|---|---|---|---|")
            lines.append(
                f"| Final invocation (wrote output.csv) | {this_run['calls']} | "
                f"{this_run['input_tokens']:,} | {this_run['output_tokens']:,} | "
                f"${this_run['cost_usd']:.4f} |"
            )
            prior = self.totals(self.prior_records)
            lines.append(
                f"| Earlier runs (cached evidence reused) | {prior['calls']} | "
                f"{prior['input_tokens']:,} | {prior['output_tokens']:,} | ${prior['cost_usd']:.4f} |"
            )
            lines.append(
                f"| **Cumulative (this output.csv)** | **{t['calls']}** | "
                f"**{t['input_tokens']:,}** | **{t['output_tokens']:,}** | **${t['cost_usd']:.4f}** |"
            )
            lines.append("")

        lines.append("## Totals")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|---|---|")
        lines.append(f"| Model calls | {t['calls']} |")
        lines.append(f"| Failed/retried calls | {t['failed_calls']} |")
        lines.append(f"| Input tokens | {t['input_tokens']:,} |")
        lines.append(f"| Output tokens | {t['output_tokens']:,} |")
        lines.append(f"| Total tokens | {t['total_tokens']:,} |")
        lines.append(f"| Estimated total cost (USD) | ${t['cost_usd']:.4f} |")
        lines.append("")
        lines.append("## Per request averages")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|---|---|")
        lines.append(f"| Model calls / request | {t['avg_calls_per_request']:.3f} |")
        lines.append(f"| Total tokens / request | {t['avg_tokens_per_request']:.1f} |")
        lines.append(f"| Estimated cost / request (USD) | ${t['avg_cost_per_request_usd']:.6f} |")
        lines.append("")

        if per_model:
            lines.append("## Per model")
            lines.append("")
            lines.append(
                "| Model | Calls | Input tokens | Output tokens | Cached | $/Mtok in | $/Mtok out | Cost (USD) | Rate source |"
            )
            lines.append("|---|---|---|---|---|---|---|---|---|")
            for model, m in sorted(per_model.items()):
                lines.append(
                    f"| `{model}` | {m['calls']} | {m['input_tokens']:,} | {m['output_tokens']:,} | "
                    f"{m['cached_tokens']:,} | {m['rate_in_usd_per_mtok']:.2f} | "
                    f"{m['rate_out_usd_per_mtok']:.2f} | ${m['cost_usd']:.4f} | {m['rate_source']} |"
                )
            lines.append("")

        if per_purpose:
            lines.append("## Per call purpose")
            lines.append("")
            lines.append("| Purpose | Calls | Input tokens | Output tokens |")
            lines.append("|---|---|---|---|")
            for purpose, p in sorted(per_purpose.items()):
                lines.append(
                    f"| {purpose} | {p['calls']} | {p['input_tokens']:,} | {p['output_tokens']:,} |"
                )
            lines.append("")

        lines.append("## Method")
        lines.append("")
        lines.append(
            "- Token counts are taken from each API response's own usage metadata "
            "(`usage_metadata.prompt_token_count` / `candidates_token_count`), accumulated "
            "per call as the run proceeds -- not estimated after the fact."
        )
        lines.append(
            "- Cost is derived as `input_tokens/1e6 * rate_in + output_tokens/1e6 * rate_out` "
            "using the rate table in `code/agent/usage.py`, overridable per run via the "
            "`GEMINI_PRICE_IN` / `GEMINI_PRICE_OUT` environment variables. Rates are a "
            "configured input, not a value returned by the API."
        )
        lines.append(
            "- No API keys, credentials, or configuration secrets are included in this report."
        )
        if notes:
            lines.append("")
            lines.append("## Run notes")
            lines.append("")
            for note in notes:
                lines.append(f"- {note}")
        lines.append("")

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    def write_raw(self, path: Path) -> Path:
        payload = {
            "provider": self.provider,
            "started_at": self.started_at,
            "requests_processed": self.requests_processed,
            "totals": self.totals(),
            "per_model": self.per_model(),
            "per_purpose": self.per_purpose(),
            # The merged ledger: prior runs plus this one, so the next
            # invocation can adopt it and keep the accounting cumulative.
            "calls": [
                {
                    "model": r.model, "purpose": r.purpose, "input_tokens": r.input_tokens,
                    "output_tokens": r.output_tokens, "cached_tokens": r.cached_tokens,
                    "ok": r.ok, "error": r.error,
                }
                for r in self.all_records
            ],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path
