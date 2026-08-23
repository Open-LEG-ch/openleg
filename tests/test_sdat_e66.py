# SPDX-License-Identifier: AGPL-3.0-or-later
"""Parser contract for Swiss ebIX E66 (ValidatedMeteredData_16) documents.

An E66 file carries one series per (metering point, direction, product
channel). The parser folds the three product channels of a (point, direction)
pair into one wide row, derives each timestamp from the block interval and the
observation sequence, and reports data problems as warnings rather than
refusing the file.
"""

import os
from datetime import datetime, timezone
from decimal import Decimal

import sdat_e66

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")

POINT_ONE = "CH000000000000000000000000000001"
POINT_TWO = "CH000000000000000000000000000002"


def _load_fixture(filename):
    with open(os.path.join(FIXTURES_DIR, filename), encoding="utf-8") as handle:
        return handle.read()


def _sample():
    return _load_fixture("sdat_e66_sample.xml")


def _parsed():
    document, errors = sdat_e66.parse_e66_xml(_sample())
    assert errors == [], errors
    return document


def _row(rows, point, direction, sequence):
    measured_at = datetime(2026, 1, 5, 23, 0, tzinfo=timezone.utc)
    measured_at = measured_at.replace(minute=15 * (sequence - 1))
    for row in rows:
        if (
            row["metering_point_id"] == point
            and row["direction"] == direction
            and row["measured_at"] == measured_at
        ):
            return row
    raise AssertionError(f"no row for {point} {direction} seq {sequence}")


# ==== Document metadata ====


def test_parses_document_metadata():
    document = _parsed()
    assert document["document_id"] == "TESTDOC-1"
    assert document["doc_type"] == "E66"
    assert document["vnb_community_id"] == "TEST-COMMUNITY"
    assert document["block_count"] == 9
    assert document["period_start"] == datetime(2026, 1, 5, 23, 0, tzinfo=timezone.utc)
    assert document["period_end"] == datetime(2026, 1, 5, 23, 45, tzinfo=timezone.utc)


def test_reports_the_distinct_metering_points():
    document = _parsed()
    assert document["point_ids"] == [POINT_ONE, POINT_TWO]


def test_folds_three_channels_per_pair_into_one_row_each():
    document = _parsed()
    assert len(document["rows"]) == 9


# ==== Timestamps ====


def test_derives_timestamps_from_sequence_and_resolution():
    rows = _parsed()["rows"]
    stamps = sorted(
        row["measured_at"] for row in rows if row["metering_point_id"] == POINT_ONE
    )
    assert stamps == [
        datetime(2026, 1, 5, 23, 0, tzinfo=timezone.utc),
        datetime(2026, 1, 5, 23, 15, tzinfo=timezone.utc),
        datetime(2026, 1, 5, 23, 30, tzinfo=timezone.utc),
    ]


def test_every_timestamp_is_timezone_aware_utc():
    for row in _parsed()["rows"]:
        assert row["measured_at"].tzinfo is not None
        assert row["measured_at"].utcoffset() == timezone.utc.utcoffset(None)


def test_rows_are_labelled_with_the_interval_start():
    rows = _parsed()["rows"]
    first = _row(rows, POINT_ONE, "consumption", 1)
    assert first["measured_at"] == datetime(2026, 1, 5, 23, 0, tzinfo=timezone.utc)
    assert first["resolution_minutes"] == 15


# ==== Channel folding ====


def test_wide_row_carries_total_grid_and_community():
    row = _row(_parsed()["rows"], POINT_ONE, "consumption", 1)
    assert row["total_kwh"] == Decimal("0.100")
    assert row["grid_kwh"] == Decimal("0.060")
    assert row["community_kwh"] == Decimal("0.040")


def test_volumes_are_decimals_not_floats():
    row = _row(_parsed()["rows"], POINT_ONE, "consumption", 1)
    for field in ("total_kwh", "grid_kwh", "community_kwh"):
        assert isinstance(row[field], Decimal), f"{field} must stay Decimal"


# ==== The dual role point ====


def test_dual_role_point_yields_both_directions():
    rows = _parsed()["rows"]
    directions = {
        row["direction"] for row in rows if row["metering_point_id"] == POINT_TWO
    }
    assert directions == {"consumption", "production"}


