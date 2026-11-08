# SPDX-License-Identifier: AGPL-3.0-or-later
"""A fictional registration through the ordinary product and captured inbox."""

import html
import json
import re
import signal
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from uuid import uuid4

import psycopg2
import pytest
from psycopg2.extensions import make_dsn

import action_token
from sandbox_seed import SEED_VERSION
from tests import test_sandbox_http
from tests.test_sandbox_personas import _reload_worker, _start, _switch

village = test_sandbox_http.village
_csrf = test_sandbox_http._csrf
pytestmark = pytest.mark.integration


@pytest.mark.parametrize("missing_fixture", [False, True])
def test_missing_original_identity_proofs_can_be_recovered_only_by_gateway_reset(
    village, missing_fixture
):
    client = village.test_client()
    prefix, _ = _start(client, "resident")
    manager = village.extensions["villages"]
    workspace = manager.workspaces[prefix.rsplit("/", 1)[1]]
    manifest = workspace["directory"] / "fixture.json"
    if missing_fixture:
        manifest.unlink()
    else:
        fixture = json.loads(manifest.read_text())
        del fixture["household_identities"]
        manifest.write_text(json.dumps(fixture))
    workspace["process"].send_signal(signal.SIGHUP)
    assert workspace["process"].wait(timeout=20) != 0
    if missing_fixture:
        assert not manifest.exists()
    else:
        assert "household_identities" not in json.loads(manifest.read_text())

    management = client.get(prefix + "/sandbox/manage")
    assert management.status_code == 200
    reset = client.post(
        prefix + "/sandbox/reset",
        data={
            "csrf_token": _csrf(management),
            "confirm": "reset",
            "seed_version": SEED_VERSION,
        },
    )
    assert reset.status_code == 302
    fresh = reset.location.removesuffix("/sandbox/enter")
    assert fresh != prefix
    assert client.get(reset.location, follow_redirects=True).status_code == 200
    profile = client.get(fresh + "/dashboard/export").json
    assert profile["building_id"] == "sonnwil-resident"
    fresh_workspace = manager.workspaces[fresh.rsplit("/", 1)[1]]
    identities = json.loads(
        (fresh_workspace["directory"] / "fixture.json").read_text()
    )["household_identities"]
    with (
        _workspace_connection(village, client) as connection,
        connection.cursor() as cur,
    ):
        cur.execute(
            "SELECT referral_code FROM buildings WHERE building_id=%s",
            (profile["building_id"],),
        )
        assert identities[profile["building_id"]] == cur.fetchone()[0]
    assert client.get(prefix + "/dashboard/export").status_code == 410
    assert not workspace["directory"].exists()


def _mail_link(client, prefix, path):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        inbox = client.get(prefix + "/sandbox/inbox")
        match = re.search(r'href="(' + prefix + path + r'[^"?]+)', inbox.text)
        if match:
            return html.unescape(match.group(1))
        time.sleep(0.05)
    pytest.fail("the captured inbox did not receive the expected link")


