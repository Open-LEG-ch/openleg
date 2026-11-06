# SPDX-License-Identifier: AGPL-3.0-or-later
"""Municipality onboarding remains within the visitor's disposable village."""

import html
import re
from urllib.parse import parse_qs, urlsplit

import pytest

from tests import test_sandbox_http
from tests.test_sandbox_personas import _start

village = test_sandbox_http.village
pytestmark = pytest.mark.integration


@pytest.mark.parametrize("language", ("de-CH", "fr-CH"))
def test_municipality_invitation_opens_only_its_own_fictional_onboarding(
    village, language
):
    """The displayed invitation opens a local form and denies another visitor."""
    owner = village.test_client()
    prefix, _ = _start(owner, "municipality")
    page = owner.get(prefix + "/gemeinde/dashboard?lang=" + language)
    invitation = html.unescape(
        re.search(r'id="invite-link"[^>]*value="([^"]+)"', page.text).group(1)
    )
    parsed = urlsplit(invitation)
    assert (parsed.scheme, parsed.netloc) == ("http", "localhost")
    assert parsed.path == prefix + "/sandbox/onboarding"
    assert parse_qs(parsed.query) == {"lang": [language]}
    onboarding = owner.get(invitation)
    assert onboarding.status_code == 200
    assert 'id="onboarding"' in onboarding.text
    assert f'<html lang="{language}"' in onboarding.text
    other = village.test_client()
    _start(other, "resident")
    assert other.get(invitation).status_code == 410


@pytest.mark.parametrize(
    ("language", "explanation"),
    [
        ("de-CH", "Vertragsanfragen an OpenLEG sind im Übungsdorf deaktiviert."),
        (
            "fr-CH",
            "Les demandes de contrat à OpenLEG sont désactivées dans le village d'essai.",
        ),
    ],
)
def test_municipality_contract_request_cannot_open_a_real_email_action(
    village, language, explanation
):
    """The real contract-contact action is visibly unavailable during simulation."""
    owner = village.test_client()
    prefix, _ = _start(owner, "municipality")
    page = owner.get(prefix + "/gemeinde/dashboard?lang=" + language)
    assert page.status_code == 200
    assert not re.search(r'href=["\']mailto:', page.text)
    assert 'aria-disabled="true"' in page.text
    assert explanation in html.unescape(page.text)
