# SPDX-License-Identifier: AGPL-3.0-or-later
"""The shared development proxy resolves to an immutable release image."""

import re
from pathlib import Path

import yaml


def test_development_caddy_uses_a_release_and_immutable_digest():
    compose = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "docker-compose.yml").read_text()
    )
    assert re.fullmatch(
        r"caddy:\d+\.\d+\.\d+-alpine@sha256:[0-9a-f]{64}",
        compose["services"]["caddy"]["image"],
    )
