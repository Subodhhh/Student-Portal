"""Student inbox notifications and assignment-status sync (written before the implementation)."""
from datetime import timedelta
from uuid import UUID

import pytest

from app.core.roles import Role
from app.database import utcnow
from app.services import assignment_sync, outbox, student_notifications
from app.services.internal_http import InternalRejected, InternalServiceError
from tests.test_exams import API, FakePublisher, create_plan, ctx, make_paper, pa  # noqa: F401  (fixtures)


class FakeNotifier:
    def __init__(self, errors=()):
        self.errors, self.sent = list(errors), []

    def send(self, *, dedupe_key, event_type, title, message, metadata, student_ids):
        if self.errors:
            raise self.errors.pop(0)
        self.sent.append({"dedupe_key": dedupe_key, "event_type": event_type, "title": title, "message": message, "metadata": metadata, "student_ids": list(student_ids)})


class FakeAttempts:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.asked = []

    def exam_attempts(self, test_id):
        self.asked.append(test_id)
        return self.rows


class FakeResults:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def exam_results(self, test_id):
        return self.rows


def attempt(student, status):
    return {"student_id": student, "attempt_id": "a-" + student, "status": status, "started_at": "x", "expires_at": "x", "submitted_at": None}


@pytest.fixture()
def live_exam(client, world, ctx, pa, login, db_factory):  # noqa: F811
    """A generated + delivered exam whose window opened an hour ago and closes in five hours; STU000001/2 are assigned."""
    h = login(pa)
    now = utcnow()
    with db_factory() as db:
        from app.models.papers import PaperVersion
        db.get(PaperVersion, ctx["chem_v1"]).status = "PUBLISHED"
        db.commit()
    plan = create_plan(client, h, world, ctx, start_at=(now - timedelta(hours=1)).isoformat(), end_at=(now + timedelta(hours=5)).isoformat())
    job = client.post(f"{API}/exam-plans/{plan['plan_id']}/generate", json={}, headers=h).json()
    with db_factory() as db:
        assert outbox.deliver_due(db, FakePublisher())["sent"] == 1
        from sqlalchemy import select
        from app.models.exams import ExamVersion
        ev = db.scalar(select(ExamVersion).where(ExamVersion.exam_id == UUID(job["exam_id"])))
        return {"exam_id": job["exam_id"], "ev": ev.exam_version_id, "end_at": ev.end_at, "h": h, "now": now}


def statuses(db_factory):
    from app.models.exams import StudentExamAssignment
    with db_factory() as db:
        return {a.student_id: a.status for a in db.query(StudentExamAssignment)}


# ------------------------------------------------------------ inbox notifications

def test_delivering_an_exam_queues_one_student_notification(live_exam, db_factory):
    from app.models.exams import StudentNotificationOutbox
    with db_factory() as db:
        rows = db.query(StudentNotificationOutbox).all()
        assert len(rows) == 1
        r = rows[0]
        assert r.event_type == "TEST_SCHEDULED" and sorted(r.student_ids) == ["STU000001", "STU000002"] and r.status == "PENDING"
        assert "Weekly Test" in r.title and "180" in r.message and "IST" in r.message       # window shown to students in IST
        assert outbox.deliver_due(db, FakePublisher())["sent"] == 0                         # replay does not enqueue again
        assert db.query(StudentNotificationOutbox).count() == 1


def test_notification_delivery_is_confirmed_idempotent_and_retried(live_exam, db_factory):
    from app.models.exams import StudentNotificationOutbox
    notifier = FakeNotifier([InternalServiceError("503")])
    with db_factory() as db:
        assert student_notifications.deliver_due(db, notifier) == {"sent": 0, "retry": 1, "failed": 0}
        row = db.query(StudentNotificationOutbox).one()
        assert row.status == "PENDING" and row.attempts == 1 and row.next_attempt_at > utcnow()
        assert student_notifications.deliver_due(db, notifier)["sent"] == 0                 # backoff: not due yet
        assert student_notifications.deliver_due(db, notifier, now=utcnow() + timedelta(hours=2)) == {"sent": 1, "retry": 0, "failed": 0}
        assert student_notifications.deliver_due(db, notifier, now=utcnow() + timedelta(hours=9))["sent"] == 0   # SENT is final
    assert len(notifier.sent) == 1 and notifier.sent[0]["event_type"] == "TEST_SCHEDULED"
    assert notifier.sent[0]["dedupe_key"].startswith("exam-published:")


def test_permanent_rejection_alerts_staff_instead_of_retrying(live_exam, db_factory):
    from app.models.exams import Notification, StudentNotificationOutbox
    with db_factory() as db:
        assert student_notifications.deliver_due(db, FakeNotifier([InternalRejected(422)])) == {"sent": 0, "retry": 0, "failed": 1}
        assert db.query(StudentNotificationOutbox).one().status == "FAILED"
        assert db.query(Notification).filter_by(kind="STUDENT_NOTIFICATION_FAILED").count() >= 1


