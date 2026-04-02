#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFESTS = sorted((REPO_ROOT / ".orch" / "cloud-runs").glob("*.json"))


def sh(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=REPO_ROOT, check=check, text=True, capture_output=True)


def codex_status(task_id: str) -> str:
    res = sh("codex", "cloud", "status", task_id)
    text = res.stdout.lower()
    if any(token in text for token in ("failed", "error", "cancelled")):
        return "failed"
    if any(token in text for token in ("completed", "complete", "succeeded", "success")):
        return "completed"
    return "running"


def codex_diff(task_id: str) -> str:
    res = sh("codex", "cloud", "diff", task_id, check=False)
    return res.stdout if res.returncode == 0 else ""


def codex_submit(env_id: str, branch: str, prompt: str) -> str:
    res = sh("codex", "cloud", "exec", "--env", env_id, "--branch", branch, prompt)
    text = res.stdout.strip()
    for line in text.splitlines():
        line = line.strip()
        if line.lower().startswith("task_id:"):
            return line.split(":", 1)[1].strip()
    if text:
        return text.split()[0]
    raise RuntimeError("missing Codex task id")


def ensure_remote_branch(base: str) -> None:
    probe = sh("git", "ls-remote", "--heads", "origin", base, check=False)
    if probe.returncode == 0 and probe.stdout.strip():
        return
    sh("git", "fetch", "origin", "main")
    sh("git", "checkout", "-B", base, "origin/main")
    sh("git", "push", "--set-upstream", "origin", base)


def ensure_lane_branch(base: str, branch: str) -> None:
    sh("git", "fetch", "origin", base)
    sh("git", "checkout", "-B", branch, f"origin/{base}")
    sh("git", "push", "--force-with-lease", "--set-upstream", "origin", branch)


def commit_diff(base: str, branch: str, lane_id: str, diff_text: str) -> str:
    sh("git", "fetch", "origin", base)
    sh("git", "checkout", "-B", branch, f"origin/{base}")
    if not diff_text.strip():
        return ""
    subprocess.run(["git", "apply", "-"], cwd=REPO_ROOT, input=diff_text, text=True, check=True)
    status = sh("git", "status", "--porcelain")
    if not status.stdout.strip():
        return ""
    sh("git", "add", "-A")
    sh("git", "commit", "-m", f"orch({lane_id}): apply Codex Cloud diff")
    sh("git", "push", "--force-with-lease", "origin", branch)
    return sh("git", "rev-parse", "HEAD").stdout.strip()


def ensure_pr(branch: str, base: str, lane_id: str, run_id: str) -> str:
    view = sh("gh", "pr", "list", "--head", branch, "--base", base, "--json", "number", "--jq", ".[0].number", check=False)
    if view.returncode == 0 and view.stdout.strip():
        return view.stdout.strip()
    create = sh(
        "gh",
        "pr",
        "create",
        "--base",
        base,
        "--head",
        branch,
        "--title",
        f"orch {lane_id} — {run_id}",
        "--body",
        f"Codex Cloud lane for {lane_id} in run {run_id}.",
    )
    return create.stdout.strip().rstrip("/").split("/")[-1]


def pr_data(pr_number: str) -> dict:
    res = sh(
        "gh",
        "pr",
        "view",
        pr_number,
        "--json",
        "state,isDraft,mergeStateStatus,statusCheckRollup",
    )
    return json.loads(res.stdout)


def checks_state(payload: dict) -> str:
    rollup = payload.get("statusCheckRollup") or []
    if not rollup:
        return "none"
    states: list[str] = []
    for item in rollup:
        if not isinstance(item, dict):
            continue
        state = (item.get("conclusion") or item.get("state") or "").upper()
        if state:
            states.append(state)
    if not states:
        return "none"
    if any(state in {"FAILURE", "FAILED", "TIMED_OUT", "ACTION_REQUIRED", "CANCELLED", "STARTUP_FAILURE"} for state in states):
        return "failed"
    if any(state in {"PENDING", "QUEUED", "IN_PROGRESS", "WAITING", "EXPECTED"} for state in states):
        return "pending"
    return "passed"


def merge_pr_if_ready(pr_number: str) -> str:
    payload = pr_data(pr_number)
    if payload.get("state") == "MERGED":
        return "merged"
    if payload.get("isDraft"):
        sh("gh", "pr", "ready", pr_number, check=False)
        payload = pr_data(pr_number)
    checks = checks_state(payload)
    if checks == "failed":
        return "failed"
    if checks == "pending":
        return "pending"
    merge = sh("gh", "pr", "merge", pr_number, "--squash", "--delete-branch", "--admin", check=False)
    if merge.returncode == 0:
        return "merged"
    return "pending"


