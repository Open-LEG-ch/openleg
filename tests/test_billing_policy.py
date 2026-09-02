# SPDX-License-Identifier: AGPL-3.0-or-later
"""Behaviour tests for the versioned LEG billing policy (issue #398).

Covers the SQL-free validation module (``billing_policy``), the store.billing
public seams, and the authenticated LEG admin HTTP surface. Each cycle pins one
vertical slice: form validation, versioned persistence, policy resolution, and
the admin routes.
"""

import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

import billing_policy

VALID_FORM = {
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


def _form(**overrides):
    form = dict(VALID_FORM)
    form.update(overrides)
    return form


def test_valid_form_produces_complete_policy():
    result = billing_policy.validate_policy_form(VALID_FORM)

    assert result["errors"] == {}
    policy = result["policy"]
    assert policy["effective_from"] == datetime(
        2026, 9, 1, tzinfo=ZoneInfo("Europe/Zurich")
    )
    assert policy["internal_price_chf_per_kwh"] == Decimal("0.15")
    assert policy["grid_fee_chf_per_kwh"] == Decimal("0.08")
    assert policy["network_level"] == "same"
    assert policy["distribution_model"] == "proportional"
    assert policy["vat_mode"] == "none"
    assert policy["vat_rate_pct"] == Decimal(0)
    assert policy["payment_days"] == 30
    assert policy["invoice_prefix"] == "LEG-2026"
    assert policy["delivery_method"] == "email"


def test_persisted_policy_definition_owns_the_complete_field_set():
    assert billing_policy.PERSISTED_POLICY_FIELDS == (
        "tariff_id",
        "community_id",
        "effective_from",
        "internal_price_chf_per_kwh",
        "grid_fee_chf_per_kwh",
        "network_level",
        "distribution_model",
        "vat_mode",
        "vat_rate_pct",
        "payment_days",
        "invoice_prefix",
        "delivery_method",
    )


def test_fingerprint_projection_uses_every_persisted_policy_field():
    policy = {
        "tariff_id": 7,
        "community_id": "community-a",
        "effective_from": datetime(2026, 9, 1, tzinfo=ZoneInfo("Europe/Zurich")),
        "internal_price_chf_per_kwh": Decimal("0.15"),
        "grid_fee_chf_per_kwh": Decimal("0.08"),
        "network_level": "same",
        "distribution_model": "proportional",
        "vat_mode": "none",
        "vat_rate_pct": Decimal(0),
        "payment_days": 30,
        "invoice_prefix": "LEG-2026",
        "delivery_method": "email",
    }

    projected = billing_policy.policy_fingerprint_values(policy)

    assert tuple(projected) == billing_policy.FINGERPRINT_POLICY_FIELDS
    assert "effective_from" not in projected
    assert projected["internal_price_chf_per_kwh"] == "0.15"


def test_persisted_policy_refuses_temporal_types_that_cannot_be_compared():
    policy = _policy(
        tariff_id=7,
        community_id="community-a",
        effective_from=date(2026, 9, 1),
    )

    with pytest.raises(billing_policy.InvalidPersistedPolicy):
        billing_policy.validate_persisted_policy(
            policy,
            period_start=datetime(2026, 9, 1, tzinfo=ZoneInfo("Europe/Zurich")),
            community_id="community-a",
        )


@pytest.mark.parametrize(
    "field",
    (
        "tariff_id",
        "community_id",
        "effective_from",
        "internal_price_chf_per_kwh",
        "grid_fee_chf_per_kwh",
        "network_level",
        "distribution_model",
        "vat_mode",
        "vat_rate_pct",
        "payment_days",
        "invoice_prefix",
        "delivery_method",
    ),
)
def test_validate_persisted_policy_refuses_each_missing_canonical_field(field):
    policy = _policy(tariff_id=7, community_id="community-a")
    del policy[field]

    with pytest.raises(billing_policy.InvalidPersistedPolicy):
        billing_policy.validate_persisted_policy(
            policy,
            period_start=datetime(2026, 9, 1, tzinfo=ZoneInfo("Europe/Zurich")),
            community_id="community-a",
        )


def test_validate_persisted_policy_normalizes_one_complete_policy():
    policy = {
        "tariff_id": 7,
        "community_id": "community-a",
        "effective_from": datetime(2026, 9, 1, tzinfo=ZoneInfo("Europe/Zurich")),
        "internal_price_chf_per_kwh": 0.15,
        "grid_fee_chf_per_kwh": 0.08,
        "network_level": "same",
        "distribution_model": "proportional",
        "vat_mode": "none",
        "vat_rate_pct": 0.0,
        "payment_days": 30,
        "invoice_prefix": "LEG-2026",
        "delivery_method": "email",
    }

    normalized = billing_policy.validate_persisted_policy(
        policy,
        period_start=datetime(2026, 9, 1, tzinfo=ZoneInfo("Europe/Zurich")),
        community_id="community-a",
    )

    assert normalized == {
        "tariff_id": 7,
        "community_id": "community-a",
        "effective_from": datetime(2026, 9, 1, tzinfo=ZoneInfo("Europe/Zurich")),
        "internal_price_chf_per_kwh": Decimal("0.15"),
        "grid_fee_chf_per_kwh": Decimal("0.08"),
        "network_level": "same",
        "distribution_model": "proportional",
        "vat_mode": "none",
        "vat_rate_pct": Decimal("0.0"),
        "payment_days": 30,
        "invoice_prefix": "LEG-2026",
        "delivery_method": "email",
    }


# --- Issue #461: persisted policy identity validation ------------------------

_IDENTITY_VALID_POLICY = {
    "tariff_id": 7,
    "community_id": "community-a",
    "effective_from": datetime(2026, 9, 1, tzinfo=ZoneInfo("Europe/Zurich")),
    "internal_price_chf_per_kwh": Decimal("0.15"),
    "grid_fee_chf_per_kwh": Decimal("0.08"),
    "network_level": "same",
    "distribution_model": "proportional",
    "vat_mode": "none",
    "vat_rate_pct": Decimal(0),
    "payment_days": 30,
    "invoice_prefix": "LEG-2026",
    "delivery_method": "email",
}

_INVALID_IDENTITY_CASES = (
    ("community_id", "community-b"),
    ("community_id", ""),
    ("community_id", "community-a "),
    ("community_id", " community-a"),
    ("community_id", 7),
    ("community_id", ["community-a"]),
    ("tariff_id", True),
    ("tariff_id", False),
    ("tariff_id", 0),
    ("tariff_id", -1),
    ("tariff_id", "7"),
    ("tariff_id", "007"),
    ("tariff_id", 7.0),
)


@pytest.mark.parametrize(("field", "value"), _INVALID_IDENTITY_CASES)
def test_validate_persisted_policy_refuses_invalid_identity(field, value):
    policy = dict(_IDENTITY_VALID_POLICY)
    policy[field] = value

    with pytest.raises(billing_policy.InvalidPersistedPolicy):
        billing_policy.validate_persisted_policy(
            policy,
            period_start=datetime(2026, 9, 1, tzinfo=ZoneInfo("Europe/Zurich")),
            community_id="community-a",
        )


# --- Issue #461: truthy non-dict policies are refused --------------------------

_TRUTHY_NON_DICT_POLICIES = (
    "community-a",
    7,
    7.5,
    True,
    ["community-a"],
    ("community-a",),
    {"community-a"},
)


@pytest.mark.parametrize("policy", _TRUTHY_NON_DICT_POLICIES)
def test_validate_persisted_policy_refuses_truthy_non_dict_policy(policy):
    with pytest.raises(billing_policy.InvalidPersistedPolicy):
        billing_policy.validate_persisted_policy(
            policy,
            period_start=datetime(2026, 9, 1, tzinfo=ZoneInfo("Europe/Zurich")),
            community_id="community-a",
        )


# --- Issue #461: persisted energy price validation ---------------------------

_ENERGY_PRICE_FIELDS = (
    "internal_price_chf_per_kwh",
    "grid_fee_chf_per_kwh",
)

_INVALID_ENERGY_PRICE_CASES = (
    "abc",
    "",
    Decimal("NaN"),
    Decimal("Infinity"),
    Decimal("-Infinity"),
    float("nan"),
    float("inf"),
    float("-inf"),
    Decimal("-0.01"),
    "-0.01",
    Decimal("10.01"),
    "10.01",
    Decimal("0.1234567"),
    "0.1234567",
)


@pytest.mark.parametrize("field", _ENERGY_PRICE_FIELDS)
@pytest.mark.parametrize("value", _INVALID_ENERGY_PRICE_CASES)
def test_validate_persisted_policy_refuses_invalid_energy_price(field, value):
    policy = dict(_IDENTITY_VALID_POLICY)
    policy[field] = value

    with pytest.raises(billing_policy.InvalidPersistedPolicy):
        billing_policy.validate_persisted_policy(
            policy,
            period_start=datetime(2026, 9, 1, tzinfo=ZoneInfo("Europe/Zurich")),
            community_id="community-a",
        )


_INVALID_EFFECTIVE_FROM_CASES = (
    "",
    "2026-13-01",
    "2026-09-32",
    "01.09.2026",
    "20260901",
    "not-a-date",
    20260901,
    datetime(2026, 9, 1),  # noqa: DTZ001 - deliberately incomparable with aware time
    datetime(2026, 10, 1, tzinfo=ZoneInfo("Europe/Zurich")),
)


@pytest.mark.parametrize("value", _INVALID_EFFECTIVE_FROM_CASES)
def test_validate_persisted_policy_refuses_invalid_effective_from(value):
    policy = dict(_IDENTITY_VALID_POLICY)
    policy["effective_from"] = value

    with pytest.raises(billing_policy.InvalidPersistedPolicy):
        billing_policy.validate_persisted_policy(
            policy,
            period_start=datetime(2026, 9, 1, tzinfo=ZoneInfo("Europe/Zurich")),
            community_id="community-a",
        )


# --- Invalid persisted payment_days cases -------------------------------------

_INVALID_PERSISTED_PAYMENT_DAYS_CASES = (
    True,
    False,
    0,
    -1,
    -30,
    366,
    1000,
    30.0,
    30.5,
    "30",
    "30.5",
    "abc",
    "",
    None,
)


@pytest.mark.parametrize("value", _INVALID_PERSISTED_PAYMENT_DAYS_CASES)
def test_invalid_persisted_payment_days_is_refused(value):
    policy = dict(_IDENTITY_VALID_POLICY)
    policy["payment_days"] = value

    with pytest.raises(billing_policy.InvalidPersistedPolicy):
        billing_policy.validate_persisted_policy(
            policy,
            period_start=datetime(2026, 9, 1, tzinfo=ZoneInfo("Europe/Zurich")),
            community_id="community-a",
        )


# --- Invalid persisted enum choices ------------------------------------------

_INVALID_PERSISTED_ENUM_CASES = (
    ("network_level", "different"),
    ("network_level", ""),
    ("network_level", 7),
    ("network_level", True),
    ("network_level", ["same"]),
    ("distribution_model", "simple"),
    ("distribution_model", ""),
    ("distribution_model", 3),
    ("distribution_model", False),
    ("distribution_model", ["proportional"]),
    ("delivery_method", "post"),
    ("delivery_method", ""),
    ("delivery_method", 9),
    ("delivery_method", True),
    ("delivery_method", ["email"]),
)


@pytest.mark.parametrize(("field", "value"), _INVALID_PERSISTED_ENUM_CASES)
def test_invalid_persisted_enum_choices_are_refused(field, value):
    policy = dict(_IDENTITY_VALID_POLICY)
    policy[field] = value

    with pytest.raises(billing_policy.InvalidPersistedPolicy):
        billing_policy.validate_persisted_policy(
            policy,
            period_start=datetime(2026, 9, 1, tzinfo=ZoneInfo("Europe/Zurich")),
            community_id="community-a",
        )


# --- Invalid persisted vat_mode/vat_rate_pct combinations ---------------------

_INVALID_PERSISTED_VAT_CASES = (
    ("partial", Decimal(0)),
    ("reduced", Decimal(0)),
    ("", Decimal(0)),
    (" ", Decimal(0)),
    (7, Decimal(0)),
    (True, Decimal(0)),
    (["none"], Decimal(0)),
    (None, Decimal(0)),
    ("none", Decimal("0.01")),
    ("none", Decimal("8.1")),
    ("none", Decimal("-0.01")),
    ("none", 8.1),
    ("none", Decimal("NaN")),
    ("none", Decimal("Infinity")),
    ("none", "abc"),
    ("none", None),
    ("standard", Decimal(0)),
    ("standard", Decimal(-1)),
    ("standard", Decimal("100.01")),
    ("standard", "abc"),
    ("standard", Decimal("8.123")),
)


@pytest.mark.parametrize(("vat_mode", "vat_rate_pct"), _INVALID_PERSISTED_VAT_CASES)
def test_invalid_persisted_vat_combination_is_refused(vat_mode, vat_rate_pct):
    policy = dict(_IDENTITY_VALID_POLICY)
    policy["vat_mode"] = vat_mode
    policy["vat_rate_pct"] = vat_rate_pct

    with pytest.raises(billing_policy.InvalidPersistedPolicy):
        billing_policy.validate_persisted_policy(
            policy,
            period_start=datetime(2026, 9, 1, tzinfo=ZoneInfo("Europe/Zurich")),
            community_id="community-a",
        )


# --- Invalid persisted invoice_prefix cases -----------------------------------

_INVALID_PERSISTED_INVOICE_PREFIX_CASES = (
    "",
    " ",
    "\t",
    " LEG-2026",
    "LEG-2026 ",
    "LEG 2026",
    7,
    None,
    True,
    ["LEG-2026"],
    ("LEG-2026",),
    9.5,
    "A" * 17,
    "B" * 32,
    "leg-2026",
    "Leg-2026",
    "LEG_2026",
    "LEG/2026",
    "LEG.1",
    "LEG-2026!",
    "<script>",
    "égü",
    "LEG-2026\n",
)


@pytest.mark.parametrize("value", _INVALID_PERSISTED_INVOICE_PREFIX_CASES)
def test_invalid_persisted_invoice_prefix_is_refused(value):
    policy = dict(_IDENTITY_VALID_POLICY)
    policy["invoice_prefix"] = value

    with pytest.raises(billing_policy.InvalidPersistedPolicy):
        billing_policy.validate_persisted_policy(
            policy,
            period_start=datetime(2026, 9, 1, tzinfo=ZoneInfo("Europe/Zurich")),
            community_id="community-a",
        )


def test_disclaimer_promises_no_legal_advice():
    disclaimer = billing_policy.POLICY_DISCLAIMER
    assert "Verantwortung der LEG" in disclaimer
    assert "keine Rechts- oder Steuerberatung" in disclaimer
    assert "ß" not in disclaimer


@pytest.mark.parametrize(
    "field",
    [
        "effective_from",
        "internal_price_rp",
        "grid_fee_rp",
        "network_level",
        "distribution_model",
        "vat_mode",
        "payment_days",
        "invoice_prefix",
        "delivery_method",
    ],
)
def test_missing_fields_fail_closed(field):
    form = _form(**{field: ""})
    result = billing_policy.validate_policy_form(form)
    assert result["policy"] is None
    assert field in result["errors"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("network_level", "different"),
        ("distribution_model", "simple"),
        ("vat_mode", "reduced"),
        ("delivery_method", "post"),
    ],
)
def test_unknown_enum_options_are_refused(field, value):
    result = billing_policy.validate_policy_form(_form(**{field: value}))
    assert result["policy"] is None
    assert field in result["errors"]


