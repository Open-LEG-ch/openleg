# OpenLEG governance

## Kurzfassung

Der Verein openLEG Schweiz kümmert sich um Mitglieder, Geld, Treffen und
rechtliche Fragen. Die aktiven Maintainer entscheiden über Code, Releases und
die technische Roadmap. Sie können unabhängig von einer Vereinsmitgliedschaft
mitwirken. Entscheidungen werden öffentlich im Issue oder Pull Request
besprochen: zuerst mit Zustimmung ohne Einwand, bei Widerspruch mit einer
Mehrheit der aktiven Maintainer.

Geplant sind monatliche Online-Treffen und persönliche Treffen pro Quartal,
für den Austausch zur LEG-Praxis und zur Entwicklung. Termine, Gastgeber und
Orte werden erst veröffentlicht, wenn sie feststehen. O1 (Name, Domain und
GitHub-Organisation beim Verein), O2 (höchstens ein Drittel der Maintainer beim
selben Arbeitgeber ab drei Maintainern) und O3 (DCO-Pflicht für neue PRs)
warten auf die Freigabe des Projekteigentümers. Auch O4, die befristete
Bestellung von Maintainern durch den Vorstand, wenn niemand mehr aktiv ist,
wartet auf diese Freigabe. O1 ist ein Ziel, keine bereits erfolgte Übertragung.

## Association and technical leadership

OpenLEG uses a small governance model inspired by Linux Foundation projects.
It does not claim Linux Foundation membership or hosting.

| Body | Responsibility |
| --- | --- |
| Verein openLEG Schweiz: members' assembly and board (Vorstand) | Membership, money, meetups and legal matters. The three board portfolios are software and documentation, finance and administration, and community and meetups. This describes the intended structure, not a claim that all seats are filled. |
| Technical lead group: all active maintainers | Code, releases, contribution processes and the technical roadmap. Each maintainer has one vote. The board does not override individual technical decisions. |

The association does not operate the hosted product. Association membership is
separate from contributing: anyone may contribute, including energy-provider
staff who cannot be association members. Funding or membership does not buy
technical decision rights or ownership of contributors' work. Contributions
remain under AGPL-3.0-or-later.

## Decisions

Propose technical changes in a public issue or pull request. State the proposal,
its reasons and a time for comments appropriate to its scope. Lazy consensus
means maintainers can proceed when that discussion closes without an unresolved
objection. Record the outcome in the same thread.

If contested, discuss the objections, then ask the active maintainers to vote
in that issue or PR. Adoption requires more than half of all active maintainers
to support the proposal. Without that majority, the proposal is not adopted;
revise it or keep the current behavior. Record votes and reasons publicly.
Review and CI requirements still apply to every PR.

