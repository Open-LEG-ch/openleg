"""TDD tests for multi-format smart meter parsing."""
import pytest
from meter_data import parse_ekz_csv, detect_format, parse_meter_csv, _parse_decimal, _parse_timestamp


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
