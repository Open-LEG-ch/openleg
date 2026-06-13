# SPDX-License-Identifier: AGPL-3.0-or-later
"""PV-Nutzungsdaten in die Datenbank laden.

Aufruf:
    python scripts/load_pv_data.py
"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db  # noqa: E402
import pv_data  # noqa: E402

logging.basicConfig(level=logging.INFO)


def main() -> int:
    if not db.init_db():
        print("DATABASE_URL fehlt oder DB nicht erreichbar.")
        return 1
    result = pv_data.refresh_pv_data()
    print(
        f"Snapshot: {result['snapshot_rows']} Gemeinden, "
        f"Panel: {result['panel_rows']} Zeilen, "
        f"Matching-Quote: {result['plant_match_rate_pct']} %"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
