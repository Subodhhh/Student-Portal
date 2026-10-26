"""Exam integrity for staff: who sees which attempts' warnings, and who may decide a review (the attempt service is faked; its own rules
are tested in student-attempt-service/tests/test_integrity_pg.py)."""
import pytest

from institute_common.clients.attempt_client import set_attempt_client
from institute_common.core.roles import Role
from tests.test_exams import API, ctx, pa  # noqa: F401  (fixtures)
from tests.test_student_integration import live_exam  # noqa: F401  (fixture)


class FakeAttemptService:
    def __init__(self, test_id):
        self.test_id, self.decisions = str(test_id), []
        self.rows = [
            {"student_id": "STU000001", "attempt_id": "11111111-1111-4111-8111-111111111111", "status": "AUTO_SUBMITTED", "started_at": "x", "expires_at": "x", "submitted_at": "y",
             "submit_reason": "WARNING_THRESHOLD", "review_status": "UNDER_REVIEW", "warnings": 3, "retake_of_attempt_id": None},
            {"student_id": "STU000002", "attempt_id": "22222222-2222-4222-8222-222222222222", "status": "SUBMITTED", "started_at": "x", "expires_at": "x", "submitted_at": "y",
             "submit_reason": "STUDENT", "review_status": None, "warnings": 0, "retake_of_attempt_id": None},      # nothing to investigate
            {"student_id": "STU999999", "attempt_id": "99999999-9999-4999-8999-999999999999", "status": "AUTO_SUBMITTED", "started_at": "x", "expires_at": "x", "submitted_at": "y",
             "submit_reason": "WARNING_THRESHOLD", "review_status": "UNDER_REVIEW", "warnings": 3, "retake_of_attempt_id": None},  # not assigned
        ]

    def exam_attempts(self, test_id):
        assert test_id == self.test_id
        return self.rows

    def test_reviews(self, test_id):
        return [{"attempt_id": "11111111-1111-4111-8111-111111111111", "student_id": "STU000001", "status": "OPEN", "student_message": "System update prompt"}]

    def attempt_integrity(self, attempt_id):
        row = next((r for r in self.rows if r["attempt_id"] == attempt_id), None)
        if not row:
            return None
        return {**row, "test_id": self.test_id, "threshold": 3, "events": [{"kind": "TAB_HIDDEN", "counted": True, "warning_no": 1}], "review": None}

    def review_decision(self, attempt_id, body):
        if any(d[0] == attempt_id and d[1]["decision"] != body["decision"] for d in self.decisions):
            return 409, {"detail": "This review was already decided: RETAKE_AUTHORIZED"}
        self.decisions.append((attempt_id, body))
        return 200, {"status": "RETAKE_AUTHORIZED" if body["decision"] == "RETAKE" else "UPHELD"}


@pytest.fixture()
def fake(live_exam):  # noqa: F811
    svc = FakeAttemptService(live_exam["ev"])
    set_attempt_client(svc)
    yield svc
    set_attempt_client(None)


def test_staff_see_only_flagged_attempts_of_their_own_students(client, world, live_exam, fake, make_staff, login):  # noqa: F811
    url = f"{API}/exams/{live_exam['exam_id']}/integrity"
    body = client.get(url, headers=live_exam["h"]).json()                       # the provider admin of the exam's provider
    assert [(x["student_id"], x["warnings"], x["submit_reason"], x["can_decide"]) for x in body["items"]] == [
        ("STU000001", 3, "WARNING_THRESHOLD", True)]                             # never the unassigned STU999999, nor clean attempts
    assert body["items"][0]["review"]["student_message"] == "System update prompt"
    _, other = make_staff([(Role.PROVIDER_ADMIN, world["p2"], None)])
    assert client.get(url, headers=login(other)).status_code == 403             # another provider: not even the exam
    _, sa = make_staff([(Role.SUPER_ADMIN, None, None)])
    assert [x["can_decide"] for x in client.get(url, headers=login(sa)).json()["items"]] == [True]


def test_details_are_scoped_to_the_exam_and_the_callers_students(client, live_exam, fake):  # noqa: F811
    base = f"{API}/exams/{live_exam['exam_id']}/integrity"
    detail = client.get(f"{base}/11111111-1111-4111-8111-111111111111", headers=live_exam["h"]).json()
    assert detail["events"][0]["kind"] == "TAB_HIDDEN" and detail["can_decide"] is True
    assert client.get(f"{base}/99999999-9999-4999-8999-999999999999", headers=live_exam["h"]).status_code == 404   # a student outside the exam's assignments
    assert client.get(f"{base}/00000000-0000-4000-8000-000000000000", headers=live_exam["h"]).status_code == 404


def test_only_the_platform_or_the_students_provider_admin_decides_and_it_is_audited(client, world, live_exam, fake, make_staff, login, db_factory, pa):  # noqa: F811
    from institute_common.models.staff import AuditLog
    url = f"{API}/exams/{live_exam['exam_id']}/integrity/11111111-1111-4111-8111-111111111111/decision"
    _, branch_admin = make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b1"])])
    r = client.post(url, json={"decision": "RETAKE", "reason": "looks innocent"}, headers=login(branch_admin))
    assert r.status_code == 403 and fake.decisions == []                         # branch admins investigate, they do not decide
    h = login(pa)                                                                # back to the provider admin's session
    ok = client.post(url, json={"decision": "RETAKE", "reason": "Verified an OS update prompt."}, headers=h)
    assert ok.status_code == 200 and ok.json()["status"] == "RETAKE_AUTHORIZED", ok.text
    assert fake.decisions[0][1]["reason"] == "Verified an OS update prompt." and fake.decisions[0][1]["staff_name"]
    again = client.post(url, json={"decision": "UPHOLD", "reason": "changed my mind"}, headers=h)
    assert again.status_code == 409 and again.json()["detail"]["code"] == "ALREADY_DECIDED"
    with db_factory() as db:
        assert db.query(AuditLog).filter(AuditLog.action == "exam.integrity.decide").count() == 1


def test_exam_list_search_and_full_results_carry_the_subject_breakdown(client, live_exam):  # noqa: F811
    from institute_common.clients.result_client import set_result_client
    from tests.test_exams import FakeResults
    branch = client.get(f"{API}/exams/{live_exam['exam_id']}", headers=live_exam["h"]).json()["branch_id"]
    name = client.get(f"{API}/exams/{live_exam['exam_id']}", headers=live_exam["h"]).json()["name"]
    hits = client.get(f"{API}/branches/{branch}/exams", params={"q": name[:5].upper()}, headers=live_exam["h"]).json()
    assert [x["exam_id"] for x in hits["items"]] == [live_exam["exam_id"]]                  # case-insensitive name search
    assert client.get(f"{API}/branches/{branch}/exams", params={"q": "no such exam"}, headers=live_exam["h"]).json()["total"] == 0

    class Results(FakeResults):
        def exam_subject_results(self, test_id):
            return [{"student_id": "STU000001", "subjects": [{"subject": "Physics", "obtained_marks": 4, "total_marks": 8}]}]
    set_result_client(Results([{"student_id": "STU000001", "total_marks": 8, "obtained_marks": 4, "attempted_questions": 2, "correct_answers": 1,
                                "incorrect_answers": 0, "unanswered_questions": 0, "accuracy": 50.0, "submitted_at": "x"}]))
    try:
        rows = client.get(f"{API}/exams/{live_exam['exam_id']}/results", headers=live_exam["h"]).json()["results"]
    finally:
        set_result_client(None)
    assert rows[0]["subjects"] == [{"subject": "Physics", "obtained_marks": 4, "total_marks": 8}]
