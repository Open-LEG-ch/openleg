# SPDX-License-Identifier: AGPL-3.0-or-later
"""The complete site is opt-in; drafting does not invent a founded Verein."""

import re
from html import unescape

import pytest

from tests import test_i18n

app_module = test_i18n.app_module
isolated_environment = test_i18n.isolated_environment
web = test_i18n.web


@pytest.mark.parametrize(
    "path",
    ["/software", "/wissen", "/dokumente", "/community", "/mitmachen", "/kontakt"],
)
def test_new_project_destinations_require_complete_site(web, path):
    client = web.test_client()
    assert client.get(path).status_code == 404
    web.config["SITE_RELEASE_ENABLED"] = True
    response = client.get(path)
    assert response.status_code == 200
    assert response.text.count("<h1") == 1
    assert 'href="/verein"' in response.text


def test_release_costs_do_not_offer_unapproved_hosting(web):
    web.config["SITE_RELEASE_ENABLED"] = True
    response = web.test_client().get("/pricing")
    assert response.status_code == 200
    assert "Was OpenLEG kostet." in response.text
    assert "CHF 0" not in response.text
    assert "Für immer" not in response.text
    assert "AGPL-3.0-or-later" in response.text


def test_release_icons_match_the_page_head_everywhere(web):
    """Safari, iOS, crawlers and /favicon.ico get the same release icon as the tab."""
    import json
    import struct
    from pathlib import Path

    release = Path(web.static_folder) / "images" / "release"
    client = web.test_client()
    legacy = client.get("/favicon.ico").data
    web.config["SITE_RELEASE_ENABLED"] = True
    html = client.get("/pricing").text
    for href in (
        "/favicon.ico?v=2",
        "/static/images/release/favicon.svg?v=2",
        "/static/images/release/apple-touch-icon.png",
        "/static/images/release/site.webmanifest",
    ):
        assert f'href="{href}"' in html
    assert '<meta name="theme-color" content="#243b85">' in html

    icon = client.get("/favicon.ico")
    assert icon.mimetype == "image/vnd.microsoft.icon"
    assert icon.data == (release / "favicon.ico").read_bytes() != legacy
    # Letters are shapes, so the icon does not depend on the visitor's fonts.
    assert "<text" not in (release / "favicon.svg").read_text()

    def png_size(path):
        response = client.get(path)
        assert response.status_code == 200
        assert response.data[:8] == b"\x89PNG\r\n\x1a\n"
        width, height = struct.unpack(">II", response.data[16:24])
        return f"{width}x{height}"

    assert png_size("/static/images/release/apple-touch-icon.png") == "180x180"
    manifest = json.loads(client.get("/static/images/release/site.webmanifest").data)
    assert manifest["theme_color"] == "#243b85"
    assert [png_size(entry["src"]) for entry in manifest["icons"]] == [
        entry["sizes"] for entry in manifest["icons"]
    ]


