"""Agent writes stop at a durable, human-confirmed boundary."""

import pytest

from tests import operator_contract_helpers as helpers
from tests.test_operator_previews import MEMBERSHIP, SCENARIOS

interest_database = helpers.interest_database
operator_http = helpers.operator_http
pytestmark = pytest.mark.integration


def prepare(http):
    from flask_babel import Babel

    import i18n

    Babel(http.app, configure_jinja=False)
    http.app.jinja_env.add_extension("jinja2.ext.i18n")
    http.app.jinja_env.install_gettext_callables(
        i18n.gettext, i18n.ngettext, newstyle=False
    )
    helpers.seed_operator_resources(http)
    with http.db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE operator_api_clients SET actor_kind='agent'")
    with http.client.session_transaction() as session:
        session["dashboard_building_id"] = "contract-admin"
        session["dashboard_csrf_token"] = "csrf"


@pytest.mark.parametrize("suffix,payload", SCENARIOS)
def test_agent_pending_replay_has_no_business_effects(operator_http, suffix, payload):
    http = operator_http
    prepare(http)
    response = http.request("POST", suffix, json=payload)
    assert response.status_code == 202
    assert response.json["state"] == "awaiting_confirmation"
    from tests.test_operator_contract import load_contract

    helpers.validate_response(
        load_contract(),
        "/api/operator/v1/communities/{community_id}" + suffix,
        "post",
        response,
    )
    replay = http.request(
        "POST", suffix, json=payload, headers={"Idempotency-Key": "ignored"}
    )
    assert replay.json == response.json
    with http.db.get_connection() as conn, conn.cursor() as cur:
        for table in (
            "vnb_submission_cases",
            "vnb_mutation_cases",
            "vnb_mutation_events",
            "operator_events",
            "operator_webhook_deliveries",
        ):
            cur.execute(f"SELECT count(*) AS n FROM {table}")
            assert cur.fetchone()["n"] == 0
        cur.execute("SELECT * FROM agent_confirmation_requests")
        pending = cur.fetchone()
        assert pending["state"] == "awaiting_confirmation"
        assert pending["client_id"] == http.credential["id"]
        assert pending["payload_fingerprint"] == response.json["payload_fingerprint"]


@pytest.mark.parametrize("suffix,payload", SCENARIOS)
def test_confirmation_and_completed_replay(operator_http, suffix, payload):
    http = operator_http
    prepare(http)
    pending = http.request("POST", suffix, json=payload).json
    url = pending["confirmation_url"]
    denied = http.client.post(url)
    assert denied.status_code == 403
    confirmed = http.client.post(url, headers={"X-CSRF-Token": "csrf"})
    assert confirmed.status_code == 200
    assert confirmed.json["state"] == "confirmed"
    replay = http.client.post(url, headers={"X-CSRF-Token": "csrf"})
    assert replay.json == confirmed.json
    table = (
        "vnb_submission_cases"
        if suffix.startswith("/formation")
        else "vnb_mutation_cases"
    )
    with http.db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT * FROM {table}")
        rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0]["manual_package"]
        assert rows[0]["actor"]["initiating_client_id"] == http.credential["id"]
        assert rows[0]["actor"]["confirming_building_id"] == "contract-admin"


