# SPDX-License-Identifier: AGPL-3.0-or-later
"""Acceptance contract for approving reconciled billing drafts (#399)."""

import json
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
        "community_id": COMMUNITY,
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
            "vnb_allocated_kwh": 13.366670,
            "engine_allocated_kwh": 13.366670,
            "difference_kwh": 0,
            "per_participant": {
                "building-a": {
                    "vnb_kwh": 13.366667,
                    "engine_kwh": 13.366667,
                    "difference_kwh": 0,
                },
                "building-c": {
                    "vnb_kwh": 0.000003,
                    "engine_kwh": 0.000003,
                    "difference_kwh": 0,
                },
            },
            "vnb_production_kwh": 13.36667,
            "engine_production_kwh": 13.36667,
            "production_difference_kwh": 0,
            "production_per_participant": {
                "building-b": {
                    "vnb_kwh": 13.36667,
                    "engine_kwh": 13.36667,
                    "difference_kwh": 0,
                },
            },
        },
        "billing_policy_snapshot": _policy(),
        "line_items": [
            {
                "id": 3,
                "participant_id": "building-b",
                "item_type": "producer_credit",
                "quantity_kwh": Decimal("13.366670"),
                "unit_price_chf_per_kwh": Decimal("0.150000"),
                "amount_chf": Decimal("-2.005001"),
            },
            {
                "id": 1,
                "participant_id": "building-a",
                "item_type": "consumer_charge",
                "quantity_kwh": Decimal("13.366667"),
                "unit_price_chf_per_kwh": Decimal("0.150000"),
                "amount_chf": Decimal("2.005000"),
            },
            {
                "id": 2,
                "participant_id": "building-b",
                "item_type": "rounding_adjustment",
                "quantity_kwh": None,
                "unit_price_chf_per_kwh": None,
                "amount_chf": Decimal("0.000001"),
            },
            {
                "id": 4,
                "participant_id": "building-c",
                "item_type": "consumer_charge",
                "quantity_kwh": Decimal("0.000003"),
                "unit_price_chf_per_kwh": Decimal("0.150000"),
                "amount_chf": Decimal("0.000000"),
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
        "building-c",
    ]
    charged, credited, tiny = snapshots
    assert charged["net_chf"] == Decimal("2.01")
    assert charged["vat_rate_pct"] == Decimal("8.1")
    assert charged["vat_chf"] == Decimal("0.16")
    assert charged["gross_chf"] == Decimal("2.17")
    assert credited["net_chf"] == Decimal("-2.01")
    assert credited["vat_chf"] == Decimal("-0.16")
    assert credited["gross_chf"] == Decimal("-2.17")
    assert tiny["net_chf"] == Decimal("0.00")
    assert charged["issue_date"] == date(2026, 2, 5)
    assert charged["due_date"] == date(2026, 3, 7)
    assert charged["policy_snapshot"] == _policy()
    assert charged["source_document_ids"] == ["swisseldex-bkw-2026-01"]
    assert charged["input_fingerprint"] == "a" * 64
    assert [item["id"] for item in charged["line_items_snapshot"]] == [1]


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
        consumer = participants[0]
        producer = participants[1] if len(participants) > 1 else participants[0]
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
                        "vnb_allocated_kwh": 10,
                        "engine_allocated_kwh": 10,
                        "difference_kwh": 0,
                        "per_participant": {
                            consumer: {
                                "vnb_kwh": 10,
                                "engine_kwh": 10,
                                "difference_kwh": 0,
                            }
                        },
                        "vnb_production_kwh": 10,
                        "engine_production_kwh": 10,
                        "production_difference_kwh": 0,
                        "production_per_participant": {
                            producer: {
                                "vnb_kwh": 10,
                                "engine_kwh": 10,
                                "difference_kwh": 0,
                            }
                        },
                    }
                ),
                psycopg2.extras.Json(_policy()),
            ),
        )
        period_id = cur.fetchone()["id"]
        cur.execute(
            """
            INSERT INTO billing_line_items (
                billing_period_id, participant_id, item_type,
                quantity_kwh, unit_price_chf_per_kwh, amount_chf
            ) VALUES (%s, %s, 'consumer_charge', 10, 0.15, %s)
            """,
            (period_id, consumer, Decimal("1.500000")),
        )
        cur.execute(
            """
            INSERT INTO billing_line_items (
                billing_period_id, participant_id, item_type,
                quantity_kwh, unit_price_chf_per_kwh, amount_chf
            ) VALUES (%s, %s, 'producer_credit', 10, 0.15, %s)
            """,
            (period_id, producer, Decimal("-1.500000")),
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
        assert stored[0]["net_chf"] == Decimal("1.50")
        assert stored[0]["vat_rate_pct"] == Decimal("8.1")
        assert stored[0]["vat_chf"] == Decimal("0.12")
        assert stored[0]["gross_chf"] == Decimal("1.62")
        assert stored[0]["issue_date"] == date(2026, 2, 5)
        assert stored[0]["due_date"] == date(2026, 3, 7)
        assert stored[1]["net_chf"] == Decimal("-1.50")
        assert stored[1]["vat_chf"] == Decimal("-0.12")
        assert stored[1]["gross_chf"] == Decimal("-1.62")

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


@pytest.mark.parametrize(
    "missing_path",
    [
        ("difference_kwh",),
        ("production_difference_kwh",),
        ("per_participant", "building-a", "difference_kwh"),
        ("production_per_participant", "building-b", "difference_kwh"),
    ],
)
def test_prepare_requires_every_canonical_reconciliation_gap(missing_path):
    draft = _draft()
    parent = draft["reconciliation"]
    for key in missing_path[:-1]:
        parent = parent[key]
    parent.pop(missing_path[-1])

    with pytest.raises(billing_approval.BillingApprovalError):
        billing_approval.prepare_invoice_snapshots(draft, issue_date=date(2026, 2, 5))


@pytest.mark.parametrize(
    "fingerprint",
    [True, 42, "a", "g" * 64, "a" * 63, "a" * 65, " a" * 32],
)
def test_prepare_requires_a_sha256_shaped_input_fingerprint(fingerprint):
    with pytest.raises(billing_approval.BillingApprovalError):
        billing_approval.prepare_invoice_snapshots(
            _draft(input_fingerprint=fingerprint), issue_date=date(2026, 2, 5)
        )


@pytest.mark.parametrize(
    "source_document_ids",
    ["DOC-1", {"DOC-1"}, {"id": "DOC-1"}, [""], ["   "], [42], ["DOC-1", None]],
)
def test_prepare_requires_a_nonempty_document_id_sequence(source_document_ids):
    with pytest.raises(billing_approval.BillingApprovalError):
        billing_approval.prepare_invoice_snapshots(
            _draft(source_document_ids=source_document_ids),
            issue_date=date(2026, 2, 5),
        )


@pytest.mark.parametrize(
    ("period_start", "period_end"),
    [
        (None, "2026-02-01T00:00:00+01:00"),
        ("not-a-date", "2026-02-01T00:00:00+01:00"),
        ("2026-02-01T00:00:00+01:00", "2026-01-01T00:00:00+01:00"),
        ("2026-01-01T00:00:00+01:00", "not-a-date"),
    ],
)
def test_prepare_requires_a_valid_increasing_billing_window(period_start, period_end):
    with pytest.raises(billing_approval.BillingApprovalError):
        billing_approval.prepare_invoice_snapshots(
            _draft(period_start=period_start, period_end=period_end),
            issue_date=date(2026, 2, 5),
        )


@pytest.mark.parametrize("tariff_id", [True, False, 0, -1, "7", ""])
def test_prepare_requires_a_positive_integer_tariff_id(tariff_id):
    with pytest.raises(billing_approval.BillingApprovalError):
        billing_approval.prepare_invoice_snapshots(
            _draft(billing_policy_snapshot=_policy(tariff_id=tariff_id)),
            issue_date=date(2026, 2, 5),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("internal_price_chf_per_kwh", "NaN"),
        ("internal_price_chf_per_kwh", "Infinity"),
        ("internal_price_chf_per_kwh", "-0.01"),
        ("internal_price_chf_per_kwh", "10.000001"),
        ("grid_fee_chf_per_kwh", "not-a-number"),
        ("grid_fee_chf_per_kwh", "-0.01"),
        ("grid_fee_chf_per_kwh", "10.000001"),
    ],
)
def test_prepare_requires_valid_policy_prices(field, value):
    with pytest.raises(billing_approval.BillingApprovalError):
        billing_approval.prepare_invoice_snapshots(
            _draft(billing_policy_snapshot=_policy(**{field: value})),
            issue_date=date(2026, 2, 5),
        )


