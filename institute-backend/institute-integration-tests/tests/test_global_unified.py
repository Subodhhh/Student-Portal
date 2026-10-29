"""One UNIFIED exam per student per Global Test date, assembled from the papers of the student's own providers, and cross-provider
isolation of everything the Institute side shows about it.

World: tests.test_global_schedule.gs, plus Biology offered by p2 (the tutor) with an approved paper. The test has Physics, Chemistry and
Biology. Submissions: tA Physics for batch A (p1), tC Chemistry for all of p1-b1, the tutor Biology for STU000004 and STU000001 (p2).
STU000001 studies with both providers (g1 in p1-b1 batch A, g5 with p2), STU000002 only with p1 (batch B), STU000004 only with p2."""
from decimal import Decimal
from uuid import UUID

import pytest

from institute_common.core.roles import Role
from inst_exam.services import global_generation as gg
from tests.test_exams import make_paper
from tests.test_global_contributions import client, login  # noqa: F401
from tests.test_global_schedule import aud, gs, published, run_generation, submit  # noqa: F401

API = "/api/v1"


@pytest.fixture()
def uni(client, world, gs, login, db_factory):  # noqa: F811
    from institute_common.models.academics import ProviderSubject
    with db_factory() as db:
        db.add(ProviderSubject(provider_id=world["p2"], subject_id=gs["bio"]))
        db.commit()
    _pid, pv = make_paper(db_factory, world["p2"], world["p2b1"], gs["bio"], "paperBio paper", "PUBLISHED", n=2)
    gs["paperBio"] = str(pv)
    t, occ = published(client, login, gs, subject_ids=[str(gs["phy"]), str(gs["chem"]), str(gs["bio"])])
    assert submit(client, login, gs["tA"], occ, gs["phy"], gs["paperA"], aud(world, gs, batches=("batch_a",))).status_code == 201
    assert submit(client, login, gs["tC"], occ, gs["chem"], gs["paperC"], aud(world, gs, whole=("p1b1",))).status_code == 201
    assert submit(client, login, gs["tutor"], occ, gs["bio"], gs["paperBio"],
                  aud(world, gs, provider="p2", branches=("p2b1",), students=("STU000004", "STU000001"))).status_code == 201
    return t, occ


def exams_by_student(db_factory, occurrence_id):
    """{student: [(exam_id, [(subject_id, paper_version_id, source provider), ...])]} for one occurrence."""
    from institute_common.models.exams import Exam, ExamPlan, ExamVersion, ExamVersionPaper, GlobalPaperSubmission, StudentExamAssignment
    out: dict = {}
    with db_factory() as db:
        for plan in db.query(ExamPlan).filter_by(global_occurrence_id=UUID(str(occurrence_id))):
            for exam in db.query(Exam).filter_by(plan_id=plan.plan_id):
                ev = db.query(ExamVersion).filter_by(exam_id=exam.exam_id).one()
                papers = []
                for x in db.query(ExamVersionPaper).filter_by(exam_version_id=ev.exam_version_id).order_by(ExamVersionPaper.position):
                    sub = db.get(GlobalPaperSubmission, x.source_submission_id)
                    assert sub.provider_id == x.source_provider_id and sub.paper_version_id == x.paper_version_id   # provenance kept
                    papers.append((x.subject_id, str(x.paper_version_id), sub.provider_id))
                for a in db.query(StudentExamAssignment).filter_by(exam_id=exam.exam_id):
                    out.setdefault(a.student_id, []).append((str(exam.exam_id), papers))
    return out


def counts(db_factory):
    from institute_common.models.exams import (Exam, ExamGenerationJob, ExamNotificationOutbox, ExamOutbox, ExamPlan, StudentExamAssignment,
                                               StudentNotificationOutbox)
    with db_factory() as db:
        return {m.__name__: db.query(m).count() for m in (ExamPlan, ExamGenerationJob, Exam, StudentExamAssignment, ExamOutbox,
                                                          ExamNotificationOutbox, StudentNotificationOutbox)}


def deliver(db_factory):
    """Run the real outbox with a capturing publisher; returns the payloads sent to the Student test service."""
    from inst_exam.services import outbox
    sent = []

    class Capture:
        def send(self, payload):
            sent.append(payload)
    with db_factory() as db:
        outbox.deliver_due(db, publisher=Capture())
    return sent


