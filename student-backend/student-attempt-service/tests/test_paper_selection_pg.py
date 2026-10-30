"""Global Test with paper choice paper selection and the attempt snapshot, on a DISPOSABLE PostgreSQL only (skipped otherwise; row locks need it):

    TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:55432/stu_verify pytest tests/test_paper_selection_pg.py

Proves that an incomplete or stale choice can never enter an attempt: a save and a Start serialize on the student's selection rows,
a Start snapshots only what that lock returned, and a duplicate Start returns the existing attempt."""
import json
import os
import re
import threading
import time
import uuid
from uuid import UUID

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

URL = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not (URL.startswith("postgresql") and re.search(r"@(127\.0\.0\.1|localhost)[:/].*(verify|test|scratch|tmp|disposable)", URL)),
    reason="needs a disposable local PostgreSQL in TEST_DATABASE_URL")

PA, PB, PC1, PC2, PD = (uuid.uuid4() for _ in range(5))
SUBJECTS = [   # what the test service's eligibility API returns for the student
    {"position": 1, "subject": "Physics", "status": "SELECTABLE", "message": None, "papers": [{"paper_id": str(PA)}, {"paper_id": str(PB)}]},
    {"position": 2, "subject": "Chemistry", "status": "SELECTABLE", "message": None, "papers": [{"paper_id": str(PC1)}, {"paper_id": str(PC2)}]},
    {"position": 3, "subject": "Biology", "status": "PAPER", "message": None, "papers": [{"paper_id": str(PD)}]},
]
QUESTIONS = [("Physics", PA), ("Physics", PA), ("Physics", PB), ("Chemistry", PC1), ("Chemistry", PC2), ("Biology", PD)]   # orders 1..6


@pytest.fixture()
def env(monkeypatch):
    from app import auth
    from app.database import get_db
    from app.main import app
    from app.services import expiry, paper_selection

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
    real_fetch = paper_selection.fetch_eligibility
    monkeypatch.setattr(paper_selection, "fetch_eligibility", lambda test_id, student_id: SUBJECTS)
    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(auth, "redis_client", fake)
    run = uuid.uuid4().hex[:8]
    sid, tid, p, b = f"PS{run[:6].upper()}", uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    fake.set(f"student_session:sess-{run}", json.dumps({"student_id": sid, "expires_at": "2999-01-01T00:00:00+00:00"}))
    sections = [{"position": s["position"], "subject": s["subject"], "papers": [{"paper_id": x["paper_id"], "label": "L", "question_count": 1,
                                                                              "total_marks": 4} for x in s["papers"]]} for s in SUBJECTS]
    qids = {}
    with engine.begin() as c:
        c.execute(text("INSERT INTO students (student_id, first_name, last_name, email, status, created_at, updated_at) "
                       "VALUES (:s, 'Pa', 'Per', :e, 'ACTIVE', now(), now())"), {"s": sid, "e": f"{run}@example.com"})
        c.execute(text("INSERT INTO providers (provider_id, name, email, status, created_at, updated_at) VALUES (:p, 'P', :e, 'ACTIVE', now(), now())"),
                  {"p": p, "e": f"p-{run}@example.com"})
        c.execute(text("INSERT INTO branches (branch_id, provider_id, name, status, created_at, updated_at) VALUES (:b, :p, 'B', 'ACTIVE', now(), now())"),
                  {"b": b, "p": p})
        c.execute(text("""INSERT INTO tests (test_id, provider_id, branch_id, name, duration_minutes, total_questions, total_marks, start_at, end_at,
                          status, global_occurrence_id, sections, created_at, updated_at) VALUES (:t, :p, :b, 'Global', 30, 6, 24,
                          now() - interval '1 hour', now() + interval '2 hours', 'ACTIVE', :o, CAST(:s AS jsonb), now(), now())"""),
                  {"t": tid, "p": p, "b": b, "o": uuid.uuid4(), "s": json.dumps(sections)})
        for order, (subject, paper) in enumerate(QUESTIONS, start=1):
            qids[order] = uuid.uuid4()
            c.execute(text("""INSERT INTO questions (question_id, test_id, question_order, subject, paper_id, question_text, question_type, option_a,
                              option_b, option_c, option_d, correct_answer, marks, negative_marks, created_at, updated_at)
                              VALUES (:q, :t, :o, :s, :pp, :x, 'MCQ', 'a', 'b', 'c', 'd', 'A', 4, 1, now(), now())"""),
                      {"q": qids[order], "t": tid, "o": order, "s": subject, "pp": paper, "x": f"Q{order}"})
        c.execute(text("INSERT INTO test_audiences (test_audience_id, test_id, audience_type, student_id, created_at) VALUES (:a, :t, 'STUDENT', :s, now())"),
                  {"a": uuid.uuid4(), "t": tid, "s": sid})
    client = TestClient(app, cookies={"session_id": f"sess-{run}"})
    client.__enter__()
    yield {"c": client, "engine": engine, "Session": Session, "sid": sid, "tid": tid, "qids": qids, "real_fetch": real_fetch}
    client.__exit__(None, None, None)
    app.dependency_overrides.clear()


