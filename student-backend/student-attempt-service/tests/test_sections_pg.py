"""Unified Global Test (tests.sections) in the attempt service, on a DISPOSABLE PostgreSQL only (skipped otherwise):

    TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:55432/stu_verify pytest tests/test_sections_pg.py

A test whose sections hold no paper (a card that explains every subject) cannot be started; a sectioned test keeps the configured
subject order with an order of its own for every student inside each subject, stable across reloads, and answers stay on their
question; a test without sections keeps the existing order."""
import json
import os
import re
import uuid

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

URL = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not (URL.startswith("postgresql") and re.search(r"@(127\.0\.0\.1|localhost)[:/].*(verify|test|scratch|tmp|disposable)", URL)),
    reason="needs a disposable local PostgreSQL in TEST_DATABASE_URL")


@pytest.fixture()
def env(monkeypatch):
    from app import auth
    from app.database import get_db
    from app.main import app
    from app.services import expiry

    engine = create_engine(URL)
    Session = sessionmaker(bind=engine, autoflush=False)

    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    monkeypatch.setattr(expiry, "SessionLocal", Session)
    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(auth, "redis_client", fake)
    run = uuid.uuid4().hex[:8]
    p, b = uuid.uuid4(), uuid.uuid4()
    students = [f"STS{run[:5].upper()}{i}" for i in range(3)]
    with engine.begin() as c:
        for i, sid in enumerate(students):
            c.execute(text("INSERT INTO students (student_id, first_name, last_name, email, status, created_at, updated_at) "
                           "VALUES (:s, 'Sec', 'Tion', :e, 'ACTIVE', now(), now())"), {"s": sid, "e": f"s{i}-{run}@example.com"})
            fake.set(f"student_session:sess-{run}-{i}", json.dumps({"student_id": sid, "expires_at": "2999-01-01T00:00:00+00:00"}))
        c.execute(text("INSERT INTO providers (provider_id, name, email, status, created_at, updated_at) VALUES (:p, 'P', :e, 'ACTIVE', now(), now())"),
                  {"p": p, "e": f"p-{run}@example.com"})
        c.execute(text("INSERT INTO branches (branch_id, provider_id, name, status, created_at, updated_at) VALUES (:b, :p, 'B', 'ACTIVE', now(), now())"),
                  {"b": b, "p": p})

    def make_test(questions_per_subject: dict[str, int], sections=None) -> tuple[uuid.UUID, list[uuid.UUID]]:
        tid, qids, order = uuid.uuid4(), [], 0
        total = sum(questions_per_subject.values())
        with engine.begin() as c:
            c.execute(text("""INSERT INTO tests (test_id, provider_id, branch_id, name, duration_minutes, total_questions, total_marks, start_at, end_at,
                              status, sections, created_at, updated_at) VALUES (:t, :p, :b, 'Global', 30, :n, :m, now() - interval '1 hour',
                              now() + interval '2 hours', 'ACTIVE', CAST(:s AS json), now(), now())"""),
                      {"t": tid, "p": p, "b": b, "n": total, "m": 4 * total, "s": json.dumps(sections) if sections is not None else None})
            for subject, n in questions_per_subject.items():
                for _ in range(n):
                    order += 1
                    q = uuid.uuid4()
                    qids.append(q)
                    c.execute(text("""INSERT INTO questions (question_id, test_id, question_order, subject, question_text, question_type, option_a,
                                      option_b, option_c, option_d, correct_answer, marks, negative_marks, created_at, updated_at)
                                      VALUES (:q, :t, :o, :s, :x, 'MCQ', 'a', 'b', 'c', 'd', 'A', 4, 1, now(), now())"""),
                              {"q": q, "t": tid, "o": order, "s": subject, "x": f"{subject} {order}"})
            for sid in students:
                c.execute(text("INSERT INTO test_audiences (test_audience_id, test_id, audience_type, student_id, created_at) "
                               "VALUES (:a, :t, 'STUDENT', :s, now())"), {"a": uuid.uuid4(), "t": tid, "s": sid})
        return tid, qids

    clients = [TestClient(app, cookies={"session_id": f"sess-{run}-{i}"}) for i in range(3)]
    for c in clients:
        c.__enter__()
    yield clients, engine, make_test, students
    for c in clients:
        c.__exit__(None, None, None)
    app.dependency_overrides.clear()


SECTIONS = [{"position": 1, "subject": "Physics", "status": "PAPER", "message": None, "question_count": 5},
            {"position": 2, "subject": "Chemistry", "status": "PAPER", "message": None, "question_count": 5},
            {"position": 3, "subject": "Biology", "status": "NOT_ENROLLED", "message": "You are not enrolled in Biology", "question_count": 0}]


def test_a_card_without_any_paper_cannot_be_started(env):
    clients, engine, make_test, _ = env
    tid, _ = make_test({}, [{"position": 1, "subject": "Physics", "status": "NO_SUBMISSION", "message": "No paper", "question_count": 0}])
    r = clients[0].post("/attempts/start", json={"test_id": str(tid)})
    assert r.status_code == 400 and "No paper is available" in r.json()["detail"]
    with engine.connect() as c:
        assert c.execute(text("select count(*) from test_attempts where test_id = :t"), {"t": tid}).scalar() == 0   # no fake attempt


def test_sections_keep_their_order_and_each_student_gets_a_stable_order_of_their_own(env):
    clients, _engine, make_test, _ = env
    tid, _qids = make_test({"Chemistry": 5, "Physics": 5}, SECTIONS)        # stored Chemistry first: the configured order must win
    orders = []
    for c in clients:
        attempt = c.post("/attempts/start", json={"test_id": str(tid)}).json()
        first = c.get(f"/attempts/{attempt['attempt_id']}/workspace").json()["questions"]
        assert [q["subject"] for q in first] == ["Physics"] * 5 + ["Chemistry"] * 5          # configured subject order
        again = c.get(f"/attempts/{attempt['attempt_id']}/workspace").json()["questions"]
        assert [q["question_id"] for q in again] == [q["question_id"] for q in first]          # stable across reloads
        resumed = c.post("/attempts/start", json={"test_id": str(tid)}).json()
        assert resumed["attempt_id"] == attempt["attempt_id"]                                  # one attempt: start again resumes it
        orders.append([q["question_id"] for q in first])
    assert len({tuple(o) for o in orders}) > 1                                                 # students do not share one order
    # an answer stays on its question through a reload
    c = clients[0]
    attempt = c.post("/attempts/start", json={"test_id": str(tid)}).json()
    target = orders[0][3]
    assert c.post(f"/attempts/{attempt['attempt_id']}/answers", json={"question_id": target, "selected_answer": "B"}).status_code == 200
    after = {q["question_id"]: q for q in c.get(f"/attempts/{attempt['attempt_id']}/workspace").json()["questions"]}
    assert after[target]["selected_answer"] == "B" and sum(1 for q in after.values() if q["selected_answer"]) == 1


def test_a_test_without_sections_keeps_the_existing_order(env):
    clients, _engine, make_test, _ = env
    tid, _ = make_test({"Physics": 2, "Chemistry": 2})
    attempt = clients[0].post("/attempts/start", json={"test_id": str(tid)}).json()
    qs = clients[0].get(f"/attempts/{attempt['attempt_id']}/workspace").json()["questions"]
    assert [(q["subject"], q["question_order"]) for q in qs] == [("Chemistry", 3), ("Chemistry", 4), ("Physics", 1), ("Physics", 2)]
