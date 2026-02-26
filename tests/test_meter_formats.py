"""TDD tests for multi-format smart meter parsing."""
import pytest
from meter_data import (
    parse_ekz_csv,
    detect_format,
    parse_meter_csv,
    _parse_decimal,
    _parse_timestamp,
    score_meter_profile_usability,
)


class TestFormatDetection:
    """Test auto-detection of CSV format."""

    def test_detect_ekz(self):
        csv = "Zeitstempel;Verbrauch (kWh);Produktion (kWh);Einspeisung (kWh)\n01.01.2026 00:15;0,25;0,00;0,00"
        assert detect_format(csv) == "ekz"

    def test_detect_ewz(self):
        csv = "Timestamp;Consumption (kWh);Production (kWh);Feed-in (kWh)\n2026-01-01 00:15;0.25;0.00;0.00"
        assert detect_format(csv) == "ewz"

    def test_detect_ckw(self):
        csv = "Datum;Zeit;Bezug (kWh);Rücklieferung (kWh)\n01.01.2026;00:15;0,25;0,00"
        assert detect_format(csv) == "ckw"

    def test_detect_bkw(self):
        csv = "Zeitpunkt,Bezug kWh,Erzeugung kWh,Einspeisung kWh\n01.01.2026 00:15,0.25,0.00,0.00"
        assert detect_format(csv) == "bkw"

    def test_detect_generic(self):
        csv = "date,consumption,production\n2026-01-01 00:15,0.25,0.00"
        assert detect_format(csv) == "generic"


class TestEkzFormat:
    """EKZ: semicolon, European decimals, DD.MM.YYYY HH:MM."""

    def test_parse_basic(self):
        csv = "Zeitstempel;Verbrauch (kWh);Produktion (kWh);Einspeisung (kWh)\n01.01.2026 00:15;1,50;0,00;0,00\n01.01.2026 00:30;2,30;0,50;0,10"
        readings, errors = parse_meter_csv(csv)
        assert len(readings) == 2
        assert abs(readings[0][1] - 1.50) < 0.01
        assert abs(readings[1][2] - 0.50) < 0.01


class TestEwzFormat:
    """ewz: semicolon, dot decimals, ISO timestamps."""

    def test_parse_basic(self):
        csv = "Timestamp;Consumption (kWh);Production (kWh);Feed-in (kWh)\n2026-01-01 00:15;0.25;0.00;0.00\n2026-01-01 00:30;0.30;0.10;0.05"
        readings, errors = parse_meter_csv(csv)
        assert len(readings) == 2
        assert abs(readings[0][1] - 0.25) < 0.01


class TestCkwFormat:
    """CKW: semicolon, separate date/time columns, European decimals."""

    def test_parse_basic(self):
        csv = "Datum;Zeit;Bezug (kWh);Rücklieferung (kWh)\n01.01.2026;00:15;0,25;0,10\n01.01.2026;00:30;0,30;0,00"
        readings, errors = parse_meter_csv(csv)
        assert len(readings) == 2
        assert abs(readings[0][1] - 0.25) < 0.01
        assert abs(readings[0][3] - 0.10) < 0.01


class TestBkwFormat:
    """BKW: comma-separated, dot decimals."""

    def test_parse_basic(self):
        csv = "Zeitpunkt,Bezug kWh,Erzeugung kWh,Einspeisung kWh\n01.01.2026 00:15,0.25,0.10,0.05\n01.01.2026 00:30,0.30,0.00,0.00"
        readings, errors = parse_meter_csv(csv)
        assert len(readings) == 2
        assert abs(readings[1][1] - 0.30) < 0.01


class TestEdgeCases:
    """Format-agnostic edge cases."""

    def test_empty_file(self):
        readings, errors = parse_meter_csv("")
        assert len(readings) == 0
        assert len(errors) > 0

    def test_header_only(self):
        csv = "Zeitstempel;Verbrauch (kWh);Produktion (kWh);Einspeisung (kWh)\n"
        readings, errors = parse_meter_csv(csv)
        assert len(readings) == 0

    def test_mixed_empty_rows(self):
        csv = "Zeitstempel;Verbrauch (kWh);Produktion (kWh);Einspeisung (kWh)\n\n01.01.2026 00:15;1,00;0,00;0,00\n\n"
        readings, errors = parse_meter_csv(csv)
        assert len(readings) == 1


# ============================================================
# Fixture-based integration tests
# ============================================================
import os
from meter_data import validate_readings_quality

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), 'fixtures')


def _load_fixture(filename):
    with open(os.path.join(FIXTURES_DIR, filename), 'r') as f:
        return f.read()


class TestEkzFixture:
    """Integration test: EKZ fixture file."""

    def test_parse_fixture(self):
        content = _load_fixture('ekz_sample.csv')
        assert detect_format(content) == 'ekz'
        readings, errors = parse_meter_csv(content)
        assert len(readings) == 16
        assert len(errors) == 0

    def test_15min_intervals(self):
        content = _load_fixture('ekz_sample.csv')
        readings, _ = parse_meter_csv(content)
        # First 4 readings should be 15-min apart
        for i in range(1, 4):
            diff = (readings[i][0] - readings[i-1][0]).total_seconds()
            assert diff == 900

    def test_european_decimals(self):
        content = _load_fixture('ekz_sample.csv')
        readings, _ = parse_meter_csv(content)
        assert abs(readings[0][1] - 0.45) < 0.01

    def test_quality_validation(self):
        content = _load_fixture('ekz_sample.csv')
        readings, _ = parse_meter_csv(content)
        quality = validate_readings_quality(readings)
        assert quality['total_readings'] == 16
        # Has gaps (not continuous 15-min for full day)
        assert any('Datenlücken' in i for i in quality['issues'])


