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
  section-mobile: 64px
  section-desktop: 128px
  composition-mobile: 32px
  composition-desktop: 80px
  reading-gap: 24px
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
animated wordmark. The neighbourhood illustrations are static. Interaction motion
follows the Motion section.
Use a local monospace fallback for the wordmark; the theme
requires no external font request.

Start the hero with its h1. Do not add an eyebrow, slash label or miniature brand
slogan above it. Public display and section headings use Georgia at weight 400.
Body copy, form controls and product headings use the system sans stack. Mono
belongs to identifiers, code and numerical data. Let headings wrap naturally.

| Text | Desktop | Phone |
| --- | --- | --- |
| Display | 64-80 px | 44-48 px |
| Page title | 48-64 px | 36-42 px |
| Section title | 32-40 px | 28-34 px |
| Homepage section title | 34-52 px | 34 px |
| Card heading | 22-24 px | 20-22 px |
| Body and controls | at least 16 px | at least 16 px |
| Captions and sources | at least 14 px | at least 14 px |

## Color and layout

YAML contains the semantic tokens. `static/css/site_release.css` applies them
only to the opt-in theme. `static/css/site_story.css` adds public page spacing,
illustration compositions. Product forms retain their existing task layout. The
existing Tailwind utility names remain compatible.
Cobalt identifies actions, selection and explanatory panels. Ink is body text on
paper, sky and coral surfaces. Coral adds illustration detail and the association
panel; it is unsuitable for small text on paper. Use labeled success, warning and
error states rather than color alone.

Normal text needs 4.5:1 contrast; large text and essential controls need 3:1.
Check actual foreground/background pairs. The quiet line token alone does not
identify an input; use the stronger control boundary. On cobalt, use on-primary
text, underlined on-primary-link links and inverse focus outlines.

Public containers are 1280 px; reading columns are 720 px. Gutters are 48 px on
large screens, 32 px on tablets and 20 px below 768 px. Public sections use fluid
vertical spacing from 64 px on phones to 128 px on
large screens (`clamp(64px, 8vw, 128px)`). Composition gaps scale from 32 to 80 px.
Keep 24 px between related paragraphs and 36 px before a group of actions.
Below 1100 px navigation becomes a vertical
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
| Hover | Primary becomes primary-hover; secondary gets sky background; arrow links send one pulse along the underline. |
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

## Modular neighbourhood illustration system

`templates/release/illustrations.html` is the shared composition library. Assets
live in `static/images/release/components/`. Each is a complete, independently
rendered object matching the original neighbourhood's architectural style.
Do not crop the old neighbourhood image to simulate a separate building.

| Asset | Role | Material and shape |
| --- | --- | --- |
| `house.webp` | Individual homes and participants | Two-storey ivory house, coral pitched roof, cobalt solar panels. |
| `apartments.webp` | Shared housing and municipalities | Four-storey ivory apartment building, balconies and a flat solar roof. |
| `workshop.webp` | Businesses and practical operating work | Low timber building, glazed entrance and a broad solar roof. |
| `bungalow.webp` | Smaller participants and foreground variation | Single-storey ivory home with a flat solar roof and terrace. |
| `tree.webp` | Scale, separation and composition | Complete muted sage deciduous tree on transparent background. |
| `ground.webp` | Shared neighbourhood base | Empty pale stone platform; buildings can be positioned independently. |

All six assets have true alpha transparency. Keep the elevated isometric view,
upper-left daylight, ivory plaster, timber and solar-panel detail consistent.
Assets are images, not editable 3D models. The SVG composition controls position,
scale, depth ordering and the choice of buildings. It introduces no runtime 3D
library. Do not mirror buildings or stretch their proportions.

The macros have separate responsibilities:

- `piece(kind, x, y, size)` places a complete square building or tree asset.
- `walkway(path)` draws a static pale-stone connecting path behind buildings.
- `neighbourhood()` assembles the full homepage scene from those components.
- `building('homes' | 'apartments' | 'workshop')` creates a smaller page-specific
  composition. Homes combine the house, bungalow and trees; apartments and the
  workshop have their own arrangements.
- `page_scene` chooses the relevant composition for a public document header.

Example for a new illustration in a template:

```jinja
{% from 'release/illustrations.html' import piece, walkway %}
<svg viewBox="0 0 720 580" width="720" height="580" aria-hidden="true">
  {{ walkway('M 160 440 L 560 300') }}
  {{ piece('tree', 480, 110, 170) }}
  {{ piece('house', 230, 30, 380) }}
  {{ piece('bungalow', 10, 180, 430) }}
</svg>
```

Use decorative compositions only beside equivalent HTML explanations. The full
homepage scene has an accessible description and a visible fictional-scene
caption. Keep all important content in normal HTML. On phones, scale a complete
composition uniformly; do not crop roofs or hide buildings. Check that every
trunk and building base sits on its ground plane and that foreground items
occlude background items in the intended order.

The original `neighbourhood.webp` remains the style reference and source for
existing social graphics. The live homepage and page illustrations use the
component kit. Generation prompts, provenance and export settings are recorded
in the component directory. Further assets must follow the same reference.

The Demodorf (demo village) entry (`templates/sandbox/entry.html`) uses the public
type and colors inside the product shell: serif heading, three plain condition
chips, fictional accounts grouped by audience as selectable cards, and a sticky
submit bar. A `?konto=` value preselects only a known account; `#konto-<key>`
scrolls to it. Every simulation and deletion notice stays visible in text.

