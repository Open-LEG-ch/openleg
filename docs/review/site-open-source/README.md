# Site review evidence

Screenshots use the real Flask templates and compiled catalogs with synthetic
homepage statistics, the site switch enabled, and the confirmed formation date.
No production configuration was applied. Images are review artifacts.

`browser-audit.json` records 48 cases: three pages, four locales, four widths
(320, 390, 768, 1280). Chromium + axe-core found no page overflow, clipped map
labels or accessibility violations. Automated checks do not replace native
language, legal or human accessibility review.

The paired SVG map labels were visually inspected at desktop and 320 px.
The private Archify workflow draft passed all nine showcase checks with zero
composition errors or warnings. The shipped map is static inline SVG, with no
Archify viewer runtime or added JavaScript.

Source evidence: `CONTEXT.md`, `docs/architecture.md`, `docs/mcp-sidecar.md`,
`docs/community-archive.md`, the corresponding Python modules, and the public
software/pricing copy. Module existence and text equivalence are covered by
`tests/test_site_stewardship.py`.
