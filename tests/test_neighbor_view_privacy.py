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
import math
import os
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import database
from tests.consent_visibility import filters_by_consent

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


def test_provisional_match_includes_the_150_metre_boundary():
    neighbor_view = importlib.import_module("neighbor_view")

    with (
        patch.object(
            database, "get_all_building_profiles", return_value=[NEIGHBOUR_PROFILES[0]]
        ),
        patch.object(neighbor_view.ml_models, "calculate_distance", return_value=150),
        patch.object(
            neighbor_view.ml_models,
            "calculate_community_autarky",
            return_value=(0.5, 0, 0),
        ),
    ):
        summary = neighbor_view.find_provisional_matches(dict(CALLER_PROFILE))

    assert summary is not None
    assert summary["num_members"] == 2


def test_provisional_match_excludes_a_point_beyond_150_metres():
    neighbor_view = importlib.import_module("neighbor_view")

    with (
        patch.object(
            database, "get_all_building_profiles", return_value=[NEIGHBOUR_PROFILES[0]]
        ),
        patch.object(neighbor_view.ml_models, "calculate_distance", return_value=150.5),
    ):
        summary = neighbor_view.find_provisional_matches(dict(CALLER_PROFILE))

    assert summary is None


def test_provisional_match_summary_reports_members_and_autarky_percentage():
    neighbor_view = importlib.import_module("neighbor_view")
    seen_buildings = []

    def fixed_autarky(community, _profiles):
        seen_buildings.extend(community["building_id"].tolist())
        return 0.5, 0, 0

    with (
        patch.object(
            database, "get_all_building_profiles", return_value=[NEIGHBOUR_PROFILES[0]]
        ),
        patch.object(neighbor_view.ml_models, "calculate_distance", return_value=10),
        patch.object(
            neighbor_view.ml_models,
            "calculate_community_autarky",
            side_effect=fixed_autarky,
        ),
    ):
        summary = neighbor_view.find_provisional_matches(dict(CALLER_PROFILE))

    assert seen_buildings == ["", "neighbour-one"]
    assert summary == {
        "community_id": "provisional",
        "num_members": 2,
        "autarky_percent": 50.0,
    }


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
        if filters_by_consent(self.query):
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
    assert {row["building_id"] for row in visibility_cursor.fetchall()} == {
        "consented",
        "revoked",
        "missing",
    }


def test_profile_visibility_double_sees_through_an_outer_join(visibility_cursor):
    """A LEFT JOIN with the predicate in its ON clause keeps everyone; say so."""
    visibility_cursor.execute(
        """
        SELECT b.building_id FROM buildings b
        LEFT JOIN consents c ON b.building_id = c.building_id
        AND c.share_with_neighbors = TRUE
        WHERE b.verified = TRUE
        """
    )
    assert {row["building_id"] for row in visibility_cursor.fetchall()} == {
        "consented",
        "revoked",
        "missing",
    }


def test_building_profiles_read_excludes_revoked_and_missing_consent(
    visibility_cursor,
):
    visible = database.get_all_building_profiles()

    assert [row["building_id"] for row in visible] == ["consented"]


def test_the_city_scoped_read_is_gated_too(visibility_cursor):
    """Both branches of the query carry the gate, not only the unscoped one."""
    visible = database.get_all_building_profiles(city_id="baden")

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
    ".worktrees",
    "__pycache__",
    "archive",
    "mutants",
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


# ---------------------------------------------------------------------------
# The jitter that hides the exact address
# ---------------------------------------------------------------------------


def _haversine_meters(lat1, lon1, lat2, lon2):
    """Great-circle distance in metres between two WGS84 points."""
    earth_radius = 6_378_137.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * earth_radius * math.asin(math.sqrt(a))


def test_deterministic_seed_jitters_reproducibly_within_the_anonymity_radius():
    neighbor_view = importlib.import_module("neighbor_view")
    lat, lon = 47.3700, 8.5400

    first = neighbor_view.jitter_coordinates(lat, lon, seed="neighbour-one")
    second = neighbor_view.jitter_coordinates(lat, lon, seed="neighbour-one")

    assert first == second, "a fixed seed must reproduce the same jittered point"
    assert first != (lat, lon), "the jittered point must not be the stored coordinate"
    assert first == pytest.approx((47.370086804814235, 8.540551330974948)), (
        "the seeded jitter must land on the known fixed point for this input"
    )

    displacement = _haversine_meters(lat, lon, first[0], first[1])
    assert displacement <= neighbor_view.ANONYMITY_RADIUS_METERS, (
        "the jitter must not carry the point beyond the anonymity radius"
    )