def test_new_household_uses_offline_defaults_and_normal_confirmation_login(village):
    first, other = village.test_client(), village.test_client()
    prefix, resident = _start(first, "resident")
    other_prefix, _ = _start(other, "resident")
    form = first.get(prefix + "/sandbox/onboarding")
    assert form.status_code == 200
    assert 'name="email"' in form.text
    assert 'value="sonnwil-new@example.org"' in form.text
    assert "Musterweg 7" in form.text
    assert "Offline" in form.text
    assert "onboarding.js" in form.text
    registration_id = re.search(
        r'data-building-id="(sonnwil-new-[a-f0-9]{16})"', form.text
    ).group(1)
    assert (
        first.post(
            prefix + "/dashboard/logout", data={"csrf_token": _csrf(resident)}
        ).status_code
        == 302
    )
    response = first.post(
        prefix + "/api/register_full",
        json={
            "email": "sonnwil-new@example.org",
            "profile": {
                "building_id": registration_id,
                "address": "Musterweg 7, Sonnwil",
                "annual_consumption_kwh": 4200,
                "lat": 47.1007,
                "lon": 8.1007,
                "bfs_number": 999999,
                "municipality_name": "Sonnwil (fiktiv)",
                "plz": "0000",
                "canton": "ZH",
            },
            "consents": {"share_with_neighbors": False},
        },
    )
    assert response.status_code == 200, response.text
    assert response.json["verification_email_sent"] is True
    confirmation = _mail_link(first, prefix, "/confirm/")
    inbox = first.get(prefix + "/sandbox/inbox")
    assert 'action="' + prefix + '/dashboard/access/request"' in inbox.text
    assert 'name="email" value="sonnwil-new@example.org"' in inbox.text
    assert confirmation not in other.get(other_prefix + "/sandbox/inbox").text
    assert other.get(confirmation.replace(prefix, other_prefix)).status_code == 404
    assert first.get(prefix + "/dashboard/export").status_code == 401
    assert first.get(confirmation).status_code == 200
    assert first.get(confirmation).status_code == 404
    assert first.get(prefix + "/dashboard/export").status_code == 401
    assert (
        first.post(
            prefix + "/dashboard/access/request",
            data={"email": "sonnwil-new@example.org"},
        ).status_code
        == 200
    )
    access = _mail_link(first, prefix, "/dashboard/access/")
    assert other.get(access.replace(prefix, other_prefix)).location.endswith(
        "access=invalid"
    )
    assert first.get(access).location == prefix + "/dashboard"
    assert first.get(access).location.endswith("access=invalid")
    profile = first.get(prefix + "/dashboard/export")
    assert profile.json["building_id"] == registration_id
    assert profile.json["annual_consumption_kwh"] == 4200
    assert profile.json["share_with_neighbors"] is False
    home = first.get(prefix + "/sandbox/home")
    assert "Musterweg 7" in home.text
    assert "sonnwil-new@example.org" in home.text
    assert "abgemeldet" not in home.text

    dashboard = first.get(prefix + "/dashboard")
    assert (
        first.post(
            prefix + "/dashboard/profile",
            data={
                "csrf_token": _csrf(dashboard),
                "annual_consumption_kwh": "7200",
                "potential_pv_kwp": "8",
                "share_with_neighbors": "on",
            },
        ).status_code
        == 302
    )
    saved = first.get(prefix + "/dashboard/export").json
    assert saved["annual_consumption_kwh"] == 7200
    assert saved["share_with_neighbors"] is True
    calculation = first.post(
        prefix + "/api/calculate_savings",
        json={
            "consumption_kwh": saved["annual_consumption_kwh"],
            "has_solar": True,
            "pv_kwp": 8,
        },
    )
    assert calculation.status_code == 200
    assert calculation.json["annual_savings_chf"] >= 0
    assert calculation.json["assumptions"]["community_size"] == 5
    contacts = first.get(prefix + "/dashboard/contacts")
    candidates = re.findall(r'name="candidate" value="([^"]+)"', contacts.text)
    assert len(candidates) == 5  # Five opted-in seeds; the private seed is absent.
    stale_candidate = candidates[0]
    resident = _switch(first, prefix, "resident")
    assert _neighbor_count(resident) == 6
    assert (
        len(
            re.findall(
                r'name="candidate"', first.get(prefix + "/dashboard/contacts").text
            )
        )
        == 5
    )

    assert (
        first.post(
            prefix + "/dashboard/access/request",
            data={"email": "sonnwil-new@example.org"},
        ).status_code
        == 200
    )
    assert (
        first.get(_mail_link(first, prefix, "/dashboard/access/")).location
        == prefix + "/dashboard"
    )
    dashboard = first.get(prefix + "/dashboard")
    assert (
        first.post(
            prefix + "/dashboard/profile",
            data={
                "csrf_token": _csrf(dashboard),
                "annual_consumption_kwh": "7200",
                "potential_pv_kwp": "8",
            },
        ).status_code
        == 302
    )
    dashboard = first.get(prefix + "/dashboard")
    assert (
        first.post(
            prefix + "/dashboard/contacts",
            data={
                "csrf_token": _csrf(dashboard),
                "candidate": stale_candidate,
                "share_email": "yes",
            },
        ).status_code
        == 403
    )
    resident = _switch(first, prefix, "resident")
    assert _neighbor_count(resident) == 5
    assert (
        len(
            re.findall(
                r'name="candidate"', first.get(prefix + "/dashboard/contacts").text
            )
        )
        == 4
    )
    assert _neighbor_count(other.get(other_prefix + "/dashboard")) == 5