def test_text_on_dark_release_surfaces_is_white():
    """Orange or grey text on the navy sections was hard to read; text is white."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    css = "".join(
        (root / "static" / "css" / name).read_text()
        for name in ("homepage.css", "site_story.css")
    )
    for selector in (
        ".home-practice .home-lead",
        ".home-flow p",
        ".home-example-label",
        ".release-software-band > p, .release-software-band .release-caption",
        ".release-software-band .release-workflow span",
    ):
        rule = re.search(re.escape(selector) + r" \{([^}]*)\}", css)
        assert rule and "color: #fffdf8;" in rule.group(1), selector
    for template in (
        "partials/install_console.html",
        "open_source.html",
        "dashboard.html",
    ):
        markup = (root / "templates" / template).read_text()
        if template == "dashboard.html":
            markup = markup.split('aria-labelledby="readiness-title"', 1)[1]
            markup = markup.split("</section>", 1)[0]
            assert "text-sage-light" not in markup and "text-paper" not in markup
        for grey in ("text-slate-100", "text-slate-300", "text-slate-400"):
            assert grey not in markup, (template, grey)
        assert "text-accent-light" not in markup, template
    script = (root / "static" / "js" / "install_console.js").read_text()
    assert "text-slate-400" not in script and "border-indigo-400" not in script
    api = (root / "templates" / "api_docs.html").read_text()
    example = re.search(r"\.api-copy-example \{([^}]*)\}", api)
    assert example and "color: #ffffff;" in example.group(1)
    # No other rule or element inside the examples may set a different colour.
    for selector, body in re.findall(r"([^{}]*api-copy-example[^{}]*)\{([^}]*)\}", api):
        colours = re.findall(r"(?<![-\w])color:\s*([^;]+);", body)
        assert colours in ([], ["#ffffff"]), (selector, colours)
    examples = re.findall(
        r'<pre class="api-copy-example"[^>]*>(.*?)</pre>', api, re.DOTALL
    )
    assert examples and all(e.startswith("<code>") for e in examples)
    profile = (root / "templates" / "gemeinde" / "profil.html").read_text()
    callout = profile.split('<section class="mt-8 bg-brand', 1)[1].split(
        "</section>", 1
    )[0]
    grey = re.compile(r"\btext-(?:white/|ink|slate|gray|sage|stone|zinc|neutral|muted)")
    for element in re.findall(r"<[a-z][^>]*>", callout):
        # The white button keeps brand-coloured text on its own white background.
        if "bg-white" not in element:
            assert not grey.search(element), element


@pytest.mark.parametrize("path", ["/", "/software", "/pricing", "/how-it-works"])
def test_release_header_offers_a_visible_github_button(web, monkeypatch, path):
    """The repository link sits outside the collapsible menu, so phones see it too."""
    client = _release_home(web, monkeypatch)
    html = client.get(path).text
    # The language switcher nests its own <nav>, so cut at the menu script.
    nav = html.split("data-release-nav", 1)[1].split("site_navigation.js", 1)[0]
    # The link follows the closed menu container, not an item inside it.
    assert re.search(r'</details>\s*</div>\s*<a class="release-github"', nav)
    menu_end = nav.index('<a class="release-github"')
    button = nav[menu_end:].split("</a>", 1)[0]
    assert 'href="https://github.com/Open-LEG-ch/openleg"' in button
    assert 'class="github-mark"' in button and 'aria-hidden="true"' in button
    assert "<span>GitHub</span>" in button


def test_homepage_github_invitation_is_a_button(web, monkeypatch):
    html = _release_home(web, monkeypatch).get("/").text
    verein = html.split('aria-labelledby="home-verein-title"', 1)[1].split(
        "</section>", 1
    )[0]
    button = verein.split('class="release-button release-button-github"', 1)[1]
    button = button.split("</a>", 1)[0]
    assert 'href="https://github.com/Open-LEG-ch/openleg"' in button
    assert 'class="github-mark"' in button and "Auf GitHub mitarbeiten" in button


def test_verified_formation_metadata_updates_public_status_and_join_link(web):
    web.config.update(
        SITE_RELEASE_ENABLED=True,
        VEREIN_FORMATION_DATE="2026-01-01",
        VEREIN_STATUTES_URL="/statuten.pdf",
    )
    response = web.test_client().get("/verein")
    assert "noch nicht gegründet" not in response.text
    assert "Der Verein wurde am 1. Januar 2026 gegründet" in response.text
    assert "Sitz in Obersiggenthal" in response.text
    assert 'href="/verein/beitreten"' not in response.text
    assert 'href="mailto:hallo@openleg.ch"' in response.text
    assert 'href="/statuten.pdf"' in response.text
    web.config.update(
        VEREIN_ACCEPTING_APPLICATIONS=True,
        VEREIN_CONTACT_ADDRESS="Fictional contact",
        VEREIN_PRIVACY_VERSION="fictional-reviewed-v1",
        VEREIN_DATA_CONTROLLER="Fictional test association",
    )
    assert 'href="/verein/beitreten"' in web.test_client().get("/verein").text


def test_new_verein_surface_is_not_published_by_default(web):
    assert web.test_client().get("/verein").status_code == 404


def test_release_verein_keeps_story_without_inventing_formation(web):
    web.config["SITE_RELEASE_ENABLED"] = True
    response = web.test_client().get("/verein")
    assert response.status_code == 200
    assert "Verein openLEG Schweiz" in response.text
    assert "Warum dieser Verein entstehen soll" in response.text
    assert "Der Verein wurde am" not in response.text
    assert "Verabschiedete Statuten lesen" not in response.text
    assert "20. Oktober 2026" not in response.text
    assert "CHF 50" in response.text
    assert 'name="email"' not in response.text


def test_release_shell_has_static_identity_and_task_navigation(web):
    web.config["SITE_RELEASE_ENABLED"] = True
    response = web.test_client().get("/verein")
    assert 'data-site-release="true"' in response.text
    assert "ol-caret" not in response.text
    assert 'href="/login"' in response.text
    assert 'href="/utility/login"' in response.text
    assert 'aria-controls="release-menu"' in response.text
    assert 'src="/static/js/site_navigation.js"' in response.text
    for label in ("LEG gründen", "Software", "Für Gemeinden", "Wissen", "Verein"):
        assert label in response.text


def test_release_home_explains_tasks_and_keeps_real_address_intake(web, monkeypatch):
    import database
    import homepage_view_model

    web.config["SITE_RELEASE_ENABLED"] = True
    monkeypatch.setattr(
        database, "get_stats", lambda city_id=None: {"total_buildings": 0}
    )
    monkeypatch.setattr(homepage_view_model, "ranking_extremes", lambda: ([], [], 0))
    client = web.test_client()
    response = client.get("/")
    assert response.status_code == 200
    assert response.text.count("<h1") == 1
    assert "Solarstrom im Quartier teilen. Eine LEG gründen." in response.text
    assert 'href="/leg-gruenden"' in response.text
    assert 'class="release-home"' in response.text
    assert 'viewBox="0 0 900 730"' in response.text
    for identifier in (
        "registrieren",
        "address-input",
        "address-status",
        "map",
        "step-address",
    ):
        assert response.text.count('id="' + identifier + '"') == 1
    assert "Fiktives Quartier" in response.text
    assert "Zum Verein" in response.text
    with client.session_transaction() as session:
        session["dashboard_building_id"] = "existing-resident"
    assert client.get("/").headers["Location"] == "/dashboard"


def _release_home(web, monkeypatch):
    import database
    import homepage_view_model

    web.config["SITE_RELEASE_ENABLED"] = True
    monkeypatch.setattr(
        database, "get_stats", lambda city_id=None: {"total_buildings": 0}
    )
    monkeypatch.setattr(homepage_view_model, "ranking_extremes", lambda: ([], [], 0))
    return web.test_client()


def test_practice_village_is_not_offered_without_a_configured_service(web, monkeypatch):
    client = _release_home(web, monkeypatch)
    web.jinja_env.globals["sandbox_url"] = ""
    for path in ("/", "/software"):
        response = client.get(path)
        assert response.status_code == 200
        assert "release-practice" not in response.text
        assert "/sandbox" not in response.text
    assert 'href="/dashboard/demo"' in client.get("/").text


def test_practice_village_links_each_role_to_its_preselected_account(web, monkeypatch):
    from sandbox_seed import ACCOUNTS

    client = _release_home(web, monkeypatch)
    web.jinja_env.globals["sandbox_url"] = "https://sonnwil.example.org/sandbox"
    home = client.get("/").text
    assert 'href="https://sonnwil.example.org/sandbox"' in home
    assert 'class="release-practice"' not in home
    for path in ("/software",):
        text = client.get(path).text
        assert text.count('class="release-practice"') == 1
        assert 'href="https://sonnwil.example.org/sandbox"' in text
        roles = re.findall(r'sandbox\?konto=([a-z]+)#konto-\1"', text)
        assert roles == [
            "resident",
            "founder",
            "administrator",
            "municipality",
            "utility",
        ]
        assert set(roles) <= set(ACCOUNTS)
    french = client.get("/software?lang=fr-CH").text
    assert (
        'href="https://sonnwil.example.org/sandbox?konto=founder&amp;lang=fr-CH#konto-founder"'
        in french
    )


@pytest.mark.parametrize(
    "locale,story,closed,pending",
    [
        (
            "de-CH",
            "Warum dieser Verein entstehen soll",
            "Mitgliedsanträge sind noch nicht geöffnet.",
            (
                r"noch nicht gegründet|geplant\w* (?:sitz|verein|mitgliedschaft)"
                r"|nach (?:der (?:tatsächlichen )?)?gründung|tatsächlicher gründungsstand"
            ),
        ),
        (
            "fr-CH",
            "Pourquoi cette association doit-elle naître?",
            "Les demandes d’adhésion ne sont pas encore ouvertes.",
            (
                r"pas encore constituée|siège (?:est )?prévu|après la constitution"
                r"|état réel de la constitution|adhésion prévue|association prévue"
            ),
        ),
        (
            "en",
            "Why this association should be established",
            "Membership applications are not yet open.",
            (
                r"not yet (?:been )?formed|planned (?:seat|membership|association)"
                r"|after (?:the )?(?:actual creation|founding|formation)|actual status"
            ),
        ),
        (
            "it-CH",
            "Perché questa associazione dovrebbe nascere",
            "Le domande di adesione non sono ancora aperte.",
            (
                r"non è ancora costituita|sede è prevista|dopo (?:la|l'effettiva) costituzione"
                r"|stato effettivo della costituzione|adesione prevista|associazione prevista"
            ),
        ),
    ],
)
def test_public_pages_omit_pending_verein_formation(
    web, monkeypatch, locale, story, closed, pending
):
    client = _release_home(web, monkeypatch)
    for path in (
        "/",
        "/verein",
        "/verein/beitreten",
        "/verein/spenden",
        "/community",
        "/llms.txt",
    ):
        response = client.get(
            path, query_string={"lang": locale}, follow_redirects=True
        )
        assert response.status_code == (503 if path == "/verein/beitreten" else 200)
        assert response.headers["Content-Language"] == locale
        text = unescape(response.text)
        assert not re.search(pending, text, re.IGNORECASE), path
        if path == "/verein":
            assert story in text
        if path == "/verein/beitreten":
            assert closed in text


@pytest.mark.parametrize(
    "locale,warning",
    [
        ("de-CH", "Bitte senden Sie kein Geld an vermutete oder ältere Kontodaten."),
        (
            "fr-CH",
            "N’envoyez pas d’argent sur la base de coordonnées supposées ou anciennes.",
        ),
        ("en", "Do not send money using guessed or older account details."),
        (
            "it-CH",
            "Non inviare denaro sulla base di dati di conto ipotizzati o precedenti.",
        ),
    ],
)
def test_donations_keep_the_payment_fraud_warning(web, monkeypatch, locale, warning):
    """Removing the founding status must not remove the warning about old account details."""
    client = _release_home(web, monkeypatch)
    response = client.get(
        "/verein/spenden", query_string={"lang": locale}, follow_redirects=True
    )
    assert response.status_code == 200
    assert warning in unescape(response.text)


@pytest.mark.parametrize("name", ["site_release.css", "site_story.css", "homepage.css"])
def test_public_release_stylesheets_retire_coral_colours(web, name):
    response = web.test_client().get(f"/static/css/{name}")
    assert response.status_code == 200
    public_styles = re.sub(
        r"\.site-release\.product-shell\s*\{[^}]*\}", "", response.text
    )
    assert not {"#ee8d7c", "#f5d4ca", "#ffd0c2"}.intersection(
        re.findall(r"#[0-9a-f]+", public_styles.lower())
    )
