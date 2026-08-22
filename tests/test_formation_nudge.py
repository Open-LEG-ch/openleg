# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for formation nudge email template and registration."""

import os
from contextlib import contextmanager

import formation_wizard
from tests.consent_visibility import filters_by_consent

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

NEIGHBOURS = (
    {"building_id": "consented-a", "address": "Badstrasse 1, Baden"},
    {"building_id": "consented-b", "address": "Badstrasse 3, Baden"},
    {"building_id": "revoked", "address": "Badstrasse 5, Baden"},
    {"building_id": "never-consented", "address": "Badstrasse 7, Baden"},
)
CONSENTS = {"consented-a": True, "consented-b": True, "revoked": False}


class _FormationCursor:
    """Answers the two statements get_formable_clusters issues, in order."""

    def __init__(self):
        self.queries = []

    def execute(self, query, params=None):
        self.queries.append(" ".join(query.split()))
        self.params = params or ()

    def fetchone(self):
        return {"lat": 47.4736, "lon": 8.3060}

    def fetchall(self):
        rows = [
            {**row, "email": f"{row['building_id']}@example.ch", "distance": 40.0}
            for row in NEIGHBOURS
        ]
        if filters_by_consent(self.queries[-1]):
            rows = [row for row in rows if CONSENTS.get(row["building_id"]) is True]
        return rows

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class _FormationConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


class _FormationDb:
    def __init__(self):
        self.cursor = _FormationCursor()

    @contextmanager
    def get_connection(self):
        yield _FormationConnection(self.cursor)


def test_formation_neighbor_search_excludes_revoked_and_missing_consent():
    """Executed, not read.

    This test used to read the function's own source and assert two substrings.
    A LEFT JOIN carrying the predicate in its ON clause keeps both substrings
    and every revoked neighbour with them.
    """
    clusters = formation_wizard.get_formable_clusters(_FormationDb(), "searcher")

    assert clusters, "two consenting neighbours are enough to form"
    nearby = {row["building_id"] for row in clusters[0]["nearby_buildings"]}
    assert nearby == {"consented-a", "consented-b"}
    assert clusters[0]["potential_members"] == 3
    assert clusters[0]["ready_to_form"] is True


class TestFormationNudgeTemplate:
    def test_formation_nudge_template_exists(self):
        path = os.path.join(PROJECT_ROOT, "templates", "emails", "formation_nudge.html")
        assert os.path.exists(path)

    def test_template_has_german_content(self):
        path = os.path.join(PROJECT_ROOT, "templates", "emails", "formation_nudge.html")
        with open(path) as f:
            content = f.read()
        assert "LEG-Gründung wartet" in content
        assert "community_name" in content
        assert "days_stuck" in content


class TestFormationNudgeRegistration:
    def test_formation_nudge_in_email_module(self):
        import email_automation

        assert "formation_nudge" in email_automation.TRIGGER_TEMPLATES
        config = email_automation.TRIGGER_TEMPLATES["formation_nudge"]
        assert "subject" in config
        assert "template" in config
