"""Regression tests for the defects found in the review of the Super Admin redesign (one section per finding)."""
import os
import threading
from datetime import timedelta
from uuid import UUID

import pytest

from institute_common.core.roles import Role
from institute_common.database import utcnow
from inst_exam.services import exam_generation as gen
from tests.test_question_bank import bank, create, q_body  # noqa: F401  (fixtures)
from tests.test_test_creation import body, gtest, tc  # noqa: F401  (fixtures)

API = "/api/v1"


def exams_of(db_factory, plan_id):
    from institute_common.models.exams import Exam
    with db_factory() as db:
        return [e.exam_id for e in db.query(Exam).filter_by(plan_id=UUID(str(plan_id))).all()]


def scheduled_plan(client, world, tc, h):  # noqa: F811
    start = (utcnow() + timedelta(days=1)).replace(microsecond=0)
    return client.post(f"{API}/branches/{world['p1b1']}/exam-plans", headers=h, json=body(
        tc, start_at=start.isoformat(), end_at=(start + timedelta(hours=3)).isoformat(),
        generate_at=(start - timedelta(hours=1)).isoformat())).json(), start


# ------------------------------------------------------------------ 2. one exam per plan and window

def test_manual_then_scheduled_generation_converges(client, world, tc, login, db_factory):  # noqa: F811
    h = login(tc["pa"])
    plan, start = scheduled_plan(client, world, tc, h)
    manual = client.post(f"{API}/exam-plans/{plan['plan_id']}/generate", json={}, headers={**h, "Idempotency-Key": "wizard-x"}).json()
    with db_factory() as db:
        assert gen.run_due_plans(db, now=start) == []           # nothing left to generate for that window
    assert exams_of(db_factory, plan["plan_id"]) == [UUID(manual["exam_id"])]


def test_scheduled_then_manual_generation_converges(client, world, tc, login, db_factory):  # noqa: F811
    h = login(tc["pa"])
    plan, start = scheduled_plan(client, world, tc, h)
    with db_factory() as db:
        [job] = gen.run_due_plans(db, now=start)
        scheduled_exam = job.exam_id
    again = client.post(f"{API}/exam-plans/{plan['plan_id']}/generate", json={}, headers={**h, "Idempotency-Key": "something-else"}).json()
    assert again["exam_id"] == str(scheduled_exam)
    assert exams_of(db_factory, plan["plan_id"]) == [scheduled_exam]


def test_repeated_requests_return_the_same_exam(client, world, tc, login, db_factory):  # noqa: F811
    h = login(tc["pa"])
    plan, _ = scheduled_plan(client, world, tc, h)
    ids = {client.post(f"{API}/exam-plans/{plan['plan_id']}/generate", json={}, headers={**h, "Idempotency-Key": f"k{i}"}).json()["exam_id"] for i in range(3)}
    assert len(ids) == 1 and len(exams_of(db_factory, plan["plan_id"])) == 1
    # a later window is a new exam
    later = utcnow() + timedelta(days=5)
    nxt = client.post(f"{API}/exam-plans/{plan['plan_id']}/generate", headers=h,
                      json={"start_at": later.isoformat(), "end_at": (later + timedelta(hours=2)).isoformat()}).json()
    assert nxt["exam_id"] not in ids


def test_runs_recorded_under_the_older_keys_are_recognised(client, world, tc, login, db_factory):  # noqa: F811
    """Exams generated before the canonical key existed ("manual:<idempotency key>") are not generated again."""
    from institute_common.models.exams import ExamGenerationJob
    h = login(tc["pa"])
    plan, start = scheduled_plan(client, world, tc, h)
    first = client.post(f"{API}/exam-plans/{plan['plan_id']}/generate", json={}, headers=h).json()
    with db_factory() as db:   # rewrite the key the way older code recorded it
        job = db.get(ExamGenerationJob, UUID(first["job_id"]))
        job.run_key = "manual:legacy-request"
        db.commit()
        assert gen.run_due_plans(db, now=start) == []
    again = client.post(f"{API}/exam-plans/{plan['plan_id']}/generate", json={}, headers=h).json()
    assert again["job_id"] == first["job_id"] and len(exams_of(db_factory, plan["plan_id"])) == 1


