# SPDX-License-Identifier: AGPL-3.0-or-later
"""Private equipment through owner HTTP sessions and real PostgreSQL."""

import os

import pytest

import database as db
from tests import test_dashboard_access_routes, test_interest_postgres
from tests.test_app_organic_routes import _disable_rate_limit_hooks

interest_database = test_interest_postgres.interest_database
pytestmark = pytest.mark.integration
BASE = "/api/equipment/v1/records"
HEADERS = {"X-CSRF-Token": "csrf-secret"}
SOURCE_URL = "https://www.bacs.admin.ch/en/device-security"


@pytest.fixture
def app_module(monkeypatch):
    # An empty mapping pins even optional/unknown future settings; retain only
    # the explicit integration-test database rather than ambient app config.
    monkeypatch.setattr(
        os, "environ", {"DATABASE_URL": os.environ.get("DATABASE_URL", "")}
    )
    import app as imported_app

    web = imported_app.create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "equipment-test-key",
            "APP_BASE_URL": "http://localhost:5003",
            "PUBLIC_SITE_URL": "https://openleg.ch",
            "MATOMO_SITE_ID": "",
            "SESSION_COOKIE_SECURE": False,
            "RATELIMIT_STORAGE_URI": "memory://",
        },
        load_environment=False,
        check_database=False,
    )
    hooks = _disable_rate_limit_hooks(web)
    monkeypatch.setattr(imported_app, "web", web, raising=False)
    try:
        yield imported_app
    finally:
        web.before_request_funcs[None] = hooks


def evidence(kind="owner_report"):
    return {"kind": kind, "reviewed_on": "2026-01-02", "source_url": SOURCE_URL}


@pytest.fixture
def equipment_client(interest_database, app_module):
    for actor in ("owner", "other", "unverified"):
        assert db.save_building(
            actor,
            f"{actor}@example.ch",
            {"address": "Synthetic street", "lat": 47, "lon": 8},
            {"share_with_neighbors": True},
            verified=actor != "unverified",
        )
    client = app_module.web.test_client()
    test_dashboard_access_routes._set_session(client, "owner")
    return client


def test_owner_without_community_or_telemetry_can_record_unknown_equipment(
    equipment_client,
):
    client = equipment_client
    response = client.post(
        BASE, json={"manufacturer": "Example", "model": "Roof 1"}, headers=HEADERS
    )
    assert response.status_code == 201, response.text
    record = response.json["record"]
    assert record["manufacturer"] == "Example"
    assert record["firmware"] is None
    assert record["rated_ac_kw"] is None
    assert record["cloud_dependency"] == "unknown"
    assert record["backup"]["capability"] == "unknown"
    assert record["vendor_cloud_review"]["status"] == "unknown"
    assert all(task["status"] == "unknown" for task in record["checklist"].values())
    assert client.get(f"{BASE}/{record['id']}").json["record"] == record
    assert len(client.get(BASE).json["records"]) == 1


def test_owner_records_provenance_model_applicability_cloud_and_backup_separately(
    equipment_client,
):
    payload = {
        "manufacturer": "Example",
        "model": "Roof 1",
        "firmware": "1.2",
        "logger": "Logger 2",
        "rated_ac_kw": "8.25",
        "pv_dc_kwp": "10.2",
        "cloud_dependency": "required",
        "installer_access": "active",
        "serial_number": "PRIVATE-SERIAL",
        "network_details": "192.168.1.4",
        "private_notes": "Private maintenance discussion",
        "installer_users": "PRIVATE-USER",
        "evidence": evidence("device_observation"),
        "checklist": {
            "account_mfa": {
                "applicability": "not_supported",
                "status": "open",
                "evidence": evidence(),
            }
        },
        "vendor_cloud_review": {
            "status": "reviewed",
            "remote_access": "possible",
            "evidence": evidence(),
        },
        "backup": {
            "capability": "available",
            "installer_evidence": evidence("installer_evidence"),
        },
    }
    response = equipment_client.post(BASE, json=payload, headers=HEADERS)
    assert response.status_code == 201, response.text
    record = response.json["record"]
    assert record["rated_ac_kw"] == "8.25"
    assert record["pv_dc_kwp"] == "10.2"
    assert record["evidence"] == evidence("device_observation")
    assert record["checklist"]["account_mfa"]["applicability"] == "not_supported"
    assert record["checklist"]["account_mfa"]["evidence"]["kind"] == "owner_report"
    assert record["vendor_cloud_review"]["remote_access"] == "possible"
    assert record["backup"]["installer_evidence"]["kind"] == "installer_evidence"
    assert "backup_ready" not in record
    assert record["serial_number"] == "PRIVATE-SERIAL"


