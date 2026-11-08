# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fictional login choices through isolated workers and real product routes."""

import re

import pytest

from tests import test_sandbox_http

village = test_sandbox_http.village
_csrf = test_sandbox_http._csrf


def test_visitor_can_choose_a_named_administrator_before_signing_in(village):
    client = village.test_client()
    entry = client.get("/sandbox")
    for persona in (
        "resident",
        "producer",
        "founder",
        "administrator",
        "operator",
        "municipality",
        "utility",
    ):
        assert f'value="{persona}"' in entry.text
    assert "LEG-Administration" in entry.text
    assert "Musterweg 1" in entry.text
    response = client.post(
        "/sandbox/start", data={"csrf_token": _csrf(entry), "persona": "administrator"}
    )
    assert response.status_code == 302
    prefix = response.location.removesuffix("/sandbox/enter")
    assert re.fullmatch(r"/v/[a-f0-9]{32}", prefix)
    page = client.get(response.location, follow_redirects=True)
    assert page.status_code == 200
    assert page.request.path.endswith("/leg/dashboard")
    assert "LEG Sonnwil" in page.text
    assert "LEG-Administration" in page.text
    assert "sonnwil-neighbour@example.org" in page.text
    assert (
        client.get(prefix + "/dashboard/export").json["annual_consumption_kwh"] == 4800
    )
    assert client.get(prefix + "/admin/api/stats").status_code in (403, 404)


def _start(client, persona):
    entry = client.get("/sandbox")
    response = client.post(
        "/sandbox/start", data={"csrf_token": _csrf(entry), "persona": persona}
    )
    assert response.status_code == 302
    prefix = response.location.removesuffix("/sandbox/enter")
    return prefix, client.get(response.location, follow_redirects=True)


def _switch(client, prefix, persona):
    chooser = client.get(prefix + "/sandbox/accounts")
    assert chooser.status_code == 200
    response = client.post(
        prefix + "/sandbox/persona",
        data={"csrf_token": _csrf(chooser), "persona": persona},
    )
    assert response.status_code == 302
    return client.get(response.location, follow_redirects=True)


def test_admin_to_resident_switch_preserves_data_and_denies_stale_privileged_forms(
    village,
):
    client = village.test_client()
    prefix, admin = _start(client, "administrator")
    old_csrf = _csrf(admin)
    policy_path = prefix + "/leg/community/sonnwil-operating-leg/access-policy"
    assert (
        client.post(
            policy_path, data={"csrf_token": old_csrf, "require_dual_control": "yes"}
        ).status_code
        == 302
    )
    profile = client.get(prefix + "/dashboard")
    assert (
        client.post(
            prefix + "/dashboard/profile",
            data={
                "csrf_token": _csrf(profile),
                "annual_consumption_kwh": "7900",
                "potential_pv_kwp": "8",
            },
        ).status_code
        == 302
    )
    resident = _switch(client, prefix, "resident")
    assert resident.status_code == 200
    assert "Lea Muster" in resident.text
    new_csrf = _csrf(resident)
    assert new_csrf != old_csrf
    assert client.post(policy_path, data={"csrf_token": old_csrf}).status_code == 400
    assert client.post(policy_path, data={"csrf_token": new_csrf}).status_code == 403
    assert (
        client.get(prefix + "/dashboard/export").json["annual_consumption_kwh"] == 4500
    )
    restored = _switch(client, prefix, "administrator")
    assert 'name="require_dual_control" value="yes" checked' in restored.text
    assert (
        client.get(prefix + "/dashboard/export").json["annual_consumption_kwh"] == 7900
    )


