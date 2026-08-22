# SPDX-License-Identifier: AGPL-3.0-or-later
"""Helpers for issuing and consuming magic-link access tokens, both kinds.

The two kinds used to be two modules with the same body and a different noun,
so a fix to the token regex, the expiry bounds or the hashing could land in one
copy and never reach the other. One module now holds the policy and the kind is
data.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urljoin

_MIN_TOKEN_LENGTH = 32
_MAX_TOKEN_LENGTH = 128
_URLSAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")


def _normalize_building_id(subject: Any) -> str | None:
    """A building id is a non-empty string, stored without its padding."""
    if not isinstance(subject, str):
        return None
    stripped = subject.strip()
    return stripped or None


def _normalize_municipality_id(subject: Any) -> int | None:
    """A municipality id is a positive int. `True` is an int; it is not an id."""
    if type(subject) is not int or subject <= 0:
        return None
    return subject


@dataclass(frozen=True)
class AccessTokenKind:
    """One access-token kind. The difference between kinds is data, not code."""

    name: str
    subject_key: str
    save_name: str
    consume_name: str
    url_path: str
    normalize_subject: Callable[[Any], Any | None]


DASHBOARD = AccessTokenKind(
    name="dashboard",
    subject_key="building_id",
    save_name="save_dashboard_access_token",
    consume_name="consume_dashboard_access_token",
    url_path="dashboard/access",
    normalize_subject=_normalize_building_id,
)

MUNICIPALITY = AccessTokenKind(
    name="municipality",
    subject_key="municipality_id",
    save_name="save_municipality_access_token",
    consume_name="consume_municipality_access_token",
    url_path="gemeinde/access",
    normalize_subject=_normalize_municipality_id,
)


def _is_valid_token(raw_token: str) -> bool:
    return bool(_URLSAFE_TOKEN_RE.fullmatch(raw_token))


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def issue(
    kind: AccessTokenKind,
    repository,
    subject,
    *,
    ttl_seconds: int = 900,
    token_factory=secrets.token_urlsafe,
) -> str | None:
    subject = kind.normalize_subject(subject)
    if subject is None:
        return None
    if ttl_seconds < 60 or ttl_seconds > 86_400:
        return None

    raw_token = token_factory(_MIN_TOKEN_LENGTH)
    if not _is_valid_token(raw_token):
        return None

    saved = getattr(repository, kind.save_name)(
        _hash_token(raw_token), subject, ttl_seconds
    )
    if not saved:
        return None

    return raw_token


def consume(kind: AccessTokenKind, repository, raw_token: str):
    if not _is_valid_token(raw_token):
        return None

    row = getattr(repository, kind.consume_name)(_hash_token(raw_token))
    if not row:
        return None

    return row.get(kind.subject_key)


def access_url(kind: AccessTokenKind, base_url: str, raw_token: str) -> str:
    """Build the magic-link URL under `base_url`, keeping any path prefix.

    urljoin drops the last segment of a base without a trailing slash, so a
    deployment under https://example.ch/openleg would lose its prefix. The rule
    lives here rather than in each caller.
    """
    encoded_token = quote(raw_token, safe="")
    base = base_url if base_url.endswith("/") else base_url + "/"
    return urljoin(base, f"{kind.url_path}/{encoded_token}")
