# SPDX-License-Identifier: AGPL-3.0-or-later
"""Gemeindeprofil cache contract (#527).

The assembled profile read model is one cache unit per municipality. The
freshness bound is set by veracity, not preference: a successful refresh
invalidates the unit through the shared cache, and the TTL is only a
backstop that bounds staleness when invalidation itself fails. Hits and
misses are observable so the claimed win is measurable; cache unavailability
degrades to direct reads.
"""

from datetime import UTC, datetime
from decimal import Decimal
from unittest import mock

import pytest

import municipality_profile
import public_data
from municipality_profile import profile_cache_metrics

BFS = 261


def _profile(**overrides):
    base = {
        "bfs_number": BFS,
        "name": "Dietikon",
        "kanton": "ZH",
        "solar_potential_pct": Decimal("45.00"),
        "data_sources": {"elcom": True, "last_refresh": "2026-01-01T00:00:00Z"},
    }
    base.update(overrides)
    return base


def _context(profile):
    return {
        "profile": profile,
        "tariffs": [{"category": "H4", "total_rp_kwh": Decimal("26.50")}],
        "solar": None,
        "value_gap": {"annual_savings_chf": 171.0},
        "created": datetime(2026, 1, 1, tzinfo=UTC),
    }


@pytest.fixture(autouse=True)
def fresh_metrics(monkeypatch):
    municipality_profile._reset_profile_cache_metrics_for_tests()
    yield
    municipality_profile._reset_profile_cache_metrics_for_tests()


@pytest.fixture(autouse=True)
def fake_cache(monkeypatch):
    """Dict-backed cache standing in for Redis; the seam stays the real one."""
    import cache

    store = {}

    def fake_get(key):
        return store.get(f"openleg:{key}")

    def fake_set(key, value, ttl=3600):
        store[f"openleg:{key}"] = value

    def fake_clear_prefix(prefix):
        for key in [k for k in store if k.startswith(f"openleg:{prefix}")]:
            del store[key]

    monkeypatch.setattr(cache, "cache_get", fake_get)
    monkeypatch.setattr(cache, "cache_set", fake_set)
    monkeypatch.setattr(cache, "cache_clear_prefix", fake_clear_prefix)
    return store


@pytest.fixture
def assembled():
    calls = []

    def _build(bfs, *, site_url):
        calls.append(bfs)
        return _context(_profile())

    with (
        mock.patch.object(
            municipality_profile, "_build_profile_context", side_effect=_build
        ),
        mock.patch.dict(municipality_profile.PILOT_MUNICIPALITIES, clear=True),
    ):
        yield calls


class TestObservableHitsAndMisses:
    def test_first_read_is_a_miss_and_the_second_is_a_hit(self, assembled):
        first = municipality_profile.profile_context(BFS, site_url="https://x.ch")
        second = municipality_profile.profile_context(BFS, site_url="https://x.ch")

        assert first == second
        assert assembled == [BFS], "the second read must come from the cache"
        assert profile_cache_metrics() == {
            "hits": 1,
            "misses": 1,
            "invalidations": 0,
        }

    def test_a_different_site_url_is_a_different_cache_unit(self, assembled):
        municipality_profile.profile_context(BFS, site_url="https://x.ch")
        municipality_profile.profile_context(BFS, site_url="https://y.ch")

        assert assembled == [BFS, BFS]
        assert profile_cache_metrics()["hits"] == 0


class TestVeracityBoundedFreshness:
    def test_invalidation_serves_the_newly_refreshed_profile(self, assembled):
        """A stale profile must never be served after a refresh (#527)."""
        municipality_profile.profile_context(BFS, site_url="https://x.ch")

        def _build_after_refresh(bfs, *, site_url):
            assembled.append(bfs)
            return _context(_profile(name="Dietikon NEU"))

        with mock.patch.object(
            municipality_profile,
            "_build_profile_context",
            side_effect=_build_after_refresh,
        ):
            municipality_profile.invalidate_profile_cache(BFS)
            refreshed = municipality_profile.profile_context(
                BFS, site_url="https://x.ch"
            )

        assert refreshed["profile"]["name"] == "Dietikon NEU"
        assert profile_cache_metrics()["invalidations"] == 1

    def test_refresh_municipality_invalidates_the_profile_unit(self, monkeypatch):
        """The refresh seam's success is the invalidation trigger."""
        invalidated = []
        monkeypatch.setattr(
            municipality_profile,
            "invalidate_profile_cache",
            lambda bfs: invalidated.append(bfs),
        )
        with (
            mock.patch.object(public_data, "fetch_energie_reporter", return_value=None),
            mock.patch.object(
                public_data, "fetch_sonnendach_municipal", return_value=None
            ),
            mock.patch.object(
                public_data,
                "fetch_elcom_tariffs",
                return_value=[
                    {"category": "H4", "total_rp_kwh": 26.5, "grid_rp_kwh": 9.5}
                ],
            ),
            mock.patch("database.get_municipality_profile", return_value=_profile()),
            mock.patch("database.save_municipality_profile", return_value=True) as save,
        ):
            public_data.refresh_municipality(BFS, year=2026)

        assert save.called
        assert invalidated == [BFS], "a successful refresh must invalidate"


class TestDegradeToDirectReads:
    def test_cache_unavailability_still_serves_the_profile(self, assembled):
        import cache

        with mock.patch.object(
            cache, "cache_get", side_effect=ConnectionError("redis down")
        ):
            context = municipality_profile.profile_context(BFS, site_url="https://x.ch")

        assert context["profile"]["bfs_number"] == BFS


class TestRoundTrip:
    def test_decimal_and_datetime_survive_the_cache_round_trip(self, assembled):
        first = municipality_profile.profile_context(BFS, site_url="https://x.ch")
        cached = municipality_profile.profile_context(BFS, site_url="https://x.ch")

        assert cached["tariffs"][0]["total_rp_kwh"] == Decimal("26.50")
        assert isinstance(cached["tariffs"][0]["total_rp_kwh"], Decimal)
        assert cached["created"] == first["created"]
        assert cached["created"].tzinfo is not None


class TestFreshnessBound:
    def test_ttl_is_a_named_constant_with_rationale(self):
        import inspect
        from pathlib import Path

        assert 0 < municipality_profile.MUNICIPALITY_PROFILE_TTL_SECONDS <= 3600
        source = Path(inspect.getfile(municipality_profile)).read_text(encoding="utf-8")
        constant_index = source.index("MUNICIPALITY_PROFILE_TTL_SECONDS =")
        rationale = source[:constant_index]
        assert rationale.count("#") >= 3, "the TTL needs its stated rationale"
