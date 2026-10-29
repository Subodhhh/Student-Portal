#!/usr/bin/env python3
"""Guards the two-portal deployment boundary (no dependencies beyond PyYAML).

  * The GitHub Pages workflow (deploy-pages.yml) is disabled; if its push trigger is ever restored it must again be
    path-filtered to the Student frontend (an Institute-only change must not trigger it, every Student change must).
  * The Cloudflare Pages demo workflow (cloudflare-pages-demo.yml) runs only for feature/* and Institute-Student-Portal
    pushes and pull requests, may deploy only for a push to Institute-Student-Portal, only through the protected
    "cloudflare-demo" environment, and only that job may see the two Cloudflare secrets. Nothing in it may target main,
    QA or dev.
  * No Institute workflow may deploy anything, reference repository secrets, or use a database URL that is not a
    local/throwaway one, so CI can never reach production credentials.
"""
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"


def glob_to_regex(pattern: str) -> re.Pattern:
    """GitHub path-filter glob: ** crosses directories, * does not."""
    out, i = "", 0
    while i < len(pattern):
        if pattern[i:i + 3] == "**/":
            out += "(?:.*/)?"
            i += 3
        elif pattern[i:i + 2] == "**":
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile(f"^{out}$")


def triggers(path: str, patterns: list) -> bool:
    return any(glob_to_regex(p).match(path) for p in patterns)


def load_workflow(name: str):
    f = WORKFLOWS / name
    return yaml.safe_load(f.read_text()) if f.exists() else None


def triggers_of(data) -> dict:
    """The `on:` block as a dict (YAML reads a bare `on` key as True; `on: push` / `on: [push]` are normalised)."""
    on = data.get("on", data.get(True))
    if isinstance(on, str):
        return {on: None}
    if isinstance(on, list):
        return {k: None for k in on}
    return on or {}


DEPLOY_STEP = re.compile(r"deploy-pages|upload-pages-artifact|configure-pages|docker (push|login)|kubectl|terraform|\bssh |\bscp ")
SECRET_REF = re.compile(r"\$\{\{\s*secrets\.(\w+)")
DB_VAR = re.compile(r"(?:DATABASE_URL|MIGRATION_DATABASE_URL|PGURL|PGBASE|TEST_DATABASE_URL)\s*[:=]\s*(\S+)")
SAFE_DB = re.compile(r"^(sqlite|postgresql(\+\w+)?://[^@/]*@(127\.0\.0\.1|localhost)|\$\{\{)")


def check() -> list:
    errors = []
    gh_pages = load_workflow("deploy-pages.yml")
    gh_on = triggers_of(gh_pages) if gh_pages else {}
    paths = (gh_on.get("push") or {}).get("paths") if "push" in gh_on else None
    if "push" in gh_on and not paths:
        errors.append("deploy-pages.yml has no push path filter: any push to dev redeploys the Student Portal")
    elif paths:
        for p in ("institute-frontend/src/App.tsx", "institute-backend/legacy-monolith/app/main.py", "docs/institute-portal/README.md",
                  "student-backend/student-test-service/app/main.py", ".github/workflows/institute-backend-ci.yml",
                  "scripts/check_workflow_triggers.py"):
            if triggers(p, paths):
                errors.append(f"{p} must NOT trigger the Student Pages deployment")
        for p in ("student-frontend/src/App.tsx", "student-frontend/package.json", "student-frontend/package-lock.json",
                  "student-frontend/public/favicon.svg",
                  ".github/workflows/deploy-pages.yml"):
            if not triggers(p, paths):
                errors.append(f"{p} MUST still trigger the Student Pages deployment")

    for wf in sorted(WORKFLOWS.glob("institute-*.yml")) + sorted(WORKFLOWS.glob("student-*.yml")):
        text = wf.read_text()
        if DEPLOY_STEP.search(text):
            errors.append(f"{wf.name} contains a deployment step")
        used = set(SECRET_REF.findall(text)) - {"GITHUB_TOKEN"}
        if used:
            errors.append(f"{wf.name} references secrets {sorted(used)}: CI must not receive production credentials")
        for m in DB_VAR.finditer(text):
            value = m.group(1).strip("\"'")
            if not SAFE_DB.match(value):
                errors.append(f"{wf.name}: database URL {value!r} is not a local/throwaway one")
        if re.search(r"pull_request_target", text):
            errors.append(f"{wf.name} uses pull_request_target (would run untrusted code with secrets)")
        perms = yaml.safe_load(text).get("permissions")
        if perms != {"contents": "read"}:
            errors.append(f"{wf.name} must declare top-level `permissions: {{contents: read}}` (found {perms!r})")

    errors += check_cloudflare_demo()

    # Coverage the release process relies on: each of these must be exercised by some CI workflow.
    all_ci = "\n".join(w.read_text() for w in sorted(WORKFLOWS.glob("institute-*.yml")) + sorted(WORKFLOWS.glob("student-*.yml")))
    for needle, what in (("verify-institute-on-postgres.sh", "the PostgreSQL migration/suite verification"),
                         ("pytest -q", "the backend test suites"), ("npm run build", "the Institute frontend build"),
                         ("tests/test_auth.py", "the existing Student authentication regression test"),
                         ("tests/test_internal_corrections.py", "the result-correction service tests"),
                         ("tests/test_internal_notifications.py", "the notification service tests"),
                         ("tests/test_internal_attempts.py", "the attempt service tests"),
                         ("tests/test_internal_answer_key.py", "the answer-key correction tests"),
                         ("verify-institute-services-on-postgres.sh", "the service migration verification on PostgreSQL"),
                         ("gen_gateway_conf.py --check", "the gateway-configuration freshness check"),
                         ("docker build -f institute-backend/institute-", "the Institute service image builds"),
                         ("institute-backend/institute-integration-tests", "the cross-service integration suite")):
        if needle not in all_ci:
            errors.append(f"no CI workflow runs {what} ({needle!r} not found)")
    return errors


