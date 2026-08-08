# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contract tests for public CI workflow shape."""

from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = PROJECT_ROOT / ".github" / "workflows"


def _read_workflow(path: Path):
    with path.open(encoding="utf-8") as handle:
        return handle.read(), yaml.safe_load(handle.read())


def _on_section(data):
    return data.get("on") or data.get(True) or {}


def _has_mainline_trigger(data):
    on = _on_section(data)
    push = on.get("push", {})
    pull_request = on.get("pull_request", {})
    push_branches = push.get("branches", [])
    pr_branches = pull_request.get("branches", [])
    return "main" in push_branches or "main" in pr_branches


def _gates_pull_requests(data):
    on = _on_section(data)
    pr_branches = (on.get("pull_request", {}) or {}).get("branches", [])
    return "main" in pr_branches


def test_exactly_three_required_check_workflows_with_expected_job_names():
    """The PR-gating workflows must be exactly the three required checks.

    Deploy/CD workflows push to main but do not gate PRs, so they are excluded.
    """
    expected_jobs = {"ci/lint", "ci/test", "ci/security"}
    required_workflows = []
    seen_job_names = set()

    for path in WORKFLOWS_DIR.glob("*.yml"):
        with path.open(encoding="utf-8") as handle:
            text = handle.read()
        data = yaml.safe_load(text)
        if not _gates_pull_requests(data):
            continue
        required_workflows.append(path.name)
        jobs = data.get("jobs", {})
        for _job_id, job_data in jobs.items():
            if "name" in job_data:
                seen_job_names.add(job_data["name"])

    assert len(required_workflows) == 3, required_workflows
    assert seen_job_names == expected_jobs, seen_job_names


def test_no_mainline_workflow_pushes_directly():
    offenders = []

    for path in WORKFLOWS_DIR.glob("*.yml"):
        with path.open(encoding="utf-8") as handle:
            text = handle.read()
        data = yaml.safe_load(text)
        if not _has_mainline_trigger(data):
            continue
        if "git push" in text:
            offenders.append(path.name)

    assert offenders == [], offenders


def test_public_repo_never_deploys_production():
    for path in WORKFLOWS_DIR.glob("*.yml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        jobs = data.get("jobs", {})
        assert "deploy" not in jobs, path.name


def test_release_image_is_immutable_and_attested():
    text = (WORKFLOWS_DIR / "image.yml").read_text(encoding="utf-8")
    assert "type=raw,value=latest" not in text
    assert "sbom: true" in text
    assert "provenance: mode=max" in text
    assert "attest-build-provenance" in text
    assert "steps.build.outputs.digest" in text
