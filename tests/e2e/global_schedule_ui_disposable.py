"""Global Test schedule + photo papers in a REAL browser, on DISPOSABLE infrastructure
(run: tests/e2e/pipeline_disposable.sh global_schedule_ui_disposable.py).

Like test_types_ui_disposable.py: the Institute services run in-process behind the in-process gateway and serve a production build of
the Institute portal; the six real Student services run as processes on a throwaway PostgreSQL, and a production build of the STUDENT
portal is served on a second origin that proxies to them. Photo OCR is the real Tesseract code of this revision, run inside the
institute-paper-service image (the host has no Tesseract). Chromium (Playwright run-server container) drives both portals:
tests/e2e/global_schedule_ui_browser.py (phase "staff", then phase "student"). This driver creates the starting data through the
APIs, simulates the clock reaching the first date's paper deadline (the only time shift), runs the worker's generation, and checks in
the database and through the Student services what the browser did.

Photos are SYNTHETIC phone-camera-like images (tests/e2e/phone_photos.py), not photos taken with a real phone.

Needs: Docker, the images mcr.microsoft.com/playwright/python:v1.48.0-noble and institute-paper-service:latest, npm, and a Python with
playwright==1.48 for the browser script (PW_PYTHON, default: python3).
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

DB, KEY, AUTH_LOG = os.environ["PIPELINE_DB"], os.environ["PIPELINE_KEY"], Path(os.environ["PIPELINE_AUTH_LOG"])
if not re.search(r"@127\.0\.0\.1:\d+/pipeline_verify$", DB):
    sys.exit("refusing: disposable pipeline database only")
ROOT = Path(__file__).resolve().parents[2]
WORK = AUTH_LOG.parent
PORT, SPORT, PW_PORT, PW_IMAGE = 18190, 18191, int(os.environ.get("UI_PW_PORT", "3011")), "mcr.microsoft.com/playwright/python:v1.48.0-noble"
STU = {"auth": "http://127.0.0.1:18102", "enroll": "http://127.0.0.1:18104", "test": "http://127.0.0.1:18105",
       "attempt": "http://127.0.0.1:18106", "result": "http://127.0.0.1:18107", "notify": "http://127.0.0.1:18108"}
for name, url in (("STUDENT_SERVICE", STU["auth"]), ("STUDENT_ENROLLMENT_SERVICE", STU["enroll"]), ("STUDENT_TEST_SERVICE", STU["test"]),
                  ("STUDENT_ATTEMPT_SERVICE", STU["attempt"]), ("STUDENT_RESULT_SERVICE", STU["result"]), ("STUDENT_NOTIFICATION_SERVICE", STU["notify"])):
    os.environ[f"{name}_URL"], os.environ[f"{name}_API_KEY"] = url, KEY

sys.path.insert(0, str(ROOT / "institute-backend" / "institute-integration-tests"))
sys.path.insert(0, str(ROOT / "tests" / "e2e"))
import fakeredis
import httpx
import tests.conftest  # noqa: F401  (service paths + test settings; the database is overridden below)
import uvicorn
from fastapi.testclient import TestClient
from inst_enrollment.services import reference_sync
from inst_exam.services import global_generation, notify, outbox
from inst_paper.services import extraction
from institute_common.core import email, redis_store
from institute_common.core.passwords import hash_password
from institute_common.database import get_db
from institute_common.internal_http import set_peer_client
from institute_common.models.staff import Staff, StaffRoleAssignment
from institute_common.ownership import attach_guard
from phone_photos import blank_photo, phone_photo
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from tests.gateway import SERVICES
from tests.gateway import app as gateway

ORIGIN, API, PW = {"Origin": "http://localhost:5174"}, "/api/v1", "GlobalSched-" + uuid4().hex
ACAD = {"class_level": "11th", "academic_year": "2026-2027"}   # every enrollment needs its Class / Academic Year
results: list[tuple[str, bool]] = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f" - {str(detail)[:600]}"), flush=True)
    if not ok:
        summary()


def summary():
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} driver checks passed" + (f"; FAILED: {failed}" if failed else ""), flush=True)
    subprocess.run(["docker", "rm", "-f", "gs-pw", "gs-ocr"], capture_output=True)
    os._exit(1 if failed else 0)


engine = create_engine(DB)
factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def sql(q, **a):
    with engine.connect() as conn:
        return conn.execute(text(q), a).all()


def execute(q, **a):
    with engine.begin() as conn:
        conn.execute(text(q), a)


class FileSender(email.MemoryEmailSender):
    """Keeps mail in memory (for this driver) and appends it to a file the browser script reads (verification codes)."""

    def send(self, mail):
        super().send(mail)
        with open(WORK / "mail.log", "a") as f:
            f.write(json.dumps({"to": mail.to, "body": mail.body}) + "\n")


sender = FileSender()
email.set_sender(sender)
redis_store.set_client(fakeredis.FakeRedis(decode_responses=True))


def override():
    db = factory()
    attach_guard(db)
    try:
        yield db
    finally:
        db.close()


reference_sync.new_session = factory
for name, service_app in SERVICES.items():
    service_app.dependency_overrides[get_db] = override
    set_peer_client(name, TestClient(service_app, base_url=f"http://{name}"))


# ------------------------------------------------------------------ real Tesseract (this revision's code) inside the paper-service image
OCR_SNIPPET = ("import sys, json\nfrom inst_paper.services.ocr_tesseract import TesseractBackend\n"
               "from inst_paper.services.extraction import OcrUnreadable\n"
               "data = sys.stdin.buffer.read()\nrotate = int(sys.argv[3]) if sys.argv[3] else None\n"
               "try:\n    print(json.dumps({'lines': TesseractBackend().read_lines(data, sys.argv[1], sys.argv[2], rotate)}))\n"
               "except OcrUnreadable as exc:\n    print(json.dumps({'unreadable': str(exc)}))\n")
subprocess.run(["docker", "rm", "-f", "gs-ocr"], capture_output=True)
subprocess.run(["docker", "run", "-d", "--name", "gs-ocr", "-w", "/", "-v", f"{ROOT / 'institute-backend'}:/src:ro", "-e", "DATABASE_URL=sqlite://",
                "-e", "PYTHONPATH=/src/institute-paper-service:/src/institute-common", "institute-paper-service:latest", "sleep", "infinity"],
               check=True, capture_output=True)


class DockerTesseract:
    """The paper service's OCR backend, executed by the real TesseractBackend in the paper-service image."""

    def read_lines(self, data: bytes, mime: str, purpose: str = "page", rotate: int | None = None) -> list[list]:
        from inst_paper.services.extraction import OcrUnreadable
        r = subprocess.run(["docker", "exec", "-i", "gs-ocr", "python", "-c", OCR_SNIPPET, mime, purpose, "" if rotate is None else str(rotate)],
                           input=data, capture_output=True, timeout=120)
        if r.returncode:
            raise RuntimeError(r.stderr.decode()[-300:])
        out = json.loads(r.stdout)
        if "unreadable" in out:
            raise OcrUnreadable(out["unreadable"])
        return out["lines"]

    def extract_text(self, data: bytes, mime: str) -> str:
        return "\n".join(t for t, *_ in self.read_lines(data, mime))


