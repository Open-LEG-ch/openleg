# SPDX-License-Identifier: AGPL-3.0-or-later
"""Parser für Swiss ebIX E66 Messdaten (ValidatedMeteredData_16).

Ein E66 Dokument liefert pro Messpunkt, Richtung und Produktkanal eine eigene
Zeitreihe. Dieser Parser faltet die drei Produktkanäle eines Paars aus Messpunkt
und Richtung zu einer breiten Zeile zusammen:

    total_kwh      Gesamtenergie (ebIXCode 8716867000030)
    grid_kwh       Netzanteil (VSENationalCode 2404050010124)
    community_kwh  LEG-Anteil (VSENationalCode 2404050010123)

Beobachtungen tragen keinen Zeitstempel. Der Zeitstempel entsteht aus dem
Intervallbeginn des Blocks plus (Sequence - 1) mal Auflösung und bezeichnet
immer den **Beginn** des Intervalls. Alle Zeitstempel sind UTC.

Dieses Modul bleibt bewusst getrennt von `meter_data`: jenes speichert pro
Gebäude, dieses pro Messpunkt.
"""

import logging
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from fractions import Fraction

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import fromstring as _safe_xml_fromstring

logger = logging.getLogger(__name__)

E66_PRODUCT_TOTAL = "8716867000030"
E66_PRODUCT_GRID = "2404050010124"
E66_PRODUCT_COMMUNITY = "2404050010123"

CHANNEL_BY_PRODUCT_CODE = {
    E66_PRODUCT_TOTAL: "total",
    E66_PRODUCT_GRID: "grid",
    E66_PRODUCT_COMMUNITY: "community",
}

DIRECTION_CONSUMPTION = "consumption"
DIRECTION_PRODUCTION = "production"

# Zwei unabhängig auf drei Stellen gerundete Werte weichen von der gerundeten
# Summe um bis zu 0.001 ab. 0.0015 lässt etwas Luft.
E66_BALANCE_TOLERANCE_KWH = Decimal("0.0015")

# Veracity flags (#517): heuristische, konservative Schwellen. Ein falscher
# Alarm muss billig wegzudismissen sein, ein übersehenes Muster ist
# akzeptabel. 32 identische ungleich-Null Intervalle sind acht Stunden
# konstanter Last über Tag und Nacht hinweg: für einen Haushalt unrealistisch.
E66_FLATLINE_INTERVALS = 32
# Ein Sprung ist ein Vielfaches des Medians der ungleich-Null Werte derselben
# Reihe, aber mindestens 20 kWh pro Intervall (80 kW Dauerlast): eine
# Elektroauto-Ladung allein löst keinen Flag aus.
E66_MAGNITUDE_JUMP_FACTOR = 20
E66_MAGNITUDE_JUMP_MIN_KWH = Decimal(20)

MAX_E66_BYTES = 64 * 1024 * 1024

_RESOLUTION_UNIT_MINUTES = {"MIN": 1, "HOUR": 60}

_PARSE_ERRORS = (
    ET.ParseError,
    DefusedXmlException,
    KeyError,
    TypeError,
    ValueError,
    AttributeError,
)


def _local_name(tag) -> str:
    """Elementname ohne Namespace."""
    return str(tag).rsplit("}", 1)[-1]


def _child(element, name):
    if element is None:
        return None
    for candidate in element:
        if _local_name(candidate.tag) == name:
            return candidate
    return None


def _path(element, *names):
    current = element
    for name in names:
        current = _child(current, name)
        if current is None:
            return None
    return current


def _text(element, *names):
    found = _path(element, *names) if names else element
    if found is None or found.text is None:
        return None
    value = found.text.strip()
    return value or None


def _utc(value):
    """ISO 8601 mit Z in ein zeitzonenbewusstes UTC-datetime umwandeln."""
    if not value:
        return None
    # Preserve rejection of Z as a date/time separator, even on Python 3.11.
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))  # noqa: FURB162
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def mask_point_id(metering_point_id) -> str:
    """Messpunkt-ID auf die letzten sechs Zeichen kürzen.

    Messpunkt-IDs sind Personendaten, sobald sie mit dem Register verknüpft
    sind. Ausgaben und Logs zeigen nur das Kürzel.
    """
    if not metering_point_id:
        return ""
    return "..." + str(metering_point_id)[-6:]


