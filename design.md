---
version: alpha
name: OpenLEG
description: Selected ivory, cobalt and coral website and product design. Activation is controlled by SITE_RELEASE_ENABLED.
colors:
  primary: "#243B85"
  primary-hover: "#1B2F6A"
  primary-selected: "#3B55A2"
  ink: "#26324B"
  text-secondary: "#505869"
  paper: "#F6F2EA"
  white: "#FFFDF8"
  sky: "#EDF1F9"
  line: "#BCC3D0"
  coral: "#EE8D7C"
  coral-surface: "#F5D4CA"
  on-primary: "#FFFDF8"
  on-primary-secondary: "#DDE4F5"
  on-primary-link: "#FFD0C2"
  focus: "#243B85"
  focus-inverse: "#FFC8B8"
  success: "#245546"
  success-surface: "#E8F1EC"
  warning: "#775127"
  warning-surface: "#F6EADB"
  error: "#982F40"
  error-surface: "#FAE9EC"
typography:
  display:
    fontFamily: "Georgia, Times New Roman, serif"
    fontSize: 80px
    fontWeight: 400
    lineHeight: 1.05
    letterSpacing: -0.04em
  heading-page:
    fontFamily: "Georgia, Times New Roman, serif"
    fontSize: 64px
    fontWeight: 400
    lineHeight: 1.08
    letterSpacing: -0.035em
  heading-section:
    fontFamily: "Georgia, Times New Roman, serif"
    fontSize: 40px
    fontWeight: 400
    lineHeight: 1.15
    letterSpacing: -0.025em
  heading-card:
    fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: 24px
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: -0.015em
  lead:
    fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: 20px
    fontWeight: 400
    lineHeight: 1.6
  body:
    fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: 16px
    fontWeight: 400
    lineHeight: 1.65
  label:
    fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: 16px
    fontWeight: 600
    lineHeight: 1.4
  caption:
    fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: 14px
    fontWeight: 400
    lineHeight: 1.5
  data:
    fontFamily: "ui-monospace, SFMono-Regular, Consolas, monospace"
    fontSize: 16px
    fontWeight: 400
    lineHeight: 1.5
    fontFeature: "'tnum' 1"
  wordmark:
    fontFamily: "JetBrains Mono, ui-monospace, monospace"
    fontSize: 28px
    fontWeight: 500
    lineHeight: 1.1
    letterSpacing: -0.04em
rounded:
  small: 4px
  control: 8px
  panel: 12px
  pill: 9999px
spacing:
  xs: 4px
  sm: 8px
  md: 16px
  control: 12px
  lg: 24px
  xl: 32px
  section-mobile: 40px
  section-desktop: 72px
  container: 1280px
  reading: 720px
  gutter-mobile: 20px
  gutter-tablet: 32px
  gutter-desktop: 48px
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    typography: "{typography.label}"
    rounded: "{rounded.pill}"
    padding: "{spacing.control}"
    height: 48px
  button-primary-hover:
    backgroundColor: "{colors.primary-hover}"
    textColor: "{colors.on-primary}"
  button-secondary:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.primary}"
    typography: "{typography.label}"
    rounded: "{rounded.pill}"
    padding: "{spacing.control}"
    height: 48px
  input:
    backgroundColor: "{colors.white}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "{spacing.control}"
    height: 48px
  role-card:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.panel}"
    padding: "{spacing.xl}"
  explanation-panel:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    rounded: "{rounded.panel}"
    padding: "{spacing.xl}"
  association-panel:
    backgroundColor: "{colors.coral-surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.panel}"
    padding: "{spacing.xl}"
---

# OpenLEG design contract

Use this contract when changing page layout, shared components, typography, colors,
illustrations or interface wording. The selected design is implemented behind
`SITE_RELEASE_ENABLED`. The existing default stays available until the complete
website and association launch meets its release conditions. See
[the engineering contract](docs/engineering-contract.md) and
[frontend build](docs/frontend-build.md).

## Compatibility while the release switch is off

The default pages still use the existing Daylight cooperative theme. Its pine
brand, solar accent and paper surfaces remain in the Tailwind build until the
complete launch switches the shared presentation. The cobalt theme overrides
these utility tokens only within `.site-release`; do not use the legacy palette
for new release illustrations or components. Legacy primary buttons use
`bg-brand text-white`. Their existing favicons remain the default until activation.

| Legacy token | Shipped value |
| --- | --- |
| brand / pine | #1f3d32 |
| brand-light | #2c5545 |
| brand-dark | #16302a |
| accent / solar | #e8a13a |
| accent-light | #f0b968 |
| accent-dark | #c9832a |
| sage | #6e8f7c |
| sage-light | #e4ede6 |
| sage-dark | #4f6d5c |
| paper | #f5f2ea |
| paper-deep | #ece7da |
| line | #ded7c6 |
| ink | #22201b |
| ink-soft | #3a362c |
| ink-muted | #6b6555 |

## Identity and type

Keep the text wordmark `openLEG`, with a static coral baseline under LEG. The
favicon uses the same letters and baseline. No caret, roof, pulsing dot or
perpetual animation. Use a local monospace fallback for the wordmark; the theme
requires no external font request.

