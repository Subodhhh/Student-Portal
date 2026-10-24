"""Driver for tests/e2e/pipeline_disposable.sh (run it through that script, which provides the disposable database and the real
Student services). The Institute services run in-process (the integration-test gateway) with REAL HTTP clients to the Student services.

Stages proven, each checked through the API and in the database:
  1 upload a paper + separate answer key      2 extraction (options, answers from the key, marks, explanations, topics)
  3 any bad question/answer rejects the WHOLE paper (nothing persisted) and the errors are reported
  4 preview of the extracted content + import history     5 the teacher approves and publishes their own paper
  6 persistence: bank questions, versions, paper version, question-to-paper links
  7 exam plan with duration and exam-level marking (+4/-1), generated and published to the eligible batch, delivered to the
    Student test service      8 the assigned student signs in, sees and attempts the test (an unassigned student cannot)
  9 submission, automatic evaluation with negative marking, results visible to the student and to Institute staff
"""
import json
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

ORIGIN, API, PW = {"Origin": "http://localhost:5174"}, "/api/v1", "Pipeline-" + uuid4().hex
ACAD = {"class_level": "11th", "academic_year": "2026-2027"}   # every enrollment needs its Class / Academic Year
results: list[tuple[str, bool]] = []


def check(stage, name, ok, detail=""):
    results.append((f"{stage} {name}", bool(ok)))
    print(f"  [{'PASS' if ok else 'FAIL'}] {stage} {name}" + ("" if ok else f" - {str(detail)[:400]}"))
    if not ok and os.environ.get("STOP_ON_FAIL", "1") == "1":
        summary()


def summary():
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed" + (f"; FAILED: {failed}" if failed else " - every pipeline stage proven"))
    sys.exit(1 if failed else 0)


engine = create_engine(DB)
factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
sql = lambda q, **a: engine.connect().execute(text(q), a).all()
sender = email.MemoryEmailSender()
email.set_sender(sender)
redis_store.set_client(fakeredis.FakeRedis(decode_responses=True))   # Institute identity sessions/MFA (the Student side uses real Redis)


def override():
    db = factory()
    attach_guard(db)
    try:
        yield db
    finally:
        db.close()


reference_sync.new_session = factory   # sessions opened outside a request (the pre-delivery projection refresh) use this database too
for name, service_app in SERVICES.items():
    service_app.dependency_overrides[get_db] = override
    set_peer_client(name, TestClient(service_app, base_url=f"http://{name}"))

with factory() as db:   # the only seeded row: the platform owner (everything else is created through the APIs)
    sa = Staff(staff_code="STF000001", email="owner@example.com", full_name="Platform Owner", password_hash=hash_password(PW), status="ACTIVE",
               email_verified_at=datetime.now(UTC))
    db.add(sa)
    db.flush()
    db.add(StaffRoleAssignment(staff_id=sa.staff_id, role="SUPER_ADMIN", status="ACTIVE"))
    db.commit()

PAPER = ("question,option_a,option_b,option_c,option_d,marks,negative_marks,explanation,topic\n"
         "What is the SI unit of force?,Newton,Joule,Watt,Pascal,1,0,Force is measured in newtons.,Units\n"
         "What is the SI unit of energy?,Newton,Joule,Watt,Pascal,1,0,Energy is measured in joules.,Units\n"
         "What is the SI unit of power?,Newton,Joule,Pascal,Watt,1,0,Power is measured in watts.,Units\n")
KEY_OK = "question,answer\n1,A\n2. b\n3 - (d)\n"


def upload(c, h, branch, subject, paper, key, title):
    files = {"file": ("paper.csv", paper.encode(), "text/csv")}
    if key is not None:
        files["answer_key"] = ("key.csv", key.encode(), "text/csv")
    return c.post(f"{API}/branches/{branch}/subjects/{subject}/papers/upload", files=files, data={"title": title}, headers=h)


def login(c, code, password=PW):
    r = c.post(f"{API}/auth/login", json={"staff_code": code, "password": password}, headers=ORIGIN)
    body = r.json()
    if body.get("status") == "MFA_REQUIRED":
        otp = sender.outbox[-1].body.split("code is ")[1][:6]
        body = c.post(f"{API}/auth/mfa/verify", json={"challenge_id": body["challenge_id"], "otp": otp}, headers=ORIGIN).json()
    return {**ORIGIN, "X-CSRF-Token": body["me"]["csrf_token"]}