@pytest.mark.parametrize("field", ["internal_price_rp", "grid_fee_rp"])
@pytest.mark.parametrize("value", ["-0.01", "abc", "nan", "inf", "1e3", "12.12345"])
def test_unsafe_prices_are_refused(field, value):
    result = billing_policy.validate_policy_form(_form(**{field: value}))
    assert result["policy"] is None
    assert field in result["errors"]


def test_zero_prices_are_allowed():
    result = billing_policy.validate_policy_form(
        _form(internal_price_rp="0", grid_fee_rp="0")
    )
    assert result["errors"] == {}
    assert result["policy"]["internal_price_chf_per_kwh"] == Decimal(0)


@pytest.mark.parametrize(
    "value", ["2026-13-01", "2026-09-32", "01.09.2026", "20260901"]
)
def test_invalid_effective_dates_are_refused(value):
    result = billing_policy.validate_policy_form(_form(effective_from=value))
    assert result["policy"] is None
    assert "effective_from" in result["errors"]


def test_effective_from_is_zurich_midnight_with_winter_and_summer_offsets():
    """A form date is midnight Europe/Zurich, not a naive date or session time."""
    winter = billing_policy.validate_policy_form(_form(effective_from="2026-01-15"))
    summer = billing_policy.validate_policy_form(_form(effective_from="2026-07-15"))

    assert winter["policy"]["effective_from"] == datetime(
        2026, 1, 15, tzinfo=ZoneInfo("Europe/Zurich")
    )
    assert summer["policy"]["effective_from"] == datetime(
        2026, 7, 15, tzinfo=ZoneInfo("Europe/Zurich")
    )
    assert winter["policy"]["effective_from"].utcoffset() == timedelta(hours=1)
    assert summer["policy"]["effective_from"].utcoffset() == timedelta(hours=2)


