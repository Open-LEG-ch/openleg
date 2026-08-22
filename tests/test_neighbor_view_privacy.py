# SPDX-License-Identifier: AGPL-3.0-or-later
"""The neighbour view must not disclose who the neighbours are or exactly where.

`/api/check_potential` is unauthenticated. It once answered with the
`building_id` and the raw coordinates of every verified registration within
150 m of any address a caller typed, while the map path through
`collect_building_locations` jittered the same coordinates by 120 m first.
The read that fed it, `get_all_building_profiles`, carried no consent gate,
so a resident who revoked neighbour sharing was disclosed anyway.
"""

import ast
import importlib
import os
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import database

ROOT = Path(__file__).resolve().parents[1]

NEIGHBOUR_PROFILES = [
    {
        "building_id": "neighbour-one",
        "address": "Bahnhofstrasse 3, 8001 Zürich",
        "lat": 47.3700,
        "lon": 8.5400,
        "plz": "8001",
        "building_type": "mfh",
        "annual_consumption_kwh": 12000,
        "potential_pv_kwp": 14.0,
        "user_type": "owner",
    },
    {
        "building_id": "neighbour-two",
        "address": "Bahnhofstrasse 5, 8001 Zürich",
        "lat": 47.3701,
        "lon": 8.5401,
        "plz": "8001",
        "building_type": "efh",
        "annual_consumption_kwh": 4500,
        "potential_pv_kwp": 8.0,
        "user_type": "owner",
    },
]

CALLER_PROFILE = {
    "building_id": "",
    "address": "Bahnhofstrasse 1, 8001 Zürich",
    "lat": 47.3699,
    "lon": 8.5399,
    "annual_consumption_kwh": 5000,
    "potential_pv_kwp": 9.0,
}


