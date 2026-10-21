"""Test -> Create Test: roles and scope, audience (branches, batches, students; union, deduplicated, server-resolved), question paper +
answer key (numbered and question-ID keys, CSV quoting, the >5 failure threshold with all-or-nothing imports), scheduling validation,
and idempotent, all-or-nothing publication with recoverable delivery."""
import os
import threading
import uuid
from datetime import timedelta
from uuid import UUID

import pytest

from institute_common.core.roles import Role
from institute_common.database import utcnow
from inst_exam.services import exam_generation as gen
from inst_exam.services import outbox
from tests.test_academics_enrollment import FakeStudents
from tests.test_test_creation import tc  # noqa: F401  (fixture)

API = "/api/v1"
HEAD = "question_id,question,option_a,option_b,option_c,option_d,marks,negative_marks,explanation\n"


def paper(n=3, prefix="M", bad=()):
    """A CSV paper with explicit question IDs and no answers (they come from the answer key). `bad` = question numbers whose option C is empty."""
    rows = []
    for i in range(1, n + 1):
        c = "" if i in bad else f"C{i}"
        rows.append(f"{prefix}{i:03d},Question number {i} for {prefix}?,A{i},B{i},{c},D{i},4,1,")
    return HEAD + "\n".join(rows) + "\n"


def key(n=3, prefix="M", answers="ABCD"):
    lines = ["question_id,correct_option,explanation"]
    for i in range(n, 0, -1):                                   # deliberately NOT in paper order: matching is by ID
        lines.append(f'{prefix}{i:03d},{answers[(i - 1) % 4]},"Because {i}, and that is final"')
    return "\n".join(lines) + "\n"


@pytest.fixture()
def ct(client, world, tc, make_staff, login, db_factory):  # noqa: F811
    """tc + teachers (Physics in p1-b1: one only batch A, one the whole branch), a platform Admin, a Branch Admin, the Student
    service fake with names, and STU000001 also enrolled with provider p2 (one global identity, two providers)."""
    from institute_common.clients.student_client import set_student_client
    from institute_common.models.academics import TeacherSubjectAssignment
    from institute_common.models.org import Batch, Enrollment, EnrollmentBatch
    c = dict(tc)
    t1, c["t_batch"] = make_staff([(Role.TEACHER, world["p1"], None)])
    t2, c["t_branch"] = make_staff([(Role.TEACHER, world["p1"], None)])
    _, c["admin"] = make_staff([(Role.ADMIN, None, None)])
    _, c["ba"] = make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b1"])])
    with db_factory() as db:
        db.add(TeacherSubjectAssignment(staff_id=t1, provider_id=world["p1"], branch_id=world["p1b1"], subject_id=c["phy"], batch_id=c["batch_a"]))
        db.add(TeacherSubjectAssignment(staff_id=t2, provider_id=world["p1"], branch_id=world["p1b1"], subject_id=c["phy"]))
        d = db.query(Batch).filter_by(branch_id=world["p2b1"]).one()
        db.add(Enrollment(enrollment_id="e1p2", provider_id=world["p2"], branch_id=world["p2b1"], student_id="STU000001"))
        db.flush()
        db.add(EnrollmentBatch(enrollment_id="e1p2", batch_id=d.batch_id))
        c["batch_d"] = d.batch_id
        db.commit()
    fake = FakeStudents(db_factory)
    for i, (first, last) in enumerate((("Riya", "Patil"), ("Riya", "Patel"), ("Aman", "Shah"), ("Neel", "Rao"), ("Old", "Student"), ("Zoya", "Khan")), start=1):
        fake.add(f"STU{i:06d}", first, last, f"s{i}@example.com")
    set_student_client(fake)
    yield c
    set_student_client(None)


def aud(world, ct, branches=("p1b1",), whole=(), batches=(), students=(), provider="p1"):
    return {"provider_id": str(world[provider]), "branch_ids": [str(world[b]) for b in branches], "whole_branch_ids": [str(world[b]) for b in whole],
            "batch_ids": [str(ct[b]) for b in batches], "student_ids": list(students)}


