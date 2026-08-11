# SPDX-License-Identifier: AGPL-3.0-or-later
"""Neighbour clustering runs on our own DBSCAN, not on scikit-learn.

scikit-learn was pulled in for one call: a haversine DBSCAN over building
coordinates. The expectations below are the labels scikit-learn produced for
these fixtures (eps = radius / earth radius, min_samples = min community size,
haversine metric), so the replacement has to reproduce them exactly.
"""

import os

import pandas as pd
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Two dense groups roughly 2 km apart plus two isolated buildings.
TWO_GROUPS = [
    (47.4700, 8.3000),
    (47.4704, 8.3002),
    (47.4702, 8.3006),
    (47.4706, 8.3008),
    (47.4900, 8.3200),
    (47.4903, 8.3203),
    (47.4901, 8.3207),
    (47.5500, 8.4000),
    (47.6000, 8.5000),
]
TWO_GROUPS_LABELS = [0, 0, 0, 0, 1, 1, 1, -1, -1]

# The fifth building sits 143 m from the first: inside the 150 m radius, so it
# joins the cluster as a border point even though it is not a core point.
BORDER_POINT = [
    (47.4700, 8.3000),
    (47.4704, 8.3002),
    (47.4702, 8.3006),
    (47.4706, 8.3008),
    (47.4710, 8.3012),
    (47.4900, 8.3200),
]
BORDER_POINT_LABELS = [0, 0, 0, 0, 0, -1]


def _partition(labels):
    groups = {}
    for index, label in enumerate(labels):
        groups.setdefault(label, set()).add(index)
    return groups


class TestClusterLabels:
    def test_separates_two_groups_and_marks_isolated_buildings_as_noise(self):
        import ml_models

        labels = ml_models.cluster_labels(TWO_GROUPS, 150, 3)

        assert _partition(labels) == _partition(TWO_GROUPS_LABELS)

    def test_border_points_join_the_cluster_that_reaches_them(self):
        import ml_models

        labels = ml_models.cluster_labels(BORDER_POINT, 150, 3)

        assert _partition(labels) == _partition(BORDER_POINT_LABELS)

    def test_radius_controls_membership(self):
        import ml_models

        # The border building sits 54 m from its nearest neighbour, so a 50 m
        # radius drops it while the other four still form a cluster.
        tight = ml_models.cluster_labels(BORDER_POINT, 50, 3)

        assert tight[4] == -1
        assert set(tight[:4]) == {0}

    def test_min_community_size_controls_cluster_formation(self):
        import ml_models

        labels = ml_models.cluster_labels(TWO_GROUPS, 150, 5)

        assert set(labels) == {-1}, "no group reaches five buildings"

    def test_too_few_buildings_are_all_noise(self):
        import ml_models

        assert ml_models.cluster_labels([(47.47, 8.3), (47.4704, 8.3002)], 150, 3) == [
            -1,
            -1,
        ]
        assert ml_models.cluster_labels([], 150, 3) == []


class TestFindOptimalCommunities:
    def test_assigns_the_same_clusters_end_to_end(self):
        import ml_models

        frame = pd.DataFrame(
            [
                {
                    "building_id": f"b{index}",
                    "lat": lat,
                    "lon": lon,
                    "annual_consumption_kwh": 4500,
                    "potential_pv_kwp": 8,
                }
                for index, (lat, lon) in enumerate(TWO_GROUPS)
            ]
        )

        communities, result = ml_models.find_optimal_communities(frame)

        assert _partition(list(result["cluster"])) == _partition(TWO_GROUPS_LABELS)
        assert {entry["num_members"] for entry in communities} == {4, 3}
        assert all(entry["autarky_percent"] >= 0 for entry in communities)


class TestScikitLearnIsGone:
    def test_requirements_do_not_pin_scikit_learn(self):
        with open(
            os.path.join(PROJECT_ROOT, "requirements.txt"), encoding="utf-8"
        ) as handle:
            content = handle.read().lower()
        assert "scikit-learn" not in content
        assert "sklearn" not in content

    def test_ml_models_does_not_import_sklearn(self):
        with open(
            os.path.join(PROJECT_ROOT, "ml_models.py"), encoding="utf-8"
        ) as handle:
            assert "sklearn" not in handle.read()

    def test_no_module_imports_sklearn(self):
        offenders = []
        for root, _, files in os.walk(PROJECT_ROOT):
            if any(part.startswith(".") for part in root.split(os.sep)):
                continue
            for name in files:
                if not name.endswith(".py") or name == os.path.basename(__file__):
                    continue
                path = os.path.join(root, name)
                with open(path, encoding="utf-8", errors="ignore") as handle:
                    if "sklearn" in handle.read():
                        offenders.append(path)
        assert offenders == []


@pytest.mark.parametrize("radius", [50, 150, 400])
def test_labels_are_stable_across_repeated_runs(radius):
    import ml_models

    first = ml_models.cluster_labels(TWO_GROUPS, radius, 3)
    second = ml_models.cluster_labels(TWO_GROUPS, radius, 3)

    assert first == second
