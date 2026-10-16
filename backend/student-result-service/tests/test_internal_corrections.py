"""Run with: DATABASE_URL=sqlite:// pytest tests/test_internal_corrections.py (in-memory, never the real DB).
The PostgreSQL-only evaluation SQL is replaced by a fake here; tests/test_corrections_pg.py covers the real SQL."""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models.result_history import ResultHistory
from app.models.test_result import TestResult as ResultRow
from app.services import reevaluation

H = {"X-Service-Key": "k-corr"}
TEST = uuid.uuid4()


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
    monkeypatch.setattr(settings, "institute_service_api_keys", "k-corr")
    ids = {}
    with Session() as db:
        for sid, obtained, correct, incorrect, attempted in (("STU000001", 7, 2, 1, 3), ("STU000002", 4, 1, 0, 1), ("STU000003", 0, 0, 0, 0)):
            r = ResultRow(attempt_id=uuid.uuid4(), test_id=TEST, student_id=sid, total_questions=3, attempted_questions=attempted, correct_answers=correct,
                          incorrect_answers=incorrect, unanswered_questions=3 - attempted, total_marks=12, obtained_marks=obtained, accuracy=round(correct / attempted * 100, 2) if attempted else 0.0)
            db.add(r)
            db.flush()
            ids[sid] = r.result_id
        db.commit()
    with TestClient(app) as c:
        yield c, Session, ids
    app.dependency_overrides.clear()


def adjust_body(**over):
    b = {"test_id": str(TEST), "student_id": "STU000001", "obtained_marks": 9, "reason": "Re-checked answer sheet manually", "actor": "STF000001", "correction_ref": "corr-adjust-0001"}
    b.update(over)
    return b


def reeval_body(**over):
    b = {"reason": "Answer key corrected for Q2", "actor": "STF000001", "correction_ref": "corr-reeval-0001"}
    b.update(over)
    return b


def history(Session):
    with Session() as db:
        return db.scalars(select(ResultHistory).order_by(ResultHistory.created_at)).all()


def marks(Session, sid):
    with Session() as db:
        return db.scalar(select(ResultRow.obtained_marks).where(ResultRow.student_id == sid))


def test_key_required_and_disabled_by_default(env, monkeypatch):
    c, _, _ = env
    assert c.post("/internal/v1/results/adjust", json=adjust_body()).status_code == 401
    assert c.post(f"/internal/v1/tests/{TEST}/reevaluate", json=reeval_body(), headers={"X-Service-Key": "bad"}).status_code == 401
    c.cookies.set("session_id", "student")
    assert c.post("/internal/v1/results/adjust", json=adjust_body()).status_code == 401
    monkeypatch.setattr(settings, "institute_service_api_keys", "")
    assert c.post("/internal/v1/results/adjust", json=adjust_body(), headers=H).status_code == 404


def test_adjustment_updates_the_result_and_preserves_the_original_in_history(env):
    c, Session, ids = env
    r = c.post("/internal/v1/results/adjust", json=adjust_body(), headers=H)
    assert r.status_code == 200 and r.json()["previous"]["obtained_marks"] == 7 and r.json()["new"]["obtained_marks"] == 9
    assert marks(Session, "STU000001") == 9
    h = history(Session)
    assert len(h) == 1 and h[0].kind == "MARKS_ADJUSTMENT" and h[0].previous["obtained_marks"] == 7 and h[0].new["obtained_marks"] == 9
    assert h[0].reason == "Re-checked answer sheet manually" and h[0].actor == "STF000001" and h[0].result_id == ids["STU000001"]


def test_history_chain_keeps_every_earlier_value(env):
    c, Session, _ = env
    c.post("/internal/v1/results/adjust", json=adjust_body(obtained_marks=9, correction_ref="corr-adjust-0001"), headers=H)
    c.post("/internal/v1/results/adjust", json=adjust_body(obtained_marks=5, correction_ref="corr-adjust-0002"), headers=H)
    assert [(h.previous["obtained_marks"], h.new["obtained_marks"]) for h in history(Session)] == [(7, 9), (9, 5)]
    assert marks(Session, "STU000001") == 5


def test_adjustment_replay_is_idempotent(env):
    c, Session, _ = env
    first = c.post("/internal/v1/results/adjust", json=adjust_body(), headers=H).json()
    again = c.post("/internal/v1/results/adjust", json=adjust_body(), headers=H)
    assert again.status_code == 200 and again.json()["new"] == first["new"] and again.json()["replayed"] is True
    assert len(history(Session)) == 1 and marks(Session, "STU000001") == 9