def draft(world, ct, audience=None, subjects=(("phy", None),), **over):
    start = utcnow() + timedelta(hours=2)
    d = {"audience": audience or aud(world, ct, batches=("batch_a",)), "name": "  Weekly   Physics  ", "description": "Read carefully.",
         "subjects": [{"subject_id": str(ct[s]), "paper_version_id": pv} for s, pv in subjects], "duration_minutes": 30,
         "start_at": start.isoformat(), "end_at": (start + timedelta(hours=1)).isoformat(), "marking_mode": "UNIFORM", "marks_correct": 4, "marks_wrong": 1}
    d.update(over)
    return d


def upload(client, h, world, ct, text, k, subject="phy", branch="p1b1", title="Physics test paper"):
    files = {"file": ("paper.csv", text.encode(), "text/csv")}
    if k is not None:
        files["answer_key"] = ("key.csv", k.encode(), "text/csv")
    return client.post(f"{API}/branches/{world[branch]}/subjects/{ct[subject]}/papers/upload", files=files, data={"title": title}, headers=h)


def ready_paper(client, login, world, ct, who="pa", subject="phy", prefix="M", n=3):
    h = login(ct[who])
    r = upload(client, h, world, ct, paper(n, prefix), key(n, prefix), subject=subject, title=f"{prefix} paper")
    assert r.status_code == 201, r.text
    pv = r.json()["paper_version_id"]
    assert client.post(f"{API}/paper-versions/{pv}/approve", json={"note": "ok"}, headers=h).status_code == 200
    assert client.post(f"{API}/paper-versions/{pv}/publish", json={"note": None}, headers=h).status_code == 200
    return pv


def publish(client, h, body, k=None):
    return client.post(f"{API}/tests", json=body, headers={**h, "Idempotency-Key": k or f"create-test-{uuid.uuid4()}"})


def counts(db_factory, plan_id=None):
    from institute_common.models.exams import Exam, ExamOutbox, ExamPlan, StudentExamAssignment
    from institute_common.models.papers import PaperVersion, Question
    with db_factory() as db:
        return {"plans": db.query(ExamPlan).count(), "exams": db.query(Exam).count(), "assignments": db.query(StudentExamAssignment).count(),
                "outbox": db.query(ExamOutbox).count(), "questions": db.query(Question).count(), "versions": db.query(PaperVersion).count()}


# ------------------------------------------------------------------ A. roles and scope

def test_admins_hold_test_create_and_teachers_only_read_scheduled_tests(client, world, ct, login):
    for who, expected in (("sa", True), ("admin", True), ("pa", True), ("ba", True), ("t_batch", False)):
        perms = client.get(f"{API}/auth/me", headers=login(ct[who])).json()["permissions"]
        assert ("test.create" in perms) is expected, who
    h = login(ct["t_batch"])
    assert client.get(f"{API}/tests", headers=h).status_code == 200                       # exam.read: scheduled tests they teach
    for path in (f"/tests/scope?provider_id={world['p1']}", f"/tests/students?provider_id={world['p1']}&branch_ids={world['p1b1']}"):
        assert client.get(f"{API}{path}", headers=h).status_code == 403
    assert client.post(f"{API}/tests/check", json=draft(world, ct), headers=h).status_code == 403
    assert publish(client, h, draft(world, ct)).status_code == 403


def test_scope_lists_only_what_each_role_may_target(client, world, ct, login):
    full = client.get(f"{API}/tests/scope?provider_id={world['p1']}", headers=login(ct["pa"])).json()["branches"]
    assert {b["name"] for b in full} == {"p1-b1", "p1-b2"} and all(b["whole_branch_allowed"] for b in full)
    for who in ("sa", "admin"):
        assert len(client.get(f"{API}/tests/scope?provider_id={world['p1']}", headers=login(ct[who])).json()["branches"]) == 2
    [b] = client.get(f"{API}/tests/scope?provider_id={world['p1']}", headers=login(ct["ba"])).json()["branches"]     # own branch only
    assert b["name"] == "p1-b1" and [x["name"] for x in b["batches"]] == ["A", "B"] and b["whole_branch_allowed"]
    assert {s["name"] for s in b["subjects"]} == {"Physics", "Chemistry"}
    assert client.get(f"{API}/tests/scope?provider_id={world['p1']}", headers=login(ct["pa2"])).status_code == 404     # other provider


