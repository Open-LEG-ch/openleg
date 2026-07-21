# SPDX-License-Identifier: AGPL-3.0-or-later
"""OpenLEG private network, Tier A (Program 9 W8): self-hosted WireGuard control plane.

An optional Headscale control server lets LEG members reach the box over a private
WireGuard network with no ports opened on the home router. It ships as a compose
profile so the base 4-service stack is unchanged and only starts on request.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _load(name):
    with open(ROOT / name) as handle:
        return yaml.safe_load(handle)


def test_net_override_defines_headscale_behind_profile():
    hs = _load("docker-compose.net.yml")["services"]["headscale"]
    assert "net" in hs["profiles"]  # optional; not started by default
    assert "headscale" in hs["image"]
    assert ":" in hs["image"]  # version-pinned, not :latest floating
    assert "latest" not in hs["image"]
    assert "web" in hs["networks"]


def test_base_compose_stays_four_services():
    base = _load("docker-compose.yml")
    assert len(base["services"]) == 4
    assert "headscale" not in base["services"]


def test_headscale_config_example_present():
    assert (ROOT / "headscale" / "config.example.yaml").is_file()
