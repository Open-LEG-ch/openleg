# SPDX-License-Identifier: AGPL-3.0-or-later
"""Render outbound mail in the recipient's language, including outside Flask."""

from functools import lru_cache
from pathlib import Path

from babel.support import Translations
from jinja2 import Environment, FileSystemLoader

from i18n import localized_url
from locale_policy import resolve_locale

_ROOT = Path(__file__).resolve().parent
_TEMPLATES = frozenset(
    f"emails/day_{day}_{name}.html"
    for day, name in [
        (0, "welcome"),
        (3, "smartmeter"),
        (7, "consumption"),
        (14, "formation"),
    ]
).union({"emails/municipality_interest.html"})


@lru_cache(maxsize=2)
def _catalog(locale):
    return Translations.load(_ROOT / "translations", locales=[locale])


def gettext(message, *, locale="de_CH", **variables):
    """Translate a complete message without reading request or tenant language."""
    message = _catalog(resolve_locale(locale)).gettext(message)
    return message % variables if variables else message


@lru_cache(maxsize=2)
def _environment(locale):
    environment = Environment(
        loader=FileSystemLoader(_ROOT / "templates"),
        autoescape=True,
        extensions=["jinja2.ext.i18n"],
    )
    environment.install_gettext_translations(_catalog(locale), newstyle=True)
    environment.filters["localized_url"] = lambda url: localized_url(url, locale)
    return environment


def render_template(name, *, locale="de_CH", **context):
    """Keep user data in render arguments, never in shared environment globals."""
    if name not in _TEMPLATES:
        raise ValueError("unsupported_email_template")
    selected = resolve_locale(locale)
    return (
        _environment(selected)
        .get_template(name)
        .render(
            **context,
            current_locale=selected,
        )
    )
