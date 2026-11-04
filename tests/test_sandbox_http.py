# SPDX-License-Identifier: AGPL-3.0-or-later
"""Drive independent disposable villages through their HTTP entry point."""

import os
import re
from pathlib import Path

import pytest


@pytest.fixture
def village(tmp_path):
    from sandbox_runtime import create_gateway

    postgres_bin = os.environ.get(
        "OPENLEG_SANDBOX_POSTGRES_BIN", "/usr/lib/postgresql/16/bin"
    )
    if not (Path(postgres_bin) / "initdb").is_file():
        pytest.skip("requires local PostgreSQL 16 binaries")
    gateway = create_gateway(
        tmp_path / "villages", postgres_bin=postgres_bin, max_workspaces=2
    )
    gateway.config["TESTING"] = True
    try:
        yield gateway
    finally:
        gateway.extensions["villages"].close()


def _csrf(response):
    return re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)


def _enter(client):
    page = client.get("/sandbox")
    assert page.status_code == 200
    response = client.post(
        "/sandbox/start", data={"csrf_token": _csrf(page), "persona": "resident"}
    )
    assert response.status_code == 302
    dashboard = client.get(response.location, follow_redirects=True)
    assert dashboard.status_code == 200
    assert "Sonnwil" in dashboard.text
    assert "Simulation" in dashboard.text
    return dashboard


@pytest.mark.integration
def test_two_visitors_get_independent_writable_villages(village):
    first, second = village.test_client(), village.test_client()
    first_page = _enter(first)
    _enter(second)

    saved = first.post(
        "/dashboard/profile",
        data={
            "csrf_token": _csrf(first_page),
            "annual_consumption_kwh": "7200",
            "potential_pv_kwp": "8",
            "share_with_neighbors": "on",
        },
    )
    assert saved.status_code == 302
    assert first.get("/dashboard/export").json["annual_consumption_kwh"] == 7200
    assert second.get("/dashboard/export").json["annual_consumption_kwh"] == 4500
    assert "7200" in first.get("/dashboard").text
    assert first.get("/dashboard").headers["Cache-Control"] == "no-store"

    # The worker cookie alone cannot authenticate in a different village.
    cookie = first.get_cookie("openleg_product")
    assert cookie is not None
    second.set_cookie("openleg_product", cookie.value)
    assert second.get("/dashboard/export").status_code == 401

    # Capacity is bounded and an unowned worker path is never an alternate login.
    third = village.test_client()
    page = third.get("/sandbox")
    assert (
        third.post(
            "/sandbox/start", data={"csrf_token": _csrf(page), "persona": "resident"}
        ).status_code
        == 503
    )
    assert third.get("/dashboard?workspace=resident").status_code == 303


@pytest.mark.integration
def test_access_email_is_delivered_only_to_the_visitors_simulated_inbox(village):
    first, second = village.test_client(), village.test_client()
    _enter(first)
    _enter(second)
    response = first.post(
        "/dashboard/access/request", data={"email": "sonnwil-resident@example.org"}
    )
    assert response.status_code == 200
    inbox = first.get("/sandbox/inbox")
    assert inbox.status_code == 200
    assert "sonnwil-resident@example.org" in inbox.text
    link = re.search(r'href="(/dashboard/access/[^"?]+)', inbox.text).group(1)
    assert link not in second.get("/sandbox/inbox").text
    assert first.get(link).status_code == 302
    assert first.get(link).location.endswith("access=invalid")
    assert second.get(link).location.endswith("access=invalid")


@pytest.mark.integration
def test_gateway_rejects_untrusted_host_and_cross_site_start(village):
    client = village.test_client()
    assert (
        client.get("/sandbox", headers={"Host": "untrusted.example.org"}).status_code
        == 400
    )
    assert (
        client.post("/sandbox/start", data={"persona": "resident"}).status_code == 400
    )


@pytest.mark.integration
def test_french_entry_keeps_the_selected_language_when_entering_the_village(village):
    client = village.test_client()
    page = client.get("/sandbox?lang=fr-CH")
    assert "Votre village d&#39;essai" in page.text
    response = client.post(
        "/sandbox/start",
        data={"csrf_token": _csrf(page), "persona": "resident"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert '<html lang="fr-CH">' in response.text
    assert "Village d&#39;essai" in response.text
