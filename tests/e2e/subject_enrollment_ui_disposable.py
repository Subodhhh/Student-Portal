"""Subject-wise enrollment in a REAL browser, on DISPOSABLE infrastructure (run: tests/e2e/pipeline_disposable.sh subject_enrollment_ui_disposable.py).

Same set-up as test_types_ui_disposable.py: the Institute services in-process behind the in-process gateway, served with a production
build of the portal on one origin; the real Student services as processes on a throwaway PostgreSQL; Chromium (Playwright run-server
container) runs tests/e2e/subject_enrollment_ui_browser.py. This driver creates the starting data through the APIs, then checks what the
browser did: persisted selections, enrollment history, audit, exam eligibility and teacher visibility. Existing memberships without a
selection must behave as before.

Needs: Docker, the Playwright image mcr.microsoft.com/playwright/python:v1.48.0-noble, npm, and PW_PYTHON with playwright==1.48.
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

DB, KEY, AUTH_LOG = os.environ["PIPELINE_DB"], os.environ["PIPELINE_KEY"], Path(os.environ["PIPELINE_AUTH_LOG"])
if not re.search(r"@127\.0\.0\.1:\d+/pipeline_verify$", DB):
    sys.exit("refusing: disposable pipeline database only")
ROOT = Path(__file__).resolve().parents[2]
WORK = AUTH_LOG.parent
PORT, PW_PORT, PW_IMAGE = 18191, int(os.environ.get("UI_PW_PORT", "3012")), "mcr.microsoft.com/playwright/python:v1.48.0-noble"
STU = {"auth": "http://127.0.0.1:18102", "enroll": "http://127.0.0.1:18104", "test": "http://127.0.0.1:18105",
       "attempt": "http://127.0.0.1:18106", "result": "http://127.0.0.1:18107", "notify": "http://127.0.0.1:18108"}
for name, url in (("STUDENT_SERVICE", STU["auth"]), ("STUDENT_ENROLLMENT_SERVICE", STU["enroll"]), ("STUDENT_TEST_SERVICE", STU["test"]),
                  ("STUDENT_ATTEMPT_SERVICE", STU["attempt"]), ("STUDENT_RESULT_SERVICE", STU["result"]), ("STUDENT_NOTIFICATION_SERVICE", STU["notify"])):
    os.environ[f"{name}_URL"], os.environ[f"{name}_API_KEY"] = url, KEY

sys.path.insert(0, str(ROOT / "institute-backend" / "institute-integration-tests"))
import fakeredis
import httpx
import tests.conftest  # noqa: F401  (service paths + test settings; the database is overridden below)
import uvicorn
from fastapi.testclient import TestClient
from inst_enrollment.services import reference_sync
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

ORIGIN, API, PW = {"Origin": "http://localhost:5174"}, "/api/v1", "SubjEnroll-" + uuid4().hex
ACAD = {"class_level": "11th", "academic_year": "2026-2027"}   # every enrollment needs its Class / Academic Year
results: list[tuple[str, bool]] = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f" - {str(detail)[:400]}"), flush=True)
    if not ok:
        summary()


def summary():
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} driver checks passed" + (f"; FAILED: {failed}" if failed else ""), flush=True)
    subprocess.run(["docker", "rm", "-f", "se-pw"], capture_output=True, check=False)
    os._exit(1 if failed else 0)


engine = create_engine(DB)
factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def sql(q, **a):
    with engine.connect() as conn:
        return conn.execute(text(q), a).all()


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


def invite(c, h, name, mail, role, provider, branch=None):
    assignment = {"role": role, "provider_id": provider, **({"branch_id": branch} if branch else {})}
    r = c.post(f"{API}/staff", json={"full_name": name, "email": mail, "assignment": assignment}, headers=h)
    assert r.status_code == 201, r.text
    token = re.search(r"token=([A-Za-z0-9_\-]+)", sender.outbox[-1].body).group(1)
    c.post(f"{API}/auth/verify-email", json={"token": token, "new_password": PW}, headers=ORIGIN)
    return r.json()


def published_paper(c, h, branch, subject, prefix, title):
    rows = [f"{prefix}{i:03d},{title} question {i}?,A{i},B{i},C{i},D{i},1,0," for i in range(1, 3)]
    paper = "question_id,question,option_a,option_b,option_c,option_d,marks,negative_marks,explanation\n" + "\n".join(rows) + "\n"
    key = "question_id,correct_option,explanation\n" + "\n".join(f"{prefix}{i:03d},A,Reason {i}" for i in range(1, 3)) + "\n"
    files = {"file": ("paper.csv", paper.encode(), "text/csv"), "answer_key": ("key.csv", key.encode(), "text/csv")}
    r = c.post(f"{API}/branches/{branch}/subjects/{subject}/papers/upload", files=files, data={"title": title}, headers=h)
    assert r.status_code == 201, r.text
    pv = r.json()["paper_version_id"]
    assert c.post(f"{API}/paper-versions/{pv}/approve", json={"note": "ok"}, headers=h).status_code == 200
    assert c.post(f"{API}/paper-versions/{pv}/publish", json={"note": None}, headers=h).status_code == 200
    return pv


# ------------------------------------------------------------------ starting data, through the APIs
with TestClient(gateway, base_url="http://localhost") as c:
    print("set-up through the Institute API")
    h = login(c, "STF000001")
    P = c.post(f"{API}/providers", json={"name": "North Academy", "email": "office@north.example.com"}, headers=h).json()["provider_id"]
    B1 = c.post(f"{API}/providers/{P}/branches", json={"name": "Pune", "city": "Pune", "state": "MH"}, headers=h).json()["branch_id"]
    PHY = c.post(f"{API}/subjects", json={"code": "PHY", "name": "Physics"}, headers=h).json()["subject_id"]
    CHE = c.post(f"{API}/subjects", json={"code": "CHE", "name": "Chemistry"}, headers=h).json()["subject_id"]
    for sj in (PHY, CHE):
        c.put(f"{API}/providers/{P}/subjects/{sj}", json={}, headers=h)
    A = c.post(f"{API}/branches/{B1}/batches", json={"name": "NEET A"}, headers=h).json()["batch_id"]
    for sj in (PHY, CHE):
        assert c.put(f"{API}/batches/{A}/subjects/{sj}", headers=h).status_code == 200
    PA = invite(c, h, "Priya Admin", "priya@north.example.com", "PROVIDER_ADMIN", P)
    TC = invite(c, h, "Chandra Chemistry", "chandra@north.example.com", "TEACHER", P)
    h = login(c, "STF000001")
    r = c.post(f"{API}/teaching", json={"staff_id": TC["staff_id"], "branch_id": B1, "subject_id": CHE}, headers=h)   # whole branch
    assert r.status_code in (200, 201), r.text

    def enroll(first, mail):
        r = c.post(f"{API}/branches/{B1}/enrollments", json={"academic": ACAD, "new_student": {"first_name": first, "last_name": "Existing", "email": mail}, "batch_ids": [A]}, headers=h)
        assert r.status_code == 201, r.text
        return r.json()
    KEEP = enroll("Kiran", "kiran@family.example.com")      # existing membership, never touched: every subject
    MAN = enroll("Meera", "meera@family.example.com")       # existing membership, changed to Chemistry in the Manage dialog
    with factory() as db:
        reference_sync.run_once(db, full=True)
    hp = login(c, PA["staff_code"])
    PV_PHY = published_paper(c, hp, B1, PHY, "P", "Physics Set")
    PV_CHE = published_paper(c, hp, B1, CHE, "C", "Chemistry Set")
    check("set-up: batch NEET A offers Physics and Chemistry; 2 existing students without a selection; papers published",
          sql("select count(*) from enrollment_batch_subjects")[0][0] == 0
          and sorted(s["name"] for s in c.get(f"{API}/batches/{A}/subjects", headers=hp).json()) == ["Chemistry", "Physics"])

# ------------------------------------------------------------------ serve the portal build and the API on one origin
DIST = WORK / "dist"
build = subprocess.run(["npx", "vite", "build", "--outDir", str(DIST), "--emptyOutDir"], cwd=ROOT / "institute-frontend",
                       env={**os.environ, "VITE_INSTITUTE_API_URL": ""}, capture_output=True, text=True, check=False)
check("production build of the Institute portal (same-origin API)", build.returncode == 0 and (DIST / "index.html").exists(), build.stderr[-500:])


async def site(scope, receive, send):
    """The portal on one origin, as Cloudflare Pages serves it: /api/* to the gateway, everything else the SPA."""
    if scope["type"] != "http" or scope["path"].startswith("/api/"):
        return await gateway(scope, receive, send)
    rel = scope["path"].lstrip("/")
    target = DIST / rel if rel and (DIST / rel).is_file() else DIST / "index.html"
    body = target.read_bytes()
    kind = {".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".html": "text/html"}.get(target.suffix, "application/octet-stream")
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", kind.encode()), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


server = uvicorn.Server(uvicorn.Config(site, host="0.0.0.0", port=PORT, log_level="warning", lifespan="off"))
threading.Thread(target=server.run, daemon=True).start()
for _ in range(40):
    try:
        if httpx.get(f"http://127.0.0.1:{PORT}/login", timeout=2).status_code == 200:
            break
    except httpx.HTTPError:
        time.sleep(0.25)

# ------------------------------------------------------------------ Chromium in the Playwright container (localhost:5174 -> this server)
PW_PYTHON = os.environ.get("PW_PYTHON", "python3")
pkgs = subprocess.run([PW_PYTHON, "-c", "import os, playwright; print(os.path.dirname(os.path.dirname(playwright.__file__)))"],
                      capture_output=True, text=True, check=True).stdout.strip()
subprocess.run(["docker", "rm", "-f", "se-pw"], capture_output=True, check=False)
subprocess.run(["docker", "run", "-d", "--name", "se-pw", "--add-host", "host.docker.internal:host-gateway", "-p", f"127.0.0.1:{PW_PORT}:3000",
                "-v", f"{pkgs}:/pwpkgs:ro", "-e", "PYTHONPATH=/pwpkgs", PW_IMAGE, "python3", "-m", "playwright", "run-server", "--port", "3000",
                "--host", "0.0.0.0"], check=True, capture_output=True)
for _ in range(60):
    try:
        if httpx.get(f"http://127.0.0.1:{PW_PORT}/", timeout=2).status_code == 200:
            break
    except httpx.HTTPError:
        time.sleep(0.5)
FWD = (f"import asyncio\nasync def pipe(r, w):\n    try:\n        while data := await r.read(65536):\n            w.write(data); await w.drain()\n"
       f"    finally:\n        w.close()\nasync def h(r, w):\n    ur, uw = await asyncio.open_connection('host.docker.internal', {PORT})\n"
       f"    await asyncio.gather(pipe(r, uw), pipe(ur, w), return_exceptions=True)\n"
       f"async def main():\n    s = await asyncio.start_server(h, '127.0.0.1', 5174)\n    await s.serve_forever()\nasyncio.run(main())\n")
subprocess.run(["docker", "exec", "-d", "se-pw", "python3", "-c", FWD], check=True)
time.sleep(2)

params = {"ui": "http://localhost:5174", "ws": f"ws://127.0.0.1:{PW_PORT}/", "mail_log": str(WORK / "mail.log"), "password": PW,
          "shots": str(Path(os.environ.get("UI_SHOTS", WORK / "shots"))), "pa": PA["staff_code"]}
(WORK / "ui_params.json").write_text(json.dumps(params))
print("\nbrowser journeys (Chromium)")
browser = subprocess.run([PW_PYTHON, str(ROOT / "tests" / "e2e" / "subject_enrollment_ui_browser.py"), str(WORK / "ui_params.json")], check=False)
check("every browser journey passed (see the list above)", browser.returncode == 0)

# ------------------------------------------------------------------ what the browser did, checked in the database and through the API
print("\npersisted results")
chosen = lambda sid: sorted(n for (n,) in sql("""select s.name from enrollment_batch_subjects x join enrollment_batches eb on eb.enrollment_batch_id = x.enrollment_batch_id
                                                 join enrollments e on e.enrollment_id = eb.enrollment_id join subjects s on s.subject_id = x.subject_id
                                                 where e.student_id = :s""", s=sid))
NEW = sql("select e.student_id from enrollments e where e.student_id not in (:a, :b)", a=KEEP["student_id"], b=MAN["student_id"])
check("the student enrolled in the browser exists once (new global Student ID)", len(NEW) == 1, NEW)
NEW = NEW[0][0]
check("enrollment form: the new student's membership takes Physics only", chosen(NEW) == ["Physics"], chosen(NEW))
check("Manage dialog: the existing student's membership now takes Chemistry only", chosen(MAN["student_id"]) == ["Chemistry"], chosen(MAN["student_id"]))
check("the untouched existing membership still has no selection rows (every subject)", chosen(KEEP["student_id"]) == [])
events = sql("select e.student_id, ev.reason from enrollment_events ev join enrollments e on e.enrollment_id = ev.enrollment_id where ev.event_type = 'BATCH_SUBJECTS_SET' order by ev.occurred_at")
check("enrollment history records each selection", sorted(events) == sorted([(NEW, "Subjects: Physics"), (MAN["student_id"], "Subjects: Chemistry")]), events)

with TestClient(gateway, base_url="http://localhost") as c:
    h = login(c, "STF000001")
    actions = [x["action"] for x in c.get(f"{API}/audit-logs?page_size=100", headers=h).json()["items"]]
    check("audit: the enrollment and the subject change are recorded", "enrollment.create" in actions and "enrollment.batch_subjects" in actions, actions[:15])

    hp = login(c, PA["staff_code"])

    def test(name, subjects, students=()):
        start = datetime.now(UTC) + timedelta(hours=2)
        body = {"audience": {"provider_id": P, "branch_ids": [B1], "whole_branch_ids": [], "batch_ids": [A], "student_ids": list(students)},
                "name": name, "description": None, "subjects": [{"subject_id": s, "paper_version_id": v} for s, v in subjects], "duration_minutes": 30,
                "start_at": start.isoformat(), "end_at": (start + timedelta(hours=1)).isoformat(), "marking_mode": "UNIFORM", "marks_correct": 4, "marks_wrong": 1}
        r = c.post(f"{API}/tests", json=body, headers={**hp, "Idempotency-Key": f"se-{uuid.uuid4()}"})
        assert r.status_code == 201, r.text
        exam = r.json()["exam_id"]
        return exam, sorted(s for (s,) in sql("select student_id from student_exam_assignments where exam_id = :e", e=exam))
    _, phy = test("Physics only", [(PHY, PV_PHY)])
    _, che = test("Chemistry only", [(CHE, PV_CHE)])
    both_exam, both = test("Physics and Chemistry", [(PHY, PV_PHY), (CHE, PV_CHE)])
    over_exam, over = test("Listed override", [(PHY, PV_PHY), (CHE, PV_CHE)], students=(NEW,))
    check("Physics test: the Physics-only student and the untouched student", phy == sorted([NEW, KEEP["student_id"]]), phy)
    check("Chemistry test: the Chemistry-only student and the untouched student", che == sorted([MAN["student_id"], KEEP["student_id"]]), che)
    check("Physics + Chemistry test: only the untouched student (takes every subject)", both == [KEEP["student_id"]], both)
    check("a student listed by staff is an override", over == sorted([NEW, KEEP["student_id"]]), over)
    ht = login(c, TC["staff_code"])
    seen = sorted(a["student_id"] for a in c.get(f"{API}/exams/{over_exam}/assignments", headers=ht).json()["items"])
    check("the Chemistry teacher sees only students taking Chemistry (not the Physics-only student)", seen == [KEEP["student_id"]], seen)

summary()
