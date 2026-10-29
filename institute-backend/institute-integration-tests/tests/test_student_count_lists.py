"""Clickable student counts: every list endpoint returns exactly the students behind the count it explains, under the same scope, and
never another provider's students."""
from institute_common.core.roles import Role
from tests.test_global_contributions import client, login  # noqa: F401
from tests.test_global_schedule import aud, gs, published, submit  # noqa: F401
from tests.test_exams import ctx, pa  # noqa: F401  (fixtures)
from tests.test_review_fixes import API, people, two_branches  # noqa: F401  (fixtures)
from tests.test_student_integration import live_exam  # noqa: F401  (fixture)


def test_an_exams_assigned_count_matches_its_student_list_for_every_scope(client, world, two_branches, make_staff, login):  # noqa: F811
    exam = two_branches["inst_exam"]
    for staff, branch, expected in (([(Role.PROVIDER_ADMIN, world["p1"], None)], world["p1b1"], {"STU000001", "STU000002"}),
                                    ([(Role.BRANCH_ADMIN, world["p1"], world["p1b2"])], world["p1b2"], {"STU000002"})):  # institute-wide: own branch only
        _, code = make_staff(staff)
        h = login(code)
        listed = next(x for x in client.get(f"{API}/branches/{branch}/exams", headers=h).json()["items"] if x["exam_id"] == str(exam))
        students = client.get(f"{API}/exams/{exam}/assignments", headers=h).json()
        assert listed["assigned_students"] == students["total"] == len(expected)
        assert {x["student_id"] for x in students["items"]} == expected
        assert all("name" in x for x in students["items"])
    _, other = make_staff([(Role.PROVIDER_ADMIN, world["p2"], None)])
    assert client.get(f"{API}/exams/{exam}/assignments", headers=login(other)).status_code == 403


def test_global_submission_and_coverage_counts_match_their_lists_and_stay_within_the_provider(client, world, gs, login, make_staff):  # noqa: F811
    t, occ = published(client, login, gs)
    sub = submit(client, login, gs["tA"], occ, gs["phy"], gs["paperA"], aud(world, gs, batches=("batch_a",))).json()
    detail = client.get(f"{API}/global-occurrences/{occ['occurrence_id']}", headers=login(gs["pa"])).json()
    row = next(x for x in detail["submission_list"] if x["submission_id"] == sub["submission_id"])
    reach = client.get(f"{API}/global-paper-submissions/{sub['submission_id']}/students", headers=login(gs["pa"])).json()
    assert reach["total"] == row["students_now"] > 0 and len(reach["items"]) == reach["total"]
    cov = next(x for x in detail["coverage"] if x["subject_id"] == str(gs["phy"]) and x["provider_id"] == str(world["p1"]))
    covered = client.get(f"{API}/global-occurrences/{occ['occurrence_id']}/coverage/students",
                         params={"provider_id": str(world["p1"]), "subject_id": str(gs["phy"])}, headers=login(gs["pa"])).json()
    assert covered["total"] == cov["students_covered"]
    tutor = login(gs["tutor"])                                                      # another provider's staff
    assert client.get(f"{API}/global-paper-submissions/{sub['submission_id']}/students", headers=tutor).status_code == 404
    assert client.get(f"{API}/global-occurrences/{occ['occurrence_id']}/coverage/students",
                      params={"provider_id": str(world["p1"]), "subject_id": str(gs["phy"])}, headers=tutor).status_code == 404
    platform = client.get(f"{API}/global-paper-submissions/{sub['submission_id']}/students", headers=login(gs["sa"])).json()
    assert platform["total"] == reach["total"]


def test_scheduled_tests_assigned_count_matches_the_list_for_admins_and_teachers(client, world, ctx, live_exam, login, db_factory):  # noqa: F811
    from institute_common.models.academics import TeacherSubjectAssignment
    from institute_common.models.staff import Staff
    with db_factory() as db:                                                         # a teacher of this branch from the ctx world
        teacher = db.query(Staff).join(TeacherSubjectAssignment, TeacherSubjectAssignment.staff_id == Staff.staff_id).first().staff_code
    for h in (live_exam["h"], login(teacher)):
        summary = next(x for x in client.get(f"{API}/tests", headers=h).json()["items"] if x["exam_id"] == live_exam["exam_id"])
        students = client.get(f"{API}/exams/{live_exam['exam_id']}/assignments", headers=h).json()
        assert summary["assigned_students"] == students["total"] == len(students["items"])
