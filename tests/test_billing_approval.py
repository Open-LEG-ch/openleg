# SPDX-License-Identifier: AGPL-3.0-or-later
"""Acceptance contract for approving reconciled billing drafts (#399)."""

import os
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import MagicMock

import psycopg2.extras
import pytest

import billing_approval
import database
from tests.test_dashboard_access_routes import _set_session, app_module  # noqa: F401

COMMUNITY = "community-a"
WORKSPACE_URL = f"/leg/community/{COMMUNITY}/billing"


def _policy(**overrides):
    policy = {
        "tariff_id": 7,
        "effective_from": "2026-01-01T00:00:00+01:00",
        "internal_price_chf_per_kwh": "0.150000",
        "grid_fee_chf_per_kwh": "0.080000",
        "network_level": "same",
        "distribution_model": "proportional",
        "vat_mode": "standard",
        "vat_rate_pct": "8.1",
        "payment_days": 30,
        "invoice_prefix": "MUSTER",
        "delivery_method": "download",
    }
    policy.update(overrides)
    return policy


def _draft(**overrides):
    period = {
        "id": 42,
        "community_id": COMMUNITY,
        "status": "draft",
        "period_start": "2026-01-01T00:00:00+01:00",
        "period_end": "2026-02-01T00:00:00+01:00",
        "input_fingerprint": "a" * 64,
        "source_document_ids": ["swisseldex-bkw-2026-01"],
        "reconciliation": {
            "difference_kwh": 0,
            "production_difference_kwh": 0,
            "per_participant": {
                "building-a": {"difference_kwh": 0},
            },
            "production_per_participant": {
                "building-b": {"difference_kwh": 0},
            },
        },
        "billing_policy_snapshot": _policy(),
        "line_items": [
            {
                "id": 3,
                "participant_id": "building-b",
                "item_type": "producer_credit",
                "quantity_kwh": Decimal("13.366667"),
                "unit_price_chf_per_kwh": Decimal("0.150000"),
                "amount_chf": Decimal("-2.005000"),
            },
            {
                "id": 1,
                "participant_id": "building-a",
                "item_type": "consumer_charge",
                "quantity_kwh": Decimal("66.700000"),
                "unit_price_chf_per_kwh": Decimal("0.150000"),
                "amount_chf": Decimal("10.004000"),
            },
            {
                "id": 2,
                "participant_id": "building-a",
                "item_type": "rounding_adjustment",
                "quantity_kwh": None,
                "unit_price_chf_per_kwh": None,
                "amount_chf": Decimal("0.001000"),
            },
        ],
    }
    period.update(overrides)
    return period


def test_prepare_snapshots_groups_participants_and_rounds_chf_half_up():
    snapshots = billing_approval.prepare_invoice_snapshots(
        _draft(), issue_date=date(2026, 2, 5)
    )

    assert [row["participant_id"] for row in snapshots] == [
        "building-a",
        "building-b",
    ]
    charged, credited = snapshots
    assert charged["net_chf"] == Decimal("10.01")
    assert charged["vat_rate_pct"] == Decimal("8.1")
    assert charged["vat_chf"] == Decimal("0.81")
    assert charged["gross_chf"] == Decimal("10.82")
    assert credited["net_chf"] == Decimal("-2.01")
    assert credited["vat_chf"] == Decimal("-0.16")
    assert credited["gross_chf"] == Decimal("-2.17")
    assert charged["issue_date"] == date(2026, 2, 5)
    assert charged["due_date"] == date(2026, 3, 7)
    assert charged["policy_snapshot"] == _policy()
    assert charged["source_document_ids"] == ["swisseldex-bkw-2026-01"]
    assert charged["input_fingerprint"] == "a" * 64
    assert [item["id"] for item in charged["line_items_snapshot"]] == [1, 2]