@pytest.mark.parametrize(
    "fault", ["fingerprint", "adapter", "revoked", "admin", "community"]
)
@pytest.mark.parametrize("suffix,payload", SCENARIOS)
def test_confirmation_rechecks_authority_and_intent(
    operator_http, suffix, payload, fault
):
    http = operator_http
    prepare(http)
    pending = http.request("POST", suffix, json=payload).json
    with http.db.get_connection() as conn, conn.cursor() as cur:
        if fault == "fingerprint":
            if suffix.startswith("/formation"):
                cur.execute(
                    "UPDATE leg_documents SET pdf_data=%s", (b"new signed document",)
                )
            else:
                cur.execute(
                    "UPDATE community_members SET access_roles='[\"documents\"]'"
                )
        elif fault == "adapter":
            cur.execute("UPDATE communities SET vnb_adapter_key='unavailable'")
        elif fault == "revoked":
            cur.execute("UPDATE operator_api_clients SET active=false")
        elif fault == "admin":
            cur.execute(
                "UPDATE community_members SET role='member',access_roles='[\"documents\",\"membership\"]'"
            )
    url = pending["confirmation_url"]
    if fault == "community":
        url = url.replace(http.community_id, "another-leg")
    response = http.client.post(url, headers={"X-CSRF-Token": "csrf"})
    assert (
        response.status_code
        == {
            "fingerprint": 409,
            "adapter": 409,
            "revoked": 403,
            "admin": 403,
            "community": 404,
        }[fault]
    )
    with http.db.get_connection() as conn, conn.cursor() as cur:
        for table in (
            "vnb_submission_cases",
            "vnb_mutation_cases",
            "operator_events",
            "operator_webhook_deliveries",
        ):
            cur.execute(f"SELECT count(*) AS n FROM {table}")
            assert cur.fetchone()["n"] == 0


def test_membership_identity_rejects_changed_payload(operator_http):
    http = operator_http
    prepare(http)
    assert (
        http.request("POST", "/membership-mutations", json=MEMBERSHIP).status_code
        == 202
    )
    assert (
        http.request(
            "POST",
            "/membership-mutations",
            json={**MEMBERSHIP, "after": {"role": "admin"}},
        ).status_code
        == 409
    )


def test_cron_expires_once(operator_http):
    import cron

    http = operator_http
    prepare(http)
    http.app.register_blueprint(cron.cron_bp)
    http.app.config["CRON_SECRET"] = "cron"
    pending = http.request("POST", "/formation/submissions", json={}).json
    with http.db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE agent_confirmation_requests SET expires_at=now()-interval '1 second'"
        )
    path = "/api/cron/expire-agent-confirmations"
    assert http.client.post(path).status_code == 403
    assert http.client.post(path, headers={"X-Cron-Secret": "cron"}).json == {
        "expired": 1
    }
    assert http.client.post(path, headers={"X-Cron-Secret": "cron"}).json == {
        "expired": 0
    }
    assert (
        http.client.post(
            pending["confirmation_url"], headers={"X-CSRF-Token": "csrf"}
        ).status_code
        == 409
    )
    with http.db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM operator_events WHERE event_type='formation.confirmation.expired'"
        )
        assert len(cur.fetchall()) == 1


