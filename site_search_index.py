# SPDX-License-Identifier: AGPL-3.0-or-later
"""The public content behind the site search, and when it is refreshed.

Pages are rendered through their own view functions, so the index holds the
text visitors read, in every site language. Only the routes listed in `PAGES`
are rendered, anonymously and without a session. Municipalities come from the
public municipality profiles, LEGs from the published directory, postcodes
from `swiss_postcodes`. Nothing else is read: no registrations, members,
invoices, administration pages or sandbox data.
"""

import hashlib
import logging
import threading
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import quote, urlsplit

from flask import g, request
from werkzeug.exceptions import HTTPException

import database as db
import i18n
import tenant as tenant_module
from security_extensions import limiter
from site_search import Block, Document, SearchIndex
from swiss_postcodes import postcodes_by_municipality

logger = logging.getLogger(__name__)

PAGE = "page"
KNOWLEDGE = "knowledge"
MUNICIPALITY = "municipality"
LEG = "leg"

# Public routes only. Each is rendered as an anonymous visitor would see it.
# Listing pages contribute their title, description and headings; their
# entries are indexed as municipalities and LEGs instead.
PAGES = (
    ("/", PAGE),
    ("/software", PAGE),
    ("/pricing", PAGE),
    ("/open-source", PAGE),
    ("/self-host", PAGE),
    ("/fuer-bewohner", PAGE),
    ("/fuer-gemeinden", PAGE),
    ("/leg-check", PAGE),
    ("/leg-kalkulator", PAGE),
    ("/gemeinde/onboarding", PAGE),
    ("/verein", PAGE),
    ("/verein/beitreten", PAGE),
    ("/verein/spenden", PAGE),
    ("/verein/finanzen", PAGE),
    ("/veranstaltungen", PAGE),
    ("/community", PAGE),
    ("/mitmachen", PAGE),
    ("/kontakt", PAGE),
    ("/api/v1/docs", PAGE),
    ("/login", PAGE),
    ("/impressum", PAGE),
    ("/datenschutz", PAGE),
    ("/wissen", KNOWLEDGE),
    ("/wissen/strommodelle", KNOWLEDGE),
    ("/wissen/glossar", KNOWLEDGE),
    ("/dokumente", KNOWLEDGE),
    ("/how-it-works", KNOWLEDGE),
    ("/leg-gruenden", KNOWLEDGE),
    ("/rangliste/methodik", KNOWLEDGE),
)
LISTING_PAGES = (
    ("/leg-verzeichnis", PAGE),
    ("/rangliste", PAGE),
    ("/rangliste/fortschritte", PAGE),
    ("/rangliste/vergleich", PAGE),
    ("/gemeinde/verzeichnis", PAGE),
)
# A listing page's introduction fits well within this many characters.
LISTING_HTML_LIMIT = 48_000

PAGE_TTL_SECONDS = 600
DATA_TTL_SECONDS = 600
# A withdrawn LEG must leave the search soon; its directory read is small.
LEG_TTL_SECONDS = 60
RETRY_SECONDS = 60
# Marks the internal page renders, which no rate limit counts. Clients cannot
# set WSGI environment keys, only HTTP_* headers.
RENDER_ENVIRON_KEY = "openleg.site_search.render"
MAX_DIRECTORY_PAGES = 40

_SKIP = frozenset(
    {
        "script",
        "style",
        "svg",
        "noscript",
        "template",
        "nav",
        "form",
        "select",
        "table",
        "button",
        "iframe",
        "video",
        "audio",
        "canvas",
    }
)
_INLINE = frozenset(
    {
        "a",
        "abbr",
        "b",
        "bdi",
        "bdo",
        "cite",
        "code",
        "data",
        "dfn",
        "em",
        "i",
        "kbd",
        "mark",
        "q",
        "s",
        "small",
        "span",
        "strong",
        "sub",
        "sup",
        "time",
        "u",
    }
)
_VOID = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)


def _clean(parts):
    return " ".join("".join(parts).split())


@dataclass
class PageContent:
    title: str = ""
    head_title: str = ""
    description: str = ""
    blocks: list = field(default_factory=list)
    questions: list = field(default_factory=list)
    definitions: list = field(default_factory=list)
    links: list = field(default_factory=list)


