# SPDX-License-Identifier: AGPL-3.0-or-later
"""Member and board views display each contribution year's stored amount."""

import re
from datetime import date
from decimal import Decimal

import pytest
from flask import render_template

from tests import test_i18n

app_module = test_i18n.app_module
isolated_environment = test_i18n.isolated_environment
web = test_i18n.web


@pytest.mark.parametrize("template", ["member", "application"])
@pytest.mark.parametrize(
    "locale,amounts,received",
    [
        ("de", ["37.25", "82.5", "50"], "12.25"),
        ("en", ["37.25", "82.5", "50"], "12.25"),
        ("fr", ["37,25", "82,5", "50"], "12,25"),
        ("it", ["37.25", "82.5", "50"], "12.25"),
    ],
)
def test_member_and_board_render_stored_dues_for_each_year(
    web, template, locale, amounts, received
):
    # View fixtures deliberately bypass the storage constraint fixing dues at 50.
    dues = [
        {
            "id": str(year),
            "period_start": date(year, 6, 1),
            "due_on": date(year, 7, 1),
            "amount_chf": Decimal(amount),
            "received_chf": Decimal(paid),
            "outstanding_chf": Decimal(outstanding),
            "paid_at": date(year, 6, 15) if outstanding == "0" else None,
            "reminder_sent_at": None,
            "can_remind": True,
            "can_terminate": False,
        }
        for year, amount, paid, outstanding in [
            (2024, "37.25", "12.25", "25.00"),
            (2025, "82.50", "0", "82.50"),
            (2026, "50.00", "50.00", "0"),
        ]
    ]
    record = {
        "id": "application-id",
        "name": "Ada",
        "email": "ada@example.org",
        "status": "accepted",
        "member_id": "member-id",
        "member_status": "active",
        "requests": [],
        "dues": dues,
    }
    web.config["SITE_RELEASE_ENABLED"] = True
    with web.test_request_context("/verein/mitglied?lang=" + locale):
        rendered = render_template(
            "verein/" + template + ".html", record=record, csrf_token="test-csrf"
        )

    rows = re.findall(
        r'<article\b[^>]*data-dues-id="([^"]+)"[^>]*>(.*?)</article>',
        rendered,
        re.DOTALL,
    )
    assert [identifier for identifier, _ in rows] == ["2024", "2025", "2026"]
    for (_, row), amount in zip(rows, amounts, strict=True):
        assert f'"mt-3">CHF {amount} · ' in row
    assert f": CHF {received}</p>" in rows[0][1]
    assert ": CHF 0</p>" in rows[1][1]
    assert ": CHF 50</p>" in rows[2][1]
    if template == "application":
        for (_, row), outstanding in zip(rows[:2], ["25.00", "82.50"], strict=True):
            assert 'action="/verein/vorstand/antraege/application-id/payment"' in row
            assert f'min="0.01" max="{outstanding}" step="0.01" required' in row
        assert "<form" not in rows[2][1]
    else:
        assert all("<form" not in row for _, row in rows)
