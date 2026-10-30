"""Run with: DATABASE_URL=sqlite:// pytest tests/test_internal_results.py (in-memory, never the real DB)."""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models.test_result import TestResult as ResultRow

H = {"X-Service-Key": "k-res"}


@pytest.fixture()
def env(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:   # owned by the attempt service; the official-result rule reads it
        conn.exec_driver_sql("CREATE TABLE test_attempts (attempt_id CHAR(32) PRIMARY KEY, retake_of_attempt_id CHAR(32))")
        conn.exec_driver_sql("CREATE TABLE tests (test_id CHAR(32) PRIMARY KEY, sections TEXT, global_occurrence_id CHAR(32))")   # not a Global Test
    Session = sessionmaker(bind=engine, autoflush=False)

    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    monkeypatch.setattr(settings, "institute_service_api_keys", "k-res")
    with TestClient(app) as c:
        yield c, Session
    app.dependency_overrides.clear()


def seed(Session, test_id, rows):
    with Session() as db:
        for sid, obt in rows:
            db.add(ResultRow(attempt_id=uuid.uuid4(), test_id=test_id, student_id=sid, total_questions=3, attempted_questions=3,
                             correct_answers=2, incorrect_answers=1, unanswered_questions=0, total_marks=12, obtained_marks=obt, accuracy=66.7))
        db.commit()


def test_key_required_and_disabled_by_default(env, monkeypatch):
    c, _ = env
    t = uuid.uuid4()
    assert c.get(f"/internal/v1/tests/{t}/results").status_code == 401
    assert c.get(f"/internal/v1/tests/{t}/results", headers={"X-Service-Key": "x"}).status_code == 401
    c.cookies.set("session_id", "student")
    assert c.get(f"/internal/v1/tests/{t}/results").status_code == 401
    monkeypatch.setattr(settings, "institute_service_api_keys", "")
    assert c.get(f"/internal/v1/tests/{t}/results", headers=H).status_code == 404


def test_returns_only_requested_exam_ordered_by_marks(env):
    c, Session = env
    a, b = uuid.uuid4(), uuid.uuid4()
    seed(Session, a, [("STU000001", 7), ("STU000002", 10)])
    seed(Session, b, [("STU000009", 12)])
    rows = c.get(f"/internal/v1/tests/{a}/results", headers=H).json()
    assert [(r["student_id"], r["obtained_marks"]) for r in rows] == [("STU000002", 10), ("STU000001", 7)]
    assert c.get(f"/internal/v1/tests/{uuid.uuid4()}/results", headers=H).json() == []


def test_subject_results_score_each_subject_with_the_evaluation_rules(env, monkeypatch):
    """Per-subject marks for subject-scoped readers (a provider's view of a unified Global Test exam): +marks right, -negative wrong,
    0 unanswered, case/space-insensitive, exactly as evaluation; the overall result row is not changed or returned."""
    from app.routes import internal
    c, Session = env
    t = uuid.uuid4()
    seed(Session, t, [("STU000001", 7)])
    q = lambda subject, right, chosen: {"subject": subject, "marks": 4, "negative_marks": 1, "correct_answer": right, "selected_answer": chosen}
    answers = [q("Physics", "A", " a "), q("Physics", "B", "C"), q("Physics", "C", None), q("Chemistry", "D", "d"), q(None, "A", "A")]
    monkeypatch.setattr(internal, "get_questions_with_answers", lambda db, attempt_id: answers)
    [row] = c.get(f"/internal/v1/tests/{t}/results/subjects", headers=H).json()
    by = {s["subject"]: s for s in row["subjects"]}
    assert row["student_id"] == "STU000001" and "obtained_marks" not in row
    assert (by["Physics"]["obtained_marks"], by["Physics"]["total_marks"], by["Physics"]["correct_answers"], by["Physics"]["incorrect_answers"],
            by["Physics"]["unanswered_questions"]) == (3, 12, 1, 1, 1)
    assert by["Chemistry"]["obtained_marks"] == 4 and by["Other"]["obtained_marks"] == 4
    assert c.get(f"/internal/v1/tests/{t}/results/subjects").status_code == 401             # service key required


def test_result_details_list_every_configured_subject_in_order_and_reconcile(monkeypatch):
    """A unified Global Test result: subjects in the configured order, a subject without a paper shown as unavailable with no
    questions and no marks, per-subject marks (+4 right, -1 wrong, 0 unanswered) adding up to the overall result."""
    from app.services import result_service as rs
    test_id, attempt_id = uuid.uuid4(), uuid.uuid4()
    q = lambda i, subject, right, chosen: {"question_id": uuid.UUID(int=i), "question_order": i, "subject": subject, "question_text": f"Q{i}",
                                          "question_type": "MCQ", "option_a": "a", "option_b": "b", "option_c": "c", "option_d": "d",
                                          "correct_answer": right, "selected_answer": chosen, "marks": 4, "negative_marks": 1, "image_reference": None}
    questions = [q(1, "Chemistry", "A", "A"), q(2, "Chemistry", "B", "C"), q(3, "Physics", "A", "A"), q(4, "Physics", "D", None), q(5, "Physics", "C", "C")]
    sections = [{"position": 1, "subject": "Physics", "status": "PAPER", "message": None, "question_count": 3},
                {"position": 2, "subject": "Chemistry", "status": "PAPER", "message": None, "question_count": 2},
                {"position": 3, "subject": "Biology", "status": "NO_SUBMISSION", "message": "Your institute has not submitted a Biology paper.",
                 "question_count": 0}]
    monkeypatch.setattr(rs, "get_result", lambda db, attempt_id, student_id: {"test_id": test_id, "obtained_marks": 11, "total_marks": 20,
                                                                    "result_id": "r1", "subject_adjustments": None})
    monkeypatch.setattr(rs, "get_review_window", lambda db, test_id: None)
    monkeypatch.setattr(rs, "get_questions_with_answers", lambda db, attempt_id: questions)
    monkeypatch.setattr(rs, "_sections", lambda db, tid: sections)
    monkeypatch.setattr(rs.global_results, "is_global_test", lambda db, tid: True)              # a Global Test made for one combination
    monkeypatch.setattr(rs.global_results, "deltas", lambda db, result_id, adjustments: {})         # never adjusted
    out = rs.get_result_details(db=None, attempt_id=attempt_id, student_id="STU000001")
    subjects = [(s["subject"], s["status"], s["total_questions"], s["obtained_marks"], s["total_marks"]) for s in out["subjects"]]
    assert subjects == [("Physics", "PAPER", 3, 8, 12), ("Chemistry", "PAPER", 2, 3, 8), ("Biology", "NO_SUBMISSION", 0, 0, 0)]
    assert sum(s["obtained_marks"] for s in out["subjects"]) == 11 and sum(s["total_marks"] for s in out["subjects"]) == 20   # reconciles
    assert out["subjects"][2]["message"].startswith("Your institute has not submitted")
    assert [r["subject"] for r in out["questions"]] == ["Physics"] * 3 + ["Chemistry"] * 2                # review in the configured order
    again = rs.get_result_details(db=None, attempt_id=attempt_id, student_id="STU000001")
    assert [r["question_id"] for r in again["questions"]] == [r["question_id"] for r in out["questions"]]   # the same order every time