@pytest.mark.parametrize(
    "invalid",
    [
        {"evidence": {"kind": "installer_evidence", "reviewed_on": None}},
        {"evidence": {"kind": "owner_report", "reviewed_on": "2026-02-30"}},
        {
            "evidence": {
                "kind": "owner_report",
                "reviewed_on": "9999-12-31",
            }
        },
        {"evidence": {"kind": "unknown", "reviewed_on": "2026-01-02"}},
        {"evidence": {"kind": "openleg_verified", "reviewed_on": "2026-01-02"}},
        {"checklist": {"default_passwords": {"status": "done"}}},
        {
            "checklist": {
                "account_mfa": {
                    "status": "done",
                    "applicability": "not_supported",
                    "evidence": evidence(),
                }
            }
        },
        {"checklist": {"made_up_check": {}}},
        {"vendor_cloud_review": {"status": "reviewed", "remote_access": "disabled"}},
        {"backup": {"installer_evidence": evidence("owner_report")}},
        {"rated_ac_kw": True},
        {"pv_dc_kwp": "NaN"},
        {"rated_ac_kw": "-1"},
        {"manufacturer": "x" * 121},
        {"serial_number": {"bad": "shape"}},
        {"auto_scan": True},
    ],
)
def test_invalid_or_undated_assertions_cannot_be_saved(equipment_client, invalid):
    response = equipment_client.post(
        BASE,
        json={"manufacturer": "Example", "model": "Roof 1", **invalid},
        headers=HEADERS,
    )
    assert response.status_code == 400, response.text
    assert equipment_client.get(BASE).json == {"records": []}


def test_owner_edits_and_deletes_private_checklist_evidence(equipment_client):
    client = equipment_client
    created = client.post(BASE, json={}, headers=HEADERS).json["record"]
    path = f"{BASE}/{created['id']}"
    update = {
        "manufacturer": "Example",
        "model": "Roof 2",
        "private_notes": "PRIVATE",
        "checklist": {
            "default_passwords": {
                "applicability": "applicable",
                "status": "done",
                "evidence": evidence(),
            }
        },
    }
    response = client.put(path, json=update, headers=HEADERS)
    assert response.status_code == 200, response.text
    assert (
        client.get(path).json["record"]["checklist"]["default_passwords"]["status"]
        == "done"
    )
    assert client.delete(path, headers=HEADERS).status_code == 200
    assert client.get(path).status_code == 404
    assert client.get(BASE).json == {"records": []}


def test_only_current_verified_owner_can_read_or_change_equipment(equipment_client):
    client = equipment_client
    record = client.post(
        BASE, json={"serial_number": "PRIVATE-SERIAL"}, headers=HEADERS
    ).json["record"]
    path = f"{BASE}/{record['id']}"
    for method in (client.put, client.delete):
        assert method(path, json={}).status_code == 403
    for actor, status in (("other", 404), ("unverified", 403)):
        test_dashboard_access_routes._set_session(client, actor)
        for method in (client.get, client.put, client.delete):
            response = method(path, json={}, headers=HEADERS)
            assert response.status_code == status
            assert "PRIVATE-SERIAL" not in response.text
        assert client.get(BASE).json == (
            {"records": []}
            if actor == "other"
            else {"error": "verified_owner_required"}
        )
    with client.session_transaction() as state:
        state.clear()
    for method in (client.get, client.put, client.delete):
        response = method(path, json={}, headers=HEADERS)
        assert response.status_code == 401
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["Referrer-Policy"] == "no-referrer"
    test_dashboard_access_routes._set_session(client, "owner")
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE buildings SET verified=FALSE WHERE building_id='owner'")
    assert client.get(path).status_code == 403


def test_french_handoff_translates_labels_and_keeps_private_fields_out(
    equipment_client,
):
    client = equipment_client
    record = client.post(
        BASE,
        json={
            "manufacturer": "Synthetic",
            "model": "Unit",
            "serial_number": "PRIVATE-FRENCH-SERIAL",
            "network_details": "PRIVATE-FRENCH-NETWORK",
            "private_notes": "PRIVATE-FRENCH-NOTE",
        },
        headers=HEADERS,
    ).json["record"]
    response = client.post(f"{BASE}/{record['id']}/handoff?lang=fr-CH", headers=HEADERS)
    assert response.status_code == 200
    assert response.json["schema_version"] == "equipment-handoff/1"
    assert "OpenLEG ne vérifie pas" in response.json["notice"]
    assert "Mises à jour et assistance" in [
        item["label"] for item in response.json["findings"]
    ]
    assert all("PRIVATE-FRENCH" not in str(value) for value in response.json.values())
    assert "no-store" in response.headers["Cache-Control"]
    assert {item["check"] for item in response.json["findings"]} == set(
        record["checklist"]
    )


