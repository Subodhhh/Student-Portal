"""The real pilot check: drives the RUNNING load-test stack over HTTP on its loopback ports, in the order the frontends call it. Student password
login and session handling, every list page, a complete exam, and result details across the 16-week history; then staff login with MFA and
the main staff pages. Run from tests/load after the guard: reads the database only through read-only sessions and needs no service keys."""
import json
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

import httpx
import psycopg

LOAD = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(LOAD / "guard"))
os.environ.setdefault("LT_CONFIG_DIR", "./tests/load/config")
import guard  # noqa: E402

PORTS = {"auth": 18002, "profile": 18003, "enrollment": 18004, "test": 18005, "attempt": 18006, "result": 18007, "ranking": 18008, "notification": 18009}
GATEWAY = "http://127.0.0.1:18010"
SYNTH = dict(line.rstrip("\n").split("=", 1) for line in open(LOAD / "config/local/synthetic.env"))
results, timings = [], defaultdict(list)


def db(name):
    cfg = guard.load_config()
    if guard.cmd_check(cfg, ["local"]) != 0:
        raise SystemExit("guard refused")
    t = cfg["targets"][f"local-{name}"]
    return psycopg.connect(guard.libpq_url(guard.database_urls(t)[0][1], t["connect_host"]), autocommit=True, options="-c default_transaction_read_only=on")


stu, ins = db("student"), db("institute")


def same(label, got, want):
    results.append((label, got == want, got, want))


class Student:
    def __init__(self):
        self.c = httpx.Client(timeout=60, cookies=httpx.Cookies())

    def call(self, service, method, path, want=200, label=None, **kw):
        t = time.monotonic()
        r = self.c.request(method, f"http://127.0.0.1:{PORTS[service]}{path}", **kw)
        name = label or f"{method} {re.sub(r'[0-9a-f]{8}-[0-9a-f-]{27}', '{id}', path)}"
        timings[name].append(time.monotonic() - t)
        wanted = want if isinstance(want, tuple) else (want,)
        if r.status_code not in wanted:
            same(f"{name} status", r.status_code, want)
            print("   body:", r.text[:200])
        else:
            same(f"{name} status", True, True)
        return r


def redis_ttl(session_id):
    return int(subprocess.run(["docker", "exec", "lt-redis", "redis-cli", "TTL", f"student_session:{session_id}"], capture_output=True, text=True).stdout.strip())


def browse(s, sid):
    """Shell, Dashboard, Tests, Scheduled, Results, Notifications, as those pages call them."""
    s.call("auth", "GET", "/auth/me")
    s.call("profile", "GET", "/profile")
    s.call("notification", "GET", "/notifications/unread-count")
    listed = s.call("result", "GET", "/results").json()
    enrolled = s.call("enrollment", "GET", "/enrollments").json()
    same("enrollments listed", len(enrolled), stu.execute("select count(*) from enrollments where student_id=%s", (sid,)).fetchone()[0])
    same("results listed", len(listed), stu.execute("select count(*) from test_results where student_id=%s", (sid,)).fetchone()[0])
    active = s.call("test", "GET", "/tests").json()
    upcoming = s.call("test", "GET", "/tests/scheduled").json()
    missed = s.call("test", "GET", "/tests/missed").json()
    done = s.call("attempt", "GET", "/attempts/completed").json()
    same("missed listed", len(missed), stu.execute("""select count(*) from test_audiences a join tests t using (test_id) where a.student_id=%(s)s and t.status='ACTIVE' and t.end_at < now()
         and not exists (select 1 from test_attempts x where x.test_id=t.test_id and x.student_id=%(s)s)""", {"s": sid}).fetchone()[0])
    same("completed attempts listed", len(done), stu.execute("select count(*) from test_attempts where student_id=%s", (sid,)).fetchone()[0])
    same("upcoming listed", len(upcoming), stu.execute("select count(*) from test_audiences a join tests t using (test_id) where a.student_id=%s and t.status='ACTIVE' and t.start_at > now()", (sid,)).fetchone()[0])
    for t in active:
        s.call("attempt", "GET", f"/attempts/test/{t['test_id']}", want=(200, 404))
    for a in done:
        s.call("test", "GET", f"/tests/{a['test_id']}/details")
    notes = s.call("notification", "GET", "/notifications").json()
    same("notifications listed", len(notes), stu.execute("select count(*) from notification_recipients where recipient_id=%s", (sid,)).fetchone()[0])
    return listed, active, done


