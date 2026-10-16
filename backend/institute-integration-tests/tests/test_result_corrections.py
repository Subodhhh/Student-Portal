"""Result correction and re-evaluation: authorization, idempotency, immutable history, audit (tests first)."""
import pytest

from institute_common.core.roles import Role
from institute_common.internal_http import InternalRejected, InternalServiceError
from institute_common.clients.result_client import ResultRejected, ResultServiceError
from tests.test_exams import API, create_plan, ctx, pa  # noqa: F401  (fixtures)
from tests.test_student_integration import FakeAttempts, attempt, live_exam  # noqa: F401  (fixtures)

KEY = "Idempotency-Key"


class FakeCorrectionResults:
    """Stands in for the Student result service."""

    def __init__(self):
        self.marks = {"STU000001": 7, "STU000002": 4}
        self.calls, self.fail_next, self.reject_next = [], 0, None
        self.reeval_changed = ["STU000001"]
        self.reeval_down = 0

    def exam_results(self, test_id):
        return [{"student_id": s, "obtained_marks": m, "total_marks": 24, "accuracy": 50.0} for s, m in self.marks.items()]

    def adjust(self, *, test_id, student_id, obtained_marks, reason, actor, correction_ref):
        self.calls.append(("adjust", test_id, student_id, obtained_marks, reason, actor, correction_ref))
        if self.fail_next:
            self.fail_next -= 1
            raise ResultServiceError("down")
        if self.reject_next:
            raise ResultRejected(self.reject_next)
        previous, self.marks[student_id] = self.marks[student_id], obtained_marks
        return {"previous": {"obtained_marks": previous}, "new": {"obtained_marks": obtained_marks}}

    def reevaluate(self, *, test_id, reason, actor, correction_ref):
        self.calls.append(("reevaluate", test_id, reason, actor, correction_ref))
        if self.reeval_down:
            self.reeval_down -= 1
            raise ResultServiceError("down")
        return {"changed": self.reeval_changed, "unchanged": 1, "skipped_manual": [], "already_applied": 0, "no_questions": 0}


class FakeKeys:
    def __init__(self):
        self.calls, self.fail_next = [], 0

    def set_answer_key(self, *, test_id, changes, reason, actor, correction_ref):
        self.calls.append((test_id, list(changes), reason, actor, correction_ref))
        if self.fail_next:
            self.fail_next -= 1
            raise InternalServiceError("down")
        return {"changes": [{"question_order": c["question_order"], "previous": "A", "new": c["correct"]} for c in changes]}


@pytest.fixture()
def fakes():
    from institute_common.clients.answer_key_client import set_answer_key_client
    from institute_common.clients.result_client import set_result_client
    results, keys = FakeCorrectionResults(), FakeKeys()
    set_result_client(results)
    set_answer_key_client(keys)
    yield results, keys
    set_result_client(None)
    set_answer_key_client(None)


@pytest.fixture()
def super_admin(make_staff, login):
    sid, code = make_staff([(Role.SUPER_ADMIN, None, None)])
    return {"id": sid, "code": code, "h": lambda: login(code)}


def adjust(client, exam_id, student, marks, h, key="adj-key-0001", reason="Re-checked the answer sheet by hand"):
    return client.post(f"{API}/exams/{exam_id}/results/{student}/adjustments", json={"obtained_marks": marks, "reason": reason}, headers={**h, KEY: key})


def key_fix(client, exam_id, h, key="key-fix-0001", changes=None, reason="Printed answer key was wrong for Q2"):
    return client.post(f"{API}/exams/{exam_id}/answer-key-corrections", json={"changes": changes if changes is not None else [{"question_order": 2, "correct_option": "D"}], "reason": reason},
                       headers={**h, KEY: key})


def corrections(db_factory):
    from institute_common.models.exams import ResultCorrection
    with db_factory() as db:
        return db.query(ResultCorrection).order_by(ResultCorrection.created_at).all()


# ------------------------------------------------------------ marks adjustment

