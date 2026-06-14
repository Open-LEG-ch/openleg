# SPDX-License-Identifier: AGPL-3.0-or-later
"""High-level Ranking facade over PV profiles.

The class works with an injected profile list, so it stays pure and easy to
unit-test. Database access is only performed through ``Ranking.load()``.
"""

from typing import Dict, List, Optional

import pv_ranking
from store import ranking as store_ranking


class Ranking:
    """Ranking facade over a list of PV municipality profiles."""

    def __init__(self, profiles: List[Dict]):
        self._profiles = profiles

    @classmethod
    def load(cls, kanton: Optional[str] = None) -> "Ranking":
        """Profile aus dem Store laden. Ruft ``get_pv_profiles`` genau einmal auf."""
        profiles = (
            store_ranking.get_pv_profiles(kanton)
            if kanton is not None
            else store_ranking.get_pv_profiles()
        )
        return cls(profiles)

    def national(self) -> List[Dict]:
        """National ranking with display score and over-100 flag."""
        ranked = pv_ranking.assign_ranks(self._profiles)
        enriched = []
        for row in ranked:
            display_score, score_over_100 = pv_ranking.capped_score(
                row.get("pv_score_pct")
            )
            enriched.append(
                {
                    **row,
                    "display_score": display_score,
                    "score_over_100": score_over_100,
                }
            )
        return enriched

    def league_chips(self, profile: Dict) -> List[Dict]:
        """League chips for a single profile."""
        return pv_ranking.league_standings(self._profiles, profile)