def test_dual_role_point_rows_do_not_collide():
    rows = _parsed()["rows"]
    keys = {
        (row["metering_point_id"], row["direction"], row["measured_at"]) for row in rows
    }
    assert len(keys) == len(rows), "point, direction and time must be unique together"
    consumption = _row(rows, POINT_TWO, "consumption", 1)
    production = _row(rows, POINT_TWO, "production", 1)
    assert consumption["measured_at"] == production["measured_at"]
    assert consumption["total_kwh"] != production["total_kwh"]


# ==== Condition ====


def test_condition_rolls_up_from_a_non_total_channel():
    rows = _parsed()["rows"]
    flagged = [row for row in rows if row["condition_code"] is not None]
    assert len(flagged) == 1
    assert flagged[0]["condition_code"] == "21"
    assert flagged[0]["metering_point_id"] == POINT_TWO
    assert flagged[0]["direction"] == "production"


# ==== Balance validation ====


def test_rounding_inside_tolerance_does_not_warn():
    document = _parsed()
    balance_warnings = [w for w in document["warnings"] if "Kanalsumme" in w]
    assert balance_warnings == [], document["warnings"]


def test_channel_imbalance_warns_but_still_returns_rows():
    document, errors = sdat_e66.parse_e66_xml(E66_IMBALANCED)
    assert errors == []
    assert document["rows"], "an imbalance must not discard the data"
    assert any("Kanalsumme" in warning for warning in document["warnings"])


def test_missing_channel_is_null_and_warns():
    document, errors = sdat_e66.parse_e66_xml(E66_MISSING_CHANNEL)
    assert errors == []
    assert document["rows"][0]["community_kwh"] is None
    assert any("Kanal" in warning for warning in document["warnings"])


# ==== Hard errors ====


def test_unexpected_measure_unit_is_a_hard_error():
    document, errors = sdat_e66.parse_e66_xml(E66_BAD_UNIT)
    assert document == {}
    assert errors and "Masseinheit" in errors[0]


def test_one_series_cannot_mix_interval_definitions():
    xml = _sample().replace(
        "<rsm:Resolution>15</rsm:Resolution>",
        "<rsm:Resolution>30</rsm:Resolution>",
        1,
    )

    document, errors = sdat_e66.parse_e66_xml(xml)

    assert document == {}
    assert errors and "widersprüchliche Intervalle" in errors[0]


def test_empty_input_returns_an_error():
    document, errors = sdat_e66.parse_e66_xml("")
    assert document == {}
    assert errors


def test_malformed_xml_does_not_leak_the_input():
    secret = "sensitive test payload"
    document, errors = sdat_e66.parse_e66_xml(f"<broken>{secret}")
    assert document == {}
    assert errors
    assert all(secret not in message for message in errors)


def test_oversize_input_is_rejected(monkeypatch):
    monkeypatch.setattr(sdat_e66, "MAX_E66_BYTES", 100)
    document, errors = sdat_e66.parse_e66_xml(_sample())
    assert document == {}
    assert errors and "gross" in errors[0]


