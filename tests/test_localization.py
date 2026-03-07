"""Tests for Swiss trilingual localization (P6)."""

from unittest.mock import patch


class TestTranslations:
    """Translation dict completeness and t() helper."""

    def test_all_keys_have_de_fr_it(self):
        from translations import TRANSLATIONS

        missing = []
        for key, langs in TRANSLATIONS.items():
            for lang in ('de', 'fr', 'it'):
                if lang not in langs:
                    missing.append(f'{key}:{lang}')
        assert not missing, f'Missing translations: {missing}'

    def test_romansh_stubs_exist(self):
        from translations import TRANSLATIONS

        rm_count = sum(1 for v in TRANSLATIONS.values() if 'rm' in v)
        assert rm_count >= 20, f'Only {rm_count} Romansh stubs, need 20+'

    def test_t_returns_german_by_default(self):
        from translations import t

        result = t('hero_title')
        assert 'Ihr Strom' in result

    def test_t_returns_french(self):
        from translations import t

        result = t('hero_title', 'fr')
        assert 'Votre courant' in result

    def test_t_returns_italian(self):
        from translations import t

        result = t('hero_title', 'it')
        assert 'vostra energia' in result

    def test_t_romansh_falls_back_to_german(self):
        from translations import t

        # Key without 'rm' entry should fall back to German
        result = t('bewohner_subtitle', 'rm')
        assert 'OpenLEG koordiniert' in result

    def test_t_returns_key_if_missing(self):
        from translations import t

        assert t('nonexistent_key_xyz') == 'nonexistent_key_xyz'


class TestKantonLanguageMapping:
    """KANTON_LANGUAGE maps all 26 cantons."""

    def test_zurich_is_german(self):
        from translations import KANTON_LANGUAGE

        assert KANTON_LANGUAGE['ZH'] == 'de'

    def test_vaud_is_french(self):
        from translations import KANTON_LANGUAGE

        assert KANTON_LANGUAGE['VD'] == 'fr'

    def test_ticino_is_italian(self):
        from translations import KANTON_LANGUAGE

        assert KANTON_LANGUAGE['TI'] == 'it'

    def test_graubuenden_is_romansh(self):
        from translations import KANTON_LANGUAGE

        assert KANTON_LANGUAGE['GR'] == 'rm'


class TestLocalizedPages:
    """Tenant language drives page content."""

    @patch('tenant.get_tenant_config')
    def test_landing_french_tenant(self, mock_tenant, full_app):
        mock_tenant.return_value = {
            'territory': 'lausanne',
            'city_name': 'Lausanne',
            'kanton': 'Vaud',
            'kanton_code': 'VD',
            'language': 'fr',
            'platform_name': 'OpenLEG',
            'brand_prefix': 'OpenLEG',
            'utility_name': 'Romande Energie',
            'primary_color': '#0d9488',
            'secondary_color': '#f59e0b',
            'contact_email': 'hallo@openleg.ch',
            'dso_contact': '',
            'legal_entity': '',
            'map_center_lat': 46.52,
            'map_center_lon': 6.63,
            'map_zoom': 12,
            'map_bounds_sw': [46.4, 6.5],
            'map_bounds_ne': [46.6, 6.8],
            'solar_kwh_per_kwp': 1000,
            'active': True,
        }
        with full_app.test_client() as c:
            resp = c.get('/')
            html = resp.data.decode()
            assert 'Votre courant' in html or 'communaut' in html

    @patch('tenant.get_tenant_config')
    def test_landing_italian_tenant(self, mock_tenant, full_app):
        mock_tenant.return_value = {
            'territory': 'lugano',
            'city_name': 'Lugano',
            'kanton': 'Ticino',
            'kanton_code': 'TI',
            'language': 'it',
            'platform_name': 'OpenLEG',
            'brand_prefix': 'OpenLEG',
            'utility_name': 'AIL',
            'primary_color': '#0d9488',
            'secondary_color': '#f59e0b',
            'contact_email': 'hallo@openleg.ch',
            'dso_contact': '',
            'legal_entity': '',
            'map_center_lat': 46.0,
            'map_center_lon': 8.95,
            'map_zoom': 12,
            'map_bounds_sw': [45.9, 8.8],
            'map_bounds_ne': [46.1, 9.1],
            'solar_kwh_per_kwp': 1100,
            'active': True,
        }
        with full_app.test_client() as c:
            resp = c.get('/')
            html = resp.data.decode()
            assert 'vostra energia' in html or 'comunità' in html


class TestDesignRebrand:
    """New brand colors in templates."""

    def test_landing_has_brand_colors(self, full_client):
        resp = full_client.get('/')
        html = resp.data.decode()
        assert '#0d9488' in html or 'brand-500' in html or 'teal' in html