def test_prepare_snapshots_applies_no_vat_when_policy_says_none():
    draft = _draft(billing_policy_snapshot=_policy(vat_mode="none", vat_rate_pct="0"))

    snapshots = billing_approval.prepare_invoice_snapshots(
        draft, issue_date=date(2026, 2, 5)
    )

    assert all(row["vat_chf"] == Decimal("0.00") for row in snapshots)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda draft: draft.update(status="issued"),
        lambda draft: draft.update(input_fingerprint=""),
        lambda draft: draft.update(source_document_ids=[]),
        lambda draft: draft.update(reconciliation={}),
        lambda draft: draft.update(line_items=[]),
        lambda draft: draft.update(billing_policy_snapshot=None),
        lambda draft: draft.update(reconciliation={"some_other_value": 0}),
    ],
)
def test_prepare_snapshots_refuses_incomplete_or_non_draft_periods(mutation):
    draft = _draft()
    mutation(draft)

    with pytest.raises(billing_approval.BillingApprovalError):
        billing_approval.prepare_invoice_snapshots(draft, issue_date=date(2026, 2, 5))


@pytest.mark.parametrize("missing_field", tuple(_policy()))
def test_prepare_snapshots_requires_every_policy_field(missing_field):
    draft = _draft()
    draft["billing_policy_snapshot"].pop(missing_field)

    with pytest.raises(billing_approval.BillingApprovalError):
        billing_approval.prepare_invoice_snapshots(draft, issue_date=date(2026, 2, 5))


def test_prepare_snapshots_refuses_reconciliation_line_participant_mismatch():
    draft = _draft()
    draft["line_items"] = [
        item for item in draft["line_items"] if item["participant_id"] != "building-b"
    ]

    with pytest.raises(billing_approval.BillingApprovalError):
        billing_approval.prepare_invoice_snapshots(draft, issue_date=date(2026, 2, 5))


@pytest.mark.parametrize("difference", [Decimal("0.000001"), float("nan"), "NaN"])
def test_prepare_snapshots_refuses_every_nonzero_or_nonfinite_nested_gap(difference):
    draft = _draft()
    draft["reconciliation"]["per_participant"]["building-a"]["difference_kwh"] = (
        difference
    )

    with pytest.raises(billing_approval.BillingApprovalError):
        billing_approval.prepare_invoice_snapshots(draft, issue_date=date(2026, 2, 5))


def _status(members=None):
    return {
        "community_id": COMMUNITY,
        "name": "LEG Musterweg",
        "status": "active",
        "members": members
        or [{"building_id": "building-admin", "role": "admin", "status": "confirmed"}],
    }


def _patch_workspace(monkeypatch, app_module, *, members=None):  # noqa: F811
    monkeypatch.setattr(
        app_module.dashboard_module.formation_wizard,
        "get_community_status",
        MagicMock(return_value=_status(members)),
    )
    monkeypatch.setattr(
        app_module.db,
        "list_community_billing_periods",
        MagicMock(return_value=[_draft()]),
        raising=False,
    )
    approve = MagicMock(return_value=[{"invoice_number": "MUSTER-2026-000001"}])
    monkeypatch.setattr(app_module.db, "approve_billing_period", approve, raising=False)
    return approve


def test_billing_workspace_requires_a_confirmed_admin(app_module, monkeypatch):  # noqa: F811
    _patch_workspace(monkeypatch, app_module)
    assert app_module.web.test_client().get(WORKSPACE_URL).status_code == 401

    for building_id, members in (
        (
            "member",
            [{"building_id": "member", "role": "member", "status": "confirmed"}],
        ),
        (
            "invited-admin",
            [{"building_id": "invited-admin", "role": "admin", "status": "invited"}],
        ),
        (
            "stranger",
            [{"building_id": "someone-else", "role": "admin", "status": "confirmed"}],
        ),
    ):
        _patch_workspace(monkeypatch, app_module, members=members)
        client = app_module.web.test_client()
        _set_session(client, building_id=building_id)
        assert client.get(WORKSPACE_URL).status_code == 403