def test_standard_vat_requires_a_positive_rate():
    result = billing_policy.validate_policy_form(_form(vat_mode="standard"))
    assert result["policy"] is None
    assert "vat_rate_pct" in result["errors"]

    result = billing_policy.validate_policy_form(
        _form(vat_mode="standard", vat_rate_pct="-1")
    )
    assert result["policy"] is None
    assert "vat_rate_pct" in result["errors"]


def test_standard_vat_accepts_the_swiss_standard_rate():
    result = billing_policy.validate_policy_form(
        _form(vat_mode="standard", vat_rate_pct="8.1")
    )
    assert result["errors"] == {}
    assert result["policy"]["vat_rate_pct"] == Decimal("8.1")


@pytest.mark.parametrize("value", ["101", "8.123", "abc"])
def test_invalid_vat_rates_are_refused(value):
    result = billing_policy.validate_policy_form(
        _form(vat_mode="standard", vat_rate_pct=value)
    )
    assert result["policy"] is None
    assert "vat_rate_pct" in result["errors"]


def test_vat_rate_without_vat_mode_is_refused():
    result = billing_policy.validate_policy_form(_form(vat_rate_pct="8.1"))
    assert result["policy"] is None
    assert "vat_rate_pct" in result["errors"]


@pytest.mark.parametrize("value", ["0", "-5", "366", "30.5", "abc"])
def test_invalid_payment_terms_are_refused(value):
    result = billing_policy.validate_policy_form(_form(payment_days=value))
    assert result["policy"] is None
    assert "payment_days" in result["errors"]


