# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bind rendered navigation to a village without changing script or API data."""

import re
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlsplit, urlunsplit


def scope_url(value, prefix, origin):
    """Keep external and generation-bound URLs; bind local root paths once."""
    parsed = urlsplit(value)
    public = urlsplit(origin)
    if (
        (parsed.netloc and parsed.netloc != public.netloc)
        or (parsed.scheme and parsed.scheme != public.scheme)
        or not parsed.path.startswith("/")
        or parsed.path.startswith("//")
        or re.match(r"/v/[a-f0-9]{32}(?:/|$)", parsed.path)
    ):
        return value
    return urlunsplit(parsed._replace(path=prefix + parsed.path))


class _Page(HTMLParser):
    def __init__(self, prefix, origin):
        super().__init__(convert_charrefs=False)
        self.prefix, self.origin = prefix, origin
        self.output = []
        self.injected = False

    def _start(self, tag, attrs, ending):
        attributes = []
        for name, value in attrs:
            if value is None:
                attributes.append(name)
                continue
            if name in {"href", "src", "action", "formaction", "poster"}:
                value = scope_url(value, self.prefix, self.origin)
            elif name == "srcset":
                value = ", ".join(
                    " ".join(
                        [scope_url(parts[0], self.prefix, self.origin), *parts[1:]]
                    )
                    for item in value.split(",")
                    if (parts := item.split())
                )
            attributes.append(f'{name}="{escape(value, quote=True)}"')
        self.output.append("<" + " ".join([tag, *attributes]) + ending)
        if tag == "head" and not self.injected:
            # Execute before product scripts. Their strings and OpenAPI path
            # fragments stay intact; fetch binds only the final request URL.
            self.output.append(
                f'<script src="{self.prefix}/static/js/sandbox_urls.js" '
                f'data-prefix="{self.prefix}"></script>'
            )
            self.injected = True

    def handle_starttag(self, tag, attrs):
        self._start(tag, attrs, ">")

    def handle_startendtag(self, tag, attrs):
        self._start(tag, attrs, "/>")

    def handle_endtag(self, tag):
        self.output.append(f"</{tag}>")

    def handle_data(self, data):
        self.output.append(data)

    def handle_entityref(self, name):
        self.output.append(f"&{name};")

    def handle_charref(self, name):
        self.output.append(f"&#{name};")

    def handle_comment(self, data):
        self.output.append(f"<!--{data}-->")

    def handle_decl(self, decl):
        self.output.append(f"<!{decl}>")


def scope_page(html, prefix, origin):
    """Scope real HTML URLs and install the browser request adapter."""
    page = _Page(prefix, origin)
    page.feed(html)
    page.close()
    return "".join(page.output)
