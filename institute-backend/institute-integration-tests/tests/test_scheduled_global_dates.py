"""Test -> Scheduled Tests lists a Global Test date from the moment it is published, before any paper, deadline or generation: the date
is listed on its own until its exam plan exists (made at generation), then by that plan, never both."""
from tests.test_global_contributions import client, login  # noqa: F401  (one session per staff member)
from tests.test_global_schedule import API, aud, create_test, gs, next_months, published, run_generation, submit  # noqa: F401

PLAN = {"weekdays": [5], "start_time": "10:00", "timezone": "Asia/Kolkata", "window_minutes": 180}


def scheduled(client, login, who, **query):
    r = client.get(f"{API}/tests", params={"page_size": 100, **query}, headers=login(who))
    assert r.status_code == 200, r.text
    return r.json()


def dates_of(listing, test_id):
    return [x for x in listing["items"] if x["global_test_id"] == test_id]


def test_a_published_date_is_listed_at_once_for_super_admin_and_admin(client, world, gs, login):
    t, first = published(client, login, gs)
    occurrences = client.get(f"{API}/global-tests/{t['test_id']}/occurrences", headers=login(gs["sa"])).json()["items"]
    for who in ("sa", "admin"):
        for query in ({}, {"test_type": "GLOBAL"}):
            rows = dates_of(scheduled(client, login, gs[who], **query), t["test_id"])
            assert sorted(x["occurrence_id"] for x in rows) == sorted(o["occurrence_id"] for o in occurrences)   # each date exactly once
            row = next(x for x in rows if x["occurrence_id"] == first["occurrence_id"])
            assert row["plan_id"] is None and row["exam_id"] is None and row["test_type"] == "GLOBAL" and row["state"] == "SCHEDULED"
            assert (row["name"], row["start_at"], row["end_at"], row["submission_deadline"]) == (
                "National Science Mock", first["start_at"], first["end_at"], first["submission_deadline"])
            assert row["subjects"] == ["Physics", "Chemistry"] and row["duration_minutes"] == 60 and row["provider_name"] == "Global Test"
            assert row["marking"] == {"mode": "UNIFORM", "correct": 4, "wrong": 1} and row["assigned_students"] == 0
    again = dates_of(scheduled(client, login, gs["sa"]), t["test_id"])
    assert len(again) == len(occurrences)                                                           # a refresh adds nothing
    assert dates_of(scheduled(client, login, gs["sa"], test_type="DAILY"), t["test_id"]) == []
    assert dates_of(scheduled(client, login, gs["sa"], provider_id=str(world["p1"])), t["test_id"]) == []
    for who in ("pa", "ba", "tA"):                     # as for a generated date's plan: Scheduled Tests shows Global Test dates to the platform
        assert dates_of(scheduled(client, login, gs[who]), t["test_id"]) == []


def test_draft_tests_and_draft_dates_are_never_listed(client, gs, login):
    t = create_test(client, login, gs)
    h = login(gs["sa"])
    assert client.post(f"{API}/global-tests/{t['test_id']}/occurrences/plan", headers=h, json={"months": next_months(1), **PLAN}).status_code == 200
    assert dates_of(scheduled(client, login, gs["sa"]), t["test_id"]) == []                 # the test and its dates are drafts
    assert client.post(f"{API}/global-tests/{t['test_id']}/publish", headers=h).json()["status"] == "PUBLISHED"
    listed = {x["occurrence_id"] for x in dates_of(scheduled(client, login, gs["sa"]), t["test_id"])}
    assert listed
    more = client.post(f"{API}/global-tests/{t['test_id']}/occurrences/plan", headers=h, json={"months": next_months(2)[1:], **PLAN}).json()
    assert more["created"] > 0                                                                # new dates of a published test start as drafts
    assert {x["occurrence_id"] for x in dates_of(scheduled(client, login, gs["sa"]), t["test_id"])} == listed


def test_a_cancelled_date_keeps_its_row_as_cancelled(client, gs, login):
    t, occ = published(client, login, gs)
    r = client.post(f"{API}/global-occurrences/{occ['occurrence_id']}/cancel", headers=login(gs["sa"]), json={"reason": "Exam centre closed"})
    assert r.status_code == 200, r.text
    [row] = [x for x in dates_of(scheduled(client, login, gs["sa"]), t["test_id"]) if x["occurrence_id"] == occ["occurrence_id"]]
    assert row["state"] == "CANCELLED" and row["error"] == "Exam centre closed"


def test_a_generated_date_is_listed_once_by_its_plan(client, world, gs, login, db_factory):
    t, occ = published(client, login, gs)
    assert submit(client, login, gs["tA"], occ, gs["phy"], gs["paperA"], aud(world, gs, batches=("batch_a",))).status_code == 201
    run_generation(db_factory, t, occ)
    rows = dates_of(scheduled(client, login, gs["sa"]), t["test_id"])
    plan_rows = [x for x in rows if x["plan_id"]]
    assert len(plan_rows) == 1 and plan_rows[0]["exam_id"] and plan_rows[0]["state"] in ("PUBLISHED", "PUBLISHED_DELIVERY_PENDING")
    assert occ["occurrence_id"] not in {x.get("occurrence_id") for x in rows}               # never also as an ungenerated date
    assert all(x["state"] == "SCHEDULED" for x in rows if not x["plan_id"])                  # the later dates are still listed
    listing = scheduled(client, login, gs["sa"])
    assert listing["total"] == len(listing["items"])                                         # the count includes the dates, once