def test_a_run_in_progress_elsewhere_is_returned_not_duplicated(client, world, tc, login, db_factory):  # noqa: F811
    from institute_common.models.exams import ExamGenerationJob
    h = login(tc["pa"])
    plan, start = scheduled_plan(client, world, tc, h)
    with db_factory() as db:   # the scheduler has just claimed this window
        db.add(ExamGenerationJob(plan_id=UUID(plan["plan_id"]), run_key=gen.canonical_run_key(start), started_at=utcnow()))
        db.commit()
    r = client.post(f"{API}/exam-plans/{plan['plan_id']}/generate", json={}, headers=h).json()
    assert r["status"] == "RUNNING" and r["exam_id"] is None and exams_of(db_factory, plan["plan_id"]) == []


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="real concurrency needs PostgreSQL (TEST_DATABASE_URL)")
def test_concurrent_manual_and_scheduled_generation_create_one_exam(client, world, tc, login, db_factory):  # noqa: F811
    h = login(tc["pa"])
    plan, start = scheduled_plan(client, world, tc, h)
    barrier, results = threading.Barrier(4), []

    def run():
        with db_factory() as db:
            barrier.wait()
            job = gen.generate(db, UUID(plan["plan_id"]), now=start)
            results.append(job.job_id)
    threads = [threading.Thread(target=run) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(results)) == 1 and len(exams_of(db_factory, plan["plan_id"])) == 1


# ------------------------------------------------------------------ 4. withdrawn Global Tests

def schedule(client, world, tc, gtest, h, branch="p1b1", hours=2):  # noqa: F811
    start = utcnow() + timedelta(hours=hours)
    return client.post(f"{API}/global-tests/{gtest['test_id']}/schedule", headers=h, json={
        "branch_id": str(world[branch]), "audience_type": "BRANCH", "start_at": start.isoformat(), "end_at": (start + timedelta(hours=2)).isoformat()}).json()


def test_withdrawn_test_cannot_be_reactivated_or_generated(client, world, tc, gtest, login, db_factory):  # noqa: F811
    client.post(f"{API}/global-tests/{gtest['test_id']}/publish", headers=login(tc["sa"]))
    h = login(tc["pa"])
    plan = schedule(client, world, tc, gtest, h)
    exam_before = client.post(f"{API}/exam-plans/{plan['plan_id']}/generate", json={}, headers=h).json()["exam_id"]
    client.post(f"{API}/global-tests/{gtest['test_id']}/withdraw", json={"reason": "Wrong answer in Q3"}, headers=login(tc["sa"]))
    h = login(tc["pa"])
    r = client.patch(f"{API}/exam-plans/{plan['plan_id']}", json={"status": "ACTIVE"}, headers=h)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "GLOBAL_TEST_NOT_PUBLISHED"
    later = utcnow() + timedelta(days=3)
    r = client.post(f"{API}/exam-plans/{plan['plan_id']}/generate", headers=h,
                    json={"start_at": later.isoformat(), "end_at": (later + timedelta(hours=1)).isoformat()})
    assert r.status_code == 409
    assert client.get(f"{API}/exams/{exam_before}", headers=h).json()["status"] == "PUBLISHED"   # history untouched


def test_generation_rechecks_the_test_inside_the_transaction(client, world, tc, gtest, login, db_factory):  # noqa: F811
    """Even if the plan were still ACTIVE (e.g. withdrawal committed between the plan check and the write), nothing is generated."""
    from institute_common.models.exams import ExamPlan, GlobalTest
    client.post(f"{API}/global-tests/{gtest['test_id']}/publish", headers=login(tc["sa"]))
    plan = schedule(client, world, tc, gtest, login(tc["pa"]))
    with db_factory() as db:   # withdrawn behind the plan's back
        db.get(GlobalTest, UUID(gtest["test_id"])).status = "WITHDRAWN"
        db.commit()
        assert db.get(ExamPlan, UUID(plan["plan_id"])).status == "ACTIVE"
        with pytest.raises(gen.PlanNotGeneratable):
            gen.generate(db, UUID(plan["plan_id"]))
        assert gen.run_due_plans(db, now=utcnow() + timedelta(days=30)) == []
    assert exams_of(db_factory, plan["plan_id"]) == []


def test_unpublished_test_plans_are_skipped_by_the_scheduler(db_factory, client, world, tc, gtest, login):  # noqa: F811
    from institute_common.models.exams import ExamPlan, GlobalTest
    client.post(f"{API}/global-tests/{gtest['test_id']}/publish", headers=login(tc["sa"]))
    plan = schedule(client, world, tc, gtest, login(tc["pa"]))
    with db_factory() as db:
        p = db.get(ExamPlan, UUID(plan["plan_id"]))
        p.generate_at = utcnow() - timedelta(minutes=1)
        db.get(GlobalTest, UUID(gtest["test_id"])).status = "WITHDRAWN"
        db.commit()
        assert gen.run_due_plans(db) == []


# ------------------------------------------------------------------ 1. corrections never re-version someone else's question

def _paper_with(client, login, code, branch, subject, qids, title="Mixed"):
    r = client.post(f"{API}/papers", json={"branch_id": str(branch), "subject_id": str(subject), "title": title, "question_ids": qids}, headers=login(code))
    assert r.status_code == 201, r.text
    return r.json()["paper_version_id"]


