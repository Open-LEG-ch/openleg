# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for formation nudge email template and registration."""

import os

import formation_wizard

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_formation_neighbor_search_requires_current_sharing_consent():
    with open(formation_wizard.__file__, encoding="utf-8") as source_file:
        source = source_file.read()
    start = source.index("def get_formable_clusters")
    query = source[
        start : source.index("def calculate_municipality_business_case", start)
    ]

    assert "JOIN consents" in query
    assert "share_with_neighbors = TRUE" in query


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