@pytest.mark.parametrize(
    "value",
    ["a", "rechnung 1", "LEG_2026", "<script>", "LEG/2026", "A" * 17, "LEG.1", "égü"],
)
def test_unsafe_invoice_prefixes_are_refused(value):
    result = billing_policy.validate_policy_form(_form(invoice_prefix=value))
    assert result["policy"] is None
    assert "invoice_prefix" in result["errors"]


@pytest.mark.parametrize("value", ["LEG", "MUSTERWEG-1", "A" * 16])
def test_safe_invoice_prefixes_are_accepted(value):
    result = billing_policy.validate_policy_form(_form(invoice_prefix=value))
    assert result["errors"] == {}
    assert result["policy"]["invoice_prefix"] == value


# --- Cycle 2: store.billing versioned policy seams ---------------------------

from contextlib import contextmanager
from pathlib import Path

import database
from store import billing

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class _Cursor:
    def __init__(self, rows=None, one=None, error=None):
        self.rows = rows or []
        self.one = one
        self.error = error
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))
        if self.error is not None:
            raise self.error

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.one

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def _conn(cursor):
    @contextmanager
    def factory():
        yield _Connection(cursor)

    return factory


def _policy(**overrides):
    policy = billing_policy.validate_policy_form(VALID_FORM)["policy"]
    policy.update(overrides)
    return policy


def test_save_billing_policy_inserts_one_new_version(monkeypatch):
    cursor = _Cursor(one={"id": 9})
    monkeypatch.setattr(database, "get_connection", _conn(cursor))

    policy_id = billing.save_billing_policy("community-a", _policy())

    assert policy_id == 9
    query, params = cursor.executed[0]
    assert "INSERT INTO billing_tariffs" in query
    assert "UPDATE" not in query
    assert params[0] == "community-a"
    assert params[1] == datetime(2026, 9, 1, tzinfo=ZoneInfo("Europe/Zurich"))
    assert params[2] == Decimal("0.15")
    assert params[3] == Decimal("0.08")
    assert params[4] == "same"
    assert params[5] == "proportional"
    assert params[6] == "none"
    assert params[7] == Decimal(0)
    assert params[8] == 30
    assert params[9] == "LEG-2026"
    assert params[10] == "email"


def test_save_billing_policy_refuses_duplicate_effective_date(monkeypatch):
    class _UniqueViolation(Exception):
        pgcode = "23505"

    cursor = _Cursor(error=_UniqueViolation("duplicate key"))
    monkeypatch.setattr(database, "get_connection", _conn(cursor))

    with pytest.raises(billing.BillingPolicyConflict):
        billing.save_billing_policy("community-a", _policy())


def test_save_billing_policy_wraps_storage_failure(monkeypatch):
    cursor = _Cursor(error=RuntimeError("database unavailable"))
    monkeypatch.setattr(database, "get_connection", _conn(cursor))

    with pytest.raises(billing.BillingStoreError) as exc:
        billing.save_billing_policy("community-a", _policy())
    assert not isinstance(exc.value, billing.BillingPolicyConflict)


def test_list_billing_policies_is_newest_first(monkeypatch):
    rows = [
        {"id": 2, "effective_from": date(2026, 9, 1)},
        {"id": 1, "effective_from": date(2026, 1, 1)},
    ]
    cursor = _Cursor(rows=rows)
    monkeypatch.setattr(database, "get_connection", _conn(cursor))

    assert billing.list_billing_policies("community-a") == rows
    query, params = cursor.executed[0]
    assert "billing_tariffs" in query
    assert "ORDER BY effective_from DESC, id DESC" in " ".join(query.split())
    assert params == ("community-a",)


def test_list_billing_policies_wraps_storage_failure(monkeypatch):
    cursor = _Cursor(error=RuntimeError("database unavailable"))
    monkeypatch.setattr(database, "get_connection", _conn(cursor))

    with pytest.raises(billing.BillingStoreError):
        billing.list_billing_policies("community-a")


def test_database_reexports_the_policy_seams():
    for name in ("save_billing_policy", "list_billing_policies"):
        assert getattr(database, name) is getattr(billing, name), name