@pytest.mark.parametrize("who,audience,subjects,field", [
    ("ba", dict(branches=("p1b1", "p1b2"), batches=("batch_a",)), ("phy",), "branch_ids"),      # another branch of the provider
    ("ba", dict(branches=("p1b2",), whole=("p1b2",)), ("phy",), "branch_ids"),
    ("ba", dict(batches=("batch_c",)), ("phy",), "batch_ids"),                                   # batch of another branch
    ("ba", dict(batches=("batch_a",), students=("STU000003",)), ("phy",), "student_ids"),        # student enrolled only elsewhere
    ("pa", dict(batches=("batch_c",)), ("phy",), "batch_ids"),                       # batch of a branch that is not selected
    ("pa", dict(students=("STU000006",)), ("phy",), "student_ids"),                  # another provider's student
])
def test_unauthorized_or_invalid_targets_are_refused_by_the_api(client, world, ct, login, who, audience, subjects, field):
    body = draft(world, ct, audience=aud(world, ct, **audience), subjects=[(s, None) for s in subjects])
    r = client.post(f"{API}/tests/check", json=body, headers=login(ct[who]))
    assert r.status_code == 422 and field in {e["field"] for e in r.json()["detail"]["errors"]}, r.text


def test_other_providers_cannot_target_this_provider_at_all(client, world, ct, login):
    r = client.post(f"{API}/tests/audience", json=aud(world, ct, batches=("batch_a",)), headers=login(ct["pa2"]))
    assert r.status_code == 404


# ------------------------------------------------------------------ B. audience

def eligible(client, login, ct, who, body):
    r = client.post(f"{API}/tests/audience", json=body, headers=login(ct[who]))
    assert r.status_code == 200, r.text
    return sorted(s["student_id"] for s in r.json()["students"])


def test_audience_one_branch_one_batch(client, world, ct, login):
    assert eligible(client, login, ct, "pa", aud(world, ct, batches=("batch_a",))) == ["STU000001", "STU000004"]   # STU000005 inactive


def test_audience_multiple_branches_and_batches_assigns_a_student_once(client, world, ct, login):
    body = aud(world, ct, branches=("p1b1", "p1b2"), batches=("batch_a", "batch_c"))
    assert eligible(client, login, ct, "pa", body) == ["STU000001", "STU000003", "STU000004"]     # STU000004 in A and C: once


def test_individual_students_supplement_batches(client, world, ct, login):
    body = aud(world, ct, branches=("p1b1", "p1b2"), batches=("batch_b",), students=("STU000003",))
    assert eligible(client, login, ct, "pa", body) == ["STU000002", "STU000003"]


def test_multiple_individual_students_and_whole_branches(client, world, ct, login):
    assert eligible(client, login, ct, "pa", aud(world, ct, branches=("p1b1", "p1b2"), students=("STU000001", "STU000003"))) == ["STU000001", "STU000003"]
    assert eligible(client, login, ct, "pa", aud(world, ct, branches=("p1b2",), whole=("p1b2",))) == ["STU000003", "STU000004"]


def test_a_student_of_two_providers_is_targeted_by_each_under_one_identity(client, world, ct, login):
    assert "STU000001" in eligible(client, login, ct, "pa", aud(world, ct, batches=("batch_a",)))
    body = aud(world, ct, provider="p2", branches=("p2b1",), batches=("batch_d",))
    assert eligible(client, login, ct, "pa2", body) == ["STU000001", "STU000006"]


def test_student_search_identifies_students_with_similar_names(client, world, ct, login):
    r = client.get(f"{API}/tests/students?provider_id={world['p1']}&branch_ids={world['p1b1']}&q=riya", headers=login(ct["pa"])).json()
    assert [(s["student_id"], s["name"], s["batches"]) for s in r["items"]] == [("STU000001", "Riya Patil", ["A"]), ("STU000002", "Riya Patel", ["B"])]
    mine = client.get(f"{API}/tests/students?provider_id={world['p1']}&branch_ids={world['p1b1']}&branch_ids={world['p1b2']}",
                      headers=login(ct["ba"])).json()
    assert {s["student_id"] for s in mine["items"]} == {"STU000001", "STU000002", "STU000004"}     # the branch admin's branch only


@pytest.mark.parametrize("audience", [
    {"branch_ids": [], "whole_branch_ids": [], "batch_ids": [], "student_ids": []},
    {"whole_branch_ids": [], "batch_ids": [], "student_ids": []},                       # a branch but nobody chosen
    {"student_ids": ["STU000001", "STU000001"]},                                        # duplicate
])
def test_empty_or_duplicate_audiences_are_rejected(client, world, ct, login, audience):
    body = {**aud(world, ct, batches=("batch_a",)), **audience}
    assert client.post(f"{API}/tests/audience", json=body, headers=login(ct["pa"])).status_code == 422