extraction.set_ocr_backend(DockerTesseract())

with factory() as db:
    sa = Staff(staff_code="STF000001", email="owner@example.com", full_name="Platform Owner", password_hash=hash_password(PW), status="ACTIVE",
               email_verified_at=datetime.now(UTC))
    db.add(sa)
    db.flush()
    db.add(StaffRoleAssignment(staff_id=sa.staff_id, role="SUPER_ADMIN", status="ACTIVE"))
    db.commit()


def login(c, code):
    body = c.post(f"{API}/auth/login", json={"staff_code": code, "password": PW}, headers=ORIGIN).json()
    if body.get("status") == "MFA_REQUIRED":
        otp = sender.outbox[-1].body.split("code is ")[1][:6]
        body = c.post(f"{API}/auth/mfa/verify", json={"challenge_id": body["challenge_id"], "otp": otp}, headers=ORIGIN).json()
    return {**ORIGIN, "X-CSRF-Token": body["me"]["csrf_token"]}


def invite(c, h, name, mail, role, provider=None, branch=None):
    assignment = {"role": role, **({"provider_id": provider} if provider else {}), **({"branch_id": branch} if branch else {})}
    r = c.post(f"{API}/staff", json={"full_name": name, "email": mail, "assignment": assignment}, headers=h)
    assert r.status_code == 201, r.text
    token = re.search(r"token=([A-Za-z0-9_\-]+)", sender.outbox[-1].body).group(1)
    c.post(f"{API}/auth/verify-email", json={"token": token, "new_password": PW}, headers=ORIGIN)
    return r.json()


