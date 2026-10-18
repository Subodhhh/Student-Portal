"""Test -> Create Test, end to end on DISPOSABLE infrastructure (run: tests/e2e/pipeline_disposable.sh create_test_disposable.py).

The Institute services run in-process with REAL HTTP clients to the six real Student services (processes) on a throwaway PostgreSQL.
Everything is created through the APIs; the only seeded row is the platform owner. Proves, through the actual student-facing APIs:
roles/scope, audience resolution (batches across branches + individual students, one assignment per student, a student of two
providers), question paper + answer key (ID key, quoted explanations, >5 failures, rejection then corrected retry, no partial import),
multi-subject publication with exam-level marking, idempotent re-publication, delivery, the availability window (not before, not
after), eligibility (an unassigned student is refused), one attempt, negative marking and results for students and staff.
"""
import os
import re
import sys
import time
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
from inst_exam.services import outbox
from institute_common.core import email, redis_store
from institute_common.core.passwords import hash_password
from institute_common.database import get_db
from institute_common.internal_http import set_peer_client
from institute_common.models.staff import Staff, StaffRoleAssignment
from institute_common.ownership import attach_guard
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from tests.gateway import SERVICES, app

ORIGIN, API, PW = {"Origin": "http://localhost:5174"}, "/api/v1", "CreateTest-" + uuid4().hex
results: list[tuple[str, bool]] = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f" - {str(detail)[:400]}"), flush=True)
    if not ok:
        summary()


def summary():
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed" + (f"; FAILED: {failed}" if failed else " - Create Test proven end to end"))
    sys.exit(1 if failed else 0)


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


def invite(c, h, name, mail, role, provider):
    r = c.post(f"{API}/staff", json={"full_name": name, "email": mail, "assignment": {"role": role, "provider_id": provider}}, headers=h)
    token = re.search(r"token=([A-Za-z0-9_\-]+)", sender.outbox[-1].body).group(1)
    c.post(f"{API}/auth/verify-email", json={"token": token, "new_password": PW}, headers=ORIGIN)
    return r.json()


def paper_csv(n, prefix, bad=()):
    rows = [f"{prefix}{i:03d},{prefix} question {i}?,A{i},B{i},{'' if i in bad else f'C{i}'},D{i},1,0," for i in range(1, n + 1)]
    return "question_id,question,option_a,option_b,option_c,option_d,marks,negative_marks,explanation\n" + "\n".join(rows) + "\n"


def id_key(n, prefix):    # reversed order, quoted explanations with commas: matching must be by ID
    return "question_id,correct_option,explanation\n" + "\n".join(f'{prefix}{i:03d},{"ABCD"[(i - 1) % 4]},"Reason {i}, explained"' for i in range(n, 0, -1)) + "\n"


def upload(c, h, branch, subject, text_, key_, title):
    files = {"file": ("paper.csv", text_.encode(), "text/csv"), "answer_key": ("key.csv", key_.encode(), "text/csv")}
    return c.post(f"{API}/branches/{branch}/subjects/{subject}/papers/upload", files=files, data={"title": title}, headers=h)


def student_session(email_, sid):
    s = httpx.Client(timeout=20, headers={"Origin": "http://localhost"})
    s.post(STU["auth"] + "/auth/forgot-password", json={"email": email_})
    tok = None
    for _ in range(30):
        found = re.findall(r"reset-password\?token=([A-Za-z0-9_\-]+)", AUTH_LOG.read_text())
        if found and found[-1] not in USED:
            tok = found[-1]
            break
        time.sleep(0.5)
    USED.add(tok)
    s.post(STU["auth"] + "/auth/reset-password", json={"token": tok, "new_password": PW})
    ok = s.post(STU["auth"] + "/auth/login", json={"login": sid, "password": PW}).status_code == 200
    return s, ok


USED: set[str] = set()