def test_persona_change_rejects_forgery_unknown_accounts_and_old_entry_links(village):
    client = village.test_client()
    prefix, _admin = _start(client, "administrator")
    chooser = client.get(prefix + "/sandbox/accounts")
    token = _csrf(chooser)
    for data in (
        {"persona": "administrator"},
        {"csrf_token": "é", "persona": "administrator"},
        {"csrf_token": token, "persona": "platform-admin"},
    ):
        assert client.post(prefix + "/sandbox/persona", data=data).status_code == 400
    assert "Robin Muster" in client.get(prefix + "/sandbox/home").text
    resident = _switch(client, prefix, "resident")
    assert "Lea Muster" in resident.text
    old = client.get(
        prefix + "/sandbox/enter",
        headers={"X-OpenLEG-Initial-Persona": "administrator"},
        follow_redirects=True,
    )
    assert "Lea Muster" in old.text
    assert "Robin Muster" not in old.text
    assert (
        client.post(
            prefix + "/sandbox/persona",
            data={"csrf_token": token, "persona": "administrator"},
        ).status_code
        == 400
    )
    profile = client.get(prefix + "/dashboard")
    assert (
        client.post(
            prefix + "/leg/community/sonnwil-operating-leg/access-policy",
            data={"csrf_token": _csrf(profile), "require_dual_control": "yes"},
        ).status_code
        == 403
    )


def test_resident_utility_municipality_switch_replaces_authority_and_keeps_settings(
    village,
):
    client = village.test_client()
    prefix, resident = _start(client, "resident")
    household_token = _csrf(resident)
    utility = _switch(client, prefix, "utility")
    assert utility.status_code == 200
    assert re.search(r"<aside[^>]*>.*Kim Muster.*</aside>", utility.text, re.DOTALL)
    assert "Lea Muster" not in utility.text
    assert client.get(prefix + "/dashboard/export").status_code == 401
    settings = client.get(prefix + "/utility/settings")
    utility_token = _csrf(settings)
    assert utility_token != household_token
    assert (
        client.post(
            prefix + "/utility/settings",
            data={"csrf_token": household_token, "preferred_locale": "fr_CH"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            prefix + "/utility/settings",
            data={"csrf_token": utility_token, "preferred_locale": "fr_CH"},
        ).status_code
        == 302
    )
    municipality = _switch(client, prefix, "municipality")
    assert municipality.status_code == 200
    assert "Jules Beispiel" in municipality.text
    assert "Kim Muster" not in municipality.text
    assert 'data-dashboard-structure="municipality-overview"' in municipality.text
    assert 'class="font-mono text-2xl font-bold">6</p>' in municipality.text
    assert client.get(prefix + "/utility/dashboard").status_code == 302
    assert client.get(prefix + "/dashboard/export").status_code == 401
    assert (
        client.post(
            prefix + "/utility/settings",
            data={"csrf_token": utility_token, "preferred_locale": "de_CH"},
        ).status_code
        == 302
    )
    german = client.get(prefix + "/gemeinde/dashboard?lang=de-CH")
    assert '<html lang="de-CH"' in german.text
    assert '<html lang="de-CH"' in client.get(prefix + "/gemeinde/dashboard").text
    restored = _switch(client, prefix, "utility")
    assert "Kim Muster" in restored.text
    settings = client.get(prefix + "/utility/settings")
    assert 'value="fr_CH" selected' in settings.text
    assert _csrf(settings) != utility_token
    assert (
        client.post(
            prefix + "/gemeinde/logout", data={"csrf_token": _csrf(municipality)}
        ).status_code
        == 401
    )


def _connection(village, prefix):
    import psycopg2
    from psycopg2.extensions import make_dsn

    manager = village.extensions["villages"]
    workspace = manager.workspaces[prefix.rsplit("/", 1)[1]]
    return psycopg2.connect(make_dsn(manager.postgres.dsn, dbname=workspace["name"]))


def test_limited_operator_can_prepare_battery_but_not_change_access_or_foreign_leg(
    village,
):
    client = village.test_client()
    prefix, operator = _start(client, "operator")
    token = _csrf(operator)
    community = prefix + "/leg/community/sonnwil-operating-leg"
    battery = {
        "csrf_token": token,
        "participant_id": "sonnwil-battery",
        "capacity_kwh": "45",
        "annual_cost_chf": "960",
        **{
            f"share:sonnwil-{name}": "25"
            for name in ("resident", "producer", "operator", "neighbour")
        },
    }
    assert client.post(community + "/battery", data=battery).status_code == 302
    assert (
        client.post(
            community + "/access-policy", data={"csrf_token": token}
        ).status_code
        == 403
    )
    assert (
        client.post(
            prefix + "/leg/community/sonnwil-private-leg/battery", data=battery
        ).status_code
        == 403
    )
    with _connection(village, prefix) as conn, conn.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) FROM community_batteries WHERE community_id='sonnwil-operating-leg'"
        )
        assert cursor.fetchone()[0] == 1
    member = _switch(client, prefix, "resident")
    battery["csrf_token"] = _csrf(member)
    assert client.post(community + "/battery", data=battery).status_code == 403


