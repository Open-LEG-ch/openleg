# SPDX-License-Identifier: AGPL-3.0-or-later

from pathlib import Path

import pytest
from flask import Flask, render_template

from i18n import init_app

TEMPLATES = Path(__file__).parents[1] / "templates"


@pytest.fixture
def template_app():
    application = Flask(__name__, template_folder=str(TEMPLATES))
    application.secret_key = "role-template-test"
    init_app(application)
    return application


def test_billing_actions_fail_closed_when_capabilities_are_absent(template_app):
    with template_app.test_request_context("/leg/community/c1/billing"):
        rendered = render_template(
            "leg_billing.html",
            community_id="c1",
            periods=[{"id": 1, "approvable": True, "can_approve": True}],
            invoices=[],
            queries_by_invoice={},
        )

    assert "/billing/period/1/approve" not in rendered


def test_approval_only_operator_sees_the_approval_action(template_app):
    with template_app.test_request_context("/leg/community/c1/billing"):
        rendered = render_template(
            "leg_billing.html",
            community_id="c1",
            periods=[{"id": 1, "approvable": True, "can_approve": True}],
            invoices=[],
            can_prepare_billing=False,
            can_approve_billing=True,
        )

    assert "/billing/period/1/approve" in rendered


def test_approval_only_operator_can_reach_billing_workspace(template_app):
    with template_app.test_request_context("/leg/dashboard?cid=c1"):
        rendered = render_template(
            "leg_dashboard.html",
            community={
                "community_id": "c1",
                "status": "active",
                "status_label": "Aktiv",
                "distribution_model_label": "Gleichmässig",
            },
            viewer_has_session=True,
            can_manage_members=False,
            can_prepare_billing=False,
            can_approve_billing=True,
            can_audit_billing=True,
        )

    assert 'href="/leg/community/c1/billing"' in rendered
    assert 'href="/leg/community/c1/billing-policy"' not in rendered