def test_super_admin_adjusts_marks_with_full_trail(client, live_exam, fakes, super_admin, db_factory):
    from institute_common.models.exams import StudentNotificationOutbox
    from institute_common.models.staff import AuditLog
    results, _ = fakes
    r = adjust(client, live_exam["exam_id"], "STU000001", 9, super_admin["h"]())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "APPLIED" and body["previous_marks"] == 7 and body["new_marks"] == 9 and body["student_id"] == "STU000001"
    kind, test_id, sid, new, reason, actor, ref = results.calls[0]
    assert (kind, test_id, sid, new, reason) == ("adjust", str(live_exam["ev"]), "STU000001", 9, "Re-checked the answer sheet by hand")
    assert actor == super_admin["code"] and ref == body["correction_id"]
    (c,) = corrections(db_factory)
    assert c.status == "APPLIED" and c.request == {"student_id": "STU000001", "obtained_marks": 9} and c.outcome["previous"]["obtained_marks"] == 7
    with db_factory() as db:
        row = db.query(AuditLog).filter_by(action="result.adjust", outcome="SUCCESS").one()
        assert row.reason == "Re-checked the answer sheet by hand" and row.actor_staff_id == super_admin["id"]
        notices = [n for n in db.query(StudentNotificationOutbox) if n.dedupe_key.startswith("correction:")]
        assert len(notices) == 1 and notices[0].student_ids == ["STU000001"] and "updated" in notices[0].title.lower() and "9" not in notices[0].message


def test_adjustment_is_idempotent_per_key_and_rejects_key_reuse_with_a_different_request(client, live_exam, fakes, super_admin, db_factory):
    results, _ = fakes
    first = adjust(client, live_exam["exam_id"], "STU000001", 9, super_admin["h"]()).json()
    again = adjust(client, live_exam["exam_id"], "STU000001", 9, super_admin["h"]())
    assert again.status_code == 200 and again.json()["correction_id"] == first["correction_id"]
    assert [c[0] for c in results.calls] == ["adjust"] and len(corrections(db_factory)) == 1       # applied once
    clash = adjust(client, live_exam["exam_id"], "STU000001", 11, super_admin["h"]())                # same key, different marks
    assert clash.status_code == 409 and clash.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"
    other_student = adjust(client, live_exam["exam_id"], "STU000002", 9, super_admin["h"]())
    assert other_student.status_code == 409


@pytest.mark.parametrize("who", ["ADMIN", "PROVIDER_ADMIN", "BRANCH_ADMIN", "TEACHER"])
def test_only_super_admin_may_correct_results(client, world, live_exam, fakes, make_staff, login, db_factory, who):
    from institute_common.models.staff import AuditLog
    scope = {"ADMIN": (None, None), "PROVIDER_ADMIN": (world["p1"], None), "BRANCH_ADMIN": (world["p1"], world["p1b1"]), "TEACHER": (world["p1"], None)}[who]
    _, code = make_staff([(Role(who), *scope)])
    h = login(code)
    assert adjust(client, live_exam["exam_id"], "STU000001", 9, h).status_code == 403
    assert key_fix(client, live_exam["exam_id"], h).status_code == 403
    assert fakes[0].calls == [] and fakes[1].calls == [] and corrections(db_factory) == []
    with db_factory() as db:
        assert db.query(AuditLog).filter_by(action="authz.denied", outcome="DENIED").count() >= 2


def test_anonymous_callers_are_rejected(client, live_exam):
    client.cookies.clear()                                   # live_exam logged a Provider Admin in on this client
    assert client.post(f"{API}/exams/{live_exam['exam_id']}/results/STU000001/adjustments", json={"obtained_marks": 1, "reason": "no session here"},
                       headers={"Origin": "http://localhost:5174"}).status_code == 401


