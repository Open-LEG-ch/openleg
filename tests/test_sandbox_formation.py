# SPDX-License-Identifier: AGPL-3.0-or-later
"""Form a fictional LEG through ordinary controls and local external adapters."""

from urllib.parse import parse_qs, urlsplit

import pytest

from tests import test_sandbox_http
from tests.test_sandbox_personas import _start

village = test_sandbox_http.village
_csrf = test_sandbox_http._csrf
pytestmark = pytest.mark.integration


def test_founder_can_begin_the_self_guided_formation_scenario(village):
    client = village.test_client()
    prefix, _ = _start(client, "founder")
    form = client.get(prefix + "/leg/community/create")
    response = client.post(
        prefix + "/leg/community/create",
        data={"csrf_token": _csrf(form), "name": "LEG Sonnwil Simulation"},
    )
    assert response.status_code == 302
    cid = parse_qs(urlsplit(response.location).query)["cid"][0]
    page = client.get(prefix + "/sandbox/formation?cid=" + cid)
    assert page.status_code == 200
    assert "Simulation" in page.text
    assert "sonnwil-resident@example.org" in page.text
    assert "sonnwil-producer@example.org" in page.text
    assert prefix + "/leg/dashboard?cid=" + cid in page.text


def _create_ready(client):
    """Create a three-household LEG and render its unsigned contracts normally."""
    from tests.test_sandbox_personas import _switch

    prefix, _ = _start(client, "founder")
    form = client.get(prefix + "/leg/community/create")
    response = client.post(
        prefix + "/leg/community/create",
        data={"csrf_token": _csrf(form), "name": "LEG Sonnwil Simulation"},
    )
    cid = parse_qs(urlsplit(response.location).query)["cid"][0]
    for role, email in (
        ("resident", "sonnwil-resident@example.org"),
        ("producer", "sonnwil-producer@example.org"),
    ):
        page = client.get(prefix + "/leg/dashboard?cid=" + cid)
        assert (
            client.post(
                prefix + f"/leg/community/{cid}/invite",
                data={"csrf_token": _csrf(page), "invite_email": email},
            ).status_code
            == 302
        )
        _switch(client, prefix, role)
        page = client.get(prefix + "/leg/dashboard?cid=" + cid)
        assert (
            client.post(
                prefix + f"/leg/community/{cid}/confirm",
                data={"csrf_token": _csrf(page)},
            ).status_code
            == 302
        )
        _switch(client, prefix, "founder")
    page = client.get(prefix + "/leg/dashboard?cid=" + cid)
    assert (
        client.post(
            prefix + f"/leg/community/{cid}/start-formation",
            data={"csrf_token": _csrf(page)},
        ).status_code
        == 302
    )
    assert (
        client.post(
            prefix + f"/leg/community/{cid}/documents", data={"csrf_token": _csrf(page)}
        ).status_code
        == 302
    )
    return prefix, cid


def test_ordinary_invitations_are_captured_once_inside_the_village(village):
    """Keep captured invitations idempotent and private to their source village."""
    client = village.test_client()
    prefix, cid = _create_ready(client)
    inbox = client.get(prefix + "/sandbox/inbox")
    assert inbox.text.count("SIMULATION SONNWIL: LEG-Einladung") == 2
    assert "sonnwil-resident@example.org" in inbox.text
    assert "sonnwil-producer@example.org" in inbox.text
    assert prefix + "/leg/dashboard?cid=" + cid in inbox.text
    page = client.get(prefix + "/leg/dashboard?cid=" + cid)
    for email in ("sonnwil-resident@example.org", "nobody@example.org"):
        assert (
            client.post(
                prefix + f"/leg/community/{cid}/invite",
                data={"csrf_token": _csrf(page), "invite_email": email},
            ).status_code
            == 302
        )
    assert (
        client.get(prefix + "/sandbox/inbox").text.count(
            "SIMULATION SONNWIL: LEG-Einladung"
        )
        == 2
    )
    other = village.test_client()
    other_prefix, _ = _start(other, "founder")
    assert (
        "SIMULATION SONNWIL: LEG-Einladung"
        not in other.get(other_prefix + "/sandbox/inbox").text
    )


