# SPDX-License-Identifier: AGPL-3.0-or-later
"""Request-local presentation languages; domain values remain language-neutral."""

import re
from datetime import date
from functools import wraps
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from babel.dates import format_date
from babel.numbers import format_decimal
from flask import (
    current_app,
    g,
    has_app_context,
    has_request_context,
    render_template,
    request,
    session,
)
from flask_babel import Babel, get_locale
from flask_babel import gettext as babel_gettext
from flask_babel import ngettext as babel_ngettext
from jinja2 import pass_context
from werkzeug.exceptions import HTTPException

from locale_policy import normalize_locale, resolve_locale
from translation_markers import gettext_noop

SUPPORTED_LOCALES = {"de_CH": "Deutsch", "fr_CH": "Français"}

LOCALIZED_ASSETS = frozenset(
    {
        "/static/images/og-image.png",
        "/static/images/og-image.svg",
        "/static/images/product/resident-dashboard.gif",
        "/static/images/product/resident-dashboard-static.webp",
        "/static/images/product/municipality-dashboard.gif",
        "/static/images/product/municipality-dashboard-static.webp",
        "/static/images/product/leg-formation.gif",
        "/static/images/product/leg-formation-static.webp",
        "/static/images/screenshots/dashboard-gemeinde.webp",
        "/static/images/brand/flow-how-it-works.svg",
        "/static/images/brand/flow-gemeinden.svg",
        "/static/images/brand/flow-open-source.svg",
    }
)


def localized_asset(path):
    """Choose a shipped media variant; photos, brands and other URLs stay neutral."""
    if path not in LOCALIZED_ASSETS:
        return path
    selected = (
        str(get_locale())
        if has_app_context() and "babel" in current_app.extensions
        else "de_CH"
    )
    if selected == "de_CH":
        return path
    directory, filename = path.rsplit("/", 1)
    return directory + "/" + selected.replace("_", "-") + "/" + filename


HTTP_ERROR_TITLES = {
    400: gettext_noop("Ungültige Anfrage"),
    401: gettext_noop("Anmeldung erforderlich"),
    403: gettext_noop("Zugriff verweigert"),
    404: gettext_noop("Seite nicht gefunden"),
    405: gettext_noop("Methode nicht erlaubt"),
    406: gettext_noop("Format nicht verfügbar"),
    408: gettext_noop("Zeitüberschreitung der Anfrage"),
    409: gettext_noop("Aktion derzeit nicht möglich"),
    410: gettext_noop("Inhalt nicht mehr verfügbar"),
    413: gettext_noop("Anfrage zu gross"),
    415: gettext_noop("Dateiformat nicht unterstützt"),
    422: gettext_noop("Angaben können nicht verarbeitet werden"),
    500: gettext_noop("Interner Fehler"),
    501: gettext_noop("Funktion nicht verfügbar"),
    502: gettext_noop("Fehler beim vorgelagerten Dienst"),
    503: gettext_noop("Dienst vorübergehend nicht verfügbar"),
    504: gettext_noop("Zeitüberschreitung beim vorgelagerten Dienst"),
}


def format_period_label(value):
    """Localize display month names without changing frozen invoice values."""
    months = (
        "Januar",
        "Februar",
        "März",
        "April",
        "Mai",
        "Juni",
        "Juli",
        "August",
        "September",
        "Oktober",
        "November",
        "Dezember",
    )
    match = re.fullmatch(r"([A-Za-zä]+) ([0-9]{4})", str(value))
    if not match or match[1] not in months:
        return value
    locale = (
        str(get_locale())
        if has_app_context() and "babel" in current_app.extensions
        else "de_CH"
    )
    # Preserve existing German casing and formatting byte for byte.
    if locale == "de_CH":
        return value
    return format_date(
        date(int(match[2]), months.index(match[1]) + 1, 1),
        format="MMMM yyyy",
        locale=locale,
    )


def format_rate_label(value):
    """Translate only a canonical display unit; never parse or round money."""
    suffix = " Rp./kWh"
    if isinstance(value, str) and value.endswith(suffix):
        return value[: -len(suffix)] + " " + gettext("Rp./kWh")
    return gettext(value) if isinstance(value, str) else value


def format_number_label(value):
    """Format display-only counts without rounding or altering stored values."""
    locale = (
        str(get_locale())
        if has_app_context() and "babel" in current_app.extensions
        else "de_CH"
    )
    return format_decimal(value, locale=locale, decimal_quantization=False)


def gettext(message, **variables):
    """Keep domain helpers usable without a configured Flask application."""
    if not has_app_context() or "babel" not in current_app.extensions:
        return message % variables if variables else message
    return babel_gettext(message, **variables)


def ngettext(singular, plural, number, **variables):
    """Use the catalog's plural rules when available, German otherwise."""
    if not has_app_context() or "babel" not in current_app.extensions:
        message = singular if number == 1 else plural
        return message % {"num": number, **variables}
    return babel_ngettext(singular, plural, number, **variables)


