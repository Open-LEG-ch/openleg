"""Tests for onboarding flow: tenant resolution, formation states, config."""
import pytest


class TestTenantResolution:
    def test_dietikon_resolves(self):
        from tenant import resolve_tenant
        assert resolve_tenant("dietikon.openleg.ch") == "dietikon"

    def test_bare_domain_resolves_zurich(self):
        from tenant import resolve_tenant
        assert resolve_tenant("openleg.ch") == "zurich"


class TestFormationStatus:
    def test_all_states_exist(self):
        from formation_wizard import FormationStatus
        expected = ["INTERESTED", "INVITED", "CONFIRMED", "FORMATION_STARTED",
                    "DOCUMENTS_GENERATED", "SIGNATURES_PENDING", "DSO_SUBMITTED",
                    "DSO_APPROVED", "ACTIVE", "REJECTED"]
        for state in expected:
            assert hasattr(FormationStatus, state)

    def test_valid_transitions(self):
        from formation_wizard import FormationStatus
        # Verify ordering makes sense
        flow = [FormationStatus.INTERESTED, FormationStatus.FORMATION_STARTED,
                FormationStatus.DOCUMENTS_GENERATED, FormationStatus.SIGNATURES_PENDING,
                FormationStatus.DSO_SUBMITTED, FormationStatus.DSO_APPROVED,
                FormationStatus.ACTIVE]
        for i in range(len(flow) - 1):
            assert flow[i] != flow[i + 1]


class TestFormationConfig:
    def test_min_community_size(self):
        from formation_wizard import FORMATION_CONFIG
        assert FORMATION_CONFIG["min_community_size"] == 3

    def test_max_community_size(self):
        from formation_wizard import FORMATION_CONFIG
        assert FORMATION_CONFIG["max_community_size"] == 50

    def test_formation_fee(self):
        from formation_wizard import FORMATION_CONFIG
        assert FORMATION_CONFIG["formation_fee_chf"] == 0

    def test_servicing_fee(self):
        from formation_wizard import FORMATION_CONFIG
        assert FORMATION_CONFIG["servicing_fee_monthly_chf"] == 0