def is_e66_document(xml_content) -> bool:
    """Ohne vollständiges Parsen prüfen, ob ein E66 Dokument vorliegt."""
    if not xml_content:
        return False
    head = xml_content[:4096]
    return "ValidatedMeteredData_" in head and "E66" in head


def is_e31_document(xml_content) -> bool:
    """Ohne vollständiges Parsen prüfen, ob ein E31 Dokument vorliegt.

    Der Import überspringt E31, braucht aber den Unterschied zu einer sonst
    unbekannten Datei: "kein E66" heisst nicht "also E31".
    """
    if not xml_content:
        return False
    head = xml_content[:4096]
    return "AggregatedMeteredData_" in head and "E31" in head


# Die Lieferung wird durch die DocumentID im InstanceDocument identifiziert.
# Danach folgt pro Block eine weitere DocumentID ("<id>@1", "@2", ...), im
# Beispiel neun Stück, drei davon noch in den ersten 4096 Zeichen. Ein Griff
# nach der ersten DocumentID im Text trifft heute zufällig richtig, darum wird
# hier erst das InstanceDocument eingegrenzt und dann darin gesucht.
_INSTANCE_DOCUMENT_RE = re.compile(
    r"<(?!/)[^>]*\bInstanceDocument\b[^>]*>(.*?)</[^>]*\bInstanceDocument\b[^>]*>",
    re.DOTALL,
)
_DOCUMENT_ID_RE = re.compile(
    r"<[^>]*\bDocumentID\b[^>]*>([^<]+)</[^>]*\bDocumentID\b[^>]*>"
)


def extract_document_id(xml_content):
    """Die DocumentID der Lieferung aus dem Kopf lesen, ohne zu parsen.

    Nimmt auch einen abgeschnittenen Anfang der Datei. Gibt ``None`` zurück,
    wenn die ID nicht sicher bestimmbar ist; der Aufrufer muss dann die volle
    Arbeit machen und darf die Datei nicht überspringen.
    """
    if not xml_content:
        return None
    instance = _INSTANCE_DOCUMENT_RE.search(xml_content)
    if not instance:
        return None
    match = _DOCUMENT_ID_RE.search(instance.group(1))
    if not match:
        return None
    return match.group(1).strip() or None


def _resolution_minutes(block):
    resolution = _child(block, "Resolution")
    raw = _text(resolution, "Resolution")
    unit = _text(resolution, "Unit") or "MIN"
    if raw is None:
        return None
    factor = _RESOLUTION_UNIT_MINUTES.get(unit.upper())
    if factor is None:
        return None
    return int(raw) * factor


def _block_identity(block):
    consumption = _child(block, "ConsumptionMeteringPoint")
    if consumption is not None:
        return DIRECTION_CONSUMPTION, _text(consumption, "VSENationalID")
    production = _child(block, "ProductionMeteringPoint")
    if production is not None:
        return DIRECTION_PRODUCTION, _text(production, "VSENationalID")
    return None, None


def _channel(block):
    product_id = _path(block, "Product", "ID")
    if product_id is None:
        return None
    code = _text(product_id, "ebIXCode") or _text(product_id, "VSENationalCode")
    return CHANNEL_BY_PRODUCT_CODE.get(code)


class _DuplicateSequence(ValueError):
    """Gleiche Sequenz zweimal innerhalb eines Kanals."""

    def __init__(self, sequence):
        self.sequence = sequence
        super().__init__(sequence)