# ------------------------------------------------------------------ C. question paper + answer key

def test_id_based_answer_key_matches_by_question_id_with_quoted_explanations(client, world, ct, login, db_factory):
    from institute_common.models.papers import PaperVersionQuestion, QuestionVersion
    r = upload(client, login(ct["pa"]), world, ct, paper(4), key(4, answers="BDAC"))
    assert r.status_code == 201, r.text
    with db_factory() as db:
        rows = db.query(QuestionVersion.stem, QuestionVersion.correct_option, QuestionVersion.explanation).join(
            PaperVersionQuestion, PaperVersionQuestion.question_version_id == QuestionVersion.question_version_id).filter(
            PaperVersionQuestion.paper_version_id == UUID(r.json()["paper_version_id"])).order_by(PaperVersionQuestion.position).all()
    assert [c for _, c, _ in rows] == ["B", "D", "A", "C"]                                  # M001->B ... although the key is reversed
    assert rows[1][2] == "Because 2, and that is final"                                     # comma inside a quoted explanation


@pytest.mark.parametrize("text,k,fragment,question", [
    (paper(3).replace("question_id,", "").replace("M001,", "").replace("M002,", "").replace("M003,", ""), key(3), "question_id", None),
    (paper(3).replace("M003,", "M002,"), key(3), "appears more than once", "M002"),
    (paper(3), key(3) + "M002,C,\n", "appears twice in the answer key", None),
    (paper(3), key(3) + "M099,C,\n", "not in the paper: M099", None),
    (paper(3), "question_id,correct_option\nM001,A\nM002,B\n", "no answer for question M003", "M003"),
    (paper(3), "question_id,correct_option\nM001,A\nM002,E\nM003,B\n", "must be A, B, C or D", None),
    (paper(3).replace("Question number 2 for M?", ""), key(3), "Question text is empty", "M002"),
    (paper(3).replace(",4,1,\nM003", ",abc,1,\nM003"), key(3), "Marks must be a positive number", "M002"),
    (paper(3).replace("Question number 3 for M", "Question number 1 for M"), key(3), "Duplicate of row", "M003"),
    ("M001,Q?,a,b,c,d,4,1,\n", key(1), "Missing required column", None),
    (paper(3), "\x00\x01binary", "text or CSV", None),
])
def test_every_answer_key_or_paper_problem_rejects_the_whole_import(client, world, ct, login, db_factory, text, k, fragment, question):
    before = counts(db_factory)
    r = upload(client, login(ct["pa"]), world, ct, text, k)
    assert r.status_code == 422, r.text
    d = r.json()["detail"]
    hit = [e for e in d["errors"] if fragment.lower() in e["message"].lower()]
    assert hit, d["errors"]
    if question:
        assert hit[0].get("question") == question                                          # the real ID, never a guess
    assert counts(db_factory)["questions"] == before["questions"] and counts(db_factory)["versions"] == before["versions"]


def test_five_and_six_failed_questions_both_import_nothing_and_six_trips_the_threshold(client, world, ct, login, db_factory):
    h = login(ct["pa"])
    before = counts(db_factory)["questions"]
    five = upload(client, h, world, ct, paper(10, bad=(1, 2, 3, 4, 5)), key(10)).json()["detail"]
    six = upload(client, h, world, ct, paper(10, bad=(1, 2, 3, 4, 5, 6)), key(10)).json()["detail"]
    assert five["summary"] == {**five["summary"], "questions_detected": 10, "questions_failed": 5, "questions_valid": 5, "threshold_exceeded": False}
    assert six["summary"]["questions_failed"] == 6 and six["summary"]["threshold_exceeded"] is True
    assert "more than five" in six["message"] and "5 question(s) need correcting" in five["message"]
    assert {e["question"] for e in five["errors"]} == {"M001", "M002", "M003", "M004", "M005"}
    assert counts(db_factory)["questions"] == before


