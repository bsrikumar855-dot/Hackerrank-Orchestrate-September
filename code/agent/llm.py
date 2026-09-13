"""Gemini client wrapper.

Credentials come from the environment only (GEMINI_API_KEY or
GOOGLE_API_KEY), optionally seeded from a gitignored .env file at the repo
root. Nothing here ever logs, echoes, or persists a key.

Calls are configured for determinism as far as the API allows
(temperature=0, top_k=1, fixed seed where supported) and every call is
recorded in the UsageTracker from the first request onward.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional

from agent.usage import CallRecord, UsageTracker

REPO_ROOT = Path(__file__).resolve().parents[2]
# Evidence extraction (vision + message amendments) changes numbers, so it
# uses the stronger model. Explanation writing is low-stakes prose over
# already-computed figures and is validated before use, so it routes to the
# cheaper/faster model -- which also keeps the scarcer quota for the calls
# that actually affect the forecast.
DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
EXPLANATION_MODEL = os.environ.get("GEMINI_EXPLANATION_MODEL", "gemini-3.5-flash-lite")

# Free-tier quota is per model and is exhausted per day, so a long run can
# outlive one model's allowance mid-batch. Rather than stall, the client walks
# this chain when a model reports a daily-cap 429. Each call records the model
# that actually served it, so evaluation/usage_report.md shows the real mix.
# Override with GEMINI_FALLBACK_MODELS (comma-separated).
FALLBACK_MODELS = [
    m.strip()
    for m in os.environ.get(
        "GEMINI_FALLBACK_MODELS",
        "gemini-3-flash-preview,gemini-3.1-flash-lite,gemini-3.5-flash-lite,gemini-flash-lite-latest",
    ).split(",")
    if m.strip()
]
MAX_ATTEMPTS = 5
SEED = 20260913

# Free-tier quota shapes, read from the 429 payload rather than guessed. The
# observed free tier grants only *20 requests per day per model*
# (quotaId GenerateRequestsPerDayPerProjectPerModel-FreeTier, quotaValue 20),
# and -- importantly -- a daily-cap 429 still ships a "Please retry in 31s"
# hint that is useless: waiting it out just burns the clock and 429s again.
# So the daily cap is identified by its quotaId, not by hint presence, and the
# only useful response is to switch models. A genuine per-minute cap does
# clear on its own and is worth waiting out.
REQUESTS_PER_MINUTE = int(os.environ.get("GEMINI_RPM", "30"))
MAX_ATTEMPTS_NO_HINT_429 = 2
_RETRY_HINT = re.compile(r"retry in ([\d.]+)s", re.IGNORECASE)
_DAILY_QUOTA = re.compile(r"PerDay|RequestsPerDay|per day", re.IGNORECASE)


class _RateLimiter:
    """Process-wide minimum spacing between API calls."""

    def __init__(self, requests_per_minute: int):
        self.min_interval = 60.0 / max(1, requests_per_minute)
        self._last_call = 0.0

    def wait(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_call = time.monotonic()


_LIMITER = _RateLimiter(REQUESTS_PER_MINUTE)


class LLMUnavailableError(RuntimeError):
    pass


class CallBudgetExhausted(RuntimeError):
    """Raised when a run's configured API-call budget is used up.

    The observed free tier grants only 20 requests per day per model, so a
    long run can genuinely run out of capacity mid-way. A budget makes that
    boundary explicit and lets a run finish deterministically with the
    evidence it already has, instead of stalling on doomed retries.
    """


def _read_text_any_encoding(path: Path) -> str:
    """Read a small text file written by any common shell.

    PowerShell's `>` redirection emits UTF-16 LE with a BOM, cmd and POSIX
    shells emit UTF-8, and some editors add a UTF-8 BOM -- a .env written on
    Windows is frequently not plain UTF-8, so decode defensively rather than
    crashing on a byte-order mark.
    """
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "utf-16-le", "utf-8", "latin-1"):
        try:
            text = raw.decode(encoding)
        except (UnicodeDecodeError, UnicodeError):
            continue
        if "\x00" not in text:  # a mis-picked 8-bit codec on UTF-16 leaves NULs
            return text
    return raw.decode("utf-8", errors="replace")


def load_dotenv(path: Optional[Path] = None) -> None:
    """Seed os.environ from a gitignored .env, without overwriting real env
    vars. Values are never logged. Intentionally dependency-free."""
    path = path or REPO_ROOT / ".env"
    if not path.exists():
        return
    for raw in _read_text_any_encoding(path).splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def api_key() -> Optional[str]:
    load_dotenv()
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")


def is_available() -> bool:
    if not api_key():
        return False
    try:
        import google.genai  # noqa: F401
    except ImportError:
        return False
    return True


_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def parse_json_response(text: str) -> Any:
    """Tolerant JSON extraction: accepts a bare object, a fenced block, or an
    object embedded in prose. Raises ValueError if nothing parses -- callers
    treat that as a failed extraction and fall back, never as silent success.
    """
    if not text:
        raise ValueError("empty response")
    candidates = [text.strip()]
    m = _JSON_BLOCK.search(text)
    if m:
        candidates.insert(0, m.group(1).strip())
    first, last = text.find("{"), text.rfind("}")
    if first != -1 and last > first:
        candidates.append(text[first : last + 1])
    for c in candidates:
        try:
            return json.loads(c)
        except json.JSONDecodeError:
            continue
    raise ValueError(f"no parseable JSON in response: {text[:200]!r}")


class GeminiClient:
    def __init__(
        self,
        tracker: UsageTracker,
        model: str = DEFAULT_MODEL,
        fallback_models: Optional[list[str]] = None,
    ):
        self.tracker = tracker
        self.model = model
        # Models to fall back to when `model` hits its daily cap. The active
        # model is sticky once switched, so a long run doesn't re-probe an
        # exhausted model on every call.
        self.fallback_models = [
            m for m in (fallback_models if fallback_models is not None else FALLBACK_MODELS) if m != model
        ]
        self._client = None
        # None = unlimited. Counts attempted calls, so doomed retries against
        # an exhausted daily cap cannot silently eat a whole run's budget.
        self.call_budget: Optional[int] = None
        self.calls_attempted = 0

    def _switch_to_fallback(self, purpose: str) -> bool:
        """Move to the next model with remaining quota. Returns False when the
        chain is exhausted."""
        while self.fallback_models:
            nxt = self.fallback_models.pop(0)
            print(
                f"[llm] {self.model} hit its daily quota during {purpose}; switching to {nxt}",
                file=sys.stderr,
            )
            self.model = nxt
            return True
        return False

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        key = api_key()
        if not key:
            raise LLMUnavailableError(
                "no GEMINI_API_KEY/GOOGLE_API_KEY in the environment (set it in the "
                "gitignored .env at the repo root or export it before running)"
            )
        try:
            from google import genai
        except ImportError as exc:
            raise LLMUnavailableError("google-genai is not installed (pip install google-genai)") from exc
        self._client = genai.Client(api_key=key)
        return self._client

    def generate_json(
        self,
        prompt: str,
        purpose: str,
        image_paths: Optional[list[Path]] = None,
        max_output_tokens: int = 2048,
    ) -> Any:
        """One instrumented, retried, JSON-returning call. Raises on final failure."""
        client = self._ensure_client()
        from google.genai import types

        parts: list[Any] = [types.Part.from_text(text=prompt)]
        for p in image_paths or []:
            parts.append(types.Part.from_bytes(data=p.read_bytes(), mime_type="image/png"))

        config = types.GenerateContentConfig(
            temperature=0.0,
            top_k=1,
            seed=SEED,
            max_output_tokens=max_output_tokens,
            response_mime_type="application/json",
        )

        last_error: Optional[Exception] = None
        for attempt in range(MAX_ATTEMPTS):
            if self.call_budget is not None and self.calls_attempted >= self.call_budget:
                raise CallBudgetExhausted(
                    f"{purpose}: API call budget of {self.call_budget} reached"
                )
            self.calls_attempted += 1
            try:
                _LIMITER.wait()
                response = client.models.generate_content(
                    model=self.model,
                    contents=[types.Content(role="user", parts=parts)],
                    config=config,
                )
                usage = getattr(response, "usage_metadata", None)
                self.tracker.record(
                    CallRecord(
                        model=self.model,
                        purpose=purpose,
                        input_tokens=int(getattr(usage, "prompt_token_count", 0) or 0),
                        output_tokens=int(getattr(usage, "candidates_token_count", 0) or 0),
                        cached_tokens=int(getattr(usage, "cached_content_token_count", 0) or 0),
                        ok=True,
                    )
                )
                return parse_json_response(response.text or "")
            except Exception as exc:  # transport error, rate limit, or unparseable JSON
                last_error = exc
                self.tracker.record(
                    CallRecord(
                        model=self.model, purpose=purpose, input_tokens=0, output_tokens=0,
                        ok=False, error=f"{exc.__class__.__name__}: {exc}"[:300],
                    )
                )
                if self._is_exhausted_quota(exc) and self._switch_to_fallback(purpose):
                    continue  # retry immediately on the next model, no backoff
                attempts_allowed = (
                    MAX_ATTEMPTS_NO_HINT_429 if self._is_exhausted_quota(exc) else MAX_ATTEMPTS
                )
                if attempt >= attempts_allowed - 1:
                    break
                time.sleep(self._backoff_seconds(exc, attempt))
        raise RuntimeError(f"{purpose} failed: {last_error}")

    @staticmethod
    def _is_exhausted_quota(exc: Exception) -> bool:
        """True when this 429 is a per-DAY cap, which will not clear within the
        run, so the only useful response is to switch models.

        Identified by the quotaId in the payload
        ("GenerateRequestsPerDayPerProjectPerModel-FreeTier"), not by whether
        a retry hint is present -- daily-cap responses ship a `retry in 31s`
        hint anyway, and obeying it just stalls the run. A hint-less 429 is
        also treated as exhausted, since there is then nothing to wait for.
        """
        text = str(exc)
        if "429" not in text and "RESOURCE_EXHAUSTED" not in text:
            return False
        return bool(_DAILY_QUOTA.search(text)) or not _RETRY_HINT.search(text)

    @staticmethod
    def _backoff_seconds(exc: Exception, attempt: int) -> float:
        """Exponential backoff, but prefer the server's own retry hint when a
        rate-limit response supplies one."""
        text = str(exc)
        hint = _RETRY_HINT.search(text)
        if hint:
            try:
                return max(1.0, float(hint.group(1)) + 1.0)
            except ValueError:
                pass
        if "429" in text or "RESOURCE_EXHAUSTED" in text:
            return min(30.0, 5.0 * (attempt + 1))
        return min(30.0, 2.0 * (2**attempt))