def choose(e, subject, paper):
    return e["c"].put(f"/attempts/tests/{e['tid']}/selection", json={"subject": subject, "paper_id": str(paper)})


def attempts(e):
    with e["engine"].connect() as c:
        return c.execute(text("SELECT attempt_id FROM test_attempts WHERE test_id = :t"), {"t": e["tid"]}).scalars().all()


def snapshot(e):
    with e["engine"].connect() as c:
        return {s: p for s, p in c.execute(text("""SELECT sp.subject, sp.paper_id FROM attempt_selected_papers sp JOIN test_attempts a
                                                   ON a.attempt_id = sp.attempt_id WHERE a.test_id = :t"""), {"t": e["tid"]})}


def start_in_thread(e, out: dict):
    from app.services.attempt_service import start_attempt

    def run():
        db = e["Session"]()
        try:
            out["attempt"] = start_attempt(db=db, student_id=e["sid"], test_id=e["tid"], duration_minutes=30).attempt_id
        except Exception as exc:  # noqa: BLE001
            out["error"] = exc
        finally:
            db.close()
    t = threading.Thread(target=run)
    t.start()
    return t


def test_choices_are_validated_persisted_and_changeable_before_start(env):
    e = env
    v = e["c"].get(f"/attempts/tests/{e['tid']}/papers").json()
    assert v["complete"] is False and v["locked"] is False
    assert [x["selected_paper_id"] for x in v["subjects"]] == [None, None, str(PD)]        # one eligible paper: included without a choice
    assert choose(e, "Physics", PC1).json()["detail"]["code"] == "PAPER_NOT_ELIGIBLE"
    assert choose(e, "Biology", PD).json()["detail"]["code"] == "SELECTION_NOT_REQUIRED"
    assert choose(e, "Physics", PA).status_code == 200
    v = choose(e, "Physics", PB).json()                                                      # changed before Start
    assert v["subjects"][0]["selected_paper_id"] == str(PB) and v["complete"] is False
    assert e["c"].get(f"/attempts/tests/{e['tid']}/papers").json()["subjects"][0]["selected_paper_id"] == str(PB)   # persisted


def test_start_with_a_missing_required_choice_is_refused_and_writes_nothing(env):
    e = env
    choose(e, "Physics", PA)
    r = e["c"].post("/attempts/start", json={"test_id": str(e["tid"])})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "SELECTION_REQUIRED" and r.json()["detail"]["subjects"] == ["Chemistry"]
    assert attempts(e) == [] and snapshot(e) == {}


def test_start_snapshots_the_choice_and_the_attempt_sees_only_its_papers(env):
    e = env
    choose(e, "Physics", PB)
    choose(e, "Chemistry", PC1)
    a = e["c"].post("/attempts/start", json={"test_id": str(e["tid"])}).json()
    assert snapshot(e) == {"Physics": PB, "Chemistry": PC1, "Biology": PD}
    ws = e["c"].get(f"/attempts/{a['attempt_id']}/workspace").json()["questions"]
    assert sorted(q["question_order"] for q in ws) == [3, 4, 6]
    assert e["c"].get(f"/attempts/{a['attempt_id']}/summary").json()["total_questions"] == 3
    other = e["c"].post(f"/attempts/{a['attempt_id']}/answers", json={"question_id": str(e["qids"][1]), "selected_answer": "A"})
    assert other.status_code == 400                                                          # paper A's question is not in this attempt
    mine = e["c"].post(f"/attempts/{a['attempt_id']}/answers", json={"question_id": str(e["qids"][3]), "selected_answer": "A"})
    assert mine.status_code == 200
    r = choose(e, "Physics", PA)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "SELECTION_LOCKED"
    v = e["c"].get(f"/attempts/tests/{e['tid']}/papers").json()
    assert v["locked"] is True and [x["selected_paper_id"] for x in v["subjects"]] == [str(PB), str(PC1), str(PD)]


