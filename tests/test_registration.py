# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for registration verbs (no Flask, no real DB)."""

import registration as reg


VALID_ANON_PAYLOAD = {
    "email": "test@example.ch",
    "phone": "",
    "referral_code": "",
    "consents": {
        "share_with_neighbors": True,
        "share_with_utility": True,
        "updates_opt_in": False,
    },
    "profile": {
        "building_id": "abc123",
        "lat": 47.37,
        "lon": 8.54,
        "address": "Bahnhofstr. 1, 8001 Zürich",
    },
}


def _stub_security(
    monkeypatch, email_ok=True, phone_ok=True, id_ok=True, coords_ok=True
):
    monkeypatch.setattr(
        reg.security_utils,
        "validate_email_address",
        lambda e: (
            (email_ok, e.strip().lower(), "")
            if email_ok
            else (False, "", "E-Mail ungültig")
        ),
    )
    monkeypatch.setattr(
        reg.security_utils,
        "validate_phone",
        lambda p: (phone_ok, p, "") if phone_ok else (False, "", "Telefon ungültig"),
    )
    monkeypatch.setattr(
        reg.security_utils,
        "validate_building_id",
        lambda i: (id_ok, "") if id_ok else (False, "ID ungültig"),
    )
    monkeypatch.setattr(
        reg.security_utils,
        "validate_coordinates",
        lambda la, lo: (
            (coords_ok, "") if coords_ok else (False, "Koordinaten ungültig")
        ),
    )


def _stub_db(monkeypatch, referrer=None, save_ok=True):
    monkeypatch.setattr(reg.db, "get_building_by_referral_code", lambda _: referrer)
    monkeypatch.setattr(reg.db, "save_building", lambda **_k: None)
    monkeypatch.setattr(reg.db, "save_token", lambda *_a: None)
    monkeypatch.setattr(reg.db, "track_event", lambda *_a, **_k: None)
    monkeypatch.setattr(reg.db, "get_referral_code", lambda _: "REF001")


def _stub_background(monkeypatch):
    monkeypatch.setattr(reg, "find_provisional_matches", lambda _: None)
    monkeypatch.setattr(reg, "collect_building_locations", lambda **_k: [])
    monkeypatch.setattr(reg, "_spawn_post_registration_threads", lambda *_a, **_k: None)


# --- parse_consents ---


class TestParseConsents:
    def test_true_values(self):
        result = reg.parse_consents(
            {"share_with_neighbors": True, "share_with_utility": True}
        )
        assert result["share_with_neighbors"] is True
        assert result["share_with_utility"] is True

    def test_string_true(self):
        result = reg.parse_consents(
            {"share_with_neighbors": "true", "share_with_utility": "1"}
        )
        assert result["share_with_neighbors"] is True
        assert result["share_with_utility"] is True

    def test_falsy_values(self):
        result = reg.parse_consents(
            {"share_with_neighbors": False, "share_with_utility": None}
        )
        assert result["share_with_neighbors"] is False
        assert result["share_with_utility"] is False

    def test_empty_dict_defaults(self):
        result = reg.parse_consents({})
        assert result["share_with_neighbors"] is False
        assert result["share_with_utility"] is False
        assert "consent_version" in result
        assert "consent_timestamp" in result

    def test_none_defaults(self):
        result = reg.parse_consents(None)
        assert result["share_with_neighbors"] is False


# --- collect_building_locations ---


class TestCollectBuildingLocations:
    def test_returns_list(self, monkeypatch):
        monkeypatch.setattr(
            reg.db,
            "get_all_buildings",
            lambda city_id=None: [
                {"building_id": "x1", "lat": 47.0, "lon": 8.0, "user_type": "anonymous"}
            ],
        )
        locs = reg.collect_building_locations()
        assert isinstance(locs, list)
        assert len(locs) == 1
        assert "lat" in locs[0] and "lon" in locs[0]

    def test_excludes_building_id(self, monkeypatch):
        monkeypatch.setattr(
            reg.db,
            "get_all_buildings",
            lambda city_id=None: [
                {"building_id": "exclude_me", "lat": 47.0, "lon": 8.0}
            ],
        )
        locs = reg.collect_building_locations(exclude_building_id="exclude_me")
        assert locs == []

    def test_skips_missing_coords(self, monkeypatch):
        monkeypatch.setattr(
            reg.db,
            "get_all_buildings",
            lambda city_id=None: [{"building_id": "x1", "lat": None, "lon": None}],
        )
        assert reg.collect_building_locations() == []


# --- check_potential ---


