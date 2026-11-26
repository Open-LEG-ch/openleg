# Release graphics

`neighbourhood.webp` is an original, fictional architectural scene generated for
OpenLEG on 19 October 2026 with OpenAI image generation. No reference image was
supplied. The transparent source was exported to WebP at quality 86, alpha 100,
1706 × 922 pixels. It illustrates community roles; it depicts no real installation
and establishes no network eligibility. The HTML caption and role descriptions
carry the information when the image is unavailable.

`favicon.svg` uses the openLEG letter identity and static roof-coloured baseline.
The letters are drawn as shapes, so the icon does not depend on installed fonts.
`favicon.ico` (16, 32 and 48 px), `apple-touch-icon.png` (180 px, square corners for
iOS masking), `icon-192.png` and `icon-512.png` are rendered from that SVG in Chromium
at each native size. `site.webmanifest` lists the PNG icons. When the release site is
enabled, `/favicon.ico` serves this ICO instead of the legacy daylight icon.
Both assets are distributed under the repository's AGPL-3.0-or-later license.

The language-specific `og-image.svg` sources are built from
`templates/assets/release/og-image.svg` and the gettext catalogs. Their PNG exports
are rendered at 1200 × 630 pixels in Chromium. They reuse this scene and static
wordmark, with no additional image generation or external reference assets.

The public illustration macros in `templates/release/illustrations.html` now use
independently generated components in `components/`. The original neighbourhood
is their style reference. Cropped views and the experimental light animation
were removed after design feedback. The complete homepage scene is assembled
from the new components. See `components/README.md` and `design.md`.
