#!/usr/bin/env python3
"""Weekly research scan: BFE grants, regulation changes, competitor activity.

Appends findings to research.md. Uses Brave Search + Groq LLM.
No new dependencies: uses requests (already in requirements.txt).

Usage:
    python3 scripts/research_cron.py                    # default: ./research.md
    python3 scripts/research_cron.py --path /opt/badenleg/research.md

Env vars required:
    BRAVE_API_KEY  - Brave Search API key
    GROQ_API_KEY   - Groq API key (free tier sufficient)
"""

import argparse
import os
import re
import sys
from datetime import datetime, timezone

import requests

BRAVE_API_URL = 'https://api.search.brave.com/res/v1/web/search'
GROQ_API_URL = 'https://api.groq.com/openai/v1/chat/completions'
GROQ_MODEL = 'meta-llama/llama-4-scout-17b-16e-instruct'

SEARCH_TOPICS = {
    'bfe_grants': [
        'BFE Pilot Leuchtturm 2026 Förderung Energiegemeinschaft',
        'EnergieSchweiz Förderung digital Energiegemeinschaft LEG',
        'Pronovo Förderung Lokale Elektrizitätsgemeinschaft',
    ],
    'regulation': [
        'StromVG Revision 2026 LEG Elektrizitätsgemeinschaft',
        'LEG Verordnung StromVV Änderung 2026',
        'SDAT-CH 2025 2026 Messdatenaustausch',
    ],
    'competitors': [
        'LEGHub Swisspower neue Partner 2026',
        'Ormera Optimatik Exnaton Elektrizitätsgemeinschaft Schweiz',
        'lokalerstrom enshift LEG Schweiz 2026',
    ],
}

CATEGORY_LABELS = {
    'bfe_grants': 'BFE Grants',
    'regulation': 'Regulation',
    'competitors': 'Competitors',
}


def search_brave(query, max_results=5):
    """Search Brave and return list of {title, url, description}."""
    api_key = os.environ.get('BRAVE_API_KEY', '')
    if not api_key:
        return []
    try:
        resp = requests.get(
            BRAVE_API_URL,
            headers={'X-Subscription-Token': api_key, 'Accept': 'application/json'},
            params={'q': query, 'count': max_results},
            timeout=15,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        results = data.get('web', {}).get('results', [])
        return [
            {'title': r.get('title', ''), 'url': r.get('url', ''), 'description': r.get('description', '')}
            for r in results
        ]
    except Exception:
        return []


def summarize_with_llm(topic, search_results):
    """Summarize search results into 2-3 bullet points via Groq."""
    if not search_results:
        return ''
    api_key = os.environ.get('GROQ_API_KEY', '')
    if not api_key:
        return ''

    context = '\n'.join(f'- {r["title"]}: {r["description"]} ({r["url"]})' for r in search_results)
    prompt = (
        f"Du bist ein Schweizer Energieexperte. Fasse die folgenden Suchergebnisse zum Thema '{topic}' "
        f"in 2-3 prägnanten Stichpunkten zusammen. Jeder Punkt beginnt mit '- '. "
        f'Erwähne die Quelle (URL) inline. Schreib auf Deutsch.\n\n{context}'
    )

    try:
        resp = requests.post(
            GROQ_API_URL,
            headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
            json={
                'model': GROQ_MODEL,
                'messages': [{'role': 'user', 'content': prompt}],
                'max_tokens': 500,
                'temperature': 0.3,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            return ''
        return resp.json()['choices'][0]['message']['content'].strip()
    except Exception:
        return ''


def format_research_section(date_str, findings):
    """Format findings as a markdown section for research.md."""
    lines = [f'\n## Research Scan {date_str}\n']
    for category, summary in findings.items():
        if not summary:
            continue
        label = CATEGORY_LABELS.get(category, category)
        lines.append(f'### {label}\n')
        lines.append(f'{summary}\n')
    return '\n'.join(lines) + '\n'


def append_to_research_md(path, section, date_str):
    """Append section to research.md and update Freshness Index rows."""
    with open(path, 'r') as f:
        content = f.read()

    # Update Freshness Index: BFE grant programs row
    content = re.sub(
        r'\| BFE grant programs\s*\|[^|]*\|[^|]*\|[^|]*\|',
        f'| BFE grant programs | {date_str} | Auto-scanned | `scripts/research_cron.py` |',
        content,
    )
    # Update Competitive landscape row
    content = re.sub(
        r'\| Competitive landscape\s*\|[^|]*\|[^|]*\|[^|]*\|',
        f'| Competitive landscape | {date_str} | Auto-scanned | `scripts/research_cron.py` |',
        content,
    )

    content = content.rstrip() + '\n' + section
    with open(path, 'w') as f:
        f.write(content)


def main(research_md_path=None):
    """Run the full research scan."""
    if research_md_path is None:
        research_md_path = os.path.join(os.path.dirname(__file__), '..', 'research.md')
    research_md_path = os.path.abspath(research_md_path)

    date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    findings = {}
    categories_scanned = 0

    for category, queries in SEARCH_TOPICS.items():
        all_results = []
        for query in queries:
            results = search_brave(query)
            all_results.extend(results)

        if not all_results:
            findings[category] = ''
            continue

        # Deduplicate by URL
        seen = set()
        unique = []
        for r in all_results:
            if r['url'] not in seen:
                seen.add(r['url'])
                unique.append(r)

        summary = summarize_with_llm(CATEGORY_LABELS.get(category, category), unique[:8])
        findings[category] = summary
        if summary:
            categories_scanned += 1

    if categories_scanned == 0:
        print(f'[research_cron] {date_str}: no findings from any category')
        return {'success': False, 'categories_scanned': 0, 'date': date_str}

    section = format_research_section(date_str, findings)
    append_to_research_md(research_md_path, section, date_str)
    print(f'[research_cron] {date_str}: scanned {categories_scanned} categories, appended to {research_md_path}')
    return {'success': True, 'categories_scanned': categories_scanned, 'date': date_str}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Weekly research scan for OpenLEG')
    parser.add_argument('--path', default=None, help='Path to research.md')
    args = parser.parse_args()
    result = main(research_md_path=args.path)
    sys.exit(0 if result['success'] else 1)
