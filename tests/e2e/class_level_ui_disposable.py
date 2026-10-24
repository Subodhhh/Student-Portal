"""Class / Academic Year in a REAL browser, on DISPOSABLE infrastructure (run: tests/e2e/pipeline_disposable.sh class_level_ui_disposable.py).

Same set-up as subject_enrollment_ui_disposable.py (in-process Institute services + production portal build on one origin, real Student
services on a throwaway PostgreSQL, Chromium in the Playwright container running tests/e2e/class_level_ui_browser.py). The browser creates
a batch with Class defaults, enrolls students (prefill from defaults, conflicting defaults, override), and completes a legacy enrollment that
has no Class. The driver then checks the stored profiles, history, audit, Class-aware exam audiences, staff overrides and teacher visibility.

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
PORT, PW_PORT, PW_IMAGE = 18192, int(os.environ.get("UI_PW_PORT", "3013")), "mcr.microsoft.com/playwright/python:v1.48.0-noble"
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

ORIGIN, API, PW = {"Origin": "http://localhost:5174"}, "/api/v1", "ClassLevel-" + uuid4().hex
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
    subprocess.run(["docker", "rm", "-f", "cl-pw"], capture_output=True, check=False)
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
    A = c.post(f"{API}/branches/{B1}/batches", json={"name": "NEET 11", "default_class_level": "11th", "default_academic_year": "2026-2027"}, headers=h).json()["batch_id"]
    B = c.post(f"{API}/branches/{B1}/batches", json={"name": "NEET 12", "default_class_level": "12th", "default_academic_year": "2026-2027"}, headers=h).json()["batch_id"]
    for batch in (A, B):
        for sj in (PHY, CHE):
            assert c.put(f"{API}/batches/{batch}/subjects/{sj}", headers=h).status_code == 200
    PA = invite(c, h, "Priya Admin", "priya@north.example.com", "PROVIDER_ADMIN", P)
    TC = invite(c, h, "Chandra Chemistry", "chandra@north.example.com", "TEACHER", P)
    h = login(c, "STF000001")
    r = c.post(f"{API}/teaching", json={"staff_id": TC["staff_id"], "branch_id": B1, "subject_id": CHE}, headers=h)   # whole branch
    assert r.status_code in (200, 201), r.text

    def enroll(first, mail, batch, cls):
        r = c.post(f"{API}/branches/{B1}/enrollments", json={"new_student": {"first_name": first, "last_name": "Existing", "email": mail}, "batch_ids": [batch],
                                                             "academic": {"class_level": cls, "academic_year": "2026-2027"}}, headers=h)
        assert r.status_code == 201, r.text
        return r.json()
    KEEP = enroll("Kiran", "kiran@family.example.com", A, "11th")            # existing, complete: 11th
    LEGACY = enroll("Lata", "lata@family.example.com", A, "11th")
    with engine.begin() as conn:     # simulate an enrollment made before Classes existed (disposable database only)
        conn.execute(text("delete from enrollment_academic_profiles where enrollment_id = :e"), {"e": LEGACY["enrollment_id"]})
    with factory() as db:
        reference_sync.run_once(db, full=True)
    hp = login(c, PA["staff_code"])
    PV_PHY = published_paper(c, hp, B1, PHY, "P", "Physics Set")
    PV_CHE = published_paper(c, hp, B1, CHE, "C", "Chemistry Set")
    check("set-up: batches NEET 11 / NEET 12 with Class defaults; one complete and one legacy (no Class) enrollment; papers published",
          sql("select count(*) from enrollment_academic_profiles")[0][0] == 1 and sql("select count(*) from batch_academic_defaults")[0][0] == 2)

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
subprocess.run(["docker", "rm", "-f", "cl-pw"], capture_output=True, check=False)
subprocess.run(["docker", "run", "-d", "--name", "cl-pw", "--add-host", "host.docker.internal:host-gateway", "-p", f"127.0.0.1:{PW_PORT}:3000",
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
subprocess.run(["docker", "exec", "-d", "cl-pw", "python3", "-c", FWD], check=True)
time.sleep(2)

params = {"ui": "http://localhost:5174", "ws": f"ws://127.0.0.1:{PW_PORT}/", "mail_log": str(WORK / "mail.log"), "password": PW,
          "shots": str(Path(os.environ.get("UI_SHOTS", WORK / "shots"))), "pa": PA["staff_code"]}
(WORK / "ui_params.json").write_text(json.dumps(params))
print("\nbrowser journeys (Chromium)")
browser = subprocess.run([PW_PYTHON, str(ROOT / "tests" / "e2e" / "class_level_ui_browser.py"), str(WORK / "ui_params.json")], check=False)
check("every browser journey passed (see the list above)", browser.returncode == 0)

# ------------------------------------------------------------------ what the browser did, checked in the database and through the API
print("\npersisted results")


def profile(first):
    rows = sql("""select p.class_level, p.academic_year, p.other_class_description, e.student_id from enrollment_academic_profiles p
                  join enrollments e on e.enrollment_id = p.enrollment_id where e.student_id in (select student_id from students where first_name = :f)""", f=first)
    return rows[0] if len(rows) == 1 else rows


NISHA, OM, LATA = profile("Nisha"), profile("Om"), profile("Lata")
check("enrollment form: prefilled from NEET 11's defaults and saved as 11th / 2026-2027", tuple(NISHA[:3]) == ("11TH", "2026-2027", None), NISHA)
check("conflicting batch defaults: the explicitly chosen 12th was saved", tuple(OM[:3]) == ("12TH", "2026-2027", None), OM)
check("Manage dialog: the legacy enrollment now has Other (NIOS bridge) / 2027-2028", tuple(LATA[:3]) == ("OTHER", "2027-2028", "NIOS bridge"), LATA)
check("the complete existing profile is untouched", tuple(profile("Kiran")[:3]) == ("11TH", "2026-2027", None))
batch = sql("select d.default_class_level, d.default_academic_year from batch_academic_defaults d join batches b on b.batch_id = d.batch_id where b.name = 'JEE 12'")
check("New batch: JEE 12 saved with its Class defaults", batch == [("12TH", "2027-2028")], batch)
events = [r for (r,) in sql("select reason from enrollment_events where event_type = 'ACADEMIC_PROFILE_SET' order by occurred_at")]
check("enrollment history records every Class change (including the legacy completion)", "Class Other (NIOS bridge), 2027-2028" in events and len(events) >= 4, events)
NEW, OMID, LEGID, KEEPID = NISHA[3], OM[3], LATA[3], KEEP["student_id"]

with TestClient(gateway, base_url="http://localhost") as c:
    h = login(c, "STF000001")
    actions = [x["action"] for x in c.get(f"{API}/audit-logs?page_size=100", headers=h).json()["items"]]
    check("audit: enrollment create, profile change and batch create recorded", {"enrollment.create", "enrollment.academic_profile", "batch.create"} <= set(actions))
    hp = login(c, PA["staff_code"])

    def test(name, subjects, cls, students=()):
        start = datetime.now(UTC) + timedelta(hours=2)
        body = {"audience": {"provider_id": P, "branch_ids": [B1], "whole_branch_ids": [], "batch_ids": [A, B], "student_ids": list(students)},
                "name": name, "description": None, "subjects": [{"subject_id": s, "paper_version_id": v} for s, v in subjects], "duration_minutes": 30,
                "start_at": start.isoformat(), "end_at": (start + timedelta(hours=1)).isoformat(), "marking_mode": "UNIFORM", "marks_correct": 4, "marks_wrong": 1,
                "target_class": cls}
        r = c.post(f"{API}/tests", json=body, headers={**hp, "Idempotency-Key": f"cl-{uuid.uuid4()}"})
        assert r.status_code == 201, r.text
        exam = r.json()["exam_id"]
        return exam, sorted(s for (s,) in sql("select student_id from student_exam_assignments where exam_id = :e", e=exam))
    _, p11 = test("Physics 11", [(PHY, PV_PHY)], "11th")
    _, p12 = test("Physics 12", [(PHY, PV_PHY)], "12th")
    _, anyc = test("Physics any class", [(PHY, PV_PHY)], None)
    over_exam, over = test("Physics 12 + listed", [(PHY, PV_PHY)], "12th", students=(NEW,))
    chem_exam, _chem = test("Chemistry 11", [(CHE, PV_CHE)], "11th")
    check("11th exam: only the 11th students (new + existing), not the 12th or the Other student", p11 == sorted([NEW, KEEPID]), p11)
    check("12th exam: only the 12th student", p12 == [OMID], p12)
    check("exam without a Class: every student of the batches", anyc == sorted([NEW, KEEPID, OMID, LEGID]), anyc)
    check("staff-listed student overrides the Class", over == sorted([OMID, NEW]), over)
    bad = c.post(f"{API}/tests/audience", json={"provider_id": P, "branch_ids": [B1], "whole_branch_ids": [], "batch_ids": [A], "student_ids": [],
                                                "target_class": "Class 11"}, headers=hp)
    check("an unrecognised (legacy-style) class label is refused, never guessed", bad.status_code == 422, bad.status_code)
    ht = login(c, TC["staff_code"])
    seen = sorted(a["student_id"] for a in c.get(f"{API}/exams/{chem_exam}/assignments", headers=ht).json()["items"])
    check("the Chemistry teacher sees the Chemistry 11 students only", seen == sorted([NEW, KEEPID]), seen)

summary()
