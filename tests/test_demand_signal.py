"""Tests for compute_municipality_demand_signal in insights_engine.py.

Covers US-001: verified municipality demand signal.
"""
import pytest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db_mock(main_rows, formation_rows=None):
    """Return a patched `insights_engine.db` that yields *main_rows* on the
    first cursor.fetchall() call and *formation_rows* on the second call."""
    if formation_rows is None:
        formation_rows = []

    mock_cur = MagicMock()
    mock_cur.__enter__ = lambda s: s
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_cur.fetchall.side_effect = [main_rows, formation_rows]

    mock_conn = MagicMock()
    mock_conn.__enter__ = lambda s: s
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = mock_cur

    mock_db = MagicMock()
    mock_db.get_connection.return_value = mock_conn
    return mock_db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestComputeMunicipalityDemandSignal:

    def test_positive_case_returns_verified_signal(self):
        """Municipality with resident registrations and LEG formation produces
        a non-zero demand_score and signal_type='verified'."""
        main_rows = [
            {
                "bfs_number": 261,
                "name": "Dietikon",
                "kanton": "ZH",
                "subdomain": "dietikon",
                "total_registered": 15,
                "verified_buildings": 10,
                "recent_signups_90d": 5,
                "confirmed_leg_members": 3,
                "meter_data_uploads": 4,
            }
        ]
        formation_rows = [{"bfs_number": 261, "communities_in_formation": 1}]

        with patch("insights_engine.db", _make_db_mock(main_rows, formation_rows)):
            from insights_engine import compute_municipality_demand_signal
            result = compute_municipality_demand_signal(bfs_number=261)

        assert "signals" in result
        assert len(result["signals"]) == 1

        sig = result["signals"][0]
        assert sig["bfs_number"] == 261
        assert sig["name"] == "Dietikon"
        assert sig["signal_type"] == "verified"
        assert sig["heuristic_baseline"]["has_resident_data"] is True

        vd = sig["verified_demand"]
        assert vd["verified_buildings"] == 10
        assert vd["recent_signups_90d"] == 5
        assert vd["confirmed_leg_members"] == 3
        assert vd["communities_in_formation"] == 1
        assert vd["demand_score"] > 0
        assert sig["demand_level"] in ("low", "medium", "high")

    def test_positive_case_high_demand_level(self):
        """Municipality with many verified buildings reaches 'high' demand level
        (demand_score >= 40)."""
        main_rows = [
            {
                "bfs_number": 230,
                "name": "Winterthur",
                "kanton": "ZH",
                "subdomain": "winterthur",
                "total_registered": 50,
                "verified_buildings": 30,
                "recent_signups_90d": 20,
                "confirmed_leg_members": 15,
                "meter_data_uploads": 10,
            }
        ]
        formation_rows = [{"bfs_number": 230, "communities_in_formation": 3}]

        with patch("insights_engine.db", _make_db_mock(main_rows, formation_rows)):
            from insights_engine import compute_municipality_demand_signal
            result = compute_municipality_demand_signal()

        sig = result["signals"][0]
        assert sig["demand_level"] == "high"
        assert sig["verified_demand"]["demand_score"] >= 40

    def test_empty_case_returns_heuristic_only_signal(self):
        """Municipality with no resident registrations returns demand_level='none'
        and signal_type='heuristic_only'."""
        main_rows = [
            {
                "bfs_number": 242,
                "name": "Urdorf",
                "kanton": "ZH",
                "subdomain": "urdorf",
                "total_registered": 0,
                "verified_buildings": 0,
                "recent_signups_90d": 0,
                "confirmed_leg_members": 0,
                "meter_data_uploads": 0,
            }
        ]

        with patch("insights_engine.db", _make_db_mock(main_rows)):
            from insights_engine import compute_municipality_demand_signal
            result = compute_municipality_demand_signal(bfs_number=242)

        assert len(result["signals"]) == 1
        sig = result["signals"][0]
        assert sig["signal_type"] == "heuristic_only"
        assert sig["heuristic_baseline"]["has_resident_data"] is False
        assert sig["verified_demand"]["demand_score"] == 0.0
        assert sig["demand_level"] == "none"
        assert sig["heuristic_baseline"]["source"] == "public_data_only"

    def test_low_signal_case(self):
        """Municipality with a single verified building is 'low' demand."""
        main_rows = [
            {
                "bfs_number": 247,
                "name": "Schlieren",
                "kanton": "ZH",
                "subdomain": "schlieren",
                "total_registered": 1,
                "verified_buildings": 1,
                "recent_signups_90d": 0,
                "confirmed_leg_members": 0,
                "meter_data_uploads": 0,
            }
        ]

        with patch("insights_engine.db", _make_db_mock(main_rows)):
            from insights_engine import compute_municipality_demand_signal
            result = compute_municipality_demand_signal()

        sig = result["signals"][0]
        assert sig["signal_type"] == "verified"
        assert sig["demand_level"] == "low"
        assert 0 < sig["verified_demand"]["demand_score"] < 15

    def test_no_municipalities_returns_empty_signals(self):
        """When the municipalities table is empty, signals list is empty."""
        with patch("insights_engine.db", _make_db_mock([])):
            from insights_engine import compute_municipality_demand_signal
            result = compute_municipality_demand_signal()

        assert result["signals"] == []
        assert "computed_at" in result

    def test_db_error_returns_error_key(self):
        """A database exception must be caught and returned as an error key."""
        mock_db = MagicMock()
        mock_db.get_connection.side_effect = Exception("connection refused")

        with patch("insights_engine.db", mock_db):
            from insights_engine import compute_municipality_demand_signal
            result = compute_municipality_demand_signal()

        assert result["signals"] == []
        assert "error" in result
        assert "connection refused" in result["error"]

    def test_demand_score_components(self):
        """Demand score is computed from verified resident signals only."""
        main_rows = [
            {
                "bfs_number": 261,
                "name": "Dietikon",
                "kanton": "ZH",
                "subdomain": "dietikon",
                "total_registered": 5,
                "verified_buildings": 5,   # contributes min(5*2=10, 40) = 10
                "recent_signups_90d": 5,   # contributes min(5, 15) = 5
                "confirmed_leg_members": 2, # contributes min(2*3=6, 30) = 6
                "meter_data_uploads": 0,
            }
        ]
        # 0 communities in formation → 0 extra points
        with patch("insights_engine.db", _make_db_mock(main_rows)):
            from insights_engine import compute_municipality_demand_signal
            result = compute_municipality_demand_signal(bfs_number=261)

        vd = result["signals"][0]["verified_demand"]
        # expected: 10 + 5 + 6 + 0 = 21
        assert vd["demand_score"] == 21.0

    def test_bfs_filter_passes_to_query(self):
        """When bfs_number is provided, it is forwarded to the DB query."""
        main_rows = [
            {
                "bfs_number": 261,
                "name": "Dietikon",
                "kanton": "ZH",
                "subdomain": "dietikon",
                "total_registered": 3,
                "verified_buildings": 3,
                "recent_signups_90d": 0,
                "confirmed_leg_members": 0,
                "meter_data_uploads": 0,
            }
        ]
        mock_db = _make_db_mock(main_rows)

        with patch("insights_engine.db", mock_db):
            from insights_engine import compute_municipality_demand_signal
            result = compute_municipality_demand_signal(bfs_number=261)

        # The query must have been called with bfs_number as parameter
        calls = mock_db.get_connection.return_value.cursor.return_value.execute.call_args_list
        assert any("261" in str(c) for c in calls)
        assert result["bfs_number"] == 261
