# Spec: The homeowner-owned, LEG-shared OpenLEG appliance

Status: draft for maintainer review. Owner: registry/self-host workstream (Phase 9).
Related: `docs/leg-registry.md` (the durable goal), `README.md` (self-host on-ramp),
`docs/codex-execution.md` (execution + review rubric), `CLAUDE.md` (data policy).

## Why this exists (the growth thesis under "no direct outreach")

We want to be the #1 independent LEG platform in Switzerland, and we have one hard
constraint: no direct outreach. That rules out a sales motion. It does not rule out
distribution. The three engines that work without outreach are programmatic SEO (the
registry and Gemeinde pages, shipped in Phases 1-8), self-serve virality (claim and
referral flows, shipped), and open-source / self-host distribution, which is barely
started.

Self-host distribution is the one that also doubles as the mission. Our data policy is
that citizen smart-meter data stays inside each LEG and is never sold or aggregated for
third parties. The most literal expression of that promise is a LEG running OpenLEG on a
device the LEG itself owns, so the data never leaves the building. That is a product the
incumbent structurally cannot ship: LegHub is sold to grid operators who white-label it,
so the data always lives on the utility's infrastructure, not the residents'.

So the wedge is: make it trivial for one willing homeowner to stand up an OpenLEG box for
their neighbours, and make it obvious that this is the honest, sovereign default rather
than an escape hatch for infrastructure teams. openclaw.ai is the reference for the feel:
download-first, zero mandatory account, one-line installer, self-host as the primary path
with a hosted fallback, and a "this runs on a Raspberry Pi" framing that tells a
non-datacenter audience it is meant for them.

Today the on-ramp works against that. `README.md` says "most users do not self-host" and
frames it as "for teams who want full data sovereignty." The Quick Start is a six-line
venv/pip sequence; the Docker path is a bare `docker compose up -d` that only works after
you hand-edit `.env` and replace a dozen `your-secure-...` placeholders. That is a wall,
not an on-ramp. This spec inverts it.

## Users

- **The LEG host.** One technically-willing person in a building or neighbourhood who is
  happy to run a small always-on box (Raspberry Pi, mini PC, NAS, or a cheap VPS) for
  their community. Comfortable pasting one command. Not a sysadmin: should never have to
  generate a secret, edit YAML, or reason about TLS to get started.
- **The neighbours.** The other LEG members. Non-technical. They need to reach the
  dashboard from their own homes without the host opening ports on a home router and
  without anyone's data traversing a third-party SaaS.
- (Unchanged) **The self-hosting team** who wants the full manual/production path. We keep
  that path; we just stop making it the only door.

## Requirements

1. **One command installs a working instance.** `curl -fsSL https://openleg.ch/install.sh | sh`
   (with a documented download-and-read-first alternative) brings up a running OpenLEG on
   the host's own machine. The script is idempotent, adds missing settings and strong
   secrets to `.env`, never overwrites existing values, waits for `/livez`, and
   prints the local URL plus the two or three next steps.
2. **The served installer is the audited installer.** `GET /install.sh` returns the exact
   bytes of `scripts/install.sh` from the repo, so "pipe to shell" can never drift from the
   file a cautious host reads first. No second copy to keep in sync.
3. **Download-first landing.** `/self-host` leads with the one command and a QuickStart vs
   Advanced split. QuickStart is the installer. Advanced is the existing manual venv/pip
   and full compose path, kept verbatim, not deleted.
4. **Honest framing.** Self-host is presented as the sovereign default, hosted as the
   no-maintenance fallback, with a truthful statement of the trade-off (you run the box,
   you own the data and the updates). Swiss Hochdeutsch rules apply. No claim we cannot
   back: we do not claim the box needs zero maintenance, and we do not claim grid-topology
   eligibility (the standing honesty boundary from the registry work).
5. **Operable by a non-devops host.** A single `scripts/openleg` helper wraps the compose
   lifecycle a host actually needs: `status`, `logs`, `update`, `backup`, `restore`, `stop`.
   No Makefile, no memorising compose flags.
