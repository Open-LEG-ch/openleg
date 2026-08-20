# SPDX-License-Identifier: AGPL-3.0-or-later
"""Security contracts for municipality magic-link tokens."""

import hashlib

import municipality_access


class _Repository:
    def __init__(self, *, saved=True, consumed=None):
        self.saved = saved
        self.consumed = consumed
        self.save_calls = []
        self.consume_calls = []

    def save_municipality_access_token(self, token_hash, municipality_id, ttl_seconds):
        self.save_calls.append((token_hash, municipality_id, ttl_seconds))
        return self.saved

    def consume_municipality_access_token(self, token_hash):
        self.consume_calls.append(token_hash)
        return self.consumed


def test_issue_returns_raw_token_but_persists_only_sha256_hash():
    raw_token = "municipality-access-token-with-enough-entropy"
    repository = _Repository()

    issued = municipality_access.issue_access_token(
        repository, 7, ttl_seconds=900, token_factory=lambda _size: raw_token
    )

    assert issued == raw_token
    assert repository.save_calls == [
        (hashlib.sha256(raw_token.encode()).hexdigest(), 7, 900)
    ]
    assert raw_token not in repr(repository.save_calls)


def test_consume_hashes_token_before_atomic_repository_exchange():
    raw_token = "municipality-access-token-with-enough-entropy"
    repository = _Repository(consumed={"municipality_id": 7})

    municipality_id = municipality_access.consume_access_token(repository, raw_token)

    assert municipality_id == 7
    assert repository.consume_calls == [hashlib.sha256(raw_token.encode()).hexdigest()]


def test_malformed_tokens_fail_before_repository_access():
    repository = _Repository(consumed={"municipality_id": 7})

    for raw_token in ("", "short", "a" * 129, "spaces are rejected"):
        assert municipality_access.consume_access_token(repository, raw_token) is None

    assert repository.consume_calls == []


def test_issue_rejects_invalid_municipality_ids_before_repository_access():
    repository = _Repository()

    for municipality_id in (None, 0, -1, "7", True):
        assert (
            municipality_access.issue_access_token(repository, municipality_id) is None
        )

    assert repository.save_calls == []
