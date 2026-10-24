"""Account lifecycle, end to end, on DISPOSABLE infrastructure (run through: tests/e2e/pipeline_disposable.sh lifecycle_disposable.py).

The Institute services run in-process (the integration-test gateway) with REAL HTTP clients to the real Student services. Starting
from the only seeded row (a Super Admin, like the reset shared database), everything is created through the APIs exactly as the UI
does it, and each step is checked through the API AND in the database:

  Super Admin -> providers P1, P2 -> Provider Admin of P1 (invite, e-mail verification sets the password, MFA sign-in) -> branches
  B1, B2 with a batch of the SAME NAME in each -> Branch Admin of B1 -> Teacher (subject + batch) -> students (global identity by the
  real Student service; a second provider enrolls the same person without a duplicate) -> student password setup and sign-in ->
  audience resolution for a test aimed at B1's batch (B2's same-name batch never included) -> backend authorization boundaries ->
  deactivation and role removal take effect immediately.
"""
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

DB, KEY, AUTH_LOG = os.environ["PIPELINE_DB"], os.environ["PIPELINE_KEY"], Path(os.environ["PIPELINE_AUTH_LOG"])
if not re.search(r"@127\.0\.0\.1:\d+/pipeline_verify$", DB):
    sys.exit("refusing: disposable pipeline database only")
ROOT = Path(__file__).resolve().parents[2]
STU = {"auth": "http://127.0.0.1:18102", "enroll": "http://127.0.0.1:18104", "test": "http://127.0.0.1:18105",
       "attempt": "http://127.0.0.1:18106", "result": "http://127.0.0.1:18107", "notify": "http://127.0.0.1:18108"}
for name, url in (("STUDENT_SERVICE", STU["auth"]), ("STUDENT_ENROLLMENT_SERVICE", STU["enroll"]), ("STUDENT_TEST_SERVICE", STU["test"]),
                  ("STUDENT_ATTEMPT_SERVICE", STU["attempt"]), ("STUDENT_RESULT_SERVICE", STU["result"]), ("STUDENT_NOTIFICATION_SERVICE", STU["notify"])):
    os.environ[f"{name}_URL"], os.environ[f"{name}_API_KEY"] = url, KEY

sys.path.insert(0, str(ROOT / "institute-backend" / "institute-integration-tests"))
import fakeredis
import httpx
import tests.conftest  # noqa: F401  (service paths + test settings; the database is overridden below)
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
from tests.gateway import SERVICES, app

ORIGIN, API, PW = {"Origin": "http://localhost:5174"}, "/api/v1", "Lifecycle-" + uuid4().hex
ACAD = {"class_level": "11th", "academic_year": "2026-2027"}   # every enrollment needs its Class / Academic Year
DENIED = (401, 403, 404)
results: list[tuple[str, bool]] = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f" - {str(detail)[:400]}"))


engine = create_engine(DB)
factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
sql = lambda q, **a: engine.connect().execute(text(q), a).all()
sender = email.MemoryEmailSender()
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

with factory() as db:   # the only seeded row: the Super Admin (as in the reset shared database)
    sa = Staff(staff_code="STF000001", email="owner@example.com", full_name="Platform Owner", password_hash=hash_password(PW), status="ACTIVE",
               email_verified_at=datetime.now(UTC))
    db.add(sa)
    db.flush()
    db.add(StaffRoleAssignment(staff_id=sa.staff_id, role="SUPER_ADMIN", status="ACTIVE"))
    db.commit()


def login(c, code, password=PW):
    """Sign in through the API (MFA code from the dev mail outbox when the role requires it); returns headers or the failed response."""
    c.cookies.clear()
    r = c.post(f"{API}/auth/login", json={"staff_code": code, "password": password}, headers=ORIGIN)
    body = r.json()
    mfa = body.get("status") == "MFA_REQUIRED"
    if mfa:
        otp = sender.outbox[-1].body.split("code is ")[1][:6]
        r = c.post(f"{API}/auth/mfa/verify", json={"challenge_id": body["challenge_id"], "otp": otp}, headers=ORIGIN)
        body = r.json()
    if r.status_code != 200:
        return r
    return {**ORIGIN, "X-CSRF-Token": body["me"]["csrf_token"], "_mfa": str(mfa), "_role": body["me"]["primary_role"]}


def invite(c, h, full_name, mail, assignment):
    """Create a staff account and let the person set their password through the e-mailed verification link (as in the UI)."""
    r = c.post(f"{API}/staff", json={"full_name": full_name, "email": mail, "assignment": assignment}, headers=h)
    if r.status_code != 201:
        return r, None
    token = re.search(r"token=([A-Za-z0-9_\-]+)", sender.outbox[-1].body).group(1)
    v = c.post(f"{API}/auth/verify-email", json={"token": token, "new_password": PW}, headers=ORIGIN)
    return r, v