def test_schema_versions_billing_policy_columns_additively():
    schema = (PROJECT_ROOT / "store" / "schema.py").read_text(encoding="utf-8")
    create_block = re.search(
        r"CREATE TABLE IF NOT EXISTS billing_tariffs \((.*?)\)\s*\"\"\"",
        schema,
        flags=re.DOTALL,
    ).group(1)
    alter_block = re.search(
        r"ALTER TABLE billing_tariffs\s+(.*?)\s*\"\"\"",
        schema,
        flags=re.DOTALL,
    ).group(1)

    for column in (
        "distribution_model",
        "vat_mode",
        "vat_rate_pct",
        "payment_days",
        "invoice_prefix",
        "delivery_method",
    ):
        assert column in create_block
        assert f"ADD COLUMN IF NOT EXISTS {column}" in alter_block
    assert "UNIQUE(community_id, effective_from)" in create_block


def test_schema_adds_nullable_check_constraints_for_policy_choices():
    """Each choice field has a nullable CHECK on fresh and migrated schema."""
    schema = (PROJECT_ROOT / "store" / "schema.py").read_text(encoding="utf-8")
    create_block = re.search(
        r"CREATE TABLE IF NOT EXISTS billing_tariffs \((.*?)\)\s*\"\"\"",
        schema,
        flags=re.DOTALL,
    ).group(1)
    alter_block = re.search(
        r"ALTER TABLE billing_tariffs\s+(.*?)\s*\"\"\"",
        schema,
        flags=re.DOTALL,
    ).group(1)

    checks = {
        "chk_billing_tariffs_distribution_model": (
            "'proportional'",
            "'einfach'",
        ),
        "chk_billing_tariffs_vat_mode": ("'none'", "'standard'"),
        "chk_billing_tariffs_vat_rate": ("vat_mode", "vat_rate_pct"),
        "chk_billing_tariffs_payment_days": ("BETWEEN 1 AND 365",),
        "chk_billing_tariffs_invoice_prefix": ("~",),
        "chk_billing_tariffs_delivery_method": ("'email'", "'download'"),
    }
    for constraint, fragments in checks.items():
        assert f"CONSTRAINT {constraint}" in create_block, constraint
        # PG16 does not support ADD CONSTRAINT IF NOT EXISTS; the migration must
        # use an idempotent DO block checking pg_constraint.
        assert f"ADD CONSTRAINT IF NOT EXISTS {constraint}" not in alter_block, (
            f"{constraint}: ADD CONSTRAINT IF NOT EXISTS is not PG16-safe"
        )
        assert f"{constraint}" in alter_block, constraint
        assert "pg_constraint" in alter_block, "constraints must be added idempotently"
        for fragment in fragments:
            assert fragment in create_block, fragment
    # Legacy NULL rows must stay valid: no NOT NULL and no DEFAULT.
    for column in (
        "distribution_model",
        "vat_mode",
        "vat_rate_pct",
        "payment_days",
        "invoice_prefix",
        "delivery_method",
    ):
        for block in (create_block, alter_block):
            for line in block.splitlines():
                if column in line:
                    assert "DEFAULT" not in line.upper()
                    assert "NOT NULL" not in line.upper()


def test_schema_vat_check_uses_total_boolean_case():
    """The vat CHECK must not pass through UNKNOWN for partial NULL rows."""
    schema = (PROJECT_ROOT / "store" / "schema.py").read_text(encoding="utf-8")
    create_block = re.search(
        r"CREATE TABLE IF NOT EXISTS billing_tariffs \((.*?)\)\s*\"\"\"",
        schema,
        flags=re.DOTALL,
    ).group(1)
    alter_block = re.search(
        r"ALTER TABLE billing_tariffs\s+(.*?)\s*\"\"\"",
        schema,
        flags=re.DOTALL,
    ).group(1)

    for block in (create_block, alter_block):
        vat_check_match = re.search(
            r"CONSTRAINT chk_billing_tariffs_vat_rate.*?CHECK\s*\((.*?)\)\s*[,;]",
            block,
            flags=re.DOTALL,
        )
        assert vat_check_match, "vat_rate CHECK constraint not found"
        vat_check = vat_check_match.group(1)
        assert "CASE" in vat_check, "vat CHECK must use a total boolean CASE"
        assert "WHEN" in vat_check
        # The three allowed states must be explicit.
        assert "vat_mode IS NULL AND vat_rate_pct IS NULL" in vat_check
        assert "vat_mode = 'none' AND vat_rate_pct = 0" in vat_check
        assert "vat_mode = 'standard'" in vat_check
        assert "vat_rate_pct > 0" in vat_check
        assert "vat_rate_pct <= 100" in vat_check


# --- Cycle 3: billing_runner resolves the complete effective policy ---------


def test_get_billing_policy_selects_the_complete_versioned_policy(monkeypatch):
    cursor = _Cursor(one={"tariff_id": 7})
    monkeypatch.setattr(database, "get_connection", _conn(cursor))

    billing.get_billing_policy("community-a", "2026-01-01", "2026-02-01")

    query = cursor.executed[0][0]
    for column in (
        "distribution_model",
        "vat_mode",
        "vat_rate_pct",
        "payment_days",
        "invoice_prefix",
        "delivery_method",
    ):
        assert f"t.{column}" in query
    assert "COALESCE" not in query, "versioned distribution_model is authoritative"
    assert cursor.executed[0][1] == (
        "community-a",
        "2026-01-01",
        "2026-02-01",
        "2026-01-01",
        "2026-02-01",
    )