def _observations(block):
    """Beobachtungen als {sequence: (volume, condition)}.

    Wirft :class:`_DuplicateSequence`, sobald eine Sequenz im selben Block
    doppelt vorkommt. Doppelte Sequenzen sind defekte Daten: die letzte
    Beobachtung still gewinnen zu lassen würde eine der beiden Messungen
    verwerfen, ohne dass jemand davon erfährt.
    """
    values = {}
    for observation in block:
        if _local_name(observation.tag) != "Observation":
            continue
        sequence = _text(observation, "Position", "Sequence")
        volume = _text(observation, "Volume")
        if sequence is None or volume is None:
            continue
        sequence = int(sequence)
        if sequence in values:
            raise _DuplicateSequence(sequence)
        values[sequence] = (Decimal(volume), _text(observation, "Condition"))
    return values


def exact_e66_kwh(value):
    """Validate the 0.001 kWh grid without ambient Decimal rounding.

    The NUMERIC(12,4) reading columns permit eight integer digits. Bound
    magnitude before building a Fraction, including scientific notation.
    """
    number = Decimal(str(value))
    if not number.is_finite() or number < 0:
        raise ValueError("invalid_e66_precision")
    if number >= Decimal(100000000):
        raise ValueError("e66_value_out_of_range")
    if number.is_zero():
        return Fraction(0)
    sign, digits, exponent = number.as_tuple()
    last = len(digits)
    while last and digits[last - 1] == 0:
        last -= 1
    exponent += len(digits) - last
    if exponent < -3:
        raise ValueError("invalid_e66_precision")
    return Fraction(Decimal((sign, digits[:last], exponent)))


def _unique_paths(element, paths):
    """The evidence profile cannot choose between repeated identity fields."""
    for path in paths:
        node = element
        for name in path:
            children = [child for child in node if _local_name(child.tag) == name]
            if len(children) != 1:
                return False
            node = children[0]
    return True


def parse_e66_xml(xml_content, *, include_evidence_metadata=False):
    """E66 Dokument einlesen.

    Returns:
        (document, errors). `document` ist bei einem harten Fehler leer.
        Weiche Probleme stehen in `document["warnings"]`.
    """
    if not xml_content or not xml_content.strip():
        return {}, ["SDAT E66: Leere Datei."]

    if len(xml_content.encode("utf-8")) > MAX_E66_BYTES:
        limit_mb = MAX_E66_BYTES // (1024 * 1024)
        return {}, [f"SDAT E66: Datei zu gross (max. {limit_mb} MB)."]

    try:
        root = _safe_xml_fromstring(xml_content)
    except _PARSE_ERRORS:
        return {}, ["SDAT E66: Ungültiges Dateiformat."]

    try:
        return _parse_document(
            root, include_evidence_metadata=include_evidence_metadata
        )
    except _PARSE_ERRORS + (InvalidOperation,):
        logger.warning("[SDAT] E66 konnte nicht gelesen werden")
        return {}, ["SDAT E66: Ungültiges Dateiformat."]


def _parse_document(root, *, include_evidence_metadata=False):
    header = None
    for child in root:
        if _local_name(child.tag).endswith("HeaderInformation"):
            header = child
            break

    doc_type = _text(header, "InstanceDocument", "DocumentType", "ebIXCode")
    if doc_type != "E66":
        return {}, ["SDAT E66: Keine Messpunkt-Daten gefunden."]

    period = _path(header, "BusinessScopeProcess", "ReportPeriod")
    document = {
        "document_id": _text(header, "InstanceDocument", "DocumentID"),
        "doc_type": doc_type,
        "sender_id": _text(header, "Sender", "ID", "EICID"),
        "receiver_id": _text(header, "Receiver", "ID", "EICID"),
        "document_created_at": _utc(_text(header, "InstanceDocument", "Creation")),
        "period_start": _utc(_text(period, "StartDateTime")),
        "period_end": _utc(_text(period, "EndDateTime")),
        "vnb_community_id": None,
        "block_count": 0,
        "point_ids": [],
        "rows": [],
        "warnings": [],
        "veracity_flags": [],
    }
    if include_evidence_metadata:
        document["evidence_metadata"] = {
            "unambiguous_header": sum(
                _local_name(child.tag).endswith("HeaderInformation") for child in root
            )
            == 1
            and _unique_paths(
                header,
                (
                    ("InstanceDocument", "DocumentID"),
                    ("InstanceDocument", "DocumentType", "ebIXCode"),
                    ("InstanceDocument", "Status"),
                    ("InstanceDocument", "Creation"),
                    ("Sender", "ID", "EICID"),
                    ("Receiver", "ID", "EICID"),
                    ("BusinessScopeProcess", "ReportPeriod", "StartDateTime"),
                    ("BusinessScopeProcess", "ReportPeriod", "EndDateTime"),
                ),
            ),
            "document_status": _text(header, "InstanceDocument", "Status"),
            "created_at": _text(header, "InstanceDocument", "Creation"),
            "period_start": _text(period, "StartDateTime"),
            "period_end": _text(period, "EndDateTime"),
            "blocks": [],
        }

    groups, warnings, hard_error = _collect_groups(root, document)
    if hard_error:
        return {}, [hard_error]
    if not groups:
        return {}, ["SDAT E66: Keine Messpunkt-Daten gefunden."]

    document["warnings"] = warnings
    document["point_ids"] = sorted({point for point, _ in groups})
    document["rows"] = _build_rows(groups, warnings)
    _warn_on_balance(document["rows"], warnings)
    _warn_on_interval_count(document, groups, warnings)
    _flag_series_anomalies(document, document["rows"])
    return document, []


