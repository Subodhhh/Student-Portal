"""Test -> Weekly Test: one month of recurring tests planned at once. Deterministic occurrences (local time across DST), idempotent
creation and generation (retries and repeated requests never duplicate a test), the Daily Test's scope and paper rules, generation
through the normal generator (missing papers notified, late approvals used next time), recorded and notified failures, and
cancellation that leaves generated exams untouched."""
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

import pytest

from institute_common.database import utcnow
from inst_exam.routes import test_series as series_mod
from inst_exam.services import exam_generation as gen
from tests.test_create_test import aud, ct  # noqa: F401  (fixtures and helpers)
from tests.test_exams import make_paper
from tests.test_test_creation import tc  # noqa: F401  (fixture)

API = "/api/v1"


def next_month(months_ahead: int = 1) -> str:
    today = utcnow().date()
    y, m = today.year, today.month + months_ahead
    while m > 12:
        y, m = y + 1, m - 12
    return f"{y:04d}-{m:02d}"


def saturdays(month: str) -> list[date]:
    y, m = (int(x) for x in month.split("-"))
    d, out = date(y, m, 1), []
    while d.month == m:
        if d.weekday() == 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def series(world, ct, **over):  # noqa: F811
    b = {"audience": aud(world, ct, batches=("batch_a",)), "name": "  Saturday   Physics ", "subjects": [{"subject_id": str(ct["phy"]), "paper_id": str(ct["phy_paper"])}],
         "month": next_month(), "weekdays": [5], "start_time": "10:00", "timezone": "Asia/Kolkata", "window_minutes": 120, "duration_minutes": 60,
         "generate_lead_minutes": 60, "marking_mode": "UNIFORM", "marks_correct": 4, "marks_wrong": 1}
    b.update(over)
    return b


def create(client, h, body, key="weekly-request-0001"):
    return client.post(f"{API}/test-series", json=body, headers={**h, "Idempotency-Key": key})


def plans(db_factory, series_id=None):
    from institute_common.models.exams import ExamPlan
    with db_factory() as db:
        q = db.query(ExamPlan).filter(ExamPlan.series_id.is_not(None))
        if series_id:
            q = q.filter(ExamPlan.series_id == UUID(str(series_id)))
        return sorted(q.all(), key=lambda p: p.start_at)


# ------------------------------------------------------------------ recurrence

def test_occurrences_keep_the_local_time_across_daylight_saving():
    occ = series_mod.occurrences("2027-03", [6], "10:00", "America/New_York", 90)       # US DST starts Sunday 14 March 2027
    starts = {d.isoformat(): s for d, s, _e in occ}
    assert starts["2027-03-07"] == datetime(2027, 3, 7, 15, 0, tzinfo=timezone.utc)     # EST (UTC-5)
    assert starts["2027-03-14"] == datetime(2027, 3, 14, 14, 0, tzinfo=timezone.utc)    # EDT (UTC-4)
    assert all(e - s == timedelta(minutes=90) for _d, s, e in occ) and len(occ) == 4


def test_preview_lists_the_dates_and_writes_nothing(client, world, ct, login, db_factory):  # noqa: F811
    body = series(world, ct)
    r = client.post(f"{API}/test-series/preview", json=body, headers=login(ct["pa"]))
    assert r.status_code == 200, r.text
    out = r.json()
    expected = [d.isoformat() for d in saturdays(body["month"])]
    assert [o["date"] for o in out["occurrences"]] == expected and out["ok"] and out["eligible_count"] == 2
    first = datetime.fromisoformat(out["occurrences"][0]["start_at"])
    assert (first.hour, first.minute) == (4, 30)                                          # 10:00 in Asia/Kolkata
    assert plans(db_factory) == []


# ------------------------------------------------------------------ creation, idempotency, generation

