# SPDX-License-Identifier: AGPL-3.0-or-later
"""Security contracts for magic-link access tokens, dashboard and municipality.

The two kinds used to be two modules with the same body and a different noun,
so a fix to the token regex, the expiry bounds or the hashing could land in one
copy and never reach the other. One module now holds the policy and the kind is
data. These tests run every contract against both kinds.
"""

import hashlib

import pytest

import access_token


class _Repository:
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

    def save_municipality_access_token(self, token_hash, municipality_id, ttl_seconds):
        self.save_calls.append((token_hash, municipality_id, ttl_seconds))
        return self.saved

    def consume_municipality_access_token(self, token_hash):
        self.consume_calls.append(token_hash)
        return self.consumed


def _kinds():
    return (
        pytest.param(access_token.DASHBOARD, "building-1", id="dashboard"),
        pytest.param(access_token.MUNICIPALITY, 7, id="municipality"),
    )


RAW_TOKEN = "access-token-with-more-than-enough-entropy"


@pytest.mark.parametrize("kind, subject", _kinds())
def test_issue_returns_the_raw_token_but_persists_only_its_sha256_hash(kind, subject):
    repository = _Repository()

    issued = access_token.issue(
        kind,
        repository,
        subject,
        ttl_seconds=900,
        token_factory=lambda _size: RAW_TOKEN,
    )

    assert issued == RAW_TOKEN
    assert repository.save_calls == [
        (hashlib.sha256(RAW_TOKEN.encode()).hexdigest(), subject, 900)
    ]
    assert RAW_TOKEN not in repr(repository.save_calls)


@pytest.mark.parametrize("kind, subject", _kinds())
def test_issue_fails_closed_when_the_token_cannot_be_persisted(kind, subject):
    repository = _Repository(saved=False)

    assert (
        access_token.issue(
            kind, repository, subject, token_factory=lambda _size: RAW_TOKEN
        )
        is None
    )


@pytest.mark.parametrize("kind, subject", _kinds())
@pytest.mark.parametrize("ttl_seconds", (59, 86_401))
def test_issue_rejects_an_unsafe_expiry_before_generating_a_token(
    kind, subject, ttl_seconds
):
    repository = _Repository()
    generated = []

    def token_factory(_size):
        generated.append(True)
        return RAW_TOKEN

    assert (
        access_token.issue(
            kind,
            repository,
            subject,
            ttl_seconds=ttl_seconds,
            token_factory=token_factory,
        )
        is None
    )
    assert generated == []
    assert repository.save_calls == []


@pytest.mark.parametrize(
    "kind, subject",
    (
        pytest.param(access_token.DASHBOARD, "", id="dashboard-blank"),
        pytest.param(access_token.DASHBOARD, "   ", id="dashboard-whitespace"),
        pytest.param(access_token.DASHBOARD, None, id="dashboard-missing"),
        pytest.param(access_token.MUNICIPALITY, None, id="municipality-missing"),
        pytest.param(access_token.MUNICIPALITY, 0, id="municipality-zero"),
        pytest.param(access_token.MUNICIPALITY, -1, id="municipality-negative"),
        pytest.param(access_token.MUNICIPALITY, "7", id="municipality-string"),
        pytest.param(access_token.MUNICIPALITY, True, id="municipality-bool"),
    ),
)
def test_issue_rejects_an_invalid_subject_before_touching_the_repository(kind, subject):
    repository = _Repository()

    assert access_token.issue(kind, repository, subject) is None
    assert repository.save_calls == []


@pytest.mark.parametrize("kind, subject", _kinds())
def test_consume_hashes_the_token_before_the_atomic_exchange(kind, subject):
    repository = _Repository(consumed={kind.subject_key: subject})

    assert access_token.consume(kind, repository, RAW_TOKEN) == subject
    assert repository.consume_calls == [hashlib.sha256(RAW_TOKEN.encode()).hexdigest()]


@pytest.mark.parametrize("kind, subject", _kinds())
@pytest.mark.parametrize(
    "raw_token", ("", "short", "a" * 129, "spaces are not allowed here")
)
def test_consume_rejects_a_malformed_token_without_touching_the_repository(
    kind, subject, raw_token
):
    repository = _Repository(consumed={kind.subject_key: subject})

    assert access_token.consume(kind, repository, raw_token) is None
    assert repository.consume_calls == []


@pytest.mark.parametrize(
    "kind, expected",
    (
        pytest.param(
            access_token.DASHBOARD,
            "https://openleg.ch/dashboard/access/token%2Fwith%3Freserved%23characters",
            id="dashboard",
        ),
        pytest.param(
            access_token.MUNICIPALITY,
            "https://openleg.ch/gemeinde/access/token%2Fwith%3Freserved%23characters",
            id="municipality",
        ),
    ),
)
def test_access_url_encodes_the_token_and_names_no_subject(kind, expected):
    url = access_token.access_url(
        kind, "https://openleg.ch/", "token/with?reserved#characters"
    )

    assert url == expected
    assert "building" not in url
    assert "municipality" not in url


def test_both_kinds_hash_through_the_same_implementation(monkeypatch):
    """Divergence is the failure mode this module exists to prevent."""
    calls = []

    def _tracked(raw_token):
        calls.append(raw_token)
        return "f" * 64

    monkeypatch.setattr(access_token, "_hash_token", _tracked)
    repository = _Repository()

    access_token.issue(
        access_token.DASHBOARD,
        repository,
        "building-1",
        token_factory=lambda _size: RAW_TOKEN,
    )
    access_token.issue(
        access_token.MUNICIPALITY,
        repository,
        7,
        token_factory=lambda _size: RAW_TOKEN,
    )

    assert calls == [RAW_TOKEN, RAW_TOKEN]
    assert [call[0] for call in repository.save_calls] == ["f" * 64, "f" * 64]


@pytest.mark.parametrize("module", ("dashboard_access", "municipality_access"))
def test_the_duplicated_modules_are_gone(module):
    with pytest.raises(ModuleNotFoundError):
        __import__(module)
