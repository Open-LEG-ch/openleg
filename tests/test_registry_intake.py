# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared registry-intake contract."""

from unittest.mock import MagicMock

import registry_intake


def test_submit_normalizes_and_persists_once(monkeypatch):
    save = MagicMock(return_value={"id": 1})
    monkeypatch.setattr(registry_intake.db, "save_registry_entry", save)
    monkeypatch.setattr(
        registry_intake.db, "get_registry_entry_by_slug", MagicMock(return_value=None)
    )
    track = MagicMock()
    monkeypatch.setattr(registry_intake.db, "track_event", track)

    result = registry_intake.submit(
        {
            "name": "  LEG Müswangen-Süd ",
            "contact_email": " INFO@example.ch ",
            "kanton": "zh",
            "member_count_estimate": "4",
            "leg_status": "aktiv",
        },
        source="self_hosted",
    )

    assert result == {
        "error": None,
        "slug": "leg-mueswangen-sued",
        "moderation_status": "pending",
    }
    save.assert_called_once()
    assert save.call_args.kwargs["contact_email"] == "info@example.ch"
    assert save.call_args.kwargs["kanton"] == "ZH"
    assert save.call_args.kwargs["member_count_estimate"] == 4
    assert save.call_args.kwargs["source"] == "self_hosted"
    track.assert_called_once()


def test_submit_rejects_non_http_website_url(monkeypatch):
    save = MagicMock()
    monkeypatch.setattr(registry_intake.db, "save_registry_entry", save)

    result = registry_intake.submit(
        {
            "name": "LEG Baden",
            "contact_email": "info@example.ch",
            "website_url": "javascript:alert(1)",
        },
        source="self_hosted",
    )

    assert result == {
        "error": "Website-URL muss mit http:// oder https:// beginnen.",
        "status": 400,
    }
    save.assert_not_called()
