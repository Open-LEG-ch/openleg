# SPDX-License-Identifier: AGPL-3.0-or-later
"""Require installation authority for HTTP archive writes, including legacy data."""

import io
import json

import pytest

import community_archive
from tests import test_calculated_values_lifecycle_postgres as lifecycle
from tests.test_dashboard_access_routes import _set_session

interest_database = lifecycle.interest_database
calculated_client = lifecycle.calculated_client
pytestmark = pytest.mark.integration


@pytest.fixture(params=["current", "legacy"])
def archive_request(calculated_client, request):
    client, db, payload = calculated_client
    cid = payload["community_id"]
    _set_session(client, "member", "archive-csrf")
    exported = client.get(f"/leg/community/{cid}/archive")
    assert exported.status_code == 200
    archive = exported.data
    if request.param == "legacy":
        parsed = json.loads(archive)
        names = {table for table, _, _ in community_archive._LEGACY_DATASETS}
        parsed["manifest"]["schema_version"] = community_archive.LEGACY_SCHEMA_VERSION
        parsed["datasets"] = {
            name: rows for name, rows in parsed["datasets"].items() if name in names
        }
        parsed["manifest"]["hashes"] = {
            name: value
            for name, value in parsed["manifest"]["hashes"].items()
            if name in names
        }
        archive = json.dumps(parsed).encode()
    return client, db, cid, archive, exported.data


def _upload(
    client, cid, archive, *, token=None, endpoint="restore", csrf="archive-csrf"
):
    return client.post(
        f"/leg/community/{cid}/archive/{endpoint}",
        headers={"X-Admin-Token": token} if token is not None else {},
        data={
            "csrf_token": csrf,
            "platform_admin": "true",
            "is_admin": "true",
            "dry_run": "true",
            "archive": (io.BytesIO(archive), "archive.json"),
        },
    )


@pytest.mark.parametrize("token", [None, "forged-platform-token"])
def test_restore_write_requires_platform_admin(archive_request, token):
    client, _, cid, archive, before = archive_request
    response = _upload(client, cid, archive, token=token)
    assert response.status_code == 403
    assert client.get(f"/leg/community/{cid}/archive").data == before


def test_platform_admin_restores_with_leg_session_and_csrf(archive_request):
    client, _, cid, archive, before = archive_request
    response = _upload(client, cid, archive, token="synthetic-admin")
    assert response.status_code == 200
    assert response.get_json() == {
        "valid": True,
        "errors": [],
        "conflicts": [],
        "restored": True,
    }
    assert client.get(f"/leg/community/{cid}/archive").data == before


def test_dry_run_keeps_leg_admin_read_only_access(archive_request):
    client, _, cid, archive, before = archive_request
    response = _upload(client, cid, archive, endpoint="dry-run")
    assert response.status_code == 200
    assert response.get_json() == {"valid": True, "errors": [], "conflicts": []}
    assert client.get(f"/leg/community/{cid}/archive").data == before


@pytest.mark.parametrize("missing", ["session", "csrf", "role"])
def test_platform_token_preserves_leg_session_csrf_and_role(archive_request, missing):
    client, db, cid, archive, before = archive_request
    if missing == "session":
        with client.session_transaction() as state:
            state.clear()
        expected = 401
    elif missing == "csrf":
        expected = 400
    else:
        with db.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE community_members SET role = 'member' WHERE community_id = %s",
                (cid,),
            )
        expected = 403
    response = _upload(
        client,
        cid,
        archive,
        token="synthetic-admin",
        csrf="wrong-csrf" if missing == "csrf" else "archive-csrf",
    )
    assert response.status_code == expected
    if missing != "role":
        _set_session(client, "member", "archive-csrf")
        assert client.get(f"/leg/community/{cid}/archive").data == before


def test_restore_fails_closed_without_configured_platform_token(
    archive_request, monkeypatch
):
    client, _, cid, archive, before = archive_request
    monkeypatch.delenv("ADMIN_TOKEN")
    response = _upload(client, cid, archive, token="synthetic-admin")
    assert response.status_code == 404
    assert client.get(f"/leg/community/{cid}/archive").data == before