# ------------------------------------------------------------------ generation: one exam, the right papers, nothing twice

def test_each_student_gets_one_unified_exam_with_all_and_only_their_authorized_papers(client, world, gs, uni, db_factory):
    t, occ = uni
    [run] = run_generation(db_factory, t, occ)
    assert not run["failed"] and run["students"] == 4 and run["conflicts"] == 0 and run["without_paper"] == 1, run
    got = exams_by_student(db_factory, occ["occurrence_id"])
    p1, p2 = world["p1"], world["p2"]
    # the multi-provider student: ONE exam, each subject from the provider it is taught by, in the test's subject order
    assert len(got["STU000001"]) == 1
    assert got["STU000001"][0][1] == [(gs["phy"], gs["paperA"], p1), (gs["chem"], gs["paperC"], p1), (gs["bio"], gs["paperBio"], p2)]
    assert [x[1] for x in got["STU000002"]] == [[(gs["chem"], gs["paperC"], p1)]]            # batch B: no Physics paper was submitted
    assert [x[1] for x in got["STU000004"]] == [[(gs["bio"], gs["paperBio"], p2)]]            # single-provider student: one exam too
    assert [x[1] for x in got["STU000003"]] == [[]]            # batch C: no paper at all, still ONE card (that cannot be started)
    assert len({e for rows in got.values() for e, _ in rows}) == 4                           # one exam per distinct layout

    sent = deliver(db_factory)
    per_student = {}
    for payload in sent:
        assert payload["global_occurrence_id"] == occ["occurrence_id"]                       # the Student side shows it as the Global Test
        for sid in payload["student_ids"]:
            per_student.setdefault(sid, []).append(payload)
    assert {k: len(v) for k, v in per_student.items()} == {"STU000001": 1, "STU000002": 1, "STU000003": 1, "STU000004": 1}   # one card each
    sec = {sid: [(x["position"], x["subject"], x["status"], x["question_count"]) for x in v[0]["sections"]] for sid, v in per_student.items()}
    assert sec == {                                          # every configured subject, in the configured order, with its outcome
        "STU000001": [(1, "Physics", "PAPER", 3), (2, "Chemistry", "PAPER", 2), (3, "Biology", "PAPER", 2)],
        "STU000002": [(1, "Physics", "NOT_COVERED", 0), (2, "Chemistry", "PAPER", 2), (3, "Biology", "NOT_ENROLLED", 0)],
        "STU000003": [(1, "Physics", "NOT_COVERED", 0), (2, "Chemistry", "NOT_COVERED", 0), (3, "Biology", "NOT_ENROLLED", 0)],
        # STU000004 is enrolled with the tutor (p2) in no batch: an enrollment without an active batch membership takes no subject,
        # so Physics is "not enrolled"; Biology is a paper because the tutor listed the student individually (a staff override)
        "STU000004": [(1, "Physics", "NOT_ENROLLED", 0), (2, "Chemistry", "NOT_ENROLLED", 0), (3, "Biology", "PAPER", 2)]}, sec
    assert all((x["message"] is None) == (x["status"] == "PAPER") for v in per_student.values() for x in v[0]["sections"])
    assert "Biology" in per_student["STU000002"][0]["sections"][2]["message"] and per_student["STU000003"][0]["questions"] == []
    mine = per_student["STU000001"][0]
    assert [q["subject"] for q in mine["questions"]] == ["Physics"] * 3 + ["Chemistry"] * 2 + ["Biology"] * 2   # grouped by subject
    assert mine["duration_minutes"] == 60 and {(q["marks"], q["negative_marks"]) for q in mine["questions"]} == {(4, 1)}   # one timer, test marking
    assert {p["name"] for p in sent} == {"National Science Mock"}                          # the Global Test's own name, no date suffix


def test_retries_create_no_duplicate_exams_assignments_notifications_or_deliveries(client, world, gs, uni, db_factory):
    from institute_common.models.exams import ExamPlan, GlobalTestOccurrence
    from inst_exam.services import exam_generation as gen
    t, occ = uni
    run_generation(db_factory, t, occ)
    deliver(db_factory)
    before, got = counts(db_factory), exams_by_student(db_factory, occ["occurrence_id"])
    assert run_generation(db_factory, t, occ) == []                                          # generated once
    with db_factory() as db:                                                                 # a forced re-run of the whole date ...
        o = db.get(GlobalTestOccurrence, UUID(occ["occurrence_id"]))
        gg.generate_occurrence(db, o)
        for plan in db.query(ExamPlan).filter_by(global_occurrence_id=o.occurrence_id):      # ... and of every plan
            assert gen.generate(db, plan.plan_id).status == "SUCCEEDED"
    assert deliver(db_factory) == []
    assert counts(db_factory) == before and exams_by_student(db_factory, occ["occurrence_id"]) == got


