"""
Smart meter data ingestion for OpenLEG.
Parses EKZ CSV exports, validates readings, stores in database.
"""
import csv
import io
import logging
from datetime import datetime
from typing import List, Tuple, Optional, Dict

import database as db

logger = logging.getLogger(__name__)

# EKZ CSV format: semicolon-separated, European decimals (comma), timestamp format varies
EKZ_EXPECTED_HEADERS = ['Zeitstempel', 'Verbrauch (kWh)', 'Produktion (kWh)', 'Einspeisung (kWh)']
EKZ_ALT_HEADERS = ['Timestamp', 'Consumption (kWh)', 'Production (kWh)', 'Feed-in (kWh)']


def parse_ekz_csv(file_content: str) -> Tuple[List[tuple], List[str]]:
    """Parse EKZ smart meter CSV export.

    Returns:
        (readings, errors) where readings = [(timestamp, consumption, production, feed_in), ...]
    """
    readings = []
    errors = []

    # Try semicolon first (EKZ standard), then comma
    for delimiter in [';', ',', '\t']:
        try:
            reader = csv.reader(io.StringIO(file_content), delimiter=delimiter)
            header = next(reader, None)
            if not header or len(header) < 2:
                continue

            # Normalize headers
            header_clean = [h.strip().lower() for h in header]

            # Detect column mapping
            col_map = _detect_columns(header_clean)
            if not col_map:
                continue

            for i, row in enumerate(reader, start=2):
                if not row or all(c.strip() == '' for c in row):
                    continue
                try:
                    ts = _parse_timestamp(row[col_map['timestamp']].strip())
                    if not ts:
                        errors.append(f"Zeile {i}: Ungültiger Zeitstempel '{row[col_map['timestamp']]}'")
                        continue

                    consumption = _parse_decimal(row[col_map.get('consumption', -1)]) if 'consumption' in col_map else 0
                    production = _parse_decimal(row[col_map.get('production', -1)]) if 'production' in col_map else 0
                    feed_in = _parse_decimal(row[col_map.get('feed_in', -1)]) if 'feed_in' in col_map else 0

                    readings.append((ts, consumption, production, feed_in))
                except (IndexError, ValueError) as e:
                    errors.append(f"Zeile {i}: {str(e)}")

            if readings:
                break  # Found working delimiter
        except Exception as e:
            errors.append(f"Parse-Fehler mit Delimiter '{delimiter}': {str(e)}")

    if not readings and not errors:
        errors.append("Keine Messdaten in der Datei gefunden. Bitte EKZ-CSV-Export verwenden.")

    return readings, errors


def _detect_columns(header: List[str]) -> Optional[Dict[str, int]]:
    """Map header columns to our schema."""
    col_map = {}

    for i, h in enumerate(header):
        h_lower = h.lower().strip()
        if any(kw in h_lower for kw in ['zeit', 'timestamp', 'datum', 'date']):
            col_map['timestamp'] = i
        elif any(kw in h_lower for kw in ['verbrauch', 'consumption', 'bezug']):
            col_map['consumption'] = i
        elif any(kw in h_lower for kw in ['produktion', 'production', 'erzeugung']):
            col_map['production'] = i
        elif any(kw in h_lower for kw in ['einspeisung', 'feed-in', 'feed_in', 'rücklieferung']):
            col_map['feed_in'] = i

    if 'timestamp' not in col_map:
        return None
    if 'consumption' not in col_map and 'production' not in col_map:
        return None

    return col_map


def _parse_timestamp(value: str) -> Optional[datetime]:
    """Parse various timestamp formats from Swiss utility CSVs."""
    formats = [
        '%d.%m.%Y %H:%M',      # 01.01.2026 00:15
        '%d.%m.%Y %H:%M:%S',   # 01.01.2026 00:15:00
        '%Y-%m-%d %H:%M',      # 2026-01-01 00:15
        '%Y-%m-%d %H:%M:%S',   # 2026-01-01 00:15:00
        '%Y-%m-%dT%H:%M:%S',   # ISO format
        '%Y-%m-%dT%H:%M',      # ISO without seconds
        '%d/%m/%Y %H:%M',      # DD/MM/YYYY
    ]
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _parse_decimal(value: str) -> float:
    """Parse European decimal format (comma as decimal separator)."""
    if not value or value.strip() == '' or value.strip() == '-':
        return 0.0
    # Replace comma with dot for European format
    cleaned = value.strip().replace("'", "").replace(' ', '')
    if ',' in cleaned and '.' in cleaned:
        # 1.234,56 format
        cleaned = cleaned.replace('.', '').replace(',', '.')
    elif ',' in cleaned:
        cleaned = cleaned.replace(',', '.')
    return float(cleaned)