def _revise(client, h, pv, position, stem):
    return client.post(f"{API}/paper-versions/{pv}/revisions", json={"edits": [{"position": position, "stem": stem}], "reason": "typo fix"}, headers=h)


def _versions(db_factory, qid):
    from institute_common.models.papers import QuestionVersion
    with db_factory() as db:
        return [(v.version_no, v.stem) for v in db.query(QuestionVersion).filter_by(question_id=UUID(qid)).order_by(QuestionVersion.version_no)]


def test_correcting_a_platform_question_makes_a_provider_copy(client, world, bank, login, db_factory):  # noqa: F811
    from institute_common.models.papers import PaperVersionQuestion, Question, QuestionVersion
    g = create(client, login, bank["sa"], q_body(bank["phy"], scope="GLOBAL", stem="Platform question?"))
    pv = _paper_with(client, login, bank["pa1"], world["p1b1"], bank["phy"], [g])
    r = _revise(client, login(bank["pa1"]), pv, 1, "Corrected by the provider?")
    assert r.status_code == 201, r.text
    assert _versions(db_factory, g) == [(1, "Platform question?")]              # the platform question is untouched
    with db_factory() as db:
        qv = db.query(QuestionVersion).join(PaperVersionQuestion, PaperVersionQuestion.question_version_id == QuestionVersion.question_version_id)\
            .filter(PaperVersionQuestion.paper_version_id == UUID(r.json()["paper_version_id"])).one()
        copy = db.get(Question, qv.question_id)
        assert (copy.scope, copy.provider_id, copy.branch_id, copy.forked_from_question_id) == ("PROVIDER", world["p1"], world["p1b1"], UUID(g))
        assert qv.stem == "Corrected by the provider?" and qv.version_no == 1
    other = next(x for x in client.get(f"{API}/questions?scope=GLOBAL", headers=login(bank["pa2"])).json()["items"] if x["question_id"] == g)
    assert other["latest"]["stem"] == "Platform question?"


def test_correcting_another_branchs_question_makes_a_copy_even_for_the_provider_admin(client, world, bank, login, db_factory, make_staff):  # noqa: F811
    b2q = create(client, login, bank["pa1"], q_body(bank["phy"], branch_id=str(world["p1b2"]), stem="Branch two question?"))
    pv = _paper_with(client, login, bank["pa1"], world["p1b1"], bank["phy"], [b2q])
    _, ba = make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b1"])])
    assert _revise(client, login(ba), pv, 1, "Branch one wording?").status_code == 201
    assert _revise(client, login(bank["pa1"]), pv, 1, "Provider admin wording?").status_code == 201
    assert _versions(db_factory, b2q) == [(1, "Branch two question?")]
    # editing that question directly is a separate, authorised action: the provider admin may, the other branch's admin may not
    body_ = {k: v for k, v in q_body(bank["phy"], stem="Edited at its source?").items()
             if k in ("stem", "option_a", "option_b", "option_c", "option_d", "correct_option", "marks_positive", "marks_negative")}
    assert client.post(f"{API}/questions/{b2q}/versions", json=body_, headers=login(ba)).status_code == 403
    assert client.post(f"{API}/questions/{b2q}/versions", json=body_, headers=login(bank["pa1"])).status_code == 201


def test_own_question_in_the_papers_branch_is_still_versioned(client, world, bank, login, db_factory):  # noqa: F811
    own = create(client, login, bank["pa1"], q_body(bank["phy"], branch_id=str(world["p1b1"]), stem="Own question?"))
    pv = _paper_with(client, login, bank["pa1"], world["p1b1"], bank["phy"], [own])
    assert _revise(client, login(bank["pa1"]), pv, 1, "Own question, corrected?").status_code == 201
    assert _versions(db_factory, own) == [(1, "Own question?"), (2, "Own question, corrected?")]


def test_a_teacher_correcting_another_teachers_question_makes_a_copy(client, world, bank, login, db_factory, make_staff):  # noqa: F811
    from institute_common.models.academics import TeacherSubjectAssignment
    t2, t2code = make_staff([(Role.TEACHER, world["p1"], None)])
    with db_factory() as db:
        db.add(TeacherSubjectAssignment(staff_id=t2, provider_id=world["p1"], branch_id=world["p1b1"], subject_id=bank["phy"]))
        db.commit()
    theirs = create(client, login, t2code, q_body(bank["phy"], branch_id=str(world["p1b1"]), stem="Teacher two question?"))
    pv = _paper_with(client, login, bank["teacher"], world["p1b1"], bank["phy"], [theirs])
    assert _revise(client, login(bank["teacher"]), pv, 1, "Teacher one rewording?").status_code == 201
    assert _versions(db_factory, theirs) == [(1, "Teacher two question?")]


