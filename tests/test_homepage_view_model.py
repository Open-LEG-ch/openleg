# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contracts for the public-site homepage data seam."""

import logging
from types import SimpleNamespace

from flask import g


def _rows(count=7):
    return [
        {
            "rank": index,
            "name": f"Gemeinde {index}",
            "kanton": "AG",
            "bfs_number": 4000 + index,
            "pv_score_pct": 101 - index,
            "display_score": 101 - index,
            "private_note": "never expose",
        }
        for index in range(1, count + 1)
    ]


def test_homepage_model_shapes_stats_and_ranking_without_private_fields(monkeypatch):
    import homepage_view_model

    get_stats_calls = []
    monkeypatch.setattr(
        homepage_view_model.db,
        "get_stats",
        lambda city_id=None: (
            get_stats_calls.append(city_id)
            or {"total_buildings": 42, "registrations_today": 3}
        ),
    )
    monkeypatch.setattr(
        homepage_view_model.Ranking,
        "load",
        lambda: SimpleNamespace(national=lambda: _rows()),
    )

    model = homepage_view_model.build_homepage_view_model("baden")

    assert model == {
        "schema_version": 1,
        "stats": {"registered_buildings": 42},
        "ranking": {
            "best": [
                {
                    "rank": 1,
                    "name": "Gemeinde 1",
                    "kanton": "AG",
                    "bfs_number": 4001,
                    "score": 100,
                },
                {
                    "rank": 2,
                    "name": "Gemeinde 2",
                    "kanton": "AG",
                    "bfs_number": 4002,
                    "score": 99,
                },
                {
                    "rank": 3,
                    "name": "Gemeinde 3",
                    "kanton": "AG",
                    "bfs_number": 4003,
                    "score": 98,
                },
            ],
            "needs_action": [
                {
                    "rank": 7,
                    "name": "Gemeinde 7",
                    "kanton": "AG",
                    "bfs_number": 4007,
                    "score": 94,
                },
                {
                    "rank": 6,
                    "name": "Gemeinde 6",
                    "kanton": "AG",
                    "bfs_number": 4006,
                    "score": 95,
                },
                {
                    "rank": 5,
                    "name": "Gemeinde 5",
                    "kanton": "AG",
                    "bfs_number": 4005,
                    "score": 96,
                },
            ],
            "total": 7,
        },
    }
    assert get_stats_calls == ["baden"]


def test_homepage_model_hides_extremes_when_fewer_than_six_rows(monkeypatch):
    import homepage_view_model

    monkeypatch.setattr(homepage_view_model.db, "get_stats", lambda **_kwargs: {})
    monkeypatch.setattr(
        homepage_view_model.Ranking,
        "load",
        lambda: SimpleNamespace(national=lambda: _rows(5)),
    )

    model = homepage_view_model.build_homepage_view_model("baden")

    assert model["ranking"] == {"best": [], "needs_action": [], "total": 5}


def test_homepage_model_fails_closed_when_ranking_storage_fails(monkeypatch, caplog):
    import homepage_view_model

    monkeypatch.setattr(homepage_view_model.db, "get_stats", lambda **_kwargs: {})

    def fail():
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr(homepage_view_model.Ranking, "load", fail)

    with caplog.at_level(logging.ERROR):
        model = homepage_view_model.build_homepage_view_model("baden")

    assert model["ranking"] == {"best": [], "needs_action": [], "total": 0}
    assert "ranking preview failed" in caplog.text


def test_referral_lookup_is_opt_in_for_server_rendering(monkeypatch):
    import homepage_view_model

    monkeypatch.setattr(homepage_view_model.db, "get_stats", lambda **_kwargs: {})
    monkeypatch.setattr(
        homepage_view_model.Ranking,
        "load",
        lambda: SimpleNamespace(national=list),
    )
    lookup_calls = []
    monkeypatch.setattr(
        homepage_view_model.db,
        "get_building_by_referral_code",
        lambda code: lookup_calls.append(code) or {"address": "Badstrasse 1, Baden"},
    )

    public_model = homepage_view_model.build_homepage_view_model("baden")
    preview_model = homepage_view_model.build_homepage_view_model(
        "baden", referral_code="invite-code"
    )

    assert "referral" not in public_model
    assert preview_model["referral"] == {
        "code": "invite-code",
        "street": "Badstrasse 1",
    }
    assert lookup_calls == ["invite-code"]


def test_site_home_endpoint_uses_tenant_and_never_serializes_referral(
    app, client, monkeypatch
):
    import homepage_view_model

    @app.before_request
    def set_test_tenant():
        g.tenant = {"territory": "baden"}

    monkeypatch.setattr(
        homepage_view_model,
        "build_homepage_view_model",
        lambda territory: {
            "schema_version": 1,
            "stats": {"registered_buildings": 12},
            "ranking": {"best": [], "needs_action": [], "total": 0},
            "referral": {"code": "must-not-leak", "street": "Privatweg"},
            "territory_seen": territory,
        },
    )

    response = client.get("/api/v1/site/home?ref=must-not-leak")

    assert response.status_code == 200
    assert response.get_json() == {
        "schema_version": 1,
        "stats": {"registered_buildings": 12},
        "ranking": {"best": [], "needs_action": [], "total": 0},
    }
