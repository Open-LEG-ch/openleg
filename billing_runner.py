# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fail-closed orchestration for one LEG billing period."""

import hashlib
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import billing_engine
import billing_policy
import billing_readings
import database as db
import quartierakku


class BillingRunError(ValueError):
    """Billing inputs are complete but not safe to persist."""


def _battery_lines(community_id, participants, period_start, period_end):
    """Return (battery_snapshot, cost_share_lines) for the billed participants.

    No battery in the community bills nothing. A battery whose config is
    incomplete, or that does not cover every billed participant, fails
    closed: the cost share must be auditable for every participant.
    """
    battery = db.get_battery(community_id)
    if not battery:
        return None, []
    try:
        battery = quartierakku.validate_battery_config(
            {**battery, "community_id": community_id}
        )
    except quartierakku.InvalidBatteryConfig as exc:
        raise BillingRunError(str(exc)) from exc
    lines = []
    for participant_id in participants:
        share = battery["shares"].get(participant_id)
        if share is None:
            raise BillingRunError(
                "Der Quartierakku ist unvollständig konfiguriert: Teilnehmer "
                f"{participant_id} hat keinen Anteil."
            )
        lines.append(
            {
                "participant_id": participant_id,
                "item_type": "battery_cost_share",
                "quantity_kwh": None,
                "unit_price_chf_per_kwh": None,
                "amount_chf": float(
                    quartierakku.period_cost_share_chf(
                        battery["annual_cost_chf"],
                        share,
                        period_start,
                        period_end,
                    )
                ),
            }
        )
    # The snapshot is fingerprinted and JSONB-frozen: decimals travel as
    # exact strings so a re-run hashes identically.
    snapshot = {
        "community_id": community_id,
        "capacity_kwh": str(battery["capacity_kwh"]),
        "annual_cost_chf": str(battery["annual_cost_chf"]),
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "period_fraction": str(quartierakku.period_fraction(period_start, period_end)),
        "shares": {
            participant: str(share) for participant, share in battery["shares"].items()
        },
    }
    return snapshot, lines


def previous_complete_month(now=None):
    """Return the previous local calendar month as a half-open interval."""
    timezone = ZoneInfo(billing_readings.DEFAULT_TIMEZONE)
    now = now.astimezone(timezone) if now else datetime.now(timezone)
    period_end = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    period_start = (period_end - timedelta(days=1)).replace(day=1)
    return period_start, period_end


def _canonical_frame(frame):
    """JSON-safe deterministic content: index, participant columns, values."""
    return {
        "index": [moment.isoformat() for moment in frame.index],
        "columns": [str(column) for column in frame.columns],
        "values": [[float(value) for value in row] for row in frame.to_numpy()],
    }


def _fingerprint(frames, policy, summary, reconciliation):
    provenance = frames.provenance
    payload = {
        "community_id": policy["community_id"],
        "period_start": provenance["period_start"].isoformat(),
        "period_end": provenance["period_end"].isoformat(),
        "source_document_ids": list(provenance["source_document_ids"]),
        "interval_count": provenance["interval_count"],
        "resolution_minutes": provenance["resolution_minutes"],
        "timezone": provenance["timezone"],
        "production": _canonical_frame(frames.production),
        "consumption": _canonical_frame(frames.consumption),
        "participants": list(frames.participants),
        "vnb_reference": frames.vnb_reference,
        "summary": summary,
        "reconciliation": reconciliation,
    }
    payload.update(billing_policy.policy_fingerprint_values(policy))
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def run_billing_period(
    community_id, period_start, period_end, *, prepared_by: str | None = None
):
    """Validate, reconcile and persist one immutable draft billing period."""
    try:
        policy = db.get_billing_policy(community_id, period_start, period_end)
        if not policy:
            raise BillingRunError("No effective billing tariff configured")
        policy = {**policy, "community_id": community_id}

        frames = billing_readings.load_period_frames(
            community_id, period_start, period_end
        )
        if not frames.provenance["source_document_ids"]:
            raise BillingRunError("Billing readings have no import provenance")
        summary = billing_engine.generate_billing_summary(
            frames.production,
            frames.consumption,
            grid_fee_per_kwh=policy["grid_fee_chf_per_kwh"],
            internal_price_per_kwh=policy["internal_price_chf_per_kwh"],
            network_level=policy["network_level"],
            distribution_model=policy["distribution_model"],
            settlement_fee_per_kwh=policy["settlement_fee_chf_per_kwh"],
        )
        battery_snapshot, battery_lines = _battery_lines(
            community_id, list(frames.participants), period_start, period_end
        )
        if battery_lines:
            summary["line_items"].extend(battery_lines)
            summary["battery_snapshot"] = battery_snapshot
        reconciliation = billing_readings.reconcile_with_vnb(frames, summary)
        participant_gaps = reconciliation["per_participant"].values()
        production_gaps = reconciliation["production_per_participant"].values()
        if (
            reconciliation["difference_kwh"] != 0
            or reconciliation["production_difference_kwh"] != 0
            or any(item["difference_kwh"] != 0 for item in participant_gaps)
            or any(item["difference_kwh"] != 0 for item in production_gaps)
        ):
            raise BillingRunError(
                "OpenLEG allocation does not match the VNB allocation"
            )

        fingerprint = _fingerprint(frames, policy, summary, reconciliation)
        existing = db.get_billing_period_for_window(
            community_id, period_start, period_end
        )
        if existing:
            if existing["input_fingerprint"] != fingerprint:
                raise BillingRunError("Billing period inputs changed after processing")
            return {"status": "already_processed", "period_id": existing["id"]}

        summary.update(
            input_fingerprint=fingerprint,
            source_document_ids=list(frames.provenance["source_document_ids"]),
            reconciliation=reconciliation,
            timezone=frames.provenance["timezone"],
            # Freeze the complete effective policy so approval never
            # reconstructs historic choices from mutable tariff tables.
            billing_policy_snapshot=dict(policy),
            prepared_by=prepared_by or "system",
        )
        period_id = db.save_billing_period(
            community_id, period_start, period_end, summary
        )
        return {"status": "created", "period_id": period_id}
    except BillingRunError:
        raise
    except (KeyError, ValueError) as exc:
        raise BillingRunError(str(exc)) from exc