@pytest.mark.parametrize("mutate,status", [
    (lambda b: b.update(obtained_marks=13), 422),             # above total
    (lambda b: b.update(obtained_marks=-13), 422),
    (lambda b: b.update(obtained_marks=7), 409),              # no change
    (lambda b: b.update(reason="no"), 422),
    (lambda b: b.update(correction_ref="short"), 422),
    (lambda b: b.update(student_id="STU999999"), 404),
    (lambda b: b.update(test_id=str(uuid.uuid4())), 404),
])
def test_invalid_adjustments_write_nothing(env, mutate, status):
    c, Session, _ = env
    b = adjust_body()
    mutate(b)
    assert c.post("/internal/v1/results/adjust", json=b, headers=H).status_code == status
    assert history(Session) == [] and marks(Session, "STU000001") == 7


def fake_questions(monkeypatch, per_attempt):
    """per_attempt: {student_id: [(selected, correct, marks, negative), ...]}; keyed through the attempt id."""
    def fake(db, attempt_id):
        sid = db.scalar(select(ResultRow.student_id).where(ResultRow.attempt_id == attempt_id))
        return [{"selected_answer": s, "correct_answer": ca, "marks": m, "negative_marks": n} for s, ca, m, n in per_attempt[sid]]
    monkeypatch.setattr(reevaluation, "get_questions_with_answers", fake)


def test_reevaluation_recomputes_changes_only_what_differs_and_records_history(env, monkeypatch):
    c, Session, _ = env
    # After the key fix, STU000001's third answer becomes correct (+4 instead of -1); STU000002 unaffected; STU000003 unanswered.
    fake_questions(monkeypatch, {
        "STU000001": [("A", "A", 4, 1), ("B", "B", 4, 1), ("C", "C", 4, 1)],
        "STU000002": [("A", "A", 4, 1), (None, "B", 4, 1), (None, "C", 4, 1)],
        "STU000003": [(None, "A", 4, 1), (None, "B", 4, 1), (None, "C", 4, 1)]})
    r = c.post(f"/internal/v1/tests/{TEST}/reevaluate", json=reeval_body(), headers=H)
    assert r.status_code == 200
    out = r.json()
    assert out["changed"] == ["STU000001"] and out["unchanged"] == 2 and out["skipped_manual"] == [] and out["already_applied"] == 0
    assert marks(Session, "STU000001") == 12 and marks(Session, "STU000002") == 4
    h = history(Session)
    assert len(h) == 1 and h[0].kind == "REEVALUATION" and h[0].previous["obtained_marks"] == 7 and h[0].new["obtained_marks"] == 12
    assert h[0].new["correct_answers"] == 3 and h[0].new["incorrect_answers"] == 0 and h[0].new["accuracy"] == 100.0


def test_reevaluation_is_idempotent_and_never_overrides_a_manual_adjustment(env, monkeypatch):
    c, Session, _ = env
    c.post("/internal/v1/results/adjust", json=adjust_body(student_id="STU000002", obtained_marks=3, correction_ref="corr-adjust-0009"), headers=H)
    fake_questions(monkeypatch, {
        "STU000001": [("A", "A", 4, 1), ("B", "B", 4, 1), ("C", "C", 4, 1)],
        "STU000002": [("A", "A", 4, 1), (None, "B", 4, 1), (None, "C", 4, 1)],
        "STU000003": [(None, "A", 4, 1), (None, "B", 4, 1), (None, "C", 4, 1)]})
    first = c.post(f"/internal/v1/tests/{TEST}/reevaluate", json=reeval_body(), headers=H).json()
    assert first["skipped_manual"] == ["STU000002"] and marks(Session, "STU000002") == 3
    replay = c.post(f"/internal/v1/tests/{TEST}/reevaluate", json=reeval_body(), headers=H).json()
    assert replay["changed"] == [] and replay["already_applied"] == 1
    assert len([h for h in history(Session) if h.kind == "REEVALUATION"]) == 1


def test_reevaluation_of_an_unknown_test_changes_nothing(env):
    c, Session, _ = env
    out = c.post(f"/internal/v1/tests/{uuid.uuid4()}/reevaluate", json=reeval_body(), headers=H).json()
    assert out == {"changed": [], "unchanged": 0, "skipped_manual": [], "already_applied": 0, "no_questions": 0}
    assert history(Session) == []


def test_scoring_matches_the_existing_evaluator_rules():
    """Same arithmetic as result_service.evaluate_attempt: +marks right, -negative wrong, unanswered 0; case/space-insensitive."""
    rows = [{"selected_answer": " a ", "correct_answer": "A", "marks": 4, "negative_marks": 1},
            {"selected_answer": "B", "correct_answer": "C", "marks": 4, "negative_marks": 1},
            {"selected_answer": None, "correct_answer": "D", "marks": 4, "negative_marks": 1},
            {"selected_answer": "", "correct_answer": "D", "marks": 4, "negative_marks": 1}]
    s = reevaluation.score(rows)
    assert s == {"total_questions": 4, "attempted_questions": 2, "correct_answers": 1, "incorrect_answers": 1, "unanswered_questions": 2,
                 "total_marks": 16, "obtained_marks": 3, "accuracy": 50.0}
