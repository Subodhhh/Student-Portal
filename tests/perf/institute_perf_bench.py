"""Driver of tests/perf/perf_replica.sh (see there): the seven Institute services in-process, configured as deployed (one engine with
pool_pre_ping, peers over HTTP-in-process), against the latency-proxied replica. Records, for every API request, its server time, SQL
statements and connection checkouts (each checkout = one pre-ping round trip); serves a production build of the Institute portal and
runs tests/perf/institute_perf_browser.py, which times every page. Writes <work>/<label>.json and prints a summary."""
import contextvars
import json
import os
import re
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

DB, KEY, WORK, LABEL = os.environ["PERF_DB"], os.environ["PERF_KEY"], Path(os.environ["PERF_WORK"]), os.environ["PERF_LABEL"]
if not re.search(r"@127\.0\.0\.1:\d+/institute_perf_verify$", DB):
    sys.exit("refusing: disposable replica database only")
ROOT = Path(__file__).resolve().parents[2]
PORT, PW_PORT, PW_IMAGE = 18290, int(os.environ.get("PERF_PW_PORT", "3012")), "mcr.microsoft.com/playwright/python:v1.48.0-noble"
STU = {"STUDENT_SERVICE": 18202, "STUDENT_ENROLLMENT_SERVICE": 18204, "STUDENT_TEST_SERVICE": 18205, "STUDENT_ATTEMPT_SERVICE": 18206,
       "STUDENT_RESULT_SERVICE": 18207, "STUDENT_NOTIFICATION_SERVICE": 18208}
for name, port in STU.items():
    os.environ[f"{name}_URL"], os.environ[f"{name}_API_KEY"] = f"http://127.0.0.1:{port}", KEY
os.environ.update(APP_ENV="dev", DATABASE_URL=DB, REDIS_URL="redis://localhost:6379", CORS_ORIGINS="http://localhost:5176",
                  SESSION_COOKIE_SECURE="false", FRONTEND_BASE_URL="http://localhost:5176", INTERNAL_API_KEYS="perf-internal-key",
                  AUDIT_FORWARD_INLINE="true", AUDIT_FORWARD_INTERVAL_SECONDS="0")
for d in (ROOT / "institute-backend").glob("institute-*-service"):
    sys.path.insert(0, str(d))
sys.path.insert(0, str(ROOT / "institute-backend" / "institute-common"))
sys.path.insert(0, str(ROOT / "institute-backend" / "institute-integration-tests"))

import fakeredis  # noqa: E402
import httpx  # noqa: E402
import uvicorn  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import event, text  # noqa: E402

from institute_common.core import email, redis_store  # noqa: E402
from institute_common.core.passwords import hash_password  # noqa: E402
from institute_common.database import engine  # noqa: E402
from institute_common.internal_http import set_peer_client  # noqa: E402
from tests.gateway import SERVICES  # noqa: E402
from tests.gateway import app as gateway  # noqa: E402

PW = "Perf-" + os.urandom(6).hex()
STAFF = os.environ.get("PERF_STAFF", "STF000001").split()
redis_store.set_client(fakeredis.FakeRedis(decode_responses=True))


class OtpFile:
    """Sign-in verification codes go to a local file the browser script reads (never printed: the replica holds real addresses)."""
    def send(self, mail):
        m = re.search(r"\b(\d{6})\b", mail.body)
        if m:
            (WORK / "otp.txt").write_text(m.group(1))


email.set_sender(OtpFile())
for name, service_app in SERVICES.items():
    set_peer_client(name, TestClient(service_app, base_url=f"http://{name}"))

# ------------------------------------------------------------------ per-request accounting
current: contextvars.ContextVar[dict | None] = contextvars.ContextVar("perf_request", default=None)
records: list[dict] = []
lock = threading.Lock()


@event.listens_for(engine, "before_cursor_execute")
def _statement(*_a, **_k):
    r = current.get()
    if r is not None:
        r["queries"] += 1


@event.listens_for(engine, "checkout")
def _checkout(*_a, **_k):
    r = current.get()
    if r is not None:
        r["checkouts"] += 1


@event.listens_for(engine, "connect")
def _connect(*_a, **_k):
    r = current.get()
    if r is not None:
        r["connects"] += 1


async def measured(scope, receive, send):
    if scope["type"] != "http" or not scope["path"].startswith("/api/"):
        return await gateway(scope, receive, send)
    r = {"method": scope["method"], "path": scope["path"], "queries": 0, "checkouts": 0, "connects": 0, "start": time.time()}
    token = current.set(r)
    status = {}

    async def send_(msg):
        if msg["type"] == "http.response.start":
            status["code"] = msg["status"]
        await send(msg)
    try:
        await gateway(scope, receive, send_)
    finally:
        current.reset(token)
        r["ms"] = round((time.time() - r["start"]) * 1000)
        r["status"] = status.get("code")
        with lock:
            records.append(r)

# ------------------------------------------------------------------ replica-only passwords for the measured staff, latency sanity check
with engine.begin() as c:
    for code in STAFF:
        n = c.execute(text("update staff set password_hash = :h where staff_code = :c"),
                      {"h": hash_password(PW), "c": code}).rowcount
        assert n == 1, f"{code} not found in the replica"
with engine.connect() as c:
    c.execute(text("select 1"))
    t = time.perf_counter()
    for _ in range(5):
        c.execute(text("select 1"))
    rtt = (time.perf_counter() - t) / 5 * 1000
print(f"replica query round trip through the proxy: {rtt:.0f} ms")