# ------------------------------------------------------------------ 3 + 5. Global Test answers and papers are reserved

@pytest.fixture()
def gdt(client, world, bank, login):  # noqa: F811
    """A published Global Test built entirely through the API from platform questions."""
    h = login(bank["sa"])
    qids = [create(client, login, bank["sa"], q_body(bank["phy"], scope="GLOBAL", stem=f"Daily question {i}?")) for i in range(2)]
    h = login(bank["sa"])
    pv = client.post(f"{API}/papers", json={"scope": "GLOBAL", "subject_id": str(bank["phy"]), "title": "Daily paper", "question_ids": qids}, headers=h).json()["paper_version_id"]
    client.post(f"{API}/paper-versions/{pv}/approve", json={}, headers=h)
    client.post(f"{API}/paper-versions/{pv}/publish", json={}, headers=h)
    t = client.post(f"{API}/global-tests", json={"name": "Daily #1", "duration_minutes": 30, "sections": [{"paper_version_id": pv}]}, headers=h).json()
    assert client.post(f"{API}/global-tests/{t['test_id']}/publish", headers=h).json()["status"] == "PUBLISHED"
    return {"test_id": t["test_id"], "pv": pv, "qids": qids}


def _answers(client, login, code, gdt):
    h = login(code)
    detail = client.get(f"{API}/paper-versions/{gdt['pv']}", headers=h).json()["questions"]
    listed = [x["latest"] for x in client.get(f"{API}/questions?scope=GLOBAL", headers=h).json()["items"] if x["question_id"] in gdt["qids"]]
    one = client.get(f"{API}/questions/{gdt['qids'][0]}", headers=h).json()
    values = [q["correct_option"] for q in detail + listed + one["versions"] + [one["question"]["latest"]]]
    expl = [q["explanation"] for q in detail + listed]
    hidden = {q["answers_hidden"] for q in detail + listed + one["versions"]}
    return values, expl, hidden


def _staff(make_staff, world):
    _, ba = make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b1"])])
    _, t = make_staff([(Role.TEACHER, world["p1"], None)])
    return ba, t


def _schedule_and_generate(client, world, login, code, gdt, start, end):
    h = login(code)
    plan = client.post(f"{API}/global-tests/{gdt['test_id']}/schedule", headers=h, json={
        "branch_id": str(world["p1b1"]), "audience_type": "BRANCH", "start_at": start.isoformat(), "end_at": end.isoformat()}).json()
    assert "plan_id" in plan, plan
    job = client.post(f"{API}/exam-plans/{plan['plan_id']}/generate", json={}, headers=h).json()
    assert job["exam_id"], job
    return job["exam_id"]


def test_platform_admins_always_see_global_test_answers(client, world, bank, gdt, login):  # noqa: F811
    values, expl, hidden = _answers(client, login, bank["sa"], gdt)
    assert set(values) == {"A"} and set(expl) == {None} and hidden == {False}


def test_provider_staff_do_not_see_answers_before_or_during_the_exam(client, world, bank, gdt, login, make_staff):  # noqa: F811
    ba, t = _staff(make_staff, world)
    for code in (bank["pa1"], ba, t, bank["pa2"]):
        values, _, hidden = _answers(client, login, code, gdt)
        assert set(values) == {None} and hidden == {True}, code
    now = utcnow()
    _schedule_and_generate(client, world, login, bank["pa1"], gdt, now - timedelta(minutes=5), now + timedelta(hours=2))   # window open
    for code in (bank["pa1"], ba, t):
        assert set(_answers(client, login, code, gdt)[0]) == {None}, code


def test_answers_are_released_to_a_provider_once_its_exam_window_has_closed(client, world, bank, gdt, login, make_staff):  # noqa: F811
    ba, t = _staff(make_staff, world)
    now = utcnow()
    _schedule_and_generate(client, world, login, bank["pa1"], gdt, now - timedelta(hours=3), now - timedelta(hours=1))   # closed
    for code in (bank["pa1"], ba, t):
        values, _, hidden = _answers(client, login, code, gdt)
        assert set(values) == {"A"} and hidden == {False}, code
    values, _, hidden = _answers(client, login, bank["pa2"], gdt)   # another provider: no exam of theirs has closed
    assert set(values) == {None} and hidden == {True}


def test_providers_cannot_build_reserved_questions_into_their_papers(client, world, bank, gdt, login):  # noqa: F811
    r = client.post(f"{API}/papers", json={"branch_id": str(world["p1b1"]), "subject_id": str(bank["phy"]), "title": "Copy", "question_ids": gdt["qids"]},
                    headers=login(bank["pa1"]))
    assert r.status_code == 422 and "Global Test" in r.json()["detail"]["errors"][0]["message"]
    item = next(x for x in client.get(f"{API}/questions?scope=GLOBAL", headers=login(bank["pa1"])).json()["items"] if x["question_id"] == gdt["qids"][0])
    assert item["reserved_for_global_test"] is True


