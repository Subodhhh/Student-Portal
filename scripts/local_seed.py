"""First-run data for the LOCAL instance (scripts/local-stack.sh up): only through the Institute API and the documented operator
command, never by writing to the database. Creates a Super Admin, a provider with one branch and batch, the subjects Mathematics,
Physics and Chemistry with their recognition categories, and a teacher of all three; writes the logins to
deploy/local/logins.local.env (git-ignored) and the subject categories to deploy/local/institute.local.env.

Refuses to run against anything but this machine's local gateway."""
import json
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path

import httpx

GATEWAY, ORIGIN = "http://127.0.0.1:18010", {"Origin": "http://localhost:18081"}
API = GATEWAY + "/api/v1"
LOCAL = Path("deploy/local")
IDENTITY = "local-institute-identity-service"
if "127.0.0.1" not in GATEWAY:
    sys.exit("local gateway only")


def password() -> str:
    return "Local-" + secrets.token_urlsafe(12) + "-9a"            # meets the password policy (length, classes)


def mail(pattern: str, after: int) -> str:
    """The newest match in the identity service's console mail (the local email backend prints mail to stderr)."""
    for _ in range(60):
        log = subprocess.run(["docker", "logs", IDENTITY], capture_output=True, text=True, check=False).stderr
        found = re.findall(pattern, log[after:])
        if found:
            return found[-1]
        time.sleep(0.5)
    sys.exit(f"no mail matching {pattern!r} in {IDENTITY}'s log")


def log_size() -> int:
    return len(subprocess.run(["docker", "logs", IDENTITY], capture_output=True, text=True, check=False).stderr)


def login(c: httpx.Client, code: str, pw: str) -> dict:
    before = log_size()
    body = c.post(f"{API}/auth/login", json={"staff_code": code, "password": pw}, headers=ORIGIN).raise_for_status().json()
    if body.get("status") == "MFA_REQUIRED":
        otp = mail(r"code is (\d{6})", before)
        body = c.post(f"{API}/auth/mfa/verify", json={"challenge_id": body["challenge_id"], "otp": otp}, headers=ORIGIN).raise_for_status().json()
    return {**ORIGIN, "X-CSRF-Token": body["me"]["csrf_token"]}


def ok(r: httpx.Response) -> dict:
    if r.status_code >= 400:
        sys.exit(f"{r.request.method} {r.request.url.path}: {r.status_code} {r.text[:300]}")
    return r.json() if r.content else {}


admin_pw, teacher_pw = password(), password()
admin_email, teacher_email = "admin@portal-local.example.com", "teacher@portal-local.example.com"
r = subprocess.run(["docker", "exec", "-e", f"INSTITUTE_ADMIN_PASSWORD={admin_pw}", IDENTITY, "python", "-m", "inst_identity.cli",
                    "create-super-admin", admin_email, "Local Super Admin"], capture_output=True, text=True, check=False)
if r.returncode:
    sys.exit("create-super-admin failed: " + (r.stderr or r.stdout)[-300:])
admin_code = r.stdout.strip().split()[-1]

with httpx.Client(timeout=30) as c:
    h = login(c, admin_code, admin_pw)
    P = ok(c.post(f"{API}/providers", json={"name": "Local Demo Academy", "email": "office@portal-local.example.com"}, headers=h))["provider_id"]
    B = ok(c.post(f"{API}/providers/{P}/branches", json={"name": "Main", "city": "Pune", "state": "MH"}, headers=h))["branch_id"]
    subjects = {name: ok(c.post(f"{API}/subjects", json={"code": code, "name": name}, headers=h))["subject_id"]
                for code, name in (("MATHS", "Mathematics"), ("PHYS", "Physics"), ("CHEM", "Chemistry"))}
    for s in subjects.values():
        ok(c.put(f"{API}/providers/{P}/subjects/{s}", json={}, headers=h))
    batch = ok(c.post(f"{API}/branches/{B}/batches", json={"name": "JEE 2027"}, headers=h))["batch_id"]
    before = log_size()
    teacher = ok(c.post(f"{API}/staff", json={"full_name": "Local Teacher", "email": teacher_email,
                                               "assignment": {"role": "TEACHER", "provider_id": P}}, headers=h))
    token = mail(r"token=([A-Za-z0-9_\-]+)", before)
    ok(c.post(f"{API}/auth/verify-email", json={"token": token, "new_password": teacher_pw}, headers=ORIGIN))
    h = login(c, admin_code, admin_pw)
    for s in subjects.values():
        ok(c.post(f"{API}/teaching", json={"staff_id": teacher["staff_id"], "branch_id": B, "subject_id": s, "batch_id": batch}, headers=h))

categories = {subjects["Mathematics"]: "MATHEMATICS", subjects["Physics"]: "PHYSICS", subjects["Chemistry"]: "CHEMISTRY"}
env = LOCAL / "institute.local.env"
env.write_text(re.sub(r"RECOGNITION_SUBJECT_CATEGORIES=.*", "RECOGNITION_SUBJECT_CATEGORIES=" + json.dumps(categories), env.read_text()))
(LOCAL / "logins.local.env").write_text(
    "# LOCAL instance only (http://localhost:18081). Teacher: imports papers (Question Bank & Papers > Import Processing), no email code.\n"
    f"TEACHER_STAFF_ID={teacher['staff_code']}\nTEACHER_PASSWORD={teacher_pw}\n"
    "# Super Admin: asks for an email code at every sign-in; print it with: scripts/local-stack.sh otp\n"
    f"ADMIN_STAFF_ID={admin_code}\nADMIN_PASSWORD={admin_pw}\n")
print(f"created: Super Admin {admin_code}, teacher {teacher['staff_code']}, provider 'Local Demo Academy' (branch Main, batch JEE 2027), "
      "subjects Mathematics/Physics/Chemistry; logins in deploy/local/logins.local.env")
