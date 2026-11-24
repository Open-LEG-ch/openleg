# SPDX-License-Identifier: AGPL-3.0-or-later
"""Effective robots.txt rules per crawler, read the way the crawlers read them.

Google, Bing, OpenAI, Anthropic and Perplexity apply only the most specific
matching group and ignore ``User-agent: *`` once a named group matches. Protego
implements that group choice and Google's longest-match rule precedence, so a
named group without the shared exclusions would show up here as an allowed URL.
"""

import re

import pytest
from protego import Protego

from tests import test_i18n

app_module = test_i18n.app_module
isolated_environment = test_i18n.isolated_environment
web = test_i18n.web

SEARCH_CRAWLERS = (
    "Googlebot",
    "Bingbot",
    "OAI-SearchBot",
    "Claude-SearchBot",
    "PerplexityBot",
)
USER_FETCHERS = ("ChatGPT-User", "Claude-User", "Perplexity-User")
TRAINING_CRAWLERS = ("GPTBot", "ClaudeBot", "Google-Extended", "Applebot-Extended")
ALL_CRAWLERS = SEARCH_CRAWLERS + USER_FETCHERS + TRAINING_CRAWLERS

PUBLIC_URLS = (
    "/",
    "/how-it-works",
    "/leg-gruenden",
    "/gemeinde/onboarding",
    "/gemeinde/profil/261",
    "/rangliste",
    "/api/v1/docs",
    "/api/v1/openapi.json",
    "/verein",
    "/verein?lang=en",
    "/verein/finanzen",
    "/verein/spenden",
    "/veranstaltungen",
    "/leg-verzeichnis",
    "/wissen/glossar",
    "/llms.txt",
    "/static/css/openleg.css",
)
EXCLUDED_URLS = (
    # API
    "/api/billing/community/c1/period/1",
    "/api/v1/municipalities",
    "/gemeinde/api/municipalities",
    # Administration, including the LEG administration workspace
    "/admin/overview",
    "/utility/admin/clients",
    "/leg/community/create",
    "/leg/community/c1/members",
    "/leg/community/c1/billing",
    "/leg/document/1",
    # Private Verein areas
    "/verein/vorstand/antraege",
    "/verein/vorstand/login",
    "/verein/mitglied",
    "/verein/mitglied/zugang",
    # Confirmation links
    "/confirm/token",
    "/interest/confirm/token",
    "/verein/bestaetigen/token",
    "/registry/verify/token",
    # Unsubscribe links
    "/unsubscribe/token",
    # Credential and access links
    "/dashboard/access/token",
    "/gemeinde/access/token",
    "/utility/access/token",
    "/verein/mitglied/zugang/token",
    "/leg/community/c1/operator-api/credentials",
    "/oauth/authorize?client_id=c1",
)


def _robots_body(web):
    response = web.test_client().get("/robots.txt")
    assert response.status_code == 200
    return response.get_data(as_text=True)


def _robots(web):
    return Protego.parse(_robots_body(web))


@pytest.mark.parametrize("crawler", ALL_CRAWLERS)
def test_crawler_is_excluded_from_private_paths(web, crawler):
    robots = _robots(web)
    allowed = [url for url in EXCLUDED_URLS if robots.can_fetch(url, crawler)]
    assert allowed == []


@pytest.mark.parametrize("crawler", ALL_CRAWLERS)
def test_crawler_may_fetch_public_pages(web, crawler):
    robots = _robots(web)
    blocked = [url for url in PUBLIC_URLS if not robots.can_fetch(url, crawler)]
    assert blocked == []


def test_every_named_group_keeps_the_shared_rules(web):
    body = _robots_body(web)
    robots = Protego.parse(body)
    named = re.findall(r"^User-agent: (?!\*$)(.+)$", body, flags=re.MULTILINE)
    assert named
    for agent in named:
        assert [url for url in EXCLUDED_URLS if robots.can_fetch(url, agent)] == []
        assert [url for url in PUBLIC_URLS if not robots.can_fetch(url, agent)] == []


@pytest.mark.parametrize("crawler", ALL_CRAWLERS)
def test_private_routes_deny_crawlers_without_relying_on_robots(web, crawler):
    # User-initiated fetchers may ignore robots.txt, so access control must hold.
    client = web.test_client()
    headers = {"User-Agent": f"Mozilla/5.0 (compatible; {crawler}/1.0)"}
    credentials = client.get(
        "/leg/community/c1/operator-api/credentials", headers=headers
    )
    assert credentials.status_code == 403
    access = client.get("/dashboard/access/unknown-token", headers=headers)
    assert access.status_code == 302
    assert access.headers["Location"].endswith("access=invalid")


def test_launch_flags_leave_robots_alone_and_draft_noindex_visible(web):
    closed = _robots_body(web)
    web.config["SITE_RELEASE_ENABLED"] = True
    draft = web.test_client().get("/verein")
    assert '<meta name="robots" content="noindex, nofollow">' in draft.text
    assert _robots_body(web) == closed
    robots = Protego.parse(closed)
    assert all(robots.can_fetch("/verein", crawler) for crawler in SEARCH_CRAWLERS)
    web.config["SITE_TRANSLATIONS_APPROVED"] = True
    assert _robots_body(web) == closed


def _labelled_groups(body):
    groups = {}
    label = None
    for line in body.splitlines():
        if line.startswith("# "):
            label = line.lower()
        elif line.startswith("User-agent: "):
            groups.setdefault(label, set()).add(line.removeprefix("User-agent: "))
    return groups


def test_robots_separates_search_user_fetch_and_training_groups(web):
    groups = _labelled_groups(_robots_body(web))
    by_kind = {
        kind: agents
        for label, agents in groups.items()
        for kind in ("search crawlers", "user-initiated", "training")
        if label and kind in label
    }
    assert by_kind["search crawlers"] == {"OAI-SearchBot", "PerplexityBot"}
    assert by_kind["user-initiated"] == {"ChatGPT-User"}
    # The existing preference welcomes training crawlers to public pages.
    assert by_kind["training"] == set(TRAINING_CRAWLERS)