def _collect_groups(root, document):
    """Blöcke nach (Messpunkt, Richtung) gruppieren."""
    groups = {}
    warnings = []
    unit_error = None

    for block in root:
        if _local_name(block.tag) != "MeteringData":
            continue
        document["block_count"] += 1

        direction, point_id = _block_identity(block)
        if "evidence_metadata" in document:
            product = _path(block, "Product", "ID")
            document["evidence_metadata"]["blocks"].append(
                {
                    "metering_point_id": point_id,
                    "unambiguous_block": sum(
                        _local_name(child.tag)
                        in {"ConsumptionMeteringPoint", "ProductionMeteringPoint"}
                        for child in block
                    )
                    == 1
                    and _unique_paths(
                        block,
                        (
                            ("Community", "CommunityID"),
                            (
                                "ConsumptionMeteringPoint"
                                if direction == DIRECTION_CONSUMPTION
                                else "ProductionMeteringPoint",
                                "VSENationalID",
                            ),
                            ("Product", "ID"),
                            ("Product", "MeasureUnit"),
                            ("Interval", "StartDateTime"),
                            ("Interval", "EndDateTime"),
                            ("Resolution", "Resolution"),
                            ("Resolution", "Unit"),
                        ),
                    )
                    and product is not None
                    and sum(
                        _local_name(child.tag) in {"ebIXCode", "VSENationalCode"}
                        for child in product
                    )
                    == 1,
                    "direction": direction,
                    "community_id": _text(block, "Community", "CommunityID"),
                    "unit": _text(block, "Product", "MeasureUnit"),
                    "product_code": _text(product, "ebIXCode")
                    or _text(product, "VSENationalCode"),
                    "channel": _channel(block),
                    "start": _text(block, "Interval", "StartDateTime"),
                    "end": _text(block, "Interval", "EndDateTime"),
                    "resolution": _resolution_minutes(block),
                    "observations": [
                        {
                            "sequence": _text(obs, "Position", "Sequence"),
                            "value": _text(obs, "Volume"),
                            "condition": _text(obs, "Condition"),
                            "unambiguous": _unique_paths(
                                obs, (("Position", "Sequence"), ("Volume",))
                            )
                            and sum(
                                _local_name(child.tag) == "Condition" for child in obs
                            )
                            <= 1,
                        }
                        for obs in block
                        if _local_name(obs.tag) == "Observation"
                    ],
                }
            )
        if not point_id:
            warnings.append("Block ohne Messpunkt übersprungen.")
            continue

        unit = _text(block, "Product", "MeasureUnit")
        if unit and unit.upper() != "KWH":
            # Ein Feed in Wh würde jede Abrechnung um Faktor 1000 verfälschen.
            unit_error = f"SDAT E66: Unerwartete Masseinheit {unit}."
            break

        channel = _channel(block)
        if channel is None:
            warnings.append(
                f"Messpunkt {mask_point_id(point_id)}: unbekannter Produktcode "
                "übersprungen."
            )
            continue

        resolution = _resolution_minutes(block)
        interval_start = _utc(_text(block, "Interval", "StartDateTime"))
        if resolution is None or interval_start is None:
            warnings.append(
                f"Messpunkt {mask_point_id(point_id)}: Intervall oder Auflösung "
                "fehlt, Block übersprungen."
            )
            continue

        community = _text(block, "Community", "CommunityID")
        if community and not document["vnb_community_id"]:
            document["vnb_community_id"] = community

        key = (point_id, direction)
        existing = groups.get(key)
        if existing and (
            existing["start"] != interval_start or existing["resolution"] != resolution
        ):
            return (
                groups,
                warnings,
                f"SDAT E66: Messpunkt {mask_point_id(point_id)} hat widersprüchliche Intervalle.",
            )

        group = groups.setdefault(
            key,
            {"start": interval_start, "resolution": resolution, "channels": {}},
        )
        try:
            channel_observations = _observations(block)
        except _DuplicateSequence as duplicate:
            detail = (
                f"SDAT E66: Messpunkt {mask_point_id(point_id)} ({direction}), "
                f"Kanal {channel}: doppelte Sequenz {duplicate.sequence}."
            )
            return groups, warnings, detail
        previous = group["channels"].get(channel)
        if previous is not None:
            # Zwei Blöcke beanspruchen dasselbe Fenster. Die Daten bleiben
            # wie bisher (letzter Block gewinnt), aber ein Widerspruch wird
            # sichtbar statt still verworfen (#517). Identische Wiederholung
            # ist eine harmlose erneute Lieferung und bleibt unbeflaggt.
            conflicts = sorted(
                sequence
                for sequence, value in channel_observations.items()
                if sequence in previous and previous[sequence][0] != value[0]
            )
            if conflicts:
                document.setdefault("veracity_flags", []).append(
                    {
                        "kind": "duplicate_window",
                        "metering_point_id": point_id,
                        "direction": direction,
                        "window_start": interval_start,
                        "window_end": None,
                        "detail": (
                            f"Kanal {channel}: zweiter Block widerspricht in "
                            f"{len(conflicts)} Sequenzen."
                        ),
                    }
                )
        group["channels"][channel] = channel_observations

    return groups, warnings, unit_error


