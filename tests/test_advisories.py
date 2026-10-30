# SPDX-License-Identifier: AGPL-3.0-or-later
"""Advisory review/publication and private owner actions through real HTTP/PostgreSQL."""

import pytest

from tests import test_equipment, test_interest_postgres
from tests.test_advisories_domain import entry

app_module = test_equipment.app_module
interest_database = test_interest_postgres.interest_database
equipment_client = test_equipment.equipment_client
pytestmark = pytest.mark.integration
ADMIN = "/api/admin/advisories/v1"
ADMIN_HEADERS = {"X-Admin-Token": "advisory-admin-test"}
OWNER_HEADERS = {"X-CSRF-Token": "csrf-secret"}


@pytest.fixture
def client(equipment_client, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "advisory-admin-test")
    return equipment_client


def test_catalog_starts_empty_and_draft_creation_is_header_authenticated(client):
    assert client.get(ADMIN + "/catalog", headers=ADMIN_HEADERS).json == {"entries": []}
    assert client.post(ADMIN + "/catalog", json=entry()).status_code == 403
    assert (
        client.post(
            ADMIN + "/catalog?token=advisory-admin-test", json=entry()
        ).status_code
        == 403
    )
    response = client.post(ADMIN + "/catalog", json=entry(), headers=ADMIN_HEADERS)
    assert response.status_code == 201, response.text
    draft = response.json
    assert draft["revision"] == 1
    assert draft["state"] == "draft"
    assert draft["review"] is None
    assert len(draft["digest"]) == 64
    assert draft["entry"]["source_revision"] == "1.0"
    assert client.get(ADMIN + "/catalog", headers=ADMIN_HEADERS).json["entries"] == [
        draft
    ]
    assert response.headers["Cache-Control"] == "no-store"


def review(draft, **changes):
    from datetime import UTC, datetime

    return {
        "digest": draft["digest"],
        "reviewer_id": "PRIVATE-MAINTAINER",
        "reviewed_on": datetime.now(UTC).date().isoformat(),
        "approve": True,
        **changes,
    }


def test_exact_revision_review_keeps_corrections_superseded_and_withdrawn_sources(
    client,
):
    draft = client.post(ADMIN + "/catalog", json=entry(), headers=ADMIN_HEADERS).json
    path = ADMIN + "/catalog/" + draft["advisory_id"]
    publish = path + "/revisions/1/publish"
    assert (
        client.post(
            publish, json=review(draft, digest="0" * 64), headers=ADMIN_HEADERS
        ).status_code
        == 409
    )
    assert (
        client.post(
            publish, json=review(draft, approve=False), headers=ADMIN_HEADERS
        ).status_code
        == 400
    )
    assert (
        client.post(
            publish, json=review(draft, reviewed_on="2024-01-01"), headers=ADMIN_HEADERS
        ).status_code
        == 400
    )
    assert (
        client.post(
            publish, json=review(draft, reviewed_on="9999-01-01"), headers=ADMIN_HEADERS
        ).status_code
        == 400
    )
    assert (
        client.post(publish, json=review(draft), headers=ADMIN_HEADERS).status_code
        == 200
    )
    corrected = client.post(
        path + "/revisions",
        json=entry(
            source_revision="2.0",
            summary="Corrected source text",
            change_note="Publisher correction",
        ),
        headers=ADMIN_HEADERS,
    )
    assert corrected.status_code == 201, corrected.text
    corrected = corrected.json
    assert corrected["revision"] == 2
    history = client.get(path, headers=ADMIN_HEADERS).json["revisions"]
    assert (
        history[0]["entry"]["summary"]
        == "Synthetic test evidence, not a published finding."
    )
    assert history[0]["state"] == "active"
    assert history[1]["state"] == "draft"
    assert (
        client.post(publish, json=review(draft), headers=ADMIN_HEADERS).status_code
        == 409
    )
    assert (
        client.post(
            path + "/revisions/2/publish", json=review(corrected), headers=ADMIN_HEADERS
        ).status_code
        == 200
    )
    history = client.get(path, headers=ADMIN_HEADERS).json["revisions"]
    assert [item["state"] for item in history] == ["superseded", "active"]
    withdrawn = client.post(
        path + "/revisions",
        json=entry(
            source_revision="3.0",
            status="withdrawn",
            change_note="Publisher withdrew this advisory",
        ),
        headers=ADMIN_HEADERS,
    ).json
    assert (
        client.post(
            path + "/revisions/3/publish", json=review(withdrawn), headers=ADMIN_HEADERS
        ).status_code
        == 200
    )
    history = client.get(path, headers=ADMIN_HEADERS).json["revisions"]
    assert [item["state"] for item in history] == [
        "superseded",
        "superseded",
        "withdrawn",
    ]
    assert history[0]["review"]["reviewer_id"] == "PRIVATE-MAINTAINER"
    assert history[1]["entry"]["summary"] == "Corrected source text"