def test_ordinary_plans_cannot_reuse_global_test_papers(client, world, bank, gdt, login):  # noqa: F811
    now = utcnow() + timedelta(days=1)
    base = {"name": "Reuse attempt", "duration_minutes": 600, "start_at": now.isoformat(), "end_at": (now + timedelta(hours=2)).isoformat(),
            "audience_type": "BRANCH", "marking_mode": "UNIFORM", "marks_correct": 1, "marks_wrong": 0}
    h = login(bank["pa1"])
    for slot in ({"paper_version_id": gdt["pv"]}, {"paper_id": client.get(f"{API}/paper-versions/{gdt['pv']}", headers=h).json()["paper_id"]}):
        r = client.post(f"{API}/branches/{world['p1b1']}/exam-plans", json={**base, "slots": [{"subject_id": str(bank["phy"]), **slot}]}, headers=h)
        assert r.status_code == 422 and r.json()["detail"]["code"] == "GLOBAL_TEST_PAPER", r.text
    # the authorised route still works
    start = utcnow() + timedelta(days=2)
    assert _schedule_and_generate(client, world, login, bank["pa1"], gdt, start, start + timedelta(hours=1))


def test_a_plan_created_before_the_paper_became_reserved_cannot_generate_with_it(client, world, bank, login, db_factory):  # noqa: F811
    h = login(bank["sa"])
    qid = create(client, login, bank["sa"], q_body(bank["phy"], scope="GLOBAL", stem="Soon reserved?"))
    h = login(bank["sa"])
    pv = client.post(f"{API}/papers", json={"scope": "GLOBAL", "subject_id": str(bank["phy"]), "title": "Free platform paper", "question_ids": [qid]}, headers=h).json()["paper_version_id"]
    client.post(f"{API}/paper-versions/{pv}/approve", json={}, headers=h)
    client.post(f"{API}/paper-versions/{pv}/publish", json={}, headers=h)
    start = utcnow() + timedelta(days=1)
    h = login(bank["pa1"])
    plan = client.post(f"{API}/branches/{world['p1b1']}/exam-plans", headers=h, json={
        "name": "Uses a free platform paper", "duration_minutes": 60, "start_at": start.isoformat(), "end_at": (start + timedelta(hours=1)).isoformat(),
        "audience_type": "BRANCH", "slots": [{"subject_id": str(bank["phy"]), "paper_version_id": pv}]})
    assert plan.status_code == 201, plan.text              # unreserved platform papers stay usable
    h = login(bank["sa"])
    t = client.post(f"{API}/global-tests", json={"name": "Now reserved", "duration_minutes": 30, "sections": [{"paper_version_id": pv}]}, headers=h).json()
    client.post(f"{API}/global-tests/{t['test_id']}/publish", headers=h)
    job = client.post(f"{API}/exam-plans/{plan.json()['plan_id']}/generate", json={}, headers=login(bank["pa1"])).json()
    assert job["status"] == "FAILED"
    with db_factory() as db:
        from institute_common.models.exams import ExamGenerationEvent
        events = db.query(ExamGenerationEvent).filter_by(job_id=UUID(job["job_id"])).all()
        assert any(m.get("reason") == "RESERVED_FOR_GLOBAL_TEST" for e in events for m in (e.details or {}).get("missing", []))


# ------------------------------------------------------------------ 7. topic and label validation

@pytest.mark.parametrize("topic", ["Laws of Motion", "Organic Chemistry: Basics", "Newton’s laws", "Cell – structure", "Topic_1",
                                   "सजीव और निर्जीव", "क्ष‍ेत्र", "pH & buffers (1st order)", "x" * 200])
def test_valid_topics_are_accepted(client, world, bank, login, topic):  # noqa: F811
    r = client.post(f"{API}/questions", json=q_body(bank["phy"], branch_id=str(world["p1b1"]), topic=topic), headers=login(bank["pa1"]))
    assert r.status_code == 201, r.text
    assert client.get(f"{API}/questions/{r.json()['question_id']}", headers=login(bank["pa1"])).json()["question"]["topic"] == topic


@pytest.mark.parametrize("topic", ["x" * 201, "bad\u0007bell", "---", "    "])
def test_invalid_topics_are_rejected_or_blanked(client, world, bank, login, topic):  # noqa: F811
    r = client.post(f"{API}/questions", json=q_body(bank["phy"], branch_id=str(world["p1b1"]), topic=topic), headers=login(bank["pa1"]))
    if topic.strip():
        assert r.status_code == 422, r.text
    else:
        assert r.status_code == 201 and client.get(f"{API}/questions/{r.json()['question_id']}", headers=login(bank["pa1"])).json()["question"]["topic"] is None