def build_prompt(manifest: dict, lane: dict) -> str:
    files = lane.get("files") or []
    depends = lane.get("depends_on") or []
    files_line = ", ".join(files) if files else "no explicit file allowlist"
    depends_line = ", ".join(depends) if depends else "none"
    goal = lane.get("goal") or lane.get("lane_id") or lane.get("lane_slug") or "lane"
    return (
        f"Repo: {REPO_ROOT.name}\n"
        f"Run: {manifest.get('run_id','')}\n"
        f"Lane: {lane.get('lane_id','')}\n"
        f"Goal: {goal}\n"
        f"Depends on: {depends_line}\n"
        f"Preferred files: {files_line}\n"
        "Constraints:\n"
        "- Work only on this lane.\n"
        "- Keep the diff small and mergeable.\n"
        "- Preserve existing patterns.\n"
        "- Add or update tests if behavior changes.\n"
        "- Return a code diff only; no issue workflow.\n"
    )


def lane_lookup(manifest: dict) -> dict[str, dict]:
    return {lane.get("lane_id", lane.get("lane_slug", "")): lane for lane in manifest.get("lanes", [])}


def lane_dependencies_complete(manifest: dict, lane: dict) -> bool:
    lookup = lane_lookup(manifest)
    for dep in lane.get("depends_on") or []:
        dep_lane = lookup.get(dep)
        if not dep_lane:
            return False
        if dep_lane.get("status") != "completed":
            return False
    return True


def update_manifest_status(manifest: dict) -> None:
    lanes = manifest.get("lanes") or []
    if lanes and all((lane.get("status") == "completed") for lane in lanes):
        manifest["status"] = "completed"
        return
    if any((lane.get("status") == "failed") for lane in lanes):
        manifest["status"] = "failed"
        return
    if any((lane.get("status") in {"ready", "running", "started"}) for lane in lanes):
        manifest["status"] = "running"
        return
    manifest["status"] = "pending"


def submit_ready_lanes(manifest: dict) -> bool:
    env_id = manifest.get("env_id") or ""
    if not env_id:
        return False
    changed = False
    integration_branch = manifest.get("integration_branch") or "main"
    for lane in manifest.get("lanes", []):
        if lane.get("cloud_task_id"):
            continue
        if lane.get("status") not in {"pending_dependency", "open", ""}:
            continue
        if not lane_dependencies_complete(manifest, lane):
            lane["status"] = "pending_dependency"
            continue
        branch = lane.get("cloud_branch") or lane.get("branch")
        if not branch:
            continue
        ensure_lane_branch(integration_branch, branch)
        task_id = codex_submit(env_id, branch, build_prompt(manifest, lane))
        lane["cloud_task_id"] = task_id
        lane["cloud_branch"] = branch
        lane["cloud_env_id"] = env_id
        lane["child_status"] = "running"
        lane["status"] = "started"
        changed = True
    return changed


def process_manifest(path: Path) -> bool:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    changed = False
    integration_branch = manifest.get("integration_branch") or "main"
    ensure_remote_branch(integration_branch)

    for lane in manifest.get("lanes", []):
        task_id = lane.get("cloud_task_id") or ""
        pr_number = lane.get("pr_number") or ""

        if task_id and lane.get("status") != "completed":
            child_status = codex_status(task_id)
            if lane.get("child_status") != child_status:
                lane["child_status"] = child_status
                changed = True
            if child_status == "failed":
                lane["status"] = "failed"
                changed = True
            elif child_status == "completed" and not pr_number:
                branch = lane.get("cloud_branch") or lane.get("branch")
                if branch:
                    diff_text = codex_diff(task_id)
                    lane["commit_sha"] = commit_diff(integration_branch, branch, lane.get("lane_id") or lane.get("lane_slug") or "lane", diff_text)
                    lane["pr_number"] = ensure_pr(branch, integration_branch, lane.get("lane_id") or lane.get("lane_slug") or "lane", manifest.get("run_id") or path.stem)
                    lane["status"] = "ready"
                    changed = True

        pr_number = lane.get("pr_number") or ""
        if pr_number and lane.get("status") != "completed":
            merge_state = merge_pr_if_ready(pr_number)
            lane["pr_state"] = merge_state
            if merge_state == "merged":
                lane["status"] = "completed"
            elif merge_state == "failed":
                lane["status"] = "failed"
            changed = True

    if submit_ready_lanes(manifest):
        changed = True

    update_manifest_status(manifest)
    if changed:
        path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return changed


def main() -> int:
    changed_any = False
    for manifest in MANIFESTS:
        if process_manifest(manifest):
            changed_any = True
    for manifest in MANIFESTS:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        print(f"{manifest.name}: {data.get('status','')}")
        for lane in data.get("lanes", []):
            print(
                f"  {lane.get('lane_id','')}: lane={lane.get('status','')} child={lane.get('child_status','')} "
                f"task={lane.get('cloud_task_id','')} pr={lane.get('pr_number','')} pr_state={lane.get('pr_state','')}"
            )
    return 0 if changed_any or MANIFESTS else 0


if __name__ == "__main__":
    raise SystemExit(main())
