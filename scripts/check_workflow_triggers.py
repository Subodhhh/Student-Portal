#!/usr/bin/env python3
"""Guards the two-portal deployment boundary (no dependencies beyond PyYAML).

  * The Student Pages deployment must be path-filtered to the Student frontend: an Institute-only change must not
    trigger it, while every Student frontend change still must.
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


def push_paths(name: str):
    data = yaml.safe_load((WORKFLOWS / name).read_text())
    on = data.get("on", data.get(True))
    return (on.get("push") or {}).get("paths")


DEPLOY_STEP = re.compile(r"deploy-pages|upload-pages-artifact|configure-pages|docker (push|login)|kubectl|terraform|\bssh |\bscp ")
SECRET_REF = re.compile(r"\$\{\{\s*secrets\.(\w+)")
DB_VAR = re.compile(r"(?:DATABASE_URL|MIGRATION_DATABASE_URL|PGURL|PGBASE|TEST_DATABASE_URL)\s*[:=]\s*(\S+)")
SAFE_DB = re.compile(r"^(sqlite|postgresql(\+\w+)?://[^@/]*@(127\.0\.0\.1|localhost)|\$\{\{)")


def check() -> list:
    errors = []
    paths = push_paths("deploy-pages.yml")
    if not paths:
        errors.append("deploy-pages.yml has no push path filter: any push to dev redeploys the Student Portal")
    else:
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


if __name__ == "__main__":
    problems = check()
    for e in problems:
        print("FAIL:", e)
    if not problems:
        print("workflow trigger/credential checks passed")
    sys.exit(1 if problems else 0)
