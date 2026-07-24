---
name: OpenLEG
description: >
  Visual identity for OpenLEG, free open-source infrastructure for Swiss local
  electricity communities (Lokale Elektrizitätsgemeinschaften). Communal,
  grounded, trustworthy. The "Daylight cooperative" direction: pine on paper,
  warmed by solar. Action and identity coded in pine, energy sparked in solar.
colors:
  ink: "#22201b"          # primary text, warm near-black
  ink-soft: "#3a362c"     # dense secondary text
  ink-muted: "#6b6555"    # muted secondary text
  paper: "#f5f2ea"        # warm base surface, body background
  paper-deep: "#ece7da"   # alternate warm surface, section banding
  white: "#ffffff"
  line: "#ded7c6"         # warm hairline borders
  brand: "#1f3d32"        # pine: action + identity, links, primary buttons, logo
  brand-light: "#2c5545"
  brand-dark: "#16302a"   # button hover, dark surfaces
  accent: "#e8a13a"       # solar: the energy spark, caret, highlights, data marks
  accent-light: "#f0b968"
  accent-dark: "#c9832a"
  sage: "#6e8f7c"         # secondary support, positive/community states
  sage-light: "#e4ede6"   # tinted sage surface
  sage-dark: "#4f6d5c"
typography:
  wordmark:
    family: "JetBrains Mono"
    weight: 500
    tracking: "-0.04em"
    note: "open in ink/paper at 500, LEG in pine at 700, solar caret"
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
    structure: "'open' lowercase ink/paper + 'LEG' uppercase pine, 700 + solar caret"
    partial: "templates/partials/brand_wordmark.html"
  favicon:
    file: "static/favicon.svg"
    art: "solar blinking caret block on pine rounded tile"
  button-primary:
    bg: "{colors.brand}"
    color: "{colors.white}"
    radius: "{rounded.md}"
    hover: "{colors.brand-dark}"
---

## Overview

OpenLEG is free, open-source infrastructure for Swiss local electricity
communities. The identity carries three things at once: **open source**
(developer credibility), **energy coordination** (what the product enables), and
**civic trust** (citizens and municipalities act on it).

The direction is **Daylight cooperative**: it should feel like a Swiss local
co-op in daylight, not a crypto dashboard. Warm paper ground, grounded pine for
action, a solar spark for energy. Real neighborhoods over neon glow.

Aesthetic: warm, communal, Swiss-precise, trustworthy. Not a dark SaaS template.

## Colors

One action colour, one ground, one spark:

- **`brand` pine `#1f3d32`** is **both action and identity**. It is the primary
  action (links, buttons, focus) AND the logo `LEG`. Pine is the whole action
  system; there is no second action colour.
- `brand-light #2c5545` supports lighter identity accents. `brand-dark #16302a`
  is the primary hover colour and the warm dark surface (nav, footer).
- Pine fills carry **white text**. Primary button:
  `bg-brand text-white rounded-lg font-semibold hover:bg-brand-dark`.
  This is the shipped contrast rule.
- Tinted chips and icon badges use `bg-brand/10 text-brand`.
- **`accent` solar `#e8a13a`** is the energy spark: the wordmark caret,
  highlights, data marks, and small celebratory moments. It is an accent, not an
  action; primary buttons stay pine. `accent-light #f0b968` and
  `accent-dark #c9832a` support solar on dark and hover states. On paper, small
  solar text uses `accent-dark` for legibility.
- **`sage` `#6e8f7c`** is the secondary/community colour for positive states,
  supporting marks, and quiet fills (`sage-light #e4ede6`, `sage-dark #4f6d5c`).
- Focus indicators use a 2px solid pine `#1f3d32` outline on light surfaces
  (`paper`, `white`). On `brand-dark` surfaces the focus outline switches to
  solar `#e8a13a`.
- **On dark pine surfaces**, use `paper` / `sage-light` for text, solar
  `#f0b968` for identity accents and large numbers. Never put pine text on a
  pine fill.
- Muted small text on the `paper` body uses `text-ink-muted #6b6555`.
- **`ink` `#22201b`** is the warm near-black ground: all body text and **all
  h1/h2/h3 titles stay ink, never pine or solar**. `ink-soft #3a362c` supports
  dense text. `ink-muted #6b6555` is secondary text.