def _neighbor_count(page):
    return int(re.search(r"<strong data-statistic[^>]*>(\d+) ", page.text).group(1))


def _request_contact(client, prefix):
    page = client.get(prefix + "/dashboard/contacts")
    candidate = re.search(r'name="candidate" value="([^"]+)"', page.text).group(1)
    data = {"csrf_token": _csrf(page), "candidate": candidate}
    assert client.post(prefix + "/dashboard/contacts", data=data).status_code == 403
    assert (
        client.post(
            prefix + "/dashboard/contacts", data={**data, "share_email": "yes"}
        ).status_code
        == 302
    )
    page = client.get(prefix + "/dashboard/contacts")
    request_id = re.search(r"/contacts/([a-f0-9-]+)/revoke", page.text).group(1)
    return request_id, page


def _find_incoming(client, prefix, request_id):
    accounts = {
        "producer": "sonnwil-producer@example.org",
        "founder": "sonnwil-founder@example.org",
        "administrator": "sonnwil-neighbour@example.org",
        "operator": "sonnwil-operator@example.org",
    }
    for role, email in accounts.items():
        _switch(client, prefix, role)
        page = client.get(prefix + "/dashboard/contacts")
        if f"/contacts/{request_id}/accept" in page.text:
            return role, email, page
    pytest.fail("no consenting fictional household received the contact request")


def test_contact_details_require_both_grants_and_can_be_revoked_or_declined(village):
    client = village.test_client()
    prefix, _ = _start(client, "resident")
    request_id, owner_page = _request_contact(client, prefix)
    path = prefix + f"/dashboard/contacts/{request_id}"
    assert (
        client.post(
            path + "/accept",
            data={"csrf_token": _csrf(owner_page), "share_email": "yes"},
        ).status_code
        == 403
    )
    role, peer_email, incoming = _find_incoming(client, prefix, request_id)
    assert peer_email not in owner_page.text
    assert "sonnwil-resident@example.org" not in incoming.text
    assert (
        client.post(path + "/accept", data={"csrf_token": _csrf(incoming)}).status_code
        == 403
    )
    assert (
        client.post(
            path + "/accept", data={"csrf_token": _csrf(incoming), "share_email": "yes"}
        ).status_code
        == 302
    )
    assert (
        "sonnwil-resident@example.org"
        in client.get(prefix + "/dashboard/contacts").text
    )
    _switch(client, prefix, "resident")
    shared = client.get(prefix + "/dashboard/contacts")
    assert peer_email in shared.text
    assert (
        client.post(path + "/revoke", data={"csrf_token": _csrf(shared)}).status_code
        == 302
    )
    assert peer_email not in client.get(prefix + "/dashboard/contacts").text
    _switch(client, prefix, role)
    assert (
        "sonnwil-resident@example.org"
        not in client.get(prefix + "/dashboard/contacts").text
    )

    _switch(client, prefix, "resident")
    declined_id, owner_page = _request_contact(client, prefix)
    assert declined_id != request_id
    _role, peer_email, incoming = _find_incoming(client, prefix, declined_id)
    path = prefix + f"/dashboard/contacts/{declined_id}"
    assert (
        client.post(path + "/decline", data={"csrf_token": _csrf(incoming)}).status_code
        == 302
    )
    declined = client.get(prefix + "/dashboard/contacts")
    assert "Abgelehnt" in declined.text
    assert "sonnwil-resident@example.org" not in declined.text
    assert (
        client.post(
            path + "/accept", data={"csrf_token": _csrf(declined), "share_email": "yes"}
        ).status_code
        == 403
    )
    _switch(client, prefix, "resident")
    assert peer_email not in client.get(prefix + "/dashboard/contacts").text


