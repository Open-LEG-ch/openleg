# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fail-closed orchestration for one LEG billing period."""

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from psycopg2 import Error as DatabaseError

import billing_engine
import billing_policy
import billing_readings
import billing_reconciliation
import database as db
import quartierakku


class BillingRunError(ValueError):
    """A billing run cannot safely persist a draft."""

    _PUBLIC_CODES = frozenset(
        {
            "billing_run_failed",
            "billing_tariff_missing",
            "vnb_evidence_missing",
            "metering_readings_invalid",
            "vnb_allocation_mismatch",
            "billing_inputs_changed",
            "battery_configuration_invalid",
            "billing_store_unavailable",
            "billing_provenance_missing",
        }
    )

    _REASONS = frozenset({"vnb_balancing_policy_required"})

    def __init__(self, message, *, code="billing_run_failed", reason=None):
        super().__init__(message)
        self.code = code if code in self._PUBLIC_CODES else "billing_run_failed"
        self.reason = (
            reason
            if reason in self._REASONS and self.code == "vnb_allocation_mismatch"
            else None
        )


def _battery_lines(community_id, participants, period_start, period_end):
    """Return (battery_snapshot, cost_share_lines) for the billed participants.

    No battery in the community bills nothing. A battery whose config is
    incomplete, or that does not cover every billed participant, fails
    closed: the cost share must be auditable for every participant.
    """
    battery = db.get_battery(community_id)
    if not battery:
        return None, None, []
    try:
        battery = quartierakku.validate_battery_config(
            {**battery, "community_id": community_id}
        )
    except quartierakku.InvalidBatteryConfig as exc:
        raise BillingRunError(str(exc), code="battery_configuration_invalid") from exc
    lines = []
    battery_participant_id = battery["participant_id"]
    for participant_id in participants:
        if participant_id == battery_participant_id:
            continue
        share = battery["shares"].get(participant_id)
        if share is None:
            raise BillingRunError(
                "Der Quartierakku ist unvollständig konfiguriert: Teilnehmer "
                f"{participant_id} hat keinen Anteil.",
                code="battery_configuration_invalid",
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
        "participant_id": battery_participant_id,
        "capacity_kwh": str(battery["capacity_kwh"]),
        "annual_cost_chf": str(battery["annual_cost_chf"]),
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "period_fraction": str(quartierakku.period_fraction(period_start, period_end)),
        "shares": {
            participant: str(share) for participant, share in battery["shares"].items()
        },
    }
    return battery, snapshot, lines


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
    if provenance.get("calculated_values_fingerprint"):
        payload.update(
            calculated_values_fingerprint=provenance["calculated_values_fingerprint"],
            vnb_case_id=provenance["vnb_case_id"],
            vnb_source=provenance["vnb_source"],
        )
    payload.update(billing_policy.policy_fingerprint_values(policy))
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _main_draft_fingerprint(
    frames, policy, summary, reconciliation, *, precision_basis, battery_kwargs
):
    """Reproduce main 43f8826's payload only for a mismatching existing draft.

    Current evidence and policy gates run first. This projection never prices
    or saves a new draft, and still hashes every original input.
    """
    reference = {
        key: frames.vnb_reference[key]
        for key in (
            "community_consumption_kwh",
            "community_production_kwh",
            "per_participant",
        )
    }
    legacy_frames = replace(
        frames,
        vnb_reference={
            **frames.vnb_reference,
            "billed": {
                direction + "_kwh": {
                    participant: values[direction + "_kwh"]
                    for participant, values in reference["per_participant"].items()
                }
                for direction in ("consumption", "production")
            },
        },
    )
    independent = billing_engine.generate_billing_summary(
        frames.production,
        frames.consumption,
        grid_fee_per_kwh=policy["grid_fee_chf_per_kwh"],
        internal_price_per_kwh=policy["internal_price_chf_per_kwh"],
        network_level=policy["network_level"],
        distribution_model=policy["distribution_model"],
        settlement_fee_per_kwh=policy["settlement_fee_chf_per_kwh"],
        **battery_kwargs,
    )
    audit = billing_readings.reconcile_with_vnb(legacy_frames, independent)
    for key in (
        "engine_audit",
        "vnb_delivered_production_kwh",
        "vnb_imbalance_kwh",
        "vnb_imbalance_ratio",
    ):
        audit.pop(key, None)
    if precision_basis:
        # Main required exact source balance, before tolerance releases existed.
        if frames.vnb_reference["imbalance_kwh"] != 0:
            return None
        audit["precision_policy"] = reconciliation["precision_policy"]
    elif (
        any(
            item["difference_kwh"] != 0
            for key in ("per_participant", "production_per_participant")
            for item in audit[key].values()
        )
        or audit["difference_kwh"] != 0
        or audit["production_difference_kwh"] != 0
    ):
        return None
    legacy_summary = dict(summary if precision_basis else independent)
    legacy_summary.pop("engine_audit", None)
    if "battery_snapshot" in summary:
        legacy_summary["battery_snapshot"] = summary["battery_snapshot"]
        legacy_summary["line_items"] = [
            *legacy_summary["line_items"],
            *(
                item
                for item in summary["line_items"]
                if item["item_type"] == "battery_cost_share"
            ),
        ]
    return _fingerprint(
        replace(frames, vnb_reference=reference), policy, legacy_summary, audit
    )


