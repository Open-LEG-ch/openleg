# SPDX-License-Identifier: AGPL-3.0-or-later
"""Summaries of already-authorized power samples, with explicit coverage."""

from collections import defaultdict
from decimal import Context, Decimal, localcontext

from telemetry import timestamp

MIN_PROFILES = 3


def view(installations, now, community):
    channels = defaultdict(lambda: defaultdict(list))
    sources = []
    for installation in installations:
        samples = [
            sample
            for sample in installation["samples"]
            if sample["metric"] == "power" and sample["unit"] == "W"
        ]
        # Share grants cover sample identity, not private installation labels,
        # owner identifiers, credentials or acknowledgement state.
        sources.append(
            {
                **{
                    key: installation[key]
                    for key in ("id", "source_id", "device_id", "cadence_seconds")
                },
                "samples": [
                    {
                        **sample,
                        "value": sample["value"]
                        if sample["freshness"] in ("fresh", "stale")
                        else None,
                    }
                    for sample in samples
                ],
            }
        )
        for sample in samples:
            channels[(sample["measurement_location"], sample["direction"])][
                installation["owner_building_id"]
            ].append(sample)
    groups = []
    for (location, direction), owners in sorted(channels.items()):
        eligible = []
        excluded = {
            "overlapping": 0,
            "unavailable": 0,
            "estimated": 0,
            "unsynchronized": 0,
        }
        for samples in owners.values():
            if len(samples) != 1:
                excluded["overlapping"] += 1
                continue
            sample = samples[0]
            if sample["quality"] == "estimated":
                excluded["estimated"] += 1
            elif (
                sample["quality"] != "measured"
                or sample["freshness"] != "fresh"
                or sample["delivery_status"] != "recent"
            ):
                excluded["unavailable"] += 1
            else:
                eligible.append(sample)
        observed = sorted({timestamp(sample["observed_at"]) for sample in eligible})
        value = None
        state = "too_small"
        contributors = 0
        if len(observed) > 1:
            state = "unsynchronized"
            excluded["unsynchronized"] = len(eligible)
        elif len(eligible) >= MIN_PROFILES:
            with localcontext(Context(prec=40)):
                value = format(
                    sum((Decimal(sample["value"]) for sample in eligible), Decimal(0)),
                    "f",
                )
            state = "available"
            contributors = len(eligible)
        groups.append(
            {
                "measurement_location": location,
                "direction": direction,
                "unit": "W",
                "state": state,
                "value": value,
                "contributing_profiles": contributors,
                "eligible_profiles": len(eligible),
                "visible_profiles": len(owners),
                "excluded": excluded,
                "earliest_observed_at": observed[0].isoformat() if observed else None,
                "latest_observed_at": observed[-1].isoformat() if observed else None,
            }
        )
    return {
        "community_id": community,
        "as_of": now.isoformat(),
        "authorized_installations": len(sources),
        "minimum_profiles": MIN_PROFILES,
        "groups": groups,
        "sources": sources,
    }
