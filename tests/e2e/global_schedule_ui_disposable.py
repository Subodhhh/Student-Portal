"""Global Test schedule + a teacher's Excel paper with a picture, in a REAL browser, on DISPOSABLE infrastructure
(run: tests/e2e/pipeline_disposable.sh global_schedule_ui_disposable.py).

Like test_types_ui_disposable.py: the Institute services run in-process behind the in-process gateway and serve a production build of
the Institute portal; the six real Student services run as processes on a throwaway PostgreSQL, and a production build of the STUDENT
portal is served on a second origin that proxies to them. Chromium (Playwright run-server container) drives both portals:
tests/e2e/global_schedule_ui_browser.py (phase "staff", then phase "student"). This driver creates the starting data through the
APIs, simulates the clock reaching the first date's paper deadline (the only time shift), runs the worker's generation, and checks in
the database and through the Student services what the browser did. Teacher Tara's paper is an ordinary Question Bank upload: an .xlsx
with a picture inserted into a question's image cell and a separate answer key (pictures are never read: OCR was removed 2026-11-16).

Needs: Docker, the image mcr.microsoft.com/playwright/python:v1.48.0-noble, npm, and a Python with
playwright==1.48 for the browser script (PW_PYTHON, default: python3).
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

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
from inst_exam.services import global_generation, global_layout, notify, outbox
from institute_common.core import email, redis_store
from institute_common.core.passwords import hash_password
from institute_common.database import get_db
from institute_common.internal_http import set_peer_client
from institute_common.models.staff import Staff, StaffRoleAssignment
from institute_common.ownership import attach_guard
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
    subprocess.run(["docker", "rm", "-f", "gs-pw"], capture_output=True)
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
    BIO = c.post(f"{API}/subjects", json={"code": "BIO", "name": "Biology"}, headers=h).json()["subject_id"]
    for prov, sjs in ((P, (PHY, CHE)), (P2, (BIO,))):        # the tutor teaches Biology only
        for sj in sjs:
            c.put(f"{API}/providers/{prov}/subjects/{sj}", json={}, headers=h)
    A = c.post(f"{API}/branches/{B1}/batches", json={"name": "NEET A"}, headers=h).json()["batch_id"]
    BB = c.post(f"{API}/branches/{B1}/batches", json={"name": "NEET B"}, headers=h).json()["batch_id"]
    C = c.post(f"{API}/branches/{B2}/batches", json={"name": "JEE C"}, headers=h).json()["batch_id"]
    for batch in (A, BB, C):          # the subjects each batch offers: what a membership without a selection takes for Global Tests
        for sj in (PHY, CHE):
            assert c.put(f"{API}/batches/{batch}/subjects/{sj}", json={}, headers=h).status_code == 200
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
    hpa = login(c, PA["staff_code"])
    PV_PA_CHE = csv_paper(c, hpa, B2, CHE, "M", 3, "Mumbai Chemistry Draft", approve=False)   # never approved
    PV_PA_NCHE = csv_paper(c, hpa, B1, CHE, "N", 3, "North Chemistry Set")                     # approved: a second Pune Chemistry paper
    PV_TUTOR = csv_paper(c, login(c, TU["staff_code"]), B3, BIO, "R", 2, "Ravi Biology Set")
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

# Tara's paper: the browser script uploads these files from this folder (Playwright sends the files to the remote browser)
FILES = WORK / "files"
FILES.mkdir(exist_ok=True)
EXPECTED = [{"question": "What is the SI unit of force?", "options": ["Newton", "Joule", "Watt", "Pascal"], "correct": "A"},
            {"question": "Which quantity is a vector?", "options": ["Mass", "Speed", "Velocity", "Energy"], "correct": "C"},
            {"question": "What is the unit of electric charge?", "options": ["Volt", "Coulomb", "Ohm", "Ampere"], "correct": "B"},
            {"question": "Light travels fastest in which medium?", "options": ["Glass", "Water", "Vacuum", "Diamond"], "correct": "C"}]


def force_diagram() -> bytes:
    """A block with an arrow labelled F: question 1's picture (shown to students exactly as drawn, never read)."""
    import io
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (320, 140), "white")
    d = ImageDraw.Draw(img)
    d.rectangle((40, 50, 120, 110), outline="black", width=3)
    d.line((120, 80, 250, 80), fill="black", width=3)
    d.polygon([(250, 70), (270, 80), (250, 90)], fill="black")
    d.text((255, 50), "F", fill="black")
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def tara_workbook(picture: bytes) -> bytes:
    """The question template without answers (they come from the separate key); the picture is inserted into question 1's image cell."""
    import io
    from openpyxl import Workbook
    from openpyxl.drawing.image import Image as XLImage
    wb = Workbook()
    ws = wb.active
    ws.append(["question", "question_image", "option_a", "option_b", "option_c", "option_d", "positive_marks", "negative_marks"])
    for e in EXPECTED:
        ws.append([e["question"], None, *e["options"], 4, 1])
    ws.add_image(XLImage(io.BytesIO(picture)), "B2")
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