@pytest.mark.parametrize(
    "effective_from",
    ["never", "", "2026-01-02T00:00:00+01:00"],
)
def test_prepare_requires_policy_effective_at_period_start(effective_from):
    with pytest.raises(billing_approval.BillingApprovalError):
        billing_approval.prepare_invoice_snapshots(
            _draft(billing_policy_snapshot=_policy(effective_from=effective_from)),
            issue_date=date(2026, 2, 5),
        )


class _ApprovalCursor:
    """Fake cursor answering the approve_billing_period query sequence."""

    def __init__(self, period, line_items, invoices=(), community_active=True):
        self.executed = []
        self._last_query = ""
        self._period = period
        self._line_items = line_items
        self._invoices = list(invoices)
        self._community_row = (
            {
                "community_id": COMMUNITY,
                "name": "LEG Musterweg",
                "status": "active",
            }
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
    assert "name" in community_lock
    assert "community_id = %s" in community_lock
    assert "status = 'active'" in community_lock
    assert "FOR UPDATE" in period_lock
    assert queries.index(community_lock) < queries.index(period_lock)

    invoice_insert = next(
        params for query, params in cursor.executed if "INSERT INTO invoices" in query
    )
    provenance = json.loads(invoice_insert[6])
    assert provenance["issuer"] == {
        "community_id": COMMUNITY,
        "name": "LEG Musterweg",
    }


def test_prepare_freezes_the_validated_rounding_adjustment_proof():
    snapshots = billing_approval.prepare_invoice_snapshots(
        _draft(), issue_date=date(2026, 2, 5)
    )

    proofs = {
        snapshot["participant_id"]: snapshot["provenance_snapshot"][
            "rounding_adjustment"
        ]
        for snapshot in snapshots
    }
    assert proofs["building-b"] == {
        "participant_id": "building-b",
        "amount_chf": "0.000001",
    }
    assert proofs["building-a"] is None
    assert proofs["building-c"] is None


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


@pytest.mark.parametrize("bad_issue_date", [False, 0, ""])
def test_store_does_not_replace_falsey_invalid_issue_dates(monkeypatch, bad_issue_date):
    cursor = _ApprovalCursor(_period_row(), _draft()["line_items"])
    _approve_connection(monkeypatch, cursor)

    with pytest.raises(billing_approval.BillingApprovalError):
        database.approve_billing_period(42, COMMUNITY, issue_date=bad_issue_date)


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


# ---------------------------------------------------------------------------
# Regression contracts from the final #399 re-review (money-path evidence).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "missing_key",
    [
        "vnb_allocated_kwh",
        "engine_allocated_kwh",
        "vnb_production_kwh",
        "engine_production_kwh",
    ],
)
def test_prepare_requires_canonical_reconciliation_totals(missing_key):
    draft = _draft()
    draft["reconciliation"].pop(missing_key)

    with pytest.raises(billing_approval.BillingApprovalError):
        billing_approval.prepare_invoice_snapshots(draft, issue_date=date(2026, 2, 5))


@pytest.mark.parametrize(
    ("section", "participant"),
    [
        ("per_participant", "building-a"),
        ("production_per_participant", "building-b"),
    ],
)
@pytest.mark.parametrize("missing_key", ["vnb_kwh", "engine_kwh"])
def test_prepare_requires_canonical_participant_evidence(
    section, participant, missing_key
):
    draft = _draft()
    draft["reconciliation"][section][participant].pop(missing_key)

    with pytest.raises(billing_approval.BillingApprovalError):
        billing_approval.prepare_invoice_snapshots(draft, issue_date=date(2026, 2, 5))


