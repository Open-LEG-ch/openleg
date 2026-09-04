# SPDX-License-Identifier: AGPL-3.0-or-later
"""Public-interface tests for the clustering run (clustering_run).

The clustering run owns profile loading, community ranking, valid-only
assignment persistence, and an observable outcome — outside of Flask.
"""

import importlib
import os
from unittest.mock import MagicMock, call, patch

import numpy as np
import pandas as pd
import pytest

import clustering_run
from clustering_run import ClusteringOutcome


def _profiles(count):
    return [
        {
            "building_id": f"b{index}",
            "lat": 47.47 + index * 0.0004,
            "lon": 8.30 + index * 0.0002,
            "annual_consumption_kwh": 4500,
            "potential_pv_kwp": 8,
        }
        for index in range(count)
    ]


def _run(**overrides):
    deps = {
        "load_profiles": MagicMock(return_value=_profiles(3)),
        "rank_communities": MagicMock(
            return_value=(
                [{"community_id": 0, "num_members": 3, "autarky_percent": 42.0}],
                pd.DataFrame(
                    [
                        {"building_id": "b0", "cluster": 0},
                        {"building_id": "b1", "cluster": 0},
                        {"building_id": "b2", "cluster": 0},
                    ]
                ),
            )
        ),
        "save_cluster": MagicMock(return_value=True),
        "save_cluster_info": MagicMock(return_value=True),
    }
    deps.update(overrides)
    return clustering_run.run_clustering(**deps), deps


class TestTooFewProfilesIsANoop:
    @pytest.mark.parametrize("count", [0, 1])
    def test_returns_an_assertable_noop_outcome(self, count):
        outcome, _ = _run(load_profiles=MagicMock(return_value=_profiles(count)))

        assert isinstance(outcome, ClusteringOutcome)
        assert outcome.status == "noop"
        assert outcome.is_noop is True
        assert outcome.reason == "insufficient_profiles"
        assert outcome.profile_count == count
        assert outcome.ranked_communities == ()
        assert outcome.assignments_saved == 0
        assert outcome.cluster_info_saved == 0

    @pytest.mark.parametrize("count", [0, 1])
    def test_never_ranks_or_persists(self, count):
        _, deps = _run(load_profiles=MagicMock(return_value=_profiles(count)))

        deps["rank_communities"].assert_not_called()
        deps["save_cluster"].assert_not_called()
        deps["save_cluster_info"].assert_not_called()


class TestMinimumProfileGuard:
    def test_no_ranking_or_persistence_below_two_profiles(self):
        """Below MIN_PROFILES (2), ranking and persistence never happen."""
        outcome, deps = _run(load_profiles=MagicMock(return_value=_profiles(1)))

        assert outcome.is_noop is True
        assert outcome.reason == "insufficient_profiles"
        assert outcome.profile_count == 1
        deps["rank_communities"].assert_not_called()
        deps["save_cluster"].assert_not_called()
        deps["save_cluster_info"].assert_not_called()


class TestSuccessfulRun:
    def test_outcome_exposes_the_ranked_communities(self):
        ranked = [
            {"community_id": 1, "num_members": 2, "autarky_percent": 55.0},
            {"community_id": 0, "num_members": 3, "autarky_percent": 42.0},
        ]
        ranker = MagicMock(
            return_value=(
                ranked,
                pd.DataFrame([{"building_id": "b0", "cluster": 0}]),
            )
        )

        outcome, _ = _run(rank_communities=ranker)

        assert outcome.status == "completed"
        assert outcome.is_noop is False
        assert outcome.profile_count == 3
        assert list(outcome.ranked_communities) == ranked

    def test_profiles_are_loaded_for_the_territory_and_ranked_as_a_frame(self):
        outcome, deps = _run()

        deps["load_profiles"].assert_called_once_with(city_id=None)
        frame = deps["rank_communities"].call_args.args[0]
        assert list(frame["building_id"]) == ["b0", "b1", "b2"]
        assert outcome.assignments_saved == 3
        assert outcome.cluster_info_saved == 1

    def test_run_with_real_ranker_produces_ranked_output(self):
        profiles = [
            {**profile, "lat": 47.47 + (index % 3) * 0.0004, "lon": 8.30}
            for index, profile in enumerate(_profiles(3))
        ]

        outcome, _ = _run(
            load_profiles=MagicMock(return_value=profiles),
            rank_communities=clustering_run.ml_models.find_optimal_communities,
        )

        assert outcome.status == "completed"
        assert len(outcome.ranked_communities) == 1
        community = outcome.ranked_communities[0]
        assert community["num_members"] == 3
        assert community["autarky_percent"] >= 0
        assert outcome.assignments_saved == 3


