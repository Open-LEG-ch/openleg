# SPDX-License-Identifier: AGPL-3.0-or-later
"""Versioned fictional village fixtures, used only inside disposable workers."""

import json
import tempfile
from pathlib import Path

from translation_markers import gettext_noop as _N

SEED_VERSION = "sonnwil/1"
PERSONAS = (
    ("sonnwil-resident", "Musterweg 1, Sonnwil", 4500, False),
    ("sonnwil-producer", "Musterweg 2, Sonnwil", 5000, True),
    ("sonnwil-founder", "Musterweg 3, Sonnwil", 6000, True),
    ("sonnwil-operator", "Musterweg 4, Sonnwil", 4000, False),
    ("sonnwil-private", "Musterweg 5, Sonnwil", 3000, False),
    ("sonnwil-neighbour", "Musterweg 6, Sonnwil", 4800, False),
)

ACCOUNTS = {
    "resident": {
        "name": "Lea Muster",
        "role": _N("Haushalt"),
        "scope": "Musterweg 1, Sonnwil",
        "building_id": "sonnwil-resident",
        "destination": "/dashboard",
        "scenario": _N("Energieprofil ändern"),
        "starting_state": _N("Sie sind als Haushalt am Musterweg 1 angemeldet."),
        "instructions": _N(
            "Ändern Sie den Jahresverbrauch im Energieprofil und speichern Sie."
        ),
        "expected": _N(
            "Nach dem Neuladen ist Ihr gespeicherter Jahresverbrauch weiterhin sichtbar."
        ),
    },
    "producer": {
        "name": "Noah Beispiel",
        "role": _N("Haushalt mit Solaranlage"),
        "scope": "Musterweg 2, Sonnwil",
        "building_id": "sonnwil-producer",
        "destination": "/dashboard",
        "scenario": _N("Verbrauch und Solaranlage ansehen"),
        "starting_state": _N("Ihr Haushalt am Musterweg 2 hat eine Solaranlage."),
        "instructions": _N("Ändern Sie Verbrauch oder PV-Leistung im Energieprofil."),
        "expected": _N("Die gespeicherten Angaben erscheinen nach dem Neuladen."),
    },
    "founder": {
        "name": "Mira Muster",
        "role": _N("LEG gründen"),
        "scope": "Musterweg 3, Sonnwil",
        "building_id": "sonnwil-founder",
        "destination": "/leg/community/create",
        "scenario": _N("Eine neue LEG gründen"),
        "starting_state": _N(
            "Sie haben ein bestätigtes Profil und noch keine eigene LEG."
        ),
        "instructions": _N(
            "Geben Sie Ihrer neuen LEG einen Namen und wählen Sie ein Verteilmodell."
        ),
        "expected": _N("Die neue LEG erscheint in Ihrem LEG-Dashboard."),
    },
    "administrator": {
        "name": "Robin Muster",
        "role": _N("LEG-Administration"),
        "scope": "LEG Sonnwil",
        "building_id": "sonnwil-neighbour",
        "destination": "/leg/dashboard?cid=sonnwil-operating-leg",
        "scenario": _N("Mitglieder und LEG-Einstellungen verwalten"),
        "starting_state": _N(
            "Die LEG Sonnwil ist aktiv. Sie verwalten die Gemeinschaft."
        ),
        "instructions": _N(
            "Ändern Sie die Regel für die gemeinsame Freigabe von Abrechnungen."
        ),
        "expected": _N("Die gewählte Freigaberegel bleibt nach dem Neuladen erhalten."),
    },
    "operator": {
        "name": "Sam Beispiel",
        "role": _N("Beschränkter LEG-Betrieb"),
        "scope": "LEG Sonnwil",
        "building_id": "sonnwil-operator",
        "destination": "/leg/dashboard?cid=sonnwil-operating-leg",
        "scenario": _N("Quartierakku erfassen"),
        "starting_state": _N(
            "Sie dürfen Abrechnungen vorbereiten. Die Administration bleibt einem anderen Konto vorbehalten."
        ),
        "instructions": _N(
            "Erfassen Sie einen Quartierakku mit Kapazität, Jahreskosten und Kostenanteilen."
        ),
        "expected": _N(
            "Der Quartierakku bleibt gespeichert. Die Freigaberegel können Sie mit diesem Konto nicht ändern."
        ),
    },
    "municipality": {
        "name": "Jules Beispiel",
        "role": _N("Gemeindeverwaltung"),
        "scope": "Sonnwil",
        "email": "sonnwil-municipality@example.org",
        "destination": "/gemeinde/dashboard",
        "scenario": _N("Interesse ansehen und Oberflächensprache ändern"),
        "starting_state": _N(
            "Sechs bestätigte Haushalte interessieren sich für eine LEG."
        ),
        "instructions": _N(
            "Wählen Sie eine andere Oberflächensprache und laden Sie das Dashboard neu."
        ),
        "expected": _N("Die Sprache bleibt bis zur nächsten Auswahl erhalten."),
    },
    "utility": {
        "name": "Kim Muster",
        "role": _N("Netzbetreiber"),
        "scope": "Sonnwil Energie (Simulation)",
        "email": "sonnwil-utility@example.org",
        "destination": "/utility/dashboard",
        "scenario": _N("E-Mail-Sprache und API-Zugang verwalten"),
        "starting_state": _N(
            "Ihr fiktiver Netzbetreiber ist aktiv und hat noch keinen API-Schlüssel."
        ),
        "instructions": _N(
            "Ändern Sie die E-Mail-Sprache oder erstellen und widerrufen Sie einen API-Schlüssel."
        ),
        "expected": _N(
            "Die gespeicherte E-Mail-Sprache und der Schlüsselstatus bleiben beim Kontowechsel erhalten."
        ),
    },
}
for account in ACCOUNTS.values():
    if "building_id" in account:
        account["email"] = account["building_id"] + "@example.org"