class TestCheckPotential:
    def test_returns_potential_false_no_match(self, monkeypatch):
        monkeypatch.setattr(
            reg,
            "_get_energy_profile",
            lambda addr: ({"lat": 47.0, "lon": 8.0}, {"lat": 47.0, "lon": 8.0}),
        )
        monkeypatch.setattr(reg, "find_provisional_matches", lambda _: None)
        result = reg.check_potential("Bahnhofstr. 1, 8001 Zürich")
        assert result["potential"] is False

    def test_returns_potential_true_with_match(self, monkeypatch):
        cluster = {"community_id": "c1", "num_members": 2}
        monkeypatch.setattr(
            reg,
            "_get_energy_profile",
            lambda addr: ({"lat": 47.0, "lon": 8.0}, {"lat": 47.0, "lon": 8.0}),
        )
        monkeypatch.setattr(reg, "find_provisional_matches", lambda _: cluster)
        result = reg.check_potential("Bahnhofstr. 1, 8001 Zürich")
        assert result["potential"] is True
        assert result["cluster_info"] == cluster

    def test_returns_error_when_no_estimates(self, monkeypatch):
        monkeypatch.setattr(reg, "_get_energy_profile", lambda addr: (None, None))
        result = reg.check_potential("unknown address")
        assert "error" in result
        assert result.get("_status") == 404


# --- register_anonymous ---


class TestRegisterAnonymous:
    def test_success(self, monkeypatch):
        _stub_security(monkeypatch)
        _stub_db(monkeypatch)
        _stub_background(monkeypatch)
        result = reg.register_anonymous(
            VALID_ANON_PAYLOAD, city_id="zurich", app_base_url="http://localhost:5003"
        )
        assert "error" not in result
        assert result["verification_email_sent"] is True

    def test_invalid_email(self, monkeypatch):
        _stub_security(monkeypatch, email_ok=False)
        result = reg.register_anonymous(
            VALID_ANON_PAYLOAD, city_id="zurich", app_base_url="http://localhost:5003"
        )
        assert "error" in result
        assert result.get("_status", 400) == 400

    def test_missing_consents(self, monkeypatch):
        _stub_security(monkeypatch)
        payload = {
            **VALID_ANON_PAYLOAD,
            "consents": {"share_with_neighbors": False, "share_with_utility": False},
        }
        result = reg.register_anonymous(
            payload, city_id="zurich", app_base_url="http://localhost:5003"
        )
        assert "error" in result

    def test_missing_profile(self, monkeypatch):
        _stub_security(monkeypatch)
        payload = {**VALID_ANON_PAYLOAD, "profile": None}
        result = reg.register_anonymous(
            payload, city_id="zurich", app_base_url="http://localhost:5003"
        )
        assert "error" in result

    def test_referral_code_resolved(self, monkeypatch):
        _stub_security(monkeypatch)
        referrer = {"building_id": "ref_bld"}
        _stub_db(monkeypatch, referrer=referrer)
        _stub_background(monkeypatch)
        saved_kwargs = {}
        monkeypatch.setattr(
            reg.db, "save_building", lambda **kw: saved_kwargs.update(kw)
        )
        payload = {**VALID_ANON_PAYLOAD, "referral_code": "CODE1"}
        reg.register_anonymous(
            payload, city_id="zurich", app_base_url="http://localhost:5003"
        )
        assert saved_kwargs.get("referrer_id") == "ref_bld"

    def test_referral_link_in_result(self, monkeypatch):
        _stub_security(monkeypatch)
        _stub_db(monkeypatch)
        _stub_background(monkeypatch)
        result = reg.register_anonymous(
            VALID_ANON_PAYLOAD, city_id="zurich", app_base_url="http://localhost:5003"
        )
        assert result["referral_link"] == "http://localhost:5003/?ref=REF001"


# --- register_full ---


class TestRegisterFull:
    def test_success(self, monkeypatch):
        _stub_security(monkeypatch)
        _stub_db(monkeypatch)
        _stub_background(monkeypatch)
        result = reg.register_full(
            VALID_ANON_PAYLOAD, city_id="zurich", app_base_url="http://localhost:5003"
        )
        assert "error" not in result
        assert result["verification_email_sent"] is True

    def test_user_type_is_registered(self, monkeypatch):
        _stub_security(monkeypatch)
        _stub_db(monkeypatch)
        _stub_background(monkeypatch)
        saved = {}
        monkeypatch.setattr(reg.db, "save_building", lambda **kw: saved.update(kw))
        reg.register_full(
            VALID_ANON_PAYLOAD, city_id="zurich", app_base_url="http://localhost:5003"
        )
        assert saved.get("user_type") == "registered"

    def test_anon_user_type_is_anonymous(self, monkeypatch):
        _stub_security(monkeypatch)
        _stub_db(monkeypatch)
        _stub_background(monkeypatch)
        saved = {}
        monkeypatch.setattr(reg.db, "save_building", lambda **kw: saved.update(kw))
        reg.register_anonymous(
            VALID_ANON_PAYLOAD, city_id="zurich", app_base_url="http://localhost:5003"
        )
        assert saved.get("user_type") == "anonymous"