## Page rhythm and composition

Alternate compositions according to the topic, rather than repeating an equal
split or a row of boxed cards throughout the page:

1. A generous opening: the German headline has room for natural phrase breaks,
   with a short supporting paragraph and the assembled neighbourhood. One primary
   action, LEG vorbereiten; Demodorf as secondary link.
2. A clear definition: a narrow question heading and a wider answer, followed by
   four expandable role explanations with a simple cobalt rule.
3. A practical sequence: a composition of complete houses beside three numbered steps.
4. A cobalt software section: four stages, light text and coral step numbers.
   When `OPENLEG_SANDBOX_URL` names the separate Demodorf, an inset ivory
   panel follows the stages: one primary action to open the village and five
   role rows that preselect a fictional account. Without that setting the panel,
   its links and the `/software` copy are absent and the demo dashboard remains.
5. A municipality section: apartment illustration opposite text and actions.
6. An inset coral Verein panel: contribution and membership information, with the
   familiar building components in a smaller arrangement.
7. Questions: a narrow introductory heading beside a wider list of answers.

Give each section one dominant idea. Reading paragraphs stay within 64 characters
where practical, while a headline may span 12-18 characters per line. Let the grid
collapse before translated text becomes cramped. Mobile keeps the full narrative,
64 px section spacing, 20 px outer gutters and 48 px controls. Do not use fixed
section heights, scroll snapping or empty full-screen panels to create space.

## Motion

Motion answers something the visitor did. It never starts on its own.

Allowed:

- Hover, keyboard focus, press, open and close. The same effect runs for focus
  as for hover, so keyboard users see it too.
- CSS transitions and keyframes that run once and finish within 800 ms.
- Movement of a few pixels, scale, rotation, opacity and color. Animate
  `transform` and `opacity`; avoid properties that change layout.
- A short highlight that links two things, for example a role and its building,
  while the visitor points at one of them.

Not allowed:

- Loops, pulsing dots, blinking carets, perpetual energy flows and automatic
  scene rotation.
- Entrance effects on page load or scroll, parallax and scroll-driven animation.
- Moving the neighbourhood scene, the wordmark or the favicon.
- An animation script or a motion library. If a later effect cannot be done in
  CSS, it needs a new design decision.
- Hiding information behind motion or hover. Every state must also be reachable
  without a pointer and readable without the effect.

Under `prefers-reduced-motion: reduce`, skip the movement and show the end state
directly. Nothing that moves for more than 5 seconds may exist, so no pause
control is needed.

Selected hover symbols:

- Text link with arrow: one short coral pulse runs along the underline to the
  arrow, and the arrow moves forward by 3 px. On cobalt the pulse is ivory.

The neighbourhood illustrations stay still. Ordinary navigation and disclosure
controls remain interactive.

## Wording, languages and association facts

German uses Schweizer Hochdeutsch, real umlauts, ss and Sie. Explain LEG and
Verteilnetzbetreiber before abbreviating. Name the actor, decision and next step.
Do not invent adoption, partners, guaranteed savings, tax exemption or offers.

Language priority is German, Swiss French, English, then Swiss Italian. German
is the reference copy and the first version shown in design reviews. Use the
same order in language navigation. Preserve an explicit visitor language choice.
The approved homepage heading is `Solarstrom im Quartier teilen. Eine LEG gründen.`
Keep existing German URLs and language-query compatibility. Public translated
URLs must be stable with reciprocal alternates; never advertise a locale that
still falls back to German. Preserve query filters, municipality identity and
section when switching. APIs, webhooks, downloads and capability links retain
their URL and response contracts. Emails and documents use the recipient's
validated stored language. Record native-speaker review separately from
catalog/build checks.

The legal name is Verein openLEG Schweiz. It was founded on 1 November 2026
(`VEREIN_FORMATION_DATE=2026-11-01` switches the founded copy). Seat: Obersiggenthal
AG. Name board members and link the adopted Statuten only once they are supplied.
The Verein's role is software, documentation and community, without hosted-product
operation. Do not infer membership from a contribution or donation. Publish actual
people only with their consent; a portrait must depict the real person.

## Page acceptance

Verify each changed task at phone and desktop widths, keyboard, zoom, slow or
failed resources, and the next real destination. Run the relevant route, access,
privacy, build and packaging checks. Preserve old routes and workspace behavior.
Keep human language/legal review and actual account/payment setup visible as
release prerequisites. A local preview does not establish launch readiness.

## Participation and code journey

The community page alternates a plain invitation, a cobalt meetup panel, a
request/PR split, an unboxed two-column roles grid and a coral governance panel.
Role descriptions state the work and an entry point. Membership remains a
separate choice. Foundation facts come from `verein_facts`, never template dates.

The software page pairs two static inline SVG journey maps. Both use the same
eight numbered steps: people and tasks on the left, code modules on the right.
On phones the complete views stack. SVG foreignObject labels wrap as HTML;
identifiers may break within words. An expandable ordered list gives the same
labels, actors and modules as text. Shared services and boundaries remain in
ordinary HTML. The map has no script or motion, including under reduced motion.
Use cobalt for the sequence and approval stages; do not imply automated approval.
