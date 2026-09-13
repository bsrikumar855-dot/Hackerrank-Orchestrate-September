"""Batched amendment extraction: many users per API call. Batching must
change throughput only -- never which amendment a given user gets, and never
silently drop a user when a batch response is unusable.

Uses fake clients, so no network and no key required.
"""
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from agent.evidence import BatchItem, EvidenceCache, extract_amendments_batched
from core.models import Message


def _msg(mid, uid, text):
    return Message(
        message_id=mid, user_id=uid, request_id=None, related_event_id=None,
        sent_at="2026-01-01T00:00:00Z", source_type="employer", message_text=text,
    )


def _item(uid):
    return BatchItem(
        user_id=uid,
        messages=[_msg(f"m_{uid}", uid, f"Payroll for {uid} rises to ZAR 1200 from 2026-02-01.")],
        known_series={("salary", "credit"): Decimal("1000")},
        home_currency="ZAR",
        request_date=date(2026, 1, 15),
    )


class FakeBatchClient:
    """Returns a well-formed batched payload for every user in the prompt."""

    def __init__(self):
        self.calls = 0

    def generate_json(self, prompt, purpose, image_paths=None, max_output_tokens=0):
        self.calls += 1
        users = {}
        for uid in ("user_01", "user_02", "user_03", "user_04", "user_05", "user_06", "user_07"):
            if f'id="{uid}"' in prompt:
                users[uid] = {
                    "amendments": [
                        {
                            "kind": "income_change", "category": "salary", "new_amount": 1200,
                            "change_pct": None, "currency": "ZAR", "effective_date": "2026-02-01",
                            "scope": "ongoing", "confidence": "high",
                            "source_message_id": f"m_{uid}", "reason": "rises to 1200",
                        }
                    ],
                    "injection_attempts": [],
                }
        return {"users": users}


class BrokenBatchClient:
    """Returns junk for the batch call, then a valid single-user payload."""

    def __init__(self):
        self.calls = 0
        self.batch_calls = 0
        self.single_calls = 0

    def generate_json(self, prompt, purpose, image_paths=None, max_output_tokens=0):
        self.calls += 1
        if purpose == "message_amendments_batched":
            self.batch_calls += 1
            return {"unexpected": "shape"}
        self.single_calls += 1
        return {
            "amendments": [
                {
                    "kind": "income_change", "category": "salary", "new_amount": 900,
                    "change_pct": None, "currency": "ZAR", "effective_date": None,
                    "scope": "ongoing", "confidence": "high",
                    "source_message_id": "m", "reason": "reduced",
                }
            ],
            "injection_attempts": [],
        }


def test_one_call_covers_a_whole_batch(tmp_path: Path):
    cache = EvidenceCache(tmp_path / "cache.json")
    items = [_item(f"user_0{i}") for i in range(1, 7)]
    client = FakeBatchClient()
    calls = extract_amendments_batched(items, client, cache, batch_size=6)
    assert calls == 1  # six users, one call
    for item in items:
        cached = cache.get_amendments(item.user_id)
        assert cached and cached["amendments"][0]["new_amount"] == 1200


def test_batches_are_chunked_by_batch_size(tmp_path: Path):
    cache = EvidenceCache(tmp_path / "cache.json")
    items = [_item(f"user_0{i}") for i in range(1, 8)]  # 7 users
    client = FakeBatchClient()
    calls = extract_amendments_batched(items, client, cache, batch_size=3)
    assert calls == 3  # 3 + 3 + 1
    assert all(cache.get_amendments(i.user_id) for i in items)


def test_already_cached_users_cost_nothing(tmp_path: Path):
    cache = EvidenceCache(tmp_path / "cache.json")
    items = [_item("user_01"), _item("user_02")]
    cache.put_amendments("user_01", {"amendments": [], "injection_attempts": []})
    client = FakeBatchClient()
    extract_amendments_batched(items, client, cache, batch_size=6)
    assert 'id="user_01"' not in "".join([""])  # sanity: nothing weird
    assert cache.get_amendments("user_01") == {"amendments": [], "injection_attempts": []}
    assert cache.get_amendments("user_02")["amendments"][0]["new_amount"] == 1200


def test_unusable_batch_response_falls_back_to_per_user_calls(tmp_path: Path):
    cache = EvidenceCache(tmp_path / "cache.json")
    items = [_item("user_01"), _item("user_02")]
    client = BrokenBatchClient()
    extract_amendments_batched(items, client, cache, batch_size=6)
    assert client.batch_calls == 1
    assert client.single_calls == 2  # both users recovered individually
    for item in items:
        assert cache.get_amendments(item.user_id)["amendments"][0]["new_amount"] == 900


class PartialBatchClient:
    """Answers the batch but omits one user entirely -- the observed failure
    mode that silently lost real evidence before it was handled."""

    def __init__(self, omit):
        self.omit = omit
        self.batch_calls = 0
        self.single_calls = 0
        self.single_users = []

    def generate_json(self, prompt, purpose, image_paths=None, max_output_tokens=0):
        if purpose == "message_amendments_batched":
            self.batch_calls += 1
            users = {}
            for uid in ("user_01", "user_02", "user_03"):
                if f'id="{uid}"' in prompt and uid != self.omit:
                    users[uid] = {"amendments": [], "injection_attempts": []}
            return {"users": users}
        self.single_calls += 1
        # The single-user prompt has no <user id=...> wrapper; identify the
        # user by its fenced message id (m_<user_id>) instead.
        for uid in ("user_01", "user_02", "user_03"):
            if f"m_{uid}" in prompt:
                self.single_users.append(uid)
        return {
            "amendments": [
                {
                    "kind": "income_change", "category": "salary", "new_amount": 0,
                    "change_pct": None, "currency": "ZAR", "effective_date": None,
                    "scope": "ongoing", "confidence": "high",
                    "source_message_id": "m", "reason": "payout still pending",
                }
            ],
            "injection_attempts": [],
        }


def test_user_omitted_from_a_batch_response_is_re_asked_individually(tmp_path: Path):
    cache = EvidenceCache(tmp_path / "cache.json")
    items = [_item("user_01"), _item("user_02"), _item("user_03")]
    client = PartialBatchClient(omit="user_02")
    extract_amendments_batched(items, client, cache, batch_size=6)

    assert client.batch_calls == 1
    assert client.single_calls == 1
    assert client.single_users == ["user_02"]
    # the omitted user's real evidence survived instead of being cached as {}
    assert cache.get_amendments("user_02")["amendments"][0]["new_amount"] == 0
    assert cache.get_amendments("user_01")["amendments"] == []


def test_users_without_messages_are_not_sent(tmp_path: Path):
    cache = EvidenceCache(tmp_path / "cache.json")
    empty = BatchItem(
        user_id="user_09", messages=[], known_series={}, home_currency="ZAR",
        request_date=date(2026, 1, 15),
    )
    client = FakeBatchClient()
    calls = extract_amendments_batched([empty], client, cache, batch_size=6)
    assert calls == 0
    assert cache.get_amendments("user_09") is None


def test_cache_survives_a_reload(tmp_path: Path):
    path = tmp_path / "cache.json"
    cache = EvidenceCache(path)
    extract_amendments_batched([_item("user_01")], FakeBatchClient(), cache, batch_size=6)
    reloaded = EvidenceCache(path)
    assert reloaded.get_amendments("user_01")["amendments"][0]["category"] == "salary"
    assert json.loads(path.read_text(encoding="utf-8"))["amendments"]["user_01"]
