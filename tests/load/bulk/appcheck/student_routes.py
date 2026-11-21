"""Runs REAL Student service routes (FastAPI TestClient, in the service's own virtualenv) on a dataset in a DISPOSABLE PostgreSQL.
python student_routes.py <service> <state.json>; DATABASE_URL must be the fixture. Redis is fakeredis. The state file carries ids between
services, because each service has its own dependency set and its `app` package cannot share a process with another."""
import json
import os
import sys

import fakeredis
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

assert "127.0.0.1:55439" in os.environ["DATABASE_URL"], "the fixture only"
service, state_path = sys.argv[1], sys.argv[2]
state = json.load(open(state_path))
sys.path.insert(0, ".")
import redis  # noqa: E402
fake = fakeredis.FakeStrictRedis(decode_responses=True)
redis.Redis.from_url = lambda *_a, **_k: fake       # every service builds its client through this
from app.main import app  # noqa: E402

db = create_engine(os.environ["DATABASE_URL"])


def q(sql, **kw):
    with db.begin() as c:
        return c.execute(text(sql), kw).fetchall()


student = state["student"]
expires = "2099-01-01T00:00:00+00:00"
fake.set("student_session:s1", json.dumps({"student_id": student, "expires_at": expires}))
client = TestClient(app, raise_server_exceptions=True)
client.cookies.set("session_id", "s1")
checks = []


def call(method, path, want=200, **kw):
    r = client.request(method, path, **kw)
    checks.append((f"{method} {path.replace(str(state.get('live_test')), '{live}').replace(str(state.get('old_attempt')), '{attempt}')}", r.status_code, want))
    return r


def same(label, got, expected):
    checks.append((f"{label}: {got} vs {expected}", got, expected))


if service == "student-authentication-service":
    me = call("GET", "/auth/me").json()
    same("me is the session's student", me.get("student_id"), student)
elif service == "student-profile-service":
    same("profile email", call("GET", "/profile").json()["email"].endswith("@loadtest.example"), True)
elif service == "student-enrollment-service":
    same("enrollments listed", len(call("GET", "/enrollments").json()), q("select count(*) from enrollments where student_id=:s", s=student)[0][0])
elif service == "student-notification-service":
    rows = call("GET", "/notifications").json()
    same("notifications listed", len(rows), q("select count(*) from notification_recipients where recipient_id=:s", s=student)[0][0])
    unread = q("select count(*) from notification_recipients where recipient_id=:s and not is_read", s=student)[0][0]
    same("unread count", call("GET", "/notifications/unread-count").json()["unread_count"], unread)
elif service == "student-test-service":
    open_now = q("""select count(*) from test_audiences a join tests t using (test_id) where a.student_id=:s and t.status='ACTIVE' and now() between t.start_at and t.end_at""", s=student)[0][0]
    future = q("""select count(*) from test_audiences a join tests t using (test_id) where a.student_id=:s and t.status='ACTIVE' and t.start_at > now()""", s=student)[0][0]
    missed = q("""select count(*) from test_audiences a join tests t using (test_id) where a.student_id=:s and t.status='ACTIVE' and t.end_at < now()
                  and not exists (select 1 from test_attempts x where x.test_id=t.test_id and x.student_id=:s)""", s=student)[0][0]
    same("GET /tests rows", len(call("GET", "/tests").json()), open_now)
    same("GET /tests/scheduled rows", len(call("GET", "/tests/scheduled").json()), future)
    same("GET /tests/missed rows", len(call("GET", "/tests/missed").json()), missed)
    done = q("select test_id::text from test_attempts where student_id=:s limit 1", s=student)[0][0]
    call("GET", f"/tests/{done}/details")
    body = call("GET", f"/tests/{state['live_test']}/start").json()
    same("delivered questions", len(body["questions"]), state["live_questions"])
    state["question_ids"] = [x.get("question_id") or x.get("id") for x in body["questions"]]
elif service == "student-attempt-service":
    done = call("GET", "/attempts/completed").json()
    same("completed attempts", len(done), q("select count(*) from test_attempts where student_id=:s", s=student)[0][0])
    old = done[0]
    state["old_attempt"] = old["attempt_id"]
    call("GET", f"/attempts/test/{old['test_id']}")
    same("old attempt answers", len(call("GET", f"/attempts/{old['attempt_id']}/answers").json()),
         q("select count(*) from attempt_answers where attempt_id=:a", a=old["attempt_id"])[0][0])
    call("GET", f"/attempts/test/{state['live_test']}", want=404)            # the Tests page treats 404 as "not started"
    call("POST", "/attempts/start", json={"test_id": old["test_id"]}, want=400)    # already sat
    attempt = call("POST", "/attempts/start", json={"test_id": state["live_test"]}).json()
    state["new_attempt"] = attempt["attempt_id"]
    call("GET", f"/attempts/{attempt['attempt_id']}/integrity")
    for qid in state["question_ids"][:10]:
        call("POST", f"/attempts/{attempt['attempt_id']}/answers", json={"question_id": qid, "selected_answer": "A"})
    call("POST", f"/attempts/{attempt['attempt_id']}/answers", json={"question_id": state["question_ids"][1], "selected_answer": None})
    call("POST", f"/attempts/{attempt['attempt_id']}/submit")
    call("POST", f"/attempts/{attempt['attempt_id']}/answers", json={"question_id": state["question_ids"][0], "selected_answer": "B"}, want=400)
elif service == "student-result-service":
    listed = call("GET", "/results").json()
    same("results listed", len(listed), q("select count(*) from test_results where student_id=:s", s=student)[0][0])
    d = call("GET", f"/results/attempts/{state['old_attempt']}/details").json()
    same("old result rescored by the app", sum(x["obtained_marks"] for x in d["subjects"]), d["result"]["obtained_marks"])
    same("review open after the window", d["review_available"], True)
    r = call("POST", f"/results/attempts/{state['new_attempt']}/evaluate").json()
    same("new attempt answered", r["attempted_questions"], 9)
    call("GET", f"/results/attempts/{state['new_attempt']}/details")
elif service == "student-ranking-service":
    t = q("select test_id::text from test_results where student_id=:s limit 1", s=student)[0][0]
    fake.set("student_session:s1", json.dumps({"student_id": student, "expires_at": expires}))
    body = call("GET", f"/ranking/{t}").json()
    same("ranking total", body["total_students"], q("select count(*) from test_results where test_id=:t", t=t)[0][0])
else:
    raise SystemExit(f"unknown service {service}")

bad = [c for c in checks if c[1] != c[2]]
json.dump(state, open(state_path, "w"))
print(f"{service}: {len(checks) - len(bad)}/{len(checks)} passed")
for c in bad:
    print("  FAIL", c)
sys.exit(1 if bad else 0)
