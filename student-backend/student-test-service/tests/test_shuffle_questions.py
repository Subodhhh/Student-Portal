"""Shuffle Questions delivered through /exams and applied when a student loads the paper. Run with dummy env and an in-memory DB:
   DATABASE_URL=sqlite:// pytest tests/test_shuffle_questions.py"""
import uuid
from datetime import UTC, datetime, timedelta

from app.models.test import Test
from app.services import test_service
from app.services.question_order import section_order

from tests.test_global_exams import H, _row, env  # noqa: F401  (same in-memory app fixture)


def exam(**over):
    start = datetime.now(UTC) - timedelta(minutes=5)
    qs = [{"order": i, "subject": "Physics" if i <= 6 else "Chemistry", "text": f"Q{i}", "options": {"A": f"a{i}", "B": "b", "C": "c", "D": "d"},
           "correct": "B", "marks": 4, "negative_marks": 1} for i in range(1, 13)]
    return {"operation": "PUBLISH", "exam_version_id": str(uuid.uuid4()), "provider_id": str(uuid.uuid4()), "branch_id": str(uuid.uuid4()),
            "name": "Daily", "duration_minutes": 60, "start_at": start.isoformat(), "end_at": (start + timedelta(hours=2)).isoformat(),
            "questions": qs, "student_ids": ["STU1", "STU2", "STU3"], **over}


def _start(db, monkeypatch, tid, student):
    monkeypatch.setattr(test_service, "get_test_by_id", lambda **kw: {**_row(tid, None), "global_occurrence_id": None, "sections": None})
    return test_service.get_test_for_start(db, tid, student)["questions"]


def test_the_choice_is_stored_and_defaults_to_the_paper_order(env):  # noqa: F811
    c, Session = env
    on, off, absent = exam(shuffle_questions=True), exam(shuffle_questions=False), exam()
    for body in (on, off, absent):
        assert c.post("/internal/v1/exams", json=body, headers=H).status_code == 201
    with Session() as db:
        got = [db.get(Test, uuid.UUID(b["exam_version_id"])).shuffle_questions for b in (on, off, absent)]
    assert got == [True, False, False]                     # an older payload without the field keeps today's order


def test_each_student_gets_their_own_fixed_order_and_subjects_keep_their_place(env, monkeypatch):  # noqa: F811
    c, Session = env
    body = exam(shuffle_questions=True)
    c.post("/internal/v1/exams", json=body, headers=H)
    tid = uuid.UUID(body["exam_version_id"])
    with Session() as db:
        orders = {}
        for sid in ("STU1", "STU2", "STU3"):
            first = _start(db, monkeypatch, tid, sid)
            assert [q["subject"] for q in first] == ["Chemistry"] * 6 + ["Physics"] * 6          # the existing subject order
            assert sorted(q["question_order"] for q in first) == list(range(1, 13))             # the same questions, each once
            assert all(q["option_a"] == f"a{q['question_order']}" and (q["marks"], q["negative_marks"]) == (4, 1) for q in first)
            assert "correct_answer" not in first[0]
            assert [q["question_id"] for q in _start(db, monkeypatch, tid, sid)] == [q["question_id"] for q in first]   # stable
            orders[sid] = [q["question_order"] for q in first]
        assert len({tuple(o) for o in orders.values()}) == 3                                     # three students, three orders
        assert any(o != [7, 8, 9, 10, 11, 12, 1, 2, 3, 4, 5, 6] for o in orders.values())
        # the same function as the attempt and result services: the paper order is what the student sees there too
        rows = sorted(_start(db, monkeypatch, tid, "STU1"), key=lambda q: (q["subject"], q["question_order"]))
        assert [q["question_order"] for q in section_order(rows, None, "STU1", tid, subject_of=lambda q: q["subject"],
                                                           id_of=lambda q: q["question_id"], shuffle=True)] == orders["STU1"]


def test_without_shuffle_the_existing_order_is_kept(env, monkeypatch):  # noqa: F811
    c, Session = env
    body = exam(shuffle_questions=False)
    c.post("/internal/v1/exams", json=body, headers=H)
    tid = uuid.UUID(body["exam_version_id"])
    with Session() as db:
        for sid in ("STU1", "STU2"):
            assert [q["question_order"] for q in _start(db, monkeypatch, tid, sid)] == [7, 8, 9, 10, 11, 12, 1, 2, 3, 4, 5, 6]
