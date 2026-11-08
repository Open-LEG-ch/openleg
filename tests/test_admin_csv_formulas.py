# SPDX-License-Identifier: AGPL-3.0-or-later
"""Administrative CSV exports keep resident text from becoming spreadsheet code."""

import csv
import io

import pytest

from tests import security_review_fixtures

app_module = security_review_fixtures.app_module


@pytest.mark.parametrize(
    "value", ["=1+1", "+SUM(1,2)", "-1+2", "@SUM(1,2)", "\t=1+1", " \r\n+1+1"]
)
def test_export_neutralizes_formula_prefixes_in_every_text_cell(
    app_module, monkeypatch, value
):
    monkeypatch.setenv("ADMIN_TOKEN", "private-admin-credential")
    monkeypatch.setattr(
        app_module.db,
        "get_operator_building_profiles",
        lambda **kwargs: [
            {
                "address": value,
                "email": value,
                "consumption": 4500,
                "name": "Plain text",
            }
        ],
    )
    response = app_module.web.test_client().get(
        "/admin/export?format=csv",
        headers={"X-Admin-Token": "private-admin-credential"},
    )
    assert response.status_code == 200
    row = next(csv.DictReader(io.StringIO(response.text)))
    assert row == {
        "address": "'" + value,
        "email": "'" + value,
        "consumption": "4500",
        "name": "Plain text",
    }
