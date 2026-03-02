"""Phase 3 bug tests: duplicate function, ingest_csv bypass, rate limit, timeouts."""
import os
import re
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestDuplicateVnbPipelineStats:
    """Only one get_vnb_pipeline_stats definition should exist."""

    def test_single_definition(self):
        with open(os.path.join(PROJECT_ROOT, "database.py")) as f:
            content = f.read()
        count = len(re.findall(r'^def get_vnb_pipeline_stats\b', content, re.MULTILINE))
        assert count == 1, f"Found {count} definitions of get_vnb_pipeline_stats (expected 1)"

    def test_funnel_stats_renamed(self):
        with open(os.path.join(PROJECT_ROOT, "database.py")) as f:
            content = f.read()
        assert "def get_vnb_funnel_stats" in content, \
            "Second function should be renamed to get_vnb_funnel_stats"


class TestIngestCsvUsesDetection:
    """ingest_csv must use parse_meter_csv (auto-detect) not parse_ekz_csv."""

    def test_ingest_calls_parse_meter_csv(self):
        with open(os.path.join(PROJECT_ROOT, "meter_data.py")) as f:
            content = f.read()
        # Find ingest_csv function body
        lines = content.split('\n')
        in_ingest = False
        uses_correct = False
        uses_wrong = False
        for line in lines:
            if 'def ingest_csv' in line:
                in_ingest = True
            elif in_ingest and line.strip().startswith('def '):
                break
            elif in_ingest:
                if 'parse_meter_csv' in line:
                    uses_correct = True
                if 'parse_ekz_csv' in line:
                    uses_wrong = True
        assert uses_correct, "ingest_csv does not call parse_meter_csv"
        assert not uses_wrong, "ingest_csv still calls parse_ekz_csv directly"


class TestPublicApiRateLimit:
    """Public API blueprint must enforce per-IP rate limiting."""

    def test_before_request_rate_limit(self):
        with open(os.path.join(PROJECT_ROOT, "api_public.py")) as f:
            content = f.read()
        assert "before_request" in content, \
            "Public API has no before_request rate limiting"
        assert "429" in content, \
            "Public API does not return 429 on rate limit"


class TestHttpTimeouts:
    """All external HTTP requests must have explicit timeouts."""

    def test_data_enricher_timeouts(self):
        with open(os.path.join(PROJECT_ROOT, "data_enricher.py")) as f:
            content = f.read()
        # Every requests.get call should have timeout=
        calls = re.findall(r'requests\.get\([^)]+\)', content, re.DOTALL)
        for call in calls:
            assert 'timeout' in call, f"Missing timeout in data_enricher.py: {call[:80]}"

    def test_deepsign_timeouts(self):
        with open(os.path.join(PROJECT_ROOT, "deepsign_integration.py")) as f:
            content = f.read()
        # Count requests calls vs timeout= occurrences
        call_count = len(re.findall(r'requests\.(?:get|post)\(', content))
        timeout_count = content.count('timeout=')
        assert call_count > 0, "No requests calls found"
        assert timeout_count >= call_count, \
            f"Found {call_count} requests calls but only {timeout_count} timeouts"
