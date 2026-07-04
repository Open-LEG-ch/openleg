---
name: OpenLEG
description: >
  Visual identity for OpenLEG, free open-source infrastructure for Swiss local
  electricity communities (Lokale Elektrizitätsgemeinschaften). Civic,
  technical, trustworthy. Action and identity coded in violet, grounded in
  slate.
colors:
  ink: "#0f172a"          # primary text, dark surfaces, footer, favicon tile
  ink-soft: "#1e293b"
  ink-muted: "#475569"    # secondary text
  paper: "#f6f4ef"        # warm light surface and inverse wordmark text
  white: "#ffffff"
  line: "#e2e8f0"         # hairline borders
  brand: "#4f46e5"        # action + identity: links, primary buttons, logo highlight
  brand-light: "#6366f1"
  brand-dark: "#4338ca"   # button hover
  accent: "#4f46e5"       # alias of brand
typography:
  wordmark:
    family: "JetBrains Mono"
    weight: 500
    tracking: "-0.04em"
    note: "open in ink/paper at 500, LEG in brand at 700, violet caret"
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
    structure: "'open' lowercase ink/paper + 'LEG' uppercase brand, 700 + blinking caret"
    partial: "templates/partials/brand_wordmark.html"
  favicon:
    file: "static/favicon.svg"
    art: "violet blinking caret block on ink rounded tile"
  button-primary:
    bg: "{colors.brand}"
    color: "{colors.white}"
    radius: "{rounded.md}"
    hover: "{colors.brand-dark}"
---

## Overview

OpenLEG is free, open-source infrastructure for Swiss local electricity
communities. The identity has to carry three things at once: **open source**
(developer credibility), **energy coordination** (what the product enables), and
**civic trust** (citizens and municipalities act on it).

The logo encodes the product through a precise technical wordmark: lowercase
`open`, uppercase `LEG`, and a blinking violet caret. Keep the system quiet. Let
violet carry action and identity.

Aesthetic: technical, Swiss-precise, trustworthy. Not playful, not corporate.

## Colors

One action colour, one ground:

- **`brand` violet `#4f46e5`** is **both action and identity**. It is the
  primary action (links, buttons, focus) AND the logo highlight (`LEG`, caret).
  Violet is the whole action system; there is no second action colour.
- `brand-light #6366f1` supports lighter identity accents. `brand-dark #4338ca`
  is the primary hover colour.
- `accent #4f46e5` is an alias of `brand`, not a separate colour.
- Violet fills carry **white text**. Primary button:
  `bg-brand text-white rounded-lg font-semibold hover:bg-brand-dark`.
  This is the shipped contrast rule.
- Tinted chips and icon badges use `bg-brand/10 text-brand`.
- Focus indicators use a 2px solid `#4f46e5` outline.
- **On dark ink surfaces, never use raw brand `#4f46e5` for text or marks**
  (2.8:1, fails WCAG AA). Use indigo-400 `#818cf8` for identity accents and
  large numbers (6.0:1) and indigo-300 `#a5b4fc` for small text (9.0:1). The
  inverse wordmark (`LEG`, caret) uses `#818cf8`. Footer text on ink uses
  `text-slate-400` or lighter.
- Muted small text on the `paper` body uses `text-ink-muted` (6.9:1), not
  gray-400/gray-500 (both fail on paper). Positive money values on paper use
  `text-green-700`.
- **`ink` slate `#0f172a`** is the ground: all body text, **all h1/h2/h3 titles
  stay ink (black), never violet**, plus dark surfaces (footer, favicon tile).
  `ink-soft #1e293b` supports dense dark text. `ink-muted #475569` is secondary
  text.
- **`paper #f6f4ef`** and `white #ffffff` are light surfaces. `line #e2e8f0` is
  the hairline colour.

No gradient is part of the core brand system. Use flat fills and clear contrast.

## Typography

- **Sans** (`system-ui` stack): everything readable. Headings and body.
- **Mono** (`JetBrains Mono`): the technical voice. The wordmark, inline code,
  tariff figures, data tables, `kbd`. Mono signals "this is real infrastructure."

The wordmark sets the rule: lowercase `open` at weight 500, uppercase `LEG` at
weight 700 in violet `#4f46e5`, tracking `-0.04em`. It has no shell prompt
character. The caret is a violet block with a slow 1.1s blink, disabled under
`prefers-reduced-motion`.

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
favicon tile and feature panels `16px`. The favicon mark itself is a rounded
caret block on a rounded tile.

## Components

### Logo / wordmark

```
openLEG▮
```

- `open` lowercase, ink (paper/`#f6f4ef` on dark via `.ol-logo--inverse`).
- `LEG` uppercase, violet `#4f46e5`, weight 700, no gap after `open`.
- Caret: violet `#4f46e5` block, slow blink (1.1s),
  `prefers-reduced-motion` disables it.
- Font: `JetBrains Mono`, weight 500, tracking `-0.04em`.
- Source of truth: `templates/partials/brand_wordmark.html`, with CSS in
  `templates/partials/brand_head.html`.
- Inverse variant for dark surfaces: `{% with brand_inverse=true %}` makes
  `open` paper `#f6f4ef`.
- Standalone asset (docs, OG, README): `static/images/openleg-logo.svg`.

Never re-typeset the wordmark by hand: always include the partial or the SVG.
There is no shell prompt `>` character in the wordmark.

### Favicon

`static/favicon.svg`: violet `#4f46e5` blinking caret block on an ink `#0f172a`
16px-radius tile with `rx 16`. It is path-based, animated by opacity, and has no
font dependency. Linked from `templates/partials/tailwind_brand.html` with
`favicon.ico` as raster fallback.

### Buttons

- Primary action: `bg-brand text-white rounded-lg font-semibold hover:bg-brand-dark`.
- Secondary: `border border-slate-200 text-ink bg-white`.
- Chips and icon badges: `bg-brand/10 text-brand`.

## Do's and Don'ts

- **Do** make every primary action violet (`bg-brand`); violet is the only action colour.
- **Do** use **white text on violet fills** with `bg-brand text-white`.
- **Do** keep all h1/h2/h3 titles **ink/black**, never violet.
- **Do** use the wordmark partial; keep `LEG` uppercase and violet.
- **Do** respect `prefers-reduced-motion` (caret stops).
- **Don't** introduce a second action colour.
- **Don't** put violet on a heading.
- **Don't** add a separate icon badge next to the wordmark; the caret is the mark.
- **Don't** reintroduce a shell prompt `>` into the wordmark or favicon.
- **Don't** introduce new fonts; sans for reading, mono for the technical voice.
- **Don't** use em or en dashes in German copy.
