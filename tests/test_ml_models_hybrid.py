import pandas as pd

import ml_models


class _InMemoryCache:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, _ttl):
        self.store[key] = value
        return True


def test_signature_order_independent():
    df1 = pd.DataFrame([
        {"building_id": "b2", "annual_consumption_kwh": 5200, "potential_pv_kwp": 7.2, "building_type": "MFH"},
        {"building_id": "b1", "annual_consumption_kwh": 4300, "potential_pv_kwp": 4.1, "building_type": "EFH"},
    ])
    df2 = pd.DataFrame([
        {"building_id": "b1", "annual_consumption_kwh": 4300, "potential_pv_kwp": 4.1, "building_type": "EFH"},
        {"building_id": "b2", "annual_consumption_kwh": 5200, "potential_pv_kwp": 7.2, "building_type": "MFH"},
    ])
    sig1 = ml_models.build_community_signature(df1, city_id="baden", sim_version="v2")
    sig2 = ml_models.build_community_signature(df2, city_id="baden", sim_version="v2")
    assert sig1 == sig2


def test_archetype_profiles_not_identical():
    office = ml_models.generate_mock_profiles(annual_consumption_kwh=12000, potential_pv_kwp=5, num_intervals=96, archetype="office")
    efh = ml_models.generate_mock_profiles(annual_consumption_kwh=12000, potential_pv_kwp=5, num_intervals=96, archetype="EFH")
    assert not office["consumption_kw"].equals(efh["consumption_kw"])


def test_hybrid_mix_and_cache_hit():
    community_df = pd.DataFrame([
        {"building_id": "real-1", "annual_consumption_kwh": 5000, "potential_pv_kwp": 0, "building_type": "EFH"},
        {"building_id": "mock-1", "annual_consumption_kwh": 5000, "potential_pv_kwp": 0, "building_type": "office"},
    ])

    def provider(row, window=None, strategy="hybrid"):
        idx = pd.date_range(start="2025-01-01 00:00:00", periods=8, freq="15min")
        profile = pd.DataFrame({"consumption_kw": [4.0] * 8, "production_kw": [0.0] * 8}, index=idx)
        if row.get("building_id") == "real-1":
            return profile, {"source": "real", "quality_score": 1.0}
        return profile, {"source": "mock", "quality_score": 0.45}

    cache = _InMemoryCache()
    backend = {"get": cache.get, "set": cache.set}
    key = "community-key"

    first = ml_models.calculate_community_autarky_details(
        community_buildings_df=community_df,
        profile_provider=provider,
        cache_backend=backend,
        cache_key=key,
        window={"start": "2025-01-01 00:00:00", "num_intervals": 8},
    )
    assert first["profile_data_mix"] == "hybrid"
    assert first["cache_hit"] is False
    assert first["confidence_percent"] > 50

    second = ml_models.calculate_community_autarky_details(
        community_buildings_df=community_df,
        profile_provider=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("should not compute")),
        cache_backend=backend,
        cache_key=key,
        window={"start": "2025-01-01 00:00:00", "num_intervals": 8},
    )
    assert second["cache_hit"] is True
    assert second["profile_data_mix"] == "hybrid"