def policy(**changes):
    return {
        "enabled": True,
        "review_owner": "PRIVATE-SOURCE-OWNER",
        "cadence_days": 14,
        "coverage": "Synthetic vendor source only; incomplete coverage.",
        "source_urls": ["https://example.com/advisory"],
        **changes,
    }


def source_review(policy_revision, **changes):
    from datetime import UTC, datetime

    return {
        "policy_revision": policy_revision,
        "review_owner": "PRIVATE-SOURCE-OWNER",
        "reviewed_on": datetime.now(UTC).date().isoformat(),
        "outcome": "success",
        "source_urls": ["https://example.com/advisory"],
        "failure_code": None,
        **changes,
    }


def test_named_source_review_cadence_and_failures_control_notice_readiness(client):
    path = ADMIN + "/source-policy"
    assert (
        client.get(path, headers=ADMIN_HEADERS).json["health"]["status"] == "disabled"
    )
    assert (
        client.post(
            path, json=policy(review_owner=""), headers=ADMIN_HEADERS
        ).status_code
        == 400
    )
    configured = client.post(path, json=policy(), headers=ADMIN_HEADERS)
    assert configured.status_code == 201, configured.text
    revision = configured.json["policy"]["revision"]
    assert configured.json["health"]["status"] == "unreviewed"
    source_path = ADMIN + "/source-reviews"
    assert (
        client.post(
            source_path,
            json=source_review(revision, review_owner="Wrong owner"),
            headers=ADMIN_HEADERS,
        ).status_code
        == 409
    )
    assert (
        client.post(
            source_path,
            json=source_review(revision, source_urls=[]),
            headers=ADMIN_HEADERS,
        ).status_code
        == 400
    )
    assert (
        client.post(
            source_path, json=source_review(revision), headers=ADMIN_HEADERS
        ).status_code
        == 201
    )
    health = client.get(path, headers=ADMIN_HEADERS).json["health"]
    assert health["status"] == "current"
    assert health["last_successful_review"] == source_review(revision)["reviewed_on"]
    assert "PRIVATE-SOURCE-OWNER" not in str(health)
    assert (
        client.post(
            source_path,
            json=source_review(
                revision, outcome="failure", failure_code="fetch_failed"
            ),
            headers=ADMIN_HEADERS,
        ).status_code
        == 201
    )
    health = client.get(path, headers=ADMIN_HEADERS).json["health"]
    assert health["status"] == "failed"
    assert health["last_successful_review"] is not None
    assert (
        client.post(
            source_path,
            json=source_review(revision, reviewed_on="2025-01-01"),
            headers=ADMIN_HEADERS,
        ).status_code
        == 201
    )
    assert client.get(path, headers=ADMIN_HEADERS).json["health"]["status"] == "stale"
    changed = client.post(path, json=policy(cadence_days=7), headers=ADMIN_HEADERS).json
    assert changed["health"]["status"] == "unreviewed"
    assert (
        client.post(
            source_path, json=source_review(revision), headers=ADMIN_HEADERS
        ).status_code
        == 409
    )


def create_device(client, **changes):
    response = client.post(
        "/api/equipment/v1/records",
        json={
            "manufacturer": "Example Solar",
            "model": "Roof-1",
            "firmware": "1.5.0",
            **changes,
        },
        headers=OWNER_HEADERS,
    )
    assert response.status_code == 201, response.text
    return response.json["record"]


def publish_entry(client, payload=None):
    draft = client.post(
        ADMIN + "/catalog", json=payload or entry(), headers=ADMIN_HEADERS
    ).json
    path = f"{ADMIN}/catalog/{draft['advisory_id']}/revisions/1/publish"
    assert (
        client.post(path, json=review(draft), headers=ADMIN_HEADERS).status_code == 200
    )
    return draft


