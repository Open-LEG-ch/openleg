"""TDD tests for research_cron.py - weekly automated research scan.

Tests mock external APIs (Brave Search, Groq LLM) to verify:
- Search query execution and result parsing
- LLM summarization of search results
- Markdown formatting matching research.md conventions
- Freshness Index updates in research.md
- Error handling for API failures
"""

import os

# Will import from scripts.research_cron once implemented
import sys
import tempfile
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))


MOCK_BRAVE_RESPONSE = {
    'web': {
        'results': [
            {
                'title': 'BFE Pilot- und Leuchtturmprogramm 2026',
                'url': 'https://www.bfe.admin.ch/pilot-2026',
                'description': 'Neue Ausschreibung für LEG-Pilotprojekte ab Q2 2026.',
            },
            {
                'title': 'EnergieSchweiz Förderung Digitalisierung',
                'url': 'https://www.energieschweiz.ch/foerderung',
                'description': 'Fördermittel für digitale Energielösungen inkl. LEG-Plattformen.',
            },
        ]
    }
}

MOCK_GROQ_RESPONSE = {
    'choices': [
        {
            'message': {
                'content': '- BFE opens Q2 2026 call for LEG pilot projects (source: bfe.admin.ch)\n- EnergieSchweiz funds digital energy platforms including LEG tools (source: energieschweiz.ch)'
            }
        }
    ]
}

RESEARCH_MD_HEADER = """# Research

Living document. All market, regulatory, competitive, and technical research for OpenLEG. Referenced by PRDs and decision-making.

## Freshness Index

| Topic | Last Updated | Status | Source |
|-------|-------------|--------|--------|
| Regulatory (StromVG/StromVV) | 2026-02-24 | Fresh | `open-strategy.md` |
| Competitive landscape | 2026-02-24 | Needs refresh | `open-strategy.md` |
| BFE grant programs | — | Not started | — |
| Academic partnerships | — | Not started | — |

---

## Regulatory Framework

Content here...
"""


@pytest.fixture(autouse=True)
def set_api_keys(monkeypatch):
    """Set API keys for all tests."""
    monkeypatch.setenv('BRAVE_API_KEY', 'test-brave-key')
    monkeypatch.setenv('GROQ_API_KEY', 'test-groq-key')


class TestSearchBrave:
    """Test Brave Search API integration."""

    @patch('research_cron.requests.get')
    def test_returns_parsed_results(self, mock_get):
        from research_cron import search_brave

        mock_get.return_value = MagicMock(status_code=200, json=lambda: MOCK_BRAVE_RESPONSE)
        results = search_brave('BFE Pilot Leuchtturm 2026')
        assert len(results) == 2
        assert results[0]['title'] == 'BFE Pilot- und Leuchtturmprogramm 2026'
        assert results[0]['url'].startswith('https://')
        assert 'description' in results[0]

    @patch('research_cron.requests.get')
    def test_handles_api_error(self, mock_get):
        from research_cron import search_brave

        mock_get.return_value = MagicMock(status_code=429, text='Rate limited')
        results = search_brave('test query')
        assert results == []

    @patch('research_cron.requests.get')
    def test_handles_network_error(self, mock_get):
        from research_cron import search_brave

        mock_get.side_effect = Exception('Connection timeout')
        results = search_brave('test query')
        assert results == []

    @patch('research_cron.requests.get')
    def test_handles_empty_results(self, mock_get):
        from research_cron import search_brave

        mock_get.return_value = MagicMock(status_code=200, json=lambda: {'web': {'results': []}})
        results = search_brave('obscure query')
        assert results == []