def test_handoff_is_explicit_owner_download_and_redacts_private_fields(
    equipment_client,
):
    client = equipment_client
    payload = {
        "manufacturer": "Example",
        "model": "Roof 1",
        "serial_number": "PRIVATE-SERIAL",
        "network_details": "PRIVATE-NETWORK",
        "private_notes": "PRIVATE-NOTE",
        "installer_users": "PRIVATE-USER",
        "checklist": {
            "update_support": {
                "applicability": "applicable",
                "status": "needs_installer",
                "evidence": evidence(),
            }
        },
    }
    record = client.post(BASE, json=payload, headers=HEADERS).json["record"]
    path = f"{BASE}/{record['id']}/handoff"
    assert client.get(path).status_code == 405
    assert client.post(path).status_code == 403
    response = client.post(path, headers=HEADERS)
    assert response.status_code == 200, response.text
    assert (
        response.headers["Content-Disposition"]
        == "attachment; filename=equipment-handoff.json"
    )
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json["equipment"]["model"] == "Roof 1"
    finding = next(
        item for item in response.json["findings"] if item["check"] == "update_support"
    )
    assert finding["status"] == "needs_installer"
    assert finding["evidence"]["source_url"] == SOURCE_URL
    assert "OpenLEG prüft diese Angaben nicht" in response.json["notice"]
    for private in (
        "PRIVATE-SERIAL",
        "PRIVATE-NETWORK",
        "PRIVATE-NOTE",
        "PRIVATE-USER",
        "Synthetic street",
        "owner@example.ch",
    ):
        assert private not in response.text
    test_dashboard_access_routes._set_session(client, "other")
    assert client.post(path, headers=HEADERS).status_code == 404
    test_dashboard_access_routes._set_session(client, "owner")
    assert client.delete(f"{BASE}/{record['id']}", headers=HEADERS).status_code == 200
    assert client.post(path, headers=HEADERS).status_code == 404


def test_payload_is_bounded_and_json_only(equipment_client):
    client = equipment_client
    response = client.post(
        BASE,
        data='{"manufacturer":"' + "x" * 25_000 + '"}',
        content_type="application/json",
        headers=HEADERS,
    )
    assert response.status_code == 413
    assert (
        client.post(
            BASE, json={}, headers={**HEADERS, "Content-Encoding": "gzip"}
        ).status_code
        == 413
    )
    assert (
        client.post(BASE, data="manufacturer=Example", headers=HEADERS).status_code
        == 415
    )
    assert (
        client.post(
            BASE, data="{", content_type="application/json", headers=HEADERS
        ).status_code
        == 400
    )
    assert (
        client.post(
            BASE,
            data='{"rated_ac_kw":NaN}',
            content_type="application/json",
            headers=HEADERS,
        ).status_code
        == 400
    )
    assert client.get(BASE).json == {"records": []}


def test_product_page_creates_edits_downloads_and_deletes_without_javascript(
    equipment_client,
):
    client = equipment_client
    page = client.get("/dashboard/equipment")
    assert page.status_code == 200
    assert 'href="/dashboard/equipment/new"' in page.text
    form = client.get("/dashboard/equipment/new")
    assert form.status_code == 200
    assert 'name="csrf_token" value="csrf-secret"' in form.text
    response = client.post(
        "/dashboard/equipment/new",
        data={
            "csrf_token": "csrf-secret",
            "manufacturer": "Example",
            "model": "Roof 3",
        },
    )
    assert response.status_code == 302, response.text
    location = response.headers["Location"]
    assert location.startswith("/dashboard/equipment/")
    html = client.get(location).text
    assert "Roof 3" in html
    assert "Lokale Messwerte deaktivieren keinen Fernzugriff" in html
    assert "OpenLEG prüft diese Angaben nicht" in html
    assert 'name="checklist.account_mfa.applicability"' in html
    assert (
        client.post(location, data={"manufacturer": "Missing CSRF"}).status_code == 403
    )
    invalid = client.post(
        location,
        data={
            "csrf_token": "csrf-secret",
            "evidence.kind": "owner_report",
            "evidence.reviewed_on": "9999-01-01",
        },
    )
    assert invalid.status_code == 400
    assert 'role="alert"' in invalid.text
    assert (
        client.post(
            location,
            data={
                "csrf_token": "csrf-secret",
                "manufacturer": "Example",
                "model": "Roof 4",
            },
        ).status_code
        == 302
    )
    assert "Roof 4" in client.get(location).text
    download = client.post(location + "/handoff", data={"csrf_token": "csrf-secret"})
    assert download.status_code == 200
    assert download.json["equipment"]["model"] == "Roof 4"
    assert (
        client.post(
            location + "/delete", data={"csrf_token": "csrf-secret"}
        ).status_code
        == 302
    )
    assert "Roof 4" not in client.get("/dashboard/equipment").text
    assert client.get(location).status_code == 404


