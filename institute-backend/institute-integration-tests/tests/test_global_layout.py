"""Unified Global Test: every configured subject for every eligible student (persisted per student, with the reason when there is no
paper), student-specific papers across providers (reversed mappings), the per-student layout API and its provider scoping, card-only
exams, a RUNNING generation job, and historical dates. World: tests.test_global_schedule.gs plus, for the reversed mappings, a batch D in
p2-b1 and two students who take Physics with one provider and Chemistry with the other."""
from uuid import UUID

import pytest

from institute_common.core.roles import Role
from inst_exam.services import exam_generation
from tests.test_exams import make_paper
from tests.test_global_contributions import client, login  # noqa: F401
from tests.test_global_schedule import aud, gs, layout_of, published, run_generation, submit  # noqa: F401
from tests.test_global_unified import deliver, exams_by_student

API = "/api/v1"


@pytest.fixture()
def rev(client, world, gs, login, db_factory, make_staff):  # noqa: F811
    """X: Physics with p1 (batch A), Chemistry with p2 (batch D). Y: Chemistry with p1 (batch A), Physics with p2 (batch D).
    The test configures Physics, Chemistry and Biology; nobody here takes Biology."""
    from institute_common.models.academics import EnrollmentBatchSubject, ProviderSubject
    from institute_common.models.org import Batch, Enrollment, EnrollmentBatch
    with db_factory() as db:
        db.add(ProviderSubject(provider_id=world["p2"], subject_id=gs["chem"]))
        d = Batch(branch_id=world["p2b1"], name="D")
        db.add(d)
        db.flush()
        gs["batch_d"] = d.batch_id
        for sid, mine_p1, mine_p2 in (("STU000010", "phy", "chem"), ("STU000011", "chem", "phy")):
            for eid, prov, br, batch, subject in ((f"{sid}-p1", "p1", "p1b1", gs["batch_a"], mine_p1), (f"{sid}-p2", "p2", "p2b1", d.batch_id, mine_p2)):
                db.add(Enrollment(enrollment_id=eid, provider_id=world[prov], branch_id=world[br], student_id=sid))
                db.flush()
                link = EnrollmentBatch(enrollment_id=eid, batch_id=batch)
                db.add(link)
                db.flush()
                db.add(EnrollmentBatchSubject(enrollment_batch_id=link.enrollment_batch_id, subject_id=gs[subject]))
        db.commit()
    _pid, chem2 = make_paper(db_factory, world["p2"], world["p2b1"], gs["chem"], "p2 chemistry", "PUBLISHED", n=2)
    gs["paperChem2"] = str(chem2)
    t, occ = published(client, login, gs, subject_ids=[str(gs["phy"]), str(gs["chem"]), str(gs["bio"])])
    p2_batch = {"provider_id": str(world["p2"]), "branch_ids": [str(world["p2b1"])], "whole_branch_ids": [], "batch_ids": [str(d.batch_id)], "student_ids": []}
    assert submit(client, login, gs["tA"], occ, gs["phy"], gs["paperA"], aud(world, gs, batches=("batch_a",))).status_code == 201
    assert submit(client, login, gs["tC"], occ, gs["chem"], gs["paperC"], aud(world, gs, batches=("batch_a",))).status_code == 201
    assert submit(client, login, gs["tutor"], occ, gs["phy"], gs["paperT"], p2_batch).status_code == 201
    r = submit(client, login, gs["tutor"], occ, gs["chem"], gs["paperChem2"], p2_batch)
    assert r.status_code == 201, r.text
    return t, occ


