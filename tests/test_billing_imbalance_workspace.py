# SPDX-License-Identifier: AGPL-3.0-or-later
"""Blocked billing periods and per-period imbalance releases in the workspace.

A period the VNB delivered with too large a gap between its two sides gets no
draft. The workspace shows the figures, so the LEG can investigate or complain
to the VNB, and lets a billing preparer raise the tolerance for exactly that
period, with a reason, up to 2%. The draft is then created immediately.
"""

from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from tests.test_billing_approval import WORKSPACE_URL, _draft, _patch_workspace
from tests.test_dashboard_access_routes import _set_session, app_module  # noqa: F401

ZURICH = ZoneInfo("Europe/Zurich")
AUGUST_START = datetime(2026, 8, 1, tzinfo=ZURICH)
AUGUST_END = datetime(2026, 9, 1, tzinfo=ZURICH)
RELEASE_URL = WORKSPACE_URL + "/imbalance-release"
BLOCK = {
    "period_start": AUGUST_START,
    "period_end": AUGUST_END,
    "community_consumption_kwh": Decimal("1497.000000"),
    "community_production_kwh": Decimal("1500.000000"),
    "imbalance_kwh": Decimal("3.000000"),
    "imbalance_ratio": Decimal("0.002004"),
    "tolerance_ratio": Decimal("0.001000"),
    "recorded_at": datetime(2026, 9, 2, 3, 0, tzinfo=ZURICH),
    "release_ratio": None,
    "release_reason": None,
    "release_by": None,
    "release_at": None,
}
FORM = {
    "csrf_token": "csrf-secret",
    "period_start": AUGUST_START.isoformat(),
    "period_end": AUGUST_END.isoformat(),
    "tolerance_pct": "0.3",
    "reason": "BKW Ticket 4711: Rundung bestätigt",
}


def _patch_blocks(monkeypatch, app_module, blocks=None, *, members=None):  # noqa: F811
    _patch_workspace(monkeypatch, app_module, members=members)
    monkeypatch.setattr(app_module.db, "is_db_available", lambda: True)
    monkeypatch.setattr(
        app_module.db, "list_invoice_queries", MagicMock(return_value=[])
    )
    listed = [BLOCK] if blocks is None else list(blocks)
    monkeypatch.setattr(
        app_module.db,
        "list_billing_imbalance_blocks",
        MagicMock(side_effect=lambda _community_id: list(listed)),
        raising=False,
    )
    save = MagicMock(return_value={"id": 9, "released_at": None})
    monkeypatch.setattr(
        app_module.db, "save_billing_imbalance_release", save, raising=False
    )

    live = []
    monkeypatch.setattr(
        app_module.db,
        "get_billing_period_for_window",
        MagicMock(side_effect=lambda *_args: {"id": 77} if live else None),
    )

    def drafted(*_args, **_kwargs):
        # A created draft is live and takes the window out of the blocked list.
        listed.clear()
        live.append(77)
        return {"status": "created", "period_id": 77}

    run = MagicMock(side_effect=drafted)
    monkeypatch.setattr(
        app_module.dashboard_module.billing_runner, "run_billing_period", run
    )
    return save, run


def _client(app_module, building_id="building-admin"):  # noqa: F811
    client = app_module.web.test_client()
    _set_session(client, building_id=building_id)
    return client


def test_a_blocked_period_shows_its_figures_and_the_release_form(
    app_module,  # noqa: F811
    monkeypatch,
):
    _patch_blocks(monkeypatch, app_module)

    html = _client(app_module).get(WORKSPACE_URL).get_data(as_text=True)

    assert "Blockierte Perioden" in html
    assert "August 2026" in html
    assert "1497.000 kWh" in html
    assert "1500.000 kWh" in html
    assert "3.000 kWh" in html
    # The measured deviation is shown rounded up, so typing it in passes.
    assert "0.2004 %" in html
    assert "0.1 %" in html
    assert f'action="{RELEASE_URL}"' in html
    assert f'value="{AUGUST_START.isoformat()}"' in html
    assert 'name="tolerance_pct"' in html
    assert 'name="reason"' in html


