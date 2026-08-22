# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for api_public.py: REST API endpoints."""

from unittest.mock import patch

from tests.conftest import (
    MOCK_ELCOM_TARIFFS,
    MOCK_MUNICIPALITY_PROFILE,
    MOCK_PROFILES_LIST,
    MOCK_SONNENDACH,
)


class TestMunicipalityEndpoints:
    @patch("api_public.db")
    def test_list_municipalities(self, mock_db, client):
        mock_db.get_all_municipality_profiles.return_value = MOCK_PROFILES_LIST
        resp = client.get("/api/v1/municipalities")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "municipalities" in data
        assert data["count"] == 2
        assert data["kanton"] == "all"
        assert mock_db.get_all_municipality_profiles.call_args.kwargs["kanton"] is None

    @patch("api_public.db")
    def test_list_municipalities_kanton_all_is_supported(self, mock_db, client):
        mock_db.get_all_municipality_profiles.return_value = MOCK_PROFILES_LIST
        resp = client.get("/api/v1/municipalities?kanton=all")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["kanton"] == "all"
        assert mock_db.get_all_municipality_profiles.call_args.kwargs["kanton"] is None

    @patch("api_public.db")
    def test_list_municipalities_invalid_kanton_is_safe(self, mock_db, client):
        mock_db.get_all_municipality_profiles.return_value = MOCK_PROFILES_LIST
        resp = client.get("/api/v1/municipalities?kanton=XX")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["kanton"] == "all"
        assert mock_db.get_all_municipality_profiles.call_args.kwargs["kanton"] is None

    @patch("api_public.db")
    def test_get_municipality(self, mock_db, client):
        mock_db.get_municipality_profile.return_value = MOCK_MUNICIPALITY_PROFILE
        resp = client.get("/api/v1/municipalities/261")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["bfs_number"] == 261
        assert data["name"] == "Dietikon"


REGISTRY_ENTRY = {
    "id": 9,
    "slug": "leg-baden",
    "name": "LEG Baden",
    "kanton": "AG",
    "plz": "5400",
    "ort": "Baden",
    "vnb_name": "Regionalwerke Baden",
    "leg_status": "aktiv",
    "member_count_estimate": 12,
    "description": "Lokale Gemeinschaft.",
    "website_url": "https://example.ch",
    "contact_email": "private@example.ch",
    "claim_token_hash": "private-token",
    "moderation_status": "published",
}


class TestRegistryReadEndpoints:
    @patch("api_public.db")
    def test_registry_list_is_published_only_and_field_filtered(self, mock_db, client):
        mock_db.list_registry_entries.return_value = [REGISTRY_ENTRY]

        response = client.get(
            "/api/v1/registry?kanton=ag&plz=5400&leg_status=aktiv&q=Baden&limit=50"
            "&moderation_status=pending"
        )

        assert response.status_code == 200
        payload = response.get_json()
        assert payload == {
            "entries": [
                {
                    "slug": "leg-baden",
                    "name": "LEG Baden",
                    "kanton": "AG",
                    "plz": "5400",
                    "ort": "Baden",
                    "vnb_name": "Regionalwerke Baden",
                    "leg_status": "aktiv",
                    "member_count_estimate": 12,
                    "description": "Lokale Gemeinschaft.",
                    "website_url": "https://example.ch",
                }
            ],
            "count": 1,
        }
        mock_db.list_registry_entries.assert_called_once_with(
            kanton="AG",
            plz="5400",
            leg_status="aktiv",
            q="Baden",
            moderation_status="published",
            limit=50,
        )
        serialized = repr(payload)
        assert "private@example.ch" not in serialized
        assert "private-token" not in serialized

    @patch("api_public.db")
    def test_registry_detail_rejects_unpublished_and_filters_fields(
        self, mock_db, client
    ):
        mock_db.get_registry_entry_by_slug.side_effect = [
            REGISTRY_ENTRY,
            {**REGISTRY_ENTRY, "moderation_status": "pending"},
        ]

        published = client.get("/api/v1/registry/leg-baden")
        pending = client.get("/api/v1/registry/leg-baden")

        assert published.status_code == 200
        assert published.get_json()["slug"] == "leg-baden"
        assert "contact_email" not in published.get_json()
        assert "claim_token_hash" not in published.get_json()
        assert pending.status_code == 404

    @patch("api_public.db")
    def test_registry_clears_non_http_website_url(self, mock_db, client):
        mock_db.get_registry_entry_by_slug.return_value = {
            **REGISTRY_ENTRY,
            "website_url": "javascript:alert(1)",
        }

        response = client.get("/api/v1/registry/leg-baden")

        assert response.status_code == 200
        assert response.get_json()["website_url"] == ""