def enable_sources(client, **changes):
    saved = client.post(
        ADMIN + "/source-policy", json=policy(**changes), headers=ADMIN_HEADERS
    )
    assert saved.status_code == 201, saved.text
    revision = saved.json["policy"]["revision"]
    assert (
        client.post(
            ADMIN + "/source-reviews",
            json=source_review(
                revision,
                source_urls=changes.get("source_urls", policy()["source_urls"]),
            ),
            headers=ADMIN_HEADERS,
        ).status_code
        == 201
    )
    return revision


def test_owner_notices_require_published_revision_and_fresh_covered_sources(client):
    device = create_device(client)
    path = f"/api/equipment/v1/records/{device['id']}/advisories"
    empty = client.get(path)
    assert empty.status_code == 200, empty.text
    assert empty.json["catalog"]["status"] == "disabled"
    assert empty.json["notices"] == []
    draft = client.post(ADMIN + "/catalog", json=entry(), headers=ADMIN_HEADERS).json
    enable_sources(client)
    assert client.get(path).json["assessments"] == []
    publish_path = f"{ADMIN}/catalog/{draft['advisory_id']}/revisions/1/publish"
    assert (
        client.post(publish_path, json=review(draft), headers=ADMIN_HEADERS).status_code
        == 200
    )
    response = client.get(path)
    assert response.json["notices"][0]["match"]["classification"] == "affected"
    assert response.json["notices"][0]["reviewed_on"] == review(draft)["reviewed_on"]
    assert (
        response.json["notices"][0]["entry"]["source_url"]
        == "https://example.com/advisory"
    )
    assert "PRIVATE-MAINTAINER" not in response.text
    assert "PRIVATE-SOURCE-OWNER" not in response.text
    assert response.headers["Cache-Control"] == "no-store"
    revision = client.post(
        ADMIN + "/source-policy",
        json=policy(source_urls=["https://other.example.com/advisory"]),
        headers=ADMIN_HEADERS,
    ).json["policy"]["revision"]
    assert client.get(path).json["notices"] == []
    assert (
        client.post(
            ADMIN + "/source-reviews",
            json=source_review(
                revision, source_urls=["https://other.example.com/advisory"]
            ),
            headers=ADMIN_HEADERS,
        ).status_code
        == 201
    )
    uncovered = client.get(path).json
    assert uncovered["catalog"]["status"] == "current"
    assert uncovered["assessments"][0]["source_covered"] is False
    assert uncovered["notices"] == []