6. **Shared access without opening ports, in two tiers.** Neighbours reach the box from
   their own homes over an OpenLEG-branded private network on an audited WireGuard data
   plane, with no inbound firewall holes. Two tiers, so the LEG chooses its own
   sovereignty/effort trade-off:
   - **Tier A - fully self-hosted, fully open.** The LEG runs its own WireGuard control
     plane on its own box. No OpenLEG service in the loop at all: coordination and data
     both stay on the host's hardware. This is the maximal-sovereignty, open-source default.
   - **Tier B - OpenLEG-operated network (subscription or sponsored).** OpenLEG runs a
     hosted WireGuard coordinator and relay for LEGs that do not want to operate their own.
     Offered as a paid subscription, or sponsored (grant-funded, free) for eligible LEGs.
     Honesty invariant: because WireGuard is peer-to-peer encrypted, the coordinator sees
     connection metadata (which member joined, when) but never the smart-meter payloads,
     even when it relays packets for carrier-grade-NAT homes. The data policy holds in both
     tiers; only the metadata custody differs, and we state that plainly.
   (Access-model decision below. This is T4 and gets its own design pass and sign-off.)

## Non-goals (pinned, not just intended)

- **We do not invent a VPN protocol or write our own transport crypto.** See the decision
  section. "Proprietary" for us means the control plane and the enrolment UX, not the wire
  format.
- **No phone-home.** A self-hosted box never enrols itself into any OpenLEG-operated
  central service by default, and the installer sends us no telemetry. This is a direct
  consequence of the data policy and is testable: the installer contains no analytics or
  callback to our servers beyond fetching the script itself.
- **QuickStart does not provision public-domain TLS.** The one-command path targets
  LAN and the private network, where a self-signed or internal cert is fine. Public-domain
  Caddy TLS stays on the Advanced path. We do not silently request Let's Encrypt certs for
  a domain the host has not configured.
- **Not a hosted-account funnel in disguise.** The installer requires no OpenLEG account
  and no API key to bring up a working instance. Bring-your-own optional integrations
  (SMTP, model keys) stay optional and blank by default.

## Access-model decision: own the network, not the protocol

The maintainer asked whether we should build our own proprietary private VPN for the
shared-appliance access model. The answer this spec commits to: **own the control plane and
the experience, run it ourselves, but build it on an existing audited WireGuard data plane
rather than inventing a protocol.**

Reasoning:

- Inventing a secure transport means owning key exchange, NAT traversal and hole-punching,
  a relay for carrier-grade-NAT homes, rekeying, and replay protection. Every one of those
  is a place where a bug becomes a citizen-data breach. That is the exact outcome our data
  policy exists to prevent, and it is a multi-year security commitment with no product
  payoff, because neighbours do not care what is on the wire.
- What neighbours (and the mission) actually need is: one tap to join, no open ports, and
  data that never touches anyone's SaaS. All three are delivered by a self-hosted WireGuard
  control plane. Headscale (the open, self-hostable Tailscale control server) and Netbird
  are both WireGuard-based, both self-hostable, both permissively/AGPL licensed. The LEG
  host runs the coordinator on their box; neighbours install a small client and join with a
  one-time code we mint and brand. It presents as "the OpenLEG private network" with our
  UX and our codes, the coordination and the data stay on the host's box, and we inherit a
  decade of audited crypto.
- This is the low-regret shape: proprietary where it differentiates (enrolment UX, codes,
  branding, the "join your LEG's network" flow), standard where mistakes are fatal (the
  crypto and transport). We would only revisit a hand-rolled protocol if a concrete
  requirement appears that WireGuard genuinely cannot meet, and none is in view.

The same reasoning settles the self-hosted-vs-OpenLEG-operated question the maintainer
raised. Because the data plane is peer-to-peer encrypted, we can offer the coordinator
either way without breaking the data policy:

- **Tier A, fully self-hosted:** the LEG runs the whole WireGuard control plane itself.
  Nothing OpenLEG operates is in the loop. This is the open-source, maximal-sovereignty
  default and the one we lead with.
- **Tier B, OpenLEG-operated (subscription or sponsored):** OpenLEG runs the coordinator
  and relay as a hosted service for LEGs that would rather not. Charged as a subscription,
  or sponsored free for eligible LEGs where a grant covers it. This is the openclaw.ai
  shape one layer down: self-host is primary, the hosted service is the no-maintenance
  fallback. The coordinator handles key distribution and NAT coordination and can relay
  encrypted packets, but it never holds the WireGuard session keys of a peer pair, so it
  cannot read meter data. We say exactly that, and never imply the hosted tier is more
  private than it is.

