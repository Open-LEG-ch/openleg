# SPDX-License-Identifier: AGPL-3.0-or-later
"""Security contracts for dashboard magic-link tokens."""

import hashlib

import dashboard_access


class _FakeDatabase:
    def __init__(self, *, saved=True, consumed=None):
        self.saved = saved
        self.consumed = consumed
        self.save_calls = []
        self.consume_calls = []

    def save_dashboard_access_token(self, token_hash, building_id, ttl_seconds):
        self.save_calls.append((token_hash, building_id, ttl_seconds))
        return self.saved

    def consume_dashboard_access_token(self, token_hash):
        self.consume_calls.append(token_hash)
        return self.consumed


def test_issue_returns_raw_token_but_persists_only_its_sha256_hash():
    raw_token = "dashboard-access-token-with-enough-entropy"
    database = _FakeDatabase()

    issued = dashboard_access.issue_access_token(
        database,
        "building-1",
        ttl_seconds=900,
        token_factory=lambda _size: raw_token,
    )

    assert issued == raw_token
    assert database.save_calls == [
        (hashlib.sha256(raw_token.encode()).hexdigest(), "building-1", 900)
    ]
    assert raw_token not in repr(database.save_calls)


def test_issue_fails_closed_when_token_cannot_be_persisted():
    database = _FakeDatabase(saved=False)

    issued = dashboard_access.issue_access_token(
        database,
        "building-1",
        token_factory=lambda _size: "dashboard-access-token-with-enough-entropy",
    )

    assert issued is None


def test_issue_rejects_missing_identity_or_unsafe_expiry_before_generation():
    database = _FakeDatabase()
    generated = []

    def token_factory(_size):
        generated.append(True)
        return "dashboard-access-token-with-enough-entropy"

    for building_id, ttl_seconds in (
        ("", 900),
        ("building-1", 59),
        ("building-1", 86_401),
    ):
        assert (
            dashboard_access.issue_access_token(
                database,
                building_id,
                ttl_seconds=ttl_seconds,
                token_factory=token_factory,
            )
            is None
        )

    assert generated == []
    assert database.save_calls == []


def test_consume_hashes_token_before_atomic_repository_exchange():
    raw_token = "dashboard-access-token-with-enough-entropy"
    database = _FakeDatabase(consumed={"building_id": "building-1"})

    building_id = dashboard_access.consume_access_token(database, raw_token)

    assert building_id == "building-1"
    assert database.consume_calls == [hashlib.sha256(raw_token.encode()).hexdigest()]


def test_consume_rejects_malformed_tokens_without_touching_database():
    database = _FakeDatabase(consumed={"building_id": "building-1"})

    for raw_token in ("", "short", "a" * 129, "spaces are not allowed here"):
        assert dashboard_access.consume_access_token(database, raw_token) is None

    assert database.consume_calls == []


def test_access_url_encodes_token_and_never_includes_building_id():
    url = dashboard_access.access_url(
        "https://openleg.ch/", "token/with?reserved#characters"
    )

    assert url == (
        "https://openleg.ch/dashboard/access/token%2Fwith%3Freserved%23characters"
    )
    assert "building" not in url