def test_cancelling_a_delivered_exam_tells_students(live_exam, client, db_factory):
    with db_factory() as db:
        student_notifications.deliver_due(db, FakeNotifier())
    r = client.post(f"{API}/exams/{live_exam['exam_id']}/cancel", json={"reason": "paper error found"}, headers=live_exam["h"])
    assert r.status_code == 200
    with db_factory() as db:
        assert outbox.deliver_due(db, FakePublisher())["sent"] == 1                         # the CANCEL reaches the student services
        notifier = FakeNotifier()
        student_notifications.deliver_due(db, notifier)
        cancelled = [m for m in notifier.sent if m["dedupe_key"].startswith("exam-cancelled:")]
        assert len(cancelled) == 1 and cancelled[0]["event_type"] == "SYSTEM_ANNOUNCEMENT" and "cancel" in cancelled[0]["title"].lower()


def test_enqueue_is_idempotent_by_dedupe_key(db_factory):
    from app.models.exams import StudentNotificationOutbox
    with db_factory() as db:
        kw = dict(dedupe_key="k-12345678", event_type="SYSTEM_ANNOUNCEMENT", title="t", message="m", details=None, student_ids=["STU1"])
        assert student_notifications.enqueue(db, **kw) is True
        db.commit()
        assert student_notifications.enqueue(db, **kw) is False
        db.commit()
        assert db.query(StudentNotificationOutbox).count() == 1


# ------------------------------------------------------------ assignment status sync

def test_sync_marks_started_completed_and_keeps_waiting_while_window_is_open(live_exam, db_factory):
    fake = FakeAttempts([attempt("STU000001", "SUBMITTED"), attempt("STU000002", "IN_PROGRESS")])
    with db_factory() as db:
        out = assignment_sync.sync_exam(db, live_exam["exam_id"], fake, now=live_exam["now"])
    assert out == {"started": 1, "completed": 1, "missed": 0}
    assert statuses(db_factory) == {"STU000001": "COMPLETED", "STU000002": "STARTED"}
    assert fake.asked == [str(live_exam["ev"])]                                              # asked by the exam version (= test id)


def test_unstarted_assignments_become_missed_only_after_the_window_closes(live_exam, db_factory):
    with db_factory() as db:
        assignment_sync.sync_exam(db, live_exam["exam_id"], FakeAttempts([attempt("STU000001", "SUBMITTED")]), now=live_exam["end_at"] - timedelta(minutes=1))
    assert statuses(db_factory) == {"STU000001": "COMPLETED", "STU000002": "ASSIGNED"}        # window still open: not missed
    with db_factory() as db:
        out = assignment_sync.sync_exam(db, live_exam["exam_id"], FakeAttempts([attempt("STU000001", "SUBMITTED")]), now=live_exam["end_at"] + timedelta(minutes=1))
    assert out["missed"] == 1 and statuses(db_factory) == {"STU000001": "COMPLETED", "STU000002": "MISSED"}


def test_a_student_who_started_inside_the_window_is_never_missed_even_after_it_closes(live_exam, db_factory):
    after = live_exam["end_at"] + timedelta(hours=2)
    with db_factory() as db:
        assignment_sync.sync_exam(db, live_exam["exam_id"], FakeAttempts([attempt("STU000001", "IN_PROGRESS"), attempt("STU000002", "AUTO_SUBMITTED")]), now=after)
    assert statuses(db_factory) == {"STU000001": "STARTED", "STU000002": "COMPLETED"}        # attempt ran past the window end: full duration honoured


def test_sync_is_idempotent_monotonic_and_ignores_foreign_attempts(live_exam, db_factory):
    rows = [attempt("STU000001", "SUBMITTED"), attempt("STU999999", "SUBMITTED")]            # STU999999 was never assigned
    with db_factory() as db:
        assert assignment_sync.sync_exam(db, live_exam["exam_id"], FakeAttempts(rows), now=live_exam["now"])["completed"] == 1
        assert assignment_sync.sync_exam(db, live_exam["exam_id"], FakeAttempts(rows), now=live_exam["now"]) == {"started": 0, "completed": 0, "missed": 0}
        # an out-of-date read that still shows IN_PROGRESS must not move a COMPLETED assignment backwards
        assignment_sync.sync_exam(db, live_exam["exam_id"], FakeAttempts([attempt("STU000001", "IN_PROGRESS")]), now=live_exam["now"])
    assert statuses(db_factory)["STU000001"] == "COMPLETED" and "STU999999" not in statuses(db_factory)