with TestClient(app, base_url="http://localhost") as c:
    print("set-up through the Institute API")
    h = login(c, "STF000001")
    P = c.post(f"{API}/providers", json={"name": "Pipeline Academy", "email": "office@pipeline-academy.example.com"}, headers=h).json()["provider_id"]
    B = c.post(f"{API}/providers/{P}/branches", json={"name": "Main", "city": "Pune", "state": "MH"}, headers=h).json()["branch_id"]
    SJ = c.post(f"{API}/subjects", json={"code": "PHY", "name": "Physics"}, headers=h).json()["subject_id"]
    c.put(f"{API}/providers/{P}/subjects/{SJ}", json={}, headers=h)
    BT = c.post(f"{API}/branches/{B}/batches", json={"name": "NEET 2027"}, headers=h).json()["batch_id"]
    r = c.post(f"{API}/staff", json={"full_name": "Asha Teacher", "email": "asha@pipeline-academy.example.com",
                                     "assignment": {"role": "TEACHER", "provider_id": P}}, headers=h)
    T = r.json()
    token = re.search(r"token=([A-Za-z0-9_\-]+)", sender.outbox[-1].body).group(1)
    check("0", "teacher invited and verified", c.post(f"{API}/auth/verify-email", json={"token": token, "new_password": PW}, headers=ORIGIN).status_code == 200, r.text)
    h = login(c, "STF000001")
    c.post(f"{API}/teaching", json={"staff_id": T["staff_id"], "branch_id": B, "subject_id": SJ, "batch_id": BT}, headers=h)
    r = c.post(f"{API}/branches/{B}/enrollments", json={"academic": ACAD, "new_student": {"first_name": "Riya", "last_name": "Patil", "email": "riya@family.example.com"},
                                                        "batch_ids": [BT]}, headers=h)
    check("0", "student identity created by the REAL Student service and enrolled", r.status_code == 201, r.text)
    SID = r.json()["student_id"]
    r = c.post(f"{API}/branches/{B}/enrollments", json={"academic": ACAD, "new_student": {"first_name": "Aman", "last_name": "Shah", "email": "aman@family.example.com"}}, headers=h)
    OTHER = r.json()["student_id"]                                    # enrolled, but not in the exam's batch
    with factory() as db:
        sync = reference_sync.run_once(db, full=True)
    check("0", "enrollments projected to the Student database", not sync.get("rejected") and sql("select count(*) from enrollments where student_id = :s and provider_id::text = :p", s=SID, p=P)[0][0] == 1, sync)

    print("1-4 upload, extraction, validation, preview")
    ht = login(c, T["staff_code"])
    before = sql("select (select count(*) from bank_questions), (select count(*) from papers), (select count(*) from paper_versions)")[0]
    bad = PAPER.replace("What is the SI unit of energy?,Newton,Joule,Watt,Pascal", "What is the SI unit of energy?,Newton,,Watt,Pascal")
    r = upload(c, ht, B, SJ, bad, KEY_OK, "Units test (bad option)")
    errs = r.json().get("detail", {}).get("errors", [])
    check("3", "a question with a missing option rejects the whole paper with a row-level error", r.status_code == 422 and any(e.get("row") == 3 for e in errs), r.text)
    r = upload(c, ht, B, SJ, PAPER, "1,A\n2,B\n", "Units test (incomplete key)")
    check("3", "an answer key missing question 3 rejects the whole paper", r.status_code == 422
          and any("question 3" in e["message"].lower() for e in r.json()["detail"]["errors"]), r.text)
    check("3", "nothing from rejected papers was persisted",
          sql("select (select count(*) from bank_questions), (select count(*) from papers), (select count(*) from paper_versions)")[0] == before)
    r = upload(c, ht, B, SJ, PAPER, KEY_OK, "Units test")
    check("1", "paper + separate answer key accepted", r.status_code == 201, r.text)
    PV = r.json()["paper_version_id"]
    pv = c.get(f"{API}/paper-versions/{PV}", headers=ht).json()
    qs = pv["questions"]
    check("2", "extracted: stems, four options, answers from the key, marks, explanations, topics",
          [q["correct_option"] for q in qs] == ["A", "B", "D"] and qs[2]["option_d"] == "Watt" and qs[0]["explanation"] == "Force is measured in newtons."
          and {q["topic"] for q in qs} == {"Units"} and [str(q["marks_positive"]) for q in qs] == ["1.00"] * 3, qs)
    check("4", "preview shows the draft (VALIDATED) version to the teacher", pv["latest_version"]["status"] == "VALIDATED" and len(qs) == 3, pv)
    hist = c.get(f"{API}/uploads?branch_id={B}", headers=ht).json()
    check("4", "import history lists the accepted and the two rejected uploads",
          sorted(i["status"] for i in hist["items"]) == ["REJECTED", "REJECTED", "VALIDATED"] and any(i["has_answer_key"] for i in hist["items"]), hist)

    print("5-6 teacher approval, persistence")
    check("5", "the teacher approves their paper", c.post(f"{API}/paper-versions/{PV}/approve", json={"note": "checked"}, headers=ht).status_code == 200)
    check("5", "the teacher publishes it", c.post(f"{API}/paper-versions/{PV}/publish", json={"note": None}, headers=ht).json().get("status") == "PUBLISHED")
    rows = sql("""select qv.stem, qv.option_a, qv.option_b, qv.option_c, qv.option_d, qv.correct_option, qv.marks_positive, qv.marks_negative, qv.explanation,
                         bq.topic, bq.provider_id::text, bq.branch_id::text, bq.subject_id::text, pv.status, pvq.position
                  from paper_version_questions pvq join question_versions qv using (question_version_id) join bank_questions bq using (question_id)
                  join paper_versions pv using (paper_version_id) where pvq.paper_version_id = :pv order by pvq.position""", pv=PV)
    check("6", "bank questions + versions + paper version + question-to-paper links persisted with answers, marks, metadata",
          len(rows) == 3 and [r[5] for r in rows] == ["A", "B", "D"] and rows[1][2] == "Joule" and rows[0][8].startswith("Force")
          and all(r[9] == "Units" and r[10] == P and r[11] == B and r[12] == SJ and r[13] == "PUBLISHED" for r in rows) and [r[14] for r in rows] == [1, 2, 3], rows)
    try:
        with engine.begin() as x:
            x.execute(text("update question_versions set stem = 'tampered'"))
        check("6", "published question versions are append-only (trigger)", False)
    except Exception:  # noqa: BLE001 - any database error means the trigger rejected the edit
        check("6", "published question versions are append-only (trigger)", True)

    print("7 test creation, publication, delivery")
    h = login(c, "STF000001")
    start = datetime.now(UTC) + timedelta(seconds=25)
    r = c.post(f"{API}/branches/{B}/exam-plans", json={
        "name": "Weekly Physics", "duration_minutes": 30, "start_at": start.isoformat(), "end_at": (start + timedelta(minutes=40)).isoformat(),
        "audience_type": "BATCHES", "batch_ids": [BT], "marking_mode": "UNIFORM", "marks_correct": 4, "marks_wrong": 1,
        "slots": [{"subject_id": SJ, "paper_version_id": PV}]}, headers=h)
    check("7", "exam plan created (30 min, +4 correct / -1 wrong, batch audience)", r.status_code == 201, r.text)
    job = c.post(f"{API}/exam-plans/{r.json()['plan_id']}/generate", json={}, headers=h).json()
    ex = c.get(f"{API}/exams/{job.get('exam_id')}", headers=h).json()
    check("7", "exam generated and published to exactly the eligible student", job.get("status") == "SUCCEEDED" and ex["status"] == "PUBLISHED"
          and ex["assigned_students"] == 1 and ex["settings"]["marking"] == {"mode": "UNIFORM", "correct": 4, "wrong": 1}, (job, ex))
    with factory() as db:
        sent = outbox.deliver_due(db)
    TEST_ID = ex["exam_version_id"]
    delivered = sql("select t.duration_minutes, t.total_questions, t.total_marks::text, count(q.*), min(q.marks), max(q.negative_marks) from tests t "
                    "join questions q using (test_id) where t.test_id::text = :t group by t.test_id", t=TEST_ID)
    check("7", "delivered over HTTP to the real Student test service with exam-level marks on every question",
          sent.get("sent") == 1 and delivered and delivered[0][:2] == (30, 3) and float(delivered[0][2]) == 12 and delivered[0][3] == 3
          and float(delivered[0][4]) == 4 and float(delivered[0][5]) == 1, (sent, delivered))

    print("8 the student sees and attempts the test")
    s = httpx.Client(timeout=20, headers={"Origin": "http://localhost"})
    s.post(STU["auth"] + "/auth/forgot-password", json={"email": "riya@family.example.com"})
    for _ in range(20):
        found = re.findall(r"reset-password\?token=([A-Za-z0-9_\-]+)", AUTH_LOG.read_text())
        if found:
            break
        time.sleep(0.5)
    check("8", "password set via the (dev console) reset link", s.post(STU["auth"] + "/auth/reset-password", json={"token": found[-1], "new_password": PW}).status_code == 200)
    r = s.post(STU["auth"] + "/auth/login", json={"login": SID, "password": PW})
    check("8", "student signs in with the Student ID", r.status_code == 200 and s.get(STU["auth"] + "/auth/me").json().get("student_id") == SID, r.text)
    check("8", "My Institutes shows the provider", P in {e["provider"]["provider_id"] for e in s.get(STU["enroll"] + "/enrollments").json()})
    time.sleep(max(0, (start - datetime.now(UTC)).total_seconds() + 1))
    tests_ = {t["test_id"]: t for t in s.get(STU["test"] + "/tests").json()}
    check("8", "the published test is listed for the assigned student", TEST_ID in tests_, list(tests_))
    att = s.post(STU["attempt"] + "/attempts/start", json={"test_id": TEST_ID}).json()
    AID = att.get("attempt_id")
    ws = s.get(f"{STU['attempt']}/attempts/{AID}/workspace").json()
    wq = sorted(ws.get("questions", []), key=lambda q: q["question_order"])
    check("8", "attempt started; 3 questions, one timer, no answers exposed", att.get("status") == "IN_PROGRESS" and len(wq) == 3 and ws["remaining_seconds"] > 0
          and not any("correct" in json.dumps(q).lower() for q in wq), ws)
    s.post(f"{STU['attempt']}/attempts/{AID}/answers", json={"question_id": wq[0]["question_id"], "selected_answer": "A"})   # correct
    s.post(f"{STU['attempt']}/attempts/{AID}/answers", json={"question_id": wq[1]["question_id"], "selected_answer": "C"})   # wrong; Q3 unanswered
    o = httpx.Client(timeout=20, headers={"Origin": "http://localhost"})
    o.post(STU["auth"] + "/auth/forgot-password", json={"email": "aman@family.example.com"})
    time.sleep(1)
    tok = re.findall(r"reset-password\?token=([A-Za-z0-9_\-]+)", AUTH_LOG.read_text())[-1]
    o.post(STU["auth"] + "/auth/reset-password", json={"token": tok, "new_password": PW})
    o.post(STU["auth"] + "/auth/login", json={"login": OTHER, "password": PW})
    check("8", "an enrolled student outside the audience neither sees nor can start the test",
          TEST_ID not in {t["test_id"] for t in o.get(STU["test"] + "/tests").json()} and o.post(STU["attempt"] + "/attempts/start", json={"test_id": TEST_ID}).status_code >= 400)

    print("9 submission, evaluation, results")
    check("9", "submitted", s.post(f"{STU['attempt']}/attempts/{AID}/submit").json().get("status") == "SUBMITTED")
    res = s.post(f"{STU['result']}/results/attempts/{AID}/evaluate").json()
    check("9", "automatic evaluation with negative marking: +4 -1 +0 = 3 of 12", res.get("obtained_marks") == 3 and res.get("total_marks") == 12
          and res.get("correct_answers") == 1 and res.get("incorrect_answers") == 1 and res.get("unanswered_questions") == 1, res)
    check("9", "re-evaluation is idempotent", s.post(f"{STU['result']}/results/attempts/{AID}/evaluate").json().get("result_id") == res.get("result_id"))
    det = s.get(f"{STU['result']}/results/attempts/{AID}/details").json()
    check("9", "the student sees the result; the answer key stays hidden while the window is open", det.get("review_available") is False and det.get("questions") == [], det)
    h = login(c, "STF000001")
    synced = c.post(f"{API}/exams/{ex['exam_id']}/sync", headers=h).json()
    out = c.get(f"{API}/exams/{ex['exam_id']}/results", headers=h).json()
    check("9", "Institute staff see the attempt COMPLETED and the result, read from the Student result service over HTTP",
          synced.get("completed") == 1 and [(r["student_id"], float(r["obtained_marks"])) for r in out.get("results", [])] == [(SID, 3.0)], (synced, out))
summary()