def test_real_foreign_invoice_equipment_attachment_and_community_stay_private(village):
    """Changing persona does not expose another member's invoices, equipment or evidence."""
    from copy import deepcopy

    from psycopg2.extras import Json

    from tests.test_billing_member_invoices import INVOICE_ROW

    client = village.test_client()
    prefix, producer = _start(client, "producer")
    created = client.post(
        prefix + "/api/equipment/v1/records",
        headers={"X-CSRF-Token": _csrf(producer)},
        json={"manufacturer": "Fictional producer equipment", "model": "Sonnwil1"},
    )
    assert created.status_code == 201
    equipment_id = created.json["record"]["id"]
    assert (
        client.get(prefix + "/api/equipment/v1/records/" + equipment_id).status_code
        == 200
    )
    invoice = deepcopy(INVOICE_ROW)
    invoice["policy_snapshot"]["network_level"] = "same"
    invoice.update(
        community_id="sonnwil-operating-leg",
        participant_id="sonnwil-producer",
        invoice_number="SONNWIL-PRIVATE-1",
    )
    invoice["provenance_snapshot"]["issuer"] = {
        "community_id": "sonnwil-operating-leg",
        "name": "LEG Sonnwil",
    }
    for item in invoice["line_items_snapshot"]:
        item["participant_id"] = "sonnwil-producer"
    columns = [key for key in invoice if key != "id"]
    with _connection(village, prefix) as conn, conn.cursor() as cursor:
        # This fixture represents a new invoice for the original seeded owner.
        cursor.execute(
            "SELECT referral_code FROM buildings WHERE building_id=%s",
            ("sonnwil-producer",),
        )
        invoice["provenance_snapshot"]["participant_registration_id"] = (
            cursor.fetchone()[0]
        )
        cursor.execute(
            "INSERT INTO invoices("
            + ",".join(columns)
            + ") VALUES ("
            + ",".join(["%s"] * len(columns))
            + ") RETURNING id",
            [
                Json(invoice[key])
                if isinstance(invoice[key], (dict, list))
                else invoice[key]
                for key in columns
            ],
        )
        invoice_id = cursor.fetchone()[0]
        attachments = []
        for community in ("sonnwil-operating-leg", "sonnwil-private-leg"):
            cursor.execute(
                """INSERT INTO correspondence_log(community_id,direction,channel,subject,attachment_filename,attachment_mime,attachment_data)
                   VALUES (%s,'in','email','Synthetic private attachment','fictional.pdf','application/pdf',%s) RETURNING id""",
                (community, b"%PDF-fictional-correspondence"),
            )
            attachments.append(cursor.fetchone()[0])
    invoice_path = prefix + f"/dashboard/invoices/{invoice_id}"
    assert client.get(invoice_path).status_code == 200
    own_attachment = (
        prefix
        + f"/leg/community/sonnwil-operating-leg/correspondence/{attachments[0]}/attachment"
    )
    foreign_attachment = (
        prefix
        + f"/leg/community/sonnwil-private-leg/correspondence/{attachments[1]}/attachment"
    )
    assert client.get(own_attachment).data == b"%PDF-fictional-correspondence"
    for persona in (
        "resident",
        "founder",
        "administrator",
        "operator",
        "municipality",
        "utility",
    ):
        active = _switch(client, prefix, persona)
        assert client.get(invoice_path).status_code in (401, 404)
        assert client.get(invoice_path + "/pdf").status_code in (401, 404)
        assert client.get(
            prefix + "/api/equipment/v1/records/" + equipment_id
        ).status_code in (401, 404)
        assert client.get(foreign_attachment).status_code == 404
        if persona not in ("municipality", "utility"):
            token = _csrf(active)
            record_path = prefix + "/api/equipment/v1/records/" + equipment_id
            assert (
                client.put(
                    record_path,
                    headers={"X-CSRF-Token": token},
                    json={"manufacturer": "Forbidden change", "model": "Sonnwil1"},
                ).status_code
                == 404
            )
            assert (
                client.delete(record_path, headers={"X-CSRF-Token": token}).status_code
                == 404
            )
            assert (
                client.post(
                    prefix + "/leg/community/sonnwil-private-leg/access-policy",
                    data={"csrf_token": token, "require_dual_control": "yes"},
                ).status_code
                == 403
            )
        # A valid attachment ID cannot be transplanted into an entitled LEG.
        assert (
            client.get(
                own_attachment.replace(
                    str(attachments[0]) + "/attachment",
                    str(attachments[1]) + "/attachment",
                )
            ).status_code
            == 404
        )
        denied = client.get(prefix + "/leg/dashboard?cid=sonnwil-private-leg")
        assert denied.status_code == 200  # The ordinary product renders an error view.
        assert "LEG Privat (Simulation)" not in denied.text
        assert "sonnwil-private@example.org" not in denied.text
    _switch(client, prefix, "producer")
    assert client.get(invoice_path).status_code == 200
    assert (
        client.get(prefix + "/api/equipment/v1/records/" + equipment_id).json["record"]
        == created.json["record"]
    )


