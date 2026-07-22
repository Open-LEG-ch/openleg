# SPDX-License-Identifier: AGPL-3.0-or-later
"""Trust and accessibility contracts for the public LEG registry journey."""

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

from flask import Flask, render_template

import leg_registry


TEMPLATES = Path(__file__).parents[1] / "templates"
BASE_ENTRY = {
    "id": 1,
    "slug": "leg-baden",
    "name": "LEG Baden",
    "ort": "Baden",
    "kanton": "AG",
    "plz": "5400",
    "leg_status": "aktiv",
    "moderation_status": "published",
    "source": "self_submitted",
    "claimed_at": None,
    "last_verified_at": None,
}


def _app():
    app = Flask(__name__, template_folder=TEMPLATES)
    app.config["TESTING"] = True
    app.register_blueprint(leg_registry.registry_bp)
    return app


def _marker(html):
    start = html.index('data-testid="registry-trust"')
    start = html.rfind("<", 0, start)
    end = html.index("</div>", start) + len("</div>")
    return html[start:end]


def test_registry_trust_states_use_distinct_words_and_markers():
    app = _app()
    states = {
        "pending": {**BASE_ENTRY, "moderation_status": "pending"},
        "self_reported": BASE_ENTRY,
        "confirmed": {
            **BASE_ENTRY,
            "source": "claimed",
            "claimed_at": datetime(2026, 6, 2),
        },
    }

    with app.app_context():
        rendered = {
            name: render_template("partials/registry_trust.html", entry=entry)
            for name, entry in states.items()
        }

    assert "Prüfung ausstehend" in rendered["pending"]
    assert "Moderierte Selbstauskunft" in rendered["self_reported"]
    assert "Vom Betreiber bestätigt" in rendered["confirmed"]
    assert len({_marker(html) for html in rendered.values()}) == 3


def test_same_registry_trust_state_renders_identically_in_list_and_detail(monkeypatch):
    entry = {
        **BASE_ENTRY,
        "source": "claimed",
        "claimed_at": datetime(2026, 6, 2),
    }
    monkeypatch.setattr(leg_registry.db, "list_registry_entries", lambda **_: [entry])
    monkeypatch.setattr(leg_registry.db, "get_registry_entry_by_slug", lambda _: entry)
    client = _app().test_client()

    list_html = client.get("/leg-verzeichnis").get_data(as_text=True)
    detail_html = client.get("/leg-verzeichnis/leg-baden").get_data(as_text=True)

    assert _marker(list_html) == _marker(detail_html)


def test_registry_trust_states_last_verification_or_plain_absence():
    app = _app()
    confirmed = {
        **BASE_ENTRY,
        "source": "claimed",
        "claimed_at": datetime(2026, 6, 2),
        "last_verified_at": datetime(2026, 7, 10),
    }
    never_reconfirmed = {**confirmed, "last_verified_at": None}

    with app.app_context():
        fresh = render_template("partials/registry_trust.html", entry=confirmed)
        missing = render_template(
            "partials/registry_trust.html", entry=never_reconfirmed
        )

    assert "10.07.2026" in fresh
    assert "Zuletzt bestätigt" in fresh
    assert "Noch nie erneut bestätigt" in missing


def test_registry_trust_renders_string_dates_without_error():
    entry = {
        **BASE_ENTRY,
        "source": "claimed",
        "claimed_at": "02.06.2026",
        "last_verified_at": "10.07.2026",
    }

    with _app().app_context():
        rendered = render_template("partials/registry_trust.html", entry=entry)

    assert "02.06.2026" in rendered
    assert "10.07.2026" in rendered


def test_registry_forms_keep_controls_actions_and_bound_labels():
    expected = {
        "liste.html": ("get", ["q", "plz", "kanton", "leg_status"]),
        "leg_check.html": ("get", ["q"]),
        "eintragen.html": (
            "post",
            [
                "name",
                "contact_email",
                "plz",
                "ort",
                "kanton",
                "leg_status",
                "vnb_name",
                "member_count_estimate",
                "website_url",
                "description",
            ],
        ),
        "beanspruchen.html": ("post", []),
    }

    for filename, (method, controls) in expected.items():
        source = (TEMPLATES / "leg_verzeichnis" / filename).read_text()
        assert f'<form method="{method}"' in source
        for control in controls:
            assert f'name="{control}"' in source
            assert f'id="{control}"' in source
            assert f'for="{control}"' in source


def test_registry_search_publish_and_public_pending_contracts_unchanged(monkeypatch):
    listed = MagicMock(return_value=[])
    monkeypatch.setattr(leg_registry.db, "list_registry_entries", listed)
    monkeypatch.setattr(
        leg_registry.db,
        "get_registry_entry_by_slug",
        lambda _: {**BASE_ENTRY, "moderation_status": "pending"},
    )
    monkeypatch.setattr(
        leg_registry.registry_intake,
        "submit",
        lambda data, source: {"error": None, "slug": "leg-baden"},
    )
    client = _app().test_client()

    client.get("/leg-verzeichnis?q=Baden&plz=5400&kanton=ag&leg_status=aktiv")
    assert listed.call_args.kwargs == {
        "q": "Baden",
        "plz": "5400",
        "kanton": "AG",
        "leg_status": "aktiv",
    }
    assert client.get("/leg-verzeichnis/leg-baden").status_code == 404
    response = client.post("/api/registry/publish", json={"name": "LEG Baden"})
    assert response.status_code == 201
    assert response.get_json() == {
        "slug": "leg-baden",
        "moderation_status": "pending",
    }
