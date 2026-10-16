
import pytest

from institute_common.core.roles import Role
from institute_common.clients.student_client import StudentServiceError, StudentSummary, set_student_client

API = "/api/v1"


class FakeStudents:
    """Stands in for the Student service. Like the real one, it writes the shared `students` row,
    which `enrollments.student_id` references with a foreign key (enforced on PostgreSQL)."""

    def __init__(self, db_factory=None):
        self.rows: dict[str, StudentSummary] = {}
        self.created = 0
        self.down = False
        self.db_factory = db_factory

    def add(self, sid, first, last, email, phone=None):
        # Student identities live only in the Student service (separate database): nothing is written locally.
        self.rows[sid] = StudentSummary(sid, first, last, email, phone)

    def _chk(self):
        if self.down:
            raise StudentServiceError("down")

    def search(self, *, email=None, phone=None):
        self._chk()
        return [s for s in self.rows.values() if (email and s.email == email) or (phone and s.phone == phone)]

    def get(self, sid):
        self._chk()
        return self.rows.get(sid)

    def get_many(self, ids):
        self._chk()
        return {i: self.rows[i] for i in ids if i in self.rows}

    def create(self, *, first_name, last_name, email, phone, idempotency_key):
        self._chk()
        self.created += 1
        sid = f"STU{len(self.rows) + 1:06d}"
        self.add(sid, first_name, last_name, email, phone)
        return self.rows[sid]


@pytest.fixture()
def students(client, db_factory):
    fake = FakeStudents(db_factory)
    set_student_client(fake)
    yield fake
    set_student_client(None)


@pytest.fixture()
def seeded(client, world, make_staff, login, db_factory):
    """Subjects + a batch per branch; returns ids."""
    from institute_common.models.academics import Subject
    from institute_common.models.org import Batch
    with db_factory() as db:
        phy = Subject(code="PHYSICS", name="Physics")
        db.add(phy)
        db.flush()
        ids = {"phy": phy.subject_id}
        for k in ("p1b1", "p1b2", "p2b1"):
            b = Batch(branch_id=world[k], name=f"batch-{k}")
            db.add(b)
            db.flush()
            ids[f"batch_{k}"] = b.batch_id
        db.commit()
    return ids


def test_subject_catalogue_is_platform_managed(client, world, make_staff, login):
    _, pa = make_staff([(Role.PROVIDER_ADMIN, world["p1"], None)])
    h = login(pa)
    assert client.post(f"{API}/subjects", json={"code": "CHEM", "name": "Chemistry"}, headers=h).status_code == 403
    _, ad = make_staff([(Role.ADMIN, None, None)])
    h2 = login(ad)
    assert client.post(f"{API}/subjects", json={"code": "CHEM", "name": "Chemistry"}, headers=h2).status_code == 201
    assert client.post(f"{API}/subjects", json={"code": "CHEM", "name": "Again"}, headers=h2).status_code == 409


def test_batches_are_branch_scoped(client, world, seeded, make_staff, login):
    _, ba = make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b1"])])
    h = login(ba)
    assert client.post(f"{API}/branches/{world['p1b1']}/batches", json={"name": "NEET 2027"}, headers=h).status_code == 201
    assert client.post(f"{API}/branches/{world['p1b2']}/batches", json={"name": "Sneaky"}, headers=h).status_code == 403
    assert client.post(f"{API}/branches/{world['p2b1']}/batches", json={"name": "Sneaky"}, headers=h).status_code == 403
    assert client.patch(f"{API}/batches/{seeded['batch_p2b1']}", json={"name": "Hijack batch"}, headers=h).status_code == 403
    assert client.get(f"{API}/branches/{world['p1b2']}/batches", headers=h).status_code == 403
    bad = client.post(f"{API}/branches/{world['p1b1']}/batches", json={"name": "Dates", "start_date": "2026-05-01", "end_date": "2026-01-01"}, headers=h)
    assert bad.status_code == 422


def test_batch_subject_mapping_requires_provider_enablement(client, world, seeded, make_staff, login):
    _, pa = make_staff([(Role.PROVIDER_ADMIN, world["p1"], None)])
    h = login(pa)
    url = f"{API}/batches/{seeded['batch_p1b1']}/subjects/{seeded['phy']}"
    assert client.put(url, headers=h).status_code == 422
    assert client.put(f"{API}/providers/{world['p1']}/subjects/{seeded['phy']}", headers=h).status_code == 200
    assert client.put(url, headers=h).status_code == 200
    assert client.put(f"{API}/providers/{world['p2']}/subjects/{seeded['phy']}", headers=h).status_code == 403