def history_pages(s, sid, listed):
    """Every result of the student, as ResultsPage -> TestResultPage opens it; scores recomputed from the page's own numbers."""
    pages = bad = oldest = 0
    for row in listed:
        r = s.call("result", "GET", f"/results/attempts/{row['attempt_id']}/details", label="GET /results/attempts/{id}/details").json()
        s.call("test", "GET", f"/tests/{row['test_id']}/details", label="GET /tests/{id}/details (result page)")
        pages += 1
        answered = sum(1 for q in r["questions"] if q["selected_answer"])
        total = sum(q["marks_obtained"] for q in r["questions"])
        fine = (r["review_available"] and len(r["questions"]) == r["result"]["total_questions"] and answered == r["result"]["attempted_questions"]
                and total == r["result"]["obtained_marks"] == sum(x["obtained_marks"] for x in r["subjects"]))
        bad += not fine
        oldest = max(oldest, (time.time() - time.mktime(time.strptime(row["created_at"][:19], "%Y-%m-%dT%H:%M:%S"))) / 86400)
    return pages, bad, oldest


def student_section():
    row = stu.execute("""select a.student_id, a.test_id::text, t.total_questions from test_audiences a join tests t using (test_id)
        where t.status='ACTIVE' and now() between t.start_at and t.end_at and not exists (select 1 from test_attempts x where x.test_id=t.test_id and x.student_id=a.student_id)
          and exists (select 1 from test_results r where r.student_id=a.student_id) order by t.total_questions desc, (select count(*) from test_results r where r.student_id=a.student_id) desc limit 1""").fetchone()
    assert row, "no test is open right now"
    sid, test, total = row
    email = stu.execute("select email from students where student_id=%s", (sid,)).fetchone()[0]
    s = Student()
    print(f"exam student {sid}: {total}-question test open now")

    # ---- login and session
    s.call("auth", "POST", "/auth/login", json={"login": email, "password": "wrong-password"}, want=401, label="POST /auth/login (wrong password)")
    s.call("auth", "GET", "/auth/me", want=401, label="GET /auth/me (no session)")
    s.call("attempt", "GET", "/attempts/completed", want=401, label="GET /attempts/completed (no session)")
    s.call("auth", "POST", "/auth/login", json={"login": email, "password": SYNTH["LT_SYNTH_PASSWORD"]}, label="POST /auth/login (email)")
    session = s.c.cookies.get("session_id")
    same("session cookie issued", bool(session), True)
    me = s.call("auth", "GET", "/auth/me").json()
    same("session belongs to the student", me.get("student_id"), sid)
    ttl_login = redis_ttl(session)

    # ---- every page, then the 16-week history
    listed, active, done = browse(s, sid)
    same("open test listed", test in {t["test_id"] for t in active}, True)
    pages, bad, oldest = history_pages(s, sid, listed)
    same(f"exam student's {pages} result pages agree (oldest {oldest:.0f} days)", bad, 0)
    ranked = s.call("ranking", "GET", f"/ranking/{listed[0]['test_id']}").json()
    same("ranking counts the test's results", ranked["total_students"], stu.execute("select count(*) from test_results where test_id=%s", (listed[0]["test_id"],)).fetchone()[0])

    # ---- the complete exam, as TestAttemptPage calls it
    s.call("test", "GET", f"/tests/{test}/details")
    a = s.call("attempt", "POST", "/attempts/start", json={"test_id": test}).json()
    attempt = a["attempt_id"]
    resumed = s.call("attempt", "POST", "/attempts/start", json={"test_id": test}, label="POST /attempts/start (resume)").json()
    same("start resumes the same attempt", resumed["attempt_id"], attempt)
    paper = s.call("test", "GET", f"/tests/{test}/start").json()
    same("exam delivers its questions", len(paper["questions"]), total)
    same("correct answers are not sent to the browser", any("correct_answer" in q for q in paper["questions"]), False)
    s.call("profile", "GET", "/profile")
    same("no saved answers yet", s.call("attempt", "GET", f"/attempts/{attempt}/answers").json(), [])
    s.call("attempt", "GET", f"/attempts/{attempt}/integrity")
    event = s.call("attempt", "POST", f"/attempts/{attempt}/integrity-events", json={"client_event_id": "11111111-1111-4111-8111-111111111111", "kind": "TAB_HIDDEN",
                                                                                    "occurred_at": "2026-11-04T07:40:00+00:00"}).json()
    same("one integrity warning counted", event["warnings"], 1)
    ids = [q.get("question_id") or q["id"] for q in paper["questions"]]
    for i, q in enumerate(ids[:int(total * 0.75)]):
        s.call("attempt", "POST", f"/attempts/{attempt}/answers", json={"question_id": q, "selected_answer": "ABCD"[i % 4]})
    s.call("attempt", "POST", f"/attempts/{attempt}/answers", json={"question_id": ids[0], "selected_answer": "B"}, label="POST answers (change)")
    s.call("attempt", "POST", f"/attempts/{attempt}/answers", json={"question_id": ids[1], "selected_answer": None}, label="POST answers (clear)")
    saved = s.call("attempt", "GET", f"/attempts/{attempt}/answers").json()
    same("reload keeps the saved answers", sum(1 for x in saved if x["selected_answer"]), int(total * 0.75) - 1)
    same("a question of another test is refused", s.call("attempt", "POST", f"/attempts/{attempt}/answers", want=400, label="POST answers (foreign question)",
         json={"question_id": "00000000-0000-4000-8000-000000000000", "selected_answer": "A"}).status_code, 400)
    ttl_after = redis_ttl(session)
    s.call("attempt", "POST", f"/attempts/{attempt}/submit")
    s.call("attempt", "POST", f"/attempts/{attempt}/submit", want=400, label="POST submit (again)")
    s.call("attempt", "POST", f"/attempts/{attempt}/answers", json={"question_id": ids[0], "selected_answer": "A"}, want=400, label="POST answers (after submit)")
    r = s.call("result", "POST", f"/results/attempts/{attempt}/evaluate").json()
    r2 = s.call("result", "POST", f"/results/attempts/{attempt}/evaluate", label="POST evaluate (again)").json()
    same("evaluate is idempotent", r2["result_id"], r["result_id"])
    same("attempted count", r["attempted_questions"], int(total * 0.75) - 1)
    expected = stu.execute("""select coalesce(sum(case when a.selected_answer = q.correct_answer then q.marks else -q.negative_marks end), 0) from attempt_answers a
                              join questions q using (question_id) where a.attempt_id=%s and a.selected_answer is not null""", (attempt,)).fetchone()[0]
    same("score equals a database rescore", r["obtained_marks"], expected)
    d = s.call("result", "GET", f"/results/attempts/{attempt}/details").json()
    same("answers hidden while the window is open", (d["review_available"], d["questions"]), (False, []))
    same("subject totals shown right away", sum(x["obtained_marks"] for x in d["subjects"]), r["obtained_marks"])
    s.call("test", "GET", f"/tests/{test}/details")
    s.call("attempt", "POST", "/attempts/start", json={"test_id": test}, want=400, label="POST /attempts/start (already sat)")
    same("new result appears in the list", attempt in {x["attempt_id"] for x in s.call("result", "GET", "/results").json()}, True)

    # ---- session lifetime and logout
    print(f"session ttl: {ttl_login} s after login, {ttl_after} s after the exam")
    same("session is not renewed by activity", ttl_after <= ttl_login, True)
    s.call("auth", "POST", "/auth/logout")
    s.c.cookies.set("session_id", session)
    s.call("auth", "GET", "/auth/me", want=401, label="GET /auth/me (after logout)")
    s.call("attempt", "GET", "/attempts/completed", want=401, label="GET /attempts/completed (after logout)")

    login_by_id = Student()
    login_by_id.call("auth", "POST", "/auth/login", json={"login": sid, "password": SYNTH["LT_SYNTH_PASSWORD"]}, label="POST /auth/login (student id)")


