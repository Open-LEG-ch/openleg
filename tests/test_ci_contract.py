# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contract tests for public CI workflow shape."""

import json
import os
import re
import subprocess
from pathlib import Path

import pytest
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
        "announce-release.yml",
        "release-announcement-watchdog.yml",
        "secret-scan.yml",
    }


def test_release_announcement_is_manual_or_release_driven():
    path = WORKFLOWS_DIR / "announce-release.yml"
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    on = _on_section(data)

    assert on["release"]["types"] == ["published"]
    assert "workflow_dispatch" in on
    assert data["permissions"] == {"contents": "read", "discussions": "write"}
    assert "https://openleg.ch/community" in text
    assert "createDiscussion" in text
    assert '"${DRY_RUN}" = "true"' in text
    assert "github.event.release.node_id" in text
    assert "<!-- openleg-release:${RELEASE_ID} -->" in text
    assert "discussions(first: 100, after: $after, categoryId: $categoryId)" in text
    assert "pageInfo { hasNextPage endCursor }" in text
    assert 'pagination_args=(-f after="${after}")' in text
    assert "pageInfo.hasNextPage" in text
    assert "pageInfo.endCursor" in text
    assert 'if [[ -n "${existing_url}" ]]' in text
    assert "Announcement already exists" in text


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
        == "docker/build-push-action@c3c9e263c25d99ce0380d002d59b67737d91b0dc"
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
    assert "/app/templates/role_access.html" in runtime_assets["run"]
    assert "/app/templates/dashboard.html" in runtime_assets["run"]
    assert "/app/static/js/landing_segments.js" in runtime_assets["run"]
    assert "/app/static/images/landing/urban.webp" in runtime_assets["run"]
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
    assert "test -f /app/templates/dashboard.html" in dockerfile
    assert "test -f /app/static/css/openleg.css" in dockerfile


def test_release_announcement_watchdog_contract():
    text = (WORKFLOWS_DIR / "release-announcement-watchdog.yml").read_text(
        encoding="utf-8"
    )
    data = yaml.safe_load(text)
    on = _on_section(data)
    assert set(on) == {"push", "release", "schedule", "workflow_dispatch"}
    assert on["schedule"] == [{"cron": "17 * * * *"}]
    assert data["permissions"] == {
        "contents": "read",
        "discussions": "read",
        "issues": "write",
    }
    assert on["push"] == {"branches": ["main"]}
    assert on["release"]["types"] == ["published", "edited", "unpublished", "deleted"]
    assert data["concurrency"] == {
        "group": "release-announcement-watchdog",
        "cancel-in-progress": False,
    }
    for job in data["jobs"].values():
        assert "permissions" not in job
        for step in job["steps"]:
            assert "${{" not in step.get("run", "")
    script = data["jobs"]["watchdog"]["steps"][0]["run"]
    assert script.startswith("set -euo pipefail")
    assert "https://openleg.ch/health" in script
    assert "--max-time" in script
    assert "--retry 2" in script
    assert ".release.tag" in script
    assert "releases/tags/${tag}" in script
    assert "<!-- openleg-release:${RELEASE_ID} -->" in script
    assert 'select(.slug == "announcements" or .name == "Ankündigungen")' in script
    assert 'owner="${owner}"' in script
    assert 'name="${name}"' in script
    assert "discussions(first: 100, after: $after, categoryId: $categoryId)" in script
    assert "pageInfo { hasNextPage endCursor }" in script
    assert 'pagination_args=(-f after="${after}")' in script
    assert "pageInfo.hasNextPage" in script
    assert "pageInfo.endCursor" in script
    assert "GITHUB_STEP_SUMMARY" in script
    assert "<!-- openleg-release-watchdog -->" in script
    assert "state=open" in script
    assert "gh issue close" in script
    assert "--comment" in script
    assert "::error::" in script
    assert "::warning::" in script


