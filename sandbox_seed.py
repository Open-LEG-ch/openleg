# SPDX-License-Identifier: AGPL-3.0-or-later
"""Versioned fictional village fixtures, used only inside disposable workers."""

import json

SEED_VERSION = "sonnwil/1"
PERSONAS = (
    ("sonnwil-resident", "Musterweg 1, Sonnwil", 4500, False),
    ("sonnwil-producer", "Musterweg 2, Sonnwil", 5000, True),
    ("sonnwil-founder", "Musterweg 3, Sonnwil", 6000, True),
    ("sonnwil-operator", "Musterweg 4, Sonnwil", 4000, False),
    ("sonnwil-private", "Musterweg 5, Sonnwil", 3000, False),
    ("sonnwil-neighbour", "Musterweg 6, Sonnwil", 4800, False),
)

FIXTURE_MANIFEST = {
    "version": SEED_VERSION,
    "personas": {
        "resident": "sonnwil-resident",
        "producer": "sonnwil-producer",
        "founder": "sonnwil-founder",
        "operator": "sonnwil-operator",
        "private_household": "sonnwil-private",
        "neighbour": "sonnwil-neighbour",
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
    "available_scenarios": ["resident-profile", "access-email"],
}


def seed_village(db):
    with db.get_connection() as connection, connection.cursor() as cursor:
        cursor.execute("""CREATE TABLE IF NOT EXISTS sandbox_fixture (
            singleton BOOLEAN PRIMARY KEY CHECK (singleton),
            version TEXT NOT NULL, manifest JSONB NOT NULL
        )""")
        cursor.execute("SELECT version FROM sandbox_fixture WHERE singleton = TRUE")
        previous = cursor.fetchone()
        if previous:
            if previous["version"] != SEED_VERSION:
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
        cursor.execute(
            "INSERT INTO sandbox_fixture (singleton, version, manifest) VALUES (TRUE, %s, %s)",
            (SEED_VERSION, json.dumps(FIXTURE_MANIFEST)),
        )