def test_a_student_never_gets_a_second_exam_for_the_same_date(client, world, gs, uni, db_factory):
    """Even if a second plan of the same date lists the student (e.g. a concurrent or repaired run), they keep exactly one."""
    from institute_common.models.exams import ExamPlan, ExamPlanStudent, ExamPlanSubject
    from inst_exam.services import exam_generation as gen
    t, occ = uni
    run_generation(db_factory, t, occ)
    with db_factory() as db:
        src = db.query(ExamPlan).join(ExamPlanStudent, ExamPlanStudent.plan_id == ExamPlan.plan_id).filter(
            ExamPlan.global_occurrence_id == UUID(occ["occurrence_id"]), ExamPlanStudent.student_id == "STU000004").one()
        slot = db.query(ExamPlanSubject).filter_by(plan_id=src.plan_id).one()
        twin = ExamPlan(**{c: getattr(src, c) for c in ("provider_id", "branch_id", "name", "duration_minutes", "start_at", "end_at", "auto_publish",
                                                       "status", "audience_type", "marking_mode", "marks_correct", "marks_wrong", "exam_type",
                                                       "target_class", "global_test_id", "global_occurrence_id", "created_by")},
                        client_request_id="gocc2:twin-for-test")
        db.add(twin)
        db.flush()
        db.add(ExamPlanStudent(plan_id=twin.plan_id, student_id="STU000004"))
        db.add(ExamPlanSubject(plan_id=twin.plan_id, position=1, subject_id=slot.subject_id, paper_version_id=slot.paper_version_id,
                               source_submission_id=slot.source_submission_id))
        db.commit()
        gen.generate(db, twin.plan_id)
    assert len(exams_by_student(db_factory, occ["occurrence_id"])["STU000004"]) == 1


def test_unapproved_paper_is_excluded_and_its_provider_told_while_other_subjects_continue(client, world, gs, login, db_factory):  # noqa: F811
    from institute_common.models.academics import ProviderSubject
    from institute_common.models.exams import ExamNotificationOutbox
    with db_factory() as db:
        db.add(ProviderSubject(provider_id=world["p2"], subject_id=gs["bio"]))
        db.commit()
    _pid, draft = make_paper(db_factory, world["p2"], world["p2b1"], gs["bio"], "bio draft", "VALIDATED", n=2)
    t, occ = published(client, login, gs, subject_ids=[str(gs["phy"]), str(gs["chem"]), str(gs["bio"])])
    submit(client, login, gs["tA"], occ, gs["phy"], gs["paperA"], aud(world, gs, batches=("batch_a",)))
    submit(client, login, gs["tutor"], occ, gs["bio"], str(draft), aud(world, gs, provider="p2", branches=("p2b1",), students=("STU000001",)))
    [run] = run_generation(db_factory, t, occ)
    got = exams_by_student(db_factory, occ["occurrence_id"])
    assert [x[1] for x in got["STU000001"]] == [[(gs["phy"], gs["paperA"], world["p1"])]]    # Biology left out, never substituted
    with db_factory() as db:
        bodies = [o.payload["body"] for o in db.query(ExamNotificationOutbox) if o.payload["provider_id"] == str(world["p2"])]
    assert any("not approved by the deadline" in b for b in bodies), (run, bodies)


