# SPDX-License-Identifier: AGPL-3.0-or-later
"""High-level Ranking facade over PV profiles.

The class works with an injected profile list, so it stays pure and easy to
unit-test. Database access is only performed through ``Ranking.load()``.
"""

import pv_badge
import pv_ranking
from store import ranking as store_ranking


class Ranking:
    """Ranking facade over a list of PV municipality profiles."""

    TOP_QUARTILE = pv_ranking.TOP_QUARTILE

    def __init__(self, profiles: list[dict]):
        self._profiles = profiles

    @classmethod
    def load(cls, kanton: str | None = None) -> "Ranking":
        """Profile aus dem Store laden. Ruft ``get_pv_profiles`` genau einmal auf."""
        profiles = (
            store_ranking.get_pv_profiles(kanton)
            if kanton is not None
            else store_ranking.get_pv_profiles()
        )
        return cls(profiles)

    def national(self) -> list[dict]:
        """National ranking with display score and over-100 flag."""
        return self._with_display(pv_ranking.assign_ranks(self._profiles))

    @staticmethod
    def _with_display(ranked: list[dict]) -> list[dict]:
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

    def league_chips(self, profile: dict) -> list[dict]:
        """League chips for a single profile."""
        return pv_ranking.league_standings(self._profiles, profile)

    @staticmethod
    def capped_score(score: float | None) -> tuple[float | None, bool]:
        """Score auf 100 deckeln; zweiter Wert signalisiert Schätzung übertroffen."""
        return pv_ranking.capped_score(score)

    def size_league_rank(self, profile: dict) -> dict | None:
        """Rang, Liga-Total und Quartil eines Profils in seiner Grössen-Liga."""
        size = pv_ranking.size_band(profile.get("population"))
        if size is None:
            return None
        league = pv_ranking.filter_league(self._profiles, size=size)
        return pv_ranking._rank_entry(league, profile.get("bfs_number"))

    def standings(
        self,
        kanton: str | None = None,
        size: str | None = None,
        density: str | None = None,
    ) -> list[dict]:
        """Gefilterte Rangliste mit nationaler Darstellungsform.

        Wendet ``filter_league`` an und ergänzt anschliessend Rang,
        display_score und score_over_100.
        """
        filtered = pv_ranking.filter_league(
            self._profiles, kanton=kanton, size=size, density=density
        )
        return self._with_display(pv_ranking.assign_ranks(filtered))

    def improvement_target(self, profile: dict) -> dict | None:
        """Verbesserungsziel für die Grössen-Liga eines Profils."""
        size = pv_ranking.size_band(profile.get("population"))
        if size is None:
            return None
        league = pv_ranking.filter_league(self._profiles, size=size)
        threshold = pv_ranking.top_quartile_threshold(league)
        return pv_ranking.improvement_target(profile, threshold)

    def leaders(self, kanton: str, exclude_bfs: int | None = None) -> list[dict]:
        """Vorbilder eines Kantons."""
        canton_profiles = pv_ranking.filter_league(self._profiles, kanton=kanton)
        return pv_ranking.league_leaders(canton_profiles, exclude_bfs=exclude_bfs)

    def movers(
        self,
        mover_rows: list[dict] | None = None,
        kanton: str | None = None,
        size: str | None = None,
        density: str | None = None,
    ) -> list[dict]:
        """Gemeinden mit dem grössten Score-Delta.

        Nutzt injizierte Zeilen, falls vorhanden, sonst ``store_ranking.get_pv_movers``.
        Wendet anschliessend dieselben Liga-Filter wie ``standings`` an.
        """
        rows = mover_rows if mover_rows is not None else store_ranking.get_pv_movers()
        return pv_ranking.filter_league(rows, kanton=kanton, size=size, density=density)

    def _rank_for_bfs(self, bfs: int) -> int | None:
        for row in pv_ranking.assign_ranks(self._profiles):
            if row.get("bfs_number") == bfs:
                return row.get("rank")
        return None

    def badge_svg(self, bfs: int, profile: dict | None = None) -> str:
        """SVG badge for one municipality.

        If ``profile`` is provided, it is used as the data source and only the
        national rank is resolved against the loaded ranking. This supports
        municipalities that exist in the profile table but are not part of the
        PV ranking because they have no score yet.
        """
        if profile is None:
            for row in self._profiles:
                if row.get("bfs_number") == bfs:
                    profile = row
                    break
            if profile is None:
                return ""
        display_score, _ = pv_ranking.capped_score(profile.get("pv_score_pct"))
        rank = self._rank_for_bfs(bfs)
        return pv_badge.badge_svg(profile.get("name"), display_score, rank)

    def og_card_svg(self, bfs: int, profile: dict | None = None) -> str:
        """SVG OpenGraph card for one municipality."""
        if profile is None:
            for row in self._profiles:
                if row.get("bfs_number") == bfs:
                    profile = row
                    break
            if profile is None:
                return ""
        display_score, _ = pv_ranking.capped_score(profile.get("pv_score_pct"))
        rank = self._rank_for_bfs(bfs)
        return pv_badge.og_card_svg(
            profile.get("name"),
            profile.get("kanton"),
            display_score,
            rank,
            profile.get("pv_untapped_kw"),
        )
