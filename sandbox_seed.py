# SPDX-License-Identifier: AGPL-3.0-or-later
"""Versioned fictional village fixtures, used only inside disposable workers."""

import json
import tempfile
from pathlib import Path

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
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=directory, prefix=".fixture-", delete=False
    ) as output:
        json.dump(FIXTURE_MANIFEST, output)
        pending = Path(output.name)
    pending.replace(manifest)