def test_profile_deletion_retires_copied_sessions_pending_links_and_demo_login(village):
    client = village.test_client()
    prefix, _ = _start(client, "resident")
    exported = client.get(prefix + "/dashboard/export")
    assert exported.status_code == 200
    old_cookie = client.get_cookie("openleg_product", path=prefix).value
    assert (
        client.post(
            prefix + "/dashboard/access/request",
            data={"email": "sonnwil-resident@example.org"},
        ).status_code
        == 200
    )
    pending_access = _mail_link(client, prefix, "/dashboard/access/")
    assert (
        client.post(
            prefix + "/unsubscribe", data={"email": "sonnwil-resident@example.org"}
        ).status_code
        == 200
    )
    deletion = _mail_link(client, prefix, "/unsubscribe/")
    assert client.get(deletion).status_code == 200
    assert client.get(prefix + "/dashboard/export").status_code == 200
    deleted = client.post(deletion)
    assert deleted.status_code == 200
    assert "erfolgreich gelöscht" in deleted.text
    assert client.get(prefix + "/dashboard/export").status_code == 401
    client.set_cookie("openleg_product", old_cookie, path=prefix)
    assert client.get(prefix + "/dashboard/export").status_code == 401
    assert client.get(pending_access).location.endswith("access=invalid")
    assert client.post(deletion).status_code == 404
    chooser = client.get(prefix + "/sandbox/accounts")
    assert (
        client.post(
            prefix + "/sandbox/persona",
            data={"csrf_token": _csrf(chooser), "persona": "resident"},
        ).status_code
        == 410
    )
    assert exported.json["_simulation"] == {"village": "Sonnwil", "synthetic": True}
    assert "simulation-sonnwil" in exported.headers["Content-Disposition"]


@contextmanager
def _workspace_connection(gateway, client):
    with client.session_transaction() as state:
        manager = gateway.extensions["villages"]
        name = manager.workspaces[state["workspace"]]["name"]
    connection = psycopg2.connect(make_dsn(manager.postgres.dsn, dbname=name))
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def test_invalid_deletion_links_do_not_retire_the_current_household_login(village):
    owner, anonymous = village.test_client(), village.test_client()
    prefix, _ = _start(owner, "resident")
    copied_cookie = owner.get_cookie("openleg_product", path=prefix).value
    anonymous.set_cookie("openleg_sandbox", owner.get_cookie("openleg_sandbox").value)
    tokens = [str(uuid4()) for _ in range(4)]
    with (
        _workspace_connection(village, owner) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            """INSERT INTO tokens(token,building_id,token_type,expires_at,used_at) VALUES
            (%s,'sonnwil-resident','unsubscribe',CURRENT_TIMESTAMP-INTERVAL '1 second',NULL),
            (%s,'sonnwil-resident','unsubscribe',CURRENT_TIMESTAMP+INTERVAL '1 hour',CURRENT_TIMESTAMP),
            (%s,'sonnwil-resident','verify',CURRENT_TIMESTAMP+INTERVAL '1 hour',NULL)""",
            tuple(action_token.digest(token) for token in tokens[:3]),
        )
    for token in ["not-a-uuid", *tokens]:
        assert anonymous.post(prefix + "/unsubscribe/" + token).status_code == 404
        owner.set_cookie("openleg_product", copied_cookie, path=prefix)
        assert owner.get(prefix + "/dashboard/export").status_code == 200


