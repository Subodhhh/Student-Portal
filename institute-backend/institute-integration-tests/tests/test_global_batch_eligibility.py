"""Global Test eligibility follows ACTIVE batch membership: a membership's explicit subject selection, or, without one, the subjects its batch
offers (never every subject of the provider); each provider resolved independently; paper audiences still restrict. World: gs plus
  STU000010  p1 batch D (offers Physics only), no selection
  STU000011  p1 batch A (offers Physics + Chemistry), selection: Chemistry only
  STU000012  p1 batch E (INACTIVE, offers Physics), no selection
  STU000013  p1 batch D (Physics) and p2 batch F (offers Chemistry; p2 now also offers Chemistry), no selections"""
from types import SimpleNamespace

import pytest

from inst_exam.services import global_layout
from inst_exam.services.global_generation import resolve_submission
from tests.test_global_contributions import client, login  # noqa: F401
from tests.test_global_schedule import aud, gs, layout_of, published, run_generation, submit  # noqa: F401
from tests.test_exams import make_paper


@pytest.fixture()
def be(world, gs, db_factory):  # noqa: F811
    from institute_common.models.academics import BatchSubject, EnrollmentBatchSubject, ProviderSubject
    from institute_common.models.org import Batch, Enrollment, EnrollmentBatch
    with db_factory() as db:
        db.add(ProviderSubject(provider_id=world["p2"], subject_id=gs["chem"]))
        made = {}
        for key, branch, subjects, status in (("d", "p1b1", ("phy",), "ACTIVE"), ("e", "p1b1", ("phy",), "INACTIVE"), ("f", "p2b1", ("chem",), "ACTIVE")):
            b = Batch(branch_id=world[branch], name=key.upper(), status=status)
            db.add(b)
            db.flush()
            made[key] = gs[f"batch_{key}"] = b.batch_id
            for s in subjects:
                db.add(BatchSubject(batch_id=b.batch_id, subject_id=gs[s]))
        for eid, sid, prov, branch, batch, chosen in (("x10", "STU000010", "p1", "p1b1", gs["batch_d"], None), ("x11", "STU000011", "p1", "p1b1", gs["batch_a"], "chem"),
                                                      ("x12", "STU000012", "p1", "p1b1", gs["batch_e"], None), ("x13a", "STU000013", "p1", "p1b1", gs["batch_d"], None),
                                                      ("x13b", "STU000013", "p2", "p2b1", gs["batch_f"], None)):
            db.add(Enrollment(enrollment_id=eid, provider_id=world[prov], branch_id=world[branch], student_id=sid))
            db.flush()
            link = EnrollmentBatch(enrollment_id=eid, batch_id=batch)
            db.add(link)
            db.flush()
            if chosen:
                db.add(EnrollmentBatchSubject(enrollment_batch_id=link.enrollment_batch_id, subject_id=gs[chosen]))
        db.commit()
    return made


def reach(db_factory, audience, subject, gs):  # noqa: F811
    with db_factory() as db:
        return sorted(r[0] for r in resolve_submission(db, SimpleNamespace(audience=audience, subject_id=gs[subject]), None))


def p2_batch(world, gs, batch):  # noqa: F811
    return {"provider_id": str(world["p2"]), "branch_ids": [str(world["p2b1"])], "whole_branch_ids": [], "batch_ids": [str(gs[batch])], "student_ids": []}


def test_a_submission_reaches_only_students_who_take_its_subject_in_an_active_batch(world, gs, be, db_factory):
    whole_b1 = aud(world, gs, whole=("p1b1",))
    phy, chem = reach(db_factory, whole_b1, "phy", gs), reach(db_factory, whole_b1, "chem", gs)
    assert "STU000010" in phy and "STU000010" not in chem         # no selection: batch D offers only Physics
    assert "STU000011" in chem and "STU000011" not in phy         # explicit selection: Chemistry only, although batch A offers both
    assert "STU000012" not in phy                                  # inactive batch: eligible for nothing
    assert reach(db_factory, aud(world, gs, batches=("batch_b",)), "phy", gs) == ["STU000002"]     # the paper's audience still restricts
    assert reach(db_factory, p2_batch(world, gs, "batch_f"), "chem", gs) == ["STU000013"]          # p2 resolved on its own batch
    assert reach(db_factory, p2_batch(world, gs, "batch_f"), "phy", gs) == []                      # batch F does not offer Physics


