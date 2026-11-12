# SPDX-License-Identifier: AGPL-3.0-or-later
"""The verified release tuple supplied by operations, never inferred from Git."""

import re
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ReleaseIdentity:
    tag: str
    source_sha: str
    image_digest: str

    def as_dict(self):
        return asdict(self)


def from_config(config):
    values = [
        config.get(key, "")
        for key in (
            "OPENLEG_RELEASE_TAG",
            "OPENLEG_RELEASE_SHA",
            "OPENLEG_RELEASE_IMAGE_DIGEST",
        )
    ]
    if not any(values):
        return None
    patterns = (r"v[0-9]+\.[0-9]+\.[0-9]+", r"[0-9a-f]{40}", r"sha256:[0-9a-f]{64}")
    if not all(
        isinstance(value, str) and re.fullmatch(pattern, value)
        for pattern, value in zip(patterns, values)
    ):
        raise ValueError(
            "release identity requires a valid tag, full SHA and image digest"
        )
    return ReleaseIdentity(*values)
