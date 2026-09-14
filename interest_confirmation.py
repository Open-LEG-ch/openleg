# SPDX-License-Identifier: AGPL-3.0-or-later
"""Committed interest confirmation and its subsequent effects."""

import threading
from dataclasses import dataclass
from typing import Literal

import email_automation


@dataclass(frozen=True)
class ConfirmationResult:
    status: Literal["confirmed", "invalid", "conflict"]
    municipality_name: str | None = None


def _notify_municipality(interest, *, base_url):
    bfs_number = interest.get("bfs_number")
    municipality_name = interest.get("municipality_name")
    if bfs_number and municipality_name:
        email_automation.notify_new_municipality_interest(
            bfs_number=int(bfs_number),
            municipality_name=municipality_name,
            newcomer_email=interest.get("email", ""),
            base_url=base_url,
        )


def confirm_building(token, *, db, base_url, run_clustering):
    try:
        building = db.confirm_building_interest(token)
    except db.VerificationConflict:
        return ConfirmationResult("conflict")
    if not building:
        return ConfirmationResult("invalid")

    email_automation.schedule_sequence_for_user(
        building["building_id"], building.get("email", "")
    )
    threading.Thread(
        target=run_clustering,
        args=(building["building_id"], building.get("city_id")),
        daemon=True,
    ).start()
    _notify_municipality(building, base_url=base_url)
    return ConfirmationResult("confirmed", building.get("municipality_name"))


def confirm_coverage(token, *, db, base_url):
    interest = db.verify_coverage_request(token)
    if not interest:
        return ConfirmationResult("invalid")

    _notify_municipality(interest, base_url=base_url)
    return ConfirmationResult("confirmed", interest.get("municipality_name"))