class TestValidOnlyPersistence:
    def test_invalid_assignment_rows_never_create_cluster_records(self):
        frame = pd.DataFrame(
            [
                {"building_id": "b0", "cluster": 0},  # valid
                {"building_id": "b1", "cluster": -1},  # noise
                {"building_id": None, "cluster": 0},  # missing id
                {"building_id": "", "cluster": 1},  # empty id
                {"building_id": "b2", "cluster": "abc"},  # not a number
                {"building_id": "b3", "cluster": 2},  # valid
                {"building_id": "b4", "cluster": True},  # bool is not an int ID
                {"building_id": "b5", "cluster": 0.5},  # fractional float
                {"building_id": "b6", "cluster": 3.0},  # whole float, still not an int
                {"building_id": "b7", "cluster": float("nan")},  # NaN
                {"building_id": "b8", "cluster": np.int64(4)},  # valid numpy int
                {"building_id": "b9"},  # missing cluster
            ]
        )
        ranker = MagicMock(return_value=([], frame))

        outcome, deps = _run(rank_communities=ranker)

        assert {call.args for call in deps["save_cluster"].call_args_list} == {
            ("b0", 0),
            ("b3", 2),
            ("b8", 4),
        }
        assert outcome.assignments_saved == 3

    def test_missing_building_id_column_persists_nothing(self):
        ranker = MagicMock(return_value=([], pd.DataFrame([{"cluster": 0}])))

        outcome, deps = _run(rank_communities=ranker)

        deps["save_cluster"].assert_not_called()
        assert outcome.assignments_saved == 0

    def test_invalid_ranking_rows_never_create_cluster_records(self):
        valid_numpy = {"community_id": np.int64(7), "num_members": 2}
        ranked = [
            {"community_id": 0, "num_members": 3, "autarky_percent": 42.0},  # valid
            {"num_members": 2, "autarky_percent": 10.0},  # no community_id
            {"community_id": None, "num_members": 2},  # null community_id
            {"community_id": "not-a-number"},  # incomplete
            {"community_id": True},  # bool is not an int ID
            {"community_id": 1.5},  # fractional float
            {"community_id": float("nan")},  # NaN
            valid_numpy,  # valid numpy int
            "not-a-dict",
        ]
        ranker = MagicMock(
            return_value=(ranked, pd.DataFrame([{"building_id": "b0", "cluster": 0}]))
        )

        outcome, deps = _run(rank_communities=ranker)

        assert deps["save_cluster_info"].call_args_list == [
            call(0, ranked[0]),
            call(7, valid_numpy),
        ]
        assert outcome.cluster_info_saved == 2
        assert list(outcome.ranked_communities) == ranked


class TestDefaultWiring:
    def test_defaults_load_profiles_from_the_store(self, monkeypatch):
        from store import building

        load = MagicMock(return_value=_profiles(1))
        monkeypatch.setattr(building, "get_all_building_profiles", load)

        outcome = clustering_run.run_clustering(city_id="zurich")

        load.assert_called_once_with(city_id="zurich")
        assert outcome.is_noop is True
        assert outcome.city_id == "zurich"

    def test_defaults_persist_through_the_cluster_store(self, monkeypatch):
        from store import building, cluster

        monkeypatch.setattr(
            building, "get_all_building_profiles", MagicMock(return_value=_profiles(3))
        )
        save_cluster = MagicMock(return_value=True)
        save_cluster_info = MagicMock(return_value=True)
        monkeypatch.setattr(cluster, "save_cluster", save_cluster)
        monkeypatch.setattr(cluster, "save_cluster_info", save_cluster_info)
        ranker = MagicMock(
            return_value=(
                [{"community_id": 0, "num_members": 1}],
                pd.DataFrame([{"building_id": "b0", "cluster": 0}]),
            )
        )

        outcome = clustering_run.run_clustering(rank_communities=ranker)

        save_cluster.assert_called_once_with("b0", 0)
        save_cluster_info.assert_called_once()
        assert outcome.status == "completed"


class TestFlaskOwnsNoClusteringDecisions:
    def test_app_run_full_ml_task_is_a_thin_adapter(self, monkeypatch):
        with (
            patch.dict(
                os.environ,
                {
                    "DATABASE_URL": "postgresql://x:x@localhost/x",
                    "REDIS_URL": "memory://",
                    "CRON_SECRET": "test-cron-secret",
                    "APP_BASE_URL": "http://localhost:5003",
                },
            ),
            patch("database.is_db_available", return_value=True),
            patch("database._connection_pool", MagicMock()),
        ):
            import app

            app = importlib.reload(app)

        sentinel = object()
        fake_run = MagicMock(return_value=sentinel)
        monkeypatch.setattr(app.clustering_run, "run_clustering", fake_run)

        result = app.run_full_ml_task("building-1", "zurich")

        fake_run.assert_called_once_with(new_building_id="building-1", city_id="zurich")
        assert result is sentinel

    def test_app_module_makes_no_clustering_calls_itself(self):
        import inspect

        import app

        source = inspect.getsource(app.run_full_ml_task)
        assert "find_optimal_communities" not in source
        assert "get_all_building_profiles" not in source
        assert "save_cluster" not in source