@pytest.mark.parametrize(
    ("path", "value"),
    [
        # Difference no longer equals engine minus vnb.
        (("vnb_allocated_kwh",), "13.0"),
        (("vnb_production_kwh",), "13.0"),
        (("per_participant", "building-a", "vnb_kwh"), "13.0"),
        # Non-finite evidence is not evidence.
        (("engine_allocated_kwh",), "nan"),
        (("vnb_production_kwh",), "inf"),
        (("per_participant", "building-a", "vnb_kwh"), "-inf"),
        (("production_per_participant", "building-b", "engine_kwh"), "nan"),
    ],
)
def test_prepare_refuses_inconsistent_or_nonfinite_reconciliation_evidence(path, value):
    draft = _draft()
    node = draft["reconciliation"]
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value

    with pytest.raises(billing_approval.BillingApprovalError):
        billing_approval.prepare_invoice_snapshots(draft, issue_date=date(2026, 2, 5))


@pytest.mark.parametrize(
    ("allocated_key", "production_key"),
    [
        (("vnb_allocated_kwh", "engine_allocated_kwh"), None),
        (None, ("vnb_production_kwh", "engine_production_kwh")),
    ],
)
def test_prepare_cross_checks_engine_totals_against_line_quantities(
    allocated_key, production_key
):
    """Internally consistent zeros still fail when line quantities say 13.4."""
    draft = _draft()
    for key in allocated_key or production_key:
        draft["reconciliation"][key] = 0

    with pytest.raises(billing_approval.BillingApprovalError):
        billing_approval.prepare_invoice_snapshots(draft, issue_date=date(2026, 2, 5))


@pytest.mark.parametrize(
    ("section", "participant"),
    [
        ("per_participant", "building-a"),
        ("production_per_participant", "building-b"),
    ],
)
def test_prepare_cross_checks_participant_engine_kwh_against_line_quantity(
    section, participant
):
    """A self-consistent participant entry must still match the billed kWh."""
    draft = _draft()
    entry = draft["reconciliation"][section][participant]
    entry["vnb_kwh"] = 5
    entry["engine_kwh"] = 5

    with pytest.raises(billing_approval.BillingApprovalError):
        billing_approval.prepare_invoice_snapshots(draft, issue_date=date(2026, 2, 5))


@pytest.mark.parametrize("item_type", ["grid_fee", "", None, 42])
def test_prepare_refuses_unknown_line_item_types(item_type):
    draft = _draft()
    draft["line_items"][0] = {**draft["line_items"][0], "item_type": item_type}

    with pytest.raises(billing_approval.BillingApprovalError):
        billing_approval.prepare_invoice_snapshots(draft, issue_date=date(2026, 2, 5))


@pytest.mark.parametrize(
    ("index", "field", "value"),
    [
        # Sign discipline: consumers pay, producers are credited.
        (1, "amount_chf", Decimal("-2.005000")),
        (0, "amount_chf", Decimal("2.005001")),
        # Amount must equal quantity times unit price at 6-decimal precision.
        (1, "amount_chf", Decimal("2.005001")),
        (0, "amount_chf", Decimal("-2.005000")),
        # Quantities and unit prices are finite and non-negative.
        (1, "quantity_kwh", Decimal("-13.366667")),
        (1, "quantity_kwh", Decimal("NaN")),
        (1, "quantity_kwh", None),
        (0, "unit_price_chf_per_kwh", Decimal("-0.150000")),
        (0, "unit_price_chf_per_kwh", Decimal("Infinity")),
        # Unit price must equal the stored policy internal price.
        (1, "unit_price_chf_per_kwh", Decimal("0.160000")),
        (0, "unit_price_chf_per_kwh", Decimal("0.080000")),
        # Amount is required and finite.
        (1, "amount_chf", None),
        (1, "amount_chf", Decimal("NaN")),
        # Rounding adjustments carry no quantity or unit price.
        (2, "quantity_kwh", Decimal("0.000007")),
        (2, "unit_price_chf_per_kwh", Decimal("0.150000")),
        (2, "amount_chf", Decimal("Infinity")),
    ],
)
def test_prepare_refuses_line_items_that_break_the_money_math(index, field, value):
    draft = _draft()
    draft["line_items"][index] = {**draft["line_items"][index], field: value}

    with pytest.raises(billing_approval.BillingApprovalError):
        billing_approval.prepare_invoice_snapshots(draft, issue_date=date(2026, 2, 5))


def test_prepare_refuses_a_second_rounding_adjustment():
    draft = _draft()
    draft["line_items"].append(
        {
            "id": 4,
            "participant_id": "building-b",
            "item_type": "rounding_adjustment",
            "quantity_kwh": None,
            "unit_price_chf_per_kwh": None,
            "amount_chf": Decimal(0),
        }
    )

    with pytest.raises(billing_approval.BillingApprovalError):
        billing_approval.prepare_invoice_snapshots(draft, issue_date=date(2026, 2, 5))


def test_prepare_requires_a_zero_sum_pool_when_producer_credits_exist():
    """Without the rounding adjustment the pool leaks 0.000001 CHF."""
    draft = _draft()
    draft["line_items"] = [
        item
        for item in draft["line_items"]
        if item["item_type"] != "rounding_adjustment"
    ]

    with pytest.raises(billing_approval.BillingApprovalError):
        billing_approval.prepare_invoice_snapshots(draft, issue_date=date(2026, 2, 5))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("internal_price_chf_per_kwh", "0.1500005"),
        ("grid_fee_chf_per_kwh", "0.0800001"),
        ("vat_rate_pct", "8.125"),
    ],
)
def test_prepare_refuses_policy_values_beyond_persisted_precision(field, value):
    draft = _draft(billing_policy_snapshot=_policy(**{field: value}))

    with pytest.raises(billing_approval.BillingApprovalError):
        billing_approval.prepare_invoice_snapshots(draft, issue_date=date(2026, 2, 5))


@pytest.mark.parametrize("community_id", [None, "", "   ", "community-b", 42])
def test_prepare_requires_the_policy_snapshot_community_to_match(community_id):
    draft = _draft(billing_policy_snapshot=_policy(community_id=community_id))

    with pytest.raises(billing_approval.BillingApprovalError):
        billing_approval.prepare_invoice_snapshots(draft, issue_date=date(2026, 2, 5))


@pytest.mark.parametrize("community_id", [None, "", "   ", 42])
def test_prepare_requires_a_valid_period_community_id(community_id):
    with pytest.raises(billing_approval.BillingApprovalError):
        billing_approval.prepare_invoice_snapshots(
            _draft(community_id=community_id), issue_date=date(2026, 2, 5)
        )


