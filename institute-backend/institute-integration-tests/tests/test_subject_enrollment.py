"""Subject-wise enrollment: a batch membership may take only some of the batch's subjects (enrollment_batch_subjects).
A membership without a selection keeps the original meaning (every subject). A batch-based exam reaches a student only if they take
ALL of its subjects in that batch; students listed by staff are an override. Teachers see only students who take their subject."""
from types import SimpleNamespace

import pytest

from tests.test_create_test import aud, ct, draft, publish  # noqa: F401  (fixtures and helpers)
from tests.test_teacher_visibility import names, students_of, tv  # noqa: F401
from tests.test_test_creation import tc  # noqa: F401  (fixture)

API = "/api/v1"
ACAD = {"class_level": "11th", "academic_year": "2026-2027"}


@pytest.fixture()
def se(tv, db_factory):  # noqa: F811
    """tv, with batch A offering Physics and Chemistry. STU000001 (e1) and STU000004 (e4a) are in batch A without a selection."""
    from institute_common.models.academics import BatchSubject
    with db_factory() as db:
        for s in ("phy", "chem"):
            db.add(BatchSubject(batch_id=tv["batch_a"], subject_id=tv[s]))
        db.commit()
    return tv


def choose(client, h, se, eid, subjects, batch="batch_a"):
    body = {"subject_ids": None if subjects is None else [str(se[s]) for s in subjects]}
    return client.put(f"{API}/enrollments/{eid}/batches/{se[batch]}", json=body, headers=h)


def roster(client, h, world):
    return {e["enrollment_id"]: e for e in client.get(f"{API}/branches/{world['p1b1']}/enrollments", headers=h).json()["items"]}


def test_a_membership_can_take_some_of_its_batch_subjects_and_the_change_is_recorded(client, world, se, login, db_factory):
    from institute_common.models.academics import EnrollmentBatchSubject, EnrollmentEvent
    h = login(se["pa"])
    assert roster(client, h, world)["e1"]["batch_subjects"] == {}                       # no selection: every subject
    assert choose(client, h, se, "e1", ["phy"]).status_code == 204
    assert roster(client, h, world)["e1"]["batch_subjects"] == {str(se["batch_a"]): [str(se["phy"])]}
    assert roster(client, h, world)["e4a"]["batch_subjects"] == {}                      # other memberships untouched
    with db_factory() as db:
        ev = db.query(EnrollmentEvent).filter_by(enrollment_id="e1", event_type="BATCH_SUBJECTS_SET").one()
        assert ev.batch_id == se["batch_a"] and ev.reason == "Subjects: Physics"
    assert choose(client, h, se, "e1", None).status_code == 204                         # back to every subject
    assert roster(client, h, world)["e1"]["batch_subjects"] == {}
    with db_factory() as db:
        assert db.query(EnrollmentBatchSubject).count() == 0


def test_only_subjects_the_batch_offers_can_be_chosen(client, world, se, login):
    h = login(se["pa"])
    assert choose(client, h, se, "e1", ["bio"]).status_code == 422                      # not offered by batch A
    assert choose(client, h, se, "e2", ["phy"], batch="batch_b").status_code == 422     # batch B offers nothing
    assert choose(client, h, se, "e1", []).status_code == 422                           # at least one subject
    assert roster(client, h, world)["e1"]["batch_subjects"] == {}


def test_enrolling_with_a_subject_selection_validates_it(client, world, se, login):
    h = login(se["pa"])
    url = f"{API}/branches/{world['p1b1']}/enrollments"
    a = str(se["batch_a"])
    assert client.post(url, json={"student_id": "STU000003", "academic": ACAD, "batch_ids": [], "batch_subjects": [{"batch_id": a, "subject_ids": [str(se["chem"])]}]},
                       headers=h).status_code == 422                                    # a batch the student is not put into
    assert client.post(url, json={"student_id": "STU000003", "academic": ACAD, "batch_ids": [a], "batch_subjects": [{"batch_id": a, "subject_ids": [str(se["bio"])]}]},
                       headers=h).status_code == 422
    r = client.post(url, json={"student_id": "STU000003", "academic": ACAD, "batch_ids": [a], "batch_subjects": [{"batch_id": a, "subject_ids": [str(se["chem"])]}]}, headers=h)
    assert r.status_code == 201, r.text
    assert r.json()["batch_subjects"] == {a: [str(se["chem"])]}


def test_removing_a_batch_membership_removes_its_subject_selection(client, world, se, login, db_factory):
    from institute_common.models.academics import EnrollmentBatchSubject
    h = login(se["pa"])
    assert choose(client, h, se, "e1", ["phy"]).status_code == 204
    assert client.delete(f"{API}/enrollments/e1/batches/{se['batch_a']}", headers=h).status_code == 204
    with db_factory() as db:
        assert db.query(EnrollmentBatchSubject).count() == 0