with TestClient(app, base_url="http://localhost") as c:
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
    T = invite(c, h, "Tara Teacher", "tara@north.example.com", "TEACHER", P)
    h = login(c, "STF000001")
    c.post(f"{API}/teaching", json={"staff_id": T["staff_id"], "branch_id": B1, "subject_id": PHY, "batch_id": A}, headers=h)

    def enroll(branch, first, mail, batch_ids, sid=None):
        body = {"student_id": sid, "batch_ids": batch_ids} if sid else {"new_student": {"first_name": first, "last_name": "Test", "email": mail}, "batch_ids": batch_ids}
        r = c.post(f"{API}/branches/{branch}/enrollments", json=body, headers=h)
        assert r.status_code == 201, r.text
        return r.json()["student_id"]
    S1 = enroll(B1, "Asha", "asha@family.example.com", [A])                 # batch A (Pune)
    S2 = enroll(B2, "Bilal", "bilal@family.example.com", [C])               # batch C (Mumbai)
    S3 = enroll(B1, "Chitra", "chitra@family.example.com", [A])             # batch A ...
    enroll(B2, "", "", [C], sid=S3)                                          # ... and batch C (second enrollment)
    S4 = enroll(B1, "Dev", "dev@family.example.com", [])                    # no batch: targeted individually
    S5 = enroll(B2, "Esha", "esha@family.example.com", [])                  # enrolled, never targeted
    enroll(B3, "", "", [D], sid=S1)                                          # S1 also studies with South Coaching
    with factory() as db:
        reference_sync.run_once(db, full=True)
    check("set-up: 2 providers, 3 branches, 3 batches, teacher, provider admin, 5 students (one with two providers)", True)

    print("roles and scope")
    hp = login(c, PA["staff_code"])
    ht = login(c, T["staff_code"])
    scope_t = c.get(f"{API}/tests/scope?provider_id={P}", headers=ht).json()["branches"]
    check("teacher scope: only Pune, batch NEET A, Physics", [(b["name"], [x["name"] for x in b["batches"]], [s["name"] for s in b["subjects"]]) for b in scope_t]
          == [("Pune", ["NEET A"], ["Physics"])], scope_t)
    hp = login(c, PA["staff_code"])
    check("provider admin scope: both branches", sorted(b["name"] for b in c.get(f"{API}/tests/scope?provider_id={P}", headers=hp).json()["branches"]) == ["Mumbai", "Pune"])
    check("other provider's data is not targetable", c.get(f"{API}/tests/scope?provider_id={P2}", headers=hp).status_code == 404)

    print("audience")
    audience = {"provider_id": P, "branch_ids": [B1, B2], "whole_branch_ids": [], "batch_ids": [A, C], "student_ids": [S4]}
    r = c.post(f"{API}/tests/audience", json=audience, headers=hp).json()
    check("batches across two branches + an individual student: S1, S2, S3 (once), S4; not S5", sorted(x["student_id"] for x in r["students"]) == sorted([S1, S2, S3, S4]), r)
    bad = c.post(f"{API}/tests/audience", json={**audience, "student_ids": [S4, "STU999999"]}, headers=hp)
    check("an unknown/unauthorized student is refused by the server", bad.status_code == 422, bad.text)

    print("question paper + answer key")
    before = sql("select count(*) from bank_questions")[0][0]
    r = upload(c, hp, B1, PHY, paper_csv(10, "P", bad=(1, 2, 3, 4, 5, 6)), id_key(10, "P"), "Weekly Physics – Physics")
    d = r.json()["detail"]
    check("6 failed questions: rejected as a whole, threshold reported, nothing imported", r.status_code == 422 and d["summary"]["threshold_exceeded"]
          and d["summary"]["questions_failed"] == 6 and sql("select count(*) from bank_questions")[0][0] == before, d.get("summary"))
    r = upload(c, hp, B1, PHY, paper_csv(3, "P", bad=(2,)), id_key(3, "P"), "Weekly Physics – Physics")
    d = r.json()["detail"]
    check("1 failed question: rejected, the real question ID reported, nothing imported", r.status_code == 422 and not d["summary"]["threshold_exceeded"]
          and [e["question"] for e in d["errors"]] == ["P002"] and sql("select count(*) from bank_questions")[0][0] == before, d)
    r = upload(c, hp, B1, PHY, paper_csv(3, "P"), id_key(3, "P"), "Weekly Physics – Physics")
    check("corrected files pass on retry", r.status_code == 201, r.text)
    PV_PHY = r.json()["paper_version_id"]
    again = upload(c, hp, B1, PHY, paper_csv(3, "P"), id_key(3, "P"), "Weekly Physics – Physics")
    check("re-importing the same files reuses the version (no duplicate)", again.status_code == 409 and again.json()["detail"]["paper_version_id"] == PV_PHY)
    answers = sql("select qv.correct_option, qv.explanation from paper_version_questions x join question_versions qv using (question_version_id) where x.paper_version_id = :pv order by x.position", pv=PV_PHY)
    check("ID-keyed answers land on the right questions with the quoted explanations", [a for a, _ in answers] == ["A", "B", "C"] and answers[1][1] == "Reason 2, explained", answers)
    r = upload(c, hp, B1, CHE, "question,option_a,option_b,option_c,option_d,marks,negative_marks\nC one?,a,b,c,d,1,0\nC two?,a,b,c,d,1,0\n", '1,D,"why, 1"\n2,A\n', "Weekly – Chemistry")
    check("numbered answer key (quoted explanation) still works", r.status_code == 201, r.text)
    PV_CHE = r.json()["paper_version_id"]

    print("publication")
    start = datetime.now(UTC) + timedelta(seconds=40)
    test_body = {"audience": audience, "name": "  Weekly   Science  ", "description": "Answer carefully.",
                 "subjects": [{"subject_id": PHY, "paper_version_id": PV_PHY}, {"subject_id": CHE, "paper_version_id": PV_CHE}],
                 "duration_minutes": 30, "start_at": start.isoformat(), "end_at": (start + timedelta(minutes=30)).isoformat(),
                 "marking_mode": "UNIFORM", "marks_correct": 4, "marks_wrong": 1}
    r = c.post(f"{API}/tests", json=test_body, headers={**hp, "Idempotency-Key": "e2e-weekly-science-1"})
    check("publishing before the papers are approved/published is refused", r.status_code == 422 and "approved and published" in r.text, r.text)
    for pv in (PV_PHY, PV_CHE):
        c.post(f"{API}/paper-versions/{pv}/approve", json={"note": "ok"}, headers=hp)
        c.post(f"{API}/paper-versions/{pv}/publish", json={"note": None}, headers=hp)
    r = c.post(f"{API}/tests", json=test_body, headers={**hp, "Idempotency-Key": "e2e-weekly-science-1"})
    t = r.json()
    check("published: 5 questions, 20 marks, +4/-1, 4 students assigned", r.status_code == 201 and t["state"] == "PUBLISHED_DELIVERY_PENDING" and t["name"] == "Weekly Science"
          and t["question_count"] == 5 and float(t["total_marks"]) == 20 and t["assigned_students"] == 4, t)
    r2 = c.post(f"{API}/tests", json=test_body, headers={**hp, "Idempotency-Key": "e2e-weekly-science-1"})
    check("repeating the publication returns the same test (no duplicate exam or assignments)", r2.json().get("exam_id") == t["exam_id"]
          and sql("select count(*) from exams")[0][0] == 1 and sql("select count(*) from student_exam_assignments")[0][0] == 4)
    listed = c.get(f"{API}/tests", headers=hp).json()["items"]
    check("staff Scheduled Tests shows it", [x["name"] for x in listed] == ["Weekly Science"])
    ht = login(c, T["staff_code"])
    check("the teacher cannot publish beyond their batch", c.post(f"{API}/tests", json={**test_body, "name": "Teacher overreach"},
          headers={**ht, "Idempotency-Key": "e2e-teacher-overreach"}).status_code == 422)
    with factory() as db:
        sent = outbox.deliver_due(db)
    TEST_ID = t["exam_version_id"]
    hp = login(c, PA["staff_code"])
    check("delivered to the Student test service", sent.get("sent") == 1 and c.get(f"{API}/tests", headers=hp).json()["items"][0]["state"] == "PUBLISHED", sent)
    subjects = sql("select distinct subject from questions where test_id::text = :t", t=TEST_ID)
    check("each delivered question carries its subject", sorted(s for (s,) in subjects) == ["Chemistry", "Physics"], subjects)

    print("students")
    s1, ok1 = student_session("asha@family.example.com", S1)
    s3, ok3 = student_session("chitra@family.example.com", S3)
    s5, ok5 = student_session("esha@family.example.com", S5)
    check("students sign in through the real Student auth service", ok1 and ok3 and ok5)
    check("before the window opens a start is refused", s1.post(STU["attempt"] + "/attempts/start", json={"test_id": TEST_ID}).status_code == 400)
    provs = {e["provider"]["name"] for e in s1.get(STU["enroll"] + "/enrollments").json()}
    check("S1 (two providers, one account) sees both institutes", provs == {"North Academy", "South Coaching"}, provs)
    time.sleep(max(0, (start - datetime.now(UTC)).total_seconds() + 2))
    listed = {x["test_id"]: x for x in s1.get(STU["test"] + "/tests").json()}
    check("the assigned student sees the test with its provider", TEST_ID in listed and listed[TEST_ID]["provider"]["name"] == "North Academy", list(listed))
    check("an enrolled but untargeted student neither sees nor starts it", TEST_ID not in {x["test_id"] for x in s5.get(STU["test"] + "/tests").json()}
          and s5.post(STU["attempt"] + "/attempts/start", json={"test_id": TEST_ID}).status_code >= 400)
    att = s3.post(STU["attempt"] + "/attempts/start", json={"test_id": TEST_ID}).json()
    AID = att["attempt_id"]
    ws = s3.get(f"{STU['attempt']}/attempts/{AID}/workspace").json()
    q = sorted(ws["questions"], key=lambda x: x["question_order"])
    check("S3 (reached through two batches) has ONE attempt with 5 questions, both subjects, a 30-minute timer",
          len(q) == 5 and {x["subject"] for x in q} == {"Physics", "Chemistry"} and 0 < ws["remaining_seconds"] <= 1800, ws.get("remaining_seconds"))
    phys = [x for x in q if x["subject"] == "Physics"]
    s3.post(f"{STU['attempt']}/attempts/{AID}/answers", json={"question_id": phys[0]["question_id"], "selected_answer": "A"})   # correct
    s3.post(f"{STU['attempt']}/attempts/{AID}/answers", json={"question_id": phys[1]["question_id"], "selected_answer": "D"})   # wrong (B)
    again = s3.post(STU["attempt"] + "/attempts/start", json={"test_id": TEST_ID}).json()
    check("reconnecting resumes the same attempt", again["attempt_id"] == AID)
    check("submitted", s3.post(f"{STU['attempt']}/attempts/{AID}/submit").json().get("status") == "SUBMITTED")
    res = s3.post(f"{STU['result']}/results/attempts/{AID}/evaluate").json()
    check("evaluated with negative marking: +4 -1 = 3 of 20", res.get("obtained_marks") == 3 and res.get("total_marks") == 20 and res.get("unanswered_questions") == 3, res)
    check("no second attempt after submission", s3.post(STU["attempt"] + "/attempts/start", json={"test_id": TEST_ID}).status_code == 400)
    hp = login(c, PA["staff_code"])
    c.post(f"{API}/exams/{t['exam_id']}/sync", headers=hp)
    out = c.get(f"{API}/exams/{t['exam_id']}/results", headers=hp).json()
    check("staff see S3's result", [(x["student_id"], float(x["obtained_marks"])) for x in out.get("results", [])] == [(S3, 3.0)], out)

    print("availability window end")
    start2 = datetime.now(UTC) + timedelta(seconds=8)
    body2 = {**test_body, "name": "Short window", "audience": {**audience, "batch_ids": [A], "student_ids": [], "branch_ids": [B1]},
             "subjects": [{"subject_id": PHY, "paper_version_id": PV_PHY}], "start_at": start2.isoformat(), "end_at": (start2 + timedelta(seconds=12)).isoformat()}
    t2 = c.post(f"{API}/tests", json=body2, headers={**hp, "Idempotency-Key": "e2e-short-window"}).json()
    with factory() as db:
        outbox.deliver_due(db)
    time.sleep(max(0, (start2 + timedelta(seconds=14) - datetime.now(UTC)).total_seconds()))
    check("after the deadline a new attempt is refused", s1.post(STU["attempt"] + "/attempts/start", json={"test_id": t2["exam_version_id"]}).status_code == 400)
summary()