def test_only_complete_participant_signatures_unlock_vnb_submission(village):
    """Require explicit consent and all current receipts before VNB submission."""
    import re

    from tests.test_sandbox_onboarding import _workspace_connection
    from tests.test_sandbox_personas import _switch

    client = village.test_client()
    prefix, cid = _create_ready(client)
    guide = prefix + "/sandbox/formation?cid=" + cid
    page = client.get(guide)
    bundle = re.search(r'name="bundle" value="([a-f0-9]{64})"', page.text)
    assert bundle is not None, "the guide must expose the reviewed document bundle"
    path = prefix + f"/sandbox/formation/{cid}/signatures"
    fields = {"csrf_token": _csrf(page), "bundle": bundle.group(1), "confirm": "yes"}
    assert (
        client.post(
            path, data={"bundle": fields["bundle"], "confirm": "yes"}
        ).status_code
        == 400
    )
    assert client.post(path, data={**fields, "confirm": ""}).status_code == 400
    assert client.post(path, data=fields).status_code == 302
    assert client.post(path, data=fields).status_code == 302
    assert (
        client.post(
            prefix + f"/leg/community/{cid}/vnb-submissions",
            data={"csrf_token": fields["csrf_token"]},
        ).status_code
        == 409
    )
    with (
        _workspace_connection(village, client) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT COUNT(*) FROM leg_signature_receipts WHERE community_id=%s", (cid,)
        )
        assert cursor.fetchone()[0] == 1
        cursor.execute("SELECT status FROM communities WHERE community_id=%s", (cid,))
        assert cursor.fetchone()[0] == "documents_generated"
    for role in ("resident", "producer"):
        _switch(client, prefix, role)
        page = client.get(guide)
        assert (
            client.post(path, data={**fields, "csrf_token": _csrf(page)}).status_code
            == 302
        )
    with (
        _workspace_connection(village, client) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT COUNT(*) FROM leg_signature_receipts WHERE community_id=%s", (cid,)
        )
        assert cursor.fetchone()[0] == 3
        cursor.execute(
            "SELECT DISTINCT signing_status FROM leg_documents WHERE community_id=%s",
            (cid,),
        )
        assert cursor.fetchall() == [("completed",)]
        cursor.execute("SELECT status FROM communities WHERE community_id=%s", (cid,))
        assert cursor.fetchone()[0] == "signatures_pending"


def test_simulation_marker_is_explicit_and_absent_from_ordinary_documents():
    from flask import Flask

    import document_generator

    web = Flask(__name__)
    values = {
        "community_name": "Sonnwil",
        "municipality": "Sonnwil",
        "date_str": "2026-11-01",
        "participant_rows": "",
        "signature_rows": "",
        "distribution_label": "",
    }
    with web.app_context():
        ordinary = document_generator.render_document_template(
            "documents/community_agreement.html", **values
        )
        web.config["SANDBOX_SIMULATION"] = True
        simulation = document_generator.render_document_template(
            "documents/community_agreement.html", **values
        )
    assert "SIMULATION SONNWIL" not in ordinary
    assert "SIMULATION SONNWIL" in simulation
    assert "Keine rechtsgültige Unterschrift" in simulation


def _signed(client):
    """Collect all three local receipts and return the founder session."""
    import re

    from tests.test_sandbox_personas import _switch

    prefix, cid = _create_ready(client)
    for role in ("founder", "resident", "producer"):
        _switch(client, prefix, role)
        page = client.get(prefix + "/sandbox/formation?cid=" + cid)
        bundle = re.search(r'name="bundle" value="([a-f0-9]{64})"', page.text).group(1)
        assert (
            client.post(
                prefix + f"/sandbox/formation/{cid}/signatures",
                data={"csrf_token": _csrf(page), "bundle": bundle, "confirm": "yes"},
            ).status_code
            == 302
        )
    _switch(client, prefix, "founder")
    return prefix, cid