- **`paper #f5f2ea`** is the body surface; `paper-deep #ece7da` bands alternate
  sections; `white #ffffff` raises cards. `line #ded7c6` is the warm hairline.

No neon glow, no gridline overlay, no gradient blobs. Warmth comes from the
paper ground, real photography, and flat pine/solar fills.

## Typography

- **Sans** (`system-ui` stack): everything readable. Headings and body.
- **Mono** (`JetBrains Mono`): the technical voice. The wordmark, inline code,
  tariff figures, data tables, `kbd`. Mono signals "this is real infrastructure."

The wordmark sets the rule: lowercase `open` at weight 500, uppercase `LEG` at
weight 700 in pine `#1f3d32`, tracking `-0.04em`, with a solar `#e8a13a` caret.
The caret blinks slowly (1.1s), disabled under `prefers-reduced-motion`.

German user-facing text: Schweizer Hochdeutsch, real umlauts, `ss` not `ß`,
active voice, no em or en dashes.

## Layout

- Centered column, `max-w-6xl` (72rem), `px-4` gutters.
- Sticky translucent nav (`bg-white/80 backdrop-blur`), warm hairline `line`.
- Warm dark footer on `brand-dark #16302a`.
- The homepage hero leads with real neighborhood photography on the paper
  ground, not a dark panel. Photos are the thesis.

## Elevation & Depth

Restraint. Flat surfaces, warm hairline borders (`line #ded7c6`) over shadows.
Allowed: nav backdrop-blur, a soft `shadow-sm` on raised cards. No heavy drop
shadows, no glow, no blurred colour blobs.

## Shapes

Rounded, not pill (except tags/badges). Buttons/inputs `8px`, cards `12px`, the
favicon tile and feature panels `16px`. Photo tiles may go larger (`24px`).

## Components

### Logo / wordmark

```
openLEG▮
```

- `open` lowercase, ink `#22201b` (paper `#f5f2ea` on dark via `.ol-logo--inverse`).
- `LEG` uppercase, pine `#1f3d32`, weight 700, no gap after `open`.
- Caret: solar `#e8a13a` block, slow blink (1.1s),
  `prefers-reduced-motion` disables it.
- Font: `JetBrains Mono`, weight 500, tracking `-0.04em`.
- Source of truth: `templates/partials/brand_wordmark.html`, with CSS in
  `templates/partials/brand_head.html`.
- Inverse variant for dark pine surfaces: `{% with brand_inverse=true %}` makes
  `open` paper `#f5f2ea` and `LEG` solar `#f0b968`.
- Standalone asset (docs, OG, README): `static/images/openleg-logo.svg`.

Never re-typeset the wordmark by hand: always include the partial or the SVG.
There is no shell prompt `>` character in the wordmark.

### Favicon

`static/favicon.svg`: solar `#e8a13a` blinking caret block on a pine `#1f3d32`
16px-radius tile with `rx 16`. It is path-based, animated by opacity, and has no
font dependency. Linked from `templates/partials/tailwind_brand.html` with
`favicon.ico` as raster fallback.

### Buttons

- Primary action: `bg-brand text-white rounded-lg font-semibold hover:bg-brand-dark`.
- Secondary: `border border-line text-ink bg-white`.
- Chips and icon badges: `bg-brand/10 text-brand`; solar chips `bg-accent/15 text-accent-dark`.

## Do's and Don'ts

- **Do** make every primary action pine (`bg-brand`); pine is the only action colour.
- **Do** use **white text on pine fills** with `bg-brand text-white`.
- **Do** keep all h1/h2/h3 titles **ink**, never pine or solar.
- **Do** reserve solar for the spark: caret, highlights, data marks, not buttons.
- **Do** use the wordmark partial; keep `LEG` uppercase and pine.
- **Do** respect `prefers-reduced-motion` (caret stops).
- **Don't** reintroduce the dark navy hero, neon glow, or gridline overlay.
- **Don't** introduce a second action colour.
- **Don't** put pine or solar on a heading.
- **Don't** add a separate icon badge next to the wordmark; the caret is the mark.
- **Don't** reintroduce a shell prompt `>` into the wordmark or favicon.
- **Don't** introduce new fonts; sans for reading, mono for the technical voice.
- **Don't** use em or en dashes in German copy.
