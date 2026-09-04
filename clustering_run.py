# SPDX-License-Identifier: AGPL-3.0-or-later
"""Clustering run orchestration, independent of Flask.

One run owns profile loading, community ranking, valid-only assignment
persistence, and returns an observable outcome. Flask (or any other caller)
only wires the trigger; it makes no clustering decisions itself.
"""

import logging
import numbers
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pandas as pd

import ml_models

logger = logging.getLogger(__name__)

MIN_PROFILES = 2
DEFAULT_RADIUS_METERS = 150
DEFAULT_MIN_COMMUNITY_SIZE = 2

STATUS_COMPLETED = "completed"
STATUS_NOOP = "noop"


@dataclass(frozen=True)
class ClusteringOutcome:
    """Observable result of one clustering run."""

    status: str
    reason: str
    city_id: str | None = None
    new_building_id: str | None = None
    profile_count: int = 0
    ranked_communities: tuple = ()
    assignments_saved: int = 0
    cluster_info_saved: int = 0

    @property
    def is_noop(self) -> bool:
        return self.status == STATUS_NOOP


def _coerce_id(value: Any) -> int | None:
    """Return int for actual integer IDs (incl. numpy integers), else None.

    Booleans, floats (whole, fractional, or NaN), strings, and missing
    values are never valid cluster/community IDs.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, numbers.Integral):
        return int(value)
    return None


def _valid_assignment(row: dict) -> tuple[str, int] | None:
    """Return (building_id, cluster_id) for persistable rows, else None."""
    building_id = row.get("building_id")
    if not isinstance(building_id, str) or not building_id.strip():
        return None
    cluster_id = _coerce_id(row.get("cluster"))
    if cluster_id is None or cluster_id < 0:
        return None
    return building_id, cluster_id


def _valid_community(community: Any) -> bool:
    """A ranking row is persistable only with an integer community_id."""
    if not isinstance(community, dict):
        return False
    return _coerce_id(community.get("community_id")) is not None


def run_clustering(
    *,
    new_building_id: str | None = None,
    city_id: str | None = None,
    load_profiles: Callable[..., list[dict]] | None = None,
    rank_communities: Callable[..., Any] = ml_models.find_optimal_communities,
    save_cluster: Callable[[str, int], bool] | None = None,
    save_cluster_info: Callable[[int, dict], bool] | None = None,
    radius_meters: int = DEFAULT_RADIUS_METERS,
    min_community_size: int = DEFAULT_MIN_COMMUNITY_SIZE,
) -> ClusteringOutcome:
    """Run one clustering pass and report what happened."""
    if load_profiles is None:
        from store.building import get_all_building_profiles as load_profiles
    if save_cluster is None:
        from store.cluster import save_cluster
    if save_cluster_info is None:
        from store.cluster import save_cluster_info

    profiles = load_profiles(city_id=city_id) or []
    if len(profiles) < MIN_PROFILES:
        logger.info(
            "[ML] Not enough buildings for clustering (%d < %d).",
            len(profiles),
            MIN_PROFILES,
        )
        return ClusteringOutcome(
            status=STATUS_NOOP,
            reason="insufficient_profiles",
            city_id=city_id,
            new_building_id=new_building_id,
            profile_count=len(profiles),
        )

    building_data = pd.DataFrame(profiles)
    ranked_communities, buildings_with_clusters = rank_communities(
        building_data,
        radius_meters=radius_meters,
        min_community_size=min_community_size,
    )

    assignments_saved = 0
    if "building_id" in buildings_with_clusters.columns:
        for row in buildings_with_clusters.to_dict("records"):
            assignment = _valid_assignment(row)
            if assignment is None:
                continue
            if save_cluster(*assignment):
                assignments_saved += 1

    cluster_info_saved = 0
    for community in ranked_communities:
        if not _valid_community(community):
            continue
        if save_cluster_info(int(community["community_id"]), community):
            cluster_info_saved += 1

    logger.info(
        "[ML] Clustering done: %d clusters, %d assignments saved.",
        len(ranked_communities),
        assignments_saved,
    )
    return ClusteringOutcome(
        status=STATUS_COMPLETED,
        reason="ok",
        city_id=city_id,
        new_building_id=new_building_id,
        profile_count=len(profiles),
        ranked_communities=tuple(ranked_communities),
        assignments_saved=assignments_saved,
        cluster_info_saved=cluster_info_saved,
    )