def test_labels_keep_their_80_character_limit_and_unicode():
    from institute_common.core.taxonomy import clean_label
    assert clean_label("  NEET   UG ") == "NEET UG"
    assert clean_label("कक्षा 12") == "कक्षा 12"
    with pytest.raises(ValueError):
        clean_label("x" * 81)


# ------------------------------------------------------------------ 8. student search

from tests.test_directories import people  # noqa: E402,F401  (fixture)


@pytest.mark.parametrize("q,expected", [("STU", ["STU000001", "STU000002"]), ("stu", ["STU000001", "STU000002"]),
                                        ("0002", ["STU000002"]), ("STU000001", ["STU000001"]), ("aman@example.com", ["STU000002"]),
                                        ("9000000001", ["STU000001"]), ("", ["STU000001", "STU000002"]), ("%20%20", ["STU000001", "STU000002"]),
                                        ("Riya", []), ("%25", None), ("STU_", None)])
def test_student_search_terms(client, world, people, make_staff, login, q, expected):  # noqa: F811
    _, pa = make_staff([(Role.PROVIDER_ADMIN, world["p1"], None)])
    r = client.get(f"{API}/students?q={q}", headers=login(pa))
    if expected is None:                                   # LIKE wildcards are never interpreted
        assert r.status_code == 422
    else:
        assert r.status_code == 200 and [x["student_id"] for x in r.json()["items"]] == expected


def test_student_search_stays_scoped(client, world, people, make_staff, login):  # noqa: F811
    _, ba = make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b2"])])
    assert [x["student_id"] for x in client.get(f"{API}/students?q=STU", headers=login(ba)).json()["items"]] == ["STU000002"]
    _, pa2 = make_staff([(Role.PROVIDER_ADMIN, world["p2"], None)])
    assert [x["student_id"] for x in client.get(f"{API}/students?q=STU00000", headers=login(pa2)).json()["items"]] == ["STU000001", "STU000003"]
    _, t = make_staff([(Role.TEACHER, world["p1"], None)])
    assert client.get(f"{API}/students?q=STU", headers=login(t)).status_code == 403
    client.cookies.clear()
    assert client.get(f"{API}/students?q=STU").status_code == 401


# ------------------------------------------------------------------ 6. scoping: student detail and whole-institute exams

def _assign(db_factory, world, student, branch, name, audience="BRANCH", end_offset_h=2):
    """A generated, published exam assigned to `student` through `enrollment` (rows written directly: generation is tested elsewhere)."""
    from institute_common.models.exams import Exam, ExamPlan, ExamVersion, StudentExamAssignment
    now = utcnow()
    with db_factory() as db:
        plan = ExamPlan(provider_id=world["p1"], branch_id=world[branch], name=name, duration_minutes=60, start_at=now,
                        end_at=now + timedelta(hours=end_offset_h), audience_type=audience, created_by=world["p1"])
        db.add(plan)
        db.flush()
        e = Exam(plan_id=plan.plan_id, run_key="r", provider_id=world["p1"], branch_id=world[branch], name=name, status="PUBLISHED")
        db.add(e)
        db.flush()
        ev = ExamVersion(exam_id=e.exam_id, version_no=1, start_at=now, end_at=now + timedelta(hours=end_offset_h), duration_minutes=60,
                         total_questions=1, total_marks=4)
        db.add(ev)
        db.flush()
        for sid, eid in student:
            db.add(StudentExamAssignment(exam_id=e.exam_id, exam_version_id=ev.exam_version_id, student_id=sid, enrollment_id=eid))
        db.commit()
        return e.exam_id


@pytest.fixture()
def two_branches(client, world, people, db_factory):  # noqa: F811
    """STU000001 is enrolled in p1-b1 (e1) and, for this test, also in p1-b2 (e5); STU000002 only in p1-b2 (e3)."""
    from institute_common.models.org import Enrollment
    with db_factory() as db:
        db.add(Enrollment(enrollment_id="e5", provider_id=world["p1"], branch_id=world["p1b2"], student_id="STU000001"))
        db.commit()
    return {
        "b1_exam": _assign(db_factory, world, [("STU000001", "e1")], "p1b1", "Branch one weekly"),
        "b2_exam": _assign(db_factory, world, [("STU000001", "e5"), ("STU000002", "e3")], "p1b2", "Branch two weekly"),
        "inst_exam": _assign(db_factory, world, [("STU000001", "e1"), ("STU000002", "e3")], "p1b1", "Institute mock", audience="INSTITUTE"),
    }


