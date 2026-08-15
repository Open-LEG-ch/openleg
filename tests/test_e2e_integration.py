# SPDX-License-Identifier: AGPL-3.0-or-later
"""E2E integration tests (static/mocked, no live services)."""

import os

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestSchemaModule:
    def test_schema_has_lea_reports_table(self):
        with open(os.path.join(PROJECT_ROOT, "store", "schema.py")) as f:
            content = f.read()
        assert "CREATE TABLE IF NOT EXISTS lea_reports" in content

    def test_schema_has_ops_snapshots_table(self):
        with open(os.path.join(PROJECT_ROOT, "store", "schema.py")) as f:
            content = f.read()
        assert "CREATE TABLE IF NOT EXISTS ops_snapshots" in content

    def test_database_has_lea_report_functions(self):
        with open(os.path.join(PROJECT_ROOT, "database.py")) as f:
            content = f.read()
        assert "def save_lea_report" in content
        assert "def get_lea_reports" in content
        assert "def save_ops_snapshot" in content
        assert "def get_ops_snapshots" in content


class TestCSVFixtureParse:
    def test_parse_ekz_csv(self):
        fixture_dir = os.path.join(PROJECT_ROOT, "tests", "fixtures")
        if not os.path.isdir(fixture_dir):
            pytest.skip("No fixtures directory")
        csvs = [f for f in os.listdir(fixture_dir) if f.endswith(".csv")]
        if not csvs:
            pytest.skip("No CSV fixtures")
        import meter_data

        with open(os.path.join(fixture_dir, csvs[0])) as f:
            content = f.read()
        readings, errors = meter_data.parse_ekz_csv(content)
        assert isinstance(readings, list)
        assert isinstance(errors, list)


class TestHealthRedisKey:
    def test_health_json_has_redis(self):
        with open(os.path.join(PROJECT_ROOT, "health.py")) as f:
            content = f.read()
        assert "redis" in content


class TestB2BApiRemoved:
    def test_no_b2b_import_in_app(self):
        with open(os.path.join(PROJECT_ROOT, "app.py")) as f:
            content = f.read()
        assert "api_b2b" not in content
        assert "b2b_bp" not in content

    def test_no_refresh_insights_cron(self):
        with open(os.path.join(PROJECT_ROOT, "app.py")) as f:
            content = f.read()
        assert "refresh-insights" not in content

    def test_no_insights_subdomain_in_caddy(self):
        with open(os.path.join(PROJECT_ROOT, "Caddyfile")) as f:
            content = f.read()
        assert "insights.openleg.ch" not in content


class TestStripeRemoved:
    def test_stripe_module_and_dependency_are_absent(self):
        assert not os.path.exists(os.path.join(PROJECT_ROOT, "stripe_integration.py"))
        with open(os.path.join(PROJECT_ROOT, "requirements.txt")) as f:
            requirements = f.read().splitlines()
        assert not any(
            line.strip().lower().startswith("stripe") for line in requirements
        )

    def test_no_stripe_config_or_schema(self):
        for filename in (".env.example", "database.py"):
            with open(os.path.join(PROJECT_ROOT, filename)) as f:
                assert "stripe_" not in f.read().lower()

    def test_no_stripe_webhook_route(self):
        with open(os.path.join(PROJECT_ROOT, "app.py")) as f:
            content = f.read()
        assert "webhook/stripe" not in content

    def test_no_stripe_import_in_app(self):
        with open(os.path.join(PROJECT_ROOT, "app.py")) as f:
            content = f.read()
        assert "stripe_integration" not in content

    def test_no_stripe_crud_in_database(self):
        with open(os.path.join(PROJECT_ROOT, "database.py")) as f:
            content = f.read()
        assert "update_utility_client_stripe" not in content
        assert "deactivate_utility_by_subscription" not in content
        assert "flag_utility_payment_failed" not in content
