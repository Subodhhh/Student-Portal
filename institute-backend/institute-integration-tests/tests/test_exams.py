from datetime import timedelta
from decimal import Decimal

import pytest

from institute_common.core.roles import Role
from institute_common.database import utcnow
from inst_exam.services import exam_generation as gen
from inst_exam.services import outbox
from inst_exam.services.outbox import PermanentPublishError, TransientPublishError

API = "/api/v1"


@pytest.fixture()
def ctx(client, world, make_staff, login, db_factory):
    """Provider p1/branch b1 with Physics+Chem enabled, two batches, 3 enrolled students,
    a published Physics paper, an *unpublished* Chem paper, plus a teacher per subject."""
    from institute_common.models.academics import ProviderSubject, Subject, TeacherSubjectAssignment
    from institute_common.models.org import Batch, Enrollment, EnrollmentBatch
    c = {}
    with db_factory() as db:
        phy, chem = Subject(code="PHYSICS", name="Physics"), Subject(code="CHEM", name="Chemistry")
        db.add_all([phy, chem])
        db.flush()
        c["phy"], c["chem"] = phy.subject_id, chem.subject_id
        for prov in ("p1", "p2"):
            for s in (phy, chem):
                db.add(ProviderSubject(provider_id=world[prov], subject_id=s.subject_id))
        in_batch, other_batch = Batch(branch_id=world["p1b1"], name="NEET A"), Batch(branch_id=world["p1b1"], name="Other")
        db.add_all([in_batch, other_batch])
        db.flush()
        c["batch"], c["other_batch"] = in_batch.batch_id, other_batch.batch_id
        for sid, batch, status in (("STU000001", in_batch, "ACTIVE"), ("STU000002", in_batch, "ACTIVE"),
                                   ("STU000003", other_batch, "ACTIVE"), ("STU000004", in_batch, "INACTIVE")):
            e = Enrollment(enrollment_id=f"enr-{sid}", provider_id=world["p1"], branch_id=world["p1b1"], student_id=sid, status=status)
            db.add(e)
            db.flush()
            db.add(EnrollmentBatch(enrollment_id=e.enrollment_id, batch_id=batch.batch_id))
        db.commit()
    t_phy, c["tphy"] = make_staff([(Role.TEACHER, world["p1"], None)])
    t_chem, c["tchem"] = make_staff([(Role.TEACHER, world["p1"], None)])
    with db_factory() as db:
        db.add(TeacherSubjectAssignment(staff_id=t_phy, provider_id=world["p1"], branch_id=world["p1b1"], subject_id=c["phy"]))
        db.add(TeacherSubjectAssignment(staff_id=t_chem, provider_id=world["p1"], branch_id=world["p1b1"], subject_id=c["chem"]))
        db.commit()
    c["t_phy_id"], c["t_chem_id"] = t_phy, t_chem
    c["phy_paper"], c["phy_v1"] = make_paper(db_factory, world["p1"], world["p1b1"], c["phy"], "Physics Mock", "PUBLISHED")
    c["chem_paper"], c["chem_v1"] = make_paper(db_factory, world["p1"], world["p1b1"], c["chem"], "Chem Mock", "VALIDATED")
    return c


def make_paper(db_factory, provider, branch, subject, title, status, n=3, marks="4", neg="1", paper_id=None, version_no=1):
    from institute_common.models.papers import Paper, PaperVersion, PaperVersionQuestion, Question, QuestionVersion
    from uuid import uuid4
    import hashlib
    with db_factory() as db:
        paper = db.get(Paper, paper_id) if paper_id else Paper(provider_id=provider, branch_id=branch, subject_id=subject, title=title, created_by=uuid4())
        if not paper_id:
            db.add(paper)
            db.flush()
        pv = PaperVersion(paper_id=paper.paper_id, version_no=version_no, status=status, question_count=n,
                          total_marks=Decimal(marks) * n, content_hash=hashlib.sha256(f"{title}{version_no}{uuid4()}".encode()).hexdigest(),
                          created_by=uuid4())
        db.add(pv)
        db.flush()
        for i in range(1, n + 1):
            q = Question(provider_id=provider, branch_id=branch, subject_id=subject, owner_staff_id=uuid4())
            db.add(q)
            db.flush()
            qv = QuestionVersion(question_id=q.question_id, version_no=1, stem=f"{title} Q{i}", option_a="a", option_b="b", option_c="c",
                                 option_d="d", correct_option="A", marks_positive=Decimal(marks), marks_negative=Decimal(neg), created_by=uuid4())
            db.add(qv)
            db.flush()
            db.add(PaperVersionQuestion(paper_version_id=pv.paper_version_id, position=i, question_version_id=qv.question_version_id))
        db.commit()
        return paper.paper_id, pv.paper_version_id


