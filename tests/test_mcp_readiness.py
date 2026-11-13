# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pinned production readiness evidence. Renames require explicit review."""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
# Confirmation pins cover both formation and membership: zero business rows,
# one package after CSRF-protected dashboard confirmation, then transport.
# OAuth pins cover issuer/audience/expiry/community, code reuse, redirect/PKCE,
# scope expansion and capability denial before any downstream operator call.
# Refresh pins cover rotation, family reuse revocation, dashboard revocation
# and immediate rejection at the real MCP HTTP boundary.
# Projection pins inject private columns into every read and include a real
# PostgreSQL membership read. Sidecar pins cover opt-in startup and packaging.
PINNED_IDS = (
    "tests/test_agent_confirmation.py::test_agent_pending_replay_has_no_business_effects[/formation/submissions-payload0]",
    "tests/test_agent_confirmation.py::test_agent_pending_replay_has_no_business_effects[/membership-mutations-payload1]",
    "tests/test_agent_confirmation.py::test_confirmation_and_completed_replay[/formation/submissions-payload0]",
    "tests/test_agent_confirmation.py::test_confirmation_and_completed_replay[/membership-mutations-payload1]",
    "tests/test_agent_confirmation.py::test_transport_only_runs_after_human_confirmation[/formation/submissions-payload0]",
    "tests/test_agent_confirmation.py::test_transport_only_runs_after_human_confirmation[/membership-mutations-payload1]",
    "tests/test_oauth.py::test_sidecar_rejects_invalid_identity",
    "tests/test_oauth_postgres.py::test_flow",
    "tests/test_oauth_postgres.py::test_exchange_rejects_mismatch[change0]",
    "tests/test_oauth_postgres.py::test_exchange_rejects_mismatch[change1]",
    "tests/test_oauth_postgres.py::test_exchange_rejects_mismatch[change2]",
    "tests/test_oauth_postgres.py::test_exchange_rejects_mismatch[change3]",
    "tests/test_oauth_postgres.py::test_authorization_rejects_invalid_request[change0]",
    "tests/test_oauth_postgres.py::test_authorization_rejects_invalid_request[change1]",
    "tests/test_oauth_postgres.py::test_authorization_rejects_invalid_request[change2]",
    "tests/test_oauth_postgres.py::test_authorization_rejects_invalid_request[change3]",
    "tests/test_oauth_postgres.py::test_authorization_rejects_invalid_request[change4]",
    "tests/test_oauth_postgres.py::test_authorization_rejects_invalid_request[change5]",
    "tests/test_oauth_postgres.py::test_authorization_rejects_invalid_request[change6]",
    "tests/test_oauth_postgres.py::test_authorization_rejects_invalid_request[change7]",
    "tests/test_oauth_postgres.py::test_mcp_host_flow",
    "tests/test_oauth_postgres.py::test_scope_cannot_expand_at_exchange",
    "tests/test_oauth_postgres.py::test_sidecar_enforces_capability",
    "tests/test_oauth_refresh_postgres.py::test_rotation_and_reuse",
    "tests/test_oauth_refresh_postgres.py::test_dashboard_revoke",
    "tests/test_oauth_refresh_postgres.py::test_next_mcp_request_rejected[dashboard]",
    "tests/test_oauth_refresh_postgres.py::test_next_mcp_request_rejected[reuse]",
    "tests/test_operator_projection_allowlists.py::test_formation_inventory",
    "tests/test_operator_projection_allowlists.py::test_membership_inventory",
    "tests/test_operator_projection_allowlists.py::test_webhook_deliveries_inventory",
    "tests/test_operator_projection_allowlists.py::test_credentials_inventory",
    "tests/test_operator_projection_allowlists.py::test_real_membership_read_excludes_participant",
    "tests/test_operator_projection_allowlists.py::test_operational_inventory[metering/jobs-list_operator_metering_jobs-fields0]",
    "tests/test_operator_projection_allowlists.py::test_operational_inventory[metering/calculated-deliveries-list_operator_calculated_deliveries-fields1]",
    "tests/test_operator_projection_allowlists.py::test_operational_inventory[billing/periods-list_operator_billing_periods-fields2]",
    "tests/test_operator_projection_allowlists.py::test_operational_inventory[billing/invoices-list_operator_invoices-fields3]",
    "tests/test_operator_projection_allowlists.py::test_operational_inventory[billing/cases-list_operator_invoice_cases-fields4]",
    "tests/test_operator_projection_allowlists.py::test_operational_inventory[payments/matches-list_operator_payment_matches-fields5]",
    "tests/test_mcp_sidecar.py::test_disabled_by_default",
    "tests/test_mcp_sidecar.py::test_local_packaging_is_opt_in",
)


def test_pinned_evidence_is_collected_and_unmarked():
    result = subprocess.run(
        [sys.executable, "-m", "scripts.mcp_readiness", "--collect-only"],
        cwd=ROOT,
        env={**os.environ, "PYTEST_ADDOPTS": ""},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_default_configuration_is_off(monkeypatch):
    from mcp_sidecar.server import create_app

    # Valid remaining config prevents an enabled default hiding behind OAuth errors.
    monkeypatch.setenv("OPENLEG_MCP_DEV_STATIC", "true")
    monkeypatch.setenv("OPENLEG_OPERATOR_ORIGIN", "http://127.0.0.1:5000")
    for value in (None, "false", "", "1", "TRUE"):
        if value is None:
            monkeypatch.delenv("OPENLEG_MCP_ENABLED", raising=False)
        else:
            monkeypatch.setenv("OPENLEG_MCP_ENABLED", value)
        with pytest.raises(RuntimeError, match="disabled"):
            create_app()
    monkeypatch.setenv("OPENLEG_MCP_ENABLED", "true")
    assert create_app() is not None
    services = yaml.safe_load((ROOT / "compose.mcp.yml").read_text())["services"]
    assert (
        services["mcp"]["environment"]["OPENLEG_MCP_ENABLED"]
        == "${OPENLEG_MCP_ENABLED:-false}"
    )
    assert services["mcp"]["profiles"] == ["mcp"]
    default = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]
    assert "mcp" not in default
    assert "mcp-proxy" not in default


def test_documented_scopes_match_operator_contract():
    contract = yaml.safe_load((ROOT / "docs/openapi/operator-v1.yaml").read_text())
    capabilities = {
        operation["x-openleg-capability"]
        for path in contract["paths"].values()
        for operation in path.values()
        if isinstance(operation, dict) and "x-openleg-capability" in operation
    }
    guide = (ROOT / "docs/mcp-sidecar.md").read_text()
    table = guide.split("<!-- mcp-scopes:start -->")[1].split(
        "<!-- mcp-scopes:end -->"
    )[0]
    documented = re.findall(r"^\| `([^`]+)` \|", table, re.MULTILINE)
    assert len(documented) == len(set(documented))
    assert set(documented) == capabilities