def test_owner_acknowledgement_and_remedy_are_bound_to_revision_and_device_context(
    client,
):
    device = create_device(client)
    draft = publish_entry(client)
    enable_sources(client)
    base = f"/api/equipment/v1/records/{device['id']}"
    view = client.get(base + "/advisories").json
    action_path = f"{base}/advisories/{draft['advisory_id']}/1/actions"
    payload = {
        "context_digest": view["context_digest"],
        "acknowledged": True,
        "remedy_status": "reported_done",
        "evidence": {
            "kind": "installer_evidence",
            "reviewed_on": "2026-01-02",
            "source_url": "https://example.com/remedy",
        },
        "private_note": "PRIVATE-OWNER-EVIDENCE",
    }
    assert client.post(action_path, json=payload).status_code == 403
    assert (
        client.post(
            action_path,
            json={**payload, "context_digest": "0" * 64},
            headers=OWNER_HEADERS,
        ).status_code
        == 409
    )
    assert (
        client.post(
            action_path, json={**payload, "evidence": {}}, headers=OWNER_HEADERS
        ).status_code
        == 400
    )
    assert (
        client.post(
            action_path,
            json={
                **payload,
                "evidence": {"kind": "installer_evidence", "reviewed_on": "9999-01-01"},
            },
            headers=OWNER_HEADERS,
        ).status_code
        == 400
    )
    response = client.post(action_path, json=payload, headers=OWNER_HEADERS)
    assert response.status_code == 200, response.text
    saved = client.get(base + "/advisories").json["notices"][0]["action"]
    assert saved["acknowledged"] is True
    assert saved["remedy_status"] == "reported_done"
    assert saved["private_note"] == "PRIVATE-OWNER-EVIDENCE"
    assert (
        client.get(base + "/advisories").json["notices"][0]["match"]["classification"]
        == "affected"
    )
    revision_path = f"{ADMIN}/catalog/{draft['advisory_id']}/revisions"
    corrected = client.post(
        revision_path,
        json=entry(source_revision="2.0", change_note="Updated publication"),
        headers=ADMIN_HEADERS,
    ).json
    assert (
        client.post(
            revision_path + "/2/publish", json=review(corrected), headers=ADMIN_HEADERS
        ).status_code
        == 200
    )
    updated = client.get(base + "/advisories").json
    assert updated["notices"][0]["revision"] == 2
    assert updated["notices"][0]["action"] is None
    assert updated["history"][0]["state"] == "superseded"
    assert (
        updated["past_actions"][0]["action"]["private_note"] == "PRIVATE-OWNER-EVIDENCE"
    )
    assert (
        client.post(action_path, json=payload, headers=OWNER_HEADERS).status_code == 409
    )
    assert (
        client.put(
            base,
            json={
                "manufacturer": "Example Solar",
                "model": "Roof-1",
                "firmware": "2.1.0",
            },
            headers=OWNER_HEADERS,
        ).status_code
        == 200
    )
    after_firmware = client.get(base + "/advisories").json
    assert after_firmware["notices"] == []
    assert after_firmware["assessments"][0]["match"]["classification"] == "not_within"
    assert after_firmware["past_actions"][0]["device_context"]["firmware"] == "1.5.0"
    assert client.delete(base, headers=OWNER_HEADERS).status_code == 200
    assert client.get(base + "/advisories").status_code == 404


def test_private_product_page_shows_evidence_and_records_owner_action_without_javascript(
    client,
):
    device = create_device(client)
    draft = publish_entry(
        client,
        entry(
            title="<script>unsafe title</script>",
            summary="<img src=x onerror=alert(1)>",
        ),
    )
    enable_sources(client)
    path = f"/dashboard/equipment/{device['id']}/advisories"
    response = client.get(path)
    assert response.status_code == 200, response.text
    assert (
        "Kein passender Hinweis bedeutet nicht, dass Ihre Anlage sicher ist"
        in response.text
    )
    assert "&lt;script&gt;unsafe title&lt;/script&gt;" in response.text
    assert "<script>unsafe title</script>" not in response.text
    assert "<img src=x onerror=alert(1)>" not in response.text
    assert "https://example.com/advisory" in response.text
    assert 'name="csrf_token" value="csrf-secret"' in response.text
    assert "PRIVATE-MAINTAINER" not in response.text
    assert "PRIVATE-SOURCE-OWNER" not in response.text
    assert f'href="{path}"' in client.get(f"/dashboard/equipment/{device['id']}").text
    action_path = f"{path}/{draft['advisory_id']}/1/actions"
    assert client.post(action_path, data={"acknowledged": "yes"}).status_code == 403
    digest = client.get(f"/api/equipment/v1/records/{device['id']}/advisories").json[
        "context_digest"
    ]
    invalid = client.post(
        action_path,
        data={
            "csrf_token": "csrf-secret",
            "context_digest": digest,
            "remedy_status": "reported_done",
        },
    )
    assert invalid.status_code == 400
    assert 'role="alert"' in invalid.text
    saved = client.post(
        action_path,
        data={
            "csrf_token": "csrf-secret",
            "context_digest": digest,
            "acknowledged": "yes",
            "remedy_status": "planned",
            "private_note": "Private installer discussion",
        },
    )
    assert saved.status_code == 302
    assert "Private installer discussion" in client.get(path).text