@pytest.mark.parametrize("student,marks,reason,key,status,code", [
    ("STU000001", 9, "Re-checked the answer sheet by hand", None, 422, "IDEMPOTENCY_KEY_REQUIRED"),
    ("STU000001", 9, "", "adj-key-0002", 422, "REASON_REQUIRED"),
    ("STU000001", 9, "hm", "adj-key-0003", 422, "REASON_REQUIRED"),
    ("STU000001", 25, "Re-checked the answer sheet by hand", "adj-key-0004", 422, "INVALID_MARKS"),     # above the exam's 24 marks
    ("STU000001", -25, "Re-checked the answer sheet by hand", "adj-key-0005", 422, "INVALID_MARKS"),
    ("STU000001", 7, "Re-checked the answer sheet by hand", "adj-key-0006", 409, "NO_CHANGE"),
    ("STU000003", 5, "Re-checked the answer sheet by hand", "adj-key-0007", 404, "NOT_ASSIGNED"),       # enrolled elsewhere, not assigned to this exam
    ("STU000999", 5, "Re-checked the answer sheet by hand", "adj-key-0008", 404, "NOT_ASSIGNED"),
])
def test_invalid_adjustments_never_reach_the_student_service(client, live_exam, fakes, super_admin, db_factory, student, marks, reason, key, status, code):
    h = super_admin["h"]()
    headers = {**h, KEY: key} if key else h
    r = client.post(f"{API}/exams/{live_exam['exam_id']}/results/{student}/adjustments", json={"obtained_marks": marks, "reason": reason}, headers=headers)
    assert r.status_code == status and r.json()["detail"]["code"] == code
    assert fakes[0].calls == [] and corrections(db_factory) == []


def test_exam_without_results_for_the_student_is_refused(client, live_exam, fakes, super_admin):
    del fakes[0].marks["STU000002"]
    r = adjust(client, live_exam["exam_id"], "STU000002", 3, super_admin["h"](), key="adj-key-0009")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "NO_RESULT"


def test_unpublished_or_cancelled_exam_cannot_be_corrected(client, live_exam, fakes, super_admin):
    client.post(f"{API}/exams/{live_exam['exam_id']}/cancel", json={"reason": "cancelled for the test"}, headers=live_exam["h"])
    r = adjust(client, live_exam["exam_id"], "STU000001", 9, super_admin["h"]())
    assert r.status_code == 409 and r.json()["detail"]["code"] == "NOT_PUBLISHED"


def test_student_service_outage_leaves_a_failed_record_and_a_retry_with_the_same_key_completes_it(client, live_exam, fakes, super_admin, db_factory):
    results, _ = fakes
    results.fail_next = 1
    r = adjust(client, live_exam["exam_id"], "STU000001", 9, super_admin["h"]())
    assert r.status_code == 503 and r.json()["detail"]["code"] == "CORRECTION_PENDING" and r.json()["detail"]["correction_id"]
    (c,) = corrections(db_factory)
    assert c.status == "FAILED" and "down" in c.error
    ok = adjust(client, live_exam["exam_id"], "STU000001", 9, super_admin["h"]())                    # retry, same key
    assert ok.status_code == 200 and ok.json()["correction_id"] == str(c.correction_id) and ok.json()["status"] == "APPLIED"
    assert len(corrections(db_factory)) == 1 and [x[0] for x in results.calls] == ["adjust", "adjust"]
    assert results.calls[0][-1] == results.calls[1][-1]                                              # same correction_ref: the receiver dedupes


def test_rejection_by_the_student_service_is_recorded_not_retried_blindly(client, live_exam, fakes, super_admin, db_factory):
    fakes[0].reject_next = 409
    r = adjust(client, live_exam["exam_id"], "STU000001", 9, super_admin["h"]())
    assert r.status_code == 409 and r.json()["detail"]["code"] == "CORRECTION_REJECTED"
    assert corrections(db_factory)[0].status == "FAILED"


# ------------------------------------------------------------ answer key correction + re-evaluation

def test_answer_key_correction_updates_the_key_then_reevaluates_and_notifies_changed_students(client, live_exam, fakes, super_admin, db_factory):
    from institute_common.models.exams import StudentNotificationOutbox
    from institute_common.models.staff import AuditLog
    results, keys = fakes
    r = key_fix(client, live_exam["exam_id"], super_admin["h"](), changes=[{"question_order": 2, "correct_option": "D"}, {"question_order": 5, "correct_option": "B"}])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "APPLIED" and body["reevaluation"] == {"changed": ["STU000001"], "unchanged": 1, "skipped_manual": [], "already_applied": 0, "no_questions": 0}
    assert [c["question_order"] for c in body["key_changes"]] == [2, 5] and body["key_changes"][0]["previous"] == "A"
    assert keys.calls[0][1] == [{"question_order": 2, "correct": "D"}, {"question_order": 5, "correct": "B"}]
    assert [c[0] for c in results.calls] == ["reevaluate"] and results.calls[0][1] == str(live_exam["ev"])
    with db_factory() as db:
        assert db.query(AuditLog).filter_by(action="result.answer_key_correction", outcome="SUCCESS").one().reason == "Printed answer key was wrong for Q2"
        notices = [n for n in db.query(StudentNotificationOutbox) if n.dedupe_key.startswith("correction:")]
        assert len(notices) == 1 and notices[0].student_ids == ["STU000001"]                           # only students whose result changed