@pytest.mark.parametrize("suffix,payload", SCENARIOS)
def test_concurrent_confirmations_call_domain_once(
    operator_http, monkeypatch, suffix, payload
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, Lock

    import vnb_exchange

    http = operator_http
    prepare(http)
    pending = http.request("POST", suffix, json=payload).json
    name = (
        "submit_formation"
        if suffix.startswith("/formation")
        else "submit_membership_mutation"
    )
    original = getattr(vnb_exchange, name)
    calls = []
    lock = Lock()

    def counted(*args, **kwargs):
        if not kwargs.get("dry_run"):
            with lock:
                calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(vnb_exchange, name, counted)
    barrier = Barrier(2)

    def confirm():
        client = http.app.test_client()
        with client.session_transaction() as session:
            session["dashboard_building_id"] = "contract-admin"
            session["dashboard_csrf_token"] = "csrf"
        barrier.wait(timeout=10)
        return client.post(
            pending["confirmation_url"], headers={"X-CSRF-Token": "csrf"}
        )

    with ThreadPoolExecutor(2) as executor:
        results = list(executor.map(lambda _: confirm(), range(2)))
    assert [r.status_code for r in results] == [200, 200]
    assert results[0].json == results[1].json
    assert calls == [1]


def test_confirmation_racing_expiry_has_one_outcome(operator_http):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    import cron

    http = operator_http
    prepare(http)
    http.app.register_blueprint(cron.cron_bp)
    http.app.config["CRON_SECRET"] = "cron"
    pending = http.request("POST", "/formation/submissions", json={}).json
    with http.db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE agent_confirmation_requests SET expires_at=now()-interval '1 second'"
        )
    barrier = Barrier(2)

    def request(confirm):
        client = http.app.test_client()
        with client.session_transaction() as session:
            session["dashboard_building_id"] = "contract-admin"
            session["dashboard_csrf_token"] = "csrf"
        barrier.wait(timeout=10)
        return client.post(
            pending["confirmation_url"]
            if confirm
            else "/api/cron/expire-agent-confirmations",
            headers={"X-CSRF-Token": "csrf", "X-Cron-Secret": "cron"},
        )

    with ThreadPoolExecutor(2) as executor:
        results = list(executor.map(request, [True, False]))
    assert results[0].status_code == 409
    assert results[1].status_code == 200
    with http.db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT state FROM agent_confirmation_requests")
        assert cur.fetchone()["state"] == "expired"
        cur.execute(
            "SELECT count(*) AS n FROM operator_events WHERE event_type='formation.confirmation.expired'"
        )
        assert cur.fetchone()["n"] == 1
        cur.execute("SELECT count(*) AS n FROM vnb_submission_cases")
        assert cur.fetchone()["n"] == 0


@pytest.mark.parametrize("suffix,payload", SCENARIOS)
def test_failed_confirmation_rolls_back_package_and_case(
    operator_http, monkeypatch, suffix, payload
):
    import agent_confirmation

    http = operator_http
    prepare(http)
    pending = http.request("POST", suffix, json=payload).json
    name = (
        "submit_formation"
        if suffix.startswith("/formation")
        else "submit_membership_mutation"
    )
    original = getattr(agent_confirmation.vnb_exchange, name)

    def fail_after_submission(*args, **kwargs):
        result = original(*args, **kwargs)
        if not kwargs.get("dry_run"):
            raise ValueError("interrupted completion")
        return result

    monkeypatch.setattr(agent_confirmation.vnb_exchange, name, fail_after_submission)
    assert (
        http.client.post(
            pending["confirmation_url"], headers={"X-CSRF-Token": "csrf"}
        ).status_code
        == 409
    )
    with http.db.get_connection() as conn, conn.cursor() as cur:
        for table in (
            "vnb_submission_cases",
            "vnb_mutation_cases",
            "operator_events",
            "operator_webhook_deliveries",
        ):
            cur.execute(f"SELECT count(*) AS n FROM {table}")
            assert cur.fetchone()["n"] == 0
        cur.execute("SELECT state FROM agent_confirmation_requests")
        assert cur.fetchone()["state"] == "awaiting_confirmation"


@pytest.mark.parametrize(
    "locale,title",
    [
        ("de_CH", "Agentenanfrage bestätigen"),
        ("fr_CH", "Confirmer la demande de l’agent"),
    ],
)
def test_confirmation_page_limits_summary_and_uses_csrf(operator_http, locale, title):
    from pathlib import Path

    from jinja2 import FileSystemLoader

    http = operator_http
    http.app.jinja_loader = FileSystemLoader(
        Path(__file__).resolve().parents[1] / "templates"
    )
    http.app.config.update(
        BABEL_DEFAULT_LOCALE=locale,
        BABEL_TRANSLATION_DIRECTORIES=str(
            Path(__file__).resolve().parents[1] / "translations"
        ),
    )
    prepare(http)
    http.app.jinja_env.globals.update(supported_locales={}, current_locale="de-CH")
    pending = http.request("POST", "/membership-mutations", json=MEMBERSHIP).json
    response = http.client.get(pending["confirmation_url"])
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert title in html
    assert pending["payload_fingerprint"] in html
    assert 'name="csrf_token" value="csrf"' in html
    assert "contract@example.invalid" not in html
    assert "contract-admin" in html
    assert (
        http.client.post(
            pending["confirmation_url"], data={"csrf_token": "csrf"}
        ).status_code
        == 303
    )


