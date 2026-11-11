# OpenLEG neighbourhood components

Six independent illustrations generated with the built-in OpenAI image-generation
tool on 7 November 2026. The original `../neighbourhood.webp` provided the style
reference. Each building and tree is rendered as a complete transparent object;
the ground is a separate empty platform. These are new renders, not crops of the
original scene.

| File | Component |
| --- | --- |
| `house.webp` | Ivory house with a coral pitched roof and solar panels |
| `apartments.webp` | Four-storey apartment building with balconies and solar roof |
| `workshop.webp` | Timber workshop with a broad solar roof |
| `bungalow.webp` | Single-storey home with a flat solar roof |
| `tree.webp` | Muted sage deciduous tree |
| `ground.webp` | Empty paved neighbourhood platform |

The generated PNGs were exported at their original dimensions with cwebp 1.3.2,
quality 86, alpha quality 100 and method 6. Alpha transparency is preserved.
`manifest.json` records dimensions, sizes and SHA-256 hashes. `PROMPTS.md` contains
the exact prompts and source output filenames.

Composition lives in `templates/release/illustrations.html`. The `piece` macro
places an individual object. The `walkway` macro draws quiet static paving. The
homepage and smaller page illustrations assemble the same components in different
arrangements. No circular crop or animated light overlay is used.

Keep perspective and proportions consistent. Position each base on the ground
plane; draw background objects before foreground objects. Complete scenes need
a visible fictional-scene caption and an accessible description. Decorative page
illustrations can be hidden from assistive technology when adjacent text carries
all their meaning. See the root `design.md` for composition and language rules.

The illustrations are fictional and establish no network eligibility, real
installation or measured electricity flow. Distributed under the repository's
AGPL-3.0-or-later licence.

## Smaller derivatives (`w480/`)

`w480/` holds downscaled copies of the same six assets for pages that show them
small: buildings and tree at 480 × 480 pixels, `ground.webp` at 900 × 486. They were
made on 9 November 2026 in Chromium (canvas, high-quality smoothing, halving steps)
and encoded as WebP at quality 84 with alpha. No content was changed. Homepage
variant F loads them; its hero scene drops from about 1.5 MB to 291 KB.
