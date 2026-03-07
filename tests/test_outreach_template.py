"""Tests for Gemeinde outreach email template (P3: prepare only)."""

import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_PATH = os.path.join(REPO_ROOT, 'templates', 'emails', 'gemeinde_outreach.html')


class TestOutreachTemplate:
    """Gemeinde outreach email template exists with correct personalization."""

    def test_template_file_exists(self):
        assert os.path.isfile(TEMPLATE_PATH)

    def test_template_has_personalization_vars(self):
        with open(TEMPLATE_PATH) as f:
            content = f.read()
        for var in ['gemeinde_name', 'kanton', 'energy_transition_score', 'leg_value_gap_chf', 'profil_url']:
            assert var in content, f'Missing personalization var: {var}'

    def test_template_no_data_sale_language(self):
        with open(TEMPLATE_PATH) as f:
            content = f.read().lower()
        for term in ['verkauf', 'monetize', 'sell data', 'daten verkaufen']:
            assert term not in content, f'Found prohibited term: {term}'

    def test_render_outreach_exists(self):
        from email_automation import render_outreach_email

        assert callable(render_outreach_email)

    def test_render_outreach_returns_html(self, full_app):
        from email_automation import render_outreach_email

        profile = {
            'name': 'Dietikon',
            'kanton': 'ZH',
            'energy_transition_score': 42.5,
            'leg_value_gap_chf': 171.0,
            'bfs_number': 261,
        }
        with full_app.app_context():
            html = render_outreach_email(profile, app=full_app)
        assert 'Dietikon' in html
        assert '<' in html  # contains HTML
