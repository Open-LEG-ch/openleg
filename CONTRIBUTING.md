# Contributing to OpenLEG

OpenLEG is free, open-source infrastructure for Swiss Lokale Elektrizitätsgemeinschaften (LEGs). Contributions can be code, reviews, documentation, translations or practical LEG experience.

## Our Mission

Maximize the number of functioning LEGs in Switzerland. Maximize their autarky. Minimize their costs. Never sell citizen data.

## Code of Conduct

Follow the [Contributor Covenant 2.1](CODE_OF_CONDUCT.md). Report conduct concerns privately to [hallo@openleg.ch](mailto:hallo@openleg.ch).

- Be respectful and constructive
- Swiss energy policy discussions are welcome
- Data sovereignty is non-negotiable
- Municipality-first, not EVU-first

## How to Contribute

### Requests and questions

Use [Anfrage / Request](https://github.com/Open-LEG-ch/openleg/issues/new?template=anfrage.yml)
to explain who needs something, the problem and how you handle it today. New
requests are reviewed at the monthly online meetup. Review does not promise
implementation or a response time; work depends on volunteer capacity.

Without a GitHub account, email [hallo@openleg.ch](mailto:hallo@openleg.ch).
The project records the request as an issue after agreeing which details can be
public. Never send credentials or personal meter data. Use
[Discussions](https://github.com/Open-LEG-ch/openleg/discussions) for questions;
report vulnerabilities privately as described below.

### Roles and ways to start

Anyone can be a contributor. Start with a request, correction or focused PR;
reviewers check changes and evidence; maintainers take responsibility for an
area. Sustained public work and a maintainer nomination lead from contributor
to reviewer to maintainer, with lazy consensus of maintainers or a majority
vote if contested. Active maintainers form the technical lead group; six months
inactive leads to emeritus status.

Maintainer areas are billing and meter data, VNB interfaces, website and
translations, and operations and security. See the current
[maintainer list](MAINTAINERS.md) and [role descriptions](GOVERNANCE.md#participation-and-roles).

Association and community roles include the board's software/documentation,
finance/administration and community/meetup portfolios, accounts review, meetup
hosting, French/Italian/English translation, LEG practice mentoring and
documentation writing. Email hallo@openleg.ch to offer help. Association
membership is separate from contributing code.

### Reporting Bugs

Use the [bug report template](.github/ISSUE_TEMPLATE/bug_report.yml) and include:
- What happened vs what you expected
- Steps to reproduce
- Browser/OS if relevant

### Proposing Features

Use the [feature request template](.github/ISSUE_TEMPLATE/feature_request.yml) and:
- Explain the problem it solves
- Indicate feature discipline alignment
- Provide context (mockups, references, etc.)

### Research Contributions

Use the [research template](.github/ISSUE_TEMPLATE/research.yml) for:
- Public dataset corrections
- Regulatory source updates
- Technical evidence
- Documentation corrections

### Pull Requests

Small reproducible fixes can go straight to a focused PR with a regression
test. Discuss changes to allocation, reconciliation or rounding, VNB formats,
billing lifecycle, and schema or API contracts in an issue before implementation.

1. Fork and clone the repository, then create a branch: `git checkout -b fix/issue-description`.
2. Follow the [onboarding guide](docs/contributor-onboarding.md). Run `scripts/contribute doctor`; if dependencies are missing, run `scripts/contribute setup`, activate `.venv`, and rerun the doctor.
3. Make a focused change. For behavior changes, start with a failing test and keep it as regression coverage.
4. Run `scripts/tdd_cycle.sh gate`; include PostgreSQL validation when the change touches database behavior. Stage only your changed paths and run `scripts/contribute check --staged`.
5. Read the DCO section below and commit with a descriptive message. If you choose to sign off while O3 is pending, use `git commit -s -m "docs(scope): describe the change"`. Push your branch and open a PR against `main`. Describe the change and validation, link any related issue, and address review findings.

#### DCO sign-off

[Owner decision O3](GOVERNANCE.md#pending-owner-approval) proposes requiring a
[Developer Certificate of Origin 1.1](https://developercertificate.org/) sign-off
on every commit in new PRs. It is pending owner approval. Sign-off is encouraged
now; this change adds no CI enforcement.

Read the certificate before signing. `git commit -s` adds a `Signed-off-by`
trailer using your configured Git name and email. It certifies your right to
submit the contribution under its license; it is not a cryptographic signature.
Your sign-off is part of the public, lasting contribution record. If you choose
to sign off while O3 is pending, sign each commit, not just the PR description.

AI-assisted or not, you own every hunk. Before opening the PR:

- Trace every caller and reuse the existing module or database seam before adding code or dependencies.
- Start with a failing behavior test. Never weaken, delete, or repurpose an existing case; add a case for new behavior.
- Test behavior through public interfaces or Flask's route map, not source text or comments.
- Tests that import or reload `app.py` must pin every environment variable the path reads, including variables expected to be empty.
- Do not catch setup or import failures and turn them into skips; broken fixtures must fail.
- A feature needs a production caller. Test-only reachability is not integration.
- For dependency replacements, compare randomized outputs against the replaced library before removing it.
- Do not report success unless the tested production path performed the work.

#### Commit Messages

- Use present tense: "Add feature" not "Added feature"
- Be concise but descriptive
- Reference issues: "Fix #123: Description"

#### Code Style

- Python 3.11+
- Follow PEP 8 (enforced by ruff)
- Add type hints where helpful
- All `.py` files must have `# SPDX-License-Identifier: AGPL-3.0-or-later` header
- Swiss German text: use proper umlauts (ä, ö, ü), ss instead of ß

### Development Setup

Start with the [contributor onboarding guide](docs/contributor-onboarding.md) if this is your first change to OpenLEG.

```bash
# Clone repository
git clone https://github.com/Open-LEG-ch/openleg.git
cd openleg

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt -r requirements-dev.txt

# Run local tests, lint, formatting, and strict typing checks
scripts/tdd_cycle.sh gate
```

`bash scripts/strict_mypy.sh` runs the same pinned strict check as `ci/lint`.
Its explicit scope lives in `mypy-strict.ini`: money display, billing approval,
and their shared record types. Add modules there as their interfaces settle.
Imported modules are analysed without reporting their existing errors; this
does not claim strict coverage of storage or the whole application. Missing-stub
exceptions, if needed, must name individual modules and explain why. The older
non-strict CI check still covers its original four files.

The local gate can pass while PostgreSQL tests are skipped if `DATABASE_URL`
is unset. Follow the [disposable PostgreSQL 16 setup](docs/contributor-onboarding.md#postgresql-validation)
to execute them. SQL query doubles do not validate PostgreSQL parsing or behavior.
Never use a production database for tests.

### Testing

- Use small red, green, refactor slices
- Keep the test that exposed each non-trivial bug
- Run `scripts/tdd_cycle.sh gate` before every PR
- For database changes, run the relevant PostgreSQL tests and check that their
  results say `PASSED`, not `SKIPPED`. See the onboarding guide for the database
  privileges, cleanup and additional CI checks.

### Security vulnerabilities

Follow [SECURITY.md](SECURITY.md) to report vulnerabilities privately. Do not
include credentials or customer data in a public issue or pull request.

### Documentation

- Update README.md for user-facing changes
- Update architecture or system docs for structural changes
- Document new API endpoints
- Swiss German for user-facing text
- Follow the public rules in `docs/engineering-contract.md`
- Follow `docs/frontend-build.md` when changing templates, styles, or CSS utilities

## License

By contributing, you agree that your contributions will be licensed under AGPL-3.0-or-later.

## Questions?

- Ask usage and setup questions in [Q&A](https://github.com/Open-LEG-ch/openleg/discussions/categories/q-a).
- Share feature ideas in [Discussions](https://github.com/Open-LEG-ch/openleg/discussions). Open an issue when the work is concrete enough to track.
- Check existing issues, pull requests and discussions before starting a new thread.