Because T1-T3 do not need any of this (a LAN reaches the box directly; a VPS reaches it
over TLS), we ship the easy-download workstream first and treat the private network as T4,
with three supported access tiers overall: (0) LAN-only, zero config, the default for
members on the host's LAN; (A) self-hosted OpenLEG private network; (B) OpenLEG-operated
private network, subscription or sponsored.

## Ecosystem: the adjacent tooling that turns a box into a platform

openclaw.ai is not just an installer; it is an installer plus an ecosystem (plugins,
connectors, clients, an MCP surface) that makes the self-hosted core worth running. The
OpenLEG appliance deserves the same treatment. The box is the wedge; the ecosystem is why
a LEG stays and why word spreads without us doing outreach. Being bold about the vision
here, while keeping each piece a TDD tracer-bullet when it becomes real code:

- **Connectors - get meter data in and keep it local.** This is the highest-leverage
  ecosystem layer, because a box with no meter data is a brochure. Targets, in priority
  order: (1) importers for the Swiss standard metering-exchange formats (SDAT/ESL XML, the
  formats VNBs actually hand out), so a LEG can load its own consumption without any live
  integration; (2) a customer-interface / P1 / MUC reader for meters that expose a local
  port, and an MQTT bridge; (3) the manual CSV upload that already exists at `/meter-upload`
  as the always-works fallback. Every connector writes to the local box only. Nothing
  leaves the LEG. That constraint is the product.
- **Home Assistant add-on.** The single biggest distribution multiplier for a
  homeowner-owns-a-Pi audience: Home Assistant is already running on hundreds of thousands
  of Swiss home Pis and mini-PCs. An OpenLEG add-on (one-click install from an HA add-on
  repo, or an integration that surfaces LEG self-consumption in the HA energy dashboard)
  puts us in front of exactly the person who becomes a LEG host, with no outreach. Bold but
  very much in-scope for the mission.
- **Registry federation - self-host feeds the public registry.** The flywheel: an opt-in
  `registry publish` that lets a self-hosted box announce its LEG to the public registry at
  openleg.ch (with explicit consent, honest-scoped, human-moderated like every other
  entry). Self-host distribution then feeds the SEO growth engine we built in Phases 1-8,
  and it is a loop the incumbent cannot run because their boxes are utility-owned, not
  LEG-owned. This is uniquely ours.
- **The OpenLEG private network** (T4, both tiers above) - the shared-access layer, itself
  an ecosystem piece: open clients, self-hosted or OpenLEG-operated coordinator.
- **Operator tooling** - the `openleg` CLI (T3), the installer (T1), backup/restore/update,
  and a hardware reference ("the OpenLEG box": recommended Pi / mini-PC / NAS bills of
  materials) so a non-expert host buys the right hardware once.
- **AI / agent surface** - the LEA (AgentMail) webhook receiver remains in this repo;
  the agent gateway and its deployment belong in `openleg-ops`.
- **Template gallery** - the contract and billing-model templates (Phase 5 document
  generator) presented as a reusable, community-extendable gallery rather than buried in a
  dashboard button.

Sequencing: T1-T3 below ship the box and its operability now. Connectors (SDAT/ESL importer
first), the Home Assistant add-on, and registry federation each become their own spec +
tickets once the appliance itself is easy to stand up - they are worthless without a box to
run them on, and dangerous to design before the box's shape is fixed. T4 (the private
network) is the one that needs a design pass and sign-off before any code.

## The distribution ladder: how a LEG can run OpenLEG (and how OpenLEG earns)

There are two independent axes. The **compute axis** is where the box physically runs; the
**network axis** (the VPN tiers above) is how neighbours reach it. Keeping them separate
lets a LEG dial its own sovereignty/effort/cost trade-off instead of taking a single
bundled product.

Compute axis, from most-sovereign to least-effort:

