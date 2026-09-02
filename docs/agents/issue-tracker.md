# Issue tracker: GitHub

Issues and specs for this repository live in GitHub Issues. Use the `gh` CLI for tracker operations and infer the repository from `git remote -v`.

## Conventions

- Create, read, comment on, label, and close issues with `gh issue`.
- Fetch issue comments and labels when evaluating a request.
- Publish specs and tickets as GitHub issues.
- Treat pull requests as implementation surfaces, not incoming requests for triage.
- Use GitHub sub-issues and native issue dependencies for multi-ticket plans when available.

GitHub shares one number space across issues and pull requests. Resolve an ambiguous reference with `gh pr view <number>` and then `gh issue view <number>`.