def test_branch_admin_student_detail_hides_sibling_branch_exams(client, world, two_branches, make_staff, login):
    _, ba2 = make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b2"])])
    d = client.get(f"{API}/students/STU000001", headers=login(ba2)).json()
    assert [e["branch_name"] for e in d["enrollments"]] == ["p1-b2"]
    assert {e["name"] for e in d["exams"]} == {"Branch two weekly"}                 # not branch one's exams (incl. the institute one via e1)
    _, pa = make_staff([(Role.PROVIDER_ADMIN, world["p1"], None)])
    assert {e["name"] for e in client.get(f"{API}/students/STU000001", headers=login(pa)).json()["exams"]} == {
        "Branch one weekly", "Branch two weekly", "Institute mock"}


def test_institute_exam_is_visible_to_every_branch_admin_of_the_provider_once(client, world, two_branches, make_staff, login):
    _, ba2 = make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b2"])])
    h = login(ba2)
    listed = client.get(f"{API}/branches/{world['p1b2']}/exams", headers=h).json()["items"]
    assert {x["name"] for x in listed} == {"Branch two weekly", "Institute mock"}
    inst = next(x for x in listed if x["name"] == "Institute mock")
    assert inst["institute_wide"] is True and inst["branch_id"] == str(world["p1b1"]) and inst["assigned_students"] == 1   # own students only
    detail = client.get(f"{API}/exams/{two_branches['inst_exam']}", headers=h)
    assert detail.status_code == 200 and detail.json()["assigned_students"] == 1
    assigned = client.get(f"{API}/exams/{two_branches['inst_exam']}/assignments", headers=h).json()["items"]
    assert [a["student_id"] for a in assigned] == ["STU000002"]
    # ordinary exams stay branch-scoped
    assert client.get(f"{API}/exams/{two_branches['b1_exam']}", headers=h).status_code == 403
    # provider admin: the whole exam; exactly one exam row exists for it
    _, pa = make_staff([(Role.PROVIDER_ADMIN, world["p1"], None)])
    assert client.get(f"{API}/exams/{two_branches['inst_exam']}", headers=login(pa)).json()["assigned_students"] == 2


def test_institute_exam_results_are_scoped_to_the_viewers_branch(client, world, two_branches, make_staff, login):
    from institute_common.clients.result_client import set_result_client

    class Results:
        def exam_subject_results(self, _ev):
            return []

        def exam_results(self, _ev):
            return [{"student_id": "STU000001", "obtained_marks": 4}, {"student_id": "STU000002", "obtained_marks": 2}]
    set_result_client(Results())
    try:
        _, ba2 = make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b2"])])
        r = client.get(f"{API}/exams/{two_branches['inst_exam']}/results", headers=login(ba2)).json()
        assert [x["student_id"] for x in r["results"]] == ["STU000002"] and r["summary"]["assigned"] == 1
        _, pa = make_staff([(Role.PROVIDER_ADMIN, world["p1"], None)])
        assert len(client.get(f"{API}/exams/{two_branches['inst_exam']}/results", headers=login(pa)).json()["results"]) == 2
    finally:
        set_result_client(None)


def test_other_providers_and_other_branch_teachers_cannot_see_institute_exams(client, world, two_branches, make_staff, login):
    _, pa2 = make_staff([(Role.PROVIDER_ADMIN, world["p2"], None)])
    h = login(pa2)
    assert client.get(f"{API}/exams/{two_branches['inst_exam']}", headers=h).status_code == 403
    assert client.get(f"{API}/exams/{two_branches['inst_exam']}/results", headers=h).status_code == 403
    _, ba_p2 = make_staff([(Role.BRANCH_ADMIN, world["p2"], world["p2b1"])])
    assert client.get(f"{API}/exams/{two_branches['inst_exam']}", headers=login(ba_p2)).status_code == 403
    assert client.get(f"{API}/branches/{world['p1b2']}/exams", headers=login(ba_p2)).status_code == 403


def test_institute_exam_notifications_reach_every_branch_admin(db_factory, world, make_staff):
    from inst_exam.services.notify import responsible_staff
    ba1, _ = make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b1"])])
    ba2, _ = make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b2"])])
    other, _ = make_staff([(Role.BRANCH_ADMIN, world["p2"], world["p2b1"])])
    with db_factory() as db:
        branch_only = set(responsible_staff(db, world["p1"], world["p1b1"]))
        institute = set(responsible_staff(db, world["p1"], world["p1b1"], institute=True))
    assert ba1 in branch_only and ba2 not in branch_only
    assert {ba1, ba2} <= institute and other not in institute


# ------------------------------------------------------------------ 11. paper versions of published tests