def test_a_start_waiting_on_a_save_gets_the_saved_value(env):
    e = env
    choose(e, "Physics", PA)
    choose(e, "Chemistry", PC1)
    with e["engine"].connect() as c:                      # a save in flight: holds the lock, changes Physics to B, not committed
        tx = c.begin()
        c.execute(text("SELECT 1 FROM paper_selections WHERE test_id = :t AND student_id = :s FOR UPDATE"), {"t": e["tid"], "s": e["sid"]})
        c.execute(text("UPDATE paper_selections SET paper_id = :p WHERE test_id = :t AND student_id = :s AND subject = 'Physics'"),
                  {"p": PB, "t": e["tid"], "s": e["sid"]})
        out = {}
        t = start_in_thread(e, out)
        time.sleep(0.5)
        assert t.is_alive()                               # the Start waits for the save
        tx.commit()
    t.join(10)
    assert "error" not in out and snapshot(e)["Physics"] == PB   # never the stale A


def test_a_save_waiting_on_a_start_is_refused(env):
    from app.services import paper_selection
    e = env
    choose(e, "Physics", PA)
    choose(e, "Chemistry", PC1)
    with e["engine"].connect() as c:                      # a Start in flight: holds the lock and has inserted its attempt, not committed
        tx = c.begin()
        c.execute(text("SELECT 1 FROM paper_selections WHERE test_id = :t AND student_id = :s FOR UPDATE"), {"t": e["tid"], "s": e["sid"]})
        c.execute(text("""INSERT INTO test_attempts (attempt_id, test_id, student_id, started_at, expires_at, status)
                          VALUES (:a, :t, :s, now(), now() + interval '30 minutes', 'IN_PROGRESS')"""), {"a": uuid.uuid4(), "t": e["tid"], "s": e["sid"]})
        out = {}

        def save():
            db = e["Session"]()
            try:
                paper_selection.save(db, e["tid"], e["sid"], "Physics", PB, SUBJECTS)
                out["saved"] = True
            except Exception as exc:  # noqa: BLE001
                out["error"] = exc
            finally:
                db.close()
        t = threading.Thread(target=save)
        t.start()
        time.sleep(0.5)
        assert t.is_alive()
        tx.commit()
    t.join(10)
    assert getattr(out.get("error"), "status_code", None) == 409
    with e["engine"].connect() as c:
        assert c.execute(text("SELECT paper_id FROM paper_selections WHERE test_id = :t AND subject = 'Physics'"), {"t": e["tid"]}).scalar() == PA


def test_a_choice_inserted_during_a_start_is_not_half_taken(env):
    e = env
    choose(e, "Physics", PA)                              # Chemistry has no row yet
    with e["engine"].connect() as c:                      # a save in flight inserts the first Chemistry choice
        tx = c.begin()
        c.execute(text("SELECT 1 FROM paper_selections WHERE test_id = :t AND student_id = :s FOR UPDATE"), {"t": e["tid"], "s": e["sid"]})
        c.execute(text("INSERT INTO paper_selections (test_id, student_id, subject, paper_id) VALUES (:t, :s, 'Chemistry', :p)"),
                  {"t": e["tid"], "s": e["sid"], "p": PC2})
        out = {}
        t = start_in_thread(e, out)
        time.sleep(0.5)
        assert t.is_alive()
        tx.commit()
    t.join(10)
    assert getattr(out.get("error"), "status_code", None) == 409 and out["error"].detail["code"] == "SELECTION_REQUIRED"
    assert attempts(e) == [] and snapshot(e) == {}        # refused, never an incomplete snapshot
    r = e["c"].post("/attempts/start", json={"test_id": str(e["tid"])})        # pressing Start again sees the committed choice
    assert r.status_code == 200 and snapshot(e) == {"Physics": PA, "Chemistry": PC2, "Biology": PD}


def test_concurrent_starts_create_one_attempt_and_both_get_it(env):
    e = env
    choose(e, "Physics", PA)
    choose(e, "Chemistry", PC1)
    outs = [{}, {}, {}]
    threads = [start_in_thread(e, o) for o in outs]
    for t in threads:
        t.join(10)
    assert all("error" not in o for o in outs), outs
    assert len({o["attempt"] for o in outs}) == 1 and len(attempts(e)) == 1
    assert snapshot(e) == {"Physics": PA, "Chemistry": PC1, "Biology": PD}


def test_an_unreachable_test_service_refuses_start_and_writes_nothing(env, monkeypatch):
    from app.config import settings
    from app.services import paper_selection
    e = env
    choose(e, "Physics", PA)
    choose(e, "Chemistry", PC1)
    monkeypatch.setattr(paper_selection, "fetch_eligibility", e["real_fetch"])           # the real client ...
    monkeypatch.setattr(settings, "student_test_service_url", "http://127.0.0.1:9")    # ... towards nothing
    monkeypatch.setattr(settings, "student_test_service_api_key", "k")
    r = e["c"].post("/attempts/start", json={"test_id": str(e["tid"])})
    assert r.status_code == 503
    assert attempts(e) == [] and snapshot(e) == {}