@pytest.mark.parametrize("wait_for_token_lock", [False, True])
def test_mailbox_deletion_retires_cookies_before_the_household_can_be_recreated(
    village, wait_for_token_lock
):
    owner, anonymous = village.test_client(), village.test_client()
    prefix, _ = _start(owner, "resident")
    copied_cookie = owner.get_cookie("openleg_product", path=prefix).value
    anonymous.set_cookie("openleg_sandbox", owner.get_cookie("openleg_sandbox").value)
    assert (
        owner.post(
            prefix + "/unsubscribe", data={"email": "sonnwil-resident@example.org"}
        ).status_code
        == 200
    )
    deletion = _mail_link(owner, prefix, "/unsubscribe/")
    if wait_for_token_lock:
        _delete_while_token_is_locked(village, anonymous, deletion)
    else:
        assert anonymous.post(deletion).status_code == 200
    # No authenticated read occurs between deletion and a complete new signup.
    assert (
        anonymous.post(
            prefix + "/api/register_full",
            json={
                "email": "replacement@example.org",
                "profile": {
                    "building_id": "sonnwil-resident",
                    "address": "Musterweg 9, Sonnwil",
                    "lat": 47.1009,
                    "lon": 8.1009,
                    "annual_consumption_kwh": 9000,
                    "potential_pv_kwp": 0,
                    "bfs_number": 999999,
                },
            },
        ).status_code
        == 200
    )
    assert anonymous.get(_mail_link(anonymous, prefix, "/confirm/")).status_code == 200
    owner.set_cookie("openleg_product", copied_cookie, path=prefix)
    assert owner.get(prefix + "/dashboard/export").status_code == 401

    accounts = anonymous.get(prefix + "/sandbox/accounts")
    assert (
        anonymous.post(
            prefix + "/sandbox/persona",
            data={"csrf_token": _csrf(accounts), "persona": "resident"},
        ).status_code
        == 410
    )
    assert 'name="persona" value="resident"' not in accounts.text
    assert anonymous.get(prefix + "/dashboard/export").status_code == 401

    # The replacement can still use its own ordinary access link. It must not
    # inherit the original fictional persona merely because its ID was reused.
    assert (
        anonymous.post(
            prefix + "/dashboard/access/request",
            data={"email": "replacement@example.org"},
        ).status_code
        == 200
    )
    assert (
        anonymous.get(
            _mail_link(anonymous, prefix, "/dashboard/access/"), follow_redirects=True
        ).status_code
        == 200
    )
    home = anonymous.get(prefix + "/sandbox/home")
    assert "replacement@example.org" in home.text
    assert "Ihr neuer Haushalt ist bestätigt." in home.text
    assert "Lea Muster" not in home.text
    _reload_worker(village, prefix)
    accounts = anonymous.get(prefix + "/sandbox/accounts")
    assert 'name="persona" value="resident"' not in accounts.text
    assert (
        anonymous.post(
            prefix + "/sandbox/persona",
            data={"csrf_token": _csrf(accounts), "persona": "resident"},
        ).status_code
        == 410
    )
    assert (
        anonymous.get(prefix + "/dashboard/export").json["email"]
        == "replacement@example.org"
    )
    assert (
        "Ihr neuer Haushalt ist bestätigt."
        in anonymous.get(prefix + "/sandbox/home").text
    )


def _delete_while_token_is_locked(gateway, client, deletion):
    with client.session_transaction() as state:
        manager = gateway.extensions["villages"]
        nonce = (
            manager.workspaces[state["workspace"]]["directory"] / ".session-generation"
        )
    previous = nonce.read_text()
    with _workspace_connection(gateway, client) as blocker:
        with blocker.cursor() as cursor:
            cursor.execute(
                "SELECT token FROM tokens WHERE token = %s FOR UPDATE",
                (action_token.digest(deletion.rsplit("/", 1)[1]),),
            )
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(client.post, deletion)
            try:
                with _workspace_connection(gateway, client) as probe:
                    probe.autocommit = True
                    with probe.cursor() as cursor:
                        deadline = time.monotonic() + 5
                        while time.monotonic() < deadline:
                            cursor.execute("SELECT pg_stat_clear_snapshot()")
                            cursor.execute(
                                "SELECT 1 FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock' AND query LIKE '%FROM tokens%' AND query LIKE '%FOR UPDATE%'"
                            )
                            if cursor.fetchone():
                                break
                            time.sleep(0.02)
                        else:
                            pytest.fail(
                                "deletion did not reach its locked token after session validation"
                            )
                assert nonce.read_text() == previous
            finally:
                blocker.rollback()
            response = pending.result(timeout=10)
            assert response.status_code == 200
            assert nonce.read_text() != previous


