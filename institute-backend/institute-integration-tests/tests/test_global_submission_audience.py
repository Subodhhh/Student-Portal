"""A Global Test paper submission resolves its audience exactly as generation does (global_generation.resolve_submission): only
students who take the paper's subject in that batch, and who are in the test's Class. It used to resolve the bare audience, so the
empty-audience check passed for students who would receive nothing, the overlap check refused submissions that did not really
overlap, and the reported student count was inflated. World: tests.test_global_schedule.gs (tA Physics batch A, tB Physics batch B,
tC Chemistry all of p1-b1; STU000001 in A, STU000002 in B)."""
from uuid import UUID, uuid4

from tests.test_global_schedule import aud, exams_of, gs, published, run_generation, submit  # noqa: F401
from tests.test_global_contributions import client, login  # noqa: F401


def _membership(db_factory, enrollment_id, batch, takes=None):
    """Add `enrollment_id` to `batch`; `takes` limits the subjects it takes there (None = every subject)."""
    from institute_common.models.academics import EnrollmentBatchSubject
    from institute_common.models.org import EnrollmentBatch
    with db_factory() as db:
        link = EnrollmentBatch(enrollment_id=enrollment_id, batch_id=batch)
        db.add(link)
        db.flush()
        for s in takes or []:
            db.add(EnrollmentBatchSubject(enrollment_batch_id=link.enrollment_batch_id, subject_id=s))
        db.commit()


def _takes_only(db_factory, enrollment_id, batch, subject):
    from institute_common.models.academics import EnrollmentBatchSubject
    from institute_common.models.org import EnrollmentBatch
    with db_factory() as db:
        link = db.query(EnrollmentBatch).filter_by(enrollment_id=enrollment_id, batch_id=batch).one()
        db.add(EnrollmentBatchSubject(enrollment_batch_id=link.enrollment_batch_id, subject_id=subject))
        db.commit()


def _enroll(db_factory, world, student_id, batch, cls=None):
    from institute_common.models.academics import EnrollmentAcademicProfile
    from institute_common.models.org import Enrollment, EnrollmentBatch
    eid = f"e-{uuid4().hex[:8]}"
    with db_factory() as db:
        db.add(Enrollment(enrollment_id=eid, provider_id=world["p1"], branch_id=world["p1b1"], student_id=student_id))
        db.flush()
        db.add(EnrollmentBatch(enrollment_id=eid, batch_id=batch))
        if cls:
            db.add(EnrollmentAcademicProfile(enrollment_id=eid, class_level=cls, academic_year="2026-2027"))
        db.commit()
    return eid


def test_a_batch_whose_students_do_not_take_the_subject_is_refused(client, world, gs, login, db_factory):  # noqa: F811
    _takes_only(db_factory, "g1", gs["batch_a"], gs["chem"])                 # STU000001 takes only Chemistry in batch A
    _t, occ = published(client, login, gs)
    r = submit(client, login, gs["tA"], occ, gs["phy"], gs["paperA"], aud(world, gs, batches=("batch_a",)))
    assert r.status_code == 422 and r.json()["detail"]["code"] == "AUDIENCE_EMPTY", r.text   # before: accepted for nobody


def test_no_false_overlap_and_each_student_gets_the_right_teachers_paper(client, world, gs, login, db_factory):  # noqa: F811
    _takes_only(db_factory, "g1", gs["batch_a"], gs["chem"])                 # STU000001: Chemistry in A ...
    _membership(db_factory, "g1", gs["batch_b"])                             # ... and every subject (so Physics) in B
    _enroll(db_factory, world, "STU000005", gs["batch_a"])                    # a Physics student of batch A
    t, occ = published(client, login, gs)
    b = submit(client, login, gs["tB"], occ, gs["phy"], gs["paperB"], aud(world, gs, batches=("batch_b",)))
    assert b.status_code == 201 and b.json()["students_now"] == 2, b.text    # STU000001 + STU000002 take Physics in B
    a = submit(client, login, gs["tA"], occ, gs["phy"], gs["paperA"], aud(world, gs, batches=("batch_a",)))
    assert a.status_code == 201, a.text                                      # before: 409 AUDIENCE_OVERLAP (STU000001)
    assert a.json()["students_now"] == 1                                     # only STU000005 takes Physics in A
    run_generation(db_factory, t, occ)
    got = exams_of(db_factory, UUID(occ["occurrence_id"]))
    assert got["STU000001"] == [(world["p1"], [gs["paperB"]])]               # exactly tB's paper, once
    assert got["STU000005"] == [(world["p1"], [gs["paperA"]])]


def test_the_tests_class_limits_who_a_submission_reaches(client, world, gs, login, db_factory):  # noqa: F811
    from institute_common.models.exams import GlobalTest
    from institute_common.models.org import Enrollment
    t, occ = published(client, login, gs, target_class="12th")               # the Global Test is for Class 12th
    with db_factory() as db:
        assert db.query(GlobalTest).filter_by(test_id=UUID(t["test_id"])).one().target_class == "12th"
        assert db.query(Enrollment).filter_by(enrollment_id="g2").one()      # STU000002 (batch B) exists, no Class yet
    r = submit(client, login, gs["tB"], occ, gs["phy"], gs["paperB"], aud(world, gs, batches=("batch_b",)), key="cls-nothing")
    assert r.status_code == 422 and "Class 12th" in r.json()["detail"]["message"], r.text
    _enroll(db_factory, world, "STU000006", gs["batch_b"], cls="12TH")
    r = submit(client, login, gs["tB"], occ, gs["phy"], gs["paperB"], aud(world, gs, batches=("batch_b",)), key="cls-twelve")
    assert r.status_code == 201 and r.json()["students_now"] == 1, r.text   # only the 12th student