def test_a_weekly_test_schedules_every_occurrence_once_and_retries_never_duplicate(client, world, ct, login, db_factory):  # noqa: F811
    from institute_common.models.exams import ExamPlanBatch, ExamPlanSubject, TestSeries
    h = login(ct["pa"])
    body = series(world, ct)
    r = create(client, h, body)
    assert r.status_code == 201, r.text
    s = r.json()
    n = len(saturdays(body["month"]))
    assert s["name"] == "Saturday Physics" and s["scheduled_count"] == n == len(s["generation"]["created"]) and s["missing_occurrences"] == []
    assert {t["test_type"] for t in s["tests"]} == {"WEEKLY"} and all(t["state"] == "NOT_PUBLISHED" for t in s["tests"])
    rows = plans(db_factory, s["series_id"])
    with db_factory() as db:
        for plan in rows:
            assert plan.audience_type == "TARGETED" and plan.auto_publish and plan.status == "ACTIVE"
            assert plan.generate_at == plan.start_at - timedelta(minutes=60) and plan.end_at - plan.start_at == timedelta(minutes=120)
            assert [b.batch_id for b in db.query(ExamPlanBatch).filter_by(plan_id=plan.plan_id)] == [ct["batch_a"]]
            [slot] = db.query(ExamPlanSubject).filter_by(plan_id=plan.plan_id).all()
            assert slot.paper_id == ct["phy_paper"] and slot.paper_version_id is None       # latest published version at generation time
    # the same request again, and an explicit "generate missing tests": nothing new
    again = create(client, login(ct["pa"]), body)
    assert again.status_code == 201 and again.json()["series_id"] == s["series_id"] and again.json()["generation"]["created"] == []
    retry = client.post(f"{API}/test-series/{s['series_id']}/generate", headers=login(ct["pa"]))
    assert retry.status_code == 200 and retry.json()["generation"]["created"] == [] and len(retry.json()["generation"]["existing"]) == n
    assert len(plans(db_factory)) == n
    with db_factory() as db:
        assert db.query(TestSeries).count() == 1
    assert create(client, login(ct["sa"]), body).status_code == 409                       # someone else's request key
    listed = client.get(f"{API}/tests?test_type=WEEKLY", headers=login(ct["pa"])).json()
    assert listed["total"] == n


def test_a_lost_race_finds_the_existing_occurrence(client, world, ct, login, db_factory, monkeypatch):  # noqa: F811
    """Two generations at once: the unique (series_id, start_at) constraint lets only one plan per occurrence exist."""
    from institute_common.models.exams import TestSeries
    s = create(client, login(ct["pa"]), series(world, ct)).json()
    first = plans(db_factory, s["series_id"])[0]
    from institute_common.models.exams import ExamPlanBatch, ExamPlanSubject
    with db_factory() as db:                           # pretend the first occurrence is still missing ...
        for child in (ExamPlanBatch, ExamPlanSubject):
            db.query(child).filter_by(plan_id=first.plan_id).delete()
        db.delete(db.get(type(first), first.plan_id))
        db.commit()
    real = series_mod._new_plan

    def racing(db, srs, a, d, start, end):                # ... and a concurrent request creates it while this one is running
        if start == first.start_at:
            with db_factory() as other:
                real(other, other.get(TestSeries, srs.series_id), a, d, start, end)
                other.commit()
        return real(db, srs, a, d, start, end)
    monkeypatch.setattr(series_mod, "_new_plan", racing)
    r = client.post(f"{API}/test-series/{s['series_id']}/generate", headers=login(ct["pa"]))
    assert r.status_code == 200, r.text
    assert r.json()["generation"]["created"] == [] and len(plans(db_factory, s["series_id"])) == s["scheduled_count"]


