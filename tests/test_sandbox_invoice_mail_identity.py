# SPDX-License-Identifier: AGPL-3.0-or-later
"""Captured invoice mail requires the original registration proof."""

import json

import database
import sandbox_mail


def test_legacy_and_replacement_invoice_mail_stays_hidden(tmp_path, monkeypatch):
    """Captured invoice mail requires the viewer's current original-registration proof."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENLEG_SANDBOX_DIRECTORY", str(tmp_path))
    monkeypatch.setenv("OPENLEG_SANDBOX_WORKER", "1")
    monkeypatch.setenv("APP_BASE_URL", "http://localhost/v/fictional")
    monkeypatch.setattr(
        database, "get_building", lambda _: {"referral_code": "original-registration"}
    )
    directory = tmp_path / "mail"
    directory.mkdir()
    for name, fields in (
        (
            "original",
            {"owner": "resident", "owner_registration_id": "original-registration"},
        ),
        ("legacy", {"owner": "resident"}),
        (
            "replacement",
            {"owner": "resident", "owner_registration_id": "replacement-registration"},
        ),
        (
            "foreign",
            {"owner": "neighbour", "owner_registration_id": "original-registration"},
        ),
        ("access", {}),
    ):
        (directory / (name + ".json")).write_text(json.dumps({"body": name, **fields}))
    assert {item["body"] for item in sandbox_mail.messages(viewer="resident")} == {
        "original",
        "access",
    }
    monkeypatch.setattr(
        database,
        "get_building",
        lambda _: {"referral_code": "replacement-registration"},
    )
    assert {item["body"] for item in sandbox_mail.messages(viewer="resident")} == {
        "replacement",
        "access",
    }
    monkeypatch.setattr(database, "get_building", lambda _: None)
    assert {item["body"] for item in sandbox_mail.messages(viewer="resident")} == {
        "access"
    }
