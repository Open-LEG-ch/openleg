# SPDX-License-Identifier: AGPL-3.0-or-later
"""The admin and internal surface lives in its own blueprint.

`app.py` carried the operator surface inline: six `/admin/*` routes, three
`/api/internal/*` ingestion endpoints and their auth helpers. They form one
cohesive area with one audience, so they move to `admin.py` behind
`admin_bp`, and `app.py` keeps only the wiring.
"""

import importlib
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ADMIN_RULES = (
    "/admin/overview",
    "/admin/export",
    "/admin/lea-reports",
    "/admin/ops",
    "/admin/abrechnungen",
    "/admin/registry/<int:entry_id>/approve",
    "/admin/registry/<int:entry_id>/reject",
    "/api/internal/lea-report",
    "/api/internal/ops-snapshot",
    "/api/internal/agentmail",
)


def _load_app():
    with (
        patch("database.is_db_available", return_value=True),
        patch("database._connection_pool", MagicMock()),
    ):
        import app as app_module

        return SimpleNamespace(
            app=app_module.create_app(load_environment=False), db=app_module.db
        )


@pytest.fixture
def app_with_tokens():
    with patch.dict(
        os.environ,
        {
            "DATABASE_URL": "postgresql://x:x@localhost/x",
            "REDIS_URL": "memory://",
            "APP_BASE_URL": "http://localhost:5003",
            "ADMIN_TOKEN": "admin-token",
            "INTERNAL_TOKEN": "internal-token",
            "AGENTMAIL_WEBHOOK_SECRET": "",
        },
    ):
        yield _load_app()


@pytest.fixture
def app_without_admin_token():
    with patch.dict(
        os.environ,
        {
            "DATABASE_URL": "postgresql://x:x@localhost/x",
            "REDIS_URL": "memory://",
            "APP_BASE_URL": "http://localhost:5003",
            "INTERNAL_TOKEN": "internal-token",
            "AGENTMAIL_WEBHOOK_SECRET": "",
        },
    ):
        os.environ.pop("ADMIN_TOKEN", None)
        yield _load_app()


class TestBlueprintOwnsTheOperatorSurface:
    def test_app_imports_the_blueprint_without_reloading_it(self):
        with open(os.path.join(PROJECT_ROOT, "app.py")) as handle:
            content = handle.read()

        assert "from admin import admin_bp, require_admin" in content
        assert "importlib" not in content

    def test_module_exposes_the_blueprint_and_the_guard(self):
        import admin

        assert admin.admin_bp.name == "admin"
        assert callable(admin.require_admin)
        assert callable(admin.require_internal_token)

        with patch.object(admin.db, "is_db_available") as is_db_available:
            importlib.reload(admin)

        is_db_available.assert_not_called()

    def test_every_operator_route_is_served_by_the_blueprint(self, app_with_tokens):
        rules = {
            rule.rule: rule.endpoint
            for rule in app_with_tokens.app.url_map.iter_rules()
        }

        for rule in ADMIN_RULES:
            assert rule in rules, f"{rule} disappeared from the URL map"
            assert rules[rule].startswith("admin."), (
                f"{rule} is still served by {rules[rule]}"
            )

    def test_app_module_no_longer_declares_the_routes(self):
        with open(os.path.join(PROJECT_ROOT, "app.py")) as handle:
            content = handle.read()

        for rule in ADMIN_RULES:
            assert f'@app.route("{rule}"' not in content

    def test_app_module_stays_within_its_budget(self):
        with open(os.path.join(PROJECT_ROOT, "app.py")) as handle:
            lines = len(handle.readlines())

        assert lines < 1250, f"app.py is {lines} lines after the extraction"