@pytest.mark.parametrize(
    ("lat", "lon", "radius_meters"),
    [
        (None, 8.54, 120),
        (47.37, None, 120),
        (47.37, 8.54, 0),
        (47.37, 8.54, -1),
    ],
)
def test_jitter_coordinates_invalid_jitter_input_returns_it_unchanged(
    lat, lon, radius_meters
):
    """A missing coordinate or a non-positive radius must pass through
    unchanged: the guard returns the inputs as-is instead of raising."""
    neighbor_view = importlib.import_module("neighbor_view")

    assert neighbor_view.jitter_coordinates(lat, lon, radius_meters) == (lat, lon)


def test_jitter_coordinates_a_small_positive_radius_still_jitters():
    neighbor_view = importlib.import_module("neighbor_view")
    rng = MagicMock()
    rng.random.return_value = 0.25
    rng.uniform.return_value = math.pi / 4

    with patch.object(neighbor_view.np.random, "default_rng", return_value=rng):
        jittered = neighbor_view.jitter_coordinates(47.37, 8.54, 1, seed="tiny")

    assert jittered != (47.37, 8.54), (
        "a positive radius must displace the point, not pass it through"
    )
    assert _haversine_meters(47.37, 8.54, *jittered) <= 1, (
        "even a one-metre radius must respect its own bound"
    )


def test_jitter_coordinates_a_non_string_seed_stays_deterministic():
    neighbor_view = importlib.import_module("neighbor_view")

    first = neighbor_view.jitter_coordinates(47.37, 8.54, seed=123)
    second = neighbor_view.jitter_coordinates(47.37, 8.54, seed=123)
    assert first == second, "a non-string seed must still pin the jitter"

    other = neighbor_view.jitter_coordinates(47.37, 8.54, seed=456)
    assert first != other, "different seeds must not share one jittered point"


def test_jitter_coordinates_an_unseeded_call_still_jitters():
    neighbor_view = importlib.import_module("neighbor_view")
    rng = MagicMock()
    rng.random.return_value = 0.25
    rng.uniform.return_value = math.pi / 4

    with patch.object(
        neighbor_view.np.random, "default_rng", return_value=rng
    ) as make_rng:
        jittered = neighbor_view.jitter_coordinates(47.37, 8.54)

    make_rng.assert_called_once_with(None)
    assert jittered != (47.37, 8.54)
    assert _haversine_meters(47.37, 8.54, *jittered) <= (
        neighbor_view.ANONYMITY_RADIUS_METERS
    )


def test_jitter_coordinates_at_the_pole_uses_a_safe_longitude_displacement():
    neighbor_view = importlib.import_module("neighbor_view")
    lat, lon = 90.0, 8.54

    jittered = neighbor_view.jitter_coordinates(lat, lon, seed="polar")

    assert all(math.isfinite(value) for value in jittered)
    assert jittered != (lat, lon), "the pole must not freeze the jitter"
    assert abs(jittered[1] - lon) < 0.01, "the pole fallback must remain bounded"


def test_jitter_coordinates_handles_boundary_coordinates():
    neighbor_view = importlib.import_module("neighbor_view")

    for lat, lon, seed in [
        (0.0, 8.54, "equator"),
        (47.37, 180.0, "antimeridian"),
        (47.37, -180.0, "antimeridian-west"),
    ]:
        first = neighbor_view.jitter_coordinates(lat, lon, seed=seed)
        second = neighbor_view.jitter_coordinates(lat, lon, seed=seed)
        assert first == second, f"{seed}: a fixed seed must reproduce the point"
        assert first != (lat, lon), f"{seed}: the point must be displaced"
        assert _haversine_meters(lat, lon, *first) <= (
            neighbor_view.ANONYMITY_RADIUS_METERS
        ), f"{seed}: the displacement must stay inside the anonymity radius"


def test_jitter_coordinates_normalizes_geographic_boundaries():
    neighbor_view = importlib.import_module("neighbor_view")
    rng = MagicMock()
    rng.random.return_value = 1.0

    for lat, lon, angle in [
        (90.0, 8.54, math.pi),
        (47.37, 180.0, math.pi / 2),
        (47.37, -180.0, 3 * math.pi / 2),
    ]:
        rng.uniform.return_value = angle
        with patch.object(neighbor_view.np.random, "default_rng", return_value=rng):
            jittered = neighbor_view.jitter_coordinates(lat, lon)

        assert all(math.isfinite(value) for value in jittered)
        assert jittered != (lat, lon)
        assert -90 <= jittered[0] <= 90
        assert -180 <= jittered[1] <= 180
        assert _haversine_meters(lat, lon, *jittered) <= (
            neighbor_view.ANONYMITY_RADIUS_METERS + 1e-6
        )