def test_vnb_receipt_never_authorizes_start_and_response_replays_are_safe(village):
    from tests.test_sandbox_onboarding import _workspace_connection
    from tests.test_sandbox_personas import _switch

    client = village.test_client()
    prefix, cid = _signed(client)
    guide = prefix + "/sandbox/formation?cid=" + cid
    page = client.get(guide)
    path = prefix + f"/sandbox/formation/{cid}/vnb"
    fields = {"csrf_token": _csrf(page), "state": "delivered"}
    assert client.post(path, data=fields).status_code == 302
    with (
        _workspace_connection(village, client) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT case_id FROM vnb_submission_cases WHERE community_id=%s", (cid,)
        )
        case = cursor.fetchone()[0]
    page = _switch(client, prefix, "resident")
    assert (
        client.post(
            path,
            data={"csrf_token": _csrf(page), "state": "acknowledged", "case_id": case},
        ).status_code
        == 403
    )
    _switch(client, prefix, "founder")
    fields["csrf_token"] = _csrf(client.get(guide))
    assert (
        client.post(
            path, data={**fields, "state": "acknowledged", "case_id": case}
        ).status_code
        == 302
    )
    assert (
        client.post(
            path, data={**fields, "state": "acknowledged", "case_id": case}
        ).status_code
        == 302
    )
    assert (
        client.post(
            prefix + f"/leg/community/{cid}/activate",
            data={"csrf_token": fields["csrf_token"]},
        ).status_code
        == 409
    )
    with (
        _workspace_connection(village, client) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT status, activated_at, vnb_start_confirmation FROM communities WHERE community_id=%s",
            (cid,),
        )
        assert cursor.fetchone() == ("dso_submitted", None, None)
        cursor.execute(
            "SELECT state, response_evidence FROM vnb_submission_cases WHERE case_id=%s",
            (case,),
        )
        state, evidence = cursor.fetchone()
        assert state == "acknowledged" and b"SIMULATION" in bytes(evidence)
        cursor.execute(
            "SELECT COUNT(*) FROM operator_events WHERE community_id=%s AND event_type='formation.submission.acknowledged'",
            (cid,),
        )
        assert cursor.fetchone()[0] == 1
    assert "Empfang" in client.get(guide).text


def test_rejection_is_final_audited_and_keeps_formation_inactive(village):
    from tests.test_sandbox_onboarding import _workspace_connection

    client = village.test_client()
    prefix, cid = _signed(client)
    page = client.get(prefix + "/sandbox/formation?cid=" + cid)
    path = prefix + f"/sandbox/formation/{cid}/vnb"
    fields = {"csrf_token": _csrf(page), "state": "delivered"}
    assert client.post(path, data=fields).status_code == 302
    with (
        _workspace_connection(village, client) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT case_id FROM vnb_submission_cases WHERE community_id=%s", (cid,)
        )
        case = cursor.fetchone()[0]
    rejected = {**fields, "state": "rejected", "case_id": case}
    assert client.post(path, data=rejected).status_code == 302
    assert client.post(path, data=rejected).status_code == 302
    assert (
        client.post(path, data={**rejected, "state": "acknowledged"}).status_code == 409
    )
    assert (
        client.post(prefix + f"/leg/community/{cid}/activate", data=fields).status_code
        == 409
    )
    assert (
        "Beginnen Sie mit einer neuen LEG"
        in client.get(prefix + "/sandbox/formation?cid=" + cid).text
    )
    with (
        _workspace_connection(village, client) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT state, response_evidence, next_action FROM vnb_submission_cases WHERE case_id=%s",
            (case,),
        )
        state, evidence, action = cursor.fetchone()
        assert (
            state == "rejected"
            and b"SIMULATION" in bytes(evidence)
            and action == "review_rejection"
        )
        cursor.execute(
            "SELECT COUNT(*) FROM operator_events WHERE community_id=%s AND event_type='formation.submission.rejected'",
            (cid,),
        )
        assert cursor.fetchone()[0] == 1
        cursor.execute(
            "SELECT activated_at, vnb_start_confirmation FROM communities WHERE community_id=%s",
            (cid,),
        )
        assert cursor.fetchone() == (None, None)


