# SPDX-License-Identifier: AGPL-3.0-or-later
"""No operator action may release unequal VNB energy for billing."""

from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

import billing_runner
from tests.test_billing_approval import WORKSPACE_URL, _patch_workspace
from tests.test_dashboard_access_routes import _set_session, app_module  # noqa: F401

START = datetime(2026, 8, 1, tzinfo=ZoneInfo("Europe/Zurich"))
END = datetime(2026, 9, 1, tzinfo=ZoneInfo("Europe/Zurich"))
RELEASE_URL = WORKSPACE_URL + "/imbalance-release"


def _workspace(monkeypatch, app_module, *, blocked=True):  # noqa: F811
    _patch_workspace(monkeypatch, app_module)
    monkeypatch.setattr(app_module.db, "is_db_available", lambda: True)
    monkeypatch.setattr(app_module.db, "list_invoice_queries", lambda *_a, **_k: [])
    monkeypatch.setattr(app_module.db, "get_sdat_veracity_flags", lambda *_: [])
    block = {
        "period_start": START,
        "period_end": END,
        "community_consumption_kwh": Decimal(3999),
        "community_production_kwh": Decimal(4000),
        "imbalance_kwh": Decimal(1),
        "imbalance_ratio": Decimal("0.000251"),
        "tolerance_ratio": Decimal("0.001"),
        "recorded_at": END,
        "release_ratio": None,
        "release_reason": None,
        "release_by": None,
        "release_at": None,
    }
    # A stale proposal-era block must not bring back release controls.
    monkeypatch.setattr(
        app_module.db,
        "list_billing_imbalance_blocks",
        lambda *_: [block] if blocked else [],
        raising=False,
    )
    monkeypatch.setattr(app_module.db, "get_billing_period_for_window", lambda *_: None)
    save = MagicMock(return_value={"id": 7})
    monkeypatch.setattr(
        app_module.db, "save_billing_imbalance_release", save, raising=False
    )
    run = MagicMock(return_value={"status": "created", "period_id": 42})
    monkeypatch.setattr(billing_runner, "run_billing_period", run)
    client = app_module.web.test_client()
    _set_session(client, building_id="building-admin")
    return client, save, run


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_operator_imbalance_release_endpoint_is_absent(app_module, monkeypatch, method):  # noqa: F811
    client, save, run = _workspace(monkeypatch, app_module)

    response = client.open(
        RELEASE_URL,
        method=method,
        data={
            "csrf_token": "csrf-secret",
            "period_start": START.isoformat(),
            "period_end": END.isoformat(),
            "tolerance_pct": "2",
            "reason": "Obsolete exception request",
        },
    )

    assert response.status_code == 404
    save.assert_not_called()
    run.assert_not_called()


@pytest.mark.parametrize("blocked", [False, True], ids=["no-block", "stale-block"])
def test_billing_workspace_offers_no_tolerance_or_release_control(
    app_module,  # noqa: F811
    monkeypatch,
    blocked,
):
    client, save, run = _workspace(monkeypatch, app_module, blocked=blocked)

    response = client.get(WORKSPACE_URL)

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Abrechnung" in html
    assert RELEASE_URL not in html
    assert 'name="tolerance_pct"' not in html
    assert "Toleranz für diese Periode" not in html
    assert "Toleranz speichern" not in html
    save.assert_not_called()
    run.assert_not_called()
