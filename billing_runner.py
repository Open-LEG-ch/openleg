# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fail-closed orchestration for one LEG billing period."""

import hashlib
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import billing_engine
import billing_readings
import database as db


class BillingRunError(ValueError):
    """Billing inputs are complete but not safe to persist."""


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
        "tariff_id": policy["tariff_id"],
        "internal_price_chf_per_kwh": str(policy["internal_price_chf_per_kwh"]),
        "grid_fee_chf_per_kwh": str(policy["grid_fee_chf_per_kwh"]),
        "network_level": policy["network_level"],
        "distribution_model": policy["distribution_model"],
        "vat_mode": policy["vat_mode"],
        "vat_rate_pct": str(policy["vat_rate_pct"]),
        "payment_days": policy["payment_days"],
        "invoice_prefix": policy["invoice_prefix"],
        "delivery_method": policy["delivery_method"],
        "vnb_reference": frames.vnb_reference,
        "summary": summary,
        "reconciliation": reconciliation,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def run_billing_period(community_id, period_start, period_end):
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
        )
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
        )
        period_id = db.save_billing_period(
            community_id, period_start, period_end, summary
        )
        return {"status": "created", "period_id": period_id}
    except BillingRunError:
        raise
    except (KeyError, ValueError) as exc:
        raise BillingRunError(str(exc)) from exc