class TestGuardsStayFailClosed:
    def test_admin_routes_hide_when_no_admin_token_is_configured(
        self, app_without_admin_token
    ):
        client = app_without_admin_token.app.test_client()

        for rule in ADMIN_RULES:
            if not rule.startswith("/admin/") or "<" in rule:
                continue
            assert client.get(rule).status_code == 404, f"{rule} leaked"

    def test_admin_routes_reject_a_wrong_token(self, app_with_tokens, caplog):
        client = app_with_tokens.app.test_client()

        with patch("admin.hmac.compare_digest", return_value=False) as compare:
            assert client.get("/admin/overview").status_code == 403
            assert (
                client.get(
                    "/admin/ops",
                    headers={"X-Admin-Token": "nope"},
                    environ_overrides={
                        "HTTP_X_FORWARDED_FOR": "198.51.100.4\r\nFORGED, 203.0.113.8"
                    },
                ).status_code
                == 403
            )

        assert compare.call_count == 2
        record = caplog.records[-1].message
        assert "IP: 198.51.100.4FORGED |" in record
        assert "203.0.113.8" not in record
        assert "\r" not in record and "\n" not in record

    def test_admin_routes_reject_a_non_ascii_token_without_error(
        self, app_with_tokens, caplog
    ):
        response = app_with_tokens.app.test_client().get(
            "/admin/overview", headers={"X-Admin-Token": "tökén"}
        )

        assert response.status_code == 403
        assert "ADMIN_ACCESS_DENIED" in caplog.records[-1].message

    def test_internal_ingestion_rejects_a_wrong_token(self, app_with_tokens):
        client = app_with_tokens.app.test_client()

        with (
            patch("admin.hmac.compare_digest", return_value=False) as compare,
            patch(
                "admin.require_internal_token",
                wraps=__import__("admin").require_internal_token,
            ) as guard,
        ):
            for rule in (
                "/api/internal/lea-report",
                "/api/internal/ops-snapshot",
                "/api/internal/agentmail",
            ):
                response = client.post(
                    rule,
                    json={"job_name": "x"},
                    headers={"X-Internal-Token": "nope"},
                )
                assert response.status_code == 403, f"{rule} accepted a wrong token"

        assert guard.call_count == 3
        assert compare.call_count == 3

    def test_internal_ingestion_rejects_a_non_ascii_token_without_error(
        self, app_with_tokens
    ):
        response = app_with_tokens.app.test_client().post(
            "/api/internal/lea-report",
            json={"job_name": "x"},
            headers={"X-Internal-Token": "tökén"},
        )

        assert response.status_code == 403

    def test_agentmail_preview_survives_a_non_string_body(self, app_with_tokens):
        module = app_with_tokens
        with patch.object(module.db, "save_ops_snapshot", return_value=True) as saved:
            response = module.app.test_client().post(
                "/api/internal/agentmail",
                json={
                    "event_type": "message.received",
                    "message": {
                        "message_id": "msg_1",
                        "inbox_id": "hallo@openleg.ch",
                        "subject": "LEG Anfrage",
                        "text": 12345,
                    },
                },
                headers={"X-Internal-Token": "internal-token"},
            )

        assert response.status_code == 200
        assert saved.call_args.kwargs["payload"]["text_preview"] == "12345"

    def test_admin_overview_still_answers_with_a_valid_token(self, app_with_tokens):
        module = app_with_tokens
        with (
            patch.object(module.db, "get_stats", return_value={"buildings": 1}),
            patch.object(module.db, "get_email_stats", return_value={}),
            patch.object(module.db, "count_consented_buildings", return_value=0),
            patch.object(module.db, "get_all_municipalities", return_value=[]),
            patch.object(
                module.db, "is_db_available", return_value=True
            ) as is_db_available,
        ):
            response = module.app.test_client().get(
                "/admin/overview", headers={"X-Admin-Token": "admin-token"}
            )

        assert response.status_code == 200
        assert response.get_json()["platform"] == "OpenLEG"
        is_db_available.assert_called_once_with()

    def test_billing_workspace_renders_an_honest_read_only_draft(self, app_with_tokens):
        import admin

        workspace = {
            "empty": False,
            "periods": [{"id": 42, "period_label": "Juli 2026"}],
            "selected": {
                "id": 42,
                "community_id": "community-a",
                "period_label": "Juli 2026",
                "status_label": "Entwurf",
                "draft_notice": (
                    "Dieser Abrechnungsentwurf ist keine definitive Rechnung."
                ),
                "metrics": {
                    "production_kwh": 125.5,
                    "allocated_kwh": 100.25,
                    "surplus_kwh": 25.25,
                    "network_discount_chf": 8.2,
                },
                "metrics_display": {
                    "production_kwh": "125.50",
                    "allocated_kwh": "100.25",
                    "surplus_kwh": "25.25",
                    "network_discount_chf": "8.20",
                },
                "tariff": {
                    "internal_price": "15.00 Rp./kWh",
                    "grid_fee": "8.00 Rp./kWh",
                    "distribution_model": "Proportional",
                    "network_level": "Gleiche Netzebene",
                },
                "reconciliation": {
                    "balanced": True,
                    "label": "Vollständig abgeglichen",
                },
                "provenance": {
                    "source_document_ids": ["E66-A"],
                    "source_count": 1,
                    "input_fingerprint": "abc123",
                },
                "consumer_charges": [
                    {
                        "participant_id": "consumer-a",
                        "quantity_kwh": 100.25,
                        "unit_price_chf_per_kwh": 0.15,
                        "amount_chf": 15.0375,
                        "display_quantity_kwh": "100.250",
                        "display_unit_price_rp": "15.00",
                        "display_amount_chf": "15.04",
                    }
                ],
                "producer_credits": [],
                "rounding_adjustments": [],
            },
        }
        with patch.object(admin.billing_workspace, "load", return_value=workspace):
            response = app_with_tokens.app.test_client().get(
                "/admin/abrechnungen?token=admin-token",
                headers={"Accept": "text/html"},
            )

        html = response.get_data(as_text=True)
        assert response.status_code == 200
        for text in (
            "Abrechnungsentwurf",
            "keine definitive Rechnung",
            "Tarifbasis",
            "Vollständig abgeglichen",
            "Quelldokumente",
            "Belastungen",
        ):
            assert text in html
        for forbidden in ("Rechnung ausstellen", "Finalisieren", "Bezahlen"):
            assert forbidden not in html
        assert "?period_id=42&amp;token=" not in html

    def test_billing_workspace_selects_period_without_token_in_url(
        self, app_with_tokens
    ):
        import admin

        workspace = {"empty": True, "periods": [], "selected": None}
        with patch.object(
            admin.billing_workspace, "load", return_value=workspace
        ) as load:
            response = app_with_tokens.app.test_client().post(
                "/admin/abrechnungen",
                data={"token": "admin-token", "period_id": "42"},
                headers={"Accept": "text/html"},
            )

        assert response.status_code == 200
        load.assert_called_once_with(period_id=42)

    def test_billing_workspace_rejects_invalid_period_before_loading(
        self, app_with_tokens
    ):
        import admin

        with patch.object(admin.billing_workspace, "load") as load:
            response = app_with_tokens.app.test_client().get(
                "/admin/abrechnungen?token=admin-token&period_id=invalid"
            )

        assert response.status_code == 404
        load.assert_not_called()

    def test_billing_workspace_does_not_hide_internal_type_errors(
        self, app_with_tokens
    ):
        import admin

        with patch.object(
            admin.billing_workspace, "load", side_effect=TypeError("programming bug")
        ):
            response = app_with_tokens.app.test_client().get(
                "/admin/abrechnungen?token=admin-token&period_id=42"
            )

        assert response.status_code == 500

    def test_billing_workspace_reports_storage_failure_as_unavailable(
        self, app_with_tokens
    ):
        import admin

        with patch.object(
            admin.billing_workspace,
            "load",
            side_effect=app_with_tokens.db.BillingStoreError("offline"),
        ):
            response = app_with_tokens.app.test_client().get(
                "/admin/abrechnungen?token=admin-token"
            )

        assert response.status_code == 503

    def test_billing_json_scopes_period_to_community(self, app_with_tokens):
        get_period = MagicMock(
            return_value={"id": 42, "community_id": "community-a", "line_items": []}
        )
        with patch.object(app_with_tokens.db, "get_billing_period", get_period):
            response = app_with_tokens.app.test_client().get(
                "/api/billing/community/community-a/period/42",
                headers={"X-Admin-Token": "admin-token"},
            )

        assert response.status_code == 200
        get_period.assert_called_once_with(42, "community-a")

    def test_billing_workspace_explains_why_it_is_empty(self, app_with_tokens):
        import admin

        with patch.object(
            admin.billing_workspace,
            "load",
            return_value={"empty": True, "periods": [], "selected": None},
        ):
            response = app_with_tokens.app.test_client().get(
                "/admin/abrechnungen?token=admin-token",
                headers={"Accept": "text/html"},
            )

        html = response.get_data(as_text=True)
        assert response.status_code == 200
        assert "Keine Abrechnungsentwürfe vorhanden" in html
        assert "Messdatenimport" in html
        assert "VNB-Abgleich" in html

    def test_billing_json_hides_missing_or_cross_community_period(
        self, app_with_tokens
    ):
        with patch.object(
            app_with_tokens.db, "get_billing_period", return_value=None
        ) as get_period:
            response = app_with_tokens.app.test_client().get(
                "/api/billing/community/community-b/period/42",
                headers={"X-Admin-Token": "admin-token"},
            )

        assert response.status_code == 404
        get_period.assert_called_once_with(42, "community-b")

    def test_billing_json_reports_storage_failure_as_unavailable(self, app_with_tokens):
        with patch.object(
            app_with_tokens.db,
            "get_billing_period",
            side_effect=app_with_tokens.db.BillingStoreError("offline"),
        ):
            response = app_with_tokens.app.test_client().get(
                "/api/billing/community/community-a/period/42",
                headers={"X-Admin-Token": "admin-token"},
            )

        assert response.status_code == 503

    def test_billing_template_uses_no_forbidden_dash_characters(self):
        template_path = os.path.join(
            PROJECT_ROOT, "templates", "admin", "abrechnungen.html"
        )

        with open(template_path) as handle:
            template = handle.read()

        assert "–" not in template
        assert "—" not in template
