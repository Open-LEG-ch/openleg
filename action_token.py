# SPDX-License-Identifier: AGPL-3.0-or-later
"""Digest policy for verification, claim and deletion bearer links."""

import hashlib


def digest(raw_token: str) -> str:
    """Hash every presented value, including values that resemble a digest."""
    return "sha256$" + hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