def plan_body(ctx, **over):
    now = utcnow()
    body = {"name": "Weekly Test", "duration_minutes": 180, "start_at": (now + timedelta(days=2)).isoformat(),
            "end_at": (now + timedelta(days=2, hours=6)).isoformat(), "auto_publish": True,
            "batch_ids": [str(ctx["batch"])],
            "slots": [{"subject_id": str(ctx["phy"]), "paper_id": str(ctx["phy_paper"])},
                      {"subject_id": str(ctx["chem"]), "paper_id": str(ctx["chem_paper"])}]}
    body.update(over)
    return body


@pytest.fixture()
def pa(make_staff, world):
    return make_staff([(Role.PROVIDER_ADMIN, world["p1"], None)])[1]


def create_plan(client, h, world, ctx, **over):
    r = client.post(f"{API}/branches/{world['p1b1']}/exam-plans", json=plan_body(ctx, **over), headers=h)
    assert r.status_code == 201, r.text
    return r.json()


# ------------------------------------------------------------ plans

def test_plan_validation_and_authorization(client, world, ctx, pa, make_staff, login):
    h = login(pa)
    assert client.post(f"{API}/branches/{world['p1b1']}/exam-plans", json=plan_body(ctx, duration_minutes=0), headers=h).status_code == 422
    now = utcnow()
    assert client.post(f"{API}/branches/{world['p1b1']}/exam-plans", json=plan_body(ctx, end_at=(now - timedelta(days=1)).isoformat()), headers=h).status_code == 422
    dup = plan_body(ctx, slots=[{"subject_id": str(ctx["phy"]), "paper_id": str(ctx["phy_paper"])}, {"subject_id": str(ctx["phy"]), "paper_id": str(ctx["phy_paper"])}])
    assert client.post(f"{API}/branches/{world['p1b1']}/exam-plans", json=dup, headers=h).status_code == 422
    both = plan_body(ctx, slots=[{"subject_id": str(ctx["phy"]), "paper_id": str(ctx["phy_paper"]), "paper_version_id": str(ctx["phy_v1"])}])
    assert client.post(f"{API}/branches/{world['p1b1']}/exam-plans", json=both, headers=h).status_code == 422
    other = plan_body(ctx, batch_ids=[str(ctx["batch"]), "00000000-0000-0000-0000-000000000001"])
    assert client.post(f"{API}/branches/{world['p1b1']}/exam-plans", json=other, headers=h).status_code == 422
    create_plan(client, h, world, ctx)
    # teachers cannot manage plans; another provider's admin cannot see them
    assert client.post(f"{API}/branches/{world['p1b1']}/exam-plans", json=plan_body(ctx), headers=login(ctx["tphy"])).status_code == 403
    _, pa2 = make_staff([(Role.PROVIDER_ADMIN, world["p2"], None)])
    assert client.get(f"{API}/branches/{world['p1b1']}/exam-plans", headers=login(pa2)).status_code == 403


def test_plan_cannot_reference_unshared_foreign_paper(client, world, ctx, db_factory, make_staff, login):
    p2_paper, _ = make_paper(db_factory, world["p2"], world["p2b1"], ctx["phy"], "Foreign", "PUBLISHED")
    h = login(make_staff([(Role.PROVIDER_ADMIN, world["p1"], None)])[1])
    body = plan_body(ctx, slots=[{"subject_id": str(ctx["phy"]), "paper_id": str(p2_paper)}])
    r = client.post(f"{API}/branches/{world['p1b1']}/exam-plans", json=body, headers=h)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "PAPER_NOT_AVAILABLE"


