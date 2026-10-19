# SPDX-License-Identifier: AGPL-3.0-or-later
"""Localized media has real assets, not German screenshots behind French alt text."""

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from tests import test_i18n

isolated_environment = test_i18n.isolated_environment
app_module = test_i18n.app_module
web = test_i18n.web

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("locale", ["de-CH", "fr-CH"])
@pytest.mark.parametrize(
    "asset",
    [
        "product/resident-dashboard.gif",
        "product/resident-dashboard-static.webp",
        "product/municipality-dashboard.gif",
        "product/municipality-dashboard-static.webp",
        "product/leg-formation.gif",
        "product/leg-formation-static.webp",
        "screenshots/dashboard-gemeinde.webp",
        "og-image.png",
        "brand/flow-how-it-works.svg",
        "brand/flow-open-source.svg",
        "brand/flow-gemeinden.svg",
    ],
)
def test_selected_media_locale_resolves_to_an_existing_asset(web, locale, asset):
    from i18n import localized_asset

    with web.test_request_context("/?lang=" + locale):
        target = localized_asset("/static/images/" + asset)
    assert (ROOT / target.lstrip("/")).is_file()
    assert ("/fr-CH/" in target) == (locale == "fr-CH")


def test_generated_svg_labels_are_french_and_well_formed():
    for filename in ("flow-how-it-works", "flow-open-source", "flow-gemeinden"):
        path = ROOT / f"static/images/brand/fr-CH/{filename}.svg"
        svg = ET.parse(path).getroot()
        assert svg.attrib["aria-label"]
        assert "Lizenz" not in path.read_text()
    assert (
        "Licence"
        in (ROOT / "static/images/brand/fr-CH/flow-open-source.svg").read_text()
    )


def test_localized_svg_build_check_rejects_stale_output(tmp_path, monkeypatch):
    from scripts import translations

    assets = tmp_path / "templates/assets/brand"
    assets.mkdir(parents=True)
    (assets / "test.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><text>{{ _("Sprache") }}</text></svg>'
    )
    monkeypatch.setattr(translations, "ROOT", tmp_path)
    monkeypatch.setattr(translations, "TRANSLATIONS", ROOT / "translations")
    translations.build_svg_assets(check=False)
    french = tmp_path / "static/images/brand/fr-CH/test.svg"
    assert "Langue" in french.read_text()
    translations.build_svg_assets(check=True)
    french.write_text("stale")
    with pytest.raises(ValueError, match="asset is stale"):
        translations.build_svg_assets(check=True)