def detect_format(file_content: str) -> str:
    """Auto-detect CSV format from header line.

    Returns: ekz, ewz, ckw, bkw, or generic
    """
    first_line = file_content.split('\n')[0].lower().strip() if file_content else ""

    if 'zeitstempel' in first_line and ';' in first_line:
        return "ekz"
    elif 'timestamp' in first_line and ';' in first_line:
        return "ewz"
    elif 'datum' in first_line and 'zeit' in first_line and 'bezug' in first_line:
        return "ckw"
    elif 'zeitpunkt' in first_line and ',' in first_line:
        return "bkw"
    return "generic"


def _parse_ckw_csv(file_content: str) -> Tuple[List[tuple], List[str]]:
    """Parse CKW format with separate Datum and Zeit columns."""
    readings = []
    errors = []
    try:
        reader = csv.reader(io.StringIO(file_content), delimiter=';')
        header = next(reader, None)
        if not header:
            return [], ["Leere Datei"]

        header_clean = [h.strip().lower() for h in header]
        date_col = next((i for i, h in enumerate(header_clean) if 'datum' in h), None)
        time_col = next((i for i, h in enumerate(header_clean) if h == 'zeit'), None)
        bezug_col = next((i for i, h in enumerate(header_clean) if 'bezug' in h), None)
        rueck_col = next((i for i, h in enumerate(header_clean) if 'rücklieferung' in h or 'ruecklieferung' in h or 'einspeisung' in h), None)

        if date_col is None or bezug_col is None:
            return [], ["CKW-Format: Datum oder Bezug Spalte fehlt"]

        for i, row in enumerate(reader, start=2):
            if not row or all(c.strip() == '' for c in row):
                continue
            try:
                date_str = row[date_col].strip()
                time_str = row[time_col].strip() if time_col is not None else "00:00"
                ts = _parse_timestamp(f"{date_str} {time_str}")
                if not ts:
                    errors.append(f"Zeile {i}: Ungültiger Zeitstempel")
                    continue
                consumption = _parse_decimal(row[bezug_col])
                feed_in = _parse_decimal(row[rueck_col]) if rueck_col is not None else 0.0
                readings.append((ts, consumption, 0.0, feed_in))
            except (IndexError, ValueError) as e:
                errors.append(f"Zeile {i}: {str(e)}")
    except Exception as e:
        errors.append(f"CKW Parse-Fehler: {str(e)}")

    return readings, errors


def parse_meter_csv(file_content: str) -> Tuple[List[tuple], List[str]]:
    """Auto-detect format and parse any Swiss utility CSV.

    Returns: (readings, errors)
    """
    if not file_content or not file_content.strip():
        return [], ["Leere Datei"]

    fmt = detect_format(file_content)

    if fmt == "ckw":
        return _parse_ckw_csv(file_content)
    else:
        # ekz, ewz, bkw, generic all handled by the generic parser
        return parse_ekz_csv(file_content)


def ingest_csv(building_id: str, file_content: str, source: str = 'csv') -> Dict:
    """Parse and store meter readings from CSV upload.

    Returns:
        {"success": bool, "readings_count": int, "errors": [...], "stats": {...}}
    """
    readings, errors = parse_ekz_csv(file_content)

    if not readings:
        return {
            "success": False,
            "readings_count": 0,
            "errors": errors or ["Keine gültigen Messdaten gefunden."]
        }

    # Store in database
    stored = db.save_meter_readings(building_id, readings, source=source)

    # Get updated stats
    stats = db.get_meter_reading_stats(building_id)

    result = {
        "success": stored > 0,
        "readings_count": stored,
        "errors": errors,
        "stats": stats
    }

    if stored > 0:
        logger.info(f"[METER] Ingested {stored} readings for building {building_id}")
        db.track_event('meter_data_uploaded', building_id, {
            'readings_count': stored,
            'source': source,
            'error_count': len(errors)
        })

    return result


