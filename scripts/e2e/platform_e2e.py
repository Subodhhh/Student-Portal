#!/usr/bin/env python3
"""Cross-portal black-box verification against the RUNNING local stack (docker compose up). Stdlib only.

Exercises the real path end to end, through the public APIs only:
  Institute (gateway :8010)  provider/branch/subject/batch setup -> student lookup -> enrollment (new + existing identity)
                             -> paper upload (rejected + accepted) -> preview -> approve -> publish -> exam plan -> generation
                             (missing subject recorded) -> automatic publication -> worker delivery
  Student services           reference projection -> password setup -> login -> My Institutes -> tests -> attempt -> resume
                             -> answers -> submit -> evaluation (negative marking) -> gated review -> one attempt only
  Composite                  second provider contributes a paper -> two-provider exam reaches the student
Every record it creates is labelled "QA <run>" so it is recognisable in the (test) databases.

Usage: platform_e2e.py STAFF_CODE PASSWORD     (OTP and dev password links are read from `docker logs`)
"""
import http.cookiejar
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

INST = "http://localhost:8010"
STU = {"auth": "http://localhost:8002", "enroll": "http://localhost:8004", "test": "http://localhost:8005",
       "attempt": "http://localhost:8006", "result": "http://localhost:8007", "ranking": "http://localhost:8008",
       "notify": "http://localhost:8009"}
STAFF_CODE, PASSWORD = sys.argv[1:3]
RUN = datetime.now().strftime("%m%d%H%M%S")
results: list[bool] = []