def test_cross_provider_conflict_excludes_the_subject_and_tells_every_provider(client, world, gs, login, db_factory):  # noqa: F811
    from institute_common.models.exams import ExamNotificationOutbox
    t, occ = published(client, login, gs)
    submit(client, login, gs["tA"], occ, gs["phy"], gs["paperA"], aud(world, gs, batches=("batch_a",)))
    submit(client, login, gs["tC"], occ, gs["chem"], gs["paperC"], aud(world, gs, whole=("p1b1",)))
    submit(client, login, gs["tutor"], occ, gs["phy"], gs["paperT"], aud(world, gs, provider="p2", branches=("p2b1",), students=("STU000001",)))
    [run] = run_generation(db_factory, t, occ)
    assert run["conflicts"] == 1
    got = exams_by_student(db_factory, occ["occurrence_id"])
    assert [x[1] for x in got["STU000001"]] == [[(gs["chem"], gs["paperC"], world["p1"])]]   # Physics from neither provider; Chemistry kept
    with db_factory() as db:
        told = {o.payload["provider_id"] for o in db.query(ExamNotificationOutbox) if "another provider's paper" in o.payload["body"]}
    assert told == {str(world["p1"]), str(world["p2"])}


# ------------------------------------------------------------------ isolation: the anchor grants nothing, providers see only their part

def _exam_of(db_factory, occ, student):
    [(exam_id, _papers)] = exams_by_student(db_factory, occ["occurrence_id"])[student]
    return exam_id


class SubjectResults:
    """Fake Student result service: overall rows and per-subject rows for STU000001's exam."""
    def __init__(self):
        self.asked = []

    def exam_results(self, test_id):
        self.asked.append(("overall", test_id))
        return [{"student_id": "STU000001", "obtained_marks": 21, "total_marks": 28, "accuracy": 80.0, "submitted_at": "2026-01-01T00:00:00"}]

    def exam_subject_results(self, test_id):
        self.asked.append(("subjects", test_id))
        row = lambda name, obt, tot, att, cor: {"subject": name, "obtained_marks": obt, "total_marks": tot, "attempted_questions": att,  # noqa: E731
                                                "correct_answers": cor, "incorrect_answers": att - cor, "unanswered_questions": 0, "total_questions": att}
        return [{"student_id": "STU000001", "submitted_at": "2026-01-01T00:00:00",
                 "subjects": [row("Physics", 7, 12, 3, 2), row("Chemistry", 8, 8, 2, 2), row("Biology", 6, 8, 2, 2)]}]


def test_each_provider_sees_only_its_own_subjects_students_and_results(client, world, gs, uni, login, db_factory):
    from institute_common.clients.result_client import set_result_client
    t, occ = uni
    run_generation(db_factory, t, occ)
    exam_id = _exam_of(db_factory, occ, "STU000001")

    p1_view = client.get(f"{API}/exams/{exam_id}", headers=login(gs["pa"])).json()
    assert p1_view["unified_global"] is True
    assert {x["subject_id"] for x in p1_view["papers"]} == {str(gs["phy"]), str(gs["chem"])}           # never p2's Biology
    assert p1_view["total_questions"] == 5 and p1_view["assigned_students"] == 1
    p2_view = client.get(f"{API}/exams/{exam_id}", headers=login(gs["tutor"])).json()
    assert [x["subject_id"] for x in p2_view["papers"]] == [str(gs["bio"])] and p2_view["total_questions"] == 2
    full = client.get(f"{API}/exams/{exam_id}", headers=login(gs["sa"])).json()
    assert len(full["papers"]) == 3 and full["total_questions"] == 7 and full["visible_subject_ids"] is None

    fake = SubjectResults()
    set_result_client(fake)
    try:
        r1 = client.get(f"{API}/exams/{exam_id}/results", headers=login(gs["pa"])).json()
        assert r1["subject_view"] is True and r1["visible_subjects"] == ["Chemistry", "Physics"]
        [row] = r1["results"]
        assert row["obtained_marks"] == 15 and row["total_marks"] == 20 and {s["subject"] for s in row["subjects"]} == {"Physics", "Chemistry"}
        assert r1["total_marks"] == "20.00" and 21 not in (row["obtained_marks"], r1["summary"]["highest"])   # never the overall score
        r2 = client.get(f"{API}/exams/{exam_id}/results", headers=login(gs["tutor"])).json()
        assert [s["subject"] for s in r2["results"][0]["subjects"]] == ["Biology"] and r2["results"][0]["obtained_marks"] == 6
        rs = client.get(f"{API}/exams/{exam_id}/results", headers=login(gs["sa"])).json()
        assert rs["subject_view"] is False and rs["results"][0]["obtained_marks"] == 21                    # platform: the whole result
        # teachers: only their subject, only their students
        rc = client.get(f"{API}/exams/{exam_id}/results", headers=login(gs["tC"])).json()
        assert [s["subject"] for s in rc["results"][0]["subjects"]] == ["Chemistry"]
        ra = client.get(f"{API}/exams/{exam_id}/results", headers=login(gs["tA"])).json()
        assert [s["subject"] for s in ra["results"][0]["subjects"]] == ["Physics"]
    finally:
        set_result_client(None)