def test_billing_workspace_distinguishes_drafts_and_issued_invoices(
    app_module,  # noqa: F811
    monkeypatch,
):
    _patch_workspace(monkeypatch, app_module)
    app_module.db.list_community_billing_periods.return_value = [
        _draft(),
        _draft(id=43, status="issued"),
    ]
    client = app_module.web.test_client()
    _set_session(client, building_id="building-admin")

    response = client.get(WORKSPACE_URL)

    assert response.status_code == 200
    assert "no-store" in response.headers["Cache-Control"]
    html = response.get_data(as_text=True)
    assert "Entwurf" in html
    assert "Freigegeben" in html
    assert html.count('name="confirm_approval"') == 1
    assert html.count(WORKSPACE_URL + "/period/42/approve") == 1
    assert WORKSPACE_URL + "/period/43/approve" not in html
    assert 'name="csrf_token" value="csrf-secret"' in html


def test_billing_approval_requires_csrf_and_explicit_confirmation(
    app_module,  # noqa: F811
    monkeypatch,
):
    approve = _patch_workspace(monkeypatch, app_module)
    client = app_module.web.test_client()
    _set_session(client, building_id="building-admin")
    url = WORKSPACE_URL + "/period/42/approve"

    assert client.post(url, data={"confirm_approval": "yes"}).status_code == 400
    assert client.post(url, data={"csrf_token": "csrf-secret"}).status_code == 400
    assert (
        client.post(
            url,
            data={"csrf_token": "csrf-secret", "confirm_approval": "no"},
        ).status_code
        == 400
    )
    approve.assert_not_called()


def test_billing_approval_post_requires_confirmed_admin(
    app_module,  # noqa: F811
    monkeypatch,
):
    url = WORKSPACE_URL + "/period/42/approve"
    form = {"csrf_token": "csrf-secret", "confirm_approval": "yes"}

    _patch_workspace(monkeypatch, app_module)
    assert app_module.web.test_client().post(url, data=form).status_code == 401

    for building_id, members in (
        (
            "member",
            [{"building_id": "member", "role": "member", "status": "confirmed"}],
        ),
        (
            "invited-admin",
            [{"building_id": "invited-admin", "role": "admin", "status": "invited"}],
        ),
        (
            "stranger",
            [{"building_id": "someone-else", "role": "admin", "status": "confirmed"}],
        ),
    ):
        approve = _patch_workspace(monkeypatch, app_module, members=members)
        client = app_module.web.test_client()
        _set_session(client, building_id=building_id)
        assert client.post(url, data=form).status_code == 403
        approve.assert_not_called()


