# SPDX-License-Identifier: AGPL-3.0-or-later
"""The LEG value gap for one municipality.

Owns the ElCom tariff lookup and the value-gap calculation behind
`/api/v1/municipalities/<bfs>/value-gap`. The profile and pilot page contexts
that used to live here went with their pages to the public-site repository.
"""

import database as db
import public_data

PROFILE_TARIFF_YEAR = 2026


def _first_h4_tariff(bfs, year=None):
    tariffs = db.get_elcom_tariffs(bfs, year=year)
    h4 = next((t for t in tariffs if str(t.get("category", "")).startswith("H4")), None)
    return tariffs, h4


def _value_gap_for_tariff(h4, grid_reduction_pct):
    if not h4:
        return None
    return public_data.compute_leg_value_gap(h4, grid_reduction_pct=grid_reduction_pct)


def value_gap(bfs, *, year=PROFILE_TARIFF_YEAR, grid_reduction_pct=40.0):
    _tariffs, h4 = _first_h4_tariff(bfs, year=year)
    return _value_gap_for_tariff(h4, grid_reduction_pct)