def test_the_anchor_and_unrelated_staff_get_no_control_and_no_access(client, world, gs, uni, login, db_factory, make_staff):
    from institute_common.models.exams import Exam
    t, occ = uni
    run_generation(db_factory, t, occ)
    exam_id = _exam_of(db_factory, occ, "STU000001")
    with db_factory() as db:
        plan_id = db.get(Exam, UUID(exam_id)).plan_id
    for who in ("pa", "tutor", "ba"):                         # p1 (the anchor's provider), p2, the anchor branch's admin
        h = login(gs[who])
        assert client.get(f"{API}/exam-plans/{plan_id}", headers=h).status_code == 403
        assert client.post(f"{API}/exam-plans/{plan_id}/generate", json={}, headers=h).status_code == 403
        assert client.get(f"{API}/exam-plans/{plan_id}/jobs", headers=h).status_code == 403
        assert client.post(f"{API}/exams/{exam_id}/cancel", json={"reason": "not mine to cancel"}, headers=h).status_code == 403
        assert client.post(f"{API}/exams/{exam_id}/sync", headers=h).status_code == 403
        assert client.get(f"{API}/exams/{exam_id}/result-corrections", headers=h).status_code == 403
    assert client.get(f"{API}/exam-plans/{plan_id}", headers=login(gs["sa"])).status_code == 200
    assert client.get(f"{API}/exams/{exam_id}/result-corrections", headers=login(gs["sa"])).status_code == 200
    # p1 taught nobody Biology here and b2's admin has none of the exam's students: no access at all
    _, b2_admin = make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b2"])])
    for h in (login(gs["tBio"]), login(b2_admin)):
        assert client.get(f"{API}/exams/{exam_id}", headers=h).status_code == 403
        assert client.get(f"{API}/exams/{exam_id}/assignments", headers=h).status_code == 403
        assert client.get(f"{API}/exams/{exam_id}/results", headers=h).status_code == 403


def test_listings_show_unified_exams_by_contribution_never_by_anchor(client, world, gs, uni, login, db_factory, make_staff):
    t, occ = uni
    run_generation(db_factory, t, occ)
    exam_id = _exam_of(db_factory, occ, "STU000001")
    p1_list = client.get(f"{API}/branches/{world['p1b1']}/exams", headers=login(gs["pa"])).json()["items"]
    mine = [x for x in p1_list if x["exam_id"] == exam_id]
    assert len(mine) == 1 and {p["subject_id"] for p in mine[0]["papers"]} == {str(gs["phy"]), str(gs["chem"])}
    p2_list = client.get(f"{API}/branches/{world['p2b1']}/exams", headers=login(gs["tutor"])).json()["items"]
    assert [p["subject_id"] for x in p2_list if x["exam_id"] == exam_id for p in x["papers"]] == [str(gs["bio"])]
    assert client.get(f"{API}/branches/{world['p1b2']}/exams", headers=login(gs["pa"])).json()["items"] == []   # no student of b2 took it
    # plans are the platform's: never in a provider's plan list or Scheduled Tests list
    assert all(not x["global_test_id"] for x in client.get(f"{API}/branches/{world['p1b1']}/exam-plans", headers=login(gs["pa"])).json()["items"])
    assert [x for x in client.get(f"{API}/tests?test_type=GLOBAL", headers=login(gs["pa"])).json()["items"]] == []
    plans = client.get(f"{API}/tests?test_type=GLOBAL", headers=login(gs["sa"])).json()["items"]
    assert len(plans) == 4 and {(x["unified_global"], x["provider_name"], x["home_branch"]) for x in plans} == {(True, "Global Test", None)}
    # the date's page: each provider sees the exams with its students, counted for its students only
    occ_p2 = client.get(f"{API}/global-occurrences/{occ['occurrence_id']}", headers=login(gs["tutor"])).json()
    assert sorted(e["assigned"] for e in occ_p2["exams"]) == [1, 1]                         # STU000001's and STU000004's exams


