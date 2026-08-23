# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contract for the SDAT metering to billing adapter.

The billing engine wants participant-keyed 15-minute frames with identical
indexes. The metering store holds point-keyed rows in UTC. This adapter is the
only place that translation happens, so it also owns the data-quality gate:
a period that cannot be billed must fail loudly, naming the offending point.

Fixtures are synthetic SDAT-E66 documents. No citizen data enters this suite.
"""

from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import billing_engine
import billing_readings
import database
import sdat_e66

FIXTURE = Path(__file__).parent / "fixtures" / "sdat_e66_sample.xml"
ZURICH = ZoneInfo("Europe/Zurich")
COMMUNITY = "COMM-TEST"
POINT_A = "CH000000000000000000000000000001"
POINT_B = "CH000000000000000000000000000002"
BUILDING_A = "BLD-A"
BUILDING_B = "BLD-B"

# The fixture covers 2026-01-05 23:00 to 23:45 UTC, which is 2026-01-06
# 00:00 to 00:45 in Europe/Zurich. Crossing midnight keeps the timezone
# handling honest.
PERIOD_START = datetime(2026, 1, 6, 0, 0, tzinfo=ZURICH)
PERIOD_END = datetime(2026, 1, 6, 0, 45, tzinfo=ZURICH)


def _fixture_rows():
    document, errors = sdat_e66.parse_e66_file(FIXTURE)
    assert errors == [], errors
    return document


def _points(status_a="confirmed", status_b="confirmed", building_a=BUILDING_A):
    return [
        {
            "metering_point_id": POINT_A,
            "building_id": building_a,
            "member_status": status_a,
        },
        {
            "metering_point_id": POINT_B,
            "building_id": BUILDING_B,
            "member_status": status_b,
        },
    ]


def _install(monkeypatch, points, rows):
    """Fake the repository seam the way the real SQL behaves."""

    def _get_points(community_id):
        assert community_id == COMMUNITY
        return deepcopy(points)

    def _get_readings(community_id, period_start, period_end):
        assert community_id == COMMUNITY
        return [
            deepcopy(row)
            for row in rows
            if period_start <= row["measured_at"] < period_end
        ]

    monkeypatch.setattr(database, "get_community_metering_points", _get_points)
    monkeypatch.setattr(database, "get_period_readings", _get_readings)


@pytest.fixture
def rows():
    return _fixture_rows()["rows"]


# --- Step 1: participant-keyed frames for a half-open period ---


def test_loads_participant_keyed_frames(monkeypatch, rows):
    _install(monkeypatch, _points(), rows)

    frames = billing_readings.load_period_frames(COMMUNITY, PERIOD_START, PERIOD_END)

    assert list(frames.consumption.columns) == [BUILDING_A, BUILDING_B]
    assert len(frames.consumption) == 3
    assert frames.consumption[BUILDING_A].sum() == pytest.approx(0.6)
    assert frames.consumption[BUILDING_B].sum() == pytest.approx(1.15)
    assert frames.production[BUILDING_B].sum() == pytest.approx(6.0)


def test_index_is_local_quarter_hours(monkeypatch, rows):
    _install(monkeypatch, _points(), rows)

    frames = billing_readings.load_period_frames(COMMUNITY, PERIOD_START, PERIOD_END)

    assert str(frames.consumption.index.tz) == "Europe/Zurich"
    assert frames.consumption.index[0] == PERIOD_START
    assert frames.consumption.index[-1] == PERIOD_START + timedelta(minutes=30)


def test_period_end_is_exclusive(monkeypatch, rows):
    _install(monkeypatch, _points(), rows)

    frames = billing_readings.load_period_frames(
        COMMUNITY, PERIOD_START, PERIOD_START + timedelta(minutes=30)
    )

    assert len(frames.consumption) == 2


def test_naive_boundaries_are_read_in_the_period_timezone(monkeypatch, rows):
    _install(monkeypatch, _points(), rows)
    # Naive on purpose: an operator entering a period says "1 July", not
    # "1 July 00:00+02:00". DTZ001 is the rule under test here.
    naive_start = datetime(2026, 1, 6, 0, 0)  # noqa: DTZ001
    naive_end = datetime(2026, 1, 6, 0, 45)  # noqa: DTZ001

    frames = billing_readings.load_period_frames(COMMUNITY, naive_start, naive_end)

    assert len(frames.consumption) == 3


def test_production_frame_matches_the_consumption_shape(monkeypatch, rows):
    """The engine asserts index equality and needs a column per participant."""
    _install(monkeypatch, _points(), rows)

    frames = billing_readings.load_period_frames(COMMUNITY, PERIOD_START, PERIOD_END)

    assert frames.production.index.equals(frames.consumption.index)
    assert list(frames.production.columns) == list(frames.consumption.columns)
    # Building A has no production meter and must read as zero, not as absent.
    assert frames.production[BUILDING_A].sum() == 0.0


def test_values_are_floats_not_decimals(monkeypatch, rows):
    _install(monkeypatch, _points(), rows)

    frames = billing_readings.load_period_frames(COMMUNITY, PERIOD_START, PERIOD_END)

    assert frames.consumption.to_numpy().dtype.kind == "f"
    assert frames.production.to_numpy().dtype.kind == "f"


def test_two_points_of_one_member_collapse_into_one_column(monkeypatch, rows):
    """A member with a separate production meter is still one participant."""
    points = _points()
    points[1]["building_id"] = BUILDING_A
    _install(monkeypatch, points, rows)

    frames = billing_readings.load_period_frames(COMMUNITY, PERIOD_START, PERIOD_END)

    assert list(frames.consumption.columns) == [BUILDING_A]
    assert frames.consumption[BUILDING_A].sum() == pytest.approx(1.75)
    assert frames.production[BUILDING_A].sum() == pytest.approx(6.0)


# --- Step 3: data quality must fail with actionable diagnostics ---


def _kinds(excinfo):
    return {problem["kind"] for problem in excinfo.value.problems}


def test_unconfirmed_member_is_rejected(monkeypatch, rows):
    _install(monkeypatch, _points(status_b="invited"), rows)

    with pytest.raises(billing_readings.PeriodDataError) as excinfo:
        billing_readings.load_period_frames(COMMUNITY, PERIOD_START, PERIOD_END)

    assert "unconfirmed_member" in _kinds(excinfo)
    assert POINT_B in str(excinfo.value)


def test_point_without_a_member_is_rejected(monkeypatch, rows):
    _install(monkeypatch, _points(building_a=None), rows)

    with pytest.raises(billing_readings.PeriodDataError) as excinfo:
        billing_readings.load_period_frames(COMMUNITY, PERIOD_START, PERIOD_END)

    assert "unmapped_point" in _kinds(excinfo)
    assert POINT_A in str(excinfo.value)


def test_unknown_metering_point_is_rejected(monkeypatch, rows):
    stray = deepcopy(rows[0])
    stray["metering_point_id"] = "CH000000000000000000000000000099"
    _install(monkeypatch, _points(), rows + [stray])

    with pytest.raises(billing_readings.PeriodDataError) as excinfo:
        billing_readings.load_period_frames(COMMUNITY, PERIOD_START, PERIOD_END)

    assert "unknown_point" in _kinds(excinfo)


def test_duplicate_interval_is_rejected(monkeypatch, rows):
    _install(monkeypatch, _points(), rows + [deepcopy(rows[0])])

    with pytest.raises(billing_readings.PeriodDataError) as excinfo:
        billing_readings.load_period_frames(COMMUNITY, PERIOD_START, PERIOD_END)

    assert "duplicate_interval" in _kinds(excinfo)


def test_missing_interval_is_rejected(monkeypatch, rows):
    gapped = [row for row in rows if row["measured_at"].minute != 15]
    _install(monkeypatch, _points(), gapped)

    with pytest.raises(billing_readings.PeriodDataError) as excinfo:
        billing_readings.load_period_frames(COMMUNITY, PERIOD_START, PERIOD_END)

    assert "missing_interval" in _kinds(excinfo)
    assert "00:15" in str(excinfo.value)


def test_negative_value_is_rejected(monkeypatch, rows):
    dirty = deepcopy(rows)
    dirty[0]["total_kwh"] = -1
    _install(monkeypatch, _points(), dirty)

    with pytest.raises(billing_readings.PeriodDataError) as excinfo:
        billing_readings.load_period_frames(COMMUNITY, PERIOD_START, PERIOD_END)

    assert "negative_value" in _kinds(excinfo)


def test_misaligned_interval_is_rejected(monkeypatch, rows):
    """A timestamp off the quarter-hour grid must not silently add a row.

    Assigning by label would extend the frame with an extra index entry, which
    would leave the period looking complete while carrying an interval nobody
    expects.
    """
    dirty = deepcopy(rows)
    dirty[0]["measured_at"] = dirty[0]["measured_at"] + timedelta(minutes=7)
    _install(monkeypatch, _points(), dirty)

    with pytest.raises(billing_readings.PeriodDataError) as excinfo:
        billing_readings.load_period_frames(COMMUNITY, PERIOD_START, PERIOD_END)

    assert "misaligned_interval" in _kinds(excinfo)


def test_mixed_resolution_is_rejected(monkeypatch, rows):
    dirty = deepcopy(rows)
    dirty[0]["resolution_minutes"] = 30
    _install(monkeypatch, _points(), dirty)

    with pytest.raises(billing_readings.PeriodDataError) as excinfo:
        billing_readings.load_period_frames(COMMUNITY, PERIOD_START, PERIOD_END)

    assert "mixed_resolution" in _kinds(excinfo)


def test_missing_vnb_allocation_is_rejected(monkeypatch, rows):
    dirty = deepcopy(rows)
    next(row for row in dirty if row["direction"] == "consumption")["community_kwh"] = (
        None
    )
    _install(monkeypatch, _points(), dirty)

    with pytest.raises(billing_readings.PeriodDataError) as excinfo:
        billing_readings.load_period_frames(COMMUNITY, PERIOD_START, PERIOD_END)

    assert "missing_vnb_allocation" in _kinds(excinfo)


def test_mixed_units_never_reach_the_adapter():
    """The unit gate lives at the parse boundary; readings carry no unit column.

    A document in Wh must fail before anything is stored, so the adapter can
    trust that every row it loads is in kWh.
    """
    xml = FIXTURE.read_text(encoding="utf-8").replace(
        "<rsm:MeasureUnit>KWH</rsm:MeasureUnit>",
        "<rsm:MeasureUnit>WH</rsm:MeasureUnit>",
        1,
    )
    document, errors = sdat_e66.parse_e66_xml(xml)

    assert errors, "a non-kWh document must be a hard parse error"
    assert not document.get("rows")


def test_all_problems_are_reported_at_once(monkeypatch, rows):
    """One run per period: an operator should see every defect, not the first."""
    dirty = deepcopy(rows)
    dirty[0]["total_kwh"] = -1
    dirty[1]["resolution_minutes"] = 30
    _install(monkeypatch, _points(status_b="invited"), dirty)

    with pytest.raises(billing_readings.PeriodDataError) as excinfo:
        billing_readings.load_period_frames(COMMUNITY, PERIOD_START, PERIOD_END)

    assert {"negative_value", "mixed_resolution", "unconfirmed_member"} <= _kinds(
        excinfo
    )


@pytest.mark.parametrize(
    "period_end",
    (
        pytest.param(datetime(2026, 2, 1, tzinfo=ZURICH), id="equal-to-the-start"),
        pytest.param(datetime(2026, 1, 31, tzinfo=ZURICH), id="before-the-start"),
    ),
)
def test_a_period_that_does_not_move_forward_is_rejected(monkeypatch, rows, period_end):
    """The period_end <= period_start guard, which fires before any lookup.

    It had no test: the one named test_empty_period_is_rejected passed a valid,
    ordered range with no matching readings and reached the no_readings branch
    instead. That test is renamed below for what it actually covers.
    """
    _install(monkeypatch, _points(), rows)

    with pytest.raises(billing_readings.PeriodDataError) as excinfo:
        billing_readings.load_period_frames(
            COMMUNITY, datetime(2026, 2, 1, tzinfo=ZURICH), period_end
        )

    assert "empty_period" in _kinds(excinfo)


def test_a_period_with_no_readings_is_rejected(monkeypatch, rows):
    _install(monkeypatch, _points(), rows)

    with pytest.raises(billing_readings.PeriodDataError) as excinfo:
        billing_readings.load_period_frames(
            COMMUNITY,
            datetime(2026, 2, 1, tzinfo=ZURICH),
            datetime(2026, 2, 2, tzinfo=ZURICH),
        )

    assert "no_readings" in _kinds(excinfo)


# --- Step 5: the frames drive the billing engine ---


def _summary(frames, price=0.11):
    return billing_engine.generate_billing_summary(
        frames.production,
        frames.consumption,
        grid_fee_per_kwh=0.09,
        internal_price_per_kwh=price,
        network_level="same",
    )


def test_frames_drive_the_engine_into_charges_and_credits(monkeypatch, rows):
    _install(monkeypatch, _points(), rows)
    frames = billing_readings.load_period_frames(COMMUNITY, PERIOD_START, PERIOD_END)

    summary = _summary(frames)

    charges = [i for i in summary["line_items"] if i["item_type"] == "consumer_charge"]
    credits = {
        i["participant_id"]: i
        for i in summary["line_items"]
        if i["item_type"] == "producer_credit"
    }
    assert charges, "consumption must produce positive charges"
    assert all(item["amount_chf"] > 0 for item in charges)
    # Building B owns the only production meter, so it carries the credit.
    assert credits[BUILDING_B]["amount_chf"] < 0
    # Building A produced nothing. The engine still emits a zero-value credit
    # line because the frames must share their columns; it must not be positive.
    assert credits[BUILDING_A]["amount_chf"] == pytest.approx(0.0)
    assert summary["internal_price_chf_per_kwh"] == 0.11


def test_engine_ledger_balances_to_zero(monkeypatch, rows):
    _install(monkeypatch, _points(), rows)
    frames = billing_readings.load_period_frames(COMMUNITY, PERIOD_START, PERIOD_END)

    summary = _summary(frames)

    assert sum(item["amount_chf"] for item in summary["line_items"]) == pytest.approx(
        0.0, abs=1e-9
    )


def test_reconciliation_reports_the_gap_to_the_vnb_allocation(monkeypatch, rows):
    """The VNB already allocated. The engine reallocates. Surface the delta."""
    _install(monkeypatch, _points(), rows)
    frames = billing_readings.load_period_frames(COMMUNITY, PERIOD_START, PERIOD_END)
    summary = _summary(frames)

    report = billing_readings.reconcile_with_vnb(frames, summary)

    # The fixture's VNB community channel sums to 0.721 kWh on the consumption
    # side while proportional allocation reaches 1.75 kWh.
    assert report["vnb_allocated_kwh"] == pytest.approx(0.721)
    assert report["engine_allocated_kwh"] == pytest.approx(1.75)
    assert report["difference_kwh"] == pytest.approx(1.029)
    assert report["per_participant"][BUILDING_A]["vnb_kwh"] == pytest.approx(0.22)


def test_reconciliation_uses_six_decimal_charge_quantities():
    frames = SimpleNamespace(
        vnb_reference={
            "community_consumption_kwh": 0.123456,
            "community_production_kwh": 0.0,
            "per_participant": {
                BUILDING_A: {"consumption_kwh": 0.123456, "production_kwh": 0.0},
            },
        }
    )
    summary = {
        "total_allocated_kwh": 0.12,
        "participants": [{"id": BUILDING_A, "allocated_kwh": 0.12}],
        "line_items": [
            {
                "participant_id": BUILDING_A,
                "item_type": "consumer_charge",
                "quantity_kwh": 0.123456,
            }
        ],
    }

    report = billing_readings.reconcile_with_vnb(frames, summary)

    assert report["difference_kwh"] == 0
    assert report["per_participant"][BUILDING_A]["difference_kwh"] == 0


def test_nothing_is_persisted_by_the_adapter(monkeypatch, rows):
    """This slice reads. Writing a period stays with save_billing_period."""

    def _fail(*_args, **_kwargs):
        raise AssertionError("the adapter must not write")

    monkeypatch.setattr(database, "save_billing_period", _fail)
    _install(monkeypatch, _points(), rows)

    frames = billing_readings.load_period_frames(COMMUNITY, PERIOD_START, PERIOD_END)
    _summary(frames)


# --- Provenance ---


def test_frames_carry_import_provenance(monkeypatch, rows):
    sourced = deepcopy(rows)
    for row in sourced:
        row["source_document_id"] = "TESTDOC-1"
    _install(monkeypatch, _points(), sourced)

    frames = billing_readings.load_period_frames(COMMUNITY, PERIOD_START, PERIOD_END)

    assert frames.provenance["source_document_ids"] == ("TESTDOC-1",)
    assert frames.provenance["interval_count"] == 3
    assert frames.provenance["resolution_minutes"] == 15


def test_provenance_survives_multiple_source_documents(monkeypatch, rows):
    sourced = deepcopy(rows)
    for index, row in enumerate(sourced):
        row["source_document_id"] = "DOC-B" if index % 2 else "DOC-A"
    _install(monkeypatch, _points(), sourced)

    frames = billing_readings.load_period_frames(COMMUNITY, PERIOD_START, PERIOD_END)

    assert frames.provenance["source_document_ids"] == ("DOC-A", "DOC-B")


def test_frames_are_plain_pandas(monkeypatch, rows):
    _install(monkeypatch, _points(), rows)

    frames = billing_readings.load_period_frames(COMMUNITY, PERIOD_START, PERIOD_END)

    assert isinstance(frames.consumption, pd.DataFrame)
    assert isinstance(frames.production, pd.DataFrame)


def test_an_unknown_direction_is_refused_rather_than_dropped(monkeypatch, rows):
    """A reading the module cannot classify must not vanish from a bill.

    load_period_frames looked the direction up in a two-entry dict and skipped
    anything it did not recognise, with no warning and no record. Every other
    defect in this module is collected and raised, and billing_runner fails
    closed on all of them, so a VNB sending an unexpected direction code was the
    one way to get a quietly incomplete period instead of a refused one.
    """
    stray = dict(rows[0])
    stray["direction"] = "storage"
    _install(monkeypatch, _points(), [*rows, stray])

    with pytest.raises(billing_readings.PeriodDataError) as excinfo:
        billing_readings.load_period_frames(COMMUNITY, PERIOD_START, PERIOD_END)

    assert "unknown_direction" in _kinds(excinfo)