class TestMunicipalityDetailEndpoints:
    @patch("api_public.db")
    def test_get_municipality_not_found(self, mock_db, client):
        mock_db.get_municipality_profile.return_value = None
        resp = client.get("/api/v1/municipalities/999")
        assert resp.status_code == 404

    @patch("api_public.db")
    def test_get_tariffs(self, mock_db, client):
        mock_db.get_elcom_tariffs.return_value = MOCK_ELCOM_TARIFFS
        resp = client.get("/api/v1/municipalities/261/tariffs")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == 2
        assert data["tariffs"][0]["operator_name"] == "EKZ"

    @patch("api_public.db")
    def test_get_solar(self, mock_db, client):
        mock_db.get_sonnendach_municipal.return_value = MOCK_SONNENDACH
        resp = client.get("/api/v1/municipalities/261/solar")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["bfs_number"] == 261
        assert data["potential_kwp"] == 180000.0

    @patch("api_public.db")
    def test_get_solar_not_found(self, mock_db, client):
        mock_db.get_sonnendach_municipal.return_value = None
        resp = client.get("/api/v1/municipalities/999/solar")
        assert resp.status_code == 404


class TestScoreEndpoint:
    @patch("api_public.db")
    def test_score_breakdown(self, mock_db, client):
        mock_db.get_municipality_profile.return_value = MOCK_MUNICIPALITY_PROFILE
        resp = client.get("/api/v1/municipalities/261/score")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "breakdown" in data
        assert "total_score" in data
        assert data["total_score"] > 0


class TestLegPotentialEndpoint:
    @patch("api_public.municipality_profile")
    def test_leg_potential(self, mock_mp, client):
        mock_mp.value_gap.return_value = {
            "grid_fee_rp_kwh": 9.5,
            "savings_rp_kwh": 3.8,
            "annual_savings_chf": 171.0,
            "monthly_savings_chf": 14.25,
            "savings_pct": 13.8,
            "grid_reduction_pct": 40.0,
            "assumed_consumption_kwh": 4500,
        }
        resp = client.get("/api/v1/municipalities/261/leg-potential")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["annual_savings_chf"] > 0
        assert data["total_community_savings_chf"] > 0
        assert set(data.keys()) == {
            "grid_fee_rp_kwh",
            "savings_rp_kwh",
            "annual_savings_chf",
            "monthly_savings_chf",
            "savings_pct",
            "grid_reduction_pct",
            "assumed_consumption_kwh",
            "num_participants",
            "total_community_savings_chf",
            "avg_consumption_kwh",
            "bfs_number",
        }
        mock_mp.value_gap.assert_called_once_with(
            261, year=2026, grid_reduction_pct=40.0
        )

    @patch("api_public.municipality_profile")
    def test_leg_potential_no_tariff(self, mock_mp, client):
        mock_mp.value_gap.return_value = None
        resp = client.get("/api/v1/municipalities/261/leg-potential")
        assert resp.status_code == 404
        data = resp.get_json()
        assert data == {
            "error": "No H4 tariff found. Refresh data first.",
            "bfs_number": 261,
        }


class TestSearchEndpoint:
    @patch("api_public.db")
    def test_search(self, mock_db, client):
        mock_db.get_all_municipality_profiles.return_value = MOCK_PROFILES_LIST
        resp = client.get("/api/v1/search?q=Dietikon")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == 1
        assert data["results"][0]["name"] == "Dietikon"
        assert data["limit"] == 10

    @patch("api_public.db")
    def test_search_limit_applied(self, mock_db, client):
        many = []
        for i in range(20):
            many.append(
                {
                    **MOCK_MUNICIPALITY_PROFILE,
                    "bfs_number": 1000 + i,
                    "name": f"Dietikon {i}",
                }
            )
        mock_db.get_all_municipality_profiles.return_value = many
        resp = client.get("/api/v1/search?q=Dietikon&limit=3")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == 3
        assert data["limit"] == 3

    @patch("api_public.db")
    def test_search_invalid_limit_falls_back_to_default(self, mock_db, client):
        many = []
        for i in range(20):
            many.append(
                {
                    **MOCK_MUNICIPALITY_PROFILE,
                    "bfs_number": 2000 + i,
                    "name": f"Dietikon {i}",
                }
            )
        mock_db.get_all_municipality_profiles.return_value = many
        resp = client.get("/api/v1/search?q=Dietikon&limit=oops")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["limit"] == 10
        assert data["count"] == 10

    @patch("api_public.db")
    def test_search_no_query(self, mock_db, client):
        resp = client.get("/api/v1/search?q=")
        assert resp.status_code == 400

    @patch("api_public.db")
    def test_search_no_results(self, mock_db, client):
        mock_db.get_all_municipality_profiles.return_value = MOCK_PROFILES_LIST
        resp = client.get("/api/v1/search?q=Nonexistent")
        data = resp.get_json()
        assert data["count"] == 0