# ---------------------------------------------------------------------------
# The map a fresh registration receives
# ---------------------------------------------------------------------------


def test_collect_building_locations_omits_the_excluded_building():
    """registration.py hands the caller a map of others; the caller's own
    building must not be representable anywhere in that result."""
    neighbor_view = importlib.import_module("neighbor_view")
    buildings = [
        {
            "building_id": "map-caller",
            "lat": 47.3700,
            "lon": 8.5400,
            "user_type": "owner",
            "verified": True,
        },
        {
            "building_id": "map-other",
            "lat": 47.3701,
            "lon": 8.5401,
            "user_type": "tenant",
            "verified": True,
        },
    ]

    with patch.object(neighbor_view.db, "get_all_buildings", return_value=buildings):
        locations = neighbor_view.collect_building_locations(
            exclude_building_id="map-caller"
        )

    other_point = neighbor_view.jitter_coordinates(47.3701, 8.5401, seed="map-other")
    assert locations == [
        {"lat": other_point[0], "lon": other_point[1], "type": "tenant"}
    ], "exactly the non-excluded building must come back, jittered and typed"

    excluded_point = neighbor_view.jitter_coordinates(
        47.3700, 8.5400, seed="map-caller"
    )
    coordinates = {(loc["lat"], loc["lon"]) for loc in locations}
    assert excluded_point not in coordinates, (
        "the jitter is deterministic per building_id, so this point is the only "
        "way the excluded record could appear; it must not appear"
    )


def test_collect_building_locations_skips_rows_with_incomplete_coordinates():
    """buildings.lat and buildings.lon are nullable: a row missing either must
    be dropped from the map, leaving the complete rows jittered and typed."""
    neighbor_view = importlib.import_module("neighbor_view")
    buildings = [
        {
            "building_id": "row-without-lat",
            "lat": None,
            "lon": 8.5400,
            "user_type": "owner",
        },
        {
            "building_id": "row-complete",
            "lat": 47.3700,
            "lon": 8.5400,
            "user_type": "tenant",
        },
        {
            "building_id": "row-without-lon",
            "lat": 47.3701,
            "lon": None,
            "user_type": "owner",
        },
    ]

    with patch.object(neighbor_view.db, "get_all_buildings", return_value=buildings):
        locations = neighbor_view.collect_building_locations()

    assert len(locations) == 1, (
        "a missing latitude or longitude must drop that building from the map"
    )
    complete = locations[0]
    assert complete["type"] == "tenant"
    assert (complete["lat"], complete["lon"]) != (47.3700, 8.5400), (
        "the exact stored coordinate must not reach the map; it must be jittered"
    )


def test_collect_building_locations_passes_the_requested_city_to_the_read():
    """A city-scoped map must scope the read itself, not filter afterwards."""
    neighbor_view = importlib.import_module("neighbor_view")
    buildings = [
        {
            "building_id": "map-baden",
            "lat": 47.3700,
            "lon": 8.5400,
            "user_type": "owner",
        },
    ]
    read = MagicMock(return_value=buildings)

    with patch.object(neighbor_view.db, "get_all_buildings", read):
        locations = neighbor_view.collect_building_locations(city_id="baden")

    read.assert_called_once_with(city_id="baden")
    point = neighbor_view.jitter_coordinates(47.3700, 8.5400, seed="map-baden")
    assert locations == [{"lat": point[0], "lon": point[1], "type": "owner"}], (
        "exactly the supplied building must come back, jittered and typed"
    )


def test_collect_building_locations_defaults_to_anonymous_without_user_type():
    """A complete building row can carry no user_type at all: it must still
    reach the map, typed anonymous instead of raising or leaking a key."""
    neighbor_view = importlib.import_module("neighbor_view")
    buildings = [
        {
            "building_id": "map-untyped",
            "lat": 47.3700,
            "lon": 8.5400,
        },
    ]

    with patch.object(neighbor_view.db, "get_all_buildings", return_value=buildings):
        locations = neighbor_view.collect_building_locations()

    assert len(locations) == 1, (
        "a complete row without user_type must not be dropped from the map"
    )
    assert locations[0]["type"] == "anonymous", (
        "a building with no user_type must be typed anonymous"
    )