def test_store_defaults_issue_date_via_public_today(monkeypatch):
    """approve_billing_period must not reach into billing_approval privates."""
    assert not hasattr(billing_approval, "_today")
    monkeypatch.setattr(billing_approval, "today", lambda: date(2026, 2, 5))
    cursor = _ApprovalCursor(_period_row(), _draft()["line_items"])
    _approve_connection(monkeypatch, cursor)

    database.approve_billing_period(42, COMMUNITY)

    inserts = [
        params for query, params in cursor.executed if "INSERT INTO invoices" in query
    ]
    assert inserts
    assert all(
        date(2026, 2, 5) in params and date(2026, 3, 7) in params for params in inserts
    )


def test_prepare_refuses_consumer_amount_that_does_not_match_quantity_times_price():
    """A consumer-only draft isolates the amount == quantity * price guard."""
    draft = _draft()
    draft["line_items"] = [
        {
            "id": 1,
            "participant_id": "building-a",
            "item_type": "consumer_charge",
            "quantity_kwh": Decimal("13.366667"),
            "unit_price_chf_per_kwh": Decimal("0.150000"),
            "amount_chf": Decimal("2.005001"),  # 0.000001 too much
        }
    ]
    draft["reconciliation"] = {
        "vnb_allocated_kwh": 13.366667,
        "engine_allocated_kwh": 13.366667,
        "difference_kwh": 0,
        "per_participant": {
            "building-a": {
                "vnb_kwh": 13.366667,
                "engine_kwh": 13.366667,
                "difference_kwh": 0,
            }
        },
        "vnb_production_kwh": 0,
        "engine_production_kwh": 0,
        "production_difference_kwh": 0,
        "production_per_participant": {},
    }

    with pytest.raises(billing_approval.BillingApprovalError):
        billing_approval.prepare_invoice_snapshots(draft, issue_date=date(2026, 2, 5))


def test_prepare_isolates_the_unit_price_matches_policy_guard():
    """A repriced credit is refused; rounding stays with the min producer."""
    draft = _draft()
    draft["line_items"][0].update(
        unit_price_chf_per_kwh=Decimal("0.160000"),
        amount_chf=Decimal("-2.138667"),  # 13.366670 x 0.16 at 6 decimals
    )
    draft["line_items"][2].update(
        participant_id="building-b", amount_chf=Decimal("0.133667")
    )  # pool stays closed

    with pytest.raises(billing_approval.BillingApprovalError):
        billing_approval.prepare_invoice_snapshots(draft, issue_date=date(2026, 2, 5))


# ---------------------------------------------------------------------------
# Orchestrator review corrections (#399): zero lines, exact reconciliation
# keys, and fail-closed overflow handling.
# ---------------------------------------------------------------------------


def test_prepare_accepts_zero_quantity_consumer_and_producer_lines():
    """Zero lines are allowed when the overall pool still conserves energy."""
    draft = _draft()
    # building-b produces nothing this month but stays a producer participant.
    draft["line_items"][0].update(quantity_kwh=Decimal(0), amount_chf=Decimal(0))
    # A second producer supplies the matching energy.
    draft["line_items"].append(
        {
            "id": 5,
            "participant_id": "building-z",
            "item_type": "producer_credit",
            "quantity_kwh": Decimal("13.366670"),
            "unit_price_chf_per_kwh": Decimal("0.150000"),
            "amount_chf": Decimal("-2.005001"),
        }
    )
    reconciliation = draft["reconciliation"]
    reconciliation["production_per_participant"] = {
        "building-b": {"vnb_kwh": 0, "engine_kwh": 0, "difference_kwh": 0},
        "building-z": {
            "vnb_kwh": 13.36667,
            "engine_kwh": 13.36667,
            "difference_kwh": 0,
        },
    }

    snapshots = billing_approval.prepare_invoice_snapshots(
        draft, issue_date=date(2026, 2, 5)
    )

    assert {row["participant_id"] for row in snapshots} == {
        "building-a",
        "building-b",
        "building-c",
        "building-z",
    }
    by_id = {row["participant_id"]: row for row in snapshots}
    assert by_id["building-a"]["net_chf"] == Decimal("2.01")
    assert by_id["building-b"]["net_chf"] == Decimal("0.00")
    assert by_id["building-c"]["net_chf"] == Decimal("0.00")
    assert by_id["building-z"]["net_chf"] == Decimal("-2.01")


def test_prepare_accepts_zero_quantity_consumer_participant():
    draft = _draft()
    # building-d consumes nothing. The rounding amount stays with building-b,
    # the only producer and therefore the deterministic rounding participant.
    draft["line_items"].insert(
        1,
        {
            "id": 5,
            "participant_id": "building-d",
            "item_type": "consumer_charge",
            "quantity_kwh": Decimal(0),
            "unit_price_chf_per_kwh": Decimal("0.150000"),
            "amount_chf": Decimal(0),
        },
    )
    draft["line_items"][3].update(
        participant_id="building-b", amount_chf=Decimal("0.000001")
    )  # close the pool
    reconciliation = draft["reconciliation"]
    reconciliation["vnb_allocated_kwh"] = 13.366670
    reconciliation["engine_allocated_kwh"] = 13.366670
    reconciliation["per_participant"]["building-d"] = {
        "vnb_kwh": 0,
        "engine_kwh": 0,
        "difference_kwh": 0,
    }

    snapshots = billing_approval.prepare_invoice_snapshots(
        draft, issue_date=date(2026, 2, 5)
    )

    assert {row["participant_id"] for row in snapshots} == {
        "building-a",
        "building-b",
        "building-c",
        "building-d",
    }


@pytest.mark.parametrize(
    ("section", "participant", "other_section"),
    [
        ("per_participant", "building-a", "production_per_participant"),
        ("production_per_participant", "building-b", "per_participant"),
    ],
)
def test_prepare_refuses_reconciliation_key_in_wrong_section(
    section, participant, other_section
):
    """A producer must not appear under per_participant or vice versa."""
    draft = _draft()
    entry = draft["reconciliation"][section].pop(participant)
    draft["reconciliation"][other_section][participant] = entry

    with pytest.raises(billing_approval.BillingApprovalError):
        billing_approval.prepare_invoice_snapshots(draft, issue_date=date(2026, 2, 5))


