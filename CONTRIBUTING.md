# Contributing to OpenLEG

Thank you for your interest in contributing to OpenLEG! This project is free, open-source public infrastructure for Swiss Lokale Elektrizitätsgemeinschaften (LEGs).

## Our Mission

Maximize the number of functioning LEGs in Switzerland. Maximize their autarky. Minimize their costs. Never sell citizen data.

## Code of Conduct

- Be respectful and constructive
- Swiss energy policy discussions are welcome
- Data sovereignty is non-negotiable
- Municipality-first, not EVU-first

## How to Contribute

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

1. Fork the repository
2. Create a branch: `git checkout -b fix/issue-description`
3. Make your changes
4. Run the local gate: `scripts/tdd_cycle.sh gate`; include PostgreSQL validation when the change touches database behavior.
5. Commit with descriptive message
6. Push and create a Pull Request

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

# Run local tests, lint, and formatting checks
scripts/tdd_cycle.sh gate
```

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

Thank you for helping build free infrastructure for Switzerland's energy future! 🇨🇭⚡