def test_teaching_assignment_and_teacher_visibility(client, world, seeded, make_staff, login):
    t, tcode = make_staff([(Role.TEACHER, world["p1"], None)])
    _, pa = make_staff([(Role.PROVIDER_ADMIN, world["p1"], None)])
    h = login(pa)
    client.put(f"{API}/providers/{world['p1']}/subjects/{seeded['phy']}", headers=h)
    body = {"staff_id": str(t), "branch_id": str(world["p1b1"]), "subject_id": str(seeded["phy"]), "batch_id": str(seeded["batch_p1b1"])}
    assert client.post(f"{API}/teaching", json=body, headers=h).status_code == 201
    assert client.post(f"{API}/teaching", json=body, headers=h).status_code == 409
    # cross-tenant batch / branch are rejected
    assert client.post(f"{API}/teaching", json={**body, "batch_id": str(seeded["batch_p1b2"])}, headers=h).status_code == 422
    assert client.post(f"{API}/teaching", json={**body, "branch_id": str(world["p2b1"])}, headers=h).status_code == 403
    th = login(tcode)
    # the teacher sees exactly the branch/batch they teach
    assert client.get(f"{API}/branches/{world['p1b1']}/batches", headers=th).status_code == 200
    assert client.get(f"{API}/branches/{world['p1b2']}/batches", headers=th).status_code == 403
    ids = [b["batch_id"] for b in client.get(f"{API}/branches/{world['p1b1']}/batches", headers=th).json()["items"]]
    assert ids == [str(seeded["batch_p1b1"])]
    assert client.post(f"{API}/branches/{world['p1b1']}/batches", json={"name": "Teacher batch"}, headers=th).status_code == 403
    assert client.get(f"{API}/branches/{world['p1b1']}/enrollments", headers=th).status_code == 403


def test_enroll_new_student_and_no_duplicate_identity(client, world, seeded, students, make_staff, login):
    _, ba = make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b1"])])
    h = login(ba)
    new = {"first_name": "Asha", "last_name": "Rao", "email": "asha@example.com", "phone": "9000000001"}
    r = client.post(f"{API}/branches/{world['p1b1']}/enrollments", json={"new_student": new, "batch_ids": [str(seeded["batch_p1b1"])]}, headers=h)
    assert r.status_code == 201, r.text
    assert r.json()["student_id"] == "STU000001" and r.json()["batch_ids"] == [str(seeded["batch_p1b1"])]
    # same email again: never creates a second identity
    again = client.post(f"{API}/branches/{world['p1b1']}/enrollments", json={"new_student": new}, headers=h)
    assert again.status_code == 409 and again.json()["detail"]["code"] == "STUDENT_EXISTS"
    assert students.created == 1
    # enrolling the existing student twice in the branch is rejected
    dup = client.post(f"{API}/branches/{world['p1b1']}/enrollments", json={"student_id": "STU000001"}, headers=h)
    assert dup.status_code == 409 and dup.json()["detail"]["code"] == "ALREADY_ENROLLED"


def test_phone_match_requires_explicit_confirmation(client, world, students, make_staff, login):
    students.add("STU000009", "Other", "Person", "other@example.com", "9000000002")
    _, ba = make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b1"])])
    h = login(ba)
    new = {"first_name": "Ravi", "last_name": "K", "email": "ravi@example.com", "phone": "9000000002"}
    r = client.post(f"{API}/branches/{world['p1b1']}/enrollments", json={"new_student": new}, headers=h)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "STUDENT_MATCH_FOUND" and students.created == 0
    ok = client.post(f"{API}/branches/{world['p1b1']}/enrollments", json={"new_student": new, "confirm_distinct_person": True}, headers=h)
    assert ok.status_code == 201 and students.created == 1


def test_existing_student_joins_second_provider_without_duplicate(client, world, students, make_staff, login):
    students.add("STU000005", "Meera", "S", "meera@example.com")
    _, ba1 = make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b1"])])
    _, ba2 = make_staff([(Role.BRANCH_ADMIN, world["p2"], world["p2b1"])])
    h1 = login(ba1)
    assert client.post(f"{API}/branches/{world['p1b1']}/enrollments", json={"student_id": "STU000005"}, headers=h1).status_code == 201
    h2 = login(ba2)
    assert client.post(f"{API}/branches/{world['p2b1']}/enrollments", json={"student_id": "STU000005"}, headers=h2).status_code == 201
    assert students.created == 0 and len(students.rows) == 1