def history_sample(size=30):
    """Students spread over the activity range: every result page of each, including the oldest attempts."""
    rows = stu.execute("""select student_id from (select student_id, row_number() over (order by count(*)) n, count(*) over () c from test_results group by 1) x
                          where n %% greatest(c / %s, 1) = 0 limit %s""", (size, size)).fetchall()
    pages = bad = 0
    oldest = 0
    for (sid,) in rows:
        email = stu.execute("select email from students where student_id=%s", (sid,)).fetchone()[0]
        s = Student()
        s.call("auth", "POST", "/auth/login", json={"login": email, "password": SYNTH["LT_SYNTH_PASSWORD"]}, label="POST /auth/login (email)")
        listed = s.call("result", "GET", "/results").json()
        p, b, o = history_pages(s, sid, listed)
        pages, bad, oldest = pages + p, bad + b, max(oldest, o)
        answers = s.call("attempt", "GET", f"/attempts/{listed[-1]['attempt_id']}/answers").json()
        same("oldest attempt keeps its answers", len(answers), listed[-1]["attempted_questions"])
    same(f"history: {pages} result pages for {len(rows)} students agree (oldest {oldest:.0f} days)", bad, 0)


def staff_login(code):
    c = httpx.Client(base_url=GATEWAY, timeout=60)
    r = c.post("/api/v1/auth/login", json={"staff_code": code, "password": SYNTH["LT_SYNTH_PASSWORD"]})
    same(f"staff login {code} status", r.status_code, 200)
    body = r.json()
    if body.get("status") == "MFA_REQUIRED":
        log = subprocess.run(["docker", "logs", "--tail", "60", "lt-institute-identity-service"], capture_output=True, text=True)
        otp = re.findall(r"verification code is (\d{6})", log.stdout + log.stderr)[-1]
        r = c.post("/api/v1/auth/mfa/verify", json={"challenge_id": body["challenge_id"], "otp": otp})
        same(f"staff MFA {code} status", r.status_code, 200)
        body = r.json()
    same(f"staff {code} authenticated", body.get("status"), "AUTHENTICATED")
    return c, body["me"]


