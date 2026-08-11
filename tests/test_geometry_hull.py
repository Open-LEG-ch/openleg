# SPDX-License-Identifier: AGPL-3.0-or-later
"""Cluster polygons come from a pure Python convex hull, not from SciPy.

SciPy was a full scientific dependency pulled in for a single call site. The
hull it computed is small, well defined and deterministic, so the behaviour it
produced is pinned here and served by ``geometry.convex_hull``.
"""

import os
from unittest.mock import patch

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SQUARE = [[47.0, 8.0], [47.0, 8.01], [47.01, 8.01], [47.01, 8.0]]
INTERIOR = [47.005, 8.005]
COLLINEAR = [[47.0, 8.0], [47.001, 8.001], [47.002, 8.002]]


class TestConvexHull:
    def test_hull_keeps_only_the_outer_points(self):
        import geometry

        hull = geometry.convex_hull(SQUARE + [INTERIOR])

        assert hull is not None
        assert INTERIOR not in hull
        assert {tuple(point) for point in hull} == {tuple(p) for p in SQUARE}

    def test_hull_walks_the_ring_in_order(self):
        import geometry

        hull = geometry.convex_hull(SQUARE + [INTERIOR])

        # Each step moves along one edge of the square, never across a diagonal.
        for current, following in zip(hull, hull[1:] + hull[:1]):
            shared_axis = current[0] == following[0] or current[1] == following[1]
            assert shared_axis, f"{current} to {following} cuts across the square"

    def test_hull_returns_none_for_degenerate_input(self):
        import geometry

        assert geometry.convex_hull(COLLINEAR) is None
        assert geometry.convex_hull([[47.0, 8.0], [47.0, 8.0]]) is None

    def test_hull_points_are_returned_unchanged(self):
        import geometry

        hull = geometry.convex_hull(SQUARE)

        assert all(isinstance(point, list) for point in hull)
        assert all(len(point) == 2 for point in hull)


class TestClusterPolygon:
    @pytest.fixture
    def create_simple_polygon(self):
        with patch("database.is_db_available", return_value=True):
            import app as app_module

        return app_module.create_simple_polygon

    def test_polygon_closes_the_hull_ring(self, create_simple_polygon):
        polygon = create_simple_polygon(SQUARE + [INTERIOR])

        assert len(polygon) == 5
        assert polygon[0] == polygon[-1]
        assert INTERIOR not in polygon
        assert {tuple(point) for point in polygon[:-1]} == {tuple(p) for p in SQUARE}

    def test_polygon_falls_back_to_a_padded_box_when_the_hull_degenerates(
        self, create_simple_polygon
    ):
        polygon = create_simple_polygon(COLLINEAR)

        assert len(polygon) == 5
        assert polygon[0] == polygon[-1]
        lats = [point[0] for point in polygon]
        lons = [point[1] for point in polygon]
        assert min(lats) == pytest.approx(47.0 - 0.0003)
        assert max(lats) == pytest.approx(47.002 + 0.0003)
        assert min(lons) == pytest.approx(8.0 - 0.0003)
        assert max(lons) == pytest.approx(8.002 + 0.0003)

    def test_single_and_paired_coordinates_keep_their_shapes(
        self, create_simple_polygon
    ):
        assert len(create_simple_polygon([[47.0, 8.0]])) == 5
        assert len(create_simple_polygon([[47.0, 8.0], [47.01, 8.01]])) == 5


class TestSciPyIsGone:
    def test_requirements_do_not_pin_scipy(self):
        with open(os.path.join(PROJECT_ROOT, "requirements.txt")) as handle:
            assert "scipy" not in handle.read().lower()

    def test_app_does_not_import_scipy(self):
        with open(os.path.join(PROJECT_ROOT, "app.py")) as handle:
            content = handle.read()
        assert "scipy" not in content
        assert "ConvexHull" not in content
        assert "HAS_SCIPY" not in content
