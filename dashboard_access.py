# SPDX-License-Identifier: AGPL-3.0-or-later
"""Helpers for issuing and consuming dashboard access tokens."""

from __future__ import annotations

import hashlib
import re
import secrets
from urllib.parse import quote, urljoin

_MIN_TOKEN_LENGTH = 32
_MAX_TOKEN_LENGTH = 128
_URLSAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")


def _is_valid_token(raw_token: str) -> bool:
    return bool(_URLSAFE_TOKEN_RE.fullmatch(raw_token))


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def issue_access_token(
    repository,
    building_id: str,
    *,
    ttl_seconds: int = 900,
    token_factory=secrets.token_urlsafe,
) -> str | None:
    building_id = (building_id or "").strip()
    if not building_id:
        return None
    if ttl_seconds < 60 or ttl_seconds > 86_400:
        return None

    raw_token = token_factory(_MIN_TOKEN_LENGTH)
    if not _is_valid_token(raw_token):
        return None

    saved = repository.save_dashboard_access_token(
        _hash_token(raw_token), building_id, ttl_seconds
    )
    if not saved:
        return None

    return raw_token


def consume_access_token(repository, raw_token: str) -> str | None:
    if not _is_valid_token(raw_token):
        return None

    row = repository.consume_dashboard_access_token(_hash_token(raw_token))
    if not row:
        return None

    return row.get("building_id")


def access_url(base_url: str, raw_token: str) -> str:
    encoded_token = quote(raw_token, safe="")
    return urljoin(base_url, f"dashboard/access/{encoded_token}")