def test_sync_skips_cancelled_exams_and_reports_an_unavailable_attempt_service(live_exam, client, db_factory):
    class Down:
        def exam_attempts(self, t):
            raise InternalServiceError("down")
    with db_factory() as db:
        with pytest.raises(InternalServiceError):
            assignment_sync.sync_exam(db, live_exam["exam_id"], Down(), now=live_exam["now"])
    client.post(f"{API}/exams/{live_exam['exam_id']}/cancel", json={"reason": "cancelled for test"}, headers=live_exam["h"])
    fake = FakeAttempts([attempt("STU000001", "SUBMITTED")])
    with db_factory() as db:
        assert assignment_sync.sync_exam(db, live_exam["exam_id"], fake, now=live_exam["now"]) == {"started": 0, "completed": 0, "missed": 0}
    assert fake.asked == [] and statuses(db_factory) == {"STU000001": "ASSIGNED", "STU000002": "ASSIGNED"}


# ------------------------------------------------------------ result notifications

def test_each_student_is_told_once_when_their_result_exists(live_exam, db_factory):
    from app.models.exams import StudentNotificationOutbox
    now = live_exam["now"]
    with db_factory() as db:
        assignment_sync.sync_exam(db, live_exam["exam_id"], FakeAttempts([attempt("STU000001", "SUBMITTED"), attempt("STU000002", "SUBMITTED")]), now=now)
        # STU000002's evaluation has not produced a result row yet
        assert assignment_sync.notify_results(db, FakeResults([{"student_id": "STU000001", "obtained_marks": 9}]), now=now) == 1
        assert assignment_sync.notify_results(db, FakeResults([{"student_id": "STU000001", "obtained_marks": 9}]), now=now) == 0   # not twice
        assert assignment_sync.notify_results(db, FakeResults([{"student_id": "STU000001", "obtained_marks": 9}, {"student_id": "STU000002", "obtained_marks": 4}]), now=now) == 1
        rows = db.query(StudentNotificationOutbox).filter(StudentNotificationOutbox.event_type == "RESULT_PUBLISHED").all()
        assert sorted(sid for r in rows for sid in r.student_ids) == ["STU000001", "STU000002"]
        assert all("marks" not in r.message.lower() for r in rows)                          # the inbox never carries scores


def test_no_result_notification_for_students_who_have_not_completed(live_exam, db_factory):
    with db_factory() as db:
        assignment_sync.sync_exam(db, live_exam["exam_id"], FakeAttempts([attempt("STU000001", "IN_PROGRESS")]), now=live_exam["now"])
        assert assignment_sync.notify_results(db, FakeResults([{"student_id": "STU000001", "obtained_marks": 1}]), now=live_exam["now"]) == 0


# ------------------------------------------------------------ manual sync endpoint and tenant isolation

def test_sync_endpoint_runs_with_the_configured_clients_and_is_tenant_scoped(client, world, live_exam, make_staff, login):
    from app.services.attempt_client import set_attempt_client
    set_attempt_client(FakeAttempts([attempt("STU000001", "SUBMITTED")]))
    try:
        r = client.post(f"{API}/exams/{live_exam['exam_id']}/sync", headers=live_exam["h"])
        assert r.status_code == 200 and r.json()["completed"] == 1
        for role, scope in ((Role.PROVIDER_ADMIN, (world["p2"], None)), (Role.BRANCH_ADMIN, (world["p1"], world["p1b2"]))):
            _, code = make_staff([(role, *scope)])
            assert client.post(f"{API}/exams/{live_exam['exam_id']}/sync", headers=login(code)).status_code == 403
        assert client.post(f"{API}/exams/{live_exam['exam_id']}/sync", headers=login(live_exam_teacher(make_staff, world))).status_code == 403
    finally:
        set_attempt_client(None)


def live_exam_teacher(make_staff, world):
    return make_staff([(Role.TEACHER, world["p1"], None)])[1]


def test_sync_endpoint_reports_unavailable_service(client, live_exam):
    r = client.post(f"{API}/exams/{live_exam['exam_id']}/sync", headers=live_exam["h"])        # no attempt service configured
    assert r.status_code == 503 and r.json()["detail"]["code"] == "STUDENT_SERVICE_UNAVAILABLE"


# ------------------------------------------------------------ worker wiring

def test_worker_run_once_wires_delivery_sync_and_notifications(live_exam, db_factory, monkeypatch):
    from app import worker
    from app.services.attempt_client import set_attempt_client
    from app.services.notification_client import set_notification_client
    from app.services.result_client import set_result_client
    from app.services.outbox import set_publisher
    notifier = FakeNotifier()
    set_attempt_client(FakeAttempts([attempt("STU000001", "SUBMITTED")]))
    set_result_client(FakeResults([{"student_id": "STU000001", "obtained_marks": 9}]))
    set_notification_client(notifier)
    set_publisher(FakePublisher())
    monkeypatch.setattr(worker, "SessionLocal", db_factory)
    try:
        out = worker.run_once()
    finally:
        for s in (set_attempt_client, set_result_client, set_notification_client, set_publisher):
            s(None)
    assert {m["event_type"] for m in notifier.sent} == {"TEST_SCHEDULED", "RESULT_PUBLISHED"}
    assert statuses(db_factory)["STU000001"] == "COMPLETED"
    assert out["sync"]["completed"] == 1 and out["student_notifications"]["sent"] == 2