@pytest.mark.parametrize(
    ("section", "participant", "line_builder"),
    [
        (
            "per_participant",
            "building-c",
            lambda: {
                "id": 4,
                "participant_id": "building-c",
                "item_type": "consumer_charge",
                "quantity_kwh": Decimal(0),
                "unit_price_chf_per_kwh": Decimal("0.150000"),
                "amount_chf": Decimal(0),
            },
        ),
        (
            "production_per_participant",
            "building-c",
            lambda: {
                "id": 4,
                "participant_id": "building-c",
                "item_type": "producer_credit",
                "quantity_kwh": Decimal(0),
                "unit_price_chf_per_kwh": Decimal("0.150000"),
                "amount_chf": Decimal(0),
            },
        ),
    ],
)
def test_prepare_refuses_missing_reconciliation_key_for_zero_line_item(
    section, participant, line_builder
):
    """A billed participant must have a key in the correct reconciliation section."""
    draft = _draft()
    draft["line_items"].insert(1, line_builder())
    # Do NOT add the matching reconciliation key.

    with pytest.raises(billing_approval.BillingApprovalError):
        billing_approval.prepare_invoice_snapshots(draft, issue_date=date(2026, 2, 5))


@pytest.mark.parametrize(
    ("section", "participant", "line_builder"),
    [
        (
            "per_participant",
            "building-c",
            lambda: {
                "id": 4,
                "participant_id": "building-c",
                "item_type": "consumer_charge",
                "quantity_kwh": Decimal(0),
                "unit_price_chf_per_kwh": Decimal("0.150000"),
                "amount_chf": Decimal(0),
            },
        ),
        (
            "production_per_participant",
            "building-c",
            lambda: {
                "id": 4,
                "participant_id": "building-c",
                "item_type": "producer_credit",
                "quantity_kwh": Decimal(0),
                "unit_price_chf_per_kwh": Decimal("0.150000"),
                "amount_chf": Decimal(0),
            },
        ),
    ],
)
def test_prepare_refuses_extra_reconciliation_key_for_unbilled_participant(
    section, participant, line_builder
):
    """A reconciliation key must correspond to a billed line item."""
    draft = _draft()
    draft["reconciliation"][section][participant] = {
        "vnb_kwh": 0,
        "engine_kwh": 0,
        "difference_kwh": 0,
    }

    with pytest.raises(billing_approval.BillingApprovalError):
        billing_approval.prepare_invoice_snapshots(draft, issue_date=date(2026, 2, 5))


@pytest.mark.parametrize(
    "path",
    [
        ("vnb_allocated_kwh",),
        ("engine_allocated_kwh",),
        ("per_participant", "building-a", "vnb_kwh"),
    ],
)
def test_prepare_fails_closed_on_reconciliation_decimal_overflow(path):
    """Absurd reconciliation values must raise BillingApprovalError."""
    draft = _draft()
    node = draft["reconciliation"]
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = "1E+999999"

    with pytest.raises(billing_approval.BillingApprovalError):
        billing_approval.prepare_invoice_snapshots(draft, issue_date=date(2026, 2, 5))


@pytest.mark.parametrize(
    ("index", "field"),
    [
        (1, "quantity_kwh"),
        (1, "amount_chf"),
        (2, "amount_chf"),
    ],
)
def test_prepare_fails_closed_on_line_item_decimal_overflow(index, field):
    """Absurd line-item values must raise BillingApprovalError."""
    draft = _draft()
    draft["line_items"][index][field] = "1E+999999"

    with pytest.raises(billing_approval.BillingApprovalError):
        billing_approval.prepare_invoice_snapshots(draft, issue_date=date(2026, 2, 5))


def test_prepare_refuses_union_only_section_swap_of_zero_participants():
    """Exact section keys matter: a zero consumer must not live under production.

    The billed set is {building-a, building-c, building-d, building-b}. Every
    numeric is zero and canonical, the pool closes, and the union of the two
    reconciliation sections equals the billed set. Union-only matching would
    accept this; exact section-key equality must refuse because building-c is
    billed as a consumer and building-d as a producer, but their reconciliation
    sections are swapped.
    """
    draft = _draft()
    draft["line_items"] = [
        {
            "id": 1,
            "participant_id": "building-a",
            "item_type": "consumer_charge",
            "quantity_kwh": Decimal(0),
            "unit_price_chf_per_kwh": Decimal("0.150000"),
            "amount_chf": Decimal(0),
        },
        {
            "id": 2,
            "participant_id": "building-c",
            "item_type": "consumer_charge",
            "quantity_kwh": Decimal(0),
            "unit_price_chf_per_kwh": Decimal("0.150000"),
            "amount_chf": Decimal(0),
        },
        {
            "id": 3,
            "participant_id": "building-d",
            "item_type": "producer_credit",
            "quantity_kwh": Decimal(0),
            "unit_price_chf_per_kwh": Decimal("0.150000"),
            "amount_chf": Decimal(0),
        },
        {
            "id": 4,
            "participant_id": "building-b",
            "item_type": "producer_credit",
            "quantity_kwh": Decimal(0),
            "unit_price_chf_per_kwh": Decimal("0.150000"),
            "amount_chf": Decimal(0),
        },
    ]
    draft["reconciliation"] = {
        "vnb_allocated_kwh": 0,
        "engine_allocated_kwh": 0,
        "difference_kwh": 0,
        "per_participant": {
            # building-c is billed as a consumer but placed in production.
            "building-d": {"vnb_kwh": 0, "engine_kwh": 0, "difference_kwh": 0},
            "building-a": {"vnb_kwh": 0, "engine_kwh": 0, "difference_kwh": 0},
        },
        "vnb_production_kwh": 0,
        "engine_production_kwh": 0,
        "production_difference_kwh": 0,
        "production_per_participant": {
            # building-d is billed as a producer but placed in consumption.
            "building-c": {"vnb_kwh": 0, "engine_kwh": 0, "difference_kwh": 0},
            "building-b": {"vnb_kwh": 0, "engine_kwh": 0, "difference_kwh": 0},
        },
    }

    with pytest.raises(billing_approval.BillingApprovalError):
        billing_approval.prepare_invoice_snapshots(draft, issue_date=date(2026, 2, 5))


