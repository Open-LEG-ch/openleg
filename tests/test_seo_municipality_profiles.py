# SPDX-License-Identifier: AGPL-3.0-or-later
"""TDD tests for municipality profile + verzeichnis SEO enhancements.

Covers: canonical tags, JSON-LD structured data (LocalPlace, BreadcrumbList,
CollectionPage), optimized H1 tags, og:image fallback.
Refs: seo-strategy.md [SEO-1 to SEO-8], GitHub issues #13, #14.
"""
import json
import pytest
from unittest.mock import patch
from tests.conftest import (
    MOCK_MUNICIPALITY_PROFILE, MOCK_ELCOM_TARIFFS, MOCK_SONNENDACH,
)


class TestProfilPageSEO:
    """SEO elements on /gemeinde/profil/<bfs>."""

    @patch('municipality.db')
    def _get_profil_html(self, mock_db, full_client, bfs=261):
        mock_db.get_municipality_profile.return_value = MOCK_MUNICIPALITY_PROFILE
        mock_db.get_elcom_tariffs.return_value = MOCK_ELCOM_TARIFFS
        mock_db.get_sonnendach_municipal.return_value = MOCK_SONNENDACH
        resp = full_client.get(f'/gemeinde/profil/{bfs}')
        assert resp.status_code == 200
        return resp.data.decode()

    def test_canonical_tag(self, full_client):
        """SEO-1: profil page has <link rel=canonical>."""
        html = self._get_profil_html(full_client=full_client)
        assert 'rel="canonical"' in html
        assert '/gemeinde/profil/261' in html

    def test_jsonld_local_place(self, full_client):
        """SEO-2: profil page has LocalPlace JSON-LD with name and areaServed."""
        html = self._get_profil_html(full_client=full_client)
        assert 'application/ld+json' in html
        # Extract JSON-LD blocks
        import re
        blocks = re.findall(
            r'<script type="application/ld\+json">(.*?)</script>',
            html, re.DOTALL
        )
        assert len(blocks) >= 1
        found_place = False
        for block in blocks:
            data = json.loads(block)
            if data.get('@type') == 'Place':
                found_place = True
                assert data['name'] == 'Dietikon'
                assert 'ZH' in data.get('areaServed', '')
                assert 'description' in data
        assert found_place, "No Place schema found in JSON-LD"

    def test_optimized_h1(self, full_client):
        """SEO-3: H1 includes Stromtarif and municipality name."""
        html = self._get_profil_html(full_client=full_client)
        # H1 should mention tariff + municipality for keyword targeting
        import re
        h1_match = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL)
        assert h1_match, "No H1 found"
        h1_text = h1_match.group(1)
        assert 'Dietikon' in h1_text
        assert 'Stromtarif' in h1_text or 'Rp/kWh' in h1_text

    def test_breadcrumb_schema(self, full_client):
        """SEO-8: profil page has BreadcrumbList JSON-LD."""
        html = self._get_profil_html(full_client=full_client)
        import re
        blocks = re.findall(
            r'<script type="application/ld\+json">(.*?)</script>',
            html, re.DOTALL
        )
        found_breadcrumb = False
        for block in blocks:
            data = json.loads(block)
            if data.get('@type') == 'BreadcrumbList':
                found_breadcrumb = True
                items = data.get('itemListElement', [])
                assert len(items) >= 2
                # First item should be Verzeichnis
                assert 'verzeichnis' in items[0].get('item', {}).get('@id', '').lower() or \
                       'Verzeichnis' in items[0].get('name', '')
        assert found_breadcrumb, "No BreadcrumbList schema found"

    def test_og_image_present(self, full_client):
        """SEO-6: profil page has og:image meta tag."""
        html = self._get_profil_html(full_client=full_client)
        assert 'og:image' in html


class TestVerzeichnisPageSEO:
    """SEO elements on /gemeinde/verzeichnis."""

    @patch('municipality.db')
    def _get_verzeichnis_html(self, mock_db, full_client):
        from tests.conftest import MOCK_PROFILES_LIST
        mock_db.get_all_municipality_profiles.return_value = MOCK_PROFILES_LIST
        resp = full_client.get('/gemeinde/verzeichnis')
        assert resp.status_code == 200
        return resp.data.decode()

    def test_canonical_tag(self, full_client):
        """SEO-4: verzeichnis has canonical tag."""
        html = self._get_verzeichnis_html(full_client=full_client)
        assert 'rel="canonical"' in html
        assert '/gemeinde/verzeichnis' in html

    def test_collection_page_schema(self, full_client):
        """SEO-5: verzeichnis has CollectionPage JSON-LD."""
        html = self._get_verzeichnis_html(full_client=full_client)
        assert 'application/ld+json' in html
        import re
        blocks = re.findall(
            r'<script type="application/ld\+json">(.*?)</script>',
            html, re.DOTALL
        )
        found = False
        for block in blocks:
            data = json.loads(block)
            if data.get('@type') == 'CollectionPage':
                found = True
                assert 'name' in data
                assert 'description' in data
        assert found, "No CollectionPage schema found"

    def test_og_image_present(self, full_client):
        """SEO-6: verzeichnis has og:image fallback."""
        html = self._get_verzeichnis_html(full_client=full_client)
        assert 'og:image' in html