class TestTariffsEndpoint:
    @patch("api_public.db")
    def test_tariffs_defaults_all_cantons(self, mock_db, client):
        mock_db.get_all_elcom_tariffs.return_value = [
            {**row, "municipality_name": "Dietikon"} for row in MOCK_ELCOM_TARIFFS
        ]
        resp = client.get("/api/v1/tariffs")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["kanton"] == "all"
        assert data["count"] >= 2
        mock_db.get_all_elcom_tariffs.assert_called_once_with(year=2026, kanton=None)
        mock_db.get_elcom_tariffs.assert_not_called()

    @patch("api_public.db")
    def test_tariffs_invalid_kanton_is_safe(self, mock_db, client):
        mock_db.get_all_elcom_tariffs.return_value = MOCK_ELCOM_TARIFFS
        resp = client.get("/api/v1/tariffs?kanton=XX")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["kanton"] == "all"
        mock_db.get_all_elcom_tariffs.assert_called_once_with(year=2026, kanton=None)


class TestRankingsEndpoint:
    @patch("api_public.db")
    def test_rankings(self, mock_db, client):
        mock_db.get_all_municipality_profiles.return_value = MOCK_PROFILES_LIST
        resp = client.get("/api/v1/rankings?metric=energy_transition_score")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["rankings"]) == 2
        assert data["rankings"][0]["rank"] == 1
        assert data["kanton"] == "all"
        assert mock_db.get_all_municipality_profiles.call_args.kwargs["kanton"] is None


class TestPublicSitePvRankings:
    @patch("api_public.ranking_module.Ranking")
    def test_site_rankings_filter_limit_and_whitelist(self, mock_ranking, client):
        mock_ranking.load.return_value.standings.return_value = [
            {
                "rank": 1,
                "bfs_number": 4021,
                "name": "Baden",
                "kanton": "AG",
                "population": 23000,
                "pv_score_pct": 77.0,
                "display_score": 77.0,
                "score_over_100": False,
                "pv_untapped_kw": 4200.0,
                "private_note": "do not publish",
            },
            {"rank": 2, "bfs_number": 261, "name": "Dietikon", "kanton": "ZH"},
        ]

        response = client.get(
            "/api/v1/site/rankings?kanton=ag&size=large&density=mid&limit=1"
        )

        assert response.status_code == 200
        assert response.get_json() == {
            "rankings": [
                {
                    "rank": 1,
                    "bfs_number": 4021,
                    "name": "Baden",
                    "kanton": "AG",
                    "population": 23000,
                    "pv_score_pct": 77.0,
                    "display_score": 77.0,
                    "score_over_100": False,
                    "pv_untapped_kw": 4200.0,
                }
            ],
            "count": 2,
            "limit": 1,
        }
        mock_ranking.load.assert_called_once_with()
        mock_ranking.load.return_value.standings.assert_called_once_with(
            kanton="AG", size="large", density="mid"
        )

    @patch("api_public.ranking_module.Ranking")
    def test_site_movers_filter_limit_and_whitelist(self, mock_ranking, client):
        mock_ranking.return_value.movers.return_value = [
            {
                "bfs_number": 4021,
                "name": "Baden",
                "kanton": "AG",
                "year": 2025,
                "score_now": 77.0,
                "score_prev": 70.5,
                "delta": 6.5,
                "private_note": "do not publish",
            }
        ]

        response = client.get("/api/v1/site/rankings/movers?kanton=ag&limit=20")

        assert response.status_code == 200
        assert response.get_json() == {
            "movers": [
                {
                    "bfs_number": 4021,
                    "name": "Baden",
                    "kanton": "AG",
                    "year": 2025,
                    "score_now": 77.0,
                    "score_prev": 70.5,
                    "delta": 6.5,
                }
            ],
            "count": 1,
            "limit": 20,
        }
        mock_ranking.assert_called_once_with([])
        mock_ranking.return_value.movers.assert_called_once_with(
            kanton="AG", size=None, density=None
        )

    @patch("api_public.db")
    def test_rankings_kanton_all_supported(self, mock_db, client):
        mock_db.get_all_municipality_profiles.return_value = MOCK_PROFILES_LIST
        resp = client.get("/api/v1/rankings?kanton=all")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["kanton"] == "all"
        assert mock_db.get_all_municipality_profiles.call_args.kwargs["kanton"] is None


