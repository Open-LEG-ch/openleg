# SPDX-License-Identifier: AGPL-3.0-or-later

# Squared degrees: float noise is ~1e-19; few-metre triangles are ~1e-10.
_COLLINEAR_EPSILON = 1e-15


def convex_hull(points: list[list[float]]) -> list[list[float]] | None:
    """Return the outer points counterclockwise, or ``None`` for degenerate input.

    ``None`` means fewer than three distinct points or all points collinear
    within ``_COLLINEAR_EPSILON``.
    """
    unique = {}
    for point in points:
        unique.setdefault(tuple(point), point)
    ordered = sorted(unique.items())
    if len(ordered) < 3:
        return None

    def cross(origin, a, b):
        left = (a[0] - origin[0]) * (b[1] - origin[1])
        right = (a[1] - origin[1]) * (b[0] - origin[0])
        cross_product = left - right
        return 0 if abs(cross_product) < _COLLINEAR_EPSILON else cross_product

    def half(scan):
        result = []
        for coordinates, point in scan:
            while (
                len(result) >= 2
                and cross(result[-2][0], result[-1][0], coordinates) <= 0
            ):
                result.pop()
            result.append((coordinates, point))
        return result

    hull = half(ordered)[:-1] + half(reversed(ordered))[:-1]
    return [point for _, point in hull] if len(hull) >= 3 else None