def test_source_failure_or_stale_review_pauses_existing_notices(client):
    device = create_device(client)
    publish_entry(client)
    path = f"/api/equipment/v1/records/{device['id']}/advisories"
    configured = client.post(
        ADMIN + "/source-policy", json=policy(), headers=ADMIN_HEADERS
    ).json
    revision = configured["policy"]["revision"]
    assert client.get(path).json["notices"] == []
    assert (
        client.post(
            ADMIN + "/source-reviews",
            json=source_review(revision),
            headers=ADMIN_HEADERS,
        ).status_code
        == 201
    )
    assert len(client.get(path).json["notices"]) == 1
    assert (
        client.post(
            ADMIN + "/source-reviews",
            json=source_review(
                revision, outcome="failure", failure_code="format_changed"
            ),
            headers=ADMIN_HEADERS,
        ).status_code
        == 201
    )
    failed = client.get(path).json
    assert failed["catalog"]["status"] == "failed"
    assert failed["notices"] == []
    assert len(failed["assessments"]) == 1
    assert (
        client.post(
            ADMIN + "/source-reviews",
            json=source_review(revision, reviewed_on="2025-01-01"),
            headers=ADMIN_HEADERS,
        ).status_code
        == 201
    )
    assert client.get(path).json["catalog"]["status"] == "stale"
    assert client.get(path).json["notices"] == []


def test_device_firmware_change_never_reuses_acknowledgement_from_the_same_advisory_revision(
    client,
):
    device = create_device(client)
    draft = publish_entry(client)
    enable_sources(client)
    base = f"/api/equipment/v1/records/{device['id']}"
    view = client.get(base + "/advisories").json
    action_path = f"{base}/advisories/{draft['advisory_id']}/1/actions"
    assert (
        client.post(
            action_path,
            json={"context_digest": view["context_digest"], "acknowledged": True},
            headers=OWNER_HEADERS,
        ).status_code
        == 200
    )
    assert (
        client.put(
            base,
            json={
                "manufacturer": "Example Solar",
                "model": "Roof-1",
                "firmware": "1.5.1",
            },
            headers=OWNER_HEADERS,
        ).status_code
        == 200
    )
    changed = client.get(base + "/advisories").json
    assert changed["notices"][0]["action"] is None
    assert changed["past_actions"][0]["action"]["acknowledged"] is True
    assert changed["past_actions"][0]["device_context"]["firmware"] == "1.5.0"


def test_equipment_notices_and_remedy_evidence_remain_owner_only(client):
    import database as db
    from tests.test_dashboard_access_routes import _set_session

    owner_device = create_device(client)
    draft = publish_entry(client)
    enable_sources(client)
    owner_path = f"/api/equipment/v1/records/{owner_device['id']}/advisories"
    digest = client.get(owner_path).json["context_digest"]
    action_path = f"{owner_path}/{draft['advisory_id']}/1/actions"
    payload = {
        "context_digest": digest,
        "acknowledged": True,
        "private_note": "PRIVATE-REMEDY-EVIDENCE",
    }
    assert (
        client.post(action_path, json=payload, headers=OWNER_HEADERS).status_code == 200
    )
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO communities(community_id,name) VALUES ('advisory-community','Example')"
        )
        cur.execute(
            "INSERT INTO community_members(community_id,building_id,status,role) VALUES ('advisory-community','owner','confirmed','member'),('advisory-community','other','confirmed','admin')"
        )
    _set_session(client, "other")
    other_device = create_device(client)
    other_view = client.get(
        f"/api/equipment/v1/records/{other_device['id']}/advisories"
    )
    assert other_view.json["notices"][0]["action"] is None
    assert "PRIVATE-REMEDY-EVIDENCE" not in other_view.text
    for response in (
        client.get(owner_path),
        client.post(action_path, json=payload, headers=OWNER_HEADERS),
        client.get(f"/dashboard/equipment/{owner_device['id']}/advisories"),
    ):
        assert response.status_code == 404
        assert "PRIVATE-REMEDY-EVIDENCE" not in response.text
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["Referrer-Policy"] == "no-referrer"
    _set_session(client, "owner")
    assert db.update_building_verified("owner", False)
    assert client.get(owner_path).status_code == 403
    assert (
        client.post(action_path, json=payload, headers=OWNER_HEADERS).status_code == 403
    )
    with client.session_transaction() as session:
        session.clear()
    assert client.get(owner_path).status_code == 401
    assert (
        client.post(action_path, json=payload, headers=OWNER_HEADERS).status_code == 401
    )


