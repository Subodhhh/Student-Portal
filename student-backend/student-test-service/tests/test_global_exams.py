"""Global Test with paper choice delivered through /exams (one test per occurrence, papers per subject, per-student eligibility). Run with dummy env and an
in-memory DB (never the configured one):
   DATABASE_URL=sqlite:// pytest tests/test_global_exams.py"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models.paper_eligibility import StudentPaperEligibility
from app.models.question import Question
from app.models.test import Test
from app.services import paper_eligibility, test_service

KEY = "k-test"
H = {"X-Service-Key": KEY}
PA, PB, PC = (str(uuid.uuid4()) for _ in range(3))   # Physics A, Physics B, Chemistry C


@pytest.fixture()
def env(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)

    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    monkeypatch.setattr(settings, "institute_service_api_keys", KEY)
    with TestClient(app) as c:
        yield c, Session
    app.dependency_overrides.clear()


def q(order, subject, paper, marks=4):
    return {"order": order, "subject": subject, "paper_id": paper, "text": f"Q{order}", "options": {"A": "a", "B": "b", "C": "c", "D": "d"},
            "correct": "A", "marks": marks, "negative_marks": 1}


def payload(**over):
    start = datetime.now(timezone.utc) + timedelta(days=1)
    body = {
        "operation": "PUBLISH", "exam_version_id": str(uuid.uuid4()), "provider_id": str(uuid.uuid4()),
        "branch_id": str(uuid.uuid4()), "name": "Global Test", "duration_minutes": 120, "global_occurrence_id": str(uuid.uuid4()),
        "start_at": start.isoformat(), "end_at": (start + timedelta(hours=3)).isoformat(),
        "sections": [
            {"position": 1, "subject": "Physics", "papers": [
                {"paper_id": PA, "label": "Physics A", "provider_name": "P1", "question_count": 2, "total_marks": 8},
                {"paper_id": PB, "label": "Physics B", "provider_name": "P2", "question_count": 1, "total_marks": 4}]},
            {"position": 2, "subject": "Chemistry", "papers": [
                {"paper_id": PC, "label": "Chemistry C", "provider_name": "P1", "question_count": 1, "total_marks": 4}]},
        ],
        "questions": [q(1, "Physics", PA), q(2, "Physics", PA), q(3, "Physics", PB), q(4, "Chemistry", PC)],
        "audiences": [
            {"audience_id": "a1", "cells": [{"subject": "Physics", "status": "SELECTABLE", "paper_ids": [PA, PB]},
                                            {"subject": "Chemistry", "status": "PAPER", "paper_ids": [PC]}]},
            {"audience_id": "a2", "cells": [{"subject": "Physics", "status": "PAPER", "paper_ids": [PB]},
                                            {"subject": "Chemistry", "status": "NOT_ENROLLED", "message": "Not enrolled for Chemistry"}]},
        ],
        "student_audiences": {"a1": ["STU1", "STU2"], "a2": ["STU3"]},
    }
    body.update(over)
    return body


def test_publish_creates_one_test_with_paper_tagged_questions_and_frozen_eligibility(env):
    c, Session = env
    body = payload()
    r = c.post("/internal/v1/exams", json=body, headers=H)
    assert r.status_code == 201, r.text
    assert r.json() == {"status": "published", "questions": 4, "students": 3}
    tid = uuid.UUID(body["exam_version_id"])
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(Test)) == 1
        t = db.get(Test, tid)
        assert t.total_questions == 4 and paper_eligibility.has_paper_choice(t.sections)
        assert sorted((x.question_order, str(x.paper_id)) for x in db.scalars(select(Question))) == [(1, PA), (2, PA), (3, PB), (4, PC)]
        rows = {(e.student_id, e.subject, e.status, str(e.paper_id) if e.paper_id else None, e.message)
                for e in db.scalars(select(StudentPaperEligibility))}
    assert rows == {("STU1", "Physics", "SELECTABLE", PA, None), ("STU1", "Physics", "SELECTABLE", PB, None), ("STU1", "Chemistry", "PAPER", PC, None),
                    ("STU2", "Physics", "SELECTABLE", PA, None), ("STU2", "Physics", "SELECTABLE", PB, None), ("STU2", "Chemistry", "PAPER", PC, None),
                    ("STU3", "Physics", "PAPER", PB, None), ("STU3", "Chemistry", "NOT_ENROLLED", None, "Not enrolled for Chemistry")}
    # replay: acknowledged, never rewritten; CANCEL as /exams
    assert c.post("/internal/v1/exams", json=body, headers=H).json() == {"status": "already_published"}
    assert c.post("/internal/v1/exams", json={**body, "operation": "CANCEL"}, headers=H).json() == {"status": "cancelled"}


def _bad(**over):
    return payload(**over)


@pytest.mark.parametrize("body", [
    _bad(student_ids=["STU1"]),                                                                     # audiences decide the students
    _bad(student_audiences=None),                                                                   # paper choice without students
    _bad(audiences=[{"audience_id": "a1", "cells": [{"subject": "Physics", "status": "SELECTABLE", "paper_ids": [PA]},
                                                    {"subject": "Chemistry", "status": "PAPER", "paper_ids": [PC]}]}],
         student_audiences={"a1": ["STU1"]}),                                                        # SELECTABLE needs 2+ papers
    _bad(audiences=[{"audience_id": "a1", "cells": [{"subject": "Physics", "status": "PAPER", "paper_ids": [PC]},
                                                    {"subject": "Chemistry", "status": "PAPER", "paper_ids": [PC]}]}],
         student_audiences={"a1": ["STU1"]}),                                                        # another subject's paper
    _bad(audiences=[{"audience_id": "a1", "cells": [{"subject": "Physics", "status": "PAPER", "paper_ids": [PA]},
                                                    {"subject": "Chemistry", "status": "NOT_COVERED"}]}],
         student_audiences={"a1": ["STU1"]}),                                                        # unavailable without a message
    _bad(audiences=[{"audience_id": "a1", "cells": [{"subject": "Physics", "status": "PAPER", "paper_ids": [PA]}]}],
         student_audiences={"a1": ["STU1"]}),                                                        # a subject without a cell
    _bad(student_audiences={"a1": ["STU1"], "a2": ["STU1"]}),                                        # a student in two audiences
    _bad(student_audiences={"zz": ["STU1"]}),                                                        # unknown audience
    _bad(questions=[q(1, "Physics", PA), q(2, "Physics", PA), q(3, "Physics", PB), q(4, "Chemistry", PC, marks=5)]),  # marks mismatch
    _bad(questions=[q(1, "Physics", PA), q(2, "Physics", PA), q(3, "Physics", PB), q(4, "Physics", PC)]),  # paper of another subject
    _bad(questions=[q(1, "Physics", PA), q(2, "Physics", PA), q(3, "Physics", PB)]),                 # a declared question missing
])
def test_invalid_exams_are_refused_and_nothing_is_stored(env, body):
    c, Session = env
    assert c.post("/internal/v1/exams", json=body, headers=H).status_code == 422
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(Test)) == 0
        assert db.scalar(select(func.count()).select_from(StudentPaperEligibility)) == 0


def test_question_limit_is_enforced_without_truncating(env, monkeypatch):
    c, Session = env
    monkeypatch.setattr(settings, "max_exam_questions", 3)
    r = c.post("/internal/v1/exams", json=payload(), headers=H)
    assert r.status_code == 422 and r.json()["detail"] == {"code": "QUESTION_LIMIT_EXCEEDED", "questions": 4, "limit": 3}
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(Question)) == 0


def test_requires_service_key(env):
    c, _ = env
    assert c.post("/internal/v1/exams", json=payload()).status_code == 401
    assert c.get(f"/internal/v1/tests/{uuid.uuid4()}/eligibility/STU1").status_code == 401


def test_eligibility_api(env):
    c, _ = env
    body = payload()
    c.post("/internal/v1/exams", json=body, headers=H)
    tid = body["exam_version_id"]
    r = c.get(f"/internal/v1/tests/{tid}/eligibility/STU1", headers=H).json()
    assert r["paper_selection"] is True
    assert [(s["subject"], s["status"], sorted(p["paper_id"] for p in s["papers"])) for s in r["subjects"]] == [
        ("Physics", "SELECTABLE", sorted([PA, PB])), ("Chemistry", "PAPER", [PC])]
    r3 = c.get(f"/internal/v1/tests/{tid}/eligibility/STU3", headers=H).json()
    assert [(s["status"], s["message"], s["papers"]) for s in r3["subjects"]][1] == ("NOT_ENROLLED", "Not enrolled for Chemistry", [])
    assert c.get(f"/internal/v1/tests/{tid}/eligibility/NOBODY", headers=H).status_code == 404
    assert c.get(f"/internal/v1/tests/{uuid.uuid4()}/eligibility/STU1", headers=H).status_code == 404


def test_eligibility_api_for_a_test_without_papers(env):
    c, _ = env
    start = datetime.now(timezone.utc) + timedelta(days=1)
    v1 = {"operation": "PUBLISH", "exam_version_id": str(uuid.uuid4()), "provider_id": str(uuid.uuid4()), "branch_id": str(uuid.uuid4()),
          "name": "Weekly", "duration_minutes": 60, "start_at": start.isoformat(), "end_at": (start + timedelta(hours=1)).isoformat(),
          "student_ids": ["STU1"], "questions": [{k: v for k, v in q(1, "Physics", PA).items() if k != "paper_id"}]}
    assert c.post("/internal/v1/exams", json=v1, headers=H).status_code == 201
    r = c.get(f"/internal/v1/tests/{v1['exam_version_id']}/eligibility/STU1", headers=H).json()
    assert r["paper_selection"] is False and r["subjects"] == []


def _row(tid, sections):
    return {"test_id": tid, "provider_id": uuid.uuid4(), "provider_name": "P", "branch_id": uuid.uuid4(), "branch_name": "B", "city": None,
            "state": None, "name": "Global Test", "description": None, "duration_minutes": 120, "total_questions": 4, "total_marks": 16,
            "start_at": None, "end_at": None, "status": "ACTIVE", "global_occurrence_id": uuid.uuid4(), "sections": sections}


def test_start_serves_only_the_attempt_papers_and_needs_an_attempt(env, monkeypatch):
    c, Session = env
    body = payload()
    c.post("/internal/v1/exams", json=body, headers=H)
    tid = uuid.UUID(body["exam_version_id"])
    with Session() as db:
        sections = db.get(Test, tid).sections
        monkeypatch.setattr(test_service, "get_test_by_id", lambda **kw: _row(tid, sections))
        monkeypatch.setattr(paper_eligibility, "snapshot", lambda *a: None)
        with pytest.raises(test_service.AttemptRequired):          # no attempt: no questions at all (no preview before choosing)
            test_service.get_test_for_start(db, tid, "STU1")

        monkeypatch.setattr(paper_eligibility, "snapshot", lambda *a: {"Physics": uuid.UUID(PB), "Chemistry": uuid.UUID(PC)})
        t = test_service.get_test_for_start(db, tid, "STU1")
        assert sorted(x["question_order"] for x in t["questions"]) == [3, 4]               # never paper A's questions
        assert [(s["subject"], s["status"], s["question_count"]) for s in t["sections"]] == [("Physics", "PAPER", 1), ("Chemistry", "PAPER", 1)]
        assert (t["total_questions"], t["total_marks"]) == (2, 8)


def test_listing_view_before_the_attempt(env, monkeypatch):
    c, Session = env
    body = payload()
    c.post("/internal/v1/exams", json=body, headers=H)
    tid = uuid.UUID(body["exam_version_id"])
    monkeypatch.setattr(paper_eligibility, "snapshot", lambda *a: None)
    with Session() as db:
        sections = db.get(Test, tid).sections
        t1 = test_service.personalize(db, test_service.format_test(_row(tid, sections), db), "STU1")
        assert [(s["subject"], s["status"], len(s["papers"])) for s in t1["sections"]] == [("Physics", "SELECTABLE", 2), ("Chemistry", "PAPER", 1)]
        assert t1["total_questions"] is None                       # unknown until the choice is fixed by the attempt
        t3 = test_service.personalize(db, test_service.format_test(_row(tid, sections), db), "STU3")
        assert (t3["total_questions"], t3["total_marks"]) == (1, 4) and t3["sections"][1]["status"] == "NOT_ENROLLED"
        assert test_service.personalize(db, test_service.format_test(_row(tid, sections), db), "NOBODY") is None
        legacy = test_service.format_test({**_row(tid, None), "sections": None}, db)
        assert test_service.personalize(db, dict(legacy), "STU1") == legacy        # other tests unchanged


def test_an_exam_without_audiences_cannot_carry_papers(env):
    c, _ = env
    body = payload()
    body.pop("audiences"), body.pop("student_audiences")
    body["student_ids"] = ["STU1"]
    assert c.post("/internal/v1/exams", json=body, headers=H).status_code == 422          # papers are never served to everyone