def test_billing_runner_fingerprints_each_complete_policy_field(monkeypatch):
    """Each versioned policy field, changed alone, changes the fingerprint."""
    from tests.test_billing_runner import _fingerprint_case, _fingerprint_through_runner

    changes = {
        "vat_mode": "standard",
        "vat_rate_pct": Decimal("8.1"),
        "payment_days": 14,
        "invoice_prefix": "LEG-X",
        "delivery_method": "download",
    }
    for field, value in changes.items():
        baseline = _fingerprint_through_runner(monkeypatch, _fingerprint_case())
        changed = _fingerprint_case()
        changed["policy"][field] = value
        changed_fingerprint = _fingerprint_through_runner(monkeypatch, changed)
        assert changed_fingerprint != baseline, (
            f"{field} is missing from the billing run fingerprint"
        )


# --- Cycle 5: authenticated LEG admin HTTP surface ---------------------------

from unittest.mock import MagicMock

from tests.test_dashboard_access_routes import (
    _set_session,
    app_module,  # noqa: F401
)

COMMUNITY = "c0ffee"
POLICY_URL = f"/leg/community/{COMMUNITY}/billing-policy"

STATUS = {
    "community_id": COMMUNITY,
    "name": "LEG Musterweg",
    "status": "active",
    "distribution_model": "simple",
    "members": [
        {"building_id": "b-admin", "role": "admin", "status": "confirmed"},
        {"building_id": "b-member", "role": "member", "status": "confirmed"},
    ],
}


def _patch_admin(monkeypatch, app_module):  # noqa: F811
    monkeypatch.setattr(
        app_module.dashboard_module.formation_wizard,
        "get_community_status",
        MagicMock(return_value=dict(STATUS)),
    )
    monkeypatch.setattr(
        app_module.db, "list_billing_policies", MagicMock(return_value=[])
    )
    save = MagicMock(return_value=9)
    monkeypatch.setattr(app_module.db, "save_billing_policy", save)
    return save


def _post_form(**overrides):
    form = dict(VALID_FORM)
    form.update(overrides)
    form.setdefault("csrf_token", "csrf-secret")
    return form


def test_policy_page_requires_session(app_module):  # noqa: F811
    client = app_module.web.test_client()
    assert client.get(POLICY_URL).status_code == 401
    assert client.post(POLICY_URL, data=_post_form()).status_code == 401


def test_policy_page_refuses_members_and_strangers(app_module, monkeypatch):  # noqa: F811
    _patch_admin(monkeypatch, app_module)
    for building_id in ("b-member", "b-stranger"):
        client = app_module.web.test_client()
        _set_session(client, building_id=building_id)
        assert client.get(POLICY_URL).status_code == 403
        assert client.post(POLICY_URL, data=_post_form()).status_code == 403


def test_policy_page_refuses_unknown_community(app_module, monkeypatch):  # noqa: F811
    _patch_admin(monkeypatch, app_module)
    app_module.dashboard_module.formation_wizard.get_community_status = MagicMock(
        side_effect=lambda cid: dict(STATUS) if cid == COMMUNITY else None
    )
    client = app_module.web.test_client()
    _set_session(client, building_id="b-admin")
    assert client.get("/leg/community/unknown/billing-policy").status_code == 403


def test_policy_post_requires_csrf(app_module, monkeypatch):  # noqa: F811
    save = _patch_admin(monkeypatch, app_module)
    client = app_module.web.test_client()
    _set_session(client, building_id="b-admin")

    response = client.post(POLICY_URL, data=_post_form(csrf_token="wrong"))

    assert response.status_code == 400
    save.assert_not_called()


def test_policy_page_renders_form_disclaimer_and_versions(app_module, monkeypatch):  # noqa: F811
    _patch_admin(monkeypatch, app_module)
    app_module.db.list_billing_policies.return_value = [
        {
            "id": 3,
            "effective_from": date(2026, 9, 1),
            "internal_price_chf_per_kwh": Decimal("0.15"),
            "grid_fee_chf_per_kwh": Decimal("0.08"),
            "network_level": "same",
            "distribution_model": "proportional",
            "vat_mode": "none",
            "vat_rate_pct": Decimal(0),
            "payment_days": 30,
            "invoice_prefix": "LEG-2026",
            "delivery_method": "email",
        }
    ]
    client = app_module.web.test_client()
    _set_session(client, building_id="b-admin")

    response = client.get(POLICY_URL)

    assert response.status_code == 200
    assert "no-store" in response.headers["Cache-Control"]
    html = response.get_data(as_text=True)
    assert billing_policy.POLICY_DISCLAIMER in html
    for field in (
        "effective_from",
        "internal_price_rp",
        "grid_fee_rp",
        "network_level",
        "distribution_model",
        "vat_mode",
        "vat_rate_pct",
        "payment_days",
        "invoice_prefix",
        "delivery_method",
        "csrf_token",
    ):
        assert f'name="{field}"' in html
    assert "2026-09-01" in html
    assert "LEG-2026" in html
    assert "Keine Mehrwertsteuer" in html


def test_policy_save_persists_valid_version(app_module, monkeypatch):  # noqa: F811
    save = _patch_admin(monkeypatch, app_module)
    client = app_module.web.test_client()
    _set_session(client, building_id="b-admin")

    response = client.post(POLICY_URL, data=_post_form())

    assert response.status_code == 302
    assert response.headers["Location"].endswith("?saved=1")
    (community_id, policy), _ = save.call_args
    assert community_id == COMMUNITY
    assert policy["effective_from"] == datetime(
        2026, 9, 1, tzinfo=ZoneInfo("Europe/Zurich")
    )
    assert policy["invoice_prefix"] == "LEG-2026"


def test_policy_save_refuses_invalid_input_without_saving(app_module, monkeypatch):  # noqa: F811
    save = _patch_admin(monkeypatch, app_module)
    client = app_module.web.test_client()
    _set_session(client, building_id="b-admin")

    response = client.post(
        POLICY_URL,
        data=_post_form(internal_price_rp="-1", network_level="different"),
    )

    assert response.status_code == 400
    html = response.get_data(as_text=True)
    assert "interner preis" in html.lower() or "Interner Preis" in html
    assert "Netzebene" in html
    save.assert_not_called()