def test_a_released_period_shows_who_raised_the_tolerance_and_why(
    app_module,  # noqa: F811
    monkeypatch,
):
    released = {
        **BLOCK,
        "release_ratio": Decimal("0.001500"),
        "release_reason": "BKW Ticket 4711",
        "release_by": "building-admin",
        "release_at": datetime(2026, 9, 3, 10, 0, tzinfo=ZURICH),
    }
    _patch_blocks(monkeypatch, app_module, [released])

    html = _client(app_module).get(WORKSPACE_URL).get_data(as_text=True)

    assert "0.15 %" in html
    assert "BKW Ticket 4711" in html
    assert "building-admin" in html


def test_an_auditor_sees_the_figures_but_cannot_release(
    app_module,  # noqa: F811
    monkeypatch,
):
    members = [
        {
            "building_id": "building-auditor",
            "role": "member",
            "status": "confirmed",
            "access_roles": ["auditor"],
        }
    ]
    _patch_blocks(monkeypatch, app_module, members=members)

    html = (
        _client(app_module, "building-auditor")
        .get(WORKSPACE_URL)
        .get_data(as_text=True)
    )

    assert "0.2004 %" in html
    assert RELEASE_URL not in html


def test_without_blocks_the_section_is_absent(app_module, monkeypatch):  # noqa: F811
    _patch_blocks(monkeypatch, app_module, [])

    html = _client(app_module).get(WORKSPACE_URL).get_data(as_text=True)

    assert "Blockierte Perioden" not in html


def test_a_release_is_saved_and_the_draft_created_immediately(
    app_module,  # noqa: F811
    monkeypatch,
):
    save, run = _patch_blocks(monkeypatch, app_module)

    response = _client(app_module).post(RELEASE_URL, data=FORM)

    assert response.status_code == 302
    assert response.headers["Location"].endswith(WORKSPACE_URL + "?imbalance=released")
    save.assert_called_once_with(
        "community-a",
        AUGUST_START,
        AUGUST_END,
        Decimal("0.003"),
        "BKW Ticket 4711: Rundung bestätigt",
        "building-admin",
    )
    # The releaser prepared this draft, so dual control keeps them from approving.
    run.assert_called_once_with(
        "community-a", AUGUST_START, AUGUST_END, prepared_by="building-admin"
    )


@pytest.mark.parametrize("typed", ["0,25", "0.25 %", "0,25%"])
def test_a_swiss_decimal_comma_and_a_percent_sign_are_accepted(
    app_module,  # noqa: F811
    monkeypatch,
    typed,
):
    """The page shows every figure with a percent sign; copying one must work."""
    save, _ = _patch_blocks(monkeypatch, app_module)

    _client(app_module).post(RELEASE_URL, data={**FORM, "tolerance_pct": typed})

    assert save.call_args.args[3] == Decimal("0.0025")


def test_a_run_that_still_fails_keeps_the_release_and_says_so(
    app_module,  # noqa: F811
    monkeypatch,
):
    save, run = _patch_blocks(monkeypatch, app_module)
    run.side_effect = app_module.dashboard_module.billing_runner.BillingRunError(
        "tariff", code="billing_tariff_missing"
    )

    response = _client(app_module).post(RELEASE_URL, data=FORM)

    assert response.status_code == 302
    assert response.headers["Location"].endswith(WORKSPACE_URL + "?imbalance=pending")
    save.assert_called_once()
    html = (
        _client(app_module)
        .get(WORKSPACE_URL + "?imbalance=pending")
        .get_data(as_text=True)
    )
    assert "noch nicht erstellt" in html


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("tolerance_pct", "", "Toleranz"),
        ("tolerance_pct", "abc", "Toleranz"),
        ("tolerance_pct", "0", "Toleranz"),
        ("tolerance_pct", "1e999999999", "Toleranz"),
        # Finer than a stored ratio: rounding could lift it over the deviation.
        ("tolerance_pct", "0.20035", "Toleranz"),
        ("tolerance_pct", "2.01", "höchstens 2 %"),
        ("tolerance_pct", "0.15", "0.2004 %"),
        ("reason", "   ", "Begründung"),
        ("reason", "x" * 501, "Begründung"),
    ],
)
def test_an_invalid_release_is_refused_with_a_reason(
    app_module,  # noqa: F811
    monkeypatch,
    field,
    value,
    message,
):
    save, run = _patch_blocks(monkeypatch, app_module)

    response = _client(app_module).post(RELEASE_URL, data={**FORM, field: value})

    assert response.status_code == 400
    assert message in response.get_data(as_text=True)
    save.assert_not_called()
    run.assert_not_called()