def test_a_paper_version_in_a_published_test_cannot_be_withdrawn(client, bank, gdt, login):  # noqa: F811
    h = login(bank["sa"])
    r = client.post(f"{API}/paper-versions/{gdt['pv']}/withdraw", json={"reason": "outdated"}, headers=h)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "PAPER_IN_GLOBAL_TEST" and r.json()["detail"]["tests"] == ["Daily #1"]
    r = client.post(f"{API}/paper-versions/{gdt['pv']}/override-status", json={"to_status": "WITHDRAWN", "reason": "outdated"}, headers=h)
    assert r.status_code == 409
    client.post(f"{API}/global-tests/{gdt['test_id']}/withdraw", json={"reason": "retired"}, headers=h)
    assert client.post(f"{API}/paper-versions/{gdt['pv']}/withdraw", json={"reason": "outdated"}, headers=h).status_code == 200


def test_published_test_cannot_be_edited_through_the_api(client, bank, gdt, login):  # noqa: F811
    r = client.put(f"{API}/global-tests/{gdt['test_id']}", headers=login(bank["sa"]),
                   json={"name": "Edited after publish", "duration_minutes": 10, "sections": [{"paper_version_id": gdt["pv"]}]})
    assert r.status_code == 409


# ------------------------------------------------------------------ 10. dry runs used by the wizard write nothing

def _counts(db_factory):
    from institute_common.models.exams import ExamPlan
    from institute_common.models.papers import Paper, PaperVersion
    with db_factory() as db:
        return db.query(ExamPlan).count(), db.query(Paper).count(), db.query(PaperVersion).count()


def test_plan_check_validates_like_create_and_writes_nothing(client, world, tc, login, db_factory, make_staff):  # noqa: F811
    h = login(tc["pa"])
    before = _counts(db_factory)
    url = f"{API}/branches/{world['p1b1']}/exam-plans/check"
    ok = client.post(url, json=body(tc, slots=[], pending_subject_ids=[str(tc["phy"])]), headers=h)
    assert ok.status_code == 200 and ok.json() == {"ok": True}
    bad = [body(tc, slots=[], pending_subject_ids=[]),                                                  # no subjects
           body(tc, slots=[], pending_subject_ids=[str(tc["phy"])], audience_type="STUDENTS", batch_ids=[], student_ids=["STU999999"]),
           body(tc, slots=[{"subject_id": str(tc["phy"]), "paper_version_id": str(tc["phy_v1"])}], pending_subject_ids=[str(tc["phy"])]),  # duplicate
           body(tc, slots=[], pending_subject_ids=[str(tc["phy"])], end_at=utcnow().isoformat())]       # window ends before it starts
    for b in bad:
        assert client.post(url, json=b, headers=h).status_code == 422, b
    _, other = make_staff([(Role.PROVIDER_ADMIN, world["p2"], None)])
    assert client.post(url, json=body(tc, slots=[], pending_subject_ids=[str(tc["phy"])]), headers=login(other)).status_code == 403
    assert _counts(db_factory) == before


def test_paper_check_validates_like_build_and_writes_nothing(client, world, bank, login, db_factory):  # noqa: F811
    own = create(client, login, bank["pa1"], q_body(bank["phy"], branch_id=str(world["p1b1"]), stem="Checked question?"))
    h = login(bank["pa1"])
    before = _counts(db_factory)
    req = {"branch_id": str(world["p1b1"]), "subject_id": str(bank["phy"]), "title": "Checked", "question_ids": [own]}
    r = client.post(f"{API}/papers/check", json=req, headers=h)
    assert r.status_code == 200 and r.json()["question_count"] == 1
    assert client.post(f"{API}/papers/check", json={**req, "question_ids": [own, "00000000-0000-0000-0000-000000000000"]}, headers=h).status_code == 422
    assert _counts(db_factory) == before
    assert client.post(f"{API}/papers/check", json=req, headers=login(bank["pa2"])).status_code == 403


def test_answers_are_withheld_by_reserved_version_even_for_unusual_question_rows(client, world, tc, gtest, login, make_staff):  # noqa: F811
    """The review probe: a platform paper whose question rows are not GLOBAL-scoped still has its answers withheld."""
    client.post(f"{API}/global-tests/{gtest['test_id']}/publish", headers=login(tc["sa"]))
    _, t2 = make_staff([(Role.TEACHER, world["p2"], None)])
    qs = client.get(f"{API}/paper-versions/{gtest['version']}", headers=login(t2)).json()["questions"]
    assert {q["correct_option"] for q in qs} == {None} and {q["answers_hidden"] for q in qs} == {True}
    assert {q["correct_option"] for q in client.get(f"{API}/paper-versions/{gtest['version']}", headers=login(tc["sa"])).json()["questions"]} <= set("ABCD")