def test_policy_save_reports_duplicate_effective_date(app_module, monkeypatch):  # noqa: F811
    save = _patch_admin(monkeypatch, app_module)
    save.side_effect = app_module.db.BillingPolicyConflict("duplicate")
    client = app_module.web.test_client()
    _set_session(client, building_id="b-admin")

    response = client.post(POLICY_URL, data=_post_form())

    assert response.status_code == 400
    assert "existiert bereits" in response.get_data(as_text=True)


def test_policy_routes_fail_closed_on_storage_failure(app_module, monkeypatch):  # noqa: F811
    _patch_admin(monkeypatch, app_module)
    app_module.db.list_billing_policies.side_effect = app_module.db.BillingStoreError(
        "down"
    )
    client = app_module.web.test_client()
    _set_session(client, building_id="b-admin")

    assert client.get(POLICY_URL).status_code == 503

    app_module.db.list_billing_policies.side_effect = None
    app_module.db.list_billing_policies.return_value = []
    app_module.db.save_billing_policy.side_effect = app_module.db.BillingStoreError(
        "down"
    )
    assert client.post(POLICY_URL, data=_post_form()).status_code == 503


# --- Review blockers: policy boundary, nullable migration, confirmed admin ---


def test_get_billing_policy_fails_closed_across_a_policy_boundary(monkeypatch):
    """A newer version starting inside the period must refuse the whole period."""
    cursor = _Cursor(one={"tariff_id": 7})
    monkeypatch.setattr(database, "get_connection", _conn(cursor))

    billing.get_billing_policy("community-a", "2026-01-01", "2026-02-01")

    query, params = cursor.executed[0]
    normalized = " ".join(query.split()).upper()
    assert "NOT EXISTS" in normalized, "a boundary check must refuse split periods"
    assert params == (
        "community-a",
        "2026-01-01",
        "2026-02-01",
        "2026-01-01",
        "2026-02-01",
    )


def test_get_billing_policy_requires_complete_versioned_fields(monkeypatch):
    cursor = _Cursor(one={"tariff_id": 7})
    monkeypatch.setattr(database, "get_connection", _conn(cursor))

    billing.get_billing_policy("community-a", "2026-01-01", "2026-02-01")

    query = cursor.executed[0][0]
    for column in (
        "distribution_model",
        "vat_mode",
        "vat_rate_pct",
        "payment_days",
        "invoice_prefix",
        "delivery_method",
    ):
        assert f"t.{column} IS NOT NULL" in " ".join(query.split())


def test_get_billing_policy_selects_newest_then_requires_complete(monkeypatch):
    """The query must pick the newest effective version, then require completeness.

    Falling back to an older complete version would let an incomplete newest
    version silently disappear instead of failing the billing run.
    """
    cursor = _Cursor(one={"tariff_id": 7})
    monkeypatch.setattr(database, "get_connection", _conn(cursor))

    billing.get_billing_policy("community-a", "2026-01-01", "2026-02-01")

    query = cursor.executed[0][0]
    normalized = " ".join(query.split()).upper()
    assert "WITH NEWEST" in normalized, "newest version must be selected first"
    assert "LIMIT 1" in normalized
    # The CTE must select on effective_from only; coverage and boundary checks
    # belong in the outer query so an expired/incomplete newest row cannot fall
    # back to an older complete row.
    cte_match = re.search(r"WITH NEWEST AS \((.*?)\)\s*SELECT", normalized, re.DOTALL)
    assert cte_match, "could not locate CTE body"
    cte_body = cte_match.group(1)
    assert "T.COMMUNITY_ID" in cte_body
    assert "EFFECTIVE_FROM <= %S" in cte_body
    assert "EFFECTIVE_TO IS NULL" not in cte_body, (
        "effective_to coverage check must be outside the CTE"
    )
    assert "EFFECTIVE_TO >= %S" not in cte_body, (
        "effective_to coverage check must be outside the CTE"
    )
    assert "NOT EXISTS" not in cte_body, "boundary check must be outside the CTE"

    outer_match = re.search(r"FROM NEWEST T(.*)", normalized, re.DOTALL)
    assert outer_match, "could not locate outer WHERE clause"
    outer_where = outer_match.group(1)
    assert "EFFECTIVE_TO IS NULL OR T.EFFECTIVE_TO >= %S" in outer_where
    assert "NOT EXISTS" in outer_where
    completeness = (
        "T.DISTRIBUTION_MODEL IS NOT NULL",
        "T.VAT_MODE IS NOT NULL",
        "T.VAT_RATE_PCT IS NOT NULL",
        "T.PAYMENT_DAYS IS NOT NULL",
        "T.INVOICE_PREFIX IS NOT NULL",
        "T.DELIVERY_METHOD IS NOT NULL",
    )
    for predicate in completeness:
        assert predicate in outer_where, f"missing completeness predicate: {predicate}"


def test_schema_migration_does_not_invent_policy_values_for_legacy_rows():
    schema = (PROJECT_ROOT / "store" / "schema.py").read_text(encoding="utf-8")
    create_block = re.search(
        r"CREATE TABLE IF NOT EXISTS billing_tariffs \((.*?)\)\s*\"\"\"",
        schema,
        flags=re.DOTALL,
    ).group(1)
    alter_block = re.search(
        r"ALTER TABLE billing_tariffs\s+(.*?)\s*\"\"\"",
        schema,
        flags=re.DOTALL,
    ).group(1)

    for column in (
        "vat_mode",
        "vat_rate_pct",
        "payment_days",
        "invoice_prefix",
        "delivery_method",
    ):
        for block in (create_block, alter_block):
            lines = [line for line in block.splitlines() if column in line]
            assert lines, column
            for line in lines:
                assert "DEFAULT" not in line.upper(), (
                    f"{column} must stay NULL on legacy rows: {line.strip()}"
                )
                assert "NOT NULL" not in line.upper(), (
                    f"{column} must stay nullable for legacy rows: {line.strip()}"
                )


