"""Model fallback on daily-quota exhaustion.

Free-tier quota is per model and per day, so a long run can outlive one
model's allowance mid-batch (it happened four times while building this).
The client must walk a fallback chain rather than stall, and must keep
recording which model actually served each call.
"""
import pytest

from agent.llm import GeminiClient
from agent.usage import UsageTracker


class _Quota429(Exception):
    """Mimics a daily-cap 429: no 'retry in Xs' hint."""

    def __str__(self):
        return (
            "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your "
            "current quota', 'status': 'RESOURCE_EXHAUSTED'}}"
        )


class _RateLimit429(Exception):
    """Mimics a per-minute 429: carries a retry hint, so it is waited out."""

    def __str__(self):
        return "429 RESOURCE_EXHAUSTED. Please retry in 0.2s"


def test_is_exhausted_quota_distinguishes_the_two_429_shapes():
    assert GeminiClient._is_exhausted_quota(_Quota429()) is True
    assert GeminiClient._is_exhausted_quota(_RateLimit429()) is False


def test_hinted_429_backoff_uses_the_servers_own_hint():
    assert GeminiClient._backoff_seconds(_RateLimit429(), 0) == pytest.approx(1.2, abs=0.01)


def test_switch_to_fallback_walks_the_chain_then_gives_up():
    client = GeminiClient(UsageTracker(), model="m0", fallback_models=["m1", "m2"])
    assert client.model == "m0"
    assert client._switch_to_fallback("test") is True
    assert client.model == "m1"
    assert client._switch_to_fallback("test") is True
    assert client.model == "m2"
    assert client._switch_to_fallback("test") is False  # chain exhausted
    assert client.model == "m2"


def test_fallback_list_never_includes_the_primary_model():
    client = GeminiClient(UsageTracker(), model="m1", fallback_models=["m1", "m2"])
    assert client.fallback_models == ["m2"]


def test_generate_json_switches_models_and_records_the_serving_model(monkeypatch):
    tracker = UsageTracker()
    client = GeminiClient(tracker, model="dead-model", fallback_models=["live-model"])

    class _Usage:
        prompt_token_count = 11
        candidates_token_count = 7
        cached_content_token_count = 0

    class _Response:
        text = '{"ok": true}'
        usage_metadata = _Usage()

    class _Models:
        def generate_content(self, model, contents, config):
            if model == "dead-model":
                raise _Quota429()
            return _Response()

    class _FakeSDKClient:
        models = _Models()

    monkeypatch.setattr(client, "_ensure_client", lambda: _FakeSDKClient())

    result = client.generate_json("prompt", purpose="unit_test")
    assert result == {"ok": True}
    assert client.model == "live-model"

    per_model = tracker.per_model()
    assert per_model["dead-model"]["failed_calls"] == 1
    assert per_model["live-model"]["calls"] == 1
    assert per_model["live-model"]["input_tokens"] == 11
    assert per_model["live-model"]["output_tokens"] == 7
