# SPDX-License-Identifier: AGPL-3.0-or-later
"""Local meter connectors (Program 9 W4): SDAT XML import + CSV dispatch fix.

Keeps the appliance's data local: a LEG loads its own consumption from the
Swiss SDAT interval export (the XML a VNB hands out) without any live
integration. The parser targets the SDAT interval profile represented by the
fixtures below and reuses the existing meter storage path; real-world files may
carry more profiles and can extend it.
"""

from datetime import datetime
from unittest.mock import MagicMock

import meter_data

# A minimal but representative SDAT ValidatedMeteredData interval document:
# a start time, a 15-minute resolution, and three sequential volume readings.
SDAT_PLAIN = """<?xml version="1.0" encoding="UTF-8"?>
<ValidatedMeteredData_02>
  <MeteringData>
    <DocumentID>SDAT-TEST-001</DocumentID>
    <Interval>
      <StartDateTime>2026-01-01T00:00:00Z</StartDateTime>
    </Interval>
    <Resolution>
      <Resolution>15</Resolution>
      <Unit>MIN</Unit>
    </Resolution>
    <Observation><Sequence>1</Sequence><Volume>0.250</Volume></Observation>
    <Observation><Sequence>2</Sequence><Volume>0.300</Volume></Observation>
    <Observation><Sequence>3</Sequence><Volume>0.275</Volume></Observation>
  </MeteringData>
</ValidatedMeteredData_02>
"""

# The same document, namespaced, as SDAT files actually arrive.
SDAT_NAMESPACED = """<?xml version="1.0" encoding="UTF-8"?>
<rsm:ValidatedMeteredData_02 xmlns:rsm="http://www.strom.ch/SDAT">
  <rsm:MeteringData>
    <rsm:DocumentID>SDAT-TEST-002</rsm:DocumentID>
    <rsm:Interval>
      <rsm:StartDateTime>2026-01-01T00:00:00Z</rsm:StartDateTime>
    </rsm:Interval>
    <rsm:Resolution>
      <rsm:Resolution>15</rsm:Resolution>
      <rsm:Unit>MIN</rsm:Unit>
    </rsm:Resolution>
    <rsm:Observation><rsm:Sequence>1</rsm:Sequence><rsm:Volume>0.250</rsm:Volume></rsm:Observation>
    <rsm:Observation><rsm:Sequence>2</rsm:Sequence><rsm:Volume>0.300</rsm:Volume></rsm:Observation>
  </rsm:MeteringData>
</rsm:ValidatedMeteredData_02>
"""


class TestParseSdat:
    def test_returns_sequential_interval_readings(self):
        readings, errors = meter_data.parse_sdat_xml(SDAT_PLAIN)
        assert errors == []
        assert len(readings) == 3
        # each reading is (timestamp, consumption, production, feed_in)
        timestamps = [r[0] for r in readings]
        assert timestamps[0] == datetime(2026, 1, 1, 0, 0)
        assert timestamps[1] == datetime(2026, 1, 1, 0, 15)
        assert timestamps[2] == datetime(2026, 1, 1, 0, 30)
        consumptions = [r[1] for r in readings]
        assert consumptions == [0.250, 0.300, 0.275]
        # consumption-only document: no production / feed-in
        assert all(r[2] == 0.0 and r[3] == 0.0 for r in readings)

    def test_namespace_tolerant(self):
        readings, errors = meter_data.parse_sdat_xml(SDAT_NAMESPACED)
        assert errors == []
        assert len(readings) == 2
        assert readings[0][0] == datetime(2026, 1, 1, 0, 0)
        assert readings[1][1] == 0.300

    def test_empty_input_is_error_not_crash(self):
        readings, errors = meter_data.parse_sdat_xml("")
        assert readings == []
        assert errors


class TestIngestFile:
    def test_detects_and_stores_sdat(self, monkeypatch):
        monkeypatch.setattr(
            meter_data.db, "save_meter_readings", MagicMock(return_value=3)
        )
        monkeypatch.setattr(
            meter_data.db, "get_meter_reading_stats", MagicMock(return_value={})
        )
        monkeypatch.setattr(meter_data.db, "track_event", MagicMock())
        result = meter_data.ingest_file("b-1", SDAT_PLAIN)
        assert result["success"]
        assert result["readings_count"] == 3
        _, kwargs = meter_data.db.save_meter_readings.call_args
        assert kwargs.get("source") == "sdat"


def test_upload_endpoint_uses_one_ingestion_seam():
    from pathlib import Path

    source = Path(__file__).resolve().parent.parent / "app.py"
    app_source = source.read_text(encoding="utf-8")
    assert "meter_data.ingest_file(" in app_source
    assert "meter_data.ingest_sdat(" not in app_source


class TestCsvDispatchFix:
    def test_ingest_file_uses_csv_format_dispatch(self, monkeypatch):
        dispatch = MagicMock(return_value=([(datetime(2026, 1, 1), 1.0, 0.0, 0.0)], []))
        monkeypatch.setattr(meter_data, "parse_meter_csv", dispatch)
        monkeypatch.setattr(
            meter_data.db, "save_meter_readings", MagicMock(return_value=1)
        )
        monkeypatch.setattr(
            meter_data.db, "get_meter_reading_stats", MagicMock(return_value={})
        )
        monkeypatch.setattr(meter_data.db, "track_event", MagicMock())
        meter_data.ingest_file("b-1", "Datum;Zeit;Bezug\n2026-01-01;00:00;1.0")
        dispatch.assert_called_once()