1. **DIY self-host.** The LEG's own Pi, mini-PC, NAS, or VPS. Free and fully open source
   (this spec's T1-T3 make it a one-command install). Data lives on hardware the LEG bought.
2. **OpenLEG box, in the building.** We sell a pre-configured physical appliance that sits
   in a member's home - the maximal-locality option: the citizen data is literally in the
   neighbourhood, on Swiss soil, in the LEG's own space. Plug in power and network, it is
   running. Sold as hardware plus an optional support/updates subscription.
3. **OpenLEG-hosted VPS, near the LEG.** For LEGs that want no hardware but still want their
   data on Swiss soil and physically close: we sell a managed VPS in a Swiss region near the
   neighbourhood. Honesty caveat we must hold: "close" mainly buys Swiss jurisdiction and
   resilience, not a privacy property beyond that - the data is off-premises, on
   infrastructure we operate, so we say that plainly and never imply it equals the
   in-building box.
4. **OpenLEG multi-tenant hosted** (the existing openleg.ch platform). Zero maintenance,
   least sovereign, the fallback for a LEG that just wants it to work.

This ladder is also the answer to "how does an open, no-outreach, never-sell-data project
fund itself." We sell convenience, hardware, managed hosting near the LEG, and the
OpenLEG-operated private network subscription. We never sell citizen data, and the free DIY
rung means the paid rungs compete on genuine convenience, not on lock-in - the same code
runs on all four, and a LEG can move down the ladder to full self-host at any time. Every
rung's pitch is honest about exactly what custody the LEG is trading for how little effort.

Scope note: rungs 2 and 3 (selling hardware and managed VPS) are real business/ops
commitments - procurement, imaging, shipping, a Swiss VPS provider, support SLAs, GDPR/DSG
data-processing agreements - and those operational parts live in `openleg-ops`, not this
public repo. What the public repo owns is making the *software* identical and trivially
installable across all four rungs, so the managed offerings are pure convenience on top of
the open core. That is T1-T3.

## Tracer-bullet tickets

Each is a thin vertical slice, TDD-first (`scripts/tdd_cycle.sh` red -> green -> refactor),
gated by `scripts/tdd_cycle.sh gate` before a PR. Two-axis review (standards vs spec) and
the deletion test apply per `docs/codex-execution.md`.

- **T1 - one-command installer + route.** `scripts/install.sh` and `GET /install.sh`
  serving it verbatim. The thinnest end-to-end: a host runs one command and gets a running
  instance with generated secrets. Tests pin the safety invariants (`set -euo pipefail`,
  never overwrites an existing `.env`, generates real random secrets, waits on `/livez`,
  no phone-home) and the route (correct content-type, bytes equal the repo file).
- **T2 - `/self-host` landing + README inversion.** Download-first page with QuickStart vs
  Advanced, sitemap entry, funnel cross-links from `/open-source` and `/leg-gruenden`, and
  the README on-ramp reframed. Honesty boundary and Swiss German pinned by tests. Advanced
  path preserved.
- **T3 - `scripts/openleg` operator helper.** status/logs/update/backup/restore/stop over
  compose, plus `.env` secret-generation hardening shared with T1. Tests pin that each
  subcommand maps to the right compose call and that backup/restore round-trips the
  Postgres volume.
- **T4 - the OpenLEG private network (needs sign-off).** WireGuard/Headscale control plane
  in two tiers: **Tier A** self-hosted, packaged as an optional compose profile on the
  host's box, fully open source, no OpenLEG service in the loop; **Tier B** OpenLEG-operated
  coordinator/relay as a subscription or sponsored (grant-funded) service for LEGs that do
  not want to run their own, with the metadata-not-payload honesty invariant stated in the
  UI. Both use one-time-code enrolment and open-source clients. Forked-architecture slice:
  it gets its own design pass and an explicit maintainer go/no-go before merge, same
  discipline as the Phase 5 contract builder and Phase 6 correspondence ledger. Tier B also
  carries a business decision (pricing, sponsorship eligibility) that belongs partly in
  `openleg-ops`.

## Acceptance

- A fresh machine with Docker runs `curl -fsSL <base>/install.sh | sh` and reaches a
  working dashboard on the local URL the script prints, with no manual secret editing.
- Re-running the installer is safe: it does not clobber `.env` or the database.
- `/self-host` reads as "sovereign default," not "escape hatch," in Schweizer Hochdeutsch,
  and makes no claim we cannot back.
- The full suite stays green and `main`'s three required checks are unchanged.