def _build_rows(groups, warnings):
    rows = []
    for (point_id, direction), group in sorted(groups.items()):
        channels = group["channels"]
        for name in ("total", "grid", "community"):
            if name not in channels:
                warnings.append(
                    f"Messpunkt {mask_point_id(point_id)} ({direction}): "
                    f"Kanal {name} fehlt."
                )

        sequences = sorted({seq for values in channels.values() for seq in values})
        for sequence in sequences:
            measured_at = group["start"] + timedelta(
                minutes=(sequence - 1) * group["resolution"]
            )
            volumes = {}
            condition = None
            for name in ("total", "grid", "community"):
                entry = channels.get(name, {}).get(sequence)
                volumes[name] = entry[0] if entry else None
                if condition is None and entry and entry[1]:
                    condition = entry[1]

            rows.append(
                {
                    "metering_point_id": point_id,
                    "direction": direction,
                    "measured_at": measured_at,
                    "resolution_minutes": group["resolution"],
                    "total_kwh": volumes["total"],
                    "grid_kwh": volumes["grid"],
                    "community_kwh": volumes["community"],
                    "condition_code": condition,
                }
            )
    return rows


def _warn_on_balance(rows, warnings):
    """Prüfen, ob total dem Anteil aus Netz und LEG entspricht."""
    deviations = []
    for row in rows:
        total, grid, community = (
            row["total_kwh"],
            row["grid_kwh"],
            row["community_kwh"],
        )
        if total is None or grid is None or community is None:
            continue
        deviation = abs(total - (grid + community))
        if deviation > E66_BALANCE_TOLERANCE_KWH:
            deviations.append((deviation, row))

    if not deviations:
        return

    worst = max(deviation for deviation, _ in deviations)
    examples = ", ".join(
        f"{mask_point_id(row['metering_point_id'])} {row['direction']} "
        f"{row['measured_at']:%Y-%m-%d %H:%MZ}"
        for _, row in deviations[:3]
    )
    warnings.append(
        f"{len(deviations)} Intervalle mit Kanalsumme-Abweichung "
        f"(max. {worst} kWh): {examples}"
    )