def test_schema_upgrade_is_repeatable_and_constrains_states(operator_http):
    import psycopg2

    http = operator_http
    prepare(http)
    pending = http.request("POST", "/formation/submissions", json={}).json
    http.db.create_tables()
    http.db.create_tables()
    with http.db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT expires_at-created_at AS lifetime FROM agent_confirmation_requests"
        )
        assert cur.fetchone()["lifetime"].total_seconds() == 72 * 3600
    with pytest.raises(psycopg2.errors.CheckViolation):
        with http.db.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE agent_confirmation_requests SET state='sent' WHERE request_id=%s",
                (pending["request_id"],),
            )


@pytest.mark.parametrize("suffix,payload", SCENARIOS)
def test_transport_only_runs_after_human_confirmation(
    operator_http, monkeypatch, suffix, payload
):
    import vnb_exchange

    http = operator_http
    prepare(http)
    sent = []

    class Adapter:
        def contract(self):
            return vnb_exchange.ExchangeContract(
                "test-vnb",
                "vnb-formation/1",
                frozenset(
                    {vnb_exchange.FORMATION_SUBMISSION, vnb_exchange.PARTICIPANT_CHANGE}
                ),
                "Fictional VNB",
            )

        def submit(self, package, *, idempotency_key):
            sent.append(idempotency_key)
            return vnb_exchange.DeliveryResult(
                "delivered", request_id="request", response_status="accepted"
            )

        submit_mutation = submit

    monkeypatch.setitem(vnb_exchange.ADAPTERS, "test-vnb", Adapter())
    with http.db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE communities SET vnb_adapter_key='test-vnb'")
    pending = http.request("POST", suffix, json=payload).json
    assert sent == []
    assert http.app.test_client().post(pending["confirmation_url"]).status_code == 403
    confirmed = http.client.post(
        pending["confirmation_url"], headers={"X-CSRF-Token": "csrf"}
    )
    assert confirmed.status_code == 200
    assert confirmed.json["result"]["state"] == "delivered"
    assert sent == [pending["payload_fingerprint"]]


@pytest.mark.parametrize("suffix,payload", SCENARIOS)
def test_pending_identity_cannot_be_taken_over_by_another_credential(
    operator_http, suffix, payload
):
    http = operator_http
    prepare(http)
    first = http.request("POST", suffix, json=payload)
    assert first.status_code == 202
    headers, credential = http.credential_headers(
        ["formation.mutate", "membership.mutate"]
    )
    with http.db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE operator_api_clients SET actor_kind='agent' WHERE id=%s",
            (credential["id"],),
        )
    assert (
        http.request("POST", suffix, json=payload, headers=headers).status_code == 409
    )


def test_intervening_direct_submission_is_not_relabelled_as_agent(operator_http):
    http = operator_http
    prepare(http)
    pending = http.request("POST", "/formation/submissions", json={}).json
    headers, _ = http.credential_headers(["formation.mutate"])
    assert (
        http.request(
            "POST", "/formation/submissions", json={}, headers=headers
        ).status_code
        == 202
    )
    response = http.client.post(
        pending["confirmation_url"], headers={"X-CSRF-Token": "csrf"}
    )
    assert response.status_code == 409


def test_request_is_not_visible_to_another_community_admin(operator_http):
    http = operator_http
    prepare(http)
    pending = http.request("POST", "/formation/submissions", json={}).json
    with http.client.session_transaction() as session:
        session["dashboard_building_id"] = "foreign-admin"
    assert http.client.get(pending["confirmation_url"]).status_code == 404
    assert (
        http.client.post(
            pending["confirmation_url"], headers={"X-CSRF-Token": "csrf"}
        ).status_code
        == 404
    )
