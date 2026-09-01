# SPDX-License-Identifier: AGPL-3.0-or-later
"""Direct contracts for the community formation status read model."""

from contextlib import contextmanager

import pytest

from formation_wizard import FormationStatus, get_community_status


class _Cursor:
    def __init__(self, row):
        self.row = row

    def execute(self, _query, _params=None):
        pass

    def fetchone(self):
        return self.row

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


class _Database:
    def __init__(self, row):
        self.cursor = _Cursor(row)

    @contextmanager
    def get_connection(self):
        yield _Connection(self.cursor)


def _row(status, members):
    return {
        "community_id": "community-1",
        "name": "LEG Musterweg",
        "status": status,
        "distribution_model": "proportional",
        "admin_building_id": "building-admin",
        "created_at": None,
        "formation_started_at": None,
        "dso_submitted_at": None,
        "members": members,
    }


def _confirmed_members(count=3):
    return [
        {"building_id": f"building-{number}", "status": "confirmed"}
        for number in range(count)
    ]


@pytest.mark.parametrize(
    ("status", "expected_score"),
    [
        (FormationStatus.FORMATION_STARTED.value, 30),
        (FormationStatus.DOCUMENTS_GENERATED.value, 60),
        (FormationStatus.SIGNATURES_PENDING.value, 60),
        (FormationStatus.DSO_SUBMITTED.value, 80),
        (FormationStatus.DSO_APPROVED.value, 100),
        (FormationStatus.ACTIVE.value, 100),
        (FormationStatus.REJECTED.value, 0),
    ],
)
def test_community_readiness_follows_the_weighted_state_table(status, expected_score):
    result = get_community_status(
        _Database(_row(status, _confirmed_members())), "community-1"
    )

    assert result is not None
    assert result["readiness_score"] == expected_score


def test_existing_community_without_members_returns_an_empty_status_model():
    result = get_community_status(
        _Database(_row(FormationStatus.INTERESTED.value, None)), "community-1"
    )

    assert result is not None
    assert result["members"] == []
    assert result["member_count"] == {"total": 0, "confirmed": 0, "invited": 0}
    assert result["readiness_score"] == 0
