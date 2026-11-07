# SPDX-License-Identifier: AGPL-3.0-or-later
"""Native fixtures preserve external test instrumentation during isolation."""

import os

import pytest

from tests import (
    test_app_config,
    test_interest_postgres,
)
from tests import (
    test_calculated_values_lifecycle_postgres as lifecycle,
)
from tests import (
    test_community_create_postgres as community,
)
from tests import (
    test_cron_refresh_postgres as refresh,
)

interest_database = test_interest_postgres.interest_database
calculated_client = lifecycle.calculated_client
founder = community.founder
refresh_web = refresh.refresh_web
secret_key_factory = test_app_config.secret_key_factory
pytestmark = pytest.mark.integration


@pytest.fixture
def native_i18n_environment():
    from tests.test_i18n import _synthetic_environment

    with _synthetic_environment():
        yield


@pytest.mark.parametrize(
    "fixture_name",
    [
        "calculated_client",
        "founder",
        "refresh_web",
        "native_i18n_environment",
        "secret_key_factory",
    ],
)
def test_native_fixture_preserves_test_runner_environment(
    request, monkeypatch, fixture_name
):
    monkeypatch.setenv("MUTANT_UNDER_TEST", "external-test-runner-marker")
    monkeypatch.setenv("MUTMUT_DEPENDENCY_DEPTH", "77")
    monkeypatch.setenv("OPENLEG_PRIVATE_ENVIRONMENT_SENTINEL", "must-be-isolated")

    request.getfixturevalue(fixture_name)

    assert os.environ.get("MUTANT_UNDER_TEST") == "external-test-runner-marker"
    assert os.environ.get("MUTMUT_DEPENDENCY_DEPTH") == "77"
    assert "OPENLEG_PRIVATE_ENVIRONMENT_SENTINEL" not in os.environ
