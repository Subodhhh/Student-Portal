"""MSQ and NAT through the Institute side (QUESTION_TYPES_ARCHITECTURE.md, T3): manual creation and versions checked by the shared
rulebook, the paper builder's duplicate review with the key (D11), corrections of typed questions only in the Question Bank, the
answer-key PDF, the generation guard (MCQ only until enabled), delivery payload v2 with MSQ never negative (R5), typed answer-key
corrections, copy to the platform, competition ranking (D10) and the question-level analysis."""
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from institute_common.config import settings
from inst_exam.services import outbox
from inst_paper.services import paper_pdf
from tests.test_exams import create_plan, ctx, pa  # noqa: F401  (fixtures)
from tests.test_result_corrections import fakes, key_fix, super_admin  # noqa: F401  (fixtures)
from tests.test_student_integration import live_exam  # noqa: F401  (fixture)
from tests.test_test_creation import body, generate, tc  # noqa: F401  (fixtures)

API = "/api/v1"
MSQ_CONTENT = {"options": [{"id": "A", "text": "Force"}, {"id": "B", "text": "Mass"}, {"id": "C", "text": "Velocity"},
                           {"id": "D", "text": "Time"}]}
NAT_CONTENT = {"format": "DECIMAL", "decimals": 2, "unit": "m/s²"}
NAT_KEY = {"value": "9.8", "tolerance": {"kind": "ABS", "amount": "0.05"}}


def question(tc, world, qtype="MCQ_SINGLE", **over) -> dict:
    q = {"branch_id": str(world["p1b1"]), "subject_id": str(tc["phy"]), "stem": "Which of these are vector quantities?"}
    if qtype == "MCQ_SINGLE":
        q.update(option_a="Newton", option_b="Joule", option_c="Watt", option_d="Pascal", correct_option="A")
    elif qtype == "MCQ_MULTI":
        q.update(question_type=qtype, content=MSQ_CONTENT, answer_key={"correct": ["A", "C"]})
    else:
        q.update(question_type=qtype, stem="What is g at the Earth's surface?", content=NAT_CONTENT, answer_key=NAT_KEY)
    q.update(over)
    return q