def test_utility_demo_credentials_are_private_persistent_and_revocable(village):
    client = village.test_client()
    prefix, utility = _start(client, "utility")
    old_token = _csrf(utility)
    created = client.post(
        prefix + "/utility/api-key", headers={"X-CSRF-Token": old_token}
    )
    assert created.status_code == 200
    raw_key = created.json["api_key"]
    assert raw_key not in client.get(prefix + "/utility/dashboard").text
    member = _switch(client, prefix, "resident")
    assert (
        client.post(
            prefix + "/utility/api-key/revoke", data={"csrf_token": old_token}
        ).status_code
        == 302
    )
    assert "Lea Muster" in member.text
    utility = _switch(client, prefix, "utility")
    token = _csrf(utility)
    assert token != old_token
    assert "✓ API-Schlüssel aktiv" in utility.text
    assert (
        client.post(
            prefix + "/utility/api-key/revoke", data={"csrf_token": old_token}
        ).status_code
        == 403
    )
    assert (
        client.post(
            prefix + "/utility/api-key/revoke", data={"csrf_token": token}
        ).status_code
        == 302
    )
    assert (
        "Noch kein API-Schlüssel vorhanden."
        in client.get(prefix + "/utility/dashboard").text
    )


def test_founder_scenario_explains_and_creates_a_community_through_the_product(village):
    client = village.test_client()
    prefix, form = _start(client, "founder")
    home = client.get(prefix + "/sandbox/home")
    assert "noch keine eigene LEG" in home.text
    assert "Verteilmodell" in home.text
    assert "LEG-Dashboard" in home.text
    created = client.post(
        prefix + "/leg/community/create",
        data={
            "csrf_token": _csrf(form),
            "name": "LEG Fiktiver Sonnenweg",
            "distribution_model": "simple",
        },
        follow_redirects=True,
    )
    assert created.status_code == 200
    assert "LEG Fiktiver Sonnenweg" in created.text
    assert "Mira Muster" in created.text


def test_french_scenario_instructions_follow_the_selected_display_language(village):
    client = village.test_client()
    prefix, _ = _start(client, "resident")
    home = client.get(prefix + "/sandbox/home?lang=fr-CH")
    assert "Vous êtes connecté comme foyer du Musterweg 1." in home.text
    assert "Modifiez la consommation annuelle" in home.text
    assert "Votre consommation annuelle enregistrée reste visible" in home.text
    assert "Ändern Sie den Jahresverbrauch" not in home.text


