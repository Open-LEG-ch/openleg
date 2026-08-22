# SPDX-License-Identifier: AGPL-3.0-or-later
"""Route contracts for private dashboard sessions and LEG mutations."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tests.test_app_organic_routes import _disable_rate_limit_hooks


@pytest.fixture
def app_module():
    import app as imported_app

    web = imported_app.create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "dashboard-access-test-key",
            "APP_BASE_URL": "http://localhost:5003",
            "RATELIMIT_STORAGE_URI": "memory://",
        },
        load_environment=False,
        check_database=False,
    )
    hooks = _disable_rate_limit_hooks(web)
    try:
        imported_app.web = web
        yield imported_app
    finally:
        web.before_request_funcs[None] = hooks


def _set_session(client, building_id="building-session", csrf_token="csrf-secret"):
    with client.session_transaction() as state:
        state["dashboard_building_id"] = building_id
        state["dashboard_csrf_token"] = csrf_token


def _readiness(building_id, **_kwargs):
    return {
        "error": None,
        "user": {
            "building_id": building_id,
            "address": "Musterweg 1",
            "annual_consumption_kwh": None,
            "potential_pv_kwp": None,
        },
        "readiness_score": 25,
        "checks": [],
        "neighbor_count": 0,
        "referral_link": "",
    }


def test_magic_link_is_exchanged_once_for_clean_session_url(app_module, monkeypatch):
    raw_token = "a" * 43
    consumed_hashes = []

    def consume(token_hash):
        consumed_hashes.append(token_hash)
        return (
            {"building_id": "building-session"} if len(consumed_hashes) == 1 else None
        )

    monkeypatch.setattr(app_module.db, "consume_dashboard_access_token", consume)
    client = app_module.web.test_client()

    with client.session_transaction() as state:
        state["stale_session_value"] = "must-be-rotated-away"

    response = client.get(f"/dashboard/access/{raw_token}")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")
    assert raw_token not in response.headers["Location"]
    assert "bid=" not in response.headers["Location"]
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert "no-store" in response.headers["Cache-Control"]
    assert raw_token not in consumed_hashes
    with client.session_transaction() as state:
        assert state["dashboard_building_id"] == "building-session"
        assert state["dashboard_csrf_token"]
        assert state.permanent is True
        assert "stale_session_value" not in state

    replay_client = app_module.web.test_client()
    replay = replay_client.get(f"/dashboard/access/{raw_token}")
    assert replay.status_code == 302
    assert replay.headers["Location"].endswith("/dashboard?access=invalid")
    with replay_client.session_transaction() as state:
        assert "dashboard_building_id" not in state
    landing = replay_client.get(replay.headers["Location"])
    assert 'action="/dashboard/access/request"' in landing.get_data(as_text=True)


def test_session_identity_wins_over_legacy_bid(app_module, monkeypatch):
    seen = []
    monkeypatch.setattr(
        app_module.dashboard_module,
        "readiness",
        lambda building_id, **kwargs: (
            seen.append(building_id) or _readiness(building_id, **kwargs)
        ),
    )
    client = app_module.web.test_client()
    _set_session(client)

    response = client.get("/dashboard?bid=building-attacker")

    assert response.status_code == 200
    assert seen == ["building-session"]
    html = response.get_data(as_text=True)
    assert "Nur Lesezugriff" not in html
    assert 'action="/dashboard/logout"' in html
    assert 'name="csrf_token" value="csrf-secret"' in html


def test_legacy_bid_is_ignored_and_requires_secure_access(app_module, monkeypatch):
    readiness = MagicMock(side_effect=_readiness)
    monkeypatch.setattr(app_module.dashboard_module, "readiness", readiness)
    client = app_module.web.test_client()

    response = client.get("/dashboard?bid=building-legacy")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'action="/dashboard/access/request"' in html
    assert "Musterweg 1" not in html
    readiness.assert_not_called()


def test_dashboard_without_identity_offers_access_request(app_module):
    client = app_module.web.test_client()

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert 'action="/dashboard/access/request"' in response.get_data(as_text=True)


def test_access_request_has_same_public_response_for_known_and_unknown_email(
    app_module, monkeypatch
):
    client = app_module.web.test_client()
    send = MagicMock(return_value=True)
    monkeypatch.setattr(app_module, "send_email", send)
    monkeypatch.setattr(
        app_module.dashboard_access_module,
        "issue_access_token",
        lambda _db, _building_id, **_kwargs: "a" * 43,
    )

    monkeypatch.setattr(app_module.db, "get_building_by_email", lambda _email: [])
    unknown = client.post(
        "/dashboard/access/request", data={"email": "unknown@example.ch"}
    )

    monkeypatch.setattr(
        app_module.db,
        "get_building_by_email",
        lambda _email: [{"building_id": "building-known"}],
    )
    known = client.post("/dashboard/access/request", data={"email": "known@example.ch"})

    assert unknown.status_code == known.status_code == 200
    generic_message = "Falls ein Profil zu dieser E-Mail-Adresse existiert"
    assert generic_message in unknown.get_data(as_text=True)
    assert generic_message in known.get_data(as_text=True)
    send.assert_called_once()
    body = send.call_args.args[2]
    assert "/dashboard/access/" in body
    assert "bid=" not in body
    assert "building-known" not in body


def test_access_request_stays_generic_when_mail_delivery_raises(
    app_module, monkeypatch
):
    client = app_module.web.test_client()
    monkeypatch.setattr(
        app_module.db,
        "get_building_by_email",
        lambda _email: [{"building_id": "building-known"}],
    )
    monkeypatch.setattr(
        app_module.dashboard_access_module,
        "issue_access_token",
        lambda _db, _building_id, **_kwargs: "a" * 43,
    )
    monkeypatch.setattr(
        app_module,
        "send_email",
        MagicMock(side_effect=RuntimeError("mail transport unavailable")),
    )

    response = client.post(
        "/dashboard/access/request", data={"email": "known@example.ch"}
    )

    assert response.status_code == 200
    assert "Falls ein Profil zu dieser E-Mail-Adresse existiert" in response.get_data(
        as_text=True
    )


def test_leg_mutation_requires_session_and_csrf(app_module, monkeypatch):
    invite = MagicMock(return_value={"error": None})
    monkeypatch.setattr(app_module.dashboard_module, "leg_invite_by_email", invite)
    client = app_module.web.test_client()

    anonymous = client.post(
        "/leg/community/community-1/invite",
        data={"bid": "building-attacker", "invite_email": "new@example.ch"},
    )
    assert anonymous.status_code == 401

    _set_session(client)
    missing_csrf = client.post(
        "/leg/community/community-1/invite",
        data={"bid": "building-attacker", "invite_email": "new@example.ch"},
    )
    assert missing_csrf.status_code == 400
    invite.assert_not_called()

    accepted = client.post(
        "/leg/community/community-1/invite",
        data={
            "bid": "building-attacker",
            "invite_email": "new@example.ch",
            "csrf_token": "csrf-secret",
        },
    )
    assert accepted.status_code == 302
    invite.assert_called_once_with("community-1", "building-session", "new@example.ch")
    assert "bid=" not in accepted.headers["Location"]


def test_leg_mutation_rejects_non_ascii_csrf_as_bad_request(app_module, monkeypatch):
    invite = MagicMock(return_value={"error": None})
    monkeypatch.setattr(app_module.dashboard_module, "leg_invite_by_email", invite)
    client = app_module.web.test_client()
    _set_session(client)

    response = client.post(
        "/leg/community/community-1/invite",
        data={"csrf_token": "nön-ascii", "invite_email": "new@example.ch"},
    )

    assert response.status_code == 400
    invite.assert_not_called()


def test_leg_document_uses_session_identity_not_query_bid(app_module, monkeypatch):
    document_for_member = MagicMock(
        return_value={"pdf_data": b"pdf", "filename": "vertrag.pdf"}
    )
    monkeypatch.setattr(
        app_module.dashboard_module, "leg_document_for_member", document_for_member
    )
    client = app_module.web.test_client()

    anonymous = client.get("/leg/document/7?bid=building-attacker")
    assert anonymous.status_code == 404
    document_for_member.assert_not_called()

    _set_session(client)
    authenticated = client.get("/leg/document/7?bid=building-attacker")
    assert authenticated.status_code == 200
    document_for_member.assert_called_once_with(7, "building-session")


def test_leg_document_quotes_untrusted_filename_safely(app_module, monkeypatch):
    monkeypatch.setattr(
        app_module.dashboard_module,
        "leg_document_for_member",
        lambda _doc_id, _building_id: {
            "pdf_data": b"pdf",
            "filename": 'Gründung "final"; v1.pdf',
        },
    )
    client = app_module.web.test_client()
    _set_session(client)

    response = client.get("/leg/document/7")

    assert response.status_code == 200
    disposition = response.headers["Content-Disposition"]
    assert disposition.startswith("inline;")
    assert 'filename="Grundung \\"final\\"; v1.pdf"' in disposition
    assert "filename*=UTF-8''Gr%C3%BCndung%20%22final%22%3B%20v1.pdf" in disposition


def test_logout_requires_csrf_revokes_links_and_clears_session(app_module, monkeypatch):
    revoke = MagicMock(return_value=2)
    monkeypatch.setattr(app_module.db, "revoke_dashboard_access_tokens", revoke)
    client = app_module.web.test_client()
    _set_session(client)

    rejected = client.post("/dashboard/logout")
    assert rejected.status_code == 400

    response = client.post("/dashboard/logout", data={"csrf_token": "csrf-secret"})

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")
    revoke.assert_called_once_with("building-session")
    with client.session_transaction() as state:
        assert "dashboard_building_id" not in state
        assert "dashboard_csrf_token" not in state


def test_http_access_exchange_emits_session_cookie_without_secure_attribute(
    monkeypatch,
):
    import app as app_module

    raw_token = "a" * 43
    monkeypatch.setattr(
        app_module.db,
        "consume_dashboard_access_token",
        lambda _token_hash: {"building_id": "building-session"},
    )
    web = app_module.create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "dashboard-access-test-key",
            "APP_BASE_URL": "http://localhost:5003",
            "SESSION_COOKIE_SECURE": False,
            "RATELIMIT_STORAGE_URI": "memory://",
        },
        load_environment=False,
        check_database=False,
    )
    hooks = _disable_rate_limit_hooks(web)
    try:
        client = web.test_client()
        response = client.get(f"/dashboard/access/{raw_token}", follow_redirects=False)
    finally:
        web.before_request_funcs[None] = hooks

    assert response.status_code == 302
    set_cookies = response.headers.getlist("Set-Cookie")
    session_cookie = next((c for c in set_cookies if c.startswith("session=")), None)
    assert session_cookie is not None
    assert "Secure" not in session_cookie


def test_https_base_url_defaults_session_cookie_to_secure(monkeypatch):
    import app as app_module

    monkeypatch.delenv("SESSION_COOKIE_SECURE", raising=False)
    monkeypatch.setenv("APP_BASE_URL", "https://openleg.ch")
    web = app_module.create_app(
        {"TESTING": True, "RATELIMIT_STORAGE_URI": "memory://"},
        load_environment=False,
        check_database=False,
    )

    assert web.config["SESSION_COOKIE_SECURE"] is True


@pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes", "on"])
def test_session_cookie_secure_accepts_truthy_environment_values(monkeypatch, value):
    import app as app_module

    monkeypatch.setenv("SESSION_COOKIE_SECURE", value)
    web = app_module.create_app(
        {"TESTING": True, "RATELIMIT_STORAGE_URI": "memory://"},
        load_environment=False,
        check_database=False,
    )

    assert web.config["SESSION_COOKIE_SECURE"] is True


@pytest.mark.parametrize(
    ("value", "expected"),
    [("invalid", 900), ("1", 60), ("999999", 86_400)],
)
def test_dashboard_token_ttl_is_validated_and_clamped(monkeypatch, value, expected):
    import app as app_module

    monkeypatch.setenv("DASHBOARD_ACCESS_TOKEN_TTL_SECONDS", value)
    web = app_module.create_app(
        {"TESTING": True, "RATELIMIT_STORAGE_URI": "memory://"},
        load_environment=False,
        check_database=False,
    )

    assert web.config["DASHBOARD_ACCESS_TOKEN_TTL_SECONDS"] == expected


def test_private_dashboard_and_document_responses_are_never_cached(
    app_module, monkeypatch
):
    monkeypatch.setattr(app_module.dashboard_module, "readiness", _readiness)
    monkeypatch.setattr(
        app_module.dashboard_module,
        "leg_document_for_member",
        lambda _doc_id, _building_id: {
            "pdf_data": b"pdf",
            "filename": "vertrag.pdf",
        },
    )
    client = app_module.web.test_client()
    _set_session(client)

    for path in ("/dashboard", "/leg/document/7"):
        response = client.get(path)
        assert "no-store" in response.headers["Cache-Control"]
        assert response.headers["Referrer-Policy"] == "no-referrer"


def test_legacy_leg_view_does_not_offer_private_document_download(
    app_module, monkeypatch
):
    overview = {
        "error": None,
        "community": {
            "community_id": "community-1",
            "name": "LEG Musterweg",
            "status": "interested",
            "distribution_model": "simple",
            "readiness_score": 25,
            "member_count": {"confirmed": 1, "total": 1, "invited": 0},
            "members": [
                {
                    "building_id": "building-legacy",
                    "role": "admin",
                    "status": "confirmed",
                    "address": "Musterweg 1",
                }
            ],
            "next_steps": [],
        },
        "viewer_building_id": "building-legacy",
        "is_admin": True,
        "leg_documents": [
            {"id": 7, "filename": "vertrag.pdf", "signing_status": "draft"}
        ],
        "correspondence": [],
    }
    seen = []

    def leg_overview(community_id, building_id):
        seen.append((community_id, building_id))
        if not building_id:
            return {"error": "Sicherer Zugang erforderlich", "community": None}
        return overview

    monkeypatch.setattr(app_module.dashboard_module, "leg_overview", leg_overview)
    client = app_module.web.test_client()

    anonymous = client.get("/leg/dashboard?cid=community-1&bid=building-legacy")
    assert "LEG Musterweg" not in anonymous.get_data(as_text=True)
    assert "/leg/document/7" not in anonymous.get_data(as_text=True)
    assert seen == [("community-1", "")]

    _set_session(client, building_id="building-legacy")
    authenticated = client.get("/leg/dashboard?cid=community-1")
    assert "/leg/document/7" in authenticated.get_data(as_text=True)
    assert seen[-1] == ("community-1", "building-legacy")


def test_leg_forms_use_csrf_and_never_submit_building_id():
    source = Path("templates/leg_dashboard.html").read_text(encoding="utf-8")

    assert 'name="bid"' not in source
    assert source.count('name="csrf_token"') >= 5
    assert "?bid=" not in source
