"""Tests for /fuer-bewohner page containing formation CTA."""

from unittest.mock import patch


class TestBewohnerFormationLink:
    """Resident page connects to formation flow."""

    def test_bewohner_has_formation_cta(self, full_client):
        """Page should contain a link or CTA pointing to formation."""
        with patch('database.get_stats', return_value={'total_buildings': 5}):
            resp = full_client.get('/fuer-bewohner')
            assert resp.status_code == 200
            html = resp.data.decode()
            # Should contain formation-related CTA
            assert 'formation' in html.lower() or 'LEG gr' in html or 'gruenden' in html.lower()

    def test_bewohner_success_step_has_formation_link(self, full_client):
        """After registration, success step should offer formation path."""
        with patch('database.get_stats', return_value={'total_buildings': 5}):
            resp = full_client.get('/fuer-bewohner')
            html = resp.data.decode()
            # The hidden success step should contain formation link
            assert '/gemeinde/formation' in html

    def test_bewohner_page_mentions_leg_gruenden(self, full_client):
        """Page should mention LEG founding as next step."""
        with patch('database.get_stats', return_value={'total_buildings': 5}):
            resp = full_client.get('/fuer-bewohner')
            html = resp.data.decode()
            assert 'Stromgemeinschaft' in html or 'LEG' in html
