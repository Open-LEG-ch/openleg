# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contract tests for docs/architecture.md.

The doc drifted from the code once already (it described an app factory that
never existed). These tests pin the claims that are cheap to verify.
"""

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DOC_PATH = PROJECT_ROOT / "docs" / "architecture.md"
APP_PATH = PROJECT_ROOT / "app.py"

BLUEPRINTS = {
    "main_bp": "app.py",
    "municipality_bp": "municipality.py",
    "pilot_bp": "municipality.py",
    "public_api_bp": "api_public.py",
    "health_bp": "health.py",
    "utility_bp": "utility_portal.py",
    "rangliste_bp": "rangliste.py",
    "registry_bp": "leg_registry.py",
    "self_host_bp": "self_host.py",
    "admin_bp": "admin.py",
}


def _doc_text() -> str:
    return DOC_PATH.read_text(encoding="utf-8")


def test_architecture_doc_exists() -> None:
    assert DOC_PATH.exists(), "docs/architecture.md must exist"


def test_doc_matches_how_the_flask_app_is_actually_built() -> None:
    """The doc claimed an app factory for a module-level app. Pin it to reality."""
    app_source = APP_PATH.read_text(encoding="utf-8")
    has_factory = bool(re.search(r"^def create_app\(", app_source, re.MULTILINE))
    lowered = _doc_text().lower()
    if has_factory:
        assert "app factory" in lowered, "app.py grew a factory; document it"
        # Without this the check is vacuous: "no app factory" contains the
        # substring "app factory", so a doc claiming the opposite passed.
        assert "no app factory" not in lowered, (
            "app.py defines create_app, so the doc must not deny a factory"
        )
    else:
        assert "no app factory" in lowered, (
            "app.py builds a module-level Flask app, so the doc must say so"
        )


def test_doc_states_that_startup_fails_without_a_database() -> None:
    """create_app raises rather than degrading, and the doc has to say which.

    An operator who reads "degrades to JSON storage" will look for a silently
    wrong app instead of the RuntimeError they actually get.
    """
    app_source = APP_PATH.read_text(encoding="utf-8")
    assert "PostgreSQL required" in app_source, (
        "startup must keep failing explicitly when PostgreSQL is unavailable"
    )

    lowered = _doc_text().lower()
    assert "degrades to json" not in lowered, (
        "create_app raises when the database is unavailable; it does not degrade"
    )
    assert "runtimeerror" in lowered or "refuses to start" in lowered, (
        "the doc must state that startup fails without a database"
    )


def test_doc_documents_the_store_seam() -> None:
    text = _doc_text()
    assert "store/" in text
    assert "get_connection" in text, "The connection seam is the key data-layer idea"


def test_doc_lists_every_registered_blueprint_module() -> None:
    text = _doc_text()
    missing = [
        name
        for name, module in BLUEPRINTS.items()
        if name not in text or module not in text
    ]
    assert missing == [], f"Blueprint documentation is incomplete: {missing}"


def test_blueprint_contract_matches_the_full_registration_set() -> None:
    source = APP_PATH.read_text(encoding="utf-8")
    block = re.search(
        r"for blueprint in \((.*?)\):\s*application\.register_blueprint",
        source,
        re.DOTALL,
    )
    assert block is not None, "create_app blueprint registration loop is missing"
    registered = set(re.findall(r"\b[a-z_]+_bp\b", block.group(1)))
    assert registered == set(BLUEPRINTS)


def test_doc_covers_multi_tenant_host_resolution() -> None:
    text = _doc_text()
    assert "tenant.py" in text
    assert "openleg.ch" in text, "Document how a hostname maps to a territory"


def test_doc_records_the_store_extraction_order() -> None:
    text = _doc_text().lower()
    assert "extraction" in text, (
        "The deepening roadmap moved here from the private prd/ path"
    )


def test_documented_route_prefixes_match_the_blueprints() -> None:
    text = _doc_text()
    for prefix in ("/gemeinde", "/api/v1", "/utility", "/pilotgemeinde"):
        assert prefix in text, f"Route map must document the {prefix} prefix"


def test_doc_keeps_operations_out_of_the_public_repo() -> None:
    """Naming the boundary is fine; carrying secrets or host inventory is not."""
    lowered = _doc_text().lower()
    for leak in ("password", "api_key=", "secret=", "ssh ", "ansible"):
        assert leak not in lowered, f"docs/architecture.md must not carry {leak}"
    assert not re.search(r"\b\d{1,3}(\.\d{1,3}){3}\b", lowered), (
        "No production host addresses in the public repo"
    )


def test_doc_matches_billing_orchestration_boundary() -> None:
    """The doc must describe the billing seam that app.py actually wires up.

    app.py drives billing_runner from a cron route and per-community routes. A
    doc that calls the billing code uncalled, or its period table empty, sends a
    reader looking for work that is already shipped, and hides the work that is
    genuinely missing: there is no operator UI or invoice PDF.
    """
    app_source = APP_PATH.read_text(encoding="utf-8")
    assert "billing_runner.run_billing_period" in app_source, (
        "app.py must call billing_runner.run_billing_period"
    )
    assert "/api/cron/process-billing" in app_source, (
        "app.py must expose the /api/cron/process-billing route"
    )
    assert "/api/billing/community/" in app_source, (
        "app.py must expose the /api/billing/community/ routes"
    )

    lowered = " ".join(_doc_text().lower().split())
    assert "billing_runner.py" in lowered, (
        "docs/architecture.md must name billing_runner.py as the billing orchestrator"
    )
    assert "/api/cron/process-billing" in lowered, (
        "docs/architecture.md must document the /api/cron/process-billing route"
    )
    assert "/api/billing/community/" in lowered, (
        "docs/architecture.md must document the /api/billing/community/ routes"
    )
    assert "no member or operator ui" in lowered, (
        "docs/architecture.md must state that no member or operator UI exists yet"
    )
    assert "no invoice pdf" in lowered, (
        "docs/architecture.md must state that no invoice PDF exists yet"
    )

    assert "has no callers" not in lowered, (
        "docs/architecture.md must not claim the billing code has no callers"
    )
    assert not re.search(r"`?billing_periods`?\s+is empty", lowered), (
        "docs/architecture.md must not claim billing_periods is empty"
    )