def test_existing_per_provider_exams_keep_their_rules(client, world, gs, login, db_factory):  # noqa: F811
    """Exams generated before unified generation (no source_submission_id) are not unified: same access, same results as before."""
    from institute_common.models.exams import ExamPlan, ExamPlanStudent, ExamPlanSubject, GlobalTestOccurrence
    from inst_exam.services import exam_generation as gen
    t, occ = published(client, login, gs)
    with db_factory() as db:
        o = db.get(GlobalTestOccurrence, UUID(occ["occurrence_id"]))
        plan = ExamPlan(provider_id=world["p1"], branch_id=world["p1b1"], name="Legacy", duration_minutes=60, start_at=o.start_at, end_at=o.end_at,
                        auto_publish=True, status="ACTIVE", audience_type="STUDENTS", marking_mode="UNIFORM", marks_correct=4, marks_wrong=1,
                        global_test_id=UUID(t["test_id"]), global_occurrence_id=o.occurrence_id, client_request_id="gocc:legacy-test", created_by=o.created_by)
        db.add(plan)
        db.flush()
        db.add(ExamPlanStudent(plan_id=plan.plan_id, student_id="STU000001"))
        db.add(ExamPlanSubject(plan_id=plan.plan_id, position=1, subject_id=gs["phy"], paper_version_id=UUID(gs["paperA"])))
        o.generated_at = o.start_at                                                       # generated earlier: never regenerated
        db.commit()
        exam_id = gen.generate(db, plan.plan_id).exam_id
    before = counts(db_factory)
    assert run_generation(db_factory, t, occ) == [] and counts(db_factory) == before
    view = client.get(f"{API}/exams/{exam_id}", headers=login(gs["pa"])).json()
    assert view["unified_global"] is False and view["visible_subject_ids"] is None and view["assigned_students"] == 1
    assert client.get(f"{API}/exam-plans/{plan.plan_id}", headers=login(gs["pa"])).status_code == 200   # its provider still manages it


def test_provider_views_never_name_the_anchor(client, world, gs, uni, login, db_factory):
    """M2: the anchor plan/provider/branch of a unified exam may be another provider's, so a provider's view never carries them, nor
    whole-exam counts; the platform keeps them; an ordinary exam keeps its identifiers for its own provider."""
    from institute_common.models.exams import Exam
    t, occ = uni
    run_generation(db_factory, t, occ)
    exam_id = _exam_of(db_factory, occ, "STU000001")
    with db_factory() as db:
        e = db.get(Exam, UUID(exam_id))
        anchor = {"plan_id": str(e.plan_id), "provider_id": str(e.provider_id), "branch_id": str(e.branch_id)}
    assert anchor["provider_id"] == str(world["p1"])                                   # Physics comes first: p1 is the anchor
    for who in ("tutor", "pa", "tC"):                                                    # another provider, the anchor's own provider, a teacher
        view = client.get(f"{API}/exams/{exam_id}", headers=login(gs[who])).json()
        assert (view["plan_id"], view["provider_id"], view["branch_id"]) == (None, None, None), (who, view)
        assert "skipped_duplicates" not in (view["settings"] or {})
        assert not set(anchor.values()) & {str(v) for v in view.values() if not isinstance(v, (dict, list))}, who
    listed = [x for x in client.get(f"{API}/branches/{world['p2b1']}/exams", headers=login(gs["tutor"])).json()["items"] if x["exam_id"] == exam_id]
    assert listed and (listed[0]["plan_id"], listed[0]["provider_id"], listed[0]["branch_id"]) == (None, None, None)
    full = client.get(f"{API}/exams/{exam_id}", headers=login(gs["sa"])).json()
    assert {k: full[k] for k in anchor} == anchor and "skipped_duplicates" in full["settings"]     # the platform keeps them
    assert client.get(f"{API}/exams/{exam_id}", headers=login(gs["tBio"])).status_code == 403    # still denied without a contribution