def _leaves(value):
    """Yield every (key, value) pair anywhere inside a JSON-ish structure."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield key, item
            yield from _leaves(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _leaves(item)


# ---------------------------------------------------------------------------
# The summary a stranger receives
# ---------------------------------------------------------------------------


def test_provisional_match_summary_carries_no_member_identities():
    neighbor_view = importlib.import_module("neighbor_view")

    with patch.object(
        database, "get_all_building_profiles", return_value=NEIGHBOUR_PROFILES
    ):
        summary = neighbor_view.find_provisional_matches(dict(CALLER_PROFILE))

    assert summary is not None, "two neighbours within 150 m must still match"
    assert set(summary) == {"community_id", "num_members", "autarky_percent"}, (
        "the provisional match may report how many and how autark, nothing else"
    )
    for key, item in _leaves(summary):
        assert key not in {"members", "building_id", "lat", "lon", "address"}, (
            f"{key!r} identifies a neighbour and must not leave the server"
        )
        assert item not in {47.3700, 8.5400, 47.3701, 8.5401}, (
            "a raw neighbour coordinate reached the summary"
        )


def test_a_neighbour_without_coordinates_cannot_break_the_match():
    """buildings.lat is nullable: collect_building_locations already skips those."""
    neighbor_view = importlib.import_module("neighbor_view")
    profiles = [
        {**NEIGHBOUR_PROFILES[0], "lat": None},
        {**NEIGHBOUR_PROFILES[1], "lon": ""},
        NEIGHBOUR_PROFILES[0],
    ]

    with patch.object(database, "get_all_building_profiles", return_value=profiles):
        summary = neighbor_view.find_provisional_matches(dict(CALLER_PROFILE))

    assert summary["num_members"] == 2


def test_a_caller_without_coordinates_gets_no_match_rather_than_an_error():
    neighbor_view = importlib.import_module("neighbor_view")

    with patch.object(
        database, "get_all_building_profiles", return_value=NEIGHBOUR_PROFILES
    ):
        assert neighbor_view.find_provisional_matches({"lat": None, "lon": 8.5}) is None
        assert neighbor_view.find_provisional_matches({}) is None


# ---------------------------------------------------------------------------
# The route a stranger calls
# ---------------------------------------------------------------------------


def _disable_rate_limit_hooks(flask_app):
    hooks = list(flask_app.before_request_funcs.get(None, []))
    flask_app.before_request_funcs[None] = [
        hook
        for hook in hooks
        if not (
            getattr(hook, "__module__", "").startswith("flask_limiter")
            or getattr(hook, "__name__", "") == "_check_request_limit"
        )
    ]
    return hooks


@pytest.fixture
def full_app_module():
    with (
        patch.dict(
            os.environ,
            {
                "DATABASE_URL": "postgresql://x:x@localhost/x",
                "REDIS_URL": "memory://",
                "CRON_SECRET": "test-cron-secret",
                "APP_BASE_URL": "http://localhost:5003",
            },
        ),
        patch("database.is_db_available", return_value=True),
        patch("database._connection_pool", MagicMock()),
    ):
        import app as app_module

        app_module = importlib.reload(app_module)
        app_module.web = app_module.create_app(load_environment=False)
        hooks = _disable_rate_limit_hooks(app_module.web)
        try:
            yield app_module
        finally:
            app_module.web.before_request_funcs[None] = hooks


def test_check_potential_answers_without_naming_or_locating_neighbours(
    full_app_module, monkeypatch
):
    app_module = full_app_module
    monkeypatch.setattr(
        app_module.data_enricher,
        "get_energy_profile_for_address",
        lambda _address: (dict(CALLER_PROFILE), None),
    )

    with patch.object(
        database, "get_all_building_profiles", return_value=NEIGHBOUR_PROFILES
    ):
        response = app_module.web.test_client().post(
            "/api/check_potential", json={"address": "Bahnhofstrasse 1, 8001 Zürich"}
        )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["potential"] is True, "the match itself is the product; keep it"

    cluster_info = payload["cluster_info"]
    assert "members" not in cluster_info
    for key, item in _leaves(cluster_info):
        assert key not in {"building_id", "lat", "lon", "address"}
        assert item not in {"neighbour-one", "neighbour-two"}
        assert item not in {47.3700, 8.5400, 47.3701, 8.5401}


# ---------------------------------------------------------------------------
# The read underneath
# ---------------------------------------------------------------------------


class _ProfileVisibilityCursor:
    """A double that filters only when the query really states the predicate."""

    buildings = (
        {"building_id": "consented", "lat": 47.1, "lon": 8.1, "city_id": "baden"},
        {"building_id": "revoked", "lat": 47.2, "lon": 8.2, "city_id": "baden"},
        {"building_id": "missing", "lat": 47.3, "lon": 8.3, "city_id": "baden"},
    )

    def __init__(self):
        self.consents = {"consented": True, "revoked": False}

    def execute(self, query, params=None):
        self.query = " ".join(query.split())
        self.params = params or ()

    def _visible(self):
        rows = list(self.buildings)
        if (
            "JOIN consents" in self.query
            and "share_with_neighbors = TRUE" in self.query
        ):
            rows = [
                row for row in rows if self.consents.get(row["building_id"]) is True
            ]
        return rows

    def fetchall(self):
        return self._visible()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _ProfileVisibilityConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


@pytest.fixture
def visibility_cursor(monkeypatch):
    cursor = _ProfileVisibilityCursor()

    @contextmanager
    def connection():
        yield _ProfileVisibilityConnection(cursor)

    monkeypatch.setattr(database, "get_connection", connection)
    return cursor


def test_profile_visibility_double_requires_the_predicate(visibility_cursor):
    """The double must fail if production joins consents but drops the predicate."""
    visibility_cursor.execute(
        """
        SELECT b.building_id FROM buildings b
        INNER JOIN consents c ON b.building_id = c.building_id
        WHERE b.verified = TRUE
        """
    )
    assert any(row["building_id"] == "revoked" for row in visibility_cursor.fetchall())


def test_building_profiles_read_excludes_revoked_and_missing_consent(
    visibility_cursor,
):
    visible = database.get_all_building_profiles()

    assert [row["building_id"] for row in visible] == ["consented"]


def test_operator_profile_read_is_named_apart_and_stays_ungated(visibility_cursor):
    """Operators still see every registration; the name says so out loud."""
    visible = database.get_operator_building_profiles()

    assert {row["building_id"] for row in visible} == {
        "consented",
        "revoked",
        "missing",
    }


def test_admin_surface_uses_the_operator_read():
    source = (ROOT / "admin.py").read_text(encoding="utf-8")

    assert "get_operator_building_profiles" in source
    assert "get_all_building_profiles" not in source


# ---------------------------------------------------------------------------
# One home for the policy
# ---------------------------------------------------------------------------

POLICY_NAMES = {
    "ANONYMITY_RADIUS_METERS",
    "jitter_coordinates",
    "collect_building_locations",
    "find_provisional_matches",
}

SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "archive",
    "node_modules",
    "private",
    "scripts",
    "tests",
}


def _product_modules():
    for path in ROOT.rglob("*.py"):
        if SKIP_DIRS.intersection(path.relative_to(ROOT).parts):
            continue
        yield path


def test_the_neighbour_policy_is_defined_once_in_neighbor_view():
    homes = {name: [] for name in POLICY_NAMES}
    for path in _product_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in POLICY_NAMES:
                homes[node.name].append(path.name)
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in POLICY_NAMES:
                        homes[target.id].append(path.name)

    assert homes == {name: ["neighbor_view.py"] for name in POLICY_NAMES}, (
        "the anonymity policy must have exactly one home"
    )
