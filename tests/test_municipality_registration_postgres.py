# SPDX-License-Identifier: AGPL-3.0-or-later
"""Public registration cannot replace a municipality's established owner."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from tests import security_review_fixtures
from tests import test_interest_postgres as interest_postgres_tests

app_module = security_review_fixtures.app_module
interest_database = interest_postgres_tests.interest_database

pytestmark = pytest.mark.integration


@pytest.fixture
def registration(app_module, interest_database, monkeypatch):
    monkeypatch.setattr(
        interest_database,
        "get_municipality_profile",
        lambda bfs: {"name": "Synthetic", "kanton": "ZH", "population": 100},
    )
    return app_module.web, interest_database


@pytest.mark.parametrize(
    "status,email",
    [
        ("pending", "owner@example.ch"),
        ("registered", "owner@example.ch"),
        ("active", None),
        ("verified", "owner@example.ch"),
    ],
)
def test_registration_cannot_replace_or_reset_an_existing_municipality(
    registration, status, email
):
    web, db = registration
    mid = db.save_municipality(261, "Original", subdomain="original")
    assert db.update_municipality_status(261, status, admin_email=email)
    response = web.test_client().post(
        "/gemeinde/register",
        json={"bfs_number": 261, "admin_email": "attacker@example.ch"},
    )
    assert response.status_code == 409
    retained = db.get_municipality(municipality_id=mid)
    assert retained["admin_email"] == email
    assert retained["onboarding_status"] == status
    assert retained["name"] == "Original"


def test_concurrent_first_registrations_establish_one_owner(registration):
    web, db = registration
    ready = Barrier(2)

    def register(email):
        ready.wait(timeout=10)
        response = web.test_client().post(
            "/gemeinde/register", json={"bfs_number": 261, "admin_email": email}
        )
        return email, response.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(register, ["first@example.ch", "second@example.ch"]))
    assert sorted(status for _, status in responses) == [200, 409]
    owner = next(email for email, status in responses if status == 200)
    assert db.get_municipality(bfs_number=261)["admin_email"] == owner