def test_retrying_after_a_rejection_and_reimporting_never_duplicates(client, world, ct, login, db_factory):
    h = login(ct["pa"])
    before = counts(db_factory)
    assert upload(client, h, world, ct, paper(3, bad=(2,)), key(3)).status_code == 422
    ok = upload(client, h, world, ct, paper(3), key(3))
    assert ok.status_code == 201
    again = upload(client, h, world, ct, paper(3), key(3))
    assert again.status_code == 409 and again.json()["detail"]["paper_version_id"] == ok.json()["paper_version_id"]
    after = counts(db_factory)
    assert after["versions"] - before["versions"] == 1 and after["questions"] - before["questions"] == 3


def test_numbered_keys_keep_working_with_quoted_explanations(client, world, ct, login):
    text = "question,option_a,option_b,option_c,option_d,marks,negative_marks\nFirst?,a,b,c,d,4,1\nSecond?,a,b,c,d,4,1\n"
    r = upload(client, login(ct["pa"]), world, ct, text, '1,B,"why, exactly"\n2,C\n')
    assert r.status_code == 201, r.text


# ------------------------------------------------------------------ D. scheduling and publication

@pytest.mark.parametrize("over,fragment", [
    ({"name": "   "}, "at least 3"),
    ({"duration_minutes": 0}, "greater than 0"),
    ({"subjects": []}, "at least 1"),
    ({"marks_correct": None}, "uniform marking"),
])
def test_invalid_metadata_is_rejected(client, world, ct, login, over, fragment):
    r = client.post(f"{API}/tests/check", json=draft(world, ct, **over), headers=login(ct["pa"]))
    assert r.status_code == 422 and fragment in r.text, r.text


def test_schedule_rules(client, world, ct, login):
    h = login(ct["pa"])
    now = utcnow()
    for start, end in ((now + timedelta(hours=3), now + timedelta(hours=2)), (now - timedelta(hours=3), now - timedelta(hours=2))):
        assert client.post(f"{API}/tests/check", json=draft(world, ct, start_at=start.isoformat(), end_at=end.isoformat()), headers=h).status_code == 422
    naive = (now + timedelta(hours=2)).replace(tzinfo=None).isoformat()
    assert client.post(f"{API}/tests/check", json=draft(world, ct, start_at=naive), headers=h).status_code == 422      # timezone required
    ok = client.post(f"{API}/tests/check", json=draft(world, ct), headers=h)
    assert ok.status_code == 200 and ok.json()["eligible_count"] == 2


def test_publishing_needs_validated_published_papers_with_an_answer_key(client, world, ct, login, db_factory):
    h = login(ct["pa"])
    r = publish(client, h, draft(world, ct))                                                 # no paper at all
    assert r.status_code == 422 and "Upload this subject" in r.text
    draft_pv = upload(client, h, world, ct, paper(3), key(3)).json()["paper_version_id"]   # imported, not approved/published
    r = publish(client, h, draft(world, ct, subjects=[("phy", draft_pv)]))
    assert r.status_code == 422 and "approved and published" in r.text
    assert counts(db_factory)["plans"] == 0 and counts(db_factory)["exams"] == 0
    with_answers = "question,option_a,option_b,option_c,option_d,correct,marks,negative_marks\nOnly?,a,b,c,d,A,4,1\n"
    pv = upload(client, h, world, ct, with_answers, None, title="no key").json()["paper_version_id"]
    client.post(f"{API}/paper-versions/{pv}/approve", json={"note": "x"}, headers=h)
    client.post(f"{API}/paper-versions/{pv}/publish", json={"note": None}, headers=h)
    r = publish(client, h, draft(world, ct, subjects=[("phy", pv)]))
    assert r.status_code == 422 and "answer key" in r.text


def test_an_approved_bank_paper_can_be_chosen_instead_of_an_upload(client, world, ct, login, db_factory):
    """A Daily Test may use an approved, published paper already in the bank (built from bank questions, no upload); papers the
    caller may not use are refused and never listed."""
    h = login(ct["pa"])
    listed = client.get(f"{API}/tests/papers?provider_id={world['p1']}&subject_id={ct['phy']}", headers=h).json()["items"]
    assert [(x["title"], x["source"], x["published_version"]["paper_version_id"]) for x in listed] == [("Physics Set", "Own", str(ct["phy_v1"]))]
    assert client.get(f"{API}/tests/papers?provider_id={world['p1']}&subject_id={ct['phy']}", headers=login(ct["pa2"])).status_code == 404
    assert client.get(f"{API}/tests/papers?provider_id={world['p1']}&subject_id={ct['phy']}", headers=login(ct["t_batch"])).status_code == 403
    r = publish(client, login(ct["pa"]), draft(world, ct, subjects=[("phy", str(ct["phy_v1"]))]))   # (one session per client)
    assert r.status_code == 201, r.text
    assert r.json()["test_type"] == "DAILY" and r.json()["question_count"] == 3
    other = publish(client, login(ct["pa2"]), draft(world, ct, audience=aud(world, ct, provider="p2", branches=("p2b1",), batches=("batch_d",)),
                                                   subjects=[("phy", str(ct["phy_v1"]))]))
    assert other.status_code == 422 and "not available" in other.text                    # another provider's paper