@pytest.mark.parametrize(
    ("scenario", "age", "existing", "status", "action"),
    [
        ("unreachable", 10800, "different", 0, ""),
        ("missing", 10800, "different", 0, ""),
        ("null", 10800, "different", 0, ""),
        ("malformed", 10800, "", 5, ""),
        ("404", 10800, "", 0, "create"),
        ("draft", 10800, "", 0, "create"),
        ("api-error", 10800, "", 1, ""),
        ("no-thread", 1800, "", 0, ""),
        ("no-thread", 1801, "", 0, "create"),
        ("bare", 7200, "", 0, ""),
        ("bare", 7201, "", 0, "create"),
        ("bare", 10800, "different", 0, "edit"),
        ("bare", 10800, "same", 0, ""),
        ("enriched", 10800, "", 0, ""),
        ("enriched", 10800, "different", 0, "close"),
    ],
)
def test_release_watchdog_script(tmp_path, scenario, age, existing, status, action):
    """Execute shell with paginated API replies and capture every issue write."""
    from datetime import UTC, datetime

    workflow = yaml.safe_load(
        (WORKFLOWS_DIR / "release-announcement-watchdog.yml").read_text()
    )
    script = workflow["jobs"]["watchdog"]["steps"][0]["run"]
    release_url = "https://github.com/Open-LEG-ch/openleg/releases/tag/v0.1.24"
    thread_url = "https://github.com/Open-LEG-ch/openleg/discussions/743"
    body = (
        f"OpenLEG v0.1.24 ist veröffentlicht: {release_url}\n\n"
        "Fragen, Ideen und die aktuelle Arbeit der Community: "
        "https://openleg.ch/community\n\n<!-- openleg-release:RE_node -->"
    )
    if scenario == "enriched":
        body += "\nRelease highlights."
    fixture = {
        "release": {
            "draft": scenario == "draft",
            "html_url": release_url,
            "node_id": "RE_node",
            "name": "v0.1.24",
            "published_at": datetime.fromtimestamp(2000000000 - age, tz=UTC).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
        },
        "thread": {"body": body.replace("\n\n", " \n\t"), "url": thread_url},
    }
    (tmp_path / "fixture.json").write_text(json.dumps(fixture))
    stub = r"""#!/usr/bin/env python3
import json, os, pathlib, sys
scenario = os.environ['SCENARIO']
exe = pathlib.Path(sys.argv[0]).name
if exe == 'date':
    print(2000000000)
    sys.exit(0)
if exe == 'curl':
    if scenario == 'unreachable':
        sys.exit(28)
    if scenario == 'malformed':
        print('not json')
    else:
        print(json.dumps({} if scenario == 'missing' else {
            'release': None if scenario == 'null' else {'tag': 'v0.1.24'}
        }))
    sys.exit(0)
args = sys.argv[1:]
log = pathlib.Path(os.environ['WRITES'])
if args[0] == 'issue':
    with log.open('a') as handle:
        handle.write(json.dumps(args) + '\n')
    if '--body-file' in args:
        body = pathlib.Path(args[args.index('--body-file') + 1]).read_text()
        pathlib.Path(os.environ['LAST_BODY']).write_text(body)
    sys.exit(0)
assert args[0] == 'api'
args = args[1:]
fixture = json.loads(pathlib.Path(os.environ['FIXTURE']).read_text())
endpoint = next((arg for arg in args if arg.startswith('repos/')), '')
if '/issues?' in endpoint:
    assert 'state=open' in endpoint
    existing = os.environ['EXISTING']
    issues = [{'number': 1, 'title': 'unrelated', 'body': 'Other issue'},
              {'number': 99, 'title': 'human issue', 'body': '<!-- openleg-release-watchdog -->',
               'user': {'login': 'contributor'}}]
    if existing:
        issues.append({'number': 42, 'user': {'login': 'github-actions[bot]'},
                       'title': os.environ.get('SAME_TITLE', 'old'),
                       'body': os.environ.get('SAME_BODY', '<!-- openleg-release-watchdog -->')})
    print(json.dumps([issues]))
elif endpoint:
    if scenario in ('404', 'api-error'):
        print('gh: Not Found (HTTP 404)' if scenario == '404'
              else 'gh: Forbidden (HTTP 403)', file=sys.stderr)
        sys.exit(1)
    print(json.dumps(fixture['release']))
elif any('discussionCategories(' in arg for arg in args):
    print('category_live')
else:
    assert 'categoryId=category_live' in args
    second = 'after=next_page' in args
    print(json.dumps({'data': {'repository': {'discussions': {
        'nodes': [fixture['thread']] if second and scenario != 'no-thread' else [],
        'pageInfo': {'hasNextPage': not second, 'endCursor': 'next_page'}
    }}}}))
"""
    for executable in ("curl", "gh", "date"):
        path = tmp_path / executable
        path.write_text(stub)
        path.chmod(0o755)
    summary = tmp_path / "summary"
    writes = tmp_path / "writes"
    last_body = tmp_path / "body"
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "SCENARIO": scenario,
        "EXISTING": existing,
        "FIXTURE": str(tmp_path / "fixture.json"),
        "WRITES": str(writes),
        "LAST_BODY": str(last_body),
        "GITHUB_REPOSITORY": "Open-LEG-ch/openleg",
        "GITHUB_STEP_SUMMARY": str(summary),
    }
    if existing == "same":
        seed = subprocess.run(
            ["bash", "-c", script],
            check=False,
            env={**env, "EXISTING": ""},
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert seed.returncode == 0, seed.stderr
        call = json.loads(writes.read_text().splitlines()[0])
        env["SAME_TITLE"] = call[call.index("--title") + 1]
        env["SAME_BODY"] = last_body.read_text()
        writes.unlink()
    result = subprocess.run(
        ["bash", "-c", script],
        check=False,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == status, result.stderr
    calls = (
        [json.loads(line) for line in writes.read_text().splitlines()]
        if writes.exists()
        else []
    )
    assert [call[1] for call in calls] == ([action] if action else [])
    if action in ("create", "edit"):
        assert "::warning::" in result.stdout
        assert "<!-- openleg-release-watchdog -->" in last_body.read_text()
        assert "v0.1.24" in last_body.read_text()
    if action == "close":
        assert "--comment" in calls[0]
        assert "42" in calls[0]
    report = summary.read_text()
    assert all(field in report for field in ("Tag:", "Release:", "Thread:", "Verdict:"))
    if scenario in ("bare", "enriched"):
        assert release_url in report
        assert thread_url in report