def test_each_occurrence_is_generated_by_the_normal_generator(client, world, ct, login, db_factory):  # noqa: F811
    """At generate_at the worker generates and publishes the occurrence's exam; a subject whose paper has no published version is
    left out and notified; once approved, the NEXT occurrence uses it; the earlier exam never changes."""
    from institute_common.models.exams import Exam, ExamNotificationOutbox, ExamVersion, ExamVersionPaper
    late_paper, late_v1 = make_paper(db_factory, world["p1"], world["p1b1"], ct["chem"], "Chem weekly", "VALIDATED", n=2)
    body = series(world, ct, subjects=[{"subject_id": str(ct["phy"]), "paper_id": str(ct["phy_paper"])},
                                       {"subject_id": str(ct["chem"]), "paper_id": str(late_paper)}])
    pre = client.post(f"{API}/test-series/preview", json=body, headers=login(ct["pa"])).json()
    assert any("no published version yet" in w["message"] for w in pre["warnings"])
    s = create(client, login(ct["pa"]), body).json()
    first, second = plans(db_factory, s["series_id"])[:2]
    with db_factory() as db:
        jobs = gen.run_due_plans(db, now=first.generate_at + timedelta(minutes=1))
        assert [str(j.plan_id) for j in jobs] == [str(first.plan_id)] and jobs[0].status == "PARTIAL"
        exam = db.query(Exam).filter_by(plan_id=first.plan_id).one()
        ev = db.query(ExamVersion).filter_by(exam_id=exam.exam_id).one()
        assert exam.status == "PUBLISHED" and [m["subject"] for m in ev.missing_subjects] == ["Chemistry"]
        assert any(o.payload["kind"] == "MISSING_PAPER" for o in db.query(ExamNotificationOutbox))
        first_exam = exam.exam_id
    make_paper(db_factory, world["p1"], world["p1b1"], ct["chem"], "Chem weekly", "PUBLISHED", n=2, paper_id=late_paper, version_no=2)
    with db_factory() as db:
        jobs = gen.run_due_plans(db, now=second.generate_at + timedelta(minutes=1))
        assert [j.status for j in jobs] == ["SUCCEEDED"]
        exam2 = db.query(Exam).filter_by(plan_id=second.plan_id).one()
        ev2 = db.query(ExamVersion).filter_by(exam_id=exam2.exam_id).one()
        assert not ev2.missing_subjects and db.query(ExamVersionPaper).filter_by(exam_version_id=ev2.exam_version_id).count() == 2
        old = db.query(ExamVersion).filter_by(exam_id=first_exam).one()
        assert db.query(ExamVersionPaper).filter_by(exam_version_id=old.exam_version_id).count() == 1     # unchanged
        assert gen.run_due_plans(db, now=second.generate_at + timedelta(minutes=2)) == []                 # worker retry: nothing new


