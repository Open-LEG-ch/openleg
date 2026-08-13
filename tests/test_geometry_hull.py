# SPDX-License-Identifier: AGPL-3.0-or-later
"""Cluster polygons come from a pure Python convex hull, not from SciPy.

SciPy was a full scientific dependency pulled in for a single call site. The
hull it computed is small, well defined and deterministic, so the behaviour it
produced is pinned here and served by ``geometry.convex_hull``.
"""

import os

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

        assert hull is not None
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

        assert hull is not None
        assert all(isinstance(point, list) for point in hull)
        assert all(len(point) == 2 for point in hull)


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