def select_locale():
    """Resolve explicit choice, signed session, tenant, then installation default.

    Keep German as the existing default instead of silently switching existing
    installations based on a browser's Accept-Language header.
    """
    default = (
        normalize_locale(current_app.config.get("BABEL_DEFAULT_LOCALE")) or "de_CH"
    )
    if not has_request_context():
        return default
    return resolve_locale(
        request.args.get("lang"),
        session.get("locale"),
        getattr(g, "tenant", {}).get("locale"),
        default,
    )


def language_switch_url(locale):
    """Retain page/filter context without accepting a redirect destination."""
    selected = normalize_locale(locale)
    if selected is None:
        raise ValueError("Unsupported display language")
    if not has_request_context():
        return "/?" + urlencode({"lang": selected.replace("_", "-")})
    arguments = [
        (key, value) for key, value in request.args.items(multi=True) if key != "lang"
    ]
    arguments.append(("lang", selected.replace("_", "-")))
    return "/" + request.path.lstrip("/") + "?" + urlencode(arguments)


def localized_url(url, locale=None):
    """Carry a display choice into links, including durable recipient links.

    An explicit recipient locale always travels with the URL. Keep historical
    German URLs only for implicit choices on German-default installations.
    """
    selected = normalize_locale(locale)
    if selected is None:
        selected = (
            str(get_locale())
            if has_app_context() and "babel" in current_app.extensions
            else select_locale()
            if has_app_context()
            else "de_CH"
        )
    parts = urlsplit(url)
    default = (
        normalize_locale(current_app.config.get("BABEL_DEFAULT_LOCALE"))
        if has_app_context()
        else "de_CH"
    )
    arguments = parse_qsl(parts.query, keep_blank_values=True)
    if (
        selected == "de_CH"
        and default == "de_CH"
        and locale is None
        and all(
            normalize_locale(value) == selected
            for key, value in arguments
            if key == "lang"
        )
    ):
        return url
    arguments = [(key, value) for key, value in arguments if key != "lang"]
    arguments.append(("lang", selected.replace("_", "-")))
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(arguments), parts.fragment)
    )


def _request_local_filter(formatter):
    @pass_context
    @wraps(formatter)
    def render(_context, *args, **kwargs):
        return formatter(*args, **kwargs)

    return render


def init_app(app):
    """Install gettext in Python/Jinja and publish presentation-only metadata."""
    app.config.setdefault("BABEL_DEFAULT_LOCALE", "de_CH")
    app.config.setdefault("BABEL_DEFAULT_TIMEZONE", "Europe/Zurich")
    default = normalize_locale(app.config["BABEL_DEFAULT_LOCALE"])
    if not default:
        raise ValueError("BABEL_DEFAULT_LOCALE must be de_CH or fr_CH")
    app.config["BABEL_DEFAULT_LOCALE"] = default
    Babel(app, configure_jinja=False, locale_selector=select_locale)
    app.jinja_env.add_extension("jinja2.ext.i18n")
    # Old-style Jinja preserves literal '%' in German source text. Flask-Babel's
    # gettext still supports named interpolation when kwargs are supplied.
    app.jinja_env.install_gettext_callables(gettext, ngettext, newstyle=False)
    # Locale-sensitive filters must execute per render, even for literals.
    # Without pass_context Jinja folds them into the compiled template cache.
    for name, formatter in {
        "periodlabel": format_period_label,
        "ratelabel": format_rate_label,
        "numberlabel": format_number_label,
        "localized_url": localized_url,
        "localized_asset": localized_asset,
    }.items():
        app.jinja_env.filters[name] = _request_local_filter(formatter)

    @app.errorhandler(HTTPException)
    def localized_http_error(error):
        # Preserve status and protocol headers (Allow, Retry-After, etc.).
        # Never expose exception descriptions, which can contain private data.
        response = error.get_response()
        if response.mimetype == "text/html":
            title = HTTP_ERROR_TITLES.get(
                error.code, gettext_noop("Anfrage nicht möglich")
            )
            response.set_data(
                render_template(
                    "http_error.html", status=error.code, title=gettext(title)
                )
            )
        return response

    @app.before_request
    def remember_language():
        selected = normalize_locale(request.args.get("lang"))
        if request.method == "GET" and selected and request.endpoint != "static":
            session["locale"] = selected

    @app.context_processor
    def language_context():
        return {
            "current_locale": str(get_locale()).replace("_", "-"),
            "supported_locales": SUPPORTED_LOCALES,
            "language_switch_url": language_switch_url,
        }

    @app.after_request
    def language_headers(response):
        if response.mimetype in {"text/html", "image/svg+xml"}:
            language = str(get_locale()).replace("_", "-")
            if request.endpoint == "static":
                language = next(
                    (
                        locale.replace("_", "-")
                        for locale in SUPPORTED_LOCALES
                        if "/" + locale.replace("_", "-") + "/" in request.path
                    ),
                    "de-CH",
                )
            response.headers["Content-Language"] = language
            response.vary.add("Cookie")
        return response