class TestEwzFixture:
    """Integration test: ewz fixture file."""

    def test_parse_fixture(self):
        content = _load_fixture('ewz_sample.csv')
        assert detect_format(content) == 'ewz'
        readings, errors = parse_meter_csv(content)
        assert len(readings) == 12
        assert len(errors) == 0

    def test_dot_decimals(self):
        content = _load_fixture('ewz_sample.csv')
        readings, _ = parse_meter_csv(content)
        assert abs(readings[0][1] - 0.45) < 0.01

    def test_iso_timestamps(self):
        content = _load_fixture('ewz_sample.csv')
        readings, _ = parse_meter_csv(content)
        assert readings[0][0].year == 2026
        assert readings[0][0].month == 1


class TestCkwFixture:
    """Integration test: CKW fixture file."""

    def test_parse_fixture(self):
        content = _load_fixture('ckw_sample.csv')
        assert detect_format(content) == 'ckw'
        readings, errors = parse_meter_csv(content)
        assert len(readings) == 10
        assert len(errors) == 0

    def test_separate_date_time(self):
        content = _load_fixture('ckw_sample.csv')
        readings, _ = parse_meter_csv(content)
        assert readings[0][0].hour == 0
        assert readings[0][0].minute == 0

    def test_feed_in_values(self):
        content = _load_fixture('ckw_sample.csv')
        readings, _ = parse_meter_csv(content)
        # Row with Rücklieferung 2.80
        feed_ins = [r[3] for r in readings if r[3] > 2]
        assert len(feed_ins) >= 1


class TestBkwFixture:
    """Integration test: BKW fixture file."""

    def test_parse_fixture(self):
        content = _load_fixture('bkw_sample.csv')
        assert detect_format(content) == 'bkw'
        readings, errors = parse_meter_csv(content)
        assert len(readings) == 9
        assert len(errors) == 0

    def test_comma_separated(self):
        content = _load_fixture('bkw_sample.csv')
        readings, _ = parse_meter_csv(content)
        assert abs(readings[0][1] - 0.45) < 0.01


class TestGapDetection:
    """Test gap and negative value detection across formats."""

    def test_gap_detection(self):
        content = _load_fixture('ekz_sample.csv')
        readings, _ = parse_meter_csv(content)
        quality = validate_readings_quality(readings)
        assert quality['quality'] in ('fair', 'poor')

    def test_no_negative_values_in_fixtures(self):
        for fname in ['ekz_sample.csv', 'ewz_sample.csv', 'ckw_sample.csv', 'bkw_sample.csv']:
            content = _load_fixture(fname)
            readings, _ = parse_meter_csv(content)
            quality = validate_readings_quality(readings)
            assert not any('negative' in i for i in quality['issues']), f"Negative values in {fname}"

    def test_negative_value_detection(self):
        csv = "Zeitstempel;Verbrauch (kWh);Produktion (kWh);Einspeisung (kWh)\n01.01.2026 00:15;-0,50;0,00;0,00"
        readings, _ = parse_meter_csv(csv)
        quality = validate_readings_quality(readings)
        assert any('negative' in i for i in quality['issues'])


class TestSimulationUsability:
    """Meter profile quality score for hybrid simulation."""

    def test_usable_with_good_coverage(self):
        csv = (
            "Zeitstempel;Verbrauch (kWh);Produktion (kWh);Einspeisung (kWh)\n"
            "01.01.2026 00:00;0,25;0,00;0,00\n"
            "01.01.2026 00:15;0,30;0,00;0,00\n"
            "01.01.2026 00:30;0,22;0,00;0,00\n"
            "01.01.2026 00:45;0,28;0,00;0,00"
        )
        readings, _ = parse_meter_csv(csv)
        score = score_meter_profile_usability(readings, expected_points=4)
        assert score["usable_for_simulation"] is True
        assert score["coverage_ratio"] == 1.0

    def test_not_usable_with_low_coverage(self):
        csv = (
            "Zeitstempel;Verbrauch (kWh);Produktion (kWh);Einspeisung (kWh)\n"
            "01.01.2026 00:00;0,25;0,00;0,00\n"
            "01.01.2026 01:00;0,22;0,00;0,00"
        )
        readings, _ = parse_meter_csv(csv)
        score = score_meter_profile_usability(readings, expected_points=8)
        assert score["usable_for_simulation"] is False
        assert score["coverage_ratio"] < 0.7

    def test_not_usable_with_negative_values(self):
        csv = (
            "Zeitstempel;Verbrauch (kWh);Produktion (kWh);Einspeisung (kWh)\n"
            "01.01.2026 00:00;-0,25;0,00;0,00\n"
            "01.01.2026 00:15;0,30;0,00;0,00\n"
            "01.01.2026 00:30;0,22;0,00;0,00\n"
            "01.01.2026 00:45;0,28;0,00;0,00"
        )
        readings, _ = parse_meter_csv(csv)
        score = score_meter_profile_usability(readings, expected_points=4)
        assert score["usable_for_simulation"] is False
        assert score["negative_count"] > 0