class TestSummarizeWithLLM:
    """Test Groq LLM summarization."""

    @patch('research_cron.requests.post')
    def test_returns_summary_string(self, mock_post):
        from research_cron import summarize_with_llm

        mock_post.return_value = MagicMock(status_code=200, json=lambda: MOCK_GROQ_RESPONSE)
        summary = summarize_with_llm(
            'BFE grants', [{'title': 'BFE Pilot 2026', 'url': 'https://bfe.admin.ch', 'description': 'New call'}]
        )
        assert isinstance(summary, str)
        assert len(summary) > 10

    @patch('research_cron.requests.post')
    def test_handles_api_error(self, mock_post):
        from research_cron import summarize_with_llm

        mock_post.return_value = MagicMock(status_code=500, text='Internal error')
        summary = summarize_with_llm('test', [{'title': 't', 'url': 'u', 'description': 'd'}])
        assert summary == ''

    @patch('research_cron.requests.post')
    def test_handles_empty_results(self, mock_post):
        from research_cron import summarize_with_llm

        summary = summarize_with_llm('test', [])
        assert summary == ''
        mock_post.assert_not_called()


class TestFormatResearchSection:
    """Test Markdown formatting for research.md."""

    def test_formats_dated_section(self):
        from research_cron import format_research_section

        findings = {
            'bfe_grants': '- BFE opens Q2 2026 call for LEG pilots',
            'regulation': '- No changes to StromVG detected',
            'competitors': '- LEGHub added 1 new client (Romande Energie)',
        }
        section = format_research_section('2026-03-10', findings)
        assert '## Research Scan 2026-03-10' in section
        assert '### BFE Grants' in section or '### bfe_grants' in section
        assert 'BFE opens Q2' in section
        assert 'LEGHub' in section

    def test_skips_empty_categories(self):
        from research_cron import format_research_section

        findings = {'bfe_grants': '- New funding round', 'regulation': '', 'competitors': ''}
        section = format_research_section('2026-03-10', findings)
        assert 'bfe_grants' in section.lower() or 'BFE' in section
        assert 'regulation' not in section.lower() or 'No new findings' in section


class TestAppendToResearchMd:
    """Test appending to research.md and updating Freshness Index."""

    def test_appends_section_at_end(self):
        from research_cron import append_to_research_md

        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write(RESEARCH_MD_HEADER)
            f.flush()
            section = '\n## Research Scan 2026-03-10\n\n### BFE Grants\n- New funding\n'
            append_to_research_md(f.name, section, '2026-03-10')
            with open(f.name) as r:
                content = r.read()
            assert 'Research Scan 2026-03-10' in content
            assert 'New funding' in content
            os.unlink(f.name)

    def test_updates_freshness_index(self):
        from research_cron import append_to_research_md

        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write(RESEARCH_MD_HEADER)
            f.flush()
            section = '\n## Research Scan 2026-03-10\n\n### BFE Grants\n- Found items\n'
            append_to_research_md(f.name, section, '2026-03-10')
            with open(f.name) as r:
                content = r.read()
            # BFE grant programs row should be updated from "Not started" to the scan date
            assert '2026-03-10' in content
            assert 'Not started' not in content or content.count('Not started') < RESEARCH_MD_HEADER.count(
                'Not started'
            )
            os.unlink(f.name)


class TestMainIntegration:
    """Test end-to-end flow with all APIs mocked."""

    @patch('research_cron.requests.post')
    @patch('research_cron.requests.get')
    def test_full_run_produces_output(self, mock_get, mock_post):
        from research_cron import main

        mock_get.return_value = MagicMock(status_code=200, json=lambda: MOCK_BRAVE_RESPONSE)
        mock_post.return_value = MagicMock(status_code=200, json=lambda: MOCK_GROQ_RESPONSE)
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write(RESEARCH_MD_HEADER)
            f.flush()
            result = main(research_md_path=f.name)
            assert result['success'] is True
            assert result['categories_scanned'] >= 1
            with open(f.name) as r:
                content = r.read()
            assert 'Research Scan' in content
            os.unlink(f.name)

    @patch('research_cron.requests.post')
    @patch('research_cron.requests.get')
    def test_full_run_handles_all_failures(self, mock_get, mock_post):
        from research_cron import main

        mock_get.side_effect = Exception('Network down')
        mock_post.side_effect = Exception('Network down')
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write(RESEARCH_MD_HEADER)
            f.flush()
            result = main(research_md_path=f.name)
            assert result['success'] is False
            os.unlink(f.name)