def test_device_context_change_requires_fresh_checklist_cloud_and_backup_review(
    equipment_client,
):
    client = equipment_client
    payload = {
        "manufacturer": "Example",
        "model": "Roof 1",
        "firmware": "1.0",
        "checklist": {
            "default_passwords": {
                "applicability": "applicable",
                "status": "done",
                "evidence": evidence(),
            }
        },
        "vendor_cloud_review": {
            "status": "reviewed",
            "remote_access": "disabled",
            "evidence": evidence(),
        },
        "backup": {
            "capability": "available",
            "installer_evidence": evidence("installer_evidence"),
        },
    }
    record = client.post(BASE, json=payload, headers=HEADERS).json["record"]
    path = f"{BASE}/{record['id']}"
    # A changed model/firmware cannot silently carry old completed checks forward.
    response = client.put(path, json={**payload, "firmware": "2.0"}, headers=HEADERS)
    assert response.status_code == 200
    saved = response.json["record"]
    assert saved["checklist"]["default_passwords"]["status"] == "unknown"
    assert saved["vendor_cloud_review"]["status"] == "unknown"
    assert saved["backup"] == {
        "capability": "unknown",
        "installer_evidence": {
            "kind": "unknown",
            "reviewed_on": None,
            "source_url": None,
        },
    }
    # The owner can submit a new review against the now-stored device context.
    response = client.put(path, json={**payload, "firmware": "2.0"}, headers=HEADERS)
    assert response.json["record"]["checklist"]["default_passwords"]["status"] == "done"


def test_profile_deletion_removes_equipment_and_its_private_evidence(equipment_client):
    client = equipment_client
    old = client.post(
        BASE, json={"private_notes": "PRIVATE", "evidence": evidence()}, headers=HEADERS
    ).json["record"]
    assert db.delete_building("owner")
    assert db.save_building(
        "owner",
        "owner@example.ch",
        {"address": "Synthetic street", "lat": 47, "lon": 8},
        {},
        verified=True,
    )
    assert client.get(BASE).json == {"records": []}
    assert client.get(f"{BASE}/{old['id']}").status_code == 404


def test_owner_inventory_has_a_storage_bound(equipment_client):
    for _ in range(100):
        db.create_equipment("owner", {})
    response = equipment_client.post(BASE, json={}, headers=HEADERS)
    assert response.status_code == 409
    assert response.json == {"error": "record_limit_reached"}
    assert len(equipment_client.get(BASE).json["records"]) == 100


def test_admin_and_telemetry_grant_do_not_reveal_equipment(equipment_client):
    client = equipment_client
    record = client.post(
        BASE,
        json={"model": "PRIVATE-MODEL", "private_notes": "PRIVATE-NOTE"},
        headers=HEADERS,
    ).json["record"]
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO communities(community_id,name) VALUES ('community-equipment','Example')"
        )
        cur.execute(
            "INSERT INTO community_members(community_id,building_id,status,role) VALUES ('community-equipment','owner','confirmed','member'),('community-equipment','other','confirmed','admin')"
        )
    telemetry_path = "/api/telemetry/v1/communities/community-equipment/installations"
    enrolled = client.post(
        telemetry_path,
        json={
            "name": "Roof",
            "device_id": "inverter",
            "source_id": "reader",
            "cadence_seconds": 30,
        },
        headers=HEADERS,
    )
    assert enrolled.status_code == 201, enrolled.text
    installation = enrolled.json["installation"]["id"]
    assert (
        client.post(
            f"{telemetry_path}/{installation}/share",
            json={"viewer_building_id": "other"},
            headers=HEADERS,
        ).status_code
        == 200
    )
    test_dashboard_access_routes._set_session(client, "other")
    assert client.get(f"{telemetry_path}/{installation}/samples").status_code == 200
    assert client.get(BASE).json == {"records": []}
    assert client.get(f"{BASE}/{record['id']}").status_code == 404
    page_path = f"/dashboard/equipment/{record['id']}"
    for path in (page_path, page_path + "/handoff", page_path + "/delete"):
        response = (
            client.get(path)
            if path == page_path
            else client.post(path, data={"csrf_token": "csrf-secret"})
        )
        assert response.status_code == 404
        assert "PRIVATE-MODEL" not in response.text
        assert "PRIVATE-NOTE" not in response.text
        assert response.headers["Cache-Control"] == "no-store"


def test_form_body_without_content_length_is_bounded_before_parsing(equipment_client):
    response = equipment_client.post(
        "/dashboard/equipment/new",
        data="csrf_token=csrf-secret&private_notes=" + "x" * 25_000,
        content_type="application/x-www-form-urlencoded",
        environ_overrides={"CONTENT_LENGTH": "", "wsgi.input_terminated": True},
    )
    assert response.status_code == 413
    assert equipment_client.get(BASE).json == {"records": []}