def create(client, h, q) -> dict:
    r = client.post(f"{API}/questions", json=q, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def build(client, world, tc, h, ids, title="Typed paper"):
    return client.post(f"{API}/papers", json={"scope": "PROVIDER", "branch_id": str(world["p1b1"]), "subject_id": str(tc["phy"]),
                                              "title": title, "question_ids": ids}, headers=h)


def test_typed_questions_are_created_and_versioned_through_the_rulebook(client, world, tc, login):
    h = login(tc["pa"])
    msq = create(client, h, question(tc, world, "MCQ_MULTI", marks_positive=4, marks_negative=0))
    assert msq["question_type"] == "MCQ_MULTI" and msq["answer_key"] == {"correct": ["A", "C"]} and msq["option_a"] == ""
    nat = create(client, h, question(tc, world, "NUMERICAL", answer_key={"value": "9.80", "tolerance": {"kind": "EXACT"}}))
    assert nat["answer_key"] == {"value": "9.8", "tolerance": {"kind": "EXACT"}}                  # canonical number
    for bad in (question(tc, world, "MCQ_MULTI", marks_positive=4, marks_negative=1),             # MSQ never negative (R5)
                question(tc, world, "MCQ_MULTI", answer_key={"correct": ["E"]}),
                question(tc, world, "MCQ_MULTI", content={"options": [{"id": "A", "text": "only one"}]}),
                question(tc, world, "NUMERICAL", answer_key={"value": "1e3"}),
                question(tc, world, "NUMERICAL", answer_key={"value": "1,000"}),
                question(tc, world, "NUMERICAL", option_a="x"),                                       # typed: no MCQ columns
                question(tc, world, "MCQ_SINGLE", content=MSQ_CONTENT)):                              # MCQ: no typed content
        assert client.post(f"{API}/questions", json=bad, headers=h).status_code == 422, bad
    pictured = question(tc, world, "MCQ_MULTI", content={"options": [{"id": "A", "image": str(uuid4())}, {"id": "B", "text": "b"}]}, answer_key={"correct": ["A"]})
    r = client.post(f"{API}/questions", json=pictured, headers=h)
    assert r.status_code == 422 and "PICTURE_NOT_ALLOWED" in r.text, r.text

    v2 = client.post(f"{API}/questions/{msq['question_id']}/versions", headers=h,
                     json={"question_type": "MCQ_MULTI", "stem": msq["stem"], "content": MSQ_CONTENT, "answer_key": {"correct": ["A"]}})
    assert v2.status_code in (200, 201), v2.text
    assert v2.json()["version_no"] == 2 and v2.json()["answer_key"] == {"correct": ["A"]}
    locked = client.post(f"{API}/questions/{msq['question_id']}/versions", headers=h,
                         json={"question_type": "NUMERICAL", "stem": "x?", "content": NAT_CONTENT, "answer_key": NAT_KEY})
    assert locked.status_code == 409 and locked.json()["detail"]["code"] == "TYPE_LOCKED"
    create(client, h, question(tc, world))                                                          # MCQ bodies work as before


def test_the_paper_builder_blocks_the_same_question_and_flags_a_different_key(client, world, tc, login):
    h = login(tc["pa"])
    a = create(client, h, question(tc, world, "MCQ_MULTI"))
    same = create(client, h, question(tc, world, "MCQ_MULTI"))
    other_key = create(client, h, question(tc, world, "MCQ_MULTI", answer_key={"correct": ["A"]}))
    nat = create(client, h, question(tc, world, "NUMERICAL"))
    mcq_same_text = create(client, h, question(tc, world))                       # same stem, another type: not the same question
    r = build(client, world, tc, h, [a["question_id"], same["question_id"]])
    assert r.status_code == 422 and "Same question as position 1" in r.text
    r = build(client, world, tc, h, [a["question_id"], other_key["question_id"], nat["question_id"], mcq_same_text["question_id"]])
    assert r.status_code == 201, r.text
    reasons = [x["reason"] for x in r.json().get("review", [])]
    assert "Same question with a different answer key." in reasons
    built = r.json()

    # MSQ / NAT positions are corrected in the Question Bank, never in the paper
    pv = built["paper_version_id"]
    r = client.post(f"{API}/paper-versions/{pv}/revisions", headers=h,
                    json={"reason": "Fix the key", "edits": [{"position": 1, "stem": "Changed?"}]})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "TYPED_QUESTION_EDIT", r.text


def test_a_published_typed_paper_downloads_its_question_paper_and_answer_key(client, world, tc, login):
    h = login(tc["pa"])
    ids = [create(client, h, question(tc, world, t))["question_id"] for t in ("MCQ_SINGLE", "MCQ_MULTI", "NUMERICAL")]
    built = build(client, world, tc, h, ids, title="Typed download")
    assert built.status_code == 201, built.text
    pv = built.json()["paper_version_id"]
    for step in ("approve", "publish"):
        assert client.post(f"{API}/paper-versions/{pv}/{step}", json={"note": None}, headers=h).status_code == 200
    for kind in ("pdf", "answer-key"):
        r = client.get(f"{API}/paper-versions/{pv}/{kind}", headers=h)
        assert r.status_code == 200 and r.content.startswith(b"%PDF"), (kind, r.status_code, r.text[:200])


def test_the_answer_key_pdf_shows_typed_keys():
    paper = {"title": "Typed", "version_no": 1, "subject": "Physics", "provider": "P", "branch": "B", "global_tests": [],
             "exam_type": None, "target_class": None, "total_questions": 2, "total_marks": None, "marking": [], "marking_notes": [],
             "questions": [{"type": "MCQ_MULTI", "stem": "Vectors?", "options": ["Force", "Mass", "Velocity"], "letters": "ABC",
                            "marks_positive": None, "marks_negative": None, "answer": "A, C", "correct": {"A", "C"}, "explanation": None},
                           {"type": "NUMERICAL", "stem": "g?", "options": [], "letters": "", "unit": "m/s²", "marks_positive": None,
                            "marks_negative": None, "answer": "9.8 ± 0.05", "correct": set(), "explanation": None}]}
    assert paper_pdf.render(paper, answer_key=True).startswith(b"%PDF")
    assert paper_pdf.render(paper, answer_key=False).startswith(b"%PDF")


def typed_paper(db_factory, world, tc) -> UUID:
    """A PUBLISHED Physics paper: one MCQ, one MSQ, one NAT (each with its own marks 4 / 1, the MSQ 4 / 0)."""
    import hashlib
    from institute_common.models.papers import Paper, PaperVersion, PaperVersionQuestion, Question, QuestionVersion
    with db_factory() as db:
        paper = Paper(provider_id=world["p1"], branch_id=world["p1b1"], subject_id=tc["phy"], title="Typed paper", created_by=uuid4())
        db.add(paper)
        db.flush()
        pv = PaperVersion(paper_id=paper.paper_id, version_no=1, status="PUBLISHED", question_count=3, total_marks=Decimal(12),
                          content_hash=hashlib.sha256(str(uuid4()).encode()).hexdigest(), created_by=uuid4())
        db.add(pv)
        db.flush()
        for pos, (qtype, fields, neg) in enumerate((
                ("MCQ_SINGLE", {"option_a": "a", "option_b": "b", "option_c": "c", "option_d": "d", "correct_option": "B"}, 1),
                ("MCQ_MULTI", {"option_a": "", "option_b": "", "option_c": "", "option_d": "", "content": MSQ_CONTENT,
                               "answer_key": {"correct": ["A", "C"]}, "schema_version": 1}, 0),
                ("NUMERICAL", {"option_a": "", "option_b": "", "option_c": "", "option_d": "", "content": NAT_CONTENT,
                               "answer_key": NAT_KEY, "schema_version": 1}, 1)), start=1):
            q = Question(provider_id=world["p1"], branch_id=world["p1b1"], subject_id=tc["phy"], owner_staff_id=uuid4(), question_type=qtype)
            db.add(q)
            db.flush()
            qv = QuestionVersion(question_id=q.question_id, version_no=1, stem=f"Typed Q{pos}", question_type=qtype, marks_positive=Decimal(4),
                                 marks_negative=Decimal(neg), created_by=uuid4(), **fields)
            db.add(qv)
            db.flush()
            db.add(PaperVersionQuestion(paper_version_id=pv.paper_version_id, position=pos, question_version_id=qv.question_version_id))
        db.commit()
        return pv.paper_version_id


@pytest.fixture()
def typed_plan(client, world, tc, login, db_factory):
    h = login(tc["pa"])
    pv = typed_paper(db_factory, world, tc)
    r = client.post(f"{API}/branches/{world['p1b1']}/exam-plans", headers=h, json=body(
        tc, slots=[{"subject_id": str(tc["phy"]), "paper_version_id": str(pv)}], marking_mode="UNIFORM", marks_correct=4, marks_wrong=1))
    assert r.status_code == 201, r.text
    return h, r.json()["plan_id"]


def test_generation_refuses_msq_and_nat_until_they_are_enabled(client, typed_plan):
    h, plan_id = typed_plan
    job = generate(client, h, plan_id)
    assert job["status"] == "FAILED" and job["exam_id"] is None
    assert "MSQ" in job["error"] and "only MCQ" in job["error"]          # the running portal never delivers them by default


def test_enabled_types_are_delivered_as_payload_v2_and_msq_never_loses_marks(client, typed_plan, db_factory, monkeypatch):
    monkeypatch.setattr(settings, "student_question_types", "MCQ_SINGLE,MCQ_MULTI,NUMERICAL")
    h, plan_id = typed_plan
    job = generate(client, h, plan_id, key="typed")
    assert job["status"] != "FAILED", job
    exam = client.get(f"{API}/exams/{job['exam_id']}", headers=h).json()
    with db_factory() as db:
        payload = outbox.build_payload(db, UUID(exam["exam_version_id"]))
    mcq, msq, nat = payload["questions"]
    assert "type" not in mcq and mcq["correct"] == "B" and set(mcq["options"]) == set("ABCD")       # MCQ entries unchanged
    assert (mcq["marks"], mcq["negative_marks"]) == (4, 1)
    assert msq["type"] == "MCQ_MULTI" and msq["scheme"] == "ALL_OR_NOTHING" and msq["key"] == {"correct": ["A", "C"]}
    assert "options" not in msq and "correct" not in msq and msq["content"]["options"][0] == {"id": "A", "text": "Force", "image": None}
    assert (msq["marks"], msq["negative_marks"]) == (4, 0)                                           # uniform wrong=1, MSQ: 0 (R5)
    assert nat["type"] == "NUMERICAL" and nat["scheme"] == "NUMERIC_TOLERANCE" and nat["key"] == NAT_KEY
    assert (nat["marks"], nat["negative_marks"]) == (4, 1)                                           # NAT: the test's marking (D6)
    # the cross-portal contract: the Student test service's tests publish exactly this file (T6); regenerate it with WRITE_CONTRACT=1
    import json
    import os
    from pathlib import Path
    contract = Path(__file__).resolve().parents[3] / "contracts" / "question-types" / "delivery_v2.json"
    if os.environ.get("WRITE_CONTRACT"):
        contract.write_text(json.dumps(payload["questions"], indent=2, ensure_ascii=False) + "\n")
    assert payload["questions"] == json.loads(contract.read_text())


def test_typed_key_corrections_pass_the_typed_key(client, live_exam, fakes, super_admin):
    results, keys = fakes
    keys.set_answer_key = lambda **kw: (keys.calls.append(kw["changes"]) or {"changes": []})
    r = key_fix(client, live_exam["exam_id"], super_admin["h"](), changes=[{"question_order": 2, "key": {"correct": ["A", "B"]}}])
    assert r.status_code == 200, r.text
    assert keys.calls[0] == [{"question_order": 2, "key": {"correct": ["A", "B"]}}]
    for bad in ([{"question_order": 2}], [{"question_order": 2, "correct_option": "A", "key": {"correct": ["A"]}}]):
        assert key_fix(client, live_exam["exam_id"], super_admin["h"](), key="key-fix-0002", changes=bad).status_code == 422


def test_results_use_competition_ranking_and_question_analysis_is_for_full_scope_readers(client, live_exam, fakes, make_staff, login, world):
    results, _keys = fakes
    results.marks = {"STU000001": 7, "STU000002": 7}
    results.exam_subject_results = lambda test_id: []
    rows = client.get(f"{API}/exams/{live_exam['exam_id']}/results", headers=live_exam["h"]).json()["results"]
    assert [r["rank"] for r in rows] == [1, 1]                                                       # 1, 1 (then 3): D10

    results.exam_question_results = lambda test_id: [{"question_order": 1, "question_type": "MCQ_MULTI", "correct": 1, "incorrect": 1,
                                                      "unanswered": 0, "average_marks": "2.00", "choices": {"A": 2, "C": 1}}]
    r = client.get(f"{API}/exams/{live_exam['exam_id']}/results/questions", headers=live_exam["h"])
    assert r.status_code == 200 and r.json()["questions"][0]["choices"] == {"A": 2, "C": 1}
    from institute_common.core.roles import Role
    teacher = make_staff([(Role.TEACHER, world["p1"], None)])[1]
    assert client.get(f"{API}/exams/{live_exam['exam_id']}/results/questions", headers=login(teacher)).status_code == 403