def test_intervening_asset_request_does_not_consume_the_selected_initial_account(
    village,
):
    client = village.test_client()
    entry = client.get("/sandbox")
    started = client.post(
        "/sandbox/start", data={"csrf_token": _csrf(entry), "persona": "administrator"}
    )
    prefix = started.location.removesuffix("/sandbox/enter")
    assert client.get(prefix + "/static/css/openleg.css").status_code == 200
    dashboard = client.get(started.location, follow_redirects=True)
    assert "Robin Muster" in dashboard.text
    assert dashboard.request.path.endswith("/leg/dashboard")


def _reload_worker(village, prefix):
    """Wait for a fresh product worker using the same persisted village files."""
    import signal
    import time
    from pathlib import Path

    manager = village.extensions["villages"]
    workspace = manager.workspaces[prefix.rsplit("/", 1)[1]]
    master = workspace["process"]
    children = Path(f"/proc/{master.pid}/task/{master.pid}/children")
    original = set(children.read_text().split())
    master.send_signal(signal.SIGHUP)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        current = set(children.read_text().split())
        if current and not current.intersection(original):
            return
        time.sleep(0.05)
    pytest.fail("product worker did not restart")


def test_copied_pre_downgrade_cookie_never_restores_admin_authority_even_after_reload(
    village,
):
    client = village.test_client()
    prefix, admin = _start(client, "administrator")
    cookie = client.get_cookie("openleg_product", path=prefix).value
    csrf = _csrf(admin)
    resident = _switch(client, prefix, "resident")
    current_cookie = client.get_cookie("openleg_product", path=prefix).value
    policy = prefix + "/leg/community/sonnwil-operating-leg/access-policy"
    assert client.post(policy, data={"csrf_token": _csrf(resident)}).status_code == 403
    for restart in (False, True):
        if restart:
            _reload_worker(village, prefix)
        client.set_cookie("openleg_product", cookie, path=prefix)
        assert client.post(policy, data={"csrf_token": csrf}).status_code in (
            400,
            401,
            403,
        )
        denied = client.get(prefix + "/leg/dashboard?cid=sonnwil-operating-leg")
        assert 'name="require_dual_control"' not in denied.text
        client.set_cookie("openleg_product", current_cookie, path=prefix)
        assert "Lea Muster" in client.get(prefix + "/sandbox/home").text


def test_ordinary_captured_magic_login_retires_the_prior_persona_cookie(village):
    from html import unescape

    client = village.test_client()
    prefix, resident = _start(client, "resident")
    old_cookie = client.get_cookie("openleg_product", path=prefix).value
    assert _csrf(resident)
    assert (
        client.post(
            prefix + "/utility/login", json={"email": "sonnwil-utility@example.org"}
        ).status_code
        == 200
    )
    inbox = client.get(prefix + "/sandbox/inbox")
    link = unescape(
        re.search(r'href="(' + prefix + r'/utility/access/[^\"]+)"', inbox.text).group(
            1
        )
    )
    utility = client.get(link, follow_redirects=True)
    assert "Kim Muster" in utility.text
    assert "Lea Muster" not in utility.text
    current_cookie = client.get_cookie("openleg_product", path=prefix).value
    assert client.get(prefix + "/dashboard/export").status_code == 401
    client.set_cookie("openleg_product", old_cookie, path=prefix)
    assert client.get(prefix + "/dashboard/export").status_code == 401
    client.set_cookie("openleg_product", current_cookie, path=prefix)
    settings = client.get(prefix + "/utility/settings")
    assert settings.status_code == 200
    dashboard = client.get(prefix + "/utility/dashboard")
    assert dashboard.status_code == 200
    assert client.get(prefix + "/utility/logout").status_code == 405
    assert (
        client.post(
            prefix + "/utility/logout", data={"csrf_token": _csrf(dashboard)}
        ).status_code
        == 302
    )
    client.set_cookie("openleg_product", current_cookie, path=prefix)
    assert client.get(prefix + "/utility/dashboard").status_code == 302