def test_key_correction_is_idempotent_and_survives_a_failure_between_the_two_steps(client, live_exam, fakes, super_admin, db_factory):
    results, keys = fakes
    results.reeval_down = 1
    r = key_fix(client, live_exam["exam_id"], super_admin["h"]())
    assert r.status_code == 503 and r.json()["detail"]["code"] == "CORRECTION_PENDING"
    (c,) = corrections(db_factory)
    assert c.status == "FAILED" and c.outcome["key_changes"]                                           # step 1 is on record
    ok = key_fix(client, live_exam["exam_id"], super_admin["h"]())
    assert ok.status_code == 200 and ok.json()["status"] == "APPLIED" and len(corrections(db_factory)) == 1
    assert len(keys.calls) == 2 and keys.calls[0][-1] == keys.calls[1][-1]                              # key step repeated idempotently
    third = key_fix(client, live_exam["exam_id"], super_admin["h"]())
    assert third.status_code == 200 and len(keys.calls) == 2                                            # APPLIED => replay does nothing


@pytest.mark.parametrize("changes", [[], [{"question_order": 0, "correct_option": "A"}], [{"question_order": 1, "correct_option": "Z"}],
                                     [{"question_order": 1, "correct_option": "A"}, {"question_order": 1, "correct_option": "B"}]])
def test_invalid_key_corrections_are_refused(client, live_exam, fakes, super_admin, changes):
    r = key_fix(client, live_exam["exam_id"], super_admin["h"](), changes=changes)
    assert r.status_code == 422 and fakes[1].calls == []


def test_key_correction_rejected_by_the_test_service_is_reported(client, live_exam, fakes, super_admin, db_factory):
    def reject(**kw):
        raise InternalRejected(404, "Questions not found")
    fakes[1].set_answer_key = reject
    r = key_fix(client, live_exam["exam_id"], super_admin["h"]())
    assert r.status_code == 422 and r.json()["detail"]["code"] == "CORRECTION_REJECTED"
    assert corrections(db_factory)[0].status == "FAILED" and fakes[0].calls == []                       # no re-evaluation after a refused key change


# ------------------------------------------------------------ history view

def test_history_is_listed_for_authorized_staff_only(client, world, live_exam, fakes, super_admin, make_staff, login):
    adjust(client, live_exam["exam_id"], "STU000001", 9, super_admin["h"]())
    key_fix(client, live_exam["exam_id"], super_admin["h"]())
    rows = client.get(f"{API}/exams/{live_exam['exam_id']}/result-corrections", headers=super_admin["h"]()).json()
    assert [r["kind"] for r in rows] == ["ANSWER_KEY", "MARKS_ADJUSTMENT"] and all(r["reason"] and r["actor"] for r in rows)
    _, pa_code = make_staff([(Role.PROVIDER_ADMIN, world["p1"], None)])
    assert client.get(f"{API}/exams/{live_exam['exam_id']}/result-corrections", headers=login(pa_code)).status_code == 200      # own provider
    _, other = make_staff([(Role.PROVIDER_ADMIN, world["p2"], None)])
    assert client.get(f"{API}/exams/{live_exam['exam_id']}/result-corrections", headers=login(other)).status_code == 403
    _, teacher = make_staff([(Role.TEACHER, world["p1"], None)])
    assert client.get(f"{API}/exams/{live_exam['exam_id']}/result-corrections", headers=login(teacher)).status_code == 403


def test_a_stored_correction_cannot_be_rewritten_through_the_api(client, live_exam, fakes, super_admin, db_factory):
    first = adjust(client, live_exam["exam_id"], "STU000001", 9, super_admin["h"]()).json()
    adjust(client, live_exam["exam_id"], "STU000001", 11, super_admin["h"](), reason="A completely different reason")    # conflict, ignored
    (c,) = corrections(db_factory)
    assert str(c.correction_id) == first["correction_id"] and c.request["obtained_marks"] == 9 and c.reason == "Re-checked the answer sheet by hand"