def hdr(h):
    return {k: v for k, v in h.items() if not k.startswith("_")}


with TestClient(app, base_url="http://localhost") as c:
    print("Super Admin")
    h = login(c, "STF000001")
    check("Super Admin signs in with MFA; role SUPER_ADMIN", isinstance(h, dict) and h["_mfa"] == "True" and h["_role"] == "SUPER_ADMIN", h)
    H = hdr(h)
    me = c.get(f"{API}/auth/me", headers=H).json()
    check("/auth/me returns the account from the database", me["staff_code"] == "STF000001" and me["full_name"] == "Platform Owner", me)
    P1 = c.post(f"{API}/providers", json={"name": "Alpha Academy", "email": "office@alpha.example.com"}, headers=H).json()["provider_id"]
    P2 = c.post(f"{API}/providers", json={"name": "Beta Classes", "email": "office@beta.example.com"}, headers=H).json()["provider_id"]
    check("providers created and ACTIVE (no approval step exists)", {r[0] for r in sql("select status from providers")} == {"ACTIVE"})
    SJ = c.post(f"{API}/subjects", json={"code": "PHY", "name": "Physics"}, headers=H).json()["subject_id"]
    for p in (P1, P2):
        c.put(f"{API}/providers/{p}/subjects/{SJ}", json={}, headers=H)

    print("Provider Admin")
    r, v = invite(c, H, "Priya Provider", "priya@alpha.example.com", {"role": "PROVIDER_ADMIN", "provider_id": P1})
    check("Provider Admin created, verification e-mail sets the password", r.status_code == 201 and v is not None and v.status_code == 200, r.text)
    PA = r.json()
    check("staff code assigned and account persisted ACTIVE", sql("select status from staff where staff_id = :s", s=PA["staff_id"]) == [("ACTIVE",)])
    r2, _ = invite(c, H, "Paul Beta", "paul@beta.example.com", {"role": "PROVIDER_ADMIN", "provider_id": P2})
    PB = r2.json()
    hp = login(c, PA["staff_code"])
    check("Provider Admin signs in with MFA; role PROVIDER_ADMIN", isinstance(hp, dict) and hp["_mfa"] == "True" and hp["_role"] == "PROVIDER_ADMIN", hp)
    HP = hdr(hp)
    seen = {p["provider_id"] for p in c.get(f"{API}/providers", headers=HP).json()["items"]}
    check("Provider Admin sees only their provider", seen == {P1}, seen)
    check("Provider Admin cannot open another provider", c.get(f"{API}/providers/{P2}", headers=HP).status_code in DENIED)
    check("Provider Admin cannot create a provider", c.post(f"{API}/providers", json={"name": "Nope", "email": "n@x.example.com"}, headers=HP).status_code in DENIED)

    print("Branches and same-name batches")
    B1 = c.post(f"{API}/providers/{P1}/branches", json={"name": "Pune", "city": "Pune", "state": "MH"}, headers=HP).json()["branch_id"]
    B2 = c.post(f"{API}/providers/{P1}/branches", json={"name": "Mumbai", "city": "Mumbai", "state": "MH"}, headers=HP).json()["branch_id"]
    A1 = c.post(f"{API}/branches/{B1}/batches", json={"name": "NEET 2027"}, headers=HP).json()["batch_id"]
    A2 = c.post(f"{API}/branches/{B2}/batches", json={"name": "NEET 2027"}, headers=HP).json()["batch_id"]
    check("two batches with the SAME name get distinct Batch IDs in their own branch",
          A1 != A2 and sorted(sql("select branch_id::text from batches where name = 'NEET 2027'")) == sorted([(B1,), (B2,)]))
    for bt in (A1, A2):
        c.put(f"{API}/batches/{bt}/subjects/{SJ}", json={}, headers=HP)

    print("Branch Admin")
    r, v = invite(c, HP, "Bina Branch", "bina@alpha.example.com", {"role": "BRANCH_ADMIN", "provider_id": P1, "branch_id": B1})
    check("Provider Admin creates a Branch Admin for one branch", r.status_code == 201 and v.status_code == 200, r.text)
    BA = r.json()
    check("the assignment names that exact branch", sql("select role, branch_id::text from staff_role_assignments where staff_id = :s", s=BA["staff_id"]) == [("BRANCH_ADMIN", B1)])
    hb = login(c, BA["staff_code"])
    check("Branch Admin signs in with MFA; role BRANCH_ADMIN", isinstance(hb, dict) and hb["_mfa"] == "True" and hb["_role"] == "BRANCH_ADMIN", hb)
    HB = hdr(hb)
    br = {b["branch_id"] for b in c.get(f"{API}/providers/{P1}/branches", headers=HB).json()["items"]}
    check("Branch Admin sees only their branch", br == {B1}, br)
    check("Branch Admin cannot open the other branch", c.get(f"{API}/branches/{B2}", headers=HB).status_code in DENIED)
    check("Branch Admin cannot list the other branch's batches (same name)", c.get(f"{API}/branches/{B2}/batches", headers=HB).status_code in DENIED)
    check("Branch Admin cannot create a Provider Admin",
          c.post(f"{API}/staff", json={"full_name": "X Y", "email": "xy@alpha.example.com", "assignment": {"role": "PROVIDER_ADMIN", "provider_id": P1}}, headers=HB).status_code in DENIED)

    print("Teacher")
    r, v = invite(c, HB, "Tara Teacher", "tara@alpha.example.com", {"role": "TEACHER", "provider_id": P1})
    check("Branch Admin creates a Teacher", r.status_code == 201 and v.status_code == 200, r.text)
    T = r.json()
    t = c.post(f"{API}/teaching", json={"staff_id": T["staff_id"], "branch_id": B1, "subject_id": SJ, "batch_id": A1}, headers=HB)
    check("teacher assigned Physics for B1's batch (by Batch ID)", t.status_code == 201 and
          sql("select branch_id::text, batch_id::text from teacher_subject_assignments where staff_id = :s", s=T["staff_id"]) == [(B1, A1)], t.text)
    bad = c.post(f"{API}/teaching", json={"staff_id": T["staff_id"], "branch_id": B1, "subject_id": SJ, "batch_id": A2}, headers=HB)
    check("a batch from another branch cannot be used for B1 (same name, different Batch ID)", bad.status_code >= 400, bad.status_code)
    ht = login(c, T["staff_code"])
    check("Teacher signs in without MFA; role TEACHER", isinstance(ht, dict) and ht["_mfa"] == "False" and ht["_role"] == "TEACHER", ht)
    HT = hdr(ht)
    check("Teacher cannot create staff", c.post(f"{API}/staff", json={"full_name": "Z Z", "email": "zz@alpha.example.com", "assignment": {"role": "TEACHER", "provider_id": P1}}, headers=HT).status_code in DENIED)
    check("Teacher cannot enroll students", c.post(f"{API}/branches/{B1}/enrollments", json={"academic": ACAD, "new_student": {"first_name": "A", "last_name": "B", "email": "ab@x.example.com"}}, headers=HT).status_code in DENIED)

    print("Students and the global identity")
    hb = login(c, BA["staff_code"])
    HB = hdr(hb)
    r = c.post(f"{API}/branches/{B1}/enrollments", json={"academic": ACAD, "new_student": {"first_name": "Riya", "last_name": "Patil", "email": "riya@family.example.com"}, "batch_ids": [A1]}, headers=HB)
    check("Branch Admin enrolls a new student; the real Student service creates the identity", r.status_code == 201, r.text)
    S1 = r.json()["student_id"]
    check("Branch Admin cannot enroll into the other branch", c.post(f"{API}/branches/{B2}/enrollments",
          json={"academic": ACAD, "new_student": {"first_name": "Q", "last_name": "R", "email": "qr@family.example.com"}}, headers=HB).status_code in DENIED)
    bad = c.post(f"{API}/branches/{B1}/enrollments", json={"academic": ACAD, "new_student": {"first_name": "Q", "last_name": "R", "email": "qr2@family.example.com"}, "batch_ids": [A2]}, headers=HB)
    check("a batch of another branch is refused for a B1 enrollment", bad.status_code >= 400, bad.status_code)
    hp = login(c, PA["staff_code"])
    HP = hdr(hp)
    r = c.post(f"{API}/branches/{B2}/enrollments", json={"academic": ACAD, "new_student": {"first_name": "Aman", "last_name": "Shah", "email": "aman@family.example.com"}, "batch_ids": [A2]}, headers=HP)
    S2 = r.json()["student_id"]
    again = c.post(f"{API}/branches/{B1}/enrollments", json={"academic": ACAD, "new_student": {"first_name": "Riya", "last_name": "Patil", "email": "riya@family.example.com"}}, headers=HP)
    check("the same e-mail is never turned into a second identity", again.status_code >= 400 or again.json().get("student_id") == S1, again.text)
    hq = login(c, PB["staff_code"])
    HQ = hdr(hq)
    P2B = c.post(f"{API}/providers/{P2}/branches", json={"name": "Nashik", "city": "Nashik", "state": "MH"}, headers=HQ).json()["branch_id"]
    look = c.post(f"{API}/students/lookup", json={"branch_id": P2B, "email": "riya@family.example.com"}, headers=HQ)
    check("another provider's lookup finds the existing student", look.status_code == 200 and S1 in look.text, look.text)
    r = c.post(f"{API}/branches/{P2B}/enrollments", json={"academic": ACAD, "student_id": S1}, headers=HQ)
    check("the second provider enrolls the SAME student (no new identity)", r.status_code == 201 and r.json()["student_id"] == S1, r.text)
    check("one global identity, two provider enrollments", sql("select count(*) from students where email = 'riya@family.example.com'") == [(1,)]
          and sql("select count(distinct provider_id) from enrollments where student_id = :s", s=S1) == [(2,)])
    check("Beta's admin cannot see Alpha's enrollments", c.get(f"{API}/branches/{B1}/enrollments", headers=HQ).status_code in DENIED)

    print("Student sign-in (real Student authentication service)")
    s = httpx.Client(timeout=30)
    s.post(STU["auth"] + "/auth/forgot-password", json={"email": "riya@family.example.com"})
    tok = re.findall(r"reset-password\?token=([A-Za-z0-9_\-]+)", AUTH_LOG.read_text())
    check("password setup link issued (dev console)", bool(tok))
    check("student sets a password", s.post(STU["auth"] + "/auth/reset-password", json={"token": tok[-1], "new_password": PW}).status_code == 200)
    check("wrong password is refused", httpx.post(STU["auth"] + "/auth/login", json={"login": S1, "password": PW + "x"}).status_code in (400, 401))
    r = s.post(STU["auth"] + "/auth/login", json={"login": S1, "password": PW})
    check("student signs in with their Student ID", r.status_code == 200, r.text)
    sme = s.get(STU["auth"] + "/auth/me").json()
    check("the student's name comes from the database", sme.get("student_id") == S1 and "Riya" in str(sme), sme)
    check("an Institute staff session is not accepted by the Student portal", httpx.get(STU["auth"] + "/auth/me", cookies=dict(c.cookies)).status_code in (401, 403))

    print("Audience resolution with same-name batches")
    hb = login(c, BA["staff_code"])
    HB = hdr(hb)
    start = datetime.now(UTC) + timedelta(days=2)
    draft = lambda aud: {"audience": aud, "name": "Lifecycle check", "subjects": [{"subject_id": SJ, "paper_version_id": None}],
                         "duration_minutes": 30, "start_at": start.isoformat(), "end_at": (start + timedelta(hours=1)).isoformat(),
                         "marking_mode": "UNIFORM", "marks_correct": 4, "marks_wrong": 1}
    r = c.post(f"{API}/tests/check", json=draft({"provider_id": P1, "branch_ids": [B1], "whole_branch_ids": [], "batch_ids": [A1], "student_ids": []}), headers=HB)
    check("a test aimed at B1's NEET 2027 reaches exactly 1 student (Riya), not B2's same-name batch", r.status_code == 200 and r.json().get("eligible_count") == 1, r.text)
    r = c.post(f"{API}/tests/check", json=draft({"provider_id": P1, "branch_ids": [B1], "whole_branch_ids": [], "batch_ids": [A2], "student_ids": []}), headers=HB)
    check("B2's batch cannot be targeted from B1 (refused by the backend)", r.status_code >= 400 or r.json().get("eligible_count") == 0, r.text)

    print("Deactivation and role changes")
    ht = login(c, T["staff_code"])
    tcookie = dict(c.cookies)                                         # the Teacher's live session
    H = hdr(login(c, "STF000001"))
    d = c.post(f"{API}/staff/{T['staff_id']}/deactivate", json={"reason": "lifecycle check"}, headers=H)
    check("Super Admin deactivates the Teacher", d.status_code == 200, d.text)
    check("the deactivated Teacher's session stops working immediately",
          c.get(f"{API}/auth/me", cookies=tcookie).status_code == 401)
    check("the deactivated Teacher cannot sign in", not isinstance(login(c, T["staff_code"]), dict))
    H = hdr(login(c, "STF000001"))
    c.post(f"{API}/staff/{T['staff_id']}/reactivate", json={"reason": "lifecycle check"}, headers=H)
    check("reactivation restores sign-in, the identity was never deleted", isinstance(login(c, T["staff_code"]), dict))
    H = hdr(login(c, "STF000001"))
    asg = sql("select assignment_id::text from staff_role_assignments where staff_id = :s", s=BA["staff_id"])[0][0]
    rm = c.request("DELETE", f"{API}/staff/{BA['staff_id']}/assignments/{asg}", json={"reason": "lifecycle check"}, headers=H)
    check("removing the Branch Admin role", rm.status_code in (200, 204), rm.text)
    check("the account still exists after its role is removed", sql("select count(*) from staff where staff_id = :s", s=BA["staff_id"]) == [(1,)])
    hb = login(c, BA["staff_code"])
    check("without a role the account cannot reach branch data", not isinstance(hb, dict) or c.get(f"{API}/branches/{B1}", headers=hdr(hb)).status_code in DENIED)

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} lifecycle checks passed" + (f"; FAILED: {failed}" if failed else ""))
sys.exit(1 if failed else 0)
