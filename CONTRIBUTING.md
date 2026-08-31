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

1. Fork the repository
2. Create a branch: `git checkout -b fix/issue-description`
3. Make your changes
4. Run the full gate: `scripts/tdd_cycle.sh gate`
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

```bash
# Clone repository
git clone https://github.com/Open-LEG-ch/openleg.git
cd openleg

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt -r requirements-dev.txt

# Run tests, lint, and formatting checks
scripts/tdd_cycle.sh gate
```

### Testing

- Use small red, green, refactor slices
- Keep the test that exposed each non-trivial bug
- Run `scripts/tdd_cycle.sh gate` before every PR

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

- Open an issue with the relevant template
- Check existing issues and PRs

Thank you for helping build free infrastructure for Switzerland's energy future! 🇨🇭⚡