FORCE = force_diagram()
(FILES / "tara-physics.xlsx").write_bytes(tara_workbook(FORCE))
(FILES / "tara-key.csv").write_text("".join(f"{i},{e['correct']}\n" for i, e in enumerate(EXPECTED, start=1)))
(FILES / "page-photo.png").write_bytes(FORCE)                 # a picture offered as a whole paper: refused, never read
check("Tara's Excel paper (4 questions, a picture in question 1's image cell) and separate answer key prepared",
      (FILES / "tara-physics.xlsx").stat().st_size > 0)

params = {"ui": "http://localhost:5174", "sui": "http://localhost:5175", "ws": f"ws://127.0.0.1:{PW_PORT}/", "mail_log": str(WORK / "mail.log"),
          "password": PW, "shots": str(Path(os.environ.get("UI_SHOTS", WORK / "shots"))), "files_host": str(FILES), "expected": EXPECTED,
          "sa": "STF000001", "admin": ADM["staff_code"], "pa": PA["staff_code"], "ba": BA["staff_code"], "ta": TA["staff_code"],
          "tb": TB["staff_code"], "tutor": TU["staff_code"], "s1": S1, "s3": S3, "s1_name": "Asha", "tutor_students": [S1, S5],
          "pune_chemistry_students": len([S1, S2, S4])}          # every Pune student (NEET A + NEET B) takes Chemistry
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
check("its subjects are Physics, Chemistry and Biology", subjects == ["Biology", "Chemistry", "Physics"], subjects)
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
check("first date: Tara (Excel paper, NEET A), Tom (NEET B), the Branch Admin (Chemistry, all of Pune), the Provider Admin (two Chemistry "
      "papers) and the tutor (Biology, 2 students) have ACTIVE submissions",
      sorted((r[0], r[1]) for r in subs if r[2] == "ACTIVE") == sorted([("Physics", "Tara Physics"), ("Physics", "Tom Physics"),
                                                                       ("Chemistry", "Bharat Branch"), ("Chemistry", "Priya Admin"),
                                                                       ("Chemistry", "Priya Admin"), ("Biology", "Ravi Tutor")]),
      [(r[0], r[1], r[2]) for r in subs])
priya = {str(r[3]): r for r in subs if r[1] == "Priya Admin"}
check("the Provider Admin's North Chemistry for all of Pune (overlapping the Branch Admin's Pune paper: a different paper, allowed since "
      "per-student paper choice) is stored ONCE - the duplicate of the same paper was refused - and her Mumbai draft once, for Mumbai",
      sorted(priya) == sorted([PV_PA_NCHE, PV_PA_CHE]) and len([r for r in subs if r[1] == "Priya Admin"]) == 2
      and (priya[PV_PA_NCHE][4] or {}).get("whole_branch_ids") == [B1] and (priya[PV_PA_CHE][4] or {}).get("whole_branch_ids") == [B2],
      [(r[1], str(r[3]), r[4]) for r in subs])
tara_pv = str(active[("Physics", "Tara Physics")][3])
tara = sql("""select v.status, p.title, u.status, sf.content_type from paper_versions v join papers p on p.paper_id = v.paper_id
              join paper_uploads u on u.paper_version_id = v.paper_version_id join stored_files sf on sf.file_id = u.file_id
              where v.paper_version_id = :v""", v=tara_pv)
