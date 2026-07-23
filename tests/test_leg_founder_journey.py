# SPDX-License-Identifier: AGPL-3.0-or-later
"""Rendered founder-journey contracts for issue #216."""

import importlib
import json
import os
from html.parser import HTMLParser
from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit

import pytest
from werkzeug.exceptions import MethodNotAllowed, NotFound


class FounderPageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stages = []
        self.faq = []
        self.links = []
        self.jsonld = []
        self.h1_count = 0
        self._stage = None
        self._faq = None
        self._capture = None
        self._script = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "h1":
            self.h1_count += 1
        if tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])
        if number := attrs.get("data-formation-stage"):
            self._stage = {"number": number, "slots": set(), "title": []}
            self.stages.append(self._stage)
        if self._stage and (slot := attrs.get("data-stage-slot")):
            self._stage["slots"].add(slot)
        if self._stage and tag == "h3":
            self._capture = self._stage["title"]
        if "data-faq-item" in attrs:
            self._faq = {"question": [], "answer": []}
            self.faq.append(self._faq)
        if self._faq and tag == "summary":
            self._capture = self._faq["question"]
        if self._faq and "data-faq-answer" in attrs:
            self._capture = self._faq["answer"]
        if tag == "script" and attrs.get("type") == "application/ld+json":
            self._script = []

    def handle_data(self, data):
        if self._capture is not None:
            self._capture.append(data)
        if self._script is not None:
            self._script.append(data)

    def handle_endtag(self, tag):
        if tag in {"h3", "summary", "p"}:
            self._capture = None
        if tag == "li" and self._stage:
            self._stage = None
        if tag == "details":
            self._faq = None
        if tag == "script" and self._script is not None:
            self.jsonld.append(json.loads("".join(self._script)))
            self._script = None


@pytest.fixture
def founder_page():
    with patch.dict(
        os.environ,
        {
            "DATABASE_URL": "postgresql://x:x@localhost/x",
            "REDIS_URL": "memory://",
            "APP_BASE_URL": "http://localhost:5003",
        },
    ):
        with (
            patch("database.is_db_available", return_value=True),
            patch("database._connection_pool", MagicMock()),
        ):
            import app as app_module

            app_module = importlib.reload(app_module)
            response = app_module.app.test_client().get("/leg-gruenden")
            parser = FounderPageParser()
            parser.feed(response.get_data(as_text=True))
            yield app_module.app, response, parser


def _jsonld(parser, schema_type):
    for document in parser.jsonld:
        if document.get("@type") == schema_type:
            return document
    raise AssertionError(f"JSON-LD schema missing: {schema_type}")


def test_leg_gruenden_route_returns_200(founder_page):
    _, response, parser = founder_page
    assert response.status_code == 200
    assert parser.h1_count == 1


def test_founder_timeline_renders_four_slots_per_stage(founder_page):
    _, _, parser = founder_page
    assert len(parser.stages) >= 5
    for number, stage in enumerate(parser.stages, 1):
        assert stage["number"] == str(number)
        assert "".join(stage["title"]).strip()
        assert stage["slots"] == {"start", "action", "evidence", "next"}


def test_founder_jsonld_matches_visible_timeline_and_faq(founder_page):
    _, _, parser = founder_page
    howto = _jsonld(parser, "HowTo")
    faq = _jsonld(parser, "FAQPage")

    visible_steps = [
        " ".join("".join(stage["title"]).split()) for stage in parser.stages
    ]
    assert [step["name"] for step in howto["step"]] == visible_steps

    visible_faq = {
        " ".join("".join(item["question"]).split()): " ".join(
            "".join(item["answer"]).split()
        )
        for item in parser.faq
    }
    schema_faq = {
        item["name"]: item["acceptedAnswer"]["text"] for item in faq["mainEntity"]
    }
    assert schema_faq == visible_faq


def test_every_internal_founder_link_targets_a_real_route(founder_page):
    app, _, parser = founder_page
    adapter = app.url_map.bind("localhost")
    paths = {urlsplit(href).path for href in parser.links if href.startswith("/")}
    assert {"/leg-check", "/leg-verzeichnis", "/leg/dashboard/demo"} <= paths
    assert {
        "/leg-kalkulator",
        "/pricing",
        "/self-host",
        "/gemeinde/onboarding",
    } <= paths
    for path in paths:
        try:
            adapter.match(path, method="GET")
        except MethodNotAllowed:
            adapter.match(path)
        except NotFound:
            pytest.fail(f"No route for internal link: {path}")