def test_unavailable_login_binding_blocks_deletion_without_consuming_its_token(village):
    """A deterministic filesystem failure refuses deletion before any commit."""
    owner, anonymous = village.test_client(), village.test_client()
    prefix, _ = _start(owner, "resident")
    copied_cookie = owner.get_cookie("openleg_product", path=prefix).value
    anonymous.set_cookie("openleg_sandbox", owner.get_cookie("openleg_sandbox").value)
    assert (
        owner.post(
            prefix + "/unsubscribe", data={"email": "sonnwil-resident@example.org"}
        ).status_code
        == 200
    )
    deletion = _mail_link(owner, prefix, "/unsubscribe/")
    with owner.session_transaction() as state:
        workspace = village.extensions["villages"].workspaces[state["workspace"]]
    generation = workspace["directory"] / ".session-generation"
    backup = generation.with_suffix(".test-backup")
    generation.rename(backup)
    generation.mkdir(mode=0o700)
    try:
        assert anonymous.post(deletion).status_code == 503
        with (
            _workspace_connection(village, owner) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "SELECT verified FROM buildings WHERE building_id='sonnwil-resident'"
            )
            assert cursor.fetchone() == (True,)
            cursor.execute(
                "SELECT token FROM tokens WHERE token=%s",
                (action_token.digest(deletion.rsplit("/", 1)[1]),),
            )
            assert cursor.fetchone() is not None
    finally:
        generation.rmdir()
        backup.rename(generation)
    assert anonymous.post(deletion).status_code == 200
    owner.set_cookie("openleg_product", copied_cookie, path=prefix)
    assert owner.get(prefix + "/dashboard/export").status_code == 401


def test_expired_confirmation_and_access_links_require_fresh_captured_mail(village):
    client = village.test_client()
    prefix, resident = _start(client, "resident")
    assert (
        client.post(
            prefix + "/dashboard/logout", data={"csrf_token": _csrf(resident)}
        ).status_code
        == 302
    )
    payload = {
        "email": "sonnwil-expiring@example.org",
        "profile": {
            "building_id": "sonnwil-expiring",
            "address": "Musterweg 8, Sonnwil",
            "lat": 47.1008,
            "lon": 8.1008,
            "annual_consumption_kwh": 4000,
            "potential_pv_kwp": 0,
            "bfs_number": 999999,
        },
    }
    assert client.post(prefix + "/api/register_full", json=payload).status_code == 200
    expired = _mail_link(client, prefix, "/confirm/")
    with (
        _workspace_connection(village, client) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "UPDATE tokens SET expires_at = CURRENT_TIMESTAMP - INTERVAL '1 second' WHERE token = %s",
            (action_token.digest(expired.rsplit("/", 1)[1]),),
        )
    assert client.get(expired).status_code == 404
    assert client.get(prefix + "/dashboard/export").status_code == 401
    assert client.post(prefix + "/api/register_full", json=payload).status_code == 200
    fresh = _mail_link(client, prefix, "/confirm/")
    assert fresh != expired
    assert client.get(fresh).status_code == 200
    assert (
        client.post(
            prefix + "/dashboard/access/request", data={"email": payload["email"]}
        ).status_code
        == 200
    )
    expired_access = _mail_link(client, prefix, "/dashboard/access/")
    with (
        _workspace_connection(village, client) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "UPDATE dashboard_access_tokens SET expires_at = CURRENT_TIMESTAMP - INTERVAL '1 second' WHERE building_id = %s",
            (payload["profile"]["building_id"],),
        )
    assert client.get(expired_access).location.endswith("access=invalid")
    assert client.get(prefix + "/dashboard/export").status_code == 401
    assert (
        client.post(
            prefix + "/dashboard/access/request", data={"email": payload["email"]}
        ).status_code
        == 200
    )
    fresh_access = _mail_link(client, prefix, "/dashboard/access/")
    assert fresh_access != expired_access
    assert client.get(fresh_access).location == prefix + "/dashboard"
    assert (
        client.get(prefix + "/dashboard/export").json["building_id"]
        == "sonnwil-expiring"
    )
