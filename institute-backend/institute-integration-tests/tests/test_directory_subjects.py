"""Students & Enrollments directory: each enrollment lists the subjects it takes (from its batch memberships), so the directory shows which
provider teaches the student which subject; never another provider's enrollments."""
import pytest

from institute_common.core.roles import Role

API = "/api/v1"


@pytest.fixture()
def mapped(world, db_factory):
    """STU000101: Physics (chosen) in p1's batch A, Chemistry in p2's batch C (no selection: every subject of batch C, which offers only
    Chemistry), and an INACTIVE p1 enrollment in branch b2's batch B (Biology). STU000102: enrolled with p1 in no batch."""
    from institute_common.models.academics import BatchSubject, EnrollmentBatchSubject, Subject
    from institute_common.models.org import Batch, Enrollment, EnrollmentBatch
    with db_factory() as db:
        subj = {n: Subject(code=n.upper()[:8], name=n) for n in ("Physics", "Chemistry", "Biology")}
        db.add_all(subj.values())
        db.flush()
        batches = {}
        for key, branch, offers in (("A", "p1b1", ("Physics", "Chemistry")), ("C", "p2b1", ("Chemistry",)), ("B", "p1b2", ("Biology",))):
            b = Batch(branch_id=world[branch], name=f"Batch {key}")
            db.add(b)
            db.flush()
            batches[key] = b.batch_id
            for name in offers:
                db.add(BatchSubject(batch_id=b.batch_id, subject_id=subj[name].subject_id))
        rows = (("e101a", "p1", "p1b1", "A", ["Physics"], "ACTIVE"), ("e101c", "p2", "p2b1", "C", None, "ACTIVE"),
                ("e101b", "p1", "p1b2", "B", None, "INACTIVE"), ("e102", "p1", "p1b1", None, None, "ACTIVE"))
        for eid, prov, branch, batch, chosen, status in rows:
            sid = "STU000102" if eid == "e102" else "STU000101"
            db.add(Enrollment(enrollment_id=eid, provider_id=world[prov], branch_id=world[branch], student_id=sid, status=status))
            db.flush()
            if batch:
                link = EnrollmentBatch(enrollment_id=eid, batch_id=batches[batch])
                db.add(link)
                db.flush()
                for name in chosen or []:
                    db.add(EnrollmentBatchSubject(enrollment_batch_id=link.enrollment_batch_id, subject_id=subj[name].subject_id))
        db.commit()


def pairs(body, sid):
    row = next(r for r in body["items"] if r["student_id"] == sid)
    return sorted((s["subject"], e["provider_name"], e["status"], s["chosen"]) for e in row["enrollments"] for s in e["subjects"])


def test_each_subject_is_shown_with_the_provider_that_teaches_it(client, world, mapped, make_staff, login):
    _, sa = make_staff([(Role.SUPER_ADMIN, None, None)])
    body = client.get(f"{API}/students", headers=login(sa)).json()
    assert pairs(body, "STU000101") == [("Biology", "Provider p1", "INACTIVE", False),        # kept, marked inactive
                                        ("Chemistry", "Provider p2", "ACTIVE", False),         # no selection: the batch's subjects
                                        ("Physics", "Provider p1", "ACTIVE", True)]            # chosen in the membership
    assert pairs(body, "STU000102") == []                                                    # in no batch: no subject is invented
    row = next(r for r in body["items"] if r["student_id"] == "STU000101")
    assert {e["enrollment_id"]: [s["batch"] for s in e["subjects"]] for e in row["enrollments"]}["e101a"] == ["Batch A"]


def test_a_provider_admin_sees_only_their_own_enrollments_subjects(client, world, mapped, make_staff, login):
    _, p2 = make_staff([(Role.PROVIDER_ADMIN, world["p2"], None)])
    body = client.get(f"{API}/students", headers=login(p2)).json()
    assert pairs(body, "STU000101") == [("Chemistry", "Provider p2", "ACTIVE", False)]       # never p1's Physics or Biology
    assert "STU000102" not in {r["student_id"] for r in body["items"]}
