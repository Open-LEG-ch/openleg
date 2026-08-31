# LEG Registry

## Goal

Become the #1 **independent** LEG registry in Switzerland: open to every Lokale
Elektrizitätsgemeinschaft (LEG) regardless of which platform formed it,
self-service submit and claim, honestly scoped eligibility guidance, and a
real freshness/verification pipeline so listings stay trustworthy over time.

The product repository owns registry storage, verification actions, moderation,
and API endpoints. Its public website renders the directory and stakeholder
pages. `openleg-ops` owns their production deployment.

## Why This Matters

Existing LEG tooling in Switzerland is largely sold through individual grid
operators (VNB) to their own customers. A directory built that way can only
ever show LEGs that went through one specific platform and one specific
onboarded utility — it cannot become a neutral, nationwide source of truth.
There is currently no comprehensive public data source that lists all Swiss
LEGs: ElCom has no LEG-specific dataset yet, federal reporting is periodic
rather than live, canton-level energy offices are inconsistent, and the
commercial register is not a reliable source because a LEG is typically
formed as an *einfache Gesellschaft* (simple partnership), not a registered
legal entity. An open, self-service registry that any LEG can join — whoever
formed it, on whatever platform — fills that gap.

## Phase 0 — Positioning

Sharpen the public website's stakeholder pathway pages (`/fuer-bewohner`,
`/fuer-gemeinden`, `/leg-gruenden`, `/open-source`) so the platform's real,
already-shippable differentiators are explicit: transparent pricing, no
lock-in to a single grid operator, and tooling that works the same way
regardless of which Verteilnetzbetreiber serves an address. All positioning
copy is competitor-neutral: it states what OpenLEG does, not a comparison to
any named product.

## Phase 1 — Open Registry MVP

A public, searchable website directory (`/leg-verzeichnis`) of Swiss LEGs, populated
by self-service submission and gated by human moderation before anything
goes public. Any LEG can list itself, independent of which platform (if any)
was used to form it. Claim-by-email-verification lets a LEG's real operator
take ownership of a self-submitted listing.

## Phase 2 — Freshness and Verification Pipeline

Periodic re-confirmation nudges to listed contacts, plausibility cross-checks
against public reference data already used elsewhere in this codebase (ElCom
grid-operator-per-municipality data, postal-code/canton resolution), and
staleness surfaced to moderators. This keeps the registry accurate without
requiring data that does not publicly exist.

## Explicit Non-Goals (Right Now)

- **No grid-topology (Netz-Topologie) eligibility verification.** Whether
  specific buildings share a low-voltage grid segment is data held
  individually by each Swiss grid operator, not public data. Nothing in the
  registry, and nothing in any eligibility-check tooling built on top of it,
  claims to verify this. A published listing means a LEG self-reported and
  was moderated for plausibility — it is not a topology or eligibility
  verdict.
- **No automated import or scrape** from ElCom, BFE, or canton sources yet.
  Every registry entry starts as a human-moderated self-submission.
- **No fully automated re-verification policy yet.** Registry entries,
  verification tokens, `last_verified_at`, reminder emails, and the explicit
  confirmation action exist. Scheduling, escalation, and automatic moderation
  decisions remain operator-controlled.

## Related Docs

- `docs/codex-execution.md` — execution contract and review rubric.
- `docs/architecture.md` — overall system architecture.
- `docs/repo-boundary.md` — public/private repo boundary rules.