# ---------------------------------------------------------------------------
# Rounding adjustment contract: must match billing_engine semantics exactly.
# ---------------------------------------------------------------------------


def _consumer_only_draft():
    """A valid consumer-only draft without any producer credits."""
    draft = _draft()
    draft["line_items"] = [
        {
            "id": 1,
            "participant_id": "building-a",
            "item_type": "consumer_charge",
            "quantity_kwh": Decimal(0),
            "unit_price_chf_per_kwh": Decimal("0.150000"),
            "amount_chf": Decimal(0),
        }
    ]
    draft["reconciliation"] = {
        "vnb_allocated_kwh": 0,
        "engine_allocated_kwh": 0,
        "difference_kwh": 0,
        "per_participant": {
            "building-a": {
                "vnb_kwh": 0,
                "engine_kwh": 0,
                "difference_kwh": 0,
            }
        },
        "vnb_production_kwh": 0,
        "engine_production_kwh": 0,
        "production_difference_kwh": 0,
        "production_per_participant": {},
    }
    return draft


def test_prepare_refuses_rounding_adjustment_without_producer_credits():
    """Consumer-only drafts have no rounding target; any rounding is a bypass."""
    draft = _consumer_only_draft()
    draft["line_items"].append(
        {
            "id": 2,
            "participant_id": "attacker",
            "item_type": "rounding_adjustment",
            "quantity_kwh": None,
            "unit_price_chf_per_kwh": None,
            "amount_chf": Decimal("999999.000000"),
        }
    )

    with pytest.raises(
        billing_approval.BillingApprovalError, match="ohne Produzentengutschrift"
    ):
        billing_approval.prepare_invoice_snapshots(draft, issue_date=date(2026, 2, 5))


def test_prepare_refuses_rounding_adjustment_for_non_min_producer():
    """billing_engine assigns rounding to min(producer_ids, key=str)."""
    draft = _draft()
    # Add a second producer with a lexicographically larger id.
    draft["line_items"].append(
        {
            "id": 4,
            "participant_id": "building-z",
            "item_type": "producer_credit",
            "quantity_kwh": Decimal(0),
            "unit_price_chf_per_kwh": Decimal("0.150000"),
            "amount_chf": Decimal(0),
        }
    )
    draft["line_items"][2]["participant_id"] = "building-z"  # wrong participant
    draft["reconciliation"]["production_per_participant"]["building-z"] = {
        "vnb_kwh": 0,
        "engine_kwh": 0,
        "difference_kwh": 0,
    }

    with pytest.raises(billing_approval.BillingApprovalError):
        billing_approval.prepare_invoice_snapshots(draft, issue_date=date(2026, 2, 5))


@pytest.mark.parametrize(
    "amount",
    [
        Decimal("0.000002"),  # wrong magnitude
        Decimal("-0.000001"),  # wrong sign
        # Numerically equal to the required 0.000001, but carries 7 fractional places.
        Decimal("0.0000010"),
    ],
)
def test_prepare_refuses_incorrect_rounding_adjustment_amount(amount):
    draft = _draft()
    draft["line_items"][2]["amount_chf"] = amount

    with pytest.raises(billing_approval.BillingApprovalError) as exc_info:
        billing_approval.prepare_invoice_snapshots(draft, issue_date=date(2026, 2, 5))

    if amount == Decimal("0.0000010"):
        assert "6 Dezimalstellen" in str(exc_info.value)


def test_prepare_refuses_missing_required_rounding_adjustment():
    draft = _draft()
    draft["line_items"] = [
        item
        for item in draft["line_items"]
        if item["item_type"] != "rounding_adjustment"
    ]

    with pytest.raises(billing_approval.BillingApprovalError):
        billing_approval.prepare_invoice_snapshots(draft, issue_date=date(2026, 2, 5))


def test_prepare_refuses_unnecessary_rounding_adjustment():
    """When the non-rounding total is already zero, no rounding may exist."""
    draft = _draft()
    # Make consumer and producer amounts equal so the non-rounding total is zero.
    draft["line_items"] = [
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
            "quantity_kwh": Decimal("13.366667"),
            "unit_price_chf_per_kwh": Decimal("0.150000"),
            "amount_chf": Decimal("2.005000"),
        },
        {
            "id": 2,
            "participant_id": "building-b",
            "item_type": "rounding_adjustment",
            "quantity_kwh": None,
            "unit_price_chf_per_kwh": None,
            "amount_chf": Decimal("0.000001"),
        },
    ]
    draft["reconciliation"]["per_participant"]["building-a"].update(
        vnb_kwh=13.366667, engine_kwh=13.366667
    )
    draft["reconciliation"]["production_per_participant"]["building-b"].update(
        vnb_kwh=13.366667, engine_kwh=13.366667
    )
    draft["reconciliation"]["vnb_allocated_kwh"] = 13.366667
    draft["reconciliation"]["engine_allocated_kwh"] = 13.366667
    draft["reconciliation"]["vnb_production_kwh"] = 13.366667
    draft["reconciliation"]["engine_production_kwh"] = 13.366667

    with pytest.raises(billing_approval.BillingApprovalError):
        billing_approval.prepare_invoice_snapshots(draft, issue_date=date(2026, 2, 5))


def test_prepare_accepts_rounding_adjustment_with_zero_amount_producer():
    """A zero producer still counts as a producer participant for rounding."""
    draft = _draft()
    draft["line_items"][0].update(quantity_kwh=Decimal(0), amount_chf=Decimal(0))
    # A second producer supplies the matching energy.
    draft["line_items"].append(
        {
            "id": 5,
            "participant_id": "building-z",
            "item_type": "producer_credit",
            "quantity_kwh": Decimal("13.366670"),
            "unit_price_chf_per_kwh": Decimal("0.150000"),
            "amount_chf": Decimal("-2.005001"),
        }
    )
    draft["reconciliation"]["production_per_participant"] = {
        "building-b": {"vnb_kwh": 0, "engine_kwh": 0, "difference_kwh": 0},
        "building-z": {
            "vnb_kwh": 13.36667,
            "engine_kwh": 13.36667,
            "difference_kwh": 0,
        },
    }

    snapshots = billing_approval.prepare_invoice_snapshots(
        draft, issue_date=date(2026, 2, 5)
    )

    by_id = {row["participant_id"]: row for row in snapshots}
    assert by_id["building-a"]["net_chf"] == Decimal("2.01")
    assert by_id["building-b"]["net_chf"] == Decimal("0.00")
    assert by_id["building-z"]["net_chf"] == Decimal("-2.01")