def test_enrollment_isolation_and_lookup_minimal_disclosure(client, world, seeded, students, make_staff, login):
    students.add("STU000005", "Meera", "Sharma", "meera@example.com", "9000000003")
    _, ba1 = make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b1"])])
    h = login(ba1)
    look = client.post(f"{API}/students/lookup", json={"branch_id": str(world["p1b1"]), "email": "meera@example.com"}, headers=h).json()
    assert look["matches"] == [{"student_id": "STU000005", "name": "Meera S."}]   # no email/phone/surname
    assert client.post(f"{API}/students/lookup", json={"branch_id": str(world["p2b1"]), "email": "meera@example.com"}, headers=h).status_code == 403
    assert client.post(f"{API}/branches/{world['p2b1']}/enrollments", json={"student_id": "STU000005"}, headers=h).status_code == 403
    # batch from another branch cannot be attached
    bad = client.post(f"{API}/branches/{world['p1b1']}/enrollments", json={"student_id": "STU000005", "batch_ids": [str(seeded["batch_p1b2"])]}, headers=h)
    assert bad.status_code == 422
    assert client.get(f"{API}/branches/{world['p2b1']}/enrollments", headers=h).status_code == 403


def test_student_service_outage_is_reported_not_swallowed(client, world, students, make_staff, login):
    _, ba = make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b1"])])
    h = login(ba)
    students.down = True
    r = client.post(f"{API}/branches/{world['p1b1']}/enrollments", json={"student_id": "STU000001"}, headers=h)
    assert r.status_code == 503 and r.json()["detail"]["code"] == "STUDENT_SERVICE_UNAVAILABLE"


def test_cross_branch_transfer_needs_provider_admin(client, world, students, make_staff, login):
    students.add("STU000005", "Meera", "S", "meera@example.com")
    _, ba = make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b1"])])
    _, pa = make_staff([(Role.PROVIDER_ADMIN, world["p1"], None)])
    hb = login(ba)
    eid = client.post(f"{API}/branches/{world['p1b1']}/enrollments", json={"student_id": "STU000005"}, headers=hb).json()["enrollment_id"]
    t = client.post(f"{API}/enrollments/{eid}/transfers", json={"to_branch_id": str(world["p1b2"]), "reason": "relocated"}, headers=hb)
    assert t.status_code == 201
    tid = t.json()["transfer_id"]
    assert client.post(f"{API}/transfers/{tid}/approve", json={"note": "approved by me"}, headers=hb).status_code == 403   # BA cannot approve
    assert client.post(f"{API}/enrollments/{eid}/transfers", json={"to_branch_id": str(world["p2b1"]), "reason": "other provider"}, headers=hb).status_code in (409, 422)
    hp = login(pa)
    ok = client.post(f"{API}/transfers/{tid}/approve", json={"note": "documents verified"}, headers=hp)
    assert ok.status_code == 200 and ok.json()["status"] == "APPROVED"
    assert client.post(f"{API}/transfers/{tid}/approve", json={"note": "again again"}, headers=hp).status_code == 409
    hb = login(ba)
    old = client.get(f"{API}/branches/{world['p1b1']}/enrollments", headers=hb).json()["items"][0]
    assert old["status"] == "INACTIVE"                                      # history preserved, not deleted
    assert client.get(f"{API}/providers/{world['p1']}/transfers", headers=hb).status_code == 200


def test_other_provider_admin_cannot_decide_transfer(client, world, students, make_staff, login):
    students.add("STU000005", "Meera", "S", "meera@example.com")
    _, ba = make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b1"])])
    _, pa2 = make_staff([(Role.PROVIDER_ADMIN, world["p2"], None)])
    hb = login(ba)
    eid = client.post(f"{API}/branches/{world['p1b1']}/enrollments", json={"student_id": "STU000005"}, headers=hb).json()["enrollment_id"]
    tid = client.post(f"{API}/enrollments/{eid}/transfers", json={"to_branch_id": str(world["p1b2"]), "reason": "relocated"}, headers=hb).json()["transfer_id"]
    h2 = login(pa2)
    assert client.post(f"{API}/transfers/{tid}/approve", json={"note": "not my provider"}, headers=h2).status_code == 403
    assert client.get(f"{API}/providers/{world['p1']}/transfers", headers=h2).status_code == 403