def test_roles_and_private_documents_keep_ordinary_authority(village):
    import re

    from tests.test_sandbox_onboarding import _workspace_connection
    from tests.test_sandbox_personas import _switch

    client = village.test_client()
    prefix, cid = _signed(client)
    signature_bundle = re.search(
        r'name="bundle" value="([a-f0-9]{64})"',
        client.get(prefix + "/sandbox/formation?cid=" + cid).text,
    ).group(1)
    page = client.get(prefix + "/leg/dashboard?cid=" + cid)
    fields = {"csrf_token": _csrf(page)}
    roles = prefix + f"/leg/community/{cid}/members/sonnwil-resident/roles"
    assert (
        client.post(
            roles, data={**fields, "roles": ["metering", "billing_preparer"]}
        ).status_code
        == 302
    )
    with (
        _workspace_connection(village, client) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT id FROM leg_documents WHERE community_id=%s ORDER BY id", (cid,)
        )
        documents = [row[0] for row in cursor.fetchall()]
        cursor.execute(
            "SELECT access_roles FROM community_members WHERE community_id=%s AND building_id='sonnwil-resident'",
            (cid,),
        )
        assert set(cursor.fetchone()[0]) == {"metering", "billing_preparer"}
    for doc in documents:
        response = client.get(prefix + f"/leg/document/{doc}")
        assert response.status_code == 200 and response.data.startswith(b"%PDF")
    _switch(client, prefix, "resident")
    page = client.get(prefix + "/sandbox/formation?cid=" + cid)
    fields = {"csrf_token": _csrf(page)}
    assert client.post(roles, data={**fields, "roles": ["admin"]}).status_code == 400
    assert (
        client.post(
            prefix + f"/sandbox/formation/{cid}/vnb",
            data={**fields, "state": "acknowledged"},
        ).status_code
        == 403
    )
    assert (
        client.get(prefix + f"/sandbox/formation/{cid}/start-evidence.pdf").status_code
        == 403
    )
    assert (
        client.post(prefix + f"/leg/community/{cid}/activate", data=fields).status_code
        == 403
    )
    page = _switch(client, prefix, "operator")
    assert client.get(prefix + "/sandbox/formation?cid=" + cid).status_code == 404
    assert (
        client.post(
            prefix + f"/sandbox/formation/{cid}/signatures",
            data={
                "csrf_token": _csrf(page),
                "bundle": signature_bundle,
                "confirm": "yes",
            },
        ).status_code
        == 403
    )
    for doc in documents:
        assert client.get(prefix + f"/leg/document/{doc}").status_code == 404
    other = village.test_client()
    other_prefix, _ = _start(other, "founder")
    assert other.get(other_prefix + "/sandbox/formation?cid=" + cid).status_code == 404
    for doc in documents:
        assert other.get(other_prefix + f"/leg/document/{doc}").status_code == 404
    with (
        _workspace_connection(village, client) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT COUNT(*) FROM vnb_submission_cases WHERE community_id=%s", (cid,)
        )
        assert cursor.fetchone()[0] == 0
        cursor.execute(
            "SELECT access_roles FROM community_members WHERE community_id=%s AND building_id='sonnwil-resident'",
            (cid,),
        )
        assert set(cursor.fetchone()[0]) == {"metering", "billing_preparer"}


def test_external_simulation_adapters_are_not_installed_outside_a_worker(monkeypatch):
    from flask import Flask

    import sandbox_formation
    import vnb_exchange

    monkeypatch.delenv("OPENLEG_SANDBOX_WORKER", raising=False)
    before = dict(vnb_exchange.ADAPTERS)
    web = Flask(__name__)
    with pytest.raises(RuntimeError, match="requires a village worker"):
        sandbox_formation.install(web)
    assert vnb_exchange.ADAPTERS == before
    assert not any(
        rule.rule.startswith("/sandbox/") for rule in web.url_map.iter_rules()
    )


