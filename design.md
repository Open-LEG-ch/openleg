---
name: OpenLEG
description: >
  Visual identity for OpenLEG, free open-source infrastructure for Swiss local
  electricity communities (Lokale Elektrizitätsgemeinschaften). Civic, technical,
  trustworthy. Energy coded in amber, action coded in indigo, grounded in slate.
colors:
  ink: "#0f172a"          # primary text, dark surfaces, footer
  ink-soft: "#1e293b"
  ink-muted: "#475569"    # secondary text
  paper: "#f6f4ef"        # warm light surface
  white: "#ffffff"
  line: "#e2e8f0"         # hairline borders
  brand: "#f59e0b"        # action + identity: links, primary buttons, logo highlight (amber/yellow)
  brand-light: "#fbbf24"
  brand-dark: "#d97706"   # button hover, darker amber
  accent: "#f59e0b"       # alias of brand; energy highlight
  accent-hi: "#ffc043"    # amber top stop for gradients
typography:
  wordmark:
    family: "JetBrains Mono"
    weight: 500
    tracking: "-0.04em"
    note: "open in ink/paper at 500, LEG in accent at 700"
  mono:
    family: "JetBrains Mono"
    weight: 500
    use: "logo, code, tariff numbers, data, kbd"
  sans:
    family: "system-ui, -apple-system, Segoe UI, Roboto, sans-serif"
    use: "all body copy, headings, UI"
rounded:
  sm: "6px"
  md: "8px"      # buttons, inputs
  lg: "12px"     # cards
  xl: "16px"     # favicon tile, feature panels
spacing:
  container: "72rem"   # max-w-6xl
  gutter: "1rem"
components:
  logo:
    structure: "prompt '>' (accent) + 'open' (ink) + 'LEG' (accent, 700) + blinking caret"
    partial: "templates/partials/brand_wordmark.html"
  favicon:
    file: "static/favicon.svg"
    art: "amber shell prompt '>_' on ink rounded tile"
  button-primary:
    bg: "{colors.brand}"
    color: "{colors.white}"
    radius: "{rounded.md}"
    hover: "{colors.brand-dark}"
---

## Overview

OpenLEG is free, open-source infrastructure for Swiss local electricity
communities. The identity has to carry three things at once: **open source**
(developer credibility), **energy** (what the product moves), and **civic trust**
(citizens and municipalities act on it).

The logo encodes all three: a shell prompt for open source, amber for energy,
and a plain, honest grotesk word for trust. Keep the system quiet. Let the amber
do the talking.

Aesthetic: technical, Swiss-precise, warm. Not playful, not corporate.

## Colors

One loud colour, one ground:

- **`brand` amber/yellow `#f59e0b`** is **both action and identity**. It is the
  primary action (links, buttons, focus) AND the logo highlight (`LEG`, prompt,
  caret). Yellow is the whole brand; there is no second action colour.
- Amber fills carry **ink text**, never white (white on amber fails contrast).
  Primary button: `bg-brand text-ink hover:bg-brand-dark`.
- **`ink` slate `#0f172a`** is the ground: all body text, **all h1/h2/h3 titles
  stay ink (black), never amber**, plus dark surfaces (footer, favicon tile).
  `ink-muted #475569` for secondary text.
- **`paper #f6f4ef`** and `white` are light surfaces.

Gradient: `accent-hi #ffc043` (top) to `accent #f59e0b` (bottom), used only inside
marks (sun, spark), never behind text.

## Typography

- **Sans** (`system-ui` stack): everything readable. Headings and body.
- **Mono** (`JetBrains Mono`): the technical voice. The wordmark, inline code,
  tariff figures, data tables, `kbd`. Mono signals "this is real infrastructure."

The wordmark sets the rule: lowercase `open` at weight 500, uppercase `LEG` at
weight 700 in amber, tracking `-0.04em`.

German user-facing text: Schweizer Hochdeutsch, real umlauts, `ss` not `ß`,
active voice, no em or en dashes.

## Layout

- Centered column, `max-w-6xl` (72rem), `px-4` gutters.
- Sticky translucent nav (`bg-white/80 backdrop-blur`), hairline `border-slate-200`.
- Dark footer on `ink`.

## Elevation & Depth

Restraint. Flat surfaces, hairline borders (`line #e2e8f0`) over shadows.
Allowed: nav backdrop-blur, a soft `shadow-sm` on raised cards. No heavy drop
shadows, no glow.

## Shapes

Rounded, not pill (except tags/badges). Buttons/inputs `8px`, cards `12px`, the
favicon tile and feature panels `16px`. The favicon mark itself is built from
round-capped strokes.

## Components

### Logo / wordmark

```
> openLEG▮
```

- `>` shell prompt, amber, weight 700, ~0.45em right margin.
- `open` lowercase, ink (paper/`#f6f4ef` on dark via `.ol-logo--inverse`).
- `LEG` uppercase, amber, weight 700, no gap after `open`.
- Caret: amber block, slow blink (1.1s), `prefers-reduced-motion` disables it.
- Source of truth: `templates/partials/brand_wordmark.html`.
  Inverse variant for dark surfaces: `{% with brand_inverse=true %}`.
- Standalone asset (docs, OG, README): `static/images/openleg-logo.svg`.

Never re-typeset the wordmark by hand: always include the partial or the SVG.

### Favicon

`static/favicon.svg`: amber `>_` prompt on an ink `#0f172a` 16px-radius tile.
Path-based, no font dependency, legible at 16px. Linked from
`templates/partials/tailwind_brand.html` with `favicon.ico` as raster fallback.

### Buttons

- Primary (yellow action): `bg-brand text-ink rounded-lg font-semibold hover:bg-brand-dark`.
- Secondary: `border border-slate-200 text-ink bg-white`.

## Do's and Don'ts

- **Do** make every primary action yellow (`bg-brand`); yellow is the only action colour.
- **Do** use **ink text on amber fills** (white on amber fails contrast).
- **Do** keep all h1/h2/h3 titles **ink/black**, never amber.
- **Do** use the wordmark partial; keep `LEG` uppercase and amber.
- **Do** respect `prefers-reduced-motion` (caret stops).
- **Don't** use indigo or any second action colour anywhere.
- **Don't** put white text on an amber button, or amber on a heading.
- **Don't** add a separate icon badge next to the wordmark; the prompt is the mark.
- **Don't** introduce new fonts; sans for reading, mono for the technical voice.
- **Don't** use em or en dashes in German copy.