check("Tara's paper: uploaded as an .xlsx with a separate key, VALIDATED -> approved and PUBLISHED, and it is the paper she submitted",
      tara == [("PUBLISHED", "Tara Physics paper", "VALIDATED", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")], tara)
pic = sql("""select a.image_data, q.option_a_image_id from paper_version_questions pq join question_versions q on q.question_version_id = pq.question_version_id
             join question_image_assets a on a.asset_id = q.stem_image_id where pq.paper_version_id = :v order by pq.position""", v=tara_pv)
check("question 1 carries its picture, byte-identical to the one inserted in the Excel cell; no other question or option has one",
      len(pic) == 1 and bytes(pic[0][0]) == FORCE and pic[0][1] is None, len(pic))
check("nothing read the picture: no recognition job exists", sql("select count(*) from picture_recognition_jobs")[0][0] == 0)
qs = sql("select q.stem, q.option_a, q.option_b, q.option_c, q.option_d, q.correct_option from paper_version_questions pq join question_versions q "
         "on q.question_version_id = pq.question_version_id where pq.paper_version_id = :v order by pq.position", v=tara_pv)
check("the imported paper has exactly the file's questions and options and the answers from the separate key",
      [(r[0], [r[1], r[2], r[3], r[4]], r[5]) for r in qs] == [(e["question"], e["options"], e["correct"]) for e in EXPECTED], qs)
check("the picture offered as a whole paper was refused: no paper and no upload record for it",
      sql("select count(*) from papers where title = 'Photo of a paper'")[0][0] == 0)

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
date_exams = {xs[0][2] for xs in got.values()}
SUB = {str(v): str(k) for k, v in sql("select submission_id, paper_version_id from global_paper_submissions where occurrence_id = :o "
                                      "and status = 'ACTIVE'", o=O1)}            # paper version -> its submission (the Student services' paper id)
USABLE = sorted([tara_pv, PV_TOM, PV_BA_CHE, PV_PA_NCHE, PV_TUTOR])                # approved + published by the deadline; never the draft
check("ONE exam for the whole date (commit cd39e29): every eligible student - Chitra too - is assigned the same exam, once",
      len(date_exams) == 1 and sorted(got) == sorted([S1, S2, S3, S4, S5]) and all(len(xs) == 1 for xs in got.values()), got)
DATE_EXAM = next(iter(date_exams))
check("its papers are exactly the date's usable submissions: Tara + Tom (Physics), Pune + North Chemistry, Ravi Biology - never the "
      "unapproved Mumbai draft", sorted(got[S1][0][1]) == USABLE and PV_PA_CHE not in got[S1][0][1], got[S1])
elig = {}
for sid, subj, pv in sql("""select x.student_id, s.name, x.paper_version_id::text from global_test_student_subject_papers x
                            join subjects s on s.subject_id = x.subject_id where x.occurrence_id = :o""", o=O1):
    elig.setdefault(sid, {}).setdefault(subj, set()).add(pv)
want_elig = {S1: {"Physics": {tara_pv}, "Chemistry": {PV_BA_CHE, PV_PA_NCHE}, "Biology": {PV_TUTOR}},    # NEET A + the tutor
             S2: {"Physics": {tara_pv}, "Chemistry": {PV_BA_CHE, PV_PA_NCHE}},                            # NEET A
             S4: {"Physics": {PV_TOM}, "Chemistry": {PV_BA_CHE, PV_PA_NCHE}},                             # NEET B
             S5: {"Biology": {PV_TUTOR}}}                                                                  # the tutor's student only
check("each student may sit exactly their own institutes' papers (global_test_student_subject_papers): their teacher's Physics, BOTH "
      "Pune Chemistry papers for every Pune student (a choice), Ravi Biology for the tutor's two; Chitra none",
      elig == want_elig and S3 not in elig, elig)
prov_of = dict(sql("""select vp.paper_version_id::text, sub.provider_id::text from exam_version_papers vp
                      join global_paper_submissions sub on sub.submission_id = vp.source_submission_id
                      join exam_versions v on v.exam_version_id = vp.exam_version_id where v.exam_id = :e""", e=DATE_EXAM))
check("the date's exam keeps each paper's provenance: Physics and Chemistry from North, Biology from the tutor",
      prov_of == {tara_pv: P, PV_TOM: P, PV_BA_CHE: P, PV_PA_NCHE: P, PV_TUTOR: P2}, prov_of)
ORDER = [n for (n,) in sql("select s.name from global_test_subjects g join subjects s on s.subject_id = g.subject_id where g.test_id = :t order by g.position", t=GT)]


def layout(sid):
    return [(n, st) for n, st in sql("""select s.name, x.status from global_test_student_subjects x join subjects s on s.subject_id = x.subject_id
                                         where x.occurrence_id = :o and x.student_id = :s order by x.position""", o=O1, s=sid)]


chitra = dict(layout(S3))
check("every eligible student has ONE row for the date and one row per configured subject, in the configured order",
      sorted(sid for (sid,) in sql("select student_id from global_test_students where occurrence_id = :o", o=O1)) == sorted([S1, S2, S3, S4, S5])
      and all([n for n, _ in layout(x)] == ORDER for x in (S1, S2, S3, S4, S5)), (ORDER, {x: layout(x) for x in (S1, S2, S3, S4, S5)}))
check("Asha: Physics and Biology one paper each (PAPER), Chemistry two (SELECTABLE: she chooses); Chitra none, each with its reason: "
      "Physics not covered (Pune's papers are for NEET A/B), Chemistry not approved in time",
      dict(layout(S1)) == {"Physics": "PAPER", "Chemistry": "SELECTABLE", "Biology": "PAPER"}
      and chitra.get("Physics") == "NOT_COVERED" and chitra.get("Chemistry") == "NOT_APPROVED"
      and not {"PAPER", "SELECTABLE"} & set(chitra.values()), (layout(S1), chitra))
chitra_exam = sql("""select g.exam_id::text, e.status from global_test_students g join exams e on e.exam_id = g.exam_id
                     where g.occurrence_id = :o and g.student_id = :s""", o=O1, s=S3)
check("Chitra still gets ONE Global Test card: the date's exam, with no paper she may sit (each subject explains why)",
      chitra_exam == [(str(DATE_EXAM), "PUBLISHED")] and S3 not in elig, chitra_exam)
names = {n for (n,) in sql("select distinct e.name from exams e join exam_plans pl on pl.plan_id = e.plan_id where pl.global_occurrence_id = :o", o=O1)}
check("every exam of the date carries the Global Test's own name, without provider, branch or date", names == {"National Mock"}, names)
notes = [json.loads(p) if isinstance(p, str) else p for (p,) in sql("select payload from exam_notification_outbox where payload->>'kind' = 'GLOBAL_TEST_MISSING_PAPERS'")]
north_notes = [n for n in notes if n["provider_id"] == P]
check("provider admins are told what is missing: North (unapproved Mumbai Chemistry, the Mumbai student uncovered); the tutor, whose only "
      "subject (Biology) is covered, is told nothing",
      north_notes and all("not approved" in n["body"] and "Mumbai: 1" in n["body"] for n in north_notes)
      and not [n for n in notes if n["provider_id"] == P2], notes)
exam_window = sql("""select pl.start_at, pl.end_at, pl.duration_minutes, e.status, o.start_at, o.end_at from exams e join exam_plans pl on pl.plan_id = e.plan_id
                     join global_test_occurrences o on o.occurrence_id = pl.global_occurrence_id where pl.global_occurrence_id = :o""", o=O1)
check("generated exams are PUBLISHED with the date's window and the test's duration (30 min)",
      exam_window and all(r[3] == "PUBLISHED" and r[2] == 30 and (r[0], r[1]) == (r[4], r[5]) for r in exam_window), exam_window)

# mapping changes after generation never alter the generated exams
before = (exams_by_student(), sorted(sql("select occurrence_id::text, student_id, subject_id::text, submission_id::text "
                                         "from global_test_student_subject_papers where occurrence_id = :o", o=O1)))
with TestClient(gateway, base_url="http://localhost") as c:
    hb = login(c, BA["staff_code"])
    sub_ba = sql("select submission_id from global_paper_submissions where occurrence_id = :o and paper_version_id = :v", o=O1, v=PV_BA_CHE)[0][0]
    w = c.post(f"{API}/global-paper-submissions/{sub_ba}/withdraw", json={"reason": "too late"}, headers=hb)
    check("withdrawing a paper after generation is refused", w.status_code == 409, w.text)
execute("update teacher_subject_assignments set status = 'INACTIVE' where staff_id = :t", t=TA["staff_id"])                    # Tara stops teaching NEET A
execute("delete from enrollment_batches where batch_id = :b and enrollment_id in (select enrollment_id from enrollments where student_id = :s)", b=A, s=S2)
with factory() as db:
    global_generation.run_due(db, now=datetime.now(UTC))
check("teaching and batch changes after generation leave the date's exam, every assignment and every student's frozen paper "
      "eligibility unchanged", (exams_by_student(), sorted(sql("select occurrence_id::text, student_id, subject_id::text, "
      "submission_id::text from global_test_student_subject_papers where occurrence_id = :o", o=O1))) == before)

with factory() as db:
    delivered = outbox.deliver_due(db)
    notify.deliver_pending(db)
check("the date's ONE exam delivered to the Student test service", delivered.get("failed", 0) == 0 and delivered.get("sent", 0) == 1, delivered)
check("students get a password through the real Student auth service", set_student_password("asha@family.example.com")
      and set_student_password("chitra@family.example.com"))
[(_p, _v, asha_exam, asha_ev)] = got[S1]                         # the date's exam (every student holds this same one)
# provider isolation of the date's exam: each provider sees only the subjects its papers fill (services/unified.py); the anchor grants nothing
with TestClient(gateway, base_url="http://localhost") as c:
    hn = login(c, PA["staff_code"])
    north = c.get(f"{API}/exams/{asha_exam}", headers=hn).json()
    north_layout = c.get(f"{API}/global-occurrences/{O1}/student-subjects", headers=hn).json()
    cancel = c.post(f"{API}/exams/{asha_exam}/cancel", json={"reason": "not North's to cancel"}, headers=hn)
with TestClient(gateway, base_url="http://localhost") as c:
    tutor_view = c.get(f"{API}/exams/{asha_exam}", headers=login(c, TU["staff_code"])).json()
with TestClient(gateway, base_url="http://localhost") as c:
    hp = login(c, "STF000001")
    full = c.get(f"{API}/exams/{asha_exam}", headers=hp).json()
    full_layout = c.get(f"{API}/global-occurrences/{O1}/student-subjects", headers=hp).json()
pvs = lambda view: sorted(x["paper_version_id"] for x in view.get("papers", []))
check("North's admin sees only the date exam's Physics + Chemistry papers (its own four), the tutor only Ravi Biology, the platform all "
      "five usable papers; North cannot cancel the exam",
      {x["subject_id"] for x in north["papers"]} == {PHY, CHE} and pvs(north) == sorted([tara_pv, PV_TOM, PV_BA_CHE, PV_PA_NCHE])
      and [x["subject_id"] for x in tutor_view["papers"]] == [BIO] and pvs(tutor_view) == [PV_TUTOR]
      and pvs(full) == USABLE and cancel.status_code == 403, (pvs(north), pvs(tutor_view), pvs(full), cancel.status_code))
cells = lambda body, sid: [(x["subject"], x["status"], x["provider_name"]) for st in body["students"] if st["student_id"] == sid for x in st["subjects"]]
check("the platform's layout shows every student and subject; North's shows only its students and its own part (never the tutor's Biology)",
      {st["student_id"] for st in full_layout["students"]} == {S1, S2, S3, S4, S5} and [x[0] for x in cells(full_layout, S1)] == ORDER
      and {x[0] for x in cells(north_layout, S1)} == {"Physics", "Chemistry"} and S5 not in {st["student_id"] for st in north_layout["students"]},
      (cells(full_layout, S1), cells(north_layout, S1)))
# the Student test service holds ONE test for the date: every slot's questions (tagged with their submission) and the shared sections
qcount = {str(pv): n for pv, n in sql("select paper_version_id, question_count from paper_versions where paper_version_id::text in "
                                     "(:a, :b, :c, :d, :e)", a=USABLE[0], b=USABLE[1], c=USABLE[2], d=USABLE[3], e=USABLE[4])}
per_paper = {str(p): n for p, n in sql("select paper_id, count(*) from questions where test_id = :t group by paper_id", t=asha_ev)}
check("the delivered test holds every usable paper's questions, each tagged with its submission (the Student services' paper id)",
      per_paper == {SUB[pv]: qcount[pv] for pv in USABLE}, (per_paper, {SUB[pv]: qcount[pv] for pv in USABLE}))
sections = sql("select sections from tests where test_id = :t", t=asha_ev)[0][0]
check("its sections are the shared configuration: every configured subject in order, listing exactly that subject's papers",
      [(x["subject"], sorted(str(p["paper_id"]) for p in x["papers"])) for x in sections]
      == [(n, sorted(SUB[pv] for pv in USABLE if pv in {v for s in want_elig.values() for v in s.get(n, set())})) for n in ORDER], sections)
student_elig = {}
for sid, subj, status, pid in sql("select student_id, subject, status, paper_id from student_paper_eligibility where test_id = :t", t=asha_ev):
    student_elig.setdefault(sid, {})[subj] = (status, student_elig.get(sid, {}).get(subj, (None, set()))[1] | ({str(pid)} if pid else set()))
inst_status = {sid: dict(layout(sid)) for sid in (S1, S2, S3, S4, S5)}
check("each student's frozen eligibility reached the Student test service unchanged: the same status per subject and the same papers",
      student_elig == {sid: {n: (inst_status[sid][n], {SUB[pv] for pv in elig.get(sid, {}).get(n, set())}) for n in ORDER}
                       for sid in (S1, S2, S3, S4, S5)}, student_elig)
# Asha's choice (a scenario decision): Pune Chemistry. The papers she will sit are then her PAPER subjects plus that choice.
CHOSEN = {"Chemistry": PV_BA_CHE}
asha_papers = {SUB[pv] for subj, pvs_ in want_elig[S1].items() for pv in pvs_ if len(pvs_) == 1 or CHOSEN.get(subj) == pv}
# the order Asha must see: only her papers' questions; subjects in the configured order, inside each an order of her own
# (sha256 of student, test, question - app/services/question_order.py)
qs = sql("select question_id::text, subject, question_text, paper_id::text from questions where test_id = :t", t=asha_ev)
expected_order = [text_ for _q, _s, text_, _p in sorted((r for r in qs if r[3] in asha_papers),
                                                         key=lambda r: (ORDER.index(r[1]), hashlib.sha256(f"{S1}:{asha_ev}:{r[0]}".encode()).hexdigest()))]
title_of = dict(sql("select v.paper_version_id::text, p.title from paper_versions v join papers p on p.paper_id = v.paper_id"))
# the answers Asha gives: every Physics question right; Chemistry: question 1 right, question 2 wrong, question 3 left blank;
# Biology: question 1 right, question 2 left blank
answers = {e["question"]: e["options"]["ABCD".index(e["correct"])] for e in EXPECTED}
answers.update({"Pune Chemistry Set question 1?": "A1", "Pune Chemistry Set question 2?": "A2", "Ravi Biology Set question 1?": "A1"})
params.update({"asha_test": asha_ev, "answers": answers, "subject_order": ORDER, "expected_order": expected_order,
               # what Test Details must offer her: a choice only where she has two or more eligible papers, with their titles
               "selectable": {subj: sorted(title_of[pv] for pv in pvs_) for subj, pvs_ in want_elig[S1].items() if len(pvs_) > 1},
               "chosen": {subj: title_of[pv] for subj, pv in CHOSEN.items()},
               # Chitra's reasons, in the product's own words (inst_exam.services.global_layout.MESSAGES)
               "chitra_messages": {n: global_layout.MESSAGES[st].format(subject=n) for n, st in layout(S3)}})
(WORK / "ui_params.json").write_text(json.dumps(params))

print("\nbrowser journeys, student (Chromium, Student portal)")
browser = subprocess.run([PW_PYTHON, str(ROOT / "tests" / "e2e" / "global_schedule_ui_browser.py"), str(WORK / "ui_params.json"), "student"])
check("every student browser journey passed (see the list above)", browser.returncode == 0)

print("\npersisted results (student journey)")
s = httpx.Client(timeout=20, headers={"Origin": "http://localhost"})
check("Asha signs in to the Student services", s.post(STU["auth"] + "/auth/login", json={"login": S1, "password": PW}).status_code == 200)
attempts = sql("select attempt_id, status, test_id, started_at, expires_at from test_attempts where student_id = :s", s=S1)
check("exactly one attempt by Asha for the date, SUBMITTED on her unified exam, timed by one shared timer (30 minutes)",
      len(attempts) == 1 and str(attempts[0][2]) == asha_ev and str(attempts[0][1]).upper().endswith(("SUBMITTED", "EVALUATED", "COMPLETED"))
      and abs((attempts[0][4] - attempts[0][3]).total_seconds() - 1800) < 120, attempts)
warned = sql("select count(*) from attempt_integrity_events e join test_attempts a on a.attempt_id = e.attempt_id where a.student_id = :s", s=S1)
check("her reload mid-attempt and the submit confirmation recorded no warning (a refresh is not leaving the exam)", warned == [(0,)], warned)
reason = sql("select submit_reason from test_attempts where student_id = :s", s=S1)
check("her attempt records why it ended: submitted by the student", reason == [("STUDENT",)], reason)
res = sql("select total_marks, obtained_marks from test_results where test_id = :t and student_id = :s", t=asha_ev, s=S1)
check("one overall result, evaluated by the existing Student result logic with the Global Test's +4/-1 marking across all subjects: "
      "6 correct, 1 wrong, 2 skipped of 9 -> 23 of 36", res == [(36, 23)], res)
listed = [t for t in s.get(STU["test"] + "/tests").json() if t["name"].startswith("National Mock")]
check("the Student test service lists exactly one National Mock for Asha, marked as the Global Test date (no copy per provider)",
      len(listed) == 1 and str(listed[0]["test_id"]) == asha_ev and str(listed[0]["global_occurrence_id"]) == str(O1), listed)
mine = [r for r in s.get(STU["result"] + "/results").json() if r["test_name"].startswith("National Mock")]
check("her Results list has ONE National Mock result, labelled as the Global Test (not as the anchor provider), marked with the date",
      len(mine) == 1 and mine[0]["provider_name"] == "Global Test" and str(mine[0]["global_occurrence_id"]) == str(O1)
      and mine[0]["obtained_marks"] == 23, mine)
# provider-specific results through the REAL Student result service (its per-subject SQL on this PostgreSQL), never a fake client
print("\nresults of the unified exam, per provider (real Student result service)")
with TestClient(gateway, base_url="http://localhost") as c:                    # one session per staff member, one sign-in each
    h = login(c, PA["staff_code"])
    north_r = c.get(f"{API}/exams/{asha_exam}/results", headers=h)
    north_a = c.get(f"{API}/exams/{asha_exam}/assignments", headers=h)
with TestClient(gateway, base_url="http://localhost") as c:
    ht = login(c, TU["staff_code"])
    tutor_r = c.get(f"{API}/exams/{asha_exam}/results", headers=ht)
    tutor_a = c.get(f"{API}/exams/{asha_exam}/assignments", headers=ht)
nr, tr = north_r.json(), tutor_r.json()


def subjects_of(r):
    return {x["subject"]: (x["obtained_marks"], x["total_marks"]) for row in r.get("results", []) for x in row.get("subjects", [])}


# the subject view's header total is that of every paper the caller supplied to the date's exam (inst_exam/routes/exams.py, _papers_total
# over the caller's papers) - for North all four of its papers, not one student's; each student row keeps that student's own papers
from inst_exam.routes.exams import _papers_total
from institute_common.models.exams import ExamVersion, ExamVersionPaper

with factory() as db:
    _ev = db.get(ExamVersion, UUID(asha_ev))
    north_total = _papers_total(_ev, [x for x in db.query(ExamVersionPaper).filter_by(exam_version_id=_ev.exam_version_id)
                                      if str(x.paper_version_id) in {tara_pv, PV_TOM, PV_BA_CHE, PV_PA_NCHE}])
check(f"North's admin sees only Physics + Chemistry of Asha's result: 16/16 + 3/12 = 19 of 28 (her Physics all right; Chemistry +4 -1); "
      f"the header total is North's four papers ({north_total})",
      north_r.status_code == 200 and nr["subject_view"] is True and nr["visible_subjects"] == ["Chemistry", "Physics"]
      and subjects_of(nr) == {"Physics": (16, 16), "Chemistry": (3, 12)} and [(x["student_id"], x["obtained_marks"], x["total_marks"]) for x in nr["results"]] == [(S1, 19, 28)]
      and nr["total_marks"] == str(north_total), (north_total, nr))
check("the tutor sees only Biology: 4 of 8 (question 1 right, question 2 left blank)",
      tutor_r.status_code == 200 and tr["subject_view"] is True and subjects_of(tr) == {"Biology": (4, 8)}
      and [(x["student_id"], x["obtained_marks"]) for x in tr["results"]] == [(S1, 4)], tr)
ANSWER_KEYS = {"selected_answer", "correct_answer", "correct_option", "answer", "answers", "options", "question_text", "explanation"}


def keys_of(v):
    if isinstance(v, dict):
        return set(v) | {k for x in v.values() for k in keys_of(x)}
    return {k for x in v for k in keys_of(x)} if isinstance(v, list) else set()


def overall_of(r):                                   # any row or summary carrying the whole-exam score (23 of 36)
    return [x for x in r.get("results", []) if x.get("obtained_marks") == 23 or x.get("total_marks") == 36] + \
           ([r["summary"]] if r.get("summary", {}).get("highest") == 23 else [])


leak = sorted(keys_of(nr) & ANSWER_KEYS) + sorted(keys_of(tr) & ANSWER_KEYS) + overall_of(nr) + overall_of(tr) + \
       sorted(set(subjects_of(nr)) - {"Physics", "Chemistry"}) + sorted(set(subjects_of(tr)) - {"Biology"})
check("neither provider sees the other's subject, any answer, or the overall score (23)", not leak, leak)
# provider scope of the date's exam (services/unified.py): the assigned students ENROLLED with that provider
enrolled_with = lambda prov: {sid for (sid,) in sql("select distinct student_id from enrollments where provider_id = :p and status = 'ACTIVE'", p=prov)}
assigned = set(got)
check("each provider lists only the exam's students enrolled with it: North its four (never Esha), the tutor its two (Asha, Esha)",
      north_a.status_code == 200 and sorted(x["student_id"] for x in north_a.json()["items"]) == sorted(assigned & enrolled_with(P))
      and S5 not in {x["student_id"] for x in north_a.json()["items"]}
      and tutor_a.status_code == 200 and sorted(x["student_id"] for x in tutor_a.json()["items"]) == sorted(assigned & enrolled_with(P2)),
      (north_a.text[:300], tutor_a.text[:300], sorted(assigned & enrolled_with(P)), sorted(assigned & enrolled_with(P2))))
snap = {s_: str(p_) for s_, p_ in sql("""select sp.subject, sp.paper_id from attempt_selected_papers sp join test_attempts a on a.attempt_id = sp.attempt_id
                                       where a.student_id = :s and a.test_id::text = :t""", s=S1, t=asha_ev)}
check("her attempt was started with exactly the papers she may sit and chose: Tara Physics, Pune Chemistry (her choice), Ravi Biology",
      set(snap.values()) == asha_papers and snap.get("Chemistry") == SUB[PV_BA_CHE], snap)

with TestClient(gateway, base_url="http://localhost") as c:            # the platform admin's last sign-in (5 per account per 15 minutes)
    h = login(c, "STF000001")
    full_r = c.get(f"{API}/exams/{asha_exam}/results", headers=h)
    actions = {x["action"] for x in c.get(f"{API}/audit-logs?page_size=200", headers=h).json()["items"]}
fr = full_r.json()
check("the platform sees the whole result: 23 of 36 overall", full_r.status_code == 200 and fr["subject_view"] is False
      and [(x["student_id"], x["obtained_marks"], x["total_marks"]) for x in fr["results"]] == [(S1, 23, 36)], fr)
wanted = {"global_test.occurrence.generate"}
check("audit log records the generation", wanted <= actions, sorted(actions))
summary()