class Client:
    def __init__(self, origin: str):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.origin = origin
        self.csrf = ""

    def call(self, method, url, body=None, headers=None, raw=None, content_type=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        h = {"Origin": self.origin, **({"X-CSRF-Token": self.csrf} if self.csrf else {}), **(headers or {})}
        if data is not None:
            h["Content-Type"] = content_type or "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=h)
        try:
            with self.opener.open(req, timeout=30) as r:
                payload = r.read()
                return r.status, (json.loads(payload) if payload else None)
        except urllib.error.HTTPError as e:
            payload = e.read()
            try:
                return e.code, json.loads(payload)
            except ValueError:
                return e.code, payload.decode(errors="replace")[:300]


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {str(detail)[:400]}"), flush=True)
    return ok


def wait_for(fn, timeout=150, every=3):
    deadline = time.time() + timeout
    while True:
        out = fn()
        if out or time.time() > deadline:
            return out
        time.sleep(every)


def logs(container: str, since: str = "10m") -> str:
    p = subprocess.run(["docker", "logs", "--since", since, container], capture_output=True, text=True)
    return p.stdout + p.stderr


def multipart(fields: dict, filename: str, content: bytes, ctype: str):
    boundary = "----qa" + uuid.uuid4().hex
    parts = [f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode() for k, v in fields.items()]
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
                 f"Content-Type: {ctype}\r\n\r\n".encode() + content + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


def staff_session(full_name: str, email: str, role: str, provider_id: str, branch_id: str | None = None):
    """Create a staff member through the API, activate the account from the (dev console) verification email, log in."""
    s, st = I("POST", "/staff", {"full_name": full_name, "email": email, "assignment": {"role": role, "provider_id": provider_id, "branch_id": branch_id}})
    check(f"create {role} staff account", s == 201, (s, st))
    token = wait_for(lambda: next(iter(re.findall(r"to=" + re.escape(email) + r"[^\n]*\n[^\n]*verify-email\?token=([A-Za-z0-9_\-]+)",
                                                    logs("institute-identity-service", "3m"))), None), 20, 1)
    pw = "Qa-Staff-" + uuid.uuid4().hex[:12] + "!x"
    c = Client("http://localhost:8081")
    s, b = c.call("POST", INST + "/api/v1/auth/verify-email", {"token": token, "new_password": pw})
    check(f"{role} activates the account from the emailed link", s == 200, (s, b))
    s, b = c.call("POST", INST + "/api/v1/auth/login", {"staff_code": st["staff_code"], "password": pw})
    if s == 200 and b.get("status") == "MFA_REQUIRED":
        time.sleep(1)
        otp = wait_for(lambda: (re.findall(r"to=" + re.escape(email) + r"[^\n]*\n[^\n]*code is (\d{6})", logs("institute-identity-service", "2m")) or [None])[-1], 20, 1)
        s, b = c.call("POST", INST + "/api/v1/auth/mfa/verify", {"challenge_id": b["challenge_id"], "otp": otp})
    ok = check(f"{role} logs in", s == 200 and "me" in b, (s, b))
    if ok:
        c.csrf = b["me"]["csrf_token"]
    st["password"] = pw
    return c, st


def docx_paper(lines: list[str]) -> bytes:
    import io
    import zipfile
    from xml.sax.saxutils import escape
    body = "".join(f"<w:p><w:r><w:t xml:space=\"preserve\">{escape(x)}</w:t></w:r></w:p>" for x in lines)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="xml" ContentType="application/xml"/></Types>')
        z.writestr("word/document.xml", '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                   f"<w:body>{body}</w:body></w:document>")
    return buf.getvalue()


inst = Client("http://localhost:8081")
I = lambda m, p, b=None, h=None: inst.call(m, INST + "/api/v1" + p, b, h)  # noqa: E731

# ----------------------------------------------------------------------------------------------------------- institute login
s, b = I("POST", "/auth/login", {"staff_code": STAFF_CODE, "password": PASSWORD})
check("institute login asks for MFA", s == 200 and b.get("status") == "MFA_REQUIRED", (s, b))
otp = wait_for(lambda: (re.findall(r"code is (\d{6})", logs("institute-identity-service", "2m")) or [None])[-1], 20, 1)
s, b = I("POST", "/auth/mfa/verify", {"challenge_id": b["challenge_id"], "otp": otp})
check("MFA creates a staff session", s == 200 and "me" in b, (s, b))
inst.csrf = b["me"]["csrf_token"]

s, h = I("GET", "/system/health")
check("Super Admin service health: all 7 Institute services ready and no failed queues", s == 200 and h["healthy"] and len(h["services"]) == 7, (s, h))

# ----------------------------------------------------------------------------------------------------------- organisation
s, a = I("POST", "/providers", {"name": f"QA Provider A {RUN}", "email": f"qa-a-{RUN}@example.com"})
check("create provider A", s == 201, (s, a))
s, bp = I("POST", "/providers", {"name": f"QA Provider B {RUN}", "email": f"qa-b-{RUN}@example.com"})
check("create provider B", s == 201, (s, bp))
PA, PB = a["provider_id"], bp["provider_id"]
s, br = I("POST", f"/providers/{PA}/branches", {"name": f"QA A Main {RUN}", "city": "Pune", "state": "MH"})
check("create branch A1", s == 201, (s, br))
BA = br["branch_id"]
s, br = I("POST", f"/providers/{PB}/branches", {"name": f"QA B Main {RUN}", "city": "Mumbai", "state": "MH"})
check("create branch B1", s == 201, (s, br))
BB = br["branch_id"]
subjects = {}
for code, name in (("PHY", "Physics"), ("CHE", "Chemistry")):
    s, sj = I("POST", "/subjects", {"code": f"QA{code}{RUN}", "name": f"QA {name} {RUN}"})
    check(f"create subject {name}", s == 201, (s, sj))
    subjects[code] = sj["subject_id"]
    for pid in (PA, PB):
        s, _ = I("PUT", f"/providers/{pid}/subjects/{sj['subject_id']}", {})
        check(f"enable {name} for provider", s in (200, 201, 204), (s, _))
s, bt = I("POST", f"/branches/{BA}/batches", {"name": f"QA Batch A {RUN}"})
check("create batch in A1", s == 201, (s, bt))
BATCH_A = bt["batch_id"]
s, bt = I("POST", f"/branches/{BB}/batches", {"name": f"QA Batch B {RUN}"})
check("create batch in B1", s == 201, (s, bt))
BATCH_B = bt["batch_id"]

# ----------------------------------------------------------------------------------------------------------- staff roles
pa, pa_staff = staff_session(f"QA Provider Admin {RUN}", f"qa-pa-{RUN}@example.com", "PROVIDER_ADMIN", PA)
teacher, t_staff = staff_session(f"QA Teacher {RUN}", f"qa-teacher-{RUN}@example.com", "TEACHER", PA)
s, b = I("POST", "/teaching", {"staff_id": t_staff["staff_id"], "branch_id": BA, "subject_id": subjects["CHE"], "batch_id": BATCH_A})
check("assign the teacher to Chemistry for batch A (teacher-subject mapping)", s == 201, (s, b))

# ----------------------------------------------------------------------------------------------------------- lookup + enrollment
email = f"qa-student-{RUN}@example.com"
s, lk = I("POST", "/students/lookup", {"branch_id": BA, "email": email})
check("student lookup works (no 503) and finds no match for a new email", s == 200 and lk["matches"] == [], (s, lk))
s, en = I("POST", f"/branches/{BA}/enrollments", {"new_student": {"first_name": "QA", "last_name": f"Student{RUN}", "email": email},
                                                 "batch_ids": [BATCH_A]})
check("enroll a NEW student in provider A (identity created in the Student service)", s == 201 and en["student_id"].startswith("STU"), (s, en))
SID = en.get("student_id")
s, lk = I("POST", "/students/lookup", {"branch_id": BB, "email": email})
check("provider B lookup finds the existing global student", s == 200 and [m["student_id"] for m in lk["matches"]] == [SID], (s, lk))
s, en2 = I("POST", f"/branches/{BB}/enrollments", {"student_id": SID, "batch_ids": [BATCH_B]})
check("provider B enrolls the SAME student (no duplicate identity)", s == 201 and en2["student_id"] == SID, (s, en2))
s, dup = I("POST", f"/branches/{BB}/enrollments", {"new_student": {"first_name": "Dup", "last_name": "Person", "email": email}})
check("re-registering the same email is refused (link instead)", s == 409, (s, dup))
s, roster = I("GET", f"/branches/{BA}/enrollments")
check("enrollment persisted in the Institute database (roster)", s == 200 and any(e["student_id"] == SID for e in roster["items"]), (s, roster))

# ----------------------------------------------------------------------------------------------------------- papers
HEAD = "question,option_a,option_b,option_c,option_d,correct,marks,negative_marks\n"
bad_csv = (HEAD + f"QA {RUN} what is 2+2?,1,2,3,4,D,4,1\nQA {RUN} broken row,,2,3,4,Z,4,1\n").encode()
raw, ct = multipart({"title": f"QA bad paper {RUN}"}, "bad.csv", bad_csv, "text/csv")
s, b = inst.call("POST", f"{INST}/api/v1/branches/{BA}/subjects/{subjects['PHY']}/papers/upload", raw=raw, content_type=ct)
check("a paper with one invalid row is rejected as a whole, with row errors", s == 422 and b["detail"]["code"] == "EXTRACTION_FAILED"
      and b["detail"]["errors"], (s, b))
s, papers = I("GET", f"/branches/{BA}/papers")
check("nothing from the rejected paper was imported", s == 200 and papers["total"] == 0, (s, papers))

phy_csv = (HEAD + f"QA {RUN} Q1 speed of light?,3e8 m/s,3e6 m/s,30 m/s,3 m/s,A,4,1\n"
           f"QA {RUN} Q2 unit of force?,Joule,Newton,Watt,Pascal,B,4,1\n"
           f"QA {RUN} Q3 g on earth?,9.8,1.6,3.7,24.8,A,4,1\n").encode()
raw, ct = multipart({"title": f"QA Physics paper {RUN}"}, "phy.csv", phy_csv, "text/csv")
s, up = inst.call("POST", f"{INST}/api/v1/branches/{BA}/subjects/{subjects['PHY']}/papers/upload", raw=raw, content_type=ct)
check("valid CSV paper is extracted into a VALIDATED version", s == 201 and up["status"] == "VALIDATED" and up["question_count"] == 3, (s, up))
PHY_PAPER, PHY_PV = up["paper_id"], up["paper_version_id"]
s, pv = I("GET", f"/paper-versions/{PHY_PV}")
check("preview shows the extracted questions before acceptance", s == 200 and len(pv.get("questions", [])) == 3, (s, pv))
raw, ct = multipart({"title": f"QA Physics paper {RUN}"}, "phy.csv", phy_csv, "text/csv")
s, b = inst.call("POST", f"{INST}/api/v1/branches/{BA}/subjects/{subjects['PHY']}/papers/upload", raw=raw, content_type=ct)
check("uploading an identical paper again is refused as a duplicate", s == 409, (s, b))
s, b = I("POST", f"/paper-versions/{PHY_PV}/approve", {"note": "QA check"})
check("approve paper version", s == 200 and b["status"] == "APPROVED", (s, b))
s, b = I("POST", f"/paper-versions/{PHY_PV}/publish", {"note": "QA check"})
check("publish paper version", s == 200 and b["status"] == "PUBLISHED", (s, b))

che_csv = (HEAD + f"QA {RUN} C1 symbol of sodium?,Na,So,S,N,A,4,1\n").encode()
raw, ct = multipart({"title": f"QA Chemistry draft {RUN}"}, "che.csv", che_csv, "text/csv")
s, upc = inst.call("POST", f"{INST}/api/v1/branches/{BA}/subjects/{subjects['CHE']}/papers/upload", raw=raw, content_type=ct)
check("chemistry paper uploaded but left unapproved", s == 201, (s, upc))

# ----------------------------------------------------------------------------------------------------------- exam plan + generation
now = datetime.now(timezone.utc)
plan_body = {"name": f"QA Unified Exam {RUN}", "description": "QA verification exam", "duration_minutes": 30,
             "start_at": iso(now + timedelta(seconds=20)), "end_at": iso(now + timedelta(hours=3)), "auto_publish": True,
             "batch_ids": [BATCH_A], "slots": [{"subject_id": subjects["PHY"], "paper_id": PHY_PAPER},
                                               {"subject_id": subjects["CHE"], "paper_id": upc["paper_id"]}]}
s, plan = I("POST", f"/branches/{BA}/exam-plans", plan_body)
check("create exam plan (Physics + Chemistry slots, batch A)", s == 201, (s, plan))
s, job = I("POST", f"/exam-plans/{plan['plan_id']}/generate", {})
check("generation builds the exam from available papers and records the missing subject (PARTIAL)",
      s in (200, 201) and job["status"] == "PARTIAL" and job["exam_id"], (s, job))
EXAM = job["exam_id"]
s, ex = I("GET", f"/exams/{EXAM}")
check("exam auto-published per plan with one shared duration", s == 200 and ex["status"] == "PUBLISHED" and ex["duration_minutes"] == 30
      and ex["total_questions"] == 3, (s, ex))
TEST_ID = ex["exam_version_id"]
s, asg = I("GET", f"/exams/{EXAM}/assignments")
check("the enrolled batch student is assigned", s == 200 and [x["student_id"] for x in asg["items"]] == [SID], (s, asg))

def titles(c):
    s, b = c.call("GET", INST + "/api/v1/notifications?page_size=50")
    return [n.get("title", "") for n in (b or {}).get("items", [])] if s == 200 else []
got = wait_for(lambda: any("Missing paper" in x and RUN in x for x in titles(teacher)), 60, 3)
check("the responsible Chemistry teacher is notified about the missing paper", got, titles(teacher)[:5])
got = wait_for(lambda: any("Missing paper" in x and RUN in x for x in titles(pa)), 60, 3)
check("the provider admin is notified about the missing paper", got, titles(pa)[:5])

delivered = wait_for(lambda: (I("GET", f"/exams/{EXAM}")[1].get("delivery_status") == "SENT"), 150, 5)
check("worker delivered the exam to the Student test service (outbox SENT)", delivered, I("GET", f"/exams/{EXAM}"))

# ----------------------------------------------------------------------------------------------------------- student portal
stu = Client("http://localhost:8080")
s, b = stu.call("POST", STU["auth"] + "/auth/forgot-password", {"email": email})
check("student requests a password setup link", s == 200, (s, b))
token = wait_for(lambda: (re.findall(r"reset-password\?token=([A-Za-z0-9_\-]+)", logs("student-authentication-service", "2m")) or [None])[-1], 20, 1)
STUDENT_PW = "Qa-Student-" + uuid.uuid4().hex[:12]
s, b = stu.call("POST", STU["auth"] + "/auth/reset-password", {"token": token, "new_password": STUDENT_PW})
check("student sets a password with the emailed (dev console) link", s == 200, (s, b))
s, b = stu.call("POST", STU["auth"] + "/auth/login", {"login": SID, "password": STUDENT_PW})
check("student logs in with the global Student ID", s == 200, (s, b))
s, me = stu.call("GET", STU["auth"] + "/auth/me")
check("session identifies the right student", s == 200 and me["student_id"] == SID, (s, me))

def my_providers():
    s, rows = stu.call("GET", STU["enroll"] + "/enrollments")
    return {r["provider"]["provider_id"] for r in rows} if s == 200 else set()
ok = wait_for(lambda: {PA, PB} <= my_providers(), 60, 3)
check("My Institutes shows BOTH providers (Institute enrollments projected to the Student DB)", ok, my_providers())

def visible_tests():
    s, rows = stu.call("GET", STU["test"] + "/tests")
    return {r["test_id"]: r for r in rows} if s == 200 else {}
tests = wait_for(lambda: visible_tests() if TEST_ID in visible_tests() else None, 90, 3) or {}
check("the unified exam is listed for the student once its window opens", TEST_ID in tests, tests.keys())
if TEST_ID in tests:
    check("the listed test shows the correct provider", tests[TEST_ID]["provider"]["provider_id"] == PA, tests[TEST_ID])

s, att = stu.call("POST", STU["attempt"] + "/attempts/start", {"test_id": TEST_ID})
check("student starts the attempt", s == 200 and att["status"] == "IN_PROGRESS", (s, att))
AID = att["attempt_id"]
s, ws = stu.call("GET", f"{STU['attempt']}/attempts/{AID}/workspace")
check("workspace returns the questions with one shared timer", s == 200 and len(ws["questions"]) == 3 and ws["remaining_seconds"] > 0, (s, ws))
qs = sorted(ws["questions"], key=lambda q: q["question_order"])
stu.call("POST", f"{STU['attempt']}/attempts/{AID}/answers", {"question_id": qs[0]["question_id"], "selected_answer": "A"})   # correct
stu.call("POST", f"{STU['attempt']}/attempts/{AID}/answers", {"question_id": qs[1]["question_id"], "selected_answer": "C"})   # wrong
s, again = stu.call("POST", STU["attempt"] + "/attempts/start", {"test_id": TEST_ID})
check("reconnecting resumes the SAME attempt (no second attempt)", s == 200 and again["attempt_id"] == AID, (s, again))
s, ans = stu.call("GET", f"{STU['attempt']}/attempts/{AID}/answers")
check("answers persisted across the reconnect", s == 200 and len([a for a in ans if a["selected_answer"]]) == 2, (s, ans))
s, sub = stu.call("POST", f"{STU['attempt']}/attempts/{AID}/submit")
check("submit the attempt", s == 200 and sub["status"] == "SUBMITTED", (s, sub))
s, sub2 = stu.call("POST", f"{STU['attempt']}/attempts/{AID}/submit")
check("a duplicate submit is refused safely", s == 400, (s, sub2))
s, res = stu.call("POST", f"{STU['result']}/results/attempts/{AID}/evaluate")
check("evaluation applies negative marking: +4 correct, -1 wrong, 0 unanswered = 3/12",
      s == 200 and res["obtained_marks"] == 3 and res["total_marks"] == 12 and res["unanswered_questions"] == 1, (s, res))
s, res2 = stu.call("POST", f"{STU['result']}/results/attempts/{AID}/evaluate")
check("re-evaluation is idempotent (same result row)", s == 200 and res2["result_id"] == res["result_id"], (s, res2))
s, det = stu.call("GET", f"{STU['result']}/results/attempts/{AID}/details")
check("answer review stays closed while the window is open (no answer key leak)", s == 200 and det["review_available"] is False
      and det["questions"] == [] and det["subjects"], (s, det))
s, b = stu.call("POST", STU["attempt"] + "/attempts/start", {"test_id": TEST_ID})
check("one attempt per scheduled exam: starting again after submit is refused", s == 400, (s, b))
s, rk = stu.call("GET", f"{STU['ranking']}/ranking/{TEST_ID}")
check("ranking visible to a participant", s == 200 and rk["my_rank"] == 1, (s, rk))
s, rk = stu.call("GET", f"{STU['ranking']}/ranking/{uuid.uuid4()}")
check("ranking of a test the student did not take is not disclosed", s == 404, (s, rk))
anon = Client("http://localhost:8080")
s, b = anon.call("GET", f"{STU['attempt']}/attempts/{AID}")
check("attempts are not readable without the student's session", s == 401, (s, b))
s, b = anon.call("POST", STU["test"] + "/internal/v1/exams", {})
check("Student internal APIs refuse calls without a service key", s == 401, (s, b))

synced = wait_for(lambda: any(r["student_id"] == SID and r.get("obtained_marks") == 3 for r in (I("GET", f"/exams/{EXAM}/results")[1] or {}).get("results", [])), 30, 3)
check("the Institute sees the student's result (read through the result service)", synced, I("GET", f"/exams/{EXAM}/results"))

def inbox_titles():
    s, rows = stu.call("GET", STU["notify"] + "/notifications")
    items = rows if isinstance(rows, list) else (rows or {}).get("items", []) if isinstance(rows, dict) else []
    return [str(r.get("title", "")) for r in items]
got = wait_for(lambda: any(f"QA Unified Exam {RUN}" in t for t in inbox_titles()), 90, 5)
check("the student's inbox received the 'new test scheduled' notification", got, inbox_titles()[:5])

# ----------------------------------------------------------------------------------------------------------- composite (A + B)
che_b = (HEAD + f"QA {RUN} B1 pH of water?,7,1,14,0,A,4,1\nQA {RUN} B2 H2O is?,Water,Salt,Acid,Base,A,4,1\n").encode()
raw, ct = multipart({"title": f"QA B Chemistry {RUN}"}, "cheb.csv", che_b, "text/csv")
s, upb = inst.call("POST", f"{INST}/api/v1/branches/{BB}/subjects/{subjects['CHE']}/papers/upload", raw=raw, content_type=ct)
check("provider B uploads a chemistry paper", s == 201, (s, upb))
I("POST", f"/paper-versions/{upb['paper_version_id']}/approve", {"note": "QA"})
s, b = I("POST", f"/paper-versions/{upb['paper_version_id']}/publish", {"note": "QA"})
check("provider B publishes it", s == 200 and b["status"] == "PUBLISHED", (s, b))
s, cr = I("POST", f"/branches/{BA}/composite-requests", {"name": f"QA Composite {RUN}", "slots": [{"subject_id": subjects["CHE"], "provider_id": PB}]},
          {"Idempotency-Key": f"qa-composite-{RUN}"})
check("provider A opens a composite request inviting provider B for chemistry", s == 201 and cr["status"] == "OPEN", (s, cr))
slot = cr["slots"][0]
s, b = I("POST", f"/composite-requests/{cr['request_id']}/slots/{slot['slot_id']}/contribute", {"paper_version_id": upb["paper_version_id"]})
check("provider B contributes its published paper (share granted, request complete)", s == 200 and b["status"] == "COMPLETE", (s, b))
now = datetime.now(timezone.utc)
s, plan2 = I("POST", f"/branches/{BA}/exam-plans", {
    "name": f"QA Composite Exam {RUN}", "duration_minutes": 20, "start_at": iso(now + timedelta(seconds=15)),
    "end_at": iso(now + timedelta(hours=2)), "auto_publish": True, "batch_ids": [BATCH_A],
    "slots": [{"subject_id": subjects["PHY"], "paper_version_id": PHY_PV}, {"subject_id": subjects["CHE"], "paper_version_id": upb["paper_version_id"]}]})
check("provider A builds a plan from its own paper + the contributed paper (exact versions)", s == 201, (s, plan2))
s, job2 = I("POST", f"/exam-plans/{plan2['plan_id']}/generate", {})
check("composite exam generated with both subjects", s in (200, 201) and job2["status"] == "SUCCEEDED", (s, job2))
s, ex2 = I("GET", f"/exams/{job2['exam_id']}")
TEST2 = ex2["exam_version_id"]
check("composite exam has 5 questions across two providers' papers", ex2["total_questions"] == 5, ex2)
tests = wait_for(lambda: visible_tests() if TEST2 in visible_tests() else None, 150, 5) or {}
check("composite exam reaches the unified Student Portal", TEST2 in tests, tests.keys())
s, att2 = stu.call("POST", STU["attempt"] + "/attempts/start", {"test_id": TEST2})
s, ws2 = stu.call("GET", f"{STU['attempt']}/attempts/{att2['attempt_id']}/workspace")
subjects_seen = {q["subject"] for q in ws2.get("questions", [])}
check("composite attempt spans both subjects under one timer", s == 200 and len(subjects_seen) == 2, (s, subjects_seen))
for q in ws2["questions"]:
    stu.call("POST", f"{STU['attempt']}/attempts/{att2['attempt_id']}/answers", {"question_id": q["question_id"], "selected_answer": "A"})
stu.call("POST", f"{STU['attempt']}/attempts/{att2['attempt_id']}/submit")
s, r2 = stu.call("POST", f"{STU['result']}/results/attempts/{att2['attempt_id']}/evaluate")
# Physics key A,B,A -> answering A everywhere: 2 right (+8) 1 wrong (-1); Chemistry key A,A -> +8. Total 15 / 20.
check("composite evaluation across subjects (15/20)", s == 200 and r2["obtained_marks"] == 15 and r2["total_marks"] == 20, (s, r2))

# ----------------------------------------------------------------------------------------------------------- role scoping
s, b = pa.call("GET", INST + "/api/v1/providers?page_size=100")
check("provider admin sees only their own provider", s == 200 and [p["provider_id"] for p in b["items"]] == [PA], (s, b))
s, b = pa.call("GET", f"{INST}/api/v1/branches/{BB}/enrollments")
check("provider admin cannot read another provider's roster", s == 403, (s, b))
s, b = pa.call("POST", INST + "/api/v1/students/lookup", {"branch_id": BB, "email": email})
check("provider admin cannot look up students for another provider's branch", s == 403, (s, b))
s, b = pa.call("GET", f"{INST}/api/v1/exams/{EXAM}/results")
check("provider admin reads their own exam results", s == 200, (s, b))
s, b = teacher.call("GET", f"{INST}/api/v1/branches/{BA}/enrollments")
check("teacher cannot manage enrollments", s == 403, (s, b))
s, b = teacher.call("POST", f"{INST}/api/v1/branches/{BA}/exam-plans", plan_body)
check("teacher cannot create exam plans", s == 403, (s, b))
s, b = teacher.call("POST", INST + "/api/v1/providers", {"name": f"QA Escalation {RUN}", "email": f"x-{RUN}@example.com"})
check("teacher cannot create providers (no privilege escalation)", s == 403, (s, b))
s, b = teacher.call("POST", f"{INST}/api/v1/exams/{EXAM}/results/{SID}/adjustments", {"obtained_marks": 12, "reason": "trying to raise marks"}, {"Idempotency-Key": f"qa-adj-{RUN}"})
check("teacher cannot adjust results (Super Admin only)", s == 403, (s, b))
raw, ct = multipart({"title": f"QA Chemistry docx {RUN}"}, "che.docx", docx_paper([
    "Default marks: 4", "Default negative: 1", f"1. QA {RUN} Atomic number of carbon?", "A) 6", "B) 12", "C) 8", "D) 14", "Answer: A",
    f"2. QA {RUN} Formula of table salt?", "A) KCl", "B) NaCl", "C) NaOH", "D) HCl", "Answer: B"]),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
s, b = teacher.call("POST", f"{INST}/api/v1/branches/{BA}/subjects/{subjects['CHE']}/papers/upload", raw=raw, content_type=ct)
check("the Chemistry teacher uploads a Word (.docx) paper that is extracted and validated", s == 201 and b["question_count"] == 2, (s, b))
s, b = teacher.call("POST", f"{INST}/api/v1/branches/{BA}/subjects/{subjects['PHY']}/papers/upload", raw=raw, content_type=ct)
check("a teacher cannot upload for a subject they do not teach", s == 403, (s, b))

if os.environ.get("OCR_SAMPLE"):   # a rendered scan of a printed page (PNG); only meaningful where OCR_ENGINE is configured
    png = open(os.environ["OCR_SAMPLE"], "rb").read()
    raw, ct = multipart({"title": f"QA scanned Chemistry {RUN}"}, "scan.png", png, "image/png")
    s, b = inst.call("POST", f"{INST}/api/v1/branches/{BA}/subjects/{subjects['CHE']}/papers/upload", raw=raw, content_type=ct)
    check("a scanned (PNG) printed paper is OCR-extracted into a VALIDATED draft awaiting approval", s == 201 and b["status"] == "VALIDATED"
          and b["question_count"] == 1, (s, b))
    if s == 201:
        s, pvd = I("GET", f"/paper-versions/{b['paper_version_id']}")
        q = (pvd.get("questions") or [{}])[0]
        check("the OCR draft preview shows exactly what was printed", q.get("option_b") == "Carbon dioxide" and q.get("correct_option") == "B", q)

if os.environ.get("QA_OUT"):   # credentials of the QA accounts for follow-up UI checks (written outside the repository)
    with open(os.environ["QA_OUT"], "w") as fh:
        json.dump({"run": RUN, "student_id": SID, "student_password": STUDENT_PW, "pa_code": pa_staff["staff_code"],
                   "pa_email": f"qa-pa-{RUN}@example.com", "pa_password": pa_staff["password"], "teacher_code": t_staff["staff_code"],
                   "teacher_password": t_staff["password"], "provider_a": PA, "branch_a": BA, "exam": EXAM, "test_id": TEST_ID}, fh)
    os.chmod(os.environ["QA_OUT"], 0o600)

print(f"\nQA run {RUN}: student {SID}, providers {PA} / {PB}")
print(f"{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