class _PageParser(HTMLParser):
    """Collect the readable text inside `<main>`, split at section headings.

    Questions in `<details>` and terms in `<dl>` become their own entries.
    Navigation, forms, tables, scripts and hidden content are left out.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.content = PageContent()
        self._stack = []
        self._skip_depth = 0
        self._in_main = 0
        self._in_head_title = False
        self._heading = None
        self._block = {"heading": "", "anchor": "", "text": []}
        self._details = []
        self._term = None
        self._definition = None
        self._head_title = []
        self._link = None

    # -- helpers ---------------------------------------------------------

    def _anchor(self):
        """The closest element id inside `<main>`; links jump to it."""
        for tag, element_id, _skip in reversed(self._stack):
            if tag == "main":
                break
            if element_id:
                return element_id
        return ""

    def _sink(self):
        if self._heading is not None:
            return self._heading["parts"]
        if self._definition is not None:
            return self._definition
        if self._term is not None:
            return self._term
        if self._details:
            details = self._details[-1]
            return details["summary"] if details["in_summary"] else details["answer"]
        return self._block["text"]

    def _flush(self):
        text = _clean(self._block["text"])
        if text or self._block["heading"]:
            self.content.blocks.append(
                Block(
                    text=text,
                    heading=self._block["heading"],
                    anchor=self._block["anchor"],
                )
            )
        self._block = {"heading": "", "anchor": "", "text": []}

    # -- parser events -----------------------------------------------------

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "meta" and attributes.get("name") == "description":
            self.content.description = " ".join(
                (attributes.get("content") or "").split()
            )
        if tag == "title" and not self._in_main:
            self._in_head_title = True
        if tag == "a" and attributes.get("href"):
            self._link = (attributes["href"], [])
        if tag in _VOID:
            return
        skip = (
            tag in _SKIP
            or attributes.get("aria-hidden") == "true"
            or "hidden" in attributes
            or "data-search-skip" in attributes
        )
        self._stack.append((tag, attributes.get("id") or "", skip))
        if tag == "main":
            self._in_main += 1
        if skip:
            self._skip_depth += 1
        if not self._in_main or self._skip_depth:
            return
        if tag == "h1":
            self._heading = {"level": 1, "parts": []}
        elif tag in {"h2", "h3"} and self._heading is None:
            self._flush()
            self._heading = {"level": 2, "parts": [], "anchor": self._anchor()}
        elif tag == "details":
            self._details.append(
                {
                    "summary": [],
                    "answer": [],
                    "in_summary": False,
                    "anchor": self._block["anchor"] or self._anchor(),
                }
            )
        elif tag == "summary" and self._details:
            self._details[-1]["in_summary"] = True
        elif tag == "dt" and not self._details:
            self._term = []
        elif tag == "dd" and self._term is not None:
            self._definition = []
        if tag not in _INLINE:
            self._sink().append(" ")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_head_title = False
        if tag == "a" and self._link is not None:
            href, parts = self._link
            self._link = None
            if text := _clean(parts):
                self.content.links.append((href, text))
        if not any(entry[0] == tag for entry in self._stack):
            return
        while self._stack:
            open_tag, _element_id, skip = self._stack.pop()
            if skip:
                self._skip_depth -= 1
            elif self._in_main and not self._skip_depth:
                self._close(open_tag)
            if open_tag == "main":
                self._in_main -= 1
                if not self._in_main:
                    self._flush()
            if open_tag == tag:
                break

    def _close(self, tag):
        if tag not in _INLINE:
            self._sink().append(" ")
        if tag == "h1" and self._heading is not None:
            self.content.title = self.content.title or _clean(self._heading["parts"])
            self._heading = None
        elif tag in {"h2", "h3"} and self._heading is not None:
            heading = self._heading
            self._heading = None
            self._block = {
                "heading": _clean(heading["parts"]),
                "anchor": heading["anchor"],
                "text": [],
            }
        elif tag == "summary" and self._details:
            self._details[-1]["in_summary"] = False
        elif tag == "details" and self._details:
            details = self._details.pop()
            question = _clean(details["summary"])
            answer = _clean(details["answer"])
            if question.endswith("?") and answer:
                self.content.questions.append((question, answer, details["anchor"]))
            else:
                self._sink().extend((" ", question, " ", answer, " "))
        elif tag == "dd" and self._definition is not None:
            term, definition = _clean(self._term or []), _clean(self._definition)
            if term and definition:
                self.content.definitions.append(
                    (term, definition, self._block["anchor"] or self._anchor())
                )
            self._term = None
            self._definition = None

    def handle_data(self, data):
        if self._link is not None and not (
            self._stack and self._stack[-1][0] in {"script", "style"}
        ):
            self._link[1].append(data)
        if self._in_head_title:
            self._head_title.append(data)
            return
        if self._in_main and not self._skip_depth:
            self._sink().append(data)

    def close(self):
        super().close()
        if self._in_main:
            self._flush()
        self.content.head_title = _clean(self._head_title)
        return self.content


def extract_page(html, *, listing=False):
    """Return the title, description, sections, questions and terms of a page.

    For a listing page only the beginning is read and only headings are kept.
    """
    parser = _PageParser()
    parser.feed(html[:LISTING_HTML_LIMIT] if listing else html)
    content = parser.close()
    if listing:
        content.blocks = [
            Block(text="", heading=block.heading, anchor=block.anchor)
            for block in content.blocks
            if block.heading
        ]
        content.questions = []
        content.definitions = []
    return content


def _digest(*parts):
    return hashlib.sha1("\x1f".join(parts).encode()).hexdigest()


def documents_from_page(path, kind, locale, content, labels=()):
    """Turn one rendered page into its page, question and term documents.

    `labels` are the texts of links to this page, such as its menu entry. They
    and the words of the path help visitors who search for what they clicked.
    """
    title = (content.title or content.head_title.split(" | ")[0] or path).rstrip(".")
    page = Document(
        kind=kind,
        url=path,
        title=title,
        locale=locale,
        summary=content.description,
        keywords=(
            content.head_title.split(" | ")[0],
            path.replace("/", " ").replace("-", " ").strip(),
            *sorted(set(labels)),
        ),
        blocks=tuple(content.blocks),
        group_key=path,
        # Only pages with the same text merge; listing pages keep their own entry.
        duplicate_key=_digest(*(block.heading + block.text for block in content.blocks))
        if any(block.text for block in content.blocks)
        else "",
    )
    documents = [page]
    for number, (question, answer, anchor) in enumerate(content.questions):
        documents.append(
            Document(
                kind=KNOWLEDGE,
                url=path,
                title=question,
                locale=locale,
                summary=answer,
                context=title,
                anchor=anchor,
                group_key=f"{path}#question-{number}",
                duplicate_key=_digest(question, answer),
            )
        )
    for number, (term, definition, anchor) in enumerate(content.definitions):
        documents.append(
            Document(
                kind=KNOWLEDGE,
                url=path,
                title=term,
                locale=locale,
                summary=definition,
                context=title,
                anchor=anchor,
                group_key=f"{path}#term-{number}",
                duplicate_key=_digest(term, definition),
            )
        )
    return documents


def render_public_page(app, path, locale, tenant):
    """Render one listed page anonymously; return its HTML or None.

    The handler runs without request hooks and without the visitor's session.
    Route rate limits skip it (see `_skip_rate_limits`). A fresh application context keeps `g` separate from
    the request that triggered the refresh.
    """
    with (
        app.app_context(),
        app.test_request_context(
            path,
            base_url=app.config.get("SITE_URL") or "http://localhost",
            query_string={"lang": locale.replace("_", "-")},
            environ_overrides={RENDER_ENVIRON_KEY: True},
        ),
    ):
        g.tenant = tenant
        try:
            response = app.make_response(app.dispatch_request())
        except HTTPException:
            return None
        if response.status_code != 200 or response.mimetype != "text/html":
            return None
        return response.get_data(as_text=True)


@limiter.request_filter
def _skip_rate_limits():
    """Route decorators still run inside dispatch; spare their quotas."""
    return request.environ.get(RENDER_ENVIRON_KEY) is True


def page_documents(app):
    """Render every listed page in every site language.

    Returns `(documents, complete)`; a page that cannot be rendered now is
    missing until the next refresh.
    """
    documents = []
    complete = True
    with app.app_context():
        locales = tuple(i18n.supported_locales())
        tenant = tenant_module.get_tenant_config("zurich", db=db)
    pages = [(path, kind, False) for path, kind in PAGES]
    pages += [(path, kind, True) for path, kind in LISTING_PAGES]
    paths = {path for path, _kind, _listing in pages}
    for locale in locales:
        rendered = []
        labels = {}
        for path, kind, listing in pages:
            try:
                html = render_public_page(app, path, locale, tenant)
            except Exception:
                logger.exception("[SEARCH] Rendering %s (%s) failed", path, locale)
                html = None
            if html is None:
                complete = False
                continue
            content = extract_page(html, listing=listing)
            rendered.append((path, kind, content))
            for href, text in content.links:
                target = urlsplit(href)
                if not target.netloc and target.path in paths:
                    labels.setdefault(target.path, set()).add(text)
        for path, kind, content in rendered:
            documents.extend(
                documents_from_page(path, kind, locale, content, labels.get(path, ()))
            )
    return documents, complete


def _postcode_summary(name, entries):
    """`5400, 5404 Baden · 5405 Dättwil AG`: postcodes grouped by locality."""
    groups = {}
    for code, _share, names in entries:
        groups.setdefault(", ".join(names) or name, []).append(code)
    return " · ".join(f"{', '.join(codes)} {place}" for place, codes in groups.items())


def municipality_documents():
    """Public municipality profiles with their postcodes, or None if unavailable."""
    profiles = db.get_all_municipality_profiles()
    if not profiles:
        return None
    postcodes = postcodes_by_municipality()
    documents = []
    for profile in profiles:
        bfs = profile.get("bfs_number")
        name = (profile.get("name") or "").strip()
        if not isinstance(bfs, int) or not name:
            continue
        kanton = (profile.get("kanton") or "").strip()
        entries = postcodes.get(bfs, ())
        localities = sorted(
            {name for _code, _share, names in entries for name in names}
        )
        documents.append(
            Document(
                kind=MUNICIPALITY,
                url=f"/gemeinde/profil/{bfs}",
                title=name,
                keywords=(
                    kanton,
                    *(code for code, _share, _names in entries),
                    *localities,
                ),
                summary=_postcode_summary(name, entries),
                summary_fallback=False,
                group_key=f"bfs-{bfs}",
                boosts={code: share / 50 for code, share, _names in entries},
                data={"kanton": kanton, "bfs_number": bfs},
            )
        )
    return documents


def leg_documents():
    """Published directory entries, or None if the directory is unavailable."""
    documents = []
    for page in range(1, MAX_DIRECTORY_PAGES + 1):
        try:
            result = db.read_public_registry(page=page)
        except db.PublicRegistryUnavailable:
            return None
        for entry in result["entries"]:
            slug = entry.get("slug") or ""
            name = (entry.get("name") or "").strip()
            if not slug or not name:
                continue
            place = " ".join(
                part
                for part in (entry.get("plz") or "", entry.get("ort") or "")
                if part
            )
            documents.append(
                Document(
                    kind=LEG,
                    url="/leg-verzeichnis/" + quote(slug, safe=""),
                    title=name,
                    keywords=tuple(
                        value
                        for value in (place, entry.get("kanton"), entry.get("vnb_name"))
                        if value
                    ),
                    summary=" ".join((entry.get("description") or "").split()),
                    group_key=f"leg-{slug}",
                    data={
                        "place": place,
                        "kanton": entry.get("kanton") or "",
                        "leg_status": entry.get("leg_status") or "",
                    },
                )
            )
        if not result.get("has_next"):
            break
    return documents


@dataclass
class _Part:
    documents: tuple = ()
    expires: float = 0.0


class SiteSearch:
    """Holds the current index; refreshes stale parts without blocking reads.

    The first search builds the index. Later, a stale part is rebuilt in a
    background thread while the previous index keeps answering. Testing apps
    refresh synchronously.
    """

    def __init__(self, app, *, clock=time.monotonic):
        self._app = app
        self._clock = clock
        self._lock = threading.Lock()
        self._refreshing = False
        self._index = None
        self._parts = {name: _Part() for name in ("pages", MUNICIPALITY, LEG)}

    def index(self):
        if self._index is None:
            with self._lock:
                if self._index is None:
                    self._refresh()
            return self._index
        if self._stale() and not self._refreshing:
            if self._app.testing:
                with self._lock:
                    self._refresh()
            else:
                self._refreshing = True
                threading.Thread(
                    target=self._refresh_in_background, daemon=True
                ).start()
        return self._index

    def warm(self):
        """Start building in the background unless an index exists or is coming."""
        if self._index is None and not self._refreshing:
            self._refreshing = True
            threading.Thread(target=self._refresh_in_background, daemon=True).start()

    def _stale(self):
        now = self._clock()
        return any(part.expires <= now for part in self._parts.values())

    def _refresh_in_background(self):
        try:
            with self._lock:
                self._refresh()
        finally:
            self._refreshing = False

    def _refresh(self):
        now = self._clock()
        changed = self._index is None
        for name, part in self._parts.items():
            if part.expires > now:
                continue
            documents, ttl = self._load(name)
            part.expires = now + ttl
            if documents is None and name == LEG:
                # Publication cannot be confirmed: hide LEGs rather than show
                # entries that may have been withdrawn meanwhile.
                documents = ()
            if documents is not None:
                part.documents = tuple(documents)
                changed = True
        if changed:
            self._index = SearchIndex(
                document for part in self._parts.values() for document in part.documents
            )

    def _load(self, name):
        try:
            if name == "pages":
                documents, complete = page_documents(self._app)
                return documents, PAGE_TTL_SECONDS if complete else RETRY_SECONDS
            with self._app.app_context():
                loader = (
                    municipality_documents if name == MUNICIPALITY else leg_documents
                )
                documents = loader()
        except Exception:
            logger.exception("[SEARCH] Loading %s failed", name)
            return None, RETRY_SECONDS
        if documents is None:
            return None, RETRY_SECONDS
        return documents, LEG_TTL_SECONDS if name == LEG else DATA_TTL_SECONDS
