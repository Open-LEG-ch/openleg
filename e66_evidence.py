# SPDX-License-Identifier: AGPL-3.0-or-later
"""Strict E66 source adapter for the existing calculated-values contract."""

import hashlib
from datetime import UTC, datetime, timedelta
from fractions import Fraction

import sdat_e66
import vnb_calculated_values


class E66EvidenceError(ValueError):
    """A fixed diagnostic code suitable for the private operator endpoint."""


def _moment(value):
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError
    return moment.astimezone(UTC)


def parse_source(evidence):
    """Reject incomplete or uncertain sources before any database writes."""
    try:
        document, errors = sdat_e66.parse_e66_xml(
            evidence.decode("utf-8"), include_evidence_metadata=True
        )
        if errors:
            raise E66EvidenceError("invalid_e66")
        metadata = document["evidence_metadata"]
        if not metadata["unambiguous_header"]:
            raise E66EvidenceError("ambiguous_e66_identity")
        if metadata["document_status"] not in {"9", "5"}:
            raise E66EvidenceError("e66_function_requires_operator_review")
        if (
            not all(
                isinstance(document[key], str) and document[key].strip()
                for key in ("document_id", "sender_id", "receiver_id")
            )
            or len(document["document_id"]) > 64
        ):
            raise E66EvidenceError("missing_e66_provenance")
        _moment(metadata["created_at"])
        start, end = (_moment(metadata[key]) for key in ("period_start", "period_end"))
        if end <= start or any(
            moment.minute % 15 or moment.second or moment.microsecond
            for moment in (start, end)
        ):
            raise E66EvidenceError("invalid_e66_period")
        count = int((end - start) / timedelta(minutes=15))
        series = {}
        external_communities = set()
        for block in metadata["blocks"]:
            external_communities.add(block["community_id"])
            if (
                not block["unambiguous_block"]
                or not block["metering_point_id"]
                or block["direction"] not in {"consumption", "production"}
                or block["channel"] not in {"total", "community", "grid"}
                or (block["unit"] or "").upper() != "KWH"
                or block["resolution"] != 15
                or _moment(block["start"]) != start
                or _moment(block["end"]) != end
            ):
                raise E66EvidenceError("incomplete_e66_channels")
            channels = series.setdefault(
                (block["metering_point_id"], block["direction"]), set()
            )
            if block["channel"] in channels:
                raise E66EvidenceError("duplicate_e66_channel")
            channels.add(block["channel"])
            observations = block["observations"]
            if len(observations) != count or {
                int(obs["sequence"]) for obs in observations
            } != set(range(1, count + 1)):
                raise E66EvidenceError("incomplete_e66_period")
            for observation in observations:
                if not observation["unambiguous"]:
                    raise E66EvidenceError("ambiguous_e66_observation")
                # Coded quality needs a separately reviewed source profile.
                if observation["condition"] is not None:
                    raise E66EvidenceError("unsupported_e66_quality")
                try:
                    sdat_e66.exact_e66_kwh(observation["value"])
                except ValueError as error:
                    raise E66EvidenceError(str(error)) from None
        if (
            not series
            or external_communities == {None}
            or len(external_communities) != 1
            or not next(iter(external_communities))
            or any(
                channels != {"total", "community", "grid"}
                for channels in series.values()
            )
        ):
            raise E66EvidenceError("incomplete_e66_channels")
        for row in document["rows"]:
            if abs(
                sdat_e66.exact_e66_kwh(row["total_kwh"])
                - sdat_e66.exact_e66_kwh(row["grid_kwh"])
                - sdat_e66.exact_e66_kwh(row["community_kwh"])
            ) > Fraction(1, 1000):
                raise E66EvidenceError("unbalanced_e66_channels")
        document["evidence_sha256"] = hashlib.sha256(evidence).hexdigest()
        return document
    except E66EvidenceError:
        raise
    except (KeyError, TypeError, ValueError, ArithmeticError):
        raise E66EvidenceError("invalid_e66") from None


def build_delivery(territory, document, mappings):
    """Freeze the explicitly dated registry bindings with the source identity."""
    start, end = document["period_start"], document["period_end"]
    by_point = {row["metering_point_id"]: row for row in mappings}
    if not mappings or set(document["point_ids"]) != set(by_point):
        raise E66EvidenceError("e66_mapping_unavailable")
    community_ids = {row["community_id"] for row in mappings}
    if len(community_ids) != 1:
        raise E66EvidenceError("ambiguous_e66_community")
    for mapping in mappings:
        if (
            mapping["vnb_community_id"] != document["vnb_community_id"]
            or mapping["assignment_effective_from"] is None
            or mapping["assignment_effective_from"] > start
            or (
                mapping["assignment_effective_until"] is not None
                and end > mapping["assignment_effective_until"]
            )
        ):
            raise E66EvidenceError("e66_mapping_outside_validity")
    records = []
    for row in document["rows"]:
        mapping = by_point[row["metering_point_id"]]
        if row["direction"] not in (mapping["expected_directions"] or []):
            raise E66EvidenceError("e66_mapping_direction_mismatch")
        records.append(
            {
                "participant_id": mapping["building_id"],
                "direction": row["direction"],
                "measured_at": row["measured_at"].isoformat(),
                "value": str(row["community_kwh"]),
                "source_metering_point_id": row["metering_point_id"],
                "source_quantities": {
                    key: str(row[key])
                    for key in ("total_kwh", "grid_kwh", "community_kwh")
                },
            }
        )
    metadata = {
        "document_status": document["evidence_metadata"]["document_status"],
        "evidence_sha256": document["evidence_sha256"],
        "sender_id": document["sender_id"],
        "receiver_id": document["receiver_id"],
        "document_created_at": document["document_created_at"].isoformat(),
        "vnb_community_id": document["vnb_community_id"],
        "precision": "0.001 kWh",
        "quality_policy": "no Condition code",
        "channel_balance_policy": "absolute difference <= 0.001 kWh per interval",
        "products": {
            block["channel"]: block["product_code"]
            for block in document["evidence_metadata"]["blocks"]
        },
        "mappings": [
            {
                key: value.isoformat() if isinstance(value, datetime) else value
                for key, value in mapping.items()
            }
            for mapping in sorted(mappings, key=lambda item: item["metering_point_id"])
        ],
    }
    return {
        "contract_version": vnb_calculated_values.CONTRACT_VERSION,
        "format_version": "e66/1",
        "territory": territory,
        "community_id": next(iter(community_ids)),
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "timezone": "Europe/Zurich",
        "source": document["sender_id"],
        "vnb_case_id": document["document_id"],
        "unit": "kWh",
        "records": records,
    }, metadata
