# SPDX-License-Identifier: AGPL-3.0-or-later
"""Interface tests for the LEG community billing repository (store.billing).

Verifies the extracted module resolves the connection seam via
`database.get_connection` and that `database` re-exports the identical objects,
so legacy callers and existing monkeypatches keep working unchanged. Mirrors
`test_store_ranking.py` / `test_store_profile.py`; the seam is the test surface.
"""

import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import billing_policy
import database
from store import billing

_REEXPORTED = (
    "save_billing_period",
    "get_active_communities",
    "get_community_for_building",
    "list_billing_periods",
    "get_billing_period",
    "get_billing_period_for_window",
    "get_billing_policy",
    "get_invoices_for_participant",
    "get_invoice_for_participant",
)


class _FakeCursor:
    def __init__(self, rows=None, one=None):
        self.rows = rows or []
        self.one = one
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.one

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class _FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def _conn_ctx(cursor):
    @contextmanager
    def _factory():
        yield _FakeConnection(cursor)

    return _factory


def test_database_reexports_are_identical_objects():
    for name in _REEXPORTED:
        assert getattr(database, name) is getattr(billing, name), name


def test_store_billing_imports_without_database_bootstrap():
    result = subprocess.run(
        [sys.executable, "-c", "import store.billing; print('ok')"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_billing_uses_database_connection_seam(monkeypatch):
    # Monkeypatching database.get_connection must affect store.billing calls,
    # proving the seam is shared (not a stale direct import binding).
    cur = _FakeCursor(rows=[{"community_id": 1, "status": "active"}])
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    rows = billing.get_active_communities()
    assert rows == [{"community_id": 1, "status": "active"}]
    assert "communities" in cur.executed[0][0]
    assert "status = 'active'" in cur.executed[0][0]


def test_get_active_communities_propagates_storage_failure(monkeypatch):
    @contextmanager
    def unavailable_connection():
        raise RuntimeError("database unavailable")
        yield

    monkeypatch.setattr(database, "get_connection", unavailable_connection)

    with pytest.raises(billing.BillingStoreError):
        billing.get_active_communities()


def test_get_billing_period_missing_returns_none(monkeypatch):
    cur = _FakeCursor(one=None)
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    assert billing.get_billing_period(999) is None


def test_get_billing_period_propagates_storage_failure(monkeypatch):
    @contextmanager
    def unavailable_connection():
        raise RuntimeError("database unavailable")
        yield

    monkeypatch.setattr(database, "get_connection", unavailable_connection)

    with pytest.raises(billing.BillingStoreError):
        billing.get_billing_period(999)


def test_list_billing_periods_is_newest_first_and_bounded(monkeypatch):
    cur = _FakeCursor(rows=[{"id": 42, "community_id": "community-a"}])
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    rows = billing.list_billing_periods(limit=25)

    assert rows == [{"id": 42, "community_id": "community-a"}]
    query, params = cur.executed[0]
    assert "ORDER BY period_start DESC, id DESC" in " ".join(query.split())
    assert "LIMIT %s" in query
    assert params == (25,)


@pytest.mark.parametrize(("limit", "expected"), [(0, 1), (501, 500)])
def test_list_billing_periods_clamps_limit(monkeypatch, limit, expected):
    cur = _FakeCursor()
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    billing.list_billing_periods(limit=limit)

    assert cur.executed[0][1] == (expected,)


@pytest.mark.parametrize("invalid_limit", [None, "not-a-number"])
def test_list_billing_periods_rejects_invalid_limit(invalid_limit):
    with pytest.raises((TypeError, ValueError)):
        billing.list_billing_periods(limit=invalid_limit)


def test_list_billing_periods_wraps_invalid_database_rows(monkeypatch):
    cur = _FakeCursor(rows=[object()])
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    with pytest.raises(billing.BillingStoreError):
        billing.list_billing_periods()


def test_list_billing_periods_propagates_storage_failure(monkeypatch):
    @contextmanager
    def unavailable_connection():
        raise RuntimeError("database unavailable")
        yield

    monkeypatch.setattr(database, "get_connection", unavailable_connection)

    with pytest.raises(billing.BillingStoreError):
        billing.list_billing_periods()


def test_get_billing_period_can_fail_closed_to_one_community(monkeypatch):
    cur = _FakeCursor(
        rows=[{"participant_id": "building-a", "item_type": "consumer_charge"}],
        one={"id": 42, "community_id": "community-a"},
    )
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    period = billing.get_billing_period(42, "community-a")

    assert period["line_items"][0]["participant_id"] == "building-a"
    period_query, period_params = cur.executed[0]
    line_query, line_params = cur.executed[1]
    assert "id = %s" in period_query
    assert "community_id = %s" in period_query
    assert period_params == (42, "community-a")
    assert "billing_period_id = %s" in line_query
    assert line_params == (42,)


def test_get_effective_policy_and_existing_period_use_the_connection_seam(monkeypatch):
    policy = {
        "tariff_id": 7,
        "internal_price_chf_per_kwh": 0.12,
        "grid_fee_chf_per_kwh": 0.08,
        "network_level": "same",
        "distribution_model": "proportional",
    }
    policy_cur = _FakeCursor(one=policy)
    monkeypatch.setattr(database, "get_connection", _conn_ctx(policy_cur))

    assert (
        billing.get_billing_policy("community-a", "2026-01-01", "2026-02-01") == policy
    )
    period_cur = _FakeCursor(one={"id": 42, "input_fingerprint": "abc"})
    monkeypatch.setattr(database, "get_connection", _conn_ctx(period_cur))
    assert billing.get_billing_period_for_window(
        "community-a", "2026-01-01", "2026-02-01"
    ) == {"id": 42, "input_fingerprint": "abc"}
    assert "billing_tariffs" in policy_cur.executed[0][0]
    assert policy_cur.executed[0][1] == (
        "community-a",
        "2026-01-01",
        "2026-02-01",
        "2026-01-01",
        "2026-02-01",
    )
    assert "period_start" in period_cur.executed[0][0]


def test_save_billing_policy_receives_aware_zurich_midnight(monkeypatch):
    """The form date must become an aware datetime before insertion."""
    policy = billing_policy.validate_policy_form(
        {
            "effective_from": "2026-09-01",
            "internal_price_rp": "15.00",
            "grid_fee_rp": "8.00",
            "network_level": "same",
            "distribution_model": "proportional",
            "vat_mode": "none",
            "vat_rate_pct": "",
            "payment_days": "30",
            "invoice_prefix": "LEG-2026",
            "delivery_method": "email",
        }
    )["policy"]
    cur = _FakeCursor(one={"id": 9})
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    billing.save_billing_policy("community-a", policy)

    query, params = cur.executed[0]
    assert "INSERT INTO billing_tariffs" in query
    assert params[1] == datetime(2026, 9, 1, tzinfo=ZoneInfo("Europe/Zurich"))
    assert params[1].utcoffset().total_seconds() == 7200


def test_get_invoices_for_participant_scopes_to_building_and_issued(monkeypatch):
    cur = _FakeCursor(
        rows=[
            {"id": 1, "invoice_number": "LEG-2026-000001", "issue_date": "2026-08-05"}
        ]
    )
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    rows = billing.get_invoices_for_participant("building-a")

    assert rows == [
        {"id": 1, "invoice_number": "LEG-2026-000001", "issue_date": "2026-08-05"}
    ]
    query, params = cur.executed[0]
    normalised = " ".join(query.split())
    assert "participant_id = %s" in normalised
    assert "status = 'issued'" in normalised
    assert "ORDER BY issue_date DESC, id DESC" in normalised
    assert params == ("building-a",)


def test_get_invoices_for_participant_selects_integrity_fields_explicitly(monkeypatch):
    """The list returns only summary values to the browser, but reads all
    frozen integrity fields needed to reject a corrupted finite total."""
    cur = _FakeCursor(rows=[])
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    billing.get_invoices_for_participant("building-a")

    query = cur.executed[0][0]
    normalised = " ".join(query.split())
    assert "select *" not in normalised.lower()
    select_clause = normalised.split("FROM")[0]
    for column in (
        "id",
        "community_id",
        "participant_id",
        "invoice_number",
        "issue_date",
        "due_date",
        "policy_snapshot",
        "gross_chf",
        "provenance_snapshot",
        "line_items_snapshot",
        "net_chf",
        "vat_rate_pct",
        "vat_chf",
    ):
        assert column in select_clause, f"list query must select {column}"


def test_get_invoices_for_participant_propagates_storage_failure(monkeypatch):
    @contextmanager
    def unavailable_connection():
        raise RuntimeError("database unavailable")
        yield

    monkeypatch.setattr(database, "get_connection", unavailable_connection)

    with pytest.raises(billing.BillingStoreError):
        billing.get_invoices_for_participant("building-a")


def test_get_invoice_for_participant_uses_only_the_immutable_invoice(monkeypatch):
    """Issuer identity is frozen in provenance_snapshot at approval time, so
    reading an invoice must not join mutable community data."""
    cur = _FakeCursor(one={"id": 7, "participant_id": "building-a"})
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    invoice = billing.get_invoice_for_participant(7, "building-a")

    assert invoice == {
        "id": 7,
        "participant_id": "building-a",
    }
    query, params = cur.executed[0]
    normalised = " ".join(query.split())
    assert "join communities" not in normalised.lower()
    assert "id = %s" in normalised
    assert "participant_id = %s" in normalised
    assert "status = 'issued'" in normalised
    assert params == (7, "building-a")


def test_get_invoice_for_participant_missing_returns_none(monkeypatch):
    cur = _FakeCursor(one=None)
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    assert billing.get_invoice_for_participant(999, "building-a") is None


def test_get_invoice_for_participant_cross_member_is_indistinguishable_from_missing(
    monkeypatch,
):
    """A wrong owner and a nonexistent id both fail the same WHERE clause, so
    both return None through the identical code path: nothing downstream can
    tell "exists, not yours" from "does not exist"."""
    owner_cur = _FakeCursor(
        one=None
    )  # invoice 7 belongs to "building-b", not "building-a"
    monkeypatch.setattr(database, "get_connection", _conn_ctx(owner_cur))
    cross_member = billing.get_invoice_for_participant(7, "building-a")

    missing_cur = _FakeCursor(one=None)  # invoice 7 does not exist at all
    monkeypatch.setattr(database, "get_connection", _conn_ctx(missing_cur))
    missing = billing.get_invoice_for_participant(7, "building-a")

    assert cross_member is missing is None
    assert owner_cur.executed == missing_cur.executed


def test_get_invoice_for_participant_propagates_storage_failure(monkeypatch):
    @contextmanager
    def unavailable_connection():
        raise RuntimeError("database unavailable")
        yield

    monkeypatch.setattr(database, "get_connection", unavailable_connection)

    with pytest.raises(billing.BillingStoreError):
        billing.get_invoice_for_participant(7, "building-a")


def test_get_billing_policy_outer_select_projects_community_id(monkeypatch):
    """The resolved policy must include the tariff's community_id.

    The CTE already selects t.community_id, but the storage seam must project
    it in the outer SELECT so callers receive a complete, community-scoped
    policy. Direct consumers of the seam must not rely on billing_runner's
    defensive community_id enrichment.
    """
    import re

    policy = {
        "tariff_id": 7,
        "community_id": "community-a",
        "internal_price_chf_per_kwh": 0.12,
        "grid_fee_chf_per_kwh": 0.08,
        "network_level": "same",
        "distribution_model": "proportional",
    }
    cur = _FakeCursor(one=policy)
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    result = billing.get_billing_policy("community-a", "2026-01-01", "2026-02-01")

    assert result == policy
    query = cur.executed[0][0]
    # Capture only the outer SELECT projection, after the CTE closes.
    match = re.search(
        r"\)\s*SELECT\s+(.*?)\s+FROM\s+newest\s+t",
        query,
        re.IGNORECASE | re.DOTALL,
    )
    assert match is not None, "could not locate outer SELECT projection"
    outer_projection = match.group(1)
    assert "community_id" in outer_projection, (
        "outer SELECT must project community_id, not only the CTE"
    )
