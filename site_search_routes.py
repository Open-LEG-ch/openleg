# SPDX-License-Identifier: AGPL-3.0-or-later
"""Public site search pages: `/suche` renders results without JavaScript."""

from urllib.parse import quote, urlencode

from flask import Blueprint, current_app, make_response, render_template, request
from flask_babel import get_locale

from i18n import RELEASE_LOCALES, localized_url
from i18n import gettext as _
from security_extensions import RATE_LIMIT_ANONYMOUS_READ, rate_limit
from site_release import require_complete_site
from site_search import clean_query
from site_search_index import KNOWLEDGE, LEG, MUNICIPALITY, PAGE, SiteSearch
from translation_markers import gettext_noop

site_search_bp = Blueprint("site_search", __name__)
site_search_bp.before_request(require_complete_site)

# Group order on an empty page, URL parameter and heading per result kind.
GROUPS = {
    PAGE: ("seiten", gettext_noop("Seiten")),
    KNOWLEDGE: ("wissen", gettext_noop("Wissen")),
    MUNICIPALITY: ("gemeinden", gettext_noop("Gemeinden")),
    LEG: ("legs", gettext_noop("LEGs")),
}
KIND_BY_PARAMETER = {parameter: kind for kind, (parameter, _label) in GROUPS.items()}
LEG_STATUS_LABELS = {
    "planung": gettext_noop("Planung"),
    "gruendung": gettext_noop("Gründung"),
    "aktiv": gettext_noop("aktiv"),
    "pausiert": gettext_noop("pausiert"),
}
# Shown when nothing was searched yet or nothing was found.
EXAMPLE_QUERIES = (
    gettext_noop("LEG gründen"),
    gettext_noop("Smart Meter"),
    gettext_noop("Netzbetreiber"),
    gettext_noop("Kosten"),
    "3011",
)
PREVIEW_LIMIT = 8
GROUP_PAGE_SIZE = 20
MAX_GROUP_PAGES = 50


def site_search():
    """The application's search; built on first use."""
    app = current_app._get_current_object()
    search = app.extensions.get("site_search")
    if search is None:
        search = app.extensions.setdefault("site_search", SiteSearch(app))
    return search


@site_search_bp.before_app_request
def warm_site_search():
    """Build the index in the background once per process, before anyone types."""
    app = current_app._get_current_object()
    if app.config.get("SITE_RELEASE_ENABLED") is True and not app.testing:
        site_search().warm()


def search_url(query, **arguments):
    return localized_url("/suche?" + urlencode({"q": query, **arguments}))


def _segments(segments):
    return [{"text": segment.text, "match": segment.match} for segment in segments]


def _meta(document):
    if document.kind == MUNICIPALITY:
        kanton = document.data.get("kanton")
        return (
            _("Gemeinde im Kanton %(kanton)s", kanton=kanton)
            if kanton
            else _("Gemeinde")
        )
    if document.kind == LEG:
        status = LEG_STATUS_LABELS.get(document.data.get("leg_status"))
        return " · ".join(
            part
            for part in (
                document.data.get("place"),
                document.data.get("kanton"),
                _(status) if status else "",
            )
            if part
        )
    return ""


def present_hit(hit):
    """Plain values for templates and JSON; text is escaped where it is output."""
    document = hit.document
    if hit.secondary:
        url = localized_url(document.url, locale=document.locale)
    else:
        url = localized_url(document.url)
    if hit.anchor:
        url += "#" + quote(hit.anchor, safe="-_.")
    language = RELEASE_LOCALES.get(document.locale) if hit.secondary else None
    if document.context:
        context = document.context
    elif hit.section:
        context = _("Abschnitt «%(section)s»", section=hit.section)
    else:
        context = ""
    return {
        "kind": document.kind,
        "url": url,
        "title": _segments(hit.title),
        "context": context,
        "meta": _meta(document),
        "snippet": _segments(hit.snippet),
        "lang": document.locale.replace("_", "-") if language else None,
        "language": language,
    }


def present_group(group, query):
    parameter, label = GROUPS[group.kind]
    return {
        "kind": group.kind,
        "parameter": parameter,
        "label": _(label),
        "total": group.total,
        "hits": [present_hit(hit) for hit in group.hits],
        "more_url": search_url(query, typ=parameter),
    }


def _page_number():
    value = request.args.get("seite", "1")
    # Check the length first: int() refuses very long digit strings.
    if not (value.isascii() and value.isdigit() and len(value) <= 3):
        return 1
    return min(max(int(value), 1), MAX_GROUP_PAGES)


@site_search_bp.get("/suche")
@rate_limit(RATE_LIMIT_ANONYMOUS_READ)
def results():
    query = clean_query(request.args.get("q", ""))
    kind = KIND_BY_PARAMETER.get(request.args.get("typ", ""))
    page = _page_number() if kind else 1
    result = None
    groups = []
    if query:
        index = site_search().index()
        if kind:
            result = index.search(
                query,
                locale=str(get_locale()),
                kinds={kind},
                limit=GROUP_PAGE_SIZE,
                offset=(page - 1) * GROUP_PAGE_SIZE,
            )
        else:
            result = index.search(query, locale=str(get_locale()), limit=PREVIEW_LIMIT)
        groups = [present_group(group, query) for group in result.groups]
    group_total = groups[0]["total"] if kind and groups else 0
    response = make_response(
        render_template(
            "release/search.html",
            query=query,
            result=result,
            groups=groups,
            kind=kind,
            kind_label=_(GROUPS[kind][1]) if kind else "",
            page=page,
            has_previous=page > 1,
            has_next=page < MAX_GROUP_PAGES and page * GROUP_PAGE_SIZE < group_total,
            page_url=lambda number: search_url(
                query, typ=GROUPS[kind][0], seite=number
            ),
            all_url=search_url(query) if query else "",
            suggestion_url=search_url(result.suggestion)
            if result and result.suggestion
            else "",
            examples=[
                (_(example), search_url(_(example))) for example in EXAMPLE_QUERIES
            ],
            site_url=current_app.config["SITE_URL"],
        )
    )
    response.headers["X-Robots-Tag"] = "noindex"
    return response
