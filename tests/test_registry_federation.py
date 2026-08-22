# SPDX-License-Identifier: AGPL-3.0-or-later
"""Registry federation (Program 9 W5): self-hosted boxes feed the public registry.

A self-hosted OpenLEG instance publishes its LEG to the central registry over a
JSON API. Entries land as source="self_hosted" and stay moderation pending, the
same human-moderated, honesty-bounded path as a web submission. This is the
loop the incumbent cannot run: self-host distribution -> public registry -> SEO.
"""

import os
from unittest.mock import MagicMock

from flask import Flask

import leg_registry as leg_registry_module

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

VALID = {
    "name": "LEG Sonnenhof",
    "contact_email": "kontakt@sonnenhof.example.ch",
    "kanton": "zh",
    "plz": "8000",
    "ort": "Zürich",
    "leg_status": "aktiv",
    "vnb_name": "EKZ",
}


def _client(monkeypatch, saved=1):
    monkeypatch.setattr(
        leg_registry_module.registry_intake,
        "_unique_slug",
        lambda name: "leg-sonnenhof",
    )
    save = MagicMock(return_value=saved)
    monkeypatch.setattr(leg_registry_module.db, "save_registry_entry", save)
    monkeypatch.setattr(leg_registry_module.db, "track_event", MagicMock())
    app = Flask(__name__)
    app.register_blueprint(leg_registry_module.registry_api_bp)
    return app.test_client(), save


class TestPublishEndpoint:
    def test_creates_pending_self_hosted_entry(self, monkeypatch):
        client, save = _client(monkeypatch)
        resp = client.post("/api/registry/publish", json=VALID)
        assert resp.status_code == 201
        body = resp.get_json()
        assert body["slug"] == "leg-sonnenhof"
        assert body["moderation_status"] == "pending"
        _, kwargs = save.call_args
        assert kwargs["source"] == "self_hosted"
        assert kwargs["name"] == "LEG Sonnenhof"

    def test_requires_name_and_email(self, monkeypatch):
        client, save = _client(monkeypatch)
        resp = client.post("/api/registry/publish", json={"name": "Nur Name"})
        assert resp.status_code == 400
        save.assert_not_called()

    def test_rejects_invalid_email(self, monkeypatch):
        client, save = _client(monkeypatch)
        bad = dict(VALID, contact_email="not-an-email")
        resp = client.post("/api/registry/publish", json=bad)
        assert resp.status_code == 400
        save.assert_not_called()


def test_publish_route_in_source():
    with open(
        os.path.join(PROJECT_ROOT, "leg_registry.py"), encoding="utf-8"
    ) as handle:
        assert '"/api/registry/publish"' in handle.read()