def test_a_batch_exam_reaches_only_students_taking_all_its_subjects(client, world, se, login):
    h = login(se["pa"])
    assert choose(client, h, se, "e1", ["phy"]).status_code == 204                      # STU000001: Physics only
    both = publish(client, h, draft(world, se, name="Physics and Chemistry", audience=aud(world, se, batches=("batch_a",)),
                                    subjects=[("phy", str(se["phy_v1"])), ("chem", str(se["chem_v1"]))]))
    phy = publish(client, h, draft(world, se, name="Physics", audience=aud(world, se, batches=("batch_a",)), subjects=[("phy", str(se["phy_v1"]))]))
    chem = publish(client, h, draft(world, se, name="Chemistry", audience=aud(world, se, batches=("batch_a",)), subjects=[("chem", str(se["chem_v1"]))]))
    for r in (both, phy, chem):
        assert r.status_code == 201, r.text
    assert students_of(client, login, se["pa"], both.json()["exam_id"]) == ["STU000004"]
    assert students_of(client, login, se["pa"], phy.json()["exam_id"]) == ["STU000001", "STU000004"]
    assert students_of(client, login, se["pa"], chem.json()["exam_id"]) == ["STU000004"]


def test_whole_branch_audience_and_previews_apply_the_same_rule(client, world, se, login):
    h = login(se["pa"])
    assert choose(client, h, se, "e1", ["phy"]).status_code == 204
    body = aud(world, se, whole=("p1b1",))
    r = client.post(f"{API}/tests/audience", json={**body, "subject_ids": [str(se["chem"])]}, headers=h)
    assert r.status_code == 200, r.text
    # STU000001 takes only Physics in its one batch; STU000002 (batch B, no selection) takes every subject
    assert sorted(s["student_id"] for s in r.json()["students"]) == ["STU000002", "STU000004"]
    r = client.post(f"{API}/branches/{world['p1b1']}/audience-preview",
                    json={"audience_type": "BATCHES", "batch_ids": [str(se["batch_a"])], "subject_ids": [str(se["chem"])]}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["eligible_count"] == 1
    r = client.post(f"{API}/branches/{world['p1b1']}/audience-preview", json={"audience_type": "BATCHES", "batch_ids": [str(se["batch_a"])]}, headers=h)
    assert r.json()["eligible_count"] == 2                                              # no subjects given: unchanged


def test_students_listed_by_staff_are_an_override(client, world, se, login):
    h = login(se["pa"])
    assert choose(client, h, se, "e1", ["phy"]).status_code == 204
    r = publish(client, h, draft(world, se, name="Listed", audience=aud(world, se, batches=("batch_a",), students=("STU000001",)),
                                 subjects=[("phy", str(se["phy_v1"])), ("chem", str(se["chem_v1"]))]))
    assert r.status_code == 201, r.text
    assert students_of(client, login, se["pa"], r.json()["exam_id"]) == ["STU000001", "STU000004"]


def test_teachers_see_only_students_who_take_their_subject(client, world, se, login):
    h = login(se["pa"])
    assert choose(client, h, se, "e1", ["phy"]).status_code == 204
    r = publish(client, h, draft(world, se, name="Listed", audience=aud(world, se, batches=("batch_a",), students=("STU000001",)),
                                 subjects=[("phy", str(se["phy_v1"])), ("chem", str(se["chem_v1"]))]))
    exam = r.json()["exam_id"]
    assert students_of(client, login, se["t_phy_a"], exam) == ["STU000001", "STU000004"]
    assert students_of(client, login, se["t_chem_a"], exam) == ["STU000004"]            # STU000001 takes no Chemistry in batch A
    assert names(client, login, se["t_chem_a"]) == ["Listed"]


def test_a_global_test_submission_reaches_only_students_taking_its_subject(world, se, db_factory, client, login):
    from inst_exam.services.global_generation import resolve_submission
    assert choose(client, login(se["pa"]), se, "e1", ["phy"]).status_code == 204
    audience = aud(world, se, batches=("batch_a",))
    with db_factory() as db:
        chem = resolve_submission(db, SimpleNamespace(audience=audience, subject_id=se["chem"]), None)   # no Class on this test
        phy = resolve_submission(db, SimpleNamespace(audience=audience, subject_id=se["phy"]), None)
    assert [r[0] for r in chem] == ["STU000004"]
    assert [r[0] for r in phy] == ["STU000001", "STU000004"]
