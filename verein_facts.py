# SPDX-License-Identifier: AGPL-3.0-or-later
"""Evidence-backed association facts and shared public readiness.

Policy values are code-owned, never runtime fee overrides. Existing dues and
payment evidence retain their stored amounts and dates.
"""

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class VereinFacts:
    legal_name: str = "Verein openLEG Schweiz"
    seat: str = "Obersiggenthal AG"
    annual_dues_chf: int = 50
    payment_days: int = 30
    reminder_days: int = 60
    contact_email: str = "hallo@openleg.ch"
    configured_formation_date: str = ""
    formation_date: date | None = None
    contact_address: str = ""
    privacy_version: str = ""
    data_controller: str = ""
    statutes_url: str = ""

    @property
    def public_ready(self):
        return self.formation_date is not None and all(
            (self.contact_address, self.privacy_version, self.data_controller)
        )


POLICY = VereinFacts()


def facts(config=None, *, now=None):
    """Keep configured evidence; expose a founded claim only from Zurich's date."""
    from dataclasses import replace

    config = config or {}
    raw = config.get("VEREIN_FORMATION_DATE", "")
    try:
        formed = date.fromisoformat(raw)
    except (ValueError, TypeError):
        formed = None
    today = (
        (now or datetime.now(ZoneInfo("Europe/Zurich")))
        .astimezone(ZoneInfo("Europe/Zurich"))
        .date()
    )
    return replace(
        POLICY,
        configured_formation_date=raw,
        formation_date=formed if formed and formed <= today else None,
        **{
            field: str(config.get("VEREIN_" + field.upper(), "") or "").strip()
            for field in (
                "contact_address",
                "privacy_version",
                "data_controller",
                "statutes_url",
            )
        },
    )