def test_a_reload_resumes_the_same_attempt_with_the_same_papers(env):
    e = env
    choose(e, "Physics", PA)
    choose(e, "Chemistry", PC2)
    first = e["c"].post("/attempts/start", json={"test_id": str(e["tid"])}).json()
    again = e["c"].post("/attempts/start", json={"test_id": str(e["tid"])})              # the page reloaded: Start is sent again
    assert again.status_code == 200 and again.json()["attempt_id"] == first["attempt_id"] and len(attempts(e)) == 1
    assert snapshot(e) == {"Physics": PA, "Chemistry": PC2, "Biology": PD}


def test_a_submitted_attempt_never_starts_again(env):
    e = env
    choose(e, "Physics", PA)
    choose(e, "Chemistry", PC1)
    a = e["c"].post("/attempts/start", json={"test_id": str(e["tid"])}).json()
    assert e["c"].post(f"/attempts/{a['attempt_id']}/submit").status_code == 200
    r = e["c"].post("/attempts/start", json={"test_id": str(e["tid"])})
    assert r.status_code == 400 and attempts(e) == [UUID(a["attempt_id"])]
    assert snapshot(e) == {"Physics": PA, "Chemistry": PC1, "Biology": PD}


def test_no_eligible_paper_refuses_start_and_writes_nothing(env, monkeypatch):
    from app.services import paper_selection
    e = env
    none = [{**s, "status": "NOT_ENROLLED", "message": "Not enrolled", "papers": []} for s in SUBJECTS]
    monkeypatch.setattr(paper_selection, "fetch_eligibility", lambda test_id, student_id: none)
    r = e["c"].post("/attempts/start", json={"test_id": str(e["tid"])})
    assert r.status_code == 400 and attempts(e) == [] and snapshot(e) == {}


def test_an_ordinary_test_starts_without_the_test_service(env, monkeypatch):
    from app.services import paper_selection
    e = env

    def unreachable(test_id, student_id):
        raise AssertionError("an ordinary test never asks for paper eligibility")
    monkeypatch.setattr(paper_selection, "fetch_eligibility", unreachable)
    ordinary = uuid.uuid4()
    with e["engine"].begin() as c:
        c.execute(text("""INSERT INTO tests (test_id, provider_id, branch_id, name, duration_minutes, total_questions, total_marks, start_at, end_at,
                          status, created_at, updated_at) SELECT :o, provider_id, branch_id, 'Weekly', 30, 1, 4, start_at, end_at, 'ACTIVE', now(), now()
                          FROM tests WHERE test_id = :t"""), {"o": ordinary, "t": e["tid"]})
        c.execute(text("INSERT INTO test_audiences (test_audience_id, test_id, audience_type, student_id, created_at) VALUES (:a, :t, 'STUDENT', :s, now())"),
                  {"a": uuid.uuid4(), "t": ordinary, "s": e["sid"]})
    r = e["c"].post("/attempts/start", json={"test_id": str(ordinary)})
    assert r.status_code == 200
    with e["engine"].connect() as c:
        assert c.execute(text("SELECT count(*) FROM attempt_selected_papers WHERE attempt_id = :a"), {"a": r.json()["attempt_id"]}).scalar() == 0


def test_an_authorized_retake_sits_exactly_the_original_papers(env):
    e = env
    choose(e, "Physics", PB)
    choose(e, "Chemistry", PC2)
    a = e["c"].post("/attempts/start", json={"test_id": str(e["tid"])}).json()
    with e["engine"].begin() as c:
        c.execute(text("UPDATE test_attempts SET status = 'AUTO_SUBMITTED', submitted_at = now(), submit_reason = 'WARNING_THRESHOLD', "
                       "review_status = 'RETAKE_AUTHORIZED' WHERE attempt_id = :a"), {"a": a["attempt_id"]})
        c.execute(text("""INSERT INTO attempt_reviews (review_id, attempt_id, student_id, status, student_message, requested_at, decided_at)
                          VALUES (:r, :a, :s, 'RETAKE_AUTHORIZED', 'please', now(), now())"""), {"r": uuid.uuid4(), "a": a["attempt_id"], "s": e["sid"]})
    r = e["c"].post("/attempts/start", json={"test_id": str(e["tid"])}).json()
    assert r["attempt_id"] != a["attempt_id"]
    with e["engine"].connect() as c:
        rows = c.execute(text("SELECT attempt_id, subject, paper_id FROM attempt_selected_papers WHERE attempt_id IN (:a, :b)"),
                         {"a": a["attempt_id"], "b": r["attempt_id"]}).all()
    by = {}
    for att, subject, paper in rows:
        by.setdefault(str(att), {})[subject] = paper
    assert by[a["attempt_id"]] == by[r["attempt_id"]] == {"Physics": PB, "Chemistry": PC2, "Biology": PD}
