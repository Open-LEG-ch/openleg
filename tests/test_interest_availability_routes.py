# SPDX-License-Identifier: AGPL-3.0-or-later
"""Interest availability through HTTP with real PostgreSQL failures."""

import re

import pytest

import database as db
import municipality_profile
from tests import test_dashboard_access_routes, test_interest_postgres
from tests.test_verified_interest_reads import _seed

app_module = test_dashboard_access_routes.app_module
interest_database = test_interest_postgres.interest_database
pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def application_environment(monkeypatch):
    for key, value in {
        "APP_BASE_URL": "http://localhost:5003",
        "SECRET_KEY": "interest-availability-test-key",
        "SESSION_COOKIE_SECURE": "false",
        "SESSION_COOKIE_SAMESITE": "Lax",
        "PERMANENT_SESSION_LIFETIME": "3600",
        "DASHBOARD_ACCESS_TOKEN_TTL_SECONDS": "900",
        "DASHBOARD_EMAIL_TOKEN_TTL_SECONDS": "86400",
        "PUBLIC_SITE_URL": "https://openleg.ch",
        "ALLOWED_HOSTS": "localhost,127.0.0.1",
        "ADMIN_EMAIL": "admin@example.ch",
        "CRON_SECRET": "",
        "REDIS_URL": "memory://",
        "MATOMO_SITE_ID": "",
        "GA4_MEASUREMENT_ID": "",
    }.items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def interest_client(interest_database, app_module, monkeypatch):
    for bfs, name in ((2554, "Alphawil"), (999, "Zetwil")):
        assert db.save_municipality_profile(
            {"bfs_number": bfs, "name": name, "kanton": "SO"}
        )
    monkeypatch.setattr(municipality_profile.cache, "cache_get", lambda _key: None)
    monkeypatch.setattr(
        municipality_profile.cache, "cache_set", lambda *_args, **_kwargs: False
    )
    return app_module.web.test_client()


def _drop_interest_view():
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("DROP VIEW verified_interest")


def _interest_heading(html):
    heading = re.search(r'<h2 id="interest-title"[^>]*>(.*?)</h2>', html, re.DOTALL)
    assert heading
    return re.sub(r"\s+", " ", heading.group(1)).strip()


def test_public_profile_distinguishes_query_failure_from_zero(interest_client):
    _drop_interest_view()
    response = interest_client.get("/gemeinde/profil/2554")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "nicht verfügbar" in _interest_heading(html)
    assert "0 bestätigte Interessierte" not in _interest_heading(html)
    assert "verified_interest" not in html


def test_municipality_dashboard_does_not_turn_query_failure_into_zero(interest_client):
    municipality_id = db.save_municipality(2554, "Alphawil", kanton="SO")
    assert municipality_id
    with interest_client.session_transaction() as state:
        state["municipality_id"] = municipality_id
    _drop_interest_view()

    response = interest_client.get("/gemeinde/dashboard")
    html = response.get_data(as_text=True)
    section = re.search(
        r'<section[^>]*aria-labelledby="interest-summary-title"[^>]*>(.*?)</section>',
        html,
        re.DOTALL,
    )
    assert response.status_code == 200
    assert section
    assert "nicht verfügbar" in section.group(1)
    visible = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", section.group(1)))
    assert "0 bestätigte Interessierte" not in visible
    assert "0 in den letzten 30 Tagen" not in visible
    assert "0 mit Solaranlage" not in visible
    assert "0 offene Adresszuordnung" not in visible
    assert "verified_interest" not in html


def test_directory_marks_failed_counts_unavailable_and_falls_back_to_names(
    interest_client,
):
    for index in range(3):
        _seed("building", f"z-{index}", f"z{index}@example.ch", bfs=999)
    _drop_interest_view()

    response = interest_client.get("/gemeinde/verzeichnis?sort=interest")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert html.count("Nicht verfügbar") == 2
    assert "0 Interessierte" not in html
    assert html.index("Alphawil") < html.index("Zetwil")
    assert re.search(r'<option value="name"\s+selected', html)
    assert re.search(r'<option value="interest"[^>]*\bdisabled', html)
    assert "verified_interest" not in html


@pytest.mark.parametrize(
    ("count", "label"),
    [
        (0, "0 bestätigte Interessierte"),
        (1, "&lt; 3 bestätigte Interessierte"),
        (2, "&lt; 3 bestätigte Interessierte"),
        (3, "3 bestätigte Interessierte"),
    ],
)
def test_public_profile_preserves_zero_masked_and_exact_counts(
    interest_client, count, label
):
    for index in range(count):
        _seed("building", f"p-{index}", f"p{index}@example.ch")
    response = interest_client.get("/gemeinde/profil/2554")
    assert response.status_code == 200
    assert _interest_heading(response.get_data(as_text=True)) == f"{label} in Alphawil"


def test_unknown_municipality_stays_missing_when_interest_is_unavailable(
    interest_client,
):
    _drop_interest_view()
    assert interest_client.get("/gemeinde/profil/1234567").status_code == 404


def test_directory_does_not_reveal_hidden_counts_through_order(interest_client):
    _seed("building", "alpha", "alpha@example.ch")
    _seed("building", "zeta-1", "zeta1@example.ch", bfs=999)
    _seed("building", "zeta-2", "zeta2@example.ch", bfs=999)

    response = interest_client.get("/gemeinde/verzeichnis?sort=interest")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert html.count("&lt; 3 Interessierte") == 2
    assert html.index("Alphawil") < html.index("Zetwil")
    assert "Nicht verfügbar" not in html


def test_municipality_dashboard_preserves_real_zero_counts(interest_client):
    municipality_id = db.save_municipality(2554, "Alphawil", kanton="SO")
    assert municipality_id
    with interest_client.session_transaction() as state:
        state["municipality_id"] = municipality_id
    response = interest_client.get("/gemeinde/dashboard")
    html = response.get_data(as_text=True)
    visible = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
    assert response.status_code == 200
    assert "0 bestätigte Interessierte" in visible
    assert "nicht verfügbar" not in visible