def test_a_providers_totals_follow_the_tests_uniform_marking(client, world, gs, login, db_factory):  # noqa: F811
    """The papers carry 4 marks a question, the Global Test gives +5/-2: a provider's subject totals are what was delivered (5 a question,
    like the generator's exam total), never the papers' own totals."""
    from institute_common.clients.result_client import set_result_client
    t, occ = published(client, login, gs, marks_correct=5, marks_wrong=2)
    submit(client, login, gs["tA"], occ, gs["phy"], gs["paperA"], aud(world, gs, batches=("batch_a",)))      # 3 questions
    submit(client, login, gs["tC"], occ, gs["chem"], gs["paperC"], aud(world, gs, whole=("p1b1",)))           # 2 questions
    run_generation(db_factory, t, occ)
    exam_id = _exam_of(db_factory, occ, "STU000001")
    full = client.get(f"{API}/exams/{exam_id}", headers=login(gs["sa"])).json()
    tc = client.get(f"{API}/exams/{exam_id}", headers=login(gs["tC"])).json()                                 # Chemistry teacher: one subject
    assert Decimal(full["total_marks"]) == 25 and tc["total_questions"] == 2 and Decimal(tc["total_marks"]) == 10, (full, tc)

    class Rows:
        def exam_results(self, test_id):
            return []

        def exam_subject_results(self, test_id):
            return [{"student_id": "STU000001", "submitted_at": "2026-01-01T00:00:00", "subjects": [
                {"subject": "Chemistry", "obtained_marks": 3, "total_marks": 10, "attempted_questions": 2, "correct_answers": 1,
                 "incorrect_answers": 1, "unanswered_questions": 0, "total_questions": 2}]}]
    set_result_client(Rows())
    try:
        r = client.get(f"{API}/exams/{exam_id}/results", headers=login(gs["tC"])).json()
        assert Decimal(r["total_marks"]) == 10 and r["results"][0]["total_marks"] == 10, r
    finally:
        set_result_client(None)


def test_dashboards_never_count_a_unified_exam_for_its_anchor(client, world, gs, uni, login, db_factory):
    """Operations dashboard (inst_operations/routes/ops.py): a provider's upcoming-exam count leaves out unified Global Test exams (the
    anchor does not own them); the platform's upcoming list shows them as Global Tests, never under the anchor provider/branch."""
    from datetime import timedelta
    from uuid import uuid4
    from institute_common.database import utcnow
    from institute_common.models.exams import Exam, ExamPlan, ExamVersion
    t, occ = uni
    run_generation(db_factory, t, occ)                                                   # 3 unified exams: 2 anchored on p1, 1 on p2
    start = utcnow() + timedelta(days=3)
    with db_factory() as db:                                                             # control: one ordinary upcoming p1 exam
        plan = ExamPlan(provider_id=world["p1"], branch_id=world["p1b1"], name="Weekly", duration_minutes=60, start_at=start,
                        end_at=start + timedelta(hours=2), created_by=uuid4())
        db.add(plan)
        db.flush()
        e = Exam(plan_id=plan.plan_id, run_key="ordinary", provider_id=world["p1"], branch_id=world["p1b1"], name="Ordinary weekly", status="PUBLISHED")
        db.add(e)
        db.flush()
        db.add(ExamVersion(exam_id=e.exam_id, version_no=1, duration_minutes=60, start_at=start, end_at=start + timedelta(hours=2),
                           total_questions=10, total_marks=40))
        db.commit()
        unified_ids = {str(x.exam_id) for x in db.query(Exam).join(ExamPlan, ExamPlan.plan_id == Exam.plan_id)
                       .filter(ExamPlan.global_occurrence_id == UUID(occ["occurrence_id"]))}
    assert len(unified_ids) == 4
    assert client.get(f"{API}/dashboard", headers=login(gs["pa"])).json()["upcoming_exams"] == 1      # only the ordinary exam
    assert client.get(f"{API}/dashboard", headers=login(gs["tutor"])).json()["upcoming_exams"] == 0   # p2 anchors one unified exam
    d = client.get(f"{API}/dashboard", headers=login(gs["sa"])).json()
    assert d["upcoming_exams"] == 5                                                      # the platform counts every exam
    rows = {x["exam_id"]: x for x in d["upcoming_exam_list"]}
    assert set(rows) == unified_ids | {str(e.exam_id)}
    for exam_id in unified_ids:
        assert rows[exam_id]["unified_global"] is True and rows[exam_id]["provider_name"] is None and rows[exam_id]["branch_name"] is None
    assert rows[str(e.exam_id)]["unified_global"] is False and rows[str(e.exam_id)]["provider_name"] == "Provider p1"