def test_only_a_blocked_window_can_be_released(app_module, monkeypatch):  # noqa: F811
    save, run = _patch_blocks(monkeypatch, app_module)
    september = datetime(2026, 9, 1, tzinfo=ZURICH).isoformat()

    response = _client(app_module).post(
        RELEASE_URL, data={**FORM, "period_start": september}
    )

    # Usually a draft was created meanwhile: back to the workspace, saying so.
    assert response.status_code == 302
    assert response.headers["Location"].endswith(WORKSPACE_URL + "?imbalance=gone")
    save.assert_not_called()
    run.assert_not_called()
    html = (
        _client(app_module)
        .get(WORKSPACE_URL + "?imbalance=gone")
        .get_data(as_text=True)
    )
    assert "nicht mehr blockiert" in html


def test_a_refused_release_is_not_rendered_without_workspace_access(
    app_module,  # noqa: F811
    monkeypatch,
):
    """Access lost between the check and the re-render is a 403, not a broken page."""
    _patch_blocks(monkeypatch, app_module)
    monkeypatch.setattr(
        app_module.dashboard_module,
        "leg_billing_workspace_view",
        lambda *_args, **_kwargs: {"error": "Kein Zugriff."},
    )

    response = _client(app_module).post(RELEASE_URL, data={**FORM, "reason": " "})

    assert response.status_code == 403


def test_a_release_requires_csrf(app_module, monkeypatch):  # noqa: F811
    save, _ = _patch_blocks(monkeypatch, app_module)

    response = _client(app_module).post(
        RELEASE_URL, data={k: v for k, v in FORM.items() if k != "csrf_token"}
    )

    assert response.status_code == 400
    save.assert_not_called()


@pytest.mark.parametrize(
    "members",
    [
        [
            {
                "building_id": "building-admin",
                "role": "member",
                "status": "confirmed",
                "access_roles": ["auditor"],
            }
        ],
        [
            {
                "building_id": "building-admin",
                "role": "member",
                "status": "confirmed",
                "access_roles": ["billing_approver"],
            }
        ],
    ],
    ids=["auditor", "approver"],
)
def test_a_release_requires_the_preparer_capability(
    app_module,  # noqa: F811
    monkeypatch,
    members,
):
    save, run = _patch_blocks(monkeypatch, app_module, members=members)

    response = _client(app_module).post(RELEASE_URL, data=FORM)

    assert response.status_code == 403
    save.assert_not_called()
    run.assert_not_called()


def test_a_run_that_leaves_the_window_blocked_reports_pending(
    app_module,  # noqa: F811
    monkeypatch,
):
    """A run that stores no live draft, e.g. a withdrawn source, is no success."""
    _, run = _patch_blocks(monkeypatch, app_module)
    run.side_effect = None
    run.return_value = {"status": "created", "period_id": 77}

    response = _client(app_module).post(RELEASE_URL, data=FORM)

    assert response.headers["Location"].endswith(WORKSPACE_URL + "?imbalance=pending")


def test_an_imbalance_above_the_cap_offers_no_release(
    app_module,  # noqa: F811
    monkeypatch,
):
    _patch_blocks(
        monkeypatch, app_module, [{**BLOCK, "imbalance_ratio": Decimal("0.03")}]
    )

    html = _client(app_module).get(WORKSPACE_URL).get_data(as_text=True)

    assert "3 %" in html
    assert RELEASE_URL not in html
    assert "korrigierte Lieferung" in html


def test_the_approver_sees_the_release_a_draft_was_billed_under(
    app_module,  # noqa: F811
    monkeypatch,
):
    _patch_blocks(monkeypatch, app_module, [])
    draft = _draft()
    draft["reconciliation"]["vnb_imbalance_release"] = {
        "id": 9,
        "max_imbalance_ratio": "0.003000",
        "reason": "BKW Ticket 4711",
        "released_by": "building-preparer",
        "released_at": "2026-02-03T10:00:00+01:00",
    }
    app_module.db.list_community_billing_periods.return_value = [draft]

    html = _client(app_module).get(WORKSPACE_URL).get_data(as_text=True)

    assert "Angehobene Toleranz:" in html
    assert "0.3 %" in html
    assert "BKW Ticket 4711" in html
    assert "building-preparer" in html


def test_the_displayed_minimum_is_accepted(app_module, monkeypatch):  # noqa: F811
    save, _ = _patch_blocks(monkeypatch, app_module)

    response = _client(app_module).post(
        RELEASE_URL, data={**FORM, "tolerance_pct": "0.2004"}
    )

    assert response.status_code == 302
    assert save.call_args.args[3] == Decimal("0.002004")