def _activate(village, client):
    """Record local start evidence through the ordinary confirmation controls."""
    import io
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from tests.test_sandbox_onboarding import _workspace_connection

    prefix, cid = _signed(client)
    guide = prefix + "/sandbox/formation?cid=" + cid
    page = client.get(guide)
    fields = {"csrf_token": _csrf(page)}
    assert (
        client.post(
            prefix + f"/sandbox/formation/{cid}/vnb",
            data={**fields, "state": "acknowledged"},
        ).status_code
        == 302
    )
    evidence = client.get(prefix + f"/sandbox/formation/{cid}/start-evidence.pdf")
    assert evidence.status_code == 200
    assert evidence.data.startswith(b"%PDF")
    assert "simulation" in evidence.headers["Content-Disposition"]
    assert (
        client.post(
            prefix + f"/leg/community/{cid}/correspondence",
            data={
                **fields,
                "direction": "in",
                "channel": "email",
                "counterparty": "Sonnwil Energie (Simulation)",
                "subject": "SIMULATION SONNWIL Startbestätigung",
                "notes": "Erfundener Beleg, keine reale Bewilligung",
                "attachment": (io.BytesIO(evidence.data), "simulation-start.pdf"),
            },
            content_type="multipart/form-data",
        ).status_code
        == 302
    )
    with (
        _workspace_connection(village, client) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT id FROM correspondence_log WHERE community_id=%s ORDER BY id DESC LIMIT 1",
            (cid,),
        )
        entry = cursor.fetchone()[0]
    today = datetime.now(ZoneInfo("Europe/Zurich")).date().isoformat()
    confirmation = {
        **fields,
        "correspondence_id": str(entry),
        "effective_date": today,
        "reference": "SIMULATION-START-" + cid,
        "confirm_start": "yes",
    }
    start = prefix + f"/leg/community/{cid}/vnb-start-confirmation"
    assert client.post(start, data=confirmation).status_code == 302
    assert client.post(start, data=confirmation).status_code == 302
    activation = prefix + f"/leg/community/{cid}/activate"
    assert client.post(activation, data=fields).status_code == 302
    assert client.post(activation, data=fields).status_code == 302
    with (
        _workspace_connection(village, client) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT status, activated_at, vnb_start_confirmation FROM communities WHERE community_id=%s",
            (cid,),
        )
        status, activated, proof = cursor.fetchone()
        assert status == "active" and activated is not None
        assert proof["correspondence_id"] == entry
        assert proof["reference"] == "SIMULATION-START-" + cid

    return prefix, cid


def test_synthetic_start_attachment_uses_normal_confirmation_and_activation(village):
    _activate(village, village.test_client())


def test_changed_signer_set_cannot_reuse_a_completed_bundle(village):
    from tests.test_sandbox_personas import _switch

    client = village.test_client()
    prefix, cid = _signed(client)
    dashboard = client.get(prefix + "/leg/dashboard?cid=" + cid)
    assert (
        client.post(
            prefix + f"/leg/community/{cid}/invite",
            data={
                "csrf_token": _csrf(dashboard),
                "invite_email": "sonnwil-operator@example.org",
            },
        ).status_code
        == 302
    )
    _switch(client, prefix, "operator")
    page = client.get(prefix + "/leg/dashboard?cid=" + cid)
    assert (
        client.post(
            prefix + f"/leg/community/{cid}/confirm", data={"csrf_token": _csrf(page)}
        ).status_code
        == 302
    )
    _switch(client, prefix, "founder")
    dashboard = client.get(prefix + "/leg/dashboard?cid=" + cid)
    assert (
        client.post(
            prefix + f"/leg/community/{cid}/vnb-submissions",
            data={"csrf_token": _csrf(dashboard)},
        ).status_code
        == 409
    )


def test_new_participant_requires_new_drafts_before_any_signature(village):
    import re

    from tests.test_sandbox_personas import _switch

    client = village.test_client()
    prefix, cid = _create_ready(client)
    guide = prefix + "/sandbox/formation?cid=" + cid
    page = client.get(guide)
    previous = re.search(r'name="bundle" value="([a-f0-9]{64})"', page.text).group(1)
    assert (
        client.post(
            prefix + f"/leg/community/{cid}/invite",
            data={
                "csrf_token": _csrf(page),
                "invite_email": "sonnwil-operator@example.org",
            },
        ).status_code
        == 302
    )
    _switch(client, prefix, "operator")
    dashboard = client.get(prefix + "/leg/dashboard?cid=" + cid)
    assert (
        client.post(
            prefix + f"/leg/community/{cid}/confirm",
            data={"csrf_token": _csrf(dashboard)},
        ).status_code
        == 302
    )
    page = client.get(guide)
    assert 'name="bundle"' not in page.text
    assert (
        client.post(
            prefix + f"/sandbox/formation/{cid}/signatures",
            data={"csrf_token": _csrf(dashboard), "bundle": previous, "confirm": "yes"},
        ).status_code
        == 409
    )
    _switch(client, prefix, "founder")
    dashboard = client.get(prefix + "/leg/dashboard?cid=" + cid)
    assert (
        client.post(
            prefix + f"/leg/community/{cid}/documents",
            data={"csrf_token": _csrf(dashboard)},
        ).status_code
        == 302
    )
    assert 'name="bundle"' in client.get(guide).text