CLOUDFLARE_WORKFLOW = "cloudflare-pages-demo.yml"
CLOUDFLARE_SECRETS = {"CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID"}
RELEASE_BRANCH = "Institute-Student-Portal"
RELEASE_REF = f"refs/heads/{RELEASE_BRANCH}"
# The ONLY condition under which a job may deploy or read a secret. Compared exactly (whitespace-normalised), so an
# added `|| ...` or a different event/branch can never widen it.
DEPLOY_CONDITION = f"github.event_name == 'push' && github.ref == '{RELEASE_REF}'"
ALLOWED_TRIGGERS = {"push", "pull_request"}
PROTECTED_BRANCH = re.compile(r"(?<![\w/-])(main|QA|dev)(?![\w/-])")


def _norm(expr) -> str:
    text = str(expr or "").strip()
    if text.startswith("${{") and text.endswith("}}"):
        text = text[3:-2]
    return " ".join(text.split())


def check_cloudflare_demo() -> list:
    data = load_workflow(CLOUDFLARE_WORKFLOW)
    if data is None:
        return []
    errors, name = [], CLOUDFLARE_WORKFLOW
    text = (WORKFLOWS / name).read_text()
    on = triggers_of(data)
    if set(on) - ALLOWED_TRIGGERS:
        errors.append(f"{name} may only run on push (feature/*, {RELEASE_BRANCH}) and pull_request, found {sorted(set(on) - ALLOWED_TRIGGERS)}")
    if re.search(r"pull_request_target", text):
        errors.append(f"{name} uses pull_request_target (would run untrusted code with secrets)")
    push = on.get("push") if isinstance(on.get("push"), dict) else {}
    branches = push.get("branches") or []
    if "push" in on and (not branches or any(not (str(b).startswith("feature/") or b == RELEASE_BRANCH) for b in branches)
                         or push.get("branches-ignore") or push.get("tags") or push.get("tags-ignore")):
        errors.append(f"{name}: push must be limited to feature/* branches and {RELEASE_BRANCH} (no tags, no branches-ignore)")
    if data.get("permissions") != {"contents": "read"}:
        errors.append(f"{name} must declare top-level `permissions: {{contents: read}}`")
    for job_name, job in (data.get("jobs") or {}).items():
        job_text = yaml.safe_dump(job)
        condition = _norm(job.get("if"))
        if PROTECTED_BRANCH.search(condition):
            errors.append(f"{name}:{job_name} condition refers to a protected branch (main/QA/dev)")
        secrets = set(SECRET_REF.findall(job_text)) - {"GITHUB_TOKEN"}
        if re.search(r"deploy-pages|upload-pages-artifact|configure-pages", job_text):
            errors.append(f"{name}:{job_name} must not publish to GitHub Pages")
        environment = job.get("environment")
        env_name = environment.get("name") if isinstance(environment, dict) else environment
        if "pages deploy" not in job_text and not secrets and env_name is None:
            continue
        if condition != DEPLOY_CONDITION:
            errors.append(f"{name}:{job_name} deploys or uses secrets but is not limited to `if: {DEPLOY_CONDITION}`")
        if env_name != "cloudflare-demo":
            errors.append(f"{name}:{job_name} must run in the protected 'cloudflare-demo' environment")
        if secrets - CLOUDFLARE_SECRETS:
            errors.append(f"{name}:{job_name} references unexpected secrets {sorted(secrets - CLOUDFLARE_SECRETS)}")
        if job.get("permissions") not in (None, {"contents": "read"}):
            errors.append(f"{name}:{job_name} must not widen permissions beyond contents: read")
    return errors


if __name__ == "__main__":
    problems = check()
    for e in problems:
        print("FAIL:", e)
    if not problems:
        print("workflow trigger/credential checks passed")
    sys.exit(1 if problems else 0)