def test_runner_refuses_an_incomplete_policy_with_missing_vat_fields(monkeypatch):
    from billing_runner import BillingRunError, run_billing_period
    from tests.test_billing_runner import END, START, _install_billing_fixture

    incomplete = {
        "tariff_id": 7,
        "internal_price_chf_per_kwh": 0.12,
        "grid_fee_chf_per_kwh": 0.08,
        "network_level": "same",
        "distribution_model": "proportional",
    }
    saved = _install_billing_fixture(monkeypatch, policy=incomplete)

    with pytest.raises(BillingRunError) as exc:
        run_billing_period("community-a", START, END)
    assert "vat_mode" in str(exc.value)

    assert saved == []


# --- Blocker 3: policy access needs a confirmed admin membership -------------


def _patch_status_members(monkeypatch, app_module, members):  # noqa: F811
    status = dict(STATUS)
    status["members"] = members
    monkeypatch.setattr(
        app_module.dashboard_module.formation_wizard,
        "get_community_status",
        MagicMock(return_value=status),
    )
    monkeypatch.setattr(
        app_module.db, "list_billing_policies", MagicMock(return_value=[])
    )
    save = MagicMock(return_value=9)
    monkeypatch.setattr(app_module.db, "save_billing_policy", save)
    return save


@pytest.mark.parametrize("member_status", ["invited", "rejected"])
def test_policy_routes_refuse_unconfirmed_admin_memberships(
    app_module,  # noqa: F811
    monkeypatch,
    member_status,
):
    save = _patch_status_members(
        monkeypatch,
        app_module,
        [{"building_id": "b-admin", "role": "admin", "status": member_status}],
    )
    client = app_module.web.test_client()
    _set_session(client, building_id="b-admin")

    assert client.get(POLICY_URL).status_code == 403
    assert client.post(POLICY_URL, data=_post_form()).status_code == 403
    save.assert_not_called()


def test_policy_routes_accept_a_confirmed_admin(app_module, monkeypatch):  # noqa: F811
    _patch_status_members(
        monkeypatch,
        app_module,
        [{"building_id": "b-admin", "role": "admin", "status": "confirmed"}],
    )
    client = app_module.web.test_client()
    _set_session(client, building_id="b-admin")

    assert client.get(POLICY_URL).status_code == 200


# --- Blocker 4: canonical label maps reach the template ----------------------


def test_policy_view_passes_the_canonical_label_maps(app_module, monkeypatch):  # noqa: F811
    _patch_admin(monkeypatch, app_module)
    view = app_module.dashboard_module.leg_billing_policy_view(COMMUNITY, "b-admin")

    assert view["policy_labels"]["network_level"] == billing_policy.NETWORK_LEVEL_LABELS
    assert (
        view["policy_labels"]["distribution_model"]
        == billing_policy.DISTRIBUTION_MODEL_LABELS
    )
    assert view["policy_labels"]["vat_mode"] == billing_policy.VAT_MODE_LABELS
    assert (
        view["policy_labels"]["delivery_method"]
        == billing_policy.DELIVERY_METHOD_LABELS
    )


def test_policy_template_renders_labels_from_the_canonical_maps():
    source = (PROJECT_ROOT / "templates" / "leg_billing_policy.html").read_text(
        encoding="utf-8"
    )
    for key in ("network_level", "distribution_model", "vat_mode", "delivery_method"):
        assert f"policy_labels.{key}" in source
    for duplicated in (
        "Gleiche Netzebene",
        "Unterschiedliche Netzebenen",
        "Proportional",
        "Einfach",
        "Keine Mehrwertsteuer",
        "Mehrwertsteuer ausweisen",
        "PDF-Download",
    ):
        assert duplicated not in source, f"duplicated label in template: {duplicated}"


def test_describe_version_shows_legacy_null_payment_days_and_prefix():
    version = {
        "effective_from": date(2026, 1, 1),
        "internal_price_chf_per_kwh": Decimal("0.15"),
        "grid_fee_chf_per_kwh": Decimal("0.08"),
        "network_level": "same",
        "distribution_model": "proportional",
        "vat_mode": "none",
        "vat_rate_pct": Decimal(0),
        "payment_days": None,
        "invoice_prefix": None,
        "delivery_method": "email",
    }
    described = billing_policy.describe_version(version)
    assert described["payment_days_display"] == "Nicht angegeben"
    assert described["invoice_prefix_display"] == "Nicht angegeben"


def test_describe_version_shows_populated_payment_days_and_prefix():
    version = {
        "effective_from": date(2026, 1, 1),
        "internal_price_chf_per_kwh": Decimal("0.15"),
        "grid_fee_chf_per_kwh": Decimal("0.08"),
        "network_level": "same",
        "distribution_model": "proportional",
        "vat_mode": "none",
        "vat_rate_pct": Decimal(0),
        "payment_days": 30,
        "invoice_prefix": "LEG-2026",
        "delivery_method": "email",
    }
    described = billing_policy.describe_version(version)
    assert described["payment_days_display"] == "30 Tage"
    assert described["invoice_prefix_display"] == "LEG-2026"


def test_policy_template_uses_display_values_for_payment_days_and_prefix():
    source = (PROJECT_ROOT / "templates" / "leg_billing_policy.html").read_text(
        encoding="utf-8"
    )
    assert "version.payment_days_display" in source
    assert "version.invoice_prefix_display" in source
    assert "{{ version.payment_days }} Tage" not in source
    assert "{{ version.invoice_prefix }}" not in source