# ------------------------------------------------------------------ production build of the Institute portal, served with the gateway
DIST = WORK / "dist"
build = subprocess.run(["npx", "vite", "build", "--outDir", str(DIST), "--emptyOutDir"], cwd=ROOT / "institute-frontend",
                       env={**os.environ, "VITE_INSTITUTE_API_URL": ""}, capture_output=True, text=True)
assert build.returncode == 0, build.stderr[-800:]
KINDS = {".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".html": "text/html", ".png": "image/png", ".ico": "image/x-icon"}


async def site(scope, receive, send):
    if scope["type"] != "http" or scope["path"].startswith("/api/"):
        return await measured(scope, receive, send)
    rel = scope["path"].lstrip("/")
    target = DIST / rel if rel and (DIST / rel).is_file() else DIST / "index.html"
    body = target.read_bytes()
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", KINDS.get(target.suffix, "application/octet-stream").encode()),
                                                                          (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


server = uvicorn.Server(uvicorn.Config(site, host="0.0.0.0", port=PORT, log_level="warning", lifespan="off", workers=1))
threading.Thread(target=server.run, daemon=True).start()
for _ in range(60):
    try:
        if httpx.get(f"http://127.0.0.1:{PORT}/login", timeout=2).status_code == 200:
            break
    except httpx.HTTPError:
        time.sleep(0.25)

# ------------------------------------------------------------------ Chromium (localhost:5176 inside the container -> this server)
PW_PYTHON = os.environ.get("PW_PYTHON", "python3")
pkgs = subprocess.run([PW_PYTHON, "-c", "import os, playwright; print(os.path.dirname(os.path.dirname(playwright.__file__)))"],
                      capture_output=True, text=True, check=True).stdout.strip()
subprocess.run(["docker", "rm", "-f", "perf-pw"], capture_output=True)
subprocess.run(["docker", "run", "-d", "--name", "perf-pw", "--add-host", "host.docker.internal:host-gateway", "-p", f"127.0.0.1:{PW_PORT}:3000",
                "-v", f"{pkgs}:/pwpkgs:ro", "-e", "PYTHONPATH=/pwpkgs", PW_IMAGE, "python3", "-m", "playwright", "run-server", "--port", "3000",
                "--host", "0.0.0.0"], check=True, capture_output=True)
for _ in range(60):
    try:
        if httpx.get(f"http://127.0.0.1:{PW_PORT}/", timeout=2).status_code == 200:
            break
    except httpx.HTTPError:
        time.sleep(0.5)
FWD = ("import asyncio\nasync def pipe(r, w):\n    try:\n        while data := await r.read(65536):\n            w.write(data); await w.drain()\n"
       "    finally:\n        w.close()\nasync def h(r, w):\n    ur, uw = await asyncio.open_connection('host.docker.internal', %d)\n"
       "    await asyncio.gather(pipe(r, uw), pipe(ur, w), return_exceptions=True)\n"
       "async def main():\n    s = await asyncio.start_server(h, '127.0.0.1', 5176)\n    await s.serve_forever()\nasyncio.run(main())\n") % PORT
subprocess.run(["docker", "exec", "-d", "perf-pw", "python3", "-c", FWD], check=True)
time.sleep(2)

params = {"otp_file": str(WORK / "otp.txt"), "ui": "http://localhost:5176", "ws": f"ws://127.0.0.1:{PW_PORT}/", "password": PW, "staff": STAFF, "out": str(WORK / "browser.json"),
          "runs": int(os.environ.get("PERF_RUNS", "3"))}
(WORK / "params.json").write_text(json.dumps(params))
browser = subprocess.run([PW_PYTHON, str(ROOT / "tests" / "perf" / "institute_perf_browser.py"), str(WORK / "params.json")])
assert browser.returncode == 0, "browser script failed"

# ------------------------------------------------------------------ correlate: every API request inside a navigation window belongs to it
navs = json.loads((WORK / "browser.json").read_text())
for n in navs:
    mine = [r for r in records if n["start"] <= r["start"] <= n["end"]]
    n["server"] = [{k: r[k] for k in ("method", "path", "status", "ms", "queries", "checkouts", "connects", "start")} for r in mine]
    n["queries"] = sum(r["queries"] for r in mine)
    n["checkouts"] = sum(r["checkouts"] for r in mine)
    n["connects"] = sum(r["connects"] for r in mine)
out = Path(os.environ.get("PERF_OUT_DIR", str(ROOT.parent))) / f"perf-{LABEL}.json"
out.write_text(json.dumps({"label": LABEL, "rtt_ms": round(rtt), "navigations": navs}, indent=1))

print(f"\n== {LABEL}: per page, median (max) of {params['runs']} runs; API requests, SQL statements, connection checkouts per load")
by: dict = {}
for n in navs:
    by.setdefault((n["staff"], n["kind"], n["page"]), []).append(n)
for (staff, kind, page), ns in by.items():
    ms = [n["ms"] for n in ns]
    print(f"  {staff:9} {kind:8} {page:42} {statistics.median(ms):7.0f} ms ({max(ms):6.0f}) | api {statistics.median([len(n['server']) for n in ns]):4.0f}"
          f" | sql {statistics.median([n['queries'] for n in ns]):5.0f} | checkouts {statistics.median([n['checkouts'] for n in ns]):4.0f}")
slow = sorted((r for r in records), key=lambda r: -r["ms"])[:12]
print("\n  slowest API requests:")
for r in slow:
    print(f"    {r['ms']:6} ms  sql {r['queries']:4}  checkouts {r['checkouts']:3}  {r['method']} {re.sub(r'[0-9a-f-]{36}', '{id}', r['path'])}")
print(f"\nresults: {out}")
server.should_exit = True