def test_join_proposal_replays_after_separate_membership_confirmation(village):
    import io
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from tests.test_sandbox_onboarding import _workspace_connection
    from tests.test_sandbox_personas import _switch

    client = village.test_client()
    prefix, cid = _activate(village, client)
    dashboard = client.get(prefix + "/leg/dashboard?cid=" + cid)
    fields = {"csrf_token": _csrf(dashboard)}
    assert (
        client.post(
            prefix + f"/leg/community/{cid}/invite",
            data={**fields, "invite_email": "sonnwil-operator@example.org"},
        ).status_code
        == 302
    )
    date = (
        datetime.now(ZoneInfo("Europe/Zurich")).date() + timedelta(days=7)
    ).isoformat()
    agreement = client.get(
        prefix
        + f"/sandbox/formation/{cid}/join-agreement.pdf?participant=sonnwil-operator"
    )
    assert agreement.status_code == 200 and agreement.data.startswith(b"%PDF")
    assert "simulation-sonnwil" in agreement.headers["Content-Disposition"]
    assert (
        client.post(
            prefix + f"/leg/community/{cid}/correspondence",
            data={
                **fields,
                "direction": "out",
                "channel": "email",
                "counterparty": "Sam (Simulation)",
                "subject": "SIMULATION Teilnehmervertrag",
                "notes": "Erfundener Beitrittsvertrag",
                "attachment": (
                    io.BytesIO(agreement.data),
                    "simulation-sonnwil-join.pdf",
                ),
            },
            content_type="multipart/form-data",
        ).status_code
        == 302
    )
    with (
        _workspace_connection(village, client) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT id FROM correspondence_log WHERE community_id=%s ORDER BY id DESC LIMIT 1",
            (cid,),
        )
        agreement_id = cursor.fetchone()[0]
    proposal = {
        **fields,
        "mutation_id": "SIMULATION-JOIN-" + cid,
        "participant_id": "sonnwil-operator",
        "mutation_type": "join",
        "effective_date": date,
        "source_agreement_id": "correspondence:" + str(agreement_id),
    }
    path = prefix + f"/leg/community/{cid}/vnb-mutations"
    assert client.post(path, data=proposal).status_code == 302
    with (
        _workspace_connection(village, client) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT status FROM community_members WHERE community_id=%s AND building_id='sonnwil-operator'",
            (cid,),
        )
        assert cursor.fetchone()[0] == "invited"
    with (
        _workspace_connection(village, client) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT mutation_id FROM vnb_mutation_cases WHERE community_id=%s", (cid,)
        )
        mutation_id = cursor.fetchone()[0]
    response_path = prefix + f"/sandbox/formation/{cid}/mutation-response"
    assert (
        client.post(
            response_path,
            data={**fields, "mutation_id": mutation_id, "state": "acknowledged"},
        ).status_code
        == 302
    )
    assert (
        client.post(
            response_path,
            data={**fields, "mutation_id": mutation_id, "state": "acknowledged"},
        ).status_code
        == 302
    )
    with (
        _workspace_connection(village, client) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT status FROM community_members WHERE community_id=%s AND building_id='sonnwil-operator'",
            (cid,),
        )
        assert cursor.fetchone()[0] == "invited"
    _switch(client, prefix, "operator")
    dashboard = client.get(prefix + "/leg/dashboard?cid=" + cid)
    assert (
        client.post(
            prefix + f"/leg/community/{cid}/confirm",
            data={"csrf_token": _csrf(dashboard)},
        ).status_code
        == 302
    )
    _switch(client, prefix, "founder")
    dashboard = client.get(prefix + "/leg/dashboard?cid=" + cid)
    assert (
        client.post(path, data={**proposal, "csrf_token": _csrf(dashboard)}).status_code
        == 302
    )
    with (
        _workspace_connection(village, client) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT COUNT(*), MIN(effective_date), MIN(before_facts->>'status'), MIN(after_facts->>'status') FROM vnb_mutation_cases WHERE community_id=%s",
            (cid,),
        )
        count, stored_date, before, after = cursor.fetchone()
        assert (count, stored_date.isoformat(), before, after) == (
            1,
            date,
            "invited",
            "confirmed",
        )
