#!/usr/bin/env python3
"""Black-box flow against the running gateway (stdlib only). Usage: institute_e2e.py BASE_URL IDENTITY_CONTAINER STAFF_CODE PASSWORD"""
import http.cookiejar
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

base, container, code, password = sys.argv[1:5]
jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
ORIGIN = {"Origin": "http://localhost:8081"}
csrf = ""
results = []


def call(method, path, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {**ORIGIN, **({"Content-Type": "application/json"} if data else {}), **({"X-CSRF-Token": csrf} if csrf else {}), **(headers or {})}
    req = urllib.request.Request(base + path, data=data, method=method, headers=h)
    try:
        with opener.open(req, timeout=20) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw.decode()[:200]


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {detail}"))


s, b = call("GET", "/health")
check("gateway health", s == 200, (s, b))
s, b = call("POST", "/internal/v1/introspect", {})
check("internal APIs are not reachable through the gateway", s == 404, (s, b))
s, b = call("GET", "/api/v1/providers")
check("anonymous request is refused (identity introspection path)", s == 401, (s, b))

s, b = call("POST", "/api/v1/auth/login", {"staff_code": code, "password": password})
check("login asks for MFA", s == 200 and b.get("status") == "MFA_REQUIRED", (s, b))
time.sleep(1)
logs = subprocess.run(["docker", "logs", container], capture_output=True, text=True).stderr
otps = re.findall(r"code is (\d{6})", logs)
check("OTP email reached the (console) mail sender", bool(otps), logs[-300:])
s, b = call("POST", "/api/v1/auth/mfa/verify", {"challenge_id": b["challenge_id"], "otp": otps[-1]})
check("MFA verification creates a session", s == 200 and "me" in b, (s, b))
csrf = b["me"]["csrf_token"]

s, b = call("GET", "/api/v1/auth/me")
check("session works through the gateway", s == 200 and b["primary_role"] == "SUPER_ADMIN", (s, b))
s, b = call("POST", "/api/v1/providers", {"name": "E2E Institute", "email": "e2e-institute@example.com"})
check("organization service creates a provider (auth via identity)", s == 201, (s, b))
pid = b["provider_id"] if s == 201 else None
s, b = call("POST", f"/api/v1/providers/{pid}/branches", {"name": "Main Branch"})
check("organization service creates a branch", s == 201, (s, b))
bid = b["branch_id"] if s == 201 else None
s, b = call("POST", "/api/v1/subjects", {"code": "PHY", "name": "Physics"})
check("academics service creates a subject", s == 201, (s, b))
s, b = call("GET", "/api/v1/dashboard")
check("operations service dashboard", s == 200, (s, b))
s, b = call("GET", f"/api/v1/branches/{bid}/exam-plans")
check("exam service answers (empty list)", s == 200 and b["total"] == 0, (s, b))

deadline, found = time.time() + 30, False
while time.time() < deadline and not found:
    s, b = call("GET", "/api/v1/audit-logs?action=provider.create")
    found = s == 200 and any(x["action"] == "provider.create" for x in b["items"])
    if not found:
        time.sleep(2)
check("audit event written by the organization service reached the identity audit log", found, (s, b))

csv = "question,option_a,option_b,option_c,option_d,correct,marks,negative_marks\n2+2?,1,2,3,4,D,4,1\n"
boundary = "----e2e"
body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"title\"\r\n\r\nE2E paper\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"p.csv\"\r\nContent-Type: text/csv\r\n\r\n{csv}\r\n--{boundary}--\r\n").encode()
sid_ = call("GET", "/api/v1/subjects")[1]
subj = next((x["subject_id"] for x in sid_["items"] if x["code"] == "PHY"), None) if isinstance(sid_, dict) else None
call("PUT", f"/api/v1/providers/{pid}/subjects/{subj}", {})
req = urllib.request.Request(f"{base}/api/v1/branches/{bid}/subjects/{subj}/papers/upload", data=body, method="POST",
                             headers={**ORIGIN, "X-CSRF-Token": csrf, "Content-Type": f"multipart/form-data; boundary={boundary}"})
try:
    with opener.open(req, timeout=20) as r:
        s, b = r.status, json.loads(r.read())
except urllib.error.HTTPError as e:
    s, b = e.code, e.read().decode()[:200]
check("paper service imports a CSV paper", s == 201, (s, b))

s, b = call("GET", "/api/v1/system/health")
check("operations reports every service healthy (readiness probes across the network)", s == 200 and b["healthy"] and len(b["services"]) == 7, (s, b))

# preview -> correct -> new version (paper service)
if isinstance(b, dict):
    pass
s, papers = call("GET", f"/api/v1/branches/{bid}/papers")
pv = papers["items"][0]["latest_version"]["paper_version_id"] if s == 200 and papers["items"] else None
s, b = call("POST", f"/api/v1/paper-versions/{pv}/revisions", {"edits": [{"position": 1, "correct_option": "C"}], "reason": "Key typo found in review"})
check("a correction creates the next paper version", s == 201 and b["version_no"] == 2, (s, b))
s, b = call("POST", f"/api/v1/paper-versions/{pv}/revisions", {"edits": [{"position": 1, "correct_option": "Z"}], "reason": "Key typo found in review"})
check("an invalid correction is rejected", s == 422, (s, b))

# composite request (exam service) with an invited second provider; contribution shares through the paper service
s, b = call("POST", "/api/v1/providers", {"name": "E2E Partner", "email": "e2e-partner@example.com"})
partner = b["provider_id"] if s == 201 else None
call("PUT", f"/api/v1/providers/{partner}/subjects/{subj}", {})
s, b = call("POST", f"/api/v1/branches/{bid}/composite-requests", {"name": "E2E joint test", "slots": [{"subject_id": subj, "provider_id": partner}]},
            {"Idempotency-Key": "e2e-request-0001"})
check("exam service opens a composite request across providers", s == 201 and b["status"] == "OPEN", (s, b))

print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
