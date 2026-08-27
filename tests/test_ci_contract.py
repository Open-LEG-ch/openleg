# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contract tests for public CI workflow shape."""

import re
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = PROJECT_ROOT / ".github" / "workflows"
DEPENDABOT_CONFIG = PROJECT_ROOT / ".github" / "dependabot.yml"
DOCKERFILE = PROJECT_ROOT / "Dockerfile"


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

    for path in WORKFLOWS_DIR.glob("*.y*ml"):
        with path.open(encoding="utf-8") as handle:
            text = handle.read()
        data = yaml.safe_load(text)
        if not _gates_pull_requests(data):
            continue
        required_workflows.append(path.name)
        jobs = data.get("jobs", {})
        for job_data in jobs.values():
            if "name" in job_data:
                seen_job_names.add(job_data["name"])

    assert len(required_workflows) == 3, required_workflows
    assert seen_job_names == expected_jobs, seen_job_names


def test_no_mainline_workflow_pushes_directly():
    offenders = []

    for path in WORKFLOWS_DIR.glob("*.y*ml"):
        with path.open(encoding="utf-8") as handle:
            text = handle.read()
        data = yaml.safe_load(text)
        if not _has_mainline_trigger(data):
            continue
        if "git push" in text:
            offenders.append(path.name)

    assert offenders == [], offenders


def test_public_repo_never_deploys_production():
    paths = tuple(WORKFLOWS_DIR.glob("*.y*ml"))
    forbidden_patterns = (
        r"\b(?:ssh|scp|rsync)\s+",
        r"\bDEPLOY_(?:HOST|USER|PATH|SSH_KEY|KNOWN_HOSTS)\b",
        r"\b(?:REMOTE_DIR|HEALTH_URL)\b",
        r"(?:ssh-action|rsync-deployments)@",
    )
    for path in paths:
        text = path.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        jobs = data.get("jobs", {})
        assert "deploy" not in jobs, path.name
        assert not any(re.search(pattern, text) for pattern in forbidden_patterns), (
            path.name
        )
        for job in jobs.values():
            environment = job.get("environment")
            name = (
                environment.get("name")
                if isinstance(environment, dict)
                else environment
            )
            assert name != "production", path.name


def test_workflow_set_changes_require_contract_review():
    assert {path.name for path in WORKFLOWS_DIR.glob("*.y*ml")} == {
        "deploy.yml",
        "image.yml",
        "lint.yml",
        "secret-scan.yml",
    }


def test_dependabot_keeps_python_and_actions_updates():
    data = yaml.safe_load(DEPENDABOT_CONFIG.read_text(encoding="utf-8"))
    ecosystems = {update["package-ecosystem"] for update in data["updates"]}
    assert {"pip", "github-actions"} <= ecosystems


def test_release_image_is_immutable_and_attested():
    path = WORKFLOWS_DIR / "image.yml"
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    job = data["jobs"]["publish"]
    steps = job["steps"]
    image = next(step for step in steps if step.get("name") == "Define image name")
    metadata = next(step for step in steps if step.get("name") == "Image metadata")
    build_steps = [
        step for step in steps if "docker/build-push-action@" in step.get("uses", "")
    ]
    scan = next(
        step
        for step in steps
        if step.get("name") == "Block high-severity image vulnerabilities"
    )
    runtime_assets = next(
        step for step in steps if step.get("name") == "Verify runtime image assets"
    )
    push = next(step for step in steps if step.get("name") == "Push scanned image")
    provenance = next(
        step for step in steps if step.get("name") == "Attest image provenance"
    )
    sbom = next(step for step in steps if step.get("name") == "Attest SBOM")

    assert "type=raw,value=latest" not in text
    assert "latest=false" in text
    assert "${GITHUB_REPOSITORY_OWNER,,}" in image["run"]
    assert metadata["with"]["images"] == "${{ steps.image.outputs.name }}"
    assert len(build_steps) == 1
    assert (
        build_steps[0]["uses"]
        == "docker/build-push-action@10e90e3645eae34f1e60eeb005ba3a3d33f178e8"
    )
    assert build_steps[0]["with"]["load"] is True
    assert build_steps[0]["with"].get("push") is not True
    assert "sbom" not in build_steps[0]["with"]
    assert "provenance" not in build_steps[0]["with"]
    assert build_steps[0]["with"]["tags"] == "${{ steps.meta.outputs.tags }}"
    assert scan["with"]["image-ref"].endswith(":${{ steps.meta.outputs.version }}")
    assert scan["with"]["image-ref"].startswith("${{ steps.image.outputs.name }}:")
    assert runtime_assets["env"]["IMAGE_REF"] == scan["with"]["image-ref"]
    assert steps.index(runtime_assets) < steps.index(scan)
    assert "docker run --rm --entrypoint test" in runtime_assets["run"]
    assert "/app/app.py" in runtime_assets["run"]
    assert "/app/templates/index.html" in runtime_assets["run"]
    assert "/app/static/css/openleg.css" in runtime_assets["run"]
    assert job["env"]["TRIVY_IMAGE_SRC"] == "docker"
    assert push["env"]["IMAGE_TAGS"] == build_steps[0]["with"]["tags"]
    assert steps.index(scan) < steps.index(push)
    assert "docker push" in push["run"]
    assert "imagetools inspect" in push["run"]
    assert provenance["with"]["subject-digest"] == "${{ steps.push.outputs.digest }}"
    assert sbom["with"]["subject-digest"] == "${{ steps.push.outputs.digest }}"
    assert provenance["with"]["subject-name"] == "${{ steps.image.outputs.name }}"
    assert sbom["with"]["subject-name"] == "${{ steps.image.outputs.name }}"
    assert "docker/setup-buildx-action@" in text
    assert "sbom-path: sbom.cdx.json" in text
    assert "attest-build-provenance" in text
    assert "trivy-action@" in text

    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    assert "RUN test -f /app/app.py" in dockerfile
    assert "test -f /app/templates/index.html" in dockerfile
    assert "test -f /app/static/css/openleg.css" in dockerfile
