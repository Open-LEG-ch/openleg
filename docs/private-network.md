# The OpenLEG private network (Tier A, self-hosted)

How LEG members reach a self-hosted OpenLEG box from their own homes without the host
opening any ports on the home router, and without any OpenLEG-operated service in the loop.

## What it is

An optional Headscale control server (the open, self-hostable Tailscale control plane) on an
audited WireGuard data plane. The host runs it as a compose profile on the same box:

    scripts/openleg net up

Members install the standard Tailscale/WireGuard client and join with a one-time code the
host mints:

    scripts/openleg net invite      # prints a join code (a preauth key, valid 24h)
    scripts/openleg net status      # lists joined members

The data plane is WireGuard, encrypted peer to peer. Members reach the box over the private
network; nothing is exposed to the public internet.

## Why this shape

We own the control plane and the enrolment experience, but we do not invent a VPN protocol
or write transport crypto. Building a novel secure transport (key exchange, NAT traversal, a
relay for CGNAT, rekeying, replay protection) is a multi-year, security-critical effort where
any bug is a citizen-data breach. WireGuard plus a self-hosted control server gives the same
"data stays in the LEG, no open ports" outcome with a decade of audited crypto. Full
reasoning: `docs/self-host-appliance.md`.

## Honesty invariants

- **No open ports.** WireGuard is outbound UDP; the home router needs no port forwarding for
  members to reach the box.
- **Reachability caveat.** The self-hosted coordinator itself must be reachable by the members
  joining it: run it on a small VPS, or on a home box whose coordinator endpoint is reachable.
  Homes behind carrier-grade NAT cannot expose a coordinator; those LEGs need the
  OpenLEG-operated network (Tier B), which is not part of this self-hosted profile.
- **Metadata, not payloads.** A coordinator (self-hosted here, or OpenLEG-operated in Tier B)
  handles key distribution and NAT coordination. It sees connection metadata (which member
  joined, when) but never the peer-to-peer-encrypted smart-meter payloads.

## Tiers

- **Tier A (this doc):** fully self-hosted, open, no OpenLEG service in the loop.
- **Tier B (not built here):** an OpenLEG-operated coordinator and relay as a subscription or
  grant-sponsored service, for LEGs that will not run their own. A business decision that
  lives partly in `openleg-ops`.

## Setup

1. `cp headscale/config.example.yaml headscale/config.yaml` and set `server_url` to an
   endpoint your members can reach.
2. `scripts/openleg net up`
3. Create the Headscale user once (`headscale users create openleg`), then
   `scripts/openleg net invite` to mint join codes for your neighbours.