def run_billing_period(
    community_id, period_start, period_end, *, prepared_by: str | None = None
):
    """Validate, reconcile and persist one immutable draft billing period."""
    # A run started from stored bounds gets them in UTC. Express them in the
    # period timezone up front, so everything derived from the bounds (battery
    # snapshot, prorated cost share, fingerprint) matches the cron's run.
    zone = ZoneInfo(billing_readings.DEFAULT_TIMEZONE)
    period_start, period_end = (
        moment.astimezone(zone)
        if moment.tzinfo is not None
        else moment.replace(tzinfo=zone)
        for moment in (period_start, period_end)
    )
    try:
        policy = db.get_billing_policy(community_id, period_start, period_end)
        if not policy:
            raise BillingRunError(
                "No effective billing tariff configured", code="billing_tariff_missing"
            )
        policy = {**policy, "community_id": community_id}

        calculated = None
        if db.is_db_available():
            calculated = db.get_validated_calculated_values(
                community_id,
                period_start,
                period_end,
            )
            if not calculated:
                raise BillingRunError(
                    "Billing period has no validated VNB calculated-values evidence",
                    code="vnb_evidence_missing",
                )
        frames = billing_readings.load_period_frames(
            community_id,
            period_start,
            period_end,
            calculated_delivery=calculated,
        )
        if not frames.provenance["source_document_ids"]:
            raise BillingRunError(
                "Billing readings have no import provenance",
                code="billing_provenance_missing",
            )
        battery, battery_snapshot, battery_lines = _battery_lines(
            community_id, list(frames.participants), period_start, period_end
        )
        battery_kwargs = {}
        if battery:
            battery_kwargs = {
                "battery_participant_id": battery["participant_id"],
                "battery_capacity_kwh": battery["capacity_kwh"],
            }
            if calculated is not None:
                source_consumption, source_production = (
                    billing_readings.calculated_quantity_frames(frames, calculated)
                )
                allocated = billing_engine.allocate_energy(
                    frames.production.sum(axis=1),
                    frames.consumption,
                    model=policy["distribution_model"],
                )
                shares = frames.production.div(
                    frames.production.sum(axis=1).replace(0, float("nan")), axis=0
                ).fillna(0)
                credited = shares.mul(allocated.sum(axis=1), axis=0)
                if any(
                    not source.astype(float).round(6).equals(engine.round(6))
                    for source, engine in (
                        (source_consumption, allocated),
                        (source_production, credited),
                    )
                ):
                    raise BillingRunError(
                        "Battery attribution requires the VNB interval split "
                        "to match the engine allocation",
                        code="vnb_allocation_mismatch",
                    )
        summary = billing_engine.generate_billing_summary(
            frames.production,
            frames.consumption,
            grid_fee_per_kwh=policy["grid_fee_chf_per_kwh"],
            internal_price_per_kwh=policy["internal_price_chf_per_kwh"],
            network_level=policy["network_level"],
            distribution_model=policy["distribution_model"],
            settlement_fee_per_kwh=policy["settlement_fee_chf_per_kwh"],
            # Verrechnet wird die Aufteilung des VNB, nicht die eigene: sie
            # steht auf der Abrechnung, die das Mitglied selbst nachprüfen kann.
            billed_allocation=frames.vnb_reference["billed"],
            **battery_kwargs,
        )
        reconciliation = billing_readings.reconcile_with_vnb(frames, summary)
        existing = db.get_billing_period_for_window(
            community_id, period_start, period_end
        )
        existing_einfach = (
            db.get_billing_period(existing["id"], community_id)
            if existing is not None and policy["distribution_model"] == "einfach"
            else None
        )
        # Existing unmarked einfach drafts retain their original pricing and
        # exact audit; only the original legacy fingerprint permits a retry.
        legacy_einfach = (
            policy["distribution_model"] == "einfach"
            and isinstance(existing_einfach, dict)
            and isinstance(existing_einfach.get("reconciliation"), dict)
            and "precision_policy" not in existing_einfach["reconciliation"]
        )
        precision_basis = (
            calculated is not None
            and calculated.get("format_version") == "e66/1"
            and policy["distribution_model"] in {"proportional", "einfach"}
            and not battery
            and not legacy_einfach
        )
        if precision_basis or (
            battery
            and calculated is not None
            and calculated.get("format_version") == "e66/1"
        ):
            try:
                if precision_basis:
                    certificate = billing_reconciliation.audit_e66(
                        calculated, model=policy["distribution_model"]
                    )
                source_frames = billing_readings.frames_from_calculated_source(
                    calculated
                )
                if any(
                    not actual.tz_convert("UTC").round(6).equals(source.round(6))
                    for actual, source in (
                        (frames.consumption, source_frames.consumption),
                        (frames.production, source_frames.production),
                    )
                ):
                    raise ValueError("Physical readings differ from accepted source")
                if precision_basis:
                    vnb_consumption, vnb_production = (
                        billing_readings.calculated_quantity_frames(frames, calculated)
                    )
                    summary = billing_engine.generate_vnb_billing_summary(
                        frames.production,
                        frames.consumption,
                        vnb_consumption,
                        vnb_production,
                        grid_fee_per_kwh=policy["grid_fee_chf_per_kwh"],
                        internal_price_per_kwh=policy["internal_price_chf_per_kwh"],
                        network_level=policy["network_level"],
                        distribution_model=policy["distribution_model"],
                        settlement_fee_per_kwh=policy["settlement_fee_chf_per_kwh"],
                    )
                    # Here the source quantities are what gets billed, so the
                    # reconciliation keeps the engine's own allocation beside them:
                    # approval re-derives exactly this model gap from the source.
                    model = billing_engine.generate_billing_summary(
                        frames.production,
                        frames.consumption,
                        grid_fee_per_kwh=policy["grid_fee_chf_per_kwh"],
                        internal_price_per_kwh=policy["internal_price_chf_per_kwh"],
                        network_level=policy["network_level"],
                        distribution_model=policy["distribution_model"],
                        settlement_fee_per_kwh=policy["settlement_fee_chf_per_kwh"],
                    )
                    reconciliation = billing_readings.reconcile_with_vnb(frames, model)
            except billing_engine.VnbBalancingPolicyRequired as exc:
                raise BillingRunError(
                    str(exc),
                    code="vnb_allocation_mismatch",
                    reason="vnb_balancing_policy_required",
                ) from exc
            except ValueError as exc:
                raise BillingRunError(str(exc), code="vnb_allocation_mismatch") from exc
            if precision_basis:
                reconciliation["precision_policy"] = certificate
        if battery_lines:
            summary["line_items"].extend(battery_lines)
        if battery_snapshot:
            battery_snapshot["attribution"] = {
                participant["id"]: {
                    "direct_solar_kwh": participant["direct_solar_kwh"],
                    "battery_kwh": participant["battery_kwh"],
                    "value_chf": participant["battery_value_chf"],
                }
                for participant in summary["participants"]
                if participant["id"] != battery["participant_id"]
            }
            battery_snapshot["energy"] = dict(summary["battery_energy"])
            summary["battery_snapshot"] = battery_snapshot
        participant_gaps = reconciliation["per_participant"].values()
        production_gaps = reconciliation["production_per_participant"].values()
        if not precision_basis and (
            reconciliation["difference_kwh"] != 0
            or reconciliation["production_difference_kwh"] != 0
            or any(item["difference_kwh"] != 0 for item in participant_gaps)
            or any(item["difference_kwh"] != 0 for item in production_gaps)
        ):
            raise BillingRunError(
                "OpenLEG allocation does not match the VNB allocation",
                code="vnb_allocation_mismatch",
            )

        fingerprint = _fingerprint(frames, policy, summary, reconciliation)
        if existing:
            matches_main = (
                existing["input_fingerprint"] != fingerprint
                and existing.get("status") == "draft"
                and existing["input_fingerprint"]
                == _main_draft_fingerprint(
                    frames,
                    policy,
                    summary,
                    reconciliation,
                    precision_basis=precision_basis,
                    battery_kwargs=battery_kwargs,
                )
            )
            if existing["input_fingerprint"] != fingerprint and not matches_main:
                raise BillingRunError(
                    "Billing period inputs changed after processing",
                    code="billing_inputs_changed",
                )
            return {"status": "already_processed", "period_id": existing["id"]}

        summary.update(
            input_fingerprint=fingerprint,
            source_document_ids=list(frames.provenance["source_document_ids"]),
            reconciliation=reconciliation,
            timezone=frames.provenance["timezone"],
            calculated_values_fingerprint=frames.provenance.get(
                "calculated_values_fingerprint"
            ),
            # Freeze the complete effective policy so approval never
            # reconstructs historic choices from mutable tariff tables.
            billing_policy_snapshot=dict(policy),
            prepared_by=prepared_by or "system",
        )
        # Persist a named preparer; automated runs wait for a human to submit.
        period_id = db.save_billing_period(
            community_id,
            period_start,
            period_end,
            summary,
            **({"prepared_by": prepared_by} if prepared_by else {}),
        )
        return {"status": "created", "period_id": period_id}
    except BillingRunError:
        raise
    except billing_engine.BatteryBilledSplitMismatch as exc:
        raise BillingRunError(str(exc), code="vnb_allocation_mismatch") from exc
    except billing_readings.PeriodDataError as exc:
        if calculated is not None and any(
            problem["kind"] == "vnb_imbalance" for problem in exc.problems
        ):
            raise BillingRunError(
                str(exc),
                code="vnb_allocation_mismatch",
                reason="vnb_balancing_policy_required",
            ) from exc
        raise BillingRunError(str(exc), code="metering_readings_invalid") from exc
    except (db.BillingStoreError, DatabaseError) as exc:
        raise BillingRunError(str(exc), code="billing_store_unavailable") from exc
    except (KeyError, ValueError) as exc:
        raise BillingRunError(str(exc)) from exc