def csv_paper(c, h, branch, subject, prefix, n, title, approve=True):
    rows = [f"{prefix}{i:03d},{title} question {i}?,A{i},B{i},C{i},D{i},4,1," for i in range(1, n + 1)]
    paper = "question_id,question,option_a,option_b,option_c,option_d,marks,negative_marks,explanation\n" + "\n".join(rows) + "\n"
    key = "question_id,correct_option,explanation\n" + "\n".join(f"{prefix}{i:03d},{'ABCD'[(i - 1) % 4]},Reason {i}" for i in range(1, n + 1)) + "\n"
    files = {"file": ("paper.csv", paper.encode(), "text/csv"), "answer_key": ("key.csv", key.encode(), "text/csv")}
    r = c.post(f"{API}/branches/{branch}/subjects/{subject}/papers/upload", files=files, data={"title": title}, headers=h)
    assert r.status_code == 201, r.text
    pv = r.json()["paper_version_id"]
    if approve:
        assert c.post(f"{API}/paper-versions/{pv}/approve", json={"note": "ok"}, headers=h).status_code == 200
        assert c.post(f"{API}/paper-versions/{pv}/publish", json={"note": None}, headers=h).status_code == 200
    return pv


USED: set[str] = set()


def set_student_password(email_):
    """Gives a student a password through the real Student auth service (forgot -> reset link from its log)."""
    s = httpx.Client(timeout=20, headers={"Origin": "http://localhost"})
    s.post(STU["auth"] + "/auth/forgot-password", json={"email": email_})
    tok = None
    for _ in range(30):
        found = [t for t in re.findall(r"reset-password\?token=([A-Za-z0-9_\-]+)", AUTH_LOG.read_text()) if t not in USED]
        if found:
            tok = found[-1]
            break
        time.sleep(0.5)
    USED.add(tok)
    return s.post(STU["auth"] + "/auth/reset-password", json={"token": tok, "new_password": PW}).status_code in (200, 204)


# ------------------------------------------------------------------ starting data, through the APIs
with TestClient(gateway, base_url="http://localhost") as c:
    print("set-up through the Institute API")
    h = login(c, "STF000001")
    P = c.post(f"{API}/providers", json={"name": "North Academy", "email": "office@north.example.com"}, headers=h).json()["provider_id"]
    P2 = c.post(f"{API}/providers", json={"name": "Ravi Tutorials", "email": "ravi@tutor.example.com"}, headers=h).json()["provider_id"]
    B1 = c.post(f"{API}/providers/{P}/branches", json={"name": "Pune", "city": "Pune", "state": "MH"}, headers=h).json()["branch_id"]
    B2 = c.post(f"{API}/providers/{P}/branches", json={"name": "Mumbai", "city": "Mumbai", "state": "MH"}, headers=h).json()["branch_id"]
    B3 = c.post(f"{API}/providers/{P2}/branches", json={"name": "Home classes", "city": "Nashik", "state": "MH"}, headers=h).json()["branch_id"]
    PHY = c.post(f"{API}/subjects", json={"code": "PHY", "name": "Physics"}, headers=h).json()["subject_id"]
    CHE = c.post(f"{API}/subjects", json={"code": "CHE", "name": "Chemistry"}, headers=h).json()["subject_id"]
    for prov in (P, P2):
        for sj in (PHY, CHE):
            c.put(f"{API}/providers/{prov}/subjects/{sj}", json={}, headers=h)
    A = c.post(f"{API}/branches/{B1}/batches", json={"name": "NEET A"}, headers=h).json()["batch_id"]
    BB = c.post(f"{API}/branches/{B1}/batches", json={"name": "NEET B"}, headers=h).json()["batch_id"]
    C = c.post(f"{API}/branches/{B2}/batches", json={"name": "JEE C"}, headers=h).json()["batch_id"]
    ADM = invite(c, h, "Jay Junior", "jay@platform.example.com", "ADMIN")
    PA = invite(c, h, "Priya Admin", "priya@north.example.com", "PROVIDER_ADMIN", P)
    BA = invite(c, h, "Bharat Branch", "bharat@north.example.com", "BRANCH_ADMIN", P, B1)
    TA = invite(c, h, "Tara Physics", "tara@north.example.com", "TEACHER", P)
    TB = invite(c, h, "Tom Physics", "tom@north.example.com", "TEACHER", P)
    TU = invite(c, h, "Ravi Tutor", "ravi.tutor@tutor.example.com", "PROVIDER_ADMIN", P2)
    h = login(c, "STF000001")
    for staff, batch in ((TA, A), (TB, BB)):                                 # two Physics teachers of the same branch, one batch each
        r = c.post(f"{API}/teaching", json={"staff_id": staff["staff_id"], "branch_id": B1, "subject_id": PHY, "batch_id": batch}, headers=h)
        assert r.status_code in (200, 201), r.text

    def enroll(branch, first, mail, batch_ids, sid=None):
        body = {"student_id": sid, "batch_ids": batch_ids} if sid else {"new_student": {"first_name": first, "last_name": "Test", "email": mail}, "batch_ids": batch_ids}
        r = c.post(f"{API}/branches/{branch}/enrollments", json={**body, "academic": ACAD}, headers=h)
        assert r.status_code == 201, r.text
        return r.json()["student_id"]
    S1 = enroll(B1, "Asha", "asha@family.example.com", [A])            # Pune NEET A, also with Ravi Tutorials
    S2 = enroll(B1, "Bilal", "bilal@family.example.com", [A])          # Pune NEET A
    S4 = enroll(B1, "Dev", "dev@family.example.com", [BB])             # Pune NEET B
    S3 = enroll(B2, "Chitra", "chitra@family.example.com", [C])        # Mumbai JEE C: no Physics paper will exist for her
    enroll(B3, "", "", [], sid=S1)                                      # the tutor has no batches: students individually
    S5 = enroll(B3, "Esha", "esha@family.example.com", [])
    with factory() as db:
        reference_sync.run_once(db, full=True)

    PV_TOM = csv_paper(c, login(c, TB["staff_code"]), B1, PHY, "T", 4, "Tom Physics Set")
    PV_BA_CHE = csv_paper(c, login(c, BA["staff_code"]), B1, CHE, "B", 3, "Pune Chemistry Set")
    PV_PA_CHE = csv_paper(c, login(c, PA["staff_code"]), B2, CHE, "M", 3, "Mumbai Chemistry Draft", approve=False)   # never approved
    PV_TUTOR = csv_paper(c, login(c, TU["staff_code"]), B3, PHY, "R", 2, "Ravi Physics Set")
    check("set-up: platform Admin, 2 providers (one a tutor without batches), Branch Admin, 2 Physics teachers, 5 students, 4 papers", True)