def test_reversed_provider_mappings_give_each_student_their_own_papers_and_every_configured_subject(client, world, gs, rev, db_factory):
    t, occ = rev
    [run] = run_generation(db_factory, t, occ)
    assert not run["failed"], run
    got = exams_by_student(db_factory, occ["occurrence_id"])
    p1, p2 = world["p1"], world["p2"]
    assert got["STU000010"] == [(got["STU000010"][0][0], [(gs["phy"], gs["paperA"], p1), (gs["chem"], gs["paperChem2"], p2)])]
    assert got["STU000011"] == [(got["STU000011"][0][0], [(gs["phy"], gs["paperT"], p2), (gs["chem"], gs["paperC"], p1)])]
    assert got["STU000010"][0][0] != got["STU000011"][0][0]                             # different papers: different exams
    lay = layout_of(db_factory, occ["occurrence_id"])
    assert list(lay["STU000010"]) == ["Physics", "Chemistry", "Biology"]                  # configured order, every configured subject
    assert lay["STU000010"] == lay["STU000011"] == {"Physics": "PAPER", "Chemistry": "PAPER", "Biology": "NOT_ENROLLED"}
    sent = {p["student_ids"][0]: p for p in deliver(db_factory) if len(p["student_ids"]) == 1 and p["student_ids"][0] in ("STU000010", "STU000011")}
    for sid in ("STU000010", "STU000011"):
        bio = sent[sid]["sections"][2]
        assert (bio["subject"], bio["status"], bio["question_count"]) == ("Biology", "NOT_ENROLLED", 0)
        assert bio["message"] == "You are not enrolled in Biology with any of your institutes."
        assert {q["subject"] for q in sent[sid]["questions"]} == {"Physics", "Chemistry"}   # never a question of an unrelated subject
    # the providers' questions never mix: each student's Physics comes only from the provider they take Physics with
    assert [q["text"] for q in sent["STU000010"]["questions"] if q["subject"] == "Physics"] == [f"paperA paper Q{i}" for i in (1, 2, 3)]
    assert [q["text"] for q in sent["STU000011"]["questions"] if q["subject"] == "Physics"] == [f"paperT paper Q{i}" for i in range(1, 6)]


def test_the_layout_api_shows_everything_to_the_platform_and_only_their_own_part_to_each_provider(client, world, gs, rev, login, db_factory, make_staff):
    t, occ = rev
    run_generation(db_factory, t, occ)
    url = f"{API}/global-occurrences/{occ['occurrence_id']}/student-subjects"
    full = client.get(url, headers=login(gs["sa"])).json()
    rows = {s["student_id"]: s for s in full["students"]}
    assert full["scope"] == "ALL" and [x["subject"] for x in full["subjects"]] == ["Physics", "Chemistry", "Biology"]
    x = rows["STU000010"]
    assert [(c["subject"], c["status"], c["provider_name"]) for c in x["subjects"]] == [
        ("Physics", "PAPER", "Provider p1"), ("Chemistry", "PAPER", "Provider p2"), ("Biology", "NOT_ENROLLED", None)]
    assert x["exam_id"] and x["subjects"][2]["message"].startswith("You are not enrolled in Biology")
    p1_view = {s["student_id"]: s for s in client.get(url, headers=login(gs["pa"])).json()["students"]}
    assert [(c["subject"], c["provider_name"]) for c in p1_view["STU000010"]["subjects"]] == [("Physics", "Provider p1")]      # never p2's paper
    assert [(c["subject"], c["provider_name"]) for c in p1_view["STU000011"]["subjects"]] == [("Chemistry", "Provider p1")]
    assert "STU000004" not in p1_view                                                      # not p1's student
    p2_view = {s["student_id"]: s for s in client.get(url, headers=login(gs["tutor"])).json()["students"]}
    assert [(c["subject"], c["provider_name"]) for c in p2_view["STU000010"]["subjects"]] == [("Chemistry", "Provider p2")]
    assert "STU000002" not in p2_view and "STU000003" not in p2_view
    _, b2_admin = make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b2"])])
    b2 = client.get(url, headers=login(b2_admin)).json()["students"]
    assert [s["student_id"] for s in b2] == ["STU000003"]                                   # only students enrolled in their branch
    assert {c["status"] for c in b2[0]["subjects"]} <= {"NOT_COVERED", "NO_SUBMISSION"}       # p1's own gaps for that student
    assert client.get(url, headers=login(gs["tA"])).status_code == 403                     # teachers use the exam pages


