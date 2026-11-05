# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bind retained billing records to a household registration incarnation."""

REGISTRATION_MATCH_SQL = """
    jsonb_typeof(i.provenance_snapshot->'participant_registration_id') = 'string'
    AND NULLIF(b.referral_code, '') = i.provenance_snapshot->>'participant_registration_id'
"""


class InvoiceRegistrationMismatch(ValueError):
    """The original billing registration is missing or has changed."""


def lock_original_registrations(cursor, registrations, participants):
    """Hold the original profiles through draft persistence or invoice issuance."""
    if (
        not isinstance(registrations, dict)
        or set(registrations) != set(participants)
        or any(
            not isinstance(value, str) or not value for value in registrations.values()
        )
    ):
        raise InvoiceRegistrationMismatch(
            "Die ursprünglichen Teilnehmerregistrierungen sind nicht belegt. "
            "Klären Sie die Zuordnung vor einer neuen Abrechnung."
        )
    cursor.execute(
        """SELECT building_id,referral_code FROM buildings
           WHERE building_id=ANY(%s) ORDER BY building_id FOR SHARE""",
        (sorted(registrations),),
    )
    actual = {row["building_id"]: row["referral_code"] for row in cursor.fetchall()}
    if actual != registrations:
        raise InvoiceRegistrationMismatch(
            "Eine Teilnehmerregistrierung hat sich seit der Vorbereitung geändert. "
            "Klären Sie die Zuordnung und erstellen Sie den Entwurf neu."
        )