# ------------------------------------------------------------------ the two portals on their own origins
DIST, SDIST = WORK / "dist", WORK / "sdist"
build = subprocess.run(["npx", "vite", "build", "--outDir", str(DIST), "--emptyOutDir"], cwd=ROOT / "institute-frontend",
                       env={**os.environ, "VITE_INSTITUTE_API_URL": ""}, capture_output=True, text=True)
check("production build of the Institute portal (same-origin API)", build.returncode == 0 and (DIST / "index.html").exists(), build.stderr[-500:])
SVC = {"auth": STU["auth"], "enrollment": STU["enroll"], "test": STU["test"], "attempt": STU["attempt"], "result": STU["result"],
       "notification": STU["notify"], "profile": STU["auth"], "ranking": STU["result"]}
senv = {**os.environ, **{f"VITE_{k.upper()}_API_URL": f"/s/{k}" for k in SVC}}
sbuild = subprocess.run(["npx", "vite", "build", "--outDir", str(SDIST), "--emptyOutDir"], cwd=ROOT / "student-frontend", env=senv, capture_output=True, text=True)
check("production build of the Student portal (proxied same-origin APIs)", sbuild.returncode == 0 and (SDIST / "index.html").exists(), sbuild.stderr[-500:])

KINDS = {".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".html": "text/html", ".png": "image/png", ".ico": "image/x-icon"}


async def static(dist, scope, send):
    rel = scope["path"].lstrip("/")
    target = dist / rel if rel and (dist / rel).is_file() else dist / "index.html"
    body = target.read_bytes()
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", KINDS.get(target.suffix, "application/octet-stream").encode()),
                                                                          (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


async def institute_site(scope, receive, send):
    """The Institute portal on one origin, as Cloudflare Pages serves it: /api/* to the gateway, everything else the SPA."""
    if scope["type"] != "http" or scope["path"].startswith("/api/"):
        return await gateway(scope, receive, send)
    await static(DIST, scope, send)


proxy_client = httpx.AsyncClient(timeout=60)


async def student_site(scope, receive, send):
    """The Student portal: /s/<service>/... proxied to that Student service (Origin as the services expect), everything else the SPA."""
    if scope["type"] != "http":
        return
    m = re.match(r"^/s/([a-z]+)(/.*)?$", scope["path"])
    if not m:
        return await static(SDIST, scope, send)
    body = b""
    while True:
        msg = await receive()
        body += msg.get("body", b"")
        if not msg.get("more_body"):
            break
    headers = [(k.decode(), v.decode()) for k, v in scope["headers"] if k.lower() not in (b"host", b"origin", b"content-length", b"referer")]
    url = SVC[m.group(1)] + (m.group(2) or "/") + (("?" + scope["query_string"].decode()) if scope["query_string"] else "")
    r = await proxy_client.request(scope["method"], url, headers=headers + [("origin", "http://localhost")], content=body)
    out = [(k.encode(), v.encode()) for k, v in r.headers.multi_items() if k.lower() not in ("content-length", "transfer-encoding", "content-encoding",
                                                                                            "access-control-allow-origin", "access-control-allow-credentials")]
    await send({"type": "http.response.start", "status": r.status_code, "headers": out + [(b"content-length", str(len(r.content)).encode())]})
    await send({"type": "http.response.body", "body": r.content})


for app_, port in ((institute_site, PORT), (student_site, SPORT)):
    server = uvicorn.Server(uvicorn.Config(app_, host="0.0.0.0", port=port, log_level="warning", lifespan="off"))
    threading.Thread(target=server.run, daemon=True).start()
for port in (PORT, SPORT):
    for _ in range(40):
        try:
            if httpx.get(f"http://127.0.0.1:{port}/login", timeout=2).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.25)

# ------------------------------------------------------------------ Chromium in the Playwright container (localhost:5174/5175 -> these servers)
PW_PYTHON = os.environ.get("PW_PYTHON", "python3")
pkgs = subprocess.run([PW_PYTHON, "-c", "import os, playwright; print(os.path.dirname(os.path.dirname(playwright.__file__)))"],
                      capture_output=True, text=True, check=True).stdout.strip()
subprocess.run(["docker", "rm", "-f", "gs-pw"], capture_output=True)
subprocess.run(["docker", "run", "-d", "--name", "gs-pw", "--add-host", "host.docker.internal:host-gateway", "-p", f"127.0.0.1:{PW_PORT}:3000",
                "-v", f"{pkgs}:/pwpkgs:ro", "-e", "PYTHONPATH=/pwpkgs", PW_IMAGE, "python3", "-m", "playwright", "run-server", "--port", "3000",
                "--host", "0.0.0.0"], check=True, capture_output=True)
for _ in range(60):
    try:
        if httpx.get(f"http://127.0.0.1:{PW_PORT}/", timeout=2).status_code == 200:
            break
    except httpx.HTTPError:
        time.sleep(0.5)
FWD = ("import asyncio\nasync def pipe(r, w):\n    try:\n        while data := await r.read(65536):\n            w.write(data); await w.drain()\n"
       "    finally:\n        w.close()\ndef handler(port):\n    async def h(r, w):\n        ur, uw = await asyncio.open_connection('host.docker.internal', port)\n"
       "        await asyncio.gather(pipe(r, uw), pipe(ur, w), return_exceptions=True)\n    return h\n"
       f"async def main():\n    a = await asyncio.start_server(handler({PORT}), '127.0.0.1', 5174)\n"
       f"    b = await asyncio.start_server(handler({SPORT}), '127.0.0.1', 5175)\n    await asyncio.gather(a.serve_forever(), b.serve_forever())\n"
       "asyncio.run(main())\n")
subprocess.run(["docker", "exec", "-d", "gs-pw", "python3", "-c", FWD], check=True)
time.sleep(2)

# photos: the browser script uploads them from this folder (Playwright sends the files to the remote browser)
PHOTOS = WORK / "photos"
PHOTOS.mkdir(exist_ok=True)
PAGE1 = ["Physics - Weekly Paper", "", "1. What is the SI unit of force?", "A) Newton", "B) Joule", "C) Watt", "D) Pascal", "",
         "2. Which quantity is a vector?", "A) Mass", "B) Speed", "C) Velocity", "D) Energy"]
PAGE2 = ["3. What is the unit of electric charge?", "A) Volt", "B) Coulomb", "C) Ohm", "D) Ampere", "",
         "4. Light travels fastest in which medium?", "A) Glass", "B) Water", "C) Vacuum", "D) Diamond"]
EXPECTED = [{"question": "What is the SI unit of force?", "options": ["Newton", "Joule", "Watt", "Pascal"], "correct": "A"},
            {"question": "Which quantity is a vector?", "options": ["Mass", "Speed", "Velocity", "Energy"], "correct": "C"},
            {"question": "What is the unit of electric charge?", "options": ["Volt", "Coulomb", "Ohm", "Ampere"], "correct": "B"},
            {"question": "Light travels fastest in which medium?", "options": ["Glass", "Water", "Vacuum", "Diamond"], "correct": "C"}]
BROKEN = ["Chemistry - torn page", "", "1. What is the pH of pure water?", "A) 5", "B) 7", "C) 9", "D) 11", "",
          "2. Which gas is used in balloons?", "A) Helium", "B) Oxygen"]           # options C/D torn off, no answer key
photos = {"page2-sideways.jpg": phone_photo(PAGE2, seed=2, sideways=True, blur=1.6), "page1.jpg": phone_photo(PAGE1, seed=1),
          "key.jpg": phone_photo(["Answer Key", "1. A", "2. C", "3. B", "4. C"], seed=3, size=44),
          "torn.jpg": phone_photo(BROKEN, seed=7), "blank.jpg": blank_photo(8)}
for n, data in photos.items():
    (PHOTOS / n).write_bytes(data)
check("phone-style photos prepared (4000x3000 JPEG, perspective, shadow, blur, one page sideways)",
      all(len(d) > 1_000_000 for n, d in photos.items() if n != "blank.jpg"), {n: len(d) for n, d in photos.items()})

params = {"ui": "http://localhost:5174", "sui": "http://localhost:5175", "ws": f"ws://127.0.0.1:{PW_PORT}/", "mail_log": str(WORK / "mail.log"),
          "password": PW, "shots": str(Path(os.environ.get("UI_SHOTS", WORK / "shots"))), "photos_host": str(PHOTOS), "expected": EXPECTED,
          "sa": "STF000001", "admin": ADM["staff_code"], "pa": PA["staff_code"], "ba": BA["staff_code"], "ta": TA["staff_code"],
          "tb": TB["staff_code"], "tutor": TU["staff_code"], "s1": S1, "s3": S3, "s1_name": "Asha", "tutor_students": [S1, S5]}
(WORK / "ui_params.json").write_text(json.dumps(params))
print("\nbrowser journeys, staff (Chromium)")
browser = subprocess.run([PW_PYTHON, str(ROOT / "tests" / "e2e" / "global_schedule_ui_browser.py"), str(WORK / "ui_params.json"), "staff"])
check("every staff browser journey passed (see the list above)", browser.returncode == 0)

# ------------------------------------------------------------------ what the browser did, in the database
print("\npersisted results (staff journeys)")
tests_ = sql("select test_id, kind, status, created_by, marking_mode, marks_correct, marks_wrong, duration_minutes from global_tests where name = 'National Mock'")
check("Global Test: one SCHEDULE test, published, created by the Super Admin, +4/-1, 30 minutes, no platform paper",
      len(tests_) == 1 and tests_[0][1:3] == ("SCHEDULE", "PUBLISHED") and str(tests_[0][3]) == sql("select staff_id from staff where staff_code='STF000001'")[0][0].__str__()
      and tests_[0][4:8] == ("UNIFORM", 4, 1, 30) and sql("select count(*) from global_test_sections where test_id = :t", t=tests_[0][0])[0][0] == 0, tests_)
GT = tests_[0][0]
subjects = sorted(n for (n,) in sql("select s.name from global_test_subjects g join subjects s on s.subject_id = g.subject_id where g.test_id = :t", t=GT))
check("its subjects are Physics and Chemistry", subjects == ["Chemistry", "Physics"], subjects)
occ = sql("select occurrence_id, start_at, status from global_test_occurrences where test_id = :t order by start_at", t=GT)
months = {o[1].strftime("%Y-%m") for o in occ if o[2] == "PUBLISHED"}
check("dates across 3 months, all published (the deleted draft is gone), every one a Saturday 10:00 Asia/Kolkata",
      len(months) == 3 and all(o[2] == "PUBLISHED" for o in occ) and all((o[1] + timedelta(hours=5, minutes=30)).weekday() == 5
                                                                        and (o[1] + timedelta(hours=5, minutes=30)).strftime("%H:%M") == "10:00" for o in occ),
      [(str(o[1]), o[2]) for o in occ])
admin_test = sql("select status, created_by from global_tests where name = 'Admin Draft Mock'")
check("the platform Admin created a draft Global Test (allowed by the Admin role)", len(admin_test) == 1 and admin_test[0][0] == "DRAFT"
      and str(admin_test[0][1]) == ADM["staff_id"], admin_test)
O1 = occ[0][0]
subs = sql("""select s.name as subj, coalesce(st.full_name, ''), sub.status, sub.paper_version_id, sub.audience from global_paper_submissions sub
              join subjects s on s.subject_id = sub.subject_id left join staff st on st.staff_id = sub.submitted_by where sub.occurrence_id = :o
              order by sub.submitted_at""", o=O1)
active = {(r[0], r[1]): r for r in subs if r[2] == "ACTIVE"}
check("first date: Tara (photo paper, NEET A), Tom (NEET B), the Branch Admin (Chemistry, all of Pune), the Provider Admin (Chemistry, Mumbai, "
      "unapproved) and the tutor (Physics, 2 students) each have one ACTIVE submission",
      {("Physics", "Tara Physics"), ("Physics", "Tom Physics"), ("Chemistry", "Bharat Branch"), ("Chemistry", "Priya Admin"), ("Physics", "Ravi Tutor")}
      == set(active), [(r[0], r[1], r[2]) for r in subs])
check("the refused overlapping submission (Provider Admin, Chemistry for all of North) was not stored",
      not any(r[1] == "Priya Admin" and r[4].get("whole_branch_ids") and len(r[4]["whole_branch_ids"]) == 2 for r in subs), subs)
tara_pv = str(active[("Physics", "Tara Physics")][3])
photo = sql("""select d.status, d.accepted_paper_version_id, jsonb_array_length(d.corrections), v.status, p.title from paper_photo_drafts d
               join paper_versions v on v.paper_version_id = d.accepted_paper_version_id join papers p on p.paper_id = v.paper_id
               where d.created_by = :t and d.status = 'ACCEPTED'""", t=TA["staff_id"])
check("Tara's photo paper: draft ACCEPTED -> paper version approved and PUBLISHED, corrections logged, and it is the paper she submitted",
      len(photo) == 1 and photo[0][3] == "PUBLISHED" and photo[0][2] > 0 and str(photo[0][1]) == tara_pv, photo)
qs = sql("select q.stem, q.option_a, q.option_b, q.option_c, q.option_d, q.correct_option from paper_version_questions pq join question_versions q "
         "on q.question_version_id = pq.question_version_id where pq.paper_version_id = :v order by pq.position", v=tara_pv)
check("the imported photo paper has exactly the printed questions, options and the answers from the separate key",
      [(r[0], [r[1], r[2], r[3], r[4]], r[5]) for r in qs] == [(e["question"], e["options"], e["correct"]) for e in EXPECTED], qs)
torn = sql("select status, accepted_paper_version_id from paper_photo_drafts where title = 'Torn chemistry page'")
check("the torn-page photo paper was refused as a whole: nothing imported, draft discarded by its owner",
      torn and torn[0][0] == "DISCARDED" and torn[0][1] is None
      and sql("select count(*) from papers where title = 'Torn chemistry page'")[0][0] == 0, torn)

# ------------------------------------------------------------------ the clock reaches the first date's paper deadline
print("\nfirst date: the deadline passes, the worker generates the exams")
now = datetime.now(UTC).replace(tzinfo=None)
execute("update global_test_occurrences set start_at = :s, end_at = :e where occurrence_id = :o", s=now - timedelta(minutes=2), e=now + timedelta(hours=3), o=O1)
with TestClient(gateway, base_url="http://localhost") as c:
    late = c.post(f"{API}/global-occurrences/{O1}/submissions", headers={**login(c, TB["staff_code"]), "Idempotency-Key": "late-tom-0001"},
                  json={"subject_id": PHY, "paper_version_id": PV_TOM, "audience": {"provider_id": P, "branch_ids": [B1], "batch_ids": [BB]}})
    check("a submission after the deadline is refused", late.status_code == 409, late.text)
with factory() as db:
    runs = global_generation.run_due(db, now=datetime.now(UTC))
report = runs[0] if runs else {}
check("the worker generates the first date (one run, no failure)", len(runs) == 1 and not report.get("failed"), runs)
with factory() as db:
    again = global_generation.run_due(db, now=datetime.now(UTC))
check("a repeated worker run generates nothing more", again == [], again)


def exams_by_student():
    rows = sql("""select a.student_id, pl.provider_id, e.exam_id, v.exam_version_id, array_agg(vp.paper_version_id::text order by vp.position)
                  from exam_plans pl join exams e on e.plan_id = pl.plan_id join exam_versions v on v.exam_id = e.exam_id
                  join exam_version_papers vp on vp.exam_version_id = v.exam_version_id join student_exam_assignments a on a.exam_id = e.exam_id
                  where pl.global_occurrence_id = :o group by a.student_id, pl.provider_id, e.exam_id, v.exam_version_id""", o=O1)
    out: dict = {}
    for sid, prov, exam, ev, papers in rows:
        out.setdefault(sid, []).append((str(prov), sorted(papers), str(exam), str(ev)))
    return out


got = exams_by_student()
want = {S1: {(P, tuple(sorted([tara_pv, PV_BA_CHE]))), (P2, (PV_TUTOR,))}, S2: {(P, tuple(sorted([tara_pv, PV_BA_CHE])))},
        S4: {(P, tuple(sorted([PV_TOM, PV_BA_CHE])))}, S5: {(P2, (PV_TUTOR,))}}
check("each student gets exactly their own teachers' papers: NEET A = Tara + Pune Chemistry, NEET B = Tom + Pune Chemistry, "
      "tutor students = Ravi's paper (Asha gets one exam per provider)",
      {s: {(p, tuple(v)) for p, v, *_ in xs} for s, xs in got.items()} == want, got)
check("Chitra (Mumbai) gets no exam: no Physics paper and the Mumbai Chemistry paper was never approved", S3 not in got, got.get(S3))
notes = [json.loads(p) if isinstance(p, str) else p for (p,) in sql("select payload from exam_notification_outbox where payload->>'kind' = 'GLOBAL_TEST_MISSING_PAPERS'")]
text_ = json.dumps(notes)
check("provider admins are told what is missing: North (Mumbai Physics uncovered, unapproved Chemistry), the tutor (no Chemistry)",
      len(notes) >= 2 and "not approved" in text_ and "Chemistry" in text_, notes)
exam_window = sql("""select pl.start_at, pl.end_at, pl.duration_minutes, e.status, o.start_at, o.end_at from exams e join exam_plans pl on pl.plan_id = e.plan_id
                     join global_test_occurrences o on o.occurrence_id = pl.global_occurrence_id where pl.global_occurrence_id = :o""", o=O1)
check("generated exams are PUBLISHED with the date's window and the test's duration (30 min)",
      exam_window and all(r[3] == "PUBLISHED" and r[2] == 30 and (r[0], r[1]) == (r[4], r[5]) for r in exam_window), exam_window)

# mapping changes after generation never alter the generated exams
before = exams_by_student()
with TestClient(gateway, base_url="http://localhost") as c:
    hb = login(c, BA["staff_code"])
    sub_ba = sql("select submission_id from global_paper_submissions where occurrence_id = :o and paper_version_id = :v", o=O1, v=PV_BA_CHE)[0][0]
    w = c.post(f"{API}/global-paper-submissions/{sub_ba}/withdraw", json={"reason": "too late"}, headers=hb)
    check("withdrawing a paper after generation is refused", w.status_code == 409, w.text)
execute("update teacher_subject_assignments set status = 'INACTIVE' where staff_id = :t", t=TA["staff_id"])                    # Tara stops teaching NEET A
execute("delete from enrollment_batches where batch_id = :b and enrollment_id in (select enrollment_id from enrollments where student_id = :s)", b=A, s=S2)
with factory() as db:
    global_generation.run_due(db, now=datetime.now(UTC))
check("teaching and batch changes after generation leave every generated exam and assignment unchanged", exams_by_student() == before)

with factory() as db:
    delivered = outbox.deliver_due(db)
    notify.deliver_pending(db)
check("published exams delivered to the Student test service", delivered.get("failed", 0) == 0 and delivered.get("sent", 0) >= 3, delivered)
check("students get a password through the real Student auth service", set_student_password("asha@family.example.com")
      and set_student_password("chitra@family.example.com"))
north_ev = next(ev for p, _v, _e, ev in got[S1] if p == P)
tutor_ev = next(ev for p, _v, _e, ev in got[S1] if p == P2)
# the answers Asha gives: every Physics question right; Chemistry: question 1 right, question 2 wrong, question 3 left blank
answers = {e["question"]: e["options"]["ABCD".index(e["correct"])] for e in EXPECTED}
answers.update({"Pune Chemistry Set question 1?": "A1", "Pune Chemistry Set question 2?": "A2"})
params.update({"north_test": north_ev, "tutor_test": tutor_ev, "answers": answers})
(WORK / "ui_params.json").write_text(json.dumps(params))

print("\nbrowser journeys, student (Chromium, Student portal)")
browser = subprocess.run([PW_PYTHON, str(ROOT / "tests" / "e2e" / "global_schedule_ui_browser.py"), str(WORK / "ui_params.json"), "student"])
check("every student browser journey passed (see the list above)", browser.returncode == 0)

print("\npersisted results (student journey)")
s = httpx.Client(timeout=20, headers={"Origin": "http://localhost"})
check("Asha signs in to the Student services", s.post(STU["auth"] + "/auth/login", json={"login": S1, "password": PW}).status_code == 200)
attempts = sql("select attempt_id, status, test_id, started_at, expires_at from test_attempts where student_id = :s", s=S1)
check("one SUBMITTED attempt by Asha on North's Global Test exam, timed by the existing timer (30 minutes)",
      any(str(a[2]) == north_ev and str(a[1]).upper().endswith(("SUBMITTED", "EVALUATED", "COMPLETED"))
          and abs((a[4] - a[3]).total_seconds() - 1800) < 120 for a in attempts), attempts)
res = sql("select total_marks, obtained_marks from test_results where test_id = :t and student_id = :s", t=north_ev, s=S1)
check("her result is evaluated by the existing Student result logic with the Global Test's +4/-1 marking: "
      "5 correct, 1 wrong, 1 skipped of 7 -> 19 of 28", res == [(28, 19)], res)
with TestClient(gateway, base_url="http://localhost") as c:
    h = login(c, "STF000001")
    actions = {x["action"] for x in c.get(f"{API}/audit-logs?page_size=200", headers=h).json()["items"]}
wanted = {"global_test.occurrence.generate"}
check("audit log records the generation", wanted <= actions, sorted(actions))
summary()