def test_confirmed_admin_can_approve_only_the_exact_community_period(
    app_module,  # noqa: F811
    monkeypatch,
):
    approve = _patch_workspace(monkeypatch, app_module)
    client = app_module.web.test_client()
    _set_session(client, building_id="building-admin")

    response = client.post(
        WORKSPACE_URL + "/period/42/approve",
        data={"csrf_token": "csrf-secret", "confirm_approval": "yes"},
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/billing?approved=1")
    approve.assert_called_once_with(42, COMMUNITY)


def test_billing_approval_fails_closed_on_storage_error(app_module, monkeypatch):  # noqa: F811
    approve = _patch_workspace(monkeypatch, app_module)
    approve.side_effect = app_module.db.BillingStoreError("database unavailable")
    client = app_module.web.test_client()
    _set_session(client, building_id="building-admin")

    response = client.post(
        WORKSPACE_URL + "/period/42/approve",
        data={"csrf_token": "csrf-secret", "confirm_approval": "yes"},
    )

    assert response.status_code == 503


@pytest.mark.integration
def test_postgres_approval_is_atomic_idempotent_and_rolls_back_partial_writes():
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("needs a live database")

    from store.schema import create_tables
    from tests.test_schema_migrations import _pool_against, _temporary_database

    def seed_period(
        cur,
        *,
        participants,
        period_start="2026-01-01 00:00:00+01",
        period_end="2026-02-01 00:00:00+01",
    ):
        cur.execute(
            """
            INSERT INTO billing_periods (
                community_id, period_start, period_end, status,
                input_fingerprint, source_document_ids, reconciliation,
                billing_policy_snapshot
            ) VALUES (
                %s, %s::timestamptz, %s::timestamptz, 'draft', %s, %s, %s, %s
            ) RETURNING id
            """,
            (
                COMMUNITY,
                period_start,
                period_end,
                "a" * 64,
                psycopg2.extras.Json(["DOC-1"]),
                psycopg2.extras.Json(
                    {
                        "difference_kwh": 0,
                        "per_participant": {
                            participant: {"difference_kwh": 0}
                            for participant in participants
                        },
                    }
                ),
                psycopg2.extras.Json(_policy()),
            ),
        )
        period_id = cur.fetchone()["id"]
        for offset, participant in enumerate(participants, start=1):
            cur.execute(
                """
                INSERT INTO billing_line_items (
                    billing_period_id, participant_id, item_type,
                    quantity_kwh, unit_price_chf_per_kwh, amount_chf
                ) VALUES (%s, %s, 'consumer_charge', 10, 0.15, %s)
                """,
                (period_id, participant, Decimal(offset)),
            )
        return period_id

    with _temporary_database() as url, _pool_against(url):
        create_tables()
        with database.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO buildings (building_id, email, address, lat, lon)
                VALUES
                    ('building-a', 'a@example.ch', 'Musterweg 1', 47, 8),
                    ('building-b', 'b@example.ch', 'Musterweg 2', 47, 8),
                    ('building-c', 'c@example.ch', 'Musterweg 3', 47, 8),
                    ('building-d', 'd@example.ch', 'Musterweg 4', 47, 8)
                """
            )
            cur.execute(
                """
                INSERT INTO communities (community_id, name, status)
                VALUES (%s, 'LEG Musterweg', 'active')
                """,
                (COMMUNITY,),
            )
            period_id = seed_period(cur, participants=("building-a", "building-b"))

        first = database.approve_billing_period(
            period_id, COMMUNITY, issue_date=date(2026, 2, 5)
        )
        retry = database.approve_billing_period(
            period_id, COMMUNITY, issue_date=date(2026, 2, 5)
        )

        assert [(row["id"], row["invoice_number"]) for row in retry] == [
            (row["id"], row["invoice_number"]) for row in first
        ]
        assert [row["participant_id"] for row in first] == [
            "building-a",
            "building-b",
        ]
        assert [row["invoice_number"] for row in first] == [
            "MUSTER-2026-000001",
            "MUSTER-2026-000002",
        ]
        with database.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT status FROM billing_periods WHERE id = %s", (period_id,)
            )
            assert cur.fetchone()["status"] == "issued"
            cur.execute(
                "SELECT count(*) AS count FROM invoices WHERE billing_period_id = %s",
                (period_id,),
            )
            assert cur.fetchone()["count"] == 2
            cur.execute(
                """
                SELECT participant_id, policy_snapshot, provenance_snapshot,
                       line_items_snapshot, net_chf, vat_rate_pct, vat_chf,
                       gross_chf, issue_date, due_date
                FROM invoices
                WHERE billing_period_id = %s
                ORDER BY participant_id
                """,
                (period_id,),
            )
            stored = [dict(row) for row in cur.fetchall()]

        assert stored[0]["policy_snapshot"] == _policy()
        assert stored[0]["provenance_snapshot"]["input_fingerprint"] == "a" * 64
        assert stored[0]["provenance_snapshot"]["source_document_ids"] == ["DOC-1"]
        assert stored[0]["provenance_snapshot"]["reconciliation"]["difference_kwh"] == 0
        assert stored[0]["provenance_snapshot"]["period_start"] == (
            "2025-12-31T23:00:00+00:00"
        )
        assert stored[0]["provenance_snapshot"]["period_end"] == (
            "2026-01-31T23:00:00+00:00"
        )
        assert [
            item["participant_id"] for item in stored[0]["line_items_snapshot"]
        ] == ["building-a"]
        assert stored[0]["net_chf"] == Decimal("1.00")
        assert stored[0]["vat_rate_pct"] == Decimal("8.1")
        assert stored[0]["vat_chf"] == Decimal("0.08")
        assert stored[0]["gross_chf"] == Decimal("1.08")
        assert stored[0]["issue_date"] == date(2026, 2, 5)
        assert stored[0]["due_date"] == date(2026, 3, 7)
        assert stored[1]["net_chf"] == Decimal("2.00")
        assert stored[1]["vat_chf"] == Decimal("0.16")
        assert stored[1]["gross_chf"] == Decimal("2.16")

        with database.get_connection() as conn, conn.cursor() as cur:
            continuation_period_id = seed_period(
                cur,
                participants=("building-c",),
                period_start="2026-02-01 00:00:00+01",
                period_end="2026-03-01 00:00:00+01",
            )

        continued = database.approve_billing_period(
            continuation_period_id, COMMUNITY, issue_date=date(2026, 3, 5)
        )
        assert [row["invoice_number"] for row in continued] == ["MUSTER-2026-000003"]

        with database.get_connection() as conn, conn.cursor() as cur:
            rollback_period_id = seed_period(
                cur,
                participants=("building-a", "building-d"),
                period_start="2026-03-01 00:00:00+01",
                period_end="2026-04-01 00:00:00+02",
            )
            cur.execute(
                """
                CREATE FUNCTION reject_building_d_invoice() RETURNS trigger AS $$
                BEGIN
                    IF NEW.participant_id = 'building-d' THEN
                        RAISE EXCEPTION 'forced invoice failure';
                    END IF;
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql;
                CREATE TRIGGER reject_building_d_invoice
                BEFORE INSERT ON invoices
                FOR EACH ROW EXECUTE FUNCTION reject_building_d_invoice()
                """
            )

        with pytest.raises(database.BillingStoreError):
            database.approve_billing_period(
                rollback_period_id, COMMUNITY, issue_date=date(2026, 2, 5)
            )

        with database.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT status FROM billing_periods WHERE id = %s",
                (rollback_period_id,),
            )
            assert cur.fetchone()["status"] == "draft"
            cur.execute(
                "SELECT count(*) AS count FROM invoices WHERE billing_period_id = %s",
                (rollback_period_id,),
            )
            assert cur.fetchone()["count"] == 0


# ---------------------------------------------------------------------------
# Regression contracts from Codex review of #399 (money-path gaps).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("vat_mode", "vat_rate"),
    [
        ("reduced", "8.1"),
        ("NONE", "0"),
        ("none", "8.1"),
        ("none", "0.01"),
        ("standard", "0"),
        ("standard", "-1"),
        ("standard", "100.1"),
        ("standard", "nan"),
        ("standard", "inf"),
    ],
)
def test_prepare_refuses_invalid_vat_mode_and_rate_combinations(vat_mode, vat_rate):
    draft = _draft(
        billing_policy_snapshot=_policy(vat_mode=vat_mode, vat_rate_pct=vat_rate)
    )

    with pytest.raises(billing_approval.BillingApprovalError):
        billing_approval.prepare_invoice_snapshots(draft, issue_date=date(2026, 2, 5))


@pytest.mark.parametrize(
    "payment_days", [True, False, 0, 366, -1, "30", 1.5, Decimal(30), None]
)
def test_prepare_refuses_non_integer_or_out_of_range_payment_days(payment_days):
    draft = _draft(billing_policy_snapshot=_policy(payment_days=payment_days))

    with pytest.raises(billing_approval.BillingApprovalError):
        billing_approval.prepare_invoice_snapshots(draft, issue_date=date(2026, 2, 5))


@pytest.mark.parametrize(
    "invoice_prefix",
    ["muster", "M", "-MUSTER", "MUSTER WEG", "MUSTER_WEG", "TOOLONGPREFIX123456", ""],
)
def test_prepare_refuses_invalid_invoice_prefix(invoice_prefix):
    draft = _draft(billing_policy_snapshot=_policy(invoice_prefix=invoice_prefix))

    with pytest.raises(billing_approval.BillingApprovalError):
        billing_approval.prepare_invoice_snapshots(draft, issue_date=date(2026, 2, 5))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("delivery_method", "post"),
        ("delivery_method", "EMAIL"),
        ("distribution_model", "simple"),
        ("distribution_model", "custom"),
        ("distribution_model", "proportional "),
        ("network_level", "different"),
        ("network_level", "SAME"),
    ],
)
def test_prepare_refuses_values_outside_the_policy_enums(field, value):
    draft = _draft(billing_policy_snapshot=_policy(**{field: value}))

    with pytest.raises(billing_approval.BillingApprovalError):
        billing_approval.prepare_invoice_snapshots(draft, issue_date=date(2026, 2, 5))


@pytest.mark.parametrize(
    "bad_issue_date",
    ["2026-02-05", 20260205, datetime(2026, 2, 5, tzinfo=UTC), 5.0, object()],
)
def test_prepare_refuses_invalid_issue_date_types(bad_issue_date):
    with pytest.raises(billing_approval.BillingApprovalError):
        billing_approval.prepare_invoice_snapshots(_draft(), issue_date=bad_issue_date)


@pytest.mark.parametrize(
    "line_items",
    [
        "not-a-list",
        {"participant_id": "building-a"},
        [None],
        [["building-a"]],
        [{"item_type": "consumer_charge", "amount_chf": Decimal("1.00")}],
    ],
)
def test_prepare_refuses_malformed_line_item_structures(line_items):
    draft = _draft(line_items=line_items)

    with pytest.raises(billing_approval.BillingApprovalError):
        billing_approval.prepare_invoice_snapshots(draft, issue_date=date(2026, 2, 5))


@pytest.mark.parametrize("participant_id", ["", "   ", 42, None, ["building-a"]])
def test_prepare_refuses_empty_or_non_string_participant_ids(participant_id):
    draft = _draft()
    draft["line_items"][0] = {
        **draft["line_items"][0],
        "participant_id": participant_id,
    }

    with pytest.raises(billing_approval.BillingApprovalError):
        billing_approval.prepare_invoice_snapshots(draft, issue_date=date(2026, 2, 5))


@pytest.mark.parametrize(
    "section",
    [
        {"per_participant": "building-a"},
        {"production_per_participant": 42},
    ],
)
def test_prepare_refuses_malformed_reconciliation_participant_sections(section):
    draft = _draft()
    draft["reconciliation"].update(section)

    with pytest.raises(billing_approval.BillingApprovalError):
        billing_approval.prepare_invoice_snapshots(draft, issue_date=date(2026, 2, 5))


class _ApprovalCursor:
    """Fake cursor answering the approve_billing_period query sequence."""

    def __init__(self, period, line_items, invoices=(), community_active=True):
        self.executed = []
        self._last_query = ""
        self._period = period
        self._line_items = line_items
        self._invoices = list(invoices)
        self._community_row = (
            {"community_id": COMMUNITY, "status": "active"}
            if community_active
            else None
        )

    def execute(self, query, params=None):
        self.executed.append((query, params))
        self._last_query = query

    def fetchone(self):
        if "FROM communities" in self._last_query:
            return self._community_row
        if "FROM billing_periods" in self._last_query:
            return self._period
        return None

    def fetchall(self):
        if "FROM billing_line_items" in self._last_query:
            return self._line_items
        if "FROM invoices" in self._last_query:
            return [] if "LIKE" in self._last_query else self._invoices
        return []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _approve_connection(monkeypatch, cursor):
    from contextlib import contextmanager

    class _Connection:
        def cursor(self):
            return cursor

    @contextmanager
    def _factory():
        yield _Connection()

    monkeypatch.setattr(database, "get_connection", _factory)


def _period_row(**overrides):
    return {
        key: value for key, value in _draft(**overrides).items() if key != "line_items"
    }


def test_approve_locks_the_active_community_row_before_the_period(monkeypatch):
    cursor = _ApprovalCursor(
        _period_row(),
        _draft()["line_items"],
        invoices=[
            {
                "id": 1,
                "invoice_number": "MUSTER-2026-000001",
                "participant_id": "building-a",
            }
        ],
    )
    _approve_connection(monkeypatch, cursor)

    database.approve_billing_period(42, COMMUNITY, issue_date=date(2026, 2, 5))

    queries = [" ".join(query.split()) for query, _ in cursor.executed]
    community_lock = next(q for q in queries if "FROM communities" in q)
    period_lock = next(q for q in queries if "FROM billing_periods" in q)
    assert "FOR UPDATE" in community_lock
    assert "community_id = %s" in community_lock
    assert "status = 'active'" in community_lock
    assert "FOR UPDATE" in period_lock
    assert queries.index(community_lock) < queries.index(period_lock)


def test_approve_refuses_a_missing_or_inactive_community_before_touching_periods(
    monkeypatch,
):
    cursor = _ApprovalCursor(
        _period_row(), _draft()["line_items"], community_active=False
    )
    _approve_connection(monkeypatch, cursor)

    with pytest.raises(billing_approval.BillingApprovalError):
        database.approve_billing_period(42, COMMUNITY, issue_date=date(2026, 2, 5))

    assert not any("FROM billing_periods" in query for query, _ in cursor.executed)


def test_approve_refuses_a_wrong_state_period_as_a_domain_conflict(monkeypatch):
    cursor = _ApprovalCursor(_period_row(status="cancelled"), _draft()["line_items"])
    _approve_connection(monkeypatch, cursor)

    with pytest.raises(billing_approval.BillingApprovalError) as exc:
        database.approve_billing_period(42, COMMUNITY, issue_date=date(2026, 2, 5))

    assert not isinstance(exc.value, database.BillingStoreError)


def test_approve_refuses_an_unknown_period_as_a_domain_conflict(monkeypatch):
    cursor = _ApprovalCursor(None, [])
    _approve_connection(monkeypatch, cursor)

    with pytest.raises(billing_approval.BillingApprovalError) as exc:
        database.approve_billing_period(999, COMMUNITY, issue_date=date(2026, 2, 5))

    assert not isinstance(exc.value, database.BillingStoreError)


def test_approve_wraps_storage_outages_as_store_errors(monkeypatch):
    from contextlib import contextmanager

    @contextmanager
    def unavailable_connection():
        raise RuntimeError("connection refused")
        yield

    monkeypatch.setattr(database, "get_connection", unavailable_connection)

    with pytest.raises(database.BillingStoreError):
        database.approve_billing_period(42, COMMUNITY, issue_date=date(2026, 2, 5))


def test_billing_approval_domain_conflict_returns_a_private_409_without_details(
    app_module,  # noqa: F811
    monkeypatch,
):
    approve = _patch_workspace(monkeypatch, app_module)
    approve.side_effect = billing_approval.BillingApprovalError(
        "internal detail: difference_kwh mismatch for building-a"
    )
    client = app_module.web.test_client()
    _set_session(client, building_id="building-admin")

    response = client.post(
        WORKSPACE_URL + "/period/42/approve",
        data={"csrf_token": "csrf-secret", "confirm_approval": "yes"},
    )

    assert response.status_code == 409
    assert "no-store" in response.headers["Cache-Control"]
    html = response.get_data(as_text=True)
    assert "nicht freigegeben" in html
    assert "difference_kwh" not in html
    assert "internal detail" not in html


def test_workspace_omits_the_approval_action_for_visibly_unreconciled_drafts(
    app_module,  # noqa: F811
    monkeypatch,
):
    _patch_workspace(monkeypatch, app_module)
    draft = _draft()
    draft["reconciliation"]["difference_kwh"] = 3
    app_module.db.list_community_billing_periods.return_value = [draft]
    client = app_module.web.test_client()
    _set_session(client, building_id="building-admin")

    response = client.get(WORKSPACE_URL)

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'name="confirm_approval"' not in html
    assert WORKSPACE_URL + "/period/42/approve" not in html
    assert "nicht freigabebereit" in html


def test_workspace_omits_the_approval_action_without_source_documents(
    app_module,  # noqa: F811
    monkeypatch,
):
    _patch_workspace(monkeypatch, app_module)
    app_module.db.list_community_billing_periods.return_value = [
        _draft(source_document_ids=[])
    ]
    client = app_module.web.test_client()
    _set_session(client, building_id="building-admin")

    response = client.get(WORKSPACE_URL)

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'name="confirm_approval"' not in html
    assert WORKSPACE_URL + "/period/42/approve" not in html
    assert "nicht freigabebereit" in html