def test_prepare_accepts_billing_engine_shaped_rounding():
    """A realistic engine output: rounding closes a nonzero cent residue."""
    draft = _draft()
    # 13.366670 * 0.15 = 2.0050005 -> 2.005001;
    # 13.366667 * 0.15 = 2.00500005 -> 2.005000;
    # the remaining 0.000003 kWh round to a zero-cent charge.
    draft["line_items"] = [
        {
            "id": 1,
            "participant_id": "building-a",
            "item_type": "consumer_charge",
            "quantity_kwh": Decimal("13.366667"),
            "unit_price_chf_per_kwh": Decimal("0.150000"),
            "amount_chf": Decimal("2.005000"),
        },
        {
            "id": 2,
            "participant_id": "building-b",
            "item_type": "producer_credit",
            "quantity_kwh": Decimal("13.366670"),
            "unit_price_chf_per_kwh": Decimal("0.150000"),
            "amount_chf": Decimal("-2.005001"),
        },
        {
            "id": 3,
            "participant_id": "building-b",
            "item_type": "rounding_adjustment",
            "quantity_kwh": None,
            "unit_price_chf_per_kwh": None,
            "amount_chf": Decimal("0.000001"),
        },
        {
            "id": 4,
            "participant_id": "building-c",
            "item_type": "consumer_charge",
            "quantity_kwh": Decimal("0.000003"),
            "unit_price_chf_per_kwh": Decimal("0.150000"),
            "amount_chf": Decimal("0.000000"),
        },
    ]
    draft["reconciliation"]["per_participant"]["building-a"].update(
        vnb_kwh=13.366667, engine_kwh=13.366667
    )

    snapshots = billing_approval.prepare_invoice_snapshots(
        draft, issue_date=date(2026, 2, 5)
    )

    assert snapshots


# ---------------------------------------------------------------------------
# Energy conservation and monetary residue bounds.
# ---------------------------------------------------------------------------


def test_prepare_refuses_phantom_energy_rounding_exploit():
    """A zero producer cannot offset a large consumer via rounding alone.

    With energy conserved only by assertion, a reconciled consumer of
    99,999.9 kWh, a zero producer_credit, and a -999,999.00 CHF rounding
    adjustment assigned to that producer currently closes the money pool.
    Approval must reject this because the billed energies do not conserve.
    """
    draft = _draft()
    draft["billing_policy_snapshot"] = _policy(
        internal_price_chf_per_kwh=Decimal("10.000000")
    )
    draft["line_items"] = [
        {
            "id": 1,
            "participant_id": "building-a",
            "item_type": "consumer_charge",
            "quantity_kwh": Decimal("99999.9"),
            "unit_price_chf_per_kwh": Decimal("10.000000"),
            "amount_chf": Decimal("999999.000000"),
        },
        {
            "id": 2,
            "participant_id": "building-b",
            "item_type": "producer_credit",
            "quantity_kwh": Decimal(0),
            "unit_price_chf_per_kwh": Decimal("10.000000"),
            "amount_chf": Decimal(0),
        },
        {
            "id": 3,
            "participant_id": "building-b",
            "item_type": "rounding_adjustment",
            "quantity_kwh": None,
            "unit_price_chf_per_kwh": None,
            "amount_chf": Decimal("-999999.000000"),
        },
    ]
    draft["reconciliation"] = {
        "vnb_allocated_kwh": 99999.9,
        "engine_allocated_kwh": 99999.9,
        "difference_kwh": 0,
        "per_participant": {
            "building-a": {
                "vnb_kwh": 99999.9,
                "engine_kwh": 99999.9,
                "difference_kwh": 0,
            },
        },
        "vnb_production_kwh": 0,
        "engine_production_kwh": 0,
        "production_difference_kwh": 0,
        "production_per_participant": {
            "building-b": {
                "vnb_kwh": 0,
                "engine_kwh": 0,
                "difference_kwh": 0,
            },
        },
    }

    with pytest.raises(billing_approval.BillingApprovalError, match="Energiemengen"):
        billing_approval.prepare_invoice_snapshots(draft, issue_date=date(2026, 2, 5))


def test_prepare_accepts_energy_mismatch_within_aggregate_rounding_tolerance():
    """Independent 6dp rounding per line can leave a sub-milliwatt-hour residue."""
    draft = _draft()
    # Producer quantity one micro-kWh larger than the consumer total; the
    # monetary residue is still closed by the existing rounding adjustment.
    draft["line_items"][0].update(
        quantity_kwh=Decimal("13.366671"), amount_chf=Decimal("-2.005001")
    )
    draft["reconciliation"]["production_per_participant"]["building-b"].update(
        vnb_kwh=13.366671, engine_kwh=13.366671
    )
    draft["reconciliation"]["vnb_production_kwh"] = 13.366671
    draft["reconciliation"]["engine_production_kwh"] = 13.366671

    snapshots = billing_approval.prepare_invoice_snapshots(
        draft, issue_date=date(2026, 2, 5)
    )

    assert snapshots


def test_prepare_refuses_energy_mismatch_exceeding_aggregate_rounding_tolerance():
    draft = _draft()
    draft["line_items"].append(
        {
            "id": 5,
            "participant_id": "building-d",
            "item_type": "consumer_charge",
            "quantity_kwh": Decimal("0.001000"),
            "unit_price_chf_per_kwh": Decimal("0.150000"),
            "amount_chf": Decimal("0.000150"),
        }
    )

    with pytest.raises(billing_approval.BillingApprovalError, match="Energiemengen"):
        billing_approval.prepare_invoice_snapshots(draft, issue_date=date(2026, 2, 5))


def test_prepare_refuses_zero_producer_as_sole_source_against_positive_consumption():
    draft = _draft()
    draft["line_items"][0].update(quantity_kwh=Decimal(0), amount_chf=Decimal(0))
    draft["line_items"][2].update(
        participant_id="building-b", amount_chf=Decimal("-2.005000")
    )
    draft["reconciliation"]["production_per_participant"]["building-b"].update(
        vnb_kwh=0, engine_kwh=0
    )
    draft["reconciliation"]["vnb_production_kwh"] = 0
    draft["reconciliation"]["engine_production_kwh"] = 0

    with pytest.raises(billing_approval.BillingApprovalError, match="Energiemengen"):
        billing_approval.prepare_invoice_snapshots(draft, issue_date=date(2026, 2, 5))


