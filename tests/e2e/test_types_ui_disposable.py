"""Daily, Weekly and Global Tests in a REAL browser, on DISPOSABLE infrastructure (run: tests/e2e/pipeline_disposable.sh test_types_ui_disposable.py).

The Institute services run in-process behind the in-process gateway, served over HTTP together with a production build of the Institute
portal (same origin, as on Cloudflare Pages); the six real Student services run as processes on a throwaway PostgreSQL. Chromium
(Playwright run-server container) drives the portal: tests/e2e/test_types_ui_browser.py. This driver creates the starting data through
the APIs (the only seeded row is the platform owner), then checks in the database and through the Student services what the browser
did: persisted records, idempotency, generated exams, delivery to eligible students only, audit and notifications.

Needs: Docker, the Playwright image mcr.microsoft.com/playwright/python:v1.48.0-noble, npm, and a Python with playwright==1.48 for the
browser script (PW_PYTHON, default: python3).
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
PORT, PW_PORT, PW_IMAGE = 18190, int(os.environ.get("UI_PW_PORT", "3011")), "mcr.microsoft.com/playwright/python:v1.48.0-noble"
STU = {"auth": "http://127.0.0.1:18102", "enroll": "http://127.0.0.1:18104", "test": "http://127.0.0.1:18105",
       "attempt": "http://127.0.0.1:18106", "result": "http://127.0.0.1:18107", "notify": "http://127.0.0.1:18108"}
for name, url in (("STUDENT_SERVICE", STU["auth"]), ("STUDENT_ENROLLMENT_SERVICE", STU["enroll"]), ("STUDENT_TEST_SERVICE", STU["test"]),
                  ("STUDENT_ATTEMPT_SERVICE", STU["attempt"]), ("STUDENT_RESULT_SERVICE", STU["result"]), ("STUDENT_NOTIFICATION_SERVICE", STU["notify"])):
    os.environ[f"{name}_URL"], os.environ[f"{name}_API_KEY"] = url, KEY

sys.path.insert(0, str(ROOT / "institute-backend" / "institute-integration-tests"))
import fakeredis  # noqa: E402
import httpx  # noqa: E402
import tests.conftest  # noqa: E402,F401  (service paths + test settings; the database is overridden below)
import uvicorn  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from inst_enrollment.services import reference_sync  # noqa: E402
from inst_exam.services import exam_generation as gen  # noqa: E402
from inst_exam.services import notify, outbox  # noqa: E402
from institute_common.core import email, redis_store  # noqa: E402
from institute_common.core.passwords import hash_password  # noqa: E402
from institute_common.database import get_db  # noqa: E402
from institute_common.internal_http import set_peer_client  # noqa: E402
from institute_common.models.staff import Staff, StaffRoleAssignment  # noqa: E402
from institute_common.ownership import attach_guard  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from tests.gateway import SERVICES, app as gateway  # noqa: E402

ORIGIN, API, PW = {"Origin": "http://localhost:5174"}, "/api/v1", "TestTypes-" + uuid4().hex
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
    subprocess.run(["docker", "rm", "-f", "tt-pw"], capture_output=True)
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


def published_paper(c, h, branch, subject, prefix, n, title):
    rows = [f"{prefix}{i:03d},{title} question {i}?,A{i},B{i},C{i},D{i},1,0," for i in range(1, n + 1)]
    paper = "question_id,question,option_a,option_b,option_c,option_d,marks,negative_marks,explanation\n" + "\n".join(rows) + "\n"
    key = "question_id,correct_option,explanation\n" + "\n".join(f"{prefix}{i:03d},{'ABCD'[(i - 1) % 4]},Reason {i}" for i in range(1, n + 1)) + "\n"
    files = {"file": ("paper.csv", paper.encode(), "text/csv"), "answer_key": ("key.csv", key.encode(), "text/csv")}
    r = c.post(f"{API}/branches/{branch}/subjects/{subject}/papers/upload", files=files, data={"title": title}, headers=h)
    assert r.status_code == 201, r.text
    pv = r.json()["paper_version_id"]
    assert c.post(f"{API}/paper-versions/{pv}/approve", json={"note": "ok"}, headers=h).status_code == 200
    assert c.post(f"{API}/paper-versions/{pv}/publish", json={"note": None}, headers=h).status_code == 200
    return pv


USED: set[str] = set()


def student_session(email_, sid):
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
    s.post(STU["auth"] + "/auth/reset-password", json={"token": tok, "new_password": PW})
    ok = s.post(STU["auth"] + "/auth/login", json={"login": sid, "password": PW}).status_code == 200
    return s, ok


# ------------------------------------------------------------------ starting data, through the APIs
with TestClient(gateway, base_url="http://localhost") as c:
    print("set-up through the Institute API")
    h = login(c, "STF000001")
    P = c.post(f"{API}/providers", json={"name": "North Academy", "email": "office@north.example.com"}, headers=h).json()["provider_id"]
    P2 = c.post(f"{API}/providers", json={"name": "South Coaching", "email": "office@south.example.com"}, headers=h).json()["provider_id"]
    B1 = c.post(f"{API}/providers/{P}/branches", json={"name": "Pune", "city": "Pune", "state": "MH"}, headers=h).json()["branch_id"]
    B2 = c.post(f"{API}/providers/{P}/branches", json={"name": "Mumbai", "city": "Mumbai", "state": "MH"}, headers=h).json()["branch_id"]
    B3 = c.post(f"{API}/providers/{P2}/branches", json={"name": "Nagpur", "city": "Nagpur", "state": "MH"}, headers=h).json()["branch_id"]
    PHY = c.post(f"{API}/subjects", json={"code": "PHY", "name": "Physics"}, headers=h).json()["subject_id"]
    CHE = c.post(f"{API}/subjects", json={"code": "CHE", "name": "Chemistry"}, headers=h).json()["subject_id"]
    for prov in (P, P2):
        for sj in (PHY, CHE):
            c.put(f"{API}/providers/{prov}/subjects/{sj}", json={}, headers=h)
    A = c.post(f"{API}/branches/{B1}/batches", json={"name": "NEET A"}, headers=h).json()["batch_id"]
    C = c.post(f"{API}/branches/{B2}/batches", json={"name": "JEE C"}, headers=h).json()["batch_id"]
    D = c.post(f"{API}/branches/{B3}/batches", json={"name": "Foundation D"}, headers=h).json()["batch_id"]
    PA = invite(c, h, "Priya Admin", "priya@north.example.com", "PROVIDER_ADMIN", P)
    BA = invite(c, h, "Bharat Branch", "bharat@north.example.com", "BRANCH_ADMIN", P, B1)
    T = invite(c, h, "Tara Teacher", "tara@north.example.com", "TEACHER", P)
    T2 = invite(c, h, "Chandra Chemistry", "chandra@north.example.com", "TEACHER", P)
    PA2 = invite(c, h, "Sohan Admin", "sohan@south.example.com", "PROVIDER_ADMIN", P2)
    h = login(c, "STF000001")
    for staff, branch, subject in ((T, B1, PHY), (T2, B2, CHE)):       # Physics in Pune; Chemistry in Mumbai only
        r = c.post(f"{API}/teaching", json={"staff_id": staff["staff_id"], "branch_id": branch, "subject_id": subject}, headers=h)
        assert r.status_code in (200, 201), r.text

    def enroll(branch, first, mail, batch_ids, sid=None):
        body = {"student_id": sid, "batch_ids": batch_ids} if sid else {"new_student": {"first_name": first, "last_name": "Test", "email": mail}, "batch_ids": batch_ids}
        r = c.post(f"{API}/branches/{branch}/enrollments", json={**body, "academic": ACAD}, headers=h)
        assert r.status_code == 201, r.text
        return r.json()["student_id"]
    S1 = enroll(B1, "Asha", "asha@family.example.com", [A])            # Pune, NEET A
    S2 = enroll(B1, "Bilal", "bilal@family.example.com", [A])          # Pune, NEET A
    S3 = enroll(B2, "Chitra", "chitra@family.example.com", [C])        # Mumbai only: never targeted below
    enroll(B3, "", "", [D], sid=S1)                                     # S1 also studies with South Coaching (one identity)
    with factory() as db:
        reference_sync.run_once(db, full=True)

    hp = login(c, PA["staff_code"])
    PV_PHY = published_paper(c, hp, B1, PHY, "P", 3, "North Physics Set")
    PV_CHE = published_paper(c, hp, B1, CHE, "C", 3, "North Chemistry Set")
    hp2 = login(c, PA2["staff_code"])
    PV_CHE2 = published_paper(c, hp2, B3, CHE, "S", 2, "South Chemistry Set")

    # A published Global Test (Physics) built by the platform from its own questions.
    h = login(c, "STF000001")
    qids = []
    for i in range(2):
        r = c.post(f"{API}/questions", json={"scope": "GLOBAL", "subject_id": PHY, "stem": f"National physics question {i}?", "option_a": "a", "option_b": "b",
                                             "option_c": "c", "option_d": "d", "correct_option": "A", "marks_positive": 4, "marks_negative": 1}, headers=h)
        assert r.status_code == 201, r.text
        qids.append(r.json()["question_id"])
    gpv = c.post(f"{API}/papers", json={"scope": "GLOBAL", "subject_id": PHY, "title": "National Physics", "question_ids": qids}, headers=h).json()["paper_version_id"]
    c.post(f"{API}/paper-versions/{gpv}/approve", json={}, headers=h)
    c.post(f"{API}/paper-versions/{gpv}/publish", json={}, headers=h)
    GT = c.post(f"{API}/global-tests", json={"name": "National Mock", "duration_minutes": 60, "sections": [{"paper_version_id": gpv}]}, headers=h).json()["test_id"]
    check("set-up: 2 providers, Branch Admin, Teacher, 3 students, 3 provider papers, a published Global Test",
          c.post(f"{API}/global-tests/{GT}/publish", headers=h).json()["status"] == "PUBLISHED")

# ------------------------------------------------------------------ serve the portal build and the API on one origin
DIST = WORK / "dist"
build = subprocess.run(["npx", "vite", "build", "--outDir", str(DIST), "--emptyOutDir"], cwd=ROOT / "institute-frontend",
                       env={**os.environ, "VITE_INSTITUTE_API_URL": ""}, capture_output=True, text=True)
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
# The image has the browsers but not the Python package: the browser script's own playwright package (and its bundled driver) is
# mounted read-only and serves the browsers from inside the container.
PW_PYTHON = os.environ.get("PW_PYTHON", "python3")
pkgs = subprocess.run([PW_PYTHON, "-c", "import os, playwright; print(os.path.dirname(os.path.dirname(playwright.__file__)))"],
                      capture_output=True, text=True, check=True).stdout.strip()
subprocess.run(["docker", "rm", "-f", "tt-pw"], capture_output=True)
subprocess.run(["docker", "run", "-d", "--name", "tt-pw", "--add-host", "host.docker.internal:host-gateway", "-p", f"127.0.0.1:{PW_PORT}:3000",
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
subprocess.run(["docker", "exec", "-d", "tt-pw", "python3", "-c", FWD], check=True)
time.sleep(2)

params = {"ui": "http://localhost:5174", "ws": f"ws://127.0.0.1:{PW_PORT}/", "mail_log": str(WORK / "mail.log"), "password": PW,
          "shots": str(Path(os.environ.get("UI_SHOTS", WORK / "shots"))),
          "sa": "STF000001", "pa": PA["staff_code"], "ba": BA["staff_code"], "teacher": T["staff_code"], "teacher2": T2["staff_code"], "pa2": PA2["staff_code"]}
(WORK / "ui_params.json").write_text(json.dumps(params))
print("\nbrowser journeys (Chromium)")
browser = subprocess.run([PW_PYTHON, str(ROOT / "tests" / "e2e" / "test_types_ui_browser.py"), str(WORK / "ui_params.json")])
check("every browser journey passed (see the list above)", browser.returncode == 0)

# ------------------------------------------------------------------ what the browser did, checked in the database and the Student services
print("\npersisted results")
daily = sql("select plan_id, created_by, series_id, global_test_id from exam_plans where name = 'Bank Physics Daily'")
check("Daily Test: one plan, created by the Provider Admin, neither weekly nor global", len(daily) == 1 and str(daily[0][1]) == PA["staff_id"]
      and daily[0][2] is None and daily[0][3] is None, daily)
d_exam = sql("select e.exam_id, e.status, v.exam_version_id from exams e join exam_versions v on v.exam_id = e.exam_id where e.plan_id = :p", p=daily[0][0])
assigned = sorted(s for (s,) in sql("select student_id from student_exam_assignments where exam_id = :e", e=d_exam[0][0]))
check("Daily Test exam PUBLISHED with exactly the NEET A students and the chosen bank paper", d_exam[0][1] == "PUBLISHED" and assigned == sorted([S1, S2])
      and sql("select paper_version_id from exam_version_papers where exam_version_id = :v", v=d_exam[0][2])[0][0].__str__() == PV_PHY, (d_exam, assigned))

series = sql("select series_id, created_by, month from test_series")
check("Weekly Test: one series, created by the Branch Admin", len(series) == 1 and str(series[0][1]) == BA["staff_id"], series)
plans = sql("select plan_id, start_at, generate_at from exam_plans where series_id = :s order by start_at", s=series[0][0])
starts = [p[1] for p in plans]
check("Weekly Test: one plan per Saturday of the month, no duplicates after the retry in the browser", len(plans) >= 4 and len(set(starts)) == len(starts)
      and all(s.weekday() == 5 or (s + timedelta(hours=5, minutes=30)).weekday() == 5 for s in starts), [str(s) for s in starts])
with factory() as db:
    jobs = gen.run_due_plans(db, now=plans[0][2] + timedelta(minutes=1))
check("Weekly Test: the worker generates the first occurrence's exam at its generate time (normal generator)", [j.status for j in jobs] == ["SUCCEEDED"], jobs)
with factory() as db:
    again = gen.run_due_plans(db, now=plans[0][2] + timedelta(minutes=2))
check("Weekly Test: a repeated worker run creates nothing", again == [] and sql("select count(*) from exams where plan_id = :p", p=plans[0][0])[0][0] == 1)

contribs = {r[0]: r[1:] for r in sql("select p.name, c.status, c.copy_status, c.global_paper_version_id from global_test_contributions c join providers p on p.provider_id = c.provider_id")}
check("Global Test: North's contribution APPROVED and copied, South's REJECTED (conflict resolved explicitly)",
      contribs.get("North Academy", ())[:2] == ("APPROVED", "COPIED") and contribs.get("South Coaching", ())[0] == "REJECTED", contribs)
copy_v = contribs["North Academy"][2]
copy = sql("select p.scope, p.provider_id, v.status from paper_versions v join papers p on p.paper_id = v.paper_id where v.paper_version_id = :v", v=copy_v)
check("Global Test: the copy is an independent GLOBAL paper, approved and published through the paper workflow in the browser",
      copy and copy[0][0] == "GLOBAL" and copy[0][1] is None and copy[0][2] == "PUBLISHED", copy)
check("Global Test: the provider's own paper is untouched", sql("select status from paper_versions where paper_version_id = :v", v=PV_CHE)[0][0] == "PUBLISHED")
orig = sql("select count(*) from global_test_sections where test_id = :t", t=GT)[0][0]
derived = sql("select test_id, status from global_tests where derived_from_test_id = :t", t=GT)
dsections = sql("select subject_id, paper_version_id from global_test_sections where test_id = :t order by position", t=derived[0][0]) if derived else []
check("Global Test: the published test still has its single section; the new version (derived, published) has Physics + the copied Chemistry",
      orig == 1 and derived and derived[0][1] == "PUBLISHED" and [str(v) for _, v in dsections] == [gpv, str(copy_v)], (orig, derived, dsections))

with TestClient(gateway, base_url="http://localhost") as c:
    g_plan = sql("select plan_id, created_by, branch_id from exam_plans where global_test_id = :t", t=derived[0][0])
    check("the provider scheduled the new Global Test version in the browser (one plan, its branch)", len(g_plan) == 1
          and str(g_plan[0][1]) == PA["staff_id"] and str(g_plan[0][2]) == B1, g_plan)
    g_ev = sql("select v.exam_version_id from exam_versions v join exams e on e.exam_id = v.exam_id where e.plan_id = :p", p=g_plan[0][0])[0][0]
    papers = [str(v) for (v,) in sql("select paper_version_id from exam_version_papers where exam_version_id = :v order by position", v=g_ev)]
    g_students = sorted(s for (s,) in sql("select a.student_id from student_exam_assignments a join exams e on e.exam_id = a.exam_id where e.plan_id = :p", p=g_plan[0][0]))
    check("its exam (generated from the Global Test page) uses exactly the test's papers and the NEET A students",
          papers == [gpv, str(copy_v)] and g_students == sorted([S1, S2]), (papers, g_students))
    with factory() as db:
        delivered = outbox.deliver_due(db)
        notify.deliver_pending(db)
    check("published exams delivered to the Student test service", delivered.get("failed", 0) == 0 and delivered.get("sent", 0) >= 3, delivered)

    s1, ok1 = student_session("asha@family.example.com", S1)
    s3, ok3 = student_session("chitra@family.example.com", S3)
    check("students sign in through the real Student auth service", ok1 and ok3)
    def visible(sess):   # open now + upcoming (the student portal's Available and Scheduled lists)
        return {x["test_id"]: x for path in ("/tests", "/tests/scheduled") for x in sess.get(STU["test"] + path).json()}
    mine = visible(s1)
    weekly_ev = sql("select v.exam_version_id from exam_versions v join exams e on e.exam_id = v.exam_id where e.plan_id = :p", p=plans[0][0])[0][0]
    expected = {str(d_exam[0][2]): "Daily", str(weekly_ev): "Weekly", str(g_ev): "Global"}
    check("the eligible student sees the Daily, Weekly and Global tests, each with its provider",
          all(k in mine and mine[k]["provider"]["name"] == "North Academy" for k in expected), {k: (k in mine) for k in expected})
    other = set(visible(s3))
    check("a student of another branch sees none of them", not (set(expected) & other), other)
    subjects = sorted(s for (s,) in sql("select distinct subject from questions where test_id::text = :t", t=str(g_ev)))
    check("the delivered Global Test carries both subjects", subjects == ["Chemistry", "Physics"], subjects)

    h = login(c, "STF000001")
    actions = {x["action"] for x in c.get(f"{API}/audit-logs?page_size=100", headers=h).json()["items"]}
    wanted = {"test.create", "test.publish", "test_series.create", "global_test.contribution.submit", "global_test.contribution.approve",
              "global_test.contribution.reject", "global_test.contribution.copy", "global_test.derive", "global_test.publish", "paper.approved", "paper.published"}
    check("audit log records every step (test, series, contributions, copy, review, publication)", wanted <= actions, sorted(wanted - actions))
    kinds = sql("select payload->>'kind' from exam_notification_outbox")
    check("notifications queued: new submissions for the platform, decisions for the submitters", {"GLOBAL_CONTRIBUTION_SUBMITTED", "GLOBAL_CONTRIBUTION_APPROVED",
          "GLOBAL_CONTRIBUTION_REJECTED"} <= {k for (k,) in kinds}, kinds)

summary()
