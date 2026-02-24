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