def test_prepare_refuses_rounding_adjustment_exceeding_residue_bound():
    """A rounding adjustment must stay within the energy-derived residue bound."""
    draft = _draft()
    draft["line_items"][2]["amount_chf"] = Decimal("1.000000")

    with pytest.raises(
        billing_approval.BillingApprovalError, match="zulässigen Restbetrag"
    ):
        billing_approval.prepare_invoice_snapshots(draft, issue_date=date(2026, 2, 5))


def test_prepare_accepts_real_billing_engine_output_with_multiple_participants():
    """Canonical reconciliation built from actual engine quantities is approved."""
    import pandas as pd

    import billing_engine

    index = pd.date_range("2026-01-01", periods=3, freq="15min", tz="Europe/Zurich")
    production = pd.DataFrame(
        {"building-b": [0.3, 0.3, 0.3], "building-d": [0.2, 0.2, 0.2]},
        index=index,
    )
    consumption = pd.DataFrame(
        {"building-a": [0.5, 0.5, 0.5], "building-c": [0.5, 0.5, 0.5]},
        index=index,
    )
    summary = billing_engine.generate_billing_summary(
        production,
        consumption,
        grid_fee_per_kwh=0.08,
        internal_price_per_kwh=0.15,
        network_level="same",
        distribution_model="proportional",
    )

    per_participant = {}
    production_per_participant = {}
    for item in summary["line_items"]:
        participant_id = item["participant_id"]
        quantity = item["quantity_kwh"]
        entry = {"vnb_kwh": quantity, "engine_kwh": quantity, "difference_kwh": 0}
        if item["item_type"] == "consumer_charge":
            per_participant[participant_id] = entry
        elif item["item_type"] == "producer_credit":
            production_per_participant[participant_id] = entry

    allocated = sum(p["engine_kwh"] for p in per_participant.values())
    produced = sum(p["engine_kwh"] for p in production_per_participant.values())
    reconciliation = {
        "vnb_allocated_kwh": allocated,
        "engine_allocated_kwh": allocated,
        "difference_kwh": 0,
        "per_participant": per_participant,
        "vnb_production_kwh": produced,
        "engine_production_kwh": produced,
        "production_difference_kwh": 0,
        "production_per_participant": production_per_participant,
    }

    period = {
        "id": 1,
        "community_id": COMMUNITY,
        "status": "draft",
        "period_start": "2026-01-01T00:00:00+01:00",
        "period_end": "2026-01-01T00:45:00+01:00",
        "input_fingerprint": "a" * 64,
        "source_document_ids": ["E66-CONSUMPTION", "E66-PRODUCTION"],
        "reconciliation": reconciliation,
        "billing_policy_snapshot": _policy(),
        "line_items": summary["line_items"],
    }

    snapshots = billing_approval.prepare_invoice_snapshots(period)

    assert snapshots
    assert {s["participant_id"] for s in snapshots} == {
        "building-a",
        "building-b",
        "building-c",
        "building-d",
    }


def test_prepare_refuses_positive_consumer_energy_without_production():
    """Positive allocated consumption requires credited production to balance."""
    draft = _consumer_only_draft()
    draft["line_items"][0].update(
        quantity_kwh=Decimal("13.366667"), amount_chf=Decimal("2.005000")
    )

    with pytest.raises(billing_approval.BillingApprovalError, match="Energiemengen"):
        billing_approval.prepare_invoice_snapshots(draft, issue_date=date(2026, 2, 5))


def test_prepare_refuses_duplicate_zero_lines_inflating_tolerance():
    """Duplicate (participant_id, item_type) rows must not inflate the tolerance.

    With 1 consumer, 1 producer and 1 adjustment plus 1998 duplicate zero
    consumer lines, the aggregate rounding tolerance would authorise a
    0.001 kWh mismatch and a false 0.01 CHF transfer.
    """
    draft = _draft()
    draft["billing_policy_snapshot"] = _policy(
        internal_price_chf_per_kwh=Decimal("10.000000")
    )
    line_items = [
        {
            "id": 1,
            "participant_id": "building-a",
            "item_type": "consumer_charge",
            "quantity_kwh": Decimal("1.000000"),
            "unit_price_chf_per_kwh": Decimal("10.000000"),
            "amount_chf": Decimal("10.000000"),
        },
        {
            "id": 2,
            "participant_id": "building-b",
            "item_type": "producer_credit",
            "quantity_kwh": Decimal("0.999000"),
            "unit_price_chf_per_kwh": Decimal("10.000000"),
            "amount_chf": Decimal("-9.990000"),
        },
        {
            "id": 3,
            "participant_id": "building-b",
            "item_type": "rounding_adjustment",
            "quantity_kwh": None,
            "unit_price_chf_per_kwh": None,
            "amount_chf": Decimal("-0.010000"),
        },
    ]
    for extra_id in range(4, 2002):
        line_items.append(
            {
                "id": extra_id,
                "participant_id": "building-a",
                "item_type": "consumer_charge",
                "quantity_kwh": Decimal(0),
                "unit_price_chf_per_kwh": Decimal("10.000000"),
                "amount_chf": Decimal(0),
            }
        )
    draft["line_items"] = line_items
    draft["reconciliation"] = {
        "vnb_allocated_kwh": 1.0,
        "engine_allocated_kwh": 1.0,
        "difference_kwh": 0,
        "per_participant": {
            "building-a": {"vnb_kwh": 1.0, "engine_kwh": 1.0, "difference_kwh": 0},
        },
        "vnb_production_kwh": 0.999,
        "engine_production_kwh": 0.999,
        "production_difference_kwh": 0,
        "production_per_participant": {
            "building-b": {
                "vnb_kwh": 0.999,
                "engine_kwh": 0.999,
                "difference_kwh": 0,
            },
        },
    }

    with pytest.raises(
        billing_approval.BillingApprovalError, match="nur eine Position"
    ):
        billing_approval.prepare_invoice_snapshots(draft, issue_date=date(2026, 2, 5))