def test_a_generation_failure_is_recorded_notified_and_recovered(client, world, ct, login, db_factory, monkeypatch):  # noqa: F811
    from institute_common.models.exams import ExamNotificationOutbox, TestSeries
    real, calls = series_mod._new_plan, {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("database went away")
        return real(*a, **k)
    monkeypatch.setattr(series_mod, "_new_plan", flaky)
    r = create(client, login(ct["pa"]), series(world, ct))
    assert r.status_code == 502 and r.json()["detail"]["code"] == "GENERATION_FAILED", r.text
    sid = r.json()["detail"]["series_id"]
    assert len(plans(db_factory, sid)) == 1                                                # the occurrence before the failure is kept
    with db_factory() as db:
        assert "database went away" in db.get(TestSeries, UUID(sid)).last_generation_error
        assert db.query(ExamNotificationOutbox).filter(ExamNotificationOutbox.dedupe_key.like(f"seriesfail:{sid}:%")).count() == 1
    detail = client.get(f"{API}/test-series/{sid}", headers=login(ct["pa"])).json()
    assert detail["last_generation_error"] and len(detail["missing_occurrences"]) == detail["planned_count"] - 1
    monkeypatch.setattr(series_mod, "_new_plan", real)
    ok = client.post(f"{API}/test-series/{sid}/generate", headers=login(ct["pa"]))
    assert ok.status_code == 200 and ok.json()["last_generation_error"] is None and ok.json()["missing_occurrences"] == []
    assert len(plans(db_factory, sid)) == ok.json()["planned_count"]


def test_cancelling_stops_future_occurrences_and_keeps_generated_exams(client, world, ct, login, db_factory):  # noqa: F811
    from institute_common.models.exams import Exam, ExamPlan
    s = create(client, login(ct["pa"]), series(world, ct)).json()
    first = plans(db_factory, s["series_id"])[0]
    with db_factory() as db:
        gen.run_due_plans(db, now=first.generate_at + timedelta(minutes=1))
    h = login(ct["pa"])
    assert client.post(f"{API}/test-series/{s['series_id']}/cancel", json={"reason": ""}, headers=h).status_code == 422
    r = client.post(f"{API}/test-series/{s['series_id']}/cancel", json={"reason": "Holiday month"}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "CANCELLED" and r.json()["stopped"] == s["scheduled_count"] - 1
    with db_factory() as db:
        assert db.get(ExamPlan, first.plan_id).status == "ACTIVE" and db.query(Exam).filter_by(plan_id=first.plan_id).one().status == "PUBLISHED"
    assert client.post(f"{API}/test-series/{s['series_id']}/generate", headers=h).status_code == 409
    assert client.post(f"{API}/test-series/{s['series_id']}/cancel", json={"reason": "again"}, headers=h).status_code == 409


# ------------------------------------------------------------------ authorization and validation

def test_only_admins_in_scope_manage_weekly_tests(client, world, ct, login, db_factory):  # noqa: F811
    assert create(client, login(ct["t_batch"]), series(world, ct)).status_code == 403            # teachers never create tests
    assert client.post(f"{API}/test-series/preview", json=series(world, ct), headers=login(ct["pa2"])).status_code == 404   # other provider
    r = create(client, login(ct["ba"]), series(world, ct, audience=aud(world, ct, branches=("p1b2",), batches=("batch_c",))), key="weekly-ba-other")
    assert r.status_code == 422 and r.json()["detail"]["errors"][0]["field"] == "branch_ids"
    mine = create(client, login(ct["ba"]), series(world, ct), key="weekly-ba-own-01")
    assert mine.status_code == 201, mine.text
    sid = mine.json()["series_id"]
    assert client.get(f"{API}/test-series/{sid}", headers=login(ct["pa2"])).status_code == 404
    assert client.post(f"{API}/test-series/{sid}/cancel", json={"reason": "x"}, headers=login(ct["t_batch"])).status_code == 403
    assert client.get(f"{API}/test-series/{sid}", headers=login(ct["t_batch"])).status_code == 200      # teaches Physics in p1-b1
    assert [x["series_id"] for x in client.get(f"{API}/test-series", headers=login(ct["pa"])).json()["items"]] == [sid]


@pytest.mark.parametrize("over,fragment", [
    ({"duration_minutes": 180}, "fit inside the window"),
    ({"timezone": "Mars/Olympus"}, "unknown time zone"),
    ({"weekdays": [5, 5]}, "distinct"),
    ({"weekdays": [7]}, "distinct"),
    ({"month": "2020-01"}, "still in the future"),
    ({"start_time": "25:00"}, "start_time"),
    ({"marking_mode": "UNIFORM", "marks_correct": None}, "uniform marking"),
    ({"window_minutes": 1441}, "window_minutes"),
])
def test_invalid_schedules_are_rejected(client, world, ct, login, db_factory, over, fragment):  # noqa: F811
    r = create(client, login(ct["pa"]), series(world, ct, **over))
    assert r.status_code == 422 and fragment in r.text, r.text
    assert plans(db_factory) == []


def test_papers_the_caller_may_not_use_are_refused(client, world, ct, login, db_factory):  # noqa: F811
    p2_paper, _ = make_paper(db_factory, world["p2"], world["p2b1"], ct["phy"], "Other provider", "PUBLISHED")
    r = create(client, login(ct["pa"]), series(world, ct, subjects=[{"subject_id": str(ct["phy"]), "paper_id": str(p2_paper)}]))
    assert r.status_code == 422 and "not available" in r.text
    r = create(client, login(ct["pa"]), series(world, ct, subjects=[{"subject_id": str(ct["chem"]), "paper_id": str(ct["phy_paper"])}]), key="weekly-mismatch-1")
    assert r.status_code == 422 and "does not belong to this subject" in r.text
    assert plans(db_factory) == []


def test_overlapping_tests_are_reported_as_conflicts(client, world, ct, login):  # noqa: F811
    body = series(world, ct)
    first = create(client, login(ct["pa"]), body).json()
    pre = client.post(f"{API}/test-series/preview", json={**body, "name": "Second series"}, headers=login(ct["pa"])).json()
    assert pre["ok"] and len(pre["conflicts"]) == first["scheduled_count"]
