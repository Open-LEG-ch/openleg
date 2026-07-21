# SPDX-License-Identifier: AGPL-3.0-or-later
"""A self-hosted OpenLEG box publishes its LEG to a central registry."""

import time

import requests


def publish_leg(base_url: str, entry: dict, timeout: int = 10) -> dict:
    url = base_url.rstrip("/") + "/api/registry/publish"
    for attempt in range(3):
        try:
            response = requests.post(url, json=entry, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            if attempt == 2:
                raise
            time.sleep(0.1 * (attempt + 1))

    raise RuntimeError("unreachable")