Technical meetings are open to anyone. Publish agendas and notes in
[Discussions](https://github.com/Open-LEG-ch/openleg/discussions), and link
technical decisions back to their issue or PR so people who cannot attend can
participate. Keep personal information, conduct reports and undisclosed security
issues private; publish outcomes only where safe. Association decisions follow
its adopted statutes and members' assembly, not the technical voting process.

## Participation and roles

The path is contributor, then reviewer, then maintainer. Sustained public work
and useful reviews establish experience. An existing maintainer nominates a
reviewer or maintainer in a public issue, with examples of their work and the
proposed area. Active maintainers use lazy consensus, or the majority rule above
if contested. Update [MAINTAINERS.md](MAINTAINERS.md) when appointments change.

After six months without project contributions, reviews or technical decision
participation, a maintainer moves to emeritus. Record this in the maintainer list
and remove active voting and maintainer access. Emeritus recognises past work;
returning to active status uses the same public nomination process.

### Recovery when no active maintainer remains

O4 is pending owner approval and is not yet in force. Until approved, the
regular process provides no appointment path when no active maintainer remains.
The proposed exception applies only in that situation:

1. The Vorstand of Verein openLEG Schweiz publishes an open call for at least
   14 days.
2. After the call, the board publicly appoints one or more interim maintainers
   by board decision and records the appointment in a public issue.
3. Interim maintainers serve until the regular contributor-to-maintainer path
   has produced at least one maintainer, for at most 12 months. The members'
   assembly may revoke the appointment.

This recovery role belongs to the association, not to the founder or any other
individual. It grants no founder veto or permanent seat.

### Role descriptions

| Code and project role | What it does and how to start |
| --- | --- |
| Contributor | Anyone can contribute code, tests, documentation or requests; start with an issue or a focused PR using [CONTRIBUTING.md](CONTRIBUTING.md). |
| Reviewer | Checks proposed changes and their evidence; start by reviewing an open PR and build towards a public nomination. |
| Maintainer: billing and meter data | Reviews and maintains allocation, billing and metering changes; start with contributions and reviews in that area. |
| Maintainer: VNB interfaces | Maintains distribution-system-operator exchanges; start with a reproducible integration case or review. |
| Maintainer: website and translations | Maintains the site and language coverage; start with a page correction or translation review. |
| Maintainer: operations and security | Maintains operational interfaces and security practices; start with public tooling or documentation, reporting vulnerabilities privately through [SECURITY.md](SECURITY.md). |
| Technical lead group | Active maintainers jointly decide technical direction and releases; join through the maintainer nomination process. |

For association and community roles, contact [hallo@openleg.ch](mailto:hallo@openleg.ch)
to offer help. These are invitations to contribute, not a list of filled posts.
Board and accounts-review appointments follow the association's statutes.

| Association and community role | What it does and how to start |
| --- | --- |
| Board: software and documentation | Coordinates association support for software and documentation; express interest to the association. |
| Board: finance and administration | Handles membership administration, budgets and accounts; express interest to the association. |
| Board: community and meetups | Coordinates community participation and meetings; express interest to the association. |
| Accounts reviewer (Rechnungsprüfung) | Checks the association's accounts; offer relevant experience to the association. |
| Meetup host | Organises a practical or development session; propose a topic and format. |
| Translator | Helps with French, Italian or English; offer a translation or review of an existing page. |
| LEG practice mentor | Shares experience from running a LEG; propose a practical question or session. |
| Documentation writer | Makes setup and project workflows easier to follow; suggest or submit a correction. |

## Affiliations and independence

Maintainers disclose employers and other relevant affiliations in
[MAINTAINERS.md](MAINTAINERS.md), keep them current, and disclose conflicts in
related decisions. A missing disclosure is marked as pending, never assumed to
mean independent.

## Meetups and requests

The agreed rhythm is one online meetup each month and one in-person meetup each
quarter, covering both LEG practice and development. Dates, hosts and locations
are not yet set. Publish only confirmed events and publish meeting notes.

New requests are reviewed at the monthly online meetup. Use the
[Anfrage / Request form](https://github.com/Open-LEG-ch/openleg/issues/new?template=anfrage.yml)
or email [hallo@openleg.ch](mailto:hallo@openleg.ch) without a GitHub account.
The project records emailed requests as issues after agreeing which details can
be public. Reviewing a request does not promise implementation or a response
time. Work depends on volunteer capacity.

## Pending owner approval

These four proposals are not in force until the project owner explicitly
approves them and that decision is recorded here with a public issue or PR link.

| Decision | Proposal and current status |
| --- | --- |
| O1: project assets | Goal: Verein openLEG Schweiz holds the OpenLEG name, openleg.ch domain and Open-LEG-ch GitHub organisation. Pending owner approval; transfer is not claimed as complete. Approval and completed transfer must be recorded separately. |
| O2: employer limit | Once there are three or more active maintainers, at most one third may come from the same employer. Pending owner approval; not an existing eligibility rule. |
| O3: DCO | Require a Developer Certificate of Origin sign-off on every commit in new PRs. Pending owner approval; sign-off is encouraged meanwhile. This documentation change adds no CI enforcement. |
| O4: maintainer recovery | Only when no active maintainer remains, the association board may appoint interim maintainers under the [recovery rule](#recovery-when-no-active-maintainer-remains). Pending owner approval; not an existing appointment power. |

## Sources and terminology

- [LF Projects General Rules of Operation](https://lfprojects.org/policies/general-rules-of-operation-policy/), sections 2.2, 2.3 and 3, inform open participation, public decisions and the asset-holding goal.
- [PowSyBL technical charter](https://www.powsybl.org/pages/contributing/technical-charter.html), sections 2 and 3, is the real LF project charter used here. Its contributors, committers and Technical Steering Committee correspond to OpenLEG's contributors, maintainers and technical lead group. OpenLEG adds a reviewer role and uses the public, all-active-maintainer majority rule above rather than a separate meeting quorum.
- [Developer Certificate of Origin 1.1](https://developercertificate.org/) defines the certification made by a sign-off.
- The [Contributor Covenant 2.1](CODE_OF_CONDUCT.md) sets participation standards and the reporting route.