def test_a_card_only_exam_is_unified_and_only_the_platform_opens_it(client, world, gs, rev, login, db_factory):
    from institute_common.models.exams import ExamVersion
    t, occ = rev
    run_generation(db_factory, t, occ)
    [(exam_id, papers)] = exams_by_student(db_factory, occ["occurrence_id"])["STU000003"]
    assert papers == []
    with db_factory() as db:
        ev = db.query(ExamVersion).filter_by(exam_id=UUID(exam_id)).one()
        assert ev.total_questions == 0 and [c["status"] for c in ev.settings["sections"]] == ["NOT_COVERED", "NOT_COVERED", "NOT_ENROLLED"]
    view = client.get(f"{API}/exams/{exam_id}", headers=login(gs["sa"])).json()
    assert view["unified_global"] is True and view["total_questions"] == 0 and view["name"] == "National Science Mock"
    assert client.get(f"{API}/exams/{exam_id}", headers=login(gs["pa"])).status_code == 403          # its anchor grants nothing
    assert client.post(f"{API}/exams/{exam_id}/cancel", json={"reason": "not the anchor's"}, headers=login(gs["pa"])).status_code == 403


def test_a_running_generation_job_never_marks_the_date_generated(client, world, gs, rev, db_factory, monkeypatch):
    """A group whose job is still RUNNING (another worker, or a pass interrupted mid-generation) holds the date open; the next pass
    completes it, and nobody ends up with two exams or none."""
    from types import SimpleNamespace
    from institute_common.models.exams import GlobalTestOccurrence
    t, occ = rev
    real, calls = exam_generation.generate, {"n": 0}

    def first_call_running(db, plan_id, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return SimpleNamespace(status="RUNNING", exam_id=None, attempts=1, error=None)
        return real(db, plan_id, *a, **kw)
    monkeypatch.setattr(exam_generation, "generate", first_call_running)
    [first] = run_generation(db_factory, t, occ)
    assert any("still running" in f["error"] for f in first["failed"]), first
    with db_factory() as db:
        assert db.get(GlobalTestOccurrence, UUID(occ["occurrence_id"])).generated_at is None
    [second] = run_generation(db_factory, t, occ)
    assert not second["failed"], second
    got = exams_by_student(db_factory, occ["occurrence_id"])
    assert all(len(rows) == 1 for rows in got.values()) and set(got) == set(layout_of(db_factory, occ["occurrence_id"]))


def test_a_withdrawn_test_with_cancelled_exams_is_never_regenerated(client, world, gs, rev, login, db_factory):
    from institute_common.models.exams import Exam, ExamPlan
    t, occ = rev
    run_generation(db_factory, t, occ)
    with db_factory() as db:
        exams = db.query(Exam).join(ExamPlan, ExamPlan.plan_id == Exam.plan_id).filter(ExamPlan.global_occurrence_id == UUID(occ["occurrence_id"])).all()
        ids = [str(e.exam_id) for e in exams]
    for exam_id in ids:
        assert client.post(f"{API}/exams/{exam_id}/cancel", json={"reason": "test-only date withdrawn"}, headers=login(gs["sa"])).status_code == 200
    assert client.post(f"{API}/global-tests/{t['test_id']}/withdraw", json={"reason": "test-only Global Test"}, headers=login(gs["sa"])).status_code == 200
    assert run_generation(db_factory, t, occ) == []
    with db_factory() as db:
        assert {e.status for e in db.query(Exam).filter(Exam.exam_id.in_([UUID(i) for i in ids]))} == {"CANCELLED"}
        assert db.query(Exam).join(ExamPlan, ExamPlan.plan_id == Exam.plan_id).filter(ExamPlan.global_occurrence_id == UUID(occ["occurrence_id"])).count() == len(ids)