def test_missing_server_binding_refuses_writes_without_changing_community(village):
    client = village.test_client()
    prefix, admin = _start(client, "administrator")
    manager = village.extensions["villages"]
    workspace = manager.workspaces[prefix.rsplit("/", 1)[1]]
    (workspace["directory"] / ".session-generation").unlink()
    assert (
        client.post(
            prefix + "/leg/community/sonnwil-operating-leg/access-policy",
            data={"csrf_token": _csrf(admin), "require_dual_control": "yes"},
        ).status_code
        == 503
    )
    with _connection(village, prefix) as conn, conn.cursor() as cursor:
        cursor.execute(
            "SELECT require_dual_control FROM communities WHERE community_id='sonnwil-operating-leg'"
        )
        assert cursor.fetchone()[0] is False


@pytest.mark.parametrize("method", ["get", "head"])
@pytest.mark.parametrize(
    "persona,request_path,email,link_path,name",
    [
        (
            "resident",
            "/dashboard/access/request",
            "sonnwil-resident@example.org",
            "/dashboard/access/",
            "Lea Muster",
        ),
        (
            "utility",
            "/utility/login",
            "sonnwil-utility@example.org",
            "/utility/access/",
            "Kim Muster",
        ),
        (
            "municipality",
            "/gemeinde/access/request",
            "sonnwil-municipality@example.org",
            "/gemeinde/access/",
            "Jules Beispiel",
        ),
    ],
)
def test_same_account_magic_login_retires_copied_cookie_and_stale_csrf(
    village, persona, request_path, email, link_path, name, method
):
    from html import unescape

    client = village.test_client()
    prefix, _ = _start(client, persona)
    chooser = client.get(prefix + "/sandbox/accounts")
    old_csrf = _csrf(chooser)
    invalid = link_path + ("token=invalid" if "?" in link_path else "invalid")
    client.get(prefix + invalid)
    assert _csrf(client.get(prefix + "/sandbox/accounts")) == old_csrf
    payload = {"json" if persona == "utility" else "data": {"email": email}}
    assert client.post(prefix + request_path, **payload).status_code == 200
    inbox = client.get(prefix + "/sandbox/inbox")
    links = [unescape(link) for link in re.findall(r'href="([^"]+)"', inbox.text)]
    link = next(link for link in links if link.startswith(prefix + link_path))
    old_cookie = client.get_cookie("openleg_product", path=prefix).value
    logged_in = getattr(client, method)(link, follow_redirects=True)
    assert logged_in.status_code == 200
    assert name in client.get(prefix + "/sandbox/home").text
    current_cookie = client.get_cookie("openleg_product", path=prefix).value
    assert (
        client.post(
            prefix + "/sandbox/persona",
            data={"csrf_token": old_csrf, "persona": persona},
        ).status_code
        == 400
    )
    client.set_cookie("openleg_product", old_cookie, path=prefix)
    assert name not in client.get(prefix + "/sandbox/home").text
    assert (
        client.post(
            prefix + "/sandbox/persona",
            data={"csrf_token": old_csrf, "persona": persona},
        ).status_code
        == 400
    )
    client.set_cookie("openleg_product", current_cookie, path=prefix)
    assert name in client.get(prefix + "/sandbox/home").text


def test_returning_entry_selection_opens_existing_village_account_chooser(village):
    client = village.test_client()
    prefix, _ = _start(client, "administrator")
    entry = client.get("/sandbox")
    response = client.post(
        "/sandbox/start", data={"csrf_token": _csrf(entry), "persona": "resident"}
    )
    assert response.status_code == 302
    assert response.location == prefix + "/sandbox/accounts"
    chooser = client.get(response.location)
    assert chooser.status_code == 200 and "Robin Muster" in chooser.text
    assert len(village.extensions["villages"].workspaces) == 1
    switched = client.post(
        prefix + "/sandbox/persona",
        data={"csrf_token": _csrf(chooser), "persona": "resident"},
        follow_redirects=True,
    )
    assert switched.status_code == 200 and "Lea Muster" in switched.text