Start the hero with its h1. Do not add an eyebrow, slash label or miniature brand
slogan above it. Public display and section headings use Georgia at weight 400.
Body copy, form controls and product headings use the system sans stack. Mono
belongs to identifiers, code and numerical data. Let headings wrap naturally.

| Text | Desktop | Phone |
| --- | --- | --- |
| Display | 64-80 px | 40-48 px |
| Page title | 48-64 px | 36-42 px |
| Section title | 32-40 px | 28-34 px |
| Card heading | 22-24 px | 20-22 px |
| Body and controls | at least 16 px | at least 16 px |
| Captions and sources | at least 14 px | at least 14 px |

## Color and layout

YAML contains the semantic tokens. `static/css/site_release.css` applies them
only to the opt-in theme. The existing Tailwind utility names remain compatible.
Cobalt identifies actions, selection and explanatory panels. Ink is body text on
paper, sky and coral surfaces. Coral adds illustration detail and the association
panel; it is unsuitable for small text on paper. Use labeled success, warning and
error states rather than color alone.

Normal text needs 4.5:1 contrast; large text and essential controls need 3:1.
Check actual foreground/background pairs. The quiet line token alone does not
identify an input; use the stronger control boundary. On cobalt, use on-primary
text, underlined on-primary-link links and inverse focus outlines.

Public containers are 1280 px; reading columns are 720 px. Gutters are 48 px on
large screens, 32 px on tablets and 20 px below 768 px. Sections use 72 px vertical
spacing, reduced to 40 px on phones. Below 1100 px navigation becomes a vertical
menu. Reduce content columns before translated text becomes cramped. Text must
reflow at 320 CSS pixels and 200% zoom without clipping or page-wide scrolling.
Long identifiers wrap; data tables may scroll within their own labeled region.

## Shared components

Public navigation has LEG gründen, Software, Für Gemeinden, Wissen and Verein.
Every destination must exist when linked. Anmelden opens the existing owner and
municipality chooser. The utility portal has a separate explicit link. The mobile
menu exposes expanded state. Escape closes an expanded group first, then the
menu, and returns focus to its trigger. Navigation remains available without JS.

Product screens retain task-specific toolbars, tables and navigation. They use
the shared wordmark, colors, controls and focus treatment without adding the
marketing navigation or footer. Keep private and capability URLs unchanged.

| Component state | Required behavior |
| --- | --- |
| Normal | Primary action uses cobalt and on-primary text; secondary uses a cobalt boundary. |
| Hover | Primary becomes primary-hover; secondary gets sky background. |
| Selected/pressed | Use primary-selected and an explicit state label or ARIA state. |
| Focus | 3 px outline and 3 px offset, inverse on cobalt. |
| Disabled | Explain the unmet condition; retain readable text and do not simulate a click target. |
| Loading | Name the pending task, prevent duplicate submission and support recovery. |
| Empty | State what is missing and the next useful action. Missing measurements remain missing. |
| Error | Give a text reason and recovery action; inputs also get an error boundary. |

Controls have a 48 px minimum height and may grow when text wraps. Pointer targets
are at least 44 px where practical; inline prose links are exempt. Inputs keep
persistent labels and valid submitted values. Errors belong beside the affected
field and in a linked summary. Use links for destinations and buttons for actions.
Keep the checkbox purposes for email, map visibility and membership separate.

## Graphics and evidence

Write the text explanation before drawing. Raster scenes show place; HTML/SVG
diagrams explain relationships; charts show real or explicitly illustrative
values. Labels, units, sources, period and uncertainty remain readable without
color or hover. Provide an ordered explanation or data table. No automatic scene
rotation or 3D runtime; honor reduced motion. Reserve raster dimensions and keep
a useful caption when an image fails.

Use original assets with a recorded source and export settings. Do not copy
reference-site people, buildings, diagrams or text. Sanitized product captures
must identify demo data and the actual state, including incomplete measurements,
draft calculation, human approval and issued invoice. Print output keeps dates,
units, sources and logical reading order.

## Wording, languages and association facts

German uses Schweizer Hochdeutsch, real umlauts, ss and Sie. Explain LEG and
Verteilnetzbetreiber before abbreviating. Name the actor, decision and next step.
Do not invent adoption, partners, guaranteed savings, tax exemption or offers.

The complete redesign requires German, English, Swiss French and Swiss Italian.
Keep existing German URLs and language-query compatibility. Public translated
URLs must be stable with reciprocal alternates; never advertise a locale that
still falls back to German. Preserve query filters, municipality identity and
section when switching. APIs, webhooks, downloads and capability links retain
their URL and response contracts. Emails and documents use the recipient's
validated stored language. Record native-speaker review separately from
catalog/build checks.

The legal name is Verein openLEG Schweiz. Formation, adopted statutes and elected
people remain pending until actual evidence exists. Seat: Baden AG. The Verein's
planned role is software, documentation and community, without hosted-product
operation. Do not infer membership from a contribution or donation. Publish actual
people only with their consent; a portrait must depict the real person.

## Page acceptance

Verify each changed task at phone and desktop widths, keyboard, zoom, slow or
failed resources, and the next real destination. Run the relevant route, access,
privacy, build and packaging checks. Preserve old routes and workspace behavior.
Keep human language/legal review and actual account/payment setup visible as
release prerequisites. A local preview does not establish launch readiness.