def staff_section():
    provider, branch = ins.execute("select e.provider_id, e.branch_id from enrollments e group by 1, 2 order by count(*) desc limit 1").fetchone()
    code = ins.execute("""select s.staff_code from staff s join staff_role_assignments r using (staff_id) where r.role='PROVIDER_ADMIN' and r.provider_id=%s""", (provider,)).fetchone()[0]
    teacher = ins.execute("""select s.staff_code from staff s join staff_role_assignments r using (staff_id) where r.role='TEACHER' and r.provider_id=%s limit 1""", (provider,)).fetchone()[0]
    exam = ins.execute("""select e.exam_id from exams e join exam_versions v using (exam_id) where e.branch_id=%s and e.status='PUBLISHED' and v.end_at < now() order by v.start_at limit 1""", (branch,)).fetchone()[0]
    version, paper = ins.execute("""select v.paper_version_id, p.paper_id from paper_versions v join papers p using (paper_id) where p.provider_id=%s limit 1""", (provider,)).fetchone()
    c, me = staff_login(code)
    same("provider admin role", me["primary_role"], "PROVIDER_ADMIN")
    same("csrf token issued", bool(me["csrf_token"]), True)
    pages = ["/api/v1/auth/me", "/api/v1/notifications/unread-count", "/api/v1/dashboard", f"/api/v1/branches/{branch}/enrollments?page=1&page_size=25",
             f"/api/v1/branches/{branch}/batches?page_size=100", f"/api/v1/providers/{provider}/subjects", f"/api/v1/providers/{provider}/branches?page_size=100",
             f"/api/v1/providers/{provider}/transfers?status=PENDING", f"/api/v1/students?provider_id={provider}&status=ACTIVE", "/api/v1/tests?page=1&page_size=25",
             "/api/v1/exams?status=PUBLISHED&page=1&page_size=20", f"/api/v1/exams/{exam}", f"/api/v1/exams/{exam}/assignments", f"/api/v1/exams/{exam}/results",
             f"/api/v1/paper-versions/{version}", f"/api/v1/papers/{paper}/versions", f"/api/v1/branches/{branch}/papers?page=1&page_size=20", f"/api/v1/branches/{branch}/exams?page=1&page_size=20"]
    for path in pages:
        t = time.monotonic()
        r = c.get(path)
        name = "GET " + re.sub(r"[0-9a-f]{8}-[0-9a-f-]{27}", "{id}", path.split("?")[0])
        timings[name].append(time.monotonic() - t)
        same(f"provider admin {name}", r.status_code, 200)
        if r.status_code != 200:
            print("   body:", r.text[:220])
        if path.endswith("/results"):
            body = r.json()
            items = body.get("results") or body.get("items") or []
            have = stu.execute("select count(*) from test_results t where t.test_id=(select exam_version_id::uuid from (values (%s::text)) v(exam_version_id))",
                               (str(ins.execute("select exam_version_id from exam_versions where exam_id=%s", (exam,)).fetchone()[0]),)).fetchone()[0]
            same("exam results reach the Institute", len(items), have)
    csrf = c.post("/api/v1/notifications/read-all")
    same("write without CSRF is refused", csrf.status_code, 403)
    same("write with CSRF passes", c.post("/api/v1/notifications/read-all", headers={"X-CSRF-Token": me["csrf_token"]}).status_code, 200)
    same("admin logout", c.post("/api/v1/auth/logout", headers={"X-CSRF-Token": me["csrf_token"]}).status_code in (200, 204), True)
    same("session ends at logout", c.get("/api/v1/auth/me").status_code, 401)
    t, me2 = staff_login(teacher)
    same("teacher role", me2["primary_role"], "TEACHER")
    same("teacher reads teaching dashboard", t.get("/api/v1/dashboard").status_code, 200)


if os.environ.get("LT_ONLY") != "history":
    student_section()
history_sample()
if os.environ.get("LT_ONLY") != "history":
    staff_section()
for r in results:
    if "result pages" in r[0]:
        print(r[0], "PASS" if r[1] else "FAIL")
bad = [r for r in results if not r[1]]
print(f"{len(results) - len(bad)}/{len(results)} checks passed")
for r in bad:
    print("  FAIL", r[0], "got", r[2], "want", r[3])
slow = sorted(((max(v), len(v), k) for k, v in timings.items()), reverse=True)[:14]
for top, n, name in slow:
    print(f"  {top * 1000:8.0f} ms max  {n:5d} calls  {name}")
sys.exit(1 if bad else 0)
