# Public Site Release Contract

This contract protects the public OpenLEG website from being replaced by a
product access screen during application or deployment changes.

## Ownership

The public app repository serves both surfaces on `openleg.ch`:

- Public website: anonymous `GET /` and the public content routes.
- Product access: `GET /login`, authenticated dashboards, and their access links.

`PUBLIC_SITE_URL` is a link-generation seam. It does not transfer ownership of
`openleg.ch/` to another runtime. A proposal to split the website into another
service requires an approved migration plan that proves routing, content parity,
redirects, SEO metadata, recovery, and production ownership before any public
route or asset is removed.

## Invariants

Every change must preserve these observable behaviours:

1. Anonymous `GET /` returns 200 and contains the public homepage markers
   `Gründen Sie eine`, `Stromgemeinschaft` and `Was ist eine LEG?`.
2. Anonymous `GET /` contains the shared public navigation and footer and does
   not contain `Dashboard-Zugang`.
3. `GET /login` returns 200, contains `Dashboard-Zugang`, and offers exactly the
   Eigentümer and Gemeinde dashboard choices.
4. Existing authenticated owner and municipality sessions may redirect `/` to
   their respective dashboards.
5. Public navigation, legal pages, ranking pages, municipality profiles, static
   images, JavaScript, and compiled CSS remain packaged in the release image.
6. Billing and dashboard releases preserve the public entry contract unless the
   PR explicitly names and tests a different approved product decision.

The executable HTTP contract is `tests/test_root_role_access.py`. Municipality
profile links are covered by `tests/test_municipality_organic.py`. Release-image
contents are covered by `tests/test_ci_contract.py`.

## Crawl Rules

`GET /robots.txt` gives every user-agent group the same rules. A crawler that
matches a named group ignores `User-agent: *`, so a named group without the
shared exclusions would open the API, administration, private Verein,
confirmation, unsubscribe and credential paths to that crawler.
`tests/test_robots_policy.py` parses the served file with Protego, which follows
Google's group selection and longest-match precedence, and checks each crawler
against public and excluded URLs.

robots.txt is a request to crawlers, not access control. Private routes keep
their own checks, and user-initiated fetchers may ignore robots.txt. Draft
pages stay crawlable so that crawlers can read their `noindex`. A local request
with a crawler user agent does not show what the production CDN allows.

Vendor documentation checked on 2026-11-23:

| Crawler | Kind | Group | Source |
| --- | --- | --- | --- |
| Googlebot | Search | `*` | [robots.txt rules](https://developers.google.com/search/docs/crawling-indexing/robots/robots_txt), [crawlers](https://developers.google.com/search/docs/crawling-indexing/google-common-crawlers) |
| Bingbot | Search | `*` | [Bing, 2012-05-03](https://blogs.bing.com/webmaster/2012/05/03/to-crawl-or-not-to-crawl-that-is-bingbots-question/) |
| OAI-SearchBot | AI search | named | [OpenAI](https://developers.openai.com/api/docs/bots) |
| Claude-SearchBot | AI search | `*` | [Anthropic](https://support.claude.com/en/articles/8896518-does-anthropic-crawl-data-from-the-web-and-how-can-site-owners-block-the-crawler) |
| PerplexityBot | AI search | named | [Perplexity](https://docs.perplexity.ai/guides/bots) |
| ChatGPT-User, Claude-User, Perplexity-User | User-initiated | named, `*`, `*` | Vendor pages above |
| GPTBot, ClaudeBot | Training | named | Vendor pages above |
| Google-Extended, Applebot-Extended | Training control token | named | [Google](https://developers.google.com/search/docs/crawling-indexing/google-common-crawlers), [Apple](https://support.apple.com/en-us/119829) |

Training crawlers keep their existing allow groups. Change that preference
only through an explicit maintainer decision.

## Required Checks

Before merging a change to routing, templates, assets, `PUBLIC_SITE_URL`, Caddy,
Docker packaging, or release workflows:

```bash
pytest tests/test_root_role_access.py tests/test_municipality_organic.py tests/test_ci_contract.py -q
scripts/tdd_cycle.sh gate
```

Review the rendered homepage at desktop and mobile widths. Confirm that `/login`
shows the role chooser and that `/` does not.

The release image must contain:

- `/app/templates/index.html`
- `/app/templates/role_access.html`
- `/app/templates/dashboard.html`
- `/app/static/js/landing_segments.js`
- `/app/static/images/landing/urban.webp`
- `/app/static/css/openleg.css`

## Production Smoke Check

After digest deployment, make a bounded set of anonymous requests:

1. Check `/livez` and `/health` once.
2. Check `/` once for all three current homepage markers and the absence of
   `Dashboard-Zugang`.
3. Check `/login` once for `Dashboard-Zugang`.
4. Check the fixed primary public routes once each.
5. Check only the municipality profile links visible on the homepage, with at
   most six requests.
6. Compare the deployed image, source SHA, CSS, code, and homepage hashes with
   the release ledger.

Treat any unexpected 4xx/5xx, missing marker, missing asset, or hash mismatch as
a failed release. Follow the owning recovery procedure in private `openleg-ops`.
For the product, once candidate initialization has begun, keep the same candidate
verified read-only or stopped and recover through a validated roll-forward
correction. Do not automatically start an older image or restore the database
after that point. See [read-only release validation](release-validation.md) for
the public process-mode contract.

Do not crawl the full municipality directory during a smoke test. It contains
thousands of profile links and will correctly trigger rate limiting.

## Incident Record

The `v0.1.8` deployment exposed a dashboard-only root introduced by commit
`def10b6`, replacing the public website. Releases `v0.1.9` and `v0.1.10`
restored the website, assets, public routes, and municipality profiles. The
failure escaped because billing tests and image verification covered dashboard
assets but did not assert the anonymous root contract.
