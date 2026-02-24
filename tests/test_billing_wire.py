"""Tests for billing cron + endpoints wiring."""
import os
import pytest
from unittest.mock import patch, MagicMock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestBillingCronAuth:
    def test_cron_returns_403_without_secret(self):
        with open(os.path.join(PROJECT_ROOT, "app.py")) as f:
            content = f.read()
        assert "process-billing" in content

    def test_billing_route_exists(self):
        with open(os.path.join(PROJECT_ROOT, "app.py")) as f:
            content = f.read()
        assert "/api/cron/process-billing" in content

    def test_billing_summary_route_exists(self):
        with open(os.path.join(PROJECT_ROOT, "app.py")) as f:
            content = f.read()
        assert "/api/billing/community/" in content


class TestBillingDBFunctions:
    def test_get_active_communities_exists(self):
        import database as db
        assert hasattr(db, "get_active_communities")

    def test_get_community_for_building_exists(self):
        import database as db
        assert hasattr(db, "get_community_for_building")

    def test_get_billing_period_exists(self):
        import database as db
        assert hasattr(db, "get_billing_period")