def test_a_release_is_never_shown_below_the_deviation_it_covers(
    app_module,  # noqa: F811
    monkeypatch,
):
    """Both figures are exact, so a covering release never reads as lower."""
    released = {
        **BLOCK,
        "release_ratio": Decimal("0.002004"),
        "release_reason": "BKW Ticket 4711",
        "release_by": "building-admin",
        "release_at": datetime(2026, 9, 3, 10, 0, tzinfo=ZURICH),
    }
    _patch_blocks(monkeypatch, app_module, [released])

    html = _client(app_module).get(WORKSPACE_URL).get_data(as_text=True)

    # The deviation and the release covering it read the same.
    assert html.count("0.2004 %") == 2


@pytest.mark.parametrize(
    ("released_by", "offered"),
    [("building-admin", False), ("building-preparer", True)],
)
@pytest.mark.parametrize("readable_ratio", [True, False])
def test_dual_control_offers_no_approval_to_the_person_who_released(
    app_module,  # noqa: F811
    monkeypatch,
    released_by,
    offered,
    readable_ratio,
):
    """Even a release whose ratio cannot be shown keeps its author from approving."""
    from tests.test_billing_approval import _status

    _patch_blocks(monkeypatch, app_module, [])
    monkeypatch.setattr(
        app_module.dashboard_module.formation_wizard,
        "get_community_status",
        MagicMock(return_value={**_status(), "require_dual_control": True}),
    )
    draft = _draft(prepared_by="building-preparer-2")
    draft["reconciliation"]["vnb_imbalance_release"] = {
        "id": 9,
        "max_imbalance_ratio": "0.003000" if readable_ratio else "kaputt",
        "reason": "BKW Ticket 4711",
        "released_by": released_by,
        "released_at": "2026-02-03T10:00:00+01:00",
    }
    app_module.db.list_community_billing_periods.return_value = [draft]

    html = _client(app_module).get(WORKSPACE_URL).get_data(as_text=True)

    assert ("Rechnungen freigeben" in html) is offered


@pytest.mark.parametrize("error", ["store", "driver"])
def test_a_failing_recheck_after_a_created_draft_still_reports_it(
    app_module,  # noqa: F811
    monkeypatch,
    error,
):
    """The release and the draft are committed; a lost recheck changes nothing."""
    import psycopg2.pool

    _patch_blocks(monkeypatch, app_module)
    app_module.db.get_billing_period_for_window.side_effect = (
        app_module.db.BillingStoreError("pool")
        if error == "store"
        else psycopg2.pool.PoolError("connection pool exhausted")
    )

    response = _client(app_module).post(RELEASE_URL, data=FORM)

    assert response.status_code == 302
    assert response.headers["Location"].endswith(WORKSPACE_URL + "?imbalance=released")


def test_a_release_reason_is_escaped(app_module, monkeypatch):  # noqa: F811
    released = {
        **BLOCK,
        "release_ratio": Decimal("0.003"),
        "release_reason": "<script>alert(1)</script>",
        "release_by": "building-admin",
        "release_at": datetime(2026, 9, 3, 10, 0, tzinfo=ZURICH),
    }
    _patch_blocks(monkeypatch, app_module, [released])

    html = _client(app_module).get(WORKSPACE_URL).get_data(as_text=True)

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


@pytest.mark.parametrize("period_start", ["2026-09", "", "2026-08-01T00:00:00"])
def test_a_malformed_window_is_refused_not_reported_as_gone(
    app_module,  # noqa: F811
    monkeypatch,
    period_start,
):
    save, run = _patch_blocks(monkeypatch, app_module)

    response = _client(app_module).post(
        RELEASE_URL, data={**FORM, "period_start": period_start}
    )

    assert response.status_code == 400
    save.assert_not_called()
    run.assert_not_called()


def test_line_breaks_count_once_towards_the_reason_limit(
    app_module,  # noqa: F811
    monkeypatch,
):
    """Browsers count a line break once but submit it as CRLF."""
    save, _ = _patch_blocks(monkeypatch, app_module)
    # 497 characters as the browser counts them, 502 as submitted with CRLF.
    reason = "\r\n".join(["x" * 82] * 6)

    response = _client(app_module).post(RELEASE_URL, data={**FORM, "reason": reason})

    assert response.status_code == 302
    assert save.call_args.args[4] == reason.replace("\r\n", "\n")
