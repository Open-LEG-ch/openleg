# Proposal: reviewed equivalents in the mutation strong gate

Status: **proposal, awaiting maintainer ratification** (parent #478, opening
evidence in the #484 store-metering classification). Nothing in this document
changes a running gate; the automated strong gate keeps failing on every
survivor and every uncovered mutant until a maintainer ratifies the change
described here.

## The decision to make

The parent acceptance asks for a native 100% strong gate with unfiltered
evidence, and at the same time forbids weakening assertions or excluding
mutants. Mutmut 3.7.0 generates a fixed set of mutations on every expression.
Some of them change behavior that no test through a public seam can observe —
for example, mutating the casing of a PostgreSQL SQL string that both the
database and the driver treat case-insensitively, or renaming a reinforcement
key whose value is fixed by the query that produced it. A behavior test for
such a mutant either does not exist or asserts implementation spelling, which
the contributing guide forbids.

Today this shows up as a permanent, honest gate failure: the September
classification (#484) recorded nine SQL-casing equivalents in `store/metering`
and stopped rather than fabricate tests. The equivalent count has stayed
roughly proportional to the scope since (currently in the dozens across ten
modules), so the strict score plateaus below 100% no matter how good the
behavior tests get.

## Options

### Option A — reviewed-equivalent registry, gate exempts registry entries

Track one registry file (or extend the existing per-module
`docs/*-mutation-survivors.md` records) with, for every reviewed equivalent:

- the native mutant id,
- the mutated expression,
- the behavioral argument in one or two sentences,
- the focused test command a reviewer can run to re-verify the argument.

A small gate script runs the native mutation pass, then fails unless every
surviving mutant appears in the registry. Trick entries — a mutant listed as
equivalent while a behavior test could kill it — surface in review, because
each entry carries its re-verification command. Registry entries are re-read
on every mutmut upgrade; a tool change that makes a registered mutant
killable flips the gate to red, which is the correct outcome.

- Keeps native mutmut untouched: evidence stays unfiltered, and the strict
  call about what "counts" moves from the tool to a reviewable plaintext
  file.
- CI can finally show green, and the diff that breaks the gate is either a
  code change, a test weakening, or an unreviewed survivor.
- Cost: the gate's definition becomes repository-owned rather than
  tool-owned. That is exactly the "explicit architecture decision" the
  parent's #484 blocker asked for, and it should be a maintainer decision,
  not an agent one.

### Option B — stop generating the unobservable mutations

Change the mutation tooling so case-only string transforms (and any other
operator the review proves valueless) are never generated. Mutmut 3.7 has no
operator configuration, so this means a wrapper or fork plus a contract test
that pins the generation. The gate semantics stay pure ("every generated
mutant killed"), the tooling grows, and every mutmut upgrade must re-prove
the operator list.

### Option C — keep the strict gate and accept the plateau

Do nothing: the gate stays red forever at roughly (100 - equivalents) percent.
Honest, but it disables the gate as a review signal: a developer who knows the
gate is always red will ignore genuine regressions hidden in the same noise.

## Recommendation

Option A. It preserves the unfalsified evidence chain, turns an unkillable
class of mutants into a reviewed list instead of a permanent red, and gives CI
a gate that fails for real reasons. The per-module survivor docs the repo
already keeps are the beginning of the registry; the work to ratify is
mechanical: number the entries, add the re-verification commands, and wire the
gate script.

The checked-in PRs that implement the final gate may only exist after a
maintainer ratifies this document; until then every equivalent keeps blocking
the strict gate exactly as the parent requires.