def test_the_layout_rule_matches_per_provider(world, gs, be, db_factory):
    with db_factory() as db:
        takes = global_layout._takes(db, {"STU000010", "STU000011", "STU000012", "STU000013"},
                                     {world["p1"]: {gs["phy"], gs["chem"]}, world["p2"]: {gs["phy"], gs["chem"]}})
    taken = {sid: {subj: sorted(str(p) for p in provs) for subj, provs in per.items()} for sid, per in takes.items()}
    assert taken["STU000010"] == {gs["phy"]: [str(world["p1"])]}
    assert taken["STU000011"] == {gs["chem"]: [str(world["p1"])]}
    assert "STU000012" not in taken
    assert taken["STU000013"] == {gs["phy"]: [str(world["p1"])], gs["chem"]: [str(world["p2"])]}    # never Chemistry from p1 or Physics from p2


def test_generation_gives_each_student_only_their_eligible_papers_with_accurate_reasons(client, world, gs, be, login, db_factory):  # noqa: F811
    _pid, chem2 = make_paper(db_factory, world["p2"], world["p2b1"], gs["chem"], "p2 chemistry", "PUBLISHED", n=2)
    t, occ = published(client, login, gs)
    assert submit(client, login, gs["pa"], occ, gs["phy"], gs["paperA"], aud(world, gs, whole=("p1b1",))).status_code == 201
    assert submit(client, login, gs["pa"], occ, gs["chem"], gs["paperC"], aud(world, gs, whole=("p1b1",))).status_code == 201
    assert submit(client, login, gs["tutor"], occ, gs["chem"], str(chem2), p2_batch(world, gs, "batch_f")).status_code == 201
    [run] = run_generation(db_factory, t, occ)
    assert not run["failed"], run
    lay = layout_of(db_factory, occ["occurrence_id"])
    assert lay["STU000010"] == {"Physics": "PAPER", "Chemistry": "NOT_ENROLLED"}
    assert lay["STU000011"] == {"Physics": "NOT_ENROLLED", "Chemistry": "PAPER"}
    assert lay.get("STU000012") in (None, {"Physics": "NOT_ENROLLED", "Chemistry": "NOT_ENROLLED"})   # inactive batch: no paper either way
    assert lay["STU000013"] == {"Physics": "PAPER", "Chemistry": "PAPER"}                          # p1 Physics + p2 Chemistry


def test_the_occurrence_pages_snapshot_resolves_exactly_like_the_queries(world, gs, be, db_factory):
    """GET /global-occurrences/{id} resolves every submission from one snapshot per provider (db.info["audience_snapshot"]); the result
    must equal the query path for every audience shape, subject and class."""
    from inst_exam.services.exam_generation import resolve_audience
    audiences = [aud(world, gs, whole=("p1b1",)), aud(world, gs, batches=("batch_a",)), aud(world, gs, batches=("batch_d", "batch_e")),
                 aud(world, gs, branches=("p1b1", "p1b2"), whole=("p1b2",)), p2_batch(world, gs, "batch_f")]
    for a in audiences:
        for subject in ("phy", "chem", "bio"):
            for class_level in (None, "11TH"):
                sub = SimpleNamespace(audience=a, subject_id=gs[subject])
                with db_factory() as db:
                    plain = resolve_submission(db, sub, class_level)
                with db_factory() as db:
                    db.info["audience_snapshot"] = True
                    snap = resolve_submission(db, sub, class_level)
                assert snap == plain, (a, subject, class_level)
    with db_factory() as db:                                   # Daily/Weekly (no opt-in) never use the snapshot
        resolve_audience(db, world["p1"], world["p1b1"], "INSTITUTE", [], [])
        assert "audience_snapshot_data" not in db.info
