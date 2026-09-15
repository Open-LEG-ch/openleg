# SPDX-License-Identifier: AGPL-3.0-or-later

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

TEMPLATES = Path(__file__).parents[1] / "templates"


def test_billing_actions_fail_closed_when_capabilities_are_absent():
    template = Environment(loader=FileSystemLoader(TEMPLATES)).get_template(
        "leg_billing.html"
    )

    rendered = template.render(
        community_id="c1",
        periods=[{"id": 1, "approvable": True}],
        invoices=[],
        queries_by_invoice={},
    )

    assert "/billing/period/1/approve" not in rendered


def test_approval_only_operator_sees_the_approval_action():
    template = Environment(loader=FileSystemLoader(TEMPLATES)).get_template(
        "leg_billing.html"
    )

    rendered = template.render(
        community_id="c1",
        periods=[{"id": 1, "approvable": True}],
        invoices=[],
        can_prepare_billing=False,
        can_approve_billing=True,
    )

    assert "/billing/period/1/approve" in rendered