def test_parser_never_raises_on_truncated_input():
    """Not raising is half of it; the other half is saying what went wrong.

    The arity of the return value passed even for a parser that swallowed the
    truncation and answered with an empty document and no error, which the
    importer would have stored as a delivery containing nothing.
    """
    sample = _sample()
    for cut in range(0, len(sample), max(1, len(sample) // 12)):
        document, errors = sdat_e66.parse_e66_xml(sample[:cut])

        assert errors, f"truncation at {cut} reported no error"
        assert not document.get("rows"), (
            f"truncation at {cut} yielded rows the file does not contain"
        )


def test_duplicate_sequence_keeps_the_last_value():
    document, errors = sdat_e66.parse_e66_xml(E66_DUPLICATE_SEQUENCE)
    assert errors == []
    assert len(document["rows"]) == 1
    assert document["rows"][0]["total_kwh"] == Decimal("0.900")


# ==== Document type detection ====


def test_recognises_an_e66_document():
    assert sdat_e66.is_e66_document(_sample()) is True


def test_does_not_recognise_an_e31_document():
    assert sdat_e66.is_e66_document(E31_SAMPLE) is False


def test_parsing_an_e31_document_returns_an_error_not_a_crash():
    document, errors = sdat_e66.parse_e66_xml(E31_SAMPLE)
    assert document == {}
    assert errors


def test_recognises_an_e31_document():
    assert sdat_e66.is_e31_document(E31_SAMPLE) is True


def test_an_e66_document_is_not_an_e31():
    assert sdat_e66.is_e31_document(_sample()) is False


def test_unknown_xml_is_neither_e66_nor_e31():
    # The importer treats an unrecognised file differently from a known E31
    # sibling, so "not E66" must not be read as "therefore E31".
    foreign = "<?xml version='1.0'?><something-else/>"
    assert sdat_e66.is_e66_document(foreign) is False
    assert sdat_e66.is_e31_document(foreign) is False


def test_empty_input_is_not_an_e31():
    assert sdat_e66.is_e31_document("") is False
    assert sdat_e66.is_e31_document(None) is False


# ==== Cheap identity ====


def test_extracts_the_header_document_id_not_a_block_one():
    # The fixture carries ten DocumentID elements: TESTDOC-1 in the header and
    # TESTDOC-1@1..@9 one per block. Three of them sit inside the first 4096
    # characters, so "the first match" is not a safe rule. Only the id inside
    # InstanceDocument identifies the delivery, and it is the ledger key.
    assert sdat_e66.extract_document_id(_sample()) == "TESTDOC-1"


def test_extracts_the_document_id_from_a_truncated_head():
    # The caller passes a bounded prefix, not the whole document.
    head = _sample()[:16384]
    assert sdat_e66.extract_document_id(head) == "TESTDOC-1"


def test_extracts_the_document_id_of_an_e31():
    assert sdat_e66.extract_document_id(E31_SAMPLE) == "AGG-1"


def test_returns_none_when_there_is_no_document_id():
    # None must make the caller do the full work rather than skip the file.
    assert sdat_e66.extract_document_id("<not-sdat/>") is None
    assert sdat_e66.extract_document_id("") is None
    assert sdat_e66.extract_document_id(None) is None


def test_returns_none_when_the_head_cuts_off_before_the_id():
    assert sdat_e66.extract_document_id(_sample()[:200]) is None


def test_ignores_a_document_id_outside_the_instance_document():
    # A block id alone must not be mistaken for the delivery id.
    orphan = (
        "<?xml version='1.0'?><rsm:ValidatedMeteredData_16 xmlns:rsm='x'>"
        "<rsm:MeteringData><rsm:DocumentID>BLOCK-ONLY@1</rsm:DocumentID>"
        "</rsm:MeteringData></rsm:ValidatedMeteredData_16>"
    )
    assert sdat_e66.extract_document_id(orphan) is None


# ==== Masking ====


def test_mask_point_id_hides_all_but_the_last_six_digits():
    masked = sdat_e66.mask_point_id(POINT_ONE)
    assert masked.endswith("000001")
    assert POINT_ONE not in masked
    assert len(masked) < len(POINT_ONE)


def test_mask_point_id_tolerates_short_and_empty_values():
    assert sdat_e66.mask_point_id("") == ""
    assert sdat_e66.mask_point_id(None) == ""
    assert sdat_e66.mask_point_id("abc")


# ==== Module boundary ====


def test_sdat_e66_does_not_import_meter_data():
    path = os.path.join(os.path.dirname(FIXTURES_DIR), "..", "sdat_e66.py")
    with open(os.path.abspath(path), encoding="utf-8") as handle:
        source = handle.read()
    assert "import meter_data" not in source, (
        "sdat_e66 and meter_data must stay decoupled; they model different grains"
    )


# ==== Inline edge case documents ====


def _document(blocks: str) -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<rsm:ValidatedMeteredData_16 xmlns:rsm="http://www.strom.ch">
  <rsm:ValidatedMeteredData_HeaderInformation>
    <rsm:InstanceDocument>
      <rsm:DocumentID>EDGE-1</rsm:DocumentID>
      <rsm:DocumentType listAgencyID="260">
        <rsm:ebIXCode>E66</rsm:ebIXCode>
      </rsm:DocumentType>
      <rsm:Creation>2026-01-06T04:30:00Z</rsm:Creation>
    </rsm:InstanceDocument>
    <rsm:BusinessScopeProcess>
      <rsm:ReportPeriod>
        <rsm:StartDateTime>2026-01-05T23:00:00Z</rsm:StartDateTime>
        <rsm:EndDateTime>2026-01-05T23:15:00Z</rsm:EndDateTime>
      </rsm:ReportPeriod>
    </rsm:BusinessScopeProcess>
  </rsm:ValidatedMeteredData_HeaderInformation>
{blocks}
</rsm:ValidatedMeteredData_16>"""


def test_extract_document_id_does_not_treat_a_closing_tag_as_an_opening_tag():
    malformed = (
        "</rsm:InstanceDocument>"
        "<rsm:DocumentID>WRONG</rsm:DocumentID>"
        "</rsm:InstanceDocument>"
    )

    assert sdat_e66.extract_document_id(malformed) is None


def _block(product_xml: str, observations: str, unit: str = "KWH") -> str:
    return f"""  <rsm:MeteringData>
    <rsm:Interval>
      <rsm:StartDateTime>2026-01-05T23:00:00Z</rsm:StartDateTime>
      <rsm:EndDateTime>2026-01-05T23:15:00Z</rsm:EndDateTime>
    </rsm:Interval>
    <rsm:Resolution>
      <rsm:Resolution>15</rsm:Resolution>
      <rsm:Unit>MIN</rsm:Unit>
    </rsm:Resolution>
    <rsm:ConsumptionMeteringPoint>
      <rsm:VSENationalID>{POINT_ONE}</rsm:VSENationalID>
    </rsm:ConsumptionMeteringPoint>
    <rsm:Product>
      <rsm:ID>{product_xml}</rsm:ID>
      <rsm:MeasureUnit>{unit}</rsm:MeasureUnit>
    </rsm:Product>
    <rsm:Community>
      <rsm:CommunityID>TEST-COMMUNITY</rsm:CommunityID>
    </rsm:Community>
{observations}
  </rsm:MeteringData>"""


def _observation(sequence: int, volume: str) -> str:
    return f"""    <rsm:Observation>
      <rsm:Position><rsm:Sequence>{sequence}</rsm:Sequence></rsm:Position>
      <rsm:Volume>{volume}</rsm:Volume>
    </rsm:Observation>"""


TOTAL_ID = '<rsm:ebIXCode schemeAgencyID="9">8716867000030</rsm:ebIXCode>'
GRID_ID = (
    '<rsm:VSENationalCode schemeAgencyID="260">2404050010124</rsm:VSENationalCode>'
)
COMMUNITY_ID = (
    '<rsm:VSENationalCode schemeAgencyID="260">2404050010123</rsm:VSENationalCode>'
)

E66_IMBALANCED = _document(
    "\n".join(
        [
            _block(TOTAL_ID, _observation(1, "1.000")),
            _block(GRID_ID, _observation(1, "0.500")),
            _block(COMMUNITY_ID, _observation(1, "0.100")),
        ]
    )
)

E66_MISSING_CHANNEL = _document(
    "\n".join(
        [
            _block(TOTAL_ID, _observation(1, "1.000")),
            _block(GRID_ID, _observation(1, "1.000")),
        ]
    )
)

E66_BAD_UNIT = _document(_block(TOTAL_ID, _observation(1, "1.000"), unit="WH"))

E66_DUPLICATE_SEQUENCE = _document(
    _block(TOTAL_ID, _observation(1, "0.100") + "\n" + _observation(1, "0.900"))
)

E31_SAMPLE = """<?xml version="1.0" encoding="utf-8"?>
<rsm:AggregatedMeteredData_13 xmlns:rsm="http://www.strom.ch">
  <rsm:AggregatedMeteredData_HeaderInformation>
    <rsm:InstanceDocument>
      <rsm:DocumentID>AGG-1</rsm:DocumentID>
      <rsm:DocumentType listAgencyID="260">
        <rsm:ebIXCode>E31</rsm:ebIXCode>
      </rsm:DocumentType>
    </rsm:InstanceDocument>
  </rsm:AggregatedMeteredData_HeaderInformation>
  <rsm:MeteringData>
    <rsm:MeteringGridArea>
      <rsm:EICID schemeAgencyID="305">12Y-0000000041-T</rsm:EICID>
    </rsm:MeteringGridArea>
  </rsm:MeteringData>
</rsm:AggregatedMeteredData_13>"""
