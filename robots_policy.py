# SPDX-License-Identifier: AGPL-3.0-or-later
"""Crawl rules served at /robots.txt.

A crawler that matches a named user-agent group ignores the ``*`` group, so
every group repeats the same rules. robots.txt only asks crawlers to stay away;
private routes keep their own access checks.
"""

_RULES = (
    "Allow: /",
    "Allow: /api/v1/docs",
    "Allow: /api/v1/openapi.json",
    "Disallow: /api/",
    "Disallow: /gemeinde/api/",
    "Disallow: /admin/",
    "Disallow: /utility/admin/",
    "Disallow: /leg/community/",
    "Disallow: /leg/document/",
    "Disallow: /verein/vorstand/",
    "Disallow: /verein/mitglied",
    "Disallow: /confirm/",
    "Disallow: /interest/confirm/",
    "Disallow: /verein/bestaetigen/",
    "Disallow: /registry/verify/",
    "Disallow: /unsubscribe/",
    "Disallow: /dashboard/access/",
    "Disallow: /gemeinde/access/",
    "Disallow: /utility/access/",
    "Disallow: /oauth/",
)

_NAMED_GROUPS = (
    ("AI search crawlers.", ("OAI-SearchBot", "PerplexityBot")),
    (
        "User-initiated fetchers. Their vendors say robots.txt may not apply.",
        ("ChatGPT-User",),
    ),
    (
        "AI training crawlers and control tokens are welcome on public pages.",
        ("GPTBot", "ClaudeBot", "Google-Extended", "Applebot-Extended"),
    ),
)


def robots_txt(site_url: str) -> str:
    """Return the robots.txt body for the public site at ``site_url``."""
    lines = [
        "# Default group, also used by Googlebot, Bingbot and Claude-SearchBot.",
        "User-agent: *",
        *_RULES,
        "",
    ]
    for label, agents in _NAMED_GROUPS:
        lines.append(f"# {label}")
        for agent in agents:
            lines.extend((f"User-agent: {agent}", *_RULES, ""))
    lines += [
        f"Sitemap: {site_url}/sitemap.xml",
        f"# LLM summary: {site_url}/llms.txt",
    ]
    return "\n".join(lines) + "\n"