def validate_readings_quality(readings: List[tuple]) -> Dict:
    """Check data quality: gaps, duplicates, outliers."""
    if not readings:
        return {"quality": "no_data", "issues": []}

    issues = []
    timestamps = sorted([r[0] for r in readings])

    # Check for gaps (expect 15-min intervals)
    gap_count = 0
    for i in range(1, len(timestamps)):
        diff = (timestamps[i] - timestamps[i-1]).total_seconds()
        if diff > 1800:  # > 30 min gap
            gap_count += 1

    if gap_count > 0:
        issues.append(f"{gap_count} Datenlücken erkannt (> 30 Min.)")

    # Check for negative values
    neg_count = sum(1 for r in readings if r[1] < 0 or r[2] < 0 or r[3] < 0)
    if neg_count > 0:
        issues.append(f"{neg_count} negative Messwerte")

    quality = "good" if not issues else ("fair" if len(issues) <= 2 else "poor")

    return {
        "quality": quality,
        "total_readings": len(readings),
        "date_range": f"{timestamps[0]} bis {timestamps[-1]}",
        "issues": issues
    }


def score_meter_profile_usability(
    readings: List[tuple],
    expected_interval_minutes: int = 15,
    expected_points: Optional[int] = None,
) -> Dict:
    """Score whether meter readings are usable for simulation."""
    if not readings:
        return {
            "usable_for_simulation": False,
            "coverage_ratio": 0.0,
            "quality_score": 0.0,
            "gap_count": 0,
            "duplicate_count": 0,
            "negative_count": 0,
            "outlier_count": 0,
            "total_points": 0,
            "expected_points": expected_points or 0,
        }

    interval_seconds = expected_interval_minutes * 60
    timestamps = [r[0] for r in readings]
    unique_timestamps = set(timestamps)
    duplicate_count = len(timestamps) - len(unique_timestamps)

    sorted_ts = sorted(unique_timestamps)
    gap_count = 0
    for i in range(1, len(sorted_ts)):
        diff = (sorted_ts[i] - sorted_ts[i - 1]).total_seconds()
        if diff > interval_seconds * 1.5:
            gap_count += 1

    negative_count = sum(
        1 for r in readings
        if (r[1] or 0) < 0 or (r[2] or 0) < 0 or (r[3] or 0) < 0
    )

    # 20 kWh in 15 minutes is an intentionally strict outlier threshold.
    outlier_threshold_kwh = 20.0
    outlier_count = sum(
        1 for r in readings
        if (r[1] or 0) > outlier_threshold_kwh
        or (r[2] or 0) > outlier_threshold_kwh
        or (r[3] or 0) > outlier_threshold_kwh
    )

    if expected_points is None:
        span_seconds = max((sorted_ts[-1] - sorted_ts[0]).total_seconds(), 0)
        expected_points = int(span_seconds / interval_seconds) + 1 if sorted_ts else 0
    expected_points = max(expected_points, 1)

    total_points = len(unique_timestamps)
    coverage_ratio = min(total_points / expected_points, 1.0)

    quality_score = coverage_ratio
    quality_score -= min(gap_count * 0.01, 0.2)
    quality_score -= min(duplicate_count * 0.005, 0.1)
    quality_score -= min(negative_count * 0.1, 0.4)
    quality_score -= min(outlier_count * 0.05, 0.3)
    quality_score = max(0.0, min(1.0, quality_score))

    critical_issues = negative_count > 0 or outlier_count > 0
    usable_for_simulation = coverage_ratio >= 0.70 and not critical_issues

    return {
        "usable_for_simulation": usable_for_simulation,
        "coverage_ratio": round(coverage_ratio, 4),
        "quality_score": round(quality_score, 4),
        "gap_count": gap_count,
        "duplicate_count": duplicate_count,
        "negative_count": negative_count,
        "outlier_count": outlier_count,
        "total_points": total_points,
        "expected_points": expected_points,
    }