FIXTURE_MANIFEST = {
    "version": SEED_VERSION,
    "personas": {
        "resident": "sonnwil-resident",
        "producer": "sonnwil-producer",
        "founder": "sonnwil-founder",
        "operator": "sonnwil-operator",
        "private_household": "sonnwil-private",
        "neighbour": "sonnwil-neighbour",
        "administrator": "sonnwil-neighbour",
        "municipality": "sonnwil-municipality",
        "utility": "sonnwil-utility",
    },
    "reserved_states": {
        "new_household": "unverified",
        "sonnwil-forming-leg": "collecting_interest",
        "sonnwil-operating-leg": "active",
        "billing_period": "2026-09",
        "mail": "workspace-local",
        "webhooks": "workspace-local",
    },
    "available_scenarios": [
        "resident-profile",
        "access-email",
        "persona-switch",
        "role-scope",
        "community-create",
        "municipality-demand",
        "display-language",
        "utility-preferences",
        "utility-credentials",
    ],
}


def seed_village(db, directory):
    manifest = Path(directory) / "fixture.json"
    if manifest.exists():
        if json.loads(manifest.read_text())["version"] != SEED_VERSION:
            raise RuntimeError("sandbox fixture version mismatch")
        return
    for identifier, address, consumption, solar in PERSONAS:
        if not db.save_building(
            identifier,
            identifier + "@example.org",
            {
                "address": address,
                "annual_consumption_kwh": consumption,
                "lat": 0.01,
                "lon": 0.01,
                "potential_pv_kwp": 8 if solar else None,
                "municipality_name": "Sonnwil (fiktiv)",
                "bfs_number": 999999,
                "plz": "0000",
                "canton": "ZH",
            },
            {
                "share_with_neighbors": identifier != "sonnwil-private",
                "share_with_utility": False,
            },
            city_id="zurich",
            verified=True,
            has_solar=solar,
        ):
            raise RuntimeError("fictional village seed failed")
    with db.get_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            """INSERT INTO white_label_configs (territory, utility_name, config)
               VALUES ('zurich', 'Sonnwil Energie (Simulation)', %s)
               ON CONFLICT (territory) DO NOTHING""",
            (
                json.dumps(
                    {
                        "city_name": "Sonnwil",
                        "platform_name": "OpenLEG",
                        "ga4_id": "",
                        "seed_version": SEED_VERSION,
                    }
                ),
            ),
        )
    seed_accounts(db)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=directory, prefix=".fixture-", delete=False
    ) as output:
        json.dump(FIXTURE_MANIFEST, output)
        pending = Path(output.name)
    pending.replace(manifest)


def seed_accounts(db):
    """Use ordinary community memberships and staff accounts in this private copy."""
    with db.get_connection() as connection, connection.cursor() as cursor:
        for identifier, name, owner in (
            ("sonnwil-operating-leg", "LEG Sonnwil", "sonnwil-neighbour"),
            ("sonnwil-private-leg", "LEG Privat (Simulation)", "sonnwil-private"),
        ):
            cursor.execute(
                """INSERT INTO communities(community_id,name,admin_building_id,status)
                   VALUES (%s,%s,%s,'active')""",
                (identifier, name, owner),
            )
        for community, building, roles in (
            ("sonnwil-operating-leg", "sonnwil-neighbour", ["admin"]),
            ("sonnwil-operating-leg", "sonnwil-resident", []),
            ("sonnwil-operating-leg", "sonnwil-producer", []),
            (
                "sonnwil-operating-leg",
                "sonnwil-operator",
                ["metering", "billing_preparer"],
            ),
            ("sonnwil-private-leg", "sonnwil-private", ["admin"]),
        ):
            cursor.execute(
                """INSERT INTO community_members(community_id,building_id,role,access_roles,status,confirmed_at)
                   VALUES (%s,%s,%s,%s::jsonb,'confirmed',CURRENT_TIMESTAMP)""",
                (
                    community,
                    building,
                    "admin" if "admin" in roles else "member",
                    json.dumps(roles),
                ),
            )
    municipality = db.save_municipality(
        999999,
        "Sonnwil",
        kanton="ZH",
        dso_name="Sonnwil Energie (Simulation)",
        population=600,
        subdomain="zurich",
    )
    if not municipality or not db.update_municipality_status(
        999999, "active", admin_email="sonnwil-municipality@example.org"
    ):
        raise RuntimeError("fictional municipality seed failed")
    if not db.save_utility_client(
        "sonnwil-utility",
        "Sonnwil Energie (Simulation)",
        "sonnwil-utility@example.org",
        contact_name="Kim Muster",
        vnb_name="Sonnwil Energie (Simulation)",
        population=600,
        kanton="ZH",
        preferred_locale="de_CH",
    ) or not db.update_utility_client_status("sonnwil-utility", "active"):
        raise RuntimeError("fictional utility seed failed")
