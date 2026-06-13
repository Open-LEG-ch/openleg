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
4. Run tests: `pytest tests/ -v`
5. Run linter: `ruff check . && ruff format --check .`
6. Commit with descriptive message
7. Push and create a Pull Request

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

# Run tests
pytest tests/ -v

# Run linter
ruff check .
ruff format --check .
```

### Testing

- Write tests for new features
- Maintain or improve test coverage
- Use TDD when practical

### Documentation

- Update README.md for user-facing changes
- Update architecture or system docs for structural changes
- Document new API endpoints
- Swiss German for user-facing text

## License

By contributing, you agree that your contributions will be licensed under AGPL-3.0-or-later.

## Questions?

- Open an issue with the relevant template
- Check existing issues and PRs

Thank you for helping build free infrastructure for Switzerland's energy future! 🇨🇭⚡
