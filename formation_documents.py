# SPDX-License-Identifier: AGPL-3.0-or-later
"""Generate one complete, persistable LEG document bundle."""

import database as db
import document_generator
import formation_wizard


def generate(community_id: str, building_id: str) -> dict:
    """Generate and atomically persist documents for a community administrator."""
    status = formation_wizard.get_community_status(community_id)
    if not status or not any(
        member["building_id"] == building_id and member["role"] == "admin"
        for member in status["members"] or []
    ):
        return {"error": "Nur die Administration kann Dokumente erstellen."}

    participants = []
    for member in status["members"] or []:
        if member["status"] != "confirmed":
            continue
        building = db.get_building_for_dashboard(member["building_id"]) or {}
        pv = building.get("potential_pv_kwp") or 0
        participants.append(
            {
                "name": member.get("email") or member["building_id"],
                "address": member.get("address") or "",
                "role": "producer" if pv and float(pv) > 0 else "consumer",
            }
        )

    try:
        documents = [
            {
                "doc_type": "gemeinschaftsvereinbarung",
                "filename": "gemeinschaftsvereinbarung.pdf",
                "pdf_data": document_generator.generate_gemeinschaftsvereinbarung(
                    community_name=status["name"],
                    participants=participants,
                    municipality="",
                    distribution_model=status["distribution_model"],
                ),
            }
        ]
        documents.extend(
            {
                "doc_type": "teilnehmervertrag",
                "filename": f"teilnehmervertrag-{participant['name']}.pdf",
                "pdf_data": document_generator.generate_teilnehmervertrag(
                    participant_name=participant["name"],
                    participant_address=participant["address"],
                    community_name=status["name"],
                    role=participant["role"],
                ),
            }
            for participant in participants
        )
        generated = db.replace_leg_document_bundle(community_id, documents)
    except ValueError as exc:
        return {"error": str(exc)}

    return {"error": None, "generated": generated}