class TestLegToolkitEndpoints:
    @patch("api_public.municipality_profile")
    def test_value_gap_post(self, mock_mp, client):
        mock_mp.value_gap.return_value = {
            "grid_fee_rp_kwh": 9.5,
            "savings_rp_kwh": 3.8,
            "annual_savings_chf": 171.0,
            "monthly_savings_chf": 14.25,
            "savings_pct": 13.8,
        }
        resp = client.post(
            "/api/v1/leg/value-gap",
            json={
                "bfs_number": 261,
                "num_participants": 20,
                "avg_consumption_kwh": 5000,
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["annual_savings_per_household"] > 0
        assert data["total_community_savings"] > 0
        assert set(data.keys()) == {
            "bfs_number",
            "annual_savings_per_household",
            "total_community_savings",
            "grid_fee_reduction",
            "grid_level",
            "num_participants",
            "avg_consumption_kwh",
        }
        mock_mp.value_gap.assert_called_once_with(
            261, year=2026, grid_reduction_pct=40.0
        )

    @patch("api_public.db")
    def test_value_gap_no_bfs(self, mock_db, client):
        resp = client.post("/api/v1/leg/value-gap", json={})
        assert resp.status_code == 400

    @patch("api_public.municipality_profile")
    def test_value_gap_post_no_h4(self, mock_mp, client):
        mock_mp.value_gap.return_value = None
        resp = client.post(
            "/api/v1/leg/value-gap",
            json={"bfs_number": 261},
        )
        assert resp.status_code == 404
        assert resp.get_json() == {"error": "No H4 tariff found"}

    @patch("api_public.db")
    def test_financial_model(self, mock_db, client):
        resp = client.post(
            "/api/v1/leg/financial-model",
            json={
                "bfs_number": 261,
                "scenario": {
                    "community_size": 10,
                    "pv_kwp": 30,
                    "consumption_kwh": 4500,
                },
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["projections"]) == 10
        assert data["projections"][0]["year"] == 1
        assert data["co2_reduction_kg_year"] > 0

    def test_templates(self, client):
        resp = client.get("/api/v1/leg/templates")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["contracts"]) == 3


class TestCorsHeaders:
    def test_cors_origin(self, client):
        resp = client.get("/api/v1/search?q=test")
        assert resp.headers.get("Access-Control-Allow-Origin") == "*"


class TestApiDocs:
    def test_api_docs_has_copy_paste_examples(self, client):
        resp = client.get("/api/v1/docs")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8", errors="ignore")
        assert (
            'curl -s "https://openleg.ch/api/v1/municipalities?kanton=all&amp;order_by=name"'
            in html
        )
        assert (
            'curl -s "https://openleg.ch/api/v1/municipalities/261/tariffs?year=2026"'
            in html
        )
        assert (
            'curl -s "https://openleg.ch/api/v1/municipalities/261/leg-potential?year=2026&amp;participants=10"'
            in html
        )
        assert "/api/cron/" not in html

    def test_api_docs_has_share_metadata(self, client):
        resp = client.get("/api/v1/docs")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8", errors="ignore")
        assert '<html lang="de">' in html
        assert '<meta name="description"' in html
        assert 'rel="canonical"' in html
        assert 'property="og:title"' in html
        assert "Offene Schweizer Energiedaten API" in html

    def test_api_docs_uses_host_canonical(self, client):
        resp = client.get("/api/v1/docs", headers={"Host": "openleg.ch"})
        assert resp.status_code == 200
        html = resp.data.decode("utf-8", errors="ignore")
        assert 'rel="canonical" href="http://openleg.ch/api/v1/docs"' in html