def _flag_series_anomalies(document, rows):
    """Flatline und Grössenordnungs-Sprünge als Veracity-Flags vermerken.

    Heuristisch und konservativ: ein Flag verwirft nichts und korrigiert
    nichts, es macht ein unplausibel aussehendes Fenster nur sichtbar (#517).
    """
    series = {}
    for row in rows:
        series.setdefault((row["metering_point_id"], row["direction"]), []).append(row)

    for (point_id, direction), series_rows in sorted(series.items()):
        _flag_flatline(document, point_id, direction, series_rows)
        _flag_magnitude_jumps(document, point_id, direction, series_rows)


def _flag_flatline(document, point_id, direction, series_rows):
    """Identische ungleich-Null Werte über die Schwellenzahl Intervalle."""
    run_value, run_start, run_length = None, 0, 0
    for index, row in enumerate(series_rows):
        value = row["total_kwh"]
        if value is not None and value == run_value and value != 0:
            run_length += 1
        else:
            run_value, run_start, run_length = value, index, 1
        if (
            run_value is not None
            and run_value != 0
            and run_length == E66_FLATLINE_INTERVALS
        ):
            document.setdefault("veracity_flags", []).append(
                {
                    "kind": "flatline",
                    "metering_point_id": point_id,
                    "direction": direction,
                    "window_start": series_rows[run_start]["measured_at"],
                    "window_end": row["measured_at"],
                    "detail": (
                        f"{run_length} Intervalle mit identischem Wert {run_value} kWh."
                    ),
                }
            )


def _flag_magnitude_jumps(document, point_id, direction, series_rows):
    """Ein Vielfaches des Reihenmedians, aber mindestens die Absolutschwelle."""
    nonzero = sorted(
        row["total_kwh"]
        for row in series_rows
        if row["total_kwh"] is not None and row["total_kwh"] != 0
    )
    if not nonzero:
        return
    median = nonzero[len(nonzero) // 2]
    threshold = max(E66_MAGNITUDE_JUMP_MIN_KWH, E66_MAGNITUDE_JUMP_FACTOR * median)
    for row in series_rows:
        value = row["total_kwh"]
        if value is not None and value > threshold:
            document.setdefault("veracity_flags", []).append(
                {
                    "kind": "magnitude_jump",
                    "metering_point_id": point_id,
                    "direction": direction,
                    "window_start": row["measured_at"],
                    "window_end": None,
                    "detail": f"{value} kWh gegen Median {median} kWh der Reihe.",
                }
            )


def _warn_on_interval_count(document, groups, warnings):
    """Erwartete Intervallzahl aus dem Berichtszeitraum ableiten.

    Nicht fest 480 annehmen: der Zeitraum ist an lokale Mitternacht verankert,
    ein Fenster über den Zeitumstellungssonntag hat 476 oder 484 Intervalle.
    """
    start, end = document["period_start"], document["period_end"]
    if not start or not end:
        return
    for (point_id, direction), group in groups.items():
        minutes = (end - start).total_seconds() / 60
        expected = int(minutes // group["resolution"])
        actual = len({seq for values in group["channels"].values() for seq in values})
        if expected and actual != expected:
            warnings.append(
                f"Messpunkt {mask_point_id(point_id)} ({direction}): "
                f"{actual} Intervalle statt {expected}."
            )


def parse_e66_file(path):
    """E66 Datei von der Platte lesen und einlesen."""
    try:
        with open(path, encoding="utf-8") as handle:
            return parse_e66_xml(handle.read())
    except OSError:
        return {}, [f"SDAT E66: Datei nicht lesbar: {path}"]