def test_a_valid_test_is_published_with_exactly_its_audience_and_marking(client, world, ct, login, db_factory):
    from institute_common.models.exams import ExamOutbox, StudentExamAssignment
    pv_phy = ready_paper(client, login, world, ct, subject="phy", prefix="P")
    pv_chem = ready_paper(client, login, world, ct, subject="chem", prefix="C", n=2)
    body = draft(world, ct, audience=aud(world, ct, branches=("p1b1", "p1b2"), batches=("batch_a", "batch_c"), students=("STU000002",)),
                 subjects=[("phy", pv_phy), ("chem", pv_chem)])
    r = publish(client, login(ct["pa"]), body)
    assert r.status_code == 201, r.text
    t = r.json()
    assert t["state"] == "PUBLISHED_DELIVERY_PENDING" and t["exam_status"] == "PUBLISHED" and t["name"] == "Weekly Physics"
    assert t["question_count"] == 5 and float(t["total_marks"]) == 20 and t["marking"] == {"mode": "UNIFORM", "correct": 4, "wrong": 1}
    assert t["subjects"] == ["Physics", "Chemistry"] and t["assigned_students"] == 4
    with db_factory() as db:
        assert sorted(a.student_id for a in db.query(StudentExamAssignment).all()) == ["STU000001", "STU000002", "STU000003", "STU000004"]
        assert db.query(ExamOutbox).filter_by(operation="PUBLISH", status="PENDING").count() == 1          # delivery queued, retried by the worker
    listed = client.get(f"{API}/tests", headers=login(ct["pa"])).json()["items"]
    assert [x["name"] for x in listed] == ["Weekly Physics"] and listed[0]["audience"]["batches"] == ["A", "C"]


def test_repeated_publication_is_idempotent(client, world, ct, login, db_factory):
    pv = ready_paper(client, login, world, ct)
    h = login(ct["pa"])
    body = draft(world, ct, subjects=[("phy", pv)])
    first = publish(client, h, body, "same-request-key-1").json()
    again = publish(client, h, body, "same-request-key-1")
    assert again.status_code == 201 and again.json()["exam_id"] == first["exam_id"]
    c = counts(db_factory)
    assert (c["plans"], c["exams"], c["assignments"], c["outbox"]) == (1, 1, 2, 1)
    assert publish(client, login(ct["sa"]), body, "same-request-key-1").status_code == 409                # someone else's key


def test_branch_admin_publishes_for_own_branch_and_teachers_see_the_tests_they_teach(client, world, ct, login):
    pv = ready_paper(client, login, world, ct, who="ba")
    r = publish(client, login(ct["ba"]), draft(world, ct, subjects=[("phy", pv)]))
    assert r.status_code == 201, r.text
    chem = publish(client, login(ct["pa"]), draft(world, ct, name="Chemistry only", subjects=[("chem", str(ct["chem_v1"]))]))
    assert chem.status_code == 201, chem.text
    assert {x["name"] for x in client.get(f"{API}/tests", headers=login(ct["ba"])).json()["items"]} == {"Weekly Physics", "Chemistry only"}
    assert [x["name"] for x in client.get(f"{API}/tests", headers=login(ct["t_batch"])).json()["items"]] == ["Weekly Physics"]   # teaches Physics
    assert client.get(f"{API}/tests", headers=login(ct["pa2"])).json()["items"] == []
    r = publish(client, login(ct["ba"]), draft(world, ct, audience=aud(world, ct, branches=("p1b2",), batches=("batch_c",)), subjects=[("phy", pv)]))
    assert r.status_code == 422                                                              # another branch: unauthorized publication