def test_advisory_payloads_and_header_only_admin_access_are_bounded(client):
    assert (
        client.post(
            ADMIN + "/catalog", data={"token": "advisory-admin-test"}
        ).status_code
        == 403
    )
    assert (
        client.get(ADMIN + "/source-reviews?token=advisory-admin-test").status_code
        == 403
    )
    huge = '{"summary":"' + "x" * 33_000 + '"}'
    assert (
        client.post(
            ADMIN + "/catalog",
            data=huge,
            content_type="application/json",
            headers=ADMIN_HEADERS,
        ).status_code
        == 413
    )
    assert (
        client.post(
            ADMIN + "/catalog",
            data=huge,
            content_type="application/json",
            headers=ADMIN_HEADERS,
            environ_overrides={"CONTENT_LENGTH": "", "wsgi.input_terminated": True},
        ).status_code
        == 413
    )
    assert (
        client.post(
            ADMIN + "/catalog",
            json=entry(),
            headers={**ADMIN_HEADERS, "Content-Encoding": "gzip"},
        ).status_code
        == 413
    )
    assert (
        client.post(
            ADMIN + "/catalog",
            data="{",
            content_type="application/json",
            headers=ADMIN_HEADERS,
        ).status_code
        == 400
    )


def test_admin_catalog_rejects_query_credentials_even_with_a_valid_header(client):
    response = client.post(
        ADMIN + "/catalog?token=advisory-admin-test",
        json=entry(),
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 403
    assert client.get(ADMIN + "/catalog", headers=ADMIN_HEADERS).json == {"entries": []}


def test_catalog_and_revision_counts_have_storage_bounds(client):
    import database as db

    first = db.create_advisory(entry())
    for _ in range(199):
        db.create_advisory(entry())
    assert (
        client.post(ADMIN + "/catalog", json=entry(), headers=ADMIN_HEADERS).status_code
        == 409
    )
    for _ in range(49):
        db.append_advisory_revision(first["advisory_id"], entry())
    assert (
        client.post(
            f"{ADMIN}/catalog/{first['advisory_id']}/revisions",
            json=entry(),
            headers=ADMIN_HEADERS,
        ).status_code
        == 409
    )
    assert (
        len(
            client.get(
                f"{ADMIN}/catalog/{first['advisory_id']}", headers=ADMIN_HEADERS
            ).json["revisions"]
        )
        == 50
    )


def test_publication_cannot_approve_an_obsolete_draft_using_a_newer_digest(client):
    first = client.post(ADMIN + "/catalog", json=entry(), headers=ADMIN_HEADERS).json
    path = f"{ADMIN}/catalog/{first['advisory_id']}/revisions"
    second = client.post(
        path,
        json=entry(source_revision="2.0", change_note="Unpublished correction"),
        headers=ADMIN_HEADERS,
    ).json
    assert (
        client.post(
            path + "/1/publish", json=review(second), headers=ADMIN_HEADERS
        ).status_code
        == 409
    )
    assert (
        client.post(
            path + "/2/publish", json=review(first), headers=ADMIN_HEADERS
        ).status_code
        == 409
    )
    assert [
        item["state"]
        for item in client.get(
            f"{ADMIN}/catalog/{first['advisory_id']}", headers=ADMIN_HEADERS
        ).json["revisions"]
    ] == ["draft", "draft"]


def test_published_withdrawal_removes_active_notices_without_erasing_source_history(
    client,
):
    device = create_device(client)
    draft = publish_entry(client)
    enable_sources(client)
    path = f"/api/equipment/v1/records/{device['id']}/advisories"
    assert len(client.get(path).json["notices"]) == 1
    revisions = f"{ADMIN}/catalog/{draft['advisory_id']}/revisions"
    withdrawn = client.post(
        revisions,
        json=entry(
            source_revision="2.0",
            status="withdrawn",
            change_note="Publisher withdrawal",
        ),
        headers=ADMIN_HEADERS,
    ).json
    assert (
        client.post(
            revisions + "/2/publish", json=review(withdrawn), headers=ADMIN_HEADERS
        ).status_code
        == 200
    )
    result = client.get(path).json
    assert result["notices"] == []
    assert result["assessments"][0]["state"] == "withdrawn"
    assert result["history"][0]["state"] == "superseded"
    assert result["history"][0]["entry"]["source_revision"] == "1.0"
    assert (
        client.post(
            f"{path}/{draft['advisory_id']}/2/actions",
            json={"context_digest": result["context_digest"], "acknowledged": True},
            headers=OWNER_HEADERS,
        ).status_code
        == 409
    )
