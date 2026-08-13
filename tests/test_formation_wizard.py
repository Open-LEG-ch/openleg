# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for formation_wizard public seams."""

from unittest.mock import MagicMock

import pytest

import formation_wizard


def _make_mock_db(row):
    """Return a mock db module whose get_connection/cursor seam yields row."""
    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = row
    cur_cm = MagicMock()
    cur_cm.__enter__ = MagicMock(return_value=mock_cur)
    cur_cm.__exit__ = MagicMock(return_value=False)
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = cur_cm
    conn_cm = MagicMock()
    conn_cm.__enter__ = MagicMock(return_value=mock_conn)
    conn_cm.__exit__ = MagicMock(return_value=False)
    mock_db = MagicMock()
    mock_db.get_connection.return_value = conn_cm
    return mock_db


def test_get_community_status_normalizes_none_members_to_empty_list():
    """PostgreSQL array_agg with FILTER returns NULL for zero members.

    The function must still return the community, normalize members to [],
    and report member_count total/confirmed/invited all zero.
    """
    community_id = "c0ffee"
    row = {
        "community_id": community_id,
        "name": "LEG Musterweg",
        "status": "interested",
        "distribution_model": "simple",
        "admin_building_id": "b-admin",
        "created_at": None,
        "formation_started_at": None,
        "dso_submitted_at": None,
        "members": None,
    }
    mock_db = _make_mock_db(row)

    result = formation_wizard.get_community_status(mock_db, community_id)

    assert result is not None
    assert result["community_id"] == community_id
    assert result["members"] == []
    assert result["member_count"] == {"total": 0, "confirmed": 0, "invited": 0}


@pytest.mark.parametrize(
    "status, expected_next_steps",
    [
        (
            "interested",
            ["Laden Sie mindestens 3 weitere Nachbarn ein."],
        ),
        (
            "formation_started",
            [
                "Erstellen Sie die Rechtsdokumente.",
                "Prüfen Sie die Gemeinschaftsvereinbarung.",
            ],
        ),
        (
            "documents_generated",
            [
                "Holen Sie die Unterschriften aller Mitglieder ein.",
                "Prüfen Sie die Teilnehmerverträge.",
            ],
        ),
        (
            "signatures_pending",
            ["Reichen Sie die Anmeldung beim VNB ein."],
        ),
        (
            "dso_submitted",
            [
                "Warten Sie auf die Genehmigung des VNB. Dies kann bis zu 30 Tage dauern."
            ],
        ),
        (
            "dso_approved",
            [
                "Legen Sie das Aktivierungsdatum fest.",
                "Konfigurieren Sie die Abrechnung.",
            ],
        ),
    ],
)
def test_get_community_status_next_steps(status, expected_next_steps):
    """get_community_status reports the correct next steps per workflow status."""
    community_id = "c0ffee"
    row = {
        "community_id": community_id,
        "name": "LEG Musterweg",
        "status": status,
        "distribution_model": "simple",
        "admin_building_id": "b-admin",
        "created_at": None,
        "formation_started_at": None,
        "dso_submitted_at": None,
        "members": [],
    }
    mock_db = _make_mock_db(row)

    result = formation_wizard.get_community_status(mock_db, community_id)

    assert result is not None
    assert result["next_steps"] == expected_next_steps