def test_a_reduced_exam_is_never_published(client, world, ct, login, db_factory, monkeypatch):
    from institute_common.models.exams import Exam, ExamOutbox, ExamPlan
    pv = ready_paper(client, login, world, ct)
    real = gen.resolve_slots

    def lose_a_subject(db, plan):
        res = real(db, plan)
        res.missing.append({"subject_id": str(ct["chem"]), "subject": "Chemistry", "position": 2, "reason": "NO_PUBLISHED_PAPER", "detail": "gone"})
        return res
    monkeypatch.setattr(gen, "resolve_slots", lose_a_subject)
    r = publish(client, login(ct["pa"]), draft(world, ct, subjects=[("phy", pv)]))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "PUBLICATION_FAILED"
    with db_factory() as db:
        assert db.query(Exam).one().status == "CANCELLED" and db.query(ExamPlan).one().status == "INACTIVE"
        assert db.query(ExamOutbox).count() == 0                                             # nothing delivered, nobody notified


def test_a_failed_generation_is_recorded_and_the_same_request_recovers(client, world, ct, login, db_factory, monkeypatch):
    pv = ready_paper(client, login, world, ct)
    h = login(ct["pa"])
    body = draft(world, ct, subjects=[("phy", pv)])
    real = gen.eligible_students
    monkeypatch.setattr(gen, "eligible_students", lambda db, plan: (_ for _ in ()).throw(RuntimeError("database hiccup")))
    r = publish(client, h, body, "recover-me-please")
    assert r.status_code == 409 and r.json()["detail"]["test"]["state"] == "PUBLICATION_FAILED"
    assert client.get(f"{API}/tests", headers=h).json()["items"][0]["state"] == "PUBLICATION_FAILED"   # recorded, visible
    monkeypatch.setattr(gen, "eligible_students", real)
    ok = publish(client, h, body, "recover-me-please")
    assert ok.status_code == 201 and ok.json()["state"] == "PUBLISHED_DELIVERY_PENDING" and counts(db_factory)["exams"] == 1


def test_delivery_failures_are_retried_and_students_are_notified_only_once_delivered(client, world, ct, login, db_factory):
    from institute_common.models.exams import ExamOutbox, StudentNotificationOutbox
    pv = ready_paper(client, login, world, ct)
    exam = publish(client, login(ct["pa"]), draft(world, ct, subjects=[("phy", pv)])).json()

    class Broken:
        def send(self, payload):
            raise outbox.TransientPublishError("student test service down")

    class Working:
        sent = []

        def send(self, payload):
            self.sent.append(payload)
    with db_factory() as db:
        outbox.deliver_due(db, publisher=Broken())
        row = db.query(ExamOutbox).one()
        assert row.status == "PENDING" and row.attempts == 1 and row.last_error                  # recorded, not "delivered"
        assert db.query(StudentNotificationOutbox).count() == 0
        row.next_attempt_at = utcnow()
        db.commit()
        outbox.deliver_due(db, publisher=Working())
        assert db.query(ExamOutbox).one().status == "SENT" and len(Working.sent) == 1
        assert db.query(StudentNotificationOutbox).count() == 1
        outbox.deliver_due(db, publisher=Working())
        assert len(Working.sent) == 1 and db.query(StudentNotificationOutbox).count() == 1   # no duplicates
    assert client.get(f"{API}/tests", headers=login(ct["pa"])).json()["items"][0]["state"] == "PUBLISHED"
    assert exam["exam_id"]


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="real concurrency needs PostgreSQL (TEST_DATABASE_URL)")
def test_concurrent_publication_requests_create_one_test(client, world, ct, login, db_factory):
    from fastapi.testclient import TestClient
    pv = ready_paper(client, login, world, ct)
    h = login(ct["pa"])
    body, results, barrier = draft(world, ct, subjects=[("phy", pv)]), [], threading.Barrier(3)
    cookies = dict(client.cookies)

    def go():
        with TestClient(client.app, base_url="http://localhost", cookies=cookies) as c:
            barrier.wait()
            results.append(c.post(f"{API}/tests", json=body, headers={**h, "Idempotency-Key": "concurrent-key-1"}).status_code)
    ts = [threading.Thread(target=go) for _ in range(3)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    c = counts(db_factory)
    assert (c["plans"], c["exams"], c["assignments"], c["outbox"]) == (1, 1, 2, 1), (results, c)
    assert results == [201, 201, 201], results                     # every concurrent request returns the same published test