# ------------------------------------------------------------ generation

def test_generation_reports_missing_subject_and_notifies(client, world, ctx, pa, login, outbox, db_factory):
    from institute_common.models.exams import Notification
    h = login(pa)
    plan = create_plan(client, h, world, ctx)
    r = client.post(f"{API}/exam-plans/{plan['plan_id']}/generate", json={}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "PARTIAL"
    exam = client.get(f"{API}/exams/{r.json()['exam_id']}", headers=h).json()
    assert exam["status"] == "PUBLISHED" and exam["total_questions"] == 3          # Physics only
    assert [m["reason"] for m in exam["missing_subjects"]] == ["NO_PUBLISHED_VERSION"] or exam["missing_subjects"][0]["reason"] == "NO_PUBLISHED_VERSION"
    assert exam["missing_subjects"][0]["subject"] == "Chemistry"
    assert exam["assigned_students"] == 2 and exam["delivery_status"] == "PENDING"
    with db_factory() as db:
        kinds = [(n.kind, n.recipient_staff_id) for n in db.query(Notification)]
    assert any(k == "MISSING_PAPER" and rid == ctx["t_chem_id"] for k, rid in kinds)    # the chemistry teacher is told
    assert any(k == "MISSING_PAPER" for k, _ in kinds)
    assert not any(rid == ctx["t_phy_id"] for k, rid in kinds)                          # physics teacher is not spammed


def test_eligibility_uses_active_enrollment_and_plan_batches(client, world, ctx, pa, login):
    h = login(pa)
    plan = create_plan(client, h, world, ctx)
    job = client.post(f"{API}/exam-plans/{plan['plan_id']}/generate", json={}, headers=h).json()
    a = client.get(f"{API}/exams/{job['exam_id']}/assignments", headers=h).json()
    assert sorted(i["student_id"] for i in a["items"]) == ["STU000001", "STU000002"]    # not 3 (other batch) or 4 (inactive)


def test_generation_is_idempotent_and_does_not_duplicate_anything(client, world, ctx, pa, login, db_factory):
    from institute_common.models.exams import Exam, ExamGenerationJob, Notification, StudentExamAssignment, ExamOutbox
    h = login(pa)
    plan = create_plan(client, h, world, ctx)
    first = client.post(f"{API}/exam-plans/{plan['plan_id']}/generate", json={}, headers={**h, "Idempotency-Key": "run-1"}).json()
    again = client.post(f"{API}/exam-plans/{plan['plan_id']}/generate", json={}, headers={**h, "Idempotency-Key": "run-1"}).json()
    assert first["job_id"] == again["job_id"] and first["exam_id"] == again["exam_id"]
    with db_factory() as db:
        assert db.query(Exam).count() == 1 and db.query(ExamGenerationJob).count() == 1
        assert db.query(StudentExamAssignment).count() == 2 and db.query(ExamOutbox).count() == 1
        n = db.query(Notification).count()
    client.post(f"{API}/exam-plans/{plan['plan_id']}/generate", json={}, headers={**h, "Idempotency-Key": "run-1"})
    with db_factory() as db:
        assert db.query(Notification).count() == n


def test_late_approved_paper_applies_only_to_next_exam(client, world, ctx, pa, login, db_factory):
    h = login(pa)
    plan = create_plan(client, h, world, ctx)
    first = client.post(f"{API}/exam-plans/{plan['plan_id']}/generate", json={}, headers={**h, "Idempotency-Key": "week-1"}).json()
    with db_factory() as db:                                    # chemistry is approved + published afterwards
        from institute_common.models.papers import PaperVersion
        pv = db.get(PaperVersion, ctx["chem_v1"])
        pv.status = "PUBLISHED"
        db.commit()
    unchanged = client.get(f"{API}/exams/{first['exam_id']}", headers=h).json()
    assert len(unchanged["papers"]) == 1 and unchanged["total_questions"] == 3              # scheduled exam untouched
    # the NEXT exam is the next window; the same window would return the same exam (one exam per plan and start)
    nxt_start = utcnow() + timedelta(days=9)
    second = client.post(f"{API}/exam-plans/{plan['plan_id']}/generate", headers={**h, "Idempotency-Key": "week-2"},
                         json={"start_at": nxt_start.isoformat(), "end_at": (nxt_start + timedelta(hours=3)).isoformat()}).json()
    assert second["status"] == "SUCCEEDED"
    nxt = client.get(f"{API}/exams/{second['exam_id']}", headers=h).json()
    assert len(nxt["papers"]) == 2 and nxt["total_questions"] == 6 and nxt["missing_subjects"] == []


def test_explicit_version_is_used_exactly_even_if_newer_published(client, world, ctx, pa, login, db_factory):
    h = login(pa)
    _, v2 = make_paper(db_factory, world["p1"], world["p1b1"], ctx["phy"], "Physics Mock", "PUBLISHED", n=5, paper_id=ctx["phy_paper"], version_no=2)
    with db_factory() as db:
        from institute_common.models.papers import PaperVersion
        db.get(PaperVersion, ctx["phy_v1"]).status = "SUPERSEDED"
        db.commit()
    pinned = plan_body(ctx, slots=[{"subject_id": str(ctx["phy"]), "paper_version_id": str(ctx["phy_v1"])}])
    p1 = client.post(f"{API}/branches/{world['p1b1']}/exam-plans", json=pinned, headers=h).json()
    latest = plan_body(ctx, name="Latest plan", slots=[{"subject_id": str(ctx["phy"]), "paper_id": str(ctx["phy_paper"])}])
    p2 = client.post(f"{API}/branches/{world['p1b1']}/exam-plans", json=latest, headers=h).json()
    e1 = client.get(f"{API}/exams/{client.post(f'{API}/exam-plans/{p1['plan_id']}/generate', json={}, headers=h).json()['exam_id']}", headers=h).json()
    e2 = client.get(f"{API}/exams/{client.post(f'{API}/exam-plans/{p2['plan_id']}/generate', json={}, headers=h).json()['exam_id']}", headers=h).json()
    assert e1["papers"][0]["paper_version_id"] == str(ctx["phy_v1"]) and e1["total_questions"] == 3
    assert e2["papers"][0]["paper_version_id"] == str(v2) and e2["total_questions"] == 5


def test_no_papers_available_fails_without_creating_an_exam(client, world, ctx, pa, login, db_factory):
    from institute_common.models.exams import Exam, Notification
    h = login(pa)
    plan = create_plan(client, h, world, ctx, slots=[{"subject_id": str(ctx["chem"]), "paper_id": str(ctx["chem_paper"])}])
    job = client.post(f"{API}/exam-plans/{plan['plan_id']}/generate", json={}, headers=h).json()
    assert job["status"] == "FAILED" and job["exam_id"] is None
    with db_factory() as db:
        assert db.query(Exam).count() == 0
        assert db.query(Notification).filter_by(kind="EXAM_GENERATION_FAILED").count() >= 1
    # retrying after the paper is published succeeds on the SAME job/run key
    with db_factory() as db:
        from institute_common.models.papers import PaperVersion
        db.get(PaperVersion, ctx["chem_v1"]).status = "PUBLISHED"
        db.commit()
    retry = client.post(f"{API}/exam-plans/{plan['plan_id']}/generate", json={}, headers=h).json()
    assert retry["job_id"] == job["job_id"] and retry["status"] == "SUCCEEDED" and retry["attempts"] == 2


def test_composite_exam_requires_share_and_records_provenance(client, world, ctx, pa, login, db_factory, make_staff):
    p2_paper, p2_v = make_paper(db_factory, world["p2"], world["p2b1"], ctx["chem"], "Provider2 Chem", "PUBLISHED")
    h = login(pa)
    slots = [{"subject_id": str(ctx["phy"]), "paper_id": str(ctx["phy_paper"])}, {"subject_id": str(ctx["chem"]), "paper_id": str(p2_paper)}]
    assert client.post(f"{API}/branches/{world['p1b1']}/exam-plans", json=plan_body(ctx, slots=slots), headers=h).status_code == 422   # not shared
    _, pa2 = make_staff([(Role.PROVIDER_ADMIN, world["p2"], None)])
    h2 = login(pa2)
    assert client.post(f"{API}/papers/{p2_paper}/shares", json={"provider_id": str(world["p1"])}, headers=h2).status_code == 201
    h = login(pa)
    plan = client.post(f"{API}/branches/{world['p1b1']}/exam-plans", json=plan_body(ctx, slots=slots), headers=h)
    assert plan.status_code == 201
    job = client.post(f"{API}/exam-plans/{plan.json()['plan_id']}/generate", json={}, headers=h).json()
    assert job["status"] == "SUCCEEDED"
    exam = client.get(f"{API}/exams/{job['exam_id']}", headers=h).json()
    sources = {p["source_provider_id"] for p in exam["papers"]}
    assert sources == {str(world["p1"]), str(world["p2"])}                         # provenance preserved
    # the sharing provider revoking later does NOT alter the already generated exam
    h2 = login(pa2)
    client.delete(f"{API}/papers/{p2_paper}/shares/{world['p1']}", headers=h2)
    h = login(pa)
    assert client.get(f"{API}/exams/{job['exam_id']}", headers=h).json()["total_questions"] == 6
    nxt_start = utcnow() + timedelta(days=9)
    nxt = client.post(f"{API}/exam-plans/{plan.json()['plan_id']}/generate", headers={**h, "Idempotency-Key": "after-revoke"},
                      json={"start_at": nxt_start.isoformat(), "end_at": (nxt_start + timedelta(hours=3)).isoformat()}).json()
    assert nxt["status"] == "PARTIAL"                                               # revoked => excluded and reported
    miss = client.get(f"{API}/exams/{nxt['exam_id']}", headers=h).json()["missing_subjects"]
    assert miss[0]["reason"] == "NOT_AUTHORIZED"


def test_scheduler_generates_due_plans_once(client, world, ctx, pa, login, db_factory):
    from institute_common.models.exams import Exam
    h = login(pa)
    now = utcnow()
    create_plan(client, h, world, ctx, generate_at=(now - timedelta(minutes=1)).isoformat(), start_at=(now + timedelta(hours=1)).isoformat(),
                end_at=(now + timedelta(hours=5)).isoformat())
    create_plan(client, h, world, ctx, name="Future", generate_at=(now + timedelta(hours=1)).isoformat())
    with db_factory() as db:
        assert len(gen.run_due_plans(db)) == 1
        assert len(gen.run_due_plans(db)) == 0                  # replay creates nothing
        assert db.query(Exam).count() == 1


# ------------------------------------------------------------ delivery (outbox)

class FakePublisher:
    def __init__(self, errors=()):
        self.errors, self.sent = list(errors), []

    def send(self, payload):
        if self.errors:
            raise self.errors.pop(0)
        self.sent.append(payload)


def test_outbox_delivers_complete_payload(client, world, ctx, pa, login, db_factory):
    h = login(pa)
    plan = create_plan(client, h, world, ctx)
    job = client.post(f"{API}/exam-plans/{plan['plan_id']}/generate", json={}, headers=h).json()
    pub = FakePublisher()
    with db_factory() as db:
        assert outbox.deliver_due(db, pub) == {"sent": 1, "retry": 0, "failed": 0}
        assert outbox.deliver_due(db, pub)["sent"] == 0                       # already confirmed
    payload = pub.sent[0]
    assert payload["duration_minutes"] == 180 and payload["total_questions"] == 3 and payload["total_marks"] == 12
    assert sorted(payload["student_ids"]) == ["STU000001", "STU000002"]
    q = payload["questions"][0]
    assert q["marks"] == 4 and q["negative_marks"] == 1 and q["correct"] == "A" and q["subject"] == "Physics"
    assert client.get(f"{API}/exams/{job['exam_id']}", headers=h).json()["delivery_status"] == "SENT"


def test_outbox_retries_transient_then_succeeds_and_surfaces_permanent_failure(client, world, ctx, pa, login, db_factory):
    from institute_common.models.exams import ExamOutbox, Notification
    h = login(pa)
    plan = create_plan(client, h, world, ctx)
    client.post(f"{API}/exam-plans/{plan['plan_id']}/generate", json={}, headers=h)
    pub = FakePublisher([TransientPublishError("503")])
    with db_factory() as db:
        assert outbox.deliver_due(db, pub)["retry"] == 1
        assert outbox.deliver_due(db, pub)["sent"] == 0                       # backoff: not due yet
        row = db.query(ExamOutbox).one()
        assert row.status == "PENDING" and row.attempts == 1 and row.next_attempt_at > utcnow()
        assert outbox.deliver_due(db, pub, now=utcnow() + timedelta(hours=2))["sent"] == 1
    plan2 = create_plan(client, h, world, ctx, name="Bad marks plan")
    client.post(f"{API}/exam-plans/{plan2['plan_id']}/generate", json={}, headers=h)
    with db_factory() as db:
        row = db.query(ExamOutbox).filter_by(status="PENDING").one()
        assert outbox.deliver_due(db, FakePublisher([PermanentPublishError("rejected (422)")]))["failed"] == 1
        assert db.get(ExamOutbox, row.outbox_id).status == "FAILED"
        assert db.query(Notification).filter_by(kind="EXAM_PUBLISH_FAILED").count() >= 1


def test_fractional_marks_are_refused_for_student_delivery(client, world, ctx, pa, login, db_factory):
    paper, v = make_paper(db_factory, world["p1"], world["p1b1"], ctx["chem"], "Fractional", "PUBLISHED", marks="2.5")
    h = login(pa)
    plan = create_plan(client, h, world, ctx, slots=[{"subject_id": str(ctx["chem"]), "paper_id": str(paper)}])
    client.post(f"{API}/exam-plans/{plan['plan_id']}/generate", json={}, headers=h)
    with db_factory() as db:
        assert outbox.deliver_due(db, FakePublisher())["failed"] == 1


def test_manual_publish_and_cancel_flow(client, world, ctx, pa, login, db_factory):
    from institute_common.models.exams import ExamOutbox
    h = login(pa)
    plan = create_plan(client, h, world, ctx, auto_publish=False)
    job = client.post(f"{API}/exam-plans/{plan['plan_id']}/generate", json={}, headers=h).json()
    eid = job["exam_id"]
    assert client.get(f"{API}/exams/{eid}", headers=h).json()["status"] == "SCHEDULED"
    with db_factory() as db:
        assert db.query(ExamOutbox).count() == 0                               # nothing leaves until published
    assert client.post(f"{API}/exams/{eid}/publish", headers=h).json()["status"] == "PUBLISHED"
    assert client.post(f"{API}/exams/{eid}/publish", headers=h).status_code == 409
    assert client.post(f"{API}/exams/{eid}/cancel", json={"reason": ""}, headers=h).status_code == 422
    assert client.post(f"{API}/exams/{eid}/cancel", json={"reason": "paper error found"}, headers=h).json()["status"] == "CANCELLED"
    with db_factory() as db:
        assert sorted(o.operation for o in db.query(ExamOutbox)) == ["CANCEL", "PUBLISH"]


# ------------------------------------------------------------ isolation

def test_exam_endpoints_are_tenant_scoped(client, world, ctx, pa, make_staff, login):
    h = login(pa)
    plan = create_plan(client, h, world, ctx)
    job = client.post(f"{API}/exam-plans/{plan['plan_id']}/generate", json={}, headers=h).json()
    for role, scope in ((Role.PROVIDER_ADMIN, (world["p2"], None)), (Role.BRANCH_ADMIN, (world["p1"], world["p1b2"]))):
        _, code = make_staff([(role, *scope)])
        hh = login(code)
        assert client.get(f"{API}/exams/{job['exam_id']}", headers=hh).status_code == 403
        assert client.get(f"{API}/exams/{job['exam_id']}/assignments", headers=hh).status_code == 403
        assert client.post(f"{API}/exam-plans/{plan['plan_id']}/generate", json={}, headers=hh).status_code == 403
        assert client.post(f"{API}/exams/{job['exam_id']}/cancel", json={"reason": "not mine to cancel"}, headers=hh).status_code == 403
        assert client.get(f"{API}/exam-plans/{plan['plan_id']}", headers=hh).status_code == 403
        assert client.get(f"{API}/jobs/{job['job_id']}/events", headers=hh).status_code == 403
        assert client.patch(f"{API}/exam-plans/{plan['plan_id']}", json={"name": "Hijacked plan"}, headers=hh).status_code == 403
    # teachers may read exams of their branch but not mutate them
    th = login(ctx["tphy"])
    assert client.get(f"{API}/exams/{job['exam_id']}", headers=th).status_code == 200
    assert client.post(f"{API}/exams/{job['exam_id']}/cancel", json={"reason": "teacher tries"}, headers=th).status_code == 403
    assert client.post(f"{API}/exam-plans/{plan['plan_id']}/generate", json={}, headers=th).status_code == 403


# ------------------------------------------------------------ results (read through Student result service)

class FakeResults:
    def __init__(self, rows):
        self.rows = rows
        self.asked = []

    def exam_results(self, test_id):
        self.asked.append(test_id)
        return self.rows


def _published_exam(client, world, ctx, h, db_factory):
    plan = create_plan(client, h, world, ctx)
    job = client.post(f"{API}/exam-plans/{plan['plan_id']}/generate", json={}, headers=h).json()
    return job["exam_id"]


def test_results_scoped_to_assigned_students_and_teacher_batches(client, world, ctx, pa, login, db_factory):
    from institute_common.clients.result_client import set_result_client
    from institute_common.models.academics import TeacherSubjectAssignment
    h = login(pa)
    exam_id = _published_exam(client, world, ctx, h, db_factory)
    rows = [{"student_id": "STU000001", "obtained_marks": 10, "total_marks": 12, "accuracy": 80.0},
            {"student_id": "STU000002", "obtained_marks": 6, "total_marks": 12, "accuracy": 50.0},
            {"student_id": "STU000777", "obtained_marks": 12, "total_marks": 12, "accuracy": 100.0}]   # not assigned to this exam
    fake = FakeResults(rows)
    set_result_client(fake)
    try:
        r = client.get(f"{API}/exams/{exam_id}/results", headers=h).json()
        assert [x["student_id"] for x in r["results"]] == ["STU000001", "STU000002"]            # foreign row filtered out
        assert r["summary"] == {"assigned": 2, "submitted": 2, "not_submitted": 0, "average": 8.0, "highest": 10, "lowest": 6}
        assert [x["rank"] for x in r["results"]] == [1, 2]
        # a teacher assigned to a different batch only sees nothing from this batch
        with db_factory() as db:
            for t in db.query(TeacherSubjectAssignment).filter_by(staff_id=ctx["t_phy_id"]):
                t.batch_id = ctx["other_batch"]
            db.commit()
        th = login(ctx["tphy"])
        assert client.get(f"{API}/exams/{exam_id}/results", headers=th).json()["results"] == []
        assert client.get(f"{API}/exams/{exam_id}/results", headers=login(ctx["tchem"])).status_code == 200
    finally:
        set_result_client(None)


def test_results_unavailable_or_cross_tenant(client, world, ctx, pa, login, db_factory, make_staff):
    from institute_common.clients.result_client import ResultServiceError, set_result_client
    h = login(pa)
    exam_id = _published_exam(client, world, ctx, h, db_factory)
    assert client.get(f"{API}/exams/{exam_id}/results", headers=h).status_code == 503              # not configured => explicit, not silent
    class Down:
        def exam_results(self, t):
            raise ResultServiceError("down")
    set_result_client(Down())
    try:
        assert client.get(f"{API}/exams/{exam_id}/results", headers=h).json()["detail"]["code"] == "RESULT_SERVICE_UNAVAILABLE"
    finally:
        set_result_client(None)
    set_result_client(FakeResults([]))
    try:
        _, pa2 = make_staff([(Role.PROVIDER_ADMIN, world["p2"], None)])
        assert client.get(f"{API}/exams/{exam_id}/results", headers=login(pa2)).status_code == 403
    finally:
        set_result_client(None)
