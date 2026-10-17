#!/usr/bin/env bash
# Local, offline verification of the demo edge BEFORE any tunnel exists:
#   docker compose up -d && deploy/tunnel/demo-up.sh --edge-only && deploy/tunnel/verify-edge.sh
# Probes portal-edge from a throwaway container on the student-portal-edge network (no host port, no Internet), then
# checks the services' access logs for the client address. Read-only: only unauthenticated GETs and requests that are
# refused before any handler runs; no login, no database write. The edge key is passed by name and never printed.
set -euo pipefail
cd "$(dirname "$0")"
EDGE_KEY=$(grep -m1 '^EDGE_KEY=' edge.env | cut -d= -f2-)
export EDGE_KEY
docker container inspect portal-edge >/dev/null 2>&1 || { echo "portal-edge is not running (demo-up.sh --edge-only)" >&2; exit 1; }

edge_status=0
# -i is required: without it docker does not attach stdin and python would run an empty script (and "pass").
docker run -i --rm --network student-portal-edge -e EDGE_KEY --entrypoint python student-authentication-service:latest - <<'PY' || edge_status=1
import os, urllib.error, urllib.request
EDGE, KEY = "http://portal-edge", os.environ["EDGE_KEY"]
results = []

def call(method, path, key=KEY, body=None, headers=None):
    h = dict(headers or {})
    if key is not None:
        h["X-Edge-Key"] = key
    req = urllib.request.Request(EDGE + path, data=body, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()

def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {detail}"))

s, _, _ = call("GET", "/student/auth/me", key=None); check("no edge key -> 404", s == 404, s)
s, _, _ = call("GET", "/student/auth/me", key="x" * 64); check("wrong edge key -> 404", s == 404, s)
s, h, _ = call("GET", "/student/auth/me"); check("Student auth reachable (401 without a session)", s == 401, s)
check("no backend Server header leaks", "uvicorn" not in str(h).lower(), h.get("server"))
s, _, _ = call("GET", "/institute/api/v1/auth/me"); check("Institute API reachable through the gateway (401)", s == 401, s)
for m, p in [("GET", "/student/internal/v1/students"), ("GET", "/student/docs"), ("GET", "/student/openapi.json"),
             ("GET", "/student/health"), ("GET", "/student/ready"), ("GET", "/institute/health"),
             ("GET", "/institute/internal/v1/introspect"), ("GET", "/institute/api/v1/docs"), ("GET", "/docs"), ("GET", "/"),
             ("POST", "/student/tests/t1/audiences"), ("POST", "/student/notifications"), ("GET", "/student/auth/login")]:
    s, _, b = call(m, p, body=b"{}" if m == "POST" else None, headers={"Content-Type": "application/json"})
    check(f"{m} {p} -> 404", s == 404, s)
s, _, b = call("POST", "/student/auth/login", body=b"x" * (1536 * 1024)); check("Student body > 1 MB -> 413", s == 413, s)
s, _, _ = call("POST", "/institute/api/v1/branches/b1/subjects/s1/papers/upload", body=b"x" * (2560 * 1024))
check("Institute 2.5 MB upload passes the edge and gateway (refused later by auth, not by size)", s not in (404, 413, 502, 503), s)
s, _, _ = call("POST", "/institute/api/v1/branches/b1/subjects/s1/papers/upload", body=b"x" * (3584 * 1024))
check("Institute body > 3 MB -> 413", s == 413, s)
call("GET", "/student/auth/me", headers={"X-Client-IP": "203.0.113.10", "X-Forwarded-For": "6.6.6.6"})
call("GET", "/institute/api/v1/auth/me", headers={"X-Client-IP": "203.0.113.11", "X-Forwarded-For": "6.6.6.6"})
call("GET", "/student/auth/me", headers={"X-Client-IP": "not-an-ip"})
raise SystemExit(0 if all(results) else 1)
PY

# Spoof attempts straight at the published ports (these must keep the real peer address).
curl -s -o /dev/null -H "X-Forwarded-For: 6.6.6.7" http://localhost:8002/auth/me || true
curl -s -o /dev/null -H "X-Forwarded-For: 6.6.6.8" http://localhost:8010/api/v1/auth/me || true
sleep 1
auth_log=$(docker logs --since 2m student-authentication-service 2>&1)
identity_log=$(docker logs --since 2m institute-identity-service 2>&1)
fail=0
chk() { if eval "$2"; then echo "PASS $1"; else echo "FAIL $1"; fail=1; fi; }
chk "Student auth sees the real client IP from the edge" 'grep -q "203.0.113.10" <<<"$auth_log"'
chk "Institute identity sees the real client IP through edge + gateway" 'grep -q "203.0.113.11" <<<"$identity_log"'
chk "client-supplied X-Forwarded-For via the edge is ignored" '! grep -q "6.6.6.6" <<<"$auth_log$identity_log"'
chk "X-Forwarded-For sent to the published Student port is ignored" '! grep -q "6.6.6.7" <<<"$auth_log"'
chk "X-Forwarded-For sent to the published gateway port is ignored" '! grep -q "6.6.6.8" <<<"$identity_log"'
chk "a malformed X-Client-IP never becomes the client address" '! grep -q "not-an-ip" <<<"$auth_log"'
[ "$edge_status" = 0 ] && [ "$fail" = 0 ] && echo "edge verification passed" || { echo "edge verification FAILED"; exit 1; }
