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
    "registry_api_bp": "leg_registry.py",
    "public_api_bp": "api_public.py",
    "health_bp": "health.py",
    "utility_bp": "utility_portal.py",
    "admin_bp": "admin.py",
    "cron_bp": "cron.py",
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


def test_extraction_order_does_not_list_already_extracted_stores() -> None:
    """A store listed as 'Remaining in database.py' must not already be shipped."""
    text = _doc_text()
    extraction_section = re.search(
        r"### Extraction order\n\n(.*?)\n\n`_create_tables\(\)`",
        text,
        re.DOTALL,
    )
    if extraction_section is None:
        # The list is empty because the extraction finished (#334). The doc has
        # to say so, otherwise a reader cannot tell "done" from "undocumented".
        assert "extraction is finished" in text.lower()
        return

    shipped = []
    for match in re.finditer(
        r"^\d+\.\s+`store/(?P<name>[a-z_]+)`",
        extraction_section.group(1),
        re.MULTILINE,
    ):
        name = match.group("name")
        store_path = PROJECT_ROOT / "store" / f"{name}.py"
        re_exported = (
            f"from store.{name} import" in APP_PATH.with_name("database.py").read_text()
        )
        if store_path.exists() and re_exported:
            shipped.append(name)

    assert shipped == [], (
        f"docs/architecture.md lists already-extracted stores as remaining: {shipped}"
    )


def test_documented_route_prefixes_match_the_blueprints() -> None:
    text = _doc_text()
    for prefix in ("/gemeinde", "/api/v1", "/utility"):
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
    """The doc must describe the billing seam the app actually wires up.

    The app drives billing_runner from a cron route and per-community routes,
    and a confirmed community admin approves a reconciled draft through
    billing_approval.py and store.billing's approval seam. A member then reads
    their own issued invoices and downloads a PDF through /dashboard/invoices
    (member_invoices.py). A doc that calls the billing code uncalled, its
    period table empty, its surface read-only, or invoice PDF download
    unbuilt sends a reader looking for work that is already shipped.

    Asserted against the registered route map rather than one module's source,
    because the cron surface moved into its own blueprint (#335) and the check
    should follow the route, not the file.
    """
    import app as app_module

    application = app_module.create_app(
        {
            "TESTING": True,
            "RATELIMIT_STORAGE_URI": "memory://",
            "APP_BASE_URL": "http://localhost",
        },
        load_environment=False,
        check_database=False,
    )
    registered = {str(rule) for rule in application.url_map.iter_rules()}

    assert "/api/cron/process-billing" in registered
    assert any(rule.startswith("/api/billing/community/") for rule in registered)
    assert "/dashboard/invoices" in registered
    assert "/dashboard/invoices/<int:invoice_id>" in registered
    assert "/dashboard/invoices/<int:invoice_id>/pdf" in registered

    cron_source = (PROJECT_ROOT / "cron.py").read_text(encoding="utf-8")
    assert "billing_runner.run_billing_period" in cron_source, (
        "the billing cron must call billing_runner.run_billing_period"
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
    assert "billing_approval.py" in lowered, (
        "docs/architecture.md must name billing_approval.py as the approval validator"
    )
    assert "approve_billing_period" in lowered, (
        "docs/architecture.md must name the store.billing approval seam"
    )
    assert "/leg/community/<community_id>/billing" in lowered, (
        "docs/architecture.md must document the billing approval workspace"
    )
    assert "read-only audit" in lowered, (
        "docs/architecture.md must keep the admin audit view read-only"
    )
    assert "no invoice pdf" not in lowered, (
        "docs/architecture.md must not claim invoice PDF download is still "
        "missing now that /dashboard/invoices ships it"
    )
    assert "/dashboard/invoices" in lowered, (
        "docs/architecture.md must document the private member invoice routes"
    )
    assert "member_invoices.py" in lowered, (
        "docs/architecture.md must name member_invoices.py as the member "
        "invoice display/PDF model"
    )
    assert "immutable" in lowered and "snapshot" in lowered, (
        "docs/architecture.md must say the member view renders only the "
        "frozen invoice snapshot, never a recomputation"
    )

    assert "has no callers" not in lowered, (
        "docs/architecture.md must not claim the billing code has no callers"
    )
    assert not re.search(r"`?billing_periods`?\s+is empty", lowered), (
        "docs/architecture.md must not claim billing_periods is empty"
    )
