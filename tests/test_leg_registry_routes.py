# SPDX-License-Identifier: AGPL-3.0-or-later
"""Registry API and verification operation tests."""

import importlib
import os
from unittest.mock import MagicMock, patch

import leg_registry

SAMPLE_ENTRY = {
    "id": 7,
    "name": "LEG Baden",
    "contact_email": "info@example.ch",
    "bfs_number": 4021,
    "vnb_name": "Regionalwerke Baden",
}


def _app_client():
    with (
        patch.dict(
            os.environ,
            {
                "DATABASE_URL": "postgresql://x:x@localhost/x",
                "REDIS_URL": "memory://",
                "APP_BASE_URL": "http://localhost:5003",
                "CRON_SECRET": "test-cron-secret",
            },
        ),
        patch("database.is_db_available", return_value=True),
        patch("database._connection_pool", MagicMock()),
    ):
        import app as app_module

        app_module = importlib.reload(app_module)
        return app_module, app_module.create_app(load_environment=False).test_client()


def test_registry_verification_get_only_renders_confirmation(monkeypatch):
    app_module, client = _app_client()
    monkeypatch.setattr(
        app_module.db,
        "get_registry_entry_by_verification_token",
        MagicMock(return_value=SAMPLE_ENTRY),
    )
    mark = MagicMock(return_value=True)
    monkeypatch.setattr(app_module.db, "mark_registry_entry_verified", mark)

    response = client.get("/registry/verify/validtoken")

    assert response.status_code == 200
    assert 'method="post"' in response.get_data(as_text=True)
    assert 'name="csrf_token"' in response.get_data(as_text=True)
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    mark.assert_not_called()


def test_registry_verification_post_requires_csrf_and_marks_entry(monkeypatch):
    app_module, client = _app_client()
    monkeypatch.setattr(
        app_module.db,
        "get_registry_entry_by_verification_token",
        MagicMock(return_value=SAMPLE_ENTRY),
    )
    mark = MagicMock(return_value=True)
    monkeypatch.setattr(app_module.db, "mark_registry_entry_verified", mark)

    confirmation = client.get("/registry/verify/validtoken").get_data(as_text=True)
    with client.session_transaction() as state:
        csrf_token = state["registry_verification_csrf_token"]
    assert csrf_token in confirmation

    assert client.post("/registry/verify/validtoken").status_code == 400
    assert (
        client.post(
            "/registry/verify/validtoken", data={"csrf_token": "incorrect"}
        ).status_code
        == 400
    )
    response = client.post(
        "/registry/verify/validtoken", data={"csrf_token": csrf_token}
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/?registry=verified")
    mark.assert_called_once_with(7)


def test_registry_verification_action_rejects_unknown_token(monkeypatch):
    app_module, client = _app_client()
    monkeypatch.setattr(
        app_module.db,
        "get_registry_entry_by_verification_token",
        MagicMock(return_value=None),
    )

    assert client.get("/registry/verify/badtoken").status_code == 404


def test_send_verification_nudges_uses_product_action_url(monkeypatch):
    monkeypatch.setattr(
        leg_registry.db,
        "get_registry_entries_needing_verification",
        MagicMock(return_value=[SAMPLE_ENTRY]),
    )
    monkeypatch.setattr(
        leg_registry.db,
        "set_registry_verification_token",
        MagicMock(return_value=True),
    )
    send = MagicMock(return_value=True)
    monkeypatch.setattr(leg_registry.email_utils, "send_email", send)

    result = leg_registry.send_verification_nudges("https://app.openleg.ch")

    assert result == {"candidates": 1, "sent": 1, "errors": 0}
    assert "/registry/verify/" in send.call_args.args[2]
    assert "/leg-verzeichnis/" not in send.call_args.args[2]


def test_send_verification_nudges_counts_token_errors(monkeypatch):
    monkeypatch.setattr(
        leg_registry.db,
        "get_registry_entries_needing_verification",
        MagicMock(return_value=[SAMPLE_ENTRY]),
    )
    monkeypatch.setattr(
        leg_registry.db,
        "set_registry_verification_token",
        MagicMock(return_value=False),
    )
    send = MagicMock()
    monkeypatch.setattr(leg_registry.email_utils, "send_email", send)

    assert leg_registry.send_verification_nudges() == {
        "candidates": 1,
        "sent": 0,
        "errors": 1,
    }
    send.assert_not_called()


def test_annotate_vnb_plausibility_is_only_a_moderator_hint(monkeypatch):
    tariffs = MagicMock(return_value=[{"operator_name": "Regionalwerke Baden AG"}])
    monkeypatch.setattr(leg_registry.db, "get_elcom_tariffs", tariffs)

    plausible = leg_registry.annotate_vnb_plausibility([SAMPLE_ENTRY])[0]
    absent = leg_registry.annotate_vnb_plausibility(
        [{**SAMPLE_ENTRY, "bfs_number": None}]
    )[0]

    assert plausible["vnb_plausible"] is True
    assert absent["vnb_plausible"] is None


def test_registry_verification_cron_requires_secret():
    _app_module, client = _app_client()

    assert client.post("/api/cron/verify-registry-entries").status_code == 403


def test_registry_verification_cron_calls_job():
    _app_module, client = _app_client()
    with patch(
        "leg_registry.send_verification_nudges",
        return_value={"candidates": 0, "sent": 0, "errors": 0},
    ) as job:
        response = client.post(
            "/api/cron/verify-registry-entries",
            headers={"X-Cron-Secret": "test-cron-secret"},
        )

    assert response.status_code == 200
    job.assert_called_once()
